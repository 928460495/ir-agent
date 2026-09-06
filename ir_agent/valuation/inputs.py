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


# 货币资金低于期末现金超过此比例，视为「现金分类在别处」
CASH_GAP_TOLERANCE = Decimal("0.10")


def cash_gap_warning(ledger: FactLedger, period: str, as_of: date) -> str | None:
    """资产负债表的货币资金 vs 现金流量表的期末现金，差得多就报出来。

    贵州茅台实测: 货币资金 516.9 亿而期末现金 1264.3 亿，差额在「拆出资金」
    991 亿（集团财务公司的同业拆出）。只按货币资金算净负债会少算约 750 亿
    净现金，DCF 每股价值被低估约 60 元。

    穷举类现金科目会一直追着新情况跑；检测不一致才可持续 —— **对不上就报出来
    交给人判断，取值仍用保守的货币资金**。把拆出资金视作自由现金是一个判断，
    应由人做，不该由代码默认。
    """
    mf = _optional(ledger, "cash_and_equivalents", period, as_of)
    ce = _optional(ledger, "cash_end", period, as_of)
    if mf is None or ce is None or ce.value <= 0:
        return None
    if mf.value >= ce.value * (Decimal(1) - CASH_GAP_TOLERANCE):
        return None
    yi = Decimal("1e8")
    return (f"货币资金 {mf.value / yi:,.0f} 亿显著低于期末现金及现金等价物 "
            f"{ce.value / yi:,.0f} 亿 —— 现金可能分类在拆出资金、"
            f"交易性金融资产等科目。净负债按货币资金计算会偏高"
            f"（净现金偏低），估值相应偏保守，请人工核对。")


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
