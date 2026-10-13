# 第一阶段流程重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把第一阶段改为四次调用、三道人工门，并把多空辩论改为「两个子 agent 采集对立证据 + 裁决出带数值区间的平衡点」。

**Architecture:** 新增两个模块 —— `ir_agent/stance.py`（多空辩论）与 `ir_agent/sources.py`（门①的来源清单）。`run_analysis` 拆成 `run_analyse` / `run_brief` / `run_assume` 三个函数，各对应一次 CLI 调用，靠磁盘产物与签名参数交接。第二阶段 `run_valuation` 不改。

**Tech Stack:** Python 3.13，无新依赖。沿用 `basis.validate`（依据核验）、`lineparse`（管道分隔行解析）、`claude_cli.make_client`（模型调用）、`research.next_actions`（下一步指引）。

## Global Constraints

- 第二阶段（`run_valuation`、`_write_full_report`、`_debate_and_revise`）**一行都不改**。
  这条不靠记性守，每个改 `full.py` 的 Task 在提交前跑一次：
  ```bash
  git diff main -- ir_agent/full.py | grep -c "^[+-].*\(run_valuation\|_write_full_report\|_debate_and_revise\)"
  ```
  非零就停下来逐行看。`run_analysis` 与 `run_valuation` 在同一个文件里相邻，
  而本次要重写的正是前者 —— 「我记得没动它」在 2000 行的 diff 上不是证据。
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
| `ir_agent/research.py`（改） | `Stage` 新增三态（Task 10）、`next_actions` 认四状态与 `_PREFIX` 新前缀（Task 12） |
| `ir_agent/__main__.py`（改） | `--brief` / `--assume` / `--verified-by` / `--reviewed-by` / `--accept-manual` |
| `tests/test_stance.py`（新） | stance 全部纯函数 + 编排行为 |
| `tests/test_sources.py`（新） | 清单生成 + 文件存在性校验 |
| `ir_agent/sources/announce.py`（改，Task 8） | 挑选改确定性标题分类、去掉模型调用、入库放宽到 20 篇 |
| `ir_agent/sources/external.py`（改，Task 8） | `Corpus.prompt_docs` —— 提示词层的条数上限 |
| `ir_agent/strategy.py` / `value_chain.py` / `macro_view.py`（各改一处，Task 8） | 提示词改读 `corpus.prompt_docs()` 而不是 `corpus.docs` |
| `ir_agent/methodology.py`（改，Task 9） | `PROMPT` 整条重写、`research()` 接新输入、`theme_coverage` / `term_hits` 两段标注 |

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
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `python -m pytest tests/test_stance.py -q`
Expected: PASS（14 项）　← 实跑核实

- [ ] **Step 5: 全量回归**

Run: `python -m pytest -q -m "not live" -W error`
Expected: 2109 passed（基线 2095 + 新增 14），零警告　← 实跑核实

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
Expected: PASS（约 21 项，以实跑为准）

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
Expected: PASS（约 26 项，以实跑为准）

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
  - `run_stance(client, *, round_, subjects, context, catalog, ledger, as_of, body="", corpus=None, max_attempts=3) -> StanceResult`
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
            return StanceResult(round_=round_, evidence=evs, balances=bals)
        last = "；".join(probs)
        prompt = f"{base}\n---\n**上一稿被驳回**：{last}\n请据此重写全部行。\n"
    raise StanceFailed(f"连续 {max_attempts} 次给不出可用的平衡点：{last}", raw=raw)
```

- [ ] **Step 4: 跑测试确认绿**

Run: `python -m pytest tests/test_stance.py -q`
Expected: PASS（约 33 项，以实跑为准）

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

### Task 6: 门① 的来源清单（`sources/manifest.py`）

**Files:**
- Create: `ir_agent/sources/manifest.py` —— **包内模块**。`ir_agent/sources/` 已经是一个包（内有 `external.py` / `web.py` / `announce.py`），在 `ir_agent/` 下新建同名 `sources.py` 会与它冲突
- Create: `tests/test_sources_manifest.py` —— **不要叫 `tests/test_sources.py`**。那个文件自 `7d84a57` 起就存在（腾讯行情解析、巨潮公告解析、live 源共 18 项）。实跑时我用 `>` 重定向写进去，把它整个覆盖了：全量从 2130 掉到 2127、deselected 从 19 掉到 17，而没有任何报错 —— 一个测试文件被删掉，表现就是总数变小

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
**`decide` 的降级路径（`decomps=None` 那次重试）也要传 `balances`** —— 不传
就等于「降级之后这条约束静默失效」。

> 实跑时改了两处：
>
> 1. **按 `_row_label(r, a.decomps or [])` 取名字，不要直接读 `r.factor_key`。**
>    两标量降级模式下 `factor_key` 是空串，而那时辩论② 的 subjects 用的是
>    `DRIVERS` 的键（「营收增速」/「毛利率」）—— 直接比 `factor_key` 会让这条
>    检查在降级路径上恒不生效，与 Global Constraints 矛盾。`_row_label`
>    （`assume.py:212`）已经做了这件事，不必新写。
> 2. **补两条接线测试。** 计划原本只测 `check_deviation` 本身，没测它有没有
>    被接进 `check()` —— 删掉那三行，七条测试照样全绿。变异验证：摘掉接线，
>    只有新加的 `test_这条检查真的被接进了check` 变红。
>    注意那条测试要先把三档拉开（`_check_scalar` 排在偏离检查之前，`_rows`
>    只改 `base` 会让它先抛「三档未拉开」，那样测试即使在接线断掉后也照样红，
>    就测不出接线有没有断）。

- [ ] **Step 5: 跑测试确认绿并提交**

```bash
python -m pytest tests/test_assume.py -q
python -m pytest -q -m "not live" -W error
git add ir_agent/assume.py tests/test_assume.py
git commit -m "assume: 判断项偏离多空平衡点时必须填 deviation（只查存在性）"
```

---

### Task 8: 语料库入库改为确定性挑选，上限移到提示词层

**Files:**
- Modify: `ir_agent/sources/announce.py`（挑选改确定性，去掉模型调用）
- Modify: `ir_agent/sources/external.py`（`Corpus.prompt_docs`）
- Modify: `ir_agent/strategy.py`、`ir_agent/value_chain.py`、`ir_agent/macro_view.py`（各改一处）
- Modify: `ir_agent/full.py`（抽 `_industry_path`）
- Test: `tests/test_announce.py`、`tests/test_gaps.py`

**Interfaces:**
- Consumes: `ir_agent.sources.cninfo.search`；`ir_agent.sources.external.Document` / `Corpus` / `Tier`
- Produces:
  - `announce.is_substantive(title: str) -> bool` —— 实质类 True，程序类 False
  - `announce.MAX_INGEST = 20`、`announce.PROMPT_DOCS = 8`
  - `announce.gather(*, code, company, as_of, corpus, cache_dir=None) -> AnnounceResult` —— **去掉 `client` 与 `factors` 两个参数**
  - `Corpus.prompt_docs(company_limit: int = 8) -> list[Document]`
  - `full._industry_path(code: str) -> str`

**为什么这个 Task 存在**：今天 `_load_announce` 的 `factors=` 来自
`meth.decompositions`（`full.py:1554`）、`_load_openweb` 的 `industry=` 来自
`meth.industry`（`full.py:1529`）。预研一挪到它们之后，两个参数都空了。
解法不是给它找别的上游信号 —— 是 `MAX_ANNOUNCEMENTS = 8` 这个上限**放错了层**。
详见 spec「语料库入库」一节。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_announce.py 追加
class TestIsSubstantive:
    """**挑选改为确定性，因为它本来就与因子无关。**

    现有提示词那段硬性要求（优先挑扩产/投产/产能/价格调整/重大合同/对外投资；
    不要挑股东大会通知/董事会决议/独董声明/会计师改聘/业绩说明会通知）
    分的是**公告类型**，不是「与哪个因子相关」。类型判断不需要模型。
    """

    SUBSTANTIVE = (
        "关于全资子公司投资建设年产 5 万吨高频高速覆铜板项目的公告",
        "关于部分产品价格调整的公告",
        "关于签订重大销售合同的公告",
        "关于对外投资设立控股子公司的公告",
        "关于控股股东协议转让公司部分股份的公告",
        "关于生产线技术改造项目投产的公告",
    )
    PROCEDURAL = (
        "关于召开 2025 年第一次临时股东大会的通知",
        "第八届董事会第十二次会议决议公告",
        "第八届监事会第九次会议决议公告",
        "独立董事关于关联交易的独立意见",
        "关于改聘会计师事务所的公告",
        "关于召开 2025 年度业绩说明会的公告",
        "2025 年年度报告",
        "2025 年第三季度报告",
    )

    def test_实质类全部通过(self):
        from ir_agent.sources.announce import is_substantive
        for s in self.SUBSTANTIVE:
            assert is_substantive(s), s

    def test_程序类全部拒绝(self):
        from ir_agent.sources.announce import is_substantive
        for s in self.PROCEDURAL:
            assert not is_substantive(s), s

    def test_定期报告被排除(self):
        """**年报/季报走 `r.sections`，不走公告语料库。**

        它们动辄数百页；黑名单放过去的代价不是「多一篇无用文档」，是
        下载一个几十 MB 的 PDF 并全文抽取。
        """
        from ir_agent.sources.announce import is_substantive
        for s in ("2025 年年度报告", "2025 年半年度报告",
                  "2025 年第一季度报告", "2025 年年度报告（更正后）"):
            assert not is_substantive(s), s

    def test_认不出来的标题放过去(self):
        """**这里用黑名单，不用白名单。**

        本项目有一条相反方向的教训：`analyst._ask` 的过滤器曾是黑名单，
        每放宽一次就撞上下一类，最后改成 `is_fabrication` 白名单。那条
        教训在**拒绝模型输出**的方向上成立 —— 漏放一条伪造是实质损害。

        这里方向相反：我们要的是**高召回**。误判成实质类 = 多下一篇无用
        PDF（便宜）；误判成程序类 = 丢掉一个可直接引为依据的 COMPANY 级
        来源（贵，而且无声）。所以认不出来的一律放过去。
        """
        from ir_agent.sources.announce import is_substantive
        assert is_substantive("关于公司经营情况的说明")


class TestGatherNoModel:
    def test_入库不调模型(self, monkeypatch):
        """**钉住「不调模型」这件事本身。** `gather` 的签名里不再有 client；
        这条测试防的是将来有人加一个「可疑就调模型兜底」的分支 —— 那会把
        确定性又换回去。
        """
        import inspect
        from ir_agent.sources import announce

        assert "client" not in inspect.signature(announce.gather).parameters
        assert "factors" not in inspect.signature(announce.gather).parameters


class TestPromptDocs:
    def test_上限在提示词层而不在入库层(self):
        """**这是本 Task 的全部要点。** `MAX_ANNOUNCEMENTS = 8` 的注释自己
        写着上限的理由是「同一批文档会同时压到战略/产业链/宏观定性三个模块
        的提示词上」—— 那是消费方的约束，被实现成了生产方的上限。
        """
        from datetime import date

        from ir_agent.sources.external import Corpus, Document, Tier

        cp = Corpus(as_of=date(2026, 10, 12))
        for i in range(20):
            cp.docs.append(Document(
                doc_id=f"a{i}", url=f"http://x/{i}", title=f"公告{i}",
                publisher="某公司", tier=Tier.COMPANY,
                published=date(2026, 1, 1 + i), retrieved=date(2026, 10, 12),
                text="正文"))
        assert len(cp.docs) == 20, "入库的是全部 20 篇"
        assert len(cp.prompt_docs(company_limit=8)) == 8, "进提示词的只有 8 篇"

    def test_只截断COMPANY级其余照旧全给(self):
        from datetime import date

        from ir_agent.sources.external import Corpus, Document, Tier

        cp = Corpus(as_of=date(2026, 10, 12))
        for i, tier in enumerate([Tier.COMPANY] * 20 + [Tier.SELLSIDE] * 5):
            cp.docs.append(Document(
                doc_id=f"d{i}", url=f"http://x/{i}", title=f"文档{i}",
                publisher="某处", tier=tier,
                published=date(2026, 1, 1), retrieved=date(2026, 10, 12),
                text="正文"))
        got = cp.prompt_docs(company_limit=8)
        assert sum(1 for d in got if d.tier is Tier.COMPANY) == 8
        assert sum(1 for d in got if d.tier is Tier.SELLSIDE) == 5

    def test_取最近的那几篇(self):
        from datetime import date

        from ir_agent.sources.external import Corpus, Document, Tier

        cp = Corpus(as_of=date(2026, 10, 12))
        for i in range(10):
            cp.docs.append(Document(
                doc_id=f"a{i}", url=f"http://x/{i}", title=f"公告{i}",
                publisher="某公司", tier=Tier.COMPANY,
                published=date(2026, 1, 1 + i), retrieved=date(2026, 10, 12),
                text="正文"))
        got = [d.title for d in cp.prompt_docs(company_limit=3)]
        assert got == ["公告9", "公告8", "公告7"], got
```

```python
# tests/test_gaps.py 追加
def test_放宽条数带来的召回就是本次改动的全部收益():
    """**同一个因子，在 8 篇池子里搜不到、在 20 篇池子里搜到。**

    若这条测不出差别，放宽条数就没有收益，整个 Task 的理由不成立。
    牧原股份那次因子搜不到证据，有一半原因是可搜的池子太小。
    """
    from datetime import date

    from ir_agent.gaps import _match_announce
    from ir_agent.sources.external import Corpus, Document, Tier

    def _cp(n):
        cp = Corpus(as_of=date(2026, 10, 12))
        for i in range(n):
            # 只有第 12 篇提到「铜箔采购价」
            txt = "本期铜箔采购价同比下降 8%。" if i == 12 else "例行披露内容。"
            cp.docs.append(Document(
                doc_id=f"a{i}", url=f"http://x/{i}", title=f"公告{i}",
                publisher="某公司", tier=Tier.COMPANY,
                published=date(2026, 1, 1 + i), retrieved=date(2026, 10, 12),
                text=txt))
        return cp

    assert _match_announce(_cp(8), "铜箔采购价", search_term="铜箔") is None
    assert _match_announce(_cp(20), "铜箔采购价", search_term="铜箔") is not None
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_announce.py tests/test_gaps.py -q -k "IsSubstantive or GatherNoModel or PromptDocs or 放宽条数"`
Expected: FAIL，`ImportError: cannot import name 'is_substantive'`

> `_match_announce` 的真实签名要先核一遍：`grep -n "def _match_announce" -A 3 ir_agent/gaps.py`。
> 上面那条召回测试按「(corpus, label, search_term=…)」写，若实际形参不同，
> **改测试去对齐实现，不要改实现去对齐测试** —— 它是既有代码。

- [ ] **Step 3: 实现 `is_substantive`**

```python
# ir_agent/sources/announce.py
# 单轮最多入库几篇。**这是入库上限，不是提示词上限** —— 后者在
# `Corpus.prompt_docs` 里，由 `PROMPT_DOCS` 控制。两者分开的理由：
# 入库的文档供 `gaps._match_announce` 按因子做全文检索（池子越大召回越高），
# 而进提示词的文档要控长度。此前这两件事共用一个 `MAX_ANNOUNCEMENTS = 8`，
# 注释里写的理由是提示词长度 —— 消费方的约束被实现成了生产方的上限。
MAX_INGEST = 20

# 三个定性模块的提示词里，COMPANY 级最多给几篇。沿用旧的 8。
PROMPT_DOCS = 8

# 程序类公告：对因子取值没有帮助。**黑名单而非白名单** —— 见
# `test_认不出来的标题放过去` 的文档字符串：这里要的是高召回，
# 误判成程序类的代价（丢一个可直接引为依据的 COMPANY 级来源）
# 远高于误判成实质类（多下一篇无用 PDF）。
_PROCEDURAL = re.compile(
    r"股东大会|董事会(?:第[一二三四五六七八九十\d]+次)?会议决议|监事会"
    r"|独立董事|会计师事务所|业绩说明会|业绩预约披露|投资者关系活动"
    r"|持股计划(?:草案)?摘要|章程|募集资金.{0,6}存放|内部控制(?:自我)?评价"
    r"|关于更正|澄清公告")

# 定期报告：数百页，且年报正文已经由 `r.sections` 单独走一条路
# （`_load_sections` 从 annual_url 抓 PDF 切节）。放它进来的代价不是
# 「多一篇无用文档」，是下载一个几十 MB 的 PDF 并全文抽取。
_PERIODIC = re.compile(r"(?:年度|半年度|第[一二三四]季度|季度)报告"
                       r"|年报(?:摘要)?|半年报|季报")


def is_substantive(title: str) -> bool:
    """这条公告值不值得下载正文入库。**确定性，不调模型。**

    判的是**公告类型**，与本轮有哪些因子无关 —— 因子定向发生在消费端
    （`gaps._match_announce` 按因子做全文检索），那时因子已经知道了。
    """
    t = (title or "").strip()
    if not t:
        return False
    if _PERIODIC.search(t):
        return False
    return not _PROCEDURAL.search(t)
```

- [ ] **Step 4: 改 `gather`：去掉模型调用，放宽条数**

```python
def gather(*, code: str, company: str, as_of: date, corpus,
           cache_dir: Path | str | None = None) -> AnnounceResult:
    """公告入语料库：代码取清单 → **确定性按标题筛** → 代码抓 PDF。

    **没有 `client` 参数。** 挑选改为 `is_substantive`，与本轮因子无关
    （见该函数）。这也让整步变得可复现 —— 同一个 as_of 下
    `_search` 返回同一份清单、筛选无随机性。
    """
    res = AnnounceResult()
    start = as_of - timedelta(days=int(ANNOUNCE_MONTHS * 30.5))
    anns = _search(code, start, as_of)
    res.candidates = len(anns)
    if not anns:
        return res

    # 实质类按披露日倒序取前 MAX_INGEST 篇。
    picked = [a for a in anns if is_substantive(a.title)]
    res.rejected = len(anns) - len(picked)
    picked.sort(key=lambda a: a.ann_date, reverse=True)
    picked = picked[:MAX_INGEST]
    res.picked = len(picked)

    seen = {d.url for d in corpus.docs}
    seen_failure_keys: set[str] = set()
    for a in picked:
        if a.url in seen:
            continue
        try:
            data = _download(a.url, cache_dir)
            text = pdf_text(data)
        except Exception as e:                              # noqa: BLE001
            _note_failure(res, seen_failure_keys,
                          f"{a.ann_id}：{e.__class__.__name__}: {e}",
                          _exception_cause_key(e))
            continue
        if not text.strip():
            _note_failure(res, seen_failure_keys, f"{a.ann_id}：PDF 取文为空",
                          _EMPTY_TEXT_KEY)
            continue
        doc = Document(
            doc_id=_doc_id(a.ann_id), url=a.url, title=a.title,
            publisher=a.name or company or code,
            tier=Tier.COMPANY,
            published=a.ann_date, retrieved=as_of,
            text=text, prompt_limit=TEXT_LIMIT)
        if not corpus.add_if_visible(doc):
            continue
        seen.add(doc.url)
        res.docs.append(doc)
    return res
```

`AnnounceResult` 加一个字段（`picked` 已有）：

```python
    # 按标题判为程序类而被跳过的条数。**必须能说出来** —— 与 `candidates`
    # 同一条理由：读者看到「入库 14 篇」时要能知道另外多少篇是被筛掉的、
    # 筛掉的理由是类型而不是故障。
    rejected: int = 0
```

删掉 `PROMPT`、`format_candidates`、`parse_picks` 及其测试 —— 它们只服务
那次模型调用。**先 `grep -rn "parse_picks\|format_candidates" ir_agent/ tests/`
确认没有别的调用方**，有就先处理那一处。

- [ ] **Step 5: `Corpus.prompt_docs`**

```python
# ir_agent/sources/external.py，Corpus 内
    def prompt_docs(self, company_limit: int = 8) -> list[Document]:
        """进提示词的文档。**只截断 COMPANY 级，其余分级照旧全给。**

        入库的公告放宽到 20 篇之后，三个定性模块若仍 `for d in corpus.docs`
        无差别全喂，提示词会是原先的 2.5 倍、而且是三个模块各一份。
        截断放在这里而不是入库处：入库的文档供 `gaps._match_announce` 按
        因子做全文检索，池子越大召回越高 —— 那是放宽条数的全部收益。

        只动 COMPANY 级，因为只有它放宽了（研报走 `corpus.gather` 的 24
        个月窗口、开放网络本来就最多 10 篇，两者条数没变）。
        """
        from ir_agent.sources.announce import PROMPT_DOCS  # noqa: PLC0415

        if company_limit is None:
            company_limit = PROMPT_DOCS
        company = sorted((d for d in self.docs if d.tier is Tier.COMPANY),
                         key=lambda d: d.published, reverse=True)[:company_limit]
        keep = {id(d) for d in company}
        return [d for d in self.docs
                if d.tier is not Tier.COMPANY or id(d) in keep]
```

> 循环导入要当心：`announce.py` 从 `external.py` 导 `Document`/`Tier`。
> 因此 `PROMPT_DOCS` 的导入写在函数体内（延迟导入）。若仍报循环，
> 把 `PROMPT_DOCS` 挪到 `external.py` 定义、`announce.py` 从那里导。

- [ ] **Step 6: 三个定性模块各改一处**

`strategy.py:259`、`value_chain.py:321`、`macro_view.py:238` 三处
`for d in corpus.docs:` **全部**改为：

```python
        for d in corpus.prompt_docs():
```

同一文件里紧邻的 `seen_tiers = {d.tier for d in corpus.docs}` 保持读
`corpus.docs` —— 它算的是「本轮语料库涉及哪几个分级」，用来决定打印哪几条
`TIER_RULE`。那是**语料库的属性**，不是提示词里给了哪几篇的属性。

- [ ] **Step 7: 抽 `_industry_path`**

`_load_openweb` 的 `industry=` 今天来自 `meth.industry`，而那个值其实是
`taxonomy.fetch_all()` 的 `me.path` —— **确定性查表，不是模型输出**。
抽成模块级助手（`full.py`，放在 `_peer_names` 旁边，它用同一条路）：

```python
def _industry_path(code: str) -> str:
    """申万行业分类路径。**确定性查表，与预研无关。**

    此前这个值从 `meth.industry` 取，于是「开放网络检索」隐式依赖了预研
    先跑完。预研挪到辩论① 之后以后，那条依赖会让 `industry=` 恒为空串。
    它本来就不该经过模型 —— `_research_methodology` 自己也是这么拿的。
    """
    try:
        from ir_agent.sources.taxonomy import fetch_all
        rows = fetch_all()
    except Exception:                                       # noqa: BLE001
        return ""
    me = next((e for e in rows if e.code == code), None)
    return me.path if me is not None else ""
```

`_research_methodology` 里原来那段取 industry 的代码改为调它（纯搬家）。

- [ ] **Step 8: 改 `_load_announce` 的签名与调用**

`_load_announce(code, r, as_of, out, client, md, factors)` 去掉 `client`
与 `factors` 两个形参，内部 `announce.gather(...)` 相应改。`run_analysis`
里那一处调用同步改（这个 Task 还没拆 `full.py`，改的仍是 `run_analysis`）：

```python
    skipped += _load_openweb(code, r, as_of, out, research_client,
                             industry=_industry_path(code))
    _refresh_external_md(r, md)
    # 公告入库不再需要 client —— 挑选是确定性的
    skipped += _load_announce(code, r, as_of, out, md)
    _refresh_external_md(r, md)
```

原先为公告专门开的 `announce_client = make_client(model=model)` **整句删掉**，
并把下面累加开销那一行里的 `announce_client.total_cost_usd` 一并删掉 ——
**漏删会是 NameError**，而那正是本会话已经崩过一整轮的那个形状
（`assume_client` 的 `UnboundLocalError`）。

> **实跑与本 Task 计划的五处出入：**
>
> 1. **`is_substantive` 复用 `cninfo.classify`，不另写 `_PERIODIC`/`_PROCEDURAL`
>    两条正则。** `classify`（`cninfo.py:49`）已经在用 —— `Announcement.category`
>    就是它算的，分成 定期报告/业绩预告/股东大会/股份变动/重大事项/其他。
>    再写一套词表意味着两套会漂，而它们**当时就已经不一致**：`classify` 的
>    定期报告模式带 `(?!.*摘要)`，所以「年度报告摘要」归「其他」，而计划的
>    `_PERIODIC` 把它归定期报告。新实现是：`classify` 判成定期报告/股东大会
>    → 拒绝；判成另三类 → 接受；落「其他」→ 再过一道黑名单（董事会决议/
>    监事会/独董/会计师改聘/业绩说明会/报告摘要…，这些 `classify` 认不出来）。
> 2. **`prompt_docs` 保持 `corpus.docs` 的原顺序**，不按披露日降序返回。
>    计划的测试断言 `["公告9","公告8","公告7"]`；改成原顺序，因为返回的是
>    **混合分级**的列表，只把 COMPANY 那一部分重排会让提示词里的次序和
>    `_external_md` 统计的那份清单对不上（而那份清单进看板、进 state.json、
>    第二阶段读回）。「留哪几篇」仍按新旧选。
> 3. **`MAX_ANNOUNCEMENTS` 整个删掉**，不留别名 —— 读它的 `parse_picks` 一起
>    删了，留着只会让人以为还有人在用。
> 4. **计划那条「收益」测试证不了收益。** `tests/test_gaps.py` 的
>    `test_放宽条数带来的召回` 直接拿手搭的两个语料库调 `_match_announce`，
>    证明的是「文档在库里就搜得到」—— 而那在改动之前就成立（`_match_announce`
>    从来没有条数上限，上限在入库处）。实跑时它**一次就绿**。补了两条真的：
>    `test_入库条数放宽到MAX_INGEST而且留的是最近的`（直接驱动 `gather`，
>    30 条候选 → 入库 20 篇，且留下的是最近的）和 `test_旧上限8篇已经不再是入库上限`
>    （钉住 `PROMPT_DOCS == 8 < MAX_INGEST`，防止有人又把两个数合回一个）。
> 5. **要删的旧测试是 11 条不是 10 条**，且 `gather` 的调用点有 21 处（含
>    重试/错误处理那一大批），`_load_announce` 在 `tests/test_full_context.py`
>    里还有 6 处 —— 计划没提后两项。
>
> 两个变异都验证过：`MAX_INGEST` 改回 8 → 2 条红；`prompt_docs` 不截断 →
> 3 条红。

- [ ] **Step 9: 全量回归并提交**

```bash
python -m pytest -q -m "not live" -W error
```
Expected: PASS。原先 `tests/test_announce.py` 里针对 `parse_picks` /
`format_candidates` / `PROMPT` 的测试会红 —— 它们测的那次模型调用已经
不存在，**整条删除**（不要改成测新实现，那是两件事）。

```bash
git add ir_agent/sources/announce.py ir_agent/sources/external.py \
        ir_agent/strategy.py ir_agent/value_chain.py ir_agent/macro_view.py \
        ir_agent/full.py tests/test_announce.py tests/test_gaps.py
git commit -m "announce: 挑选改为确定性标题分类，入库放宽到 20 篇，上限移到提示词层"
```

---

### Task 9: 方法论预研的框架重写

**Files:**
- Modify: `ir_agent/methodology.py`（`PROMPT` 整条重写；`research()` 接新输入；两段标注）
- Modify: `ir_agent/full.py`（`_research_methodology` 传新输入）
- Test: `tests/test_methodology.py`

**Interfaces:**
- Consumes: Task 8 的 `full._industry_path`；`gaps._core_candidates(label, search_term)`
- Produces:
  - `methodology.PROMPT`（重写）
  - `methodology.research(client, industry, code, *, max_attempts=3, facts="", corpus_note="", qualitative="", stance_note="", available_keys=())`
  - `methodology.theme_coverage(decomps, themes) -> list[tuple[str, str]]` —— `(主题, 覆盖它的因子标签或空串)`
  - `methodology.term_hits(decomps, *, sections=None, corpus=None) -> dict[str, list[tuple[str, int]]]` —— `{因子标签: [(候选词, 命中次数), …]}`，四级顺序
  - `to_markdown(m, coverage=(), hits=None)` —— 两段标注写进 `drivers.md` 顶部

**保持不变**（spec「要改的既有不变量」第 3 条）：`Methodology` / `Decomposition` /
`Factor` 的数据结构、DSL 行格式、算术闭合的全部硬约束、`drivers.md` 的往返格式。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_methodology.py 追加
class TestThemeCoverage:
    """**覆盖约束：标注，不阻断。**

    分解式要能承载辩论① 列出的主题 —— 多空在「份额」上分歧最大、分解式里
    却没有份额因子，辩论② 就无从给出三元组、判断项也无从反映分歧，整条链
    在这里断掉而产物上看不出来。

    但这是**语义判断**：代码只能查「某个词有没有出现在因子标签或数据线索
    里」，而「份额这件事有没有被这个分解式覆盖」要靠读。实测教训就在手边
    —— 隐含份额检查想用算术判断一件该由语义判断的事，10 份存档上 10/10
    惰性。所以标注、不阻断。
    """

    def _decomps(self):
        from ir_agent.methodology import parse
        return parse(
            "分解: 营收 | 乘 | 高端覆铜板出货量:变化率:年报经营情况:出货量"
            " | 平均单价:变化率:年报分产品:平均单价\n"
            "分解: 毛利率 | 加 | 铜箔采购价:pp:年报采购情况:铜箔\n"
        ).decompositions

    def test_覆盖到的主题给出覆盖它的因子(self):
        from ir_agent.methodology import theme_coverage
        got = dict(theme_coverage(self._decomps(), ["价格", "成本"]))
        assert "平均单价" in got["价格"]
        assert "铜箔" in got["成本"]

    def test_没覆盖的主题给空串而不是抛异常(self):
        from ir_agent.methodology import theme_coverage
        got = dict(theme_coverage(self._decomps(), ["份额", "政策"]))
        assert got["份额"] == "" and got["政策"] == ""

    def test_主题顺序原样保留(self):
        """标注是给人看的，顺序要和辩论① 列出的主题一致。"""
        from ir_agent.methodology import theme_coverage
        themes = ["需求", "价格", "成本", "份额", "政策"]
        assert [x[0] for x in theme_coverage(self._decomps(), themes)] == themes

    def test_没有主题时返回空列表(self):
        """辩论① 失败并由人工补写为空时，不能凭空要求覆盖。"""
        from ir_agent.methodology import theme_coverage
        assert theme_coverage(self._decomps(), []) == []


class TestTermHits:
    """**检索词从「靠告诫」改为「靠数据」。**

    现有提示词花了一整段劝模型不要给泛词，还得举实测数字 —— 因为那时候
    语料库不存在，模型只能凭猜。现在语料库已在手，命中次数算得出来。
    """

    def _decomps(self):
        from ir_agent.methodology import parse
        return parse(
            "分解: 营收 | 乘 | 服务器用高频覆铜板出货量（含通信）:变化率:"
            "年报经营情况:铜箔\n"
        ).decompositions

    def test_四级候选词各自给出命中次数(self):
        from ir_agent.methodology import term_hits
        text = "铜箔价格波动。铜箔采购。服务器用高频覆铜板出货量（含通信）稳定。"
        got = term_hits(self._decomps(), sections=text)
        label = "服务器用高频覆铜板出货量（含通信）"
        pairs = got[label]
        assert dict(pairs)["铜箔"] == 2
        assert dict(pairs)[label] == 1

    def test_检索词排在第四级而不是第一级(self):
        """**这一条是整节里最要紧的。**

        `gaps._core_candidates` 的阶梯是 L1 整串标签 → L2 去括号 →
        L3 剥后缀 → L4 模型给的检索词。检索词在**最底端**：L1–L3 全部落空
        才轮到它。只反馈检索词的命中次数，等于在优化用得最少的那根杠杆。

        重写提示词时极易破坏这个顺序（会想当然地把检索词放前面），所以
        这里直接钉住它。
        """
        from ir_agent.gaps import _core_candidates
        from ir_agent.methodology import term_hits

        label = "服务器用高频覆铜板出货量（含通信）"
        cands = _core_candidates(label, "铜箔")
        assert cands[0] == label, "第一级必须是整串标签"
        assert cands[-1] == "铜箔", "模型给的检索词必须在最后一级"

        order = [c for c, _ in term_hits(self._decomps(), sections="x")[label]]
        assert order == cands, "标注里的顺序要与实际探测顺序一致"

    def test_没有语料时次数全零而不是报错(self):
        from ir_agent.methodology import term_hits
        got = term_hits(self._decomps(), sections=None, corpus=None)
        assert all(n == 0 for pairs in got.values() for _, n in pairs)


class TestMarkdownAnnotations:
    def test_两段标注进drivers_md顶部(self):
        from ir_agent.methodology import parse, to_markdown
        m = parse("分解: 营收 | 乘 | 出货量:变化率:年报:出货量 | "
                  "单价:变化率:年报:单价\n")
        m.industry = "测试业"
        out = to_markdown(m, coverage=[("需求", "出货量"), ("份额", "")],
                          hits={"出货量": [("出货量", 3)]})
        assert "主题覆盖" in out and "份额" in out
        assert "命中次数" in out and "3" in out

    def test_标注不参与往返解析(self):
        """**人改分解式时不必维护这两段标注。**

        `from_markdown` 只读 text 围栏块与标题行；标注是普通 markdown，
        下次重跑由代码重算。这条测试防的是「标注里写了个看起来像 DSL 的
        东西，被 from_markdown 当真」。
        """
        from ir_agent.methodology import from_markdown, parse, to_markdown
        m = parse("分解: 营收 | 乘 | 出货量:变化率:年报:出货量 | "
                  "单价:变化率:年报:单价\n")
        m.industry = "测试业"
        back = from_markdown(to_markdown(
            m, coverage=[("需求", "出货量")], hits={"出货量": [("出货量", 3)]}))
        assert len(back.decompositions) == len(m.decompositions)
        assert [f.label for f in back.decompositions[0].factors] == \
               [f.label for f in m.decompositions[0].factors]


class TestResearchFallback:
    def test_不传新输入时仍退回行业级预研(self):
        """**两标量降级路径要走它。** 预研失败、或拿不到语料库时，
        `research()` 必须还能只凭行业分类跑 —— 那条路是 `_research_methodology`
        的既有降级出口，不能因为新增输入而失效。
        """
        from ir_agent.methodology import research

        seen = []

        def c(p):
            seen.append(p)
            return ("分解: 营收 | 乘 | 出货量:变化率:年报:出货量 | "
                    "单价:变化率:年报:单价\n"
                    "分解: 毛利率 | 加 | 原材料成本:pp:年报:原材料成本\n")

        m = research(c, "电子/元件/覆铜板", "600183")
        assert m.decompositions
        assert "覆铜板" in seen[0]
        for absent in ("【可用事实】", "【多空平衡点】", "【定性结论】"):
            assert absent not in seen[0], f"没传的输入不该出现在提示词里：{absent}"

    def test_传了新输入就进提示词(self):
        from ir_agent.methodology import research

        seen = []

        def c(p):
            seen.append(p)
            return ("分解: 营收 | 乘 | 出货量:变化率:年报:出货量 | "
                    "单价:变化率:年报:单价\n"
                    "分解: 毛利率 | 加 | 原材料成本:pp:年报:原材料成本\n")

        research(c, "电子/元件/覆铜板", "600183",
                 facts="revenue@2025FY = 203 亿",
                 stance_note="需求：多方看 AI 服务器放量，空方看消费电子疲软",
                 available_keys=("revenue.yoy", "industry.revenue_yoy"))
        assert "203 亿" in seen[0]
        assert "AI 服务器" in seen[0]
        assert "industry.revenue_yoy" in seen[0]
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_methodology.py -q -k "ThemeCoverage or TermHits or MarkdownAnnotations or ResearchFallback"`
Expected: FAIL，`ImportError: cannot import name 'theme_coverage'`

- [ ] **Step 3: `theme_coverage` 与 `term_hits`**

```python
# ir_agent/methodology.py

def theme_coverage(decomps: list, themes: list[str]) -> list[tuple[str, str]]:
    """辩论① 的每个主题，有没有被某个因子覆盖。**标注用，不阻断。**

    判据只能是词面：主题词出现在因子标签或数据线索里就算覆盖。
    「份额这件事有没有被这个分解式真的覆盖」是语义判断，代码判不了 ——
    所以这个函数的产出是一行标注，交给门② 的人读，不是闸门。

    `themes` 为空（辩论① 失败且人工补写为空）时返回空列表 ——
    不能凭空要求覆盖。
    """
    out: list[tuple[str, str]] = []
    for th in themes:
        hit = ""
        for d in decomps:
            for f in d.factors:
                blob = f"{f.label}{getattr(f, 'hint', '')}{getattr(f, 'search_term', '')}"
                if th and th in blob:
                    hit = f.label
                    break
            if hit:
                break
        out.append((th, hit))
    return out


def term_hits(decomps: list, *, sections=None, corpus=None
              ) -> dict[str, list[tuple[str, int]]]:
    """每个因子的四级候选词，在年报分节 + 语料库里各命中几次。

    **顺序必须与 `gaps._core_candidates` 一致** —— L1 整串标签 → L2 去括号
    → L3 剥后缀 → L4 模型给的检索词。检索词在最底端：L1–L3 全部落空才轮到
    它。只给检索词的次数，等于在优化用得最少的那根杠杆。

    **不设阈值、不反馈给模型**：次数原样印进 `drivers.md`，门② 的人看得见
    就能判断。原本想按实测取「> 10 次判为泛词嫌疑」，但那组实测只有五个
    样本（生益科技 8 篇公告 8.9 万字里覆铜板 98 / 产能 31 / 服务器 18 是
    反例，原材料成本 2 / 铜箔 6 是正例），而公告篇数与字数每家公司都不同
    —— 一个绝对次数的阈值换个行业就不对。

    计数用朴素的 `str.count`，不走 `gaps._valid_hit` 的否定语境过滤：
    这是标注而不是判定，多数几次不会误伤谁，而引入过滤会让印出来的数字
    与「实际能不能命中」之间多一层解释。
    """
    from ir_agent.gaps import _core_candidates

    blobs = [sections if isinstance(sections, str) else ""]
    if sections is not None and not isinstance(sections, str):
        from ir_agent.strategy import annual_text
        blobs = [annual_text(sections)]
    for d in (getattr(corpus, "docs", None) or []):
        blobs.append(getattr(d, "text", "") or "")
    hay = "\n".join(x for x in blobs if x)

    out: dict[str, list[tuple[str, int]]] = {}
    for d in decomps:
        for f in d.factors:
            cands = _core_candidates(f.label, getattr(f, "search_term", "") or "")
            out[f.label] = [(c, hay.count(c)) for c in cands]
    return out
```

> `Factor` 的字段名要先核：`grep -n "class Factor" -A 12 ir_agent/drivers.py`。
> 上面用的是 `label` / `hint` / `search_term`，`hint` 那个名字按实际改
> （数据线索那一段）。**改代码去对齐既有字段，不要改 `Factor`。**

- [ ] **Step 4: 两段标注进 `to_markdown`**

```python
def to_markdown(m: Methodology, coverage=(), hits=None) -> str:
    """驱动因子分解落盘为 drivers.md。

    （原有文档字符串整段保留，在末尾追加下面这一段）

    `coverage` / `hits` 是**标注**，不参与 `from_markdown` 的往返解析
    —— 后者只读 text 围栏块与 `# 驱动因子分解（…）` 标题行。人改分解式
    时不必维护这两段，下次重跑由代码重算。
    """
```

在原有输出的标题行之后、分解式之前插入：

```python
    if coverage:
        miss = [th for th, who in coverage if not who]
        lines += ["## 辩论① 主题覆盖（标注，未阻断）", ""]
        lines += [f"- {th}：{('✓ ' + who) if who else '✗ 未进入分解式'}"
                  for th, who in coverage]
        if miss:
            lines += ["", f"⚠ {len(miss)} 个辩论主题未进入分解式"
                          f"（{'、'.join(miss)}）—— 请在门② 复核：这几点"
                          f"多空有分歧，而判断项将无从反映它们。"]
        lines.append("")
    if hits:
        lines += ["## 因子候选词在本轮语料里的命中次数（标注，未阻断）", "",
                  "探测顺序：整串标签 → 去括号 → 剥后缀 → **模型给的检索词**"
                  "（最后一级）。`gaps` 按这个顺序逐级试，命中即停。", ""]
        for label, pairs in hits.items():
            shown = "　".join(f"{c}:{n}" for c, n in pairs)
            lines.append(f"- {label}　{shown}")
        lines.append("")
```

- [ ] **Step 5: `PROMPT` 整条重写与 `research()` 接新输入**

`PROMPT` **保留**的部分（算术闭合那一整套硬约束，逐字不动）：
两条分解式的行格式、营收只能乘且单位一律变化率、毛利率只能加且单位一律 pp、
pp 不进乘法链、写不出单位的项不许进来、营收乘法链禁占比/比重/结构类因子
（市占率例外）、每条 2–5 个因子、`陷阱:` 行的格式。

**改掉的四处：**

1. **开头的问题换掉。** 原文是「先上网搜索这个行业的研究与估值方法论 ——
   卖方怎么看这个行业、营收和毛利率分别由什么驱动」。改为：

   > 下面给出这家公司本轮已经采集到的全部材料。请回答：**就多空双方争执的
   > 这几个点，这家公司的营收和毛利率该怎么分解，才能把争议落到可测的因子
   > 上？**
   >
   > **先用下面已入库的卖方研报**；只在它们不足以支撑方法论判断时才上网补。
   > （这不改变卖方资料的可引用范围：你读研报读的是**方法论**，产出的是
   > 分解式的**框架**，不是任何一个因子的取值。）

2. **新增一条硬性要求（覆盖）**：

   > 下面列出了多空双方争执的主题。**每个主题都应有一个因子承载它**；
   > 确实无法因子化的，用一行 `未覆盖: <主题> | <为什么不能因子化>` 说明。
   > 代码会逐个核对并把结果标注在产物顶部 —— 不会因此驳回你，但人会看到。

3. **删掉那段劝「不要给泛词」的实测举例**（覆铜板 98 次 / 产能 31 次 /
   服务器 18 次那一段）。保留「不要给泛词」这条要求本身与一句理由，
   删掉举例 —— 命中次数现在由代码算出来印在产物里，不再需要靠举例说服。
   **同时告诉模型这件事**：

   > 代码会把你给的每个因子的候选词在本轮语料里的实际命中次数印出来，
   > 所以不必猜。宁可给一个搜不到的精确词（因子落「无数据」，诚实），
   > 也不要给一个到处都能搜到的泛词。

4. **数据线索从「去哪找」改为「有没有」**。原文是「数据线索要具体到
   『去哪找』（年报哪一节 / 哪类研报 / 哪个行业数据源）」。改为：

   > 下面列出了账本里**已经有**的 key。优先分解成这些数据能支撑的因子；
   > 数据线索写明你指望它来自哪里（已有 key / 年报某节 / 语料库某篇 /
   > 需要新采集）。

5. **「陷阱」改为针对这组因子**（原文是行业通用陷阱）：

   > 最后，针对**你刚给出的这组因子**，指出它们自身的误用方式，每条一行：
   >
   > `陷阱: <一句话>`
   >
   > 例如：哪个因子容易被双重计数、哪两个因子实际上不独立、哪个单位换算
   > 容易错。这正是下游结构审查要对的账。**不要写行业通用风险** ——
   > 那些已经在上面的定性结论与多空辩论里。

`research()` 的签名与新输入的拼装：

```python
def research(client, industry: str, code: str,
             max_attempts: int = 3, *,
             facts: str = "", corpus_note: str = "",
             qualitative: str = "", stance_note: str = "",
             available_keys: tuple[str, ...] = ()) -> Methodology:
    """让模型基于本轮已采集的材料，产出可计算的分解式。

    **新输入全部可选，不传就退回行业级预研。** 那条路是
    `_research_methodology` 的既有降级出口（预研失败 → 两标量模式），
    不能因为新增输入而失效 —— 见 `test_不传新输入时仍退回行业级预研`。

    **`client` 是否真的联网仍取决于调用方**（原有文档字符串那条前提不变）。
    但现在提示词先让它用已入库的研报，联网是补充而非起点。
    """
    blocks = []
    if facts:
        blocks.append(f"【可用事实】\n{facts}")
    if corpus_note:
        blocks.append(f"【已入库资料】\n{corpus_note}")
    if qualitative:
        blocks.append(f"【定性结论】\n{qualitative}")
    if stance_note:
        blocks.append(f"【多空平衡点】\n{stance_note}")
    if available_keys:
        blocks.append("【账本里已有的 key】\n" + "、".join(available_keys))
    prompt = PROMPT.format(industry=industry, code=code,
                           context="\n\n".join(blocks))
```

函数体余下部分（`for attempt in range(1, max_attempts + 1)` 那个重试循环、
`parse(raw)`、驳回后把理由拼回提示词、以及末尾的
`raise MethodologyFailed(f"连续 {max_attempts} 次给不出闭合的分解式…")`）
**逐字不动** —— 本 Step 只改签名、提示词拼装与 `PROMPT` 的内容。

> `PROMPT` 要多一个 `{context}` 占位符。**注意模板里已有的 `{{…}}` 转义
> 约定**（见 `announce.PROMPT` 顶部那段注释）：`methodology.PROMPT` 今天
> 是普通字符串用 `.format(industry=…, code=…)`，加 `{context}` 不需要转义，
> 但若正文里出现了字面的大括号就要。先 `grep -c "{" ir_agent/methodology.py`
> 对一眼。

- [ ] **Step 6: `_research_methodology` 传新输入**

```python
def _research_methodology(code: str, r, out: Path, client, *,
                          md: dict | None = None, stance_note: str = ""):
    """方法论预研。**现在排在辩论① 之后，手里有全部证据与多空结论。**

    `md` 是定性分析的结果（`state.json` 的 `md`），`stance_note` 是辩论①
    的平衡结论。两者都可选 —— 不传就退回行业级预研（降级路径要走它）。
    """
    from ir_agent import methodology

    m, derr = _read_drivers(out)        # Task 10 Step 0a 提取的只读读回
    if m is not None or derr:
        return m, derr

    industry = _industry_path(code)     # Task 8 Step 7
    if not industry:
        return None, "方法论预研（取不到申万行业分类）"

    try:
        m = methodology.research(
            client, industry, code,
            facts=_facts_digest(r),
            corpus_note=(md or {}).get("外部资料", ""),
            qualitative="\n\n".join(
                f"【{k}】\n{v}" for k, v in (md or {}).items()
                if k in ("宏观定性", "战略", "产业链") and v),
            stance_note=stance_note,
            available_keys=_ledger_keys(r))
    except methodology.MethodologyFailed as e:
        return _methodology_failed(out, e)      # 见下
    except ClaudeCliError as e:
        return _methodology_api_failed(e)       # 见下

    # 两段标注
    themes = ["需求", "价格", "成本", "份额", "政策"]
    cov = methodology.theme_coverage(m.decompositions, themes)
    hits = methodology.term_hits(m.decompositions,
                                 sections=getattr(r, "sections", None),
                                 corpus=getattr(r, "corpus", None))
    path = out / DRIVERS_MD
    path.write_text(methodology.to_markdown(m, coverage=cov, hits=hits),
                    encoding="utf-8")
    note = ""
    if (miss := [th for th, who in cov if not who]):
        note = (f"方法论预研：{len(miss)} 个辩论主题未进入分解式"
                f"（{'、'.join(miss)}）—— 已标注，未阻断，需在门② 复核")
    return m, note
```

两个失败分支改为调助手，**纯提取**：把现有 `_research_methodology` 里那两个
`except` 分支的函数体原样搬出来，各返回 `(None, 原因字符串)`。

```python
def _methodology_failed(out: Path, e) -> tuple[None, str]:
    """预研给不出闭合的分解式 —— 退回不分解模式，整轮继续。

    （函数体从现有 `_research_methodology` 的 `except
    methodology.MethodologyFailed` 分支整段搬来，**含那段关于存活者偏差的
    长注释，逐字带走一个字都不要删**：它记着牧原股份 002714 那次「公司
    出栏量占全国比重」被拒 3 次、drivers.md 根本没生成，于是用 10 份存档
    做消融时两个词法修复在 56 个存活因子上效果为 0 —— 真正的拦截与漏报
    全在看不见的那一半。）
    """
    dumped = _dump_refused(out, getattr(e, "raw", ""), DRIVERS_FAILED_RAW)
    tail = f"（最后一稿已落盘：{dumped}）" if dumped else ""
    return None, (f"方法论预研（{str(e)[:160]}）{tail}—— "
                  "退回不分解模式，判断项仍按营收增速/毛利率两项填写")


def _methodology_api_failed(e) -> tuple[None, str]:
    """模型接口失败（额度用尽/超时/CLI 报错）—— 不是这个行业分解不了。

    （函数体从现有 `except ClaudeCliError` 分支整段搬来，**含那段关于
    5db4ca5 同一个洞的长注释**：600584 那次确定性层已跑完 2930 条事实，
    第一个联网调用就撞上 session limit，而这里当时只捕 MethodologyFailed，
    整轮退出码 1、产物一个都没写出来。）

    降级理由必须说清这是接口失败而非能力边界：两者都退回两标量模式，
    但前者重跑就能拿回因子分解，后者重跑多少次都一样。
    """
    return None, (f"方法论预研（{e.__class__.__name__}: {str(e)[:160]}）"
                  "—— 模型接口失败（额度用尽/超时/CLI 报错），不是这个"
                  "行业分解不了：本轮退回不分解模式，判断项按营收增速/"
                  "毛利率两项填写；接口恢复后重跑 --brief 即可拿回因子分解")
```

提取的理由：这两个分支共 25 行、其中 20 行是注释，内联在新版
`_research_methodology` 里会把「现在它多了四类输入和两段标注」这件事
埋在注释堆下面看不见。

> 注意末句改了：原文写「重跑 `--analyse` 即可拿回因子分解」，而预研挪进
> 第二次调用之后，该重跑的是 `--brief`。**过时的指引是会主动误导人的那种错**
> （`next_actions` 的 docstring 原话）。

四个新助手（同一文件）：

```python
def _ledger_keys(r) -> tuple[str, ...]:
    """账本里已有的 key（去重、排序）。让预研优先分解成有数据的因子。"""
    keys = {k for k, _ in getattr(r.ledger, "_facts", {})}   # noqa: SLF001
    return tuple(sorted(keys))


def _facts_digest(r) -> str:
    """喂给预研的事实摘要。复用撰写方那份清单，不另造一份口径。"""
    cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=r.as_of,
                                  extra_periods=_catalog_periods(r))
    return cat.render_for_prompt()
```

> **实跑与本 Task 计划的八处出入：**
>
> 1. **计划四个测试夹具的 DSL 全都不合法。** `parse` 要求两条分解式都给
>    （缺一半会让读者以为两项都分解了）、每条 2–5 个因子（`MIN_FACTORS = 2`）。
>    计划里的毛利率分解只给 1 个因子、有的只给营收一条。全部修正。
> 2. **`Factor` 的字段叫 `source_hint`，不是 `hint`**（`drivers.py:48`）。
>    计划的 `getattr(f, 'hint', '')` 会静默取到空串 —— 数据线索于是永不参与
>    覆盖判断，而「成本」这类主题常常只出现在数据线索里（「年报营业成本
>    构成」）。补了 `test_数据线索也算覆盖` 钉住它。
> 3. **覆盖标注只在传了 `stance_note` 时才做。** spec 对 `theme_coverage`
>    写的是「themes 为空时返回空列表 —— 不能凭空要求覆盖」，那条逻辑在调用
>    方同样成立：没传辩论结论时模型压根没看到过主题清单，拿五个主题去核对
>    它、再在产物上标「4 个未覆盖」是凭空问责。
> 4. **`_read_drivers` 不在本 Task 提取**（计划 Step 6 的代码用了它）。保留
>    现有的内联读回，按原计划留给 Task 10 Step 0a —— 不跨 Task 动它。
> 5. **`_ledger_keys` 容错**：拿不到 `r.ledger` 返回空元组而不是抛。与紧邻的
>    `_facts_digest` 一条理由：这两个值都是提示词的**可选**输入，缺了只是
>    退回行业级预研，而这个函数整体的设计就是「降级但继续」。
> 6. **`STANCE_THEMES` 抽成模块级常量** —— 辩论① 的 subjects 与覆盖标注的
>    themes 是同一份清单，不各写一遍。
> 7. **补了一条透传测试，因为变异暴露了一个洞。** 摘掉
>    `stance_note=stance_note`（辩论① 的结论根本到不了模型），全量 2178 条
>    **一条都不红** —— 原有测试验的是**形参**（它读 `stance_note` 决定要不要
>    做覆盖标注），而不是那个值有没有真的传给 `methodology.research`。
>    而这一行正是「辩论① 排在预研之前」的唯一兑现处。新增
>    `test_辩论结论与定性结论真的进了预研的提示词`：捕获 client 收到的提示词，
>    断言辩论结论、定性三节、语料库构成都在里面。
> 8. `_methodology_api_failed` 的降级说明里「重跑 `--analyse`」改为
>    「重跑 `--brief`」—— 预研已挪进第二次调用。**过时的指引是会主动误导人的
>    那种错。**
>
> 另外：提取两个失败分支时我的正则匹配到了**第一个**
> `except methodology.MethodologyFailed`（drivers.md 读回那个），吞掉了函数
> 中段。断言（检查分支内容）本该拦住，但我用的词「存活者偏差」在原文里被
> 换行切成了「存活者」+「偏差」，所以断言通过了。缩进错误当场暴露，已重建。
> **教训：按内容断言时，别用可能被换行切断的长词组。**

- [ ] **Step 7: 变异验证「覆盖是标注而不是闸门」**

```bash
cp ir_agent/methodology.py /tmp/meth.bak
python3 - <<'PY'
import pathlib
p = pathlib.Path("ir_agent/methodology.py")
s = p.read_text(encoding="utf-8")
s = s.replace("        out.append((th, hit))",
              "        assert hit, th\n        out.append((th, hit))")
p.write_text(s, encoding="utf-8")
PY
python -m pytest tests/test_methodology.py -q -k 没覆盖的主题
cp /tmp/meth.bak ir_agent/methodology.py
```

Expected: 变异后 `test_没覆盖的主题给空串而不是抛异常` 变红；还原后绿。
**这一步是「标注不阻断」这条设计唯一的机械证据** —— 没有它，把
`theme_coverage` 改成抛异常，全量照样绿。

- [ ] **Step 8: 全量回归并提交**

```bash
python -m pytest -q -m "not live" -W error
```
Expected: PASS。原先 `tests/test_methodology.py` 里断言 `PROMPT` 含某段
实测举例的测试会红 —— 那段举例已按 Step 5 第 3 条删除，**整条删除那个断言**。
`grep -n "98\|覆铜板.*31\|产能.*31" tests/test_methodology.py` 先看一眼有哪些。

```bash
git add ir_agent/methodology.py ir_agent/full.py tests/test_methodology.py
git commit -m "methodology: 预研框架重写 —— 接本轮全部证据与多空结论，两段标注落 drivers.md"
```

---

### Task 10: `full.py` 拆三段与新顺序

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `ir_agent/research.py`（`Stage` 三个新成员）
- Modify: `tests/test_full_context.py`

**Interfaces:**
- Consumes: Task 4/5 的 `stance.run_stance` / `load_manual` / `template_json` / `check_interval`；Task 6 的 `sources.manifest`；Task 7 的 `check_deviation`；Task 8 的 `_industry_path` 与去掉 client 的 `_load_announce`；Task 9 的 `_research_methodology(…, md=, stance_note=)`
- Produces:
  - `run_analyse(code, r, as_of, out_dir, *, model=None, snapshot_dir="snapshots") -> ResearchOutcome`
  - `run_brief(code, r, as_of, out_dir, verified_by, *, model=None, snapshot_dir="snapshots", accept_manual=False, refresh_stance=False) -> ResearchOutcome`
  - `run_assume(code, r, as_of, out_dir, reviewed_by, *, model=None, snapshot_dir="snapshots") -> ResearchOutcome` —— **没有 `accept_manual`**：两轮辩论都在 `run_brief` 里，这一次调用没有任何多空环节，那个开关在这儿无物可读
  - `Stage.AWAITING_SOURCES` / `Stage.AWAITING_BRIEF_REVIEW` / `Stage.AWAITING_ASSUME_REVIEW`
  - 私有助手：`_restore_state` / `_save_state` / `_collect_factors` / `_factor_ctx` / `_stop_for_manual` / `_snapshot_gaps`
  - 文件名常量：`SOURCES_MD="sources.md"`、`SOURCES_JSON="sources.json"`、`STANCE_A="stance_a.json"`、`STANCE_B="stance_b.json"`、`REVIEW_MD="review.md"`

- [ ] **Step 0a: 纯提取三个助手（不改行为，单独一个提交）**

`run_brief` / `run_assume` 要用的这三件事，今天都内联在 `run_analysis` 里。
**先原样搬出来、跑全量确认行为没变、提交**，再动顺序 —— 搬家与改流程分成两个
可独立回滚的提交。搬完 `run_analysis` 改为调用它们，此时它仍是一个完整函数、
行为与搬之前逐字节相同。

先在 `full.py` 已有的文件名常量旁（`DRIVERS_MD = "drivers.md"` 附近）加五条
—— 下面 Step 3/4/5 的代码全靠它们，Interfaces 里列了名字不等于它们存在：

```python
SOURCES_MD = "sources.md"
SOURCES_JSON = "sources.json"
STANCE_A = "stance_a.json"
STANCE_B = "stance_b.json"
REVIEW_MD = "review.md"
```

然后在 `_analyse_qualitative` 之后、`_research_methodology` 之前插入：

```python
def _restore_state(out: Path) -> tuple[dict, dict]:
    """读回上一次调用落盘的定性分析结果（`state.json` 的 `md` / `pr`）。

    与 `run_valuation` 读 `state.json` 的那两行同源 —— 现在有三处要读它，
    形状必须是同一个：缺文件时给空壳而不是抛错，因为「上一阶段没留下 md」
    与「state.json 不存在」对调用方是同一件事。`or {}` 不能省：人手改过的
    state.json 里 `"md": null` 是合法 JSON，`.get("md")` 会返回 None，
    下游 `md.items()` 当场崩。
    """
    f = out / STATE
    state = json.loads(f.read_text(encoding="utf-8")) if f.exists() \
        else {"md": {}, "pr": {}}
    return state.get("md") or {}, state.get("pr") or {}


def _save_state(out: Path, code: str, r, as_of: date, md: dict, pr: dict,
                *, upheld: int | None = None, **signoff: str) -> None:
    """落盘 `state.json`。三次调用都写它，每次把自己那道门的签名补进去。

    **先读旧的再 update，不能整份覆盖。** `upheld` 只有跑过辩论的那次调用
    算得出来；若无条件按默认值写入，后一次调用会把它清成 0 —— 读回时
    「这轮确实是 0」和「这轮没算」长得一样。这正是本项目反复踩的那条缝：
    产物上两种情形不可区分，人就看不见。
    """
    f = out / STATE
    old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    old.update({"code": code, "period": r.period, "as_of": as_of.isoformat(),
                "md": md, "pr": pr})
    if upheld is not None:
        old["upheld"] = upheld
    old.update({k: v for k, v in signoff.items() if v})
    f.write_text(json.dumps(old, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _read_drivers(out: Path):
    """只读 `drivers.md`，读不回就说为什么。**不联网。**

    这段逻辑今天内联在 `_research_methodology` 开头（`full.py:1203`）：
    文件在就读回、不在就往下走去联网预研。`run_assume` 需要的是**只有读回
    那一半** —— 它绝不能在 drivers.md 缺失时悄悄重新联网预研一个新分解式：
    那样人在门② 审过的简报与接下来要填的判断项，依据的是两个不同的分解式。
    """
    from ir_agent import methodology

    path = out / DRIVERS_MD
    if not path.exists():
        return None, ""
    try:
        return methodology.from_markdown(path.read_text(encoding="utf-8")), ""
    except methodology.MethodologyFailed as e:
        return None, (f"方法论预研（{path} 已存在但读不回：{str(e)[:160]}）"
                      "—— 未重新联网预研（避免悄悄覆盖你的修改），"
                      "退回不分解模式，判断项仍按营收增速/毛利率两项填写")


def _collect_factors(r, as_of: date, client, md: dict, decomps,
                     industry: str, skipped: list[str]) -> dict:
    """按分解式定向采集因子数据。整段从 `run_analysis` 原样搬出。

    **`_refresh_external_md` 必须留在 `finally` 里。** 见原处的长注释：
    `gaps.collect` 在循环内部往 `r.corpus` 加文档，第 N 个因子已经往语料库
    里塞了文档、第 N+1 个因子才抛异常时，`md["外部资料"]` 必须被告知，
    否则读者看到的篇数与分级停在这次采集之前那一刻。
    """
    from ir_agent import gaps
    from ir_agent.sources.external import Corpus

    factor_data: dict = {}
    try:
        factor_data = gaps.collect(
            client, decomps, r.ledger, r.corpus or Corpus(as_of=as_of),
            industry, as_of, sections=getattr(r, "sections", None),
            annual_url=getattr(r, "annual_url", ""))
        md["因子数据"] = gaps.summary(factor_data)
    except Exception as e:                                  # noqa: BLE001
        skipped.append(f"因子数据采集（{e.__class__.__name__}: {e}）")
    finally:
        _refresh_external_md(r, md)
    # 轮次级主机安全校验降级聚合。异常路径上确有欠计数且这一层治不好 ——
    # `gaps.collect` 不对外暴露异常路径上的部分结果。详见
    # `_factor_host_check_note` 自己的文档字符串。
    skipped += _factor_host_check_note(factor_data)
    return factor_data
```

`_research_methodology` 开头那段读回改为 `m, err = _read_drivers(out)`：
`err` 非空或 `m` 非 None 时按原样 return，`m is None and not err` 才往下走
联网预研 —— 与搬之前逐字节等价（原来「文件不存在」就是往下走）。

把 `run_analysis` 里原来那三处改为调用：`md, pr` 的来源不动（这一次调用自己
算定性分析，不读回），`state.json` 那一段整体换成
`_save_state(out, code, r, as_of, md, pr, upheld=upheld)`，因子采集那一整段
（含注释）换成：

```python
    factor_data: dict = {}
    if meth is not None:
        factor_data = _collect_factors(r, as_of, research_client, md,
                                       meth.decompositions, meth.industry,
                                       skipped)
```

- [ ] **Step 0b: 跑全量确认行为没变，并提交**

Run: `python -m pytest -q -m "not live" -W error`
Expected: PASS，数量与搬之前一致（搬家不该让任何测试变色）

```bash
git add ir_agent/full.py
git commit -m "full: 提取 _restore_state/_save_state/_collect_factors（纯搬家，行为不变）"
```

- [ ] **Step 0c: 新增两个助手与三个 Stage 成员（带测试）**

这两个助手在 `run_analysis` 里没有对应物 —— 它们是新流程才需要的。

```python
# tests/test_full_context.py
def test_因子采集失败时上下文显式说出来而不是留空段落():
    """**空段落让辩手把「没采到」读成「采到了但没有」。** 前者该降低双方的
    置信度，后者不该 —— 产物上必须能区分。"""
    from ir_agent.full import _factor_ctx

    for empty in (None, {}):
        assert "未成功" in _factor_ctx(empty)


def test_停下来的指引里年度取自报告期而不是当前年(capsys):
    """可粘贴的命令必须真能跑。as_of 是今天（2026），报告期是 2025FY ——
    用 as_of.year 拼出来的 `--year 2026` 会把人引到一个空账本上。

    **断言必须落在年度上。** 第一版这条测试只断言了
    `o.stage is Stage.INCOMPLETE`，名字说的那件事一条没测 —— 把
    `r.period[:4]` 换回 `as_of.year` 它照样绿。
    """
    import datetime
    from ir_agent import full

    class _R:
        period, ledger, quotes, corpus = "2025FY", None, None, None
        verdicts, store = [], None

    class _C:
        total_cost_usd = 0.0

    o = full._stop_for_manual("600183", _R(), datetime.date(2026, 10, 12),
                              __import__("pathlib").Path("out/x"), {}, [],
                              _C(), round_="A")
    err = capsys.readouterr().err
    assert "--year 2025" in err, err
    assert "2026" not in err, "年度取成了 as_of.year"
    assert "--accept-manual" in err, "第二条路（人工补写）没打出来"
    assert o.stage is full.Stage.INCOMPLETE


def test_后一次调用不清掉前一次算出来的_upheld(tmp_path):
    import datetime
    from ir_agent.full import STATE, _restore_state, _save_state

    class _R:
        period = "2025FY"

    d = datetime.date(2026, 10, 12)
    _save_state(tmp_path, "600183", _R(), d, {"a": "1"}, {}, upheld=5)
    _save_state(tmp_path, "600183", _R(), d, {"a": "1"}, {},
                verified_by="xhl")          # 不传 upheld
    import json as _j
    saved = _j.loads((tmp_path / STATE).read_text(encoding="utf-8"))
    assert saved["upheld"] == 5, "没算过 upheld 的调用把它清掉了"
    assert saved["verified_by"] == "xhl"
    assert _restore_state(tmp_path) == ({"a": "1"}, {})
```

Run: `python -m pytest tests/test_full_context.py -q -k "因子采集失败 or 停下来的指引 or 不清掉"`
Expected: FAIL，`ImportError: cannot import name '_factor_ctx'`

实现（`ir_agent/full.py`）：

```python
def _factor_ctx(factor_data: dict | None) -> str:
    """把因子采集结果渲染进辩论②的提示词。

    **采集失败时必须显式说出来**，不能给一个空段落 —— 见
    `test_因子采集失败时上下文显式说出来而不是留空段落`。
    """
    if not factor_data:
        return ("（本轮因子数据采集未成功；以下取值只能基于上面的定量事实"
                "与语料库，双方都应据此降低置信度）")
    from ir_agent import gaps
    return gaps.summary(factor_data)


def _stop_for_manual(code: str, r, as_of: date, out: Path, arts: dict,
                     skipped: list[str], client, *,
                     round_: str) -> ResearchOutcome:
    """多空环节失败时停下，给出两条可粘贴的命令。

    **没有「跳过」这条路。** 每份研报的多空输入要么来自模型、要么来自人。
    已知代价：某家公司反复失败时流程会卡住，直到有人手写那份 JSON。

    年度取自 `r.period` 而不是 `as_of.year`：as_of 是今天，报告期是上一个
    年度，拼错了这条命令就把人引到一个空账本上。
    """
    low = round_.lower()
    cmd = (f"python -m ir_agent {code} --year {r.period[:4]} "
           f"--brief {out} --verified-by <名字>")
    print(f"\n多空辩论{round_} 未完成。最后一稿与待填模板已落盘：\n"
          f"  {out}/stance_{low}_failed_raw.md\n"
          f"  {out}/stance_{low}.template.json\n\n"
          f"  ① 原样重试（模型重抽）\n     {cmd}\n\n"
          f"  ② 人工补写：按模板填好 {out}/stance_{low}.json，然后\n"
          f"     {cmd} --accept-manual\n", file=sys.stderr)
    skipped.append(f"多空辩论{round_} 未完成 —— 需重跑或人工补写")
    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=Stage.INCOMPLETE,
        artifacts=arts, skipped=skipped,
        cost_usd=Decimal(str(client.total_cost_usd)), phase="brief")
```

三个 `Stage` 成员（`ir_agent/research.py`，`class Stage` 内紧随
`AWAITING_REVIEW`）—— **必须在本 Task 建好**，下面 Step 3/4 就要用；
Task 12 的 `next_actions` 只是再读它们：

```python
    AWAITING_SOURCES = "awaiting_sources"              # 停在门①：来源待核验
    AWAITING_BRIEF_REVIEW = "awaiting_brief_review"    # 停在门②：简报待审阅
    AWAITING_ASSUME_REVIEW = "awaiting_assume_review"  # 停在门③：判断项待核实
```

Run: `python -m pytest tests/test_full_context.py -q -k "因子采集失败 or 停下来的指引 or 不清掉"`
Expected: PASS

```bash
git add ir_agent/full.py ir_agent/research.py tests/test_full_context.py
git commit -m "full: 新增 _factor_ctx/_stop_for_manual 与三个待审 Stage"
```

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
    class _AnalyseR:          # 不要叫 _R —— 模块级已有一个只带 ledger 的 _R
        period, as_of, ledger, quotes, corpus = "2025FY", None, None, None, None
        verdicts, store = [], None

    full.run_analyse("600183", _AnalyseR(),
                     __import__("datetime").date(2026, 10, 12), tmp_path)
    assert "定量采集" in calls and "定性分析" in calls
    assert "方法论预研" not in calls, "预研不再属于第一次调用"
    assert "撰写" not in calls, "撰写不再属于第一次调用"
    # 门① 的清单是本次调用的交付物，断言文件真的在磁盘上 —— 不 monkeypatch
    # 一个「写清单」的函数来替代：那样断言的是「我们调了它」，而人要的是
    # 「清单在那儿」。
    assert (tmp_path / "sources.md").exists(), "门① 的来源清单没有落盘"


def _stub_brief(monkeypatch, full):
    """把 `run_brief` 的五个模型环节打掉，只记录它们被调用的先后。

    **断言记录下来的调用顺序，不断言源码里字符串的先后。** 本会话有四次
    「结构式断言盯着源码字面量」而误报或漏报的记录；更现实的是字面量会漂：
    因子采集一旦从内联改为 `_collect_factors(...)`，`src.index("gaps.collect(")`
    当场 `ValueError`，而流程顺序其实一点没变 —— 测试红了却不是因为它要守的
    那条不变量被破坏。
    """
    seq: list[str] = []

    class _S:                      # 冒充 StanceResult
        unverified = ()
        balances = {}

        def summary(self):
            return ""

        def to_json(self):
            return {}

    def stance_round(*a, round_=None, **k):
        seq.append(f"辩论{round_ or a[3]}")
        return _S()

    class _M:
        decompositions, industry = [], "测试业"

    monkeypatch.setattr(full, "_stance_round", stance_round)
    monkeypatch.setattr(full, "_research_methodology",
                        lambda *a, **k: (seq.append("预研"), (_M(), ""))[1])
    monkeypatch.setattr(full, "_collect_factors",
                        lambda *a, **k: (seq.append("因子采集"), {})[1])
    monkeypatch.setattr(full, "_factor_ctx", lambda d: "")
    return seq


def _brief_client():
    """假客户端。照 `tests/test_full_context.py` 既有写法：带属性的函数。"""
    def c(prompt):                       # pragma: no cover - 不该被调用
        raise AssertionError("不该真的调模型")
    c.total_cost_usd = 0.0
    return c


class _BriefR:
    """`run_brief` 读到的最小 r。模块级那个 `_R` 只有 ledger，不够用。

    **`ledger` 必须是真的空账本，不能是 `None`。** `run_brief` 会调
    `FactCatalog.from_ledger(r.ledger, ...)`，传 None 进去当场崩，而那不是
    这条测试要测的东西。
    """
    period, as_of = "2025FY", None
    store = corpus = quotes = sections = None
    verdicts, audit, annual_url = [], None, ""

    def __init__(self):
        self.ledger = FactLedger()


def test_第一阶段之二的五个环节按新顺序跑(tmp_path, monkeypatch):
    """**替换掉旧的 `test_预研排在数据采集之前`。** 旧不变量（预研在**所有**
    采集之前）按 spec 已废除 —— 新顺序让分解式能基于全部证据与多空结论设计。
    但预研仍须在**因子**采集之前，否则「按分解式定向采集」无从谈起；而两轮
    辩论必须一前一后夹住它们。
    """
    import datetime

    from ir_agent import full

    seq = _stub_brief(monkeypatch, full)
    monkeypatch.setattr(full, "make_client", lambda **k: _brief_client())
    monkeypatch.setattr(full, "_analyse_facts", lambda *a, **k: None)
    monkeypatch.setattr(full, "_restore_state", lambda out: ({}, {}))
    monkeypatch.setattr(full, "_build_evidence", lambda r, code: None)
    monkeypatch.setattr(full, "_catalog_periods", lambda r: ())
    # `write_body` patch 在 `full` 上（它是 full.py 顶部的模块级名字），
    # 返回空正文 —— 顺序测试只关心它被调到，不关心正文内容。
    monkeypatch.setattr(full, "write_body",
                        lambda *a, **k: (seq.append("撰写"), ("", None))[1])
    full.run_brief("600183", _BriefR(), datetime.date(2026, 10, 12),
                   tmp_path, "xhl")
    assert seq == ["辩论A", "预研", "因子采集", "辩论B", "撰写"], seq
```

- [ ] **Step 2: 跑测试确认红**

Run: `python -m pytest tests/test_full_context.py -q -k "第一阶段之一 or 第一阶段之二"`
Expected: FAIL，`AttributeError: module 'ir_agent.full' has no attribute 'run_analyse'`

> 选择器要跟着测试名改。此前这里写的是 `-k "… or 预研排在因子 or 多空辩论一"`，
> 而那两个名字在 Step 1 改写顺序测试时就已经不存在了 —— `-k` 匹配不到任何
> 测试时 pytest 报的是「collected 0 items」而**不是失败**，于是这一步会
> 「通过」。`-k` 写错的失败模式是静默的。

- [ ] **Step 3: 实现 `run_analyse`**

把现有 `run_analysis` 复制为 `run_analyse`，**只保留**到定性分析为止的部分（取数 → `_analyse_facts` → `_load_openweb` → `_load_announce` → `_analyse_qualitative`），然后：

```python
    # ── 门①：来源清单 ───────────────────────────────────────────
    from ir_agent.sources.manifest import collect_rows, missing_files, render

    # `peers` 不在 `r` 上 —— `peer_set(code)` 的结果是 `_load_industry`
    # 的局部变量，从未挂到 r。写成 `getattr(r, "peers", ())` 会恒取到空元组，
    # 于是门① 的同业样本名单永远是空的，而 spec 要的正是让人看见
    # 「牧原 11 家 vs 生益 46 家」这个代表性差异。用 `_build_evidence` 已经
    # 在用的那条路：`_peer_names(code)`。
    rows = collect_rows(r.ledger, corpus=getattr(r, "corpus", None),
                        pdf_dir=out / "pdf", peers=_peer_names(code))
    gaps = _snapshot_gaps(r)          # [(来源名, 条数)]，见下一步
    (out / SOURCES_MD).write_text(render(rows, gaps), encoding="utf-8")
    (out / SOURCES_JSON).write_text(
        json.dumps({"rows": [vars(x) for x in rows], "verified_by": ""},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    arts["资料来源清单"] = str(out / SOURCES_MD)
    for bad in missing_files(rows):
        skipped.append(f"来源清单：{bad}")

    # ── 看板与工作簿（④：第①②两次调用各生成一次）──────────────
    # 门① 核来源的人需要一个交叉核对的入口；两者都不调模型、成本近零。
    build_dashboard(r, path=out / "dashboard.html", context=md)
    arts["看板"] = str(out / "dashboard.html")
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)
    arts["工作簿"] = str(out / "model.xlsx")

    skipped += _media_tier_note(r.corpus)
    _save_state(out, code, r, as_of, md, pr)
    # **必须真的 return。** 上一版这里只写到 `stage = Stage.AWAITING_SOURCES`
    # 就断了 —— 那正是本计划自己警告的「调用存在但返回没绑定」那一形：
    # 读的人以为写完了，而函数隐式返回 None，`report()` 当场崩。
    return ResearchOutcome(code=code, period=r.period, as_of=as_of,
                           stage=Stage.AWAITING_SOURCES,
                           artifacts=arts, skipped=skipped,
                           cost_usd=Decimal(str(cost)), phase="analyse")
```

并新增私有助手：

```python
def _snapshot_gaps(r) -> list[tuple[str, int]]:
    """统计本轮涉及的「缺原始接口快照」来源及条数。

    这条告警此前每轮都在终端重复一遍。它属于**来源质量**，归门①说一次。
    """
    from collections import Counter
    from ir_agent.sources.manifest import KNOWN_GAPS

    # **`_facts` 的值是 `list[Fact]`，不是单条 Fact**（`ledger.py:63`）。
    # 写成 `for (key, per), f in ….items()` 再读 `f.source_id`，拿的是 list
    # 的属性 —— 恒得空串，于是这个函数永远返回空列表，门① 的「缺原始接口
    # 快照」那一节一次也不会打印。而那一节正是这道门的首要动机：这条告警
    # 连续六轮只在终端一闪而过。实跑 Task 6 时在同形的 collect_rows 上
    # 踩到过同一处。
    c: Counter = Counter()
    for bucket in getattr(r.ledger, "_facts", {}).values():         # noqa: SLF001
        for f in bucket:
            sid = (getattr(f, "source_id", "") or "").split("#")[0]
            for name in KNOWN_GAPS:
                if sid.startswith(name):
                    c[name] += 1
    return sorted(c.items())
```

- [ ] **Step 4: 实现 `run_brief`**

```python
def run_brief(code, r, as_of, out_dir, verified_by, *, model=None,
              snapshot_dir="snapshots", accept_manual=False,
              refresh_stance=False):
    """第一阶段之二：辩论① → 预研 → 因子采集 → 辩论② → 撰写简报。

    **五个模型环节逐个立刻落盘。** 今天的实测里这五个各自都失败过；没有中间
    落盘点的话，失败会丢掉已完成的部分。
    """
    import json

    # **`assume` 必须在这里导入。** 下面 `list(assume.DRIVERS)` 要用它，
    # 而 `full.py` 顶部**没有** `from ir_agent import assume`（`grep -c` 得 0，
    # 全仓库都是在函数内导）—— 不导就是 NameError。
    from ir_agent import assume

    # **不要在这里 `from ir_agent.analyst import write_body`。** 它在
    # `full.py` 顶部（第 16 行）已是模块级名字；函数内再导一次会在每次调用时
    # 重新绑定成本地名，`monkeypatch.setattr(full, "write_body", ...)` 于是
    # patch 不到 —— 测试会静默地去调真模型。
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
    sa = _stance_round(client, out, STANCE_A, "A", themes, ctx,
                       cat, r, as_of, accept_manual, verified_by, skipped,
                       refresh=refresh_stance)
    if sa is None:
        return _stop_for_manual(code, r, as_of, out, arts, skipped, client,
                                round_="A")
    arts["多空辩论①"] = str(out / "stance_a.md")

    # ── 方法论预研（现在能看到全部证据与多空结论）──────────────
    # 签名见 Task 9 Step 6：`md` 是定性分析结果（辩论① 与预研共用的那份），
    # `stance_note` 是辩论① 的平衡结论 —— **这是「辩论① 排在预研之前」
    # 唯一的兑现处。** 不传这个参数，辩论① 的结论就到不了预研，流程图上
    # 它在那儿而数据流上它不在。
    meth, merr = _research_methodology(code, r, out, client,
                                       md=md, stance_note=sa.summary())
    if merr:
        skipped.append(merr)

    # ── 因子定向采集 ───────────────────────────────────────────
    factor_data = None
    if meth is not None:
        factor_data = _collect_factors(r, as_of, client, md,
                                       meth.decompositions, meth.industry,
                                       skipped)

    # ── 多空辩论②：定取值 ─────────────────────────────────────
    subjects = ([f.label for d in meth.decompositions for f in d.factors]
                if meth is not None else list(assume.DRIVERS))
    sb = _stance_round(client, out, STANCE_B, "B", subjects,
                       ctx + "\n\n【因子采集】\n" + _factor_ctx(factor_data),
                       cat, r, as_of, accept_manual, verified_by, skipped,
                       refresh=refresh_stance)
    if sb is None:
        return _stop_for_manual(code, r, as_of, out, arts, skipped, client,
                                round_="B")
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
        _write_review(out, sa, sb, _snapshot_gaps(r))   # 门② 的待核清单
        arts["待核清单"] = str(out / REVIEW_MD)
    # ── 看板与工作簿 ────────────────────────────────────────────
    # **这两件产物今天在 `run_analysis` 末尾生成；`run_analysis` 删掉之后
    # 必须有人接着做，否则它们无声消失** —— 没有任何测试断言这两个文件，
    # 所以全量绿不代表它们还在。放在本次调用而不是第①次：`md["因子数据"]`
    # 是上面刚采集的，看板要拿最完整的那份 md。
    build_dashboard(r, path=out / "dashboard.html", context=md)
    arts["看板"] = str(out / "dashboard.html")
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)
    arts["工作簿"] = str(out / "model.xlsx")

    # 依据核验过、但来源分级是 MEDIA 的记录 —— 警告，不拒绝。放在最后一刻
    # 读：本次调用所有会调 `basis.validate()` 的环节此时都已跑完。
    skipped += _media_tier_note(r.corpus)
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
def _stance_round(client, out, fname, round_, subjects, ctx, cat, r,
                  as_of, accept_manual, by, skipped, refresh=False):
    """跑一轮多空辩论，或读回已有产物 / 人工补写。失败则落盘模板，返回 None。

    **不存在「跳过」**：每份研报的多空输入要么来自模型、要么来自人。

    **已有产物算数（③）**：与 `drivers.md` 同一条规矩 —— 文件在就读回，
    不再重抽。两条理由：门② 打回来重跑一次 `--brief`，重抽就是 6 次模型
    调用全做一遍；而人若手改过 `stance_b.json` 的某个三元组，重抽会把那次
    修改悄悄冲掉，正是 `drivers.md` 当初要堵的洞。要重抽给 `--refresh-stance`。
    """
    import json

    from ir_agent import stance

    path = out / fname
    if path.exists() and not refresh:
        if accept_manual:
            res = stance.load_manual(path, round_=round_, by=by)
            skipped.append(f"多空辩论{round_}：由 {by} 人工补写")
        else:
            # 模型产出的已有稿。**区间校验照样走** —— 它守的是「平衡点对
            # 两侧可问责」，与这份 JSON 是谁写的、什么时候写的无关；文件
            # 可能被人手改过。
            res = stance.StanceResult.from_json(
                json.loads(path.read_text(encoding="utf-8")))
            if (probs := stance.check_interval(res.balances)):
                _dump_refused(out, path.read_text(encoding="utf-8"),
                              f"stance_{round_.lower()}_failed_raw.md")
                skipped.append(f"多空辩论{round_} 已有产物不合法（"
                               f"{'；'.join(probs)[:160]}）—— 请修正该文件，"
                               f"或加 --refresh-stance 重抽")
                return None
            skipped.append(f"多空辩论{round_}：沿用已有产物"
                           f"（要重抽加 --refresh-stance）")
        # **`.md` 要按读回的内容重写，`.json` 不动。** 人手改过 JSON 之后，
        # 旁边那份 `.md` 还是上一轮模型的摘要 —— 两个文件说的不是同一件事，
        # 而人读的是 `.md`。只重写 `.md`：`.json` 是人的输入，不该被
        # `to_json()` 往返一遍（会把人加的未知字段抹掉）。
        (out / f"stance_{round_.lower()}.md").write_text(res.summary(),
                                                         encoding="utf-8")
        return res
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


def _write_review(out, sa, sb, gaps_rows) -> None:
    """门② 的待核清单：把所有「代码没把握」的东西聚到一处。"""
    lines = ["# 待核清单（门②：请在批准简报结论前过一遍）", ""]
    for name, s in (("多空辩论①", sa), ("多空辩论②", sb)):
        if s is not None and s.unverified:
            lines += [f"## {name}：{len(s.unverified)} 条证据代码核验不过", ""]
            lines += [f"- [{e.side}] {e.subject}：{e.unverified}"
                      for e in s.unverified] + [""]
    # **不要写成 `getattr(audit_res, "unsnapshotted", None)`。** `audit()`
    # 的返回值没有这个属性（全仓库查无此名），`getattr` 带默认值会让这一节
    # 永远不打印 —— 待核清单上写着它会列这件事，而它一次也不会列。这正是
    # 本项目反复踩的「声明与现实之间的缝」。改为传入 `_snapshot_gaps(r)`
    # 已经算好的那份清单。
    if gaps_rows:
        lines += ["## 本轮账本事实缺原始接口快照（结构性限制，非本轮故障）", ""]
        lines += [f"- {name}：{n} 条" for name, n in gaps_rows] + [""]
    if len(lines) == 2:
        lines.append("本轮没有需要人工复核的标注项。")
    (out / REVIEW_MD).write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 5: 实现 `run_assume`**

```python
def run_assume(code: str, r, as_of: date, out_dir: str | Path,
               reviewed_by: str, *, model: str | None = None,
               snapshot_dir: str | Path = "snapshots") -> ResearchOutcome:
    """第一阶段之三：AI 填 DCF 判断项 + 四道检查。

    判断项的三档候选输入来自 `stance_b.json` 的平衡三元组；偏离该三元组的
    行必须填 `deviation`（Task 7，只查存在性）。
    """
    import json

    from ir_agent import stance

    out = Path(out_dir)
    brief = out / BRIEF
    sb_path = out / STANCE_B
    for f in (brief, sb_path):
        if not f.exists():
            raise SystemExit(
                f"找不到 {f} —— 请先运行第一阶段之二：\n"
                f"  python -m ir_agent {code} --year {r.period[:4]} "
                f"--brief {out} --verified-by <名字>")
    if not reviewed_by.strip():
        raise SystemExit("必须署名审阅人 —— 简报结论要有人审过才谈得上可据以建模。")

    arts: dict[str, str] = {}
    skipped: list[str] = []
    body = brief.read_text(encoding="utf-8")
    sb = stance.StanceResult.from_json(
        json.loads(sb_path.read_text(encoding="utf-8")))
    md, pr = _restore_state(out)
    # **重建派生事实必须排在填判断项之前。** industry.* / comps.* / macro.*
    # 只存在于内存账本，由 `_analyse_facts` 算出来；判断项的依据完全可以
    # 合法地引用它们（写成 `[[industry.dtl@2025FY]]`），而本次调用是一个
    # 新进程，账本刚从接口重建、还没跑派生层。与 `run_valuation` 里那条
    # 同源的实测教训（600584 端到端）：不重建的话，人已经批准的假设会在
    # 下一步被判为「引用的事实在账本中不存在」—— 理由不是假设有问题，
    # 是校验时账本还没建好。
    _analyse_facts(code, r, as_of, snapshot_dir)

    # **单独一个客户端，开超时重试、给更长的超时。** 这一步是全链路输出最长
    # 的一次调用（7~8 个因子 × 5 年 × 3 档），瓶颈在输出而不在提示词长度。
    # 实测（生益科技 2026-10-06）：它 900s 超时，整轮 $11.09 作废。
    client = make_client(model=model, timeout=ASSUME_TIMEOUT,
                         retry_timeout=True)
    meth, derr = _read_drivers(out)    # 只读 drivers.md，不联网重新预研
    if derr:
        skipped.append(derr)
    aerr = _propose_assumptions(
        code, r, body, md, as_of, out, client, arts,
        decomps=(meth.decompositions if meth is not None else None),
        factor_data=None, balances=sb.balances)
    if aerr:
        skipped.append(aerr)
    _save_state(out, code, r, as_of, md, pr, reviewed_by=reviewed_by)
    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of,
        stage=(Stage.AWAITING_ASSUME_REVIEW
               if (out / ASSUMPTIONS_JSON).exists() else Stage.INCOMPLETE),
        artifacts=arts, skipped=skipped,
        cost_usd=Decimal(str(client.total_cost_usd)), phase="assume")
```

`_propose_assumptions` 加一个 `balances=None` 参数并透传给 `assume.decide`
（Task 7 已给 `decide` / `check` / `_decide_once` 加好同名参数）：

```python
def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
                         decomps=None, factor_data=None, balances=None) -> str:
```

调用 `assume.decide(...)` 处补 `balances=balances`。

`_read_drivers(out)` 已在 Step 0a 从 `_research_methodology` 提取出来。
**这里必须用只读的那一半**：`_research_methodology` 在 drivers.md 缺失时会
联网重新预研，而本次调用若那样做，人在门② 审过的简报与接下来要填的判断项
就依据两个不同的分解式 —— 而产物上看不出来。

- [ ] **Step 6: 删除 `run_analysis` 并把旧测试指向新函数**

```bash
python -m pytest -q -m "not live" -W error
```
Expected: 原先引用 `run_analysis` 的测试会失败，逐个改为对应的新函数；`test_预研排在数据采集之前` 整条删除（已被 Step 1 的新测试替换）。

> **实跑与本 Task 计划的出入（Step 1–6）：**
>
> 1. **计划的 `run_brief` 有一个会静默生效的回退。** 它只开了一个
>    `make_client(model=model)`、不带联网工具，拿它跑预研和因子采集。而
>    `run_analysis` 里专门写着：「不开的话不会报错，只会安静地什么都搜不到」。
>    改为两个 client：`research_client`（带 WebSearch/WebFetch）给预研与因子
>    采集；`client`（不带）给辩论与撰写 —— 后两者只能用已入库的材料，联网
>    会让引文出处跑到语料库之外、核验不过。新增测试钉住，变异验证过。
> 2. **计划 Step 1 的测试照抄会崩在六处**（桩的返回形状与调用方的解包不符、
>    假 client 没有 `total_cost_usd`、`_peer_names` 真去联网……）。换成共用的
>    离线夹具 `_offline` / `_stub_brief`。
> 3. **补三条计划没有的测试**：预研与因子采集拿到的是带联网工具的 client；
>    `run_brief → _research_methodology` 这一段真的透传了辩论① 结论（Task 9
>    只验了下一段 `_research_methodology → research()`，两段缺一段结论都到不了
>    模型）；辩论① 失败时停下而不是跳过。四个变异各打红一条。
> 4. **`_write_and_debate` 删除。** 它只被 `run_analysis` 调用，spec 也写明
>    第一阶段的成稿辩论删除。但调它的四条测试守的是「辩论/回写失败时正文
>    留不留得住」—— 那是 `_debate_and_revise` 的行为，第二阶段还在用 —— 所以
>    改为直接测它，不删。茅台那条（被驳回的稿子必须落盘）改验 `run_brief` 的
>    撰写失败路径。
> 5. **`DEBATE_BRIEF` 删除**（死常量）。「两阶段辩论记录不撞名」那条测试泛化为
>    「第二阶段的辩论记录不与第一阶段任何产物撞名」。
> 6. **CLI 的 `--analyse` 在本 Task 就改调 `run_analyse`**，计划没提。删掉
>    `run_analysis` 而 CLI 要到 Task 11 才改，中间这段 `--analyse` 实跑会
>    ImportError —— 正是评审时反对过的破损窗口。
> 7. `run_assume` 里的 client 叫 `assume_client`：两条既有测试
>    （判断项开了超时重试、创建早于开销累加）靠这个名字区分「判断项不借用
>    别的 client」。
> 8. 14 条盯 `run_analysis` 源码的结构测试逐条判断不变量现在住在哪：6 条改指
>    `run_analyse`、2 条 `run_brief`、2 条 `run_assume`、1 条（预研失败不中断）
>    从「源码里有 MethodologyFailed 字样」改为行为断言，1 条随 spec 删除
>    （`test_预研排在数据采集之前`）。Step 0a 时另有 3 条改指 `_collect_factors`，
>    并补了一条守卫：那个函数真的被流水线调用。
> 9. 抽出 `_write_dashboard`，两次调用共用（spec ④）。`_stance_round` 读回已有
>    产物时，人手改坏的 JSON（`ValueError`/`KeyError`）记进 skipped 并停下，
>    不崩。
>
> **Step 7 的检查方法本身要换。** 计划写的是 `git diff main | grep -c`：我们就在
> main 上，`git diff main` 只看未提交的部分；而且 grep 会把文档字符串里**提到**
> 这几个函数名的行也算进去。实跑改为：取重构开始前（`8f52537`）与现在的
> `full.py`，用 AST 把 `run_valuation` / `_write_full_report` /
> `_debate_and_revise` / `report` / `_analyse_facts` 的源码段逐字节比对 —— 五个
> 全部相同。`_propose_assumptions` 有改动，difflib 确认只有两处（加 `balances`
> 形参、透传给 `assume.decide`），属第一阶段，符合预期。

- [ ] **Step 7: 机械确认第二阶段没被碰到**

```bash
git diff main -- ir_agent/full.py | grep -c "^[+-].*\(run_valuation\|_write_full_report\|_debate_and_revise\)"
```
Expected: `0`

本 Task 重写的 `run_analysis` 与 `run_valuation` 在同一个文件里相邻，删一个
200 行的函数时很容易多带走几行。非零就逐行看那几处是什么 —— 若确实只是
`run_valuation` 的调用方改名之类，在提交信息里写明；否则改回去。

- [ ] **Step 8: 提交**

```bash
git add ir_agent/full.py tests/test_full_context.py
git commit -m "full: 第一阶段拆为 analyse/brief/assume 三段，新顺序与逐步落盘"
```

---

### Task 11: CLI 三个新子命令与签名参数

**Files:**
- Modify: `ir_agent/__main__.py`
- Modify: `tests/test_full_context.py`

**Interfaces:**
- Consumes: Task 10 的 `full.run_analyse` / `full.run_brief` / `full.run_assume`（已实现）
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


def test_签名缺失即拒绝运行并且不先去取数():
    """与 --approved-by 同一处理：**要有人负责才谈得上「已核验」。**

    **这条测试不连网，而这正是它要守的事。** 校验若排在 `run()` 之后，
    这里会真的去抓巨潮与三表 —— 测试变慢、离线就红，而真实用户的代价是
    「忘了署名要等整轮取数跑完才被告知」。`monkeypatch` 把 `run` 换成会
    炸的函数：校验正确地排在它前面，就永远不会碰到它。
    """
    import ir_agent.__main__ as M

    def _boom(*a, **k):                 # pragma: no cover - 不该被调用
        raise AssertionError("校验排在取数之后了")

    orig, M.run = M.run, _boom
    try:
        for argv in (["600183", "--year", "2025", "--brief", "out/x"],
                     ["600183", "--year", "2025", "--assume", "out/x"],
                     ["600183", "--year", "2025", "--value", "out/x"]):
            assert M.main(argv) == 2, f"缺签名却没有拒绝：{argv}"
    finally:
        M.run = orig


def test_assume配accept_manual直接报错():
    """**静默忽略一个用户显式给出的开关是最坏的形状** —— 人会以为自己的
    补写生效了。两轮辩论都在 --brief 里，--assume 无物可读。"""
    import ir_agent.__main__ as M

    def _boom(*a, **k):                 # pragma: no cover
        raise AssertionError("校验排在取数之后了")

    orig, M.run = M.run, _boom
    try:
        assert M.main(["600183", "--year", "2025", "--assume", "out/x",
                       "--reviewed-by", "xhl", "--accept-manual"]) == 2
    finally:
        M.run = orig


def test_refresh_stance是开关而且只对brief有意义():
    from ir_agent.__main__ import build_parser

    a = build_parser().parse_args(
        ["600183", "--year", "2025", "--brief", "out/x",
         "--verified-by", "xhl", "--refresh-stance"])
    assert a.refresh_stance is True


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
                   help="读回人工补写的 stance_*.json，跳过该环节的模型调用"
                        "（只对 --brief 有意义）")
    p.add_argument("--refresh-stance", action="store_true",
                   help="重抽多空辩论，覆盖已有的 stance_a/b.json"
                        "（默认沿用已有产物，与 drivers.md 同一规矩）")
```

**先把参数校验提到取数之前。** 在 `main()` 里 `a = build_parser().parse_args(argv)`
之后、`r = run(...)` 之前插入：

```python
    if (err := _check_args(a)):
        print(err, file=sys.stderr)
        return 2
```

并新增（`__main__.py` 内，`main` 之前）：

```python
def _check_args(a) -> str:
    """参数自洽性检查。**必须排在取数之前。**

    今天 `--approved-by` 的校验排在 `run()` 之后（`__main__.py:68`），于是
    忘了署名的人要等整轮取数（几千条事实、三表勾稽、每家同业各一次接口）
    跑完，才被告知「你没署名」。参数错误是**在读任何数据之前**就能判定的，
    放在取数之后纯属浪费人的时间；顺带这也让签名那条单元测试不必连网。

    `--assume --accept-manual` **报错而不是静默忽略**：两轮辩论都在
    `--brief` 里，这一次调用没有任何多空环节，那个开关在这儿无物可读。
    静默忽略一个用户显式给出的开关，会让人以为自己的补写生效了。
    """
    for flag, sig, why in (
            ("brief", "verified_by", "资料来源要有人核验过才谈得上可用"),
            ("assume", "reviewed_by", "简报结论要有人审阅过才谈得上可据以建模"),
            ("value", "approved_by", "假设要有人负责才谈得上「已核实」")):
        if getattr(a, flag) and not getattr(a, sig):
            return (f"--{flag} 需同时给出 --{sig.replace('_', '-')} <署名> "
                    f"—— {why}。")
    if a.assume and a.accept_manual:
        return ("--accept-manual 只对 --brief 有意义 —— 两轮多空辩论都在那一次"
                "调用里，--assume 没有可读回的 stance_*.json。")
    if a.refresh_stance and not a.brief:
        return "--refresh-stance 只对 --brief 有意义。"
    return ""
```

在 `main()` 中，`if a.analyse:` 之后、`if a.value:` 之前加入：

```python
    # 签名校验已在 `_check_args` 里做过（取数之前），这里不再重复。
    if a.brief:
        from ir_agent.full import report, run_brief
        print(r.summary())
        report(run_brief(a.code, r, as_of, a.brief, a.verified_by,
                         model=a.model, snapshot_dir=a.snapshots,
                         accept_manual=a.accept_manual,
                         refresh_stance=a.refresh_stance),
               out_dir=a.brief, year=a.year)
        return 0

    if a.assume:
        from ir_agent.full import report, run_assume
        print(r.summary())
        report(run_assume(a.code, r, as_of, a.assume, a.reviewed_by,
                          model=a.model, snapshot_dir=a.snapshots),
               out_dir=a.assume, year=a.year)
        return 0
```

把 `--value` 原有的那一处校验（`__main__.py:68-71` 的 `if not a.approved_by`）
**删掉** —— `_check_args` 已经覆盖它。`run_valuation` 内部那句 `SystemExit`
（`full.py:1892`）**保留**：它是给直接调 API 的人留的第二道，不经过 CLI。
`run_brief` / `run_assume` 也各要有同形的函数层校验（今天三者里只有
`run_valuation` 有，`run_assume` 有、`run_brief` 没有 —— 补齐）。

- [ ] **Step 4: 跑测试确认绿并提交**

```bash
python -m pytest tests/test_full_context.py -q -k "四个阶段 or 签名缺失 or accept_manual or refresh_stance"
git add ir_agent/__main__.py tests/test_full_context.py
git commit -m "cli: 新增 --brief / --assume / --refresh-stance，签名校验提到取数之前"
```

> **实跑出入：计划给的两条测试是空心的。** 它们用一个会抛 `AssertionError` 的
> 桩顶替 `run`，期望「校验排在取数之后」时它会炸。但 `main()` 把 `run()` 包在
> `except Exception` 里、接住后返回 2 —— 而 2 正是「正确拒绝」的退出码。变异
> 验证时把校验挪回取数之后、或把 `--assume --accept-manual` 静默放过，**两条都
> 照样绿**。两种情形产生同一个可观测结果，测试恒真。改为 `_spy_run` 记录调用、
> 断言 `run` 没被调用；重做变异后各自变红。另外 `--analyse` 的帮助文本还写着
> 「分析+简报+辩论+回写+AI 填 DCF 判断项」，一并改了。
>
> 注：本 Task 排在 Task 10 之后，所以提交完成时 `--brief` / `--assume` 立刻可实跑。
> 最初的排法是反的（CLI 先、实现后），理由写的是「参数协议先定，实现跟上」——
> 那个理由只在多人并行实现、需要先对齐接口时成立；单线执行没有这个收益，
> 代价却是实打实的：那段时间打给用户的命令会 ImportError。

---

### Task 12: `next_actions` 四状态与三签名落盘

**Files:**
- Modify: `ir_agent/research.py`
- Modify: `ir_agent/full.py`
- Modify: `tests/test_research.py`

**Interfaces:**
- Consumes: Task 10 的产物文件名常量；Task 10 已建好的 `Stage.AWAITING_SOURCES` / `AWAITING_BRIEF_REVIEW` / `AWAITING_ASSUME_REVIEW`（本 Task 只读，不再新增）
- Produces: `_PREFIX` 新增三条前缀；`next_actions` 认四状态

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

三个 `Stage` 成员已在 Task 10 Step 0c 建好（Task 10 的 `run_analyse` / `run_brief`
就要返回它们），**本 Task 不要再加一遍** —— 直接用。

```python
# ir_agent/research.py：_PREFIX 新增三条
    # 多空辩论的状态行。「人工补写」是**成功**路径（人兜底了），
    # 「未完成」才是缺口 —— 两者刻意分开前缀。
    ("多空辩论", "  · "),
    # 来源清单里的文件缺失：清单**产出了**，只是其中一条指向的文件不在盘上。
    ("来源清单：", "  ⚠ "),
```

`next_actions` 按新状态给命令（每条都带完整的 code / --year / 目录 / 签名参数）。

- [ ] **Step 3: 三个签名各落在自己那道门的产物上（①B）**

**不进最终研报附录** —— `_write_full_report` 属于第二阶段，一行不改。
已知代价（spec 已写明）：半年后读最终研报的人，附录上仍然只有一行
「判断项经 xhl 核实」，另两个签名要翻 `out/X/` 里的 JSON。这是为保住
「第二阶段一行不改」付的价，签名的对外可见性延后到另一个任务。

**落点三处：**

| 签名 | 写进哪里 | 谁写 |
|---|---|---|
| `verified_by` | `sources.json` 的顶层键 | `run_brief`（它收到这个署名） |
| `reviewed_by` | `state.json` 的顶层键 | `run_assume`（`_save_state(..., reviewed_by=…)`，已在 Task 10 的代码里） |
| `approved_by` | `assumptions.json` | `run_valuation`（**已有**，不改） |

`run_brief` 开头补一段把 `verified_by` 回填进 `sources.json`：

```python
def _sign_sources(out: Path, verified_by: str) -> None:
    """把门① 的署名回填进 `sources.json`。

    `run_analyse` 落盘时写的是 `"verified_by": ""` —— 那时还没人核验。
    署名是下一次调用（`--brief --verified-by`）才带进来的，所以回填发生
    在这里。**读不回就跳过、不崩**：清单本身已经在盘上，签名写不进去是
    个瑕疵，不该让整次调用失败。
    """
    import json

    f = out / SOURCES_JSON
    if not f.exists() or not verified_by.strip():
        return
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return
    d["verified_by"] = verified_by.strip()
    f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
```

并在 `brief.md` / `assumptions.md` 顶部各印一行自己那道门的签名 ——
`brief.md` 由 `run_brief` 在写盘时加一行前缀，`assumptions.md` 走
`assume.describe()` 已有的顶部区域（与 📐 算术后果 / 🔍 因子结构审查 /
⚖️ 假设辩论 并列）。

> **实跑出入：**
>
> 1. **计划的 `_PREFIX` 自相矛盾。** 注释写着「『人工补写』是成功路径，『未完成』
>    才是缺口 —— 两者刻意分开前缀」，却只登记了一条 `("多空辩论", "  · ")`。照抄
>    的话「多空辩论B 未完成」以「·」打出，一次失败被渲染成普通说明。而计划给的
>    测试只断言「不是『未生成』」，照样会过。改为失败前缀按轮次各登记一条、排在
>    通用前缀之前；测试改为分别断言 ⚠ 与 ·。另登记 `方法论预研：`（覆盖标注，
>    全角冒号），与预研**失败**的 `方法论预研（`（全角括号，落「未生成」是对的）区分。
> 2. **Task 10 写的 `run_assume` 按磁盘上有没有 assumptions.json 定状态** ——
>    上一轮残留的文件会让本轮失败冒充成功。改为复用 `_review_stage(arts)`（按本轮
>    登记的产物），它改返回 `AWAITING_ASSUME_REVIEW`。新增测试，变异验证过。
> 3. `ResearchOutcome.summary()` 对门①② 两个新状态没有对应的一行，补上。
> 4. `next_actions` 补「第二/三次调用未完成」两个分支：教人重跑**自己那一次**，
>    而不是 `--analyse`；有待填模板时给出 `--accept-manual` 的用法。
> 5. 签名落盘按 spec ①B 实现为 `_sign_sources`（回填 sources.json）与
>    `_sign_header`（brief.md 顶部印门①，assumptions.md 顶部印门①②，签名从
>    state.json 读 —— 同一个事实只有一个来源）。计划原稿的
>    `_signature_appendix` 是写进最终研报的，与 ①B 矛盾，不用。
> 6. 补一条：指引里的每条命令都拿**真实解析器**解析一遍，不比对字符串。

- [ ] **Step 4: 跑全量并提交**

```bash
python -m pytest -q -m "not live" -W error
git add ir_agent/research.py ir_agent/full.py tests/test_research.py
git commit -m "research: next_actions 认四状态、新前缀注册、三签名进研报附录"
```

---

### Task 13: 全链路行为测试与文档

**Files:**
- Modify: `tests/test_full_context.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1–12 全部

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
    # **打桩的函数必须把它负责的文件写出来。** 下面断言 `drivers.md` 与
    # `assumptions.json` 存在，而这两个文件今天由 `_research_methodology`
    # 与 `_propose_assumptions` 写 —— 打成「只返回对象、不写盘」的桩，
    # 那两条断言必然红，而红的理由不是流程错了，是桩不完整。
    def _meth(code, r, out, client, *a, **k):
        (Path(out) / "drivers.md").write_text("# 驱动因子分解（测试业）\n",
                                              encoding="utf-8")
        return _FakeMeth(), ""

    def _propose(code, r, body, md, as_of, out, client, arts, *a, **k):
        (Path(out) / "assumptions.json").write_text(
            '{"rows": [], "approved_by": ""}', encoding="utf-8")
        (Path(out) / "assumptions.md").write_text("# 判断项\n", encoding="utf-8")
        arts["判断项"] = str(Path(out) / "assumptions.md")
        return ""

    monkeypatch.setattr(full, "_research_methodology", _meth)
    monkeypatch.setattr(full, "_collect_factors", lambda *a, **k: {})
    monkeypatch.setattr(full, "_propose_assumptions", _propose)
    monkeypatch.setattr(full, "build_dashboard", lambda *a, **k: None)
    monkeypatch.setattr(full, "build_workbook", lambda *a, **k: None)
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
    # ①B：三个签名各落在自己那道门的产物上
    import json as _j
    assert _j.loads((tmp_path / "sources.json").read_text(
        encoding="utf-8"))["verified_by"] == "xhl"
    assert _j.loads((tmp_path / "state.json").read_text(
        encoding="utf-8"))["reviewed_by"] == "xhl"
```

四个替身类，**写在本测试文件顶部**（不是「类似上面」—— 照抄下面这段）：

```python
# tests/test_full_context.py 顶部
class _FakeR:
    period, as_of = "2025FY", date(2026, 10, 12)
    quotes = corpus = store = sections = None
    verdicts, audit, annual_url = [], None, ""

    def __init__(self):
        self.ledger = FactLedger()          # 空账本，清单生成要能跑


class _FakeCat:
    entries, tokens, evidence = (), (), None

    def render_for_prompt(self, *a, **k):
        return ""


class _FakeAudit:
    unrendered: list = []

    def with_sources(self):
        return "## 一节\n正文。"


class _FakeMeth:
    decompositions, industry = [], "测试业"
```

**`_FakeAudit` 不要加 `unsnapshotted`。** 真实的 `audit()` 返回值没有这个
属性 —— 给替身加上它，`_write_review` 里那条用 `getattr` 读它的分支就会在
测试里走通、在实跑里永不触发，而测试变成「证明了一件不成立的事」。那条分支
已按本计划改为读 `_snapshot_gaps(r)`；替身也就不该有这个名字。

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
| 四次调用、三道门 | 10, 11, 12 |
| 交接复用现成机制 | 10（`_restore_state` / `_analyse_facts` / `_restore_announce`） |
| 逐步落盘 | 10（`_stance_round` 每轮写盘；撰写后立即写 brief.md） |
| stance 三 agent、两轮形态不同 | 1, 4 |
| 依据核验不阻断 | 3 |
| 平衡值区间阻断 | 2（含变异验证） |
| deviation 存在性 | 7 |
| 取消降级、只有重跑或人工补写 | 5, 10（`_stance_round` 无 skip 分支） |
| 门① sources.md + 文件存在性 | 6, 10 |
| 门② review.md | 10（`_write_review`） |
| 三签名各自落盘（①B） | 12 |
| 删旧不变量、加新不变量 | 10（Step 1 与 Step 6） |
| next_actions 四状态 | 12 |
| 语料库入库：挑选确定性化、上限移到提示词层 | 8 |
| 预研框架重写（五处实质改动 + 覆盖标注不阻断 + 四级命中标注） | 9 |

**2a. `Create:` 的文件必须真的不存在**（实跑踩到）：

```bash
grep -o '^- Create: `[^`]*`' docs/superpowers/plans/2026-10-12-phase1-restructure.md \
  | sed 's/.*`\(.*\)`/\1/' | while read f; do [ -e "$f" ] && echo "✗ 已存在: $f"; done
```

写文件一律先看目标。`>` 重定向对已有文件无声覆盖，而删掉一个测试文件的
唯一症状是「全量总数变小」—— 没有报错、没有告警。本轮就是这么把
`tests/test_sources.py` 的 18 项删掉的。

**2. 占位符扫描**：零处。执行前评审（`12857b5`）清掉了七处「引用任何任务都没定义的名字」，通读又清掉八处（含两处空心测试、一处缺 `return`、一处过期的 `-k` 选择器）。机械复查的办法记在这里，便于每次改完重跑：提取计划里所有被调用的 `_xxx(` 名字，减去计划与仓库里 `def`/`class` 定义过的，差集必须为空。

**3. 类型一致性**：`StanceResult.round_`（带下划线，避免与内置 `round` 冲突）在 Task 1/4/5/10 中一致；`Balance.bear/mid/bull` 为 `Decimal | None`，`check_interval` 与 `check_deviation` 都按 `mid is None` 跳过；`Row.deviation` 为 `str`，`check_deviation` 读 `r.deviation.strip()`。

## 不做（YAGNI，照搬 spec）

- 不做 `--skip-stance`
- 不把平衡三元组直接当判断项（会绕过 `assume` 既有的三档拉开/依据可核验/锚定覆盖全套校验）
- 不改第二阶段任何环节
- 不修 `em_macro` / `eastmoney_report` 缺快照这个结构性缺口（只在门①说明）
