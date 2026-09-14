# -*- coding: utf-8 -*-
"""锚定两格的三档。

锚定开关打开后，首个预测年的营收与毛利率由已披露中期反推，上方该年
的三档判断项被完全覆盖——此时 Bull/Bear 在首年根本不分叉，而首年是
整条路径的基数。三档必须挂到真正起作用的两格上：占全年比重、剩余期
毛利率。
"""
import copy, os, pickle, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openpyxl import load_workbook
import ashare_build as B
import ashare_verify as V

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cal_600183.pkl")
SCEN_LABEL = "选择情景  1=Bull乐观 / 2=Base中性 / 3=Bear悲观"


@pytest.fixture(scope="module")
def cal():
    if not os.path.exists(FIX):
        pytest.skip("缺离线夹具：python3 tests/make_fixture.py 600183")
    with open(FIX, "rb") as f:
        return pickle.load(f)


def _rowmap(ws):
    m = {}
    for r in range(1, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if isinstance(v, str) and v.strip():
            m.setdefault(v.strip(), r)
    return m


def _build(cal, tmp_path):
    p = tmp_path / "w.xlsx"
    B.build(copy.deepcopy(cal), str(p))
    return p


def test_占比三档取历史同期占比的最小_均值_最大(cal, tmp_path):
    """占比越低 → 隐含全年营收越高 → 属于 Bull。"""
    a = load_workbook(_build(cal, tmp_path))["1_假设"]
    m = _rowmap(a)
    it = cal["interim"]
    r = m["已披露期占全年营收比重"]
    bull, base, bear = (a.cell(r, c).value for c in (2, 3, 4))
    assert bull == pytest.approx(min(it["share_hist"]), abs=1e-4)
    assert base == pytest.approx(it["share"], abs=1e-4)
    assert bear == pytest.approx(max(it["share_hist"]), abs=1e-4)
    assert str(a.cell(r, 5).value).startswith("=CHOOSE(")


def test_剩余期毛利率三档严格拉开(cal, tmp_path):
    a = load_workbook(_build(cal, tmp_path))["1_假设"]
    r = _rowmap(a)["剩余期毛利率"]
    bull, base, bear = (a.cell(r, c).value for c in (2, 3, 4))
    assert bull is not None and bear is not None
    assert bull > base > bear
    assert base == pytest.approx(round(cal["interim"]["gm"], 4), abs=1e-4)
    assert str(a.cell(r, 5).value).startswith("=CHOOSE(")


def test_隐含全年营收与成本引用生效值而不是B列(cal, tmp_path):
    """公式若仍指 B 列，切到 Base/Bear 时首年不会动——三档形同虚设。"""
    a = load_workbook(_build(cal, tmp_path))["1_假设"]
    m = _rowmap(a)
    share_r, gm_r = m["已披露期占全年营收比重"], m["剩余期毛利率"]
    fy_rev = str(a.cell(m["→ 隐含全年营业总收入"], 2).value)
    fy_cogs = str(a.cell(m["→ 隐含全年营业成本"], 2).value)
    assert f"E{share_r}" in fy_rev and f"B{share_r}" not in fy_rev
    assert f"E{gm_r}" in fy_cogs and f"B{gm_r}" not in fy_cogs


def test_read_inputs按当前情景读锚定值(cal, tmp_path):
    p = _build(cal, tmp_path)
    it = cal["interim"]
    for scen, want in ((1, min(it["share_hist"])), (2, it["share"]),
                       (3, max(it["share_hist"]))):
        wb = load_workbook(p)
        a = wb["1_假设"]
        a.cell(_rowmap(a)[SCEN_LABEL], 2).value = scen
        wb.save(p)
        got = V.read_inputs(str(p))["it"]["share"]
        assert got == pytest.approx(want, abs=1e-4), f"情景 {scen}"


def test_三档之下公式引擎验算仍然全过(cal, tmp_path):
    p = _build(cal, tmp_path)
    fails, _ = V.verify(str(p), quiet=True)
    assert not fails
    bad = [x for x in V.verify_all_scenarios(str(p), quiet=True) if x[0] != "Base"]
    assert not bad


def test_锚定三档规则只在calibrate里定义一份():
    """三档规则原先写在 build 里。ir-agent 为了算「各档锚定后的首年毛利率」也要
    用同一套规则——两边各写一遍，早晚分叉成工作簿一个数、清单另一个数。规则
    挪到 calibrate，结果写进中期块，build 与 ir-agent 都只读。"""
    import ashare_calibrate as C
    sh = [0.5204, 0.4751, 0.4723, 0.446]
    share3, gm3 = C.anchor_three(sh, gm=0.3069, gm_std=0.03)
    assert share3 == (0.446, round(sum(sh) / len(sh), 4), 0.5204)
    assert gm3 == (0.3369, 0.3069, 0.2769)


def test_毛利率历史过于平稳时三档仍然拉开():
    import ashare_calibrate as C
    _, gm3 = C.anchor_three([0.47, 0.47], gm=0.30, gm_std=0.0)
    assert gm3[0] > gm3[1] > gm3[2]


def test_calibrate把三档写进中期块(cal):
    it = cal["interim"]
    assert len(it["share3"]) == 3 and len(it["gm_rest3"]) == 3
    assert it["share3"][0] == pytest.approx(min(it["share_hist"]), abs=1e-4)
