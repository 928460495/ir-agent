"""多空辩论 —— 范围收窄到纯基本面后，唯一的系统性证伪机制。

设计立场: **辩论的价值在证伪，不在多产出散文。** 因此不做「多头再夸一遍」，
而是「质疑者针对正文提出具体挑战 → 分析师回应 → 裁决者判定哪些挑战成立」。

挑战必须**指向原文的具体片段**并归类。否则「风险依然存在」这类空话会淹没
真问题 —— 实测茅台正文里有「净利润降幅小于营收降幅」的逻辑错误
（实际是大于），招行正文里有「历史两位数水平」的无据断言，
这两类才是辩论该抓的东西。

占位符契约对所有参与者同样生效: 质疑者与裁决者都不得写出裸数字。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.debate import (
    Challenge,
    ChallengeKind,
    DebateRefused,
    parse_challenges,
    run_debate,
    should_continue,
)

D = Decimal

RAW = """
[挑战 1] 类型：逻辑错误
原文：净利润降幅小于营收降幅所隐含的收入端压力
质疑：营收同比 [[revenue.yoy@2025FY]]、净利润同比 [[net_profit.yoy@2025FY]]，
利润降幅明显大于营收降幅，此句结论与数据相反。

[挑战 2] 类型：无据断言
原文：增速显著慢于历史周期中常见的两位数水平
质疑：可用事实中不含历史增速序列，该比较无从支撑。
"""


class TestParseChallenges:
    def test_extracts_each_challenge(self):
        assert len(parse_challenges(RAW)) == 2

    def test_kind_is_classified(self):
        cs = parse_challenges(RAW)
        assert cs[0].kind is ChallengeKind.LOGIC
        assert cs[1].kind is ChallengeKind.UNSOURCED

    def test_quotes_the_passage_it_attacks(self):
        assert "净利润降幅小于营收降幅" in parse_challenges(RAW)[0].passage

    def test_body_of_the_objection_is_kept(self):
        assert "与数据相反" in parse_challenges(RAW)[0].objection

    def test_unknown_kind_falls_back_to_other(self):
        raw = "[挑战 1] 类型：随便写的\n原文：某句\n质疑：某质疑。"
        assert parse_challenges(raw)[0].kind is ChallengeKind.OTHER

    def test_challenge_without_a_quoted_passage_is_dropped(self):
        """不指向具体片段的挑战无法核验，等于空话。"""
        raw = "[挑战 1] 类型：逻辑错误\n质疑：整体上不够严谨。"
        assert parse_challenges(raw) == []

    def test_no_challenges_yields_empty_list(self):
        assert parse_challenges("正文未见明显问题。") == []


class TestRoundControl:
    def test_stops_at_the_round_limit(self):
        """轮次由计数器决定，不问模型「够了吗」。"""
        assert should_continue(round_no=2, max_rounds=2, open_count=5) is False

    def test_continues_while_challenges_remain(self):
        assert should_continue(round_no=1, max_rounds=3, open_count=2) is True

    def test_stops_early_when_nothing_is_open(self):
        assert should_continue(round_no=1, max_rounds=3, open_count=0) is False


class TestRunDebate:
    CATALOG_KEYS = ("revenue.yoy", "net_profit.yoy")

    def _catalog(self):
        from ir_agent.analyst import FactCatalog
        from ir_agent.ledger import Fact, FactLedger, Method
        l = FactLedger()
        for k, v in [("revenue.yoy", "-0.0121"), ("net_profit.yoy", "-0.045")]:
            l.put(Fact(key=k, value=D(v), unit="ratio", currency=None,
                       period="2025FY", as_of=date(2026, 4, 20),
                       source_id="calc_x", method=Method.COMPUTED))
        return FactCatalog.from_ledger(l, "2025FY", as_of=date(2026, 6, 1))

    def _client(self, *responses):
        seen = []

        def c(prompt: str) -> str:
            seen.append(prompt)
            return responses[min(len(seen) - 1, len(responses) - 1)]
        c.seen = seen
        return c

    VERDICT = ("[裁决 1] 成立　该句与 [[revenue.yoy@2025FY]] 及 "
               "[[net_profit.yoy@2025FY]] 矛盾，应改写。\n"
               "[裁决 2] 成立　无事实支撑，应删除。")

    def test_challenges_and_verdicts_are_paired(self):
        r = run_debate(self._catalog(), draft="某正文。",
                       client=self._client(RAW, "分析师回应。", self.VERDICT),
                       max_rounds=1)
        assert len(r.challenges) == 2
        assert all(c.verdict for c in r.challenges)

    def test_upheld_challenges_are_countable(self):
        r = run_debate(self._catalog(), draft="某正文。",
                       client=self._client(RAW, "分析师回应。", self.VERDICT),
                       max_rounds=1)
        assert r.upheld_count == 2

    def test_no_challenges_ends_the_debate_immediately(self):
        c = self._client("正文未见明显问题。")
        r = run_debate(self._catalog(), draft="某正文。", client=c, max_rounds=3)
        assert r.challenges == [] and len(c.seen) == 1

    def test_participants_must_respect_the_placeholder_contract(self):
        """质疑者写裸数字同样要被驳回并重写 —— 契约对所有参与者一视同仁。"""
        bad = ("[挑战 1] 类型：逻辑错误\n原文：某句\n"
               "质疑：营收同比 -1.21% 与净利润同比 -4.50% 矛盾。")
        c = self._client(bad, RAW, "分析师回应。", self.VERDICT)
        r = run_debate(self._catalog(), draft="某正文。", client=c, max_rounds=1)
        assert "被驳回" in c.seen[1]          # 第二次调用带上了驳回说明
        assert "1.21" in c.seen[1]            # 且指名了违规的那个数字
        assert r.challenges                   # 重写后仍产出了可用挑战

    def test_structural_markers_are_not_treated_as_bare_numbers(self):
        """[挑战 1] 里的编号是格式不是内容 —— 否则每一稿都会因自己的
        编号被驳回，辩论永远走不出第一轮。"""
        c = self._client(RAW, "分析师回应。", self.VERDICT)
        run_debate(self._catalog(), draft="某正文。", client=c, max_rounds=1)
        assert "被驳回" not in c.seen[1]

    def test_persistent_contract_violation_refuses(self):
        bad = "[挑战 1] 类型：逻辑错误\n原文：某句\n质疑：营收 1688.38 亿元有误。"
        with pytest.raises(DebateRefused):
            run_debate(self._catalog(), draft="某正文。",
                       client=self._client(bad), max_rounds=1, max_attempts=2)

    def test_transcript_records_every_exchange(self):
        r = run_debate(self._catalog(), draft="某正文。",
                       client=self._client(RAW, "分析师回应。", self.VERDICT),
                       max_rounds=1)
        assert len(r.transcript) >= 3

    def test_summary_names_the_upheld_challenges(self):
        r = run_debate(self._catalog(), draft="某正文。",
                       client=self._client(RAW, "分析师回应。", self.VERDICT),
                       max_rounds=1)
        assert "逻辑错误" in r.summary()


class TestEnumerationIsStructureNotContent:
    """列表编号在任何写法下都是结构，不是需要溯源的数字。

    实测同一 prompt 两次运行用了不同格式: 一次 [挑战 1]，一次裸编号 ——
    后者让辩论在第一轮就中止（「裸数字：1、2」）。只剥一种写法不够。
    """
    @staticmethod
    def s(t):
        from ir_agent.debate import strip_structure
        return strip_structure(t)

    def test_bracketed_marker(self):
        assert "1" not in self.s("[挑战 1] 类型：逻辑错误")

    def test_bracketed_verdict(self):
        assert "2" not in self.s("[裁决 2] 成立")

    def test_bare_prefix_marker(self):
        assert "1" not in self.s("挑战 1：类型 逻辑错误")

    def test_line_leading_ordinal(self):
        assert "3" not in self.s("3. 该句无据")

    def test_chinese_ordinal_list(self):
        assert "2" not in self.s("2、该句无据")

    def test_plain_bracket_index(self):
        assert "1" not in self.s("[1] 某挑战")

    def test_content_numbers_survive(self):
        """正文里的真数字必须留下 —— 剥过头就等于关掉了裸数字检测。"""
        out = self.s("营业收入 1688.38 亿元")
        assert "1688.38" in out

    def test_percent_in_the_middle_survives(self):
        assert "4.50" in self.s("质疑：净利率 4.50% 有误")

    def test_year_like_number_at_line_start_survives(self):
        """行首的 2025 是年份不是编号，不该被结构剥离吃掉。"""
        assert "2025" in self.s("2025 年公司营收下滑")
