"""同一规范化 key 被多行命中时的取值规则。

实测（中国国航 601111）新浪现金流量表里有两行都映射到 cash_net_change:
    '五、现金及现金等价物净增加额' = -6744204000   ← 主表行
    '现金及现金等价物的净增加额'   = 0             ← 补充资料（间接法），未填
后者排在后面，last-write-wins 让 0 覆盖了正确值，勾稽随之失败。
茅台两行值相同所以完全看不出问题 —— 这类 bug 只有跨源勾稽能抓到。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.sources.financials import Statement, parse_sina_table, to_facts

D = Decimal
AS_OF = {"2025FY": date(2026, 3, 28)}


def facts_from(rows: str):
    tsv = "报表日期\t20251231\t\n单位\t元\t\n" + rows
    return to_facts({Statement.CASHFLOW: parse_sina_table(tsv)},
                    as_of_by_period=AS_OF, source_id="s")


class TestFirstRowWins:
    def test_main_statement_row_is_not_overwritten_by_the_supplement(self):
        f = facts_from("五、现金及现金等价物净增加额\t-6744204000\t\n"
                       "现金及现金等价物的净增加额\t0\t\n")
        vals = [x.value for x in f if x.key == "cash_net_change"]
        assert vals == [D("-6744204000")]

    def test_only_one_fact_per_key_and_period(self):
        f = facts_from("五、现金及现金等价物净增加额\t-100\t\n"
                       "现金及现金等价物的净增加额\t0\t\n")
        assert len([x for x in f if x.key == "cash_net_change"]) == 1

    def test_supplement_still_used_when_the_main_row_is_absent(self):
        """别名本身有价值 —— 只是不能覆盖主表行。"""
        f = facts_from("现金及现金等价物的净增加额\t-250\t\n")
        assert [x.value for x in f if x.key == "cash_net_change"] == [D("-250")]

    def test_identical_duplicates_are_harmless(self):
        """茅台的情形: 两行值相同，取哪个都对。"""
        f = facts_from("五、现金及现金等价物净增加额\t-43544479810.11\t\n"
                       "现金及现金等价物的净增加额\t-43544479810.11\t\n")
        assert [x.value for x in f if x.key == "cash_net_change"] == [
            D("-43544479810.11")]


class TestAirChinaReconciles:
    def test_cash_identity_holds_with_the_main_row(self):
        from ir_agent.ledger import FactLedger
        from ir_agent.validate.reconcile import reconcile

        led = FactLedger()
        for f in facts_from(
                "五、现金及现金等价物净增加额\t-6744204000\t\n"
                "加:期初现金及现金等价物余额\t21039472000\t\n"
                "六、期末现金及现金等价物余额\t14295268000\t\n"
                "现金及现金等价物的净增加额\t0\t\n"):
            led.put(f)
        r = reconcile(led, "2025FY", as_of=date(2026, 4, 1))
        assert r.ok, r.summary()
