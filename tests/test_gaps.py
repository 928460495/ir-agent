"""缺口识别与分级采集。**采集不到就说采集不到，绝不用代理指标顶替。**"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.drivers import Decomposition, Factor, Op
from ir_agent.gaps import FactorData, collect, identify, summary
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.external import Corpus, Tier
from ir_agent.sources.web import Finding

D = Decimal
AS_OF = date(2026, 9, 7)


def _f(key, label, unit="变化率"):
    return Factor(key=key, label=label, unit=unit, source_hint="研报")


def _rev():
    return Decomposition(target="revenue", op=Op.MULTIPLY,
                         factors=[_f("capacity", "产能"),
                                  _f("utilization", "稼动率"),
                                  _f("asp", "单价")])


def _led(*keys):
    led = FactLedger()
    for k in keys:
        led.put(Fact(key=k, value=D("0.1"), unit="ratio", period="2025FY",
                     as_of=date(2026, 4, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


class _Client:
    def __init__(self, *r):
        self.replies, self.prompts = list(r), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0) if self.replies else ""


def test_识别账本里没有的因子():
    gaps = identify(_rev(), _led("capacity"), AS_OF)
    assert [f.key for f in gaps] == ["utilization", "asp"]


def test_账本齐全则无缺口():
    assert identify(_rev(), _led("capacity", "utilization", "asp"), AS_OF) == []


def test_采集不到标为无数据():
    """封测稼动率 A 股基本不披露，这是常态而非异常。"""
    data = collect(_Client("", "", ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["capacity"].status == "已披露"
    assert data["utilization"].status == "无数据"
    assert "估计" in data["utilization"].note


def test_采集到官方来源标为外部来源():
    ok = ("发现: utilization | 平均稼动率约78% | https://semi.org/x "
          "| SEMI | 2026-07-01\n")
    data = collect(_Client(ok, ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["utilization"].status == "外部来源"
    assert data["utilization"].findings[0].tier is Tier.OFFICIAL


def test_仅媒体来源仍算无数据但说明原因():
    """MEDIA 单源不足以支撑取值 —— 但要说清是「找到了但不够格」
    而不是「什么都没找到」。"""
    media = ("发现: utilization | 稼动率下滑 | https://a/b "
             "| 某财经网 | 2026-07-01\n")
    data = collect(_Client(media, ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["utilization"].status == "无数据"
    assert "单一媒体" in data["utilization"].note


def test_采集到的进语料库供核验():
    ok = ("发现: utilization | 平均稼动率约78% | https://semi.org/x "
          "| SEMI | 2026-07-01\n")
    corpus = Corpus(as_of=AS_OF)
    collect(_Client(ok, ""), [_rev()], _led("capacity"), corpus, "封测", AS_OF)
    assert any("78%" in d.text for d in corpus.docs)


def test_不可联网时不中断只标无数据():
    """联网不可用是能力边界，不是故障 —— 整轮研究不该因此停摆。"""
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = False
    try:
        data = collect(_Client(), [_rev()], _led("capacity"),
                       Corpus(as_of=AS_OF), "封测", AS_OF)
        assert data["utilization"].status == "无数据"
        assert "不能联网" in data["utilization"].note
    finally:
        W.WEB_CAPABLE = old


def test_摘要把无数据的因子挑出来():
    """**遗漏必须可见** —— 这正是分解的价值所在。"""
    data = collect(_Client("", "", ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    txt = summary(data)
    assert "稼动率" in txt and "无数据" in txt
    assert "绝不用代理指标" in txt
