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


def test_审计未通过不该被标成未生成():
    """本仓库已经为这件事写过一次注释：「产物清单里混着三种东西，标成
    同一个词会误导读者。实测: 健全性警告「终值占 EV 78%」被标成「未生成」
    —— 读者会去找什么东西没生成，而那分明是一条算出来了、且必须被看见
    的警告。」

    审计未通过是同一种东西：研报**生成了**，只是其中 20 条引用反查不到
    原始快照。实跑（600584 第二阶段复跑）里它被打成
    「– 未生成：完整研报审计未通过：…」，正是那条注释说的误导。"""
    from ir_agent.research import _mark
    assert _mark("审计未通过（完整研报）：20 条引用缺少原始接口快照") == "  ⚠ "
    assert _mark("研究简报") == "  – 未生成："


def test_锚定覆盖标成警示而不是未生成():
    from ir_agent.research import _mark
    assert _mark("判断项被锚定覆盖：首年被锚定已披露中期实际覆盖") == "  ⚠ "


def test_判断项产出了但有保留不该标成未生成():
    """**贵州茅台全流程实跑（600519，第一阶段）。** 同一份输出里两条互相
    矛盾的断言:

        ✓ DCF 判断项（待核实）：out/600519-t1/assumptions.md
        – 未生成：DCF 判断项（5 行依据未能逐字核验（已标注，未阻断，需人工复核））

    那条消息自己写着「未阻断」，却被打成「未生成」。它是在
    assumptions.md **写盘成功之后**产生的保留意见。

    难点在于 `_propose_assumptions` 有四条返回路径，全以「DCF 判断项（」
    开头: 三条是真失败（校准失败 / AssumeFailed / 调用失败），它们标成
    「未生成」是对的。所以修法不是把这个前缀注册进白名单——那会把三条真
    失败一起降级成警告——而是让成功路径用一个**自己说清状态**的前缀。

    这是 `_PREFIX` 白名单兜底的第 9 个实例。兜底值是「未生成」，而它是
    最强的那个断言；凡是没注册的条目都会被它说成「这东西没产出」。
    """
    from ir_agent.research import _mark

    assert _mark("判断项待复核：5 行依据未能逐字核验（已标注，未阻断，"
                 "需人工复核）") == "  ⚠ "
    # 三条真失败必须仍然是「未生成」——它们确实没产出判断项
    for real in ("DCF 判断项（无简报正文，假设无从追溯到分析结论）",
                 "DCF 判断项（校准失败：未找到 ashare-dcf）",
                 "DCF 判断项（ClaudeCliError: 连接中断）"):
        assert _mark(real) == "  – 未生成：", f"真失败被降级了：{real}"


def test_判断项保留意见的文案由纯函数产出():
    """钉住「产出了但有保留」这条消息本身，而不只是它的前缀 —— 前缀对了
    而消息没发出来，终端照样什么都看不到。"""
    from ir_agent.full import _assumption_caveat_note

    class _Row:
        def __init__(self, bad=""):
            self.basis_unverified = bad

    class _A:
        degraded = ""
        rows = [_Row("依据未逐字核验"), _Row(), _Row("同上")]

    note = _assumption_caveat_note(_A())
    assert note.startswith("判断项待复核：")
    assert "2 行" in note and "未阻断" in note

    class _Clean:
        degraded = ""
        rows = [_Row(), _Row()]

    assert _assumption_caveat_note(_Clean()) == ""



# ══ 第一阶段拆三段（Task 12）══════════════════════════════════

def test_四个阶段各给出可粘贴的下一条命令():
    """`next_actions` 的 docstring 写着「过时的指引是会主动误导人的那种错」。
    四次调用的流程里，它必须按产物推断当前停在哪道门。"""
    from ir_agent.research import Stage, next_actions

    for stage, flag in ((Stage.AWAITING_SOURCES, "--brief"),
                        (Stage.AWAITING_BRIEF_REVIEW, "--assume"),
                        (Stage.AWAITING_ASSUME_REVIEW, "--value")):
        joined = "\n".join(next_actions(stage, "600183", out_dir="out/x",
                                        year=2025))
        assert flag in joined, f"{stage} 没给出下一条命令"
        assert "600183" in joined and "--year 2025" in joined


def test_三道门的签名要求各自出现在指引里():
    from ir_agent.research import Stage, next_actions

    for stage, sig in ((Stage.AWAITING_SOURCES, "--verified-by"),
                       (Stage.AWAITING_BRIEF_REVIEW, "--reviewed-by"),
                       (Stage.AWAITING_ASSUME_REVIEW, "--approved-by")):
        assert sig in "\n".join(
            next_actions(stage, "600183", out_dir="out/x", year=2025))


def test_指引里的命令都能被真实解析器接受():
    """**不比对字符串，拿真实解析器去解析指引里的每一条命令。**

    `build_parser` 单独成函数正是为此：此前靠比对字符串守，删掉 --review
    之后测试照样过，而打给用户的命令已经跑不动了。
    """
    import shlex

    from ir_agent.__main__ import build_parser
    from ir_agent.research import Stage, next_actions

    p = build_parser()
    for stage in (Stage.AWAITING_SOURCES, Stage.AWAITING_BRIEF_REVIEW,
                  Stage.AWAITING_ASSUME_REVIEW):
        for act in next_actions(stage, "600183", out_dir="out/x", year=2025):
            for line in act.splitlines():
                line = line.strip()
                if line.startswith("python -m ir_agent"):
                    argv = shlex.split(line.replace("<你的名字>", "xhl"))[3:]
                    p.parse_args(argv)          # 解析失败会 SystemExit


def test_第二次与第三次调用未完成时教人重跑自己那一次(tmp_path):
    from ir_agent.research import Stage, next_actions

    b = "\n".join(next_actions(Stage.INCOMPLETE, "600183", out_dir=str(tmp_path),
                               year=2025, phase="brief"))
    a = "\n".join(next_actions(Stage.INCOMPLETE, "600183", out_dir=str(tmp_path),
                               year=2025, phase="assume"))
    assert "--brief" in b and "--analyse" not in b, "教人重跑了第一次调用"
    assert "--assume" in a and "--analyse" not in a

    (tmp_path / "stance_b.template.json").write_text("{}", encoding="utf-8")
    b2 = "\n".join(next_actions(Stage.INCOMPLETE, "600183", out_dir=str(tmp_path),
                                year=2025, phase="brief"))
    assert "--accept-manual" in b2 and "stance_b.template.json" in b2


def test_判断项审核停下时教人以底稿改好再跑第二阶段(tmp_path):
    """子项目三：审核 3 轮不过 → 停下交人工，终端给两条做法：原样重跑 --assume；
    以 assumptions_failed.json 为底稿改好存为 assumptions.json 再跑 --value。"""
    import shlex

    from ir_agent.__main__ import build_parser
    from ir_agent.research import (ASSUMPTIONS_FAILED_JSON, Stage, _mark,
                                   next_actions)

    (tmp_path / ASSUMPTIONS_FAILED_JSON).write_text("{}", encoding="utf-8")
    acts = next_actions(Stage.INCOMPLETE, "600183", out_dir=str(tmp_path),
                        year=2025, phase="assume")
    txt = "\n".join(acts)
    assert "--assume" in txt and "--value" in txt and ASSUMPTIONS_FAILED_JSON in txt
    assert "判断项未经审核" in txt
    p = build_parser()
    for line in txt.splitlines():
        line = line.strip()
        if line.startswith("python -m ir_agent"):
            p.parse_args(shlex.split(line.replace("<你的名字>", "xhl"))[3:])
    assert _mark("判断项审核 3 轮未通过（最后一轮致命 2 条）").strip() == "⚠"
    assert _mark("判断项未经审核：人工修改过 3 格").strip() == "⚠"


def test_新前缀都注册进了白名单且失败与成功分开():
    """`_PREFIX` 的兜底是「未生成」—— 最强的那个断言，所以每个新生产者都要
    来登记。**而且登记对了还不够，符号也要对。** 计划原稿的这条测试只断言
    「不是『未生成』」，于是「多空辩论B 未完成」被登记成「·」（信息性）也能
    过 —— 一次失败被渲染成一条普通说明。
    """
    from ir_agent.research import _mark

    warn = ("多空辩论A 未完成（裁决者三次给不出区间内的平衡值）",
            "多空辩论B 未完成 —— 需重跑或人工补写",
            "多空辩论B 已有产物不可用（…）—— 请修正",
            "来源清单：[公司公告] x：清单列出的本地文件不存在",
            "方法论预研：2 个辩论主题未进入分解式（份额、政策）")
    info = ("多空辩论A：由 xhl 人工补写",
            "多空辩论B：沿用已有产物（要重抽加 --refresh-stance）")
    for s in warn:
        assert _mark(s) == "  ⚠ ", f"失败/待核项没标成 ⚠：{s} → {_mark(s)!r}"
    for s in info:
        assert _mark(s) == "  · ", f"成功路径被标成了别的：{s} → {_mark(s)!r}"
    # 预研**失败**仍落默认分支（「未生成」是对的：分解式确实没产出）
    assert _mark("方法论预研（连续 3 次给不出闭合的分解式）") == "  – 未生成："


def test_停在门一门二时摘要说清停在哪():
    from datetime import date
    from decimal import Decimal

    from ir_agent.research import ResearchOutcome, Stage

    for stage, word in ((Stage.AWAITING_SOURCES, "门①"),
                        (Stage.AWAITING_BRIEF_REVIEW, "门②")):
        o = ResearchOutcome(code="600183", period="2025FY",
                            as_of=date(2026, 10, 12), stage=stage,
                            cost_usd=Decimal(0))
        assert word in o.summary(), stage


def test_财经媒体级来源的警告不被标成未生成():
    """生益科技 2026-10-13 试跑时，这条警告在门① 的终端输出里印成了
    「– 未生成：依据引用财经媒体级来源 2 处……」。"""
    from ir_agent.research import _mark
    s = "依据引用财经媒体级来源 2 处，核验通过但分级规则是「仅作线索，不得作为依据。」"
    assert _mark(s) == "  ⚠ "


def test_门三指引不再说改完会重新校验():
    """子项目三：第二阶段不再按内容拒绝人批准的判断项，只比对取值指纹、标注
    「判断项未经审核」；只有算不下去才停。指引若还说「改完仍会重新校验（三档须
    严格拉开…）」，就是在教人一件已经不存在的事。"""
    from ir_agent.research import Stage, next_actions
    txt = "\n".join(next_actions(Stage.AWAITING_ASSUME_REVIEW, "600183",
                                 assumptions_path="out/x/assumptions.md",
                                 out_dir="out/x", year=2025))
    assert "重新校验" not in txt and "三档须严格拉开" not in txt
    assert "未经审核" in txt
