# 分解式公式化（子项目一）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 分解式从「乘/加两种形状 + 字面闸门」改为「模型写公式、代码求值」，并打通判断项、第二阶段与工作簿。

**Architecture:** 新增 `ir_agent/formula.py`（白名单解析、变化/水平两种模式逐年推演、旧乘/加分解式逐位等价翻译、给工作簿的记号序列）；`methodology` 认新写法（`模式/因子/常量/公式`）并把旧写法翻译成公式；`assume` 全部经公式模型合成、校验、渲染、落盘（schema 2）；下游（full/gaps/factor_review/dcf）统一读 `FormulaModel`；`ashare-dcf` 按记号生成 Excel 公式，跨仓库对账钉住两边一致。

**Tech Stack:** Python 3.11（`ast`、`decimal`）、pytest、openpyxl；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-14-formula-decomposition-design.md`（含「写计划时的补充」三处，用户已确认）。

## 写计划的方式（与以往不同，先说清楚）

这次改动横跨两个仓库、`assume.py` 2000 行、两百多处旧测试。计划里的代码**不是先写在纸上**，而是：

1. 在两个仓库各开一个独立工作树、分支 `formula-dev`，逐任务实现、提交、跑通全量；
2. 再从 main 新建临时工作树，按本计划的步骤**完整演练一遍**（先取测试 → 跑红 → 取实现 → 跑绿），下文每一步的「Expected」都是演练实测的数字；
3. 每个任务末尾附该任务的**完整 diff**（`git diff` 原样），供审阅。

因此执行本计划 = 在 main 上按任务从 `formula-dev` 取文件（`git checkout <提交> -- <文件>`），每一步核对红绿数字与变异结果，再在 main 上提交。代码与开发分支逐字节一致。

## Global Constraints

- 两个仓库的开发分支都叫 `formula-dev`；ir-agent 的提交：`cab7bb4`（引擎）、`cd81447`（methodology）、`514487b`+`179bbae`+`0f315db`（assume）、`99f5609`+`4dded9a`（下游）、`a824d2d`（1b 表测试）、`a005fd5`（跨仓库对账）；ashare-dcf 的提交：`5232db7`。
- **旧格式逐位等价**：旧乘/加分解式翻译后的合成结果、锚定平移、历史对账与改动前逐位相同（`tests/test_assume.py` 原有 153 条、`tests/test_formula.py::TestLegacy` 钉住）。
- **代码只查能不能算**：新写法不跑运算匹配、单位白名单、占比类、营收量级四道字面闸门；保留因子个数（每条公式 2–5）、key 不撞车、检索词长度、名字合法。
- **两个子项目之间不单独试跑**（spec 五）：本计划完成后不跑 `--brief`，等子项目二（提出者 + 裁决者）完成后一起试跑。
- **合并顺序**：任务 5 同时改 ashare-dcf 与 ir-agent 的 `tests/test_driver_sheet.py`，两边要在同一个任务里落地 —— 先只合一边，另一边的 1b 表测试会红。
- 带环境变量的命令用 `bash -c`（zsh 不切分 `$VAR`）；测试计数先 `--collect-only`。
- ashare-dcf 全量要排除两个脚本式测试：`--ignore=tests/test_financing.py --ignore=tests/test_financing_mutation.py`（README 里它们单独带工作簿路径运行）；跑一次约 3 分钟。

## 基线（执行前在 main 上核对）

```bash
cd ir-agent && python -m pytest -q -m "not live" -W error --collect-only | tail -1     # 2268 tests collected
cd ../ashare-dcf && python -m pytest -q --ignore=tests/test_financing.py --ignore=tests/test_financing_mutation.py | tail -1   # 65 passed
```

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `ir_agent/formula.py`（新） | 白名单解析、`FormulaModel`、逐年推演 `simulate`、旧格式翻译 `from_decomps`、记号 `tokens()`、`flat_factors`/`as_model` | 1、3 |
| `ir_agent/drivers.py` | `Factor.short` | 1 |
| `ir_agent/methodology.py` | 新写法解析/往返/渲染、提示词改写、覆盖与命中次数接受新写法 | 2、3 |
| `ir_agent/assume.py` | 判断项全部经公式模型：解析（短名、基期行、按单位防呆）、合成、锚定平移、校验、清单、提示词、历史对账、schema 2 落盘、驱动块 | 3 |
| `ir_agent/full.py`、`gaps.py`、`factor_review.py`、`dcf.py` | 下游读 `FormulaModel`；修第二阶段重新校验退回两标量的漏洞 | 4 |
| `ashare-dcf/ashare_build.py`、`ashare_verify.py` | 1b 表按记号生成公式；引用链上限 5→64 | 5 |
| `tests/test_formula.py`、`test_assume_formula.py`、`test_formula_workbook.py`（新） | 引擎、判断项新写法、跨仓库对账 | 1、3、6 |

---

### Task 1: 计算引擎 `formula.py`

**Files:**
- Create: `ir_agent/formula.py`、`tests/test_formula.py`
- Modify: `ir_agent/drivers.py`（`Factor.short`）

**Interfaces（后续任务依赖）:**
- `parse_formula(rhs, target, declared) -> Formula`；`Formula.names()/lagged()/tokens()`
- `FormulaModel(mode, factors, constants, formulas, legacy)`；`.factor(short)`、`.const(name)`、`.factors_of(target)`、`.needs_base()`、`.describe(target)`、`.validate()`
- `simulate(model, values, *, base_factors, base_revenue, base_margin, anchor_y1) -> Path(revenue0, margin0, revenue, growth, margin)`
- `from_decomps(decomps) -> FormulaModel(legacy=True)`；`short_from_label`；`parse_constant`；`valid_name`；`FormulaInvalid`（写法算不了）、`FormulaError`（代入后算不下去）
- 常量：`TARGETS = {"营收": "revenue", "毛利率": "gross_margin"}`、`MODES = ("变化", "水平")`、`RESERVED`

- [ ] **Step 1: 取测试，确认红**

```bash
git checkout cab7bb4 -- tests/test_formula.py
python -m pytest tests/test_formula.py -q
```
Expected: `1 error`（`ModuleNotFoundError: ir_agent.formula`）。

- [ ] **Step 2: 取实现，确认绿；全量不回归**

```bash
git checkout cab7bb4 -- ir_agent/formula.py ir_agent/drivers.py
python -m pytest tests/test_formula.py -q            # 36 passed
python -m pytest -q -m "not live" -W error           # 2296 passed, 8 skipped
```

- [ ] **Step 3: 变异验证**（逐个改、逐个还原：`cp ir_agent/formula.py /tmp/f.bak` → 改 → 跑 → `cp /tmp/f.bak ir_agent/formula.py`；**不要用 `git checkout` 还原，会连带丢掉未提交的改动**）
  - `simulate` 里水平模式 `r0 = evaluate(rev_f, …)` 改为取 `base_revenue` → `test_水平模式第0年由基期因子代入公式_不用账本营收` 红；
  - 删掉锚定平移 `base["毛利率"] = m0 + (Decimal(anchor_y1) - m)` → `TestLegacy::test_锚定平移与旧margin_bases逐位相同` 红；
  - `from_decomps` 加法链 `基期(毛利率)` 改成 `上期(毛利率)` → `test_与旧引擎逐位相同` 红。

- [ ] **Step 4: 提交**

```bash
git add ir_agent/formula.py ir_agent/drivers.py tests/test_formula.py
git commit -m "formula: 分解式公式化的计算引擎 —— 白名单解析、两种模式逐年推演、旧乘/加分解式逐位等价翻译"
```

<details><summary>完整 diff（cab7bb4）</summary>

```diff
diff --git a/ir_agent/drivers.py b/ir_agent/drivers.py
index a51a08c..7bee2d5 100644
--- a/ir_agent/drivers.py
+++ b/ir_agent/drivers.py
@@ -61,6 +61,9 @@ class Factor:
     # 走到底。对价是提示词里把后果说清，以及 `gaps.collect` 在 note 里
     # 标出「靠模型给的检索词命中」（见 `gaps._via_note`）。
     search_term: str = ""
+    # 公式里指代这个因子的短名（`formula`，2026-10-14）。旧的乘/加分解式没有
+    # 它，读回时由 `formula.short_from_label` 从完整名生成。
+    short: str = ""
 
     def __post_init__(self) -> None:
         # **只查长度，这是唯一的闸门。** 1 字检索词（「率」「价」）在任何
diff --git a/ir_agent/formula.py b/ir_agent/formula.py
new file mode 100644
index 0000000..8a3a3bd
--- /dev/null
+++ b/ir_agent/formula.py
@@ -0,0 +1,425 @@
+"""分解式公式化 —— 模型写公式，代码求值。
+
+**为什么从「乘/加」两种形状改成公式**（2026-10-14，spec
+`docs/superpowers/specs/2026-10-14-formula-decomposition-design.md`）。旧的分解式
+只有两种形状：营收 = Π(1+变化率) − 1、毛利率 = 基期 + Σpp/100。形状之外的一切
+（单位、分母在哪、是不是营收量级）都靠 `drivers` 里的字面闸门去近似，生益科技
+第二次调用被「本司市占率（占全行业规模，非公司内部占比）同比增速」里一个带「非」
+的「占比」挡了三次。公式化把「怎么合成」交给提出分解式的模型、由裁决者审，代码
+只管**能不能算**。
+
+**本模块只做两件事，都不是内容判断**：
+1. 静态校验：公式能否解析、只用了白名单写法、引用的名字都已声明
+   （`FormulaInvalid`）；
+2. 逐年推演：把取值代进去算出营收水平/增速与毛利率（`FormulaError`：除以零、
+   缺取值）。
+
+**模式二选一**（用户决定 8）：同一家公司只能「变化」或「水平」。变化模式下
+因子填每年的变化，第 0 年的营收、毛利率取账本；水平模式下因子填每年的水平，
+第 0 年的营收、毛利率用基期因子值代入公式算出 —— 出货量×单价未必等于报表营收，
+分子分母必须同口径。
+
+**`上期(x)` 与 `基期(x)`**：前者取上一年，后者取第 0 年。旧毛利率加法链的 pp 是
+**相对基期的累计值**（`assume._factor_head` 提示词明文），翻译成
+`基期(毛利率) + (…)/100`，不是 `上期`。
+"""
+
+from __future__ import annotations
+
+import ast
+import keyword
+import re
+import unicodedata
+from dataclasses import dataclass, field
+from decimal import Decimal, DivisionByZero, InvalidOperation
+
+TARGETS = {"营收": "revenue", "毛利率": "gross_margin"}
+TARGET_CN = {v: k for k, v in TARGETS.items()}
+MODES = ("变化", "水平")
+PREV, BASE = "上期", "基期"
+RESERVED = frozenset(TARGETS) | {PREV, BASE}
+
+
+class FormulaInvalid(Exception):
+    """公式写法本身算不了 —— 解析不了、用了白名单外的写法、名字未声明。"""
+
+
+class FormulaError(Exception):
+    """公式写法没问题，但代入取值后算不下去 —— 除以零、缺某年取值。"""
+
+
+# 模型常写的全角/数学符号。只换写法，不改含义。
+_SYMBOLS = str.maketrans({"×": "*", "÷": "/", "（": "(", "）": ")",
+                          "−": "-", "＋": "+", "－": "-", "＊": "*", "／": "/"})
+
+
+def _normalize(src: str) -> str:
+    return src.translate(_SYMBOLS).strip()
+
+
+def valid_name(name: str) -> bool:
+    """短名、常量名的合法性：能当标识符、不撞保留字。
+
+    「不得含运算符、括号、空格」（spec 一）由 `isidentifier` 一并保证。"""
+    return (bool(name) and name.isidentifier() and name not in RESERVED
+            and not keyword.iskeyword(name))
+
+
+@dataclass(frozen=True)
+class Formula:
+    """一条已校验的公式。`target` 为 revenue / gross_margin。"""
+
+    target: str
+    text: str                 # 右边原文（给人看）
+    tree: ast.expr = field(compare=False, repr=False)
+    src: str = field(compare=False, repr=False, default="")   # 规范化后的右边
+
+    def names(self) -> set[str]:
+        """引用到的全部名字（含 上期/基期 的参数）。"""
+        out: set[str] = set()
+        for node in ast.walk(self.tree):
+            if isinstance(node, ast.Name) and node.id not in (PREV, BASE):
+                out.add(node.id)
+        return out
+
+    def lagged(self) -> set[tuple[str, str]]:
+        """`(上期|基期, 名字)` 的全部出现。"""
+        out: set[tuple[str, str]] = set()
+        for node in ast.walk(self.tree):
+            if isinstance(node, ast.Call):
+                out.add((node.func.id, node.args[0].id))
+        return out
+
+    def tokens(self) -> list[tuple[str, str]]:
+        """给工作簿的记号序列，**全括号**以免依赖 Excel 的运算优先级。
+
+        记号：("num", "100") / ("name", 短名或常量名) / ("prev", 名字) /
+        ("base", 名字) / ("op", "+-*/") / ("lp", "(") / ("rp", ")")。
+        `ashare-dcf` 只把记号换成单元格引用，**不做解析** —— 解析只在这里一处。
+        """
+        out: list[tuple[str, str]] = []
+        _emit(self.tree, self.src, out)
+        return out
+
+
+def _emit(node, src: str, out: list) -> None:
+    if isinstance(node, ast.BinOp):
+        out.append(("lp", "("))
+        _emit(node.left, src, out)
+        out.append(("op", _OPS[type(node.op)]))
+        _emit(node.right, src, out)
+        out.append(("rp", ")"))
+    elif isinstance(node, ast.UnaryOp):
+        out += [("lp", "("), ("op", "-")]
+        _emit(node.operand, src, out)
+        out.append(("rp", ")"))
+    elif isinstance(node, ast.Constant):
+        out.append(("num", str(_num(node, src))))
+    elif isinstance(node, ast.Name):
+        out.append(("name", node.id))
+    else:                                                    # ast.Call
+        kind = "prev" if node.func.id == PREV else "base"
+        out.append((kind, node.args[0].id))
+
+
+_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
+
+
+def _num(node: ast.Constant, src: str) -> Decimal:
+    """数字按**源码原文**转 Decimal —— 经 float 会把 0.035 变成 0.0350000000…01。"""
+    seg = ast.get_source_segment(src, node)
+    return Decimal(seg if seg is not None else str(node.value))
+
+
+def parse_formula(rhs: str, target: str, declared: set[str]) -> Formula:
+    """把公式右边解析成 `Formula`。`declared` 是已声明的因子短名与常量名。
+
+    白名单：加减乘除、负号、括号、数字、已声明的名字、只带一个名字参数的
+    `上期(…)` / `基期(…)`。目标名（营收、毛利率）只能出现在 上期/基期 里 ——
+    裸写本年的目标是循环定义。
+    """
+    src = _normalize(rhs)
+    if not src:
+        raise FormulaInvalid(f"{TARGET_CN.get(target, target)}的公式右边是空的")
+    try:
+        tree = ast.parse(src, mode="eval").body
+    except SyntaxError as e:
+        raise FormulaInvalid(
+            f"{TARGET_CN.get(target, target)}的公式解析不了：{rhs!r}"
+            f"（{e.msg}）") from e
+    args = {id(n.args[0]) for n in ast.walk(tree)
+            if isinstance(n, ast.Call) and n.args}
+    for node in ast.walk(tree):
+        _check_node(node, src, target, declared, rhs, args)
+    return Formula(target=target, text=rhs.strip(), tree=tree, src=src)
+
+
+def _check_node(node, src, target, declared, rhs, args) -> None:
+    where = ast.get_source_segment(src, node) or ""
+    cn = TARGET_CN.get(target, target)
+    if isinstance(node, (ast.BinOp, ast.UnaryOp)):
+        if isinstance(node, ast.BinOp) and type(node.op) not in _OPS:
+            raise FormulaInvalid(f"{cn}的公式里「{where}」用了加减乘除以外的运算")
+        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, ast.USub):
+            raise FormulaInvalid(f"{cn}的公式里「{where}」用了负号以外的一元运算")
+    elif isinstance(node, (ast.operator, ast.unaryop, ast.Load)):
+        pass
+    elif isinstance(node, ast.Constant):
+        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
+            raise FormulaInvalid(f"{cn}的公式里「{where}」不是数字")
+    elif isinstance(node, ast.Call):
+        fn = node.func.id if isinstance(node.func, ast.Name) else ""
+        if fn not in (PREV, BASE):
+            raise FormulaInvalid(
+                f"{cn}的公式里「{where}」调用了函数 —— 只允许 上期(…) 与 基期(…)，"
+                "不提供 max/min/条件")
+        if (len(node.args) != 1 or node.keywords
+                or not isinstance(node.args[0], ast.Name)):
+            raise FormulaInvalid(f"{cn}的公式里「{where}」：{fn}(…) 只能带一个名字")
+        arg = node.args[0].id
+        if arg not in declared and arg not in TARGETS:
+            raise FormulaInvalid(f"{cn}的公式里「{where}」引用了未声明的名字「{arg}」")
+    elif isinstance(node, ast.Name):
+        if node.id in (PREV, BASE):
+            return                                   # 作为 Call.func 出现
+        if node.id in TARGETS:
+            if id(node) not in args:
+                raise FormulaInvalid(
+                    f"{cn}的公式里裸写了「{node.id}」—— 目标只能出现在 上期(…)/"
+                    "基期(…) 里，裸写本年的目标是循环定义")
+            return
+        if node.id not in declared:
+            raise FormulaInvalid(f"{cn}的公式里引用了未声明的名字「{node.id}」"
+                                 f"（公式：{rhs.strip()}）")
+    else:
+        raise FormulaInvalid(f"{cn}的公式里「{where}」是白名单之外的写法")
+
+
+def parse_constant(raw: str) -> Decimal:
+    """常量取值：「78%」→ 0.78，「0.78」「58.2」原样。"""
+    t = unicodedata.normalize("NFKC", raw).strip().replace(",", "")
+    try:
+        if t.endswith("%"):
+            return Decimal(t[:-1]) / 100
+        return Decimal(t)
+    except InvalidOperation as e:
+        raise FormulaInvalid(f"常量取值不是数字：{raw!r}") from e
+
+
+@dataclass(frozen=True)
+class Constant:
+    name: str
+    value: Decimal
+    source: str
+
+
+@dataclass
+class FormulaModel:
+    """一家公司的分解式：模式、因子、常量、两条公式。
+
+    `factors` 的元素是 `drivers.Factor`（带 `short`）。`legacy=True` 表示由旧的
+    乘/加分解式翻译而来（`from_decomps`）——读回旧文件时用，行为与旧引擎逐位相同。
+    """
+
+    mode: str
+    factors: list
+    constants: list[Constant] = field(default_factory=list)
+    formulas: dict[str, Formula] = field(default_factory=dict)
+    legacy: bool = False
+
+    def factor(self, short: str):
+        return next((f for f in self.factors if f.short == short), None)
+
+    def const(self, name: str) -> Constant | None:
+        return next((c for c in self.constants if c.name == name), None)
+
+    def factors_of(self, target: str) -> list:
+        """某条公式引用到的因子，按声明顺序。"""
+        used = self.formulas[target].names() if target in self.formulas else set()
+        used |= {n for _, n in (self.formulas[target].lagged()
+                                if target in self.formulas else ())}
+        return [f for f in self.factors if f.short in used]
+
+    def needs_base(self) -> list:
+        """需要第 0 年取值的因子：水平模式下公式引用到的全部因子；变化模式下
+        公式里出现 上期(因子)/基期(因子) 的。"""
+        need: set[str] = set()
+        for fm in self.formulas.values():
+            if self.mode == "水平":
+                need |= {n for n in fm.names() if self.factor(n) is not None}
+            need |= {n for _, n in fm.lagged() if self.factor(n) is not None}
+        return [f for f in self.factors if f.short in need]
+
+    def describe(self, target: str) -> str:
+        fm = self.formulas.get(target)
+        return f"{TARGET_CN[target]} = {fm.text}" if fm else ""
+
+    def validate(self) -> None:
+        """能不能算：模式合法、两条公式都有、名字合法且不重复。"""
+        if self.mode not in MODES:
+            raise FormulaInvalid(f"模式只能是「变化」或「水平」，得到 {self.mode!r}")
+        missing = [cn for cn, t in TARGETS.items() if t not in self.formulas]
+        if missing:
+            raise FormulaInvalid(f"缺少 {'、'.join(missing)} 的公式 —— 两条都要给")
+        seen: set[str] = set()
+        for n in [f.short for f in self.factors] + [c.name for c in self.constants]:
+            if not valid_name(n):
+                raise FormulaInvalid(
+                    f"名字「{n}」不合法 —— 短名与常量名不能含运算符、括号、空格，"
+                    f"不能以数字开头，不能是 {'、'.join(sorted(RESERVED))}")
+            if n in seen:
+                raise FormulaInvalid(f"名字「{n}」重复声明")
+            seen.add(n)
+
+
+@dataclass(frozen=True)
+class Path:
+    """一档的推演结果。`revenue` 是营收水平（第 1..N 年），`growth` 是营收增速，
+    `margin` 是毛利率水平。`revenue0`/`margin0` 是第 0 年。"""
+
+    revenue0: Decimal
+    margin0: Decimal
+    revenue: list
+    growth: list
+    margin: list
+
+
+def _eval(node, src, cur, prev, base, consts, where) -> Decimal:
+    if isinstance(node, ast.BinOp):
+        a = _eval(node.left, src, cur, prev, base, consts, where)
+        b = _eval(node.right, src, cur, prev, base, consts, where)
+        try:
+            if isinstance(node.op, ast.Add):
+                return a + b
+            if isinstance(node.op, ast.Sub):
+                return a - b
+            if isinstance(node.op, ast.Mult):
+                return a * b
+            return a / b
+        except (DivisionByZero, InvalidOperation, ZeroDivisionError) as e:
+            raise FormulaError(f"{where}：「{ast.get_source_segment(src, node)}」"
+                               "除以零") from e
+    if isinstance(node, ast.UnaryOp):
+        return -_eval(node.operand, src, cur, prev, base, consts, where)
+    if isinstance(node, ast.Constant):
+        return _num(node, src)
+    if isinstance(node, ast.Call):
+        name = node.args[0].id
+        table, what = (prev, "上期") if node.func.id == PREV else (base, "基期")
+        if table is None or name not in table or table[name] is None:
+            raise FormulaError(f"{where}：取不到 {what}({name})")
+        return table[name]
+    name = node.id
+    if name in consts:
+        return consts[name]
+    if name not in cur or cur[name] is None:
+        raise FormulaError(f"{where}：缺「{name}」的取值")
+    return cur[name]
+
+
+def evaluate(fm: Formula, cur: dict, prev: dict | None, base: dict | None,
+             consts: dict, where: str = "") -> Decimal:
+    return _eval(fm.tree, fm.src, cur, prev, base, consts,
+                 where or TARGET_CN.get(fm.target, fm.target))
+
+
+def simulate(model: FormulaModel, values: dict[str, list], *,
+             base_factors: dict | None = None,
+             base_revenue: Decimal | None = None,
+             base_margin: Decimal | None = None,
+             anchor_y1: Decimal | None = None) -> Path:
+    """一档的逐年推演。`values[短名]` 是第 1..N 年的取值（已按单位换算）。
+
+    第 0 年：变化模式取 `base_revenue`/`base_margin`（账本）；水平模式用
+    `base_factors` 代入公式算出（口径不混用）。
+
+    `anchor_y1`（半年报锚定后该档第 1 年毛利率）给定时，第 2 年起：`上期(毛利率)`
+    取锚定值；`基期(毛利率)` 平移为 `基期 + (锚定值 − 第 1 年公式值)` —— 与旧
+    `assume.margin_bases` 的平移逐位一致（spec「写计划时的补充」2）。第 1 年的
+    合成值本身不被锚定替换（工作簿另行锚定覆盖）。
+    """
+    base_factors = dict(base_factors or {})
+    consts = {c.name: c.value for c in model.constants}
+    years = max((len(v) for v in values.values()), default=0)
+    rev_f, gm_f = model.formulas["revenue"], model.formulas["gross_margin"]
+    if model.mode == "水平":
+        y0 = dict(base_factors)
+        r0 = evaluate(rev_f, y0, None, y0, consts, "第 0 年营收")
+        m0 = evaluate(gm_f, y0, None, y0, consts, "第 0 年毛利率")
+    else:
+        if base_revenue is None or base_margin is None:
+            raise FormulaError("变化模式需要第 0 年的营收与毛利率（账本），没有给")
+        r0, m0 = Decimal(base_revenue), Decimal(base_margin)
+    base = dict(base_factors)
+    base["营收"], base["毛利率"] = r0, m0
+    prev = dict(base)
+    revenue, growth, margin = [], [], []
+    for t in range(1, years + 1):
+        cur = {}
+        for short, seq in values.items():
+            if len(seq) < t:
+                raise FormulaError(f"缺「{short}」第 {t} 年的取值")
+            cur[short] = seq[t - 1]
+        r = evaluate(rev_f, cur, prev, base, consts, f"第 {t} 年营收")
+        m = evaluate(gm_f, cur, prev, base, consts, f"第 {t} 年毛利率")
+        if prev["营收"] == 0:
+            raise FormulaError(f"第 {t} 年：上一年营收为零，增速算不出来")
+        growth.append(r / prev["营收"] - 1)
+        revenue.append(r)
+        margin.append(m)
+        prev = dict(cur)
+        prev["营收"], prev["毛利率"] = r, m
+        if t == 1 and anchor_y1 is not None:
+            prev["毛利率"] = Decimal(anchor_y1)
+            base = dict(base)
+            base["毛利率"] = m0 + (Decimal(anchor_y1) - m)
+    return Path(revenue0=r0, margin0=m0, revenue=revenue, growth=growth,
+                margin=margin)
+
+
+# ── 旧格式翻译 ────────────────────────────────────────────────────
+
+_STRIP = re.compile(r"[^\w]")
+
+
+def short_from_label(label: str, taken: set[str]) -> str:
+    """由完整名生成短名：去括号、标点、空格；以数字开头加「f」；撞了加序号。"""
+    s = _STRIP.sub("", unicodedata.normalize("NFKC", label)) or "因子"
+    if s[0].isdigit():
+        s = "f" + s
+    if not valid_name(s):
+        s = "因子" + s
+    out, i = s, 2
+    while out in taken or not valid_name(out):
+        out, i = f"{s}{i}", i + 1
+    taken.add(out)
+    return out
+
+
+def from_decomps(decomps: list) -> FormulaModel:
+    """旧的乘/加分解式 → 变化模式公式。**结果与旧 `drivers.compose` 逐位相同。**
+
+    乘 → `营收 = 上期(营收) × (1+A) × (1+B)…`（增速 = Π(1+r) − 1）；
+    加 → `毛利率 = 基期(毛利率) + (A + B…)/100`（旧加法链 pp 是相对基期累计值）。
+    """
+    from dataclasses import replace
+
+    from ir_agent.drivers import Op
+
+    taken: set[str] = set()
+    factors, formulas = [], {}
+    for d in decomps:
+        shorts = []
+        for f in d.factors:
+            s = f.short if getattr(f, "short", "") else short_from_label(f.label, taken)
+            taken.add(s)
+            factors.append(replace(f, short=s))
+            shorts.append(s)
+        if d.op is Op.MULTIPLY:
+            rhs = "上期(营收)" + "".join(f"*(1+{s})" for s in shorts)
+        else:
+            rhs = "基期(毛利率)+(" + "+".join(shorts) + ")/100"
+        declared = set(shorts)
+        formulas[d.target] = parse_formula(rhs, d.target, declared)
+    return FormulaModel(mode="变化", factors=factors, formulas=formulas,
+                        legacy=True)
diff --git a/tests/test_formula.py b/tests/test_formula.py
new file mode 100644
index 0000000..10af8e6
--- /dev/null
+++ b/tests/test_formula.py
@@ -0,0 +1,223 @@
+"""分解式公式化的计算引擎（spec 2026-10-14 子项目一）。"""
+from __future__ import annotations
+
+from decimal import Decimal as D
+
+import pytest
+
+from ir_agent.drivers import Decomposition, Factor, Op, compose
+from ir_agent.formula import (Constant, FormulaError, FormulaInvalid,
+                              FormulaModel, from_decomps, parse_formula,
+                              parse_constant, short_from_label, simulate)
+
+
+def _f(short, label=None, unit="变化率"):
+    return Factor(key=f"f_{short}", label=label or short, unit=unit, short=short)
+
+
+def _model(mode, factors, rev, gm, consts=()):
+    declared = {f.short for f in factors} | {c.name for c in consts}
+    m = FormulaModel(mode=mode, factors=list(factors), constants=list(consts),
+                     formulas={"revenue": parse_formula(rev, "revenue", declared),
+                               "gross_margin": parse_formula(gm, "gross_margin",
+                                                             declared)})
+    m.validate()
+    return m
+
+
+class TestParse:
+    def test_全角与数学符号等价(self):
+        f = parse_formula("上期（营收）×（1＋量）÷ 2 − 价", "revenue", {"量", "价"})
+        assert f.names() == {"量", "价", "营收"}
+        assert ("上期", "营收") in f.lagged()
+
+    @pytest.mark.parametrize("rhs,frag", [
+        ("max(量, 价)", "只允许 上期"),
+        ("量 ** 2", "加减乘除以外"),
+        ("量 if 价 else 1", "白名单之外"),
+        ("上期(量, 价)", "只能带一个名字"),
+        ("上期(1)", "只能带一个名字"),
+        ("营收 * 量", "裸写了「营收」"),
+        ("未声明 * 量", "未声明的名字「未声明」"),
+        ("上期(未声明)", "未声明的名字「未声明」"),
+        ("'a' + 量", "不是数字"),
+        ("量 +", "解析不了"),
+        ("", "空的"),
+    ])
+    def test_白名单外的写法被拒绝并指明位置(self, rhs, frag):
+        with pytest.raises(FormulaInvalid, match=frag):
+            parse_formula(rhs, "revenue", {"量", "价"})
+
+    def test_数字按原文转Decimal不经float(self):
+        f = parse_formula("量 * 0.035", "revenue", {"量"})
+        assert ("num", "0.035") in f.tokens()
+
+    def test_记号全括号(self):
+        f = parse_formula("上期(营收) * (1 + 量) - 价", "revenue", {"量", "价"})
+        assert f.tokens() == [
+            ("lp", "("), ("lp", "("), ("prev", "营收"), ("op", "*"),
+            ("lp", "("), ("num", "1"), ("op", "+"), ("name", "量"), ("rp", ")"),
+            ("rp", ")"), ("op", "-"), ("name", "价"), ("rp", ")")]
+
+    def test_常量取值(self):
+        assert parse_constant("78%") == D("0.78")
+        assert parse_constant("１,２３４.5") == D("1234.5")
+        with pytest.raises(FormulaInvalid):
+            parse_constant("约八成")
+
+
+class TestValidate:
+    def test_缺一条公式(self):
+        m = FormulaModel(mode="变化", factors=[_f("量")], formulas={
+            "revenue": parse_formula("上期(营收)*(1+量)", "revenue", {"量"})})
+        with pytest.raises(FormulaInvalid, match="缺少 毛利率"):
+            m.validate()
+
+    def test_模式只能二选一(self):
+        with pytest.raises(FormulaInvalid, match="模式"):
+            FormulaModel(mode="混合", factors=[]).validate()
+
+    @pytest.mark.parametrize("bad", ["基期", "营收", "1量", "量 价", "量(吨)"])
+    def test_短名不合法(self, bad):
+        m = _model("变化", [_f("量")], "上期(营收)*(1+量)", "基期(毛利率)+量/100")
+        m.factors.append(_f(bad))
+        with pytest.raises(FormulaInvalid, match="不合法"):
+            m.validate()
+
+    def test_名字重复(self):
+        m = _model("变化", [_f("量")], "上期(营收)*(1+量)", "基期(毛利率)+量/100")
+        m.constants.append(Constant("量", D(1), "x"))
+        with pytest.raises(FormulaInvalid, match="重复"):
+            m.validate()
+
+
+class TestSimulate:
+    def test_变化模式第0年取账本(self):
+        m = _model("变化", [_f("量"), _f("价")],
+                   "上期(营收) * (1+量) * (1+价)", "上期(毛利率) + 价 * 0")
+        p = simulate(m, {"量": [D("0.1"), D("0.1")], "价": [D("0"), D("0.05")]},
+                     base_revenue=D(100), base_margin=D("0.2"))
+        assert p.revenue == [D("110.0"), D("127.05")]
+        assert p.growth == [D("0.1"), D("0.155")]
+        assert p.revenue0 == D(100)
+
+    def test_水平模式第0年由基期因子代入公式_不用账本营收(self):
+        """出货量×单价未必等于报表营收 —— 分子分母必须同口径。"""
+        m = _model("水平", [_f("量", unit="万吨"), _f("价", unit="元"),
+                           _f("本", unit="元")],
+                   "量 * 价", "(价 - 本) / 价")
+        p = simulate(m, {"量": [D(11)], "价": [D(10)], "本": [D(7)]},
+                     base_factors={"量": D(10), "价": D(10), "本": D(8)},
+                     base_revenue=D("999999"), base_margin=D("0.9"))
+        assert p.revenue0 == D(100) and p.margin0 == D("0.2")
+        assert p.growth == [D("0.1")] and p.margin == [D("0.3")]
+
+    def test_上期与基期分别取上一年与第0年(self):
+        m = _model("变化", [_f("增量", unit="pp")],
+                   "上期(营收) * 1", "基期(毛利率) + 增量 / 100")
+        p = simulate(m, {"增量": [D(-1), D(-2), D(-3)]},
+                     base_revenue=D(1), base_margin=D("0.1415"))
+        assert p.margin == [D("0.1315"), D("0.1215"), D("0.1115")]
+        m2 = _model("变化", [_f("增量", unit="pp")],
+                    "上期(营收) * 1", "上期(毛利率) + 增量 / 100")
+        p2 = simulate(m2, {"增量": [D(-1), D(-1), D(-1)]},
+                      base_revenue=D(1), base_margin=D("0.1415"))
+        assert p2.margin == p.margin, "逐年 −1 与相对基期累计 −1/−2/−3 等价"
+
+    def test_常量参与计算(self):
+        m = _model("变化", [_f("稼动率变动", unit="pp")],
+                   "上期(营收) * (1 + 稼动率变动 / 100 / 上期稼动率)",
+                   "基期(毛利率)", [Constant("上期稼动率", D("0.78"), "年报")])
+        p = simulate(m, {"稼动率变动": [D("-3")]},
+                     base_revenue=D(1), base_margin=D("0.2"))
+        assert abs(p.growth[0] - D(-3) / 100 / D("0.78")) < D("1e-26")
+
+    def test_上期因子在第1年取基期因子值(self):
+        m = _model("变化", [_f("价", unit="元")],
+                   "上期(营收) * 价 / 上期(价)", "基期(毛利率)")
+        p = simulate(m, {"价": [D(11), D("12.1")]}, base_factors={"价": D(10)},
+                     base_revenue=D(100), base_margin=D("0.2"))
+        assert p.growth == [D("0.1"), D("0.1")]
+
+    def test_除以零与缺取值明确报错(self):
+        m = _model("水平", [_f("量"), _f("价")], "量 * 价", "(价 - 量) / 价")
+        with pytest.raises(FormulaError, match="第 1 年毛利率.*除以零"):
+            simulate(m, {"量": [D(1)], "价": [D(0)]},
+                     base_factors={"量": D(1), "价": D(1)})
+        with pytest.raises(FormulaError, match="缺「价」第 2 年"):
+            simulate(m, {"量": [D(1), D(1)], "价": [D(1)]},
+                     base_factors={"量": D(1), "价": D(1)})
+        with pytest.raises(FormulaError, match="基期\\(价\\)|取不到|缺「价」"):
+            simulate(m, {"量": [D(1)], "价": [D(1)]}, base_factors={"量": D(1)})
+
+    def test_变化模式缺账本基期报错(self):
+        m = _model("变化", [_f("量")], "上期(营收)*(1+量)", "基期(毛利率)")
+        with pytest.raises(FormulaError, match="变化模式需要第 0 年"):
+            simulate(m, {"量": [D(0)]})
+
+    def test_需要基期值的因子(self):
+        lv = _model("水平", [_f("量"), _f("价"), _f("闲置")], "量 * 价", "价 / 价")
+        assert [f.short for f in lv.needs_base()] == ["量", "价"]
+        ch = _model("变化", [_f("量"), _f("价")],
+                    "上期(营收) * 价 / 上期(价)", "基期(毛利率) + 量")
+        assert [f.short for f in ch.needs_base()] == ["价"]
+
+
+class TestLegacy:
+    """**旧格式翻译后逐位相同**（spec 五）。"""
+
+    REV = Decomposition("revenue", Op.MULTIPLY, [
+        Factor("capacity", "名义产能（万吨）", "变化率"),
+        Factor("asp", "平均单价", "变化率")])
+    GM = Decomposition("gross_margin", Op.ADD_PP, [
+        Factor("cu", "铜箔采购价", "pp"), Factor("mix", "高端占比提升", "pp")])
+    YEARS = {"capacity": ["0.035", "0.12", "-0.04"],
+             "asp": ["0.02", "-0.015", "0.0725"],
+             "cu": ["-1.2", "-2.5", "0.3"], "mix": ["0.8", "1.6", "2.4"]}
+
+    def _vals(self, m):
+        return {f.short: [D(x) for x in self.YEARS[f.key]] for f in m.factors}
+
+    def test_短名生成(self):
+        taken: set[str] = set()
+        assert short_from_label("名义产能（万吨）", taken) == "名义产能万吨"
+        assert short_from_label("名义产能(万吨)", taken) == "名义产能万吨2"
+        assert short_from_label("3D封装占比", taken) == "f3D封装占比"
+
+    def test_翻译后的公式文本(self):
+        m = from_decomps([self.REV, self.GM])
+        assert m.legacy and m.mode == "变化"
+        assert m.describe("revenue") == "营收 = 上期(营收)*(1+名义产能万吨)*(1+平均单价)"
+        assert m.describe("gross_margin") == \
+            "毛利率 = 基期(毛利率)+(铜箔采购价+高端占比提升)/100"
+
+    def test_与旧引擎逐位相同(self):
+        m = from_decomps([self.REV, self.GM])
+        base = D("0.2647")
+        p = simulate(m, self._vals(m), base_revenue=D("20345678901.23"),
+                     base_margin=base)
+        for t in range(3):
+            vals = {k: D(v[t]) for k, v in self.YEARS.items()}
+            assert p.growth[t] == compose(self.REV, vals)
+            assert p.margin[t] == compose(self.GM, vals, base=base)
+
+    def test_锚定平移与旧margin_bases逐位相同(self):
+        """旧：第 2 年起基期 = 锚定值 − 第 1 年累计 pp/100。"""
+        m = from_decomps([self.REV, self.GM])
+        base, a1 = D("0.2647"), D("0.306912")
+        p = simulate(m, self._vals(m), base_revenue=D(1), base_margin=base,
+                     anchor_y1=a1)
+        cum1 = D(self.YEARS["cu"][0]) + D(self.YEARS["mix"][0])
+        shifted = a1 - cum1 / 100
+        vals1 = {k: D(v[0]) for k, v in self.YEARS.items()}
+        assert p.margin[0] == compose(self.GM, vals1, base=base), "第 1 年不替换"
+        for t in (1, 2):
+            vals = {k: D(v[t]) for k, v in self.YEARS.items()}
+            assert p.margin[t] == compose(self.GM, vals, base=shifted)
+
+    def test_上期毛利率公式在锚定后自然承接(self):
+        m = _model("变化", [_f("增量", unit="pp")], "上期(营收)*1",
+                   "上期(毛利率) + 增量 / 100")
+        p = simulate(m, {"增量": [D(0), D(1)]}, base_revenue=D(1),
+                     base_margin=D("0.2"), anchor_y1=D("0.3"))
+        assert p.margin == [D("0.2"), D("0.31")]
```

</details>

---

### Task 2: methodology 认公式化写法

**Files:** Modify `ir_agent/methodology.py`、`tests/test_methodology.py`

**Interfaces:**
- Consumes: Task 1 全部。
- Produces: `Methodology.model: FormulaModel`（新写法直接解析；旧 `分解:` 写法由 `from_decomps` 翻译，`decompositions` 保留原样）；`Methodology.factors()`；`parse()` 遇 `公式:` 行走新写法；`to_markdown`/`_to_dsl` 按写法往返；`coverage()` 认短名。

新写法的行：`模式: 变化|水平`、`因子: 短名 | 完整名 | 单位 | 数据线索 | 检索词`、`常量: 名字 | 数值 | 出处`、`公式: 营收 = …`、`公式: 毛利率 = …`。代码只查能不能算；保留每条公式 2–5 个因子、声明了必须用到、key 不撞、名字合法（在读声明行时就查，免得先报「未声明的名字」误导修改方向）。

- [ ] **Step 1: 取测试，确认红**

```bash
git checkout cd81447 -- tests/test_methodology.py
python -m pytest tests/test_methodology.py -q
```
Expected: `18 failed, 55 passed`（新写法测试与改写的两条旧提示词断言）。

- [ ] **Step 2: 取实现，确认绿；全量不回归**

```bash
git checkout cd81447 -- ir_agent/methodology.py
python -m pytest tests/test_methodology.py -q        # 73 passed
python -m pytest -q -m "not live" -W error           # 2312 passed, 8 skipped
```

- [ ] **Step 3: 变异验证**：`_parse_formulas` 里删掉 `_check_name(short, "因子短名")` → `test_算不了的写法被驳回[…原 料…]` 红（报成「未声明的名字」，正是这条要防的误导）。

- [ ] **Step 4: 提交**

```bash
git add ir_agent/methodology.py tests/test_methodology.py
git commit -m "methodology: 公式化写法（模式/因子/常量/公式）与旧 分解: 写法并存；旧写法翻译为公式"
```

<details><summary>完整 diff（cd81447）</summary>

```diff
diff --git a/ir_agent/methodology.py b/ir_agent/methodology.py
index 26545e5..fd62514 100644
--- a/ir_agent/methodology.py
+++ b/ir_agent/methodology.py
@@ -20,8 +20,13 @@ import re
 import unicodedata
 from dataclasses import dataclass, field
 
-from ir_agent.drivers import (TARGET_LABEL, Decomposition,
-                              DecompositionInvalid, Factor, Op, check_closure)
+from ir_agent.drivers import (MAX_FACTORS, MIN_FACTORS, TARGET_LABEL,
+                              Decomposition, DecompositionInvalid, Factor, Op,
+                              check_closure)
+from ir_agent.formula import (MODES, RESERVED, TARGETS as _FORMULA_TARGETS,
+                              Constant, FormulaInvalid, FormulaModel,
+                              from_decomps, parse_constant, parse_formula,
+                              valid_name)
 from ir_agent.lineparse import keyed, pick
 
 _DECOMP = keyed("分解")
@@ -29,6 +34,11 @@ _PITFALL = keyed("陷阱")
 # `keyed("覆盖")` 不会误中「未覆盖:」行：行首前缀只吃列表符号与加粗，吃不掉「未」。
 _COVER = keyed("覆盖")
 _UNCOVER = keyed("未覆盖")
+# 公式化写法（2026-10-14）。出现 `公式:` 行即按新写法解析，否则按旧的 `分解:`。
+_MODE = keyed("模式")
+_FACTOR = keyed("因子")
+_CONST = keyed("常量")
+_FORMULA = keyed("公式")
 
 _TARGETS = {"营收": "revenue", "毛利率": "gross_margin"}
 _OPS = {"乘": Op.MULTIPLY, "加": Op.ADD_PP}
@@ -71,6 +81,14 @@ class Methodology:
     # 辩论① 主题的覆盖声明：(「覆盖」|「未覆盖」, 主题, 因子名或理由)。
     # **写进 drivers.md 的原始定义块**，人改过再重跑不丢（spec 2026-10-13 ④）。
     coverage_decl: list[tuple[str, str, str]] = field(default_factory=list)
+    # 公式化后的分解式（`formula.FormulaModel`）。**下游一律读它。** 新写法直接
+    # 解析得到；旧的 `分解:` 写法解析后由 `formula.from_decomps` 翻译（`legacy`），
+    # 此时 `decompositions` 仍保留原样，旧文件的往返与旧闸门不变。
+    model: FormulaModel | None = None
+
+    def factors(self) -> list[Factor]:
+        """全部因子（去重、按声明顺序）。水平模式下同一个因子可被两条公式共用。"""
+        return list(self.model.factors) if self.model else _flat(self.decompositions)
 
     def of(self, target: str) -> Decomposition | None:
         return next((d for d in self.decompositions if d.target == target),
@@ -78,6 +96,118 @@ class Methodology:
 
 
 def parse(text: str) -> Methodology:
+    """解析分解式。出现 `公式:` 行按公式化写法，否则按旧的 `分解:` 写法。"""
+    m = _parse_formulas(text) if _FORMULA.search(text) else _parse_legacy(text)
+    m.pitfalls = [h.group(1).strip() for h in _PITFALL.finditer(text)]
+    for kind, rx in (("覆盖", _COVER), ("未覆盖", _UNCOVER)):
+        for h in rx.finditer(text):
+            th, _, rest = h.group(1).partition("|")
+            m.coverage_decl.append((kind, th.strip(), rest.strip()))
+    return m
+
+
+def _fields(raw: str) -> list[str]:
+    return [p.strip() for p in raw.split("|")]
+
+
+def _parse_formulas(text: str) -> Methodology:
+    """公式化写法。**代码只查能不能算**（spec 2026-10-14 一）：模式合法、名字
+    已声明且合法、公式在白名单内、两条都有；另保留因子个数（每条公式 2–5 个、
+    声明了就要用到）与 key 不撞车，待子项目二由裁决者接管。旧写法的运算匹配、
+    单位白名单、占比类、营收量级四道字面闸门在这里**不跑** —— 它们建立在
+    「乘法链/加法链」概念上，公式化后无意义。
+    """
+    modes = [h.group(1).strip() for h in _MODE.finditer(text)]
+    if len(modes) != 1:
+        raise MethodologyFailed(
+            f"要有且只有一行「模式: 变化」或「模式: 水平」，得到 {len(modes)} 行")
+    mode = pick(modes[0], list(MODES)) or modes[0]
+    factors: list[Factor] = []
+    keys: dict[str, str] = {}
+    for h in _FACTOR.finditer(text):
+        parts = _fields(h.group(1))
+        if len(parts) < 3 or not all(parts[:3]):
+            raise MethodologyFailed(
+                "因子一行应为「短名 | 完整名 | 单位 | 数据线索 | 检索词」，"
+                f"得到 {h.group(1)!r}")
+        short, label, unit = parts[0], parts[1], parts[2]
+        _check_name(short, "因子短名")
+        key = _slug(label)
+        if key in keys:
+            raise MethodologyFailed(
+                f"因子「{keys[key]}」与「{label}」slug 后都是 {key!r} —— 下游按"
+                "这个 key 对采集结果与工作簿行，撞车的那个会被错认。换一个更有"
+                "区分度的完整名。")
+        keys[key] = label
+        try:
+            factors.append(Factor(
+                key=key, label=label, unit=unit,
+                source_hint=parts[3] if len(parts) > 3 else "",
+                search_term=(_strip_term_label(parts[4])
+                             if len(parts) > 4 else ""),
+                short=short))
+        except DecompositionInvalid as e:
+            raise MethodologyFailed(str(e)) from e
+    consts: list[Constant] = []
+    for h in _CONST.finditer(text):
+        parts = _fields(h.group(1))
+        if len(parts) < 3 or not all(parts[:3]):
+            raise MethodologyFailed(
+                "常量一行应为「名字 | 数值 | 出处」，出处不能空 —— 常量是公式里"
+                f"不逐年判断的数，得说清它从哪来。得到 {h.group(1)!r}")
+        _check_name(parts[0], "常量名")
+        try:
+            consts.append(Constant(parts[0], parse_constant(parts[1]), parts[2]))
+        except FormulaInvalid as e:
+            raise MethodologyFailed(str(e)) from e
+    declared = {f.short for f in factors} | {c.name for c in consts}
+    formulas: dict = {}
+    for h in _FORMULA.finditer(text):
+        lhs, sep, rhs = h.group(1).replace("＝", "=").partition("=")
+        tgt = _FORMULA_TARGETS.get(lhs.strip())
+        if not sep or tgt is None:
+            raise MethodologyFailed(
+                f"公式一行应为「营收 = …」或「毛利率 = …」，得到 {h.group(1)!r}")
+        if tgt in formulas:
+            raise MethodologyFailed(
+                f"「{lhs.strip()}」的公式出现了不止一条 —— 只能给一条")
+        try:
+            formulas[tgt] = parse_formula(rhs, tgt, declared)
+        except FormulaInvalid as e:
+            raise MethodologyFailed(str(e)) from e
+    model = FormulaModel(mode=mode, factors=factors, constants=consts,
+                         formulas=formulas)
+    try:
+        model.validate()
+    except FormulaInvalid as e:
+        raise MethodologyFailed(str(e)) from e
+    for tgt in formulas:
+        n = len(model.factors_of(tgt))
+        if not MIN_FACTORS <= n <= MAX_FACTORS:
+            raise MethodologyFailed(
+                f"「{TARGET_LABEL[tgt]}」的公式引用了 {n} 个因子 —— 每条 "
+                f"{MIN_FACTORS}–{MAX_FACTORS} 个：一个不叫分解，超过五个人"
+                "核实不过来。")
+    used = {f.short for t in formulas for f in model.factors_of(t)}
+    unused = [f.label for f in factors if f.short not in used]
+    if unused:
+        raise MethodologyFailed(
+            f"声明了却没有任何公式用到的因子：{'、'.join(unused)} —— 删掉，或写进"
+            "公式。不进公式的因子不影响营收与毛利率，却要逐年填值、联网采集。")
+    return Methodology(model=model)
+
+
+def _check_name(name: str, what: str) -> None:
+    """在读到声明行时就查名字 —— 否则先报出来的会是「公式引用了未声明的名字」，
+    把模型引向错误的修改方向。"""
+    if not valid_name(name):
+        raise MethodologyFailed(
+            f"{what}「{name}」不合法 —— 不能含运算符、括号、空格，不能以数字开头，"
+            f"不能是 {'、'.join(sorted(RESERVED))}")
+
+
+def _parse_legacy(text: str) -> Methodology:
+    """旧的 `分解: 目标 | 乘/加 | 因子:单位:线索:检索词 …` 写法。旧闸门照旧。"""
     m = Methodology()
     for hit in _DECOMP.finditer(text):
         parts = [p.strip() for p in hit.group(1).split("|")]
@@ -178,11 +308,7 @@ def parse(text: str) -> Methodology:
                     "上「（收入端）/（成本端）」这类后缀）。")
             seen_keys[f.key] = (tgt_label, f.label)
 
-    m.pitfalls = [h.group(1).strip() for h in _PITFALL.finditer(text)]
-    for kind, rx in (("覆盖", _COVER), ("未覆盖", _UNCOVER)):
-        for h in rx.finditer(text):
-            th, _, rest = h.group(1).partition("|")
-            m.coverage_decl.append((kind, th.strip(), rest.strip()))
+    m.model = from_decomps(m.decompositions)
     return m
 
 
@@ -236,22 +362,50 @@ PROMPT = """你正在为一份 A 股公司研究报告做**方法论预研**。
 的**框架**，不是任何一个因子的取值。）材料为空时就只凭行业分类回答，
 那是允许的降级路径。
 
-给出两条**算术闭合**的分解式：
+先选**模式**（整份分解式只用一种）：
 
-`分解: 营收 | 乘 | <因子名>:<单位>:<数据线索>:<检索词> | <因子名>:<单位>:<数据线索>:<检索词> | …`
-`分解: 毛利率 | 加 | <因子名>:<单位>:<数据线索>:<检索词> | …`
+- **变化**：每个因子逐年填「变化」（增速、pp 变动……），公式描述「本年的变化 →
+  本年目标」。营收、毛利率的第 0 年取账本实际值。适合拿得到变化率、拿不到绝对
+  量的行业。
+- **水平**：每个因子逐年填「水平」（出货量、单价、单位成本……），公式直接写目标
+  的水平，代码用相邻两年算增速。第 0 年的营收、毛利率由各因子基期值代入公式算出
+  （不用报表营收，分子分母同口径）。适合量价数据拿得到的行业。
 
-尖括号连同里面的字一起换成你的内容。示例（某封测公司，仅示意格式）：
+再写因子、常量与两条公式：
+
+`模式: <变化|水平>`
+`因子: <短名> | <完整名> | <单位> | <数据线索> | <检索词>`
+`常量: <名字> | <数值> | <出处>`
+`公式: 营收 = <右边>`
+`公式: 毛利率 = <右边>`
 
-`分解: 营收 | 乘 | 有效产能同比增速:变化率:年报经营情况讨论:有效产能 | 平均单价变化率:变化率:研报价格跟踪:平均单价`
+尖括号连同里面的字一起换成你的内容。示例（某封测公司，仅示意格式）：
 
-**闭合的硬性要求**
-- 营收只能用「乘」，每个因子单位一律 `变化率`；毛利率只能用「加」，
-  单位一律 `pp`。
-- **pp 不能进乘法链**：稼动率 85%→82% 是 −3pp，但乘法因子的变化率是
-  82/85−1 = −3.5%，两者不等。
-- 每个因子必须写得出单位。**写不出单位的项就写不进来** ——
-  「客户结构优化」听着专业但不能乘，这类一律不要。
+`模式: 变化`
+`因子: 产能增速 | 有效产能同比增速 | 变化率 | 年报经营情况讨论 | 有效产能`
+`因子: 单价增速 | 平均单价同比变化率 | 变化率 | 研报价格跟踪 | 平均单价`
+`因子: 降价侵蚀 | 降价对毛利率的累计影响 | pp | 研报价格跟踪 | 降价`
+`因子: 折旧摊薄 | 稼动率提升对毛利率的累计摊薄 | pp | 年报成本构成 | 折旧`
+`公式: 营收 = 上期(营收) × (1 + 产能增速) × (1 + 单价增速)`
+`公式: 毛利率 = 基期(毛利率) + (降价侵蚀 + 折旧摊薄) / 100`
+
+**公式怎么写**（代码只管能不能算；算得对不对，由审核者与人工门看）
+- 只能用加减乘除、括号、数字、因子短名、常量名，以及 `上期(名字)`（上一年的
+  值）、`基期(名字)`（第 0 年的值）。名字可以是因子短名，也可以是 营收、毛利率
+  —— 但营收、毛利率只能写在 上期(…)/基期(…) 里。不提供 max、min、条件判断。
+- 短名不能含运算符、括号、空格，不能以数字开头。
+- 每条公式引用 2–5 个因子（一个不叫分解；超过五个人核实不过来）；声明了的因子
+  都要进公式。
+- 毛利率按小数计（26.5% 是 0.265），pp 进公式要 /100。营收的单位是亿元（只在你
+  用到绝对金额时才要紧）。
+- **换算写进公式，不要让代码猜**：变化模式下稼动率变了 −3pp，要换成营收的变化率
+  就写 `稼动率变动 / 100 / 上期稼动率`，`上期稼动率` 用 `常量:` 声明数值与出处。
+- **「累计」还是「逐年」在公式里看得见**：`基期(毛利率) + X / 100` 表示 X 是相对
+  基期累计的 pp；`上期(毛利率) + X / 100` 表示 X 是逐年再变的 pp。选一种，在
+  陷阱里写清楚。
+- **分母在公司内部的占比**（如高端产品收入占比）变动，不等于营收的变动 —— 它对
+  营收的影响经由均价体现；市占率（分母在公司之外）可以乘：营收 = 行业规模 ×
+  市占率。
 
 **检索词**
 
@@ -272,11 +426,6 @@ PROMPT = """你正在为一份 A 股公司研究报告做**方法论预研**。
 → 剥后缀 → 你给的检索词，共四级）在本轮语料里的实际命中次数算出来、印在
 产物顶部。宁可给一个搜不到的精确词（因子落「无数据」，诚实），也不要给
 一个到处都能搜到的泛词。
-- **营收乘法链里不要放「占比/比重/结构」类因子**：高端产品占比从 20% 到
-  22.4% 是 +12% 的相对变化，但营收不会因此多长 12%。结构升级对营收的影响
-  并进「平均单价/ASP」，对毛利率的影响放进加法链。市占率可以乘（营收 =
-  行业规模 × 市占率）。
-- 每条 2–5 个因子。一个不叫分解；超过五个人核实不过来。
 - 数据线索写明你**指望它来自哪里**：年报某一节 / 语料库某一篇 / 需要新采集。
   **若这个因子的数据就是账本里已有的某个 key**（见上面「账本里已有的 key」
   那一节，若有），在数据线索里写成 `[[key]]`，例如 `[[industry.revenue_yoy]]`
@@ -290,7 +439,7 @@ PROMPT = """你正在为一份 A 股公司研究报告做**方法论预研**。
 因子承载它** —— 某个主题多空分歧最大、而分解式里没有对应因子，下一轮辩论就
 无从给出三档取值、判断项也无从反映这个分歧。对每个主题写一行声明：
 
-`覆盖: <主题> | <承载它的因子名，与分解式里写的一字不差>`
+`覆盖: <主题> | <承载它的因子短名或完整名，与上面写的一字不差>`
 `未覆盖: <主题> | <为什么不能因子化>`
 
 代码会核对你声明的因子名是否真在分解式里，并把结果标注在产物顶部。
@@ -379,7 +528,15 @@ def _to_dsl(m: Methodology) -> str:
     """把 Methodology 变回预研阶段那种 `分解: ... | ...` 的 DSL 文本——
     与 `parse()` 互为逆操作，`from_markdown` 靠它把落盘文件读回来。"""
     lines = []
-    for d in m.decompositions:
+    if m.model is not None and not m.model.legacy:
+        mm = m.model
+        lines.append(f"模式: {mm.mode}")
+        lines += [f"因子: {f.short} | {f.label} | {f.unit} | {f.source_hint} "
+                  f"| {f.search_term}" for f in mm.factors]
+        lines += [f"常量: {c.name} | {c.value} | {c.source}" for c in mm.constants]
+        lines += [f"公式: {mm.describe(t)}" for t in ("revenue", "gross_margin")
+                  if t in mm.formulas]
+    for d in (m.decompositions if (m.model is None or m.model.legacy) else []):
         parts = [_TARGET_CN.get(d.target, d.target), _OP_CN.get(d.op, d.op.value)]
         # 四段写全。**少写检索词这一段，人工编辑 drivers.md 后重跑就会把它
         # 吃掉**——而「编辑这段文字再重跑即可覆盖」是写在文件末尾的承诺。
@@ -393,6 +550,25 @@ def _to_dsl(m: Methodology) -> str:
     return "\n".join(lines)
 
 
+def _flat(xs) -> list[Factor]:
+    """分解式列表 / `FormulaModel` / 因子列表 → 因子列表（按 key 去重、保序）。
+
+    旧调用方传 `m.decompositions`（每条带 `.factors`），新调用方传 `m.model`；
+    两者在这里归一，下游函数不必各写一套。"""
+    if xs is None:
+        return []
+    if isinstance(xs, FormulaModel):
+        xs = xs.factors
+    out: list[Factor] = []
+    seen: set[str] = set()
+    for x in xs:
+        for f in (getattr(x, "factors", None) or [x]):
+            if f.key not in seen:
+                seen.add(f.key)
+                out.append(f)
+    return out
+
+
 def theme_coverage(decomps: list, themes: list[str]) -> list[tuple[str, str]]:
     """辩论① 的每个主题，有没有被某个因子覆盖。**标注用，不阻断。**
 
@@ -411,17 +587,15 @@ def theme_coverage(decomps: list, themes: list[str]) -> list[tuple[str, str]]:
     要求覆盖。
     """
     out: list[tuple[str, str]] = []
+    factors = _flat(decomps)
     for th in themes:
         hit = ""
         if th:
-            for d in decomps:
-                for f in d.factors:
-                    blob = (f"{f.label}{getattr(f, 'source_hint', '')}"
-                            f"{getattr(f, 'search_term', '')}")
-                    if th in blob:
-                        hit = f.label
-                        break
-                if hit:
+            for f in factors:
+                blob = (f"{f.label}{getattr(f, 'source_hint', '')}"
+                        f"{getattr(f, 'search_term', '')}")
+                if th in blob:
+                    hit = f.label
                     break
         out.append((th, hit))
     return out
@@ -452,7 +626,10 @@ def coverage(m: Methodology, themes: list[str]) -> tuple[list[Cover], list[str]]
     """
     if not themes:
         return [], []
-    labels = {f.label for d in m.decompositions for f in d.factors}
+    factors = m.factors()
+    labels = {f.label for f in factors}
+    # 声明里写短名也认（公式化后模型更可能写短名），显示时换成完整名。
+    by_short = {f.short: f.label for f in factors if getattr(f, "short", "")}
     cov: dict[str, str] = {}
     unc: dict[str, str] = {}
     notes: list[str] = []
@@ -461,11 +638,11 @@ def coverage(m: Methodology, themes: list[str]) -> tuple[list[Cover], list[str]]
             notes.append(f"声明了清单外的主题「{th}」（{kind}: {rest}）")
         elif kind == "未覆盖":
             unc.setdefault(th, rest)
-        elif rest in labels:
-            cov.setdefault(th, rest)
+        elif rest in labels or rest in by_short:
+            cov.setdefault(th, by_short.get(rest, rest))
         else:
             notes.append(f"「{th}」声明由「{rest}」承载，但分解式里没有这个因子")
-    word = dict(theme_coverage(m.decompositions, themes))
+    word = dict(theme_coverage(factors, themes))
     out: list[Cover] = []
     for th in themes:
         if th in cov and th in unc:
@@ -510,10 +687,9 @@ def term_hits(decomps: list, *, sections=None, corpus=None
     hay = "\n".join(x for x in blobs if x)
 
     out: dict[str, list[tuple[str, int]]] = {}
-    for d in decomps:
-        for f in d.factors:
-            cands = _core_candidates(f.label, getattr(f, "search_term", "") or "")
-            out[f.label] = [(c, hay.count(c)) for c in cands]
+    for f in _flat(decomps):
+        cands = _core_candidates(f.label, getattr(f, "search_term", "") or "")
+        out[f.label] = [(c, hay.count(c)) for c in cands]
     return out
 
 
@@ -566,7 +742,9 @@ def to_markdown(m: Methodology, coverage=(), hits=None,
         lines += ["", "声明了的因子直接取账本数据、不再联网采集。**口径是否与"
                       "因子名一致（例如因子写的是某机构口径、账本是同业聚合口径）"
                       "由人判断。**", ""]
-    for d in m.decompositions:
+    if m.model is not None and not m.model.legacy:
+        lines += _formula_markdown(m.model)
+    for d in (m.decompositions if (m.model is None or m.model.legacy) else []):
         lines.append(f"## {d.describe()}")
         lines.append("")
         # 检索词要在人读表里露出来：它决定代码实际去搜什么词，而一个泛词
@@ -588,14 +766,40 @@ def to_markdown(m: Methodology, coverage=(), hits=None,
               "", _RAW_HEADING, "",
               "重跑第一阶段时会先读这里的代码块，而不是重新联网预研——"
               "**编辑下面这段文字再重跑即可覆盖**，语法与预研阶段完全一致："
-              "`分解: 目标 | 运算 | 因子名:单位:数据线索:检索词 | …`、"
-              "`陷阱: 一句话`。删掉这段代码块或改坏语法，重跑时会**明确"
+              "`模式:`、`因子: 短名 | 完整名 | 单位 | 数据线索 | 检索词`、"
+              "`常量: 名字 | 数值 | 出处`、`公式: 营收 = …`、"
+              "`陷阱: 一句话`（旧的 `分解: 目标 | 运算 | …` 写法也认）。删掉这段代码块或改坏语法，重跑时会**明确"
               "报错**而不是被静默忽略——不然你会以为自己的修改生效了，"
               "实际却被悄悄丢弃、重新联网跑出一份新的分解式。", "",
               "```text", _to_dsl(m), "```", ""]
     return "\n".join(lines)
 
 
+def _formula_markdown(mm: FormulaModel) -> list[str]:
+    """公式化分解式的人读部分：模式、因子表、常量表、两条公式。"""
+    lines = [f"## 模式：{mm.mode}", "",
+             ("每个因子逐年填**变化**（增速、pp 变动…），第 0 年营收与毛利率取账本。"
+              if mm.mode == "变化" else
+              "每个因子逐年填**水平**（量、价、成本…），第 0 年营收与毛利率由基期"
+              "因子值代入公式算出 —— 不用报表营收，分子分母同口径。"), ""]
+    lines += ["## 公式", ""]
+    lines += [f"- {mm.describe(t)}" for t in ("revenue", "gross_margin")
+              if t in mm.formulas]
+    lines.append("")
+    # 检索词要在人读表里露出来：它决定代码实际去搜什么词。
+    lines += ["## 因子", "",
+              "| 短名 | 因子 | 单位 | 数据线索 | 检索词 |",
+              "| --- | --- | --- | --- | --- |"]
+    lines += [f"| {f.short} | {f.label} | {f.unit} | {f.source_hint or '—'} "
+              f"| {f.search_term or '—'} |" for f in mm.factors]
+    lines.append("")
+    if mm.constants:
+        lines += ["## 常量", "", "| 名字 | 数值 | 出处 |", "| --- | --- | --- |"]
+        lines += [f"| {c.name} | {c.value} | {c.source} |" for c in mm.constants]
+        lines.append("")
+    return lines
+
+
 _CODE_BLOCK = re.compile(r"```(?:text)?\n(.*?)```", re.DOTALL)
 _TITLE = re.compile(r"^# 驱动因子分解（(.*?)）", re.MULTILINE)
 
diff --git a/tests/test_methodology.py b/tests/test_methodology.py
index 49de2dc..4643b58 100644
--- a/tests/test_methodology.py
+++ b/tests/test_methodology.py
@@ -2,6 +2,8 @@
 
 from __future__ import annotations
 
+from decimal import Decimal
+
 import pytest
 
 from ir_agent.drivers import Op
@@ -105,7 +107,8 @@ def test_提示词要求先搜索再回答():
     research(c, industry="集成电路封测", code="600584")
     p = c.prompts[0]
     assert "集成电路封测" in p and "搜索" in p
-    assert "闭合" in p and "写不出单位" in p
+    # 公式化后（2026-10-14）不再要求「乘/加闭合」，改为讲清模式与 上期/基期。
+    assert "模式" in p and "上期(名字)" in p and "基期(名字)" in p
 
 
 def test_落盘的markdown给人看得懂():
@@ -622,10 +625,12 @@ class TestSearchTermLabel:
         assert back.decompositions[0].factors[0].search_term == "Prismark"
 
     def test_格式行用占位写法而不是裸词(self):
-        """根因在提示词：`因子名:单位:数据线索:检索词` 读起来像「字段名:值」。"""
+        """根因在提示词：`因子名:单位:数据线索:检索词` 读起来像「字段名:值」。
+        公式化后（2026-10-14）格式行换成 `因子:` 行，仍用尖括号占位。"""
         from ir_agent import methodology
         assert "因子名:单位:数据线索:检索词" not in methodology.PROMPT
-        assert "<因子名>:<单位>:<数据线索>:<检索词>" in methodology.PROMPT
+        assert "`因子: <短名> | <完整名> | <单位> | <数据线索> | <检索词>`" \
+            in methodology.PROMPT
 
 
 # ══ 第二次调用试跑后的修订 ④：主题覆盖以模型声明为主 ═══════════════
@@ -694,3 +699,104 @@ class TestLedgerKeyDecl:
         assert "✓ [[industry.revenue_yoy@2025FY]]" in out
         assert "✗ foo.bar" in out
         assert "口径" in out
+
+
+
+# ══ 分解式公式化（2026-10-14 子项目一）══════════════════════════════
+
+_NEW = """模式: 变化
+因子: 份额增速 | 本司市占率（占全行业规模，非公司内部占比）同比增速 | 变化率 | [[company.market_share_delta@2025FY]] | 市场份额
+因子: 行业增速 | 行业（覆铜板）全球市场规模同比增速 | 变化率 | Prismark | Prismark
+因子: 提价 | 产品提价传导对毛利率的净贡献 | pp | 调价函 | 调价
+因子: 原料 | 核心原材料价格上涨对毛利率的侵蚀 | pp | 年报风险因素 | 原材料价格
+常量: 上期稼动率 | 78% | 年报经营情况讨论「产能利用率约78%」
+公式: 营收 = 上期(营收) × (1 + 行业增速) × (1 + 份额增速)
+公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100
+覆盖: 份额 | 份额增速
+陷阱: 提价与原料是同一次毛利率变动的两面
+"""
+
+_LEVEL = """模式: 水平
+因子: 量 | 覆铜板出货量 | 万平方米 | 年报产销量 | 产销量
+因子: 价 | 覆铜板平均单价 | 元/平方米 | 研报价格跟踪 | 平均单价
+因子: 本 | 覆铜板单位成本 | 元/平方米 | 年报成本构成 | 单位成本
+公式: 营收 = 量 × 价
+公式: 毛利率 = (价 − 本) ÷ 价
+"""
+
+
+class TestFormulaDSL:
+    def test_生益那条被字面闸门误拦的因子在新写法下能过(self):
+        """2026-10-14 第二次调用：「非公司内部占比」里的「占比」被
+        `_is_mix_factor` 拦了三次。新写法不跑字面闸门，交给裁决者。"""
+        from ir_agent.methodology import parse
+        m = parse(_NEW)
+        assert m.model.mode == "变化" and not m.model.legacy
+        assert [f.short for f in m.factors()] == ["份额增速", "行业增速", "提价", "原料"]
+        assert m.model.const("上期稼动率").value == Decimal("0.78")
+        assert m.factors()[0].search_term == "市场份额"
+        assert m.coverage_decl == [("覆盖", "份额", "份额增速")]
+        assert m.pitfalls and m.decompositions == []
+
+    def test_水平模式同一因子被两条公式共用(self):
+        from ir_agent.methodology import parse
+        m = parse(_LEVEL)
+        assert m.model.mode == "水平"
+        assert [f.short for f in m.model.factors_of("gross_margin")] == ["价", "本"]
+        assert [f.short for f in m.factors()] == ["量", "价", "本"]
+
+    def test_往返不丢(self):
+        from ir_agent.methodology import from_markdown, parse, to_markdown
+        m = parse(_NEW)
+        md = to_markdown(m)
+        assert "## 模式：变化" in md and "上期(营收)" in md and "上期稼动率" in md
+        back = from_markdown(md)
+        assert [(f.short, f.label, f.unit, f.source_hint, f.search_term)
+                for f in back.factors()] == \
+            [(f.short, f.label, f.unit, f.source_hint, f.search_term)
+             for f in m.factors()]
+        assert back.model.describe("gross_margin") == m.model.describe("gross_margin")
+        assert back.model.constants == m.model.constants
+        assert back.coverage_decl == m.coverage_decl and back.pitfalls == m.pitfalls
+
+    def test_旧写法仍然认_并翻译成公式(self):
+        from ir_agent.methodology import parse
+        m = parse(_DSL)
+        assert m.decompositions and m.model.legacy
+        assert m.model.describe("gross_margin").startswith("毛利率 = 基期(毛利率)+(")
+
+    @pytest.mark.parametrize("text,frag", [
+        (_NEW.replace("模式: 变化\n", ""), "模式"),
+        (_NEW.replace("模式: 变化", "模式: 混合"), "模式只能是"),
+        (_NEW.replace("公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100\n", ""),
+         "缺少 毛利率"),
+        (_NEW.replace("(提价 + 原料)", "(提价 + 原料 + 没声明)"), "未声明的名字「没声明」"),
+        (_NEW.replace("(提价 + 原料) / 100", "提价 / 100"), "引用了 1 个因子"),
+        (_NEW.replace("常量: 上期稼动率 | 78% | 年报经营情况讨论「产能利用率约78%」",
+                      "常量: 上期稼动率 | 78% |"), "出处不能空"),
+        (_NEW.replace("因子: 原料 |", "因子: 原 料 |"), "不合法"),
+        (_NEW + "公式: 营收 = 上期(营收)\n", "不止一条"),
+        (_NEW.replace("公式: 营收", "公式: 净利"), "营收 = …"),
+    ])
+    def test_算不了的写法被驳回(self, text, frag):
+        from ir_agent.methodology import MethodologyFailed, parse
+        with pytest.raises(MethodologyFailed, match=frag):
+            parse(text)
+
+    def test_声明了却没用到的因子被驳回(self):
+        from ir_agent.methodology import MethodologyFailed, parse
+        with pytest.raises(MethodologyFailed, match="没有任何公式用到"):
+            parse(_NEW.replace("陷阱:", "因子: 闲置 | 闲置因子 | pp | x | 闲置因子\n陷阱:"))
+
+    def test_覆盖声明写短名也认_显示完整名(self):
+        from ir_agent.methodology import coverage, parse
+        cov, notes = coverage(parse(_NEW), ["份额"])
+        assert cov[0].mark == "✓" and "非公司内部占比" in cov[0].detail
+        assert notes == []
+
+    def test_命中次数与词面覆盖接受新写法(self):
+        from ir_agent.methodology import parse, term_hits, theme_coverage
+        m = parse(_NEW)
+        hits = term_hits(m.model, sections="Prismark 数据显示。调价函。")
+        assert dict(hits["行业（覆铜板）全球市场规模同比增速"])["Prismark"] == 1
+        assert dict(theme_coverage(m.model, ["成本"]))["成本"] == ""
```

</details>

---

### Task 3: 判断项环节接入公式引擎

**Files:**
- Modify: `ir_agent/assume.py`、`ir_agent/formula.py`（`flat_factors`、`as_model`、只给一条公式的旧文件也能推演）、`ir_agent/methodology.py`（`_flat` 移到 formula）、`tests/test_assume.py`
- Create: `tests/test_assume_formula.py`、`tests/test_assume_reconcile.py`

**Interfaces:**
- `assume` 各入口的 `decomps` 参数接受 None / 旧分解式列表 / `FormulaModel`（`formula.as_model` 归一）。
- `AssumptionSet.model`、`.base_values: {短名: BaseValue}`、`.base_revenue`（亿元）、`._m()`。
- `BaseValue(value, bases, note, source="账本"|"AI", basis_unverified)`。
- 门③ 新行：`基期: <短名> | <值> | <依据>`；因子按短名或完整名认。
- `synthesize(a, case, base_margin, bases=None)`、`margin_bases(a, base_margin, anchor)` 接口不变，内部走引擎。
- `_reconcile_model(model, ledger, as_of)`；`has_model(a)`；`to_driver_block` 产出 schema 2：`{schema, mode, factors[{key,short,label,unit}], constants, formulas{target: tokens}, formula_text, base{revenue, margin, factors}, values, [base_levels, anchor_y1]}`。
- assumptions.json schema 2：新增 `model`、`base_values`。

- [ ] **Step 1: 取测试，确认红**

```bash
git checkout 514487b -- tests/test_assume.py
git checkout 179bbae -- tests/test_assume_formula.py
git checkout 0f315db -- tests/test_assume_reconcile.py
python -m pytest tests/test_assume.py tests/test_assume_formula.py tests/test_assume_reconcile.py -q
```
Expected: `20 failed, 151 passed`（4 条旧驱动块结构断言按 schema 2 改写 + 新写法 17 条中尚未实现的部分）。
`test_assume_reconcile.py` 在改动前就绿 —— 它守的是「翻译后与旧引擎逐位相同」，有没有用靠 Step 3 的变异证明。

- [ ] **Step 2: 取实现，确认绿；全量不回归**

```bash
git checkout 514487b -- ir_agent/assume.py ir_agent/formula.py ir_agent/methodology.py
python -m pytest tests/test_assume.py tests/test_assume_formula.py tests/test_assume_reconcile.py tests/test_formula.py tests/test_methodology.py -q   # 280 passed
python -m pytest -q -m "not live" -W error           # 2330 passed, 8 skipped
```

- [ ] **Step 3: 变异验证**
  - `_parse_factors` 里删掉 `_unit_guard(f.unit, label, parts[2:5])` → `test_其他单位不带百分号[110%]`、`[110bp]` 与旧的 `test_pp因子写百分号被拒绝`、`test_变化率因子裸数被拒绝` 红；
  - `_check_factors` 里 `if lacking:` 改成 `if False:` → `test_缺基期明确驳回` 红；
  - `_reconcile_model` 里 `scale = Decimal(1) if model.legacy else rev[prev_p]` 改为恒取 `rev[prev_p]` → `test_assume_reconcile.py` 红。**这条测试必须用真实量级的数**：写计划时随机抽样实测，按真实营收作尺度时约 45%（9094/20000）的样本末位与旧 `compose` 不同，按 1 作尺度 0/20000；夹具数值位数短时乘除无舍入，抓不到。

- [ ] **Step 4: 提交**

```bash
git add ir_agent/assume.py ir_agent/formula.py ir_agent/methodology.py tests/test_assume.py tests/test_assume_formula.py tests/test_assume_reconcile.py
git commit -m "assume: 判断项接入公式引擎 —— 短名、基期行、按单位防呆、锚定平移通用化、历史对账按 [[key]]、assumptions.json 与驱动块 schema 2"
```

<details><summary>完整 diff（tests/test_assume_reconcile.py，0f315db）</summary>

```diff
diff --git a/tests/test_assume_reconcile.py b/tests/test_assume_reconcile.py
new file mode 100644
index 0000000..c4b1d08
--- /dev/null
+++ b/tests/test_assume_reconcile.py
@@ -0,0 +1,35 @@
+"""旧乘/加分解式经公式引擎做历史对账，与改动前逐位相同（spec 2026-10-14 五）。"""
+from __future__ import annotations
+
+from datetime import date
+from decimal import Decimal as D
+
+from ir_agent import assume
+from ir_agent.ledger import Fact, FactLedger, Method
+
+AS_OF = date(2026, 10, 14)
+
+
+class TestLegacyReconcile:
+    def test_旧乘法链历史对账与旧compose逐位相同_真实量级(self):
+        """真实营收是十几位的数，因子历史变化率是 28 位长小数 —— 若按真实营收
+        作尺度去乘再除，Decimal 末位舍入会让结果与旧 `drivers.compose` 不再逐位
+        相同。旧乘法链与尺度无关，对账时按 1 代入（`_reconcile_model` 里的
+        `scale`）。夹具数值位数短时乘除无舍入，这条必须用真实量级的数。"""
+        from ir_agent.drivers import Decomposition, Factor, Op, compose
+
+        d = Decomposition("revenue", Op.MULTIPLY, [Factor("cap", "产能", "变化率"),
+                                                    Factor("asp", "单价", "变化率")])
+        led = FactLedger()
+        # 随机抽样实测：按真实营收作尺度时约 45% 的样本末位不同；这一组是其中之一。
+        data = {"cap": ("224015.726", "627805.789"),
+                "asp": ("947761.234", "577525.846"),
+                "revenue": ("40271366990.43", "42110587245.18")}
+        for k, (v0, v1) in data.items():
+            for per, v in (("2024FY", v0), ("2025FY", v1)):
+                led.put(Fact(key=k, value=D(v), unit="x", period=per,
+                             as_of=date(2026, 4, 1), source_id="t",
+                             method=Method.REPORTED))
+        res = assume._reconcile_decomp(d, led, AS_OF)["residuals"]
+        vals = {k: D(data[k][1]) / D(data[k][0]) - 1 for k in ("cap", "asp")}
+        assert res[0].composed == compose(d, vals)
```
</details>

<details><summary>完整 diff（cd81447 → 179bbae）</summary>

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index 3f2ad94..134c8a3 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -31,8 +31,13 @@ from enum import Enum
 
 from ir_agent import gaps
 from ir_agent.basis import Basis, BasisKind, InvalidBasis, validate
+from ir_agent.formula import (FormulaError, FormulaInvalid, FormulaModel,
+                              as_model, evaluate, flat_factors, from_decomps,
+                              parse_formula, simulate, Constant as _Constant,
+                              parse_constant)
 from ir_agent.drivers import (Decomposition, DecompositionInvalid, Factor, Op,
-                              TARGET_LABEL, check_dimensions, compose, reconcile)
+                              TARGET_LABEL, Residual, check_dimensions, compose,
+                              reconcile)
 from ir_agent.gaps import FactorData
 from ir_agent.ledger import FactLedger, LookAheadError
 from ir_agent.lineparse import keyed, pick
@@ -67,6 +72,10 @@ _LINE = keyed("假设")
 _QUOTE = re.compile(r"[「\"'“](.+?)[」”\"']", re.DOTALL)
 _REF = re.compile(r"\[\[[^\]]+\]\]")
 _NUM = re.compile(r"-?\d+(?:\.\d+)?%?")
+# 水平模式（或公式用到 上期/基期(因子) 时）因子第 0 年的取值，AI 在门③ 补填。
+_BASE_LINE = keyed("基期")
+# 按百分比填的单位：必须带 %。pp 不带 %；其他单位（万吨、元…）按原数、不带 %。
+_PCT_UNITS = frozenset({"变化率", "%", "百分比"})
 
 
 def _normalize_num(raw: str) -> str:
@@ -154,6 +163,18 @@ class Row:
     basis_flag: str = ""
 
 
+@dataclass
+class BaseValue:
+    """一个因子第 0 年的取值。`source`：「账本」（数据线索里 `[[key]]` 声明且
+    账本有上年值，代码取）或「AI」（门③ 由 AI 补填并给依据）。"""
+
+    value: Decimal
+    bases: list = field(default_factory=list)
+    note: str = ""
+    source: str = "AI"
+    basis_unverified: str = ""
+
+
 def _decomp_to_dict(d: Decomposition) -> dict:
     return {"target": d.target, "op": d.op.value,
             "factors": [{"key": f.key, "label": f.label, "unit": f.unit,
@@ -178,31 +199,81 @@ def to_json(a: "AssumptionSet", **extra) -> dict:
     同时记下 AI 的原始取值: 人改过之后，原来的依据未必还支撑新数字，
     那件事必须被看见而不是悄悄放行。
 
-    **分解模式下 `decomps` 与每行的 `factor_key` 必须一起存下来。**
-    漏掉它们不会立刻报错 —— 第二阶段照样能读出一个 AssumptionSet，
-    只是 `factor_key` 全变空串、`decomps` 变空列表：`synthesize()` 按
-    factor_key 分组，空 key 的行会被整体滤掉，`apply_to`/`to_driver_block`
-    因此拿到空列表，人在第一阶段批准的因子分解在第二阶段悄悄消失，
-    却不会有任何异常提示——这正是本模块要防的「静默出错」。
+    **分解模式下分解式与每行的 `factor_key` 必须一起存下来。** 漏掉它们
+    不会立刻报错 —— 第二阶段照样能读出一个 AssumptionSet，只是因子分解
+    悄悄消失，这正是本模块要防的「静默出错」。
+
+    **schema 2（2026-10-14 公式化）**：公式化的分解式存进 `model`（模式、因子、
+    常量、两条公式），因子第 0 年取值存进 `base_values`。旧的乘/加分解式仍存
+    `decomps`，读回时翻译成公式（`formula.from_decomps`）。
     """
-    return {"labels": a.labels,
-            "decomps": [_decomp_to_dict(d) for d in a.decomps],
-            "rows": [{"driver": r.driver, "year": r.year,
-                      "bear": str(r.bear), "base": str(r.base),
-                      "bull": str(r.bull), "note": r.note,
-                      "factor_key": r.factor_key,
-                      "bases": [b.to_dict() for b in r.bases],
-                      "proposed": {"bear": str(r.bear), "base": str(r.base),
-                                   "bull": str(r.bull)}}
-                     for r in a.rows],
-            **extra}
+    m = a.model
+    out = {"schema": 2, "labels": a.labels,
+           "decomps": [_decomp_to_dict(d) for d in a.decomps],
+           "rows": [{"driver": r.driver, "year": r.year,
+                     "bear": str(r.bear), "base": str(r.base),
+                     "bull": str(r.bull), "note": r.note,
+                     "factor_key": r.factor_key,
+                     "bases": [b.to_dict() for b in r.bases],
+                     "proposed": {"bear": str(r.bear), "base": str(r.base),
+                                  "bull": str(r.bull)}}
+                    for r in a.rows],
+           **extra}
+    if m is not None and not m.legacy:
+        out["model"] = _model_to_dict(m)
+    if a.base_values:
+        out["base_values"] = {
+            k: {"value": str(v.value), "bases": [b.to_dict() for b in v.bases],
+                "note": v.note, "source": v.source}
+            for k, v in a.base_values.items()}
+    return out
+
+
+def _model_to_dict(m: FormulaModel) -> dict:
+    return {"mode": m.mode,
+            "factors": [{"key": f.key, "label": f.label, "unit": f.unit,
+                         "source_hint": f.source_hint,
+                         "search_term": f.search_term, "short": f.short}
+                        for f in m.factors],
+            "constants": [{"name": c.name, "value": str(c.value),
+                           "source": c.source} for c in m.constants],
+            "formulas": {t: fm.text for t, fm in m.formulas.items()}}
+
+
+def _model_from_dict(d: dict) -> FormulaModel:
+    """读回公式化分解式。**构造即校验**：人手改坏的公式在这里给一句看得懂的
+    话（「请重跑第一阶段」），不让第二阶段崩成 traceback。"""
+    factors = [Factor(key=f["key"], label=f["label"], unit=f["unit"],
+                      source_hint=f.get("source_hint", ""),
+                      search_term=f.get("search_term", ""), short=f["short"])
+               for f in d.get("factors") or []]
+    consts = [_Constant(c["name"], Decimal(c["value"]), c.get("source", ""))
+              for c in d.get("constants") or []]
+    declared = {f.short for f in factors} | {c.name for c in consts}
+    try:
+        m = FormulaModel(
+            mode=d.get("mode", ""), factors=factors, constants=consts,
+            formulas={t: parse_formula(txt, t, declared)
+                      for t, txt in (d.get("formulas") or {}).items()})
+        m.validate()
+    except FormulaInvalid as e:
+        raise AssumeFailed(f"判断项里的分解式已不合法：{e}　——请重跑第一阶段，"
+                           "重新预研分解式后再填判断项。") from e
+    return m
 
 
 def from_json(d: dict, code: str = "") -> tuple["AssumptionSet", list[str]]:
-    """读回，并报出人改过哪些值。"""
+    """读回，并报出人改过哪些值。旧格式（没有 `model`）的乘/加分解式照旧读回。"""
     decomps = [_decomp_from_dict(x) for x in (d.get("decomps") or [])]
+    model = _model_from_dict(d["model"]) if d.get("model") else None
     a = AssumptionSet(code=code or d.get("code", ""),
-                      labels=d.get("labels") or [], decomps=decomps)
+                      labels=d.get("labels") or [], decomps=decomps,
+                      model=model)
+    for k, v in (d.get("base_values") or {}).items():
+        a.base_values[k] = BaseValue(
+            value=Decimal(v["value"]),
+            bases=[Basis.from_dict(b) for b in (v.get("bases") or [])],
+            note=v.get("note", ""), source=v.get("source", "AI"))
     edits: list[str] = []
     for x in d.get("rows") or []:
         r = Row(driver=x["driver"], year=int(x["year"]),
@@ -214,23 +285,22 @@ def from_json(d: dict, code: str = "") -> tuple["AssumptionSet", list[str]]:
         p = x.get("proposed") or {}
         for case in ("bear", "base", "bull"):
             if case in p and Decimal(p[case]) != getattr(r, case):
-                cn = _row_label(r, decomps)
+                cn = _row_label(r, a._m() or decomps)
                 edits.append(
                     f"{cn} 第{r.year}年 {case}：模型给 {p[case]}，"
                     f"核实后改为 {getattr(r, case)}")
     return a, edits
 
 
-def _row_label(r: "Row", decomps: list[Decomposition]) -> str:
+def _row_label(r: "Row", decomps) -> str:
     """人改过的值要报得出名字。分解模式下 `r.driver` 是 revenue/
     gross_margin，不是因子本身 —— 要报的是具体改了哪个因子（否则改了
     「产能」和改了「单价」在提示里会长得一模一样，都写成同一个笼统的
-    目标名）。"""
+    目标名）。`decomps` 可以是旧分解式列表，也可以是 `FormulaModel`。"""
     if r.factor_key:
-        for d in decomps:
-            for f in d.factors:
-                if f.key == r.factor_key:
-                    return f.label
+        for f in flat_factors(decomps):
+            if f.key == r.factor_key:
+                return f.label
         return r.factor_key
     return "营收增速" if r.driver == "growth" else "毛利率"
 
@@ -275,6 +345,18 @@ class AssumptionSet:
     # 降级时最后一次的**完整**模型输出，供调用方落盘排查（见 AssumeFailed.raw）。
     # 不进 describe()——它是给人批准用的清单，不该混进几十行失败草稿。
     degraded_raw: str = ""
+    # 公式化后的分解式（`formula.FormulaModel`）。新写法直接给；旧的 `decomps`
+    # 由 `_m()` 惰性翻译。**合成、清单、落盘一律经它。**
+    model: object = None
+    # 因子第 0 年的取值 {短名: BaseValue}（水平模式，或公式用到 上期/基期(因子)）。
+    base_values: dict = field(default_factory=dict)
+    # 第 0 年营收（亿元，与工作簿同口径）。旧的乘法链与尺度无关，缺省按 1。
+    base_revenue: Decimal | None = None
+
+    def _m(self) -> FormulaModel | None:
+        if self.model is None and self.decomps:
+            self.model = from_decomps(list(self.decomps))
+        return self.model
 
     def path(self, driver: str, case: Case) -> list[Decimal]:
         got = sorted((r for r in self.rows if r.driver == driver),
@@ -318,7 +400,10 @@ class AssumptionSet:
                       f"{(c.rationale or c.objection).strip()[:140]}"
                       for c in up]
             lines += [""]
-        if self.decomps:
+        m = self._m()
+        if m is not None and not m.legacy:
+            lines += self._describe_formula()
+        elif m is not None:
             lines += self._describe_factors()
         else:
             lines += self._describe_scalar()
@@ -353,69 +438,162 @@ class AssumptionSet:
                         f" —— {d.describe()}")
             lines.append("")
             for f in d.factors:
-                got = sorted((r for r in self.rows if r.factor_key == f.key),
-                             key=lambda r: r.year)
-                lines.append(f"### {f.label}（{f.unit}）")
-                lines.append("")
-                lines.append("| 年 | 悲观 | 中性 | 乐观 | 理由 | 依据 |")
-                lines.append("| --- | --- | --- | --- | --- | --- |")
-                if not got:
-                    # **遗漏必须可见。** 因子在分解式里，却一行取值都
-                    # 没有——不能悄悄不显示，得让人一眼看出这里空着。
-                    lines.append("| — | — | — | — | **未填写** | — |")
-                for r in got:
-                    lab = self.labels[r.year - 1] \
-                        if r.year <= len(self.labels) else f"第{r.year}年"
-                    # 出口二落地为空 `bases`——不能让这一格跟"有依据"的
-                    # 行看起来一样（一样都是拿 join 拼一遍，空列表拼出的
-                    # 是空字符串，读者会当成"依据列忘了填"而不是"这本来
-                    # 就是没有依据的估计"）。必须显式标出来，一眼分得清。
-                    #
-                    # `basis_unverified` 非空——引文逐字核验不过、但因子
-                    # 模式下已放宽为标注而非驳回：这一格同样不能跟"核验
-                    # 通过"的行长得一样，否则放宽换来的就是"审核形同虚
-                    # 设却连标注都看不见"。引文本身（quote）仍然原样显示
-                    # ——它不是编造，只是没能逐字对应，人要看到具体写的
-                    # 是什么才能判断这段转述靠不靠谱。
-                    #
-                    # **理由自称「无数据（估计）」的行，挂了占位符也还是
-                    # 估计。** 600584 实跑当场抓到：4 个因子的理由白纸黑字
-                    # 写着无数据、取值为估计，依据列却挂着一个真实存在、
-                    # 方向上沾边的账本占位符（折旧摊销之于固定成本分摊、
-                    # 营收增速之于扩产节奏），于是判据 `not r.bases` 认为
-                    # 它们"有依据"，底部那份「没有证据支撑」的名单里五个
-                    # 该在的因子只剩一个。等于用一个合法但不直接支撑取值的
-                    # 占位符，把"这个数是拍的"从清单上抹掉——而清单是人工
-                    # 批准唯一能看的东西。占位符本身照常显示（它是真的、
-                    # 有参考价值），只是改称「方向性参照」，不再冒充
-                    # 「这个取值的证据」。
-                    est = _looks_like_estimate(r.note)
-                    parts: list[str] = []
-                    if est or not r.bases:
-                        parts.append("⚠ 估计（无数据支撑）")
-                    if r.basis_flag:
-                        parts.append("⚠ " + r.basis_flag)
-                    if r.bases:
-                        body = "；".join(b.describe() for b in r.bases)
-                        if r.basis_unverified:
-                            parts.append("⚠ 依据未能逐字核验：" + body)
-                        elif est:
-                            parts.append("方向性参照：" + body)
-                        else:
-                            parts.append(body)
-                    ref = "；".join(parts)
-                    lines.append(
-                        f"| {lab} | {_fmt_factor(r.bear, f.unit)} "
-                        f"| {_fmt_factor(r.base, f.unit)} "
-                        f"| {_fmt_factor(r.bull, f.unit)} "
-                        f"| {r.note or '—'} | {ref} |")
-                lines.append("")
+                lines += self._factor_table(f)
             lines += self._describe_composed(d)
             lines += self._describe_residuals(d)
         lines += self._describe_estimated()
         lines += self._describe_unverified()
         return lines
 
+    def _factor_table(self, f) -> list[str]:
+        """一个因子的逐年表。新旧两种清单共用。"""
+        lines: list[str] = []
+        got = sorted((r for r in self.rows if r.factor_key == f.key),
+                     key=lambda r: r.year)
+        lines.append(f"### {f.label}（{f.unit}）")
+        lines.append("")
+        lines.append("| 年 | 悲观 | 中性 | 乐观 | 理由 | 依据 |")
+        lines.append("| --- | --- | --- | --- | --- | --- |")
+        if not got:
+            # **遗漏必须可见。** 因子在分解式里，却一行取值都
+            # 没有——不能悄悄不显示，得让人一眼看出这里空着。
+            lines.append("| — | — | — | — | **未填写** | — |")
+        for r in got:
+            lab = self.labels[r.year - 1] \
+                if r.year <= len(self.labels) else f"第{r.year}年"
+            # 出口二落地为空 `bases`——不能让这一格跟"有依据"的
+            # 行看起来一样（一样都是拿 join 拼一遍，空列表拼出的
+            # 是空字符串，读者会当成"依据列忘了填"而不是"这本来
+            # 就是没有依据的估计"）。必须显式标出来，一眼分得清。
+            #
+            # `basis_unverified` 非空——引文逐字核验不过、但因子
+            # 模式下已放宽为标注而非驳回：这一格同样不能跟"核验
+            # 通过"的行长得一样，否则放宽换来的就是"审核形同虚
+            # 设却连标注都看不见"。引文本身（quote）仍然原样显示
+            # ——它不是编造，只是没能逐字对应，人要看到具体写的
+            # 是什么才能判断这段转述靠不靠谱。
+            #
+            # **理由自称「无数据（估计）」的行，挂了占位符也还是
+            # 估计。** 600584 实跑当场抓到：4 个因子的理由白纸黑字
+            # 写着无数据、取值为估计，依据列却挂着一个真实存在、
+            # 方向上沾边的账本占位符（折旧摊销之于固定成本分摊、
+            # 营收增速之于扩产节奏），于是判据 `not r.bases` 认为
+            # 它们"有依据"，底部那份「没有证据支撑」的名单里五个
+            # 该在的因子只剩一个。等于用一个合法但不直接支撑取值的
+            # 占位符，把"这个数是拍的"从清单上抹掉——而清单是人工
+            # 批准唯一能看的东西。占位符本身照常显示（它是真的、
+            # 有参考价值），只是改称「方向性参照」，不再冒充
+            # 「这个取值的证据」。
+            est = _looks_like_estimate(r.note)
+            parts: list[str] = []
+            if est or not r.bases:
+                parts.append("⚠ 估计（无数据支撑）")
+            if r.basis_flag:
+                parts.append("⚠ " + r.basis_flag)
+            if r.bases:
+                body = "；".join(b.describe() for b in r.bases)
+                if r.basis_unverified:
+                    parts.append("⚠ 依据未能逐字核验：" + body)
+                elif est:
+                    parts.append("方向性参照：" + body)
+                else:
+                    parts.append(body)
+            ref = "；".join(parts)
+            lines.append(
+                f"| {lab} | {_fmt_factor(r.bear, f.unit)} "
+                f"| {_fmt_factor(r.base, f.unit)} "
+                f"| {_fmt_factor(r.bull, f.unit)} "
+                f"| {r.note or '—'} | {ref} |")
+        lines.append("")
+        return lines
+
+    def _describe_formula(self) -> list[str]:
+        """公式化分解式的清单（spec 2026-10-14 三）：公式、常量、基期值、每个
+        因子一张逐年表、三档逐年的合成结果、历史对账。"""
+        m = self.model
+        lines = [f"## 分解式（{m.mode}模式）", ""]
+        lines += [f"- {m.describe(t)}" for t in ("revenue", "gross_margin")
+                  if t in m.formulas]
+        lines.append("")
+        if m.constants:
+            lines += ["**常量**", ""]
+            lines += [f"- {c.name} = {c.value}（{c.source}）" for c in m.constants]
+            lines.append("")
+        need = m.needs_base()
+        if need:
+            lines += ["**基期值（第 0 年）**", "",
+                      "| 因子 | 取值 | 来源 | 依据 |", "| --- | --- | --- | --- |"]
+            for f in need:
+                bv = self.base_values.get(f.short)
+                if bv is None:
+                    lines.append(f"| {f.label} | **未填写** | — | — |")
+                    continue
+                ref = "；".join(b.describe() for b in bv.bases)
+                if not bv.bases or _looks_like_estimate(bv.note):
+                    ref = "⚠ 估计（无数据支撑）" + (f"；{ref}" if ref else "")
+                if bv.basis_unverified:
+                    ref = "⚠ 依据未能逐字核验：" + ref
+                lines.append(f"| {f.label} | {_fmt_factor(bv.value, f.unit)} "
+                             f"| {bv.source} | {ref}{('（' + bv.note + '）') if bv.note else ''} |")
+            lines.append("")
+        for f in m.factors:
+            lines += self._factor_table(f)
+        lines += self._describe_synthesis()
+        for t in ("revenue", "gross_margin"):
+            lines += self._residual_lines(t)
+        lines += self._describe_estimated()
+        lines += self._describe_unverified()
+        return lines
+
+    def _describe_synthesis(self) -> list[str]:
+        lines = ["## 合成结果（代码按公式算出，不是模型直接填的数）", ""]
+        m = self.model
+        if m.mode == "变化" and self.base_margin is None:
+            return lines + ["基期毛利率未知，暂无法显示合成结果——因子取值仍已"
+                            "生效，只是这里算不出用于展示的数。", ""]
+        try:
+            got = {c: synthesize(self, c, self.base_margin, self.margin_bases)
+                   for c in (Case.BEAR, Case.BASE, Case.BULL)}
+        except AssumeFailed as e:
+            return lines + [f"算不出来：{e}", ""]
+        lines += ["| 年 | 营收增速 悲观 | 中性 | 乐观 | 毛利率 悲观 | 中性 | 乐观 |",
+                  "| --- | --- | --- | --- | --- | --- | --- |"]
+        for y in range(len(got[Case.BASE][0])):
+            lab = self.labels[y] if y < len(self.labels) else f"第{y+1}年"
+            g = [got[c][0][y] for c in (Case.BEAR, Case.BASE, Case.BULL)]
+            mg = [got[c][1][y] for c in (Case.BEAR, Case.BASE, Case.BULL)]
+            lines.append(f"| {lab} | " + " | ".join(f"{x:.2%}" for x in g)
+                         + " | " + " | ".join(f"{x:.2%}" for x in mg) + " |")
+        lines.append("")
+        lagged = {n for _, n in m.formulas["gross_margin"].lagged()}
+        if self.margin_bases and "毛利率" not in lagged:
+            lines += ["⚠ 半年报锚定只影响第 1 年：毛利率公式不读 上期/基期(毛利率)，"
+                      "第 2 年起承接不上锚定值。", ""]
+        return lines
+
+    def _residual_lines(self, target: str) -> list[str]:
+        info = self.residuals.get(target)
+        if not info:
+            return []
+        cn = TARGET_LABEL.get(target, target)
+        lines = [f"**{cn}历史回算残差**（仅供核对分解式抓没抓住主要矛盾，"
+                 "不作为通过/不通过的判据）", ""]
+        res = info.get("residuals") or []
+        if res:
+            lines.append("| 期间 | 合成 | 实际 | 残差 |")
+            lines.append("| --- | --- | --- | --- |")
+            for r in res:
+                lines.append(f"| {r.period} | {r.composed:.2%} "
+                             f"| {r.actual:.2%} | {r.gap:+.2%} |")
+            lines.append("")
+        no_hist = info.get("no_history") or []
+        if no_hist:
+            lines.append(f"无历史校验：{'、'.join(no_hist)}"
+                         "（账本里取不到足够的历史序列，不是没有偏差，"
+                         "是这里没法核验）")
+            lines.append("")
+        return lines
+
     def _describe_unverified(self) -> list[str]:
         """**遗漏必须可见**——这条约束不能因为放宽引文审核而丢。
 
@@ -450,29 +628,15 @@ class AssumptionSet:
         """展示用的合成结果，**按目标单独判断能不能算**，不是「任一
         分解式缺基期就全部显示不出来」（I7）。
 
-        营收是乘法链 Π(1+r)−1，永远算得出来，不需要基期；毛利率是加法
-        链，缺基期却硬要合成，会显示一个用假基期算出来、看起来正常的
-        错数——比不显示更糟，所以只有这一条线才返回 None 让调用方改
-        显示说明文字，而不会连坐到营收那一节。
+        营收增速永远算得出来，不需要基期毛利率；毛利率缺基期却硬要合成，会
+        显示一个用假基期算出来、看起来正常的错数——比不显示更糟，所以只有
+        这一条线才返回 None 让调用方改显示说明文字。
         """
-        if d.op is not Op.MULTIPLY and self.base_margin is None:
+        if d.target != "revenue" and self.base_margin is None:
             return None
-        attr = {Case.BULL: "bull", Case.BASE: "base",
-                Case.BEAR: "bear"}[case]
-        keys = {f.key for f in d.factors}
-        years = max((r.year for r in self.rows if r.factor_key in keys),
-                    default=0)
-        out: list[Decimal] = []
-        for y in range(1, years + 1):
-            vals = {r.factor_key: getattr(r, attr) for r in self.rows
-                    if r.year == y and r.factor_key in keys}
-            if d.op is Op.MULTIPLY:
-                out.append(compose(d, vals))
-            else:
-                base = (self.margin_bases[attr][y - 1] if self.margin_bases
-                        else self.base_margin)
-                out.append(compose(d, vals, base=base))
-        return out
+        bm = self.base_margin if self.base_margin is not None else Decimal(0)
+        g, m = synthesize(self, case, bm, self.margin_bases)
+        return g if d.target == "revenue" else m
 
     def _describe_composed(self, d: Decomposition) -> list[str]:
         lines = ["**合成结果**（代码按分解式算出，不是模型直接填的数）", ""]
@@ -531,8 +695,13 @@ def _fmt_factor(v: Decimal, unit: str) -> str:
     """因子取值按单位格式化。**pp 不能用 :.2% 显示**——C4 的字面量
     百分号 bug 一旦漏网，`-0.03` 用 :.2% 会显示成 "-3.00%"，和正确的
     `-3`（应显示 "-3.00pp"）看起来一模一样，人从清单上根本发现不了。
-    pp 按原始数字显示，才能让写错单位的字面量露出马脚。"""
-    return f"{v:+.2f}pp" if unit == "pp" else f"{v:.2%}"
+    pp 按原始数字显示，才能让写错单位的字面量露出马脚。
+    其他单位（万吨、元…，公式化后允许）按「数值 + 单位」显示。"""
+    if unit == "pp":
+        return f"{v:+.2f}pp"
+    if unit in _PCT_UNITS:
+        return f"{v:.2%}"
+    return f"{v:,} {unit}"
 
 
 def parse(text: str, years: int,
@@ -553,6 +722,41 @@ def parse(text: str, years: int,
     return _parse_factors(text, years, decomps, factor_data, as_of)
 
 
+def _unit_guard(unit: str, label: str, cells: list[str]) -> None:
+    """按单位挡字面量。C4：pp 与 % 不是一回事；I1：变化率必须显式带 %、不认 bp；
+    公式化后的其他单位（万吨、元…）按原数，不带 % 也不带 bp。"""
+    norm = [c.replace("％", "%") for c in cells]
+    if unit == "pp":
+        if any("%" in c for c in norm):
+            raise AssumeFailed(
+                f"因子「{label}」按 pp 计，不按 %：{' / '.join(cells)} 里带了 % "
+                "—— pp 与 % 不是一回事，85%→82% 是 −3pp，写成 −3% 会被当成 "
+                "−0.03pp，缩小 100 倍。请直接填数字（如 −3），不要带 %。")
+        return
+    if unit in _PCT_UNITS:
+        for raw, n in zip(cells, norm):
+            if "bp" in n.lower():
+                raise AssumeFailed(
+                    f"因子「{label}」按变化率（%）计，不认 bp：{raw} —— "
+                    "bp 与 % 不是同一套单位，不做隐式换算，请直接换算"
+                    f"成百分比再填（如 −350bp 请写成 −3.5%）。")
+            # I-a（final-fix-D）：守卫必须复用 `_pct` 自己会用到的那个 token
+            # 来判断，"0.5 %" 这样的空格骗不过去。
+            if not (_pct_token(raw) or "").endswith("%"):
+                raise AssumeFailed(
+                    f"因子「{label}」按变化率计，必须带 %：{raw} —— "
+                    "裸数会被当成小数直接使用而不是百分比（0.5 会被"
+                    "解释成 50%，而不是你想表达的 0.5%），且这类错误"
+                    "合成后看起来仍然合理，人从清单上发现不了。"
+                    "请直接填百分比，如 −3.5%。")
+        return
+    for raw, n in zip(cells, norm):
+        if "%" in n or "bp" in n.lower():
+            raise AssumeFailed(
+                f"因子「{label}」的单位是「{unit}」，按原数填，不要带 % 或 bp："
+                f"{raw}")
+
+
 def _parse_scalar(text: str, years: int) -> AssumptionSet:
     out = AssumptionSet(code="", labels=[])
     # C-a（final-fix-D）：`_parse_factors` 早就查了 (factor_key, year)
@@ -610,31 +814,34 @@ def _parse_scalar(text: str, years: int) -> AssumptionSet:
 
 
 def _parse_factors(text: str, years: int,
-                   decomps: list[Decomposition],
+                   decomps,
                    factor_data: dict | None = None,
                    as_of: date | None = None) -> AssumptionSet:
     """按因子解析。每一行对应分解式里的一个因子，而不是营收增速/毛利率
     本身 —— 合成交给 `synthesize`，模型只负责填因子。
 
-    `factor_data`/`as_of` 传给 `_factor_basis` 判断依据是否合法——见
-    该函数文档，这里只负责把每个因子对应的 `FactorData` 找出来传过去。
+    `decomps` 是旧分解式列表或 `FormulaModel`。因子按**短名或完整名**认（公式化
+    后提示词让模型写短名）。另认 `基期: <短名> | <值> | <依据>` 行：需要第 0 年
+    取值、而账本里没有的因子由 AI 补填（spec 2026-10-14 三）。
     """
     factor_data = factor_data or {}
-    by_label = {f.label: f for d in decomps for f in d.factors}
-    target_of = {f.key: d.target for d in decomps for f in d.factors}
-    out = AssumptionSet(code="", labels=[], decomps=list(decomps))
-    # C2：同一因子同一年出现两行时，合成侧（synthesize/_synthesized_for）
-    # 用字典推导式取最后一行，to_driver_block 曾经用 next() 取第一行，
-    # 清单显示的是其中一行、工作簿实际建模用的是另一行——人批准的数字
-    # 与真正驱动估值的数字不是同一个。重复行本身就说明模型在一次回复
-    # 里生成了「修正前/修正后」两版，没想清楚，应当直接驳回重填，
-    # 而不是替它决定该采信哪一行。
+    model = as_model(decomps)
+    names: dict = {}
+    for f in model.factors:
+        names[f.label] = f
+        if f.short:
+            names.setdefault(f.short, f)
+    target_of: dict[str, str] = {}
+    for t in ("revenue", "gross_margin"):
+        for f in model.factors_of(t):
+            target_of.setdefault(f.key, t)
+    out = AssumptionSet(code="", labels=[],
+                        decomps=list(decomps) if isinstance(decomps, list) else [],
+                        model=model)
+    # C2：同一因子同一年出现两行，重复行本身说明模型在一次回复里生成了
+    # 「修正前/修正后」两版，应当直接驳回重填，而不是替它决定该采信哪一行。
     seen: set[tuple[str, int]] = set()
-    # 「同上」出口③要按因子回指"上一行"——不是文本里紧邻的上一行
-    # （那可能是别的因子），而是同一个 `factor_key` 之前处理过的那
-    # 一行。按因子分别记录，出现过的因子才有"上一行"可回指；键不存在
-    # （`.get()` 返回 `None`）与"上一行是空依据"（存的是 `[]`）要能
-    # 区分，前者必须驳回，后者允许原样复用一个空列表。
+    # 「同上」出口③按因子回指同一个 `factor_key` 之前处理过的那一行。
     last_basis: dict[str, list[Basis]] = {}
     for m in _LINE.finditer(text):
         parts = [p.strip() for p in m.group(1).split("|")]
@@ -642,12 +849,13 @@ def _parse_factors(text: str, years: int,
             raise AssumeFailed(
                 f"假设一行字段不足（需 6 段：因子|年|悲观|中性|乐观|依据），"
                 f"得到 {len(parts)}：{m.group(1)}")
-        label = pick(parts[0], list(by_label))
-        if label is None:
+        hit = pick(parts[0], list(names))
+        if hit is None:
             raise AssumeFailed(
                 f"未知的因子 {parts[0]!r}，本次分解式里只有："
-                + "、".join(by_label))
-        f = by_label[label]
+                + "、".join(f.short or f.label for f in model.factors))
+        f = names[hit]
+        label = f.label
         try:
             year = int(re.search(r"\d+", parts[1]).group(0))
         except (AttributeError, ValueError) as e:
@@ -661,54 +869,30 @@ def _parse_factors(text: str, years: int,
                 "修正后两版都留在同一次回复里。重复行本身说明这个因子还"
                 "没想清楚，请只保留一行，不要让代码替你决定该采信哪一行。")
         seen.add((f.key, year))
-        # C4：pp 与 % 不是一回事——85%→82% 是 −3pp，写成 −3% 会被 _pct
-        # 解析成 −0.03，缩小 100 倍。单调性/闭合校验都抓不住这个错，
-        # 只能在这里把字面量本身挡下来。
-        # I1：全角 ％ 与半角 % 一视同仁，否则 "-3％" 能绕过这道守卫
-        # （对 pp 而言"碰巧对"，但下一次全角换成 "-3.5％" 就会被当成
-        # 裸数 -3.5，渲染成 -350%）。
-        if f.unit == "pp" and any(
-                "%" in parts[i].replace("％", "%") for i in (2, 3, 4)):
-            raise AssumeFailed(
-                f"因子「{label}」按 pp 计，不按 %：{parts[2]} / {parts[3]} "
-                f"/ {parts[4]} 里带了 % —— pp 与 % 不是一回事，85%→82% "
-                "是 −3pp，写成 −3% 会被当成 −0.03pp，缩小 100 倍。"
-                "请直接填数字（如 −3），不要带 %。")
-        # I1：单位守卫此前只挡了 pp 侧的字面量 %，变化率侧完全没有防护——
-        # 裸数 0.5 会被当成小数直接使用（渲染成 50.00%），对一个扩产
-        # 故事看起来完全正常，人根本看不出这里其实想表达 0.5%；
-        # -3.5pp / -350bp 这类把 pp/bp 误当变化率填的写法也会被静默
-        # 当裸数吞下去。变化率必须显式带 %（全角 ％ 等价，直接规范化；
-        # bp 是另一套单位，不做隐式换算，明确拒绝并提示换算方法）。
-        if f.unit != "pp":
-            for i in (2, 3, 4):
-                raw, norm = parts[i], parts[i].replace("％", "%")
-                if "bp" in norm.lower():
-                    raise AssumeFailed(
-                        f"因子「{label}」按变化率（%）计，不认 bp：{raw} —— "
-                        "bp 与 % 不是同一套单位，不做隐式换算，请直接换算"
-                        f"成百分比再填（如 −350bp 请写成 −3.5%）。")
-                # I-a（final-fix-D）：不能只查"整段字符串里有没有 %"——
-                # 一个 "0.5 %" 这样的空格就能骗过这道守卫（_pct 实际
-                # 解析出的 token 是不带 % 的裸数 "0.5"，%被空白隔开、
-                # 整个丢弃），"2026年+5%" 这类字面量也会因为字符串
-                # 末尾不相干的一个 % 而放行。守卫必须复用 `_pct` 自己
-                # 会用到的那个 token 来判断，两处才是同一套判据。
-                if not (_pct_token(raw) or "").endswith("%"):
-                    raise AssumeFailed(
-                        f"因子「{label}」按变化率计，必须带 %：{raw} —— "
-                        "裸数会被当成小数直接使用而不是百分比（0.5 会被"
-                        "解释成 50%，而不是你想表达的 0.5%），且这类错误"
-                        "合成后看起来仍然合理，人从清单上发现不了。"
-                        "请直接填百分比，如 −3.5%。")
+        _unit_guard(f.unit, label, parts[2:5])
         bases = _factor_basis(parts[5], label, factor_data.get(f.key),
                              as_of, prior=last_basis.get(f.key))
         last_basis[f.key] = bases
         out.rows.append(Row(
-            driver=target_of[f.key], year=year,
+            driver=target_of.get(f.key, "factor"), year=year,
             bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
             bases=bases, note=_note(parts[5]), factor_key=f.key,
             basis_flag=_conclusion_flags(bases, factor_data.get(f.key))))
+    for m in _BASE_LINE.finditer(text):
+        parts = [p.strip() for p in m.group(1).split("|")]
+        if len(parts) < 3:
+            raise AssumeFailed(
+                f"基期一行字段不足（需 3 段：因子|取值|依据），得到：{m.group(1)}")
+        hit = pick(parts[0], list(names))
+        if hit is None:
+            raise AssumeFailed(f"基期行里未知的因子 {parts[0]!r}")
+        f = names[hit]
+        if f.short in out.base_values:
+            raise AssumeFailed(f"因子「{f.label}」的基期出现了不止一行，只保留一行。")
+        _unit_guard(f.unit, f.label, [parts[1]])
+        bases = _factor_basis(parts[2], f.label, factor_data.get(f.key), as_of)
+        out.base_values[f.short] = BaseValue(
+            value=_pct(parts[1]), bases=bases, note=_note(parts[2]), source="AI")
     return out
 
 
@@ -729,42 +913,85 @@ def anchor_from_cal(cal: dict) -> dict | None:
             "base_rev": float(base_rev)}
 
 
+def _anchor_values(anchor: dict) -> dict[str, Decimal]:
+    """半年报锚定后三档各自的第 1 年毛利率。四舍五入到 6 位，与旧 `margin_bases`
+    逐位一致（工作簿用的也是这一份）。"""
+    from ir_agent.dcf import anchored_year1
+
+    out: dict[str, Decimal] = {}
+    for i, name in enumerate(("bull", "base", "bear")):
+        _, gm1 = anchored_year1(rev_h=anchor["rev"], cogs_h=anchor["cogs"],
+                                share=float(anchor["share3"][i]),
+                                gm_rest=float(anchor["gm_rest3"][i]),
+                                base_rev=anchor["base_rev"])
+        out[name] = Decimal(str(round(gm1, 6)))
+    return out
+
+
 def margin_bases(a: AssumptionSet, base_margin: Decimal,
                  anchor: dict | None) -> dict[str, list[Decimal]] | None:
-    """锚定开启时毛利率的按档、按年基期；没有锚定返回 None（维持单一年报基期）。
+    """锚定开启时 `基期(毛利率)` 的按档、按年取值；没有锚定返回 None。
 
     **生益科技实跑**：Base 档 2026 被半年报锚定在 30.69%，2027 却回到「年报
     基期 26.47% + 累计 pp」= 25.87%，一年凭空掉 4.8pp。加法链对全部年份用同
     一个年报基期，而首年已被实际数覆盖——两个起点混用了。
 
-    第 1 年仍用年报基期（该年由工作簿锚定覆盖，清单横幅已说明）；第 2 年起
-    用「该档锚定后的首年毛利率 − 该档首年累计 pp」，于是第 t 年 = 锚定水平 +
-    （第 t 年累计 − 第 1 年累计）：保留模型判断的逐年变化，起点换成实际水平。
+    第 1 年仍用基期（该年由工作簿锚定覆盖）；第 2 年起把基期平移为
+    `基期 + (锚定值 − 第 1 年公式值)`，即「让第 1 年公式结果正好等于锚定值」的
+    基期（spec 2026-10-14「写计划时的补充」2）。旧加法链下就是「锚定值 − 第 1 年
+    累计 pp/100」，与改动前逐位一致。
     """
-    if anchor is None:
-        return None
-    gm_d = next((d for d in a.decomps if d.target == "gross_margin"), None)
-    if gm_d is None:
+    if anchor is None or a._m() is None:
         return None
-    keys = {f.key for f in gm_d.factors}
-    years = max((r.year for r in a.rows if r.factor_key in keys), default=0)
-    if years == 0:
-        return None
-    from ir_agent.dcf import anchored_year1
+    anchors = _anchor_values(anchor)
+    out: dict[str, list[Decimal]] = {}
+    for c in (Case.BULL, Case.BASE, Case.BEAR):
+        p = _simulate(a, c, base_margin)
+        if not p.margin:
+            return None
+        name = c.name.lower()
+        shifted = p.margin0 + (anchors[name] - p.margin[0])
+        out[name] = [p.margin0] + [shifted] * (len(p.margin) - 1)
+    return out
+
 
+def _values_for(a: AssumptionSet, case: Case) -> dict[str, list[Decimal]]:
+    """某一档每个因子第 1..N 年的取值 {短名: [...]}。同一因子同一年若有重复行，
+    后写入覆盖先写入（与改动前的字典推导式同一规则）。"""
+    attr = {Case.BULL: "bull", Case.BASE: "base", Case.BEAR: "bear"}[case]
+    m = a._m()
+    short_of = {f.key: f.short for f in m.factors}
+    by: dict[tuple[str, int], Decimal] = {}
+    for r in a.rows:
+        if r.factor_key in short_of:
+            by[(short_of[r.factor_key], r.year)] = getattr(r, attr)
+    years = max((y for _, y in by), default=0)
     out: dict[str, list[Decimal]] = {}
-    for i, name in enumerate(("bull", "base", "bear")):
-        _, gm1 = anchored_year1(rev_h=anchor["rev"], cogs_h=anchor["cogs"],
-                                share=float(anchor["share3"][i]),
-                                gm_rest=float(anchor["gm_rest3"][i]),
-                                base_rev=anchor["base_rev"])
-        cum1 = sum((getattr(r, name) for r in a.rows
-                    if r.year == 1 and r.factor_key in keys), Decimal(0))
-        shifted = Decimal(str(round(gm1, 6))) - cum1 / 100
-        out[name] = [base_margin] + [shifted] * (years - 1)
+    for f in m.factors:
+        seq: list[Decimal] = []
+        for y in range(1, years + 1):
+            if (f.short, y) not in by:
+                break
+            seq.append(by[(f.short, y)])
+        out[f.short] = seq
     return out
 
 
+def _simulate(a: AssumptionSet, case: Case, base_margin: Decimal | None,
+              anchor_y1: Decimal | None = None):
+    m = a._m()
+    base_rev = a.base_revenue
+    if base_rev is None and m.legacy:
+        base_rev = Decimal(1)          # 旧乘法链与尺度无关
+    try:
+        return simulate(m, _values_for(a, case),
+                        base_factors={k: v.value for k, v in a.base_values.items()},
+                        base_revenue=base_rev, base_margin=base_margin,
+                        anchor_y1=anchor_y1)
+    except FormulaError as e:
+        raise AssumeFailed(f"合成算不下去：{e}") from e
+
+
 def synthesize(a: AssumptionSet, case: Case, base_margin: Decimal,
                bases: dict | None = None) -> tuple[list[Decimal], list[Decimal]]:
     """把因子合成回逐年营收增速与毛利率。**代码算，不是模型填。**
@@ -772,25 +999,18 @@ def synthesize(a: AssumptionSet, case: Case, base_margin: Decimal,
     若让模型自己算出结果再填进去，分解就退化成一层装饰 —— 它可以先想好
     结果，再倒推几个因子凑出来。所以这里真的从因子值算，模型只填因子。
 
-    注意: 本函数本身**不做单调性校验** —— 校验属于 `check`（它要跨三档
-    分别调用本函数再比较），这里只管合成这一步。
+    `bases`（`margin_bases` 的结果，锚定开启时给）第 2 年起的值是平移后的
+    `基期(毛利率)`；这里把它换回该档的锚定值再推演，两者是同一件事的两种写法。
+
+    注意: 本函数本身**不做单调性校验** —— 校验属于 `check`。
     """
-    attr = {Case.BULL: "bull", Case.BASE: "base", Case.BEAR: "bear"}[case]
-    years = max((r.year for r in a.rows), default=0)
-    growth, margin = [], []
-    for y in range(1, years + 1):
-        vals = {r.factor_key: getattr(r, attr)
-                for r in a.rows if r.year == y and r.factor_key}
-        for d in a.decomps:
-            picked = {k: v for k, v in vals.items()
-                      if k in {f.key for f in d.factors}}
-            if d.target == "revenue":
-                growth.append(compose(d, picked))
-            else:
-                # bases 给了（锚定开启）就按档按年取，见 margin_bases。
-                base = bases[case.name.lower()][y - 1] if bases else base_margin
-                margin.append(compose(d, picked, base=base))
-    return growth, margin
+    p = _simulate(a, case, base_margin)
+    if bases:
+        seq = bases[case.name.lower()]
+        if len(seq) > 1 and p.margin:
+            p = _simulate(a, case, base_margin,
+                          anchor_y1=seq[1] - p.margin0 + p.margin[0])
+    return p.growth, p.margin
 
 
 def _note(raw: str) -> str:
@@ -1163,14 +1383,14 @@ def _latest(ledger: FactLedger, key: str, as_of: date) -> Decimal | None:
 
 
 def _implied_for(a: AssumptionSet, ledger: FactLedger, as_of: date,
-                 decomps: list[Decomposition] | None):
+                 decomps=None):
     """中性档营收路径的隐含份额。算不出来就返回 None。"""
-    rev = next((d for d in (decomps or []) if d.op is Op.MULTIPLY), None)
-    if rev is None:
+    if as_model(decomps) is None:
         rates = [r.base for r in sorted(a.rows, key=lambda x: x.year)
                  if r.driver == "growth"]
     else:
-        rates = a._synthesized_for(rev, Case.BASE)             # noqa: SLF001
+        bm = a.base_margin if a.base_margin is not None else Decimal(0)
+        rates, _ = synthesize(a, Case.BASE, bm)
     if not rates:
         return None
     return implied_scale(
@@ -1181,9 +1401,6 @@ def _implied_for(a: AssumptionSet, ledger: FactLedger, as_of: date,
         # 2026-10-06）: `industry.revenue_yoy` 取到的是 AI 爆发那一年的
         # 25.8%，把一个景气年外推五年会让行业涨 3.15 倍，分母被撑大，
         # 隐含份额算出来反而低于当前份额 —— 这道检查在景气行业里接近失效。
-        # 清单自己的理由栏引用的就是 `industry.revenue_cagr`（12.8%，并注明
-        # 「行业多年营收CAGR，中性路径的收敛锚点」）: **模型知道该用哪个数，
-        # 而这里原先用错了那个数。**
         industry_growth=(_latest(ledger, "industry.revenue_cagr", as_of)
                          or _latest(ledger, "industry.revenue_yoy", as_of)))
 
@@ -1217,7 +1434,7 @@ def check_deviation(a: AssumptionSet, balances: list,
         return []
     out: list[str] = []
     for r in sorted(a.rows, key=lambda x: (x.factor_key, x.year)):
-        name = _row_label(r, a.decomps or [])
+        name = _row_label(r, a._m() or a.decomps or [])
         mid = mids.get(name)
         if mid is None:
             continue
@@ -1234,7 +1451,12 @@ def check(a: AssumptionSet, years: int, *, ledger: FactLedger, body: str,
           as_of: date, decomps: list[Decomposition] | None = None,
           base_margin: Decimal | None = None, anchor: dict | None = None,
           balances: list | None = None) -> None:
-    if decomps:
+    model = as_model(decomps)
+    if model is not None:
+        a.model = model
+        if a.base_margin is None:
+            a.base_margin = base_margin
+        _fill_from_ledger(a, ledger, as_of)
         _check_factors(a, years, decomps, base_margin, anchor)
     else:
         _check_scalar(a, years)
@@ -1296,6 +1518,20 @@ def check(a: AssumptionSet, years: int, *, ledger: FactLedger, body: str,
     # 为空）完全不受影响——那条路径引的是正文，正文本来就覆盖增速/
     # 毛利率两项，逐字核验在那里可满足，这次放宽只针对因子行。
     fatal: list[str] = []
+    for short, bv in a.base_values.items():
+        if bv.source != "AI" or not bv.bases:
+            continue
+        soft_b: list[str] = []
+        for b in bv.bases:
+            problems = validate([b], ledger=ledger, body=body, as_of=as_of)
+            if not problems:
+                continue
+            if b.kind is BasisKind.REPORT:
+                soft_b.extend(problems)
+            else:
+                fatal.append(f"基期 {short}：" + "；".join(problems))
+        if soft_b:
+            bv.basis_unverified = "；".join(soft_b)
     for r in a.rows:
         if not r.bases:
             continue
@@ -1313,7 +1549,7 @@ def check(a: AssumptionSet, years: int, *, ledger: FactLedger, body: str,
                 # 过整段估值漏成未替换占位符）。EXTERNAL：外部资料核验
                 # 不过。两者都与"对正文的转述 vs 逐字"不是一类问题，
                 # 不在这次放宽范围内，继续致命。
-                fatal.append(f"{_row_label(r, a.decomps)} 第{r.year}年："
+                fatal.append(f"{_row_label(r, a._m() or a.decomps)} 第{r.year}年："
                             + "；".join(problems))
         if soft:
             r.basis_unverified = "；".join(soft)
@@ -1357,37 +1593,54 @@ def _check_scalar(a: AssumptionSet, years: int) -> None:
                 " —— 必须严格递增，否则情景分析没有意义")
 
 
-def _check_factors(a: AssumptionSet, years: int, decomps: list[Decomposition],
+def _fill_from_ledger(a: AssumptionSet, ledger: FactLedger, as_of: date) -> None:
+    """第 0 年取值里账本拿得到的部分（spec 2026-10-14 决定 9：账本有就用账本）。
+
+    - 需要基期值的因子：数据线索里 `[[key]]` 声明、账本有上年值的，代码取；
+    - 营收₀：账本最近一个完整年度的营收，换算成亿元（与工作簿同口径）。
+    """
+    from ir_agent import gaps
+
+    m = a._m()
+    for f in m.needs_base():
+        have, _ = gaps.ledger_refs(f.source_hint, ledger, as_of)
+        if not have:
+            continue
+        key, per = have[0].rsplit("@", 1)
+        a.base_values[f.short] = BaseValue(
+            value=ledger.get(key, per, as_of=as_of).value,
+            bases=[Basis.fact(f"[[{key}@{per}]]")], source="账本")
+    if a.base_revenue is None:
+        pers = annual_periods(ledger, as_of, years=1)
+        if pers:
+            try:
+                a.base_revenue = (ledger.get("revenue", pers[-1], as_of=as_of).value
+                                  / Decimal(10 ** 8))
+            except (KeyError, LookAheadError):
+                pass
+
+
+def _check_factors(a: AssumptionSet, years: int, decomps,
                    base_margin: Decimal | None, anchor: dict | None = None) -> None:
     """分解模式下的校验。
 
-    **不在这里逐因子比较 bear<base<bull。** 单个因子档位的方向性因子而异
-    （例如同一因子在悲观档被写得很极端，未必意味着它对目标的方向也是
-    悲观的），代码判断不了「这个因子往哪个方向走算悲观」。真正要卡的是
-    **合成结果**——三档营收增速 / 毛利率必须严格拉开，这是唯一能客观
-    判定方向对不对的地方。因子层面各自拉开不等于合成后拉开：产能悲观档
-    偏高、单价悲观档偏低，相乘后完全可能悲观反而高于中性，而那正是这道
-    校验要抓住的错数。
+    **不在这里逐因子比较 bear<base<bull。** 单个因子档位的方向性因子而异，
+    代码判断不了「这个因子往哪个方向走算悲观」。真正要卡的是**合成结果**——
+    三档营收增速 / 毛利率必须严格拉开。
     """
-    # **读回的分解式要重过闸门。** `_decomp_from_dict` 直接构造对象，
-    # 预研时的闸门在读回路径上一次都没跑。生益科技目录里残留
-    # 的 9/14 那份 assumptions.json，营收链里还有「高端产品收入占比」——新
-    # 闸门在预研时会拒收它，读回时却能一路走进建模。与「人手改 JSON 可能
-    # 引入重复行」同一类：不依赖上游一定已经查过。
-    # 只跑量纲类检查（check_dimensions），不跑因子个数——见其文档字符串。
-    for d in decomps:
-        try:
-            check_dimensions(d)
-        except DecompositionInvalid as e:
-            raise AssumeFailed(
-                f"判断项里的分解式已不合法：{e}　——请重跑第一阶段，"
-                "重新预研分解式后再填判断项。") from e
-    # C2 纵深防御：`_parse_factors` 已经在解析阶段拒绝重复行，但
-    # `check()` 也会在人工核实之后、`from_json` 读回时被再跑一遍
-    # （`full.run_valuation`）——那时的 `AssumptionSet` 未必经过
-    # `_parse_factors`，人手改 JSON 也可能引入重复行。这里再挡一次，
-    # 不依赖上游一定已经查过重。
-    label_of = {f.key: f.label for d in decomps for f in d.factors}
+    # **读回的旧分解式要重过闸门。** `_decomp_from_dict` 直接构造对象，预研时的
+    # 闸门在读回路径上一次都没跑。只对旧的乘/加分解式（公式化写法不跑这些字面
+    # 闸门，spec 2026-10-14 五）。
+    if isinstance(decomps, list):
+        for d in decomps:
+            try:
+                check_dimensions(d)
+            except DecompositionInvalid as e:
+                raise AssumeFailed(
+                    f"判断项里的分解式已不合法：{e}　——请重跑第一阶段，"
+                    "重新预研分解式后再填判断项。") from e
+    m = a._m()
+    label_of = {f.key: f.label for f in m.factors}
     seen: set[tuple[str, int]] = set()
     for r in a.rows:
         if not r.factor_key:
@@ -1399,13 +1652,8 @@ def _check_factors(a: AssumptionSet, years: int, decomps: list[Decomposition],
                 f"第 {r.year} 年出现了不止一行——重复行会让清单显示的值"
                 "与实际参与合成的值不是同一行，必须只保留一行。")
         seen.add(key)
-        # 出口二（「无数据（估计）」）落地为空的 `bases`——`_factor_basis`
-        # 已经在解析阶段确认过这个因子确实是「无数据」才放行，但那不能
-        # 免除「说明依据什么估的」这一要求：空依据 + 空理由 = 一个数字
-        # 凭空出现在清单上，读者连"这是怎么估出来的"都无从判断。这里
-        # 再挡一次而不依赖解析阶段——`check()` 在第二阶段人工核实之后
-        # 会脱离 `_parse_factors` 单独重跑，人手改 JSON 同样可能把
-        # note 清空。
+        # 出口二（「无数据（估计）」）落地为空的 `bases`——那不能免除「说明
+        # 依据什么估的」这一要求：空依据 + 空理由 = 一个数字凭空出现在清单上。
         if not r.bases and not r.note.strip():
             raise AssumeFailed(
                 f"因子「{label_of.get(r.factor_key, r.factor_key)}」"
@@ -1413,19 +1661,27 @@ def _check_factors(a: AssumptionSet, years: int, decomps: list[Decomposition],
                 "理由要说明这个估计依据什么给出（参照哪个可比公司、"
                 "哪个行业惯例……），不能只留一句「无数据」。")
 
-    for d in decomps:
-        for f in d.factors:
-            got = {r.year for r in a.rows if r.factor_key == f.key}
-            missing = sorted(set(range(1, years + 1)) - got)
-            if missing:
-                raise AssumeFailed(
-                    f"因子「{f.label}」缺第 {missing} 年 —— 预测期每一年都要给，"
-                    "漏掉的年份会沿用模板默认值，而那不是你的判断")
+    for f in m.factors:
+        got = {r.year for r in a.rows if r.factor_key == f.key}
+        missing = sorted(set(range(1, years + 1)) - got)
+        if missing:
+            raise AssumeFailed(
+                f"因子「{f.label}」缺第 {missing} 年 —— 预测期每一年都要给，"
+                "漏掉的年份会沿用模板默认值，而那不是你的判断")
+
+    lacking = [f for f in m.needs_base() if f.short not in a.base_values]
+    if lacking:
+        raise AssumeFailed(
+            "以下因子需要第 0 年（基期）的取值，账本里没有："
+            + "、".join(f"{f.short}（{f.label}）" for f in lacking)
+            + " —— 请各补一行「基期: <短名> | <值> | <依据>」（已发生的实际值，"
+            "单值不分档）。")
 
     # **合成后再验一次单调。** 这是本模块真正的闸门。
     if base_margin is None:
         base_margin = Decimal("0.10")
-    a.decomps = list(decomps)
+    if isinstance(decomps, list):
+        a.decomps = list(decomps)
     # 锚定开启时查的必须是平移后的路径——那才是建模用的。
     a.margin_bases = margin_bases(a, base_margin, anchor)
     _check_monotonic(a, base_margin, a.margin_bases)
@@ -1496,53 +1752,8 @@ _SCALAR_HEAD = """**你只填这两项**：逐年营收增速、逐年毛利率
 `假设: <营收增速|毛利率> | 第N年 | <悲观> | <中性> | <乐观> | 「正文原话」或 [[占位符]]`"""
 
 
-def _factor_head(decomps: list[Decomposition], factor_data: dict) -> str:
-    """分解模式下替换 `_SCALAR_HEAD` 的提示词头部。
-
-    **逐因子填、代码合成**——不是把「你只填这两项」换成「你只填一堆
-    因子」这么简单，还要把单位规则钉死在提示词里，否则模型很容易把 pp
-    填进乘法链（稼动率 85%→82% 写成 −3pp 混进变化率链）。
-    """
-    lines = ["**本次按驱动因子分解填写。** 分解式："]
-    for d in decomps:
-        lines.append(f"  {d.describe()}")
-    lines += ["", "逐个因子填，代码会把它们合成回营收增速与毛利率——",
-              "**不要自己算出增速或毛利率再倒推因子**，那样分解就没有意义。",
-              "", "| 因子 | 单位 | 数据 |", "| --- | --- | --- |"]
-    for d in decomps:
-        for f in d.factors:
-            fd = factor_data.get(f.key)
-            if fd is None:
-                st = "未采集"
-            elif fd.note:
-                # I2：这里此前只印状态词、不印原文片段——模型看到「年报
-                # 披露」会直接当成有数据可用去填数字。「年报披露/研报
-                # 测算」只是文字命中，未必真给出了数值（甚至可能命中的
-                # 是一句「未披露 XX」），必须把 gaps.collect 摘出来的
-                # 原文片段一起摆出来，让模型自己判断这到底是不是能当
-                # 数值依据用的原文，而不是被一个状态词代为下结论。
-                st = f"{fd.status}（{fd.note}）"
-            else:
-                st = fd.status
-            # Minor-3（final-fix-D）：`fd.note` 抄的是 gaps.collect 摘出
-            # 来的年报/研报原文片段——PDF 抽出的章节带表格竖线很常见，
-            # 未转义的 "|" 会被当成这一行提示词表格自己的列分隔符，把
-            # 单位这一列错位到别处去。转义成 markdown 认得的 "\|"。
-            lines.append(f"| {_esc_pipe(f.label)} | {_esc_pipe(f.unit)} "
-                        f"| {_esc_pipe(st)} |")
-    lines += ["",
-              "**pp 不能进乘法链**：营收链的因子一律填变化率（含稼动率）——"
-              "稼动率 85%→82% 是 −3pp，但变化率是 −3.5%。",
-              "毛利率链的因子一律填 pp，且**pp 一律不带 %**——"
-              "写 −3，不要写 −3%（−3% 会被当成 −0.03pp，缩小 100 倍）。",
-              "**pp 的参照点是基期，且是累计值，不是逐年再变一次**："
-              "毛利率 = 基期 + Σpp，基期对预测期每一年都是同一个数，"
-              "所以第 N 年的 pp 要填「相对基期」累计的变动，不是「相对"
-              "第 N−1 年」又变了多少。例如基期 14.15%，你判断毛利率会"
-              "逐年走低到 13.15%/12.15%/11.15%，三年应分别填 "
-              "−1、−2、−3（相对基期累计），而不是 −1、−1、−1"
-              "（那样合成出来三年会是同一个数 13.15%，不会继续下滑）。",
-              "",
+# 依据的写法规则。旧的乘/加清单与公式化清单共用这一段，不各写一份。
+_BASIS_RULES = (
               "**引文只能出自三处**：你的分析简报正文、账本占位符 "
               "[[key@period]]、或下表「数据」列里这个因子自己采集到的"
               "原文片段/发现。下面各节（宏观、行业、可比公司、外部资料、"
@@ -1596,6 +1807,57 @@ def _factor_head(decomps: list[Decomposition], factor_data: dict) -> str:
               "之一），没有上一行可回指的「同上」会被直接驳回。理由"
               "（为什么这一年这么判断）不受影响，仍然要每年单独写"
               "清楚——「同上」只省依据，不省理由。",
+)
+
+
+def _factor_head(decomps: list[Decomposition], factor_data: dict) -> str:
+    """分解模式下替换 `_SCALAR_HEAD` 的提示词头部。
+
+    **逐因子填、代码合成**——不是把「你只填这两项」换成「你只填一堆
+    因子」这么简单，还要把单位规则钉死在提示词里，否则模型很容易把 pp
+    填进乘法链（稼动率 85%→82% 写成 −3pp 混进变化率链）。
+    """
+    lines = ["**本次按驱动因子分解填写。** 分解式："]
+    for d in decomps:
+        lines.append(f"  {d.describe()}")
+    lines += ["", "逐个因子填，代码会把它们合成回营收增速与毛利率——",
+              "**不要自己算出增速或毛利率再倒推因子**，那样分解就没有意义。",
+              "", "| 因子 | 单位 | 数据 |", "| --- | --- | --- |"]
+    for d in decomps:
+        for f in d.factors:
+            fd = factor_data.get(f.key)
+            if fd is None:
+                st = "未采集"
+            elif fd.note:
+                # I2：这里此前只印状态词、不印原文片段——模型看到「年报
+                # 披露」会直接当成有数据可用去填数字。「年报披露/研报
+                # 测算」只是文字命中，未必真给出了数值（甚至可能命中的
+                # 是一句「未披露 XX」），必须把 gaps.collect 摘出来的
+                # 原文片段一起摆出来，让模型自己判断这到底是不是能当
+                # 数值依据用的原文，而不是被一个状态词代为下结论。
+                st = f"{fd.status}（{fd.note}）"
+            else:
+                st = fd.status
+            # Minor-3（final-fix-D）：`fd.note` 抄的是 gaps.collect 摘出
+            # 来的年报/研报原文片段——PDF 抽出的章节带表格竖线很常见，
+            # 未转义的 "|" 会被当成这一行提示词表格自己的列分隔符，把
+            # 单位这一列错位到别处去。转义成 markdown 认得的 "\|"。
+            lines.append(f"| {_esc_pipe(f.label)} | {_esc_pipe(f.unit)} "
+                        f"| {_esc_pipe(st)} |")
+    lines += ["",
+              "**pp 不能进乘法链**：营收链的因子一律填变化率（含稼动率）——"
+              "稼动率 85%→82% 是 −3pp，但变化率是 −3.5%。",
+              "毛利率链的因子一律填 pp，且**pp 一律不带 %**——"
+              "写 −3，不要写 −3%（−3% 会被当成 −0.03pp，缩小 100 倍）。",
+              "**pp 的参照点是基期，且是累计值，不是逐年再变一次**："
+              "毛利率 = 基期 + Σpp，基期对预测期每一年都是同一个数，"
+              "所以第 N 年的 pp 要填「相对基期」累计的变动，不是「相对"
+              "第 N−1 年」又变了多少。例如基期 14.15%，你判断毛利率会"
+              "逐年走低到 13.15%/12.15%/11.15%，三年应分别填 "
+              "−1、−2、−3（相对基期累计），而不是 −1、−1、−1"
+              "（那样合成出来三年会是同一个数 13.15%，不会继续下滑）。",
+              "",
+              *_BASIS_RULES,
               "",
               "对每一个因子、每一年给出一行：",
               "`假设: <因子名> | 第N年 | <悲观> | <中性> | <乐观> | "
@@ -1604,9 +1866,59 @@ def _factor_head(decomps: list[Decomposition], factor_data: dict) -> str:
     return "\n".join(lines)
 
 
+def _formula_head(m: FormulaModel, factor_data: dict,
+                  ledger_bases: dict | None = None) -> str:
+    """公式化分解式的提示词头部（spec 2026-10-14 三）。旧的乘/加分解式仍用
+    `_factor_head`，提示词与改动前一字不差。"""
+    ledger_bases = ledger_bases or {}
+    lines = [f"**本次按公式化分解填写（{m.mode}模式）。**",
+             ("每个因子逐年填**变化**（增速、pp 变动…），营收与毛利率的第 0 年取账本。"
+              if m.mode == "变化" else
+              "每个因子逐年填**水平**（量、价、成本…），第 0 年的营收与毛利率由基期"
+              "因子值代入公式算出。"),
+             "", "公式："]
+    lines += [f"  {m.describe(t)}" for t in ("revenue", "gross_margin")
+              if t in m.formulas]
+    if m.constants:
+        lines += ["", "常量："]
+        lines += [f"  {c.name} = {c.value}（{c.source}）" for c in m.constants]
+    lines += ["", "逐个因子填，代码会按公式把它们合成回营收增速与毛利率——",
+              "**不要自己算出增速或毛利率再倒推因子**，那样分解就没有意义。",
+              "", "| 短名 | 因子 | 单位 | 数据 |", "| --- | --- | --- | --- |"]
+    for f in m.factors:
+        fd = factor_data.get(f.key)
+        st = "未采集" if fd is None else (f"{fd.status}（{fd.note}）"
+                                         if fd.note else fd.status)
+        lines.append(f"| {_esc_pipe(f.short)} | {_esc_pipe(f.label)} "
+                     f"| {_esc_pipe(f.unit)} | {_esc_pipe(st)} |")
+    need = m.needs_base()
+    if need:
+        have = [f for f in need if f.short in ledger_bases]
+        todo = [f for f in need if f.short not in ledger_bases]
+        lines += ["", "**基期值（第 0 年）**："]
+        lines += [f"  - {f.short} = {ledger_bases[f.short].value}（账本，已取到）"
+                  for f in have]
+        if todo:
+            lines.append("  - 需要你补：" + "、".join(f.short for f in todo)
+                         + " —— 各补一行 `基期: <短名> | <值> | <依据>`。基期是"
+                         "已经发生的实际值，单值，不分三档；依据规则与下面相同。")
+    lines += ["",
+              "**单位规则**：单位是变化率或 % 的，取值带 %（如 −3.5%）；单位是 pp "
+              "的，填数字不带 %（如 −3）；其他单位（万吨、元/平方米…）按原数填，"
+              "不带 %。",
+              "",
+              *_BASIS_RULES,
+              "",
+              "对每一个因子、每一年给出一行（因子写短名）：",
+              "`假设: <短名> | 第N年 | <悲观> | <中性> | <乐观> | "
+              "「正文原话/证据原文」或 [[占位符]] 或 无数据（估计）或 同上`"]
+    return "\n".join(lines)
+
+
 def build_prompt(judg: dict, labels: list[str], context: str,
-                 brief: str = "", decomps: list[Decomposition] | None = None,
-                 factor_data: dict | None = None) -> str:
+                 brief: str = "", decomps=None,
+                 factor_data: dict | None = None,
+                 ledger_bases: dict | None = None) -> str:
     g, gm = judg.get("g_stats") or (), judg.get("gm_stats") or ()
     hist = []
     if g:
@@ -1618,7 +1930,13 @@ def build_prompt(judg: dict, labels: list[str], context: str,
                     f"{(judg.get('gm_hist') or [0])[-1] * 100:.1f}%、"
                     f"历史均值 {gm[0] * 100:.1f}%、标准差 {gm[1] * 100:.1f}pp、"
                     f"区间 {gm[2] * 100:.1f}%~{gm[3] * 100:.1f}%")
-    head = _factor_head(decomps, factor_data or {}) if decomps else _SCALAR_HEAD
+    m = as_model(decomps)
+    if m is None:
+        head = _SCALAR_HEAD
+    elif m.legacy:
+        head = _factor_head(decomps, factor_data or {})
+    else:
+        head = _formula_head(m, factor_data or {}, ledger_bases)
     return PROMPT.format(head=head, years=len(labels), labels="、".join(labels),
                          history="\n".join(hist) or "  （无）",
                          context=context, brief=brief or "（无）")
@@ -1626,7 +1944,7 @@ def build_prompt(judg: dict, labels: list[str], context: str,
 
 def _decide_once(client, judg: dict, labels: list[str], context: str, *,
                  ledger: FactLedger, body: str, as_of: date, code: str,
-                 max_attempts: int, decomps: list[Decomposition] | None,
+                 max_attempts: int, decomps,
                  factor_data: dict | None,
                  base_margin: Decimal | None,
                  anchor: dict | None = None,
@@ -1638,8 +1956,14 @@ def _decide_once(client, judg: dict, labels: list[str], context: str, *,
     模式再试一次，但两标量模式本身连续失败没有更低的台阶可退，必须
     原样拒绝。两件事分开才不会在这里意外递归。
     """
+    model = as_model(decomps)
+    ledger_bases = {}
+    if model is not None and not model.legacy:
+        probe = AssumptionSet(code="", labels=[], model=model)
+        _fill_from_ledger(probe, ledger, as_of)
+        ledger_bases = probe.base_values
     prompt = build_prompt(judg, labels, context, brief=body, decomps=decomps,
-                          factor_data=factor_data)
+                          factor_data=factor_data, ledger_bases=ledger_bases)
     last, raw = "", ""
     for attempt in range(1, max_attempts + 1):
         raw = client(prompt)
@@ -1656,15 +1980,14 @@ def _decide_once(client, judg: dict, labels: list[str], context: str, *,
                       "请修正后重新输出全部行。\n")
             continue
         a.code, a.labels = code, labels
-        a.decomps = list(decomps or [])
+        a.decomps = list(decomps) if isinstance(decomps, list) else []
+        a.model = model
         a.factor_data = dict(factor_data or {})
         a.base_margin = base_margin
-        if a.decomps:
+        if model is not None:
             # C2：reconcile() 写得对、测试充分，缺的是「在真实流程里被
-            # 调用一次并把结果交给人看」这个动作——此前它只在
-            # drivers.py 自己的单测里被调用过。
-            a.residuals = {d.target: _reconcile_decomp(d, ledger, as_of)
-                          for d in a.decomps}
+            # 调用一次并把结果交给人看」这个动作。
+            a.residuals = _reconcile_model(model, ledger, as_of)
         return a
     raise AssumeFailed(
         f"连续 {max_attempts} 次给不出可核验的假设：{last}"
@@ -1674,7 +1997,7 @@ def _decide_once(client, judg: dict, labels: list[str], context: str, *,
 
 def decide(client, judg: dict, labels: list[str], context: str, *,
            ledger: FactLedger, body: str, as_of: date, code: str = "",
-           max_attempts: int = 3, decomps: list[Decomposition] | None = None,
+           max_attempts: int = 3, decomps=None,
            factor_data: dict | None = None,
            base_margin: Decimal | None = None,
            anchor: dict | None = None,
@@ -1729,59 +2052,106 @@ def _historical_series(ledger: FactLedger, key: str, periods: list[str],
     return out
 
 
-def _reconcile_decomp(d: Decomposition, ledger: FactLedger,
-                      as_of: date, years: int = 5) -> dict:
-    """对一个分解式做历史回算，报残差 + 标出取不到历史的因子。
+def _change(cur: Decimal, prev: Decimal, unit: str) -> Decimal | None:
+    """相邻两期水平值 → 变化（变化模式历史对账用）。换算规则与提示词里的单位
+    规则同一套；其他单位换算不出来，返回 None（列入「无历史」）。"""
+    if unit == "pp":
+        return (cur - prev) * 100
+    if unit in _PCT_UNITS:
+        return cur / prev - 1 if prev != 0 else None
+    return None
+
+
+def _history_key(f) -> str:
+    """因子在账本里的历史序列用哪个 key：数据线索里 `[[key]]` 声明的优先，
+    否则按因子 key（旧分解式的口径）。"""
+    from ir_agent.gaps import _KEY_DECL
+
+    have = _KEY_DECL.findall(f.source_hint or "")
+    return have[0] if have else f.key
+
 
-    账本里存的因子是**水平值**（产能、单价……的原始数字），而
-    `drivers.reconcile` 要的是**期间变化**（乘法链要变化率，加法链要
-    pp 变化）——按 `d.op` 把相邻两期的水平值换算成变化，换算规则与
-    提示词里对模型的单位要求（见 `_factor_head`）是同一套。
+def _reconcile_model(model: FormulaModel, ledger: FactLedger, as_of: date,
+                     years: int = 5) -> dict:
+    """对公式化分解式做历史回算，报残差 + 标出取不到历史的因子。
+
+    每个历史年份按**单步预测**核对：`上期(·)`、`基期(·)` 都取上一期实际值
+    （spec 2026-10-14「写计划时的补充」3）。变化模式下因子历史按单位把相邻两期
+    水平换成变化；水平模式直接用水平，营收增速按公式在本期与上期的值之比算。
+    旧乘/加分解式下与改动前的 `_reconcile_decomp` 逐位一致。
 
     某个因子在账本里完全找不到历史序列，**不能因此把它从残差报告里
-    静默拿掉**——要在 `no_history` 里明说，人得知道这里没法核验，
-    而不是误以为「没提到就是没问题」。
+    静默拿掉**——要在 `no_history` 里明说。
     """
     periods = annual_periods(ledger, as_of, years=years + 1)
-    if len(periods) < 2:
-        return {"residuals": [], "no_history": [f.label for f in d.factors]}
-
-    levels = {f.key: _historical_series(ledger, f.key, periods, as_of)
-             for f in d.factors}
-    no_history = [f.label for f in d.factors if len(levels[f.key]) < 2]
-
-    history: dict[str, dict[str, Decimal]] = {}
-    for i in range(1, len(periods)):
-        cur, prev = periods[i], periods[i - 1]
-        row: dict[str, Decimal] = {}
-        for f in d.factors:
-            lv = levels[f.key]
-            if cur not in lv or prev not in lv or lv[prev] == 0:
-                continue
-            row[f.key] = (lv[cur] / lv[prev] - 1) if d.op is Op.MULTIPLY \
-                else (lv[cur] - lv[prev]) * 100
-        if row:
-            history[cur] = row
-
-    target_key = "revenue" if d.target == "revenue" else "gross_margin"
-    tgt = _historical_series(ledger, target_key, periods, as_of)
-    actual: dict[str, Decimal] = {}
-    base: dict[str, Decimal] | None = {} if d.op is Op.ADD_PP else None
-    for i in range(1, len(periods)):
-        cur, prev = periods[i], periods[i - 1]
-        if cur not in tgt:
+    consts = {c.name: c.value for c in model.constants}
+    out: dict = {}
+    levels = {f.short: _historical_series(ledger, _history_key(f), periods, as_of)
+              for f in model.factors}
+    rev = _historical_series(ledger, "revenue", periods, as_of)
+    gm = _historical_series(ledger, "gross_margin", periods, as_of)
+    for target, fm in model.formulas.items():
+        used = model.factors_of(target)
+        if len(periods) < 2:
+            out[target] = {"residuals": [], "no_history": [f.label for f in used]}
             continue
-        if d.op is Op.MULTIPLY:
-            if prev not in tgt or tgt[prev] == 0:
+        no_history = [f.label for f in used if len(levels[f.short]) < 2]
+        res: list[Residual] = []
+        for i in range(1, len(periods)):
+            cur_p, prev_p = periods[i], periods[i - 1]
+            cur: dict = {}
+            prev: dict = {"营收": rev.get(prev_p), "毛利率": gm.get(prev_p)}
+            ok = True
+            for f in used:
+                lv = levels[f.short]
+                if model.mode == "水平":
+                    if cur_p not in lv:
+                        ok = False
+                        break
+                    cur[f.short] = lv[cur_p]
+                    if prev_p in lv:
+                        prev[f.short] = lv[prev_p]
+                else:
+                    if cur_p not in lv or prev_p not in lv or lv[prev_p] == 0:
+                        ok = False
+                        break
+                    ch = _change(lv[cur_p], lv[prev_p], f.unit)
+                    if ch is None:
+                        ok = False
+                        break
+                    cur[f.short] = ch
+            if not ok:
                 continue
-            actual[cur] = tgt[cur] / tgt[prev] - Decimal(1)
-        else:
-            actual[cur] = tgt[cur]
-            if prev in tgt:
-                base[cur] = tgt[prev]
+            try:
+                if target == "revenue":
+                    if cur_p not in rev or not rev.get(prev_p):
+                        continue
+                    actual = rev[cur_p] / rev[prev_p] - Decimal(1)
+                    if model.mode == "水平":
+                        denom = evaluate(fm, prev, None, prev, consts)
+                        composed = evaluate(fm, cur, prev, prev, consts) / denom - 1
+                    else:
+                        # 旧乘法链与尺度无关：上期营收按 1 代入，与旧 compose 逐位一致。
+                        scale = Decimal(1) if model.legacy else rev[prev_p]
+                        pv = dict(prev, 营收=scale)
+                        composed = evaluate(fm, cur, pv, pv, consts) / scale - 1
+                else:
+                    if cur_p not in gm or (model.mode == "变化" and prev_p not in gm):
+                        continue
+                    actual = gm[cur_p]
+                    composed = evaluate(fm, cur, prev, prev, consts)
+            except FormulaError:
+                continue
+            res.append(Residual(period=cur_p, composed=composed, actual=actual,
+                                gap=actual - composed))
+        out[target] = {"residuals": res, "no_history": no_history}
+    return out
 
-    res = reconcile(d, history, actual, base=base)
-    return {"residuals": res, "no_history": no_history}
+
+def _reconcile_decomp(d: Decomposition, ledger: FactLedger,
+                      as_of: date, years: int = 5) -> dict:
+    """单条旧分解式的历史回算（保留给旧调用方）。翻译成公式后走同一个实现。"""
+    return _reconcile_model(from_decomps([d]), ledger, as_of, years)[d.target]
 
 
 def _annual_gm_hist(cal: dict) -> list:
@@ -1880,7 +2250,7 @@ def estimated_labels(a: AssumptionSet) -> list[str]:
     不能拿它把"这个数是拍的"从清单上抹掉——清单是人工批准唯一能看的
     东西，**遗漏必须可见**这条约束正是在这里被绕过的。
     """
-    return sorted({_row_label(r, a.decomps) for r in a.rows
+    return sorted({_row_label(r, a._m() or a.decomps) for r in a.rows
                    if r.factor_key and (not r.bases
                                         or _looks_like_estimate(r.note))})
 
@@ -1892,7 +2262,7 @@ def unverified_labels(a: AssumptionSet) -> list[str]:
     （`full._unverified_disclosure`）都用它——两处各写一遍"怎么给这些行
     起名"，早晚会分叉成人在清单上看到的和研报里写的对不上号。
     """
-    return sorted({f"{_row_label(r, a.decomps)}第{r.year}年"
+    return sorted({f"{_row_label(r, a._m() or a.decomps)}第{r.year}年"
                    for r in a.rows if r.factor_key and r.basis_unverified})
 
 
@@ -1912,8 +2282,11 @@ def apply_to(cal: dict, a: AssumptionSet, case: Case = Case.BASE) -> dict:
     否则两处各算各的，会在第一年的毛利率上重复计入一次 pp 增量。
     """
     ju = dict(cal["judg"])
-    if a.decomps:
+    if a._m() is not None:
         base_margin = _base_margin_of(cal)
+        # 第 0 年营收按工作簿口径（亿元）——公式用到绝对金额时，ir-agent 与
+        # 工作簿必须代入同一个数。
+        a.base_revenue = _base_revenue_of(cal)
         # 锚定开启时毛利率第 2 年起从各档锚定水平接着走（见 margin_bases）；
         # 第二阶段的 check() 不做这一步，所以这里要再查一次单调。
         bases = margin_bases(a, base_margin, anchor_from_cal(cal))
@@ -1940,73 +2313,81 @@ def apply_to(cal: dict, a: AssumptionSet, case: Case = Case.BASE) -> dict:
     return out
 
 
+def has_model(a) -> bool:
+    """这份判断项是不是按分解式填的（新写法或旧的乘/加）。"""
+    m = getattr(a, "_m", None)
+    return bool(m and m() is not None)
+
+
+def _base_revenue_of(cal: dict) -> Decimal | None:
+    """第 0 年营收（亿元）：calibrate 的最近完整年度营收。取不到返回 None。"""
+    annual = (cal.get("periods") or {}).get("annual") or []
+    v = (cal.get("rev") or {}).get(annual[-1]) if annual else None
+    return Decimal(str(v)) if v is not None else None
+
+
 def to_driver_block(a: AssumptionSet, cal: dict) -> dict:
-    """AssumptionSet → ashare_build 要的 `drivers` 结构。
+    """AssumptionSet → ashare_build 要的 `drivers` 结构（schema 2，公式化）。
+
+    公式以**已校验的记号序列**交付（`Formula.tokens()`）：`ashare-dcf` 只把记号
+    换成单元格引用，**不做解析** —— 解析只在 ir-agent 一处（spec 2026-10-14 四）。
 
     三档顺序固定为 bull/base/bear，与工作簿 B/C/D 列绑定 ——
     顺序写反不会让验算失败，只是「选悲观」会选出乐观值。
 
-    **base_level 取 `_base_margin_of`，不是 `cal["judg"]["gm"][0]`。**
-    `apply_to` 若已跑过，`gm[0]` 是**合成之后**的年度水平（基期 + 第一年
-    的 pp 增量），拿它当基期会让 1b_驱动分解 的公式把第一年的增量算
-    两遍：先被吸进 base_level，又被工作表公式再加一次。`gm_hist` 是
-    calibrate 的原始历史序列，不受 apply_to 影响，才是真正「不随年份
-    滚动」的基期。
-
-    **取值规则与 synthesize()/_synthesized_for() 保持一致（C2 纵深防御）。**
-    同一因子同一年出现两行本应在 `_parse_factors`/`check()` 就被拒绝，
-    但这里不依赖那道门一定挡住了一切——`next(... )` 会取**第一行**，
-    与合成侧的字典推导式取**最后一行**是两条不同的规则，一旦有重复行
-    绕过检查混进来，清单显示的数字与这里实际写进工作簿的数字就会是
-    两行不同的取值。改成与合成侧同样的「后写入覆盖先写入」，即便查重
-    将来被放开，两侧至少不会各算各的。
+    **基期毛利率取 `_base_margin_of`，不是 `cal["judg"]["gm"][0]`。** `apply_to`
+    若已跑过，`gm[0]` 是合成之后的年度水平，拿它当基期会把第一年的增量算两遍。
+
+    **取值规则与 synthesize 保持一致（C2 纵深防御）**：同一因子同一年若有重复行，
+    后写入覆盖先写入。
     """
-    base_margin = float(_base_margin_of(cal))
-    out = {}
-    years = max((r.year for r in a.rows), default=0)
+    m = a._m()
+    base_margin = _base_margin_of(cal)
+    base_rev = _base_revenue_of(cal)
+    years = max((r.year for r in a.rows if r.factor_key), default=0)
     by_year_key: dict[tuple[int, str], Row] = {}
     for r in a.rows:
         if r.factor_key:
             by_year_key[(r.year, r.factor_key)] = r
-    for d in a.decomps:
-        keys = [f.key for f in d.factors]
-        label_of = {f.key: f.label for f in d.factors}
-        vals = {}
-        for case in ("bull", "base", "bear"):
-            rows_by_year = []
-            for y in range(1, years + 1):
-                vals_for_year = []
-                for k in keys:
-                    try:
-                        row = by_year_key[(y, k)]
-                    except KeyError as e:
-                        # Minor-4（final-fix-D）：此前这里是裸 KeyError，
-                        # 一路冒到 run_valuation 的兜底里显示成「估值
-                        # （KeyError: (2, 'asp')）」——排障看不出是哪个
-                        # 因子哪一年缺了取值。`check()` 用 len(a.labels)
-                        # 定年数，这里用 max(r.year) 定年数，两者本应
-                        # 一致，但人手改 JSON 把某个因子的年份改到超出
-                        # labels 范围时会错开：那个因子多出的年份把这里
-                        # 的 years 撑大，其它因子在那一年却没有取值。
-                        raise AssumeFailed(
-                            f"因子「{label_of.get(k, k)}」第 {y} 年缺"
-                            "取值，无法写入 1b_驱动分解 工作表——多半是"
-                            "人手改 JSON 把某个因子的年份改到了超出预测"
-                            "期范围，让不同因子的年份对不上。") from e
-                    vals_for_year.append(float(getattr(row, case)))
-                rows_by_year.append(vals_for_year)
-            vals[case] = rows_by_year
-        block = {"op": d.op.value,
-                 "factors": [{"key": f.key, "label": f.label, "unit": f.unit}
-                             for f in d.factors],
-                 "values": vals}
-        if d.target == "gross_margin":
-            block["base_level"] = base_margin
-            # 与 apply_to 同一个函数算出的按档按年基期，1b 表公式按它写——否则
-            # 工作簿三档列（跨表引用 1b）与 apply_to 写进 judg 的数就对不上。
-            bases = margin_bases(a, _base_margin_of(cal), anchor_from_cal(cal))
-            if bases:
-                block["base_levels"] = {k: [float(x) for x in v]
-                                        for k, v in bases.items()}
-        out[d.target] = block
-    return out
+    vals: dict[str, list] = {}
+    for case in ("bull", "base", "bear"):
+        rows_by_year = []
+        for y in range(1, years + 1):
+            vals_for_year = []
+            for f in m.factors:
+                try:
+                    row = by_year_key[(y, f.key)]
+                except KeyError as e:
+                    # Minor-4（final-fix-D）：排障要看得出是哪个因子哪一年缺了取值。
+                    raise AssumeFailed(
+                        f"因子「{f.label}」第 {y} 年缺取值，无法写入 1b_驱动分解 "
+                        "工作表——多半是人手改 JSON 把某个因子的年份改到了超出"
+                        "预测期范围，让不同因子的年份对不上。") from e
+                vals_for_year.append(float(getattr(row, case)))
+            rows_by_year.append(vals_for_year)
+        vals[case] = rows_by_year
+    block = {
+        "schema": 2, "mode": m.mode,
+        "factors": [{"key": f.key, "short": f.short, "label": f.label,
+                     "unit": f.unit} for f in m.factors],
+        "constants": [{"name": c.name, "value": float(c.value),
+                       "source": c.source} for c in m.constants],
+        "formulas": {t: [list(tok) for tok in fm.tokens()]
+                     for t, fm in m.formulas.items()},
+        "formula_text": {t: m.describe(t) for t in m.formulas},
+        "base": {"revenue": float(base_rev) if base_rev is not None else None,
+                 "margin": float(base_margin),
+                 "factors": {k: float(v.value) for k, v in a.base_values.items()}},
+        "values": vals,
+    }
+    anchor = anchor_from_cal(cal)
+    bases = margin_bases(a, base_margin, anchor)
+    if bases:
+        # 锚定开启时 `基期(毛利率)` 的按档按年取值、各档锚定后的第 1 年毛利率
+        # （`上期(毛利率)` 在第 2 年取它）—— 与 apply_to 同一个函数算出，否则
+        # 工作簿三档列与 apply_to 写进 judg 的数就对不上。
+        block["base_levels"] = {k: [float(x) for x in v] for k, v in bases.items()}
+        block["anchor_y1"] = {k: float(v) for k, v in _anchor_values(anchor).items()}
+    return block
+
+
diff --git a/ir_agent/formula.py b/ir_agent/formula.py
index 8a3a3bd..cd264fb 100644
--- a/ir_agent/formula.py
+++ b/ir_agent/formula.py
@@ -337,19 +337,25 @@ def simulate(model: FormulaModel, values: dict[str, list], *,
     取锚定值；`基期(毛利率)` 平移为 `基期 + (锚定值 − 第 1 年公式值)` —— 与旧
     `assume.margin_bases` 的平移逐位一致（spec「写计划时的补充」2）。第 1 年的
     合成值本身不被锚定替换（工作簿另行锚定覆盖）。
+
+    **有哪条公式就算哪条。** 新写法两条都必须给（`FormulaModel.validate`），但由
+    旧文件翻译来的分解式可能只分解了一项 —— 与改动前的 `synthesize` 一致，缺的
+    那项返回空列表。
     """
     base_factors = dict(base_factors or {})
     consts = {c.name: c.value for c in model.constants}
     years = max((len(v) for v in values.values()), default=0)
-    rev_f, gm_f = model.formulas["revenue"], model.formulas["gross_margin"]
+    rev_f = model.formulas.get("revenue")
+    gm_f = model.formulas.get("gross_margin")
     if model.mode == "水平":
         y0 = dict(base_factors)
-        r0 = evaluate(rev_f, y0, None, y0, consts, "第 0 年营收")
-        m0 = evaluate(gm_f, y0, None, y0, consts, "第 0 年毛利率")
+        r0 = evaluate(rev_f, y0, None, y0, consts, "第 0 年营收") if rev_f else None
+        m0 = evaluate(gm_f, y0, None, y0, consts, "第 0 年毛利率") if gm_f else None
     else:
-        if base_revenue is None or base_margin is None:
+        if (rev_f and base_revenue is None) or (gm_f and base_margin is None):
             raise FormulaError("变化模式需要第 0 年的营收与毛利率（账本），没有给")
-        r0, m0 = Decimal(base_revenue), Decimal(base_margin)
+        r0 = Decimal(base_revenue) if base_revenue is not None else None
+        m0 = Decimal(base_margin) if base_margin is not None else None
     base = dict(base_factors)
     base["营收"], base["毛利率"] = r0, m0
     prev = dict(base)
@@ -360,16 +366,20 @@ def simulate(model: FormulaModel, values: dict[str, list], *,
             if len(seq) < t:
                 raise FormulaError(f"缺「{short}」第 {t} 年的取值")
             cur[short] = seq[t - 1]
-        r = evaluate(rev_f, cur, prev, base, consts, f"第 {t} 年营收")
-        m = evaluate(gm_f, cur, prev, base, consts, f"第 {t} 年毛利率")
-        if prev["营收"] == 0:
-            raise FormulaError(f"第 {t} 年：上一年营收为零，增速算不出来")
-        growth.append(r / prev["营收"] - 1)
-        revenue.append(r)
-        margin.append(m)
+        r = evaluate(rev_f, cur, prev, base, consts, f"第 {t} 年营收") \
+            if rev_f else None
+        m = evaluate(gm_f, cur, prev, base, consts, f"第 {t} 年毛利率") \
+            if gm_f else None
+        if rev_f:
+            if prev["营收"] == 0:
+                raise FormulaError(f"第 {t} 年：上一年营收为零，增速算不出来")
+            growth.append(r / prev["营收"] - 1)
+            revenue.append(r)
+        if gm_f:
+            margin.append(m)
         prev = dict(cur)
         prev["营收"], prev["毛利率"] = r, m
-        if t == 1 and anchor_y1 is not None:
+        if t == 1 and anchor_y1 is not None and gm_f:
             prev["毛利率"] = Decimal(anchor_y1)
             base = dict(base)
             base["毛利率"] = m0 + (Decimal(anchor_y1) - m)
@@ -377,6 +387,33 @@ def simulate(model: FormulaModel, values: dict[str, list], *,
                 margin=margin)
 
 
+def flat_factors(xs) -> list:
+    """分解式列表 / `FormulaModel` / 因子列表 → 因子列表（按 key 去重、保序）。
+
+    旧调用方传 `decompositions`（每条带 `.factors`），新调用方传 `FormulaModel`；
+    水平模式下同一个因子被两条公式共用，迭代时要去重。assume、gaps、
+    factor_review、methodology 共用这一份，不各写一套。"""
+    if xs is None:
+        return []
+    if isinstance(xs, FormulaModel):
+        xs = xs.factors
+    out: list = []
+    seen: set[str] = set()
+    for x in xs:
+        for f in (getattr(x, "factors", None) or [x]):
+            if f.key not in seen:
+                seen.add(f.key)
+                out.append(f)
+    return out
+
+
+def as_model(decomps) -> "FormulaModel | None":
+    """`decomps` 参数的三种形态归一：None/[]（两标量）、旧分解式列表、FormulaModel。"""
+    if isinstance(decomps, FormulaModel):
+        return decomps
+    return from_decomps(list(decomps)) if decomps else None
+
+
 # ── 旧格式翻译 ────────────────────────────────────────────────────
 
 _STRIP = re.compile(r"[^\w]")
diff --git a/ir_agent/methodology.py b/ir_agent/methodology.py
index fd62514..4c1c43e 100644
--- a/ir_agent/methodology.py
+++ b/ir_agent/methodology.py
@@ -25,8 +25,8 @@ from ir_agent.drivers import (MAX_FACTORS, MIN_FACTORS, TARGET_LABEL,
                               check_closure)
 from ir_agent.formula import (MODES, RESERVED, TARGETS as _FORMULA_TARGETS,
                               Constant, FormulaInvalid, FormulaModel,
-                              from_decomps, parse_constant, parse_formula,
-                              valid_name)
+                              flat_factors as _flat, from_decomps,
+                              parse_constant, parse_formula, valid_name)
 from ir_agent.lineparse import keyed, pick
 
 _DECOMP = keyed("分解")
@@ -550,25 +550,6 @@ def _to_dsl(m: Methodology) -> str:
     return "\n".join(lines)
 
 
-def _flat(xs) -> list[Factor]:
-    """分解式列表 / `FormulaModel` / 因子列表 → 因子列表（按 key 去重、保序）。
-
-    旧调用方传 `m.decompositions`（每条带 `.factors`），新调用方传 `m.model`；
-    两者在这里归一，下游函数不必各写一套。"""
-    if xs is None:
-        return []
-    if isinstance(xs, FormulaModel):
-        xs = xs.factors
-    out: list[Factor] = []
-    seen: set[str] = set()
-    for x in xs:
-        for f in (getattr(x, "factors", None) or [x]):
-            if f.key not in seen:
-                seen.add(f.key)
-                out.append(f)
-    return out
-
-
 def theme_coverage(decomps: list, themes: list[str]) -> list[tuple[str, str]]:
     """辩论① 的每个主题，有没有被某个因子覆盖。**标注用，不阻断。**
 
diff --git a/tests/test_assume.py b/tests/test_assume.py
index cec6823..db134ae 100644
--- a/tests/test_assume.py
+++ b/tests/test_assume.py
@@ -414,21 +414,22 @@ def test_to_driver_block产出结构():
     a.labels = ["2026E"]
     cal = assume.apply_to(_cal_for_apply(), a)
     drv = assume.to_driver_block(a, cal)
-    assert set(drv) == {"revenue", "gross_margin"}
-
-    rev = drv["revenue"]
-    assert rev["op"] == "乘"
-    assert [f["key"] for f in rev["factors"]] == ["capacity", "asp"]
+    # schema 2（2026-10-14 公式化）：一个块，因子按声明顺序，公式以记号交付。
+    assert drv["schema"] == 2 and drv["mode"] == "变化"
+    assert [f["key"] for f in drv["factors"]] == [
+        "capacity", "asp", "util_leverage", "input_cost"]
     # **三档顺序固定为 bull/base/bear**，与工作簿 B/C/D 列绑定——
     # 顺序写反不会让验算失败，只是「选悲观」会选出乐观值。
-    assert rev["values"]["bull"][0] == [0.14, 0.01]
-    assert rev["values"]["base"][0] == [0.10, -0.02]
-    assert rev["values"]["bear"][0] == [0.05, -0.06]
-    assert "base_level" not in rev
-
-    gm = drv["gross_margin"]
-    assert gm["op"] == "加"
-    assert abs(gm["base_level"] - 0.1415) < 1e-9
+    assert drv["values"]["bull"][0] == [0.14, 0.01, 0.5, 0.3]
+    assert drv["values"]["base"][0] == [0.10, -0.02, -1.0, -0.5]
+    assert drv["values"]["bear"][0] == [0.05, -0.06, -2.5, -1.5]
+    assert drv["formula_text"]["revenue"] == "营收 = 上期(营收)*(1+产能)*(1+单价)"
+    assert drv["formula_text"]["gross_margin"] == \
+        "毛利率 = 基期(毛利率)+(稼动率摊薄+上游成本)/100"
+    assert drv["formulas"]["revenue"][:3] == [["lp", "("], ["lp", "("],
+                                              ["prev", "营收"]]
+    assert abs(drv["base"]["margin"] - 0.1415) < 1e-9
+    assert "base_levels" not in drv
 
 
 def test_to_driver_block基期不受apply_to合成结果污染():
@@ -443,7 +444,7 @@ def test_to_driver_block基期不受apply_to合成结果污染():
     assert abs(applied["judg"]["gm"][0] - 0.1265) < 1e-6, \
         "前置断言：apply_to 之后 gm[0] 已经是合成后的年度水平"
     drv = assume.to_driver_block(a, applied)
-    assert abs(drv["gross_margin"]["base_level"] - 0.1415) < 1e-9
+    assert abs(drv["base"]["margin"] - 0.1415) < 1e-9
 
 
 def test_json往返保留分解式与因子key():
@@ -896,7 +897,7 @@ def test_to_driver_block与synthesize取值规则一致():
     cal = {"periods": {"annual": ["p0"]}, "rev": {"p0": 1.0},
           "cogs": {"p0": 1.0}, "judg": {"gm_hist": [0.1415]}}
     block = assume.to_driver_block(a, cal)
-    capacity_base = block["revenue"]["values"]["base"][0][0]
+    capacity_base = block["values"]["base"][0][0]
     assert capacity_base == pytest.approx(0.30), \
         "to_driver_block 应与 synthesize 一样取最后一行（30%），不是 next() 的第一行（1%）"
     # 两处应合成出同一个营收增速——用同一份（含重复行的）取值。
@@ -2167,7 +2168,7 @@ def test_工作簿三档列与1b表用同一套按年基期():
     a, _ = _two_year_set()
     cal = _cal_600183()
     out = assume.apply_to(cal, a)
-    block = assume.to_driver_block(a, cal)["gross_margin"]
+    block = assume.to_driver_block(a, cal)
     bm = assume._base_margin_of(cal)
     bases = assume.margin_bases(a, bm, assume.anchor_from_cal(cal))
     assert block["base_levels"]["base"] == [float(x) for x in bases["base"]]
diff --git a/tests/test_assume_formula.py b/tests/test_assume_formula.py
new file mode 100644
index 0000000..500faa5
--- /dev/null
+++ b/tests/test_assume_formula.py
@@ -0,0 +1,216 @@
+"""判断项环节的公式化写法（spec 2026-10-14 子项目一 三、四）。
+
+旧的乘/加分解式由 `tests/test_assume.py` 守着（翻译后逐位等价）；这里只测新写法。
+"""
+from __future__ import annotations
+
+from datetime import date
+from decimal import Decimal as D
+
+import pytest
+
+from ir_agent import assume
+from ir_agent.assume import AssumeFailed, Case
+from ir_agent.ledger import Fact, FactLedger, Method
+from ir_agent.methodology import parse as parse_meth
+
+AS_OF = date(2026, 10, 14)
+
+CHANGE = parse_meth("""模式: 变化
+因子: 量增 | 出货量同比增速 | 变化率 | 年报产销量 | 产销量
+因子: 价增 | 平均单价同比变化率 | 变化率 | 研报价格跟踪 | 平均单价
+因子: 提价 | 提价对毛利率的累计贡献 | pp | 调价函 | 调价
+因子: 原料 | 原材料对毛利率的累计侵蚀 | pp | 年报风险因素 | 原材料价格
+公式: 营收 = 上期(营收) × (1 + 量增) × (1 + 价增)
+公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100
+""").model
+
+LEVEL = parse_meth("""模式: 水平
+因子: 量 | 覆铜板出货量 | 万平方米 | 年报产销量 | 产销量
+因子: 价 | 覆铜板平均单价 | 元/平方米 | [[ccl.asp]] | 平均单价
+因子: 本 | 覆铜板单位成本 | 元/平方米 | 年报成本构成 | 单位成本
+公式: 营收 = 量 × 价
+公式: 毛利率 = (价 − 本) ÷ 价
+""").model
+
+Q = "「依据原文」"
+
+
+def _change_lines(years=2):
+    out = []
+    for y in range(1, years + 1):
+        out += [f"假设: 量增 | 第{y}年 | 2% | 5% | 8% | 扩产 {Q}",
+                f"假设: 价增 | 第{y}年 | -3% | 0% | 2% | 价格 {Q}",
+                f"假设: 提价 | 第{y}年 | -1 | 0.5 | 1.5 | 调价 {Q}",
+                f"假设: 原料 | 第{y}年 | -2 | -1 | -0.5 | 原料 {Q}"]
+    return "\n".join(out)
+
+
+def _level_lines(years=1, base=True):
+    out = []
+    for y in range(1, years + 1):
+        out += [f"假设: 量 | 第{y}年 | 100 | 110 | 120 | 扩产 {Q}",
+                f"假设: 价 | 第{y}年 | 9 | 10 | 11 | 价格 {Q}",
+                f"假设: 本 | 第{y}年 | 7.5 | 7.4 | 7.3 | 成本 {Q}"]
+    if base:
+        out += [f"基期: 量 | 100 | 年报产销量 {Q}",
+                f"基期: 本 | 7.2 | 年报成本构成 {Q}"]
+    return "\n".join(out)
+
+
+def _ledger(asp="9.5"):
+    led = FactLedger()
+    for key, per, v in (("revenue", "2025FY", "2000000000"),
+                        ("gross_margin", "2025FY", "0.25"),
+                        ("ccl.asp", "2025FY", asp)):
+        led.put(Fact(key=key, value=D(v), unit="x", period=per,
+                     as_of=date(2026, 4, 1), source_id="t",
+                     method=Method.REPORTED))
+    return led
+
+
+def _check(a, years, model, ledger=None, base_margin=D("0.25")):
+    assume.check(a, years, ledger=ledger or _ledger(), body=f"正文 {Q} 依据原文",
+                 as_of=AS_OF, decomps=model, base_margin=base_margin)
+
+
+class TestParse:
+    def test_按短名解析_也认完整名(self):
+        txt = _change_lines(1).replace("假设: 量增 |", "假设: 出货量同比增速 |")
+        a = assume.parse(txt, 1, decomps=CHANGE)
+        assert {r.factor_key for r in a.rows} == {f.key for f in CHANGE.factors}
+        assert a.model is CHANGE and a.decomps == []
+
+    def test_基期行(self):
+        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+        assert a.base_values["量"].value == D(100)
+        assert a.base_values["本"].source == "AI"
+
+    def test_基期行重复被驳回(self):
+        with pytest.raises(AssumeFailed, match="基期出现了不止一行"):
+            assume.parse(_level_lines() + f"\n基期: 量 | 99 | {Q}", 1, decomps=LEVEL)
+
+    @pytest.mark.parametrize("bad", ["110%", "110bp"])
+    def test_其他单位不带百分号(self, bad):
+        txt = _level_lines().replace("假设: 量 | 第1年 | 100 | 110 |",
+                                     f"假设: 量 | 第1年 | 100 | {bad} |")
+        with pytest.raises(AssumeFailed, match="按原数填"):
+            assume.parse(txt, 1, decomps=LEVEL)
+
+
+class TestLevelMode:
+    def test_账本有的基期由代码取_其余AI补(self):
+        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+        _check(a, 1, LEVEL)
+        assert a.base_values["价"].source == "账本"
+        assert a.base_values["价"].value == D("9.5")
+        assert a.base_values["价"].bases[0].ref == "[[ccl.asp@2025FY]]"
+
+    def test_缺基期明确驳回(self):
+        a = assume.parse(_level_lines(base=False), 1, decomps=LEVEL)
+        with pytest.raises(AssumeFailed, match="需要第 0 年.*量.*本"):
+            _check(a, 1, LEVEL)
+
+    def test_口径不混用_第0年由基期因子代入公式(self):
+        """营收₀ = 100 × 9.5 = 950，与账本营收 20 亿无关。"""
+        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+        _check(a, 1, LEVEL)
+        g, m = assume.synthesize(a, Case.BASE, D("0.25"))
+        assert g == [D(110) * D(10) / (D(100) * D("9.5")) - 1]
+        assert m == [(D(10) - D("7.4")) / D(10)]
+
+    def test_合成后三档不拉开被驳回(self):
+        txt = _level_lines().replace("假设: 价 | 第1年 | 9 | 10 | 11 |",
+                                     "假设: 价 | 第1年 | 11 | 10 | 9 |")
+        a = assume.parse(txt, 1, decomps=LEVEL)
+        with pytest.raises(AssumeFailed, match="合成后"):
+            _check(a, 1, LEVEL)
+
+
+class TestChangeMode:
+    def test_营收0取账本_亿元(self):
+        a = assume.parse(_change_lines(), 2, decomps=CHANGE)
+        _check(a, 2, CHANGE)
+        assert a.base_revenue == D(20)
+        g, m = assume.synthesize(a, Case.BASE, D("0.25"))
+        assert g == [D("0.05"), D("0.05")]
+        assert m == [D("0.25") + D("-0.5") / 100] * 2
+
+
+class TestDescribe:
+    def test_清单有公式_基期_合成_按单位格式化(self):
+        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+        _check(a, 1, LEVEL)
+        a.labels = ["2026E"]
+        txt = a.describe()
+        assert "## 分解式（水平模式）" in txt and "营收 = 量 × 价" in txt
+        assert "| 覆铜板平均单价 | 9.5 元/平方米 | 账本 |" in txt
+        assert "## 合成结果" in txt and "15.79%" in txt
+        assert "110 万平方米" in txt
+
+
+class TestPrompt:
+    def test_提示词头部(self):
+        p = assume.build_prompt({}, ["2026E"], "", decomps=LEVEL,
+                                ledger_bases={"价": assume.BaseValue(D("9.5"))})
+        assert "公式化分解填写（水平模式）" in p and "营收 = 量 × 价" in p
+        assert "价 = 9.5（账本，已取到）" in p
+        assert "需要你补：量、本" in p
+        assert "`假设: <短名> |" in p and "**引文只能出自三处**" in p
+
+    def test_旧分解式的提示词不变(self):
+        from ir_agent.drivers import Decomposition, Factor, Op
+        d = Decomposition("revenue", Op.MULTIPLY, [Factor("a", "产能", "变化率"),
+                                                   Factor("b", "单价", "变化率")])
+        p = assume.build_prompt({}, ["2026E"], "", decomps=[d])
+        assert "**本次按驱动因子分解填写。**" in p and "公式化" not in p
+
+
+class TestJson:
+    def test_schema2往返保留公式与基期(self):
+        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+        _check(a, 1, LEVEL)
+        a.labels = ["2026E"]
+        d = assume.to_json(a)
+        assert d["schema"] == 2 and d["model"]["mode"] == "水平"
+        back, edits = assume.from_json(d, code="600183")
+        assert back.model.describe("gross_margin") == LEVEL.describe("gross_margin")
+        assert back.base_values["价"].source == "账本"
+        assert back.base_values["量"].value == D(100)
+        assert edits == []
+        assert assume.synthesize(back, Case.BASE, D("0.25")) == \
+            assume.synthesize(a, Case.BASE, D("0.25"))
+
+    def test_人手改坏公式给看得懂的话(self):
+        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+        d = assume.to_json(a)
+        d["model"]["formulas"]["revenue"] = "量 × 未声明"
+        with pytest.raises(AssumeFailed, match="请重跑第一阶段"):
+            assume.from_json(d)
+
+
+class TestReconcile:
+    def test_按数据线索的key取历史(self):
+        led = FactLedger()
+        for per, asp, rev in (("2023FY", "8", "800"), ("2024FY", "9", "990"),
+                              ("2025FY", "9.5", "1100")):
+            for k, v in (("ccl.asp", asp), ("revenue", rev)):
+                led.put(Fact(key=k, value=D(v), unit="x", period=per,
+                             as_of=date(2026, 4, 1), source_id="t",
+                             method=Method.REPORTED))
+        res = assume._reconcile_model(LEVEL, led, AS_OF)
+        assert "覆铜板出货量" in res["revenue"]["no_history"]
+        assert "覆铜板平均单价" not in res["revenue"]["no_history"]
+
+
+class TestDriverBlock:
+    def test_水平模式带基期因子(self):
+        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+        _check(a, 1, LEVEL)
+        cal = {"periods": {"annual": ["2025"]}, "rev": {"2025": 20.0},
+               "cogs": {"2025": 15.0}, "judg": {"gm_hist": [0.25]}}
+        drv = assume.to_driver_block(a, cal)
+        assert drv["mode"] == "水平"
+        assert drv["base"]["factors"] == {"量": 100.0, "价": 9.5, "本": 7.2}
+        assert drv["values"]["base"] == [[110.0, 10.0, 7.4]]
+        assert ["name", "量"] in drv["formulas"]["revenue"]
```

</details>

---

### Task 4: 下游统一读公式模型；修第二阶段静默退回两标量

**Files:** Modify `ir_agent/full.py`、`gaps.py`、`factor_review.py`、`dcf.py`；tests `test_assume_formula.py`、`test_factor_review.py`、`test_gaps.py`、`test_full_context.py`

**写计划时实测发现的漏洞**：`run_valuation` 传 `decomps=a.decomps or None`；公式化写法下 `a.decomps` 是空列表，第二阶段于是**悄悄退回两标量校验**，人批准的公式一行都不生效。改为 `decomps=a._m()`。

其余接线：采集（`gaps.collect` 接受 `FormulaModel`，水平模式共用因子去重）、辩论② 议题（`meth.factors()`）、`factors.json` 读回、判断项、因子结构审查条目（按公式分组、共用因子不重复）、`dcf` 建 1b 表的条件（`assume.has_model`）。

- [ ] **Step 1: 取测试，确认红**

```bash
git checkout 99f5609 -- tests/test_assume_formula.py tests/test_factor_review.py tests/test_gaps.py
git checkout 4dded9a -- tests/test_full_context.py
python -m pytest tests/test_assume_formula.py tests/test_factor_review.py tests/test_gaps.py tests/test_full_context.py -q
```
Expected: `4 failed, 252 passed`（第二阶段源码断言、共用因子只采集一次、审查条目去重、新写法端到端）。

- [ ] **Step 2: 取实现，确认绿；全量不回归**

```bash
git checkout 99f5609 -- ir_agent/dcf.py ir_agent/factor_review.py ir_agent/full.py ir_agent/gaps.py
python -m pytest tests/test_assume_formula.py tests/test_factor_review.py tests/test_gaps.py tests/test_full_context.py -q   # 256 passed
python -m pytest -q -m "not live" -W error           # 2335 passed, 8 skipped
```

- [ ] **Step 3: 变异验证**（用文件拷贝还原，不用 git）
  - `run_valuation` 里 `decomps=a._m()` 改回 `decomps=a.decomps or None` → `test_第二阶段重新校验带着公式模型` 红；
  - `run_brief` 里辩论② 议题改回 `[f.label for d in meth.decompositions for f in d.factors]` → `test_公式化分解式从第二次调用一路传到判断项` 红。

- [ ] **Step 4: 提交**

```bash
git add ir_agent/dcf.py ir_agent/factor_review.py ir_agent/full.py ir_agent/gaps.py tests/test_assume_formula.py tests/test_factor_review.py tests/test_gaps.py tests/test_full_context.py
git commit -m "full/gaps/factor_review/dcf: 下游统一读公式模型；修第二阶段重新校验悄悄退回两标量的漏洞"
```

<details><summary>完整 diff（179bbae → 4dded9a，仅本任务文件）</summary>

```diff
diff --git a/ir_agent/dcf.py b/ir_agent/dcf.py
index 59aa159..a908fd7 100644
--- a/ir_agent/dcf.py
+++ b/ir_agent/dcf.py
@@ -482,7 +482,7 @@ def run(code: str, out_dir: str | Path, *, as_of: date,
     if assumptions is not None:
         from ir_agent import assume
         cal = assume.apply_to(cal, assumptions)
-        if getattr(assumptions, "decomps", None):
+        if assume.has_model(assumptions):
             # **只有方法论预研成功过才有分解式。** 预研失败退回两标量
             # 模式时 assumptions.decomps 是空列表，此时不建「1b_驱动
             # 分解」表 —— 工作簿形状与从前完全一致，不留一张空表。
diff --git a/ir_agent/factor_review.py b/ir_agent/factor_review.py
index 20d6ff3..8f58bae 100644
--- a/ir_agent/factor_review.py
+++ b/ir_agent/factor_review.py
@@ -91,11 +91,23 @@ def expected_items(decomps: list,
         # 而那正是最该看一眼的一轮。此时没有因子、谈不上两两正交，但「取值
         # 与它自己的理由是否自洽」照样要对账。
         return [("对账", d) for d in drivers]
-    for d in decomps or []:
-        labels = [f.label for f in d.factors]
-        out += [("因子", l) for l in labels]
-        out += [("正交", f"{a} × {b}") for a, b in combinations(labels, 2)]
-        out.append(("对账", d.target))
+    from ir_agent.formula import FormulaModel
+
+    if isinstance(decomps, FormulaModel):
+        groups = [(t, [f.label for f in decomps.factors_of(t)])
+                  for t in ("revenue", "gross_margin") if t in decomps.formulas]
+    else:
+        groups = [(d.target, [f.label for f in d.factors]) for d in decomps or []]
+    # 水平模式下同一因子被两条公式共用：「因子」「正交」条目去重，否则同一条
+    # 要答两遍。
+    seen: set[tuple[str, str]] = set()
+    for target, labels in groups:
+        for it in ([("因子", l) for l in labels]
+                   + [("正交", f"{a} × {b}") for a, b in combinations(labels, 2)]
+                   + [("对账", target)]):
+            if it not in seen:
+                seen.add(it)
+                out.append(it)
     return out
 
 
diff --git a/ir_agent/full.py b/ir_agent/full.py
index cfaaddb..090ecbc 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -1267,7 +1267,7 @@ def _read_factors(out: Path, meth) -> tuple[dict | None, list[str]]:
                       "作依据。重跑 --brief 可补"]
     try:
         data, notes = gaps.from_json(json.loads(f.read_text(encoding="utf-8")),
-                                     meth.decompositions)
+                                     meth.model)
     except (OSError, ValueError, KeyError, TypeError) as e:
         return None, [f"因子采集结果读不回（{e.__class__.__name__}: "
                       f"{str(e)[:120]}）—— 判断项按「未采集」处理"]
@@ -1514,18 +1514,17 @@ def _research_methodology(code: str, r, out: Path, client, *,
     # 就是凭空问责。
     themes = list(STANCE_THEMES) if stance_note.strip() else []
     cov, cov_notes = methodology.coverage(m, themes)
-    hits = methodology.term_hits(m.decompositions,
+    hits = methodology.term_hits(m.model,
                                  sections=getattr(r, "sections", None),
                                  corpus=getattr(r, "corpus", None))
     refs: dict = {}
     led, as_of = getattr(r, "ledger", None), getattr(r, "as_of", None)
     if led is not None and as_of is not None:
         from ir_agent import gaps
-        for d in m.decompositions:
-            for f in d.factors:
-                have, miss = gaps.ledger_refs(f.source_hint, led, as_of)
-                if have or miss:
-                    refs[f.label] = (have, miss)
+        for f in m.factors():
+            have, miss = gaps.ledger_refs(f.source_hint, led, as_of)
+            if have or miss:
+                refs[f.label] = (have, miss)
     path.write_text(methodology.to_markdown(m, coverage=cov, hits=hits,
                                                 coverage_notes=cov_notes,
                                                 key_refs=refs),
@@ -2150,7 +2149,7 @@ def run_brief(code: str, r, as_of: date, out_dir: str | Path,
     factor_data: dict = {}
     if meth is not None:
         factor_data = _collect_factors(r, as_of, research_client, md,
-                                       meth.decompositions, meth.industry,
+                                       meth.model, meth.industry,
                                        skipped)
         # 落盘给第三次调用。空字典也写 —— 让「采过、一个都没采到」与「没落盘」
         # 在第三次调用里可区分。
@@ -2160,7 +2159,7 @@ def run_brief(code: str, r, as_of: date, out_dir: str | Path,
             encoding="utf-8")
 
     # ── 多空辩论②：定取值 ─────────────────────────────────────
-    subjects = ([f.label for d in meth.decompositions for f in d.factors]
+    subjects = ([f.label for f in meth.factors()]
                 if meth is not None else list(assume.DRIVERS))
     sb = _stance_round(client, out, STANCE_B, "B", subjects,
                        ctx + "\n\n【因子采集】\n" + _factor_ctx(factor_data),
@@ -2261,7 +2260,7 @@ def run_assume(code: str, r, as_of: date, out_dir: str | Path,
     skipped += fnotes
     aerr = _propose_assumptions(
         code, r, body, md, as_of, out, assume_client, arts,
-        decomps=(meth.decompositions if meth is not None else None),
+        decomps=(meth.model if meth is not None else None),
         factor_data=factor_data, balances=sb.balances)
     if aerr:
         skipped.append(aerr)
@@ -2377,9 +2376,9 @@ def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
         from ir_agent import factor_review
         try:
             a.review = factor_review.review(
-                client, a.decomps, checklist=a.describe(),
+                client, a._m() or a.decomps, checklist=a.describe(),
                 implied=a.implied.describe(),
-                drivers=tuple(assume.DRIVERS) if not a.decomps else ())
+                drivers=tuple(assume.DRIVERS) if not assume.has_model(a) else ())
         except Exception as e:                              # noqa: BLE001
             # **审查失败不该毙掉判断项。** 它是一道补充意见，不是闸门 ——
             # 与本仓库「这一步失败不该毙掉已经写好的正文」同一条原则。
@@ -2577,7 +2576,10 @@ def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
         # **人改过的值要重新校验。** 批准不等于豁免 —— 三档拉不开、
         # 漏掉某一年，都会让模型算出一个看起来正常的错数。
         assume.check(a, len(a.labels), ledger=r.ledger, body=body, as_of=as_of,
-                     decomps=a.decomps or None, base_margin=base_margin,
+                     # **读 `a._m()`，不读 `a.decomps`**：公式化写法下 decomps 是空
+                     # 列表，`decomps=a.decomps or None` 会让第二阶段悄悄退回两标量
+                     # 校验，人批准的公式在这里一行都不生效。
+                     decomps=a._m(), base_margin=base_margin,
                      anchor=anchor)
     except assume.AssumeFailed as e:
         raise SystemExit(f"批准的假设不合法，拒绝建模：\n{e}")
diff --git a/ir_agent/gaps.py b/ir_agent/gaps.py
index 36c3ad3..78159fb 100644
--- a/ir_agent/gaps.py
+++ b/ir_agent/gaps.py
@@ -36,6 +36,7 @@ from datetime import date
 from ir_agent.drivers import Decomposition, Factor
 from ir_agent.ledger import FactLedger, LookAheadError
 from ir_agent.sources.external import Document, Tier
+from ir_agent.formula import flat_factors
 from ir_agent.sources import conclusions
 from ir_agent.sources.conclusions import Article, Conclusion, number_marks
 from ir_agent.sources.openweb import TEXT_LIMIT
@@ -211,7 +212,7 @@ def identify(d: Decomposition, ledger: FactLedger, as_of: date) -> list[Factor]:
     在这个时点上就是不存在的，不能因为账本里"有"就当作已披露。
     """
     out = []
-    for f in d.factors:
+    for f in getattr(d, "factors", d):
         try:
             ledger.get(f.key, _latest(ledger, f.key), as_of=as_of)
         except (KeyError, LookAheadError):
@@ -413,111 +414,113 @@ def collect(client, decomps: list[Decomposition], ledger: FactLedger,
     现有调用方。
     """
     out: dict[str, FactorData] = {}
-    for d in decomps:
-        gaps = {f.key for f in identify(d, ledger, as_of)}
-        for f in d.factors:
-            if f.key in out:
-                continue
-            if f.key not in gaps:
-                out[f.key] = FactorData(factor=f, status=DISCLOSED)
-                continue
-            have, _ = ledger_refs(f.source_hint, ledger, as_of)
-            if have:
-                out[f.key] = FactorData(
-                    factor=f, status=DISCLOSED,
-                    note="账本 " + "、".join(f"[[{x}]]" for x in have)
-                         + "（模型在数据线索中声明，口径是否与因子一致待人工确认）")
-                continue
+    # `decomps` 可以是旧分解式列表或 `FormulaModel`；水平模式下同一因子被两条
+    # 公式共用，按 key 去重（`flat_factors`）。
+    factors = flat_factors(decomps)
+    gaps = {f.key for f in identify(factors, ledger, as_of)}
+    for f in factors:
+        if f.key in out:
+            continue
+        if f.key not in gaps:
+            out[f.key] = FactorData(factor=f, status=DISCLOSED)
+            continue
+        have, _ = ledger_refs(f.source_hint, ledger, as_of)
+        if have:
+            out[f.key] = FactorData(
+                factor=f, status=DISCLOSED,
+                note="账本 " + "、".join(f"[[{x}]]" for x in have)
+                     + "（模型在数据线索中声明，口径是否与因子一致待人工确认）")
+            continue
 
-            hit = _match_annual(sections, f.label, f.search_term)
-            if hit is not None:
-                excerpt, src, core = hit
-                out[f.key] = FactorData(
-                    factor=f, status=ANNUAL,
-                    note=f"「{excerpt}」——{src}"
-                         "（文字依据，是否给出具体数值待人工确认）"
-                         + _via_note(core, f),
-                    source_url=annual_url, source_retrieved=as_of,
-                    # 与 note 里包在「」中的那段是同一份文字——note 是
-                    # 拼给人看的，这里是给 assume._match_excerpt 用的，
-                    # 两者必须来自同一个 excerpt 变量，不能事后各改各的。
-                    source_excerpt=excerpt)
-                continue
+        hit = _match_annual(sections, f.label, f.search_term)
+        if hit is not None:
+            excerpt, src, core = hit
+            out[f.key] = FactorData(
+                factor=f, status=ANNUAL,
+                note=f"「{excerpt}」——{src}"
+                     "（文字依据，是否给出具体数值待人工确认）"
+                     + _via_note(core, f),
+                source_url=annual_url, source_retrieved=as_of,
+                # 与 note 里包在「」中的那段是同一份文字——note 是
+                # 拼给人看的，这里是给 assume._match_excerpt 用的，
+                # 两者必须来自同一个 excerpt 变量，不能事后各改各的。
+                source_excerpt=excerpt)
+            continue
 
-            hit = _match_announce(corpus, f.label, f.search_term)
-            if hit is not None:
-                excerpt, doc, core = hit
-                out[f.key] = FactorData(
-                    factor=f, status=ANNOUNCE,
-                    note=f"「{excerpt}」——{doc.cite()}"
-                         "（文字依据，是否给出具体数值待人工确认）"
-                         + _via_note(core, f),
-                    source_url=doc.url, source_retrieved=doc.retrieved,
-                    source_excerpt=excerpt)
-                continue
+        hit = _match_announce(corpus, f.label, f.search_term)
+        if hit is not None:
+            excerpt, doc, core = hit
+            out[f.key] = FactorData(
+                factor=f, status=ANNOUNCE,
+                note=f"「{excerpt}」——{doc.cite()}"
+                     "（文字依据，是否给出具体数值待人工确认）"
+                     + _via_note(core, f),
+                source_url=doc.url, source_retrieved=doc.retrieved,
+                source_excerpt=excerpt)
+            continue
 
-            hit = _match_report(corpus, f.label, f.search_term)
-            if hit is not None:
-                excerpt, doc, core = hit
-                out[f.key] = FactorData(
-                    factor=f, status=REPORT,
-                    note=f"「{excerpt}」——{doc.cite()}"
-                         "（文字依据，是否给出具体数值待人工确认）"
-                         + _via_note(core, f),
-                    source_url=doc.url, source_retrieved=doc.retrieved,
-                    source_excerpt=excerpt)
-                continue
+        hit = _match_report(corpus, f.label, f.search_term)
+        if hit is not None:
+            excerpt, doc, core = hit
+            out[f.key] = FactorData(
+                factor=f, status=REPORT,
+                note=f"「{excerpt}」——{doc.cite()}"
+                     "（文字依据，是否给出具体数值待人工确认）"
+                     + _via_note(core, f),
+                source_url=doc.url, source_retrieved=doc.retrieved,
+                source_excerpt=excerpt)
+            continue
 
-            try:
-                got = conclusions.search(client, f.label, f.key, industry, as_of)
-            except WebUnavailable as e:
-                # 能力边界不是故障，整轮研究不该因此停摆。措辞里显式写
-                # 「不能联网」而不是只转述异常原文。
-                out[f.key] = FactorData(
-                    factor=f, status=MISSING,
-                    note=f"不能联网 —— {e} —— 取值为估计，无数据支撑")
-                continue
+        try:
+            got = conclusions.search(client, f.label, f.key, industry, as_of)
+        except WebUnavailable as e:
+            # 能力边界不是故障，整轮研究不该因此停摆。措辞里显式写
+            # 「不能联网」而不是只转述异常原文。
+            out[f.key] = FactorData(
+                factor=f, status=MISSING,
+                note=f"不能联网 —— {e} —— 取值为估计，无数据支撑")
+            continue
 
-            # ── ④：模型综合结论 + 引用文章（spec 2026-10-13 ②）─────
-            # 结论可以综合、可以改写；代码只做了机械比对，结果全是标注。
-            # 有结论就落 EXTERNAL（决定 A：带 ⚠ 的也能作依据，门③ 那一行
-            # 带标记），交给门② 的人复核（决定 B）。
-            if not got.items:
-                why = "；".join(got.dropped) or "没搜到可用的结论"
-                out[f.key] = FactorData(factor=f, status=MISSING,
-                                        note=f"{why} —— 取值为估计")
-                continue
-            arts: list[Article] = []
-            for c in got.items:
-                for a in c.articles:
-                    if all(a is not x for x in arts):
-                        arts.append(a)
-            for a in arts:
-                if a.opened:
-                    # **入库的是代码抓回的全文，不是模型的结论。** 辩论② 与
-                    # 撰写简报引的是文章原文 ——「不得引用本系统的分析结论」
-                    # 那条规矩照样成立。
-                    corpus.add_if_visible(Document(
-                        doc_id=f"web_{f.key}_{len(corpus.docs)}",
-                        url=a.final_url, title=f"{f.label}：文章{a.no}",
-                        publisher=a.publisher, tier=a.tier,
-                        published=a.published, retrieved=as_of,
-                        text=a.text, prompt_limit=TEXT_LIMIT,
-                        date_source="model"))
-            flagged = sum(1 for c in got.items if c.flags())
-            note = (f"{len(got.items)} 条模型综合结论，待人工复核"
-                    + (f"（其中 {flagged} 条带 ⚠）" if flagged else ""))
-            if got.dropped:
-                note += "；" + "；".join(got.dropped)
-            degraded = any(a.degraded for a in arts)
-            if degraded:
-                n = sum(1 for a in arts if a.degraded)
-                note += (f"；其中 {n} 篇文章抓取时主机安全校验降级（本机 DNS "
-                         "被代理劫持，IP 层校验无从进行）——内网地址拦截本轮"
-                         "依赖代理侧")
-            out[f.key] = FactorData(factor=f, status=EXTERNAL,
-                                    findings=got.items, note=note,
-                                    host_check_degraded=degraded)
+        # ── ④：模型综合结论 + 引用文章（spec 2026-10-13 ②）─────
+        # 结论可以综合、可以改写；代码只做了机械比对，结果全是标注。
+        # 有结论就落 EXTERNAL（决定 A：带 ⚠ 的也能作依据，门③ 那一行
+        # 带标记），交给门② 的人复核（决定 B）。
+        if not got.items:
+            why = "；".join(got.dropped) or "没搜到可用的结论"
+            out[f.key] = FactorData(factor=f, status=MISSING,
+                                    note=f"{why} —— 取值为估计")
+            continue
+        arts: list[Article] = []
+        for c in got.items:
+            for a in c.articles:
+                if all(a is not x for x in arts):
+                    arts.append(a)
+        for a in arts:
+            if a.opened:
+                # **入库的是代码抓回的全文，不是模型的结论。** 辩论② 与
+                # 撰写简报引的是文章原文 ——「不得引用本系统的分析结论」
+                # 那条规矩照样成立。
+                corpus.add_if_visible(Document(
+                    doc_id=f"web_{f.key}_{len(corpus.docs)}",
+                    url=a.final_url, title=f"{f.label}：文章{a.no}",
+                    publisher=a.publisher, tier=a.tier,
+                    published=a.published, retrieved=as_of,
+                    text=a.text, prompt_limit=TEXT_LIMIT,
+                    date_source="model"))
+        flagged = sum(1 for c in got.items if c.flags())
+        note = (f"{len(got.items)} 条模型综合结论，待人工复核"
+                + (f"（其中 {flagged} 条带 ⚠）" if flagged else ""))
+        if got.dropped:
+            note += "；" + "；".join(got.dropped)
+        degraded = any(a.degraded for a in arts)
+        if degraded:
+            n = sum(1 for a in arts if a.degraded)
+            note += (f"；其中 {n} 篇文章抓取时主机安全校验降级（本机 DNS "
+                     "被代理劫持，IP 层校验无从进行）——内网地址拦截本轮"
+                     "依赖代理侧")
+        out[f.key] = FactorData(factor=f, status=EXTERNAL,
+                                findings=got.items, note=note,
+                                host_check_degraded=degraded)
     return out
 
 
@@ -568,7 +571,7 @@ def from_json(raw: dict, decomps: list[Decomposition]
     if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
         raise ValueError(f"factors.json 是旧格式（没有 \"schema\": {SCHEMA}）"
                          "—— 重跑 --brief 生成新格式")
-    factors = {f.key: f for d in decomps for f in d.factors}
+    factors = {f.key: f for f in flat_factors(decomps)}
     out: dict[str, FactorData] = {}
     notes: list[str] = []
     for key, x in (raw.get("factors") or {}).items():
diff --git a/tests/test_assume_formula.py b/tests/test_assume_formula.py
index 500faa5..10ef0ba 100644
--- a/tests/test_assume_formula.py
+++ b/tests/test_assume_formula.py
@@ -214,3 +214,19 @@ class TestDriverBlock:
         assert drv["base"]["factors"] == {"量": 100.0, "价": 9.5, "本": 7.2}
         assert drv["values"]["base"] == [[110.0, 10.0, 7.4]]
         assert ["name", "量"] in drv["formulas"]["revenue"]
+
+
+class TestPhase2:
+    def test_只有model时校验仍走因子路径(self):
+        """第二阶段从 assumptions.json 读回，公式化写法下只有 `model`、没有
+        `decomps`。传 `decomps=a._m()` 才走因子校验；传 `a.decomps or None`
+        （改动前的写法）会退回两标量校验 —— 那条路找不到营收增速/毛利率行。"""
+        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+        _check(a, 1, LEVEL)
+        back, _ = assume.from_json(assume.to_json(a))
+        assert back.decomps == [] and back._m() is not None
+        _check(back, 1, back._m())                       # 走因子路径，通过
+        # 改动前的写法：退回两标量校验，把因子行当成营收增速/毛利率行去查
+        # （`path()` 是两标量路径专用的措辞）。
+        with pytest.raises(AssumeFailed, match=r"path\(\)"):
+            _check(back, 1, back.decomps or None)
diff --git a/tests/test_factor_review.py b/tests/test_factor_review.py
index 88f5dd1..51ee218 100644
--- a/tests/test_factor_review.py
+++ b/tests/test_factor_review.py
@@ -151,3 +151,20 @@ class TestTwoScalarMode:
                    drivers=("营收增速", "毛利率"))
         assert len(seen) == 1 and len(r.findings) == 2
         assert len(r.flagged) == 1
+
+
+def test_公式化分解式的条目按公式分组且共用因子不重复():
+    from ir_agent.factor_review import expected_items
+    from ir_agent.methodology import parse as parse_meth
+
+    m = parse_meth("""模式: 水平
+因子: 量 | 出货量 | 万吨 | 年报 | 出货量
+因子: 价 | 单价 | 元 | 年报 | 单价
+因子: 本 | 单位成本 | 元 | 年报 | 单位成本
+公式: 营收 = 量 × 价
+公式: 毛利率 = (价 − 本) ÷ 价
+""").model
+    items = expected_items(m)
+    assert items.count(("因子", "单价")) == 1
+    assert ("正交", "出货量 × 单价") in items and ("正交", "单价 × 单位成本") in items
+    assert ("对账", "revenue") in items and ("对账", "gross_margin") in items
diff --git a/tests/test_full_context.py b/tests/test_full_context.py
index 3d7b1ce..9180f1d 100644
--- a/tests/test_full_context.py
+++ b/tests/test_full_context.py
@@ -3443,7 +3443,10 @@ def _stub_brief(monkeypatch, full, seq):
         return _Bal(note=f"辩论{round_}的平衡结论")
 
     class _M:
-        decompositions, industry = [], "测试业"
+        decompositions, industry, model = [], "测试业", None
+
+        def factors(self):
+            return []
 
     def meth(code, r, out, client, **k):
         seq.append("预研")
@@ -4187,3 +4190,87 @@ def test_待核清单_快照一节逐条_因子结论一节警示在前(tmp_path
     assert md.index("7628规格满产") < md.index("稼动率约78%"), "带 ⚠ 的排最前"
     assert "7628 ⚠ 找不到" in md
     assert "factors.json" in md, "要告诉人有异议直接改哪个文件"
+
+
+def test_第二阶段重新校验带着公式模型():
+    """**写计划时实测发现（2026-10-14 公式化）。** `run_valuation` 原先传
+    `decomps=a.decomps or None`；公式化写法下 `a.decomps` 是空列表，于是第二
+    阶段悄悄退回两标量校验，人批准的公式一行都不生效。源码断言同上一条
+    （真流程要联网建模，单测里跑不动）；后果的行为测试见
+    `tests/test_assume_formula.py::TestPhase2::test_只有model时校验仍走因子路径`。
+    """
+    import inspect
+
+    from ir_agent import full
+
+    src = inspect.getsource(full.run_valuation)
+    i_check = src.index("assume.check(")
+    call = src[i_check:src.index("anchor=anchor", i_check)]
+    code = [ln.split("#")[0] for ln in call.splitlines()]   # 去掉注释，只看代码
+    assert any("decomps=a._m()" in ln for ln in code)
+    assert not any("a.decomps or None" in ln for ln in code)
+
+
+def test_公式化分解式从第二次调用一路传到判断项(tmp_path, monkeypatch):
+    """**新写法（2026-10-14 公式化）走一遍第二、三次调用。** 下游原先读
+    `meth.decompositions`；新写法下它是空列表 —— 漏改一处，因子分解就在那一处
+    静默消失（采集不跑、辩论② 没有议题、判断项退回两标量）。"""
+    from ir_agent import full, gaps
+    from ir_agent.formula import FormulaModel
+    from ir_agent.methodology import parse as parse_meth
+
+    meth = parse_meth("""模式: 水平
+因子: 量 | 覆铜板出货量 | 万平方米 | 年报产销量 | 产销量
+因子: 价 | 覆铜板平均单价 | 元/平方米 | 研报价格跟踪 | 平均单价
+因子: 本 | 覆铜板单位成本 | 元/平方米 | 年报成本构成 | 单位成本
+公式: 营收 = 量 × 价
+公式: 毛利率 = (价 − 本) ÷ 价
+""")
+    meth.industry = "测试业"
+    seq = _offline(monkeypatch, full)
+    _stub_brief(monkeypatch, full, seq)
+
+    def meth_stub(code, r, out, client, **k):
+        (out / "drivers.md").write_text(
+            __import__("ir_agent.methodology", fromlist=["x"]).to_markdown(meth),
+            encoding="utf-8")
+        return meth, ""
+
+    collected: dict = {}
+
+    def collect(r, as_of, client, md, decomps, industry, skipped):
+        collected["decomps"] = decomps
+        return {f.key: gaps.FactorData(factor=f, status=gaps.MISSING, note="无")
+                for f in meth.factors()}
+
+    subjects: dict = {}
+    real_round = full._stance_round
+
+    def stance_round(client, out, path, round_, subj, *a, **k):
+        subjects[round_] = list(subj)
+        return real_round(client, out, path, round_, subj, *a, **k)
+
+    monkeypatch.setattr(full, "_research_methodology", meth_stub)
+    monkeypatch.setattr(full, "_collect_factors", collect)
+    monkeypatch.setattr(full, "_stance_round", stance_round)
+    full.run_brief("600183", _PhaseR(), date(2026, 10, 13), tmp_path, "xhl")
+    assert isinstance(collected["decomps"], FormulaModel), "采集拿到的不是公式模型"
+    assert subjects.get("B") == ["覆铜板出货量", "覆铜板平均单价", "覆铜板单位成本"]
+
+    import json as _j
+    from ir_agent import stance
+    (tmp_path / "brief.md").write_text("## 一节\n正文。", encoding="utf-8")
+    (tmp_path / "stance_b.json").write_text(_j.dumps(stance.StanceResult(
+        round_="B", balances=[]).to_json()), encoding="utf-8")
+    got: dict = {}
+
+    def propose(code, r, body, md, as_of, out, client, arts, **k):
+        got.update(k)
+        return ""
+
+    monkeypatch.setattr(full, "_propose_assumptions", propose)
+    full.run_assume("600183", _PhaseR(), date(2026, 10, 13), tmp_path, "xhl")
+    m = got["decomps"]
+    assert isinstance(m, FormulaModel) and m.mode == "水平" and not m.legacy, \
+        "第三次调用交给判断项的不是从 drivers.md 读回的公式模型"
+    assert set(got["factor_data"]) == {f.key for f in m.factors}
diff --git a/tests/test_gaps.py b/tests/test_gaps.py
index 813c4d3..1c72e04 100644
--- a/tests/test_gaps.py
+++ b/tests/test_gaps.py
@@ -1108,3 +1108,22 @@ class TestFactorsJsonV2:
         from ir_agent.gaps import from_json
         with pytest.raises(ValueError, match="schema"):
             from_json({"capacity": {"status": "无数据"}}, [])
+
+
+def test_水平模式共用因子只采集一次():
+    """「单价」同时出现在营收与毛利率两条公式里（公式化，2026-10-14），采集只做一次。"""
+    from ir_agent.gaps import DISCLOSED
+    from ir_agent.methodology import parse as parse_meth
+
+    m = parse_meth("""模式: 水平
+因子: 量 | 出货量 | 万吨 | 年报 | 出货量
+因子: 价 | 单价 | 元 | 年报 | 单价
+因子: 本 | 单位成本 | 元 | 年报 | 单位成本
+公式: 营收 = 量 × 价
+公式: 毛利率 = (价 − 本) ÷ 价
+""").model
+    keys = [f.key for f in m.factors]
+    data = collect(_ExplodingClient(), m, _led(*keys), Corpus(as_of=AS_OF),
+                   "电子", AS_OF)
+    assert list(data) == keys
+    assert all(fd.status == DISCLOSED for fd in data.values())
```

</details>

---

### Task 5: ashare-dcf 按记号生成 1b 表（两个仓库同一任务落地）

**Files:**
- ashare-dcf: Modify `ashare_build.py`（`_build_driver_sheet` 重写）、`ashare_verify.py`（`_MAX_REF_DEPTH` 5→64）、`tests/test_anchor_scenarios.py`
- ir-agent: Modify `tests/test_driver_sheet.py`（夹具改 schema 2，由 `from_decomps` 生成；ashare-dcf 路径按 `ASHARE_DCF_DIR` 查找，不写死主仓库）

**布局**：标题 + 两条公式原文 → 表头（B=Bull C=Base D=Bear，与 1_假设 的 `CHOOSE` 绑定）→ 常量区（B 列，旁注出处）→ 基期（各因子第 0 年、营收₀ 亿元、毛利率₀；水平模式下营收₀、毛利率₀ 是公式）→ 每年：各因子三档（黄底）、「营收（合成）」「营收增速（合成）」= 营收ₜ÷营收ₜ₋₁−1、「毛利率（合成）」。1_假设 引用增速行与毛利率行。记号翻译：短名 → 当年格；`上期(x)` → 上一年格（第 1 年 → 基期格；`上期(毛利率)` 第 2 年有锚定时 → 该档锚定值）；`基期(毛利率)` 有按年平移时 → 该档该年平移值；常量 → `$B$常量格`。

**为什么放宽引用链上限**：营收合成格逐年引用上一年，加上 1_假设 → 增速行两跳，十年预测链深约 23；原上限 5 会把正常的年度链误报成循环引用。

- [ ] **Step 1: 取两边测试，确认红**

```bash
cd ../ashare-dcf && git checkout 5232db7 -- tests/test_anchor_scenarios.py
python -m pytest tests/test_anchor_scenarios.py -q                     # 3 failed, 14 passed
cd ../ir-agent && git checkout a824d2d -- tests/test_driver_sheet.py
python -m pytest tests/test_driver_sheet.py -q                          # 3 failed, 2 passed
```

- [ ] **Step 2: 取实现，确认绿；两边全量**

```bash
cd ../ashare-dcf && git checkout 5232db7 -- ashare_build.py ashare_verify.py
python -m pytest tests/test_anchor_scenarios.py -q                     # 17 passed
python -m pytest -q --ignore=tests/test_financing.py --ignore=tests/test_financing_mutation.py   # 67 passed（约 3 分钟）
cd ../ir-agent && python -m pytest tests/test_driver_sheet.py -q        # 5 passed
```

- [ ] **Step 3: 两边分别提交**

```bash
cd ../ashare-dcf && git add ashare_build.py ashare_verify.py tests/test_anchor_scenarios.py
git commit -m "build: 1b_驱动分解按 ir-agent 公式记号生成 Excel 公式（schema 2：常量区、基期区、营收水平与增速行、锚定承接）；verify 引用链上限 5→64"
cd ../ir-agent && git add tests/test_driver_sheet.py
git commit -m "test: 1b 表测试改用 schema 2 夹具（由 from_decomps 生成）；ashare-dcf 路径按环境变量查找"
```

<details><summary>完整 diff（ashare-dcf 5232db7）</summary>

```diff
diff --git a/ashare_build.py b/ashare_build.py
index 5439fea..8691a12 100644
--- a/ashare_build.py
+++ b/ashare_build.py
@@ -435,83 +435,163 @@ def build(cal: dict, path: str) -> dict:
 _SCEN_COLS = ("B", "C", "D")          # Bull / Base / Bear —— 与 1_假设 一致
 
 
+_PCT_UNITS = ("变化率", "%", "百分比")
+
+
 def _build_driver_sheet(wb, drv, YE, assum_ws, g_row, m_row):
     """建「1b_驱动分解」，并把 1_假设 的增速/毛利率改为跨表引用。
 
-    **列序必须与 1_假设 一致（B=Bull C=Base D=Bear）**，它与
-    CHOOSE($B$SCEN,…) 绑定。写反不会让验算失败 —— 公式本身没错，
-    只是「选悲观」会选出乐观值。
+    **公式化（ir-agent 2026-10-14，drivers schema 2）。** ir-agent 交来模式、因子、
+    常量、基期值、每档逐年取值，以及两条公式的**已校验记号序列**。这里**不做
+    解析**，只把记号换成单元格引用（`_ref_tokens`）——解析只在 ir-agent 一处。
 
-    单位不能混：`变化率` 只进乘法链（营收 = Π(1+因子变化率)−1），
-    `pp` 只进加法链（毛利率 = 基期 + Σ因子贡献(pp)/100）。表里对每个
-    因子都标出单位与所属运算列，避免两者被误用互换。
+    布局：常量区 → 基期（各因子第 0 年、营收₀、毛利率₀）→ 每年：各因子三档取值
+    （黄底可手改）、「营收（合成）」「营收增速」「毛利率（合成）」。1_假设 的营收
+    增速与毛利率引用「营收增速」「毛利率（合成）」两行。改一个因子或常量，合成格
+    与下游估值随之重算。
+
+    **列序必须与 1_假设 一致（B=Bull C=Base D=Bear）**，它与 CHOOSE($B$SCEN,…)
+    绑定。写反不会让验算失败 —— 公式本身没错，只是「选悲观」会选出乐观值。
     """
     ds = wb.create_sheet("1b_驱动分解", 2)
-    ds.column_dimensions["A"].width=30
-    for c in "BCD": ds.column_dimensions[c].width=12
-    ds.column_dimensions["E"].width=10
+    ds.column_dimensions["A"].width = 34
+    for c in "BCD": ds.column_dimensions[c].width = 12
+    ds.column_dimensions["E"].width = 12
+    ds.column_dimensions["F"].width = 40
     r = 1
-    ds.cell(r, 1, "驱动因子分解").font = Font(bold=True, size=12); r += 2
-    for i, t in enumerate(["因子 / 年", "Bull", "Base", "Bear", "单位"]):
+    ds.cell(r, 1, f"驱动因子分解（{drv['mode']}模式）").font = Font(bold=True, size=12)
+    r += 1
+    for t in ("revenue", "gross_margin"):
+        txt = (drv.get("formula_text") or {}).get(t)
+        if txt:
+            ds.cell(r, 1, txt).font = Font(size=9, color="808080"); r += 1
+    r += 1
+    for i, t in enumerate(["项目 / 年", "Bull", "Base", "Bear", "单位", "出处"]):
         c = ds.cell(r, i + 1, t); c.font = F_COL; c.fill = P_B
     r += 1
 
-    rows: dict[str, dict[str, int]] = {}
-    for target in ("revenue", "gross_margin"):
-        block = drv.get(target)
-        if not block:
+    factors = drv["factors"]
+    const_row: dict[str, int] = {}
+    for c in drv.get("constants") or []:
+        ds.cell(r, 1, f"常量 {c['name']}").font = Font(size=10)
+        cc = ds.cell(r, 2, c["value"]); cc.font = F_IN; cc.fill = P_Y
+        ds.cell(r, 6, c.get("source", "")).font = Font(size=9, color="808080")
+        const_row[c["name"]] = r; r += 1
+
+    base = drv["base"]
+    base_row: dict[str, int] = {}
+    for f in factors:
+        if f["short"] not in (base.get("factors") or {}):
             continue
-        rows[target] = {}
-        # 覆盖年数以 values 实际给出的档数为准，不强行对齐 YE 全长 ——
-        # AI 逐因子填写可能只覆盖部分预测年（比如方法论预研只做到第一年），
-        # 未覆盖的年份 1_假设 保留原直接取值，不被本表覆盖。
-        n_cov = len(block["values"]["base"])
-        for yi, y in enumerate(YE[:n_cov]):
-            for fi, f in enumerate(block["factors"]):
-                ds.cell(r, 1, f"{y} {f['label']}").font = Font(size=10)
-                for ci, case in enumerate(("bull", "base", "bear")):
-                    cc = ds.cell(r, 2 + ci, block["values"][case][yi][fi])
-                    cc.font = F_IN
-                    cc.number_format = PCT if f["unit"] == "变化率" else "0.00"
-                    cc.fill = P_Y
-                ds.cell(r, 5, f["unit"]).font = Font(size=9, color="808080")
-                rows[target][f"{yi}:{fi}"] = r
-                r += 1
+        ds.cell(r, 1, f"基期 {f['label']}").font = Font(size=10)
+        cc = ds.cell(r, 2, base["factors"][f["short"]]); cc.font = F_IN; cc.fill = P_Y
+        cc.number_format = PCT if f["unit"] in _PCT_UNITS else "0.00"
+        ds.cell(r, 5, f["unit"]).font = Font(size=9, color="808080")
+        base_row[f["short"]] = r; r += 1
+
+    def ref(tokens, yi, col, case, rows):
+        """记号 → Excel 公式文本。yi 为第几年（0 起），-1 表示基期。"""
+        out = []
+        for kind, val in tokens:
+            if kind in ("num", "op"):
+                out.append(val)
+            elif kind == "lp":
+                out.append("(")
+            elif kind == "rp":
+                out.append(")")
+            elif kind == "name":
+                if val in const_row:
+                    out.append(f"$B${const_row[val]}")
+                elif yi < 0:
+                    out.append(f"$B${base_row[val]}")
+                else:
+                    out.append(f"{col}{rows['f'][yi][val]}")
+            elif kind == "prev":
+                if yi < 0:
+                    raise ValueError(f"第 0 年取不到 上期({val})")
+                if val == "营收":
+                    out.append(f"$B${rows['rev0']}" if yi == 0
+                               else f"{col}{rows['rev'][yi - 1]}")
+                elif val == "毛利率":
+                    anc = drv.get("anchor_y1")
+                    if yi == 0:
+                        out.append(f"$B${rows['gm0']}")
+                    elif yi == 1 and anc:
+                        out.append(f"{anc[case]}")
+                    else:
+                        out.append(f"{col}{rows['gm'][yi - 1]}")
+                else:
+                    out.append(f"$B${base_row[val]}" if yi == 0
+                               else f"{col}{rows['f'][yi - 1][val]}")
+            elif kind == "base":
+                if val == "营收":
+                    out.append(f"$B${rows['rev0']}")
+                elif val == "毛利率":
+                    bls = drv.get("base_levels")
+                    out.append(f"{bls[case][max(yi, 0)]}" if bls and yi >= 0
+                               else f"$B${rows['gm0']}")
+                else:
+                    out.append(f"$B${base_row[val]}")
+        return "".join(out)
+
+    rows = {"f": [], "rev": [], "growth": [], "gm": []}
+    fm = drv["formulas"]
+    # 第 0 年的营收、毛利率：变化模式取账本（亿元），水平模式由基期因子代入公式。
+    ds.cell(r, 1, "基期 营收（亿元）").font = Font(size=10, bold=True)
+    if drv["mode"] == "水平":
+        ds.cell(r, 2, "=" + ref(fm["revenue"], -1, "B", "base", rows)).font = F_FX
+    else:
+        ds.cell(r, 2, base["revenue"]).font = F_IN
+    ds.cell(r, 2).number_format = "#,##0.00"
+    rows["rev0"] = r; r += 1
+    ds.cell(r, 1, "基期 毛利率").font = Font(size=10, bold=True)
+    if drv["mode"] == "水平":
+        ds.cell(r, 2, "=" + ref(fm["gross_margin"], -1, "B", "base", rows)).font = F_FX
+    else:
+        ds.cell(r, 2, base["margin"]).font = F_IN
+    ds.cell(r, 2).number_format = PCT
+    rows["gm0"] = r; r += 2
 
-            label = "营收增速（合成）" if target == "revenue" else "毛利率（合成）"
+    # 覆盖年数以 values 实际给出的档数为准，不强行对齐 YE 全长 —— 未覆盖的年份
+    # 1_假设 保留原直接取值，不被本表覆盖。
+    n_cov = len(drv["values"]["base"])
+    cases = ("bull", "base", "bear")
+    for yi, y in enumerate(YE[:n_cov]):
+        frow = {}
+        for fi, f in enumerate(factors):
+            ds.cell(r, 1, f"{y} {f['label']}").font = Font(size=10)
+            for ci, case in enumerate(cases):
+                cc = ds.cell(r, 2 + ci, drv["values"][case][yi][fi])
+                cc.font = F_IN; cc.fill = P_Y
+                cc.number_format = PCT if f["unit"] in _PCT_UNITS else "0.00"
+            ds.cell(r, 5, f["unit"]).font = Font(size=9, color="808080")
+            frow[f["short"]] = r; r += 1
+        rows["f"].append(frow)
+        for key, label, toks, fmt in (("rev", "营收（合成）", fm["revenue"], "#,##0.00"),
+                                      ("gm", "毛利率（合成）", fm["gross_margin"], PCT)):
             ds.cell(r, 1, f"{y} {label}").font = Font(size=10, bold=True)
             for ci, col in enumerate(_SCEN_COLS):
-                refs = [f"{col}{rows[target][f'{yi}:{fi}']}"
-                        for fi in range(len(block["factors"]))]
-                if block["op"] == "乘":
-                    expr = "*".join(f"(1+{x})" for x in refs) + "-1"
-                else:
-                    # 按档、按年的基期（ir-agent 在锚定开启时给）：第 2 年起毛利率从
-                    # 各档锚定后的首年水平接着走，不回到年报基期——否则 Base 档会
-                    # 出现锚定年 30.69%、次年掉回 25.87% 的断层（生益科技实跑）。
-                    # 不给时维持单一基期。公式形状不变：常数 + 引用。
-                    bls = block.get("base_levels")
-                    base = (bls[("bull", "base", "bear")[ci]][yi] if bls
-                            else block.get("base_level", 0.0))
-                    expr = f"{base}+(" + "+".join(refs) + ")/100"
-                cc = ds.cell(r, 2 + ci, f"={expr}")
-                cc.font = F_FX; cc.number_format = PCT
-            rows[target][f"synth:{yi}"] = r
-            r += 1
+                cc = ds.cell(r, 2 + ci, "=" + ref(toks, yi, col, cases[ci], rows))
+                cc.font = F_FX; cc.number_format = fmt
+            rows[key].append(r); r += 1
+            if key == "rev":
+                ds.cell(r, 1, f"{y} 营收增速（合成）").font = Font(size=10, bold=True)
+                for ci, col in enumerate(_SCEN_COLS):
+                    prev = (f"$B${rows['rev0']}" if yi == 0
+                            else f"{col}{rows['rev'][yi - 1]}")
+                    cc = ds.cell(r, 2 + ci, f"={col}{rows['rev'][yi]}/{prev}-1")
+                    cc.font = F_FX; cc.number_format = PCT
+                rows["growth"].append(r); r += 1
         r += 1
 
     # 1_假设 的判断项改为引用本表 —— 只覆盖本表实际分解到的年份
-    for target, start in (("revenue", g_row), ("gross_margin", m_row)):
-        if target not in rows:
-            continue
-        for key, r_ in rows[target].items():
-            if not key.startswith("synth:"):
-                continue
-            yi = int(key.split(":")[1])
+    for start, key in ((g_row, "growth"), (m_row, "gm")):
+        for yi, r_ in enumerate(rows[key]):
             for ci, col in enumerate(_SCEN_COLS):
                 assum_ws.cell(start + yi, 2 + ci,
                               f"='1b_驱动分解'!{col}{r_}").font = F_FX
 
+
 def _sheets(wb, cal, path):
     M,PR,O,GEN,JU,DC = cal["meta"],cal["periods"],cal["open"],cal["gen"],cal["judg"],cal["dcf"]
     IS,BS,CF = cal["stmts"]["income"],cal["stmts"]["balance"],cal["stmts"]["cashflow"]
diff --git a/ashare_verify.py b/ashare_verify.py
index 315a297..1b4c015 100644
--- a/ashare_verify.py
+++ b/ashare_verify.py
@@ -29,7 +29,10 @@ _REF_RE = re.compile(
     # "C1"，被误判成引用。跨表引用必须显式带「!」，因此收紧为必选。
     r"^=\s*(?:'([^']+)'|([^!'\s]+))!\$?([A-Z]+)\$?(\d+)\s*$")
 _SELF_REF_RE = re.compile(r"^=\s*\$?([A-Z]+)\$?(\d+)\s*$")
-_MAX_REF_DEPTH = 5
+# 引用层数上限。原为 5；ir-agent 公式化（2026-10-14）后「1b_驱动分解」的营收
+# 合成格逐年引用上一年（第 t 年 → 第 t−1 年 → … → 基期），加上 1_假设 → 增速行
+# 的两跳，十年预测的链深约 2×10+3。64 仍能拦住真正的循环引用。
+_MAX_REF_DEPTH = 64
 _ARITH_CELL_RE = re.compile(r"\$?([A-Z]{1,3})\$?(\d{1,7})")
 
 
diff --git a/tests/test_anchor_scenarios.py b/tests/test_anchor_scenarios.py
index c199e8b..32815bd 100644
--- a/tests/test_anchor_scenarios.py
+++ b/tests/test_anchor_scenarios.py
@@ -189,23 +189,44 @@ def test_calibrate把三档写进中期块(cal):
     assert it["share3"][0] == pytest.approx(min(it["share_hist"]), abs=1e-4)
 
 
-def _with_drivers(cal, base_levels=None):
-    """给夹具 cal 注入一份最小的因子分解块（ir-agent 的 to_driver_block 产出形状）。"""
+# 旧乘/加分解式经 ir-agent `formula.from_decomps` 翻译后的两条公式的记号序列
+# （`Formula.tokens()`，全括号）：营收 = 上期(营收)*(1+产能)*(1+单价)；
+# 毛利率 = 基期(毛利率)+(结构+成本)/100。
+_REV_TOKENS = [["lp", "("], ["lp", "("], ["prev", "营收"], ["op", "*"],
+               ["lp", "("], ["num", "1"], ["op", "+"], ["name", "产能"], ["rp", ")"],
+               ["rp", ")"], ["op", "*"],
+               ["lp", "("], ["num", "1"], ["op", "+"], ["name", "单价"], ["rp", ")"],
+               ["rp", ")"]]
+_GM_TOKENS = [["lp", "("], ["base", "毛利率"], ["op", "+"],
+              ["lp", "("], ["lp", "("], ["name", "结构"], ["op", "+"],
+              ["name", "成本"], ["rp", ")"], ["op", "/"], ["num", "100"], ["rp", ")"],
+              ["rp", ")"]]
+
+
+def _with_drivers(cal, base_levels=None, anchor_y1=None):
+    """给夹具 cal 注入一份最小的因子分解块（ir-agent 的 to_driver_block 产出形状，
+    schema 2：一个块、公式以记号交付）。"""
     c = copy.deepcopy(cal)
     n = len(c["periods"]["proj"])
-    rev = {"op": "乘",
-           "factors": [{"key": "cap", "label": "产能", "unit": "变化率"},
-                       {"key": "asp", "label": "单价", "unit": "变化率"}],
-           "values": {k: [[0.05, 0.01]] * n for k in ("bull", "base", "bear")}}
-    gm = {"op": "加",
-          "factors": [{"key": "mix", "label": "结构", "unit": "pp"},
-                      {"key": "cost", "label": "成本", "unit": "pp"}],
-          "values": {"bull": [[1.0, 0.5]] * n, "base": [[0.5, 0.2]] * n,
-                     "bear": [[0.0, -0.2]] * n},
-          "base_level": 0.2647}
+    annual = c["periods"]["annual"]
+    drv = {"schema": 2, "mode": "变化",
+           "factors": [{"key": "cap", "short": "产能", "label": "产能", "unit": "变化率"},
+                       {"key": "asp", "short": "单价", "label": "单价", "unit": "变化率"},
+                       {"key": "mix", "short": "结构", "label": "结构", "unit": "pp"},
+                       {"key": "cost", "short": "成本", "label": "成本", "unit": "pp"}],
+           "constants": [],
+           "formulas": {"revenue": _REV_TOKENS, "gross_margin": _GM_TOKENS},
+           "formula_text": {"revenue": "营收 = 上期(营收)*(1+产能)*(1+单价)",
+                            "gross_margin": "毛利率 = 基期(毛利率)+(结构+成本)/100"},
+           "base": {"revenue": c["rev"][annual[-1]], "margin": 0.2647, "factors": {}},
+           "values": {"bull": [[0.05, 0.01, 1.0, 0.5]] * n,
+                      "base": [[0.05, 0.01, 0.5, 0.2]] * n,
+                      "bear": [[0.05, 0.01, 0.0, -0.2]] * n}}
     if base_levels:
-        gm["base_levels"] = base_levels
-    c["judg"]["drivers"] = {"revenue": rev, "gross_margin": gm}
+        drv["base_levels"] = base_levels
+    if anchor_y1:
+        drv["anchor_y1"] = anchor_y1
+    c["judg"]["drivers"] = drv
     return c
 
 
@@ -226,18 +247,20 @@ def test_1b毛利率合成可以按档按年用不同基期(cal, tmp_path):
     p = tmp_path / "w.xlsx"
     B.build(_with_drivers(cal, bl), str(p))
     y1, y2 = cal["periods"]["proj"][0], cal["periods"]["proj"][1]
-    assert _gm_synth_formula(p, y1, "B").startswith("=0.2647+")
-    assert _gm_synth_formula(p, y2, "B").startswith("=0.301+")
-    assert _gm_synth_formula(p, y2, "C").startswith("=0.29+")
-    assert _gm_synth_formula(p, y2, "D").startswith("=0.28+")
+    assert _gm_synth_formula(p, y1, "B").startswith("=(0.2647+")
+    assert _gm_synth_formula(p, y2, "B").startswith("=(0.301+")
+    assert _gm_synth_formula(p, y2, "C").startswith("=(0.29+")
+    assert _gm_synth_formula(p, y2, "D").startswith("=(0.28+")
 
 
 def test_不给按年基期时维持单一基期(cal, tmp_path):
     p = tmp_path / "w.xlsx"
     B.build(_with_drivers(cal), str(p))
     y2 = cal["periods"]["proj"][1]
+    ds = load_workbook(p)["1b_驱动分解"]
+    gm0 = _rowmap(ds)["基期 毛利率"]
     for col in "BCD":
-        assert _gm_synth_formula(p, y2, col).startswith("=0.2647+")
+        assert _gm_synth_formula(p, y2, col).startswith(f"=($B${gm0}+")
 
 
 def test_按年基期之下公式引擎验算仍然全过(cal, tmp_path):
@@ -266,3 +289,45 @@ def test_旧形状的中期块也能建表且三档口径一致(cal, tmp_path):
         r = m[label]
         got = tuple(a.cell(r, c).value for c in (2, 3, 4))
         assert got == tuple(cal["interim"][key]), label
+
+
+def _level_block(cal):
+    """水平模式：营收 = 量 × 价；毛利率 = (价 − 本) ÷ 价；带一个常量。"""
+    c = copy.deepcopy(cal)
+    n = len(c["periods"]["proj"])
+    lp, rp = ["lp", "("], ["rp", ")"]
+    c["judg"]["drivers"] = {
+        "schema": 2, "mode": "水平",
+        "factors": [{"key": "q", "short": "量", "label": "出货量", "unit": "万吨"},
+                    {"key": "p", "short": "价", "label": "单价", "unit": "元"},
+                    {"key": "c", "short": "本", "label": "单位成本", "unit": "元"}],
+        "constants": [{"name": "系数", "value": 1.0, "source": "示意"}],
+        "formulas": {
+            "revenue": [lp, lp, ["name", "量"], ["op", "*"], ["name", "价"], rp,
+                        ["op", "*"], ["name", "系数"], rp],
+            "gross_margin": [lp, lp, ["name", "价"], ["op", "-"], ["name", "本"], rp,
+                             ["op", "/"], ["name", "价"], rp]},
+        "base": {"revenue": None, "margin": 0.25,
+                 "factors": {"量": 100.0, "价": 9.5, "本": 7.2}},
+        "values": {"bull": [[100.0 * 1.08 ** (y + 1), 11.0, 7.3] for y in range(n)],
+                   "base": [[100.0 * 1.05 ** (y + 1), 10.0, 7.4] for y in range(n)],
+                   "bear": [[100.0 * 1.02 ** (y + 1), 9.0, 7.5] for y in range(n)]}}
+    return c
+
+
+def test_水平模式的基期营收由公式算出_常量区可引用(cal, tmp_path):
+    p = tmp_path / "w.xlsx"
+    B.build(_level_block(cal), str(p))
+    ds = load_workbook(p)["1b_驱动分解"]
+    m = _rowmap(ds)
+    assert str(ds.cell(m["基期 营收（亿元）"], 2).value).startswith("=((")
+    assert "常量 系数" in m and ds.cell(m["常量 系数"], 6).value == "示意"
+    y1 = cal["periods"]["proj"][0]
+    assert f"$B${m['常量 系数']}" in str(ds[f"C{m[f'{y1} 营收（合成）']}"].value)
+
+
+def test_水平模式公式引擎验算全过(cal, tmp_path):
+    p = tmp_path / "w.xlsx"
+    B.build(_level_block(cal), str(p))
+    fails, _ = V.verify(str(p), quiet=True)
+    assert not fails
```

</details>

<details><summary>完整 diff（ir-agent tests/test_driver_sheet.py）</summary>

```diff
diff --git a/tests/test_driver_sheet.py b/tests/test_driver_sheet.py
index d2afcc9..57c5c5f 100644
--- a/tests/test_driver_sheet.py
+++ b/tests/test_driver_sheet.py
@@ -3,6 +3,7 @@
 from __future__ import annotations
 
 import json
+import os
 import pickle
 import sys
 from datetime import date
@@ -10,7 +11,11 @@ from pathlib import Path
 
 import pytest
 
-sys.path.insert(0, "/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf")
+from ir_agent import dcf
+
+# 与 `dcf._load` 同一个查找规则（环境变量优先），不写死主仓库路径 —— 写死时，
+# 在别的工作树里改 ashare-dcf 会悄悄导入主仓库的旧版。
+sys.path.insert(0, os.environ.get(dcf.ENV_DIR) or str(dcf.DEFAULT_DIR))
 
 # 建表需要一份真实的 calibrate 输出 —— 它有六十多个键，手写夹具既繁琐又会
 # 与真实结构漂移（本项目已因「固件编码我想象的结构」栽过一次: stmts 被写成
@@ -38,20 +43,27 @@ def _minimal_cal():
 
 
 def _drivers():
-    return {
-        "revenue": {
-            "op": "乘",
-            "factors": [{"key": "capacity", "label": "产能", "unit": "变化率"},
-                        {"key": "asp", "label": "单价", "unit": "变化率"}],
-            "values": {"bull": [[0.14, 0.01]], "base": [[0.10, -0.02]],
-                       "bear": [[0.05, -0.06]]}},
-        "gross_margin": {
-            "op": "加",
-            "base_level": 0.1415,
-            "factors": [{"key": "util", "label": "稼动率摊薄", "unit": "pp"},
-                        {"key": "cost", "label": "上游成本", "unit": "pp"}],
-            "values": {"bull": [[0.5, 0.3]], "base": [[-1.0, -0.5]],
-                       "bear": [[-2.5, -1.5]]}}}
+    """schema 2 驱动块（2026-10-14 公式化）。公式记号由 ir-agent 自己的
+    `formula.from_decomps` 生成，不手写 —— 夹具与真实产出同源。"""
+    from ir_agent.drivers import Decomposition, Factor, Op
+    from ir_agent.formula import from_decomps
+
+    m = from_decomps([
+        Decomposition("revenue", Op.MULTIPLY, [Factor("capacity", "产能", "变化率"),
+                                                Factor("asp", "单价", "变化率")]),
+        Decomposition("gross_margin", Op.ADD_PP, [Factor("util", "稼动率摊薄", "pp"),
+                                                   Factor("cost", "上游成本", "pp")])])
+    return {"schema": 2, "mode": m.mode,
+            "factors": [{"key": f.key, "short": f.short, "label": f.label,
+                         "unit": f.unit} for f in m.factors],
+            "constants": [],
+            "formulas": {t: [list(x) for x in fm.tokens()]
+                         for t, fm in m.formulas.items()},
+            "formula_text": {t: m.describe(t) for t in m.formulas},
+            "base": {"revenue": 100.0, "margin": 0.1415, "factors": {}},
+            "values": {"bull": [[0.14, 0.01, 0.5, 0.3]],
+                       "base": [[0.10, -0.02, -1.0, -0.5]],
+                       "bear": [[0.05, -0.06, -2.5, -1.5]]}}
 
 
 def test_无drivers时不建表(tmp_path):
@@ -93,9 +105,11 @@ def test_合成行是公式不是常量(tmp_path):
     ws = load_workbook(p)["1b_驱动分解"]
     cells = {ws.cell(r, 1).value: r for r in range(1, 40)
              if ws.cell(r, 1).value}
-    v = ws.cell(cells["2026E 营收增速（合成）"], 2).value
+    v = ws.cell(cells["2026E 营收（合成）"], 2).value
     assert isinstance(v, str) and v.startswith("=")
-    assert "1+" in v or "(1" in v
+    assert "(1+" in v
+    g = ws.cell(cells["2026E 营收增速（合成）"], 2).value
+    assert isinstance(g, str) and g.startswith("=") and g.endswith("-1")
 
 
 def test_假设表改为跨表引用(tmp_path):
```

</details>

---

### Task 6: 跨仓库对账

**Files:** Create `tests/test_formula_workbook.py`（ir-agent）

工作簿合成格用 `ashare_verify._cell_value`（ashare-dcf 自己的公式引擎）求值，与 ir-agent `assume.synthesize` 逐档逐年比对（误差 1e-9）。三种分解式：水平模式；变化模式带常量与 `上期(毛利率)`；旧乘/加。夹具用 ashare-dcf 的 `tests/fixtures/cal_600183.pkl`（含半年报，锚定路径被覆盖）。另验算全过、常量区与基期区存在、手改一个因子合成格随之变化。

这是验证任务：Task 5 落地后它直接绿。它的敏感度靠变异证明。

- [ ] **Step 1: 取测试并跑**

```bash
git checkout a005fd5 -- tests/test_formula_workbook.py
python -m pytest tests/test_formula_workbook.py -q      # 5 passed
python -m pytest -q -m "not live" -W error              # 2340 passed, 8 skipped
```

- [ ] **Step 2: 变异验证**（改的是 ashare-dcf 的 `ashare_build.py`，拷贝还原）
  - 第 2 年 `上期(毛利率)` 不接锚定值（`elif yi == 1 and anc:` → `elif False:`）→ `[变化模式_常量与上期毛利率]` 红；
  - `基期(毛利率)` 忽略按年平移（`bls[case][max(yi, 0)]` → `bls[case][0]`）→ `[旧乘加分解式]` 红。

- [ ] **Step 3: 提交**

```bash
git add tests/test_formula_workbook.py
git commit -m "test: 跨仓库对账 —— 工作簿合成格经 ashare_verify 求值与 ir-agent 引擎逐档逐年一致"
```

<details><summary>完整 diff（a005fd5）</summary>

```diff
diff --git a/tests/test_formula_workbook.py b/tests/test_formula_workbook.py
new file mode 100644
index 0000000..552aae0
--- /dev/null
+++ b/tests/test_formula_workbook.py
@@ -0,0 +1,147 @@
+"""跨仓库对账：工作簿「1b_驱动分解」的合成格 ≡ ir-agent 计算引擎（spec 2026-10-14 四）。
+
+翻译只要错一个引用（上期取成本年、基期取成上期、档位列错位……），这里就对不上。
+工作簿合成格用 `ashare_verify` 自己的公式引擎求值（与「公式引擎验算全过」同一套），
+不用 ir-agent 的引擎去算工作簿 —— 两边各算各的，才叫对账。
+"""
+from __future__ import annotations
+
+import os
+import pickle
+from decimal import Decimal as D
+from pathlib import Path
+
+import pytest
+
+from ir_agent import assume, dcf
+from ir_agent.assume import Case
+from ir_agent.drivers import Decomposition, Factor, Op
+from ir_agent.methodology import parse as parse_meth
+
+_DIR = Path(os.environ.get(dcf.ENV_DIR) or dcf.DEFAULT_DIR)
+_FIX = _DIR / "tests" / "fixtures" / "cal_600183.pkl"
+try:
+    _MODS = dcf._load(_DIR)
+except dcf.DcfUnavailable:
+    _MODS = None
+pytestmark = pytest.mark.skipif(
+    _MODS is None or not _FIX.exists(),
+    reason="未找到 ashare-dcf 或其离线夹具 tests/fixtures/cal_600183.pkl")
+
+Q = "「依据原文」"
+
+
+def _cal():
+    with open(_FIX, "rb") as f:
+        return pickle.load(f)
+
+
+def _rows(spec: dict, years: int) -> str:
+    """spec: {短名: (悲观, 中性, 乐观)} → 逐年的假设行。取值可以是字符串（逐年
+    同值），也可以是 `lambda y: str`（随年份变 —— 水平模式下水平逐年同值会让第
+    2 年起三档营收增速都是 0，被合成后单调校验正确地驳回）。"""
+    def v(x, y):
+        return x(y) if callable(x) else x
+    return "\n".join(
+        f"假设: {k} | 第{y}年 | {v(lo, y)} | {v(mid, y)} | {v(hi, y)} | 理由 {Q}"
+        for y in range(1, years + 1) for k, (lo, mid, hi) in spec.items())
+
+
+LEVEL = parse_meth("""模式: 水平
+因子: 量 | 覆铜板出货量 | 万平方米 | 年报产销量 | 产销量
+因子: 价 | 覆铜板平均单价 | 元/平方米 | 研报价格跟踪 | 平均单价
+因子: 本 | 覆铜板单位成本 | 元/平方米 | 年报成本构成 | 单位成本
+公式: 营收 = 量 × 价
+公式: 毛利率 = (价 − 本) ÷ 价
+""").model
+LEVEL_ROWS = {"量": tuple((lambda g: lambda y: f"{100 * (1 + g) ** y:.4f}")(g)
+                          for g in (0.02, 0.05, 0.08)),
+              "价": ("9", "10", "11"), "本": ("7.5", "7.4", "7.3")}
+LEVEL_BASE = f"基期: 量 | 100 | {Q}\n基期: 价 | 9.5 | {Q}\n基期: 本 | 7.2 | {Q}"
+
+CHANGE = parse_meth("""模式: 变化
+因子: 稼动率变动 | 稼动率变动 | pp | 年报 | 产能利用率
+因子: 价增 | 平均单价同比变化率 | 变化率 | 研报 | 平均单价
+因子: 提价 | 提价对毛利率的逐年贡献 | pp | 调价函 | 调价
+因子: 原料 | 原材料对毛利率的逐年侵蚀 | pp | 年报 | 原材料价格
+常量: 上期稼动率 | 78% | 年报经营情况讨论「产能利用率约78%」
+公式: 营收 = 上期(营收) × (1 + 稼动率变动 / 100 / 上期稼动率) × (1 + 价增)
+公式: 毛利率 = 上期(毛利率) + (提价 + 原料) / 100
+""").model
+CHANGE_ROWS = {"稼动率变动": ("-2", "1", "3"), "价增": ("-3%", "1%", "4%"),
+               "提价": ("-0.5", "0.3", "0.8"), "原料": ("-0.6", "-0.2", "0.1")}
+
+LEGACY = [Decomposition("revenue", Op.MULTIPLY, [Factor("cap", "产能", "变化率"),
+                                                  Factor("asp", "单价", "变化率")]),
+          Decomposition("gross_margin", Op.ADD_PP, [Factor("mix", "结构", "pp"),
+                                                     Factor("cost", "成本", "pp")])]
+LEGACY_ROWS = {"产能": ("2%", "5%", "8%"), "单价": ("-3%", "0%", "2%"),
+               "结构": ("-1", "0.5", "1.5"), "成本": ("-2", "-1", "-0.5")}
+
+
+def _build(model, rows, extra, tmp_path):
+    D_, C_, B_, V_ = _MODS
+    cal = _cal()
+    years = len(cal["periods"]["proj"])
+    a = assume.parse(_rows(rows, years) + ("\n" + extra if extra else ""), years,
+                     decomps=model)
+    a.labels = list(cal["periods"]["proj"])
+    out = assume.apply_to(cal, a)
+    out["judg"]["drivers"] = assume.to_driver_block(a, cal)
+    path = tmp_path / "w.xlsx"
+    B_.build(out, str(path))
+    return a, cal, path
+
+
+def _sheet_values(path, label_suffix, cal):
+    from openpyxl import load_workbook
+
+    V_ = _MODS[3]
+    wb = load_workbook(path)
+    ds = wb["1b_驱动分解"]
+    rowmap = {str(ds.cell(r, 1).value): r for r in range(1, ds.max_row + 1)}
+    out = {}
+    for col, case in zip("BCD", ("bull", "base", "bear")):
+        out[case] = [D(str(V_._cell_value(wb, ds, f"{col}{rowmap[f'{y} {label_suffix}']}")))
+                     for y in cal["periods"]["proj"]]
+    return out, wb, ds, rowmap
+
+
+def _engine(a, cal):
+    bm = assume._base_margin_of(cal)
+    bases = assume.margin_bases(a, bm, assume.anchor_from_cal(cal))
+    return {c.name.lower(): assume.synthesize(a, c, bm, bases)
+            for c in (Case.BULL, Case.BASE, Case.BEAR)}
+
+
+@pytest.mark.parametrize("model,rows,extra", [
+    (LEVEL, LEVEL_ROWS, LEVEL_BASE),
+    (CHANGE, CHANGE_ROWS, ""),
+    (LEGACY, LEGACY_ROWS, ""),
+], ids=["水平模式", "变化模式_常量与上期毛利率", "旧乘加分解式"])
+def test_工作簿合成格与ir_agent引擎逐档逐年一致(model, rows, extra, tmp_path):
+    a, cal, path = _build(model, rows, extra, tmp_path)
+    want = _engine(a, cal)
+    growth, *_ = _sheet_values(path, "营收增速（合成）", cal)
+    margin, *_ = _sheet_values(path, "毛利率（合成）", cal)
+    for case in ("bull", "base", "bear"):
+        for y in range(len(growth[case])):
+            assert abs(growth[case][y] - want[case][0][y]) < D("1e-9"), (case, y)
+            assert abs(margin[case][y] - want[case][1][y]) < D("1e-9"), (case, y)
+
+
+def test_工作簿公式引擎验算全过(tmp_path):
+    _, _, path = _build(LEVEL, LEVEL_ROWS, LEVEL_BASE, tmp_path)
+    fails, _ = _MODS[3].verify(str(path), quiet=True)
+    assert not fails
+
+
+def test_常量区与基期区存在_改一个因子合成格随之变化(tmp_path):
+    _, cal, path = _build(CHANGE, CHANGE_ROWS, "", tmp_path)
+    before, wb, ds, rowmap = _sheet_values(path, "营收增速（合成）", cal)
+    assert "常量 上期稼动率" in rowmap and "基期 营收（亿元）" in rowmap
+    y1 = cal["periods"]["proj"][0]
+    ds[f"C{rowmap[f'{y1} 平均单价同比变化率']}"] = 0.20
+    V_ = _MODS[3]
+    after = D(str(V_._cell_value(wb, ds, f"C{rowmap[f'{y1} 营收增速（合成）']}")))
+    assert after != before["base"][0]
```

</details>

---

### Task 7: 收尾

- [ ] **Step 1: 两边全量**

```bash
cd ir-agent && python -m pytest -q -m "not live" -W error --collect-only | tail -1    # 2348 tests collected
python -m pytest -q -m "not live" -W error                                            # 2340 passed, 8 skipped
cd ../ashare-dcf && python -m pytest -q --ignore=tests/test_financing.py --ignore=tests/test_financing_mutation.py   # 67 passed
```

- [ ] **Step 2: 与开发分支逐字节一致**

```bash
cd ir-agent && git diff formula-dev -- ir_agent tests | head      # 应为空
cd ../ashare-dcf && git diff formula-dev -- ashare_build.py ashare_verify.py tests | head   # 应为空
```

- [ ] **Step 3: 清理开发分支与工作树**（确认上一步为空之后）

```bash
git worktree list            # 两个仓库各自执行，移除 formula-dev 工作树
git branch -D formula-dev
```

- [ ] **Step 4: 不试跑**。spec 五：两个子项目之间预研没有任何语义审查（四道字面闸门已去、裁决者未上线）。下一步是子项目二（提出者 + 裁决者）的 spec。

## 自查

**1. Spec 覆盖**

| spec 要求 | 任务 |
|---|---|
| 一、写法与数据结构（模式/因子/常量/公式，短名，代码只查能不能算） | 2（`Factor.short` 在 1） |
| 二、求值器白名单、`上期`/`基期`、两种模式第 0 年口径、锚定（含平移规则）、隐含份额、历史对账单步口径、出错明确 | 1、3 |
| 三、门③ 短名与完整名、基期行、提示词、沿用校验、清单（按单位、合成一节、锚定只影响第 1 年标注） | 3 |
| 四、assumptions.json schema 2、第二阶段同一引擎、驱动块记号、1b 表布局、跨仓库对账 | 3、4、5、6 |
| 五、旧格式翻译逐位相同、过渡期闸门、测试 | 1、2、3 |
| 补充 1–3（`基期(·)`、锚定平移、单步对账） | 1、3 |

**2. 写计划时实测发现并已修的问题**
- 旧乘法链历史对账若按真实营收作尺度，约 45% 的真实量级样本末位与旧引擎不同；改为按 1 代入，并用真实量级数据的测试钉住（Task 3）；
- 第二阶段 `decomps=a.decomps or None` 在新写法下静默退回两标量（Task 4）；
- `ashare_verify` 引用链上限 5 会把年度链误报成循环引用（Task 5）；
- `tests/test_driver_sheet.py` 写死主仓库 ashare-dcf 路径，在别的工作树改 ashare-dcf 时会悄悄导入旧版（Task 5）；
- 名字不合法时先报出的是「未声明的名字」，误导修改方向（Task 2）。

**3. 类型一致性**：`FormulaModel` 在 methodology（`Methodology.model`）、assume（`AssumptionSet.model`、`decomps` 参数）、gaps/factor_review（`decomps` 参数）、dcf（`has_model`）一致；`flat_factors` 是唯一的去重实现；驱动块字段名在 `assume.to_driver_block` 与 `ashare_build._build_driver_sheet` 两边一致（跨仓库对账钉住）。
