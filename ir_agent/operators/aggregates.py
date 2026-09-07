"""科目合计 —— 报表给的是分量，模型要的是合计。

单独成模块而非塞进某个算子: 折旧摊销与有息负债这两项，比率、FCF 直接法、
WACC 三处都要用。写在哪一处，另外两处就得跨模块调用或重写一遍。

**分量缺失一律视为 0，但至少要有一个分量**。理由: 公司确实可能没有应付
债券、没有使用权资产摊销 —— 缺失是常态而非异常。但一个分量都没有时，
合计为 0 是**假的 0**（意味着取数没成功），必须报错而不是返回零。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, LookAheadError
from ir_agent.operators.base import derive

# 折旧摊销的四个分量。现金流量表「将净利润调节为经营活动现金流量」
# 附注里逐项列示，报表主体不给合计。
DA_PARTS = (
    "depreciation_fixed_asset",       # 固定资产折旧
    "amortization_intangible",        # 无形资产摊销
    "amortization_long_prepaid",      # 长期待摊费用摊销
    "amortization_useright",          # 使用权资产摊销
)

# 有息负债分量。注意**不含应付账款、合同负债等经营性负债** ——
# 它们不构成融资义务，计入会虚增净负债、压低股权价值。
DEBT_PARTS = (
    "short_loan",
    "long_loan",
    "bond_payable",
    "noncurrent_liab_1y",             # 一年内到期的非流动负债
)


class NoComponents(Exception):
    """一个分量都取不到 —— 合计为 0 是假的 0，不能当成真值往下传。"""


def _parts(ledger: FactLedger, keys, period: str, as_of: date) -> list[Fact]:
    out = []
    for k in keys:
        try:
            out.append(ledger.get(k, period, as_of=as_of))
        except (KeyError, LookAheadError):
            continue
    return out


def _sum(ledger: FactLedger, key: str, keys, period: str, as_of: date,
         label: str) -> Fact:
    parts = _parts(ledger, keys, period, as_of)
    if not parts:
        raise NoComponents(
            f"{period} 的{label}分量一个都没取到（{'、'.join(keys)}）—— "
            "合计为 0 会被下游当成真值，拒绝返回。")
    total = sum((f.value for f in parts), Decimal(0))
    return derive(ledger, key, total, parts[0].unit, period, parts,
                  currency=parts[0].currency)


def depreciation_amortization(ledger: FactLedger, period: str,
                              as_of: date) -> Fact:
    """折旧摊销合计。EBITDA 与 FCF 直接法的加回项。"""
    return _sum(ledger, "depreciation_amortization", DA_PARTS, period, as_of,
                "折旧摊销")


def interest_bearing_debt(ledger: FactLedger, period: str,
                          as_of: date) -> Fact:
    """有息负债合计。净负债、资本结构权重、ROIC 的投入资本都要用。"""
    return _sum(ledger, "interest_bearing_debt", DEBT_PARTS, period, as_of,
                "有息负债")


def compute(ledger: FactLedger, period: str, as_of: date) -> dict[str, Fact]:
    """能算的都算出来写回账本；分量全缺的项跳过而非中断。

    跳过是对的: 无有息负债的公司（茅台）确实取不到任何债务分量，
    这不是故障。但折旧摊销全缺就值得注意 —— 除非是纯轻资产公司。
    """
    out: dict[str, Fact] = {}
    for name, fn in (("depreciation_amortization", depreciation_amortization),
                     ("interest_bearing_debt", interest_bearing_debt)):
        try:
            out[name] = fn(ledger, period, as_of)
        except NoComponents:
            continue
    return out
