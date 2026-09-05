"""跨源勾稽。

逐字段比数值只能发现「两源数字不同」。但两源数字可以**逐项一致却仍然错**——
典型情形是某源的字段映射错位（把「营业总成本」映到了 cost_of_revenue），
此时两边取的是同一个上游字段，值当然相同，错误却依然存在。

办法是让每个源**各自独立跑一遍勾稽**: 映射错位会让恒等式立刻不成立。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ir_agent.ledger import FactLedger
from ir_agent.validate.reconcile import ReconcileReport, reconcile


DEFAULT_MIN_CHECKS = 2


@dataclass
class CrossReport:
    period: str
    per_source: dict[str, ReconcileReport] = field(default_factory=dict)
    min_checks: int = DEFAULT_MIN_CHECKS

    @property
    def coverage(self) -> dict[str, int]:
        """各源实际跑通的校验项数。通过率与覆盖率必须分开看。"""
        return {s: r.checks_run for s, r in self.per_source.items()}

    @property
    def corroborating_sources(self) -> list[str]:
        """覆盖达标、真正具备佐证价值的源。

        只能跑 1 项校验的源「通过」几乎没有验证价值 —— 东财的通用报表
        模板不适用银行/保险，实测只能跑 1/5，却会显示成「一致通过」。
        """
        return sorted(s for s, r in self.per_source.items()
                      if r.checks_run >= self.min_checks)

    @property
    def cross_checked(self) -> bool:
        return len(self.corroborating_sources) > 1

    @property
    def failing_sources(self) -> list[str]:
        return [s for s, r in self.per_source.items() if not r.ok]

    @property
    def ok(self) -> bool:
        return not self.failing_sources

    @property
    def divergent_checks(self) -> list[str]:
        """在不同源上结论不同的校验项 —— 最强的字段映射错误信号。"""
        if not self.cross_checked:
            return []
        eligible = set(self.corroborating_sources)
        by_check: dict[str, set[str]] = {}
        for name, report in self.per_source.items():
            if name not in eligible:
                continue
            for c in report.results:
                by_check.setdefault(c.name, set()).add(c.status)
        return sorted(n for n, statuses in by_check.items()
                      if len(statuses) > 1 and "failed" in statuses)

    def summary(self) -> str:
        weak = [s for s in self.per_source if s not in self.corroborating_sources]
        if not self.ok:
            head = f"{len(self.failing_sources)} 个源未通过"
        elif self.cross_checked:
            head = "一致通过"
        else:
            head = "未构成有效交叉验证（有效源不足 2 个）"
        lines = [f"跨源勾稽 {self.period}: {head}"]
        for source, report in self.per_source.items():
            state = "通过" if report.ok else f"失败 {len(report.failures)} 项"
            tag = "  ← 覆盖不足，不计入佐证" if source in weak else ""
            lines.append(f"  {'✓' if report.ok else '✗'} {source}: {state}"
                         f"（{report.checks_run}/{len(report.results)} 项已执行）{tag}")
        for name in self.divergent_checks:
            lines.append(f"  ⚠ 两源结论不同: {name} —— 疑似字段映射错位")
        return "\n".join(lines)


def cross_reconcile(
    facts_by_source: dict[str, list],
    period: str,
    as_of: date,
    min_checks: int = DEFAULT_MIN_CHECKS,
) -> CrossReport:
    report = CrossReport(period=period, min_checks=min_checks)
    for source, facts in facts_by_source.items():
        led = FactLedger()
        for f in facts:
            led.put(f)
        report.per_source[source] = reconcile(led, period, as_of=as_of)
    return report
