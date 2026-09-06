"""假设依据 —— 结构化引用，不是自由文本。

原设计让每项假设填一段说明文字，但自由文本让人工审核门形同虚设:
可以写「量价承压期后回归个位数增长」，听起来有依据却不指向任何东西，
审核者除了凭感觉点头做不了别的。

改为三类**可核验**的引用:

  · FACT      指向事实账本，用占位符寻址，必须能解析
  · REPORT    指向已经过辩论的正文，引文必须真的出现在正文里
  · EXTERNAL  外部来源，必须同时有 URL、原文片段与抓取日期

WACC 的无风险利率、ERP、β 本来就不在账本里，EXTERNAL 是它们的正当出口 ——
但要付出留下可核验痕迹的代价。外部资料会变，没有抓取日期，事后无法判断
当时看到的是什么。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ir_agent.citation import REF_RE
from ir_agent.ledger import FactLedger, LookAheadError

_WS = re.compile(r"\s+")
_URL = re.compile(r"^https?://\S+$")


class InvalidBasis(Exception):
    """依据本身不合法 —— 缺少可核验的要素。"""


class BasisKind(str, Enum):
    FACT = "fact"
    REPORT = "report"
    EXTERNAL = "external"


@dataclass(frozen=True)
class Basis:
    kind: BasisKind
    ref: str = ""              # 占位符 / URL
    quote: str = ""            # 正文引文 / 外部原文片段
    retrieved: date | None = None

    @classmethod
    def fact(cls, placeholder: str) -> "Basis":
        if not REF_RE.fullmatch(placeholder.strip()):
            raise InvalidBasis(
                f"{placeholder!r} 不是合法占位符，应形如 [[key@period]]。")
        return cls(BasisKind.FACT, ref=placeholder.strip())

    @classmethod
    def report(cls, quote: str) -> "Basis":
        if not quote.strip():
            raise InvalidBasis("引用正文必须给出引文。")
        return cls(BasisKind.REPORT, quote=quote.strip())

    @classmethod
    def external(cls, url: str, quote: str, retrieved: date | None) -> "Basis":
        if not _URL.match(url.strip()):
            raise InvalidBasis(f"外部依据必须给出 URL，得到 {url!r}。")
        if not quote.strip():
            raise InvalidBasis("外部依据必须摘录原文片段。")
        if retrieved is None:
            raise InvalidBasis(
                "外部依据必须记录抓取日期 —— 外部资料会变，"
                "没有日期事后无法判断当时看到的是什么。")
        return cls(BasisKind.EXTERNAL, ref=url.strip(),
                   quote=quote.strip(), retrieved=retrieved)

    def describe(self) -> str:
        if self.kind is BasisKind.FACT:
            return f"事实 {self.ref}"
        if self.kind is BasisKind.REPORT:
            return f"正文「{self.quote[:40]}」"
        return f"外部 {self.ref}（{self.retrieved}）「{self.quote[:32]}」"


def _norm(s: str) -> str:
    return _WS.sub("", s)


def validate(
    bases: list[Basis],
    ledger: FactLedger,
    body: str,
    as_of: date,
) -> list[str]:
    """逐条核验依据，返回问题列表。空列表 = 全部可核验。"""
    if not bases:
        return ["未提供任何依据。"]

    body_norm = _norm(body)
    problems: list[str] = []
    for b in bases:
        if b.kind is BasisKind.FACT:
            m = REF_RE.fullmatch(b.ref)
            key, period = m.group(2), m.group(3)
            try:
                ledger.get(key, period, as_of=as_of)
            except (KeyError, LookAheadError):
                problems.append(
                    f"依据引用的事实 {key}@{period} 在账本中不存在或该时点不可得。")
        elif b.kind is BasisKind.REPORT:
            if _norm(b.quote) not in body_norm:
                problems.append(
                    f"依据引用的正文片段「{b.quote[:30]}」在正文中找不到 —— "
                    "与凭空捏造无异。")
        # EXTERNAL 无法在本地核验，但已留下 URL、原文与日期供人复核
    return problems
