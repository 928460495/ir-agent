"""多空辩论 —— 范围收窄到纯基本面后，唯一的系统性证伪机制。

**辩论的价值在证伪，不在多产出散文。** 所以这里不做「多头再夸一遍」，
而是三方: 质疑者提出挑战 → 分析师回应 → 裁决者判定哪些成立。

挑战必须**指向原文的具体片段**并归类，否则「风险依然存在」这类空话会淹没
真问题。实测两类才是该抓的:

  · 逻辑错误 —— 茅台正文写「净利润降幅小于营收降幅」，实际是大于
  · 无据断言 —— 招行正文写「历史周期中常见的两位数水平」，事实清单里没有

第二类尤其重要: 占位符契约拦得住编造的**数字**，拦不住编造的**背景**。

轮次由计数器控制，不问模型「够了吗」（见架构手册原理 4）。
占位符契约对所有参与者一视同仁 —— 质疑者与裁决者同样不得写裸数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ir_agent.analyst import FactCatalog, _violations

Client = Callable[[str], str]

_CHALLENGE = re.compile(
    r"\[挑战\s*(\d+)\]\s*类型[：:]\s*(?P<kind>[^\n]+)\n"
    r"(?:原文[：:]\s*(?P<passage>[^\n]+)\n)?"
    r"质疑[：:]\s*(?P<body>.+?)(?=\n\s*\[挑战|\Z)",
    re.S)
# 结构标记 [挑战 1] / [裁决 2] 里的编号是**格式**不是内容，
# 审计裸数字前必须先剥掉 —— 否则每一稿都会因自己的编号被驳回。
_STRUCT = re.compile(r"\[(?:挑战|裁决)\s*\d+\]")
_VERDICT = re.compile(r"\[裁决\s*(\d+)\]\s*(?P<stand>成立|不成立)\s*(?P<why>.*?)"
                      r"(?=\n\s*\[裁决|\Z)", re.S)


class DebateRefused(Exception):
    """参与者反复违反占位符契约，中止辩论。"""


class ChallengeKind(str, Enum):
    LOGIC = "逻辑错误"
    UNSOURCED = "无据断言"
    IGNORED = "反面证据被忽略"
    OTHER = "其他"


@dataclass
class Challenge:
    index: int
    kind: ChallengeKind
    passage: str
    objection: str
    response: str = ""
    verdict: str = ""          # "成立" | "不成立"
    rationale: str = ""

    @property
    def upheld(self) -> bool:
        return self.verdict == "成立"


@dataclass
class DebateResult:
    challenges: list[Challenge] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    rounds: int = 0

    @property
    def upheld_count(self) -> int:
        return sum(1 for c in self.challenges if c.upheld)

    def summary(self) -> str:
        if not self.challenges:
            return "多空辩论：质疑者未提出可核验的挑战。"
        lines = [f"多空辩论：{len(self.challenges)} 项挑战，"
                 f"{self.upheld_count} 项成立（{self.rounds} 轮）"]
        for c in self.challenges:
            mark = "✗ 成立" if c.upheld else "· 不成立" if c.verdict else "· 未裁决"
            lines.append(f"  {mark}　[{c.kind.value}] {c.passage[:34]}")
            if c.upheld and c.rationale:
                lines.append(f"        {c.rationale.strip()[:70]}")
        return "\n".join(lines)


def parse_challenges(raw: str) -> list[Challenge]:
    """解析质疑者输出。**不指向具体原文片段的挑战直接丢弃** —— 无法核验。"""
    out: list[Challenge] = []
    for m in _CHALLENGE.finditer(raw):
        passage = (m.group("passage") or "").strip()
        if not passage:
            continue
        kind_text = m.group("kind").strip()
        kind = next((k for k in ChallengeKind if k.value in kind_text),
                    ChallengeKind.OTHER)
        out.append(Challenge(index=int(m.group(1)), kind=kind,
                             passage=passage, objection=m.group("body").strip()))
    return out


def should_continue(round_no: int, max_rounds: int, open_count: int) -> bool:
    """轮次由计数器决定，不问模型。没有待议挑战时提前收敛。"""
    return round_no < max_rounds and open_count > 0


_RULES = """本轮为内部研究评审。数字一律写占位符 [[字段@期间]]，
清单外的字段不得引用，**不得写出任何字面数字**。
"""

_CHALLENGER = _RULES + """
你的角色是**质疑者**。目标不是唱反调，是找出正文中**站不住的地方**。

只提**可核验**的挑战，每条必须指向正文的具体原句。优先找这三类：
  · 逻辑错误 —— 结论与所引数据的方向或大小关系矛盾
  · 无据断言 —— 用了「可用事实」里没有的信息（历史增速、行业排名、市占率等）
  · 反面证据被忽略 —— 清单里有对结论不利的数字而正文回避了

严格按以下格式，每条一段；确实找不到问题就只回复「正文未见明显问题。」

[挑战 1] 类型：逻辑错误
原文：（原封不动引用被质疑的那一句）
质疑：（为什么站不住，需要时引用占位符）
"""

_RESPONDER = _RULES + """
你的角色是**原分析师**。对每条挑战逐一回应：接受并说明如何改，
或据理反驳。不要含糊其辞 —— 「已注意到该风险」不是回应。
"""

_JUDGE = _RULES + """
你的角色是**研究总监**，就每条挑战作出裁决。只看证据，不和稀泥。

严格按以下格式，每条一行：

[裁决 1] 成立　（理由）
[裁决 2] 不成立　（理由）
"""


def _ask(client: Client, prompt: str, catalog: FactCatalog,
         max_attempts: int) -> str:
    """调用并强制占位符契约 —— 违规驳回重写，用尽则中止辩论。"""
    p, last = prompt, []
    for _ in range(max_attempts):
        out = client(p)
        audited = _STRUCT.sub("[]", out)
        last = [v for v in _violations(audited, catalog)
                if "没有引用任何事实占位符" not in v]   # 质疑可以不含数字
        if not last:
            return out
        p = (f"{prompt}\n\n---\n**上一稿被驳回，原因：**\n"
             + "\n".join(f"- {v}" for v in last) + "\n请据此重写。\n")
    raise DebateRefused(f"参与者反复违反占位符契约：{'；'.join(last)}")


def run_debate(
    catalog: FactCatalog,
    draft: str,
    client: Client,
    max_rounds: int = 2,
    max_attempts: int = 3,
) -> DebateResult:
    facts = catalog.render_for_prompt()
    ctx = f"\n**可用事实**\n{facts}\n\n**待评审正文**\n{draft}\n"

    result = DebateResult()
    raw = _ask(client, _CHALLENGER + ctx, catalog, max_attempts)
    result.transcript.append(("质疑者", raw))
    result.challenges = parse_challenges(raw)
    result.rounds = 1
    if not result.challenges:
        return result

    listed = "\n".join(
        f"[挑战 {c.index}] 类型：{c.kind.value}\n原文：{c.passage}\n质疑：{c.objection}"
        for c in result.challenges)

    resp = _ask(client, _RESPONDER + ctx + f"\n**挑战**\n{listed}\n",
                catalog, max_attempts)
    result.transcript.append(("分析师", resp))

    verdicts = _ask(client, _JUDGE + ctx + f"\n**挑战**\n{listed}\n"
                    f"\n**分析师回应**\n{resp}\n", catalog, max_attempts)
    result.transcript.append(("研究总监", verdicts))

    by_idx = {int(m.group(1)): m for m in _VERDICT.finditer(verdicts)}
    for c in result.challenges:
        m = by_idx.get(c.index)
        if m:
            c.response = resp
            c.verdict = m.group("stand")
            c.rationale = m.group("why").strip()
    return result
