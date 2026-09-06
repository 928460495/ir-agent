"""从宏观与行业事实推导假设的**建议值与约束** —— 建议，不是决定。

原先 assumptions.yaml 里的 growth_rates 是一串手填的数，依据写「上期营收
同比」。那不是分析，是把最近一期外推五年，而且外推的理由就是被外推的那个数
本身 —— 循环论证。

真实的卖方模型不这么做。首次覆盖报告里的收入预测是**分解**出来的: 量假设
来自公司产能与在建项目，价假设来自行业景气与商品价格判断，两者相乘。这里
做不到那么细（免费数据源没有分产品线的产销量），但能做到把每一端的出处
讲清楚。

**为什么不外推行业增速**: 试过，结果是荒谬的。茅台所在的白酒行业 2025FY
营收同比 -18.26%，把它当作未来五年的行业增速，路径成了
-1.21% → -5.47% → -9.73% → -14.00%，与永续增速平均后末年还会反弹，
出现拐点。**一年的行业收缩是周期位置，不是结构性增长率。**

改用卖方通行的**衰减期**结构:

    起点 = 公司当期营收增速（事实）
    终点 = 永续增长率（判断，受宏观上限约束）
    中间线性过渡

单调、无拐点，两端都说得清出处。行业增速、超额增速、份额变化作为**依据**
一并列出 —— 它们不被外推，但必须摆在决策前，好回答那个真正该问的问题:
行业在收缩，起点该不该下调。

**永续增长率不给建议值，只给上限。** 永续增速刻画的是无限期之后的状态，
任何一期数据都推导不出它: 名义 GDP 当期同比 5.43%，而五年 CAGR 因 2020 年
疫情低基数被顶到 6.26% —— 点值受周期位置影响，CAGR 受基期影响，两个都有偏。
系统能正当做的是取两者较小划一条不得越过的线（保守方向: 上限估高了才危险），
判断本身留给决策方 —— 现在是模型自己（见 autodecide.py）。

无风险利率不在免费数据源里（中债接口 404，东财无该报表），因此 WACC 推不出来。
自主模式下它的理由必须先写进研报正文，再以正文原话作为依据 —— 折现率的论证
要摆在读者看得到的地方。如实标注，不假装能自动推导。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.valuation.basis import Basis

# 兜底上限。宏观事实缺失时退回这个值 —— 与 assumptions.MAX_TERMINAL_GROWTH
# 保持一致，那里是最终的硬校验。
FALLBACK_CEILING = Decimal("0.06")

DEFAULT_YEARS = 5

WACC_NOTE = (
    "无风险利率不在可用的免费数据源内（中债接口 404，东财无该报表），"
    "因此 WACC 无法由账本事实推导，只能靠判断 —— 其理由必须写进研报正文，"
    "再以正文原话作为依据")


@dataclass(frozen=True)
class Proposal:
    """一条假设的建议值/约束及其依据。

    标量用 value，逐年序列用 values。两者皆为 None 表示推不出来 —— 此时
    必须如实报告推不出来，而不是给一个看起来合理的默认值 —— 默认值会被
    照单接受，那等于既无推导也无判断，只剩一串来历不明的数字。
    """

    key: str
    bases: list[Basis] = field(default_factory=list)
    note: str = ""
    value: Decimal | None = None
    values: list[Decimal] | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None or self.values is not None

    def describe(self) -> str:
        if self.values is not None:
            return "、".join(f"{r * 100:.2f}%" for r in self.values) + f"　{self.note}"
        if self.value is not None:
            return f"{self.value * 100:.2f}%　{self.note}"
        return f"无法推导 —— {self.note}"


def _get(ledger: FactLedger, key: str, period: str, as_of: date) -> Decimal | None:
    try:
        return ledger.get(key, period, as_of=as_of).value
    except (KeyError, LookAheadError):
        return None


def _latest(ledger: FactLedger, key: str, as_of: date) -> tuple[Decimal, str] | None:
    """账本里该 key 在时点内可见的最新一期。"""
    from ir_agent.periods import UnknownPeriod
    from ir_agent.periods import sort_key as pk

    hits = []
    for k, per in ledger._facts:                          # noqa: SLF001
        if k != key:
            continue
        try:
            hits.append((pk(per), per, ledger.get(k, per, as_of=as_of).value))
        except (KeyError, LookAheadError, UnknownPeriod):
            continue
    if not hits:
        return None
    _, per, val = max(hits)
    return val, per


# ── 永续增长率: 只给上限 ──────────────────────────────────────

def terminal_ceiling(ledger: FactLedger, as_of: date) -> Proposal:
    """永续增长率的上限 = min(名义 GDP 同比, 名义 GDP 多年 CAGR)。

    取较小者是保守方向 —— 上限估高了才危险。两个参考值都有偏（一个受周期
    位置影响，一个受基期影响），取小的那个至少不会把线划得太松。
    """
    picks: list[tuple[Decimal, Basis, str]] = []
    for key, label in (("macro.gdp_nominal_yoy", "当期同比"),
                       ("macro.gdp_nominal_cagr", "多年 CAGR")):
        hit = _latest(ledger, key, as_of)
        if hit is not None:
            v, per = hit
            picks.append((v, Basis.fact(f"[[{key}@{per}]]"),
                          f"{label}（{per}）{v * 100:.2f}%"))
    if not picks:
        return Proposal("terminal_ceiling", [],
                        f"账本中无名义 GDP 事实，退回兜底上限 "
                        f"{FALLBACK_CEILING * 100:.0f}%", value=FALLBACK_CEILING)

    ceiling = min(p[0] for p in picks)
    return Proposal(
        "terminal_ceiling", [p[1] for p in picks],
        "名义 GDP 的" + "、".join(p[2] for p in picks)
        + " —— 取较小者为上限（保守方向）。"
          "永续增速是对无限期之后的判断，任何单期数据都推不出它，故只给上限不给建议值",
        value=min(ceiling, FALLBACK_CEILING))


# ── 预测期增速: 衰减期 ────────────────────────────────────────

def growth_rates(ledger: FactLedger, period: str, as_of: date,
                 years: int = DEFAULT_YEARS,
                 terminal: Decimal | None = None) -> Proposal:
    """预测期收入增速 = 从公司当期增速线性衰减到永续增速。

    行业增速与份额变化进入依据但**不参与外推** —— 见模块文档。
    """
    start = _get(ledger, "revenue.yoy", period, as_of)
    if start is None:
        return Proposal("growth_rates", [],
                        f"账本中无 revenue.yoy@{period} —— 无法确定衰减起点")

    bases = [Basis.fact(f"[[revenue.yoy@{period}]]")]
    context: list[str] = []
    for key, label in (("industry.revenue_yoy", "行业增速"),
                       ("company.revenue_yoy_vs_industry", "超额增速"),
                       ("company.market_share_delta", "份额变化")):
        v = _get(ledger, key, period, as_of)
        if v is not None:
            bases.append(Basis.fact(f"[[{key}@{period}]]"))
            context.append(f"{label} {v * 100:+.2f}%")

    end = terminal if terminal is not None else start
    rates = [start + (end - start) * Decimal(i) / (years - 1) if years > 1
             else start for i in range(years)]

    note = ("自公司当期增速线性衰减至永续增速" if terminal is not None else
            "永续增速尚未确定，此处以起点平推 —— 定下之后路径会线性收敛至该值")
    if context:
        note += "；行业口径供判断起点：" + "、".join(context)
    if not context:
        note += "（行业事实缺失，起点无行业参照）"
    return Proposal("growth_rates", bases, note, values=rates)


# ── 汇总 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Derived:
    terminal_ceiling: Proposal
    growth: Proposal
    wacc_note: str = WACC_NOTE

    def describe(self) -> str:
        lines = ["假设推导（建议与硬约束）",
                 f"  永续增长率上限　{self.terminal_ceiling.describe()}"]
        lines += [f"      · {b.describe()}" for b in self.terminal_ceiling.bases]
        lines.append(f"  预测期增速　{self.growth.describe()}")
        lines += [f"      · {b.describe()}" for b in self.growth.bases]
        lines.append(f"  WACC　{self.wacc_note}")
        return "\n".join(lines)


def derive(ledger: FactLedger, period: str, as_of: date,
           years: int = DEFAULT_YEARS,
           terminal: Decimal | None = None) -> Derived:
    """一次算出全部可推导的假设建议与约束。

    terminal 为人工已定的永续增速时，增速路径据其收敛；未定时以起点平推，
    并在说明里点出这一点 —— 不假装衰减到一个还不存在的终点。
    """
    ceiling = terminal_ceiling(ledger, as_of)
    return Derived(terminal_ceiling=ceiling,
                   growth=growth_rates(ledger, period, as_of, years=years,
                                       terminal=terminal))
