"""第③步判断五年、第④步预测十年、旧判断项文件明确停下、产物写明过渡期规则（2026-11-16）。不联网。"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent import dcf, full, opex_judg as OJ

AS_OF = date(2026, 11, 15)


def test_第四步默认十年():
    assert dcf.HORIZON == 10 and OJ.JUDGED_YEARS == 5
    for fn in (dcf.run, dcf.calibrate_only):
        assert inspect.signature(fn).parameters["years"].default == dcf.HORIZON


def test_第三步只判断前五年():
    cal = {"periods": {"proj": [f"FY{2026 + i}E" for i in range(10)]}}
    assert full._judged_labels(cal) == [f"FY{2026 + i}E" for i in range(5)]


def test_回读只比判断期():
    judg = {"growth": [0.1, 0.2, 0.3, 0.2, 0.1], "gm": [0.5] * 5}
    inp = {"growth": [0.1, 0.2, 0.3, 0.2, 0.1, 0.08, 0.06, 0.05, 0.04, 0.03], "gm": [0.5] * 5 + [0.45] * 5}
    assert dcf._readback_check(judg, inp) == []


def test_旧判断项文件没有费用率时第四步明确停下():
    from ir_agent.assume import AssumptionSet
    with pytest.raises(SystemExit, match="重跑第③步"):
        full._require_opex(AssumptionSet(code="PLTR", labels=["FY2026E"]), "out/PLTR/assumptions.json")
    a = AssumptionSet(code="PLTR", labels=["FY2026E"], market="us")
    with pytest.raises(SystemExit, match="费用率"):
        full._require_opex(a, "x")


def test_估值摘要写明过渡期规则_不含裸数字():
    from ir_agent.citation import audit_bare_numbers
    res = dcf.DcfResult(code="PLTR", workbook="/tmp/x.xlsx", price=D("100"), value_per_share=D("120"),
                        wacc=D("0.09"), terminal_roic=D("0.2"), reinvest_rate=D("0.1"),
                        terminal_share=D("0.6"), verified=True, as_of=AS_OF)
    line = next(x for x in res.summary().splitlines() if "过渡期" in x)
    assert "第一至五年" in line and "第六至十年" in line and audit_bare_numbers(line) == []


def test_产物文案不再说费用率由代码校准():
    import ir_agent.research as R
    assert "（费用率、周转天数" not in inspect.getsource(R)
    assert "（费用率、周转天数" not in inspect.getsource(dcf)
