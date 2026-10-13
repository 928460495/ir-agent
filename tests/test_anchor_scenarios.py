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


# ── I2（final-fix-D 终审）：Bull/Bear 用的 σ 不该混进中报 ────────────────
#
# ashare_build.py 写进 F 列给人看的是「Bull/Bear＝±1 个历史年度毛利率
# 标准差」，但实际喂给 anchor_three 的 σ 此前是 round(np.std(gm_hist), 4)
# ——gm_hist 最后一项就是本期中报毛利率，σ 因此混进了中报，且把 Base 值
# 自己（往往正是那个离群的中报数）算进了它自己的离散度。
#
# 600183 真实数据（tests/fixtures/cal_600183.pkl）：
#   gm_hist = [0.2682, 0.2203, 0.1919, 0.2204, 0.2647, 0.3069]（末项=2026H1）
#   σ(全部6项，此前实际用于三档) = 0.0383
#   σ(年度5项，F 列文案声称的口径) = 0.0292 —— 宽 31%
#
# gm_mean（gm_stats[0]）已经用 `n_ann_gm` 过滤掉中报（见 b13b276），σ 要
# 复用同一套判据，不新写一份。

_GM_HIST_600183 = [0.2682, 0.2203, 0.1919, 0.2204, 0.2647, 0.3069]
_ANN_600183 = ["20211231", "20221231", "20231231", "20241231", "20251231"]
_REV_600183 = {"20211231": 202.74262977869998, "20221231": 180.14442222419999,
              "20231231": 165.860728088, "20241231": 203.88330213560002,
              "20251231": 284.3113845944, "20260630": 190.2561176613}
_COGS_600183 = {p: 1.0 for p in _ANN_600183 + ["20260630"]}  # 只需非空即可，n_ann_gm 只判存在性


def test_gm_std按年度序列算不混进中报():
    """**600183 实跑复现。** `np.std(gm_hist)`（此前实际用于三档的口径）与
    `np.std(gm_hist[:5])`（F 列文案声称的"历史年度毛利率标准差"）差 31%，
    只因为 gm_hist 末项是中报，混进了不该混的一项。"""
    import numpy as np
    import ashare_calibrate as C
    sigma_full = round(float(np.std(_GM_HIST_600183)), 4)
    sigma_ann = round(float(np.std(_GM_HIST_600183[:5])), 4)
    assert sigma_full == 0.0383, "混进中报的（此前的）口径"
    assert sigma_ann == 0.0292, "只按年度序列算的（文案声称的）口径"
    got = C._gm_std_annual(_GM_HIST_600183, _ANN_600183, _REV_600183, _COGS_600183)
    assert round(got, 4) == 0.0292, "calibrate 实际用的 σ 必须是年度口径，不是混了中报的那个"


def test_gm_std_annual与gm_mean用同一套过滤判据():
    """`gm_mean` 已经用 `n_ann_gm`（数 rev 与 cogs 都有的年度期数）过滤掉
    中报——近 5 年缺 1 年营业成本时，年度项数会小于 len(ann)，此时 σ 也
    必须跟着少算这一年，不能自己另开一个"取前 len(ann) 项"的口径。"""
    import ashare_calibrate as C
    ann = _ANN_600183
    rev = dict(_REV_600183)
    cogs = {p: 1.0 for p in ann[1:] + ["20260630"]}   # 缺最早一年的营业成本
    gm_hist = [0.20, 0.21, 0.22, 0.23, 0.24, 0.30]     # 只有 4 个年度项 + 1 个中报
    got = C._gm_std_annual(gm_hist, ann, rev, cogs)
    import numpy as np
    assert got == pytest.approx(float(np.std(gm_hist[:4])), abs=1e-9)


def test_三档口径改用年度sigma之后前后对比(cal):
    """**改动会让三档变窄，这是预期结果。** 600183：
    旧（混中报，σ=0.0383）→ Bull 34.52% / Base 30.69% / Bear 26.86%；
    新（纯年度，σ≈0.0292）→ Bull 33.61% / Base 30.69% / Bear 27.78%。
    Base 不变（沿用已披露期毛利率），Bull/Bear 收窄，间距从 ±3.83pp
    收到 ±2.92pp——约窄 31%。"""
    import ashare_calibrate as C
    it = cal["interim"]
    gm = it["gm"]
    sigma_old = 0.0383
    sigma_new = C._gm_std_annual(cal["judg"]["gm_hist"], cal["periods"]["annual"],
                                 cal["rev"], cal["cogs"])
    _, gm3_old = C.anchor_three(it["share_hist"], gm, sigma_old)
    _, gm3_new = C.anchor_three(it["share_hist"], gm, sigma_new)
    assert gm3_old == (0.3452, 0.3069, 0.2686)
    assert gm3_new == (0.3361, 0.3069, 0.2778)
    assert (gm3_new[0] - gm3_new[2]) < (gm3_old[0] - gm3_old[2]), \
        "改用年度口径之后三档间距应该收窄，不是放宽"


def test_calibrate把三档写进中期块(cal):
    it = cal["interim"]
    assert len(it["share3"]) == 3 and len(it["gm_rest3"]) == 3
    assert it["share3"][0] == pytest.approx(min(it["share_hist"]), abs=1e-4)


# 旧乘/加分解式经 ir-agent `formula.from_decomps` 翻译后的两条公式的记号序列
# （`Formula.tokens()`，全括号）：营收 = 上期(营收)*(1+产能)*(1+单价)；
# 毛利率 = 基期(毛利率)+(结构+成本)/100。
_REV_TOKENS = [["lp", "("], ["lp", "("], ["prev", "营收"], ["op", "*"],
               ["lp", "("], ["num", "1"], ["op", "+"], ["name", "产能"], ["rp", ")"],
               ["rp", ")"], ["op", "*"],
               ["lp", "("], ["num", "1"], ["op", "+"], ["name", "单价"], ["rp", ")"],
               ["rp", ")"]]
_GM_TOKENS = [["lp", "("], ["base", "毛利率"], ["op", "+"],
              ["lp", "("], ["lp", "("], ["name", "结构"], ["op", "+"],
              ["name", "成本"], ["rp", ")"], ["op", "/"], ["num", "100"], ["rp", ")"],
              ["rp", ")"]]


def _with_drivers(cal, base_levels=None, anchor_y1=None):
    """给夹具 cal 注入一份最小的因子分解块（ir-agent 的 to_driver_block 产出形状，
    schema 2：一个块、公式以记号交付）。"""
    c = copy.deepcopy(cal)
    n = len(c["periods"]["proj"])
    annual = c["periods"]["annual"]
    drv = {"schema": 2, "mode": "变化",
           "factors": [{"key": "cap", "short": "产能", "label": "产能", "unit": "变化率"},
                       {"key": "asp", "short": "单价", "label": "单价", "unit": "变化率"},
                       {"key": "mix", "short": "结构", "label": "结构", "unit": "pp"},
                       {"key": "cost", "short": "成本", "label": "成本", "unit": "pp"}],
           "constants": [],
           "formulas": {"revenue": _REV_TOKENS, "gross_margin": _GM_TOKENS},
           "formula_text": {"revenue": "营收 = 上期(营收)*(1+产能)*(1+单价)",
                            "gross_margin": "毛利率 = 基期(毛利率)+(结构+成本)/100"},
           "base": {"revenue": c["rev"][annual[-1]], "margin": 0.2647, "factors": {}},
           "values": {"bull": [[0.05, 0.01, 1.0, 0.5]] * n,
                      "base": [[0.05, 0.01, 0.5, 0.2]] * n,
                      "bear": [[0.05, 0.01, 0.0, -0.2]] * n}}
    if base_levels:
        drv["base_levels"] = base_levels
    if anchor_y1:
        drv["anchor_y1"] = anchor_y1
    c["judg"]["drivers"] = drv
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
    assert _gm_synth_formula(p, y1, "B").startswith("=(0.2647+")
    assert _gm_synth_formula(p, y2, "B").startswith("=(0.301+")
    assert _gm_synth_formula(p, y2, "C").startswith("=(0.29+")
    assert _gm_synth_formula(p, y2, "D").startswith("=(0.28+")


def test_不给按年基期时维持单一基期(cal, tmp_path):
    p = tmp_path / "w.xlsx"
    B.build(_with_drivers(cal), str(p))
    y2 = cal["periods"]["proj"][1]
    ds = load_workbook(p)["1b_驱动分解"]
    gm0 = _rowmap(ds)["基期 毛利率"]
    for col in "BCD":
        assert _gm_synth_formula(p, y2, col).startswith(f"=($B${gm0}+")


def test_按年基期之下公式引擎验算仍然全过(cal, tmp_path):
    n = len(cal["periods"]["proj"])
    bl = {k: [0.2647] + [v] * (n - 1)
          for k, v in (("bull", 0.301), ("base", 0.29), ("bear", 0.28))}
    p = tmp_path / "w.xlsx"
    B.build(_with_drivers(cal, bl), str(p))
    fails, _ = V.verify(str(p), quiet=True)
    assert not fails


def test_旧形状的中期块也能建表且三档口径一致(cal, tmp_path):
    """旧版 calibrate 的产出（包括各处缓存下来的 pickle）中期块里没有 share3 /
    gm_rest3。4049205 让 build 只读这两项，于是任何旧形状的 cal 都会 KeyError
    崩掉——ir-agent 的 1b 工作表测试就是这么撞上的。build 缺这两项时要调用同一个
    规则函数现算，不另写一份规则，结果与新版 calibrate 逐位相同。"""
    old = copy.deepcopy(cal)
    old["interim"].pop("share3")
    old["interim"].pop("gm_rest3")
    p = tmp_path / "w.xlsx"
    B.build(old, str(p))
    a = load_workbook(p)["1_假设"]
    m = _rowmap(a)
    for label, key in (("已披露期占全年营收比重", "share3"), ("剩余期毛利率", "gm_rest3")):
        r = m[label]
        got = tuple(a.cell(r, c).value for c in (2, 3, 4))
        assert got == tuple(cal["interim"][key]), label


def _level_block(cal):
    """水平模式：营收 = 量 × 价；毛利率 = (价 − 本) ÷ 价；带一个常量。"""
    c = copy.deepcopy(cal)
    n = len(c["periods"]["proj"])
    lp, rp = ["lp", "("], ["rp", ")"]
    c["judg"]["drivers"] = {
        "schema": 2, "mode": "水平",
        "factors": [{"key": "q", "short": "量", "label": "出货量", "unit": "万吨"},
                    {"key": "p", "short": "价", "label": "单价", "unit": "元"},
                    {"key": "c", "short": "本", "label": "单位成本", "unit": "元"}],
        "constants": [{"name": "系数", "value": 1.0, "source": "示意"}],
        "formulas": {
            "revenue": [lp, lp, ["name", "量"], ["op", "*"], ["name", "价"], rp,
                        ["op", "*"], ["name", "系数"], rp],
            "gross_margin": [lp, lp, ["name", "价"], ["op", "-"], ["name", "本"], rp,
                             ["op", "/"], ["name", "价"], rp]},
        "base": {"revenue": None, "margin": 0.25,
                 "factors": {"量": 100.0, "价": 9.5, "本": 7.2}},
        "values": {"bull": [[100.0 * 1.08 ** (y + 1), 11.0, 7.3] for y in range(n)],
                   "base": [[100.0 * 1.05 ** (y + 1), 10.0, 7.4] for y in range(n)],
                   "bear": [[100.0 * 1.02 ** (y + 1), 9.0, 7.5] for y in range(n)]}}
    return c


def test_水平模式的基期营收由公式算出_常量区可引用(cal, tmp_path):
    p = tmp_path / "w.xlsx"
    B.build(_level_block(cal), str(p))
    ds = load_workbook(p)["1b_驱动分解"]
    m = _rowmap(ds)
    assert str(ds.cell(m["基期 营收（亿元）"], 2).value).startswith("=((")
    assert "常量 系数" in m and ds.cell(m["常量 系数"], 6).value == "示意"
    y1 = cal["periods"]["proj"][0]
    assert f"$B${m['常量 系数']}" in str(ds[f"C{m[f'{y1} 营收（合成）']}"].value)


def test_水平模式公式引擎验算全过(cal, tmp_path):
    p = tmp_path / "w.xlsx"
    B.build(_level_block(cal), str(p))
    fails, _ = V.verify(str(p), quiet=True)
    assert not fails
