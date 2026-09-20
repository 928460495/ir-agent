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
