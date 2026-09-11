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

**它不属于估值模块，因此不在 valuation 包里。** 依据体系服务的是战略分析、
产业链、宏观定性、外部资料这些**定性判断**: 每条判断都要能指回账本事实、
年报原文或已抓取的外部文档。估值只是它的使用者之一。

换掉 DCF 实现时这一点变得要紧 —— 估值模块整体删除，而依据核验必须留下，
否则四个定性模块会一起断掉。
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

    def to_dict(self) -> dict:
        """落盘用。**依据必须能跨阶段存活** —— 两阶段流程里第一阶段写出、
        第二阶段读回，此前只存了数值不存依据，读回来的假设一律被判
        「未提供任何依据」而拒绝建模。实测踩到。"""
        d = {"kind": self.kind.value, "ref": self.ref, "quote": self.quote}
        if self.retrieved:
            d["retrieved"] = self.retrieved.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Basis":
        from datetime import date as _date
        rt = d.get("retrieved")
        return cls(kind=BasisKind(d["kind"]), ref=d.get("ref", ""),
                   quote=d.get("quote", ""),
                   retrieved=_date.fromisoformat(rt) if rt else None)

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
    corpus=None,
) -> list[str]:
    """逐条核验依据，返回问题列表。空列表 = 全部可核验。

    **corpus 是 EXTERNAL 的核验基准。** 三类依据现在一视同仁:
    FACT 对账本、REPORT 对正文、EXTERNAL 对语料库 —— 引文都必须逐字
    出自代码实际取回的东西。

    corpus 为 None 时保留旧行为（只检查格式）。这不是留后门: WACC 的
    无风险利率等少数几处，URL 由 rates.py 生成、模型碰不到，本来就无从
    捏造。但凡把外部资料交给模型自由引用的地方，都必须传 corpus。"""
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
        elif b.kind is BasisKind.EXTERNAL:
            if corpus is None:
                # 未提供语料库: 已留下 URL、原文与日期供人复核
                continue
            bad = corpus.verify(b.quote, b.ref)
            if bad:
                problems.append(bad)
    return problems
