"""端到端流程 —— 一次跑完，估值假设由模型自主决定（见 research.py 模块说明）。

独立成模块而非塞进 __main__: CLI 已经有十来个开关，再叠一段长分支会让
两种用法互相纠缠。这里的流程是线性的、可单独测试的。
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from ir_agent.analyst import AnalystRefused, FactCatalog, write_body
from ir_agent.audit import audit
from ir_agent.clients.claude_cli import ClaudeCliError, make_client
from ir_agent.dashboard import build_dashboard
from ir_agent.debate import DebateRefused, run_debate
from ir_agent.market import market_of
from ir_agent.research import (
    ResearchOutcome,
    Stage,
    assumption_template,
    next_actions,
)
from ir_agent.revision import RevisionFailed, revise_from_verdicts
from ir_agent.sources import eastmoney
from ir_agent.valuation.route import ValuationMethod, annual_series, route
from ir_agent.xlsx import build_workbook

FORECAST_YEARS = 5


def _prior_period(period: str) -> str | None:
    """上年同期。2025FY → 2024FY，2025H1 → 2024H1。"""
    if len(period) < 5 or not period[:4].isdigit():
        return None
    return f"{int(period[:4]) - 1}{period[4:]}"


def _load_macro(r, as_of: date) -> tuple[str, str, str]:
    """宏观事实入账本。返回 (给人看的摘要, 给模型看的口径, 失败说明)。

    两份摘要不能合一: 看板要读数，提示词要占位符。给模型看数值而它又
    无法引用，就是在诱导它写裸数字然后驳回它 —— 自相矛盾的契约。
    """
    from ir_agent.sources.macro import MacroSnapshot, MacroUnavailable, fetch_macro

    try:
        facts = fetch_macro()
    except MacroUnavailable as e:
        return "", "", f"宏观事实（{e}）"
    for f in facts:
        r.ledger.put(f)
    snap = MacroSnapshot(facts, as_of=as_of)
    return snap.describe(), snap.for_prompt(), ""


def _load_capm(code: str, r, as_of: date) -> tuple[str, str, str]:
    """无风险利率与 β 入账本。返回 (给人看的, 给模型看的, 失败说明)。

    这两个数原本被判定为「免费源拿不到，只能人工填」。前者是没找对接口
    （中债官网 404，但东财 RPTA_WEB_TREASURYYIELD 一直都在，且逐日）；
    后者根本不该抓 —— 自己回归算，窗口、基准、频率、复权才说得清楚，
    还能过 as_of 守卫。

    有了这两条，WACC 里只剩风险溢价是判断。
    """
    from ir_agent.beta import BetaUnavailable, beta_facts, estimate
    from ir_agent.sources.rates import RatesUnavailable, fetch_yields, risk_free

    human, prompt, errs = [], [], []

    try:
        ys = fetch_yields()
        for f in ys:
            r.ledger.put(f)
        rf = risk_free(ys, as_of)
        if rf is None:
            errs.append("无风险利率（该时点无可见收益率）")
        else:
            human.append("  " + rf.describe())
            prompt.append(f"  无风险利率（{rf.tenor} 年期国债）　{rf.ref}")
    except RatesUnavailable as e:
        errs.append(f"无风险利率（{e}）")

    try:
        b = estimate(code, as_of)
        for f in beta_facts(b):
            r.ledger.put(f)
        human.append("  " + b.describe())
        per = b.as_of.isoformat()
        line = (f"  β（对沪深300，周频 {b.observations} 期，2 年窗口，前复权）"
                f"　[[beta@{per}]]　R²　[[beta.r_squared@{per}]]")
        if b.weak_fit:
            line += "\n    ⚠ R² 偏低，β 对本标的解释力有限，CAPM 适用性存疑，"\
                    "若据此定 WACC 必须在正文中说明"
        prompt.append(line)
    except BetaUnavailable as e:
        errs.append(f"β（{e}）")

    return ("\n".join(human), "\n".join(prompt), "；".join(errs))


def _load_industry(code: str, r, as_of: date) -> tuple[str, str, str]:
    """行业聚合入账本。返回 (给人看的摘要, 给模型看的口径, 失败说明)。

    逐家取同业营收要走十几到几十次网络调用，是全流程里最慢的一段。失败不
    阻断 —— 没有行业分析的报告仍然有价值，但必须在 skipped 里说清楚缺了什么，
    因为缺了它，增速假设就退回人工填写。
    """
    from ir_agent.industry_facts import aggregate, industry_facts
    from ir_agent.market import market_of
    from ir_agent.sources.eastmoney import fetch_statements_em
    from ir_agent.sources.industry import IndustryUnavailable, peer_set

    prior = _prior_period(r.period)
    if prior is None:
        return "", "", f"行业聚合（无法推出 {r.period} 的上年同期）"

    try:
        peers = peer_set(code)
    except IndustryUnavailable as e:
        return "", "", f"行业聚合（{e}）"

    cache: dict[str, dict] = {}

    def fetch(c: str, period: str, when: date):
        if c not in cache:
            facts, _ = fetch_statements_em(c, market=market_of(c))
            cache[c] = {(f.key, f.period): f for f in facts}
        f = cache[c].get(("revenue", period))
        return None if f is None or f.as_of > when else f.value

    print(f"行业聚合：{peers.classification.used_path}"
          f"（{len(peers.members) + 1} 家）…", file=sys.stderr)
    agg = aggregate(peers, r.period, prior, as_of, fetch)
    for f in industry_facts(agg):
        r.ledger.put(f)
    return (f"{peers.summary()}\n{agg.summary()}", agg.for_prompt(), "")


def _load_comps(code: str, r, as_of: date, snapshot_dir) -> tuple[str, str, str]:
    """可比公司分析入账本。返回 (给人看的摘要, 给模型看的口径, 失败说明)。

    这是全流程最贵的一段: 每家可比公司都要跑一遍完整取数（财报 + 行情 +
    交叉验证）。因此先按市值就近挑出六家再取数 —— 对 18 家同业逐一跑完，
    多花的时间买不到任何结论，规模差两个数量级的 PE/PB 本来就不可比。
    """
    from ir_agent.comparables import NoComparables, select
    from ir_agent.comps import (
        InsufficientPeers,
        build_comps,
        comps_facts,
        fetch_row,
        peer_metrics_from_ledger,
    )
    from ir_agent.pipeline import run as run_pipeline
    from ir_agent.sources.industry import IndustryUnavailable, peer_set

    try:
        sel = select(peer_set(code))
    except (IndustryUnavailable, NoComparables) as e:
        return "", "", f"可比分析（{e}）"

    def fetch(c: str, period: str, when: date):
        pr = run_pipeline(c, year=int(period[:4]), as_of=when,
                          snapshot_dir=snapshot_dir,
                          period_suffix=period[4:] or "FY",
                          adjudicate_on_conflict=False)
        spot = next((f.period for f in (pr.quotes.facts if pr.quotes else [])
                     if f.key == "market_cap"), None)
        return {"name": c,
                "metrics": peer_metrics_from_ledger(pr.ledger, period, when, spot)}

    print(f"可比分析：{len(sel.chosen)} 家…", file=sys.stderr)
    rows, excluded = [], []
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    # 标的自身直接用主流程已有的账本，不重复取数
    from ir_agent.comps import CompsRow
    rows.append(CompsRow(code=code, name=code, period=r.period,
                         metrics=peer_metrics_from_ledger(r.ledger, r.period,
                                                          as_of, spot)))
    for peer in sel.chosen:
        row, exc = fetch_row(peer.code, r.period, as_of, fetch)
        (rows.append(row) if row else excluded.append(exc))

    try:
        table = build_comps(code, rows, r.period, as_of, excluded)
    except (InsufficientPeers, ValueError) as e:
        return "", "", f"可比分析（{e}）"
    comps_facts(table, r.ledger)
    return (f"{sel.summary()}\n{table.summary()}",
            f"{sel.for_prompt()}\n{table.for_prompt()}", "")


def _route(code: str):
    """估值方法路由。失败不阻断流程 —— 其余产物仍然有价值。"""
    try:
        em, _ = eastmoney.fetch_statements_em(code, market=market_of(code))
        hist = annual_series(em, "net_profit")
        if not hist:
            return None, "无年度盈利序列，无法判定估值方法"
        ct = eastmoney.detect_company_type(code, market_of(code))
        return route(ct, hist, hist[-1]), ""
    except Exception as e:                                  # noqa: BLE001
        return None, f"估值方法路由（{e.__class__.__name__}）"


# 挂在自己期间上（而非公司报告期）的事实前缀
_CONTEXT_PREFIXES = ("macro.", "rate.", "beta")


def _macro_periods(r) -> tuple[str, ...]:
    """宏观/利率/β 挂在自己的期间上（2026H1、2026-07、2026-09-04），
    不是公司报告期。

    不把这些期间放进目录，正文就引用不到它们 —— 而提示词里又要求写宏观
    一节、要求用 β 论证 WACC，模型只能写裸数字，然后被审计驳回。两边必须对齐。
    """
    from ir_agent.periods import UnknownPeriod
    from ir_agent.periods import sort_key as pk

    out: dict[str, tuple] = {}
    for key, per in r.ledger._facts:                     # noqa: SLF001
        if not key.startswith(_CONTEXT_PREFIXES):
            continue
        try:
            out[per] = pk(per)
        except UnknownPeriod:
            continue
    # 每个 key 只需最新一期即可支撑论述，全给会把目录撑爆。
    # 利率是逐日的，几十条同类事实会把可引用清单淹没。
    return tuple(sorted(out, key=lambda p: out[p], reverse=True)[:6])


def _write_and_debate(r, code: str, model: str | None,
                      macro_summary: str = "", industry_summary: str = "",
                      comps_summary: str = ""):
    """撰写 → 辩论 → 回写。返回 (正文, 成立挑战数, 辩论摘要, 开销, 失败说明)。"""
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    extra = _macro_periods(r) + ((spot,) if spot else ())
    cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=r.as_of,
                                  extra_periods=extra)
    client = make_client(model=model)
    try:
        print(f"撰写正文（{len(cat.entries)} 条可引用事实）…", file=sys.stderr)
        body, _ = write_body(cat, client, code=code, period=r.period,
                             comps_summary=comps_summary or None,
                             macro_summary=macro_summary or None,
                             industry_summary=industry_summary or None)

        print("多空辩论…", file=sys.stderr)
        d = run_debate(cat, draft=body, client=client, period=r.period)

        if d.upheld_count:
            print(f"回写 {d.upheld_count} 项成立的挑战…", file=sys.stderr)
            body, _ = revise_from_verdicts(cat, body, d.challenges, client)

        return body, d.upheld_count, d.summary(), client.total_cost_usd, ""
    except (AnalystRefused, DebateRefused, RevisionFailed, ClaudeCliError) as e:
        return None, 0, "", client.total_cost_usd, f"{e.__class__.__name__}: {e}"


def run_full(code: str, r, as_of: date, out_dir: str | Path,
             model: str | None = None,
             snapshot_dir: str | Path = "snapshots") -> ResearchOutcome:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    arts: dict[str, str] = {}
    skipped: list[str] = []

    rt, route_err = _route(code)
    if route_err:
        skipped.append(route_err)

    # 宏观与行业必须在撰写之前入账本 —— 正文的第 2、3 节要引用它们，
    # 而假设推导也要靠它们。顺序错了，两边都拿不到。
    # 每层都产出两份摘要: _md 给看板与人看（读数），_pr 给模型看（占位符）
    macro_md, macro_pr, macro_err = _load_macro(r, as_of)
    if macro_err:
        skipped.append(macro_err)

    capm_md, capm_pr, capm_err = _load_capm(code, r, as_of)
    if capm_err:
        skipped.append(capm_err)
    try:
        industry_md, industry_pr, industry_err = _load_industry(code, r, as_of)
    except Exception as e:                                  # noqa: BLE001
        industry_md = industry_pr = ""
        industry_err = f"行业聚合（{e.__class__.__name__}: {e}）"
    if industry_err:
        skipped.append(industry_err)

    try:
        comps_md, comps_pr, comps_err = _load_comps(code, r, as_of, snapshot_dir)
    except Exception as e:                                  # noqa: BLE001
        comps_md = comps_pr = ""
        comps_err = f"可比分析（{e.__class__.__name__}: {e}）"
    if comps_err:
        skipped.append(comps_err)

    body, upheld, debate_md, cost, err = _write_and_debate(
        code=code, r=r, model=model, comps_summary=comps_pr,
        macro_summary=macro_pr, industry_summary=industry_pr)
    if err:
        print(f"\n撰写/辩论中止：{err}", file=sys.stderr)
        skipped.append("研究正文")
    if debate_md:
        (out / "debate.md").write_text(debate_md, encoding="utf-8")
        arts["辩论记录"] = str(out / "debate.md")
    if body:
        r.audit = audit(body, r.ledger, r.store, as_of=r.as_of)
        (out / "report.md").write_text(r.audit.rendered, encoding="utf-8")
        arts["研究正文"] = str(out / "report.md")

    ctx = {"宏观": macro_md, "折现率输入": capm_md,
           "行业": industry_md, "可比": comps_md}

    # ── 估值 ──────────────────────────────────────────────
    # 人工审核门已取消，假设改由模型自主决定。代码仍然把能查的都查一遍
    # （永续 ≤ 宏观上限、永续 < WACC、依据可核验、WACC 必须有正文论证），
    # 但「有人看过并负责」这件事不再存在 —— 产物一律标注为模型决定。
    assumptions = valuation = sensitivity = None
    stage = Stage.NOT_APPLICABLE
    if rt is not None and rt.method is ValuationMethod.DCF:
        # DCF 适用但没定出假设，与「本标的不适用 DCF」是两回事。混为一谈
        # 会让摘要报出「不适用 DCF」，而真实情况是模型没给出合法假设 ——
        # 前者无解，后者人工接手就能继续。
        stage = Stage.AWAITING_REVIEW
        if not body:
            skipped.append("估值（无正文，模型无从给出假设的正文依据）")
        else:
            assumptions, valuation, sensitivity, verr, cost = _autovalue(
                code=code, r=r, body=body, as_of=as_of, model=model,
                out=out, cost=cost)
            if verr:
                skipped.append(verr)
            else:
                stage = Stage.VALUED

    build_dashboard(r, path=out / "dashboard.html", route=rt, context=ctx,
                    assumptions=assumptions, valuation=valuation,
                    sensitivity=sensitivity)
    arts["看板"] = str(out / "dashboard.html")
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)
    arts["工作簿"] = str(out / "model.xlsx")
    if assumptions is not None:
        arts["假设（模型自主决定）"] = str(out / "assumptions.yaml")

    reason = rt.reason if rt else ""
    if valuation is not None:
        reason = (f"每股价值 {valuation.value_per_share:,.2f} 元"
                  f"（终值占比 {valuation.terminal_share:.1%}，"
                  f"假设由模型自主决定，未经人工复核）")

    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=stage,
        artifacts=arts, skipped=skipped, upheld=upheld,
        cost_usd=Decimal(str(cost)), route_reason=reason)


def _autovalue(*, code, r, body, as_of, model, out, cost):
    """模型自主定假设并建模。返回 (假设, 估值, 敏感性, 失败说明, 累计开销)。"""
    from ir_agent.clients.claude_cli import ClaudeCliError, make_client
    from ir_agent.review import to_yaml
    from ir_agent.stage2 import ValuationBlocked, value_from_assumptions
    from ir_agent.valuation.autodecide import AutoDecideFailed, decide
    from ir_agent.valuation.derive import derive

    try:
        proposals = derive(r.ledger, r.period, as_of, years=FORECAST_YEARS)
    except Exception:                                       # noqa: BLE001
        proposals = None

    client = make_client(model=model)
    try:
        print("模型自主确定估值假设…", file=sys.stderr)
        d = decide(body, client, ledger=r.ledger, as_of=as_of,
                   derived=proposals, years=FORECAST_YEARS)
    except (AutoDecideFailed, ClaudeCliError) as e:
        return None, None, None, f"估值（{e.__class__.__name__}: {e}）", \
            cost + client.total_cost_usd
    cost += client.total_cost_usd

    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    try:
        res = value_from_assumptions(
            assumptions=d.assumptions, ledger=r.ledger, body=body,
            period=r.period, spot_period=spot or r.period, as_of=as_of)
    except ValuationBlocked as e:
        return None, None, None, f"估值（{e}）", cost

    (out / "assumptions.yaml").write_text(
        "# 本文件由**模型自主决定**，未经人工审核。\n"
        "# 取消人工审核门后，代码仍校验: 永续 ≤ 宏观上限、永续 < WACC、\n"
        "# 依据可核验、WACC 必须能引用正文中的论证原话。\n"
        "#\n" + to_yaml(d.assumptions)
        + "\n# ── 模型给出的理由原文 ──\n"
        + "\n".join("# " + ln for ln in d.rationale.split("\n")),
        encoding="utf-8")
    return d.assumptions, res.valuation, res.sensitivity, "", cost


def run_review(code: str, r, as_of: date, out_dir: str | Path,
               reviewer: str) -> ResearchOutcome:
    """交互式审核 → 写 yaml → 直接建模。一步走完人工那一关。"""
    from ir_agent.ledger import LookAheadError
    from ir_agent.review import ReviewAborted, interactive_review, to_yaml

    out = Path(out_dir)
    ctx = {}
    for key, label in (("revenue.yoy", "上期营收同比"),
                       ("net_profit.yoy", "上期净利同比"),
                       ("roe", "本期 ROE")):
        try:
            f = r.ledger.get(key, r.period, as_of=r.as_of)
            ctx[label] = f"{f.value * 100:.2f}%"
        except (KeyError, LookAheadError):
            continue

    # 推导的建议与约束一并呈给审核者 —— 审核时看得见推导，比事后翻
    # assumptions.yaml 的注释有用得多。推不出来也不阻断审核。
    try:
        from ir_agent.valuation.derive import derive
        proposals = derive(r.ledger, r.period, as_of, years=FORECAST_YEARS)
    except Exception:                                       # noqa: BLE001
        proposals = None

    try:
        a = interactive_review(context=ctx, derived=proposals)
    except ReviewAborted as e:
        raise SystemExit(str(e))

    path = out / "assumptions.yaml"
    path.write_text(to_yaml(a), encoding="utf-8")
    print(f"\n已写入 {path}")
    return run_stage2(code, r, as_of, out_dir, str(path), reviewer)


def run_stage2(code: str, r, as_of: date, out_dir: str | Path,
               assumptions_path: str, reviewer: str) -> ResearchOutcome:
    """第二阶段: 读入已审核的假设 → 建模 → 重建看板与工作簿。"""
    from ir_agent.assumptions_io import AssumptionFileError, load_assumptions
    from ir_agent.stage2 import ValuationBlocked, value_from_file

    out = Path(out_dir)
    report_md = out / "report.md"
    if not report_md.exists():
        raise SystemExit(
            f"找不到 {report_md} —— 请先运行第一阶段：\n"
            f"  python -m ir_agent {code} --year <年度> --full {out}")
    body = report_md.read_text(encoding="utf-8")

    try:
        a = load_assumptions(assumptions_path)
    except AssumptionFileError as e:
        raise SystemExit(f"假设文件有误：{e}")

    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    try:
        res = value_from_file(assumptions=a, ledger=r.ledger, body=body,
                              period=r.period, spot_period=spot or r.period,
                              as_of=as_of, reviewer=reviewer)
    except ValuationBlocked as e:
        raise SystemExit(f"无法出具估值：\n{e}")

    rt, _ = _route(code)
    # 第二阶段重建看板时口径不能丢 —— 审核之后反而看不到样本覆盖，
    # 恰好是最需要它的时候。宏观与行业事实此刻已在账本里，重算即可。
    macro_md, _, _ = _load_macro(r, as_of)
    try:
        industry_md, _, _ = _load_industry(code, r, as_of)
    except Exception:                                       # noqa: BLE001
        industry_md = ""
    build_dashboard(r, path=out / "dashboard.html", route=rt,
                    context={"宏观": macro_md, "行业": industry_md},
                    assumptions=res.valuation.assumptions,
                    valuation=res.valuation, sensitivity=res.sensitivity)
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)

    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=Stage.VALUED,
        artifacts={"研究正文": str(report_md),
                   "看板（含估值）": str(out / "dashboard.html"),
                   "工作簿": str(out / "model.xlsx")},
        route_reason=(f"每股价值 {res.valuation.value_per_share:,.2f} 元"
                      f"（终值占比 {res.valuation.terminal_share:.1%}，"
                      f"审核 {reviewer}）"))


def report(outcome: ResearchOutcome, out_dir: str | None = None,
           year: int | None = None) -> None:
    print()
    print(outcome.summary())
    acts = next_actions(outcome.stage, outcome.code,
                        outcome.artifacts.get("假设模板"),
                        out_dir=out_dir, year=year)
    if acts:
        print("\n下一步：")
        for i, act in enumerate(acts, 1):
            print(f"  {i}. {act}")
