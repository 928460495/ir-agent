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

import codecs
import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests

from ir_agent.lineparse import keyed
from ir_agent.sources.external import Document, Tier
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
    if not all(host.split(".")):
        # 空标签不是任何域名的合法子域——DNS 不接受空标签，不管空标签
        # 出现在开头（host 形如 ".gov.cn"）还是中间（host 形如
        # "a..gov.cn"）。此前这里只特判了开头那一种
        # （`host.startswith(".")`），"a..gov.cn".endswith(".gov.cn") 同样
        # 为真却不会被那条特判拦下，会被误判成 gov.cn 的子域从而拔到
        # OFFICIAL。`all(host.split("."))` 一次性堵住所有空标签形态——
        # 只要 split 出的任何一段是空字符串，`all` 就为假。fetch() 今天
        # 走不到这里（这种主机名 DNS 解析不出来），但 tier_of_url 是
        # 公开函数，判错的方向落在代价不对称里"危害"那一侧，必须堵。
        return False
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

# fetch() 允许把正文放进语料库的内容类型。**只认网页**——PDF、图片、
# 任意二进制流经过 `resp.text`/`extract_text` 处理后大概率不是空字符串
# 就是一坨乱码，乱码不会报错，会当成「抓到了」直接入库，成为一条看似
# 正常、实则不可能被人类核对出处的引文。只比较分号前的主类型，因为真实
# 响应几乎总带 `; charset=...` 后缀。
_ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# 单次抓取允许读取的最大字节数。**卡在读的过程中，不是读完再判断**——
# 卡读完再判断仍然要求先把对方发来的任意大小的响应体整个吃进内存，等于
# 没卡。8MB 是这里的选择：真实新闻/研究页面的 HTML 正文（不含图片，
# `requests` 不会主动拉子资源）几乎不会超过几百 KB，8MB 给了近百倍余量
# 覆盖异常臃肿但仍是真实文章的页面，同时把「对方开着一个几十 GB 响应体
# 的连接不断」这种最坏情况的内存占用钉死在 8MB，而不是让它跟对方愿意
# 发多少字节线性增长。
FETCH_MAX_BYTES = 8 * 1024 * 1024

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}

# ── 正文提取：标准库 HTMLParser，不再手写正则剥标签 ──────────────
#
# **这一段替换的是什么，以及为什么。** 本文件曾经用一组正则
# （_BLOCK/_strip_blocks/_COMMENT/_TAG…）手动模拟"标签配对""区域"这些
# 概念，五轮评审踩出了同一种 bug 的五个变种：正则不知道"区域"是什么，
# 只认字面量模式，模式一旦出现在不该算数的地方（嵌套标签内部、JS 字符
# 串字面量里、HTML 注释里、别的属性值里）就会被将计就计。
# `html.parser.HTMLParser` 是标准库（同一层级的还有 `json`/`re`/
# `ipaddress`），不是新增第三方依赖；它对"标签边界""CDATA 元素"
# （`<script>`/`<style>` 内容按规范整体当纯文本，标签内出现的 `<`/`>`
# 不会被当成标签）"注释体不参与标签解析"这些区域概念是原生的，不需要
# 我们自己用正则去猜、去为每一种"区域"单独写一条排除规则。
#
# **两类噪声容器，处置必须不同——这是本次改写唯一需要人工判断、必须
# 写下来的地方。**
#
#   不可见容器：<script> <style> <noscript>，以及 HTML 注释。
#     它们的内容人打开网页永远看不到——浏览器根本不渲染。把这类内容
#     admit 进 Corpus.verify() 逐字核验的语料库是**伪造风险**：一段
#     从未出现在页面上的文字，被当成"页面上的原文"核验通过。不管容器
#     是否正常闭合，一律跳过到闭合标签、或者跳到文档结尾为止，没有
#     例外，不需要权衡：跳到文档结尾本来就是正确行为。
#
#   可见容器：<nav> <header> <footer> <aside>。
#     它们的内容确实渲染在人打开的页面上，只是不是文章正文——admit
#     进语料库是"噪声/召回"问题（污染核验、浪费提示词长度），不是
#     伪造问题：读者顺着 URL 打开页面，这段文字真的在那儿能看到。
#
#     **关键决策：这类容器如果一直到文档结尾都没有闭合标签，怎么办。**
#     验证跑里真实出现过这种页面：手写的导航栏漏写了 `</nav>`。旧正则
#     实现的取舍是"匹配不到收尾就整个不剥"，代价是**这个容器之后的
#     所有真实正文都被一并当成"还在容器里"从而被吞掉**——本模块存在
#     的理由就是保证语料库里的字可核验，把"一个标签没写完"变成"这篇
#     文章后一半的真实内容全部消失"，代价远比放行几个导航词条严重
#     得多。因此这里的选择是：可见噪声容器的内容先缓冲、不立即提交为
#     正文；标签正常配对闭合时，缓冲区被确认真的是噪声，丢弃；**如果
#     直到文档结尾都没有等到闭合标签，缓冲区并入正文，而不是丢弃**——
#     宁可让"首页 产品 关于我们"这几个导航词漏进正文（噪声问题，读者
#     多看几个词，可以容忍），也不能让紧跟在它后面的真实文章内容凭空
#     消失（伪造/丢失风险，本模块要不惜代价避免的那一种危害）。不可见
#     容器没有这个让步：它们从不缓冲，跳过就是永久丢弃，直到文档结尾
#     也是如此——不可见内容本来就不该出现在语料库里，没有"来不及确认
#     就先放行"这一说。

_INVISIBLE_NOISE_TAGS = frozenset({"script", "style", "noscript"})
_VISIBLE_NOISE_TAGS = frozenset({"nav", "header", "footer", "aside"})


class _TextExtractor(HTMLParser):
    """HTML → 纯文本片段流。两类噪声容器的处置见上方模块级注释。

    用两个深度计数器代替旧实现"反复 sub 直到不再变化"的多遍扫描——
    html.parser 自己维护标签流的开合顺序，遇到几层嵌套就是几次
    +1/-1，不存在"非贪婪正则认错闭合标签"那种配对歧义（旧实现里
    `<nav>A<nav>B</nav>C</nav>` 会被错配的那个洞），也不需要
    `_BLOCK_MAX_PASSES` 那种防御性的遍数上限：不管文档嵌套多少层，
    这里都是一次线性扫描，没有"扫描到上限还没剥干净"这种退化状态。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []       # 已确认要进正文的片段
        self._pending: list[str] = []   # 可见噪声容器内、尚未确认归宿的片段
        self._invisible_depth = 0
        self._visible_depth = 0

    def _emit(self, chunk: str) -> None:
        if self._invisible_depth > 0:
            return  # 不可见噪声：永久丢弃，不缓冲、不等待闭合标签
        if self._visible_depth > 0:
            self._pending.append(chunk)
        else:
            self._out.append(chunk)

    def handle_starttag(self, tag, attrs) -> None:
        tag = tag.lower()
        if tag in _INVISIBLE_NOISE_TAGS:
            self._invisible_depth += 1
        elif tag in _VISIBLE_NOISE_TAGS:
            self._visible_depth += 1
        self._emit("\n")

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        self._emit("\n")
        if tag in _INVISIBLE_NOISE_TAGS:
            if self._invisible_depth > 0:
                self._invisible_depth -= 1
        elif tag in _VISIBLE_NOISE_TAGS:
            if self._visible_depth > 0:
                self._visible_depth -= 1
                if self._visible_depth == 0:
                    # 正常配对闭合：缓冲区确认是噪声，丢弃。
                    self._pending.clear()

    def handle_data(self, data) -> None:
        self._emit(data)

    def close(self) -> None:
        # **未闭合的注释 / CDATA / 声明——`_invisible_depth` 看不见的洞。**
        # `<!--` 从没触发 `handle_starttag`/`handle_endtag`，注释体的
        # "不可见"完全靠 CPython `html.parser` 自己在遇到 `-->` 之前不
        # 调用任何回调来保证；深度计数器只在 `handle_starttag`/
        # `handle_endtag` 里加减，从未也不需要为注释加过一次。这在
        # 注释正常闭合时没问题——但一段没有 `-->` 的注释、或没有 `]]>`
        # 的 `<![CDATA[`，在 `close()`（也就是 `goahead(1)`）里找不到
        # 收尾定界符时，CPython 的实现会把从声明起点到文档结尾的剩余
        # `self.rawdata` 原样喂给 `handle_data`，当成普通正文——不可见
        # 容器"跳到文档结尾也不例外"这条模块级承诺，对这一种情况完全
        # 落空：从未进过任何回调的文本，混进了 `_out`。
        #
        # 这里直接读/清空 `self.rawdata` 拦住它：`rawdata` 是
        # `html.parser.HTMLParser` 的**实现细节**，不是公开文档承诺的
        # 属性——标准库文档只保证 `feed`/`close`/各 `handle_*` 回调的
        # 行为，从未承诺这个字段的名字、或它在 `close()` 触发的最后一次
        # `goahead` 之前仍然held着"尚未处理的剩余原始输入"这件事，在
        # 未来 CPython 版本里保持不变。**如果这个内部实现变了**（改名、
        # 提前清空、或 `close()` 内部处理顺序变了)，下面这行判断会静默
        # 退化成 no-op——不报错，只是不再生效，未闭合注释/CDATA 泄漏的
        # 这个洞会原样回归，而不会有任何调用点收到异常提醒。能抓住这种
        # 回归的探针是测试，不是这段注释：
        # `test_未闭合的HTML注释不泄漏到文档结尾`和
        # `test_未闭合的CDATA不泄漏到文档结尾`直接断言"泄漏文本不在
        # extract_text 的结果里"，不依赖了解 rawdata 这个字段本身，
        # 一旦这里退化成 no-op，这两条测试会先变红。
        if self.rawdata.startswith("<!"):
            self.rawdata = ""
        super().close()

    def result(self) -> str:
        # 可见噪声容器直到文档结尾都没能配对闭合——见模块级注释：缓冲区
        # 并入正文而不是丢弃，宁可漏进几个导航词，也不能把它后面真实的
        # 文章内容一起吞掉。
        return "".join(self._out + self._pending)


_SPACE = re.compile(r"[ \t　\xa0]+")

# meta 里表示发布时间的键。属性顺序没有标准（content 可能写在 property
# 前面），所以逐个 meta 标签拆属性，而不是写一条把顺序焊死的正则。
_DATE_KEYS = ("article:published_time", "og:published_time",
              "article:published", "publishdate", "pubdate",
              "publish_date", "date")


class FetchFailed(Exception):
    """抓取或正文提取失败。**不静默返回空** —— 那会让「抓不到」和
    「抓到一篇空文章」长得一样。"""


def _is_globally_routable(ip_str: str) -> bool:
    """这个地址是不是一个货真价实的公网地址。

    解析不出来（理论上不该发生，getaddrinfo 给出的地址字面量一定合法）
    也当作不可信处理，不放行——「判不出来就拒绝」与本文件 tier_of_url
    的代价不对称是同一个方向：错误的方向必须落在「多拒一个」而不是
    「多抓一个不该抓的」。
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_private or ip.is_link_local
               or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


# 代理客户端的 fake-IP 段。Clash/Surge 这一类工具不把域名交给真实 DNS，
# 而是给每个域名现分配一个保留段里的**占位**地址、自己在那个地址上收
# 流量，真实解析发生在代理侧。所以本地解析到这种地址时，它与请求实际
# 去向毫无关系——不是"解析到了一个内网地址"，是"根本没解析到东西"。
#
# 198.18.0.0/15 是 RFC 2544 基准测试段，100.64.0.0/10 是 CGNAT 段，
# 这两个是这类工具最常用的 fake-IP 池。前者在 Python 的 ipaddress 里
# `is_private` 为 True，于是会被 `_is_globally_routable` 判成禁止访问
# ——这正是下面那段事故注释说的那个洞。
_FAKE_IP_NETS = (ipaddress.ip_network("198.18.0.0/15"),
                 ipaddress.ip_network("100.64.0.0/10"))

# 主机名层面一眼就能判、且**不需要 DNS** 的禁止目标。有代理时 IP 层的
# 校验必须放弃（理由见 `_reject_unsafe_host`），这一层是那时候唯一还
# 站得住的闸门，所以它无条件执行。
_UNSAFE_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_UNSAFE_HOST_NAMES = ("localhost", "localhost.localdomain")


def _is_fake_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in _FAKE_IP_NETS)


def _proxy_in_play(url: str) -> bool:
    """这个 URL 会不会经由代理发出。

    用 `urllib.request.getproxies()` 而不是自己读 `HTTP_PROXY`：
    `requests` 自己走的就是这条路（`requests.utils.get_environ_proxies`
    → `getproxies()`），而且在 macOS 上它还会读系统网络设置里配置的
    代理——命令行环境变量为空、系统层面开着代理，是这台机器上实际
    遇到的情形。判据必须和 requests 实际的行为一致，否则会出现
    「我以为没代理、requests 以为有」的错位。
    """
    from urllib.request import getproxies, proxy_bypass

    parsed = urlparse(url)
    scheme, host = parsed.scheme, (parsed.hostname or "")
    proxies = getproxies()
    if scheme not in proxies and "all" not in proxies:
        return False
    try:
        return not (host and proxy_bypass(host))
    except (TypeError, ValueError, OSError):
        return True


def _reject_unsafe_host(url: str) -> bool:
    """`url` 的主机必须指向公网，否则抛 `FetchFailed`。

    返回**IP 层校验有没有真的做过**：True = 做过，False = 只做了主机名
    层的校验、IP 层被跳过（调用方必须把这件事报进产物清单，见
    `OpenWebResult.host_check_degraded`）。

    ── 为什么 IP 层校验必须是有条件的 ──────────────────────────────
    实跑打出来的（600183，2026-09-30，`out/600183-ow3/openweb.json`）:
    11 个候选，11 个被这道闸门拒掉，开放网络采集产出恒为零，而且**不
    报错**——清单上只有一行「11 篇抓取失败」。原因逐条记着:

        …finance.sina.com.cn/…：解析到禁止访问的地址（198.18.0.103）
        …wap.miit.gov.cn/…：解析到禁止访问的地址（198.18.0.104）

    本机代理用 fake-IP DNS，每个域名都解析到 198.18.0.x，而那个段在
    Python 里 `is_private` 为 True。

    关键不在于"判严了"，在于**这道预检同时误拒且无效**：有代理时
    `requests` 把主机名交给代理、本地压根不解析，真实解析发生在代理侧，
    我们看不到。所以它拒掉的是合法主机，而它想防的内网地址仍然拦不住。
    一道既伤好人又抓不住坏人的闸门，不该无条件执行。

    判不出来的方向仍然遵守本文件的代价不对称，只是**换了对象**：DNS
    结果不可信时，不可信的是"我以为的目标地址"，而不是"这个主机"。
    此时正确的处置是退回到不依赖 DNS 的那一层（主机名字面量、URL 里
    直接写的 IP），并把降级说出来——不是拿一个已知无意义的判据去拒绝
    全部候选。

    **为什么要单独查这一步，而不是信任 requests 会自然连不上就报错。**
    环回（127.0.0.1）、内网（10/8、172.16/12、192.168/16）、链路本地
    （169.254.0.0/16——云主机上常是元数据服务：AWS/GCP/阿里云的实例
    凭证、角色临时密钥都挂在 169.254.169.254 上）、以及保留地址，进程
    自己所在的网络环境里往往*连得通*，不会报错，只会把内网服务的响应
    当成一篇网页正文抓回来、按 MEDIA 落进语料库。模型给的 URL 是它编出
    来的字符串，编一个内网地址不比编一个外网地址难——这道闸门必须靠
    代码主动查，不能指望"连不上就自然失败"兜底。

    **必须调用两次，不能只查一次。** 见调用点：一次查模型给的原始 URL，
    一次查跟完重定向后的最终 URL。模型给一个公网域名、该域名的服务端
    再 302 到内网地址，是同一类攻击的另一种写法——只查最初那次拦不住。
    """
    host = _host_of(url)
    if not host:
        raise FetchFailed(f"{url}：无法识别主机名")

    # ── 第一层：主机名字面量，不需要 DNS，无条件执行 ──────────────
    if host in _UNSAFE_HOST_NAMES or host.endswith(_UNSAFE_HOST_SUFFIXES):
        raise FetchFailed(f"{url}：禁止访问的主机名（{host}）")
    # URL 里直接写的 IP 就是请求实际要去的地址，不经 DNS，因此这一支
    # 在有没有代理时都同样可信——代理照样会按这个地址去连。
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_globally_routable(host):
            raise FetchFailed(f"{url}：禁止访问的地址（{host}）")
        return True

    # ── 第二层：DNS 解析结果，仅在解析可信时才作为判据 ────────────
    if _proxy_in_play(url):
        # 有代理：本地这次解析与请求去向无关（requests 把主机名交给
        # 代理），拿它做判据既误拒又无效。退回只有第一层。
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise FetchFailed(f"{url}：DNS 解析失败（{e}）") from e
    if not infos:
        raise FetchFailed(f"{url}：DNS 解析结果为空")
    addrs = [info[4][0] for info in infos]
    if any(_is_fake_ip(a) for a in addrs):
        # 没有配置代理，但解析结果落在 fake-IP 段——透明代理/本机
        # 网络工具在劫持 DNS。同上：这个地址是占位符，不是目标。
        return False
    for ip_str in addrs:
        if not _is_globally_routable(ip_str):
            raise FetchFailed(f"{url}：解析到禁止访问的地址（{ip_str}）")
    return True


# <meta charset> 只看原始字节的前这么多字节。HTML5 规范要求这个声明
# 出现在文档头部（浏览器自己也是这么找的），真实页面确实如此；不通读
# 全文，避免正文里恰好出现一段提到 "charset=" 的技术讨论被误判成页面
# 自己的声明。
_META_CHARSET_HEAD = 2048


class _MetaScan(HTMLParser):
    """只收集 `<meta ...>` 标签的属性（键统一小写）。

    **不用正则裸找 "charset="，用 html.parser 原生的标签流，三类历史
    假阳性因此天然不成立，不需要额外代码去堵：**

    - 注释体内的标记：html.parser 把 `<!-- ... -->` 整体交给
      `handle_comment`（这里不覆盖，默认什么也不做），注释体内的文本
      从不触发 `handle_starttag`——与浏览器"根本不解析注释里的标记"
      一致，`<!-- <meta charset=...> -->` 里那个 meta 不会被这里收集
      到。
    - `<script>`/`<style>` 标签内容：html.parser 按 HTML5 规范把这两个
      标签的内容整体当 CDATA 纯文本处理，标签内出现的任何
      `<meta ...>` 字面量文本都只是普通字符，不会被解析成标签。
    - 裸值属性：`attrs` 由 html.parser 的属性文法给出，HTML5 允许的
      `charset=Big5`（不带引号）与 `charset="Big5"` 走的是同一条解析
      路径，不需要为裸值另写一条更宽松的正则。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag.lower() == "meta":
            self.metas.append({k.lower(): (v or "") for k, v in attrs})


def _scan_metas(html: str) -> list[dict[str, str]]:
    """喂一段 HTML（或原始字节头部解出来的字符串），按文档顺序返回每个
    `<meta>` 标签的属性字典。

    解析失败不向上抛——资料收集的失败不得阻断整轮研究，`_meta_charset`
    /`meta_date` 本就是"找不到就返回 None"的语义，解析器出错等价于
    找不到，把已经收集到的部分结果原样返回即可，不需要整个函数跟着炸。
    """
    parser = _MetaScan()
    try:
        parser.feed(html)
        parser.close()
    except Exception:                                       # noqa: BLE001
        pass
    return parser.metas


# 只用来从**已经确认是老式 http-equiv 写法**的 `content` 属性值里挖出
# `charset=` 后面那段——不是在整个标签、更不是在任意字节流里裸找这段
# 子串（那正是 `_meta_charset` 曾经被打穿的洞：`content="see
# charset=iso-8859-1 for details"` 这种压根不是编码声明的属性值，裸找
# "charset=" 会把它当真）。这里的输入已经先经过 `_MetaScan` 按标签/
# 属性边界切好，只在**确认是 Content-Type 声明的那一个 content 属性
# 值**里找，范围已经收得足够窄，不是在任意文本里裸找。
_CHARSET_ATTR = re.compile(r'charset\s*=\s*["\']?\s*([a-zA-Z0-9_-]+)', re.I)


def _meta_charset(raw: bytes) -> str | None:
    """从原始字节的头部读页面自己声明的字符集；读不到返回 None。

    **头部字节先按 latin-1 解码成字符串，再喂给 html.parser。** latin-1
    是全字节映射（0x00–0xFF 每个字节都有对应字符），解码永不失败；且
    ASCII 范围（0x00–0x7F）在 latin-1 下与 ASCII 逐字节一致——不管页面
    正文实际是 UTF-8、GB18030 还是 Big5，`<meta charset=...>` 这几个
    字母、等号、引号本身都会被还原成正确的字符，`_MetaScan` 能正常
    识别标签结构。头部之外、真正的中文正文字节会被解成 latin-1 意义上
    的乱码字符，但那些字符从不出现在我们要取的
    `charset`/`http-equiv`/`content` 属性值里（这几个值本身只由 ASCII
    字符拼成），不影响结果。

    只认下面两种**真正的声明**形态之一，命中即返回，不会再对任意标签、
    任意属性做裸子串匹配：

    - HTML5 短格式：标签自己带一个字面量 `charset` 属性，
      `<meta charset="UTF-8">`（或不带引号的 `<meta charset=UTF-8>`，
      HTML5 允许、html.parser 的属性文法原生支持，不需要额外代码）；
    - 老式写法：`http-equiv` 属性的值（大小写不敏感）就是
      `Content-Type`，且 `content` 属性值里带 `charset=...`，
      `<meta http-equiv="Content-Type" content="text/html; charset=GB2312">`。
    """
    head = raw[:_META_CHARSET_HEAD].decode("latin-1")
    for attrs in _scan_metas(head):
        if "charset" in attrs:
            value = attrs["charset"].strip()
            if value:
                return value
            continue
        if attrs.get("http-equiv", "").strip().lower() == "content-type":
            m = _CHARSET_ATTR.search(attrs.get("content", ""))
            if m is not None:
                return m.group(1)
    return None


_ISO_8859_1 = codecs.lookup("iso-8859-1").name


def _is_iso_8859_1(enc: str) -> bool:
    """`enc` 是不是 ISO-8859-1 的某个别名（`latin-1`/`latin1`/`cp819`/
    `iso8859-1`/`L1`……）。

    按 `codecs.lookup(enc).name` 归一化后比较，不按字符串字面量比较——
    字面量比较（`enc.lower() != "iso-8859-1"`）只堵得住 "iso-8859-1"
    这一种拼法，上面列的几个别名全部会原样放行、复现 `_decode` 文档
    字符串里描述的乱码。`codecs.lookup` 认不出的名字（页面声明了一个
    压根不存在的编码）不是 ISO-8859-1，返回 False——这不是漏判：
    `raw.decode(enc)` 随后会自己抛 `LookupError`，被 `_decode` 的
    try/except 接住退到猜测链，行为不受影响。
    """
    try:
        return codecs.lookup(enc).name == _ISO_8859_1
    except LookupError:
        return False


def _decode(raw: bytes, header_encoding: str | None) -> str:
    """把抓回来的字节猜成字符串。

    **为什么不用 `resp.apparent_encoding`（旧实现的做法）。** 那是靠
    `chardet`/`charset_normalizer` 读一遍 `resp.content` 猜出来的，而
    `resp.content` 要求响应体没被消费过。本模块现在为了卡字节数上限
    （见 `FETCH_MAX_BYTES`），改成用 `resp.iter_content()` 边读边数，
    读完之后 requests 会把响应标记成"已消费"，这时候再碰 `resp.content`
    / `resp.apparent_encoding` 会直接抛 `RuntimeError`——两者天生冲突，
    只能二选一，这里选了字节上限（保护级别更高的那一项）。

    退化方案：先信 HTTP 头声明的编码；头没给、或给的是 requests 对没
    声明 charset 的页面的默认猜测 ISO-8859-1（中文页面几乎必错）时，
    退而求其次信页面自己在 `<meta charset>` 里的声明（`_meta_charset`）
    ——这是页面自己说的，比代码瞎猜强；两者都拿不到或都解不出来，才
    依次试 UTF-8、GB18030（覆盖 GBK/GB2312 的国标超集，中文网页最常见
    的两大编码族已经覆盖），全部失败再退到 `errors="replace"`——保证
    一定能拿到字符串，不能因为编码猜错就把整篇正文的抓取当成失败处理。

    **为什么 GB18030 必须排在猜测链的最后、且要有页面自己的声明垫在它
    前面。** GB18030 是国标超集，字节覆盖面极广，对着一份实际是 Big5
    或 EUC-JP 的页面硬解，十有八九不会抛异常——它会**成功**解码出一串
    看起来正常、实际是乱码的字符串，`extract_text` 因为它不是空字符串
    照样放行，成为一条静默污染 Corpus.verify() 逐字核验的正文，而不是
    一次可见的失败。这类页面在本项目的真实使用场景里不是假设：CCL/PCB
    覆盖点名的台湾一手来源现在仍有用 Big5 编码的。页面自己在
    `<meta charset>` 里的声明是它自报的编码，说错的概率远低于代码去
    猜——先信这个声明，才轮到会把任何字节都吃成"看起来正常"的 GB18030
    兜底。

    **`<meta charset>` 分支必须跟 HTTP 头分支一样排除 ISO-8859-1。**
    ISO-8859-1 是一个全字节映射（0x00–0xFF 每个字节都有对应字符），
    `bytes.decode("iso-8859-1")` 因此**永不抛异常**，不管原始字节实际
    是什么编码。HTTP 头分支三行前已经在排除它——那是 requests 对没有
    显式声明 charset 的响应给出的默认猜测，几乎总是错的；`<meta>` 里的
    这个值理论上是页面自己声明的，可信度更高，但页面自己写错、或者
    `_meta_charset` 误判到一个并非真实生效的声明（比如 `_meta_charset`
    的判别不够严格的历史版本），后果与 HTTP 头分支一样：一个必然不报
    错的解码器一旦被选中，不管对不对都会"成功"，把乱码悄悄放进
    Corpus.verify() 要逐字核验的正文——两个分支的选择器共享同一个
    "总能解码成功、从不用来当默认猜测"的编码，必须共享同一条排除规则，
    不能只在其中一个分支写。

    **排除时必须按 codec 的规范名比较，不能按字符串字面量比较。**
    `header_encoding.lower() != "iso-8859-1"` 这种写法只堵得住
    "iso-8859-1" 这一种拼法——`latin-1`/`latin1`/`cp819`/`iso8859-1`/
    `L1` 全部是 ISO-8859-1 的合法别名，`codecs.lookup` 认得出它们，
    `.name` 会全部归一成同一个规范名，但字符串字面量比较认不出这些
    别名是同一个东西，会原样放行、照样产生上一段描述的乱码。两个分支
    （HTTP 头声明、`<meta>` 声明）都要用 `_is_iso_8859_1`（内部用
    `codecs.lookup(enc).name` 比较）判断，不能只改其中一个——两个分支
    面对的是同一类风险。
    """
    if header_encoding and not _is_iso_8859_1(header_encoding):
        try:
            return raw.decode(header_encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    meta_encoding = _meta_charset(raw)
    if meta_encoding and not _is_iso_8859_1(meta_encoding):
        try:
            return raw.decode(meta_encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Fetched:
    final_url: str              # 跟完重定向后的最终地址，分级用它
    text: str                   # 全文
    meta_published: date | None # 页面自己声明的发布时间，没有则 None
    # 这次抓取的主机安全校验有没有降级（IP 层被跳过，只做了主机名层）。
    # 见 `_reject_unsafe_host`：有代理时本地解析拿到的是占位地址，拿它
    # 当判据既误拒又无效。降级不阻断抓取，但必须一路传到产物清单——
    # 「内网地址拦截这一轮靠的是代理，不是本代码」是读者有权知道的事。
    host_check_degraded: bool = False


def extract_text(html: str) -> str:
    """HTML → 纯文本。噪声容器的取舍见 `_TextExtractor` 与模块级注释。

    不引入 bs4/lxml：`html.parser.HTMLParser` 是标准库，跟 `re`/`json`/
    `ipaddress` 同一层级，不违反"不引入任何新的第三方依赖"。

    **解析失败不能向上抛。** `html.parser` 本身对畸形输入很宽容（未
    闭合标签、裸 `<`/`>` 都不会报错，见交互验证），但仍用 try/except
    兜底：真出现解析器本身抛异常的畸形输入，这里退化成"就当没抓到
    内容"（返回解析器出错前已收集到的部分），不能让异常一路冒到
    `fetch()`/`gather()`，连累同一批的其余候选——资料收集的失败不得
    阻断整轮研究。
    """
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:                                       # noqa: BLE001
        pass
    body = parser.result()
    lines = [_SPACE.sub(" ", ln).strip() for ln in body.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def meta_date(html: str) -> date | None:
    """从 meta 标签里读页面自己声明的发布时间；读不到返回 None。

    **按 `_DATE_KEYS` 的优先级排序取值，不按 meta 标签在文档里出现的
    顺序。** 同一个页面可能同时带好几个日期相关的 meta 标签——真正的
    `article:published_time`，也可能有 CMS 模板遗留的、语义含糊的
    `date`（页面生成时间、模板占位符都可能写在这里）。谁在 HTML 里排
    在前面是模板/渲染顺序决定的偶然结果，不代表谁更可信；`_DATE_KEYS`
    这个元组的顺序本身就是可信度排名，只按文档位置取第一个命中，等于
    完全没用这个排名。

    错认的代价在危害那一侧，而且是双重的：错的日期可能骗过 as_of 的
    前瞻守卫、放行一篇本不该在这个时点出现的文档；放行之后这篇文档还
    会被打上 `date_source="meta"`——"代码已核验"，比模型自己给的日期
    看起来*更*可信，恰好颠倒了 `date_source` 字段存在的意义（把不可
    核验的部分显式标出来，见 openweb 设计文档 §五）。
    """
    hits: dict[str, date] = {}
    for attrs in _scan_metas(html or ""):
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key not in _DATE_KEYS or key in hits:
            continue
        when = parse_date(attrs.get("content", ""))
        if when is not None:
            hits[key] = when
    for key in _DATE_KEYS:
        if key in hits:
            return hits[key]
    return None


def fetch(url: str, *, timeout: int = TIMEOUT) -> Fetched:
    """代码自己把页面抓回来。**模型不经手正文一个字。**

    这也是本文件里第一处、也是唯一一处进程自己拿模型编出来的字符串去
    发网络请求的地方（其余数据源打的都是代码写死的已知接口）。三道闸门
    缺一不可：主机不能是内网/环回/链路本地/保留地址（见
    `_reject_unsafe_host`，请求前后各查一次）；内容类型必须像网页，不
    是 PDF/图片等二进制；读取的字节数有硬顶。三者都只是让抓取本身失败
    （抛 `FetchFailed`），`gather()` 已经把它计进 `fetch_failed`、不会
    连累其余候选——这条契约不变。

    **正文读取必须在 `_get` 内部完成，跟头部一起过 `with_retry`——不能
    读完头再单独读正文。** 这是本函数曾经出过的一个真实事故：`stream=
    True` 下 `requests.get()` 一收到响应头就返回，正文是在
    `resp.iter_content()` 这个生成器被消费时才**惰性**读的。旧代码只把
    `with_retry(_get)`（即拿到响应头那一步）包进了重试与
    `FetchFailed` 边界，`iter_content()` 的循环留在外面、只有一个裸
    `try/finally`。`ReadTimeout`/`ConnectionError`/`ChunkedEncodingError`
    这类瞬时故障（`retry._TRANSIENT` 明确点名 `ChunkedEncodingError`
    要重试）因此改在读正文时才炸，从 `with_retry` 的重试范围和
    `FetchFailed` 的转换边界外面**原样**冒出去——`gather()` 只接
    `FetchFailed`，接不住裸 `ChunkedEncodingError`，一路冒到
    `_load_openweb`，那里的 `except Exception` 会在 `openweb.save()`
    执行**之前**返回，本轮已经抓到、已经进了 `r.corpus` 的其余文档全部
    没能跨阶段存活，`fetch_failed` 也没能计数——一次抓十篇里的一篇卡在
    读正文，代价是另外九篇白抓。把整段读取（含 `iter_content` 循环、
    字节上限、内容类型、跟完重定向后的主机复查）都挪进 `_get`，
    `with_retry` 失败时会整个重来一次全新的 `requests.get()`——流式响应
    不能从中断处续读，重试本来就只能是"整次请求重新来过"，这与
    `_get` 原本对头部请求的重试语义完全一致，只是覆盖范围补全了正文。
    """
    # 两处校验的降级状态要合并：预检（这里）与跟完重定向后的复检
    # （`_get` 内部）。用可变容器而不是闭包变量赋值，因为 `_get` 会被
    # `with_retry` 整次重跑，每次重跑都要重新记一遍。
    degraded = [not _reject_unsafe_host(url)]

    def _get():
        r = requests.get(url, headers=_HEADERS, timeout=timeout, stream=True)
        try:
            r.raise_for_status()
            final_url = r.url or url
            # 模型给的域名可能是公网的，但该域名服务端把请求 302 到内网
            # 地址——只查请求前那一次拦不住这种情况，跟完重定向后必须
            # 再查一次。
            if not _reject_unsafe_host(final_url):
                degraded[0] = True

            content_type = (r.headers.get("Content-Type") or "").split(";")[0]
            content_type = content_type.strip().lower()
            if content_type not in _ALLOWED_CONTENT_TYPES:
                raise FetchFailed(
                    f"{url}：内容类型不是网页（{content_type or '未声明'}）")

            raw = bytearray()
            for chunk in r.iter_content(chunk_size=65536):
                raw.extend(chunk)
                if len(raw) > FETCH_MAX_BYTES:
                    raise FetchFailed(
                        f"{url}：正文超过 {FETCH_MAX_BYTES} 字节上限")
            return final_url, bytes(raw), r.encoding
        finally:
            r.close()

    try:
        final_url, raw, header_encoding = with_retry(_get)
    except FetchFailed:
        # 闸门类失败（内容类型、字节上限、重定向后主机不安全）已经是
        # 成品 FetchFailed，直接透传——不能被下面那个 `except Exception`
        # 再包一层，那会把 `内容类型不是网页（application/pdf）` 这种
        # 已经很明确的消息变成 `FetchFailed: 内容类型不是网页（...）`，
        # 平白多一层没有信息量的包装。这些也都不是 `is_transient` 认可
        # 的瞬时故障，`with_retry` 本就不会重试它们，直接失败。
        raise
    except Exception as e:                                  # noqa: BLE001
        raise FetchFailed(f"{url}：{e.__class__.__name__}: {e}") from e

    html = _decode(raw, header_encoding)

    text = extract_text(html)
    if not text.strip():
        raise FetchFailed(f"{url}：正文提取为空")
    return Fetched(final_url=final_url, text=text,
                   host_check_degraded=degraded[0],
                   meta_published=meta_date(html))


# ── 编排 ────────────────────────────────────────────────────────

PER_SLOT = 2
MAX_DOCS = 10
# `fetch_failed_reasons` 里单条原因的截断长度。原因字符串里带着完整
# URL（见 fetch()/`_reject_unsafe_host` 里的 `f"{url}：..."` 格式），
# 加上异常自带的描述可能很长；与 full.py 里 `str(e)[:120]` 同一量级，
# 这里再放宽一些余量，因为消息本身就带着 URL 前缀。
_REASON_LIMIT = 160


@dataclass
class OpenWebResult:
    docs: list[Document] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    extra_slots: list[str] = field(default_factory=list)
    fetch_failed: int = 0
    # 去重后的失败原因，给人看时用来分清「抓取失败」到底是哪一种。
    #
    # `fetch_failed` 曾经只是一个计数，四种截然不同的诊断——模型给的
    # 是个 PDF、给的是个内网地址、这个域名 DNS 解析不出来、正文提取为
    # 空——被压成同一个数字，读者拿到「10 篇抓取失败」时无从判断该往哪
    # 查。在有本机代理的环境下这不是小概率事件：`_reject_unsafe_host`
    # 在发请求前用 `socket.getaddrinfo` 本地解析主机名，而 `requests`
    # 一旦检测到 `HTTP_PROXY`/`HTTPS_PROXY` 就会把整个 URL 连主机名一起
    # 交给代理、自己不再本地解析——于是这道主机安全闸门会在代理环境下
    # 对**每一个**候选都报「DNS 解析失败」，10 篇全部失败，却仍然只印出
    # 一行「10 篇抓取失败」，表面上符合「不阻断整轮研究」，实际上正是
    # 「缺什么必须显式列出来」要防的那种沉默降级。
    #
    # 只存**去重后**的原因（`gather()` 按去重后的顺序追加），不是每个
    # candidate 一条：候选可能有十几个，但失败的根因通常只有个位数种，
    # 逐条罗列同一句诊断十次除了刷屏没有更多信息量。
    #
    # **去重键是诊断部分，不是整条消息。** 每条消息都以
    # `f"{url}：{诊断}"` 的形式带着自己的 URL 前缀（见 fetch()/
    # `_reject_unsafe_host` 的写法）——旗舰场景恰恰是"一种诊断、十个不同
    # URL"（本机代理环境下每个候选各自的域名解析失败），如果拿整条消息
    # （含各不相同的 URL）去重，这十条永远各不相同，去重形同虚设，一种
    # 原因照样打印十遍。`gather()` 因此按 `诊断.split("：", 1)[-1]` 取出
    # 去掉 URL 前缀之后的那部分作为去重键，命中过的诊断不再追加——但
    # 列表里存的仍是**完整消息**（带着第一次撞见这种诊断时的那个 URL），
    # 不是把 URL 直接丢掉：读者除了「这是哪一种失败」，往往还想知道
    # 「至少举一个例子，我该去查哪个网址」，完全抹掉 URL 是过度归并。
    fetch_failed_reasons: list[str] = field(default_factory=list)
    # 发布日期由模型给出、未经代码核验的篇数。**必须能说出来** ——
    # 用户决定信模型报的日期，折中是把不可核验的那部分显式标出来，
    # 而不是让它隐身在一堆看起来一样的引用里。
    model_dated: int = 0
    # 入库时主机安全校验降级（IP 层被跳过）的篇数。
    #
    # **必须能说出来。** 这一项的存在本身是一次实跑教训的产物：加
    # `_reject_unsafe_host` 时那道 IP 层预检在本机代理环境下拒掉了
    # 11/11 个候选，功能产出恒为零、却只印出一行「11 篇抓取失败」。
    # 修法是解析不可信时退回主机名层——但那意味着**这一轮的内网地址
    # 拦截实际靠的是代理，不是本代码**。这不是缺陷，是环境决定的降级，
    # 可是读者无从从「10 篇入库」里看出来。与 `fetch_failed_reasons`
    # 同一条道理：静默的降级比报错更难发现。
    host_check_degraded: int = 0


def _doc_id(final_url: str) -> str:
    return "ow_" + hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:12]


def gather(client, *, code: str, company: str, industry: str,
           as_of: date, corpus) -> OpenWebResult:
    """模型给网址 → 代码抓正文 → 入语料库。

    **调用方必须传入已显式开启 WebSearch/WebFetch 工具的 client**，
    例如 `make_client(tools=["WebSearch", "WebFetch"])`。不开不会报错，
    只会安静地什么都搜不到 —— 与 web.search 同一条前提。
    """
    res = OpenWebResult()
    raw = client(PROMPT.format(company=company or code, code=code,
                               industry=industry or "未知",
                               per_slot=PER_SLOT, as_of=as_of.isoformat()))
    seen = {d.url for d in corpus.docs}
    per_slot: dict[str, int] = {}
    # 去重键集合：存的是去掉 URL 前缀之后的诊断部分，不是完整消息——
    # 见 `OpenWebResult.fetch_failed_reasons` 字段注释。
    seen_diagnoses: set[str] = set()

    for cand in parse_candidates(raw):
        if len(res.docs) >= MAX_DOCS:
            break
        if cand.published > as_of:
            # 时点守卫的第一道：候选阶段就挡，省一次网络请求。
            # 第二道在 corpus.add_if_visible 里（按最终文档再滤一遍）。
            corpus.skipped.append(f"{cand.title}（{cand.published}）")
            continue
        if per_slot.get(cand.slot, 0) >= PER_SLOT:
            continue
        if cand.url in seen:
            continue
        try:
            got = fetch(cand.url)
        except Exception as e:                              # noqa: BLE001
            # **必须接 Exception，不能只接 FetchFailed。** `fetch()` 现在
            # 已经把整段读取（含流式正文）都收进了 with_retry +
            # FetchFailed 的双重边界，理论上不该再有别的异常类型逃出
            # 来——但这道 per-candidate 保护正是上一次修复烂掉的地方：
            # `stream=True` 让正文变成惰性读取，只接 FetchFailed 曾经让
            # 一个裸 ChunkedEncodingError 从这里原样冒出去，一路冒出
            # gather()、冒到 `_load_openweb` 的 `except Exception`，在
            # `openweb.save()` 执行之前就返回，本轮已经抓到的其余文档
            # 全部没能跨阶段存活。宁可这道防线看起来"不够精确"，也不能
            # 让未来任何一次疏漏都需要重新证明"这里必须接多宽"。
            res.fetch_failed += 1
            # **去重键必须从截断之前的完整消息里切出来。** 旧实现先
            # `str(e).strip()[:_REASON_LIMIT]` 截断成 `reason`，再从
            # `reason` 里切诊断部分——真实新闻 URL 常有 80~100 字符，
            # `f"{url}：{诊断}"` 拼起来轻松超过 `_REASON_LIMIT`（160），
            # 于是诊断部分被截断保留的"尾巴"长度取决于 URL 有多长：
            # 十个不同长度的 URL、同一种诊断，切出来的尾巴各不相同，
            # 去重形同虚设——这正是本文件开头点名的"代理环境下全部候选
            # 一次性倒在同一种诊断上"的旗舰场景。这里改成先在
            # **未截断**的 `msg` 上切出诊断部分作为去重键，`msg` 本身
            # 才轮到截断、只用于落盘展示。
            msg = str(e).strip() or e.__class__.__name__
            # `split("：", 1)[-1]` 没有找到"："时原样返回整条字符串，
            # 兜住 `msg` 恰好不含"："的边角情况（比如 `e.__class__.
            # __name__` 兜底分支），行为退化成按整条消息去重，不会因此
            # 报错或漏判。
            diagnosis = msg.split("：", 1)[-1]
            if diagnosis not in seen_diagnoses:
                seen_diagnoses.add(diagnosis)
                res.fetch_failed_reasons.append(msg[:_REASON_LIMIT])
            continue
        if got.final_url in seen:
            # 重定向之后才发现撞上已有文档 —— 抓都抓了，但不重复入库。
            continue

        # 日期：代码能从 meta 提到就**以代码为准**，提不到才用模型报的。
        # 这不增加丢弃、不损召回，只是让不可核验的那部分可见。
        published = got.meta_published or cand.published
        if got.meta_published is None:
            res.model_dated += 1

        doc = Document(
            doc_id=_doc_id(got.final_url),
            url=got.final_url,
            title=cand.title,
            publisher=cand.publisher,
            # **按代码跟完重定向后的最终 URL 分级**，不是模型给的那个。
            tier=tier_of_url(got.final_url),
            published=published,
            retrieved=as_of,
            text=got.text,
            prompt_limit=TEXT_LIMIT,
            date_source="meta" if got.meta_published is not None else "model",
        )
        if not corpus.add_if_visible(doc):
            continue
        seen.add(doc.url)
        per_slot[cand.slot] = per_slot.get(cand.slot, 0) + 1
        if got.host_check_degraded:
            res.host_check_degraded += 1
        res.docs.append(doc)
        if cand.extra and cand.slot not in res.extra_slots:
            res.extra_slots.append(cand.slot)

    # 缺口只按固定槽位判定 —— 额外槽位是模型的自由发挥，不能进缺口口径。
    res.missing_slots = [s for s in SLOTS if not per_slot.get(s)]
    return res


def summary(res: OpenWebResult) -> str:
    """给人看的采集结果。**缺什么必须说出来** —— 只列取到的部分，
    读者会以为资料是全的。"""
    lines = [f"开放网络资料 {len(res.docs)} 篇"]
    if res.extra_slots:
        lines.append("  · 额外主题：" + "、".join(res.extra_slots))
    if res.model_dated:
        lines.append(f"  ⚠ 其中 {res.model_dated} 篇的发布日期由模型给出，"
                     "未经代码核验")
    if res.host_check_degraded:
        lines.append(f"  ⚠ 其中 {res.host_check_degraded} 篇入库时主机安全"
                     "校验降级（本机 DNS 被代理劫持，IP 层校验无从进行）"
                     "——内网地址拦截本轮依赖代理侧")
    if res.fetch_failed:
        lines.append(f"  – {res.fetch_failed} 篇抓取失败")
        # 去重后的原因逐条列出——不然「10 篇抓取失败」在 DNS 解析不出
        # （代理环境）、内容类型不对、正文提取为空这几种诊断之间长得
        # 一模一样，读者分不出该往哪查。
        for reason in res.fetch_failed_reasons:
            lines.append(f"    · {reason}")
    for slot in res.missing_slots:
        lines.append(f"  – {slot}：未取到")
    return "\n".join(lines)


# ── 跨阶段存活 ──────────────────────────────────────────────────
#
# **discover 调模型，而 _analyse_facts 那一层两个阶段都跑。** 若第二阶段
# 重跑一次 discover，取到的文档会与第一阶段不同，第一阶段正文里写下的
# 外部引文因此在第二阶段核验不过 —— 本项目已经吃过这类亏（判断项的依据
# 没跨阶段存活，第二阶段被自己的闸门拒掉）。
#
# 「能重算的就不要存」在这里反向成立：discover 的结果**重算不出来**，
# 正是该存的那一类。存全文而不只存网址，是因为照网址重抓时页面改一个字
# 引文核验就挂。

OPENWEB_JSON = "openweb.json"


def _doc_to_json(d: Document) -> dict:
    return {"doc_id": d.doc_id, "url": d.url, "title": d.title,
            "publisher": d.publisher, "tier": d.tier.value,
            "published": d.published.isoformat(),
            "retrieved": d.retrieved.isoformat(), "text": d.text,
            "prompt_limit": d.prompt_limit, "date_source": d.date_source}


def _str_field(raw: dict, key: str) -> str:
    """取一个字符串字段，类型不对就抛——留给 load() 的逐条 try/except
    去接。**这是本次修复的核心**：tier/published/retrieved 之外的字段
    此前直接从 JSON 原样赋值，字段齐全但类型烂掉的条目能顺利构造出
    Document、混进语料库，直到 phase-2 的 for_prompt()/verify() 才炸——
    已经在 load() 的保护范围之外。校验必须堵在这道边界上，不能指望
    crash 现场帮你补。"""
    v = raw[key]
    if not isinstance(v, str):
        raise TypeError(f"{key} 应为 str，实际是 {type(v).__name__}")
    return v


def _str_list_field(raw: dict, key: str) -> list[str]:
    """取一个字符串列表字段，逐元素过滤掉非 str 的——不是整份退化。

    与 `_str_field`（管 `Document` 单条字段：类型不对就抛，交给调用方
    的逐条 try/except 去跳过整个文档）故意不对称。这里的调用方是
    `load()` 里解析头部字段那一段，没有逐元素 try/except；容器本身
    不是列表（`raw.get(key)` 给个 `42`）时，`list(...)` 照样会抛，
    冒给外层 `except Exception` 按「整份文件退化」处理——这一层不变，
    与 `missing_slots`/`extra_slots`/`fetch_failed_reasons` 的既有约定
    一致。变的只是「容器合法、但里面混进一个非 str 元素」这一种：这里
    选择丢弃那一条脏元素，而不是让全文件陪葬。

    真实事故：`fetch_failed_reasons: ["ok reason", 7]` 这种手改/半写坏
    的文件此前会被原样 `list(...)` 收下，`full._openweb_notes` 随后对
    它做 `"；".join(...)`，遇到非 str 元素直接 `TypeError`，把 phase-2
    整个研究流程带崩——这正是本函数 docstring 里「不是『一个坏文件能
    让整次研究流程崩掉』」要防的事，而且是上一轮修复才新开的口子（旧
    实现里这几个字段从未被写过、`missing_slots` 走的是 f-string 拼接
    不会崩，只有 `fetch_failed_reasons` 走 join）。丢一条脏元素不影响
    docs 能否正常读回，也不影响时点守卫等核心契约——这个字段本身只是
    给人看的辅助展示信息，不参与任何判断逻辑。

    **容器本身必须是 list，不能是任何"看起来能被 list() 接受"的东西。**
    `list(raw.get(key, []))` 曾经对**任何**可迭代对象都来者不拒：
    `{"fetch_failed_reasons": "DNS 解析失败"}` 里那个值是个字符串，
    `list("DNS 解析失败")` 不会抛——它会把字符串拆成单字符列表
    `['D', 'N', 'S', ' ', ...]`，每个单字符都通过下面的 `isinstance(v,
    str)` 检查，安安静静地"成功"变成一份面目全非但类型合法的假数据；
    同理一个 dict 值会被 `list()` 悄悄取成它的 key 列表。两种情况都不
    是「容器合法、混进一条脏元素」，是**容器本身的类型就不对**，必须
    像 `_str_field` 拒绝非 str 值一样显式拒绝，冒给外层 `except
    Exception` 按整份文件退化处理——不能让 `list()` 的"宽容"把一次
    类型错误伪装成一次成功的解析。"""
    items = raw.get(key, [])
    if not isinstance(items, list):
        raise TypeError(f"{key} 应为 list，实际是 {type(items).__name__}")
    return [v for v in items if isinstance(v, str)]


def _doc_from_json(raw: dict) -> Document:
    prompt_limit = raw.get("prompt_limit")
    if prompt_limit is not None and not isinstance(prompt_limit, int):
        # bool 是 int 的子类，但落盘数据里不会出现 True/False 当截断长度，
        # 且即便出现，isinstance(True, int) 为真也不会构造出坏文档——
        # 不必为这个理论上的边角另设判断。
        raise TypeError(
            f"prompt_limit 应为 int 或 None，实际是 {type(prompt_limit).__name__}")
    return Document(doc_id=_str_field(raw, "doc_id"),
                    url=_str_field(raw, "url"),
                    title=_str_field(raw, "title"),
                    publisher=_str_field(raw, "publisher"),
                    tier=Tier(raw["tier"]),
                    published=date.fromisoformat(raw["published"]),
                    retrieved=date.fromisoformat(raw["retrieved"]),
                    text=_str_field(raw, "text"),
                    prompt_limit=prompt_limit,
                    date_source=_str_field(raw, "date_source")
                    if "date_source" in raw else "")


def save(res: OpenWebResult, path: Path | str) -> None:
    Path(path).write_text(json.dumps({
        "docs": [_doc_to_json(d) for d in res.docs],
        "missing_slots": res.missing_slots,
        "extra_slots": res.extra_slots,
        "fetch_failed": res.fetch_failed,
        "fetch_failed_reasons": res.fetch_failed_reasons,
        "model_dated": res.model_dated,
        "host_check_degraded": res.host_check_degraded,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path: Path | str, corpus) -> OpenWebResult:
    """读回落盘的文档并入语料库。

    容忍两个不同层级的损坏，两者都不抛：

    - **整份文件损坏**——文件不存在、JSON 语法错误、顶层不是 dict（比如
      手抖写成一个 `[]` 或裸字符串）、或某个头部字段的类型烂到没法安全
      解读（`fetch_failed`/`model_dated` 不是数字、`missing_slots`/
      `extra_slots`/`fetch_failed_reasons` 不是可迭代对象）。这些情况下
      **整份**退化成空 `OpenWebResult()`：任何一处都可能污染后面所有
      字段的读取，没有「只信一半」这回事。容器合法但里面混进非 str
      元素是更细一级的损坏，不算在这一类里——见 `_str_list_field`：
      丢的是那一条元素，不是整份文件。
    - **单条文档损坏**——`docs` 列表本身是合法列表，但其中某一条不是
      合法的 Document JSON（缺字段、字段类型不对、tier 不是已知取值）。
      这种情况下只跳过**这一条**，其余合法条目照常读回、照常入库。

    两者都不会向上抛异常：调用方的契约是「采不到就是这节缺外部资料」，
    不是「一个坏文件能让整次研究流程崩掉」。

    **时点守卫照样要过一遍**（走 corpus.add_if_visible）：as_of 可能与
    落盘时不同，「这是我们自己存的」不构成放行理由。
    """
    p = Path(path)
    if not p.exists():
        return OpenWebResult()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return OpenWebResult()

    try:
        res = OpenWebResult(
            missing_slots=_str_list_field(raw, "missing_slots"),
            extra_slots=_str_list_field(raw, "extra_slots"),
            fetch_failed=int(raw.get("fetch_failed", 0)),
            fetch_failed_reasons=_str_list_field(raw, "fetch_failed_reasons"),
            model_dated=int(raw.get("model_dated", 0)),
            host_check_degraded=int(raw.get("host_check_degraded", 0)))
        docs_raw = list(raw.get("docs", []))
    except Exception:                                       # noqa: BLE001
        # 顶层不是 dict（没有 .get）、docs 不是可迭代对象、或
        # fetch_failed/model_dated/missing_slots/extra_slots/
        # fetch_failed_reasons 类型烂到没法安全解读——任何一种都不是
        # 「跳过这一条」能兜住的，整份文件的可信度已经不在，退化成空
        # 结果。
        return OpenWebResult()

    seen = {d.url for d in corpus.docs}
    for item in docs_raw:
        try:
            doc = _doc_from_json(item)
        except Exception:                                   # noqa: BLE001
            continue
        if doc.url in seen or not corpus.add_if_visible(doc):
            continue
        seen.add(doc.url)
        res.docs.append(doc)
    return res
