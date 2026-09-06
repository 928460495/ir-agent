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
    display: str = ""      # 该引用在正文中渲染成的字符串

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
                method=fact.method.value, display=format_value(fact),
            ))
        return format_value(fact)

    return REF_RE.sub(_sub, draft), notes


# 排除：年份标签(2025年/2025年报)、季度标签(2025Q3/Q3)、纯序号
_LABELS = re.compile(
    # ISO 日期（即期期间标签，如 2026-09-04）—— 必须排在年份规则之前
    r"(19|20)\d{2}-\d{2}-\d{2}"
    # 年-月（宏观月度期间标签，如 2026-07）。必须整体匹配掉:
    # 只靠下面的「年份后跟连字符」规则会剥掉 2026 却留下 -07，
    # 残留的 07 随后被判成裸数字。宏观层引入月度标签后实测踩到。
    r"|(19|20)\d{2}[-/]\d{1,2}\b"
    # 期间标签: 2025FY / 2025H1 / 2025Q1-Q3 / 2025Q3S / 2025年 / 2025年度 /
    # 2025财年 / 2026上半年 —— 「财年」「上半年」必须排在「年」之前，
    # 否则「年」先匹配掉，剩下的「财」「上半」不成标签
    r"|(19|20)\d{2}\s*(FY|H[12]|Q[1-4]S?(-Q[1-4])?"
    r"|财年度|财年|年度|年报|上半年|下半年|全年|年)"
    r"|(19|20)\d{2}(?=[\-/])"
    r"|\bQ[1-4]S?\b"
    r"|第[一二三四]季度"
    # 指数名称。「沪深300」里的 300 是名字的一部分，不是数值 —— 实测正文
    # 写「对沪深300 的敏感度」时被判成裸数字，而白名单里又没有任何占位符
    # 能代替一个指数的名字。
    r"|(?:沪深|中证|上证|深证|科创|创业板指|北证|恒生)\s*\d{2,4}"
    # 期限标签。「10年期国债」的 10 是期限，不是财务数值。
    r"|\d{1,2}\s*年期"
    # 指标名称里的数字不是数值。CR5 是「前五大集中度」这个指标的名字，
    # 实测正文写「行业CR5为 …」时那个 5 被判成裸数字。
    # 用前后瞻而非 \b: 中文是 Unicode 词字符，「行业CR5为」两侧都不成词边界
    r"|(?<![A-Za-z])CR\d{1,2}(?!\d)"
    # 段落序号。研报的风险提示惯用（1）（2）；括号内孤立的一两位数
    # 在这个语境里只可能是序号 —— 真实数值必带单位。
    r"|[（(]\s*\d{1,2}\s*[）)]"
    # 证券代码 —— 不是需要溯源的财务数字。实测招行首稿因「600036」被驳回，
    # 白白多花一到两轮调用，且实质是在训练模型回避提及标的本身。
    r"|\b(?:sh|sz|bj|SH|SZ|BJ)?\d{6}\b"
    # 裸年份（1990–2099）。带后缀的已由上面的期间规则覆盖，这里管
    # 「2025 年度公司…」被拆开后残留的、以及「2021 至 2025 年间」这类区间。
    r"|(?<![\d.])(?:19[9]\d|20\d{2})(?![\d.%])"
)
_NUMERIC = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|亿|万|元|倍|x|个百分点)?")


def resolves(placeholder: str, ledger: FactLedger, as_of: date) -> bool:
    """该占位符此刻能否解析成真值。

    存在的理由: 递给模型一个解析不了的占位符，等于教它伪造引用 —— 而伪造
    引用比裸数字更隐蔽（格式完全合法，只是账本里没有那条事实）。实测踩到的
    是 [[pe_ttm@2025FY]]: PB / PE 是**即期**指标，挂在报价日上，不在会计期，
    而拼提示词的代码一律用了报告期。

    因此凡是要写进提示词的占位符，一律先过这道验证，而不是逐个字段打补丁。
    """
    m = REF_RE.fullmatch(placeholder.strip())
    if m is None:
        return False
    try:
        ledger.get(m.group(2), m.group(3), as_of=as_of)
    except Exception:                                        # noqa: BLE001
        return False
    return True


def audit_bare_numbers(text: str) -> list[str]:
    """返回正文中未经账本的裸数字。空列表 = 全部数字可溯源。"""
    stripped = REF_RE.sub(" ", text)
    stripped = _LABELS.sub(" ", stripped)
    return [m.group(0).strip() for m in _NUMERIC.finditer(stripped) if m.group(0).strip()]
