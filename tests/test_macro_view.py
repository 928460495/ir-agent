"""宏观定性: 政策取向与传导路径。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import macro_view as MV
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.macro_view import Impact, MacroViewFailed, Stance
from ir_agent.sources.external import Corpus, Document, Tier
from judge_stub import Judge

D = Decimal
AS_OF = date(2026, 9, 7)
Q = "央行下调政策利率，货币环境延续宽松"
DOC = f"{Q}。居民收入预期改善，商务宴请场景逐步恢复。"


def _corpus():
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="M1", url="https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl=x",
                   title="宏观周报", publisher="甲证券", tier=Tier.SELLSIDE,
                   published=date(2026, 9, 1), retrieved=AS_OF, text=DOC))
    return c


def _rates(ten, two):
    """两条曲线两个时点 —— cycle.policy_shape 至少要两点。"""
    led = FactLedger()
    for p, d in (("2026-09-01", date(2026, 9, 1)), ("2026-09-05", date(2026, 9, 5))):
        for key, v in (("rate.cgb_10y", ten), ("rate.cgb_2y", two)):
            led.put(Fact(key=key, value=D(str(v)), unit="ratio", period=p,
                         as_of=d, source_id="t", method=Method.REPORTED))
    return led


def _out(stance="宽松", channels=None):
    channels = channels if channels is not None else [
        f"传导: 政策利率下调 | 居民收入预期改善→宴请场景恢复 | 利好 | 「{Q}」",
        f"传导: 流动性宽松 | 折现率下行→估值中枢抬升 | 利好 | 「{Q}」"]
    head = [f"政策取向: {stance} | 「{Q}」"] if stance else []
    return "\n".join([*head, *channels])


class _Client:
    def __init__(self, *r):
        self.replies, self.prompts = list(r), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


# ── 解析 ─────────────────────────────────────────────────────

def test_解析取向与传导():
    v = MV.parse(_out())
    assert v.stance is Stance.EASING and len(v.channels) == 2
    assert v.channels[0].impact is Impact.POSITIVE


def test_取向不是规定的词_记进读数事实():
    v = MV.parse(f"政策取向: 偏松 | 「{Q}」")
    assert v.stance is None and "宽松/中性/收紧" in "\n".join(v.read_notes)


def test_影响不是规定的词_记进读数事实():
    v = MV.parse(f"传导: a | b | 略有帮助 | 「{Q}」")
    assert v.channels == [] and "利好/中性/利空" in "\n".join(v.read_notes)


def test_缺依据_记进读数事实():
    v = MV.parse("政策取向: 宽松 | 我的判断")
    assert v.stance is Stance.EASING and "没有给出依据" in "\n".join(v.read_notes)


# ── 传导路径是重点 ───────────────────────────────────────────

def test_未给取向_记进事实():
    """「中性」也是判断，须显式说出 —— 现在交审核者 [取向] 判。"""
    v = MV.parse(_out(stance=None))
    assert any("中性" in x and "也是判断" in x
               for x in MV.facts(v, FactLedger(), AS_OF, corpus=_corpus()))


def test_传导少于两条_记进事实():
    """「政策转向宽松」写在哪篇研报里都成立，对判断本标的没有帮助 ——
    有帮助的是那条链。现在交审核者 [传导] 判。"""
    v = MV.parse(_out(channels=[f"传导: a | b | 利好 | 「{Q}」"]))
    assert any("至少 2 条" in x for x in MV.facts(v, FactLedger(), AS_OF, corpus=_corpus()))


def test_合法输入没有事实():
    v = MV.parse(_out())
    assert v.read_notes == [] and MV.facts(v, FactLedger(), AS_OF, corpus=_corpus()) == []


def test_引文找不到_记进事实():
    v = MV.parse(_out(stance="宽松", channels=[
        "传导: a | b | 利好 | 「央行大幅加息」",
        f"传导: c | d | 利好 | 「{Q}」"]))
    assert any("核验不过" in x for x in MV.facts(v, FactLedger(), AS_OF, corpus=_corpus()))


def test_出处由代码判定():
    from ir_agent.basis import BasisKind

    v = MV.parse(_out())
    MV.facts(v, FactLedger(), AS_OF, corpus=_corpus())
    assert v.stance_bases[0].kind is BasisKind.EXTERNAL


# ── 与期限利差交叉印证 ───────────────────────────────────────

def test_说宽松而利差倒挂则方向相反():
    led = _rates(ten="0.020", two="0.026")          # 倒挂 → 收紧
    c = MV.cross_check(MV.parse(_out("宽松")), led, AS_OF)
    assert len(c) == 1 and "方向相反" in c[0] and "倒挂" in c[0]


def test_说宽松且利差陡峭则无矛盾():
    led = _rates(ten="0.030", two="0.020")          # 陡峭 → 宽松
    assert MV.cross_check(MV.parse(_out("宽松")), led, AS_OF) == []


def test_中性不参与交叉印证():
    """利差只分陡峭与平坦两档，判不出「中性」，硬比会造出假矛盾。"""
    led = _rates(ten="0.020", two="0.026")
    assert MV.cross_check(MV.parse(_out("中性")), led, AS_OF) == []


def test_无利率数据时跳过():
    assert MV.cross_check(MV.parse(_out()), FactLedger(), AS_OF) == []


def test_形态判定与cycle共用一份实现():
    """同一个概念两处各写一遍，迟早会像 EBIT 那样一处加回利息一处没加。"""
    from ir_agent import cycle
    assert cycle.policy_shape(_rates("0.020", "0.026"), AS_OF) == ("倒挂", "收紧")
    assert cycle.policy_shape(_rates("0.030", "0.020"), AS_OF) == ("陡峭", "宽松")


# ── 提示词与端到端 ───────────────────────────────────────────

def test_提示词强调链条():
    c = _Client(_out())
    MV.assess(c, _corpus(), FactLedger(), AS_OF, judge=Judge())
    assert "写不出链条的政策判断等于没写" in c.prompts[0]
    assert "转述" in c.prompts[0] and "审核者逐项审核" in c.prompts[0]


def test_无外部宏观资料时明说只能由读数推断():
    txt = MV.for_prompt(Corpus(as_of=AS_OF))
    assert "只能由读数推断" in txt


def test_宏观读数以占位符形式带入():
    txt = MV.for_prompt(_corpus(), macro_prompt="PMI [[macro.pmi@2026-08]]")
    assert "[[macro.pmi@2026-08]]" in txt


def test_财经媒体的可引用范围出现在资料正文之前():
    """媒体资料混进语料库后，约束它用途的 TIER_RULE 必须先于正文
    出现，模型才是带着这条界线去读，而不是读完才补一句规矩。"""
    from ir_agent.sources.external import TIER_RULE

    media_text = "市场情绪回暖，宽松预期升温。"
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="M1", url="https://news.example.com/m1",
                   title="宏观观察", publisher="某财经网", tier=Tier.MEDIA,
                   published=date(2026, 8, 1), retrieved=AS_OF, text=media_text))

    txt = MV.for_prompt(c)
    rule = TIER_RULE[Tier.MEDIA]
    assert rule in txt
    assert txt.index(rule) < txt.index(media_text)


def test_多档来源同时出现时可引用范围按Tier声明顺序排列():
    """Fix 5c 回归：此前用 `{d.tier for d in corpus.docs}` 这个 set 去
    遍历可引用范围，迭代顺序取决于 PYTHONHASHSEED。改成 `for t in
    Tier:` 之后，无论语料库里的文档以什么顺序加入，"可引用范围"这几行
    必须固定按 OFFICIAL → SELLSIDE → MEDIA 的声明顺序出现。"""
    from ir_agent.sources.external import TIER_RULE

    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="M1", url="https://news.example.com/m1",
                   title="宏观观察", publisher="某财经网", tier=Tier.MEDIA,
                   published=date(2026, 8, 1), retrieved=AS_OF, text="媒体正文"))
    c.add(Document(doc_id="R1",
                   url="https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl=y",
                   title="宏观周报", publisher="甲证券", tier=Tier.SELLSIDE,
                   published=date(2026, 9, 1), retrieved=AS_OF, text=DOC))

    txt = MV.for_prompt(c)
    sellside_rule = TIER_RULE[Tier.SELLSIDE]
    media_rule = TIER_RULE[Tier.MEDIA]
    assert txt.index(sellside_rule) < txt.index(media_rule)


def test_摘要安全无裸数字():
    from ir_agent.citation import audit_bare_numbers
    v, _ = MV.assess(_Client(_out()), _corpus(), _rates("0.020", "0.026"), AS_OF,
                     judge=Judge())
    assert v.conflicts
    assert audit_bare_numbers(v.summary()) == []


def test_审核致命则带建议重写():
    c = _Client(_out(), _out())
    MV.assess(c, _corpus(), FactLedger(), AS_OF,
              judge=Judge({("遗漏", "宏观因素"): "致命"}, {}))
    assert len(c.prompts) == 2 and "上一轮审核驳回" in c.prompts[1]
    assert "建议：" in c.prompts[1]


def test_三轮审核不过则停下():
    from ir_agent.qual_review import ReviewStop
    with pytest.raises(ReviewStop, match="宏观定性审核 3 轮未通过"):
        MV.assess(_Client(_out(), _out(), _out()), _corpus(), FactLedger(), AS_OF,
                  judge=Judge({"取向": "致命"}))


def test_审核清单按方法论生成():
    """用户决定 5：取向一条、每条传导一条、遗漏一条；一条传导都没读进来时也列。"""
    assert MV.checklist(MV.parse(_out())) == [
        ("取向", "政策取向"), ("传导", "第1条 政策利率下调"), ("传导", "第2条 流动性宽松"),
        ("遗漏", "宏观因素")]
    assert ("传导", "传导路径（未给出）") in MV.checklist(MV.parse(f"政策取向: 宽松 | 「{Q}」"))


def test_全部用转述_不报未提供依据():
    """转述是合法依据（用户决定 3）。整稿都用转述时没有可核对的引文，不能交给审核者
    「依据核验不过：未提供任何依据」（最终审查 Important 1）。"""
    from datetime import date

    from ir_agent.ledger import FactLedger

    class _Doc:
        title, doc_id, text = "东莞证券：玻纤行业深度报告", "d1", ""

    class _Corpus:
        docs = [_Doc()]
    P = "转述：综合 东莞证券《玻纤行业深度报告》"
    raw = "\n".join([f"政策取向: 中性 | {P}",
                     f"传导: 政策利率下调 | 融资成本下行→扩产意愿回升 | 利好 | {P}",
                     f"传导: 地产链走弱 | 建材需求下滑→玻纤需求放缓 | 利空 | {P}"])
    v = MV.parse(raw, _Corpus)
    assert v.stance is not None and len(v.channels) == 2
    facts = MV.facts(v, FactLedger(), date(2026, 10, 17), _Corpus)
    assert not [x for x in facts if x.startswith("依据核验不过")], facts
