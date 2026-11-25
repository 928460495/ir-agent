"""NVDA 美股实跑（2026-11-25）发现的问题。不联网。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources import conclusions as C
from ir_agent.sources.external import Corpus, Document, Tier
from ir_agent.sources.openweb import Fetched

D = Decimal
AS_OF = date(2026, 11, 25)


# ── 1 英文页面不再被当成打不开 ─────────────────────────────────

EN = ("Nominal 10-year TIPS yields rose to 2.79 percent on November 23, the Treasury said, as investors "
      "priced in further policy tightening by the Federal Reserve over the coming months.")


def _open(monkeypatch, text, claim="实际收益率 2.79%"):
    monkeypatch.setattr(C, "fetch", lambda u, **k: Fetched(final_url=u, text=text, meta_published=None))
    c = C.Conclusion(factor_key="k", claim=claim, articles=[C.Article(
        no="1", url="https://home.treasury.gov/x", publisher="UST", published=AS_OF)])
    C.open_articles([c])
    return c


def test_英文正文算打开并能核对数字(monkeypatch):
    c = _open(monkeypatch, EN)
    assert c.articles[0].opened
    assert c.marks[0].found_in == ("1",)


@pytest.mark.parametrize("text", [
    "Just a moment... Enable JavaScript and cookies to continue. Checking your browser before accessing the site. "
    "This process is automatic. Your browser will redirect to your requested content shortly. Please allow up to five seconds.",
    "Access Denied You don't have permission to access this page on this server. Reference #18.5f3 Please contact the "
    "site owner if you believe this is an error and include the reference number shown above in your message.",
    "Please verify you are a human. Press and hold the button below to confirm you are a human and not a bot before "
    "continuing to the page that you have requested from this web site today.",
    "x9d8f7 " + "QmFzZTY0" * 200,
    "",
])
def test_反爬页与无正文仍判打不开(monkeypatch, text):
    a = _open(monkeypatch, text).articles[0]
    assert not a.opened and "无从比对" in a.why_not


def test_长正文里偶然出现反爬措辞不误伤(monkeypatch):
    a = _open(monkeypatch, EN * 20 + " The bank said access denied errors had risen.").articles[0]
    assert a.opened


# ── 3 净负债率计入短期投资 ────────────────────────────────────

def _f(key, v, period="2026FY", unit="美元"):
    return Fact(key=key, value=D(str(v)), unit=unit, period=period, as_of=date(2026, 2, 25), source_id="x",
                method=Method.REPORTED)


def test_净负债率减去短期投资():
    from ir_agent.industry_profile import company_exposure_facts
    led = FactLedger()
    for k, v in (("short_loan", 10), ("long_loan", 20), ("cash_and_equivalents", 30),
                 ("trading_finasset", 100), ("equity_attr_parent", 400)):
        led.put(_f(k, v))
    got = {f.key: f.value for f in company_exposure_facts(led, "2026FY", AS_OF)}
    assert got["company.net_debt_ratio"] == D("-0.25")            # (30 − 30 − 100) ÷ 400


# ── 6 审核引文核对：稿子原句、省略号 ───────────────────────────

def _note(why, hay="", draft=""):
    from ir_agent import methodology_review as R
    v = R.Verdict("护城河", "第1条", "提醒", why, suggest="")
    return R.annotate_quotes([v], hay=hay, ledger=FactLedger(), as_of=AS_OF, draft=draft)[0].quote_note


def test_引稿子自己的话不算找不到():
    assert _note("「承担上游涨价的风险」没有引文", draft="本公司同时也承担上游涨价的风险。") == ""


def test_引稿子时跨占位符也能对上():
    assert _note("「期限利差为正，是短端被推高」", draft="期限利差 [[macro.term_spread@2026-11-24]] 为正，是短端被推高后") == ""


def test_省略号截短的引文按片段核对():
    hay = "The large and growing number of developers and installed base across our platforms strengthens"
    assert _note("「The large and growing number of developers…」", hay=hay) == ""
    assert _note("「The large and growing number … strengthens」", hay=hay) == ""


def test_编造的引文仍标找不到():
    assert "找不到" in _note("「公司已与客户签订十年长约」", hay="别的内容", draft="别的稿子")
    assert "找不到" in _note("「The large and growing … fabricated ending」", hay="The large and growing number")


def test_省略号片段必须按原文顺序():
    assert "找不到" in _note("「strengthens our ecosystem … The large and growing」",
                          hay="The large and growing number strengthens our ecosystem")


# ── 5 战略稿逐条回应重大公告 ─────────────────────────────────

def _doc(title, when, text, rank=1):
    return Document(doc_id=title, url=f"https://x/{title}", title=title, publisher="某公司",
                    tier=Tier.COMPANY, published=when, retrieved=AS_OF, text=text, prompt_limit=8000, rank=rank)


def _corpus(*docs):
    c = Corpus(as_of=AS_OF)
    for d in docs:
        c.add(d)
    return c


INV, BRIEF = "关于投资建设年产1200万平方米覆铜板项目的公告", "2026年10月份生猪销售简报"


def _events_corpus():
    return _corpus(_doc(BRIEF, date(2026, 11, 6), "a"), _doc("2026年9月份生猪销售简报", date(2026, 10, 8), "b"),
                   _doc(INV, date(2026, 9, 1), "投资总额若干"), _doc("关于担保的公告", date(2026, 10, 1), "d", rank=2),
                   _doc("关于收购股权的公告", date(2026, 1, 1), "超出六个月"))


def test_重大公告清单_第一档且同类月报只留最新():
    from ir_agent import strategy
    assert [d.title for d in strategy.material_events(_events_corpus())] == [BRIEF, INV]


def test_提示词列出公告与正文():
    from ir_agent import strategy
    assert INV in (txt := strategy.events_block(_events_corpus())) and "投资总额若干" in txt
    assert strategy.events_block(_corpus()) == ""


def test_没回应的公告交审核者并进检查单():
    from ir_agent import strategy
    c = _events_corpus()
    v = strategy.parse(f"公告: {INV} | 采用 | 扩产影响收入增速", c)
    fs = strategy.facts(v, FactLedger(), "", AS_OF, c)
    assert any("没有回应" in x and BRIEF in x and INV not in x for x in fs)
    ev = [s for k, s in strategy.checklist(v) if k == "公告"]
    assert len(ev) == 2 and len(set(ev)) == 2
    assert "公告" in strategy.KINDS


def test_不采用须写理由_处理方式须是规定的词():
    from ir_agent import strategy
    c = _events_corpus()
    v = strategy.parse(f"公告: {INV} | 不采用 |\n公告: {BRIEF} | 忽略 | 无关", c)
    notes = "；".join(v.read_notes + strategy.facts(v, FactLedger(), "", AS_OF, c))
    assert "没写" in notes and "忽略" in notes


def test_没有重大公告时检查单与摘要不变():
    from ir_agent import strategy
    v = strategy.parse("护城河: 无形资产 | 宽 | 「x」", _corpus())
    assert not [1 for k, _ in strategy.checklist(v) if k == "公告"]
    assert "重大公告" not in v.summary()


def test_回应进摘要且不带账本外数字():
    from ir_agent import strategy
    from ir_agent.citation import audit_bare_numbers
    v = strategy.parse(f"公告: {INV} | 采用 | 扩产影响收入增速\n公告: {BRIEF} | 不采用 | 与估值无关", _events_corpus())
    s = v.summary()
    assert "覆铜板项目" in s and "扩产影响收入增速" in s and "与估值无关" in s
    assert audit_bare_numbers(s) == []


# ── 1（续）有结论没出处的跟踪指标，重跑时再搜 ─────────────────

def test_无出处的跟踪指标重跑时再检索(monkeypatch):
    from ir_agent import macro_track as MT, macro_view as MV
    calls = []

    def fake(client, label, key, industry, as_of, us=False):
        calls.append(label)
        return C.Searched(items=[], dropped=[])
    monkeypatch.setattr(C, "search", fake)
    view = SimpleNamespace(tracks=[SimpleNamespace(indicator="甲", driver="d", falsifier="f"),
                                   SimpleNamespace(indicator="乙", driver="d", falsifier="f")])
    done = MT.TrackResult(items=[MT.TrackItem("甲", "d", "f", "有结论", []),
                                 MT.TrackItem("乙", "d", "f", "有结论", ["https://x"])])
    MT.track(object(), view, industry="x", as_of=AS_OF, corpus=None, us=True, done=done)
    assert calls == ["甲"]



# ── 终审（2026-11-25）──────────────────────────────────────────

@pytest.mark.parametrize("a,b", [("关于对外投资的公告", "关于对外投资的公告"),
                                 ("关于签订重大合同的公告（一）", "关于签订重大合同的公告（二）")])
def test_I3_通用标题的不同公告不被去重(a, b):
    from ir_agent import strategy
    c = _corpus(_doc(a, date(2026, 10, 1), "x"), _doc(b, date(2026, 8, 1), "y"))
    assert len(strategy.material_events(c)) == 2


@pytest.mark.parametrize("head", [
    "第1条 中国巨石关于巨石集团有限公司与巨石集团香港有限公司共同增资的公告",     # 补全了截断的标题
    "第1条 《中国巨石关于巨石集团有限公司与巨石集团香港有限公司》",
    "第1条",
    "第1条 中国巨石关于（半角）(2026-11-12) 的公告：补充",
])
def test_I5_公告条目按序号认(head):
    from ir_agent import methodology_review as R
    exp = [("公告", "第1条 中国巨石关于巨石集团有限公司与巨石集团香港有限公"), ("公告", "第2条 另一条公告"), ("自洽", "x")]
    vs = R.parse(f"[公告] {head}：提醒　理由　建议：补\n[公告] 第2条 另一条公告：通过\n[自洽] x：通过", exp,
                 kinds=("公告", "自洽"))
    assert [v.level for v in vs] == ["提醒", "通过", "通过"]


def test_I5_撰写者标题全半角与竖线():
    from ir_agent import strategy
    piped, half = "关于收购甲|乙股权的公告（2026）", "关于对外投资的公告（一）"
    c = _corpus(_doc(piped, date(2026, 10, 1), "x"), _doc(half, date(2026, 9, 1), "y"))
    v = strategy.parse(f"公告: 关于对外投资的公告 (一) | 采用 | 影响一\n公告: {piped} | 不采用 | 理由二", c)
    assert [(e.title, e.decision, e.note) for e in v.events] == [(half, "采用", "影响一"), (piped, "不采用", "理由二")]


def test_M6_正文里的reCAPTCHA页脚不误伤(monkeypatch):
    assert _open(monkeypatch, EN + " This site is protected by reCAPTCHA and the Google Privacy Policy.").articles[0].opened


def test_M7_省略号片段太短或相距太远不算():
    far = "revenue grew. " + "filler words here. " * 200 + "decline in guidance"
    assert "找不到" in _note("「revenue grew … decline in guidance」", hay=far)
    assert "找不到" in _note("「公司…毛利率…下降」", hay="公司本年毛利率有所下降")


def test_M9_重搜失败时保留上轮结论(monkeypatch):
    from ir_agent import macro_track as MT

    def boom(*a, **k):
        raise RuntimeError("超时")
    monkeypatch.setattr(C, "search", boom)
    view = SimpleNamespace(tracks=[SimpleNamespace(indicator="甲", driver="d", falsifier="f")])
    res = MT.track(object(), view, industry="x", as_of=AS_OF, corpus=None, us=True,
                   done=MT.TrackResult(items=[MT.TrackItem("甲", "d", "f", "上轮结论", [])]))
    assert res.items[0].conclusion == "上轮结论" and res.failed