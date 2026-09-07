"""指标适用性。这是「通用 agent」的实现点 —— 不是所有公司的报表都一样。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.metrics import (
    BY_KEY,
    REGISTRY,
    Applicability,
    Category,
    by_category,
    coverage_report,
    evaluate,
    summarize,
)
from ir_agent.sources.eastmoney import CompanyType, specs_for

AS_OF = date(2026, 9, 7)
P = "2024FY"


def _led(*keys, period=P):
    led = FactLedger()
    for k in keys:
        led.put(Fact(key=k, value=Decimal("100"), unit="元", period=period,
                     as_of=date(2025, 4, 1), source_id="t",
                     method=Method.REPORTED))
    return led


# ── 登记表本身 ───────────────────────────────────────────────

def test_五类每类都有指标():
    """FINC6600 要求五类：流动性、偿债、盈利、效率、市场前景。"""
    cats = {m.category for m in REGISTRY}
    assert cats == set(Category)


def test_通用公司五类每类至少两个指标():
    """课程的硬性要求。"""
    for cat, ms in by_category(CompanyType.GENERAL).items():
        assert len(ms) >= 2, f"{cat.value} 仅 {len(ms)} 个"


def test_不适用的指标必须给出理由():
    """「不适用」而不说为什么，读者会当成取数失败。"""
    for m in REGISTRY:
        if m.applies_to != frozenset(CompanyType):
            assert m.why_not, f"{m.key} 限定了适用范围却没写理由"


def test_key唯一():
    assert len(BY_KEY) == len(REGISTRY)


# ── 银行：不适用 ≠ 缺数据 ────────────────────────────────────

def test_银行的流动比率是不适用而非缺数据():
    """银行资产负债表按流动性排序列示，不分流动与非流动 ——
    这个比率在会计上不存在，不是取数失败。"""
    r = {x.spec.key: x for x in evaluate(_led("total_assets"), P, AS_OF,
                                         CompanyType.BANK)}
    assert r["current_ratio"].status is Applicability.NOT_MEANINGFUL
    assert "不区分流动与非流动" in r["current_ratio"].spec.why_not
    assert "会计上不存在" in r["current_ratio"].describe()


def test_银行的存货与毛利率同样不适用():
    r = {x.spec.key: x for x in evaluate(_led(), P, AS_OF, CompanyType.BANK)}
    for k in ("inventory_days", "gross_margin", "payable_days",
              "receivable_days", "interest_coverage", "roic", "ev_ebitda"):
        assert r[k].status is Applicability.NOT_MEANINGFUL, k


def test_通用公司缺同一个指标算缺数据():
    """同一个指标，对工商业是「缺数据」（该查），对银行是「不适用」（别查）。"""
    g = {x.spec.key: x for x in evaluate(_led(), P, AS_OF, CompanyType.GENERAL)}
    b = {x.spec.key: x for x in evaluate(_led(), P, AS_OF, CompanyType.BANK)}
    assert g["current_ratio"].status is Applicability.UNAVAILABLE
    assert b["current_ratio"].status is Applicability.NOT_MEANINGFUL


def test_银行有自己的专有指标():
    """「不适用」不等于「无话可说」。"""
    ms = {m.key for m in REGISTRY if m.applicable_to(CompanyType.BANK)}
    assert "cost_income_ratio" in ms and "net_interest_income_ratio" in ms
    assert "cost_income_ratio" not in {
        m.key for m in REGISTRY if m.applicable_to(CompanyType.GENERAL)}


def test_银行的流动性类无适用指标并说明():
    txt = coverage_report(CompanyType.BANK)
    assert "流动性" in txt and "无适用指标" in txt


# ── 缺数据的判定 ─────────────────────────────────────────────

def test_齐全则可算():
    led = _led("current_assets", "current_liabilities")
    r = {x.spec.key: x for x in evaluate(led, P, AS_OF, CompanyType.GENERAL)}
    assert r["current_ratio"].status is Applicability.OK


def test_缺哪个说哪个():
    led = _led("current_assets")
    r = {x.spec.key: x for x in evaluate(led, P, AS_OF, CompanyType.GENERAL)}
    assert r["current_ratio"].status is Applicability.UNAVAILABLE
    assert r["current_ratio"].missing == ("current_liabilities",)
    assert "current_liabilities" in r["current_ratio"].describe()


def test_未到披露日视同缺数据():
    led = FactLedger()
    for k in ("current_assets", "current_liabilities"):
        led.put(Fact(key=k, value=Decimal("1"), unit="元", period=P,
                     as_of=date(2026, 4, 30), source_id="t",
                     method=Method.REPORTED))
    early = {x.spec.key: x for x in evaluate(led, P, date(2026, 1, 1),
                                             CompanyType.GENERAL)}
    late = {x.spec.key: x for x in evaluate(led, P, date(2026, 5, 1),
                                            CompanyType.GENERAL)}
    assert early["current_ratio"].status is Applicability.UNAVAILABLE
    assert late["current_ratio"].status is Applicability.OK


def test_即期指标到即期期间取数():
    """市值挂在报价日上，不在会计期 —— 否则永远判成缺数据。"""
    led = _led("net_profit_ttm")
    led.put(Fact(key="market_cap", value=Decimal("1"), unit="元",
                 period="2026-09-04", as_of=date(2026, 9, 4),
                 source_id="t", method=Method.REPORTED))
    r = {x.spec.key: x for x in evaluate(led, P, AS_OF, CompanyType.GENERAL,
                                         spot="2026-09-04")}
    assert r["pe_ttm"].status is Applicability.OK


def test_摘要区分两类缺失():
    res = evaluate(_led(), P, AS_OF, CompanyType.BANK)
    txt = summarize(res)
    assert "不适用（非缺陷）" in txt
    assert "应有而缺（需查取数）" in txt


# ── 字段映射按模板分设 ───────────────────────────────────────

def test_四套模板各有自己的字段映射():
    g = specs_for(CompanyType.GENERAL)
    b = specs_for(CompanyType.BANK)
    assert "INVENTORY" in g["balance"].fields
    assert "INVENTORY" not in b["balance"].fields      # 银行没有存货
    assert "LOAN_ADVANCE" in b["balance"].fields       # 银行有贷款垫款
    assert "LOAN_ADVANCE" not in g["balance"].fields


def test_通用模板覆盖建模所需的关键字段():
    """折旧摊销与营运资本变动是 FCF 直接法的必需项，此前全部缺失。"""
    cf = specs_for(CompanyType.GENERAL)["cashflow"].fields.values()
    assert {"depreciation_fixed_asset", "amortization_intangible",
            "wc_inventory_decrease", "wc_receivable_decrease",
            "wc_payable_increase"} <= set(cf)
    inc = specs_for(CompanyType.GENERAL)["income"].fields.values()
    assert {"interest_expense_fe", "income_tax", "total_profit",
            "operating_profit", "rd_expense"} <= set(inc)
    bs = specs_for(CompanyType.GENERAL)["balance"].fields.values()
    assert {"current_assets", "current_liabilities", "inventory",
            "accounts_receivable", "retained_earnings"} <= set(bs)


def test_金融模板声明营业总收入的同义():
    """银行/保险没有 TOTAL_OPERATE_INCOME —— 对它们「营业收入」即全部。
    不做别名，所有以 revenue_total 为分母的指标都会误报缺数据。"""
    for ct in (CompanyType.BANK, CompanyType.INSURANCE, CompanyType.SECURITIES):
        assert specs_for(ct)["income"].aliases == {"revenue_total": "revenue"}
    assert specs_for(CompanyType.GENERAL)["income"].aliases == {}


def test_同义事实标记为computed且不覆盖真值():
    from ir_agent.sources.eastmoney import ReportSpec, _alias_facts

    spec = ReportSpec({}, {"revenue_total": "revenue"})
    src = Fact(key="revenue", value=Decimal("5"), unit="元", period=P,
               as_of=date(2025, 4, 1), source_id="t", method=Method.REPORTED)
    out = _alias_facts([src], spec)
    assert len(out) == 1
    assert out[0].key == "revenue_total" and out[0].value == Decimal("5")
    assert out[0].method is Method.COMPUTED
    assert out[0].derived_from == (f"revenue@{P}",)

    real = Fact(key="revenue_total", value=Decimal("9"), unit="元", period=P,
                as_of=date(2025, 4, 1), source_id="t", method=Method.REPORTED)
    assert _alias_facts([src, real], spec) == []      # 有真值就不补
