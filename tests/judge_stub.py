"""脚本化的假审核者（子项目二测试共用）。

从提示词里读出代码生成的清单（`- [类别] 条目` 行），按给定规则逐条作答。
不调模型。"""
from __future__ import annotations

import re

_ITEM = re.compile(r"^- \[(模式|因子|正交|公式|常量|账本|覆盖)\] (.+)$", re.M)


def items(prompt: str) -> list[tuple[str, str]]:
    head = prompt.split("**必须逐条作答的条目**")[-1].split("**提出者给的分解式（原文）**")[0]
    return _ITEM.findall(head)


class Judge:
    """`levels`：{(类别, 条目) 或 类别: 结论}；没列的答「通过」。可给多轮剧本
    `script=[levels_1, levels_2, ...]`，第 n 次调用用第 n 份（用完沿用最后一份）。"""

    def __init__(self, *script, skip=(), suggest=True):
        self.script = list(script) or [{}]
        self.skip = set(skip)          # 故意漏答的条目
        self.suggest = suggest         # 致命时附「建议：…」（规则要求必写）
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        levels = self.script[min(len(self.prompts), len(self.script)) - 1]
        out = []
        for k, s in items(prompt):
            if (k, s) in self.skip:
                continue
            lv = levels.get((k, s), levels.get(k, "通过"))
            why = "" if lv == "通过" else f"理由：{k}{s}"
            if lv == "致命" and self.suggest:
                why += f"　建议：改{k}{s}"
            out.append(f"[{k}] {s}：{lv}　{why}".rstrip())
        return "\n".join(out)
