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

from urllib.parse import urlparse

from ir_agent.sources.external import Tier

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
