"""宏观定性 —— 政策取向，以及它到本行业的传导路径。

**补的是宏观分析里此前完全没有的那一半。** GDP、CPI、PPI、PMI、M1−M2、
国债收益率这些**读数**接口早就有了，cycle.py 也能据此定出周期象限。
但「这些读数意味着什么、政策在往哪个方向走、它怎么传到这家公司头上」
一句都没有 —— 读数能定位周期，定不出政策取向，更定不出传导路径。

**传导路径是这一节的重点，不是政策取向本身。** 「货币政策转向宽松」写在
任何一篇研报里都成立，对判断某家白酒公司没有任何帮助。有帮助的是那条
链: 宽松 → 居民收入预期改善 → 商务与宴请场景恢复 → 高端白酒动销回升。
链条写出来，读者才能判断它是否成立；写不出链条的政策判断，等于没写。
因此**传导至少两条**，且每条都要落到「对本行业是利好还是利空」。

**与期限利差交叉印证。** cycle.py 用 10Y−2Y 的形态判政策维度（陡峭=宽松、
平坦或倒挂=收紧）。模型说宽松而利差倒挂，方向就是反的 —— 那要么是模型
错了，要么是利差被别的因素主导，两种情况都该写在正文里而不是掩盖过去。
形态判定复用 cycle.policy_shape，不在这里重写一遍。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from ir_agent.lineparse import keyed, pick
from ir_agent.ledger import FactLedger
from ir_agent.sources.external import attribute
from ir_agent.basis import Basis, InvalidBasis, validate


class MacroViewFailed(Exception):
    """宏观定性判断不可核验 —— 拒绝出结论。"""


class Stance(str, Enum):
    EASING = "宽松"
    NEUTRAL = "中性"
    TIGHTENING = "收紧"


class Impact(str, Enum):
    POSITIVE = "利好"
    NEUTRAL = "中性"
    NEGATIVE = "利空"


MIN_CHANNELS = 2

_STANCE = keyed("政策取向")
_CHANNEL = keyed("传导")
_QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)
_REF = re.compile(r"\[\[[^\]]+\]\]")



def _tail(raw: str, n: int = 400) -> str:
    """失败信息里带上模型最后写了什么。

    **只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。** 前几轮
    排查全跑失败时，每次都要单独再跑一次去抓原始输出 —— 而那几次的根因
    （列表前缀、括号补注、写「无据」）看一眼原文就清楚了。
    """
    raw = (raw or "").strip()
    return raw[-n:] if len(raw) > n else raw

@dataclass
class Channel:
    driver: str          # 政策或宏观事件
    path: str            # 传导路径
    impact: Impact
    bases: list[Basis] = field(default_factory=list)


@dataclass
class MacroView:
    stance: Stance | None = None
    stance_bases: list[Basis] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def bases(self) -> list[Basis]:
        return self.stance_bases

    def summary(self) -> str:
        lines = ["宏观定性"]
        if self.stance:
            lines.append(f"  政策取向：{self.stance.value}")
        lines.append("  传导路径（政策 → 路径 → 对本行业）")
        for c in self.channels:
            lines.append(f"    · {c.driver} → {c.path} → {c.impact.value}")
        for x in self.conflicts:
            lines.append(f"  ⚠ {x}")
        return "\n".join(lines)


def _basis(raw: str, what: str) -> list[Basis]:
    raw = raw.strip()
    ref = _REF.search(raw)
    if ref:
        try:
            return [Basis.fact(ref.group(0))]
        except InvalidBasis as e:
            raise MacroViewFailed(f"{what} 的占位符不合法: {e}") from e
    q = _QUOTE.search(raw)
    if not q:
        raise MacroViewFailed(f"{what} 缺少可核验的依据（需原文引用或占位符）")
    try:
        return [Basis.report(q.group(1))]
    except InvalidBasis as e:
        raise MacroViewFailed(f"{what} 的依据不合法: {e}") from e


def _split(line: str, n: int, what: str) -> list[str]:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < n:
        raise MacroViewFailed(
            f"{what} 字段不足（需 {n} 段，得到 {len(parts)}）：{line}")
    return parts


def parse(text: str) -> MacroView:
    view = MacroView()

    m = _STANCE.search(text)
    if m:
        p = _split(m.group(1), 2, "政策取向")
        got = pick(p[0], [x.value for x in Stance])
        if got is None:
            raise MacroViewFailed(
                "政策取向须为 " + "/".join(x.value for x in Stance)
                + f"，得到 {p[0]!r}")
        view.stance = Stance(got)
        view.stance_bases = _basis(p[1], "政策取向")

    for m in _CHANNEL.finditer(text):
        p = _split(m.group(1), 4, "传导")
        got = pick(p[2], [x.value for x in Impact])
        if got is None:
            raise MacroViewFailed(
                "传导影响须为 " + "/".join(x.value for x in Impact)
                + f"，得到 {p[2]!r}")
        impact = Impact(got)
        view.channels.append(Channel(p[0], p[1], impact, _basis(p[3], "传导")))
    return view


def cross_check(view: MacroView, ledger: FactLedger, as_of: date) -> list[str]:
    """政策取向 vs 期限利差形态。形态判定复用 cycle，不在这里重写。"""
    from ir_agent import cycle

    if view.stance is None or view.stance is Stance.NEUTRAL:
        return []
    got = cycle.policy_shape(ledger, as_of)
    if got is None:
        return []
    shape, implied = got
    if implied == view.stance.value:
        return []
    return [f"判断政策取向为「{view.stance.value}」，而期限利差呈{shape}，"
            f"按利差口径对应「{implied}」—— 方向相反。"
            f"要么判断有误，要么利差被别的因素主导，正文需说明是哪一种"]


def check(view: MacroView, ledger: FactLedger, evidence: str, as_of: date,
          corpus=None) -> None:
    if view.stance is None:
        raise MacroViewFailed("未给出政策取向 —— 「中性」也是判断，须显式说出")
    if len(view.channels) < MIN_CHANNELS:
        raise MacroViewFailed(
            f"传导路径至少 {MIN_CHANNELS} 条 —— 「政策转向宽松」写在哪篇研报里"
            "都成立，对判断本标的没有帮助；有帮助的是那条链")

    from ir_agent.citation import audit_prose

    prose = {"政策取向描述": "", **{f"传导「{c.driver[:12]}」": c.driver + c.path
                                    for c in view.channels}}
    bad = audit_prose(prose)
    if bad:
        raise MacroViewFailed(
            "描述里出现了账本外的数字：" + "、".join(bad) +
            " —— 这些描述会原样进入研报撰写环节，而那里的数字必须可溯源；"
            "请改成不带具体数字的说法，或改用占位符")

    holder = [view] + view.channels
    attribute(holder, evidence, corpus)
    flat = [b for x in holder for b in x.bases]
    problems = validate(flat, ledger=ledger, body=evidence, as_of=as_of,
                        corpus=corpus)
    if problems:
        raise MacroViewFailed("依据不可核验：" + "；".join(problems))


PROMPT = """基于下面的宏观资料，给出政策取向与它到本行业的传导路径。

**一、政策取向**（一条）
格式：`政策取向: <宽松|中性|收紧> | 「原文逐字引用」或 [[占位符]]`

**二、传导路径**（至少两条）
「货币政策转向宽松」写在任何一篇研报里都成立，对判断本标的没有帮助。
有帮助的是那条链：宽松 → 居民收入预期改善 → 商务宴请场景恢复 →
高端白酒动销回升。**写不出链条的政策判断等于没写。**
格式：`传导: <政策或宏观事件> | <传导到本行业的路径> | <利好|中性|利空> | 「原文」或 [[占位符]]`

**硬性要求**
- 引用必须**逐字照抄**下面的资料，一个字都不能改。
- 资料里没讲到的，不要凭印象补。
- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本 —— 一个无处可查的数字会让整篇报告卡在无法修复的驳回上。需要数字时用给你的占位符，否则改成不带数字的说法。
- 你的政策取向会与期限利差形态交叉核对，方向相反时需在正文说明缘由。

{evidence}
"""


def for_prompt(corpus, macro_prompt: str = "") -> str:
    lines = ["宏观定性分析的资料"]
    if macro_prompt:
        lines.append("  可引用的宏观读数（占位符）：")
        lines.append("  " + macro_prompt.replace("\n", "\n  "))
    if corpus is not None and corpus.docs:
        from ir_agent.sources.external import TIER_LABEL, TIER_RULE, Tier
        seen_tiers = {d.tier for d in corpus.docs}
        # 可引用范围要先说清楚，模型才是带着这条界线去读下面的资料 ——
        # 放在资料读完之后才说，等于让它先斩后奏。
        #
        # **按 `Tier` 的声明顺序遍历，不按 `seen_tiers` 这个 set 的迭代
        # 顺序**——set 的迭代顺序取决于 PYTHONHASHSEED，会让「[财经媒体]
        # 的可引用范围：仅作线索，不得作为依据」这条最要紧的规则随机
        # 落在第一/第二/第三条，提示词因此不可复现。与
        # `Corpus.summary()`（sources/external.py）的 `for t in Tier:`
        # 保持一致。
        for t in Tier:
            if t not in seen_tiers:
                continue
            lines.append(f"  [{TIER_LABEL[t]}] 的可引用范围：{TIER_RULE[t]}")
        # **只给 `prompt_docs()`，不是全部 `corpus.docs`。** 公告入库放宽到
        # 20 篇之后，无差别全喂会让提示词变成原先的 2.5 倍、而且三个模块
        # 各一份。上限在这里（消费方）而不在入库处：入库的文档供
        # `gaps._match_announce` 按因子做全文检索，池子越大召回越高。
        # 紧邻的 `seen_tiers` 仍读 `corpus.docs` —— 它算的是「本轮语料库
        # 涉及哪几个分级」，那是语料库的属性，不是提示词给了哪几篇的属性。
        for d in corpus.prompt_docs():
            lines.append(f"  ——【{TIER_LABEL[d.tier]}】{d.cite()}")
            lines.append("  " + d.for_prompt().replace("\n", "\n  "))
        for t in Tier:
            if t not in seen_tiers:
                continue
            lines.append(f"  [{TIER_LABEL[t]}] 的可引用范围：{TIER_RULE[t]}")
    else:
        lines.append("  外部宏观资料：本次未取到 —— 政策取向缺少文字依据，"
                     "只能由读数推断，结论强度应相应说明")
    return "\n".join(lines)


def assess(client, corpus, ledger: FactLedger, as_of: date,
           macro_prompt: str = "", max_attempts: int = 3):
    evidence = for_prompt(corpus, macro_prompt)
    prompt = PROMPT.format(evidence=evidence)
    last, raw = "", ""
    for attempt in range(1, max_attempts + 1):
        out = raw = client(prompt)
        try:
            view = parse(out)
            check(view, ledger, "", as_of, corpus)
        except MacroViewFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后按格式重新输出。\n")
            continue
        view.conflicts = cross_check(view, ledger, as_of)
        return view, evidence
    raise MacroViewFailed(
            f"连续 {max_attempts} 次给不出可核验的宏观定性判断：{last}"
            f"\n最后一次的原始输出（末 400 字）：\n{_tail(raw)}")
