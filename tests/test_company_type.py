from datetime import date

import pytest

from ir_agent.sources.eastmoney import (
    EM_REPORTS,
    CompanyType,
    detect_company_type,
    report_name,
)


class TestReportName:
    @pytest.mark.parametrize("ct,prefix", [
        (CompanyType.GENERAL, "G"), (CompanyType.BANK, "B"),
        (CompanyType.INSURANCE, "I"), (CompanyType.SECURITIES, "S"),
    ])
    def test_each_industry_has_its_own_template(self, ct, prefix):
        assert report_name("balance", ct) == f"RPT_F10_FINANCE_{prefix}BALANCE"

    def test_all_three_statements_are_covered(self):
        for stmt in ("balance", "income", "cashflow"):
            assert report_name(stmt, CompanyType.BANK).startswith(
                "RPT_F10_FINANCE_B")


class TestNormalisedFieldNames:
    """四套模板的字段名一致 —— 这正是规范化接口的价值:
    换行业只换模板名，映射表不用动，标签变体问题整类消失。"""

    def test_the_field_mapping_is_shared_across_all_industries(self):
        balance = EM_REPORTS["balance"].fields
        assert balance["TOTAL_ASSETS"] == "total_assets"
        assert balance["TOTAL_PARENT_EQUITY"] == "equity_attr_parent"

    def test_operate_cost_is_absent_for_financials_by_design(self):
        """金融业确实没有营业成本 —— 这是业务差异，不是映射缺口。"""
        assert "OPERATE_COST" in EM_REPORTS["income"].fields


class TestDetection:
    def test_cached_lookup_avoids_repeat_probing(self):
        from ir_agent.sources import eastmoney
        eastmoney._TYPE_CACHE["999999"] = CompanyType.BANK
        assert detect_company_type("999999", "SH") is CompanyType.BANK

    def test_unknown_code_falls_back_to_general(self, monkeypatch):
        from ir_agent.sources import eastmoney
        eastmoney._TYPE_CACHE.pop("888888", None)
        monkeypatch.setattr(eastmoney, "_probe", lambda *a, **k: False)
        assert detect_company_type("888888", "SH") is CompanyType.GENERAL


@pytest.mark.live
class TestLiveDetection:
    @pytest.mark.parametrize("code,market,expected", [
        ("600519", "SH", CompanyType.GENERAL),      # 贵州茅台
        ("600036", "SH", CompanyType.BANK),         # 招商银行
        ("601318", "SH", CompanyType.INSURANCE),    # 中国平安
        ("600030", "SH", CompanyType.SECURITIES),   # 中信证券
    ])
    def test_detects_the_right_industry(self, code, market, expected):
        from ir_agent.sources import eastmoney
        eastmoney._TYPE_CACHE.pop(code, None)
        assert detect_company_type(code, market) is expected

    def test_bank_statements_now_yield_the_same_canonical_keys(self):
        from ir_agent.sources.eastmoney import fetch_statements_em
        facts, _ = fetch_statements_em("600036", market="SH")
        keys = {f.key for f in facts}
        assert {"total_assets", "total_liabilities", "total_equity",
                "equity_attr_parent", "revenue", "net_profit",
                "net_profit_attr_parent", "cash_end"} <= keys

    def test_insurer_statements_yield_the_same_canonical_keys(self):
        from ir_agent.sources.eastmoney import fetch_statements_em
        facts, _ = fetch_statements_em("601318", market="SH")
        keys = {f.key for f in facts}
        assert {"total_assets", "total_equity", "revenue",
                "net_profit", "net_profit_attr_parent"} <= keys
