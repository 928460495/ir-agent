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
        fa_turnover=2.0, maint_rate=0.05)
    assert out["bull"][0] > out["base"][0] > out["bear"][0]


def test_公式逐位可复算():
    """Δ营收 100×0.20 = 20；20 ÷ 2.0 = 10 增量投资；维持性 100×0.05 = 5；合计 15。
    以营收占比表示：15 / (100×1.20) = 0.125"""
    out = C._capex_from_growth(
        growth3={"base": [0.20]}, fa_turnover=2.0, maint_rate=0.05)
    assert abs(out["base"][0] - 0.125) < 1e-9, out["base"][0]


def test_零增长时只剩维持性投资():
    out = C._capex_from_growth(
        growth3={"base": [0.0]}, fa_turnover=2.0, maint_rate=0.05)
    assert abs(out["base"][0] - 0.05) < 1e-9


def test_负增长不产生负资本开支():
    """**收缩不等于卖厂房。** 营收下滑时增量投资归零，但维持性投资还在。"""
    out = C._capex_from_growth(
        growth3={"bear": [-0.10]}, fa_turnover=2.0, maint_rate=0.05)
    assert out["bear"][0] >= 0.05, out["bear"][0]


def test_连续多年负增长时资本开支比例会回升():
    """**这不是 bug。** I5（final-review-plan2a 终审）之后，公式化简为
    capex_ratio_t = (max(g_t,0)/fa_turnover + maint_rate)/(1+g_t)，不再有
    rev_prev/rev_now 的递推——旧版 docstring 声称这条测试能抓"递推顺序
    错误"，终审实跑两个突变体（删掉 rev_prev=rev_now；base_rev 写死 1.0）
    验证了旧实现里这套递推本来就不影响输出，声称落空。现在这条测试改为
    钉住化简后公式本身的形状：连续负增长时 g<0 → 分子恒为 maint_rate，
    只有分母 (1+g) 在缩小，g 越负、比例越高——单调走高是公式的直接推论，
    不是"上一年营收当基数"这类会被递推顺序搞错的东西。
    """
    out = C._capex_from_growth(
        growth3={"bear": [-0.05, -0.10, -0.15]}, fa_turnover=2.0, maint_rate=0.05)
    bear = out["bear"]
    # 逐位核算：maint_rate/(1+g) = 0.05/0.95, 0.05/0.90, 0.05/0.85
    assert bear == pytest.approx([0.0526, 0.0556, 0.0588], abs=1e-4)
    assert bear[0] < bear[1] < bear[2], bear


def test_各年比例只依赖当年增速与更早年份无关():
    """I5：capex_ratio_t 代数上只是 g_t 的函数——分子分母里的 rev_prev 恒等
    约掉，因此不管上一年增速是什么，只要当年 g 相同，比例就必须相同。删掉
    `base_rev` 参数之后，原 `test_逐年以上一年营收为基数` 名字里"基数"这个
    概念已经不存在了，改写成直接钉住这份代数化简，而不是继续用一个已经
    不成立的理由（"用上一年营收当基数"）去解释一个恰好也算得对的数字。
    """
    seq_a = C._capex_from_growth(growth3={"base": [0.20, 0.20]}, fa_turnover=2.0, maint_rate=0.05)
    seq_b = C._capex_from_growth(growth3={"base": [-0.30, 0.20]}, fa_turnover=2.0, maint_rate=0.05)
    assert seq_a["base"][1] == pytest.approx(seq_b["base"][1], abs=1e-9), (
        "第2年比例只应看第2年自己的增速 0.20，不该被第1年是 0.20 还是 -0.30 影响")
    # 第2年：g=0.20 → (0.20/2.0+0.05)/1.20 = 0.125，与基期无关
    assert abs(seq_a["base"][1] - 0.125) < 1e-9, seq_a["base"][1]


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
    不断言任何具体数值（行情/beta/σ 都会随时间漂移，断言数值只会
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


def _anchored_share3(cal):
    it = cal["interim"]
    if it.get("share3"):
        return tuple(it["share3"])
    return C.anchor_three(it["share_hist"], it["gm"], cal["judg"]["gm_stats"][1],
                           share_mean=it["share"])[0]


def test_工作簿里三档资本开支真的拉开(cal_dict, tmp_path):
    """本任务要断言的是工作簿里真正生效的那几格，不是中间变量——
    上一个任务的教训：只断言留痕字典，测不出函数是否真的把值写回。

    C2（final-review-plan2a 终审）之后：夹具（600183）锚定默认开启，首个
    预测年的三档 growth 已不是这里注入的 0.45/0.20/0.05，而是各档锚定后
    真正生效的增速（由 share3 反推）——直接拿注入值去核对会算错，必须
    复算同一份锚定覆盖再喂给 _capex_from_growth。
    """
    n = len(cal_dict["periods"]["proj"])
    bull = [0.45] * n; base = [0.20] * n; bear = [0.05] * n
    cal = _with_growth_scen(cal_dict, bull, base, bear)
    assert cal["interim"] and cal["interim"].get("share"), "前提：夹具确有已披露中期"
    p = tmp_path / "w.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1_假设"]
    m = _rowmap(ws)
    r = m[f"{cal['periods']['proj'][0]} 资本开支 / 营收"]
    bull_v, base_v, bear_v = (ws.cell(r, c).value for c in (2, 3, 4))
    assert bull_v > base_v > bear_v, (bull_v, base_v, bear_v)
    # 逐位核算：首年 growth 换成各档锚定后生效的增速，其余年份维持注入值
    share3 = _anchored_share3(cal)
    base_rev = cal["rev"][cal["periods"]["annual"][-1]]
    it = cal["interim"]
    growth3 = {"bull": list(bull), "base": list(base), "bear": list(bear)}
    for j, k in enumerate(("bull", "base", "bear")):
        growth3[k][0] = it["rev"] / share3[j] / base_rev - 1.0
    expect = C._capex_from_growth(
        growth3=growth3, fa_turnover=cal["judg"]["fa_turn"],
        maint_rate=cal["judg"]["maint_rate"])
    assert bull_v == pytest.approx(expect["bull"][0], abs=1e-4)
    assert base_v == pytest.approx(expect["base"][0], abs=1e-4)
    assert bear_v == pytest.approx(expect["bear"][0], abs=1e-4)
    # 生效增速本身必须确实拉开——不是巧合地相等（C2 病根之一）
    eff = [it["rev"] / share3[j] / base_rev - 1.0 for j in range(3)]
    assert eff[0] > eff[1] > eff[2], eff


def test_没有三档增速时首年仍按锚定生效增速拉开_其余年同值(cal_dict, tmp_path):
    """上游没给 growth_scen 时，_scen 的 fallback 是 JU['growth']（单序列），
    第2年起三档营收增速同值，资本开支也同值——**但首个预测年不是这样**：
    锚定默认开启（开关=1），首年真正生效的增速由各档 share3 反推，三档
    本就不同，capex 必须跟着拉开。C2 之前的假设"三档 fallback 到同一路径
    时 capex 也必须同值"，在锚定生效的年份从一开始就是错的——这正是终审
    实测复现的场景（600183：三档都按 13.74% 算 capex，真正生效的是 Bull
    50.04% / Base 39.85% / Bear 28.59%）。
    """
    cal = copy.deepcopy(cal_dict)
    assert "growth_scen" not in cal["judg"]
    assert cal["interim"] and cal["interim"].get("share"), "前提：夹具确有已披露中期"
    p = tmp_path / "w.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1_假设"]
    m = _rowmap(ws)
    proj = cal["periods"]["proj"]
    r0 = m[f"{proj[0]} 资本开支 / 营收"]
    bull0, base0, bear0 = (ws.cell(r0, c).value for c in (2, 3, 4))
    assert bull0 > base0 > bear0, (bull0, base0, bear0)
    if len(proj) > 1:
        r1 = m[f"{proj[1]} 资本开支 / 营收"]
        bull1, base1, bear1 = (ws.cell(r1, c).value for c in (2, 3, 4))
        assert bull1 == pytest.approx(base1, abs=1e-6)
        assert base1 == pytest.approx(bear1, abs=1e-6)


def test_C2实测复现_600183锚定生效增速对应的资本开支(cal_dict, tmp_path):
    """终审实跑复现（final-review-plan2a.md C2）：600183 锚定开启、未注入
    growth_scen 时，首年真正生效的增速是 Bull +50.04% / Base +39.85% /
    Bear +28.59%（由 share3=(0.446, 0.4785, 0.5204) 与已披露中期营收
    190.2561176613 反推），对应资本开支应为 Bull 16.43% / Base 14.55% /
    Bear 12.13%——修复前，三档都按被标注"不生效"的 13.74% 算，Base 档少计
    约 25.2 亿资本开支，单向利好估值。这里同时给出硬编码字面值（防止
    _capex_from_growth 本身被改坏）和用当前夹具动态复算的版本（防止两边
    对同一个 bug 各自维护一份、悄悄分叉）。
    """
    cal = cal_dict
    it = cal["interim"]
    base_rev = cal["rev"][cal["periods"]["annual"][-1]]
    share3 = _anchored_share3(cal)
    eff = [it["rev"] / share3[j] / base_rev - 1.0 for j in range(3)]
    assert eff == pytest.approx([0.5004, 0.3985, 0.2859], abs=1e-3), eff
    out = C._capex_from_growth(
        growth3={"bull": [eff[0]], "base": [eff[1]], "bear": [eff[2]]},
        fa_turnover=cal["judg"]["fa_turn"], maint_rate=cal["judg"]["maint_rate"])
    assert out["bull"][0] == pytest.approx(0.1643, abs=1e-3)
    assert out["base"][0] == pytest.approx(0.1455, abs=1e-3)
    assert out["bear"][0] == pytest.approx(0.1213, abs=1e-3)
    # 端到端：工作簿里首年那一格必须等于这份复算值，不是中间变量
    p = tmp_path / "w.xlsx"
    B.build(copy.deepcopy(cal), str(p))
    ws = load_workbook(p)["1_假设"]
    m = _rowmap(ws)
    r = m[f"{cal['periods']['proj'][0]} 资本开支 / 营收"]
    bull_v, base_v, bear_v = (ws.cell(r, c).value for c in (2, 3, 4))
    assert bull_v == pytest.approx(out["bull"][0], abs=1e-4)
    assert base_v == pytest.approx(out["base"][0], abs=1e-4)
    assert bear_v == pytest.approx(out["bear"][0], abs=1e-4)


def test_share3只算一次两处用同一个值(cal_dict, tmp_path, monkeypatch):
    """C2 追加修复：build() 里「资本开支」小节与「三、锚定已披露中期」小节
    此前各自调用 anchor_three 现算一份 share3/gm_rest3——同一套输入、同一条
    规则，数值上恰好一致，但两处独立计算这件事本身就是分叉风险的自白（Plan 1
    commit 4049205「锚定三档规则只留一份，挪到 calibrate」整整一个任务就是
    为了消灭这种重复：两边各写一遍规则，早晚分叉成工作簿一个数、批准清单
    另一个数）。修复后两节共用同一个局部变量，anchor_three 在这条强制走
    "现算"分支（share3/gm_rest3 从夹具里剥掉，模拟旧版 calibrate 产出）的
    路径上，一次 build() 调用只应该被调用一次。"""
    cal = copy.deepcopy(cal_dict)
    assert cal["interim"] and cal["interim"].get("share"), "前提：夹具确有已披露中期"
    cal["interim"].pop("share3", None)
    cal["interim"].pop("gm_rest3", None)
    calls = []
    real = C.anchor_three
    def _counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(B._CAL, "anchor_three", _counting)
    p = tmp_path / "w.xlsx"
    B.build(cal, str(p))
    assert len(calls) == 1, (
        f"anchor_three 被调用 {len(calls)} 次——资本开支小节与锚定小节应共用"
        "同一份 share3/gm_rest3，只应现算一次")
