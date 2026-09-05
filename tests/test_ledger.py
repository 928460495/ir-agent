from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method


def mkfact(**kw):
    """A reported revenue fact, overridable per test."""
    base = dict(
        key="revenue",
        value=Decimal("1000"),
        unit="元",
        currency="CNY",
        period="2025Q3",
        as_of=date(2025, 10, 25),
        source_id="cninfo_a1",
        method=Method.REPORTED,
    )
    base.update(kw)
    return Fact(**base)


class TestFact:
    def test_value_must_be_decimal_not_float(self):
        with pytest.raises(TypeError, match="Decimal"):
            mkfact(value=1000.0)

    def test_fact_is_immutable(self):
        f = mkfact()
        with pytest.raises(Exception):
            f.value = Decimal("2000")

    def test_extracted_fact_must_declare_confidence_below_one(self):
        with pytest.raises(ValueError, match="confidence"):
            mkfact(method=Method.EXTRACTED, confidence=1.0)

    def test_reported_fact_defaults_to_full_confidence(self):
        assert mkfact().confidence == 1.0


class TestLedgerBasics:
    def test_get_returns_the_fact_that_was_put(self):
        led = FactLedger()
        f = mkfact()
        led.put(f)
        assert led.get("revenue", "2025Q3", as_of=date(2025, 12, 31)) is f

    def test_missing_fact_raises_keyerror(self):
        led = FactLedger()
        with pytest.raises(KeyError):
            led.get("revenue", "2025Q3", as_of=date(2025, 12, 31))

    def test_later_revision_supersedes_earlier_one(self):
        """Restated figures are common; the newest as_of at or before the cutoff wins."""
        led = FactLedger()
        led.put(mkfact(value=Decimal("1000"), as_of=date(2025, 10, 25), source_id="orig"))
        led.put(mkfact(value=Decimal("1100"), as_of=date(2026, 4, 20), source_id="restated"))
        assert led.get("revenue", "2025Q3", as_of=date(2026, 6, 1)).source_id == "restated"

    def test_revision_after_cutoff_is_invisible(self):
        """As of 2025-12-31 the restatement had not happened yet."""
        led = FactLedger()
        led.put(mkfact(value=Decimal("1000"), as_of=date(2025, 10, 25), source_id="orig"))
        led.put(mkfact(value=Decimal("1100"), as_of=date(2026, 4, 20), source_id="restated"))
        assert led.get("revenue", "2025Q3", as_of=date(2025, 12, 31)).source_id == "orig"


class TestLookAheadGuard:
    def test_fact_not_yet_knowable_raises_lookahead(self):
        led = FactLedger()
        led.put(mkfact(as_of=date(2025, 10, 25)))
        with pytest.raises(LookAheadError) as e:
            led.get("revenue", "2025Q3", as_of=date(2025, 10, 1))
        assert "2025-10-25" in str(e.value)

    def test_as_of_equal_to_disclosure_date_is_allowed(self):
        """Disclosure day itself is knowable — boundary is inclusive."""
        led = FactLedger()
        led.put(mkfact(as_of=date(2025, 10, 25)))
        assert led.get("revenue", "2025Q3", as_of=date(2025, 10, 25)).value == Decimal("1000")

    def test_ledger_can_be_pinned_so_callers_cannot_forget_as_of(self):
        led = FactLedger()
        led.put(mkfact(as_of=date(2025, 10, 25)))
        pinned = led.pin(date(2025, 10, 1))
        with pytest.raises(LookAheadError):
            pinned.get("revenue", "2025Q3")
