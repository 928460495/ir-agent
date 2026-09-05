"""累计口径 → 单季口径。

A 股财报一律是累计数: 20250930 列是前三季度累计，不是 Q3 单季。
拿累计数当单季用是最常见的口径事故，而且**在 prompt 层拦不住** ——
分析师 Agent 看到「2025Q3」就会当单季理解。所以必须在数据层解决:
单季用独立的 period 标签（2025Q3S），跟累计标签（2025Q1-Q3）不可能混淆。
"""

from __future__ import annotations

from datetime import date

from ir_agent.ledger import Fact, FactLedger, LookAheadError
from ir_agent.operators.base import derive

_CUMULATIVE_SUFFIX = {1: "Q1", 2: "H1", 3: "Q1-Q3", 4: "FY"}


def single_label(year: int, quarter: int) -> str:
    """单季标签。S = single quarter，纯 ASCII 以便占位符引用。"""
    return f"{year}Q{quarter}S"


def cumulative_label(year: int, quarter: int) -> str:
    return f"{year}{_CUMULATIVE_SUFFIX[quarter]}"


def decumulate(
    ledger: FactLedger,
    key: str,
    year: int,
    as_of: date,
    strict: bool = False,
) -> dict[str, Fact]:
    """把某个 key 的累计序列拆成四个单季，写回账本。

    某一季缺输入时只跳过该季，不影响其它季 —— 部分可用好过整体失败。
    strict=True 时时点违规会抛出而非跳过。
    """
    def cum(quarter: int) -> Fact | None:
        try:
            return ledger.get(key, cumulative_label(year, quarter), as_of=as_of)
        except LookAheadError:
            if strict:
                raise
            return None
        except KeyError:
            return None

    out: dict[str, Fact] = {}
    for q in (1, 2, 3, 4):
        cur = cum(q)
        if cur is None:
            continue
        if q == 1:
            # Q1 累计即单季，但仍写成单季标签，让下游可以统一按 S 取数
            out[single_label(year, 1)] = derive(
                ledger, key, cur.value, cur.unit, single_label(year, 1),
                [cur], currency=cur.currency)
            continue
        prev = cum(q - 1)
        if prev is None:
            continue
        out[single_label(year, q)] = derive(
            ledger, key, cur.value - prev.value, cur.unit,
            single_label(year, q), [cur, prev], currency=cur.currency)

    return out
