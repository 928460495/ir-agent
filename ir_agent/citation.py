"""占位符解析 —— LLM 写 [[source_id#key@period]]，代码填真值。

这是「模型物理上无法编造数字」的实现点。正文里出现裸数字即视为幻觉入口，
由 audit_bare_numbers 在渲染前拦截。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger

# 两种写法都支持:
#   [[key@period]]            分析师用 —— 来源由账本解析，短且跨运行稳定
#   [[source#key@period]]     显式指定来源 —— 同一 key 有多个来源时的唯一手段
REF_RE = re.compile(
    r"\[\[(?:([A-Za-z0-9_\-]+)#)?([A-Za-z0-9_.]+)@([A-Za-z0-9_\-]+)\]\]")

_YI = Decimal("100000000")
_WAN = Decimal("10000")


class UnresolvedReferenceError(Exception):
    """正文引用了账本里不存在、或 source_id 对不上的事实。"""


@dataclass(frozen=True)
class Footnote:
    marker: int
    key: str
    period: str
    source_id: str
    as_of: date
    method: str

    def text(self) -> str:
        return (
            f"[{self.marker}] {self.key}@{self.period} · "
            f"来源 {self.source_id} · 披露日 {self.as_of.isoformat()} · {self.method}"
        )


def format_value(fact: Fact) -> str:
    v = fact.value
    if fact.unit == "ratio":
        return f"{v * 100:.2f}%"
    if fact.unit == "%":
        return f"{v:.2f}%"
    if fact.unit == "元":
        if abs(v) >= _YI:
            return f"{v / _YI:.2f} 亿元"
        if abs(v) >= _WAN:
            return f"{v / _WAN:.2f} 万元"
        return f"{v:.2f} 元"
    if fact.unit == "x":
        return f"{v:.2f}x"
    if fact.unit == "分位":
        return f"{v:.0f} 分位"
    if fact.unit == "家":
        return f"{v:.0f} 家"
    # 兜底也要限精度 —— 未知单位不该把 Decimal 原始精度打进研报
    return f"{v:.2f} {fact.unit}".replace(".00 ", " ")


def render(
    draft: str, ledger: FactLedger, as_of: date
) -> tuple[str, list[Footnote]]:
    """把草稿里的占位符替换成真值，并返回脚注表。

    LookAheadError 不在此处捕获 —— 时点违规必须冒泡到调用方。
    """
    notes: list[Footnote] = []
    seen: dict[tuple[str, str, str], int] = {}

    def _sub(m: re.Match[str]) -> str:
        source_id, key, period = m.group(1), m.group(2), m.group(3)
        try:
            fact = ledger.get(key, period, as_of=as_of)
        except KeyError as e:
            raise UnresolvedReferenceError(
                f"账本中没有 {key}@{period}，但正文引用了它。"
            ) from e

        # 简写形式不指定来源 —— 由账本解析。省的是分析师的输入，不是溯源:
        # 脚注里仍然写入真实 source_id。
        if source_id is not None and fact.source_id != source_id:
            raise UnresolvedReferenceError(
                f"{key}@{period} 的 source_id 不匹配：正文写的是 {source_id!r}，"
                f"账本记录为 {fact.source_id!r}。"
            )

        ident = (fact.source_id, key, period)
        if ident not in seen:
            seen[ident] = len(seen) + 1
            notes.append(Footnote(
                marker=seen[ident], key=key, period=period,
                source_id=fact.source_id, as_of=fact.as_of,
                method=fact.method.value,
            ))
        return format_value(fact)

    return REF_RE.sub(_sub, draft), notes


# 排除：年份标签(2025年/2025年报)、季度标签(2025Q3/Q3)、纯序号
_LABELS = re.compile(
    # ISO 日期（即期期间标签，如 2026-09-04）—— 必须排在年份规则之前
    r"(19|20)\d{2}-\d{2}-\d{2}"
    # 期间标签: 2025FY / 2025H1 / 2025Q1-Q3 / 2025Q3S / 2025年 / 2025年度
    r"|(19|20)\d{2}\s*(FY|H[12]|Q[1-4]S?(-Q[1-4])?|年度|年报|年)"
    r"|(19|20)\d{2}(?=[\-/])"
    r"|\bQ[1-4]S?\b"
    r"|第[一二三四]季度"
)
_NUMERIC = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|亿|万|元|倍|x|个百分点)?")


def audit_bare_numbers(text: str) -> list[str]:
    """返回正文中未经账本的裸数字。空列表 = 全部数字可溯源。"""
    stripped = REF_RE.sub(" ", text)
    stripped = _LABELS.sub(" ", stripped)
    return [m.group(0).strip() for m in _NUMERIC.finditer(stripped) if m.group(0).strip()]
