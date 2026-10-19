# -*- coding: utf-8 -*-
"""ashare_verify.sanity 的健全性警告。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_verify as V

# 其余各项都不触发的估值结果：终值占比 50%、明确期现值为正、ROIC > WACC。
DV = dict(pvtv=50.0, ev0=100.0, pv=50.0, rr=0.3, roic=0.12, wacc=0.08, eq=100.0)


def _ebit_warn(first, last):
    rows = [dict(rev=100.0, ebit=first), dict(rev=120.0, ebit=last)]
    return [x for x in V.sanity(DV, rows) if "EBIT 下滑" in x]


def test_首年EBIT为负_回升不算下滑():
    """牧原股份 2026-10-19：EBIT 从 −111.9 升到 153.5，却报「营收增长而 EBIT 下滑
    （−237%）」——首年为负时 末年÷首年−1 的正负号反了。"""
    assert _ebit_warn(-111.9, 153.5) == []


def test_真的下滑仍然报警_幅度按首年绝对值算():
    w = _ebit_warn(36.9, 21.4)                      # 均胜电子：−42%
    assert w and "−42%" in w[0].replace("-", "−")


def test_首年为负且继续恶化也报警():
    w = _ebit_warn(-10.0, -30.0)                    # 亏损扩大：−200%
    assert w and "−200%" in w[0].replace("-", "−")
