# -*- coding: utf-8 -*-
"""多空辩论 —— 两个子 agent 采集对立证据，裁决出带数值区间的平衡点。

**与 debate.py 的分工。** 那边批判**已成稿的正文**，抓不住「这批证据被单向
挑选过」；这边让多空双方从**同一份证据**里各自挑对自己有利的，再由裁决者在
两侧划出的区间内选一个平衡点。debate.py 仍服务第二阶段的成稿辩论，不动。

**行格式是管道分隔的四段/六段**，不是自由文本: 自由文本要靠猜，而猜错时
失败模式是「静默丢掉一条证据」—— 那比报错糟得多。
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ir_agent.basis import Basis

SIDES = ("多方", "空方")
_SEP = "｜"
_NONE = ("—", "-", "", "无")


class StanceFailed(Exception):
    """多空辩论产出不可用。`raw` 带着最后一次的完整模型输出。"""

    def __init__(self, msg: str, raw: str = "") -> None:
        super().__init__(msg)
        self.raw = raw


@dataclass(frozen=True)
class Evidence:
    side: str                    # 多方 | 空方
    subject: str                 # 驱动主题（辩论①）或因子标签（辩论②）
    claim: str
    bases: list[Basis] = field(default_factory=list)
    # 代码核验不过时的说明。**空串 = 核验通过。** 不阻断 —— 见 `annotate`。
    unverified: str = ""
    # 引文出自哪里（`Document.cite()` 或「年报原文」）。核验通过时记下，
    # 门② 的人据此点开原文 —— 「核验通过」本身不告诉人该去哪看。
    source: str = ""


@dataclass(frozen=True)
class Balance:
    subject: str
    bear: Decimal | None         # 辩论① 三个都是 None（只有文字结论）
    mid: Decimal | None
    bull: Decimal | None
    note: str


def _num(tok: str, what: str, raw: str) -> Decimal | None:
    """把一个数值字段解析成 Decimal；破折号给 None。

    **模糊写法必须报错而不是给 None。** 「约 8%」静默变成 None 会让区间校验
    （Task 2）失去对象 —— 一个本该被拦的平衡点会因为写法模糊而通过。
    """
    t = tok.strip()
    if t in _NONE:
        return None
    pct = t.endswith("%")
    # **pp 写的是百分点数字本身**：「0.5pp」读作 0.5，与判断项 pp 因子的读法一致。
    # 贵州茅台 2026-10-17：裁决者写对了「0.5pp」，此前这里不认、还提示「请写成
    # 0.08 或 8%」，辩论② 连挂三次作废 —— 生益科技把 5.47 个百分点写成 0.0547
    # 也是被这句报错引过去的。
    pp = t.lower().endswith("pp")
    body = t[:-1] if pct else (t[:-2].rstrip() if pp else t)
    try:
        v = Decimal(body)
    except InvalidOperation as e:
        raise StanceFailed(
            f"{what} 的数值 {t!r} 解析不出来 —— 请写成可解析的数：变化率写 8% 或 "
            f"0.08，pp 写百分点数字本身（0.5 或 0.5pp，不要换成小数）；不要写「约」"
            f"「左右」这类模糊词。", raw=raw) from e
    return v / 100 if pct else v


# 「发布者《标题》（日期，分级）」——**这正是我们在提示词里展示文档的格式**
# （`Document.cite()`）。模型照抄它当依据，说明它确实读过那篇文档，只是没给
# 逐字原文。生益科技 2026-10-13 试跑：一条证据写成这样，整场辩论② 作废。
_CITE = re.compile(r"《([^》]{2,})》")


def _cited_doc(basis_raw: str, corpus):
    """依据若是「《标题》」式的文档引用，按标题找语料库里的那篇。"""
    m = _CITE.search(basis_raw or "")
    if not m or corpus is None:
        return None
    title = m.group(1).strip()
    return next((d for d in getattr(corpus, "docs", None) or []
                 if (getattr(d, "title", "") or "").strip() == title), None)


def parse_evidence(raw: str, *, corpus=None,
                   dropped: list[str] | None = None) -> list[Evidence]:
    """解析两侧采集到的证据。格式：`侧别｜主题｜论点｜依据`。

    **依据写不成可核验形态的那条，保留并标注，不让整场作废。** spec 定的是
    「多空证据的依据核验不阻断，核验不过的逐条标注」，理由是「实测三场辩论
    被契约杀掉（占位符缺失、裸数字……），每次损失一整场」。第一版在这里对
    缺依据直接抛异常 —— 生益科技试跑时 25 条证据里 1 条依据写成了文档标题，
    辩论② 两次模型调用白花。

    `dropped` 给了列表时，**格式**错的行（段数不对、侧别写错）也跳过并记进
    列表，而不是抛异常；不给（单元测试的直接调用）则照旧抛 —— 让格式错误在
    测试里可见。整份输出一条可用证据都没有时，无论如何都失败。
    """
    from ir_agent.assume import _basis

    out: list[Evidence] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or _SEP not in line:
            continue
        bits = [x.strip() for x in line.split(_SEP)]
        if len(bits) != 4:
            msg = (f"每条证据必须是四段「侧别｜主题｜论点｜依据」，"
                   f"得到 {len(bits)} 段：{line[:60]}")
            if dropped is None:
                raise StanceFailed(msg, raw=raw)
            dropped.append(msg)
            continue
        side, subject, claim, basis_raw = bits
        if side not in SIDES:
            msg = f"侧别只能是「多方」或「空方」，得到 {side!r}"
            if dropped is None:
                raise StanceFailed(msg, raw=raw)
            dropped.append(f"{msg}：{line[:60]}")
            continue
        bases, prob = _basis(basis_raw)
        if prob:
            doc = _cited_doc(basis_raw, corpus)
            if doc is not None:
                note = (f"引了文档《{doc.title}》但没给逐字原文 —— 文档确实在"
                        "语料库里，论点是否出自它需人工点开核对")
                src = doc.cite()
            else:
                note = f"缺少可核验的依据：{basis_raw[:60] or '（空）'}"
                src = ""
            out.append(Evidence(side=side, subject=subject, claim=claim,
                                bases=[], unverified=note, source=src))
            continue
        out.append(Evidence(side=side, subject=subject, claim=claim, bases=bases))
    if not out:
        raise StanceFailed("一条可用证据都没有解析出来。", raw=raw)
    return out


def parse_balance(raw: str) -> list[Balance]:
    """解析裁决者的平衡点。格式：`平衡｜主题｜空方值｜平衡值｜多方值｜理由`。"""
    out: list[Balance] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("平衡") or _SEP not in line:
            continue
        bits = [x.strip() for x in line.split(_SEP)]
        if len(bits) != 6:
            raise StanceFailed(
                f"平衡点必须是六段「平衡｜主题｜空方值｜平衡值｜多方值｜理由」，"
                f"得到 {len(bits)} 段：{line[:60]}", raw=raw)
        _, subject, bear, mid, bull, note = bits
        vals = [_num(x, f"「{subject}」的平衡点", raw) for x in (bear, mid, bull)]
        given = [v is not None for v in vals]
        if any(given) and not all(given):
            raise StanceFailed(
                f"「{subject}」的三档要么全给数值、要么全写破折号，"
                f"不能只给一部分 —— 缺一个就无从校验区间。", raw=raw)
        out.append(Balance(subject=subject, bear=vals[0], mid=vals[1],
                           bull=vals[2], note=note))
    if not out:
        raise StanceFailed("一条平衡点都没有解析出来。", raw=raw)
    return out


@dataclass
class StanceResult:
    round_: str                  # "A"（预研之前）| "B"（因子采集之后）
    evidence: list[Evidence] = field(default_factory=list)
    balances: list[Balance] = field(default_factory=list)
    manual: str = ""             # 人工补写时的署名
    # 因格式错误被跳过的证据行（段数不对、侧别写错）。**跳过要说出来**，
    # 否则读的人以为模型只采了这么多。
    dropped: list[str] = field(default_factory=list)

    @property
    def unverified(self) -> list[Evidence]:
        return [e for e in self.evidence if e.unverified]

    def by_side(self, side: str) -> list[Evidence]:
        return [e for e in self.evidence if e.side == side]

    def summary(self) -> str:
        lines = [f"多空辩论{self.round_}：多方 {len(self.by_side('多方'))} 条，"
                 f"空方 {len(self.by_side('空方'))} 条，"
                 f"平衡点 {len(self.balances)} 项"]
        if self.manual:
            lines.append(f"  ⚠ 本轮多空输入由 {self.manual} **人工补写**")
        if self.unverified:
            lines.append(f"  ⚠ {len(self.unverified)} 条证据代码核验不过"
                         "（已标注，未阻断，需人工复核）")
        for b in self.balances:
            if b.mid is None:
                lines.append(f"  · {b.subject}：{b.note.strip()[:70]}")
            else:
                lines.append(f"  · {b.subject}：空 {b.bear} / 平 {b.mid} / "
                             f"多 {b.bull} —— {b.note.strip()[:50]}")
        for e in self.unverified:
            lines.append(f"  ⚠ [{e.side}] {e.subject}：{e.unverified}")
        if self.dropped:
            lines.append(f"  ⚠ {len(self.dropped)} 行因格式错误被跳过：")
            lines += [f"    – {x}" for x in self.dropped]
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "round": self.round_,
            "manual": self.manual,
            "evidence": [{"side": e.side, "subject": e.subject, "claim": e.claim,
                          "bases": [b.ref for b in e.bases],
                          "quotes": [b.quote for b in e.bases],
                          "unverified": e.unverified,
                          "source": e.source} for e in self.evidence],
            "dropped": list(self.dropped),
            "balances": [{"subject": b.subject,
                          "bear": None if b.bear is None else str(b.bear),
                          "mid": None if b.mid is None else str(b.mid),
                          "bull": None if b.bull is None else str(b.bull),
                          "note": b.note} for b in self.balances],
        }

    @classmethod
    def from_json(cls, d: dict) -> "StanceResult":
        """**占位符类依据的 `quote` 本来就是空串。**

        写成 `Basis.report(q)` 会在读回时抛 `InvalidBasis`（`basis.py:75`
        对空引文直接拒绝）—— 而读回发生在 `run_assume` 的开头，等于
        `stance_b.json` 一落盘就再也读不回来。按 `ref` 的形状分派，
        两者都给不出来的那一条跳过而不是崩掉整份文件。
        """
        evs = []
        for x in d.get("evidence") or []:
            bases = []
            for r, q in zip(x.get("bases") or [], x.get("quotes") or []):
                if (r or "").startswith("[["):
                    bases.append(Basis.fact(r))
                elif (q or "").strip():
                    bases.append(Basis.report(q))
            evs.append(Evidence(side=x["side"], subject=x["subject"],
                                claim=x.get("claim", ""), bases=bases,
                                unverified=x.get("unverified", ""),
                                source=x.get("source", "")))
        bals = []
        for x in d.get("balances") or []:
            g = lambda k: (None if x.get(k) in (None, "") else Decimal(x[k]))
            bals.append(Balance(subject=x["subject"], bear=g("bear"),
                                mid=g("mid"), bull=g("bull"),
                                note=x.get("note", "")))
        return cls(round_=d.get("round", ""), evidence=evs, balances=bals,
                   manual=d.get("manual", ""),
                   dropped=list(d.get("dropped") or []))


def check_interval(balances: list[Balance]) -> list[str]:
    """平衡值必须落在两侧划出的区间内。返回问题列表，空列表 = 通过。

    **裁决者不能自己造数，它只能在两侧给出的区间里选。** 这是这套设计里
    唯一能确定性保证的东西 —— 平衡点对两侧可问责。若允许它越界，「平衡点」
    就变成第三个独立观点，而那个观点没有任何一侧为它背书。

    端点算通过：平衡点取在端点上是合法结论（一侧完全说服了裁决者）。
    纯文字的平衡点（辩论①）跳过 —— 这条检查对它无从适用。
    """
    out: list[str] = []
    for b in balances:
        if b.mid is None:
            continue
        if b.bear > b.bull:
            out.append(f"「{b.subject}」两侧值倒挂：空方 {b.bear} 高于多方 "
                       f"{b.bull} —— 两侧搞反了，不是一个区间")
            continue
        if not (b.bear <= b.mid <= b.bull):
            out.append(f"「{b.subject}」的平衡值 {b.mid} 落在两侧区间 "
                       f"[{b.bear}, {b.bull}] 之外 —— 裁决者只能在两侧给出的"
                       f"区间里选，不能自己造一个数")
    return out


def annotate(evs: list[Evidence], *, ledger, as_of, body: str = "",
             corpus=None, evidence=None, context: str = "") -> list[Evidence]:
    """逐条核验证据的依据，**核验不过的打标记，不丢弃、不驳回**。

    为什么不阻断: 实测三场辩论被占位符契约杀掉，每次损失一整场。这个项目
    已经有一个现成的折中 —— 判断项那边的「N 行依据未能逐字核验（已标注，
    未阻断，需人工复核）」。核验+标注不降低成功率，又保留检出能力。

    **引文类依据对着什么核验。** 辩论没有「正文」—— 第一版把引文拿去和空串
    比（`body=""`），于是所有引文都判「在正文中找不到 —— 与凭空捏造无异」。
    生益科技试跑辩论① 的 10 条标注里：2 条逐字出现在语料库里（冤枉的），
    8 条引的是**本系统自己的定性分析结论**（「周期定位：衰退」「政策取向：
    宽松」）。现在：

    - 核验面用 `evidence`（`attribution.Evidence`，**撰写简报时用的同一个**：
      年报正文 + 语料库）。找到就核验通过，并记下出处供门② 点开
    - 只在 `context`（喂给辩手的本系统分析结论）里找到 —— **单独标出**：
      那是循环论证，不是证据；但也不是捏造，不能那么说
    - 哪里都找不到才说找不到
    - 占位符与外部资料（URL+引文）照旧走 `basis.validate`
    - 依据在解析时就写不成（`bases` 为空）的，保留解析时的标注
    """
    from ir_agent.basis import BasisKind, validate
    from ir_agent.sources.external import norm

    ctx = norm(context or "")
    plain = norm(body or "")
    out: list[Evidence] = []
    for e in evs:
        if not e.bases:
            out.append(e)                   # 解析时已标注，不覆盖
            continue
        probs: list[str] = []
        src = ""
        rest = []
        for b in e.bases:
            if b.kind is not BasisKind.REPORT:
                rest.append(b)
                continue
            q = norm(b.quote)
            hit = evidence.find(b.quote) if evidence is not None else None
            if hit:
                src = src or hit
            elif q and q in plain:
                src = src or "正文"
            elif q and q in ctx:
                probs.append(f"引的是本系统的分析结论「{b.quote[:30]}」，不是原始"
                             "资料（循环论证）—— 请人工判断这条论点另有何据")
            else:
                probs.append(f"引文「{b.quote[:30]}」在年报与语料库里都找不到")
        if rest:
            probs += validate(rest, ledger, body, as_of, corpus=corpus)
        out.append(Evidence(side=e.side, subject=e.subject, claim=e.claim,
                            bases=e.bases, source=src,
                            unverified="；".join(probs)[:200] if probs else ""))
    return out


_RULES_COMMON = """本轮为内部研究评审。你的产出只进辩论记录，不进研报正文。

**格式严格如下，每条一行，用全角竖线分隔，不要表格、不要编号：**

  <侧别>｜<主题>｜<论点>｜<依据>

依据只能是三种之一：
  · 账本占位符    [[字段@期间]]
  · 正文引文      「逐字照抄的一句话」
  · 外部资料      URL（抓取日期）「逐字引文」

**引公告、研报、网页时，依据写「逐字引文」**（从下面「语料库原文」里照抄
一句），不要只写文档标题 —— 只写标题的话，代码核验不了这条论点是否真出自
那篇文档。

**依据是这套机制的全部价值所在。** 给不出依据的论点请直接不写 —— 写了会被
标注为「代码核验不过」并原样交给人复核，那对谁都没有帮助。
"""

_BULL = _RULES_COMMON + """
你的角色是**多方**。从下面给定的证据里，挑出**支持更高估值**的材料。

只挑你能找到依据的。不要编造，不要为了凑数把中性的材料说成利好。
每条用「多方」开头。
"""

_BEAR = _RULES_COMMON + """
你的角色是**空方**。从下面给定的证据里，挑出**不利于估值**的材料。

只挑你能找到依据的。不要编造，不要为了凑数把中性的材料说成利空。
每条用「空方」开头。
"""

_JUDGE_A = """你的角色是**裁决者**。多空两侧各自挑出了材料，请就每个主题给出
一个双方都能接受的平衡判断。

**本轮还没有因子分解式，因此只给文字结论，三个数值位一律写破折号：**

  平衡｜<主题>｜—｜—｜—｜<平衡判断>

平衡判断要说清：多方的哪一条成立、空方的哪一条成立、合起来指向什么方向。
"""

_JUDGE_B = """你的角色是**裁决者**。多空两侧各自挑出了材料，请就每个因子给出
三档取值。

  平衡｜<因子>｜<空方值>｜<平衡值>｜<多方值>｜<理由>

**硬性约束：平衡值必须落在空方值与多方值之间**（端点可取）。你不能自己造一个
区间外的数 —— 平衡点要对两侧可问责。数值写成 0.08 或 8% 这类可解析的形式，
不要写「约」「左右」。
"""


def _corpus_block(corpus) -> str:
    """语料库原文，喂给辩手。与 `strategy` / `value_chain` / `macro_view`
    同一种渲染（分级规矩 + 每篇的引用行与截断正文）、同一份文档
    （`prompt_docs()`）。"""
    if corpus is None or not getattr(corpus, "docs", None):
        return ""
    from ir_agent.sources.external import TIER_LABEL, TIER_RULE

    docs = corpus.prompt_docs()
    lines = []
    for t in sorted({d.tier for d in docs}, key=lambda t: t.value):
        lines.append(f"  [{TIER_LABEL[t]}] 的可引用范围：{TIER_RULE[t]}")
    for d in docs:
        lines.append(f"  ——【{TIER_LABEL[d.tier]}】{d.cite()}")
        lines.append("  " + d.for_prompt().replace("\n", "\n  "))
    return "\n".join(lines)


def template_json(round_: str, subjects: list[str]) -> dict:
    """人工补写的待填模板。**代码给模板，不让人猜格式。**

    猜错格式的失败模式是「填完还是读不回」—— 人白做一遍工，而错在我们没给
    底本。
    """
    return {
        "round": round_,
        "manual": "",
        "evidence": [{"side": "多方", "subject": s, "claim": "",
                      "bases": [], "quotes": [], "unverified": ""}
                     for s in subjects],
        "balances": [{"subject": s, "bear": None, "mid": None, "bull": None,
                      "note": ""} for s in subjects],
    }


def run_stance(client, *, round_: str, subjects: list[str], context: str,
               catalog, ledger, as_of, body: str = "", corpus=None,
               max_attempts: int = 3) -> StanceResult:
    """跑一轮多空辩论：多方采证 → 空方采证 → 裁决平衡点。

    `round_` 为 "A" 时裁决只给文字结论（预研之前没有因子）；为 "B" 时给三档
    数值并强制区间校验。

    失败抛 `StanceFailed`，`raw` 带最后一次输出 —— 调用方据此落盘，人工补写
    时有底本可改。

    **`max_attempts` 管的不是「失败还是交人工」**，两者都会发生、只是先后：
    驳回重写是模型自己纠错，连续 `max_attempts` 次不行才落盘模板、停下来等人
    （`full._stop_for_manual`）。取 3 而不是 2，因为多一次模型重试约 $0.3，
    而交给人工的代价是一个人手写一份 JSON；`methodology.research` 的默认值
    同样是 3。
    """
    import sys

    facts = catalog.render_for_prompt()
    topics = "、".join(subjects)
    # **语料库原文必须给。** spec：「双方拿到同一份证据（事实清单 + 语料库 +
    # 定性结论）」。第一版只给了事实与 `context`（本系统的定性分析摘要），
    # 辩手于是只能引我们自己的结论 —— 生益科技试跑辩论① 被标注的 10 条里
    # 8 条就是这么来的。给的是 `corpus.prompt_docs()`：与定性三节**同一份**
    # 按档排好的文档，双方拿同一份。
    docs = _corpus_block(corpus)
    ctx = (f"\n**本轮要覆盖的{'因子' if round_ == 'B' else '主题'}**\n{topics}\n"
           f"\n**可用事实**\n{facts}\n"
           + (f"\n**语料库原文**\n{docs}\n" if docs else "")
           + f"\n**本系统的定性分析结论**（供参考；**它们不是证据**，"
             f"请引上面的原文或事实）\n{context}\n")

    print(f"多空辩论{round_}：多方采证…", file=sys.stderr)
    bull_raw = client(_BULL + ctx)
    print(f"多空辩论{round_}：空方采证…", file=sys.stderr)
    bear_raw = client(_BEAR + ctx)
    dropped: list[str] = []
    evs = annotate(parse_evidence(bull_raw + "\n" + bear_raw, corpus=corpus,
                                  dropped=dropped),
                   ledger=ledger, body=body, as_of=as_of, corpus=corpus,
                   evidence=getattr(catalog, "evidence", None), context=context)
    # **一方没有任何可用证据，这场辩论就没有意义** —— 裁决者只看到一面之词。
    # 比「总数为零才失败」更严：25 条全是多方的，同样不是辩论。
    for side in SIDES:
        if not any(e.side == side for e in evs):
            raise StanceFailed(f"{side}一条可用证据都没有 —— 单边的材料"
                               "裁决不出平衡点", raw=bull_raw + "\n" + bear_raw)

    listed = "\n".join(f"{e.side}｜{e.subject}｜{e.claim}" for e in evs)
    base = ((_JUDGE_A if round_ == "A" else _JUDGE_B) + ctx
            + f"\n**两侧挑出的材料**\n{listed}\n")
    # `raw` 要在循环外先绑定：`max_attempts` 若被传成 0，下面那句
    # `raise StanceFailed(..., raw=raw)` 会是 UnboundLocalError —— 本会话
    # 已经因为一个同形的 UnboundLocalError 崩掉过一整轮实跑。
    prompt, last, raw = base, "", ""
    for _ in range(max_attempts):
        print(f"多空辩论{round_}：裁决平衡点…", file=sys.stderr)
        raw = client(prompt)
        try:
            bals = parse_balance(raw)
        except StanceFailed as e:
            last = str(e)
            prompt = f"{base}\n---\n**上一稿被驳回**：{last}\n请据此重写全部行。\n"
            continue
        probs = check_interval(bals)
        if not probs:
            return StanceResult(round_=round_, evidence=evs, balances=bals,
                                dropped=dropped)
        last = "；".join(probs)
        prompt = f"{base}\n---\n**上一稿被驳回**：{last}\n请据此重写全部行。\n"
    raise StanceFailed(f"连续 {max_attempts} 次给不出可用的平衡点：{last}", raw=raw)


def load_manual(path, *, round_: str, by: str) -> StanceResult:
    """读回人工补写的多空输入。

    **区间校验对人和模型一视同仁。** 它守的是「平衡点对两侧可问责」，与谁
    填的无关 —— 人也会填错，而填错的后果和模型填错一样。

    未填的模板（全 None 的平衡点）判为「还没填」而不是「没有输入」：
    「没有」这条路已经被取消，每份研报的多空输入要么来自模型、要么来自人。
    """
    import json
    from pathlib import Path

    if not by.strip():
        raise StanceFailed("人工补写必须给出署名（--accept-manual 需配合 "
                           "--verified-by/--reviewed-by）—— 要有人负责才"
                           "谈得上「已补写」。")
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    r = StanceResult.from_json(d)
    r.round_, r.manual = round_, by.strip()
    if round_ == "B" and not any(b.mid is not None for b in r.balances):
        raise StanceFailed(f"{path} 的平衡点还没填 —— 模板里的三档仍是空的。")
    probs = check_interval(r.balances)
    if probs:
        raise StanceFailed("人工补写的平衡点不合法：" + "；".join(probs))
    return r
