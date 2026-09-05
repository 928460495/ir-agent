from datetime import date, datetime
from decimal import Decimal

import pytest

from ir_agent.sources.cninfo import parse_announcements
from ir_agent.sources.quotes import ParseError, parse_tencent_quote

# 2026-09-04 从 qt.gtimg.cn 实抓（名称已解码）
TENCENT_RAW = (
    'v_sh600519="1~贵州茅台~600519~1330.00~1298.88~1295.88~45416~27411~18005~'
    '1329.82~1~1329.59~22~1329.58~1~1329.50~2~1329.49~3~'
    '1330.00~46~1330.01~2~1330.02~29~1330.03~23~1330.06~1~~'
    '20260904161433~31.12~2.40~1338.86~1295.60~1330.00/45416/6022594729~'
    '45416~602259~0.36~20.42~~1338.86~1295.60~3.33~16626.09~16626.09~6.62~'
    '1428.77~1168.99~2.06~-72~1326.11~18.67~20.20~~~0.10~602259.4729~239.4000~'
    '18~   A~GP-A~-1.42~2.51~3.91~32.41~27.30~1539.98~1151.01~4.49~1.59~6.32~"; '
)

# 2026-09-04 从 cninfo hisAnnouncement/query 实抓
CNINFO_RAW = {
    "totalRecordNum": 27,
    "announcements": [
        {
            "announcementId": "1224836346",
            "announcementTitle": "贵州茅台2025年第一次临时股东大会决议公告",
            "announcementTime": 1764345600000,
            "adjunctUrl": "finalpage/2025-11-29/1224836346.PDF",
            "secCode": "600519",
            "secName": "贵州茅台",
        },
        {
            "announcementId": "1224836351",
            "announcementTitle": "贵州茅台酒股份有限公司关联交易决策管理细则（2025年11月修订）",
            "announcementTime": 1764345600000,
            "adjunctUrl": "finalpage/2025-11-29/1224836351.PDF",
            "secCode": "600519",
            "secName": "贵州茅台",
        },
    ],
}


class TestTencentQuoteParsing:
    def test_extracts_last_price(self):
        q = parse_tencent_quote(TENCENT_RAW)
        assert q["last"] == Decimal("1330.00")

    def test_extracts_identity(self):
        q = parse_tencent_quote(TENCENT_RAW)
        assert q["code"] == "600519"
        assert q["name"] == "贵州茅台"

    def test_extracts_valuation_fields_used_downstream(self):
        q = parse_tencent_quote(TENCENT_RAW)
        assert q["pe_ttm"] == Decimal("20.42")
        assert q["pb"] == Decimal("6.62")
        assert q["market_cap"] == Decimal("16626.09") * Decimal("100000000")

    def test_quote_timestamp_is_parsed_as_the_as_of(self):
        q = parse_tencent_quote(TENCENT_RAW)
        assert q["as_of"] == datetime(2026, 9, 4, 16, 14, 33)

    def test_price_change_is_internally_consistent(self):
        """现价 - 昨收 应等于涨跌额；对不上说明字段错位。"""
        q = parse_tencent_quote(TENCENT_RAW)
        assert q["last"] - q["prev_close"] == q["change"]

    def test_empty_response_raises_rather_than_returning_blanks(self):
        with pytest.raises(ParseError):
            parse_tencent_quote('v_sh600519="";')

    def test_truncated_response_raises(self):
        with pytest.raises(ParseError, match="字段"):
            parse_tencent_quote('v_sh600519="1~贵州茅台~600519~1330.00";')


class TestCninfoAnnouncementParsing:
    def test_returns_one_record_per_announcement(self):
        anns = parse_announcements(CNINFO_RAW, source_id="cninfo_x")
        assert len(anns) == 2

    def test_announcement_time_becomes_the_disclosure_date(self):
        """这是全系统 as_of 的正确来源 —— 不是抓取日，是披露日。"""
        anns = parse_announcements(CNINFO_RAW, source_id="cninfo_x")
        assert anns[0].ann_date == date(2025, 11, 29)

    def test_pdf_url_is_absolute_and_fetchable(self):
        anns = parse_announcements(CNINFO_RAW, source_id="cninfo_x")
        assert anns[0].url.startswith("http://static.cninfo.com.cn/finalpage/")

    def test_every_record_carries_its_source_id(self):
        anns = parse_announcements(CNINFO_RAW, source_id="cninfo_x")
        assert all(a.source_id == "cninfo_x" for a in anns)

    def test_periodic_report_is_classified(self):
        raw = {"announcements": [{
            "announcementId": "1", "announcementTitle": "贵州茅台2025年年度报告",
            "announcementTime": 1764345600000, "adjunctUrl": "finalpage/a.PDF",
            "secCode": "600519", "secName": "贵州茅台",
        }]}
        assert parse_announcements(raw, source_id="s")[0].category == "定期报告"

    def test_shareholder_meeting_is_classified(self):
        anns = parse_announcements(CNINFO_RAW, source_id="s")
        assert anns[0].category == "股东大会"

    def test_unmatched_title_falls_back_to_other(self):
        raw = {"announcements": [{
            "announcementId": "9", "announcementTitle": "关于变更公司注册地址的公告",
            "announcementTime": 1764345600000, "adjunctUrl": "finalpage/b.PDF",
            "secCode": "600519", "secName": "贵州茅台",
        }]}
        assert parse_announcements(raw, source_id="s")[0].category == "其他"

    def test_annual_report_summary_is_not_counted_as_periodic_report(self):
        """摘要不是正式定期报告，混淆会让财报解析取错文件。"""
        raw = {"announcements": [{
            "announcementId": "8", "announcementTitle": "贵州茅台2025年年度报告摘要",
            "announcementTime": 1764345600000, "adjunctUrl": "finalpage/c.PDF",
            "secCode": "600519", "secName": "贵州茅台",
        }]}
        assert parse_announcements(raw, source_id="s")[0].category != "定期报告"

    def test_empty_result_set_is_not_an_error(self):
        assert parse_announcements({"announcements": None}, source_id="s") == []


@pytest.mark.live
class TestLiveSources:
    def test_tencent_quote_round_trip(self):
        from ir_agent.sources.quotes import fetch_quote
        q = fetch_quote("sh600519")
        assert q["last"] > 0
        assert q["code"] == "600519"

    def test_cninfo_search_returns_announcements(self):
        from ir_agent.sources.cninfo import search
        anns = search("600519", date(2025, 10, 1), date(2025, 11, 30))
        assert anns
        assert all(a.ann_date >= date(2025, 10, 1) for a in anns)
