"""把估值假设写进事实账本。

WACC 9% 不在账本里，质疑者引用它就会触发裸数字驳回 —— 假设因此进不了辩论。
写成 ESTIMATED 事实解决这个问题，并带来三个好处:

  · 质疑者可以用 [[wacc@2025FY]] 正常引用它
  · 脚注里 estimated 与 reported 明显区分 —— **读者一眼看出哪些数字是假设**
  · confidence < 1.0 沿派生链传播: 用假设算出的估值，置信度不会是 1

假设不是事实。让它们同处一个账本但带不同的 method 与置信度，
比把它们放在账本之外更诚实 —— 后者会让读者以为报告里的每个数字都是实测的。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, Method

ASSUMPTION_CONFIDENCE = 0.6      # 假设终究是判断，不是观测


def assumption_facts(assumptions, ledger: FactLedger, period: str,
                     as_of: date) -> list[Fact]:
    """写入 wacc / terminal_growth / growth_yN，返回写入的事实。"""
    out: list[Fact] = []

    def put(key: str, value: Decimal, basis_key: str) -> None:
        bases = assumptions.basis.get(basis_key, [])
        f = Fact(key=key, value=value, unit="ratio", currency=None,
                 period=period, as_of=as_of,
                 source_id=f"assum_{basis_key}_{period}",
                 method=Method.ESTIMATED,
                 confidence=ASSUMPTION_CONFIDENCE,
                 derived_from=tuple(b.describe() for b in bases))
        ledger.put(f)
        out.append(f)

    put("wacc", assumptions.wacc, "wacc")
    put("terminal_growth", assumptions.terminal_growth, "terminal_growth")
    for i, g in enumerate(assumptions.growth_rates, 1):
        put(f"growth_y{i}", g, "growth_rates")
    return out
