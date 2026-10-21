"""五类比率的计算。重点在「算不出来」的三种原因必须分开。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.metrics import Category
from ir_agent.operators import aggregates, ratios
from ir_agent.sources.eastmoney import CompanyType

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2024FY"


def _led(pairs, period=P, as_of=date(2025, 4, 1)):
    led = FactLedger()
    for k, v in pairs.items():
        led.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                     period=period, as_of=as_of, source_id="t",
                     method=Method.REPORTED))
    return led


BASE = {"current_assets": 400, "current_liabilities": 100, "inventory": 100,
        "total_assets": 1000, "total_liabilities": 300, "total_equity": 700,
        "equity_attr_parent": 700, "revenue": 800, "revenue_total": 800,
        "cost_of_revenue": 400, "operating_profit": 200, "net_profit": 150,
        "net_profit_attr_parent": 150, "total_profit": 190, "income_tax": 40,
        "interest_expense_fe": 20, "fixed_asset": 500,
        "accounts_receivable": 80, "accounts_payable": 60,
        "cash_and_equivalents": 200}


# ── 计算正确性 ───────────────────────────────────────────────

def test_五类都算得出来():
    rs = ratios.compute(_led(BASE), P, AS_OF, CompanyType.GENERAL)
    got = {c for c, fs in rs.by_category().items() if fs}
    assert got == set(Category)


def test_流动性():
    rs = ratios.compute(_led(BASE), P, AS_OF, CompanyType.GENERAL)
    assert rs.facts["current_ratio"].value == D(4)          # 400/100
    assert rs.facts["quick_ratio"].value == D(3)            # (400-100)/100
    assert rs.facts["nwc_to_assets"].value == D("0.3")      # (400-100)/1000


def test_效率的天数口径():
    rs = ratios.compute(_led(BASE), P, AS_OF, CompanyType.GENERAL)
    # 应收 80 / 收入 800 × 365 = 36.5 天
    assert rs.facts["receivable_days"].value == D("36.5")
    # 存货 100 / 成本 400 × 365 = 91.25 天
    assert rs.facts["inventory_days"].value == D("91.25")
    ccc = rs.facts["cash_conversion_cycle"].value
    assert ccc == D("36.5") + D("91.25") - D("54.75")


def test_利息保障倍数用营业利润():
    rs = ratios.compute(_led(BASE), P, AS_OF, CompanyType.GENERAL)
    assert rs.facts["interest_coverage"].value == D(10)     # 200/20


def test_派生事实标记computed且可溯源():
    rs = ratios.compute(_led(BASE), P, AS_OF, CompanyType.GENERAL)
    f = rs.facts["current_ratio"]
    assert f.method is Method.COMPUTED
    assert set(f.derived_from) == {f"current_assets@{P}",
                                   f"current_liabilities@{P}"}


# ── 三种「算不出来」必须分开 ─────────────────────────────────

def test_分母为零记为无定义而非缺数据():
    """分母为零是标的本身的状态，不是取数问题 —— 不该让人去查接口。"""
    led = _led({**BASE, "current_liabilities": 0})
    rs = ratios.compute(led, P, AS_OF, CompanyType.GENERAL)
    assert "current_ratio" not in rs.facts
    u = next(x for x in rs.undefined if x.spec.key == "current_ratio")
    assert "为 0" in u.reason and "无定义" in u.reason


def test_亏损公司不给PE():
    """亏损时 PE 算得出来但不该被解读。"""
    led = _led({**BASE, "net_profit_ttm": -50})
    led.put(Fact(key="market_cap", value=D(1000), unit="元", period="2026-09-04",
                 as_of=date(2026, 9, 4), source_id="t", method=Method.REPORTED))
    rs = ratios.compute(led, P, AS_OF, CompanyType.GENERAL, spot="2026-09-04")
    assert "pe_ttm" not in rs.facts
    assert any(x.spec.key == "pe_ttm" for x in rs.undefined)


def test_净资产为负不给PB():
    led = _led({**BASE, "equity_attr_parent": -100})
    led.put(Fact(key="market_cap", value=D(1000), unit="元", period="2026-09-04",
                 as_of=date(2026, 9, 4), source_id="t", method=Method.REPORTED))
    rs = ratios.compute(led, P, AS_OF, CompanyType.GENERAL, spot="2026-09-04")
    assert "pb" not in rs.facts


def test_银行不算流动比率也不报缺数据():
    bank = {"total_assets": 1000, "total_liabilities": 900, "total_equity": 100,
            "equity_attr_parent": 100, "revenue": 50, "revenue_total": 50,
            "operating_profit": 20, "net_profit": 15,
            "net_profit_attr_parent": 15, "business_manage_expense": 15,
            "interest_net_income": 35}
    rs = ratios.compute(_led(bank), P, AS_OF, CompanyType.BANK)
    assert "current_ratio" not in rs.facts
    assert "current_ratio" not in {s.key for s, _ in rs.unavailable}
    assert "流动比率" in {m.label for m in rs.not_meaningful}
    # 银行有自己的指标
    assert rs.facts["cost_income_ratio"].value == D("0.3")
    assert rs.facts["net_interest_income_ratio"].value == D("0.7")


def test_摘要三种原因分列():
    led = _led({k: v for k, v in BASE.items() if k != "inventory"})
    rs = ratios.compute(led, P, AS_OF, CompanyType.GENERAL)
    txt = rs.summary()
    assert "不适用" in txt and "缺数据" in txt


def test_利息费用为负时不给保障倍数():
    """利息收入大于支出的公司，这个倍数没有意义。"""
    rs = ratios.compute(_led({**BASE, "interest_expense_fe": -5}),
                        P, AS_OF, CompanyType.GENERAL)
    assert "interest_coverage" not in rs.facts


# ── 科目合计 ─────────────────────────────────────────────────

def test_折旧摊销合计分量():
    led = _led({"depreciation_fixed_asset": 100, "amortization_intangible": 30,
                "amortization_useright": 20})
    f = aggregates.depreciation_amortization(led, P, AS_OF)
    assert f.value == D(150)
    assert len(f.derived_from) == 3


def test_分量缺失视为零但不能全缺():
    """公司确实可能没有应付债券；但一个分量都没有时，合计 0 是假的 0。"""
    led = _led({"short_loan": 100, "bond_payable": 50})
    assert aggregates.interest_bearing_debt(led, P, AS_OF).value == D(150)

    with pytest.raises(aggregates.NoComponents, match="一个都没取到"):
        aggregates.interest_bearing_debt(_led({"total_assets": 1}), P, AS_OF)


def test_合计缺失时compute跳过而非中断():
    """无有息负债的公司（如茅台）取不到任何债务分量，这不是故障。"""
    out = aggregates.compute(_led({"depreciation_fixed_asset": 10}), P, AS_OF)
    assert "depreciation_amortization" in out
    assert "interest_bearing_debt" not in out


# ── 趋势 ─────────────────────────────────────────────────────

def _multi(values_by_period):
    led = FactLedger()
    for per, vals in values_by_period.items():
        for k, v in vals.items():
            led.put(Fact(key=k, value=D(str(v)), unit="元", period=per,
                         as_of=date(int(per[:4]) + 1, 4, 1), source_id="t",
                         method=Method.REPORTED))
    return led


def test_年度期间只取时点内可见的():
    led = _multi({f"{y}FY": BASE for y in (2021, 2022, 2023, 2024)})
    assert ratios.annual_periods(led, AS_OF) == [
        "2021FY", "2022FY", "2023FY", "2024FY"]
    # 2025-01-01 时点上，2024 年报尚未披露
    assert ratios.annual_periods(led, date(2025, 1, 1)) == [
        "2021FY", "2022FY", "2023FY"]


def test_次新股不足四年返回实际有的():
    """凑不出四年是标的的事实，不是缺陷。"""
    led = _multi({f"{y}FY": BASE for y in (2023, 2024)})
    assert len(ratios.annual_periods(led, AS_OF)) == 2


def test_一致预期的预测期不算已完成年度():
    """实测复现：as_of=2026-09-12 时，一致预期对 2026/2027/2028FY 的
    EPS 已经合法可见（披露于 as_of 之前），但这三个会计年度本身还没
    结束。`annual_periods` 此前只按「披露时点 ≤ as_of」过滤（挡的是
    未来函数），没有再问一句「这个年度是不是已经走完」——于是把
    consensus.* 的预测期当成了「最近一个已完成年度」，
    `_latest_annual_period` 会取到 2028FY 而不是 2025FY。

    这里用真实账本形状复现：2020–2025FY 是实绩，2026/2027/2028FY
    只有 consensus.* 系列的键（一致预期），披露于 as_of 之前。"""
    led = FactLedger()
    for y in range(2020, 2026):
        for k, v in BASE.items():
            led.put(Fact(key=k, value=D(str(v)), unit="元",
                        period=f"{y}FY", as_of=date(y + 1, 4, 1),
                        source_id="t", method=Method.REPORTED))
    for y in (2026, 2027, 2028):
        led.put(Fact(key="consensus.eps", value=D("1.0"), unit="currency",
                    period=f"{y}FY", as_of=date(2026, 1, 1),
                    source_id="eastmoney_report", method=Method.REPORTED))

    as_of = date(2026, 9, 12)
    periods = ratios.annual_periods(led, as_of, years=6)
    assert periods == [f"{y}FY" for y in range(2020, 2026)], \
        f"预测期混进了已完成年度列表：{periods}"


def test_趋势方向是结论():
    led = _multi({
        "2021FY": {**BASE, "current_assets": 400},
        "2022FY": {**BASE, "current_assets": 300},
        "2023FY": {**BASE, "current_assets": 200},
    })
    sets, trends = ratios.compute_trend(led, AS_OF, CompanyType.GENERAL)
    assert len(sets) == 3
    t = next(x for x in trends if x.key == "current_ratio")
    assert t.direction == "持续下降"
    assert "持续下降" in t.describe()


def test_单期不出趋势():
    led = _multi({"2024FY": BASE})
    assert ratios.trend(led, "current_ratio", ["2024FY"], AS_OF) is None


# ── EV 口径：加回少数股东权益 ─────────────────────────────────
# EBITDA 是合并口径，含少数股东那一份；EV 不加回少数股东权益，
# 分子分母就不是同一群出资人。生益科技 600183: 所有者权益合计 189.10 亿、
# 归母 167.24 亿，少数股东权益约 21.86 亿（子公司生益电子有外部股东）。

SPOT = "2026-09-04"
# EBITDA = 营业利润 200 + 折旧摊销 100 + 利息 20 = 320
EV_BASE = {**BASE, "depreciation_fixed_asset": 100, "short_loan": 300,
           "total_equity": 18910, "equity_attr_parent": 16724}


def _ev_led(**over):
    led = _led({k: v for k, v in {**EV_BASE, **over}.items() if v is not None})
    led.put(Fact(key="market_cap", value=D(30000), unit="元", period=SPOT,
                 as_of=date(2026, 9, 4), source_id="t", method=Method.REPORTED))
    return led


def _ev_ebitda(**over):
    return ratios.compute(_ev_led(**over), P, AS_OF, CompanyType.GENERAL,
                          spot=SPOT)


def test_EV加回少数股东权益():
    """少数股东权益 = 所有者权益合计 − 归母权益 = 18910 − 16724 = 2186。"""
    rs = _ev_ebitda()
    ev = D(30000) + D(300) - D(200) + D(2186)
    assert rs.facts["ev_ebitda"].value == ev / D(320)


def test_无少数股东时EV不变():
    rs = _ev_ebitda(total_equity=16724)
    assert rs.facts["ev_ebitda"].value == (D(30000) + D(300) - D(200)) / D(320)


def test_少数股东权益为负照常加回():
    """子公司累计亏损时少数股东权益可以为负 —— 照常加回，不截成 0。"""
    rs = _ev_ebitda(total_equity=16000)
    ev = D(30000) + D(300) - D(200) - D(724)
    assert rs.facts["ev_ebitda"].value == ev / D(320)


@pytest.mark.parametrize("gone", ["total_equity", "equity_attr_parent"])
def test_取不到权益两端之一判缺数据而非按零算(gone):
    """资产负债表一定有这两项，取不到是取数故障 —— 按 0 算会悄悄低估 EV。"""
    rs = _ev_ebitda(**{gone: None})
    assert "ev_ebitda" not in rs.facts
    missing = dict((s.key, m) for s, m in rs.unavailable)
    assert gone in missing["ev_ebitda"]


def test_EV的派生事实可溯源到两端权益():
    f = _ev_ebitda().facts["ev_ebitda"]
    assert {f"total_equity@{P}", f"equity_attr_parent@{P}"} <= set(f.derived_from)
