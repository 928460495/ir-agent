"""定性产物的只读阅读版（用户 2026-11-22 选方案 A）。

`macro_view.md`、`strategy.md`、`value_chain.md` 是**可编辑的源文件**：门① 的人改稿签名后程序读回
（按指纹判断是否改过），后续撰写引用的也是其中的占位符。把数字直接写进去，人一改，数字就与占位符
脱钩，裸数字审计也会把它们当成账本外数字。所以数字只进另一份文件：每次运行用账本把源文件渲染成
`<名>_阅读版.md`（占位符 → 数值 + 脚注），程序从不读回它。看板的定性部分用 `render_inline`。

与研报的 `citation.render` 同一套取值与格式（`format_value`），区别只在：取不到的占位符**不抛**，原样
保留并标「账本未取到」—— 阅读版是给人看的，不能因为一处引用缺数就整份不出。时点守卫照旧：分析时点
之后才披露的数字同样算取不到。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ir_agent.citation import REF_RE, format_value
from ir_agent.ledger import LookAheadError

SOURCES = ("macro_view", "strategy", "value_chain")
LABELS = {"macro_view": "宏观定性", "strategy": "战略分析", "value_chain": "产业链"}
SUFFIX = "_阅读版.md"
_MISSING = "（账本未取到）"


def _lookup(m: re.Match[str], ledger, as_of: date):
    source_id, key, period = m.group(1), m.group(2), m.group(3)
    try:
        fact = ledger.get(key, period, as_of=as_of)
    except (KeyError, LookAheadError):
        return None
    if source_id is not None and fact.source_id != source_id:
        return None
    return fact


def render_inline(text: str, ledger, as_of: date) -> str:
    """占位符 → 数值，不加脚注（看板卡片用）。取不到的原样保留并标注。"""
    def _sub(m):
        fact = _lookup(m, ledger, as_of)
        return format_value(fact) if fact is not None else m.group(0) + _MISSING
    return REF_RE.sub(_sub, text or "")


def render_readable(text: str, ledger, as_of: date) -> str:
    """占位符 → 数值 + Markdown 脚注；文末附「数字出处」。同一引用只编一个脚注号。"""
    seen: dict[tuple[str, str, str], int] = {}
    notes: list[str] = []

    def _sub(m):
        fact = _lookup(m, ledger, as_of)
        if fact is None:
            return m.group(0) + _MISSING
        ident = (fact.source_id, fact.key, fact.period)
        if ident not in seen:
            seen[ident] = len(seen) + 1
            notes.append(f"[^{seen[ident]}]: {fact.key}@{fact.period} · 来源 {fact.source_id} · "
                         f"披露日 {fact.as_of.isoformat()} · {fact.method.value}")
        return f"{format_value(fact)}[^{seen[ident]}]"

    body = REF_RE.sub(_sub, text or "")
    return body + ("\n\n## 数字出处\n\n" + "\n".join(notes) + "\n" if notes else "\n")


def write_readables(out: Path | str, ledger, as_of: date) -> list[Path]:
    """为目录里已有的定性源文件各写一份阅读版；源文件已不在的，删掉过时的阅读版。返回写出的文件。"""
    out = Path(out)
    written: list[Path] = []
    for name in SOURCES:
        src, dst = out / f"{name}.md", out / f"{name}{SUFFIX}"
        if not src.exists():
            dst.unlink(missing_ok=True)
            continue
        head = (f"> 只读阅读版：占位符已换成账本数值（分析时点 {as_of.isoformat()}），脚注列出处。"
                f"修改请改 `{name}.md`，本文件每次运行重新生成。\n\n")
        dst.write_text(head + render_readable(src.read_text(encoding="utf-8"), ledger, as_of),
                       encoding="utf-8")
        written.append(dst)
    return written
