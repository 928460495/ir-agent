"""终审修复（2026-11-17，十年预测期 + 费用率判断项）。不联网。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent import assume, assume_review as AR, dcf, opex_judg as OJ
from tests.test_assume_opex import US_TEXT

AS_OF = date(2026, 11, 15)


# ── C1：人签字的清单看得到费用率与稳态值 ──

def test_核实清单列出费用率逐年三档与稳态值():
    md = assume.parse(US_TEXT, 5, us=True).describe()
    assert "研发费用率" in md and "销售及管理费用率" in md
    assert "稳态毛利率" in md and "终值ROIC溢价" in md
    assert "14.00%" in md and "12.00%" in md and "10.00%" in md        # 研发费用率三档
    assert "营业利润率" in md


def test_下一步提示改数时也写费用率与稳态值():
    import inspect
    import ir_agent.research as R
    assert "opex_rows" in inspect.getsource(R) and "steady_rows" in inspect.getsource(R)


# ── I3a：FMP 报价日晚于分析时点视为不可用，退回 Tiingo ──

# ── I3b：同业只有当天行情、本司取分析时点收盘时，明确降级说明 ──

# ── I5：回读核对费用率与稳态值 ──

def _inp(rd5, sga5, st_gm=.45):
    return {"growth": [.1] * 10, "gm": [.5] * 10,
            "judged": {"growth": [.1] * 5, "gm": [.5] * 5, "rd": rd5, "sga": sga5},
            "steady": {"gm": st_gm, "rd": .06, "sga": .2}}


def _judg():
    return {"growth": [.1] * 5, "gm": [.5] * 5,
            "opex_scen": {"研发费用率": {"bull": [.09] * 5, "base": [.1] * 5, "bear": [.11] * 5},
                          "销售及管理费用率": {"bull": [.19] * 5, "base": [.2] * 5, "bear": [.21] * 5}},
            "steady": {"稳态毛利率": {"bull": .5, "base": .45, "bear": .4},
                       "稳态研发费用率": {"bull": .05, "base": .06, "bear": .07},
                       "稳态销售及管理费用率": {"bull": .15, "base": .2, "bear": .25}}}


def test_回读核对费用率与稳态值():
    assert dcf._readback_check(_judg(), _inp([.1] * 5, [.2] * 5), code="PLTR") == []
    bad = dcf._readback_check(_judg(), _inp([.08] * 5, [.2] * 5), code="PLTR")
    assert any("研发费用率" in x and "不可采信" in x for x in bad)
    bad = dcf._readback_check(_judg(), _inp([.1] * 5, [.2] * 5, st_gm=.3), code="PLTR")
    assert any("稳态毛利率" in x for x in bad)


# ── 8 / 9 / 10 / 14 ──

def test_整块没填的问题文字说第四步会停下():
    text = "\n".join(l for l in US_TEXT.splitlines() if "费用率" not in l and "稳态" not in l and "ROIC" not in l)
    _, problems = OJ.check(assume.parse(text, 5, us=True))
    assert any("第④步会停下" in p for p in problems) and not any("将按校准值建模" in p for p in problems)


def test_审核规则不写具体公司的数字():
    assert "PLTR" not in AR.RULES and "79.5%" not in AR.RULES and "31.6%" not in AR.RULES


def test_稳态项的依据逐项作答():
    items = AR.checklist(assume.parse(US_TEXT, 5, us=True))
    for it in OJ.steady_items("us"):
        assert ("依据", it) in items
    assert ("依据", "稳态值") not in items


def test_终值ROIC溢价为负是问题():
    text = US_TEXT.replace("终值ROIC溢价 | 稳态 | 1% | 3% | 5%", "终值ROIC溢价 | 稳态 | -1% | 3% | 5%")
    _, problems = OJ.check(assume.parse(text, 5, us=True))
    assert any("终值ROIC溢价" in p and "不应为负" in p for p in problems)
