"""分解式的表示与闭合校验。**闭合是硬约束** —— 不闭合的分解式不是
「不够精确」，是代码根本算不出来。"""

from __future__ import annotations

import pytest

from ir_agent.drivers import (MAX_FACTORS, MIN_FACTORS, TARGET_LABEL,
                              Decomposition, DecompositionInvalid, Factor,
                              Op, check_closure)


def _f(key="capacity", label="产能", unit="变化率"):
    return Factor(key=key, label=label, unit=unit, source_hint="年报产能表")


def _rev(*factors):
    return Decomposition(target="revenue", op=Op.MULTIPLY,
                         factors=list(factors) or [_f(), _f("asp", "单价")])


def test_合法的营收分解通过():
    check_closure(_rev(_f(), _f("utilization", "稼动率"), _f("asp", "单价")))


def test_合法的毛利率分解通过():
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util_leverage", label="稼动率摊薄", unit="pp"),
        Factor(key="depreciation", label="折旧变动", unit="pp")])
    check_closure(d)


def test_营收链里出现pp被驳回():
    """**pp 不能进乘法链。** 稼动率 85%→82% 是 −3pp，但乘法因子的变化率是
    82/85−1 = −3.5%。混用会让合成结果系统性偏错，而偏差不大、不易发现。"""
    with pytest.raises(DecompositionInvalid, match="变化率"):
        check_closure(_rev(_f(), _f("utilization", "稼动率", unit="pp")))


def test_毛利率链里出现变化率被驳回():
    d = Decomposition(target="gross_margin", op=Op.ADD_PP,
                      factors=[Factor(key="a", label="甲", unit="pp"),
                               Factor(key="b", label="乙", unit="变化率")])
    with pytest.raises(DecompositionInvalid, match="pp"):
        check_closure(d)


def test_单位不在白名单被驳回():
    """挡掉「客户结构优化」这类无法量化的因子 —— 写不出单位的项就写不进来，
    这是闭合校验真正的闸门。"""
    with pytest.raises(DecompositionInvalid, match="单位"):
        check_closure(_rev(_f(), _f("mix", "客户结构优化", unit="定性")))


def test_白名单外单位不误报为pp问题():
    """发现一：补充说明必须按「实际传入的 unit 到底错在哪」分支，不能按
    「目标链是什么」分支。「定性」根本不是 pp，报错不该把人导向
    「pp 不能进乘法链」这个不存在的问题 —— 误导性提示比没有提示更糟：
    用户会去检查一个不存在的问题，真正的原因（写不出单位的因子本就不该
    出现）反而被掩盖。"""
    with pytest.raises(DecompositionInvalid) as exc:
        check_closure(_rev(_f(), _f("mix", "客户结构优化", unit="定性")))
    msg = str(exc.value)
    # 不该出现 pp 专属的解释 —— 那是另一个问题的答案
    assert "pp 不能进乘法链" not in msg
    assert "−3pp" not in msg and "−3.5%" not in msg
    # 应该说清楚：白名单只有这两个值，且写不出单位的因子本不该在这里
    assert "变化率" in msg and "pp" in msg
    assert "本就不该出现" in msg or "不该出现在分解式" in msg


def test_加法链收到变化率不误报为pp问题():
    """同一个分支缺陷的另一面：加法链收到「变化率」（真实存在的另一条链的
    合法单位）时，才应该给出「比率的变化只能加减」这句针对性的说明；
    这句话不能被「定性」之类的白名单外单位借用。"""
    d = Decomposition(target="gross_margin", op=Op.ADD_PP,
                      factors=[Factor(key="a", label="甲", unit="pp"),
                               Factor(key="b", label="乙", unit="变化率")])
    with pytest.raises(DecompositionInvalid) as exc:
        check_closure(d)
    assert "比率的变化只能加减，不能相乘" in str(exc.value)


def test_乘法链收到pp给出针对性说明():
    """同一个分支缺陷的另一面：乘法链收到「pp」（真实存在的另一条链的
    合法单位）时，才应该给出 pp 专属的说明。"""
    with pytest.raises(DecompositionInvalid) as exc:
        check_closure(_rev(_f(), _f("utilization", "稼动率", unit="pp")))
    assert "pp 不能进乘法链" in str(exc.value)


def test_op与target必须匹配():
    d = Decomposition(target="revenue", op=Op.ADD_PP, factors=[_f(), _f("b")])
    with pytest.raises(DecompositionInvalid, match="revenue"):
        check_closure(d)


def test_因子太少被驳回():
    """一个因子不叫分解。"""
    with pytest.raises(DecompositionInvalid, match="至少"):
        check_closure(_rev(_f()))


def test_因子太多被驳回():
    """超过五个人核实不过来 —— 分解的价值在于人能检查，不在于穷举。"""
    many = [_f(f"k{i}", f"因子{i}") for i in range(MAX_FACTORS + 1)]
    with pytest.raises(DecompositionInvalid, match="至多"):
        check_closure(_rev(*many))


def test_因子key不得重复():
    with pytest.raises(DecompositionInvalid, match="重复"):
        check_closure(_rev(_f("capacity"), _f("capacity", "产能2")))


def test_未知target被驳回():
    d = Decomposition(target="ebitda", op=Op.MULTIPLY, factors=[_f(), _f("b")])
    with pytest.raises(DecompositionInvalid, match="ebitda"):
        check_closure(d)


def test_边界值刚好通过():
    assert MIN_FACTORS == 2 and MAX_FACTORS == 5
    check_closure(_rev(*[_f(f"k{i}", f"因子{i}") for i in range(MIN_FACTORS)]))
    check_closure(_rev(*[_f(f"k{i}", f"因子{i}") for i in range(MAX_FACTORS)]))


def test_describe乘法链产出可读表达式():
    """发现二：describe() 是公开成员，后续方法论预研模块要用它生成给人看的
    markdown —— 拼接逻辑（分隔符、"基期 +" 前缀）没有测试就是没有保护。"""
    d = _rev(_f(), _f("utilization", "稼动率"), _f("asp", "单价"))
    assert d.describe() == "营收 = 产能 × 稼动率 × 单价"


def test_describe加法链产出可读表达式带基期前缀():
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util_leverage", label="稼动率摊薄", unit="pp"),
        Factor(key="depreciation", label="折旧变动", unit="pp")])
    assert d.describe() == "毛利率 = 基期 + 稼动率摊薄 + 折旧变动"


def test_TARGET_LABEL覆盖所有分解目标():
    assert TARGET_LABEL == {"revenue": "营收", "gross_margin": "毛利率"}


# ============================================================================
# 新增: 合成与历史回算
# ============================================================================

from decimal import Decimal

from ir_agent.drivers import Residual, compose, pp_hint, reconcile

D = Decimal


def test_乘法链合成增速():
    """营收增速 = Π(1+r) − 1。"""
    d = _rev(_f("capacity"), _f("utilization", "稼动率"), _f("asp", "单价"))
    got = compose(d, {"capacity": D("0.12"), "utilization": D("-0.035"),
                      "asp": D("-0.02")})
    # 1.12 × 0.965 × 0.98 − 1 = 0.059184
    assert abs(got - D("0.059184")) < D("1e-6")


def test_加法链需要基期():
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util", label="稼动率摊薄", unit="pp"),
        Factor(key="dep", label="折旧变动", unit="pp")])
    got = compose(d, {"util": D("-1.5"), "dep": D("-0.8")}, base=D("0.1415"))
    # 14.15% − 1.5pp − 0.8pp = 11.85%
    assert abs(got - D("0.1185")) < D("1e-9")


def test_加法链缺基期就报错():
    """毛利率是绝对水平，没有基期算不出来 —— 默认成 0 会得到一个
    看起来正常的错数。"""
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="a", label="甲", unit="pp"),
        Factor(key="b", label="乙", unit="pp")])
    with pytest.raises(DecompositionInvalid, match="基期"):
        compose(d, {"a": D("1"), "b": D("1")})


def test_因子取值缺失就报错而非当零():
    """当零会让一个漏填悄悄变成「该因子无影响」。"""
    d = _rev(_f("capacity"), _f("asp", "单价"))
    with pytest.raises(DecompositionInvalid, match="capacity|缺"):
        compose(d, {"asp": D("0.01")})


def test_历史回算报残差():
    """Π(1+r) 几乎永远不等于实际增速 —— 产品结构、汇率、并表都会留下残差。
    要求严格相等等于禁用所有分解式，所以这里**报残差不判对错**。"""
    d = _rev(_f("capacity"), _f("asp", "单价"))
    hist = {"2024FY": {"capacity": D("0.10"), "asp": D("0.05")},
            "2025FY": {"capacity": D("0.08"), "asp": D("0.02")}}
    actual = {"2024FY": D("0.16"), "2025FY": D("0.11")}
    got = reconcile(d, hist, actual)
    assert [r.period for r in got] == ["2024FY", "2025FY"]
    # 1.10×1.05−1 = 0.155 → 残差 0.16−0.155 = +0.005
    assert abs(got[0].gap - D("0.005")) < D("1e-9")
    assert abs(got[1].gap - D("0.0084")) < D("1e-9")


def test_回算跳过数据不全的期间():
    """标「无历史校验」由调用方负责；这里只做能算的，绝不用零补齐。"""
    d = _rev(_f("capacity"), _f("asp", "单价"))
    hist = {"2024FY": {"capacity": D("0.10")},          # 缺 asp
            "2025FY": {"capacity": D("0.08"), "asp": D("0.02")}}
    got = reconcile(d, hist, {"2024FY": D("0.16"), "2025FY": D("0.11")})
    assert [r.period for r in got] == ["2025FY"]


def test_回算缺实际值也跳过():
    d = _rev(_f("capacity"), _f("asp", "单价"))
    hist = {"2025FY": {"capacity": D("0.08"), "asp": D("0.02")}}
    assert reconcile(d, hist, {}) == []


def test_pp提示仅在基期已知时给出():
    """基期未知就只显示变化率，**绝不倒推** —— 倒推出来的 pp 是个凭空的数。"""
    assert pp_hint(D("-0.035"), D("0.85")) == "-3.50%（约 -2.98pp）"
    assert pp_hint(D("-0.035"), None) == "-3.50%"
