"""会计期间标签的排序。

为什么单独成模块: 期间标签是字符串，而字符串的字典序和时间序**不一致**。
实测踩到的例子 —— 取「最新一期宏观数据」时用 max(period) 选出了 2026Q1
而不是更新的 2026H1，因为 "2026H1" < "2026Q1"（'H' 在 'Q' 前）。
错法不报错，只是静默引用了过期的一期。

正确的年内先后是累计口径的截止月份: Q1(3) < H1(6) < Q1-Q3(9) < FY(12)。
"""

from __future__ import annotations

import re

# 累计口径截止月份。半年报叫 H1 而不是 Q2，这是账本沿用的标签，不是笔误。
_END_MONTH = {
    "Q1": 3, "H1": 6, "Q1-Q3": 9, "FY": 12,
    "Q2": 6, "Q3": 9, "Q4": 12,          # 单季标签，同截止月
}

_ACCOUNTING = re.compile(r"^(\d{4})(Q1-Q3|Q[1-4]S?|H[12]|FY)$")
_MONTHLY = re.compile(r"^(\d{4})-(\d{2})$")
_DAILY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


class UnknownPeriod(ValueError):
    """无法排序的期间标签。不猜 —— 猜错的代价是静默引用错期间。"""


def sort_key(period: str) -> tuple[int, int, int]:
    """(年, 月, 日)。可跨会计期间 / 月度 / 即期三种标签比较。

    单季标签 Q3S 与累计 Q1-Q3 截止月相同，靠第三位区分: 累计口径排在后面，
    因为「1-3 季度累计」覆盖的信息多于「第 3 季度单季」。
    """
    m = _DAILY.match(period)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    m = _MONTHLY.match(period)
    if m:
        return int(m.group(1)), int(m.group(2)), 31

    m = _ACCOUNTING.match(period)
    if m:
        year, tag = int(m.group(1)), m.group(2)
        single = tag.endswith("S")
        base = tag[:-1] if single else tag
        month = _END_MONTH.get(base)
        if month is None:
            raise UnknownPeriod(period)
        return year, month, 0 if single else 31

    raise UnknownPeriod(period)


def latest(periods) -> str | None:
    """最新的一期。无法解析的标签直接跳过而非抛错 —— 调用方多为展示路径。"""
    ok = []
    for p in periods:
        try:
            ok.append((sort_key(p), p))
        except UnknownPeriod:
            continue
    return max(ok)[1] if ok else None
