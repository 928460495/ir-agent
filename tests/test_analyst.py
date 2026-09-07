"""分析师 Agent —— V1 第一步: 正文从模板换成 LLM 撰写。

契约不变，因此模型**物理上编不了数字**:
  · 模型可以看到数值（否则说不出任何有分析含量的话），
    但输出里的数字位必须写占位符 [[source#key@period]]；
  · 裸数字由 audit_bare_numbers 拦截；
  · 伪造的占位符由 render() 抛 UnresolvedReferenceError 拦截；
  · 违规不是警告而是**驳回重写**，把具体违规点回传给模型；
  · 重试用尽则**报错，绝不出报告** —— 宁可没有，不要一份掺假的。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.analyst import (
    AnalystRefused,
    FactCatalog,
    build_prompt,
    write_body,
)
from ir_agent.ledger import Fact, FactLedger, Method

D = Decimal
AS_OF = date(2026, 6, 1)


def led() -> FactLedger:
    l = FactLedger()
    for k, v in [("revenue", 1000), ("net_profit", 200),
                 ("gross_margin", "0.35"), ("roe", "0.18")]:
        l.put(Fact(key=k, value=D(str(v)),
                   unit="ratio" if k in ("gross_margin", "roe") else "元",
                   currency=None if k in ("gross_margin", "roe") else "CNY",
                   period="2025FY", as_of=date(2026, 4, 20),
                   source_id="em_x", method=Method.REPORTED))
    return l


@pytest.fixture
def catalog():
    return FactCatalog.from_ledger(led(), period="2025FY", as_of=AS_OF)


class TestFactCatalog:
    def test_lists_every_available_fact_as_a_token(self, catalog):
        """给分析师的是简写 —— source_id 含时间戳，冗长且跨运行失效，
        而它本就是账本的职责，不该占用分析师的注意力。"""
        assert "[[revenue@2025FY]]" in catalog.tokens

    def test_tokens_do_not_leak_source_ids(self, catalog):
        assert not any("#" in t for t in catalog.tokens)

    def test_carries_values_so_the_model_can_reason(self, catalog):
        """不给数值，模型说不出「毛利率高于同业」这类话，只能堆砌占位符。"""
        assert "35.00%" in catalog.render_for_prompt()

    def test_carries_human_labels_not_just_keys(self, catalog):
        assert "营业收入" in catalog.render_for_prompt()

    def test_token_for_a_missing_key_is_absent(self, catalog):
        assert not any("total_assets" in t for t in catalog.tokens)


class TestPrompt:
    def test_prompt_states_the_placeholder_rule(self, catalog):
        p = build_prompt(catalog, code="600519", period="2025FY")
        assert "[[" in p and "占位符" in p

    def test_prompt_includes_the_catalog(self, catalog):
        assert "营业收入" in build_prompt(catalog, code="600519", period="2025FY")

    def test_prompt_forbids_writing_literal_numbers(self, catalog):
        p = build_prompt(catalog, code="600519", period="2025FY")
        assert "不得" in p or "禁止" in p

    def test_comps_are_included_when_provided(self, catalog):
        p = build_prompt(catalog, code="600519", period="2025FY",
                         comps_summary="毛利率 100 分位")
        assert "100 分位" in p


class TestWriteBodyEnforcesTheContract:
    def _client(self, *responses):
        seen = []

        def c(prompt: str) -> str:
            seen.append(prompt)
            return responses[min(len(seen) - 1, len(responses) - 1)]
        c.seen = seen
        return c

    GOOD = ("## 经营概览\n报告期营业收入 [[em_x#revenue@2025FY]]，"
            "净利润 [[em_x#net_profit@2025FY]]。\n")

    def test_clean_output_is_accepted(self, catalog):
        body, attempts = write_body(catalog, self._client(self.GOOD))
        assert attempts == 1
        assert "[[em_x#revenue@2025FY]]" in body

    def test_bare_number_triggers_a_rewrite(self, catalog):
        bad = "## 经营概览\n营业收入 1000 元。\n"
        c = self._client(bad, self.GOOD)
        body, attempts = write_body(catalog, c)
        assert attempts == 2
        assert "1000" not in body

    def test_the_rewrite_prompt_names_the_offending_numbers(self, catalog):
        bad = "营业收入 1688 亿元。"
        c = self._client(bad, self.GOOD)
        write_body(catalog, c)
        assert "1688" in c.seen[1]

    def test_fabricated_placeholder_triggers_a_rewrite(self, catalog):
        """模型可能编出账本里没有的引用 —— 这比裸数字更隐蔽。"""
        bad = "营业收入 [[em_x#revenue@2099FY]]。"
        c = self._client(bad, self.GOOD)
        _, attempts = write_body(catalog, c)
        assert attempts == 2

    def test_the_rewrite_prompt_names_the_bad_reference(self, catalog):
        bad = "营收 [[em_x#ebitda@2025FY]]。"
        c = self._client(bad, self.GOOD)
        write_body(catalog, c)
        assert "ebitda" in c.seen[1]

    def test_exhausted_retries_refuse_rather_than_ship(self, catalog):
        """宁可没有报告，不要一份掺假的。"""
        c = self._client("营业收入 1000 元。")
        with pytest.raises(AnalystRefused, match="3 次"):
            write_body(catalog, c, max_attempts=3)

    def test_refusal_reports_what_kept_failing(self, catalog):
        c = self._client("营业收入 1000 元。")
        with pytest.raises(AnalystRefused, match="1000"):
            write_body(catalog, c, max_attempts=2)

    def test_percent_signs_in_prose_are_not_bare_numbers(self, catalog):
        ok = "毛利率 [[em_x#gross_margin@2025FY]]，处于同业前列。\n"
        body, attempts = write_body(catalog, self._client(ok))
        assert attempts == 1

    def test_empty_output_is_refused(self, catalog):
        with pytest.raises(AnalystRefused):
            write_body(catalog, self._client("   "), max_attempts=2)


class TestCatalogLegibility:
    """模型看 key 猜不出含义，也就写不出像样的中文。
    实跑茅台时发现 ps_ttm / revenue_ttm / net_profit_attr_parent_ttm 三个
    key 没有中文标签，直接以英文原样进入 prompt。"""

    def test_every_key_the_pipeline_produces_has_a_label(self):
        from ir_agent.analyst import LABELS
        produced = {
            "revenue", "revenue_total", "cost_of_revenue", "net_profit",
            "net_profit_attr_parent", "minority_interest_profit",
            "total_assets", "total_liabilities", "total_equity",
            "equity_attr_parent", "minority_equity",
            "cash_begin", "cash_end", "cash_net_change", "cf_net_profit",
            "gross_margin", "net_margin", "roe", "asset_turnover",
            "equity_multiplier", "revenue.yoy", "net_profit.yoy",
            "market_cap", "last_price", "pb", "pe_ttm", "ps_ttm",
            "revenue_ttm", "net_profit_ttm", "net_profit_attr_parent_ttm",
        }
        assert not (produced - set(LABELS)), f"缺标签: {sorted(produced - set(LABELS))}"

    def test_duplicate_ttm_keys_are_collapsed(self):
        """net_profit_ttm 是 net_profit_attr_parent_ttm 的别名，
        两条都摆进清单会让模型以为是两个不同指标。"""
        from ir_agent.ledger import Fact, Method
        l = FactLedger()
        for k in ("net_profit_ttm", "net_profit_attr_parent_ttm"):
            l.put(Fact(key=k, value=D("100"), unit="元", currency="CNY",
                       period="2025FY", as_of=date(2026, 4, 20),
                       source_id="calc_x", method=Method.COMPUTED))
        cat = FactCatalog.from_ledger(l, period="2025FY", as_of=AS_OF)
        assert sum(1 for e in cat.entries if "ttm" in e.key) == 1


class Test驳回信息要能被执行:
    def _cat(self):
        from datetime import date
        from decimal import Decimal
        from ir_agent.ledger import Fact, FactLedger, Method
        led = FactLedger()
        led.put(Fact(key="industry.peer_count", value=Decimal(19), unit="家",
                     period="2025FY", as_of=date(2025, 1, 1),
                     source_id="t", method=Method.COMPUTED))
        return FactCatalog.from_ledger(led, "2025FY", date(2026, 9, 7))

    def test_能对上账本的裸数字直接给出占位符(self):
        """只说「19 是裸数字」，模型得自己在几十条事实里找；指出占位符一轮就改对。"""
        from ir_agent.analyst import _violations
        v = _violations("行业共 19 家，[[industry.peer_count@2025FY]]", self._cat())
        assert any("应写作 [[industry.peer_count@2025FY]]" in x for x in v)

    def test_对不上的仍原样报出(self):
        from ir_agent.analyst import _violations
        v = _violations("毛利率 91.18%，[[industry.peer_count@2025FY]]", self._cat())
        assert any("91.18%" in x and "应写作" not in x for x in v)


class TestReportStructure:
    """报告结构必须给每个已接入的模块留位置。

    此前产业链、护城河/五力、一致预期只是提示词末尾的附加材料，
    1–8 节里没有任何一节提到它们 —— 模型被告知「这里有些材料」，
    却没被告知「要写成一节」。跑长电时才发现这个缺口。
    """

    def _sections(self):
        import re
        from ir_agent.analyst import _RULES
        return [ln for ln in _RULES.split("\n") if re.match(r"^\d+\. ", ln)]

    def test_九节且编号连续(self):
        secs = self._sections()
        assert len(secs) == 9
        assert [s.split(".")[0] for s in secs] == [str(i) for i in range(1, 10)]

    def test_每个模块都有归属章节(self):
        from ir_agent.analyst import _RULES
        for kw in ("产业链", "护城河", "五力", "一致预期", "关键不确定性"):
            assert kw in _RULES, f"结构里没有 {kw} 的位置"

    def test_产业链要为五力提供因果(self):
        """产业链不是背景介绍 —— 它要解释议价能力从何而来。
        两节各写各的，等于把因和果拆开摆着。"""
        from ir_agent.analyst import _RULES
        assert "产业链不是背景介绍" in _RULES and "提供因果" in _RULES

    def test_一致预期只作对照(self):
        from ir_agent.analyst import _RULES
        assert "不得" in _RULES and "反过来用一致预期" in _RULES

    def test_引号规则写进了写作要求(self):
        """长电那一版是被出处核验挡死的，直接原因之一就是模型给术语加了
        引号。事前说清比事后驳回省一轮。"""
        from ir_agent.analyst import _RULES
        assert "引号只用于逐字引述" in _RULES

    def test_假设可追溯的层级引用与章节号一致(self):
        from ir_agent.analyst import _RULES
        assert "第 8 节的每一条假设" in _RULES and "第 2–7 节" in _RULES


class TestCosmeticViolationsDoNotStarveTheReport:
    """排版问题不该毙掉整篇报告。

    **实测踩到。** 加上「占位符后重复写单位」的检查之后重跑长电，模型在
    重写过程中反而伪造了一个占位符，三次耗尽，最终一个字都没产出 ——
    而上一轮至少出了一份带「17 分位分位」的完整报告。为错字阻断整篇报告
    是比例失当，与之前出处闸门那次是同一个毛病。
    """

    def _cat(self):
        from datetime import date
        from decimal import Decimal
        from ir_agent.analyst import FactCatalog
        from ir_agent.ledger import Fact, FactLedger, Method
        led = FactLedger()
        led.put(Fact(key="gross_margin.percentile", value=Decimal("17"),
                     unit="分位", period="2025FY", as_of=date(2025, 1, 1),
                     source_id="t", method=Method.COMPUTED))
        return FactCatalog(ledger=led, as_of=date(2026, 9, 7))

    def test_单位重复被归为排版类(self):
        from ir_agent.analyst import is_cosmetic
        assert is_cosmetic("以下占位符后重复写了单位（渲染值已自带单位）：…")

    def test_伪造占位符不是排版类(self):
        from ir_agent.analyst import is_cosmetic
        assert not is_cosmetic("以下引用在事实账本中不存在，属于伪造：…")
        assert not is_cosmetic("以下数字是字面值，必须改写为占位符：…")

    def test_最后一轮只剩排版问题则收下(self):
        from ir_agent.analyst import write_body
        bad = "毛利率处于同业[[gross_margin.percentile@2025FY]]分位。"
        calls = []

        def client(p):
            calls.append(p)
            return bad

        body, n = write_body(self._cat(), client, max_attempts=3)
        assert body == bad and n == 3
        assert len(calls) == 3, "前两轮仍应驳回重写，只是最后一轮收下"

    def test_仍有实质违规则照常拒绝(self):
        from ir_agent.analyst import AnalystRefused, write_body
        bad = "引用[[company.made_up@2025FY]]。"
        with pytest.raises(AnalystRefused):
            write_body(self._cat(), lambda p: bad, max_attempts=3)


class TestHintMustNotMisdirect:
    """错误的指认比没有指认更糟。

    **实测踩到，代价是整篇报告三次都没产出。** 正文写「β 明显高于 1」——
    那个 1 是 β 的定义基准（市场组合的 β 恒为 1），账本里不可能有。而
    company.industry_rank 显示为「1 名」，数字部分一样，于是驳回信息成了
    「1（应写作 [[company.industry_rank@2025FY]]）」，等于让模型写
    「β 高于行业排名」。照做荒谬、不照做违规，两条路都不通。
    """

    def _cat(self):
        from datetime import date
        from decimal import Decimal
        from ir_agent.analyst import FactCatalog
        from ir_agent.ledger import Fact, FactLedger, Method
        led = FactLedger()
        for k, v, u in (("company.industry_rank", "1", "名"),
                        ("beta", "1.58", "x"),
                        ("gross_margin", "0.1415", "ratio")):
            led.put(Fact(key=k, value=Decimal(v), unit=u, period="2025FY",
                         as_of=date(2025, 1, 1), source_id="t",
                         method=Method.COMPUTED))
        return FactCatalog.from_ledger(led, "2025FY", date(2026, 9, 7))

    def test_光秃秃的个位整数不给指认(self):
        from ir_agent.analyst import _NO_MATCH, _hint
        out = _hint("1", self._cat())
        assert "industry_rank" not in out
        assert _NO_MATCH in out

    def test_长说明只汇总一次(self):
        """每个数字后面都跟一段同样的长解释，驳回信息会变成一堵墙 ——
        实测四个裸数字后面跟了四遍同一段话，反而看不清要改什么。"""
        from ir_agent.analyst import _NO_MATCH_NOTE, FactCatalog, _violations
        from ir_agent.ledger import FactLedger
        cat = FactCatalog(ledger=FactLedger(), as_of=date(2026, 9, 7))
        msgs = _violations("参见 4 与 6 与 8。[[a#b@c]]", cat)
        joined = "\n".join(msgs)
        assert joined.count(_NO_MATCH_NOTE) == 1

    def test_带单位的仍然给出正确指认(self):
        from ir_agent.analyst import _hint
        assert "[[company.industry_rank@2025FY]]" in _hint("1 名", self._cat())
        assert "[[beta@2025FY]]" in _hint("1.58", self._cat())
        assert "[[gross_margin@2025FY]]" in _hint("14.15%", self._cat())

    def test_数字撞上但单位对不上时不指认(self):
        """1.58x 与 1.58% 不是同一个东西。"""
        from ir_agent.analyst import _hint
        assert "beta" not in _hint("1.58%", self._cat())

    def test_规则里讲清了定义基准值的写法(self):
        from ir_agent.analyst import _RULES
        assert "定义上的基准值用文字表述" in _RULES
        assert "β 明显高于市场平均水平" in _RULES
