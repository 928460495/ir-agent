"""驱动因子分解式 —— 「这个行业靠什么赚钱」的可计算表达。

**闭合是硬约束，不是精度问题。** 「营收 = 产能 × 稼动率 × 单价」闭合，
三项能乘回营收；「营收 = 产能 × 品牌力」不闭合 —— 它是个比喻。一旦代码
要从因子合成营收增速，不闭合的分解式就根本算不出来。

而 AI 很容易产出听着专业但不闭合的分解:「产能 × 稼动率 × 单价 × 客户结构
优化」前三项能乘、第四项不能。**要求每个因子声明单位，写不出单位的项就
写不进来** —— 这是这里真正的闸门。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Op(str, Enum):
    MULTIPLY = "乘"      # 营收增速 = Π(1 + 因子变化率) − 1
    ADD_PP = "加"        # 毛利率   = 基期 + Σ 因子贡献(pp)


# 单位白名单**只有两个值**，与 op 严格绑定。
#
# pp 不得进乘法链: 稼动率 85%→82% 是 −3pp，但作为乘法因子它的变化率是
# 82/85 − 1 = −3.5%。两者不等；混用会让合成结果系统性偏错，而偏差不大、
# 不容易被发现 —— 正是那种会一直藏着的错。
UNITS: dict[Op, str] = {Op.MULTIPLY: "变化率", Op.ADD_PP: "pp"}

TARGETS: dict[str, Op] = {"revenue": Op.MULTIPLY, "gross_margin": Op.ADD_PP}
TARGET_LABEL = {"revenue": "营收", "gross_margin": "毛利率"}

MIN_FACTORS = 2      # 一个因子不叫分解
MAX_FACTORS = 5      # 超过五个人核实不过来


class DecompositionInvalid(Exception):
    """分解式不闭合 —— 代码算不出来，拒绝往下走。"""


@dataclass(frozen=True)
class Factor:
    key: str            # capacity | utilization | asp
    label: str          # 产能 / 稼动率 / 单价
    unit: str           # 必须是 UNITS 里的值
    source_hint: str = ""   # 这个数去哪找


@dataclass
class Decomposition:
    target: str                 # revenue | gross_margin
    op: Op
    factors: list[Factor] = field(default_factory=list)

    def describe(self) -> str:
        sep = " × " if self.op is Op.MULTIPLY else " + "
        body = sep.join(f.label for f in self.factors)
        head = TARGET_LABEL.get(self.target, self.target)
        return f"{head} = {body}" if self.op is Op.MULTIPLY \
            else f"{head} = 基期 + {body}"


def check_closure(d: Decomposition) -> None:
    """闭合校验。不合法抛 DecompositionInvalid，**不返回布尔值** ——
    调用方拿到的必须是「为什么不行」，不是一个 False。"""
    want_op = TARGETS.get(d.target)
    if want_op is None:
        raise DecompositionInvalid(
            f"未知的分解目标 {d.target!r}，只能是："
            + "、".join(TARGETS))
    if d.op is not want_op:
        raise DecompositionInvalid(
            f"{d.target} 只能用 {want_op.value}（{UNITS[want_op]}），"
            f"得到 {d.op.value}")

    n = len(d.factors)
    if n < MIN_FACTORS:
        raise DecompositionInvalid(
            f"至少 {MIN_FACTORS} 个因子，得到 {n} —— 一个因子不叫分解")
    if n > MAX_FACTORS:
        raise DecompositionInvalid(
            f"至多 {MAX_FACTORS} 个因子，得到 {n} —— "
            "分解的价值在于人能检查，不在于穷举")

    want_unit = UNITS[d.op]
    seen: set[str] = set()
    for f in d.factors:
        if f.unit != want_unit:
            raise DecompositionInvalid(
                f"因子「{f.label}」的单位是 {f.unit!r}，"
                f"而 {d.op.value} 法链只接受 {want_unit!r}。"
                + ("pp 不能进乘法链 —— 稼动率 85%→82% 是 −3pp，"
                   "但乘法因子的变化率是 −3.5%。"
                   if want_unit == "变化率" else
                   "比率的变化只能加减，不能相乘。"))
        if f.key in seen:
            raise DecompositionInvalid(f"因子 key 重复：{f.key}")
        seen.add(f.key)
