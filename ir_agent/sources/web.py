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
# SEMI / Gartner / TrendForce / IDC 严格说是付费市场研究商，不是本 docstring
# （见 tier_of 下方 TIER_LABEL 的官方定义）里说的统计局/协会/部委/交易所。
# 放进 OFFICIAL 是有意的：封测的产能、出货这类数据，这几家就是该领域证明力
# 最强的一手来源，官方统计反而覆盖不到、或滞后到失去时效。分级看的是
# 「这句话的证明力有多强」，不是「这个机构算不算官方机构」。
_OFFICIAL = ("SEMI", "统计局", "工信部", "发改委", "海关", "协会", "交易所",
             "Gartner", "TrendForce", "IDC")
_SELLSIDE = ("证券", "期货", "investment", "securities")

# 中文财经报纸几乎都以"报"收尾（证券时报/中国证券报/证券日报/经济日报/
# ……），但报纸的角色是新闻报道，不是出具研究的卖方机构 —— 即便报纸名字
# 本身含有"证券"这样的字样。用命名习惯而不是逐一列举报纸名字来排除，
# 是因为逐一列举永远补不完，而"以报纸自称"这件事本身就说明它是媒体。
# 命中这条规则的名字直接判 MEDIA，不再往上试 OFFICIAL/SELLSIDE 关键词。
_NEWSPAPER_SUFFIX = "报"


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
    p = (publisher or "").strip()
    if p.endswith(_NEWSPAPER_SUFFIX):
        return Tier.MEDIA
    if any(k.lower() in p.lower() for k in _OFFICIAL):
        return Tier.OFFICIAL
    if any(k.lower() in p.lower() for k in _SELLSIDE):
        return Tier.SELLSIDE
    return Tier.MEDIA


def parse_findings(text: str) -> list[Finding]:
    out: list[Finding] = []
    for hit in _LINE.finditer(text):
        parts = [p.strip() for p in hit.group(1).split("|")]
        if len(parts) < 4:
            continue
        url_m = _URL.search(parts[2])
        if not url_m:
            # 外部依据必须有 URL —— 没有网址就无从复核，与凭空捏造无异。
            continue
        pub = parts[3]
        # 「必须给出网址」与「必须给出发布日期」在提示词里是并列的硬性
        # 要求，此处也应对称处理：日期字段缺失（没写第 5 段）或解析不出
        # 来（写了但认不出/不是合法日期），一律整条丢弃，绝不放行成
        # published=None。原因见 Finding.published 字段上的注释——
        # 一旦允许 None 流出本模块，编排层的兜底逻辑会把它悄悄变成
        # 「发布日期=as_of」，让最不可信的证据获得最宽松的时点待遇，
        # 静静滑过系统里最关键的一道防线（时点守卫）。这个隐患必须在
        # 产生 Finding 的这一步就堵死，不能指望下游每个调用点都记得防。
        if len(parts) < 5:
            continue
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
- **必须给出发布日期**。晚于 {as_of} 的会被剔除：拿未来的资料分析过去，
  与账本的时点守卫是同一件事。
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
