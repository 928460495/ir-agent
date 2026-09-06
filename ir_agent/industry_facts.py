"""行业聚合 —— 把同业集合变成账本事实。

这是「行业分析」落到数字的地方: 行业规模、行业增速、集中度、公司市占率
及其变化。有了它，增速假设的依据才能从「上期营收同比」这种自我外推，
变成「行业增速 + 份额变化」这样可以被质疑、被证伪的分解。

两个必须防住的聚合错误:

1. **同比要在两期都有数的交集上算。** 某家同业若本期有收入而上年同期缺失，
   计入分子不计入分母，行业增速凭空虚增。覆盖率越低错得越狠，且不会报错。

2. **覆盖率必须随结论一起走。** 12/18 家算出来的「行业规模」是残缺的，
   拿它做分母会**高估**标的市占率。所以市占率与集中度都带覆盖率，
   覆盖不足时下游不得声称这是行业全貌。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, Method

SOURCE_ID = "industry_agg"

# 低于此覆盖率不出行业总量与集中度结论 —— 残缺的分母比没有分母更危险。
MIN_COVERAGE = Decimal("0.7")

# 集中度取前 N 家
CR_N = 5


class InsufficientCoverage(Exception):
    """同业数据覆盖太低，行业口径的结论不成立。"""


@dataclass(frozen=True)
class Missing:
    code: str
    name: str
    reason: str


@dataclass
class IndustryAggregate:
    """某一期的行业横截面。revenues / prior 的键是股票代码。"""

    target: str
    target_name: str
    industry: str                      # 采用的分类路径
    period: str
    prior_period: str
    as_of: date
    revenues: dict[str, Decimal] = field(default_factory=dict)
    prior: dict[str, Decimal] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    missing: list[Missing] = field(default_factory=list)
    thin: bool = False

    # ── 覆盖 ──────────────────────────────────────────────

    @property
    def universe(self) -> int:
        """行业内应有的公司数（含标的）。"""
        return len(self.revenues) + len(self.missing)

    @property
    def coverage(self) -> Decimal:
        if not self.universe:
            return Decimal(0)
        return Decimal(len(self.revenues)) / self.universe

    @property
    def covered(self) -> bool:
        return self.coverage >= MIN_COVERAGE

    # ── 规模与增速 ────────────────────────────────────────

    @property
    def total(self) -> Decimal:
        return sum(self.revenues.values(), Decimal(0))

    @property
    def both(self) -> list[str]:
        """两期都有数的公司 —— 同比只能在这个集合上算。"""
        return sorted(set(self.revenues) & set(self.prior))

    @property
    def yoy(self) -> Decimal | None:
        """行业营收同比。分子分母同集合，否则口径不一致。"""
        codes = self.both
        if not codes:
            return None
        now = sum((self.revenues[c] for c in codes), Decimal(0))
        was = sum((self.prior[c] for c in codes), Decimal(0))
        return None if was == 0 else now / was - 1

    @property
    def yoy_basis(self) -> int:
        return len(self.both)

    # ── 集中度 ────────────────────────────────────────────

    @property
    def cr5(self) -> Decimal | None:
        if not self.covered or self.total == 0:
            return None
        top = sorted(self.revenues.values(), reverse=True)[:CR_N]
        return sum(top, Decimal(0)) / self.total

    # ── 标的的位置 ────────────────────────────────────────

    @property
    def share(self) -> Decimal | None:
        """市占率。覆盖不足时不给 —— 分母残缺会系统性高估。"""
        if not self.covered or self.total == 0:
            return None
        v = self.revenues.get(self.target)
        return None if v is None else v / self.total

    @property
    def prior_share(self) -> Decimal | None:
        codes = self.both
        if not codes or self.target not in codes:
            return None
        was = sum((self.prior[c] for c in codes), Decimal(0))
        return None if was == 0 else self.prior[self.target] / was

    @property
    def share_delta(self) -> Decimal | None:
        """市占率变化（百分点差）。增速假设的核心依据之一。

        用同一交集口径的两期份额相减 —— 与 self.share 的口径不同（后者用
        全部本期数据），因为份额**变化**必须在同一组公司上比才有意义。
        """
        codes = self.both
        if self.target not in codes:
            return None
        now = sum((self.revenues[c] for c in codes), Decimal(0))
        was = sum((self.prior[c] for c in codes), Decimal(0))
        if now == 0 or was == 0:
            return None
        return self.revenues[self.target] / now - self.prior[self.target] / was

    @property
    def target_yoy(self) -> Decimal | None:
        a, b = self.revenues.get(self.target), self.prior.get(self.target)
        if a is None or b is None or b == 0:
            return None
        return a / b - 1

    @property
    def excess_growth(self) -> Decimal | None:
        """超额增速 = 公司增速 − 行业增速。正值即在抢份额。"""
        me, ind = self.target_yoy, self.yoy
        return None if me is None or ind is None else me - ind

    @property
    def rank(self) -> int | None:
        if self.target not in self.revenues:
            return None
        order = sorted(self.revenues, key=lambda c: self.revenues[c], reverse=True)
        return order.index(self.target) + 1

    # ── 展示 ──────────────────────────────────────────────

    def summary(self) -> str:
        pct = lambda v: "—" if v is None else f"{v * 100:.2f}%"   # noqa: E731
        lines = [
            f"行业聚合 {self.industry} · {self.period}",
            f"  覆盖 {len(self.revenues)}/{self.universe} 家"
            f"（{self.coverage * 100:.0f}%）"
            + ("" if self.covered else f"  ⚠ 低于 {MIN_COVERAGE * 100:.0f}%，"
                                       "不出行业总量/集中度/市占率结论"),
            f"  行业营收同比 {pct(self.yoy)}（在两期都有数的 {self.yoy_basis} 家上计算）",
            f"  {self.target_name} 营收同比 {pct(self.target_yoy)}"
            f"  超额 {pct(self.excess_growth)}",
        ]
        if self.covered:
            lines.append(f"  CR{CR_N} {pct(self.cr5)}"
                         f"   本司份额 {pct(self.share)}"
                         f"（行业第 {self.rank} 位）")
            d = self.share_delta
            if d is not None:
                lines.append(f"  份额变化 {d * 100:+.2f} 个百分点")
        if self.thin:
            lines.append("  ⚠ 同业样本偏薄，行业口径仅供参考")
        for m in self.missing[:6]:
            lines.append(f"  – 缺 {m.name}（{m.code}）: {m.reason}")
        if len(self.missing) > 6:
            lines.append(f"  – 另有 {len(self.missing) - 6} 家缺失")
        return "\n".join(lines)


def aggregate(peers, period: str, prior_period: str, as_of: date,
              fetch) -> IndustryAggregate:
    """对同业集合逐家取营收并聚合。

    fetch(code, period, as_of) -> Decimal | None，抛异常视为缺失。
    标的自身也要取 —— 它是行业的一部分，市占率的分子。
    """
    agg = IndustryAggregate(
        target=peers.target, target_name=peers.target_name,
        industry=peers.classification.used_path,
        period=period, prior_period=prior_period, as_of=as_of,
        thin=peers.thin)

    everyone = [(peers.target, peers.target_name)] + [
        (m.code, m.name) for m in peers.members]

    for code, name in everyone:
        agg.names[code] = name
        try:
            now = fetch(code, period, as_of)
        except Exception as e:                       # noqa: BLE001
            agg.missing.append(Missing(code, name,
                                       f"{e.__class__.__name__}: {str(e)[:50]}"))
            continue
        if now is None:
            agg.missing.append(Missing(code, name, f"{period} 无营收数据"))
            continue
        agg.revenues[code] = now
        try:
            was = fetch(code, prior_period, as_of)
        except Exception:                            # noqa: BLE001
            was = None
        if was is not None:
            agg.prior[code] = was

    return agg


def industry_facts(agg: IndustryAggregate) -> list[Fact]:
    """把行业结论写进账本，供正文以 [[引用]] 的形式使用。

    不入账本，分析师就只能把「市占率 43%」写成字面数字 —— 审计会正确地把它
    判为裸数字驳回。占位符契约对行业结论一视同仁。

    覆盖率本身也入账: 「行业第一」建立在多少家数据之上，读者必须能看到。
    """
    out: list[Fact] = []

    def add(key, value, unit, period=None, derived=()):
        if value is None:
            return
        out.append(Fact(key=key, value=value, unit=unit,
                        period=period or agg.period, as_of=agg.as_of,
                        source_id=SOURCE_ID, method=Method.COMPUTED,
                        derived_from=tuple(derived)))

    src = tuple(f"revenue@{agg.period}#{c}" for c in sorted(agg.revenues))
    both = tuple(f"revenue@{p}#{c}" for c in agg.both
                 for p in (agg.period, agg.prior_period))

    add("industry.peer_count", Decimal(len(agg.revenues)), "家")
    add("industry.coverage", agg.coverage, "ratio")
    add("industry.revenue_yoy", agg.yoy, "ratio", derived=both)
    add("company.revenue_yoy_vs_industry", agg.excess_growth, "ratio",
        derived=both)

    if agg.covered:
        add("industry.revenue", agg.total, "元", derived=src)
        add("industry.cr5", agg.cr5, "ratio", derived=src)
        add("company.market_share", agg.share, "ratio", derived=src)
        add("company.market_share_delta", agg.share_delta, "ratio",
            derived=both)
        if agg.rank is not None:
            add("company.industry_rank", Decimal(agg.rank), "名", derived=src)
    return out
