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


def test_中间空标签不算gov_cn子域():
    """真实事故：`.gov.cn` 的空标签守卫只特判了「开头是空标签」这一种
    形态（`host.startswith(".")`）。`"a..gov.cn"` 的空标签在中间——
    不以 `.` 开头，但 `"a..gov.cn".endswith(".gov.cn")` 同样为真，旧
    实现会漏过这条守卫、把它判成 gov.cn 的子域从而拔到 OFFICIAL。
    `all(host.split("."))` 一次性堵住所有空标签形态，不管出现在哪。"""
    assert tier_of_url("https://a..gov.cn/x") is Tier.MEDIA


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


def test_未闭合的可见噪声容器不吞掉之后的真实正文():
    """**本次改写的核心设计决策**（见 `_TextExtractor` 与模块级长注释）：
    `<nav>`/`<header>`/`<footer>`/`<aside>` 这类可见噪声容器如果一直到
    文档结尾都没有闭合标签，缓冲区要并入正文，不能丢弃——验证跑里真实
    出现过手写导航栏漏写 `</nav>` 的页面，旧正则实现的取舍是"匹配不到
    收尾就整个不剥"，代价是这个容器之后**所有**真实正文都被一并当成
    "还在容器里"从而被吞掉。

    这里不闭合 `<nav>`，断言其后的 `<p>正文</p>` 必须完整保留——这是
    不能妥协的核心保证。至于 `<nav>` 自己的"首页 产品 关于我们"这几个
    导航词是否也跟着漏进来，本条测试不作断言：那是本次改写明确接受的
    代价（噪声/召回问题，比丢真实正文轻得多），断言了反而会把这个
    可接受的实现细节焊死成契约。"""
    html = "<nav>首页 产品 关于我们<p>正文</p>"
    got = extract_text(html)
    assert "正文" in got


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


def test_未闭合的HTML注释不泄漏到文档结尾():
    """真实事故：`html.parser.HTMLParser` 对没有 `-->` 收尾的注释，只在
    `feed()` 过程中还没吃到 `close()` 时才会按"不完整，先攒着等下一次
    feed"处理——但本模块只调用一次 `feed()` 后立刻 `close()`，触发的是
    `close()`（即 `goahead(1)`）内部找不到收尾定界符时的退化路径：把从
    `<!--` 起点到文档结尾的剩余原始字节**原样交给 `handle_data`**，当
    成普通正文。`handle_comment` 根本没被调用，`_TextExtractor` 用来
    识别"这是不可见内容"的 `_invisible_depth` 计数器也从未加过——这个
    洞在深度计数器这一层完全看不见。

    `out/600183-openweb/openweb.json` 里存的 10 篇真实抓取页面已经证实
    这类畸形注释标记不是假设：里面有 62 处孤立的 `-->` 残留，说明这些
    站点确实会出这种没头没尾的注释。放行到 Corpus 里最致命的后果正是
    第二个用例：草稿/内部批注里写的数字（"实为 45%，对外口径 30%"）会
    被当成"页面上真实出现过的文字"通过 `Corpus.verify()` 的逐字核验，
    而人打开这同一个网址永远看不到这句话——这正是本模块存在的理由
    （verbatim 可核验）要防止的伪造风险。"""
    html = "<p>A</p><!-- 草稿：覆铜板均价下跌 30%\n<p>B</p>"
    got = extract_text(html)
    assert got == "A"
    assert "草稿" not in got
    assert "30%" not in got
    assert "<!--" not in got

    html2 = "<p>真实正文</p><!-- 内部备注：本季毛利率实为 45%，对外口径 30%"
    got2 = extract_text(html2)
    assert got2 == "真实正文"
    assert "内部备注" not in got2
    assert "45%" not in got2


def test_未闭合的CDATA不泄漏到文档结尾():
    """与未闭合注释同一个洞的另一种触发方式：`<![CDATA[` 同样以 `<!`
    开头、同样可能没有 `]]>` 收尾，同样会在 `close()` 里被
    `html.parser` 当成"找不到收尾，把剩余原始字节整段喂给
    handle_data"处理，`_invisible_depth` 同样从未加过。"""
    html = "<p>A</p><![CDATA[内部草稿"
    got = extract_text(html)
    assert got == "A"
    assert "内部草稿" not in got
    assert "CDATA" not in got


def test_正常闭合的注释与doctype声明不受未闭合修复影响():
    """`_TextExtractor.close()` 的修复只在 `close()` 触发时
    `self.rawdata` 仍以 `<!` 开头（即 feed() 结束时还剩一段没能在
    `feed()` 过程中被完整解析掉的 `<!...` 声明）才生效。正常闭合的
    `<!-- ... -->` 在 `feed()` 期间就已经被 `parse_comment` 完整消费，
    不会剩在 `rawdata` 里；`<!doctype html>` 同理在 `feed()` 期间就被
    `parse_html_declaration` 处理掉。这条测试确认修复没有误伤这两种
    收尾正常的声明。"""
    html = "<p>正文</p><!-- 正常注释 -->\n<p>后续</p>"
    got = extract_text(html)
    assert "正文" in got
    assert "后续" in got
    assert "正常注释" not in got

    html2 = "<!doctype html>\n<p>正文</p>"
    got2 = extract_text(html2)
    assert got2 == "正文"


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


# ── final-fix-5 Fix 2：`_meta_charset` 曾经在任意含 "charset=" 子串的
# `<meta>` 标签里裸找这个词，不管它是不是真的编码声明、也不管它是不是
# 被注释掉的死代码。ISO-8859-1 是全字节映射、`decode` 永不抛异常，一旦
# 被这类假阳性选中，`_decode` 会"成功"解码出一串乱码，且没有 HTTP 头
# 分支三行前那道 iso-8859-1 排除兜底——三条真实场景都是先前能正确解码
# 的 UTF-8 页面，被这个洞重新变成乱码 ──


def test_meta声明_非声明属性里恰好含charset子串不误判():
    """`<meta name="description" content="...charset=iso-8859-1...">` 里
    的 "charset=" 只是一段与编码声明无关的正文，真正生效的声明在后面
    那个 `<meta charset="utf-8">`——必须取到后者，不能被前面这个假阳性
    抢先命中。"""
    import ir_agent.sources.openweb as OW

    text = "真实正文：覆铜板均价上涨12%"
    head = ('<meta name="description" content="see charset=iso-8859-1 '
           'for details"><meta charset="utf-8">').encode("ascii")
    raw = head + text.encode("utf-8")
    assert OW._meta_charset(raw) == "utf-8"
    assert text in OW._decode(raw, None)


def test_meta声明_被HTML注释掉的声明不生效():
    """`<!-- <meta charset="iso-8859-1"> legacy -->` 这段标记在注释体
    内，浏览器从不渲染、不解析注释——必须先剥注释，再找真正生效的
    `<meta charset="utf-8">`。"""
    import ir_agent.sources.openweb as OW

    text = "真实正文：覆铜板均价上涨12%"
    head = ('<!-- <meta charset="iso-8859-1"> legacy -->'
           '<meta charset="utf-8">').encode("ascii")
    raw = head + text.encode("utf-8")
    assert OW._meta_charset(raw) == "utf-8"
    assert text in OW._decode(raw, None)


def test_meta声明_http_equiv声明iso_8859_1时被排除改用猜测链():
    """ISO-8859-1 是全字节映射，`decode` 永不抛异常——`<meta
    http-equiv>` 声明的编码即便就是 iso-8859-1，也必须跟 HTTP 头分支
    一样被排除，落到 UTF-8/GB18030 猜测链，而不是被这个"从不失败"的
    解码器直接拿去用，把真实是 UTF-8 的字节解成乱码。"""
    import ir_agent.sources.openweb as OW

    text = "真实正文：覆铜板均价上涨12%"
    head = (b'<meta http-equiv="Content-Type" '
           b'content="text/html; charset=iso-8859-1">')
    raw = head + text.encode("utf-8")
    assert OW._meta_charset(raw) == "iso-8859-1"
    assert text in OW._decode(raw, None)


def test_meta声明_真正的Big5声明仍然生效不被误伤():
    """回归用例：修 Fix 2 的三个假阳性洞，不能连带把 `<meta
    charset="Big5">` 这种真声明也拦下——这正是 `_meta_charset` 最初
    存在的理由（CCL/PCB 覆盖点名的台湾一手来源仍有用 Big5 的）。"""
    import ir_agent.sources.openweb as OW

    text = "報價上漲"
    body = text.encode("big5")
    head = b'<meta charset="Big5">'
    raw = head + body
    assert OW._meta_charset(raw) == "Big5"
    assert text in OW._decode(raw, None)


# ── 本轮修复 Fix：`_decode` 排除 ISO-8859-1 时按字符串字面量比较
# （`enc.lower() != "iso-8859-1"`），只堵得住这一种拼法——`latin-1`/
# `latin1`/`cp819`/`iso8859-1`/`L1` 全部是同一个编码的合法别名，
# 字面量比较认不出来，会原样放行、把真实是 UTF-8 的字节解成乱码。
# 必须改成 `codecs.lookup(enc).name` 归一化后比较，两个分支（HTTP 头、
# meta 声明）都要堵 ──


def test_解码_iso_8859_1的常见别名都被排除不直接使用():
    """header_encoding 给出这些别名中的任意一个，都不能被直接拿去
    `raw.decode(...)`——它们和 "iso-8859-1" 本身一样，是全字节映射，
    对着真实是 UTF-8 的字节解码永不失败但会产出乱码。必须落到猜测链，
    正确解出 UTF-8 中文。"""
    import ir_agent.sources.openweb as OW

    text = "覆铜板报价上涨"
    raw = text.encode("utf-8")
    for alias in ("iso-8859-1", "latin-1", "latin1", "cp819",
                 "iso8859-1", "L1", "ISO-8859-1"):
        assert OW._decode(raw, alias) == text, alias


def test_解码_meta声明的iso_8859_1别名同样被排除():
    """与上一条对照：排除规则必须同时管住 `<meta http-equiv>` 分支，
    不能只改 HTTP 头分支——两个分支面对的是同一类风险（一个必然不
    报错的解码器一旦被选中就会静默产出乱码）。"""
    import ir_agent.sources.openweb as OW

    text = "覆铜板报价上涨"
    head = (b'<meta http-equiv="Content-Type" '
           b'content="text/html; charset=latin1">')
    raw = head + text.encode("utf-8")
    assert text in OW._decode(raw, None)


def test_meta声明认不出的编码名时退回猜测链():
    """页面自己声明了一个压根不存在的编码——`raw.decode(enc)` 会抛
    `LookupError`，必须被接住退到 UTF-8/GB18030 猜测链，不能让
    `LookupError` 冒出 `_decode`。"""
    import ir_agent.sources.openweb as OW

    text = "覆铜板报价上涨"
    head = b'<meta charset="not-a-real-encoding-xyz">'
    raw = head + text.encode("utf-8")
    assert OW._meta_charset(raw) == "not-a-real-encoding-xyz"
    assert OW._decode(raw, None) == raw.decode("utf-8")


def test_解码_header与meta声明冲突时以meta为准():
    """HTTP 头没有声明真实编码（走 ISO-8859-1 默认猜测分支，被排除），
    页面自己在 `<meta>` 里声明了另一种——`<meta>` 是页面自己说的，必须
    优先于 HTTP 头那个"没声明时的默认猜测"，能正确解出 meta 声明的
    那种编码。"""
    import ir_agent.sources.openweb as OW

    text = "報價上漲"
    body = text.encode("big5")
    head = b'<meta charset="Big5">'
    raw = head + body
    # header_encoding 传 None，模拟 HTTP 头没有声明（走 meta 分支）。
    assert text in OW._decode(raw, None)
    # 即便 header_encoding 明确给了一个错误的猜测（ISO-8859-1，会被
    # 排除），也要落到 meta 分支正确解码，而不是被这个错误的头声明
    # 抢先"成功"解码成乱码。
    assert text in OW._decode(raw, "ISO-8859-1")


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


def _nested_nav_with_noise_at_every_level(depth: int, marker_prefix: str) -> str:
    """构造 `depth` 层自嵌套 `<nav>`，**每一层自己的开标签后面都直接跟
    一段该层专属的噪声文字**（`{marker_prefix}0`、`{marker_prefix}1`……），
    不是只把噪声放在最内层。

    这个区别是这条测试是否具有区分力的关键。旧实现（`_strip_blocks`，
    正则反复 `sub` 直到不再变化，`_BLOCK_MAX_PASSES = 32` 封顶）一遍
    `_BLOCK.sub` 只吃得掉"不含嵌套"的最内层那一对——如果噪声只写在最
    内层（旧版本这条测试的构造方式），最内层那对标签在**第一遍**就已
    经连标签带内容一起被替换掉，后续 31 遍处理的只是外层剩下的、内容
    已经清空的空壳 `<nav> </nav>`，跟深度是 20 还是 1000 无关——这正是
    实测验证到的：旧实现在这种构造下，深度 1000 也不泄漏一个字，这条
    测试因此对旧实现和新实现给出相同的"通过"结果，起不到区分作用（见
    `.superpowers/sdd/final-gate-report.md`）。

    每层都放一段专属噪声之后，情况不同：第 i 层的噪声文字属于"第 i 层
    以内、第 i+1 层以外"的那个标签对，必须先剥掉所有比它更深的嵌套、
    轮到它自己被识别成"不含嵌套的最内层"才会被处理——第 i 层要等到第
    `depth - i` 遍才被剥到。旧实现封顶在 32 遍，depth 超过 32 时，深度
    编号小于 `depth - 32` 的那些外层噪声永远轮不到，会跟着未剥净的
    `<nav>` 标签一起被 `_TAG.sub` 转成裸文字漏出来。"""
    opens = [f"<nav>{marker_prefix}{i} " for i in range(depth)]
    closes = [f" {marker_prefix}{i}</nav>" for i in range(depth)]
    closes.reverse()
    return "".join(opens) + "最内层噪声" + "".join(closes)


def test_深度嵌套容器无论多深都能完全剥掉():
    """**回归改写，且已用旧实现验证过区分力。** 旧实现用正则反复 `sub`
    到不再变化为止，`_BLOCK_MAX_PASSES = 32` 给这个循环封顶——每一遍
    只能剥掉"当前不含嵌套的最内层"那一对，深度超过 32 时，编号小于
    `depth - 32` 的外层永远轮不到被剥，其自带的噪声文字会跟着未剥净的
    标签一起漏出来（构造细节见 `_nested_nav_with_noise_at_every_level`
    的说明）。改写成 `html.parser` 之后，噪声容器的剔除靠
    `_TextExtractor` 的深度计数器，一次线性扫描处理任意嵌套深度，
    `_BLOCK_MAX_PASSES` 这个概念随旧实现一起被删掉了。

    这条测试曾经把噪声只放在最内层，深度推到 1000——但那种构造下旧
    实现在第一遍就已经把噪声连标签一起吃掉，深度 20/40/1000 对旧实现
    而言结果完全一样（不泄漏），测试对新旧实现给出相同的"通过"，没有
    任何区分力，等于没测到旧实现在深度 40 就已经会泄漏这件事。这里改成
    每一层都带专属噪声标记，已用 `git worktree` 检出旧实现（commit
    a6b3bcb）实测确认：depth=40 时旧实现泄漏 8 个外层标记，depth=1000
    时泄漏 968 个；新实现在这两个深度下都是 0 个。"""
    depth = 1000  # 远超旧实现 _BLOCK_MAX_PASSES = 32 曾经的上限
    nested = _nested_nav_with_noise_at_every_level(depth, "N")
    real_content = "真实内容"
    html = f"<div><p>{real_content}</p>{nested}</div>"

    got = extract_text(html)

    # 真实内容必须保留
    assert real_content in got
    # 最内层噪声、以及每一层自带的噪声标记都必须被彻底剔除
    assert "最内层噪声" not in got
    leaked = [f"N{i}" for i in range(depth) if f"N{i}" in got]
    assert leaked == [], f"泄漏了 {len(leaked)} 层噪声标记，例如 {leaked[:5]}"


def test_容器深度极深且真实内容夹在中间仍不丢失():
    """**回归改写，且已用旧实现验证过区分力**（同上一条，构造方式见
    `_nested_nav_with_noise_at_every_level`）。旧实现的姊妹测试断言
    "深度超过 32 时噪声可能泄露，但容器外的真实内容必须保留"——那是给
    旧实现的遍数上限留的让步；但那条测试把噪声只放在最内层，旧实现在
    这种构造下深度 1000 也不会泄漏，"可能泄露"这个前提从未被真正触发
    过。这里改成每层都带专属噪声标记后，新实现没有遍数上限，断言更强
    的保证：即使噪声容器深度远超旧上限，前后的真实内容也必须完整保留，
    且每一层的噪声都必须被完全剥掉。"""
    depth = 1000  # 远超旧实现 _BLOCK_MAX_PASSES = 32 曾经的上限
    nested = _nested_nav_with_noise_at_every_level(depth, "M")
    real_before = "前面内容"
    real_after = "后面内容"
    html = f"<div><p>{real_before}</p>{nested}<p>{real_after}</p></div>"

    got = extract_text(html)

    # 容器外的真实内容必须保留
    assert real_before in got, "前面的真实内容被误删了"
    assert real_after in got, "后面的真实内容被误删了"
    # 新实现没有遍数上限，每一层自带的噪声都必须被完全剥掉
    assert "最内层噪声" not in got
    leaked = [f"M{i}" for i in range(depth) if f"M{i}" in got]
    assert leaked == [], f"泄漏了 {len(leaked)} 层噪声标记，例如 {leaked[:5]}"


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
    同一句话被打印十次。

    **此前这条测试名不副实，从未真正走到 gather() 的去重分支。** 旧
    断言是 `{r.split("：", 1)[-1] for r in res.fetch_failed_reasons} ==
    {...}`——现取现算，先把 URL 前缀切掉再拼集合，测的是"测试自己在
    事后能不能算出一个只有一个元素的集合"，跟 `gather()` 是否真的把
    `fetch_failed_reasons` 去重成一条完全无关。旧实现按**整条消息**
    去重，而每条消息都带着各不相同的 URL 前缀（`f"{url}：DNS 解析
    失败..."`），四个候选四个不同 URL，实际会原样存下四条一字不差
    地重复的诊断——旧的断言写法对四条也照样能通过（`split` 之后集合
    还是只剩一个元素），从未真正触发过"这条测试的名字说的那件事"。
    现在 `gather()` 改成按去掉 URL 前缀之后的诊断部分去重，这里直接
    断言 `fetch_failed_reasons` 长度为一，这才是测试名字承诺的东西。
    """
    import ir_agent.sources.openweb as OW

    def _same_fail(url, *, timeout=OW.TIMEOUT):
        raise OW.FetchFailed(f"{url}：DNS 解析失败（模拟代理环境）")

    monkeypatch.setattr(OW, "fetch", _same_fail)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.fetch_failed == 4
    assert len(res.fetch_failed_reasons) == 1
    assert res.fetch_failed_reasons[0].endswith(
        "：DNS 解析失败（模拟代理环境）")


def test_长URL共享同一诊断仍去重为一条(monkeypatch):
    """Fix：`gather()` 的去重键此前从**截断后**的 `reason` 里切出来
    （旧代码是 `str(e).strip()[:_REASON_LIMIT]` 先执行，再
    `split("：", 1)`）。真实新闻 URL 常有 80~100+ 字符，`f"{url}：
    {诊断}"` 拼起来轻松超过 `_REASON_LIMIT`（160）——不同长度的 URL
    被截断后剩下的"诊断尾巴"长度不同，切出来的去重键因此各不相同，
    去重形同虚设，正是本文件字段注释里说的"代理环境下全部候选一次性
    倒在同一种诊断上"那个旗舰场景。这里用四个 100+ 字符、长度互不相同
    的 URL，拼上同一句诊断后总长度都超过 160，验证现在确实只记一条——
    去重键必须从**未截断**的完整消息里切出来，不能受 URL 长度影响。
    """
    import ir_agent.sources.openweb as OW

    diagnosis = "DNS 解析失败（模拟代理环境，跨专线代理转发处理超时且重试耗尽）"
    urls = [
        "https://news.example.com/finance/2026/09/21/article-"
        + "x" * n + "-detail.html?ref=home&utm_source=test"
        for n in (60, 80, 100, 120)
    ]
    assert all(len(u) > 100 for u in urls), urls
    # 确认拼起来确实会超过 _REASON_LIMIT——旧实现的截断只有在这个条件
    # 成立时才会被真正触发，不然这条测试测不出旧 bug。
    assert all(len(f"{u}：{diagnosis}") > OW._REASON_LIMIT for u in urls)

    cands = "".join(
        f"候选: {slot} | 标题{i} | {u} | 某网 | 2026-08-01\n"
        for i, (slot, u) in enumerate(zip(SLOTS, urls))
    )

    def _fail(url, *, timeout=OW.TIMEOUT):
        raise OW.FetchFailed(f"{url}：{diagnosis}")

    monkeypatch.setattr(OW, "fetch", _fail)
    res = gather(_Client(cands), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.fetch_failed == 4
    assert len(res.fetch_failed_reasons) == 1, res.fetch_failed_reasons


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
    """`_str_list_field` 显式判 `isinstance(items, list)`，42 不是 list，
    直接抛 TypeError——这两个字段本该是字符串列表。"""
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


# ── 本轮修复 Fix：`_str_list_field` 曾经写成 `list(raw.get(key, []))`，
# 对任何可迭代对象都来者不拒——字符串、字典都能被 `list()` 接受，悄悄
# 变成一份类型合法但面目全非的假数据，而不是被拒绝。真实事故：
# `{"fetch_failed_reasons": "DNS 解析失败"}` 会被 `list(...)` 拆成单
# 字符列表 `['D','N','S',' ',...]`，每个字符都是合法 str，能顺利通过
# 后面的 `isinstance` 过滤，安安静静地"成功"——比 42 这种一望而知的
# 类型错误更隐蔽，因为它不会抛异常、不会被现有测试的 TypeError 断言
# 覆盖 ──


def test_missing_slots类字段是字符串时被拒绝而非拆成单字符列表(tmp_path):
    """真实事故复现：`fetch_failed_reasons` 的值若是一个字符串（不是
    列表），旧实现的 `list("DNS 解析失败")` 会把它拆成单字符列表，每个
    字符都是合法 `str`，安静地"成功"变成一份面目全非的假数据。这不是
    「容器合法、混进一条脏元素」，是容器本身的类型就不对，必须像
    `_str_field` 拒绝非 str 值一样显式拒绝，整份文件退化成空结果。

    **`docs` 里必须放一条真实文档，不能是空列表。** 用空 `docs` 测
    "整份退化"测不出任何东西——退化前后 `back.docs` 都是 `[]`，两种
    实现看起来一样"通过"。必须放一条能正常读回的文档，只有旧实现让
    它读回、新实现把它一起拖下水退化掉，这条测试才真的在测两种实现
    的区别。"""
    import json

    import ir_agent.sources.openweb as OW
    good_doc = _good_doc()
    for bad_field in ("missing_slots", "extra_slots", "fetch_failed_reasons"):
        path = tmp_path / OW.OPENWEB_JSON
        payload = {"docs": [good_doc], "missing_slots": [], "extra_slots": [],
                   "fetch_failed": 0, "model_dated": 0,
                   "fetch_failed_reasons": []}
        payload[bad_field] = "DNS 解析失败"
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        cp = Corpus(as_of=AS_OF)
        back = OW.load(path, cp)
        assert back.docs == [], bad_field
        assert cp.docs == [], bad_field


def test_missing_slots类字段是字典时被拒绝而非拆成key列表(tmp_path):
    """同一个洞的另一种形态：字典值会被 `list(...)` 悄悄取成它的 key
    列表——同样是容器类型不对，必须显式拒绝，不能被 `list()` 的宽容
    放过。`docs` 同样必须放一条真实文档，理由见上一条测试。"""
    import json

    import ir_agent.sources.openweb as OW
    good_doc = _good_doc()
    for bad_field in ("missing_slots", "extra_slots", "fetch_failed_reasons"):
        path = tmp_path / OW.OPENWEB_JSON
        payload = {"docs": [good_doc], "missing_slots": [], "extra_slots": [],
                   "fetch_failed": 0, "model_dated": 0,
                   "fetch_failed_reasons": []}
        payload[bad_field] = {"行业规模与增速": 1}
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


# ── final-fix-5 Fix 1：`load()` 对 `fetch_failed_reasons` 只做
# `list(raw.get(...))`，没有像 `_str_field` 那样逐元素校验类型。
# `full._openweb_notes` 随后对它做 `"；".join(...)`，混进一个非 str
# 元素在 phase-2 直接 TypeError——这条崩溃面撞坏了 load() 自己的
# docstring 承诺（不是「一个坏文件能让整次研究流程崩掉」），且是新引入
# 的：`missing_slots` 走的是逐个 f-string 拼接，同样的坏元素不会让它崩 ──


def test_fetch_failed_reasons混入非字符串元素时过滤该元素其余仍入库(tmp_path):
    """真实事故复现：`fetch_failed_reasons: ["ok reason", 7]` 这种手改/
    半写坏的落盘文件此前会被原样 `list(...)` 收下，交给
    `"；".join(res.fetch_failed_reasons)`（`full._openweb_notes`）时直接
    `TypeError: sequence item 1: expected str instance, int found`——把
    phase-2 整个研究流程带崩。这里验证混入的坏元素被丢弃、其余合法
    字符串与好文档都照常读回，不抛异常、不牵连整份文件。"""
    import json

    import ir_agent.sources.openweb as OW
    good_doc = _good_doc()
    for bad_element in (7, {"x": 1}):
        path = tmp_path / OW.OPENWEB_JSON
        payload = {"docs": [good_doc], "missing_slots": [],
                   "extra_slots": [], "fetch_failed": 2, "model_dated": 0,
                   "fetch_failed_reasons": ["ok reason", bad_element]}
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        cp = Corpus(as_of=AS_OF)
        back = OW.load(path, cp)
        assert [d.doc_id for d in back.docs] == ["ow_good"], bad_element
        assert back.fetch_failed_reasons == ["ok reason"], bad_element


# ── 主机安全校验在代理环境下的降级 ────────────────────────────────
#
# 实跑事故（600183，2026-09-30）：本机代理用 fake-IP DNS，每个域名都
# 解析到 198.18.0.x（RFC 2544 段，Python 判 is_private=True），于是
# _reject_unsafe_host 拒掉 11/11 个候选，开放网络采集产出恒为零、且
# 只印出一行「11 篇抓取失败」。以下这组测试钉住修复后的两个方向：
# 解析不可信时不得误拒，解析可信时必须照样拒。

def _fake_getaddrinfo(ip):
    return lambda host, port, *a, **k: [(2, 1, 6, "", (ip, 0))]


def test_解析到fakeIP段时不误拒且报告降级(monkeypatch):
    """代理的 fake-IP 是占位地址，与请求去向无关——拿它当判据既误拒
    又无效（真实解析在代理侧，本代码看不到）。"""
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW.socket, "getaddrinfo",
                        _fake_getaddrinfo("198.18.0.103"))
    assert OW._reject_unsafe_host("https://finance.sina.com.cn/x") is False


def test_解析到CGNAT段时同样按占位地址处理(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW.socket, "getaddrinfo",
                        _fake_getaddrinfo("100.64.1.2"))
    assert OW._reject_unsafe_host("https://example.com/x") is False


def test_配置了代理时跳过IP层校验(monkeypatch):
    """有代理时 requests 把主机名交给代理，本地这次解析与去向无关。"""
    import ir_agent.sources.openweb as OW

    def _boom(*a, **k):
        raise AssertionError("有代理时不该做本地解析")

    monkeypatch.setattr(OW.socket, "getaddrinfo", _boom)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert OW._reject_unsafe_host("https://example.com/x") is False


def test_无代理时内网地址仍然照样拒(monkeypatch):
    """降级只在解析不可信时生效。直连环境下这道闸门一个都不能放过——
    169.254.169.254 在云主机上是实例凭证的元数据服务。"""
    import ir_agent.sources.openweb as OW
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.setattr(OW, "_proxy_in_play", lambda url: False)
    for ip in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.1.1"):
        monkeypatch.setattr(OW.socket, "getaddrinfo", _fake_getaddrinfo(ip))
        with pytest.raises(OW.FetchFailed, match="禁止访问的地址"):
            OW._reject_unsafe_host("https://evil.example.com/x")


def test_不依赖DNS的那一层无条件执行(monkeypatch):
    """代理下 IP 层放弃之后，这一层是唯一还站得住的闸门，因此它不能
    受代理与否影响——把解析函数换成会炸的，它也必须拒掉。"""
    import ir_agent.sources.openweb as OW

    def _boom(*a, **k):
        raise AssertionError("这一层不该触发 DNS")

    monkeypatch.setattr(OW.socket, "getaddrinfo", _boom)
    for url in ("http://localhost/x", "http://foo.local/x",
                "http://bar.internal/x", "http://127.0.0.1/x",
                "http://10.0.0.5/x", "http://[::1]/x"):
        with pytest.raises(OW.FetchFailed):
            OW._reject_unsafe_host(url)


def test_公网字面量IP放行且算作已校验(monkeypatch):
    """URL 里直接写的 IP 就是请求实际要去的地址，不经 DNS——有没有
    代理都同样可信，因此这一支返回 True（未降级）。"""
    import ir_agent.sources.openweb as OW

    def _boom(*a, **k):
        raise AssertionError("字面量 IP 不该触发 DNS")

    monkeypatch.setattr(OW.socket, "getaddrinfo", _boom)
    assert OW._reject_unsafe_host("http://8.8.8.8/x") is True


def test_降级篇数进产物清单(monkeypatch):
    """环境决定的降级不是缺陷，但读者无从从「N 篇入库」里看出
    「内网拦截这一轮靠代理」——静默降级比报错更难发现。"""
    from ir_agent import full
    from ir_agent.sources.openweb import OpenWebResult

    notes = full._openweb_notes(OpenWebResult(host_check_degraded=3))
    assert notes, "有降级就必须出条目"
    assert "3 篇" in notes[0] and "主机安全校验" in notes[0]
    assert full._openweb_notes(OpenWebResult()) == []


def test_降级篇数跨阶段存活(tmp_path, monkeypatch):
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Corpus
    res = OW.OpenWebResult(host_check_degraded=2, fetch_failed=1)
    path = tmp_path / OW.OPENWEB_JSON
    OW.save(res, path)
    back = OW.load(path, Corpus(as_of=AS_OF))
    assert back.host_check_degraded == 2


# ── B2：Finding 引文的代码核验 ────────────────────────────────────
#
# 在此之前，一条 Finding 的证明力只有「模型说它在这个网址上见过」：
# claim 是模型转述、代码从没核实过，tier 来自模型自报的机构名。
# gaps 甚至把它包成 Document(text=claim) 放进语料库，与代码亲手抓回
# 的文档躺在一起，Corpus.verify 分不出来。

def _finding(claim, url="https://finance.example.com/a",
             publisher="某财经网", tier=None):
    from datetime import date as _d

    from ir_agent.sources.external import Tier as _T
    from ir_agent.sources.web import Finding
    return Finding(factor_key="capacity", claim=claim, url=url,
                   publisher=publisher, tier=tier or _T.MEDIA,
                   published=_d(2026, 6, 1))


def test_claim在原页则核验通过并带回全文(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url=url, text="公司名义产能约 300 万平方米，同比增长 12%。",
        meta_published=None))
    got = OW.verify_finding(_finding("名义产能约 300 万平方米"))
    assert got.ok is True
    assert "同比增长 12%" in got.text, "入库要用抓回的全文，不是 claim"


def test_核验忽略空白差异(monkeypatch):
    """页面里可能是全角空格或换行——与 Corpus.verify 同一套 norm。"""
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url=url, text="名义产能约　300 万\n平方米", meta_published=None))
    assert OW.verify_finding(_finding("名义产能约 300 万平方米")).ok is True


def test_claim不在原页则核验不通过(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url=url, text="本文讨论覆铜板行业的竞争格局。",
        meta_published=None))
    got = OW.verify_finding(_finding("名义产能约 300 万平方米"))
    assert got.ok is False
    assert "原页" in got.reason


def test_抓不到也算核验不通过(monkeypatch):
    import ir_agent.sources.openweb as OW

    def _boom(url, **k):
        raise OW.FetchFailed(f"{url}：内容类型不是网页（application/pdf）")

    monkeypatch.setattr(OW, "fetch", _boom)
    got = OW.verify_finding(_finding("名义产能约 300 万平方米"))
    assert got.ok is False
    assert "application/pdf" in got.reason


def test_通过时tier按最终域名重判(monkeypatch):
    """模型自报 publisher 只能走 tier_of（白名单全称）；抓回来之后
    域名是代码观测到的，tier_of_url 比它可信。"""
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Tier
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url="https://www.miit.gov.cn/real", text="名义产能 300 万",
        meta_published=None))
    got = OW.verify_finding(_finding("名义产能 300 万", publisher="某财经网"))
    assert got.ok is True
    assert got.tier is Tier.OFFICIAL, "模型报的是媒体，实际落在 .gov.cn"
    assert got.final_url == "https://www.miit.gov.cn/real"


def test_核验不通过时不改tier也不带正文(monkeypatch):
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Tier
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url="https://www.miit.gov.cn/real", text="无关正文",
        meta_published=None))
    got = OW.verify_finding(_finding("名义产能 300 万", tier=Tier.MEDIA))
    assert got.ok is False
    assert got.tier is Tier.MEDIA, "没核实的发现不该借重定向拿到升级"
    assert got.text == ""


def test_空claim直接算不通过(monkeypatch):
    """空串在任何正文里都"找得到"——那是 `in` 的性质，不是证据。"""
    import ir_agent.sources.openweb as OW

    def _never(url, **k):
        raise AssertionError("空 claim 不该发请求")

    monkeypatch.setattr(OW, "fetch", _never)
    assert OW.verify_finding(_finding("")).ok is False
    assert OW.verify_finding(_finding("   ")).ok is False
