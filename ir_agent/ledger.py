"""事实账本 —— 系统里所有数字的唯一入口。

两条不可妥协的规则:
  1. 数字一律用 Decimal，不用 float。财务数据的精度问题不是理论风险。
  2. 每次取值都要过 as_of 时点守卫。拿不到 as_of 就拿不到数字。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class LookAheadError(Exception):
    """请求了一个在该时点尚未披露的事实 —— 回测里的未来函数。"""


class Method(str, Enum):
    REPORTED = "reported"    # 直接来自公告/官方接口
    COMPUTED = "computed"    # 由确定性算子从其它事实算出
    EXTRACTED = "extracted"  # 由 LLM 从非结构化文本抽取，必须复核
    ESTIMATED = "estimated"  # 第三方估算值，如已停止披露的口径


@dataclass(frozen=True, slots=True)
class Fact:
    key: str
    value: Decimal
    unit: str
    period: str
    as_of: date
    source_id: str
    method: Method
    currency: str | None = None
    confidence: float = 1.0
    derived_from: tuple[str, ...] = field(default=())
    basis: str | None = None      # "parent" | "total" —— 归母口径还是全口径

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError(
                f"{self.key}: value must be Decimal, got {type(self.value).__name__}. "
                "浮点数会在财务计算里累积误差。"
            )
        if self.method in (Method.EXTRACTED, Method.ESTIMATED) and self.confidence >= 1.0:
            raise ValueError(
                f"{self.key}: method={self.method.value} 必须声明 confidence < 1.0，"
                "以便下游复核与降权。"
            )

    @property
    def ref(self) -> str:
        """研报正文里使用的占位符。LLM 只写这个，不写数字。"""
        return f"[[{self.source_id}#{self.key}@{self.period}]]"


class FactLedger:
    """按 (key, period) 保存多个版本；取值时按 as_of 选出当时可见的最新版本。"""

    def __init__(self) -> None:
        self._facts: dict[tuple[str, str], list[Fact]] = {}

    def put(self, fact: Fact) -> Fact:
        bucket = self._facts.setdefault((fact.key, fact.period), [])
        bucket.append(fact)
        bucket.sort(key=lambda f: f.as_of)
        return fact

    def get(self, key: str, period: str, as_of: date) -> Fact:
        bucket = self._facts.get((key, period))
        if not bucket:
            raise KeyError(f"账本中没有 {key}@{period}")

        visible = [f for f in bucket if f.as_of <= as_of]
        if not visible:
            earliest = bucket[0].as_of
            raise LookAheadError(
                f"{key}@{period} 最早于 {earliest.isoformat()} 披露，"
                f"但请求时点为 {as_of.isoformat()} —— 使用它将构成未来函数。"
            )
        return visible[-1]

    def pin(self, as_of: date) -> PinnedLedger:
        """固定时点，调用方就不可能忘记传 as_of。"""
        return PinnedLedger(self, as_of)

    def __len__(self) -> int:
        return sum(len(v) for v in self._facts.values())


class PinnedLedger:
    def __init__(self, ledger: FactLedger, as_of: date) -> None:
        self._ledger = ledger
        self.as_of = as_of

    def get(self, key: str, period: str) -> Fact:
        return self._ledger.get(key, period, as_of=self.as_of)

    def put(self, fact: Fact) -> Fact:
        return self._ledger.put(fact)
