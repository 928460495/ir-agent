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

from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.sources.external import attribute
from ir_agent.valuation.basis import Basis, InvalidBasis, validate


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
        return "\n".join(lines)


class StrategyFailed(Exception):
    """模型给不出可核验的战略判断。不退回一段「看起来像分析」的文字 ——
    没有依据的战略判断在研报里比空白更危险。"""


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
        from ir_agent.sources.external import TIER_LABEL, TIER_RULE
        lines.append("\n  公司之外的资料（引用时同样须逐字照抄）：")
        seen_tiers = set()
        for d in corpus.docs:
            lines.append(f"  ——【{TIER_LABEL[d.tier]}】{d.cite()}")
            lines.append("  " + d.text.replace("\n", "\n  "))
            seen_tiers.add(d.tier)
        for t in seen_tiers:
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
格式：`护城河: <成本优势|网络效应|无形资产|转换成本|有效规模|无明显护城河> | <宽|窄|无> | 「年报原文逐字引用」`

**二、波特五力**（五条都要给，名称必须逐字照抄，不要改写）
格式：`五力: <{forces}> | <高|中|低> | <对本司有利|中性|对本司不利> | 「年报原文」或 [[占位符]]`

**三、关键不确定性 → 情景驱动因子**（两到三条）
这一条是战略分析与估值之间的桥。每条不确定性必须映射到一个**可调的
建模参数**，否则它在情景分析里没有出口。
可选驱动因子：{drivers}
格式：`不确定性: <描述> | <驱动因子> | <不利时该因子如何变化> | 「年报原文」或 [[占位符]]`

**硬性要求**
- 引年报时必须**逐字照抄**，一个字都不能改，否则核验通不过。
- 占位符只能用上面列出的那些。
- 年报未设的章节，相关判断**明说无据**，不要凭印象补足。
- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本 —— 一个无处可查的数字会让整篇报告卡在无法修复的驳回上。需要数字时用给你的占位符，否则改成不带数字的说法。
- 判断与数字打架时（例如年报自述成本优势、而毛利率处于同业低分位），
  **把矛盾写出来**，那正是研报该说的东西。

{evidence}
"""

_MOAT = re.compile(r"^\s*护城河\s*[:：]\s*(.+)$", re.MULTILINE)
_FORCE = re.compile(r"^\s*五力\s*[:：]\s*(.+)$", re.MULTILINE)
_UNCERT = re.compile(r"^\s*不确定性\s*[:：]\s*(.+)$", re.MULTILINE)
_QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)
_REF = re.compile(r"\[\[[^\]]+\]\]")


def _basis(raw: str, what: str) -> list[Basis]:
    raw = raw.strip()
    ref = _REF.search(raw)
    if ref:
        try:
            return [Basis.fact(ref.group(0))]
        except InvalidBasis as e:
            raise StrategyFailed(f"{what} 的占位符不合法: {e}") from e
    q = _QUOTE.search(raw)
    if not q:
        raise StrategyFailed(f"{what} 缺少可核验的依据（需年报原文或占位符）")
    try:
        return [Basis.report(q.group(1))]
    except InvalidBasis as e:
        raise StrategyFailed(f"{what} 的依据不合法: {e}") from e


def _split(line: str, n: int, what: str) -> list[str]:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < n:
        raise StrategyFailed(f"{what} 字段不足（需 {n} 段，得到 {len(parts)}）：{line}")
    return parts


def parse(text: str) -> StrategyView:
    view = StrategyView()

    for m in _MOAT.finditer(text):
        p = _split(m.group(1), 3, "护城河")
        try:
            kind = MoatKind(p[0])
        except ValueError as e:
            raise StrategyFailed(
                f"未知的护城河类型 {p[0]!r}，必须逐字使用以下之一："
                + "、".join(k.value for k in MoatKind)) from e
        if kind is not MoatKind.NONE and p[1] not in STRENGTH:
            raise StrategyFailed(f"护城河强度须为 宽/窄/无，得到 {p[1]!r}")
        view.moats.append(MoatView(kind, p[1], _basis(p[2], "护城河")))

    for m in _FORCE.finditer(text):
        p = _split(m.group(1), 4, "五力")
        try:
            force = Force(p[0])
        except ValueError as e:
            # **驳回信息必须列出合法取值。** 长电实测: 模型写
            # 「现有竞争者的竞争强度」，与枚举值「现有竞争强度」只差两个字，
            # 而原信息只说「未知的力」，不给对照表 —— 模型连挂三次，
            # 整个战略分析没能产出。护城河那一行的提示词列了取值，
            # 五力这一行没列，是纯粹的疏漏。
            raise StrategyFailed(
                f"未知的力 {p[0]!r}，必须逐字使用以下五个名称之一："
                + "、".join(f.value for f in Force)) from e
        if p[1] not in INTENSITY:
            raise StrategyFailed(f"五力强度须为 高/中/低，得到 {p[1]!r}")
        view.forces.append(ForceView(force, p[1], p[2], _basis(p[3], "五力")))

    for m in _UNCERT.finditer(text):
        p = _split(m.group(1), 4, "不确定性")
        if p[1] not in MAPPABLE_DRIVERS:
            raise StrategyFailed(
                f"驱动因子 {p[1]!r} 不在可调参数之列 —— "
                f"映射不到参数的不确定性在情景分析里没有出口。"
                f"可选：{'、'.join(MAPPABLE_DRIVERS)}")
        view.uncertainties.append(
            Uncertainty(p[0], p[1], p[2], _basis(p[3], "不确定性")))

    return view


def check(view: StrategyView, ledger: FactLedger, evidence: str,
          as_of: date, corpus=None) -> None:
    """依据必须可核验。REPORT 引文要逐字出现在年报摘录里，
    EXTERNAL 引文要逐字出现在语料库里。"""
    if not view.moats:
        raise StrategyFailed("未给出护城河判断 —— 「无明显护城河」也是结论，"
                             "但必须显式说出并给出依据")
    got = {f.force for f in view.forces}
    if got != set(Force):
        raise StrategyFailed("五力须五条都给，缺：" +
                             "、".join(f.value for f in set(Force) - got))
    if len(view.uncertainties) < 2:
        raise StrategyFailed(
            "关键不确定性至少两条 —— 少于两条，Bear/Base/Bull 三情景就没有"
            "定性依据，只能靠调参数")

    from ir_agent.citation import audit_prose

    bad = audit_prose({f"不确定性「{u.what[:12]}」": u.what + u.direction
                       for u in view.uncertainties})
    if bad:
        raise StrategyFailed(
            "描述里出现了账本外的数字：" + "、".join(bad) +
            " —— 这些描述会原样进入研报撰写环节，而那里的数字必须可溯源；"
            "请改成不带具体数字的说法")

    attribute((*view.moats, *view.forces, *view.uncertainties),
              evidence, corpus)
    flat = [b for x in (*view.moats, *view.forces, *view.uncertainties)
            for b in x.bases]
    problems = validate(flat, ledger=ledger, body=evidence, as_of=as_of,
                        corpus=corpus)
    if problems:
        raise StrategyFailed("依据不可核验：" + "；".join(problems))


def assess(client, ledger: FactLedger, period: str, as_of: date,
           sections, spot: str | None = None,
           max_attempts: int = 3, corpus=None) -> tuple[StrategyView, str]:
    """让模型做战略分析，逐轮校验。返回 (结论, 证据文本)。"""
    evidence = for_prompt(ledger, period, as_of, sections, spot, corpus)
    annual = annual_text(sections)
    prompt = PROMPT.format(drivers="、".join(MAPPABLE_DRIVERS),
                           forces="|".join(f.value for f in Force),
                           evidence=evidence)
    last = ""
    for attempt in range(1, max_attempts + 1):
        out = client(prompt)
        try:
            view = parse(out)
            check(view, ledger, annual, as_of, corpus)
        except StrategyFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后按格式重新输出全部三部分。\n")
            continue
        view.unquantified = [f for f in Force
                             if f not in anchors(ledger, period, as_of, spot)]
        return view, evidence
    raise StrategyFailed(f"连续 {max_attempts} 次给不出可核验的战略判断：{last}")
