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
    basis: dict[str, list] = field(default_factory=dict)   # key -> [Basis]
    reviewed_by: str | None = None
    reviewed_at: date | None = None

    def __post_init__(self) -> None:
        missing = [k for k in REQUIRED_BASIS if not self.basis.get(k)]
        if missing:
            raise UnsupportedAssumption(
                f"以下假设缺少依据：{'、'.join(missing)}。"
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

    def approve(self, reviewer: str, when: date, *,
                ledger=None, body: str | None = None,
                as_of: date | None = None) -> "Assumptions":
        """核验依据后返回**新对象** —— 原对象保持未审核，便于留痕对比。

        必须同时给出账本与正文作为证据: 不给证据就不能审核，
        否则契约又退回成走过场。
        """
        from ir_agent.valuation.basis import validate

        if not reviewer.strip():
            raise ValueError("审核人不能为空。")
        if ledger is None or body is None or as_of is None:
            raise UnsupportedAssumption(
                "审核必须同时提供证据（ledger / body / as_of）才能核验依据 —— "
                "不核验的审核就是走过场。")

        problems: list[str] = []
        for key, bases in self.basis.items():
            problems += [f"[{key}] {p}" for p in
                         validate(list(bases), ledger, body, as_of)]
        if problems:
            raise UnsupportedAssumption(
                "依据核验未通过，拒绝审核：\n  " + "\n  ".join(problems))

        return replace(self, reviewed_by=reviewer, reviewed_at=when)

    def revise(self, basis_note: str, basis: dict[str, list] | None = None,
               **changes) -> "Assumptions":
        """修改假设并**自动撤销审核**。

        改了取值就必须给出新依据 —— 原依据是为旧数字写的，未必还成立。
        沿用旧依据会让「已核验」的标记跟着一个新数字继续挂着，
        比没有审核更危险。
        """
        if not basis_note.strip():
            raise UnsupportedAssumption("修改假设必须说明理由。")
        new_basis = dict(self.basis)
        supplied = basis or {}
        missing = [k for k in changes
                   if k in REQUIRED_BASIS and not supplied.get(k)]
        if missing:
            raise UnsupportedAssumption(
                f"修改了 {'、'.join(missing)} 但未提供新依据 —— "
                "原依据是为旧数字写的，不能顺延。")
        new_basis.update(supplied)
        return replace(self, **changes, basis=new_basis,
                       reviewed_by=None, reviewed_at=None)

    def summary(self) -> str:
        state = (f"已审核（{self.reviewed_by} · {self.reviewed_at}）"
                 if self.approved else "**未审核 —— 不得据此出具估值结论**")
        lines = [f"估值假设：{state}",
                 f"  WACC {self.wacc:.2%}",
                 *[f"    依据：{b.describe()}" for b in self.basis.get("wacc", [])],
                 f"  永续增长 {self.terminal_growth:.2%}",
                 *[f"    依据：{b.describe()}"
                   for b in self.basis.get("terminal_growth", [])],
                 f"  预测期 {len(self.growth_rates)} 年，增速 "
                 + "、".join(f"{g:.1%}" for g in self.growth_rates),
                 *[f"    依据：{b.describe()}"
                   for b in self.basis.get("growth_rates", [])]]
        return "\n".join(lines)
