"""年报 PDF 提取扩展到利润表与现金流量表。

资产负债表的格式最规整，另两张表有三个额外陷阱（均来自中国神华 2025 年报实抓）:
  1. 标签与数值之间插了**附注编号列**（'五、35'、'五、47(1)'），
     按「标签后连续数字行」扫描会一个都取不到；
  2. 标签带前缀（'一、营业收入'、'减：营业成本'、'加：年初现金…'）；
  3. **现金流量净额的标签随正负变化** —— 增加时叫「净增加额」，
     减少时叫「净减少额」，只认一个会在半数公司上失配。
"""
from decimal import Decimal

import pytest

from ir_agent.sources.annual_pdf import (
    ExtractionFailed,
    StatementKind,
    column_years,
    detect_statement,
    extract_rows,
    is_note_ref,
    normalize_pdf_label,
)

D = Decimal
M = D("1000000")

INCOME = """中国神华能源股份有限公司
合并利润表
2025 年度
(金额单位：人民币百万元)
附注
2025 年度
2024 年度
(已重述）
一、营业收入
五、35
294,916
339,788
减：营业成本
五、35
191,465
223,923
四、净利润
62,783
65,999"""

CASHFLOW = """中国神华能源股份有限公司
合并现金流量表 (续)
2025 年度
(金额单位：人民币百万元)
附注
2025 年度
2024 年度
五、现金及现金等价物净减少额
五、47(1)
(43,125)
(43,515)
加：年初现金及现金等价物余额
66,413
109,928
六、年末现金及现金等价物余额
五、47(2)
23,288
66,413"""


class TestNoteReferences:
    @pytest.mark.parametrize("s", ["五、35", "六、1", "五、47(1)", "十、2"])
    def test_note_refs_are_recognised(self, s):
        assert is_note_ref(s) is True

    @pytest.mark.parametrize("s", ["一、营业收入", "四、净利润", "294,916", "附注"])
    def test_labels_and_amounts_are_not_note_refs(self, s):
        """判别关键: 「、」后面是数字才是附注，是中文就是行标签。"""
        assert is_note_ref(s) is False


class TestLabelNormalisation:
    @pytest.mark.parametrize("raw,clean", [
        ("一、营业收入", "营业收入"),
        ("减：营业成本", "营业成本"),
        ("四、净利润", "净利润"),
        ("加：年初现金及现金等价物余额", "年初现金及现金等价物余额"),
        ("六、年末现金及现金等价物余额", "年末现金及现金等价物余额"),
        ("营业收入", "营业收入"),
    ])
    def test_prefixes_stripped(self, raw, clean):
        assert normalize_pdf_label(raw) == clean

    def test_inner_colon_is_preserved(self):
        assert normalize_pdf_label("其中：利息费用") == "其中：利息费用"


class TestStatementDetection:
    @pytest.mark.parametrize("text,kind", [
        ("合并资产负债表", StatementKind.BALANCE),
        ("合并利润表", StatementKind.INCOME),
        ("合并现金流量表 (续)", StatementKind.CASHFLOW),
    ])
    def test_recognises_each_statement(self, text, kind):
        assert detect_statement(text) is kind

    @pytest.mark.parametrize("text", ["母公司利润表", "母公司现金流量表"])
    def test_parent_company_statements_are_refused(self, text):
        assert detect_statement(text) is None


class TestSummaryPagesAreRejected:
    """年报末尾的「五年财务摘要」列顺序是 2021→2025（从旧到新），
    而主表是 2025,2024（从新到旧）。按位置取值会在摘要页上取到 2021 年 ——
    中国神华实测: 归母净利润被取成 69,709 百万（2021 年），
    而当年净利润才 62,783，归母大于净利润，在会计上不可能。
    改为**按年份取值**后，列顺序怎么排都不会错。"""

    SUMMARY = """合并利润表
单位：百万元
2021 年
2022 年
2023 年
2024 年
2025 年
净利润
81,738
67,660
65,999
70,000
62,783"""

    def test_values_are_keyed_by_year_not_position(self):
        rows = extract_rows(self.SUMMARY, ["net_profit"])
        assert rows["net_profit"][2025] == D("62783") * M
        assert rows["net_profit"][2021] == D("81738") * M

    def test_the_same_key_from_a_summary_and_a_statement_agree_on_the_year(self):
        a = extract_rows(INCOME, ["net_profit"])["net_profit"][2025]
        b = extract_rows(self.SUMMARY, ["net_profit"])["net_profit"][2025]
        assert a == b


class TestColumnYears:
    def test_annual_header_uses_niandu_not_nian(self):
        """年报表头是「2025 年度」，资产负债表是「2025 年」—— 两种都要认。"""
        assert column_years(INCOME) == [2025, 2024]


class TestIncomeExtraction:
    def test_revenue_skips_the_note_reference_column(self):
        rows = extract_rows(INCOME, ["revenue"])
        assert rows["revenue"][2025] == D("294916") * M

    def test_prior_year_column_captured(self):
        assert extract_rows(INCOME, ["revenue"])["revenue"][2024] == D("339788") * M

    def test_cost_label_carries_a_jian_prefix(self):
        rows = extract_rows(INCOME, ["cost_of_revenue"])
        assert rows["cost_of_revenue"][2025] == D("191465") * M

    def test_row_without_a_note_reference_still_works(self):
        rows = extract_rows(INCOME, ["net_profit"])
        assert rows["net_profit"][2025] == D("62783") * M

    def test_balance_sheet_keys_are_not_matched_in_the_income_statement(self):
        assert extract_rows(INCOME, ["total_assets"]) == {}


class TestCashflowExtraction:
    def test_net_decrease_label_is_recognised_as_net_change(self):
        """减少时标签是「净减少额」，只认「净增加额」会在半数公司失配。"""
        rows = extract_rows(CASHFLOW, ["cash_net_change"])
        assert rows["cash_net_change"][2025] == D("-43125") * M

    def test_beginning_balance_uses_nianchu_in_annual_reports(self):
        rows = extract_rows(CASHFLOW, ["cash_begin"])
        assert rows["cash_begin"][2025] == D("66413") * M

    def test_ending_balance_skips_its_note_reference(self):
        rows = extract_rows(CASHFLOW, ["cash_end"])
        assert rows["cash_end"][2025] == D("23288") * M

    def test_cash_identity_holds_on_extracted_values(self):
        r = extract_rows(CASHFLOW, ["cash_net_change", "cash_begin", "cash_end"])
        assert r["cash_begin"][2025] + r["cash_net_change"][2025] == r["cash_end"][2025]

    def test_income_keys_do_not_leak_into_cashflow(self):
        """「净利润」在两张表都出现，必须按表区分，否则跨表勾稽自己跟自己比。"""
        assert extract_rows(CASHFLOW, ["net_profit"]) == {}


@pytest.mark.live
class TestLiveAllStatements:
    URL = "http://static.cninfo.com.cn/finalpage/2026-03-31/1225064293.PDF"

    def test_extracts_across_all_three_statements(self, tmp_path):
        from ir_agent.sources.annual_pdf import fetch_and_extract
        got = fetch_and_extract(
            self.URL, cache_dir=tmp_path,
            keys=["total_assets", "revenue", "net_profit", "cash_end"])
        assert got["total_assets"][2025] == D("627761") * M
        assert got["revenue"][2025] == D("294916") * M
        assert got["net_profit"][2025] == D("62783") * M
        assert got["cash_end"][2025] == D("23288") * M

    def test_pdf_sides_with_sina_on_the_income_statement_too(self, tmp_path):
        """神华 2025H1 的分歧就出现在收入与净利润上，此前裁不了。"""
        from ir_agent.sources.annual_pdf import fetch_and_extract
        from ir_agent.validate.adjudicate import adjudicate
        got = fetch_and_extract(self.URL, keys=["revenue"], cache_dir=tmp_path)
        v = adjudicate(got["revenue"][2025],
                       {"sina": D("294916000000"), "eastmoney": D("380000000000")})
        assert v.winner == "sina"
