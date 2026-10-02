# 因子「检索词」字段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `Factor` 加一个由模型决定的 `search_term`，接进候选词阶梯的最底端，
让不带括号、没有结构信号可切的冗长因子标签（`服务器及通信用高端覆铜板出货量同比增速`）
也能命中 ②年报章节 / ②.5 公司公告 / ③研报语料库。

**Architecture:** `Factor` 加第五个字段，闸门只查长度（≥2 字）写在
`__post_init__` 里以覆盖全部三条构造路径；DSL 第 4 段带它往返；
`_core_candidates` 把它放在 L4 最底端；三个匹配器报出命中的候选词，
`collect` 据此在 `note` 里标出「靠模型给的检索词命中」。

**Tech Stack:** Python 3，标准库。无新依赖。

## Global Constraints

- **闸门只有一条：非空时 ≥2 字。** 不校验是否为 `label` 的子串、不校验是否泛化、
  不校验在语料里命中几次。**检索词由模型决定**——这是用户裁定，要重开先读
  spec 第二节那张实测命中表。
- **≥2 字这条不能去掉。** 1 字检索词在任何年报里都必然命中，那不是「模型决定」
  而是「定然命中」，会让「年报披露」这个状态标签彻底失去信息量。
- **`_core_candidates` 绝不下探括号内，这条不变。** 代码不猜、模型自由——
  这处不对称是刻意的，两面都要有断言守，**不得「修平」**。
- **检索词在阶梯最底端（L4），不是顶端。** 它比整串标签更泛；放顶端会颠倒
  「按特指度排序」这条原则。它存在的理由就是前三级全部落空。
- **「是否靠 L4 命中」按字符串比较，不按位置。** 候选列表会去重：若标签本身
  就是 `产能`、检索词也给 `产能`，它不会作为第四项出现，数位置会说错话。
- **清单标注的断言必须落在 `gaps.summary()` 的渲染输出上，不是 `fd.note` 字段上。**
  子项目 B 终审用一个阻断项换来这条教训（要求是「人看得见」而断言落在数据
  字段上，渲染器把那个字段整行丢掉，两条字段级测试都通过）。
- **四处往返缺一处，检索词就会被静默丢掉**：`parse`、`_to_dsl`、`to_markdown`、
  `_decomp_to_dict`/`_decomp_from_dict`。
- **`_core_label`、`_valid_hit`、`_SUFFIXES`、`_PAREN` 一行不动。**
- 不引入任何新的第三方依赖。
- 测试函数名用中文。
- 每个任务结束跑 `python -m pytest tests -q -m "not live" -W error`，必须全绿
  无警告。基线：`b3-core-label` 分支 **1954 passed, 19 deselected**。

---

## File Structure

| 文件 | 职责 | 改动 |
|---|---|---|
| `ir_agent/drivers.py` | 分解式数据模型与校验 | `Factor` 加 `search_term` + `__post_init__` 闸门 |
| `ir_agent/methodology.py` | 预研、DSL 解析与往返 | `parse` 读第 4 段并转译错误；`_to_dsl` 写第 4 段；`to_markdown` 加列；提示词两处 |
| `ir_agent/assume.py` | 判断项与持久化 | `_decomp_to_dict`/`_decomp_from_dict` 带上字段 |
| `ir_agent/full.py` | 流程编排 | `from_json` 调用点兜 `DecompositionInvalid` → `SystemExit` |
| `ir_agent/gaps.py` | 缺口识别与分级采集 | `_core_candidates` 收 `search_term`；三个匹配器加参数、返回三元组；`collect` 接线与标注 |
| `tests/test_drivers.py`、`tests/test_methodology.py`、`tests/test_assume.py`、`tests/test_gaps.py` | 测试 | 新增 |

不新建文件。

---

## Task 1：`Factor.search_term` 与三条构造路径的错误转译

**Files:**
- Modify: `ir_agent/drivers.py`（`Factor`，现第 43-49 行）
- Modify: `ir_agent/methodology.py`（`parse` 里 `Factor(...)` 的构造，现第 79 行附近）
- Modify: `ir_agent/full.py`（`from_json` 调用点，现第 1741 行）
- Test: `tests/test_drivers.py`、`tests/test_methodology.py`、`tests/test_assume.py`

**Interfaces:**
- Consumes: `ir_agent.drivers.DecompositionInvalid`（既有异常，`drivers.py:39`）
- Produces: `Factor(key, label, unit, source_hint="", search_term="")`，
  非空 `search_term` 长度 <2 时构造即抛 `DecompositionInvalid`。
  Task 2 与 Task 3 都依赖这个字段存在。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_drivers.py` 末尾：

```python
# ── 检索词：由模型决定，代码只查长度 ──────────────────────────
#
# B3 第一版靠括号这个结构信号切分因子标签。实测它对**不带括号但同样
# 冗长**的标签无能为力：「服务器及通信用高端覆铜板出货量同比增速」剥掉
# 运算后缀仍 17 字，不会逐字出现在任何年报里。唯一知道该用什么词检索的
# 是生成这个标签的那个模型，所以给它一条显式通道。


def test_检索词不必是标签的子串():
    """**这条钉住「放开闸门」这个决定。**

    设计过程中先定的是「必须是 label 的子串」，用户裁定改为放开：在因子
    检索这一环尽量不设闸门，由模型决定。「客户集中度」在年报里真实存在、
    比标签里任何一段都准，而它不是标签的子串。

    这条红了意味着有人把子串闸门加回来了——那是一个要重新讨论的决定，
    不是一次「加固」。要重开请先读 spec 第二节那张实测命中表。
    """
    from ir_agent.drivers import Factor

    f = Factor(key="k", label="前五大客户销售收入占营业收入比重变化率",
               unit="变化率", search_term="客户集中度")
    assert f.search_term == "客户集中度"


def test_检索词的长度下限仍然生效():
    """1 字检索词在任何年报里都**必然**命中，那不是「模型决定」而是
    「定然命中」，会把所有因子都抬成「年报披露」、让这个状态标签彻底
    失去信息量。放开闸门是为了让模型的判断生效，不是让判断失效。
    """
    import pytest

    from ir_agent.drivers import DecompositionInvalid, Factor

    with pytest.raises(DecompositionInvalid):
        Factor(key="k", label="产能利用率", unit="变化率", search_term="率")
    # 空检索词通过——旧的 3 段 DSL 与旧的 JSON 都靠这条向后兼容
    assert Factor(key="k", label="产能利用率", unit="变化率").search_term == ""


def test_代码不下探括号内而模型可以给任何词():
    """**这处不对称是刻意的，两面同时钉住。** 分开写，将来只会有一半
    被读到。

    · 代码侧：B3 第一版的决定，不得倒退——实测代码分不开「铜箔」
      （真信号，命中 6 次）与「通信」（巧合，命中 4 次），长度分不开。
    · 模型侧：完全自由，连不在标签里的词也可以给。

    谁要「修平」这处不对称，请先读 spec 第二节。往任一方向修都会破坏
    一个已做出的决定。
    """
    from ir_agent.drivers import Factor
    from ir_agent.gaps import _core_candidates

    assert "铜箔" not in _core_candidates(
        "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)")
    assert Factor(key="k", label="原材料成本(铜箔/电子级玻纤布/环氧树脂价格)",
                  unit="pp", search_term="铜箔").search_term == "铜箔"
```

追加到 `tests/test_methodology.py` 末尾：

```python
def test_解析遇到过短检索词报方法论错误而非分解式错误():
    """`Factor.__post_init__` 抛的是 `DecompositionInvalid`，而 `parse`
    里 `Factor(...)` 的构造**不在任何 try 内**——不转译的话它会绕过
    `methodology.py` 的重试循环（那里只 `except MethodologyFailed`），
    模型拿不到驳回原因、也没有重试机会。

    消息里要带因子名与那个检索词，否则模型不知道该改哪一个。
    """
    import pytest

    from ir_agent.methodology import MethodologyFailed, parse

    text = ("分解: 营收 | 乘 | 产能:变化率:年报:率 | 稼动率:变化率:年报 | "
            "单价:变化率:年报 | 结构:变化率:年报\n"
            "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    with pytest.raises(MethodologyFailed) as e:
        parse(text)
    assert "产能" in str(e.value) and "率" in str(e.value)
```

追加到 `tests/test_assume.py` 末尾：

```python
def test_读回的分解式里过短检索词不会让第二阶段崩成traceback():
    """`from_json` 里 `_decomp_from_dict` 直接构造 `Factor`，而
    `full.py` 的调用点（第 1741 行）**完全没兜**——`run_valuation` 对
    `AssumeFailed` 的捕获在第 1809 行，**在 from_json 之后**，所以那里
    抛任何异常都会以 traceback 的形式崩掉第二阶段命令。

    人手改过的 assumptions.json 应该得到一句能看懂的话，不是 traceback。
    措辞照 `assume.py` 既有那条「请重跑第一阶段」。
    """
    import pytest

    from ir_agent.drivers import DecompositionInvalid
    from ir_agent import assume

    bad = {"code": "600183", "labels": [], "rows": [],
           "decomps": [{"target": "revenue", "op": "乘", "factors": [
               {"key": "capacity", "label": "产能利用率", "unit": "变化率",
                "source_hint": "年报", "search_term": "率"}]}]}
    with pytest.raises(DecompositionInvalid):
        assume.from_json(bad, code="600183")
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_drivers.py tests/test_methodology.py tests/test_assume.py -q -m "not live" -k "检索词 or 不下探括号内 or 过短检索词"`

Expected: FAIL，`TypeError: Factor.__init__() got an unexpected keyword argument 'search_term'`

- [ ] **Step 3: 加字段与闸门**

`ir_agent/drivers.py` 第 43-49 行，`Factor` 改成：

```python
@dataclass(frozen=True)
class Factor:
    key: str            # capacity | utilization | asp
    label: str          # 产能 / 稼动率 / 单价
    unit: str           # 必须是 UNITS 里的值
    source_hint: str = ""   # 这个数去哪找
    # 用什么词去找。**由模型决定，代码只查长度。**
    #
    # 因子标签常常是「服务器及通信用高端覆铜板出货量同比增速」这种形状
    # ——不会逐字出现在任何年报里，而它没有括号这类结构信号可切（带括号
    # 的那类由 `gaps._core_candidates` 处理）。唯一知道该用什么词检索的
    # 是生成这个标签的模型，所以给它一条显式通道，接在候选词阶梯的最
    # 底端（L4，见 `gaps._core_candidates`）。
    #
    # **不校验它是否为 label 的子串**：设计时先定的是子串约束，用户裁定
    # 放开——「客户集中度」比「前五大客户销售收入占营业收入比重变化率」
    # 里任何一段都准，而它不是子串。代价写在 spec 第七节①：这条通道
    # 因此没有代码保护，泛词（实测「覆铜板」98 次、「产能」31 次、
    # 「服务器」18 次）会一路走到底。对价是提示词里把后果说清，以及
    # `gaps.collect` 在 note 里标出「靠模型给的检索词命中」。
    search_term: str = ""

    def __post_init__(self) -> None:
        # **只查长度，这是唯一的闸门。** 1 字检索词（「率」「价」）在任何
        # 年报里都必然命中，那不是「模型决定」而是「定然命中」，会把所有
        # 因子都抬成「年报披露」、让这个状态标签彻底失去信息量。
        # frozen=True 只禁赋值，不禁在这里抛异常。
        if self.search_term and len(self.search_term) < 2:
            raise DecompositionInvalid(
                f"因子「{self.label}」的检索词 {self.search_term!r} 只有一个字"
                "——一个字的词在任何年报里都必然命中，那不是依据而是巧合。"
                "请给一个至少两个字、且指向这个因子本身的词。")
```

- [ ] **Step 4: 转译 `methodology.parse` 的错误**

`ir_agent/methodology.py` 第 79 行附近，把

```python
            factors.append(Factor(key=_slug(label), label=label, unit=unit,
                                  source_hint=hint))
```

改成（**第 4 段的读取在 Task 2，这一步只加转译**）：

```python
            # **`Factor.__post_init__` 抛的是 `DecompositionInvalid`，必须
            # 在这里转译成 `MethodologyFailed`。** 下面那个 try 只包着
            # `check_closure(d)`，不包构造本身；不转译的话异常会绕过本模块
            # 的重试循环（`parse` 的调用方只 `except MethodologyFailed`），
            # 模型既拿不到驳回原因、也没有重试机会。
            try:
                factors.append(Factor(key=_slug(label), label=label,
                                      unit=unit, source_hint=hint))
            except DecompositionInvalid as e:
                raise MethodologyFailed(str(e)) from e
```

并确认 `methodology.py` 顶部已 import `DecompositionInvalid`（它已 import
`check_closure`、`DecompositionInvalid` 同在 `drivers.py`；若未 import 则加
进既有的那一行 import）。

- [ ] **Step 5: 兜住 `from_json` 的调用点**

`ir_agent/full.py` 第 1741 行，把

```python
    a, edits = assume.from_json(saved, code=code)
```

改成：

```python
    # **`from_json` 里 `_decomp_from_dict` 直接构造 `Factor`，构造即校验。**
    # 这里不兜的话，一份人手改坏的 assumptions.json 会以 traceback 的形式
    # 崩掉整条第二阶段命令——`run_valuation` 对 `AssumeFailed` 的捕获在
    # 下面（约 1809 行），在这一行之后，兜不到这里。措辞与上下两处
    # `SystemExit` 同源：这份文件不可用，请做什么。
    try:
        a, edits = assume.from_json(saved, code=code)
    except DecompositionInvalid as e:
        raise SystemExit(
            f"{js} 里的分解式已不合法：{e}　——请重跑第一阶段，"
            "重新预研分解式后再填判断项。")
```

并在 `full.py` 顶部 import 区加 `from ir_agent.drivers import DecompositionInvalid`
（若该行已存在则不重复）。

- [ ] **Step 6: 跑，确认通过**

Run: `python -m pytest tests/test_drivers.py tests/test_methodology.py tests/test_assume.py -q -m "not live" -k "检索词 or 不下探括号内 or 过短检索词"`

Expected: PASS，4 passed

- [ ] **Step 7: 突变验证两处转译真的在起作用**

① 把 Step 4 的 `try/except` 去掉（只留 `factors.append(Factor(...))`），跑：

Run: `python -m pytest tests/test_methodology.py -q -m "not live" -k "过短检索词"`

Expected: **FAIL**——报的是 `DecompositionInvalid` 而不是 `MethodologyFailed`，
证明不转译就会绕过重试循环。**看到红之后还原。**

② 把 Step 5 的 `try/except` 去掉，跑：

Run: `python -m pytest tests/test_assume.py -q -m "not live" -k "过短检索词"`

Expected: 这条测试断言的是 `DecompositionInvalid`，去掉 `full.py` 的兜不会让它红
（它测的是 `assume.from_json` 本身）。**这是预期的**——`full.py` 那一层的行为
没有单元测试覆盖（它要起一条真实命令），所以 Step 5 靠的是代码审查而不是测试。
在报告里**明确写出这一点**，不要假称它被测到了。

还原两处之后再跑一次确认绿，并用 `git diff` 确认没有残留。

- [ ] **Step 8: 全套**

Run: `python -m pytest tests -q -m "not live" -W error`

Expected: PASS，`1959 passed, 19 deselected`（基线 1954 + 5），无警告。

- [ ] **Step 9: 提交**

```bash
git add ir_agent/drivers.py ir_agent/methodology.py ir_agent/full.py \
        tests/test_drivers.py tests/test_methodology.py tests/test_assume.py
git commit -m "drivers: Factor 加 search_term，闸门只查长度，三条构造路径各自转译错误"
```

---

## Task 2：DSL 第 4 段与四处往返

**Files:**
- Modify: `ir_agent/methodology.py`（`parse` 第 76 行附近读第 4 段；`_to_dsl`
  第 259 行写第 4 段；`to_markdown` 的表加一列；提示词第 181 行附近与
  文件末尾往返说明第 289 行附近）
- Modify: `ir_agent/assume.py`（`_decomp_to_dict` 第 147 行、`_decomp_from_dict`
  第 155 行）
- Test: `tests/test_methodology.py`、`tests/test_assume.py`

**Interfaces:**
- Consumes: Task 1 的 `Factor(..., search_term="")`
- Produces: DSL 格式 `名称:单位:线索:检索词`；`_to_dsl` 写出 4 段；
  `to_markdown` 的表有「检索词」列；`_decomp_to_dict` 的 factor dict 含
  `"search_term"` 键。Task 3 依赖 `f.search_term` 在 `collect` 里拿得到。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_methodology.py` 末尾：

```python
def test_DSL第四段读成检索词且旧的三段仍然可用():
    """第 4 段**今天是被静默丢弃的**（`hint = bits[2] if len(bits) > 2`），
    所以旧的 3 段文件自然得到空检索词、退回 `_core_candidates` 的前三级
    ——向后兼容是免费的，但要有断言守住它。
    """
    from ir_agent.methodology import parse

    m = parse("分解: 营收 | 乘 | 出货量同比增速:变化率:年报:出货量 | "
              "稼动率:变化率:年报 | 单价:变化率:年报 | 结构:变化率:年报\n"
              "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    rev = next(d for d in m.decompositions if d.target == "revenue")
    by_label = {f.label: f for f in rev.factors}
    assert by_label["出货量同比增速"].search_term == "出货量"   # 4 段
    assert by_label["稼动率"].search_term == ""                # 3 段，兼容


def test_往返不丢检索词():
    """`_to_dsl` 是 `parse` 的逆操作，`from_markdown` 靠它把落盘文件读回。
    **不改 `_to_dsl`，人工编辑 drivers.md 后重跑会把检索词吃掉**——而那个
    往返是写进文件末尾的承诺（「编辑下面这段文字再重跑即可覆盖」）。
    """
    from ir_agent.methodology import _to_dsl, parse

    src = ("分解: 营收 | 乘 | 出货量同比增速:变化率:年报:出货量 | "
           "稼动率:变化率:年报 | 单价:变化率:年报 | 结构:变化率:年报\n"
           "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    m1 = parse(src)
    m2 = parse(_to_dsl(m1))
    got = {f.label: f.search_term
           for d in m2.decompositions for f in d.factors}
    assert got["出货量同比增速"] == "出货量", "往返丢了检索词"
    assert got["稼动率"] == ""


def test_人读表里出现检索词列():
    """`drivers.md` 是人看的。检索词决定了代码实际去搜什么词，人核实
    分解式时必须看得见它——否则一个泛词（实测「服务器」18 次巧合命中）
    会在没人注意的情况下决定一个因子的「年报披露」状态。
    """
    from ir_agent.methodology import parse, to_markdown

    m = parse("分解: 营收 | 乘 | 出货量同比增速:变化率:年报:出货量 | "
              "稼动率:变化率:年报 | 单价:变化率:年报 | 结构:变化率:年报\n"
              "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    md = to_markdown(m)
    assert "检索词" in md, "人读表没有检索词列"
    assert "出货量" in md
```

追加到 `tests/test_assume.py` 末尾：

```python
def test_判断项落盘往返不丢检索词():
    """`_decomp_to_dict`/`_decomp_from_dict` 两边都要带上，缺一边就在
    第二阶段读回时静默丢掉。读回侧用 `.get` 带默认值，与既有的
    `f.get("source_hint", "")` 同形——旧的 assumptions.json 没有这个键。
    """
    from ir_agent.assume import _decomp_from_dict, _decomp_to_dict
    from ir_agent.drivers import Decomposition, Factor, Op

    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="shipment", label="出货量同比增速", unit="变化率",
               source_hint="年报", search_term="出货量"),
        Factor(key="asp", label="单价", unit="变化率", source_hint="年报")])
    back = _decomp_from_dict(_decomp_to_dict(d))
    got = {f.label: f.search_term for f in back.factors}
    assert got["出货量同比增速"] == "出货量", "往返丢了检索词"
    assert got["单价"] == ""

    # 旧文件没有这个键，不能崩
    raw = _decomp_to_dict(d)
    for f in raw["factors"]:
        f.pop("search_term")
    assert all(f.search_term == "" for f in _decomp_from_dict(raw).factors)
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_methodology.py tests/test_assume.py -q -m "not live" -k "第四段 or 往返不丢检索词 or 人读表 or 落盘往返"`

Expected: FAIL，4 条全失败（`search_term` 读不出来、`_to_dsl` 写不出来、
表里没有「检索词」、dict 里没有该键）

- [ ] **Step 3: `parse` 读第 4 段**

`ir_agent/methodology.py` 第 76 行附近，把

```python
            label, unit = bits[0], bits[1]
            hint = bits[2] if len(bits) > 2 else ""
```

改成：

```python
            label, unit = bits[0], bits[1]
            hint = bits[2] if len(bits) > 2 else ""
            # 第 4 段 = 检索词。**此前它被静默丢弃**，所以旧的 3 段文件
            # 自然得到空串、退回 `gaps._core_candidates` 的前三级——向后
            # 兼容是免费的。
            #
            # **已知的坏情形（明账）**：`raw.split(":")` 对所有字段生效，
            # 若 `source_hint` 里出现冒号（`年报附注:汇兑损益`），一行 3 段
            # 的因子会被切成 4 段，尾巴落进这里，变成一个模型从没打算给的
            # 检索词。闸门只查长度，拦不住它。本任务不修（修法是限定
            # `split(":", 3)` 或禁止 hint 带冒号，是独立的一件事），但有
            # 测试把它钉成已知——见 `test_hint带冒号时第四段会变成假检索词`。
            term = bits[3] if len(bits) > 3 else ""
```

并把 Step 4 的构造改成带上它：

```python
            try:
                factors.append(Factor(key=_slug(label), label=label,
                                      unit=unit, source_hint=hint,
                                      search_term=term))
            except DecompositionInvalid as e:
                raise MethodologyFailed(str(e)) from e
```

- [ ] **Step 4: `_to_dsl` 写第 4 段**

`ir_agent/methodology.py` 第 259 行，把

```python
        parts += [f"{f.label}:{f.unit}:{f.source_hint}" for f in d.factors]
```

改成：

```python
        # 四段写全。**少写检索词这一段，人工编辑 drivers.md 后重跑就会把它
        # 吃掉**——而「编辑这段文字再重跑即可覆盖」是写在文件末尾的承诺。
        parts += [f"{f.label}:{f.unit}:{f.source_hint}:{f.search_term}"
                  for f in d.factors]
```

- [ ] **Step 5: `to_markdown` 加列**

`ir_agent/methodology.py` 的 `to_markdown` 里，把

```python
        lines.append("| 因子 | 单位 | 数据线索 |")
        lines.append("| --- | --- | --- |")
        for f in d.factors:
            lines.append(f"| {f.label} | {f.unit} | {f.source_hint or '—'} |")
```

改成：

```python
        # 检索词要在人读表里露出来：它决定代码实际去搜什么词，而一个泛词
        # （实测「服务器」在真实公告语料里 18 次巧合命中）会在没人注意的
        # 情况下决定一个因子的「年报披露」状态。
        lines.append("| 因子 | 单位 | 数据线索 | 检索词 |")
        lines.append("| --- | --- | --- | --- |")
        for f in d.factors:
            lines.append(f"| {f.label} | {f.unit} | {f.source_hint or '—'} "
                         f"| {f.search_term or '—'} |")
```

- [ ] **Step 6: `assume` 的两处往返**

`ir_agent/assume.py` 第 147 行，把

```python
            "factors": [{"key": f.key, "label": f.label, "unit": f.unit,
                        "source_hint": f.source_hint} for f in d.factors]}
```

改成：

```python
            "factors": [{"key": f.key, "label": f.label, "unit": f.unit,
                        "source_hint": f.source_hint,
                        "search_term": f.search_term} for f in d.factors]}
```

第 155 行，把

```python
        factors=[Factor(key=f["key"], label=f["label"], unit=f["unit"],
                        source_hint=f.get("source_hint", ""))
                for f in d["factors"]])
```

改成（`.get` 带默认值，与既有 `source_hint` 同形——旧文件没有这个键）：

```python
        factors=[Factor(key=f["key"], label=f["label"], unit=f["unit"],
                        source_hint=f.get("source_hint", ""),
                        search_term=f.get("search_term", ""))
                for f in d["factors"]])
```

- [ ] **Step 7: 把冒号碰撞钉成明账**

追加到 `tests/test_methodology.py` 末尾：

```python
def test_hint带冒号时第四段会变成假检索词():
    """**这是一条把已知坏情形钉成明账的测试，不是一条验证正确行为的测试。**

    `raw.split(":")` 对所有字段生效。`source_hint` 里出现冒号时，一行 3 段
    的因子被切成 4 段，尾巴落进检索词位——而闸门只查长度，拦不住它。

    设计早期版本把子串闸门当作这个风险的解药（hint 的碎片几乎不可能是
    label 的子串）。闸门放开之后那条解药不存在了，所以这里只能记成明账。

    它红了意味着有人修了这个 bug（限定 `split(":", 3)` 或禁止 hint 带冒号）
    ——那是好事，请连这条测试一起改掉。
    """
    from ir_agent.methodology import parse

    m = parse("分解: 营收 | 乘 | 汇兑损益:变化率:年报附注:汇兑损益 | "
              "稼动率:变化率:年报 | 单价:变化率:年报 | 结构:变化率:年报\n"
              "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    f = next(f for d in m.decompositions for f in d.factors
             if f.label == "汇兑损益")
    assert f.source_hint == "年报附注", "hint 被切断（这是已知行为）"
    assert f.search_term == "汇兑损益", \
        "hint 的尾巴成了检索词（这是已知的坏情形，见本测试 docstring）"
```

- [ ] **Step 8: 改提示词两处**

① `ir_agent/methodology.py` 第 181-182 行，把

```
`分解: 营收 | 乘 | 因子名:单位:数据线索 | 因子名:单位:数据线索 | …`
`分解: 毛利率 | 加 | 因子名:单位:数据线索 | …`
```

改成：

```
`分解: 营收 | 乘 | 因子名:单位:数据线索:检索词 | 因子名:单位:数据线索:检索词 | …`
`分解: 毛利率 | 加 | 因子名:单位:数据线索:检索词 | …`
```

② 在同一段「闭合的硬性要求」之后，加一段关于检索词的说明。
**闸门放开之后提示词是唯一约束模型的地方，所以写后果、写实测数字，
不只写规则**：

```
**检索词**

每个因子再给一个检索词：你认为年报/公告里会**逐字出现**、且**指向这个因子
本身**的那个词。

因子名可以写完整（「服务器及通信用高端覆铜板出货量同比增速」），但那样的整串
不会出现在任何年报里——检索词才是代码真正拿去全文搜索的东西。它**不必**是
因子名里的片段：若「客户集中度」比因子名里任何一段都准，就写「客户集中度」。

**但不要给泛词。** 代码会拿这个词在年报与公告全文里搜，命中就会被当成这个
因子的文字依据。给「产能」「覆铜板」「服务器」这类词，在一家覆铜板公司的
文件里必然命中几十次，而那些命中与这个因子无关——那是把一次巧合伪装成证据。
**宁可给一个搜不到的精确词（因子落「无数据」，诚实），也不要给一个到处都能
搜到的泛词。**

实测参考：在生益科技 8 篇公告（8.9 万字）里，「覆铜板」出现 98 次、「产能」
31 次、「服务器」18 次——这三个都是**不该给**的例子；「原材料成本」2 次、
「铜箔」6 次是**可以给**的例子。
```

③ 文件末尾往返说明（第 289 行附近）里的语法示例，把
`` `分解: 目标 | 运算 | 因子名:单位:数据线索 | …` `` 改成
`` `分解: 目标 | 运算 | 因子名:单位:数据线索:检索词 | …` ``。

- [ ] **Step 9: 跑，确认通过**

Run: `python -m pytest tests/test_methodology.py tests/test_assume.py -q -m "not live" -k "第四段 or 往返不丢检索词 or 人读表 or 落盘往返 or hint带冒号"`

Expected: PASS，5 passed

- [ ] **Step 10: 突变验证四处往返各自都在起作用**

逐处去掉再跑，每处必须有测试变红；每次看到红之后还原：

| 去掉 | 跑 | 必须红的测试 |
|---|---|---|
| `parse` 的 `term = bits[3] …`（改回 `term = ""`） | `-k "第四段"` | `test_DSL第四段读成检索词且旧的三段仍然可用` |
| `_to_dsl` 的第 4 段 | `-k "往返不丢检索词"` | `test_往返不丢检索词` |
| `to_markdown` 的列 | `-k "人读表"` | `test_人读表里出现检索词列` |
| `_decomp_to_dict` 的键 | `-k "落盘往返"` | `test_判断项落盘往返不丢检索词` |

四处都验过、都还原之后，`git diff` 确认无残留。**没见过红的往返是没验证过的
往返——这四处里缺任何一处，检索词都会在某条路上被静默丢掉。**

- [ ] **Step 11: 全套**

Run: `python -m pytest tests -q -m "not live" -W error`

Expected: PASS，`1964 passed, 19 deselected`（Task 1 后的 1959 + 5），无警告。

- [ ] **Step 12: 提交**

```bash
git add ir_agent/methodology.py ir_agent/assume.py \
        tests/test_methodology.py tests/test_assume.py
git commit -m "methodology/assume: 检索词进 DSL 第 4 段并带全四处往返，提示词写明后果与实测数字"
```

---

## Task 3：检索词接进候选词阶梯的 L4

**Files:**
- Modify: `ir_agent/gaps.py`（`_core_candidates` 现第 102 行；
  `_match_annual` 现第 242 行；`_match_report` 现第 267 行；
  `_match_announce` 现第 305 行；`collect` 的三处调用现第 353/367/378 行）
- Test: `tests/test_gaps.py`

**Interfaces:**
- Consumes: Task 1 的 `Factor.search_term`
- Produces: `_core_candidates(label: str, search_term: str = "") -> list[str]`
  —— 检索词作为 L4 追加在末尾；
  `_match_annual(sections, label, search_term="")`、
  `_match_report(corpus, label, search_term="")`、
  `_match_announce(corpus, label, search_term="")`。
  **返回值此任务仍是二元组**，三元组在 Task 4。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_gaps.py` 末尾：

```python
def test_检索词是候选阶梯的最后一级():
    """**不是第一级。** 「出货量」比整串标签更泛，不是更具体；放顶端会
    压过更具体的候选，颠倒「按特指度排序」这条原则。检索词存在的理由
    恰恰是前三级全部落空。
    """
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("服务器及通信用高端覆铜板出货量同比增速",
                            "出货量") == [
        "服务器及通信用高端覆铜板出货量同比增速",
        "服务器及通信用高端覆铜板出货量同比",
        "出货量",
    ]
    # 不传检索词时与 B3 第一版完全一致
    assert _core_candidates("服务器及通信用高端覆铜板出货量同比增速") == [
        "服务器及通信用高端覆铜板出货量同比增速",
        "服务器及通信用高端覆铜板出货量同比",
    ]


def test_检索词与结构候选重复时只出现一次():
    """`add` 去重。这条同时是 Task 4 那个「按字符串比较而非位置」决定的
    依据：标签本身就是「产能」、检索词也给「产能」时，检索词**不会**作为
    第四项出现——按位置判断「是否靠 L4 命中」会在这里说错话。
    """
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("产能", "产能") == ["产能"]


def test_前三级落空时检索词兜底命中():
    """这是整个字段存在的理由：不带括号的冗长标签，前三级都不会逐字出现
    在年报里，只有模型给的检索词能命中。"""
    import ir_agent.gaps as G

    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期出货量同比增长 23%，高端产品占比提升。"))
    label = "服务器及通信用高端覆铜板出货量同比增速"
    assert G._match_annual(sections, label) is None, \
        "不给检索词时本该落空——否则这条测试证明不了检索词起了作用"
    hit = G._match_annual(sections, label, search_term="出货量")
    assert hit is not None, "检索词没能兜底"
    excerpt, where = hit
    assert "出货量" in excerpt and where == "《经营情况讨论与分析》"


def test_更具体的结构候选优先于检索词():
    """L2 能命中时就不该用 L4——「按特指度排序」。

    构造：L2（去括号的头「原材料成本」）在**靠后**的章节里命中，
    L4（检索词「成本」）在**靠前**的章节里命中。候选在外层循环，
    所以 L2 先扫完所有章节、命中靠后那篇。
    """
    import ir_agent.gaps as G

    sections = _sections(
        ("a", "第一节", "本期成本控制良好。"),
        ("b", "第二节", "本期原材料成本上升 7%。"))
    hit = G._match_annual(
        sections, "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)",
        search_term="成本")
    assert hit is not None
    _, where = hit
    assert where == "《第二节》", \
        "用了检索词「成本」而不是更具体的「原材料成本」——L4 被提前了"


def test_三个匹配器都收检索词():
    """②、②.5、③ 走同一条阶梯，三处都要接上，漏一处那一级就得不到兜底。"""
    import ir_agent.gaps as G

    label = "服务器及通信用高端覆铜板出货量同比增速"

    cp_sell = Corpus(as_of=AS_OF)
    cp_sell.add(_doc("我们测算其出货量同比增长 23%。",
                     tier=Tier.SELLSIDE, doc_id="s1"))
    assert G._match_report(cp_sell, label) is None
    assert G._match_report(cp_sell, label, search_term="出货量") is not None

    cp_co = Corpus(as_of=AS_OF)
    cp_co.add(_doc("本期出货量同比增长 23%。", title="年度报告",
                   tier=Tier.COMPANY, doc_id="a1"))
    assert G._match_announce(cp_co, label) is None
    assert G._match_announce(cp_co, label, search_term="出货量") is not None
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "检索词 or 更具体的结构候选"`

Expected: FAIL，`TypeError: _core_candidates() takes 1 positional argument but 2 were given`

- [ ] **Step 3: `_core_candidates` 收检索词，追加在末尾**

`ir_agent/gaps.py` 的 `_core_candidates`，签名与末尾各改一处：

```python
def _core_candidates(label: str, search_term: str = "") -> list[str]:
```

docstring 里补一段（接在既有「2 字下限沿用…」那段之后）：

```python
    **L4 = 模型给的检索词，在最底端不是顶端。** 「出货量」比整串标签更泛、
    不是更具体；放顶端会压过更具体的候选，颠倒本函数「按特指度排序」这条
    原则。它存在的理由恰恰是 L1–L3 全部落空（不带括号的冗长标签没有结构
    信号可切）。由模型决定、代码只查长度，见 `drivers.Factor.search_term`。
```

函数体末尾，把

```python
    add(_core_label(head))                  # L3 头再剥后缀
    return out
```

改成：

```python
    add(_core_label(head))                  # L3 头再剥后缀
    add(search_term)                        # L4 模型给的检索词，最后才试
    return out
```

（`add` 已经带 ≥2 字下限与去重，所以空检索词与重复项自动不入列。）

- [ ] **Step 4: 三个匹配器加参数**

三处各改两行。`_match_annual`：

```python
def _match_annual(sections, label: str,
                  search_term: str = "") -> tuple[str, str] | None:
```

```python
    for core in _core_candidates(label, search_term):
```

`_match_report`：

```python
def _match_report(corpus, label: str,
                  search_term: str = "") -> tuple[str, Document] | None:
```

```python
    for core in _core_candidates(label, search_term):
```

`_match_announce`：

```python
def _match_announce(corpus, label: str,
                    search_term: str = "") -> tuple[str, Document] | None:
```

```python
    for core in _core_candidates(label, search_term):
```

- [ ] **Step 5: `collect` 三处传 `f.search_term`**

`ir_agent/gaps.py` 的 `collect` 里，三处调用各加一个实参：

```python
            hit = _match_annual(sections, f.label, f.search_term)
```

```python
            hit = _match_announce(corpus, f.label, f.search_term)
```

```python
            hit = _match_report(corpus, f.label, f.search_term)
```

- [ ] **Step 6: 跑，确认通过**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "检索词 or 更具体的结构候选"`

Expected: PASS，5 passed

- [ ] **Step 7: 突变验证 L4 的位置真的被钉住**

把 Step 3 的 `add(search_term)` 从末尾挪到 `add(label)` **之前**（即 L4 变 L0），跑：

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "检索词是候选阶梯 or 更具体的结构候选"`

Expected: **FAIL**，两条都红——`test_检索词是候选阶梯的最后一级` 的列表顺序
不对，`test_更具体的结构候选优先于检索词` 命中了《第一节》。
**看到红之后还原**，再跑一次确认绿，`git diff` 确认无残留。

- [ ] **Step 8: 全套**

Run: `python -m pytest tests -q -m "not live" -W error`

Expected: PASS，`1969 passed, 19 deselected`（Task 2 后的 1964 + 5），无警告。

- [ ] **Step 9: 提交**

```bash
git add ir_agent/gaps.py tests/test_gaps.py
git commit -m "gaps: 检索词接进候选阶梯的 L4 最底端，三个匹配器与 collect 同步"
```

---

## Task 4：产物清单标出「靠检索词命中」

**Files:**
- Modify: `ir_agent/gaps.py`（三个匹配器的返回值改三元组；新增 `_via_note`；
  `collect` 三处解包与 `note` 拼装）
- Test: `tests/test_gaps.py`

**Interfaces:**
- Consumes: Task 3 的 `_core_candidates(label, search_term)` 与三个匹配器的
  `search_term` 参数
- Produces: `_match_annual(...) -> tuple[str, str, str] | None`（原文片段,
  出处, 命中的候选词）；`_match_report`/`_match_announce` 同理返回
  `tuple[str, Document, str] | None`；`_via_note(core, factor) -> str`

**这个任务是放开闸门的对价，不是可选项。** 闸门去掉之后，让读者看见
「这条证据是靠模型指的路找到的」是唯一剩下的知情手段。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_gaps.py` 末尾：

```python
def test_靠检索词命中要在清单渲染里说出来():
    """**断言落在 `G.summary(data)` 的渲染输出上，不是 `fd.note` 字段上。**

    子项目 B 的终审用一个阻断项换来这条教训：一条要求是「人看得见」，而
    两条测试都断在 `FactorData.note` 字段上，渲染器把那个字段整行丢掉，
    两条测试照样通过。要求是人看得见，断言就必须落在人看到的那个字符串上。
    """
    import ir_agent.gaps as G
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.ledger import FactLedger

    f = Factor(key="shipment", label="服务器及通信用高端覆铜板出货量同比增速",
               unit="变化率", source_hint="年报", search_term="出货量")
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[f])
    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期出货量同比增长 23%。"))
    data = G.collect(_ExplodingClient(), [d], FactLedger(), None,
                     "覆铜板", AS_OF, sections=sections)

    assert data["shipment"].status == G.ANNUAL
    rendered = G.summary(data)
    assert "靠模型给的检索词" in rendered, \
        "靠检索词命中这件事没出现在人看到的清单里"
    assert "出货量" in rendered


def test_靠标签结构命中时不加这句标注():
    """**反向。** 没有它，一个无条件追加这句话的实现也能让正向测试通过，
    而那会让这条标注失去全部信息量——每一行都写着「靠检索词命中」，
    等于没写。
    """
    import ir_agent.gaps as G
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.ledger import FactLedger

    f = Factor(key="cost", label="原材料成本(铜箔/电子级玻纤布/环氧树脂价格)",
               unit="pp", source_hint="年报", search_term="成本")
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[f])
    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期原材料成本上升 7%。"))
    data = G.collect(_ExplodingClient(), [d], FactLedger(), None,
                     "覆铜板", AS_OF, sections=sections)

    assert data["cost"].status == G.ANNUAL
    assert "靠模型给的检索词" not in G.summary(data), \
        "L2（去括号的头「原材料成本」）命中却被标成了靠检索词命中"


def test_标注按字符串比较而非按位置():
    """标签本身就是「产能」、检索词也给「产能」时，候选列表去重后它**不会**
    作为第四项出现。按位置判断（「第 4 项就是检索词」）会在这里说错话——
    把一次 L1 命中标成「靠检索词命中」。

    这条红了意味着有人改成了数位置。
    """
    import ir_agent.gaps as G
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.ledger import FactLedger

    f = Factor(key="capacity", label="产能", unit="变化率",
               source_hint="年报", search_term="产能")
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[f])
    sections = _sections(("mgmt", "经营情况讨论与分析", "本期产能提升 10%。"))
    data = G.collect(_ExplodingClient(), [d], FactLedger(), None,
                     "覆铜板", AS_OF, sections=sections)
    assert data["capacity"].status == G.ANNUAL
    # 命中的候选词确实等于 search_term，所以这里**应当**标注——
    # 本测试要钉住的是实现方式（比较字符串，不数位置），不是标不标。
    assert "靠模型给的检索词" in G.summary(data)
```

- [ ] **Step 2: 跑，确认失败**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "靠检索词 or 靠标签结构 or 按字符串比较"`

Expected: FAIL，三条里至少两条失败（渲染输出里没有「靠模型给的检索词」）

- [ ] **Step 3: 三个匹配器返回命中的候选词**

三处各改返回类型与 `return` 语句。`_match_annual`：

```python
def _match_annual(sections, label: str,
                  search_term: str = "") -> tuple[str, str, str] | None:
```

```python
            if hit is not None:
                return hit, f"《{s.label}》", core
```

`_match_report`：

```python
def _match_report(corpus, label: str,
                  search_term: str = "") -> tuple[str, Document, str] | None:
```

```python
            if hit is not None:
                return hit, d, core
```

`_match_announce`：

```python
def _match_announce(corpus, label: str,
                    search_term: str = "") -> tuple[str, Document, str] | None:
```

```python
            if hit is not None:
                return hit, d, core
```

三处 docstring 各补一句：

```python
    **第三个返回值是命中的那个候选词。** `collect` 靠它判断命中来自哪一级
    ——相等于 `Factor.search_term` 就说明是靠模型给的检索词命中的，那件事
    要在产物清单上说出来（见 `_via_note`）。
```

- [ ] **Step 4: 加 `_via_note`**

加在 `_match_announce` 之后、`collect` 之前：

```python
def _via_note(core: str, f: Factor) -> str:
    """命中来自模型给的检索词时，在 `note` 末尾说出来；否则空串。

    **这是放开检索词闸门的对价。** 闸门只查长度（见
    `drivers.Factor.search_term`），所以一个泛词会一路走到底：实测
    「覆铜板」在真实公告语料里命中 98 次、「产能」31 次、「服务器」18 次，
    那些命中与因子无关，却会被标成「年报披露」。代码不再拦，那就至少要
    让读者分得清「这条证据是代码按标签结构找到的」与「这条是靠模型指的
    路找到的」。同源做法见 `Corpus.media_cited` 的「警告不驳回」与
    `host_check_degraded` 的「不阻断但必须一路传到产物清单」。

    **按字符串比较，不按位置。** `_core_candidates` 会去重：若标签本身就是
    「产能」、检索词也给「产能」，它根本不会作为第四项出现，数位置会在这种
    情形下说错话。见 `test_标注按字符串比较而非按位置`。
    """
    if f.search_term and core == f.search_term:
        return f"（靠模型给的检索词「{f.search_term}」命中，非标签结构）"
    return ""
```

- [ ] **Step 5: `collect` 三处解包与拼装**

②年报那一处（现第 353-365 行）：

```python
            hit = _match_annual(sections, f.label, f.search_term)
            if hit is not None:
                excerpt, src, core = hit
                out[f.key] = FactorData(
                    factor=f, status=ANNUAL,
                    note=f"「{excerpt}」——{src}"
                         "（文字依据，是否给出具体数值待人工确认）"
                         + _via_note(core, f),
                    source_url=annual_url, source_retrieved=as_of,
                    # 与 note 里包在「」中的那段是同一份文字——note 是
                    # 拼给人看的，这里是给 assume._match_excerpt 用的，
                    # 两者必须来自同一个 excerpt 变量，不能事后各改各的。
                    source_excerpt=excerpt)
                continue
```

②.5 公告那一处：

```python
            hit = _match_announce(corpus, f.label, f.search_term)
            if hit is not None:
                excerpt, doc, core = hit
                out[f.key] = FactorData(
                    factor=f, status=ANNOUNCE,
                    note=f"「{excerpt}」——{doc.cite()}"
                         "（文字依据，是否给出具体数值待人工确认）"
                         + _via_note(core, f),
                    source_url=doc.url, source_retrieved=doc.retrieved,
                    source_excerpt=excerpt)
                continue
```

③研报那一处：

```python
            hit = _match_report(corpus, f.label, f.search_term)
            if hit is not None:
                excerpt, doc, core = hit
                out[f.key] = FactorData(
                    factor=f, status=REPORT,
                    note=f"「{excerpt}」——{doc.cite()}"
                         "（文字依据，是否给出具体数值待人工确认）"
                         + _via_note(core, f),
                    source_url=doc.url, source_retrieved=doc.retrieved,
                    source_excerpt=excerpt)
                continue
```

- [ ] **Step 6: 跑，确认通过**

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "靠检索词 or 靠标签结构 or 按字符串比较"`

Expected: PASS，3 passed

- [ ] **Step 7: 突变验证两件事**

① **标注不是无条件加的**——把 `_via_note` 改成无条件返回那句话：

```python
    return f"（靠模型给的检索词「{f.search_term}」命中，非标签结构）"
```

Run: `python -m pytest tests/test_gaps.py -q -m "not live" -k "靠标签结构"`

Expected: **FAIL**。**看到红之后还原。**

② **标注确实到得了人眼前，这一条已由计划作者核实，不必再验。**
实测把一个带标注的 `FactorData(status=ANNUAL)` 丢给 `G.summary()`，渲染结果是：

```
| 服务器及通信用高端覆铜板出货量同比增速 | 年报披露 | 「本期出货量同比增长 23%。」
——《经营情况讨论与分析》（文字依据，是否给出具体数值待人工确认）
（靠模型给的检索词「出货量」命中，非标签结构） |
```

`summary()` 只在 `status == EXTERNAL` 时才用 `findings` 顶替 `note`；
②/②.5/③ 三种状态渲染的就是 `fd.note`，所以标注一路到人眼前。

**仍然要把断言写在 `G.summary(data)` 上而不是 `fd.note` 上**——不是因为
今天两者会不一致，而是因为要求本身是「人看得见」。子项目 B 的终审正是在
这里栽过：两条测试都断在字段上，而 EXTERNAL 分支的渲染器把那个字段整行
丢掉，两条测试照样绿。断言要跟着要求走，不跟着今天的实现走。

两处都还原之后跑一次确认绿，`git diff` 确认无残留。

- [ ] **Step 8: 既有行为回归**

Run: `python -m pytest tests/test_gaps.py tests/test_full_context.py tests/test_assume.py -q -m "not live"`

Expected: PASS。重点看这几条既有测试仍绿（它们解包匹配器的返回值或依赖
三级状态标签）：`test_年报优先于研报`、`test_公告优先于研报`、
`test_公告命中取出COMPANY文档的摘录`、`test_公告的否定语境不算命中`、
`test_MEDIA语料不产生研报测算命中`、`test_候选词在外层容器在内层`、
`test_带括号注解的标签能命中年报章节`、`test_带括号注解的标签能命中公告`。

**若有测试因为二元组变三元组而红**：那是正常的——改它的解包，不要改回
二元组。在报告里逐条列出改了哪些。

- [ ] **Step 9: 全套**

Run: `python -m pytest tests -q -m "not live" -W error`

Expected: PASS，`1972 passed, 19 deselected`（Task 3 后的 1969 + 3），无警告。

- [ ] **Step 10: 提交**

```bash
git add ir_agent/gaps.py tests/test_gaps.py
git commit -m "gaps: 靠模型检索词命中的因子在清单上标出来，匹配器返回命中的候选词"
```

---

## 实跑之后要如实记录的事（不设通过线）

本计划**不包含**端到端实跑。真跑的时候如实记录：

1. **模型给了什么检索词。** `drivers.md` 的人读表现在有这一列，直接看。
   逐条判断：是精确词还是泛词？对照 spec 第二节那张实测命中表
   （「覆铜板」98 次、「产能」31 次、「服务器」18 次是不该给的例子）。
2. **有几个因子是靠 L4 命中的。** 清单里数「靠模型给的检索词」出现几次。
   这个数字就是放开闸门换来的东西。
3. **那几条命中是证据还是巧合。** 逐条看摘出的原文片段。**如果过半是巧合，
   那是一条重要发现**，说明「让模型决定」在这个环节还不成立——该报告它，
   而不是悄悄把闸门加回去。
4. **方法论环节有没有因为 1 字检索词被驳回。** 3 次驳回失败 = 整个因子层
   消失（spec 第七节①之二）。

**绝不为了让数字好看而回头加回子串闸门、或把 L4 挪到更前面。** 前者是用户
已裁定的决定，后者有测试守着——两者都要先重新讨论，不是实跑时顺手改。
