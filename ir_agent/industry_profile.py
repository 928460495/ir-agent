"""行业画像 —— 集中度、生命周期、周期敏感度、比率分布。

`industry_facts.py` 回答「行业多大、本司占多少」，本模块回答**这是一个
什么样的行业**。课程把后者放在前者之前:

> 行业的结构性因素导致长期的盈利能力差异。
> **McGahan & Porter (1997): 行业效应对盈利能力的重要性高于公司特有效应。**

**不增加任何网络调用。** 逐家同业的完整报表在行业聚合时已经取过一次，
那里只用了 revenue 一个字段。本模块复用同一批数据。

三块内容:

  · **集中度** —— CR5 已有，这里补 HHI。两者不可互相替代:
    CR5 只看前五家的总和，HHI 对**每一家**的份额平方求和，
    因此能区分「五家各占 16%」与「一家 60% + 四家各 5%」——
    前者 CR5=80% HHI=0.128，后者 CR5=80% HHI=0.37，竞争格局完全不同。

  · **生命周期** —— 影响行业盈利能力如何度量。判据是增速水平 + 增速
    波动 + 集中度，不是单看增速: 高集中度 + 负增长是成熟末期/衰退，
    低集中度 + 负增长可能只是周期底部。
    **课程只提出「生命周期阶段影响盈利能力度量」，没有给判据 ——
    阈值与规则是本项目的启发式。**

  · **周期敏感度** —— 课程给的三个决定因素（销售性质、经营杠杆、
    财务杠杆）**三个都可算**。它是宏观与行业之间真正的接口:
    知道周期在哪一阶段还不够，还要知道这个行业对周期有多敏感。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method

SOURCE_ID = "industry_profile"

# 集中度分档（HHI，美国司法部/FTC 横向合并指南的口径）
HHI_UNCONCENTRATED = Decimal("0.15")
HHI_MODERATE = Decimal("0.25")

# 生命周期的增速门槛。**课程只说「行业盈利能力受生命周期阶段影响」，
# 没有给判据，这两个阈值是本项目的启发式** —— 15% 约为高成长行业的
# 常见量级，3% 约为名义 GDP 增速的下沿。据此下结论时要知道它们是自定的。
GROWTH_HIGH = Decimal("0.15")
GROWTH_LOW = Decimal("0.03")


class Lifecycle(str, Enum):
    GROWTH = "成长期"
    MATURE = "成熟期"
    LATE_MATURE = "成熟末期"
    DECLINE = "衰退期"
    UNKNOWN = "无法判定"


def _get(led: FactLedger, key: str, period: str, as_of: date) -> Decimal | None:
    try:
        return led.get(key, period, as_of=as_of).value
    except (KeyError, LookAheadError):
        return None


def _median(xs: list[Decimal]) -> Decimal | None:
    if not xs:
        return None
    v = sorted(xs)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _quartiles(xs: list[Decimal]) -> tuple[Decimal, Decimal] | None:
    """(Q1, Q3)。离散度比中位数更能说明「行业内部是否分化」。"""
    if len(xs) < 4:
        return None
    v = sorted(xs)
    n = len(v)
    return v[n // 4], v[(3 * n) // 4]


# ── 集中度 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Concentration:
    hhi: Decimal
    cr5: Decimal | None
    n: int

    @property
    def level(self) -> str:
        if self.hhi < HHI_UNCONCENTRATED:
            return "分散"
        if self.hhi < HHI_MODERATE:
            return "中度集中"
        return "高度集中"

    @property
    def effective_players(self) -> Decimal:
        """有效竞争者数量 = 1/HHI。比「有 19 家公司」有信息量得多 ——
        份额高度不均时，实际在竞争的可能只有两三家。"""
        return Decimal(1) / self.hhi if self.hhi > 0 else Decimal(0)

    def describe(self) -> str:
        s = (f"HHI {self.hhi:.4f}（{self.level}），"
             f"有效竞争者约 {self.effective_players:.1f} 家 / 实有 {self.n} 家")
        if self.cr5 is not None:
            s += f"；CR5 {self.cr5 * 100:.2f}%"
        return s


def concentration(revenues: dict[str, Decimal],
                  cr5: Decimal | None = None) -> Concentration | None:
    total = sum(revenues.values(), Decimal(0))
    if total <= 0 or not revenues:
        return None
    hhi = sum(((v / total) ** 2 for v in revenues.values()), Decimal(0))
    return Concentration(hhi=hhi, cr5=cr5, n=len(revenues))


# ── 生命周期 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class LifecycleView:
    stage: Lifecycle
    cagr: Decimal | None
    volatility: Decimal | None
    hhi: Decimal | None
    reason: str          # 带读数，给人看
    basis: str = ""      # 定性表述，给模型看 —— 提示词里不许有账本外的数字

    def describe(self) -> str:
        return f"{self.stage.value} —— {self.reason}"


def lifecycle(revenue_by_period: dict[str, Decimal],
              hhi: Decimal | None) -> LifecycleView:
    """按增速水平 + 增速波动 + 集中度判定阶段。

    **不单看增速**: 高集中度 + 负增长是成熟末期或衰退（格局已定、需求在退），
    低集中度 + 负增长更可能是周期底部（还在洗牌）。两者的估值含义完全不同。
    """
    pers = sorted(revenue_by_period)
    vals = [revenue_by_period[p] for p in pers]
    if len(vals) < 3 or vals[0] <= 0:
        return LifecycleView(Lifecycle.UNKNOWN, None, None, hhi,
                             f"可用年度不足三期（{len(vals)} 期），无法判定")

    span = len(vals) - 1
    cagr = (vals[-1] / vals[0]) ** (Decimal(1) / span) - 1
    yoys = [b / a - 1 for a, b in zip(vals, vals[1:]) if a > 0]
    mean = sum(yoys) / len(yoys) if yoys else Decimal(0)
    vol = ((sum((y - mean) ** 2 for y in yoys) / len(yoys)).sqrt()
           if len(yoys) >= 2 else None)

    concentrated = hhi is not None and hhi >= HHI_MODERATE
    if cagr >= GROWTH_HIGH:
        stage, tail = Lifecycle.GROWTH, "高速扩张"
    elif cagr >= GROWTH_LOW:
        stage, tail = Lifecycle.MATURE, "稳定增长"
    elif cagr >= 0:
        stage = Lifecycle.LATE_MATURE if concentrated else Lifecycle.MATURE
        tail = ("增长停滞且格局已高度集中" if concentrated
                else "增长停滞但格局仍分散，可能处周期低位")
    else:
        stage = Lifecycle.DECLINE if concentrated else Lifecycle.LATE_MATURE
        tail = ("总量收缩且格局已高度集中 —— 需求侧退坡而非份额洗牌"
                if concentrated
                else "总量收缩但格局仍分散，可能是周期底部而非结构性衰退")
    # 两版: 带读数的给人看，定性的给模型看。提示词里出现账本外的数字，
    # 模型照抄就会被审计驳回 —— 这个坑此前已经踩过三次。
    return LifecycleView(
        stage, cagr, vol, hhi,
        reason=f"{span} 年营收 CAGR {cagr * 100:.1f}%，{tail}",
        basis=f"多年营收 CAGR 口径下{tail}")


# ── 周期敏感度 ────────────────────────────────────────────────

@dataclass(frozen=True)
class CyclicalSensitivity:
    """课程（Bodie Ch.17）给的三个决定因素:

        Sales（necessities / discretionary）、Operating leverage、
        Financial leverage —— 「↑ leverage, ↑ sensitivity」

    前两项与课程一一对应。第一项**销售性质**课程只给了定性描述，
    这里用「行业营收增速对名义 GDP 增速的弹性」作**操作化代理** ——
    必需品的收入弹性低、可选消费的高，这是该定性区分背后的经济学含义。
    但这是本项目的操作化，不是课程给定的度量，据此下结论时要说明。
    """

    operating_leverage: Decimal | None    # 行业中位 DOL
    financial_leverage: Decimal | None    # 行业中位权益乘数
    gdp_elasticity: Decimal | None        # 销售性质的代理
    n: int

    @property
    def elasticity_suspect(self) -> bool:
        """负弹性意味着「GDP 上行时行业收入下行」。对绝大多数行业这不是
        发现，而是样本太短或处在自身供给周期上的噪声 —— 必须标注，
        不能悄悄取绝对值当成高敏感度。"""
        return self.gdp_elasticity is not None and self.gdp_elasticity < 0

    @property
    def score(self) -> Decimal | None:
        """三项合成。任一项缺失即不给分数 —— 三缺一的合成分会误导。

        **合成方式是本项目的启发式，课程没有给公式。** 取几何平均是为了
        让任一项接近中性（=1）时整体不被另外两项拉高，且量纲不同的三项
        不会因加法而被隐式加权。阈值 2.0 / 1.3 同样是启发式。
        """
        if None in (self.operating_leverage, self.financial_leverage,
                    self.gdp_elasticity):
            return None
        o = max(self.operating_leverage, Decimal(1))
        f = max(self.financial_leverage, Decimal(1))
        g = abs(self.gdp_elasticity)      # 取绝对值＝看共动强度，符号另行标注
        return (o * f * max(g, Decimal("0.1"))) ** (Decimal(1) / 3)

    def describe(self) -> str:
        def s(x, fmt="{:.2f}"):
            return "—" if x is None else fmt.format(x)

        base = (f"经营杠杆 DOL 中位 {s(self.operating_leverage)}、"
                f"财务杠杆 中位 {s(self.financial_leverage)}、"
                f"营收对名义 GDP 弹性 {s(self.gdp_elasticity)}"
                f"（{self.n} 家）")
        sc = self.score
        if sc is None:
            return base + "　—— 分量不全，不合成敏感度分数"
        level = "高" if sc >= Decimal("2") else ("中" if sc >= Decimal("1.3") else "低")
        out = base + f"　→ 周期敏感度 {sc:.2f}（{level}，启发式合成）"
        if self.elasticity_suspect:
            out += ("　⚠ GDP 弹性为负 —— 多半是样本过短或行业自身供给周期"
                    "所致，该分量的可靠性存疑")
        return out


def dol(led: FactLedger, period: str, as_of: date) -> Decimal | None:
    """经营杠杆度 DOL = 1 + 固定成本 / EBIT。

    **分母必须加回利息费用。** 中国准则把「财务费用」列在「营业总成本」
    之内，因此**营业利润是扣息之后的**。直接拿它当分母，算出的杠杆里
    混进了财务杠杆 —— 而周期敏感度另有一个独立的财务杠杆因子，
    等于把同一件事算两遍。这与 quality.altman_z 中 C 项的处理保持一致。

    **口径选择**: 课程给了两版 ——
        DOL = 1 + FC/OCF                    （假设无利息、无折旧、无税）
        DOL = 1 + [FC(1−t) − Dep×t]/OCF     （含折旧与税，OCF 为税后）
    这里用的是教科书标准形式 `1 + FC/EBIT`，即把折旧计入固定成本、
    分母取税前息前利润。它与第一版在无折旧无税时等价，
    且不需要逐家估算有效税率 —— 后者在同业间差异很大，会引入更多噪声。

    固定成本用「管理费用 + 研发费用 + 折旧摊销」近似 —— 这三项都不随
    销量短期变动。销售费用不计入: 它含大量随收入变动的渠道返利与运费。
    """
    from ir_agent.operators.aggregates import ebit as _ebit

    e = _ebit(led, period, as_of)
    if e is None or e <= 0:
        return None                       # 亏损公司的 DOL 无意义
    fixed = Decimal(0)
    got = False
    for k in ("admin_expense", "rd_expense", "depreciation_amortization"):
        v = _get(led, k, period, as_of)
        if v is not None:
            fixed += abs(v)
            got = True
    return Decimal(1) + fixed / e if got else None


def gdp_elasticity(industry_yoy: dict[str, Decimal],
                   gdp_yoy: dict[str, Decimal]) -> Decimal | None:
    """行业营收增速对名义 GDP 增速的弹性 = 两者增速之比的稳健估计。

    用中位数比值而非回归: 三到五个观测点做回归，斜率的标准误会大到
    没有意义。中位数比值粗糙但不会给人虚假的精确感。
    """
    pairs = [(industry_yoy[p], gdp_yoy[p]) for p in industry_yoy
             if p in gdp_yoy and gdp_yoy[p] not in (None, 0)]
    if len(pairs) < 3:
        return None
    return _median([i / g for i, g in pairs])


# ── 比率分布 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class MetricSpread:
    key: str
    median: Decimal
    q1: Decimal | None
    q3: Decimal | None
    n: int

    @property
    def dispersed(self) -> bool:
        """四分位距超过中位数绝对值本身 —— 行业内部高度分化，
        「行业中位数」这个说法本身就该打折扣。"""
        if self.q1 is None or self.q3 is None or self.median == 0:
            return False
        return abs(self.q3 - self.q1) > abs(self.median)


@dataclass
class IndustryProfile:
    industry: str
    period: str
    as_of: date
    concentration: Concentration | None = None
    lifecycle: LifecycleView | None = None
    sensitivity: CyclicalSensitivity | None = None
    spreads: dict[str, MetricSpread] = field(default_factory=dict)

    def summary(self) -> str:
        from ir_agent.metrics import BY_KEY

        lines = [f"行业画像 {self.industry} · {self.period}"]
        if self.concentration:
            lines.append(f"  集中度　{self.concentration.describe()}")
        if self.lifecycle:
            lines.append(f"  生命周期　{self.lifecycle.describe()}")
        if self.sensitivity:
            lines.append(f"  周期敏感度　{self.sensitivity.describe()}")
        spread = [s for s in self.spreads.values() if s.dispersed]
        if spread:
            names = "、".join(BY_KEY[s.key].label for s in spread
                              if s.key in BY_KEY)
            lines.append(f"  ⚠ 以下指标行业内部高度分化，中位数代表性有限：{names}")
        return "\n".join(lines)

    def for_prompt(self) -> str:
        """给模型看的口径：数字一律写成占位符。

        定性判定（集中度分档、生命周期阶段）不是数值，直接写文字 ——
        它们本来就没有对应的账本事实，写成占位符反而无法解析。
        """
        from ir_agent.metrics import BY_KEY

        p = self.period
        lines = [f"行业画像 {self.industry}"]
        if self.concentration:
            lines.append(
                f"  集中度　{self.concentration.level}"
                f"　HHI [[industry.hhi@{p}]]"
                f"　有效竞争者 [[industry.effective_players@{p}]]")
        if self.lifecycle and self.lifecycle.stage is not Lifecycle.UNKNOWN:
            line = f"  生命周期　{self.lifecycle.stage.value}"
            if self.lifecycle.cagr is not None:
                line += f"（多年营收 CAGR [[industry.revenue_cagr@{p}]]）"
            lines.append(line)
            if self.lifecycle.basis:
                lines.append(f"    判据：{self.lifecycle.basis}")
        if self.sensitivity and self.sensitivity.score is not None:
            lines.append(
                f"  周期敏感度　[[industry.cyclical_score@{p}]]"
                f"　＝ 经营杠杆 [[industry.dol_median@{p}]]"
                f" × 财务杠杆 [[industry.leverage_median@{p}]]"
                f" × GDP 弹性 [[industry.gdp_elasticity@{p}]]")
            lines.append(
                "    该分数决定宏观周期对本行业的传导强度：分数越高，"
                "同样的宏观变化对本行业盈利的冲击越大")
        dispersed = [s for s in self.spreads.values() if s.dispersed]
        if dispersed:
            names = "、".join(BY_KEY[s.key].label for s in dispersed
                              if s.key in BY_KEY)
            lines.append(
                f"  ⚠ 以下指标行业内部高度分化（四分位距超过中位数本身），"
                f"引用「行业中位数」时必须说明其代表性有限：{names}")
        return "\n".join(lines)


def build(industry: str, period: str, as_of: date,
          revenues: dict[str, Decimal],
          ledgers: dict[str, FactLedger],
          revenue_history: dict[str, Decimal] | None = None,
          cr5: Decimal | None = None,
          industry_yoy: dict[str, Decimal] | None = None,
          gdp_yoy: dict[str, Decimal] | None = None,
          metrics: list[str] | None = None) -> IndustryProfile:
    """由已取回的同业账本组装行业画像。不发起任何网络请求。"""
    from ir_agent.metrics import REGISTRY

    prof = IndustryProfile(industry=industry, period=period, as_of=as_of)
    prof.concentration = concentration(revenues, cr5)
    hhi = prof.concentration.hhi if prof.concentration else None

    if revenue_history:
        prof.lifecycle = lifecycle(revenue_history, hhi)

    dols = [d for led in ledgers.values() if (d := dol(led, period, as_of))]
    levs = [v for led in ledgers.values()
            if (v := _get(led, "equity_multiplier", period, as_of))]
    prof.sensitivity = CyclicalSensitivity(
        operating_leverage=_median(dols),
        financial_leverage=_median(levs),
        gdp_elasticity=gdp_elasticity(industry_yoy or {}, gdp_yoy or {}),
        n=len(ledgers))

    keys = metrics or [m.key for m in REGISTRY]
    for key in keys:
        vals = [v for led in ledgers.values()
                if (v := _get(led, key, period, as_of)) is not None]
        med = _median(vals)
        if med is None:
            continue
        q = _quartiles(vals)
        prof.spreads[key] = MetricSpread(key=key, median=med,
                                         q1=q[0] if q else None,
                                         q3=q[1] if q else None, n=len(vals))
    return prof


def profile_facts(prof: IndustryProfile) -> list[Fact]:
    """把行业画像写进账本，供正文以占位符引用。"""
    out: list[Fact] = []

    def add(key, value, unit):
        if value is None:
            return
        out.append(Fact(key=key, value=value, unit=unit, period=prof.period,
                        as_of=prof.as_of, source_id=SOURCE_ID,
                        method=Method.COMPUTED,
                        derived_from=(f"industry={prof.industry}",)))

    if prof.concentration:
        add("industry.hhi", prof.concentration.hhi, "ratio")
        add("industry.effective_players",
            prof.concentration.effective_players, "家")
    if prof.lifecycle and prof.lifecycle.cagr is not None:
        add("industry.revenue_cagr", prof.lifecycle.cagr, "ratio")
    if prof.sensitivity:
        add("industry.dol_median", prof.sensitivity.operating_leverage, "x")
        add("industry.leverage_median", prof.sensitivity.financial_leverage, "x")
        add("industry.gdp_elasticity", prof.sensitivity.gdp_elasticity, "x")
        add("industry.cyclical_score", prof.sensitivity.score, "x")
    for key, s in prof.spreads.items():
        from ir_agent.metrics import BY_KEY

        unit = BY_KEY[key].unit if key in BY_KEY else "x"
        add(f"industry.{key}_median", s.median, unit)
    return out
