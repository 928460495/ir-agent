# -*- coding: utf-8 -*-
"""多空辩论 —— 两个子 agent 采集对立证据，裁决出带数值区间的平衡点。

**与 debate.py 的分工。** 那边批判**已成稿的正文**，抓不住「这批证据被单向
挑选过」；这边让多空双方从**同一份证据**里各自挑对自己有利的，再由裁决者在
两侧划出的区间内选一个平衡点。debate.py 仍服务第二阶段的成稿辩论，不动。

**行格式是管道分隔的四段/六段**，不是自由文本: 自由文本要靠猜，而猜错时
失败模式是「静默丢掉一条证据」—— 那比报错糟得多。
"""

from __future__ import annotations

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
    # 代码核验不过时的说明。**空串 = 核验通过。** 不阻断 —— 见 Task 3。
    unverified: str = ""


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
    try:
        v = Decimal(t[:-1] if pct else t)
    except InvalidOperation as e:
        raise StanceFailed(
            f"{what} 的数值 {t!r} 解析不出来 —— 请写成 0.08 或 8% 这类"
            f"可解析的数，不要写「约」「左右」这类模糊词。", raw=raw) from e
    return v / 100 if pct else v


def parse_evidence(raw: str) -> list[Evidence]:
    """解析两侧采集到的证据。格式：`侧别｜主题｜论点｜依据`。"""
    from ir_agent.assume import AssumeFailed, _basis

    out: list[Evidence] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or _SEP not in line:
            continue
        bits = [x.strip() for x in line.split(_SEP)]
        if len(bits) != 4:
            raise StanceFailed(
                f"每条证据必须是四段「侧别｜主题｜论点｜依据」，"
                f"得到 {len(bits)} 段：{line[:60]}", raw=raw)
        side, subject, claim, basis_raw = bits
        if side not in SIDES:
            raise StanceFailed(
                f"侧别只能是「多方」或「空方」，得到 {side!r}", raw=raw)
        try:
            bases = _basis(basis_raw, f"「{subject}」的{side}证据")
        except AssumeFailed as e:
            raise StanceFailed(str(e), raw=raw) from e
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
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "round": self.round_,
            "manual": self.manual,
            "evidence": [{"side": e.side, "subject": e.subject, "claim": e.claim,
                          "bases": [b.ref for b in e.bases],
                          "quotes": [b.quote for b in e.bases],
                          "unverified": e.unverified} for e in self.evidence],
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
                                unverified=x.get("unverified", "")))
        bals = []
        for x in d.get("balances") or []:
            g = lambda k: (None if x.get(k) in (None, "") else Decimal(x[k]))
            bals.append(Balance(subject=x["subject"], bear=g("bear"),
                                mid=g("mid"), bull=g("bull"),
                                note=x.get("note", "")))
        return cls(round_=d.get("round", ""), evidence=evs, balances=bals,
                   manual=d.get("manual", ""))


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


def annotate(evs: list[Evidence], *, ledger, body: str, as_of,
             corpus=None) -> list[Evidence]:
    """逐条核验证据的依据，**核验不过的打标记，不丢弃、不驳回**。

    为什么不阻断: 实测三场辩论被占位符契约杀掉（占位符缺失、裸数字、
    分位数后多写一个单位），每次损失一整场。而这个项目已经有一个现成的
    折中 —— 判断项那边的「N 行依据未能逐字核验（已标注，未阻断，需人工
    复核）」。核验+标注不降低成功率，又保留检出能力。

    代价：若某条证据是编的，人要到门②才看到标注（而那时预研、因子采集、
    简报都已基于它跑完）。所以标注必须进产物与摘要，不能只写日志。
    """
    from ir_agent.basis import validate

    out: list[Evidence] = []
    for e in evs:
        probs = validate(e.bases, ledger, body, as_of, corpus=corpus)
        out.append(Evidence(side=e.side, subject=e.subject, claim=e.claim,
                            bases=e.bases,
                            unverified="；".join(probs)[:200] if probs else ""))
    return out
