"""开放网络文档采集 —— 让券商研报之外的第三方资料也能被正文引用。

**为什么这个模块能往 Corpus 里放东西，而 web.py 的 Finding 不能。**

    Document.text    代码抓回来的全文    → Corpus.verify() 可逐字核验
    Finding.claim    模型转述的一句话    → 只有 URL 是模型给的，正文从未抓回

区别不是分级，是**证明力的种类**：一个能回答「这句话在不在那个网址上」，
另一个不能。本模块把模型限制在「去哪个网址」这一件事上，正文由代码自己
抓——与 reports.py 的 search() / fetch_text() 完全同构。不是放宽了契约，
是用同一条契约接了一个新来源。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

import requests

from ir_agent.lineparse import keyed
from ir_agent.sources.external import Tier
from ir_agent.sources.retry import with_retry
from ir_agent.sources.web import parse_date

# ── 来源分级 ────────────────────────────────────────────────────
#
# **这里的判别器与 web.py 的 tier_of 是两个东西，不要合并。**
#
# tier_of 吃的是**模型自报的机构名字符串**。正因为输入不可信，它被真实
# 来源名打穿过三轮（证券时报网 / 新浪证券 / Securities Daily / 中国 IDC
# 圈），最后只能退守到「白名单全称 + 修饰词」，并在函数注释里写死了禁止
# 再加任何模糊规则。
#
# 本模块的输入不是同一种东西：URL 是代码自己请求的，跟完重定向后的最终
# 域名也是代码观测到的。**模型编不了域名——编了代码就抓不到那个页面。**
# 于是这个输入允许一件字符串输入绝不允许的事：后缀匹配。`.gov.cn` 是受
# 管制注册后缀，stats.gov.cn / miit.gov.cn / csrc.gov.cn 天然全部命中，
# 一条规则顶一张白名单。
#
# 代价不对称与 tier_of 一致，且是这套分级安全的唯一理由：把媒体错拔成
# OFFICIAL，等于一篇博客单独支撑了正文里的一条事实，是**危害**；把真
# 官方错压成 MEDIA，只是需要再凑一条来源，是**召回损失**。因此判不出来
# 落 MEDIA 是正确行为，不是缺陷。

# 非 .gov.cn 的官方/协会/一手市场研究机构注册域。
#
# 为什么只有这么几个：**每一条都必须是逐条核实过的注册域**。写错一个
# 域名的后果落在「危害」那一侧（一个不相干的站点被拔成 OFFICIAL），
# 漏写一个只落在「召回损失」那一侧。要加新来源，加注册域，不要碰
# _matches / tier_of_url 的判别逻辑本身。
#
# 核实记录（2026-09-20，见 task-1-report.md）：semi.org / gartner.com /
# idc.com / trendforce.com / counterpointresearch.com / yolegroup.com
# 均直接渲染在自己的注册域上，已逐条核实。omdia.com 被删掉了——它对外
# 302 永久跳到 omdia.tech.informa.com（Informa 集团的共享域名下），不是
# 落在自己的注册域上：按本模块「调用方必须传最终 URL」的前提，真实场景
# 里 tier_of_url 收到的 host 会是 omdia.tech.informa.com，永远匹配不上
# 这里写的 omdia.com；而如果把白名单条目改成 informa.com，Informa 名下
# 还有大量与 Omdia 无关的媒体/出版品牌，会把不相干的站点错拔成
# OFFICIAL——这正是本模块要不惜代价避免的那一种「危害」。核实不了就删，
# 不做替代猜测。
_OFFICIAL_HOSTS: tuple[str, ...] = (
    "semi.org",                 # SEMI 国际半导体产业协会
    "gartner.com",              # Gartner
    "idc.com",                  # IDC
    "trendforce.com",           # TrendForce（中文名集邦咨询）
    "counterpointresearch.com", # Counterpoint Research
    "yolegroup.com",            # Yole Group
)

# 券商自有站点的注册域。
#
# **初始为空，这是有意的，不是没写完。** 券商研报本来就通过东财接口入库
# （reports.to_document 把 tier 硬编码成 SELLSIDE），开放网络上撞到券商
# 自有研究站点的概率很低；而中文券商的研究站域名核实成本高、猜错的代价
# 落在「危害」那一侧。留空意味着这类页面落 MEDIA——按本模块的代价不对称，
# 这是正确的默认值。要支持，逐条核实后往这里加注册域。
_SELLSIDE_HOSTS: tuple[str, ...] = ()


def _host_of(url: str) -> str:
    """取主机名，小写、去掉末尾的点（DNS 绝对域名写法）。取不到返回空串。

    只认 http/https：本模块的分级前提是「代码抓完重定向后的最终 URL」，
    非 http(s) 协议不是代码会去抓正文的东西（`ftp://x.gov.cn` 不会被
    fetch_text 打开），冒充 gov.cn 主机名毫无实际证明力，必须落 MEDIA
    而不是顺着 hostname 解析结果直接给 OFFICIAL。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https"):
        return ""
    host = (parsed.hostname or "").strip().lower()
    return host.rstrip(".")


def _matches(host: str, registered: str) -> bool:
    """host 是否就是这个注册域，或它的子域。

    **只有这两种形态算命中，绝不做裸子串判断。** 裸子串正是 web.py 的
    tier_of 被打穿的第一种形态：`evil-semi.org` 含子串 `semi.org`，
    `semi.org.evil.com` 也含，两者都不是 SEMI。
    """
    return host == registered or host.endswith("." + registered)


def tier_of_url(url: str) -> Tier:
    """按**代码跟完重定向后的最终 URL** 分级。

    调用方必须传最终 URL，不是模型给的原始 URL：模型给一个 gov.cn 链接、
    实际 302 到博客，分级必须按博客走。
    """
    host = _host_of(url)
    if not host:
        return Tier.MEDIA
    if _matches(host, "gov.cn"):
        return Tier.OFFICIAL
    for entry in _SELLSIDE_HOSTS:
        if _matches(host, entry):
            return Tier.SELLSIDE
    for entry in _OFFICIAL_HOSTS:
        if _matches(host, entry):
            return Tier.OFFICIAL
    return Tier.MEDIA


# ── 主题槽位 ────────────────────────────────────────────────────
#
# **固定槽位存在的唯一理由是让代码说得出缺了什么。** 这是本项目贯穿始终
# 的原则（research.py 模块注释、full._load_external 里关于一致预期为空的
# 那段长注释）：只列已完成的部分，读者会以为资料是全的。没有预期就没有
# 缺口——主题若全由模型自由发挥，代码永远报不出「这次没取到政策原文」。
#
# 代价是覆盖面被槽位限死，Candidate.extra 正是为这个代价留的出口：模型
# 可按行业特性追加槽位（白酒的批价、半导体的稼动率这类清单预先枚举不完），
# 追加的收下并标为额外，**但不参与缺口判定**——缺口口径必须固定，不能
# 随模型每轮的发挥浮动。
SLOTS: tuple[str, ...] = (
    "行业规模与增速",
    "政策与监管",
    "竞争格局与份额",
    "上下游价格与供需",
    "公司重大动态",
)

_LINE = keyed("候选")
_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Candidate:
    """模型给出的一个候选网址。**注意它不含正文** —— 正文由 fetch 抓。"""

    slot: str
    title: str
    url: str
    publisher: str
    published: date
    extra: bool          # 槽位不在 SLOTS 里 → 额外发现，不参与缺口判定


def parse_candidates(text: str) -> list[Candidate]:
    """解析 `候选:` 行。三道守卫与 web.parse_findings 逐条对齐。"""
    out: list[Candidate] = []
    for hit in _LINE.finditer(text):
        parts = [p.strip() for p in hit.group(1).split("|")]
        # 卡在 5：一行要完整给出 槽位|标题|网址|机构|日期 五段。少任何
        # 一段都不是「4 段也合法」，而是整条不合规。
        if len(parts) < 5:
            continue
        url_m = _URL.search(parts[2])
        if not url_m:
            # 没有网址就无从复核，与凭空捏造无异。
            continue
        when = parse_date(parts[4])
        if when is None:
            # **这道守卫是 Document.published 能保持非空 date 的前提。**
            # 放行「日期不明」的候选，下游迟早会写出 `published or as_of`
            # 那种兜底——而 as_of 恰好是唯一必然不触发 OutOfWindow 的取值，
            # 等于让最不可信的证据获得时点上最宽松的待遇。见 web.py 的
            # Finding.published 字段注释，那是同一条教训。
            continue
        slot = parts[0]
        out.append(Candidate(slot=slot, title=parts[1], url=url_m.group(0),
                             publisher=parts[3], published=when,
                             extra=slot not in SLOTS))
    return out


PROMPT = """为一份 A 股研究报告采集公司与行业的外部资料。

公司：{company}（{code}）
行业：{industry}

上网搜索，为下面每个主题各找**最多 {per_slot} 个**具体网页。
只给网址，不要摘抄正文——正文由我们自己抓取。

必找的主题：
- 行业规模与增速
- 政策与监管
- 竞争格局与份额
- 上下游价格与供需
- 公司重大动态

若这个行业还有别的关键外部资料（例如渠道批价、稼动率、库存周期），
可另起主题名追加。

每条一行：

`候选: <主题> | <网页标题> | <网址> | <发布机构> | <发布日期 YYYY-MM-DD>`

**硬性要求**
- **必须给出网址**。没有网址的条目会被直接丢弃 —— 无从复核的转述与凭空
  捏造无异。
- **必须给出发布日期（YYYY-MM-DD）**。没写日期、或只写到「2026年7月」
  这种月份粒度以致解析不出来的条目，同样会被直接丢弃。晚于 {as_of} 的
  会被剔除：拿未来的资料分析过去，与账本的时点守卫是同一件事。
- 优先统计局、部委、行业协会、一手市场研究机构的原始页面，其次财经媒体。
- 找不到的主题就不要输出该主题的行，**不要用推测或不相关的网页填充**。

只输出上述格式的行。
"""


# ── 抓取与正文提取 ──────────────────────────────────────────────

TIMEOUT = 20
# 进提示词的截断长度。**入库存全文，进提示词才截断** —— Corpus.verify()
# 用的是全文，模型只能引用它看得见的那一段，而那一段必然在全文里，所以
# 截断不会让核验误杀。
TEXT_LIMIT = 2000

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}

# 整块剔除的噪声容器。研报走东财的 ctx-content 块，噪声天然被框住；
# 开放网页没有这个便利，导航、页脚、脚本、样式会被 _TAG 剥成一堆散字
# 混进正文，既污染引文核验也白白吃掉提示词长度。
_BLOCK = re.compile(
    r"<(script|style|nav|header|footer|aside|noscript)\b[^>]*>.*?</\1\s*>",
    re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t　\xa0]+")
_ENTITIES = (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
             ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"))

_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']')
# meta 里表示发布时间的键。属性顺序没有标准（content 可能写在 property
# 前面），所以逐个 meta 标签拆属性，而不是写一条把顺序焊死的正则。
_DATE_KEYS = ("article:published_time", "og:published_time",
              "article:published", "publishdate", "pubdate",
              "publish_date", "date")


class FetchFailed(Exception):
    """抓取或正文提取失败。**不静默返回空** —— 那会让「抓不到」和
    「抓到一篇空文章」长得一样。"""


@dataclass(frozen=True)
class Fetched:
    final_url: str              # 跟完重定向后的最终地址，分级用它
    text: str                   # 全文
    meta_published: date | None # 页面自己声明的发布时间，没有则 None


def extract_text(html: str) -> str:
    """HTML → 纯文本。与 reports.fetch_text 同构，额外整块剔除噪声容器。

    不引入 bs4/lxml：本仓库没有依赖声明文件，新增 import 意味着别人的
    环境可能跑不起来。
    """
    body = _BLOCK.sub(" ", html or "")
    body = _TAG.sub("\n", body)
    for src, dst in _ENTITIES:
        body = body.replace(src, dst)
    lines = [_SPACE.sub(" ", ln).strip() for ln in body.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def meta_date(html: str) -> date | None:
    """从 meta 标签里读页面自己声明的发布时间；读不到返回 None。"""
    for tag in _META_TAG.finditer(html or ""):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag.group(0))}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key not in _DATE_KEYS:
            continue
        when = parse_date(attrs.get("content", ""))
        if when is not None:
            return when
    return None


def fetch(url: str, *, timeout: int = TIMEOUT) -> Fetched:
    """代码自己把页面抓回来。**模型不经手正文一个字。**"""
    def _get():
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r

    try:
        resp = with_retry(_get)
    except Exception as e:                                  # noqa: BLE001
        raise FetchFailed(f"{url}：{e.__class__.__name__}: {e}") from e

    # requests 对没有显式 charset 的页面会猜成 ISO-8859-1，中文页面因此
    # 解出一片乱码——乱码不会报错，只会让正文提取和引文核验静默失效。
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    html = resp.text

    text = extract_text(html)
    if not text.strip():
        raise FetchFailed(f"{url}：正文提取为空")
    return Fetched(final_url=resp.url or url, text=text,
                   meta_published=meta_date(html))
