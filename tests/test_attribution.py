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
def test_各归属标记配上来源才算归属(mk):
    """标记单独出现不构成归属 —— 必须在引文紧邻处点出来源。"""
    assert A.attributed_quotes(f"{mk}「{ANNUAL[:10]}」") == []
    assert A.attributed_quotes(
        f"{mk}年报「{ANNUAL[:10]}」") == [ANNUAL[:10]]


# ── 收窄触发条件: 长电那一版研报就是被这里挡死的 ─────────────

def test_术语加引号不算引述():
    """**实测踩到，代价是整篇研报没跑出来。** 最初的判据是「同句内出现过
    归属标记」，于是:

        据同业数据显示，公司毛利率低于「行业中位数」，主因产品结构

    「行业中位数」被判成捏造的引述。中文研报大量用「」标注术语，这条判据
    把它们全误伤，连挂三次。"""
    assert A.attributed_quotes(
        "据同业数据显示，公司毛利率低于「行业中位数」，主因产品结构") == []
    assert A.attributed_quotes(
        "据宏观定性判断，当前指向「内需驱动的宽松环境」") == []


def test_点出来源才查():
    assert A.attributed_quotes("据年报披露，「主营封装测试」") == ["主营封装测试"]
    assert A.attributed_quotes("西南证券研报指出，「库存去化」") == ["库存去化"]


def test_本报告不是外部来源():
    """「本报告认为…」引出的是本文自己的判断。"""
    assert A.attributed_quotes("本报告认为公司具备「有效规模」优势") == []


def test_来源离得远则不算紧邻():
    """来源在句首、引文在句尾、中间隔着大段自己的推理 —— 那个引号
    多半是术语。"""
    far = ("据年报所述，公司在先进封装领域持续投入并已形成一定客户基础，"
           "其竞争优势可归纳为「有效规模」")
    assert A.attributed_quotes(far) == []


def test_驳回信息给出逃生口():
    """**必须说清怎么改才能通过，否则约束不可满足。** 多数误报的正确修法
    是去掉引号，而不是去找一句真引文 —— 不说这一点，模型会一直去找。"""
    p = A.audit("据研报指出，「行业景气度已全面回升」。", _ev())
    assert "去掉引号" in p[0]
    p2 = A.audit("据研报指出，「行业景气度已全面回升」。", Evidence())
    assert "去掉引号" in p2[0]


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


def test_机构名豁免不牵连引文核验():
    """分析券商时同业名单会豁免掉几十个机构名 —— 那是刻意放宽，代价是
    机构名这一项失去约束。此时**引文核验必须仍然咬得住**，否则就是开了
    个口子: 名字随便点，内容也随便编。"""
    body = "据中信证券研报指出，「行业景气度已全面回升」。"
    p = A.audit(body, _ev(peers=("中信证券",)))
    assert len(p) == 1 and "找不到" in p[0]
    assert "中信证券" not in p[0].split("可引述的来源")[0][:20]


def test_机构名的前缀不粘连():
    """最自然的写法就是「据西南证券研报」。若把「据西南证券」当作机构名，
    它与语料库里的「西南证券」永远对不上 —— 正确归属的句子反被判捏造，
    重试也修不好。实测踩到。"""
    assert A.named_sources("据西南证券研报，需求承压") == ["西南证券"]
    assert A.named_sources("根据中信证券研报指出") == ["中信证券"]
    assert A.audit("据西南证券研报显示，「终端库存得到有效去化」。", _ev()) == []


def test_同一机构的不同写法能对上():
    """语料库记「国泰君安」，正文写「国泰君安证券研究所」——
    全等比较对不上，又是一条修不好的驳回。"""
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="R2", url="https://x.cn/r2.html", title="t",
                   publisher="国泰君安", tier=Tier.SELLSIDE,
                   published=date(2026, 8, 1), retrieved=AS_OF, text=EXT))
    ev = Evidence(annual=ANNUAL, corpus=c)
    assert A.audit("据国泰君安证券研究所的研报测算，需求回暖。", ev) == []
