from datetime import date
from decimal import Decimal

import pytest

from ir_agent.sources.eastmoney import EM_REPORTS, parse_em_rows, period_from_report

# 2026-09-04 从 datacenter.eastmoney.com 实抓（截取所需字段）
EM_BALANCE = [{
    "SECURITY_CODE": "600183", "REPORT_DATE": "2025-12-31 00:00:00",
    "REPORT_TYPE": "年报", "NOTICE_DATE": "2026-03-20 00:00:00",
    "TOTAL_ASSETS": 42398289384.02, "TOTAL_LIABILITIES": 21338433915.16,
    "TOTAL_EQUITY": 21059855468.86, "TOTAL_PARENT_EQUITY": 18501750849.25,
    "MINORITY_EQUITY": 2558104619.61,
}]
EM_INCOME = [{
    "SECURITY_CODE": "600183", "REPORT_DATE": "2025-12-31 00:00:00",
    "REPORT_TYPE": "年报", "NOTICE_DATE": "2026-03-20 00:00:00",
    "OPERATE_INCOME": 19025611766.13, "OPERATE_COST": 13186218676.71,
    "NETPROFIT": 3717825241.13, "PARENT_NETPROFIT": 3286977278.33,
    "MINORITY_INTEREST": 430847962.8,
}]
EM_CASHFLOW = [{
    "SECURITY_CODE": "600183", "REPORT_DATE": "2025-12-31 00:00:00",
    "REPORT_TYPE": "年报", "NOTICE_DATE": "2026-03-20 00:00:00",
    "NETPROFIT": 3717825241.13, "CCE_ADD": 251547333.1,
    "BEGIN_CCE": 1731247516.14, "END_CCE": 1982794849.24,
}]


class TestPeriodFromReport:
    @pytest.mark.parametrize("rd,expected", [
        ("2025-12-31 00:00:00", "2025FY"),
        ("2025-09-30 00:00:00", "2025Q1-Q3"),
        ("2025-06-30 00:00:00", "2025H1"),
        ("2025-03-31 00:00:00", "2025Q1"),
    ])
    def test_report_date_maps_to_the_same_labels_as_sina(self, rd, expected):
        """两源必须用同一套期间标签，否则交叉验证会全部落空。"""
        assert period_from_report(rd) == expected


class TestParseEmRows:
    def test_canonical_keys_match_the_sina_adapter(self):
        facts = parse_em_rows(EM_BALANCE, EM_REPORTS["balance"], source_id="em_x")
        keys = {f.key for f in facts}
        assert {"total_assets", "total_liabilities", "total_equity"} <= keys

    def test_values_are_decimal_not_float(self):
        """东财返回 JSON number（float），必须转 Decimal 才能进账本。"""
        f = next(f for f in parse_em_rows(EM_BALANCE, EM_REPORTS["balance"],
                                          source_id="em_x")
                 if f.key == "total_assets")
        assert isinstance(f.value, Decimal)
        assert f.value == Decimal("42398289384.02")

    def test_notice_date_becomes_as_of(self):
        """东财自带公告日 —— 这是它比新浪更有价值的地方。"""
        f = parse_em_rows(EM_BALANCE, EM_REPORTS["balance"], source_id="em_x")[0]
        assert f.as_of == date(2026, 3, 20)

    def test_null_fields_are_skipped_not_zeroed(self):
        rows = [dict(EM_BALANCE[0], TOTAL_LIABILITIES=None)]
        keys = {f.key for f in parse_em_rows(rows, EM_REPORTS["balance"],
                                            source_id="em_x")}
        assert "total_liabilities" not in keys

    def test_income_and_cashflow_net_profit_land_on_distinct_keys(self):
        inc = {f.key for f in parse_em_rows(EM_INCOME, EM_REPORTS["income"],
                                            source_id="em_x")}
        cf = {f.key for f in parse_em_rows(EM_CASHFLOW, EM_REPORTS["cashflow"],
                                           source_id="em_x")}
        assert "net_profit" in inc
        assert "cf_net_profit" in cf


class TestEastmoneyDataReconciles:
    def test_all_three_statements_tie_out(self):
        from ir_agent.ledger import FactLedger
        from ir_agent.validate.reconcile import reconcile

        led = FactLedger()
        for rows, spec in ((EM_BALANCE, EM_REPORTS["balance"]),
                           (EM_INCOME, EM_REPORTS["income"]),
                           (EM_CASHFLOW, EM_REPORTS["cashflow"])):
            for f in parse_em_rows(rows, spec, source_id="em_x"):
                led.put(f)

        r = reconcile(led, "2025FY", as_of=date(2026, 4, 1))
        assert r.ok, r.summary()
        assert r.checks_run >= 4


@pytest.mark.live
class TestLiveEastmoney:
    def test_fetches_real_statements(self):
        from ir_agent.sources.eastmoney import fetch_statements_em
        facts, sids = fetch_statements_em("600183", market="SH")
        assert facts
        assert any(f.key == "total_assets" for f in facts)
        assert all(f.value is not None for f in facts)
