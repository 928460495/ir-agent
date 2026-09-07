"""Bear / Base / Bull —— 战略分析通向估值的那座桥的另一端。

**情景不是把参数上下调两档。** stage2 里已有一张 5×5 的 WACC × 永续增速
网格，它回答的是「假设错了多少，估值差多少」——是**机械敏感性**，与这家
公司的生意没有关系，换成任何一家公司那张网格长得一模一样。

情景要回答的是另一个问题: **战略分析识别出的那几条不确定性一旦兑现，
这家公司值多少钱。** 因此:

  · 驱动因子来自 strategy 的 Uncertainty，不是随便挑两个参数
  · 熊市里几条不利因素**同时兑现**（它们本就相关: 需求转弱既压增速
    也压提价能力），而不是一次只动一个
  · 每个取值都要有依据，且移动方向必须与战略分析写明的「不利时如何变化」
    一致 —— 否则情景与战略脱钩，又变回调参数

**方向一致性是这里最要紧的一条校验。** 战略分析已经说了「渠道去库存 →
revenue_growth → 不利时下调」；若熊市情景反而调高了收入增速，那不是情景
分析，是自相矛盾。代码逐条比对，对不上就驳回。

**当前只有三个驱动因子接得上。** MAPPABLE_DRIVERS 有六个，而 Assumptions
只有 wacc、terminal_growth、growth_rates 三个旋钮。毛利率、费用率、资本
开支率要落到估值上，得先有三张表的预测模型 —— 现在的 DCF 直接吃一个
fcf_base，中间没有可供毛利率作用的结构。**这类不确定性如实报告为暂不可
建模，不做近似**: 硬编一个「毛利率降 1pp 对应 FCF 降多少」的系数，等于
凭空发明一条不存在的关系。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum

from ir_agent.lineparse import keyed
from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.valuation.assumptions import Assumptions
from ir_agent.valuation.basis import Basis, InvalidBasis, validate
from ir_agent.valuation.model import Valuation, value_company


class ScenarioFailed(Exception):
    """情景不可核验或自相矛盾 —— 拒绝出结论。"""


class Case(str, Enum):
    BEAR = "熊市"
    BASE = "基准"
    BULL = "牛市"


# 能落到当前估值模型上的驱动因子 → Assumptions 的字段。
MODELLABLE = {
    "revenue_growth": "growth_rates",
    "terminal_growth": "terminal_growth",
    "wacc": "wacc",
}

# 战略分析里「不利时如何变化」的说法 → 熊市相对基准的方向。
ADVERSE = {"下调": -1, "调低": -1, "降低": -1, "下降": -1,
           "上调": +1, "调高": +1, "提高": +1, "上升": +1}

_SHIFT = keyed("情景")
_QUOTE = re.compile(r"[「\"'“](.+?)[」”\"']", re.DOTALL)
_REF = re.compile(r"\[\[[^\]]+\]\]")
_NUM = re.compile(r"-?\d+(?:\.\d+)?%?")


def _pct(raw: str) -> Decimal:
    m = _NUM.search(raw.replace(",", ""))
    if not m:
        raise ScenarioFailed(f"取值不可解析：{raw!r}")
    txt = m.group(0)
    try:
        v = Decimal(txt.rstrip("%"))
    except InvalidOperation as e:
        raise ScenarioFailed(f"取值不可解析：{raw!r}") from e
    return v / 100 if txt.endswith("%") else v


@dataclass
class Shift:
    """一条不确定性在两端情景下的取值。"""

    what: str                  # 不确定性描述，来自战略分析
    driver: str
    bear: Decimal
    bull: Decimal
    bases: list[Basis] = field(default_factory=list)


@dataclass
class ScenarioSet:
    # 期间必须带上 —— 占位符没有 @period 就不是合法占位符，render() 不会
    # 替换它，会把 [[valuation.per_share.bear]] 原样印进研报。
    # 那比被驳回更糟: 驳回还能重试，这个是静默出错。
    period: str = ""
    shifts: list[Shift] = field(default_factory=list)
    unmodellable: list[tuple[str, str]] = field(default_factory=list)
    values: dict[Case, Valuation] = field(default_factory=dict)

    def spread(self) -> Decimal | None:
        """牛熊区间相对基准的宽度。**区间比点估计更有信息量。**"""
        if len(self.values) < 3:
            return None
        base = self.values[Case.BASE].value_per_share
        if base <= 0:
            return None
        return ((self.values[Case.BULL].value_per_share
                 - self.values[Case.BEAR].value_per_share) / base)

    def summary(self) -> str:
        lines = ["情景分析（驱动因子来自战略分析识别的关键不确定性）"]
        for s in self.shifts:
            lines.append(f"  · {s.what} → {s.driver}")
        for case in Case:
            v = self.values.get(case)
            if v is not None:
                lines.append(f"  {case.value}　每股价值 "
                             f"[[valuation.per_share.{case.name.lower()}"
                             f"@{self.period}]]")
        if self.spread() is not None:
            lines.append("  牛熊区间见上 —— 区间宽度本身是结论的一部分，"
                         "不确定性大的标的不该给出精确到分的目标价")
        for what, driver in self.unmodellable:
            lines.append(f"  ⚠ 「{what}」映射到 {driver}，当前 DCF 无处落脚"
                         f"（需三张表的预测模型），本次未纳入情景")
        return "\n".join(lines)


def _basis(raw: str, what: str) -> list[Basis]:
    raw = raw.strip()
    ref = _REF.search(raw)
    if ref:
        try:
            return [Basis.fact(ref.group(0))]
        except InvalidBasis as e:
            raise ScenarioFailed(f"{what} 的占位符不合法: {e}") from e
    q = _QUOTE.search(raw)
    if not q:
        raise ScenarioFailed(f"{what} 缺少可核验的依据")
    try:
        return [Basis.report(q.group(1))]
    except InvalidBasis as e:
        raise ScenarioFailed(f"{what} 的依据不合法: {e}") from e


def parse(text: str) -> list[Shift]:
    out = []
    for m in _SHIFT.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) < 5:
            raise ScenarioFailed(
                f"情景一行字段不足（需 5 段，得到 {len(parts)}）：{m.group(1)}")
        what, driver = parts[0], parts[1]
        if driver not in MODELLABLE:
            raise ScenarioFailed(
                f"驱动因子 {driver!r} 当前无法落到估值模型上。"
                f"可用：{'、'.join(MODELLABLE)}")
        out.append(Shift(what=what, driver=driver, bear=_pct(parts[2]),
                         bull=_pct(parts[3]), bases=_basis(parts[4], "情景")))
    return out


def check_directions(shifts: list[Shift], uncertainties,
                     base: Assumptions) -> None:
    """熊市取值必须朝战略分析写明的不利方向移动。

    战略分析已经说了「渠道去库存 → revenue_growth → 不利时下调」；
    若熊市反而调高收入增速，那不是情景分析，是自相矛盾。
    """
    by_driver = {u.driver: u for u in uncertainties}
    for s in shifts:
        u = by_driver.get(s.driver)
        if u is None:
            continue
        sign = next((v for k, v in ADVERSE.items() if k in u.direction), None)
        if sign is None:
            continue                      # 说法无法判向，不硬判
        cur = _current(base, s.driver)
        if cur is None:
            continue
        moved = 1 if s.bear > cur else (-1 if s.bear < cur else 0)
        if moved == 0:
            raise ScenarioFailed(
                f"「{s.what}」在熊市下 {s.driver} 与基准相同 —— "
                "不确定性若不改变任何取值，它就不构成情景")
        if moved != sign:
            raise ScenarioFailed(
                f"「{s.what}」的战略判断是不利时{u.direction}，"
                f"但熊市情景把 {s.driver} 朝相反方向调了 —— "
                "情景与战略分析脱钩，等于又变回调参数")
        if (1 if s.bull > cur else -1) == sign:
            raise ScenarioFailed(
                f"「{s.what}」的牛市取值与熊市同向 —— "
                "两端情景必须分居基准两侧")


def _current(base: Assumptions, driver: str) -> Decimal | None:
    if driver == "revenue_growth":
        return base.growth_rates[0] if base.growth_rates else None
    return getattr(base, MODELLABLE[driver], None)


def _apply(base: Assumptions, shifts: list[Shift], case: Case) -> Assumptions:
    """把一个情景下的全部取值**同时**施加到基准假设上。

    同时施加是关键: 需求转弱既压增速也压提价能力，逐个单独动一次得到的
    是敏感性，不是情景。
    """
    if case is Case.BASE:
        return base
    kw: dict = {}
    for s in shifts:
        val = s.bear if case is Case.BEAR else s.bull
        if s.driver == "revenue_growth":
            # 逐年增速整体平移到该情景的首年取值，保持原有的收敛形态 ——
            # 只改首年而让后续年份不动，等于假设冲击一年就消失。
            delta = val - base.growth_rates[0]
            kw["growth_rates"] = [g + delta for g in base.growth_rates]
        else:
            kw[MODELLABLE[s.driver]] = val
    return replace(base, **kw)


def build(base: Assumptions, shifts: list[Shift], uncertainties,
          *, fcf_base: Decimal, net_debt: Decimal, shares: Decimal,
          ledger: FactLedger, body: str, as_of: date,
          period: str = "") -> ScenarioSet:
    if not shifts:
        raise ScenarioFailed(
            "未给出任何情景取值 —— 战略分析识别了关键不确定性却不落到估值上，"
            "那座桥就只搭了一半")

    flat = [b for s in shifts for b in s.bases]
    problems = validate(flat, ledger=ledger, body=body, as_of=as_of)
    if problems:
        raise ScenarioFailed("情景依据不可核验：" + "；".join(problems))

    check_directions(shifts, uncertainties, base)

    out = ScenarioSet(period=period, shifts=shifts)
    out.unmodellable = [(u.what, u.driver) for u in uncertainties
                        if u.driver not in MODELLABLE]
    for case in Case:
        try:
            a = _apply(base, shifts, case)
        except ValueError as e:
            raise ScenarioFailed(
                f"{case.value}情景的假设不自洽：{e} —— "
                "永续增速仍须低于 WACC，情景不能突破这条约束") from e
        out.values[case] = value_company(a, fcf_base=fcf_base,
                                         net_debt=net_debt, shares=shares)

    bear, mid, bull = (out.values[c].value_per_share for c in
                       (Case.BEAR, Case.BASE, Case.BULL))
    if not (bear < mid < bull):
        raise ScenarioFailed(
            f"情景估值不单调（熊 {bear:.2f} / 基准 {mid:.2f} / 牛 {bull:.2f}）"
            " —— 多半是某个驱动因子的方向搞反了，或两端取值跨过了基准")
    return out


def scenario_facts(s: ScenarioSet, period: str, as_of: date,
                   source_id: str = "scenario"):
    """情景估值落账本，好让正文能用占位符引用。"""
    from ir_agent.ledger import Fact, Method

    out = []
    for case, v in s.values.items():
        out.append(Fact(key=f"valuation.per_share.{case.name.lower()}",
                        value=v.value_per_share, unit="currency",
                        period=period, as_of=as_of, source_id=source_id,
                        method=Method.COMPUTED))
    return out


def anchors(ledger: FactLedger, as_of: date) -> list[str]:
    """可用于给情景取值定锚的账本事实。

    **提供锚点，但不代模型定幅度。** 历史增速区间、卖方预测的高低端、
    宏观算出的永续上限，都是可观察的；「这次会不会更差」则是判断，
    判断必须由模型给出并留下依据。
    """
    want = ("revenue.yoy", "consensus.eps_low", "consensus.eps_high",
            "consensus.eps_revision", "industry.revenue_cagr")
    out = []
    for key, per in sorted(ledger._facts):                  # noqa: SLF001
        if key not in want:
            continue
        try:
            ledger.get(key, per, as_of=as_of)
        except (KeyError, LookAheadError):
            continue
        out.append(f"[[{key}@{per}]]")
    return out


PROMPT = """把战略分析识别出的关键不确定性，落成 Bear / Bull 两端的取值。

**关键不确定性（来自战略分析）**
{uncertainties}

**基准假设**
{base}

**可用于定锚的事实**
{anchors}

对每一条能落到估值模型上的不确定性给出一行：
`情景: <不确定性描述> | <驱动因子> | <熊市取值> | <牛市取值> | 「正文原话」或 [[占位符]]`

驱动因子只能用：{drivers}

**硬性要求**
- 熊市取值必须朝战略分析写明的「不利时如何变化」那个方向移动，
  牛市取值在基准的另一侧。方向搞反会被驳回。
- 两端取值都要有依据。**幅度是判断，但判断要留下理由** ——
  「参考历史最差年份」「参考卖方预测区间下沿」都是理由，光给个数字不是。
- 熊市里几条不利因素是**同时**兑现的，取值时按这个前提定，
  不要按「一次只出一个问题」来定。
- 永续增速在任何情景下都必须低于 WACC。
"""
