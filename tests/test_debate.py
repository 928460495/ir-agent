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
        """契约对所有参与者一视同仁 —— **但「一视同仁」的内容变了。**

        这条测试原先断言「质疑者写裸数字要被驳回并重写」。该策略已撤销:
        实跑三次辩论失败里有两次是它造成的，而放行的理由本就写在
        debate.py 的注释里（辩论文字不进正文，回写另有审计）。见
        TestChallengerMayDoArithmetic。

        仍然一视同仁的是**不得凭空拼造占位符** —— 那是声称账本里有一条
        不存在的事实，与「没写成占位符」不是一个量级。这条测试改为钉住
        这一半: 驳回信息要指名那个伪造的引用，且重写后仍能产出可用挑战。
        """
        bad = ("[挑战 1] 类型：逻辑错误\n原文：某句\n"
               "质疑：毛利率 [[gross_margin.fake@2025FY]] 与结论矛盾。")
        c = self._client(bad, RAW, "分析师回应。", self.VERDICT)
        r = run_debate(self._catalog(), draft="某正文。", client=c, max_rounds=1)
        assert "被驳回" in c.seen[1]              # 第二次调用带上了驳回说明
        assert "gross_margin.fake" in c.seen[1]   # 且指名了伪造的那个引用
        assert r.challenges                       # 重写后仍产出了可用挑战

    def test_structural_markers_are_not_treated_as_bare_numbers(self):
        """[挑战 1] 里的编号是格式不是内容 —— 否则每一稿都会因自己的
        编号被驳回，辩论永远走不出第一轮。"""
        c = self._client(RAW, "分析师回应。", self.VERDICT)
        run_debate(self._catalog(), draft="某正文。", client=c, max_rounds=1)
        assert "被驳回" not in c.seen[1]

    def test_persistent_contract_violation_refuses(self):
        """反复伪造引用则中止辩论。**违规样本从裸数字换成了伪造占位符** ——
        裸数字对参与者已放行，见上一条测试的 docstring。"""
        bad = ("[挑战 1] 类型：逻辑错误\n原文：某句\n"
               "质疑：营收 [[revenue.fake@2025FY]] 有误。")
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


# ── 占位符契约：执行一视同仁，指令也必须一视同仁 ──────────────────
#
# **实测事故（生益科技 600183 第六轮）。** 撰写正文通过了全部闸门，多空辩论
# 却被驳回三次后中止，违规的数字是 17、67、50 —— 全是**分位数**，而它们正是
# 事实清单「数值」一列里显示的东西：
#
#     权益乘数分位        67 分位   写作 [[equity_multiplier.percentile@2025FY]]
#
# 提示词必须显示数值（模型不知道数量级就无法推理），而契约禁止写出数值。
# 撰写者为此拿到整整一段纪律，其中专门有一条讲「数值」列是渲染结果、不要
# 照抄——例子用的就是「17 分位」。而质疑者与裁决者拿到的只有两行，这条坑
# 一个字都没提。
#
# debate.py 的模块 docstring 写着「占位符契约对所有参与者一视同仁」。
# **执行确实一视同仁（_violations 对三方同一套），指令从来不是。** 这是
# 同一个会话里第三次撞上「声明与现实之间的缝」: _norm 的三份副本都声称
# 与另两处相同；两处 except 的注释都论证了失败不该毙掉正文而元组没照做。
class TestPlaceholderRuleParity:
    def test_三方提示词都带着与撰写者同一份纪律(self):
        """钉的是**同一份文本**，不是「也提到了占位符」：任何人再写一个
        简版，这条立刻变红。"""
        from ir_agent.analyst import PLACEHOLDER_RULES
        from ir_agent import debate

        for name, prompt in (("质疑者", debate._CHALLENGER),
                             ("回应者", debate._RESPONDER),
                             ("裁决者", debate._JUDGE)):
            assert PLACEHOLDER_RULES in prompt, f"{name}拿到的纪律不是同一份"

    def test_撰写者两种模式也用同一份(self):
        """简报版与完整研报版此前逐字重复了两遍（553 字符），一份改了另一份
        不会跟着改。"""
        from ir_agent.analyst import PLACEHOLDER_RULES, build_prompt
        from ir_agent.analyst import FactCatalog

        cat = FactCatalog(entries=[])
        for brief in (True, False):
            assert PLACEHOLDER_RULES in build_prompt(
                cat, code="600183", period="2025FY", brief=brief)

    def test_纪律里确实有数值列那条坑(self):
        """这条是事故的直接成因，单独钉一次 —— 收归一处时若把它丢了，
        上面那条身份测试照样全绿。"""
        from ir_agent.analyst import PLACEHOLDER_RULES

        assert "「数值」一列" in PLACEHOLDER_RULES
        assert "渲染结果" in PLACEHOLDER_RULES


# ── 质疑者可以算账：裸数字对辩论参与者放行 ────────────────────────
#
# **今天三次辩论失败里有两次是这条规则造成的**（生益科技 600183 与贵州茅台
# 600519 各一次）:
#   · 「238亿、3.7倍」—— 质疑者自己算的差额与倍数，账本里没有这两条事实
#   · 「67（应写作 [[equity_multiplier.percentile@2025FY]]）」—— 照抄事实
#     清单「数值」列里的分位数
#
# 两类都无害，而放行的理由**代码注释里早就写好了**:
#
#   > 正文有独立的验收审计，辩论里的裸数字进不了正文: 修订稿仍要过
#   > `_violations`。
#
# 结构上三道保障: ①质疑者/回应者/裁决者的文字只进 debate.md，不进研报正文
# ②正文只能由 revision.py 改，而那条路径的审计**不放宽** ③`_ask` 里本来就有
# 放宽先例（「没有引用任何事实占位符」对质疑者不适用）。
#
# 代价是 debate.md 里会出现未经账本校验的派生数字 —— 但那本来就是「某人提出
# 的质疑」，不是事实陈述。而收益是实质的: 算账恰恰是最有力的质疑形式，旧规则
# 在**系统性淘汰质量最高的那类挑战**。
class TestChallengerMayDoArithmetic:
    VERDICT = "[裁决 1] 成立　（理由）"

    def _client(self, *rs):
        seen = []

        def c(p):
            seen.append(p)
            return rs[min(len(seen) - 1, len(rs) - 1)]
        c.seen = seen
        return c

    def _catalog(self):
        from ir_agent.analyst import FactCatalog
        from ir_agent.ledger import Fact, FactLedger, Method
        l = FactLedger()
        for k, v in [("revenue.yoy", "-0.0121"), ("net_profit.yoy", "-0.045")]:
            l.put(Fact(key=k, value=D(v), unit="ratio", currency=None,
                       period="2025FY", as_of=date(2026, 4, 20),
                       source_id="calc_x", method=Method.COMPUTED))
        return FactCatalog.from_ledger(l, "2025FY", as_of=date(2026, 6, 1))

    def test_质疑者写自己算的数字不再被驳回(self):
        """账本里没有「差额」这条事实 —— 禁止派生算术等于禁止它提出有力质疑。"""
        ch1 = ("[挑战 1] 类型：逻辑错误\n原文：某句\n"
               "质疑：投资现金流与资本开支的差额约 238亿，占比 3.7倍，与结论矛盾。")
        c = self._client(ch1, "分析师回应。", self.VERDICT)
        r = run_debate(self._catalog(), draft="某正文。", client=c, max_rounds=1)
        assert "被驳回" not in c.seen[1], "质疑者又因为算账被驳回了"
        assert r.challenges

    def test_质疑者照抄清单数值列也不再被驳回(self):
        ch1 = ("[挑战 1] 类型：逻辑错误\n原文：某句\n"
               "质疑：营收同比 -1.21% 与结论方向相反。")
        c = self._client(ch1, "分析师回应。", self.VERDICT)
        run_debate(self._catalog(), draft="某正文。", client=c, max_rounds=1)
        assert "被驳回" not in c.seen[1]

    def test_凭空拼造占位符仍然被驳回(self):
        """**这条界线不能放。** 裸数字只是「没写成占位符」，拼造占位符是
        声称账本里有一条不存在的事实 —— 裁决者会据一个不存在的事实判挑战
        成立，而回写会据那个裁决改正文。"""
        bad = ("[挑战 1] 类型：无据断言\n原文：某句\n"
               "质疑：毛利率 [[gross_margin.fabricated@2025FY]] 并不支持该结论。")
        with pytest.raises(DebateRefused):
            run_debate(self._catalog(), draft="某正文。",
                       client=self._client(bad), max_rounds=1, max_attempts=2)

    def test_辩论记录如实标出放行过裸数字(self):
        """放行不等于不说。读者看 debate.md 时要知道里面的派生数字未经校验。"""
        # 夹具带上占位符: 真实的质疑是「引用账本事实 + 自己算一个数」，
        # 而「一个占位符都没有」现在走的是「根本没交正文」那条路，
        # 裸数字意见压根不产生（见 analyst.NO_REF_MSG）。
        ch1 = ("[挑战 1] 类型：逻辑错误\n原文：某句\n"
               "质疑：营收同比 [[revenue.yoy@2025FY]] 之下差额约 238亿，"
               "与结论矛盾。")
        r = run_debate(self._catalog(), draft="某正文。",
                       client=self._client(ch1, "分析师回应。", self.VERDICT),
                       max_rounds=1)
        assert "未经账本校验" in r.summary()

    def test_回写路径不放宽(self):
        """正文只能由 revision.py 改，而那条路径必须保持严格 —— 放宽它等于
        允许裸数字进研报。"""
        import inspect

        from ir_agent import revision
        src = inspect.getsource(revision.revise_from_verdicts)
        assert "is_bare_number" not in src, "回写路径放宽了裸数字审计"
