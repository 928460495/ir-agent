"""年报 PDF 提取器 —— 两源分歧的裁决依据。

背景: 中国神华 2025FY，东财给 903,830 百万且标注 NOTICE_DATE=2026-03-31，
但 2026-03-31 发布的年报原文写的是 627,761 百万（新浪与之一致）。
东财供的是**重述后**数字却挂了**原始公告日** —— 数据源层面内建的未来函数。
遇到这类分歧，只有回到公告原文才能裁定。
"""
from decimal import Decimal

import pytest

from ir_agent.sources.annual_pdf import (
    ExtractionFailed,
    column_years,
    extract_rows,
    parse_amount,
    parse_unit,
)

D = Decimal

# 中国神华 2025 年报第 148 页实抓文本（合并资产负债表(续)）
P148 = """中国神华能源股份有限公司2025 年度报告
148
中国神华能源股份有限公司
合并资产负债表(续)
2025 年12 月31 日
(金额单位：人民币百万元)
附注
2025 年
12 月31 日
2024 年
12 月31 日
(已重述）
非流动资产合计
480,792
460,883
资产总计
627,761
668,022
后附的财务报表附注为本财务报表的组成部分。"""


class TestParseUnit:
    @pytest.mark.parametrize("text,mult", [
        ("(金额单位：人民币百万元)", D("1000000")),
        ("单位：百万元", D("1000000")),
        ("金额单位：人民币千元", D("1000")),
        ("单位：元", D("1")),
    ])
    def test_recognises_scale(self, text, mult):
        assert parse_unit(text) == mult

    def test_missing_unit_raises_rather_than_assuming_yuan(self):
        """猜错量级会让数字差 6 个数量级，且三表勾稽照样通过。"""
        with pytest.raises(ExtractionFailed, match="单位"):
            parse_unit("中国神华能源股份有限公司")


class TestParseAmount:
    @pytest.mark.parametrize("raw,val", [
        ("627,761", D("627761")),
        ("1,234.56", D("1234.56")),
        ("(1,234)", D("-1234")),        # 中文财报用括号表示负数
        ("（1,234）", D("-1234")),       # 全角括号
        ("-89", D("-89")),
        ("0", D("0")),
    ])
    def test_formats(self, raw, val):
        assert parse_amount(raw) == val

    @pytest.mark.parametrize("raw", ["附注", "", "2025 年", "—", "-"])
    def test_non_amounts_return_none(self, raw):
        assert parse_amount(raw) is None


class TestColumnYears:
    def test_reads_both_comparative_years_in_order(self):
        assert column_years(P148) == [2025, 2024]

    def test_restatement_marker_does_not_add_a_column(self):
        assert len(column_years(P148)) == 2


class TestExtractRows:
    def test_finds_total_assets_for_the_current_year(self):
        rows = extract_rows(P148, ["total_assets"])
        assert rows["total_assets"][2025] == D("627761") * D("1000000")

    def test_prior_year_is_keyed_by_its_own_year(self):
        rows = extract_rows(P148, ["total_assets"])
        assert rows["total_assets"][2024] == D("668022") * D("1000000")

    def test_label_variants_map_to_the_same_key(self):
        """年报用「归属于本公司股东权益合计」，接口用「归属于母公司…」。"""
        text = P148.replace("资产总计", "归属于本公司股东权益合计")
        rows = extract_rows(text, ["equity_attr_parent"])
        assert rows["equity_attr_parent"][2025] == D("627761") * D("1000000")

    def test_absent_label_is_omitted_not_zeroed(self):
        rows = extract_rows(P148, ["total_liabilities"])
        assert "total_liabilities" not in rows

    def test_parent_company_statement_is_refused(self):
        """母公司报表与合并报表数值不同，混用会造成量级错误。"""
        text = P148.replace("合并资产负债表(续)", "母公司资产负债表")
        with pytest.raises(ExtractionFailed, match="母公司"):
            extract_rows(text, ["total_assets"])


class TestAdjudication:
    def test_pdf_value_identifies_the_correct_source(self):
        from ir_agent.validate.adjudicate import adjudicate
        verdict = adjudicate(
            pdf_value=D("627761000000"),
            candidates={"eastmoney": D("903830000000"),
                        "sina": D("627761000000.00")},
        )
        assert verdict.winner == "sina"
        assert verdict.losers == ["eastmoney"]

    def test_no_source_matching_the_pdf_is_reported_as_such(self):
        from ir_agent.validate.adjudicate import adjudicate
        v = adjudicate(pdf_value=D("100"),
                       candidates={"a": D("200"), "b": D("300")})
        assert v.winner is None
        assert set(v.losers) == {"a", "b"}

    def test_tolerance_absorbs_rounding_from_the_millions_unit(self):
        """年报以百万元为单位，接口以元 —— 末位差异不该判为不一致。"""
        from ir_agent.validate.adjudicate import adjudicate
        v = adjudicate(pdf_value=D("627761000000"),
                       candidates={"sina": D("627761400000")},
                       rel_tol=D("0.00001"))
        assert v.winner == "sina"


@pytest.mark.live
class TestLiveShenhua:
    def test_extracts_the_annual_report_figures(self, tmp_path):
        from ir_agent.sources.annual_pdf import fetch_and_extract
        got = fetch_and_extract(
            "http://static.cninfo.com.cn/finalpage/2026-03-31/1225064293.PDF",
            keys=["total_assets", "total_liabilities", "total_equity"],
            cache_dir=tmp_path)
        assert got["total_assets"][2025] == D("627761") * D("1000000")
        assert got["total_liabilities"][2025] == D("146310") * D("1000000")

    def test_the_pdf_sides_with_sina_not_eastmoney(self, tmp_path):
        from ir_agent.sources.annual_pdf import fetch_and_extract
        from ir_agent.validate.adjudicate import adjudicate
        got = fetch_and_extract(
            "http://static.cninfo.com.cn/finalpage/2026-03-31/1225064293.PDF",
            keys=["total_assets"], cache_dir=tmp_path)
        v = adjudicate(pdf_value=got["total_assets"][2025],
                       candidates={"eastmoney": D("903830000000"),
                                   "sina": D("627761000000.00")})
        assert v.winner == "sina"


class TestBothSourcesCorrect:
    """两源都与原文一致时不该有「输家」。
    实测中神华的利润表两源完全相同，却被报成「eastmoney 正确；sina 偏离 0.00%」
    —— 读者会以为新浪出了问题。"""

    def test_agreeing_sources_produce_no_losers(self):
        from ir_agent.validate.adjudicate import adjudicate
        v = adjudicate(pdf_value=D("100"),
                       candidates={"eastmoney": D("100"), "sina": D("100")})
        assert v.losers == []

    def test_describe_says_both_agree(self):
        from ir_agent.validate.adjudicate import adjudicate
        v = adjudicate(pdf_value=D("100"),
                       candidates={"eastmoney": D("100"), "sina": D("100")})
        assert "均与原文一致" in v.describe()

    def test_genuine_loser_is_still_named(self):
        from ir_agent.validate.adjudicate import adjudicate
        v = adjudicate(pdf_value=D("100"),
                       candidates={"eastmoney": D("200"), "sina": D("100")})
        assert v.losers == ["eastmoney"] and v.winner == "sina"
