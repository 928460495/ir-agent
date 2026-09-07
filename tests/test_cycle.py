"""商业周期定位与向行业的传导。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent import cycle
from ir_agent.cycle import Direction, Phase
from ir_agent.ledger import Fact, FactLedger, Method

D = Decimal
AS_OF = date(2026, 9, 7)


def _months(led, key, values, unit="ratio", start=1):
    """按 2026-01、2026-02… 逐月写入。"""
    for i, v in enumerate(values, start):
        led.put(Fact(key=key, value=D(str(v)), unit=unit,
                     period=f"2026-{i:02d}", as_of=date(2026, i, 20),
                     source_id="t", method=Method.REPORTED))


def _rates(led, tens, twos):
    for i, (a, b) in enumerate(zip(tens, twos), 1):
        for key, v in (("rate.cgb_10y", a), ("rate.cgb_2y", b)):
            led.put(Fact(key=key, value=D(str(v)), unit="ratio",
                         period=f"2026-0{i}-01", as_of=date(2026, i, 1),
                         source_id="t", method=Method.REPORTED))


def _scenario(pmi, gap, tens, twos, ppi):
    led = FactLedger()
    _months(led, "macro.pmi_manufacturing", pmi, unit="index")
    _months(led, "macro.m1_m2_gap", gap)
    _months(led, "macro.ppi_yoy", ppi)
    _rates(led, tens, twos)
    return led


# ── 方向判定 ─────────────────────────────────────────────────

def test_方向用首末之差而非逐期比较():
    """月度数据噪声大，逐期比较会把锯齿读成反转。"""
    assert cycle._direction([D(1), D(3), D(2), D(5)]) is Direction.UP
    assert cycle._direction([D(5), D(2), D(3), D(1)]) is Direction.DOWN


def test_微小变化视为持平():
    assert cycle._direction([D("50.0"), D("50.1")]) is Direction.FLAT


def test_单期无方向():
    assert cycle._direction([D(1)]) is Direction.FLAT


# ── 四个维度 ─────────────────────────────────────────────────

def test_活动看PMI的水平与荣枯线():
    led = FactLedger()
    _months(led, "macro.pmi_manufacturing", [48, 49, 51], unit="index")
    s = cycle.activity(led, AS_OF)
    assert "扩张区" in s.detail and s.direction is Direction.UP


def test_信贷看剪刀差正负():
    led = FactLedger()
    _months(led, "macro.m1_m2_gap", ["-0.01", "-0.03", "-0.037"])
    s = cycle.credit(led, AS_OF)
    assert "资金沉淀" in s.detail and s.direction is Direction.DOWN


def test_政策看期限利差形态():
    led = FactLedger()
    _rates(led, ["0.017", "0.016", "0.015"], ["0.020", "0.019", "0.019"])
    s = cycle.policy(led, AS_OF)
    assert "倒挂" in s.detail          # 10Y < 2Y


def test_缺数据的维度直接不参与():
    v = cycle.assess(FactLedger(), AS_OF)
    assert v.signals == [] and v.phase is None
    assert "无可用宏观信号" in v.describe()


# ── 阶段判定：水平不足以定位，必须看方向 ─────────────────────

def test_早周期与衰退靠活动方向区分():
    """两者在期限利差上都表现为陡峭化（都对应宽松预期）——
    只看利差会把复苏初期误判成衰退。"""
    steep_t, steep_2 = [0.030, 0.031, 0.032], [0.010, 0.010, 0.010]
    early = cycle.assess(_scenario(
        pmi=[47, 49, 51], gap=["-0.01", "0.0", "0.01"],
        tens=steep_t, twos=steep_2, ppi=["-0.02", "-0.01", "0.0"]), AS_OF)
    recession = cycle.assess(_scenario(
        pmi=[51, 49, 46], gap=["0.01", "-0.01", "-0.03"],
        tens=steep_t, twos=steep_2, ppi=["0.02", "0.0", "-0.02"]), AS_OF)
    assert early.phase is Phase.EARLY
    assert recession.phase is Phase.RECESSION


def test_中周期():
    v = cycle.assess(_scenario(
        pmi=[52, 52, 52], gap=["0.02", "0.02", "0.02"],
        tens=[0.030, 0.028, 0.026], twos=[0.010, 0.011, 0.012],
        ppi=["0.0", "0.01", "0.02"]), AS_OF)
    assert v.phase in (Phase.MID, Phase.LATE)


def test_不用状态机可跳过阶段():
    """课程明确说阶段可能被跳过或回退 —— 判定只看当期信号，
    不依赖上一次判定。"""
    v1 = cycle.assess(_scenario([52, 52, 52], ["0.02"] * 3,
                                [0.03, 0.03, 0.03], [0.01] * 3,
                                ["0.01"] * 3), AS_OF)
    v2 = cycle.assess(_scenario([46, 44, 42], ["-0.04"] * 3,
                                [0.03, 0.031, 0.032], [0.01] * 3,
                                ["0.02", "0.0", "-0.02"]), AS_OF)
    assert v1.phase != v2.phase       # 直接跳，无需经过中间阶段


# ── 交界的诚实报告 ───────────────────────────────────────────

def test_首选与次选接近时标注交界():
    """实测当前数据: 后周期 0.88 vs 衰退 0.88 —— 报平局比硬选一个诚实。"""
    v = cycle.assess(_scenario(
        pmi=[50, 49.5, 49.2], gap=["-0.03", "-0.035", "-0.037"],
        tens=[0.017, 0.017, 0.0168], twos=[0.0125, 0.0124, 0.0124],
        ppi=["0.041", "0.038", "0.035"]), AS_OF)
    assert v.at_boundary
    assert "交界" in v.describe() and "不宜据此下强结论" in v.describe()
    assert "阶段交界" in v.for_prompt()


def test_次选与差距一并报出():
    v = cycle.assess(_scenario([47, 49, 51], ["0.0"] * 3,
                               [0.03] * 3, [0.01] * 3, ["0.0"] * 3), AS_OF)
    assert len(v.ranked) == 4
    assert "次选" in v.describe()


def test_缺失维度显式记录():
    """Fidelity 用于定义阶段的五维是 Activity/Credit/Profits/Policy/
    Inventories-Sales。利润与库存销售都取不到 —— 两维都要声明。"""
    v = cycle.assess(_scenario([50] * 3, ["0.0"] * 3, [0.03] * 3,
                               [0.01] * 3, ["0.0"] * 3), AS_OF)
    assert any("利润" in m for m in v.missing)
    assert any("库存与销售" in m for m in v.missing)
    assert v.coverage == "3/5 个阶段维度"          # 给人看，带比例
    assert v.coverage_note == "部分阶段维度"        # 给模型看，定性
    assert "未覆盖" in v.describe()
    assert "该判定未覆盖" in v.for_prompt()


def test_通胀是补充分析不参与打分():
    """Fidelity 原文: inflation 属于 complementary analysis，
    不是定义阶段的维度。拿它打分等于用框架外的变量定义框架内的阶段。"""
    v = cycle.assess(_scenario([50] * 3, ["0.0"] * 3, [0.03] * 3,
                               [0.01] * 3, ["0.035"] * 3), AS_OF)
    assert "通胀" not in {s.dimension for s in v.signals}
    assert "通胀" in {s.dimension for s in v.complementary}
    assert "不参与阶段判定" in v.describe()
    assert "未参与阶段判定" in v.for_prompt()


def test_通胀读数变化不改变阶段判定():
    base = dict(pmi=[50] * 3, gap=["0.0"] * 3,
                tens=[0.03] * 3, twos=[0.01] * 3)
    low = cycle.assess(_scenario(**base, ppi=["-0.05"] * 3), AS_OF)
    high = cycle.assess(_scenario(**base, ppi=["0.08"] * 3), AS_OF)
    assert low.scores == high.scores


# ── 传导 ─────────────────────────────────────────────────────

def _view(phase=Phase.LATE, conf=D("0.3")):
    return cycle.CycleView(
        signals=[cycle.Signal("活动", D(49), Direction.DOWN, "PMI 49")],
        scores={phase: D("0.9"), Phase.MID: D("0.9") - conf,
                Phase.EARLY: D("0.2"), Phase.RECESSION: D("0.1")},
        as_of=AS_OF)


def test_高敏感度行业标注放大():
    """同样的宏观下行，DOL 9.5 的钢铁与 DOL 1.7 的白酒受到的冲击不是一回事。"""
    t = cycle.transmission(_view(), D("3.15"), "钢铁")
    assert "强" in t and "放大" in t


def test_低敏感度行业标注抑制():
    t = cycle.transmission(_view(), D("0.9"), "公用事业")
    assert "弱" in t and "抑制" in t


def test_敏感度缺失时不硬推():
    t = cycle.transmission(_view(), None, "某行业")
    assert "无法量化传导强度" in t


def test_周期无法判定时不做传导():
    empty = cycle.CycleView(signals=[], scores={}, as_of=AS_OF)
    assert "不做传导推断" in cycle.transmission(empty, D("2"), "某行业")


def test_交界时传导结论要打折():
    t = cycle.transmission(_view(conf=D("0.05")), D("3.15"), "钢铁")
    assert "打折" in t


# ── 入账本 ───────────────────────────────────────────────────

def test_只有分数入账阶段名不入账():
    """阶段名是分类不是数值，正文直接写「后周期」即可，不需要占位符。"""
    v = _view()
    facts = cycle.cycle_facts(v, "2026-07")
    assert {f.key for f in facts} == {"macro.cycle_confidence"}
    assert all(f.method is Method.COMPUTED for f in facts)


def test_无信号时不入账():
    empty = cycle.CycleView(signals=[], scores={}, as_of=AS_OF)
    assert cycle.cycle_facts(empty, "2026-07") == []
