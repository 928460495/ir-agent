"""相对估值的取数：同业与本司走同一条算子链，口径只有一份。不联网。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ir_agent import relval_data
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.operators import dupont, margins
from ir_agent.periods_spot import spot_period
from ir_agent.relval_data import RelvalUnavailable, gather, obs_from_facts

D = Decimal
AS_OF = date(2026, 10, 21)
SPOT = spot_period(AS_OF)


def F(key, period, value, as_of, unit="元"):
    return Fact(key=key, value=D(str(value)), unit=unit, period=period, as_of=as_of,
                source_id="em_x", method=Method.REPORTED, currency="CNY")


def facts_years(years=(2022, 2023, 2024, 2025)):
    """一家公司逐年的年报事实（营收每年 +10%）。"""
    out = {}
    for i, y in enumerate(years):
        p, disc = f"{y}FY", date(y + 1, 4, 25)
        vals = dict(revenue=100 * 1.1 ** i, net_profit_attr_parent=10, net_profit=11,
                    equity_attr_parent=50, total_equity=60, total_assets=120,
                    cash_and_equivalents=5, short_loan=20, operating_profit=15,
                    interest_expense_fe=1, depreciation_fixed_asset=7)
        for k, v in vals.items():
            out[(k, p)] = F(k, p, round(v, 6), disc)
    return out


def _obs(facts, code="002384"):
    return obs_from_facts(code, "某同业", facts, "2025FY", D(1000), AS_OF)


def test_ROE与账本同一算子链():
    led = FactLedger()
    for f in facts_years().values():
        led.put(f)
    margins.compute(led, "2025FY", as_of=AS_OF)
    dupont.compute(led, "2025FY", as_of=AS_OF)
    assert _obs(facts_years()).roe == led.get("roe", "2025FY", as_of=AS_OF).value


def test_三年增速与一年退路():
    o = _obs(facts_years())
    assert o.growth_basis == "3y"
    assert float(o.growth) == pytest.approx(0.1, rel=1e-4)
    assert _obs(facts_years((2024, 2025))).growth_basis == "1y"


def test_TTM取不到用FY并标注():
    o = _obs(facts_years())
    assert o.np_basis == "FY" and o.net_profit == D(10)


def test_EBITDA与有息负债走同一套合计():
    """EBITDA = 营业利润 + 折旧摊销 + 利息费用；折旧摊销与有息负债由分量合计。"""
    o = _obs(facts_years())
    assert o.ebitda == D(15 + 7 + 1)
    assert o.debt == D(20)
    assert o.minority == D(10)


# ── gather：样本、可比子集、行情快照、剔除说明 ──────────────────────

def _quote(mc):
    return {"market_cap": D(mc), "last": D(50), "code": "", "name": ""}


def fake_r(peers: dict[str, dict], comps=("002384",)):
    tgt = FactLedger()
    for f in facts_years().values():
        tgt.put(f)
    mc = F("market_cap", SPOT, 5000, AS_OF)
    px = F("last_price", SPOT, 50, AS_OF)
    tgt.put(mc)
    tgt.put(px)
    members = [SimpleNamespace(code=c, name=f"同业{c}") for c in peers]
    return SimpleNamespace(
        period="2025FY", ledger=tgt, quotes=SimpleNamespace(facts=[mc, px]),
        peer_statements={"600183": facts_years(), **peers},
        peer_set=SimpleNamespace(target="600183", target_name="生益科技", members=members,
                                 classification=SimpleNamespace(used_path="电子-元件-印制电路板")),
        comps_codes=list(comps))


def _patch_quotes(monkeypatch, codes):
    def fake(symbols, **kw):
        got = {c: _quote(1000) for c in codes}
        return got, [("URL", "RAW")]
    monkeypatch.setattr(relval_data, "fetch_quotes", fake)


def test_本司与样本口径(monkeypatch, tmp_path):
    _patch_quotes(monkeypatch, ["600183", "002384", "002463"])
    tgt, sample, comps, shares, price, notes = gather(
        fake_r({"002384": facts_years(), "002463": facts_years()}), AS_OF, tmp_path)
    assert tgt.code == "600183" and tgt.market_cap == D(5000)
    assert shares == D(100) and price == D(50)          # 市值 ÷ 现价
    assert {o.code for o in sample} == {"002384", "002463"}
    assert [o.code for o in comps] == ["002384"]        # 可比是样本里的子集，同一份观测


def test_行情原始响应落快照(monkeypatch, tmp_path):
    _patch_quotes(monkeypatch, ["600183", "002384"])
    gather(fake_r({"002384": facts_years()}), AS_OF, tmp_path)
    assert any(p.name.startswith("tencent_batch_") for p in tmp_path.iterdir())


def test_停牌缺行情的同业被剔除并说明(monkeypatch, tmp_path):
    _patch_quotes(monkeypatch, ["600183"])
    _, sample, _, _, _, notes = gather(fake_r({"002384": facts_years()}), AS_OF, tmp_path)
    assert all(o.code != "002384" for o in sample)
    assert any("002384" in n and "行情" in n for n in notes)


def test_未披露本期财报的同业剔除而不用上期凑(monkeypatch, tmp_path):
    _patch_quotes(monkeypatch, ["600183", "002463"])
    _, sample, _, _, _, notes = gather(
        fake_r({"002463": facts_years((2022, 2023, 2024))}), AS_OF, tmp_path)
    assert all(o.code != "002463" for o in sample)
    assert any("002463" in n and "未披露" in n for n in notes)


def test_没有行业样本时明确不可用(tmp_path):
    r = fake_r({})
    del r.peer_statements
    with pytest.raises(RelvalUnavailable):
        gather(r, AS_OF, tmp_path)


def test_行情接口整体失败时明确不可用(monkeypatch, tmp_path):
    def boom(symbols, **kw):
        raise ConnectionError("网络不通")
    monkeypatch.setattr(relval_data, "fetch_quotes", boom)
    with pytest.raises(RelvalUnavailable):
        gather(fake_r({"002384": facts_years()}), AS_OF, tmp_path)


def test_确定性层把同业数据交给相对估值():
    """行业 46 家的财报在 `_load_industry` 里取过一次；不交出来就得重取（东财有频控）。"""
    import inspect

    from ir_agent import full
    ind = inspect.getsource(full._load_industry)
    assert "r.peer_statements = cache" in ind and "r.peer_set = peers" in ind
    assert ind.index("aggregate(peers") < ind.index("r.peer_statements = cache")
    assert "r.comps_codes = " in inspect.getsource(full._load_comps)


def test_剔除说明只上页面不进摘要():
    """说明里有代码与期间（数字），进摘要会被裸数字审计判掉。"""
    from ir_agent.relval import RelvalResult
    assert "sample_notes" in RelvalResult.__dataclass_fields__
    import inspect
    assert "sample_notes" not in inspect.getsource(RelvalResult.summary)
