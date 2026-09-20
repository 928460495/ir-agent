"""开放网络文档入语料库。**模型只给网址，正文由代码抓。**"""

from __future__ import annotations

from datetime import date

import pytest

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


def test_fetch_用最终URL并解码中文(monkeypatch):
    """两件事一起验：跟完重定向后的 final_url 必须是最终地址（分级要用
    它），以及中文页面的编码必须按页面声明解，不能留 ISO-8859-1。"""
    import ir_agent.sources.openweb as OW

    class _Resp:
        url = "https://blog.example.com/real"
        encoding = "ISO-8859-1"
        apparent_encoding = "utf-8"
        content = "<p>覆铜板报价上涨</p>".encode("utf-8")

        @property
        def text(self):
            return self.content.decode(self.encoding)

        def raise_for_status(self):
            return None

    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: _Resp())
    got = OW.fetch("https://www.miit.gov.cn/redirects-away")
    assert got.final_url == "https://blog.example.com/real"
    assert "覆铜板报价上涨" in got.text


def test_fetch_正文为空则抛(monkeypatch):
    import ir_agent.sources.openweb as OW

    class _Resp:
        url = "https://x.example.com/a"
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        content = b"<html><body><script>1</script></body></html>"

        @property
        def text(self):
            return self.content.decode("utf-8")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://x.example.com/a")
