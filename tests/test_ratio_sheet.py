# -*- coding: utf-8 -*-
"""7_财务比率与杜邦：中期列口径与负值显示。

**问题一**：中期列直接拿半年流量除以存量，得到的「净利率」还算对，但
「总资产周转率」「ROE」这类含流量÷存量的比率会系统性偏低约一半——读者
看到的是「今年效率腰斩」，实际只是少算了半年。

**问题二**：EBIT 利息保障倍数用 IFERROR 包着，EBIT 为负时返回空白，
读者看到的是「没有数据」，而真相是「利润不够付利息」——**最该看见的
那一格恰好被藏起来了**。
"""
import copy, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pickle
import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter as CL
import ashare_build as B

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cal_600183.pkl")


def _wb(tmp_path):
    cal = pickle.load(open(FIX, "rb"))
    out = str(tmp_path / "t.xlsx")
    B.build(cal, out)
    return load_workbook(out), cal


def test_中期列的流量类比率已年化(tmp_path):
    ws, _ = _wb(tmp_path)
    ws = ws["7_财务比率与杜邦"]
    hit = [c for row in ws.iter_rows() for c in row
           if c.value and isinstance(c.value, str) and "年化" in str(c.value)]
    assert hit, "中期列的流量类比率必须标明已年化，否则读者会当成全年口径"


def test_ROE中期列公式已按share倒数年化(tmp_path):
    """ROE 是「流量÷平均存量」，中期列须对分子（归母净利）乘以 1/share。

    fixture 的 share=0.4785 → 年化因子 1/0.4785≈2.0899，而不是固定的 2.0
    （固定 ×2 正是 `_wc` 注释里说会系统性带偏的做法）。
    """
    cal = pickle.load(open(FIX, "rb"))
    wb, _ = _wb(tmp_path)
    ws = wb["7_财务比率与杜邦"]
    row = [r for r in ws.iter_rows() if r[0].value == "ROE（归母，平均净资产）"]
    assert row, "找不到 ROE 行"
    R = cal["_rows"] if "_rows" in cal else None
    NH = 6  # 5 个年报 + 1 个中报，来自 fixture periods.labels
    itm_cell = row[0][1 + (NH - 1)]  # B=索引1 对应第0列，中期列是第 NH-1 列（0-based）
    f = itm_cell.value
    assert isinstance(f, str) and f.startswith("="), "ROE 中期列必须是活公式"
    factor = round(1.0 / cal["interim"]["share"], 6)
    assert str(factor) in f or f"{factor:.6f}" in f, (
        f"ROE 中期列公式未按 1/share={factor} 年化，公式为：{f}")
    # 年报列不应被年化——不能一刀切
    annual_cell = row[0][1]  # 第一个年报列
    af = annual_cell.value
    assert af and str(factor) not in af and f"{factor:.6f}" not in af, (
        "年报列的 ROE 不应被年化因子污染")


def test_DSO中期列分母已年化(tmp_path):
    """DSO 是「存量÷流量×365」，中期列须对分母（营收）乘以 1/share，
    而不是像旧实现那样直接拿半年营收算，导致天数虚高约一倍。
    """
    cal = pickle.load(open(FIX, "rb"))
    wb, _ = _wb(tmp_path)
    ws = wb["7_财务比率与杜邦"]
    row = [r for r in ws.iter_rows() if r[0].value == "应收周转天数 DSO"]
    assert row, "找不到 DSO 行"
    NH = 6
    itm_cell = row[0][1 + (NH - 1)]
    f = itm_cell.value
    factor = round(1.0 / cal["interim"]["share"], 6)
    assert isinstance(f, str) and (str(factor) in f or f"{factor:.6f}" in f), (
        f"DSO 中期列公式未按 1/share={factor} 年化分母，公式为：{f}")


def test_毛利率等流量比流量不年化(tmp_path):
    """毛利率、营业利润率、EBIT 利润率、归母净利率——本来就对，不能被一刀切年化。"""
    cal = pickle.load(open(FIX, "rb"))
    wb, _ = _wb(tmp_path)
    ws = wb["7_财务比率与杜邦"]
    factor = round(1.0 / cal["interim"]["share"], 6)
    for label in ("毛利率", "营业利润率", "EBIT 利润率", "归母净利率"):
        row = [r for r in ws.iter_rows() if r[0].value == label]
        assert row, f"找不到 {label} 行"
        NH = 6
        itm_cell = row[0][1 + (NH - 1)]
        f = itm_cell.value
        assert isinstance(f, str)
        assert str(factor) not in f and f"{factor:.6f}" not in f, (
            f"{label} 是流量÷流量，中期列不该被年化，公式为：{f}")


def test_总资产周转率三处口径一致(tmp_path):
    """总资产周转率在本表出现三处：营运效率区块独立展示一次、杜邦三因子②、
    杜邦五因子④。三处都是「流量÷平均存量」，中期列必须年化，且年化后必须
    是同一个数——不能出现「杜邦里一个数、营运效率区块另一个数」，那会让
    读者看到同名指标两个不同的值，只会认为表错了。

    这条测试要能在有人将来只改一处（例如只改了营运效率区块，漏了杜邦区块，
    或反过来）时变红：断言用的是公式原文的逐字相等，任何一处的年化因子或
    年化对象（分子/分母）与另外两处不一致都会导致失败。
    """
    wb, cal = _wb(tmp_path)
    ws = wb["7_财务比率与杜邦"]
    NH = cal["_rows"]["NH"]
    # 精确匹配三处指标行的标签（不用子串匹配，避免连表头说明文字里提到
    # 「总资产周转率」的那一整段说明也被算进来）。
    labels = {"总资产周转率", "② 总资产周转率 = 营收 / 平均总资产", "④ 总资产周转率"}
    rows = [r for r in ws.iter_rows() if r[0].value in labels]
    assert len(rows) == 3, f"预期三处「总资产周转率」（营运效率/杜邦三因子②/杜邦五因子④），实际找到 {len(rows)} 处"
    itm_formulas = [r[1 + (NH - 1)].value for r in rows]
    assert all(isinstance(f, str) and f.startswith("=") for f in itm_formulas), \
        f"总资产周转率三处中期列都应是活公式，实际：{itm_formulas}"
    assert len(set(itm_formulas)) == 1, (
        "总资产周转率三处中期列公式不一致——同一张表出现了两个数值不同的"
        f"「总资产周转率」：{itm_formulas}")
    factor = round(1.0 / cal["interim"]["share"], 6)
    assert all(str(factor) in f or f"{factor:.6f}" in f for f in itm_formulas), (
        f"总资产周转率中期列未按 1/share={factor} 年化，公式为：{itm_formulas}")


def test_固定资产周转率中期列已年化(tmp_path):
    """固定资产周转率＝营收(流量)/平均固定资产(存量)，与总资产周转率同类，
    也必须对分子年化，否则中期列会显示成腰斩的效率。"""
    wb, cal = _wb(tmp_path)
    ws = wb["7_财务比率与杜邦"]
    NH = cal["_rows"]["NH"]
    row = [r for r in ws.iter_rows() if r[0].value == "固定资产周转率"]
    assert row, "找不到固定资产周转率行"
    itm_cell = row[0][1 + (NH - 1)]
    f = itm_cell.value
    factor = round(1.0 / cal["interim"]["share"], 6)
    assert isinstance(f, str) and (str(factor) in f or f"{factor:.6f}" in f), (
        f"固定资产周转率中期列未按 1/share={factor} 年化，公式为：{f}")


def test_杜邦恒等式中期列成立(tmp_path):
    """杜邦三因子/五因子分解的连乘结果必须与直接计算的 ROE 相等（中期列也不
    例外）。这是把 brief 里「保住恒等式」这条约束写成可执行断言，而不是只
    靠人读公式判断——用 formulas 引擎真正把工作簿算一遍，取「★ 恒等式校验」
    那一行在中期列的数值，断言其等于 0（浮点误差内）。
    """
    import formulas
    cal = pickle.load(open(FIX, "rb"))
    out = str(tmp_path / "t_dupont.xlsx")
    B.build(cal, out)
    rr = cal["_ratio_rows"]
    NH = cal["_rows"]["NH"]
    col_idx = 2 + (NH - 1)  # 1-based 列号：B=2 是第一个历史列，中期是第 NH 列
    from openpyxl.utils import get_column_letter as CL
    col = CL(col_idx)
    sol = formulas.ExcelModel().loads(out).finish().calculate()
    fn = os.path.basename(out)

    def G(sheet, cell):
        v = sol.get(f"'[{fn}]{sheet}'!{cell}")
        assert v is not None, f"formulas 引擎未能算出 {sheet}!{cell}"
        return float(v.value[0, 0])

    chk3 = G("7_财务比率与杜邦", f"{col}{rr['chk3']}")
    chk5 = G("7_财务比率与杜邦", f"{col}{rr['chk5']}")
    assert abs(chk3) < 1e-6, f"三因子恒等式在中期列未成立：连乘−直接计算 = {chk3}"
    assert abs(chk5) < 1e-6, f"五因子恒等式在中期列未成立：连乘−直接计算 = {chk5}"


def test_利息保障倍数为负时不被藏起来(tmp_path):
    ws, _ = _wb(tmp_path)
    ws = ws["7_财务比率与杜邦"]
    row = [r for r in ws.iter_rows()
           if r[0].value == "EBIT 利息保障倍数"]
    assert row, "找不到利息保障倍数行"
    formulas = [c.value for c in row[0][1:] if c.value]
    assert formulas, "该行不能整行为空"
    assert not any('IFERROR' in f and '""' in f for f in formulas if isinstance(f, str)), \
        "IFERROR 回退成空字符串会把「利润不够付利息」这件事藏起来"
    assert any('利润不足' in f for f in formulas if isinstance(f, str)), \
        "应显式回退成「利润不足」，而不是让读者猜"


def _interim_col(cal):
    NH = cal["_rows"]["NH"]
    return CL(2 + (NH - 1))


def _proj_col(cal, i=0):
    NH = cal["_rows"]["NH"]
    return CL(2 + NH + i)


def _eval_cell(path, sheet, cell):
    import formulas
    sol = formulas.ExcelModel().loads(path).finish().calculate()
    fn = os.path.basename(path)
    v = sol.get(f"'[{fn}]{sheet}'!{cell}")
    assert v is not None, f"formulas 引擎未能算出 {sheet}!{cell}"
    val = v.value[0, 0]
    return val.item() if hasattr(val, "item") else val


def _row_of(ws, label):
    row = [r for r in ws.iter_rows() if r[0].value == label]
    assert row, f"找不到「{label}」行"
    return row[0][0].row


# ── C3（final-review-plan2a 终审）：成长性三行中期列显示隐含全年增速估计 ──

def test_成长性三行中期列不再是荒谬的半年比全年(tmp_path):
    """终审实测复现：600183 中期列改前显示 −33.1%/−1.4%（半年直接除以上一
    整年，分子分母期间长度不同，本来就没意义），而真实 H1 同比其实是
    +50.0%/+130.4%——一家利润翻 1.3 倍的公司被显示成在萎缩。改用 ann="num"
    对分子年化后，中期列应变成正的、量级合理的隐含全年增速估计（不是真实
    同比，但至少方向与量级不再说反话）。用 formulas 引擎实算，不是 grep
    公式文本。
    """
    import formulas
    cal = pickle.load(open(FIX, "rb"))
    out = str(tmp_path / "t_growth.xlsx")
    B.build(cal, out)
    ws = load_workbook(out)["7_财务比率与杜邦"]
    col = _interim_col(cal)
    for label, lo, hi in (("营业收入增速", 0.2, 0.6), ("归母净利增速", 0.3, 2.0),
                           ("EBIT 增速", 0.3, 2.0)):
        r_idx = _row_of(ws, label)
        val = _eval_cell(out, "7_财务比率与杜邦", f"{col}{r_idx}")
        assert isinstance(val, (int, float)), f"{label} 中期列应算出数值，实际 {val!r}"
        assert lo < val < hi, (
            f"{label} 中期列={val:.4f}，改前会显示负值（腰斩/萎缩），"
            f"改后应是正的、量级合理的隐含全年增速估计")


def test_成长性三行未年化时是荒谬的负值(tmp_path):
    """对照组：证明"改前会显示反话"这件事本身是真的，而不是臆测——直接拿
    半年数除以上一整年（不年化，即改前的公式），600183 的营业收入增速会
    算出负值，尽管公司 H1 同比其实是正增长 50%。直接用 cal 里的原始财报
    数据算，不依赖 Excel 公式引擎，两条测试各自独立地钉住"改前/改后"。
    """
    cal = pickle.load(open(FIX, "rb"))
    it = cal["interim"]
    base_rev = cal["rev"][cal["periods"]["annual"][-1]]
    raw = it["rev"] / base_rev - 1   # 改前公式：半年直接除以上一整年，不年化
    assert raw < 0, f"对照：未年化的半年/整年比值应为负（改前的错误行为），实际={raw}"
