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

# 收入弹性的分界。**这是标准经济学口径，不是自定阈值**:
# 需求收入弹性 > 1 为周期品（cyclic good），0–1 为非周期品（noncyclic），
# < 0 为劣等品（收入上升时需求下降）。
ELASTICITY_CYCLIC = Decimal(1)

# 行业增速相对名义 GDP 增速的容差 —— 超出即视为「显著快于/慢于经济」。
# 用相对比较而非绝对阈值: 绝对阈值（如「15% 算高增长」）没有依据，
# 而「是否快于名义 GDP」既是 CFA 生命周期里 growth 与 mature 的实质区别
# （前者在扩大占经济的份额），也正好等价于收入弹性大于/小于 1。
GDP_TOLERANCE = Decimal("0.02")


class Lifecycle(str, Enum):
    """CFA 的五阶段模型。判据是**定性多维**的，课程与 CFA 均未给数值阈值:

        embryonic  缓慢增长、高价格、低销量、投资需求大、失败风险高
        growth     需求快速increase、盈利改善、价格下降、竞争相对低
        shakeout   增长放缓、竞争激烈、盈利下滑、聚焦成本
        mature     几无增长、**行业整合**、进入壁垒高
        decline    负增长、产能过剩、竞争激烈

    本模块用三个可测信号去对应上述特征: 增速相对名义 GDP、行业利润率
    趋势、集中度（对应 mature 的「industry consolidation」）。
    **embryonic 不做判定** —— 萌芽期行业鲜有足够多的上市公司可供聚合，
    从上市公司口径判不出来，判了也不可信。
    """

    GROWTH = "成长期"
    SHAKEOUT = "洗牌期"
    MATURE = "成熟期"
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
    margin_trend: str = ""

    def describe(self) -> str:
        return f"{self.stage.value} —— {self.reason}"


def margin_trend(margin_by_period: dict[str, Decimal] | None
                 ) -> tuple[str, bool]:
    """行业整体利润率的走向，返回 (描述, 是否可靠)。

    **首末两点比较不足以判断趋势。** 序列非单调时，结论完全取决于起点落在
    周期的哪一段 —— 强周期行业尤其如此。实测封测行业净利率
    6.73→11.33→7.92→3.75→4.47→4.92: 首末比较判「下滑」，而最近三年是
    连续回升，2021 的行业高点被当成了起点。

    与 ratios.Trend.direction 同一条判准: 单调序列的方向可靠，
    非单调序列只能说「首末孰高孰低」，不能当趋势用。
    """
    if not margin_by_period or len(margin_by_period) < 2:
        return "", False
    vals = [margin_by_period[p] for p in sorted(margin_by_period)]
    diffs = [b - a for a, b in zip(vals, vals[1:])]
    if all(d > 0 for d in diffs):
        return "持续改善", True
    if all(d < 0 for d in diffs):
        return "持续下滑", True

    # 非单调: 报出最近一段的方向，并声明首末比较不可靠
    run = 1
    while run < len(diffs) and (diffs[-run] > 0) == (diffs[-1] > 0):
        run += 1
    recent = "回升" if diffs[-1] > 0 else ("回落" if diffs[-1] < 0 else "持平")
    ends = "低于" if vals[-1] < vals[0] else ("高于" if vals[-1] > vals[0]
                                              else "持平于")
    return (f"波动（近 {run} 期{recent}，期末{ends}期初）", False)


def lifecycle(revenue_by_period: dict[str, Decimal],
              hhi: Decimal | None,
              gdp_cagr: Decimal | None = None,
              margin_by_period: dict[str, Decimal] | None = None) -> LifecycleView:
    """按 CFA 五阶段的**定性特征**判定，不用自定的数值阈值。

    三个可测信号 → CFA 的判据:

      增速 vs 名义 GDP    growth「需求快速增长」/ mature「几无增长」
                          / decline「负增长」
      利润率趋势          growth「盈利改善」/ shakeout「盈利下滑」
      集中度 HHI          mature「行业整合」

    没有名义 GDP 参照时退化为只看增速符号，并在理由中说明 ——
    「快不快」必须有参照系，没有参照系就不该给出「成长/成熟」的判断。
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

    # 利润率趋势 —— CFA 用它区分 growth（改善）与 shakeout（下滑）。
    # **只有可靠（单调）的方向才允许改变阶段判定**: 拿一个取决于起点选择的
    # 信号去推翻增速给出的判定，是让噪声压过证据。
    trend, reliable = margin_trend(margin_by_period)
    improving = reliable and trend == "持续改善"
    deteriorating = reliable and trend == "持续下滑"

    consolidated = hhi is not None and hhi >= HHI_MODERATE

    if gdp_cagr is None:
        # 无参照系时只能说符号，不能说「成长」或「成熟」
        stage = Lifecycle.DECLINE if cagr < 0 else Lifecycle.UNKNOWN
        tail = ("总量收缩" if cagr < 0 else
                "缺名义 GDP 参照，无法判断增速是快于还是慢于经济")
    elif cagr > gdp_cagr + GDP_TOLERANCE:
        # **增速快于经济时不判洗牌期。** CFA 的 shakeout 首要特征是
        # 「增长放缓」；营收增速两倍于名义 GDP 却说它增长放缓，是用数据
        # 明确否定的判据去支撑分类。盈利下滑是值得写出来的张力，
        # 但它不改变阶段 —— 阶段由增长轨迹定义。
        stage = Lifecycle.GROWTH
        tail = "增速显著快于名义 GDP，行业在扩大其占经济的份额"
        if deteriorating:
            tail += ("；但盈利能力持续下滑，与成长期通常「盈利改善」的特征"
                     "背离 —— 多为扩产期产能集中释放所致，"
                     "若增速随后放缓则可能转入洗牌期")
    elif cagr < 0:
        stage = Lifecycle.DECLINE
        tail = "总量负增长"
        if not consolidated:
            stage = Lifecycle.SHAKEOUT
            tail += "但格局尚未整合 —— 更接近洗牌期而非衰退期"
    elif cagr < gdp_cagr - GDP_TOLERANCE:
        # 这一支的增速确实慢于经济，「增长放缓 + 盈利下滑」两条都成立，
        # 判洗牌期没有矛盾。
        stage = Lifecycle.SHAKEOUT if deteriorating else Lifecycle.MATURE
        tail = "增速显著慢于名义 GDP" + (
            "且盈利能力下滑" if deteriorating else
            ("且格局已整合" if consolidated else "，格局尚未整合"))
    else:
        stage = Lifecycle.MATURE
        tail = "增速与名义 GDP 相当" + ("且格局已整合" if consolidated else "")

    return LifecycleView(
        stage, cagr, vol, hhi,
        reason=f"{span} 年营收 CAGR {cagr * 100:.1f}%，{tail}"
               + (f"；利润率{trend}" if trend else ""),
        basis=f"多年营收 CAGR 口径下{tail}" + (f"；利润率{trend}" if trend else ""),
        margin_trend=trend)


# ── 周期敏感度 ────────────────────────────────────────────────

@dataclass(frozen=True)
class CyclicalSensitivity:
    """课程（Bodie Ch.17）给的三个决定因素:

        Sales（necessities / discretionary）、Operating leverage、
        Financial leverage —— 「↑ leverage, ↑ sensitivity」

    **不做三者的合成分数。** Bodie 只是并列这三个因素，没有给合成公式；
    检索也未找到把「弹性 × 经营杠杆 × 财务杠杆」合成单一指标的标准做法。
    早前版本用几何平均自行合成并配了 2.0 / 1.3 的阈值 —— 那是编的，
    会让一个无依据的数字看起来像标准度量。现改为三项分别呈现，
    每一项都用它自己有依据的口径与判据:

      · **收入弹性** —— 需求收入弹性是「必需品 / 可选消费」的标准经济学
        度量。分界为 **1**: >1 为周期品（cyclic good），0–1 为非周期品，
        <0 为劣等品。这个阈值不是自定的。
      · **DOL** —— 课程给的公式（见 dol 的文档）。
      · **DFL / DTL** —— DFL = EBIT/(EBIT − 利息) 是标准定义；
        **DTL = DOL × DFL** 是合并经营与财务杠杆的通行做法，衡量税前
        利润对销量变动的总敏感度。课程未涉及 DFL/DTL，但它是既有的标准
        构造，比自编的三因子合成有依据得多。弹性仍单独呈现 ——
        它与两个杠杆量纲不同，没有理由揉进同一个乘积。
    """

    operating_leverage: Decimal | None      # DOL 中位
    financial_leverage: Decimal | None      # DFL 中位
    gdp_elasticity: Decimal | None          # 收入弹性（销售性质的度量）
    n: int
    equity_multiplier: Decimal | None = None   # 权益乘数中位，辅助参考

    @property
    def total_leverage(self) -> Decimal | None:
        """DTL = DOL × DFL。标准构造，非自定合成。"""
        if self.operating_leverage is None or self.financial_leverage is None:
            return None
        return self.operating_leverage * self.financial_leverage

    @property
    def demand_type(self) -> str | None:
        """按收入弹性的标准分界给出需求性质。"""
        e = self.gdp_elasticity
        if e is None:
            return None
        if e < 0:
            return "劣等品（收入上升时需求下降）"
        if e > ELASTICITY_CYCLIC:
            return "周期品（收入弹性大于一）"
        return "非周期品（收入弹性介于零与一）"

    @property
    def elasticity_suspect(self) -> bool:
        """负弹性在经济学上是「劣等品」，有明确含义，但对多数行业不成立。
        实测钢铁得到 -1.42 —— 更可能是样本过短或行业自身供给周期所致。
        标注而非当成发现。"""
        return self.gdp_elasticity is not None and self.gdp_elasticity < 0

    def describe(self) -> str:
        def s(x):
            return "—" if x is None else f"{x:.2f}"

        parts = [f"经营杠杆 DOL 中位 {s(self.operating_leverage)}",
                 f"财务杠杆 DFL 中位 {s(self.financial_leverage)}"]
        dtl = self.total_leverage
        if dtl is not None:
            parts.append(f"总杠杆 DTL {dtl:.2f}")
        if self.gdp_elasticity is not None:
            parts.append(f"收入弹性 {self.gdp_elasticity:.2f}"
                         f"（{self.demand_type}）")
        out = "、".join(parts) + f"（{self.n} 家）"
        if self.elasticity_suspect:
            out += ("　⚠ 弹性为负在经济学上意为劣等品，对本行业多半不成立"
                    " —— 更可能是样本过短或行业自身供给周期所致")
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


def dfl(led: FactLedger, period: str, as_of: date) -> Decimal | None:
    """财务杠杆度 DFL = EBIT / (EBIT − 利息费用)。

    标准定义: 税前利润对 EBIT 变动的敏感度。无息负债的公司 DFL = 1，
    即财务结构不放大波动。利息接近 EBIT 时 DFL 发散 —— 那是真实的
    脆弱性，但数值不可用，此时返回 None 而非一个巨大的数。
    """
    from ir_agent.operators.aggregates import ebit as _ebit

    e = _ebit(led, period, as_of)
    if e is None or e <= 0:
        return None
    ie = _get(led, "interest_expense_fe", period, as_of)
    interest = max(ie, Decimal(0)) if ie is not None else Decimal(0)
    ebt = e - interest
    if ebt <= 0 or ebt / e < Decimal("0.05"):
        return None                       # 利息吞掉九成以上 EBIT，DFL 失真
    return e / ebt


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
        sen = self.sensitivity
        if sen is not None:
            bits = []
            if sen.operating_leverage is not None:
                bits.append(f"经营杠杆 DOL [[industry.dol_median@{p}]]")
            if sen.financial_leverage is not None:
                bits.append(f"财务杠杆 DFL [[industry.dfl_median@{p}]]")
            if sen.total_leverage is not None:
                bits.append(f"总杠杆 DTL [[industry.dtl@{p}]]")
            if sen.gdp_elasticity is not None:
                bits.append(f"收入弹性 [[industry.income_elasticity@{p}]]"
                            f"（{sen.demand_type}）")
            if bits:
                lines.append("  周期敏感度（Bodie Ch.17 的三个决定因素，"
                             "分列不合成）　" + "　".join(bits))
                lines.append(
                    "    收入弹性大于一为周期品、介于零与一为非周期品 ——"
                    "这是标准经济学分界；DTL 越高，同样的收入波动对税前"
                    "利润的放大越强。**三者不合成单一分数**，"
                    "教材未给合成公式。")
            if sen.elasticity_suspect:
                lines.append("    ⚠ 收入弹性为负，多半是样本过短所致，"
                             "该分量不可靠，正文中不得据此下强结论")
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
          gdp_cagr: Decimal | None = None,
          margin_history: dict[str, Decimal] | None = None,
          metrics: list[str] | None = None) -> IndustryProfile:
    """由已取回的同业账本组装行业画像。不发起任何网络请求。"""
    from ir_agent.metrics import REGISTRY

    prof = IndustryProfile(industry=industry, period=period, as_of=as_of)
    prof.concentration = concentration(revenues, cr5)
    hhi = prof.concentration.hhi if prof.concentration else None

    if revenue_history:
        prof.lifecycle = lifecycle(revenue_history, hhi, gdp_cagr=gdp_cagr,
                                   margin_by_period=margin_history)

    dols = [d for led in ledgers.values() if (d := dol(led, period, as_of))]
    dfls = [d for led in ledgers.values() if (d := dfl(led, period, as_of))]
    ems = [v for led in ledgers.values()
           if (v := _get(led, "equity_multiplier", period, as_of))]
    prof.sensitivity = CyclicalSensitivity(
        operating_leverage=_median(dols),
        financial_leverage=_median(dfls),
        gdp_elasticity=gdp_elasticity(industry_yoy or {}, gdp_yoy or {}),
        n=len(ledgers), equity_multiplier=_median(ems))

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
        add("industry.dfl_median", prof.sensitivity.financial_leverage, "x")
        add("industry.dtl", prof.sensitivity.total_leverage, "x")
        add("industry.income_elasticity", prof.sensitivity.gdp_elasticity, "x")
        add("industry.leverage_median", prof.sensitivity.equity_multiplier, "x")
    for key, s in prof.spreads.items():
        from ir_agent.metrics import BY_KEY

        unit = BY_KEY[key].unit if key in BY_KEY else "x"
        add(f"industry.{key}_median", s.median, unit)
    return out
