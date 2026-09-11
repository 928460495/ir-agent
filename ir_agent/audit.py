"""验收审计 —— V0 的硬性门槛。

通过条件: 正文里没有裸数字，且每个引用都能一路反查到原始接口响应。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ir_agent.citation import Footnote, audit_bare_numbers, render
from ir_agent.ledger import Method, FactLedger
from ir_agent.sources.snapshot import SnapshotStore


@dataclass
class AuditResult:
    rendered: str
    footnotes: list[Footnote] = field(default_factory=list)
    bare_numbers: list[str] = field(default_factory=list)
    missing_snapshots: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.bare_numbers and not self.missing_snapshots

    @property
    def traceability(self) -> float:
        """可溯源数字 / 全部数字。V0 验收线是 1.0。"""
        total = len(self.footnotes) + len(self.bare_numbers)
        if total == 0:
            return 1.0
        traced = len(self.footnotes) - len(self.missing_snapshots)
        return max(0.0, traced / total)

    def with_sources(self) -> str:
        """正文 + 数据来源附录。

        脚注一直算得出来，却只出现在 dashboard.html 里 —— 而对外传阅的
        通常是 report.md。这套系统的核心承诺是「每个数字都可反查到账本
        条目与原始接口响应」，算了不交付等于没兑现。

        不插行内标记，只附一张对照表: 行内插 [1][2] 会把中文研报的阅读
        节奏切碎，而按「渲染值 + 字段@期间」对照已经足以回查。
        """
        if not self.footnotes:
            return self.rendered
        lines = [self.rendered.rstrip(), "", "---", "", "## 数据来源",
                 "", "| 数值 | 字段@期间 | 来源 | 披露日 | 取得方式 |",
                 "| --- | --- | --- | --- | --- |"]
        for n in self.footnotes:
            lines.append(f"| {n.display} | {n.key}@{n.period} | {n.source_id} "
                         f"| {n.as_of.isoformat()} | {n.method} |")
        if self.missing_snapshots:
            lines.append("")
            lines.append("> 以下引用缺少原始接口快照，无法完整回查："
                         + "、".join(self.missing_snapshots))
        return "\n".join(lines)

    def summary(self) -> str:
        lines = [
            f"数字可溯源率 {self.traceability:.1%} "
            f"（引用 {len(self.footnotes)} 个，裸数字 {len(self.bare_numbers)} 个）"
        ]
        if self.bare_numbers:
            lines.append(f"  ✗ 未经账本的裸数字: {', '.join(self.bare_numbers[:8])}")
        if self.missing_snapshots:
            lines.append(f"  ✗ 缺失快照: {', '.join(self.missing_snapshots)}")
        if self.ok:
            lines.append("  ✓ 每个数字都可反查到账本条目与原始接口响应")
        return "\n".join(lines)


def audit(
    draft: str,
    ledger: FactLedger,
    store: SnapshotStore,
    as_of: date,
) -> AuditResult:
    bare = audit_bare_numbers(draft)
    rendered, notes = render(draft, ledger, as_of=as_of)

    # 依据 method 而非 source_id 前缀: 前缀是命名约定，换一个就漏。
    # COMPUTED 事实由算子从其它事实推出，溯源在 derived_from 链上，本无快照。
    missing: list[str] = []
    for n in notes:
        if n.method == Method.COMPUTED.value:
            continue
        try:
            store.load(n.source_id)
        except KeyError:
            missing.append(n.source_id)

    return AuditResult(rendered=rendered, footnotes=notes,
                       bare_numbers=bare, missing_snapshots=missing)
