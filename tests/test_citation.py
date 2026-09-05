from datetime import date
from decimal import Decimal

import pytest

from ir_agent.citation import (
    UnresolvedReferenceError,
    audit_bare_numbers,
    render,
)
from ir_agent.ledger import Fact, FactLedger, Method


@pytest.fixture
def ledger():
    led = FactLedger()
    led.put(Fact(
        key="revenue", value=Decimal("39374532100"), unit="元", currency="CNY",
        period="2025Q3", as_of=date(2025, 10, 25),
        source_id="cninfo_a1", method=Method.REPORTED,
    ))
    led.put(Fact(
        key="gross_margin", value=Decimal("0.4213"), unit="ratio",
        period="2025Q3", as_of=date(2025, 10, 25),
        source_id="calc_1", method=Method.COMPUTED,
    ))
    return led


class TestRender:
    def test_placeholder_is_replaced_by_the_ledger_value(self, ledger):
        out, _ = render("营业收入 [[cninfo_a1#revenue@2025Q3]]。", ledger, as_of=date(2025, 12, 1))
        assert "393.75 亿元" in out
        assert "[[" not in out

    def test_ratio_unit_renders_as_percentage(self, ledger):
        out, _ = render("毛利率 [[calc_1#gross_margin@2025Q3]]。", ledger, as_of=date(2025, 12, 1))
        assert "42.13%" in out

    def test_unknown_reference_raises_rather_than_passing_through(self, ledger):
        with pytest.raises(UnresolvedReferenceError, match="net_profit"):
            render("[[cninfo_a1#net_profit@2025Q3]]", ledger, as_of=date(2025, 12, 1))

    def test_source_id_mismatch_is_rejected(self, ledger):
        """Guards against an LLM inventing a plausible-looking source id."""
        with pytest.raises(UnresolvedReferenceError, match="source_id"):
            render("[[wrong_src#revenue@2025Q3]]", ledger, as_of=date(2025, 12, 1))

    def test_lookahead_propagates_during_render(self, ledger):
        from ir_agent.ledger import LookAheadError
        with pytest.raises(LookAheadError):
            render("[[cninfo_a1#revenue@2025Q3]]", ledger, as_of=date(2025, 9, 1))

    def test_every_resolved_reference_produces_a_footnote(self, ledger):
        _, notes = render(
            "收入 [[cninfo_a1#revenue@2025Q3]]，毛利率 [[calc_1#gross_margin@2025Q3]]。",
            ledger, as_of=date(2025, 12, 1),
        )
        assert len(notes) == 2
        assert notes[0].source_id == "cninfo_a1"
        assert notes[0].as_of == date(2025, 10, 25)

    def test_repeated_reference_yields_one_footnote(self, ledger):
        _, notes = render(
            "[[cninfo_a1#revenue@2025Q3]] 与 [[cninfo_a1#revenue@2025Q3]]",
            ledger, as_of=date(2025, 12, 1),
        )
        assert len(notes) == 1


class TestBareNumberAudit:
    def test_bare_financial_number_in_draft_is_flagged(self):
        """LLM 直接写数字 = 幻觉入口，必须在渲染前拦下。"""
        hits = audit_bare_numbers("营业收入 393.75 亿元，同比增长 12%。")
        assert hits

    def test_placeholders_are_not_flagged(self):
        assert audit_bare_numbers("营业收入 [[cninfo_a1#revenue@2025Q3]]。") == []

    def test_ordinary_prose_without_figures_is_clean(self):
        assert audit_bare_numbers("公司在高端市场保持领先地位。") == []

    def test_year_and_quarter_labels_are_not_flagged(self):
        """2025年 / 2025Q3 是标签不是财务数值，误报会让审计噪音过大。"""
        assert audit_bare_numbers("2025年前三季度，公司 2025Q3 表现稳健。") == []


class TestPeriodLabelsAreNotBareNumbers:
    """期间标签不是财务数值。误报会让审计噪音大到无法使用。"""

    @pytest.mark.parametrize("label", ["2025FY", "2025H1", "2025Q1-Q3", "2025Q1"])
    def test_period_label_is_not_flagged(self, label):
        assert audit_bare_numbers(f"## 经营概览（{label}）") == []


class TestDerivedFactReferences:
    def test_a_computed_fact_placeholder_actually_resolves(self):
        """派生事实的 source_id 必须落在占位符语法允许的字符集内，
        否则正文里的 [[...]] 会原样留在成稿里。"""
        from ir_agent.operators import margins

        led = FactLedger()
        for key, val in (("revenue", "1000"), ("cost_of_revenue", "600")):
            led.put(Fact(key=key, value=Decimal(val), unit="元", currency="CNY",
                         period="2025FY", as_of=date(2026, 3, 28),
                         source_id="sina_x", method=Method.REPORTED))
        gm = margins.compute(led, "2025FY", as_of=date(2026, 4, 1))["gross_margin"]

        out, notes = render(f"毛利率 {gm.ref}。", led, as_of=date(2026, 4, 1))
        assert "[[" not in out
        assert "40.00%" in out
        assert len(notes) == 1
