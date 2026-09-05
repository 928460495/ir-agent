"""行情适配器（腾讯 qt.gtimg.cn）。

注意这是位置分隔的非官方格式，字段顺序随时可能变。因此:
  1. 解析后立刻做自洽校验（现价 - 昨收 == 涨跌额），字段错位会立刻暴露；
  2. 原始响应一律落快照。
生产环境的主干应换成 mootdx（通达信协议），本模块作为兜底与交叉验证。
"""

from __future__ import annotations

import re
from datetime import datetime
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
