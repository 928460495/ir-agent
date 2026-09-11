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
    def test_待核实说清要核实什么(self):
        """这个 stage 的含义是「AI 填好了判断项，等人看一眼」——
        不是「等人来写假设」。指引要说清是哪一种。"""
        acts = next_actions(Stage.AWAITING_REVIEW, code="600519")
        assert acts and "核实" in acts[0] and "判断项" in acts[0]

    def test_说清哪些不需要人确认(self):
        """费用率、周转天数、折旧率由历史校准 —— 把它们一起推给人核实，
        等于让人去复核一堆不需要判断的东西，真正该看的两项反而被淹没。"""
        acts = next_actions(Stage.AWAITING_REVIEW, code="600519")
        txt = "\n".join(acts)
        assert "不需要你确认" in txt and "校准" in txt

    def test_指名待核实的文件(self):
        acts = next_actions(Stage.AWAITING_REVIEW, code="600519",
                            out_dir="out/x")
        txt = "\n".join(acts)
        assert "out/x/assumptions.md" in txt
        assert "out/x/assumptions.json" in txt

    def test_未出估值时给出重跑第一阶段的路(self):
        acts = next_actions(Stage.NOT_APPLICABLE, code="600519",
                            out_dir="out/x", year=2025)
        assert acts and "--analyse" in acts[0]

    def test_已建模则无阻塞(self):
        assert next_actions(Stage.VALUED, code="600519") == []


class TestNextActionsGiveRunnableCommands:
    """指引里的命令必须能直接跑。

    **这条规则救过一次，又被绕过一次。** 第一次是打出的命令缺了必需的
    --full，粘贴即失败。第二次是估值模块换成 ashare-dcf、两阶段重构之后，
    这里仍在教用户跑已被删除的 --review / --assumptions —— 而当时的测试
    断言的是「命令里含 --review」，删掉那个 flag 它照样通过。

    所以现在**不比对字符串，直接拿真实的 argparse 解析器去解**:
    删掉一个 flag，这里立刻红。
    """

    def _commands(self, stage):
        out = []
        for act in next_actions(stage, code="600519", out_dir="out/x",
                                year=2025):
            for line in act.split("\n"):
                line = line.strip()
                if line.startswith("python -m ir_agent"):
                    out.append(line)
        return out

    def test_每条命令都能被真实解析器解开(self):
        from ir_agent.__main__ import build_parser
        for stage in (Stage.AWAITING_REVIEW, Stage.NOT_APPLICABLE):
            for cmd in self._commands(stage):
                argv = cmd.split()[3:]          # 去掉 python -m ir_agent
                # **占位符要替换成实际值，不是删掉** —— 用户照做时填的是
                # 名字，不是把那一段整个抹掉。删掉会让 --approved-by 失去
                # 取值，测出来的是我造的假故障。
                argv = ["张三" if x.startswith("<") else x for x in argv]
                try:
                    build_parser().parse_args(argv)
                except SystemExit as e:          # argparse 用 SystemExit 报错
                    raise AssertionError(
                        f"指引给出的命令解析不了：{cmd}") from e

    def test_至少给出一条可跑的命令(self):
        assert self._commands(Stage.AWAITING_REVIEW)
        assert self._commands(Stage.NOT_APPLICABLE)



class TestSkippedListDistinguishesKinds:
    """产物清单里混着三种东西，标成同一个词会误导读者。

    **实测踩到**: 健全性警告「终值占 EV 78%（>75%）：估值高度依赖终值
    假设」被标成「未生成：…」—— 读者会去找什么东西没生成，而那分明是
    一条算出来了、且必须被看见的警告。
    """

    def _render(self, *items):
        from datetime import date

        from ir_agent.research import ResearchOutcome, Stage
        return ResearchOutcome(
            code="600584", period="2025FY", as_of=date(2026, 9, 7),
            stage=Stage.VALUED, skipped=list(items)).summary()

    def test_健全性警告标为警告(self):
        out = self._render("估值健全性：终值占 EV 78%（>75%）")
        assert "⚠" in out and "未生成" not in out

    def test_口径不一致标为警告(self):
        out = self._render("估值口径与账本不一致：营业收入差 3%")
        assert "⚠" in out and "未生成" not in out

    def test_人工调整标为改动(self):
        out = self._render("判断项经人工调整：营收增速 第1年 base 改为 0.30")
        assert "✎" in out and "未生成" not in out

    def test_真正的缺失仍标为未生成(self):
        assert "未生成" in self._render("战略分析（连续 3 次失败）")
