"""十年预测期、费用率逐年判断、过渡期向稳态收敛、终值 ROIC 收敛与终值 Beta（spec 2026-11-16）。不联网。

A 股与美股同一套规则；A 股终值此前没有 ROIC 收敛开关（终值 ROIC 一律沿用末年），本次一并补上。"""

from __future__ import annotations

import copy
import os
import pickle
import sys

import pytest
from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import ashare_build as B  # noqa: E402
import ashare_verify as V  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "cal_600183.pkl")


def load_cal():
    with open(FIX, "rb") as f:
        return pickle.load(f)


def cal10(**judg):
    cal = copy.deepcopy(load_cal())
    y0 = int(cal["periods"]["proj"][0][:4])
    cal["periods"]["proj"] = [f"{y0 + i}E" for i in range(10)]
    cal["judg"].update(judg)
    return cal


def _build(tmp, cal, name="t.xlsx"):
    p = os.path.join(str(tmp), name)
    B.build(cal, p)
    return p


def _rows(ws):
    return {ws.cell(r, 1).value: r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value}


OPEX = {k: {"bull": [v - .005] * 5, "base": [v] * 5, "bear": [v + .005] * 5}
        for k, v in (("销售费用率", .02), ("管理费用率", .04), ("研发费用率", .05))}
STEADY = {"稳态毛利率": {"bull": .24, "base": .22, "bear": .20},
          "稳态销售费用率": {"bull": .015, "base": .02, "bear": .025},
          "稳态管理费用率": {"bull": .035, "base": .04, "bear": .045},
          "稳态研发费用率": {"bull": .045, "base": .05, "bear": .055},
          "终值ROIC溢价": {"bull": .05, "base": .03, "bear": .01}}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("h"), cal10(opex_scen=OPEX, steady=STEADY))


def test_十年列且第6到10年为公式(built):
    wb = load_workbook(built)
    hdr = [h for h in (wb["2_利润表"].cell(5, c).value for c in range(2, 40)) if h]
    proj = [h for h in hdr if str(h).endswith("E")]
    assert len(proj) == 10
    a = wb["1_假设"]; m = _rows(a)
    for k in ("营收增速", "毛利率", "销售费用率", "管理费用率", "研发费用率"):
        assert not str(a.cell(m[f"{proj[4]} {k}"], 3).value).startswith("=")
        for y in proj[5:]:
            assert str(a.cell(m[f"{y} {k}"], 3).value).startswith("="), (y, k)


def test_写入费用率判断项三档(built):
    a = load_workbook(built)["1_假设"]; m = _rows(a)
    y1 = [k for k in m if isinstance(k, str) and k[:4].isdigit() and k.endswith(" 管理费用率")][0]
    assert [a.cell(m[y1], c).value for c in (2, 3, 4)] == [0.035, 0.04, 0.045]


def test_过渡期展开与美股同一规则(built):
    inp = V.read_inputs(built)
    full = V.expand_paths(inp)
    assert len(full["growth"]) == 10 and abs(full["growth"][9] - inp["dcf"]["永续增长率 g"]) < 1e-12
    assert abs(full["sell"][9] - 0.02) < 1e-12 and abs(full["gm"][9] - 0.22) < 1e-12


def test_终值ROIC收敛与终值Beta(built):
    inp = V.read_inputs(built)
    D = inp["dcf"]
    assert D["终值 Beta（向 1 收敛）"] == 1.0 and D["终值 ROIC 处理"] == 2
    _r, dv = V.recompute(inp)
    t = inp["gen"]["实际所得税率"]
    wt = ((1 - D["债务权重 D/(D+E)"]) * (D["无风险利率 Rf"] + 1.0 * D["股权风险溢价 ERP"])
          + D["债务权重 D/(D+E)"] * D["债务成本 Kd"] * (1 - t))
    assert abs(dv["wacc_term"] - wt) < 1e-12 and abs(dv["roic"] - (wt + 0.03)) < 1e-12


def test_十年结构三档验算通过(built):
    fails = V.verify(built, quiet=True)
    fails = fails[0] if isinstance(fails, tuple) else fails
    assert fails == []
    assert V.verify_all_scenarios(built, quiet=True) == []


def test_无判断项时按校准值建表仍可验算(tmp_path):
    fails = V.verify(_build(tmp_path, cal10()), quiet=True)
    fails = fails[0] if isinstance(fails, tuple) else fails
    assert fails == []


def test_生益固件新旧每股价值变化在合理带内(tmp_path):
    old_cal = copy.deepcopy(load_cal()); old_cal["dcf"]["roic_mode"] = 1; old_cal["dcf"]["beta_term"] = None
    _, old = V.recompute(V.read_inputs(_build(tmp_path, old_cal, "old.xlsx")))
    _, new = V.recompute(V.read_inputs(_build(tmp_path, cal10(), "new.xlsx")))
    assert 0.5 < new["ps"] / old["ps"] < 2.0, (old["ps"], new["ps"])


def test_首年锚定在十年结构下仍只作用于第1年(built):
    inp = V.read_inputs(built)
    rows, _ = V.recompute(inp)
    assert len(rows) == 10
    if inp.get("it") and inp["it"]["sw"] == 1:
        assert abs(rows[0]["rev"] - inp["it"]["fy_rev"]) < 1e-6


def test_蒙特卡洛零波动复现确定性结果(built):
    import ashare_mc as MC
    inp = V.read_inputs(built)
    P = MC.defaults({})
    for k in list(P):
        if str(k).startswith("sd_"):
            P[k] = 0.0
    P["n_paths"] = 10
    out = MC.simulate(inp, P)
    _, dv = V.recompute(inp)
    assert abs(float(out["ps"].mean()) - dv["ps"]) < 1e-6 * max(1.0, abs(dv["ps"]))


def test_全局营收增速Δ不为零时引擎与主模型一致(tmp_path):
    """终审 I2：引擎变动 g 的模块里，过渡期从「第 5 年生效值 + 本行 Δ」收敛到本行 g，Δ 在第 10 年消失；
    主模型 Δ 加在每一年 —— 中心格对不上，验算失败。"""
    p = _build(tmp_path, cal10(opex_scen=OPEX, steady=STEADY), "d.xlsx")
    wb = load_workbook(p); a = wb["1_假设"]; m = _rows(a)
    k = next(x for x in m if isinstance(x, str) and x.startswith("营收增速 Δ"))
    a.cell(m[k], 2).value = 0.01
    wb.save(p)
    fails = V.verify(p, quiet=True)
    fails = fails[0] if isinstance(fails, tuple) else fails
    assert fails == []


def test_健全性检查拿终值ROIC与终值WACC比(built):
    """生益 2026-11-17：「终值 ROIC 9.0% ≤ WACC 9.9%」——终值 ROIC 按模式 2 = 终值 WACC + 溢价，必高于终值
    WACC；拿明确期 WACC 比是误报（PLTR 研报同一误读）。"""
    rows, dv = V.recompute(V.read_inputs(built))
    dv = dict(dv, roic=0.090, wacc=0.099, wacc_term=0.075)
    assert not any("终值 ROIC" in w for w in V.sanity(dv, rows))
    assert any("终值 ROIC" in w and "终值 WACC" in w for w in V.sanity(dict(dv, roic=0.070), rows))
