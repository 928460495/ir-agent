"""β —— 自己回归算，不去抓。

抓来的 β 是个孤零零的数字: 用了多长窗口？日频还是周频？基准是沪深300 还是
中证全指？复权了吗？这些全不知道，而它们能把 β 改变三成以上。自己算的话
每一项都是显式的，可复现，还能过 as_of 守卫 —— 用截至某日的行情算，
不会掺进当时看不到的价格。

口径（写进事实的 derived_from，随结论一起走）:

  · 基准    沪深300（1.000300）
  · 频率    周频。日频的 β 受非同步交易与微观噪声影响明显，周频更稳；
            年频样本又太少。周频是卖方常用口径。
  · 窗口    2 年（约 104 个周观测）
  · 价格    前复权，避免分红除权造成的假跌
  · 估计    OLS: β = cov(r_i, r_m) / var(r_m)

同时产出 R²。β 本身不说明拟合好坏 —— 一只与大盘几乎无关的股票也会算出
某个 β，R² 才告诉你这个数值有多少解释力。R² 极低时 CAPM 的适用性本身存疑，
这一点必须让读者看到。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import requests

from ir_agent.ledger import Fact, Method
from ir_agent.market import market_of
from ir_agent.sources.retry import with_retry

# Tier-1 腾讯，Tier-2 东财。次序不是偏好而是实测: push2his 对短时间内的
# 重复请求限流明显，连续几次调用后整机静默返回空串（不是报错，是空响应）。
# 一次运行要给标的和六家可比公司各算一次 β，正好踩在它的限流线上。
# 腾讯的 fqkline 在本项目里已长期用于行情，稳定。
TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
EM_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
TIMEOUT = 20

_SESSION = requests.Session()
_SESSION.headers.update(_HEADERS)
# 基准序列进程内缓存 —— 沪深300 对每家公司都一样，拉七遍既慢又招限流。
_BENCH_CACHE: dict[tuple[str, int], dict[str, "Decimal"]] = {}

SOURCE_ID = "em_beta"

BENCHMARK = "000300"               # 沪深300
BENCHMARK_NAME = "沪深300"
FQT_FORWARD = 1                    # 前复权
KLT_WEEKLY = 102                   # 东财 klt: 101 日 / 102 周 / 103 月

WINDOW_YEARS = 2
WEEKS_PER_YEAR = 52
MIN_OBS = 52                       # 少于一年的周观测不出 β


class BetaUnavailable(Exception):
    """行情不足以估计 β。不给默认 1.0 —— 那是个看不出来的假设。"""


def _symbol(code: str) -> str:
    """腾讯口径。指数用 sh 前缀（沪深300 挂在上证）。"""
    return ("sh" + code) if code == BENCHMARK else (
        ("sh" if market_of(code) == "SH" else "sz") + code)


def _tencent(code: str, weeks: int) -> dict[str, Decimal]:
    sym = _symbol(code)

    def call():
        r = _SESSION.get(TX_URL, params={
            "param": f"{sym},week,,,{weeks},qfq"}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    payload = with_retry(call, attempts=3, base_delay=1.0)
    node = ((payload.get("data") or {}).get(sym)) or {}
    # 个股返回 qfqweek（前复权），指数没有复权概念，返回 week
    rows = node.get("qfqweek") or node.get("week") or []
    out: dict[str, Decimal] = {}
    for row in rows:
        if len(row) < 3:
            continue
        try:
            out[str(row[0])] = Decimal(str(row[2]))     # [日期, 开, 收, …]
        except Exception:                                # noqa: BLE001
            continue
    return out


def _eastmoney(code: str, beg: str, end: str) -> dict[str, Decimal]:
    secid = ("1." if code == BENCHMARK or market_of(code) == "SH" else "0.") + code

    def call():
        r = _SESSION.get(EM_URL, params={
            "secid": secid, "klt": KLT_WEEKLY, "fqt": FQT_FORWARD,
            "beg": beg, "end": end, "fields1": "f1,f2", "fields2": "f51,f53",
        }, timeout=TIMEOUT)
        r.raise_for_status()
        if not r.text.strip():
            raise ValueError("空响应（限流）")
        return r.json()

    payload = with_retry(call, attempts=3, base_delay=1.5)
    out: dict[str, Decimal] = {}
    for ln in ((payload.get("data") or {}).get("klines")) or []:
        parts = ln.split(",")
        if len(parts) >= 2:
            try:
                out[parts[0]] = Decimal(parts[1])
            except Exception:                            # noqa: BLE001
                continue
    return out


def _closes(code: str, as_of: date, years: int) -> dict[str, Decimal]:
    """周收盘序列。腾讯优先，失败降级东财；两者都不行才报错。"""
    weeks = years * WEEKS_PER_YEAR + 8          # 留冗余，边界按日期再裁
    if code == BENCHMARK and (BENCHMARK, weeks) in _BENCH_CACHE:
        return _BENCH_CACHE[(BENCHMARK, weeks)]

    errors = []
    for name, fetch in (
        ("tencent", lambda: _tencent(code, weeks)),
        ("eastmoney", lambda: _eastmoney(
            code, date(as_of.year - years, as_of.month, as_of.day).strftime("%Y%m%d"),
            as_of.strftime("%Y%m%d"))),
    ):
        try:
            out = fetch()
        except Exception as e:                           # noqa: BLE001
            errors.append(f"{name}: {e.__class__.__name__}: {str(e)[:60]}")
            continue
        if out:
            if code == BENCHMARK:
                _BENCH_CACHE[(BENCHMARK, weeks)] = out
            return out
        errors.append(f"{name}: 返回空序列")
    raise BetaUnavailable(f"{code}: 行情取不到 —— " + "；".join(errors))


@dataclass(frozen=True)
class BetaResult:
    code: str
    beta: Decimal
    r_squared: Decimal
    observations: int
    as_of: date
    benchmark: str = BENCHMARK_NAME

    @property
    def weak_fit(self) -> bool:
        """R² 过低时 β 的解释力有限，CAPM 的适用性本身存疑。"""
        return self.r_squared < Decimal("0.1")

    def describe(self) -> str:
        s = (f"β {self.beta:.3f}（对 {self.benchmark}，周频 "
             f"{self.observations} 期，R² {self.r_squared:.3f}）")
        if self.weak_fit:
            s += "　⚠ R² 偏低，β 对本标的的解释力有限"
        return s


def _regress(ri: list[Decimal], rm: list[Decimal]) -> tuple[Decimal, Decimal]:
    n = Decimal(len(ri))
    mi, mm = sum(ri) / n, sum(rm) / n
    cov = sum((a - mi) * (b - mm) for a, b in zip(ri, rm))
    var_m = sum((b - mm) ** 2 for b in rm)
    if var_m == 0:
        raise BetaUnavailable("基准收益率方差为零，无法回归")
    beta = cov / var_m
    var_i = sum((a - mi) ** 2 for a in ri)
    r2 = Decimal(0) if var_i == 0 else (cov * cov) / (var_i * var_m)
    return beta, r2


def estimate(code: str, as_of: date,
             years: int = WINDOW_YEARS) -> BetaResult:
    """对沪深300 回归估计 β。只用 as_of 之前的行情。"""
    stock = _closes(code, as_of, years)
    bench = _closes(BENCHMARK, as_of, years)
    # 只取两边都有的交易周，且严格早于等于 as_of —— 接口的 end 参数不足以
    # 保证这一点（节假日、停牌都会让边界漂）
    days = sorted(d for d in (stock.keys() & bench.keys())
                  if d <= as_of.isoformat())
    if len(days) < MIN_OBS + 1:
        raise BetaUnavailable(
            f"{code}: 可用周观测仅 {max(0, len(days) - 1)} 期，"
            f"少于下限 {MIN_OBS} 期（新股或长期停牌）")

    ri, rm = [], []
    for prev, cur in zip(days, days[1:]):
        p0, p1 = stock[prev], stock[cur]
        b0, b1 = bench[prev], bench[cur]
        if p0 <= 0 or b0 <= 0:
            continue
        ri.append(p1 / p0 - 1)
        rm.append(b1 / b0 - 1)
    if len(ri) < MIN_OBS:
        raise BetaUnavailable(f"{code}: 有效收益率观测不足 {MIN_OBS} 期")

    beta, r2 = _regress(ri, rm)
    return BetaResult(code=code, beta=beta, r_squared=r2,
                      observations=len(ri), as_of=date.fromisoformat(days[-1]))


def beta_facts(res: BetaResult) -> list[Fact]:
    """入账本，供正文与 WACC 论证以占位符引用。

    口径写进 derived_from —— 「β 是 0.8」这句话脱离窗口与基准就没有意义，
    读者必须能看到它是怎么算的。
    """
    src = (f"基准={res.benchmark}", "频率=周频", f"窗口={WINDOW_YEARS}年",
           "复权=前复权", f"观测={res.observations}期")
    per = res.as_of.isoformat()
    return [
        Fact(key="beta", value=res.beta, unit="x", period=per, as_of=res.as_of,
             source_id=SOURCE_ID, method=Method.COMPUTED, derived_from=src),
        Fact(key="beta.r_squared", value=res.r_squared, unit="ratio",
             period=per, as_of=res.as_of, source_id=SOURCE_ID,
             method=Method.COMPUTED, derived_from=src),
    ]
