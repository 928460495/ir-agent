"""简版 FCFF 折现。

刻意保持为纯函数: 输入全部显式传入，不隐式读账本。DCF 的假设必须是
调用方明确写下的，而不是从某处推断出来的 —— 这是估值可审计的前提。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class DCFResult:
    pv_explicit: Decimal          # 显式预测期现值合计
    pv_terminal: Decimal          # 终值现值
    enterprise_value: Decimal
    equity_value: Decimal
    value_per_share: Decimal
    projected_fcf: tuple[Decimal, ...]

    @property
    def terminal_share(self) -> Decimal:
        """终值占企业价值比重 —— DCF 最重要的单一诊断指标。"""
        if self.enterprise_value == 0:
            return Decimal(0)
        return self.pv_terminal / self.enterprise_value


def value(
    fcf_base: Decimal,
    growth_rates: list[Decimal],
    terminal_growth: Decimal,
    wacc: Decimal,
    net_debt: Decimal,
    shares: Decimal,
) -> DCFResult:
    if wacc <= terminal_growth:
        raise ValueError(
            f"WACC ({wacc}) 必须大于永续增长率 ({terminal_growth})，"
            "否则终值发散、估值无意义。"
        )
    if shares <= 0:
        raise ValueError("股本必须为正。")

    projected: list[Decimal] = []
    fcf = fcf_base
    for g in growth_rates:
        fcf = fcf * (Decimal(1) + g)
        projected.append(fcf)

    one = Decimal(1)
    pv_explicit = sum(
        (f / (one + wacc) ** (i + 1) for i, f in enumerate(projected)),
        Decimal(0),
    )

    terminal = projected[-1] * (one + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal / (one + wacc) ** len(projected)

    ev = pv_explicit + pv_terminal
    equity = ev - net_debt

    return DCFResult(
        pv_explicit=pv_explicit,
        pv_terminal=pv_terminal,
        enterprise_value=ev,
        equity_value=equity,
        value_per_share=equity / shares,
        projected_fcf=tuple(projected),
    )


def sensitivity(
    fcf_base: Decimal,
    growth_rates: list[Decimal],
    waccs: list[Decimal],
    terminal_growths: list[Decimal],
    net_debt: Decimal,
    shares: Decimal,
) -> list[list[Decimal]]:
    """每股价值敏感性矩阵，行=WACC，列=永续增长率。"""
    grid: list[list[Decimal]] = []
    for w in waccs:
        row: list[Decimal] = []
        for g in terminal_growths:
            row.append(value(fcf_base, growth_rates, g, w, net_debt, shares).value_per_share)
        grid.append(row)
    return grid
