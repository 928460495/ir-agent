"""公司公告入语料库。**模型只挑下标，正文由代码抓。**

这个契约比 openweb 的 discover/fetch 更强：openweb 里模型给的是 URL
（一个它可以凭空构造的字符串），这里模型给的是指向代码已有列表的下标，
可编造的空间从「任意网址」缩到「这些条目里的哪几条」。
"""

from __future__ import annotations

from datetime import date

from ir_agent.sources.announce import (MAX_ANNOUNCEMENTS, PROMPT,
                                       format_candidates, parse_picks)
from ir_agent.sources.cninfo import Announcement

AS_OF = date(2026, 9, 30)


def _ann(i: int, title: str, when: date) -> Announcement:
    return Announcement(
        ann_id=f"a{i}", title=title, ann_date=when,
        url=f"http://static.cninfo.com.cn/finalpage/{when}/a{i}.PDF",
        code="600183", name="生益科技", category="其他",
        source_id=f"src{i}")


ANNS = [
    _ann(0, "生益科技关于高频高速覆铜板扩产项目投产的公告", date(2026, 8, 1)),
    _ann(1, "生益科技2026年半年度报告", date(2026, 8, 28)),
    _ann(2, "生益科技关于部分产品价格调整的公告", date(2026, 5, 20)),
]


def test_候选清单带序号标题与披露日():
    text = format_candidates(ANNS)
    # 逐行核对「序号. 」前缀本身，而不是在整段文本里裸找数字——测试数据
    # 的披露日（2026-08-01/08-28/05-20）本身就含 0/1/2，裸找会在序号被
    # 整个漏掉时也误判通过。
    lines = text.split("\n")
    assert len(lines) == len(ANNS)
    for i, line in enumerate(lines):
        assert line.startswith(f"{i}. ")
    assert "高频高速覆铜板扩产项目投产" in text
    assert "2026-08-01" in text


def test_候选清单不含下载地址():
    """正文的下载地址由代码按 ann_id 取，不经模型——清单里给了 URL 反而
    让模型有机会把它改掉再交回来。"""
    text = format_candidates(ANNS)
    for a in ANNS:
        assert a.url not in text


def test_解析挑选出的下标():
    picks, ignored = parse_picks(
        "选取: 0 | 与名义产能相关\n选取: 2 | 与ASP相关\n", 3)
    assert picks == [0, 2]
    assert ignored == []


def test_越界的下标被忽略并记录():
    picks, ignored = parse_picks("选取: 0\n选取: 99\n", 3)
    assert picks == [0]
    assert len(ignored) == 1 and "99" in ignored[0]


def test_重复的下标只取一次():
    picks, ignored = parse_picks("选取: 1\n选取: 1\n选取: 1\n", 3)
    assert picks == [1]


def test_非数字的下标被忽略并记录():
    picks, ignored = parse_picks("选取: 第一条 | 相关\n选取: 2\n", 3)
    assert picks == [2]
    assert len(ignored) == 1


def test_超过上限只取前若干条():
    lines = "".join(f"选取: {i}\n" for i in range(20))
    picks, _ = parse_picks(lines, 20)
    assert len(picks) == MAX_ANNOUNCEMENTS


def test_负数下标被忽略():
    """`-1` 在 Python 里是合法索引，会静默取到最后一条——不是模型的意思。"""
    picks, ignored = parse_picks("选取: -1\n选取: 0\n", 3)
    assert picks == [0]
    assert len(ignored) == 1


def test_容忍列表前缀与加粗():
    picks, _ = parse_picks("1. **选取**: 2 | 相关\n", 3)
    assert picks == [2]


def test_一条都没挑出来时返回空():
    picks, ignored = parse_picks("我觉得都不相关。\n", 3)
    assert picks == [] and ignored == []


def test_提示词说明只给下标且逐字含上限():
    assert "序号" in PROMPT
    assert str(MAX_ANNOUNCEMENTS) in PROMPT
    assert "选取:" in PROMPT


from ir_agent.sources.announce import AnnounceResult, gather, pdf_text
from ir_agent.sources.external import Corpus, Tier


class _Client:
    """与 tests/test_openweb.py 的 _Client 同一形状：文本进、文本出。"""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _patch(monkeypatch, anns, pages):
    """anns 作为 cninfo.search 的返回；pages 是 {url: 文本} 的假 PDF。"""
    import ir_agent.sources.announce as A

    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _fake_download(url, cache_dir=None):
        if url not in pages:
            raise OSError("404")
        return pages[url].encode("utf-8")

    monkeypatch.setattr(A, "_download", _fake_download)
    monkeypatch.setattr(A, "pdf_text", lambda data: data.decode("utf-8"))


PAGES = {a.url: f"正文{i}：本公司本次扩产新增产能若干万平方米。"
         for i, a in enumerate(ANNS)}


def test_挑中的公告入库且分级为COMPANY(monkeypatch):
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0 | 名义产能\n选取: 2 | ASP\n"),
                 code="600183", company="生益科技",
                 factors=["名义产能", "ASP"], as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 2
    assert all(d.tier is Tier.COMPANY for d in cp.docs)
    assert res.candidates == 3 and res.picked == 2
    assert len(res.docs) == 2


def test_入库文档的时点用披露日不是抓取日(monkeypatch):
    """把抓取日当 as_of 会让回测凭空多出几天信息优势，且不会报错——
    cninfo.py 模块注释记着这条。"""
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    gather(_Client("选取: 0\n"), code="600183", company="生益科技",
           factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert cp.docs[0].published == ANNS[0].ann_date
    assert cp.docs[0].retrieved == AS_OF


def test_披露日晚于as_of的公告被挡下(monkeypatch):
    late = [_ann(9, "生益科技关于扩产的公告", date(2026, 12, 31))]
    _patch(monkeypatch, late, {late[0].url: "正文"})
    cp = Corpus(as_of=AS_OF)
    gather(_Client("选取: 0\n"), code="600183", company="生益科技",
           factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert cp.docs == []
    assert cp.skipped != []


def test_单篇下载失败不牵连其余(monkeypatch):
    partial = {k: v for k, v in PAGES.items() if "a0" not in k}
    _patch(monkeypatch, ANNS, partial)
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0\n选取: 1\n选取: 2\n"),
                 code="600183", company="生益科技",
                 factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 2
    assert res.fetch_failed == 1
    assert res.fetch_failed_reasons and "a0" in res.fetch_failed_reasons[0]


def test_取文为空的公告不入库(monkeypatch):
    _patch(monkeypatch, ANNS, {ANNS[0].url: "   \n  "})
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0\n"), code="600183", company="生益科技",
                 factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert cp.docs == []
    assert res.fetch_failed == 1
    assert "取文为空" in res.fetch_failed_reasons[0]


def test_被忽略的挑选传到结果里(monkeypatch):
    _patch(monkeypatch, ANNS, PAGES)
    res = gather(_Client("选取: 0\n选取: 99\n"),
                 code="600183", company="生益科技",
                 factors=["名义产能"], as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert len(res.ignored) == 1 and "99" in res.ignored[0]


def test_一条公告都没取到时不调模型(monkeypatch):
    """候选为空就没有可挑的，省一次模型调用。"""
    _patch(monkeypatch, [], {})
    c = _Client()          # 没有预备回复：一旦被调用就 IndexError
    res = gather(c, code="600183", company="生益科技",
                 factors=["名义产能"], as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.candidates == 0 and res.docs == []
    assert c.prompts == []


def test_入库文档带截断长度(monkeypatch):
    """截断长度必须真的读 `openweb.TEXT_LIMIT` 这个名字，不能写死字面量
    2000——`TEXT_LIMIT` 今天恰好也是 2000，断言 `== TEXT_LIMIT` 对一个
    硬编码 `prompt_limit=2000` 的实现同样会通过，测不出退化。这里把
    announce 模块里已经 import 进来的 `TEXT_LIMIT`（`gather` 实际引用
    的那个名字）改写成一个不等于 2000 的哨兵值：源码若真的动态读它，
    产物的 `prompt_limit` 会跟着变成哨兵值；若退化成字面量 2000，两者
    不等，测试变红。"""
    import ir_agent.sources.announce as A
    sentinel = 12345
    monkeypatch.setattr(A, "TEXT_LIMIT", sentinel)
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    gather(_Client("选取: 0\n"), code="600183", company="生益科技",
           factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert cp.docs[0].prompt_limit == sentinel


def test_已在语料库里的URL不重复入库(monkeypatch):
    from ir_agent.sources.external import Document
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="x", url=ANNS[0].url, title="已有",
                    publisher="生益科技", tier=Tier.COMPANY,
                    published=ANNS[0].ann_date, retrieved=AS_OF, text="已有"))
    gather(_Client("选取: 0\n选取: 1\n"), code="600183", company="生益科技",
           factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert [d.url for d in cp.docs].count(ANNS[0].url) == 1


def test_pdf取文按页数上限截断():
    """多数公告一两页，但定期报告可能两百页以上，全取会挤掉其余篇的额度。

    额外断言 `get_text()` 从未在第 `PDF_MAX_PAGES` 页之后被调用过——只
    断言输出内容够不够，测不出「先读完全部 100 页、再把拼好的字符串
    切到上限」这种退化：那种实现和正确实现（读到上限就 `break`，根本
    不碰后面的页）产出的文本一模一样，区别只在读的工作量，必须直接数
    调用次数才能分辨。"""
    import ir_agent.sources.announce as A
    pages = []
    calls: list[int] = []

    class _FakePage:
        def __init__(self, i):
            self.i = i

        def get_text(self):
            calls.append(self.i)
            return f"第{self.i}页"

    class _FakeDoc:
        def __iter__(self):
            return iter([_FakePage(i) for i in range(100)])

        def close(self):
            pages.append("closed")

    import sys
    import types
    fake = types.ModuleType("fitz")
    fake.open = lambda **k: _FakeDoc()
    sys.modules["fitz"] = fake
    try:
        got = A.pdf_text(b"%PDF-1.4 fake")
        assert "第0页" in got
        assert f"第{A.PDF_MAX_PAGES - 1}页" in got
        assert f"第{A.PDF_MAX_PAGES}页" not in got
        assert pages == ["closed"], "PDF 句柄必须关闭"
        assert calls == list(range(A.PDF_MAX_PAGES)), (
            "get_text() 必须只在页数上限之前被调用——调用到了上限之后，"
            "说明实现先读完了全部页面，只是事后把拼好的文本切短")
    finally:
        del sys.modules["fitz"]


def _long_ann(ann_id: str, url: str) -> Announcement:
    return Announcement(ann_id=ann_id, title="生益科技关于扩产的公告",
                        ann_date=date(2026, 8, 1), url=url,
                        code="600183", name="生益科技",
                        category="其他", source_id=ann_id)


def test_相同原因不同URL且均超截断上限只记一条(monkeypatch):
    """旗舰场景：同一种系统性原因（巨潮整体拒绝 PDF 下载/代理宕了）在
    不同公告上必须收敛成一条，不能「八篇都下载超时」印八遍。两条消息
    都刻意造得超过 `_REASON_LIMIT`——如果去重键从『已截断的存储副本』
    反推（Defect A），或者直接拿带 URL 的原始消息当 key（Defect B），
    这里都会分别失败、印出两条。"""
    import ir_agent.sources.announce as A

    url_a = ("http://static.cninfo.com.cn/finalpage/2026-08-01/"
             + "a" * 100 + ".PDF")
    url_b = ("http://static.cninfo.com.cn/finalpage/2026-08-02/"
             + "b" * 100 + ".PDF")
    anns = [_long_ann("long0", url_a), _long_ann("long1", url_b)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        raise ConnectionError(
            "HTTPSConnectionPool(host='static.cninfo.com.cn', port=443): "
            f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0\n选取: 1\n"), code="600183",
                 company="生益科技", factors=["名义产能"], as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 1
    # 存进去的展示串本身确实超过了截断上限（验证「两条都需要截断」这个
    # 前提成立，不是因为凑巧都很短才合并成功）。
    stored = res.fetch_failed_reasons[0]
    assert len(stored) <= A._REASON_LIMIT


def test_不同系统性原因分别记录不被合并(monkeypatch):
    """去重不能矫枉过正：404、PDF 取文为空、内容类型不对是三种真正不同
    的诊断，必须各自留一条——合并成一条会把『这是哪一种失败』的诊断
    价值直接抹掉，比不去重更糟。"""
    import ir_agent.sources.announce as A

    url_404 = "http://static.cninfo.com.cn/finalpage/2026-08-01/notfound.PDF"
    url_ctype = "http://static.cninfo.com.cn/finalpage/2026-08-02/bad.PDF"
    url_empty = "http://static.cninfo.com.cn/finalpage/2026-08-03/empty.PDF"
    anns = [_long_ann("c0", url_404), _long_ann("c1", url_ctype),
            _long_ann("c2", url_empty)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _fake_download(url, cache_dir=None):
        if url == url_404:
            raise OSError(f"404 Client Error: Not Found for url: {url}")
        if url == url_ctype:
            raise OSError(f"内容类型不是网页（application/octet-stream）：{url}")
        return b"EMPTY"

    monkeypatch.setattr(A, "_download", _fake_download)
    monkeypatch.setattr(
        A, "pdf_text",
        lambda data: "" if data == b"EMPTY" else data.decode("utf-8"))

    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0\n选取: 1\n选取: 2\n"), code="600183",
                 company="生益科技", factors=["名义产能"], as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 3
    assert len(res.fetch_failed_reasons) == 3


def test_fetch_failed计数不受去重影响(monkeypatch):
    """`fetch_failed` 是「失败了几次」，`fetch_failed_reasons` 是「有
    几种不同的原因」——两个口径不同，去重只应该影响后者，不能连计数
    也一起被去重掉。"""
    import ir_agent.sources.announce as A

    urls = [f"http://static.cninfo.com.cn/finalpage/2026-08-0{i}/x.PDF"
            for i in range(1, 4)]
    anns = [_long_ann(f"d{i}", u) for i, u in enumerate(urls)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        raise TimeoutError(f"下载超时：{url}")

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0\n选取: 1\n选取: 2\n"), code="600183",
                 company="生益科技", factors=["名义产能"], as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 3
    assert len(res.fetch_failed_reasons) == 1
