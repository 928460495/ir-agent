"""即期（行情）期间标签。

市值、股价是**即期量**，跟会计期间没有关系。早期把 market_cap 挂在
period="2025FY" 上，单期运行看不出问题，多期对比时会把今天的市值
错配到某个历史会计期上。用报价日本身作为 period，语义就不可能混淆。
"""

from __future__ import annotations

from datetime import date

SPOT_PATTERN = r"\d{4}-\d{2}-\d{2}"


def spot_period(d: date) -> str:
    return d.isoformat()


def is_spot(period: str) -> bool:
    import re
    return bool(re.fullmatch(SPOT_PATTERN, period))
