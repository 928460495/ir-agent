from datetime import date

import pytest

from ir_agent.pipeline import disclosure_dates
from ir_agent.sources.cninfo import Announcement


def ann(title, d):
    return Announcement(ann_id="1", title=title, ann_date=d, url="u",
                        code="000858", name="五粮液",
                        category="定期报告", source_id="s")


class TestTitleVariants:
    """实测标题（五粮液 000858, 2026-09 抓取）暴露出原正则过窄。"""

    @pytest.mark.parametrize("title,period", [
        ("2025年年度报告", "2025FY"),
        ("2025年半年度报告", "2025H1"),
        ("2025年第一季度报告", "2025Q1"),
        ("2025年一季度报告", "2025Q1"),          # 五粮液用这种写法
        ("2026年一季度报告", "2026Q1"),
        ("2025年第三季度报告", "2025Q1-Q3"),
        ("2025年三季度报告", "2025Q1-Q3"),
    ])
    def test_common_title_forms_are_recognised(self, title, period):
        assert period in disclosure_dates([ann(title, date(2026, 4, 30))])

    @pytest.mark.parametrize("suffix", ["（更新前）", "（更新后）", "（更正后）", "(修订版)"])
    def test_revision_suffixes_still_match(self, suffix):
        got = disclosure_dates([ann(f"2025年第三季度报告{suffix}", date(2026, 4, 30))])
        assert "2025Q1-Q3" in got

    def test_summary_is_still_excluded(self):
        assert disclosure_dates([ann("2025年年度报告摘要", date(2026, 4, 30))]) == {}

    def test_english_version_is_excluded(self):
        """英文版与中文版同内容，重复计入会干扰披露日判定。"""
        assert disclosure_dates([ann("2025年度报告（英文版）", date(2026, 5, 23))]) == {}

    def test_notice_about_a_report_is_not_the_report(self):
        for t in ["关于延期披露2025年度报告及2026年第一季度报告的公告",
                  "2025年年度报告披露的提示性公告"]:
            assert disclosure_dates([ann(t, date(2026, 4, 22))]) == {}


class TestRestatementSemantics:
    """数据源给的是**重述后**的数字。配上最早披露日等于凭空获得信息优势。"""

    def test_latest_version_date_wins_not_earliest(self):
        got = disclosure_dates([
            ann("2025年半年度报告（更新前）", date(2025, 8, 28)),
            ann("2025年半年度报告（更新后）", date(2026, 4, 30)),
        ])
        assert got["2025H1"] == date(2026, 4, 30)

    def test_order_of_announcements_does_not_matter(self):
        got = disclosure_dates([
            ann("2025年半年度报告（更新后）", date(2026, 4, 30)),
            ann("2025年半年度报告（更新前）", date(2025, 8, 28)),
        ])
        assert got["2025H1"] == date(2026, 4, 30)

    def test_single_filing_uses_its_own_date(self):
        got = disclosure_dates([ann("2025年年度报告", date(2026, 4, 30))])
        assert got["2025FY"] == date(2026, 4, 30)


class TestForeignLanguageVariants:
    """英文版与中文版同内容，重复计入会污染披露日。
    泸州老窖实测: 中文年报 2026-04-29、英文版 2026-05-16，
    因排除规则只写了「英文版」而漏掉「（英文）」，披露日被推后 17 天 ——
    在可比分析里会导致该公司被误判为「尚未披露」而移出样本。"""

    @pytest.mark.parametrize("title", [
        "2025年年度报告（英文版）",
        "2025年年度报告（英文）",
        "2025年年度报告(英文)",
        "2025年度报告（英文版）",
        "2025 Annual Report",
        "2025年年度报告（H股）",
    ])
    def test_non_chinese_editions_are_excluded(self, title):
        assert disclosure_dates([ann(title, date(2026, 5, 16))]) == {}

    def test_the_chinese_edition_still_sets_the_date(self):
        got = disclosure_dates([
            ann("2025年年度报告", date(2026, 4, 29)),
            ann("2025年年度报告（英文）", date(2026, 5, 16)),
        ])
        assert got["2025FY"] == date(2026, 4, 29)
