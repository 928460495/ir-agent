"""盈余质量、Altman Z、共同比。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.operators import common_size, quality
from ir_agent.sources.eastmoney import CompanyType

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2024FY"


def _led(pairs, period=P, led=None, as_of=date(2025, 4, 1)):
    # 不能写 `led or FactLedger()` —— FactLedger 定义了 __len__，
    # 空账本是 falsy，每次都会被替换成新的。
    led = FactLedger() if led is None else led
    for k, v in pairs.items():
        led.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                     period=period, as_of=as_of, source_id="t",
                     method=Method.REPORTED))
    return led


HEALTHY = {"net_profit": 100, "cf_operating": 130, "total_assets": 1000,
           "capex": 50, "revenue_total": 800}


# ── 盈余质量 ─────────────────────────────────────────────────

def test_应计率为负表示现金流强于利润():
    r = quality.compute(_led(HEALTHY), P, AS_OF)
    assert r.facts["accrual_ratio"].value == D("-0.03")     # (100-130)/1000
    assert not r.flags


def test_应计率偏高触发提示():
    """净利润远高于经营现金流 —— 利润的现金含量不足。"""
    r = quality.compute(_led({**HEALTHY, "cf_operating": 10}), P, AS_OF)
    assert r.facts["accrual_ratio"].value == D("0.09")
    r2 = quality.compute(_led({**HEALTHY, "cf_operating": -50}), P, AS_OF)
    assert any("应计率" in f and "现金含量不足" in f for f in r2.flags)


def test_利润现金含量低于阈值触发提示():
    r = quality.compute(_led({**HEALTHY, "cf_operating": 50}), P, AS_OF)
    assert r.facts["cash_conversion"].value == D("0.5")
    assert any("有利润但现金没进来" in f for f in r.flags)


def test_净利润非正时不算现金含量():
    """分子分母都为负会算出一个正数，那个数没有意义。"""
    r = quality.compute(_led({**HEALTHY, "net_profit": -10}), P, AS_OF)
    assert "cash_conversion" not in r.facts
    assert any("净利润非正" in s for s in r.skipped)


def test_资本开支覆盖不足触发提示():
    r = quality.compute(_led({**HEALTHY, "capex": 200}), P, AS_OF)
    assert any("扩张依赖外部融资" in f for f in r.flags)


def test_经营现金流波动需要至少三期():
    """课程三问的第三问看的是波动而非水平，单期无从谈起。"""
    led = FactLedger()
    for i, per in enumerate(("2022FY", "2023FY", "2024FY")):
        _led({"cf_operating": 100 + i * 50, "revenue_total": 1000}, per, led)
    r = quality.compute(led, P, AS_OF, history=["2022FY", "2023FY", "2024FY"])
    assert "ocf_volatility" in r.facts
    assert r.facts["ocf_volatility"].value > 0

    r2 = quality.compute(led, P, AS_OF, history=["2023FY", "2024FY"])
    assert "ocf_volatility" not in r2.facts


def test_现金流稳定则波动接近零():
    led = FactLedger()
    for per in ("2022FY", "2023FY", "2024FY"):
        _led({"cf_operating": 200, "revenue_total": 1000}, per, led)
    r = quality.compute(led, P, AS_OF,
                        history=["2022FY", "2023FY", "2024FY"])
    assert r.facts["ocf_volatility"].value == 0


# ── Altman Z ─────────────────────────────────────────────────

Z_INPUTS = {"current_assets": 400, "current_liabilities": 100,
            "total_assets": 1000, "retained_earnings": 300,
            "operating_profit": 150, "total_liabilities": 300,
            "revenue_total": 800, "interest_expense_fe": 10}


def _with_cap(cap=900):
    led = _led(Z_INPUTS)
    led.put(Fact(key="market_cap", value=D(cap), unit="元", period="SPOT",
                 as_of=date(2026, 9, 4), source_id="t", method=Method.REPORTED))
    return led


def test_标准五因子():
    z, why = quality.altman_z(_with_cap(), P, AS_OF, spot="SPOT")
    # A=.3 B=.3 C=.16 D=3 E=.8
    expect = (D("1.2") * D("0.3") + D("1.4") * D("0.3") + D("3.3") * D("0.16")
              + D("0.6") * D(3) + D("1.0") * D("0.8"))
    assert why == "" and z.value == expect


def test_EBIT加回利息费用():
    """营业利润是扣息后的，EBIT 要加回 —— 否则高杠杆公司的 C 项被低估。"""
    a, _ = quality.altman_z(_with_cap(), P, AS_OF, spot="SPOT")
    led = _led({**Z_INPUTS, "interest_expense_fe": 0})
    led.put(Fact(key="market_cap", value=D(900), unit="元", period="SPOT",
                 as_of=date(2026, 9, 4), source_id="t", method=Method.REPORTED))
    b, _ = quality.altman_z(led, P, AS_OF, spot="SPOT")
    assert a.value > b.value


def test_金融企业不适用且给出会计理由():
    for ct in (CompanyType.BANK, CompanyType.INSURANCE, CompanyType.SECURITIES):
        z, why = quality.altman_z(_with_cap(), P, AS_OF, ct, spot="SPOT")
        assert z is None
        assert "不区分流动性" in why and "不适用" in why


def test_缺市值时说明缺什么():
    z, why = quality.altman_z(_led(Z_INPUTS), P, AS_OF)
    assert z is None and "market_cap" in why


def test_困境区与灰区分档():
    weak = {**Z_INPUTS, "operating_profit": 1, "retained_earnings": 0,
            "current_assets": 110, "revenue_total": 100}
    led = _led(weak)
    led.put(Fact(key="market_cap", value=D(10), unit="元", period="SPOT",
                 as_of=date(2026, 9, 4), source_id="t", method=Method.REPORTED))
    r = quality.compute(led, P, AS_OF, spot="SPOT")
    assert any("困境区" in f for f in r.flags)


def test_中国版不实现并在文档中说明():
    """讲义只给系数不给变量定义，凭系数猜变量等于编一个模型。"""
    assert not hasattr(quality, "altman_z_china")
    assert "只有系数没有 X1–X4 的定义" in quality.altman_z.__doc__


# ── 共同比 ───────────────────────────────────────────────────

BS = {"total_assets": 1000, "current_assets": 400, "inventory": 200,
      "accounts_receivable": 100, "fixed_asset": 500,
      "total_liabilities": 300, "total_equity": 700}


def test_资产负债表按总资产换算():
    cs = common_size.compute(_led(BS), P, AS_OF, "balance")
    assert cs.denominator == "total_assets"
    assert cs.get("inventory") == D("0.2")
    assert cs.get("total_assets") is None       # 分母本身不列


def test_利润表按营业总收入换算():
    inc = {"revenue_total": 800, "cost_of_revenue": 400,
           "operating_profit": 200, "rd_expense": 40}
    cs = common_size.compute(_led(inc), P, AS_OF, "income")
    assert cs.denominator == "revenue_total"
    assert cs.get("rd_expense") == D("0.05")


def test_分母缺失或为零返回空():
    assert common_size.compute(_led({"inventory": 1}), P, AS_OF, "balance") is None
    assert common_size.compute(_led({**BS, "total_assets": 0}), P, AS_OF,
                               "balance") is None


def test_科目清单随模板变化():
    """银行没有存货，共同比里也不该出现这一行。"""
    bank = _led({"total_assets": 1000, "loan_advance": 600,
                 "accept_deposit": 800})
    cs = common_size.compute(bank, P, AS_OF, "balance", CompanyType.BANK)
    assert cs.get("loan_advance") == D("0.6")
    assert cs.get("inventory") is None


def test_跨期变化才是重点():
    """单期「应收占 8%」说明不了什么，「三年从 4% 升到 8%」才是线索。"""
    led = FactLedger()
    _led({**BS, "inventory": 100}, "2022FY", led)
    _led({**BS, "inventory": 150}, "2023FY", led)
    _led({**BS, "inventory": 300}, "2024FY", led)
    series, sh = common_size.compute_series(
        led, ["2022FY", "2023FY", "2024FY"], AS_OF, "balance")
    assert len(series) == 3
    top = sh[0]
    assert top.key == "inventory"
    assert top.delta == D("0.2")                # 10% → 30%
    assert "+20.00 个百分点" in top.describe()


def test_小幅变化不进清单():
    led = FactLedger()
    _led({**BS, "inventory": 200}, "2023FY", led)
    _led({**BS, "inventory": 205}, "2024FY", led)
    _, sh = common_size.compute_series(led, ["2023FY", "2024FY"], AS_OF,
                                       "balance")
    assert not any(s.key == "inventory" for s in sh)


def test_单期不出变化():
    led = _led(BS, "2024FY")
    _, sh = common_size.compute_series(led, ["2024FY"], AS_OF, "balance")
    assert sh == []
