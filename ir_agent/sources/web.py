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
# 往高了猜等于凭空给一个来源加权。
#
# SEMI / Gartner / TrendForce / IDC / Yole / Omdia / IC Insights 严格说是
# 付费市场研究商，不是本 docstring（官方定义见 external.py 的 TIER_LABEL）
# 里说的统计局/协会/部委/交易所。放进 OFFICIAL 是有意的：封测的产能、
# 出货这类数据，这几家就是该领域证明力最强的一手来源，官方统计反而
# 覆盖不到、或滞后到失去时效。分级看的是「这句话的证明力有多强」，
# 不是「这个机构算不算官方机构」。
#
# 中文关键词用裸子串匹配是安全的——这些都是机构全称的独有片段
# （"统计局""交易所""人民银行"……），不是"证券""semi""idc"这类会在
# 别的名字中间冒出来的通用短词，因此不需要下面 ASCII 关键词那套
# 词边界处理。
_OFFICIAL_CN = ("统计局", "工信部", "发改委", "商务部", "财政部", "海关",
                "协会", "交易所", "上交所", "深交所", "北交所", "证监会",
                "人民银行", "央行", "集邦", "赛迪")
_OFFICIAL_ASCII = ("SEMI", "GARTNER", "TRENDFORCE", "IDC", "YOLE", "OMDIA",
                    "IC INSIGHTS")

# 结构化卖方判别，见 tier_of 顶部注释：以 证券/期货/资管 收尾才是机构
# 类型后缀；"研究所/研究院/研究部"单独不算数，必须同时含"证券"字样。
_SELLSIDE_SUFFIX = ("证券", "期货", "资管")
_SELLSIDE_RESEARCH_SUFFIX = ("研究所", "研究院", "研究部")
# 结构规则覆盖不到的具名卖方机构（"中金公司"不以"证券"收尾），
# 用全称做裸子串匹配是安全的——全称足够长，不会在别的机构名里凑巧出现。
_SELLSIDE_CN_EXPLICIT = ("中金公司",)
_SELLSIDE_ASCII = ("INVESTMENT", "SECURITIES", "MORGAN STANLEY",
                    "GOLDMAN SACHS", "UBS", "J.P. MORGAN", "NOMURA")

# 媒体标识优先级最高：命中任意一个直接判 MEDIA，不再往下试官方/卖方
# 关键词。这是对付"证券时报网"这类前缀碰撞的正解——"证券"在这类报纸
# 名字里是前缀，报纸的自我标识（报/网/新闻……）才是能定性的后缀。
_MEDIA_MARKERS = ("报", "网", "周刊", "杂志", "传媒", "晚报", "商报", "新闻")

_ASCII_WORD_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _has_ascii_keyword(name: str, keywords: tuple[str, ...]) -> bool:
    """英文关键词按词边界匹配，而不是裸子串——见 tier_of 顶部注释。"""
    for kw in keywords:
        pat = _ASCII_WORD_RE_CACHE.get(kw)
        if pat is None:
            pat = re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
            _ASCII_WORD_RE_CACHE[kw] = pat
        if pat.search(name):
            return True
    return False


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

    **上一轮（2816197）的病根是裸子串匹配，这次要换判别机制，不是再加
    一条 if。** 裸子串分不清一个词在名字里是前缀还是后缀：「证券」在
    「证券时报」里是前缀（报纸拿它当报头用），在「中信证券」里是后缀
    （机构类型）；「semi」是「Semiconductor」的前缀，「idc」是
    「MidCap」的中段。逐个名字加排除只会一直漏，因为病根不在名单缺项，
    在匹配机制本身允许关键词命中在名字的任何位置。这里换成两条不同的
    判别方式，都不是裸子串：
      - 英文关键词按**词边界**匹配（`_has_ascii_keyword`），
        `\\bSEMI\\b` 不会命中 `Semiconductor` 或 `SemiAnalysis`；
      - 中文关键词按**结构**判别（前缀/后缀/是否为媒体自我标识），
        而不是看这个词出不出现。
    往后再发现漏判，先问「这次踩的是不是同一个裸子串陷阱」，而不是
    先加一条排除。
    """
    p = (publisher or "").strip()
    if not p:
        return Tier.MEDIA

    # 媒体标识优先级最高，见 _MEDIA_MARKERS 处注释：先把"以报/网/新闻
    # 自称"的名字挑出去，就不会被这些名字里可能带的"证券"前缀带偏。
    if any(marker in p for marker in _MEDIA_MARKERS):
        return Tier.MEDIA

    if any(k in p for k in _OFFICIAL_CN) or _has_ascii_keyword(p, _OFFICIAL_ASCII):
        return Tier.OFFICIAL

    # 卖方按结构判别：必须是"以证券/期货/资管收尾"这种机构类型后缀，
    # 或者"含证券字样、且以研究所/研究院/研究部收尾"。「研究所」单独
    # 不构成卖方——官方背景智库（中国社科院工业经济研究所）也常用这
    # 三个字收尾，不含"证券"就不该被判进卖方。
    if p.endswith(_SELLSIDE_SUFFIX):
        return Tier.SELLSIDE
    if "证券" in p and p.endswith(_SELLSIDE_RESEARCH_SUFFIX):
        return Tier.SELLSIDE
    if (any(k in p for k in _SELLSIDE_CN_EXPLICIT)
            or _has_ascii_keyword(p, _SELLSIDE_ASCII)):
        return Tier.SELLSIDE

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
