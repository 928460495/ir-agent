# 因子标签关键词提取（B3）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `gaps` 的三级文本匹配（②年报章节、②.5 公司公告、③研报语料库）
在因子标签带括号注解时仍能命中——现在 `_core_label` 只剥后缀，对
`覆铜板下游需求规模(服务器/通信/消费电子PCB用量)` 这类标签原样返回，三级同时归零。

**Architecture:** 不动 `_core_label`（它保持单一职责：剥 `_SUFFIXES` 后缀），
新增 `_core_candidates(label) -> list[str]` 返回按特指度降序的候选词列表
（整串 → 去括号的头 → 头再剥后缀），三个匹配器改成**候选词在外层循环、
章节/文档在内层循环**，第一个非否定命中即停。`_valid_hit` 一行不动。

**Tech Stack:** Python 3，标准库 `re`。无新依赖。

## Global Constraints

- **绝不下探括号内的词。** 括号里是举例/注解（`原材料成本(铜箔/…)` 的括号内
  是成本的**组成项**），拿组成项的命中当整个因子的证据是归因错位。且代价不
  对称：一次巧合命中会把错摆的原文放到 DCF 因子后面并标成「年报披露」，
  一次漏判只是退到 ④。**实测：`服务器` 在真实公告语料里出现 18 次。**
- **`_core_label` 一行不动。** `tests/test_gaps.py:392-399` 那 7 条断言原样
  继续守它，它们编码的是本仓库自己的规范因子名。
- **`_valid_hit` 一行不动，`_SUFFIXES` 一行不动。**
- **不引入「窗口里必须有数字」这类新过滤条件。** 那会同时收紧 ② 与 ③，
  可能把 ow4 那条唯一的研报命中也滤掉。
- **不读 `source_hint`**（实测其引号术语在公告语料命中 0–1 次，那 1 次还是
  章节标题而非数据）。
- **2 字下限保留**，连同既有理由：一两个字的片段几乎到处都能命中。
- **候选词在外层循环、容器在内层。** 顺序写反等于悄悄放弃「按特指度排序」
  这整个决定。
- 不引入任何新的第三方依赖（`re` 是标准库）。
- 测试函数名用中文，与 `tests/test_gaps.py` 既有风格一致。
- 每个任务结束时跑 `python -m pytest tests -q -m "not live" -W error`，
  必须全绿、无警告再提交。基线：`b3-core-label` 分支上 **1944 passed,
  19 deselected**。

---

## File Structure

| 文件 | 职责 | 本计划的改动 |
|---|---|---|
| `ir_agent/gaps.py` | 缺口识别与分级采集 | 加 `import re`、`_PAREN`、`_core_candidates`；改 `_match_annual`/`_match_report`/`_match_announce` 三处循环 |
| `tests/test_gaps.py` | 上述的测试 | 新增 `_core_candidates` 的单元测试、顺序测试、集成测试 |

不新建文件。`_core_candidates` 与 `_core_label`/`_valid_hit` 同属一组文本
匹配助手，放在一起（`_core_label` 之后、`_negated` 之前）符合该文件现有布局。

---

## Task 1: `_core_candidates`（纯函数）

**Files:**
- Modify: `ir_agent/gaps.py`（加 `import re` 到第 31 行附近的 import 区；
  `_PAREN` 与 `_core_candidates` 加在 `_core_label` 函数之后）
- Test: `tests/test_gaps.py`（追加到文件末尾）

**Interfaces:**
- Consumes: `ir_agent.gaps._core_label(label: str) -> str`（既有，不改）
- Produces: `ir_agent.gaps._core_candidates(label: str) -> list[str]`
  —— 按特指度降序，去重，每项长度 ≥2。Task 2 的三个匹配器遍历它。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_gaps.py` 末尾：

```python
# ── B3：因子标签的关键词提取 ──────────────────────────────────
#
# 实跑（out/600183-subB，2026-10-01）里 8 因子全落「无数据」，②②.5③
# 三级文本命中全为 0。原因不是 A 股不披露：那一轮模型给的标签形如
# 「覆铜板下游需求规模(服务器/通信/消费电子PCB用量)」，而 _core_label
# 只剥 _SUFFIXES 后缀、对这类标签原样返回，于是匹配器拿整串去 89055 字
# 正文里找。对照 ow4 那轮标签短（名义产能/ASP/良率），③ 命中 1/8。
#
# 下面的期望值全部由 spec 第四节的代码实测得出，不是推演。


def test_候选词按特指度降序且去重():
    """整串 → 去括号的头 → 头再剥后缀。调用方命中即停，所以顺序就是优先级。"""
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("先进封装收入占比提升贡献(高端CCL)") == [
        "先进封装收入占比提升贡献(高端CCL)",
        "先进封装收入占比提升贡献",
        "先进封装收入",
    ], "三级阶梯没走通——这是唯一同时用上 L2（去括号）与 L3（剥后缀）的例子"


def test_候选词绝不下探括号内():
    """**这条钉的是 spec 第三节那个决定。**

    括号里是举例/注解：`原材料成本(铜箔/…)` 的括号内是成本的**组成项**，
    拿组成项的命中当整个因子的证据是归因错位。而实测「服务器」在真实
    公告语料（8 篇，89055 字）里出现 **18 次**——下探括号内会让
    「覆铜板下游需求规模」凭 18 次纯巧合拿到它唯一的「证据」。

    将来谁想把提取「改进」成下探括号内，必须先让这条红，而不是悄悄
    多出一个 18 次命中的巧合。
    """
    from ir_agent.gaps import _core_candidates

    cands = _core_candidates("覆铜板下游需求规模(服务器/通信/消费电子PCB用量)")
    assert cands == ["覆铜板下游需求规模(服务器/通信/消费电子PCB用量)",
                     "覆铜板下游需求规模"]
    for inner in ("服务器", "通信", "消费电子PCB用量"):
        assert inner not in cands, f"括号内的词「{inner}」被当成候选了"

    cands2 = _core_candidates("原材料成本(铜箔/电子级玻纤布/环氧树脂价格)")
    assert cands2 == ["原材料成本(铜箔/电子级玻纤布/环氧树脂价格)", "原材料成本"]
    assert "铜箔" not in cands2, \
        "「铜箔」实测命中 6 次、是真信号——但它是成本的组成项、不是成本本身"


def test_本轮实跑的其余五个真实标签():
    """另两个（覆铜板下游需求规模、原材料成本）在
    `test_候选词绝不下探括号内` 里断言——那条的重点是括号内不入候选，
    顺带把它们的完整期望列表也钉住了，这里不重复。七个标签合起来全覆盖。

    fixture 取自真实运行的产物，不是手写的理想形状。

    **这一点是本任务存在的理由本身**：原先所有测试都用「产能」「稼动率」
    这类我自己写的短标签，而生产里模型吐出的是下面这些。测试用的输入
    形状与生产中实际出现的形状不是同一个分布，于是三级归零不报错、
    不抛异常、套件全绿。
    """
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("公司覆铜板市占率") == ["公司覆铜板市占率"]
    assert _core_candidates("覆铜板平均单价ASP") == ["覆铜板平均单价ASP"]
    assert _core_candidates("产能利用率(稼动率同比变化)") == [
        "产能利用率(稼动率同比变化)", "产能利用率"]
    assert _core_candidates("产品结构升级(高端CCL如M6/M7/AI服务器用板占比提升)") == [
        "产品结构升级(高端CCL如M6/M7/AI服务器用板占比提升)", "产品结构升级"]
    assert _core_candidates("汇兑损益(出口业务人民币兑美元波动)") == [
        "汇兑损益(出口业务人民币兑美元波动)", "汇兑损益"]


def test_仓库规范因子名多试一次整串后退回现状():
    """无括号的标签：比现状多试一次整串，再落到 `_core_label` 那一级。

    整串若真的逐字出现，那是更强的命中；不出现就退到现状那一级，
    净结果与改动前一致。
    """
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("有效产能变化率") == ["有效产能变化率", "有效产能"]
    assert _core_candidates("稼动率贡献") == ["稼动率贡献", "稼动率"]
    assert _core_candidates("先进封装收入占比提升贡献") == [
        "先进封装收入占比提升贡献", "先进封装收入"]
    assert _core_candidates("原材料及折旧成本变化贡献") == [
        "原材料及折旧成本变化贡献", "原材料及折旧成本"]
    assert _core_candidates("汇率贡献") == ["汇率贡献", "汇率"]


def test_候选词的退化情形都不崩且方向保守():
    """退化时宁可落空退 ④，不可拿一两个字去冒充命中。"""
    from ir_agent.gaps import _core_candidates

    # 无括号无后缀：与现状完全一致
    assert _core_candidates("产能") == ["产能"]
    # 剥完只剩 1 字则不剥——沿用 `_core_label` 既有行为
    assert _core_candidates("率贡献") == ["率贡献"]
    # 括号不闭合：正则不匹配，头等于整串，不崩
    assert _core_candidates("产能(稼动率") == ["产能(稼动率"]
    # 括号占满整个标签：去括号后为空串，被 2 字下限挡掉
    assert _core_candidates("(稼动率)") == ["(稼动率)"]
    # 全角括号同样处理
    assert _core_candidates("（全角括号）产能") == ["（全角括号）产能", "产能"]
    # 2 字恰好在下限上，保留
    assert _core_candidates("AB") == ["AB"]
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "候选词 or 七个真实标签 or 仓库规范因子名"`

Expected: FAIL，`ImportError: cannot import name '_core_candidates' from 'ir_agent.gaps'`

- [ ] **Step 3: 加 `import re`**

`ir_agent/gaps.py` 当前的 import 区（第 30-41 行）**没有 `re`**。在
`from __future__ import annotations` 之后、`from dataclasses import ...`
之前加一行，与该文件现有的 import 分组习惯一致（标准库一组、项目内一组）：

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
```

- [ ] **Step 4: 加 `_PAREN` 与 `_core_candidates`**

加在 `_core_label` 函数**之后**、`# final-fix-D I-b：命中处如果落在否定语境里`
那段注释**之前**：

```python
# B3：标签里的括号是**举例或注解**，不是因子的名字。实跑（out/600183-subB）
# 里模型给的标签形如「覆铜板下游需求规模(服务器/通信/消费电子PCB用量)」，
# `_core_label` 只剥 `_SUFFIXES` 后缀、对这类标签原样返回，于是三个匹配器
# 拿整串去 89055 字正文里找，②②.5③ 三级同时归零 —— 而同一批正文里
# 「产能」出现 31 次、「覆铜板」98 次：**材料在，匹配器伸不到。**
#
# 「[^）)]*」而不是「.*?」：括号不闭合（`产能(稼动率`）时整体不匹配，
# 头退化成整串、落空退 ④ —— 退化方向保守。
_PAREN = re.compile(r"[（(][^）)]*[）)]")


def _core_candidates(label: str) -> list[str]:
    """按特指度降序的候选词，调用方命中即停。**绝不下探括号内。**

    L1 整串标签 → L2 去掉括号及其内容 → L3 头再剥 `_SUFFIXES` 后缀。

    **为什么不下探括号内**：括号里写的是举例或注解——
    `原材料成本(铜箔/电子级玻纤布/环氧树脂价格)` 的括号内枚举的是成本的
    **组成项**，`覆铜板下游需求规模(服务器/通信/消费电子PCB用量)` 的括号内
    是下游**行业名**。拿一个组成项或行业名的命中当成整个因子的证据，是
    归因错位。而实测「服务器」在真实公告语料里出现 **18 次**、「通信」4 次
    —— 下探括号内会让这个因子凭纯巧合拿到它唯一的「证据」，正是本模块
    注释里写的「把一个巧合伪装成证据」。见 `test_候选词绝不下探括号内`。

    **为什么判据不是长度**：实测「铜箔」2 字是真信号（命中 6 次，覆铜板的
    原材料）、「通信」2 字是巧合——长度分不开具体与泛化。`_core_label` 的
    文档字符串里已经记着一条同形教训：上一轮把阈值从 2 提到 4 想挡否定语境
    误判，防不住，因为失败模式跟长度无关。这次失败模式是泛化程度。所以这里
    不加新的过滤条件，只按特指度排序、让调用方命中即停。

    2 字下限沿用 `_core_label` 的理由：一两个字的片段几乎到处都能命中。
    """
    out: list[str] = []

    def add(c: str) -> None:
        if len(c) >= 2 and c not in out:
            out.append(c)

    add(label)                              # L1 整串标签
    head = _PAREN.sub("", label).strip()    # L2 去掉括号及其内容
    add(head)
    add(_core_label(head))                  # L3 头再剥后缀
    return out
```

- [ ] **Step 5: 跑，确认通过**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "候选词 or 七个真实标签 or 仓库规范因子名"`

Expected: PASS，5 passed

- [ ] **Step 6: 确认 `_core_label` 的既有 7 条断言未被动**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "剥后缀 or 否定语境 or 误判"`

Expected: PASS。这几条守的是 `_core_label` 与 `_valid_hit`，本任务一行都没碰
它们——若有红，说明动错了文件。

- [ ] **Step 7: 全套**

Run: `python -m pytest tests -q -m "not live" -W error`

Expected: PASS，`1949 passed, 19 deselected`（基线 1944 + 本任务 5 条新测试），
无警告。

- [ ] **Step 8: 提交**

```bash
git add ir_agent/gaps.py tests/test_gaps.py
git commit -m "gaps: 新增 _core_candidates，按特指度降序出候选词且绝不下探括号内"
```

---

## Task 2: 三个匹配器改用候选词阶梯

**Files:**
- Modify: `ir_agent/gaps.py`
  - `_match_annual`（现第 196-213 行，`core = _core_label(label)` 在第 208 行）
  - `_match_report`（现第 216-250 行，`core = _core_label(label)` 在第 242 行）
  - `_match_announce`（现第 252-271 行，`core = _core_label(label)` 在第 263 行）
- Test: `tests/test_gaps.py`（追加到文件末尾）

**Interfaces:**
- Consumes: Task 1 的 `_core_candidates(label: str) -> list[str]`；
  既有的 `_valid_hit(text: str, core: str) -> str | None`（不改）
- Produces: 三个匹配器的签名与返回类型**一律不变**——
  `_match_annual(sections, label) -> tuple[str, str] | None`、
  `_match_report(corpus, label) -> tuple[str, Document] | None`、
  `_match_announce(corpus, label) -> tuple[str, Document] | None`。
  只有内部循环结构变了，`collect` 一行不用改。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_gaps.py` 末尾：

```python
def test_候选词在外层容器在内层():
    """**这条钉住循环嵌套的方向，把它写反会红。**

    构造：两篇 SELLSIDE 文档，**靠前**那篇只含最不具体的候选词
    （L3「有效产能」），**靠后**那篇含更具体的 L2「有效产能变化率」。

    · 候选词在外层（正确）：先拿 L2 扫完所有文档 → 命中靠后那篇 → d2
    · 容器在外层（写反）：先扫靠前那篇 → L1 不中、L2 不中、L3 中 → d1

    若容器在外层，一个较泛的候选词在靠前的章节/文档里命中，会压过一个
    更具体的候选词在靠后的容器里的命中——那等于悄悄放弃了「按特指度
    排序」这整个决定，而套件会全绿。
    """
    import ir_agent.gaps as G

    cp = Corpus(as_of=AS_OF)
    cp.add(_doc("公司有效产能保持稳定，不再披露细分口径。",
                tier=Tier.SELLSIDE, doc_id="d1"))
    cp.add(_doc("我们测算其有效产能变化率为 12%。",
                tier=Tier.SELLSIDE, doc_id="d2"))

    hit = G._match_report(cp, "有效产能变化率(含新建产线)")
    assert hit is not None, "两篇都没命中——候选词阶梯没接上"
    _, doc = hit
    assert doc.doc_id == "d2", (
        f"命中了 {doc.doc_id}，应为 d2——容器在外层了：靠前那篇只含最不"
        "具体的候选词「有效产能」，却压过了靠后那篇更具体的「有效产能变化率」")


def test_带括号注解的标签能命中年报章节(monkeypatch):
    """改动前：`_core_label` 对带括号的标签原样返回，整串不会逐字出现在
    年报里，② 必然落空。改动后：去括号的头「原材料成本」能命中。"""
    import ir_agent.gaps as G

    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期原材料成本上升，其中铜箔采购均价同比上涨 18%。"))
    hit = G._match_annual(sections, "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)")
    assert hit is not None, "去括号的头没能命中——候选词阶梯没接进 _match_annual"
    excerpt, where = hit
    assert "原材料成本" in excerpt and where == "《经营情况讨论与分析》"


def test_带括号注解的标签能命中公告():
    """②.5 走同一条阶梯。实跑里 8 篇公告、89055 字，「原材料成本」命中 2 次。"""
    import ir_agent.gaps as G

    cp = Corpus(as_of=AS_OF)
    cp.add(_doc("本期原材料成本同比上升 7.2 个百分点。", title="年度报告",
                tier=Tier.COMPANY, doc_id="a1"))
    hit = G._match_announce(cp, "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)")
    assert hit is not None, "②.5 没接上候选词阶梯"
    _, doc = hit
    assert doc.tier is Tier.COMPANY


def test_括号内的词不会让因子误命中():
    """**反向**：语料里只有括号内那个泛词时，不得命中。

    实测「服务器」在真实公告语料里出现 18 次。没有这条断言，一个下探
    括号内的实现会让「覆铜板下游需求规模」凭纯巧合拿到唯一的「证据」，
    而正向测试全部照绿。
    """
    import ir_agent.gaps as G

    cp = Corpus(as_of=AS_OF)
    cp.add(_doc("AI 服务器需求旺盛，通信板块回暖。",
                tier=Tier.SELLSIDE, doc_id="s1"))
    assert G._match_report(
        cp, "覆铜板下游需求规模(服务器/通信/消费电子PCB用量)") is None, \
        "凭括号内的泛词「服务器」命中了——这正是「把巧合伪装成证据」"

    sections = _sections(("mgmt", "经营情况讨论与分析", "AI 服务器出货增长。"))
    assert G._match_annual(
        sections, "覆铜板下游需求规模(服务器/通信/消费电子PCB用量)") is None


def test_否定语境对每个候选词都生效():
    """`_valid_hit` 一行不动，所以否定判断逐候选词成立——不是只对第一个。

    这里 L1 整串不出现，L2「原材料成本」唯一的出现落在「未披露」窗口里，
    必须判不命中、退到下一级或落空，而不是把否定句当证据摘出来。
    """
    import ir_agent.gaps as G

    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期未披露原材料成本的具体构成。"))
    assert G._match_annual(
        sections, "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)") is None, \
        "否定语境里的命中被当成了证据"
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "外层 or 带括号注解 or 括号内的词 or 否定语境对每个"`

Expected: FAIL。`test_候选词在外层容器在内层` 与两条 `带括号注解` 失败
（改动前 `_core_label` 原样返回整串、三级落空）；
`test_括号内的词不会让因子误命中` 与 `test_否定语境对每个候选词都生效`
此时**已经通过**（改动前更保守），它们是回归保护，Step 5 之后必须仍然绿。

- [ ] **Step 3: 改 `_match_annual`**

把 `ir_agent/gaps.py` 的 `_match_annual` 里这三行：

```python
    core = _core_label(label)
    for s in sections.sections.values():
        hit = _valid_hit(s.text, core)
        if hit is not None:
            return hit, f"《{s.label}》"
```

改成：

```python
    # **候选词在外层，章节在内层。** 顺序写反的话，一个较泛的候选词在
    # 靠前的章节里命中会压过更具体的候选词在靠后章节里的命中——等于
    # 放弃了「按特指度排序」这整个决定，且套件会全绿。
    # 见 `test_候选词在外层容器在内层`。
    for core in _core_candidates(label):
        for s in sections.sections.values():
            hit = _valid_hit(s.text, core)
            if hit is not None:
                return hit, f"《{s.label}》"
```

同时把该函数文档字符串里这句：

```
    **匹配用剥掉后缀的核心词，不是原始标签。** 原始标签「有效产能变化
    率」这种带单位/运算后缀的整串不会逐字出现在年报里，见 `_core_label`。
```

改成：

```
    **匹配用按特指度降序的候选词，命中即停，不是单一核心词。** 原始标签
    「有效产能变化率」这种带单位/运算后缀的整串不会逐字出现在年报里；
    「原材料成本(铜箔/…)」这种带括号注解的更不会——见 `_core_candidates`。
```

- [ ] **Step 4: 改 `_match_report` 与 `_match_announce`**

`_match_report` 里这五行：

```python
    core = _core_label(label)
    for d in corpus.docs:
        if d.tier is Tier.MEDIA:
            continue
        hit = _valid_hit(d.text, core)
        if hit is not None:
            return hit, d
```

改成（**`Tier.MEDIA` 的跳过逻辑一字不动，只是挪进内层**）：

```python
    # 候选词在外层，文档在内层——理由同 `_match_annual`，见
    # `test_候选词在外层容器在内层`。
    for core in _core_candidates(label):
        for d in corpus.docs:
            if d.tier is Tier.MEDIA:
                continue
            hit = _valid_hit(d.text, core)
            if hit is not None:
                return hit, d
```

`_match_announce` 里这五行：

```python
    core = _core_label(label)
    for d in corpus.docs:
        if d.tier is not Tier.COMPANY:
            continue
        hit = _valid_hit(d.text, core)
        if hit is not None:
            return hit, d
```

改成（**`Tier.COMPANY` 的过滤一字不动**）：

```python
    # 候选词在外层，文档在内层——理由同 `_match_annual`，见
    # `test_候选词在外层容器在内层`。
    for core in _core_candidates(label):
        for d in corpus.docs:
            if d.tier is not Tier.COMPANY:
                continue
            hit = _valid_hit(d.text, core)
            if hit is not None:
                return hit, d
```

- [ ] **Step 5: 跑，确认通过**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "外层 or 带括号注解 or 括号内的词 or 否定语境对每个"`

Expected: PASS，5 passed

- [ ] **Step 6: 用突变验证顺序断言真的会红**

把 `_match_report` 的两层循环对调（容器挪到外层）：

```python
    for d in corpus.docs:
        if d.tier is Tier.MEDIA:
            continue
        for core in _core_candidates(label):
            hit = _valid_hit(d.text, core)
            if hit is not None:
                return hit, d
```

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "外层"`

Expected: **FAIL**，断言消息写着「命中了 d1，应为 d2」。

**看到红之后把循环还原成 Step 4 的样子**，再跑一次确认绿，并用
`git diff ir_agent/gaps.py` 确认没有残留。没见过红的测试是没验证过的测试。

- [ ] **Step 7: 确认三级的既有行为全未回退**

Run: `python -m pytest tests/test_gaps.py tests/test_full_context.py tests/test_assume.py -q -m "not live"`

Expected: PASS。重点看这几条既有测试仍绿——它们守的是 ② 与 ③ 的既有命中
与状态标签：`test_真实因子标签剥后缀才能命中年报`、
`test_真实因子标签剥后缀才能命中研报`、`test_年报优先于研报`、
`test_公告优先于研报`、`test_MEDIA语料不产生研报测算命中`、
`test_公告命中只看COMPANY文档`、`test_公告的否定语境不算命中`。

- [ ] **Step 8: 全套**

Run: `python -m pytest tests -q -m "not live" -W error`

Expected: PASS，`1954 passed, 19 deselected`（Task 1 之后的 1949 + 本任务
5 条新测试），无警告。

- [ ] **Step 9: 提交**

```bash
git add ir_agent/gaps.py tests/test_gaps.py
git commit -m "gaps: 三级文本匹配改走候选词阶梯，候选在外层容器在内层"
```

---

## 实跑之后要如实记录的事（不设通过线）

本计划**不包含**端到端实跑——那要花约 $12，由用户决定何时跑。真跑的时候，
按 spec 第三节那张表对照，如实记录：

1. 有几个因子的状态变成了「年报披露」/「公告披露」/「研报测算」。
   spec 的预测是：对**公告语料**从 0/8 到 1/8（只有 `原材料成本` 会命中）。
2. ② 的收益**无从预测**。`annual_sections` 读整本 235 页年报、不截断，所以
   语料面比公告大得多，② 的收益应当更高——但本轮产物不持久化 `sections`，
   spec 里已明确写了「不要把『② 应当更好』当成已经验证过的事」。
3. 「待人工确认」的条目变多是**预期代价**（spec 第五节 ①），不是缺陷。
4. 若因此反复撞上 `assume.py:952` 的闸门（因子拿到非 `MISSING` 状态、而摘录
   里没有数值、模型唯一诚实的答案「无数据（估计）」被驳回），**该动的是闸门
   或那条出口，不是回头把提取改窄**（spec 第五节 ②）。

**绝不为了让数字好看而回头下探括号内、或放宽 2 字下限。**
