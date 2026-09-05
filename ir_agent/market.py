"""交易所与板块识别。

早期用 `"SH" if code.startswith(("60","68")) else "SZ"` —— 一个 else 兜住了
所有非沪代码，北交所（43/83/87 段）会被静默当成深市，拿到一份空数据却不报错。
这里改成白名单: 认识的才返回，不认识的明确报错。
"""

from __future__ import annotations

# 前缀 → 交易所。按段列明，不用 else 兜底。
_SH = ("600", "601", "603", "605", "688", "689")          # 沪主板 + 科创板
_SZ = ("000", "001", "002", "003", "300", "301")          # 深主板 + 创业板
_BJ = ("43", "83", "87", "88", "920")                     # 北交所


class UnsupportedMarket(Exception):
    """代码所属市场当前不支持。"""


def market_of(code: str) -> str:
    """返回 'SH' / 'SZ'。北交所与未知代码抛错，绝不猜。"""
    code = code.strip()
    if code.startswith(_SH):
        return "SH"
    if code.startswith(_SZ):
        return "SZ"
    if code.startswith(_BJ):
        raise UnsupportedMarket(
            f"{code} 属于北交所 —— 东财 F10 与新浪财报均不覆盖北交所"
            "（新浪只返回 19700101 占位列）。拒绝用错误的交易所后缀去取空数据。"
        )
    raise UnsupportedMarket(f"无法识别代码 {code} 所属交易所。")


def quote_symbol(code: str) -> str:
    """行情接口用的小写前缀符号，如 sh600519。"""
    return market_of(code).lower() + code
