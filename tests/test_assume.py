"""AI 填 DCF 判断项。边界与三档拉开是这个模块的全部要害。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import assume
from ir_agent.assume import AssumeFailed, Case
from ir_agent.ledger import FactLedger

D = Decimal
AS_OF = date(2026, 9, 7)
BODY = "行业营收同比高于本司，份额在丢，因此基准增速取低于行业。"
Q = "份额在丢，因此基准增速取低于行业"


def _line(item="营收增速", y=1, bear="2%", base="6%", bull="10%", q=Q):
    return f"假设: {item} | 第{y}年 | {bear} | {base} | {bull} | 「{q}」"


def _full(years=2):
    out = []
    for y in range(1, years + 1):
        out.append(_line("营收增速", y, f"{y}%", f"{y+4}%", f"{y+8}%"))
        out.append(_line("毛利率", y, "12%", "14%", "16%"))
    return "\n".join(out)


def _ok(a, years=2):
    assume.check(a, years, ledger=FactLedger(), body=BODY, as_of=AS_OF)


# ── 边界：只填判断项 ─────────────────────────────────────────

def test_只认两个判断项():
    """费用率、周转天数、折旧率由 calibrate 从历史算出 —— 那些是对历史的
    度量不是判断，AI 去动只会把已经算对的东西改坏。"""
    assert set(assume.DRIVERS) == {"营收增速", "毛利率"}


def test_填校准项被驳回且说明原因():
    with pytest.raises(AssumeFailed) as e:
        assume.parse(_line(item="销售费用率"), 2)
    assert "由历史数据校准" in str(e.value)


# ── 三档必须拉开 ─────────────────────────────────────────────

def test_三档同值被驳回():
    """模板默认 Bull=Base=Bear，那等于没有情景 —— 拉不开的情景分析
    只是把同一个数抄三遍。"""
    a = assume.parse("\n".join([
        _line("营收增速", 1, "5%", "5%", "5%"),
        _line("毛利率", 1, "12%", "14%", "16%")]), 1)
    with pytest.raises(AssumeFailed, match="三档未拉开"):
        _ok(a, 1)


def test_顺序颠倒被驳回():
    a = assume.parse("\n".join([
        _line("营收增速", 1, "10%", "6%", "2%"),
        _line("毛利率", 1, "12%", "14%", "16%")]), 1)
    with pytest.raises(AssumeFailed, match="三档未拉开|严格递增"):
        _ok(a, 1)


def test_拉开则通过():
    _ok(assume.parse(_full(2), 2), 2)


# ── 每一年都要给 ─────────────────────────────────────────────

def test_漏掉某年被驳回():
    """漏掉的年份会沿用模板默认值，而那不是你的判断。"""
    a = assume.parse("\n".join([_line("营收增速", 1),
                                _line("毛利率", 1, "12%", "14%", "16%")]), 3)
    with pytest.raises(AssumeFailed, match="缺第"):
        _ok(a, 3)


def test_年份越界被驳回():
    with pytest.raises(AssumeFailed, match="超出预测期"):
        assume.parse(_line(y=9), 5)


# ── 依据 ─────────────────────────────────────────────────────

def test_缺依据被驳回():
    with pytest.raises(AssumeFailed, match="缺少可核验的依据"):
        assume.parse("假设: 营收增速 | 第1年 | 2% | 6% | 10% | 我的判断", 1)


def test_依据查无此句被驳回():
    a = assume.parse("\n".join([
        _line("营收增速", 1, q="行业景气全面回升"),
        _line("毛利率", 1, "12%", "14%", "16%")]), 1)
    with pytest.raises(AssumeFailed, match="不可核验"):
        _ok(a, 1)


# ── 解析容错（与四个定性模块同一套） ─────────────────────────

def test_容忍列表前缀与括号补注():
    a = assume.parse("1. " + _line(item="营收增速（判断项）"), 1)
    assert a.rows[0].driver == "growth"


def test_百分号与小数都认():
    a = assume.parse(_line(bear="0.02", base="0.06", bull="0.10"), 1)
    assert a.rows[0].bear == D("0.02")


# ── 写回 calibrate ───────────────────────────────────────────

def test_三档分别写回():
    a = assume.parse(_full(2), 2)
    a.labels = ["2026E", "2027E"]
    cal = {"judg": {"growth": [0.0, 0.0], "gm": [0.0, 0.0], "cagr3": 0.1}}
    out = assume.apply_to(cal, a)
    assert out["judg"]["growth_scen"]["bear"] == [0.01, 0.02]
    assert out["judg"]["growth_scen"]["bull"] == [0.09, 0.10]
    assert out["judg"]["growth"] == [0.05, 0.06]      # 选定档=中性
    assert cal["judg"]["growth"] == [0.0, 0.0], "不应就地改坏原 cal"


def test_列顺序对应BullBaseBear():
    """B=Bull C=Base D=Bear 与 CHOOSE($B$SCEN,...) 绑定。顺序写反会让
    「选悲观」选出乐观值，**而模型照样验算通过** —— 公式本身没错。"""
    a = assume.parse(_full(1), 1)
    assert a.path("growth", Case.BEAR)[0] < a.path("growth", Case.BASE)[0] \
        < a.path("growth", Case.BULL)[0]


# ── 待核实清单 ───────────────────────────────────────────────

def test_清单给人看得懂的表():
    a = assume.parse(_full(2), 2)
    a.code, a.labels = "600584", ["2026E", "2027E"]
    txt = a.describe()
    assert "需人工核实后方可建模" in txt
    assert "2026E" in txt and "悲观" in txt and "依据" in txt


def test_提示词点明边界与拉开():
    txt = assume.build_prompt({"g_stats": (0.1, 0.05, 0.0, 0.2), "cagr3": 0.12,
                               "gm_stats": (0.15, 0.02, 0.12, 0.18),
                               "gm_hist": [0.14]}, ["2026E"], "上下文")
    assert "你只填这两项" in txt and "严格拉开" in txt
    assert "不是理由" in txt          # 不许复述历史当依据


def test_连续失败则拒绝():
    class _C:
        def __init__(self): self.n = 0
        def __call__(self, p):
            self.n += 1
            return "假设: 营收增速 | 第1年 | 5% | 5% | 5% | 「x」"
    with pytest.raises(AssumeFailed, match="连续"):
        assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
                      ledger=FactLedger(), body=BODY, as_of=AS_OF)


# ── 理由必须进清单 ───────────────────────────────────────────

def test_理由被抽出来而不是丢掉():
    """**实测踩到。** 模型在第 6 字段写了真正有用的推理（「跑输行业且份额
    流失…基准显著低于行业中枢」），但原来只抽占位符、把散文丢掉 ——
    待核实清单上只剩一串数字和一串 key，人要判断「这个增速为什么是 3%」
    却无从判起。"""
    a = assume.parse(
        "假设: 营收增速 | 第1年 | -2% | 3% | 8% | "
        "跑输行业且份额流失，基准显著低于行业中枢 "
        "[[company.market_share_delta@2025FY]]", 1)
    assert "份额流失" in a.rows[0].note
    assert "[[" not in a.rows[0].note, "占位符不该混在理由里"


def test_理由出现在待核实清单的表格里():
    a = assume.parse(
        "假设: 营收增速 | 第1年 | -2% | 3% | 8% | "
        "份额在丢 [[company.market_share_delta@2025FY]]", 1)
    a.code, a.labels = "600584", ["2026E"]
    txt = a.describe()
    assert "理由" in txt and "份额在丢" in txt
    assert "company.market_share_delta" in txt


def test_不再倾倒模型的原始回复():
    """实测那一版把模型对**本项目源码**的元评论（「我看了一下
    assume.py 的校验逻辑…」）原样印进了待核实清单 —— 一份股票研究文档
    里讨论 Python 内部实现，对核实的人只是噪音。"""
    a = assume.parse("假设: 营收增速 | 第1年 | 1% | 2% | 3% | 「x」", 1)
    a.code, a.labels = "600584", ["2026E"]
    assert "理由原文" not in a.describe()


def test_提示词说清可引用的是简报正文():
    """摘要与简报是两段不同的文字，而 REPORT 依据只对简报校验 ——
    实测模型被驳回一次才摸索出来。"""
    txt = assume.build_prompt({"g_stats": (), "gm_stats": ()}, ["2026E"],
                              "摘要内容", brief="简报正文")
    assert "简报正文" in txt and "引文只能逐字出自" in txt
    assert "不是可引用的正文" in txt


# ── 跨阶段往返：依据必须活下来 ───────────────────────────────

def test_依据跨阶段存活():
    """**实测踩到，第二阶段被自己的门挡住。** 原来只存数值不存依据，
    读回来的假设一律「未提供任何依据」而拒绝建模 —— 门是对的，
    是我没把依据带过去。"""
    a = assume.parse(_full(1), 1)
    a.labels = ["2026E"]
    back, edits = assume.from_json(assume.to_json(a), code="600584")
    assert edits == []
    assert len(back.rows) == len(a.rows)
    assert back.rows[0].bases and back.rows[0].bases[0].quote == Q
    _ok(back, 1)                      # 读回来的能过校验


def test_读回后仍要过全部校验():
    """批准不等于豁免。"""
    a = assume.parse(_full(1), 1)
    a.labels = ["2026E"]
    d = assume.to_json(a)
    d["rows"][0]["bull"] = d["rows"][0]["base"]      # 人手改坏：三档不再拉开
    back, _ = assume.from_json(d)
    with pytest.raises(AssumeFailed, match="三档未拉开"):
        _ok(back, 1)


def test_人改过的数值要被报出来():
    """依据是为模型原来那个数写的，改了之后它未必还支撑新数字 ——
    代码判断不了，但必须让读者看见。"""
    a = assume.parse(_full(1), 1)
    a.labels = ["2026E"]
    d = assume.to_json(a)
    d["rows"][0]["base"] = "0.30"                    # 人手上调
    _, edits = assume.from_json(d)
    assert len(edits) == 1
    assert "营收增速" in edits[0] and "0.30" in edits[0]


def test_理由也跨阶段存活():
    a = assume.parse(
        f"假设: 营收增速 | 第1年 | 1% | 2% | 3% | 份额在丢 「{Q}」", 1)
    back, _ = assume.from_json(assume.to_json(a))
    assert "份额在丢" in back.rows[0].note


# ── Task 7：按驱动因子填写 ───────────────────────────────────

from ir_agent.drivers import Decomposition, Factor, Op


def _decomps():
    rev = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="capacity", label="产能", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])
    gm = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util_leverage", label="稼动率摊薄", unit="pp"),
        Factor(key="input_cost", label="上游成本", unit="pp")])
    return [rev, gm]


def _factor_lines(years=1):
    out = []
    for y in range(1, years + 1):
        out += [
            f"假设: 产能 | 第{y}年 | 5% | 10% | 14% | 新厂投产 「{Q}」",
            f"假设: 单价 | 第{y}年 | -6% | -2% | 1% | 竞争压价 「{Q}」",
            f"假设: 稼动率摊薄 | 第{y}年 | -2.5 | -1.0 | 0.5 | 扩产摊薄 「{Q}」",
            f"假设: 上游成本 | 第{y}年 | -1.5 | -0.5 | 0.3 | 基板涨价 「{Q}」"]
    return "\n".join(out)


def test_按因子解析():
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    assert {r.factor_key for r in a.rows} == {
        "capacity", "asp", "util_leverage", "input_cost"}


def test_未知因子被驳回并列出可选():
    bad = f"假设: 品牌力 | 第1年 | 1% | 2% | 3% | 「{Q}」"
    with pytest.raises(AssumeFailed) as e:
        assume.parse(bad, 1, decomps=_decomps())
    assert "产能" in str(e.value) and "单价" in str(e.value)


def test_缺某个因子被驳回():
    """漏掉的因子会沿用模板默认值，而那不是你的判断。"""
    partial = f"假设: 产能 | 第1年 | 5% | 10% | 14% | 「{Q}」"
    a = assume.parse(partial, 1, decomps=_decomps())
    with pytest.raises(AssumeFailed, match="单价|缺"):
        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
                     decomps=_decomps())


def test_合成营收增速与毛利率():
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    g, m = assume.synthesize(a, Case.BASE, base_margin=D("0.1415"))
    # 1.10 × 0.98 − 1 = 0.078
    assert abs(g[0] - D("0.078")) < D("1e-9")
    # 14.15% − 1.0pp − 0.5pp = 12.65%
    assert abs(m[0] - D("0.1265")) < D("1e-9")


def test_三档合成后仍然单调():
    """因子各自拉开不等于合成后拉开 —— 必须在合成结果上再验一次。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    lo, _ = assume.synthesize(a, Case.BEAR, base_margin=D("0.1415"))
    mid, _ = assume.synthesize(a, Case.BASE, base_margin=D("0.1415"))
    hi, _ = assume.synthesize(a, Case.BULL, base_margin=D("0.1415"))
    assert lo[0] < mid[0] < hi[0]


def test_合成后不单调则驳回():
    """构造一组因子: 各自三档拉开，但相乘后悲观反而高于中性。"""
    weird = "\n".join([
        f"假设: 产能 | 第1年 | 50% | 10% | 11% | 「{Q}」",   # 悲观档产能暴涨
        f"假设: 单价 | 第1年 | -1% | -2% | 1% | 「{Q}」"])
    d = [Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="capacity", label="产能", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])]
    a = assume.parse(weird, 1, decomps=d)
    a.decomps = d
    with pytest.raises(AssumeFailed, match="合成后"):
        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
                     decomps=d, base_margin=D("0.14"))


def test_提示词列出因子与单位():
    txt = assume.build_prompt({"g_stats": (), "gm_stats": ()}, ["2026E"],
                              "摘要", brief="简报", decomps=_decomps())
    assert "产能" in txt and "变化率" in txt
    assert "稼动率摊薄" in txt and "pp" in txt
    assert "pp 不能进乘法链" in txt


def test_无分解式时退回两标量模式():
    """降级路径必须还能走 —— 预研失败不该让整轮研究停摆。"""
    a = assume.parse(_full(1), 1)
    assert {r.driver for r in a.rows} == {"growth", "gm"}
    _ok(a, 1)


# ── Task 10：apply_to / to_driver_block 接线 ────────────────────
#
# 分解模式下 Row.driver 记的是分解目标（revenue/gross_margin），不是
# growth/gm —— apply_to 若还按旧键用 path() 过滤，会把 rows 全部滤空，
# 悄悄把 calibrate 算好的模板覆盖成空列表（下游 ashare_build 逐年取值
# 会直接 IndexError）。以下测试锁住分解模式下 apply_to 必须改用
# synthesize() 合成。

def _cal_for_apply(gm_hist=("0.1415",)):
    return {"judg": {"growth": [0.0], "gm": [0.0],
                     "gm_hist": [float(x) for x in gm_hist],
                     "cagr3": 0.1}}


def test_分解模式下apply_to按因子合成而非清空():
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.labels = ["2026E"]
    out = assume.apply_to(_cal_for_apply(), a)
    assert out["judg"]["growth"], "分解模式下 growth 不应被清空"
    assert out["judg"]["gm"], "分解模式下 gm 不应被清空"
    # 与 test_合成营收增速与毛利率 同一组因子：1.10×0.98−1=0.078；
    # 14.15%−1.0pp−0.5pp=12.65%（中性档）
    assert abs(D(str(out["judg"]["growth"][0])) - D("0.078")) < D("1e-6")
    assert abs(D(str(out["judg"]["gm"][0])) - D("0.1265")) < D("1e-6")


def test_分解模式下apply_to不改坏原cal():
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.labels = ["2026E"]
    cal = _cal_for_apply()
    assume.apply_to(cal, a)
    assert cal["judg"]["growth"] == [0.0]
    assert cal["judg"]["gm"] == [0.0]


def test_分解模式下apply_to产出三档且拉开():
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.labels = ["2026E"]
    out = assume.apply_to(_cal_for_apply(), a)
    lo = out["judg"]["growth_scen"]["bear"][0]
    mid = out["judg"]["growth_scen"]["base"][0]
    hi = out["judg"]["growth_scen"]["bull"][0]
    assert lo < mid < hi


def test_to_driver_block产出结构():
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.labels = ["2026E"]
    cal = assume.apply_to(_cal_for_apply(), a)
    drv = assume.to_driver_block(a, cal)
    assert set(drv) == {"revenue", "gross_margin"}

    rev = drv["revenue"]
    assert rev["op"] == "乘"
    assert [f["key"] for f in rev["factors"]] == ["capacity", "asp"]
    # **三档顺序固定为 bull/base/bear**，与工作簿 B/C/D 列绑定——
    # 顺序写反不会让验算失败，只是「选悲观」会选出乐观值。
    assert rev["values"]["bull"][0] == [0.14, 0.01]
    assert rev["values"]["base"][0] == [0.10, -0.02]
    assert rev["values"]["bear"][0] == [0.05, -0.06]
    assert "base_level" not in rev

    gm = drv["gross_margin"]
    assert gm["op"] == "加"
    assert abs(gm["base_level"] - 0.1415) < 1e-9


def test_to_driver_block基期不受apply_to合成结果污染():
    """base_level 必须是账本口径的基期毛利率（gm_hist 最后一项），不能
    读 apply_to 合成之后的 judg['gm'][0] —— 那已经是**含第一年 pp 增量**
    的年度水平（12.65%），若拿它当基期，1b_驱动分解 的公式会把第一年的
    pp 增量算两遍（先被吸进 base_level，又被工作表公式再加一次）。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.labels = ["2026E"]
    applied = assume.apply_to(_cal_for_apply(), a)
    assert abs(applied["judg"]["gm"][0] - 0.1265) < 1e-6, \
        "前置断言：apply_to 之后 gm[0] 已经是合成后的年度水平"
    drv = assume.to_driver_block(a, applied)
    assert abs(drv["gross_margin"]["base_level"] - 0.1415) < 1e-9


def test_json往返保留分解式与因子key():
    """to_json/from_json 若漏掉 decomps 或 factor_key，第二阶段读回的
    AssumptionSet 就退化成一堆没有 factor_key 的行 —— synthesize() 按
    factor_key 分组，空 key 的行会被整体滤掉，apply_to/to_driver_block
    全部拿到空列表，人批准的因子分解在第二阶段就悄悄消失了。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.labels = ["2026E"]
    back, edits = assume.from_json(assume.to_json(a), code="600584")
    assert edits == []
    assert len(back.decomps) == 2
    assert {d.target for d in back.decomps} == {"revenue", "gross_margin"}
    assert {r.factor_key for r in back.rows} == {
        "capacity", "asp", "util_leverage", "input_cost"}
    # 往返后仍能正确合成 —— 这是最终的验收标准，不是「字段存在」这么浅
    g, m = assume.synthesize(back, Case.BASE, base_margin=D("0.1415"))
    assert abs(g[0] - D("0.078")) < D("1e-9")
    assert abs(m[0] - D("0.1265")) < D("1e-9")


def test_json往返后check仍能正确校验():
    """run_valuation 读回 assumptions.json 后要用同一套 check()——若
    decomps 没有一起存下来，check() 会退回两标量分支，把因子模式的行
    误判成「营收增速缺第 1..N 年」而拒绝建模。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.labels = ["2026E"]
    back, _ = assume.from_json(assume.to_json(a), code="600584")
    assume.check(back, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
                 decomps=back.decomps, base_margin=D("0.1415"))


# ── 终审 Critical 修复（final-fix-A）──────────────────────────
#
# 下面四组对应终审报告的 C1~C4，另加 I5/I6/M1。每组先给出「修复前会
# 失败」的复现场景，再验证修复后的行为。


# ── C1：因子模式下 describe() 曾是空白页 ────────────────────────

def test_因子模式清单不再是空白页():
    """C1 实测复现：`describe()` 按 `DRIVERS`（growth/gm）分组，但因子
    模式下 `Row.driver` 记的是分解目标（revenue/gross_margin），两个 key
    永不相等——`got` 恒空，除标题外整页空白。人工批准是这条流程的核心
    关卡，清单空白等于这一关形同虚设。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.code, a.labels = "600584", ["2026E"]
    txt = a.describe()
    # 因子标签、单位、三档取值都要出现——不能只剩标题。
    for label in ("产能", "单价", "稼动率摊薄", "上游成本"):
        assert label in txt
    assert "变化率" in txt and "pp" in txt
    assert "新厂投产" in txt          # 理由
    assert Q in txt                   # 依据


def test_因子清单显示代码合成的结果():
    """C1 要求：不仅列出因子，还要显式标出该目标的**合成结果**——
    人批准的是合成后的营收增速/毛利率，不是一堆孤立的因子取值。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.base_margin = D("0.1415")
    a.code, a.labels = "600584", ["2026E"]
    txt = a.describe()
    assert "合成结果" in txt
    # 中性档：1.10×0.98−1=0.078；14.15%−1.0pp−0.5pp=12.65%
    assert "7.80%" in txt
    assert "12.65%" in txt


def test_因子清单遗漏因子必须可见():
    """硬约束「遗漏必须可见」：分解式里的因子没有任何一行取值，
    不能悄悄不显示——必须让人一眼看出这里空着。"""
    a = assume.AssumptionSet(code="600584", labels=["2026E"],
                             decomps=_decomps())
    txt = a.describe()
    assert "稼动率摊薄" in txt and "未填写" in txt


def test_两标量模式describe行为不变():
    a = assume.parse(_full(2), 2)
    a.code, a.labels = "600584", ["2026E", "2027E"]
    txt = a.describe()
    assert "需人工核实后方可建模" in txt
    assert "2026E" in txt and "悲观" in txt and "依据" in txt


# ── I7：营收一节被毛利率基期连坐 ─────────────────────────────
#
# `_synthesized()` 此前只要**任一**分解式缺基期就整体返回 None，于是
# `_describe_composed` 对每一个目标都打印「基期毛利率未知」——包括
# 营收。营收是乘法链 Π(1+r)−1，根本不需要毛利率基期，完全算得出来；
# 在营收标题下说「基期毛利率未知」本身就是误导。降级路径恰恰是人想
# 搞清楚出了什么事的时候，这时候该显示的东西更不该丢。

def test_营收合成结果不受毛利率缺基期连坐():
    """实测复现：base_margin 未知时，修复前营收一节下面也会打印
    「基期毛利率未知，暂无法显示合成后的毛利率」——这条测试在修复前
    必须失败。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.base_margin = None                     # 毛利率基期未知
    a.code, a.labels = "600584", ["2026E"]
    txt = a.describe()

    rev_section, gm_section = txt.split("## 毛利率", 1)
    # 营收永远算得出来：合成结果（1.10×0.98−1=7.80%）必须显示，
    # 且不能出现「基期毛利率未知」这类文不对题的说明。
    assert "7.80%" in rev_section
    assert "基期毛利率未知" not in rev_section
    # 毛利率一节：缺基期，只在这一节显示说明文字。
    assert "基期毛利率未知" in gm_section


def test_营收缺基期时仍能过check的合成校验():
    """`_check_factors` 里 base_margin 缺失时用占位默认值合成校验——
    这条只是确认 describe() 的展示层修复不影响这条既有的校验路径。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
                decomps=_decomps(), base_margin=None)


# ── C2：历史回算残差是死代码 ─────────────────────────────────

from ir_agent.ledger import Fact, Method


def _put(led, key, period, value):
    led.put(Fact(key=key, value=D(str(value)), unit="unit", period=period,
                 as_of=AS_OF, source_id="t", method=Method.COMPUTED))


def test_decide把reconcile的结果存进residuals():
    """C2 实测复现：`reconcile()` 在 `drivers.py` 之外没有任何调用点，
    `AssumptionSet.residuals` 声明后从未被赋值。这里验证 `decide()`
    真的跑了历史回算，而不是只是把字段摆在那里。"""
    led = FactLedger()
    for k, p, v in [("capacity", "2023FY", 100), ("capacity", "2024FY", 112),
                    ("asp", "2023FY", 10), ("asp", "2024FY", 9.8),
                    ("revenue", "2023FY", 1000), ("revenue", "2024FY", 1097)]:
        _put(led, k, p, v)
    from ir_agent.drivers import Decomposition, Factor, Op as OpEnum
    d = [Decomposition(target="revenue", op=OpEnum.MULTIPLY, factors=[
        Factor(key="capacity", label="产能", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])]

    def client(_prompt):
        return "\n".join([
            f"假设: 产能 | 第1年 | 5% | 10% | 14% | 新厂投产 「{Q}」",
            f"假设: 单价 | 第1年 | -6% | -2% | 1% | 竞争压价 「{Q}」"])

    a = assume.decide(client, {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
                      ledger=led, body=BODY, as_of=AS_OF, code="600584",
                      decomps=d)
    assert "revenue" in a.residuals
    res = a.residuals["revenue"]["residuals"]
    assert [r.period for r in res] == ["2024FY"]
    # 1.12×0.98−1=0.0976；实际 1097/1000−1=0.097；残差 = −0.0006
    assert abs(res[0].composed - D("0.0976")) < D("1e-9")
    assert abs(res[0].gap - D("-0.0006")) < D("1e-9")
    assert a.residuals["revenue"]["no_history"] == []
    assert "历史回算残差" in a.describe()
    assert "不作为通过" in a.describe()


def test_历史值取不到标为无历史校验而不是静默跳过():
    """设计文档硬性要求：因子历史值取不到要标『无历史校验』，
    绝不静默跳过。"""
    from ir_agent.drivers import Decomposition, Factor, Op as OpEnum
    led = FactLedger()
    for k, p, v in [("capacity", "2023FY", 100), ("capacity", "2024FY", 112),
                    ("revenue", "2023FY", 1000), ("revenue", "2024FY", 1097)]:
        _put(led, k, p, v)
    # 单价 asp 账本里完全没有历史。
    d = [Decomposition(target="revenue", op=OpEnum.MULTIPLY, factors=[
        Factor(key="capacity", label="产能", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])]

    def client(_prompt):
        return "\n".join([
            f"假设: 产能 | 第1年 | 5% | 10% | 14% | 新厂投产 「{Q}」",
            f"假设: 单价 | 第1年 | -6% | -2% | 1% | 竞争压价 「{Q}」"])

    a = assume.decide(client, {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
                      ledger=led, body=BODY, as_of=AS_OF, code="600584",
                      decomps=d)
    assert a.residuals["revenue"]["no_history"] == ["单价"]
    txt = a.describe()
    assert "无历史校验" in txt and "单价" in txt


# ── C3：毛利率基期取到中报，约 32% 估值误差 ───────────────────

def test_base_margin取最近完整年度不是中报():
    """C3 实测复现（用真实缓存 /tmp/cj/cal_600584.pkl 同量级重建）：
    `gm_hist` 末项是 2026H1 中报（15.15%），最近一个完整年度其实是
    2025A（14.15%）。修复前 `_base_margin_of` 直接取 `gm_hist[-1]`，
    取到中报，在该模型上约相当于 32% 的估值误差。"""
    judg = {"g_hist": [0.1069, -0.1215, 0.2124, 0.0809],   # 4 → 5 个年度
           "gm_hist": [0.1841, 0.1704, 0.1365, 0.1306, 0.1415, 0.1515]}
    got = assume._base_margin_of(judg)
    assert got == D("0.1415"), f"应取最近完整年度 0.1415，取到了 {got}"


def test_base_margin无中报时仍取最后一项():
    """回归：没有中报（gm_hist 长度与年度数一致）时行为不变。"""
    judg = {"g_hist": [0.10, 0.08], "gm_hist": [0.18, 0.17, 0.14]}
    assert assume._base_margin_of(judg) == D("0.14")


def test_base_margin与full用的是同一个数():
    """C3 要求：直接断言 `full.py` 与 `to_driver_block`/`apply_to` 用的
    是同一个数——不能只测 assume 这一侧，两处各算各的才是这个 bug
    真正的危害所在（full.py 用账本年度值，assume 曾经用 gm_hist 末项，
    分歧只是因为这个值最终只喂进一个对基期免疫的单调性校验才没暴露）。
    """
    from ir_agent import full
    judg = {"g_hist": [0.1069, -0.1215, 0.2124, 0.0809],
           "gm_hist": [0.1841, 0.1704, 0.1365, 0.1306, 0.1415, 0.1515]}
    led = FactLedger()
    _put(led, "gross_margin", "2025FY", "0.1415")
    got_full = full._fact_value(led, "gross_margin", "2025FY", AS_OF)
    got_assume = assume._base_margin_of(judg)
    assert got_full == D("0.1415")
    assert D(str(got_full)) == got_assume, \
        "full.py 与 assume._base_margin_of 必须用同一个数，否则第一" \
        "阶段判断项与第二阶段建模的基期口径就各算各的"


# ── I6：_base_margin_of 的 fallback 会重新引入 C3 修掉的双重计入 ──

def test_gm_hist为空时报错而不是静默复用gm0():
    """I6：fallback 到 `judg['gm'][0]` 危险在于 —— `apply_to` 先跑、
    `to_driver_block` 后跑时，`gm[0]` 已经是「基期 + 第一年 pp 增量」
    的合成结果，拿它当基期会把第一年的增量在工作表公式里再加一遍。
    今天走不到这条路（calibrate 正常产出时 gm_hist 不会空），但这是
    「以后有人改了 calibrate 就静默算错钱」的形状，fallback 必须改成
    抛错。"""
    with pytest.raises(AssumeFailed, match="gm_hist"):
        assume._base_margin_of({"gm": [0.20]})
    with pytest.raises(AssumeFailed):
        assume._base_margin_of({})


# ── C4：pp 因子写成百分号被静默缩小 100 倍 ───────────────────

def test_pp因子写百分号被拒绝():
    """C4 实测复现：`_pct('-3pp') == -3`（贡献 -3pp，正确），但
    `_pct('-3%') == -0.03`（贡献 -0.03pp，缩小 100 倍）。单调性校验、
    闭合校验都不会抓住这个错——因子各自的三档依旧严格拉开，只是
    合成结果几乎不动。必须在解析时就把带 % 的字面量挡下来。"""
    bad = f"假设: 稼动率摊薄 | 第1年 | -3% | -1 | 0.5 | 「{Q}」"
    with pytest.raises(AssumeFailed, match="pp"):
        assume.parse(bad, 1, decomps=_decomps())


def test_pp因子不带百分号正常解析():
    """回归：既有写法（纯数字、"-3pp" 后缀）不受影响。"""
    ok = f"假设: 稼动率摊薄 | 第1年 | -3 | -1 | 0.5 | 「{Q}」"
    a = assume.parse(ok, 1, decomps=_decomps())
    assert a.rows[0].bear == D("-3")


def test_c4修复后pp显示为pp不是百分号():
    """C4 报告特别指出的陷阱：修好 C1 之后，`describe()` 若仍用 `:.2%`
    格式化 pp 值，`-0.03` 会显示成 "-3.00%"，和正确的 `-3`（应显示
    "-3.00pp"）看起来一模一样，人从清单上根本发现不了。pp 必须按原始
    数字 + pp 后缀显示。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    a.code, a.labels = "600584", ["2026E"]
    txt = a.describe()
    assert "-1.00pp" in txt
    assert "-2.50pp" in txt


# ── I5：pp 的参照点在提示词里没定义 ───────────────────────────

def test_提示词说明pp相对基期是累计值():
    """I5 实测复现：`compose` 的 ADD_PP 是「基期 + Σpp」，基期对每个
    预测年都是同一个数，所以第 3 年的 pp 必须是相对基期的**累计**变动，
    不是相对上一年的变动——提示词此前只说「一律填 pp」，没定义参照点。
    模型若按逐年变动理解，填「每年 −1」意在逐年下台阶，实际算出的却是
    三年同一个 base−1pp，且不会报错。"""
    txt = assume._factor_head(_decomps(), {})
    assert "累计" in txt and "基期" in txt


# ── M1：_NUM 不认 Unicode 减号，符号被悄悄翻转 ─────────────────

def test_全角减号不会被吞掉导致符号翻转():
    """M1：`'−3pp'`（Unicode 减号 U+2212，设计文档全文用的就是这个
    字符）此前被解析成 `+3`——全负号那几档会被单调性校验抓住，但
    「−2% | 3% | 8%」这类只有悲观档为负的写法仍然单调，会静默通过。"""
    assert assume._pct("−3pp") == D("-3")
    assert assume._pct("−3%") == D("-0.03")


def test_全角减号在标量模式下解析出正确符号():
    a = assume.parse(_line("营收增速", 1, bear="−2%", base="3%", bull="8%"), 1)
    assert a.rows[0].bear == D("-0.02")
    assert a.rows[0].bear < a.rows[0].base < a.rows[0].bull


# ── I4：因子模式连续失败没有回落到两标量 ─────────────────────
#
# 因子模式要填 N 因子 × Y 年 × 3 档，比标量模式的 2×5×3 重数倍，失败
# 概率明显更高。修复前 `decide()` 在因子模式下连续 `max_attempts` 次
# 填不出合格的判断项就直接抛 `AssumeFailed`，调用方（`full._propose_
# assumptions`）捕获后只返回一句错误串——整个 DCF 判断项直接没有，
# 连 assumptions.md 都不写。硬约束「降级但继续、并如实说明降级原因」
# 在这一层缺位：能力边界不是故障，整轮研究不该因此停摆。

def test_因子模式连续失败后回落两标量模式():
    """实测复现：因子模式怎么喂都给不出合格的行（写了个分解式里根本
    不存在的因子名），修复前这里会直接抛 AssumeFailed，调用方拿到的
    只有一句错误串，整段判断项凭空消失——这条测试在修复前必须失败。"""
    class _C:
        def __call__(self, p):
            if "本次按驱动因子分解填写" in p:
                # 因子模式无论怎么重试都给不出合法的因子名
                return "假设: 不存在的因子 | 第1年 | 1% | 2% | 3% | 「x」"
            return _full(1)     # 退回两标量模式后能正常给出

    a = assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
                      ledger=FactLedger(), body=BODY, as_of=AS_OF,
                      decomps=_decomps())
    assert a.decomps == [], "应已退回两标量模式，不应再带着分解式"
    assert {r.driver for r in a.rows} == {"growth", "gm"}
    assert a.degraded, "必须如实写明降级原因，不能假装成功"
    assert "因子" in a.degraded


def test_因子模式降级原因出现在待核实清单里():
    """降级但继续——损失的是精度，不是担保，读者必须在清单上看到这件事，
    而不是打开 assumptions.md 却看不出这轮其实是降级产出的。"""
    class _C:
        def __call__(self, p):
            if "本次按驱动因子分解填写" in p:
                return "假设: 不存在的因子 | 第1年 | 1% | 2% | 3% | 「x」"
            return _full(1)

    a = assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
                      ledger=FactLedger(), body=BODY, as_of=AS_OF,
                      decomps=_decomps())
    a.code, a.labels = "600584", ["2026E"]
    txt = a.describe()
    assert a.degraded in txt


def test_两标量模式本身连续失败仍照常拒绝():
    """回归：没有 decomps（本来就是两标量模式）时，连续失败不该被当成
    "因子模式失败"再退化一次——那样会造成无限递归或行为混乱，必须原样
    拒绝。"""
    class _C:
        def __call__(self, p):
            return "假设: 营收增速 | 第1年 | 5% | 5% | 5% | 「x」"
    with pytest.raises(AssumeFailed, match="连续"):
        assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
                      ledger=FactLedger(), body=BODY, as_of=AS_OF)
