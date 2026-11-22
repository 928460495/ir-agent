"""宏观定性 —— 政策取向，以及它到本行业的传导路径。

**补的是宏观分析里此前完全没有的那一半。** GDP、CPI、PPI、PMI、M1−M2、
国债收益率这些**读数**接口早就有了，cycle.py 也能据此定出周期象限。
但「这些读数意味着什么、政策在往哪个方向走、它怎么传到这家公司头上」
一句都没有 —— 读数能定位周期，定不出政策取向，更定不出传导路径。

**传导路径是这一节的重点，不是政策取向本身。** 「货币政策转向宽松」写在
任何一篇研报里都成立，对判断某家公司没有任何帮助。有帮助的是那条链：
按揭利率下调 → 月供负担下降 → 新房成交回升 → 交房后装修需求释放。
链条写出来，读者才能判断它是否成立；写不出链条的政策判断，等于没写。

**专业化（spec 2026-11-21-staged-collection §5.1）**：每条传导先标渠道（货币政策传导的标准分类 ——
ECB、BIS、Mishkin —— 加上财政、监管、大宗商品、外需），写明落在哪个经营变量上；敏感度用行业周期
敏感度的三个决定因素（Bodie、Kane、Marcus《投资学》：收入弹性、经营杠杆、财务杠杆）并说明本公司的暴露
差异；时滞与「新增还是延续」是封闭取值；领先指标单独写 `跟踪:` 行，宏观审核通过后按它定向取最新读数。
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
MACRO_TRACK_MAX = 4
CHANNELS = ("利率", "信贷", "资产价格与财富", "汇率", "预期与信心", "财政", "监管与产业政策",
            "大宗商品与成本", "外需与贸易")
LAGS = ("即期", "短期", "中长期")
NOVELTY = ("新增", "延续")

_STANCE = keyed("政策取向")
_CHANNEL = keyed("传导")
_TRACK = keyed("跟踪")
_EXCLUDE = keyed("排除")
_QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)
_REF = re.compile(r"\[\[[^\]]+\]\]")




@dataclass
class Channel:
    driver: str          # 政策或宏观事件
    path: str            # 传导路径
    impact: Impact
    bases: list[Basis] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)    # 转述指向的文章标题
    # 九段写法的字段（旧四段写法读回时为空）
    channel: str = ""        # 九个渠道之一
    target: str = ""         # 落点变量
    sensitivity: str = ""    # 强中弱及理由
    lag: str = ""            # 即期 / 短期 / 中长期
    novelty: str = ""        # 新增 / 延续


@dataclass
class Track:
    """一个领先指标：宏观审核通过后按它定向取最新读数（spec §4）。"""
    indicator: str
    driver: str              # 对应哪条传导（事件名照抄）
    falsifier: str           # 出现什么读数说明链条不成立


@dataclass
class MacroView:
    stance: Stance | None = None
    stance_bases: list[Basis] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
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
            if c.channel:
                tail = "；".join(x for x in (f"落点：{c.target}" if c.target else "", c.lag, c.novelty) if x)
                lines.append(f"    · [{c.channel}] {c.driver} → {c.path} → {c.impact.value}（{tail}）")
            else:
                lines.append(f"    · {c.driver} → {c.path} → {c.impact.value}")
        if self.tracks:
            lines.append("  跟踪指标（对应传导；证伪读数）")
            for t in self.tracks:
                lines.append(f"    · {t.indicator}（对应「{t.driver}」；证伪：{t.falsifier}）")
        for ch, why in self.excluded:
            lines.append(f"  排除渠道：{ch} —— {why}")
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
        if len(p) >= 9:
            _parse_nine(p, view, corpus)
            continue
        if len(p) >= 5 and pick(p[0], list(CHANNELS)) is not None:
            # 首段是渠道名却不满九段：模型漏写了字段。不能落进旧四段解析（会把渠道当事件、事件当路径）
            view.read_notes.append(f"传导「{p[1]}」按九段写法缺字段（只有 {len(p)} 段），没读进来")
            continue
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
    for m in _TRACK.finditer(text):
        p = _cells(m.group(1))
        if len(p) < 3 or not all(p[:3]):
            view.read_notes.append(f"跟踪一行字段不足（需 3 段：指标 | 对应传导 | 证伪读数），没读进来：{m.group(1)}")
            continue
        view.tracks.append(Track(p[0], p[1], p[2]))
    for m in _EXCLUDE.finditer(text):
        p = _cells(m.group(1))
        if len(p) >= 2 and p[0]:
            view.excluded.append((p[0], p[1]))
    return view


def _parse_nine(p: list[str], view: MacroView, corpus) -> None:
    """九段：渠道 | 事件 | 路径 | 落点 | 影响 | 敏感度 | 时滞 | 新增/延续 | 依据。渠道、影响读不出整条跳过；
    时滞、新增/延续读不出照收、字段留空 —— 都记进 read_notes 交审核者。"""
    ch = pick(p[0], list(CHANNELS))
    if ch is None:
        view.read_notes.append(f"传导「{p[1]}」的渠道「{p[0]}」不是九个渠道之一（"
                               + "、".join(CHANNELS) + "），没读进来")
        return
    got = pick(p[4], [x.value for x in Impact])
    if got is None:
        view.read_notes.append(f"传导「{p[1]}」的影响「{p[4]}」不是 "
                               + "/".join(x.value for x in Impact) + " 之一，没读进来")
        return
    lag, nov = pick(p[6], list(LAGS)), pick(p[7], list(NOVELTY))
    if lag is None:
        view.read_notes.append(f"传导「{p[1]}」的时滞「{p[6]}」不是 {'/'.join(LAGS)} 之一")
    if nov is None:
        view.read_notes.append(f"传导「{p[1]}」的「{p[7]}」不是 新增/延续 之一")
    bases, prob, cites = Q.basis(" | ".join(p[8:]), corpus)
    if prob:
        view.read_notes.append(f"传导「{p[1]}」的依据：{prob}")
    view.channels.append(Channel(p[1], p[2], Impact(got), bases, cites, channel=ch, target=p[3],
                                 sensitivity=p[5], lag=lag or "", novelty=nov or ""))


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
    new = [c for c in view.channels if c.channel]
    if new and len({c.channel for c in new}) < MIN_CHANNELS:
        out.append(f"传导只涉及 {len({c.channel for c in new})} 个渠道（方法论要求至少两个不同渠道）")
    for c in new:
        if not any(t.driver and (t.driver in c.driver or c.driver in t.driver) for t in view.tracks):
            out.append(f"传导「{c.driver}」没有对应的跟踪指标（方法论要求每条传导至少一个）")
    if len(view.tracks) > MACRO_TRACK_MAX:
        out.append(f"跟踪指标 {len(view.tracks)} 个，超过上限 {MACRO_TRACK_MAX}（只检索前 {MACRO_TRACK_MAX} 个）")
    from ir_agent.citation import audit_prose

    bad = audit_prose({**{f"传导「{c.driver[:12]}」": c.driver + c.path + c.sensitivity
                          for c in view.channels},
                       **{f"跟踪「{t.indicator[:12]}」": t.indicator + t.falsifier for t in view.tracks}})
    if bad:
        out.append("描述里有账本外的数字：" + "、".join(bad))
    holder = [view] + view.channels
    attribute(holder, "", corpus)
    flat = [b for x in holder for b in x.bases]
    # 整稿都用转述时没有可核对的引文 —— validate([]) 会报「未提供任何依据」，对转述是假事实。
    if flat:
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
  另核：渠道与链条是否相符；落点变量是否真是链条终点；敏感度理由是否引用了敏感度段的占位符、
  方向与数值是否一致；「新增 / 延续」是否判对（延续性因素多半已体现在近期报表与股价里）；
  这条传导对应的跟踪指标能否提前反映它；排除渠道的理由是否成立。
- 遗漏：有没有漏掉对本行业最要紧的宏观因素。""")


PROMPT = """基于下面的宏观资料与行业敏感度，给出政策取向，以及宏观因素到本行业、本公司的传导路径。

**一、政策取向**（一条）
指货币与信用条件的总体取向（利率、流动性、信贷投放）。财政政策、产业政策写进下面的传导，
不在这里合并成一个词。
格式：`政策取向: <宽松|中性|收紧> | <依据>`

**二、传导路径**（至少两条，且至少涉及两个不同渠道）
「货币政策转向宽松」写在任何一篇研报里都成立，对判断本标的没有帮助。有帮助的是那条链：
政策或宏观变量 → 中间环节 → 本行业的哪个经营变量 → 利好还是利空。**写不出链条的政策判断等于没写。**

先按下面九个渠道逐一想一遍，再挑出对本行业**最要紧**的写（通常两到四条）：
- 利率：贷款与债券利率变动 → 融资成本、资本开支、按揭与消费信贷需求、估值的折现率
- 信贷：银行放贷意愿与额度、信用利差 → 依赖外部融资的企业，以及它们的下游客户
- 资产价格与财富：股市、房价变动 → 居民财富与消费意愿、抵押品价值
- 汇率：本币升值或贬值 → 出口报价与折算收入、进口原料成本、外币负债
- 预期与信心：政策表态、就业与收入预期 → 居民与企业的支出意愿
- 财政：政府支出、专项债、补贴、税收 → 基建、政府采购、补贴驱动的需求
- 监管与产业政策：准入、价格管制、集中采购、环保、产能政策 → 供给、价格、成本
- 大宗商品与成本：能源、原材料价格 → 投入成本，以及能否沿产业链转嫁
- 外需与贸易：海外需求、关税与贸易限制 → 出口量与出口价格

格式（九段，用 | 分隔）：
`传导: <渠道> | <政策或宏观事件> | <传导路径，每一环用 → 连接> | <落点变量> | <利好|中性|利空> | <敏感度> | <即期|短期|中长期> | <新增|延续> | <依据>`

- **渠道**：上面九个之一，名字照抄。
- **落点变量**：链条最终作用在哪个经营变量上 —— 需求量、价格、产品结构、单位成本、融资成本、
  信用成本、利息与投资收益（净现金公司的存款与理财收益）、估值（折现率）。可写多个，用「、」分隔。方法论预研会按这些变量拆营收与毛利率，
  落点写清楚，两边才对得上。
- **敏感度**：强、中、弱，并给理由。理由用行业周期敏感度的三个决定因素（Bodie、Kane、Marcus
  《投资学》）：需求的收入弹性、经营杠杆（固定成本占比）、财务杠杆。下面「行业与公司敏感度」
  一节给了占位符，引用它们，不要凭印象。再说明**本公司比行业更敏感还是更不敏感**：例如净现金
  的公司受加息的影响小于净负债的公司，甚至因利息收入受益；境外收入占比高的公司受汇率影响更大。
- **时滞**：即期（一个季度内）、短期（一年内）、中长期（一年以上）选一个。
- **新增 / 延续**：这是新的冲击，还是延续已久的因素 —— 延续性的因素多半已经体现在近期报表与股价里，
  不能当成新的利好或利空重复计入。
- **依据**：见下面的依据规则。

**三、跟踪指标**（每条传导至少一个，全文最多四个）
能**提前**看到这条链是否在兑现的领先指标（如新订单、开工、审批、预售、社会融资规模），以及出现什么
读数就说明链条不成立。审核通过后会按指标名去取最新读数，所以指标名要写成统计口径里的正式名称。
**账本里已有的序列（上面给了占位符的利率、货币供应、PMI、物价等）每次运行都会刷新，不要列为跟踪指标**
—— 跟踪只写账本之外、能提前反映这条链的指标。
格式：`跟踪: <指标名> | <对应哪条传导（事件名照抄）> | <证伪读数>`

示例（只示意格式与写法，分属不同行业；不要照抄）：

传导: 利率 | 按揭利率下调 | 月供负担下降 → 新房成交回升 → 交房后装修需求释放 | 需求量 | 利好 | 中：家居需求随竣工滞后释放，收入弹性大于一（引用占位符）；本公司净现金，不受融资成本拖累 | 中长期 | 延续 | <依据>
传导: 汇率 | 本币升值 | 外币报价折算为本币后收入减少 → 毛利率受压；进口原料折算成本下降，部分抵消 | 价格、单位成本 | 利空 | 强：境外收入占比高，原料多在国内采购，自然对冲有限 | 即期 | 新增 | <依据>
传导: 监管与产业政策 | 存款利率自律上限下调 | 存款付息率下降 → 负债成本下降 → 净息差收窄放缓 | 融资成本 | 利好 | 中：本行存款占负债比重高于同业 | 短期 | 新增 | <依据>
传导: 大宗商品与成本 | 原油价格上行 | 主要原料采购成本上升 → 行业集中度低、对下游议价弱，涨价难以转嫁 → 毛利率下降 | 单位成本 | 利空 | 强：原料占营业成本比重高，经营杠杆高 | 即期 | 新增 | <依据>
跟踪: 商品房销售面积 | 按揭利率下调 | 成交回升而竣工面积持续下滑
跟踪: 出口订单指数 | 本币升值 | 报价折算后订单仍回升

**四、排除的渠道**（可选，最多三条）
乍看相关、但你判断对本行业不重要的渠道写一行，审核者就不会误以为你漏了：
`排除: <渠道> | <为什么对本行业不重要>`

{basis_rules}

**要求**
- 资料里没讲到的，不要凭印象补；资料撑不起某条链时，宁可少写一条。
- 顺风逆风都要写：同一时期若有方向相反的力量（如需求回升而原料涨价），两条都写，不要只写一边。
- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，
  而那里的数字一律要求可溯源到账本。需要数字时用给你的占位符，否则改成不带数字的说法。
- 你的政策取向会与期限利差形态交叉核对，方向相反时需说明缘由。

{review_note}
{sensitivity}
{evidence}
"""


_SENSITIVITY = (("收入弹性（行业）", "industry.income_elasticity"),
                ("经营杠杆 DOL（行业中位）", "industry.dol_median"),
                ("财务杠杆 DFL", "industry.dfl_median"),
                ("总杠杆 DTL（行业）", "industry.dtl"),
                ("经营杠杆 DOL（本公司）", "company.dol"),
                ("净负债率（本公司，为负即净现金）", "company.net_debt_ratio"))


def sensitivity_block(ledger: FactLedger, period: str, as_of: date) -> str:
    """「行业与公司敏感度」段：账本里有的给占位符，没有的写「本次未取到」（spec §5.1）。"""
    from ir_agent.ledger import LookAheadError

    lines = ["行业与公司敏感度（占位符，引用时照抄）"]
    for label, key in _SENSITIVITY:
        try:
            ledger.get(key, period, as_of=as_of)
            lines.append(f"  {label}：[[{key}@{period}]]")
        except (KeyError, LookAheadError):
            lines.append(f"  {label}：本次未取到")
    lines.append("  未取到的项，对应的敏感度只能定性判断，正文要说明")
    return "\n".join(lines)


def for_prompt(corpus, macro_prompt: str = "") -> str:
    lines = ["宏观定性分析的资料"]
    if macro_prompt:
        lines.append("  可引用的宏观读数（占位符）：")
        lines.append("  " + macro_prompt.replace("\n", "\n  "))
    if corpus is not None and corpus.docs:
        from ir_agent.sources.external import TIER_LABEL, TIER_RULE, Tier
        seen_tiers = {d.tier for d in corpus.visible("macro")}
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
        for d in corpus.prompt_docs(module="macro"):       # 共用 + 本模块采集的（spec 2026-11-21 §6）
            lines.append(f"  ——【{TIER_LABEL[d.tier]}】{d.cite()}")
            if d.scope:
                lines.append(f"  口径（采集时自述，未核验）：{d.scope}")
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
           max_rounds: int = Q.MAX_ROUNDS, sensitivity: str = ""):
    """撰写者写、审核者审（子项目四）。返回 (结论, 证据文本)；审核不过抛
    `qual_review.ReviewStop`，模型接口错误原样抛出。"""
    evidence = for_prompt(corpus, macro_prompt)
    if sensitivity:
        evidence = sensitivity + "\n\n" + evidence        # 审核者看到与撰写者相同的敏感度段
    prompt = PROMPT.format(evidence=evidence, basis_rules=Q.BASIS_RULES,
                           review_note=Q.REVIEW_NOTE, sensitivity="")
    view, res, rounds = Q.run(
        client, judge, name="宏观定性", prompt=prompt, materials=evidence,
        read=lambda raw: parse(raw, corpus),
        facts=lambda v: facts(v, ledger, as_of, corpus),
        checklist=checklist, kinds=KINDS, rules=RULES, catalog=catalog,
        hay=evidence, ledger=ledger, as_of=as_of, max_rounds=max_rounds)
    view.conflicts = cross_check(view, ledger, as_of)
    view.review, view.rounds = res, rounds
    return view, evidence
