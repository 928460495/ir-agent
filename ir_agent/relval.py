"""相对估值 —— 与 DCF 并列交叉验证（设计：docs/design/2026-10-21-relative-valuation-design.md）。

本模块只做计算，不联网：倍数口径、可比法区间、全行业横截面回归、结果落账本。
取数在 `relval_data.py`，写页在 `relval_sheet.py`。

**口径只有一份。** 同业倍数与本司「桥接回股权」必须用同一个定义，否则可比法算出的
隐含价值里混着口径差。EV 含少数股东权益：EBITDA 是合并口径，含少数股东那一份
（与 `operators/ratios.py` 修正后的 `ev_ebitda` 一致）。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import numpy as np

from ir_agent.industry_profile import _median, _quartiles


def money_unit(code: str) -> str:
    """A 股只有人民币（us-ir-agent 按市场给美元）。"""
    return "元"



KINDS = ("pe", "pb", "ev_ebitda")
# 美股（设计「四.3」）：PE 同时给 TTM 与 NTM（NTM 只有本司与可比有一致预期）；亏损公司加用 EV/Sales
US_KINDS = ("pe", "pe_ntm", "pb", "ev_ebitda", "ev_sales")
LABEL = {"pe": "PE(TTM)", "pb": "PB", "ev_ebitda": "EV/EBITDA",
         "pe_ntm": "PE(NTM)", "ev_sales": "EV/Sales"}
# 四分位至少要 4 个值（`industry_profile._quartiles`）—— 三家里的「P25」没有意义
MIN_BAND = 4

# 回归（设计：一.2）。阈值见设计文档「回归不可用的判定」。
MIN_REG_N = 15
MIN_SLOPE_T = 2.0
PI_QUANTILE = 0.90          # 80% 预测区间的单侧分位 —— P10–P90，与蒙特卡洛龙卷风同口径
TRIM = (5, 95)              # 因变量两端各剔 5%
# 回归种类 → (自变量字段（第一个是主驱动，判定看它）, 因变量倍数, 研报里的名字)。
# 按驱动表配对（spec 2026-11-21 §5）：多元 OLS，逐个系数报 t 值。
REG_SPEC = {"pb_roe": (("roe3y", "growth"), "pb", "PB 对三年平均 ROE 与增速"),
            "pe_growth": (("growth", "roe3y"), "pe", "PE 对增速与三年平均 ROE")}
# 行业样本不足十五家时扩大样本的上限（只用于回归；A 股最多扩到申万二级）
REG_EXPAND_CAP = 80

# 倍数 → 理论驱动（spec 2026-11-21 §1；Damodaran 的伴随变量）。挑可比、做回归都按这张表，全行业同一规则。
# ROE 一律用三年平均（跨周期口径，不判定是不是周期股）；EV 类倍数看 EBITDA 利润率（可为负）。
DRIVERS: dict[str, tuple[str, ...]] = {
    "pe": ("growth", "roe3y"),
    "pe_ntm": ("growth", "roe3y"),
    "pb": ("roe3y", "growth"),
    "ev_ebitda": ("ebitda_margin", "growth"),
    "ev_sales": ("ebitda_margin", "growth"),
}


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
    growth_basis: str                 # "TTM 3y" / "3y" / "1y" / ""
    # 存量（净资产、现金、负债）取的余额期；流量（EBITDA）取的 TTM 截止期。
    # 同业对齐本司，没出就退上一期 —— 逐家标在页面上。
    bs_basis: str = ""
    flow_basis: str = ""
    # 美股扩展（默认值让 A 股调用点不改）：TTM 营收（EV/Sales 用）、每股现价与 NTM EPS（PE(NTM) 用）、
    # 按期末日期对齐时落后一季的标注（键 "np" / "bs"，值是页面上要标的说明）
    revenue: Decimal | None = None
    price: Decimal | None = None
    eps_ntm: Decimal | None = None
    ntm_basis: str = ""
    lags: dict[str, str] = field(default_factory=dict)
    # 基本面驱动（spec 2026-11-21）：近三个年报 ROE 的平均（不足三年按实有年数，basis 写「2y」等）、
    # TTM EBITDA ÷ TTM 营收
    roe3y: Decimal | None = None
    roe3y_basis: str = ""
    ebitda_margin: Decimal | None = None
    # 业务分类路径：A 股申万（一级, 二级, 三级），美股（四位 SIC,）—— 业务距离用
    path: tuple[str, ...] = ()

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
        # NTM 用每股口径：一致预期给的是每股收益，乘回总股本会混进稀释口径差
        "pe_ntm": o.price / o.eps_ntm if _pos(o.price) and _pos(o.eps_ntm) else None,
        "ev_sales": ev / o.revenue if ev is not None and _pos(o.revenue) else None,
    }


# ── 按业务与基本面挑可比（spec 2026-11-21 §2） ────────────────────────

RELVAL_K = 6
# 业务距离的名字（页面与说明用）
_BD_LABEL = {"cn": {0: "同申万三级", 1: "同申万二级"},
             "us": {0: "同四位 SIC", 1: "同三位 SIC", 2: "同一 FF 行业"}}


def business_distance(a: tuple[str, ...], b: tuple[str, ...], market: str) -> int | None:
    """只看分类层级，全行业同一规则。A 股：0 = 同三级、1 = 同二级，再远不算（底线二级，
    `taxonomy.widen` 同一条线）；美股：0 = 同四位 SIC、1 = 同三位、2 = 同 FF49（样本本就是同一 FF49）。
    路径缺失给 None —— 不参与挑选。"""
    if not a or not b:
        return None
    if market == "us":
        x, y = a[0], b[0]
        if x == y:
            return 0
        return 1 if x[:3] == y[:3] else 2
    if a == b:
        return 0
    return 1 if len(a) >= 2 and len(b) >= 2 and a[:2] == b[:2] else None


def _market(code: str) -> str:
    return "cn"                     # 本仓只做 A 股（us-ir-agent 按代码分市场）


def select_by_drivers(kind: str, sample: list[PeerObs], target: PeerObs, k: int = RELVAL_K
                      ) -> tuple[list[PeerObs], list[tuple[str, int, float]], str]:
    """在「该倍数适用且驱动齐全」的行业样本里，按（业务距离，基本面距离）排序取前 k 家。

    基本面距离：各驱动按候选的四分位距标准化后的欧氏距离（稳健：一两家极端值不主导；
    四分位距为 0 的维度不参与）。先用尽业务距离 0 的公司，不够才用 1、2 补。
    返回（可比，[(代码, 业务距离, 基本面距离)]，说明）；不足 `MIN_BAND` 家返回空列表与原因。
    """
    mk = _market(target.code)
    drv = DRIVERS[kind]
    if any(getattr(target, d) is None for d in drv) or not target.path:
        return [], [], f"{LABEL[kind]}：本司缺驱动数据或行业路径，无从按基本面挑可比"
    cands: list[tuple[PeerObs, int]] = []
    for p in sample:
        if p.code == target.code or multiples(p)[kind] is None:
            continue
        if any(getattr(p, d) is None for d in drv):
            continue
        bd = business_distance(target.path, p.path, mk)
        if bd is not None:
            cands.append((p, bd))
    if len(cands) < MIN_BAND:
        return [], [], f"{LABEL[kind]}：业务与基本面可比不足四家"
    scales: dict[str, float] = {}
    for d in drv:
        q1, q3 = np.percentile([float(getattr(p, d)) for p, _ in cands], [25, 75])
        if q3 - q1 > 0:
            scales[d] = float(q3 - q1)

    def dist(p: PeerObs) -> float:
        return math.sqrt(sum(((float(getattr(p, d)) - float(getattr(target, d))) / sc) ** 2
                             for d, sc in scales.items()))

    ranked = sorted(((p, bd, dist(p)) for p, bd in cands), key=lambda x: (x[1], x[2], x[0].code))[:k]
    far = max(bd for _, bd, _ in ranked)
    note = (f"{LABEL[kind]}：按业务距离与{'、'.join(_DRV_CN[d] for d in drv)}挑可比，"
            f"最远用到{_BD_LABEL[mk][far]}")
    return [p for p, _, _ in ranked], [(p.code, bd, ds) for p, bd, ds in ranked], note


def near_count(kind: str, sample: list[PeerObs], target: PeerObs) -> int:
    """业务距离 0（同申万三级 / 同四位 SIC）里该倍数适用且驱动齐全的家数 —— 不足 k 家时要用上一级补。"""
    mk = _market(target.code)
    return sum(1 for p in sample
               if p.code != target.code and multiples(p)[kind] is not None
               and all(getattr(p, d) is not None for d in DRIVERS[kind])
               and business_distance(target.path, p.path, mk) == 0)


_DRV_CN = {"growth": "营收增速", "roe3y": "三年平均 ROE", "ebitda_margin": "EBITDA 利润率"}


def kinds_for(target: PeerObs, us: bool) -> tuple[str, ...]:
    """本次要算的倍数。两个市场同一规则（spec 2026-11-21 §4）：PE 同时给 TTM 与 NTM；本司 TTM
    归母净利润不为正时再加 EV/Sales（盈利公司的 EV/Sales 不看利润率，拿来比反而误导）。"""
    return tuple(k for k in US_KINDS if k != "ev_sales" or not _pos(target.net_profit))


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
    if kind == "pe_ntm":
        return mult * target.eps_ntm if _pos(target.eps_ntm) else None
    if kind == "ev_sales":
        if not _pos(target.revenue) or target.cash is None or target.minority is None:
            return None
        return (mult * target.revenue - target.debt + target.cash - target.minority) / shares
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
    # 调和平均（只用正倍数；Baker-Ruback、Liu-Nissim-Thomas）—— 与中位数并列作交叉参照
    mult_hm: Decimal | None = None
    hm: Decimal | None = None


def _target_metric_ok(kind: str, t: PeerObs) -> bool:
    return implied_price(kind, Decimal(1), t, Decimal(1)) is not None


HM_GAP_WARN = 0.15


def _harmonic(vals: list[Decimal]) -> Decimal | None:
    pos = [v for v in vals if v > 0]
    return Decimal(len(pos)) / sum((1 / v for v in pos), Decimal(0)) if pos else None


def comps_bands(peers: list[PeerObs], target: PeerObs,
                shares: Decimal, kinds: tuple[str, ...] = KINDS,
                comps_by_kind: dict[str, list[PeerObs]] | None = None
                ) -> tuple[dict[str, Band | None], list[str]]:
    """同业 P25 / 中位数 / P75 × 本司指标，并列调和平均。第二项是「不适用」与提示，每条以倍数名开头。

    `comps_by_kind` 给出时每个倍数用自己的可比名单（按驱动挑出的，或退回的市值名单）。"""
    if not peers and not comps_by_kind:
        # 可比名单没取到（可比分析未完成）—— 说成「不足四家」会误导
        return ({k: None for k in kinds},
                [f"{LABEL[k]}：可比名单未取到，不适用" for k in kinds])
    bands: dict[str, Band | None] = {}
    why: list[str] = []
    for kind in kinds:
        if not _target_metric_ok(kind, target):
            bands[kind] = None
            why.append(f"{LABEL[kind]}：本司无一致预期（或预期为负），只给 TTM" if kind == "pe_ntm"
                       else f"{LABEL[kind]}：本司对应指标为负或缺失，不适用")
            continue
        pool = comps_by_kind.get(kind, peers) if comps_by_kind else peers
        vals = [m for p in pool if (m := multiples(p)[kind]) is not None]
        q = _quartiles(vals)
        if q is None:
            bands[kind] = None
            # 原因会进研报摘要 —— 不写数字（裸数字审计），具体家数在页面上
            why.append(f"{LABEL[kind]}：同业有效值不足四家，不适用")
            continue
        lo, hi = q
        mid = _median(vals)
        hm = _harmonic(vals)
        bands[kind] = Band(lo=implied_price(kind, lo, target, shares),
                           mid=implied_price(kind, mid, target, shares),
                           hi=implied_price(kind, hi, target, shares),
                           mult_lo=lo, mult_mid=mid, mult_hi=hi, n=len(vals),
                           mult_hm=hm, hm=implied_price(kind, hm, target, shares) if hm else None)
        if hm is not None and mid > 0 and abs(mid - hm) / mid > Decimal(str(HM_GAP_WARN)):
            why.append(f"{LABEL[kind]}：中位数与调和平均差距超过提示阈值，同业倍数分布偏斜，以中位数为主")
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
    # 样本里用了退路口径的公司（代码 → 说明），页面逐家标出
    flags: dict[str, str] = field(default_factory=dict)
    # 多元：各自变量的取值（与 sample 逐行对齐）、各驱动的系数与 t 值、因无差异被剔出的驱动
    xnames: tuple[str, ...] = ()
    xsample: list[tuple[float, ...]] = field(default_factory=list)
    coefs: dict[str, float] = field(default_factory=dict)
    ts: dict[str, float] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    # 样本扩大到了哪一级（空 = 没扩）
    expanded: str = ""


_X_CN = {"roe": "ROE", "growth": "营收增速", "roe3y": "三年平均 ROE", "ebitda_margin": "EBITDA 利润率"}


def _screen(kind: str, peers: list[PeerObs], target: PeerObs):
    """剔除顺序：本司 → 净资产 → 亏损 → 缺数 → 因变量两端各 5%。每家记原因。

    返回的行为 (代码, 名称, 主驱动, 因变量, 全部驱动的元组)。"""
    xs, yk, _ = REG_SPEC[kind]
    rows: list[tuple] = []
    excluded: list[tuple[str, str, str]] = []
    flags: dict[str, str] = {}
    dated = _dated(target.np_basis)
    for p in peers:
        if p.code == target.code:
            excluded.append((p.code, p.name, "本司"))
        elif not _pos(p.equity_parent):
            excluded.append((p.code, p.name, "净资产为负或缺失"))
        elif not _pos(p.net_profit):
            excluded.append((p.code, p.name, "亏损（归母净利润为负或缺失）"))
        elif any(getattr(p, x) is None for x in xs) or (y := multiples(p)[yk]) is None:
            excluded.append((p.code, p.name, f"缺{'或'.join(_X_CN[x] for x in xs)}或{LABEL[yk]}"))
        else:
            vals = tuple(float(getattr(p, x)) for x in xs)
            rows.append((p.code, p.name, vals[0], float(y), vals))
            marks = []
            if "growth" in xs and p.growth_basis == "1y":
                marks.append("一年增速")
            if "roe3y" in xs and p.roe3y_basis and p.roe3y_basis != "3y":
                marks.append(f"ROE 均值 {p.roe3y_basis}")
            if dated:
                # 美股：各家财年不同，期间标签天然不同 —— 按期末日期对齐时已判过是否落后一季，
                # 只标那几家（gather_us 写进 lags）
                if yk == "pe" and p.lags.get("np"):
                    marks.append(p.lags["np"])
                if yk == "pb" and p.lags.get("bs"):
                    marks.append(p.lags["bs"])
            elif yk == "pe" and p.np_basis != target.np_basis:
                # 同业还没出本司最新那一季，用了上一季 TTM 或年报（用户决定）—— 逐家标出
                marks.append(f"利润 {p.np_basis}")
            elif yk == "pb" and p.bs_basis and p.bs_basis != target.bs_basis:
                marks.append(f"净资产 {p.bs_basis}")
            if marks:
                flags[p.code] = "；".join(marks)
    if len(rows) >= 3:
        lo, hi = np.percentile([r[3] for r in rows], TRIM)
        keep = []
        for r in rows:
            if r[3] < lo or r[3] > hi:
                excluded.append((r[0], r[1], f"{LABEL[yk]} 落在样本 P5–P95 之外"))
            else:
                keep.append(r)
        rows = keep
    kept = {r[0] for r in rows}
    return rows, excluded, {c: f for c, f in flags.items() if c in kept}


def _dated(basis: str) -> bool:
    """利润口径是按期末日期写的（美股 gather_us：「TTM 截至 2026-06-27」）。"""
    return bool(re.fullmatch(r"TTM 截至 \d{4}-\d{2}-\d{2}", basis or ""))


def regress(kind: str, sample: list[PeerObs], target: PeerObs,
            shares: Decimal) -> Regression:
    """全行业横截面多元回归：倍数 = a + Σ bⱼ × 驱动ⱼ（驱动按 REG_SPEC 配对），给出本司的应得倍数、
    P10–P90 预测区间与对应每股价值。

    可比只有 6 家时做回归，系数是噪声 —— 所以用整个行业（Damodaran 的做法）。判定看主驱动
    （第一个自变量）：样本少、主斜率反向、不显著、应得倍数不为正时只展示、不进区间。
    次驱动在样本里没有差异（或与主驱动共线）时剔出、退化为单变量，记在 `dropped`。
    """
    from scipy.stats import t as tdist

    xs, yk, _ = REG_SPEC[kind]
    rows, excluded, flags = _screen(kind, sample, target)
    r = Regression(kind=kind, sample=[row[:4] for row in rows], excluded=excluded, n=len(rows),
                   flags=flags, xnames=xs, xsample=[row[4] for row in rows])
    if r.n < 3:
        r.why = "样本不足十五家"
        return r
    X = np.array([row[4] for row in rows], dtype=float)
    y = np.array([row[3] for row in rows], dtype=float)
    if np.ptp(X[:, 0]) == 0:
        r.why = f"样本的{_X_CN[xs[0]]}没有差异"
        return r
    used = [0] + [j for j in range(1, len(xs)) if np.ptp(X[:, j]) > 0]
    A = np.column_stack([np.ones(r.n)] + [X[:, j] for j in used])
    if r.n - A.shape[1] < 1 or np.linalg.matrix_rank(A) < A.shape[1]:
        used = [0]
        A = np.column_stack([np.ones(r.n), X[:, 0]])
    r.dropped = [xs[j] for j in range(len(xs)) if j not in used]
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    dof = r.n - A.shape[1]
    sse = float(((y - A @ beta) ** 2).sum())
    sst = float(((y - y.mean()) ** 2).sum())
    s = math.sqrt(sse / dof)
    xtxi = np.linalg.inv(A.T @ A)
    se = s * np.sqrt(np.diag(xtxi))
    tv = [float(beta[i] / se[i]) if se[i] > 0 else math.inf for i in range(len(beta))]
    r.a, r.b, r.s, r.t = float(beta[0]), float(beta[1]), s, tv[1]
    r.r2 = 1 - sse / sst if sst else 0.0
    r.coefs = {xs[j]: float(beta[i + 1]) for i, j in enumerate(used)}
    r.ts = {xs[j]: tv[i + 1] for i, j in enumerate(used)}

    x0s = [getattr(target, xs[j]) for j in used]
    if all(v is not None for v in x0s):
        r.x0 = float(x0s[0])
        x0 = np.array([1.0] + [float(v) for v in x0s])
        r.extrapolated = any(not (np.percentile(X[:, j], TRIM[0]) <= float(v)
                                  <= np.percentile(X[:, j], TRIM[1])) for j, v in zip(used, x0s))
        half = float(tdist.ppf(PI_QUANTILE, dof)) * s * math.sqrt(1 + float(x0 @ xtxi @ x0))
        r.fit_mult = float(x0 @ beta)
        r.lo_mult, r.hi_mult = r.fit_mult - half, r.fit_mult + half

    missing = [_X_CN[xs[j]] for j, v in zip(used, x0s) if v is None]
    if r.n < MIN_REG_N:
        r.why = "样本不足十五家"
    elif r.b <= 0:
        r.why = "斜率方向与理论相反"
    elif r.t < MIN_SLOPE_T:
        r.why = "斜率不显著"
    elif missing:
        r.why = f"本司缺{'、'.join(missing)}，不适用"
    elif "growth" in [xs[j] for j in used] and target.growth_basis not in ("3y", "TTM 3y"):
        # 「TTM 3y」也是三年复合（截止期为中报 / 季报时的写法），只有一年数据才停
        # 本司 x₀ 与样本不同口径，算出的应得倍数是错的 —— 停下，不静默算价
        r.why = "本司营收增速只有一年数据，与样本的三年复合增速口径不同"
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
    # 市值近邻可比（分析阶段的名单；驱动可比不足时的退路）。
    comps_peers: list[PeerObs] = field(default_factory=list)
    # 各倍数实际用的可比名单，及按驱动挑选时的 [(代码, 业务距离, 基本面距离)]（退回的倍数没有）
    comps_by_kind: dict[str, list[PeerObs]] = field(default_factory=dict)
    selection: dict[str, list[tuple[str, int, float]]] = field(default_factory=dict)

    def summary(self) -> str:
        """进撰写提示词。**只用占位符**，原因文字不含数字 —— 与估值摘要同一契约。"""
        at = f"@{self.as_of.isoformat()}"
        lines = [f"相对估值（可比按各倍数的理论驱动从行业样本挑选，不足时退回按市值；回归样本为{self.industry}行业全体）",
                 "  本司分母（与同业同一口径，倍数 × 分母 → 每股价值）：归母净利润 "
                 f"[[valuation.rel_target_np{at}]]、EBITDA [[valuation.rel_target_ebitda{at}]]"
                 + ("（滚动十二个月，截至本司最新已披露季度 —— 不是年报利润，截止期见相对估值页"
                    "「利润口径」列）" if self.target.np_basis.startswith("TTM") else "（最近完整财年）"),
                 "  可比法（同业 P25／中位数／P75 的倍数 → 本司每股价值）："]
        for kind in self.comps:
            if self.comps.get(kind) is None:
                continue
            k = f"valuation.rel_{kind}"
            hm = (f"；调和平均 [[{k}_mult_hm{at}]] → 每股 [[{k}_hm{at}]]"
                  if self.comps[kind].mult_hm is not None else "")
            lines.append(
                f"    {LABEL[kind]}：倍数 [[{k}_mult_lo{at}]]／[[{k}_mult_mid{at}]]／"
                f"[[{k}_mult_hi{at}]] → 每股 [[{k}_lo{at}]]／[[{k}_mid{at}]]／[[{k}_hi{at}]]{hm}")
            names = [p.name for p in self.comps_by_kind.get(kind, [])]
            if names:
                how = "按驱动挑选" if self.selection.get(kind) else "市值近邻"
                lines.append(f"      可比：{'、'.join(names)}（{how}）")
        if self.comps.get("ev_ebitda") is not None:
            # 用户 2026-11-15 选 A：第一阶段可比表的 EV/EBITDA 是年报口径，这里是 TTM
            lines.append("    注：本节 EV/EBITDA 用最新 TTM EBITDA，第一阶段可比表用报告期年报 EBITDA，"
                         "口径不同，倍数不一致是正常的，不必当作数据错误")
        for note in self.comps_notes:
            lines.append(f"    {note}")
        lines.append("  回归调整（全行业横截面，P10–P90 预测区间）：")
        if any(g.expanded for g in self.regs.values()):
            lab = next(g.expanded for g in self.regs.values() if g.expanded)
            lines.append(f"    行业样本不足，回归样本扩大到{lab}（用于回归；三级同业不足时也补进可比名单）")
        for kind, (xfs, _, name) in REG_SPEC.items():
            xf = xfs[0]
            r = self.regs.get(kind)
            if r is None:
                continue
            k = f"valuation.rel_reg_{_REG_KEY[kind]}"
            # 统计量要用占位符写出来：辩论裁判只认账本事实，「不显著」若只是一句
            # 文字，会被判无据断言、回写删光（生益科技第三次试跑踩到）。
            stats = (f"（斜率 [[{k}_slope{at}]]，斜率 t 值 [[{k}_t{at}]]，"
                     f"R² [[{k}_r2{at}]]，样本 [[{k}_n{at}]]）" if _has_stats(r) else "")
            if not r.usable:
                # 统计量照样给（「为什么不可用」要能核验），但要说清它们不是回归有效的证据 ——
                # 茅台实跑：样本 11 家判不可用，研报却拿 R² 写「统计上较强，不能凭空否决」
                note = "；统计量只说明为什么不可用，不作依据" if stats else ""
                lines.append(f"    {name}：不可用 —— {r.why}{stats[:-1] + note + '）' if stats else ''}")
                continue
            ext = (f"（外推：本司{_X_CN[xf]}落在行业样本主体之外，区间因此变宽）"
                   if r.extrapolated else "")
            lines.append(
                f"    {name}：斜率 t 值 [[{k}_t{at}]]，R² [[{k}_r2{at}]]，样本 [[{k}_n{at}]]，应得倍数 "
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

    money = money_unit(res.target.code)
    out = []
    # 本司的倍数分母：撰写时要能拿它对账（AAPL ④ 实跑：研报拿年报利润去核 PE(TTM) 对不上，把 PE
    # 可比法整个排除了 —— 分母其实是最新季度的 TTM，只是没进账本、没人看得见）
    t = res.target
    for key, val, unit in (("np", t.net_profit, money), ("ebitda", t.ebitda, money),
                           ("revenue", t.revenue, money), ("eps_ntm", t.eps_ntm, f"{money}/股")):
        if val is not None:
            out.append(f(f"valuation.rel_target_{key}", val, unit))
    for kind, b in res.comps.items():
        if b is None:
            continue
        k = f"valuation.rel_{kind}"
        out += [f(f"{k}_lo", b.lo, money), f(f"{k}_mid", b.mid, money), f(f"{k}_hi", b.hi, money),
                f(f"{k}_mult_lo", b.mult_lo, "x"), f(f"{k}_mult_mid", b.mult_mid, "x"),
                f(f"{k}_mult_hi", b.mult_hi, "x")]
        if b.mult_hm is not None and b.hm is not None:
            out += [f(f"{k}_mult_hm", b.mult_hm, "x"), f(f"{k}_hm", b.hm, money)]
    for kind, r in res.regs.items():
        k = f"valuation.rel_reg_{_REG_KEY[kind]}"
        # 统计量不论可不可用都落账本 —— 「为什么不可用」也要能被核验
        if _has_stats(r):
            out += [f(f"{k}_slope", Decimal(str(r.b)), "num"),
                    f(f"{k}_t", Decimal(str(r.t)), "num"),
                    f(f"{k}_r2", Decimal(str(r.r2)), "ratio"),
                    f(f"{k}_n", Decimal(r.n), "家")]
        if r.usable:
            out += [f(f"{k}_fit", r.fit, money), f(f"{k}_lo", r.lo, money),
                    f(f"{k}_hi", r.hi, money),
                    f(f"{k}_mult", Decimal(str(r.fit_mult)), "x")]
    return out


def _has_stats(r: Regression) -> bool:
    """回归真正算过（样本够、统计量有限）才有可落账本的统计量。"""
    return r.n >= 3 and all(math.isfinite(v) for v in (r.b, r.t, r.r2))
