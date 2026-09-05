"""算子共用基座 —— 统一处理派生事实的溯源与时点。"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, Method


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", s)


def derive(
    ledger: FactLedger,
    key: str,
    value: Decimal,
    unit: str,
    period: str,
    inputs: list[Fact],
    currency: str | None = None,
    basis: str | None = None,
) -> Fact:
    """由若干输入事实生成一个 COMPUTED 事实并写回账本。

    派生事实的 as_of 取输入中最晚的披露日 —— 它不可能比最后一个输入更早可知。
    confidence 取输入中的最低值，不确定性沿链路传播而非被洗掉。
    """
    fact = Fact(
        key=key,
        value=value,
        unit=unit,
        currency=currency,
        period=period,
        as_of=max(f.as_of for f in inputs),
        # 只用 [A-Za-z0-9_-]，否则占位符正则解析不了，引用会原样留在成稿里
        source_id=f"calc_{_slug(key)}_{_slug(period)}",
        method=Method.COMPUTED,
        confidence=min(f.confidence for f in inputs),
        derived_from=tuple(f"{f.key}@{f.period}" for f in inputs),
        basis=basis,
    )
    ledger.put(fact)
    return fact
