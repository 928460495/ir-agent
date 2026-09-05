"""跳过原因分类。

「没做」有三种，严重性差着数量级:
  - 数据本就没披露（CAS 利润表无毛利行）—— 正常
  - 口径不适用（负盈利不产生 PE）—— 正常
  - 我们没抓到（分页截断、接口失败）—— **故障**

V0 早期把三者混成一个字符串列表，结果温氏股份因分页截断丢掉同比，
显示得和「CAS 无毛利行」一模一样。这个类就是为了让它们不再长一个样。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SkipReason(Enum):
    DATA_NOT_DISCLOSED = "data_not_disclosed"   # 上游根本没有这个数
    NOT_APPLICABLE = "not_applicable"           # 口径本身不成立
    FETCH_INCOMPLETE = "fetch_incomplete"       # 我们没抓全 —— 故障

    @property
    def is_defect(self) -> bool:
        return self is SkipReason.FETCH_INCOMPLETE

    @property
    def label(self) -> str:
        return {
            SkipReason.DATA_NOT_DISCLOSED: "未披露",
            SkipReason.NOT_APPLICABLE: "口径不适用",
            SkipReason.FETCH_INCOMPLETE: "抓取不全",
        }[self]


@dataclass(frozen=True)
class Skip:
    what: str
    reason: SkipReason
    detail: str = ""

    def describe(self) -> str:
        tail = f"（{self.detail}）" if self.detail else ""
        return f"{self.what} · {self.reason.label}{tail}"


@dataclass
class SkipLog:
    entries: list[Skip] = field(default_factory=list)

    def add(self, what: str, reason: SkipReason, detail: str = "") -> None:
        if any(e.what == what for e in self.entries):
            return
        self.entries.append(Skip(what, reason, detail))

    @property
    def defects(self) -> list[Skip]:
        return [e for e in self.entries if e.reason.is_defect]

    @property
    def benign(self) -> list[Skip]:
        return [e for e in self.entries if not e.reason.is_defect]

    @property
    def has_defects(self) -> bool:
        return bool(self.defects)

    def summary(self) -> str:
        if not self.entries:
            return ""
        lines: list[str] = []
        if self.defects:
            lines.append("  ✗ 因抓取不全而缺失（属故障，需处理）:")
            lines += [f"      {e.describe()}" for e in self.defects]
        if self.benign:
            lines.append("  – 正常跳过:")
            lines += [f"      {e.describe()}" for e in self.benign]
        return "\n".join(lines)
