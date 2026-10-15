"""脚本化的假审核者（子项目二测试共用）。

从提示词里读出代码生成的清单（`- [类别] 条目` 行），按给定规则逐条作答。
不调模型。"""
from __future__ import annotations

import re

_ITEM = re.compile(r"^- \[([^\]\s]{1,4})\] (.+)$", re.M)


def items(prompt: str) -> list[tuple[str, str]]:
    # 条目块到下一个「空行 + **标题**」为止（方法论审核与判断项审核共用）。
    head = prompt.split("**必须逐条作答的条目**")[-1].split("\n\n**")[0]
    return _ITEM.findall(head)


class Judge:
    """`levels`：{(类别, 条目) 或 类别: 结论}；没列的答「通过」。可给多轮剧本
    `script=[levels_1, levels_2, ...]`，第 n 次调用用第 n 份（用完沿用最后一份）。"""

    def __init__(self, *script, skip=(), suggest=True, extra=()):
        self.script = list(script) or [{}]
        self.extra = list(extra)       # 原样附在回答末尾的行（如「补充：…」）
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
        return "\n".join(out + self.extra)
