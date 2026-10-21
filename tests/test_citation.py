from datetime import date
from decimal import Decimal

import pytest

from ir_agent.citation import (
    UnresolvedReferenceError,
    audit_bare_numbers,
    render,
)
from ir_agent.ledger import Fact, FactLedger, Method


@pytest.fixture
def ledger():
    led = FactLedger()
    led.put(Fact(
        key="revenue", value=Decimal("39374532100"), unit="元", currency="CNY",
        period="2025Q3", as_of=date(2025, 10, 25),
        source_id="cninfo_a1", method=Method.REPORTED,
    ))
    led.put(Fact(
        key="gross_margin", value=Decimal("0.4213"), unit="ratio",
        period="2025Q3", as_of=date(2025, 10, 25),
        source_id="calc_1", method=Method.COMPUTED,
    ))
    return led


class TestRender:
    def test_placeholder_is_replaced_by_the_ledger_value(self, ledger):
        out, _ = render("营业收入 [[cninfo_a1#revenue@2025Q3]]。", ledger, as_of=date(2025, 12, 1))
        assert "393.75 亿元" in out
        assert "[[" not in out

    def test_ratio_unit_renders_as_percentage(self, ledger):
        out, _ = render("毛利率 [[calc_1#gross_margin@2025Q3]]。", ledger, as_of=date(2025, 12, 1))
        assert "42.13%" in out

    def test_unknown_reference_raises_rather_than_passing_through(self, ledger):
        with pytest.raises(UnresolvedReferenceError, match="net_profit"):
            render("[[cninfo_a1#net_profit@2025Q3]]", ledger, as_of=date(2025, 12, 1))

    def test_source_id_mismatch_is_rejected(self, ledger):
        """Guards against an LLM inventing a plausible-looking source id."""
        with pytest.raises(UnresolvedReferenceError, match="source_id"):
            render("[[wrong_src#revenue@2025Q3]]", ledger, as_of=date(2025, 12, 1))

    def test_lookahead_propagates_during_render(self, ledger):
        from ir_agent.ledger import LookAheadError
        with pytest.raises(LookAheadError):
            render("[[cninfo_a1#revenue@2025Q3]]", ledger, as_of=date(2025, 9, 1))

    def test_every_resolved_reference_produces_a_footnote(self, ledger):
        _, notes = render(
            "收入 [[cninfo_a1#revenue@2025Q3]]，毛利率 [[calc_1#gross_margin@2025Q3]]。",
            ledger, as_of=date(2025, 12, 1),
        )
        assert len(notes) == 2
        assert notes[0].source_id == "cninfo_a1"
        assert notes[0].as_of == date(2025, 10, 25)

    def test_repeated_reference_yields_one_footnote(self, ledger):
        _, notes = render(
            "[[cninfo_a1#revenue@2025Q3]] 与 [[cninfo_a1#revenue@2025Q3]]",
            ledger, as_of=date(2025, 12, 1),
        )
        assert len(notes) == 1


class TestBareNumberAudit:
    def test_bare_financial_number_in_draft_is_flagged(self):
        """LLM 直接写数字 = 幻觉入口，必须在渲染前拦下。"""
        hits = audit_bare_numbers("营业收入 393.75 亿元，同比增长 12%。")
        assert hits

    def test_placeholders_are_not_flagged(self):
        assert audit_bare_numbers("营业收入 [[cninfo_a1#revenue@2025Q3]]。") == []

    def test_ordinary_prose_without_figures_is_clean(self):
        assert audit_bare_numbers("公司在高端市场保持领先地位。") == []

    def test_year_and_quarter_labels_are_not_flagged(self):
        """2025年 / 2025Q3 是标签不是财务数值，误报会让审计噪音过大。"""
        assert audit_bare_numbers("2025年前三季度，公司 2025Q3 表现稳健。") == []


class TestPeriodLabelsAreNotBareNumbers:
    """期间标签不是财务数值。误报会让审计噪音大到无法使用。"""

    @pytest.mark.parametrize("label", ["2025FY", "2025H1", "2025Q1-Q3", "2025Q1"])
    def test_period_label_is_not_flagged(self, label):
        assert audit_bare_numbers(f"## 经营概览（{label}）") == []


class TestDerivedFactReferences:
    def test_a_computed_fact_placeholder_actually_resolves(self):
        """派生事实的 source_id 必须落在占位符语法允许的字符集内，
        否则正文里的 [[...]] 会原样留在成稿里。"""
        from ir_agent.operators import margins

        led = FactLedger()
        for key, val in (("revenue", "1000"), ("cost_of_revenue", "600")):
            led.put(Fact(key=key, value=Decimal(val), unit="元", currency="CNY",
                         period="2025FY", as_of=date(2026, 3, 28),
                         source_id="sina_x", method=Method.REPORTED))
        gm = margins.compute(led, "2025FY", as_of=date(2026, 4, 1))["gross_margin"]

        out, notes = render(f"毛利率 {gm.ref}。", led, as_of=date(2026, 4, 1))
        assert "[[" not in out
        assert "40.00%" in out
        assert len(notes) == 1


class TestPercentileAndCountFormatting:
    """分位数与家数是新单位，format_value 不认识就会把 Decimal 原始精度
    直接打出来（66.66666666666666666666666667 分位）。"""

    def _f(self, unit, v):
        from ir_agent.ledger import Fact, Method
        from decimal import Decimal
        from datetime import date
        return Fact(key="x", value=Decimal(v), unit=unit, currency=None,
                    period="2025FY", as_of=date(2026, 4, 20),
                    source_id="s", method=Method.COMPUTED)

    def test_percentile_is_rounded_to_whole_numbers(self):
        from ir_agent.citation import format_value
        assert format_value(self._f("分位", "66.6666666666666666")) == "67 分位"

    def test_percentile_keeps_the_unit(self):
        from ir_agent.citation import format_value
        assert format_value(self._f("分位", "100")) == "100 分位"

    def test_peer_count_is_an_integer(self):
        from ir_agent.citation import format_value
        assert format_value(self._f("家", "4")) == "4 家"

    def test_no_raw_decimal_precision_survives(self):
        from ir_agent.citation import format_value
        s = format_value(self._f("分位", "33.33333333333333333333"))
        assert len(s) < 10


class TestAuditUsesMethodNotNamingConvention:
    """审计原本靠 source_id.startswith("calc_") 判断派生事实 ——
    这是字符串前缀约定，换个前缀就漏。可比事实用 comps_ 前缀，
    于是 9 条全被误判为「缺失快照」，可溯源率从 100% 掉到 66.7%。
    正确依据是 Fact.method: COMPUTED 的溯源在 derived_from 链上，
    本来就不该有快照。"""

    def _run(self, source_id, method, confidence=1.0):
        from datetime import date
        from decimal import Decimal
        from ir_agent.audit import audit
        from ir_agent.ledger import Fact, FactLedger
        from ir_agent.sources.snapshot import SnapshotStore
        import tempfile

        led = FactLedger()
        led.put(Fact(key="roe", value=Decimal("0.3"), unit="ratio",
                     currency=None, period="2025FY", as_of=date(2026, 4, 20),
                     source_id=source_id, method=method,
                     confidence=confidence))
        with tempfile.TemporaryDirectory() as d:
            return audit("ROE [[roe@2025FY]]。", led, SnapshotStore(d),
                         as_of=date(2026, 6, 1))

    def test_computed_fact_needs_no_snapshot_whatever_the_prefix(self):
        from ir_agent.ledger import Method
        r = self._run("comps_600519_2025FY", Method.COMPUTED)
        assert r.missing_snapshots == []
        assert r.traceability == 1.0

    def test_calc_prefix_still_passes(self):
        from ir_agent.ledger import Method
        assert self._run("calc_roe_2025FY", Method.COMPUTED).missing_snapshots == []

    def test_reported_fact_without_a_snapshot_is_still_flagged(self):
        """真正该有快照却没有的，必须继续报出来。"""
        from ir_agent.ledger import Method
        r = self._run("em_g_income_xyz", Method.REPORTED)
        assert r.missing_snapshots == ["em_g_income_xyz"]

    def test_extracted_fact_from_a_pdf_still_needs_provenance(self):
        from ir_agent.ledger import Method
        # EXTRACTED 按 V0 的不变量必须带 confidence < 1.0
        r = self._run("cninfo_pdf_p148", Method.EXTRACTED, confidence=0.9)
        assert r.missing_snapshots == ["cninfo_pdf_p148"]


class TestFootnoteCarriesItsRenderedValue:
    """脚注应当知道自己渲染成了什么 —— 下游（看板回填来源提示、
    Excel 定位单元格）都需要把正文里的数字对回脚注，
    没有这个字段就只能重新算一遍，容易与正文不一致。"""

    def _notes(self):
        from datetime import date
        from decimal import Decimal
        from ir_agent.citation import render
        from ir_agent.ledger import Fact, FactLedger, Method
        l = FactLedger()
        l.put(Fact(key="roe", value=Decimal("0.3365"), unit="ratio",
                   currency=None, period="2025FY", as_of=date(2026, 4, 20),
                   source_id="calc_x", method=Method.COMPUTED))
        return render("ROE [[roe@2025FY]]。", l, as_of=date(2026, 6, 1))

    def test_display_matches_what_appears_in_the_text(self):
        out, notes = self._notes()
        assert notes[0].display in out

    def test_display_is_the_formatted_form(self):
        _, notes = self._notes()
        assert notes[0].display == "33.65%"


class TestIdentifiersAreNotBareNumbers:
    """股票代码与年份不是需要溯源的数字。

    实测招商银行首稿被驳回，违规项是「600036、2025、2025」—— 模型写
    「招商银行（600036）2025 年度…」就被判违规。代价是每次多一到两轮调用
    （招行 3 次 $0.50 vs 神华 1 次 $0.14，贵 3.5 倍），更糟的是它在训练
    模型回避提及标的本身。
    """
    @staticmethod
    def _a(text):
        from ir_agent.citation import audit_bare_numbers
        return audit_bare_numbers(text)

    def test_six_digit_stock_code_is_not_flagged(self):
        assert self._a("招商银行（600036）本期…") == []

    def test_stock_code_with_market_prefix_is_not_flagged(self):
        assert self._a("标的 sh600519 与 sz000858。") == []

    def test_汉字紧贴股票代码时豁免仍然生效(self):
        r"""**这是本类既有两条测试漏掉的形状，而它是生产里最常见的那种。**

        原先这条豁免写的是 `\b…\d{6}\b`。Python 的 `\w` 把汉字算作单词
        字符，于是「生益科技600183」里「技」与「6」之间**没有 `\b` 边界**，
        豁免整条不触发。而既有两条测试恰好都给代码加了括号或空格
        （「招商银行（600036）」「标的 sh600519 与」），边界由标点/空格
        提供，所以 `\b` 一直看着好用。

        实跑后果（out/600183-b3，2026-10-02，$7.47）：撰写正文连续 3 次
        被驳回，违规项里有「600183」——而那是标的自己的代码，模型无从
        修正（报告的主体就是这家公司），三次尝试全烧掉、整轮正文丢弃。
        """
        for text in ("生益科技600183的覆铜板业务…",
                     "600183本期产能提升…",
                     "标的为生益科技600183。",
                     "覆铜板龙头600183与竞争对手000636对比…"):
            assert self._a(text) == [], f"汉字紧贴代码时被判违规：{text!r}"

    def test_六位数的真实数值仍然要被拦下(self):
        r"""**反向。** 豁免放宽到「非数字边界」之后，不能把带单位的六位数
        一起放过——那些是真要溯源的财务数字。

        没有这条，一个把 `\d{6}` 改成「只要是六位数就豁免」的实现也能让
        上面那条通过，而那会在报告里放过「营收 123456 万元」这种裸数字。
        """
        assert self._a("营收 123456 万元") != []
        assert self._a("成本 654321 元") != []

    def test_bare_year_is_not_flagged(self):
        assert self._a("2025 年公司经营稳健。") == []

    def test_year_range_is_not_flagged(self):
        assert self._a("2021 至 2025 年间…") == []

    def test_a_real_figure_is_still_flagged(self):
        assert any("1688.38" in x for x in self._a("营业收入 1688.38 亿元。"))

    def test_a_percentage_is_still_flagged(self):
        assert self._a("毛利率 91.18%。") != []

    def test_a_plain_large_number_is_still_flagged(self):
        assert self._a("净利润 853 亿元。") != []

    def test_code_and_figure_together_flags_only_the_figure(self):
        out = self._a("招商银行（600036）营业收入 3375.32 亿元。")
        assert out == ["3375.32 亿"]      # 命中值含单位

    def test_far_future_number_is_not_treated_as_a_year(self):
        """9999 不是年份，是个数字。"""
        assert self._a("某项为 9999。") != []


class Test期间标签白名单的实测缺口:
    """这些全部来自一次真实运行被驳回的正文 —— 都是误判，不是幻觉。"""

    def test_月度期间标签整体豁免(self):
        """宏观层引入 2026-07 后踩到: 年份规则剥掉 2026 却留下 -07。"""
        assert audit_bare_numbers("CPI 同比在 2026-05、2026-06、2026-07 维持低位") == []

    def test_财年与上半年(self):
        assert audit_bare_numbers("贵州茅台2025财年延续了…") == []
        assert audit_bare_numbers("2026上半年名义GDP同比进一步回落") == []
        assert audit_bare_numbers("2025全年与2026下半年") == []

    def test_指标名称里的数字不是数值(self):
        assert audit_bare_numbers("行业CR5为高位，公司作为CR5中份额最高者") == []
        assert audit_bare_numbers("CR10 同样集中") == []

    def test_段落序号豁免(self):
        assert audit_bare_numbers("（1）行业出清节奏 （2）终端价格 (3) 杠杆结构") == []

    def test_豁免不会放过真实数值(self):
        """括号里带单位的是数值，不是序号。"""
        assert audit_bare_numbers("营收（1.2 亿元）") != []
        assert audit_bare_numbers("同比 5.4%") != []
        assert audit_bare_numbers("共 7 家样本") != []
        assert audit_bare_numbers("处于 100 分位") != []

    def test_月度豁免不吞掉相邻数值(self):
        got = audit_bare_numbers("2026-07 当月同比 3.5%")
        assert "3.5%" in " ".join(got)

    def test_指数名称不是数值(self):
        """「沪深300」的 300 是名字的一部分 —— 而白名单里没有任何占位符
        能代替一个指数的名字。实测正文写「对沪深300 的敏感度」时被驳回。"""
        assert audit_bare_numbers("β 系对沪深300 回归所得，中证500 同理") == []
        assert audit_bare_numbers("上证50 与科创50 成分") == []

    def test_期限标签不是数值(self):
        assert audit_bare_numbers("10年期国债与 30 年期国债") == []
        assert audit_bare_numbers("十年期国债到期收益率") == []

    def test_货币层次是口径名不是数值(self):
        assert audit_bare_numbers("M1−M2 剪刀差走阔，M2 同比回落") == []
        assert audit_bare_numbers("M0 与 M3 口径") == []

    def test_分位数名称不是数值(self):
        """P5 / P50 / P95 是「第 5 百分位」这个统计量的名字，与 CR5 同理。
        研报第 8 节要解读蒙特卡洛分布，不放行就只能写「第九十五百分位数」。"""
        assert audit_bare_numbers("P5 与 P95 之间，中位数（P50）偏低") == []
        assert audit_bare_numbers("龙卷风取 P10/P90 摆幅") == []

    def test_分位数豁免不放过紧跟的真实数值(self):
        assert "28.71 元" in " ".join(audit_bare_numbers("P5 为 28.71 元"))

    def test_顿号序号豁免(self):
        """中文写作里"1、2、3……"比括号序号"（1）（2）"更常见——同一条
        理由：顿号是列举标点，不是小数分隔符，真实数值必带单位。
        600584 实测：辩论环节模型用顿号列出论点优先级，连续两轮被判
        "数字是字面值，必须改写为占位符"，整轮辩论因此被丢弃、正文
        未经证伪。"""
        assert audit_bare_numbers(
            "核心逻辑：4、行业产能过剩加剧；2、下游需求持续走弱；"
            "1、成本端持续承压；5、政策面缺乏催化；3、估值仍处高位。") == []

    def test_markdown有序列表豁免(self):
        assert audit_bare_numbers("1. 行业分析\n2. 财务分析\n3. 估值") == []

    def test_顿号序号豁免不会吞掉相邻的两个真实数值(self):
        """回归锁：`3、4季度`是两个真实的季度数值并列，不是编号列表——
        顿号序号要求后面不紧跟数字，真正的列表项后面接的是文字，不是
        另一个数字。"""
        assert audit_bare_numbers("3、4季度") != []

    def test_顿号序号豁免不会吞掉小数点后的数字(self):
        """回归锁：`1.5`是小数，不能因为后面紧跟顿号就被误判成序号——
        序号要求前面不是数字或小数点。"""
        assert audit_bare_numbers("1.5、2.3两个假设区间") != []


class TestUnrenderedPlaceholdersAreCaught:
    """**没有一道闸门查「占位符有没有被替换」。**

    实测教训（600584 端到端，用户亲自读到）：`dcf.summary()` 写的是
    `[[valuation.wacc]]`——少了 `@period`，`REF_RE` 压根不认它是占位符，
    于是 `REF_RE.sub` 看都没看见，整段估值原样漏进最终交付的 report.md，
    真实数字只出现在终端。它穿过了全部四道闸门：每一道查的都是「占位符
    用得对不对」（格式合法吗、指向的事实存在吗、期间同源吗），
    **没有一道查「占位符有没有被替换」**。

    2481cde 修的是那一个产出端（补上 @period），闸门本身一直没有。
    这里补的就是闸门：`render()` 之后正文里若还留着 `[[...]]` 形状的
    东西，那就是没被替换——不管它为什么没被替换，都不能算验收通过。
    """

    def _audit(self, draft):
        import tempfile
        from datetime import date
        from ir_agent.audit import audit
        from ir_agent.ledger import FactLedger
        from ir_agent.sources.snapshot import SnapshotStore
        with tempfile.TemporaryDirectory() as d:
            return audit(draft, FactLedger(), SnapshotStore(d),
                         as_of=date(2026, 9, 12))

    def test_畸形占位符漏进正文时审计判不通过(self):
        r = self._audit("## 估值\n\n每股内在价值 [[valuation.value_per_share]]，"
                        "WACC [[valuation.wacc]]。\n")
        assert not r.ok, "整段估值都是未替换的占位符，不能判通过"
        assert "[[valuation.wacc]]" in r.unrendered
        assert "[[valuation.value_per_share]]" in r.unrendered
        assert "未替换" in r.summary()

    def test_未替换的占位符要拉低可溯源率(self):
        """`__main__` 的验收线是 `audit.traceability == 1.0`——未替换的
        占位符本来就是"没落地的数字"，不计进分母的话，一份一个数字都没
        渲染出来的研报反而是满分（实测确认过：可溯源率 100.0%）。"""
        r = self._audit("WACC [[valuation.wacc]]。")
        assert r.traceability < 1.0

    def test_正常渲染的正文不被误伤(self):
        from datetime import date
        from decimal import Decimal
        import tempfile
        from ir_agent.audit import audit
        from ir_agent.ledger import Fact, FactLedger, Method
        from ir_agent.sources.snapshot import SnapshotStore
        led = FactLedger()
        led.put(Fact(key="roe", value=Decimal("0.3"), unit="ratio",
                     currency=None, period="2025FY", as_of=date(2026, 4, 20),
                     source_id="calc_roe", method=Method.COMPUTED))
        with tempfile.TemporaryDirectory() as d:
            r = audit("ROE [[roe@2025FY]]。", led, SnapshotStore(d),
                      as_of=date(2026, 6, 1))
        assert r.unrendered == [] and r.ok and r.traceability == 1.0


def test_缺快照的来源在研报附录里也要去重():
    """实跑产出的 report.md 附录里写着「em_macro、em_macro、em_macro…」
    整整 20 个，其中 em_macro 重复 7 次、eastmoney_report 重复 13 次
    ——列表是按脚注逐条追加的，照抄只会刷屏。读者要知道的是哪几个来源、
    总共多少条（终端那条说明已经这么做了，交付文件不该比终端更难读）。"""
    from ir_agent.audit import AuditResult, Footnote
    from datetime import date
    res = AuditResult(
        rendered="正文",
        footnotes=[Footnote(marker=1, key="roe", period="2025FY",
                            source_id="em_macro", as_of=date(2026, 1, 1),
                            method="reported", display="30%")],
        missing_snapshots=["em_macro"] * 7 + ["eastmoney_report"] * 13)
    txt = res.with_sources()
    line = [x for x in txt.splitlines() if "缺少原始接口快照" in x][0]
    assert line.count("em_macro") == 1, "来源要去重"
    assert "20" in line, "要给出总条数"


def test_纯数字单位只显示数值():
    """回归的斜率与 t 值没有量纲 —— 写成「4.15x」或「4.15 count」都是错的单位。"""
    from datetime import date as _d
    from decimal import Decimal as _D

    from ir_agent.citation import format_value
    from ir_agent.ledger import Fact, Method
    f = Fact(key="valuation.rel_reg_pb_t", value=_D("4.1534"), unit="num", period="2026-10-21",
             as_of=_d(2026, 10, 21), source_id="relval", method=Method.COMPUTED)
    assert format_value(f) == "4.15"
