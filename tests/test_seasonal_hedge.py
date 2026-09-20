# -*- coding: utf-8 -*-
"""季节性对冲：最近年报 +（本期中期 − 去年同期中期）。

**为什么不能直接用 H1 实际值当全年。** 年末计提奖金、研发集中下半年，
H1 的费用率系统性偏低；而中期"×2 年化"会因收入非均匀分布而系统性抬高
周转天数（生益 DPO 126→164，ashare_calibrate.py 里早有注释说它不可用）。
同比差分把"今年比去年同期变了多少"这一份增量加到年报口径上，两种季节性
偏差各自抵消。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_calibrate as C


def test_同比差分把增量加到年报口径上():
    # 年报 4.00%；本期中期 3.41%，去年同期 3.00% → 同比多了 0.41pp
    got = C._yoy_hedge(0.04, 0.0341, 0.0300)
    assert abs(got - 0.0441) < 1e-9, got


def test_中期同比下降时对冲值低于年报():
    got = C._yoy_hedge(0.04, 0.0280, 0.0300)
    assert abs(got - 0.0380) < 1e-9, got


def test_缺去年同期时原样退回年报值():
    """**缺数据不能当成零变化。** 去年同期缺失时，`itm_now - None` 无从谈起，
    退回年报值是唯一诚实的选择——不是"同比没变"。"""
    assert C._yoy_hedge(0.04, 0.0341, None) == 0.04
    assert C._yoy_hedge(0.04, None, 0.0300) == 0.04


def test_年报值本身缺失时返回None():
    assert C._yoy_hedge(None, 0.0341, 0.0300) is None


def test_截断会记进CLAMPS():
    C._CLAMPS.clear()
    got = C._yoy_hedge(0.04, 0.30, 0.01, lo=0.0, hi=0.10, what="销售费用率")
    assert got == 0.10
    assert any("销售费用率" in x for x in C._CLAMPS), C._CLAMPS


def test_未触发截断时不记CLAMPS():
    C._CLAMPS.clear()
    C._yoy_hedge(0.04, 0.0341, 0.0300, lo=0.0, hi=0.10, what="销售费用率")
    assert C._CLAMPS == []
