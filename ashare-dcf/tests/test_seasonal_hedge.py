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


def test_费用率三项都走对冲且留下痕迹(monkeypatch):
    """sell/admin/rd 三项必须都走对冲，且 `_hedge_note` 要记下年报值与对冲值。

    **留痕是硬要求**：交付物里写着这些是「由历史校准」，读者有权知道
    校准用的到底是裸年报均值还是对冲后的值。
    """
    import ashare_calibrate as C
    note = {}
    # gen 必须绑定成变量再传入——如果直接写成内联字面量传给 gen= 参数，
    # 函数内部对 gen 的就地修改（或漏改）根本没有出口可供测试观察，
    # 「gen 三项真的被换成对冲值」这件事就无从断言。
    gen = {"sell": 0.02, "admin": 0.04, "rd": 0.03}
    C._apply_expense_hedge(
        gen=gen,
        itm_now={"sell": 0.0162, "admin": 0.0341, "rd": 0.0481},
        itm_prev={"sell": 0.0150, "admin": 0.0300, "rd": 0.0450},
        note=note)
    assert note["sell"]["base"] == 0.02
    assert abs(note["sell"]["hedged"] - 0.0212) < 1e-9
    assert abs(note["admin"]["hedged"] - 0.0441) < 1e-9
    assert abs(note["rd"]["hedged"] - 0.0331) < 1e-9
    # 只断言 note 留痕还不够——留痕真实性取决于 gen 三项是否真的被换成了
    # 对冲值。之前这里没断言 gen，用一份故意漏写 `gen[k] = hedged` 的对照
    # 实现去跑，四条 note 断言照样全绿：note 显示"已对冲"，gen 却还是原始
    # 年报均值，正是"交付物说一件没发生的事"。这里直接比对 gen 与 note 记
    # 录的对冲值，确保两者一致，才算真的验证了"留痕"与"实际生效"没有脱节。
    assert abs(gen["sell"] - note["sell"]["hedged"]) < 1e-9
    assert abs(gen["admin"] - note["admin"]["hedged"]) < 1e-9
    assert abs(gen["rd"] - note["rd"]["hedged"]) < 1e-9


def test_只有本期中期没有去年同期时退回年报值():
    """新上市公司的真实场景：有本期中报，但去年同期还没上市。

    守的是入口那道 `if not itm_now or not itm_prev: return`——若它被误写成
    `and`，`itm_prev.get(k)` 会在 `itm_prev is None` 时抛 AttributeError，
    而不是优雅地退回年报值。这类 and/or 打字失误在代码审查里最难看出来。
    """
    import ashare_calibrate as C
    note = {}
    gen = {"sell": 0.02, "admin": 0.04, "rd": 0.03}
    C._apply_expense_hedge(gen=gen, itm_now={"sell": 0.0162, "admin": 0.0341,
                                             "rd": 0.0481},
                           itm_prev=None, note=note)
    assert gen == {"sell": 0.02, "admin": 0.04, "rd": 0.03}, \
        "缺去年同期时必须原样退回年报值，不能当成同比零变化"
    assert note == {}


def test_中期缺失时费用率退回年报值且不留对冲痕迹():
    import ashare_calibrate as C
    note = {}
    gen = {"sell": 0.02, "admin": 0.04, "rd": 0.03}
    C._apply_expense_hedge(gen=gen, itm_now=None, itm_prev=None, note=note)
    assert gen == {"sell": 0.02, "admin": 0.04, "rd": 0.03}
    assert note == {}, "没对冲就不该留痕，否则读者会以为对冲过了"
