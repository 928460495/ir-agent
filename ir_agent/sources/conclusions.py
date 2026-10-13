"""因子联网结论 —— 模型综合判断 + 引用文章清单 + 代码机械比对 + 人工复核。

**为什么不再要求逐字原文**（用户决定，2026-10-13）。此前提示词让模型写「一句
话陈述」（允许改写），核实却要求这句话逐字出现在原页（`openweb.verify_finding`）
—— 生益科技第二次调用 15 条全部核实失败、被丢弃。单因子实验（样本见
`tests/fixtures/factor_web_experiment_600183.json`）逐条对照原页：4 条是改写或
跨段拼接，1 条抓回的是反爬挑战页。模型的一句话本来就可能是跨多篇文章取材后
的分析判断，要求逐字是错位的。

**代码不判对错，只做四件机械的事**，结果全部是标注、交给门② 的人：
1. 文章打不打得开（抓不到、或抓回的文本里一个中文字都没有）；
2. 时点（晚于 as_of 的文章剔除；结论引的文章全被剔除就丢弃）—— 唯一会丢东西的一件；
3. 结论里的每个数字在它引用的文章里有没有出处；
4. 按代码实际抓到的最终域名分级，不按模型自报的机构名。

**只核数字、不核措辞**：改写的危害集中在数字上 —— 实验第 1 条的数字大多在
原页，却混进了一个原页没有的「7628型规格」。若把规则放宽成「数字对得上就算
通过」它会被放过；这里把它标出来请人看，而不是替人判。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from ir_agent.lineparse import keyed
from ir_agent.sources import web
from ir_agent.sources.external import Tier
from ir_agent.sources.openweb import fetch, tier_of_url

_ART = keyed("文章")
_CON = keyed("结论")
_URL = re.compile(r"https?://\S+")
_CITE = re.compile(r"\d+")
_NUM = re.compile(r"\d+(?:\.\d+)?")
_CJK = re.compile(r"[一-鿿]")
_CN = "零一二三四五六七八九"
_CN_ALL = "零一二三四五六七八九十百千万亿两"

PROMPT = """为一份 A 股研究报告采集一个因子的数据。

行业：{industry}
要找的因子：**{factor}**

上网搜索，并**用 WebFetch 打开**相关文章，就这个因子给出结论（水平、同比
变化、行业均值皆可）。结论可以综合多篇文章、可以用你自己的话概括 —— 但每条
结论都要列出它采纳了哪几篇文章的观点，人会逐篇复核。

先列文章，每篇一行：

`文章: <编号> | <网址> | <发布机构> | <发布日期 YYYY-MM-DD>`

再列结论，每条一行：

`结论: {key} | <一句话结论> | 依据 <编号>,<编号>`

**硬性要求**
- 文章**必须给出网址与发布日期（YYYY-MM-DD）**，缺任何一样那篇文章作废；
  只引用了作废文章的结论也作废。晚于 {as_of} 的文章会被剔除：拿未来的资料
  分析过去，与账本的时点守卫是同一件事。
- 结论里的数字**尽量沿用原文的写法与单位**。代码会拿每个数字去你列的文章里
  找出处，找不到的会被标出来请人重点核对。
- 数据要具体（「稼动率约 78%」而非「稼动率偏低」）。
- 找不到就一行都不要输出，**不要用推测填充**。

只输出上述格式的行。
"""


@dataclass
class Article:
    no: str
    url: str
    publisher: str
    published: date
    final_url: str = ""
    tier: Tier = Tier.MEDIA
    opened: bool = False
    text: str = ""
    why_not: str = ""
    degraded: bool = False          # 抓取时主机安全校验降级（见 openweb.Fetched）

    @property
    def link(self) -> str:
        return self.final_url or self.url


@dataclass(frozen=True)
class NumberMark:
    number: str
    found_in: tuple[str, ...]       # 在哪几篇文章里找到；空 = 引用的文章里都没有


@dataclass
class Conclusion:
    factor_key: str
    claim: str
    articles: list[Article]
    dropped: list[str] = field(default_factory=list)
    marks: list[NumberMark] = field(default_factory=list)

    # ── 与 `web.Finding` 同名的三个只读属性 ──────────────────────
    # `assume._match_finding`（读 claim/url）与 `gaps.summary`（读
    # publisher/url）按这几个名字读 `FactorData.findings` 的元素。
    def _primary(self) -> Article:
        opened = [a for a in self.articles if a.opened]
        return (opened or self.articles)[0]

    @property
    def url(self) -> str:
        return self._primary().link

    @property
    def publisher(self) -> str:
        return self._primary().publisher

    @property
    def tier(self) -> Tier:
        opened = [a for a in self.articles if a.opened]
        return web.by_tier(opened)[0].tier if opened else Tier.MEDIA

    def missing_numbers(self) -> list[str]:
        return [m.number for m in self.marks if not m.found_in]

    def unopened(self) -> list[str]:
        return [a.no for a in self.articles if not a.opened]

    def flags(self) -> str:
        """门② 排序与门③ 标记共用的一句话。空串 = 没有要重点看的。"""
        out: list[str] = []
        if (miss := self.missing_numbers()):
            out.append(f"依据含 {len(miss)} 个在引用文章里找不到出处的数字"
                       f"（{'、'.join(miss)}）")
        if (shut := self.unopened()):
            out.append(f"依据引用的文章 {'、'.join(shut)} 代码无法打开")
        return "；".join(out)


@dataclass
class Searched:
    items: list[Conclusion]
    dropped: list[str]


def parse(text: str, as_of: date) -> Searched:
    """解析 `文章:` / `结论:` 行。时点守卫在这里做完。"""
    arts: dict[str, Article] = {}
    late: dict[str, str] = {}
    dropped: list[str] = []
    for h in _ART.finditer(text):
        p = [x.strip() for x in h.group(1).split("|")]
        if len(p) < 4:
            dropped.append(f"文章行字段不足，作废：{h.group(1)[:60]}")
            continue
        no, u, when = p[0], _URL.search(p[1]), web.parse_date(p[-1])
        if not u:
            dropped.append(f"文章 {no} 没有网址，作废")
            continue
        if when is None:
            dropped.append(f"文章 {no} 没有可解析的发布日期（YYYY-MM-DD），作废")
            continue
        if when > as_of:
            late[no] = f"文章 {no} 发布于 {when}，晚于 {as_of}，已剔除"
            continue
        arts[no] = Article(no=no, url=u.group(0),
                           publisher="|".join(p[2:-1]).strip(), published=when)
    items: list[Conclusion] = []
    for h in _CON.finditer(text):
        p = [x.strip() for x in h.group(1).split("|")]
        if len(p) < 3 or "依据" not in p[-1]:
            dropped.append(f"结论没写依据哪几篇文章，作废：{h.group(1)[:60]}")
            continue
        claim = "|".join(p[1:-1]).strip()
        cites = list(dict.fromkeys(_CITE.findall(p[-1])))
        use = [arts[n] for n in cites if n in arts]
        gone = [late.get(n, f"文章 {n} 未列出或已作废")
                for n in cites if n not in arts]
        if not use:
            dropped.append(f"结论「{claim[:30]}」引用的文章全部不可用"
                           f"（{'；'.join(gone) or '没有编号'}），作废")
            continue
        items.append(Conclusion(factor_key=p[0], claim=claim, articles=use,
                                dropped=gone))
    return Searched(items=items, dropped=dropped)


def _cn_forms(n: int) -> list[str]:
    """1–99 的中文写法（实验里「第五轮」↔「第5轮」）。"""
    if not 1 <= n <= 99:
        return []
    t, u = divmod(n, 10)
    if t == 0:
        return [_CN[u]]
    return [("十" if t == 1 else _CN[t] + "十") + (_CN[u] if u else "")]


def _page_form(text: str) -> str:
    """只在数字比对这一步做的归一：全角→半角、去千分位逗号。
    **不改共用的 `external.norm`**（spec ②「不做」）。"""
    t = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)


def _has_number(page: str, n: str) -> bool:
    if re.search(rf"(?<![\d.]){re.escape(n)}(?!\d|\.\d)", page):
        return True
    if "." not in n:
        for c in _cn_forms(int(n)):
            if re.search(rf"(?<![{_CN_ALL}]){c}(?![{_CN_ALL}])", page):
                return True
    return False


def number_marks(claim: str, articles: list[Article]) -> list[NumberMark]:
    """结论里每个数字，在它引用的（打开了的）文章里有没有出处。"""
    nums = list(dict.fromkeys(_NUM.findall(unicodedata.normalize("NFKC", claim))))
    pages = [(a.no, _page_form(a.text)) for a in articles if a.opened]
    return [NumberMark(n, tuple(no for no, pg in pages if _has_number(pg, n)))
            for n in nums]


def open_articles(items: list[Conclusion]) -> None:
    """逐篇抓回正文（同一篇只抓一次），再给每条结论标数字出处。

    抓回的文本里一个中文字都没有（反爬挑战页、外文页面）记为打不开 ——
    **措辞是「无从比对」，不是「结论未能核实」**：实验里捷配返回的是阿里云 WAF
    的 JS 挑战页，旧代码把它当正文、报成「转述未能核实」，那是说错话。
    **不绕过反爬**，人可以在浏览器里看。
    """
    done: set[int] = set()
    for c in items:
        for a in c.articles:
            if id(a) in done:
                continue
            done.add(id(a))
            try:
                got = fetch(a.url)
            except Exception as e:                          # noqa: BLE001
                a.why_not = f"抓取失败：{str(e)[:80]}"
                continue
            a.final_url = got.final_url
            a.degraded = got.host_check_degraded
            a.tier = tier_of_url(got.final_url)
            if not _CJK.search(got.text or ""):
                a.why_not = "抓回的文本里没有中文（反爬校验页或外文页面），无从比对"
                continue
            a.opened, a.text = True, got.text
        c.marks = number_marks(c.claim, c.articles)


def search(client, factor_label: str, factor_key: str, industry: str,
           as_of: date) -> Searched:
    """按因子联网，返回已打开文章、已标好数字出处的结论。

    **调用方必须传入已开启 WebSearch/WebFetch 的 client**（同 `web.search`）。
    """
    web.require_web()
    raw = client(PROMPT.format(industry=industry, factor=factor_label,
                               key=factor_key, as_of=as_of.isoformat()))
    got = parse(raw, as_of)
    open_articles(got.items)
    return got
