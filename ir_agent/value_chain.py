"""产业链与行业真实规模 —— 行业分析里此前完全缺失的定性部分。

**在此之前，「行业分析」实际上只是「A 股同行业上市公司报表聚合」。**
行业规模 = 上市公司营收之和，市占率 = 本公司营收 / 该合计。这个口径有个
系统性偏差: 真实行业里还有大量非上市企业，分母被系统性低估，市占率被
系统性高估。而产业链结构、公司在链条中的位置，则是**一点都没有**。

这一节要补的正是那部分:

  · **完整产业链**: 上游 → 本环节 → 下游，每一环都要有出处
  · **公司所处环节**，以及由此决定的议价能力
  · **行业真实规模**（若外部资料给出），与上市公司口径并列，让读者看到差距

**为什么产业链比应付/应收天数更根本。** 五力里的供应商与买方议价，此前
只有应付天数、应收天数作锚点 —— 那是**结果**，不是原因。为什么这家公司
能占用上游资金？因为它的上游是分散的种植户，而它是少数几个大买家之一。
产业链结构才是那个原因，也才是判断这种优势能否持续的依据。

**交叉印证只用同业中位数作分界，不另设阈值。** 说「对上游议价强」，那么
应付天数应当高于同业中位数；低于中位数就是与数字相反，值得写出来。中位
数是定义上的分界（比典型同业更好还是更差），不是我编的数。至于「相差
多少才算显著」，材料里没有依据，因此不判 —— 把分位数摆出来，让读者自己
掂量。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from ir_agent.lineparse import keyed, pick
from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method
from ir_agent.sources.external import attribute
from ir_agent.basis import Basis, validate
from ir_agent import qual_review as Q


class ChainFailed(Exception):
    """产业链分析无从进行（没有任何资料等）。**内容是否成立不再由代码驳回**
    （子项目四，spec 2026-10-17）：交给审核者，审核不过抛 `qual_review.ReviewStop`。"""


class Role(str, Enum):
    UPSTREAM = "上游"
    FOCAL = "本环节"
    DOWNSTREAM = "下游"


class Power(str, Enum):
    STRONG = "强"
    BALANCED = "均衡"
    WEAK = "弱"


# 议价方向 → 用于交叉印证的账本事实，以及该事实应指向的方向。
#
#   对上游议价强 ⟹ 能占用供应商资金 ⟹ 应付天数**高于**同业中位数
#   对下游议价强 ⟹ 能先收钱后发货 ⟹ 应收天数**低于**同业中位数
#
# 两条都是会计恒等式层面的直接含义，不是经验规律。
BARGAIN_ANCHORS: dict[Role, tuple[str, bool]] = {
    Role.UPSTREAM: ("payable_days.percentile", True),      # True = 应偏高
    Role.DOWNSTREAM: ("receivable_days.percentile", False),
}

MEDIAN = Decimal("0.5")   # 唯一的分界线: 比典型同业更好还是更差

_LINK = keyed("环节")
_POWER = keyed("议价")
_SIZE = keyed("行业规模")
_QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)
_REF = re.compile(r"\[\[[^\]]+\]\]")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")




@dataclass
class Link:
    role: Role
    name: str
    note: str
    bases: list[Basis] = field(default_factory=list)
    cites: list = field(default_factory=list)       # 转述指向的文章标题


@dataclass
class SizeClaim:
    """行业真实规模。**口径必须写清楚** —— 「白酒行业 7800 亿」是规模以上
    企业营收、是零售额、还是产量折算，差得很远。口径不明的数字不能用。"""

    label: str
    value: Decimal
    unit: str
    period: str
    bases: list[Basis] = field(default_factory=list)
    cites: list = field(default_factory=list)

    def to_fact(self, source_id: str, as_of: date) -> Fact:
        """落账本。由 LLM 从文本抽取，因此 method=EXTRACTED、confidence<1，
        下游据此降权并提示复核 —— 与年报 PDF 抽数走同一条路。"""
        return Fact(key="industry.size_external", value=self.value,
                    unit=self.unit, period=self.period, as_of=as_of,
                    source_id=source_id, method=Method.EXTRACTED,
                    confidence=0.8)


@dataclass
class ChainView:
    links: list[Link] = field(default_factory=list)
    power: dict[Role, Power] = field(default_factory=dict)
    size: SizeClaim | None = None
    conflicts: list[str] = field(default_factory=list)
    # 子项目四：代码读数时的事实、审核结论、各轮记录。
    read_notes: list[str] = field(default_factory=list)
    review: object = None
    rounds: list = field(default_factory=list)

    def of(self, role: Role) -> list[Link]:
        return [x for x in self.links if x.role is role]

    def summary(self) -> str:
        lines = ["产业链"]
        for r in Role:
            got = self.of(r)
            if not got:
                continue
            lines.append(f"  【{r.value}】")
            for x in got:
                lines.append(f"    · {x.name} —— {x.note}")
            if r in self.power:
                lines.append(f"    议价：{self.power[r].value}")
        if self.size:
            lines.append(f"  行业规模口径：{self.size.label}"
                         f"（{self.size.period}，来源见依据）")
        else:
            lines.append("  行业规模：外部资料未给出真实口径规模 —— "
                         "本报告的行业规模仅为 A 股同行业上市公司合计，"
                         "非上市企业未计入，据此算出的市占率系高估")
        for c in self.conflicts:
            lines.append(f"  ⚠ {c}")
        lines += Q.review_tail(self.review)
        return "\n".join(lines)






def _cells(line: str) -> list[str]:
    return [p.strip() for p in line.split("|")]


def parse(text: str, corpus=None) -> ChainView:
    """读数。**只读，不驳回**（子项目四）：段数不够、环节位置/议价强弱不是规定的词、
    对本环节给议价、行业规模读不出数的行跳过；依据问题照读 —— 都记进 `read_notes`，
    作为事实交给审核者。"""
    view = ChainView()
    note = view.read_notes.append
    for m in _LINK.finditer(text):
        p = _cells(m.group(1))
        if len(p) < 4:
            note(f"环节一行字段不足（需 4 段），没读进来：{m.group(1)}")
            continue
        got = pick(p[0], [x.value for x in Role])
        if got is None:
            note(f"环节位置「{p[0]}」不是 " + "/".join(x.value for x in Role) + " 之一，没读进来")
            continue
        bases, prob, cites = Q.basis(" | ".join(p[3:]), corpus)
        if prob:
            note(f"环节「{p[1]}」的依据：{prob}")
        view.links.append(Link(Role(got), p[1], p[2], bases, cites))
    for m in _POWER.finditer(text):
        p = _cells(m.group(1))
        r_ = pick(p[0], [x.value for x in Role]) if p else None
        p_ = pick(p[1], [x.value for x in Power]) if len(p) > 1 else None
        if r_ is None or p_ is None:
            note(f"议价一行读不出：{m.group(1)}（位置须为 " + "/".join(x.value for x in Role)
                 + "，强弱须为 " + "/".join(x.value for x in Power) + "）")
            continue
        if Role(r_) is Role.FOCAL:
            note("议价给了「本环节」—— 议价只对上游与下游而言，这一行没读进来")
            continue
        view.power[Role(r_)] = Power(p_)
    m = _SIZE.search(text)
    if m:
        p = _cells(m.group(1))
        num = _NUM.search(p[1].replace(",", "")) if len(p) > 1 else None
        if len(p) < 5:
            note(f"行业规模一行字段不足（需 5 段），没读进来：{m.group(1)}")
        elif not num:
            note(f"行业规模「{p[1]}」读不出数值，没读进来")
        else:
            bases, prob, cites = Q.basis(" | ".join(p[4:]), corpus)
            if prob:
                note(f"行业规模的依据：{prob}")
            view.size = SizeClaim(label=p[0], value=Decimal(num.group(0)), unit=p[2],
                                  period=p[3], bases=bases, cites=cites)
    return view


def cross_check(view: ChainView, ledger: FactLedger, period: str,
                as_of: date) -> list[str]:
    """议价判断与账本分位数对不上的地方。**分界线只用同业中位数。**

    「相差多少才算显著」在材料里没有依据，因此不判 —— 只报方向相反，
    并把分位数一并给出，让读者自己掂量。
    """
    out = []
    for role, (key, should_be_high) in BARGAIN_ANCHORS.items():
        claimed = view.power.get(role)
        if claimed is not Power.STRONG:
            continue
        try:
            pct = ledger.get(key, period, as_of=as_of).value
        except (KeyError, LookAheadError):
            continue
        is_high = pct > MEDIAN
        if is_high is should_be_high:
            continue
        word = "高于" if should_be_high else "低于"
        out.append(
            f"判断「对{role.value}议价强」意味着{key.split('.')[0]}应{word}"
            f"同业中位数，实际处于同业 [[{key}@{period}]] 分位 —— 方向相反，"
            f"正文需说明缘由或改判")
    return out


def facts(view: ChainView, ledger: FactLedger, evidence: str, as_of: date,
          corpus=None, period: str = "") -> list[str]:
    """代码核对出的事实，交审核者（子项目四：此前这些是驳回理由）。"""
    out: list[str] = []
    for role in Role:
        if not view.of(role):
            out.append(f"产业链缺少「{role.value}」环节")
    for role in (Role.UPSTREAM, Role.DOWNSTREAM):
        if role not in view.power:
            out.append(f"没有给出对{role.value}的议价判断")
    from ir_agent.citation import audit_prose

    prose = {f"环节「{x.name[:12]}」": x.name + x.note for x in view.links}
    if view.size:
        prose["行业规模口径"] = view.size.label
    bad = audit_prose(prose)
    if bad:
        out.append("描述里有账本外的数字：" + "、".join(bad))
    items = list(view.links) + ([view.size] if view.size else [])
    attribute(items, evidence, corpus)
    flat = [b for x in items for b in x.bases]
    # 整稿都用转述时没有可核对的引文 —— validate([]) 会报「未提供任何依据」，对转述是假事实。
    if flat:
        out += [f"依据核验不过：{p}" for p in validate(flat, ledger=ledger, body=evidence,
                                                      as_of=as_of, corpus=corpus)]
    if period:
        out += [f"与账本交叉核对：{x}" for x in cross_check(view, ledger, period, as_of)]
    return out


KINDS = ("环节", "议价", "规模")


def checklist(view: ChainView) -> list[tuple[str, str]]:
    """按方法论生成（用户决定 5）：环节固定 3 条、议价固定 2 条、行业规模给了才列。"""
    items = [("环节", r.value) for r in Role]
    items += [("议价", "对上游"), ("议价", "对下游")]
    return items + ([("规模", "行业规模")] if view.size else [])


RULES = Q.make_rules("产业链", """\
- 环节：上游/本环节/下游的识别是否正确；对本公司意味着什么是否说清；有没有凭常识补出来
  的环节（看起来完整、其中一环是编的，比缺一环更糟）。没给出的环节判「未给出」。
- 议价：强/均衡/弱与证据是否一致；与账本周转天数是否一致 —— 对上游议价强应体现为
  应付天数高于同业中位（占用供应商资金），对下游议价强应体现为应收天数低于同业中位
  （先款后货）；代码给了交叉核对结果。没给出的判「未给出」。
- 规模：口径、数值、单位、期间是否对得上原文；口径不明的规模不能用。""")


PROMPT = """基于下面的资料，梳理本公司所处的产业链。

**一、完整产业链**（上游、本环节、下游都要有，各一到三条）
格式：`环节: <上游|本环节|下游> | <环节名> | <该环节对本公司意味着什么> | <依据>`

**二、议价能力**（对上游、对下游各一条）
格式：`议价: <上游|下游> | <强|均衡|弱>`

**三、行业真实规模**（资料里有明确口径的才写，没有就整行不要）
口径必须写清楚——「规模以上企业营业收入」和「终端零售额」差得很远。
格式：`行业规模: <口径描述> | <数值> | <单位> | <期间如2025FY> | <依据>`

{basis_rules}

**要求**
- 资料里没讲到的环节，**不要凭常识补全**。产业链看起来完整但其中一环
  是编的，比缺一环更糟。
- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本。需要数字时用给你的占位符，否则改成不带数字的说法。
- 议价判断会与账本里的应付/应收天数同业分位交叉核对，方向相反时你需要
  说明缘由。

{review_note}

{evidence}
"""


def for_prompt(corpus, ledger: FactLedger, period: str, as_of: date,
               sections=None) -> str:
    lines = ["产业链分析的资料"]
    if sections is not None:
        for s in sections.sections.values():
            lines.append(f"  【年报·{s.label}】")
            lines.append("  " + s.text.replace("\n", "\n  "))
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
        lines.append("  外部资料：本次未取到 —— 产业链只能依据年报自述，"
                     "覆盖面有限，未提及的环节须明说无据")

    refs = []
    for key, _ in BARGAIN_ANCHORS.values():
        try:
            ledger.get(key, period, as_of=as_of)
        except (KeyError, LookAheadError):
            continue
        refs.append(f"[[{key}@{period}]]")
    if refs:
        lines.append("\n  可交叉印证的账本事实（同业分位）：" + "　".join(refs))
    return "\n".join(lines)


def analyse(client, corpus, ledger: FactLedger, period: str, as_of: date,
            sections=None, *, judge, catalog: str = "",
            max_rounds: int = Q.MAX_ROUNDS) -> tuple[ChainView, str]:
    """撰写者写、审核者审（子项目四）。返回 (结论, 证据文本)；审核不过抛
    `qual_review.ReviewStop`，模型接口错误原样抛出。"""
    evidence = for_prompt(corpus, ledger, period, as_of, sections)
    body = "\n".join(
        [s.text for s in (sections.sections.values() if sections else [])])
    prompt = PROMPT.format(evidence=evidence, basis_rules=Q.BASIS_RULES,
                           review_note=Q.REVIEW_NOTE)
    view, res, rounds = Q.run(
        client, judge, name="产业链", prompt=prompt, materials=evidence,
        read=lambda raw: parse(raw, corpus),
        facts=lambda v: facts(v, ledger, body, as_of, corpus, period),
        checklist=checklist, kinds=KINDS, rules=RULES, catalog=catalog,
        hay=evidence, ledger=ledger, as_of=as_of, max_rounds=max_rounds)
    view.conflicts = cross_check(view, ledger, period, as_of)
    view.review, view.rounds = res, rounds
    return view, evidence
