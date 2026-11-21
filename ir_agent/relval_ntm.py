"""预期 PE 的分母：NTM（未来十二个月）EPS，A 股与美股共用（spec 2026-11-21 §4）。

NTM EPS = w × E[本财年] + (1 − w) × E[下一财年]，w = 本财年剩余天数 ÷ 365（截到 0–1）。
任意财年末都适用：美股按各家 10-K 的财年末，A 股一律 12-31。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from ir_agent.ledger import FactLedger, LookAheadError

CN_FYE = "1231"


# 财年日期（与 us-ir-agent 的 `sources.us_periods` 同一算法，本仓只用到 12-31 财年末）
def _parse_fye(fye_mmdd: str) -> tuple[int, int]:
    return int(fye_mmdd[:2]), int(fye_mmdd[2:])


def _nominal_fye(year: int, m: int, d: int) -> date:
    import calendar
    return date(year, m, min(d, calendar.monthrange(year, m)[1]))


def fiscal_year(end: date, fye_mmdd: str) -> int:
    m, d = _parse_fye(fye_mmdd)
    return next(y for y in (end.year - 1, end.year, end.year + 1) if _nominal_fye(y, m, d) >= end)


def ntm_eps(cons, fye_mmdd: str, as_of: date, source: str = "FMP") -> tuple[Decimal, str] | None:
    """缺任一财年的预期返回 None —— 拿一个财年顶两个会把 NTM 说成 FY。"""
    if cons is None or not getattr(cons, "eps", None):
        return None
    fy0 = fiscal_year(as_of, fye_mmdd)
    e0, e1 = cons.eps.get(fy0), cons.eps.get(fy0 + 1)
    if e0 is None or e1 is None:
        return None
    m, d = _parse_fye(fye_mmdd)
    days = (_nominal_fye(fy0, m, d) - as_of).days
    w = min(max(Decimal(days) / Decimal(365), Decimal(0)), Decimal(1))
    v = w * e0.median + (1 - w) * e1.median
    stat = getattr(cons, "stat", "") or "均值"
    return v, f"NTM：FY{fy0} × {w:.3f} + FY{fy0 + 1} × {1 - w:.3f}（{stat}，{source}）"


def ntm_from_ledger(led: FactLedger, fye_mmdd: str, as_of: date) -> tuple[Decimal, str] | None:
    """本司 NTM：取账本里已落的一致预期 `consensus.eps@<年>FY`（A 股研报中位数）。"""
    fy0 = fiscal_year(as_of, fye_mmdd)
    eps = {}
    for y in (fy0, fy0 + 1):
        try:
            eps[y] = SimpleNamespace(median=led.get("consensus.eps", f"{y}FY", as_of=as_of).value)
        except (KeyError, LookAheadError):
            return None
    return ntm_eps(SimpleNamespace(eps=eps, stat="中位"), fye_mmdd, as_of, source="东财研报")
