"""战略分析：护城河、五力、以及通向估值的那座桥。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import strategy
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.annual_sections import Section, SectionSet
from ir_agent.strategy import Force, MoatKind, StrategyFailed

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2024FY"

QUOTE = "公司拥有品质、品牌、工艺、环境、文化组成的五大核心竞争力"
EVIDENCE = f"【核心竞争力分析】原文第 9 页\n{QUOTE}。行业集中度持续提升。"


def _led(*keys):
    led = FactLedger()
    for k in keys:
        led.put(Fact(key=k, value=D("0.5"), unit="ratio", period=P,
                     as_of=date(2025, 1, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


def _out(moats=None, forces=None, uncerts=None):
    moats = moats if moats is not None else [
        f"护城河: 无形资产 | 宽 | 「{QUOTE}」"]
    forces = forces if forces is not None else [
        f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」" for f in Force]
    uncerts = uncerts if uncerts is not None else [
        f"不确定性: 渠道去库存 | revenue_growth | 下调 | 「{QUOTE}」",
        f"不确定性: 提价能力减弱 | gross_margin | 下调 | 「{QUOTE}」"]
    return "\n".join([*moats, *forces, *uncerts])


class _Client:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


# ── 解析 ─────────────────────────────────────────────────────

def test_解析三部分():
    v = strategy.parse(_out())
    assert v.moats[0].kind is MoatKind.INTANGIBLE and v.moats[0].strength == "宽"
    assert len(v.forces) == 5
    assert {u.driver for u in v.uncertainties} == {"revenue_growth",
                                                   "gross_margin"}


def test_未知的护城河类型被驳回():
    with pytest.raises(StrategyFailed, match="未知的护城河类型"):
        strategy.parse(f"护城河: 品牌溢价 | 宽 | 「{QUOTE}」")


def test_强度取值受限():
    with pytest.raises(StrategyFailed, match="宽/窄/无"):
        strategy.parse(f"护城河: 无形资产 | 很强 | 「{QUOTE}」")
    with pytest.raises(StrategyFailed, match="高/中/低"):
        strategy.parse(f"五力: 现有竞争强度 | 很激烈 | 中性 | 「{QUOTE}」")


def test_字段不足直接驳回():
    with pytest.raises(StrategyFailed, match="字段不足"):
        strategy.parse("护城河: 无形资产 | 宽")


def test_缺依据驳回():
    with pytest.raises(StrategyFailed, match="缺少可核验的依据"):
        strategy.parse("护城河: 无形资产 | 宽 | 这是我的判断")


# ── 桥：不确定性必须映射到可调参数 ───────────────────────────

def test_驱动因子必须在可调参数之列():
    """「宏观需求波动」映射不到任何参数，在情景分析里没有出口。"""
    with pytest.raises(StrategyFailed, match="不在可调参数之列"):
        strategy.parse(f"不确定性: 需求波动 | 宏观环境 | 下调 | 「{QUOTE}」")


def test_可调参数与建模假设对齐():
    assert {"revenue_growth", "gross_margin", "wacc",
            "terminal_growth"} <= set(strategy.MAPPABLE_DRIVERS)


def test_不确定性少于两条驳回():
    """少于两条，Bear/Base/Bull 就没有定性依据，只能靠调参数。"""
    v = strategy.parse(_out(uncerts=[
        f"不确定性: 渠道去库存 | revenue_growth | 下调 | 「{QUOTE}」"]))
    with pytest.raises(StrategyFailed, match="至少两条"):
        strategy.check(v, _led(), EVIDENCE, AS_OF)


# ── 校验 ─────────────────────────────────────────────────────

def test_引文必须逐字出自年报摘录():
    v = strategy.parse(_out(moats=["护城河: 无形资产 | 宽 | 「公司品牌力极强」"]))
    with pytest.raises(StrategyFailed, match="不可核验"):
        strategy.check(v, _led(), EVIDENCE, AS_OF)


def test_五力必须五条都给():
    v = strategy.parse(_out(forces=[
        f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」"
        for f in list(Force)[:3]]))
    with pytest.raises(StrategyFailed, match="五力须五条都给"):
        strategy.check(v, _led(), EVIDENCE, AS_OF)


def test_无护城河也要显式说出():
    """「无明显护城河」是结论，不是省略的理由。"""
    v = strategy.parse(_out(moats=[]))
    with pytest.raises(StrategyFailed, match="也是结论"):
        strategy.check(v, _led(), EVIDENCE, AS_OF)
    ok = strategy.parse(_out(moats=[f"护城河: 无明显护城河 | 无 | 「{QUOTE}」"]))
    strategy.check(ok, _led(), EVIDENCE, AS_OF)


def test_合法输入通过():
    strategy.check(strategy.parse(_out()), _led(), EVIDENCE, AS_OF)


def test_占位符依据也可核验():
    led = _led("industry.hhi")
    v = strategy.parse(_out(forces=[
        "五力: 现有竞争强度 | 高 | 对本司不利 | [[industry.hhi@2024FY]]",
        *[f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」"
          for f in list(Force)[1:]]]))
    strategy.check(v, led, EVIDENCE, AS_OF)


def _sections():
    ss = SectionSet(code="600519", pages=143)
    ss.sections["core_competency"] = Section(
        key="core_competency", label="核心竞争力分析", heading="四、核心竞争力分析",
        page=9, text=QUOTE)
    ss.missing.append("industry_view")
    return ss


# ── 量化锚点 ─────────────────────────────────────────────────

def test_三力有账本锚点():
    got = strategy.anchors(_led("industry.hhi", "payable_days",
                                "receivable_days"), P, AS_OF)
    assert Force.RIVALRY in got and Force.SUPPLIERS in got
    assert Force.BUYERS in got


def test_替代品威胁没有锚点():
    """硬给它编一个代理指标，是把无依据的东西装扮成有依据。"""
    assert Force.SUBSTITUTES not in strategy.FORCE_ANCHORS


def test_无锚点的力被标注为纯定性():
    c = _Client(_out())
    v, _ = strategy.assess(c, _led("industry.hhi"), P, AS_OF, _sections())
    assert Force.SUBSTITUTES in v.unquantified
    assert "纯定性判断" in v.summary()


def test_账本无该事实时不给锚点():
    assert strategy.anchors(FactLedger(), P, AS_OF) == {}


# ── 提示词 ───────────────────────────────────────────────────

def test_提示词含年报摘录与缺失说明():
    txt = strategy.for_prompt(_led("industry.hhi"), P, AS_OF, _sections())
    assert QUOTE in txt
    assert "年报未设此节" in txt and "不得凭印象补足" in txt


def test_提示词点明矛盾要写出来():
    c = _Client(_out())
    strategy.assess(c, _led("industry.hhi"), P, AS_OF, _sections())
    assert "把矛盾写出来" in c.prompts[0]


def test_提示词列出可调参数():
    c = _Client(_out())
    strategy.assess(c, _led(), P, AS_OF, _sections())
    assert "revenue_growth" in c.prompts[0] and "gross_margin" in c.prompts[0]


def test_无年报摘录时引文无处可核验():
    """没有年报就没有可引用的原文 —— 此时应当失败，而不是放行一段
    无从核验的「战略分析」。"""
    with pytest.raises(StrategyFailed):
        strategy.assess(_Client(_out(), _out(), _out()),
                        _led(), P, AS_OF, None)


# ── 端到端 ───────────────────────────────────────────────────

def test_驳回后重试并带回原因():
    bad = _out(uncerts=[f"不确定性: 需求波动 | 宏观环境 | 下调 | 「{QUOTE}」",
                        f"不确定性: 提价 | gross_margin | 下调 | 「{QUOTE}」"])
    c = _Client(bad, _out())
    strategy.assess(c, _led(), P, AS_OF, _sections())
    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]


def test_连续失败则拒绝出结论():
    """没有依据的战略判断在研报里比空白更危险。"""
    bad = _out(moats=["护城河: 无形资产 | 宽 | 「查无此句」"])
    with pytest.raises(StrategyFailed, match="连续"):
        strategy.assess(_Client(bad, bad, bad), _led(), P, AS_OF, _sections())


def test_摘要含三部分():
    v, _ = strategy.assess(_Client(_out()), _led(), P, AS_OF, _sections())
    txt = v.summary()
    assert "护城河" in txt and "现有竞争强度" in txt
    assert "关键不确定性 → 情景驱动因子" in txt
