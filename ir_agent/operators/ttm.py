"""滚动十二个月（TTM）。

必须建立在**单季**之上 —— 累计数直接相加会重复计入。
缺任一季则返回 None: 三个季度加总冒充 TTM 会系统性低估，比没有 TTM 更糟。
"""

from __future__ import annotations

from datetime import date

from ir_agent.ledger import Fact, FactLedger, LookAheadError
from ir_agent.operators.base import derive
from ir_agent.operators.periods import single_label


def trailing_quarters(year: int, quarter: int) -> list[tuple[int, int]]:
    """截至 (year, quarter) 的最近四个单季，含跨年。"""
    out: list[tuple[int, int]] = []
    y, q = year, quarter
    for _ in range(4):
        out.append((y, q))
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return list(reversed(out))


def compute(
    ledger: FactLedger,
    key: str,
    year: int,
    quarter: int,
    as_of: date,
    target_period: str | None = None,
) -> Fact | None:
    inputs: list[Fact] = []
    for y, q in trailing_quarters(year, quarter):
        try:
            inputs.append(ledger.get(key, single_label(y, q), as_of=as_of))
        except (KeyError, LookAheadError):
            return None                     # 宁可没有，也不给部分和

    period = target_period or f"{year}{'FY' if quarter == 4 else f'Q{quarter}'}"
    total = sum((f.value for f in inputs), start=inputs[0].value * 0)
    return derive(ledger, f"{key}_ttm", total, inputs[0].unit, period,
                  inputs, currency=inputs[0].currency)
