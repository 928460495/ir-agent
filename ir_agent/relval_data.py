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
from ir_agent.sources.quotes import fetch_quotes

_QUARTER = {"FY": 4, "Q1-Q3": 3, "H1": 2, "Q1": 1}


class RelvalUnavailable(Exception):
    """相对估值整体做不了（行业样本没取到、行情整体失败、本司市值缺失）。"""


def _v(led: FactLedger, key: str, period: str, as_of: date) -> Decimal | None:
    try:
        return led.get(key, period, as_of=as_of).value
    except (KeyError, LookAheadError):
        return None


def _prepare(led: FactLedger, period: str, as_of: date) -> None:
    """同业账本补齐派生事实 —— 与 pipeline.py 的算子顺序一致。算不出的跳过。"""
    from ir_agent.operators import aggregates, dupont, margins, ttm
    from ir_agent.operators import periods as P

    for fn in (margins.compute, dupont.compute, aggregates.compute):
        try:
            fn(led, period, as_of)
        except Exception:                                   # noqa: BLE001
            pass
    quarter = _QUARTER.get(period[4:])
    if quarter is None:
        return
    year = int(period[:4])
    for y in (year, year - 1):
        try:
            P.decumulate(led, "net_profit_attr_parent", y, as_of=as_of)
        except Exception:                                   # noqa: BLE001
            pass
    try:
        ttm.compute(led, "net_profit_attr_parent", year, quarter, as_of=as_of,
                    target_period=period)
    except Exception:                                       # noqa: BLE001
        pass


def _growth(led: FactLedger, period: str, as_of: date) -> tuple[Decimal | None, str]:
    """三年营收复合增速；缺数据退回一年。"""
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
                    as_of: date, market_cap: Decimal) -> PeerObs:
    """账本 → 观测。口径见 Global Constraints：基本面取报告期，利润优先 TTM。"""
    from ir_agent.operators.aggregates import DEBT_PARTS

    np_ttm = (_v(led, "net_profit_ttm", period, as_of)
              or _v(led, "net_profit_attr_parent_ttm", period, as_of))
    if np_ttm is not None:
        net_profit, basis = np_ttm, "TTM"
    else:
        net_profit, basis = _v(led, "net_profit_attr_parent", period, as_of), "FY"
    op = _v(led, "operating_profit", period, as_of)
    da = _v(led, "depreciation_amortization", period, as_of)
    ie = _v(led, "interest_expense_fe", period, as_of)
    ebitda = (op + da + max(ie or Decimal(0), Decimal(0))
              if op is not None and da is not None else None)
    debt = sum((v for k in DEBT_PARTS if (v := _v(led, k, period, as_of)) is not None),
               Decimal(0))
    growth, gbasis = _growth(led, period, as_of)
    return PeerObs(code=code, name=name, market_cap=market_cap,
                   net_profit=net_profit, np_basis=basis,
                   equity_parent=_v(led, "equity_attr_parent", period, as_of),
                   total_equity=_v(led, "total_equity", period, as_of),
                   debt=debt, cash=_v(led, "cash_and_equivalents", period, as_of),
                   ebitda=ebitda, roe=_v(led, "roe", period, as_of),
                   growth=growth, growth_basis=gbasis)


def obs_from_facts(code: str, name: str, facts: dict, period: str,
                   market_cap: Decimal, as_of: date) -> PeerObs:
    """同业：报表事实 → 账本 → 补派生事实 → 观测。"""
    led = FactLedger()
    for f in facts.values():
        led.put(f)
    _prepare(led, period, as_of)
    return obs_from_ledger(code, name, led, period, as_of, market_cap)


def fund_period(period: str) -> str:
    """基本面取最近一个完整财年：报告期是 FY 就是它本身，中报期取上一年 FY。

    中报的利润、EBITDA 是半年流量，直接算倍数会差一倍；增速也不能锚在当年尚未
    披露的 FY 上。本司与同业用同一个期间，口径才一致。
    """
    return period if period.endswith("FY") else f"{int(period[:4]) - 1}FY"


def _disclosed(facts: dict, period: str, as_of: date) -> bool:
    return all((f := facts.get((k, period))) is not None and f.as_of <= as_of
               for k in ("revenue", "equity_attr_parent"))


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
    for c in codes:
        name = names.get(c, c)
        q = got.get(c)
        if q is None or q.get("market_cap") is None:
            notes.append(f"{c} {name}：无行情（停牌或接口未返回），剔除")
            continue
        if q.get("as_of") is not None and q["as_of"].date() != quote_day:
            notes.append(f"{c} {name}：行情停在 {q['as_of'].date()}，与本司报价日 "
                         f"{quote_day} 不同（可能停牌），剔除")
            continue
        if not _disclosed(cache[c], fp, as_of):
            notes.append(f"{c} {name}：截至估值时点未披露 {fp} 财报，剔除（不用上期凑）")
            continue
        sample.append(obs_from_facts(c, name, cache[c], fp, q["market_cap"], as_of))

    try:
        aggregates.compute(r.ledger, fp, as_of)
    except Exception:                                       # noqa: BLE001
        pass
    target = obs_from_ledger(tcode, peers.target_name, r.ledger, fp, as_of, mc_fact.value)

    by_code = {o.code: o for o in sample}
    comps_codes = getattr(r, "comps_codes", None) or []
    notes += [f"可比 {c} {names.get(c, c)}：未进入样本（原因见上），可比法少一家"
              for c in comps_codes if c not in by_code]
    comps = [by_code[c] for c in comps_codes if c in by_code]
    return target, sample, comps, mc_fact.value / price, price, notes
