from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.operators import multiples, ttm

D = Decimal
AS_OF = date(2026, 4, 30)
LATER = date(2026, 5, 1)


def led_singles(key="net_profit", **by_period) -> FactLedger:
    led = FactLedger()
    for period, v in by_period.items():
        led.put(Fact(key=key, value=D(str(v)), unit="元", currency="CNY",
                     period=period, as_of=AS_OF, source_id="calc_x",
                     method=Method.COMPUTED))
    return led


class TestTTM:
    def test_sums_the_trailing_four_single_quarters(self):
        led = led_singles(**{"2024Q4S": 10, "2025Q1S": 20,
                             "2025Q2S": 30, "2025Q3S": 40})
        f = ttm.compute(led, "net_profit", 2025, 3, as_of=LATER)
        assert f.value == D("100")

    def test_ttm_at_year_end_equals_the_full_year(self):
        led = led_singles(**{"2025Q1S": 10, "2025Q2S": 20,
                             "2025Q3S": 30, "2025Q4S": 40})
        f = ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        assert f.value == D("100")

    def test_result_key_is_suffixed_ttm(self):
        led = led_singles(**{"2025Q1S": 10, "2025Q2S": 20,
                             "2025Q3S": 30, "2025Q4S": 40})
        f = ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        assert f.key == "net_profit_ttm"

    def test_missing_any_quarter_returns_none_rather_than_a_partial_sum(self):
        """三个季度加总冒充 TTM 会系统性低估，比没有 TTM 更糟。"""
        led = led_singles(**{"2025Q2S": 20, "2025Q3S": 30, "2025Q4S": 40})
        assert ttm.compute(led, "net_profit", 2025, 4, as_of=LATER) is None

    def test_derived_from_lists_all_four_quarters(self):
        led = led_singles(**{"2025Q1S": 10, "2025Q2S": 20,
                             "2025Q3S": 30, "2025Q4S": 40})
        f = ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        assert len(f.derived_from) == 4

    def test_trailing_window_crosses_the_year_boundary(self):
        led = led_singles(**{"2024Q3S": 5, "2024Q4S": 10,
                             "2025Q1S": 20, "2025Q2S": 30})
        f = ttm.compute(led, "net_profit", 2025, 2, as_of=LATER)
        assert f.value == D("65")


class TestPEIsNowReachable:
    """修复前 multiples 支持 pe_ttm，但没有任何路径写入 net_profit_ttm，
    PE 从未被算出过 —— 而且是静默跳过。"""

    def _led(self, q4=40):
        led = led_singles(**{"2025Q1S": 10, "2025Q2S": 20,
                             "2025Q3S": 30, "2025Q4S": q4})
        for k, v in (("market_cap", 2000), ("equity_attr_parent", 500)):
            led.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                         period="2025FY", as_of=AS_OF, source_id="src",
                         method=Method.REPORTED))
        return led

    def test_pe_is_produced_once_ttm_exists(self):
        led = self._led()
        ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        out = multiples.compute(led, "2025FY", as_of=LATER)
        assert out["pe_ttm"].value == D("20")

    def test_pe_still_absent_when_ttm_earnings_are_negative(self):
        led = self._led(q4=-200)
        ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        out = multiples.compute(led, "2025FY", as_of=LATER)
        assert "pe_ttm" not in out
