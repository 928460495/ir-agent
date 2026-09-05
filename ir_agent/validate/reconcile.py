"""三表勾稽校验。

设计要点: 缺数据时「跳过」而非「通过」。静默通过会让一份数据残缺的报表
看起来完全健康 —— 这正是审计里最危险的一类假阳性。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ir_agent.ledger import FactLedger, LookAheadError

DEFAULT_ABS_TOL = Decimal("0.01")
DEFAULT_REL_TOL = Decimal("0.0005")   # 5bp，容纳财报四舍五入


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str                      # "passed" | "failed" | "skipped"
    diff: Decimal | None = None
    detail: str = ""


@dataclass
class ReconcileReport:
    period: str
    as_of: date
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status == "failed"]

    @property
    def checks_run(self) -> int:
        return sum(1 for r in self.results if r.status != "skipped")

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def complete(self) -> bool:
        """所有校验都拿到了数据 —— 覆盖率与通过率必须分开看。"""
        return all(r.status != "skipped" for r in self.results)

    def summary(self) -> str:
        head = f"{self.period} @ {self.as_of.isoformat()}"
        state = "通过" if self.ok else f"失败 {len(self.failures)} 项"
        cover = "完整" if self.complete else "覆盖不完整"
        lines = [f"三表勾稽 {head}: {state}（{self.checks_run}/{len(self.results)} 项已执行，{cover}）"]
        for r in self.results:
            mark = {"passed": "✓", "failed": "✗", "skipped": "–"}[r.status]
            lines.append(f"  {mark} {r.name}{'  ' + r.detail if r.detail else ''}")
        return "\n".join(lines)


# (名称, 左侧项, 右侧各项)
_IDENTITIES: list[tuple[str, str, tuple[str, ...]]] = [
    ("资产=负债+所有者权益", "total_assets", ("total_liabilities", "total_equity")),
    ("净利润=归母+少数股东损益", "net_profit",
     ("net_profit_attr_parent", "minority_interest_profit")),
    ("毛利=营业收入-营业成本", "gross_profit", ("revenue", "-cost_of_revenue")),
    ("期末现金=期初+净增加额", "cash_end", ("cash_begin", "cash_net_change")),
    ("利润表净利润=现金流量表起点", "net_profit", ("cf_net_profit",)),
]


def _within(diff: Decimal, scale: Decimal, abs_tol: Decimal, rel_tol: Decimal) -> bool:
    return diff <= max(abs_tol, abs(scale) * rel_tol)


def reconcile(
    ledger: FactLedger,
    period: str,
    as_of: date,
    abs_tol: Decimal = DEFAULT_ABS_TOL,
    rel_tol: Decimal = DEFAULT_REL_TOL,
) -> ReconcileReport:
    report = ReconcileReport(period=period, as_of=as_of)

    for name, lhs_key, rhs_keys in _IDENTITIES:
        needed = [lhs_key] + [k.lstrip("-") for k in rhs_keys]
        values: dict[str, Decimal] = {}
        missing: list[str] = []

        for key in needed:
            try:
                values[key] = ledger.get(key, period, as_of=as_of).value
            except LookAheadError:
                raise                      # 时点违规必须冒泡，不能降级为 skip
            except KeyError:
                missing.append(key)

        if missing:
            report.results.append(CheckResult(
                name=name, status="skipped",
                detail=f"缺少 {', '.join(missing)}",
            ))
            continue

        lhs = values[lhs_key]
        rhs = sum(
            (-values[k.lstrip("-")] if k.startswith("-") else values[k]
             for k in rhs_keys),
            Decimal(0),
        )
        diff = abs(lhs - rhs)

        if _within(diff, lhs, abs_tol, rel_tol):
            report.results.append(CheckResult(name=name, status="passed", diff=diff))
        else:
            report.results.append(CheckResult(
                name=name, status="failed", diff=diff,
                detail=f"左 {lhs} vs 右 {rhs}，差 {diff}",
            ))

    return report
