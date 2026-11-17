"""首年已有季报时，门③ 首年营收增速 / 毛利率直接取锚定值（A 股 + 美股，spec 2026-11-17-year1-anchor）。

生益 600183：门③ 批准首年 27.66% / 27.23%，第④步被锚定覆盖为 50.05% / 31.27%，清单、终端、研报按批准值讲，
模型按锚定值算。不联网。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent import assume, dcf
from ir_agent.ledger import FactLedger
from tests.test_assume import _anchor_600183, _cal_600183, _two_year_set

AS_OF = date(2026, 10, 21)
G1, M1 = dcf.anchored_year1(fy_rev=426.596379280956, fy_cogs=293.2044922458345, base_rev=284.3113845944)


def _checked(anchor=_anchor_600183(), **kw):
    a, ds = _two_year_set(**kw)
    facts = assume.check(a, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, decomps=ds,
                         base_margin=D("0.2647"), anchor=anchor)
    return a, facts


# ── Task 2 ──

def test_锚定值换算只有一份():
    g, m = assume.anchor_y1_of(_anchor_600183())
    assert (g, m) == (D(str(round(G1, 6))), D(str(round(M1, 6))))
    assert assume.anchor_y1_of(None) is None


def test_synthesize首年取锚定值_三档相同():
    a, _ = _checked()
    for c in assume.Case:
        g, m = assume.synthesize(a, c, D("0.2647"), a.margin_bases)
        assert (g[0], m[0]) == a.anchor_y1
    assert f"{a.anchor_y1[0]:.2%}" == "50.05%" and f"{a.anchor_y1[1]:.2%}" == "31.27%"


def test_无锚定时首年照合成():
    a, _ = _checked(anchor=None)
    assert a.anchor_y1 is None
    g, m = assume.synthesize(a, assume.Case.BASE, D("0.2647"), None)
    assert float(m[0]) == pytest.approx(0.2647 + 0.007, abs=1e-9)


def test_锚定首年三档相同不算不单调():
    _, facts = _checked()
    assert "第 1 年的营收增速合成后不单调" not in facts.text()
    assert "第 1 年的毛利率合成后不单调" not in facts.text()


def test_apply_to写入锚定值_不再报批准与实际不符():
    a, _ = _two_year_set()
    out = assume.apply_to(_cal_600183(), a)
    for c in ("bull", "base", "bear"):
        assert out["judg"]["growth_scen"][c][0] == pytest.approx(G1, abs=1e-6)
        assert out["judg"]["gm_scen"][c][0] == pytest.approx(M1, abs=1e-6)
    inp = {"it": {"sw": 1, "fy_rev": 426.596379280956, "fy_cogs": 293.2044922458345},
           "o": {"基期营业总收入": 284.3113845944}}
    assert dcf._effective_first_year(inp) is not None
    assert dcf._anchor_note(out["judg"], inp) == ""


def test_旧JSON读回无anchor_y1不报错():
    a, _ = _two_year_set()
    b, edits = assume.from_json(assume.to_json(a))
    assert b.anchor_y1 is None and edits == []
