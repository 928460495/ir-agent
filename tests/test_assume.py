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
