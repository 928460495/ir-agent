"""宏观事实适配器 —— 把统计局口径的 GDP / CPI / PPI 变成账本事实。

为什么需要它: 永续增长率、价格假设这些数不该由分析师凭印象填。它们有官方
口径，取回来就是可溯源的事实，假设的依据从「外部链接 + 摘录」升级成 [[引用]]。

**时点陷阱（本模块存在的主要理由）**: 接口返回的 REPORT_DATE 是**统计期**，
不是**发布日**。2026 年 7 月 CPI 的 REPORT_DATE 是 2026-07-01，而统计局要到
8 月 9 日才公布。若直接拿 REPORT_DATE 当 as_of，任何 7 月内的回溯都会读到
当时根本不存在的数字 —— 教科书式的未来函数，且不会报错。

因此这里按「统计期末 + 保守滞后」推算披露日。滞后取得比实际发布节奏更长:
宁可晚几天可用，不可早一天可见。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import requests

from ir_agent.ledger import Fact, Method
from ir_agent.periods import sort_key as period_sort_key
from ir_agent.sources.retry import with_retry

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 20

SOURCE_ID = "em_macro"

# 保守滞后（自统计期末起算）。真实节奏: CPI/PPI 次月 9-10 日、GDP 季后 15-20 日。
# 这里取得更长 —— 早一天可见就是未来函数，晚几天可见只是少用一期数据。
MONTHLY_LAG = timedelta(days=15)
QUARTERLY_LAG = timedelta(days=30)


class MacroUnavailable(Exception):
    """宏观接口取不到数据。调用方应当降级而非崩溃 —— 宏观是背景，不是主线。"""


def _end_of_month(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def _month_period(time_label: str, report_date: date) -> tuple[str, date]:
    """「2026年07月份」→ ("2026-07", 披露日)。"""
    m = re.search(r"(\d{4})年\s*(\d{1,2})月", time_label)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
    else:
        y, mo = report_date.year, report_date.month
    return f"{y}-{mo:02d}", _end_of_month(date(y, mo, 1)) + MONTHLY_LAG


# 「2026年第1-2季度」是**累计**口径，对应本项目既有的期间标签体系
_QUARTER_LABELS = {
    "1": ("Q1", 3), "1-2": ("H1", 6), "1-3": ("Q1-Q3", 9), "1-4": ("FY", 12),
}


def _quarter_period(time_label: str, report_date: date) -> tuple[str, date]:
    """「2026年第1-2季度」→ ("2026H1", 披露日)。累计口径，与账本期间标签一致。"""
    m = re.search(r"(\d{4})年第\s*(\d(?:-\d)?)\s*季度", time_label)
    if not m:
        raise MacroUnavailable(f"无法解析 GDP 期间标签: {time_label!r}")
    year, span = int(m.group(1)), m.group(2)
    if span not in _QUARTER_LABELS:
        raise MacroUnavailable(f"未知的季度跨度: {time_label!r}")
    suffix, end_month = _QUARTER_LABELS[span]
    return (f"{year}{suffix}",
            _end_of_month(date(year, end_month, 1)) + QUARTERLY_LAG)


def _get(report: str, size: int) -> list[dict]:
    def call():
        r = requests.get(API, params={
            "reportName": report, "columns": "ALL", "pageSize": size,
            "sortColumns": "REPORT_DATE", "sortTypes": -1,
        }, headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    try:
        payload = with_retry(call)
    except Exception as e:                                   # noqa: BLE001
        raise MacroUnavailable(f"{report}: {e.__class__.__name__}: {e}") from e
    rows = ((payload.get("result") or {}).get("data")) or []
    if not rows:
        raise MacroUnavailable(f"{report}: 返回空结果（{payload.get('message')}）")
    return rows


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


def _report_date(row: dict) -> date:
    return date.fromisoformat(str(row["REPORT_DATE"])[:10])


def fetch_cpi_ppi(size: int = 24) -> list[Fact]:
    """CPI / PPI 同比。单位 ratio —— 接口给的是百分数，这里除以 100 归一。"""
    facts: list[Fact] = []
    for report, col, key in (("RPT_ECONOMY_CPI", "NATIONAL_SAME", "macro.cpi_yoy"),
                             ("RPT_ECONOMY_PPI", "BASE_SAME", "macro.ppi_yoy")):
        for row in _get(report, size):
            v = _dec(row.get(col))
            if v is None:
                continue
            period, as_of = _month_period(str(row.get("TIME") or ""),
                                          _report_date(row))
            facts.append(Fact(key=key, value=v / 100, unit="ratio",
                              period=period, as_of=as_of,
                              source_id=SOURCE_ID, method=Method.REPORTED))
    return facts


def fetch_gdp(size: int = 24) -> list[Fact]:
    """名义 GDP 与实际同比。

    永续增长率要对齐的是**名义**增速（现金流是名义的），而接口的 SUM_SAME 是
    **实际**增速 —— 两者差着平减指数。所以名义同比必须自己从总量算，标记为
    COMPUTED 并写明 derived_from。直接拿 4.7% 当永续增长率是个常见但实质性的错误。
    """
    rows = _get("RPT_ECONOMY_GDP", size)
    facts: list[Fact] = []
    levels: dict[str, tuple[Decimal, str, date]] = {}

    for row in rows:
        total = _dec(row.get("DOMESTICL_PRODUCT_BASE"))
        if total is None:
            continue
        label = str(row.get("TIME") or "")
        period, as_of = _quarter_period(label, _report_date(row))
        levels[period] = (total, label, as_of)
        facts.append(Fact(key="macro.gdp_nominal", value=total, unit="亿元",
                          period=period, as_of=as_of, currency="CNY",
                          source_id=SOURCE_ID, method=Method.REPORTED))
        real = _dec(row.get("SUM_SAME"))
        if real is not None:
            facts.append(Fact(key="macro.gdp_real_yoy", value=real / 100,
                              unit="ratio", period=period, as_of=as_of,
                              source_id=SOURCE_ID, method=Method.REPORTED))

    for period, (total, _, as_of) in levels.items():
        year = int(period[:4])
        prior = levels.get(f"{year - 1}{period[4:]}")
        if prior is None or prior[0] == 0:
            continue
        facts.append(Fact(
            key="macro.gdp_nominal_yoy", value=total / prior[0] - 1,
            unit="ratio", period=period,
            # 同比要等**两期都已披露**才成立，取晚的那个。本期总是更晚，
            # 但写成 max 而非直接取本期，是因为追溯修订可能让上期披露日更晚。
            as_of=max(as_of, prior[2]),
            source_id=SOURCE_ID, method=Method.COMPUTED,
            derived_from=(f"macro.gdp_nominal@{period}",
                          f"macro.gdp_nominal@{year - 1}{period[4:]}")))

    facts.extend(_nominal_cagr(levels))
    return facts


# 永续增长率要对齐的是**长期趋势**，不是最近一期的点值。名义 GDP 增速本身
# 在趋势性下行，拿某一期的同比外推到永续，等于把周期位置误当成结构参数。
CAGR_YEARS = 5


def _nominal_cagr(levels: dict[str, tuple[Decimal, str, date]]) -> list[Fact]:
    """名义 GDP 的多年复合增速。

    只用同口径期间（都是 FY，或都是 H1）两端计算 —— 拿 FY 和 H1 的水平值
    比，比出来的是季节性，不是增长。
    """
    out: list[Fact] = []
    for suffix in ("FY", "H1", "Q1-Q3", "Q1"):
        same = sorted((int(p[:4]), p) for p in levels if p[4:] == suffix)
        if len(same) < 2:
            continue
        (y0, p0), (y1, p1) = same[0], same[-1]
        span = min(y1 - y0, CAGR_YEARS)
        if span < 2:
            continue                      # 两年跨度算不出「趋势」，只是同比
        y0, p0 = next((y, p) for y, p in same if y == y1 - span)
        start, end = levels[p0][0], levels[p1][0]
        if start <= 0:
            continue
        out.append(Fact(
            key="macro.gdp_nominal_cagr", value=(end / start) ** (Decimal(1) / span) - 1,
            unit="ratio", period=p1,
            as_of=max(levels[p0][2], levels[p1][2]),
            source_id=SOURCE_ID, method=Method.COMPUTED,
            derived_from=(f"macro.gdp_nominal@{p0}", f"macro.gdp_nominal@{p1}")))
        break                             # 一个口径足够，FY 优先
    return out


def fetch_macro(size: int = 24) -> list[Fact]:
    """全部宏观事实。任一子项失败不影响其余 —— 宏观是背景信息，缺一项不该阻断研究。"""
    facts: list[Fact] = []
    errors: list[str] = []
    for fn in (fetch_gdp, fetch_cpi_ppi):
        try:
            facts.extend(fn(size))
        except MacroUnavailable as e:
            errors.append(str(e))
    if not facts:
        raise MacroUnavailable("；".join(errors) or "无宏观数据")
    return facts


@dataclass(frozen=True)
class MacroSnapshot:
    """某个时点上可见的宏观图景。用于给分析师提示词提供背景。"""

    facts: list[Fact]
    as_of: date

    def latest(self, key: str) -> Fact | None:
        """时点内可见的最新一期。

        排序走 periods.sort_key 而非字符串比较 —— 字典序会把 2026H1 判成
        早于 2026Q1，静默返回过期的一期。
        """
        seen = [f for f in self.facts if f.key == key and f.as_of <= self.as_of]
        if not seen:
            return None
        return max(seen, key=lambda f: (period_sort_key(f.period), f.as_of))

    def describe(self) -> str:
        lines = []
        for key, label in (("macro.gdp_nominal_yoy", "名义 GDP 同比"),
                           ("macro.gdp_real_yoy", "实际 GDP 同比"),
                           ("macro.cpi_yoy", "CPI 同比"),
                           ("macro.ppi_yoy", "PPI 同比")):
            f = self.latest(key)
            if f is not None:
                lines.append(f"  {label}（{f.period}）{f.value * 100:.2f}%")
        return "\n".join(lines) or "  （该时点无可见宏观数据）"
