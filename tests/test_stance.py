# -*- coding: utf-8 -*-
"""多空辩论 —— 两个子 agent 采集对立证据，裁决出带数值区间的平衡点。

**为什么不复用 debate.py。** 那边批判的是**已成稿的正文**（逻辑错误/无据断言/
反面证据被忽略），抓不住「这批证据被单向挑选过」。多空双方各自采证，才可能
暴露证据选择本身的偏向。debate.py 仍服务第二阶段的成稿辩论，不动。
"""
from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent.stance import (
    Balance,
    Evidence,
    StanceFailed,
    StanceResult,
    parse_balance,
    parse_evidence,
)


class TestParseEvidence:
    def test_管道分隔四字段(self):
        raw = "多方｜覆铜板销量｜AI 服务器需求放量支撑出货｜[[revenue.yoy@2025FY]]"
        evs = parse_evidence(raw)
        assert len(evs) == 1
        e = evs[0]
        assert e.side == "多方" and e.subject == "覆铜板销量"
        assert "AI 服务器" in e.claim
        assert e.bases and e.bases[0].ref == "[[revenue.yoy@2025FY]]"

    def test_两侧混在一起也能分开(self):
        raw = ("多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]\n"
               "空方｜销量｜周期下行｜[[industry.revenue_yoy@2025FY]]")
        evs = parse_evidence(raw)
        assert [e.side for e in evs] == ["多方", "空方"]

    def test_侧别写错直接报错(self):
        """只有「多方」「空方」两个合法值 —— 认不出来的侧别不能静默丢掉。"""
        with pytest.raises(StanceFailed, match="侧别"):
            parse_evidence("中立｜销量｜看法｜[[revenue.yoy@2025FY]]")

    def test_缺依据的那条报错并指名(self):
        """依据是这套机制的全部价值所在，缺了不能收。"""
        with pytest.raises(StanceFailed, match="销量"):
            parse_evidence("多方｜销量｜需求放量｜")

    def test_字段数不对报错(self):
        with pytest.raises(StanceFailed, match="四段"):
            parse_evidence("多方｜销量｜需求放量")

    def test_一条都没有报错(self):
        with pytest.raises(StanceFailed, match="一条"):
            parse_evidence("（无可用证据）")


class TestParseBalance:
    def test_带数值的三元组(self):
        b = parse_balance("平衡｜覆铜板销量｜-0.02｜0.08｜0.18｜中性取份额小幅提升")
        assert len(b) == 1
        assert b[0].subject == "覆铜板销量"
        assert (b[0].bear, b[0].mid, b[0].bull) == (D("-0.02"), D("0.08"), D("0.18"))
        assert "份额" in b[0].note

    def test_辩论一的纯文字形态用破折号占位(self):
        """辩论① 在预研之前，还没有因子，只能给文字结论。"""
        b = parse_balance("平衡｜需求侧｜—｜—｜—｜量增价稳")
        assert b[0].bear is None and b[0].mid is None and b[0].bull is None
        assert b[0].note == "量增价稳"

    def test_百分号写法也接受(self):
        b = parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由")
        assert b[0].mid == D("0.08")

    def test_数值写不出来就报错而不是当成文字(self):
        """「约 8%」这种模糊写法不能静默变成 None —— 那会让区间校验失效。"""
        with pytest.raises(StanceFailed, match="约 8%"):
            parse_balance("平衡｜销量｜-2%｜约 8%｜18%｜理由")

    def test_三个值要么全给要么全不给(self):
        with pytest.raises(StanceFailed, match="三档"):
            parse_balance("平衡｜销量｜-2%｜—｜18%｜理由")


class TestStanceResult:
    def _evs(self):
        return parse_evidence(
            "多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]\n"
            "空方｜销量｜周期下行｜[[industry.revenue_yoy@2025FY]]")

    def test_摘要分两侧列出并统计(self):
        r = StanceResult(round_="B", evidence=self._evs(),
                         balances=parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由"))
        s = r.summary()
        assert "多方 1 条" in s and "空方 1 条" in s and "销量" in s

    def test_人工补写要在摘要里留痕(self):
        r = StanceResult(round_="B", evidence=self._evs(), balances=[], manual="xhl")
        assert "人工补写" in r.summary() and "xhl" in r.summary()

    def test_json往返(self):
        r = StanceResult(round_="B", evidence=self._evs(),
                         balances=parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由"))
        back = StanceResult.from_json(r.to_json())
        assert back.round_ == "B"
        assert [e.claim for e in back.evidence] == [e.claim for e in r.evidence]
        assert back.balances[0].mid == D("0.08")
