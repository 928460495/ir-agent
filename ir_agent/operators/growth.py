"""增长率算子。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger
from ir_agent.operators.base import derive


def _rate(ledger: FactLedger, key: str, cur_period: str, base_period: str,
          as_of: date, suffix: str) -> Fact:
    cur = ledger.get(key, cur_period, as_of=as_of)
    base = ledger.get(key, base_period, as_of=as_of)

    if base.value == 0:
        raise ZeroDivisionError(
            f"{key}@{base_period} 为 0，增长率无定义。"
        )
    if base.value < 0:
        raise ValueError(
            f"{key}@{base_period} 为负（{base.value}），由负转正的增长率无经济含义，"
            "请改用绝对额变动描述。"
        )

    return derive(
        ledger,
        key=f"{key}.{suffix}",
        value=(cur.value - base.value) / base.value,
        unit="ratio",
        period=cur_period,
        inputs=[cur, base],
    )


def yoy(ledger: FactLedger, key: str, cur_period: str, base_period: str,
        as_of: date) -> Fact:
    """同比。base_period 显式传入，不做période推断 —— A股的季报口径歧义太多。"""
    return _rate(ledger, key, cur_period, base_period, as_of, "yoy")


def qoq(ledger: FactLedger, key: str, cur_period: str, base_period: str,
        as_of: date) -> Fact:
    """环比。"""
    return _rate(ledger, key, cur_period, base_period, as_of, "qoq")
