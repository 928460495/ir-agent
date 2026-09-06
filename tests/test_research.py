"""端到端编排 —— 一条命令产出所有能自动产出的东西，并在审核门前停下。

**自动化必须诚实地停在人工审核门前。** 假设未经审核不得建模，这是设计约束
而非未完成的功能。因此完整流程分两段:

  第一段（全自动）  数据 → 路由 → 撰写 → 辩论 → 修订 → 看板 / Excel
                    并写出**待审核的假设模板**
  ——— 人工审核假设 ———
  第二段            建模 → 估值段落补入看板与 Excel

产物清单必须**显式列出缺什么**: 只给已完成的部分，读者会以为报告是完整的。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.research import Stage, ResearchOutcome, next_actions

D = Decimal


class TestOutcomeReportsWhatIsMissing:
    def _o(self, **kw):
        base = dict(code="600519", period="2025FY", as_of=date(2026, 6, 1),
                    stage=Stage.AWAITING_REVIEW, artifacts={}, skipped=[],
                    upheld=0, cost_usd=D("0"))
        base.update(kw)
        return ResearchOutcome(**base)

    def test_awaiting_review_is_not_reported_as_complete(self):
        assert self._o().complete is False

    def test_valued_stage_is_complete(self):
        assert self._o(stage=Stage.VALUED).complete is True

    def test_artifacts_are_listed(self):
        o = self._o(artifacts={"看板": "d.html", "工作簿": "m.xlsx"})
        assert "d.html" in o.summary() and "m.xlsx" in o.summary()

    def test_summary_states_the_valuation_is_absent(self):
        """产物齐全但没有估值 —— 必须说出来，否则读者以为报告完整。"""
        assert "未建模" in self._o().summary() or "未估值" in self._o().summary()

    def test_upheld_challenges_are_surfaced(self):
        assert "3" in self._o(upheld=3).summary()

    def test_cost_is_surfaced(self):
        assert "1.35" in self._o(cost_usd=D("1.35")).summary()


class TestNextActions:
    def test_awaiting_review_tells_the_user_to_review(self):
        acts = next_actions(Stage.AWAITING_REVIEW, code="600519")
        assert acts and "审核" in acts[0]

    def test_awaiting_review_names_the_assumptions_file(self):
        acts = next_actions(Stage.AWAITING_REVIEW, code="600519",
                            assumptions_path="out/a.yaml")
        assert any("out/a.yaml" in a for a in acts)

    def test_valued_stage_has_no_blocking_action(self):
        assert next_actions(Stage.VALUED, code="600519") == []

    def test_route_excludes_dcf_says_so(self):
        """路由判定不适用 DCF 时，「去审核假设」是错误的指引。"""
        acts = next_actions(Stage.NOT_APPLICABLE, code="600519")
        assert acts and ("不适用" in acts[0] or "PB" in acts[0])


class TestAssumptionTemplate:
    def test_template_lists_every_required_key(self):
        from ir_agent.research import assumption_template
        t = assumption_template(years=5)
        for k in ("wacc", "terminal_growth", "growth_rates"):
            assert k in t

    def test_template_leaves_values_blank(self):
        """预填一个数字会让人直接点通过 —— 模板要逼人填。"""
        from ir_agent.research import assumption_template
        t = assumption_template(years=5)
        assert "null" in t or "TODO" in t or "待填" in t

    def test_template_has_a_basis_slot_per_key(self):
        from ir_agent.research import assumption_template
        t = assumption_template(years=5)
        assert t.count("basis") >= 1 and "url" in t

    def test_template_states_the_basis_requirement(self):
        from ir_agent.research import assumption_template
        assert "抓取日期" in assumption_template(years=5)

    def test_growth_path_matches_the_year_count(self):
        from ir_agent.research import assumption_template
        assert assumption_template(years=3).count("- year:") == 3


class TestNextActionsGiveRunnableCommands:
    """指引里的命令必须能直接跑。

    实测第一版打出的是
        python -m ir_agent 600519 --year <年度> --assumptions ... --reviewer ...
    缺了必需的 --full，粘贴即失败 —— 指引给一条跑不通的命令，
    比不给指引更糟。
    """

    def _acts(self):
        return next_actions(Stage.AWAITING_REVIEW, code="600519",
                            assumptions_path="out/600519/assumptions.yaml",
                            out_dir="out/600519", year=2025)

    def test_interactive_path_is_offered_first(self):
        """交互式录入不必手写 YAML，应作为首选给出。"""
        assert "--review" in self._acts()[0]

    def test_every_command_includes_the_required_full_flag(self):
        cmds = [a for a in self._acts() if "python -m ir_agent" in a]
        assert cmds and all("--full" in c for c in cmds)

    def test_commands_carry_a_concrete_year_not_a_placeholder(self):
        cmds = [a for a in self._acts() if "python -m ir_agent" in a]
        assert all("<年度>" not in c and "--year 2025" in c for c in cmds)

    def test_manual_yaml_path_is_still_offered(self):
        assert any("--assumptions" in a for a in self._acts())

    def test_reviewer_flag_is_present_in_both(self):
        cmds = [a for a in self._acts() if "python -m ir_agent" in a]
        assert all("--reviewer" in c for c in cmds)
