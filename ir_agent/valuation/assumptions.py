"""估值假设与人工审核门。

**审核状态是前置条件，不是元数据。** 未审核时估值直接拒绝执行，而不是
算完再标注「仅供参考」—— 后者的结论一旦出现在文档里就会被人引用。

两条配套约束:

  · **每项假设必须带依据。** 审核者面对一个孤零零的 12% 无从判断；
    没有依据的数字与凭空捏造无异。
  · **假设被修改后审核自动失效。** 否则「审核过的假设」会在悄悄改动后
    继续挂着通过标记 —— 这比没有审核更危险。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

# 永续增长率上限。高于长期名义 GDP 增速意味着公司最终吞掉整个经济。
MAX_TERMINAL_GROWTH = Decimal("0.06")

REQUIRED_BASIS = ("wacc", "terminal_growth", "growth_rates")


class UnsupportedAssumption(Exception):
    """假设缺少依据说明。"""


class NotReviewed(Exception):
    """假设未经人工审核，拒绝出具估值。"""


@dataclass(frozen=True)
class Assumptions:
    wacc: Decimal
    terminal_growth: Decimal
    growth_rates: list[Decimal]
    basis: dict[str, str] = field(default_factory=dict)
    reviewed_by: str | None = None
    reviewed_at: date | None = None

    def __post_init__(self) -> None:
        missing = [k for k in REQUIRED_BASIS
                   if not str(self.basis.get(k, "")).strip()]
        if missing:
            raise UnsupportedAssumption(
                f"以下假设缺少依据说明：{'、'.join(missing)}。"
                "审核者面对一个孤零零的数字无从判断 —— 依据是审核的前提。"
            )
        if self.wacc <= 0:
            raise ValueError(f"WACC 必须为正，得到 {self.wacc}。")
        if self.terminal_growth >= self.wacc:
            raise ValueError(
                f"永续增长率 {self.terminal_growth} 不得高于 WACC {self.wacc}，"
                "否则终值发散。")
        if self.terminal_growth > MAX_TERMINAL_GROWTH:
            raise ValueError(
                f"永续增长率 {self.terminal_growth} 高于长期名义 GDP 增速上限 "
                f"{MAX_TERMINAL_GROWTH} —— 这意味着公司最终吞掉整个经济。")
        if not self.growth_rates:
            raise ValueError("预测期增速为空，至少需要一年。")

    @property
    def approved(self) -> bool:
        return self.reviewed_by is not None and self.reviewed_at is not None

    def approve(self, reviewer: str, when: date) -> "Assumptions":
        """返回**新对象** —— 原对象保持未审核，便于留痕对比。"""
        if not reviewer.strip():
            raise ValueError("审核人不能为空。")
        return replace(self, reviewed_by=reviewer, reviewed_at=when)

    def revise(self, basis_note: str, **changes) -> "Assumptions":
        """修改假设并**自动撤销审核** —— 改过就必须重审。"""
        if not basis_note.strip():
            raise UnsupportedAssumption("修改假设必须说明理由。")
        new_basis = dict(self.basis)
        for k in changes:
            new_basis[k] = f"{new_basis.get(k, '')}；{basis_note}".lstrip("；")
        return replace(self, **changes, basis=new_basis,
                       reviewed_by=None, reviewed_at=None)

    def summary(self) -> str:
        state = (f"已审核（{self.reviewed_by} · {self.reviewed_at}）"
                 if self.approved else "**未审核 —— 不得据此出具估值结论**")
        lines = [f"估值假设：{state}",
                 f"  WACC {self.wacc:.2%}　依据：{self.basis['wacc']}",
                 f"  永续增长 {self.terminal_growth:.2%}　依据：{self.basis['terminal_growth']}",
                 f"  预测期 {len(self.growth_rates)} 年，增速 "
                 + "、".join(f"{g:.1%}" for g in self.growth_rates),
                 f"    依据：{self.basis['growth_rates']}"]
        return "\n".join(lines)
