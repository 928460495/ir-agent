# -*- coding: utf-8 -*-
"""非经营性金融资产（财务公司）—— 引擎校准（spec 2026-11-20-nonop-financial-assets，Task 3）。不联网。

茅台 2025：拆出资金 990.96 亿、买入返售 88.80 亿、一年内到期的非流动资产 268.71 亿（附注：一年内到期的债权投资）、
债权投资 11.13 亿、吸收存款及同业存放 180.38 亿。此前全进「其他流动资产」，按 81% 的营收占比随营收增长扣自由现金流。"""
import os, sys
from decimal import Decimal

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_calibrate as C

Y = 1e8
P = "20251231"


def _bal(**extra):
    base = {"货币资金": 516.91, "交易性金融资产": 0.0, "应收票据及应收账款": 0.0, "存货": 574.57,
            "流动资产合计": 2525.19, "固定资产净额": 220.92, "在建工程": 30.0, "非流动资产合计": 513.16,
            "短期借款": 0.0, "应付票据及应付账款": 40.0, "一年内到期的非流动负债": 0.4, "流动负债合计": 640.0,
            "长期借款": 0.0, "负债合计": 650.0, "少数股东权益": 100.0, "资产总计": 3038.35,
            "拆出资金": 990.96, "买入返售金融资产": 88.80, "一年内到期的非流动资产": 268.71114612,
            "债权投资": 11.13, "吸收存款及同业存放": 180.38}
    base.update(extra)
    return pd.DataFrame({k: [v * Y] for k, v in base.items()}, index=[P])


NOTES = {"nca1y_fin": Decimal("26871114612.71"), "statutory_reserve": Decimal("7254499309.26"),
         "term_restricted": {"拆出资金": Decimal("23309929811.40")}, "encumbered": {}}


def test_茅台净金融资产():
    fb = C.fin_bridge(_bal(), P, NOTES)
    assert fb["fa_cur"] == pytest.approx(990.96 + 88.80 + 268.71114612, abs=1e-6)
    assert fb["fa_nc"] == pytest.approx(11.13) and fb["fl"] == pytest.approx(180.38) and fb["restr"] == 0
    assert fb["net"] == pytest.approx(990.96 + 88.80 + 268.71114612 + 11.13 - 180.38, abs=1e-6)
    src = {k: s for k, _, s in fb["items"]}
    assert "附注" in src["一年内到期的非流动资产（金融部分）"]


def test_没有附注时一年内到期部分不计并写明():
    fb = C.fin_bridge(_bal(), P, None)
    assert fb["fa_cur"] == pytest.approx(990.96 + 88.80)
    assert any("未取到附注" in s for s in fb["remarks"]) and not any("未取到附注" in s for _, _, s in fb["items"])


def test_质押冻结的扣除_法定准备金与定期不扣():
    notes = dict(NOTES, encumbered={"货币资金": Decimal("1000000000")})
    assert C.fin_bridge(_bal(), P, notes)["restr"] == pytest.approx(10.0)
    assert C.fin_bridge(_bal(), P, NOTES)["restr"] == 0


def test_期初余额_净额并入交易性金融资产_报表恒等不变():
    raw = C.opening_balances(_bal(), P)
    fb0 = C.fin_bridge(_bal(), P, None)
    # 原始口径：其他流动资产 = 流动资产合计 − 货币资金 − 交易性金融资产 − 应收 − 存货
    assert raw["oca"] == pytest.approx(2525.19 - 516.91 - 0 - 0 - 574.57 - fb0["fa_cur"])
    assert raw["tfa"] == pytest.approx(fb0["net"])
    after = C.opening_balances(_bal(), P, NOTES)
    fb = C.fin_bridge(_bal(), P, NOTES)
    assert after["tfa"] == pytest.approx(fb["net"])
    assert after["oca"] == pytest.approx(2525.19 - 516.91 - 574.57 - fb["fa_cur"])
    assert after["ocl"] == pytest.approx(640.0 - 0 - 40.0 - 0.4 - fb["fl"])
    assert after["_check"] == pytest.approx(raw["_check"], abs=1e-9)


def test_没有财务公司科目的公司逐位不变():
    plain = _bal(**{k: 0.0 for k in ("拆出资金", "买入返售金融资产", "一年内到期的非流动资产", "债权投资",
                                     "吸收存款及同业存放")})
    assert C.opening_balances(plain, P, NOTES) == C.opening_balances(plain, P) or \
        C.fin_bridge(plain, P, NOTES)["net"] == 0
    a, b = C.opening_balances(plain, P, None), C.opening_balances(plain.drop(columns=["拆出资金"]), P, None)
    assert {k: a[k] for k in a} == {k: b[k] for k in b}


def test_倒推折旧_拆出资金增加已在经营现金流里_与营运资本相互抵消():
    """终审 I5：合并现金流量表里「拆出资金净增加额」列在经营活动 —— 经营现金流已减去它，营运资本里的拆出资金
    增加正好抵消。把它从营运资本剔除反而让倒推的 D&A 偏差 Δ拆出资金（茅台约 570 亿）。"""
    idx = ["20241231", P]
    def frames(lend):
        bal = pd.DataFrame({"流动资产合计": [2000 * Y, (2000 + lend) * Y], "货币资金": [500 * Y, 500 * Y],
                            "交易性金融资产": [0, 0], "流动负债合计": [600 * Y, 600 * Y], "短期借款": [0, 0],
                            "一年内到期的非流动负债": [0, 0], "拆出资金": [900 * Y, (900 + lend) * Y]}, index=idx)
        inc = pd.DataFrame({"净利润": [800 * Y, 800 * Y]}, index=idx)
        cf = pd.DataFrame({"经营活动产生的现金流量净额": [900 * Y, (900 - lend) * Y]}, index=idx)
        return inc, bal, cf
    assert C.implied_da(*frames(0), idx) == pytest.approx(C.implied_da(*frames(570), idx))


def test_没有附注且只有经营性一年内到期科目时_不出小节不改余额():
    """终审 I3：多数公司都有一年内到期的非流动资产（多为一年内到期的长期应收款）—— 0 值不能进明细。"""
    plain = _bal(**{k: 0.0 for k in ("拆出资金", "买入返售金融资产", "债权投资", "吸收存款及同业存放")})
    fb = C.fin_bridge(plain, P, None)
    assert fb["items"] == [] and fb["net"] == 0 and fb["remarks"]
    assert C.opening_balances(plain, P, None)["tfa"] == 0


def test_只有受限资金扣除时措辞不说财务公司():
    plain = _bal(**{k: 0.0 for k in ("拆出资金", "买入返售金融资产", "一年内到期的非流动资产", "债权投资",
                                     "吸收存款及同业存放")})
    fb = C.fin_bridge(plain, P, dict(NOTES, nca1y_fin=None, encumbered={"货币资金": Decimal("500000000")}))
    assert fb["net"] == pytest.approx(-5.0) and fb["kind"] == "restricted_only"


def test_有财务公司时营收剔除利息收入():
    """终审 I1：营业总收入 1720.54 = 营业收入 1688.38 + 财务公司利息收入 32.16 —— 资产按账面加回，它产生的收入
    就不能再在经营价值里资本化。"""
    inc = pd.DataFrame({"营业总收入": [1700 * Y, 1720.54 * Y], "营业收入": [1670 * Y, 1688.38 * Y]},
                       index=["20241231", P])
    out, adj = C.strip_fin_income(inc, _bal(), P)
    assert list(out["营业总收入"] / Y) == pytest.approx([1670, 1688.38]) and adj == pytest.approx(32.16)
    plain = _bal(**{k: 0.0 for k in ("拆出资金", "买入返售金融资产", "一年内到期的非流动资产", "债权投资",
                                     "吸收存款及同业存放")})
    out2, adj2 = C.strip_fin_income(inc, plain, P)
    assert out2 is inc and adj2 == 0


def test_校准接收附注并交出明细(monkeypatch):
    """calibrate(fin_notes=) 用于期初余额与营运资本比率；cal["fin_bridge"] 交给建表逐项列示。"""
    from tests.test_asof_quote import _offline, AS_OF
    import pickle
    fix = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cal_600183.pkl")
    if not os.path.exists(fix):
        pytest.skip("缺离线夹具")
    cal_dict = pickle.load(open(fix, "rb"))
    seen = {}
    _offline(monkeypatch, cal_dict, seen)
    real = C.fin_bridge
    calls = []
    monkeypatch.setattr(C, "fin_bridge", lambda bal, p, fn=None: (calls.append(fn), real(bal, p, fn))[1])
    cal = C.calibrate("600183", 10, stmts=cal_dict["stmts"], as_of=AS_OF, fin_notes=NOTES)
    assert NOTES in calls
    assert cal["fin_bridge"]["net"] == 0 and cal["fin_bridge"]["items"] == []      # 生益没有财务公司科目


# ── Task 4：工作簿列示与验算 ──

def _rowmap(ws):
    return {ws.cell(r, 1).value: r for r in range(1, ws.max_row + 1) if isinstance(ws.cell(r, 1).value, str)}


@pytest.fixture(scope="module")
def cal600183():
    import pickle
    fix = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cal_600183.pkl")
    if not os.path.exists(fix):
        pytest.skip("缺离线夹具")
    return pickle.load(open(fix, "rb"))


def _with_fin(cal, fb):
    import copy
    c = copy.deepcopy(cal)
    c["fin_bridge"] = fb
    c["open"]["tfa"] = c["open"]["tfa"] + fb["net"]
    c["open"]["oca"] = c["open"]["oca"] - fb["fa_cur"]
    c["open"]["onca"] = c["open"]["onca"] - fb["fa_nc"] + fb["restr"]
    c["open"]["ocl"] = c["open"]["ocl"] - fb["fl"]
    return c


def test_有财务公司科目时1_假设逐项列示且验算通过(tmp_path, cal600183):
    import ashare_build as B, ashare_verify as V
    from openpyxl import load_workbook
    fb = C.fin_bridge(_bal(), P, NOTES)
    p = str(tmp_path / "fin.xlsx")
    B.build(_with_fin(cal600183, fb), p)
    a = load_workbook(p)["1_假设"]
    m = _rowmap(a)
    head = [k for k in m if k.startswith("五（补）、非经营性金融资产")]
    assert head, "要有逐项列示小节"
    assert a.cell(m["拆出资金"], 2).value == pytest.approx(990.96)
    assert "净额" in "".join(k for k in m if k.startswith("净额"))
    assert "非经营性金融资产" in (a.cell(m["交易性金融资产"], 6).value or "")
    fails, _ = V.verify(p, quiet=True)
    assert fails == []
    from openpyxl.styles import Font
    assert a.cell(m["拆出资金"], 2).font.color.rgb != B.F_IN.color.rgb          # 只读，不是可编辑输入的蓝色
    # 验算器核对：期初交易性金融资产 = 报表列报值 + 明细净额；有人只改了一边就报
    wb = load_workbook(p); ws = wb["1_假设"]; mm = _rowmap(ws)
    ws.cell(mm["交易性金融资产"], 2).value = float(ws.cell(mm["交易性金融资产"], 2).value) + 100
    p2 = str(tmp_path / "fin_bad.xlsx"); wb.save(p2)
    fails2, _ = V.verify(p2, quiet=True)
    assert any("非经营性金融资产" in str(f) for f in fails2)


def test_没有财务公司科目时版式与数值不变(tmp_path, cal600183):
    import ashare_build as B
    from openpyxl import load_workbook
    p1, p2 = str(tmp_path / "a.xlsx"), str(tmp_path / "b.xlsx")
    B.build(cal600183, p1)
    c2 = dict(cal600183); c2["fin_bridge"] = C.fin_bridge(_bal(**{k: 0.0 for k in (
        "拆出资金", "买入返售金融资产", "一年内到期的非流动资产", "债权投资", "吸收存款及同业存放")}), P, None)
    B.build(c2, p2)
    a1, a2 = load_workbook(p1)["1_假设"], load_workbook(p2)["1_假设"]
    assert a1.max_row == a2.max_row
    assert [[c.value for c in r] for r in a1.iter_rows()] == [[c.value for c in r] for r in a2.iter_rows()]
