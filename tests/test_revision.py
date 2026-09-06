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
