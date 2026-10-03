"""辩论结论回写 —— 让证伪真正落到正文上。

挑战成立却不改正文，辩论就只是留痕。回写要防两个失败模式:

  · **改了等于没改** —— 模型口头接受却原样保留被质疑的句子。
    因此加机械核验: 成立挑战针对的片段不得在修订稿中原样存在。
    这是可核验的，不依赖模型自述「已修改」。
  · **过度删除** —— 把被质疑处连同可支撑的分析一起铲平，得到一篇
    正确但空洞的报告。因此明确要求: 逻辑错误**修正**，
    无据断言**降级或删除**，不得整段删光。
"""

from __future__ import annotations

import re
from typing import Callable

from ir_agent.analyst import FactCatalog, _violations, strip_preamble
from ir_agent.debate import Challenge

Client = Callable[[str], str]

_WS = re.compile(r"\s+")
_QUOTES = '"\'「」『』“”‘’ '


def _norm(s: str) -> str:
    """比对用的规范化: 去空白、剥引号 —— 质疑者常把原句加引号引用。"""
    return _WS.sub("", s.strip().strip(_QUOTES))


class RevisionFailed(Exception):
    """反复未能落实已成立的挑战。"""


def unaddressed(challenges: list[Challenge], revised: str) -> list[Challenge]:
    """返回**成立但片段仍原样存在**的挑战。空列表 = 都处理了。"""
    body = _norm(revised)
    return [c for c in challenges
            if c.upheld and _norm(c.passage) and _norm(c.passage) in body]


_RULES = """你正在根据研究总监的裁决修订自己的研报正文。

**数字写法不变**：一律使用占位符 [[字段@期间]]，清单外的字段不得引用，
不得写出任何字面数字。

**如何修订**
- 逻辑错误 → **修正**结论，使其与所引数据一致
- 无据断言 → **降级为可支撑的表述，或删除**该断言
- 反面证据被忽略 → 补上该证据并调整结论

**不要整段删光。** 把被质疑处连同可支撑的分析一起铲平，会得到一篇正确但
空洞的报告 —— 保留仍然成立的分析，只改站不住的部分。

只输出修订后的完整正文 Markdown，不要说明你改了什么。
"""


_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)

# 修订稿相对原稿允许的最短长度。章节标题齐全而内容被删光是同一种损失的
# 另一张脸，只查标题会漏掉它；而正当的修订（降级一个无据断言、删掉一句
# 站不住的话）只会让正文略短。两者之间的空隙很宽: 茅台那次只剩原文的
# 26%，而正常修订实测是略微变长（生益科技第二阶段 29.6KB → 31.7KB）。
# 阈值取 0.6 —— 宁可宽也不要在正当修订上误报，一个常响的检查会被学会忽略。
MIN_KEEP = 0.6


def _lost(draft: str, revised: str) -> list[str]:
    """修订稿相对原稿丢掉了什么。空列表 = 没有可观测的损失。

    **这条规则此前只写在提示词里。** `_RULES` 一直有「不要整段删光」，而
    提示词是对模型的请求 —— 最弱的那种执行。实测（贵州茅台 600519 第一
    阶段）: 辩论 2 项成立并回写，简报从八节塌缩成三节、从 23KB 量级只剩
    6KB，保留下来的两节恰好就是那两条挑战的主题。

    两道既有验收都放行了: 截断后的文本完全满足占位符契约（它只是更短）；
    而 `unaddressed` 查的是「被质疑的原句是否仍原样存在」—— **那条检查反过来
    奖励删除**，把整节删掉是通过它最省力的办法。

    章节丢失与正文大幅缩水都是**代码能独立观测的事实**，不必依赖模型自觉。
    """
    out = []
    kept = set(_HEADING.findall(revised))
    gone = [h for h in _HEADING.findall(draft) if h not in kept]
    if gone:
        out.append("丢掉了这些章节：" + "、".join(f"「{h}」" for h in gone))
    if draft.strip() and len(revised) < MIN_KEEP * len(draft):
        out.append(f"正文从 {len(draft)} 字缩到 {len(revised)} 字"
                   f"（不足原文的 {MIN_KEEP:.0%}）")
    return out


def revise_from_verdicts(
    catalog: FactCatalog,
    draft: str,
    challenges: list[Challenge],
    client: Client,
    max_attempts: int = 3,
) -> tuple[str, bool]:
    """按已成立的挑战重写正文。返回 (正文, 是否发生修订)。"""
    upheld = [c for c in challenges if c.upheld]
    if not upheld:
        return draft, False

    listed = "\n".join(
        f"[{i}] 类型：{c.kind.value}\n    原文：{c.passage}\n    裁决理由：{c.rationale or c.objection}"
        for i, c in enumerate(upheld, 1))
    base = (f"{_RULES}\n**可用事实**\n{catalog.render_for_prompt()}\n"
            f"\n**原正文**\n{draft}\n\n**已成立的挑战（必须逐条落实）**\n{listed}\n")

    prompt, last_open, last_bad, last_lost = base, [], [], []
    for _ in range(max_attempts):
        out = strip_preamble(client(prompt))
        last_bad = _violations(out, catalog)
        last_open = unaddressed(upheld, out) if not last_bad else []
        last_lost = _lost(draft, out)
        if not last_bad and not last_open and not last_lost:
            return out, True

        notes = [f"- {v}" for v in last_bad]
        notes += [f"- 挑战「{c.passage[:40]}」未处理：该句仍原样出现在修订稿中。"
                  for c in last_open]
        # 驳回信息必须说清怎么改才能通过，否则同一条驳回会被原样重试到底
        # （与 basis.py 的 _REPORT_GUIDANCE 同一条教训）。只说「不许删」
        # 模型无从定位，这里点名丢掉的章节。
        notes += [f"- {x}。请返回**完整正文**：保留原有的全部章节与仍然"
                  f"成立的分析，只改站不住的那几句。" for x in last_lost]
        prompt = (f"{base}\n---\n**上一稿被驳回：**\n" + "\n".join(notes)
                  + "\n请据此重写全文。\n")

    detail = "；".join([*(v for v in last_bad),
                        *(c.passage[:40] for c in last_open),
                        *last_lost])
    raise RevisionFailed(f"修订反复未达要求：{detail}")
