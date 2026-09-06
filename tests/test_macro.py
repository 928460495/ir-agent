"""宏观适配器。重点在时点 —— 统计期不是发布日。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import FactLedger, LookAheadError, Method
from ir_agent.sources import macro


def _row(time, report_date, **cols):
    return {"TIME": time, "REPORT_DATE": f"{report_date} 00:00:00", **cols}


# ── 期间与披露日 ───────────────────────────────────────────────

def test_月度期间标签与保守披露日():
    period, as_of = macro._month_period("2026年07月份", date(2026, 7, 1))
    assert period == "2026-07"
    # 统计期末 7-31 起算 15 天 —— 晚于统计局实际发布的 8-09
    assert as_of == date(2026, 8, 15)


def test_月度披露日必须晚于统计局实际发布节奏():
    """保守方向只能是「更晚」。早一天可见就是未来函数。"""
    _, as_of = macro._month_period("2026年01月份", date(2026, 1, 1))
    实际发布 = date(2026, 2, 9)          # CPI 惯例为次月 9-10 日
    assert as_of > 实际发布


def test_季度累计标签映射到账本既有期间体系():
    assert macro._quarter_period("2026年第1季度", date(2026, 3, 1))[0] == "2026Q1"
    assert macro._quarter_period("2026年第1-2季度", date(2026, 6, 1))[0] == "2026H1"
    assert macro._quarter_period("2026年第1-3季度", date(2026, 9, 1))[0] == "2026Q1-Q3"
    assert macro._quarter_period("2026年第1-4季度", date(2026, 12, 1))[0] == "2026FY"


def test_季度披露日晚于季末():
    _, as_of = macro._quarter_period("2026年第1季度", date(2026, 3, 1))
    assert as_of == date(2026, 4, 30)


def test_无法解析的季度标签直接报错而非猜测():
    with pytest.raises(macro.MacroUnavailable):
        macro._quarter_period("2026年上半年", date(2026, 6, 1))


def test_跨年末月份的月末计算():
    period, as_of = macro._month_period("2025年12月份", date(2025, 12, 1))
    assert period == "2025-12"
    assert as_of == date(2026, 1, 15)


# ── GDP：名义 vs 实际 ─────────────────────────────────────────

def _gdp_rows():
    return [
        _row("2026年第1-2季度", "2026-06-01",
             DOMESTICL_PRODUCT_BASE=695704, SUM_SAME=4.7),
        _row("2025年第1-2季度", "2025-06-01",
             DOMESTICL_PRODUCT_BASE=660536, SUM_SAME=5.3),
    ]


def test_名义增速由总量自算而非取用实际增速(monkeypatch):
    monkeypatch.setattr(macro, "_get", lambda *a, **k: _gdp_rows())
    facts = {(f.key, f.period): f for f in macro.fetch_gdp()}

    实际 = facts[("macro.gdp_real_yoy", "2026H1")]
    名义 = facts[("macro.gdp_nominal_yoy", "2026H1")]

    assert 实际.value == Decimal("0.047")
    assert 实际.method is Method.REPORTED
    # 695704/660536-1 ≈ 5.32%，与实际增速 4.7% 相差一个平减指数
    assert Decimal("0.053") < 名义.value < Decimal("0.054")
    assert 名义.value != 实际.value
    assert 名义.method is Method.COMPUTED
    assert 名义.derived_from == ("macro.gdp_nominal@2026H1",
                                "macro.gdp_nominal@2025H1")


def test_同比缺少上年同期时不产出(monkeypatch):
    monkeypatch.setattr(macro, "_get", lambda *a, **k: _gdp_rows()[:1])
    keys = {f.key for f in macro.fetch_gdp()}
    assert "macro.gdp_nominal_yoy" not in keys
    assert "macro.gdp_nominal" in keys


def test_上年同期为零不触发除零(monkeypatch):
    rows = _gdp_rows()
    rows[1]["DOMESTICL_PRODUCT_BASE"] = 0
    monkeypatch.setattr(macro, "_get", lambda *a, **k: rows)
    assert "macro.gdp_nominal_yoy" not in {f.key for f in macro.fetch_gdp()}


def test_同比披露日取两期中较晚者(monkeypatch):
    monkeypatch.setattr(macro, "_get", lambda *a, **k: _gdp_rows())
    f = next(x for x in macro.fetch_gdp() if x.key == "macro.gdp_nominal_yoy")
    assert f.as_of == date(2026, 7, 30)


# ── 数值口径 ─────────────────────────────────────────────────

def test_百分数归一为_ratio(monkeypatch):
    monkeypatch.setattr(macro, "_get", lambda rep, size: (
        [_row("2026年07月份", "2026-07-01", NATIONAL_SAME=0.5)]
        if "CPI" in rep else [_row("2026年07月份", "2026-07-01", BASE_SAME=3.5)]))
    facts = {f.key: f for f in macro.fetch_cpi_ppi()}
    assert facts["macro.cpi_yoy"].value == Decimal("0.005")
    assert facts["macro.ppi_yoy"].value == Decimal("0.035")
    assert all(f.unit == "ratio" for f in facts.values())


def test_数值一律_Decimal(monkeypatch):
    monkeypatch.setattr(macro, "_get", lambda *a, **k: _gdp_rows())
    assert all(isinstance(f.value, Decimal) for f in macro.fetch_gdp())


# ── 与账本的时点守卫协同 ───────────────────────────────────────

def test_账本在披露日之前拒绝给出宏观事实(monkeypatch):
    """本模块存在的理由: 若用 REPORT_DATE 当 as_of，这条断言会失败。"""
    monkeypatch.setattr(macro, "_get", lambda rep, size:
                        [_row("2026年07月份", "2026-07-01", NATIONAL_SAME=0.5)]
                        if "CPI" in rep else [])
    led = FactLedger()
    for f in macro.fetch_cpi_ppi():
        led.put(f)

    with pytest.raises(LookAheadError):
        led.get("macro.cpi_yoy", "2026-07", as_of=date(2026, 8, 1))
    assert led.get("macro.cpi_yoy", "2026-07",
                   as_of=date(2026, 8, 20)).value == Decimal("0.005")


# ── 降级 ─────────────────────────────────────────────────────

def test_单项失败不拖垮其余(monkeypatch):
    def fake(rep, size):
        if "GDP" in rep:
            raise macro.MacroUnavailable("GDP 挂了")
        return [_row("2026年07月份", "2026-07-01",
                     NATIONAL_SAME=0.5, BASE_SAME=3.5)]

    monkeypatch.setattr(macro, "_get", fake)
    keys = {f.key for f in macro.fetch_macro()}
    assert keys == {"macro.cpi_yoy", "macro.ppi_yoy"}


def test_全部失败才抛错(monkeypatch):
    def boom(rep, size):
        raise macro.MacroUnavailable(f"{rep} 挂了")

    monkeypatch.setattr(macro, "_get", boom)
    with pytest.raises(macro.MacroUnavailable):
        macro.fetch_macro()


# ── 快照 ─────────────────────────────────────────────────────

def test_快照只看时点内可见的最新一期(monkeypatch):
    monkeypatch.setattr(macro, "_get", lambda rep, size: (
        [_row("2026年07月份", "2026-07-01", NATIONAL_SAME=0.5),
         _row("2026年06月份", "2026-06-01", NATIONAL_SAME=1.0)]
        if "CPI" in rep else []))
    facts = macro.fetch_cpi_ppi()

    早 = macro.MacroSnapshot(facts, as_of=date(2026, 8, 1))
    assert 早.latest("macro.cpi_yoy").period == "2026-06"

    晚 = macro.MacroSnapshot(facts, as_of=date(2026, 8, 20))
    assert 晚.latest("macro.cpi_yoy").period == "2026-07"

    空 = macro.MacroSnapshot(facts, as_of=date(2026, 1, 1))
    assert 空.latest("macro.cpi_yoy") is None
    assert "无可见宏观数据" in 空.describe()
