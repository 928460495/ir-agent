"""估值假设与人工审核门。

设计要求（来自设想第 3 步）: **假设条件需要人工审核，审核通过后才能构建
DCF 模型。** 因此审核状态不是元数据而是**前置条件** —— 未审核时
`value_company` 直接拒绝，而不是算完再标注「仅供参考」。

两条配套约束:
  · 每项假设必须带**依据**。没有依据的数字与凭空捏造无异，
    审核者面对一个孤零零的 12% 无从判断。
  · 假设一旦被修改，审核状态自动失效 —— 否则「审核过的假设」会在
    悄悄改动后继续挂着通过标记。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.valuation.assumptions import (
    Assumptions,
    NotReviewed,
    UnsupportedAssumption,
)

D = Decimal


def make(**kw) -> Assumptions:
    base = dict(
        wacc=D("0.09"),
        terminal_growth=D("0.025"),
        growth_rates=[D("0.08"), D("0.06"), D("0.05"), D("0.04"), D("0.03")],
        basis={"wacc": "无风险利率 2.5% + β1.1 × 股权风险溢价 6%",
               "terminal_growth": "不超过长期名义 GDP 增速",
               "growth_rates": "行业分析给出的产能与提价路径"},
    )
    base.update(kw)
    return Assumptions(**base)


class TestBasisIsRequired:
    def test_every_assumption_needs_a_stated_basis(self):
        with pytest.raises(UnsupportedAssumption, match="wacc"):
            make(basis={"terminal_growth": "x", "growth_rates": "y"})

    def test_blank_basis_counts_as_missing(self):
        with pytest.raises(UnsupportedAssumption):
            make(basis={"wacc": "  ", "terminal_growth": "x", "growth_rates": "y"})

    def test_complete_basis_constructs(self):
        assert make().wacc == D("0.09")


class TestSanityBounds:
    def test_terminal_growth_above_wacc_is_rejected(self):
        with pytest.raises(ValueError, match="WACC"):
            make(wacc=D("0.02"), terminal_growth=D("0.05"))

    def test_negative_wacc_is_rejected(self):
        with pytest.raises(ValueError, match="WACC"):
            make(wacc=D("-0.01"))

    def test_terminal_growth_above_nominal_gdp_is_rejected(self):
        """永续增长率高于长期名义 GDP 增速意味着公司最终吞掉整个经济。"""
        with pytest.raises(ValueError, match="永续"):
            make(terminal_growth=D("0.09"), wacc=D("0.12"))

    def test_empty_growth_path_is_rejected(self):
        with pytest.raises(ValueError, match="预测期"):
            make(growth_rates=[])


class TestReviewGate:
    def test_new_assumptions_are_not_approved(self):
        assert make().approved is False

    def test_approval_records_who_and_when(self):
        a = make().approve("谢海量", date(2026, 6, 1))
        assert a.approved and a.reviewed_by == "谢海量"

    def test_approval_returns_a_new_object(self):
        """审核不该就地修改 —— 原对象仍是未审核状态，便于留痕。"""
        a = make()
        a.approve("谢海量", date(2026, 6, 1))
        assert a.approved is False

    def test_editing_an_approved_assumption_revokes_approval(self):
        """审核过的假设被改动后必须重新审核，否则通过标记形同虚设。"""
        a = make().approve("谢海量", date(2026, 6, 1))
        b = a.revise(wacc=D("0.07"), basis_note="下调 β")
        assert b.approved is False

    def test_revision_records_what_changed(self):
        a = make().approve("谢海量", date(2026, 6, 1))
        b = a.revise(wacc=D("0.07"), basis_note="下调 β")
        assert "wacc" in b.basis and "下调 β" in b.basis["wacc"]


class TestValuationRefusesWithoutApproval:
    def _inputs(self):
        return dict(fcf_base=D("100"), net_debt=D("0"), shares=D("100"))

    def test_unapproved_assumptions_block_valuation(self):
        from ir_agent.valuation.model import value_company
        with pytest.raises(NotReviewed, match="审核"):
            value_company(make(), **self._inputs())

    def test_approved_assumptions_produce_a_result(self):
        from ir_agent.valuation.model import value_company
        r = value_company(make().approve("谢海量", date(2026, 6, 1)),
                          **self._inputs())
        assert r.value_per_share > 0

    def test_negative_base_fcf_is_refused_even_when_approved(self):
        """审核通过的假设不能让一个负 FCF 的 DCF 变得有意义。"""
        from ir_agent.valuation.model import value_company
        with pytest.raises(ValueError, match="自由现金流"):
            value_company(make().approve("谢海量", date(2026, 6, 1)),
                          fcf_base=D("-100"), net_debt=D("0"), shares=D("100"))

    def test_result_carries_the_terminal_share_diagnostic(self):
        from ir_agent.valuation.model import value_company
        r = value_company(make().approve("谢海量", date(2026, 6, 1)),
                          **self._inputs())
        assert D("0") < r.terminal_share < D("1")

    def test_result_carries_the_reviewer_for_the_audit_trail(self):
        from ir_agent.valuation.model import value_company
        r = value_company(make().approve("谢海量", date(2026, 6, 1)),
                          **self._inputs())
        assert r.reviewed_by == "谢海量"
