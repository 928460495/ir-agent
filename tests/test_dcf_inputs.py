"""DCF 输入项 —— 自由现金流、净负债、股本。

三项都不在 V0 的字段映射里，是 DCF 接线的前提。设计立场沿用 V0:
**缺任一分量则该派生量不产生，绝不补 0** —— 一个用 0 资本开支算出来的
自由现金流，会让估值系统性偏高，而且不会报错。

股本不取 SHARE_CAPITAL（那是面值×股数，A 股面值通常但不总是 1 元），
改用 市值 ÷ 股价 —— 由行情直接推出，不依赖面值假设。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.valuation.inputs import (
    MissingInput,
    free_cash_flow,
    net_debt,
    shares_outstanding,
)

D = Decimal
AS_OF = date(2026, 6, 1)
P = "2025FY"


def led(**kv) -> FactLedger:
    l = FactLedger()
    for k, v in kv.items():
        per = "2026-09-04" if k in ("market_cap", "last_price") else P
        l.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                   period=per, as_of=date(2026, 4, 20),
                   source_id="em_x", method=Method.REPORTED))
    return l


class TestFreeCashFlow:
    def test_operating_cash_minus_capex(self):
        f = free_cash_flow(led(cf_operating=1000, capex=300), P, as_of=AS_OF)
        assert f.value == D("700")

    def test_result_is_a_computed_fact_in_the_ledger(self):
        l = led(cf_operating=1000, capex=300)
        free_cash_flow(l, P, as_of=AS_OF)
        assert l.get("fcf", P, as_of=AS_OF).method is Method.COMPUTED

    def test_provenance_names_both_inputs(self):
        f = free_cash_flow(led(cf_operating=1000, capex=300), P, as_of=AS_OF)
        assert set(f.derived_from) == {f"cf_operating@{P}", f"capex@{P}"}

    def test_missing_capex_refuses_rather_than_assuming_zero(self):
        """0 资本开支会让 FCF 系统性偏高，而且不会报错。"""
        with pytest.raises(MissingInput, match="capex"):
            free_cash_flow(led(cf_operating=1000), P, as_of=AS_OF)

    def test_missing_operating_cash_refuses(self):
        with pytest.raises(MissingInput, match="cf_operating"):
            free_cash_flow(led(capex=300), P, as_of=AS_OF)

    def test_negative_fcf_is_reported_as_is(self):
        """烧钱是真实状态，不该被抹平。"""
        f = free_cash_flow(led(cf_operating=200, capex=900), P, as_of=AS_OF)
        assert f.value == D("-700")


class TestNetDebt:
    FULL = dict(short_loan=100, long_loan=200, bond_payable=50,
                noncurrent_liab_1y=30, cash_and_equivalents=180)

    def test_interest_bearing_debt_minus_cash(self):
        assert net_debt(led(**self.FULL), P, as_of=AS_OF).value == D("200")

    def test_absent_debt_components_count_as_zero(self):
        """没有应付债券就是没有 —— 与「拿不到数据」不同，这里是真实的 0。"""
        d = dict(self.FULL); d.pop("bond_payable")
        assert net_debt(led(**d), P, as_of=AS_OF).value == D("150")

    def test_cash_is_required_not_optional(self):
        """现金缺失会让净负债严重高估 —— 必须拒绝而非当 0。"""
        d = dict(self.FULL); d.pop("cash_and_equivalents")
        with pytest.raises(MissingInput, match="cash"):
            net_debt(led(**d), P, as_of=AS_OF)

    def test_net_cash_position_is_negative_net_debt(self):
        d = dict(self.FULL, cash_and_equivalents=1000)
        assert net_debt(led(**d), P, as_of=AS_OF).value < 0

    def test_no_debt_at_all_still_computes(self):
        assert net_debt(led(cash_and_equivalents=500), P,
                        as_of=AS_OF).value == D("-500")


class TestSharesOutstanding:
    def test_derived_from_market_cap_and_price(self):
        s = shares_outstanding(led(market_cap=1000, last_price=10),
                               "2026-09-04", as_of=AS_OF)
        assert s.value == D("100")

    def test_does_not_use_share_capital(self):
        """SHARE_CAPITAL 是面值×股数，依赖面值为 1 元的假设。"""
        l = led(market_cap=1000, last_price=10)
        l.put(Fact(key="share_capital", value=D("999"), unit="股",
                   currency=None, period=P, as_of=date(2026, 4, 20),
                   source_id="em_x", method=Method.REPORTED))
        assert shares_outstanding(l, "2026-09-04", as_of=AS_OF).value == D("100")

    def test_zero_price_refuses(self):
        with pytest.raises(MissingInput):
            shares_outstanding(led(market_cap=1000, last_price=0),
                               "2026-09-04", as_of=AS_OF)

    def test_missing_quote_refuses(self):
        with pytest.raises(MissingInput, match="market_cap"):
            shares_outstanding(led(last_price=10), "2026-09-04", as_of=AS_OF)


class TestCashClassificationGap:
    """货币资金显著低于现金流量表期末现金 = 现金被分类在别处。

    贵州茅台实测: 货币资金 516.9 亿，但期末现金及现金等价物 1264.3 亿 ——
    差额在「拆出资金」991 亿（集团财务公司的同业拆出）。只按货币资金算净负债，
    会少算约 750 亿净现金，DCF 每股价值被低估约 60 元。

    穷举类现金科目会一直追着新情况跑；**检测不一致**才是可持续的做法 ——
    对不上就报出来交给人判断，而不是悄悄用一个偏低的数。
    """

    YI = D("1e8")

    def _led(self, monetary_yi, cash_end_yi):
        """入参以「亿元」为单位，转成账本的「元」 —— 告警文案按亿元展示，
        测试数据必须是真实量级才验得到格式。"""
        return led(cash_and_equivalents=monetary_yi * self.YI,
                   cash_end=cash_end_yi * self.YI, short_loan=0)

    def test_consistent_cash_produces_no_warning(self):
        from ir_agent.valuation.inputs import cash_gap_warning
        assert cash_gap_warning(self._led(1000, 1000), P, as_of=AS_OF) is None

    def test_small_gap_is_tolerated(self):
        """受限资金等原因造成的小幅差异是常态。"""
        from ir_agent.valuation.inputs import cash_gap_warning
        assert cash_gap_warning(self._led(1000, 950), P, as_of=AS_OF) is None

    def test_large_shortfall_is_reported(self):
        from ir_agent.valuation.inputs import cash_gap_warning
        w = cash_gap_warning(self._led(517, 1264), P, as_of=AS_OF)
        assert w and "净负债" in w

    def test_warning_states_both_figures(self):
        from ir_agent.valuation.inputs import cash_gap_warning
        w = cash_gap_warning(self._led(517, 1264), P, as_of=AS_OF)
        assert "517" in w and "1,264" in w

    def test_missing_either_figure_yields_no_warning(self):
        """缺数据不是不一致 —— 不该报一个无从判断的告警。"""
        from ir_agent.valuation.inputs import cash_gap_warning
        assert cash_gap_warning(led(cash_and_equivalents=500), P,
                                as_of=AS_OF) is None

    def test_net_debt_still_uses_the_conservative_figure(self):
        """告警归告警，取值仍用货币资金 —— 把拆出资金当自由现金是另一个
        判断，应由人做，不该由代码默认。"""
        assert net_debt(self._led(517, 1264), P, as_of=AS_OF).value == D("-517") * self.YI
