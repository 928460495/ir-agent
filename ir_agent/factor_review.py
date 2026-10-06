"""因子结构审查 —— 对分解式本身做逐项核验。

**为什么要有这一道。** 实测（生益科技 600183，2026-10-06）: 模型给出
「营收 = 覆铜板销量同比增速 × 覆铜板平均售价同比增速 × 高频高速覆铜板收入
同比增速」，销量 × 售价已经是收入，第三项把同一个量又算了一遍。三个各约
20% 的因子相乘得到中性档 71.75%，五年累计 8.05 倍、隐含市占率 39.2%（当前
CR5 才 47.13%）。它过了单位检查、三表勾稽、140 格复算、每股价值量级对账与
人工门 —— 全链路**没有一道闸门检查因子之间是否重复计算**。

**与辩论的分工。** 辩论是开放式的「找出站不住的地方」，擅长不可枚举的问题
（与产能、历史不相容）；本模块是**清单式**的，擅长可枚举的问题（两两正交性、
合成值与理由栏是否自洽）。区别在于: **审核者被迫逐项回答，辩论者自由寻找。**
实测（同一轮）里简报的辩论提了 0 条挑战 —— 自由寻找可以什么都不找，而逐项
回答没有沉默的余地。

**本模块不设闸门。** 它产出的是「结论 + 理由」，进 assumptions.md 给批准人
看，不改任何数值、不中止任何流程。硬闸门（收入量级因子、隐含份额）在
drivers.py 与 assume.py，它们是确定性的；这里补的是规则写不出来的那部分。
唯一的硬性要求是**回答必须覆盖全部条目**，缺项退回重写 —— 否则沉默就等于通过。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable

Client = Callable[[str], str]

# 条目类型。结论词成对出现：前者通过、后者需要人看。
KINDS = {
    "因子": ("可测", "不可测"),
    "正交": ("独立", "重叠"),
    "对账": ("一致", "矛盾"),
}


class ReviewIncomplete(Exception):
    """回答没有覆盖全部条目 —— 沉默不能等于通过。"""


@dataclass(frozen=True)
class Finding:
    kind: str                   # 因子 | 正交 | 对账
    subject: str                # 因子标签，或「A × B」
    verdict: str
    why: str

    @property
    def flagged(self) -> bool:
        """需要人看。**认不出来的结论词也算需要人看** —— 兜底值选最弱的
        那个断言（见 research._mark 那次事故）。"""
        ok = KINDS.get(self.kind, ("",))[0]
        return self.verdict != ok


@dataclass
class ReviewResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def flagged(self) -> list[Finding]:
        return [f for f in self.findings if f.flagged]

    def summary(self) -> str:
        if not self.findings:
            return "因子结构审查：未执行。"
        head = (f"因子结构审查：{len(self.findings)} 项，"
                f"{len(self.flagged)} 项需人工复核")
        if not self.flagged:
            return head + "（全部通过）"
        return "\n".join([head] + [
            f"  ⚠ [{f.kind}] {f.subject}：{f.verdict} —— {f.why.strip()[:110]}"
            for f in self.flagged])


def expected_items(decomps: list) -> list[tuple[str, str]]:
    """代码生成「必须回答的条目」。**清单由代码出，不由模型出** —— 否则
    模型可以通过少列条目来回避问题。

    每个因子一条（可测性）、每一对因子一条（正交性）、每条分解式一条
    （合成值与理由栏对账）。
    """
    out: list[tuple[str, str]] = []
    for d in decomps or []:
        labels = [f.label for f in d.factors]
        out += [("因子", l) for l in labels]
        out += [("正交", f"{a} × {b}") for a, b in combinations(labels, 2)]
        out.append(("对账", d.target))
    return out


_LINE = re.compile(
    r"\[(?P<kind>因子|正交|对账)\]\s*(?P<subject>[^：:]+?)\s*[：:]\s*"
    r"(?P<verdict>\S+?)\s*[　 \t]+(?P<why>.+?)(?=\n\s*\[|\Z)", re.S)


def parse(raw: str, expected: list[tuple[str, str]]) -> list[Finding]:
    """解析回答；缺任何一条则抛 ReviewIncomplete。"""
    got = {}
    for m in _LINE.finditer(raw):
        got[(m.group("kind"), m.group("subject").strip())] = Finding(
            kind=m.group("kind"), subject=m.group("subject").strip(),
            verdict=m.group("verdict").strip(), why=m.group("why").strip())
    missing = [e for e in expected if e not in got]
    if missing:
        raise ReviewIncomplete(
            "以下条目没有给出结论（每一条都必须回答，"
            "沉默不等于通过）：" + "、".join(f"[{k}] {s}" for k, s in missing[:8]))
    return [got[e] for e in expected]


_RULES = """你是研究总监，正在审查一份**驱动因子分解式**本身是否站得住。
你不改任何数值，只给结论与理由 —— 你的意见会原样印在批准清单上给人看。

**逐项回答，每条一行，格式严格如下**（结论与理由之间用全角空格分隔）：

[因子] <因子标签>：可测/不可测　<理由>
[正交] <因子A> × <因子B>：独立/重叠　<理由>
[对账] <分解目标>：一致/矛盾　<理由>

三类各自要判什么：

- **[因子] 可测性**：这个因子有没有公开可查的口径？若它只能靠估计，说明
  它事实上是一个判断而不是一次测量。
- **[正交] 两两重叠**：这两个因子衡量的是不是同一个量的不同说法？
  乘法链里这一点要命 —— 销量 × 售价已经等于收入，若再乘一个「某产品线
  收入增速」，同一个量被算了两遍，两个 20% 会变成 44%，五年复合下来是
  数倍的差距。判「重叠」时请指出重复计算的是哪一个量。
- **[对账] 合成值 vs 理由栏**：把各因子的中性档相乘（或相加）得到的合成值，
  与因子理由栏自己引用的参照（行业增速、历史增速、同业水平）是否自洽？
  实测踩到过: 理由栏写「取接近行业三年 CAGR 12.8% 之上」，而合成结果是
  71.75% —— 是它自己引用的参照的 5.6 倍。

**不要吝惜「重叠」「矛盾」这两个结论。** 这一步的价值在于把问题摆上台面，
而不是在于让清单看起来干净；你给出的每一条都会有人读。
"""


def review(client: Client, decomps: list, checklist: str,
           implied: str = "", max_attempts: int = 2) -> ReviewResult:
    """跑一次结构审查。

    `checklist` 是 `AssumptionSet.describe()` 的因子表部分（含三档与理由），
    `implied` 是算术后果那一行 —— **给审核者的输入必须比作者多**，否则它只是
    同一个模型换个角色再说一遍。实测里真正把问题说穿的那句话是
    「隐含份额 39.2%，而当前 CR5 才 47.13%」，而写出分解式的模型没看过它。
    """
    expected = expected_items(decomps)
    if not expected:
        return ReviewResult()
    items = "\n".join(f"- [{k}] {s}" for k, s in expected)
    base = (f"{_RULES}\n**必须逐条回答的条目**\n{items}\n"
            f"\n**批准清单（三档取值与理由）**\n{checklist}\n"
            + (f"\n**算术后果（代码算出，作者未见过）**\n{implied}\n"
               if implied else ""))
    prompt, last = base, ""
    for _ in range(max_attempts):
        raw = client(prompt)
        try:
            return ReviewResult(findings=parse(raw, expected))
        except ReviewIncomplete as e:
            last = str(e)
            prompt = f"{base}\n---\n**上一次回答不完整**：{last}\n请补全后重新输出全部条目。\n"
    raise ReviewIncomplete(last)
