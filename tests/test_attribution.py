"""正文的出处核验 —— 三道闸之外的第四道。"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent import attribution as A
from ir_agent.attribution import Evidence
from ir_agent.sources.external import Corpus, Document, Tier

AS_OF = date(2026, 9, 7)
ANNUAL = "公司拥有品质、品牌、工艺、环境、文化组成的五大核心竞争力。"
EXT = "系列酒收入同比下滑，终端库存得到有效去化。"


def _corpus():
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="R1", url="https://x.cn/r1.html", title="中报点评",
                   publisher="西南证券", tier=Tier.SELLSIDE,
                   published=date(2026, 8, 21), retrieved=AS_OF, text=EXT))
    return c


def _ev(annual=ANNUAL, corpus=True, peers=()):
    return Evidence(annual=annual, corpus=_corpus() if corpus else None,
                    peers=peers)


# ── 触发条件要窄 ─────────────────────────────────────────────

def test_强调用的引号不受影响():
    """日常用于强调的「护城河」不跟在归属标记后面。硬性要求处处标注出处
    会造出模型无从修复的驳回项 —— 那正是把重试拖成不可满足约束的原因。"""
    body = "公司具备明显的「护城河」，「飞天」系列是其核心单品。"
    assert A.attributed_quotes(body) == []
    assert A.audit(body, _ev()) == []


def test_归属标记触发核验():
    body = f"据年报披露，「{ANNUAL[:12]}」构成壁垒。"
    assert A.attributed_quotes(body) == [ANNUAL[:12]]


@pytest.mark.parametrize("mk", ["据", "根据", "援引", "披露", "指出", "表示"])
def test_各归属标记都识别(mk):
    assert A.attributed_quotes(f"{mk}「{ANNUAL[:10]}」") == [ANNUAL[:10]]


def test_隔了句号就不算同一句的归属():
    """「据此判断。公司具备「护城河」」—— 归属标记与引号分属两句。"""
    assert A.attributed_quotes("据此判断。公司具备「护城河」优势") == []


# ── 核验 ─────────────────────────────────────────────────────

def test_引自年报的通过():
    assert A.audit(f"据年报披露，「{ANNUAL[:12]}」。", _ev()) == []


def test_引自外部资料的通过():
    assert A.audit(f"据研报指出，「{EXT[:12]}」。", _ev()) == []


def test_查无此句的被拦():
    """捏造一个出处比捏造一个数字容易得多，也更难被发现 ——
    读者不会去查每一句转述。"""
    p = A.audit("据研报指出，「行业景气度已全面回升」。", _ev())
    assert len(p) == 1 and "找不到" in p[0]


def test_驳回信息附上可引来源():
    """清单里只要有一项模型无从修复，整个重试循环就变成不可满足的约束。"""
    p = A.audit("据研报指出，「行业景气度已全面回升」。", _ev())
    assert "公司年报原文" in p[0] and "西南证券" in p[0]


def test_无任何资料时不得引述外部():
    p = A.audit("据研报指出，「行业景气度已全面回升」。",
                Evidence(annual="", corpus=None))
    assert len(p) == 1 and "未取到任何可引述的资料" in p[0]


def test_无资料但不引述外部则通过():
    assert A.audit("公司具备「护城河」。", Evidence()) == []


def test_忽略空白差异():
    assert A.audit(f"据年报披露，「{ANNUAL[:6]} {ANNUAL[6:12]}」。", _ev()) == []


# ── 点名机构 ─────────────────────────────────────────────────

def test_点名未取回的机构被拦():
    p = A.audit("中信证券的研报指出行业将复苏。", _ev())
    assert len(p) == 1 and "中信证券" in p[0] and "并未取回" in p[0]


def test_点名已取回的机构通过():
    assert A.audit("西南证券的研报指出行业承压。", _ev()) == []


def test_作为可比公司提及不受影响():
    """分析中信证券这类券商时，正文本来就会反复出现它的名字 ——
    因为语料库里没有它的研报就判捏造出处，是把正当用法误伤。"""
    body = "中信证券的市净率处于同业低位，其资本充足率高于可比样本。"
    assert A.named_sources(body) == []
    assert A.audit(body, _ev()) == []


def test_同业名单显式豁免():
    body = "中信证券的研报观点与本报告一致。"
    assert A.audit(body, _ev(peers=("中信证券",))) == []


def test_机构名须与研究类词同句():
    assert A.named_sources("公司客户包括华泰证券等金融机构。") == []
    assert A.named_sources("华泰证券分析师认为需求回暖。") == ["华泰证券"]


# ── 与撰写闸门的接线 ─────────────────────────────────────────

def test_接入撰写的第四道闸():
    from ir_agent.analyst import FactCatalog, _violations
    from ir_agent.ledger import FactLedger

    cat = FactCatalog(ledger=FactLedger(), as_of=AS_OF)
    cat.evidence = _ev()
    bad = _violations("据研报指出，「行业景气度已全面回升」。[[a#b@c]]", cat)
    assert any("找不到" in v for v in bad)


def test_未提供证据面时不启用():
    """证据面缺位时保持旧行为，而不是把所有转述一律判为违规。"""
    from ir_agent.analyst import FactCatalog, _violations
    from ir_agent.ledger import FactLedger

    cat = FactCatalog(ledger=FactLedger(), as_of=AS_OF)
    bad = _violations("据研报指出，「行业景气度已全面回升」。[[a#b@c]]", cat)
    assert not any("找不到" in v for v in bad)


def test_修订路径共用同一副闸门():
    """revision.py 复用 _violations —— 否则修订稿可以把撰写时被拦下的
    转述再写回去。"""
    import inspect

    from ir_agent import revision
    assert "_violations" in inspect.getsource(revision.revise_from_verdicts)


def test_提示词讲清规则():
    from ir_agent.analyst import FactCatalog, build_prompt
    from ir_agent.ledger import FactLedger

    cat = FactCatalog(ledger=FactLedger(), as_of=AS_OF)
    cat.evidence = _ev()
    txt = build_prompt(cat, code="600519", period="2025FY")
    assert "西南证券" in txt and "不必处处标注出处" in txt
