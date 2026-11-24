# -*- coding: utf-8 -*-
"""研发资本化（spec 2026-11-24-rd-capitalization）。不联网。

Damodaran：研发是投资，费用化让 ROIC 失真。A 股按申万行业年限把费用化研发资本化；投入资本补上
无形资产与开发支出（公司自行资本化的研发在这里）；NOPAT = EBIT×(1−t) + 研发 − 摊销（保留全额抵扣的税盾）。"""
import os
import pickle
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_calibrate as C

Y = 1e8
P = "20251231"
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cal_600183.pkl")


# ── Task 1：校准 ──────────────────────────────────────────────

def test_capitalize_rd_与美股同式():
    r = C.capitalize_rd([100, 120, 150, 180, 200, 240], 5)
    assert r["asset"] == pytest.approx(240 + 200 * 4 / 5 + 180 * 3 / 5 + 150 * 2 / 5 + 120 * 1 / 5)
    assert r["amort"] == pytest.approx((200 + 180 + 150 + 120 + 100) / 5)
    assert r["complete"] is True and r["covered"] == 6 and r["life"] == 5


def test_capitalize_rd_历史不足标不完整():
    r = C.capitalize_rd([50, 60, 70], 10)
    assert r["complete"] is False and r["covered"] == 3 and r["asset"] > 0


def test_capitalize_rd_全零或空():
    assert C.capitalize_rd([], 5)["asset"] == 0
    assert C.capitalize_rd([0, 0, 0], 5)["amort"] == 0
    assert C.capitalize_rd([None, 10.0], 5)["covered"] == 1          # None 跳过


def test_rd_history_只取年报且跳过缺失():
    inc = pd.DataFrame({"研发费用": [None, 1.5 * Y, 3.2 * Y], "营业总收入": [10 * Y] * 3},
                       index=["20231231", "20240630", "20241231"])
    assert C.rd_history(inc, "20241231") == [pytest.approx(3.2)]


def _bal(**extra):
    base = {"货币资金": 50.0, "交易性金融资产": 0.0, "应收票据及应收账款": 10.0, "存货": 20.0,
            "流动资产合计": 90.0, "固定资产净额": 40.0, "在建工程": 5.0, "非流动资产合计": 70.0,
            "短期借款": 0.0, "应付票据及应付账款": 10.0, "一年内到期的非流动负债": 0.0, "流动负债合计": 30.0,
            "长期借款": 0.0, "负债合计": 35.0, "少数股东权益": 0.0, "资产总计": 160.0}
    base.update(extra)
    return pd.DataFrame({k: [v * Y] for k, v in base.items()}, index=[P])


def test_opening_balances_无形部分():
    o = C.opening_balances(_bal(无形资产=12.0, 开发支出=3.0), P)
    assert o["intan"] == pytest.approx(15.0)
    assert C.opening_balances(_bal(), P)["intan"] == 0          # 两项都缺


def test_calibrate_写入rdcap与开关(monkeypatch):
    from tests.test_asof_quote import _offline, AS_OF
    if not os.path.exists(FIX):
        pytest.skip("缺离线夹具")
    cal_dict = pickle.load(open(FIX, "rb"))
    _offline(monkeypatch, cal_dict, {})
    cal = C.calibrate("600183", 10, stmts=cal_dict["stmts"], as_of=AS_OF, rd_life=10,
                      rd_disclosure={"total": 82.28e8, "capitalized": 16.45e8, "rate": 0.1999})
    assert cal["dcf"]["rd_cap"] == 1 and cal["dcf"]["rd_life"] == 10
    rc = cal["rdcap"]
    assert rc["life"] == 10 and rc["rd_hist"] and rc["asset"] > 0
    assert rc["intan0"] == pytest.approx(cal["open"]["intan"])
    assert rc["nonintan0"] == pytest.approx(cal["open"]["onca"] - cal["open"]["intan"])
    assert rc["rate"] == pytest.approx(0.1999) and rc["capitalized"] == pytest.approx(16.45)


def test_calibrate_缺省年限5_资本化率由金额推出(monkeypatch):
    from tests.test_asof_quote import _offline, AS_OF
    if not os.path.exists(FIX):
        pytest.skip("缺离线夹具")
    cal_dict = pickle.load(open(FIX, "rb"))
    _offline(monkeypatch, cal_dict, {})
    cal = C.calibrate("600183", 10, stmts=cal_dict["stmts"], as_of=AS_OF,
                      rd_disclosure={"total": 10e8, "capitalized": 2e8, "rate": None})
    assert cal["dcf"]["rd_life"] == 5 and cal["rdcap"]["rate"] == pytest.approx(0.2)
    cal2 = C.calibrate("600183", 10, stmts=cal_dict["stmts"], as_of=AS_OF)
    assert cal2["rdcap"]["rate"] is None and cal2["rdcap"]["total"] is None


# ── Task 2：工作簿（formulas 引擎真算）────────────────────────────

import copy


def _fixture_cal():
    if not os.path.exists(FIX):
        pytest.skip("缺离线夹具")
    return pickle.load(open(FIX, "rb"))


def _with_rd(cal, rd_cap=1, roic_mode=2, nonintan_mult=0.5, life=5):
    cal = copy.deepcopy(cal)
    onca = cal["open"]["onca"]
    intan = onca * (1 - nonintan_mult)
    cal["dcf"].update(rd_cap=rd_cap, rd_life=life, roic_mode=roic_mode)
    cal["rdcap"] = dict(asset=30.0, amort=5.0, life=life, covered=6, complete=True,
                        rd_hist=[3.0, 4.0, 5.0, 6.0, 7.0, 8.0], intan0=intan,
                        nonintan0=onca * nonintan_mult, total=None, capitalized=None, rate=None)
    return cal


def _calc(tmp_path, cal, name="t.xlsx"):
    import ashare_build as B
    import formulas
    from openpyxl import load_workbook
    out = str(tmp_path / name)
    B.build(cal, out)
    sol = formulas.ExcelModel().loads(out).finish().calculate()

    def G(sh, cell):
        v = sol.get(f"'[{name.upper()}]{sh.upper()}'!{cell}") or sol.get(f"'[{name}]{sh}'!{cell}")
        if v is None:
            return None
        try:
            return float(v.value[0, 0])
        except Exception:                                   # noqa: BLE001
            return None
    return load_workbook(out), G, cal


def _row(ws, label, col=1):
    for r in range(1, ws.max_row + 1):
        v = ws.cell(r, col).value
        if isinstance(v, str) and v.startswith(label):
            return r
    raise AssertionError(f"找不到「{label}」")


def _ps(wb, G):
    ws = wb["5_DCF"]
    return G("5_DCF", f"B{_row(ws, '每股内在价值')}")


def test_备查行_研发资本滚动且不破坏平衡(tmp_path):
    wb, G, cal = _calc(tmp_path, _with_rd(_fixture_cal()))
    CC = cal["_rows"]["CC"]
    bs = "3_资产负债表"
    for c in CC:
        assert abs(G(bs, f"{c}29")) < 1e-6                                   # 平衡校验
    a_prev = 30.0
    for i, c in enumerate(CC):
        am = G(bs, f"{c}32"); asset = G(bs, f"{c}33")
        rd = G("2_利润表", f"{c}{cal['_ref']['RRD']}")
        assert am == pytest.approx(a_prev / 5, rel=1e-9)
        assert asset == pytest.approx(a_prev + rd - am, rel=1e-9)
        a_prev = asset


def test_无形部分_摊销压低时取0(tmp_path):
    wb, G, cal = _calc(tmp_path, _with_rd(_fixture_cal(), nonintan_mult=50.0))
    for c in cal["_rows"]["CC"]:
        assert G("3_资产负债表", f"{c}34") == 0


def test_两行ROIC与开关(tmp_path):
    for sw in (1, 0):
        wb, G, cal = _calc(tmp_path, _with_rd(_fixture_cal(), rd_cap=sw), name=f"r{sw}.xlsx")
        ws = wb["7_财务比率与杜邦"]
        r_rep, r_rd = _row(ws, "ROIC（报表口径）"), _row(ws, "ROIC（研发资本化）")
        c = cal["_rows"]["CC"][-1]; col = cal["_rows"]["NH"] + len(cal["_rows"]["CC"]) + 1
        from openpyxl.utils import get_column_letter as CL
        ebit = G("2_利润表", f"{c}{cal['_ref']['REBIT']}")
        rd = G("2_利润表", f"{c}{cal['_ref']['RRD']}")
        bs = "3_资产负债表"
        ic_rep = G(bs, f"{c}31") + G(bs, f"{c}12") + G(bs, f"{c}34")
        v_rep, v_rd = G("7_财务比率与杜邦", f"{CL(col)}{r_rep}"), G("7_财务比率与杜邦", f"{CL(col)}{r_rd}")
        nopat_rep = v_rep * ic_rep
        if sw == 1:
            assert v_rd == pytest.approx((nopat_rep + rd - G(bs, f"{c}32")) / (ic_rep + G(bs, f"{c}33")), rel=1e-6)
        else:
            assert v_rd == pytest.approx(v_rep, rel=1e-9)
        assert G(bs, f"{c}35") == pytest.approx(ic_rep, rel=1e-9)


def test_默认模式每股价值不随开关变(tmp_path):
    base = _fixture_cal()
    wb1, G1, _ = _calc(tmp_path, _with_rd(base, rd_cap=1), name="a1.xlsx")
    wb0, G0, _ = _calc(tmp_path, _with_rd(base, rd_cap=0), name="a0.xlsx")
    assert _ps(wb1, G1) == pytest.approx(_ps(wb0, G0), rel=1e-9)


def test_模式1终值用生效IC(tmp_path):
    base = _fixture_cal()
    wb1, G1, _ = _calc(tmp_path, _with_rd(base, rd_cap=1, roic_mode=1), name="m1.xlsx")
    wb0, G0, _ = _calc(tmp_path, _with_rd(base, rd_cap=0, roic_mode=1), name="m0.xlsx")
    assert _ps(wb1, G1) != pytest.approx(_ps(wb0, G0), rel=1e-6)
    ws = wb1["5_DCF"]
    assert G1("5_DCF", f"B{_row(ws, '调整后 NOPAT')}") is not None


def test_旧夹具无rdcap照常建模且IC不变(tmp_path):
    wb, G, cal = _calc(tmp_path, _fixture_cal(), name="old.xlsx")
    for c in cal["_rows"]["CC"]:
        assert G("3_资产负债表", f"{c}34") == 0
    a = wb["1_假设"]
    assert a.cell(_row(a, "研发资本化开关"), 2).value == 0


def test_历史营运资本有值(tmp_path):
    wb, G, cal = _calc(tmp_path, _with_rd(_fixture_cal()), name="h.xlsx")
    ws = wb["3_资产负债表"]
    assert ws.cell(31, 2).value is not None              # 第一个历史列


# ── Task 3：独立验算与蒙特卡洛同口径 ────────────────────────────

def _build(tmp_path, cal, name):
    import ashare_build as B
    out = str(tmp_path / name)
    B.build(cal, out)
    return out


@pytest.mark.parametrize("sw,mode", [(1, 2), (0, 2), (1, 1)])
def test_验算覆盖新增行(tmp_path, sw, mode):
    import ashare_verify as V
    path = _build(tmp_path, _with_rd(_fixture_cal(), rd_cap=sw, roic_mode=mode), f"v{sw}{mode}.xlsx")
    fails, dv = V.verify(path, quiet=True)
    assert fails == []
    assert "nopat_adj" in dv and "ic_rep" in dv
    rows, _ = V.recompute(V.read_inputs(path))
    assert rows[-1]["rda"] > 0 and rows[-1]["intan"] >= 0


def test_旧工作簿验算按开关0(tmp_path):
    """11-24 之前的工作簿没有研发资本化的假设行与备查行：按开关 0、无形部分 0 读，验算不失配。"""
    import ashare_verify as V
    from openpyxl import load_workbook
    path = _build(tmp_path, _fixture_cal(), "old.xlsx")
    wb = load_workbook(path)
    a = wb["1_假设"]
    for lbl in ("研发资本化开关", "研发摊销年限", "期初研发资本", "基期非无形部分", "研发资本化率"):
        a.cell(_row(a, lbl), 1).value = "（旧版无此行）"
    bs = wb["3_资产负债表"]
    for r in range(32, 38):
        bs.cell(r, 1).value = None
    wb.save(path)
    inp = V.read_inputs(path)
    assert inp["dcf"]["研发资本化开关"] == 0 and inp["dcf"]["基期非无形部分"] is None
    fails, _ = V.verify(path, quiet=True)
    assert fails == []


@pytest.mark.parametrize("sw,mode", [(1, 1), (0, 1), (1, 2)])
def test_蒙特卡洛零波动复现确定性结果_研发资本化(tmp_path, sw, mode):
    import ashare_mc as MC
    import ashare_verify as V
    path = _build(tmp_path, _with_rd(_fixture_cal(), rd_cap=sw, roic_mode=mode), f"mc{sw}{mode}.xlsx")
    inp = V.read_inputs(path)
    P = MC.defaults({})
    for k in list(P):
        if str(k).startswith("sd_"):
            P[k] = 0.0
    P["n_paths"] = 10
    out = MC.simulate(inp, P)
    _, dv = V.recompute(inp)
    assert abs(float(out["ps"].mean()) - dv["ps"]) < 1e-6 * max(1.0, abs(dv["ps"]))


def test_模式1下蒙特卡洛随开关变化(tmp_path):
    import ashare_mc as MC
    import ashare_verify as V
    ps = {}
    for sw in (1, 0):
        path = _build(tmp_path, _with_rd(_fixture_cal(), rd_cap=sw, roic_mode=1), f"mm{sw}.xlsx")
        P = MC.defaults({}); P["n_paths"] = 200
        ps[sw] = float(MC.simulate(V.read_inputs(path), P)["ps"].mean())
    assert ps[1] != pytest.approx(ps[0], rel=1e-6)
