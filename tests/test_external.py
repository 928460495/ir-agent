"""外部资料语料库与卖方研报。用例来自实测踩到的情况。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from ir_agent.sources import reports as R
from ir_agent.sources.external import (Corpus, Document, OutOfWindow, Tier,
                                       TIER_RULE)

D = Decimal
AS_OF = date(2026, 9, 7)
TXT = "26H1系列酒实现收入129.3亿元，同比-6%，终端库存得到有效去化。"


def _doc(published=date(2026, 8, 21), url="https://data.eastmoney.com/report/info/A1.html",
         text=TXT, tier=Tier.SELLSIDE, title="2026年中报点评"):
    return Document(doc_id="A1", url=url, title=title, publisher="西南证券",
                    tier=tier, published=published, retrieved=AS_OF, text=text)


# ── 时点守卫 ─────────────────────────────────────────────────

def test_发布日晚于as_of的文档被拒():
    """拿未来的研报分析过去 —— 与账本的 LookAheadError 是同一件事。"""
    c = Corpus(as_of=AS_OF)
    with pytest.raises(OutOfWindow):
        c.add(_doc(published=AS_OF + timedelta(days=1)))


def test_批量抓取时越界的记下来而非中断():
    c = Corpus(as_of=AS_OF)
    assert c.add_if_visible(_doc()) is True
    assert c.add_if_visible(_doc(published=date(2026, 12, 1))) is False
    assert len(c.docs) == 1 and len(c.skipped) == 1
    assert str(AS_OF) in c.summary()


# ── 引文核验: EXTERNAL 不再是免检通道 ────────────────────────

def test_引文必须出自实际抓回的文档():
    """此前 EXTERNAL 注释写着「无法在本地核验」—— 一旦放开外部资料，
    那就是整套依据体系的漏斗。"""
    c = Corpus(as_of=AS_OF)
    c.add(_doc())
    assert c.verify("终端库存得到有效去化") == ""
    assert "找不到" in c.verify("行业景气度显著回升")


def test_忽略空白差异():
    c = Corpus(as_of=AS_OF)
    c.add(_doc())
    assert c.verify("终端库存 得到\n有效去化") == ""


def test_归因到错误的文档也要拦():
    """引文确实存在、却挂在另一篇名下 —— 读者顺 URL 去查会查不到。"""
    c = Corpus(as_of=AS_OF)
    c.add(_doc())
    p = c.verify("终端库存得到有效去化", url="https://example.com/other")
    assert "归因错误" in p


def test_空语料库一律不通过():
    assert "找不到" in Corpus(as_of=AS_OF).verify("任何一句话")


# ── MEDIA 级来源：警告，不拒绝 ───────────────────────────────
#
# `TIER_RULE[Tier.MEDIA]` 写死「仅作线索，不得作为依据」，但这是一条
# 关于分级的规矩，不是关于「引文是否逐字出现」的规矩——verify() 仍然
# 要对 MEDIA 文档的引文放行（核验该过就该过），只是要把这件事记下来，
# 交给上层（full.py）去警告，而不是让它隐身。见 Corpus.media_cited
# 字段注释。


def test_引文出自MEDIA文档仍通过但记入media_cited():
    c = Corpus(as_of=AS_OF)
    c.add(_doc(tier=Tier.MEDIA, url="https://finance.example.com/x"))
    assert c.verify("终端库存得到有效去化") == ""
    assert len(c.media_cited) == 1


def test_引文出自非MEDIA文档不记入media_cited():
    c = Corpus(as_of=AS_OF)
    c.add(_doc(tier=Tier.SELLSIDE))
    assert c.verify("终端库存得到有效去化") == ""
    assert c.media_cited == []


def test_同一条MEDIA引文被重复核验多次只记一次():
    """真实触发场景：strategy.assess/value_chain.analyse/macro_view.assess
    都以 max_attempts=3 重试，每次 check() 都重跑一遍 validate() ——
    也就是重跑一遍 verify()。模型第一次交的草稿因某个不相关的问题被
    整体打回重试，第二次交的草稿即便引用的是同一条 MEDIA 引文，也会
    让 verify() 再命中一次；不去重的话 media_cited 数的是"这条引文
    经历了几次尝试"，不是"最终定稿依赖了几条 MEDIA 依据"，会随重试
    次数虚高——这里模拟三次重试全部核验同一句引文，确认只记一次。"""
    c = Corpus(as_of=AS_OF)
    c.add(_doc(tier=Tier.MEDIA, url="https://finance.example.com/x"))
    for _ in range(3):  # 模拟 max_attempts=3 的重试循环
        assert c.verify("终端库存得到有效去化") == ""
    assert len(c.media_cited) == 1


def test_同一文档两条不同MEDIA引文分别计数不被合并():
    """去重键必须是 (文档, 引文)，不能只按文档去重。同一篇文档支撑两条
    不同的判断，是比只支撑一条判断更大的风险敞口——压成按文档去重会把
    这个差异抹掉，让「N 处」比实际引用条数少报。"""
    text = ("26H1系列酒实现收入129.3亿元，同比-6%，终端库存得到有效去化。"
           "次高端价格带持续承压，经销商打款意愿不足。")
    c = Corpus(as_of=AS_OF)
    c.add(_doc(tier=Tier.MEDIA, url="https://finance.example.com/x", text=text))
    assert c.verify("终端库存得到有效去化") == ""
    assert c.verify("次高端价格带持续承压") == ""
    assert len(c.media_cited) == 2


# ── 来源分级 ─────────────────────────────────────────────────

@pytest.mark.parametrize("t", list(Tier))
def test_每级都有明确的可引用范围(t):
    assert TIER_RULE[t].strip()


def test_卖方的预测不得作为依据():
    """这条是整个模块的立身之本 —— 拿券商的盈利预测论证自己的盈利预测，
    结论绕一圈回到起点，还多了一层「有依据」的外衣。"""
    rule = TIER_RULE[Tier.SELLSIDE]
    assert "不得作为本报告任何假设的依据" in rule
    assert "对照" in rule


# ── 财年锚定 ─────────────────────────────────────────────────

def _row(pub, this=None, nxt=None, org="甲证券", rating="买入"):
    return {"infoCode": "X" + pub, "title": "点评", "orgSName": org,
            "publishDate": pub + " 00:00:00.000", "emRatingName": rating,
            "ratingChange": 3, "industryName": "白酒",
            "predictThisYearEps": this or "", "predictNextYearEps": nxt or "",
            "predictNextTwoYearEps": "", "predictThisYearPe": "",
            "predictNextYearPe": "", "predictNextTwoYearPe": ""}


def test_本年预测按发布日折算到绝对财年():
    """实测: 2024-06 发布的「本年 96.47」是 FY2024，2026-08 发布的
    「本年 69.83」是 FY2026（标题即「2026年中报点评」）。把两者混进
    同一个中位数，得到的是把不同年度平均起来的无意义数字，且不报错。"""
    a = R._row_to_report(_row("2024-06-04", this="96.47"))
    b = R._row_to_report(_row("2026-08-21", this="69.83", nxt="75.82"))
    assert a.eps == {2024: D("96.47")}
    assert b.eps == {2026: D("69.83"), 2027: D("75.82")}


def test_空预测不记为零():
    r = R._row_to_report(_row("2026-08-21", this="", nxt="0"))
    assert r.eps == {}


def test_发布日不可解析则丢弃():
    assert R._row_to_report(_row("", this="1")) is None


# ── 一致预期 ─────────────────────────────────────────────────

def _reports(*specs):
    return [R._row_to_report(_row(p, this=e, org=o))
            for p, e, o in specs]


def _fy26(*specs):
    """构造一组对 FY2026 的预测。**2025 年发布的要走「次年」字段** ——
    真实数据里正是如此，这也是财年锚定的另一面。"""
    out = []
    for pub, eps, org in specs:
        pub_y = int(pub[:4])
        out.append(R._row_to_report(
            _row(pub, this=eps if pub_y == 2026 else "",
                 nxt=eps if pub_y == 2025 else "", org=org)))
    return out


def test_单家券商不构成一致预期():
    c = R.consensus(_reports(("2026-08-01", "70", "甲")), AS_OF)
    assert not c.has_data()


def test_中位与离散():
    c = R.consensus(_reports(("2026-08-01", "60", "甲"),
                             ("2026-08-02", "70", "乙"),
                             ("2026-08-03", "80", "丙")), AS_OF)
    e = c.eps[2026]
    assert e.n == 3 and e.median == D("70") and e.low == D("60")
    assert e.high == D("80") and e.spread == D("20") / D("70")


def test_陈旧预测不进当期一致预期():
    """半年前的预测早已被事件推翻，混进来会把中位数往陈旧方向拖。"""
    c = R.consensus(_fy26(("2026-08-01", "68", "甲"),
                          ("2026-08-02", "69", "乙"),
                          ("2025-04-10", "85", "丙"),
                          ("2025-05-10", "84", "丁")), AS_OF)
    assert c.eps[2026].median == D("68.5")
    assert c.n_reports == 2


def test_修正轨迹本身是信息():
    """茅台实测: FY2026 的 EPS 一致预期从 84.80（56家）降到 68.43（42家），
    半年下修 19.3% —— 比某个时点的静态数字更能说明问题。"""
    c = R.consensus(_fy26(("2026-08-01", "68", "甲"),
                          ("2026-08-02", "69", "乙"),
                          ("2025-04-10", "85", "丙"),
                          ("2025-05-10", "84", "丁")), AS_OF)
    prev, now = c.drift[2026]
    assert prev.median == D("84.5") and now.median == D("68.5")


def test_评级分布():
    rs = [R._row_to_report(_row("2026-08-01", this="70", org="甲", rating="买入")),
          R._row_to_report(_row("2026-08-02", this="71", org="乙", rating="买入")),
          R._row_to_report(_row("2026-08-03", this="72", org="丙", rating="持有"))]
    assert R.consensus(rs, AS_OF).ratings == {"买入": 2, "持有": 1}


def test_研报转文档保留发布日与来源级别():
    r = R._row_to_report(_row("2026-08-21", this="69.83"))
    d = R.to_document(r, TXT, AS_OF)
    assert d.tier is Tier.SELLSIDE and d.published == date(2026, 8, 21)
    assert d.retrieved == AS_OF and "西南" not in d.publisher  # 用例里是甲证券
    assert "甲证券" in d.cite() and "2026-08-21" in d.cite()


def test_正文取不到时退回标题():
    """标题本身仍有价值，不因取不到正文而丢弃整条。"""
    r = R._row_to_report(_row("2026-08-21", this="69.83"))
    assert R.to_document(r, "", AS_OF).text == "点评"


# ── EXTERNAL 依据的核验闭环 ──────────────────────────────────

def test_外部依据现在对语料库核验():
    """把 basis.validate 的 EXTERNAL 分支与语料库接上 —— 三类依据
    一视同仁: FACT 对账本、REPORT 对正文、EXTERNAL 对语料库。"""
    from ir_agent.ledger import FactLedger
    from ir_agent.basis import Basis, validate

    c = Corpus(as_of=AS_OF)
    c.add(_doc())
    url = "https://data.eastmoney.com/report/info/A1.html"
    ok = [Basis.external(url, "终端库存得到有效去化", AS_OF)]
    bad = [Basis.external(url, "行业景气度显著回升", AS_OF)]
    assert validate(ok, FactLedger(), "", AS_OF, corpus=c) == []
    assert validate(bad, FactLedger(), "", AS_OF, corpus=c)
    # 不传语料库时保留旧行为（URL 由代码生成、模型碰不到的少数几处）
    assert validate(bad, FactLedger(), "", AS_OF) == []
