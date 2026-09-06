"""流程接线: 宏观/行业事实必须在撰写之前入账本，且它们的期间要进目录。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.full import _macro_periods, _prior_period
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.research import assumption_template
from ir_agent.valuation.derive import derive


class _R:
    def __init__(self, ledger):
        self.ledger = ledger


def _led(*pairs):
    led = FactLedger()
    for key, per in pairs:
        led.put(Fact(key=key, value=Decimal("0.05"), unit="ratio", period=per,
                     as_of=date(2025, 1, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


# ── 上年同期 ─────────────────────────────────────────────────

def test_上年同期保留期间后缀():
    assert _prior_period("2025FY") == "2024FY"
    assert _prior_period("2025H1") == "2024H1"
    assert _prior_period("2025Q1-Q3") == "2024Q1-Q3"


def test_无法推出上年同期时返回空而非猜测():
    assert _prior_period("") is None
    assert _prior_period("abcd") is None


# ── 宏观期间进目录 ───────────────────────────────────────────

def test_宏观期间被纳入可引用范围():
    """不进目录，正文就引用不到宏观事实 —— 而提示词又要求写宏观一节，
    模型只能写裸数字然后被审计驳回。两边必须对齐。"""
    r = _R(_led(("macro.cpi_yoy", "2026-07"), ("macro.gdp_nominal_yoy", "2026H1"),
                ("revenue", "2025FY")))
    got = _macro_periods(r)
    assert "2026-07" in got and "2026H1" in got
    assert "2025FY" not in got          # 公司报告期由主流程单独传


def test_宏观期间按时间倒序且有上限():
    r = _R(_led(*[("macro.cpi_yoy", f"2026-{m:02d}") for m in range(1, 9)]))
    got = _macro_periods(r)
    assert len(got) <= 4
    assert got[0] == "2026-08"          # 最新在前
    assert list(got) == sorted(got, reverse=True)


def test_无法解析的期间被跳过而非崩溃():
    r = _R(_led(("macro.cpi_yoy", "垃圾期间"), ("macro.cpi_yoy", "2026-07")))
    assert _macro_periods(r) == ("2026-07",)


def test_无宏观事实时返回空():
    assert _macro_periods(_R(_led(("revenue", "2025FY")))) == ()


# ── 假设模板 ─────────────────────────────────────────────────

def test_模板不带推导时保持原样():
    t = assumption_template(5)
    assert "wacc: null" in t and "建议" not in t


def test_推导建议以注释形式出现不落到值上():
    """预填数字会让人直接点通过，人工审核门就退化成一个回车。"""
    led = _led(("revenue.yoy", "2025FY"), ("macro.gdp_nominal_yoy", "2026H1"))
    t = assumption_template(5, derived=derive(led, "2025FY", date(2026, 9, 7)))

    head = [ln for ln in t.split("\n") if "建议" in ln]
    assert head and all(ln.startswith("#") for ln in head)
    # 值本身仍是 null，必须人工填
    assert "wacc: null" in t and "terminal_growth: null" in t
    assert "rate: null" in t


def test_模板注释含永续上限与依据():
    led = _led(("revenue.yoy", "2025FY"), ("macro.gdp_nominal_yoy", "2026H1"))
    t = assumption_template(5, derived=derive(led, "2025FY", date(2026, 9, 7)))
    assert "永续增长率上限" in t
    assert "[[macro.gdp_nominal_yoy@2026H1]]" in t
    assert "仅供参考" in t
