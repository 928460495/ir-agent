"""相对估值 —— 与 DCF 并列交叉验证（设计：docs/design/2026-10-21-relative-valuation-design.md）。

本模块只做计算，不联网：倍数口径、可比法区间、全行业横截面回归、结果落账本。
取数在 `relval_data.py`，写页在 `relval_sheet.py`。

**口径只有一份。** 同业倍数与本司「桥接回股权」必须用同一个定义，否则可比法算出的
隐含价值里混着口径差。EV 含少数股东权益：EBITDA 是合并口径，含少数股东那一份
（与 `operators/ratios.py` 修正后的 `ev_ebitda` 一致）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ir_agent.industry_profile import _median, _quartiles

KINDS = ("pe", "pb", "ev_ebitda")
LABEL = {"pe": "PE(TTM)", "pb": "PB", "ev_ebitda": "EV/EBITDA"}
# 四分位至少要 4 个值（`industry_profile._quartiles`）—— 三家里的「P25」没有意义
MIN_BAND = 4


@dataclass
class PeerObs:
    """一家公司在估值时点上的观测。金额以元计。"""
    code: str
    name: str
    market_cap: Decimal
    net_profit: Decimal | None        # 归母净利润（TTM，取不到时为报告期 FY）
    np_basis: str                     # "TTM" / "FY"
    equity_parent: Decimal | None
    total_equity: Decimal | None
    debt: Decimal                     # 有息负债，缺项按 0（同 ratios._debt_of）
    cash: Decimal | None
    ebitda: Decimal | None
    roe: Decimal | None
    growth: Decimal | None            # 三年营收复合增速，退路为一年
    growth_basis: str                 # "3y" / "1y" / ""

    @property
    def minority(self) -> Decimal | None:
        """少数股东权益 = 所有者权益合计 − 归母权益。任一取不到判缺，不按 0 计。"""
        if self.total_equity is None or self.equity_parent is None:
            return None
        return self.total_equity - self.equity_parent


def _pos(x: Decimal | None) -> bool:
    return x is not None and x > 0


def _ev(o: PeerObs) -> Decimal | None:
    if o.cash is None or o.minority is None:
        return None
    return o.market_cap + o.debt - o.cash + o.minority


def multiples(o: PeerObs) -> dict[str, Decimal | None]:
    """PE = 市值 ÷ 归母净利润；PB = 市值 ÷ 归母净资产；EV/EBITDA（EV 含少数股东权益）。
    分母 ≤ 0 或缺失给 None —— 负倍数不是「便宜」，是这个倍数不适用。"""
    ev = _ev(o)
    return {
        "pe": o.market_cap / o.net_profit if _pos(o.net_profit) else None,
        "pb": o.market_cap / o.equity_parent if _pos(o.equity_parent) else None,
        "ev_ebitda": ev / o.ebitda if ev is not None and _pos(o.ebitda) else None,
    }


def implied_price(kind: str, mult: Decimal, target: PeerObs,
                  shares: Decimal) -> Decimal | None:
    """倍数 × 本司指标 → 每股价值。本司指标 ≤ 0 或缺失时 None。"""
    if kind == "pe":
        return mult * target.net_profit / shares if _pos(target.net_profit) else None
    if kind == "pb":
        return mult * target.equity_parent / shares if _pos(target.equity_parent) else None
    if kind == "ev_ebitda":
        if not _pos(target.ebitda) or target.cash is None or target.minority is None:
            return None
        return (mult * target.ebitda - target.debt + target.cash
                - target.minority) / shares
    raise ValueError(kind)


@dataclass
class Band:
    """一个倍数的可比法区间。lo / mid / hi 为每股价值（元），mult_* 为倍数本身。"""
    lo: Decimal
    mid: Decimal
    hi: Decimal
    mult_lo: Decimal
    mult_mid: Decimal
    mult_hi: Decimal
    n: int


def _target_metric_ok(kind: str, t: PeerObs) -> bool:
    return implied_price(kind, Decimal(1), t, Decimal(1)) is not None


def comps_bands(peers: list[PeerObs], target: PeerObs,
                shares: Decimal) -> tuple[dict[str, Band | None], list[str]]:
    """同业 P25 / 中位数 / P75 × 本司指标。第二项是「不适用」的原因，每条以倍数名开头。"""
    bands: dict[str, Band | None] = {}
    why: list[str] = []
    for kind in KINDS:
        if not _target_metric_ok(kind, target):
            bands[kind] = None
            why.append(f"{LABEL[kind]}：本司对应指标为负或缺失，不适用")
            continue
        vals = [m for p in peers if (m := multiples(p)[kind]) is not None]
        q = _quartiles(vals)
        if q is None:
            bands[kind] = None
            why.append(f"{LABEL[kind]}：同业有效值不足 {MIN_BAND} 家"
                       f"（仅 {len(vals)} 家），不适用")
            continue
        lo, hi = q
        mid = _median(vals)
        bands[kind] = Band(lo=implied_price(kind, lo, target, shares),
                           mid=implied_price(kind, mid, target, shares),
                           hi=implied_price(kind, hi, target, shares),
                           mult_lo=lo, mult_mid=mid, mult_hi=hi, n=len(vals))
    return bands, why
