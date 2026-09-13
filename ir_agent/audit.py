"""验收审计 —— V0 的硬性门槛。

通过条件: 正文里没有裸数字，且每个引用都能一路反查到原始接口响应。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ir_agent.citation import Footnote, audit_bare_numbers, render
from ir_agent.ledger import Method, FactLedger
from ir_agent.sources.snapshot import SnapshotStore

# **宽口径**，与 `citation.REF_RE` 的严格两段式（`[[key@period]]`）刻意
# 不同：这里要抓的正是"长得像占位符、却没能被 REF_RE 认出来"的东西
# ——`REF_RE` 认得的那些在 `render()` 里已经被替换掉了，替换之后还留在
# 正文里的每一个 `[[…]]`，按定义就是没落地的占位符。
_LEFTOVER = re.compile(r"\[\[[^\]\n]{1,200}\]\]")


@dataclass
class AuditResult:
    rendered: str
    footnotes: list[Footnote] = field(default_factory=list)
    bare_numbers: list[str] = field(default_factory=list)
    missing_snapshots: list[str] = field(default_factory=list)
    # 渲染之后仍留在正文里的占位符形状 —— 见 `_LEFTOVER` 与 `audit()`。
    unrendered: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (not self.bare_numbers and not self.missing_snapshots
                and not self.unrendered)

    @property
    def traceability(self) -> float:
        """可溯源数字 / 全部数字。V0 验收线是 1.0。

        未替换的占位符计进分母：它本来就是"该落地却没落地的数字"。不计
        的话，一份一个数字都没渲染出来的研报反而是满分——实测确认过，
        整段估值漏成占位符的那次，可溯源率报的正是 100.0%。"""
        total = (len(self.footnotes) + len(self.bare_numbers)
                 + len(self.unrendered))
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
            # 去重后再列。这张表按脚注逐条追加，实跑里 em_macro 重复
            # 7 次、eastmoney_report 重复 13 次，照抄只会刷屏——读者要
            # 知道的是哪几个来源、总共多少条（终端那条说明已经这么做，
            # 交付文件不该比终端更难读）。
            seen: list[str] = []
            for x in self.missing_snapshots:
                if x not in seen:
                    seen.append(x)
            lines.append("")
            lines.append(f"> 有 {len(self.missing_snapshots)} 条引用缺少原始"
                         "接口快照，无法完整回查，来源：" + "、".join(seen))
        if self.unrendered:
            lines.append("")
            lines.append("> 以下占位符未能替换成真实数字，正文里仍是原样的"
                         "占位符，请勿据此阅读："
                         + "、".join(self.unrendered))
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
        if self.unrendered:
            lines.append("  ✗ 未替换的占位符（原样漏进正文，读者看不到真实"
                         f"数字）: {', '.join(self.unrendered[:8])}")
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
    # **查「占位符有没有被替换」，而不只是「占位符用得对不对」。**
    # 实测教训（600584 端到端）：`dcf.summary()` 写的是
    # `[[valuation.wacc]]`，少了 `@period`，`REF_RE` 不认它是占位符，
    # `REF_RE.sub` 看都没看见——整段估值原样漏进交付的 report.md，真实
    # 数字只在终端。它穿过了全部四道闸门，因为每一道查的都是"占位符
    # 用得对不对"（格式合法吗、指向的事实在不在、期间同源吗），
    # 没有一道查"替换发生了没有"。2481cde 修的是那一个产出端，这里补的
    # 是闸门本身：替换之后还留在正文里的 `[[…]]`，不管出于什么原因，
    # 都是没落地的数字，验收不能算通过。
    #
    # 在 `rendered`（替换之后）上扫，而不是 `draft`：合法占位符此时已经
    # 变成真实数字，扫到的只会是没被认出来的那些。去重但保序——同一个
    # 写错的占位符常出现多次，列表要能直接念给人听。
    left: list[str] = []
    for m in _LEFTOVER.findall(rendered):
        if m not in left:
            left.append(m)

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
                       bare_numbers=bare, missing_snapshots=missing,
                       unrendered=left)
