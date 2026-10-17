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
from ir_agent.basis import Basis, validate
from ir_agent import qual_review as Q


class MacroViewFailed(Exception):
    """宏观定性没有可用资料等无从进行的情形。**内容是否成立不再由代码驳回**
    （子项目四，spec 2026-10-17）：交给审核者，审核不过抛 `qual_review.ReviewStop`。"""


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




@dataclass
class Channel:
    driver: str          # 政策或宏观事件
    path: str            # 传导路径
    impact: Impact
    bases: list[Basis] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)    # 转述指向的文章标题


@dataclass
class MacroView:
    stance: Stance | None = None
    stance_bases: list[Basis] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    # 子项目四：代码读数时的事实（读不出的行……）、审核结论、各轮记录。不进 summary
    # 以外的地方，读回时由 full 重建。
    read_notes: list[str] = field(default_factory=list)
    review: object = None
    rounds: list = field(default_factory=list)

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
        lines += Q.review_tail(self.review)
        return "\n".join(lines)






def _cells(line: str) -> list[str]:
    return [p.strip() for p in line.split("|")]


def parse(text: str, corpus=None) -> MacroView:
    """读数。**只读，不驳回**（子项目四）：段数不够、取向/影响不是规定的词的行跳过，
    依据的问题照读 —— 都记进 `read_notes`，作为事实交给审核者。"""
    view = MacroView()
    m = _STANCE.search(text)
    if m:
        p = _cells(m.group(1))
        got = pick(p[0], [x.value for x in Stance])
        if len(p) < 2:
            view.read_notes.append(f"政策取向一行字段不足（需 2 段），没读进来：{m.group(1)}")
        elif got is None:
            view.read_notes.append(
                f"政策取向「{p[0]}」不是 " + "/".join(x.value for x in Stance)
                + " 之一，没读进来")
        else:
            view.stance = Stance(got)
            bases, prob, cites = Q.basis(" | ".join(p[1:]), corpus)
            view.stance_bases = bases
            if prob:
                view.read_notes.append(f"政策取向的依据：{prob}")
    for m in _CHANNEL.finditer(text):
        p = _cells(m.group(1))
        if len(p) < 4:
            view.read_notes.append(f"传导一行字段不足（需 4 段），没读进来：{m.group(1)}")
            continue
        got = pick(p[2], [x.value for x in Impact])
        if got is None:
            view.read_notes.append(
                f"传导「{p[0]}」的影响「{p[2]}」不是 " + "/".join(x.value for x in Impact)
                + " 之一，没读进来")
            continue
        bases, prob, cites = Q.basis(" | ".join(p[3:]), corpus)
        if prob:
            view.read_notes.append(f"传导「{p[0]}」的依据：{prob}")
        view.channels.append(Channel(p[0], p[1], Impact(got), bases, cites))
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


def facts(view: MacroView, ledger: FactLedger, as_of: date, corpus=None) -> list[str]:
    """代码核对出的事实，交审核者（子项目四：此前这些是驳回理由）。"""
    out: list[str] = []
    if view.stance is None:
        out.append("没有给出政策取向（「中性」也是判断，方法论要求显式说出）")
    if len(view.channels) < MIN_CHANNELS:
        out.append(f"传导路径只读到 {len(view.channels)} 条（方法论要求至少 "
                   f"{MIN_CHANNELS} 条：写不出链条的政策判断等于没写）")
    from ir_agent.citation import audit_prose

    bad = audit_prose({f"传导「{c.driver[:12]}」": c.driver + c.path
                       for c in view.channels})
    if bad:
        out.append("描述里有账本外的数字：" + "、".join(bad))
    holder = [view] + view.channels
    attribute(holder, "", corpus)
    flat = [b for x in holder for b in x.bases]
    out += [f"依据核验不过：{p}" for p in validate(flat, ledger=ledger, body="",
                                                  as_of=as_of, corpus=corpus)]
    out += [f"与期限利差交叉核对：{x}" for x in cross_check(view, ledger, as_of)]
    return out


KINDS = ("取向", "传导", "遗漏")


def checklist(view: MacroView) -> list[tuple[str, str]]:
    """按方法论生成（用户决定 5）：取向一条、每条传导一条（一条没读进来时列「传导路径
    （未给出）」）、遗漏一条。"""
    items = [("取向", "政策取向")]
    items += [("传导", f"第{i}条 {c.driver}") for i, c in enumerate(view.channels, 1)] \
        or [("传导", "传导路径（未给出）")]
    return items + [("遗漏", "宏观因素")]


RULES = Q.make_rules("宏观定性", """\
- 取向：宽松/中性/收紧与证据是否一致；与账本里的利率、信用数据（如期限利差形态，代码
  给了交叉核对结果）是否矛盾。没给出政策取向也在这条判。
- 传导：链条每一环是否成立、是否真的传到本行业；利好/中性/利空与链条方向是否一致；
  依据能否支撑。读到的传导少于两条时，在「传导路径（未给出）」或任一条里指出。
- 遗漏：有没有漏掉对本行业最要紧的宏观因素。""")


PROMPT = """基于下面的宏观资料，给出政策取向与它到本行业的传导路径。

**一、政策取向**（一条）
格式：`政策取向: <宽松|中性|收紧> | <依据>`

**二、传导路径**（至少两条）
「货币政策转向宽松」写在任何一篇研报里都成立，对判断本标的没有帮助。
有帮助的是那条链：宽松 → 居民收入预期改善 → 商务宴请场景恢复 →
高端白酒动销回升。**写不出链条的政策判断等于没写。**
格式：`传导: <政策或宏观事件> | <传导到本行业的路径> | <利好|中性|利空> | <依据>`

{basis_rules}

**要求**
- 资料里没讲到的，不要凭印象补。
- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本。需要数字时用给你的占位符，否则改成不带数字的说法。
- 你的政策取向会与期限利差形态交叉核对，方向相反时需说明缘由。

{review_note}

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
           macro_prompt: str = "", *, judge, catalog: str = "",
           max_rounds: int = Q.MAX_ROUNDS):
    """撰写者写、审核者审（子项目四）。返回 (结论, 证据文本)；审核不过抛
    `qual_review.ReviewStop`，模型接口错误原样抛出。"""
    evidence = for_prompt(corpus, macro_prompt)
    prompt = PROMPT.format(evidence=evidence, basis_rules=Q.BASIS_RULES,
                           review_note=Q.REVIEW_NOTE)
    view, res, rounds = Q.run(
        client, judge, name="宏观定性", prompt=prompt, materials=evidence,
        read=lambda raw: parse(raw, corpus),
        facts=lambda v: facts(v, ledger, as_of, corpus),
        checklist=checklist, kinds=KINDS, rules=RULES, catalog=catalog,
        hay=evidence, ledger=ledger, as_of=as_of, max_rounds=max_rounds)
    view.conflicts = cross_check(view, ledger, as_of)
    view.review, view.rounds = res, rounds
    return view, evidence
