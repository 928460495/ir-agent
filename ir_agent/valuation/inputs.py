"""DCF 的三项输入: 自由现金流、净负债、股本。

沿用 V0 的立场: **缺任一必需分量则拒绝，绝不补 0。** 一个用 0 资本开支
算出来的自由现金流会让估值系统性偏高，而且不会报错 —— 正是最危险的那类错误。

「可选为 0」与「必需」要分清: 没有应付债券是真实的 0；拿不到货币资金却
当 0 处理，会让净负债严重高估。前者可省，后者必须拒绝。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, LookAheadError
from ir_agent.operators.base import derive

# 有息负债的四个分量。缺失即视为 0 —— 公司没有该项负债是常态。
DEBT_PARTS = ("short_loan", "long_loan", "bond_payable", "noncurrent_liab_1y")


class MissingInput(Exception):
    """缺少必需输入，拒绝产出派生量。"""


def _need(ledger: FactLedger, key: str, period: str, as_of: date) -> Fact:
    try:
        return ledger.get(key, period, as_of=as_of)
    except (KeyError, LookAheadError) as e:
        raise MissingInput(
            f"缺少 {key}@{period}，无法计算 —— 拒绝以 0 代替。（{e}）") from e


def _optional(ledger: FactLedger, key: str, period: str, as_of: date) -> Fact | None:
    try:
        return ledger.get(key, period, as_of=as_of)
    except (KeyError, LookAheadError):
        return None


def free_cash_flow(ledger: FactLedger, period: str, as_of: date) -> Fact:
    """FCF = 经营活动现金流净额 − 资本开支。负值如实呈现。"""
    op = _need(ledger, "cf_operating", period, as_of)
    capex = _need(ledger, "capex", period, as_of)
    return derive(ledger, "fcf", op.value - capex.value, "元", period,
                  [op, capex], currency=op.currency)


def net_debt(ledger: FactLedger, period: str, as_of: date) -> Fact:
    """净负债 = 有息负债合计 − 货币资金。

    有息负债各分量缺失视为 0（公司确实可能没有应付债券）；
    货币资金必需 —— 缺了会让净负债严重高估。
    """
    cash = _need(ledger, "cash_and_equivalents", period, as_of)
    parts = [f for f in (_optional(ledger, k, period, as_of) for k in DEBT_PARTS)
             if f is not None]
    total = sum((f.value for f in parts), Decimal(0))
    return derive(ledger, "net_debt", total - cash.value, "元", period,
                  [*parts, cash], currency=cash.currency)


def shares_outstanding(ledger: FactLedger, spot_period: str, as_of: date) -> Fact:
    """股本 = 总市值 ÷ 股价。

    不取 SHARE_CAPITAL —— 那是面值×股数，依赖「A 股面值为 1 元」的假设，
    而这一假设并非总成立。由行情推出则无此依赖。
    """
    cap = _need(ledger, "market_cap", spot_period, as_of)
    price = _need(ledger, "last_price", spot_period, as_of)
    if price.value <= 0:
        raise MissingInput(f"股价为 {price.value}，无法推算股本。")
    return derive(ledger, "shares", cap.value / price.value, "股",
                  spot_period, [cap, price])
