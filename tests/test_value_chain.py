"""产业链与行业真实规模。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import value_chain as VC
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.external import Corpus, Document, Tier
from ir_agent.value_chain import ChainFailed, Power, Role

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2025FY"

Q_UP = "上游高粱小麦由数万家农户分散种植，采购集中度低"
Q_DOWN = "经销商需先款后货，公司对渠道保持较强掌控"
Q_SIZE = "2025年规模以上白酒企业营业收入7803亿元"
DOC = f"{Q_UP}。{Q_DOWN}。{Q_SIZE}。"


def _corpus(text=DOC, published=date(2026, 3, 1)):
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="R1", url="https://data.eastmoney.com/report/info/R1.html",
                   title="白酒行业深度", publisher="甲证券", tier=Tier.SELLSIDE,
                   published=published, retrieved=AS_OF, text=text))
    return c


def _led(**kv):
    led = FactLedger()
    for k, v in kv.items():
        led.put(Fact(key=k.replace("__", "."), value=D(str(v)), unit="ratio",
                     period=P, as_of=date(2025, 1, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


def _out(links=None, powers=None, size=True):
    links = links if links is not None else [
        f"环节: 上游 | 高粱与小麦种植 | 采购分散，本公司是少数大买家 | 「{Q_UP}」",
        f"环节: 本环节 | 白酒酿造与灌装 | 本公司所处环节 | 「{Q_UP}」",
        f"环节: 下游 | 经销商与终端 | 先款后货 | 「{Q_DOWN}」"]
    powers = powers if powers is not None else ["议价: 上游 | 强", "议价: 下游 | 强"]
    s = [f"行业规模: 规模以上白酒企业营业收入 | 7803 | 亿元 | 2025FY | 「{Q_SIZE}」"] if size else []
    return "\n".join([*links, *powers, *s])


class _Client:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


# ── 解析 ─────────────────────────────────────────────────────

def test_解析三部分():
    v = VC.parse(_out())
    assert [x.role for x in v.links] == [Role.UPSTREAM, Role.FOCAL, Role.DOWNSTREAM]
    assert v.power[Role.UPSTREAM] is Power.STRONG
    assert v.size.value == D("7803") and v.size.period == "2025FY"


def test_未知环节位置被驳回():
    with pytest.raises(ChainFailed, match="未知的环节位置"):
        VC.parse(f"环节: 中游 | x | y | 「{Q_UP}」")


def test_本环节没有议价对象():
    with pytest.raises(ChainFailed, match="无议价对象"):
        VC.parse("议价: 本环节 | 强")


def test_行业规模无数值被驳回():
    with pytest.raises(ChainFailed, match="缺少数值"):
        VC.parse(f"行业规模: 口径 | 若干 | 亿元 | 2025FY | 「{Q_SIZE}」")


def test_环节缺依据被驳回():
    with pytest.raises(ChainFailed, match="缺少可核验的依据"):
        VC.parse("环节: 上游 | 种植 | 分散 | 我的判断")


# ── 链条必须连起来 ───────────────────────────────────────────

@pytest.mark.parametrize("drop", list(Role))
def test_缺任一环即驳回(drop):
    """链条断了就谈不上「完整产业链」，也无从判断议价能力从何而来。"""
    keep = [f"环节: {r.value} | n | note | 「{Q_UP}」" for r in Role if r is not drop]
    v = VC.parse(_out(links=keep, size=False))
    with pytest.raises(ChainFailed, match=drop.value):
        VC.check(v, _led(), DOC, AS_OF, corpus=_corpus())


def test_议价须对上下游都给():
    v = VC.parse(_out(powers=["议价: 上游 | 强"], size=False))
    with pytest.raises(ChainFailed, match="上游与对下游"):
        VC.check(v, _led(), DOC, AS_OF, corpus=_corpus())


# ── 依据核验与出处判定 ───────────────────────────────────────

def test_引文出自外部资料时自动标为外部():
    from ir_agent.basis import BasisKind

    v = VC.parse(_out())
    VC.check(v, _led(), "", AS_OF, corpus=_corpus())
    b = v.links[0].bases[0]
    assert b.kind is BasisKind.EXTERNAL and b.ref.endswith("R1.html")


def test_编造的环节被驳回():
    """产业链看起来完整但其中一环是编的，比缺一环更糟。"""
    bad = [f"环节: 上游 | 包装材料 | 玻璃瓶供应充裕 | 「本行业玻璃瓶产能严重过剩」",
           f"环节: 本环节 | 酿造 | x | 「{Q_UP}」",
           f"环节: 下游 | 经销 | y | 「{Q_DOWN}」"]
    v = VC.parse(_out(links=bad, size=False))
    with pytest.raises(ChainFailed, match="不可核验|找不到"):
        VC.check(v, _led(), "", AS_OF, corpus=_corpus())


def test_发布日晚于as_of的研报进不了语料库():
    with pytest.raises(Exception):
        _corpus(published=date(2026, 12, 1))


# ── 与账本交叉印证 ───────────────────────────────────────────

def test_对上游议价强但付款比同业还快():
    """说能占用上游资金，可应付天数低于同业中位数 —— 方向相反。"""
    led = _led(payable_days__percentile="0.17")
    c = VC.cross_check(VC.parse(_out()), led, P, AS_OF)
    assert len(c) == 1 and "上游" in c[0] and "方向相反" in c[0]
    assert "[[payable_days.percentile@2025FY]]" in c[0]


def test_对下游议价强且收款快则无矛盾():
    led = _led(payable_days__percentile="0.80",
               receivable_days__percentile="0.05")
    assert VC.cross_check(VC.parse(_out()), led, P, AS_OF) == []


def test_只在方向相反时才报不另设阈值():
    """「相差多少才算显著」在材料里没有依据，因此不判 ——
    只报方向相反，把分位数摆出来让读者掂量。"""
    assert VC.MEDIAN == D("0.5")
    led = _led(payable_days__percentile="0.51")
    assert VC.cross_check(VC.parse(_out()), led, P, AS_OF) == []


def test_不声称强势时不做交叉检查():
    v = VC.parse(_out(powers=["议价: 上游 | 弱", "议价: 下游 | 均衡"]))
    assert VC.cross_check(v, _led(payable_days__percentile="0.1"), P, AS_OF) == []


def test_账本无该分位数时跳过():
    assert VC.cross_check(VC.parse(_out()), FactLedger(), P, AS_OF) == []


# ── 行业规模落账本 ───────────────────────────────────────────

def test_外部抽取的规模按抽取件降权():
    """由 LLM 从文本抽取，须 method=EXTRACTED、confidence<1，
    下游据此降权并提示复核 —— 与年报 PDF 抽数同一条路。"""
    f = VC.parse(_out()).size.to_fact("R1", AS_OF)
    assert f.method is Method.EXTRACTED and f.confidence < 1.0
    assert f.key == "industry.size_external" and f.value == D("7803")


def test_没有外部规模时明说上市公司口径高估市占率():
    """此前行业规模 = A股上市公司营收之和，分母系统性偏小、
    市占率系统性偏高。缺口径时必须说出来。"""
    v = VC.parse(_out(size=False))
    assert "非上市企业未计入" in v.summary() and "高估" in v.summary()


# ── 提示词与端到端 ───────────────────────────────────────────

def test_提示词禁止凭常识补全():
    c = _Client(_out())
    VC.analyse(c, _corpus(), _led(), P, AS_OF)
    assert "不要凭常识补全" in c.prompts[0] and "比缺一环更糟" in c.prompts[0]


def test_无外部资料时提示词明说覆盖有限():
    txt = VC.for_prompt(Corpus(as_of=AS_OF), _led(), P, AS_OF)
    assert "只能依据年报自述" in txt


def test_提示词带上可交叉印证的分位数():
    txt = VC.for_prompt(_corpus(), _led(payable_days__percentile="0.2"),
                        P, AS_OF)
    assert "[[payable_days.percentile@2025FY]]" in txt


def test_驳回后重试并带回原因():
    bad = _out(powers=["议价: 上游 | 强"], size=False)
    c = _Client(bad, _out())
    VC.analyse(c, _corpus(), _led(), P, AS_OF)
    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]


def test_连续失败则拒绝出结论():
    bad = _out(links=[f"环节: 上游 | x | y | 「查无此句」"], size=False)
    with pytest.raises(ChainFailed, match="连续"):
        VC.analyse(_Client(bad, bad, bad), _corpus(), _led(), P, AS_OF)


def test_端到端带出矛盾():
    v, _ = VC.analyse(_Client(_out()), _corpus(),
                      _led(payable_days__percentile="0.17"), P, AS_OF)
    assert v.conflicts and "⚠" in v.summary()
