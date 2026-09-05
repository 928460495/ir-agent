"""杜邦分解: ROE = 净利率 × 总资产周转率 × 权益乘数。

**一律优先归母口径。** 中芯国际实测少数股东损益占净利润 30.1%，
用全口径算出的 ROE 与归母口径相差四成以上 —— 这不是「略有差异」。
归母数据缺失时退回全口径，并在 fact.basis 上标明，读者才知道看的是哪个。
"""

from __future__ import annotations

from datetime import date

from ir_agent.ledger import Fact, FactLedger
from ir_agent.operators.base import derive


def _pick(ledger: FactLedger, period: str, as_of: date,
          parent_key: str, total_key: str) -> tuple[Fact, str]:
    try:
        return ledger.get(parent_key, period, as_of=as_of), "parent"
    except KeyError:
        return ledger.get(total_key, period, as_of=as_of), "total"


def compute(ledger: FactLedger, period: str, as_of: date) -> dict[str, Fact]:
    revenue = ledger.get("revenue", period, as_of=as_of)
    assets = ledger.get("total_assets", period, as_of=as_of)
    profit, p_basis = _pick(ledger, period, as_of,
                            "net_profit_attr_parent", "net_profit")
    equity, e_basis = _pick(ledger, period, as_of,
                            "equity_attr_parent", "total_equity")
    basis = "parent" if p_basis == "parent" and e_basis == "parent" else "total"

    for f in (revenue, assets, equity):
        if f.value == 0:
            raise ZeroDivisionError(f"{f.key}@{period} 为 0，杜邦分解无定义。")

    net_margin = derive(ledger, "net_margin", profit.value / revenue.value,
                        "ratio", period, [profit, revenue], basis=basis)
    turnover = derive(ledger, "asset_turnover", revenue.value / assets.value,
                      "x", period, [revenue, assets])
    multiplier = derive(ledger, "equity_multiplier", assets.value / equity.value,
                        "x", period, [assets, equity], basis=basis)
    roe = derive(ledger, "roe",
                 net_margin.value * turnover.value * multiplier.value,
                 "ratio", period, [net_margin, turnover, multiplier],
                 basis=basis)

    return {"net_margin": net_margin, "asset_turnover": turnover,
            "equity_multiplier": multiplier, "roe": roe}
