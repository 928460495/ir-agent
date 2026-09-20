# -*- coding: utf-8 -*-
"""资本开支由营收增速推出。

**为什么不能三档同值。** 原实现三档都用「近三年 capex/营收均值线性收敛到
折旧率」，于是 Bull 档营收增速 45% 也配同样的资本开支——等于假设增长不
需要投入。只调有利项是情景分析最常见的作弊方式。

公式：当年资本开支 = Δ营收 ÷ 目标固定资产周转率 + 维持性投资（≈折旧）。
"""
import copy, os, pickle, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openpyxl import load_workbook
import ashare_calibrate as C
import ashare_build as B

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cal_600183.pkl")


def test_增速越高资本开支越高():
    """三档自动一致：Bull 增速高 → 需要更多产能 → 资本开支更高。"""
    out = C._capex_from_growth(
        growth3={"bull": [0.45, 0.30], "base": [0.20, 0.15], "bear": [0.05, 0.03]},
        base_rev=100.0, fa_turnover=2.0, maint_rate=0.05)
    assert out["bull"][0] > out["base"][0] > out["bear"][0]


def test_公式逐位可复算():
    """Δ营收 100×0.20 = 20；20 ÷ 2.0 = 10 增量投资；维持性 100×0.05 = 5；合计 15。
    以营收占比表示：15 / (100×1.20) = 0.125"""
    out = C._capex_from_growth(
        growth3={"base": [0.20]}, base_rev=100.0, fa_turnover=2.0, maint_rate=0.05)
    assert abs(out["base"][0] - 0.125) < 1e-9, out["base"][0]


def test_零增长时只剩维持性投资():
    out = C._capex_from_growth(
        growth3={"base": [0.0]}, base_rev=100.0, fa_turnover=2.0, maint_rate=0.05)
    assert abs(out["base"][0] - 0.05) < 1e-9


def test_负增长不产生负资本开支():
    """**收缩不等于卖厂房。** 营收下滑时增量投资归零，但维持性投资还在。"""
    out = C._capex_from_growth(
        growth3={"bear": [-0.10]}, base_rev=100.0, fa_turnover=2.0, maint_rate=0.05)
    assert out["bear"][0] >= 0.05, out["bear"][0]


def test_连续多年负增长时资本开支比例会回升():
    """**这不是 bug。** 审查 Task 3 时在真实 Bear 档见过 3.60%→3.71%→3.81%
    这样逐年上升的资本开支/营收比例，第一眼像是"营收都在萎缩，比例却在
    涨"的矛盾，但公式决定了这就是对的：

    连续负增长时增量项恒为 0（见 test_负增长不产生负资本开支），资本开支
    比例退化成 maint_rate × rev_prev / rev_now。分子用的是【上一年】那个
    还没缩水的营收，分母是【当年】已经缩水的营收——每年衰退幅度越大，
    rev_prev/rev_now 就越大，比例因此逐年走高。这是"维持性投资不会随
    营收下滑同步收缩"的合理近似（工厂折旧不会因为订单少了就当月停摆），
    不是把 rev_prev/rev_now 的更新顺序搞反了。

    若以后有人把这处顺序改错（比如误用同一年的 rev_now 当 rev_prev，
    或者把 delta 的符号放开），这条测试要能报警。
    """
    out = C._capex_from_growth(
        growth3={"bear": [-0.05, -0.10, -0.15]}, base_rev=100.0, fa_turnover=2.0, maint_rate=0.05)
    bear = out["bear"]
    # 逐位核算：手算值见本测试添加时的 commit message
    assert bear == pytest.approx([0.0526, 0.0556, 0.0588], abs=1e-4)
    # 营收本身是连续萎缩的（100 → 95 → 85.5 → 72.675），比例却单调走高——
    # 正是这条测试要钉死的"非单调回升不是 bug"。
    assert bear[0] < bear[1] < bear[2], bear


def test_逐年以上一年营收为基数():
    """第 2 年的 Δ营收要用第 1 年末的营收当基数，不是原始基期。"""
    out = C._capex_from_growth(
        growth3={"base": [0.20, 0.20]}, base_rev=100.0, fa_turnover=2.0, maint_rate=0.05)
    # 第2年：营收 120→144，Δ=24，增量 12，维持 120×0.05=6，合计 18，占比 18/144=0.125
    assert abs(out["base"][1] - 0.125) < 1e-9, out["base"][1]


# ── calibrate 交付 fa_turn / maint_rate，供 build 使用 ──────────────────

def test_calibrate交付fa_turn和maint_rate(cal_dict):
    ju = cal_dict["judg"]
    assert "fa_turn" in ju and "maint_rate" in ju
    assert 0.3 <= ju["fa_turn"] <= 10.0
    assert ju["maint_rate"] >= 0.0
    # 旧的 judg["capex"] 单序列已删除（见 task-3-report.md Important 1）：
    # _scen 的 fallback 只作用于 growth_scen，capex 从未被传给它，全仓库
    # 无消费方，留着只会让下一个维护者去找一个不存在的用途。
    assert "capex" not in ju


def test_夹具与当前calibrate代码同步(cal_dict):
    """夹具是某次 calibrate() 运行的离线快照（pickle），不会跟着代码改动
    自动更新。Task 3 审查时发现它已经滞后两三个提交——季节性对冲
    （cfabb70）与 gm_std_annual 修正（8e96197）落地时都没人重新生成过，
    直到本任务顺手 regenerate 才暴露（task-3-report.md Important 2）。
    没有测试盯着这件事，夹具就会悄悄过期而没人发现。

    这里只断言"当前 calibrate() 一定会产出/删除的字段"是否在夹具里，
    不断言任何具体数值（行情、beta、σ 都会随时间漂移，断言数值只会
    让测试变成另一种脆弱）。缺字段或多字段都说明夹具过期。
    """
    ju = cal_dict["judg"]
    missing = [k for k in ("fa_turn", "maint_rate") if k not in ju]
    stale_msg = ("夹具 tests/fixtures/cal_600183.pkl 已落后于当前 "
                 "ashare_calibrate.calibrate()，请重新生成：python3 tests/make_fixture.py 600183")
    assert not missing, f"缺字段 {missing}——{stale_msg}"
    assert "capex" not in ju, f"仍带着已删除的 judg['capex']——{stale_msg}"
    gen = cal_dict.get("gen", {})
    assert "_hedge_note" in gen, f"gen 缺少季节性对冲留痕 _hedge_note——{stale_msg}"


@pytest.fixture(scope="module")
def cal_dict():
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


def _with_growth_scen(cal, bull, base, bear):
    """注入三档营收增速判断项（ir-agent 的 assume.py 产出形状）。"""
    c = copy.deepcopy(cal)
    c["judg"]["growth_scen"] = {"bull": bull, "base": base, "bear": bear}
    return c


def test_工作簿里三档资本开支真的拉开(cal_dict, tmp_path):
    """本任务要断言的是工作簿里真正生效的那几格，不是中间变量——
    上一个任务的教训：只断言留痕字典，测不出函数是否真的把值写回。"""
    n = len(cal_dict["periods"]["proj"])
    bull = [0.45] * n; base = [0.20] * n; bear = [0.05] * n
    cal = _with_growth_scen(cal_dict, bull, base, bear)
    p = tmp_path / "w.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1_假设"]
    m = _rowmap(ws)
    r = m[f"{cal['periods']['proj'][0]} 资本开支 / 营收"]
    bull_v, base_v, bear_v = (ws.cell(r, c).value for c in (2, 3, 4))
    assert bull_v > base_v > bear_v, (bull_v, base_v, bear_v)
    # 逐位核算：与 _capex_from_growth 直接算出来的值必须一致，不是随便拉开
    base_rev = cal["rev"][cal["periods"]["annual"][-1]]
    expect = C._capex_from_growth(
        growth3={"bull": bull, "base": base, "bear": bear},
        base_rev=base_rev, fa_turnover=cal["judg"]["fa_turn"],
        maint_rate=cal["judg"]["maint_rate"])
    assert bull_v == pytest.approx(expect["bull"][0], abs=1e-4)
    assert base_v == pytest.approx(expect["base"][0], abs=1e-4)
    assert bear_v == pytest.approx(expect["bear"][0], abs=1e-4)


def test_没有三档增速时回退到同值三档(cal_dict, tmp_path):
    """上游没给 growth_scen 时，_scen 的 fallback 是 JU['growth']（单序列），
    三档营收增速因此同值，_capex_from_growth 推出的三档资本开支也应同值——
    回退是在增速层面级联到新公式的，不经过任何 capex 单序列（那个字段
    已删除，见 test_calibrate交付fa_turn和maint_rate 与 task-3-report.md）。"""
    cal = copy.deepcopy(cal_dict)
    assert "growth_scen" not in cal["judg"]
    p = tmp_path / "w.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1_假设"]
    m = _rowmap(ws)
    r = m[f"{cal['periods']['proj'][0]} 资本开支 / 营收"]
    bull_v, base_v, bear_v = (ws.cell(r, c).value for c in (2, 3, 4))
    assert bull_v == pytest.approx(base_v, abs=1e-6)
    assert base_v == pytest.approx(bear_v, abs=1e-6)
