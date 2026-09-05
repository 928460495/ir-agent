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
