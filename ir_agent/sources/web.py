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

# 来源分级。官方与协会 > 卖方 > 媒体。判不出来的一律按媒体处理 ——
# 往高了猜等于凭空给一个来源加权。
_OFFICIAL = ("SEMI", "统计局", "工信部", "发改委", "海关", "协会", "交易所",
             "Gartner", "TrendForce", "IDC")
_SELLSIDE = ("证券", "研究所", "期货", "investment", "securities")


class WebUnavailable(Exception):
    """联网能力不可用 —— 不静默返回空，那会让「搜不到」和「没搜」长得一样。"""


@dataclass(frozen=True)
class Finding:
    factor_key: str
    claim: str
    url: str
    publisher: str
    tier: Tier
    published: date | None


def tier_of(publisher: str) -> Tier:
    p = publisher or ""
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
        when = None
        if len(parts) > 4:
            try:
                when = date.fromisoformat(parts[4].strip()[:10])
            except ValueError:
                when = None
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
        raise WebUnavailable(
            "claude -p 不能联网（见 docs/superpowers/notes/"
            "2026-09-12-web-capability.md），开放网络采集不可用")
    raw = client(PROMPT.format(industry=industry, factor=factor_label,
                               key=factor_key, as_of=as_of.isoformat()))
    got = parse_findings(raw)
    return [f for f in got
            if f.published is None or f.published <= as_of]


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
    return False, ("仅单一媒体来源，不足以支撑因子取值 —— "
                   "需官方/协会或卖方研究佐证")
