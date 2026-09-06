"""估值执行 —— 审核门之后。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ir_agent.operators import dcf
from ir_agent.valuation.assumptions import Assumptions, NotReviewed


@dataclass(frozen=True)
class Valuation:
    result: dcf.DCFResult
    assumptions: Assumptions
    reviewed_by: str
    reviewed_at: date

    @property
    def value_per_share(self) -> Decimal:
        return self.result.value_per_share

    @property
    def terminal_share(self) -> Decimal:
        return self.result.terminal_share

    def summary(self) -> str:
        return (f"每股价值 {self.value_per_share:,.2f} 元"
                f"（终值占企业价值 {self.terminal_share:.1%}）"
                f"　假设审核：{self.reviewed_by} · {self.reviewed_at}")


def value_company(
    assumptions: Assumptions,
    fcf_base: Decimal,
    net_debt: Decimal,
    shares: Decimal,
) -> Valuation:
    if not assumptions.approved:
        raise NotReviewed(
            "估值假设尚未经人工审核，拒绝执行。"
            "先 approve(审核人, 日期) —— 未审核的估值一旦出现在文档里就会被引用。"
        )
    if fcf_base <= 0:
        raise ValueError(
            f"基期自由现金流为 {fcf_base}，非正数 —— DCF 会退化为对终值的猜测。"
            "改用 PB–ROE 或情景法（见 valuation/route.py）。"
        )
    r = dcf.value(
        fcf_base=fcf_base,
        growth_rates=assumptions.growth_rates,
        terminal_growth=assumptions.terminal_growth,
        wacc=assumptions.wacc,
        net_debt=net_debt,
        shares=shares,
    )
    return Valuation(result=r, assumptions=assumptions,
                     reviewed_by=assumptions.reviewed_by,
                     reviewed_at=assumptions.reviewed_at)
