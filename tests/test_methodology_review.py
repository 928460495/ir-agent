"""方法论预研的审核者（spec 2026-10-14 子项目二）。不调模型。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent import methodology_review as R
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.methodology import parse as parse_meth
from judge_stub import Judge, items

AS_OF = date(2026, 10, 14)

CHANGE = """模式: 变化
因子: 份额增速 | 本司市占率（占全行业规模，非公司内部占比）同比增速 | 变化率 | [[company.market_share_delta]] | 市场份额
因子: 行业增速 | 行业全球市场规模同比增速 | 变化率 | Prismark | Prismark
因子: 提价 | 提价对毛利率的累计贡献 | pp | 调价函 | 调价
因子: 原料 | 原材料对毛利率的累计侵蚀 | pp | 年报风险因素 | 原材料价格
常量: 上期稼动率 | 78% | 年报经营情况讨论「产能利用率约78%」
公式: 营收 = 上期(营收) × (1 + 行业增速) × (1 + 份额增速)
公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100
覆盖: 份额 | 份额增速
"""

LEVEL = """模式: 水平
因子: 量 | 出货量 | 万吨 | 年报 | 出货量
因子: 价 | 单价 | 元 | [[ccl.asp]] | 单价
因子: 本 | 单位成本 | 元 | 年报 | 单位成本
公式: 营收 = 量 × 价
公式: 毛利率 = (价 − 本) ÷ 价
"""


def _model(text):
    return parse_meth(text).model


def _ledger(**kv):
    led = FactLedger()
    for key, (per, v) in kv.items():
        led.put(Fact(key=key.replace("__", "."), value=D(v), unit="x", period=per,
                     as_of=date(2026, 4, 1), source_id="t", method=Method.REPORTED))
    return led


class TestChecklist:
    def test_七类条目(self):
        got = R.checklist(_model(CHANGE), ["需求", "份额"])
        kinds = [k for k, _ in got]
        assert kinds[0] == "模式" and kinds.count("因子") == 4
        assert ("正交", "份额增速 × 行业增速") in got     # 按声明顺序
        assert ("正交", "提价 × 原料") in got
        assert ("公式", "营收") in got and ("公式", "毛利率") in got
        assert ("常量", "上期稼动率") in got
        assert ("账本", "份额增速 [[company.market_share_delta]]") in got
        assert [s for k, s in got if k == "覆盖"] == ["需求", "份额"]

    def test_共用因子对只列一次_无主题不列覆盖(self):
        got = R.checklist(_model(LEVEL), [])
        assert got.count(("正交", "价 × 本")) == 1
        assert ("正交", "量 × 价") in got
        assert not any(k == "覆盖" for k, _ in got)


class TestParse:
    EXP = [("模式", "模式"), ("因子", "份额增速（本司市占率（占全行业规模，非公司内部占比）同比增速）"),
           ("公式", "营收")]

    def test_按预期条目名匹配_条目名里有括号和冒号也行(self):
        raw = ("- [模式] 模式：通过\n"
               "**[因子] 份额增速（本司市占率（占全行业规模，非公司内部占比）同比增速）：提醒"
               "　分母是全行业：口径需对齐**\n"
               "[公式] 营收：待采集　缺行业规模数据\n")
        got = R.parse(raw, self.EXP)
        assert [v.level for v in got] == ["通过", "提醒", "待采集"]
        assert got[1].why.startswith("分母是全行业：口径需对齐")

    def test_漏答与没写理由都算不完整(self):
        with pytest.raises(R.ReviewIncomplete, match=r"没有给出结论.*\[公式\] 营收"):
            R.parse("[模式] 模式：通过\n[因子] 份额增速（本司市占率（占全行业规模，"
                    "非公司内部占比）同比增速）：通过", self.EXP)
        with pytest.raises(R.ReviewIncomplete, match="没写理由"):
            R.parse("[模式] 模式：致命\n[因子] 份额增速（本司市占率（占全行业规模，"
                    "非公司内部占比）同比增速）：通过\n[公式] 营收：通过", self.EXP)

    def test_四级之外的结论不认(self):
        with pytest.raises(R.ReviewIncomplete):
            R.parse("[模式] 模式：还行　x\n[因子] 份额增速（本司市占率（占全行业规模，"
                    "非公司内部占比）同比增速）：通过\n[公式] 营收：通过", self.EXP)


class TestAnnotate:
    def test_引文与占位符核验不过只标注(self):
        led = _ledger(company__market_share=("2025FY", "0.08"))
        vs = [R.Verdict("因子", "a", "提醒", "据「产能利用率约78%」与 [[company.market_share@2025FY]]"),
              R.Verdict("因子", "b", "提醒", "据「凭空捏造的一句话」与 [[foo.bar@2025FY]]")]
        got = R.annotate_quotes(vs, hay="……产能利用率约78%……", ledger=led, as_of=AS_OF)
        assert got[0].quote_note == ""
        assert "找不到" in got[1].quote_note and "foo.bar" in got[1].quote_note
        assert got[1].level == "提醒", "只标注，不改结论"


class TestExtraFacts:
    def test_账本取值_命中次数_覆盖(self):
        led = _ledger(company__market_share_delta=("2025FY", "0.0087"))
        m = parse_meth(CHANGE)
        txt = R.extra_facts(m.model, ledger=led, as_of=AS_OF,
                            sections="Prismark 数据。", themes=["份额", "政策"],
                            coverage_decl=m.coverage_decl)
        assert "company.market_share_delta@2025FY = 0.0087" in txt
        assert "Prismark:1" in txt
        assert "份额：✓" in txt and "政策：？" in txt

    def test_水平模式第0年试算与报表并排(self):
        led = _ledger(ccl__asp=("2025FY", "10"), revenue=("2025FY", "1000000000"),
                      gross_margin=("2025FY", "0.25"))
        txt = R.extra_facts(_model(LEVEL), ledger=led, as_of=AS_OF)
        assert "缺 量、本 的基期值" in txt

    def test_水平模式基期齐全时给出试算(self):
        lv = LEVEL.replace("| 年报 | 出货量", "| [[ccl.qty]] | 出货量") \
                  .replace("| 年报 | 单位成本", "| [[ccl.cost]] | 单位成本")
        led = _ledger(ccl__asp=("2025FY", "10"), ccl__qty=("2025FY", "100"),
                      ccl__cost=("2025FY", "7.5"), revenue=("2025FY", "100000000000"),
                      gross_margin=("2025FY", "0.25"))
        txt = R.extra_facts(_model(lv), ledger=led, as_of=AS_OF)
        assert "营收：公式第 0 年 = 1000；报表 = 1000（亿元）" in txt
        assert "毛利率：公式第 0 年 = 0.25；报表 = 0.25" in txt


def _run(proposer, judge, **kw):
    return R.propose_and_review(proposer, judge, industry="电子", code="600183",
                                prompt="预研提示词", materials="资料",
                                themes=kw.pop("themes", ["份额"]),
                                ledger=FactLedger(), as_of=AS_OF, **kw)


class _Proposer:
    def __init__(self, *outs):
        self.outs, self.prompts = list(outs), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.outs[min(len(self.prompts), len(self.outs)) - 1]


class TestRounds:
    def test_第一轮通过(self):
        m, res, rounds = _run(_Proposer(CHANGE), Judge())
        assert res.rounds == 1 and not res.fatal() and len(rounds) == 1
        assert m.model.mode == "变化" and res.fingerprint

    def test_致命即驳回_第二轮通过_反馈带上致命项(self):
        j = Judge({("正交", "份额增速 × 行业增速"): "致命"}, {})
        p = _Proposer(CHANGE, CHANGE)
        m, res, rounds = _run(p, j)
        assert res.rounds == 2 and len(rounds) == 2 and rounds[0].review.fatal()
        assert "致命" in p.prompts[1] and "份额增速 × 行业增速" in p.prompts[1]

    def test_提醒与待采集不驳回(self):
        _, res, _ = _run(_Proposer(CHANGE), Judge({"因子": "提醒", "常量": "待采集"}))
        assert res.rounds == 1 and len(res.flagged()) == 5

    def test_代码检查不过也算一轮(self):
        p = _Proposer("胡言乱语", CHANGE)
        _, res, rounds = _run(p, Judge())
        assert res.rounds == 2 and rounds[0].code_error
        assert "代码检查不过" in p.prompts[1]

    def test_三轮仍有致命停下(self):
        with pytest.raises(R.ReviewStop, match="3 轮未通过.*致命 1 条") as e:
            _run(_Proposer(CHANGE), Judge({"模式": "致命"}))
        assert len(e.value.rounds) == 3

    def test_审核者漏答先补答_不消耗提出者轮数(self):
        class Once(Judge):
            def __call__(self, prompt):
                self.skip = {("模式", "模式")} if not self.prompts else set()
                return super().__call__(prompt)
        j, p = Once(), _Proposer(CHANGE)
        _, res, rounds = _run(p, j)
        assert len(j.prompts) == 2 and len(p.prompts) == 1 and res.rounds == 1
        assert "上一次回答不完整" in j.prompts[1]

    def test_审核者始终答不完整就停下_不当通过(self):
        with pytest.raises(R.ReviewStop, match="审核未完成"):
            _run(_Proposer(CHANGE), Judge(skip={("模式", "模式")}))

    def test_审核者看到的比提出者多(self):
        j = Judge()
        _run(_Proposer(CHANGE), j)
        assert "只给你的、代码算出的事实" in j.prompts[0]
        assert "命中次数" in j.prompts[0] and "资料" in j.prompts[0]

    def test_各轮记录(self):
        with pytest.raises(R.ReviewStop) as e:
            _run(_Proposer("胡言乱语", CHANGE), Judge({"模式": "致命"}))
        md = R.rounds_markdown(e.value.rounds)
        assert md.count("## 第 ") == 3 and "代码检查：不过" in md
        assert "审核：驳回" in md and "[模式] 模式：致命" in md


class TestResult:
    def test_json往返_markdown只列提醒待采集(self):
        _, res, _ = _run(_Proposer(CHANGE), Judge({"常量": "待采集"}))
        back = R.ReviewResult.from_json(res.to_json())
        assert back.verdicts == res.verdicts and back.fingerprint == res.fingerprint
        md = "\n".join(res.markdown())
        assert "第 1 轮通过" in md and "[常量] 上期稼动率：待采集" in md
        assert "[模式] 模式" not in md, "通过的只给条数"

    def test_清单与提示词里的条目一致(self):
        j = Judge()
        _run(_Proposer(CHANGE), j)
        assert items(j.prompts[0]) == R.checklist(_model(CHANGE), ["份额"])


def test_规则正文不出现与清单同形的条目行():
    """规则里若写 `- [模式] …`，审核者会把它当成要作答的条目（假审核者就当真
    了）。说明一律用「类别名：」。"""
    import re
    assert not re.search(r"(?m)^\s*-\s*\[(模式|因子|正交|公式|常量|账本|覆盖)\]", R.RULES)


# ══ 生益科技第二次调用试跑后的修订（2026-10-14）════════════════════════════
# 第 3 轮审核「未完成」：漏掉 [公式] 营收、[公式] 毛利率 与两条 [账本]，审核者
# 原文没落盘，原因只能推测。用户决定：A 原文落盘、B 条目名容错、C [正交] 口径、
# 并且审核者要给提出者修改建议。

class TestJudgeRawSaved:
    """A：审核者每次作答（含被退回补答的）都落进 drivers_rounds.md。"""

    def test_通过时_被退回补答的那次也落盘(self):
        class Once(Judge):
            def __call__(self, prompt):
                self.skip = {("模式", "模式")} if not self.prompts else set()
                return super().__call__(prompt)
        _, res, rounds = _run(_Proposer(CHANGE), Once())
        md = R.rounds_markdown(rounds)
        assert "审核者原文（第 1 次作答）" in md and "审核者原文（第 2 次作答）" in md
        assert "没有给出结论" in md.split("审核者原文（第 2 次作答）")[0]

    def test_未完成时_三次原文都落盘(self):
        with pytest.raises(R.ReviewStop) as e:
            _run(_Proposer(CHANGE), Judge(skip={("模式", "模式")}))
        md = R.rounds_markdown(e.value.rounds)
        assert "审核者原文（第 3 次作答）" in md
        assert md.count("[因子] 份额增速（") >= 3

    def test_原文里有三个反引号也不破坏围栏(self):
        rounds = [R.Round(1, "```text\n模式: 变化\n```", code_error="x")]
        md = R.rounds_markdown(rounds)
        assert "````text" in md


class TestSubjectTolerance:
    """B：审核者照提出者原文写条目名 —— 带不带 @期间、[公式] 后面抄不抄公式都认。"""

    def test_账本条目照提出者原文列_保留期间(self):
        m = _model(CHANGE.replace("[[company.market_share_delta]]",
                                  "[[company.market_share_delta@2025FY]]"))
        assert ("账本", "份额增速 [[company.market_share_delta@2025FY]]") \
            in R.checklist(m, [])

    @pytest.mark.parametrize("exp,ans", [
        ("份额增速 [[company.market_share_delta]]",
         "份额增速 [[company.market_share_delta@2025FY]]"),
        ("份额增速 [[company.market_share_delta@2025FY]]",
         "份额增速 [[company.market_share_delta]]"),
    ])
    def test_账本条目_期间写不写都认(self, exp, ans):
        got = R.parse(f"[账本] {ans}：提醒　口径待对齐 [[x.y@2025FY]]", [("账本", exp)])
        assert got[0].subject == exp and got[0].level == "提醒"
        assert got[0].why == "口径待对齐 [[x.y@2025FY]]", "理由里的占位符不能被剥"

    def test_公式条目后面抄了公式也认(self):
        raw = ("[公式] 营收 = 上期(营收) × (1 + 行业需求增速/100)：致命　量纲错　建议：去掉 /100\n"
               "[公式] 毛利率=基期(毛利率) + (a + b) / 100：通过")
        got = R.parse(raw, [("公式", "营收"), ("公式", "毛利率")])
        assert [v.level for v in got] == ["致命", "通过"]


class TestOrthogonalRule:
    def test_共用证据来源不判致命(self):
        """C：[正交] 只看量是否重复；两个不同的量共用一条证据是资料问题。
        第 2 轮「成本转嫁 × 高端认证进度」因共用 Q2 环比 +4.53pct 被判致命，提出者
        第 3 轮改成残差估算，反而更牵强。"""
        rule = [l for l in R.RULES.splitlines() if l.startswith("- 正交：")][0]
        block = R.RULES.split(rule)[1].split("\n- 公式：")[0]
        text = rule + block
        assert "证据" in text and "不判致命" in text


class TestSuggestions:
    """审核者要给提出者修改建议：致命必写「建议：…」，提醒/待采集可选。"""

    EXP = [("模式", "模式"), ("公式", "营收")]

    def test_致命必须附建议_否则算不完整(self):
        with pytest.raises(R.ReviewIncomplete, match="没给修改建议.*\\[公式\\] 营收"):
            R.parse("[模式] 模式：通过\n[公式] 营收：致命　重复计算", self.EXP)

    def test_建议与理由分开存(self):
        got = R.parse("[模式] 模式：提醒　牵强　建议：改水平\n"
                      "[公式] 营收：致命　重复计算　建议：删掉份额增量", self.EXP)
        assert got[1].why == "重复计算" and got[1].suggest == "删掉份额增量"
        assert got[0].suggest == "改水平"
        assert "建议：删掉份额增量" in got[1].line()

    def test_反馈给提出者带上建议(self):
        p = _Proposer(CHANGE, CHANGE)
        _run(p, Judge({("正交", "份额增速 × 行业增速"): "致命"}, {}))
        assert "建议：" in p.prompts[1]

    def test_json往返保留建议(self):
        res = R.ReviewResult(verdicts=[R.Verdict("模式", "模式", "提醒", "x", suggest="y")])
        assert R.ReviewResult.from_json(res.to_json()).verdicts == res.verdicts

    def test_规则写明致命要给建议(self):
        assert "建议：" in R.RULES and "致命" in R.RULES.split("建议：")[0]
