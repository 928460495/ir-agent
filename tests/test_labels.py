"""人读文件里的中文名（spec 2026-11-25-readable-layout §4.2）。"""
import pytest

from ir_agent.labels import driver_label, fact_label, source_label


@pytest.mark.parametrize("sid,name", [
    ("sec_companyfacts_2026_x", "SEC 财报数据"), ("sec_xbrl_instance_2026", "SEC 财报附注数据"),
    ("eastmoney_report_1", "东方财富研报"), ("em_g_income_x", "东方财富财报"), ("em_macro_1", "东方财富宏观"),
    ("fred_cp_1", "美联储 FRED"), ("calc_roe_2025FY", "账本计算"), ("comps_NVDA_2026FY", "同业可比"),
    ("industry_agg", "行业聚合"), ("industry_profile", "行业画像"), ("relval", "相对估值"),
    ("ashare_dcf", "三表 DCF"), ("us_dcf", "三表 DCF"), ("fmp_quote_1", "FMP"), ("xx_1", "xx_1"), ("", "")])
def test_来源中文名最长前缀(sid, name):
    assert source_label(sid) == name


def test_事实中文名():
    assert fact_label("industry.cr5") == "行业 CR5"
    assert fact_label("payable_days.percentile").endswith("同业分位")
    assert fact_label("payable_days.peer_median").endswith("同业中位数")
    assert fact_label("no.such.key") == "" and fact_label("") == ""


def test_驱动因子中文名():
    assert driver_label("revenue_growth") == "收入增速" and driver_label("wacc") == "折现率" and driver_label("x") == "x"
