from datetime import date

import pytest

from ir_agent.sources.cninfo import TruncatedResult, paginate


def page(n_items, has_more):
    return {"announcements": [
        {"announcementId": str(i), "announcementTitle": f"公告{i}",
         "announcementTime": 1764345600000, "adjunctUrl": "f/a.PDF",
         "secCode": "300498", "secName": "温氏股份"} for i in range(n_items)],
        "hasMore": has_more}


class TestPaginationCompleteness:
    def test_stops_when_upstream_says_no_more(self):
        pages = [page(30, True), page(30, False)]
        got, truncated = paginate(lambda i: pages[i - 1], max_pages=10)
        assert len(got) == 60
        assert truncated is False

    def test_hitting_the_page_cap_with_more_available_is_flagged(self):
        """温氏股份实测正好 300 条 = 10 页 × 30 —— 上游还有数据，
        但被我们的上限截断。静默截断会伪装成「数据不存在」。"""
        got, truncated = paginate(lambda i: page(30, True), max_pages=10)
        assert len(got) == 300
        assert truncated is True

    def test_truncation_can_be_raised_instead_of_returned(self):
        with pytest.raises(TruncatedResult, match="10 页"):
            paginate(lambda i: page(30, True), max_pages=10, strict=True)

    def test_empty_first_page_is_not_truncation(self):
        got, truncated = paginate(lambda i: page(0, False), max_pages=10)
        assert got == [] and truncated is False


@pytest.mark.live
class TestLiveCompleteness:
    def test_wens_annual_report_is_reachable_without_truncation(self):
        """修复前: 300 条上限截断，2024 年报被切掉，同比静默缺失。"""
        from ir_agent.pipeline import disclosure_dates
        from ir_agent.sources import cninfo
        anns, truncated = cninfo.search_with_status(
            "300498", date(2025, 1, 1), date(2026, 12, 31))
        periods = disclosure_dates(anns)
        assert not truncated, "仍被截断，需继续提高上限或收窄区间"
        assert "2024FY" in periods
