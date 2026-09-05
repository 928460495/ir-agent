from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method
from ir_agent.operators import multiples
from ir_agent.periods_spot import spot_period

D = Decimal
FY = "2025FY"


class TestSpotPeriodNaming:
    def test_spot_period_is_the_quote_date(self):
        assert spot_period(date(2026, 9, 4)) == "2026-09-04"

    def test_label_is_ascii_so_placeholders_can_reference_it(self):
        from ir_agent.citation import REF_RE
        assert REF_RE.fullmatch(f"[[src#market_cap@{spot_period(date(2026, 9, 4))}]]")

    def test_two_quote_dates_are_distinct_periods(self):
        assert spot_period(date(2026, 9, 4)) != spot_period(date(2026, 9, 5))


def led_with_spot(spot="2026-09-04", cap=1000, equity=250):
    l = FactLedger()
    l.put(Fact(key="market_cap", value=D(str(cap)), unit="元", currency="CNY",
               period=spot, as_of=date(2026, 9, 4), source_id="tencent",
               method=Method.REPORTED))
    l.put(Fact(key="equity_attr_parent", value=D(str(equity)), unit="元",
               currency="CNY", period=FY, as_of=date(2026, 4, 30),
               source_id="sina", method=Method.REPORTED))
    l.put(Fact(key="net_profit_ttm", value=D("50"), unit="元", currency="CNY",
               period=FY, as_of=date(2026, 4, 30), source_id="calc_x",
               method=Method.COMPUTED))
    return l


class TestMultiplesSpanTwoPeriods:
    """PB = 即期市值 ÷ 财报期账面权益。两者本就不同期间，模型必须如实表达，
    否则多期对比时市值会被错配到别的会计期上。"""

    def test_pb_combines_spot_cap_with_fiscal_equity(self):
        out = multiples.compute(led_with_spot(), FY, as_of=date(2026, 9, 5),
                                spot_period="2026-09-04")
        assert out["pb"].value == D("4")

    def test_pe_combines_spot_cap_with_fiscal_ttm_earnings(self):
        out = multiples.compute(led_with_spot(), FY, as_of=date(2026, 9, 5),
                                spot_period="2026-09-04")
        assert out["pe_ttm"].value == D("20")

    def test_result_is_stamped_with_the_spot_period_not_the_fiscal_one(self):
        """倍数随股价每日变动，属于即期量，挂在会计期上会造成错配。"""
        out = multiples.compute(led_with_spot(), FY, as_of=date(2026, 9, 5),
                                spot_period="2026-09-04")
        assert out["pb"].period == "2026-09-04"

    def test_provenance_names_both_periods(self):
        out = multiples.compute(led_with_spot(), FY, as_of=date(2026, 9, 5),
                                spot_period="2026-09-04")
        assert "market_cap@2026-09-04" in out["pb"].derived_from
        assert "equity_attr_parent@2025FY" in out["pb"].derived_from

    def test_two_quote_dates_produce_two_distinct_multiples(self):
        led = led_with_spot(cap=1000)
        led.put(Fact(key="market_cap", value=D("1200"), unit="元", currency="CNY",
                     period="2026-09-05", as_of=date(2026, 9, 5),
                     source_id="tencent", method=Method.REPORTED))
        a = multiples.compute(led, FY, as_of=date(2026, 9, 6),
                              spot_period="2026-09-04")["pb"]
        b = multiples.compute(led, FY, as_of=date(2026, 9, 6),
                              spot_period="2026-09-05")["pb"]
        assert (a.value, b.value) == (D("4"), D("4.8"))

    def test_missing_spot_quote_raises_rather_than_falling_back(self):
        with pytest.raises(KeyError):
            multiples.compute(led_with_spot(), FY, as_of=date(2026, 9, 5),
                              spot_period="2026-01-01")

    def test_lookahead_on_the_spot_quote_is_enforced(self):
        with pytest.raises(LookAheadError):
            multiples.compute(led_with_spot(), FY, as_of=date(2026, 8, 1),
                              spot_period="2026-09-04")


class TestBareNumberAuditToleratesDates:
    def test_iso_date_in_prose_is_not_a_bare_number(self):
        from ir_agent.citation import audit_bare_numbers
        assert audit_bare_numbers("截至 2026-09-04 收盘。") == []
