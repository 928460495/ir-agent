"""端到端流程 —— 一次跑完，估值假设由模型自主决定（见 research.py 模块说明）。

独立成模块而非塞进 __main__: CLI 已经有十来个开关，再叠一段长分支会让
两种用法互相纠缠。这里的流程是线性的、可单独测试的。
"""

from __future__ import annotations

import json
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
from ir_agent import dcf
from ir_agent.research import (
    ASSUMPTIONS_MD,
    ResearchOutcome,
    Stage,
    assumption_template,
    next_actions,
)
from ir_agent.revision import RevisionFailed, revise_from_verdicts
from ir_agent.sources import eastmoney
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

    # 周期定位需要利率曲线（期限利差是政策维度的信号），而利率在
    # _load_capm 里加载 —— 那一步在本函数之后。因此这里只出指标，
    # 周期定位交给 _load_cycle 在两者都就绪后再做。
    return snap.describe(), snap.for_prompt(), ""


def _load_external(code: str, r, as_of: date) -> tuple[str, str, str]:
    """公司之外的资料入语料库。返回 (给人看的, 给模型看的, 失败说明)。

    此前宏观、行业、战略三块的资料全部来自数字接口与公司自己的年报 ——
    没有一句话来自公司和交易所之外。取不到时如实说「外部视角缺位」，
    不静默降级: 读者会以为那些判断有外部印证。
    """
    from ir_agent import corpus as C

    try:
        cp, cs = C.gather(as_of=as_of, code=code)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"外部资料（{e.__class__.__name__}: {e}）"
    r.corpus, r.consensus = cp, cs
    if cs is not None and cs.has_data():
        from ir_agent.sources.reports import consensus_facts
        for f in consensus_facts(cs):
            r.ledger.put(f)
    md = cp.summary() + "\n\n" + C.consensus_md(cs)
    if not cp.docs:
        return md, "", "外部资料（未取到任何券商研报，外部视角缺位）"
    return md, C.consensus_prompt(cs), ""


def _load_sections(r):
    """年报叙述章节 —— 战略与产业链分析的证据基础。"""
    from ir_agent.sources.annual_sections import extract
    from ir_agent.sources.annual_pdf import _download

    if not r.annual_url:
        return None, "年报叙述章节（未找到年报公告）"
    try:
        data = _download(r.annual_url, Path("snapshots") / "pdf")
        return extract(data, code=r.code), ""
    except Exception as e:                                  # noqa: BLE001
        return None, f"年报叙述章节（{e.__class__.__name__}: {e}）"


def _load_macro_view(r, as_of: date, client, macro_pr: str):
    """宏观定性: 政策取向与传导路径。补的是读数之外的那一半。"""
    from ir_agent import macro_view as MV

    if r.corpus is None or not r.corpus.docs:
        return "", "", "宏观定性（无外部宏观资料，政策取向缺文字依据）"
    try:
        view, _ = MV.assess(client, r.corpus, r.ledger, as_of, macro_pr)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"宏观定性（{e.__class__.__name__}: {str(e)[:120]}）"
    return view.summary(), view.summary(), ""


def _load_strategy(r, as_of: date, client, spot=None) -> tuple[str, str, str]:
    """战略分析: 护城河、五力、关键不确定性 → 情景驱动因子。"""
    from ir_agent import strategy

    sections, sec_err = _load_sections(r)
    if sections is None and r.corpus is None:
        return "", "", sec_err or "战略分析（无任何定性证据）"
    try:
        view, _ = strategy.assess(client, r.ledger, r.period, as_of,
                                  sections, spot, corpus=r.corpus)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"战略分析（{e.__class__.__name__}: {str(e)[:120]}）"
    r.sections = sections
    r.strategy_view = view          # 情景分析要用它识别出的关键不确定性
    return view.summary(), view.summary(), sec_err


def _load_value_chain(r, as_of: date, client) -> tuple[str, str, str]:
    """产业链与行业真实规模。"""
    from ir_agent import value_chain as VC

    sections = getattr(r, "sections", None)
    if r.corpus is None and sections is None:
        return "", "", "产业链分析（无任何资料）"
    try:
        view, _ = VC.analyse(client, r.corpus, r.ledger, r.period, as_of,
                             sections)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"产业链分析（{e.__class__.__name__}: {str(e)[:120]}）"
    if view.size:
        r.ledger.put(view.size.to_fact(
            r.corpus.docs[0].doc_id if r.corpus and r.corpus.docs else "ext",
            as_of))
    return view.summary(), view.summary(), ""


def _load_cycle(r, as_of: date) -> tuple[str, str, str]:
    """商业周期定位。必须在宏观与利率都入账之后调用。"""
    from ir_agent import cycle

    try:
        view = cycle.assess(r.ledger, as_of)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"周期定位（{e.__class__.__name__}: {e}）"
    if not view.scores:
        return "", "", "周期定位（无可用宏观信号）"
    for f in cycle.cycle_facts(view, r.period):
        r.ledger.put(f)
    r.cycle_view = view                       # 供行业传导取用
    return view.describe(), view.for_prompt(), ""


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
            # 期限用中文数字: 提示词里出现「10」而正文又无处引用它，
            # 等于诱导模型写裸数字再驳回它。
            prompt.append(f"  无风险利率（{_CN_TENOR.get(rf.tenor, '')}年期国债）"
                          f"　{rf.ref}")
    except RatesUnavailable as e:
        errs.append(f"无风险利率（{e}）")

    try:
        b = estimate(code, as_of)
        for f in beta_facts(b):
            r.ledger.put(f)
        human.append("  " + b.describe())
        per = b.as_of.isoformat()
        # 观测期数与窗口年数同样不能以裸数字出现在提示词里
        line = (f"  β（对沪深300，周频，两年窗口，前复权）"
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

    # ── 行业画像 ──────────────────────────────────────────
    # 逐家同业的完整报表在上面已经取过一次，cache 里躺着全部字段而只用了
    # revenue。这里复用同一批数据算集中度、生命周期与周期敏感度，
    # **不发起任何新的网络请求**。
    prof_md = prof_pr = ""
    try:
        prof_md, prof_pr = _industry_profile(peers, agg, cache, r, as_of)
    except Exception as e:                                  # noqa: BLE001
        prof_md = f"（行业画像未生成：{e.__class__.__name__}）"

    return (f"{peers.summary()}\n{agg.summary()}\n{prof_md}",
            f"{agg.for_prompt()}\n{prof_pr}".rstrip(), "")


def _latest_key(ledger, key: str, as_of: date) -> str:
    """该 key 在时点内可见的最新期间标签。"""
    from ir_agent.ledger import LookAheadError
    from ir_agent.periods import UnknownPeriod
    from ir_agent.periods import sort_key as pk

    best, bk = "", None
    for k, per in ledger._facts:                         # noqa: SLF001
        if k != key:
            continue
        try:
            ledger.get(k, per, as_of=as_of)
            s = pk(per)
        except (KeyError, LookAheadError, UnknownPeriod):
            continue
        if bk is None or s > bk:
            best, bk = per, s
    return best


def _fact_value(ledger, key: str, period: str, as_of: date):
    from ir_agent.ledger import LookAheadError
    try:
        return ledger.get(key, period, as_of=as_of).value
    except (KeyError, LookAheadError):
        return None


def _latest_annual_period(ledger, as_of: date) -> str | None:
    """最近一个完整年度的期间——**不跟着 `r.period` 走**。

    I4（终审复核）：`r.period = f"{year}{period_suffix}"`
    （`__main__.py` 的 `--period` 允许 H1/Q1-Q3/Q1），`--period H1`
    时 `r.period` 就是「2025H1」。加法链（毛利率）的基期必须是**最近
    一个完整年度**，与 `assume.apply_to`/`to_driver_block` 走的
    `_base_margin_of`（从 calibrate 的年度历史序列取）必须是同一个
    数——直接把 `r.period` 传给 `_fact_value`，会在 `--period H1` 下
    取到中报毛利率，与 apply_to/to_driver_block 用的年度值分叉，正是
    C3 注释里「必须是同一个数，而实际不是」的原话在另一个入口复现。
    """
    from ir_agent.operators.ratios import annual_periods
    periods = annual_periods(ledger, as_of, years=1)
    return periods[-1] if periods else None


def _industry_profile(peers, agg, cache, r, as_of: date) -> tuple[str, str]:
    """由已缓存的同业事实组装行业画像。"""
    from ir_agent import industry_profile as ip
    from ir_agent.ledger import FactLedger
    from ir_agent.operators import ratios
    from ir_agent.sources.eastmoney import CompanyType

    # cache 是 {code: {(key, period): Fact}}，转成账本以便算比率
    ledgers: dict[str, FactLedger] = {}
    for code, facts in cache.items():
        led = FactLedger()
        for f in facts.values():
            led.put(f)
        try:
            ratios.compute(led, r.period, as_of, CompanyType.GENERAL)
        except Exception:                                   # noqa: BLE001
            pass
        ledgers[code] = led

    # 行业营收的历史序列 —— 生命周期判据。同一组公司口径，
    # 否则成分变化会被误读成行业增长。
    history: dict[str, Decimal] = {}
    consistent = sorted(set(agg.revenues) & set(agg.prior))
    for per in ratios.annual_periods(r.ledger, as_of, years=6):
        vals = [v for c in consistent
                if (fs := cache.get(c)) and (f := fs.get(("revenue", per)))
                and f.as_of <= as_of and (v := f.value) is not None]
        if len(vals) == len(consistent) and vals:
            history[per] = sum(vals, Decimal(0))

    # 行业增速与名义 GDP 增速的历史序列 —— 弹性的输入。
    # 宏观事实在 run_full 里先于行业加载，此刻已在账本中。
    ind_yoy: dict[str, Decimal] = {}
    pers = sorted(history)
    for a, b in zip(pers, pers[1:]):
        if history[a]:
            ind_yoy[b] = history[b] / history[a] - 1
    gdp_yoy: dict[str, Decimal] = {}
    for per in ind_yoy:
        v = _fact_value(r.ledger, "macro.gdp_nominal_yoy", per, as_of)
        if v is not None:
            gdp_yoy[per] = v

    # 生命周期要拿行业增速与名义 GDP 增速比 —— 「快不快」必须有参照系。
    gdp_cagr = _fact_value(r.ledger, "macro.gdp_nominal_cagr",
                           _latest_key(r.ledger, "macro.gdp_nominal_cagr",
                                       as_of), as_of)
    # 行业整体利润率的历史 —— CFA 用它区分 growth（盈利改善）与 shakeout
    margins: dict[str, Decimal] = {}
    for per in history:
        num = den = Decimal(0)
        for c in consistent:
            fs = cache.get(c) or {}
            pf, rv = fs.get(("net_profit", per)), fs.get(("revenue", per))
            if pf and rv and pf.as_of <= as_of and rv.as_of <= as_of:
                num += pf.value
                den += rv.value
        if den:
            margins[per] = num / den

    prof = ip.build(
        industry=peers.classification.used_path, period=r.period, as_of=as_of,
        revenues=agg.revenues, ledgers=ledgers,
        revenue_history=history, cr5=agg.cr5,
        industry_yoy=ind_yoy, gdp_yoy=gdp_yoy,
        gdp_cagr=gdp_cagr, margin_history=margins)
    for f in ip.profile_facts(prof):
        r.ledger.put(f)

    # ── 传导 ──────────────────────────────────────────────
    # 课程的要求: 说清楚**行业结构将如何抑制或放大经济中的预期发展**。
    # 知道处于后周期还不够，还要知道这个行业对周期有多敏感 ——
    # 同样的宏观下行，DOL 9.5 的钢铁与 DOL 1.7 的白酒不是一回事。
    trans = ""
    view = getattr(r, "cycle_view", None)
    if view is not None:
        from ir_agent import cycle as cy

        sen = prof.sensitivity
        trans = cy.transmission(
            view, sen.total_leverage if sen else None,
            peers.classification.used_path,
            elasticity=sen.gdp_elasticity if sen else None)
    human = prof.summary() + (f"\n  传导　{trans}" if trans else "")
    model = prof.for_prompt() + (f"\n  宏观传导　{trans}" if trans else "")
    return human, model


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

    from ir_agent.sources.eastmoney import detect_company_type

    def fetch(c: str, period: str, when: date):
        pr = run_pipeline(c, year=int(period[:4]), as_of=when,
                          snapshot_dir=snapshot_dir,
                          period_suffix=period[4:] or "FY",
                          adjudicate_on_conflict=False)
        spot = next((f.period for f in (pr.quotes.facts if pr.quotes else [])
                     if f.key == "market_cap"), None)
        ct = detect_company_type(c, market_of(c))
        return {"name": c, "company_type": ct,
                "metrics": peer_metrics_from_ledger(
                    pr.ledger, period, when, spot, company_type=ct)}

    print(f"可比分析：{len(sel.chosen)} 家…", file=sys.stderr)
    rows, excluded = [], []
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    # 标的自身直接用主流程已有的账本，不重复取数
    from ir_agent.comps import CompsRow
    target_ct = detect_company_type(code, market_of(code))
    rows.append(CompsRow(code=code, name=code, period=r.period,
                         company_type=target_ct,
                         metrics=peer_metrics_from_ledger(
                             r.ledger, r.period, as_of, spot,
                             company_type=target_ct)))
    for peer in sel.chosen:
        row, exc = fetch_row(peer.code, r.period, as_of, fetch)
        (rows.append(row) if row else excluded.append(exc))

    try:
        table = build_comps(code, rows, r.period, as_of, excluded)
    except (InsufficientPeers, ValueError) as e:
        return "", "", f"可比分析（{e}）"
    comps_facts(table, r.ledger)
    return (f"{sel.summary()}\n{table.summary()}",
            f"{sel.for_prompt()}\n"
            + table.for_prompt(ledger=r.ledger, as_of=as_of, spot=spot), "")



# 挂在自己期间上（而非公司报告期）的事实前缀
_CONTEXT_PREFIXES = ("macro.", "rate.", "beta")

_CN_TENOR = {2: "两", 5: "五", 10: "十", 30: "三十"}


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


def _peer_names(code: str) -> tuple[str, ...]:
    """本标的与同业的公司名，用于豁免出处核验里的机构名误伤。

    分析中信证券这类券商时，正文本来就会反复出现「中信证券」——
    若因为语料库里没有中信证券的研报就判它捏造出处，那是把正当用法误伤。
    """
    try:
        from ir_agent.sources.taxonomy import fetch_all
        rows = fetch_all()
    except Exception:                                       # noqa: BLE001
        return ()
    me = next((e for e in rows if e.code == code), None)
    if me is None:
        return ()
    return tuple(e.name for e in rows if e.path == me.path)


def _build_evidence(r, code: str):
    """正文可引述的资料面 —— 年报原文 + 外部语料库。"""
    from ir_agent.attribution import Evidence
    from ir_agent.strategy import annual_text

    return Evidence(annual=annual_text(getattr(r, "sections", None)),
                    corpus=r.corpus, peers=_peer_names(code))


def _write_and_debate(r, code: str, model: str | None,
                      macro_summary: str = "", industry_summary: str = "",
                      comps_summary: str = "", strategy_summary: str = "",
                      chain_summary: str = "", consensus_summary: str = "",
                      valuation_summary: str = "", brief: bool = False):
    """撰写 → 辩论 → 回写。返回 (正文, 成立挑战数, 辩论摘要, 开销, 失败说明)。"""
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    extra = _macro_periods(r) + ((spot,) if spot else ())
    cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=r.as_of,
                                  extra_periods=extra)
    cat.evidence = _build_evidence(r, code)
    client = make_client(model=model)
    try:
        print(f"撰写正文（{len(cat.entries)} 条可引用事实）…", file=sys.stderr)
        body, _ = write_body(cat, client, code=code, period=r.period,
                             comps_summary=comps_summary or None,
                             macro_summary=macro_summary or None,
                             industry_summary=industry_summary or None,
                             strategy_summary=strategy_summary or None,
                             chain_summary=chain_summary or None,
                             consensus_summary=consensus_summary or None,
                             valuation_summary=valuation_summary or None,
                             brief=brief)

        print("多空辩论…", file=sys.stderr)
        try:
            d = run_debate(cat, draft=body, client=client, period=r.period)
        except DebateRefused as e:
            # **辩论失败不该毙掉整篇报告。** 挑战者要算账才能质疑
            # （投资现金流与资本开支的差额、占比），而账本里没有「差额」
            # 这条事实 —— 禁止派生算术等于禁止它提出有力的质疑。
            #
            # 而且正文有独立的验收审计，辩论里的裸数字进不了正文: 修订稿
            # 仍要过 _violations。因此这一步失败时保留正文并说明辩论未完成，
            # 比连正文一起丢掉诚实得多。
            print(f"辩论未完成，保留正文：{str(e)[:80]}", file=sys.stderr)
            return body, 0, "", client.total_cost_usd, (
                f"多空辩论未完成（{str(e)[:160]}）—— "
                "正文**未经证伪环节**，其结论未被交叉质疑过，阅读时请留意")

        note = ""
        if d.upheld_count:
            print(f"回写 {d.upheld_count} 项成立的挑战…", file=sys.stderr)
            try:
                body, _ = revise_from_verdicts(cat, body, d.challenges, client)
            except RevisionFailed as e:
                # **回写失败不该毙掉整篇报告。** 正文已通过全部闸门、辩论
                # 也已留痕，只是某条挑战没能落实到文字上。丢掉全部产出，
                # 读者既拿不到分析、也看不到那条挑战 —— 反而更不透明。
                #
                # 保留未修订稿并**如实标注哪条没落实**: 辩论记录里有完整
                # 的质疑与裁决，读者可以自己判断。这与「缺什么必须显式说
                # 出来」是同一条原则。
                note = (f"辩论回写未完成（{e}）—— "
                        "正文为**未修订稿**，上述挑战未落实到文字，"
                        "阅读时请对照 debate.md 自行判断")
                print(f"回写失败，保留未修订稿：{str(e)[:80]}", file=sys.stderr)

        return body, d.upheld_count, d.summary(), client.total_cost_usd, note
    except (AnalystRefused, ClaudeCliError) as e:
        return None, 0, "", client.total_cost_usd, f"{e.__class__.__name__}: {e}"


# 两阶段的产物名。**简报与完整研报是两个文件，不是同一个文件的两稿** ——
# 建模之前不该存在一份读起来像结论的完整研报。
BRIEF = "brief.md"
REPORT = "report.md"
# ASSUMPTIONS_MD 定义在 research.py 并从那里导入——`next_actions` 要按
# 「判断项文件在不在」决定教人重跑哪一阶段，两处各写一遍同一个文件名
# 早晚分叉（本仓库已经在占位符期间上踩过这种分叉）。research 不能反向
# 导入 full（full 依赖 research），所以单一定义放在被依赖的那一侧。
ASSUMPTIONS_JSON = "assumptions.json"
FAILED_RAW = "assumptions_failed_raw.txt"
STATE = "state.json"
# 方法论预研（Task 4）的产出 —— 分解式落盘为独立文件，而不是塞进
# brief.md 的某一段：它要在数据采集之前就能被人看到、改坏了可以直接
# 改这个文件覆盖重跑，不必等简报写完。
DRIVERS_MD = "drivers.md"


def _analyse_facts(code: str, r, as_of: date, snapshot_dir):
    """确定性分析层：宏观 / 折现率 / 周期 / 行业 / 可比 / 外部资料。

    **不调模型，因此两个阶段都要跑。** 第二阶段跑它不是为了摘要（摘要从
    state.json 读回），是为了**把派生事实重新放回账本** —— industry.*、
    macro.*、comps.* 这些 key 只存在于内存账本里，不重建的话，喂给撰写的
    摘要里那些 [[industry.xxx]] 占位符一个都解析不了，模型照抄就被判伪造。

    实测踩到: 第二阶段写完整研报时连挂三次，
    「账本中没有 industry.revenue_yoy@2025FY，但正文引用了它」。
    这是「我往提示词里放了模型用不了的东西」的又一次。
    """
    skipped: list[str] = []
    md: dict[str, str] = {}
    pr: dict[str, str] = {}

    def take(name, fn):
        try:
            a, b, err = fn()
        except Exception as e:                              # noqa: BLE001
            md[name] = pr[name] = ""
            skipped.append(f"{name}（{e.__class__.__name__}: {str(e)[:120]}）")
            return
        md[name], pr[name] = a, b
        if err:
            skipped.append(err)

    # 顺序有讲究: 宏观 → 利率 → 周期（政策维度用期限利差）→ 行业 → 可比；
    # 外部资料必须在战略与产业链之前，它们的定性判断要引用其中的原文。
    take("宏观", lambda: _load_macro(r, as_of))
    take("折现率输入", lambda: _load_capm(code, r, as_of))
    take("商业周期", lambda: _load_cycle(r, as_of))
    take("行业", lambda: _load_industry(code, r, as_of))
    take("可比", lambda: _load_comps(code, r, as_of, snapshot_dir))
    take("外部资料", lambda: _load_external(code, r, as_of))
    return md, pr, skipped


def _analyse_qualitative(code: str, r, as_of: date, model,
                         md: dict, pr: dict, skipped: list[str]):
    """需要模型的定性层（宏观定性 / 战略 / 产业链）。**就地**写入调用方的
    md/pr/skipped，只返回本层用的 client —— 开销要累计到总开销里。

    独立于 `_analyse_facts` 拆开（此前两者合在一个 `_analyse` 里）：
    `run_analysis` 现在要在两者之间插入方法论预研之后、正式采集因子
    数据之前的一段逻辑，拆开才能让「预研先于确定性层数据采集」这件事
    在源码顺序上也看得出来（见 test_预研排在数据采集之前）。
    """
    def take(name, fn):
        try:
            a, b, err = fn()
        except Exception as e:                              # noqa: BLE001
            md[name] = pr[name] = ""
            skipped.append(f"{name}（{e.__class__.__name__}: {str(e)[:120]}）")
            return
        md[name], pr[name] = a, b
        if err:
            skipped.append(err)

    qual_client = make_client(model=model)
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    take("宏观定性", lambda: _load_macro_view(r, as_of, qual_client, pr["宏观"]))
    take("战略", lambda: _load_strategy(r, as_of, qual_client, spot))
    take("产业链", lambda: _load_value_chain(r, as_of, qual_client))
    return qual_client


def _research_methodology(code: str, r, out: Path, client):
    """方法论预研 —— 先问「这个行业靠什么赚钱」，再按答案去采集数据。

    **不另设人工确认门。** 门的价值会被数量稀释: 现有那道判断项的门之所以
    有效，是因为它一次只要求人看一件事；再加一道，人看两次的注意力未必比
    看一次多。改用「立刻落盘 + 可中断 + 可覆盖」换取早期干预 —— 落盘为
    drivers.md，看不对就改这个文件后重跑第一阶段。

    **I2：这个「可覆盖」的承诺需要一条读回路径才成立。** drivers.md 若
    已经存在（无论是不是被人改过），说明这不是第一次跑第一阶段——直接
    读回它，不再重新联网预研，否则重跑会用新的预研结果把人的修改悄悄
    覆盖掉。读不懂就**明确报错**：这里不吞掉异常改去调用 `research()`
    静默重来一遍，那样人会以为自己的修改生效了，实际却被整个丢弃。

    **预研失败（MethodologyFailed）不中断整轮研究。** 联网拿不到闭合的
    分解式是常态而非故障（比如模型答非所问、行业太冷门搜不到方法论），
    此时退回不分解模式，判断项仍按营收增速/毛利率两项填写——降级但继续，
    损失的是精度，不是担保。
    """
    from ir_agent import methodology

    path = out / DRIVERS_MD
    if path.exists():
        try:
            m = methodology.from_markdown(path.read_text(encoding="utf-8"))
        except methodology.MethodologyFailed as e:
            return None, (f"方法论预研（{path} 已存在但读不回：{str(e)[:160]}）"
                          "—— 未重新联网预研（避免悄悄覆盖你的修改），"
                          "退回不分解模式，判断项仍按营收增速/毛利率两项填写")
        return m, ""

    try:
        from ir_agent.sources.taxonomy import fetch_all
        rows = fetch_all()
        me = next((e for e in rows if e.code == code), None)
        industry = me.path if me else ""
    except Exception:                                       # noqa: BLE001
        industry = ""
    if not industry:
        return None, "方法论预研（取不到申万行业分类）"

    try:
        m = methodology.research(client, industry, code)
    except methodology.MethodologyFailed as e:
        return None, (f"方法论预研（{str(e)[:160]}）—— "
                      "退回不分解模式，判断项仍按营收增速/毛利率两项填写")
    except ClaudeCliError as e:
        # **与 5db4ca5 修过的是同一个洞，只是换了个调用点。** 实测
        # （600584 --analyse，确定性层已跑完 2930 条事实、三表与跨源勾稽
        # 全部通过）第一个联网调用就撞上「You've hit your session limit」，
        # 而这里当时只捕 MethodologyFailed，于是 ClaudeCliError 一路冒到
        # __main__，整轮退出码 1、产物一个都没写出来。run_analysis 里其他
        # 每一个联网环节都已经把这类错误记进 skipped 后继续，唯独预研这一
        # 处漏了。**能力边界不是故障，整轮研究不该因此停摆。**
        #
        # 降级理由要说清这是**接口失败**而不是「这个行业分解不了」：
        # 两者都退回不分解模式，但前者重跑就能拿回因子分解，后者重跑
        # 多少次都一样。降级到两标量是这条产品线最不该悄悄发生的事，
        # 起码要让人一眼看出这次降级是可恢复的。
        return None, (f"方法论预研（{e.__class__.__name__}: {str(e)[:160]}）"
                      "—— 模型接口失败（额度用尽/超时/CLI 报错），不是这个"
                      "行业分解不了：本轮退回不分解模式，判断项按营收增速/"
                      "毛利率两项填写；接口恢复后重跑 --analyse 即可拿回"
                      "因子分解")
    path.write_text(methodology.to_markdown(m), encoding="utf-8")
    return m, ""


def _prompt_bundle(pr: dict) -> dict:
    """分析结论 → 撰写用的各段摘要。"""
    return dict(
        comps_summary=pr.get("可比", ""),
        macro_summary=(pr.get("宏观", "")
                       + ("\n\n" + pr["宏观定性"] if pr.get("宏观定性") else "")),
        industry_summary=pr.get("行业", ""),
        strategy_summary=pr.get("战略", ""),
        chain_summary=pr.get("产业链", ""),
        consensus_summary=pr.get("外部资料", ""))


# 判断项产出后登记的产物名——`_propose_assumptions` 写成功才登记，
# 因此它就是"判断项到底写出来没有"唯一可信的信号。
ASSUMPTIONS_ART = "DCF 判断项（待核实）"


def _review_stage(arts: dict):
    """按**实际产物**定状态与指引，不按预期路径。

    实测现场（600584 --analyse，18:08）：额度用尽 → 简报没写成 → 判断项
    根本没产出，目录里只有 dashboard/model/state 三个文件。而这里当时
    无条件 `stage = AWAITING_REVIEW`，于是终端照样打出「分析完成，DCF
    判断项待人工核实。核实 out/600584/assumptions.md」以及「下一步：
    1. 核实 …/assumptions.md　3. 确认后 --value」——那个文件不存在，
    第 3 步照做只会拿模板默认值（三档同值）去建模。

    `research.next_actions` 的文档自己写着「给一条跑不通的命令比不给
    指引更糟…过时的指引是会主动误导人的那种错」，指向一个不存在的文件
    同样如此。判断项没写出来时正确的状态是 `NOT_APPLICABLE`，它给的
    指引正是「重跑第一阶段即可」——额度用尽要的就是这句。

    与「占位符有没有被替换」「批准的数有没有进工作簿」同一类：
    **只按预期路径下结论，不查结果。**
    """
    if ASSUMPTIONS_ART in arts:
        return Stage.AWAITING_REVIEW, (
            "分析完成，DCF 判断项待人工核实。核实 "
            f"{arts[ASSUMPTIONS_ART]}（需改数值就改 {ASSUMPTIONS_JSON}），"
            "确认后运行第二阶段建模")
    return Stage.INCOMPLETE, (
        "本轮未产出 DCF 判断项（原因见上方未生成清单）——"
        "确定性层的产物（看板、工作簿、事实账本）仍然可用，"
        "但估值尚无从谈起")


def _assumption_caveats(a) -> tuple[str, str]:
    """判断项的软处要跟着交付物走：(进提示词的一句话, 追加进 report.md 的附录)。

    两件事，分别说：

    - **依据未能逐字核验**：因子依据从「核验不过就驳回」改成「标注后
      放行」（用户明确决定的取舍）之后留下的代价。
    - **取值本身是估计**：模型自己在理由里写明「无数据（估计）」的因子。
      600584 实跑里这是 **8 个因子全中**——而第一版声明只写了未核验，
      读者会以为问题仅仅是"出处是转述"，真相更重：这些数字本身就没有
      数据支撑。后者比前者更该被看见。

    两者都只到 `assumptions.md` 与终端是不够的——对外传阅的是
    `report.md`。本仓库自己的标准（`AuditResult.with_sources` 的文档）：
    算出来了不交付，等于没兑现。

    两个出口分开给，因为约束不同：

    - **进提示词的那句不能带数字。** 研报正文的每个数字都必须写成占位符，
      模型照抄一句「8 个」会被裸数字审计判掉——等于自己给自己埋雷。
      所以只讲事实、不讲数量，具体几项去看 assumptions.md。
    - **附录在 `audit()` 之后追加**（与数据来源附录同一处待遇：代码写的，
      不是模型写的），因此可以带准确条数与具体是哪些因子哪些年份。
    """
    from ir_agent import assume

    unverified = assume.unverified_labels(a)
    estimated = assume.estimated_labels(a)
    if not unverified and not estimated:
        return "", ""

    says: list[str] = []
    if unverified:
        says.append("部分 DCF 判断项的依据**未能逐字核验**——出处是转述"
                    "而非原文引用")
    if estimated:
        says.append("部分驱动因子**没有任何数据支撑、取值是估计**（该因子"
                    "在公开渠道本就无口径数据），依据列里的东西只是方向性"
                    "参照，不是这个取值的证据")
    prompt_line = ("**注意：以下关于判断项的保留必须写进估值一节**（具体"
                   "条目见待核实清单 assumptions.md 上的逐行标注）："
                   + "；".join(says)
                   + "。不要把它们写得与有确凿出处的判断项同等确定。")

    body: list[str] = ["", "---", "", "## ⚠ 判断项依据核验", ""]
    if estimated:
        body += [f"本报告估值所用的驱动因子中，有 {len(estimated)} 个**取值"
                 "没有任何数据支撑、是估计**（模型在待核实清单的「理由」列"
                 "已逐条写明该因子无公开口径数据）：",
                 "", "、".join(estimated), ""]
    if unverified:
        body += [f"另有 {len(unverified)} 项判断项的依据**未能在来源里逐字"
                 "核验**（已在 assumptions.md 上逐行标注，人工批准时可见）：",
                 "", "、".join(unverified), ""]
    body += ["这是一次有意的取舍：因子分解的依据允许转述、允许在确实没有"
             "数据时给出估计，换来分解能真正跑通；代价是上述条目无法被逐字"
             "反查。请据此调整对相应判断项的信任度——它们未必错，但没有"
             "原文或数据替它们背书。", ""]
    return prompt_line, "\n".join(body)


def _report_stage(stage, arts: dict):
    """完整研报没写出来就不算「已建模完成」。

    第二阶段的同一个洞：`run_valuation` 建模成功就把 stage 设成 VALUED，
    而完整研报是**之后**才写的——`_write_full_report` 撞上额度上限时返回
    None、原因进 skipped，stage 却仍是 VALUED，于是 `complete` 为真、
    `next_actions` 返回空列表，等于告诉人「全做完了，没有待办」，而
    report.md 根本不存在。与第一阶段那次是同一类：只按预期路径下结论，
    不查结果。

    只降 VALUED 这一档：验算未通过本来就是 AWAITING_REVIEW，不该顺手
    改掉它自己的语义。
    """
    if stage is Stage.VALUED and "完整研报" not in arts:
        return Stage.INCOMPLETE
    return stage


def _audit_note(what: str, res) -> str:
    """审计发现的问题要作为非致命说明返回，供 `skipped` 用。

    **写进文件附录不等于交付了。** 两次实测都栽在这条上：600584 那次
    整段估值漏成未替换的占位符，是人读 report.md 才发现的；第二阶段那
    份研报的附录如实写了「以下引用缺少原始接口快照，无法完整回查：
    em_macro×7、eastmoney_report×13」（`audit.ok` 因此是 False），而终端
    产物清单一个字没提。终端才是读者第一眼看到的地方——降级原因、估值
    健全性告警、依据未逐字核验都往那里报，唯独审计自己发现的问题没有。

    三类一条都不吞：占位符没被替换（读者那一段根本没有数字）、引用缺
    快照（数字在，但反查不到原始接口响应）、裸数字（数字没经过账本）。
    来源名去重后再列——原始列表按脚注逐条追加，em_macro 重复 7 次，
    照抄只会刷屏；读者要知道的是哪几个来源、总共多少条。
    """
    parts: list[str] = []
    if res.unrendered:
        parts.append(f"{len(res.unrendered)} 个未替换的占位符（正文里仍是"
                     "原样的占位符，读者看不到真实数字）："
                     + "、".join(res.unrendered[:6]))
    if res.missing_snapshots:
        seen: list[str] = []
        for x in res.missing_snapshots:
            if x not in seen:
                seen.append(x)
        parts.append(f"{len(res.missing_snapshots)} 条引用缺少原始接口快照"
                     "（数字在，但反查不到当时的接口响应），来源："
                     + "、".join(seen[:6]))
    if res.bare_numbers:
        parts.append(f"{len(res.bare_numbers)} 个未经账本的裸数字："
                     + "、".join(res.bare_numbers[:6]))
    if not parts:
        return ""
    return f"{what}审计未通过：" + "；".join(parts)


def run_analysis(code: str, r, as_of: date, out_dir: str | Path,
                 model: str | None = None,
                 snapshot_dir: str | Path = "snapshots") -> ResearchOutcome:
    """第一阶段：分析 → 简报 → 辩论 → 回写 → AI 填 DCF 判断项 → 待人工核实。

    **这一阶段不建模，也不出完整研报。** 建模之前就产出一份读起来像结论的
    完整报告，读者会把未经核实的假设当成定论 —— 而那些假设恰恰是整份估值
    里最该被人看一眼的东西。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    arts: dict[str, str] = {}
    skipped: list[str] = []

    # ── 方法论预研：先问「这个行业靠什么赚钱」──────────────────
    # **必须排在 _analyse_facts（确定性层的数据采集）之前** —— 顺序
    # 反了就退化成「先收集再解释」，那正是此前的形状: 固定模板 + 填
    # 数据，无论研究什么公司问的都是同一组通用指标。
    #
    # 预研与随后的因子定向采集都要联网，client 必须提前显式开启
    # WebSearch/WebFetch 工具 —— 不开的话不会报错，只会安静地什么都
    # 搜不到，比直接失败更容易被忽略，所以单独开一个 client 而不是
    # 复用下面 `_analyse_qualitative` 里那个默认不开工具的 client。
    research_client = make_client(model=model, tools=["WebSearch", "WebFetch"])
    # 预研抛 MethodologyFailed（或取不到申万行业分类）时，_research_methodology
    # 返回 (None, 原因) 而不向上抛异常——退回不分解模式，判断项仍按营收增速/
    # 毛利率两项填写，整轮研究继续跑，只是损失了因子层面的精度。
    meth, meth_err = _research_methodology(code, r, out, research_client)
    if meth_err:
        skipped.append(meth_err)
    if meth is not None:
        arts["驱动因子分解"] = str(out / DRIVERS_MD)

    md, pr, facts_skipped = _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += facts_skipped
    qual_client = _analyse_qualitative(code, r, as_of, model, md, pr, skipped)

    # ── 按分解式定向采集因子数据 ────────────────────────────────
    # 接在确定性层之后（要用它产出的 r.sections/r.corpus 做②年报③研报
    # 两级文本命中）、撰写正文之前（采集结果要能进 DCF 判断项的提示词）。
    factor_data: dict = {}
    if meth is not None:
        from ir_agent import gaps
        from ir_agent.sources.external import Corpus
        try:
            factor_data = gaps.collect(
                research_client, meth.decompositions, r.ledger,
                r.corpus or Corpus(as_of=as_of), meth.industry, as_of,
                sections=getattr(r, "sections", None),
                annual_url=getattr(r, "annual_url", ""))
            md["因子数据"] = gaps.summary(factor_data)
        except Exception as e:                              # noqa: BLE001
            skipped.append(f"因子数据采集（{e.__class__.__name__}: {e}）")

    body, upheld, debate_md, cost, err = _write_and_debate(
        code=code, r=r, model=model, brief=True, **_prompt_bundle(pr))
    cost += qual_client.total_cost_usd + research_client.total_cost_usd
    if err and body is None:
        print(f"\n撰写/辩论中止：{err}", file=sys.stderr)
        skipped.append("研究简报")
    elif err:
        skipped.append(err)
    if debate_md:
        (out / "debate.md").write_text(debate_md, encoding="utf-8")
        arts["辩论记录"] = str(out / "debate.md")
    if body:
        r.audit = audit(body, r.ledger, r.store, as_of=r.as_of)
        (out / BRIEF).write_text(r.audit.with_sources(), encoding="utf-8")
        arts["研究简报"] = str(out / BRIEF)
        if (note := _audit_note("研究简报", r.audit)):
            skipped.append(note)

    # ── AI 填写 DCF 判断项 ────────────────────────────────
    if body:
        aerr = _propose_assumptions(
            code, r, body, md, as_of, out, qual_client, arts,
            decomps=(meth.decompositions if meth is not None else None),
            factor_data=factor_data)
        if aerr:
            skipped.append(aerr)
    else:
        skipped.append("DCF 判断项（无简报正文，假设无从追溯到分析结论）")

    build_dashboard(r, path=out / "dashboard.html", context=md)
    arts["看板"] = str(out / "dashboard.html")
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)
    arts["工作簿"] = str(out / "model.xlsx")

    (out / STATE).write_text(json.dumps(
        {"code": code, "period": r.period, "as_of": as_of.isoformat(),
         "md": md, "pr": pr, "upheld": upheld}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    # **状态按实际产物定，不按预期路径。** 见 `_review_stage`。
    stage, reason = _review_stage(arts)
    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=stage,
        artifacts=arts, skipped=skipped, upheld=upheld,
        cost_usd=Decimal(str(cost)), route_reason=reason)


def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
                         decomps=None, factor_data=None) -> str:
    """校准 → AI 填判断项 → 写出待核实清单。返回失败说明。

    `decomps`/`factor_data` 给定时按因子分解填写（方法论预研 + 定向
    采集的产出），否则退回两标量模式 —— 预研失败时的降级路径，见
    `_research_methodology`。
    """
    from ir_agent import assume

    try:
        cal = dcf.calibrate_only(code, as_of=as_of, ledger=r.ledger)
    except dcf.DcfUnavailable as e:
        return f"DCF 判断项（校准失败：{e}）"

    labels = (cal.get("periods") or {}).get("proj") or []
    ctx = "\n\n".join(f"【{k}】\n{v}" for k, v in md.items() if v)
    # 加法链（毛利率）的基期水平——与 assume.apply_to/to_driver_block
    # 里用的必须是同一个数，否则第一阶段判断项的口径与第二阶段建模的
    # 口径就各算各的了。I4：取「最近一个完整年度」，不跟着 r.period
    # 走——`--period H1` 时 r.period 是「year+H1」，直接用会取到中报。
    latest_annual = _latest_annual_period(r.ledger, as_of)
    base_margin = (_fact_value(r.ledger, "gross_margin", latest_annual, as_of)
                  if latest_annual else None)
    try:
        a = assume.decide(client, cal["judg"], labels, ctx,
                          ledger=r.ledger, body=body, as_of=as_of, code=code,
                          decomps=decomps, factor_data=factor_data,
                          base_margin=base_margin)
    except assume.AssumeFailed as e:
        return f"DCF 判断项（{str(e)[:200]}）"
    except ClaudeCliError as e:
        # 模型接口本身失败（额度用尽、超时、CLI 报错）与「填不出合格的
        # 判断项」是两件事，但处置必须一样: **记进 skipped、让整轮继续**。
        # 此前这里只捕 AssumeFailed，于是一个额度错误会以未捕获异常的形式
        # 把已经写好、已经付过钱的 brief.md 与 debate.md 连同整轮分析一起
        # 带走 —— 实测 600584 第三次实跑正是这样炸的，辩论已完成并回写了
        # 5 项挑战，却因为下一个调用撞上额度上限而一个产物都没留下。
        # run_analysis 里其他每一个联网环节都做了这件事（见产业链、因子
        # 采集、撰写/辩论），唯独这一处漏了。**能力边界不是故障，
        # 整轮研究不该因此停摆。**
        return f"DCF 判断项（{e.__class__.__name__}: {str(e)[:160]}）"

    (out / ASSUMPTIONS_MD).write_text(
        a.describe() + _APPROVE_NOTE.format(js=ASSUMPTIONS_JSON),
        encoding="utf-8")
    (out / ASSUMPTIONS_JSON).write_text(
        json.dumps(assume.to_json(a, code=code, approved_by="",
                                  approved_at=""),
                   ensure_ascii=False, indent=2), encoding="utf-8")
    arts[ASSUMPTIONS_ART] = str(out / ASSUMPTIONS_MD)
    if getattr(a, "degraded_raw", ""):
        # 降级时把最后一次的**完整**模型输出落盘。此前只有末 400 字进了
        # 降级说明，而因子模式一次要输出几十行、故障常在中段——实测排查
        # 时只能靠残缺的尾巴反推格式，每一轮都是猜。这份草稿不进
        # assumptions.md（那是给人批准的清单，不该混进失败稿），单独成文件。
        (out / FAILED_RAW).write_text(a.degraded_raw, encoding="utf-8")
        arts["因子模式失败时的原始输出"] = str(out / FAILED_RAW)
    # I4：因子模式连续失败后 assume.decide 会退回两标量模式，不是抛错——
    # 判断项照常写出，但降级这件事不能只留在 assumptions.md 里，终端的
    # skipped 清单是读者第一眼看到的地方，同一条原因要在这里再报一次。
    notes: list[str] = []
    if a.degraded:
        notes.append(a.degraded)
    # 放宽因子模式引文核验之后的代价：依据没被拒绝，但也不能只悄悄
    # 留在 assumptions.md 的表格里等人自己逐行发现——终端产物清单是
    # 读者第一眼看到的地方，同一件事要在这里也露出来，而不是非致命
    # 就等于"没有的事"。
    unverified = sum(1 for r in a.rows if getattr(r, "basis_unverified", ""))
    if unverified:
        notes.append(f"{unverified} 行依据未能逐字核验（已标注，未阻断，"
                     "需人工复核）")
    return f"DCF 判断项（{'；'.join(notes)}）" if notes else ""


_APPROVE_NOTE = """
---

## 如何批准

这些是**判断项**，模型无法从历史数据推出来，只能靠分析得出 —— 也正因此
必须有人看一眼再建模。费用率、周转天数、折旧率、税率、β 等由历史校准，
不在此列，也不需要你确认。

- 认可就直接进入第二阶段建模。
- 要改数值，改 `{js}` 里的 `rows`，再进入第二阶段；改完仍会重新校验
  （三档必须严格拉开、每年都要有值），改坏了会被挡下来而不是算出个错数。
"""


def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
                  approved_by: str, model: str | None = None,
                  snapshot_dir: str | Path = "snapshots") -> ResearchOutcome:
    """第二阶段：人工批准的假设 → 三表 DCF 建模 → 完整研报。"""
    from ir_agent import assume

    out = Path(out_dir)
    brief = out / BRIEF
    js = out / ASSUMPTIONS_JSON
    for f in (brief, js):
        if not f.exists():
            raise SystemExit(
                f"找不到 {f} —— 请先运行第一阶段：\n"
                f"  python -m ir_agent {code} --year <年度> --analyse {out}")
    if not approved_by.strip():
        raise SystemExit("必须署名批准人 —— 假设要有人负责才谈得上「已核实」。")

    arts: dict[str, str] = {}
    body = brief.read_text(encoding="utf-8")
    saved = json.loads(js.read_text(encoding="utf-8"))
    state = json.loads((out / STATE).read_text(encoding="utf-8")) \
        if (out / STATE).exists() else {"md": {}, "pr": {}}

    a, edits = assume.from_json(saved, code=code)
    if not a.rows:
        raise SystemExit(
            f"{js} 里没有判断项 —— 请先跑完第一阶段。")
    # 分解模式下重新校验要用同一套 decomps/base_margin，否则 check()
    # 会退回两标量分支，把因子模式的行误判成「营收增速缺第 N 年」而
    # 拒绝建模——那不是假设真的不合法，是校验用错了口径。
    # **人改过的数字要说出来。** 依据是为模型原来那个数写的，改了之后
    # 它未必还支撑新数字 —— 这件事代码判断不了，但必须让读者看见。
    # skipped 要累加而非被重建派生事实的结果整体覆盖——此前这里曾经是
    # `_, _, skipped = _analyse_facts(...)`，edits 非空时人工调整的说明
    # 会被那一句悄悄丢掉（edits 为空时侥幸不报错，是因为空列表压根不会
    # 走进这个 for 循环）。
    skipped: list[str] = []
    for x in edits:
        skipped.append(f"判断项经人工调整：{x}（原依据未必仍然支撑新取值）")

    # **重建派生事实必须排在校验之前。** industry.* / comps.* / macro.*
    # 这些 key 只存在于内存账本，由 _analyse_facts 算出来；第一阶段的
    # 判断项完全可以合法地引用它们（依据写成 [[industry.dtl@2025FY]]），
    # 而第二阶段是一个新进程，账本刚从接口重建、还没跑派生层。
    #
    # 实测踩到（600584 端到端）：人已经批准，第二阶段却在建模前一刻报
    # 「批准的假设不合法，拒绝建模：依据引用的事实 industry.dtl@2025FY
    # 在账本中不存在」——理由不是假设有问题，是**校验时账本还没建好**。
    # 人工批准这道关卡因此被架空：批准的东西下一步就不合法了。
    #
    # 这一层不调模型，代价只是几次网络抓取，提前跑没有副作用。
    print("重建分析层事实（不调模型）…", file=sys.stderr)
    _, _, facts_skipped = _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += facts_skipped

    # 基期毛利率也要在重建之后再取——gross_margin 同样来自派生层。
    # I4：取「最近一个完整年度」，不跟着 r.period 走——同一个理由见
    # `_propose_assumptions`，第二阶段这里必须与第一阶段用同一个基期。
    latest_annual = _latest_annual_period(r.ledger, as_of)
    base_margin = (_fact_value(r.ledger, "gross_margin", latest_annual, as_of)
                  if latest_annual else None)
    try:
        # **人改过的值要重新校验。** 批准不等于豁免 —— 三档拉不开、
        # 漏掉某一年，都会让模型算出一个看起来正常的错数。
        assume.check(a, len(a.labels), ledger=r.ledger, body=body, as_of=as_of,
                     decomps=a.decomps or None, base_margin=base_margin)
    except assume.AssumeFailed as e:
        raise SystemExit(f"批准的假设不合法，拒绝建模：\n{e}")

    md = state.get("md") or {}
    pr = state.get("pr") or {}

    dcf_res = None
    stage = Stage.NOT_APPLICABLE
    try:
        print("三表 DCF 建模（ashare-dcf）…", file=sys.stderr)
        dcf_res = dcf.run(code, out, as_of=as_of, ledger=r.ledger,
                          assumptions=a, approved_by=approved_by)
    except dcf.DcfUnavailable as e:
        skipped.append(f"估值（{e}）")
    except Exception as e:                                  # noqa: BLE001
        skipped.append(f"估值（{e.__class__.__name__}: {str(e)[:160]}）")
    else:
        arts["DCF 模型"] = dcf_res.workbook
        md["估值"] = dcf_res.summary()
        for f in dcf.to_facts(dcf_res, as_of):
            r.ledger.put(f)
        stage = Stage.VALUED if dcf_res.verified else Stage.AWAITING_REVIEW
        if not dcf_res.verified:
            skipped.append("估值（公式引擎验算未通过，结果不可采信）")
        for x in dcf_res.crosscheck:
            skipped.append(f"估值口径与账本不一致：{x}")
        # **健全性警告同样一条都不吞。** 它们此前只进了看板，没进终端
        # 产物清单 —— 而终端是读者第一眼看到的地方。实测: 终值占 EV
        # 77.7%（>75%）的警告算出来了却没交付。
        for x in dcf_res.warnings:
            skipped.append(f"估值健全性：{x}")

    cost = 0.0
    # 判断项的软处（依据未逐字核验 / 取值是估计）要跟着交付物走，见 `_assumption_caveats`。
    caveat, caveats_md = _assumption_caveats(a)
    if dcf_res is not None:
        full, cost, ferr = _write_full_report(
            code, r, body, model, dcf_res, _prompt_bundle(pr), caveat=caveat)
        if ferr:
            skipped.append(ferr)
        if full:
            r.audit = audit(full, r.ledger, r.store, as_of=r.as_of)
            # 附录追加在 `audit()` **之后**：它是代码写的、不是模型写的，
            # 里面的行数不该被当成正文里的裸数字（与数据来源附录同一处
            # 待遇）。
            (out / REPORT).write_text(r.audit.with_sources() + caveats_md,
                                      encoding="utf-8")
            arts["完整研报"] = str(out / REPORT)
            if (note := _audit_note("完整研报", r.audit)):
                skipped.append(note)
    else:
        skipped.append("完整研报（无估值结果，第 8 节无从写起）")

    # **没写出研报就不算做完。** 见 `_report_stage`。
    stage = _report_stage(stage, arts)

    build_dashboard(r, path=out / "dashboard.html", context=md, dcf=dcf_res)
    arts["看板"] = str(out / "dashboard.html")

    reason = ""
    if dcf_res is not None:
        up = dcf_res.upside
        reason = (f"每股内在价值 {dcf_res.value_per_share:,.2f} 元 vs 现价 "
                  f"{dcf_res.price:,.2f} 元"
                  + (f"，空间 {up:+.1%}" if up is not None else "")
                  + f"（WACC {dcf_res.wacc:.2%}，终值占企业价值 "
                  f"{dcf_res.terminal_share:.1%}；判断项经 {approved_by} 核实）")

    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=stage,
        artifacts=arts, skipped=skipped, upheld=0,
        cost_usd=Decimal(str(cost)), route_reason=reason)


def _write_full_report(code, r, brief, model, dcf_res, bundle, caveat=""):
    """在简报之上补出完整研报 —— 第 8 节要拿估值结果与前面各节对账。"""
    from ir_agent.analyst import FactCatalog, write_body

    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    extra = _macro_periods(r) + ((spot,) if spot else ())
    cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=r.as_of,
                                  extra_periods=extra)
    cat.evidence = _build_evidence(r, code)
    client = make_client(model=model)
    try:
        print("撰写完整研报（含估值一节）…", file=sys.stderr)
        full, _ = write_body(cat, client, code=code, period=r.period,
                             valuation_summary=(dcf_res.summary()
                                                + ("\n\n" + caveat if caveat else "")),
                             context=f"**已完成的分析简报**\n\n{brief}",
                             **{k: v or None for k, v in bundle.items()})
        return full, client.total_cost_usd, ""
    except (AnalystRefused, ClaudeCliError) as e:
        return None, client.total_cost_usd, \
            f"完整研报（{e.__class__.__name__}: {str(e)[:160]}）"


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
