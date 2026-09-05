"""估值倍数 —— 跨两个期间的量。

PB = 即期市值 ÷ 财报期归母权益；PE = 即期市值 ÷ 财报期 TTM 归母盈利。
分子是即期量、分母是会计期量，所以 compute 必须同时接收两个 period。
结果按**即期**期间入账 —— 倍数随股价每日变动，挂在会计期上会造成错配。

负值或零不产生倍数: 负的市盈率/市净率没有可比意义，静默返回负数会污染同业比较。
"""

from __future__ import annotations

from datetime import date

from ir_agent.ledger import Fact, FactLedger
from ir_agent.operators.base import derive

# (倍数名, 优先分母, 兜底分母)
_SPECS = [
    ("pe_ttm", "net_profit_ttm", None),
    ("pb", "equity_attr_parent", "total_equity"),
    ("ps_ttm", "revenue_ttm", None),
]


def compute(
    ledger: FactLedger,
    period: str,
    as_of: date,
    spot_period: str | None = None,
) -> dict[str, Fact]:
    """period 是财报期；spot_period 是报价日，省略则与财报期同（向后兼容）。"""
    sp = spot_period or period
    cap = ledger.get("market_cap", sp, as_of=as_of)
    out: dict[str, Fact] = {}

    for name, primary, fallback in _SPECS:
        denom, basis = None, None
        for key, b in ((primary, "parent"), (fallback, "total")):
            if key is None:
                continue
            try:
                denom, basis = ledger.get(key, period, as_of=as_of), b
                break
            except KeyError:
                continue
        if denom is None or denom.value <= 0:
            continue                # 负值/零/缺失都不产生倍数
        out[name] = derive(ledger, name, cap.value / denom.value,
                           "x", sp, [cap, denom],
                           basis=basis if fallback else None)
    return out
