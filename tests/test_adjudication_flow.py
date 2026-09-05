"""分歧裁决接入流水线。

Tier 顺序决定「默认信谁」，裁决决定「这一次谁对」—— 两者不能互相替代。
中国神华实测中 Tier-1(东财) 恰恰是错的那个，若没有裁决就只能拦下不出报告。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, Method
from ir_agent.validate.adjudicate import Verdict, resolve_disagreements

D = Decimal
AS_OF = date(2026, 3, 31)


def fact(src, key, val, period="2025FY"):
    return Fact(key=key, value=D(val), unit="元", currency="CNY",
                period=period, as_of=AS_OF, source_id=src,
                method=Method.REPORTED)


SHENHUA = {
    "eastmoney": [fact("em", "total_assets", "903830000000"),
                  fact("em", "total_liabilities", "300203000000")],
    "sina": [fact("sina", "total_assets", "627761000000"),
             fact("sina", "total_liabilities", "146310000000")],
}
PDF = {"total_assets": [D("627761000000")],
       "total_liabilities": [D("146310000000")]}


class TestResolveDisagreements:
    def test_winner_facts_are_returned(self):
        out, verdicts = resolve_disagreements(SHENHUA, PDF, "2025FY")
        assert {f.value for f in out} == {D("627761000000"), D("146310000000")}

    def test_verdict_names_the_correct_source(self):
        _, verdicts = resolve_disagreements(SHENHUA, PDF, "2025FY")
        assert verdicts["total_assets"].winner == "sina"

    def test_facts_keep_reported_provenance_not_extracted(self):
        """胜出的是数据源的原始事实，PDF 只是裁判 —— 不改 method。"""
        out, _ = resolve_disagreements(SHENHUA, PDF, "2025FY")
        assert all(f.method is Method.REPORTED for f in out)
        assert all(f.source_id == "sina" for f in out)

    def test_keys_the_pdf_cannot_settle_fall_back_to_tier1(self):
        data = {k: v + [fact(k[:4], "revenue", "1000")]
                for k, v in SHENHUA.items()}
        out, verdicts = resolve_disagreements(data, PDF, "2025FY", primary="eastmoney")
        rev = [f for f in out if f.key == "revenue"]
        assert rev and rev[0].source_id == "east"
        assert "revenue" not in verdicts

    def test_no_source_matches_the_pdf_yields_no_winner(self):
        bad = {"total_assets": [D("111")]}
        out, verdicts = resolve_disagreements(SHENHUA, bad, "2025FY",
                                              primary="eastmoney")
        assert verdicts["total_assets"].winner is None
        # 裁不出来时保留主源值，但分歧仍然记录在案
        assert D("903830000000") in {f.value for f in out}

    def test_only_the_analysed_period_is_touched(self):
        data = {s: fs + [fact(s[:4], "total_assets", "999", period="2024FY")]
                for s, fs in SHENHUA.items()}
        out, _ = resolve_disagreements(data, PDF, "2025FY", primary="eastmoney")
        prior = [f for f in out if f.period == "2024FY"]
        assert prior and prior[0].source_id == "east"


class TestVerdictReporting:
    def test_summary_states_which_source_was_wrong_and_by_how_much(self):
        _, verdicts = resolve_disagreements(SHENHUA, PDF, "2025FY")
        s = verdicts["total_assets"].describe()
        assert "sina" in s and "eastmoney" in s and "%" in s

    def test_unresolved_verdict_says_so_plainly(self):
        v = Verdict(pdf_value=D("100"), winner=None, losers=["a"],
                    rel_diffs={"a": D("1")})
        assert "无任何数据源与之一致" in v.describe()


class TestStrictGateAfterAdjudication:
    """已被原文裁定解决的分歧不该再拦 —— 否则裁决就白做了。
    但裁不出结论的分歧必须继续拦。"""

    def _run(self, verdicts, blocking=("total_assets",)):
        from ir_agent.pipeline import unresolved_disagreements
        from ir_agent.sources.resolve import Disagreement
        ds = [Disagreement(key=k, period="2025FY",
                           values={"eastmoney": D("9"), "sina": D("6")},
                           rel_diff=D("0.5")) for k in blocking]
        return unresolved_disagreements(ds, verdicts)

    def test_adjudicated_disagreement_is_no_longer_blocking(self):
        v = Verdict(pdf_value=D("6"), winner="sina", losers=["eastmoney"],
                    rel_diffs={"sina": D("0"), "eastmoney": D("0.5")})
        assert self._run({"total_assets": v}) == []

    def test_unadjudicated_disagreement_still_blocks(self):
        assert [d.key for d in self._run({})] == ["total_assets"]

    def test_verdict_without_a_winner_still_blocks(self):
        v = Verdict(pdf_value=D("1"), winner=None, losers=["a", "b"],
                    rel_diffs={"a": D("1"), "b": D("2")})
        assert [d.key for d in self._run({"total_assets": v})] == ["total_assets"]

    def test_mixed_case_reports_only_the_unresolved_ones(self):
        v = Verdict(pdf_value=D("6"), winner="sina", losers=["eastmoney"],
                    rel_diffs={"sina": D("0"), "eastmoney": D("0.5")})
        out = self._run({"total_assets": v},
                        blocking=("total_assets", "revenue"))
        assert [d.key for d in out] == ["revenue"]
