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
from decimal import Decimal, InvalidOperation
from enum import Enum

from ir_agent.lineparse import keyed, pick
from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method
from ir_agent.sources.external import attribute
from ir_agent.basis import Basis, InvalidBasis, validate


class ChainFailed(Exception):
    """产业链分析不可核验 —— 拒绝出结论。"""


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



def _tail(raw: str, n: int = 400) -> str:
    """失败信息里带上模型最后写了什么。

    **只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。** 前几轮
    排查全跑失败时，每次都要单独再跑一次去抓原始输出 —— 而那几次的根因
    （列表前缀、括号补注、写「无据」）看一眼原文就清楚了。
    """
    raw = (raw or "").strip()
    return raw[-n:] if len(raw) > n else raw

@dataclass
class Link:
    role: Role
    name: str
    note: str
    bases: list[Basis] = field(default_factory=list)


@dataclass
class SizeClaim:
    """行业真实规模。**口径必须写清楚** —— 「白酒行业 7800 亿」是规模以上
    企业营收、是零售额、还是产量折算，差得很远。口径不明的数字不能用。"""

    label: str
    value: Decimal
    unit: str
    period: str
    bases: list[Basis] = field(default_factory=list)

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
        return "\n".join(lines)


def _basis(raw: str, what: str) -> list[Basis]:
    raw = raw.strip()
    ref = _REF.search(raw)
    if ref:
        try:
            return [Basis.fact(ref.group(0))]
        except InvalidBasis as e:
            raise ChainFailed(f"{what} 的占位符不合法: {e}") from e
    q = _QUOTE.search(raw)
    if not q:
        raise ChainFailed(f"{what} 缺少可核验的依据（需原文引用或占位符）")
    try:
        return [Basis.report(q.group(1))]
    except InvalidBasis as e:
        raise ChainFailed(f"{what} 的依据不合法: {e}") from e


def _split(line: str, n: int, what: str) -> list[str]:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < n:
        raise ChainFailed(f"{what} 字段不足（需 {n} 段，得到 {len(parts)}）：{line}")
    return parts


def parse(text: str) -> ChainView:
    view = ChainView()

    for m in _LINK.finditer(text):
        p = _split(m.group(1), 4, "环节")
        got = pick(p[0], [x.value for x in Role])
        if got is None:
            raise ChainFailed(
                f"未知的环节位置 {p[0]!r}（须为 "
                + "/".join(x.value for x in Role) + "）")
        view.links.append(Link(Role(got), p[1], p[2], _basis(p[3], "环节")))

    for m in _POWER.finditer(text):
        p = _split(m.group(1), 2, "议价")
        r_, p_ = (pick(p[0], [x.value for x in Role]),
                  pick(p[1], [x.value for x in Power]))
        if r_ is None or p_ is None:
            raise ChainFailed(
                f"议价一行取值不合法：{m.group(1)} —— 位置须为 "
                + "/".join(x.value for x in Role) + "，强弱须为 "
                + "/".join(x.value for x in Power))
        role, power = Role(r_), Power(p_)
        if role is Role.FOCAL:
            raise ChainFailed("议价只对上游与下游而言，本环节无议价对象")
        view.power[role] = power

    m = _SIZE.search(text)
    if m:
        p = _split(m.group(1), 5, "行业规模")
        num = _NUM.search(p[1].replace(",", ""))
        if not num:
            raise ChainFailed(f"行业规模缺少数值：{p[1]!r}")
        try:
            val = Decimal(num.group(0))
        except InvalidOperation as e:
            raise ChainFailed(f"行业规模数值不可解析：{p[1]!r}") from e
        view.size = SizeClaim(label=p[0], value=val, unit=p[2], period=p[3],
                              bases=_basis(p[4], "行业规模"))
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


def check(view: ChainView, ledger: FactLedger, evidence: str, as_of: date,
          corpus=None) -> None:
    for role in Role:
        if not view.of(role):
            raise ChainFailed(
                f"产业链缺少「{role.value}」环节 —— 链条断了就谈不上"
                f"「完整产业链」，也无从判断公司的议价能力从何而来")
    if Role.UPSTREAM not in view.power or Role.DOWNSTREAM not in view.power:
        raise ChainFailed("须同时给出对上游与对下游的议价判断")

    from ir_agent.citation import audit_prose

    prose = {f"环节「{x.name[:12]}」": x.name + x.note for x in view.links}
    if view.size:
        prose["行业规模口径"] = view.size.label
    bad = audit_prose(prose)
    if bad:
        raise ChainFailed(
            "描述里出现了账本外的数字：" + "、".join(bad) +
            " —— 这些描述会原样进入研报撰写环节，而那里的数字必须可溯源；"
            "请改成不带具体数字的说法")

    items = list(view.links) + ([view.size] if view.size else [])
    attribute(items, evidence, corpus)
    flat = [b for x in view.links for b in x.bases]
    if view.size:
        flat += view.size.bases
    problems = validate(flat, ledger=ledger, body=evidence, as_of=as_of,
                        corpus=corpus)
    if problems:
        raise ChainFailed("依据不可核验：" + "；".join(problems))


PROMPT = """基于下面的资料，梳理本公司所处的产业链。

**一、完整产业链**（上游、本环节、下游都要有，各一到三条）
格式：`环节: <上游|本环节|下游> | <环节名> | <该环节对本公司意味着什么> | 「原文逐字引用」`

**二、议价能力**（对上游、对下游各一条）
格式：`议价: <上游|下游> | <强|均衡|弱>`

**三、行业真实规模**（资料里有明确口径的才写，没有就整行不要）
口径必须写清楚——「规模以上企业营业收入」和「终端零售额」差得很远。
格式：`行业规模: <口径描述> | <数值> | <单位> | <期间如2025FY> | 「原文逐字引用」`

**硬性要求**
- 引用必须**逐字照抄**下面的资料，一个字都不能改，否则核验通不过。
- 资料里没讲到的环节，**不要凭常识补全**。产业链看起来完整但其中一环
  是编的，比缺一环更糟。
- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本 —— 一个无处可查的数字会让整篇报告卡在无法修复的驳回上。需要数字时用给你的占位符，否则改成不带数字的说法。
- 议价判断会与账本里的应付/应收天数同业分位交叉核对，方向相反时你需要
  在正文里说明缘由。

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
            sections=None, max_attempts: int = 3) -> tuple[ChainView, str]:
    evidence = for_prompt(corpus, ledger, period, as_of, sections)
    body = "\n".join(
        [s.text for s in (sections.sections.values() if sections else [])])
    prompt = PROMPT.format(evidence=evidence)
    last, raw = "", ""
    for attempt in range(1, max_attempts + 1):
        out = raw = client(prompt)
        try:
            view = parse(out)
            check(view, ledger, body, as_of, corpus)
        except ChainFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后按格式重新输出。\n")
            continue
        view.conflicts = cross_check(view, ledger, period, as_of)
        return view, evidence
    raise ChainFailed(
            f"连续 {max_attempts} 次给不出可核验的产业链分析：{last}"
            f"\n最后一次的原始输出（末 400 字）：\n{_tail(raw)}")
