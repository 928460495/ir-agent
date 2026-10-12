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
    """当零会让一个漏填悄悄变成「该因子无影响」。断言要点名 capacity ——
    实现精确点名了缺失的因子，测试的强度不该低于实现（发现二）。"""
    d = _rev(_f("capacity"), _f("asp", "单价"))
    with pytest.raises(DecompositionInvalid, match="capacity"):
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


# ============================================================================
# 发现一: reconcile 的 ADD_PP（加法）链路径 —— base 参数只在这条链上有意义，
# 之前 8 条新测试全部走 MULTIPLY 链，这条实现了但从未被跑到过。
# ============================================================================

def _gm_decomp():
    return Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util", label="稼动率摊薄", unit="pp"),
        Factor(key="dep", label="折旧变动", unit="pp")])


def test_加法链回算给出基期能算出残差():
    """给了 base 的期间：正常合成、报残差 —— 和 MULTIPLY 链的语义一致，
    只是公式换成 base + Σpp/100。"""
    d = _gm_decomp()
    hist = {"2024FY": {"util": D("-1.5"), "dep": D("-0.8")}}
    actual = {"2024FY": D("0.12")}
    base = {"2024FY": D("0.1415")}
    got = reconcile(d, hist, actual, base=base)
    assert [r.period for r in got] == ["2024FY"]
    # 0.1415 − 1.5pp − 0.8pp = 0.1185；残差 = 0.12 − 0.1185
    assert abs(got[0].composed - D("0.1185")) < D("1e-9")
    assert abs(got[0].gap - D("0.0015")) < D("1e-9")


def test_加法链缺基期的期间被跳过不影响其他期间():
    """base 是按期间给的字典：某一期没有 base 就只跳过那一期，
    不拖累其他期间的回算。"""
    d = _gm_decomp()
    hist = {"2024FY": {"util": D("-1.5"), "dep": D("-0.8")},
            "2025FY": {"util": D("1.0"), "dep": D("0.5")}}
    actual = {"2024FY": D("0.12"), "2025FY": D("0.13")}
    base = {"2024FY": D("0.1415")}          # 2025FY 没有基期
    got = reconcile(d, hist, actual, base=base)
    assert [r.period for r in got] == ["2024FY"]


def test_加法链base缺失不该speculatively调用compose(monkeypatch):
    """发现一的核心：base 缺失必须是**显式前置检查**挡住的期间，不能是
    「先调用 compose、指望它抛 DecompositionInvalid、再拿 except 吞掉」。
    后一种写法把「base 缺失」和「真正的闭合性错误」混进了同一个 except，
    compose 的校验逻辑将来一变，这里不会有任何测试报警。

    用 monkeypatch 换一个「在 base=None 时抛出非 DecompositionInvalid
    异常」的 compose：如果 reconcile 仍然会在 base 缺失时把这期传给
    compose，这个替身异常就会一路冒出来，测试立刻失败；只有 reconcile
    在调用 compose 之前就用显式检查跳过该期间，这个测试才会通过。"""
    import ir_agent.drivers as drivers_mod

    d = _gm_decomp()
    hist = {"2024FY": {"util": D("-1.5"), "dep": D("-0.8")}}
    actual = {"2024FY": D("0.12")}

    calls: list[Decimal | None] = []

    def fake_compose(dd, vals, base=None):
        calls.append(base)
        if base is None:
            raise AssertionError(
                "reconcile 不该在 base 缺失时仍把该期间传给 compose —— "
                "base 缺失必须在调用 compose 之前被显式前置检查挡住")
        return drivers_mod.Decimal("0")

    monkeypatch.setattr(drivers_mod, "compose", fake_compose)
    got = drivers_mod.reconcile(d, hist, actual)  # 完全不给 base
    assert calls == [], "compose 根本不该在 base 缺失时被调用"
    assert got == []


def test_营收乘法链拒收占比类因子():
    """**生益科技实跑**：「营收 = 覆铜板产能 × 产能利用率 × 铜价传导系数 ×
    高端产品(M9/M10)收入占比」。高端占比 20%→22.4% 是 +12% 的相对变化，但营收
    不会因此多长 12%——结构变化本身不产生营收，只经由均价体现。四个乘数里单项
    贡献最大的恰恰是这个量纲错误，而 check_closure 只查算术闭合，放行了。"""
    import pytest
    from ir_agent.drivers import (Decomposition, DecompositionInvalid, Factor,
                                  Op, check_closure)
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="cap", label="覆铜板产能", unit="变化率"),
        Factor(key="mix", label="高端产品收入占比", unit="变化率")])
    with pytest.raises(DecompositionInvalid, match="占比"):
        check_closure(d)


def test_市占率可以进营收乘法链():
    """营收 = 行业规模 × 市占率 是合法的乘法分解——分母在公司之外。"""
    from ir_agent.drivers import Decomposition, Factor, Op, check_closure
    check_closure(Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="mkt", label="行业规模", unit="变化率"),
        Factor(key="shr", label="市占率", unit="变化率")]))


def test_毛利率加法链里的结构因子不受影响():
    from ir_agent.drivers import Decomposition, Factor, Op, check_closure
    check_closure(Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="mix", label="产品结构(先进封装收入占比)", unit="pp"),
        Factor(key="cost", label="上游成本", unit="pp")]))


# ── I1（final-fix-D 终审）：占比闸门要按量纲收，不能靠一字之差绕开 ──────
#
# 审查者实跑发现两个具体问题：
# 1. 旧的 `_SHARE_OK = re.compile(r"市占|份额")` 在**整条标签**上做豁免，
#    于是「客户份额占比」这种既含「占比」又含「份额」的写法，量纲错误
#    分毫未改，仅仅因为字符串里出现了「份额」两个字就被放行。
# 2. 旧的 `_MIX` 只认「占比|比重|结构」，「比例/构成/渗透率/化率/贡献度」
#    这类同义写法完全漏网；而「结构件」这种真实产品品类名（如"精密结构件"）
#    又被「结构」误伤。
#
# 8 个应挡下的同义写法 + 3 个不该被误伤的合法标签，每个都验两个方向：
# ①在营收乘法链里挡对/放对；②同一个标签放进毛利率加法链，这道闸门根本
# 不该管（`d.op is Op.MULTIPLY` 已经限定了作用范围），不能被误伤。

_MIX_SYNONYMS_SHOULD_REJECT = [
    "高端产品收入占比",
    "高端产品收入份额",
    "客户份额占比",
    "高端产品收入比例",
    "高端产品收入构成",
    "高端产品收入渗透率",
    "高端产品国产化率",
    "高端产品收入贡献度",
]

_SHOULD_NOT_BE_MISFLAGGED = [
    "市占率",
    "市场份额",
    "结构件出货量",
]


@pytest.mark.parametrize("label", _MIX_SYNONYMS_SHOULD_REJECT)
def test_占比类同义写法在营收乘法链里必须被挡住(label):
    """一字改名不该绕过量纲闸门——「占比」换成「份额」「比例」「构成」
    「渗透率」「化率」「贡献度」，说的是同一件事：拿公司自己收入的一部分
    除以公司自己的总量，不是合法的营收乘数。"""
    from ir_agent.drivers import Decomposition, DecompositionInvalid, Factor, Op, check_closure
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="cap", label="产能", unit="变化率"),
        Factor(key="mix", label=label, unit="变化率")])
    with pytest.raises(DecompositionInvalid):
        check_closure(d)


@pytest.mark.parametrize("label", _SHOULD_NOT_BE_MISFLAGGED)
def test_市占率与产品品类名不受占比闸门误伤(label):
    """「市占率/市场份额」分母在公司之外，是合法的营收乘数；「结构件」
    是真实产品品类名（如"精密结构件"），不是「产品结构」这种组合变量——
    两者都不该被这道闸门挡下。"""
    from ir_agent.drivers import Decomposition, Factor, Op, check_closure
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="cap", label="产能", unit="变化率"),
        Factor(key="ok", label=label, unit="变化率")])
    check_closure(d)


@pytest.mark.parametrize("label", _MIX_SYNONYMS_SHOULD_REJECT + _SHOULD_NOT_BE_MISFLAGGED)
def test_占比闸门只管营收乘法链不管毛利率加法链(label):
    """同一个标签放进毛利率加法链，这道闸门根本不该管——它只查
    `d.op is Op.MULTIPLY` 这一条链，加法链里"结构""份额"这类因子完全
    合法（毛利率的产品结构因子就是典型用法）。"""
    from ir_agent.drivers import Decomposition, Factor, Op, check_closure
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="cost", label="上游成本", unit="pp"),
        Factor(key="mix", label=label, unit="pp")])
    check_closure(d)


# ── 检索词：由模型决定，代码只查长度 ──────────────────────────
#
# B3 第一版靠括号这个结构信号切分因子标签。实测它对**不带括号但同样
# 冗长**的标签无能为力：「服务器及通信用高端覆铜板出货量同比增速」剥掉
# 运算后缀仍 17 字，不会逐字出现在任何年报里。唯一知道该用什么词检索的
# 是生成这个标签的那个模型，所以给它一条显式通道。


def test_检索词不必是标签的子串():
    """**这条钉住「放开闸门」这个决定。**

    设计过程中先定的是「必须是 label 的子串」，用户裁定改为放开：在因子
    检索这一环尽量不设闸门，由模型决定。「客户集中度」在年报里真实存在、
    比标签里任何一段都准，而它不是标签的子串。

    这条红了意味着有人把子串闸门加回来了——那是一个要重新讨论的决定，
    不是一次「加固」。要重开请先读 spec 第二节那张实测命中表。
    """
    from ir_agent.drivers import Factor

    f = Factor(key="k", label="前五大客户销售收入占营业收入比重变化率",
               unit="变化率", search_term="客户集中度")
    assert f.search_term == "客户集中度"


def test_检索词的长度下限仍然生效():
    """1 字检索词在任何年报里都**必然**命中，那不是「模型决定」而是
    「定然命中」，会把所有因子都抬成「年报披露」、让这个状态标签彻底
    失去信息量。放开闸门是为了让模型的判断生效，不是让判断失效。
    """
    import pytest

    from ir_agent.drivers import DecompositionInvalid, Factor

    with pytest.raises(DecompositionInvalid):
        Factor(key="k", label="产能利用率", unit="变化率", search_term="率")
    # 空检索词通过——旧的 3 段 DSL 与旧的 JSON 都靠这条向后兼容
    assert Factor(key="k", label="产能利用率", unit="变化率").search_term == ""


def test_代码不下探括号内而模型可以给任何词():
    """**这处不对称是刻意的，两面同时钉住。** 分开写，将来只会有一半
    被读到。

    · 代码侧：B3 第一版的决定，不得倒退——实测代码分不开「铜箔」
      （真信号，命中 6 次）与「通信」（巧合，命中 4 次），长度分不开。
    · 模型侧：完全自由，连不在标签里的词也可以给。

    谁要「修平」这处不对称，请先读 spec 第二节。往任一方向修都会破坏
    一个已做出的决定。
    """
    from ir_agent.drivers import Factor
    from ir_agent.gaps import _core_candidates

    assert "铜箔" not in _core_candidates(
        "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)")
    assert Factor(key="k", label="原材料成本(铜箔/电子级玻纤布/环氧树脂价格)",
                  unit="pp", search_term="铜箔").search_term == "铜箔"


# ── 乘法链禁止收入量级因子 ────────────────────────────────────────
#
# **生益科技 600183 实跑（2026-10-06）。** 模型给出的营收分解式是
#
#     营收 = 覆铜板销量同比增速 × 覆铜板平均售价同比增速
#            × 高频高速覆铜板收入同比增速
#
# 销量 × 售价**已经是收入**，第三项又是一个收入量级的增速，于是同一个量被
# 算了两遍。三个各约 20% 的因子相乘得到中性档 71.75%，五年累计 8.05 倍
# （营收 284 亿 → 2,289 亿，隐含市占率 39.2%，而当前 CR5 才 47.13%）。
#
# 既有的单位检查放行了它 —— 三项的单位都是「变化率」，完全合规。单位对了
# 不代表量纲对: **乘法链的因子必须是营收的不同维度（量、价、份额），而不
# 能是营收本身。** 这与 `_is_mix_factor` 是同一类词法规则，判据同样是
# 「这个因子衡量的到底是什么」。
class TestNoRevenueLevelFactorInMultiplyChain:
    def _d(self, *labels):
        from ir_agent.drivers import (Decomposition, Factor, Op,
                                      check_closure)
        d = Decomposition(
            target="revenue", op=Op.MULTIPLY,
            factors=[Factor(key=f"k{i}", label=l, unit="变化率")
                     for i, l in enumerate(labels)])
        check_closure(d)
        return d

    def test_收入量级的因子不得进乘法链(self):
        from ir_agent.drivers import DecompositionInvalid

        with pytest.raises(DecompositionInvalid, match="收入"):
            self._d("覆铜板销量同比增速", "覆铜板平均售价同比增速",
                    "高频高速覆铜板收入同比增速")

    def test_营收与销售额同样算收入量级(self):
        from ir_agent.drivers import DecompositionInvalid

        for bad in ("海外营收同比增速", "单客户销售额增速", "营业额增速"):
            with pytest.raises(DecompositionInvalid):
                self._d("覆铜板销量同比增速", "覆铜板平均售价同比增速", bad)

    def test_量价份额三类仍然合法(self):
        """今天两个标的实际用过的分解式都必须照旧通过 —— 这条防的是
        规则收得过紧。"""
        self._d("覆铜板销量同比增速", "覆铜板平均售价同比增速",
                "全球市场占有率变化率")
        self._d("茅台酒销量", "茅台酒出厂价", "系列酒销量", "系列酒出厂价")

    def test_驳回信息不给可照抄的安全词(self):
        """与 _is_mix_factor 那条同一处教训: 结尾写「改成售价就行」会让模型
        把标签改个字绕过去，量纲错误一字未改。"""
        from ir_agent.drivers import DecompositionInvalid

        with pytest.raises(DecompositionInvalid) as e:
            self._d("销量同比增速", "售价同比增速", "分部收入同比增速")
        msg = str(e.value)
        assert "算了两遍" in msg or "重复" in msg
        assert "改成" not in msg

    def test_加法链不受这条限制(self):
        """毛利率加法链里出现「收入占比」是正当的（那是结构变动的贡献），
        这条规则只管乘法链。"""
        from ir_agent.drivers import (Decomposition, Factor, Op,
                                      check_closure)
        check_closure(Decomposition(
            target="gross_margin", op=Op.ADD_PP,
            factors=[Factor(key="a", label="高毛利产品收入贡献", unit="pp"),
                     Factor(key="b", label="原材料成本变动", unit="pp")]))


def test_变化率后缀不被当成内部比率():
    """**既有规则的误伤，这一轮才发现。** `_MIX` 里的 `化率` 会匹配上
    **`变化率`** —— 而「变化率」正是乘法链因子的标准单位后缀（`UNITS`
    里写着），于是「市占率变化率」这类**完全正当**的营收乘数被拦下。

    而同一条规则的驳回信息自己写着:「只有分母在公司之外的份额型指标
    （该指标 × 行业规模 = 营收）才是合法的营收乘数」—— **规则说它合法，
    代码拦下它。** 这是本会话第八次撞上「声明与现实之间的缝」。
    """
    from ir_agent.drivers import _is_mix_factor

    for ok in ("市占率变化率", "全球市场占有率变化率", "销量变化率"):
        assert not _is_mix_factor(ok), f"正当的乘数被当成内部比率：{ok}"
    for bad in ("转化率提升", "渗透率变化", "高端产品收入占比"):
        assert _is_mix_factor(bad), f"真正的内部比率漏网：{bad}"


def test_分母在公司之外的份额写法不止市占率一种():
    """**牧原股份 002714 实跑（2026-10-07）: 这个误伤毁掉了整家公司的因子
    分解。** 预研连续 3 次被拒，被拒的因子是「公司出栏量占全国比重」——
    分母是**全国**，在公司之外，而同一条规则的驳回信息自己写着「只有分母在
    公司之外的份额型指标（该指标 × 行业规模 = 营收）才是合法的营收乘数」。

    豁免名单原先只认「市占率」「市场份额」两个写法，而**生猪养殖的自然口径
    就是「出栏量占全国比重」**（行业指标是全国出栏量，不是营收）—— 所以这个
    误伤在农业、大宗这类以产量算份额的行业里几乎必然发作。

    这与昨天那个 `化率` 误伤 `变化率` 是同一个病: 豁免名单跟不上真实措辞。
    判据始终只有一个 —— **分母在公司之内还是之外。**
    """
    from ir_agent.drivers import _is_mix_factor

    外部分母 = ("公司出栏量占全国比重", "出栏量占行业比重", "全国市场份额",
                "市占率变化率", "全球市场占有率", "生猪出栏占全国份额",
                "市场占比提升")
    for ok in 外部分母:
        assert not _is_mix_factor(ok), f"分母在公司之外，不该拦：{ok}"

    内部分母 = ("客户份额占比", "高端产品收入占比", "前五大客户销售收入占比",
                "转化率提升", "产品结构变化", "市场收入占比")
    for bad in 内部分母:
        assert _is_mix_factor(bad), f"分母在公司之内，必须拦：{bad}"


def test_收入只是修饰语时不算收入量级():
    """**离线消融（56 个历史因子）里唯一的误伤。** 「出口收入人民币兑美元
    汇率变化」衡量的是汇率，不是收入 —— 标签里的「收入」是修饰语，说明
    这个汇率影响的是哪一块收入。判据是标签里有没有明确的非收入计量词。"""
    from ir_agent.drivers import _is_revenue_level

    for ok in ("出口收入人民币兑美元汇率变化", "海外收入汇率影响",
               "收入加权平均单价", "分部收入价格传导"):
        assert not _is_revenue_level(ok), f"衡量的不是收入，不该拦：{ok}"
    for bad in ("分部收入增速", "直营渠道收入增速", "系列酒收入增速",
                "高频高速覆铜板收入同比增速", "海外营收同比增速"):
        assert _is_revenue_level(bad), f"衡量的就是收入，必须拦：{bad}"
