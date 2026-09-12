"""开放网络定向采集。**外部数据的证明力低一个量级，必须在代码里体现。**"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent.sources.external import Tier
from ir_agent.sources.web import (Finding, parse_findings, search,
                                  supports_value, tier_of)

AS_OF = date(2026, 9, 7)

OUT = """发现: utilization | 2026年上半年国内封测厂平均稼动率约78% | https://www.semi.org/x | SEMI | 2026-07-01
发现: utilization | 行业稼动率较去年同期下降5个百分点 | https://finance.example.com/y | 某财经网 | 2026-08-15
"""


class _Client:
    def __init__(self, *r):
        self.replies, self.prompts = list(r), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


def test_解析发现():
    got = parse_findings(OUT)
    assert len(got) == 2
    assert got[0].factor_key == "utilization" and "78%" in got[0].claim
    assert got[0].url.startswith("https://")
    assert got[0].published == date(2026, 7, 1)


def test_缺网址的发现被丢弃():
    """外部依据必须有 URL —— 没有网址就无从复核，与凭空捏造无异。"""
    assert parse_findings("发现: utilization | 稼动率很低 | 无 | 某处 | 2026-01-01") == []


def test_来源分级():
    assert tier_of("SEMI") is Tier.OFFICIAL
    assert tier_of("中国半导体行业协会") is Tier.OFFICIAL
    assert tier_of("中信证券") is Tier.SELLSIDE
    assert tier_of("某财经网") is Tier.MEDIA


def test_财经报纸不应被判为卖方研究():
    """发现一：子串匹配把"证券时报"这类报纸误判成卖方研究所，
    supports_value() 就会认为它能单独支撑因子取值 —— 直接绕过
    「MEDIA 单源不得单独支撑」这条本模块存在的唯一理由。这三家是
    A 股报道里最常被引用的报纸，不是边缘情形。"""
    assert tier_of("证券时报") is Tier.MEDIA
    assert tier_of("中国证券报") is Tier.MEDIA
    assert tier_of("证券日报") is Tier.MEDIA


def test_真正的卖方仍判为卖方研究():
    """修法不能矫枉过正 —— 真正的券商/期货公司必须仍然是 SELLSIDE，
    否则它们的行业转述会被错误地降级成不能单独支撑取值的 MEDIA。"""
    assert tier_of("中信证券") is Tier.SELLSIDE
    assert tier_of("国泰君安证券研究所") is Tier.SELLSIDE
    assert tier_of("某某期货") is Tier.SELLSIDE


def test_官方背景研究机构不被误判为卖方():
    """"研究所"这个词本身不代表卖方 —— 官方背景智库（如中国社科院下属
    研究所）更接近 OFFICIAL，至少不该被判成卖方研究。判不出来的一律按
    MEDIA 处理（往高了猜等于凭空给来源加权），这条规则保持不变。"""
    assert tier_of("中国社科院工业经济研究所") is not Tier.SELLSIDE


def test_无日期的发现被丢弃():
    """发现二：无日期的发现如果被放行成 published=None，编排层将来会用
    `published=x.published or as_of` 把它装配成"发布日期恰好等于 as_of"
    ——这是唯一必然不会触发 OutOfWindow 的取值，等于给最不可信的证据
    最宽松的时点待遇。提示词里"必须给出网址"与"必须给出发布日期"是并列
    的硬性要求，两者应对称处理：日期缺失就该像网址缺失一样直接丢弃。"""
    got = parse_findings("发现: utilization | 稼动率很低 | https://a/b | 某处")
    assert got == []


def test_日期解析失败的发现被丢弃():
    """日期字段存在但解析不出来（既非 ISO 也非本模块认得的其它写法），
    同样不能放行成 None，必须整条丢弃。"""
    got = parse_findings(
        "发现: utilization | 稼动率很低 | https://a/b | 某处 | 说不清哪天")
    assert got == []


def test_日期兼容常见写法():
    """发现三：只认 ISO 会让模型写"2026年7月1日"这类常见写法时被误杀。
    配合发现二"解析失败即丢弃"的修法，容错太差会连累大量本来合格的发现。"""
    text = "\n".join([
        "发现: a | 说法一 | https://x/1 | 某处 | 2026-07-01",
        "发现: b | 说法二 | https://x/2 | 某处 | 2026/07/01",
        "发现: c | 说法三 | https://x/3 | 某处 | 2026年7月1日",
        "发现: d | 说法四 | https://x/4 | 某处 | 2026.07.01",
    ])
    got = parse_findings(text)
    assert len(got) == 4
    assert all(f.published == date(2026, 7, 1) for f in got)


def test_全为媒体来源不问数量都不足以支撑取值():
    """发现四：三条发现分别来自三家不同的财经媒体，仍然全是 MEDIA 级，
    不该通过 —— 门槛是来源等级，不是数量。措辞里若写"单一媒体来源"，
    会误导读者以为多找几家媒体互相印证就能过关，而真正的门槛是来源
    等级，不是数量。"""
    media = [
        Finding("utilization", "稼动率下降", "https://a/1", "第一财经",
                Tier.MEDIA, date(2026, 1, 1)),
        Finding("utilization", "稼动率下降", "https://a/2", "界面新闻",
                Tier.MEDIA, date(2026, 1, 2)),
        Finding("utilization", "稼动率下降", "https://a/3", "澎湃新闻",
                Tier.MEDIA, date(2026, 1, 3)),
    ]
    ok, why = supports_value(media)
    assert ok is False
    assert "单一" not in why
    assert ("等级" in why or "数量" in why)


def test_单一媒体来源不足以支撑取值():
    """现有分级管的是「能不能引用」；因子取值是要进模型算钱的，门槛更高。
    一条财经媒体报道可以作为线索，不该单独决定 DCF 里的产能增速。"""
    media = [Finding("utilization", "稼动率下降", "https://a/b", "某财经网",
                     Tier.MEDIA, date(2026, 1, 1))]
    ok, why = supports_value(media)
    assert ok is False and "媒体" in why and "单一" not in why


def test_有官方或卖方佐证则可支撑():
    mixed = [Finding("utilization", "约78%", "https://semi/x", "SEMI",
                     Tier.OFFICIAL, date(2026, 1, 1)),
             Finding("utilization", "下降5pp", "https://a/b", "某财经网",
                     Tier.MEDIA, date(2026, 1, 2))]
    ok, _ = supports_value(mixed)
    assert ok is True


def test_无发现时明确说无():
    ok, why = supports_value([])
    assert ok is False and "未找到" in why


def test_不可联网时抛出而非静默返回空():
    """静默返回空会让「搜不到」和「没搜」长得一样。

    发现五：异常信息不应硬编码"claude -p 不能联网"——探测的是默认行为，
    不是能力上限。措辞该指向真正该查的地方（谁关了 WEB_CAPABLE 这个
    开关），而不是把排查者引向"怀疑网络能力"这个错误方向。
    """
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = False
    try:
        with pytest.raises(W.WebUnavailable, match="WEB_CAPABLE"):
            search(_Client(OUT), "稼动率", "utilization", "封测", AS_OF)
    finally:
        W.WEB_CAPABLE = old


def test_提示词要求给出网址与日期():
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = True
    try:
        c = _Client(OUT)
        got = search(c, "稼动率", "utilization", "集成电路封测", AS_OF)
        assert len(got) == 2
        assert "网址" in c.prompts[0] and "发布日期" in c.prompts[0]
        assert "集成电路封测" in c.prompts[0] and "稼动率" in c.prompts[0]
    finally:
        W.WEB_CAPABLE = old


def test_晚于as_of的发现被剔除():
    """拿未来的资料分析过去 —— 与账本的时点守卫是同一件事。"""
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = True
    try:
        late = ("发现: utilization | x | https://a/b | SEMI | 2026-12-01\n"
                "发现: utilization | y | https://a/c | SEMI | 2026-01-01\n")
        got = search(_Client(late), "稼动率", "utilization", "封测", AS_OF)
        assert len(got) == 1 and got[0].claim == "y"
    finally:
        W.WEB_CAPABLE = old
