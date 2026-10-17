"""战略分析 —— 护城河、五力，以及通向估值的那座桥。

课程对这一节的要求有三条，第三条最容易被忽略:

  1. 至多用两个框架，且**必须同时覆盖内部与外部**驱动因素
  2. 应用时结合最新年报（MD&A）
  3. > **思考你将如何利用战略分析来为后续的估值练习提供输入（例如情景规划）**

第三条决定了本模块的产出形态。战略分析若只产出几段文字，它就是研报里
可有可无的一节；只有当它产出**关键不确定性 → 建模驱动因子**的映射时，
Bear/Base/Bull 三情景才有定性依据，而不是把基准参数上下调两个百分点。

**定性判断不假装成事实。** 护城河类型、五力强度本质上是判断，账本里
不可能有对应条目。它们的正当出口是项目已有的 Basis 机制:

  · REPORT —— 引年报原文（必须逐字出现在抽取的章节里）
  · FACT   —— 引账本事实（如集中度、毛利率分位）

**其中三力可以部分量化**，用已有事实交叉印证模型的定性判断 ——
判断与数字打架时，那个矛盾本身就是研报该写的东西。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from ir_agent.lineparse import keyed, pick
from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.sources.external import attribute
from ir_agent.basis import Basis, validate
from ir_agent import qual_review as Q


class MoatKind(str, Enum):
    """课程给的五种护城河来源，外加「无」。"""

    COST = "成本优势"
    NETWORK = "网络效应"
    INTANGIBLE = "无形资产"
    SWITCHING = "转换成本"
    SCALE = "有效规模"
    NONE = "无明显护城河"


class Force(str, Enum):
    RIVALRY = "现有竞争强度"
    ENTRANTS = "新进入者威胁"
    SUPPLIERS = "供应商议价能力"
    BUYERS = "买方议价能力"
    SUBSTITUTES = "替代品威胁"


# 五力中可以部分量化的三力，以及各自的账本锚点。
# 另外两力（替代品、以及买方/供应商中不可算的部分）只能靠定性判断 ——
# 硬给它们编一个代理指标，是把无依据的东西装扮成有依据。
FORCE_ANCHORS: dict[Force, tuple[str, ...]] = {
    Force.RIVALRY: ("industry.hhi", "industry.effective_players",
                    "industry.cr5"),
    Force.ENTRANTS: ("industry.roe_median", "industry.revenue_cagr",
                     "industry.dol_median"),
    Force.SUPPLIERS: ("payable_days", "gross_margin.percentile"),
    Force.BUYERS: ("receivable_days", "contract_liability",
                   "gross_margin.percentile"),
}

_ANCHOR_NOTE = {
    Force.RIVALRY: "集中度越低、有效竞争者越多，现有竞争通常越激烈",
    Force.ENTRANTS: "行业回报率高而经营杠杆低，对新进入者越有吸引力；"
                    "重资产行业本身即是壁垒",
    Force.SUPPLIERS: "应付账款周转天数长 = 占用上游资金，议价力强；"
                     "毛利率高于同业也意味着成本端可控",
    Force.BUYERS: "应收天数短、合同负债（预收款）多 = 对下游强势",
}

INTENSITY = ("高", "中", "低")
STRENGTH = ("宽", "窄", "无")




@dataclass(frozen=True)
class MoatView:
    kind: MoatKind
    strength: str                       # 宽 / 窄 / 无
    bases: list[Basis] = field(default_factory=list)
    cites: list = field(default_factory=list)       # 转述指向的文章标题

    def describe(self) -> str:
        if self.kind is MoatKind.NONE:
            return "未识别出明显护城河"
        return f"{self.kind.value}（{self.strength}护城河）"


@dataclass(frozen=True)
class ForceView:
    force: Force
    intensity: str                      # 高 / 中 / 低
    verdict: str                        # 对本司有利 / 中性 / 不利
    bases: list[Basis] = field(default_factory=list)
    cites: list = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.force.value} {self.intensity}　→ {self.verdict}"


@dataclass(frozen=True)
class Uncertainty:
    """关键不确定性 → 建模驱动因子。**这是战略分析通向估值的桥。**

    driver 必须是 DriverSet 里真实存在的字段名，否则情景规划接不上 ——
    「宏观需求波动」这种说法无法映射到任何一个可调参数，等于没说。
    """

    what: str
    driver: str                         # 对应的建模驱动因子
    direction: str                      # 恶化时该因子往哪个方向走
    bases: list[Basis] = field(default_factory=list)
    cites: list = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.what} → {self.driver}（不利时{self.direction}）"


# 战略分析可映射到的驱动因子 —— 与 valuation/derive.py 的假设项对齐。
# 不在此列的「驱动因子」一律驳回: 映射不到可调参数的不确定性，
# 在情景分析里没有出口。
MAPPABLE_DRIVERS = {
    "revenue_growth": "收入增速",
    "gross_margin": "毛利率",
    "opex_pct_revenue": "费用率",
    "capex_pct_revenue": "资本开支率",
    "terminal_growth": "永续增长率",
    "wacc": "折现率",
}


@dataclass
class StrategyView:
    moats: list[MoatView] = field(default_factory=list)
    forces: list[ForceView] = field(default_factory=list)
    uncertainties: list[Uncertainty] = field(default_factory=list)
    unquantified: list[Force] = field(default_factory=list)
    # 子项目四：代码读数时的事实、审核结论、各轮记录。
    read_notes: list[str] = field(default_factory=list)
    review: object = None
    rounds: list = field(default_factory=list)

    def summary(self) -> str:
        lines = ["战略分析"]
        if self.moats:
            lines.append("  护城河　" + "；".join(m.describe() for m in self.moats))
        for f in self.forces:
            lines.append(f"  · {f.describe()}")
        if self.unquantified:
            lines.append("  ⚠ 以下几力无账本锚点，纯定性判断：" +
                         "、".join(f.value for f in self.unquantified))
        if self.uncertainties:
            lines.append("  关键不确定性 → 情景驱动因子")
            for u in self.uncertainties:
                lines.append(f"    {u.describe()}")
        lines += Q.review_tail(self.review)
        return "\n".join(lines)


class StrategyFailed(Exception):
    """战略分析无从进行（没有任何定性证据等）。**内容是否成立不再由代码驳回**
    （子项目四，spec 2026-10-17）：交给审核者，审核不过抛 `qual_review.ReviewStop`。"""


# ── 量化锚点 ──────────────────────────────────────────────────

def anchors(ledger: FactLedger, period: str, as_of: date,
            spot: str | None = None) -> dict[Force, list[str]]:
    """各力可用的账本占位符。用于让模型的定性判断与数字互相印证。"""
    out: dict[Force, list[str]] = {}
    for force, keys in FORCE_ANCHORS.items():
        refs = []
        for key in keys:
            per = spot if key in ("market_cap",) and spot else period
            try:
                ledger.get(key, per, as_of=as_of)
            except (KeyError, LookAheadError):
                continue
            refs.append(f"[[{key}@{per}]]")
        if refs:
            out[force] = refs
    return out


def annual_text(sections) -> str:
    """年报摘录的纯文本，用于 REPORT 引文的逐字核验。

    **必须与外部资料分开。** 若把研报正文也拼进这段，模型引研报的话会被
    判为「年报原文」通过核验 —— 引文是真的，出处是假的，比引文造假更难
    发现。归属错误在研报里是硬伤: 读者按图索骥会查不到。
    """
    if sections is None:
        return ""
    return "\n".join(s.text for s in sections.sections.values())


def for_prompt(ledger: FactLedger, period: str, as_of: date,
               sections, spot: str | None = None, corpus=None) -> str:
    """给模型的战略分析口径: 年报摘录 + 外部资料 + 可用的量化锚点。

    定性证据有两个来源，且**都必须是代码实际取回的**: 年报摘录与外部
    语料库。模型的引文必须逐字出自其中之一，否则核验通不过。摘录本身含
    大量数字（原文），但它们不进正文引用，只作判断的素材，因此不受
    占位符契约约束。

    引入外部资料的理由: 只看年报，战略判断的全部依据是**公司对自己的
    描述** —— 相当于让被评价方写自己的评价。茅台核心竞争力一节仅 95 字，
    据此写出的护城河判断，实质是把公司的自我宣传改写了一遍。
    """
    from ir_agent.sources.annual_sections import SECTION_LABELS

    lines = ["战略分析的证据基础"]
    if sections is not None:
        for s in sections.sections.values():
            lines.append(f"  【{s.label}】原文第 {s.page} 页")
            lines.append("  " + s.text.replace("\n", "\n  "))
        for k in sections.missing:
            lines.append(f"  【{SECTION_LABELS.get(k, k)}】年报未设此节 —— "
                         "相关判断需明说无据，不得凭印象补足")

    got = anchors(ledger, period, as_of, spot)
    if got:
        lines.append("\n  可用于交叉印证的账本事实：")
        for force, refs in got.items():
            lines.append(f"    {force.value}　{'　'.join(refs)}"
                         f"\n      {_ANCHOR_NOTE[force]}")
    missing = [f for f in Force if f not in got]
    if missing:
        lines.append("    以下几力无账本锚点，只能定性判断，"
                     "结论强度应相应说明：" + "、".join(f.value for f in missing))

    if corpus is not None and corpus.docs:
        from ir_agent.sources.external import TIER_LABEL, TIER_RULE, Tier
        lines.append("\n  公司之外的资料（可引用原文，也可转述 —— 转述要写明采纳了哪几篇）：")
        seen_tiers = {d.tier for d in corpus.docs}
        # 可引用范围要先说清楚，模型才是带着这条界线去读下面的资料 ——
        # 放在资料读完之后才说，等于让它先斩后奏。
        #
        # **按 `Tier` 的声明顺序遍历，不按 `seen_tiers` 这个 set 的迭代
        # 顺序。** `Tier` 是个 str-Enum，同一个 set 在不同进程里的迭代
        # 顺序取决于 PYTHONHASHSEED——「[财经媒体] 的可引用范围：仅作
        # 线索，不得作为依据」是这几条规则里最要紧的一条，它落在第一、
        # 第二还是第三条完全是随机的，提示词因此不是跨运行可复现的。
        # `Corpus.summary()`（见 sources/external.py）已经用
        # `for t in Tier:` 避开了这个问题，这里跟它保持一致。
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
    elif corpus is not None:
        lines.append("\n  公司之外的资料：本次未取到 —— "
                     "外部视角缺位，战略判断全部来自公司自述，"
                     "结论强度应相应说明")
    return "\n".join(lines)


PROMPT = """基于下面的年报原文与账本事实，做战略分析。

课程要求：至多两个框架、必须同时覆盖内部与外部驱动因素、并且**要能为
后续的估值练习提供输入**。因此本节的产出不是几段文字，而是三样东西。

**一、护城河**（至多两条，宁缺毋滥）
格式：`护城河: <成本优势|网络效应|无形资产|转换成本|有效规模|无明显护城河> | <宽|窄|无> | <依据>`

**二、波特五力**（五条都要给，名称必须逐字照抄，不要改写）
格式：`五力: <{forces}> | <高|中|低> | <对本司有利|中性|对本司不利> | <依据>`

**三、关键不确定性 → 情景驱动因子**（两到三条）
这一条是战略分析与估值之间的桥。每条不确定性必须映射到一个**可调的
建模参数**，否则它在情景分析里没有出口。
可选驱动因子：{drivers}
格式：`不确定性: <描述> | <驱动因子> | <不利时该因子如何变化> | <依据>`

{basis_rules}

**要求**
- 占位符只能用下面列出的那些。
- 年报未设的章节，相关判断**明说无据**，不要凭印象补足。
- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本。需要数字时用给你的占位符，否则改成不带数字的说法。
- 判断与数字打架时（例如年报自述成本优势、而毛利率处于同业低分位），
  **把矛盾写出来**，那正是研报该说的东西。

{review_note}

{evidence}
"""

_MOAT = keyed("护城河")
_FORCE = keyed("五力")
_UNCERT = keyed("不确定性")
_QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)
_REF = re.compile(r"\[\[[^\]]+\]\]")






def _cells(line: str) -> list[str]:
    return [p.strip() for p in line.split("|")]


def parse(text: str, corpus=None) -> StrategyView:
    """读数。**只读，不驳回**（子项目四）：段数不够、护城河类型/力的名称不是规定的词
    的行跳过；强弱等级不是规定的词、驱动因子不在可调参数之列的照读 —— 都记进
    `read_notes`，作为事实交给审核者。"""
    view = StrategyView()
    note = view.read_notes.append

    def _b(parts, what):
        bases, prob, cites = Q.basis(" | ".join(parts), corpus)
        if prob:
            note(f"{what}的依据：{prob}")
        return bases, cites

    for m in _MOAT.finditer(text):
        p = _cells(m.group(1))
        if len(p) < 3:
            note(f"护城河一行字段不足（需 3 段），没读进来：{m.group(1)}")
            continue
        try:
            kind = MoatKind(p[0])
        except ValueError:
            note(f"护城河类型「{p[0]}」不是规定的类型（"
                 + "、".join(k.value for k in MoatKind) + "），没读进来")
            continue
        strength = pick(p[1], STRENGTH)
        if kind is not MoatKind.NONE and strength is None:
            note(f"护城河「{kind.value}」的宽度「{p[1]}」不是 {'/'.join(STRENGTH)} 之一")
        bases, cites = _b(p[2:], f"护城河「{kind.value}」")
        view.moats.append(MoatView(kind, strength or p[1], bases, cites))

    for m in _FORCE.finditer(text):
        p = _cells(m.group(1))
        if len(p) < 4:
            note(f"五力一行字段不足（需 4 段），没读进来：{m.group(1)}")
            continue
        try:
            force = Force(p[0])
        except ValueError:
            # 长电实测：模型写「现有竞争者的竞争强度」，与「现有竞争强度」只差两字。
            note(f"五力名称「{p[0]}」不是规定的五个名称之一（"
                 + "、".join(f.value for f in Force) + "），没读进来")
            continue
        intensity = pick(p[1], INTENSITY)
        if intensity is None:
            note(f"五力「{force.value}」的强度「{p[1]}」不是 {'/'.join(INTENSITY)} 之一")
        bases, cites = _b(p[3:], f"五力「{force.value}」")
        view.forces.append(ForceView(force, intensity or p[1], p[2], bases, cites))

    for m in _UNCERT.finditer(text):
        p = _cells(m.group(1))
        if len(p) < 4:
            note(f"不确定性一行字段不足（需 4 段），没读进来：{m.group(1)}")
            continue
        if p[1] not in MAPPABLE_DRIVERS:
            note(f"不确定性「{p[0]}」的驱动因子「{p[1]}」不在可调参数之列（可选："
                 + "、".join(MAPPABLE_DRIVERS) + "）")
        bases, cites = _b(p[3:], f"不确定性「{p[0]}」")
        view.uncertainties.append(Uncertainty(p[0], p[1], p[2], bases, cites))
    return view


def facts(view: StrategyView, ledger: FactLedger, evidence: str, as_of: date,
          corpus=None) -> list[str]:
    """代码核对出的事实，交审核者（子项目四：此前这些是驳回理由）。`evidence` 是
    年报摘录原文 —— REPORT 引文对着它核验，外部引文对着语料库。"""
    out: list[str] = []
    if not view.moats:
        out.append("没有给出护城河判断（「无明显护城河」也是结论，方法论要求显式说出）")
    got = {f.force for f in view.forces}
    if got != set(Force):
        out.append("五力缺：" + "、".join(f.value for f in Force if f not in got))
    if len(view.uncertainties) < 2:
        out.append(f"关键不确定性只读到 {len(view.uncertainties)} 条（方法论要求至少两条 —— "
                   "少于两条，三情景就没有定性依据）")
    from ir_agent.citation import audit_prose

    bad = audit_prose({f"不确定性「{u.what[:12]}」": u.what + u.direction
                       for u in view.uncertainties})
    if bad:
        out.append("描述里有账本外的数字：" + "、".join(bad))
    items = (*view.moats, *view.forces, *view.uncertainties)
    attribute(items, evidence, corpus)
    flat = [b for x in items for b in x.bases]
    out += [f"依据核验不过：{p}" for p in validate(flat, ledger=ledger, body=evidence,
                                                  as_of=as_of, corpus=corpus)]
    return out


KINDS = ("护城河", "五力", "不确定性", "自洽")


def checklist(view: StrategyView) -> list[tuple[str, str]]:
    """按方法论生成（用户决定 5）：护城河每条（一条都没有列「未给出」）、五力固定 5 条、
    不确定性每条（不足 2 条补「未给出」到 2 条）、自洽一条。"""
    items = [("护城河", f"第{i}条 {m.kind.value}") for i, m in enumerate(view.moats, 1)] \
        or [("护城河", "护城河（未给出）")]
    items += [("五力", f.value) for f in Force]
    unc = [("不确定性", f"第{i}条 {u.what[:16]}")
           for i, u in enumerate(view.uncertainties, 1)]
    unc += [("不确定性", f"第{i}条（未给出）") for i in range(len(unc) + 1, 3)]
    return items + unc + [("自洽", "护城河、五力与不确定性")]


RULES = Q.make_rules("战略分析", """\
- 护城河：类型与宽度有没有证据支撑；「无明显护城河」也判是否成立。一条都没给也在这里判。
- 五力：高/中/低与「对本司有利/中性/不利」是否一致；依据能否支撑；**与账本数据是否
  矛盾** —— 应付账款周转天数短，常规理解是对上游议价**弱**（无力占用供应商资金），
  不是强势；应收天数短、合同负债多才是对下游强势；毛利率分位也要对照。没给出的力判「未给出」。
- 不确定性：是不是真正的关键变量；映射的驱动因子对不对（要能在情景分析里调）；「不利时
  如何变化」方向对不对。
- 自洽：护城河、五力、不确定性三部分是否彼此矛盾（例如宽护城河却五力全不利）。""")


def assess(client, ledger: FactLedger, period: str, as_of: date,
           sections, spot: str | None = None, *, judge, catalog: str = "",
           corpus=None, max_rounds: int = Q.MAX_ROUNDS) -> tuple[StrategyView, str]:
    """撰写者写、审核者审（子项目四）。返回 (结论, 证据文本)；审核不过抛
    `qual_review.ReviewStop`，模型接口错误原样抛出。"""
    evidence = for_prompt(ledger, period, as_of, sections, spot, corpus)
    annual = annual_text(sections)
    prompt = PROMPT.format(drivers="、".join(MAPPABLE_DRIVERS),
                           forces="|".join(f.value for f in Force),
                           evidence=evidence, basis_rules=Q.BASIS_RULES,
                           review_note=Q.REVIEW_NOTE)
    view, res, rounds = Q.run(
        client, judge, name="战略分析", prompt=prompt, materials=evidence,
        read=lambda raw: parse(raw, corpus),
        facts=lambda v: facts(v, ledger, annual, as_of, corpus),
        checklist=checklist, kinds=KINDS, rules=RULES, catalog=catalog,
        hay=evidence, ledger=ledger, as_of=as_of, max_rounds=max_rounds)
    view.unquantified = [f for f in Force
                         if f not in anchors(ledger, period, as_of, spot)]
    view.review, view.rounds = res, rounds
    return view, evidence
