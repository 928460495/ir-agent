"""利润率结构。"""

from __future__ import annotations

from datetime import date

from ir_agent.ledger import Fact, FactLedger
from ir_agent.operators.base import derive


def compute(ledger: FactLedger, period: str, as_of: date) -> dict[str, Fact]:
    revenue = ledger.get("revenue", period, as_of=as_of)
    if revenue.value == 0:
        raise ZeroDivisionError(f"revenue@{period} 为 0，利润率无定义。")

    out: dict[str, Fact] = {}

    try:
        cost = ledger.get("cost_of_revenue", period, as_of=as_of)
    except KeyError:
        pass
    else:
        out["gross_margin"] = derive(
            ledger, "gross_margin", (revenue.value - cost.value) / revenue.value,
            "ratio", period, [revenue, cost],
        )

    try:
        net = ledger.get("net_profit", period, as_of=as_of)
    except KeyError:
        pass
    else:
        out["net_margin"] = derive(
            ledger, "net_margin", net.value / revenue.value,
            "ratio", period, [revenue, net],
        )

    return out
