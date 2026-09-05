"""不同发行人的报表与公告结构差异 —— 只用制造业标的验证是不够的。"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.pipeline import _compose, disclosure_dates
from ir_agent.skips import SkipLog, SkipReason
from ir_agent.sources.cninfo import Announcement
from ir_agent.sources.financials import normalize_label

D = Decimal


def ann(title, d=date(2026, 3, 28)):
    return Announcement(ann_id="1", title=title, ann_date=d, url="u",
                        code="600036", name="招商银行",
                        category="定期报告", source_id="s")


class TestAnnualReportTitleVariants:
    @pytest.mark.parametrize("title", [
        "招商银行股份有限公司2025年度报告",      # 银行常用: 只有一个「年」
        "贵州茅台2025年年度报告",                # 制造业常用
        "某公司2025年度报告（更新后）",
    ])
    def test_both_annual_report_spellings_are_recognised(self, title):
        assert "2025FY" in disclosure_dates([ann(title)])

    def test_summary_of_the_short_form_is_still_excluded(self):
        assert disclosure_dates([ann("招商银行股份有限公司2025年度报告摘要")]) == {}

    def test_basel_third_pillar_report_is_not_a_periodic_report(self):
        """「第三支柱报告」是银行的资本充足率披露，不是定期财报。"""
        assert disclosure_dates([ann("招商银行2025年半年度第三支柱报告")]) == {}

    def test_dividend_announcement_is_not_a_periodic_report(self):
        for t in ["招商银行2025年半年度A股分红派息实施公告",
                  "招商银行2025年半年度利润分配方案公告"]:
            assert disclosure_dates([ann(t)]) == {}


class TestLineLabelNormalisation:
    """CAS 报表行标签带中文序号前缀，且各行业写法不同。"""

    @pytest.mark.parametrize("raw,clean", [
        ("一、营业收入", "营业收入"),          # 保险/银行
        ("一、营业总收入", "营业总收入"),      # 制造业
        ("五、净利润", "净利润"),
        ("六、期末现金及现金等价物余额", "期末现金及现金等价物余额"),
        ("加:期初现金及现金等价物余额", "期初现金及现金等价物余额"),
        ("营业收入", "营业收入"),              # 无前缀时原样返回
    ])
    def test_prefixes_are_stripped(self, raw, clean):
        assert normalize_label(raw) == clean

    def test_inner_punctuation_is_preserved(self):
        assert normalize_label("其中:分保费收入") == "其中:分保费收入"


class TestInsurerStatements:
    def test_insurance_revenue_label_maps_to_revenue(self):
        from ir_agent.sources.financials import Statement, parse_sina_table, to_facts
        tsv = ("报表日期\t20251231\t\n单位\t元\t\n"
               "一、营业收入\t1000000\t\n二、营业支出\t800000\t\n"
               "五、净利润\t150000\t\n")
        facts = to_facts({Statement.PROFIT: parse_sina_table(tsv)},
                         as_of_by_period={"2025FY": date(2026, 3, 28)},
                         source_id="s")
        assert {f.key for f in facts} >= {"revenue", "net_profit"}


class TestComposeDegradesPerSection:
    """银行/保险没有营业成本，毛利率必然缺失。
    一个字段缺失不该杀掉整份报告 —— 分段容错，缺哪段记哪段。"""

    def _led(self, **extra):
        base = dict(revenue=1000, net_profit=150, net_profit_attr_parent=140,
                    total_assets=10000, total_liabilities=9000,
                    total_equity=1000, equity_attr_parent=950, cash_end=500)
        base.update(extra)
        l = FactLedger()
        for k, v in base.items():
            l.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                       period="2025FY", as_of=date(2026, 3, 28),
                       source_id="s", method=Method.REPORTED))
        return l

    def test_missing_gross_margin_does_not_kill_the_report(self):
        skips = SkipLog()
        out = _compose("2025FY", None, self._led(), date(2026, 4, 1), skips)
        assert "资产负债结构" in out          # 后续章节仍然生成
        assert "经营概览" in out

    def test_the_missing_section_is_recorded_as_a_skip(self):
        skips = SkipLog()
        _compose("2025FY", None, self._led(), date(2026, 4, 1), skips)
        assert any("毛利" in e.what for e in skips.entries)

    def test_missing_cost_does_not_appear_as_a_blank_in_prose(self):
        skips = SkipLog()
        out = _compose("2025FY", None, self._led(), date(2026, 4, 1), skips)
        assert "营业成本 。" not in out and "[[" in out


class TestEquityLabelAliases:
    """三家写法三个样 —— 精确匹配注定在跨行业时失配。"""

    @pytest.mark.parametrize("label", [
        "所有者权益(或股东权益)合计",     # 制造业
        "股东权益合计",                    # 招商银行
        "所有者权益合计",                  # 中国平安
    ])
    def test_total_equity_aliases(self, label):
        from ir_agent.sources.financials import Statement, parse_sina_table, to_facts
        tsv = f"报表日期\t20251231\t\n单位\t元\t\n{label}\t1000\t\n"
        facts = to_facts({Statement.BALANCE: parse_sina_table(tsv)},
                         as_of_by_period={"2025FY": date(2026, 3, 28)},
                         source_id="s")
        assert [f.key for f in facts] == ["total_equity"]

    @pytest.mark.parametrize("label", [
        "归属于母公司股东权益合计",
        "归属于母公司股东的权益",
        "归属于母公司的股东权益合计",
    ])
    def test_parent_equity_aliases(self, label):
        from ir_agent.sources.financials import Statement, parse_sina_table, to_facts
        tsv = f"报表日期\t20251231\t\n单位\t元\t\n{label}\t900\t\n"
        facts = to_facts({Statement.BALANCE: parse_sina_table(tsv)},
                         as_of_by_period={"2025FY": date(2026, 3, 28)},
                         source_id="s")
        assert [f.key for f in facts] == ["equity_attr_parent"]

    def test_an_alias_never_overwrites_an_earlier_canonical_hit(self):
        """同一张表若同时出现两种写法，取值必须稳定，不能看行序。"""
        from ir_agent.sources.financials import Statement, parse_sina_table, to_facts
        tsv = ("报表日期\t20251231\t\n单位\t元\t\n"
               "所有者权益合计\t1000\t\n股东权益合计\t1000\t\n")
        facts = to_facts({Statement.BALANCE: parse_sina_table(tsv)},
                         as_of_by_period={"2025FY": date(2026, 3, 28)},
                         source_id="s")
        assert {f.value for f in facts} == {D("1000")}


class TestRunGuardsOperators:
    """算子调用也要容错。银行没有营业成本，margins 会抛 KeyError ——
    在 run() 里裸抛会让整轮以一个无信息量的 KeyError 收场。"""

    def test_margins_missing_cost_is_recorded_not_raised(self):
        from ir_agent.operators import margins
        l = FactLedger()
        for k, v in (("revenue", 1000), ("net_profit", 150)):
            l.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                       period="2025FY", as_of=date(2026, 3, 28),
                       source_id="s", method=Method.REPORTED))
        out = margins.compute(l, "2025FY", as_of=date(2026, 4, 1))
        assert "gross_margin" not in out and "net_margin" in out


class TestShapePatternsGeneralise:
    """穷举别名会一直追着新发行人的写法跑。别名之外再加一层形状正则，
    对**没见过的**变体也能命中 —— 下面这些写法都不在别名表里。"""

    def _key_for(self, label, stmt_name="BALANCE"):
        from ir_agent.sources.financials import Statement, parse_sina_table, to_facts
        stmt = getattr(Statement, stmt_name)
        tsv = f"报表日期\t20251231\t\n单位\t元\t\n{label}\t900\t\n"
        facts = to_facts({stmt: parse_sina_table(tsv)},
                         as_of_by_period={"2025FY": date(2026, 3, 28)},
                         source_id="s")
        return facts[0].key if facts else None

    @pytest.mark.parametrize("label", [
        "归属于母公司所有者的净利润",     # 茅台
        "归属于母公司的净利润",           # 招商银行
        "归属于母公司股东的净利润",       # 中国平安
        "归属于母公司普通股东的净利润",   # 未测过的变体
    ])
    def test_parent_net_profit_shapes(self, label):
        assert self._key_for(label, "PROFIT") == "net_profit_attr_parent"

    @pytest.mark.parametrize("label", [
        "归属于母公司股东权益合计",
        "归属于母公司所有者权益",         # 未测过的变体
    ])
    def test_parent_equity_shapes(self, label):
        assert self._key_for(label) == "equity_attr_parent"

    def test_shape_pattern_does_not_swallow_unrelated_lines(self):
        """综合收益、少数股东损益都含相近字样，不能被归母净利润吃掉。"""
        assert self._key_for("归属于母公司所有者的综合收益总额", "PROFIT") is None
        assert self._key_for("少数股东损益", "PROFIT") == "minority_interest_profit"

    def test_exact_alias_still_wins_over_the_pattern(self):
        assert self._key_for("净利润", "PROFIT") == "net_profit"


class TestSectionGranularity:
    """毛利率与杜邦不该绑在一段。银行没有毛利率，但 ROE 恰恰是它最核心的
    指标 —— 粗粒度分段会让一个不适用的字段带走一个关键结论。"""

    def _led_no_cost(self):
        l = FactLedger()
        vals = dict(revenue=1000, net_profit=150, net_profit_attr_parent=140,
                    total_assets=10000, total_liabilities=9000,
                    total_equity=1000, equity_attr_parent=950, cash_end=500,
                    net_margin=D("0.14"), roe=D("0.147"),
                    asset_turnover=D("0.1"), equity_multiplier=D("10.5"))
        for k, v in vals.items():
            l.put(Fact(key=k, value=D(str(v)),
                       unit="ratio" if k in ("net_margin", "roe") else "元",
                       currency=None if k in ("net_margin", "roe") else "CNY",
                       period="2025FY", as_of=date(2026, 3, 28),
                       source_id="s", method=Method.REPORTED))
        return l

    def test_roe_survives_a_missing_gross_margin(self):
        skips = SkipLog()
        out = _compose("2025FY", None, self._led_no_cost(), date(2026, 4, 1), skips)
        assert "ROE" in out

    def test_gross_margin_absence_is_still_recorded(self):
        skips = SkipLog()
        _compose("2025FY", None, self._led_no_cost(), date(2026, 4, 1), skips)
        assert any("毛利" in e.what for e in skips.entries)
