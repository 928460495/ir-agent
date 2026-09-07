"""共同比报表 —— 资产负债表 ÷ 总资产，利润表 ÷ 营业总收入。

课程点出的价值不在换算本身，而在它能看见什么:

> 跨期分析单个项目的变化可以揭示**公司正在推行的战略**
> （如应收占总资产比重上升、营业费用中营销或研发的显著变化），
> 也可以提示**关注领域**（流动性、偿债能力、盈利能力）。

**现金流量表没有共同比** —— 课程明确说明，因为它本身就是流量的分解，
再除以一个总量没有意义。这里也不做。

科目清单不硬编码，直接取自 eastmoney 的字段映射 —— 那里已经按报表模板
分设，是唯一的真相来源。硬编码一份等于把同一件事写两遍，改一处漏一处。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, LookAheadError
from ir_agent.sources.eastmoney import CompanyType, specs_for

# 分母本身与非金额项不参与换算
_EXCLUDE = {
    "total_assets", "revenue_total", "basic_eps", "diluted_eps",
}

# 变动超过这个幅度（百分点）视为「值得解释」——
# 课程要求回到年报找管理层的说明，这个阈值决定要找哪几条。
NOTABLE_SHIFT = Decimal("0.02")


def _items(ct: CompanyType, stmt: str) -> list[str]:
    return sorted(set(specs_for(ct)[stmt].fields.values()) - _EXCLUDE)


@dataclass(frozen=True)
class Line:
    key: str
    share: Decimal
    value: Decimal


@dataclass
class CommonSize:
    period: str
    statement: str          # "balance" | "income"
    denominator: str
    lines: list[Line] = field(default_factory=list)

    def get(self, key: str) -> Decimal | None:
        return next((l.share for l in self.lines if l.key == key), None)

    def top(self, n: int = 8) -> list[Line]:
        return sorted(self.lines, key=lambda l: abs(l.share), reverse=True)[:n]

    def summary(self, n: int = 8) -> str:
        head = {"balance": "资产负债表（÷ 总资产）",
                "income": "利润表（÷ 营业总收入）"}[self.statement]
        lines = [f"共同比 · {head} · {self.period}"]
        for l in self.top(n):
            lines.append(f"  {l.key:<28} {l.share * 100:>7.2f}%")
        return "\n".join(lines)


def compute(ledger: FactLedger, period: str, as_of: date, statement: str,
            company_type: CompanyType = CompanyType.GENERAL) -> CommonSize | None:
    """单期共同比。分母缺失或为零时返回 None —— 不返回一堆无意义的比例。"""
    den_key = {"balance": "total_assets", "income": "revenue_total"}[statement]
    try:
        den = ledger.get(den_key, period, as_of=as_of)
    except (KeyError, LookAheadError):
        return None
    if den.value == 0:
        return None

    lines = []
    for key in _items(company_type, statement):
        try:
            f = ledger.get(key, period, as_of=as_of)
        except (KeyError, LookAheadError):
            continue
        lines.append(Line(key=key, share=f.value / den.value, value=f.value))
    return CommonSize(period=period, statement=statement,
                      denominator=den_key, lines=lines) if lines else None


@dataclass(frozen=True)
class Shift:
    """某科目占比的跨期变化 —— 这才是共同比真正要看的东西。"""

    key: str
    first_period: str
    last_period: str
    first: Decimal
    last: Decimal

    @property
    def delta(self) -> Decimal:
        return self.last - self.first

    def describe(self) -> str:
        d = self.delta * 100
        return (f"{self.key} 占比 {self.first * 100:.2f}% → "
                f"{self.last * 100:.2f}%（{d:+.2f} 个百分点）")


def shifts(series: list[CommonSize],
           threshold: Decimal = NOTABLE_SHIFT) -> list[Shift]:
    """跨期占比变化超过阈值的科目，按幅度排序。

    **单期共同比几乎没有信息量** —— 「应收占总资产 8%」说明不了什么，
    「三年内从 4% 升到 8%」才是线索。课程举的例子（应收占比上升、
    研发费用占比变化）全都是**变化**，不是水平。
    """
    if len(series) < 2:
        return []
    first, last = series[0], series[-1]
    out = []
    for l in last.lines:
        a = first.get(l.key)
        if a is None:
            continue
        s = Shift(key=l.key, first_period=first.period,
                  last_period=last.period, first=a, last=l.share)
        if abs(s.delta) >= threshold:
            out.append(s)
    return sorted(out, key=lambda s: abs(s.delta), reverse=True)


def compute_series(ledger: FactLedger, periods: list[str], as_of: date,
                   statement: str,
                   company_type: CompanyType = CompanyType.GENERAL
                   ) -> tuple[list[CommonSize], list[Shift]]:
    series = [cs for p in periods
              if (cs := compute(ledger, p, as_of, statement, company_type))]
    return series, shifts(series)
