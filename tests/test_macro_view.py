"""宏观定性: 政策取向与传导路径。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import macro_view as MV
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.macro_view import Impact, MacroViewFailed, Stance
from ir_agent.sources.external import Corpus, Document, Tier

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


def test_取向取值受限():
    with pytest.raises(MacroViewFailed, match="宽松/中性/收紧"):
        MV.parse(f"政策取向: 偏松 | 「{Q}」")


def test_影响取值受限():
    with pytest.raises(MacroViewFailed, match="利好/中性/利空"):
        MV.parse(f"传导: a | b | 略有帮助 | 「{Q}」")


def test_缺依据被驳回():
    with pytest.raises(MacroViewFailed, match="缺少可核验的依据"):
        MV.parse("政策取向: 宽松 | 我的判断")


# ── 传导路径是重点 ───────────────────────────────────────────

def test_未给取向被驳回():
    """「中性」也是判断，须显式说出。"""
    v = MV.parse(_out(stance=None))
    with pytest.raises(MacroViewFailed, match="中性.*也是判断"):
        MV.check(v, FactLedger(), DOC, AS_OF, corpus=_corpus())


def test_传导少于两条被驳回():
    """「政策转向宽松」写在哪篇研报里都成立，对判断本标的没有帮助 ——
    有帮助的是那条链。"""
    v = MV.parse(_out(channels=[f"传导: a | b | 利好 | 「{Q}」"]))
    with pytest.raises(MacroViewFailed, match="至少 2 条|等于没写"):
        MV.check(v, FactLedger(), DOC, AS_OF, corpus=_corpus())


def test_合法输入通过():
    MV.check(MV.parse(_out()), FactLedger(), DOC, AS_OF, corpus=_corpus())


def test_引文必须出自资料():
    v = MV.parse(_out(stance="宽松", channels=[
        "传导: a | b | 利好 | 「央行大幅加息」",
        f"传导: c | d | 利好 | 「{Q}」"]))
    with pytest.raises(MacroViewFailed, match="不可核验|找不到"):
        MV.check(v, FactLedger(), "", AS_OF, corpus=_corpus())


def test_出处由代码判定():
    from ir_agent.valuation.basis import BasisKind

    v = MV.parse(_out())
    MV.check(v, FactLedger(), "", AS_OF, corpus=_corpus())
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
    MV.assess(c, _corpus(), FactLedger(), AS_OF)
    assert "写不出链条的政策判断等于没写" in c.prompts[0]


def test_无外部宏观资料时明说只能由读数推断():
    txt = MV.for_prompt(Corpus(as_of=AS_OF))
    assert "只能由读数推断" in txt


def test_宏观读数以占位符形式带入():
    txt = MV.for_prompt(_corpus(), macro_prompt="PMI [[macro.pmi@2026-08]]")
    assert "[[macro.pmi@2026-08]]" in txt


def test_摘要安全无裸数字():
    from ir_agent.citation import audit_bare_numbers
    v, _ = MV.assess(_Client(_out()), _corpus(), _rates("0.020", "0.026"), AS_OF)
    assert v.conflicts
    assert audit_bare_numbers(v.summary()) == []


def test_驳回后重试():
    bad = _out(channels=[f"传导: a | b | 利好 | 「{Q}」"])
    c = _Client(bad, _out())
    MV.assess(c, _corpus(), FactLedger(), AS_OF)
    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]


def test_连续失败则拒绝出结论():
    bad = _out(stance=None)
    with pytest.raises(MacroViewFailed, match="连续"):
        MV.assess(_Client(bad, bad, bad), _corpus(), FactLedger(), AS_OF)
