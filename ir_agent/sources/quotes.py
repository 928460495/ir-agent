"""行情适配器（腾讯 qt.gtimg.cn）。

注意这是位置分隔的非官方格式，字段顺序随时可能变。因此:
  1. 解析后立刻做自洽校验（现价 - 昨收 == 涨跌额），字段错位会立刻暴露；
  2. 原始响应一律落快照。
生产环境的主干应换成 mootdx（通达信协议），本模块作为兜底与交叉验证。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests

URL = "http://qt.gtimg.cn/q={symbol}"
TIMEOUT = 8

# 位置索引 —— 已对 2026-09 实抓样本核验
_IDX = {
    "name": 1, "code": 2, "last": 3, "prev_close": 4, "open": 5,
    "volume_lots": 6, "time": 30, "change": 31, "change_pct": 32,
    "high": 33, "low": 34, "turnover_rate": 38, "pe_ttm": 39,
    "amplitude": 43, "float_cap_yi": 44, "total_cap_yi": 45, "pb": 46,
}
_MIN_FIELDS = max(_IDX.values()) + 1
_YI = Decimal("100000000")


class ParseError(Exception):
    """行情响应无法解析 —— 通常意味着上游改了格式。"""


def _dec(raw: str, field: str) -> Decimal | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as e:
        raise ParseError(f"字段 {field} 不是数值: {raw!r}") from e


def parse_tencent_quote(text: str) -> dict:
    m = re.search(r'="([^"]*)"', text)
    if not m or not m.group(1).strip():
        raise ParseError("行情响应为空 —— 代码不存在或已停牌退市。")

    parts = m.group(1).split("~")
    if len(parts) < _MIN_FIELDS:
        raise ParseError(
            f"行情字段数不足: 得到 {len(parts)}，至少需要 {_MIN_FIELDS}。"
            "上游格式可能已变更。"
        )

    q: dict = {
        "name": parts[_IDX["name"]].strip(),
        "code": parts[_IDX["code"]].strip(),
        "as_of": datetime.strptime(parts[_IDX["time"]].strip(), "%Y%m%d%H%M%S"),
    }
    for field in ("last", "prev_close", "open", "high", "low",
                  "change", "change_pct", "turnover_rate", "pe_ttm",
                  "pb", "amplitude"):
        q[field] = _dec(parts[_IDX[field]], field)

    for name, idx in (("market_cap", "total_cap_yi"), ("float_cap", "float_cap_yi")):
        yi = _dec(parts[_IDX[idx]], idx)
        q[name] = yi * _YI if yi is not None else None

    # 自洽校验: 字段错位最常见的表现就是这条对不上
    if None not in (q["last"], q["prev_close"], q["change"]):
        if abs((q["last"] - q["prev_close"]) - q["change"]) > Decimal("0.02"):
            raise ParseError(
                f"字段自洽校验失败: 现价 {q['last']} - 昨收 {q['prev_close']} "
                f"≠ 涨跌 {q['change']}。字段顺序可能已变更。"
            )
    return q


def fetch_quote(symbol: str, session: requests.Session | None = None) -> dict:
    """symbol 形如 sh600519 / sz000001。返回解析后的行情 dict。"""
    s = session or requests
    resp = s.get(URL.format(symbol=symbol), timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "gbk"
    return parse_tencent_quote(resp.text)


def parse_tencent_quotes(text: str) -> dict[str, dict]:
    """批量响应（多条 `v_xxx="…";` 拼接）→ {6 位代码: 行情 dict}。

    单条解析失败（停牌、退市、字段错位）只丢那一条 —— 相对估值的样本里
    缺一家要记原因剔除，而不是让整批行情作废。
    """
    out: dict[str, dict] = {}
    for seg in text.split(";"):
        if "=" not in seg:
            continue
        try:
            q = parse_tencent_quote(seg)
        except (ParseError, ValueError):     # 时间字段坏了是 strptime 的 ValueError
            continue
        out[q["code"]] = q
    return out


def fetch_quotes(symbols: list[str], session: requests.Session | None = None,
                 chunk: int = 30) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """批量取行情。返回 (按代码的行情, 每批的 (请求地址, 原始响应文本))
    —— 由调用方落快照，地址记的是这一批真正请求的代码。"""
    s = session or requests
    got: dict[str, dict] = {}
    raws: list[tuple[str, str]] = []
    for i in range(0, len(symbols), chunk):
        url = URL.format(symbol=",".join(symbols[i:i + chunk]))
        resp = s.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "gbk"
        raws.append((url, resp.text))
        got.update(parse_tencent_quotes(resp.text))
    return got, raws


# ─────────────────────────────────────────────────────────────
# Tier-2: 新浪 hq.sinajs.cn
# 覆盖是子集 —— 只有价格，没有市值 / PE / PB。降级后估值章节会缺失，
# 这是有意为之: 谎称有市值比没有市值更危险。
# ─────────────────────────────────────────────────────────────

SINA_URL = "https://hq.sinajs.cn/list={symbol}"
_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn",
                 "User-Agent": "Mozilla/5.0"}

_SINA_IDX = {"name": 0, "open": 1, "prev_close": 2, "last": 3,
             "high": 4, "low": 5, "volume": 8, "amount": 9}
_SINA_DATE, _SINA_TIME = 30, 31
_SINA_MIN = 32


def parse_sina_quote(text: str) -> dict:
    m = re.search(r'="([^"]*)"', text)
    if not m or not m.group(1).strip():
        raise ParseError("行情响应为空 —— 代码不存在或已停牌退市。")

    parts = m.group(1).split(",")
    if len(parts) < _SINA_MIN:
        raise ParseError(
            f"行情字段数不足: 得到 {len(parts)}，至少需要 {_SINA_MIN}。"
            "上游格式可能已变更。"
        )

    q: dict = {"name": parts[_SINA_IDX["name"]].strip()}
    for field in ("open", "prev_close", "last", "high", "low"):
        q[field] = _dec(parts[_SINA_IDX[field]], field)

    if not q["last"] or q["last"] == 0:
        raise ParseError("现价为 0 —— 该标的停牌或无成交，不产生行情事实。")

    q["as_of"] = datetime.strptime(
        f"{parts[_SINA_DATE].strip()} {parts[_SINA_TIME].strip()}",
        "%Y-%m-%d %H:%M:%S",
    )
    q["change"] = q["last"] - q["prev_close"]
    # 新浪不提供以下字段，显式置 None 而非省略，便于下游判断覆盖范围
    q["market_cap"] = None
    q["pe_ttm"] = None
    q["pb"] = None
    return q


def fetch_quote_sina(symbol: str, session: requests.Session | None = None) -> dict:
    s = session or requests
    resp = s.get(SINA_URL.format(symbol=symbol), headers=_SINA_HEADERS,
                 timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "gbk"
    return parse_sina_quote(resp.text)


# ── 分析时点收盘 ──────────────────────────────────────────────
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
MAX_QUOTE_GAP_DAYS = 10            # 最近一笔收盘离分析时点更远就不算当时的价格（停牌）


SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"


def _tencent_day(s, symbol: str, as_of: date) -> list[tuple[date, Decimal]]:
    beg = as_of - timedelta(days=MAX_QUOTE_GAP_DAYS + 20)
    r = s.get(KLINE_URL, params={"param": f"{symbol},day,{beg.isoformat()},{as_of.isoformat()},40,"},
              timeout=TIMEOUT)
    r.raise_for_status()
    rows = (((r.json() or {}).get("data") or {}).get(symbol) or {}).get("day") or []
    return [(date.fromisoformat(str(x[0])), Decimal(str(x[2]))) for x in rows if len(x) > 2]


def _sina_day(s, symbol: str, as_of: date) -> list[tuple[date, Decimal]]:
    """新浪日 K（不复权）。只能从最新一天往回数 datalen 根，按距今天数留冗余。"""
    n = min(1023, (date.today() - as_of).days + MAX_QUOTE_GAP_DAYS + 30)
    r = s.get(SINA_KLINE_URL, params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": n},
              timeout=TIMEOUT)
    r.raise_for_status()
    return [(date.fromisoformat(str(x["day"])[:10]), Decimal(str(x["close"]))) for x in (r.json() or [])]


def close_asof(symbol: str, as_of: date, session: requests.Session | None = None) -> tuple[date, Decimal]:
    """分析时点当日或之前最近一个交易日的**不复权**收盘。腾讯日 K 为 Tier-1，新浪日 K 为 Tier-2
    （2026-11-17 腾讯 fqkline 整体返回 501）。

    不复权：要与总股本相乘还原当时市值；前复权价会被之后的分红送转改写。"""
    s = session or requests
    errs, rows = [], None
    for name, fetch in (("腾讯", _tencent_day), ("新浪", _sina_day)):
        try:
            got = fetch(s, symbol, as_of)
        except Exception as e:                               # noqa: BLE001
            errs.append(f"{name}: {e.__class__.__name__}: {str(e)[:60]}")
            continue
        if any(d <= as_of for d, _ in got):
            rows = got
            break
        # 200 但没有分析时点之前的数据（参数错误、空 data）：照样降级（终审 M5）
        errs.append(f"{name}: 没有 {as_of} 及之前的日 K")
        rows = rows if rows is not None else got
    if rows is None:
        raise ParseError(f"{symbol} 日 K 收盘取不到 —— " + "；".join(errs))
    best = max(((d, px) for d, px in rows if d <= as_of), default=None)
    if best is None:
        raise ParseError(f"{symbol} 在 {as_of} 之前没有日 K 收盘。")
    if (as_of - best[0]).days > MAX_QUOTE_GAP_DAYS:
        raise ParseError(f"{symbol} 最近收盘在 {best[0]}，距分析时点 {as_of} 已 "
                         f"{(as_of - best[0]).days} 天（停牌？），不能当作当时的价格。")
    return best
