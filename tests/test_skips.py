import pytest

from ir_agent.skips import Skip, SkipLog, SkipReason


class TestSkipReason:
    def test_three_reasons_are_distinguished(self):
        assert {r.value for r in SkipReason} == {
            "data_not_disclosed", "fetch_incomplete", "not_applicable"}

    def test_only_fetch_incomplete_is_a_defect(self):
        """数据没披露、口径不适用都是正常的；只有「我们没抓到」是故障。"""
        assert SkipReason.FETCH_INCOMPLETE.is_defect is True
        assert SkipReason.DATA_NOT_DISCLOSED.is_defect is False
        assert SkipReason.NOT_APPLICABLE.is_defect is False


class TestSkipLog:
    def test_records_what_and_why(self):
        log = SkipLog()
        log.add("同比", SkipReason.FETCH_INCOMPLETE, "公告分页截断，2024FY 缺失")
        assert log.entries[0].what == "同比"
        assert log.entries[0].reason is SkipReason.FETCH_INCOMPLETE

    def test_defects_are_separable_from_benign_skips(self):
        log = SkipLog()
        log.add("毛利勾稽", SkipReason.DATA_NOT_DISCLOSED, "CAS 利润表无毛利行")
        log.add("PE", SkipReason.NOT_APPLICABLE, "负盈利")
        log.add("同比", SkipReason.FETCH_INCOMPLETE, "分页截断")
        assert [d.what for d in log.defects] == ["同比"]
        assert len(log.benign) == 2

    def test_clean_log_has_no_defects(self):
        log = SkipLog()
        log.add("PE", SkipReason.NOT_APPLICABLE, "负盈利")
        assert log.has_defects is False

    def test_summary_separates_the_two_classes(self):
        log = SkipLog()
        log.add("毛利勾稽", SkipReason.DATA_NOT_DISCLOSED, "CAS 无此行")
        log.add("同比", SkipReason.FETCH_INCOMPLETE, "分页截断")
        out = log.summary()
        assert "抓取不全" in out
        assert "同比" in out and "毛利勾稽" in out

    def test_empty_log_summarises_as_nothing_skipped(self):
        assert SkipLog().summary() == ""

    def test_same_item_is_not_recorded_twice(self):
        log = SkipLog()
        log.add("PE", SkipReason.NOT_APPLICABLE, "负盈利")
        log.add("PE", SkipReason.NOT_APPLICABLE, "负盈利")
        assert len(log.entries) == 1
