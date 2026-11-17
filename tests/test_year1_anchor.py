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


# ── Task 3 ──

def _scalar(y1_growth="0.10", with_y1=True):
    rows = []
    for y in (1, 2):
        if y == 1 and not with_y1:
            continue
        g = D(y1_growth) if y == 1 else D("0.08")
        rows.append(assume.Row(driver="growth", year=y, bear=g - D("0.02"), base=g, bull=g + D("0.02"), note="理由"))
        rows.append(assume.Row(driver="gm", year=y, bear=D("0.25"), base=D("0.27"), bull=D("0.29"), note="理由"))
    return assume.AssumptionSet(code="600183", labels=["2026E", "2027E"], rows=rows)


def _check_scalar_set(a, anchor=_anchor_600183()):
    return assume.check(a, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, anchor=anchor)


def test_两标量首年由代码写入锚定值():
    a = _scalar()
    facts = _check_scalar_set(a)
    for drv, v in (("growth", a.anchor_y1[0]), ("gm", a.anchor_y1[1])):
        r = [r for r in a.rows if r.driver == drv and r.year == 1][0]
        assert (r.bear, r.base, r.bull) == (v, v, v) and r.note.startswith("锚定")
    assert any("锚定" in n and "10" in n for n in a.read_notes)
    assert "第 1 年的营收增速三档未拉开" not in facts.text()
    b, edits = assume.from_json(assume.to_json(a))
    assert edits == []                                           # 代码写入不算人工修改


def test_两标量首年未填也不算缺年份():
    a = _scalar(with_y1=False)
    facts = _check_scalar_set(a)
    assert not facts.blocking
    assert [r.year for r in a.rows if r.driver == "growth"] == [1, 2]


def test_两标量无锚定时首年照AI判断():
    a = _scalar()
    _check_scalar_set(a, anchor=None)
    assert [r for r in a.rows if r.driver == "growth" and r.year == 1][0].base == D("0.10")


def _explaining_set(extra_pp=0.0):
    """首年拆解恰好解释锚定值（± extra_pp 个百分点的毛利率）。"""
    a, ds = _two_year_set()
    cap = D(str(round((1 + G1) / 1.01 - 1, 6)))
    spread = D(str(round((M1 - 0.2647) * 100 - 0.5 + extra_pp, 6)))
    for r in a.rows:
        if r.year != 1:
            continue
        v = {"cap": cap, "asp": D("0.01"), "spread": spread, "util": D("0.5")}[r.factor_key]
        r.bear = r.base = r.bull = v
    return a, ds


def test_分解首年拆解解释不了锚定值记问题_不阻断():
    _, facts = _checked()                                # 固件首年合成 6.05%，锚定 50.05%
    assert not facts.blocking
    assert "拆解解释不了锚定值" in facts.text()


def test_分解首年拆解在半个百分点内不记问题():
    for extra, hit in ((0.3, False), (1.0, True)):
        a, ds = _explaining_set(extra)
        facts = assume.check(a, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, decomps=ds,
                             base_margin=D("0.2647"), anchor=_anchor_600183())
        assert ("拆解解释不了锚定值" in facts.text()) is hit, (extra, facts.text())


# ── Task 4 ──

def test_方法论预研提示词写明首年锚定规则与方法():
    from ir_agent import methodology
    p = methodology.build_prompt("电子-元件-印制电路板", "600183")
    for k in ("首年锚定", "已公布季度用实际", "年初至今同比", "三档相同", "拆解"):
        assert k in p, k


def _judg():
    return {"g_stats": (0.1, 0.05, 0.0, 0.2), "gm_stats": (0.25, 0.01, 0.24, 0.27), "gm_hist": [0.2647], "cagr3": 0.1}


def test_判断项提示词给出本公司锚定结果():
    _, ds = _two_year_set()
    p = assume.build_prompt(_judg(), ["2026E", "2027E"], "ctx", decomps=ds, anchor=_anchor_600183())
    assert "50.05%" in p and "31.27%" in p and "解释锚定值" in p and "Q1–Q2" in p
    p2 = assume.build_prompt(_judg(), ["2026E", "2027E"], "ctx", decomps=None, anchor=_anchor_600183())
    assert "不用填" in p2
    p0 = assume.build_prompt(_judg(), ["2026E", "2027E"], "ctx", decomps=ds, anchor=None)
    assert "本公司首年锚定结果" not in p0


def test_审核者拿到的填写提示词带锚定():
    import inspect
    from ir_agent import assume_review
    src = inspect.getsource(assume_review.fill_and_review)
    assert "anchor=anchor" in src[src.index("assume.build_prompt("):]


def test_清单首年标锚定():
    a, _ = _checked()
    a.base_margin = D("0.2647")
    md = a.describe()
    assert "2026E（锚定）" in md
    a0, _ = _checked(anchor=None)
    a0.base_margin = D("0.2647")
    assert "（锚定）" not in a0.describe()


def test_审核规则说首年取锚定值():
    from ir_agent import assume_review
    assert "第 1 年取锚定值" in assume_review.RULES and "解释得了锚定值" in assume_review.RULES


def test_清单横幅说首年取锚定值():
    from ir_agent import full
    b = full._anchor_banner(_cal_600183(), ["2026E", "2027E"])
    assert "取锚定值" in b and "不进模型" not in b
