"""端到端流程 —— 产出所有能自动产出的东西，在人工审核门前停下。

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


def _load_macro(r, as_of: date) -> tuple[str, str]:
    """宏观事实入账本。返回 (摘要, 失败说明)。"""
    from ir_agent.sources.macro import MacroSnapshot, MacroUnavailable, fetch_macro

    try:
        facts = fetch_macro()
    except MacroUnavailable as e:
        return "", f"宏观事实（{e}）"
    for f in facts:
        r.ledger.put(f)
    snap = MacroSnapshot(facts, as_of=as_of)
    return snap.describe(), ""


def _load_industry(code: str, r, as_of: date) -> tuple[str, str, list[str]]:
    """行业聚合入账本。返回 (摘要, 失败说明, 涉及的期间)。

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
        return "", f"行业聚合（无法推出 {r.period} 的上年同期）", []

    try:
        peers = peer_set(code)
    except IndustryUnavailable as e:
        return "", f"行业聚合（{e}）", []

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
    return f"{peers.summary()}\n{agg.summary()}", "", [r.period]


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


def _macro_periods(r) -> tuple[str, ...]:
    """宏观事实挂在自己的统计期上（2026H1 / 2026-07），不是公司报告期。

    不把这些期间放进目录，正文就引用不到宏观事实 —— 而提示词里又要求写
    宏观一节，模型只能写裸数字，然后被审计驳回。两边必须对齐。
    """
    from ir_agent.periods import UnknownPeriod
    from ir_agent.periods import sort_key as pk

    out: dict[str, tuple] = {}
    for key, per in r.ledger._facts:                     # noqa: SLF001
        if not key.startswith("macro."):
            continue
        try:
            out[per] = pk(per)
        except UnknownPeriod:
            continue
    # 每个 key 只需最新一期即可支撑论述，全给会把目录撑爆
    return tuple(sorted(out, key=lambda p: out[p], reverse=True)[:4])


def _write_and_debate(r, code: str, model: str | None,
                      macro_summary: str = "", industry_summary: str = ""):
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
             model: str | None = None) -> ResearchOutcome:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    arts: dict[str, str] = {}
    skipped: list[str] = []

    rt, route_err = _route(code)
    if route_err:
        skipped.append(route_err)

    # 宏观与行业必须在撰写之前入账本 —— 正文的第 2、3 节要引用它们，
    # 而假设推导也要靠它们。顺序错了，两边都拿不到。
    macro_md, macro_err = _load_macro(r, as_of)
    if macro_err:
        skipped.append(macro_err)
    try:
        industry_md, industry_err, _ = _load_industry(code, r, as_of)
    except Exception as e:                                  # noqa: BLE001
        industry_md, industry_err = "", f"行业聚合（{e.__class__.__name__}: {e}）"
    if industry_err:
        skipped.append(industry_err)

    body, upheld, debate_md, cost, err = _write_and_debate(
        code=code, r=r, model=model,
        macro_summary=macro_md, industry_summary=industry_md)
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

    build_dashboard(r, path=out / "dashboard.html", route=rt)
    arts["看板"] = str(out / "dashboard.html")
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)
    arts["工作簿"] = str(out / "model.xlsx")

    # 假设模板只在路由认可 DCF 时才写 —— 给一份用不上的模板会误导人去填
    if rt is not None and rt.method is ValuationMethod.DCF:
        from ir_agent.valuation.derive import derive
        try:
            proposals = derive(r.ledger, r.period, as_of, years=FORECAST_YEARS)
        except Exception:                                   # noqa: BLE001
            proposals = None                                # 推不出来就不附
        (out / "assumptions.yaml").write_text(
            assumption_template(FORECAST_YEARS, derived=proposals),
            encoding="utf-8")
        arts["假设模板"] = str(out / "assumptions.yaml")
        stage = Stage.AWAITING_REVIEW
    else:
        stage = Stage.NOT_APPLICABLE

    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=stage,
        artifacts=arts, skipped=skipped, upheld=upheld,
        cost_usd=Decimal(str(cost)),
        route_reason=rt.reason if rt else "")


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

    try:
        a = interactive_review(context=ctx)
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
    build_dashboard(r, path=out / "dashboard.html", route=rt,
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
