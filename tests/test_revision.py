"""辩论结论回写 —— 让证伪真正落到正文上。

挑战成立却不改正文，辩论就只是留痕。但回写有两个必须防的失败模式:

  · **改了等于没改** —— 模型口头接受却原样保留被质疑的句子。
    因此加机械核验: 成立挑战针对的片段不得在修订稿中原样存在。
  · **过度删除** —— 把所有被质疑的地方连同可支撑的分析一起删光，
    得到一篇正确但空洞的报告。因此要求逻辑错误**修正**、
    无据断言**降级或删除**，而不是整段铲平。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.debate import Challenge, ChallengeKind
from ir_agent.revision import (
    RevisionFailed,
    revise_from_verdicts,
    unaddressed,
)

D = Decimal


def ch(passage, kind=ChallengeKind.LOGIC, verdict="成立", idx=1):
    return Challenge(index=idx, kind=kind, passage=passage,
                     objection="某质疑", verdict=verdict)


def catalog():
    from ir_agent.analyst import FactCatalog
    from ir_agent.ledger import Fact, FactLedger, Method
    l = FactLedger()
    for k, v in [("revenue.yoy", "-0.0121"), ("net_profit.yoy", "-0.045")]:
        l.put(Fact(key=k, value=D(v), unit="ratio", currency=None,
                   period="2025FY", as_of=date(2026, 4, 20),
                   source_id="calc_x", method=Method.COMPUTED))
    return FactCatalog.from_ledger(l, "2025FY", as_of=date(2026, 6, 1))


class TestUnaddressed:
    def test_passage_still_present_verbatim_is_unaddressed(self):
        cs = [ch("净利润降幅小于营收降幅")]
        assert unaddressed(cs, "结论：净利润降幅小于营收降幅，故…") == cs

    def test_rewritten_passage_counts_as_addressed(self):
        cs = [ch("净利润降幅小于营收降幅")]
        assert unaddressed(cs, "结论：净利润降幅大于营收降幅。") == []

    def test_only_upheld_challenges_are_checked(self):
        """不成立的挑战不要求改动 —— 分析师据理反驳成功了。"""
        cs = [ch("某原句", verdict="不成立")]
        assert unaddressed(cs, "某原句仍在。") == []

    def test_whitespace_differences_do_not_count_as_a_rewrite(self):
        cs = [ch("净利润 降幅 小于 营收降幅")]
        assert unaddressed(cs, "净利润降幅小于营收降幅") == cs

    def test_quotes_around_the_passage_are_ignored(self):
        """质疑者常把原句加引号引用，比对时要剥掉。"""
        cs = [ch('"净利润降幅小于营收降幅"')]
        assert unaddressed(cs, "净利润降幅小于营收降幅，故…") == cs


class TestReviseFromVerdicts:
    GOOD = ("## 经营概览\n营业收入同比 [[revenue.yoy@2025FY]]，"
            "净利润同比 [[net_profit.yoy@2025FY]]，利润降幅大于收入降幅。\n")

    def _client(self, *rs):
        seen = []

        def c(p):
            seen.append(p)
            return rs[min(len(seen) - 1, len(rs) - 1)]
        c.seen = seen
        return c

    def test_no_upheld_challenges_returns_the_draft_untouched(self):
        c = self._client("不该被调用")
        out, changed = revise_from_verdicts(
            catalog(), "原正文。", [ch("某句", verdict="不成立")], c)
        assert out == "原正文。" and changed is False and c.seen == []

    def test_upheld_challenges_trigger_a_rewrite(self):
        out, changed = revise_from_verdicts(
            catalog(), "净利润降幅小于营收降幅。",
            [ch("净利润降幅小于营收降幅")], self._client(self.GOOD))
        assert changed and "大于" in out

    def test_prompt_lists_the_upheld_challenges(self):
        c = self._client(self.GOOD)
        revise_from_verdicts(catalog(), "净利润降幅小于营收降幅。",
                             [ch("净利润降幅小于营收降幅")], c)
        assert "净利润降幅小于营收降幅" in c.seen[0]

    def test_prompt_forbids_wholesale_deletion(self):
        """删光被质疑的段落会得到一篇正确但空洞的报告。"""
        c = self._client(self.GOOD)
        revise_from_verdicts(catalog(), "某正文。", [ch("某正文")], c)
        assert "删除" in c.seen[0] and ("保留" in c.seen[0] or "空洞" in c.seen[0])

    def test_revision_must_satisfy_the_placeholder_contract(self):
        bad = "净利润同比 -4.50%，降幅大于营收。"
        c = self._client(bad, self.GOOD)
        out, _ = revise_from_verdicts(catalog(), "原句。", [ch("原句")], c)
        assert "4.50%" not in out

    def test_revision_that_ignores_the_challenge_is_rejected(self):
        """口头接受却原样保留 —— 必须被驳回重写。"""
        unchanged = "净利润降幅小于营收降幅 [[revenue.yoy@2025FY]]。"
        c = self._client(unchanged, self.GOOD)
        out, _ = revise_from_verdicts(
            catalog(), "净利润降幅小于营收降幅。",
            [ch("净利润降幅小于营收降幅")], c)
        assert "小于" not in out
        assert "未处理" in c.seen[1] or "仍原样" in c.seen[1]

    def test_persistent_failure_raises(self):
        unchanged = "净利润降幅小于营收降幅 [[revenue.yoy@2025FY]]。"
        with pytest.raises(RevisionFailed, match="净利润降幅小于"):
            revise_from_verdicts(
                catalog(), "净利润降幅小于营收降幅。",
                [ch("净利润降幅小于营收降幅")],
                self._client(unchanged), max_attempts=2)


# ── 回写不得把正文掏空：规则此前只写在提示词里 ────────────────────
#
# **贵州茅台全流程实跑（600519 第一阶段）。** 辩论 2 项挑战成立并回写，
# 简报从应有的八节塌缩成三节，23KB 量级的正文只剩 6KB —— 而保留下来的两节
# 恰好就是那两条挑战的主题。宏观环境、行业格局、竞争格局、公司经营、财务
# 结构、风险提示连同已经花钱写出、已经通过全部闸门的内容一起消失。
#
# 为什么两道验收都没拦住:
#   · `_violations` —— 截断后的文本**完全满足**占位符契约，它只是更短
#   · `unaddressed` —— 查的是「被质疑的原句是否仍原样存在」。**这条检查
#     反过来奖励删除**: 把那句话删掉是通过它最省力的办法，把整节删掉更
#     保险。验收条件写的是「那句话不许原样留着」，而我们要的是「那句话被
#     改对了」—— 两者之差正好是模型这次走的那条路。
#
# 而「不要整段删光」这条规则**一直写在 `_RULES` 里**，`test_prompt_forbids_
# wholesale_deletion` 还专门钉住了它在提示词里。提示词是对模型的请求，是最
# 弱的那种执行；而「章节丢了」「正文缩了一大半」是代码能独立观测的事实。
#
# 对照: 生益科技第二阶段同样回写了 3 项，研报从 29.6KB **涨到** 31.7KB。
# 同一段代码、同一组检查，模型的两种行为都合法 —— 所以这不是代码写错了，
# 是验收条件弱到让两种行为都通过。
# 回写测试的原稿夹具。每节都带一段有实质长度的分析 —— 夹具必须像真产物。
# 第一版写成每节一句「若干。」，于是「标题留着、内容删光」那一稿反而有原文
# 90% 的长度（标题占了大头），长度兜底测不出来: **夹具不真实，测试就测不到
# 它要测的东西**，与本仓库踩过的「测试输入分布与生产不一致」同类。
_PARA = ("该节对相关事实逐项展开，说明口径、同业位置与其对后续假设的影响，"
         "并指出哪些结论可由清单内的事实支撑、哪些只能作为方向性参考。")
_DRAFT = "\n\n".join(
    [f"## {t}\n{_PARA}" for t in ("投资要点", "宏观环境", "行业格局")]
    + [f"## 公司经营\n净利润降幅小于营收降幅，"
       f"营收同比 [[revenue.yoy@2025FY]]。{_PARA}"]
    + [f"## {t}\n{_PARA}" for t in ("财务结构", "风险提示")]) + "\n"


class TestRevisionMustNotGutTheReport:
    DRAFT = _DRAFT

    # 只剩被质疑的那一节 —— 茅台那次的形状
    GUTTED = ("## 公司经营\n净利润降幅大于营收降幅，"
              "营收同比 [[revenue.yoy@2025FY]]。\n")

    # 章节齐全、只改站不住的那一句
    FIXED = DRAFT.replace("净利润降幅小于营收降幅", "净利润降幅大于营收降幅")

    def _client(self, *rs):
        seen = []

        def c(p):
            seen.append(p)
            return rs[min(len(seen) - 1, len(rs) - 1)]
        c.seen = seen
        return c

    def _ch(self):
        return [ch("净利润降幅小于营收降幅")]

    def test_丢了章节的修订稿被驳回并重写(self):
        c = self._client(self.GUTTED, self.FIXED)
        out, changed = revise_from_verdicts(catalog(), self.DRAFT,
                                            self._ch(), c)
        assert changed and out.strip() == self.FIXED.strip(), "塌缩的那一稿被收下了"
        assert len(c.seen) == 2, "没有驳回重写"

    def test_驳回信息要列出丢掉的章节(self):
        """只说「不许删」模型无从定位 —— 驳回信息必须说清怎么改才能通过，
        这是本仓库已有的一条教训（见 basis.py 的 _REPORT_GUIDANCE）。"""
        c = self._client(self.GUTTED, self.FIXED)
        revise_from_verdicts(catalog(), self.DRAFT, self._ch(), c)
        assert "宏观环境" in c.seen[1] and "风险提示" in c.seen[1]

    def test_一直丢章节则拒绝交付修订稿(self):
        """用尽重试后抛 RevisionFailed —— 调用方据此保留未修订稿并标注，
        那条兜底已经在 full._debate_and_revise 里（两阶段共用）。"""
        c = self._client(self.GUTTED)
        with pytest.raises(RevisionFailed, match="章节"):
            revise_from_verdicts(catalog(), self.DRAFT, self._ch(), c)

    def test_章节齐全的正常修订不被误判(self):
        out, changed = revise_from_verdicts(catalog(), self.DRAFT,
                                            self._ch(), self._client(self.FIXED))
        assert changed and "大于" in out

    def test_章节齐全但正文被掏空同样驳回(self):
        """标题留着、内容删光是同一种损失的另一张脸 —— 只查标题会漏掉它。"""
        hollow = "\n\n".join(
            f"## {t}\n略。" for t in ("投资要点", "宏观环境", "行业格局",
                                      "财务结构", "风险提示")
        ) + ("\n\n## 公司经营\n营收同比 [[revenue.yoy@2025FY]]，"
             "净利润降幅大于营收降幅。\n")
        c = self._client(hollow, self.FIXED)
        out, _ = revise_from_verdicts(catalog(), self.DRAFT, self._ch(), c)
        assert len(c.seen) == 2, "被掏空的那一稿被收下了"
        assert out.strip() == self.FIXED.strip(), \
            "最终交付的不是章节与分析都完整的那一稿"

    def test_新增章节不算丢(self):
        """补上被忽略的反面证据可能需要新起一节，这是正当修订。"""
        added = self.FIXED + "\n## 反面证据\n补充若干。\n"
        out, changed = revise_from_verdicts(catalog(), self.DRAFT,
                                           self._ch(), self._client(added))
        assert changed and "反面证据" in out
