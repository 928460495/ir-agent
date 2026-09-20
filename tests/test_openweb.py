"""开放网络文档入语料库。**模型只给网址，正文由代码抓。**"""

from __future__ import annotations

from datetime import date

import pytest
import requests

from ir_agent.sources.external import Tier
from ir_agent.sources.openweb import SLOTS, Candidate, parse_candidates, tier_of_url

AS_OF = date(2026, 9, 20)

OUT = """候选: 行业规模与增速 | 2026年上半年全球覆铜板市场规模 | https://www.semi.org/a | SEMI | 2026-07-01
候选: 政策与监管 | 工信部印发电子材料产业行动方案 | https://www.miit.gov.cn/b | 工业和信息化部 | 2026-06-15
候选: 覆铜板批价跟踪 | 华东地区覆铜板报价周报 | https://finance.example.com/c | 某财经网 | 2026-08-01
"""


def test_解析出槽位标题网址机构日期():
    got = parse_candidates(OUT)
    assert len(got) == 3
    first = got[0]
    assert first.slot == "行业规模与增速"
    assert first.title == "2026年上半年全球覆铜板市场规模"
    assert first.url == "https://www.semi.org/a"
    assert first.publisher == "SEMI"
    assert first.published == date(2026, 7, 1)
    assert first.extra is False


def test_不在SLOTS里的槽位收下并标为额外():
    got = parse_candidates(OUT)
    extra = [c for c in got if c.extra]
    assert len(extra) == 1
    assert extra[0].slot == "覆铜板批价跟踪"


def test_少于五段整条丢弃():
    """与 parse_findings 同一条守卫：不存在「4 段也合法、日期可选」的分支。"""
    assert parse_candidates(
        "候选: 政策与监管 | 标题 | https://x.gov.cn/a | 某部委\n") == []


def test_没有网址整条丢弃():
    assert parse_candidates(
        "候选: 政策与监管 | 标题 | 见官网 | 某部委 | 2026-06-15\n") == []


def test_日期解析不出整条丢弃():
    """这是 Document.published 能保持非空 date 的前提——进到 fetch 的
    候选一定带着一个可解析的日期，任何路径上都不存在「日期不明」状态。"""
    for bad in ("2026年7月", "近期", ""):
        line = f"候选: 政策与监管 | 标题 | https://x.gov.cn/a | 某部委 | {bad}\n"
        assert parse_candidates(line) == [], bad


def test_容忍列表前缀与加粗():
    """lineparse.keyed 的既有能力，这里确认确实走的是它。"""
    got = parse_candidates(
        "1. **候选**: 政策与监管 | 标题 | https://x.gov.cn/a | 某部委 | 2026-06-15\n")
    assert len(got) == 1


def test_提示词逐字含全部固定槽位():
    from ir_agent.sources.openweb import PROMPT
    for slot in SLOTS:
        assert slot in PROMPT, slot


def test_gov_cn_后缀落OFFICIAL():
    assert tier_of_url("https://www.stats.gov.cn/sj/zxfb/x.html") is Tier.OFFICIAL
    assert tier_of_url("https://www.miit.gov.cn/a/b.html") is Tier.OFFICIAL


def test_白名单注册域及其子域落对应档():
    assert tier_of_url("https://www.semi.org/en/news/x") is Tier.OFFICIAL
    assert tier_of_url("https://semi.org/x") is Tier.OFFICIAL


def test_不认识的域名落MEDIA():
    assert tier_of_url("https://finance.example.com/x") is Tier.MEDIA
    assert tier_of_url("https://zhuanlan.zhihu.com/p/1") is Tier.MEDIA


def test_不变式_域名匹配必须锚在注册域边界上():
    """裸子串是本项目被打穿过三轮的那个洞在域名上的形态。

    `evil-semi.org.cn` 含子串 `semi.org`，`semi.org.cn.evil.com` 同样含；
    两者都必须落 MEDIA。这条不变式一旦破了，任何人注册一个含白名单
    字样的域名就能把自己拔成 OFFICIAL。
    """
    for bad in ("https://evil-semi.org/x",
                "https://evil-semi.org.cn/x",
                "https://semi.org.evil.com/x",
                "https://notgov.cn/x",
                "https://gov.cn.evil.com/x"):
        assert tier_of_url(bad) is Tier.MEDIA, bad


def test_不变式_白名单条目跨档互不为子域():
    """同一个主机名不能既命中 SELLSIDE 又命中 OFFICIAL——那种情况下
    结果取决于判别顺序，属于白名单数据本身的错误。

    **注意 `_SELLSIDE_HOSTS` 初始为空，此时这条检查的循环体不执行。**
    这是有意保留的守卫：它要防的是**将来往两张表里加条目时**撞车，而
    不是现在就有冲突。下面先断言两张表的形状（元素是非空小写注册域、
    不带协议与路径），保证这个测试在两张表都为空时也不是空跑。
    """
    from ir_agent.sources.openweb import _OFFICIAL_HOSTS, _SELLSIDE_HOSTS

    assert _OFFICIAL_HOSTS, "官方白名单不该为空"
    for entry in _OFFICIAL_HOSTS + _SELLSIDE_HOSTS:
        assert entry and entry == entry.lower().strip(), entry
        assert "/" not in entry and ":" not in entry, entry
        assert "." in entry, entry

    for a in _OFFICIAL_HOSTS:
        for b in _SELLSIDE_HOSTS:
            assert not (a == b or a.endswith("." + b) or b.endswith("." + a)), \
                f"{a} 与 {b} 跨档互为子域"


def test_畸形输入不炸():
    for bad in ("", "not a url", "http://", "://x", "ftp://x.gov.cn"):
        assert tier_of_url(bad) is Tier.MEDIA, bad


def test_空前导标签不算gov_cn子域():
    """`.gov.cn`（空前导标签）不是 gov.cn 的合法子域——DNS 不接受空
    标签。字符串层面 ".gov.cn".endswith(".gov.cn") 为真，若不特判会被
    误判成 OFFICIAL。fetch() 今天走不到这里（DNS 解析不出这种主机名），
    但 tier_of_url 是公开函数，判错的方向落在危害一侧，必须堵。"""
    assert tier_of_url("https://.gov.cn/x") is Tier.MEDIA


from ir_agent.sources.openweb import extract_text, meta_date

HTML = """<html><head>
<meta charset="utf-8">
<meta property="article:published_time" content="2026-07-01T08:30:00+08:00">
<title>覆铜板市场规模</title>
</head><body>
<nav><a href="/">首页</a><a href="/about">关于我们</a></nav>
<script>var ad = {track: 1};</script>
<style>.x{color:red}</style>
<div class="content"><p>2026年上半年全球覆铜板市场规模约 120 亿美元。</p>
<p>同比增长 8.4%。</p></div>
<footer>版权所有 &copy; 某站</footer>
</body></html>"""


def test_正文提取剔除脚本样式导航页脚():
    got = extract_text(HTML)
    assert "覆铜板市场规模约 120 亿美元" in got
    assert "同比增长 8.4%" in got
    for noise in ("var ad", "color:red", "关于我们", "版权所有"):
        assert noise not in got, noise


def test_正文提取还原实体并压掉空白():
    got = extract_text("<p>A&nbsp;&amp;&nbsp;B</p><p>  C  </p>")
    assert "A & B" in got
    assert "  C  " not in got
    assert "C" in got


def test_meta日期_属性在前():
    assert meta_date(HTML) == date(2026, 7, 1)


def test_meta日期_content在前也认():
    """meta 标签的属性顺序没有标准，两种写法都得认。"""
    html = ('<meta content="2026-06-15" '
            'property="article:published_time">')
    assert meta_date(html) == date(2026, 6, 15)


def test_meta日期_没有就返回None():
    assert meta_date("<html><body>x</body></html>") is None


def test_meta日期_认不出的写法返回None():
    html = '<meta property="article:published_time" content="近期">'
    assert meta_date(html) is None


# ── final-fix-2 Fix 2：meta_date 要按 _DATE_KEYS 的优先级排序取值，
# 不能按 meta 标签在文档里出现的先后顺序取第一个命中 ──

def test_meta日期_优先级键排在文档靠前时取它():
    """`date` 排在 `article:published_time` 前面时，仍要按优先级取后者
    ——不能因为它先出现就先被返回。"""
    html = ('<meta name="date" content="2020-01-01">'
            '<meta property="article:published_time" content="2026-09-15">')
    assert meta_date(html) == date(2026, 9, 15)


def test_meta日期_优先级键排在文档靠后时也取它():
    """与上一条对照：交换两个标签在文档里的先后顺序，结果必须一样——
    取决于 `_DATE_KEYS` 的排名，不取决于文档位置。"""
    html = ('<meta property="article:published_time" content="2026-09-15">'
            '<meta name="date" content="2020-01-01">')
    assert meta_date(html) == date(2026, 9, 15)


def test_正文提取剔除嵌套同类容器不留残余文字():
    """回归用例：`<nav>A<nav>B</nav>C</nav>` 曾经在旧的非贪婪正则下漏出
    `C`——非贪婪只保证匹配尽量短，认的是**第一个**同名闭合标签，不是与
    当前开标签配对的那一个，于是外层 `<nav>` 和内层 `</nav>` 被错配成
    一对，`C</nav>` 里的 `C` 就不再处于任何被识别的容器里，被当成正文
    放行。大菜单、面包屑套在 `<nav>` 里在真实导航栏里很常见，这不是
    构造出来才会撞到的边界情况。"""
    html = "<div><p>正文A</p><nav>导航一<nav>导航二</nav>导航三</nav>" \
           "<p>正文B</p></div>"
    got = extract_text(html)
    assert "正文A" in got
    assert "正文B" in got
    for noise in ("导航一", "导航二", "导航三"):
        assert noise not in got, noise


def test_正文提取剔除两种不同容器互相嵌套():
    """同一条修复要同时管住「同类自嵌套」和「异类互嵌套」——异类互嵌套
    本来就没被旧正则的 bug 命中（\\1 回引把闭合标签锁死成同名），这里
    补一条覆盖，确认新写法没有反过来把这种情况打坏。"""
    html = "<div><p>正文C</p><nav>导航<header>页头</header>尾巴</nav>" \
           "<p>正文D</p></div>"
    got = extract_text(html)
    assert "正文C" in got
    assert "正文D" in got
    for noise in ("导航", "页头", "尾巴"):
        assert noise not in got, noise


def test_正文提取剔除HTML注释不留残余文字或定界符():
    """真实事故：`<!-- <div>草稿标题：...</div> -->` 这类被注释掉的整段
    标记，人打开网页永远看不到——浏览器不渲染注释——但旧实现只按
    「< 到下一个 >」剥标签，会把注释体里第一个 `<div>` 当独立标签吃掉，
    剩下的注释正文和收尾的 `-->` 全部被当纯文本放行，混进
    Corpus.verify() 逐字核验的正文。真实抓取到的文档里一篇就有 20 个
    `-->` 残留，其中 7~9 个落在 2000 字的提示词窗口内。"""
    html = ("<p>真正的正文。</p>"
           "<!-- <div>草稿标题：公司2026年净利润将翻倍</div> -->"
           "<p>后续正文。</p>")
    got = extract_text(html)
    assert "真正的正文" in got
    assert "后续正文" in got
    assert "草稿标题" not in got
    assert "净利润将翻倍" not in got
    assert "-->" not in got


def test_正文提取先剥噪声容器再删注释_内联JS里的箭头不会吞掉真内容():
    """真实事故（这条本身就是对上一次修复的回归）：`extract_text` 曾经
    先跑 `_COMMENT` 再跑 `_strip_blocks`。`<script>var s = "<!--";</script>`
    这行内联 JS 字符串字面量里的 `<!--` 只是三个普通字符，不是注释起点，
    但 `_COMMENT` 不认识 JS 语法，只认「下一个 `-->`」——非贪婪加 re.S
    会一路吃到文档里**下一处真正的** `-->`，也就是后面那条真注释的
    收尾，把中间「真实正文二」这段真内容和 `</script>` 一起吞掉；
    `</script>` 被吞掉后 `<script>` 变成未闭合标签，`_strip_blocks`
    剥不掉它，`var s = "` 这段 JS 代码反而作为"正文"漏出来——修复注释
    污染的同时引入了同等严重的反向污染：真内容被删、看不见的代码被admit。
    正确顺序（先剥噪声容器再删注释）下，三段真实正文必须全部保留，
    JS 片段与残留的 `-->` 一个字都不能漏出来。"""
    html = ('<p>正文一：公司2026年净利润将翻倍</p>'
           '<script>var s = "<!--";</script>'
           '<p>真实正文二：覆铜板均价上涨12%</p>'
           '<!-- 真正的注释 -->'
           '<p>正文三</p>')
    got = extract_text(html)
    assert "正文一：公司2026年净利润将翻倍" in got
    assert "真实正文二：覆铜板均价上涨12%" in got
    assert "正文三" in got
    for noise in ("var s", "-->", "<!--"):
        assert noise not in got, noise


def _fake_getaddrinfo_public(host, port):
    """与 fetch() 的主机安全闸门无关的测试用：任何主机名都解析成一个
    公网地址，绕开真实 DNS 调用（不需要网络、结果确定）。"""
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


class _FakeFetchResp:
    """`fetch()` 新实现依赖的响应形状：`headers`（内容类型闸门）、
    `iter_content`（字节上限闸门，流式读）、`close`——不再是旧实现依赖
    的一次性 `.content`/`.text`/`.apparent_encoding`（那条路径在读完流
    之后会被 requests 标记为"已消费"，见 openweb._decode 的注释）。"""

    def __init__(self, url, body: bytes,
                content_type="text/html; charset=utf-8", encoding="utf-8"):
        self.url = url
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.encoding = encoding

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        return None


class _FlakyBodyResp:
    """`iter_content()` 前 `fail_times` 次调用抛 `ChunkedEncodingError`，
    之后才正常吐出正文——用来复现 Fix 1 报的事故：`stream=True` 下正文
    要等 `iter_content()` 被消费时才读，是惰性的一步，真实世界里这一步
    发生瞬时网络故障和头部请求发生瞬时网络故障是同一类风险，必须同样
    被 `with_retry` 覆盖。"""

    def __init__(self, url, body: bytes, fail_times: int = 1):
        self.url = url
        self._body = body
        self._fail_times = fail_times
        self._calls = 0
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        self._calls += 1
        if self._calls <= self._fail_times:
            def _boom():
                raise requests.exceptions.ChunkedEncodingError(
                    "Connection broken: 模拟的中途网络中断")
                yield  # pragma: no cover — 不会执行到，只为了让这是个生成器
            return _boom()

        def _ok():
            for i in range(0, len(self._body), chunk_size):
                yield self._body[i:i + chunk_size]
        return _ok()

    def close(self):
        return None


def test_fetch_正文读取阶段的瞬时故障最终转换为FetchFailed不逃逸(monkeypatch):
    """Fix 1 的核心复现：旧实现只把拿响应头那一步（`with_retry(_get)`）
    纳入 with_retry + FetchFailed 的双重边界，`resp.iter_content()` 的
    读取循环留在外面裸跑一个 `try/finally`。`ChunkedEncodingError`
    （`retry._TRANSIENT` 明确点名要重试的瞬时故障）因此会在读正文时才
    炸，从这两层保护外面原样冒出去——`gather()` 只接 `FetchFailed`，
    接不住裸异常，一路冒到 `_load_openweb` 的 `except Exception`，那里
    会在 `openweb.save()` 执行之前就返回，本轮已经抓到的其余文档全部
    没能跨阶段存活。这里验证：即便重试次数用尽仍然读不出正文，最终
    露出来的必须是 `FetchFailed`，不能是裸的 `requests` 异常。"""
    import ir_agent.sources.openweb as OW
    import ir_agent.sources.retry as RETRY

    monkeypatch.setattr(RETRY.time, "sleep", lambda s: None)
    monkeypatch.setattr(OW.socket, "getaddrinfo", _fake_getaddrinfo_public)
    resp = _FlakyBodyResp("https://x.example.com/a",
                          "<p>正文</p>".encode("utf-8"), fail_times=99)
    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: resp)

    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://x.example.com/a")


def test_fetch_正文读取阶段的瞬时故障重试后成功(monkeypatch):
    """与上一条对照的正面用例：正文读取现在确实处在 `with_retry` 的
    重试范围内——不是"重试次数用尽就必然失败"，第一次读正文中途断线、
    第二次成功时应该正常返回抓到的正文，而不是把第一次的失败当成
    最终结果。"""
    import ir_agent.sources.openweb as OW
    import ir_agent.sources.retry as RETRY

    monkeypatch.setattr(RETRY.time, "sleep", lambda s: None)
    monkeypatch.setattr(OW.socket, "getaddrinfo", _fake_getaddrinfo_public)
    resp = _FlakyBodyResp("https://x.example.com/a",
                          "<p>覆铜板报价上涨</p>".encode("utf-8"),
                          fail_times=1)
    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: resp)

    got = OW.fetch("https://x.example.com/a")
    assert "覆铜板报价上涨" in got.text


def test_fetch_用最终URL并解码中文(monkeypatch):
    """两件事一起验：跟完重定向后的 final_url 必须是最终地址（分级要用
    它），以及中文页面的编码必须按页面声明解，不能留 ISO-8859-1 的默认
    猜测。主机安全闸门用 monkeypatch 顶掉，两个域名都放行到公网地址，
    不做真实网络调用。"""
    import ir_agent.sources.openweb as OW

    monkeypatch.setattr(OW.socket, "getaddrinfo", _fake_getaddrinfo_public)
    body = "<p>覆铜板报价上涨</p>".encode("utf-8")
    resp = _FakeFetchResp("https://blog.example.com/real", body,
                          content_type="text/html; charset=ISO-8859-1",
                          encoding="ISO-8859-1")
    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: resp)
    got = OW.fetch("https://www.miit.gov.cn/redirects-away")
    assert got.final_url == "https://blog.example.com/real"
    assert "覆铜板报价上涨" in got.text


def test_fetch_正文为空则抛(monkeypatch):
    """`match` 钉住具体消息——不然内容类型或大小闸门万一回归成误拒，
    这条测试会看到别的 FetchFailed 就绿了，形同没在守卫"正文提取为空"
    这一件事。"""
    import ir_agent.sources.openweb as OW

    monkeypatch.setattr(OW.socket, "getaddrinfo", _fake_getaddrinfo_public)
    body = b"<html><body><script>1</script></body></html>"
    resp = _FakeFetchResp("https://x.example.com/a", body)
    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: resp)
    with pytest.raises(OW.FetchFailed, match="正文提取为空"):
        OW.fetch("https://x.example.com/a")


# ── final-fix-4 Fix 5a：GB18030 是猜测链最后一环、也是个 sink——它是
# 国标超集，对着 Big5/EUC-JP 的字节硬解八九不离十不会报错，只会**成功**
# 解出一串看起来正常、实际是乱码的字符串，extract_text 因为它不是空
# 字符串照样放行。此前 `grep -rn "gb18030|big5" tests/` 一条都没有——
# 猜测链最后一环、也是这个 sink 的源头，从来没被测试覆盖过 ──

def test_解码_无header声明的GB18030页面正确解码():
    """header 没给字符集（走 ISO-8859-1 默认分支），页面也没有
    `<meta charset>` 声明，必须落到猜测链的 gb18030 分支解出正确的
    中文——而不是意外撞对或撞出乱码却不报错。"""
    import ir_agent.sources.openweb as OW

    text = "覆铜板报价上涨"
    raw = text.encode("gb18030")
    # 先确认这段字节真的不是合法 UTF-8——保证下面的断言测的是 gb18030
    # 分支被走到，不是 utf-8 侥幸猜对。
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert OW._decode(raw, None) == text


def test_解码_meta声明Big5时优先按Big5解码():
    """GB18030 这个 sink 的危害正是"不报错、也不是页面真正的编码"——
    页面自己在 `<meta charset>` 里声明了 Big5，必须先信这个声明，不能
    让它落进会把任何字节都吃成"看起来正常"的猜测链。这是本项目的真实
    使用场景：CCL/PCB 覆盖点名的台湾一手来源现在仍有用 Big5 编码的。"""
    import ir_agent.sources.openweb as OW

    text = "報價上漲"
    body = text.encode("big5")
    head = b'<meta charset="Big5">'
    raw = head + body
    assert text in OW._decode(raw, None)


def test_解码_meta声明写成老式http_equiv形式时也认():
    """`<meta http-equiv="Content-Type" content="text/html; charset=...">`
    这种老式写法把 charset 嵌在 content 属性值里，不是 HTML5 短格式的
    独立 `charset=` 属性——两种写法都要认，不能只支持其中一种。"""
    import ir_agent.sources.openweb as OW

    text = "報價上漲"
    body = text.encode("big5")
    head = (b'<meta http-equiv="Content-Type" '
           b'content="text/html; charset=Big5">')
    raw = head + body
    assert text in OW._decode(raw, None)


# ── final-fix-3 Fix 2：模型选的网址，代码自己去抓——没有主机闸门时，
# 环回/内网/链路本地/保留地址、非网页内容类型、超大响应体都会被当成
# 一篇正常文章抓回来入库 ──

def test_fetch_拒绝环回地址(monkeypatch):
    """`http://127.0.0.1/...` 这类地址在 fetch 运行的机器上往往连得通、
    不会报错——必须在发请求前就查出来拦下，不能指望"连不上"兜底。"""
    import ir_agent.sources.openweb as OW

    monkeypatch.setattr(
        OW.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("127.0.0.1", 0))])

    def _boom(*_a, **_k):
        raise AssertionError("主机闸门没拦住，不该发起请求")
    monkeypatch.setattr(OW.requests, "get", _boom)

    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://internal.example.com/x")


def test_fetch_拒绝内网地址(monkeypatch):
    import ir_agent.sources.openweb as OW

    monkeypatch.setattr(
        OW.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("10.1.2.3", 0))])

    def _boom(*_a, **_k):
        raise AssertionError("主机闸门没拦住，不该发起请求")
    monkeypatch.setattr(OW.requests, "get", _boom)

    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://internal.example.com/x")


def test_fetch_拒绝链路本地地址(monkeypatch):
    """169.254.169.254 是 AWS/GCP/阿里云等云主机的元数据端点，挂着实例
    凭证——模型编一个这样的 URL，进程不能替它去问。"""
    import ir_agent.sources.openweb as OW

    monkeypatch.setattr(
        OW.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))])

    def _boom(*_a, **_k):
        raise AssertionError("主机闸门没拦住，不该发起请求")
    monkeypatch.setattr(OW.requests, "get", _boom)

    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://metadata.example.com/x")


def test_fetch_拒绝保留地址(monkeypatch):
    import ir_agent.sources.openweb as OW

    monkeypatch.setattr(
        OW.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("240.0.0.1", 0))])

    def _boom(*_a, **_k):
        raise AssertionError("主机闸门没拦住，不该发起请求")
    monkeypatch.setattr(OW.requests, "get", _boom)

    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://reserved.example.com/x")


def test_fetch_重定向到内网地址后仍被拒绝(monkeypatch):
    """模型给的域名本身解析到公网，但该域名的服务端把请求 302 到内网
    地址——只查请求前那一次拦不住，跟完重定向后必须再查一次。"""
    import ir_agent.sources.openweb as OW

    def _getaddrinfo(host, port):
        if host == "public.example.com":
            return [(2, 1, 6, "", ("93.184.216.34", 0))]
        if host == "internal.example.com":
            return [(2, 1, 6, "", ("10.0.0.5", 0))]
        raise AssertionError(f"未预期的主机名：{host}")

    monkeypatch.setattr(OW.socket, "getaddrinfo", _getaddrinfo)
    resp = _FakeFetchResp("https://internal.example.com/after-redirect",
                          "<p>不该被读到</p>".encode("utf-8"))
    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: resp)

    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://public.example.com/x")


def test_fetch_拒绝非网页内容类型(monkeypatch):
    """PDF/二进制经过 `extract_text` 大概率不是空字符串就是乱码——乱码
    不会报错，会被当成"抓到了"直接入库，成为一条谁都对不出处的引文。"""
    import ir_agent.sources.openweb as OW

    monkeypatch.setattr(OW.socket, "getaddrinfo", _fake_getaddrinfo_public)
    resp = _FakeFetchResp("https://x.example.com/a", b"%PDF-1.4 ...",
                          content_type="application/pdf")
    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: resp)

    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://x.example.com/a")


def test_fetch_超过字节上限时拒绝(monkeypatch):
    """卡在读的过程中，不是读完再判断——不然仍然要求先把对方发来的任意
    大小的响应体整个吃进内存，等于没卡。"""
    import ir_agent.sources.openweb as OW

    monkeypatch.setattr(OW.socket, "getaddrinfo", _fake_getaddrinfo_public)
    monkeypatch.setattr(OW, "FETCH_MAX_BYTES", 10)
    body = ("<p>" + "正文超长" * 10 + "</p>").encode("utf-8")
    assert len(body) > 10
    resp = _FakeFetchResp("https://x.example.com/a", body)
    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: resp)

    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://x.example.com/a")


def test_深度嵌套容器在上限内完全剥掉():
    """钉住迭代上限 _BLOCK_MAX_PASSES = 32 的行为：深度在 32 以内的嵌套
    噪声容器必须被完全剔除，不留一个字。这条测试一旦红了，说明有人偷偷
    降低了 _BLOCK_MAX_PASSES（比如「简化」回 2），这个缺口必须被发现。"""
    noise = "噪声"
    real_content = "真实内容"
    depth = 20  # 远在 32 的上限内

    # 程序化构造：<nav>噪声</nav> 嵌套 20 层，真实内容在外层
    nested = "<nav>" * depth + noise + "</nav>" * depth
    html = f"<div><p>{real_content}</p>{nested}</div>"

    got = extract_text(html)

    # 真实内容必须保留
    assert real_content in got
    # 嵌套内的噪声必须被彻底剔除
    assert noise not in got


def test_容器深度超上限时开放失败():
    """容器深度超过 _BLOCK_MAX_PASSES = 32 时，`_strip_blocks` 会在达到
    上限时退出，可能留下一些未剔除的噪声——这是**故意的设计**。

    为什么选择开放失败而不是死循环或报错：
    - 没有对抗性输入时，真实页面的嵌套深度从未超过个位数，所以噪声
      泄露是边界情况，对正常内容的污染可以容忍
    - **相反，静默丢掉容器外的真实内容会是灾难**——用户看不见缺陷，
      引文核验也会跟着打折扣。开放失败（内容保留但有噪声）至少让
      问题可见

    此测试验证：
    1. 深度超过 32 时，一些噪声确实会泄露（证明限制生效）
    2. 但容器**外**的真实内容必须完整保留（核心保证）
    """
    noise = "噪声"
    real_before = "前面内容"
    real_after = "后面内容"
    depth = 40  # 超过 32 的上限

    # 程序化构造：前面真实内容 + 超深嵌套噪声容器 + 后面真实内容
    nested = "<nav>" * depth + noise + "</nav>" * depth
    html = f"<div><p>{real_before}</p>{nested}<p>{real_after}</p></div>"

    got = extract_text(html)

    # 容器外的真实内容**必须**保留——这是核心保证
    assert real_before in got, "前面的真实内容被误删了"
    assert real_after in got, "后面的真实内容被误删了"

    # 超过上限时，一些噪声会泄露（这个行为的代价已被明确接受）
    # 此处不做严格检查噪声泄露量，只验证「内容保留 + 可能漏噪声」
    # 这样即使将来嵌套处理方式微调，只要核心保证不变就不会红


from ir_agent.sources.external import Corpus
from ir_agent.sources.openweb import Fetched, OpenWebResult, gather, summary


class _Client:
    """与 tests/test_web_source.py 的 _Client 同一形状：文本进、文本出。"""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _fake_fetch(pages):
    """pages: {请求URL: Fetched}。缺的抛 FetchFailed。"""
    import ir_agent.sources.openweb as OW

    def _f(url, *, timeout=OW.TIMEOUT):
        if url not in pages:
            raise OW.FetchFailed(f"{url}：404")
        return pages[url]
    return _f


FOUR = """候选: 行业规模与增速 | 规模 | https://www.semi.org/a | SEMI | 2026-07-01
候选: 政策与监管 | 政策 | https://www.miit.gov.cn/b | 工信部 | 2026-06-15
候选: 竞争格局与份额 | 格局 | https://finance.example.com/c | 某财经网 | 2026-08-01
候选: 覆铜板批价 | 批价 | https://price.example.com/d | 某网 | 2026-08-20
"""

PAGES = {
    "https://www.semi.org/a": Fetched("https://www.semi.org/a", "规模约120亿美元", date(2026, 7, 1)),
    "https://www.miit.gov.cn/b": Fetched("https://www.miit.gov.cn/b", "印发行动方案", None),
    "https://finance.example.com/c": Fetched("https://finance.example.com/c", "前三大份额", None),
    "https://price.example.com/d": Fetched("https://price.example.com/d", "报价上涨", None),
}


def test_抓到的文档入语料库并按最终域名分级(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 4
    by_url = {d.url: d for d in cp.docs}
    assert by_url["https://www.semi.org/a"].tier is Tier.OFFICIAL
    assert by_url["https://www.miit.gov.cn/b"].tier is Tier.OFFICIAL
    assert by_url["https://finance.example.com/c"].tier is Tier.MEDIA
    assert len(res.docs) == 4


def test_未取到的固定槽位被报成缺口(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert set(res.missing_slots) == {"上下游价格与供需", "公司重大动态"}


def test_额外槽位不参与缺口判定(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.extra_slots == ["覆铜板批价"]
    assert "覆铜板批价" not in res.missing_slots


def test_重定向后按最终域名分级(monkeypatch):
    """模型给 gov.cn 链接、实际 302 到博客——必须按博客落 MEDIA。"""
    import ir_agent.sources.openweb as OW
    pages = {"https://www.miit.gov.cn/b":
             Fetched("https://blog.example.com/real", "正文", None)}
    monkeypatch.setattr(OW, "fetch", _fake_fetch(pages))
    cp = Corpus(as_of=AS_OF)
    gather(_Client("候选: 政策与监管 | 政策 | https://www.miit.gov.cn/b | 工信部 | 2026-06-15\n"),
           code="600183", company="生益科技", industry="电子/元件",
           as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 1
    assert cp.docs[0].tier is Tier.MEDIA
    assert cp.docs[0].url == "https://blog.example.com/real"


def test_抓取失败不牵连其余(monkeypatch):
    import ir_agent.sources.openweb as OW
    partial = {k: v for k, v in PAGES.items() if "semi.org" not in k}
    monkeypatch.setattr(OW, "fetch", _fake_fetch(partial))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 3
    assert res.fetch_failed == 1


def test_gather经历真实ChunkedEncodingError仍计入失败不牵连其余(monkeypatch):
    """Fix 1 的端到端复现：不 mock `openweb.fetch` 本身，只 mock
    `requests.get`——让其中一个候选的 `iter_content()` 在读正文时一直
    抛 `ChunkedEncodingError`（`retry._TRANSIENT` 点名的瞬时故障），
    走 `fetch()` 真实的重试与转换逻辑。此前这个异常会从 `with_retry`
    与 `FetchFailed` 的双重边界外面原样冒出去，`gather()` 接不住，
    连累同一轮其余候选的入库与落盘。这里验证：其余三个候选正常入库，
    出问题的这一个被计入 `fetch_failed`，不多不少。"""
    import ir_agent.sources.openweb as OW
    import ir_agent.sources.retry as RETRY

    monkeypatch.setattr(RETRY.time, "sleep", lambda s: None)
    monkeypatch.setattr(OW.socket, "getaddrinfo", _fake_getaddrinfo_public)

    good_bodies = {
        "https://www.miit.gov.cn/b": "印发行动方案",
        "https://finance.example.com/c": "前三大份额",
        "https://price.example.com/d": "报价上涨",
    }

    def _fake_get(url, *a, **k):
        if "semi.org" in url:
            return _FlakyBodyResp(url, "<p>不会读到这段</p>".encode("utf-8"),
                                  fail_times=99)
        return _FakeFetchResp(url, good_bodies[url].encode("utf-8"))

    monkeypatch.setattr(OW.requests, "get", _fake_get)
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 3
    assert len(res.docs) == 3
    assert res.fetch_failed == 1


def test_gather对per_candidate的保护接住任意异常不只接FetchFailed(monkeypatch):
    """防御纵深：`gather()` 对每个候选的 `fetch()` 调用曾经只接
    `FetchFailed`，看起来"更精确"，代价是上一次修复正是从这里烂掉的
    ——`fetch()` 内部一旦有任何疏漏漏出一个非 `FetchFailed` 的裸异常，
    这道本该是 per-candidate 的防线就直接失效，冒到 `gather()` 外面
    连累整轮采集。这里不依赖 `fetch()` 本身是否已经堵严实，直接让
    mock 的 `fetch()` 抛一个裸异常，验证 `gather()` 自己这道防线接得住。
    """
    import ir_agent.sources.openweb as OW

    def _boom_then_fetch(pages):
        inner = _fake_fetch(pages)

        def _f(url, *, timeout=OW.TIMEOUT):
            if "semi.org" in url:
                raise RuntimeError("模拟一个从 fetch() 内部逃出来的裸异常")
            return inner(url, timeout=timeout)
        return _f

    monkeypatch.setattr(OW, "fetch", _boom_then_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 3
    assert res.fetch_failed == 1


def test_不同抓取失败原因在结果里去重且可区分(monkeypatch):
    """Fix 4：`fetch_failed` 曾经是个不透明的计数，「模型给了个 PDF」
    「这个域名 DNS 解析不出来」等四种截然不同的诊断打印出来长得一模
    一样——有本机代理时甚至会让全部候选一次性倒在同一个原因上，读者
    看到的只有一个数字，无从判断该往哪查。这里验证不同原因被分别记下，
    且能在 `summary()` 里读到具体是哪几种。"""
    import ir_agent.sources.openweb as OW

    def _mixed_fail(url, *, timeout=OW.TIMEOUT):
        if "semi.org" in url:
            raise OW.FetchFailed(f"{url}：DNS 解析失败（模拟代理环境）")
        if "miit.gov.cn" in url:
            raise OW.FetchFailed(
                f"{url}：内容类型不是网页（application/pdf）")
        return _fake_fetch(PAGES)(url, timeout=timeout)

    monkeypatch.setattr(OW, "fetch", _mixed_fail)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.fetch_failed == 2
    assert len(res.fetch_failed_reasons) == 2
    text = summary(res)
    assert "DNS 解析失败" in text
    assert "内容类型不是网页" in text


def test_同一原因重复出现只记一次(monkeypatch):
    """去重是这个字段存在的前提——同一种诊断（比如代理环境下所有候选
    都解析不出 DNS）反复出现十次，读者要看到的是"这一种原因"，不是
    同一句话被打印十次。"""
    import ir_agent.sources.openweb as OW

    def _same_fail(url, *, timeout=OW.TIMEOUT):
        raise OW.FetchFailed(f"{url}：DNS 解析失败（模拟代理环境）")

    monkeypatch.setattr(OW, "fetch", _same_fail)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.fetch_failed == 4
    # 四个候选的失败消息只有 URL 不同、诊断本身相同（DNS 解析失败）——
    # 这条测试要求诊断层面的去重，因此逐个候选构造消息去掉 URL 前缀，
    # 而不是断言消息整体唯一。
    reasons_without_url = {r.split("：", 1)[-1] for r in res.fetch_failed_reasons}
    assert reasons_without_url == {"DNS 解析失败（模拟代理环境）"}


def test_落盘读回后失败原因逐字还原(tmp_path, monkeypatch):
    """Fix 4 要求 `fetch_failed_reasons` 跟着 `missing_slots`/
    `extra_slots` 同样的路径跨阶段存活——第二阶段的 `_restore_openweb`
    读的是落盘文件，不重新采集，读不到这个字段就等于第二阶段的读者
    看不到第一阶段已经查明的失败原因。"""
    import ir_agent.sources.openweb as OW

    def _mixed_fail(url, *, timeout=OW.TIMEOUT):
        if "semi.org" in url:
            raise OW.FetchFailed(f"{url}：DNS 解析失败（模拟代理环境）")
        return _fake_fetch(PAGES)(url, timeout=timeout)

    monkeypatch.setattr(OW, "fetch", _mixed_fail)
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert res.fetch_failed_reasons  # 确认这条测试真的在测非空列表

    path = tmp_path / OW.OPENWEB_JSON
    OW.save(res, path)
    back = OW.load(path, Corpus(as_of=AS_OF))
    assert back.fetch_failed_reasons == res.fetch_failed_reasons


def test_晚于as_of的候选被挡下(monkeypatch):
    import ir_agent.sources.openweb as OW
    late = "候选: 政策与监管 | 政策 | https://www.miit.gov.cn/b | 工信部 | 2026-12-31\n"
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    gather(_Client(late), code="600183", company="生益科技",
           industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert cp.docs == []
    assert cp.skipped != []


def test_每槽位最多两篇(monkeypatch):
    import ir_agent.sources.openweb as OW
    three = "".join(
        f"候选: 政策与监管 | 政策{i} | https://www.miit.gov.cn/{i} | 工信部 | 2026-06-15\n"
        for i in range(3))
    pages = {f"https://www.miit.gov.cn/{i}":
             Fetched(f"https://www.miit.gov.cn/{i}", f"正文{i}", None)
             for i in range(3)}
    monkeypatch.setattr(OW, "fetch", _fake_fetch(pages))
    cp = Corpus(as_of=AS_OF)
    gather(_Client(three), code="600183", company="生益科技",
           industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 2


def test_已在语料库里的URL不重复入库(monkeypatch):
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Document
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="x", url="https://www.semi.org/a", title="已有",
                    publisher="SEMI", tier=Tier.SELLSIDE,
                    published=date(2026, 5, 1), retrieved=AS_OF, text="已有正文"))
    gather(_Client(FOUR), code="600183", company="生益科技",
           industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert [d.url for d in cp.docs].count("https://www.semi.org/a") == 1


def test_日期来源统计_meta优先于模型(monkeypatch):
    """用户定：信模型报的日期；折中是代码能提到就以代码为准，并把
    「有几篇的日期未经代码核验」说出来。"""
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    # PAGES 里只有 semi.org 那篇带 meta 日期，其余三篇用模型报的
    assert res.model_dated == 3


def test_summary_逐项说出缺口与失败(monkeypatch):
    import ir_agent.sources.openweb as OW
    partial = {k: v for k, v in PAGES.items() if "semi.org" not in k}
    monkeypatch.setattr(OW, "fetch", _fake_fetch(partial))
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    text = summary(res)
    assert "上下游价格与供需：未取到" in text
    assert "公司重大动态：未取到" in text
    assert "1 篇抓取失败" in text


def test_候选日期早但页面meta日期晚于as_of则被挡下(monkeypatch):
    """时点守卫的第二层：候选日期在 as_of 前但页面 meta 日期在 as_of 后。

    候选行给出的日期是模型的声明，页面 meta 里的日期是代码实际观测到的。
    时点守卫必须在后者起效——模型的日期声明本就不可信，代码抓到的证据才是准。
    """
    import ir_agent.sources.openweb as OW

    # 候选日期远早于 as_of，能通过 gather 的早期检查
    early_cand = (
        "候选: 政策与监管 | 政策文件 | https://www.example.com/policy | 某部委 | 2026-06-15\n"
    )

    # 但页面本身声称的 meta 日期在 as_of 之后
    pages = {
        "https://www.example.com/policy": Fetched(
            "https://www.example.com/policy",
            "这是政策文件的正文内容",
            date(2026, 9, 25)  # meta_published 在 AS_OF (2026-09-20) 之后
        )
    }

    monkeypatch.setattr(OW, "fetch", _fake_fetch(pages))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(early_cand), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)

    # 应该被 add_if_visible 挡下，不入库
    assert len(cp.docs) == 0, "页面 meta 日期晚于 as_of 应该被拒绝入库"
    assert len(res.docs) == 0
    # 应该在 corpus.skipped 里记录被拒绝的文件
    assert len(cp.skipped) > 0, "被挡下的文档应该记录在 skipped"
    # 没有文档入库，所以 model_dated 也应该是 0（这篇文章未被计入）
    assert res.model_dated == 0


def test_两个候选重定向到同一最终网址只入库一次(monkeypatch):
    """去重：两个不同候选 URL 重定向到同一个最终 URL，不应该重复入库。

    这是代码层面的后置去重——与其他 URL 已在 seen 里的检查不同，这里通过的
    是不同的候选行（甚至不同的槽位），却在 fetch 时被发现指向同一个最终地址。
    """
    import ir_agent.sources.openweb as OW

    two_cands = (
        "候选: 政策与监管 | 政策文件A | https://www.a.com/page | 某部委A | 2026-06-15\n"
        "候选: 政策与监管 | 政策文件B | https://www.b.com/page | 某部委B | 2026-06-20\n"
    )

    # 两个不同的候选 URL 都最终重定向到同一个地址
    final_url = "https://final.example.com/official-policy"
    pages = {
        "https://www.a.com/page": Fetched(final_url, "政策正文第一版", None),
        "https://www.b.com/page": Fetched(final_url, "政策正文第二版", None),
    }

    monkeypatch.setattr(OW, "fetch", _fake_fetch(pages))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(two_cands), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)

    # 应该只在语料库里出现一次，不重复
    assert len(cp.docs) == 1, f"期望 1 篇文档，实际 {len(cp.docs)} 篇"
    assert len(res.docs) == 1, f"期望 res.docs 有 1 篇，实际 {len(res.docs)} 篇"
    # 验证入库的是最终 URL
    assert cp.docs[0].url == final_url


def test_落盘读回后文档逐字还原(tmp_path, monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    path = tmp_path / OW.OPENWEB_JSON
    OW.save(res, path)

    back_cp = Corpus(as_of=AS_OF)
    back = OW.load(path, back_cp)
    assert len(back_cp.docs) == len(cp.docs)
    for a, b in zip(cp.docs, back_cp.docs):
        assert (a.doc_id, a.url, a.title, a.publisher) == \
               (b.doc_id, b.url, b.title, b.publisher)
        assert a.tier is b.tier
        assert (a.published, a.retrieved, a.text) == \
               (b.published, b.retrieved, b.text)
        assert a.prompt_limit == b.prompt_limit
        assert a.date_source == b.date_source
    assert back.missing_slots == res.missing_slots
    assert back.extra_slots == res.extra_slots
    assert back.fetch_failed == res.fetch_failed
    assert back.model_dated == res.model_dated


def test_日期来源逐篇记录(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    gather(_Client(FOUR), code="600183", company="生益科技",
           industry="电子/元件", as_of=AS_OF, corpus=cp)
    by_url = {d.url: d for d in cp.docs}
    assert by_url["https://www.semi.org/a"].date_source == "meta"
    assert by_url["https://www.miit.gov.cn/b"].date_source == "model"


def test_读回时晚于as_of的文档仍被挡下(tmp_path):
    """时点守卫不能因为「这是我们自己存的」就放行 —— as_of 可能变了。"""
    import json

    import ir_agent.sources.openweb as OW
    path = tmp_path / OW.OPENWEB_JSON
    path.write_text(json.dumps({
        "docs": [{"doc_id": "ow_x", "url": "https://x.gov.cn/a",
                  "title": "标题", "publisher": "某部委", "tier": "official",
                  "published": "2026-12-31", "retrieved": "2026-09-20",
                  "text": "正文", "prompt_limit": 2000, "date_source": "model"}],
        "missing_slots": [], "extra_slots": [], "fetch_failed": 0,
        "model_dated": 1}, ensure_ascii=False), encoding="utf-8")
    cp = Corpus(as_of=AS_OF)
    OW.load(path, cp)
    assert cp.docs == []
    assert cp.skipped != []


def test_文件不存在时返回空结果而不抛(tmp_path):
    import ir_agent.sources.openweb as OW
    cp = Corpus(as_of=AS_OF)
    back = OW.load(tmp_path / "nope.json", cp)
    assert back.docs == []
    assert cp.docs == []


# ── load() 对结构性损坏的容忍 ────────────────────────────────────
#
# 前面 test_文件不存在时返回空结果而不抛 只测了「文件不存在」。这里补
# Finding 1 报的那几种「文件存在、但内容不是我们写的那种形状」的损坏：
# 语法错误的 JSON、顶层不是 dict、数值字段塞了非数字字符串、列表字段
# 塞了不可迭代的东西。任何一种都必须退化成空 OpenWebResult()，不能把
# 异常甩给调用方——调用方的整条契约就是「采不到就是这节缺资料」，不是
# 「有时候会因为一个坏文件让整次研究流程崩掉」。


def test_JSON语法错误返回空结果不抛(tmp_path):
    import ir_agent.sources.openweb as OW
    path = tmp_path / OW.OPENWEB_JSON
    path.write_text("{not valid json", encoding="utf-8")
    cp = Corpus(as_of=AS_OF)
    back = OW.load(path, cp)
    assert back.docs == []
    assert cp.docs == []


def test_顶层不是dict时返回空结果(tmp_path):
    """`[1,2,3]` / `"hello"` / `42`：都没有 .get，直接构造 OpenWebResult
    会在 raw.get(...) 上炸 AttributeError。"""
    import json

    import ir_agent.sources.openweb as OW
    for bad_top in ([1, 2, 3], "hello", 42):
        path = tmp_path / OW.OPENWEB_JSON
        path.write_text(json.dumps(bad_top), encoding="utf-8")
        cp = Corpus(as_of=AS_OF)
        back = OW.load(path, cp)
        assert back.docs == [], bad_top
        assert cp.docs == [], bad_top


def test_fetch_failed或model_dated非数字字符串时返回空结果(tmp_path):
    """`int("abc")` 抛 ValueError——这两个字段本该是采集阶段自己写的
    计数，但文件被手改坏了就不是。"""
    import json

    import ir_agent.sources.openweb as OW
    for bad_field in ("fetch_failed", "model_dated"):
        path = tmp_path / OW.OPENWEB_JSON
        payload = {"docs": [], "missing_slots": [], "extra_slots": [],
                   "fetch_failed": 0, "model_dated": 0}
        payload[bad_field] = "abc"
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        cp = Corpus(as_of=AS_OF)
        back = OW.load(path, cp)
        assert back.docs == [], bad_field
        assert cp.docs == [], bad_field


def test_missing_slots或extra_slots不可迭代时返回空结果(tmp_path):
    """`list(42)` 抛 TypeError——这两个字段本该是字符串列表。"""
    import json

    import ir_agent.sources.openweb as OW
    for bad_field in ("missing_slots", "extra_slots"):
        path = tmp_path / OW.OPENWEB_JSON
        payload = {"docs": [], "missing_slots": [], "extra_slots": [],
                   "fetch_failed": 0, "model_dated": 0}
        payload[bad_field] = 42
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        cp = Corpus(as_of=AS_OF)
        back = OW.load(path, cp)
        assert back.docs == [], bad_field
        assert cp.docs == [], bad_field


def test_单条文档损坏不牵连其余仍然入库(tmp_path):
    """回归用例：这条在修复前就已经能过——`_doc_from_json` 早就被单独
    包了一层 try/except。补上是因为 Finding 2 指出这条路径此前完全没
    有测试覆盖，修复时很容易在改 load() 时手滑连累它。"""
    import json

    import ir_agent.sources.openweb as OW
    good_doc = {"doc_id": "ow_good", "url": "https://x.gov.cn/good",
                "title": "标题", "publisher": "某部委", "tier": "official",
                "published": "2026-06-01", "retrieved": "2026-09-20",
                "text": "正文", "prompt_limit": 2000, "date_source": "model"}
    bad_doc = {"doc_id": "ow_bad"}  # 缺字段，_doc_from_json 会 KeyError
    path = tmp_path / OW.OPENWEB_JSON
    payload = {"docs": [good_doc, bad_doc], "missing_slots": [],
               "extra_slots": [], "fetch_failed": 0, "model_dated": 0}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    cp = Corpus(as_of=AS_OF)
    back = OW.load(path, cp)
    assert [d.doc_id for d in back.docs] == ["ow_good"]
    assert [d.doc_id for d in cp.docs] == ["ow_good"]


# ── _doc_from_json 对字段类型的校验 ─────────────────────────────
#
# Finding 3：前面几条只覆盖了「缺字段」这一种损坏（bad_doc 直接 KeyError）。
# 但一个字段齐全、类型却不对的条目（比如 prompt_limit 是字符串、text 是
# 数字或 None）此前会顺利构造出 Document、混进语料库，直到 phase-2 的
# for_prompt()/verify() 才崩——已经在 load() 的保护范围之外。这里的三条
# 用例都拿「一条写坏的条目 + 一条写好的条目」配对，确认坏的那条被
# _doc_from_json 拦下（进而被 load() 的逐条 try/except 跳过），好的那条
# 照常入库——而不是随手改一个字段就让整份文件退化成空结果。


def _good_doc(doc_id: str = "ow_good") -> dict:
    return {"doc_id": doc_id, "url": f"https://x.gov.cn/{doc_id}",
            "title": "标题", "publisher": "某部委", "tier": "official",
            "published": "2026-06-01", "retrieved": "2026-09-20",
            "text": "正文", "prompt_limit": 2000, "date_source": "model"}


def test_prompt_limit类型不对时该条被跳过其余仍然入库(tmp_path):
    import json

    import ir_agent.sources.openweb as OW
    good_doc = _good_doc()
    bad_doc = {**_good_doc("ow_bad"), "prompt_limit": "notanint"}
    path = tmp_path / OW.OPENWEB_JSON
    payload = {"docs": [good_doc, bad_doc], "missing_slots": [],
               "extra_slots": [], "fetch_failed": 0, "model_dated": 0}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    cp = Corpus(as_of=AS_OF)
    back = OW.load(path, cp)
    assert [d.doc_id for d in back.docs] == ["ow_good"]
    assert [d.doc_id for d in cp.docs] == ["ow_good"]


def test_text类型不对时该条被跳过其余仍然入库(tmp_path):
    """text=None 或 text=12345 若不在 _doc_from_json 拦下，会一路活到
    doc.for_prompt()/corpus.verify() 才炸 TypeError——已经在 load() 的
    保护范围之外。"""
    import json

    import ir_agent.sources.openweb as OW
    for bad_text in (None, 12345):
        good_doc = _good_doc()
        bad_doc = {**_good_doc("ow_bad"), "text": bad_text}
        path = tmp_path / OW.OPENWEB_JSON
        payload = {"docs": [good_doc, bad_doc], "missing_slots": [],
                   "extra_slots": [], "fetch_failed": 0, "model_dated": 0}
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        cp = Corpus(as_of=AS_OF)
        back = OW.load(path, cp)
        assert [d.doc_id for d in back.docs] == ["ow_good"], bad_text
        assert [d.doc_id for d in cp.docs] == ["ow_good"], bad_text


def test_字符串字段类型不对时该条被跳过其余仍然入库(tmp_path):
    """doc_id / url / title / publisher / date_source 里随便哪个被塞了
    非字符串值，都该在 _doc_from_json 这道边界上拦下，而不是留给
    调用方在别处撞见 TypeError。"""
    import json

    import ir_agent.sources.openweb as OW
    for field in ("doc_id", "url", "title", "publisher", "date_source"):
        good_doc = _good_doc()
        bad_doc = {**_good_doc("ow_bad"), field: 12345}
        path = tmp_path / OW.OPENWEB_JSON
        payload = {"docs": [good_doc, bad_doc], "missing_slots": [],
                   "extra_slots": [], "fetch_failed": 0, "model_dated": 0}
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        cp = Corpus(as_of=AS_OF)
        back = OW.load(path, cp)
        assert [d.doc_id for d in back.docs] == ["ow_good"], field
        assert [d.doc_id for d in cp.docs] == ["ow_good"], field
