"""公司公告入语料库。**挑选与正文都由代码做，不经模型。**

此前模型从代码给的候选清单里挑下标。现在连这一步也取消了：挑选的唯一
判据曾是方法论预研产出的因子清单，而预研已挪到辩论① 之后 —— 那条依赖会
让判据恒为空。改为按**交易所分类代码**（巨潮 `announcementType`）分档，
因子定向挪到消费端由 `gaps._match_announce` 做全文检索，那时因子已经知道了。

**不按标题认字。** 第一版按标题关键词判（`is_substantive`），生益科技实跑
时入库的 20 篇有 15 篇是程序性噪声：2024 年新《公司法》把「股东大会」改名
「股东会」，标题词表与 `cninfo.classify` 同时失效；而「最近 20 篇」的上限把
每一篇误放的噪声换算成一篇被挤掉的有用公告 —— 投资建设高性能覆铜板项目、
年度业绩快报全被挤掉。分类代码由披露系统按公告性质给出，与措辞无关。
"""

from __future__ import annotations

import errno as _errno
from datetime import date, timedelta

import requests
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.exceptions import MaxRetryError, NewConnectionError

from ir_agent.sources.cninfo import Announcement
from ir_agent.sources.retry import RetryExhausted, with_retry

AS_OF = date(2026, 9, 30)


def _ann(i: int, title: str, when: date, codes: str = "") -> Announcement:
    return Announcement(
        ann_id=f"a{i}", title=title, ann_date=when,
        url=f"http://static.cninfo.com.cn/finalpage/{when}/a{i}.PDF",
        code="600183", name="生益科技", category="其他",
        source_id=f"src{i}", type_codes=codes)


# 类型码照真实响应的形状给（「板块码||报告类码||类型码」）。真实数据里总是有
# 类型码；不给的话按「无类型码」进第二档、不排除 —— 那是另一条测试的事。
ANNS = [
    _ann(0, "生益科技关于高频高速覆铜板扩产项目投产的公告", date(2026, 8, 1),
         "01010503||010113||011705"),
    _ann(1, "生益科技2026年半年度报告", date(2026, 8, 28),
         "01010503||010113||010303"),
    _ann(2, "生益科技关于部分产品价格调整的公告", date(2026, 5, 20),
         "01010503||010113||012399"),
]


from ir_agent.sources.announce import AnnounceResult, gather, pdf_text
from ir_agent.sources.external import Corpus, Tier


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
    res = gather(code="600183", company="生益科技",
                 as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 2
    assert all(d.tier is Tier.COMPANY for d in cp.docs)
    assert res.candidates == 3 and res.picked == 2
    assert len(res.docs) == 2


def test_入库文档的时点用披露日不是抓取日(monkeypatch):
    """把抓取日当 as_of 会让回测凭空多出几天信息优势，且不会报错——
    cninfo.py 模块注释记着这条。"""
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    gather(code="600183", company="生益科技",
           as_of=AS_OF, corpus=cp)
    assert cp.docs[0].published == ANNS[0].ann_date
    assert cp.docs[0].retrieved == AS_OF


def test_披露日晚于as_of的公告被挡下(monkeypatch):
    late = [_ann(9, "生益科技关于扩产的公告", date(2026, 12, 31))]
    _patch(monkeypatch, late, {late[0].url: "正文"})
    cp = Corpus(as_of=AS_OF)
    gather(code="600183", company="生益科技",
           as_of=AS_OF, corpus=cp)
    assert cp.docs == []
    assert cp.skipped != []


def test_单篇下载失败不牵连其余(monkeypatch):
    partial = {k: v for k, v in PAGES.items() if "a0" not in k}
    _patch(monkeypatch, ANNS, partial)
    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183", company="生益科技",
                 as_of=AS_OF, corpus=cp)
    # ANNS 三条里只有 0 和 2 是实质类（1 是半年度报告，按类型筛掉）；
    # a0 下载失败，于是剩 1 篇入库。此前模型会挑 0/1/2 三条，所以这里
    # 原本断言的是 2。
    assert len(cp.docs) == 1
    assert res.fetch_failed == 1
    assert res.fetch_failed_reasons and "a0" in res.fetch_failed_reasons[0]


def test_取文为空的公告不入库(monkeypatch):
    # 只给一条候选，免得另一条的下载失败混进 fetch_failed 的计数里。
    _patch(monkeypatch, [ANNS[0]], {ANNS[0].url: "   \n  "})
    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183", company="生益科技",
                 as_of=AS_OF, corpus=cp)
    assert cp.docs == []
    assert res.fetch_failed == 1
    assert "取文为空" in res.fetch_failed_reasons[0]


def test_按类型筛掉的条数要说得出来(monkeypatch):
    """**替换掉 `test_被忽略的挑选传到结果里`。** 模型挑选那条路已经没有了，
    `ignored`（模型给了但不合法的挑选）于是永远是空的。

    取而代之要能说出的是 `rejected`：读者看到「入库 2 篇」时，要知道另外
    多少篇是被筛掉的、筛掉的理由是**类型**而不是故障 —— 与 `candidates`
    同一条理由。
    """
    _patch(monkeypatch, ANNS, PAGES)
    res = gather(code="600183", company="生益科技",
                 as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.candidates == 3
    assert res.picked == 2
    assert res.rejected == 1, "半年度报告该被按类型筛掉"


def test_候选为空时直接返回(monkeypatch):
    """没有候选就没有可筛的。此前这条测的是「省一次模型调用」，而现在
    整个函数一次模型都不调 —— 那件事由 `test_入库不调模型` 守签名。"""
    _patch(monkeypatch, [], {})
    res = gather(code="600183", company="生益科技",
                 as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.candidates == 0 and res.docs == [] and res.rejected == 0


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
    gather(code="600183", company="生益科技",
           as_of=AS_OF, corpus=cp)
    assert cp.docs[0].prompt_limit == sentinel


def test_已在语料库里的URL不重复入库(monkeypatch):
    from ir_agent.sources.external import Document
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="x", url=ANNS[0].url, title="已有",
                    publisher="生益科技", tier=Tier.COMPANY,
                    published=ANNS[0].ann_date, retrieved=AS_OF, text="已有"))
    gather(code="600183", company="生益科技",
           as_of=AS_OF, corpus=cp)
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
    fake = types.ModuleType("pymupdf")
    fake.open = lambda **k: _FakeDoc()
    sys.modules["pymupdf"] = fake
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
        del sys.modules["pymupdf"]


def _long_ann(ann_id: str, url: str) -> Announcement:
    return Announcement(ann_id=ann_id, title="生益科技关于扩产的公告",
                        ann_date=date(2026, 8, 1), url=url,
                        code="600183", name="生益科技",
                        category="其他", source_id=ann_id)


# 以下失败构造函数**必须**用真实的 requests/urllib3/retry 对象图，不能
# 手写消息字符串——这正是本轮修复要堵住的那类假测试（见模块顶部
# `_exception_cause_key` 的注释：上一轮的同名测试用
# `ConnectionError(f"...Max retries exceeded with url: {url}")` 手写了
# 一条真实 requests/urllib3 从不会产出的、带 scheme 的消息，测试本身
# 才是那个漏洞没被抓到的原因）。


def _real_connection_retry_exhausted(url: str) -> RetryExhausted:
    """复刻 `annual_pdf._download` 在连接被拒绝/DNS 解不出来时真实走的
    对象图：`HTTPConnectionPool` → `NewConnectionError` → `MaxRetryError`
    → `requests.ConnectionError` → `with_retry` 耗尽重试后的
    `RetryExhausted`。用真正的 `with_retry`（`base_delay=0` 只是省测试
    时间，不改变产生的异常类型/链路）驱动，而不是直接 `raise
    RetryExhausted(...)`——这样构造出来的 `__cause__` 链和线上代码
    产生的完全一致。

    关键细节：喂给 `MaxRetryError` 的不是完整 URL，而是
    `requests.PreparedRequest.path_url`——这正是
    `requests.adapters.HTTPAdapter.request_url` 在真实请求路径里传给
    `urllib3` 的值（见 `adapters.py` 的 `request_url`：非代理场景下
    `url = request.path_url`），**没有 scheme、没有 host**。旧版
    `_cause_key` 用 `https?://\\S+` 去剥 URL，如果这里像上一轮那样偷懒
    传完整 URL，正则会把它剥干净、旧代码也会"侥幸"通过——那样测出的
    就不是 reviewer 指出的真实漏洞。"""
    pool = HTTPConnectionPool(host="static.cninfo.com.cn", port=443)
    req = requests.PreparedRequest()
    req.url = url
    path_url = req.path_url

    def _always_fail():
        try:
            raise NewConnectionError(
                pool, "Failed to establish a new connection: "
                "[Errno 61] Connection refused")
        except NewConnectionError as nce:
            try:
                raise MaxRetryError(pool, path_url, reason=nce) from nce
            except MaxRetryError as mre:
                raise requests.ConnectionError(mre) from mre

    try:
        with_retry(_always_fail, attempts=3, base_delay=0)
    except RetryExhausted as e:
        return e
    raise AssertionError("_always_fail 必须触发 RetryExhausted")


def _real_timeout_retry_exhausted(url: str) -> RetryExhausted:
    """同上，但瞬时故障换成读超时（`requests.Timeout`），用来验证
    "类型链不同则 key 不同"不是靠巧合——这条链路里没有
    `MaxRetryError`/`NewConnectionError` 这两层。"""

    def _always_timeout():
        raise requests.Timeout("Read timed out. (read timeout=120)")

    try:
        with_retry(_always_timeout, attempts=3, base_delay=0)
    except RetryExhausted as e:
        return e
    raise AssertionError("_always_timeout 必须触发 RetryExhausted")


def _real_http_error(status: int, url: str) -> requests.HTTPError:
    """用真实的 `requests.Response.raise_for_status()` 产出 `HTTPError`，
    而不是手工 `HTTPError("404 ...")`——目的是让 `e.response.status_code`
    是一个真正来自 `Response` 对象的整数字段，`_exception_cause_key`
    读的正是这个字段。"""
    resp = requests.Response()
    resp.status_code = status
    resp.reason = {404: "Not Found", 403: "Forbidden"}.get(status, "Error")
    resp.url = url
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        return e
    raise AssertionError(f"status={status} 必须触发 HTTPError")


def test_三次真实连接失败合并为一条(monkeypatch):
    """旗舰场景：`RetryExhausted` 包着真实 `requests.ConnectionError`
    （由 urllib3 `MaxRetryError`/`NewConnectionError` 构造）的三次失败，
    发生在三篇不同公告、三个不同 URL 上，必须收敛成一条——这正是
    reviewer 指出旧实现（剥 URL 的正则）失效的那个场景：这条异常链的
    消息里嵌的是 `request.path_url`（无 scheme、无 host），`https?://`
    正则永远匹配不到。"""
    import ir_agent.sources.announce as A

    urls = [f"http://static.cninfo.com.cn/finalpage/2026-08-0{i}/x.PDF"
            for i in range(1, 4)]
    anns = [_long_ann(f"e{i}", u) for i, u in enumerate(urls)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        raise _real_connection_retry_exhausted(url)

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    # `fetch_failed`（口径：失败了几次）不受去重影响，`reasons`
    # （口径：有几种不同的原因）才是被去重的那个。
    assert res.fetch_failed == 3
    assert len(res.fetch_failed_reasons) == 1
    # 存的展示串仍然带着一个具体的 ann_id 例子，供人核对。
    assert "e0" in res.fetch_failed_reasons[0]


def test_两个真实404合并为一条(monkeypatch):
    """两个不同 URL 上的真实 404（同一种原因），必须收敛成一条。"""
    import ir_agent.sources.announce as A

    urls = ["http://static.cninfo.com.cn/finalpage/2026-08-01/notfound.PDF",
            "http://static.cninfo.com.cn/finalpage/2026-08-02/gone.PDF"]
    anns = [_long_ann(f"f{i}", u) for i, u in enumerate(urls)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        raise _real_http_error(404, url)

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 1


def test_404与403分别记录(monkeypatch):
    """404 与 403 是两种真正不同的诊断（权限问题 vs 资源不存在），
    必须各自留一条——两者的异常类型链完全相同（都只是一个
    `requests.HTTPError`），区分它们必须靠 `response.status_code`。"""
    import ir_agent.sources.announce as A

    url_404 = "http://static.cninfo.com.cn/finalpage/2026-08-01/notfound.PDF"
    url_403 = "http://static.cninfo.com.cn/finalpage/2026-08-02/denied.PDF"
    anns = [_long_ann("g0", url_404), _long_ann("g1", url_403)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        status = 404 if url == url_404 else 403
        raise _real_http_error(status, url)

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 2


def test_404与连接失败分别记录(monkeypatch):
    """404（`requests.HTTPError`，不经重试直接失败）与连接失败
    （`RetryExhausted` 包着 `ConnectionError`，重试三次耗尽）是两种
    结构上完全不同的异常，必须各自留一条。"""
    import ir_agent.sources.announce as A

    url_404 = "http://static.cninfo.com.cn/finalpage/2026-08-01/notfound.PDF"
    url_conn = "http://static.cninfo.com.cn/finalpage/2026-08-02/x.PDF"
    anns = [_long_ann("h0", url_404), _long_ann("h1", url_conn)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        if url == url_404:
            raise _real_http_error(404, url)
        raise _real_connection_retry_exhausted(url)

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 2


def test_取文为空与下载失败分别记录(monkeypatch):
    """本模块自己判定的本地条件（PDF 取文为空）与下载层的异常
    （连接失败）是两种不同来源的失败，必须各自留一条——不能因为都
    落在同一个 `except`/判断分支附近就被合并。"""
    import ir_agent.sources.announce as A

    url_empty = "http://static.cninfo.com.cn/finalpage/2026-08-01/empty.PDF"
    url_conn = "http://static.cninfo.com.cn/finalpage/2026-08-02/x.PDF"
    anns = [_long_ann("i0", url_empty), _long_ann("i1", url_conn)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _fake_download(url, cache_dir=None):
        if url == url_empty:
            return b"EMPTY"
        raise _real_connection_retry_exhausted(url)

    monkeypatch.setattr(A, "_download", _fake_download)
    monkeypatch.setattr(
        A, "pdf_text",
        lambda data: "" if data == b"EMPTY" else data.decode("utf-8"))

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 2


# 以下几条复刻 reviewer 直接通过 `gather()` 实测到的漏洞：`annual_pdf.
# _download` 的 `cache.parent.mkdir(...)`/`write_bytes`/`read_bytes` 调用
# 不在 `with_retry` 里，会原样抛出裸 `OSError`/`PermissionError`——这类
# 异常只靠 `.errno` 就能表达完全不同的系统性原因，类型链单看类型名
# （`"OSError"`）分不出来。同样用真实异常对象构造（`OSError(errno,
# msg)`），不手写消息字符串。


def test_ENOSPC与EIO分别记录(monkeypatch):
    """磁盘满（`ENOSPC`）与单文件 I/O 错误（`EIO`），类型链相同（都只是
    裸 `OSError`），必须靠 `.errno` 区分成两条——这正是本轮要修的漏洞：
    旧实现里两者都只读到 `"OSError"`，八篇公告因为两种不同原因下载失败
    会被合并成一条，另一种原因静默消失。"""
    import ir_agent.sources.announce as A

    url_nospc = "http://static.cninfo.com.cn/finalpage/2026-08-01/x.PDF"
    url_eio = "http://static.cninfo.com.cn/finalpage/2026-08-02/y.PDF"
    anns = [_long_ann("k0", url_nospc), _long_ann("k1", url_eio)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        if url == url_nospc:
            raise OSError(_errno.ENOSPC, "No space left on device")
        raise OSError(_errno.EIO, "Input/output error")

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 2


def test_相同errno的OSError合并为一条(monkeypatch):
    """两篇不同公告上都是 `errno.ENOSPC`（同一种系统性原因：磁盘满），
    必须收敛成一条——不能因为 ann_id/URL 不同就各记一条，那是这类去重
    机制存在的意义。"""
    import ir_agent.sources.announce as A

    url_a = "http://static.cninfo.com.cn/finalpage/2026-08-01/x.PDF"
    url_b = "http://static.cninfo.com.cn/finalpage/2026-08-02/y.PDF"
    anns = [_long_ann("l0", url_a), _long_ann("l1", url_b)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        raise OSError(_errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 1


def test_ENETUNREACH与EHOSTUNREACH分别记录(monkeypatch):
    """网络不可达（`ENETUNREACH`）与主机不可达（`EHOSTUNREACH`）是两种
    不同的网络层诊断。CPython 没有把它们提升成专门的异常子类——不像
    `ECONNREFUSED` 有 `ConnectionRefusedError`——所以两者都是裸
    `OSError`，只能靠 `.errno` 区分，必须各记一条。"""
    import ir_agent.sources.announce as A

    url_net = "http://static.cninfo.com.cn/finalpage/2026-08-01/x.PDF"
    url_host = "http://static.cninfo.com.cn/finalpage/2026-08-02/y.PDF"
    anns = [_long_ann("m0", url_net), _long_ann("m1", url_host)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        if url == url_net:
            raise OSError(_errno.ENETUNREACH, "Network is unreachable")
        raise OSError(_errno.EHOSTUNREACH, "No route to host")

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 2


def test_ConnectionRefusedError与同errno裸OSError分别记录(monkeypatch):
    """`ConnectionRefusedError`（`ECONNREFUSED` 对应的专门子类）与一个
    携带同一个 `errno`（`ECONNREFUSED`）的裸 `OSError`，判定：**必须各
    记一条**。理由：两者的类型链本来就不同（`"ConnectionRefusedError"`
    与 `"OSError"` 是两个不同的类型名字符串），这条区分在类型链层面
    早于本轮加的 `errno` 就已经成立，加了 `errno` 后两者的 key 依旧不同
    （`"ConnectionRefusedError[errno=61]"` vs `"OSError[errno=61]"`）。
    这条测试确认的是：给类型名拼接 errno 这个新逻辑不会意外抹平类型名
    本身的区分力（比如误把 key 只取 errno、丢了类型名）——那样才会把
    这两种不同来源的失败错误地合并。

    构造"裸 OSError 带 ECONNREFUSED"这一侧必须绕开一个 CPython 细节：
    `OSError(errno.ECONNREFUSED, msg)` 这种双参数构造形式会被
    `OSError.__new__` 自动提升成 `ConnectionRefusedError`（PEP 3151），
    根本得不到一个 `type() is OSError` 的实例——用这种写法会让两侧变成
    完全同一个类型，测试判定的就不是本条要判定的问题了。因此这里改用
    `OSError(msg)` 先构造一个不带 errno 参数、不会被提升的普通实例，
    再手动赋值 `.errno`——这确实模拟了一种真实但少见的形状：某些库/
    代码路径拿到系统错误后不经 `OSError(errno, msg)` 这条构造路径、而是
    直接在一个已有的 `OSError` 实例上回填 `.errno`（例如包装转译一个
    第三方来的错误码），产出的就是一个类型仍是 `OSError`、但
    `.errno` 有值的对象。"""
    import ir_agent.sources.announce as A

    url_refused = "http://static.cninfo.com.cn/finalpage/2026-08-01/x.PDF"
    url_bare = "http://static.cninfo.com.cn/finalpage/2026-08-02/y.PDF"
    anns = [_long_ann("n0", url_refused), _long_ann("n1", url_bare)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        if url == url_refused:
            raise ConnectionRefusedError(
                _errno.ECONNREFUSED, "Connection refused")
        bare = OSError("Connection refused")
        bare.errno = _errno.ECONNREFUSED
        assert type(bare) is OSError, (
            "本测试要的是一个真正的裸 OSError 实例，不是被自动提升的子类")
        raise bare

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 2


def test_原因过长按上限截断且仍按原因去重(monkeypatch):
    """恢复被删掉的截断回归测试（原名
    `test_相同原因不同URL且均超截断上限只记一条`）：存进
    `fetch_failed_reasons` 的展示字符串必须截到 `_REASON_LIMIT`——
    清单是给人看的，一条原因长到几百字没人读。原来的缺陷（去重键取自
    被截断后的字符串，导致长 ann_id 一变就撞不上）现在已经结构性地不
    存在了：`_exception_cause_key` 完全不碰 `str(e)`/展示字符串，但
    截断这件事本身此前没有任何测试覆盖，这里单独补上。"""
    import ir_agent.sources.announce as A

    long_id_a = "ann_" + "x" * 300
    long_id_b = "ann_" + "y" * 300
    url_a = "http://static.cninfo.com.cn/finalpage/2026-08-01/a.PDF"
    url_b = "http://static.cninfo.com.cn/finalpage/2026-08-02/b.PDF"
    anns = [_long_ann(long_id_a, url_a), _long_ann(long_id_b, url_b)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        raise OSError(_errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    # 同一种原因（都是 ENOSPC），即使两次拼出的展示字符串都超过上限，
    # 依旧只记一条。
    assert len(res.fetch_failed_reasons) == 1
    assert len(res.fetch_failed_reasons[0]) == A._REASON_LIMIT


def test_不同类型链的重试耗尽异常分别记录(monkeypatch):
    """`RetryExhausted` 包连接失败与 `RetryExhausted` 包读超时，最外层
    类型名相同，但内层类型链不同（前者多两层
    `MaxRetryError`/`NewConnectionError`）——`_exception_cause_key` 记的
    是整条类型链，不是只看最外层，所以两者不会被误合并。"""
    import ir_agent.sources.announce as A

    url_conn = "http://static.cninfo.com.cn/finalpage/2026-08-01/x.PDF"
    url_timeout = "http://static.cninfo.com.cn/finalpage/2026-08-02/y.PDF"
    anns = [_long_ann("j0", url_conn), _long_ann("j1", url_timeout)]
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _raise(url, cache_dir=None):
        if url == url_conn:
            raise _real_connection_retry_exhausted(url)
        raise _real_timeout_retry_exhausted(url)

    monkeypatch.setattr(A, "_download", _raise)

    cp = Corpus(as_of=AS_OF)
    res = gather(code="600183",
                 company="生益科技", as_of=AS_OF,
                 corpus=cp)
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 2


def test_落盘再读回文档内容逐字不变(tmp_path):
    """跨阶段存活的全部意义在于第二阶段拿到的是**同一批文档**。
    文档的 text 要逐字不变——第一阶段正文里的公告引文靠它核验。"""
    from datetime import date

    import ir_agent.sources.announce as A
    from ir_agent.sources.external import Corpus, Document, Tier

    as_of = date(2026, 9, 30)
    doc = Document(doc_id="ann_x", url="http://static.cninfo.com.cn/a.PDF",
                   title="扩产投产公告", publisher="生益科技",
                   tier=Tier.COMPANY, published=date(2026, 8, 1),
                   retrieved=as_of, text="本次投产新增名义产能 300 万平方米。",
                   prompt_limit=2000)
    res = A.AnnounceResult(docs=[doc], candidates=220, picked=3,
                           fetch_failed=1, fetch_failed_reasons=["超时"],
                           ignored=["越界: 99"])
    p = tmp_path / A.ANNOUNCE_JSON
    A.save(res, p)

    cp = Corpus(as_of=as_of)
    back = A.load(p, cp)
    assert len(back.docs) == 1
    d = back.docs[0]
    assert d.text == "本次投产新增名义产能 300 万平方米。", \
        "text 必须逐字不变——第一阶段的公告引文靠它核验"
    assert d.tier is Tier.COMPANY, "分级必须存活，否则它撑不了因子取值"
    assert d.published == date(2026, 8, 1) and d.retrieved == as_of
    assert d.prompt_limit == 2000
    # 头部计数也要存活：第二阶段的清单要说得出「候选 220 条里挑了 3 条」
    assert (back.candidates, back.picked, back.fetch_failed) == (220, 3, 1)
    assert back.fetch_failed_reasons == ["超时"] and back.ignored == ["越界: 99"]
    # **读回必须真的入库**，不只是构造出 Document
    assert cp.docs, "读回的文档没进语料库，第二阶段的核验就会失败"


def test_读回照样过时点守卫(tmp_path):
    """as_of 可能与落盘时不同。「这是我们自己存的」不构成放行理由——
    否则第二阶段换一个更早的 as_of 就能看见未来的公告。"""
    from datetime import date

    import ir_agent.sources.announce as A
    from ir_agent.sources.external import Corpus, Document, Tier

    doc = Document(doc_id="ann_y", url="http://static.cninfo.com.cn/b.PDF",
                   title="投产公告", publisher="生益科技", tier=Tier.COMPANY,
                   published=date(2026, 8, 1), retrieved=date(2026, 9, 30),
                   text="正文")
    p = tmp_path / A.ANNOUNCE_JSON
    A.save(A.AnnounceResult(docs=[doc], candidates=1, picked=1), p)

    cp = Corpus(as_of=date(2026, 7, 1))          # 早于公告披露日
    back = A.load(p, cp)
    assert not cp.docs, "披露日晚于 as_of 的公告被读回入库了——时点守卫被绕过"
    assert not back.docs, "没能入库的文档不该出现在 docs 里"


def test_读回不重复追加语料库里已有的公告网址(tmp_path):
    """与 `openweb.load` 同一道去重。语料库里已经有同一网址的文档时
    （如确定性层也摄入了这批巨潮公告），读回不能再追加一份——
    `Corpus.add` 本身不查重，没有这道保险「外部资料 N 篇」与 COMPANY 档
    计数会被悄悄翻倍。断言落在语料库实际内容上，不落在返回值上。"""
    from datetime import date

    import ir_agent.sources.announce as A
    from ir_agent.sources.external import Corpus, Document, Tier

    url = "http://static.cninfo.com.cn/c.PDF"

    def mk(doc_id):
        return Document(doc_id=doc_id, url=url, title="投产公告",
                        publisher="生益科技", tier=Tier.COMPANY,
                        published=date(2026, 8, 1),
                        retrieved=date(2026, 9, 30), text="正文")
    p = tmp_path / A.ANNOUNCE_JSON
    A.save(A.AnnounceResult(docs=[mk("ann_saved")], candidates=1, picked=1), p)

    cp = Corpus(as_of=date(2026, 9, 30))
    assert cp.add_if_visible(mk("ann_prior"))      # 语料库里早已有这个网址
    back = A.load(p, cp)
    assert [d.doc_id for d in cp.docs] == ["ann_prior"], \
        "同一网址的公告被追加了第二份"
    assert not back.docs, "没入库的文档不该出现在 docs 里"


def test_落盘文件损坏整份降级为空且不抛(tmp_path):
    """一个坏文件不能让整次研究流程崩掉。调用方的契约是「读不到就是这节
    缺公告」，不是「抛异常」。"""
    import ir_agent.sources.announce as A
    from ir_agent.sources.external import Corpus

    p = tmp_path / A.ANNOUNCE_JSON
    cp = Corpus(as_of=__import__("datetime").date(2026, 9, 30))

    for bad in ("{ 这不是 JSON", "[]", '"裸字符串"',
                '{"candidates": "二百二十"}', '{"docs": 7}'):
        p.write_text(bad, encoding="utf-8")
        back = A.load(p, cp)        # 不抛
        assert back.docs == [] and back.candidates == 0, f"没整份降级: {bad}"

    # 文件不存在也不抛
    assert A.load(tmp_path / "nope.json", cp).docs == []


def test_单条文档损坏只跳过那一条(tmp_path):
    """比「整份损坏」更细一级：docs 是合法列表，但其中一条不是合法的
    Document JSON。其余合法条目必须照常读回——丢一条不该连坐。"""
    import json
    from datetime import date

    import ir_agent.sources.announce as A
    from ir_agent.sources.external import Corpus

    good = {"doc_id": "ok", "url": "http://static.cninfo.com.cn/c.PDF",
            "title": "好的", "publisher": "生益科技", "tier": "company",
            "published": "2026-08-01", "retrieved": "2026-09-30",
            "text": "正文", "prompt_limit": 2000, "date_source": ""}
    p = tmp_path / A.ANNOUNCE_JSON
    p.write_text(json.dumps({
        "docs": [{"doc_id": "坏的", "tier": "company"},      # 缺字段
                 good,
                 {**good, "doc_id": "坏分级", "tier": "不存在的档"}],
        "candidates": 220, "picked": 3,
    }, ensure_ascii=False), encoding="utf-8")

    cp = Corpus(as_of=date(2026, 9, 30))
    back = A.load(p, cp)
    assert [d.doc_id for d in back.docs] == ["ok"], \
        "合法条目没被读回，或非法条目被放了进来"
    assert back.candidates == 220, "头部字段不该被单条损坏连坐"


_FIX = None


def _fixture() -> dict:
    """生益科技 2026-10-13 试跑的真实候选（136 条），加跨公司对照。"""
    global _FIX
    if _FIX is None:
        import json
        import pathlib
        _FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                           "cninfo_600183_2026.json").read_text(encoding="utf-8"))
    return _FIX


def _real_anns(key: str = "shengyi") -> list:
    """**过真实解析器**：顺带验了 announcementType 从响应流进 Announcement。"""
    from ir_agent.sources.cninfo import parse_announcements
    return parse_announcements({"announcements": _fixture()[key]}, "fixture")


class TestTypeTier:
    """**按交易所分类代码分档，不按标题认字。**

    0 = 排除（程序性）；1 = 第一档（对估值因子有实质信息）；2 = 第二档（其余，
    保留）。代码表的依据是快照里 30 家公司、7301 条公告的跨公司样本 —— 沪深
    上市公司 announcementType 的官方代码表没有公开出处（JR/T 0220.1—2021 是
    新三板挂牌公司的标准，文中没有这套编码）。
    """

    KEY = ("投资建设高性能覆铜板项目", "签订项目投资意向协议", "2025年度业绩快报",
           "2025年年度业绩预增", "2026年半年度业绩预增", "2025年前三季度业绩预增")
    NOISE = ("临时股东会决议", "法律意见书", "股票交易异常波动", "股票交易风险提示",
             "年度权益分派实施", "半年度报告摘要")

    def test_真实候选里的关键公告全部进第一档(self):
        from ir_agent.sources.announce import type_tier
        anns = _real_anns()
        for key in self.KEY:
            hit = [a for a in anns if key in a.title]
            assert hit, f"夹具里没有 {key}"
            assert type_tier(hit[0].type_codes) == 1, (key, hit[0].type_codes)

    def test_真实候选里的程序性公告被排除(self):
        from ir_agent.sources.announce import type_tier
        anns = _real_anns()
        for key in self.NOISE:
            hit = [a for a in anns if key in a.title]
            assert hit, f"夹具里没有 {key}"
            assert type_tier(hit[0].type_codes) == 0, (key, hit[0].type_codes)

    def test_股东会与股东大会是同一类型(self):
        """**这条就是不按标题认字的理由。** 2024 年新《公司法》把「股东大会」
        改名「股东会」，标题词表一夜失效；分类代码不动。"""
        from ir_agent.sources.announce import type_tier
        cross = _real_anns("cross")
        titles = [a.title for a in cross]
        assert any("股东大会" in t for t in titles)
        assert any("股东会" in t and "股东大会" not in t for t in titles)
        assert all(type_tier(a.type_codes) == 0 for a in cross)

    def test_判档只看代码不看标题(self):
        """标题里写什么都不影响 —— 反过来也要成立。"""
        from ir_agent.sources.announce import type_tier
        assert type_tier("01010503||010113||011705") == 1   # 对外投资
        assert type_tier("01010503||010113||011905") == 0   # 股东（大）会决议
        # 板块码与报告类码（0101 开头）不参与判档
        assert type_tier("01010503||010113") == 2

    def test_无类型码与未知代码默认保留(self):
        """**默认分支要选最弱的那个断言。** 判不了就保留、进第二档 —— 丢掉
        一篇是无声的，多下载一篇只是慢一点。"""
        from ir_agent.sources.announce import is_known_type, type_tier
        assert type_tier("") == 2
        assert type_tier("01010503||010113||019999") == 2
        assert not is_known_type("01010503||010113||019999")
        assert is_known_type("01010503||010113||011705")

    def test_一篇公告带多个类型码时第一档优先(self):
        """真实样本：`01010503||010113||011303||012325`。任一码是第一档就算
        第一档；**全部**码都在排除表里才排除。"""
        from ir_agent.sources.announce import type_tier
        assert type_tier("01010503||010113||011705||011905") == 1
        assert type_tier("01010503||010113||011303||012325") == 0
        assert type_tier("01010503||010113||011905||012399") == 2


class TestRealGather:
    """真实候选过一遍 `gather`：入库多少、哪些、第一档是否齐全。"""

    def _run(self, monkeypatch, cap=None):
        import ir_agent.sources.announce as A
        from ir_agent.sources.external import Corpus

        anns = _real_anns()
        monkeypatch.setattr(A, "_search", lambda *a, **k: anns)
        monkeypatch.setattr(A, "_download",
                            lambda url, cache_dir=None: b"x")
        monkeypatch.setattr(A, "pdf_text", lambda data: "正文。")
        if cap is not None:
            monkeypatch.setattr(A, "MAX_INGEST", cap)
        cp = Corpus(as_of=date(2026, 10, 13))
        return A.gather(code="600183", company="生益科技",
                        as_of=date(2026, 10, 13), corpus=cp), cp

    def test_生益科技入库51篇第一档8篇(self, monkeypatch):
        res, cp = self._run(monkeypatch)
        assert res.candidates == 136
        assert res.rejected == 85
        assert len(cp.docs) == 51
        assert sum(1 for d in cp.docs if d.rank == 1) == 8
        titles = " ".join(d.title for d in cp.docs)
        for key in TestTypeTier.KEY:
            assert key in titles, f"关键公告没入库：{key}"
        for key in TestTypeTier.NOISE:
            assert key not in titles, f"程序性公告入库了：{key}"

    def test_进提示词的八篇就是第一档(self, monkeypatch):
        """**这是上一版真正坏掉的地方。** 上一版按时间取最近 8 篇，于是定性
        三节读到的是股东会决议和律师意见书。"""
        _, cp = self._run(monkeypatch)
        got = cp.prompt_docs()
        assert len(got) == 8 and all(d.rank == 1 for d in got)
        joined = " ".join(d.title for d in got)
        assert "投资建设高性能覆铜板项目" in joined and "业绩快报" in joined

    def test_上限截断只截第二档的尾巴并且说出来(self, monkeypatch):
        """安全上限是防一年几百条公告的公司把下载拖到十几分钟。**按档排序后
        截断**：第一档无论多旧都先进；截了多少要能说出来。"""
        res, cp = self._run(monkeypatch, cap=10)
        assert len(cp.docs) == 10
        assert sum(1 for d in cp.docs if d.rank == 1) == 8, "第一档被截掉了"
        assert res.truncated == 51 - 10
        oldest_t1 = min((d for d in cp.docs if d.rank == 1),
                        key=lambda d: d.published)
        assert "前三季度业绩预增" in oldest_t1.title, "最旧的第一档也该在"


class TestGatherNoModel:
    def test_入库不调模型(self, monkeypatch):
        """**钉住「不调模型」这件事本身。** `gather` 的签名里不再有 client；
        这条测试防的是将来有人加一个「可疑就调模型兜底」的分支 —— 那会把
        确定性又换回去。
        """
        import inspect
        from ir_agent.sources import announce

        assert "client" not in inspect.signature(announce.gather).parameters
        assert "factors" not in inspect.signature(announce.gather).parameters


class TestPromptDocs:
    def test_上限在提示词层而不在入库层(self):
        """**这是本 Task 的全部要点。** `MAX_ANNOUNCEMENTS = 8` 的注释自己
        写着上限的理由是「同一批文档会同时压到战略/产业链/宏观定性三个模块
        的提示词上」—— 那是消费方的约束，被实现成了生产方的上限。
        """
        from datetime import date

        from ir_agent.sources.external import Corpus, Document, Tier

        cp = Corpus(as_of=date(2026, 10, 12))
        for i in range(20):
            cp.docs.append(Document(
                doc_id=f"a{i}", url=f"http://x/{i}", title=f"公告{i}",
                publisher="某公司", tier=Tier.COMPANY,
                published=date(2026, 1, 1 + i), retrieved=date(2026, 10, 12),
                text="正文"))
        assert len(cp.docs) == 20, "入库的是全部 20 篇"
        assert len(cp.prompt_docs(company_limit=8)) == 8, "进提示词的只有 8 篇"

    def test_只截断COMPANY级其余照旧全给(self):
        from datetime import date

        from ir_agent.sources.external import Corpus, Document, Tier

        cp = Corpus(as_of=date(2026, 10, 12))
        for i, tier in enumerate([Tier.COMPANY] * 20 + [Tier.SELLSIDE] * 5):
            cp.docs.append(Document(
                doc_id=f"d{i}", url=f"http://x/{i}", title=f"文档{i}",
                publisher="某处", tier=tier,
                published=date(2026, 1, 1), retrieved=date(2026, 10, 12),
                text="正文"))
        got = cp.prompt_docs(company_limit=8)
        assert sum(1 for d in got if d.tier is Tier.COMPANY) == 8
        assert sum(1 for d in got if d.tier is Tier.SELLSIDE) == 5

    def test_留下的是最近的那几篇但次序跟语料库(self):
        """**「留哪几篇」按新旧选，「以什么次序出现」跟 `corpus.docs`。**

        计划原本断言返回 `["公告9", "公告8", "公告7"]`（降序）。实跑时改成
        保持原顺序，理由是返回的是**混合分级**的列表 —— COMPANY 级被筛、
        研报与开放网页原样留着。只把其中一部分重排，会让提示词里的次序和
        `_external_md` 统计的那份清单对不上，而那份清单会进看板、进
        state.json、第二阶段读回。

        公告本身的新旧信息不靠次序传递：每篇都带 `published`，而
        `Document.cite()` 把它印在引用行上。
        """
        from datetime import date

        from ir_agent.sources.external import Corpus, Document, Tier

        cp = Corpus(as_of=date(2026, 10, 12))
        for i in range(10):
            cp.docs.append(Document(
                doc_id=f"a{i}", url=f"http://x/{i}", title=f"公告{i}",
                publisher="某公司", tier=Tier.COMPANY,
                published=date(2026, 1, 1 + i), retrieved=date(2026, 10, 12),
                text="正文"))
        got = [d.title for d in cp.prompt_docs(company_limit=3)]
        assert got == ["公告7", "公告8", "公告9"], got


def test_旧上限8篇已经不再是入库上限():
    """**钉住「上限搬了层」这件事本身。**

    `PROMPT_DOCS` 仍是 8（提示词层），`MAX_INGEST` 是 20（入库层）。
    若有人把两者又合成一个数，这条测试会红 —— 而那次合并正是本次要拆开的
    东西：消费方的约束被实现成了生产方的上限。
    """
    from ir_agent.sources.announce import MAX_INGEST, PROMPT_DOCS

    assert PROMPT_DOCS == 8
    assert MAX_INGEST > PROMPT_DOCS
