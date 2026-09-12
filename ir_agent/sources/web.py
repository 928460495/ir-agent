"""开放网络定向采集 —— 按分解式的因子去找数据。

**外部数据的证明力比账本低一个量级，这件事必须在代码里体现。**
账本数字可以一路反查到接口的原始响应；外部引文只能核验「我确实在某个网址上
抓到过这句话」，核验不了「这句话是对的」。两者在报告里长得一样，所以分级
不是给来源打分，是**限定它能支撑多强的结论**。

因此这里比 external.py 的引用规则更严一层：**MEDIA 级不得单独支撑一个因子的
取值**。引用一句话和进模型算钱，门槛不该一样。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from ir_agent.lineparse import keyed
from ir_agent.sources.external import Tier

# 实测 claude -p 可联网搜索，但**必须显式开启工具**（2026-09-12 验证）:
#   make_client(tools=["WebSearch", "WebFetch"])
# 默认调用 `claude -p --output-format json` 不启用任何工具 —— 第一次探测
# 正因为只测了默认调用，误判成「不可联网」。**测的是默认行为，不是能力。**
# 详见 docs/superpowers/notes/2026-09-12-web-capability.md。
WEB_CAPABLE = True

_LINE = keyed("发现")
_URL = re.compile(r"https?://\S+")

# 常见日期写法，按顺序尝试。模型不一定老实按提示词里的 YYYY-MM-DD 写，
# "2026年7月1日""2026/07/01""2026.07.01" 都是常见的等价写法 —— 解析
# 太挑剔会连累大量本来合格的发现被误杀（尤其是配合下面"解析失败即丢弃"
# 的规则之后）。认不出的一律按「日期不明」处理，见 _parse_date 用法。
_DATE_PATTERNS = (
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})"),
    re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"),
    re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})"),
)

# 来源分级。官方与协会 > 卖方 > 媒体。判不出来的一律按媒体处理 ——
# 往高了猜等于凭空给一个来源加权，这条原则本身没变过。变过三次的是
# 「怎么判」。
#
# SEMI / Gartner / TrendForce / IDC / Yole / Omdia / IC Insights / 集邦咨询 /
# 赛迪顾问 严格说是付费市场研究商，不是 external.py 的 TIER_LABEL 里说的
# 统计局/协会/部委/交易所。放进 OFFICIAL 是有意的：封测的产能、出货这类
# 数据，这几家就是该领域证明力最强的一手来源，官方统计反而覆盖不到、
# 或滞后到失去时效。**分级看的是这句话的证明力有多强，不是这个机构算
# 不算官方机构**——集邦咨询是 TrendForce 的中文马甲，同一家公司没有
# 理由分级不同，一并放进 OFFICIAL 白名单。
#
# ── 为什么换成白名单，而不是再修一条判别规则 ──────────────────────
# 同一个词元在不同名字里角色不同：「证券」在「中信证券」是机构类型
# 后缀，在「新浪证券」「腾讯证券」只是门户的频道名；「IDC」本身是市场
# 研究公司，出现在「中国 IDC 圈」里指的是互联网数据中心；`\bSEMI\b`
# 这样的词边界正则挡得住 Semiconductor，挡不住 SEMI-Insights / The SEMI
# Times。三轮审查、三种判别机制——裸子串 → 以"报"收尾排除 → 媒体标识
# 优先＋词尾结构——依次被真实来源名打穿，根因都是同一个：**词元本身不
# 携带机构性质，位置和上下文才携带，而任何基于位置/结构的模糊规则都能
# 被一个新来源名绕开。**
#
# 于是不再问"这个名字像不像官方/卖方"，只问"这个名字是不是白名单里那
# 家机构本身（允许带一点无害的修饰词）"：
#
#     升级到 SELLSIDE/OFFICIAL 只有「白名单全称 + 允许的修饰词」这
#     一条路。不要加子串匹配、词尾规则、词边界正则或"媒体标识"黑名单
#     ——这三种都试过，都被真实来源名打穿（证券时报网 / 新浪证券 /
#     Securities Daily / 中国 IDC 圈）。要支持新来源，往白名单加全称。
#     判不出来落 MEDIA 是正确行为，不是缺陷。
#
# 这条路径安全，靠的是 tier_of 唯一下游用途 supports_value() 带来的
# 代价不对称：把媒体错拔成 SELLSIDE/OFFICIAL，等于一篇博客单独决定了
# DCF 里的因子取值，是危害；把真官方/卖方错压成 MEDIA，只是需要再凑
# 一条来源互相印证，是召回损失。因此策略对"拔高"要极端保守，对"压低"
# 可以从容——这正是白名单机制的方向，不是缺省地各打五十大板。

# ── 归一化：让"国家统计局官网"和"国家统计局　官网"命中同一个前缀 ──
# 只做三件事：去空白（含全角空格）、剔除常见全/半角标点、ASCII 转大写。
# 不做任何语义层面的"聪明"处理——归一化越简单，越不会自己制造新漏洞。
_WS_RE = re.compile(r"[\s　]+")
_PUNCT_STRIP = "（）()·、，,．.-_「」《》：:；;'\""

# 繁→简：只覆盖白名单条目里实际出现的字，不引入第三方库、不做通用
# 繁简转换。覆盖不到的繁体名落 MEDIA，方向安全（宁可少召回，不可错拔高）。
_TRAD_TO_SIMP = {
    "證": "证", "國": "国", "華": "华", "廣": "广", "發": "发", "萬": "万",
    "興": "兴", "業": "业", "東": "东", "風": "风", "長": "长", "統": "统",
    "計": "计", "財": "财", "關": "关", "總": "总", "監": "监", "員": "员",
    "會": "会", "導": "导", "體": "体", "協": "协", "詢": "询", "顧": "顾",
    "問": "问", "網": "网", "眾": "众", "號": "号", "報": "报", "數": "数",
    "據": "据", "庫": "库", "聞": "闻", "際": "际", "銀": "银",
}


def _normalize(name: str) -> str:
    """tier_of 匹配前的唯一预处理：去空白、剔标点、繁转简、ASCII 转大写。"""
    if not name:
        return ""
    s = "".join(_TRAD_TO_SIMP.get(ch, ch) for ch in name)
    s = _WS_RE.sub("", s)
    s = "".join(ch for ch in s if ch not in _PUNCT_STRIP)
    return s.upper()


# 白名单——全称，不是片段。往这里加一条全称就能支持一个新来源；
# 不要往这几个元组里塞任何"关键词"性质的短片段（比如裸的"证券"
# "期货"），那等于把白名单退化回子串匹配。
_SELLSIDE_CN = (
    "中信证券", "中信建投", "中金公司", "国泰君安证券", "国泰海通",
    "海通证券", "海通国际", "华泰证券", "招商证券", "广发证券",
    "申万宏源", "兴业证券", "东吴证券", "国信证券", "光大证券",
    "天风证券", "民生证券", "长江证券", "方正证券", "东方证券",
    "中泰证券", "浙商证券",
)
_SELLSIDE_ASCII = (
    "MORGAN STANLEY", "GOLDMAN SACHS", "UBS", "JP MORGAN", "J.P. MORGAN",
    "NOMURA", "CITI", "CREDIT SUISSE", "JEFFERIES", "BERNSTEIN",
)

# 集邦咨询、赛迪顾问放在这里而不是另立名单：分级看的是证明力，这两家
# 是封测/半导体产业链数据事实上的权威一手来源，理由与 SEMI/Gartner/
# TrendForce/IDC 完全一致，见本节顶部注释。
_OFFICIAL_CN = (
    "国家统计局", "统计局", "工业和信息化部", "工信部", "国家发展改革委",
    "发改委", "商务部", "财政部", "海关总署", "中国人民银行",
    "中国证券监督管理委员会", "证监会", "上海证券交易所", "深圳证券交易所",
    "北京证券交易所", "上交所", "深交所", "北交所", "中国半导体行业协会",
    "中国电子专用设备工业协会", "集邦咨询", "赛迪顾问",
)
_OFFICIAL_ASCII = (
    "SEMI", "GARTNER", "TRENDFORCE", "IDC", "YOLE", "OMDIA", "IC INSIGHTS",
    "COUNTERPOINT", "WSTS", "SIA",
)

# 允许的修饰词——白名单全称命中后，剩余部分必须能被这张表里的词
# **完整拼出**（可以是好几个词拼接，比如"研究部"+"官网"），拼不满就
# 说明剩余部分里混进了白名单没解释到的东西（比如"中金公司观察"里的
# "观察"），落 MEDIA。
_MODIFIERS = (
    "官网", "网站", "官方网站", "网", "公众号", "微信公众号",
    "研究所", "研究院", "研究部", "研究中心", "发展研究中心", "研究",
    "股份有限公司", "有限公司",
    "公报", "月报", "季报", "年报", "快报",
    "数据库", "新闻发言人", "发布会",
    "中国", "国际",
)

_SELLSIDE_NORM = tuple(_normalize(x) for x in _SELLSIDE_CN + _SELLSIDE_ASCII)
_OFFICIAL_NORM = tuple(_normalize(x) for x in _OFFICIAL_CN + _OFFICIAL_ASCII)
_MODIFIERS_NORM = tuple(sorted({_normalize(x) for x in _MODIFIERS}, key=len,
                                reverse=True))


@lru_cache(maxsize=8192)
def _fully_modifiers(remainder: str) -> bool:
    """remainder 能否被 _MODIFIERS_NORM 里的词首尾相接、不多不少地拼满。

    需要"拼接"而不是"单个词命中"，是因为真实的修饰组合会叠加——
    比如"中信证券研究部官网"里，白名单全称"中信证券"之后剩下
    "研究部官网"，是"研究部"+"官网"两个修饰词拼出来的，不是其中
    任何一个单独能覆盖的。允许回溯尝试所有切法（而不是只贪心切最长的
    那个），保证只要存在一种能拼满的切法就不会误判——组合数受修饰词表
    很小、剩余串很短的限制，配合 lru_cache 不会有性能问题。
    """
    if remainder == "":
        return True
    return any(remainder.startswith(mod) and _fully_modifiers(remainder[len(mod):])
               for mod in _MODIFIERS_NORM if mod)


class WebUnavailable(Exception):
    """联网能力不可用 —— 不静默返回空，那会让「搜不到」和「没搜」长得一样。"""


@dataclass(frozen=True)
class Finding:
    factor_key: str
    claim: str
    url: str
    publisher: str
    tier: Tier
    # 注意：不是 date | None。发现二的教训是，一旦类型上允许 None，
    # 编排层迟早会写出 `published=x.published or as_of` 这种"看似合理"
    # 的兜底 —— 而 as_of 恰好是唯一必然不会触发 OutOfWindow 的取值，
    # 等于把最不可信的证据（连发布日期都不明）洗成时点上最合规的证据。
    # 正确的处置在 parse_findings 里就已经做完：日期不明的发现直接丢弃，
    # 传到这里的 Finding 一定带着真实日期。不留这个类型上允许、
    # 语义上危险的 None 出口，后来人就不必在每个调用点重新记得防它。
    published: date


def _parse_date(raw: str) -> date | None:
    """尝试用几种常见写法解析日期；认不出或者不是合法日期就返回 None，
    调用方（parse_findings）应将 None 视为「日期不明」并丢弃整条发现。"""
    s = raw.strip()
    for pat in _DATE_PATTERNS:
        m = pat.search(s)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    return None


def tier_of(publisher: str) -> Tier:
    """来源分级。

    **设计契约——升级到 SELLSIDE/OFFICIAL 只有一条路：**

        白名单全称 + 允许的修饰词。

    不要加子串匹配、词尾规则、词边界正则或"媒体标识"黑名单——这三种
    都试过（依次是 2816197 之前的裸子串、2816197、6d9cd4c），都被真实
    来源名打穿（证券时报网 / 新浪证券 / Securities Daily / 中国 IDC
    圈）。根因是同一个：**同一个词元在不同名字里角色不同，任何基于
    位置或结构的模糊匹配都在赌"这次的反例还没出现"。**

    要支持新来源，往 `_SELLSIDE_CN`/`_SELLSIDE_ASCII`/`_OFFICIAL_CN`/
    `_OFFICIAL_ASCII` 加全称，或往 `_MODIFIERS` 加修饰词——不要碰这个
    函数的判别逻辑本身。**判不出来落 MEDIA 是正确行为，不是缺陷**：
    见本节顶部注释里 tier_of 唯一下游用途 supports_value() 的代价
    不对称——拔高是危害，压低只是召回损失。
    """
    name = _normalize(publisher or "")
    if not name:
        return Tier.MEDIA

    candidates: list[tuple[int, Tier]] = []
    for entry in _SELLSIDE_NORM:
        if entry and name.startswith(entry):
            candidates.append((len(entry), Tier.SELLSIDE))
    for entry in _OFFICIAL_NORM:
        if entry and name.startswith(entry):
            candidates.append((len(entry), Tier.OFFICIAL))
    # 优先试最长的白名单前缀——多个全称都能作为前缀命中时（理论上
    # 白名单条目之间不应互为前缀，这里只是防御性地选更具体的那个），
    # 剩余部分更短，更可能被修饰词完全解释。
    candidates.sort(key=lambda pair: -pair[0])

    for entry_len, tier in candidates:
        if _fully_modifiers(name[entry_len:]):
            return tier
    return Tier.MEDIA


def parse_findings(text: str) -> list[Finding]:
    out: list[Finding] = []
    for hit in _LINE.finditer(text):
        parts = [p.strip() for p in hit.group(1).split("|")]
        # 一道守卫，卡在 5：一行要完整给出 因子|陈述|网址|机构|日期 五段，
        # 少任何一段——包括日期——都不是「4 段也合法，日期可选」，而是
        # 整条不合规。之前分两道守卫（先 < 4 再 < 5）会让人以为中间存在
        # 「4 段合法」的分支，实际上那道 < 4 只是为了让下面取 parts[2]/
        # parts[3] 不越界，本身并不代表 4 段是可接受的长度。
        if len(parts) < 5:
            continue
        url_m = _URL.search(parts[2])
        if not url_m:
            # 外部依据必须有 URL —— 没有网址就无从复核，与凭空捏造无异。
            continue
        pub = parts[3]
        # 「必须给出网址」与「必须给出发布日期」在提示词里是并列的硬性
        # 要求，此处也应对称处理：日期解析不出来（写了但认不出/不是
        # 合法日期），一律整条丢弃，绝不放行成 published=None。原因见
        # Finding.published 字段上的注释——一旦允许 None 流出本模块，
        # 编排层的兜底逻辑会把它悄悄变成「发布日期=as_of」，让最不可信
        # 的证据获得最宽松的时点待遇，静静滑过系统里最关键的一道防线
        # （时点守卫）。这个隐患必须在产生 Finding 的这一步就堵死，
        # 不能指望下游每个调用点都记得防。
        when = _parse_date(parts[4])
        if when is None:
            continue
        out.append(Finding(factor_key=parts[0], claim=parts[1],
                           url=url_m.group(0), publisher=pub,
                           tier=tier_of(pub), published=when))
    return out


PROMPT = """为一份 A 股研究报告采集一个具体的数据点。

行业：{industry}
要找的因子：**{factor}**

上网搜索，找出关于这个因子的具体数据（水平、同比变化、行业均值皆可）。
每条一行：

`发现: {key} | <具体数据的一句话陈述> | <网址> | <发布机构> | <发布日期 YYYY-MM-DD>`

**硬性要求**
- **必须给出网址**。没有网址的条目会被直接丢弃 —— 无从复核的转述与凭空
  捏造无异。
- **必须给出发布日期（YYYY-MM-DD）**。没写日期、或日期写得不够精确以
  致解析不出来（比如只写到"2026年7月"这样的月份粒度）的条目，同样会
  被直接丢弃——跟没有网址一样，说不清时点的证据一样等同于凭空捏造。
  晚于 {as_of} 的会被剔除：拿未来的资料分析过去，与账本的时点守卫是
  同一件事。
- 数据要具体（「稼动率约 78%」而非「稼动率偏低」）。
- 找不到就一行都不要输出，**不要用推测填充**。

只输出上述格式的行。
"""


def search(client, factor_label: str, factor_key: str, industry: str,
           as_of: date) -> list[Finding]:
    """按因子上网搜索证据。

    **调用方必须传入已显式开启 WebSearch/WebFetch 工具的 client**——
    例如 `make_client(tools=["WebSearch", "WebFetch"])`。默认（不带
    `tools` 参数）构造出的 client 不联网，会让模型如实回答「不能联网」，
    而不是本函数抛出的 WebUnavailable；这不是本函数能拦的错误，因为
    client 是否联网由调用方在构造时决定，本函数只负责在 WEB_CAPABLE
    为 False（这条链路整体判定不可联网）时提前拒绝。
    """
    if not WEB_CAPABLE:
        # 措辞不能写死「claude -p 不能联网」——本文件顶部已经交代过，
        # 之前的探测测的是默认调用不开工具，不是 claude -p 真的没有联网
        # 能力。这里判定不可用，唯一的原因是 WEB_CAPABLE 这个开关被设成
        # 了 False。真正该查的是谁、为什么关了它，而不是重新怀疑一遍
        # 已经验证过的联网能力，那会把排查者引向错误方向。
        raise WebUnavailable(
            "开放网络采集当前被禁用（WEB_CAPABLE=False）—— 请检查是谁、"
            "为什么关闭了这个开关；claude -p 本身的联网能力已经验证过，"
            "不必重新怀疑（见 docs/superpowers/notes/"
            "2026-09-12-web-capability.md）")
    raw = client(PROMPT.format(industry=industry, factor=factor_label,
                               key=factor_key, as_of=as_of.isoformat()))
    got = parse_findings(raw)
    # parse_findings 已经保证 published 不为 None（日期不明的发现在那一步
    # 就被丢弃了），这里只需要做时点过滤，不必再兼容 None。
    return [f for f in got if f.published <= as_of]


def supports_value(findings: list[Finding]) -> tuple[bool, str]:
    """这组发现够不够支撑一个因子的取值。

    **MEDIA 级不得单独支撑。** 现有 external.py 的分级管的是「能不能引用」；
    因子取值是要进模型算钱的，门槛不该一样 —— 一条财经媒体报道可以作为线索，
    不该单独决定 DCF 里的产能增速。
    """
    if not findings:
        return False, "未找到任何数据"
    if any(f.tier in (Tier.OFFICIAL, Tier.SELLSIDE) for f in findings):
        return True, ""
    # 措辞不能写「仅单一媒体来源」——即便凑齐三条来自不同媒体的发现，
    # 只要全是 MEDIA 级，一样不该通过。门槛是来源等级，不是数量；
    # 写「单一」会误导读者以为多找几家媒体互相印证就能过关，
    # 而这恰恰绕开了本模块存在的唯一理由。
    return False, ("全部为财经媒体来源，不足以支撑因子取值 —— "
                   "门槛是来源等级而非数量，即便多家媒体互相印证也不算数，"
                   "需至少一条官方/协会或卖方研究佐证")
