"""滚动十二个月（TTM）。

必须建立在**单季**之上 —— 累计数直接相加会重复计入。
缺任一季则返回 None: 三个季度加总冒充 TTM 会系统性低估，比没有 TTM 更糟。
"""

from __future__ import annotations

import re
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


# ── 最新 TTM ──────────────────────────────────────────────────
# PE 的分母要配当天已知的最新利润。用年报期跑（--year 2025）时，原先 TTM 截到
# 2025 年报 —— 2026 中报出来后 PE 仍按年报利润算，利润涨得快的公司 PE 被高估。

_CUM = re.compile(r"^(\d{4})(Q1|H1|Q1-Q3|FY)$")
_Q = {"Q1": 1, "H1": 2, "Q1-Q3": 3, "FY": 4}


def _visible_quarters(ledger: FactLedger, key: str, as_of: date):
    for k, per in list(ledger._facts):                  # noqa: SLF001
        m = _CUM.match(per) if k == key else None
        if not m:
            continue
        try:
            fact = ledger.get(k, per, as_of=as_of)
        except (KeyError, LookAheadError):
            continue
        yield (int(m.group(1)), _Q[m.group(2)]), fact


def latest_quarter(ledger: FactLedger, key: str, as_of: date) -> tuple[int, int] | None:
    """key 的累计序列里，截至 as_of 已披露的最新 (年, 季)。"""
    return max((q for q, _ in _visible_quarters(ledger, key, as_of)), default=None)


def latest_visible(ledger: FactLedger, key: str, as_of: date) -> Fact | None:
    """key 挂在累计期间标签上的事实里，截至 as_of 最新的一条（如 net_profit_ttm@2026H1）。"""
    best = max(_visible_quarters(ledger, key, as_of), key=lambda t: t[0], default=None)
    return best[1] if best else None


def ttm_at(ledger: FactLedger, key: str, year: int, quarter: int,
           as_of: date) -> Fact | None:
    """截至 (year, quarter) 的 TTM，挂在该季的累计期间标签上（如 2026H1）。
    先把两年的累计数拆成单季；缺任一季返回 None。"""
    from ir_agent.operators.periods import cumulative_label, decumulate

    for y in (year, year - 1):
        try:
            decumulate(ledger, key, y, as_of=as_of)
        except Exception:                                   # noqa: BLE001
            pass
    return compute(ledger, key, year, quarter, as_of=as_of,
                   target_period=cumulative_label(year, quarter))
