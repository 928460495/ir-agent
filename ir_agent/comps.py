"""可比公司分析。

两个设计立场，都与「省事」相反:

**可比名单是显式输入，不按行业自动推导。** 同行业 ≠ 可比 —— 茅台与顺鑫农业
同属白酒板块却不可比。选谁做可比本身就是分析判断，应当带理由写进研报，
而不是藏在代码的行业映射表里。（东财行业成分接口也确实频繁限流，
但即使它稳定，自动推导仍然是错的默认。）

**同行披露时间不同。** 某个 as_of 上部分同行还没出年报；此时必须显式排除
并说明原因，绝不用它们的上一期数据来凑 —— 混用期间的可比表看起来完整，
实际在拿去年的同行比今年的自己，而且不会报错。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

MIN_NAMES = 3          # 含目标在内；两家公司的「分位数」没有意义

# 指标的显示口径。量纲不同，混着打没法读。
_RATIO = {"roe", "net_margin", "gross_margin"}
_MULTIPLE = {"pb", "pe_ttm", "asset_turnover", "equity_multiplier"}
_LABEL = {
    "pb": "PB", "pe_ttm": "PE(TTM)", "roe": "ROE",
    "net_margin": "净利率", "gross_margin": "毛利率",
    "asset_turnover": "总资产周转率", "equity_multiplier": "权益乘数",
}


def render(metric: str, v: Decimal | None) -> str:
    if v is None:
        return "—"
    if metric in _RATIO:
        return f"{v * 100:.2f}%"
    if metric in _MULTIPLE:
        return f"{v:.2f}x"
    return f"{v:,.2f}"


def label(metric: str) -> str:
    return _LABEL.get(metric, metric)


class InsufficientPeers(Exception):
    """可比样本太少，分位数会误导。"""


@dataclass(frozen=True)
class PeerSet:
    target: str
    peers: list[str]
    rationale: str          # 为什么这几家可比 —— 要进研报的

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError("必须说明选择这组可比公司的理由 —— 它是分析判断，要写进研报。")
        if self.target in self.peers:
            raise ValueError(f"{self.target} 不能作为自身的可比公司。")
        if len(set(self.peers)) != len(self.peers):
            raise ValueError("可比公司名单中存在重复代码。")

    @property
    def all_codes(self) -> list[str]:
        return [self.target, *self.peers]


@dataclass(frozen=True)
class CompsRow:
    code: str
    name: str
    period: str
    metrics: dict[str, Decimal]


@dataclass(frozen=True)
class Exclusion:
    code: str
    name: str
    reason: str


def percentile_of(value: Decimal, population: list[Decimal]) -> Decimal | None:
    """value 在 population 中的百分位（0–100）。样本不足 2 个时返回 None。"""
    pop = sorted(population)
    if len(pop) < 2:
        return None
    below = sum(1 for v in pop if v < value)
    equal = sum(1 for v in pop if v == value)
    rank = below + (equal - 1) / 2 if equal else below
    pct = Decimal(rank) / Decimal(len(pop) - 1) * 100
    return max(Decimal(0), min(Decimal(100), pct))


@dataclass
class CompsTable:
    target: str
    period: str
    as_of: date
    rows: list[CompsRow]
    excluded: list[Exclusion] = field(default_factory=list)

    @property
    def target_row(self) -> CompsRow:
        return next(r for r in self.rows if r.code == self.target)

    def _values(self, metric: str) -> list[Decimal]:
        return [r.metrics[metric] for r in self.rows if metric in r.metrics]

    def coverage(self, metric: str) -> int:
        """有该指标的公司数 —— 分位数只在这些公司之间计算。"""
        return len(self._values(metric))

    def percentile(self, metric: str) -> Decimal | None:
        tv = self.target_row.metrics.get(metric)
        if tv is None:
            return None
        return percentile_of(tv, self._values(metric))

    def median(self, metric: str) -> Decimal | None:
        v = sorted(self._values(metric))
        if not v:
            return None
        mid = len(v) // 2
        return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2

    def summary(self, metrics: list[str] | None = None) -> str:
        keys = metrics or sorted({k for r in self.rows for k in r.metrics})
        lines = [f"可比分析 {self.target} · {self.period} @ {self.as_of.isoformat()}"
                 f"（{len(self.rows)} 家）"]
        for k in keys:
            pct, med = self.percentile(k), self.median(k)
            tv = self.target_row.metrics.get(k)
            if tv is None:
                lines.append(f"  – {label(k):8} 目标缺该指标")
                continue
            pos = f"{pct:.0f} 分位" if pct is not None else "样本不足"
            lines.append(f"  · {label(k):8} 本司 {render(k, tv):>9}"
                         f"  中位数 {render(k, med):>9}  {pos:>7}"
                         f"  覆盖 {self.coverage(k)}/{len(self.rows)}")
        for e in self.excluded:
            lines.append(f"  ✗ 已排除 {e.name}({e.code}): {e.reason}")
        return "\n".join(lines)


def build_comps(
    target: str,
    rows: list[CompsRow],
    period: str,
    as_of: date,
    excluded: list[Exclusion] | None = None,
) -> CompsTable:
    if not any(r.code == target for r in rows):
        raise ValueError(f"目标公司 {target} 不在可比数据中。")

    bad = {r.period for r in rows} - {period}
    if bad:
        raise ValueError(
            f"可比表混用了期间: {sorted(bad)} 与 {period} —— "
            "拿去年的同行比今年的自己，表面完整实则失效。"
        )

    if len(rows) < MIN_NAMES:
        raise InsufficientPeers(
            f"可比样本仅 {len(rows)} 家，至少需要 {MIN_NAMES} 家 —— "
            "两家公司之间的「分位数」没有统计意义，给出会误导。"
        )
    return CompsTable(target=target, period=period, as_of=as_of,
                      rows=rows, excluded=list(excluded or []))


# ── 取数层 ────────────────────────────────────────────────────
# 「尚未披露」与「抓取失败」必须分开: 前者是正常的时点约束，
# 后者是故障。混为一谈会让故障伪装成正常，正是 SkipReason 那套分类
# 要解决的同一类问题。

METRICS = ("pb", "pe_ttm", "roe", "net_margin", "gross_margin",
           "asset_turnover", "equity_multiplier")


def fetch_row(
    code: str,
    period: str,
    as_of: date,
    fetch,
) -> tuple[CompsRow | None, Exclusion | None]:
    """取一家公司的可比指标。fetch(code, period, as_of) -> {name, metrics}。

    返回 (行, None) 或 (None, 排除说明) —— 不会静默丢弃任何一家。
    """
    from ir_agent.ledger import LookAheadError

    try:
        data = fetch(code, period, as_of)
    except LookAheadError as e:
        return None, Exclusion(code, code, f"该时点尚未披露: {e}")
    except Exception as e:                          # noqa: BLE001
        return None, Exclusion(code, code,
                               f"抓取失败（{e.__class__.__name__}）: {str(e)[:60]}")

    if not data or not data.get("metrics"):
        return None, Exclusion(code, data.get("name", code) if data else code,
                               "返回空结果")

    return CompsRow(code=code, name=data.get("name", code), period=period,
                    metrics=data["metrics"]), None


def peer_metrics_from_ledger(ledger, period: str, as_of: date,
                             spot: str | None = None) -> dict[str, Decimal]:
    """从事实账本里取可比指标。取不到的指标直接缺失，不补默认值。"""
    from ir_agent.ledger import LookAheadError

    out: dict[str, Decimal] = {}
    for key in METRICS:
        # 倍数挂在即期期间上，基本面指标挂在会计期上
        per = spot if key in ("pb", "pe_ttm") and spot else period
        try:
            out[key] = ledger.get(key, per, as_of=as_of).value
        except (KeyError, LookAheadError):
            continue
    return out
