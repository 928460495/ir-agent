"""第二阶段: 审核 → 建模 → 敏感性。

每一步都可能**正当地**失败，而失败必须说清楚下一步做什么 ——
依据核验不过、FCF 为负、输入缺失，三者的处理方式完全不同。
统一抛 ValuationBlocked 并带上可操作的说明，而不是各自抛底层异常。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ir_agent.ledger import FactLedger
from ir_agent.operators import dcf
from ir_agent.valuation.assumptions import (
    Assumptions,
    NotReviewed,
    UnsupportedAssumption,
)
from ir_agent.valuation.facts import assumption_facts
from ir_agent.valuation.inputs import (
    MissingInput,
    free_cash_flow,
    net_debt,
    shares_outstanding,
)
from ir_agent.valuation.model import Valuation, value_company

# 敏感性网格: 围绕当前假设各取两档
_WACC_STEP = Decimal("0.01")
_GROWTH_STEP = Decimal("0.005")


class ValuationBlocked(Exception):
    """无法出具估值 —— 消息里说明原因与下一步。"""


@dataclass
class Stage2Result:
    valuation: Valuation
    sensitivity: dict
    ledger: FactLedger


def _grid_axis(center: Decimal, step: Decimal) -> list[Decimal]:
    return [center + step * k for k in (-2, -1, 0, 1, 2)]


def value_from_file(
    assumptions: Assumptions,
    ledger: FactLedger,
    body: str,
    period: str,
    spot_period: str,
    as_of: date,
    reviewer: str,
) -> Stage2Result:
    if not reviewer.strip():
        raise ValuationBlocked("必须指明审核人 —— 估值要有署名才有责任。")

    try:
        approved = assumptions.approve(reviewer, as_of, ledger=ledger,
                                       body=body, as_of=as_of)
    except UnsupportedAssumption as e:
        raise ValuationBlocked(
            f"依据核验未通过，无法审核：\n{e}\n"
            "请修正 assumptions.yaml 中的依据后重试。") from e

    try:
        fcf = free_cash_flow(ledger, period, as_of=as_of)
        nd = net_debt(ledger, period, as_of=as_of)
        sh = shares_outstanding(ledger, spot_period, as_of=as_of)
    except MissingInput as e:
        raise ValuationBlocked(
            f"估值输入缺失，无法建模：{e}\n"
            "缺失项通常意味着该口径在本标的的报表中不存在，"
            "应改用 PB–ROE 或情景法。") from e

    assumption_facts(approved, ledger, period, as_of)

    try:
        v = value_company(approved, fcf_base=fcf.value,
                          net_debt=nd.value, shares=sh.value)
    except NotReviewed as e:                                # pragma: no cover
        raise ValuationBlocked(str(e)) from e
    except ValueError as e:
        raise ValuationBlocked(
            f"{e}\n本标的应改用 PB–ROE 或情景法（见 valuation/route.py）。") from e

    waccs = _grid_axis(approved.wacc, _WACC_STEP)
    growths = _grid_axis(approved.terminal_growth, _GROWTH_STEP)
    grid = dcf.sensitivity(fcf.value, approved.growth_rates, waccs, growths,
                           nd.value, sh.value)
    return Stage2Result(
        valuation=v,
        sensitivity={"waccs": waccs, "growths": growths, "grid": grid,
                     "current": (2, 2)},
        ledger=ledger)
