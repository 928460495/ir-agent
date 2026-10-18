# -*- coding: utf-8 -*-
"""首个预测年按季度估算（2026-10-18，取代「已披露 ÷ 历史同期占比」的三档锚定）。

**为什么改。** 牧原股份 2026 中报：旧规则用近 4 年上半年占全年比重的均值
（0.44）反推全年营收约 1346 亿元，而按三季度销售简报与四季度量价推算只有约
1185 亿元——高估 13.6%，且首年是后续年份复合的基数，高估一路传到估值。周期
股的上半年占比随价格周期大幅摆动（牧原近 4 年 0.355～0.530），历史均值不可靠。

**新规则（用户决定）。** 已公布的季度用实际；未公布的季度 = 去年同季 ×
（1 + 本年已公布季度的年初至今同比），营收与营业成本各用各的同比；首年三档
相同——它是实际 + 机械外推，不是判断，情景分化从第 2 年开始。
"""
import os, sys
import pandas as pd
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_calibrate as C

# 牧原股份（002714）单季，亿元。2025 四季全年；2026 已公布 Q1、Q2。
MY_PREV_REV = [360.6, 404.0, 353.3, 323.5]
MY_PREV_COGS = [289.2, 318.5, 300.8, 276.1]
MY_CUR_REV = [298.9, 295.2]
MY_CUR_COGS = [283.5, 320.0]


class TestSingleQuarters:
    def test_累计口径相减得单季(self):
        assert C.single_quarters([100, 250, 330, 500]) == [100, 150, 80, 170]

    def test_缺一个累计值_该季及之后都算不出(self):
        assert C.single_quarters([100, None, 330, 500]) == [100, None, None, None]

    def test_本年只到中报(self):
        assert C.single_quarters([100, 250, None, None]) == [100, 150, None, None]


class TestAnchorQuarterly:
    def test_未公布季度沿用年初至今同比_营收与成本各用各的(self):
        r = C.anchor_quarterly(MY_PREV_REV, MY_PREV_COGS, MY_CUR_REV, MY_CUR_COGS)
        g_rev = (298.9 + 295.2) / (360.6 + 404.0) - 1
        g_cogs = (283.5 + 320.0) / (289.2 + 318.5) - 1
        assert r["k"] == 2
        assert r["g_rev"] == pytest.approx(g_rev)
        assert r["g_cogs"] == pytest.approx(g_cogs)
        assert r["est_rev"] == pytest.approx([298.9, 295.2, 353.3 * (1 + g_rev), 323.5 * (1 + g_rev)])
        assert r["est_cogs"] == pytest.approx([283.5, 320.0, 300.8 * (1 + g_cogs), 276.1 * (1 + g_cogs)])
        assert r["fy_rev"] == pytest.approx(sum(r["est_rev"]))
        assert r["fy_cogs"] == pytest.approx(sum(r["est_cogs"]))

    def test_牧原首年约1120亿_毛利率约负5个点(self):
        r = C.anchor_quarterly(MY_PREV_REV, MY_PREV_COGS, MY_CUR_REV, MY_CUR_COGS)
        assert r["fy_rev"] == pytest.approx(1120, abs=1)
        assert 1 - r["fy_cogs"] / r["fy_rev"] == pytest.approx(-0.0503, abs=5e-4)

    def test_一季报也能估算(self):
        r = C.anchor_quarterly(MY_PREV_REV, MY_PREV_COGS, [298.9], [283.5])
        g = 298.9 / 360.6 - 1
        assert r["k"] == 1 and r["fy_rev"] == pytest.approx(298.9 + (404.0 + 353.3 + 323.5) * (1 + g))

    def test_三季报只外推第四季(self):
        r = C.anchor_quarterly(MY_PREV_REV, MY_PREV_COGS, [298.9, 295.2, 280.0], [283.5, 320.0, 300.0])
        g = (298.9 + 295.2 + 280.0) / (360.6 + 404.0 + 353.3) - 1
        assert r["k"] == 3 and r["est_rev"][3] == pytest.approx(323.5 * (1 + g))

    @pytest.mark.parametrize("prev_rev", [[360.6, None, 353.3, 323.5], [360.6, 404.0, 353.3, None]])
    def test_去年缺任一季度_不锚定(self, prev_rev):
        assert C.anchor_quarterly(prev_rev, MY_PREV_COGS, MY_CUR_REV, MY_CUR_COGS) is None

    def test_本年缺已公布季度_不锚定(self):
        assert C.anchor_quarterly(MY_PREV_REV, MY_PREV_COGS, [], []) is None
        assert C.anchor_quarterly(MY_PREV_REV, MY_PREV_COGS, [298.9, None], [283.5, 320.0]) is None

    def test_去年同期合计不为正_不锚定(self):
        assert C.anchor_quarterly([0, 0, 1, 1], MY_PREV_COGS, [5, 5], MY_CUR_COGS) is None


def _inc(rows: dict) -> pd.DataFrame:
    """利润表：索引为报告日 YYYYMMDD，列为科目，值以元计（与 fetch_statements 同形）。"""
    return pd.DataFrame.from_dict(rows, orient="index")


class TestInterimBlock:
    """calibrate 的中期块：由利润表的累计口径构造，写出季度明细与首年估算。"""

    def _inc_my(self):
        yi = 1e8
        cum = lambda q: [sum(q[:i + 1]) for i in range(len(q))]
        pr, pc = cum(MY_PREV_REV), cum(MY_PREV_COGS)
        cr, cc = cum(MY_CUR_REV), cum(MY_CUR_COGS)
        rows = {f"2025{e}": {"营业总收入": pr[i] * yi, "营业成本": pc[i] * yi}
                for i, e in enumerate(C.QUARTER_ENDS)}
        rows |= {f"2026{e}": {"营业总收入": cr[i] * yi, "营业成本": cc[i] * yi}
                 for i, e in enumerate(C.QUARTER_ENDS[:2])}
        return _inc(rows)

    def test_中期块含季度明细与首年估算(self):
        it = C.interim_block(self._inc_my(), "20260630")
        # 金额以亿元计（D.val 已换算），与 calibrate 其余各项同口径。
        assert it["period"] == "20260630" and it["label"] == "半年报" and it["k"] == 2
        assert it["rev"] == pytest.approx(298.9 + 295.2)
        assert it["cogs"] == pytest.approx(283.5 + 320.0)
        assert it["q_prev_rev"] == pytest.approx(MY_PREV_REV)
        assert it["q_cur_rev"] == pytest.approx(MY_CUR_REV)
        assert it["fy_rev"] == pytest.approx(1120, abs=1)
        # 已披露期占全年的隐含比重：中期列年化（比率表）仍按它的倒数年化。
        assert it["share"] == pytest.approx(it["rev"] / it["fy_rev"])
        assert "share3" not in it and "gm_rest3" not in it

    def test_缺去年同季_中期块为空(self):
        inc = self._inc_my().drop(index="20250930")
        assert C.interim_block(inc, "20260630") is None


class TestGuards:
    """终审（2026-10-19）：防护太薄时会静默锚定出荒谬的首年——去年某季为负（报表
    追溯重述后「全年 − 前三季」可能为负）、本年已公布季度为负、去年同季基数极小。
    参与计算的季度一律须为正；年初至今同比须在 -75%～+200% 之内（用户决定）。"""

    def test_去年某季为负_不锚定_写明哪一季(self):
        q, why = C.quarterly_estimate([10, -50, 60, 70], [5] * 4, [20], [6])
        assert q is None and "去年 Q2" in why and "不为正" in why

    def test_本年已公布季度为负_不锚定(self):
        q, why = C.quarterly_estimate([10, 20, 30, 40], [5] * 4, [-5], [6])
        assert q is None and "本年 Q1" in why

    def test_营收同比超出上限_不锚定(self):
        q, why = C.quarterly_estimate([0.1, 20, 30, 40], [5] * 4, [10], [6])
        assert q is None and "营收年初至今同比" in why and "+200%" in why

    def test_营业成本同比低于下限_不锚定(self):
        q, why = C.quarterly_estimate(MY_PREV_REV, [100, 300, 300, 300], [300.0], [20.0])
        assert q is None and "营业成本年初至今同比" in why and "-75%" in why

    @pytest.mark.parametrize("cur", [[29.0], [3.0]])        # +190%、-70%：边界之内
    def test_同比在范围内照常锚定(self, cur):
        q, why = C.quarterly_estimate([10, 20, 30, 40], [5] * 4, cur, [5.0])
        assert q is not None and why == ""

    def test_anchor_quarterly与带原因的版本一致(self):
        a = C.anchor_quarterly(MY_PREV_REV, MY_PREV_COGS, MY_CUR_REV, MY_CUR_COGS)
        b, why = C.quarterly_estimate(MY_PREV_REV, MY_PREV_COGS, MY_CUR_REV, MY_CUR_COGS)
        assert a == b and why == ""


class TestInterimBlockWhy:
    def test_缺去年同季_写明年份与季度(self):
        inc = TestInterimBlock()._inc_my().drop(index="20250930")
        it, why = C.interim_block_why(inc, "20260630")
        assert it is None and "2025" in why and "Q3" in why

    def test_正常时原因为空(self):
        it, why = C.interim_block_why(TestInterimBlock()._inc_my(), "20260630")
        assert it is not None and why == ""
