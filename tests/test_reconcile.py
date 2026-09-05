from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.validate.reconcile import reconcile

AS_OF = date(2025, 10, 25)
P = "2025Q3"


def build(**overrides) -> FactLedger:
    """一套内部自洽的三表数据；各测试按需破坏其中一项。"""
    values = {
        # 资产负债表
        "total_assets": "1000",
        "total_liabilities": "600",
        "total_equity": "400",
        # 利润表
        "revenue": "500",
        "cost_of_revenue": "300",
        "gross_profit": "200",
        "net_profit": "80",
        "net_profit_attr_parent": "75",
        "minority_interest_profit": "5",
        # 现金流量表
        "cash_begin": "120",
        "cash_net_change": "30",
        "cash_end": "150",
        "cf_net_profit": "80",
    }
    values.update({k: str(v) for k, v in overrides.items()})

    led = FactLedger()
    for key, v in values.items():
        led.put(Fact(
            key=key, value=Decimal(v), unit="元", currency="CNY",
            period=P, as_of=AS_OF, source_id="cninfo_a1", method=Method.REPORTED,
        ))
    return led


class TestCleanStatements:
    def test_internally_consistent_statements_pass(self):
        r = reconcile(build(), P, as_of=AS_OF)
        assert r.ok, r.failures
        assert r.checks_run == 5

    def test_report_lists_every_check_by_name(self):
        r = reconcile(build(), P, as_of=AS_OF)
        names = {c.name for c in r.results}
        assert names == {
            "资产=负债+所有者权益",
            "净利润=归母+少数股东损益",
            "毛利=营业收入-营业成本",
            "期末现金=期初+净增加额",
            "利润表净利润=现金流量表起点",
        }


class TestBalanceSheetIdentity:
    def test_unbalanced_balance_sheet_fails(self):
        r = reconcile(build(total_equity="390"), P, as_of=AS_OF)
        assert not r.ok
        assert "资产=负债+所有者权益" in {f.name for f in r.failures}

    def test_failure_reports_the_actual_difference(self):
        r = reconcile(build(total_equity="390"), P, as_of=AS_OF)
        f = next(f for f in r.failures if f.name == "资产=负债+所有者权益")
        assert f.diff == Decimal("10")


class TestProfitSplit:
    def test_minority_interest_mismatch_fails(self):
        r = reconcile(build(minority_interest_profit="7"), P, as_of=AS_OF)
        assert "净利润=归母+少数股东损益" in {f.name for f in r.failures}


class TestCashFlow:
    def test_cash_rollforward_mismatch_fails(self):
        r = reconcile(build(cash_end="160"), P, as_of=AS_OF)
        assert "期末现金=期初+净增加额" in {f.name for f in r.failures}

    def test_income_statement_and_cashflow_net_profit_must_agree(self):
        r = reconcile(build(cf_net_profit="82"), P, as_of=AS_OF)
        assert "利润表净利润=现金流量表起点" in {f.name for f in r.failures}


class TestTolerance:
    def test_rounding_noise_within_tolerance_passes(self):
        """财报以元为单位披露，四舍五入造成的分位差异不应报警。"""
        r = reconcile(build(total_equity="399.9999"), P, as_of=AS_OF,
                      rel_tol=Decimal("0.0005"))
        assert r.ok

    def test_difference_beyond_tolerance_still_fails(self):
        r = reconcile(build(total_equity="398"), P, as_of=AS_OF,
                      rel_tol=Decimal("0.0005"))
        assert not r.ok


class TestMissingData:
    def test_check_is_skipped_when_inputs_absent_not_silently_passed(self):
        led = build()
        led._facts.pop(("cash_end", P))
        r = reconcile(led, P, as_of=AS_OF)
        skipped = [c for c in r.results if c.status == "skipped"]
        assert any(c.name == "期末现金=期初+净增加额" for c in skipped)
        assert r.checks_run == 4

    def test_skipped_checks_do_not_count_as_ok_overall(self):
        """跳过不等于通过 —— 覆盖率要单独可见。"""
        led = build()
        led._facts.pop(("cash_end", P))
        r = reconcile(led, P, as_of=AS_OF)
        assert r.ok is True          # 已跑的都过了
        assert r.complete is False   # 但覆盖不完整


class TestLookAheadInReconcile:
    def test_reconciling_before_disclosure_raises(self):
        from ir_agent.ledger import LookAheadError
        with pytest.raises(LookAheadError):
            reconcile(build(), P, as_of=date(2025, 9, 1))
