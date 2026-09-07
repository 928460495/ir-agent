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
from ir_agent.valuation.scenarios import Case as SCase
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
                      chain_summary: str = "", consensus_summary: str = ""):
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
                             consensus_summary=consensus_summary or None)

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

    # 周期定位放在宏观与利率之后 —— 政策维度用的是期限利差
    cycle_md, cycle_pr, cycle_err = _load_cycle(r, as_of)
    if cycle_err:
        skipped.append(cycle_err)
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

    # 外部资料必须在战略与产业链之前 —— 它们的定性判断要引用其中的原文。
    ext_md, ext_pr, ext_err = _load_external(code, r, as_of)
    if ext_err:
        skipped.append(ext_err)

    # 战略与产业链要调模型，因此共用一个 client 以便统计开销。
    qual_client = make_client(model=model)
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    mview_md, mview_pr, mview_err = _load_macro_view(
        r, as_of, qual_client, macro_pr)
    if mview_err:
        skipped.append(mview_err)
    strat_md, strat_pr, strat_err = _load_strategy(r, as_of, qual_client, spot)
    if strat_err:
        skipped.append(strat_err)
    chain_md, chain_pr, chain_err = _load_value_chain(r, as_of, qual_client)
    if chain_err:
        skipped.append(chain_err)

    body, upheld, debate_md, cost, err = _write_and_debate(
        code=code, r=r, model=model, comps_summary=comps_pr,
        macro_summary=(macro_pr + ("\n\n" + mview_pr if mview_pr else "")),
        industry_summary=industry_pr,
        strategy_summary=strat_pr, chain_summary=chain_pr,
        consensus_summary=ext_pr)
    cost += getattr(qual_client, "total_cost_usd", 0.0)
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

    ctx = {"宏观": macro_md, "宏观定性": mview_md, "商业周期": cycle_md, "折现率输入": capm_md,
           "行业": industry_md, "产业链": chain_md, "可比": comps_md,
           "战略": strat_md, "外部资料": ext_md}

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
            if getattr(r, "scenario_err", ""):
                skipped.append(r.scenario_err)
            if getattr(r, "scenario_md", ""):
                ctx["情景"] = r.scenario_md
    elif rt is not None and rt.method is ValuationMethod.PB_ROE:
        # **路由判对了，另一条路没修。** 强周期标的（半导体、钢铁、化工、
        # 有色、券商……）会被正确地判给 PB–ROE，但那个方法只有枚举值、
        # 没有实现，于是整轮不出任何估值。
        #
        # 此前这种情况只留下一个 stage=not_applicable，读起来像「本标的
        # 不适合估值」—— 而真相是「本标的适合 PB–ROE，是我们还没做」。
        # 静默地少做一块，与 V0 的「缺数据时跳过而非通过」是同一条原则:
        # 缺什么必须显式说出来。
        skipped.append(
            f"估值（路由判定应使用 PB–ROE：{rt.reason}；"
            "该方法尚未实现，因此本轮不出估值。"
            "这不是「本标的无法估值」，是本系统尚缺这条路径）")

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
        scen = getattr(r, "scenarios", None)
        if scen is not None:
            # **区间比点估计更有信息量。** 只报一个数会让读者以为它精确，
            # 而这个数完全取决于几条本就不确定的假设。
            lo = scen.values[SCase.BEAR].value_per_share
            hi = scen.values[SCase.BULL].value_per_share
            reason += f"；情景区间 {lo:,.2f}~{hi:,.2f} 元"

    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=stage,
        artifacts=arts, skipped=skipped, upheld=upheld,
        cost_usd=Decimal(str(cost)), route_reason=reason)


def _load_scenarios(r, body, assumptions, as_of, client, spot):
    """把战略分析识别的不确定性落成 Bear/Base/Bull。

    与 stage2 那张 5×5 网格是两回事: 网格回答「假设错了多少，估值差多少」，
    换成任何一家公司长得都一样；情景回答「战略分析识别出的那几条不确定性
    一旦兑现，这家公司值多少钱」。
    """
    from ir_agent.valuation import scenarios as SC
    from ir_agent.valuation.inputs import (MissingInput, free_cash_flow,
                                           net_debt, shares_outstanding)

    view = getattr(r, "strategy_view", None)
    if view is None or not view.uncertainties:
        return None, "", "情景分析（无战略分析产出的关键不确定性）"
    try:
        fcf = free_cash_flow(r.ledger, r.period, as_of=as_of)
        nd = net_debt(r.ledger, r.period, as_of=as_of)
        sh = shares_outstanding(r.ledger, spot or r.period, as_of=as_of)
    except MissingInput as e:
        return None, "", f"情景分析（估值输入缺失：{e}）"

    unc = "\n".join(f"  · {u.what} → {u.driver}（不利时{u.direction}）"
                     for u in view.uncertainties)
    base_txt = (f"  收入增速逐年 {'、'.join(str(g) for g in assumptions.growth_rates)}"
                f"\n  WACC {assumptions.wacc}"
                f"\n  永续增速 {assumptions.terminal_growth}")
    prompt = SC.PROMPT.format(
        uncertainties=unc, base=base_txt,
        anchors="　".join(SC.anchors(r.ledger, as_of)) or "（无）",
        drivers="、".join(SC.MODELLABLE))

    last = ""
    for attempt in range(1, 4):
        try:
            shifts = SC.parse(client(prompt))
            out = SC.build(assumptions, shifts, view.uncertainties,
                           fcf_base=fcf.value, net_debt=nd.value,
                           shares=sh.value, ledger=r.ledger, body=body,
                           as_of=as_of, period=r.period)
        except SC.ScenarioFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新输出全部情景行。\n")
            continue
        for f in SC.scenario_facts(out, r.period, as_of):
            r.ledger.put(f)
        return out, out.summary(), ""
    return None, "", f"情景分析（连续三次不可核验：{last[:100]}）"


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

    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    try:
        res = value_from_assumptions(
            assumptions=d.assumptions, ledger=r.ledger, body=body,
            period=r.period, spot_period=spot or r.period, as_of=as_of)
    except ValuationBlocked as e:
        return None, None, None, f"估值（{e}）", cost

    (out / "assumptions.yaml").write_text(
        to_yaml(d.assumptions, header=(
            "# 估值假设 —— 由**模型自主决定**，未经人工审核。\n"
            "# 已通过的机器校验: 永续 ≤ 宏观事实算出的上限、永续 < WACC、\n"
            "# 各项依据可核验、WACC 的论证出自本报告正文。"))
        + "\n# ── 模型给出的理由原文 ──\n"
        + "\n".join("# " + ln for ln in d.rationale.split("\n")),
        encoding="utf-8")

    scen, scen_md, scen_err = _load_scenarios(
        r, body, d.assumptions, as_of, client, spot)
    r.scenarios, r.scenario_md, r.scenario_err = scen, scen_md, scen_err

    # client 的 total_cost_usd 是**累计值** —— 中途加一次、末尾再加一次
    # 会把前半段重复计入。整段用同一个 client，因此只在这里加一次。
    return d.assumptions, res.valuation, res.sensitivity, "", \
        cost + client.total_cost_usd


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
    capm_md, _, _ = _load_capm(code, r, as_of)
    try:
        industry_md, _, _ = _load_industry(code, r, as_of)
    except Exception:                                       # noqa: BLE001
        industry_md = ""
    build_dashboard(r, path=out / "dashboard.html", route=rt,
                    context={"宏观": macro_md, "折现率输入": capm_md,
                             "行业": industry_md},
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
