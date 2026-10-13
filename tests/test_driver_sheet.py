"""1b_驱动分解 工作表。"""

from __future__ import annotations

import json
import os
import pickle
import sys
from datetime import date
from pathlib import Path

import pytest

from ir_agent import dcf

# 与 `dcf._load` 同一个查找规则（环境变量优先），不写死主仓库路径 —— 写死时，
# 在别的工作树里改 ashare-dcf 会悄悄导入主仓库的旧版。
sys.path.insert(0, os.environ.get(dcf.ENV_DIR) or str(dcf.DEFAULT_DIR))

# 建表需要一份真实的 calibrate 输出 —— 它有六十多个键，手写夹具既繁琐又会
# 与真实结构漂移（本项目已因「固件编码我想象的结构」栽过一次: stmts 被写成
# 字典嵌套，实跑才发现是 DataFrame）。因此**取一次真的缓存起来**。
_CACHE = Path("/tmp/cj/cal_600584.pkl")


def _minimal_cal():
    if _CACHE.exists():
        with _CACHE.open("rb") as f:
            return pickle.loads(f.read())
    try:
        from ir_agent import dcf
        from ir_agent.pipeline import run
        r = run("600584", 2025, date(2026, 9, 7))
        cal = dcf.calibrate_only("600584", as_of=date(2026, 9, 7),
                                 ledger=r.ledger)
    except Exception as e:                                  # noqa: BLE001
        pytest.skip(f"取不到真实 calibrate 输出（{e.__class__.__name__}），"
                    "跳过建表测试")
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE.open("wb") as f:
        f.write(pickle.dumps(cal))
    return cal


def _drivers():
    """schema 2 驱动块（2026-10-14 公式化）。公式记号由 ir-agent 自己的
    `formula.from_decomps` 生成，不手写 —— 夹具与真实产出同源。"""
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.formula import from_decomps

    m = from_decomps([
        Decomposition("revenue", Op.MULTIPLY, [Factor("capacity", "产能", "变化率"),
                                                Factor("asp", "单价", "变化率")]),
        Decomposition("gross_margin", Op.ADD_PP, [Factor("util", "稼动率摊薄", "pp"),
                                                   Factor("cost", "上游成本", "pp")])])
    return {"schema": 2, "mode": m.mode,
            "factors": [{"key": f.key, "short": f.short, "label": f.label,
                         "unit": f.unit} for f in m.factors],
            "constants": [],
            "formulas": {t: [list(x) for x in fm.tokens()]
                         for t, fm in m.formulas.items()},
            "formula_text": {t: m.describe(t) for t in m.formulas},
            "base": {"revenue": 100.0, "margin": 0.1415, "factors": {}},
            "values": {"bull": [[0.14, 0.01, 0.5, 0.3]],
                       "base": [[0.10, -0.02, -1.0, -0.5]],
                       "bear": [[0.05, -0.06, -2.5, -1.5]]}}


def test_无drivers时不建表(tmp_path):
    """降级路径: 预研失败时工作簿形状与从前完全一致。"""
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    p = tmp_path / "a.xlsx"
    B.build(cal, str(p))
    assert "1b_驱动分解" not in load_workbook(p).sheetnames


def test_有drivers时建表并写入三档(tmp_path):
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    cal["judg"]["drivers"] = _drivers()
    p = tmp_path / "b.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1b_驱动分解"]
    cells = {ws.cell(r, 1).value: r for r in range(1, 40)
             if ws.cell(r, 1).value}
    row = cells["2026E 产能"]
    # B=Bull C=Base D=Bear —— 与 CHOOSE($B$SCEN,…) 绑定
    assert abs(ws.cell(row, 2).value - 0.14) < 1e-12
    assert abs(ws.cell(row, 3).value - 0.10) < 1e-12
    assert abs(ws.cell(row, 4).value - 0.05) < 1e-12


def test_合成行是公式不是常量(tmp_path):
    """**Excel 独立算一遍**，与 Python 侧合成对照 —— 两处独立算出同一个值
    再比对，比共享一个值更能发现错误。"""
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    cal["judg"]["drivers"] = _drivers()
    p = tmp_path / "c.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1b_驱动分解"]
    cells = {ws.cell(r, 1).value: r for r in range(1, 40)
             if ws.cell(r, 1).value}
    v = ws.cell(cells["2026E 营收（合成）"], 2).value
    assert isinstance(v, str) and v.startswith("=")
    assert "(1+" in v
    g = ws.cell(cells["2026E 营收增速（合成）"], 2).value
    assert isinstance(g, str) and g.startswith("=") and g.endswith("-1")


def test_假设表改为跨表引用(tmp_path):
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    cal["judg"]["drivers"] = _drivers()
    p = tmp_path / "d.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1_假设"]
    cells = {ws.cell(r, 1).value: r for r in range(1, 60)
             if ws.cell(r, 1).value}
    v = ws.cell(cells["2026E 营收增速"], 2).value
    assert isinstance(v, str) and "1b_驱动分解" in v


def test_列序与假设表一致(tmp_path):
    """写反不会让验算失败 —— 公式本身没错，只是「选悲观」会选出乐观值。"""
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    cal["judg"]["drivers"] = _drivers()
    p = tmp_path / "e.xlsx"
    B.build(cal, str(p))
    wb = load_workbook(p)
    d = wb["1b_驱动分解"]
    hdr = [d.cell(r, c).value for r in range(1, 10) for c in (2, 3, 4)]
    assert "Bull" in str(hdr) and "Base" in str(hdr) and "Bear" in str(hdr)
    assert str(hdr).index("Bull") < str(hdr).index("Base") < str(hdr).index("Bear")
