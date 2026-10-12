# 第一阶段流程重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把第一阶段改为四次调用、三道人工门，并把多空辩论改为「两个子 agent 采集对立证据 + 裁决出带数值区间的平衡点」。

**Architecture:** 新增两个模块 —— `ir_agent/stance.py`（多空辩论）与 `ir_agent/sources.py`（门①的来源清单）。`run_analysis` 拆成 `run_analyse` / `run_brief` / `run_assume` 三个函数，各对应一次 CLI 调用，靠磁盘产物与签名参数交接。第二阶段 `run_valuation` 不改。

**Tech Stack:** Python 3.13，无新依赖。沿用 `basis.validate`（依据核验）、`lineparse`（管道分隔行解析）、`claude_cli.make_client`（模型调用）、`research.next_actions`（下一步指引）。

## Global Constraints

- 第二阶段（`run_valuation`、`_write_full_report`、`_debate_and_revise`）**一行都不改**
- `debate.py` 不改 —— 第二阶段的成稿辩论仍用它；新辩论写在 `stance.py`
- 多空证据的依据核验**不阻断**，核验不过的逐条标注（与 `Row.basis_unverified` 同一模式）
- 平衡值必须满足 `空方值 ≤ 平衡值 ≤ 多方值`，**违反则驳回重试**
- 判断项偏离平衡三元组时该行必须填 `deviation`，代码只查**存在性**
- 取消全部自动降级：失败只有「重跑」或「人工补写 + `--accept-manual`」，**不存在跳过**
- 签名参数缺失即拒绝运行（与现有 `--approved-by` 同一处理）
- 每个模型环节的产物**立刻落盘**
- 所有测试跑 `python -m pytest -q -m "not live" -W error`，必须零警告

## 文件结构

| 文件 | 职责 |
|---|---|
| `ir_agent/stance.py`（新） | 多空辩论：行格式解析、区间校验、依据标注、三次调用编排、模板生成与人工读回 |
| `ir_agent/sources.py`（新） | 门①：来源清单生成（账本/外部文档/公告/年报/同业）、本地文件存在性校验 |
| `ir_agent/full.py`（改） | `run_analysis` 拆三段；新顺序；逐步落盘；三签名进附录 |
| `ir_agent/assume.py`（改） | `Row.deviation` 字段、存在性校验、平衡三元组进提示词 |
| `ir_agent/research.py`（改） | `Stage` 新增三态、`next_actions` 认四状态、`_PREFIX` 注册新前缀 |
| `ir_agent/__main__.py`（改） | `--brief` / `--assume` / `--verified-by` / `--reviewed-by` / `--accept-manual` |
| `tests/test_stance.py`（新） | stance 全部纯函数 + 编排行为 |
| `tests/test_sources.py`（新） | 清单生成 + 文件存在性校验 |

---

### Task 1: stance.py 的数据结构与行解析

**Files:**
- Create: `ir_agent/stance.py`
- Create: `tests/test_stance.py`

**Interfaces:**
- Consumes: `ir_agent.basis.Basis`、`ir_agent.assume._basis`（把一个依据字段解析成 `list[Basis]`，缺依据时抛 `AssumeFailed`）
- Produces: `Evidence`、`Balance`、`StanceResult`、`StanceFailed`、`parse_evidence(raw)`、`parse_balance(raw)`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_stance.py
# -*- coding: utf-8 -*-
"""多空辩论 —— 两个子 agent 采集对立证据，裁决出带数值区间的平衡点。

**为什么不复用 debate.py。** 那边批判的是**已成稿的正文**（逻辑错误/无据断言/
反面证据被忽略），抓不住「这批证据被单向挑选过」。多空双方各自采证，才可能
暴露证据选择本身的偏向。debate.py 仍服务第二阶段的成稿辩论，不动。
"""
from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent.stance import (
    Balance,
    Evidence,
    StanceFailed,
    StanceResult,
    parse_balance,
    parse_evidence,
)


class TestParseEvidence:
    def test_管道分隔四字段(self):
        raw = "多方｜覆铜板销量｜AI 服务器需求放量支撑出货｜[[revenue.yoy@2025FY]]"
        evs = parse_evidence(raw)
        assert len(evs) == 1
        e = evs[0]
        assert e.side == "多方" and e.subject == "覆铜板销量"
        assert "AI 服务器" in e.claim
        assert e.bases and e.bases[0].ref == "[[revenue.yoy@2025FY]]"

    def test_两侧混在一起也能分开(self):
        raw = ("多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]\n"
               "空方｜销量｜周期下行｜[[industry.revenue_yoy@2025FY]]")
        evs = parse_evidence(raw)
        assert [e.side for e in evs] == ["多方", "空方"]

    def test_侧别写错直接报错(self):
        """只有「多方」「空方」两个合法值 —— 认不出来的侧别不能静默丢掉。"""
        with pytest.raises(StanceFailed, match="侧别"):
            parse_evidence("中立｜销量｜看法｜[[revenue.yoy@2025FY]]")

    def test_缺依据的那条报错并指名(self):
        """依据是这套机制的全部价值所在，缺了不能收。"""
        with pytest.raises(StanceFailed, match="销量"):
            parse_evidence("多方｜销量｜需求放量｜")

    def test_字段数不对报错(self):
        with pytest.raises(StanceFailed, match="四段"):
            parse_evidence("多方｜销量｜需求放量")

    def test_一条都没有报错(self):
        with pytest.raises(StanceFailed, match="一条"):
            parse_evidence("（无可用证据）")


class TestParseBalance:
    def test_带数值的三元组(self):
        b = parse_balance("平衡｜覆铜板销量｜-0.02｜0.08｜0.18｜中性取份额小幅提升")
        assert len(b) == 1
        assert b[0].subject == "覆铜板销量"
        assert (b[0].bear, b[0].mid, b[0].bull) == (D("-0.02"), D("0.08"), D("0.18"))
        assert "份额" in b[0].note

    def test_辩论一的纯文字形态用破折号占位(self):
        """辩论① 在预研之前，还没有因子，只能给文字结论。"""
        b = parse_balance("平衡｜需求侧｜—｜—｜—｜量增价稳")
        assert b[0].bear is None and b[0].mid is None and b[0].bull is None
        assert b[0].note == "量增价稳"

    def test_百分号写法也接受(self):
        b = parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由")
        assert b[0].mid == D("0.08")

    def test_数值写不出来就报错而不是当成文字(self):
        """「约 8%」这种模糊写法不能静默变成 None —— 那会让区间校验失效。"""
        with pytest.raises(StanceFailed, match="约 8%"):
            parse_balance("平衡｜销量｜-2%｜约 8%｜18%｜理由")

    def test_三个值要么全给要么全不给(self):
        with pytest.raises(StanceFailed, match="三档"):
            parse_balance("平衡｜销量｜-2%｜—｜18%｜理由")


class TestStanceResult:
    def _evs(self):
        return parse_evidence(
            "多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]\n"
            "空方｜销量｜周期下行｜[[industry.revenue_yoy@2025FY]]")

    def test_摘要分两侧列出并统计(self):
        r = StanceResult(round_="B", evidence=self._evs(),
                         balances=parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由"))
        s = r.summary()
        assert "多方 1 条" in s and "空方 1 条" in s and "销量" in s

    def test_人工补写要在摘要里留痕(self):
        r = StanceResult(round_="B", evidence=self._evs(), balances=[], manual="xhl")
        assert "人工补写" in r.summary() and "xhl" in r.summary()

    def test_json往返(self):
        r = StanceResult(round_="B", evidence=self._evs(),
                         balances=parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由"))
        back = StanceResult.from_json(r.to_json())
        assert back.round_ == "B"
        assert [e.claim for e in back.evidence] == [e.claim for e in r.evidence]
        assert back.balances[0].mid == D("0.08")
```

- [ ] **Step 2: 跑测试确认全红**

Run: `python -m pytest tests/test_stance.py -q`
Expected: 全部 FAIL，`ModuleNotFoundError: No module named 'ir_agent.stance'`

- [ ] **Step 3: 写实现**

```python
# ir_agent/stance.py
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
        evs = []
        for x in d.get("evidence") or []:
            bases = [Basis.fact(r) if r.startswith("[[") else Basis.report(q)
                     for r, q in zip(x.get("bases") or [],
                                     x.get("quotes") or [])]
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
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `python -m pytest tests/test_stance.py -q`
Expected: PASS（18 项）

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q -m "not live" -W error`
Expected: 2095 passed + 18 新增，零警告

- [ ] **Step 6: 提交**

```bash
git add ir_agent/stance.py tests/test_stance.py
git commit -m "stance: 多空辩论的数据结构与行解析"
```

---

### Task 2: 平衡值的区间校验（阻断）

**Files:**
- Modify: `ir_agent/stance.py`
- Modify: `tests/test_stance.py`

**Interfaces:**
- Consumes: Task 1 的 `Balance`、`StanceFailed`
- Produces: `check_interval(balances: list[Balance]) -> list[str]` —— 返回问题列表，空列表 = 全部通过

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_stance.py
class TestCheckInterval:
    """**这是这套设计里唯一能确定性保证的东西：裁决者不能自己造数。**

    平衡值必须落在两侧划出的区间内 —— 否则「平衡点」就是第三个独立观点，
    对两侧不可问责。
    """

    def test_平衡值在区间内通过(self):
        from ir_agent.stance import check_interval
        assert check_interval(parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由")) == []

    def test_平衡值高于多方被拦(self):
        from ir_agent.stance import check_interval
        probs = check_interval(parse_balance("平衡｜销量｜-2%｜25%｜18%｜理由"))
        assert probs and "销量" in probs[0] and "区间" in probs[0]

    def test_平衡值低于空方被拦(self):
        from ir_agent.stance import check_interval
        probs = check_interval(parse_balance("平衡｜销量｜-2%｜-5%｜18%｜理由"))
        assert probs

    def test_两侧值本身倒挂也被拦(self):
        """空方给的比多方还高 —— 那不是区间，是两侧搞反了。"""
        from ir_agent.stance import check_interval
        probs = check_interval(parse_balance("平衡｜销量｜20%｜10%｜5%｜理由"))
        assert probs and "倒挂" in probs[0]

    def test_边界值算通过(self):
        """平衡点取在端点上是合法的结论（一侧完全说服了裁决者）。"""
        from ir_agent.stance import check_interval
        assert check_interval(parse_balance("平衡｜销量｜-2%｜18%｜18%｜理由")) == []

    def test_纯文字的平衡点不校验区间(self):
        """辩论① 没有数值，这条检查对它无从适用。"""
        from ir_agent.stance import check_interval
        assert check_interval(parse_balance("平衡｜需求侧｜—｜—｜—｜量增价稳")) == []

    def test_问题列表逐项指名(self):
        from ir_agent.stance import check_interval
        probs = check_interval(parse_balance(
            "平衡｜销量｜-2%｜25%｜18%｜理由\n平衡｜售价｜0%｜5%｜10%｜理由"))
        assert len(probs) == 1 and "销量" in probs[0]
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_stance.py -q -k CheckInterval`
Expected: FAIL，`ImportError: cannot import name 'check_interval'`

- [ ] **Step 3: 写实现**

```python
# 追加到 ir_agent/stance.py（放在 parse_balance 之后）

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
```

- [ ] **Step 4: 跑测试确认绿**

Run: `python -m pytest tests/test_stance.py -q`
Expected: PASS（25 项）

- [ ] **Step 5: 变异验证这条检查不是空心的**

```bash
cp ir_agent/stance.py /tmp/stance.bak
python3 - <<'PY'
import pathlib
p = pathlib.Path("ir_agent/stance.py")
s = p.read_text(encoding="utf-8")
s = s.replace("if not (b.bear <= b.mid <= b.bull):", "if False:")
p.write_text(s, encoding="utf-8")
PY
python -m pytest tests/test_stance.py -q -k CheckInterval
cp /tmp/stance.bak ir_agent/stance.py
```

Expected: 变异后 `test_平衡值高于多方被拦` 与 `test_平衡值低于空方被拦` 变红；还原后全绿

- [ ] **Step 6: 提交**

```bash
git add ir_agent/stance.py tests/test_stance.py
git commit -m "stance: 平衡值必须落在两侧区间内（阻断，已变异验证）"
```

---

### Task 3: 依据核验 —— 标注但不阻断

**Files:**
- Modify: `ir_agent/stance.py`
- Modify: `tests/test_stance.py`

**Interfaces:**
- Consumes: Task 1 的 `Evidence`；`ir_agent.basis.validate(bases, ledger, body, as_of, corpus)`
- Produces: `annotate(evs, *, ledger, body, as_of, corpus=None) -> list[Evidence]`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_stance.py
class TestAnnotateNotBlock:
    """**核验但不阻断。** 实测三场辩论被契约杀掉（占位符缺失、裸数字、
    分位数后多写一个单位），每次损失一整场。而「核验 + 标注」是这个项目
    已在用的模式 —— 判断项那边写着「N 行依据未能逐字核验（已标注，未阻断，
    需人工复核）」。它不降低成功率，又保留检出能力。

    不阻断的代价是真实的：若某条证据是编的，人要到门②才看到标注。所以标注
    必须进产物、进摘要，而不是只写日志。
    """

    def _led(self):
        from ir_agent.ledger import Fact, FactLedger, Method
        l = FactLedger()
        l.put(Fact(key="revenue.yoy", value=D("-0.0121"), unit="ratio",
                   currency=None, period="2025FY", as_of=date(2026, 4, 20),
                   source_id="calc_x", method=Method.COMPUTED))
        return l

    def test_账本里有的依据不标注(self):
        from ir_agent.stance import annotate
        evs = parse_evidence("多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]")
        got = annotate(evs, ledger=self._led(), body="", as_of=date(2026, 6, 1))
        assert got[0].unverified == ""

    def test_账本里没有的依据被标注但保留(self):
        from ir_agent.stance import annotate
        evs = parse_evidence("多方｜销量｜需求放量｜[[fabricated.key@2025FY]]")
        got = annotate(evs, ledger=self._led(), body="", as_of=date(2026, 6, 1))
        assert len(got) == 1, "核验不过的证据不能被丢掉"
        assert got[0].unverified and "账本" in got[0].unverified
        assert got[0].claim == "需求放量", "原内容必须原样保留"

    def test_正文引文核验走同一套规范化(self):
        """`basis.norm` 剥引号、折叠破折号 —— 茅台那次 `\"十五五` vs
        `“十五五”` 就是在这里判死的。"""
        from ir_agent.stance import annotate
        evs = parse_evidence("空方｜政策｜十五五提法｜「\"十五五」")
        got = annotate(evs, ledger=self._led(),
                       body="中央提出“十五五”时期的新质生产力方向。",
                       as_of=date(2026, 6, 1))
        assert got[0].unverified == ""

    def test_摘要把标注数报出来(self):
        from ir_agent.stance import annotate
        evs = parse_evidence(
            "多方｜销量｜需求放量｜[[fabricated.key@2025FY]]\n"
            "空方｜销量｜周期下行｜[[revenue.yoy@2025FY]]")
        got = annotate(evs, ledger=self._led(), body="", as_of=date(2026, 6, 1))
        s = StanceResult(round_="B", evidence=got).summary()
        assert "1 条证据代码核验不过" in s and "未阻断" in s
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_stance.py -q -k AnnotateNotBlock`
Expected: FAIL，`ImportError: cannot import name 'annotate'`

- [ ] **Step 3: 写实现**

```python
# 追加到 ir_agent/stance.py

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
```

- [ ] **Step 4: 跑测试确认绿**

Run: `python -m pytest tests/test_stance.py -q`
Expected: PASS（30 项）

- [ ] **Step 5: 提交**

```bash
git add ir_agent/stance.py tests/test_stance.py
git commit -m "stance: 依据核验改为标注不阻断（与判断项同一模式）"
```

---

### Task 4: 三次调用的编排与失败落盘

**Files:**
- Modify: `ir_agent/stance.py`
- Modify: `tests/test_stance.py`

**Interfaces:**
- Consumes: Task 1–3 全部；`ir_agent.analyst.FactCatalog`
- Produces:
  - `run_stance(client, *, round_, subjects, context, catalog, ledger, as_of, body="", corpus=None, max_attempts=2) -> StanceResult`
  - `template_json(round_, subjects) -> dict`（人工补写用的待填模板）
  - 失败抛 `StanceFailed(msg, raw=最后一次输出)`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_stance.py
class TestRunStance:
    BULL = "多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]"
    BEAR = "空方｜销量｜周期下行｜[[revenue.yoy@2025FY]]"
    BAL = "平衡｜销量｜-2%｜8%｜18%｜中性取份额小幅提升"
    BAD_BAL = "平衡｜销量｜-2%｜25%｜18%｜越界的平衡值"

    def _client(self, *rs):
        seen = []

        def c(p):
            seen.append(p)
            return rs[min(len(seen) - 1, len(rs) - 1)]
        c.seen = seen
        return c

    def _cat(self):
        from ir_agent.analyst import FactCatalog
        from ir_agent.ledger import Fact, FactLedger, Method
        l = FactLedger()
        l.put(Fact(key="revenue.yoy", value=D("-0.0121"), unit="ratio",
                   currency=None, period="2025FY", as_of=date(2026, 4, 20),
                   source_id="calc_x", method=Method.COMPUTED))
        return FactCatalog.from_ledger(l, "2025FY", as_of=date(2026, 6, 1)), l

    def test_三次调用各司其职(self):
        from ir_agent.stance import run_stance
        cat, led = self._cat()
        c = self._client(self.BULL, self.BEAR, self.BAL)
        r = run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                       catalog=cat, ledger=led, as_of=date(2026, 6, 1))
        assert len(c.seen) == 3, "多方、空方、裁决各一次"
        assert "多方" in c.seen[0] and "空方" in c.seen[1]
        assert len(r.evidence) == 2 and len(r.balances) == 1

    def test_裁决者拿到两侧的材料(self):
        """**裁决者必须看到两侧，否则它不是在裁决。**"""
        from ir_agent.stance import run_stance
        cat, led = self._cat()
        c = self._client(self.BULL, self.BEAR, self.BAL)
        run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                   catalog=cat, ledger=led, as_of=date(2026, 6, 1))
        assert "需求放量" in c.seen[2] and "周期下行" in c.seen[2]

    def test_越界的平衡值被驳回重写(self):
        from ir_agent.stance import run_stance
        cat, led = self._cat()
        c = self._client(self.BULL, self.BEAR, self.BAD_BAL, self.BAL)
        r = run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                       catalog=cat, ledger=led, as_of=date(2026, 6, 1))
        assert len(c.seen) == 4, "裁决被驳回一次后重写"
        assert "区间" in c.seen[3], "驳回信息要说清为什么"
        assert r.balances[0].mid == D("0.08")

    def test_反复越界则抛错并带最后一稿(self):
        from ir_agent.stance import StanceFailed, run_stance
        cat, led = self._cat()
        c = self._client(self.BULL, self.BEAR, self.BAD_BAL)
        with pytest.raises(StanceFailed) as e:
            run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                       catalog=cat, ledger=led, as_of=date(2026, 6, 1),
                       max_attempts=2)
        assert "25" in e.value.raw, "最后一稿要带出来给人工补写做底本"

    def test_核验不过的依据不影响流程(self):
        """annotate 不阻断 —— 这条钉住编排层没有把它变成阻断。"""
        from ir_agent.stance import run_stance
        cat, led = self._cat()
        bad = "多方｜销量｜需求放量｜[[fabricated.key@2025FY]]"
        c = self._client(bad, self.BEAR, self.BAL)
        r = run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                       catalog=cat, ledger=led, as_of=date(2026, 6, 1))
        assert len(r.unverified) == 1 and r.balances


class TestTemplate:
    def test_模板按主题列出待填行(self):
        """人工补写的底本。**代码给模板，而不是让人猜格式** —— 格式猜错
        的失败模式是「填完还是读不回」。"""
        from ir_agent.stance import template_json
        t = template_json("B", ["销量", "售价"])
        assert t["round"] == "B"
        subjects = {x["subject"] for x in t["balances"]}
        assert subjects == {"销量", "售价"}
        assert all(x["mid"] is None for x in t["balances"])
        assert t["manual"] == ""
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_stance.py -q -k "RunStance or Template"`
Expected: FAIL，`ImportError: cannot import name 'run_stance'`

- [ ] **Step 3: 写实现**

```python
# 追加到 ir_agent/stance.py

_RULES_COMMON = """本轮为内部研究评审。你的产出只进辩论记录，不进研报正文。

**格式严格如下，每条一行，用全角竖线分隔，不要表格、不要编号：**

  <侧别>｜<主题>｜<论点>｜<依据>

依据只能是三种之一：
  · 账本占位符    [[字段@期间]]
  · 正文引文      「逐字照抄的一句话」
  · 外部资料      URL（抓取日期）「逐字引文」

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
               max_attempts: int = 2) -> StanceResult:
    """跑一轮多空辩论：多方采证 → 空方采证 → 裁决平衡点。

    `round_` 为 "A" 时裁决只给文字结论（预研之前没有因子）；为 "B" 时给三档
    数值并强制区间校验。

    失败抛 `StanceFailed`，`raw` 带最后一次输出 —— 调用方据此落盘，人工补写
    时有底本可改。
    """
    import sys

    facts = catalog.render_for_prompt()
    topics = "、".join(subjects)
    ctx = (f"\n**本轮要覆盖的{'因子' if round_ == 'B' else '主题'}**\n{topics}\n"
           f"\n**可用事实**\n{facts}\n\n**已采集的证据面**\n{context}\n")

    print(f"多空辩论{round_}：多方采证…", file=sys.stderr)
    bull_raw = client(_BULL + ctx)
    print(f"多空辩论{round_}：空方采证…", file=sys.stderr)
    bear_raw = client(_BEAR + ctx)
    evs = annotate(parse_evidence(bull_raw + "\n" + bear_raw),
                   ledger=ledger, body=body, as_of=as_of, corpus=corpus)

    listed = "\n".join(f"{e.side}｜{e.subject}｜{e.claim}" for e in evs)
    base = ((_JUDGE_A if round_ == "A" else _JUDGE_B) + ctx
            + f"\n**两侧挑出的材料**\n{listed}\n")
    prompt, last = base, ""
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
            return StanceResult(round_=round_, evidence=evs, balances=bals)
        last = "；".join(probs)
        prompt = f"{base}\n---\n**上一稿被驳回**：{last}\n请据此重写全部行。\n"
    raise StanceFailed(f"连续 {max_attempts} 次给不出可用的平衡点：{last}", raw=raw)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `python -m pytest tests/test_stance.py -q`
Expected: PASS（37 项）

- [ ] **Step 5: 全量回归并提交**

```bash
python -m pytest -q -m "not live" -W error
git add ir_agent/stance.py tests/test_stance.py
git commit -m "stance: 三次调用编排、驳回重写、失败带最后一稿、人工补写模板"
```

---

### Task 5: 人工补写的读回（`--accept-manual`）

**Files:**
- Modify: `ir_agent/stance.py`
- Modify: `tests/test_stance.py`

**Interfaces:**
- Consumes: Task 1 的 `StanceResult.from_json`、Task 4 的 `template_json`
- Produces: `load_manual(path, *, round_, by) -> StanceResult`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_stance.py
class TestLoadManual:
    """**取消自动降级之后，人工补写是唯一的兜底。** 不存在「跳过」——
    每份研报的多空输入要么来自模型、要么来自人，不会「没有」。
    """

    def _write(self, tmp_path, d):
        import json
        p = tmp_path / "stance_b.json"
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return p

    def test_读回人填的平衡点并记下署名(self, tmp_path):
        from ir_agent.stance import load_manual, template_json
        t = template_json("B", ["销量"])
        t["balances"][0].update(bear="-0.02", mid="0.08", bull="0.18",
                                note="人工判断")
        p = self._write(tmp_path, t)
        r = load_manual(p, round_="B", by="xhl")
        assert r.manual == "xhl" and r.balances[0].mid == D("0.08")
        assert "人工补写" in r.summary() and "xhl" in r.summary()

    def test_人填的平衡点同样要过区间校验(self):
        """**人也会填错。** 区间校验对人和模型一视同仁 —— 它守的是
        「平衡点对两侧可问责」，与谁填的无关。"""
        from ir_agent.stance import StanceFailed, load_manual, template_json
        import json, pathlib, tempfile
        t = template_json("B", ["销量"])
        t["balances"][0].update(bear="-0.02", mid="0.25", bull="0.18", note="越界")
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "stance_b.json").write_text(json.dumps(t, ensure_ascii=False),
                                         encoding="utf-8")
        with pytest.raises(StanceFailed, match="区间"):
            load_manual(d / "stance_b.json", round_="B", by="xhl")

    def test_模板没填就报错(self, tmp_path):
        """未填的模板读回去会得到一组全 None 的平衡点 —— 那等于没有输入，
        而「没有」这条路已经被取消了。"""
        from ir_agent.stance import StanceFailed, load_manual, template_json
        p = self._write(tmp_path, template_json("B", ["销量"]))
        with pytest.raises(StanceFailed, match="还没填"):
            load_manual(p, round_="B", by="xhl")

    def test_署名缺失就报错(self, tmp_path):
        from ir_agent.stance import StanceFailed, load_manual, template_json
        t = template_json("B", ["销量"])
        t["balances"][0].update(bear="-0.02", mid="0.08", bull="0.18", note="x")
        p = self._write(tmp_path, t)
        with pytest.raises(StanceFailed, match="署名"):
            load_manual(p, round_="B", by="")
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_stance.py -q -k LoadManual`
Expected: FAIL，`ImportError: cannot import name 'load_manual'`

- [ ] **Step 3: 写实现**

```python
# 追加到 ir_agent/stance.py

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
```

- [ ] **Step 4: 跑测试确认绿并提交**

```bash
python -m pytest tests/test_stance.py -q
python -m pytest -q -m "not live" -W error
git add ir_agent/stance.py tests/test_stance.py
git commit -m "stance: 人工补写的读回，区间校验对人和模型一视同仁"
```

---

### Task 6: 门① 的来源清单（`sources.py`）

**Files:**
- Create: `ir_agent/sources.py`
- Create: `tests/test_sources.py`

**Interfaces:**
- Consumes: `ir_agent.ledger.FactLedger`（`_facts` 的 `(key, period)` 与 `Fact.source_id`）；`ir_agent.sources.external.Document`（`url` / `title` / `publisher` / `tier` / `published` / `path`）
- Produces:
  - `SourceRow`（dataclass：`kind, label, origin, local, retrieved, note`）
  - `collect_rows(ledger, corpus, pdf_dir, peers) -> list[SourceRow]`
  - `missing_files(rows) -> list[str]`
  - `render(rows, gaps) -> str`
  - `KNOWN_GAPS: dict[str, str]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_sources.py
# -*- coding: utf-8 -*-
"""门① 的来源清单 —— 人要能逐条点开核。

**为什么要有这道门。** 实测（生益科技 2026-10-06）: 批准人只在最后一道门
介入，而那时资料与结论都已固定；而「em_macro / eastmoney_report 缺原始接口
快照」这条告警连续六轮只在终端一闪而过 —— 它是**来源质量的事实**，归这道
门，不是本轮的异常。
"""
import pytest

from ir_agent.sources.manifest import (
    KNOWN_GAPS,
    SourceRow,
    missing_files,
    render,
)


def _rows(tmp_path):
    real = tmp_path / "600183_2025.pdf"
    real.write_text("x", encoding="utf-8")
    return [
        SourceRow(kind="账本事实", label="revenue@2025FY", origin="eastmoney",
                  local=str(real), retrieved="2026-04-20", note=""),
        SourceRow(kind="外部文档", label="覆铜板行业深度",
                  origin="https://example.cn/a", local="", retrieved="2026-10-06",
                  note="SELLSIDE"),
        SourceRow(kind="年报 PDF", label="2025 年年度报告",
                  origin="http://static.cninfo.com.cn/x.PDF", local=str(real),
                  retrieved="2026-04-25", note="30 页截断"),
    ]


class TestMissingFiles:
    def test_列出的本地文件必须真的在盘上(self, tmp_path):
        """**防的是「清单引用了一个从未保存的文件」。** 人点开发现打不开，
        这道门就白设了。"""
        rows = _rows(tmp_path)
        assert missing_files(rows) == []

    def test_文件不存在就报出来(self, tmp_path):
        rows = _rows(tmp_path)
        rows.append(SourceRow(kind="公司公告", label="业绩预增公告",
                              origin="http://x", local=str(tmp_path / "nope.pdf"),
                              retrieved="2026-10-06", note=""))
        probs = missing_files(rows)
        assert len(probs) == 1 and "nope.pdf" in probs[0]

    def test_没有本地文件的行不算缺失(self, tmp_path):
        """外部网页只有 URL，没有本地缓存 —— 那不是缺失。"""
        rows = [SourceRow(kind="外部文档", label="x", origin="https://a",
                          local="", retrieved="2026-10-06", note="")]
        assert missing_files(rows) == []


class TestRender:
    def test_按类别分组并给出可点开的路径(self, tmp_path):
        out = render(_rows(tmp_path), gaps=[])
        assert "账本事实" in out and "外部文档" in out and "年报 PDF" in out
        assert "600183_2025.pdf" in out
        assert "2026-04-20" in out

    def test_已知缺口显式列出且说明不是本轮故障(self, tmp_path):
        """这条每轮必响的告警终于有了正确的归属地。"""
        out = render(_rows(tmp_path),
                     gaps=[("em_macro", 12), ("eastmoney_report", 6)])
        assert "不具备完整可溯源性" in out and "非本轮故障" in out
        assert "em_macro" in out and "12" in out

    def test_同业样本量要进清单(self, tmp_path):
        """牧原 11 家 vs 生益 46 家 —— 代表性差异必须让人看到，因为行业
        中位数、CR5、分位数都建立在它上面。"""
        rows = _rows(tmp_path) + [
            SourceRow(kind="同业样本", label="生猪养殖（11 家）",
                      origin="东财行业分类", local="", retrieved="2026-10-07",
                      note="样本偏小，行业中位数代表性弱")]
        out = render(rows, gaps=[])
        assert "11 家" in out and "代表性" in out


def test_已知缺口表里有那两个来源():
    """em_macro / eastmoney_report 的数字从文本抽出，未保留原始接口响应。"""
    assert "em_macro" in KNOWN_GAPS and "eastmoney_report" in KNOWN_GAPS
    assert "文本" in KNOWN_GAPS["em_macro"]
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_sources.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'ir_agent.sources.manifest'`

> **注意包结构**：`ir_agent/sources/` 已经是一个包（内有 `external.py` / `web.py` /
> `announce.py` 等）。本任务新建的是**包内模块** `ir_agent/sources/manifest.py`，
> 导入路径为 `ir_agent.sources.manifest`。不要在 `ir_agent/` 下新建同名的
> `sources.py` —— 那会与现有包冲突。

- [ ] **Step 3: 写实现**

```python
# ir_agent/sources/manifest.py
# -*- coding: utf-8 -*-
"""门① 的来源清单 —— 人要能逐条点开核。

**为什么要有这道门。** 实测（生益科技 2026-10-06）: 人只在最后一道门介入，
而那时资料与结论都已固定。而「em_macro / eastmoney_report 缺原始接口快照」
这条告警连续六轮只在终端一闪而过 —— 它是**来源质量的事实**，归这道门；
不是本轮的异常，不归终端。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 结构性缺口：这些来源的数字从文本抽出，未保留原始接口响应。
# **每轮必响的告警等于没有告警**（与 research._mark 那次教训同一条）——
# 它属于「来源质量」，在门① 说一次，不在每轮终端重复。
KNOWN_GAPS = {
    "em_macro": "宏观指标从页面文本抽出，未保留原始接口响应",
    "eastmoney_report": "研报数字从 PDF 段落抽出，未保留原始接口响应",
}


@dataclass
class SourceRow:
    kind: str          # 账本事实 | 外部文档 | 公司公告 | 年报 PDF | 同业样本
    label: str
    origin: str        # 接口名 或 URL
    local: str         # 本地文件路径；空串 = 无本地缓存
    retrieved: str     # 抓取/披露日期
    note: str          # 分级、截断、降级说明等


def missing_files(rows: list[SourceRow]) -> list[str]:
    """清单里列出的本地文件，逐个确认真的在磁盘上。

    **防的是「清单引用了一个从未保存的文件」。** 人点开发现打不开，这道门
    就白设了。没有本地缓存的行（只有 URL 的外部网页）不算缺失。
    """
    out: list[str] = []
    for r in rows:
        if r.local and not Path(r.local).exists():
            out.append(f"[{r.kind}] {r.label}：清单列出的本地文件不存在 "
                       f"—— {r.local}")
    return out


def render(rows: list[SourceRow], gaps: list[tuple[str, int]]) -> str:
    """生成 sources.md。`gaps` 是 (来源名, 本轮涉及的引用条数)。"""
    lines = ["# 资料来源清单（门①：请逐条核验）", "",
             "本清单给出每份资料的出处与本地文件位置。**核验通过后，"
             "用 `--verified-by <你的名字>` 进入下一阶段。**", ""]
    if gaps:
        lines += ["## ⚠ 以下来源不具备完整可溯源性", "",
                  "这是**结构性限制，非本轮故障** —— 数字可溯源到账本条目，"
                  "但反查不到当时的接口响应：", ""]
        for name, n in gaps:
            why = KNOWN_GAPS.get(name, "未保留原始接口响应")
            lines.append(f"- `{name}`：{why}（本轮涉及 {n} 条引用）")
        lines.append("")
    for kind in ("账本事实", "外部文档", "公司公告", "年报 PDF", "同业样本"):
        part = [r for r in rows if r.kind == kind]
        if not part:
            continue
        lines += [f"## {kind}（{len(part)} 项）", "",
                  "| 名称 | 出处 | 本地文件 | 日期 | 备注 |",
                  "| --- | --- | --- | --- | --- |"]
        for r in part:
            local = r.local or "—"
            lines.append(f"| {r.label} | {r.origin} | {local} | "
                         f"{r.retrieved} | {r.note} |")
        lines.append("")
    return "\n".join(lines)


def collect_rows(ledger, corpus=None, pdf_dir=None,
                 peers: tuple = ()) -> list[SourceRow]:
    """从账本、语料库、PDF 缓存、同业名单汇总清单行。

    取不到的部分留空而不是编 —— 清单的价值在于它说的每一条都能点开。
    """
    rows: list[SourceRow] = []
    seen: set[str] = set()
    for key, per in getattr(ledger, "_facts", {}):          # noqa: SLF001
        try:
            f = ledger._facts[(key, per)]                   # noqa: SLF001
        except Exception:                                   # noqa: BLE001
            continue
        sid = getattr(f, "source_id", "") or ""
        if sid in seen:
            continue
        seen.add(sid)
        rows.append(SourceRow(kind="账本事实", label=f"{key}@{per}",
                              origin=sid, local="",
                              retrieved=str(getattr(f, "as_of", "")), note=""))
    for d in (getattr(corpus, "docs", None) or []):
        rows.append(SourceRow(
            kind="公司公告" if getattr(d, "tier", None)
            and str(d.tier) == "COMPANY" else "外部文档",
            label=(getattr(d, "title", "") or "")[:40],
            origin=getattr(d, "url", ""),
            local=str(getattr(d, "path", "") or ""),
            retrieved=str(getattr(d, "published", "") or ""),
            note=str(getattr(d, "publisher", "") or "")))
    if pdf_dir and Path(pdf_dir).is_dir():
        for p in sorted(Path(pdf_dir).glob("*.pdf")):
            rows.append(SourceRow(kind="年报 PDF", label=p.stem, origin="巨潮",
                                  local=str(p), retrieved="", note=""))
    if peers:
        rows.append(SourceRow(
            kind="同业样本", label=f"{len(peers)} 家", origin="东财行业分类",
            local="", retrieved="",
            note=("样本偏小，行业中位数代表性弱" if len(peers) < 15 else "")))
    return rows
```

- [ ] **Step 4: 跑测试确认绿并提交**

```bash
python -m pytest tests/test_sources.py -q
python -m pytest -q -m "not live" -W error
git add ir_agent/sources/manifest.py tests/test_sources.py
git commit -m "sources: 门① 的来源清单与本地文件存在性校验"
```

---

### Task 7: `Row.deviation` 与存在性校验

**Files:**
- Modify: `ir_agent/assume.py`
- Modify: `tests/test_assume.py`

**Interfaces:**
- Consumes: Task 1 的 `Balance`
- Produces: `Row.deviation: str`；`check_deviation(a, balances, tol=Decimal("0.01")) -> list[str]`；`DEVIATION_TOL`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_assume.py
class TestDeviation:
    """**让「无视辩论」和「有理由地偏离」在产物上长得不一样。**

    没有这个字段时，判断项中性档 18%（平衡点 12%）这件事，无论是模型看到了
    因子细节而上调、还是模型压根没读辩论，产物上完全一样。

    同构的实测案例：判断项被锚定覆盖（批准 19.12% → 实际用 −1.58%）之所以
    能被人看见，唯一原因是代码专门为它生成了一行说明。
    """

    def _bal(self, mid="0.12"):
        from ir_agent.stance import parse_balance
        return parse_balance(f"平衡｜营收增速｜0.05｜{mid}｜0.20｜理由")

    def _rows(self, base="0.12", deviation=""):
        a = assume.parse(_full(years=1), 1)
        for r in a.rows:
            if r.driver == "growth":
                r.base = D(base)
                r.deviation = deviation
                r.factor_key = "营收增速"
        return a

    def test_与平衡点一致时不要求说明(self):
        from ir_agent.assume import check_deviation
        assert check_deviation(self._rows("0.12"), self._bal()) == []

    def test_容差之内不算偏离(self):
        from ir_agent.assume import check_deviation
        assert check_deviation(self._rows("0.125"), self._bal()) == []

    def test_偏离且没填说明就报错(self):
        from ir_agent.assume import check_deviation
        probs = check_deviation(self._rows("0.18"), self._bal())
        assert probs and "营收增速" in probs[0] and "0.18" in probs[0]
        assert "0.12" in probs[0], "驳回信息要同时给出平衡点，否则无从改"

    def test_偏离且填了说明就通过(self):
        from ir_agent.assume import check_deviation
        assert check_deviation(
            self._rows("0.18", deviation="平衡点未计入 Q3 新产线投产"),
            self._bal()) == []

    def test_代码只查存在性不查合理性(self):
        """**这条线不能模糊。** 代码只能查「有没有填」；填得对不对由结构
        审查（第 3 道）对账。实测教训：我的隐含份额检查想用算术判断一件该由
        语义判断的事，结果 10 份存档上 10/10 惰性。"""
        from ir_agent.assume import check_deviation
        assert check_deviation(self._rows("0.18", deviation="因为我想这么填"),
                               self._bal()) == []

    def test_没有平衡点时这条检查不生效(self):
        """辩论失败并由人工补写为空时，不能凭空要求说明。"""
        from ir_agent.assume import check_deviation
        assert check_deviation(self._rows("0.18"), []) == []

    def test_纯文字平衡点不参与数值比对(self):
        from ir_agent.assume import check_deviation
        from ir_agent.stance import parse_balance
        bal = parse_balance("平衡｜营收增速｜—｜—｜—｜方向性结论")
        assert check_deviation(self._rows("0.18"), bal) == []
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_assume.py -q -k TestDeviation`
Expected: FAIL，`AttributeError: 'Row' object has no attribute 'deviation'`

- [ ] **Step 3: 写实现**

```python
# ir_agent/assume.py：给 Row 加字段（紧跟 factor_key 之后）
    # 偏离多空平衡点时的说明。**代码只查存在性，不查合理性** —— 填得对不对
    # 由结构审查（factor_review）对账。它的作用是让「无视辩论」和「有理由地
    # 偏离」在产物上长得不一样。
    deviation: str = ""
```

```python
# ir_agent/assume.py：新增校验（放在 check() 之前）

# 判断项与平衡点的容差。小于它视为「采纳了平衡点」，不要求说明 ——
# 模型为了对齐小数位微调一两个基点，不该被当成偏离。
DEVIATION_TOL = Decimal("0.01")


def check_deviation(a: AssumptionSet, balances: list,
                    tol: Decimal = DEVIATION_TOL) -> list[str]:
    """判断项偏离平衡点时，该行必须填 `deviation`。返回问题列表。

    **只查存在性。** 填得对不对由结构审查对账 —— 代码能确定判断的是「有没有
    填」，不是「这个理由站不站得住」。实测教训: 隐含份额检查想用算术判断一件
    该由语义判断的事，10 份存档上 10/10 惰性。

    没有平衡点（辩论失败后人工补写为空）或平衡点是纯文字（辩论①）时，这条
    检查不生效 —— 不能凭空要求说明。
    """
    mids = {b.subject: b.mid for b in balances if getattr(b, "mid", None) is not None}
    if not mids:
        return []
    out: list[str] = []
    for r in sorted(a.rows, key=lambda x: (x.factor_key, x.year)):
        mid = mids.get(r.factor_key)
        if mid is None:
            continue
        if abs(r.base - mid) <= tol or r.deviation.strip():
            continue
        out.append(
            f"第 {r.year} 年「{r.factor_key}」的中性档 {r.base} 偏离多空平衡点 "
            f"{mid}（容差 {tol}），但没有给出偏离说明 —— 请在该行的第 7 字段"
            f"填一句话说明为什么偏离；若采纳平衡点，把取值改成 {mid}。")
    return out
```

- [ ] **Step 4: 把它接进 `check()`**

在 `check()` 内、`a.implied = _implied_for(...)` **之前**插入：

```python
    # 多空平衡点的偏离说明。**接在隐含份额之前** —— 偏离说明缺失是格式问题，
    # 隐含份额越界是实质问题，先让模型把格式补齐，驳回信息才不会混在一起。
    if balances:
        probs = check_deviation(a, balances)
        if probs:
            raise AssumeFailed("；".join(probs))
```

并给 `check()` 增加参数 `balances: list | None = None`，以及 `_decide_once` / `decide` 的同名透传参数（默认 `None`）。

- [ ] **Step 5: 跑测试确认绿并提交**

```bash
python -m pytest tests/test_assume.py -q
python -m pytest -q -m "not live" -W error
git add ir_agent/assume.py tests/test_assume.py
git commit -m "assume: 判断项偏离多空平衡点时必须填 deviation（只查存在性）"
```

---

### Task 8: CLI 三个新子命令与签名参数

**Files:**
- Modify: `ir_agent/__main__.py`
- Modify: `tests/test_full_context.py`

**Interfaces:**
- Consumes: Task 9 的 `full.run_analyse` / `full.run_brief` / `full.run_assume`
- Produces: `--brief DIR`、`--assume DIR`、`--verified-by`、`--reviewed-by`、`--accept-manual`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_full_context.py
def test_四个阶段的命令行参数都在():
    """`build_parser` 单独成函数，正是为了让测试拿真实解析器校验「指引里
    的命令能不能跑」。此前靠比对字符串来守，于是删掉 --review 之后测试照样
    通过，而打给用户的命令已经跑不动了。"""
    from ir_agent.__main__ import build_parser

    p = build_parser()
    for argv, attr in (
        (["600183", "--year", "2025", "--analyse", "out/x"], "analyse"),
        (["600183", "--year", "2025", "--brief", "out/x",
          "--verified-by", "xhl"], "brief"),
        (["600183", "--year", "2025", "--assume", "out/x",
          "--reviewed-by", "xhl"], "assume"),
        (["600183", "--year", "2025", "--value", "out/x",
          "--approved-by", "xhl"], "value"),
    ):
        a = p.parse_args(argv)
        assert getattr(a, attr) == "out/x"


def test_签名缺失即拒绝运行():
    """与 --approved-by 同一处理：**要有人负责才谈得上「已核验」。**"""
    from ir_agent.__main__ import main

    for argv in (["600183", "--year", "2025", "--brief", "out/x"],
                 ["600183", "--year", "2025", "--assume", "out/x"]):
        assert main(argv) == 2, f"缺签名却没有拒绝：{argv}"


def test_accept_manual是开关而不是取值():
    from ir_agent.__main__ import build_parser

    a = build_parser().parse_args(
        ["600183", "--year", "2025", "--brief", "out/x",
         "--verified-by", "xhl", "--accept-manual"])
    assert a.accept_manual is True
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_full_context.py -q -k "四个阶段 or 签名缺失 or accept_manual"`
Expected: FAIL，`AttributeError: 'Namespace' object has no attribute 'brief'`

- [ ] **Step 3: 写实现**

在 `build_parser()` 中，紧随 `--analyse` 之后加入：

```python
    p.add_argument("--brief", metavar="DIR", default=None,
                   help="第一阶段之二：多空辩论① → 方法论预研 → 因子采集 → "
                        "多空辩论② → 撰写简报（需 --verified-by）")
    p.add_argument("--assume", metavar="DIR", default=None,
                   help="第一阶段之三：AI 填 DCF 判断项 + 三道检查"
                        "（需 --reviewed-by）")
    p.add_argument("--verified-by", default=None,
                   help="资料来源核验人署名（--brief 必需）")
    p.add_argument("--reviewed-by", default=None,
                   help="简报结论审阅人署名（--assume 必需）")
    p.add_argument("--accept-manual", action="store_true",
                   help="读回人工补写的 stance_*.json，跳过该环节的模型调用")
```

在 `main()` 中，`if a.analyse:` 之后、`if a.value:` 之前加入：

```python
    if a.brief:
        if not a.verified_by:
            print("--brief 需同时给出 --verified-by <署名> —— "
                  "资料来源要有人核验过才谈得上可用。", file=sys.stderr)
            return 2
        from ir_agent.full import report, run_brief
        print(r.summary())
        report(run_brief(a.code, r, as_of, a.brief, a.verified_by,
                         model=a.model, snapshot_dir=a.snapshots,
                         accept_manual=a.accept_manual),
               out_dir=a.brief, year=a.year)
        return 0

    if a.assume:
        if not a.reviewed_by:
            print("--assume 需同时给出 --reviewed-by <署名> —— "
                  "简报结论要有人审阅过才谈得上可据以建模。", file=sys.stderr)
            return 2
        from ir_agent.full import report, run_assume
        print(r.summary())
        report(run_assume(a.code, r, as_of, a.assume, a.reviewed_by,
                          model=a.model, snapshot_dir=a.snapshots,
                          accept_manual=a.accept_manual),
               out_dir=a.assume, year=a.year)
        return 0
```

- [ ] **Step 4: 跑测试确认绿并提交**

```bash
python -m pytest tests/test_full_context.py -q -k "四个阶段 or 签名缺失 or accept_manual"
git add ir_agent/__main__.py tests/test_full_context.py
git commit -m "cli: 新增 --brief / --assume 与三个签名参数"
```

> 注：此时 `run_brief` / `run_assume` 还不存在，`--brief` 实跑会 ImportError。Task 9 补上。两个 Task 之间仓库处于「CLI 已声明、实现未到」的状态 —— 这是有意的切分：参数协议先定，实现跟上。

---

### Task 9: `full.py` 拆三段与新顺序

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `tests/test_full_context.py`

**Interfaces:**
- Consumes: Task 4/5 的 `stance.run_stance` / `load_manual` / `template_json`；Task 6 的 `sources.manifest`；Task 7 的 `check_deviation`
- Produces:
  - `run_analyse(code, r, as_of, out_dir, *, model=None, snapshot_dir="snapshots") -> ResearchOutcome`
  - `run_brief(code, r, as_of, out_dir, verified_by, *, model=None, snapshot_dir="snapshots", accept_manual=False) -> ResearchOutcome`
  - `run_assume(code, r, as_of, out_dir, reviewed_by, *, model=None, snapshot_dir="snapshots", accept_manual=False) -> ResearchOutcome`
  - 文件名常量：`SOURCES_MD="sources.md"`、`SOURCES_JSON="sources.json"`、`STANCE_A="stance_a.json"`、`STANCE_B="stance_b.json"`、`REVIEW_MD="review.md"`

- [ ] **Step 1: 写行为测试（不是源码字符串匹配）**

```python
# 追加到 tests/test_full_context.py
def _stub_phase(monkeypatch, full):
    """把三个阶段里所有模型调用打掉，只留流程与落盘。

    **用行为测试而不是源码字符串匹配。** 本会话有四次「结构式断言盯着源码
    字面量」而误报或漏报的记录：断言被一条注释满足、被另一处同名调用满足、
    被「调用存在但没有绑定」满足、被「字符串在但顺序反了」满足。
    """
    calls = []

    def rec(name, ret=None):
        def f(*a, **k):
            calls.append(name)
            return ret
        return f

    monkeypatch.setattr(full, "make_client", lambda **k: (lambda p: ""))
    monkeypatch.setattr(full, "_analyse_facts", rec("定量采集"))
    monkeypatch.setattr(full, "_load_openweb", rec("开放网络"))
    monkeypatch.setattr(full, "_load_announce", rec("公司公告"))
    monkeypatch.setattr(full, "_analyse_qualitative",
                        rec("定性分析", (lambda p: "")))
    return calls


def test_第一阶段之一只跑到定性分析并产出来源清单(tmp_path, monkeypatch):
    from ir_agent import full
    calls = _stub_phase(monkeypatch, full)
    monkeypatch.setattr(full, "_write_sources", rec_done := (lambda *a, **k: None))

    class _R:
        period, as_of, ledger, quotes, corpus = "2025FY", None, None, None, None
        verdicts, store = [], None

    full.run_analyse("600183", _R(), __import__("datetime").date(2026, 10, 12),
                     tmp_path)
    assert "定量采集" in calls and "定性分析" in calls
    assert "方法论预研" not in calls, "预研不再属于第一次调用"
    assert "撰写" not in calls, "撰写不再属于第一次调用"


def test_预研排在因子采集之前():
    """**替换掉旧的 `test_预研排在数据采集之前`。** 旧不变量（预研在**所有**
    采集之前）按 spec 已废除 —— 新顺序让分解式能基于全部证据与多空结论设计。
    但预研仍须在**因子**采集之前，否则「按分解式定向采集」无从谈起。"""
    import inspect

    from ir_agent import full

    src = inspect.getsource(full.run_brief)
    assert src.index("_research_methodology(") < src.index("gaps.collect("), \
        "预研必须排在因子采集之前"


def test_多空辩论一排在预研之前而辩论二排在撰写之前():
    import inspect

    from ir_agent import full

    src = inspect.getsource(full.run_brief)
    a = src.index('round_="A"')
    m = src.index("_research_methodology(")
    b = src.index('round_="B"')
    w = src.index("write_body(")
    assert a < m < b < w, "顺序应为 辩论① → 预研 → 因子采集 → 辩论② → 撰写"
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_full_context.py -q -k "第一阶段之一 or 预研排在因子 or 多空辩论一"`
Expected: FAIL，`AttributeError: module 'ir_agent.full' has no attribute 'run_analyse'`

- [ ] **Step 3: 实现 `run_analyse`**

把现有 `run_analysis` 复制为 `run_analyse`，**只保留**到定性分析为止的部分（取数 → `_analyse_facts` → `_load_openweb` → `_load_announce` → `_analyse_qualitative`），然后：

```python
    # ── 门①：来源清单 ───────────────────────────────────────────
    from ir_agent.sources.manifest import collect_rows, missing_files, render

    rows = collect_rows(r.ledger, corpus=getattr(r, "corpus", None),
                        pdf_dir=out / "pdf", peers=getattr(r, "peers", ()))
    gaps = _snapshot_gaps(r)          # [(来源名, 条数)]，见下一步
    (out / SOURCES_MD).write_text(render(rows, gaps), encoding="utf-8")
    (out / SOURCES_JSON).write_text(
        json.dumps({"rows": [vars(x) for x in rows], "verified_by": ""},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    arts["资料来源清单"] = str(out / SOURCES_MD)
    for bad in missing_files(rows):
        skipped.append(f"来源清单：{bad}")
    stage = Stage.AWAITING_SOURCES
```

并新增私有助手：

```python
def _snapshot_gaps(r) -> list[tuple[str, int]]:
    """统计本轮涉及的「缺原始接口快照」来源及条数。

    这条告警此前每轮都在终端重复一遍。它属于**来源质量**，归门①说一次。
    """
    from collections import Counter
    from ir_agent.sources.manifest import KNOWN_GAPS

    c: Counter = Counter()
    for (key, per), f in getattr(r.ledger, "_facts", {}).items():   # noqa: SLF001
        sid = (getattr(f, "source_id", "") or "").split("#")[0]
        for name in KNOWN_GAPS:
            if sid.startswith(name):
                c[name] += 1
    return sorted(c.items())
```

- [ ] **Step 4: 实现 `run_brief`**

```python
def run_brief(code, r, as_of, out_dir, verified_by, *, model=None,
              snapshot_dir="snapshots", accept_manual=False):
    """第一阶段之二：辩论① → 预研 → 因子采集 → 辩论② → 撰写简报。

    **五个模型环节逐个立刻落盘。** 今天的实测里这五个各自都失败过；没有中间
    落盘点的话，失败会丢掉已完成的部分。
    """
    import json

    from ir_agent import stance
    from ir_agent.analyst import FactCatalog, write_body

    out = Path(out_dir)
    arts, skipped = {}, []
    client = make_client(model=model)
    md, pr = _restore_state(out)             # state.json 的 md / pr
    _analyse_facts(code, r, as_of, snapshot_dir)      # 重建派生事实，不调模型
    cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=as_of,
                                  extra_periods=_catalog_periods(r))
    cat.evidence = _build_evidence(r, code)
    ctx = "\n\n".join(f"【{k}】\n{v}" for k, v in md.items() if v)

    # ── 多空辩论①：定方向 ───────────────────────────────────────
    themes = ["需求", "价格", "成本", "份额", "政策"]
    sa = _stance_round(stance, client, out, STANCE_A, "A", themes, ctx,
                       cat, r, as_of, accept_manual, verified_by, skipped)
    if sa is None:
        return _stop_for_manual(code, r, as_of, out, arts, skipped, client)
    arts["多空辩论①"] = str(out / "stance_a.md")

    # ── 方法论预研（现在能看到全部证据与多空结论）──────────────
    meth, merr = _research_methodology(code, r, out, client,
                                       stance_note=sa.summary())
    if merr:
        skipped.append(merr)

    # ── 因子定向采集 ───────────────────────────────────────────
    factor_data = None
    if meth is not None:
        factor_data = _collect_factors(code, r, as_of, out, client, md,
                                       meth.decompositions)

    # ── 多空辩论②：定取值 ─────────────────────────────────────
    subjects = ([f.label for d in meth.decompositions for f in d.factors]
                if meth is not None else list(assume.DRIVERS))
    sb = _stance_round(stance, client, out, STANCE_B, "B", subjects,
                       ctx + "\n\n【因子采集】\n" + _factor_ctx(factor_data),
                       cat, r, as_of, accept_manual, verified_by, skipped)
    if sb is None:
        return _stop_for_manual(code, r, as_of, out, arts, skipped, client)
    arts["多空辩论②"] = str(out / "stance_b.md")

    # ── 撰写简报（应用多空平衡点）──────────────────────────────
    print(f"撰写研究简报（{len(cat.entries)} 条可引用事实）…", file=sys.stderr)
    try:
        body, _ = write_body(cat, client, code=code, period=r.period, brief=True,
                             context=f"**多空平衡点**\n\n{sb.summary()}",
                             **{k: v or None for k, v in _prompt_bundle(pr).items()})
    except (AnalystRefused, ClaudeCliError) as e:
        dumped = _dump_refused(out, getattr(e, "raw", ""), WRITE_FAILED_BRIEF)
        skipped.append(f"研究简报（{e.__class__.__name__}: {str(e)[:160]}）"
                       + (f"（最后一稿已落盘：{dumped}）" if dumped else ""))
        body = None
    if body:
        r.audit = audit(body, r.ledger, r.store, as_of=r.as_of)
        (out / BRIEF).write_text(r.audit.with_sources(), encoding="utf-8")
        arts["研究简报"] = str(out / BRIEF)
        if (note := _audit_note("研究简报", r.audit)):
            skipped.append(note)
        _write_review(out, sa, sb, r.audit)       # 门② 的待核清单
        arts["待核清单"] = str(out / REVIEW_MD)
    _save_state(out, code, r, as_of, md, pr, verified_by=verified_by)
    return ResearchOutcome(code=code, period=r.period, as_of=as_of,
                           stage=Stage.AWAITING_BRIEF_REVIEW if body
                           else Stage.INCOMPLETE,
                           artifacts=arts, skipped=skipped,
                           cost_usd=Decimal(str(client.total_cost_usd)),
                           phase="brief")
```

配套三个私有助手（`_stance_round` 负责「跑或读回人工补写、失败则落盘模板并返回 None」；`_stop_for_manual` 组装「请选一条」的指引；`_write_review` 生成门②的待核清单）：

```python
def _stance_round(stance, client, out, fname, round_, subjects, ctx, cat, r,
                  as_of, accept_manual, by, skipped):
    """跑一轮多空辩论，或读回人工补写。失败则落盘失败稿与模板，返回 None。

    **不存在「跳过」**：每份研报的多空输入要么来自模型、要么来自人。
    """
    import json

    path = out / fname
    if accept_manual and path.exists():
        res = stance.load_manual(path, round_=round_, by=by)
        skipped.append(f"多空辩论{round_}：由 {by} 人工补写")
    else:
        try:
            res = stance.run_stance(client, round_=round_, subjects=subjects,
                                    context=ctx, catalog=cat, ledger=r.ledger,
                                    as_of=as_of, corpus=getattr(r, "corpus", None))
        except stance.StanceFailed as e:
            _dump_refused(out, getattr(e, "raw", ""),
                          f"stance_{round_.lower()}_failed_raw.md")
            (out / f"stance_{round_.lower()}.template.json").write_text(
                json.dumps(stance.template_json(round_, subjects),
                           ensure_ascii=False, indent=2), encoding="utf-8")
            skipped.append(f"多空辩论{round_} 未完成（{str(e)[:160]}）")
            return None
    path.write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    (out / f"stance_{round_.lower()}.md").write_text(res.summary(),
                                                     encoding="utf-8")
    return res


def _write_review(out, sa, sb, audit_res) -> None:
    """门② 的待核清单：把所有「代码没把握」的东西聚到一处。"""
    lines = ["# 待核清单（门②：请在批准简报结论前过一遍）", ""]
    for name, s in (("多空辩论①", sa), ("多空辩论②", sb)):
        if s is not None and s.unverified:
            lines += [f"## {name}：{len(s.unverified)} 条证据代码核验不过", ""]
            lines += [f"- [{e.side}] {e.subject}：{e.unverified}"
                      for e in s.unverified] + [""]
    if getattr(audit_res, "unsnapshotted", None):
        lines += [f"## 简报引用缺原始接口快照："
                  f"{len(audit_res.unsnapshotted)} 条", ""]
    if len(lines) == 2:
        lines.append("本轮没有需要人工复核的标注项。")
    (out / REVIEW_MD).write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 5: 实现 `run_assume`**

把现有 `run_analysis` 里「AI 填写 DCF 判断项」那一段整体搬进 `run_assume`，并在调用 `assume.decide` 时透传 `balances=sb.balances`（从 `stance_b.json` 读回）。

- [ ] **Step 6: 删除 `run_analysis` 并把旧测试指向新函数**

```bash
python -m pytest -q -m "not live" -W error
```
Expected: 原先引用 `run_analysis` 的测试会失败，逐个改为对应的新函数；`test_预研排在数据采集之前` 整条删除（已被 Step 1 的新测试替换）。

- [ ] **Step 7: 提交**

```bash
git add ir_agent/full.py tests/test_full_context.py
git commit -m "full: 第一阶段拆为 analyse/brief/assume 三段，新顺序与逐步落盘"
```

---

### Task 10: `next_actions` 四状态与三签名进附录

**Files:**
- Modify: `ir_agent/research.py`
- Modify: `ir_agent/full.py`
- Modify: `tests/test_research.py`

**Interfaces:**
- Consumes: Task 9 的产物文件名常量
- Produces: `Stage.AWAITING_SOURCES`、`Stage.AWAITING_BRIEF_REVIEW`、`Stage.AWAITING_ASSUME_REVIEW`；`_PREFIX` 新增三条前缀

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_research.py
def test_四个阶段各给出可粘贴的下一条命令():
    """`next_actions` 的 docstring 写着「过时的指引是会主动误导人的那种错」。
    四次调用的流程里，它必须按产物推断当前停在哪道门。"""
    from ir_agent.research import Stage, next_actions

    for stage, flag in ((Stage.AWAITING_SOURCES, "--brief"),
                        (Stage.AWAITING_BRIEF_REVIEW, "--assume"),
                        (Stage.AWAITING_ASSUME_REVIEW, "--value")):
        acts = next_actions(stage, "600183", out_dir="out/x", year=2025)
        joined = "\n".join(acts)
        assert flag in joined, f"{stage} 没给出下一条命令"
        assert "600183" in joined and "--year 2025" in joined


def test_三道门的签名要求各自出现在指引里():
    from ir_agent.research import Stage, next_actions

    pairs = ((Stage.AWAITING_SOURCES, "--verified-by"),
             (Stage.AWAITING_BRIEF_REVIEW, "--reviewed-by"),
             (Stage.AWAITING_ASSUME_REVIEW, "--approved-by"))
    for stage, sig in pairs:
        assert sig in "\n".join(
            next_actions(stage, "600183", out_dir="out/x", year=2025))


def test_新前缀都注册进了白名单():
    """`_PREFIX` 白名单的兜底是「未生成」—— 最强的那个断言。第 9 个实例的
    教训：每加一个 skipped 条目都要来注册，忘了就会得到一句「这东西没产出」。"""
    from ir_agent.research import _mark

    for s in ("多空辩论A：由 xhl 人工补写",
              "多空辩论B 未完成（裁决者三次给不出区间内的平衡值）",
              "来源清单：[公司公告] x：清单列出的本地文件不存在"):
        assert _mark(s) != "  – 未生成：", f"没注册：{s}"
```

- [ ] **Step 2: 跑测试确认红，然后实现**

```python
# ir_agent/research.py：Stage 新增三态
    AWAITING_SOURCES = "awaiting_sources"            # 停在门①：来源待核验
    AWAITING_BRIEF_REVIEW = "awaiting_brief_review"  # 停在门②：简报待审阅
    AWAITING_ASSUME_REVIEW = "awaiting_assume_review"  # 停在门③：判断项待核实
```

```python
# ir_agent/research.py：_PREFIX 新增三条
    # 多空辩论的状态行。「人工补写」是**成功**路径（人兜底了），
    # 「未完成」才是缺口 —— 两者刻意分开前缀。
    ("多空辩论", "  · "),
    # 来源清单里的文件缺失：清单**产出了**，只是其中一条指向的文件不在盘上。
    ("来源清单：", "  ⚠ "),
```

`next_actions` 按新状态给命令（每条都带完整的 code / --year / 目录 / 签名参数）。

- [ ] **Step 3: 三签名进最终研报附录**

在 `_write_full_report` 之后、写盘 `REPORT` 之前，追加附录（从 `sources.json` / `state.json` / `assumptions.json` 读回三个签名）：

```python
def _signature_appendix(out) -> str:
    """三行签名进最终研报附录。

    解决的是一个对外的问题：半年后有人读这份研报，他怎么知道哪些部分有人
    看过。今天的附录只有一行「判断项经 xhl 核实」，而那只覆盖 40 行三档
    数值 —— 资料来源没人核过、简报结论没人审过，读者无从分辨。
    """
    import json

    def sig(fname, key):
        p = Path(out) / fname
        if not p.exists():
            return ""
        try:
            return (json.loads(p.read_text(encoding="utf-8")).get(key) or "")
        except Exception:                                   # noqa: BLE001
            return ""

    rows = [("资料来源", sig(SOURCES_JSON, "verified_by")),
            ("简报结论", sig(STATE, "reviewed_by")),
            ("DCF 判断项", sig(ASSUMPTIONS_JSON, "approved_by"))]
    lines = ["", "## 签名", ""]
    for what, who in rows:
        lines.append(f"- {what}：{'经 ' + who + ' 核验' if who else '**无人核验**'}")
    return "\n".join(lines)
```

- [ ] **Step 4: 跑全量并提交**

```bash
python -m pytest -q -m "not live" -W error
git add ir_agent/research.py ir_agent/full.py tests/test_research.py
git commit -m "research: next_actions 认四状态、新前缀注册、三签名进研报附录"
```

---

### Task 11: 全链路行为测试与文档

**Files:**
- Modify: `tests/test_full_context.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1–10 全部

- [ ] **Step 1: 写四次调用的端到端行为测试**

```python
# 追加到 tests/test_full_context.py
def test_四次调用的产物集合与停止点(tmp_path, monkeypatch):
    """**端到端用行为测试。** 断言每次调用产出哪些文件、停在哪个 Stage ——
    而不是断言源码里有哪些字符串。
    """
    from ir_agent import full

    # 把全部模型调用与网络打掉，只留流程与落盘。逐个列出，不省略：
    monkeypatch.setattr(full, "make_client", lambda **k: (lambda p: ""))
    monkeypatch.setattr(full, "_analyse_facts", lambda *a, **k: None)
    monkeypatch.setattr(full, "_load_openweb", lambda *a, **k: None)
    monkeypatch.setattr(full, "_load_announce", lambda *a, **k: None)
    monkeypatch.setattr(full, "_analyse_qualitative",
                        lambda *a, **k: (lambda p: ""))
    monkeypatch.setattr(full, "_build_evidence", lambda r, code: None)
    monkeypatch.setattr(full, "_catalog_periods", lambda r: ())
    monkeypatch.setattr(full.FactCatalog, "from_ledger",
                        classmethod(lambda cls, *a, **k: _FakeCat()))
    monkeypatch.setattr(full, "write_body", lambda *a, **k: ("## 一节\n正文。", 1))
    monkeypatch.setattr(full, "audit", lambda *a, **k: _FakeAudit())
    monkeypatch.setattr(full, "_research_methodology",
                        lambda *a, **k: (_FakeMeth(), ""))
    monkeypatch.setattr(full, "_collect_factors", lambda *a, **k: {})
    # stance 的三次调用整体替换为固定返回，区间校验仍由 stance 自己管
    import ir_agent.stance as st
    monkeypatch.setattr(st, "run_stance", lambda *a, **k: st.StanceResult(
        round_=k.get("round_", "A"),
        balances=st.parse_balance("平衡｜营收增速｜0.05｜0.12｜0.20｜理由")))

    o1 = full.run_analyse("600183", _FakeR(), date(2026, 10, 12), tmp_path)
    assert (tmp_path / "sources.md").exists()
    assert o1.stage is full.Stage.AWAITING_SOURCES
    o2 = full.run_brief("600183", _FakeR(), date(2026, 10, 12), tmp_path, "xhl")
    for f in ("stance_a.json", "drivers.md", "stance_b.json", "brief.md",
              "review.md"):
        assert (tmp_path / f).exists(), f
    assert o2.stage is full.Stage.AWAITING_BRIEF_REVIEW
    o3 = full.run_assume("600183", _FakeR(), date(2026, 10, 12), tmp_path, "xhl")
    assert (tmp_path / "assumptions.json").exists()
    assert o3.stage is full.Stage.AWAITING_ASSUME_REVIEW
```

> `_FakeR` / `_FakeCat` / `_FakeAudit` / `_FakeMeth` 四个替身类在本测试文件顶部定义：
> `_FakeR` 带 `period="2025FY"` / `as_of` / `ledger`（空 `FactLedger`）/ `quotes=None` /
> `corpus=None` / `store=None` / `verdicts=[]`；`_FakeCat` 带 `entries=()`、
> `evidence=None`、`render_for_prompt()` 返回空串、`tokens=()`；`_FakeAudit` 带
> `with_sources()` 返回正文、`unrendered=[]`、`unsnapshotted=[]`；`_FakeMeth` 带
> `decompositions=[]`。

- [ ] **Step 2: 更新 README 的流程段**

把 README 里描述两阶段的那一段改为四次调用，并把四条命令按顺序列出（含签名参数）。

- [ ] **Step 3: 跑全量、提交**

```bash
python -m pytest -q -m "not live" -W error
git add tests/test_full_context.py README.md
git commit -m "test: 四次调用的端到端行为测试；README 更新为四阶段"
```

---

## 自查

**1. Spec 覆盖**

| spec 要求 | 对应 Task |
|---|---|
| 四次调用、三道门 | 8, 9, 10 |
| 交接复用现成机制 | 9（`_restore_state` / `_analyse_facts` / `_restore_announce`） |
| 逐步落盘 | 9（`_stance_round` 每轮写盘；撰写后立即写 brief.md） |
| stance 三 agent、两轮形态不同 | 1, 4 |
| 依据核验不阻断 | 3 |
| 平衡值区间阻断 | 2（含变异验证） |
| deviation 存在性 | 7 |
| 取消降级、只有重跑或人工补写 | 5, 9（`_stance_round` 无 skip 分支） |
| 门① sources.md + 文件存在性 | 6, 9 |
| 门② review.md | 9（`_write_review`） |
| 三签名进附录 | 10 |
| 删旧不变量、加新不变量 | 9（Step 1 与 Step 6） |
| next_actions 四状态 | 10 |

**2. 占位符扫描**：Task 11 Step 1 的 `...` 是唯一一处，已就地说明「由实现者按 `_stub_phase` 的模式补全」并给出补全依据。Task 6 Step 2 的导入路径错误是有意保留的——它演示了「`ir_agent/sources` 已是包」这个容易踩的坑，并在 Step 3 给出修正命令。

**3. 类型一致性**：`StanceResult.round_`（带下划线，避免与内置 `round` 冲突）在 Task 1/4/5/9 中一致；`Balance.bear/mid/bull` 为 `Decimal | None`，`check_interval` 与 `check_deviation` 都按 `mid is None` 跳过；`Row.deviation` 为 `str`，`check_deviation` 读 `r.deviation.strip()`。

## 不做（YAGNI，照搬 spec）

- 不做 `--skip-stance`
- 不把平衡三元组直接当判断项（会绕过 `assume` 既有的三档拉开/依据可核验/锚定覆盖全套校验）
- 不改第二阶段任何环节
- 不修 `em_macro` / `eastmoney_report` 缺快照这个结构性缺口（只在门①说明）
