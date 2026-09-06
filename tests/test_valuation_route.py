"""估值方法路由 —— DCF 不是默认路径。

30 家实测样本里相当一部分不适用 DCF: 银行保险没有传统自由现金流、
强周期的永续增长假设失效、亏损公司 FCF 为负会让模型退化成对终值的猜测。

**强周期用盈利波动性判定，不查行业表。** 免费源拿不到可靠的行业分类，
而我们本来就有多期盈利数据 —— 变异系数与盈亏翻转次数比行业标签更有依据，
也更诚实: 同一行业里穿越周期的公司和不穿越的公司，本就该分开对待。

路由结论必须带理由并写进研报 ——「本标的为什么用这个估值方法」本身就是
一句该出现在报告里的话，而不是代码里的隐含默认。
"""
from decimal import Decimal

import pytest

from ir_agent.sources.eastmoney import CompanyType
from ir_agent.valuation.route import (
    ValuationMethod,
    cyclicality,
    route,
)

D = Decimal


def series(*vals):
    return [D(str(v)) for v in vals]


class TestCyclicality:
    def test_steady_earnings_score_low(self):
        assert cyclicality(series(100, 105, 110, 108, 115)) < D("0.3")

    def test_swinging_earnings_score_high(self):
        assert cyclicality(series(100, 20, 150, 10, 200)) > D("0.6")

    def test_a_loss_year_pushes_the_score_up(self):
        steady = cyclicality(series(100, 105, 110, 108, 115))
        with_loss = cyclicality(series(100, 105, -30, 108, 115))
        assert with_loss > steady

    def test_multiple_sign_flips_score_higher_than_one(self):
        one = cyclicality(series(100, 100, -50, 100, 100))
        many = cyclicality(series(100, -50, 100, -50, 100))
        assert many > one

    def test_too_few_periods_returns_none(self):
        """两三年数据判不了周期性 —— 返回 None 好过给一个假的分数。"""
        assert cyclicality(series(100, 110)) is None

    def test_all_zero_series_returns_none(self):
        assert cyclicality(series(0, 0, 0, 0)) is None


class TestRouting:
    STEADY = series(100, 105, 110, 108, 115)
    CYCLIC = series(100, 20, 150, 10, 200)

    def test_stable_industrial_gets_dcf(self):
        r = route(CompanyType.GENERAL, net_profit_history=self.STEADY,
                  current_net_profit=D("115"))
        assert r.method is ValuationMethod.DCF

    def test_bank_never_gets_dcf(self):
        """银行的资本本身是经营资源，没有传统意义的自由现金流。"""
        r = route(CompanyType.BANK, net_profit_history=self.STEADY,
                  current_net_profit=D("115"))
        assert r.method is not ValuationMethod.DCF

    @pytest.mark.parametrize("ct", [CompanyType.BANK, CompanyType.INSURANCE,
                                    CompanyType.SECURITIES])
    def test_financials_route_to_pb_roe(self, ct):
        assert route(ct, net_profit_history=self.STEADY,
                     current_net_profit=D("115")).method is ValuationMethod.PB_ROE

    def test_current_loss_routes_away_from_dcf(self):
        r = route(CompanyType.GENERAL, net_profit_history=self.STEADY,
                  current_net_profit=D("-30"))
        assert r.method is ValuationMethod.PB_ROE

    def test_high_cyclicality_routes_away_from_dcf(self):
        r = route(CompanyType.GENERAL, net_profit_history=self.CYCLIC,
                  current_net_profit=D("200"))
        assert r.method is ValuationMethod.PB_ROE

    def test_insufficient_history_refuses_to_certify_dcf(self):
        """判不了周期性就不该断言「适用 DCF」—— 缺证据不等于证据支持。"""
        r = route(CompanyType.GENERAL, net_profit_history=series(100, 110),
                  current_net_profit=D("110"))
        assert r.method is ValuationMethod.UNDETERMINED


class TestReasonIsReportable:
    def test_every_route_carries_a_reason(self):
        for ct in CompanyType:
            r = route(ct, net_profit_history=series(100, 105, 110, 108, 115),
                      current_net_profit=D("115"))
            assert r.reason and len(r.reason) > 8

    def test_reason_names_the_disqualifier(self):
        r = route(CompanyType.GENERAL,
                  net_profit_history=series(100, 20, 150, 10, 200),
                  current_net_profit=D("200"))
        assert "周期" in r.reason

    def test_loss_reason_says_so(self):
        r = route(CompanyType.GENERAL,
                  net_profit_history=series(100, 105, 110, 108, 115),
                  current_net_profit=D("-30"))
        assert "亏损" in r.reason

    def test_bank_reason_explains_the_structural_cause(self):
        r = route(CompanyType.BANK, net_profit_history=series(100, 105, 110, 108, 115),
                  current_net_profit=D("115"))
        assert "现金流" in r.reason or "资本" in r.reason

    def test_cyclicality_score_is_exposed_for_the_report(self):
        r = route(CompanyType.GENERAL,
                  net_profit_history=series(100, 20, 150, 10, 200),
                  current_net_profit=D("200"))
        assert r.cyclicality is not None


class TestAnnualHistoryDepth:
    """周期性判定需要足够长的年度序列。东财按报告期倒序返回，
    默认 page_size=12 里大多是季报，只剩 3 个年度 —— 实测十家里八家
    因此被判为 undetermined，路由等于没生效。"""

    def test_default_page_size_is_too_shallow_for_cyclicality(self):
        from ir_agent.valuation.route import MIN_PERIODS
        assert MIN_PERIODS >= 4          # 判周期至少要 4 期

    def test_annual_series_helper_filters_to_fiscal_years(self):
        from ir_agent.ledger import Fact, Method
        from ir_agent.valuation.route import annual_series
        from datetime import date
        facts = [
            Fact(key="net_profit", value=D("100"), unit="元", currency="CNY",
                 period=p, as_of=date(2026, 4, 20), source_id="em",
                 method=Method.REPORTED)
            for p in ("2024FY", "2024H1", "2025Q1", "2025FY")]
        assert annual_series(facts, "net_profit") == [D("100"), D("100")]

    def test_series_is_ordered_oldest_first(self):
        from ir_agent.ledger import Fact, Method
        from ir_agent.valuation.route import annual_series
        from datetime import date
        facts = [
            Fact(key="net_profit", value=D(str(v)), unit="元", currency="CNY",
                 period=p, as_of=date(2026, 4, 20), source_id="em",
                 method=Method.REPORTED)
            for p, v in (("2025FY", 3), ("2023FY", 1), ("2024FY", 2))]
        assert annual_series(facts, "net_profit") == [D("1"), D("2"), D("3")]

    def test_other_keys_are_ignored(self):
        from ir_agent.ledger import Fact, Method
        from ir_agent.valuation.route import annual_series
        from datetime import date
        facts = [Fact(key=k, value=D("5"), unit="元", currency="CNY",
                      period="2025FY", as_of=date(2026, 4, 20),
                      source_id="em", method=Method.REPORTED)
                 for k in ("net_profit", "revenue")]
        assert annual_series(facts, "net_profit") == [D("5")]


class TestVolatilitySourceIsNamedCorrectly:
    """高波动有两种来源，路由结论相同但**理由不同** —— 而理由要进研报。

    实测比亚迪 55 49 36 21 60 40 177 313 416 338: 后三年均值是前三年的
    7.6 倍，是**增长跃迁**不是周期波动。写成「具备强周期特征」是错的。
    """

    GROWTH = series(55, 49, 36, 21, 60, 40, 177, 313, 416, 338)
    CYCLE = series(122, 70, 43, 144, 75, -135, 56, -64, 97, 55)

    def test_level_shift_detects_regime_change(self):
        from ir_agent.valuation.route import level_shift
        assert level_shift(self.GROWTH) > D("3")

    def test_cycling_series_has_no_large_level_shift(self):
        from ir_agent.valuation.route import level_shift
        s = level_shift(self.CYCLE)
        assert D("0.33") < s < D("3")

    def test_short_series_has_no_level_shift(self):
        from ir_agent.valuation.route import level_shift
        assert level_shift(series(100, 110)) is None

    def test_growth_case_still_routes_away_from_dcf(self):
        """结论不变 —— 六年涨二十倍，永续增长假设同样无从谈起。"""
        r = route(CompanyType.GENERAL, net_profit_history=self.GROWTH,
                  current_net_profit=D("338"))
        assert r.method is ValuationMethod.PB_ROE

    def test_growth_reason_says_跃升_not_周期(self):
        r = route(CompanyType.GENERAL, net_profit_history=self.GROWTH,
                  current_net_profit=D("338"))
        assert "周期" not in r.reason
        assert "跃升" in r.reason or "量级" in r.reason

    def test_true_cycle_reason_still_says_周期(self):
        r = route(CompanyType.GENERAL, net_profit_history=self.CYCLE,
                  current_net_profit=D("55"))
        assert "周期" in r.reason

    def test_moderate_volatility_without_shift_still_gets_dcf(self):
        """宝钢 93 204 230 139 141 265 140 137 86 114 —— 十年只摆三倍、
        无亏损年。行业标签会说「钢铁必是周期股」，数据不支持。"""
        baogang = series(93, 204, 230, 139, 141, 265, 140, 137, 86, 114)
        r = route(CompanyType.GENERAL, net_profit_history=baogang,
                  current_net_profit=D("114"))
        assert r.method is ValuationMethod.DCF
