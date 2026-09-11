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


def test_单一媒体来源不足以支撑取值():
    """现有分级管的是「能不能引用」；因子取值是要进模型算钱的，门槛更高。
    一条财经媒体报道可以作为线索，不该单独决定 DCF 里的产能增速。"""
    media = [Finding("utilization", "稼动率下降", "https://a/b", "某财经网",
                     Tier.MEDIA, None)]
    ok, why = supports_value(media)
    assert ok is False and "单一媒体" in why


def test_有官方或卖方佐证则可支撑():
    mixed = [Finding("utilization", "约78%", "https://semi/x", "SEMI",
                     Tier.OFFICIAL, None),
             Finding("utilization", "下降5pp", "https://a/b", "某财经网",
                     Tier.MEDIA, None)]
    ok, _ = supports_value(mixed)
    assert ok is True


def test_无发现时明确说无():
    ok, why = supports_value([])
    assert ok is False and "未找到" in why


def test_不可联网时抛出而非静默返回空():
    """静默返回空会让「搜不到」和「没搜」长得一样。"""
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = False
    try:
        with pytest.raises(W.WebUnavailable, match="不能联网"):
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
