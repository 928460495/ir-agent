"""多源降级与交叉验证。

降级只解决「拿不到数据」，交叉验证解决「拿到了错的数据」—— 后者更危险，
因为它不会报错。两源都可用时必须比对，分歧显式暴露而非静默取一个。

设计取舍: Tier-1 的值始终胜出（保证有可用数字），但分歧记录在案，
由调用方决定是否阻断。cross_checked 标志区分「真的比对过」与「只有一个源」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Sequence

DEFAULT_REL_TOL = Decimal("0.0005")


class SourceFailed(Exception):
    """所有数据源都失败了。"""


@dataclass(frozen=True)
class SourceAttempt:
    source: str
    status: str                     # "ok" | "failed" | "unavailable" | "skipped"
    error: str = ""
    fact_count: int = 0


@dataclass(frozen=True)
class Disagreement:
    key: str
    period: str
    values: dict[str, Decimal]
    rel_diff: Decimal

    def describe(self) -> str:
        pairs = "，".join(f"{s}={v}" for s, v in self.values.items())
        return f"{self.key}@{self.period}: {pairs}（相对差 {self.rel_diff:.2%}）"


@dataclass
class Resolution:
    facts: list
    primary: str
    attempts: list[SourceAttempt] = field(default_factory=list)
    disagreements: list[Disagreement] = field(default_factory=list)
    cross_checked: bool = False
    raw: dict = field(default_factory=dict)

    def disagreements_in(self, period: str) -> list[Disagreement]:
        """只看某个期间的分歧。

        期外分歧（常见于追溯调整）是情报，期内分歧才影响本期结论 ——
        两者混在一起会让告警很快被无视。
        """
        return [d for d in self.disagreements if d.period == period]

    def has_blocking_disagreement(self, period: str) -> bool:
        return bool(self.disagreements_in(period))

    def disagreement_shape(self) -> str | None:
        """分歧的形状指向不同病因，分诊时先看这个。

        - "annual_only": 只在 FY 期间分歧，中期逐项一致
          → 年报**追溯重述**（同一控制下企业合并、会计政策变更等）。
        - "all_periods": 所有期间都分歧
          → 多半是**字段映射错误**。中国国航实测: cash_net_change 全期为 0，
            因补充资料行覆盖了主表行。
        - "mixed": 两者都不像 —— **需人工看**，不给确定标签。
          中国神华实测即属此类: 分歧横跨 2024FY / 2025FY / 2025H1，
          资产类在 Q1/Q1-Q3 一致但 H1 的收入与现金项不一致，
          单看某一个字段容易误判成「仅年度分歧」。
        """
        if not self.disagreements:
            return None
        bad = {d.period for d in self.disagreements}
        allp = {f.period for f in self.facts}
        annual = {p for p in allp if p.endswith("FY")}
        interim = allp - annual
        if bad <= annual and annual:
            return "annual_only"
        if interim and bad >= interim:
            return "all_periods"
        return "mixed"

    def summary(self, period: str | None = None) -> str:
        lines = [f"数据源: 主源 {self.primary}"
                 + ("，已交叉验证" if self.cross_checked else "，未交叉验证")]
        for a in self.attempts:
            mark = {"ok": "✓", "failed": "✗", "unavailable": "–",
                    "skipped": "·"}[a.status]
            if a.status == "skipped":
                detail = " 未尝试（主源已成功）"
            elif a.error:
                detail = f" {a.error}"
            else:
                detail = f" {a.fact_count} 条事实"
            lines.append(f"  {mark} {a.source}{detail}")
        inside = self.disagreements_in(period) if period else []
        outside = [d for d in self.disagreements if d not in inside]
        shape = self.disagreement_shape()
        hint = {
            "annual_only": "  ↳ 分歧仅见于年度期间，中期逐项一致 —— 疑似年报追溯重述，"
                           "需回到公告原文裁定",
            "all_periods": "  ↳ 分歧遍布所有期间 —— 疑似字段映射错误，先查适配器",
        }.get(shape)
        for d in inside:
            lines.append(f"  ⚠ 本期分歧 {d.describe()}")
        if outside:
            lines.append(f"  · 期外分歧 {len(outside)} 处"
                         + ("（疑似追溯调整，不影响本期结论）" if period else ""))
            for d in outside[:3]:
                lines.append(f"      {d.describe()}")
            if len(outside) > 3:
                lines.append(f"      … 另有 {len(outside) - 3} 处")
        if hint and self.disagreements:
            lines.append(hint)
        return "\n".join(lines)


def _index(facts) -> dict[tuple[str, str], Decimal]:
    return {(f.key, f.period): f.value for f in facts}


def resolve(
    sources: Sequence[tuple[str, Callable[[], list]]],
    cross_check: bool = False,
    rel_tol: Decimal = DEFAULT_REL_TOL,
) -> Resolution:
    """按顺序尝试各源。sources 形如 [(名称, 无参可调用), ...]，Tier-1 在前。

    cross_check=True 时，即便 Tier-1 成功也会尝试次源用于比对（不用于取值）。
    """
    attempts: list[SourceAttempt] = []
    results: dict[str, list] = {}
    primary: str | None = None

    for name, fetch in sources:
        if primary is not None and not cross_check:
            attempts.append(SourceAttempt(name, "skipped"))
            continue
        try:
            facts = fetch()
        except Exception as e:                       # noqa: BLE001
            attempts.append(SourceAttempt(name, "failed",
                                          error=f"{e.__class__.__name__}: {e}"))
            continue
        if not facts:
            attempts.append(SourceAttempt(name, "unavailable",
                                          error="返回空结果"))
            continue
        attempts.append(SourceAttempt(name, "ok", fact_count=len(facts)))
        results[name] = facts
        if primary is None:
            primary = name

    if primary is None:
        errors = "；".join(a.error for a in attempts if a.error)
        raise SourceFailed(f"所有数据源均失败: {errors}")

    disagreements: list[Disagreement] = []
    others = [n for n in results if n != primary]
    cross_checked = bool(cross_check and others)

    if cross_checked:
        base = _index(results[primary])
        for other in others:
            comp = _index(results[other])
            for (key, period), v1 in base.items():
                v2 = comp.get((key, period))
                if v2 is None:
                    continue                        # 覆盖差异不是冲突
                # 以主源为基准 —— 分诊时要回答的是「次源偏离权威值多少」。
                # 主源为 0 时退回对称口径，避免除零。
                scale = abs(v1) if v1 != 0 else max(abs(v1), abs(v2))
                if scale == 0:
                    continue
                rel = abs(v1 - v2) / scale
                if rel > rel_tol:
                    disagreements.append(Disagreement(
                        key=key, period=period,
                        values={primary: v1, other: v2}, rel_diff=rel,
                    ))

    return Resolution(facts=results[primary], primary=primary,
                      attempts=attempts, disagreements=disagreements,
                      cross_checked=cross_checked, raw=results)
