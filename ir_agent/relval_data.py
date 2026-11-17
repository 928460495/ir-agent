"""相对估值的取数与口径统一。

**不重新取财报。** 第二阶段的确定性层（`full._load_industry`）已经把全行业每家的
完整报表取进了 `cache`，这里复用它（`r.peer_statements`）——东财有频控，46 家
重取一次就是十几分钟。行情另外一次批量请求，原始响应落快照。

**同业与本司走同一条算子链。** 同业账本由报表事实装起来，依次跑与 pipeline 相同的
`margins` → `dupont`（得 ROE）→ `aggregates`（折旧摊销、有息负债合计）→ TTM；
本司直接用主流程账本。倍数一律由 `relval.multiples` 按同一定义算，**不用**行情
接口自带的 PE/PB 字段（试验里两者差了 50%）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.market import quote_symbol
from ir_agent.relval import PeerObs
from ir_agent.sources.quotes import close_asof, fetch_quotes



class RelvalUnavailable(Exception):
    """相对估值整体做不了（行业样本没取到、行情整体失败、本司市值缺失）。"""


def _v(led: FactLedger, key: str, period: str, as_of: date) -> Decimal | None:
    try:
        return led.get(key, period, as_of=as_of).value
    except (KeyError, LookAheadError):
        return None


def _prepare(led: FactLedger, period: str, as_of: date) -> None:
    """同业账本补齐派生事实 —— 与 pipeline.py 的算子顺序一致。算不出的跳过。"""
    from ir_agent.operators import aggregates, dupont, margins

    for fn in (margins.compute, dupont.compute, aggregates.compute):
        try:
            fn(led, period, as_of)
        except Exception:                                   # noqa: BLE001
            pass


def _prev_quarter(q: tuple[int, int]) -> tuple[int, int]:
    y, n = q
    return (y, n - 1) if n > 1 else (y - 1, 4)


_CUM_SUFFIX = {1: "Q1", 2: "H1", 3: "Q1-Q3", 4: "FY"}


def _label(q: tuple[int, int]) -> str:
    return f"{q[0]}{_CUM_SUFFIX[q[1]]}"


def _peer_ttm(led: FactLedger, end: tuple[int, int] | None,
              as_of: date) -> tuple[Decimal, str] | None:
    """同业 PE 分母：对齐本司的截止季度；该季没出就用上一季的 TTM（用户决定）。
    都算不出返回 None，由调用方退回年报。"""
    from ir_agent.operators import ttm

    if end is None:
        return None
    for q in (end, _prev_quarter(end)):
        f = ttm.ttm_at(led, "net_profit_attr_parent", q[0], q[1], as_of=as_of)
        if f is not None:
            return f.value, f"TTM 截至 {f.period}"
        # 拆单季要有一季报 / 三季报的累计数；缺它们时用半年度公式（与 EBITDA 同路径）
        if q[1] in _HALF_ENDS:
            v = flow_ttm(led, "net_profit_attr_parent", q, as_of)
            if v is not None:
                return v, f"TTM 截至 {_label(q)}"
    return None


# ── 存量与流量：取更近的数 ──────────────────────────────────────
# 存量（净资产、现金、有息负债、少数股东权益）是某一时点的余额，可以直接取最近
# 一期报表；流量（营业利润、折旧摊销、利息、营收）要滚动十二个月。
#
# **A 股折旧摊销只在中报与年报披露**（现金流量表补充资料），所以流量 TTM 的截止期
# 只能是 H1 或 FY。好在半年度 TTM = 本期 H1 + 上年 FY − 上年 H1，恰好只用这两期，
# 不需要拆单季。

_HALF_ENDS = (2, 4)
_EBITDA_PARTS = ("operating_profit", "depreciation_amortization", "interest_expense_fe")


def balance_period(led: FactLedger, as_of: date, probe: str = "equity_attr_parent") -> str | None:
    """存量取哪一期：probe 已披露的最新累计期间（如 2026H1）。"""
    from ir_agent.operators.ttm import latest_visible

    f = latest_visible(led, probe, as_of)
    return f.period if f is not None else None


def _ensure_da(led: FactLedger, period: str, as_of: date) -> None:
    """折旧摊销在报表里只有分量，合计由 aggregates 算 —— 用到哪期就先算那期。"""
    from ir_agent.operators import aggregates

    try:
        aggregates.compute(led, period, as_of)
    except Exception:                                       # noqa: BLE001
        pass


def flow_ttm(led: FactLedger, key: str, end: tuple[int, int],
             as_of: date) -> Decimal | None:
    """截至 end 的流量 TTM。end 的季度必须是 2（中报）或 4（年报）。"""
    y, q = end
    if q == 4:
        return _v(led, key, _label((y, 4)), as_of)
    if q != 2:
        return None
    cur = _v(led, key, _label((y, 2)), as_of)
    prev_fy = _v(led, key, _label((y - 1, 4)), as_of)
    prev_h1 = _v(led, key, _label((y - 1, 2)), as_of)
    if None in (cur, prev_fy, prev_h1):
        return None
    return cur + prev_fy - prev_h1


def flow_end(led: FactLedger, as_of: date) -> tuple[int, int] | None:
    """流量 TTM 取哪一期：EBITDA 三个分量都算得出来的最新 H1 / FY。"""
    cands: list[tuple[int, int]] = []
    for f_end in sorted({(int(p[:4]), _Q_OF.get(p[4:]))
                         for k, p in list(led._facts)              # noqa: SLF001
                         if k == "operating_profit" and _Q_OF.get(p[4:]) in _HALF_ENDS},
                        reverse=True):
        cands.append(f_end)
    for end in cands:
        for per in (_label(end), _label((end[0] - 1, 4)), _label((end[0] - 1, 2))):
            _ensure_da(led, per, as_of)
        if all(flow_ttm(led, k, end, as_of) is not None for k in _EBITDA_PARTS):
            return end
    return None


_Q_OF = {"Q1": 1, "H1": 2, "Q1-Q3": 3, "FY": 4}


def _ebitda_ttm(led: FactLedger, end: tuple[int, int] | None,
                as_of: date) -> tuple[Decimal, str] | None:
    """EBITDA = 营业利润 + 折旧摊销 + max(利息费用, 0)，三项都取同一截止期的 TTM。
    同业没出本期就退上一期（与 PE 同一规则）。"""
    if end is None:
        return None
    for q in (end, _prev_quarter(end) if end[1] == 4 else (end[0] - 1, 4)):
        for per in (_label(q), _label((q[0] - 1, 4)), _label((q[0] - 1, 2))):
            _ensure_da(led, per, as_of)
        vals = [flow_ttm(led, k, q, as_of) for k in _EBITDA_PARTS]
        if all(v is not None for v in vals):
            op, da, ie = vals
            return op + da + max(ie, Decimal(0)), f"TTM 截至 {_label(q)}"
    return None


def _growth(led: FactLedger, period: str, as_of: date,
            end: tuple[int, int] | None) -> tuple[Decimal | None, str]:
    """营收三年复合增速。优先用 TTM 营收（与倍数同口径），缺数据退回年报三年、再退一年。"""
    if end is not None:
        now = flow_ttm(led, "revenue", end, as_of)
        then = flow_ttm(led, "revenue", (end[0] - 3, end[1]), as_of)
        if now and then and now > 0 and then > 0:
            g = (float(now) / float(then)) ** (1 / 3) - 1
            # 截止期是年报时 TTM 就是该财年，标签照实写
            return Decimal(str(round(g, 10))), "3y" if end[1] == 4 else "TTM 3y"
    y = int(period[:4])
    now = _v(led, "revenue", f"{y}FY", as_of)
    if now is None or now <= 0:
        return None, ""
    for back, basis in ((3, "3y"), (1, "1y")):
        then = _v(led, "revenue", f"{y - back}FY", as_of)
        if then is not None and then > 0:
            g = (float(now) / float(then)) ** (1 / back) - 1
            return Decimal(str(round(g, 10))), basis
    return None, ""


def obs_from_ledger(code: str, name: str, led: FactLedger, period: str,
                    as_of: date, market_cap: Decimal,
                    np_ttm: tuple[Decimal, str] | None = None,
                    bs_period: str | None = None,
                    flow_end_q: tuple[int, int] | None = None) -> PeerObs:
    """账本 → 观测。存量取最近一期余额，流量取 TTM，缺数据退回 period（最近完整财年）。

    np_ttm / bs_period / flow_end_q 给定时按它们取（同业：已对齐本司）；本司由账本自测。
    ROE 由 TTM 利润 ÷ 最新净资产算出，与 PB ÷ PE 自洽，不读账本里按年报算的 roe。
    """
    from ir_agent.operators.aggregates import DEBT_PARTS
    from ir_agent.operators.ttm import latest_visible

    if np_ttm is None and (f := latest_visible(led, "net_profit_ttm", as_of)) is not None:
        np_ttm = (f.value, f"TTM 截至 {f.period}")
    if np_ttm is not None:
        net_profit, basis = np_ttm
    else:
        net_profit, basis = _v(led, "net_profit_attr_parent", period, as_of), "FY"

    # 存量：对齐给定的余额期；该期没出就退回 period
    bs = bs_period or balance_period(led, as_of) or period
    if _v(led, "equity_attr_parent", bs, as_of) is None:
        bs = period
    def bv(key: str) -> Decimal | None:
        return _v(led, key, bs, as_of)
    debt = sum((v for k in DEBT_PARTS if (v := bv(k)) is not None), Decimal(0))

    # 流量：EBITDA 与增速
    fe = flow_end_q if flow_end_q is not None else flow_end(led, as_of)
    eb = _ebitda_ttm(led, fe, as_of)
    if eb is None:
        op, da, ie = (_v(led, k, period, as_of) for k in _EBITDA_PARTS)
        if op is not None and da is not None:
            eb = (op + da + max(ie or Decimal(0), Decimal(0)), f"FY {period}")
    growth, gbasis = _growth(led, period, as_of, fe)

    equity_parent = bv("equity_attr_parent")
    roe = (net_profit / equity_parent
           if net_profit is not None and equity_parent and equity_parent > 0 else None)
    return PeerObs(code=code, name=name, market_cap=market_cap,
                   net_profit=net_profit, np_basis=basis,
                   equity_parent=equity_parent, total_equity=bv("total_equity"),
                   debt=debt, cash=bv("cash_and_equivalents"),
                   ebitda=eb[0] if eb else None, roe=roe,
                   growth=growth, growth_basis=gbasis,
                   bs_basis=bs, flow_basis=eb[1] if eb else "")


def obs_from_facts(code: str, name: str, facts: dict, period: str,
                   market_cap: Decimal, as_of: date,
                   ttm_end: tuple[int, int] | None = None,
                   bs_period: str | None = None,
                   flow_end: tuple[int, int] | None = None) -> PeerObs:
    """同业：报表事实 → 账本 → 补派生事实 → 观测。三个口径都对齐本司。"""
    led = FactLedger()
    for f in facts.values():
        led.put(f)
    _prepare(led, period, as_of)
    return obs_from_ledger(code, name, led, period, as_of, market_cap,
                           np_ttm=_peer_ttm(led, ttm_end, as_of),
                           bs_period=bs_period, flow_end_q=flow_end)


def fund_period(period: str) -> str:
    """基本面取最近一个完整财年：报告期是 FY 就是它本身，中报期取上一年 FY。

    中报的利润、EBITDA 是半年流量，直接算倍数会差一倍；增速也不能锚在当年尚未
    披露的 FY 上。本司与同业用同一个期间，口径才一致。
    """
    return period if period.endswith("FY") else f"{int(period[:4]) - 1}FY"


def _disclosed(facts: dict, period: str, as_of: date) -> bool:
    return all((f := facts.get((k, period))) is not None and f.as_of <= as_of
               for k in ("revenue", "equity_attr_parent"))


def _peer_closes(snapshot_dir, day: date) -> tuple[dict, "Path"]:
    """同业在本司报价日的收盘，按报价日落盘：{symbol: [日期, 收盘]}。重跑同一时点不再逐家取日 K。"""
    import json
    from pathlib import Path
    path = Path(snapshot_dir) / f"peer_close_{day.isoformat()}.json"
    try:
        return json.loads(path.read_text("utf-8")), path
    except (OSError, ValueError):
        return {}, path


def _close_on(symbol: str, day: date, cache: dict) -> tuple[date, Decimal]:
    if symbol not in cache:
        d, px = close_asof(symbol, day)
        cache[symbol] = [d.isoformat(), str(px)]
    d, px = cache[symbol]
    return date.fromisoformat(d), Decimal(px)


def gather(r, as_of: date, snapshot_dir):
    """返回 (本司, 行业样本, 可比, 总股本, 现价, 剔除说明)。

    可比 = 行业样本中 `r.comps_codes` 那几家 —— **同一份观测**，保证可比法与回归同口径。
    截至 as_of 尚无该期财报的同业剔除，不用上期凑（与 comps.py 的立场一致）。
    **没进样本的每一家都留一条说明**：财报没取到、没有行情、行情停在旧日期、未披露。
    """
    from ir_agent.operators import aggregates
    from ir_agent.sources.snapshot import SnapshotStore

    cache = getattr(r, "peer_statements", None)
    peers = getattr(r, "peer_set", None)
    if not cache or peers is None:
        raise RelvalUnavailable("行业样本未取到（第二阶段行业聚合失败或未运行）")
    fp = fund_period(r.period)
    tcode = peers.target
    names = {m.code: m.name for m in peers.members}
    notes: list[str] = [f"{m.code} {m.name}：财报未取到（行业聚合时取数失败），剔除"
                        for m in peers.members if m.code not in cache]
    codes = [c for c in cache if c != tcode]

    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    try:
        mc_fact = r.ledger.get("market_cap", spot, as_of=as_of) if spot else None
    except (KeyError, LookAheadError):
        mc_fact = None
    price = _v(r.ledger, "last_price", spot, as_of) if spot else None
    if mc_fact is None or not mc_fact.value or not price:
        raise RelvalUnavailable("本司市值或现价缺失")
    quote_day = mc_fact.as_of
    # 本司 PE 分母的截止季度 —— 同业对齐它（没出就退上一季）
    from ir_agent.operators.ttm import latest_quarter
    ttm_end = latest_quarter(r.ledger, "net_profit_attr_parent", as_of)
    # 存量与流量的截止期同样由本司定，同业对齐（没出则退一期）
    tgt_bs = balance_period(r.ledger, as_of)
    tgt_flow = flow_end(r.ledger, as_of)

    symbols = [quote_symbol(c) for c in codes]
    try:
        got, raws = fetch_quotes(symbols)
    except Exception as e:                                  # noqa: BLE001
        raise RelvalUnavailable(f"行业样本的批量行情取不到（{e.__class__.__name__}）") from e
    store, now = SnapshotStore(snapshot_dir), datetime.now()
    for url, raw in raws:
        store.save(source="tencent_batch", payload={"raw": raw}, url=url,
                   fetched_at=now, params={"url": url})

    sample: list[PeerObs] = []
    closes, close_path = _peer_closes(snapshot_dir, quote_day)
    n_cached = len(closes)
    for c in codes:
        name = names.get(c, c)
        q = got.get(c)
        if q is None or q.get("market_cap") is None:
            notes.append(f"{c} {name}：无行情（停牌或接口未返回），剔除")
            continue
        if q.get("as_of") is not None and q["as_of"].date() > quote_day and q.get("last"):
            # 本司报价日早于同业实时行情日（分析时点早于今天）：同业也换成本司报价日的收盘，
            # 市值按价格同比缩放（股本沿用实时口径）。否则两边不是同一天的价格。
            try:
                d, px = _close_on(quote_symbol(c), quote_day, closes)
            except Exception as e:                          # noqa: BLE001
                notes.append(f"{c} {name}：{quote_day} 收盘取不到（{e.__class__.__name__}），剔除")
                continue
            if d != quote_day:
                notes.append(f"{c} {name}：{quote_day} 无成交（最近收盘 {d}，可能停牌），剔除")
                continue
            q = dict(q, market_cap=q["market_cap"] * px / q["last"], last=px,
                     as_of=datetime(d.year, d.month, d.day))
        if q.get("as_of") is not None and q["as_of"].date() != quote_day:
            notes.append(f"{c} {name}：行情停在 {q['as_of'].date()}，与本司报价日 "
                         f"{quote_day} 不同（可能停牌），剔除")
            continue
        if not _disclosed(cache[c], fp, as_of):
            notes.append(f"{c} {name}：截至估值时点未披露 {fp} 财报，剔除（不用上期凑）")
            continue
        sample.append(obs_from_facts(c, name, cache[c], fp, q["market_cap"], as_of,
                                     ttm_end=ttm_end, bs_period=tgt_bs,
                                     flow_end=tgt_flow))

    if len(closes) != n_cached:
        import json
        try:
            close_path.parent.mkdir(parents=True, exist_ok=True)
            close_path.write_text(json.dumps(closes, ensure_ascii=False, indent=1), "utf-8")
        except OSError:
            pass

    try:
        aggregates.compute(r.ledger, fp, as_of)
    except Exception:                                       # noqa: BLE001
        pass
    target = obs_from_ledger(tcode, peers.target_name, r.ledger, fp, as_of, mc_fact.value,
                             bs_period=tgt_bs, flow_end_q=tgt_flow)

    by_code = {o.code: o for o in sample}
    comps_codes = getattr(r, "comps_codes", None) or []
    notes += [f"可比 {c} {names.get(c, c)}：未进入样本（原因见上），可比法少一家"
              for c in comps_codes if c not in by_code]
    comps = [by_code[c] for c in comps_codes if c in by_code]
    return target, sample, comps, mc_fact.value / price, price, notes
