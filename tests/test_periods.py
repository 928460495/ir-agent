"""期间排序。这个模块因一个实测 bug 而生 —— 字典序 ≠ 时间序。"""

from __future__ import annotations

import pytest

from ir_agent.periods import UnknownPeriod, latest, sort_key


def test_年内累计口径按截止月排序():
    got = sorted(["2026FY", "2026Q1", "2026Q1-Q3", "2026H1"], key=sort_key)
    assert got == ["2026Q1", "2026H1", "2026Q1-Q3", "2026FY"]


def test_半年报晚于一季报():
    """触发本模块诞生的原始 bug: 字典序下 '2026H1' < '2026Q1'。"""
    assert "2026H1" < "2026Q1"                      # 字典序是错的
    assert sort_key("2026H1") > sort_key("2026Q1")  # 时间序是对的


def test_跨年比较():
    assert sort_key("2025FY") < sort_key("2026Q1")
    assert latest(["2026Q1", "2025FY", "2024FY"]) == "2026Q1"


def test_单季与累计同截止月时累计在后():
    assert sort_key("2026Q3S") < sort_key("2026Q1-Q3")


def test_月度与即期标签可与会计期间同列比较():
    assert sort_key("2026-07") > sort_key("2026H1")
    assert sort_key("2026-09-07") > sort_key("2026-07")
    assert latest(["2026H1", "2026-07", "2026Q1"]) == "2026-07"


def test_无法解析的标签抛错而非猜测():
    with pytest.raises(UnknownPeriod):
        sort_key("上半年")


def test_latest_跳过无法解析的标签而非崩溃():
    assert latest(["垃圾", "2026Q1", "另一个垃圾"]) == "2026Q1"
    assert latest(["全是垃圾"]) is None
    assert latest([]) is None
