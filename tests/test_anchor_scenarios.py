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


def _with_drivers(cal, base_levels=None):
    """给夹具 cal 注入一份最小的因子分解块（ir-agent 的 to_driver_block 产出形状）。"""
    c = copy.deepcopy(cal)
    n = len(c["periods"]["proj"])
    rev = {"op": "乘",
           "factors": [{"key": "cap", "label": "产能", "unit": "变化率"},
                       {"key": "asp", "label": "单价", "unit": "变化率"}],
           "values": {k: [[0.05, 0.01]] * n for k in ("bull", "base", "bear")}}
    gm = {"op": "加",
          "factors": [{"key": "mix", "label": "结构", "unit": "pp"},
                      {"key": "cost", "label": "成本", "unit": "pp"}],
          "values": {"bull": [[1.0, 0.5]] * n, "base": [[0.5, 0.2]] * n,
                     "bear": [[0.0, -0.2]] * n},
          "base_level": 0.2647}
    if base_levels:
        gm["base_levels"] = base_levels
    c["judg"]["drivers"] = {"revenue": rev, "gross_margin": gm}
    return c


def _gm_synth_formula(path, year_label, col):
    ds = load_workbook(path)["1b_驱动分解"]
    r = _rowmap(ds)[f"{year_label} 毛利率（合成）"]
    return str(ds[f"{col}{r}"].value)


def test_1b毛利率合成可以按档按年用不同基期(cal, tmp_path):
    """锚定开启时，第 2 年起的毛利率要从各档锚定后的首年水平接着走，而不是回到
    2025 年的基期——生益科技实跑 Base 档 2026 年锚定 30.69%，2027 年却掉回
    25.87%。基期因此要能按档、按年给。"""
    n = len(cal["periods"]["proj"])
    bl = {"bull": [0.2647] + [0.3010] * (n - 1),
          "base": [0.2647] + [0.2900] * (n - 1),
          "bear": [0.2647] + [0.2800] * (n - 1)}
    p = tmp_path / "w.xlsx"
    B.build(_with_drivers(cal, bl), str(p))
    y1, y2 = cal["periods"]["proj"][0], cal["periods"]["proj"][1]
    assert _gm_synth_formula(p, y1, "B").startswith("=0.2647+")
    assert _gm_synth_formula(p, y2, "B").startswith("=0.301+")
    assert _gm_synth_formula(p, y2, "C").startswith("=0.29+")
    assert _gm_synth_formula(p, y2, "D").startswith("=0.28+")


def test_不给按年基期时维持单一基期(cal, tmp_path):
    p = tmp_path / "w.xlsx"
    B.build(_with_drivers(cal), str(p))
    y2 = cal["periods"]["proj"][1]
    for col in "BCD":
        assert _gm_synth_formula(p, y2, col).startswith("=0.2647+")


def test_按年基期之下公式引擎验算仍然全过(cal, tmp_path):
    n = len(cal["periods"]["proj"])
    bl = {k: [0.2647] + [v] * (n - 1)
          for k, v in (("bull", 0.301), ("base", 0.29), ("bear", 0.28))}
    p = tmp_path / "w.xlsx"
    B.build(_with_drivers(cal, bl), str(p))
    fails, _ = V.verify(str(p), quiet=True)
    assert not fails
