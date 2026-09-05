from datetime import date
from decimal import Decimal

import pytest

from ir_agent.sources.financials import (
    Statement,
    parse_sina_table,
    period_label,
    to_facts,
)

BALANCE_TSV = """报表日期\t20251231\t20250930\t19700101\t
单位\t元\t元\t\t
流动资产
货币资金\t51690610946.50\t51753057846.45\t0\t
资产总计\t303834844021.44\t304738184929.86\t0\t
负债合计\t49875590112.37\t39033160057.01\t0\t
所有者权益
所有者权益(或股东权益)合计\t253959253909.07\t265705024872.85\t0\t
"""

PROFIT_TSV = """报表日期\t20251231\t20250930\t19700101\t
单位\t元\t元\t\t
一、营业总收入\t172054171890.91\t130903889634.88\t0\t
营业收入\t168838102514.79\t128453707655.86\t0\t
营业成本\t14892277570.91\t11183972073.77\t0\t
五、净利润\t85310324833.67\t66898804746.17\t0\t
归属于母公司所有者的净利润\t82320067101.68\t64626746712.18\t0\t
少数股东损益\t2990257731.99\t2272058033.99\t0\t
"""

CASHFLOW_TSV = """报表日期\t20251231\t20250930\t19700101\t
单位\t元\t元\t\t
净利润\t85310324833.67\t0\t0\t
五、现金及现金等价物净增加额\t-43544479810.11\t-10467626915.55\t0\t
加:期初现金及现金等价物余额\t169970089257.83\t169970089257.83\t0\t
六、期末现金及现金等价物余额\t126425609447.72\t159502462342.28\t0\t
"""


class TestPeriodLabel:
    def test_year_end_is_labelled_full_year(self):
        assert period_label("20251231") == "2025FY"

    def test_q3_is_labelled_as_cumulative_not_single_quarter(self):
        """新浪财报是累计口径。标成 2025Q3 会让人误当单季，同比就算错了。"""
        assert period_label("20250930") == "2025Q1-Q3"

    def test_half_year_label(self):
        assert period_label("20250630") == "2025H1"

    def test_q1_label(self):
        assert period_label("20250331") == "2025Q1"


class TestParseSinaTable:
    def test_returns_a_column_per_report_date(self):
        t = parse_sina_table(BALANCE_TSV)
        assert "2025FY" in t and "2025Q1-Q3" in t

    def test_placeholder_epoch_column_is_dropped(self):
        """19700101 是新浪的占位列，混进来会产生一堆零值事实。"""
        t = parse_sina_table(BALANCE_TSV)
        assert all("1970" not in p for p in t)

    def test_values_are_decimal(self):
        t = parse_sina_table(BALANCE_TSV)
        assert t["2025FY"]["资产总计"] == Decimal("303834844021.44")

    def test_section_header_rows_without_values_are_skipped(self):
        t = parse_sina_table(BALANCE_TSV)
        assert "流动资产" not in t["2025FY"]

    def test_unit_row_is_not_treated_as_a_line_item(self):
        t = parse_sina_table(BALANCE_TSV)
        assert "单位" not in t["2025FY"]


class TestToFacts:
    def _facts(self, as_of=date(2026, 3, 28)):
        return to_facts(
            {
                Statement.BALANCE: parse_sina_table(BALANCE_TSV),
                Statement.PROFIT: parse_sina_table(PROFIT_TSV),
                Statement.CASHFLOW: parse_sina_table(CASHFLOW_TSV),
            },
            as_of_by_period={"2025FY": as_of},
            source_id="sina_x",
        )

    def test_canonical_keys_are_produced(self):
        keys = {f.key for f in self._facts()}
        assert {"total_assets", "total_liabilities", "total_equity",
                "revenue", "cost_of_revenue", "net_profit",
                "net_profit_attr_parent", "minority_interest_profit",
                "cash_begin", "cash_net_change", "cash_end",
                "cf_net_profit"} <= keys

    def test_same_label_in_two_statements_maps_to_distinct_keys(self):
        """利润表与现金流量表都有「净利润」，必须分别落到 net_profit / cf_net_profit，
        否则跨表校验会变成自己跟自己比。"""
        facts = {f.key: f for f in self._facts()}
        assert facts["net_profit"].value == facts["cf_net_profit"].value
        assert facts["net_profit"].key != facts["cf_net_profit"].key

    def test_as_of_comes_from_the_supplied_disclosure_date(self):
        facts = self._facts(as_of=date(2026, 3, 28))
        assert all(f.as_of == date(2026, 3, 28) for f in facts)

    def test_period_without_a_known_disclosure_date_is_dropped(self):
        """没有披露日就没有 as_of，宁可丢弃也不能猜 —— 猜就是未来函数。"""
        facts = self._facts()
        assert all(f.period == "2025FY" for f in facts)

    def test_facts_carry_the_source_id(self):
        assert all(f.source_id == "sina_x" for f in self._facts())


class TestRealDataReconciles:
    def test_maotai_fy2025_passes_all_three_statement_checks(self):
        from ir_agent.ledger import FactLedger
        from ir_agent.validate.reconcile import reconcile

        led = FactLedger()
        for f in to_facts(
            {
                Statement.BALANCE: parse_sina_table(BALANCE_TSV),
                Statement.PROFIT: parse_sina_table(PROFIT_TSV),
                Statement.CASHFLOW: parse_sina_table(CASHFLOW_TSV),
            },
            as_of_by_period={"2025FY": date(2026, 3, 28)},
            source_id="sina_x",
        ):
            led.put(f)

        r = reconcile(led, "2025FY", as_of=date(2026, 4, 1))
        assert r.ok, r.summary()
        assert r.checks_run >= 4


@pytest.mark.live
class TestLiveFinancials:
    def test_fetches_and_reconciles_real_statements(self):
        from ir_agent.sources.financials import fetch_statements
        stmts = fetch_statements("600519", 2025)
        assert Statement.BALANCE in stmts
        assert "2025FY" in stmts[Statement.BALANCE]
        assert stmts[Statement.BALANCE]["2025FY"]["资产总计"] > 0


class TestPerStatementProvenance:
    """每个事实必须指向它真正来源的那张表的快照，而不是任取一张。
    指错文件的溯源比没有溯源更危险 —— 它看起来是通过的。"""

    def test_profit_and_balance_facts_carry_different_source_ids(self):
        facts = to_facts(
            {
                Statement.BALANCE: parse_sina_table(BALANCE_TSV),
                Statement.PROFIT: parse_sina_table(PROFIT_TSV),
                Statement.CASHFLOW: parse_sina_table(CASHFLOW_TSV),
            },
            as_of_by_period={"2025FY": date(2026, 3, 28)},
            source_id={
                Statement.BALANCE: "snap_balance",
                Statement.PROFIT: "snap_profit",
                Statement.CASHFLOW: "snap_cashflow",
            },
        )
        by_key = {f.key: f.source_id for f in facts}
        assert by_key["total_assets"] == "snap_balance"
        assert by_key["revenue"] == "snap_profit"
        assert by_key["cash_end"] == "snap_cashflow"
