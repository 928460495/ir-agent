"""AI 填 DCF 判断项。边界与三档拉开是这个模块的全部要害。"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from ir_agent import assume, gaps
from ir_agent.assume import AssumeFailed, Case
from ir_agent.ledger import FactLedger
from ir_agent.sources.external import Tier
from ir_agent.sources.web import Finding

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
    # C1（终审复核）后 _base_margin_of 从 cal["periods"]["annual"] 数
    # 年度期数，不再从 g_hist 反推——这里给一个与 gm_hist 长度一致、
    # 每期 rev/cogs 都有值的最小 periods，保证数出来的年度期数正好
    # 等于 gm_hist 的长度（没有中报，也没有缺口）。
    annual = [f"p{i}" for i in range(len(gm_hist))]
    return {"periods": {"annual": annual},
           "rev": {p: 1.0 for p in annual},
           "cogs": {p: 1.0 for p in annual},
           "judg": {"growth": [0.0], "gm": [0.0],
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
#
# C1（终审复核）：`_base_margin_of` 现在接收完整的 `cal`（不再只是
# `judg`），因为年度期数改从 `cal["periods"]["annual"]` 数，不再从
# `judg["g_hist"]` 的长度反推——见下面 C1 一节。这里补一个最小的
# `cal` 构造器，`periods.annual` 给年度期间列表，`rev`/`cogs` 给每个
# 期间是否有值（决定该期间会不会被 gm_hist 的过滤条件计入）。

def _cal(annual, gm_hist, missing_rev=(), missing_cogs=(), g_hist=None):
    rev = {p: 1.0 for p in annual if p not in missing_rev}
    cogs = {p: 1.0 for p in annual if p not in missing_cogs}
    judg = {"gm_hist": list(gm_hist)}
    if g_hist is not None:
        judg["g_hist"] = list(g_hist)
    return {"periods": {"annual": list(annual)}, "rev": rev, "cogs": cogs,
           "judg": judg}


def test_base_margin取最近完整年度不是中报():
    """C3 实测复现（用真实缓存 /tmp/cj/cal_600584.pkl 同量级重建）：
    `gm_hist` 末项是 2026H1 中报（15.15%），最近一个完整年度其实是
    2025A（14.15%）。修复前 `_base_margin_of` 直接取 `gm_hist[-1]`，
    取到中报，在该模型上约相当于 32% 的估值误差。"""
    annual = ["20211231", "20221231", "20231231", "20241231", "20251231"]
    cal = _cal(annual, gm_hist=[0.1841, 0.1704, 0.1365, 0.1306, 0.1415, 0.1515],
              g_hist=[0.1069, -0.1215, 0.2124, 0.0809])   # 4 → 5 个年度
    got = assume._base_margin_of(cal)
    assert got == D("0.1415"), f"应取最近完整年度 0.1415，取到了 {got}"


def test_base_margin无中报时仍取最后一项():
    """回归：没有中报（gm_hist 长度与年度数一致）时行为不变。"""
    cal = _cal(["p0", "p1", "p2"], gm_hist=[0.18, 0.17, 0.14],
              g_hist=[0.10, 0.08])
    assert assume._base_margin_of(cal) == D("0.14")


def test_base_margin与full用的是同一个数():
    """C3 要求：直接断言 `full.py` 与 `to_driver_block`/`apply_to` 用的
    是同一个数——不能只测 assume 这一侧，两处各算各的才是这个 bug
    真正的危害所在（full.py 用账本年度值，assume 曾经用 gm_hist 末项，
    分歧只是因为这个值最终只喂进一个对基期免疫的单调性校验才没暴露）。

    这条测试当初没覆盖「账本里混有一致预期的预测期」这个场景——
    C3 之后又实测复现了一次分叉：`full._latest_annual_period` 内部靠
    `ratios.annual_periods` 找「最近一个完整年度」，而 `annual_periods`
    此前只按披露时点过滤、不问期间是否已经结束，账本里一旦有
    consensus.* 在 2026/2027/2028FY 这类未来期间上的键，就会被
    误当成「最近的年度」取到 2028FY（对应的 gross_margin 事实并不
    存在，取到 None）。这里不再手工把 "2025FY" 传给 `_fact_value`，
    而是先跑 `_latest_annual_period` 拿到期间、再取值——这样才会真的
    走到 `annual_periods` 里去，复现（或验证修复）这条链路。
    """
    from ir_agent import full
    annual = ["20211231", "20221231", "20231231", "20241231", "20251231"]
    cal = _cal(annual, gm_hist=[0.1841, 0.1704, 0.1365, 0.1306, 0.1415, 0.1515],
              g_hist=[0.1069, -0.1215, 0.2124, 0.0809])
    led = FactLedger()
    _put(led, "gross_margin", "2025FY", "0.1415")
    # 一致预期对未来三个年度的 EPS——披露于 AS_OF 之前，合法可见，
    # 但这三个会计年度本身还没结束，不该被当成「最近一个完整年度」。
    for y in (2026, 2027, 2028):
        _put(led, "consensus.eps", f"{y}FY", "1.0")

    latest = full._latest_annual_period(led, AS_OF)
    assert latest == "2025FY", \
        f"预测期混进了年度期间列表，取到了 {latest}"
    got_full = full._fact_value(led, "gross_margin", latest, AS_OF)
    assert got_full is not None, "基期毛利率取到了 None——判断项清单会显示" \
        "「基期毛利率未知」，人批准时看不到合成后的毛利率"
    got_assume = assume._base_margin_of(cal)
    assert got_full == D("0.1415")
    assert D(str(got_full)) == got_assume, \
        "full.py 与 assume._base_margin_of 必须用同一个数，否则第一" \
        "阶段判断项与第二阶段建模的基期口径就各算各的"


# ── I4（终审复核）：--period H1 时清单的基期又变回中报 ───────────

def test_base_margin在periodH1下仍取最近完整年度():
    """I4 实测复现：`full.py` 里 `r.period = f"{{year}}{{period_suffix}}"`
    （`__main__.py` 允许 `--period H1/Q1-Q3/Q1`），`--period H1` 时
    `r.period` 是「2025H1」。修复前 `_fact_value(r.ledger,
    "gross_margin", r.period, as_of)` 直接把 `r.period` 传进去——
    这里手工验证：账本里 2025H1 确实存在一个不同的（中报）毛利率
    0.1515，若真传 "2025H1" 会取到它，与 `apply_to`/`to_driver_block`
    走 `assume._base_margin_of`（从 calibrate 年度历史序列取）拿到的
    0.1415 分叉——正是 C3 注释里「必须是同一个数，而实际不是」的
    原话在 `--period H1` 这个入口的复现。修复后 `_latest_annual_period`
    不跟着 `r.period` 走，无论 `r.period` 是不是 H1，都取最近一个
    完整年度。"""
    from ir_agent import full
    led = FactLedger()
    _put(led, "gross_margin", "2025FY", "0.1415")
    _put(led, "gross_margin", "2025H1", "0.1515")   # 中报，不该被取到

    latest = full._latest_annual_period(led, AS_OF)
    assert latest == "2025FY"
    got = full._fact_value(led, "gross_margin", latest, AS_OF)
    assert got == D("0.1415")

    # 确认账本里 "2025H1" 确实存在且是不同的值——不是因为账本里根本
    # 没有这个期间才"侥幸"没取错，而是修复后的代码根本不会把
    # r.period 传给 _fact_value。
    wrong = full._fact_value(led, "gross_margin", "2025H1", AS_OF)
    assert wrong == D("0.1515") and wrong != got


# ── C1（终审复核）：反推年度期数在上游明文容忍的场景下失效 ──────
#
# `ashare_data.assert_modelable` 明文容忍近 5 年缺 1 年营业成本
# （`have < len(annuals) - 1` 才拒绝），而 `gm_hist` 按「营收 *和*
# 营业成本」过滤、`g_hist` 只按「营收」过滤——两者独立过滤，「差 1」
# 这个前提被容忍缺口打破后，`len(g_hist) + 1` 反推出来的年度期数就
# 不对了。改为直接从 `cal["periods"]["annual"]` 数满足同一过滤条件
# 的期间个数，不再反推。

def test_base_margin缺一年营业成本不会取到中报():
    """C1 实测复现：`gm_hist` 因为缺 1 年营业成本而少一项（5 项，
    原本 6 项少了中间那年），`g_hist` 只看营收、不受影响（仍是 4
    项）。用 HEAD 版本的旧实现手算：`n_annual = len(g_hist) + 1 = 5`，
    `gm_hist[:5]` 会把 5 项全取上——其中最后一项其实是中报（0.1515），
    结果取到 0.1515 而不是最近完整年度 0.1415（与 C3 同量级的误差，
    且完全静默）。"""
    annual = ["20211231", "20221231", "20231231", "20241231", "20251231"]
    cal = _cal(annual, gm_hist=[0.1841, 0.1365, 0.1306, 0.1415, 0.1515],
              missing_cogs=["20221231"],
              g_hist=[0.1069, -0.1215, 0.2124, 0.0809])
    got = assume._base_margin_of(cal)
    assert got == D("0.1415"), f"应取最近完整年度 0.1415，取到了 {got}"


def test_base_margin营收缺失不会取到更早的年份():
    """C1 反方向复现：某一年营收缺失时，`g_hist`（只按营收过滤）会
    比这里手工构造的「正常情形」少掉更多项，而 `gm_hist` 只少了对应
    这一年的那一项。旧实现按 `len(g_hist) + 1` 反推会把年度期数算小
    （这里反推出 3），截断掉本该保留的最近年度，取到 `gm_hist[2]`
    （0.1306，2023 年）而不是最近完整年度 0.1415（2025 年）——不是
    「取到中报」，是取到了一个更早的年份，同样完全静默。"""
    annual = ["p0", "p1", "p2", "p3", "p4"]
    cal = _cal(annual, gm_hist=[0.1841, 0.1365, 0.1306, 0.1415, 0.1515],
              missing_rev=["p1"], g_hist=[0.10, 0.08])
    got = assume._base_margin_of(cal)
    assert got == D("0.1415"), f"应取最近完整年度 0.1415，取到了 {got}"


# ── I6：_base_margin_of 的 fallback 会重新引入 C3 修掉的双重计入 ──

def test_gm_hist为空时报错而不是静默复用gm0():
    """I6：fallback 到 `judg['gm'][0]` 危险在于 —— `apply_to` 先跑、
    `to_driver_block` 后跑时，`gm[0]` 已经是「基期 + 第一年 pp 增量」
    的合成结果，拿它当基期会把第一年的增量在工作表公式里再加一遍。
    今天走不到这条路（calibrate 正常产出时 gm_hist 不会空），但这是
    「以后有人改了 calibrate 就静默算错钱」的形状，fallback 必须改成
    抛错。"""
    with pytest.raises(AssumeFailed, match="gm_hist"):
        assume._base_margin_of({"judg": {"gm": [0.20]}})
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


# ── C2（终审复核）：同一因子同一年重复行，三处取值规则不一致 ─────
#
# 用 HEAD 版本（修复前）实测验证过的分叉：`产能` 第1年先给 1%，模型
# 在同一次回复里又给出「修正后」的 30%——`parse()` 不查重，两行都留
# 下来。`synthesize()`（清单合成、`apply_to`）用字典推导式，最后一行
# 覆盖，取到 30%；`to_driver_block()`（驱动「1b_驱动分解」工作表，
# 真正参与建模的地方）用 `next()`，取到第一行 1%。人在清单上批准的
# 是 30%，工作簿实际驱动估值的却是 1%——`check()` 的三档单调、闭合
# 校验全部通过，因为拉开/闭合看的是同一行的三档，不看重复行之间的
# 分歧。

def test_同一因子同一年重复行被驳回():
    """C2 实测复现：重试提示词里的「请修正后重新输出全部行」很容易
    诱发模型把修正前/修正后两版都留在同一次回复里。必须在解析阶段
    就拒绝，不能留给下游两处各自选一行、选出两个不同的数。"""
    bad = ("假设: 产能 | 第1年 | -1% | 1% | 3% | 「先给1%」\n"
           "假设: 产能 | 第1年 | -5% | 30% | 35% | 「修正后」\n")
    with pytest.raises(AssumeFailed, match="产能") as e:
        assume.parse(bad, 1, decomps=_decomps())
    assert "第 1 年" in str(e.value)


def _dup_rows():
    """`_check_factors`/`to_driver_block` 的纵深防御测试要跳过
    `_parse_factors` 直接构造重复行——模拟人手改 JSON 或未来有人放开
    查重之后，重复行仍然可能从别的路径混进 `AssumptionSet`。"""
    return [
        assume.Row(driver="revenue", year=1, bear=D("-0.01"), base=D("0.01"),
                  bull=D("0.03"), factor_key="capacity"),
        assume.Row(driver="revenue", year=1, bear=D("-0.05"), base=D("0.30"),
                  bull=D("0.35"), factor_key="capacity"),
        assume.Row(driver="revenue", year=1, bear=D("-0.06"), base=D("-0.02"),
                  bull=D("0.01"), factor_key="asp"),
        assume.Row(driver="gross_margin", year=1, bear=D("-2.5"), base=D("-1.0"),
                  bull=D("0.5"), factor_key="util_leverage"),
        assume.Row(driver="gross_margin", year=1, bear=D("-1.5"), base=D("-0.5"),
                  bull=D("0.3"), factor_key="input_cost"),
    ]


def test_check也拒绝重复行不只依赖parse():
    """C2 纵深防御：`check()` 在人工核实之后的第二阶段（`full.
    run_valuation` 里 `from_json` 读回）会被再跑一遍，那时的
    `AssumptionSet` 未必经过 `_parse_factors`——人手改 JSON 也可能
    引入重复行。`_check_factors` 不能假设上游一定已经查过重。"""
    a = assume.AssumptionSet(code="", labels=["2026E"], decomps=_decomps())
    a.rows = _dup_rows()
    with pytest.raises(AssumeFailed, match="产能"):
        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
                     decomps=_decomps(), base_margin=D("0.1415"))


def test_to_driver_block与synthesize取值规则一致():
    """C2 纵深防御：`to_driver_block` 此前用 `next()` 取第一行，
    `synthesize`/`_synthesized_for` 用字典推导式取最后一行——即便
    重复行绕过了 `check()`，两处至少不该各算各的。改成同一条『后写
    入覆盖先写入』规则之后，两处对同一份（含重复行的）数据取到同一
    个值。"""
    a = assume.AssumptionSet(code="", labels=["2026E"], decomps=_decomps())
    a.rows = _dup_rows()
    a.decomps = _decomps()
    g, _ = assume.synthesize(a, Case.BASE, D("0.1415"))
    cal = {"periods": {"annual": ["p0"]}, "rev": {"p0": 1.0},
          "cogs": {"p0": 1.0}, "judg": {"gm_hist": [0.1415]}}
    block = assume.to_driver_block(a, cal)
    capacity_base = block["revenue"]["values"]["base"][0][0]
    assert capacity_base == pytest.approx(0.30), \
        "to_driver_block 应与 synthesize 一样取最后一行（30%），不是 next() 的第一行（1%）"
    # 两处应合成出同一个营收增速——用同一份（含重复行的）取值。
    expected = (1 + 0.30) * (1 + (-0.02)) - 1
    assert float(g[0]) == pytest.approx(expected)


# ── I1（终审复核）：变化率因子单向单位守卫，裸数被静默吞下 ──────
#
# C4 只挡了 pp 因子里的字面量 %；变化率因子这一侧此前完全没有守卫——
# 裸数、pp 后缀、bp 后缀都会被 `_pct` 当成小数直接使用。pp 侧的错法
# 渲染成 "-300.00pp" 这种一眼可见的数，危害有限；变化率侧最危险的是
# 裸数 0.5：合成后渲染成 "50.00%"，对一个扩产故事完全看得过去，人
# 不会觉得异常。

def test_变化率因子裸数被拒绝():
    """I1 实测复现：修复前 `产能 | 第1年 | 0.5 | 0.5 | 0.6` 会被
    `_pct` 当成小数 0.5 直接使用，合成/显示成 50.00%——对一个扩产
    故事看起来完全正常，人根本看不出这里想表达的其实是 0.5%。"""
    bad = f"假设: 产能 | 第1年 | 0.5 | 0.5 | 0.6 | 「{Q}」"
    with pytest.raises(AssumeFailed, match="变化率"):
        assume.parse(bad, 1, decomps=_decomps())


def test_变化率因子写成pp后缀被拒绝():
    """I1 实测复现：`-3.5pp` 没有 %，会被当裸数 -3.5 直接使用，合成
    显示成 -350.00%——把 pp 误当变化率填的写法此前被静默吞下。"""
    bad = f"假设: 产能 | 第1年 | -3.5pp | -1pp | 0.5pp | 「{Q}」"
    with pytest.raises(AssumeFailed, match="变化率"):
        assume.parse(bad, 1, decomps=_decomps())


def test_变化率因子写成bp被拒绝():
    """I1 实测复现：`-350bp` 没有 %，会被当裸数 -350 直接使用——
    bp 与 % 不是同一套单位，不做隐式换算，必须显式拒绝并提示换算。"""
    bad = f"假设: 产能 | 第1年 | -350bp | -100bp | 50bp | 「{Q}」"
    with pytest.raises(AssumeFailed, match="bp"):
        assume.parse(bad, 1, decomps=_decomps())


def test_变化率因子全角百分号被规范化接受():
    """I1：全角 ％ 信息无损，等价于 ASCII %，直接规范化接受——不强迫
    模型改用半角，但要解析出正确的数值（修复前 `_NUM` 认不出全角 ％，
    会把 % 整个丢掉按裸数处理，`-3.5％` 被解析成 -3.5 = -350%）。"""
    ok = f"假设: 产能 | 第1年 | -6％ | -2％ | 1％ | 「{Q}」"
    a = assume.parse(ok, 1, decomps=_decomps())
    row = next(r for r in a.rows if r.factor_key == "capacity")
    assert row.bear == D("-0.06")


def test_pp因子全角百分号也被拒绝():
    """I1：pp 侧的守卫此前只认 ASCII %，全角 ％ 能绕过去（`-3％` 被
    当裸数 -3 直接使用，对 pp 而言"碰巧对"，但下一次全角换成
    `-3.5％` 就会变成 -3.5pp 与 -350% 傻傻分不清）。规范化后全角
    ％ 与半角 % 一视同仁，同样被拒绝。"""
    bad = f"假设: 稼动率摊薄 | 第1年 | -3％ | -1 | 0.5 | 「{Q}」"
    with pytest.raises(AssumeFailed, match="pp"):
        assume.parse(bad, 1, decomps=_decomps())


# ── I5：pp 的参照点在提示词里没定义 ───────────────────────────

def test_提示词说明pp相对基期是累计值():
    """I5 实测复现：`compose` 的 ADD_PP 是「基期 + Σpp」，基期对每个
    预测年都是同一个数，所以第 3 年的 pp 必须是相对基期的**累计**变动，
    不是相对上一年的变动——提示词此前只说「一律填 pp」，没定义参照点。
    模型若按逐年变动理解，填「每年 −1」意在逐年下台阶，实际算出的却是
    三年同一个 base−1pp，且不会报错。"""
    txt = assume._factor_head(_decomps(), {})
    assert "累计" in txt and "基期" in txt


# ── I2（终审复核）：提示词表只印状态词、不印原文片段 ────────────

def test_提示词表对年报披露印出原文片段不只印状态词():
    """I2 实测复现：`_factor_head` 此前只印 `fd.status`——模型看到
    「年报披露」会直接当成有数据可用去填数字。「年报披露/研报测算」
    只是文字命中，未必真给出了数值，甚至可能命中的是一句「未披露
    XX」（见 gaps.py 的 I2 修复）。提示词表必须把 `gaps.collect` 摘出
    来的原文片段一起摆出来，不能只让模型看一个状态词就代为下结论。"""
    from ir_agent.gaps import ANNUAL, FactorData
    d = _decomps()[0]
    f = d.factors[0]   # capacity/产能
    factor_data = {f.key: FactorData(
        factor=f, status=ANNUAL,
        note="「公司本年度未披露稼动率数据」——《管理层讨论与分析》"
             "（文字依据，是否给出具体数值待人工确认）")}
    txt = assume._factor_head([d], factor_data)
    assert "年报披露" in txt
    assert "未披露稼动率数据" in txt, \
        "提示词表必须带上原文片段，只印状态词会让模型把'未披露'读成'已核实'"


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


# ═══════════════════════════════════════════════════════════════
# final-fix-D：合并前最后一批发现
# ═══════════════════════════════════════════════════════════════

# ── C-a：两标量降级路径漏了因子侧早就有的重复行查重 ───────────────
#
# 因子模式连续 3 次被「请修正后重新输出全部行」驳回之后才会走到这条
# 降级路径——模型此时已经被连续训练成输出"修正前/修正后"两版。比
# 因子模式更糟：`path()` 把全部行按年份排序后直接返回一个扁平列表，
# `ashare_build.py` 按下标 `i`（不是按年份）取值，一条重复行会让它
# 后面每一年整体错位一格。

def test_两标量模式重复行被驳回():
    """C-a 实测复现：`_parse_scalar` 修复前不查重，"先给的" 与 "修正
    后" 两行会一起留下来，交给 `path()` 排序后返回，下游按下标取值时
    会整体错位。必须在解析阶段就拒绝。"""
    bad = "\n".join([
        _line("营收增速", 1, "2%", "6%", "10%"),
        _line("营收增速", 1, "2%", "30%", "40%", q="修正后"),
        _line("营收增速", 2, "3%", "7%", "11%"),
        _line("毛利率", 1, "12%", "14%", "16%"),
        _line("毛利率", 2, "12%", "14%", "16%")])
    with pytest.raises(AssumeFailed, match="营收增速") as e:
        assume.parse(bad, 2)
    assert "第 1 年" in str(e.value)


def test_两标量模式check也拒绝重复行不只依赖parse():
    """C-a 纵深防御：`check()` 在人工核实之后（`from_json` 读回）会被
    再跑一遍（`full.run_valuation`），那时的 `AssumptionSet` 未必经过
    `_parse_scalar`——人手改 JSON 也可能引入重复行。"""
    a = assume.AssumptionSet(code="", labels=["2026E"])
    a.rows = [
        assume.Row(driver="growth", year=1, bear=D("0.01"), base=D("0.02"),
                  bull=D("0.03")),
        assume.Row(driver="growth", year=1, bear=D("-0.05"), base=D("0.30"),
                  bull=D("0.35")),
        assume.Row(driver="gm", year=1, bear=D("0.12"), base=D("0.14"),
                  bull=D("0.16")),
    ]
    with pytest.raises(AssumeFailed, match="营收增速"):
        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)


def test_两标量模式重复行会让path按年份整体错位():
    """C-a：不拦住会造成的真实危害——`path()` 是这条降级路径唯一把
    数值交给 `apply_to` 的地方，重复行会让排序后的列表长度不对、
    内容错位，后面每一年拿到的其实是前一条重复行的数字（`ashare_
    build.py:87` 按下标 `i` 取值，不是按年份查找）。这里跳过 check()，
    直接验证这个危害本身，证明"必须在早于 path() 的地方拦下"不是
    纸上谈兵。"""
    a = assume.AssumptionSet(code="", labels=["2026E", "2027E"])
    a.rows = [
        assume.Row(driver="growth", year=1, bear=D("0.01"), base=D("0.06"),
                  bull=D("0.10")),
        assume.Row(driver="growth", year=1, bear=D("0.05"), base=D("0.30"),
                  bull=D("0.40")),
        assume.Row(driver="growth", year=2, bear=D("0.02"), base=D("0.07"),
                  bull=D("0.11")),
    ]
    got = a.path("growth", Case.BASE)
    assert got == [D("0.06"), D("0.30"), D("0.07")]
    assert got[1] != D("0.07"), \
        "人批准的第2年 0.07 被顶到了下标2，下标1本该是第2年却是重复行的0.30"


# ── I-a：一个空格就能绕过 I-1 的单位守卫 ─────────────────────────

def test_变化率因子数字与百分号间空格绕过守卫被堵住():
    """I-a 实测复现：修复前 `0.5 %`（数字与 % 间一个空格）能骗过守卫——
    守卫只查整段字符串里有没有 % 字符（有，只是隔了个空格），但
    `_pct` 的 `_NUM` 正则要求 % 紧贴数字，实际解析出的 token 是不带
    % 的裸数 "0.5"，被当成 50% 直接使用（且不会被守卫拦下，因为守卫
    看到字符串里"有 %"就放行了）。合成营收增速会因此暴涨，对一个
    扩产故事完全看得过去，人从清单上发现不了。修复后应正确解析成
    0.5%（=0.005），既不是 50%，也不是被拒绝。"""
    ok = f"假设: 产能 | 第1年 | 0.5 % | 0.5 % | 0.6 % | 「{Q}」"
    a = assume.parse(ok, 1, decomps=_decomps())
    row = next(r for r in a.rows if r.factor_key == "capacity")
    assert row.bear == D("0.005"), \
        f"应解析成 0.5%=0.005，不该是被当裸数吞下的 0.5（=50%），取到了 {row.bear}"


def test_变化率因子数字与百分号间空格规范化后能正确解析():
    """回归：真正想表达 0.5% 而只是多打了个空格时，规范化后应该正确
    解析出对应的小数，而不是被拒绝，也不是被错解成 50%。"""
    ok = f"假设: 产能 | 第1年 | -6 % | -2 % | 1 % | 「{Q}」"
    a = assume.parse(ok, 1, decomps=_decomps())
    row = next(r for r in a.rows if r.factor_key == "capacity")
    assert row.bear == D("-0.06")


def test_变化率因子裸数与不相干百分号不再绕过守卫():
    """I-a 实测复现：`2026年+5%` 整段确实含有 %，但 `_NUM.search`
    实际抓到的第一个数字是它前面的 "2026"（后面跟着"年"，不紧贴
    %），若守卫只查"字符串里有没有 %"就会被这个不相干的 % 放行，
    `_pct` 却会解析出裸数 2026（渲染成 202600%）。守卫必须复用
    `_pct` 自己会用到的那个 token 来判断，两处才是同一套判据。"""
    bad = f"假设: 产能 | 第1年 | 2026年+5% | -2% | 1% | 「{Q}」"
    with pytest.raises(AssumeFailed, match="变化率"):
        assume.parse(bad, 1, decomps=_decomps())


def test_变化率单位守卫不误伤已确认合法的写法():
    """回归清单：I-a 的修法（规范化数字与 % 间的空白 + 守卫复用
    _pct 实际解析出的 token）不能误伤这些已确认合法的写法。"""
    legal = ["0%", "+3%", "100%", "-100%", "约3%", "3%~5%",
            "1,000%", "10%(含新厂)", "-3.5％"]
    for lit in legal:
        line = f"假设: 产能 | 第1年 | {lit} | {lit} | {lit} | 「{Q}」"
        a = assume.parse(line, 1, decomps=_decomps())
        assert a.rows[0].factor_key == "capacity", f"{lit!r} 不应被拒绝"


# ── Minor-1：_base_margin_of 在新失败路径下报错文案错误 ──────────

def test_base_margin区分gm_hist为空与periods数不出年度():
    """Minor-1 实测复现：`_annual_gm_hist` 在两种截然不同的原因下都
    会返回空列表——`gm_hist` 本身是空的，或者 `gm_hist` 有值但 `cal`
    缺 `periods["annual"]`/`rev`/`cogs`，按同一过滤条件数出来的年度
    期数恰好是 0。修复前不管哪种原因都报「judg 里没有 gm_hist」——
    后一种情形下 gm_hist 明明在，排障会被指向错误的地方。"""
    with pytest.raises(AssumeFailed) as e:
        assume._base_margin_of({"judg": {"gm_hist": [0.14, 0.15]}})
    msg = str(e.value)
    assert "periods" in msg
    assert "judg 里没有 gm_hist" not in msg, \
        "gm_hist 明明有值，不该报「judg 里没有 gm_hist」"


# ── Minor-2 见 assume._annual_gm_hist 的文档字符串更新（无独立测试，
#    只是把引用一个自身有 bug 的上游行为改成如实表述） ─────────────


# ── Minor-3：年报原文片段里的 | 未转义，会打断提示词表 ────────────

def test_年报原文含竖线时被转义不会撑坏提示词表():
    """Minor-3 实测复现：`fd.note` 抄的是 gaps.collect 摘出来的原文
    片段，PDF 抽出的表格章节带竖线很常见——未转义时那一行会被 markdown
    表格解析器当成多出的列，单位这一列跟着错位。"""
    from ir_agent.gaps import ANNUAL, FactorData
    d = _decomps()[0]
    f = d.factors[0]
    factor_data = {f.key: FactorData(
        factor=f, status=ANNUAL,
        note="「产品|产能|产量…|1200|1350」——《产能情况》")}
    txt = assume._factor_head(_decomps(), factor_data)
    assert "产品|产能" not in txt, \
        "未转义的竖线会被 markdown 表格当成列分隔符"
    assert "产品\\|产能" in txt


# ── Minor-4：to_driver_block 缺行时抛裸 KeyError ─────────────────

def test_to_driver_block缺行时报出因子与年份而不是裸KeyError():
    """Minor-4 实测复现：`check()` 用 `len(a.labels)` 定年数，
    `to_driver_block` 用 `max(r.year)` 定年数——手改 JSON 把某个因子
    的年份改到超出 labels 范围时两者会错开：那个因子多出的年份把这里
    的 years 撑大，其它因子在那一年却没有取值。修复前这里直接抛裸
    KeyError，一路冒到 `run_valuation` 的兜底里显示成「估值
    （KeyError: (2, 'asp')）」，排障看不出是哪个因子哪一年。"""
    a = assume.AssumptionSet(code="", labels=["2026E"], decomps=_decomps())
    a.rows = [
        assume.Row(driver="revenue", year=1, bear=D("0.01"), base=D("0.02"),
                  bull=D("0.03"), factor_key="capacity"),
        assume.Row(driver="revenue", year=1, bear=D("-0.01"), base=D("0.00"),
                  bull=D("0.01"), factor_key="asp"),
        assume.Row(driver="revenue", year=2, bear=D("0.02"), base=D("0.03"),
                  bull=D("0.04"), factor_key="capacity"),
        # asp 第 2 年缺行——手改 JSON 把 capacity 的年份改到了 2，
        # asp 没有跟着改。
        assume.Row(driver="gross_margin", year=1, bear=D("-1"), base=D("0"),
                  bull=D("1"), factor_key="util_leverage"),
        assume.Row(driver="gross_margin", year=1, bear=D("-1"), base=D("0"),
                  bull=D("1"), factor_key="input_cost"),
    ]
    cal = {"periods": {"annual": ["p0"]}, "rev": {"p0": 1.0},
          "cogs": {"p0": 1.0}, "judg": {"gm_hist": [0.1415]}}
    with pytest.raises(AssumeFailed, match="单价") as e:
        assume.to_driver_block(a, cal)
    assert "第 2 年" in str(e.value)


# ── Minor-5：两标量降级模式没有任何单位守卫 ───────────────────────

def test_两标量模式bp字面量被拒绝():
    """Minor-5 实测复现：与 C-a 同一片薄弱区域——两标量降级模式此前
    完全没有单位守卫，`-350bp` 会被 `_pct` 当裸数 -350 直接吞下，
    渲染成 -35000%。"""
    bad = _line("营收增速", 1, "-350bp", "-100bp", "50bp")
    with pytest.raises(AssumeFailed, match="bp"):
        assume.parse(bad, 1)


def test_两标量模式小数记法不受因子侧必须带百分号的约束():
    """记录一个刻意的判断：没有照抄因子侧"必须带 %"的完整守卫。小数
    记法（0.3 = 30%）是这条路径本就认可的既有约定（见
    `test_百分号与小数都认`），营收增速/毛利率本身只有一套单位（都是
    读作"这个比例"），不像因子侧那样存在 pp/% 两套单位真会相差
    100 倍——没有第二套单位可供混淆，谈不上"必须带 %"，所以只加了
    bp 这个真正跨单位、会被静默吞下的窄口径守卫。"""
    a = assume.parse(_line("营收增速", 1, "0.02", "0.06", "0.10"), 1)
    assert a.rows[0].bear == D("0.02")


# ═══════════════════════════════════════════════════════════════
# fix-factor-basis：因子模式「依据」补两条合法出口
#
# 契约缺口：正文允许「无数据的因子取值是估计」，但 `_basis`（两标量）
# 与因子模式原来共用的同一套逐字核验只认账本占位符与正文逐字引用两种
# 写法——对一个正文没提过、账本也没有的因子，根本不存在合法的依据
# 写法。真实联网跑长电科技 600584 时，方法论预研分出的 9 个因子有 7
# 个正文一次都没提过，模型连续 3 次被拒后只能整体退回两标量模式，
# 把分解白白丢掉。这里验证的是 `assume._factor_basis`（`_basis` 本身
# 两标量降级路径不动，见该函数）。
# ═══════════════════════════════════════════════════════════════

def _one_factor_decomp(key="utilization", label="稼动率", unit="变化率"):
    from ir_agent.drivers import Decomposition, Factor, Op
    return Decomposition(target="revenue", op=Op.MULTIPLY,
                         factors=[Factor(key=key, label=label, unit=unit)])


def test_无数据因子明说估计不再被驳回():
    """**这是本次要修的失败场景本身的最小复现**：因子标签「稼动率」
    在正文里一次都不出现，采集状态确实是 `gaps.MISSING`——修复前
    `_basis` 只认占位符与正文逐字引用，这里两样都没有，会被
    `AssumeFailed("缺少可核验的依据（需正文原话或占位符）")` 直接驳回
    （已用 git worktree 跑过修复前的 `ir_agent/assume.py`，实测复现，
    见 fix-factor-basis-report.md）。修复后允许「无数据（估计）」，
    只要因子的采集状态真的是 MISSING，且理由里说明了估计依据。"""
    d = _one_factor_decomp()
    body = "境外收入敞口大小暂无法量化。"        # 「稼动率」一次都不出现
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.MISSING,
        note="未找到任何数据 —— 取值为估计")}
    raw = ("假设: 稼动率 | 第1年 | 5% | 8% | 12% | "
          "无数据（估计），参照同业平均稼动率下滑趋势判断")
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF,
                decomps=[d])           # 不抛异常即为通过
    assert a.rows[0].bases == [], "无数据估计应落地为空依据，不是编一个假依据"


def test_对有数据的因子写无数据估计仍被驳回():
    """「无数据」只对确实没有数据支撑的因子开放——否则会变成模型对任何
    因子都想用的万能逃生口，防的正是这个。"""
    d = _one_factor_decomp()
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.ANNUAL, note="「稼动率保持稳定」——《核心竞争力分析》")}
    raw = "假设: 稼动率 | 第1年 | -2% | 0% | 2% | 无数据（估计），图省事"
    with pytest.raises(AssumeFailed, match="无数据"):
        assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)


def test_提示词列出的会被驳回的状态等于门实际驳回的状态():
    """**这条锁的是「提示词没说、门却会驳回」这类遗漏**：子项目 B 加了
    ②.5 公司公告（`gaps.ANNOUNCE`），门对它照样驳回「无数据（估计）」，
    提示词的枚举却漏了「公告披露」——公告摘录常常根本没有数字，这时
    「无数据（估计）」恰是诚实答案，模型却从没被告知会被驳回，白吃
    一次重试（本仓库有连续三次驳回把一轮拖进两标量降级的先例）。

    枚举不手写：状态集合来自 `gaps` 里的状态常量，「门驳回哪些」是对
    `assume.parse` 真跑出来的（每个状态各造一个因子喂一遍），提示词
    里列出的状态是从渲染出的文本里解析出来的。以后 `gaps` 再加第六
    档而提示词没更新，这里变红。"""
    statuses = [v for k, v in vars(gaps).items()
                if k.isupper() and isinstance(v, str)]
    assert gaps.MISSING in statuses and len(statuses) >= 6, \
        "没扫到 gaps 的状态常量——扫描方式失效了，下面的比对会空转"

    d = _one_factor_decomp()
    raw = "假设: 稼动率 | 第1年 | 5% | 8% | 12% | 无数据（估计），参照同业惯例"
    rejected = []
    for st in statuses:
        fd = {"utilization": gaps.FactorData(
            factor=d.factors[0], status=st, note="占位")}
        try:
            assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
        except AssumeFailed:
            rejected.append(st)
    # 门的语义：除 MISSING 外一律驳回——先把这一前提钉住，否则下面的
    # 比对可能在比两个都错的东西。
    assert gaps.MISSING not in rejected
    assert set(rejected) == set(statuses) - {gaps.MISSING}

    # 因子模式的提示词才带这段规则（两标量模式没有「无数据（估计）」出口）。
    txt = assume.build_prompt({"g_stats": (), "gm_stats": ()}, ["2026E"], "上下文",
                              decomps=[d], factor_data={})
    m = re.search(r"开放，对((?:「[^」]+」)+)的因子这么写会被直接", txt)
    assert m, "提示词里找不到「对…的因子这么写会被直接驳回」这句枚举"
    listed = re.findall(r"「([^」]+)」", m.group(1))
    # 顺序也跟着梯子走：`gaps` 里的常量就是按 ①②②.5③④ 的顺序声明的。
    assert listed == rejected, \
        f"提示词枚举 {listed} 与门实际驳回的状态 {rejected} 不一致"


def test_因子压根没被采集也能用无数据出口():
    """`factor_data` 里都没有这个因子（比如 gaps.collect 整体失败、
    factor_data={}）——同样属于"确实没有数据支撑"，不应该因为字典里
    连键都没有就被当成异常拒绝。"""
    d = _one_factor_decomp()
    raw = "假设: 稼动率 | 第1年 | 5% | 8% | 12% | 无数据（估计），保守假设持平微降"
    a = assume.parse(raw, 1, decomps=[d], factor_data={}, as_of=AS_OF)
    assert a.rows[0].bases == []


def test_无数据估计没给理由被check挡下():
    """空依据 + 空理由 = 一个数字凭空出现在清单上，连"这是怎么估出来的"
    都无从判断——`_factor_basis` 只在解析阶段判过一次"这个因子确实
    MISSING"，理由是否给出要在 `check()` 里再挡一道，因为 `check()`
    在第二阶段人工核实之后会脱离 `_parse_factors` 单独重跑，人手改
    JSON 同样可能把 note 清空。"""
    d = _one_factor_decomp()
    a = assume.AssumptionSet(code="", labels=["2026E"], decomps=[d])
    a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.05"),
                         base=D("0.08"), bull=D("0.12"),
                         factor_key="utilization", note="")]
    with pytest.raises(AssumeFailed, match="没有给理由"):
        assume.check(a, 1, ledger=FactLedger(), body="无关正文", as_of=AS_OF,
                    decomps=[d])


def test_全部因子都无数据时check仍能通过():
    """极端场景：分解式里每个因子都没有数据支撑——这不是"没做功课"，
    是真实可能发生的情形（比如整个行业都不披露稼动率），应当被允许，
    不能因为 `validate([])` 原本是给"整批一条依据都没给"设的全局闸门
    就被误伤。"""
    from ir_agent.drivers import Decomposition, Factor, Op
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="utilization", label="稼动率", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])
    raw = "\n".join([
        "假设: 稼动率 | 第1年 | 3% | 5% | 8% | 无数据（估计），参照行业均值",
        "假设: 单价 | 第1年 | -2% | 0% | 2% | 无数据（估计），参照历史波动区间"])
    a = assume.parse(raw, 1, decomps=[d], factor_data={}, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body="无关正文", as_of=AS_OF,
                decomps=[d])


def test_估计行在清单上被显著标出():
    """遗漏必须可见——估计行不能跟"有依据"的行长得一样，读者要能一眼
    分清哪些数是有据可查的，哪些是拍脑袋估的。"""
    d = _one_factor_decomp()
    a = assume.AssumptionSet(code="600584", labels=["2026E"], decomps=[d])
    a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.05"),
                         base=D("0.08"), bull=D("0.12"),
                         factor_key="utilization", note="参照行业均值")]
    txt = a.describe()
    assert "⚠ 估计" in txt


def test_估计因子在清单末尾被单独汇总():
    """不能只淹没在各自因子的表格里，要单独汇总一段，让人一眼看出
    「这几个数是估的，没有证据支撑」。"""
    d = _one_factor_decomp()
    a = assume.AssumptionSet(code="600584", labels=["2026E"], decomps=[d])
    a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.05"),
                         base=D("0.08"), bull=D("0.12"),
                         factor_key="utilization", note="参照行业均值")]
    txt = a.describe()
    assert "没有证据支撑，是估计" in txt and "稼动率" in txt.split("估计")[-1]


def test_决策层面因子模式一次就能给出合格判断项不再降级():
    """**复现的是编排层实测踩到的真实后果**：因子模式若怎么填都给不出
    合格的行，`decide()` 会在连续 `max_attempts` 次之后整体退回两标量
    模式（`a.decomps == []`）——这正是 600584 真实联网那一轮发生的事。
    这里用假 client 还原同样的分解式形状（一个因子正文完全没提、状态
    MISSING），验证修复后**第一次**就能给出合格的因子行，根本不需要
    走到降级分支。"""
    d = _one_factor_decomp()
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.MISSING,
        note="未找到任何数据 —— 取值为估计")}

    class _C:
        def __init__(self): self.calls = 0
        def __call__(self, p):
            self.calls += 1
            return ("假设: 稼动率 | 第1年 | 5% | 8% | 12% | "
                    "无数据（估计），参照同业平均稼动率下滑趋势判断")

    client = _C()
    body = "境外收入敞口大小暂无法量化。"
    a = assume.decide(client, {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
                      ledger=FactLedger(), body=body, as_of=AS_OF,
                      decomps=[d], factor_data=fd)
    assert client.calls == 1, "应当第一次就通过，不需要重试更不需要降级"
    assert a.decomps == [d], "不应该退回两标量模式——因子分解本该被保留"
    assert not a.degraded
    assert a.rows[0].bases == []


# ── 出口一：引用该因子自己采集到的证据 ────────────────────────

def test_引用该因子采集到的Finding代码自动补上真实出处():
    """模型只管逐字引用 Finding.claim，出处（URL、抓取日期）由代码
    填——绝不让模型自己写网址，那等于给捏造开一扇门。"""
    d = _one_factor_decomp()
    finding = Finding(factor_key="utilization", claim="平均稼动率约78%",
                      url="https://semi.org/x", publisher="SEMI",
                      tier=Tier.OFFICIAL, published=date(2026, 7, 1))
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.EXTERNAL, findings=[finding])}
    body = "公司积极扩产，产能持续释放。"       # 正文完全没提这句话
    raw = ("假设: 稼动率 | 第1年 | 2% | 4% | 6% | "
          "「平均稼动率约78%」，参照采集到的行业数据")
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    basis = a.rows[0].bases[0]
    assert basis.kind.value == "external"
    assert basis.ref == "https://semi.org/x", "出处必须是代码从 Finding 里填的真实 URL"
    assert basis.retrieved == AS_OF


def test_引用不存在的发现会掉回正文核验并被标注():
    """模型编一句看起来像证据、但不在任何 Finding.claim 里的话——不能
    因为"看起来像因子证据"就绕过核验，应当掉回"当作正文引用"这条老
    路径去核验（不能被 `_match_finding` 意外放行）。

    回归调整（放宽因子模式引文审核）：这条路径核验的落点变了——引文
    确实不在正文里，但那与凭空捏造不是一回事，因子模式下不再因此
    致命，而是照常通过并标注「依据未能逐字核验」。行为仍然与修复前
    对普通正文引用的处理一致（同一条 REPORT 校验路径），变的只是
    check() 对 REPORT 核验失败的处置方式。"""
    d = _one_factor_decomp()
    finding = Finding(factor_key="utilization", claim="平均稼动率约78%",
                      url="https://semi.org/x", publisher="SEMI",
                      tier=Tier.OFFICIAL, published=date(2026, 7, 1))
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.EXTERNAL, findings=[finding])}
    body = "公司积极扩产，产能持续释放。"
    raw = "假设: 稼动率 | 第1年 | 2% | 4% | 6% | 「稼动率将大幅提升至95%」"
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    assert a.rows[0].bases[0].kind.value == "report", \
        "没有匹配到 Finding.claim，必须走回正文引用这条老路径，不能被意外放行成别的 kind"
    assert a.rows[0].basis_unverified, "正文里确实没有这句话，应当被标注为未核验"


def test_引用年报摘录片段代码自动补上年报出处():
    """②年报叙述章节命中时，`gaps.collect` 已经把摘出的原文片段记进
    `fd.note`、真实出处记进 `fd.source_url`——模型逐字引用这段原文，
    代码把出处填进 EXTERNAL 依据。选 EXTERNAL 而不是 REPORT：REPORT
    的核验（`basis.validate`）比对的是 `check()` 当时的 `body`，也就是
    研究简报正文，年报原文根本不在这段正文里，标成 REPORT 会在紧接着
    的 `check()` 里就被判"正文里找不到"而驳回；EXTERNAL 在没有 corpus
    时只验格式不验内容，恰好符合"年报只在预研当轮验一次、以后不再回头
    复查"的实际情况（第二阶段不会重新下载解析年报 PDF）。"""
    d = _one_factor_decomp()
    excerpt = "稼动率维持在行业前列，具体测算详见内部管理报表"
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.ANNUAL,
        note=f"「{excerpt}」"
            "——《核心竞争力分析》（文字依据，是否给出具体数值待人工确认）",
        source_url="https://static.cninfo.com.cn/annual.pdf",
        source_retrieved=AS_OF, source_excerpt=excerpt)}
    body = "公司持续推进降本增效。"
    raw = ("假设: 稼动率 | 第1年 | -2% | 0% | 2% | "
          "「稼动率维持在行业前列」，年报未给具体数值，取中性估计")
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    basis = a.rows[0].bases[0]
    assert basis.kind.value == "external"
    assert basis.ref == "https://static.cninfo.com.cn/annual.pdf"
    assert basis.quote == "稼动率维持在行业前列"


def test_引用研报摘录片段代码自动补上研报文档的真实url():
    """③研报语料库命中同理，出处是那篇 Document 自己的 url/retrieved
    （由 `gaps._match_report` 带回给 `collect`），不是年报的。"""
    d = _one_factor_decomp()
    excerpt = "我们测算行业稼动率将于下半年回升"
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.REPORT,
        note=f"「{excerpt}」——某券商研究所"
            "《行业深度报告》（2026-06-01，卖方研究）"
            "（文字依据，是否给出具体数值待人工确认）",
        source_url="https://research.example/x",
        source_retrieved=date(2026, 6, 1), source_excerpt=excerpt)}
    body = "公司持续推进降本增效。"
    raw = ("假设: 稼动率 | 第1年 | 1% | 3% | 5% | "
          "「我们测算行业稼动率将于下半年回升」，参照研报测算的回升节奏")
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    basis = a.rows[0].bases[0]
    assert basis.ref == "https://research.example/x"
    assert basis.retrieved == date(2026, 6, 1), \
        "研报的抓取日期应是该 Document 自己的 retrieved，不是这一轮的 as_of"


def test_引用公告摘录片段代码自动补上公司公告的真实url():
    """②.5 公司公告命中同理——出处是那篇 Document（cninfo 公告本身）
    自己的 url/retrieved（由 `gaps._match_announce` 带回给 `collect`），
    不是年报也不是研报的。

    回归动机：`_match_excerpt` 的状态白名单曾经只有 `(gaps.ANNUAL,
    gaps.REPORT)` 两项，`gaps.ANNOUNCE` 不在其中——ANNOUNCE 状态的
    因子即使摘录逐字匹配，也会被 `_match_excerpt` 直接判 False，
    落到下面的 `Basis.report(quote)` 出口：那条出口按"简报正文"校验，
    公告原文根本不在正文里，真实的 cninfo URL 也被丢弃。这条测试要
    锁住的就是"公告摘录命中 → EXTERNAL 且 ref 是公告的真实 url"，
    而不是"退化成 REPORT"。"""
    d = _one_factor_decomp()
    excerpt = "本次投产新增名义产能 300 万平方米"
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.ANNOUNCE,
        note=f"「{excerpt}」——生益科技《扩产投产公告》"
            "（2026-08-01，公司公告）"
            "（文字依据，是否给出具体数值待人工确认）",
        source_url="http://static.cninfo.com.cn/a.PDF",
        source_retrieved=date(2026, 8, 1), source_excerpt=excerpt)}
    body = "公司持续推进降本增效。"
    raw = ("假设: 稼动率 | 第1年 | 1% | 3% | 5% | "
          "「本次投产新增名义产能 300 万平方米」，参照公告披露的新增产能")
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    basis = a.rows[0].bases[0]
    assert basis.kind.value == "external", \
        "ANNOUNCE 命中的摘录应该走 EXTERNAL 出口，不能退化成 report"
    assert basis.ref == "http://static.cninfo.com.cn/a.PDF"
    assert basis.retrieved == date(2026, 8, 1), \
        "公告的抓取日期应是该 Document 自己的 retrieved，不是这一轮的 as_of"


def test_年报摘录引文对不上原文片段时掉回正文核验并被标注():
    """模型引的话跟 `fd.source_excerpt` 对不上（比如编了数字或改了
    表述）——不能被 `_match_excerpt` 放过，应当掉回"当作正文引用"这条
    老路径核验，不能被这条出口意外放行成 EXTERNAL。

    回归调整（放宽因子模式引文审核）：掉回正文核验之后，引文确实不在
    正文里，但因子模式下这不再致命——转述与凭空捏造不是一回事，照常
    通过并标注「依据未能逐字核验」，人工批准时会看见。"""
    d = _one_factor_decomp()
    excerpt = "稼动率维持在行业前列"
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.ANNUAL,
        note=f"「{excerpt}」——《核心竞争力分析》（文字依据，"
            "是否给出具体数值待人工确认）",
        source_url="https://static.cninfo.com.cn/annual.pdf",
        source_retrieved=AS_OF, source_excerpt=excerpt)}
    body = "公司持续推进降本增效。"
    raw = "假设: 稼动率 | 第1年 | -2% | 0% | 2% | 「稼动率已提升至95%」"
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    assert a.rows[0].bases[0].kind.value == "report", \
        "跟年报摘录对不上，必须掉回正文引用这条老路径，不能被误判成 EXTERNAL"
    assert a.rows[0].basis_unverified, "跟正文也对不上，应当被标注为未核验"


def test_年报摘录含嵌套弯引号仍可核验依据_600584实测复现():
    """600584 实跑复现的真实 bug：产能规模因子命中年报，`gaps.collect`
    摘出的原文片段里恰好嵌着一对中文弯引号（“…“创新提效、行深致远”
    …”）。修复前 `_match_excerpt` 用非贪婪正则 `_QUOTE` 从 `fd.note`
    （拼给人看的展示文本）里把摘录抠出来，一遇到年报原文内部这对
    弯引号的收尾引号就把摘录腰斩——本人已实测复现：

        >>> m = _QUOTE.search(note); m.group(1)
        '…公司将围绕“创新提效、行深致远'
        >>> "高端先进制造产能规模化创收" in m.group(1)
        False

    模型引用的「高端先进制造产能规模化创收」落在截断点之后，而这
    正是我们自己采集给它的证据（不是编造），却被判定"匹配不到"，
    掉回正文核验、因为不在研究简报正文里而被驳回——连续 3 次失败后
    拖累整轮研究降级成两标量模式。

    修复后 `_match_excerpt` 只认独立字段 `fd.source_excerpt`，不再碰
    `fd.note` 一个字符，这里用同一段真实原文（含嵌套弯引号）应当能
    顺利核验，且构造出的 Basis 带着真实出处（年报公告的下载地址）。"""
    d = _one_factor_decomp(key="capacity", label="产能规模")
    excerpt = ("…公司将围绕“创新提效、行深致远”的主题，立足先进技术研发"
               "成果转化、高端先进制造产能规模化创收、生态体系系统建设"
               "以及主流封测业务先进化转型，稳妥统筹国内国际双循环，坚…")
    note = (f"「{excerpt}」——《未来发展的讨论与分析》"
            "（文字依据，是否给出具体数值待人工确认）")
    fd = {"capacity": gaps.FactorData(
        factor=d.factors[0], status=gaps.ANNUAL, note=note,
        source_url="https://static.cninfo.com.cn/600584_2025_annual.pdf",
        source_retrieved=AS_OF, source_excerpt=excerpt)}
    body = "公司持续推进降本增效，聚焦主业发展。"
    raw = ("假设: 产能规模 | 第1年 | 3% | 6% | 9% | "
          "「高端先进制造产能规模化创收」，公司明确将扩大高端产能，"
          "带动营收增长")
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    basis = a.rows[0].bases[0]
    assert basis.kind.value == "external"
    assert basis.ref == "https://static.cninfo.com.cn/600584_2025_annual.pdf"
    assert basis.quote == "高端先进制造产能规模化创收"


def test_摘录含ASCII引号同样可匹配():
    """摘录原文里若嵌着 ASCII 双引号（同样会让旧的 `_QUOTE` 正则腰斩：
    ASCII `"` 既在开引号字符集也在收引号字符集里），改用独立字段后
    不应受影响——`_match_excerpt` 现在直接对 `source_excerpt` 做子串
    比对，不再用正则去解析任何带引号的文本。"""
    d = _one_factor_decomp()
    excerpt = "公司在报告期内表示\"降本增效\"成效显著，产能利用率保持高位运行"
    note = (f"「{excerpt}」——《管理层讨论与分析》"
            "（文字依据，是否给出具体数值待人工确认）")
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.ANNUAL, note=note,
        source_url="https://static.cninfo.com.cn/annual.pdf",
        source_retrieved=AS_OF, source_excerpt=excerpt)}
    body = "公司持续推进降本增效。"
    raw = "假设: 稼动率 | 第1年 | -1% | 1% | 3% | 「产能利用率保持高位运行」"
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 1, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    basis = a.rows[0].bases[0]
    assert basis.kind.value == "external"
    assert basis.quote == "产能利用率保持高位运行"


def test_依据跨阶段存活_因子证据引用():
    """新出口构造出的 EXTERNAL 依据同样要能经过 to_json/from_json 往返，
    第二阶段读回后仍能通过 check()——中间人工批准，第一阶段写出、
    第二阶段读回，不能因为序列化漏了字段就被自己的门挡住。"""
    d = _one_factor_decomp()
    finding = Finding(factor_key="utilization", claim="平均稼动率约78%",
                      url="https://semi.org/x", publisher="SEMI",
                      tier=Tier.OFFICIAL, published=date(2026, 7, 1))
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.EXTERNAL, findings=[finding])}
    body = "公司积极扩产，产能持续释放。"
    raw = "假设: 稼动率 | 第1年 | 2% | 4% | 6% | 「平均稼动率约78%」"
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    a.code, a.labels = "600584", ["2026E"]
    js = assume.to_json(a, code="600584")
    back, _ = assume.from_json(js, code="600584")
    assume.check(back, 1, ledger=FactLedger(), body=body, as_of=AS_OF,
                decomps=back.decomps)
    assert back.rows[0].bases[0].ref == "https://semi.org/x"


def test_依据跨阶段存活_无数据估计():
    """空依据（出口二）同样要能跨阶段存活——第二阶段没有 factor_data
    可查，也不需要：这条出口的合法性只在解析当轮判一次。"""
    d = _one_factor_decomp()
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.MISSING, note="未找到任何数据")}
    raw = "假设: 稼动率 | 第1年 | 5% | 8% | 12% | 无数据（估计），保守假设"
    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
    a.code, a.labels = "600584", ["2026E"]
    js = assume.to_json(a, code="600584")
    back, _ = assume.from_json(js, code="600584")
    assume.check(back, 1, ledger=FactLedger(), body="无关正文", as_of=AS_OF,
                decomps=back.decomps)
    assert back.rows[0].bases == []


def test_提示词说明两条新出口何时用哪条():
    """提示词要把两条新出口讲清楚，且不能让模型误以为「无数据」可以
    随便写——必须点出它只对确实没数据的因子开放。"""
    d = _one_factor_decomp()
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.MISSING, note="未找到任何数据")}
    txt = assume._factor_head([d], fd)
    assert "无数据（估计）" in txt
    assert "只对确实没有数据支撑的因子开放" in txt


# ═══════════════════════════════════════════════════════════════
# fix-basis-ditto：因子模式「依据」允许「同上」回指同一因子上一行
#
# 契约缺口：600584 真实联网跑分解式有 9 个因子、预测期 5 年，模型要填
# 45 行，逐年重复同一段年报引文纯属浪费——依据在语义上是"每个因子
# 一份"（证明这个因子的数据来源），逐年不同的是理由（判断）。第四次
# 实跑模型试图用「同上引文」缩写重复的依据，被 `_factor_basis` 判
# "缺少可核验的依据"直接驳回（既找不到 `[[key@period]]`，也找不到
# `「…」`，也不是「无数据…估计」）。
#
# 修法：允许「同上」类回指，但把含义精确定为"同一因子（factor_key）
# 上一行的依据"——不是"文本里紧邻的上一行"，那可能是别的因子，会有
# 歧义。由代码复用那条已经核验过的 Basis 对象，审计链条不受损。
# ═══════════════════════════════════════════════════════════════

def test_同上引文复用同一因子上一年已核验的依据_600584实测复现():
    """**这是本次要修的失败场景本身的最小复现**：产能规模因子第 1 年
    给出完整的年报引文依据，第 2 年模型写「依据：同上引文」缩写——
    修复前 `_factor_basis` 在这一行既找不到占位符也找不到「…」引号
    引文，也不是「无数据…估计」，会被直接驳回（已实测复现，见
    fix-basis-ditto-report.md）。修复后第 2 行应当拿到与第 1 行完全
    相同的、带真实年报出处的 Basis，理由（note）仍然各自独立。"""
    d = _one_factor_decomp(key="capacity", label="产能规模")
    excerpt = "高端先进制造产能规模化创收"
    fd = {"capacity": gaps.FactorData(
        factor=d.factors[0], status=gaps.ANNUAL,
        note=f"「{excerpt}」——《未来发展的讨论与分析》"
            "（文字依据，是否给出具体数值待人工确认）",
        source_url="https://static.cninfo.com.cn/600584_2025_annual.pdf",
        source_retrieved=AS_OF, source_excerpt=excerpt)}
    body = "公司持续推进降本增效，聚焦主业发展。"
    raw = "\n".join([
        "假设: 产能规模 | 第1年 | 3% | 6% | 9% | "
        f"「{excerpt}」，公司明确将扩大高端产能，带动营收增长",
        "假设: 产能规模 | 第2年 | 4% | 7% | 10% | "
        "理由：产能持续爬坡，带动营收进一步增长。依据：同上引文。",
    ])
    a = assume.parse(raw, 2, decomps=[d], factor_data=fd, as_of=AS_OF)
    assume.check(a, 2, ledger=FactLedger(), body=body, as_of=AS_OF, decomps=[d])
    row1, row2 = a.rows[0], a.rows[1]
    assert row2.bases == row1.bases, "「同上」应复用同一因子上一行已核验的 Basis"
    basis2 = row2.bases[0]
    assert basis2.kind.value == "external"
    assert basis2.ref == "https://static.cninfo.com.cn/600584_2025_annual.pdf"
    assert basis2.quote == excerpt
    # 理由仍然各自独立，不能因为依据复用就把理由也搞混
    assert row1.note != row2.note
    assert "产能持续爬坡" in row2.note


def test_同上写法变体_同前_见上_同该因子上一年():
    """措辞不做固定短语死匹配——「同上」「同前」「见上」「同该因子
    上一年」都应识别为回指，与 `_looks_like_estimate` 同一套只要求
    关键词共现的思路。"""
    d = _one_factor_decomp()
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.MISSING, note="未找到任何数据")}
    variants = ["同前", "见上", "同该因子上一年"]
    for variant in variants:
        raw = "\n".join([
            "假设: 稼动率 | 第1年 | 5% | 8% | 12% | "
            "无数据（估计），参照同业平均稼动率下滑趋势判断",
            f"假设: 稼动率 | 第2年 | 5% | 8% | 12% | {variant}，判断不变",
        ])
        a = assume.parse(raw, 2, decomps=[d], factor_data=fd, as_of=AS_OF)
        assert a.rows[1].bases == a.rows[0].bases, variant


def test_因子第一次出现就写同上被驳回():
    """「同上」指的是同一因子上一行的依据——该因子第一次出现（没有
    上一行可回指）时必须驳回，不能悄悄放行成"没有依据"。"""
    d = _one_factor_decomp()
    raw = "假设: 稼动率 | 第1年 | 5% | 8% | 12% | 同上，参照上次报告"
    with pytest.raises(AssumeFailed, match="没有上一行"):
        assume.parse(raw, 1, decomps=[d], factor_data={}, as_of=AS_OF)


def test_同上不跨因子继承():
    """歧义不能出现在一个负责"可核验"的机制里——「同上」必须精确指向
    同一因子（factor_key）的上一行，不能因为文本里紧邻的上一行恰好
    是别的因子、且那一行有合法依据，就被当成可以借用。"""
    d = _two_factor_decomp()
    finding = Finding(factor_key="utilization", claim="平均稼动率约78%",
                      url="https://semi.org/x", publisher="SEMI",
                      tier=Tier.OFFICIAL, published=date(2026, 7, 1))
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.EXTERNAL, findings=[finding])}
    raw = "\n".join([
        "假设: 稼动率 | 第1年 | 2% | 4% | 6% | 「平均稼动率约78%」，参照采集数据",
        "假设: 单价 | 第1年 | -1% | 1% | 3% | 同上，判断口径一致",
    ])
    with pytest.raises(AssumeFailed, match="没有上一行"):
        assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)


def test_同行真实引文优先于同上字样():
    """注意不要误伤：一行里如果同时给了真实引文和「同上」字样，应以
    真实引文为准——`_QUOTE` 在 `_looks_like_ditto` 之前被检查，真实
    引文分支必然优先命中，不能被误判成"同上"回指。

    回归调整（放宽因子模式引文审核）：走到 REPORT 核验之后，引文确实
    不在正文里——但这不再是"不可核验就致命"，因子模式下照常通过并
    标注。这条测试守的核心不变（不能被误判成回指，必须真去核验这句
    话本身），变的只是核验不过之后 check() 的处置方式。"""
    d = _one_factor_decomp()
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.MISSING, note="未找到任何数据")}
    raw = "\n".join([
        "假设: 稼动率 | 第1年 | 5% | 8% | 12% | "
        "无数据（估计），参照同业平均稼动率下滑趋势判断",
        "假设: 稼动率 | 第2年 | 5% | 8% | 12% | "
        "「行业稼动率同上季度基本持平」，年报原话",
    ])
    a = assume.parse(raw, 2, decomps=[d], factor_data=fd, as_of=AS_OF)
    # 第二行含有真实的「…」引文，即便引文正文本身出现了"同上"字样，
    # 也必须走 `_basis`/`_factor_basis` 的正文引用出口，而不是被误判
    # 成回指——不能与第一行共享同一个（空的）Basis 列表。
    assert a.rows[1].bases != a.rows[0].bases
    assume.check(a, 2, ledger=FactLedger(), body="公司持续推进降本增效。",
                as_of=AS_OF, decomps=[d])
    assert a.rows[1].bases[0].kind.value == "report"
    assert a.rows[1].basis_unverified, "正文里没有这句话，应当被标注为未核验"
    assert not a.rows[0].basis_unverified, "第一行走的是无数据估计出口，不该被牵连标注"


def _two_factor_decomp():
    from ir_agent.drivers import Decomposition, Factor, Op
    return Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="utilization", label="稼动率", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])


def test_提示词说明同上的精确含义():
    """`_factor_head` 要写明「同上」的合法写法与精确含义——同一因子
    上一年的依据，且第一次出现该因子时必须给出完整依据。这是降低
    45 行判断项失败率的关键，不能只在代码里悄悄支持而不告诉模型。"""
    d = _one_factor_decomp()
    fd = {"utilization": gaps.FactorData(
        factor=d.factors[0], status=gaps.MISSING, note="未找到任何数据")}
    txt = assume._factor_head([d], fd)
    assert "同上" in txt
    assert "同一因子" in txt or "同一个因子" in txt
    assert "第一次出现该因子时必须给出完整依据" in txt or "第一次出现" in txt


def test_因子模式引文核验不过被标注而非驳回_600584实测复现():
    """**这是本次放宽要修的失败场景本身的最小复现**（用户明确决定：
    「放宽对于因子的审核，只要数据合理便可以考虑通过」）。

    实测教训（600584 诊断实跑，9 因子×5 年×3 档=45 行，约 50% 成功率）：
    模型在依据列写的是对正文含义的**概括**，不是**捏造**——
    「上游议价能力弱意味着公司对设备与原材料价格波动的传导能力有限」
    「修复但未完全追平」，这两句正文里都没有逐字对应。放宽前的行为
    （已用本条最小复现实测确认，见下方 `git show` 的固定探针）：

        依据不可核验：依据引用的正文片段「修复但未完全追平」在正文中
        找不到 —— 与凭空捏造无异。　引文必须与正文**逐字一致**……

    连续 3 次被拒后整个因子分解被丢掉、退回两标量模式——而因子分解
    正是这条产品线的全部意义。放宽后：这一行照常通过，但要在 `Row`
    上留下可见的标记，人工批准时能看见、能拒，不能被悄悄放行成跟
    "核验通过"一样。
    """
    cap = Factor(key="cap", label="产能规模", unit="变化率")
    rev = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[cap])
    a = assume.AssumptionSet(code="600584", labels=["2026E"])
    a.decomps = [rev]
    a.rows = [assume.Row(driver="revenue", year=1, bear=D("-0.02"), base=D("0.03"),
                         bull=D("0.08"), note="理由", factor_key="cap",
                         bases=[assume.Basis.report("修复但未完全追平")])]

    assume.check(a, 1, ledger=FactLedger(), body="正文里没有这句话",
                as_of=AS_OF, decomps=[rev], base_margin=D("0.1415"))

    row = a.rows[0]
    assert row.basis_unverified, "引文核验不过应当标注在 Row 上，不能悄悄放行"
    assert "修复但未完全追平" in row.basis_unverified or \
        "在正文中找不到" in row.basis_unverified
    # 原始引文本身不能被抹掉——人要看到具体写的是什么才能判断这段
    # 转述靠不靠谱，不是只留一个"未核验"的标签。
    assert row.bases and row.bases[0].quote == "修复但未完全追平"

    a.code = "600584"
    txt = a.describe()
    assert "依据未能逐字核验" in txt, "待核实清单上必须显著标出，与核验通过的行一眼可分"
    assert "修复但未完全追平" in txt, "原始引文仍要留在清单上，不能只剩标签"
    # 底部要单独汇总一段：哪些因子的哪些年份依据是软的——遗漏必须可见。
    assert "⚠ **以下因子的依据未能逐字核验**" in txt
    assert "产能规模第1年" in txt


def test_两标量模式对同款引文场景仍然驳回_回归锁():
    """回归锁：同一个真实失败场景（引文「修复但未完全追平」，正文里
    没有这句话）放到两标量降级模式下，必须仍然驳回——这次放宽只针对
    因子行，两标量模式引的是正文，正文本来就覆盖增速/毛利率两项，
    逐字核验在那里可满足，不该被一并放宽。"""
    a = assume.AssumptionSet(code="600584", labels=["2026E"])
    a.rows = [
        assume.Row(driver="growth", year=1, bear=D("-0.02"), base=D("0.03"),
                  bull=D("0.08"), note="理由",
                  bases=[assume.Basis.report("修复但未完全追平")]),
        # 补全毛利率这一行，确保测试真的走到依据核验这一步，而不是
        # 提前被"缺第 1 年"这条不相干的门拦下。
        assume.Row(driver="gm", year=1, bear=D("0.12"), base=D("0.14"),
                  bull=D("0.16"), note="理由", bases=[assume.Basis.report(Q)]),
    ]
    with pytest.raises(AssumeFailed, match="不可核验"):
        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)


def test_两标量模式一条依据都没给仍然驳回_全局闸门不能被顺手拆掉():
    """**回归锁：放宽因子引文，不能把「整批一条依据都没给」这道全局
    闸门一起拆掉。**

    这道门守的是真实踩过的事故（见 `test_依据跨阶段存活` 的记录：
    「读回来的假设一律『未提供任何依据』而拒绝建模 —— 门是对的」）。
    它原来由 `validate(flat)` 在 `flat` 为空时提供；放宽把整批核验
    改成了逐行逐依据核验，而逐行循环对空 `bases` 的行直接 `continue`
    ——因子模式没事（空 `bases` 是「无数据（估计）」这条合法出口，
    `_check_factors` 另有「没给理由就驳回」兜底），**两标量模式却
    没有任何兜底**：`_parse_scalar` 走 `_basis()` 时缺依据必然抛错，
    但 `check()` 会在人工核实之后、`from_json` 读回时脱离解析器再跑
    一遍（`full.run_valuation`），人手改 JSON 把 `bases` 清空就能一路
    通过——依据被抹掉了，建模照做。

    两标量模式是因子模式连续被拒后的降级路径，正是最容易被忽视的
    那条；这次放宽明确只针对因子行，它必须原样硬。"""
    a = assume.AssumptionSet(code="600584", labels=["2026E"])
    a.rows = [
        assume.Row(driver="growth", year=1, bear=D("-0.02"), base=D("0.03"),
                  bull=D("0.08"), note="理由", bases=[]),
        assume.Row(driver="gm", year=1, bear=D("0.12"), base=D("0.14"),
                  bull=D("0.16"), note="理由", bases=[]),
    ]
    with pytest.raises(AssumeFailed, match="未提供任何依据"):
        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)


def test_因子模式伪造账本占位符依旧驳回():
    """**放宽不能连坐到账本占位符。** `[[key@period]]` 指向账本里根本
    不存在的事实，是对账本的假陈述，且下游会拿占位符去做字符串替换
    ——错了会让整段估值替换失败（实测踩过整段漏成未替换占位符）。这
    与"对正文的转述"不是一类问题，因子模式下必须继续致命，不能被
    这次放宽一并放过。"""
    d = _one_factor_decomp()
    a = assume.AssumptionSet(code="600584", labels=["2026E"], decomps=[d])
    a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.05"), base=D("0.08"),
                         bull=D("0.12"), note="理由", factor_key="utilization",
                         bases=[assume.Basis.fact("[[revenue.yoy@2099FY]]")])]
    with pytest.raises(AssumeFailed, match="不可核验"):
        assume.check(a, 1, ledger=FactLedger(), body="无关正文", as_of=AS_OF,
                    decomps=[d])


def test_两标量模式的引文驳回不附加因子出路():
    """回归锁：出口二只存在于因子模式，两标量模式不该看到这段指引
    ——那会让人以为标量假设也能写「无数据（估计）」。"""
    from datetime import date
    from decimal import Decimal as D

    import pytest

    from ir_agent import assume as A
    from ir_agent.basis import Basis
    from ir_agent.ledger import FactLedger

    a = A.AssumptionSet(code="600584", labels=["2026E"])
    a.rows = [A.Row(driver="growth", year=1, bear=D("-0.02"), base=D("0.03"),
                    bull=D("0.08"), note="理由",
                    bases=[Basis.report("正文里没有的话")])]
    with pytest.raises(A.AssumeFailed) as ei:
        A.check(a, 1, ledger=FactLedger(), body="别的内容",
                as_of=date(2026, 9, 12))
    assert "无数据（估计）" not in str(ei.value)


def _estimate_note():
    return ("无数据（估计）。该因子没有官方或卖方口径数据，取值为估计，"
            "估计方向参照资本开支规模")


def test_理由自称估计却挂了占位符时仍要算进估计汇总():
    """**600584 实跑当场抓到的绕过。** 40 行判断项里有 4 个因子，理由
    白纸黑字写着「无数据（估计）」，依据列却挂了一个账本占位符：

        产能规模           → [[company.revenue_yoy_vs_industry@2025FY]]
        稼动率环比变化     → [[depreciation_amortization@2025FY]]
        产品结构(先进封装) → [[gross_margin.percentile@2025FY]]
        折旧摊销占收入比重 → [[depreciation_amortization@2025FY]]

    这些事实**真实存在、方向上也沾边**（折旧摊销之于固定成本分摊、
    营收增速之于扩产节奏），所以四道闸门全部放行。问题在后果：
    `_describe_estimated` 当时的判据是 `not r.bases`，于是只要挂上任何
    一个占位符，这一行就不再算「估计」——底部那份「没有证据支撑」的
    名单里，五个该在的因子只剩下一个（人民币兑美元均价）。

    **等于用一个合法但不直接支撑取值的占位符，把「这个数是拍的」从
    清单上抹掉了。** 而清单是人工批准唯一能看的东西，「遗漏必须可见」
    这条约束就是在这里被绕过的。

    判据改为**按理由自述归类**：理由自己说了无数据、是估计，那它就是
    估计，挂不挂占位符都一样。占位符本身仍然照常显示——它是真的、也
    有参考价值，只是不能冒充「这个取值的证据」。
    """
    d = _one_factor_decomp()
    a = assume.AssumptionSet(code="600584", labels=["2026E"], decomps=[d])
    a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.05"),
                         base=D("0.08"), bull=D("0.12"),
                         factor_key="utilization", note=_estimate_note(),
                         bases=[assume.Basis.fact("[[depreciation_amortization@2025FY]]")])]
    txt = a.describe()

    assert "没有证据支撑，是估计" in txt, "理由自称估计的因子必须进估计汇总"
    assert "稼动率" in txt.split("没有证据支撑")[-1]
    # 占位符不能被藏起来——它是真事实、有参考价值，只是不能冒充证据
    assert "depreciation_amortization" in txt
    assert "⚠ 估计" in txt


def test_真有依据的行不被误判成估计():
    """回归锁：理由正常、依据是真占位符的行，不该被这条新判据误伤。"""
    d = _one_factor_decomp()
    a = assume.AssumptionSet(code="600584", labels=["2026E"], decomps=[d])
    a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.05"),
                         base=D("0.08"), bull=D("0.12"),
                         factor_key="utilization",
                         note="理由：产能爬坡带动稼动率回升",
                         bases=[assume.Basis.fact("[[depreciation_amortization@2025FY]]")])]
    txt = a.describe()
    assert "没有证据支撑，是估计" not in txt
    assert "⚠ 估计" not in txt


def test_读回的判断项要重过分解式闸门():
    """**第二阶段读回判断项时不会再过分解式闸门**：`_decomp_from_dict` 直接构造
    对象，`check_closure` 在 assume/full 里一次都没调用。生益科技目录里残留的
    9/14 那份 assumptions.json，营收链里还有「高端产品收入占比」——预研时新闸门
    会拒收它，读回时却能一路走进建模。与「人手改 JSON 可能引入重复行」同一类，
    check() 必须再挡一次，不依赖上游一定已经查过。"""
    rev = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="cap", label="覆铜板产能", unit="变化率"),
        Factor(key="mix", label="高端产品收入占比", unit="变化率")])
    a = assume.AssumptionSet(code="600183", labels=["2026E"], decomps=[rev])
    a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.01"), base=D("0.05"),
                         bull=D("0.09"), note="理由", factor_key=k)
              for k in ("cap", "mix")]
    with pytest.raises(AssumeFailed, match="占比"):
        assume.check(a, 1, ledger=FactLedger(), body="正文", as_of=AS_OF,
                     decomps=[rev])


# ── 锚定开启时，毛利率第 2 年起从各档锚定水平接着走 ─────────────────────

def _anchor_600183():
    """生益科技 2026H1 实跑的锚定输入；三档与 ashare_calibrate.anchor_three 同口径。"""
    return {"rev": 190.2561, "cogs": 131.8622,
            "share3": (0.446, 0.4785, 0.5204), "gm_rest3": (0.3369, 0.3069, 0.2769),
            "base_rev": 284.3113845944}


def _two_year_set(bull_gm=((1.0, 0.5), (1.2, 0.7)), base_gm=((0.5, 0.2), (0.9, 0.4)),
                  bear_gm=((0.0, -0.2), (0.0, -0.1))):
    rev = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="cap", label="产能", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])
    gm = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="spread", label="价差", unit="pp"),
        Factor(key="util", label="稼动率摊薄", unit="pp")])
    a = assume.AssumptionSet(code="600183", labels=["2026E", "2027E"], decomps=[rev, gm])
    rows = []
    for y in (1, 2):
        rows.append(assume.Row(driver="revenue", year=y, bear=D("0.01"), base=D("0.05"),
                               bull=D("0.09"), note="理由", factor_key="cap"))
        rows.append(assume.Row(driver="revenue", year=y, bear=D("0.00"), base=D("0.01"),
                               bull=D("0.02"), note="理由", factor_key="asp"))
        for k, j in (("spread", 0), ("util", 1)):
            rows.append(assume.Row(
                driver="gross_margin", year=y, note="理由", factor_key=k,
                bear=D(str(bear_gm[y - 1][j])), base=D(str(base_gm[y - 1][j])),
                bull=D(str(bull_gm[y - 1][j]))))
    a.rows = rows
    return a, [rev, gm]


def _cal_600183():
    return {"judg": {"gm_hist": [0.2500, 0.2647], "growth": [0.1, 0.1], "gm": [0.26, 0.26]},
            "periods": {"annual": ["20241231", "20251231"], "proj": ["2026E", "2027E"]},
            "rev": {"20241231": 200.0, "20251231": 284.3113845944},
            "cogs": {"20241231": 150.0, "20251231": 284.3113845944 * (1 - 0.2647)},
            "interim": {"rev": 190.2561, "cogs": 131.8622,
                        "share3": [0.446, 0.4785, 0.5204],
                        "gm_rest3": [0.3369, 0.3069, 0.2769]}}


def test_锚定时毛利率第二年起从各档锚定水平接着走():
    """**生益科技实跑**：Base 档 2026 被半年报锚定在 30.69%，2027 却回到「年报
    基期 26.47% + 累计 pp」= 25.87%，一年凭空掉 4.8pp。加法链对全部年份用同一个
    年报基期，与已被实际数覆盖的首年混用了两个起点。

    改为第 t 年 = 该档锚定后的首年毛利率 +（第 t 年累计 pp − 第 1 年累计 pp）：
    保留模型判断的逐年变化，起点换成实际水平。"""
    a, _ = _two_year_set()
    bases = assume.margin_bases(a, D("0.2647"), _anchor_600183())
    _, gm = assume.synthesize(a, assume.Case.BASE, D("0.2647"), bases)
    assert float(gm[1]) == pytest.approx(0.3069 + 0.006, abs=5e-4)
    # 第 1 年仍是年报基期 + 首年累计：该年由锚定覆盖，清单横幅已说明
    assert float(gm[0]) == pytest.approx(0.2647 + 0.007, abs=1e-9)


def test_没有锚定时维持单一年报基期():
    a, _ = _two_year_set()
    assert assume.margin_bases(a, D("0.2647"), None) is None
    _, gm = assume.synthesize(a, assume.Case.BASE, D("0.2647"), None)
    assert float(gm[1]) == pytest.approx(0.2647 + 0.013, abs=1e-9)


def test_合成后单调性按平移后的路径查():
    """不平移时三档合格，平移后乐观档第 2 年落到中性档下面——闸门必须查真正
    建模用的那条路径，不是展示用的另一条。"""
    a, ds = _two_year_set(bull_gm=((5.0, 5.0), (2.5, 2.5)))
    assume.check(a, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, decomps=ds,
                 base_margin=D("0.2647"))
    a2, ds2 = _two_year_set(bull_gm=((5.0, 5.0), (2.5, 2.5)))
    with pytest.raises(AssumeFailed, match="毛利率"):
        assume.check(a2, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, decomps=ds2,
                     base_margin=D("0.2647"), anchor=_anchor_600183())


def test_清单上的合成结果与建模同一套基期():
    a, ds = _two_year_set()
    assume.check(a, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, decomps=ds,
                 base_margin=D("0.2647"), anchor=_anchor_600183())
    a.base_margin = D("0.2647")
    assert "31.29%" in a.describe(), "清单展示的第 2 年毛利率要与建模时平移后的一致"


def test_工作簿三档列与1b表用同一套按年基期():
    a, _ = _two_year_set()
    cal = _cal_600183()
    out = assume.apply_to(cal, a)
    block = assume.to_driver_block(a, cal)["gross_margin"]
    bm = assume._base_margin_of(cal)
    bases = assume.margin_bases(a, bm, assume.anchor_from_cal(cal))
    assert block["base_levels"]["base"] == [float(x) for x in bases["base"]]
    _, want = assume.synthesize(a, assume.Case.BASE, bm, bases)
    assert out["judg"]["gm_scen"]["base"] == [float(x) for x in want]


def test_建模时平移后不单调也要拒绝():
    """`apply_to` 自己要再查一次单调（哪怕调用方已经用同一个 anchor 查过一次），
    不能让不合格的路径靠"上游肯定查过"这种假设溜进工作簿。"""
    a, _ = _two_year_set(bull_gm=((5.0, 5.0), (2.5, 2.5)))
    with pytest.raises(AssumeFailed, match="毛利率"):
        assume.apply_to(_cal_600183(), a)


# ── C1：两阶段 check() 必须用同一个 anchor，否则批准的东西下一步就不合法 ──

def test_两阶段用不同anchor会给出相反结论():
    """**600183 实跑复现（final-fix-D 审查 C1）。** 单因子毛利率累计 pp：
    第 1 年 悲观 0.5 / 中性 1.3 / 乐观 2.0，第 2 年 悲观 0.5 / 中性 3.1 /
    乐观 3.0，base_margin=0.2647。

    第一阶段 `_propose_assumptions` 传 `anchor=assume.anchor_from_cal(cal)`，
    查的是平移后（锚定水平）的路径——PASS。第二阶段 `run_valuation` 此前
    调 `check()` 时漏传 `anchor`，`margin_bases` 因此为 `None`，查的是未平移
    （年报基期）路径——第 2 年悲观 26.97% / 中性 29.57% / 乐观 29.47%，乐观
    落到中性下面，FAIL。

    同一组 `AssumptionSet`、同一个 `base_margin`，只因为两处调用给不给
    `anchor` 就得出相反结论——这正是 C1 要修的口径分裂。本测试锁住这个
    分裂在 `assume.check()` 这一层确实存在，`full.run_valuation` 的修复
    （见 `test_full_context.py` 里对应用例）则要求它在调用处消失：两阶段
    必须传同一个 `anchor`。
    """
    gm = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="m", label="毛利率因子", unit="pp")])

    def build():
        a = assume.AssumptionSet(code="600183", labels=["2026E", "2027E"],
                                 decomps=[gm])
        for y, (bear, base, bull) in ((1, ("0.5", "1.3", "2.0")),
                                      (2, ("0.5", "3.1", "3.0"))):
            a.rows.append(assume.Row(
                driver="gross_margin", year=y, note="理由", factor_key="m",
                bear=D(bear), base=D(base), bull=D(bull)))
        return a

    anchor = _anchor_600183()
    base_margin = D("0.2647")

    # 第一阶段的口径：带 anchor，查平移后的路径 —— PASS。
    assume.check(build(), 2, ledger=FactLedger(), body="正文", as_of=AS_OF,
                decomps=[gm], base_margin=base_margin, anchor=anchor)

    # 第二阶段修复前的口径：不带 anchor，查未平移的路径 —— FAIL，
    # 且报出的三档数字与实跑现场一致。
    with pytest.raises(AssumeFailed, match=r"26\.97%.*29\.57%.*29\.47%"):
        assume.check(build(), 2, ledger=FactLedger(), body="正文", as_of=AS_OF,
                    decomps=[gm], base_margin=base_margin)


def test_读回的分解式里过短检索词不会让第二阶段崩成traceback():
    """`from_json` 里 `_decomp_from_dict` 直接构造 `Factor`，而
    `full.py` 的调用点（第 1741 行）**完全没兜**——`run_valuation` 对
    `AssumeFailed` 的捕获在第 1809 行，**在 from_json 之后**，所以那里
    抛任何异常都会以 traceback 的形式崩掉第二阶段命令。

    人手改过的 assumptions.json 应该得到一句能看懂的话，不是 traceback。
    措辞照 `assume.py` 既有那条「请重跑第一阶段」。
    """
    import pytest

    from ir_agent import assume
    from ir_agent.drivers import DecompositionInvalid

    bad = {"code": "600183", "labels": [], "rows": [],
           "decomps": [{"target": "revenue", "op": "乘", "factors": [
               {"key": "capacity", "label": "产能利用率", "unit": "变化率",
                "source_hint": "年报", "search_term": "率"}]}]}
    with pytest.raises(DecompositionInvalid):
        assume.from_json(bad, code="600183")


def test_判断项落盘往返不丢检索词():
    """`_decomp_to_dict`/`_decomp_from_dict` 两边都要带上，缺一边就在
    第二阶段读回时静默丢掉。读回侧用 `.get` 带默认值，与既有的
    `f.get("source_hint", "")` 同形——旧的 assumptions.json 没有这个键。
    """
    from ir_agent.assume import _decomp_from_dict, _decomp_to_dict
    from ir_agent.drivers import Decomposition, Factor, Op

    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="shipment", label="出货量同比增速", unit="变化率",
               source_hint="年报", search_term="出货量"),
        Factor(key="asp", label="单价", unit="变化率", source_hint="年报")])
    back = _decomp_from_dict(_decomp_to_dict(d))
    got = {f.label: f.search_term for f in back.factors}
    assert got["出货量同比增速"] == "出货量", "往返丢了检索词"
    assert got["单价"] == ""

    # 旧文件没有这个键，不能崩
    raw = _decomp_to_dict(d)
    for f in raw["factors"]:
        f.pop("search_term")
    assert all(f.search_term == "" for f in _decomp_from_dict(raw).factors)


# ── 合成后果换算：把增速路径翻译成隐含市占率 ──────────────────────
#
# **生益科技 600183 实跑（2026-10-06）。** 分解式是
# 「营收 = 覆铜板销量同比增速 × 覆铜板平均售价同比增速 × 高频高速覆铜板
# 收入同比增速」—— 销量×售价已经是收入，第三项又是一个收入量级的增速，
# 乘法链因此重复计算。三个各约 20% 的因子相乘得到中性档 71.75%。
#
# 合成表如实印出了 71.75%/66.88%/59.31%/50.69%/43.71%，而它过了全部闸门:
# 单位检查（三项都是「变化率」）、三表勾稽（股本/增速不参与）、140 格复算
# （复算同一套公式）、每股价值量级对账（183/130 = 1.4 倍，落在 10 倍容差内）、
# 以及人工门（批准人看到的就是这张表）。
#
# **71.75% 看不出荒谬，「隐含市占率 39%」看得出。** 账本里有换算所需的
# 全部事实:
#     本司份额 8.89%   CR5 47.13%   行业增速   2025A 营收 284.3 亿
# 推出: 五年累计 8.05 倍 → 2030E 营收 2,289 亿 → 隐含份额 39.2%，而当前
# 前五名合计才 47.13% —— 等于一家吃掉接近当前 CR5 的全部。
#
# 这是唯一不依赖模型判断的那道检查: 它把「这个增速高不高」（见解之争）
# 变成「这个份额可能吗」（算术之争）。
class TestImpliedScale:
    def _s(self, rates, share=D("0.0889"), cr5=D("0.4713"), ind=D("0.128")):
        from ir_agent.assume import implied_scale
        return implied_scale([D(str(r)) for r in rates],
                             share_now=share, cr5=cr5, industry_growth=ind)

    def test_生益实测那条路径算出隐含份额(self):
        """含锚定后的首年 39.85%，其余四年是批准值。"""
        s = self._s(["0.3985", "0.6688", "0.5931", "0.5069", "0.4371"])
        assert s.multiple == pytest.approx(D("8.05"), abs=D("0.05"))
        assert s.implied_share == pytest.approx(D("0.392"), abs=D("0.01"))

    def test_份额相对倍数超过二即阻断(self):
        """生益科技那条 8.05 倍路径: 相对倍数 4.4。"""
        s = self._s(["0.3985", "0.6688", "0.5931", "0.5069", "0.4371"])
        assert s.blocking and "相对倍数" in s.reason
        assert s.share_ratio > 4

    def test_超过CR5也阻断(self):
        """份额涨到超过当前前五名合计 —— 等于一家吃掉整个头部。"""
        s = self._s(["1.0"] * 5, share=D("0.30"), cr5=D("0.60"))
        assert s.blocking

    def test_隐含份额超过百分之百必然阻断(self):
        """数学上不可能，与阈值无关。"""
        s = self._s(["1.24"] * 5, share=D("0.0889"), cr5=None)
        assert s.implied_share > 1 and s.blocking

    def test_温和的路径不阻断(self):
        """份额从 8.89% 到 12% 左右 —— 快于行业但可能，不该误报。"""
        s = self._s(["0.18", "0.16", "0.15", "0.14", "0.13"])
        assert not s.blocking, s.reason

    def test_判据与份额水平无关(self):
        """**离线消融证明旧判据是惰性的。** 旧判据「隐含份额 > 2×当前份额」
        的阈值随份额水平走: 份额 8.9% 的公司阈值 17.8%，份额 47% 的公司阈值
        94% —— 同一条规则对龙头极宽松、对小公司极严苛，10 份历史判断项
        **一次都不触发**。

        改用相对倍数后，同一条增速路径在任何份额水平上得到同一个结论。"""
        rates = ["0.5"] * 5                      # 相对倍数固定
        small = self._s(rates, share=D("0.02"), cr5=D("0.40"))
        big = self._s(rates, share=D("0.45"), cr5=D("0.90"))
        assert small.share_ratio == pytest.approx(big.share_ratio, rel=D("0.001"))
        assert small.blocking and big.blocking, "同一路径在两种份额水平下结论不同"

    def test_历史存活样本全部通过而灾难案例被拦(self):
        """离线消融（10 份历史判断项）测到的相对倍数区间是 0.78~1.66，而那条
        五年 8.05 倍营收的灾难路径是 4.4。阈值 2.0 必须把两者分开 —— 否则
        要么回溯误报历史产物，要么漏掉它本来要防的那一个。

        用目标倍数反推等效路径（行业按 12.8% 外推，五年 1.8252 倍）: 
        公司累计 = 目标倍数 × 1.8252，均匀年增速 = 该值的五次方根 − 1。
        """
        ind = (D("1.128") ** 5)

        def path_for(ratio):
            mult = D(str(ratio)) * ind
            r = mult ** (D(1) / D(5)) - 1
            return [r] * 5

        for ratio in ("0.78", "1.09", "1.46", "1.66"):        # 存活区间
            s = self._s(path_for(ratio))
            assert s.share_ratio == pytest.approx(D(ratio), rel=D("0.01"))
            assert not s.blocking, f"回溯误报了历史样本：{ratio} → {s.reason}"

        bad = self._s(path_for("4.4"))                        # 灾难案例
        assert bad.blocking and "相对倍数" in bad.reason

    def test_行业负增长不放大隐含份额(self):
        """行业最近一期负增长时，若按它外推五年，行业会缩掉一半、隐含份额
        凭空翻倍 —— 那是外推假设造成的误报，不是公司的问题。增速取下限 0
        并在文字里说明。"""
        a = self._s(["0.10"] * 5, ind=D("-0.1826"))
        b = self._s(["0.10"] * 5, ind=D("0"))
        assert a.implied_share == b.implied_share
        assert "行业增速按 0" in a.describe()

    def test_描述里要给出可核对的中间量(self):
        """只给结论无从复核 —— 累计倍数、隐含营收、当前份额都要印出来。"""
        d = self._s(["0.3985", "0.6688", "0.5931", "0.5069", "0.4371"],
                    ).describe()
        for frag in ("累计", "倍", "隐含份额", "当前份额", "CR5"):
            assert frag in d

    def test_缺份额事实时不判(self):
        """算不出来就不算 —— 不用猜一个份额去凑一个结论。"""
        from ir_agent.assume import implied_scale
        assert implied_scale([D("0.5")] * 5, share_now=None, cr5=None,
                             industry_growth=D("0.1")) is None


def _ledger_with_share(share="0.0889", cr5="0.4713", yoy="0.128"):
    from ir_agent.ledger import Fact, FactLedger, Method
    l = FactLedger()
    for k, v in (("company.market_share", share), ("industry.cr5", cr5),
                 ("industry.revenue_yoy", yoy)):
        l.put(Fact(key=k, value=D(v), unit="ratio", currency=None,
                   period="2025FY", as_of=date(2026, 4, 20),
                   source_id="agg", method=Method.COMPUTED))
    return l


def test_隐含份额超限时退回让模型重算():
    """**阻断的含义是退回重算，不是毙掉整轮。** check() 抛 AssumeFailed 会
    进 `_decide_once` 的重试循环并把理由附进提示词；连续失败后 decide() 仍
    会降级到两标量模式。生益科技实测那条路径（五年累计 8 倍）就该走到这里。"""
    a = assume.parse(_full(years=2), 2)
    # 年年翻倍 → 远超任何可能的份额。三档要一起改: 三档未严格拉开会先被
    # 另一条检查拦住，那样这条测试测的就不是它想测的东西了。
    for r in a.rows:
        if r.driver == "growth":
            r.bear, r.base, r.bull = D("0.9"), D("1.0"), D("1.1")
    with pytest.raises(assume.AssumeFailed, match="隐含份额"):
        assume.check(a, 2, ledger=_ledger_with_share(), body=BODY, as_of=AS_OF)


def test_正常路径不被阻断且算术后果印在清单顶部():
    a = assume.parse(_full(years=2), 2)
    assume.check(a, 2, ledger=_ledger_with_share(), body=BODY, as_of=AS_OF)
    assert a.implied is not None and not a.implied.blocking
    assert "算术后果" in a.describe() and "隐含份额" in a.describe()


def test_账本没有份额事实时这道检查不生效():
    """算不出来就不算 —— 不猜一个份额去凑一个阻断。"""
    a = assume.parse(_full(years=2), 2)
    for r in a.rows:
        if r.driver == "growth":
            r.bear, r.base, r.bull = D("0.9"), D("1.0"), D("1.1")
    assume.check(a, 2, ledger=FactLedger(), body=BODY, as_of=AS_OF)
    assert a.implied is None


def test_行业增速优先用多年CAGR而不是最近单年():
    """**实测（生益科技 2026-10-06）这道检查差一倍灵敏度。**
    `industry.revenue_yoy` 取到的是 AI 爆发那一年的 25.8%，外推五年让行业
    涨 3.15 倍，分母被撑大，隐含份额算出来反而低于当前份额 —— 在景气行业里
    这道检查接近失效。而清单自己的理由栏引用的是 `industry.revenue_cagr`
    （12.8%，注明「行业多年营收CAGR，中性路径的收敛锚点」）: 模型知道该用
    哪个数，而检查原先用错了那个数。"""
    from ir_agent.ledger import Fact, FactLedger, Method

    def _led(**kw):
        l = FactLedger()
        for k, v in kw.items():
            l.put(Fact(key=f"industry.{k}", value=D(v), unit="ratio",
                       currency=None, period="2025FY",
                       as_of=date(2026, 4, 20), source_id="agg",
                       method=Method.COMPUTED))
        l.put(Fact(key="company.market_share", value=D("0.0889"), unit="ratio",
                   currency=None, period="2025FY", as_of=date(2026, 4, 20),
                   source_id="agg", method=Method.COMPUTED))
        return l

    a = assume.parse(_full(years=2), 2)
    assume.check(a, 2, ledger=_led(revenue_cagr="0.128", revenue_yoy="0.258"),
                 body=BODY, as_of=AS_OF)
    assert a.implied.industry_growth == D("0.128"), "没优先用多年 CAGR"

    b = assume.parse(_full(years=2), 2)
    assume.check(b, 2, ledger=_led(revenue_yoy="0.258"), body=BODY, as_of=AS_OF)
    assert b.implied.industry_growth == D("0.258"), "CAGR 缺失时没回落到 yoy"
