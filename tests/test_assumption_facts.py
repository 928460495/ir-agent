"""假设入账本 —— 让假设可被引用、可被质疑、可被审计。

WACC 9% 不在账本里，质疑者引用它就会触发裸数字驳回，假设因此无法进入辩论。
把假设写成 ESTIMATED 事实解决这个问题，同时带来三个好处:

  · 质疑者可以用 [[wacc@2025FY]] 正常引用它
  · 脚注里 estimated 与 reported 明显区分 —— 读者一眼看出哪些数字是**假设**
  · confidence < 1.0 沿着派生链传播: 用假设算出的估值，置信度不会是 1
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import FactLedger, Method
from ir_agent.valuation.assumptions import Assumptions
from ir_agent.valuation.basis import Basis
from ir_agent.valuation.facts import assumption_facts

D = Decimal
AS_OF = date(2026, 6, 1)


def make():
    ext = lambda u, q: Basis.external(u, q, date(2026, 6, 1))
    return Assumptions(
        wacc=D("0.09"), terminal_growth=D("0.025"),
        growth_rates=[D("0.03"), D("0.04")],
        basis={"wacc": [ext("https://x.cn/gz", "十年期国债 2.5%")],
               "terminal_growth": [ext("https://x.cn/gdp", "名义 GDP 5%")],
               "growth_rates": [ext("https://x.cn/i", "行业增速 4%")]})


class TestAssumptionFacts:
    def test_wacc_enters_the_ledger(self):
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        assert l.get("wacc", "2025FY", as_of=AS_OF).value == D("0.09")

    def test_terminal_growth_enters_the_ledger(self):
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        assert l.get("terminal_growth", "2025FY", as_of=AS_OF).value == D("0.025")

    def test_each_forecast_year_is_addressable(self):
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        assert l.get("growth_y1", "2025FY", as_of=AS_OF).value == D("0.03")
        assert l.get("growth_y2", "2025FY", as_of=AS_OF).value == D("0.04")

    def test_marked_estimated_not_reported(self):
        """假设不是事实。脚注里必须一眼看出区别。"""
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        assert l.get("wacc", "2025FY", as_of=AS_OF).method is Method.ESTIMATED

    def test_confidence_is_below_one(self):
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        assert l.get("wacc", "2025FY", as_of=AS_OF).confidence < 1.0

    def test_source_id_points_at_the_basis(self):
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        assert "assum" in l.get("wacc", "2025FY", as_of=AS_OF).source_id

    def test_ratio_unit_so_it_renders_as_percent(self):
        from ir_agent.citation import format_value
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        assert format_value(l.get("wacc", "2025FY", as_of=AS_OF)) == "9.00%"

    def test_catalog_exposes_them_to_the_analyst(self):
        from ir_agent.analyst import FactCatalog
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        cat = FactCatalog.from_ledger(l, "2025FY", as_of=AS_OF)
        assert "[[wacc@2025FY]]" in cat.tokens

    def test_labels_are_chinese(self):
        from ir_agent.analyst import FactCatalog
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        cat = FactCatalog.from_ledger(l, "2025FY", as_of=AS_OF)
        assert "折现率" in cat.render_for_prompt()


class TestDebateCoversAssumptions:
    def _cat(self):
        from ir_agent.analyst import FactCatalog
        l = FactLedger()
        assumption_facts(make(), l, "2025FY", AS_OF)
        return FactCatalog.from_ledger(l, "2025FY", as_of=AS_OF)

    def _client(self, *rs):
        seen = []

        def c(p):
            seen.append(p)
            return rs[min(len(seen) - 1, len(rs) - 1)]
        c.seen = seen
        return c

    RAW = ("[挑战 1] 类型：假设不当\n"
           "原文：WACC 取 [[wacc@2025FY]]\n"
           "质疑：依据仅给出无风险利率，β 与股权风险溢价无任何来源。")

    def test_assumption_kind_is_recognised(self):
        from ir_agent.debate import ChallengeKind, parse_challenges
        assert parse_challenges(self.RAW)[0].kind is ChallengeKind.ASSUMPTION

    def test_assumptions_appear_in_the_challenge_context(self):
        from ir_agent.debate import run_debate
        c = self._client("正文未见明显问题。")
        run_debate(self._cat(), draft="某正文。", client=c,
                   assumptions=make())
        assert "折现率" in c.seen[0] or "WACC" in c.seen[0]

    def test_basis_is_shown_so_it_can_be_attacked(self):
        from ir_agent.debate import run_debate
        c = self._client("正文未见明显问题。")
        run_debate(self._cat(), draft="某正文。", client=c, assumptions=make())
        assert "十年期国债" in c.seen[0]

    def test_challenger_is_told_to_check_assumptions(self):
        from ir_agent.debate import run_debate
        c = self._client("正文未见明显问题。")
        run_debate(self._cat(), draft="某正文。", client=c, assumptions=make())
        assert "假设" in c.seen[0]

    def test_without_assumptions_context_is_unchanged(self):
        from ir_agent.debate import run_debate
        c = self._client("正文未见明显问题。")
        run_debate(self._cat(), draft="某正文。", client=c)
        assert "估值假设" not in c.seen[0]


class TestPromptItselfObeysTheContract:
    """**我们塞进 prompt 的数字也必须是占位符。**

    实测辩论中止于「裸数字：3.0%、3.0%、4.0%」—— 假设区把增速打成了字面值，
    模型照抄回来就被判违规。往 prompt 里塞裸数字再惩罚模型引用它，
    是自相矛盾的契约。
    """

    def test_assumption_block_uses_placeholders_for_values(self):
        from ir_agent.debate import _assumption_block
        out = _assumption_block(make(), period="2025FY")
        assert "[[wacc@2025FY]]" in out
        assert "9.00%" not in out

    def test_growth_path_uses_per_year_placeholders(self):
        from ir_agent.debate import _assumption_block
        out = _assumption_block(make(), period="2025FY")
        assert "[[growth_y1@2025FY]]" in out and "[[growth_y2@2025FY]]" in out

    def test_our_own_assertions_carry_no_bare_numbers(self):
        """我们**断言**的取值必须是占位符；依据里的数字是外部**引文**，
        保留原文才有核验价值 —— 两者性质不同。"""
        from ir_agent.citation import audit_bare_numbers
        from ir_agent.debate import _assumption_block
        block = _assumption_block(make(), period="2025FY")
        ours = "\n".join(l for l in block.splitlines() if "依据：" not in l)
        assert audit_bare_numbers(ours) == []

    def test_basis_text_is_still_shown(self):
        """依据里的数字来自外部原文摘录，是引文不是我们的断言 —— 保留。"""
        from ir_agent.debate import _assumption_block
        assert "十年期国债" in _assumption_block(make(), period="2025FY")

    def test_quoted_external_evidence_keeps_its_numbers(self):
        """外部引文若被改写成占位符，就失去了「原文说了什么」的核验价值。"""
        from ir_agent.debate import _assumption_block
        assert "2.5%" in _assumption_block(make(), period="2025FY")
