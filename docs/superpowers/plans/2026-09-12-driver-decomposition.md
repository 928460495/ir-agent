# 驱动因子分解 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在数据采集之前插入方法论预研，产出算术闭合的驱动因子分解式；按分解式定向采集数据；判断项改为逐因子填写，由代码合成回营收增速与毛利率，并在 DCF 工作簿中以可联动的方式呈现。

**Architecture:** 新增四个模块（`drivers.py` 表示与校验、`methodology.py` 预研、`sources/web.py` 开放网络采集、`gaps.py` 缺口识别与采集编排），改造 `assume.py` 使判断项按因子填写，并扩展 ashare-dcf 的 `ashare_build`（新增 `1b_驱动分解` 工作表）与 `ashare_verify`（支持跨表引用解析）。

**Tech Stack:** Python 3.13、pytest、openpyxl、formulas（公式引擎）、pandas、requests；模型调用走既有 `ir_agent/clients/claude_cli.py`（`claude -p --output-format json`）。

## Global Constraints

- 规格文件：`docs/superpowers/specs/2026-09-12-driver-decomposition-design.md`，所有取值与规则以它为准。
- 单位白名单只有两个值：`变化率` 与 `pp`，与 op 严格绑定（MULTIPLY↔变化率，ADD_PP↔pp），用错即驳回。
- **pp 不得进乘法链。** 稼动率 85%→82% 是 −3pp，但乘法因子的变化率是 82/85−1 = −3.5%。
- 因子数 2–5。
- 历史回算**报残差不判对错**，不设硬上限。
- MEDIA 级来源不得单独支撑一个因子的取值。
- 因子采集不到 → 标「无数据，取值为估计」，**绝不用代理指标顶替**。
- 三档列序 **B=Bull、C=Base、D=Bear**，与 `CHOOSE($B$SCEN,…)` 绑定。
- `ashare_verify` 的改动属验证核心：验算失败为**硬失败**，不降级。
- 每个任务结束前跑 `python -m pytest -q`，全绿才提交。
- 提交信息用中文，说明「为什么」而非「改了什么」，结尾附
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`。

---

### Task 0: 给 ashare-dcf 建立版本控制

后续 Task 8、9 要改 ashare-dcf 的建表与验证核心。该目录目前**不是 git 仓库**，
改坏了无法回滚。

**Files:**
- Create: `/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf/.gitignore`

- [ ] **Step 1: 确认它确实不是仓库**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" && git rev-parse --is-inside-work-tree 2>&1 | head -1
```

Expected: `fatal: not a git repository (or any of the parent directories): .git`

- [ ] **Step 2: 写 .gitignore**

```
__pycache__/
*.pyc
*.xlsx
.DS_Store
models/
samples/
```

- [ ] **Step 3: 初始化并提交当前状态作为基线**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" && git init -q && git add -A && git commit -q -m "基线: 接入 ir-agent 之前的 ashare-dcf 现状

后续要改 ashare_build（新增驱动分解表）与 ashare_verify（跨表引用解析）。
验证核心改坏了必须能回滚，因此先立一个基线。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" && git log --oneline | head -1
```

Expected: 打印一行提交哈希与「基线: …」

---

### Task 1: `drivers.py` — 分解式的表示与闭合校验

**Files:**
- Create: `ir_agent/drivers.py`
- Test: `tests/test_drivers.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `class Op(str, Enum)`: `MULTIPLY = "乘"`, `ADD_PP = "加"`
  - `UNITS: dict[Op, str]` = `{Op.MULTIPLY: "变化率", Op.ADD_PP: "pp"}`
  - `@dataclass(frozen=True) class Factor`: `key: str`, `label: str`, `unit: str`, `source_hint: str = ""`
  - `@dataclass class Decomposition`: `target: str`, `op: Op`, `factors: list[Factor]`
  - `class DecompositionInvalid(Exception)`
  - `def check_closure(d: Decomposition) -> None` — 不合法抛 `DecompositionInvalid`
  - `MIN_FACTORS = 2`, `MAX_FACTORS = 5`
  - `TARGETS = {"revenue": Op.MULTIPLY, "gross_margin": Op.ADD_PP}`

- [ ] **Step 1: 写失败的测试**

```python
"""分解式的表示与闭合校验。**闭合是硬约束** —— 不闭合的分解式不是
「不够精确」，是代码根本算不出来。"""

from __future__ import annotations

import pytest

from ir_agent.drivers import (MAX_FACTORS, MIN_FACTORS, Decomposition,
                              DecompositionInvalid, Factor, Op, check_closure)


def _f(key="capacity", label="产能", unit="变化率"):
    return Factor(key=key, label=label, unit=unit, source_hint="年报产能表")


def _rev(*factors):
    return Decomposition(target="revenue", op=Op.MULTIPLY,
                         factors=list(factors) or [_f(), _f("asp", "单价")])


def test_合法的营收分解通过():
    check_closure(_rev(_f(), _f("utilization", "稼动率"), _f("asp", "单价")))


def test_合法的毛利率分解通过():
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util_leverage", label="稼动率摊薄", unit="pp"),
        Factor(key="depreciation", label="折旧变动", unit="pp")])
    check_closure(d)


def test_营收链里出现pp被驳回():
    """**pp 不能进乘法链。** 稼动率 85%→82% 是 −3pp，但乘法因子的变化率是
    82/85−1 = −3.5%。混用会让合成结果系统性偏错，而偏差不大、不易发现。"""
    with pytest.raises(DecompositionInvalid, match="变化率"):
        check_closure(_rev(_f(), _f("utilization", "稼动率", unit="pp")))


def test_毛利率链里出现变化率被驳回():
    d = Decomposition(target="gross_margin", op=Op.ADD_PP,
                      factors=[Factor(key="a", label="甲", unit="pp"),
                               Factor(key="b", label="乙", unit="变化率")])
    with pytest.raises(DecompositionInvalid, match="pp"):
        check_closure(d)


def test_单位不在白名单被驳回():
    """挡掉「客户结构优化」这类无法量化的因子 —— 写不出单位的项就写不进来，
    这是闭合校验真正的闸门。"""
    with pytest.raises(DecompositionInvalid, match="单位"):
        check_closure(_rev(_f(), _f("mix", "客户结构优化", unit="定性")))


def test_op与target必须匹配():
    d = Decomposition(target="revenue", op=Op.ADD_PP, factors=[_f(), _f("b")])
    with pytest.raises(DecompositionInvalid, match="revenue"):
        check_closure(d)


def test_因子太少被驳回():
    """一个因子不叫分解。"""
    with pytest.raises(DecompositionInvalid, match="至少"):
        check_closure(_rev(_f()))


def test_因子太多被驳回():
    """超过五个人核实不过来 —— 分解的价值在于人能检查，不在于穷举。"""
    many = [_f(f"k{i}", f"因子{i}") for i in range(MAX_FACTORS + 1)]
    with pytest.raises(DecompositionInvalid, match="至多"):
        check_closure(_rev(*many))


def test_因子key不得重复():
    with pytest.raises(DecompositionInvalid, match="重复"):
        check_closure(_rev(_f("capacity"), _f("capacity", "产能2")))


def test_未知target被驳回():
    d = Decomposition(target="ebitda", op=Op.MULTIPLY, factors=[_f(), _f("b")])
    with pytest.raises(DecompositionInvalid, match="ebitda"):
        check_closure(d)


def test_边界值刚好通过():
    assert MIN_FACTORS == 2 and MAX_FACTORS == 5
    check_closure(_rev(*[_f(f"k{i}", f"因子{i}") for i in range(MIN_FACTORS)]))
    check_closure(_rev(*[_f(f"k{i}", f"因子{i}") for i in range(MAX_FACTORS)]))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_drivers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ir_agent.drivers'`

- [ ] **Step 3: 写实现**

```python
"""驱动因子分解式 —— 「这个行业靠什么赚钱」的可计算表达。

**闭合是硬约束，不是精度问题。** 「营收 = 产能 × 稼动率 × 单价」闭合，
三项能乘回营收；「营收 = 产能 × 品牌力」不闭合 —— 它是个比喻。一旦代码
要从因子合成营收增速，不闭合的分解式就根本算不出来。

而 AI 很容易产出听着专业但不闭合的分解:「产能 × 稼动率 × 单价 × 客户结构
优化」前三项能乘、第四项不能。**要求每个因子声明单位，写不出单位的项就
写不进来** —— 这是这里真正的闸门。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Op(str, Enum):
    MULTIPLY = "乘"      # 营收增速 = Π(1 + 因子变化率) − 1
    ADD_PP = "加"        # 毛利率   = 基期 + Σ 因子贡献(pp)


# 单位白名单**只有两个值**，与 op 严格绑定。
#
# pp 不得进乘法链: 稼动率 85%→82% 是 −3pp，但作为乘法因子它的变化率是
# 82/85 − 1 = −3.5%。两者不等；混用会让合成结果系统性偏错，而偏差不大、
# 不容易被发现 —— 正是那种会一直藏着的错。
UNITS: dict[Op, str] = {Op.MULTIPLY: "变化率", Op.ADD_PP: "pp"}

TARGETS: dict[str, Op] = {"revenue": Op.MULTIPLY, "gross_margin": Op.ADD_PP}
TARGET_LABEL = {"revenue": "营收", "gross_margin": "毛利率"}

MIN_FACTORS = 2      # 一个因子不叫分解
MAX_FACTORS = 5      # 超过五个人核实不过来


class DecompositionInvalid(Exception):
    """分解式不闭合 —— 代码算不出来，拒绝往下走。"""


@dataclass(frozen=True)
class Factor:
    key: str            # capacity | utilization | asp
    label: str          # 产能 / 稼动率 / 单价
    unit: str           # 必须是 UNITS 里的值
    source_hint: str = ""   # 这个数去哪找


@dataclass
class Decomposition:
    target: str                 # revenue | gross_margin
    op: Op
    factors: list[Factor] = field(default_factory=list)

    def describe(self) -> str:
        sep = " × " if self.op is Op.MULTIPLY else " + "
        body = sep.join(f.label for f in self.factors)
        head = TARGET_LABEL.get(self.target, self.target)
        return f"{head} = {body}" if self.op is Op.MULTIPLY \
            else f"{head} = 基期 + {body}"


def check_closure(d: Decomposition) -> None:
    """闭合校验。不合法抛 DecompositionInvalid，**不返回布尔值** ——
    调用方拿到的必须是「为什么不行」，不是一个 False。"""
    want_op = TARGETS.get(d.target)
    if want_op is None:
        raise DecompositionInvalid(
            f"未知的分解目标 {d.target!r}，只能是："
            + "、".join(TARGETS))
    if d.op is not want_op:
        raise DecompositionInvalid(
            f"{d.target} 只能用 {want_op.value}（{UNITS[want_op]}），"
            f"得到 {d.op.value}")

    n = len(d.factors)
    if n < MIN_FACTORS:
        raise DecompositionInvalid(
            f"至少 {MIN_FACTORS} 个因子，得到 {n} —— 一个因子不叫分解")
    if n > MAX_FACTORS:
        raise DecompositionInvalid(
            f"至多 {MAX_FACTORS} 个因子，得到 {n} —— "
            "分解的价值在于人能检查，不在于穷举")

    want_unit = UNITS[d.op]
    seen: set[str] = set()
    for f in d.factors:
        if f.unit != want_unit:
            raise DecompositionInvalid(
                f"因子「{f.label}」的单位是 {f.unit!r}，"
                f"而 {d.op.value} 法链只接受 {want_unit!r}。"
                + ("pp 不能进乘法链 —— 稼动率 85%→82% 是 −3pp，"
                   "但乘法因子的变化率是 −3.5%。"
                   if want_unit == "变化率" else
                   "比率的变化只能加减，不能相乘。"))
        if f.key in seen:
            raise DecompositionInvalid(f"因子 key 重复：{f.key}")
        seen.add(f.key)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_drivers.py -q`
Expected: `11 passed`

- [ ] **Step 5: 跑全量测试**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest -q`
Expected: 全绿（现有 1176 + 新增 11）

- [ ] **Step 6: 提交**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && git add ir_agent/drivers.py tests/test_drivers.py && git commit -q -m "驱动因子分解式的表示与闭合校验

闭合是硬约束不是精度问题:「营收 = 产能 × 稼动率 × 单价」闭合，三项能乘回
营收;「营收 = 产能 × 品牌力」不闭合 —— 它是个比喻。代码要从因子合成增速，
不闭合的分解式根本算不出来。

而 AI 很容易产出听着专业但不闭合的分解:「产能 × 稼动率 × 单价 × 客户结构
优化」前三项能乘、第四项不能。**要求每个因子声明单位，写不出单位的项就
写不进来** —— 这是真正的闸门。

单位白名单只有两个值，与 op 严格绑定。**pp 不得进乘法链**: 稼动率
85%→82% 是 −3pp，但乘法因子的变化率是 82/85−1 = −3.5%，混用会让合成结果
系统性偏错，而偏差不大、不容易被发现。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `drivers.py` — 合成与历史回算

**Files:**
- Modify: `ir_agent/drivers.py`（追加）
- Test: `tests/test_drivers.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `Op`、`Decomposition`、`Factor`
- Produces:
  - `def compose(d: Decomposition, values: dict[str, Decimal], base: Decimal | None = None) -> Decimal`
    — MULTIPLY 返回增速；ADD_PP 返回毛利率绝对值（需要 `base`）
  - `@dataclass(frozen=True) class Residual`: `period: str`, `composed: Decimal`, `actual: Decimal`, `gap: Decimal`
  - `def reconcile(d, history: dict[str, dict[str, Decimal]], actual: dict[str, Decimal], base: dict[str, Decimal] | None = None) -> list[Residual]`
  - `def pp_hint(rate: Decimal, base_level: Decimal | None) -> str` — 变化率 → 「−3.5%（约 −3.0pp）」，基期未知则只返回变化率

- [ ] **Step 1: 写失败的测试**

```python
# 追加到 tests/test_drivers.py

from decimal import Decimal

from ir_agent.drivers import Residual, compose, pp_hint, reconcile

D = Decimal


def test_乘法链合成增速():
    """营收增速 = Π(1+r) − 1。"""
    d = _rev(_f("capacity"), _f("utilization", "稼动率"), _f("asp", "单价"))
    got = compose(d, {"capacity": D("0.12"), "utilization": D("-0.035"),
                      "asp": D("-0.02")})
    # 1.12 × 0.965 × 0.98 − 1
    assert abs(got - D("0.059176")) < D("1e-6")


def test_加法链需要基期():
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util", label="稼动率摊薄", unit="pp"),
        Factor(key="dep", label="折旧变动", unit="pp")])
    got = compose(d, {"util": D("-1.5"), "dep": D("-0.8")}, base=D("0.1415"))
    # 14.15% − 1.5pp − 0.8pp = 11.85%
    assert abs(got - D("0.1185")) < D("1e-9")


def test_加法链缺基期就报错():
    """毛利率是绝对水平，没有基期算不出来 —— 默认成 0 会得到一个
    看起来正常的错数。"""
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="a", label="甲", unit="pp"),
        Factor(key="b", label="乙", unit="pp")])
    with pytest.raises(DecompositionInvalid, match="基期"):
        compose(d, {"a": D("1"), "b": D("1")})


def test_因子取值缺失就报错而非当零():
    """当零会让一个漏填悄悄变成「该因子无影响」。"""
    d = _rev(_f("capacity"), _f("asp", "单价"))
    with pytest.raises(DecompositionInvalid, match="capacity|缺"):
        compose(d, {"asp": D("0.01")})


def test_历史回算报残差():
    """Π(1+r) 几乎永远不等于实际增速 —— 产品结构、汇率、并表都会留下残差。
    要求严格相等等于禁用所有分解式，所以这里**报残差不判对错**。"""
    d = _rev(_f("capacity"), _f("asp", "单价"))
    hist = {"2024FY": {"capacity": D("0.10"), "asp": D("0.05")},
            "2025FY": {"capacity": D("0.08"), "asp": D("0.02")}}
    actual = {"2024FY": D("0.16"), "2025FY": D("0.11")}
    got = reconcile(d, hist, actual)
    assert [r.period for r in got] == ["2024FY", "2025FY"]
    # 1.10×1.05−1 = 0.155 → 残差 0.16−0.155 = +0.005
    assert abs(got[0].gap - D("0.005")) < D("1e-9")
    assert abs(got[1].gap - D("0.0084")) < D("1e-9")


def test_回算跳过数据不全的期间():
    """标「无历史校验」由调用方负责；这里只做能算的，绝不用零补齐。"""
    d = _rev(_f("capacity"), _f("asp", "单价"))
    hist = {"2024FY": {"capacity": D("0.10")},          # 缺 asp
            "2025FY": {"capacity": D("0.08"), "asp": D("0.02")}}
    got = reconcile(d, hist, {"2024FY": D("0.16"), "2025FY": D("0.11")})
    assert [r.period for r in got] == ["2025FY"]


def test_回算缺实际值也跳过():
    d = _rev(_f("capacity"), _f("asp", "单价"))
    hist = {"2025FY": {"capacity": D("0.08"), "asp": D("0.02")}}
    assert reconcile(d, hist, {}) == []


def test_pp提示仅在基期已知时给出():
    """基期未知就只显示变化率，**绝不倒推** —— 倒推出来的 pp 是个凭空的数。"""
    assert pp_hint(D("-0.035"), D("0.85")) == "-3.50%（约 -2.98pp）"
    assert pp_hint(D("-0.035"), None) == "-3.50%"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_drivers.py -q`
Expected: FAIL — `ImportError: cannot import name 'compose' from 'ir_agent.drivers'`

- [ ] **Step 3: 写实现**

```python
# 追加到 ir_agent/drivers.py 末尾

from dataclasses import dataclass as _dc
from decimal import Decimal


def compose(d: Decomposition, values: dict[str, Decimal],
            base: Decimal | None = None) -> Decimal:
    """按 op 把因子合成回目标。

    MULTIPLY → 返回**增速**（Π(1+r) − 1）
    ADD_PP   → 返回**绝对水平**（基期 + Σpp/100），因此必须给 base

    取值缺失一律报错而不当零: 当零会让一个漏填悄悄变成「该因子无影响」。
    """
    missing = [f.key for f in d.factors if f.key not in values]
    if missing:
        raise DecompositionInvalid(
            f"因子取值缺失：{'、'.join(missing)} —— 缺项当零会让漏填"
            "悄悄变成「该因子无影响」")

    if d.op is Op.MULTIPLY:
        out = Decimal(1)
        for f in d.factors:
            out *= (Decimal(1) + values[f.key])
        return out - Decimal(1)

    if base is None:
        raise DecompositionInvalid(
            "加法链需要基期水平 —— 毛利率是绝对值，没有基期算不出来，"
            "默认成 0 会得到一个看起来正常的错数")
    return base + sum(values[f.key] for f in d.factors) / Decimal(100)


@_dc(frozen=True)
class Residual:
    period: str
    composed: Decimal
    actual: Decimal
    gap: Decimal          # actual − composed


def reconcile(d: Decomposition,
              history: dict[str, dict[str, Decimal]],
              actual: dict[str, Decimal],
              base: dict[str, Decimal] | None = None) -> list[Residual]:
    """历史回算 —— **报残差，不判对错**。

    Π(1+r_i) 几乎永远不会精确等于实际营收增速: 产品结构、汇率、并表范围都
    会留下残差。若要求严格相等，没有一个分解式能通过。

    残差**小且稳定** → 分解抓住了主要矛盾。
    残差**大或跳动** → 有重要因子没被纳入，这件事要让人看见。

    数据不全的期间**跳过而非用零补齐** —— 补出来的残差是假的。
    """
    out: list[Residual] = []
    for period in sorted(history):
        vals = history[period]
        if any(f.key not in vals for f in d.factors) or period not in actual:
            continue
        try:
            c = compose(d, vals, (base or {}).get(period))
        except DecompositionInvalid:
            continue
        a = actual[period]
        out.append(Residual(period=period, composed=c, actual=a, gap=a - c))
    return out


def pp_hint(rate: Decimal, base_level: Decimal | None) -> str:
    """变化率 → 便于阅读的 pp 换算。**基期未知就不换算。**

    倒推出来的 pp 是个凭空的数 —— 显示它会让读者以为我们知道基期水平。
    """
    txt = f"{rate:.2%}"
    if base_level is None:
        return txt
    return f"{txt}（约 {base_level * rate * 100:+.2f}pp）"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_drivers.py -q`
Expected: `19 passed`

- [ ] **Step 5: 跑全量并提交**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest -q && git add -A && git commit -q -m "分解式的合成与历史回算

合成: MULTIPLY 返回增速 Π(1+r)−1；ADD_PP 返回绝对水平，因此**必须给基期**
—— 毛利率没有基期算不出来，默认成 0 会得到一个看起来正常的错数。
因子取值缺失一律报错不当零: 当零会让漏填悄悄变成「该因子无影响」。

历史回算**报残差不判对错**。Π(1+r) 几乎永远不等于实际增速 —— 产品结构、
汇率、并表范围都会留下残差；要求严格相等等于禁用所有分解式。残差小且稳定
说明分解抓住了主要矛盾，大或跳动说明有重要因子没被纳入，后者要让人看见。
数据不全的期间跳过而非用零补齐 —— 补出来的残差是假的。

pp 提示仅在基期已知时给出，绝不倒推: 倒推出来的 pp 是个凭空的数，
显示它会让读者以为我们知道基期水平。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 验证 claude CLI 的联网能力（决定后续两条路）

`methodology.py` 与 `sources/web.py` 都依赖「让模型上网搜」。ir-agent 是独立
Python 包，用不了 Claude Code 的 WebSearch 工具，只能通过既有的
`clients/claude_cli.py`（`claude -p`）让模型自己搜。**这个能力是否可用必须先
验证** —— 验不通就走规格里的降级路径（只用已有源）。

**Files:**
- Create: `tests/test_web_capability.py`

**Interfaces:**
- Consumes: `ir_agent.clients.claude_cli.make_client`
- Produces: `WEB_CAPABLE: bool`（写进 `ir_agent/sources/web.py`，Task 5 用）

- [ ] **Step 1: 手工探测（不是测试，是一次性验证）**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && PYTHONPATH="$PWD" python -c "
from ir_agent.clients.claude_cli import make_client
c = make_client()
out = c('请用一句话回答：你现在能否联网搜索？若能，搜索「半导体封测 稼动率」'
        '并给出一个你搜到的具体数据点与其来源网址；若不能，只回复「不能联网」。')
print(out[:600])
"
```

Expected: 两种之一 ——
(a) 含 `http` 开头的网址与具体数据 → 可联网，记 `WEB_CAPABLE = True`
(b) 「不能联网」或只给通用描述无网址 → 不可联网，记 `WEB_CAPABLE = False`

- [ ] **Step 2: 按探测结果写常量与说明**

若 (a)，在 Task 5 创建 `ir_agent/sources/web.py` 时写：

```python
# 实测 claude -p 可联网搜索（2026-09-12 验证）。
WEB_CAPABLE = True
```

若 (b)，写：

```python
# 实测 claude -p **不能**联网（2026-09-12 验证）。开放网络采集因此不可用，
# 定向采集退回「只用已有源」: 年报叙述章节 / 巨潮公告 / 研报语料库。
# 这不是缺陷被绕过，是能力边界 —— 规格的降级路径明确要求说出来。
WEB_CAPABLE = False
```

- [ ] **Step 3: 记录探测结果**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && mkdir -p docs/superpowers/notes && cat > docs/superpowers/notes/2026-09-12-web-capability.md <<'EOF'
# claude -p 联网能力探测

日期：2026-09-12

**结论：**（填 可联网 / 不可联网）

**探测方式：** 让模型搜索「半导体封测 稼动率」并要求给出具体数据点与来源网址。

**原始回复（截断）：**

```
（粘贴实际回复）
```

**影响：** 若不可联网，`sources/web.py` 的开放网络采集不可用，定向采集退回
年报 / 公告 / 研报语料库三源；规格中「MEDIA 级不得单独支撑因子取值」的约束
相应失去适用对象，但保留代码以备能力变化。
EOF
git add -A && git commit -q -m "探测 claude -p 的联网能力

方法论预研与开放网络采集都依赖它。ir-agent 是独立 Python 包，用不了
Claude Code 的 WebSearch 工具，只能通过 claude -p 让模型自己搜 ——
这个能力是否可用决定后续两个模块走哪条路，因此先验证再动手。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `methodology.py` — 方法论预研产出分解式

**Files:**
- Create: `ir_agent/methodology.py`
- Test: `tests/test_methodology.py`

**Interfaces:**
- Consumes: Task 1 的 `Decomposition`/`Factor`/`Op`/`check_closure`/`DecompositionInvalid`；`ir_agent.lineparse.keyed`、`pick`
- Produces:
  - `class MethodologyFailed(Exception)`
  - `@dataclass class Methodology`: `industry: str`, `decompositions: list[Decomposition]`, `pitfalls: list[str]`, `rationale: str`
  - `def parse(text: str) -> Methodology`
  - `def research(client, industry: str, code: str, max_attempts: int = 3) -> Methodology`
  - `def to_markdown(m: Methodology) -> str`

- [ ] **Step 1: 写失败的测试**

```python
"""方法论预研：先问「这个行业靠什么赚钱」，再去采集数据。"""

from __future__ import annotations

import pytest

from ir_agent.drivers import Op
from ir_agent.methodology import (Methodology, MethodologyFailed, parse,
                                  research, to_markdown)

OUT = """分解: 营收 | 乘 | 产能:变化率:年报产能与在建工程 | 稼动率:变化率:研报行业稼动率测算 | 单价:变化率:研报封测价格跟踪
分解: 毛利率 | 加 | 稼动率摊薄:pp:固定成本占比 | 折旧变动:pp:在建工程转固节奏 | 上游成本:pp:基板与金线报价
陷阱: 用周期中某一点的利润率外推永续，是封测估值最常见的误用
陷阱: 产能利用率 A 股多数封测厂不直接披露，勿用固定资产周转率代替
"""


class _Client:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


def test_解析两条分解式与陷阱():
    m = parse(OUT)
    assert len(m.decompositions) == 2
    rev = m.decompositions[0]
    assert rev.target == "revenue" and rev.op is Op.MULTIPLY
    assert [f.label for f in rev.factors] == ["产能", "稼动率", "单价"]
    assert rev.factors[0].source_hint.startswith("年报")
    assert len(m.pitfalls) == 2


def test_不闭合的分解式被驳回():
    """「客户结构优化」写不出单位，因此写不进来。"""
    bad = ("分解: 营收 | 乘 | 产能:变化率:年报 | 稼动率:变化率:研报 "
           "| 客户结构优化:定性:无\n")
    with pytest.raises(MethodologyFailed, match="单位"):
        parse(bad)


def test_pp混进乘法链被驳回():
    bad = "分解: 营收 | 乘 | 产能:变化率:年报 | 稼动率:pp:研报\n"
    with pytest.raises(MethodologyFailed, match="变化率"):
        parse(bad)


def test_两个目标都要给():
    """只给营收不给毛利率，毛利率就会沿用旧的两标量模式 ——
    半套分解比不分解更容易让人以为都分解了。"""
    only_rev = "分解: 营收 | 乘 | 产能:变化率:年报 | 单价:变化率:研报\n"
    with pytest.raises(MethodologyFailed, match="毛利率"):
        parse(only_rev)


def test_容忍列表前缀():
    """与四个定性模块同一套解析容错。"""
    m = parse("1. " + OUT.replace("\n分解", "\n2. 分解"))
    assert len(m.decompositions) == 2


def test_重试后成功():
    bad = "分解: 营收 | 乘 | 产能:定性:无 | 单价:变化率:研报\n"
    c = _Client(bad, OUT)
    m = research(c, industry="集成电路封测", code="600584")
    assert len(m.decompositions) == 2
    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]


def test_连续失败则拒绝():
    bad = "分解: 营收 | 乘 | 产能:定性:无\n"
    with pytest.raises(MethodologyFailed, match="连续"):
        research(_Client(bad, bad, bad), industry="x", code="600584")


def test_失败信息带上原始输出():
    """只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。"""
    bad = "分解: 营收 | 乘 | 产能:定性:无\n"
    with pytest.raises(MethodologyFailed) as e:
        research(_Client(bad, bad, bad), industry="x", code="600584")
    assert "产能:定性" in str(e.value)


def test_提示词要求先搜索再回答():
    c = _Client(OUT)
    research(c, industry="集成电路封测", code="600584")
    p = c.prompts[0]
    assert "集成电路封测" in p and "搜索" in p
    assert "闭合" in p and "写不出单位" in p


def test_落盘的markdown给人看得懂():
    md = to_markdown(parse(OUT))
    assert "营收 = 产能 × 稼动率 × 单价" in md
    assert "毛利率 = 基期 + 稼动率摊薄 + 折旧变动 + 上游成本" in md
    assert "年报产能与在建工程" in md          # 因子线索要留下
    assert "最常见的误用" in md                # 陷阱要留下
    assert "改这个文件后重跑" in md            # 可覆盖的说明
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_methodology.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ir_agent.methodology'`

- [ ] **Step 3: 写实现**

```python
"""方法论预研 —— 先问「这个行业靠什么赚钱」，再去采集数据。

**这是整个框架此前缺的那一环。** 原先 industry_profile 算的三样东西
（集中度、生命周期、周期敏感度）全部只需要利润表和资产负债表 —— 它们之所以
「通用」，是因为回避了行业特性: 任何行业都能算 HHI，但 HHI 不告诉你封测的
毛利率由什么决定。

于是 AI 填判断项时手上只有历史统计和五力的定性判断，那是一个制造业公司的
**纯报表视角**。封测真正的驱动链是「产能 × 稼动率 × 单价」，这三个变量框架里
一个都没有。

这个模块的产出不是「更多指标」，是**一个可计算的分解式** —— 它决定了后面
去采集什么数据、以及人在批准清单上能检查到什么。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ir_agent.drivers import (Decomposition, DecompositionInvalid, Factor, Op,
                              check_closure)
from ir_agent.lineparse import keyed, pick

_DECOMP = keyed("分解")
_PITFALL = keyed("陷阱")

_TARGETS = {"营收": "revenue", "毛利率": "gross_margin"}
_OPS = {"乘": Op.MULTIPLY, "加": Op.ADD_PP}


class MethodologyFailed(Exception):
    """预研产出不可用 —— 退回不分解模式，并说明原因。"""


@dataclass
class Methodology:
    industry: str = ""
    decompositions: list[Decomposition] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)
    rationale: str = ""

    def of(self, target: str) -> Decomposition | None:
        return next((d for d in self.decompositions if d.target == target),
                    None)


def parse(text: str) -> Methodology:
    m = Methodology()
    for hit in _DECOMP.finditer(text):
        parts = [p.strip() for p in hit.group(1).split("|")]
        if len(parts) < 4:
            raise MethodologyFailed(
                f"分解一行字段不足（需 目标|运算|因子…，至少两个因子）：{hit.group(1)}")
        tgt = pick(parts[0], list(_TARGETS))
        if tgt is None:
            raise MethodologyFailed(
                f"未知的分解目标 {parts[0]!r}，只能是：" + "、".join(_TARGETS))
        op = pick(parts[1], list(_OPS))
        if op is None:
            raise MethodologyFailed(
                f"未知的运算 {parts[1]!r}，只能是：" + "、".join(_OPS))

        factors = []
        for raw in parts[2:]:
            bits = [x.strip() for x in raw.split(":")]
            if len(bits) < 2 or not bits[0]:
                raise MethodologyFailed(
                    f"因子写法应为「名称:单位:线索」，得到 {raw!r}")
            label, unit = bits[0], bits[1]
            hint = bits[2] if len(bits) > 2 else ""
            factors.append(Factor(key=_slug(label), label=label, unit=unit,
                                  source_hint=hint))
        d = Decomposition(target=_TARGETS[tgt], op=_OPS[op], factors=factors)
        try:
            check_closure(d)
        except DecompositionInvalid as e:
            raise MethodologyFailed(str(e)) from e
        m.decompositions.append(d)

    got = {d.target for d in m.decompositions}
    missing = [cn for cn, k in _TARGETS.items() if k not in got]
    if missing:
        # 半套分解比不分解更危险: 读者会以为两项都分解了。
        raise MethodologyFailed(
            f"缺少 {'、'.join(missing)} 的分解式 —— 两个目标都要给，"
            "只给一半会让读者以为两项都分解了")

    m.pitfalls = [h.group(1).strip() for h in _PITFALL.finditer(text)]
    return m


def _slug(label: str) -> str:
    """中文标签 → 稳定的 key。用于账本字段名与工作簿行匹配。"""
    table = {"产能": "capacity", "稼动率": "utilization", "单价": "asp",
             "销量": "volume", "吨价": "unit_price",
             "稼动率摊薄": "util_leverage", "折旧变动": "depreciation",
             "上游成本": "input_cost", "产品结构": "mix"}
    if label in table:
        return table[label]
    return "f_" + re.sub(r"\W+", "_", label).strip("_").lower()


PROMPT = """你正在为一份 A 股公司研究报告做**方法论预研**。

标的行业：{industry}　标的代码：{code}

**先上网搜索**这个行业的研究与估值方法论 —— 卖方怎么看这个行业、营收和
毛利率分别由什么驱动、有哪些公认的陷阱。搜完再回答，不要凭印象。

然后给出两条**算术闭合**的分解式：

`分解: 营收 | 乘 | 因子名:单位:数据线索 | 因子名:单位:数据线索 | …`
`分解: 毛利率 | 加 | 因子名:单位:数据线索 | …`

**闭合的硬性要求**
- 营收只能用「乘」，每个因子单位一律 `变化率`；毛利率只能用「加」，
  单位一律 `pp`。
- **pp 不能进乘法链**：稼动率 85%→82% 是 −3pp，但乘法因子的变化率是
  82/85−1 = −3.5%，两者不等。
- 每个因子必须写得出单位。**写不出单位的项就写不进来** ——
  「客户结构优化」听着专业但不能乘，这类一律不要。
- 每条 2–5 个因子。一个不叫分解；超过五个人核实不过来。
- 数据线索要具体到「去哪找」（年报哪一节 / 哪类研报 / 哪个行业数据源）。

再给出这个行业估值的已知陷阱，每条一行：

`陷阱: <一句话>`

只输出上述格式的行，不要其它说明。
"""


def _tail(raw: str, n: int = 400) -> str:
    raw = (raw or "").strip()
    return raw[-n:] if len(raw) > n else raw


def research(client, industry: str, code: str,
             max_attempts: int = 3) -> Methodology:
    prompt = PROMPT.format(industry=industry, code=code)
    last, raw = "", ""
    for attempt in range(1, max_attempts + 1):
        raw = client(prompt)
        try:
            m = parse(raw)
        except MethodologyFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新输出全部行。\n")
            continue
        m.industry, m.rationale = industry, raw.strip()
        return m
    raise MethodologyFailed(
        f"连续 {max_attempts} 次给不出闭合的分解式：{last}"
        f"\n最后一次的原始输出（末 400 字）：\n{_tail(raw)}")


def to_markdown(m: Methodology) -> str:
    lines = [f"# 驱动因子分解（{m.industry}）", "",
             "本次研究采用的分解式。**它决定了后面去采集什么数据、"
             "以及批准清单上能检查到什么** —— 清单上若没有某个因子，"
             "说明这个因素没被纳入。", ""]
    for d in m.decompositions:
        lines.append(f"## {d.describe()}")
        lines.append("")
        lines.append("| 因子 | 单位 | 数据线索 |")
        lines.append("| --- | --- | --- |")
        for f in d.factors:
            lines.append(f"| {f.label} | {f.unit} | {f.source_hint or '—'} |")
        lines.append("")
    if m.pitfalls:
        lines += ["## 该行业估值的已知陷阱", ""]
        lines += [f"- {x}" for x in m.pitfalls]
        lines.append("")
    lines += ["---", "",
              "分解式不另设人工确认门 —— 门加多了人会开始机械点「同意」。",
              "若你看到分解不对，**改这个文件后重跑第一阶段**即可覆盖。", ""]
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_methodology.py -q`
Expected: `10 passed`

- [ ] **Step 5: 跑全量并提交**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest -q && git add -A && git commit -q -m "方法论预研: 先问「这个行业靠什么赚钱」

这是整个框架此前缺的那一环。原先 industry_profile 算的三样东西（集中度、
生命周期、周期敏感度）全部只需要利润表和资产负债表 —— 之所以「通用」是因为
回避了行业特性: 任何行业都能算 HHI，但 HHI 不告诉你封测的毛利率由什么决定。

产出不是「更多指标」，是一个**可计算的分解式**: 它决定了后面去采集什么数据、
以及人在批准清单上能检查到什么。

两个目标都要给。只给营收不给毛利率会让读者以为两项都分解了 ——
半套分解比不分解更危险。

提示词明确要求先搜索再回答，并把闭合规则写死: 写不出单位的项写不进来。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `sources/web.py` — 开放网络定向采集与 MEDIA 约束

**Files:**
- Create: `ir_agent/sources/web.py`
- Test: `tests/test_web_source.py`

**Interfaces:**
- Consumes: `ir_agent.sources.external` 的 `Corpus`、`Document`、`Tier`
- Produces:
  - `WEB_CAPABLE: bool`（由 Task 3 的探测结果决定）
  - `class WebUnavailable(Exception)`
  - `@dataclass class Finding`: `factor_key: str`, `claim: str`, `url: str`, `publisher: str`, `tier: Tier`, `published: date | None`
  - `def parse_findings(text: str) -> list[Finding]`
  - `def search(client, factor_label: str, factor_key: str, industry: str, as_of: date) -> list[Finding]`
  - `def tier_of(publisher: str) -> Tier`
  - `def supports_value(findings: list[Finding]) -> tuple[bool, str]` — MEDIA 单源不足以支撑取值

- [ ] **Step 1: 写失败的测试**

```python
"""开放网络定向采集。**外部数据的证明力低一个量级，必须在代码里体现。**"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent.sources.external import Tier
from ir_agent.sources.web import (Finding, parse_findings, search,
                                  supports_value, tier_of)

AS_OF = date(2026, 9, 7)

OUT = """发现: utilization | 2026年上半年国内封测厂平均稼动率约78% | https://www.semi.org/x | SEMI | 2026-07-01
发现: utilization | 行业稼动率较去年同期下降5个百分点 | https://finance.example.com/y | 某财经网 | 2026-08-15
"""


class _Client:
    def __init__(self, *r):
        self.replies, self.prompts = list(r), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


def test_解析发现():
    got = parse_findings(OUT)
    assert len(got) == 2
    assert got[0].factor_key == "utilization" and "78%" in got[0].claim
    assert got[0].url.startswith("https://")
    assert got[0].published == date(2026, 7, 1)


def test_缺网址的发现被丢弃():
    """外部依据必须有 URL —— 没有网址就无从复核，与凭空捏造无异。"""
    assert parse_findings("发现: utilization | 稼动率很低 | 无 | 某处 | 2026-01-01") == []


def test_来源分级():
    assert tier_of("SEMI") is Tier.OFFICIAL
    assert tier_of("中国半导体行业协会") is Tier.OFFICIAL
    assert tier_of("中信证券") is Tier.SELLSIDE
    assert tier_of("某财经网") is Tier.MEDIA


def test_单一媒体来源不足以支撑取值():
    """现有分级管的是「能不能引用」；因子取值是要进模型算钱的，门槛更高。
    一条财经媒体报道可以作为线索，不该单独决定 DCF 里的产能增速。"""
    media = [Finding("utilization", "稼动率下降", "https://a/b", "某财经网",
                     Tier.MEDIA, None)]
    ok, why = supports_value(media)
    assert ok is False and "单一媒体" in why


def test_有官方或卖方佐证则可支撑():
    mixed = [Finding("utilization", "约78%", "https://semi/x", "SEMI",
                     Tier.OFFICIAL, None),
             Finding("utilization", "下降5pp", "https://a/b", "某财经网",
                     Tier.MEDIA, None)]
    ok, _ = supports_value(mixed)
    assert ok is True


def test_无发现时明确说无():
    ok, why = supports_value([])
    assert ok is False and "未找到" in why


def test_不可联网时抛出而非静默返回空():
    """静默返回空会让「搜不到」和「没搜」长得一样。"""
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = False
    try:
        with pytest.raises(W.WebUnavailable, match="不能联网"):
            search(_Client(OUT), "稼动率", "utilization", "封测", AS_OF)
    finally:
        W.WEB_CAPABLE = old


def test_提示词要求给出网址与日期():
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = True
    try:
        c = _Client(OUT)
        got = search(c, "稼动率", "utilization", "集成电路封测", AS_OF)
        assert len(got) == 2
        assert "网址" in c.prompts[0] and "发布日期" in c.prompts[0]
        assert "集成电路封测" in c.prompts[0] and "稼动率" in c.prompts[0]
    finally:
        W.WEB_CAPABLE = old


def test_晚于as_of的发现被剔除():
    """拿未来的资料分析过去 —— 与账本的时点守卫是同一件事。"""
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = True
    try:
        late = ("发现: utilization | x | https://a/b | SEMI | 2026-12-01\n"
                "发现: utilization | y | https://a/c | SEMI | 2026-01-01\n")
        got = search(_Client(late), "稼动率", "utilization", "封测", AS_OF)
        assert len(got) == 1 and got[0].claim == "y"
    finally:
        W.WEB_CAPABLE = old
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_web_source.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ir_agent.sources.web'`

- [ ] **Step 3: 写实现**

```python
"""开放网络定向采集 —— 按分解式的因子去找数据。

**外部数据的证明力比账本低一个量级，这件事必须在代码里体现。**
账本数字可以一路反查到接口的原始响应；外部引文只能核验「我确实在某个网址上
抓到过这句话」，核验不了「这句话是对的」。两者在报告里长得一样，所以分级
不是给来源打分，是**限定它能支撑多强的结论**。

因此这里比 external.py 的引用规则更严一层：**MEDIA 级不得单独支撑一个因子的
取值**。引用一句话和进模型算钱，门槛不该一样。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ir_agent.lineparse import keyed
from ir_agent.sources.external import Tier

# 由 Task 3 的实测结果填写。见 docs/superpowers/notes/2026-09-12-web-capability.md
WEB_CAPABLE = True

_LINE = keyed("发现")
_URL = re.compile(r"https?://\S+")

# 来源分级。官方与协会 > 卖方 > 媒体。判不出来的一律按媒体处理 ——
# 往高了猜等于凭空给一个来源加权。
_OFFICIAL = ("SEMI", "统计局", "工信部", "发改委", "海关", "协会", "交易所",
             "Gartner", "TrendForce", "IDC")
_SELLSIDE = ("证券", "研究所", "期货", "investment", "securities")


class WebUnavailable(Exception):
    """联网能力不可用 —— 不静默返回空，那会让「搜不到」和「没搜」长得一样。"""


@dataclass(frozen=True)
class Finding:
    factor_key: str
    claim: str
    url: str
    publisher: str
    tier: Tier
    published: date | None


def tier_of(publisher: str) -> Tier:
    p = publisher or ""
    if any(k.lower() in p.lower() for k in _OFFICIAL):
        return Tier.OFFICIAL
    if any(k.lower() in p.lower() for k in _SELLSIDE):
        return Tier.SELLSIDE
    return Tier.MEDIA


def parse_findings(text: str) -> list[Finding]:
    out: list[Finding] = []
    for hit in _LINE.finditer(text):
        parts = [p.strip() for p in hit.group(1).split("|")]
        if len(parts) < 4:
            continue
        url_m = _URL.search(parts[2])
        if not url_m:
            # 外部依据必须有 URL —— 没有网址就无从复核，与凭空捏造无异。
            continue
        pub = parts[3]
        when = None
        if len(parts) > 4:
            try:
                when = date.fromisoformat(parts[4].strip()[:10])
            except ValueError:
                when = None
        out.append(Finding(factor_key=parts[0], claim=parts[1],
                           url=url_m.group(0), publisher=pub,
                           tier=tier_of(pub), published=when))
    return out


PROMPT = """为一份 A 股研究报告采集一个具体的数据点。

行业：{industry}
要找的因子：**{factor}**

上网搜索，找出关于这个因子的具体数据（水平、同比变化、行业均值皆可）。
每条一行：

`发现: {key} | <具体数据的一句话陈述> | <网址> | <发布机构> | <发布日期 YYYY-MM-DD>`

**硬性要求**
- **必须给出网址**。没有网址的条目会被直接丢弃 —— 无从复核的转述与凭空
  捏造无异。
- **必须给出发布日期**。晚于 {as_of} 的会被剔除：拿未来的资料分析过去，
  与账本的时点守卫是同一件事。
- 数据要具体（「稼动率约 78%」而非「稼动率偏低」）。
- 找不到就一行都不要输出，**不要用推测填充**。

只输出上述格式的行。
"""


def search(client, factor_label: str, factor_key: str, industry: str,
           as_of: date) -> list[Finding]:
    if not WEB_CAPABLE:
        raise WebUnavailable(
            "claude -p 不能联网（见 docs/superpowers/notes/"
            "2026-09-12-web-capability.md），开放网络采集不可用")
    raw = client(PROMPT.format(industry=industry, factor=factor_label,
                               key=factor_key, as_of=as_of.isoformat()))
    got = parse_findings(raw)
    return [f for f in got
            if f.published is None or f.published <= as_of]


def supports_value(findings: list[Finding]) -> tuple[bool, str]:
    """这组发现够不够支撑一个因子的取值。

    **MEDIA 级不得单独支撑。** 现有 external.py 的分级管的是「能不能引用」；
    因子取值是要进模型算钱的，门槛不该一样 —— 一条财经媒体报道可以作为线索，
    不该单独决定 DCF 里的产能增速。
    """
    if not findings:
        return False, "未找到任何数据"
    if any(f.tier in (Tier.OFFICIAL, Tier.SELLSIDE) for f in findings):
        return True, ""
    return False, ("仅单一媒体来源，不足以支撑因子取值 —— "
                   "需官方/协会或卖方研究佐证")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_web_source.py -q`
Expected: `9 passed`

- [ ] **Step 5: 跑全量并提交**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest -q && git add -A && git commit -q -m "开放网络定向采集与 MEDIA 约束

外部数据的证明力比账本低一个量级，这件事必须在代码里体现: 账本数字可以
一路反查到接口原始响应，外部引文只能核验「我确实在某个网址上抓到过这句话」，
核验不了「这句话是对的」。两者在报告里长得一样，所以分级不是给来源打分，
是**限定它能支撑多强的结论**。

因此比 external.py 的引用规则更严一层: **MEDIA 级不得单独支撑因子取值**。
引用一句话和进模型算钱，门槛不该一样。

不可联网时抛异常而非静默返回空 —— 静默返回空会让「搜不到」和「没搜」
长得一样。缺网址的条目直接丢弃；晚于 as_of 的剔除，与账本时点守卫同理。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `gaps.py` — 缺口识别与分级采集编排

**Files:**
- Create: `ir_agent/gaps.py`
- Test: `tests/test_gaps.py`

**Interfaces:**
- Consumes: Task 1 的 `Decomposition`/`Factor`；Task 5 的 `search`/`supports_value`/`Finding`/`WebUnavailable`；`ir_agent.ledger.FactLedger`；`ir_agent.sources.external.Corpus`/`Document`/`Tier`
- Produces:
  - `@dataclass class FactorData`: `factor: Factor`, `status: str`（`"已披露"` / `"外部来源"` / `"无数据"`）, `findings: list[Finding]`, `note: str`
  - `def identify(d: Decomposition, ledger: FactLedger, as_of: date) -> list[Factor]` — 账本里没有的因子
  - `def collect(client, decomps, ledger, corpus, industry, as_of, sections=None) -> dict[str, FactorData]`
  - `def summary(data: dict[str, FactorData]) -> str`

- [ ] **Step 1: 写失败的测试**

```python
"""缺口识别与分级采集。**采集不到就说采集不到，绝不用代理指标顶替。**"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.drivers import Decomposition, Factor, Op
from ir_agent.gaps import FactorData, collect, identify, summary
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.external import Corpus, Tier
from ir_agent.sources.web import Finding

D = Decimal
AS_OF = date(2026, 9, 7)


def _f(key, label, unit="变化率"):
    return Factor(key=key, label=label, unit=unit, source_hint="研报")


def _rev():
    return Decomposition(target="revenue", op=Op.MULTIPLY,
                         factors=[_f("capacity", "产能"),
                                  _f("utilization", "稼动率"),
                                  _f("asp", "单价")])


def _led(*keys):
    led = FactLedger()
    for k in keys:
        led.put(Fact(key=k, value=D("0.1"), unit="ratio", period="2025FY",
                     as_of=date(2026, 4, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


class _Client:
    def __init__(self, *r):
        self.replies, self.prompts = list(r), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0) if self.replies else ""


def test_识别账本里没有的因子():
    gaps = identify(_rev(), _led("capacity"), AS_OF)
    assert [f.key for f in gaps] == ["utilization", "asp"]


def test_账本齐全则无缺口():
    assert identify(_rev(), _led("capacity", "utilization", "asp"), AS_OF) == []


def test_采集不到标为无数据():
    """封测稼动率 A 股基本不披露，这是常态而非异常。"""
    data = collect(_Client("", "", ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["capacity"].status == "已披露"
    assert data["utilization"].status == "无数据"
    assert "估计" in data["utilization"].note


def test_采集到官方来源标为外部来源():
    ok = ("发现: utilization | 平均稼动率约78% | https://semi.org/x "
          "| SEMI | 2026-07-01\n")
    data = collect(_Client(ok, ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["utilization"].status == "外部来源"
    assert data["utilization"].findings[0].tier is Tier.OFFICIAL


def test_仅媒体来源仍算无数据但说明原因():
    """MEDIA 单源不足以支撑取值 —— 但要说清是「找到了但不够格」
    而不是「什么都没找到」。"""
    media = ("发现: utilization | 稼动率下滑 | https://a/b "
             "| 某财经网 | 2026-07-01\n")
    data = collect(_Client(media, ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["utilization"].status == "无数据"
    assert "单一媒体" in data["utilization"].note


def test_采集到的进语料库供核验():
    ok = ("发现: utilization | 平均稼动率约78% | https://semi.org/x "
          "| SEMI | 2026-07-01\n")
    corpus = Corpus(as_of=AS_OF)
    collect(_Client(ok, ""), [_rev()], _led("capacity"), corpus, "封测", AS_OF)
    assert any("78%" in d.text for d in corpus.docs)


def test_不可联网时不中断只标无数据():
    """联网不可用是能力边界，不是故障 —— 整轮研究不该因此停摆。"""
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = False
    try:
        data = collect(_Client(), [_rev()], _led("capacity"),
                       Corpus(as_of=AS_OF), "封测", AS_OF)
        assert data["utilization"].status == "无数据"
        assert "不能联网" in data["utilization"].note
    finally:
        W.WEB_CAPABLE = old


def test_摘要把无数据的因子挑出来():
    """**遗漏必须可见** —— 这正是分解的价值所在。"""
    data = collect(_Client("", "", ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    txt = summary(data)
    assert "稼动率" in txt and "无数据" in txt
    assert "绝不用代理指标" in txt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_gaps.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ir_agent.gaps'`

- [ ] **Step 3: 写实现**

```python
"""按分解式识别数据缺口，并分级采集。

采集顺序先近后远、先可核验后不可核验:

    ① 账本          已有事实
    ② 年报叙述章节   产能/产量的文字披露
    ③ 研报语料库    深度报告里的行业产能测算
    ④ 开放网络      SEMI 出货、基板报价、协会统计

**采集不到就说采集不到。** 封测稼动率 A 股基本不披露 —— 这是常态而非异常。
此时该因子标「无数据，取值为估计」，**绝不用代理指标偷偷顶替**（比如拿固定
资产周转率冒充稼动率）: 那会让一个估计伪装成测量，而读者分辨不出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ir_agent.drivers import Decomposition, Factor
from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.sources.external import Document, Tier
from ir_agent.sources.web import (Finding, WebUnavailable, search,
                                  supports_value)

DISCLOSED = "已披露"
EXTERNAL = "外部来源"
MISSING = "无数据"


@dataclass
class FactorData:
    factor: Factor
    status: str = MISSING
    findings: list[Finding] = field(default_factory=list)
    note: str = ""


def identify(d: Decomposition, ledger: FactLedger, as_of: date) -> list[Factor]:
    """分解式里账本还没有的因子。"""
    out = []
    for f in d.factors:
        try:
            ledger.get(f.key, _latest(ledger, f.key), as_of=as_of)
        except (KeyError, LookAheadError):
            out.append(f)
    return out


def _latest(ledger: FactLedger, key: str) -> str:
    pers = sorted(p for k, p in ledger._facts if k == key)   # noqa: SLF001
    return pers[-1] if pers else ""


def collect(client, decomps: list[Decomposition], ledger: FactLedger,
            corpus, industry: str, as_of: date,
            sections=None) -> dict[str, FactorData]:
    """逐因子采集。已在账本的跳过；缺的走外部搜索。"""
    out: dict[str, FactorData] = {}
    for d in decomps:
        gaps = {f.key for f in identify(d, ledger, as_of)}
        for f in d.factors:
            if f.key in out:
                continue
            if f.key not in gaps:
                out[f.key] = FactorData(factor=f, status=DISCLOSED)
                continue

            try:
                found = search(client, f.label, f.key, industry, as_of)
            except WebUnavailable as e:
                # 能力边界不是故障，整轮研究不该因此停摆。
                out[f.key] = FactorData(
                    factor=f, status=MISSING,
                    note=f"{e} —— 取值为估计，无数据支撑")
                continue

            ok, why = supports_value(found)
            if ok:
                for x in found:
                    corpus.add_if_visible(Document(
                        doc_id=f"web_{f.key}_{len(corpus.docs)}", url=x.url,
                        title=f"{f.label}：{x.claim[:30]}",
                        publisher=x.publisher, tier=x.tier,
                        published=x.published or as_of, retrieved=as_of,
                        text=x.claim))
                out[f.key] = FactorData(factor=f, status=EXTERNAL,
                                        findings=found)
            else:
                out[f.key] = FactorData(
                    factor=f, status=MISSING, findings=found,
                    note=f"{why} —— 取值为估计")
    return out


def summary(data: dict[str, FactorData]) -> str:
    """给人看的采集结果。**遗漏必须可见** —— 这正是分解的价值所在。"""
    lines = ["因子数据采集结果", ""]
    lines.append("| 因子 | 状态 | 来源 / 说明 |")
    lines.append("| --- | --- | --- |")
    for fd in data.values():
        if fd.status == EXTERNAL:
            src = "；".join(f"{x.publisher}（{x.url}）" for x in fd.findings[:2])
        else:
            src = fd.note or "—"
        lines.append(f"| {fd.factor.label} | {fd.status} | {src} |")
    gaps = [fd.factor.label for fd in data.values() if fd.status == MISSING]
    if gaps:
        lines += ["", f"⚠ 以下因子无数据支撑，取值为估计：{'、'.join(gaps)}。",
                  "**绝不用代理指标顶替** —— 拿固定资产周转率冒充稼动率会让"
                  "一个估计伪装成测量，而读者分辨不出来。"]
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_gaps.py -q`
Expected: `8 passed`

- [ ] **Step 5: 跑全量并提交**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest -q && git add -A && git commit -q -m "按分解式识别数据缺口并分级采集

采集顺序先近后远、先可核验后不可核验: 账本 → 年报 → 研报语料库 → 开放网络。

**采集不到就说采集不到。** 封测稼动率 A 股基本不披露 —— 这是常态而非异常。
此时标「无数据，取值为估计」，**绝不用代理指标偷偷顶替**: 拿固定资产周转率
冒充稼动率会让一个估计伪装成测量，而读者分辨不出来。

「找到了但不够格」（仅单一媒体来源）与「什么都没找到」要分开说，
两者对读者的含义不同。

不可联网时不中断，只把相关因子标为无数据 —— 能力边界不是故障，
整轮研究不该因此停摆。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `assume.py` 改造 — 判断项按因子填写

**Files:**
- Modify: `ir_agent/assume.py`
- Test: `tests/test_assume.py`（追加）

**Interfaces:**
- Consumes: Task 1/2 的 `Decomposition`/`compose`/`pp_hint`；Task 6 的 `FactorData`
- Produces（在既有基础上新增，**不删除既有的两标量路径**，它是降级模式）:
  - `Row` 新增字段 `factor_key: str = ""`（空表示旧的两标量模式）
  - `AssumptionSet` 新增字段 `decomps: list[Decomposition] = field(default_factory=list)`、`factor_data: dict = field(default_factory=dict)`、`residuals: dict[str, list] = field(default_factory=dict)`
  - `def synthesize(a: AssumptionSet, case: Case, base_margin: Decimal) -> tuple[list[Decimal], list[Decimal]]` — 返回 (逐年营收增速, 逐年毛利率)
  - `build_prompt` / `decide` 新增参数 `decomps=None`、`factor_data=None`

- [ ] **Step 1: 写失败的测试**

```python
# 追加到 tests/test_assume.py

from ir_agent.drivers import Decomposition, Factor, Op


def _decomps():
    rev = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="capacity", label="产能", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])
    gm = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util_leverage", label="稼动率摊薄", unit="pp"),
        Factor(key="input_cost", label="上游成本", unit="pp")])
    return [rev, gm]


def _factor_lines(years=1):
    out = []
    for y in range(1, years + 1):
        out += [
            f"假设: 产能 | 第{y}年 | 5% | 10% | 14% | 新厂投产 「{Q}」",
            f"假设: 单价 | 第{y}年 | -6% | -2% | 1% | 竞争压价 「{Q}」",
            f"假设: 稼动率摊薄 | 第{y}年 | -2.5 | -1.0 | 0.5 | 扩产摊薄 「{Q}」",
            f"假设: 上游成本 | 第{y}年 | -1.5 | -0.5 | 0.3 | 基板涨价 「{Q}」"]
    return "\n".join(out)


def test_按因子解析():
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    assert {r.factor_key for r in a.rows} == {
        "capacity", "asp", "util_leverage", "input_cost"}


def test_未知因子被驳回并列出可选():
    bad = f"假设: 品牌力 | 第1年 | 1% | 2% | 3% | 「{Q}」"
    with pytest.raises(AssumeFailed) as e:
        assume.parse(bad, 1, decomps=_decomps())
    assert "产能" in str(e.value) and "单价" in str(e.value)


def test_缺某个因子被驳回():
    """漏掉的因子会沿用模板默认值，而那不是你的判断。"""
    partial = f"假设: 产能 | 第1年 | 5% | 10% | 14% | 「{Q}」"
    a = assume.parse(partial, 1, decomps=_decomps())
    with pytest.raises(AssumeFailed, match="单价|缺"):
        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
                     decomps=_decomps())


def test_合成营收增速与毛利率():
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    g, m = assume.synthesize(a, Case.BASE, base_margin=D("0.1415"))
    # 1.10 × 0.98 − 1 = 0.078
    assert abs(g[0] - D("0.078")) < D("1e-9")
    # 14.15% − 1.0pp − 0.5pp = 12.65%
    assert abs(m[0] - D("0.1265")) < D("1e-9")


def test_三档合成后仍然单调():
    """因子各自拉开不等于合成后拉开 —— 必须在合成结果上再验一次。"""
    a = assume.parse(_factor_lines(1), 1, decomps=_decomps())
    a.decomps = _decomps()
    lo, _ = assume.synthesize(a, Case.BEAR, base_margin=D("0.1415"))
    mid, _ = assume.synthesize(a, Case.BASE, base_margin=D("0.1415"))
    hi, _ = assume.synthesize(a, Case.BULL, base_margin=D("0.1415"))
    assert lo[0] < mid[0] < hi[0]


def test_合成后不单调则驳回():
    """构造一组因子: 各自三档拉开，但相乘后悲观反而高于中性。"""
    weird = "\n".join([
        f"假设: 产能 | 第1年 | 50% | 10% | 11% | 「{Q}」",   # 悲观档产能暴涨
        f"假设: 单价 | 第1年 | -1% | -2% | 1% | 「{Q}」"])
    d = [Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="capacity", label="产能", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])]
    a = assume.parse(weird, 1, decomps=d)
    a.decomps = d
    with pytest.raises(AssumeFailed, match="合成后"):
        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
                     decomps=d, base_margin=D("0.14"))


def test_提示词列出因子与单位():
    txt = assume.build_prompt({"g_stats": (), "gm_stats": ()}, ["2026E"],
                              "摘要", brief="简报", decomps=_decomps())
    assert "产能" in txt and "变化率" in txt
    assert "稼动率摊薄" in txt and "pp" in txt
    assert "pp 不能进乘法链" in txt


def test_无分解式时退回两标量模式():
    """降级路径必须还能走 —— 预研失败不该让整轮研究停摆。"""
    a = assume.parse(_full(1), 1)
    assert {r.driver for r in a.rows} == {"growth", "gm"}
    _ok(a, 1)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_assume.py -q`
Expected: FAIL — `TypeError: parse() got an unexpected keyword argument 'decomps'`

- [ ] **Step 3: 改造实现**

在 `ir_agent/assume.py` 做以下改动：

3a. `Row` 增加字段（紧跟 `note: str = ""` 之后）：

```python
    # 分解模式下这一行对应哪个因子。空串表示旧的两标量模式 ——
    # 预研失败时的降级路径，必须保留。
    factor_key: str = ""
```

3b. `AssumptionSet` 增加字段（在 `rows` 之后）：

```python
    decomps: list = field(default_factory=list)
    factor_data: dict = field(default_factory=dict)
    residuals: dict = field(default_factory=dict)
```

3c. `parse` 增加 `decomps` 参数并分派：

```python
def parse(text: str, years: int, decomps: list | None = None) -> AssumptionSet:
    """decomps 给定时按因子解析；否则沿用两标量模式（降级路径）。"""
    if not decomps:
        return _parse_scalar(text, years)
    return _parse_factors(text, years, decomps)
```

把原 `parse` 的函数体改名为 `_parse_scalar`，并新增：

```python
def _parse_factors(text: str, years: int, decomps: list) -> AssumptionSet:
    by_label = {f.label: f for d in decomps for f in d.factors}
    target_of = {f.key: d.target for d in decomps for f in d.factors}
    out = AssumptionSet(code="", labels=[], decomps=list(decomps))
    for m in _LINE.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) < 6:
            raise AssumeFailed(
                f"假设一行字段不足（需 6 段：因子|年|悲观|中性|乐观|依据），"
                f"得到 {len(parts)}：{m.group(1)}")
        label = pick(parts[0], list(by_label))
        if label is None:
            raise AssumeFailed(
                f"未知的因子 {parts[0]!r}，本次分解式里只有："
                + "、".join(by_label))
        f = by_label[label]
        try:
            year = int(re.search(r"\d+", parts[1]).group(0))
        except (AttributeError, ValueError) as e:
            raise AssumeFailed(f"年份不可解析：{parts[1]!r}") from e
        if not 1 <= year <= years:
            raise AssumeFailed(f"年份 {year} 超出预测期 1..{years}")
        out.rows.append(Row(
            driver=target_of[f.key], year=year,
            bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
            bases=_basis(parts[5], label), note=_note(parts[5]),
            factor_key=f.key))
    return out
```

3d. 新增 `synthesize`：

```python
def synthesize(a: AssumptionSet, case: Case,
               base_margin: Decimal) -> tuple[list[Decimal], list[Decimal]]:
    """把因子合成回逐年营收增速与毛利率。

    **合成后要再验一次单调。** 因子各自三档拉开，不等于合成结果拉开 ——
    产能悲观档偏高、单价悲观档偏低，相乘后完全可能悲观高于中性。
    """
    from ir_agent.drivers import compose

    attr = {Case.BULL: "bull", Case.BASE: "base", Case.BEAR: "bear"}[case]
    years = max((r.year for r in a.rows), default=0)
    growth, margin = [], []
    for y in range(1, years + 1):
        vals = {r.factor_key: getattr(r, attr)
                for r in a.rows if r.year == y and r.factor_key}
        for d in a.decomps:
            picked = {k: v for k, v in vals.items()
                      if k in {f.key for f in d.factors}}
            if d.target == "revenue":
                growth.append(compose(d, picked))
            else:
                margin.append(compose(d, picked, base=base_margin))
    return growth, margin
```

3e. `check` 增加分解模式的校验（在既有 `for cn, key in DRIVERS.items()` 之前分派）：

```python
def check(a: AssumptionSet, years: int, *, ledger: FactLedger, body: str,
          as_of: date, decomps: list | None = None,
          base_margin: Decimal | None = None) -> None:
    if decomps:
        _check_factors(a, years, decomps, base_margin)
    else:
        _check_scalar(a, years)
    flat = [b for r in a.rows for b in r.bases]
    problems = validate(flat, ledger=ledger, body=body, as_of=as_of)
    if problems:
        raise AssumeFailed("依据不可核验：" + "；".join(problems))
```

把既有 `check` 里「每项每年都要给 + 三档拉开」那两段挪进 `_check_scalar`，
并新增：

```python
def _check_factors(a: AssumptionSet, years: int, decomps: list,
                   base_margin: Decimal | None) -> None:
    for d in decomps:
        for f in d.factors:
            got = {r.year for r in a.rows if r.factor_key == f.key}
            missing = sorted(set(range(1, years + 1)) - got)
            if missing:
                raise AssumeFailed(
                    f"因子「{f.label}」缺第 {missing} 年 —— 预测期每一年都要给，"
                    "漏掉的年份会沿用模板默认值，而那不是你的判断")
    for r in a.rows:
        if not (r.bear < r.base < r.bull):
            lbl = next((f.label for d in decomps for f in d.factors
                        if f.key == r.factor_key), r.factor_key)
            raise AssumeFailed(
                f"因子「{lbl}」第 {r.year} 年三档未拉开"
                f"（悲观 {r.bear} / 中性 {r.base} / 乐观 {r.bull}）")

    # **合成后再验一次。** 因子各自拉开不等于合成结果拉开。
    if base_margin is None:
        base_margin = Decimal("0.10")
    a.decomps = list(decomps)
    lo, lom = synthesize(a, Case.BEAR, base_margin)
    mid, midm = synthesize(a, Case.BASE, base_margin)
    hi, him = synthesize(a, Case.BULL, base_margin)
    for i in range(len(mid)):
        if not (lo[i] < mid[i] < hi[i]):
            raise AssumeFailed(
                f"第 {i + 1} 年的营收增速**合成后**不再单调"
                f"（悲观 {lo[i]:.2%} / 中性 {mid[i]:.2%} / 乐观 {hi[i]:.2%}）"
                " —— 因子各自拉开不等于合成结果拉开，请调整因子取值")
    for i in range(len(midm)):
        if not (lom[i] < midm[i] < him[i]):
            raise AssumeFailed(
                f"第 {i + 1} 年的毛利率**合成后**不再单调")
```

3f. `build_prompt` 增加 `decomps` 参数，分解模式下替换掉「你只填这两项」那段：

```python
def build_prompt(judg: dict, labels: list[str], context: str,
                 brief: str = "", decomps: list | None = None,
                 factor_data: dict | None = None) -> str:
    ...（既有 hist 计算不变）
    if decomps:
        head = _factor_head(decomps, factor_data or {})
    else:
        head = _SCALAR_HEAD
    return PROMPT.format(head=head, years=len(labels), ...)
```

并新增：

```python
_SCALAR_HEAD = """**你只填这两项**：逐年营收增速、逐年毛利率。
费用率、周转天数、折旧率、税率、β、债务成本等由历史财务数据校准而来，
已经算好，不需要也不应该由你改动。"""


def _factor_head(decomps: list, factor_data: dict) -> str:
    lines = ["**本次按驱动因子分解填写。** 分解式："]
    for d in decomps:
        lines.append(f"  {d.describe()}")
    lines += ["", "逐个因子填，代码会把它们合成回营收增速与毛利率。",
              "", "| 因子 | 单位 | 数据 |", "| --- | --- | --- |"]
    for d in decomps:
        for f in d.factors:
            fd = factor_data.get(f.key)
            st = fd.status if fd else "未采集"
            lines.append(f"| {f.label} | {f.unit} | {st} |")
    lines += ["",
              "**pp 不能进乘法链**：营收链的因子一律填变化率（含稼动率）——"
              "稼动率 85%→82% 是 −3pp，但变化率是 −3.5%。",
              "毛利率链的因子一律填 pp。",
              "标「无数据」的因子取值是**估计**，理由里要说明依据什么估的。"]
    return "\n".join(lines)
```

`PROMPT` 里把原先硬编码的那两行替换为 `{head}`。

3g. `decide` 透传：

```python
def decide(client, judg: dict, labels: list[str], context: str, *,
           ledger: FactLedger, body: str, as_of: date, code: str = "",
           max_attempts: int = 3, decomps: list | None = None,
           factor_data: dict | None = None,
           base_margin: Decimal | None = None) -> AssumptionSet:
    prompt = build_prompt(judg, labels, context, brief=body, decomps=decomps,
                          factor_data=factor_data)
    last, raw = "", ""
    for attempt in range(1, max_attempts + 1):
        raw = client(prompt)
        try:
            a = parse(raw, len(labels), decomps=decomps)
            check(a, len(labels), ledger=ledger, body=body, as_of=as_of,
                  decomps=decomps, base_margin=base_margin)
        except AssumeFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新输出全部行。\n")
            continue
        a.code, a.labels = code, labels
        a.decomps = list(decomps or [])
        a.factor_data = dict(factor_data or {})
        return a
    raise AssumeFailed(
        f"连续 {max_attempts} 次给不出可核验的假设：{last}"
        f"\n最后一次的原始输出（末 400 字）：\n{raw.strip()[-400:]}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_assume.py -q`
Expected: 既有 23 项 + 新增 8 项全绿

- [ ] **Step 5: 跑全量并提交**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest -q && git add -A && git commit -q -m "判断项改为按驱动因子填写，代码合成回增速与毛利率

现在人核实的是两条标量路径 —— 就算 AI 在理由里写了「考虑产能和稼动率」，
**核实的人看到的仍然只是 4%**，他没法判断这 4% 里多少来自产能、多少来自
价格。改造后人核实的是分解，遗漏因此可见: 清单上没有「稼动率」这一行，
一眼就知道这个因素没被纳入。

**合成后要再验一次单调。** 因子各自三档拉开不等于合成结果拉开 —— 产能悲观
档偏高、单价悲观档偏低，相乘后完全可能悲观高于中性，而那是个看起来正常的
错数。

两标量路径保留为降级模式: 预研失败不该让整轮研究停摆。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: `ashare_verify` 支持跨表引用

**Files:**
- Modify: `/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf/ashare_verify.py`
- Test: `/Users/ariasleo/Desktop/谢海量作品集/ir-agent/tests/test_crosssheet_ref.py`

**Interfaces:**
- Consumes: 无
- Produces: `read_inputs` 能解析 `=1b_驱动分解!B5` 形式的跨表引用

**背景（实现者必读）：** `read_inputs` 目前读三档取值是
`float(a.cell(r, 1 + scen).value)` —— 直接取值不解析公式；`g()` 的引用正则
`=\$?([A-Z]+)\$?(\d+)` 只认本表引用。Task 9 会让 `1_假设` 的增速/毛利率单元格
指向新表，**不改这里会让独立复算验证当场断掉**（`float()` 拿到公式字符串）。

- [ ] **Step 1: 写失败的测试**

```python
"""ashare_verify 的跨表引用解析。

**这是验证核心。** read_inputs 是「独立复算」的读取器 —— 它读错了，
整个「公式引擎实跑验算」的担保就失效了。因此改动虽小，测试要密。
"""

from __future__ import annotations

import sys

import pytest
from openpyxl import Workbook

sys.path.insert(0, "/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf")


def _wb(tmp_path, value_cell, ref):
    """造一个两表工作簿：assumptions 表引用 drivers 表。"""
    wb = Workbook()
    a = wb.active
    a.title = "1_假设"
    a["A1"] = "测试项"
    a["B1"] = ref
    d = wb.create_sheet("1b_驱动分解")
    d[value_cell] = 0.1234
    p = tmp_path / "x.xlsx"
    wb.save(p)
    return p


def test_解析跨表引用():
    from ashare_verify import _resolve_ref
    assert _resolve_ref("=1b_驱动分解!B5") == ("1b_驱动分解", "B5")
    assert _resolve_ref("='1b_驱动分解'!$B$5") == ("1b_驱动分解", "B5")


def test_本表引用仍然认得():
    """原有能力不能丢。"""
    from ashare_verify import _resolve_ref
    assert _resolve_ref("=B48") == (None, "B48")
    assert _resolve_ref("=$B$48") == (None, "B48")


def test_非引用返回空():
    from ashare_verify import _resolve_ref
    assert _resolve_ref("=B1+C1") is None
    assert _resolve_ref(0.12) is None
    assert _resolve_ref("普通文字") is None


def test_读取跨表引用的值(tmp_path):
    from openpyxl import load_workbook

    from ashare_verify import _cell_value
    p = _wb(tmp_path, "B5", "=1b_驱动分解!B5")
    wb = load_workbook(p, data_only=False)
    assert abs(_cell_value(wb, wb["1_假设"], "B1") - 0.1234) < 1e-12


def test_多层引用有上限(tmp_path):
    """防止循环引用把读取器转死。"""
    from openpyxl import load_workbook

    from ashare_verify import _cell_value
    wb = Workbook()
    a = wb.active
    a.title = "1_假设"
    a["B1"] = "=B2"
    a["B2"] = "=B1"          # 循环
    p = tmp_path / "loop.xlsx"
    wb.save(p)
    wb2 = load_workbook(p, data_only=False)
    with pytest.raises(ValueError, match="引用层数"):
        _cell_value(wb2, wb2["1_假设"], "B1")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_crosssheet_ref.py -q`
Expected: FAIL — `ImportError: cannot import name '_resolve_ref' from 'ashare_verify'`

- [ ] **Step 3: 在 ashare_verify.py 里实现**

在 `read_inputs` 之前插入：

```python
_REF_RE = re.compile(
    r"^=\s*(?:'([^']+)'|([^!'\s]+))?!?\$?([A-Z]+)\$?(\d+)\s*$")
_SELF_REF_RE = re.compile(r"^=\s*\$?([A-Z]+)\$?(\d+)\s*$")
_MAX_REF_DEPTH = 5


def _resolve_ref(v):
    """把单元格里的引用式拆成 (表名 或 None, 地址)。不是引用则返回 None。

    此前只认本表引用（=B48）。接入驱动因子分解后，「1_假设」的增速与毛利率
    单元格要指向「1b_驱动分解」，不支持跨表就会让 read_inputs 拿到公式字符串
    然后 float() 抛异常 —— **独立复算验证会整个断掉**。
    """
    if not isinstance(v, str):
        return None
    m = _SELF_REF_RE.match(v.strip())
    if m:
        return None, f"{m.group(1)}{m.group(2)}"
    m = _REF_RE.match(v.strip())
    if not m:
        return None
    sheet = m.group(1) or m.group(2)
    if not sheet:
        return None
    return sheet, f"{m.group(3)}{m.group(4)}"


def _cell_value(wb, sheet, addr, _depth=0):
    """读一个单元格的值，逐层解析引用（含跨表）。"""
    if _depth > _MAX_REF_DEPTH:
        raise ValueError(
            f"引用层数超过 {_MAX_REF_DEPTH} —— 可能存在循环引用")
    v = sheet[addr].value
    ref = _resolve_ref(v)
    if ref is None:
        return v
    name, target = ref
    nxt = wb[name] if name else sheet
    return _cell_value(wb, nxt, target, _depth + 1)
```

把 `read_inputs` 里的 `g()` 改为：

```python
    def g(p, _depth=0):
        """读假设值；引用（含跨表）逐层解析，避免读回公式字符串。"""
        return _cell_value(wb, a, f"B{_find(am, p)}")
```

把 `path3` 改为：

```python
    def path3(prefix, n):
        out = []
        for i in range(n):
            r = _find(am, f"{hdr[nh+i]} {prefix}")
            col = chr(ord('A') + scen)      # scen=1→B, 2→C, 3→D
            out.append(float(_cell_value(wb, a, f"{col}{r}")))
        return out
```

（注意 `read_inputs` 开头需保留 `wb = load_workbook(path, data_only=False)`
并在函数内可见。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_crosssheet_ref.py -q`
Expected: `5 passed`

- [ ] **Step 5: 用真实工作簿回归验证（关键）**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && PYTHONPATH="$PWD" python -c "
import sys; sys.path.insert(0,'/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf')
import ashare_verify as V
fn='/tmp/cj/p1/600584_长电科技_DCF_20260912.xlsx'
fails, dv = V.verify(fn, quiet=True)
print('验算失败数:', len(fails))
print('每股内在价值:', round(dv['ps'], 6))
assert not fails, '改动打断了既有验算'
assert abs(dv['ps'] - 16.95) < 0.5, '改动改变了既有结果'
print('✓ 既有工作簿验算不受影响')
" 2>&1 | grep -v "it/s\]" | tail -5
```

Expected: `验算失败数: 0` 且 `✓ 既有工作簿验算不受影响`

若该文件不存在，先跑一次第二阶段生成：
`PYTHONPATH="$PWD" python -m ir_agent 600584 --year 2025 --as-of 2026-09-07 --value /tmp/cj/p1 --approved-by test`

- [ ] **Step 6: 提交（两个仓库）**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" && git add -A && git commit -q -m "read_inputs 支持跨表引用

ir-agent 接入驱动因子分解后，「1_假设」的增速与毛利率单元格要指向
「1b_驱动分解」。此前 g() 的引用正则只认本表（=B48），而 path3 更是直接
float(cell.value) 不解析公式 —— 跨表引用会让它拿到公式字符串然后抛异常，
**独立复算验证整个断掉**。

抽出 _resolve_ref / _cell_value 两个函数，本表与跨表走同一套解析，
并设引用层数上限防循环引用转死。

这是验证核心的改动，已用长电既有工作簿回归: 验算失败 0 项、每股价值不变。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" && cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && git add -A && git commit -q -m "ashare_verify 跨表引用的测试

它是独立复算的读取器 —— 读错了整个「公式引擎实跑验算」的担保就失效，
因此改动虽小测试要密: 跨表、本表、非引用、循环引用各一条，
外加真实工作簿回归。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: `ashare_build` 新增 `1b_驱动分解` 工作表

**Files:**
- Modify: `/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf/ashare_build.py`
- Test: `/Users/ariasleo/Desktop/谢海量作品集/ir-agent/tests/test_driver_sheet.py`

**Interfaces:**
- Consumes: `cal["judg"]` 新增可选键 `drivers`，形如
  `{"revenue": {"op": "乘", "factors": [{"key","label","unit"}], "values": {"bull": [[y1_f1, y1_f2], …], "base": […], "bear": […]}}, "gross_margin": {…, "base_level": 0.1415}}`
- Produces: 工作簿新增 `1b_驱动分解` 表；`1_假设` 的营收增速/毛利率单元格改为
  跨表公式引用（仅在 `drivers` 存在时）

- [ ] **Step 1: 写失败的测试**

```python
"""1b_驱动分解 工作表。"""

from __future__ import annotations

import json
import pickle
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, "/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf")

# 建表需要一份真实的 calibrate 输出 —— 它有六十多个键，手写夹具既繁琐又会
# 与真实结构漂移（本项目已因「固件编码我想象的结构」栽过一次: stmts 被写成
# 字典嵌套，实跑才发现是 DataFrame）。因此**取一次真的缓存起来**。
_CACHE = Path("/tmp/cj/cal_600584.pkl")


def _minimal_cal():
    if _CACHE.exists():
        with _CACHE.open("rb") as f:
            return pickle.loads(f.read())
    try:
        from ir_agent import dcf
        from ir_agent.pipeline import run
        r = run("600584", 2025, date(2026, 9, 7))
        cal = dcf.calibrate_only("600584", as_of=date(2026, 9, 7),
                                 ledger=r.ledger)
    except Exception as e:                                  # noqa: BLE001
        pytest.skip(f"取不到真实 calibrate 输出（{e.__class__.__name__}），"
                    "跳过建表测试")
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE.open("wb") as f:
        f.write(pickle.dumps(cal))
    return cal


def _drivers():
    return {
        "revenue": {
            "op": "乘",
            "factors": [{"key": "capacity", "label": "产能", "unit": "变化率"},
                        {"key": "asp", "label": "单价", "unit": "变化率"}],
            "values": {"bull": [[0.14, 0.01]], "base": [[0.10, -0.02]],
                       "bear": [[0.05, -0.06]]}},
        "gross_margin": {
            "op": "加",
            "base_level": 0.1415,
            "factors": [{"key": "util", "label": "稼动率摊薄", "unit": "pp"},
                        {"key": "cost", "label": "上游成本", "unit": "pp"}],
            "values": {"bull": [[0.5, 0.3]], "base": [[-1.0, -0.5]],
                       "bear": [[-2.5, -1.5]]}}}


def test_无drivers时不建表(tmp_path):
    """降级路径: 预研失败时工作簿形状与从前完全一致。"""
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    p = tmp_path / "a.xlsx"
    B.build(cal, str(p))
    assert "1b_驱动分解" not in load_workbook(p).sheetnames


def test_有drivers时建表并写入三档(tmp_path):
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    cal["judg"]["drivers"] = _drivers()
    p = tmp_path / "b.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1b_驱动分解"]
    cells = {ws.cell(r, 1).value: r for r in range(1, 40)
             if ws.cell(r, 1).value}
    row = cells["2026E 产能"]
    # B=Bull C=Base D=Bear —— 与 CHOOSE($B$SCEN,…) 绑定
    assert abs(ws.cell(row, 2).value - 0.14) < 1e-12
    assert abs(ws.cell(row, 3).value - 0.10) < 1e-12
    assert abs(ws.cell(row, 4).value - 0.05) < 1e-12


def test_合成行是公式不是常量(tmp_path):
    """**Excel 独立算一遍**，与 Python 侧合成对照 —— 两处独立算出同一个值
    再比对，比共享一个值更能发现错误。"""
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    cal["judg"]["drivers"] = _drivers()
    p = tmp_path / "c.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1b_驱动分解"]
    cells = {ws.cell(r, 1).value: r for r in range(1, 40)
             if ws.cell(r, 1).value}
    v = ws.cell(cells["2026E 营收增速（合成）"], 2).value
    assert isinstance(v, str) and v.startswith("=")
    assert "1+" in v or "(1" in v


def test_假设表改为跨表引用(tmp_path):
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    cal["judg"]["drivers"] = _drivers()
    p = tmp_path / "d.xlsx"
    B.build(cal, str(p))
    ws = load_workbook(p)["1_假设"]
    cells = {ws.cell(r, 1).value: r for r in range(1, 60)
             if ws.cell(r, 1).value}
    v = ws.cell(cells["2026E 营收增速"], 2).value
    assert isinstance(v, str) and "1b_驱动分解" in v


def test_列序与假设表一致(tmp_path):
    """写反不会让验算失败 —— 公式本身没错，只是「选悲观」会选出乐观值。"""
    from openpyxl import load_workbook
    import ashare_build as B
    cal = _minimal_cal()
    cal["judg"]["drivers"] = _drivers()
    p = tmp_path / "e.xlsx"
    B.build(cal, str(p))
    wb = load_workbook(p)
    d = wb["1b_驱动分解"]
    hdr = [d.cell(r, c).value for r in range(1, 10) for c in (2, 3, 4)]
    assert "Bull" in str(hdr) and "Base" in str(hdr) and "Bear" in str(hdr)
    assert str(hdr).index("Bull") < str(hdr).index("Base") < str(hdr).index("Bear")
```

（`_minimal_cal()` 由实现者从既有 `ashare-dcf/tests` 或
`ir_agent/dcf.calibrate_only` 的真实输出裁剪而来；若无现成夹具，
用 `calibrate_only("600584", as_of=date(2026,9,7))` 取一次并缓存为 JSON。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_driver_sheet.py -q`
Expected: FAIL — `KeyError: '1b_驱动分解'`

- [ ] **Step 3: 在 ashare_build.py 里实现**

在 `build()` 内、写完 `1_假设` 之后插入建表逻辑：

```python
    DRV = JU.get("drivers")
    if DRV:
        _build_driver_sheet(wb, DRV, YE, ws, G_ROW, M_ROW)
```

并新增模块级函数：

```python
_SCEN_COLS = ("B", "C", "D")          # Bull / Base / Bear —— 与 1_假设 一致


def _build_driver_sheet(wb, drv, YE, assum_ws, g_row, m_row):
    """建「1b_驱动分解」，并把 1_假设 的增速/毛利率改为跨表引用。

    **列序必须与 1_假设 一致（B=Bull C=Base D=Bear）**，它与
    CHOOSE($B$SCEN,…) 绑定。写反不会让验算失败 —— 公式本身没错，
    只是「选悲观」会选出乐观值。
    """
    ds = wb.create_sheet("1b_驱动分解", 2)
    r = 1
    ds.cell(r, 1, "驱动因子分解").font = Font(bold=True, size=12); r += 2
    for i, t in enumerate(["因子 / 年", "Bull", "Base", "Bear", "单位"]):
        c = ds.cell(r, i + 1, t); c.font = F_COL; c.fill = P_B
    r += 1

    rows: dict[str, dict[str, int]] = {}
    for target in ("revenue", "gross_margin"):
        block = drv.get(target)
        if not block:
            continue
        rows[target] = {}
        for yi, y in enumerate(YE):
            for fi, f in enumerate(block["factors"]):
                ds.cell(r, 1, f"{y} {f['label']}").font = Font(size=10)
                for ci, case in enumerate(("bull", "base", "bear")):
                    cc = ds.cell(r, 2 + ci, block["values"][case][yi][fi])
                    cc.font = F_IN
                    cc.number_format = PCT if f["unit"] == "变化率" else "0.00"
                    cc.fill = P_Y
                ds.cell(r, 5, f["unit"]).font = Font(size=9, color="808080")
                rows[target][f"{yi}:{fi}"] = r
                r += 1

            label = "营收增速（合成）" if target == "revenue" else "毛利率（合成）"
            ds.cell(r, 1, f"{y} {label}").font = Font(size=10, bold=True)
            for ci, col in enumerate(_SCEN_COLS):
                refs = [f"{col}{rows[target][f'{yi}:{fi}']}"
                        for fi in range(len(block["factors"]))]
                if block["op"] == "乘":
                    expr = "*".join(f"(1+{x})" for x in refs) + "-1"
                else:
                    base = block.get("base_level", 0.0)
                    expr = f"{base}+(" + "+".join(refs) + ")/100"
                cc = ds.cell(r, 2 + ci, f"={expr}")
                cc.font = F_FX; cc.number_format = PCT
            rows[target][f"synth:{yi}"] = r
            r += 1
        r += 1

    # 1_假设 的判断项改为引用本表
    for target, start in (("revenue", g_row), ("gross_margin", m_row)):
        if target not in rows:
            continue
        for yi in range(len(YE)):
            src = rows[target][f"synth:{yi}"]
            for ci, col in enumerate(_SCEN_COLS):
                assum_ws.cell(start + yi, 2 + ci,
                              f"='1b_驱动分解'!{col}{src}").font = F_FX
```

（`G_ROW` / `M_ROW` 在既有 `build()` 中已定义，指向「营收增速」与「毛利率」
块的首行；需确保它们在调用点可见。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_driver_sheet.py -q`
Expected: `5 passed`

- [ ] **Step 5: 端到端验算（关键）**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && PYTHONPATH="$PWD" python -c "
import sys, json; sys.path.insert(0,'/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf')
from datetime import date
from ir_agent.pipeline import run
from ir_agent import dcf
import ashare_build as B, ashare_verify as V
AS=date(2026,9,7)
r=run('600584',2025,AS)
cal=dcf.calibrate_only('600584', as_of=AS, ledger=r.ledger)
ny=len(cal['periods']['proj'])
cal['judg']['drivers']={
 'revenue':{'op':'乘','factors':[{'key':'capacity','label':'产能','unit':'变化率'},
                                 {'key':'asp','label':'单价','unit':'变化率'}],
   'values':{'bull':[[0.14,0.01]]*ny,'base':[[0.10,-0.02]]*ny,'bear':[[0.05,-0.06]]*ny}},
 'gross_margin':{'op':'加','base_level':0.1415,
   'factors':[{'key':'util','label':'稼动率摊薄','unit':'pp'},
              {'key':'cost','label':'上游成本','unit':'pp'}],
   'values':{'bull':[[0.5,0.3]]*ny,'base':[[-1.0,-0.5]]*ny,'bear':[[-2.5,-1.5]]*ny}}}
fn='/tmp/cj/driver_test.xlsx'
B.build(cal, fn)
fails, dv = V.verify(fn, quiet=True)
print('验算失败数:', len(fails))
for f in fails[:5]: print('  ', f)
assert not fails, '带驱动分解表的工作簿验算未通过'
print('每股内在价值:', round(dv['ps'],4))
print('✓ 跨表引用 + 公式引擎实跑验算通过')
" 2>&1 | grep -v "it/s\]" | tail -8
```

Expected: `验算失败数: 0` 且 `✓ 跨表引用 + 公式引擎实跑验算通过`

- [ ] **Step 6: 提交（两个仓库）**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" && git add -A && git commit -q -m "新增 1b_驱动分解 工作表，1_假设 改为跨表引用

驱动因子分解若只出现在批准清单里，它在工作簿里就只是一张说明图 ——
人还是得回头改 1_假设 的常量，分解就白做了。现在改一个稼动率，
营收 → 利润 → FCFF → 每股价值整条链自动重算。

合成行写成 **Excel 公式**而非常量: Python 侧算一遍、Excel 公式算一遍，
两处独立算出同一个值再比对，比共享一个值更能发现错误 —— 这也是
ashare_verify 本身的哲学。

列序 B=Bull C=Base D=Bear 与 CHOOSE(\$B\$SCEN,…) 绑定。写反不会让验算失败
（公式本身没错），只是「选悲观」会选出乐观值，因此单独有测试守。

无 drivers 时不建表，工作簿形状与从前完全一致 —— 预研失败的降级路径。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>" && cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && git add -A && git commit -q -m "1b_驱动分解 工作表的测试

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: `full.py` 接线 — 把预研与采集接进第一阶段

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `ir_agent/dcf.py`
- Test: `tests/test_full_context.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `research`/`to_markdown`；Task 6 的 `collect`/`summary`；Task 7 的 `decide`/`synthesize`；Task 9 的 `cal["judg"]["drivers"]`
- Produces:
  - `full.DRIVERS_MD = "drivers.md"` 常量
  - `full._research_methodology(code, r, out: Path, client) -> tuple[Methodology | None, str]`
  - `run_analysis` 在 `_analyse_facts` 之前跑预研并落盘 `drivers.md`
  - `full._propose_assumptions(..., decomps=None, factor_data=None) -> str`
  - `assume.to_driver_block(a: AssumptionSet, cal: dict) -> dict` —— 产出
    Task 9 所需的 `drivers` 结构（键 `revenue` / `gross_margin`，各含
    `op` / `factors` / `values`，毛利率块另含 `base_level`）
  - `dcf.run` 接受 `assumptions` 时把因子写进 `cal["judg"]["drivers"]`

- [ ] **Step 1: 写失败的测试**

```python
# 追加到 tests/test_full_context.py

def test_预研排在数据采集之前():
    """**这是整个改造的要点。** 先问「这个行业靠什么赚钱」，再按答案去
    采集数据 —— 顺序反了就退化成「先收集再解释」。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_analysis)
    assert "methodology.research(" in src or "_research_methodology(" in src
    assert src.index("methodology") < src.index("_analyse_facts("), \
        "方法论预研必须在数据采集之前"


def test_分解式立刻落盘且说明可覆盖():
    """不另设人工门，改用「立刻落盘 + 可中断 + 可覆盖」换早期干预 ——
    门加多了人会开始机械点同意。"""
    import inspect

    from ir_agent import full
    assert full.DRIVERS_MD == "drivers.md"
    src = inspect.getsource(full.run_analysis)
    assert "DRIVERS_MD" in src


def test_预研失败退回不分解而非中断():
    """降级但继续 —— 损失的是精度，不是担保。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_analysis)
    assert "MethodologyFailed" in src
    assert "退回" in src or "不分解" in src


def test_采集结果进批准清单():
    import inspect

    from ir_agent import full
    src = inspect.getsource(full._propose_assumptions)
    assert "factor_data" in src and "decomps" in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_full_context.py -q`
Expected: FAIL — `AttributeError: module 'ir_agent.full' has no attribute 'DRIVERS_MD'`

- [ ] **Step 3: 实现接线**

3a. `full.py` 顶部常量区增加：

```python
DRIVERS_MD = "drivers.md"
```

3b. 新增预研函数：

```python
def _research_methodology(code: str, r, out: Path, client):
    """方法论预研 —— 先问「这个行业靠什么赚钱」，再按答案去采集数据。

    **不另设人工确认门。** 门的价值会被数量稀释: 现有那道判断项的门之所以
    有效，是因为它一次只要求人看一件事；再加一道，人看两次的注意力未必比
    看一次多。改用「立刻落盘 + 可中断 + 可覆盖」换取早期干预。
    """
    from ir_agent import methodology

    try:
        from ir_agent.sources.taxonomy import fetch_all
        rows = fetch_all()
        me = next((e for e in rows if e.code == code), None)
        industry = me.path if me else ""
    except Exception:                                       # noqa: BLE001
        industry = ""
    if not industry:
        return None, "方法论预研（取不到申万行业分类）"

    try:
        m = methodology.research(client, industry, code)
    except methodology.MethodologyFailed as e:
        return None, (f"方法论预研（{str(e)[:160]}）—— "
                      "退回不分解模式，判断项仍按营收增速/毛利率两项填写")
    (out / DRIVERS_MD).write_text(methodology.to_markdown(m), encoding="utf-8")
    return m, ""
```

3c. `run_analysis` 在 `_analyse_facts` 之前插入：

```python
    qual_client = make_client(model=model)
    meth, meth_err = _research_methodology(code, r, out, qual_client)
    if meth_err:
        skipped.append(meth_err)
    if meth is not None:
        arts["驱动因子分解"] = str(out / DRIVERS_MD)
```

（注意：`_analyse` 内部也会 `make_client`，为避免重复计费，把 `qual_client`
作为参数传进 `_analyse`；若改动过大，可接受两个 client 并在末尾把两者的
`total_cost_usd` 相加。）

3d. 采集接在 `_analyse` 之后、撰写之前：

```python
    factor_data = {}
    if meth is not None:
        from ir_agent import gaps
        try:
            factor_data = gaps.collect(
                qual_client, meth.decompositions, r.ledger,
                r.corpus or Corpus(as_of=as_of), meth.industry, as_of,
                sections=getattr(r, "sections", None))
            md["因子数据"] = gaps.summary(factor_data)
        except Exception as e:                              # noqa: BLE001
            skipped.append(f"因子数据采集（{e.__class__.__name__}: {e}）")
```

3e. `_propose_assumptions` 签名与调用增加 `decomps` / `factor_data`：

```python
def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
                         decomps=None, factor_data=None) -> str:
    ...
    base_margin = _fact_value(r.ledger, "gross_margin", r.period, as_of)
    try:
        a = assume.decide(client, cal["judg"], labels, ctx,
                          ledger=r.ledger, body=body, as_of=as_of, code=code,
                          decomps=decomps, factor_data=factor_data,
                          base_margin=base_margin)
    except assume.AssumeFailed as e:
        return f"DCF 判断项（{str(e)[:200]}）"
```

3f. `dcf.run` 在 `assumptions is not None` 分支里，若 `assumptions.decomps`
非空则写 `drivers`：

```python
    if assumptions is not None:
        from ir_agent import assume
        cal = assume.apply_to(cal, assumptions)
        if getattr(assumptions, "decomps", None):
            cal["judg"]["drivers"] = assume.to_driver_block(assumptions, cal)
            notes.append("工作簿含「1b_驱动分解」表，"
                         "改一个因子会带动整条估值链重算")
```

并在 `assume.py` 新增：

```python
def to_driver_block(a: AssumptionSet, cal: dict) -> dict:
    """AssumptionSet → ashare_build 要的 drivers 结构。

    三档顺序固定为 bull/base/bear，与工作簿 B/C/D 列绑定 ——
    顺序写反不会让验算失败，只是「选悲观」会选出乐观值。
    """
    from decimal import Decimal as _D

    base_margin = float((cal.get("judg") or {}).get("gm", [0.0])[0])
    out = {}
    years = max((r.year for r in a.rows), default=0)
    for d in a.decomps:
        keys = [f.key for f in d.factors]
        vals = {}
        for case, attr in (("bull", "bull"), ("base", "base"), ("bear", "bear")):
            vals[case] = [[float(getattr(
                next(r for r in a.rows
                     if r.year == y and r.factor_key == k), attr))
                for k in keys] for y in range(1, years + 1)]
        block = {"op": d.op.value,
                 "factors": [{"key": f.key, "label": f.label, "unit": f.unit}
                             for f in d.factors],
                 "values": vals}
        if d.target == "gross_margin":
            block["base_level"] = base_margin
        out[d.target] = block
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest tests/test_full_context.py -q`
Expected: 全绿

- [ ] **Step 5: 跑全量**

Run: `cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m pytest -q`
Expected: 全绿

- [ ] **Step 6: 端到端实跑（长电两阶段）**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && rm -rf /tmp/cj/p3 && PYTHONPATH="$PWD" python -m ir_agent 600584 --year 2025 --as-of 2026-09-07 --analyse /tmp/cj/p3 2>&1 | grep -v "it/s\]" | tail -25
```

Expected: 产出 `drivers.md`、`brief.md`、`assumptions.md`（含因子表与采集状态）、
`assumptions.json`

```bash
cat /tmp/cj/p3/drivers.md && echo "=====" && cat /tmp/cj/p3/assumptions.md
```

人工检查：分解式是否闭合合理、采集状态是否如实、无数据的因子是否标注为估计。

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && PYTHONPATH="$PWD" python -m ir_agent 600584 --year 2025 --as-of 2026-09-07 --value /tmp/cj/p3 --approved-by "test" 2>&1 | grep -v "it/s\]" | tail -20
```

Expected: DCF 建模成功、公式引擎验算通过、`report.md` 产出，且工作簿含
`1b_驱动分解` 表。

- [ ] **Step 7: 提交**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && git add -A && git commit -q -m "接线: 方法论预研 → 缺口识别 → 定向采集 → 按因子填判断项

**先问「这个行业靠什么赚钱」，再按答案去采集数据。** 顺序反了就退化成
「先收集再解释」—— 那正是此前的形状: 固定模板 + 填数据，五块分析写死、
每块要什么指标也写死，无论研究什么公司问的都是同一组问题。

分解式立刻落盘为 drivers.md，不另设人工门 —— 门的价值会被数量稀释，
改用「可中断 + 可覆盖」换取早期干预。

预研失败退回不分解模式并说明原因: 降级但继续，损失的是精度不是担保。

判断项因此从「两个标量」变成「按因子分解」，工作簿里也多出 1b_驱动分解 表
并与假设表联动。人核实的从此是分解而非一个合成后的数字 —— 遗漏因此可见:
清单上没有「稼动率」这一行，一眼就知道这个因素没被纳入。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 自查

**规格覆盖：**

| 规格章节 | 对应任务 |
| --- | --- |
| 流程（预研在采集之前） | Task 10 |
| `methodology.py` | Task 4 |
| `drivers.py` 表示与闭合 | Task 1 |
| pp 与变化率不得混用 | Task 1（校验）、Task 2（`pp_hint`）、Task 7（提示词） |
| 历史回算报残差 | Task 2 |
| `sources/web.py` + MEDIA 约束 | Task 5 |
| 定向采集顺序 | Task 6 |
| 采集不到不用代理指标顶替 | Task 6 |
| 判断项改造 | Task 7 |
| 批准清单形状 | Task 7（`describe` 由 Task 10 实跑验收） |
| `1b_驱动分解` 工作表 | Task 9 |
| `ashare_verify` 跨表引用 | Task 8 |
| 三档列序断言 | Task 9 |
| 失败与降级 | Task 4（预研失败）、Task 6（采集失败）、Task 5（不可联网）、Task 8/9（验算硬失败） |
| ashare-dcf 版本控制 | Task 0 |

**未覆盖但已知：** 规格中「批准清单的完整形状」（含残差行、数据状态列）在
Task 7 的 `describe()` 里实现，但没有独立的单元测试断言其全部字段 ——
由 Task 10 Step 6 的实跑人工检查兜底。若实现者希望更严，可在 Task 7 追加
一条断言 `describe()` 同时含「分解式」「残差」「数据」三段。
