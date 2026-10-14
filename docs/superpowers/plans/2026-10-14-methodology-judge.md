# 方法论预研：提出者 + 审核者（子项目二）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 方法论预研从「一个模型写 + 代码字面闸门」改为「提出者写、审核者按清单逐项四级作答、有致命即驳回、最多 3 轮、不过停下交人工」。

**Architecture:** 新增 `ir_agent/methodology_review.py`（清单生成、作答解析、引文核对、只给审核者的额外事实、多轮编排）；`full._research_methodology` 改走它，落盘 `drivers_review.json`（结论 + 指纹）与 `drivers_rounds.md`（各轮记录），重跑按指纹沿用、人改过的不送审只标注，失败抛 `MethodologyStop` 由 `run_brief` 停下；门② 待核清单、门③ 判断项清单各加「方法论审核」；门③ 因子结构审查只留 [对账]。

**Tech Stack:** Python 3.11、pytest；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-14-methodology-judge-design.md`

## 做法（同子项目一）

代码先在独立工作树、分支 `judge-dev` 实现并跑通；再从 main 新建临时工作树按本计划**完整演练**一遍，下文每一步的数字都是演练实测。执行 = 在 main 上按任务 `git checkout <提交> -- <文件>`：先取测试跑红，再取实现跑绿，变异验证，提交。每个任务附完整 diff。

## Global Constraints

- 开发分支 `judge-dev` 的提交：`4c16bb8`（闸门交接）、`0b92f70`（审核者模块）、`7537c67`（接入第二次调用）、`ecccc97`（门③）。
- 四级结论原话：通过（逻辑完全正确，资料能够对得上）/ 提醒（逻辑部分正确，资料对得上，但结论较为牵强）/ 致命（逻辑完全无法支撑结论）/ 待采集（逻辑正确，资料尚未采集到）。
- 有一项致命即驳回；代码检查不过与审核驳回都算一轮，合计最多 3 轮；审核者漏答补答最多 2 次、不消耗轮数；仍不全 = 审核未完成，**不当通过**。
- 停下交人工，**不退回两标量**；唯一仍退回两标量的是「取不到申万行业分类」。
- 人改过的 drivers.md（指纹与 `drivers_review.json` 不一致或没有审核记录）**不送审，只标注**「人工修改后未经审核」。
- 审核者不联网；提出者联网。
- 变异验证一律「拷贝备份 → 改 → 跑 → 拷回」，**不用 `git checkout` 还原**（会连带丢掉未提交的改动）。
- 全量：主仓库旁有真实 ashare-dcf，8 条依赖它的测试会运行；演练所在的临时目录没有，记为跳过。下文全量数字写「通过 + 跳过」，在主仓库上应为二者之和、0 跳过。

## 基线（执行前在 main 上核对）

```bash
python -m pytest -q -m "not live" -W error --collect-only | tail -1     # 2348 tests collected
```

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `ir_agent/drivers.py` | `check_search_term`（检索词长度只对旧写法） | 1 |
| `ir_agent/methodology.py` | 去掉移交审核者的三道检查；抽出 `build_prompt`；`to_markdown(review=)` | 1、3 |
| `ir_agent/assume.py` | 旧格式读回时查检索词长度；`AssumptionSet.method_review` 与清单一节 | 1、3 |
| `ir_agent/methodology_review.py`（新） | 清单、作答解析、引文核对、额外事实、`propose_and_review`、各轮记录 | 2 |
| `tests/judge_stub.py`（新） | 脚本化假审核者（测试共用） | 2 |
| `ir_agent/full.py` | 接入、落盘、指纹读回、`MethodologyStop` 停下、门② 一节、门③ 传遗留 | 3 |
| `ir_agent/research.py` | 终端「方法论审核」⚠；下一步给人工做法 | 3 |
| `ir_agent/factor_review.py` | 只出 [对账] | 4 |

---

### Task 1: 闸门交接给审核者

**Files:** Modify `ir_agent/drivers.py`、`ir_agent/methodology.py`、`ir_agent/assume.py`、`tests/test_drivers.py`、`tests/test_methodology.py`

**内容**：每条公式 2–5 个因子、声明了须进公式、检索词至少 2 字 —— 新写法下代码不再拦（移交审核者的 [公式]、[因子] 条目）；检索词长度从 `Factor` 构造挪到 `drivers.check_search_term`，只在旧 `分解:` 写法解析与旧 assumptions.json 读回时调用。另把预研提示词拼装抽成 `methodology.build_prompt`（提出者与审核者共用）。

**Interfaces:** Produces `drivers.check_search_term(f)`、`methodology.build_prompt(industry, code, *, facts, corpus_note, qualitative, stance_note, available_keys) -> str`。

- [ ] **Step 1: 取测试，确认红**

```bash
git checkout 4c16bb8 -- tests/test_drivers.py tests/test_methodology.py
python -m pytest tests/test_drivers.py tests/test_methodology.py -q        # 2 failed, 134 passed
```

- [ ] **Step 2: 取实现，确认绿；全量**

```bash
git checkout 4c16bb8 -- ir_agent/drivers.py ir_agent/methodology.py ir_agent/assume.py
python -m pytest tests/test_drivers.py tests/test_methodology.py -q        # 136 passed
python -m pytest -q -m "not live" -W error                               # 2339 passed + 8（主仓库合计 2347）
```

- [ ] **Step 3: 变异验证**：`_parse_legacy` 里删掉 `check_search_term(factors[-1])` → `test_检索词的长度下限仍然生效` 红。

- [ ] **Step 4: 提交**

```bash
git add ir_agent/drivers.py ir_agent/methodology.py ir_agent/assume.py tests/test_drivers.py tests/test_methodology.py
git commit -m "methodology/drivers: 因子个数、须进公式、检索词长度移交审核者；检索词长度只对旧写法保留；抽出 build_prompt"
```

<details><summary>完整 diff（4c16bb8）</summary>

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index 134c8a3..fa0f35e 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -36,6 +36,7 @@ from ir_agent.formula import (FormulaError, FormulaInvalid, FormulaModel,
                               parse_formula, simulate, Constant as _Constant,
                               parse_constant)
 from ir_agent.drivers import (Decomposition, DecompositionInvalid, Factor, Op,
+                              check_search_term,
                               TARGET_LABEL, Residual, check_dimensions, compose,
                               reconcile)
 from ir_agent.gaps import FactorData
@@ -184,12 +185,13 @@ def _decomp_to_dict(d: Decomposition) -> dict:
 
 def _decomp_from_dict(d: dict) -> Decomposition:
     op = next(o for o in Op if o.value == d["op"])
-    return Decomposition(
-        target=d["target"], op=op,
-        factors=[Factor(key=f["key"], label=f["label"], unit=f["unit"],
-                        source_hint=f.get("source_hint", ""),
-                        search_term=f.get("search_term", ""))
-                for f in d["factors"]])
+    factors = [Factor(key=f["key"], label=f["label"], unit=f["unit"],
+                      source_hint=f.get("source_hint", ""),
+                      search_term=f.get("search_term", ""))
+               for f in d["factors"]]
+    for f in factors:                  # 旧格式读回：检索词长度仍由代码查
+        check_search_term(f)
+    return Decomposition(target=d["target"], op=op, factors=factors)
 
 
 def to_json(a: "AssumptionSet", **extra) -> dict:
diff --git a/ir_agent/drivers.py b/ir_agent/drivers.py
index 7bee2d5..3be67ac 100644
--- a/ir_agent/drivers.py
+++ b/ir_agent/drivers.py
@@ -65,16 +65,20 @@ class Factor:
     # 它，读回时由 `formula.short_from_label` 从完整名生成。
     short: str = ""
 
-    def __post_init__(self) -> None:
-        # **只查长度，这是唯一的闸门。** 1 字检索词（「率」「价」）在任何
-        # 年报里都必然命中，那不是「模型决定」而是「定然命中」，会把所有
-        # 因子都抬成「年报披露」、让这个状态标签彻底失去信息量。
-        # frozen=True 只禁赋值，不禁在这里抛异常。
-        if self.search_term and len(self.search_term) < 2:
-            raise DecompositionInvalid(
-                f"因子「{self.label}」的检索词 {self.search_term!r} 只有一个字"
-                "——一个字的词在任何年报里都必然命中，那不是依据而是巧合。"
-                "请给一个至少两个字、且指向这个因子本身的词。")
+
+def check_search_term(f: "Factor") -> None:
+    """检索词至少两个字。**只对旧的 `分解:` 写法生效**（读回旧文件时）。
+
+    1 字检索词（「率」「价」）在任何年报里都必然命中，那不是「模型决定」而是
+    「定然命中」，会把所有因子都抬成「年报披露」。公式化写法（2026-10-14 子项目
+    二）把这一项移交审核者：[因子] 条目判「检索词是否过短或过泛」并附实际命中
+    次数 —— 所以它不再是 `Factor` 构造时的硬检查。
+    """
+    if f.search_term and len(f.search_term) < 2:
+        raise DecompositionInvalid(
+            f"因子「{f.label}」的检索词 {f.search_term!r} 只有一个字"
+            "——一个字的词在任何年报里都必然命中，那不是依据而是巧合。"
+            "请给一个至少两个字、且指向这个因子本身的词。")
 
 
 @dataclass
diff --git a/ir_agent/methodology.py b/ir_agent/methodology.py
index 4c1c43e..8b52773 100644
--- a/ir_agent/methodology.py
+++ b/ir_agent/methodology.py
@@ -20,9 +20,9 @@ import re
 import unicodedata
 from dataclasses import dataclass, field
 
-from ir_agent.drivers import (MAX_FACTORS, MIN_FACTORS, TARGET_LABEL,
-                              Decomposition, DecompositionInvalid, Factor, Op,
-                              check_closure)
+from ir_agent.drivers import (TARGET_LABEL, Decomposition,
+                              DecompositionInvalid, Factor, Op, check_closure,
+                              check_search_term)
 from ir_agent.formula import (MODES, RESERVED, TARGETS as _FORMULA_TARGETS,
                               Constant, FormulaInvalid, FormulaModel,
                               flat_factors as _flat, from_decomps,
@@ -139,15 +139,11 @@ def _parse_formulas(text: str) -> Methodology:
                 "这个 key 对采集结果与工作簿行，撞车的那个会被错认。换一个更有"
                 "区分度的完整名。")
         keys[key] = label
-        try:
-            factors.append(Factor(
-                key=key, label=label, unit=unit,
-                source_hint=parts[3] if len(parts) > 3 else "",
-                search_term=(_strip_term_label(parts[4])
-                             if len(parts) > 4 else ""),
-                short=short))
-        except DecompositionInvalid as e:
-            raise MethodologyFailed(str(e)) from e
+        factors.append(Factor(
+            key=key, label=label, unit=unit,
+            source_hint=parts[3] if len(parts) > 3 else "",
+            search_term=(_strip_term_label(parts[4]) if len(parts) > 4 else ""),
+            short=short))
     consts: list[Constant] = []
     for h in _CONST.finditer(text):
         parts = _fields(h.group(1))
@@ -181,19 +177,9 @@ def _parse_formulas(text: str) -> Methodology:
         model.validate()
     except FormulaInvalid as e:
         raise MethodologyFailed(str(e)) from e
-    for tgt in formulas:
-        n = len(model.factors_of(tgt))
-        if not MIN_FACTORS <= n <= MAX_FACTORS:
-            raise MethodologyFailed(
-                f"「{TARGET_LABEL[tgt]}」的公式引用了 {n} 个因子 —— 每条 "
-                f"{MIN_FACTORS}–{MAX_FACTORS} 个：一个不叫分解，超过五个人"
-                "核实不过来。")
-    used = {f.short for t in formulas for f in model.factors_of(t)}
-    unused = [f.label for f in factors if f.short not in used]
-    if unused:
-        raise MethodologyFailed(
-            f"声明了却没有任何公式用到的因子：{'、'.join(unused)} —— 删掉，或写进"
-            "公式。不进公式的因子不影响营收与毛利率，却要逐年填值、联网采集。")
+    # 因子个数（每条 2–5）、声明了必须进公式、检索词长度：子项目一过渡期由代码
+    # 查，子项目二（2026-10-14）移交审核者 —— [公式] 判因子个数是否合适，[因子]
+    # 判是否进了公式、检索词是否过短或过泛。代码只留会让计算或存储做不下去的。
     return Methodology(model=model)
 
 
@@ -256,6 +242,7 @@ def _parse_legacy(text: str) -> Methodology:
                 factors.append(Factor(key=_slug(label), label=label,
                                       unit=unit, source_hint=hint,
                                       search_term=term))
+                check_search_term(factors[-1])
             except DecompositionInvalid as e:
                 raise MethodologyFailed(str(e)) from e
         d = Decomposition(target=_TARGETS[tgt], op=_OPS[op], factors=factors)
@@ -479,23 +466,9 @@ def research(client, industry: str, code: str,
     这样已开启联网工具的 client，否则这里的「先搜索」要求形同虚设。
     这条前提容易被后来人忽略，因此单独写在这里。
     """
-    # **新输入全部可选，不传就退回行业级预研。** 那条路是
-    # `_research_methodology` 的既有降级出口（预研失败 → 两标量模式），
-    # 不能因为新增输入而失效 —— 见 `test_不传新输入时仍退回行业级预研`。
-    blocks = []
-    if facts:
-        blocks.append(f"【可用事实】\n{facts}")
-    if corpus_note:
-        blocks.append(f"【已入库资料】\n{corpus_note}")
-    if qualitative:
-        blocks.append(f"【定性结论】\n{qualitative}")
-    if stance_note:
-        blocks.append(f"【多空平衡点】\n{stance_note}")
-    if available_keys:
-        blocks.append("【账本里已有的 key】\n" + "、".join(available_keys))
-    prompt = PROMPT.format(industry=industry, code=code,
-                           context=("\n" + "\n\n".join(blocks) + "\n"
-                                    if blocks else ""))
+    prompt = build_prompt(industry, code, facts=facts, corpus_note=corpus_note,
+                          qualitative=qualitative, stance_note=stance_note,
+                          available_keys=available_keys)
     last, raw = "", ""
     for attempt in range(1, max_attempts + 1):
         raw = client(prompt)
@@ -515,6 +488,31 @@ def research(client, industry: str, code: str,
         raw=raw)
 
 
+def build_prompt(industry: str, code: str, *, facts: str = "",
+                 corpus_note: str = "", qualitative: str = "",
+                 stance_note: str = "",
+                 available_keys: tuple[str, ...] = ()) -> str:
+    """预研提示词。提出者用它；审核者看的「与提出者相同的资料」也是这一份
+    （`methodology_review`，spec 2026-10-14 子项目二 二）。
+
+    **新输入全部可选，不传就退回行业级预研**（两标量降级路径要走它）。
+    """
+    blocks = []
+    if facts:
+        blocks.append(f"【可用事实】\n{facts}")
+    if corpus_note:
+        blocks.append(f"【已入库资料】\n{corpus_note}")
+    if qualitative:
+        blocks.append(f"【定性结论】\n{qualitative}")
+    if stance_note:
+        blocks.append(f"【多空平衡点】\n{stance_note}")
+    if available_keys:
+        blocks.append("【账本里已有的 key】\n" + "、".join(available_keys))
+    return PROMPT.format(industry=industry, code=code,
+                         context=("\n" + "\n\n".join(blocks) + "\n"
+                                  if blocks else ""))
+
+
 _RAW_HEADING = "## 分解式原始定义（供人工编辑与重跑读回）"
 
 # to_markdown 反过来重建 DSL 行时要用的映射——与解析方向 _TARGETS/
diff --git a/tests/test_drivers.py b/tests/test_drivers.py
index d63e2de..147415a 100644
--- a/tests/test_drivers.py
+++ b/tests/test_drivers.py
@@ -425,10 +425,17 @@ def test_检索词的长度下限仍然生效():
     """
     import pytest
 
-    from ir_agent.drivers import DecompositionInvalid, Factor
+    from ir_agent.drivers import DecompositionInvalid, Factor, check_search_term
+    from ir_agent.methodology import MethodologyFailed, parse
 
+    # 2026-10-14 子项目二：长度检查移出 `Factor` 构造，只对旧 `分解:` 写法生效；
+    # 公式化写法交给审核者的 [因子] 条目判「检索词是否过短或过泛」。
     with pytest.raises(DecompositionInvalid):
-        Factor(key="k", label="产能利用率", unit="变化率", search_term="率")
+        check_search_term(Factor(key="k", label="产能利用率", unit="变化率",
+                                 search_term="率"))
+    with pytest.raises(MethodologyFailed, match="只有一个字"):
+        parse("分解: 营收 | 乘 | 产能利用率:变化率:年报:率 | 单价:变化率:年报:单价\n"
+              "分解: 毛利率 | 加 | 铜箔:pp:年报:铜箔 | 稼动率:pp:年报:稼动率\n")
     # 空检索词通过——旧的 3 段 DSL 与旧的 JSON 都靠这条向后兼容
     assert Factor(key="k", label="产能利用率", unit="变化率").search_term == ""
 
diff --git a/tests/test_methodology.py b/tests/test_methodology.py
index 4643b58..a54bfe7 100644
--- a/tests/test_methodology.py
+++ b/tests/test_methodology.py
@@ -771,7 +771,6 @@ class TestFormulaDSL:
         (_NEW.replace("公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100\n", ""),
          "缺少 毛利率"),
         (_NEW.replace("(提价 + 原料)", "(提价 + 原料 + 没声明)"), "未声明的名字「没声明」"),
-        (_NEW.replace("(提价 + 原料) / 100", "提价 / 100"), "引用了 1 个因子"),
         (_NEW.replace("常量: 上期稼动率 | 78% | 年报经营情况讨论「产能利用率约78%」",
                       "常量: 上期稼动率 | 78% |"), "出处不能空"),
         (_NEW.replace("因子: 原料 |", "因子: 原 料 |"), "不合法"),
@@ -783,10 +782,14 @@ class TestFormulaDSL:
         with pytest.raises(MethodologyFailed, match=frag):
             parse(text)
 
-    def test_声明了却没用到的因子被驳回(self):
-        from ir_agent.methodology import MethodologyFailed, parse
-        with pytest.raises(MethodologyFailed, match="没有任何公式用到"):
-            parse(_NEW.replace("陷阱:", "因子: 闲置 | 闲置因子 | pp | x | 闲置因子\n陷阱:"))
+    def test_移交审核者的三项代码不再拦(self):
+        """子项目二（2026-10-14）：因子个数、声明了须进公式、检索词长度移交
+        审核者 —— [公式] 判因子个数，[因子] 判是否进了公式、检索词是否过短。"""
+        from ir_agent.methodology import parse
+        m = parse(_NEW.replace("(提价 + 原料) / 100", "提价 / 100")
+                  .replace("陷阱:", "因子: 闲置 | 闲置因子 | pp | x | 率\n陷阱:"))
+        assert len(m.model.factors_of("gross_margin")) == 1
+        assert m.model.factor("闲置").search_term == "率"
 
     def test_覆盖声明写短名也认_显示完整名(self):
         from ir_agent.methodology import coverage, parse
```

</details>

---

### Task 2: 审核者模块 `methodology_review.py`

**Files:** Create `ir_agent/methodology_review.py`、`tests/judge_stub.py`、`tests/test_methodology_review.py`

**Interfaces（Task 3 依赖）:**
- `LEVELS`、`KINDS`、`MAX_ROUNDS = 3`、`JUDGE_TRIES = 3`
- `checklist(model, themes) -> list[(类别, 条目)]`（7 类；共用因子对只列一次；无主题不列 [覆盖]；账本条目写成 `短名 [[key]]`，避免条目名里出现冒号）
- `parse(raw, expected) -> list[Verdict]`：**按预期条目名匹配**（完整名里可能有括号、冒号），容忍列表符号与加粗；漏答或提醒/致命/待采集无理由 → `ReviewIncomplete`
- `annotate_quotes(...)`：引文「…」与 `[[key@期间]]` 找不到只标注 `quote_note`
- `extra_facts(model, *, ledger, as_of, sections, corpus, themes, coverage_decl) -> str`：账本实际取值、命中次数、覆盖核对、水平模式第 0 年试算
- `review(judge, *, proposal, model, materials, extra, themes, hay, ledger, as_of) -> ReviewResult`
- `propose_and_review(proposer, judge, *, industry, code, prompt, materials, themes, ledger, as_of, sections, corpus) -> (Methodology, ReviewResult, rounds)`；`ReviewStop(msg, rounds)`
- `ReviewResult(verdicts, rounds, fingerprint)`：`.fatal()`、`.flagged()`、`.counts()`、`.to_json()/from_json()`、`.markdown()`；`fingerprint(dsl)`；`rounds_markdown(rounds)`；`dump_review(res)`
- 审核规则正文不得出现与清单同形的 `- [类别]` 行（守卫测试钉住：假审核者就把它当成了条目）。

- [ ] **Step 1: 取测试，确认红**

```bash
git checkout 0b92f70 -- tests/test_methodology_review.py tests/judge_stub.py
python -m pytest tests/test_methodology_review.py -q        # 1 error（ModuleNotFoundError）
```

- [ ] **Step 2: 取实现，确认绿；全量**

```bash
git checkout 0b92f70 -- ir_agent/methodology_review.py
python -m pytest tests/test_methodology_review.py -q        # 21 passed
python -m pytest -q -m "not live" -W error                  # 2360 passed + 8
```

- [ ] **Step 3: 变异验证**（拷贝还原）
  - `propose_and_review` 里 `if not res.fatal():` → `if True:` → `test_致命即驳回…`、`test_三轮仍有致命停下`、`test_各轮记录` 红；
  - `parse` 里 `no_reason = []` → `test_漏答与没写理由都算不完整` 红；
  - `review` 末尾 `raise ReviewIncomplete(last)` → `return ReviewResult()` → `test_审核者始终答不完整就停下_不当通过` 红。

- [ ] **Step 4: 提交**

```bash
git add ir_agent/methodology_review.py tests/judge_stub.py tests/test_methodology_review.py
git commit -m "methodology_review: 方法论预研的审核者 —— 清单生成、逐项四级作答、引文核对、只给审核者的额外事实、提出→检查→审核多轮编排"
```

<details><summary>完整 diff（0b92f70）</summary>

```diff
diff --git a/ir_agent/methodology_review.py b/ir_agent/methodology_review.py
new file mode 100644
index 0000000..690137c
--- /dev/null
+++ b/ir_agent/methodology_review.py
@@ -0,0 +1,425 @@
+"""方法论预研的审核者（子项目二，spec `2026-10-14-methodology-judge-design.md`）。
+
+**为什么要有审核者。** 子项目一把分解式改成「模型写公式、代码求值」，代码只查
+能不能算；「分母在公司内外」「是不是同一个量被算两遍」这类判断原先靠
+`drivers` 里的字面闸门近似 —— 生益科技第二次调用被「非公司内部占比」里一个带
+「非」的「占比」拦了三次。这类判断改由第二个模型角色按清单逐项审。
+
+**清单由代码出，不由审核者出**（沿用 `factor_review`）：否则可以通过少列条目
+回避问题。**每条都必须作答**，沉默不等于通过。
+
+**四级结论**（用户决定，原话）：
+- 通过：逻辑完全正确，资料能够对得上；
+- 提醒：逻辑部分正确，资料对得上，但结论较为牵强；
+- 致命：逻辑完全无法支撑结论；
+- 待采集：逻辑正确，资料尚未采集到。
+
+有一项致命即驳回、交回提出者重写；最多 3 轮；仍不通过停下交人工 —— 不再退回
+两标量模式（那条路让整套因子链路悄悄消失）。
+
+**审核者的输入比提出者多**（门③ `factor_review` 的教训：只给同样的资料，审核
+只是同一个模型换个角色再说一遍）：代码额外算出账本实际取值、检索词命中次数、
+覆盖核对、水平模式第 0 年试算。审核者不联网，资料不够由「待采集」承接。
+"""
+
+from __future__ import annotations
+
+import hashlib
+import json
+import re
+from dataclasses import dataclass, field
+from decimal import Decimal
+from itertools import combinations
+
+LEVELS = ("通过", "提醒", "致命", "待采集")
+KINDS = ("模式", "因子", "正交", "公式", "常量", "账本", "覆盖")
+MAX_ROUNDS = 3          # 提出者最多写 3 轮（代码检查不过、审核驳回都算一轮）
+JUDGE_TRIES = 3         # 审核者作答 1 次 + 漏答补答 2 次，不消耗提出者轮数
+
+# 行首允许列表符号与加粗（模型常写 `- ` 或 `**[因子] …**`）。
+_ITEM = re.compile(rf"(?m)^[ \t]*[-*•·]*[ \t]*(?=\[(?:{'|'.join(KINDS)})\])")
+_HEAD = re.compile(rf"\[({'|'.join(KINDS)})\]\s*(.*)", re.S)
+_QUOTE = re.compile(r"「([^」]{4,})」")
+_REF = re.compile(r"\[\[([^\]@]+)@([^\]]+)\]\]")
+_KEY_DECL = re.compile(r"\[\[([A-Za-z_][\w.]*)(?:@[^\]]*)?\]\]")
+
+
+class ReviewIncomplete(Exception):
+    """审核者没有覆盖全部条目，或提醒/致命/待采集没写理由 —— 沉默不能等于通过。"""
+
+
+class ReviewStop(Exception):
+    """停下交人工：3 轮仍有致命项，或审核者始终答不完整。`rounds` 是全部轮次。"""
+
+    def __init__(self, msg: str, rounds: list):
+        super().__init__(msg)
+        self.rounds = rounds
+
+
+@dataclass(frozen=True)
+class Verdict:
+    kind: str
+    subject: str
+    level: str
+    why: str
+    quote_note: str = ""     # 引文核验不过时的标注（只标注，不改结论）
+
+    def line(self) -> str:
+        tail = f"（⚠ {self.quote_note}）" if self.quote_note else ""
+        return f"[{self.kind}] {self.subject}：{self.level}　{self.why}{tail}"
+
+
+@dataclass
+class ReviewResult:
+    verdicts: list = field(default_factory=list)
+    rounds: int = 1
+    fingerprint: str = ""
+
+    def fatal(self) -> list[Verdict]:
+        return [v for v in self.verdicts if v.level == "致命"]
+
+    def flagged(self) -> list[Verdict]:
+        """要人看的：提醒与待采集。"""
+        return [v for v in self.verdicts if v.level in ("提醒", "待采集")]
+
+    def counts(self) -> dict[str, int]:
+        return {lv: sum(1 for v in self.verdicts if v.level == lv) for lv in LEVELS}
+
+    def to_json(self) -> dict:
+        return {"fingerprint": self.fingerprint, "rounds": self.rounds,
+                "verdicts": [{"kind": v.kind, "subject": v.subject,
+                              "level": v.level, "why": v.why,
+                              "quote_note": v.quote_note} for v in self.verdicts]}
+
+    @classmethod
+    def from_json(cls, d: dict) -> "ReviewResult":
+        return cls(verdicts=[Verdict(x["kind"], x["subject"], x["level"],
+                                     x.get("why", ""), x.get("quote_note", ""))
+                             for x in d.get("verdicts") or []],
+                   rounds=int(d.get("rounds", 1)),
+                   fingerprint=d.get("fingerprint", ""))
+
+    def markdown(self) -> list[str]:
+        """drivers.md「方法论审核」一节：通过只给条数，提醒/待采集逐条列。"""
+        c = self.counts()
+        lines = ["## 方法论审核", "",
+                 f"第 {self.rounds} 轮通过：通过 {c['通过']} 条、提醒 {c['提醒']} 条、"
+                 f"待采集 {c['待采集']} 条。", ""]
+        lines += [f"- {v.line()}" for v in self.flagged()]
+        if self.flagged():
+            lines.append("")
+        return lines
+
+
+@dataclass
+class Round:
+    """一轮的完整记录：提出者原文、代码检查结果、审核者逐项结论。"""
+
+    n: int
+    raw: str
+    code_error: str = ""
+    review: ReviewResult | None = None
+    incomplete: str = ""
+
+
+def fingerprint(dsl: str) -> str:
+    """分解式定义的指纹。重跑读回时比对，变了 = 人改过（决定 8）。"""
+    return hashlib.sha256(dsl.strip().encode("utf-8")).hexdigest()[:16]
+
+
+def checklist(model, themes: list[str]) -> list[tuple[str, str]]:
+    """代码生成「必须作答的条目」（spec 二）。`themes` 为空时不列 [覆盖]。"""
+    items: list[tuple[str, str]] = [("模式", "模式")]
+    items += [("因子", f"{f.short}（{f.label}）") for f in model.factors]
+    seen: set[tuple[str, str]] = set()
+    for t in ("revenue", "gross_margin"):
+        if t not in model.formulas:
+            continue
+        for a, b in combinations([f.short for f in model.factors_of(t)], 2):
+            if (a, b) not in seen:
+                seen.add((a, b))
+                items.append(("正交", f"{a} × {b}"))
+    items += [("公式", {"revenue": "营收", "gross_margin": "毛利率"}[t])
+              for t in ("revenue", "gross_margin") if t in model.formulas]
+    items += [("常量", c.name) for c in model.constants]
+    items += [("账本", f"{f.short} [[{k}]]") for f in model.factors
+              for k in dict.fromkeys(_KEY_DECL.findall(f.source_hint or ""))]
+    items += [("覆盖", th) for th in themes]
+    return items
+
+
+def parse(raw: str, expected: list[tuple[str, str]]) -> list[Verdict]:
+    """解析审核者的回答。**按预期条目名匹配**，不靠正则切条目名 —— 完整名里
+    可能带冒号、括号。缺条目、或提醒/致命/待采集没写理由 → ReviewIncomplete。"""
+    by_kind: dict[str, list[str]] = {}
+    for k, s in expected:
+        by_kind.setdefault(k, []).append(s)
+    got: dict[tuple[str, str], Verdict] = {}
+    for block in _ITEM.split(raw):
+        m = _HEAD.match(block.strip())
+        if not m:
+            continue
+        kind, rest = m.group(1), m.group(2).strip()
+        subj = next((s for s in sorted(by_kind.get(kind, []), key=len, reverse=True)
+                     if rest.startswith(s)), None)
+        if subj is None:
+            continue
+        tail = rest[len(subj):].lstrip()
+        if not tail or tail[0] not in "：:":
+            continue
+        tail = tail[1:].strip().lstrip("*")
+        level = next((lv for lv in sorted(LEVELS, key=len, reverse=True)
+                      if tail.startswith(lv)), None)
+        if level is None:
+            continue
+        why = tail[len(level):].strip(" 　\t*：:")
+        got[(kind, subj)] = Verdict(kind, subj, level, " ".join(why.split()))
+    missing = [e for e in expected if e not in got]
+    no_reason = [v for v in got.values() if v.level != "通过" and not v.why]
+    if missing or no_reason:
+        parts = []
+        if missing:
+            parts.append("以下条目没有给出结论（每一条都必须回答，沉默不等于通过）："
+                         + "、".join(f"[{k}] {s}" for k, s in missing[:10]))
+        if no_reason:
+            parts.append("以下条目判为提醒/致命/待采集却没写理由："
+                         + "、".join(f"[{v.kind}] {v.subject}" for v in no_reason[:10]))
+        raise ReviewIncomplete("；".join(parts))
+    return [got[e] for e in expected]
+
+
+def annotate_quotes(verdicts: list[Verdict], *, hay: str, ledger, as_of) -> list[Verdict]:
+    """理由里引的原文「…」与账本占位符能否找到。**只标注，不改结论**（与多空
+    辩论同一处理）。"""
+    from ir_agent.sources.external import norm
+
+    h = norm(hay or "")
+    out: list[Verdict] = []
+    for v in verdicts:
+        notes = []
+        for q in _QUOTE.findall(v.why):
+            if norm(q) not in h:
+                notes.append(f"引文「{q[:20]}」在语料与年报里找不到")
+        for key, per in _REF.findall(v.why):
+            try:
+                ledger.get(key, per, as_of=as_of)
+            except Exception:                              # noqa: BLE001
+                notes.append(f"[[{key}@{per}]] 账本里没有")
+        out.append(Verdict(v.kind, v.subject, v.level, v.why, "；".join(notes)))
+    return out
+
+
+def extra_facts(model, *, ledger, as_of, sections=None, corpus=None,
+                themes=(), coverage_decl=()) -> str:
+    """只给审核者看的、代码现算的事实（spec 二）。"""
+    from ir_agent import gaps, methodology
+    from ir_agent.formula import FormulaError, evaluate
+
+    lines: list[str] = []
+    # 1. [[key]] 声明在账本里实际取到的值
+    decl = [(f, k) for f in model.factors
+            for k in dict.fromkeys(_KEY_DECL.findall(f.source_hint or ""))]
+    if decl:
+        lines += ["【账本 key 声明的实际取值】"]
+        for f, k in decl:
+            have, _ = gaps.ledger_refs(f"[[{k}]]", ledger, as_of) if ledger else ([], [])
+            if have:
+                key, per = have[0].rsplit("@", 1)
+                lines.append(f"- {f.short} [[{k}]]：账本 {key}@{per} = "
+                             f"{ledger.get(key, per, as_of=as_of).value}")
+            else:
+                lines.append(f"- {f.short} [[{k}]]：账本里没有（或晚于研究时点才披露）")
+    # 2. 检索词命中次数
+    hits = methodology.term_hits(model, sections=sections, corpus=corpus)
+    lines += ["", "【因子候选词在本轮语料里的命中次数】"]
+    lines += [f"- {lab}：" + "　".join(f"{c}:{n}" for c, n in pairs)
+              for lab, pairs in hits.items()]
+    # 3. 覆盖核对
+    if themes:
+        cov, notes = methodology.coverage(
+            methodology.Methodology(model=model,
+                                    coverage_decl=list(coverage_decl)), list(themes))
+        lines += ["", "【辩论主题覆盖（代码核对提出者的声明；未交代的按字面匹配）】"]
+        lines += [f"- {c.theme}：{c.mark} {c.detail}" for c in cov]
+        lines += [f"- ⚠ {x}" for x in notes]
+    # 4. 水平模式第 0 年试算
+    if model.mode == "水平":
+        lines += ["", "【水平模式第 0 年试算（账本基期值代入公式，与报表并排）】"]
+        base, lacking = {}, []
+        for f in model.needs_base():
+            have, _ = gaps.ledger_refs(f.source_hint, ledger, as_of) if ledger else ([], [])
+            if have:
+                key, per = have[0].rsplit("@", 1)
+                base[f.short] = ledger.get(key, per, as_of=as_of).value
+            else:
+                lacking.append(f.short)
+        if lacking:
+            lines.append(f"- 缺 {'、'.join(lacking)} 的基期值（账本里没有），无法试算")
+        else:
+            consts = {c.name: c.value for c in model.constants}
+            for t, cn, key, scale in (("revenue", "营收", "revenue", Decimal(10) ** 8),
+                                      ("gross_margin", "毛利率", "gross_margin", 1)):
+                try:
+                    got = evaluate(model.formulas[t], base, None, base, consts)
+                except FormulaError as e:
+                    lines.append(f"- {cn}：试算失败（{e}）")
+                    continue
+                actual = _latest_annual(ledger, key, as_of)
+                act = (f"{actual / scale}" if actual is not None else "报表里没有")
+                lines.append(f"- {cn}：公式第 0 年 = {got}；报表 = {act}"
+                             + ("（亿元）" if t == "revenue" else ""))
+    return "\n".join(lines)
+
+
+def _latest_annual(ledger, key: str, as_of):
+    from ir_agent.operators.ratios import annual_periods
+
+    if ledger is None:
+        return None
+    pers = annual_periods(ledger, as_of, years=1)
+    try:
+        return ledger.get(key, pers[-1], as_of=as_of).value if pers else None
+    except Exception:                                     # noqa: BLE001
+        return None
+
+
+RULES = """你是研究总监，正在审核一份**驱动因子分解式**能不能用。你不改分解式，只
+逐项给结论与理由 —— 有一项「致命」，提出者就要重写。
+
+**四级结论**（只能用这四个词）：
+- **通过**：逻辑完全正确，资料能够对得上；
+- **提醒**：逻辑部分正确，资料对得上，但结论较为牵强；
+- **致命**：逻辑完全无法支撑结论；
+- **待采集**：逻辑正确，资料尚未采集到。
+
+提醒、致命、待采集**必须写理由**。凡涉及资料的判断，引原文「…」或账本占位符
+[[key@期间]] —— 代码会核对你的引文能否在语料、年报、账本里找到。
+
+**每类要判什么**（下面用「类别名：」说明，作答时才写成 [类别]）：
+- 模式：选「变化」还是「水平」，与这个行业的特性、手里拿得到的资料是否相符。
+- 因子：有无公开可查的口径；单位对不对；分母在公司内部还是外部（分母在内部的
+  占比变动不等于营收变动）；现有资料能否支撑；是否进了公式；检索词是否过短或
+  过泛（下面给了实际命中次数）。
+- 正交：这两个因子是不是同一个量被重复计算（销量×售价已是收入，再乘一个营收
+  增速就算了两遍）。
+- 公式：量纲是否自洽（pp 换成变化率要除以上期水平、毛利率按小数计等）；能否
+  算回营收与毛利率；`基期(·)`（相对基期累计）还是 `上期(·)`（逐年）与因子的填法
+  是否一致；因子个数是否合适（一个不叫分解，超过五个人核实不过来）。
+- 常量：数值与写的出处是否对得上。
+- 账本：账本里这个 key 与因子是不是同一个量、口径是否一致（下面给了实际取值）。
+- 覆盖：这个多空主题有没有因子承载；声明「未覆盖」的理由是否成立。
+
+**每条一行，格式严格如下**（结论与理由之间用全角空格）：
+
+[类别] <条目>：<通过|提醒|致命|待采集>　<理由>
+"""
+
+
+def review(judge, *, proposal: str, model, materials: str, extra: str,
+           themes=(), hay: str = "", ledger=None, as_of=None) -> ReviewResult:
+    """审核者作答一次（漏答补答最多 2 次，不消耗提出者轮数）。"""
+    expected = checklist(model, list(themes))
+    items = "\n".join(f"- [{k}] {s}" for k, s in expected)
+    base = (f"{RULES}\n**必须逐条作答的条目**\n{items}\n\n"
+            f"**提出者给的分解式（原文）**\n{proposal}\n\n"
+            f"**提出者看到的资料**\n{materials}\n\n"
+            f"**只给你的、代码算出的事实**\n{extra}\n")
+    prompt, last = base, ""
+    for _ in range(JUDGE_TRIES):
+        raw = judge(prompt)
+        try:
+            verdicts = parse(raw, expected)
+        except ReviewIncomplete as e:
+            last = str(e)
+            prompt = f"{base}\n---\n**上一次回答不完整**：{last}\n请补全后重新输出全部条目。\n"
+            continue
+        return ReviewResult(verdicts=annotate_quotes(verdicts, hay=hay,
+                                                     ledger=ledger, as_of=as_of))
+    raise ReviewIncomplete(last)
+
+
+def _feedback(n: int, res: ReviewResult) -> str:
+    lines = [f"**上一轮审核驳回（第 {n} 轮）**：以下条目判为致命，必须修改："]
+    lines += [f"- {v.line()}" for v in res.fatal()]
+    if res.flagged():
+        lines += ["以下为提醒/待采集，可改可不改："]
+        lines += [f"- {v.line()}" for v in res.flagged()]
+    lines.append("请修改后重新输出全部行。")
+    return "\n".join(lines)
+
+
+def propose_and_review(proposer, judge, *, industry: str, code: str,
+                       prompt: str, materials: str, themes=(),
+                       ledger=None, as_of=None, sections=None, corpus=None,
+                       max_rounds: int = MAX_ROUNDS):
+    """提出 → 代码查能不能算 → 审核 → 汇总。返回 (Methodology, ReviewResult, rounds)。
+
+    代码检查不过、审核驳回都算一轮，合计最多 `max_rounds` 轮；审核者答不完整、
+    或 3 轮仍有致命项 → `ReviewStop`（停下交人工）。模型接口错误原样抛出，由
+    调用方说明是接口问题。
+    """
+    from ir_agent import methodology
+    from ir_agent.strategy import annual_text
+
+    hay = "\n".join(x for x in [
+        sections if isinstance(sections, str)
+        else (annual_text(sections) if sections is not None else ""),
+        *[getattr(d, "text", "") or "" for d in (getattr(corpus, "docs", None) or [])]]
+        if x)
+    rounds: list[Round] = []
+    for n in range(1, max_rounds + 1):
+        raw = proposer(prompt)
+        try:
+            m = methodology.parse(raw)
+        except methodology.MethodologyFailed as e:
+            rounds.append(Round(n, raw, code_error=str(e)))
+            prompt = (f"{prompt}\n\n---\n**上一轮代码检查不过（第 {n} 轮）**："
+                      f"{e}\n请修正后重新输出全部行。\n")
+            continue
+        m.industry, m.rationale = industry, raw.strip()
+        extra = extra_facts(m.model, ledger=ledger, as_of=as_of, sections=sections,
+                            corpus=corpus, themes=themes,
+                            coverage_decl=m.coverage_decl)
+        try:
+            res = review(judge, proposal=methodology._to_dsl(m), model=m.model,
+                         materials=materials, extra=extra, themes=themes,
+                         hay=hay, ledger=ledger, as_of=as_of)
+        except ReviewIncomplete as e:
+            rounds.append(Round(n, raw, incomplete=str(e)))
+            raise ReviewStop(f"方法论审核未完成：审核者补答 {JUDGE_TRIES - 1} 次仍不全"
+                             f"（{str(e)[:120]}）", rounds) from e
+        res.rounds = n
+        res.fingerprint = fingerprint(methodology._to_dsl(m))
+        rounds.append(Round(n, raw, review=res))
+        if not res.fatal():
+            return m, res, rounds
+        prompt = f"{prompt}\n\n---\n{_feedback(n, res)}\n"
+    last = rounds[-1]
+    why = (f"最后一轮致命 {len(last.review.fatal())} 条" if last.review
+           else f"最后一轮代码检查不过：{last.code_error[:80]}")
+    raise ReviewStop(f"方法论审核 {max_rounds} 轮未通过（{why}）", rounds)
+
+
+def rounds_markdown(rounds: list[Round]) -> str:
+    """`drivers_rounds.md`：每一轮的完整记录。此前只存最后一稿，前两次为什么被
+    驳回无从查证。"""
+    lines = ["# 方法论预研：各轮记录", ""]
+    for r in rounds:
+        lines += [f"## 第 {r.n} 轮", "", "### 提出者原文", "", "```text",
+                  r.raw.strip(), "```", ""]
+        if r.code_error:
+            lines += ["### 代码检查：不过", "", r.code_error, ""]
+        if r.incomplete:
+            lines += ["### 审核：未完成", "", r.incomplete, ""]
+        if r.review:
+            c = r.review.counts()
+            lines += [f"### 审核：{'驳回' if r.review.fatal() else '通过'}"
+                      f"（致命 {c['致命']}、提醒 {c['提醒']}、待采集 {c['待采集']}、"
+                      f"通过 {c['通过']}）", ""]
+            lines += [f"- {v.line()}" for v in r.review.verdicts]
+            lines.append("")
+    return "\n".join(lines)
+
+
+def dump_review(res: ReviewResult) -> str:
+    return json.dumps(res.to_json(), ensure_ascii=False, indent=2)
diff --git a/tests/judge_stub.py b/tests/judge_stub.py
new file mode 100644
index 0000000..3bc23f0
--- /dev/null
+++ b/tests/judge_stub.py
@@ -0,0 +1,36 @@
+"""脚本化的假审核者（子项目二测试共用）。
+
+从提示词里读出代码生成的清单（`- [类别] 条目` 行），按给定规则逐条作答。
+不调模型。"""
+from __future__ import annotations
+
+import re
+
+_ITEM = re.compile(r"^- \[(模式|因子|正交|公式|常量|账本|覆盖)\] (.+)$", re.M)
+
+
+def items(prompt: str) -> list[tuple[str, str]]:
+    head = prompt.split("**必须逐条作答的条目**")[-1].split("**提出者给的分解式（原文）**")[0]
+    return _ITEM.findall(head)
+
+
+class Judge:
+    """`levels`：{(类别, 条目) 或 类别: 结论}；没列的答「通过」。可给多轮剧本
+    `script=[levels_1, levels_2, ...]`，第 n 次调用用第 n 份（用完沿用最后一份）。"""
+
+    def __init__(self, *script, skip=()):
+        self.script = list(script) or [{}]
+        self.skip = set(skip)          # 故意漏答的条目
+        self.prompts: list[str] = []
+
+    def __call__(self, prompt: str) -> str:
+        self.prompts.append(prompt)
+        levels = self.script[min(len(self.prompts), len(self.script)) - 1]
+        out = []
+        for k, s in items(prompt):
+            if (k, s) in self.skip:
+                continue
+            lv = levels.get((k, s), levels.get(k, "通过"))
+            why = "" if lv == "通过" else f"理由：{k}{s}"
+            out.append(f"[{k}] {s}：{lv}　{why}".rstrip())
+        return "\n".join(out)
diff --git a/tests/test_methodology_review.py b/tests/test_methodology_review.py
new file mode 100644
index 0000000..011d1c2
--- /dev/null
+++ b/tests/test_methodology_review.py
@@ -0,0 +1,224 @@
+"""方法论预研的审核者（spec 2026-10-14 子项目二）。不调模型。"""
+from __future__ import annotations
+
+from datetime import date
+from decimal import Decimal as D
+
+import pytest
+
+from ir_agent import methodology_review as R
+from ir_agent.ledger import Fact, FactLedger, Method
+from ir_agent.methodology import parse as parse_meth
+from judge_stub import Judge, items
+
+AS_OF = date(2026, 10, 14)
+
+CHANGE = """模式: 变化
+因子: 份额增速 | 本司市占率（占全行业规模，非公司内部占比）同比增速 | 变化率 | [[company.market_share_delta]] | 市场份额
+因子: 行业增速 | 行业全球市场规模同比增速 | 变化率 | Prismark | Prismark
+因子: 提价 | 提价对毛利率的累计贡献 | pp | 调价函 | 调价
+因子: 原料 | 原材料对毛利率的累计侵蚀 | pp | 年报风险因素 | 原材料价格
+常量: 上期稼动率 | 78% | 年报经营情况讨论「产能利用率约78%」
+公式: 营收 = 上期(营收) × (1 + 行业增速) × (1 + 份额增速)
+公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100
+覆盖: 份额 | 份额增速
+"""
+
+LEVEL = """模式: 水平
+因子: 量 | 出货量 | 万吨 | 年报 | 出货量
+因子: 价 | 单价 | 元 | [[ccl.asp]] | 单价
+因子: 本 | 单位成本 | 元 | 年报 | 单位成本
+公式: 营收 = 量 × 价
+公式: 毛利率 = (价 − 本) ÷ 价
+"""
+
+
+def _model(text):
+    return parse_meth(text).model
+
+
+def _ledger(**kv):
+    led = FactLedger()
+    for key, (per, v) in kv.items():
+        led.put(Fact(key=key.replace("__", "."), value=D(v), unit="x", period=per,
+                     as_of=date(2026, 4, 1), source_id="t", method=Method.REPORTED))
+    return led
+
+
+class TestChecklist:
+    def test_七类条目(self):
+        got = R.checklist(_model(CHANGE), ["需求", "份额"])
+        kinds = [k for k, _ in got]
+        assert kinds[0] == "模式" and kinds.count("因子") == 4
+        assert ("正交", "份额增速 × 行业增速") in got     # 按声明顺序
+        assert ("正交", "提价 × 原料") in got
+        assert ("公式", "营收") in got and ("公式", "毛利率") in got
+        assert ("常量", "上期稼动率") in got
+        assert ("账本", "份额增速 [[company.market_share_delta]]") in got
+        assert [s for k, s in got if k == "覆盖"] == ["需求", "份额"]
+
+    def test_共用因子对只列一次_无主题不列覆盖(self):
+        got = R.checklist(_model(LEVEL), [])
+        assert got.count(("正交", "价 × 本")) == 1
+        assert ("正交", "量 × 价") in got
+        assert not any(k == "覆盖" for k, _ in got)
+
+
+class TestParse:
+    EXP = [("模式", "模式"), ("因子", "份额增速（本司市占率（占全行业规模，非公司内部占比）同比增速）"),
+           ("公式", "营收")]
+
+    def test_按预期条目名匹配_条目名里有括号和冒号也行(self):
+        raw = ("- [模式] 模式：通过\n"
+               "**[因子] 份额增速（本司市占率（占全行业规模，非公司内部占比）同比增速）：提醒"
+               "　分母是全行业：口径需对齐**\n"
+               "[公式] 营收：待采集　缺行业规模数据\n")
+        got = R.parse(raw, self.EXP)
+        assert [v.level for v in got] == ["通过", "提醒", "待采集"]
+        assert got[1].why.startswith("分母是全行业：口径需对齐")
+
+    def test_漏答与没写理由都算不完整(self):
+        with pytest.raises(R.ReviewIncomplete, match=r"没有给出结论.*\[公式\] 营收"):
+            R.parse("[模式] 模式：通过\n[因子] 份额增速（本司市占率（占全行业规模，"
+                    "非公司内部占比）同比增速）：通过", self.EXP)
+        with pytest.raises(R.ReviewIncomplete, match="没写理由"):
+            R.parse("[模式] 模式：致命\n[因子] 份额增速（本司市占率（占全行业规模，"
+                    "非公司内部占比）同比增速）：通过\n[公式] 营收：通过", self.EXP)
+
+    def test_四级之外的结论不认(self):
+        with pytest.raises(R.ReviewIncomplete):
+            R.parse("[模式] 模式：还行　x\n[因子] 份额增速（本司市占率（占全行业规模，"
+                    "非公司内部占比）同比增速）：通过\n[公式] 营收：通过", self.EXP)
+
+
+class TestAnnotate:
+    def test_引文与占位符核验不过只标注(self):
+        led = _ledger(company__market_share=("2025FY", "0.08"))
+        vs = [R.Verdict("因子", "a", "提醒", "据「产能利用率约78%」与 [[company.market_share@2025FY]]"),
+              R.Verdict("因子", "b", "提醒", "据「凭空捏造的一句话」与 [[foo.bar@2025FY]]")]
+        got = R.annotate_quotes(vs, hay="……产能利用率约78%……", ledger=led, as_of=AS_OF)
+        assert got[0].quote_note == ""
+        assert "找不到" in got[1].quote_note and "foo.bar" in got[1].quote_note
+        assert got[1].level == "提醒", "只标注，不改结论"
+
+
+class TestExtraFacts:
+    def test_账本取值_命中次数_覆盖(self):
+        led = _ledger(company__market_share_delta=("2025FY", "0.0087"))
+        m = parse_meth(CHANGE)
+        txt = R.extra_facts(m.model, ledger=led, as_of=AS_OF,
+                            sections="Prismark 数据。", themes=["份额", "政策"],
+                            coverage_decl=m.coverage_decl)
+        assert "company.market_share_delta@2025FY = 0.0087" in txt
+        assert "Prismark:1" in txt
+        assert "份额：✓" in txt and "政策：？" in txt
+
+    def test_水平模式第0年试算与报表并排(self):
+        led = _ledger(ccl__asp=("2025FY", "10"), revenue=("2025FY", "1000000000"),
+                      gross_margin=("2025FY", "0.25"))
+        txt = R.extra_facts(_model(LEVEL), ledger=led, as_of=AS_OF)
+        assert "缺 量、本 的基期值" in txt
+
+    def test_水平模式基期齐全时给出试算(self):
+        lv = LEVEL.replace("| 年报 | 出货量", "| [[ccl.qty]] | 出货量") \
+                  .replace("| 年报 | 单位成本", "| [[ccl.cost]] | 单位成本")
+        led = _ledger(ccl__asp=("2025FY", "10"), ccl__qty=("2025FY", "100"),
+                      ccl__cost=("2025FY", "7.5"), revenue=("2025FY", "100000000000"),
+                      gross_margin=("2025FY", "0.25"))
+        txt = R.extra_facts(_model(lv), ledger=led, as_of=AS_OF)
+        assert "营收：公式第 0 年 = 1000；报表 = 1000（亿元）" in txt
+        assert "毛利率：公式第 0 年 = 0.25；报表 = 0.25" in txt
+
+
+def _run(proposer, judge, **kw):
+    return R.propose_and_review(proposer, judge, industry="电子", code="600183",
+                                prompt="预研提示词", materials="资料",
+                                themes=kw.pop("themes", ["份额"]),
+                                ledger=FactLedger(), as_of=AS_OF, **kw)
+
+
+class _Proposer:
+    def __init__(self, *outs):
+        self.outs, self.prompts = list(outs), []
+
+    def __call__(self, p):
+        self.prompts.append(p)
+        return self.outs[min(len(self.prompts), len(self.outs)) - 1]
+
+
+class TestRounds:
+    def test_第一轮通过(self):
+        m, res, rounds = _run(_Proposer(CHANGE), Judge())
+        assert res.rounds == 1 and not res.fatal() and len(rounds) == 1
+        assert m.model.mode == "变化" and res.fingerprint
+
+    def test_致命即驳回_第二轮通过_反馈带上致命项(self):
+        j = Judge({("正交", "份额增速 × 行业增速"): "致命"}, {})
+        p = _Proposer(CHANGE, CHANGE)
+        m, res, rounds = _run(p, j)
+        assert res.rounds == 2 and len(rounds) == 2 and rounds[0].review.fatal()
+        assert "致命" in p.prompts[1] and "份额增速 × 行业增速" in p.prompts[1]
+
+    def test_提醒与待采集不驳回(self):
+        _, res, _ = _run(_Proposer(CHANGE), Judge({"因子": "提醒", "常量": "待采集"}))
+        assert res.rounds == 1 and len(res.flagged()) == 5
+
+    def test_代码检查不过也算一轮(self):
+        p = _Proposer("胡言乱语", CHANGE)
+        _, res, rounds = _run(p, Judge())
+        assert res.rounds == 2 and rounds[0].code_error
+        assert "代码检查不过" in p.prompts[1]
+
+    def test_三轮仍有致命停下(self):
+        with pytest.raises(R.ReviewStop, match="3 轮未通过.*致命 1 条") as e:
+            _run(_Proposer(CHANGE), Judge({"模式": "致命"}))
+        assert len(e.value.rounds) == 3
+
+    def test_审核者漏答先补答_不消耗提出者轮数(self):
+        class Once(Judge):
+            def __call__(self, prompt):
+                self.skip = {("模式", "模式")} if not self.prompts else set()
+                return super().__call__(prompt)
+        j, p = Once(), _Proposer(CHANGE)
+        _, res, rounds = _run(p, j)
+        assert len(j.prompts) == 2 and len(p.prompts) == 1 and res.rounds == 1
+        assert "上一次回答不完整" in j.prompts[1]
+
+    def test_审核者始终答不完整就停下_不当通过(self):
+        with pytest.raises(R.ReviewStop, match="审核未完成"):
+            _run(_Proposer(CHANGE), Judge(skip={("模式", "模式")}))
+
+    def test_审核者看到的比提出者多(self):
+        j = Judge()
+        _run(_Proposer(CHANGE), j)
+        assert "只给你的、代码算出的事实" in j.prompts[0]
+        assert "命中次数" in j.prompts[0] and "资料" in j.prompts[0]
+
+    def test_各轮记录(self):
+        with pytest.raises(R.ReviewStop) as e:
+            _run(_Proposer("胡言乱语", CHANGE), Judge({"模式": "致命"}))
+        md = R.rounds_markdown(e.value.rounds)
+        assert md.count("## 第 ") == 3 and "代码检查：不过" in md
+        assert "审核：驳回" in md and "[模式] 模式：致命" in md
+
+
+class TestResult:
+    def test_json往返_markdown只列提醒待采集(self):
+        _, res, _ = _run(_Proposer(CHANGE), Judge({"常量": "待采集"}))
+        back = R.ReviewResult.from_json(res.to_json())
+        assert back.verdicts == res.verdicts and back.fingerprint == res.fingerprint
+        md = "\n".join(res.markdown())
+        assert "第 1 轮通过" in md and "[常量] 上期稼动率：待采集" in md
+        assert "[模式] 模式" not in md, "通过的只给条数"
+
+    def test_清单与提示词里的条目一致(self):
+        j = Judge()
+        _run(_Proposer(CHANGE), j)
+        assert items(j.prompts[0]) == R.checklist(_model(CHANGE), ["份额"])
+
+
+def test_规则正文不出现与清单同形的条目行():
+    """规则里若写 `- [模式] …`，审核者会把它当成要作答的条目（假审核者就当真
+    了）。说明一律用「类别名：」。"""
+    import re
+    assert not re.search(r"(?m)^\s*-\s*\[(模式|因子|正交|公式|常量|账本|覆盖)\]", R.RULES)
```

</details>

---

### Task 3: 接入第二次调用、落盘、停下、门②

**Files:** Modify `ir_agent/full.py`、`ir_agent/methodology.py`（`to_markdown(review=)`）、`ir_agent/assume.py`（`method_review` 字段与清单一节）、`ir_agent/research.py`、`tests/test_full_context.py`

**内容**：
- `_research_methodology(code, r, out, client, *, judge, md, stance_note)`：`judge` 必填；提出者 = `client`（联网），审核者 = `judge`（不联网，`run_brief` 传 `client`）；审核者看到的「与提出者相同的资料」就是提出者的提示词。
- 通过：写 `drivers_rounds.md`、`drivers_review.json`、drivers.md「方法论审核」一节。
- `ReviewStop` / 接口失败 → 写各轮记录与最后一稿，抛 `MethodologyStop`；`run_brief` 接住，`_stop_for_methodology` 停下（INCOMPLETE），终端给两条做法；`research._PREFIX` 登记「方法论审核」⚠；`next_actions` 在有 `drivers_rounds.md` 而无 drivers.md 时给人工做法。
- 读回：`_method_review` 比对指纹；不一致或无记录 → `_unreviewed_note` 在 drivers.md 顶部加 `UNREVIEWED_BANNER`、终端标注，不送审。
- 门② review.md「方法论审核」一节（`_method_review_lines`）；第三次调用把同一份行传给 `_propose_assumptions(method_review=)`。
- 测试改写：直接调 `_research_methodology` 的补 `judge=Judge()`；「解析失败退回两标量」→「三轮都过不了代码检查时停下」；「撞额度降级」→「停下并说明是接口问题」；读回旧 drivers.md 断言「人工修改后未经审核」；`_e2e` 只换掉两次模型调用（`propose_and_review` 真跑）；「预研失败时第三次调用不补做预研」改用「取不到行业分类」构造同一场景；新增三条端到端（三轮不过停下、第一轮驳回第二轮通过、人改过定义块才标注——只改说明文字不算）。

- [ ] **Step 1: 取测试，确认红**

```bash
git checkout 7537c67 -- tests/test_full_context.py
python -m pytest tests/test_full_context.py -q        # 15 failed, 141 passed
```

- [ ] **Step 2: 取实现，确认绿；全量**

```bash
git checkout 7537c67 -- ir_agent/full.py ir_agent/methodology.py ir_agent/assume.py ir_agent/research.py
python -m pytest tests/test_full_context.py -q        # 156 passed
python -m pytest -q -m "not live" -W error             # 2363 passed + 8
```

- [ ] **Step 3: 变异验证**（拷贝还原 `ir_agent/full.py`）
  - 读回时 `return m, _unreviewed_note(out, m)` → `return m, ""` → `test_人改过drivers_md后重跑…` 红；
  - `run_brief` 接住 `MethodologyStop` 后改为照样往下走（`meth, merr = None, str(e)`）→ `test_审核三轮不过就停在预研_不往下走` 红；
  - `_write_review` 里 `if method is not None:` → `if False:` → `test_第一轮驳回第二轮通过…`、`test_人改过…` 红。

- [ ] **Step 4: 提交**

```bash
git add ir_agent/full.py ir_agent/methodology.py ir_agent/assume.py ir_agent/research.py tests/test_full_context.py
git commit -m "full: 预研改走提出者 + 审核者；审核结论与各轮记录落盘；重跑按指纹沿用、人改过的不送审只标注；审核不过停下交人工（不退回两标量）；门② 待核清单新增方法论审核"
```

<details><summary>完整 diff（7537c67）</summary>

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index fa0f35e..c8fe1b0 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -354,6 +354,9 @@ class AssumptionSet:
     base_values: dict = field(default_factory=dict)
     # 第 0 年营收（亿元，与工作簿同口径）。旧的乘法链与尺度无关，缺省按 1。
     base_revenue: Decimal | None = None
+    # 方法论审核遗留（子项目二）：预研阶段审核者判为提醒/待采集的条目，或
+    # 「人工修改后未经审核」。派生量，不进 JSON —— 第三次调用从 drivers_review.json 读。
+    method_review: list = field(default_factory=list)
 
     def _m(self) -> FormulaModel | None:
         if self.model is None and self.decomps:
@@ -386,6 +389,9 @@ class AssumptionSet:
             # 实测: 那条路径过了单位检查、三表勾稽、140 格复算、每股价值
             # 量级对账与人工门，没有一道闸门把假设换算成它的后果。
             lines += [f"📐 **算术后果**：{self.implied.describe()}", ""]
+        if self.method_review:
+            lines += ["🧭 **方法论审核遗留**（预研阶段审核者判为提醒/待采集的条目）：", ""]
+            lines += list(self.method_review) + [""]
         if self.review is not None and getattr(self.review, "flagged", None):
             # 审核意见印在清单顶部而不是末尾 —— 批准人要在看三档数值之前
             # 先知道「有哪几条已经被质疑过」。实测那条重复计算的因子若有
diff --git a/ir_agent/full.py b/ir_agent/full.py
index 090ecbc..081e13d 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -1087,6 +1087,16 @@ FAILED_RAW = "assumptions_failed_raw.txt"
 # 撰写被驳回时最后一稿的落盘名。两个阶段各一个 —— 同一个目录，同名就会
 # 把另一阶段的证据冲掉（与 debate.md / debate_report.md 同一处理）。
 DRIVERS_FAILED_RAW = "drivers_failed_raw.md"
+# 方法论审核（子项目二）：最后一轮逐项结论与指纹；每一轮的完整记录。
+DRIVERS_REVIEW_JSON = "drivers_review.json"
+DRIVERS_ROUNDS_MD = "drivers_rounds.md"
+UNREVIEWED_BANNER = "> ⚠ 本分解式人工修改后未经审核（与审核时的分解式不一致，或没有审核记录）。"
+
+
+class MethodologyStop(Exception):
+    """方法论预研停下交人工（子项目二决定 2）：审核 3 轮未通过、审核者答不完整、
+    模型接口失败。**不再退回两标量模式** —— 那条路让因子采集、辩论② 的逐因子
+    取值整套悄悄消失。"""
 WRITE_FAILED_BRIEF = "brief_failed_raw.md"
 WRITE_FAILED_REPORT = "report_failed_raw.md"
 
@@ -1450,7 +1460,7 @@ def _facts_digest(r) -> str:
         return ""
 
 
-def _research_methodology(code: str, r, out: Path, client, *,
+def _research_methodology(code: str, r, out: Path, client, *, judge,
                           md: dict | None = None, stance_note: str = ""):
     """方法论预研 —— **就多空争执的这几点，这家公司该怎么分解**。
 
@@ -1483,28 +1493,50 @@ def _research_methodology(code: str, r, out: Path, client, *,
     from ir_agent import methodology
 
     path = out / DRIVERS_MD
+    from ir_agent import methodology_review as MR
+
     m, derr = _read_drivers(out)
-    if m is not None or derr:
+    if m is not None:
+        # 重跑读回：指纹一致沿用审核结论；不一致或没有审核记录 = 人改过，不送审，
+        # 只标注（子项目二决定 8）。
+        return m, _unreviewed_note(out, m)
+    if derr:
         return m, derr
 
     industry = _industry_path(code)
     if not industry:
         return None, "方法论预研（取不到申万行业分类）"
 
+    themes = list(STANCE_THEMES) if stance_note.strip() else []
+    prompt = methodology.build_prompt(
+        industry, code,
+        facts=_facts_digest(r),
+        corpus_note=(md or {}).get("外部资料", ""),
+        qualitative="\n\n".join(
+            f"【{k}】\n{v}" for k, v in (md or {}).items()
+            if k in ("宏观定性", "战略", "产业链") and v),
+        stance_note=stance_note,
+        available_keys=_ledger_keys(r))
     try:
-        m = methodology.research(
-            client, industry, code,
-            facts=_facts_digest(r),
-            corpus_note=(md or {}).get("外部资料", ""),
-            qualitative="\n\n".join(
-                f"【{k}】\n{v}" for k, v in (md or {}).items()
-                if k in ("宏观定性", "战略", "产业链") and v),
-            stance_note=stance_note,
-            available_keys=_ledger_keys(r))
-    except methodology.MethodologyFailed as e:
-        return _methodology_failed(out, e)
+        # 提出者联网（`client`）；审核者不联网（`judge`）。审核者看到的「与提出
+        # 者相同的资料」就是提出者的提示词本身。
+        m, review, rounds = MR.propose_and_review(
+            client, judge, industry=industry, code=code, prompt=prompt,
+            materials=prompt, themes=themes, ledger=getattr(r, "ledger", None),
+            as_of=getattr(r, "as_of", None), sections=getattr(r, "sections", None),
+            corpus=getattr(r, "corpus", None))
+    except MR.ReviewStop as e:
+        (out / DRIVERS_ROUNDS_MD).write_text(MR.rounds_markdown(e.rounds),
+                                             encoding="utf-8")
+        dumped = _dump_refused(out, e.rounds[-1].raw if e.rounds else "",
+                               DRIVERS_FAILED_RAW)
+        raise MethodologyStop(
+            f"{e} —— 各轮记录：{out / DRIVERS_ROUNDS_MD}"
+            + (f"；最后一稿：{dumped}" if dumped else "")) from e
     except ClaudeCliError as e:
-        return _methodology_api_failed(e)
+        raise MethodologyStop(_methodology_api_failed(e)[1]) from e
+    (out / DRIVERS_ROUNDS_MD).write_text(MR.rounds_markdown(rounds), encoding="utf-8")
+    (out / DRIVERS_REVIEW_JSON).write_text(MR.dump_review(review), encoding="utf-8")
 
     # ── 两段标注（都不阻断）─────────────────────────────────────
     # **没有辩论① 的结论就不做覆盖标注。** spec 对 `theme_coverage` 写的是
@@ -1527,7 +1559,7 @@ def _research_methodology(code: str, r, out: Path, client, *,
                 refs[f.label] = (have, miss)
     path.write_text(methodology.to_markdown(m, coverage=cov, hits=hits,
                                                 coverage_notes=cov_notes,
-                                                key_refs=refs),
+                                                key_refs=refs, review=review),
                     encoding="utf-8")
     note = ""
     if (open_ := [c.theme for c in cov if c.mark != "✓"]):
@@ -1536,6 +1568,68 @@ def _research_methodology(code: str, r, out: Path, client, *,
     return m, note
 
 
+def _method_review(out: Path, m):
+    """读回方法论审核结论。返回 `(ReviewResult 或 None, 是否未经审核)`。
+
+    指纹 = 当前 drivers.md 分解式定义（经解析再输出，空白差异不算改动）的哈希；
+    与 `drivers_review.json` 记录的一致 → 沿用；否则视为人改过。"""
+    from ir_agent import methodology
+    from ir_agent import methodology_review as MR
+
+    if m is None:
+        return None, False
+    f = out / DRIVERS_REVIEW_JSON
+    fp = MR.fingerprint(methodology._to_dsl(m))
+    if f.exists():
+        try:
+            res = MR.ReviewResult.from_json(json.loads(f.read_text(encoding="utf-8")))
+        except (OSError, ValueError, KeyError, TypeError):
+            res = None
+        if res is not None and res.fingerprint == fp:
+            return res, False
+    return None, True
+
+
+def _unreviewed_note(out: Path, m) -> str:
+    """人改过的 drivers.md：不送审，标注（drivers.md 顶部、终端清单）。"""
+    _, unreviewed = _method_review(out, m)
+    if not unreviewed:
+        return ""
+    path = out / DRIVERS_MD
+    text = path.read_text(encoding="utf-8")
+    if UNREVIEWED_BANNER not in text:
+        path.write_text(UNREVIEWED_BANNER + "\n\n" + text, encoding="utf-8")
+    return ("方法论预研：drivers.md 人工修改后未经审核（与审核时的分解式不一致，"
+            "或没有审核记录）—— 人的判断优先，未送审，已标注")
+
+
+def _method_review_lines(out: Path, m) -> list[str]:
+    """门② 待核清单与门③ 判断项清单共用：方法论审核遗留的提醒与待采集。"""
+    if m is None:
+        return []
+    res, unreviewed = _method_review(out, m)
+    if unreviewed:
+        return ["⚠ 分解式人工修改后未经审核"]
+    return [f"- {v.line()}" for v in res.flagged()] if res else []
+
+
+def _stop_for_methodology(code: str, r, as_of: date, out: Path, arts: dict,
+                          skipped: list[str], client, why: str) -> ResearchOutcome:
+    """方法论预研停下交人工。两条做法：重跑，或以最后一稿为底手写 drivers.md。"""
+    cmd = (f"python -m ir_agent {code} --year {r.period[:4]} "
+           f"--brief {out} --verified-by <名字>")
+    print(f"\n方法论预研停下：{why}\n\n"
+          f"  ① 原样重试（重新预研）\n     {cmd}\n\n"
+          f"  ② 人工给出分解式：以 {out}/{DRIVERS_FAILED_RAW} 为底稿改好，存为 "
+          f"{out}/{DRIVERS_MD}（各轮审核意见见 {out}/{DRIVERS_ROUNDS_MD}），再跑\n"
+          f"     {cmd}\n     读回时会标注「人工修改后未经审核」。\n", file=sys.stderr)
+    skipped.append(why)
+    return ResearchOutcome(
+        code=code, period=r.period, as_of=as_of, stage=Stage.INCOMPLETE,
+        artifacts=arts, skipped=skipped,
+        cost_usd=Decimal(str(client.total_cost_usd)), phase="brief")
+
+
 def _catalog_periods(r) -> tuple[str, ...]:
     """可引用清单要收进哪些期间（公司报告期之外）。**写作方与辩论裁判用同一份。**
 
@@ -2004,7 +2098,8 @@ def _review_factor_lines(factor_data) -> list[str]:
     return lines
 
 
-def _write_review(out: Path, sa, sb, missing=(), factor_data=None) -> None:
+def _write_review(out: Path, sa, sb, missing=(), factor_data=None,
+                  method=None) -> None:
     """门② 的待核清单：把所有「代码没把握」的东西聚到一处。"""
     from ir_agent.sources.manifest import KNOWN_GAPS
 
@@ -2014,6 +2109,9 @@ def _write_review(out: Path, sa, sb, missing=(), factor_data=None) -> None:
             lines += [f"## {name}：{len(s.unverified)} 条证据代码核验不过", ""]
             lines += [f"- [{e.side}] {e.subject}：{e.unverified}"
                       for e in s.unverified] + [""]
+    if method is not None:
+        lines += ["## 方法论审核（预研阶段审核者的提醒与待采集）", ""]
+        lines += (method or ["提醒、待采集 0 条。"]) + [""]
     lines += _review_factor_lines(factor_data)
     if missing:
         groups: dict[str, list] = {}
@@ -2138,8 +2236,15 @@ def run_brief(code: str, r, as_of: date, out_dir: str | Path,
     # ── 方法论预研：现在能看到全部证据与多空结论 ────────────────
     # `stance_note=sa.summary()` 是「辩论① 排在预研之前」唯一的兑现处 ——
     # 不传它，辩论① 的结论就到不了预研，流程图上它在那儿而数据流上它不在。
-    meth, merr = _research_methodology(code, r, out, research_client,
-                                       md=md, stance_note=sa.summary())
+    try:
+        meth, merr = _research_methodology(code, r, out, research_client,
+                                           judge=client, md=md,
+                                           stance_note=sa.summary())
+    except MethodologyStop as e:
+        o = _stop_for_methodology(code, r, as_of, out, arts, skipped, client,
+                                  str(e))
+        o.cost_usd = _cost()
+        return o
     if merr:
         skipped.append(merr)
     if meth is not None:
@@ -2190,7 +2295,9 @@ def run_brief(code: str, r, as_of: date, out_dir: str | Path,
         arts["研究简报"] = str(out / BRIEF)
         if (note := _audit_note("研究简报", r.audit)):
             skipped.append(note)
-        _write_review(out, sa, sb, _missing_snapshot_notes(r.audit), factor_data)
+        _write_review(out, sa, sb, _missing_snapshot_notes(r.audit), factor_data,
+                      method=(_method_review_lines(out, meth)
+                              if meth is not None else None))
         arts["待核清单"] = str(out / REVIEW_MD)
 
     _write_dashboard(r, out, md, code, arts)
@@ -2261,7 +2368,8 @@ def run_assume(code: str, r, as_of: date, out_dir: str | Path,
     aerr = _propose_assumptions(
         code, r, body, md, as_of, out, assume_client, arts,
         decomps=(meth.model if meth is not None else None),
-        factor_data=factor_data, balances=sb.balances)
+        factor_data=factor_data, balances=sb.balances,
+        method_review=_method_review_lines(out, meth))
     if aerr:
         skipped.append(aerr)
     _save_state(out, code, r, as_of, md, pr, reviewed_by=reviewed_by)
@@ -2320,7 +2428,8 @@ def _anchor_banner(cal: dict, labels: list[str], rebased: bool = False) -> str:
 
 
 def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
-                         decomps=None, factor_data=None, balances=None) -> str:
+                         decomps=None, factor_data=None, balances=None,
+                         method_review=None) -> str:
     """校准 → AI 填判断项 → 写出待核实清单。返回失败说明。
 
     `decomps`/`factor_data` 给定时按因子分解填写（方法论预研 + 定向
@@ -2366,6 +2475,9 @@ def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
         # 整轮研究不该因此停摆。**
         return f"DCF 判断项（{e.__class__.__name__}: {str(e)[:160]}）"
 
+    # 预研阶段审核者的提醒/待采集带进门③ 清单（子项目二决定 7）。
+    a.method_review = list(method_review or [])
+
     # ── 因子结构审查（第 3 道：清单式，不设闸门）────────────────
     # **必须在写盘之前跑，但 checklist 要取写盘之前的 describe()** ——
     # 审查结果自己会进 describe()，拿带结果的清单去喂审核者是循环引用。
diff --git a/ir_agent/methodology.py b/ir_agent/methodology.py
index 8b52773..a26c51e 100644
--- a/ir_agent/methodology.py
+++ b/ir_agent/methodology.py
@@ -673,7 +673,7 @@ def term_hits(decomps: list, *, sections=None, corpus=None
 
 
 def to_markdown(m: Methodology, coverage=(), hits=None,
-                coverage_notes=(), key_refs=None) -> str:
+                coverage_notes=(), key_refs=None, review=None) -> str:
     """分解式落盘为 drivers.md。
 
     `coverage` / `hits` 是**标注**，不参与 `from_markdown` 的往返解析 ——
@@ -721,6 +721,10 @@ def to_markdown(m: Methodology, coverage=(), hits=None,
         lines += ["", "声明了的因子直接取账本数据、不再联网采集。**口径是否与"
                       "因子名一致（例如因子写的是某机构口径、账本是同业聚合口径）"
                       "由人判断。**", ""]
+    if review is not None:
+        # 方法论审核（子项目二）：第几轮通过、提醒与待采集逐条。**不参与
+        # from_markdown 的往返解析** —— 与上面两段标注同一处理。
+        lines += review.markdown()
     if m.model is not None and not m.model.legacy:
         lines += _formula_markdown(m.model)
     for d in (m.decompositions if (m.model is None or m.model.legacy) else []):
diff --git a/ir_agent/research.py b/ir_agent/research.py
index a06c4f3..e4cc46c 100644
--- a/ir_agent/research.py
+++ b/ir_agent/research.py
@@ -165,6 +165,8 @@ _PREFIX = (
     # **失败**区分：失败条目是「方法论预研（…）」（全角括号），落默认分支的
     # 「未生成」是对的；这一条是「方法论预研：」（全角冒号）。
     ("方法论预研：", "  ⚠ "),
+    # 子项目二：审核 3 轮未通过 / 审核未完成 —— 停下交人工。
+    ("方法论审核", "  ⚠ "),
     # 依据核验通过、但来源分级是 MEDIA（`full._media_tier_note`）。这是一条
     # 算出来了、必须被看见的警告 —— 落默认分支会被印成「未生成」，读者会去
     # 找什么东西没产出。重构前就漏登记了，生益科技 2026-10-13 试跑时在门①
@@ -258,6 +260,13 @@ def next_actions(stage: Stage, code: str,
                 f"     python -m ir_agent {code} {y} --brief {out} "
                 f"--verified-by <你的名字>",
             ]
+            if ((Path(out) / "drivers_rounds.md").exists()
+                    and not (Path(out) / "drivers.md").exists()):
+                acts.append(
+                    f"方法论预研停下。若要人工给出分解式：以 {out}/drivers_failed_raw.md"
+                    f" 为底稿改好，存为 {out}/drivers.md（各轮审核意见见 "
+                    f"{out}/drivers_rounds.md），再跑上面那条命令 —— 读回时会标注"
+                    "「人工修改后未经审核」。")
             for rnd in ("a", "b"):
                 tmpl = Path(out) / f"stance_{rnd}.template.json"
                 if tmpl.exists():
diff --git a/tests/test_full_context.py b/tests/test_full_context.py
index 9180f1d..0fd1576 100644
--- a/tests/test_full_context.py
+++ b/tests/test_full_context.py
@@ -12,6 +12,7 @@ from ir_agent.debate import DebateRefused
 from ir_agent.full import _macro_periods, _prior_period
 from ir_agent.ledger import Fact, FactLedger, Method
 from ir_agent.research import assumption_template
+from judge_stub import Judge
 
 
 class _R:
@@ -297,16 +298,21 @@ def test_研究方法论产出落盘为drivers_md(tmp_path, monkeypatch):
         code = "600584"
 
     m, err = full._research_methodology("600584", _R(), tmp_path,
-                                        lambda prompt: lines)
+                                        lambda prompt: lines, judge=Judge())
     assert err == ""
     assert m is not None and len(m.decompositions) == 2
     assert (tmp_path / full.DRIVERS_MD).exists()
+    # 子项目二：审核结论与各轮记录都落盘；drivers.md 有「方法论审核」一节。
+    assert (tmp_path / full.DRIVERS_REVIEW_JSON).exists()
+    assert (tmp_path / full.DRIVERS_ROUNDS_MD).exists()
+    assert "## 方法论审核" in (tmp_path / full.DRIVERS_MD).read_text(encoding="utf-8")
     assert "封测" in (tmp_path / full.DRIVERS_MD).read_text(encoding="utf-8")
 
 
-def test_研究方法论解析失败时退回不分解(tmp_path, monkeypatch):
-    """联网拿不到闭合分解式是常态（比如模型答非所问），不该让整轮
-    研究停摆 —— 退回不分解模式并如实说明原因。"""
+def test_研究方法论三轮都过不了代码检查时停下交人工(tmp_path, monkeypatch):
+    """**子项目二决定 2：不再退回两标量模式**（那条路让因子采集、辩论② 的逐因子
+    取值整套悄悄消失）。代码检查不过也算一轮，3 轮后停下：各轮记录与最后一稿
+    落盘，drivers.md 不生成。"""
     import ir_agent.sources.taxonomy as tax
     from ir_agent import full
     from ir_agent.sources.taxonomy import Entry
@@ -317,11 +323,15 @@ def test_研究方法论解析失败时退回不分解(tmp_path, monkeypatch):
     class _R:
         code = "600584"
 
-    m, err = full._research_methodology(
-        "600584", _R(), tmp_path, lambda prompt: "胡言乱语，没有任何分解行")
-    assert m is None
-    assert "退回不分解" in err
+    import pytest
+    with pytest.raises(full.MethodologyStop, match="3 轮未通过"):
+        full._research_methodology(
+            "600584", _R(), tmp_path, lambda prompt: "胡言乱语，没有任何分解行",
+            judge=Judge())
     assert not (tmp_path / full.DRIVERS_MD).exists()
+    assert (tmp_path / full.DRIVERS_ROUNDS_MD).read_text(
+        encoding="utf-8").count("## 第 ") == 3
+    assert (tmp_path / full.DRIVERS_FAILED_RAW).exists()
 
 
 def test_drivers_md已存在时读回而不重新联网(tmp_path, monkeypatch):
@@ -350,8 +360,10 @@ def test_drivers_md已存在时读回而不重新联网(tmp_path, monkeypatch):
     class _R:
         code = "600584"
 
-    m, err = full._research_methodology("600584", _R(), tmp_path, _boom_client)
-    assert err == ""
+    m, err = full._research_methodology("600584", _R(), tmp_path, _boom_client,
+                                        judge=_boom_client)
+    # 没有审核记录的 drivers.md（旧文件或人手写）：不送审，标注未经审核。
+    assert "人工修改后未经审核" in err
     assert m is not None and len(m.decompositions) == 2
     assert m.industry == "电子-半导体-封测"
 
@@ -381,8 +393,8 @@ def test_drivers_md人工改过的内容读回后生效(tmp_path, monkeypatch):
 
     m, err = full._research_methodology(
         "600584", _R(), tmp_path, lambda p: (_ for _ in ()).throw(
-            AssertionError("不该再联网预研")))
-    assert err == ""
+            AssertionError("不该再联网预研")), judge=Judge())
+    assert "人工修改后未经审核" in err
     assert m.decompositions[0].factors[0].source_hint == "人工改过：管理层交流纪要"
 
 
@@ -405,7 +417,8 @@ def test_drivers_md读不回时明确报错且不重新联网(tmp_path, monkeypa
     class _R:
         code = "600584"
 
-    m, err = full._research_methodology("600584", _R(), tmp_path, _boom_client)
+    m, err = full._research_methodology("600584", _R(), tmp_path, _boom_client,
+                                        judge=_boom_client)
     assert m is None
     assert "drivers.md" in err
     assert "未重新联网" in err or "不重新" in err or "不会再联网" in err
@@ -425,7 +438,7 @@ def test_研究方法论取不到行业分类时降级(tmp_path, monkeypatch):
         code = "600584"
 
     m, err = full._research_methodology(
-        "600584", _R(), tmp_path, lambda prompt: "不会被调用")
+        "600584", _R(), tmp_path, lambda prompt: "不会被调用", judge=Judge())
     assert m is None
     assert "方法论预研" in err
     assert not (tmp_path / full.DRIVERS_MD).exists()
@@ -823,7 +836,7 @@ def test_未替换的占位符要进终端产物清单():
     assert full._audit_note("完整研报", good) == ""
 
 
-def test_预研撞上额度上限时降级而不是把整轮炸掉(tmp_path, monkeypatch):
+def test_预研撞上额度上限时停下并说明是接口问题(tmp_path, monkeypatch):
     """**今天 17:53 的实跑现场复现。** `python -m ir_agent 600584 --year
     2025 --analyse out/600584` 跑了 25 秒，确定性层全部完成——eastmoney
     2930 条事实、sina 95 条、三表勾稽通过、跨源勾稽一致、可溯源率
@@ -861,9 +874,11 @@ def test_预研撞上额度上限时降级而不是把整轮炸掉(tmp_path, mon
     class _R:
         code = "600584"
 
-    m, err = full._research_methodology("600584", _R(), tmp_path, _limit)
-
-    assert m is None
+    # 子项目二决定 2：接口失败也停下（不退回两标量），但措辞要说清是接口问题。
+    import pytest
+    with pytest.raises(full.MethodologyStop) as e:
+        full._research_methodology("600584", _R(), tmp_path, _limit, judge=Judge())
+    err = str(e.value)
     assert "session limit" in err, "原始错误要如实带出来，人才知道该等额度还是改代码"
     assert "接口" in err, "要说清这是接口失败，不是这个行业分解不了"
     assert "重跑" in err, "要告诉人接口恢复后重跑就能拿回因子分解"
@@ -2851,6 +2866,8 @@ def test_本分支新增的清单生产者逐个断言渲染标记(monkeypatch,
                             "成未生成是对的"),
         "_load_openweb": "本分支未改动其清单措辞；共用 _openweb_notes",
         "_restore_openweb": "本分支未改动其清单措辞；共用 _openweb_notes",
+        "_method_review_lines": ("不是终端清单的生产者：返回 review.md 与 "
+                                 "assumptions.md 的「方法论审核」行，不进 skipped"),
         "_review_factor_lines": ("不是终端清单的生产者：返回 review.md 的 markdown "
                                  "行，不进 skipped；渲染断言在 test_待核清单_快照"
                                  "一节逐条_因子结论一节警示在前"),
@@ -3231,7 +3248,7 @@ def test_预研没收到辩论结论时不做覆盖标注(tmp_path):
     orig, full._industry_path = full._industry_path, _fake_path
     try:
         m, err = full._research_methodology("600183", _R(), tmp_path,
-                                            lambda p: lines)
+                                            lambda p: lines, judge=Judge())
         assert m is not None and err == "", err
         md = (tmp_path / "drivers.md").read_text(encoding="utf-8")
         assert "主题覆盖" not in md, "没有辩论结论却印了覆盖标注"
@@ -3240,7 +3257,7 @@ def test_预研没收到辩论结论时不做覆盖标注(tmp_path):
         # 传了辩论结论就该核对
         (tmp_path / "drivers.md").unlink()
         m2, err2 = full._research_methodology(
-            "600183", _R(), tmp_path, lambda p: lines,
+            "600183", _R(), tmp_path, lambda p: lines, judge=Judge(),
             stance_note="需求：多方看 AI 放量；份额：空方看份额在丢")
         assert "辩论主题没有声明由哪个因子承载" in err2, err2
         md2 = (tmp_path / "drivers.md").read_text(encoding="utf-8")
@@ -3280,7 +3297,7 @@ def test_辩论结论与定性结论真的进了预研的提示词(tmp_path):
     orig, full._industry_path = full._industry_path, lambda c: "电子/元件/覆铜板"
     try:
         full._research_methodology(
-            "600183", _R(), tmp_path, _client,
+            "600183", _R(), tmp_path, _client, judge=Judge(),
             md={"外部资料": "共 12 篇（卖方 7 / 官方 3 / 媒体 2）",
                 "战略": "护城河在高频高速板的客户认证周期",
                 "产业链": "上游铜箔集中度高，议价能力弱",
@@ -3442,11 +3459,10 @@ def _stub_brief(monkeypatch, full, seq):
         got[f"辩论{round_}.client"] = client
         return _Bal(note=f"辩论{round_}的平衡结论")
 
-    class _M:
-        decompositions, industry, model = [], "测试业", None
+    from ir_agent.methodology import Methodology as _Meth
 
-        def factors(self):
-            return []
+    def _M():
+        return _Meth(industry="测试业")
 
     def meth(code, r, out, client, **k):
         seq.append("预研")
@@ -3806,6 +3822,7 @@ def _e2e(monkeypatch, full, tmp_path):
     """
     import ir_agent.gaps as G
     import ir_agent.methodology as Me
+    import ir_agent.methodology_review as MR
     import ir_agent.stance as St
 
     calls = {"run_stance": 0, "research": 0}
@@ -3822,12 +3839,15 @@ def _e2e(monkeypatch, full, tmp_path):
                 f"空方｜{subjects[0]}｜疲软｜「{subjects[0]}」"),
             balances=St.parse_balance(line))
 
-    def research(client, industry, code, *a, **k):
+    real_review = MR.propose_and_review
+
+    def research(proposer, judge, **k):
+        """子项目二：预研 = 提出者 + 审核者。编排（`propose_and_review`）真跑，
+        只把两次模型调用换掉 —— 提出者给固定分解式，审核者全判通过。"""
         calls["research"] += 1
-        calls["research.stance_note"] = k.get("stance_note", "")
-        m = Me.parse(_E2E_DSL)
-        m.industry = industry
-        return m
+        calls["research.stance_note"] = k.get("prompt", "")
+        calls["judge"] = Judge(*calls.get("judge_script", ({},)))
+        return real_review(lambda p: _E2E_DSL, calls["judge"], **k)
 
     def propose(code, r, body, md, as_of, out, client, arts, **k):
         calls["balances"] = k.get("balances")
@@ -3838,7 +3858,7 @@ def _e2e(monkeypatch, full, tmp_path):
         return ""
 
     monkeypatch.setattr(St, "run_stance", run_stance)
-    monkeypatch.setattr(Me, "research", research)
+    monkeypatch.setattr(MR, "propose_and_review", research)
     monkeypatch.setattr(G, "collect", lambda *a, **k: {})
     monkeypatch.setattr(full, "_build_evidence", lambda r, code: None)
     monkeypatch.setattr(full, "write_body",
@@ -3892,6 +3912,7 @@ def test_四次调用的产物集合与停止点(tmp_path, monkeypatch):
     # 相同。变异验证时把 run_assume 换成 `_research_methodology`，本测试照样
     # 绿。能区分的场景见 `test_预研失败时第三次调用不补做预研`。
     assert calls["research"] == 1
+    assert calls["judge"].prompts, "预研必须经过审核者"
     head = (tmp_path / "assumptions.md").read_text(encoding="utf-8").split("\n")[0]
     assert "资料来源经 甲 核验" in head and "简报结论经 乙 审阅" in head
     st = _j.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
@@ -3949,21 +3970,18 @@ def test_预研失败时第三次调用不补做预研(tmp_path, monkeypatch):
     from ir_agent import full
 
     calls = _e2e(monkeypatch, full, tmp_path)
-
-    def fail(*a, **k):
-        calls["research"] += 1
-        raise Me.MethodologyFailed("测试：给不出闭合的分解式")
-
-    monkeypatch.setattr(Me, "research", fail)
+    # 子项目二之后审核不过会停下（不退回两标量）；仍退回两标量的只剩「取不到
+    # 申万行业分类、根本无从预研」这一条 —— 用它构造同一个场景。
+    monkeypatch.setattr(full, "_industry_path", lambda code: "")
     d = date(2026, 10, 12)
     full.run_analyse("600183", _PhaseR(), d, tmp_path)
     o2 = full.run_brief("600183", _PhaseR(), d, tmp_path, "甲")
     assert not (tmp_path / "drivers.md").exists()
-    assert calls["research"] == 1
+    assert calls["research"] == 0
     assert o2.stage is full.Stage.AWAITING_BRIEF_REVIEW, "预研失败不该中断简报"
 
     o3 = full.run_assume("600183", _PhaseR(), d, tmp_path, "乙")
-    assert calls["research"] == 1, "第三次调用在 drivers.md 缺失时补做了预研"
+    assert calls["research"] == 0, "第三次调用在 drivers.md 缺失时补做了预研"
     assert o3.stage is full.Stage.AWAITING_ASSUME_REVIEW
     # 辩论② 退回了两标量，判断项拿到的平衡点主题也是两标量的
     assert calls["balances"][0].subject == "营收增速"
@@ -4274,3 +4292,68 @@ def test_公式化分解式从第二次调用一路传到判断项(tmp_path, mon
     assert isinstance(m, FormulaModel) and m.mode == "水平" and not m.legacy, \
         "第三次调用交给判断项的不是从 drivers.md 读回的公式模型"
     assert set(got["factor_data"]) == {f.key for f in m.factors}
+
+
+# ══ 子项目二：方法论预研 = 提出者 + 审核者（端到端）════════════════
+
+def test_审核三轮不过就停在预研_不往下走(tmp_path, monkeypatch):
+    from ir_agent import full
+    from ir_agent.research import next_actions
+
+    calls = _e2e(monkeypatch, full, tmp_path)
+    calls["judge_script"] = ({"模式": "致命"},)
+    d = date(2026, 10, 12)
+    full.run_analyse("600183", _PhaseR(), d, tmp_path)
+    o = full.run_brief("600183", _PhaseR(), d, tmp_path, "甲")
+    assert o.stage is full.Stage.INCOMPLETE
+    assert not (tmp_path / "drivers.md").exists()
+    assert not (tmp_path / "stance_b.json").exists(), "停下后不该跑辩论②"
+    assert not (tmp_path / full.BRIEF).exists(), "停下后不该写简报"
+    assert (tmp_path / full.DRIVERS_ROUNDS_MD).exists()
+    assert any(x.startswith("方法论审核 3 轮未通过") for x in o.skipped)
+    assert "  ⚠ 方法论审核" in o.summary()
+    acts = "\n".join(next_actions(o.stage, "600183", out_dir=str(tmp_path),
+                                  year=2025, phase=o.phase))
+    assert "drivers_failed_raw.md" in acts and "人工修改后未经审核" in acts
+
+
+def test_第一轮驳回第二轮通过_一路到简报(tmp_path, monkeypatch):
+    from ir_agent import full
+
+    calls = _e2e(monkeypatch, full, tmp_path)
+    calls["judge_script"] = ({"模式": "致命"}, {"因子": "待采集"})
+    d = date(2026, 10, 12)
+    full.run_analyse("600183", _PhaseR(), d, tmp_path)
+    o = full.run_brief("600183", _PhaseR(), d, tmp_path, "甲")
+    assert o.stage is full.Stage.AWAITING_BRIEF_REVIEW
+    assert len(calls["judge"].prompts) == 2
+    drivers = (tmp_path / "drivers.md").read_text(encoding="utf-8")
+    assert "第 2 轮通过" in drivers
+    review = (tmp_path / full.REVIEW_MD).read_text(encoding="utf-8")
+    assert "## 方法论审核" in review and "待采集" in review
+
+
+def test_人改过drivers_md后重跑_不送审_门二标注未经审核(tmp_path, monkeypatch):
+    from ir_agent import full
+
+    calls = _e2e(monkeypatch, full, tmp_path)
+    d = date(2026, 10, 12)
+    full.run_analyse("600183", _PhaseR(), d, tmp_path)
+    full.run_brief("600183", _PhaseR(), d, tmp_path, "甲")
+    assert calls["research"] == 1
+    path = tmp_path / "drivers.md"
+    # 只改说明文字（定义块之外）不算改动分解式：指纹不变，沿用审核结论。
+    path.write_text(path.read_text(encoding="utf-8").replace(
+        "本次研究采用的分解式。", "本次研究采用的分解式（人看过）。", 1),
+        encoding="utf-8")
+    o0 = full.run_brief("600183", _PhaseR(), d, tmp_path, "甲")
+    assert not any("未经审核" in x for x in o0.skipped)
+    # 改定义块（加一条陷阱）才算改动。
+    path.write_text(path.read_text(encoding="utf-8").replace(
+        "\n陷阱: 出货量", "\n陷阱: 人工补充的一条\n陷阱: 出货量", 1), encoding="utf-8")
+    o = full.run_brief("600183", _PhaseR(), d, tmp_path, "甲")
+    assert calls["research"] == 1, "人改过的 drivers.md 不该重新预研或送审"
+    assert path.read_text(encoding="utf-8").startswith(full.UNREVIEWED_BANNER)
+    assert any("人工修改后未经审核" in x for x in o.skipped)
+    assert "人工修改后未经审核" in (tmp_path / full.REVIEW_MD).read_text(encoding="utf-8")
+
```

</details>

---

### Task 4: 门③ 只对账，审核遗留进判断项清单

**Files:** Modify `ir_agent/factor_review.py`、`tests/test_factor_review.py`、`tests/test_full_context.py`、`tests/test_assume_formula.py`

**内容**：`factor_review.expected_items` 有分解式时只出 `[对账]`（每个目标一条）；规则正文只讲对账，说明因子可测性与两两重叠已在预研阶段审过。判断项清单顶部「🧭 方法论审核遗留」（字段与渲染在 Task 3 已加），本任务补测试：清单顶部显示、第三次调用传入且不再调用审核者。

- [ ] **Step 1: 取测试，确认红**

```bash
git checkout ecccc97 -- tests/test_factor_review.py tests/test_full_context.py tests/test_assume_formula.py
python -m pytest tests/test_factor_review.py tests/test_full_context.py tests/test_assume_formula.py -q   # 8 failed, 183 passed
```

- [ ] **Step 2: 取实现，确认绿；全量**

```bash
git checkout ecccc97 -- ir_agent/factor_review.py
python -m pytest tests/test_factor_review.py tests/test_full_context.py tests/test_assume_formula.py -q   # 191 passed
python -m pytest -q -m "not live" -W error             # 2364 passed + 8（主仓库合计 2372）
```

- [ ] **Step 3: 变异验证**（拷贝还原）
  - `run_assume` 里 `method_review=_method_review_lines(out, meth)` → `method_review=None` → `test_第一轮驳回第二轮通过…` 红；
  - `AssumptionSet.describe` 里 `if self.method_review:` → `if False:` → `test_判断项清单顶部有方法论审核遗留` 红；
  - `expected_items` 里加回一条 `("因子", "x")` → `tests/test_factor_review.py` 8 条红。

- [ ] **Step 4: 提交**

```bash
git add ir_agent/factor_review.py tests/test_factor_review.py tests/test_full_context.py tests/test_assume_formula.py
git commit -m "factor_review/assume: 门③ 因子结构审查只保留对账；预研审核的提醒与待采集带进判断项清单「方法论审核遗留」"
```

<details><summary>完整 diff（ecccc97）</summary>

```diff
diff --git a/ir_agent/factor_review.py b/ir_agent/factor_review.py
index 8f58bae..2f49e57 100644
--- a/ir_agent/factor_review.py
+++ b/ir_agent/factor_review.py
@@ -91,23 +91,17 @@ def expected_items(decomps: list,
         # 而那正是最该看一眼的一轮。此时没有因子、谈不上两两正交，但「取值
         # 与它自己的理由是否自洽」照样要对账。
         return [("对账", d) for d in drivers]
+    # **子项目二（2026-10-14）起只出 [对账]。** [因子]（可测性）与 [正交]（两两
+    # 重叠）已在方法论预研阶段由审核者逐项审过、有权驳回重写；这里再审一遍只会
+    # 给人两份可能互相矛盾的结论。留下的是填了数值以后才看得出的问题：合成值与
+    # 理由栏是否自洽。
     from ir_agent.formula import FormulaModel
 
     if isinstance(decomps, FormulaModel):
-        groups = [(t, [f.label for f in decomps.factors_of(t)])
-                  for t in ("revenue", "gross_margin") if t in decomps.formulas]
+        targets = [t for t in ("revenue", "gross_margin") if t in decomps.formulas]
     else:
-        groups = [(d.target, [f.label for f in d.factors]) for d in decomps or []]
-    # 水平模式下同一因子被两条公式共用：「因子」「正交」条目去重，否则同一条
-    # 要答两遍。
-    seen: set[tuple[str, str]] = set()
-    for target, labels in groups:
-        for it in ([("因子", l) for l in labels]
-                   + [("正交", f"{a} × {b}") for a, b in combinations(labels, 2)]
-                   + [("对账", target)]):
-            if it not in seen:
-                seen.add(it)
-                out.append(it)
+        targets = [d.target for d in decomps or []]
+    out += [("对账", t) for t in dict.fromkeys(targets)]
     return out
 
 
@@ -136,24 +130,16 @@ _RULES = """你是研究总监，正在审查一份**驱动因子分解式**本
 
 **逐项回答，每条一行，格式严格如下**（结论与理由之间用全角空格分隔）：
 
-[因子] <因子标签>：可测/不可测　<理由>
-[正交] <因子A> × <因子B>：独立/重叠　<理由>
 [对账] <分解目标>：一致/矛盾　<理由>
 
-三类各自要判什么：
+（因子可测性与两两重叠已在方法论预研阶段审过，这里只对账。）
 
-- **[因子] 可测性**：这个因子有没有公开可查的口径？若它只能靠估计，说明
-  它事实上是一个判断而不是一次测量。
-- **[正交] 两两重叠**：这两个因子衡量的是不是同一个量的不同说法？
-  乘法链里这一点要命 —— 销量 × 售价已经等于收入，若再乘一个「某产品线
-  收入增速」，同一个量被算了两遍，两个 20% 会变成 44%，五年复合下来是
-  数倍的差距。判「重叠」时请指出重复计算的是哪一个量。
 - **[对账] 合成值 vs 理由栏**：把各因子的中性档相乘（或相加）得到的合成值，
   与因子理由栏自己引用的参照（行业增速、历史增速、同业水平）是否自洽？
   实测踩到过: 理由栏写「取接近行业三年 CAGR 12.8% 之上」，而合成结果是
   71.75% —— 是它自己引用的参照的 5.6 倍。
 
-**不要吝惜「重叠」「矛盾」这两个结论。** 这一步的价值在于把问题摆上台面，
+**不要吝惜「矛盾」这个结论。** 这一步的价值在于把问题摆上台面，
 而不是在于让清单看起来干净；你给出的每一条都会有人读。
 """
 
diff --git a/tests/test_assume_formula.py b/tests/test_assume_formula.py
index 10ef0ba..91aedda 100644
--- a/tests/test_assume_formula.py
+++ b/tests/test_assume_formula.py
@@ -230,3 +230,15 @@ class TestPhase2:
         # （`path()` 是两标量路径专用的措辞）。
         with pytest.raises(AssumeFailed, match=r"path\(\)"):
             _check(back, 1, back.decomps or None)
+
+
+def test_判断项清单顶部有方法论审核遗留():
+    """子项目二决定 7：预研阶段审核者的提醒/待采集，人在批准判断项时要看得到。"""
+    a = assume.parse(_level_lines(), 1, decomps=LEVEL)
+    _check(a, 1, LEVEL)
+    a.labels = ["2026E"]
+    a.method_review = ["- [因子] 本（覆铜板单位成本）：待采集　年报未披露单位成本"]
+    txt = a.describe()
+    assert "方法论审核遗留" in txt and "年报未披露单位成本" in txt
+    assert txt.index("方法论审核遗留") < txt.index("## 分解式")
+
diff --git a/tests/test_factor_review.py b/tests/test_factor_review.py
index 51ee218..468e0b3 100644
--- a/tests/test_factor_review.py
+++ b/tests/test_factor_review.py
@@ -32,13 +32,9 @@ def _decomp():
                  Factor(key="seg", label="分部收入增速", unit="变化率")])
 
 
+# 子项目二（2026-10-14）起门③ 只对账：[因子]、[正交] 已在方法论预研阶段由
+# 审核者审过（`methodology_review`）。
 FULL = """
-[因子] 销量增速：可测　年报产销量章节有口径。
-[因子] 售价增速：可测　分产品销售数据可算。
-[因子] 分部收入增速：可测　分部报告有。
-[正交] 销量增速 × 售价增速：独立　一个是量一个是价。
-[正交] 销量增速 × 分部收入增速：重叠　分部收入里已含该产品的销量。
-[正交] 售价增速 × 分部收入增速：重叠　分部收入里已含该产品的价。
 [对账] revenue：矛盾　理由栏引用行业 CAGR 12.8%，合成值 71.75% 是其 5.6 倍。
 """
 
@@ -47,13 +43,11 @@ class TestExpectedItems:
     def test_条目清单由代码生成(self):
         """**清单由代码出，不由模型出** —— 否则模型可以靠少列条目回避问题。"""
         items = expected_items([_decomp()])
-        assert ("因子", "销量增速") in items
-        assert ("正交", "销量增速 × 分部收入增速") in items
-        assert ("对账", "revenue") in items
+        assert items == [("对账", "revenue")]
 
-    def test_三个因子要三对正交(self):
+    def test_不再出因子与正交条目_预研阶段已审(self):
         items = expected_items([_decomp()])
-        assert len([1 for k, _ in items if k == "正交"]) == 3
+        assert not any(k in ("因子", "正交") for k, _ in items)
 
     def test_没有分解式就不审(self):
         assert expected_items([]) == []
@@ -62,18 +56,17 @@ class TestExpectedItems:
 class TestParse:
     def test_完整回答解析出全部条目(self):
         got = parse(FULL, expected_items([_decomp()]))
-        assert len(got) == 7
+        assert len(got) == 1
 
     def test_缺一条就退回(self):
         """沉默不等于通过。"""
-        partial = "\n".join(FULL.strip().splitlines()[:4])
-        with pytest.raises(ReviewIncomplete, match="正交"):
-            parse(partial, expected_items([_decomp()]))
+        with pytest.raises(ReviewIncomplete, match="对账"):
+            parse("（什么都没答）", expected_items([_decomp()]))
 
     def test_需要人看的条目被挑出来(self):
         r = ReviewResult(findings=parse(FULL, expected_items([_decomp()])))
-        assert len(r.flagged) == 3                 # 两条重叠 + 一条矛盾
-        assert "重叠" in r.summary() and "矛盾" in r.summary()
+        assert len(r.flagged) == 1
+        assert "矛盾" in r.summary()
 
     def test_认不出来的结论词算需要人看(self):
         """兜底值选最弱的那个断言 —— 与 research._mark 那次事故同一条教训:
@@ -97,16 +90,14 @@ class TestReview:
         return c
 
     def test_回答不完整会被退回重写(self):
-        partial = "\n".join(FULL.strip().splitlines()[:3])
-        c = self._client(partial, FULL)
+        c = self._client("（什么都没答）", FULL)
         r = review(c, [_decomp()], checklist="（清单）")
-        assert len(r.findings) == 7 and len(c.seen) == 2
+        assert len(r.findings) == 1 and len(c.seen) == 2
         assert "不完整" in c.seen[1]
 
     def test_反复不完整则抛错(self):
-        partial = "\n".join(FULL.strip().splitlines()[:3])
         with pytest.raises(ReviewIncomplete):
-            review(self._client(partial), [_decomp()], checklist="（清单）",
+            review(self._client("（什么都没答）"), [_decomp()], checklist="（清单）",
                    max_attempts=2)
 
     def test_算术后果要进提示词(self):
@@ -153,7 +144,7 @@ class TestTwoScalarMode:
         assert len(r.flagged) == 1
 
 
-def test_公式化分解式的条目按公式分组且共用因子不重复():
+def test_公式化分解式只出两条对账():
     from ir_agent.factor_review import expected_items
     from ir_agent.methodology import parse as parse_meth
 
@@ -164,7 +155,4 @@ def test_公式化分解式的条目按公式分组且共用因子不重复():
 公式: 营收 = 量 × 价
 公式: 毛利率 = (价 − 本) ÷ 价
 """).model
-    items = expected_items(m)
-    assert items.count(("因子", "单价")) == 1
-    assert ("正交", "出货量 × 单价") in items and ("正交", "单价 × 单位成本") in items
-    assert ("对账", "revenue") in items and ("对账", "gross_margin") in items
+    assert expected_items(m) == [("对账", "revenue"), ("对账", "gross_margin")]
diff --git a/tests/test_full_context.py b/tests/test_full_context.py
index 0fd1576..b82237a 100644
--- a/tests/test_full_context.py
+++ b/tests/test_full_context.py
@@ -3851,6 +3851,7 @@ def _e2e(monkeypatch, full, tmp_path):
 
     def propose(code, r, body, md, as_of, out, client, arts, **k):
         calls["balances"] = k.get("balances")
+        calls["method_review"] = k.get("method_review")
         (out / "assumptions.json").write_text('{"rows": [], "approved_by": ""}',
                                               encoding="utf-8")
         (out / "assumptions.md").write_text("# DCF 判断项\n", encoding="utf-8")
@@ -4332,6 +4333,12 @@ def test_第一轮驳回第二轮通过_一路到简报(tmp_path, monkeypatch):
     review = (tmp_path / full.REVIEW_MD).read_text(encoding="utf-8")
     assert "## 方法论审核" in review and "待采集" in review
 
+    # 门③：预研审核的待采集项带进第三次调用（不重新审核）。
+    (tmp_path / full.BRIEF).write_text("## 一节\n正文。", encoding="utf-8")
+    full.run_assume("600183", _PhaseR(), d, tmp_path, "乙")
+    assert calls["method_review"] and all("待采集" in x for x in calls["method_review"])
+    assert len(calls["judge"].prompts) == 2, "第三次调用不该再调审核者"
+
 
 def test_人改过drivers_md后重跑_不送审_门二标注未经审核(tmp_path, monkeypatch):
     from ir_agent import full
```

</details>

---

### Task 5: 收尾与试跑

- [ ] **Step 1: 全量与一致性**

```bash
python -m pytest -q -m "not live" -W error --collect-only | tail -1   # 2372 tests collected
python -m pytest -q -m "not live" -W error                           # 2372 passed
git diff judge-dev -- ir_agent tests | head                          # 应为空
```

- [ ] **Step 2: 清理开发分支与工作树**（上一步为空之后）：`git worktree remove <judge-dev 工作树>`、`git branch -D judge-dev`。

- [ ] **Step 3: 试跑（子项目一、二合在一起，用户确认费用后）**

```bash
mkdir -p out/600183-p2-brief3
mv out/600183-p2/{brief.md,review.md,stance_b.json,stance_b.md,drivers_failed_raw.md,run_brief.log} out/600183-p2-brief3/ 2>/dev/null
python -m ir_agent 600183 --year 2025 --brief out/600183-p2 --verified-by xhl --refresh-stance 2>&1 | tee out/600183-p2/run_brief.log
```

核对：`drivers_rounds.md` 每轮都有提出者原文与审核逐项结论；通过时 `drivers.md` 有「方法论审核」、`drivers_review.json` 有指纹；`factors.json` schema 2；`review.md` 有方法论审核一节；不通过时终端给两条做法、没有往下跑。试跑通过后推送 ir-agent（记忆 `ir-agent-push-gate`）。

## 自查

**1. Spec 覆盖**

| spec | 任务 |
|---|---|
| 一、流程与轮次（代码只查能不能算、三道检查移交、轮数、补答、停下、各轮落盘、两个 client） | 1、2、3 |
| 二、清单 7 类、作答格式、四级、理由必填、引文只标注、审核者资料与额外事实 | 2 |
| 三、`drivers_review.json`、drivers.md 一节、指纹读回、未经审核标注、门② 一节、门③ 只对账与遗留、终端措辞登记 | 3、4 |
| 四、异常表（含行业分类缺失保持现状）、测试、不做 | 2、3、4 |

**2. 写计划时发现并已修的问题**
- 审核规则正文用了与清单同形的 `- [模式] …` 说明行，假审核者把它们当成了要作答的条目（真模型同样可能混淆）——改为「模式：…」写法并加守卫测试；
- 模型常把条目加粗写成 `**[因子] …**`，解析原先不认；
- 指纹按「解析后再输出的定义块」算，只改说明文字不算改动（端到端测试钉住）；一开始的测试误改了说明文字里的「陷阱:」，正好验证了这一点；
- 「预研失败时第三次调用不补做预研」原靠「预研失败退回两标量」构造场景，审核失败现在会停下，改用仍保留两标量降级的「取不到行业分类」构造，测试原意不变。

**3. 类型一致性**：`ReviewResult`/`Verdict`/`Round` 只在 `methodology_review` 定义；`full` 只经 `MR.` 访问；`judge_stub.Judge` 的条目读取与 `review()` 提示词里的「必须逐条作答的条目」一节同源（`test_清单与提示词里的条目一致` 钉住）。
