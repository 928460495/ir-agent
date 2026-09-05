from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.operators import dupont, multiples

D = Decimal
AS_OF = date(2026, 4, 30)
LATER = date(2026, 5, 1)


def led(**kv) -> FactLedger:
    l = FactLedger()
    for k, v in kv.items():
        l.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                   period="2025FY", as_of=AS_OF, source_id="src",
                   method=Method.REPORTED))
    return l


# 中芯国际实测量级: 净利润 72.09 亿 / 归母 50.41 亿 —— 少数股东占 30.1%
SMIC = dict(revenue=67323, net_profit=7209, net_profit_attr_parent=5041,
            minority_interest_profit=2168, total_assets=367718,
            total_equity=246362, equity_attr_parent=172000,
            minority_equity=74362, market_cap=1037098)


class TestROEUsesParentBasis:
    def test_roe_is_parent_profit_over_parent_equity(self):
        out = dupont.compute(led(**SMIC), "2025FY", as_of=LATER)
        assert out["roe"].value == pytest.approx(
            D("5041") / D("172000"), rel=D("0.001"))

    def test_roe_gap_depends_on_minority_returns_not_minority_size(self):
        """中芯国际的少数股东损益占净利润 30.1%，但两种口径的 ROE 几乎相同
        （2.93% vs 2.93%）—— 因为少数股东权益的回报率与母公司相当，
        分子分母同比例变化。**占比大 ≠ 口径差异大**，这一点容易想当然。"""
        out = dupont.compute(led(**SMIC), "2025FY", as_of=LATER)
        total_basis = D("7209") / D("246362")
        assert abs(out["roe"].value - total_basis) / total_basis < D("0.01")

    def test_roe_gap_is_large_when_minority_returns_differ(self):
        """少数股东低回报时，全口径 ROE 被明显稀释 —— 这才是归母口径的价值。"""
        skewed = dict(SMIC, net_profit=5100, minority_interest_profit=59)
        out = dupont.compute(led(**skewed), "2025FY", as_of=LATER)
        total_basis = D("5100") / D("246362")
        assert abs(out["roe"].value - total_basis) / total_basis > D("0.4")

    def test_net_margin_uses_parent_profit_too(self):
        out = dupont.compute(led(**SMIC), "2025FY", as_of=LATER)
        assert out["net_margin"].value == pytest.approx(
            D("5041") / D("67323"), rel=D("0.001"))

    def test_three_factors_still_multiply_to_roe(self):
        out = dupont.compute(led(**SMIC), "2025FY", as_of=LATER)
        product = (out["net_margin"].value * out["asset_turnover"].value
                   * out["equity_multiplier"].value)
        assert out["roe"].value == pytest.approx(product, rel=D("0.0001"))

    def test_equity_multiplier_uses_parent_equity_for_consistency(self):
        out = dupont.compute(led(**SMIC), "2025FY", as_of=LATER)
        assert out["equity_multiplier"].value == pytest.approx(
            D("367718") / D("172000"), rel=D("0.001"))

    def test_falls_back_to_total_equity_when_parent_is_absent(self):
        data = {k: v for k, v in SMIC.items() if k != "equity_attr_parent"}
        out = dupont.compute(led(**data), "2025FY", as_of=LATER)
        assert out["roe"].basis == "total"

    def test_basis_is_recorded_so_readers_know_which_was_used(self):
        out = dupont.compute(led(**SMIC), "2025FY", as_of=LATER)
        assert out["roe"].basis == "parent"


class TestPBUsesParentEquity:
    def test_pb_divides_by_parent_equity(self):
        out = multiples.compute(led(**SMIC), "2025FY", as_of=LATER)
        assert out["pb"].value == pytest.approx(
            D("1037098") / D("172000"), rel=D("0.001"))

    def test_pb_falls_back_to_total_equity_when_parent_absent(self):
        data = {k: v for k, v in SMIC.items() if k != "equity_attr_parent"}
        out = multiples.compute(led(**data), "2025FY", as_of=LATER)
        assert out["pb"].value == pytest.approx(
            D("1037098") / D("246362"), rel=D("0.001"))

    def test_negative_parent_equity_produces_no_pb(self):
        data = dict(SMIC, equity_attr_parent=-5000)
        out = multiples.compute(led(**data), "2025FY", as_of=LATER)
        assert "pb" not in out


class TestPBIsWhereTheDistortionActuallyIs:
    """市值全部归属母公司股东，除以含少数股东权益的总权益是**系统性低估**。
    中芯国际实测: 归母口径 6.03x vs 全口径 4.21x，差 43%。"""

    def test_parent_and_total_basis_pb_differ_materially(self):
        parent = D("1037098") / D("172000")
        total = D("1037098") / D("246362")
        assert abs(parent - total) / total > D("0.4")

    def test_computed_pb_uses_the_parent_figure(self):
        out = multiples.compute(led(**SMIC), "2025FY", as_of=LATER)
        assert out["pb"].value > D("6")
        assert out["pb"].basis == "parent"
