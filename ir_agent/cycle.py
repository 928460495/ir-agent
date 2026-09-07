"""商业周期定位 —— 现在在四阶段的哪一段。

依据 Fidelity《The Business Cycle Approach to Equity Sector Investing》
（FINC6600 W2 指定读物）的四阶段框架:

    早周期  活动反弹、信贷开始增长、利润快速增长、政策仍宽松、库存低
    中周期  增长见顶、信贷强劲、利润增速见顶、政策中性、库存与销售均衡
    后周期  增长放缓、信贷收紧、盈利承压、政策紧缩、库存增加
    衰退    活动下滑、信贷枯竭、利润下降、政策转宽松

**两条实现上的硬约束**，都来自课程原文:

1. > 阶段之间**并非总是按时间顺序线性推进**，历史上出现过经济跳过某个
   > 阶段或回退到前一阶段的情况。

   因此不能用状态机。这里对四个阶段各算一个匹配分，取最高的，
   并**报告次高与差距** —— 差距小意味着处于阶段交界，那本身是重要信息。

2. **水平不足以定位，必须看方向。** 早周期与衰退在期限利差上都表现为
   陡峭化（都对应宽松预期），区分它们的是活动指标在**回升还是下行**。
   只看某一期的读数会把复苏初期误判成衰退。

**覆盖的是三维，不是四维也不是五维。** Fidelity 用于定义阶段的五个维度是
Activity / Credit / **Profits** / Policy / Inventories-Sales。其中:

  · 活动、信贷、政策 —— 可取（PMI、M1−M2 剪刀差、期限利差）
  · **利润** —— 宏观口径取不到。工业企业利润总额在东财 RPT_ECONOMY_*
    下无配置；企业景气指数（BOOM_INDEX）是调查型信心指数，不是利润，
    拿它顶替是另一次口径替换，不做。
  · **库存与销售** —— 同样取不到

**通胀不参与阶段打分。** Fidelity 原文把它列为**补充分析**而非阶段维度:
「may be enhanced by adding complementary analysis on industries and
inflation」。早前的实现把 PPI 当成第四个打分维度，等于用框架外的变量
定义框架内的阶段 —— 已改为与 Fidelity 一致，单独作为补充信息呈现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method
from ir_agent.periods import UnknownPeriod
from ir_agent.periods import sort_key as period_key

SOURCE_ID = "macro_cycle"

PMI_NEUTRAL = Decimal(50)
# 方向判定的最小变化幅度 —— 小于此视为持平，避免把噪声读成转折
FLAT = Decimal("0.2")


class Phase(str, Enum):
    EARLY = "早周期"
    MID = "中周期"
    LATE = "后周期"
    RECESSION = "衰退"


class Direction(str, Enum):
    UP = "回升"
    FLAT = "持平"
    DOWN = "回落"


@dataclass(frozen=True)
class Signal:
    """一个维度的读数: 水平 + 方向。缺一不可。

    detail 与 detail_ref 是同一句话的两版 —— 前者带读数给人看，后者把
    读数换成占位符给模型看。提示词里出现账本外的裸数字，模型照抄就会被
    审计驳回；而这些读数**账本里都有**，没有理由不给可引用的写法。
    """

    dimension: str
    level: Decimal
    direction: Direction
    detail: str
    detail_ref: str = ""

    def describe(self) -> str:
        return f"{self.dimension} {self.detail}，{self.direction.value}"

    def for_prompt(self) -> str:
        return (f"{self.dimension} {self.detail_ref or self.detail}，"
                f"{self.direction.value}")


def _series(ledger: FactLedger, key: str, as_of: date,
            n: int = 6) -> list[tuple[str, Decimal]]:
    """该 key 在时点内可见的最近若干期，早到晚。"""
    hits = []
    for k, per in ledger._facts:                          # noqa: SLF001
        if k != key:
            continue
        try:
            f = ledger.get(k, per, as_of=as_of)
            hits.append((period_key(per), per, f.value))
        except (KeyError, LookAheadError, UnknownPeriod):
            continue
    hits.sort()
    return [(p, v) for _, p, v in hits[-n:]]


def _direction(vals: list[Decimal], flat: Decimal = FLAT) -> Direction:
    """近三期的走向。用首末之差而非逐期比较 —— 月度数据噪声大，
    逐期比较会把锯齿读成反转。"""
    if len(vals) < 2:
        return Direction.FLAT
    recent = vals[-3:] if len(vals) >= 3 else vals
    delta = recent[-1] - recent[0]
    if abs(delta) < flat:
        return Direction.FLAT
    return Direction.UP if delta > 0 else Direction.DOWN


# ── 四个维度的信号提取 ────────────────────────────────────────

def activity(ledger: FactLedger, as_of: date) -> Signal | None:
    """活动 —— 制造业 PMI 的水平与方向。荣枯线 50。"""
    ser = _series(ledger, "macro.pmi_manufacturing", as_of)
    if not ser:
        return None
    per = ser[-1][0]
    vals = [v for _, v in ser]
    lvl = vals[-1]
    side = "扩张区" if lvl >= PMI_NEUTRAL else "收缩区"
    return Signal("活动", lvl, _direction(vals),
                  f"制造业 PMI {lvl:.1f}（{side}）",
                  f"制造业 PMI [[macro.pmi_manufacturing@{per}]]（{side}）")


def credit(ledger: FactLedger, as_of: date) -> Signal | None:
    """信贷 —— M1−M2 剪刀差。正且走阔 = 资金活化，负 = 资金沉淀。"""
    ser = _series(ledger, "macro.m1_m2_gap", as_of)
    if not ser:
        return None
    per = ser[-1][0]
    vals = [v for _, v in ser]
    lvl = vals[-1]
    tag = "资金活化" if lvl > 0 else "资金沉淀"
    return Signal("信贷", lvl, _direction(vals, flat=Decimal("0.002")),
                  f"M1−M2 剪刀差 {lvl * 100:+.2f}pp（{tag}）",
                  f"M1−M2 剪刀差 [[macro.m1_m2_gap@{per}]]（{tag}）")


def policy(ledger: FactLedger, as_of: date) -> Signal | None:
    """政策 —— 期限利差。

    陡峭化对应宽松预期，平坦或倒挂对应紧缩。**但陡峭化同时出现在早周期
    与衰退**（都是宽松），所以它单独不能定阶段，必须与活动方向合看 ——
    这正是本模块坚持「水平 + 方向」的原因。

    **口径替代需声明**: 课程点名的领先指标是「10 年期国债 − 联邦基金
    利率」（长端减**政策利率**）。A 股没有对应的政策利率日频序列，
    这里用 **10Y − 2Y** 代替 —— 两年期国债是政策预期最直接的载体，
    也是衰退预测文献里的通行口径。但它不是课程原文的那个利差，
    据此下结论时应当知道这个替换。

    课程同时点名**股票市场**为领先指标。本模块不采用: 它不属于
    Fidelity 用于定义阶段的五个维度，且在个股研报里用市场方向去判断
    周期、再用周期去支撑该股票的估值，论证上是自我循环。
    """
    ten = dict(_series(ledger, "rate.cgb_10y", as_of, n=90))
    two = dict(_series(ledger, "rate.cgb_2y", as_of, n=90))
    common = sorted(set(ten) & set(two), key=period_key)
    if len(common) < 2:
        return None
    spread = [ten[p] - two[p] for p in common]
    lvl = spread[-1]
    shape = "倒挂" if lvl < 0 else ("平坦" if lvl < Decimal("0.005") else "陡峭")
    last = common[-1]
    return Signal("政策", lvl, _direction(spread[-20:], flat=Decimal("0.0005")),
                  f"期限利差 {lvl * 100:.2f}pp（{shape}）",
                  # 利差本身不入账本（它是两条曲线之差），但两个分量都在，
                  # 给出分量的占位符让模型能自己说清这个「平坦」从何而来
                  f"期限利差呈{shape}（十年期 [[rate.cgb_10y@{last}]] "
                  f"对两年期 [[rate.cgb_2y@{last}]]）")


def inflation(ledger: FactLedger, as_of: date) -> Signal | None:
    """通胀 —— PPI 同比。工业品价格比 CPI 更贴近企业盈利端。"""
    ser = _series(ledger, "macro.ppi_yoy", as_of)
    if not ser:
        return None
    per = ser[-1][0]
    vals = [v for _, v in ser]
    lvl = vals[-1]
    return Signal("通胀", lvl, _direction(vals, flat=Decimal("0.003")),
                  f"PPI 同比 {lvl * 100:+.2f}%",
                  f"PPI 同比 [[macro.ppi_yoy@{per}]]")


# 参与阶段打分的维度 —— 只包含 Fidelity 用于定义阶段的那些且数据可得的
DIMENSIONS = (activity, credit, policy)

# 补充分析（Fidelity 原文的定位），呈现但**不参与打分**
COMPLEMENTARY = (inflation,)

# 框架内但取不到的维度 —— 显式记录而非默默略去
MISSING_DIMENSIONS = (
    "利润（Fidelity 的 Profits 维度。工业企业利润总额在东财无对应报表；"
    "企业景气指数是信心调查不是利润，不作替代）",
    "库存与销售（需工业企业产成品存货，东财无对应报表）",
)


# ── 阶段画像与打分 ────────────────────────────────────────────
# 每个阶段对每个维度期望的 (水平方向, 变化方向)。None 表示该阶段对该维度
# 没有明确期望，不参与打分 —— 强行给每格都填一个期望会制造虚假的精确。

_PROFILE: dict[Phase, dict[str, tuple[str | None, Direction | None]]] = {
# 逐格对照 Fidelity Exhibit 2 的原文措辞:
#   EARLY      Activity rebounds / Credit begins to grow / Policy still stimulative
#   MID        Growth peaking / Credit growth strong / Policy neutral
#   LATE       Growth moderating / Credit tightens / Policy contractionary
#   RECESSION  Falling activity / Credit dries up / Policy eases
    Phase.EARLY: {
        # 「rebounds」—— 定义特征是**反弹本身**，不是水平低。PMI 刚上穿 50
        # 时水平已经算「高」，但仍处早周期。对水平不设期望，只看方向。
        "活动": (None, Direction.UP),
        "信贷": (None, Direction.UP),        # begins to grow
        "政策": ("高", None),                # still stimulative → 利差陡峭
    },
    Phase.MID: {
        "活动": ("高", Direction.FLAT),      # growth peaking：仍高但不再加速
        "信贷": ("高", None),                # credit growth strong
        "政策": (None, Direction.DOWN),      # neutral：由宽松转中性，利差平坦化
    },
    Phase.LATE: {
        "活动": ("高", Direction.DOWN),      # moderating：仍在扩张区但转头向下
        "信贷": (None, Direction.DOWN),      # tightens
        "政策": ("低", None),                # contractionary → 平坦或倒挂
    },
    Phase.RECESSION: {
        "活动": ("低", Direction.DOWN),      # falling activity
        "信贷": ("低", None),                # dries up
        "政策": (None, Direction.UP),        # eases → 重新陡峭化
    },
}

_THRESHOLD = {
    "活动": PMI_NEUTRAL,
    "信贷": Decimal(0),
    "政策": Decimal("0.005"),
}


def _score(sig: Signal, want: tuple[str | None, Direction | None]) -> Decimal:
    """单维匹配分。水平与方向各占一半 —— 只看其一都会误判。"""
    want_level, want_dir = want
    got = Decimal(0)
    total = Decimal(0)
    if want_level is not None:
        total += 1
        high = sig.level >= _THRESHOLD[sig.dimension]
        if (want_level == "高") == high:
            got += 1
    if want_dir is not None:
        total += 1
        if sig.direction is want_dir:
            got += 1
        elif Direction.FLAT in (sig.direction, want_dir):
            got += Decimal("0.5")           # 持平与任何方向半匹配
    return got / total if total else Decimal(0)


@dataclass
class CycleView:
    signals: list[Signal]                 # 参与打分的维度
    scores: dict[Phase, Decimal]
    as_of: date
    complementary: list[Signal] = field(default_factory=list)   # 不参与打分
    missing: list[str] = field(default_factory=lambda: list(MISSING_DIMENSIONS))

    @property
    def coverage(self) -> str:
        """给人看: 带比例。"""
        n = len(self.signals)
        return f"{n}/{n + len(self.missing)} 个阶段维度"

    @property
    def coverage_note(self) -> str:
        """给模型看: 定性表述。

        覆盖率是个说明而不是待引用的值，账本里没有对应事实 ——
        写成「1/3」模型照抄就是裸数字。
        """
        return ("部分阶段维度" if self.missing else "全部阶段维度")

    @property
    def ranked(self) -> list[tuple[Phase, Decimal]]:
        return sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)

    @property
    def phase(self) -> Phase | None:
        return self.ranked[0][0] if self.scores else None

    @property
    def confidence(self) -> Decimal:
        """首选与次选的差距。差距小 = 处于阶段交界，这本身是重要信息。"""
        r = self.ranked
        return r[0][1] - r[1][1] if len(r) >= 2 else Decimal(0)

    @property
    def at_boundary(self) -> bool:
        return self.confidence < Decimal("0.15")

    def describe(self) -> str:
        if not self.scores:
            return "周期定位：无可用宏观信号"
        r = self.ranked
        lines = [f"周期定位：**{r[0][0].value}**"
                 f"（匹配 {r[0][1]:.2f}，次选 {r[1][0].value} {r[1][1]:.2f}）"]
        if self.at_boundary:
            lines.append(f"  ⚠ 与次选差距仅 {self.confidence:.2f} —— "
                         f"处于 {r[0][0].value}／{r[1][0].value} 交界，"
                         "不宜据此下强结论")
        lines.append(f"  依据 {self.coverage}（Fidelity 框架共五维）")
        for s in self.signals:
            lines.append(f"  · {s.describe()}")
        for s in self.complementary:
            lines.append(f"  ◦ {s.describe()}　（补充分析，不参与阶段判定）")
        for m in self.missing:
            lines.append(f"  – 未覆盖：{m}")
        return "\n".join(lines)

    def for_prompt(self) -> str:
        if not self.scores:
            return "  （无可用宏观信号，本节不下周期判断）"
        r = self.ranked
        lines = [f"  当前判定：{r[0][0].value}（次选 {r[1][0].value}）"
                 f"　依据{self.coverage_note}"]
        for s in self.signals:
            lines.append(f"    {s.for_prompt()}")
        for s in self.complementary:
            lines.append(f"    {s.for_prompt()}　（补充分析，未参与阶段判定）")
        if self.at_boundary:
            lines.append("    ⚠ 首选与次选分差很小，处于阶段交界 —— "
                         "正文中必须说明这一点，不得当作确定的阶段判断")
        for m in self.missing:
            lines.append(f"    – 该判定未覆盖：{m}")
        lines.append("    维度不全时阶段判定的把握相应下降，正文中不得"
                     "把它当作确定结论")
        lines.append("    Fidelity 框架下各阶段的板块含义需结合本行业的"
                     "周期敏感度一并判断，敏感度越高传导越强")
        return "\n".join(lines)


def assess(ledger: FactLedger, as_of: date) -> CycleView:
    """对四个阶段各打一个匹配分，取最高。**不用状态机** ——
    课程明确说阶段可能被跳过或回退。"""
    signals = [s for fn in DIMENSIONS if (s := fn(ledger, as_of)) is not None]
    extra = [s for fn in COMPLEMENTARY if (s := fn(ledger, as_of)) is not None]
    if not signals:
        return CycleView(signals=[], scores={}, as_of=as_of, complementary=extra)

    scores: dict[Phase, Decimal] = {}
    for phase, profile in _PROFILE.items():
        parts = [_score(s, profile[s.dimension]) for s in signals
                 if s.dimension in profile]
        scores[phase] = sum(parts) / len(parts) if parts else Decimal(0)
    # 打分维度不足三个时，阶段判定实质上是在猜 —— 记进缺失说明
    missing = list(MISSING_DIMENSIONS)
    if len(signals) < len(DIMENSIONS):
        got = {s.dimension for s in signals}
        missing += [f"{d}（数据缺失）" for d in ("活动", "信贷", "政策")
                    if d not in got]
    return CycleView(signals=signals, scores=scores, as_of=as_of,
                     complementary=extra, missing=missing)


def cycle_facts(view: CycleView, period: str) -> list[Fact]:
    """把匹配分入账本。阶段名本身是分类不是数值，不入账 ——
    正文里直接写「后周期」即可，不需要占位符。"""
    if not view.scores:
        return []
    return [Fact(key="macro.cycle_confidence", value=view.confidence,
                 unit="ratio", period=period, as_of=view.as_of,
                 source_id=SOURCE_ID, method=Method.COMPUTED,
                 derived_from=tuple(s.dimension for s in view.signals))]


# ── 传导到行业 ────────────────────────────────────────────────

def transmission(view: CycleView, cyclical_score: Decimal | None,
                 industry: str) -> str:
    """周期阶段 × 行业敏感度 —— 这条链真正闭合的地方。

    课程的要求是: **说清楚行业结构将如何抑制或放大经济中的预期发展。**
    知道处于后周期还不够，还要知道这个行业对周期有多敏感 ——
    同样的宏观下行，DOL 9.5 的钢铁与 DOL 1.7 的白酒受到的冲击不是一回事。
    """
    if view.phase is None:
        return "宏观周期无法判定，不做传导推断"
    if cyclical_score is None:
        return (f"当前处于{view.phase.value}，但本行业的周期敏感度分量不全，"
                "无法量化传导强度")

    if cyclical_score >= Decimal(2):
        strength, hint = "强", "宏观变化会被行业结构**放大**"
    elif cyclical_score >= Decimal("1.3"):
        strength, hint = "中", "宏观变化大体等比例传导"
    else:
        strength, hint = "弱", "宏观变化会被行业结构**抑制**"

    tail = {
        Phase.EARLY: "该阶段历史上利好利率敏感与经济敏感板块",
        Phase.MID: "该阶段板块分化收敛，超额收益更依赖公司层面",
        Phase.LATE: "该阶段防御性与抗通胀板块相对占优",
        Phase.RECESSION: "该阶段必需消费与公用事业等防御板块相对抗跌",
    }[view.phase]
    note = ("　注意首选与次选分差很小，上述判断的强度应相应打折"
            if view.at_boundary else "")
    return (f"当前处于{view.phase.value}，{industry} 的周期敏感度为"
            f"{strength}（{hint}）。{tail}。{note}")
