# -*- coding: utf-8 -*-
"""行情串解析与「现价 × 总股本 = 总市值」对账。

**实测事故（生益科技 600183，第二阶段首次跑通）。** 东财 push2 被本机代理
挡住，取数降级到腾讯通道，而那条分支把 `p[38]` 当成总股本读走：

    p[3]  = 129.96        现价
    p[38] = 1.47          **换手率（%）**，被读成总股本后 /1e4
    p[45] = 3156.73       总市值（亿元），被又除了一次 1e4
    p[73] = 2429003670    **真正的总股本（股）**

于是总股本从 24.29 亿股变成 0.000147 亿股，每股内在价值算出 25,077,036 元
（现价 129.96），上行空间 +19,295,865%。而下游所有闸门一个都没拦住它：
三表勾稽通过（股本不参与资产=负债+权益）、140 格独立复算通过（复算的是
同一套公式，自洽不等于正确）、健全性检查只报了「终值占 EV 94%」。

**所有校验都在模型内部查一致性，没有一个拿模型外的事实去校准它。** 而那个
外部事实就在同一条响应里：3156.73 ÷ 129.96 = 24.29 亿股。这就是本文件
第二组测试要钉住的对账。
"""
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_data as D

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "fixtures", "tencent_quote_600183.txt")


def raw():
    with open(FIX, encoding="utf-8") as f:
        return f.read()


class TestTencentFields:
    def test_总股本取的是股数字段而不是换手率(self):
        """24.29 亿股。读成 1.47/1e4 时每股内在价值会差 16 万倍。"""
        assert D._parse_tencent(raw())["shares"] == pytest.approx(24.29, abs=0.01)

    def test_总市值单位是亿元不再多除一次(self):
        assert D._parse_tencent(raw())["mktcap"] == pytest.approx(3156.73, rel=1e-4)

    def test_现价与名称(self):
        d = D._parse_tencent(raw())
        assert d["price"] == pytest.approx(129.96)
        assert d["name"] == "生益科技"

    def test_市盈率市净率没被下标带偏(self):
        d = D._parse_tencent(raw())
        assert 1 < d["pe"] < 500 and 0 < d["pb"] < 100


class TestQuoteCrossCheck:
    """现价 × 总股本 必须对得上同一条响应里的总市值。

    这条对账是免费的——两个数来自同一次请求，而它们的乘积关系是会计
    恒等式，不是经验规律。下标错位、单位错位、字段漂移都会被它抓住。
    """
    def test_真实响应对得上账(self):
        assert D._check_quote(D._parse_tencent(raw()))["shares"] > 0

    def test_股本错了一个数量级就报错(self):
        d = dict(source="腾讯", name="x", price=129.96, shares=24.29 / 1e4,
                 mktcap=3156.73, pe=60.0, pb=17.0)
        with pytest.raises(D.DataError, match="总市值"):
            D._check_quote(d)

    def test_市值单位错了同样报错(self):
        d = dict(source="腾讯", name="x", price=129.96, shares=24.29,
                 mktcap=3156.73 / 1e4, pe=60.0, pb=17.0)
        with pytest.raises(D.DataError):
            D._check_quote(d)

    def test_缺数不算矛盾(self):
        """NaN 不是「对不上」，而是「没这个数」——它会被工作簿的 NaN 检测
        吼出来，比在这里当成矛盾更准确。"""
        d = dict(source="新浪", name="x", price=129.96, shares=np.nan,
                 mktcap=np.nan, pe=np.nan, pb=np.nan)
        assert np.isnan(D._check_quote(d)["shares"])

    def test_微小差异允许通过(self):
        """行情源的总市值可能按另一个时点的价格算，0.5% 之内不该报错。"""
        d = dict(source="腾讯", name="x", price=129.96, shares=24.29,
                 mktcap=129.96 * 24.29 * 1.005, pe=60.0, pb=17.0)
        assert D._check_quote(d)["shares"] == 24.29
