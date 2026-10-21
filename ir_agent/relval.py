"""相对估值 —— 与 DCF 并列交叉验证（设计：docs/design/2026-10-21-relative-valuation-design.md）。

本模块只做计算，不联网：倍数口径、可比法区间、全行业横截面回归、结果落账本。
取数在 `relval_data.py`，写页在 `relval_sheet.py`。

**口径只有一份。** 同业倍数与本司「桥接回股权」必须用同一个定义，否则可比法算出的
隐含价值里混着口径差。EV 含少数股东权益：EBITDA 是合并口径，含少数股东那一份
（与 `operators/ratios.py` 修正后的 `ev_ebitda` 一致）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import numpy as np

from ir_agent.industry_profile import _median, _quartiles

KINDS = ("pe", "pb", "ev_ebitda")
LABEL = {"pe": "PE(TTM)", "pb": "PB", "ev_ebitda": "EV/EBITDA"}
# 四分位至少要 4 个值（`industry_profile._quartiles`）—— 三家里的「P25」没有意义
MIN_BAND = 4

# 回归（设计：一.2）。阈值见设计文档「回归不可用的判定」。
MIN_REG_N = 15
MIN_SLOPE_T = 2.0
PI_QUANTILE = 0.90          # 80% 预测区间的单侧分位 —— P10–P90，与蒙特卡洛龙卷风同口径
TRIM = (5, 95)              # 因变量两端各剔 5%
# 回归种类 → (自变量字段, 因变量倍数, 研报里的名字)
REG_SPEC = {"pb_roe": ("roe", "pb", "PB 对 ROE"),
            "pe_growth": ("growth", "pe", "PE 对增速")}


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
            # 原因会进研报摘要 —— 不写数字（裸数字审计），具体家数在页面上
            why.append(f"{LABEL[kind]}：同业有效值不足四家，不适用")
            continue
        lo, hi = q
        mid = _median(vals)
        bands[kind] = Band(lo=implied_price(kind, lo, target, shares),
                           mid=implied_price(kind, mid, target, shares),
                           hi=implied_price(kind, hi, target, shares),
                           mult_lo=lo, mult_mid=mid, mult_hi=hi, n=len(vals))
    return bands, why


# ── 横截面回归（全行业样本） ─────────────────────────────────────

@dataclass
class Regression:
    """一条单变量回归：倍数 = a + b × 解释变量。不可用时 fit/lo/hi 为 None，但统计量
    与样本照样保留 —— 页面要把「为什么不可用」连同数据一起摆出来。"""
    kind: str
    a: float = math.nan
    b: float = math.nan
    r2: float = math.nan
    t: float = math.nan
    n: int = 0
    s: float = math.nan
    x0: float | None = None
    fit_mult: float | None = None
    lo_mult: float | None = None
    hi_mult: float | None = None
    fit: Decimal | None = None
    lo: Decimal | None = None
    hi: Decimal | None = None
    usable: bool = False
    why: str = ""           # 不可用原因 —— 会进研报摘要，**不得含数字**
    extrapolated: bool = False
    sample: list[tuple[str, str, float, float]] = field(default_factory=list)
    excluded: list[tuple[str, str, str]] = field(default_factory=list)


_X_CN = {"roe": "ROE", "growth": "营收增速"}


def _screen(kind: str, peers: list[PeerObs], target: PeerObs):
    """剔除顺序：本司 → 净资产 → 亏损 → 缺数 → 因变量两端各 5%。每家记原因。"""
    xf, yk, _ = REG_SPEC[kind]
    rows: list[tuple[str, str, float, float]] = []
    excluded: list[tuple[str, str, str]] = []
    for p in peers:
        if p.code == target.code:
            excluded.append((p.code, p.name, "本司"))
        elif not _pos(p.equity_parent):
            excluded.append((p.code, p.name, "净资产为负或缺失"))
        elif not _pos(p.net_profit):
            excluded.append((p.code, p.name, "亏损（归母净利润为负或缺失）"))
        elif getattr(p, xf) is None or (y := multiples(p)[yk]) is None:
            excluded.append((p.code, p.name, f"缺{_X_CN[xf]}或{LABEL[yk]}"))
        else:
            rows.append((p.code, p.name, float(getattr(p, xf)), float(y)))
    if len(rows) >= 3:
        lo, hi = np.percentile([r[3] for r in rows], TRIM)
        keep = []
        for r in rows:
            if r[3] < lo or r[3] > hi:
                excluded.append((r[0], r[1], f"{LABEL[yk]} 落在样本 P5–P95 之外"))
            else:
                keep.append(r)
        rows = keep
    return rows, excluded


def regress(kind: str, sample: list[PeerObs], target: PeerObs,
            shares: Decimal) -> Regression:
    """全行业横截面回归，给出本司的应得倍数、P10–P90 预测区间与对应每股价值。

    可比只有 6 家时做回归，系数是噪声 —— 所以用整个行业（Damodaran 的做法）。
    不可用（样本少、斜率反向、不显著、应得倍数不为正）时只展示、不进区间。
    """
    from scipy.stats import t as tdist

    xf, yk, _ = REG_SPEC[kind]
    rows, excluded = _screen(kind, sample, target)
    r = Regression(kind=kind, sample=rows, excluded=excluded, n=len(rows))
    if r.n < 3:
        r.why = "样本不足十五家"
        return r
    x = np.array([v[2] for v in rows])
    y = np.array([v[3] for v in rows])
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    if sxx == 0:
        r.why = f"样本的{_X_CN[xf]}没有差异"
        return r
    b = float(((x - xm) * (y - ym)).sum() / sxx)
    a = float(ym - b * xm)
    sse = float(((y - (a + b * x)) ** 2).sum())
    sst = float(((y - ym) ** 2).sum())
    s = math.sqrt(sse / (r.n - 2))
    r.a, r.b, r.s = a, b, s
    r.r2 = 1 - sse / sst if sst else 0.0
    r.t = b / (s / math.sqrt(sxx)) if s > 0 else math.inf

    x0 = getattr(target, xf)
    if x0 is not None:
        r.x0 = float(x0)
        p5, p95 = np.percentile(x, TRIM)
        r.extrapolated = not (p5 <= r.x0 <= p95)
        half = float(tdist.ppf(PI_QUANTILE, r.n - 2)) * s * math.sqrt(
            1 + 1 / r.n + (r.x0 - xm) ** 2 / sxx)
        r.fit_mult = a + b * r.x0
        r.lo_mult, r.hi_mult = r.fit_mult - half, r.fit_mult + half

    if r.n < MIN_REG_N:
        r.why = "样本不足十五家"
    elif b <= 0:
        r.why = "斜率方向与理论相反"
    elif r.t < MIN_SLOPE_T:
        r.why = "斜率不显著"
    elif r.x0 is None:
        r.why = f"本司缺{_X_CN[xf]}，不适用"
    elif r.fit_mult <= 0 or r.lo_mult <= 0:
        r.why = "应得倍数或区间下限不为正"
    else:
        prices = [implied_price(yk, Decimal(str(m)), target, shares)
                  for m in (r.fit_mult, r.lo_mult, r.hi_mult)]
        if prices[0] is None:
            r.why = ("本司归母净利润为负或缺失，不适用" if yk == "pe"
                     else "本司归母净资产为负或缺失，不适用")
        else:
            r.fit, r.lo, r.hi = prices
            r.usable = True
    return r


# ── 结果：摘要（只用占位符）与落账本 ──────────────────────────────

_REG_KEY = {"pb_roe": "pb", "pe_growth": "pe"}


@dataclass
class RelvalResult:
    as_of: date
    price: Decimal
    shares: Decimal
    target: PeerObs
    comps: dict[str, Band | None]
    comps_notes: list[str]
    regs: dict[str, Regression]
    sample_size: int                # 行业样本家数（回归前，不含本司）
    industry: str
    warnings: list[str] = field(default_factory=list)
    # 样本剔除说明（含代码与期间）。**只上页面、不进摘要** —— 摘要不许有数字。
    sample_notes: list[str] = field(default_factory=list)
    # 6 家可比的观测（页面逐行列出）。
    comps_peers: list[PeerObs] = field(default_factory=list)

    def summary(self) -> str:
        """进撰写提示词。**只用占位符**，原因文字不含数字 —— 与估值摘要同一契约。"""
        at = f"@{self.as_of.isoformat()}"
        lines = [f"相对估值（可比沿用原名单；回归样本为{self.industry}行业全体）",
                 "  可比法（同业 P25／中位数／P75 的倍数 → 本司每股价值）："]
        for kind in KINDS:
            if self.comps.get(kind) is None:
                continue
            k = f"valuation.rel_{kind}"
            lines.append(
                f"    {LABEL[kind]}：倍数 [[{k}_mult_lo{at}]]／[[{k}_mult_mid{at}]]／"
                f"[[{k}_mult_hi{at}]] → 每股 [[{k}_lo{at}]]／[[{k}_mid{at}]]／[[{k}_hi{at}]]")
        for note in self.comps_notes:
            lines.append(f"    {note}")
        lines.append("  回归调整（全行业横截面，P10–P90 预测区间）：")
        for kind, (xf, _, name) in REG_SPEC.items():
            r = self.regs.get(kind)
            if r is None:
                continue
            if not r.usable:
                lines.append(f"    {name}：不可用 —— {r.why}")
                continue
            k = f"valuation.rel_reg_{_REG_KEY[kind]}"
            ext = (f"（外推：本司{_X_CN[xf]}落在行业样本主体之外，区间因此变宽）"
                   if r.extrapolated else "")
            lines.append(
                f"    {name}：R² [[{k}_r2{at}]]，样本 [[{k}_n{at}]]，应得倍数 "
                f"[[{k}_mult{at}]] → 每股 [[{k}_lo{at}]]～[[{k}_hi{at}]]，"
                f"中枢 [[{k}_fit{at}]]{ext}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


def to_facts(res: RelvalResult, as_of: date,
             source_id: str = "relval") -> list:
    """结果落账本。键一律 `valuation.` 开头 —— 辩论裁判的可引用清单按此前缀收录。"""
    from ir_agent.ledger import Fact, Method

    def f(key: str, value: Decimal, unit: str) -> Fact:
        return Fact(key=key, value=value, unit=unit, period=as_of.isoformat(),
                    as_of=as_of, source_id=source_id, method=Method.COMPUTED)

    out = []
    for kind, b in res.comps.items():
        if b is None:
            continue
        k = f"valuation.rel_{kind}"
        out += [f(f"{k}_lo", b.lo, "元"), f(f"{k}_mid", b.mid, "元"), f(f"{k}_hi", b.hi, "元"),
                f(f"{k}_mult_lo", b.mult_lo, "x"), f(f"{k}_mult_mid", b.mult_mid, "x"),
                f(f"{k}_mult_hi", b.mult_hi, "x")]
    for kind, r in res.regs.items():
        if not r.usable:
            continue
        k = f"valuation.rel_reg_{_REG_KEY[kind]}"
        out += [f(f"{k}_fit", r.fit, "元"), f(f"{k}_lo", r.lo, "元"), f(f"{k}_hi", r.hi, "元"),
                f(f"{k}_mult", Decimal(str(r.fit_mult)), "x"),
                f(f"{k}_r2", Decimal(str(r.r2)), "ratio"),
                f(f"{k}_n", Decimal(r.n), "家")]
    return out
