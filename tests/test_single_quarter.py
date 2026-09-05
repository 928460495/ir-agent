from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.operators import periods

D = Decimal
AS_OF = date(2026, 4, 30)
LATER = date(2026, 5, 1)


def led_cumulative(**by_period) -> FactLedger:
    led = FactLedger()
    for period, v in by_period.items():
        led.put(Fact(key="revenue", value=D(str(v)), unit="元", currency="CNY",
                     period=period.replace("_", "-"), as_of=AS_OF,
                     source_id="sina_x", method=Method.REPORTED))
    return led


class TestPeriodNaming:
    def test_single_quarter_labels_are_distinct_from_cumulative(self):
        """2025Q1-Q3 是累计，2025Q3S 是单季。同名会让两者被混用。"""
        assert periods.single_label(2025, 3) == "2025Q3S"
        assert periods.single_label(2025, 1) == "2025Q1S"

    def test_labels_are_ascii_so_placeholders_can_reference_them(self):
        from ir_agent.citation import REF_RE
        label = periods.single_label(2025, 4)
        assert REF_RE.fullmatch(f"[[src#revenue@{label}]]")

    def test_cumulative_label_for_each_quarter(self):
        assert periods.cumulative_label(2025, 1) == "2025Q1"
        assert periods.cumulative_label(2025, 2) == "2025H1"
        assert periods.cumulative_label(2025, 3) == "2025Q1-Q3"
        assert periods.cumulative_label(2025, 4) == "2025FY"


class TestDecumulate:
    def test_q1_single_equals_q1_cumulative(self):
        led = led_cumulative(**{"2025Q1": 100})
        out = periods.decumulate(led, "revenue", 2025, as_of=LATER)
        assert out["2025Q1S"].value == D("100")

    def test_q2_is_h1_minus_q1(self):
        led = led_cumulative(**{"2025Q1": 100, "2025H1": 250})
        out = periods.decumulate(led, "revenue", 2025, as_of=LATER)
        assert out["2025Q2S"].value == D("150")

    def test_q3_is_q1q3_minus_h1(self):
        led = led_cumulative(**{"2025Q1": 100, "2025H1": 250, "2025Q1_Q3": 400})
        out = periods.decumulate(led, "revenue", 2025, as_of=LATER)
        assert out["2025Q3S"].value == D("150")

    def test_q4_is_fy_minus_q1q3(self):
        led = led_cumulative(**{"2025Q1": 100, "2025H1": 250,
                                "2025Q1_Q3": 400, "2025FY": 600})
        out = periods.decumulate(led, "revenue", 2025, as_of=LATER)
        assert out["2025Q4S"].value == D("200")

    def test_single_quarters_sum_back_to_the_full_year(self):
        led = led_cumulative(**{"2025Q1": 100, "2025H1": 250,
                                "2025Q1_Q3": 400, "2025FY": 600})
        out = periods.decumulate(led, "revenue", 2025, as_of=LATER)
        total = sum(out[f"2025Q{i}S"].value for i in (1, 2, 3, 4))
        assert total == D("600")

    def test_missing_intermediate_period_skips_only_that_quarter(self):
        """缺 H1 时 Q2/Q3 都算不出，但 Q1 仍然有效 —— 不该整体失败。"""
        led = led_cumulative(**{"2025Q1": 100, "2025Q1_Q3": 400, "2025FY": 600})
        out = periods.decumulate(led, "revenue", 2025, as_of=LATER)
        assert "2025Q1S" in out and "2025Q4S" in out
        assert "2025Q2S" not in out and "2025Q3S" not in out

    def test_results_are_computed_facts_with_provenance(self):
        led = led_cumulative(**{"2025Q1": 100, "2025H1": 250})
        f = periods.decumulate(led, "revenue", 2025, as_of=LATER)["2025Q2S"]
        assert f.method is Method.COMPUTED
        assert set(f.derived_from) == {"revenue@2025H1", "revenue@2025Q1"}

    def test_written_back_into_the_ledger(self):
        led = led_cumulative(**{"2025Q1": 100, "2025H1": 250})
        periods.decumulate(led, "revenue", 2025, as_of=LATER)
        assert led.get("revenue", "2025Q2S", as_of=LATER).value == D("150")

    def test_lookahead_is_respected(self):
        from ir_agent.ledger import LookAheadError
        led = led_cumulative(**{"2025Q1": 100, "2025H1": 250})
        with pytest.raises(LookAheadError):
            periods.decumulate(led, "revenue", 2025, as_of=date(2025, 1, 1),
                               strict=True)

    def test_negative_single_quarter_is_kept_not_suppressed(self):
        """单季亏损是真实结果，累计回落必须如实呈现。"""
        led = led_cumulative(**{"2025Q1": 100, "2025H1": 60})
        out = periods.decumulate(led, "revenue", 2025, as_of=LATER)
        assert out["2025Q2S"].value == D("-40")
