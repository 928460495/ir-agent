# 判断项环节：填写者 + 审核者（子项目三）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 第一阶段之三 `--assume` 的判断项合格与否全部交给 AI 审核者判定；代码只读数、计算、核对，把结果作为事实交给审核者。

**Architecture:** 新模块 `ir_agent/assume_review.py`（结构照搬 `methodology_review`）负责清单、作答解析、三轮循环、落盘格式；`assume.parse` / `assume.check` 从「驳回」改为「记事实」（`read_notes`、`Facts(problems, blocking)`）；`assume.decide` 变成 `fill_and_review` 的薄包装；`full` 接线、删掉门③ 的 `factor_review` 与判断项假设辩论；第二阶段比对取值指纹，人改过的只标注。

**Tech Stack:** Python 3、pytest；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-15-assume-judge-design.md`

**演练：** 每个任务的 diff 取自开发分支 `dev/assume-judge`，并已在一个干净的 main worktree（`db71af9`）上按「先放测试 → 再放实现」逐任务演练过；下面每一步的红绿数字就是演练实测。跑测试统一用：

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```

（worktree 里有 8 条依赖 `out/` 产物的用例会跳过；在主仓库里跑它们会执行。）

## Global Constraints

- 判断项合格与否**全部由 AI 判定，代码不参与判定**；代码只读数、计算、核对引文，读不出、算不下去也只作为事实报告。
- 清单 5 类：[取值]、[平衡点]、[依据]、[合成]、[后果]；两标量模式只有 [依据]、[合成]、[后果]。
- 审核者可附选填的「补充」；补充判致命同样驳回。
- 审核者资料：填写者看到的全部 + 代码事实 + 完整账本事实目录（`FactCatalog`），不联网。
- 四级结论原话：通过（逻辑完全正确，资料能够对得上）/ 提醒（逻辑部分正确，资料对得上，但结论较为牵强）/ 致命（逻辑完全无法支撑结论）/ 待采集（逻辑正确，资料尚未采集到）。
- 致命必附「建议：…」；最多 3 轮；审核者补答最多 2 次、不消耗填写者轮数；仍不通过停下交人工；**不再退回两标量模式**。
- 审核通过但代码确实算不下去 → 停下，写明哪一格、为什么。
- 辩论② 提示词不改：平衡点单位错误由审核者判出、在建议里写出换算值，由填写者换算。
- 第二阶段读回人改过的判断项：不送审，只标注「人工修改后未经审核」并列出改动（取值指纹）。
- 停下时不写 `assumptions.json`（门③ 批准的对象），底稿写 `assumptions_failed.json`。
- 测试不调模型：假填写者、`tests/judge_stub.Judge` 假审核者。
- 变异验证用**复制备份**还原（不要用 `git checkout` 还原，会丢未提交的改动）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `ir_agent/assume_review.py`（新） | 判断项审核者：清单、规则提示词、作答解析（含补充）、给审核者的事实、三轮循环、指纹、落盘格式 |
| `ir_agent/assume.py` | 宽容读数（`read_notes`）、`check() -> Facts`、`decide` 薄包装、提示词措辞、清单「判断项审核」一节 |
| `ir_agent/methodology_review.py` | `parse(kinds=)`、`ledger_decl_lines` 抽出共用 |
| `ir_agent/full.py` | 第三次调用接线与落盘、停下处理、终端保留意见；第二阶段指纹比对与「算不下去才停」 |
| `ir_agent/research.py` | 文件名常量、`_PREFIX` 登记、`next_actions` 第二条做法 |
| `ir_agent/stance.py` | 跟随 `_basis` 新返回值 |
| `ir_agent/factor_review.py` | **删除**（并入审核者 [合成]） |
| `tests/test_assume_review.py`（新） | 审核者单元、轮次、指纹、第二阶段、生益回放 |
| `tests/judge_stub.py` | 条目块与类别放开、`extra=` 附加行 |
| `tests/test_factor_review.py`、`tests/test_assume_refill.py` | **删除** |

---

### Task 1: 审核作答解析器接受类别参数

**Files:**
- Modify: `ir_agent/methodology_review.py`
- Modify: `tests/test_methodology_review.py`

**Interfaces:**
- Produces: `methodology_review.parse(raw, expected, kinds=KINDS) -> list[Verdict]`；`_item_rx(kinds)`、`_head_rx(kinds)`（模块级 `_ITEM`/`_HEAD` 删除）

**为什么：** 子项目二的 `methodology_review.parse` 把 7 类条目写死在模块级正则里。判断项审核要用另一套 5 类（[取值][平衡点][依据][合成][后果]），复用同一个解析器（条目名容错、致命必附建议都要一样），所以把类别做成参数；[合成] 条目名后抄了「= …」也认（与 [公式] 同理）。

- [ ] **Step 1: 写失败测试**

```diff
diff --git a/tests/test_methodology_review.py b/tests/test_methodology_review.py
index 282ed0a..b41df7f 100644
--- a/tests/test_methodology_review.py
+++ b/tests/test_methodology_review.py
@@ -320,3 +320,13 @@ class TestSuggestions:
 
     def test_规则写明致命要给建议(self):
         assert "建议：" in R.RULES and "致命" in R.RULES.split("建议：")[0]
+
+
+def test_解析器可换一套类别_合成条目后抄了公式也认():
+    """判断项审核（子项目三）复用同一个解析器，类别换成 [取值]…[后果]。"""
+    raw = ("[取值] 提价（提价对毛利率的累计贡献）：致命　按小数填了　建议：0.0547 改为 5.47\n"
+           "[合成] 毛利率 = 基期(毛利率) + (提价 + 原料) / 100：通过")
+    got = R.parse(raw, [("取值", "提价（提价对毛利率的累计贡献）"), ("合成", "毛利率")],
+                  kinds=("取值", "平衡点", "依据", "合成", "后果"))
+    assert [v.level for v in got] == ["致命", "通过"]
+    assert got[0].suggest == "0.0547 改为 5.47"
```

- [ ] **Step 2: 跑测试，确认红**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2411 passed, 8 skipped`。失败的是：

- `tests/test_methodology_review.py::test_解析器可换一套类别_合成条目后抄了公式也认`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/methodology_review.py b/ir_agent/methodology_review.py
index 9cca875..8a18eb1 100644
--- a/ir_agent/methodology_review.py
+++ b/ir_agent/methodology_review.py
@@ -36,9 +36,15 @@ KINDS = ("模式", "因子", "正交", "公式", "常量", "账本", "覆盖")
 MAX_ROUNDS = 3          # 提出者最多写 3 轮（代码检查不过、审核驳回都算一轮）
 JUDGE_TRIES = 3         # 审核者作答 1 次 + 漏答补答 2 次，不消耗提出者轮数
 
-# 行首允许列表符号与加粗（模型常写 `- ` 或 `**[因子] …**`）。
-_ITEM = re.compile(rf"(?m)^[ \t]*[-*•·]*[ \t]*(?=\[(?:{'|'.join(KINDS)})\])")
-_HEAD = re.compile(rf"\[({'|'.join(KINDS)})\]\s*(.*)", re.S)
+# 行首允许列表符号与加粗（模型常写 `- ` 或 `**[因子] …**`）。类别集合由调用方
+# 给（判断项审核 `assume_review` 用另一套类别），默认是本模块的 7 类。
+def _item_rx(kinds) -> re.Pattern[str]:
+    return re.compile(rf"(?m)^[ \t]*[-*•·]*[ \t]*(?=\[(?:{'|'.join(kinds)})\])")
+
+
+def _head_rx(kinds) -> re.Pattern[str]:
+    return re.compile(rf"\[({'|'.join(kinds)})\]\s*(.*)", re.S)
+
 _QUOTE = re.compile(r"「([^」]{4,})」")
 _REF = re.compile(r"\[\[([^\]@]+)@([^\]]+)\]\]")
 _KEY_DECL = re.compile(r"\[\[([A-Za-z_][\w.]*)(?:@[^\]]*)?\]\]")
@@ -161,23 +167,25 @@ def checklist(model, themes: list[str]) -> list[tuple[str, str]]:
 def _subject_rx(kind: str, subject: str) -> re.Pattern[str]:
     """条目名的匹配式。审核者照它看到的原文写条目名（生益科技第二次调用第 3 轮漏掉
     [公式] 与 [账本] 共 4 条，审核未完成）：`[[key]]` 带不带 `@期间` 都认；[公式]
-    条目名后面抄了「= …」也认。"""
+    条目名后面抄了「= …」也认（判断项审核的 [合成] 同理）。"""
     base = re.sub(r"@[^\]]*(?=\]\])", "", subject)
     pat = re.escape(base).replace(r"\]\]", r"(?:@[^\]]*)?\]\]")
-    if kind == "公式":
+    if kind in ("公式", "合成"):
         pat += r"(?:\s*[=＝][^：:\n]*)?"
     return re.compile(pat)
 
 
-def parse(raw: str, expected: list[tuple[str, str]]) -> list[Verdict]:
+def parse(raw: str, expected: list[tuple[str, str]],
+          kinds: tuple = KINDS) -> list[Verdict]:
     """解析审核者的回答。**按预期条目名匹配**，不靠正则切条目名 —— 完整名里
     可能带冒号、括号。缺条目、或提醒/致命/待采集没写理由 → ReviewIncomplete。"""
     by_kind: dict[str, list[str]] = {}
     for k, s in expected:
         by_kind.setdefault(k, []).append(s)
     got: dict[tuple[str, str], Verdict] = {}
-    for block in _ITEM.split(raw):
-        m = _HEAD.match(block.strip())
+    item_rx, head_rx = _item_rx(kinds), _head_rx(kinds)
+    for block in item_rx.split(raw):
+        m = head_rx.match(block.strip())
         if not m:
             continue
         kind, rest = m.group(1), m.group(2).strip()
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2412 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- 把 `_subject_rx` 里的 `kind in ("公式", "合成")` 改回 `kind == "公式"` → 新测试红
- 把 `parse` 里的 `_item_rx(kinds)` 换成 `_item_rx(KINDS)` → 新测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor(methodology_review): 解析器接受类别参数，供判断项审核复用

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 判断项读数改为宽容读取

**Files:**
- Modify: `ir_agent/assume.py`
- Modify: `ir_agent/stance.py`
- Modify: `tests/test_assume.py`
- Modify: `tests/test_assume_formula.py`

**Interfaces:**
- Produces: `AssumptionSet.read_notes: list[str]`；`Row.raw: tuple`；`_basis(raw) -> (list[Basis], str)`；`_factor_basis(raw, fd, as_of, prior=None) -> (list[Basis], str)`；`_unit_notes(unit, where, cells) -> list[str]`；`_cells(raw) -> list[str]`（>6 段时第 6 段起合并）

**为什么：** 原 `_parse_scalar` / `_parse_factors` / `_unit_guard` / `_basis` / `_factor_basis` 里约 30 处 `AssumeFailed`：读不出、单位写法、依据写法、重复行都直接驳回。现在一律**读下来并记进 `AssumptionSet.read_notes`**（交给审核者的事实），读不出的行跳过，重复行按最后一行；第 6 段起全部算依据栏（生益科技第 3 次被拒的 7 段行）。`_basis`/`_factor_basis` 改为返回 `(依据, 问题)`，`stance.parse_evidence` 跟着改。`Row.raw` 记下三档原文，供审核者「原文 → 读作」并排看。

- [ ] **Step 1: 写失败测试**

```diff
diff --git a/tests/test_assume.py b/tests/test_assume.py
index db134ae..869cc8e 100644
--- a/tests/test_assume.py
+++ b/tests/test_assume.py
@@ -14,6 +14,11 @@ from ir_agent.ledger import FactLedger
 from ir_agent.sources.external import Tier
 from ir_agent.sources.web import Finding
 
+
+def _notes(a) -> str:
+    """代码读数/核对时记下的事实（子项目三：只报事实，不驳回）。"""
+    return "\n".join(a.read_notes + list(getattr(getattr(a, "facts", None), "problems", [])))
+
 D = Decimal
 AS_OF = date(2026, 9, 7)
 BODY = "行业营收同比高于本司，份额在丢，因此基准增速取低于行业。"
@@ -44,10 +49,9 @@ def test_只认两个判断项():
     assert set(assume.DRIVERS) == {"营收增速", "毛利率"}
 
 
-def test_填校准项被驳回且说明原因():
-    with pytest.raises(AssumeFailed) as e:
-        assume.parse(_line(item="销售费用率"), 2)
-    assert "由历史数据校准" in str(e.value)
+def test_填校准项记进读数事实且说明原因():
+    a = assume.parse(_line(item="销售费用率"), 2)
+    assert "由历史数据校准" in _notes(a)
 
 
 # ── 三档必须拉开 ─────────────────────────────────────────────
@@ -84,16 +88,16 @@ def test_漏掉某年被驳回():
         _ok(a, 3)
 
 
-def test_年份越界被驳回():
-    with pytest.raises(AssumeFailed, match="超出预测期"):
-        assume.parse(_line(y=9), 5)
+def test_年份越界记进读数事实():
+    a = assume.parse(_line(y=9), 5)
+    assert re.search("超出预测期", _notes(a)), _notes(a)
 
 
 # ── 依据 ─────────────────────────────────────────────────────
 
-def test_缺依据被驳回():
-    with pytest.raises(AssumeFailed, match="缺少可核验的依据"):
-        assume.parse("假设: 营收增速 | 第1年 | 2% | 6% | 10% | 我的判断", 1)
+def test_缺依据记进读数事实():
+    a = assume.parse("假设: 营收增速 | 第1年 | 2% | 6% | 10% | 我的判断", 1)
+    assert re.search("缺少可核验的依据", _notes(a)), _notes(a)
 
 
 def test_依据查无此句被驳回():
@@ -286,11 +290,10 @@ def test_按因子解析():
         "capacity", "asp", "util_leverage", "input_cost"}
 
 
-def test_未知因子被驳回并列出可选():
+def test_未知因子记进读数事实并列出可选():
     bad = f"假设: 品牌力 | 第1年 | 1% | 2% | 3% | 「{Q}」"
-    with pytest.raises(AssumeFailed) as e:
-        assume.parse(bad, 1, decomps=_decomps())
-    assert "产能" in str(e.value) and "单价" in str(e.value)
+    a = assume.parse(bad, 1, decomps=_decomps())
+    assert "产能" in _notes(a) and "单价" in _notes(a)
 
 
 def test_缺某个因子被驳回():
@@ -802,14 +805,14 @@ def test_gm_hist为空时报错而不是静默复用gm0():
 
 # ── C4：pp 因子写成百分号被静默缩小 100 倍 ───────────────────
 
-def test_pp因子写百分号被拒绝():
+def test_pp因子写百分号记进读数事实():
     """C4 实测复现：`_pct('-3pp') == -3`（贡献 -3pp，正确），但
     `_pct('-3%') == -0.03`（贡献 -0.03pp，缩小 100 倍）。单调性校验、
     闭合校验都不会抓住这个错——因子各自的三档依旧严格拉开，只是
     合成结果几乎不动。必须在解析时就把带 % 的字面量挡下来。"""
     bad = f"假设: 稼动率摊薄 | 第1年 | -3% | -1 | 0.5 | 「{Q}」"
-    with pytest.raises(AssumeFailed, match="pp"):
-        assume.parse(bad, 1, decomps=_decomps())
+    a = assume.parse(bad, 1, decomps=_decomps())
+    assert re.search("pp", _notes(a)), _notes(a)
 
 
 def test_pp因子不带百分号正常解析():
@@ -843,15 +846,17 @@ def test_c4修复后pp显示为pp不是百分号():
 # 校验全部通过，因为拉开/闭合看的是同一行的三档，不看重复行之间的
 # 分歧。
 
-def test_同一因子同一年重复行被驳回():
+def test_同一因子同一年重复行记进读数事实():
     """C2 实测复现：重试提示词里的「请修正后重新输出全部行」很容易
     诱发模型把修正前/修正后两版都留在同一次回复里。必须在解析阶段
     就拒绝，不能留给下游两处各自选一行、选出两个不同的数。"""
     bad = ("假设: 产能 | 第1年 | -1% | 1% | 3% | 「先给1%」\n"
            "假设: 产能 | 第1年 | -5% | 30% | 35% | 「修正后」\n")
-    with pytest.raises(AssumeFailed, match="产能") as e:
-        assume.parse(bad, 1, decomps=_decomps())
-    assert "第 1 年" in str(e.value)
+    a = assume.parse(bad, 1, decomps=_decomps())
+    assert re.search("产能", _notes(a)), _notes(a)
+    assert "第 1 年" in _notes(a)
+    got = [r for r in a.rows if r.factor_key == "capacity"]
+    assert len(got) == 1 and got[0].base == D("0.30"), "按最后一行读，前一行丢掉"
 
 
 def _dup_rows():
@@ -913,29 +918,29 @@ def test_to_driver_block与synthesize取值规则一致():
 # 裸数 0.5：合成后渲染成 "50.00%"，对一个扩产故事完全看得过去，人
 # 不会觉得异常。
 
-def test_变化率因子裸数被拒绝():
+def test_变化率因子裸数记进读数事实():
     """I1 实测复现：修复前 `产能 | 第1年 | 0.5 | 0.5 | 0.6` 会被
     `_pct` 当成小数 0.5 直接使用，合成/显示成 50.00%——对一个扩产
     故事看起来完全正常，人根本看不出这里想表达的其实是 0.5%。"""
     bad = f"假设: 产能 | 第1年 | 0.5 | 0.5 | 0.6 | 「{Q}」"
-    with pytest.raises(AssumeFailed, match="变化率"):
-        assume.parse(bad, 1, decomps=_decomps())
+    a = assume.parse(bad, 1, decomps=_decomps())
+    assert re.search("变化率", _notes(a)), _notes(a)
 
 
-def test_变化率因子写成pp后缀被拒绝():
+def test_变化率因子写成pp后缀记进读数事实():
     """I1 实测复现：`-3.5pp` 没有 %，会被当裸数 -3.5 直接使用，合成
     显示成 -350.00%——把 pp 误当变化率填的写法此前被静默吞下。"""
     bad = f"假设: 产能 | 第1年 | -3.5pp | -1pp | 0.5pp | 「{Q}」"
-    with pytest.raises(AssumeFailed, match="变化率"):
-        assume.parse(bad, 1, decomps=_decomps())
+    a = assume.parse(bad, 1, decomps=_decomps())
+    assert re.search("变化率", _notes(a)), _notes(a)
 
 
-def test_变化率因子写成bp被拒绝():
+def test_变化率因子写成bp记进读数事实():
     """I1 实测复现：`-350bp` 没有 %，会被当裸数 -350 直接使用——
     bp 与 % 不是同一套单位，不做隐式换算，必须显式拒绝并提示换算。"""
     bad = f"假设: 产能 | 第1年 | -350bp | -100bp | 50bp | 「{Q}」"
-    with pytest.raises(AssumeFailed, match="bp"):
-        assume.parse(bad, 1, decomps=_decomps())
+    a = assume.parse(bad, 1, decomps=_decomps())
+    assert re.search("bp", _notes(a)), _notes(a)
 
 
 def test_变化率因子全角百分号被规范化接受():
@@ -948,14 +953,14 @@ def test_变化率因子全角百分号被规范化接受():
     assert row.bear == D("-0.06")
 
 
-def test_pp因子全角百分号也被拒绝():
+def test_pp因子全角百分号也记进读数事实():
     """I1：pp 侧的守卫此前只认 ASCII %，全角 ％ 能绕过去（`-3％` 被
     当裸数 -3 直接使用，对 pp 而言"碰巧对"，但下一次全角换成
     `-3.5％` 就会变成 -3.5pp 与 -350% 傻傻分不清）。规范化后全角
     ％ 与半角 % 一视同仁，同样被拒绝。"""
     bad = f"假设: 稼动率摊薄 | 第1年 | -3％ | -1 | 0.5 | 「{Q}」"
-    with pytest.raises(AssumeFailed, match="pp"):
-        assume.parse(bad, 1, decomps=_decomps())
+    a = assume.parse(bad, 1, decomps=_decomps())
+    assert re.search("pp", _notes(a)), _notes(a)
 
 
 # ── I5：pp 的参照点在提示词里没定义 ───────────────────────────
@@ -1077,7 +1082,7 @@ def test_两标量模式本身连续失败仍照常拒绝():
 # `ashare_build.py` 按下标 `i`（不是按年份）取值，一条重复行会让它
 # 后面每一年整体错位一格。
 
-def test_两标量模式重复行被驳回():
+def test_两标量模式重复行记进读数事实():
     """C-a 实测复现：`_parse_scalar` 修复前不查重，"先给的" 与 "修正
     后" 两行会一起留下来，交给 `path()` 排序后返回，下游按下标取值时
     会整体错位。必须在解析阶段就拒绝。"""
@@ -1087,9 +1092,9 @@ def test_两标量模式重复行被驳回():
         _line("营收增速", 2, "3%", "7%", "11%"),
         _line("毛利率", 1, "12%", "14%", "16%"),
         _line("毛利率", 2, "12%", "14%", "16%")])
-    with pytest.raises(AssumeFailed, match="营收增速") as e:
-        assume.parse(bad, 2)
-    assert "第 1 年" in str(e.value)
+    a = assume.parse(bad, 2)
+    assert re.search("营收增速", _notes(a)), _notes(a)
+    assert "第 1 年" in _notes(a)
 
 
 def test_两标量模式check也拒绝重复行不只依赖parse():
@@ -1164,8 +1169,8 @@ def test_变化率因子裸数与不相干百分号不再绕过守卫():
     `_pct` 却会解析出裸数 2026（渲染成 202600%）。守卫必须复用
     `_pct` 自己会用到的那个 token 来判断，两处才是同一套判据。"""
     bad = f"假设: 产能 | 第1年 | 2026年+5% | -2% | 1% | 「{Q}」"
-    with pytest.raises(AssumeFailed, match="变化率"):
-        assume.parse(bad, 1, decomps=_decomps())
+    a = assume.parse(bad, 1, decomps=_decomps())
+    assert re.search("变化率", _notes(a)), _notes(a)
 
 
 def test_变化率单位守卫不误伤已确认合法的写法():
@@ -1250,13 +1255,13 @@ def test_to_driver_block缺行时报出因子与年份而不是裸KeyError():
 
 # ── Minor-5：两标量降级模式没有任何单位守卫 ───────────────────────
 
-def test_两标量模式bp字面量被拒绝():
+def test_两标量模式bp字面量记进读数事实():
     """Minor-5 实测复现：与 C-a 同一片薄弱区域——两标量降级模式此前
     完全没有单位守卫，`-350bp` 会被 `_pct` 当裸数 -350 直接吞下，
     渲染成 -35000%。"""
     bad = _line("营收增速", 1, "-350bp", "-100bp", "50bp")
-    with pytest.raises(AssumeFailed, match="bp"):
-        assume.parse(bad, 1)
+    a = assume.parse(bad, 1)
+    assert re.search("bp", _notes(a)), _notes(a)
 
 
 def test_两标量模式小数记法不受因子侧必须带百分号的约束():
@@ -1309,18 +1314,18 @@ def test_无数据因子明说估计不再被驳回():
     assert a.rows[0].bases == [], "无数据估计应落地为空依据，不是编一个假依据"
 
 
-def test_对有数据的因子写无数据估计仍被驳回():
+def test_对有数据的因子写无数据估计仍记进读数事实():
     """「无数据」只对确实没有数据支撑的因子开放——否则会变成模型对任何
     因子都想用的万能逃生口，防的正是这个。"""
     d = _one_factor_decomp()
     fd = {"utilization": gaps.FactorData(
         factor=d.factors[0], status=gaps.ANNUAL, note="「稼动率保持稳定」——《核心竞争力分析》")}
     raw = "假设: 稼动率 | 第1年 | -2% | 0% | 2% | 无数据（估计），图省事"
-    with pytest.raises(AssumeFailed, match="无数据"):
-        assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
+    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
+    assert re.search("无数据", _notes(a)), _notes(a)
 
 
-def test_提示词列出的会被驳回的状态等于门实际驳回的状态():
+def test_提示词列出的状态等于代码会记进读数事实的状态():
     """**这条锁的是「提示词没说、门却会驳回」这类遗漏**：子项目 B 加了
     ②.5 公司公告（`gaps.ANNOUNCE`），门对它照样驳回「无数据（估计）」，
     提示词的枚举却漏了「公告披露」——公告摘录常常根本没有数字，这时
@@ -1342,9 +1347,8 @@ def test_提示词列出的会被驳回的状态等于门实际驳回的状态()
     for st in statuses:
         fd = {"utilization": gaps.FactorData(
             factor=d.factors[0], status=st, note="占位")}
-        try:
-            assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
-        except AssumeFailed:
+        if assume.parse(raw, 1, decomps=[d], factor_data=fd,
+                        as_of=AS_OF).read_notes:
             rejected.append(st)
     # 门的语义：除 MISSING 外一律驳回——先把这一前提钉住，否则下面的
     # 比对可能在比两个都错的东西。
@@ -1796,13 +1800,13 @@ def test_同上写法变体_同前_见上_同该因子上一年():
         assert a.rows[1].bases == a.rows[0].bases, variant
 
 
-def test_因子第一次出现就写同上被驳回():
+def test_因子第一次出现就写同上记进读数事实():
     """「同上」指的是同一因子上一行的依据——该因子第一次出现（没有
     上一行可回指）时必须驳回，不能悄悄放行成"没有依据"。"""
     d = _one_factor_decomp()
     raw = "假设: 稼动率 | 第1年 | 5% | 8% | 12% | 同上，参照上次报告"
-    with pytest.raises(AssumeFailed, match="没有上一行"):
-        assume.parse(raw, 1, decomps=[d], factor_data={}, as_of=AS_OF)
+    a = assume.parse(raw, 1, decomps=[d], factor_data={}, as_of=AS_OF)
+    assert re.search("没有上一行", _notes(a)), _notes(a)
 
 
 def test_同上不跨因子继承():
@@ -1819,8 +1823,8 @@ def test_同上不跨因子继承():
         "假设: 稼动率 | 第1年 | 2% | 4% | 6% | 「平均稼动率约78%」，参照采集数据",
         "假设: 单价 | 第1年 | -1% | 1% | 3% | 同上，判断口径一致",
     ])
-    with pytest.raises(AssumeFailed, match="没有上一行"):
-        assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
+    a = assume.parse(raw, 1, decomps=[d], factor_data=fd, as_of=AS_OF)
+    assert re.search("没有上一行", _notes(a)), _notes(a)
 
 
 def test_同行真实引文优先于同上字样():
@@ -2595,3 +2599,16 @@ def test_没有factor_data时不标记():
     raw = "假设: 稼动率 | 第1年 | 2% | 4% | 6% | [[revenue@2025FY]]"
     a = assume.parse(raw, 1, decomps=[d], factor_data=None, as_of=AS_OF)
     assert a.rows[0].basis_flag == ""
+
+
+def test_七段的行_第六段起全算依据栏():
+    """生益科技 2026-10-15 第 3 次被拒的原文：模型写了 7 段（理由一段、`[[key]]`
+    一段）—— 第 1 次驳回信息让它「在第 7 字段填偏离说明」，而解析器只读第 6 段，
+    真正的依据被丢掉、判为「缺少可核验的依据」。内容其实是对的。"""
+    raw = ("假设: 产能 | 第1年 | 12% | 20% | 30% | 中性档采纳辩论平衡点20%；"
+           "结构性AI算力需求是驱动 | [[industry.revenue_yoy@2025FY]]")
+    a = assume.parse(raw, 1, decomps=_decomps())
+    r = [x for x in a.rows if x.factor_key == "capacity"][0]
+    assert [b.describe() for b in r.bases] and "revenue_yoy" in r.bases[0].describe()
+    assert r.note.startswith("中性档采纳辩论平衡点20%") and not r.note.endswith("|")
+    assert "缺少可核验的依据" not in _notes(a)
diff --git a/tests/test_assume_formula.py b/tests/test_assume_formula.py
index 91aedda..00963c1 100644
--- a/tests/test_assume_formula.py
+++ b/tests/test_assume_formula.py
@@ -4,6 +4,7 @@
 """
 from __future__ import annotations
 
+import re
 from datetime import date
 from decimal import Decimal as D
 
@@ -14,6 +15,11 @@ from ir_agent.assume import AssumeFailed, Case
 from ir_agent.ledger import Fact, FactLedger, Method
 from ir_agent.methodology import parse as parse_meth
 
+
+def _notes(a) -> str:
+    """代码读数/核对时记下的事实（子项目三：只报事实，不驳回）。"""
+    return "\n".join(a.read_notes + list(getattr(getattr(a, "facts", None), "problems", [])))
+
 AS_OF = date(2026, 10, 14)
 
 CHANGE = parse_meth("""模式: 变化
@@ -86,16 +92,16 @@ class TestParse:
         assert a.base_values["量"].value == D(100)
         assert a.base_values["本"].source == "AI"
 
-    def test_基期行重复被驳回(self):
-        with pytest.raises(AssumeFailed, match="基期出现了不止一行"):
-            assume.parse(_level_lines() + f"\n基期: 量 | 99 | {Q}", 1, decomps=LEVEL)
+    def test_基期行重复记进读数事实(self):
+        a = assume.parse(_level_lines() + f"\n基期: 量 | 99 | {Q}", 1, decomps=LEVEL)
+        assert re.search("基期出现了不止一行", _notes(a)), _notes(a)
 
     @pytest.mark.parametrize("bad", ["110%", "110bp"])
     def test_其他单位不带百分号(self, bad):
         txt = _level_lines().replace("假设: 量 | 第1年 | 100 | 110 |",
                                      f"假设: 量 | 第1年 | 100 | {bad} |")
-        with pytest.raises(AssumeFailed, match="按原数填"):
-            assume.parse(txt, 1, decomps=LEVEL)
+        a = assume.parse(txt, 1, decomps=LEVEL)
+        assert re.search("单位是「|bp", _notes(a)), _notes(a)
 
 
 class TestLevelMode:
```

- [ ] **Step 2: 跑测试，确认红**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`21 failed, 2392 passed, 8 skipped`。失败的是：

- `tests/test_assume.py::test_填校准项记进读数事实且说明原因`
- `tests/test_assume.py::test_年份越界记进读数事实`
- `tests/test_assume.py::test_缺依据记进读数事实`
- `tests/test_assume.py::test_未知因子记进读数事实并列出可选`
- `tests/test_assume.py::test_pp因子写百分号记进读数事实`
- `tests/test_assume.py::test_同一因子同一年重复行记进读数事实`
- `tests/test_assume.py::test_变化率因子裸数记进读数事实`
- `tests/test_assume.py::test_变化率因子写成pp后缀记进读数事实`
- `tests/test_assume.py::test_变化率因子写成bp记进读数事实`
- `tests/test_assume.py::test_pp因子全角百分号也记进读数事实`
- `tests/test_assume.py::test_两标量模式重复行记进读数事实`
- `tests/test_assume.py::test_变化率因子裸数与不相干百分号不再绕过守卫`
- `tests/test_assume.py::test_两标量模式bp字面量记进读数事实`
- `tests/test_assume.py::test_对有数据的因子写无数据估计仍记进读数事实`
- `tests/test_assume.py::test_提示词列出的状态等于代码会记进读数事实的状态`
- `tests/test_assume.py::test_因子第一次出现就写同上记进读数事实`
- `tests/test_assume.py::test_同上不跨因子继承`
- `tests/test_assume.py::test_七段的行_第六段起全算依据栏`
- `tests/test_assume_formula.py::TestParse::test_基期行重复记进读数事实`
- `tests/test_assume_formula.py::TestParse::test_其他单位不带百分号[110%]`
- `tests/test_assume_formula.py::TestParse::test_其他单位不带百分号[110bp]`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index f089187..c2ed9a1 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -162,6 +162,9 @@ class Row:
     # 这一行必须带着标记** —— 与 `basis_unverified` 同一处理：派生结果，不进
     # to_json；第二阶段没有 factor_data，算出来恒为空，行为不变。
     basis_flag: str = ""
+    # 三档的原文（悲观、中性、乐观）。给审核者「原文 → 读作」并排看（子项目三：
+    # 0.0547 与「5.47 个百分点」就是这样对出来的）。派生量，不进 JSON。
+    raw: tuple = ()
 
 
 @dataclass
@@ -360,6 +363,9 @@ class AssumptionSet:
     # 方法论审核遗留（子项目二）：预研阶段审核者判为提醒/待采集的条目，或
     # 「人工修改后未经审核」。派生量，不进 JSON —— 第三次调用从 drivers_review.json 读。
     method_review: list = field(default_factory=list)
+    # 代码读数时发现的问题（读不出、单位写法、依据写法、重复行……）。**只是事实，
+    # 不驳回**，交给判断项审核者（子项目三）。派生量，不进 JSON。
+    read_notes: list = field(default_factory=list)
 
     def _m(self) -> FormulaModel | None:
         if self.model is None and self.decomps:
@@ -735,94 +741,119 @@ def parse(text: str, years: int,
     return _parse_factors(text, years, decomps, factor_data, as_of)
 
 
-def _unit_guard(unit: str, label: str, cells: list[str]) -> None:
-    """按单位挡字面量。C4：pp 与 % 不是一回事；I1：变化率必须显式带 %、不认 bp；
-    公式化后的其他单位（万吨、元…）按原数，不带 % 也不带 bp。"""
+def _shown(raw: str) -> str:
+    try:
+        return f"{_pct(raw)}"
+    except AssumeFailed:
+        return "读不出"
+
+
+def _unit_notes(unit: str, where: str, cells: list[str]) -> list[str]:
+    """代码按什么读法读了这几格。**只报事实，不驳回**（子项目三，2026-10-15）。
+
+    读法固定：带 % 的除以 100，裸数按原数，bp 不换算。单位与写法对不上时，把
+    「原文 → 读作」摆给审核者，由它判断是不是填写者的本意 —— 生益科技
+    2026-10-15：理由栏写「5.47 个百分点」，表里填 0.0547，代码照读、合成照算，
+    只有对着理由栏才看得出缩小了 100 倍。此前这里是三道字面闸门（C4 pp 带 %、
+    I1 变化率裸数、bp），它们拦得住字面写法，拦不住 0.0547 这种合法的数，反倒
+    让模型连挂三次后整个分解式被丢掉。
+    """
     norm = [c.replace("％", "%") for c in cells]
+    shown = " / ".join(_shown(c) for c in cells)
+    raw = " / ".join(cells)
+    if any("bp" in n.lower() for n in norm):
+        return [f"{where}写了 bp（{raw}），代码不换算 bp，按原数读作 {shown}"]
     if unit == "pp":
-        if any("%" in c for c in norm):
-            raise AssumeFailed(
-                f"因子「{label}」按 pp 计，不按 %：{' / '.join(cells)} 里带了 % "
-                "—— pp 与 % 不是一回事，85%→82% 是 −3pp，写成 −3% 会被当成 "
-                "−0.03pp，缩小 100 倍。请直接填数字（如 −3），不要带 %。")
-        return
+        if any("%" in n for n in norm):
+            return [f"{where}单位是 pp，却写了 %（{raw}），按 ÷100 读作 {shown}pp"
+                    " —— 百分点应直接写数字"]
+        return []
     if unit in _PCT_UNITS:
-        for raw, n in zip(cells, norm):
-            if "bp" in n.lower():
-                raise AssumeFailed(
-                    f"因子「{label}」按变化率（%）计，不认 bp：{raw} —— "
-                    "bp 与 % 不是同一套单位，不做隐式换算，请直接换算"
-                    f"成百分比再填（如 −350bp 请写成 −3.5%）。")
-            # I-a（final-fix-D）：守卫必须复用 `_pct` 自己会用到的那个 token
-            # 来判断，"0.5 %" 这样的空格骗不过去。
-            if not (_pct_token(raw) or "").endswith("%"):
-                raise AssumeFailed(
-                    f"因子「{label}」按变化率计，必须带 %：{raw} —— "
-                    "裸数会被当成小数直接使用而不是百分比（0.5 会被"
-                    "解释成 50%，而不是你想表达的 0.5%），且这类错误"
-                    "合成后看起来仍然合理，人从清单上发现不了。"
-                    "请直接填百分比，如 −3.5%。")
-        return
-    for raw, n in zip(cells, norm):
-        if "%" in n or "bp" in n.lower():
-            raise AssumeFailed(
-                f"因子「{label}」的单位是「{unit}」，按原数填，不要带 % 或 bp："
-                f"{raw}")
+        bare = [c for c in cells if not (_pct_token(c) or "").endswith("%")]
+        if bare:
+            return [f"{where}单位是{unit}，却写了裸数（{' / '.join(bare)}），"
+                    "按小数读（0.2 读作 20%）"]
+        return []
+    if unit and any("%" in n for n in norm):
+        return [f"{where}单位是「{unit}」，却写了 %（{raw}），按 ÷100 读作 {shown}"]
+    return []
+
+
+def _cells(raw: str) -> list[str]:
+    """一行按 `|` 切段。**第 6 段起全部算依据栏**（理由 + 依据）：生益科技
+    2026-10-15 第 3 次被拒，模型写了 7 段（理由一段、`[[key]]` 一段）——因为
+    第 1 次驳回信息让它「在第 7 字段填偏离说明」，而解析器只读第 6 段，真正的
+    依据被丢掉、判为「缺少可核验的依据」。"""
+    parts = [p.strip() for p in raw.split("|")]
+    if len(parts) > 6:
+        parts = parts[:5] + [" | ".join(parts[5:])]
+    return parts
+
+
+def _year(raw: str, years: int, what: str, notes: list[str]) -> int | None:
+    try:
+        y = int(re.search(r"\d+", raw).group(0))
+    except (AttributeError, ValueError):
+        notes.append(f"{what}的年份「{raw}」读不出，这一行没读进来")
+        return None
+    if not 1 <= y <= years:
+        notes.append(f"{what}第 {y} 年超出预测期 1..{years}，这一行没读进来")
+        return None
+    return y
+
+
+def _three(cells: list[str], where: str, notes: list[str]) -> list[Decimal] | None:
+    try:
+        return [_pct(c) for c in cells]
+    except AssumeFailed:
+        notes.append(f"{where}的三档「{' / '.join(cells)}」有读不出数的，这一行没读进来")
+        return None
+
+
+def _put(out: "AssumptionSet", row: "Row", key: tuple, what: str) -> None:
+    """同一项同一年出现多行时**按最后一行读**，并记下这件事。重复行不能留在
+    `rows` 里：`path()` 按年份排序后整体错位一格（C-a），清单显示的值也会与参与
+    合成的值不是同一行。"""
+    for i, r in enumerate(out.rows):
+        if (r.factor_key or r.driver, r.year) == key:
+            out.read_notes.append(f"{what}第 {row.year} 年出现了不止一行，按最后一行读")
+            del out.rows[i]
+            break
+    out.rows.append(row)
 
 
 def _parse_scalar(text: str, years: int) -> AssumptionSet:
+    """两标量模式（取不到申万行业分类时）的读数。**只读，不驳回**：读不出的
+    行跳过并记进 `read_notes`，由审核者判断（子项目三）。"""
     out = AssumptionSet(code="", labels=[])
-    # C-a（final-fix-D）：`_parse_factors` 早就查了 (factor_key, year)
-    # 重复，这条降级路径漏了同一道门——而它恰恰是因子模式连续 3 次被
-    # 「请修正后重新输出全部行」驳回之后才会走到的路径，模型此时已经
-    # 被连续训练成输出"修正前/修正后"两版。比因子模式更糟：`path()`
-    # 把全部行按年份排序后直接返回一个列表，一条重复行会让它后面
-    # 每一年整体错位一格（`ashare_build.py` 按下标 `i` 取值，不是按
-    # 年份查找），人批准的某一年会被静默丢弃、多出的一项顶替了别的
-    # 年份。降级模式不删，那它就必须和主路径一样硬。
-    seen: set[tuple[str, int]] = set()
     for m in _LINE.finditer(text):
-        parts = [p.strip() for p in m.group(1).split("|")]
+        parts = _cells(m.group(1))
         if len(parts) < 6:
-            raise AssumeFailed(
-                f"假设一行字段不足（需 6 段：项目|年|悲观|中性|乐观|依据），"
-                f"得到 {len(parts)}：{m.group(1)}")
+            out.read_notes.append(
+                f"一行字段不足 6 段（项目|年|悲观|中性|乐观|依据），没读进来：{m.group(1)}")
+            continue
         cn = pick(parts[0], list(DRIVERS))
         if cn is None:
-            raise AssumeFailed(
-                f"未知的判断项 {parts[0]!r}，只能填：{'、'.join(DRIVERS)}。"
-                "费用率、周转天数、折旧率等由历史数据校准，不在此处填写。")
-        try:
-            year = int(re.search(r"\d+", parts[1]).group(0))
-        except (AttributeError, ValueError) as e:
-            raise AssumeFailed(f"年份不可解析：{parts[1]!r}") from e
-        if not 1 <= year <= years:
-            raise AssumeFailed(f"年份 {year} 超出预测期 1..{years}")
-        if (DRIVERS[cn], year) in seen:
-            raise AssumeFailed(
-                f"{cn} 第 {year} 年出现了不止一行——重试提示词里的「请"
-                "修正后重新输出全部行」很容易诱发模型把修正前/修正后"
-                "两版都留在同一次回复里。重复行会让 path() 按年份排序"
-                "后整体错位一格，后面每一年都会取到前一行的数字，请只"
-                "保留一行，不要让代码替你决定该采信哪一行。")
-        seen.add((DRIVERS[cn], year))
-        # Minor-5（final-fix-D）：与因子侧同形的窄口径守卫——不要求
-        # 必须带 %，小数记法（"0.02" = 2%）是这条路径本就认可的既有
-        # 约定（见 test_百分号与小数都认），营收增速/毛利率本身没有
-        # 像因子那样的 pp/% 双单位歧义，谈不上"必须带 %"。但 bp 是
-        # 另一套单位，混进来会被 `_pct` 当裸数直接吞下（"-350bp" 被
-        # 解析成 -350，渲染成 -35000%），必须显式拒绝。
-        for label, i in (("悲观", 2), ("中性", 3), ("乐观", 4)):
-            raw = parts[i]
-            if "bp" in raw.replace("％", "%").lower():
-                raise AssumeFailed(
-                    f"{cn} 第 {year} 年 {label}档 {raw!r} 按小数或百分比"
-                    "计，不认 bp —— bp 与 %/小数不是同一套单位，不做"
-                    "隐式换算，请换算成百分比或小数再填。")
-        out.rows.append(Row(
-            driver=DRIVERS[cn], year=year,
-            bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
-            bases=_basis(parts[5], cn), note=_note(parts[5])))
+            out.read_notes.append(
+                f"未知的判断项 {parts[0]!r}（只能填：{'、'.join(DRIVERS)}；费用率、"
+                "周转天数、折旧率等由历史数据校准，不在此处填写），这一行没读进来")
+            continue
+        year = _year(parts[1], years, f"{cn} ", out.read_notes)
+        if year is None:
+            continue
+        where = f"{cn} 第 {year} 年"
+        vals = _three(parts[2:5], where, out.read_notes)
+        if vals is None:
+            continue
+        # 营收增速/毛利率本身没有 pp/% 双单位歧义，小数与百分比都认；只报 bp。
+        out.read_notes += _unit_notes("", where, parts[2:5])
+        bases, prob = _basis(parts[5])
+        if prob:
+            out.read_notes.append(f"{where}：{prob}")
+        _put(out, Row(driver=DRIVERS[cn], year=year, bear=vals[0], base=vals[1],
+                      bull=vals[2], bases=bases, note=_note(parts[5]),
+                      raw=tuple(parts[2:5])),
+             (DRIVERS[cn], year), f"{cn} ")
     return out
 
 
@@ -830,12 +861,17 @@ def _parse_factors(text: str, years: int,
                    decomps,
                    factor_data: dict | None = None,
                    as_of: date | None = None) -> AssumptionSet:
-    """按因子解析。每一行对应分解式里的一个因子，而不是营收增速/毛利率
+    """按因子读数。每一行对应分解式里的一个因子，而不是营收增速/毛利率
     本身 —— 合成交给 `synthesize`，模型只负责填因子。
 
     `decomps` 是旧分解式列表或 `FormulaModel`。因子按**短名或完整名**认（公式化
     后提示词让模型写短名）。另认 `基期: <短名> | <值> | <依据>` 行：需要第 0 年
     取值、而账本里没有的因子由 AI 补填（spec 2026-10-14 三）。
+
+    **只读，不驳回**（子项目三，2026-10-15）：字段不足、因子名认不出、年份越界、
+    数读不出的行跳过；重复行按最后一行；单位写法、依据写法的问题照读。全部记进
+    `read_notes`，作为事实交给审核者。生益科技第三次调用就是被这里的闸门连挂
+    三次、整个因子分解退回两标量的。
     """
     factor_data = factor_data or {}
     model = as_model(decomps)
@@ -851,61 +887,65 @@ def _parse_factors(text: str, years: int,
     out = AssumptionSet(code="", labels=[],
                         decomps=list(decomps) if isinstance(decomps, list) else [],
                         model=model)
-    # C2：同一因子同一年出现两行，重复行本身说明模型在一次回复里生成了
-    # 「修正前/修正后」两版，应当直接驳回重填，而不是替它决定该采信哪一行。
-    seen: set[tuple[str, int]] = set()
-    # 「同上」出口③按因子回指同一个 `factor_key` 之前处理过的那一行。
+    # 「同上」按因子回指同一个 `factor_key` 之前读进来的那一行。
     last_basis: dict[str, list[Basis]] = {}
     for m in _LINE.finditer(text):
-        parts = [p.strip() for p in m.group(1).split("|")]
+        parts = _cells(m.group(1))
         if len(parts) < 6:
-            raise AssumeFailed(
-                f"假设一行字段不足（需 6 段：因子|年|悲观|中性|乐观|依据），"
-                f"得到 {len(parts)}：{m.group(1)}")
+            out.read_notes.append(
+                f"一行字段不足 6 段（因子|年|悲观|中性|乐观|依据），没读进来：{m.group(1)}")
+            continue
         hit = pick(parts[0], list(names))
         if hit is None:
-            raise AssumeFailed(
-                f"未知的因子 {parts[0]!r}，本次分解式里只有："
-                + "、".join(f.short or f.label for f in model.factors))
+            out.read_notes.append(
+                f"未知的因子 {parts[0]!r}（本次分解式里只有："
+                + "、".join(f.short or f.label for f in model.factors) + "），这一行没读进来")
+            continue
         f = names[hit]
-        label = f.label
-        try:
-            year = int(re.search(r"\d+", parts[1]).group(0))
-        except (AttributeError, ValueError) as e:
-            raise AssumeFailed(f"年份不可解析：{parts[1]!r}") from e
-        if not 1 <= year <= years:
-            raise AssumeFailed(f"年份 {year} 超出预测期 1..{years}")
-        if (f.key, year) in seen:
-            raise AssumeFailed(
-                f"因子「{label}」第 {year} 年出现了不止一行——重试提示词"
-                "里的「请修正后重新输出全部行」很容易诱发模型把修正前/"
-                "修正后两版都留在同一次回复里。重复行本身说明这个因子还"
-                "没想清楚，请只保留一行，不要让代码替你决定该采信哪一行。")
-        seen.add((f.key, year))
-        _unit_guard(f.unit, label, parts[2:5])
-        bases = _factor_basis(parts[5], label, factor_data.get(f.key),
-                             as_of, prior=last_basis.get(f.key))
+        year = _year(parts[1], years, f"因子「{f.label}」", out.read_notes)
+        if year is None:
+            continue
+        where = f"因子「{f.label}」第 {year} 年"
+        vals = _three(parts[2:5], where, out.read_notes)
+        if vals is None:
+            continue
+        out.read_notes += _unit_notes(f.unit, where, parts[2:5])
+        bases, prob = _factor_basis(parts[5], factor_data.get(f.key), as_of,
+                                    prior=last_basis.get(f.key))
+        if prob:
+            out.read_notes.append(f"{where}：{prob}")
         last_basis[f.key] = bases
-        out.rows.append(Row(
+        _put(out, Row(
             driver=target_of.get(f.key, "factor"), year=year,
-            bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
+            bear=vals[0], base=vals[1], bull=vals[2],
             bases=bases, note=_note(parts[5]), factor_key=f.key,
-            basis_flag=_conclusion_flags(bases, factor_data.get(f.key))))
+            basis_flag=_conclusion_flags(bases, factor_data.get(f.key)),
+            raw=tuple(parts[2:5])), (f.key, year), f"因子「{f.label}」")
     for m in _BASE_LINE.finditer(text):
-        parts = [p.strip() for p in m.group(1).split("|")]
+        parts = _cells(m.group(1))
         if len(parts) < 3:
-            raise AssumeFailed(
-                f"基期一行字段不足（需 3 段：因子|取值|依据），得到：{m.group(1)}")
+            out.read_notes.append(
+                f"基期一行字段不足 3 段（因子|取值|依据），没读进来：{m.group(1)}")
+            continue
         hit = pick(parts[0], list(names))
         if hit is None:
-            raise AssumeFailed(f"基期行里未知的因子 {parts[0]!r}")
+            out.read_notes.append(f"基期行里未知的因子 {parts[0]!r}，没读进来")
+            continue
         f = names[hit]
+        where = f"因子「{f.label}」基期"
+        try:
+            value = _pct(parts[1])
+        except AssumeFailed:
+            out.read_notes.append(f"{where}「{parts[1]}」读不出数，没读进来")
+            continue
         if f.short in out.base_values:
-            raise AssumeFailed(f"因子「{f.label}」的基期出现了不止一行，只保留一行。")
-        _unit_guard(f.unit, f.label, [parts[1]])
-        bases = _factor_basis(parts[2], f.label, factor_data.get(f.key), as_of)
+            out.read_notes.append(f"{where}出现了不止一行，按最后一行读")
+        out.read_notes += _unit_notes(f.unit, where, [parts[1]])
+        bases, prob = _factor_basis(" | ".join(parts[2:]), factor_data.get(f.key), as_of)
+        if prob:
+            out.read_notes.append(f"{where}：{prob}")
         out.base_values[f.short] = BaseValue(
-            value=_pct(parts[1]), bases=bases, note=_note(parts[2]), source="AI")
+            value=value, bases=bases, note=_note(" | ".join(parts[2:])), source="AI")
     return out
 
 
@@ -1030,24 +1070,26 @@ def _note(raw: str) -> str:
     """第 6 字段里的散文部分 —— 去掉引文与占位符后剩下的理由。"""
     t = _REF.sub("", raw)
     t = _QUOTE.sub("", t)
-    return re.sub(r"\s+", " ", t).strip(" ，,。;；")
+    return re.sub(r"\s+", " ", t).strip(" ，,。;；|")
 
 
-def _basis(raw: str, what: str) -> list[Basis]:
+def _basis(raw: str) -> tuple[list[Basis], str]:
+    """两标量模式的依据：账本占位符或正文原话。返回 (依据, 问题)；问题非空时
+    依据为空 —— 只报事实，由审核者判断（子项目三）。"""
     raw = raw.strip()
     ref = _REF.search(raw)
     if ref:
         try:
-            return [Basis.fact(ref.group(0))]
+            return [Basis.fact(ref.group(0))], ""
         except InvalidBasis as e:
-            raise AssumeFailed(f"{what} 的占位符不合法: {e}") from e
+            return [], f"占位符不合法：{e}"
     q = _QUOTE.search(raw)
     if not q:
-        raise AssumeFailed(f"{what} 缺少可核验的依据（需正文原话或占位符）")
+        return [], "缺少可核验的依据（没有正文原话或占位符）"
     try:
-        return [Basis.report(q.group(1))]
+        return [Basis.report(q.group(1))], ""
     except InvalidBasis as e:
-        raise AssumeFailed(f"{what} 的依据不合法: {e}") from e
+        return [], f"依据不合法：{e}"
 
 
 # 引文核验的规范化从 sources/external.py 导入——「同一套规范化」此前
@@ -1141,10 +1183,13 @@ def _match_excerpt(fd: FactorData | None, quote: str) -> bool:
     return bool(qn) and bool(fd.source_excerpt) and qn in _norm(fd.source_excerpt)
 
 
-def _factor_basis(raw: str, label: str, fd: FactorData | None,
+def _factor_basis(raw: str, fd: FactorData | None,
                   as_of: date | None,
-                  prior: list[Basis] | None = None) -> list[Basis]:
-    """因子模式下依据的合法写法——比两标量模式（`_basis`）多三条出口。
+                  prior: list[Basis] | None = None) -> tuple[list[Basis], str]:
+    """因子模式下依据的写法——比两标量模式（`_basis`）多三条出口。
+
+    **返回 (依据, 问题)，不驳回**（子项目三，2026-10-15）：下面三条出口的边界
+    原样保留，越界不再抛错，而是把问题作为事实交给审核者 [依据] 条目判断。
 
     **这两条出口要修的契约缺口**：真实联网跑出的分解式常有一多半因子
     正文一次都没提过（600584 实测：9 因子 7 个正文零命中），此前只认
@@ -1221,9 +1266,9 @@ def _factor_basis(raw: str, label: str, fd: FactorData | None,
     ref = _REF.search(raw)
     if ref:
         try:
-            return [Basis.fact(ref.group(0))]
+            return [Basis.fact(ref.group(0))], ""
         except InvalidBasis as e:
-            raise AssumeFailed(f"{label} 的占位符不合法: {e}") from e
+            return [], f"占位符不合法：{e}"
 
     q = _QUOTE.search(raw)
     if q:
@@ -1231,46 +1276,33 @@ def _factor_basis(raw: str, label: str, fd: FactorData | None,
         finding = _match_finding(fd, quote)
         if finding is not None:
             return [Basis.external(url=finding.url, quote=quote.strip(),
-                                   retrieved=as_of)]
+                                   retrieved=as_of)], ""
         if _match_excerpt(fd, quote):
             if not fd.source_url:
-                # 内部一致性错误，不是模型的问题——gaps.collect 命中
-                # ②③时理应同时记下 source_url（见该函数），走到这里
-                # 说明采集与解析这两处对同一份 factor_data 的假设不一致。
-                raise AssumeFailed(
-                    f"{label} 引用的年报/研报片段缺失真实出处"
-                    "（factor_data 内部不一致：source_url 为空）——"
-                    "请检查 gaps.collect 是否遗漏了 source_url。")
+                # 内部一致性错误，不是模型的问题——gaps.collect 命中②③时理应
+                # 同时记下 source_url。如实报告，按正文引用处理。
+                return [], ("引用的年报/研报片段缺失真实出处（factor_data 内部"
+                            "不一致：source_url 为空，代码问题）")
             return [Basis.external(url=fd.source_url, quote=quote.strip(),
-                                   retrieved=fd.source_retrieved or as_of)]
+                                   retrieved=fd.source_retrieved or as_of)], ""
         try:
-            return [Basis.report(quote)]
+            return [Basis.report(quote)], ""
         except InvalidBasis as e:
-            raise AssumeFailed(f"{label} 的依据不合法: {e}") from e
+            return [], f"依据不合法：{e}"
 
     if _looks_like_estimate(raw):
         if fd is None or fd.status == gaps.MISSING:
-            return []
-        raise AssumeFailed(
-            f"{label} 写了「无数据（估计）」，但该因子的采集状态是"
-            f"「{fd.status}」，不是「{gaps.MISSING}」——「无数据」只对"
-            "确实没有数据支撑的因子开放，不能图省事对有数据的因子也这么"
-            "写，那会让这条出口变成谁都能用的万能逃生口。")
+            return [], ""
+        return [], (f"写了「无数据（估计）」，但该因子的采集状态是「{fd.status}」，"
+                    f"不是「{gaps.MISSING}」")
 
     if _looks_like_ditto(raw):
         if prior is None:
-            raise AssumeFailed(
-                f"{label} 写了「同上」，但这是该因子出现的第一行，"
-                "没有上一行可以回指——「同上」指的是同一因子（而不是"
-                "文本里紧邻的上一行，那可能是别的因子）上一行的依据，"
-                "第一次出现该因子时必须给出完整依据（正文原话、占位符、"
-                "采集到的证据引文，或「无数据（估计）」）。")
-        return list(prior)
+            return [], "写了「同上」，但这是该因子读进来的第一行，没有上一行可回指"
+        return list(prior), ""
 
-    raise AssumeFailed(
-        f"{label} 缺少可核验的依据（需正文原话、账本占位符、该因子自身"
-        "采集到的证据引文、对确实无数据的因子明说「无数据（估计）」，"
-        "或对同一因子上一行的依据回指「同上」）")
+    return [], ("缺少可核验的依据（没有正文原话、账本占位符、该因子采集到的证据"
+                "引文、「无数据（估计）」或「同上」）")
 
 
 # 隐含份额的阻断阈值。
diff --git a/ir_agent/stance.py b/ir_agent/stance.py
index 5c1374c..ae8ab83 100644
--- a/ir_agent/stance.py
+++ b/ir_agent/stance.py
@@ -102,7 +102,7 @@ def parse_evidence(raw: str, *, corpus=None,
     列表，而不是抛异常；不给（单元测试的直接调用）则照旧抛 —— 让格式错误在
     测试里可见。整份输出一条可用证据都没有时，无论如何都失败。
     """
-    from ir_agent.assume import AssumeFailed, _basis
+    from ir_agent.assume import _basis
 
     out: list[Evidence] = []
     for line in raw.splitlines():
@@ -124,9 +124,8 @@ def parse_evidence(raw: str, *, corpus=None,
                 raise StanceFailed(msg, raw=raw)
             dropped.append(f"{msg}：{line[:60]}")
             continue
-        try:
-            bases = _basis(basis_raw, f"「{subject}」的{side}证据")
-        except AssumeFailed:
+        bases, prob = _basis(basis_raw)
+        if prob:
             doc = _cited_doc(basis_raw, corpus)
             if doc is not None:
                 note = (f"引了文档《{doc.title}》但没给逐字原文 —— 文档确实在"
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2413 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `_cells` 里 `if len(parts) > 6:` 改成 `if False:` → `test_七段的行_第六段起全算依据栏` 红
- `_unit_notes` 的 pp 分支直接 `return []` → `test_pp因子写百分号记进读数事实` 红
- `_put` 里去掉 `del out.rows[i]` → 重复行测试红（只按最后一行读）

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(assume): 判断项读数改为宽容读取 —— 读不出、单位与依据写法的问题记成事实，不再驳回

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: check() 只报事实不驳回

**Files:**
- Modify: `ir_agent/assume.py`
- Modify: `ir_agent/full.py`
- Modify: `tests/test_assume.py`
- Modify: `tests/test_assume_formula.py`
- Modify: `tests/test_phase1_seams.py`

**Interfaces:**
- Produces: `assume.Facts(problems: list[str], blocking: list[str])`，`.text() -> str`；`assume.check(...) -> Facts`（不再抛 `AssumeFailed`）；`AssumptionSet.facts`；`_monotonic_problems(a, base_margin, bases) -> list[str]`；`_basis_problems(a, *, ledger, body, as_of, decomps) -> list[str]`

**为什么：** `assume.check` 原先是 49 处 `AssumeFailed` 的另一半：三档拉开、合成单调、依据核验、平衡点偏离（「第 7 字段」）、隐含份额阻断。改为返回 `Facts(problems, blocking)` 并挂在 `a.facts` 上：内容问题进 `problems` 交审核者；缺年份、缺基期、重复行、公式算不动进 `blocking`（真算不下去）。`_check_monotonic` 换成 `_monotonic_problems`，`apply_to` 不再拒绝；`check_deviation` 措辞去掉「第 7 字段」。第二阶段 `run_valuation` 只在 `blocking` 时停。`_decide_once` 暂时只在 `blocking` 时重试（Task 5 整体替换）。

- [ ] **Step 1: 写失败测试**

```diff
diff --git a/tests/test_assume.py b/tests/test_assume.py
index 869cc8e..fa901ac 100644
--- a/tests/test_assume.py
+++ b/tests/test_assume.py
@@ -38,7 +38,7 @@ def _full(years=2):
 
 
 def _ok(a, years=2):
-    assume.check(a, years, ledger=FactLedger(), body=BODY, as_of=AS_OF)
+    return assume.check(a, years, ledger=FactLedger(), body=BODY, as_of=AS_OF)
 
 
 # ── 边界：只填判断项 ─────────────────────────────────────────
@@ -56,22 +56,22 @@ def test_填校准项记进读数事实且说明原因():
 
 # ── 三档必须拉开 ─────────────────────────────────────────────
 
-def test_三档同值被驳回():
+def test_三档同值记进事实():
     """模板默认 Bull=Base=Bear，那等于没有情景 —— 拉不开的情景分析
     只是把同一个数抄三遍。"""
     a = assume.parse("\n".join([
         _line("营收增速", 1, "5%", "5%", "5%"),
         _line("毛利率", 1, "12%", "14%", "16%")]), 1)
-    with pytest.raises(AssumeFailed, match="三档未拉开"):
-        _ok(a, 1)
+    facts = _ok(a, 1)
+    assert re.search("三档未拉开", facts.text()), facts.text()
 
 
-def test_顺序颠倒被驳回():
+def test_顺序颠倒记进事实():
     a = assume.parse("\n".join([
         _line("营收增速", 1, "10%", "6%", "2%"),
         _line("毛利率", 1, "12%", "14%", "16%")]), 1)
-    with pytest.raises(AssumeFailed, match="三档未拉开|严格递增"):
-        _ok(a, 1)
+    facts = _ok(a, 1)
+    assert re.search("三档未拉开|严格递增", facts.text()), facts.text()
 
 
 def test_拉开则通过():
@@ -80,12 +80,12 @@ def test_拉开则通过():
 
 # ── 每一年都要给 ─────────────────────────────────────────────
 
-def test_漏掉某年被驳回():
+def test_漏掉某年记进事实():
     """漏掉的年份会沿用模板默认值，而那不是你的判断。"""
     a = assume.parse("\n".join([_line("营收增速", 1),
                                 _line("毛利率", 1, "12%", "14%", "16%")]), 3)
-    with pytest.raises(AssumeFailed, match="缺第"):
-        _ok(a, 3)
+    facts = _ok(a, 3)
+    assert re.search("缺第", facts.text()), facts.text()
 
 
 def test_年份越界记进读数事实():
@@ -100,12 +100,12 @@ def test_缺依据记进读数事实():
     assert re.search("缺少可核验的依据", _notes(a)), _notes(a)
 
 
-def test_依据查无此句被驳回():
+def test_依据查无此句记进事实():
     a = assume.parse("\n".join([
         _line("营收增速", 1, q="行业景气全面回升"),
         _line("毛利率", 1, "12%", "14%", "16%")]), 1)
-    with pytest.raises(AssumeFailed, match="不可核验"):
-        _ok(a, 1)
+    facts = _ok(a, 1)
+    assert re.search("核验不过", facts.text()), facts.text()
 
 
 # ── 解析容错（与四个定性模块同一套） ─────────────────────────
@@ -235,8 +235,8 @@ def test_读回后仍要过全部校验():
     d = assume.to_json(a)
     d["rows"][0]["bull"] = d["rows"][0]["base"]      # 人手改坏：三档不再拉开
     back, _ = assume.from_json(d)
-    with pytest.raises(AssumeFailed, match="三档未拉开"):
-        _ok(back, 1)
+    facts = _ok(back, 1)
+    assert re.search("三档未拉开", facts.text()), facts.text()
 
 
 def test_人改过的数值要被报出来():
@@ -296,13 +296,13 @@ def test_未知因子记进读数事实并列出可选():
     assert "产能" in _notes(a) and "单价" in _notes(a)
 
 
-def test_缺某个因子被驳回():
+def test_缺某个因子记进事实():
     """漏掉的因子会沿用模板默认值，而那不是你的判断。"""
     partial = f"假设: 产能 | 第1年 | 5% | 10% | 14% | 「{Q}」"
     a = assume.parse(partial, 1, decomps=_decomps())
-    with pytest.raises(AssumeFailed, match="单价|缺"):
-        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
-                     decomps=_decomps())
+    facts = assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
+                 decomps=_decomps())
+    assert re.search("单价|缺", facts.text()), facts.text()
 
 
 def test_合成营收增速与毛利率():
@@ -325,7 +325,7 @@ def test_三档合成后仍然单调():
     assert lo[0] < mid[0] < hi[0]
 
 
-def test_合成后不单调则驳回():
+def test_合成后不单调则记进事实():
     """构造一组因子: 各自三档拉开，但相乘后悲观反而高于中性。"""
     weird = "\n".join([
         f"假设: 产能 | 第1年 | 50% | 10% | 11% | 「{Q}」",   # 悲观档产能暴涨
@@ -335,9 +335,9 @@ def test_合成后不单调则驳回():
         Factor(key="asp", label="单价", unit="变化率")])]
     a = assume.parse(weird, 1, decomps=d)
     a.decomps = d
-    with pytest.raises(AssumeFailed, match="合成后"):
-        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
-                     decomps=d, base_margin=D("0.14"))
+    facts = assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
+                 decomps=d, base_margin=D("0.14"))
+    assert re.search("合成后", facts.text()), facts.text()
 
 
 def test_提示词列出因子与单位():
@@ -877,16 +877,16 @@ def _dup_rows():
     ]
 
 
-def test_check也拒绝重复行不只依赖parse():
+def test_check也记进事实重复行不只依赖parse():
     """C2 纵深防御：`check()` 在人工核实之后的第二阶段（`full.
     run_valuation` 里 `from_json` 读回）会被再跑一遍，那时的
     `AssumptionSet` 未必经过 `_parse_factors`——人手改 JSON 也可能
     引入重复行。`_check_factors` 不能假设上游一定已经查过重。"""
     a = assume.AssumptionSet(code="", labels=["2026E"], decomps=_decomps())
     a.rows = _dup_rows()
-    with pytest.raises(AssumeFailed, match="产能"):
-        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
-                     decomps=_decomps(), base_margin=D("0.1415"))
+    facts = assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
+                 decomps=_decomps(), base_margin=D("0.1415"))
+    assert re.search("产能", facts.text()), facts.text()
 
 
 def test_to_driver_block与synthesize取值规则一致():
@@ -1097,7 +1097,7 @@ def test_两标量模式重复行记进读数事实():
     assert "第 1 年" in _notes(a)
 
 
-def test_两标量模式check也拒绝重复行不只依赖parse():
+def test_两标量模式check也记进事实重复行不只依赖parse():
     """C-a 纵深防御：`check()` 在人工核实之后（`from_json` 读回）会被
     再跑一遍（`full.run_valuation`），那时的 `AssumptionSet` 未必经过
     `_parse_scalar`——人手改 JSON 也可能引入重复行。"""
@@ -1110,8 +1110,8 @@ def test_两标量模式check也拒绝重复行不只依赖parse():
         assume.Row(driver="gm", year=1, bear=D("0.12"), base=D("0.14"),
                   bull=D("0.16")),
     ]
-    with pytest.raises(AssumeFailed, match="营收增速"):
-        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)
+    facts = assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)
+    assert re.search("营收增速", facts.text()), facts.text()
 
 
 def test_两标量模式重复行会让path按年份整体错位():
@@ -1376,7 +1376,7 @@ def test_因子压根没被采集也能用无数据出口():
     assert a.rows[0].bases == []
 
 
-def test_无数据估计没给理由被check挡下():
+def test_无数据估计没给理由记进事实():
     """空依据 + 空理由 = 一个数字凭空出现在清单上，连"这是怎么估出来的"
     都无从判断——`_factor_basis` 只在解析阶段判过一次"这个因子确实
     MISSING"，理由是否给出要在 `check()` 里再挡一道，因为 `check()`
@@ -1387,9 +1387,9 @@ def test_无数据估计没给理由被check挡下():
     a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.05"),
                          base=D("0.08"), bull=D("0.12"),
                          factor_key="utilization", note="")]
-    with pytest.raises(AssumeFailed, match="没有给理由"):
-        assume.check(a, 1, ledger=FactLedger(), body="无关正文", as_of=AS_OF,
-                    decomps=[d])
+    facts = assume.check(a, 1, ledger=FactLedger(), body="无关正文", as_of=AS_OF,
+                decomps=[d])
+    assert re.search("没有给理由", facts.text()), facts.text()
 
 
 def test_全部因子都无数据时check仍能通过():
@@ -1923,7 +1923,7 @@ def test_因子模式引文核验不过被标注而非驳回_600584实测复现(
     assert "产能规模第1年" in txt
 
 
-def test_两标量模式对同款引文场景仍然驳回_回归锁():
+def test_两标量模式对同款引文场景仍记进事实_回归锁():
     """回归锁：同一个真实失败场景（引文「修复但未完全追平」，正文里
     没有这句话）放到两标量降级模式下，必须仍然驳回——这次放宽只针对
     因子行，两标量模式引的是正文，正文本来就覆盖增速/毛利率两项，
@@ -1938,11 +1938,11 @@ def test_两标量模式对同款引文场景仍然驳回_回归锁():
         assume.Row(driver="gm", year=1, bear=D("0.12"), base=D("0.14"),
                   bull=D("0.16"), note="理由", bases=[assume.Basis.report(Q)]),
     ]
-    with pytest.raises(AssumeFailed, match="不可核验"):
-        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)
+    facts = assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)
+    assert re.search("核验不过", facts.text()), facts.text()
 
 
-def test_两标量模式一条依据都没给仍然驳回_全局闸门不能被顺手拆掉():
+def test_两标量模式一条依据都没给仍记进事实_全局闸门不能被顺手拆掉():
     """**回归锁：放宽因子引文，不能把「整批一条依据都没给」这道全局
     闸门一起拆掉。**
 
@@ -1966,11 +1966,11 @@ def test_两标量模式一条依据都没给仍然驳回_全局闸门不能被
         assume.Row(driver="gm", year=1, bear=D("0.12"), base=D("0.14"),
                   bull=D("0.16"), note="理由", bases=[]),
     ]
-    with pytest.raises(AssumeFailed, match="未提供任何依据"):
-        assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)
+    facts = assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF)
+    assert re.search("未提供任何依据", facts.text()), facts.text()
 
 
-def test_因子模式伪造账本占位符依旧驳回():
+def test_因子模式伪造账本占位符仍记进事实():
     """**放宽不能连坐到账本占位符。** `[[key@period]]` 指向账本里根本
     不存在的事实，是对账本的假陈述，且下游会拿占位符去做字符串替换
     ——错了会让整段估值替换失败（实测踩过整段漏成未替换占位符）。这
@@ -1981,12 +1981,12 @@ def test_因子模式伪造账本占位符依旧驳回():
     a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.05"), base=D("0.08"),
                          bull=D("0.12"), note="理由", factor_key="utilization",
                          bases=[assume.Basis.fact("[[revenue.yoy@2099FY]]")])]
-    with pytest.raises(AssumeFailed, match="不可核验"):
-        assume.check(a, 1, ledger=FactLedger(), body="无关正文", as_of=AS_OF,
-                    decomps=[d])
+    facts = assume.check(a, 1, ledger=FactLedger(), body="无关正文", as_of=AS_OF,
+                decomps=[d])
+    assert re.search("核验不过", facts.text()), facts.text()
 
 
-def test_两标量模式的引文驳回不附加因子出路():
+def test_两标量模式的引文记进事实不附加因子出路():
     """回归锁：出口二只存在于因子模式，两标量模式不该看到这段指引
     ——那会让人以为标量假设也能写「无数据（估计）」。"""
     from datetime import date
@@ -2002,10 +2002,9 @@ def test_两标量模式的引文驳回不附加因子出路():
     a.rows = [A.Row(driver="growth", year=1, bear=D("-0.02"), base=D("0.03"),
                     bull=D("0.08"), note="理由",
                     bases=[Basis.report("正文里没有的话")])]
-    with pytest.raises(A.AssumeFailed) as ei:
-        A.check(a, 1, ledger=FactLedger(), body="别的内容",
-                as_of=date(2026, 9, 12))
-    assert "无数据（估计）" not in str(ei.value)
+    facts = A.check(a, 1, ledger=FactLedger(), body="别的内容",
+            as_of=date(2026, 9, 12))
+    assert "无数据（估计）" not in facts.text()
 
 
 def _estimate_note():
@@ -2078,9 +2077,9 @@ def test_读回的判断项要重过分解式闸门():
     a.rows = [assume.Row(driver="revenue", year=1, bear=D("0.01"), base=D("0.05"),
                          bull=D("0.09"), note="理由", factor_key=k)
               for k in ("cap", "mix")]
-    with pytest.raises(AssumeFailed, match="占比"):
-        assume.check(a, 1, ledger=FactLedger(), body="正文", as_of=AS_OF,
-                     decomps=[rev])
+    facts = assume.check(a, 1, ledger=FactLedger(), body="正文", as_of=AS_OF,
+                 decomps=[rev])
+    assert re.search("占比", facts.text()), facts.text()
 
 
 # ── 锚定开启时，毛利率第 2 年起从各档锚定水平接着走 ─────────────────────
@@ -2155,9 +2154,9 @@ def test_合成后单调性按平移后的路径查():
     assume.check(a, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, decomps=ds,
                  base_margin=D("0.2647"))
     a2, ds2 = _two_year_set(bull_gm=((5.0, 5.0), (2.5, 2.5)))
-    with pytest.raises(AssumeFailed, match="毛利率"):
-        assume.check(a2, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, decomps=ds2,
-                     base_margin=D("0.2647"), anchor=_anchor_600183())
+    facts = assume.check(a2, 2, ledger=FactLedger(), body="正文", as_of=AS_OF, decomps=ds2,
+                 base_margin=D("0.2647"), anchor=_anchor_600183())
+    assert re.search("毛利率", facts.text()), facts.text()
 
 
 def test_清单上的合成结果与建模同一套基期():
@@ -2180,12 +2179,17 @@ def test_工作簿三档列与1b表用同一套按年基期():
     assert out["judg"]["gm_scen"]["base"] == [float(x) for x in want]
 
 
-def test_建模时平移后不单调也要拒绝():
-    """`apply_to` 自己要再查一次单调（哪怕调用方已经用同一个 anchor 查过一次），
-    不能让不合格的路径靠"上游肯定查过"这种假设溜进工作簿。"""
-    a, _ = _two_year_set(bull_gm=((5.0, 5.0), (2.5, 2.5)))
-    with pytest.raises(AssumeFailed, match="毛利率"):
-        assume.apply_to(_cal_600183(), a)
+def test_建模时只计算_平移后不单调由第一阶段审核者判():
+    """子项目三：第二阶段只计算，不再拒绝人批准过的判断项（600584 实测：人批准
+    了，建模前一刻又被代码拒绝，人工门被架空）。平移后不单调是第一阶段交给审核者
+    [合成] 条目的事实。"""
+    a, ds = _two_year_set(bull_gm=((5.0, 5.0), (2.5, 2.5)))
+    out = assume.apply_to(_cal_600183(), a)
+    assert out["judg"]["gm_scen"]["bull"], "照常算出三档"
+    a2, ds2 = _two_year_set(bull_gm=((5.0, 5.0), (2.5, 2.5)))
+    facts = assume.check(a2, 2, ledger=FactLedger(), body="正文", as_of=AS_OF,
+                         decomps=ds2, base_margin=D("0.2647"), anchor=_anchor_600183())
+    assert "毛利率合成后不单调" in facts.text()
 
 
 # ── C1：两阶段 check() 必须用同一个 anchor，否则批准的东西下一步就不合法 ──
@@ -2229,9 +2233,9 @@ def test_两阶段用不同anchor会给出相反结论():
 
     # 第二阶段修复前的口径：不带 anchor，查未平移的路径 —— FAIL，
     # 且报出的三档数字与实跑现场一致。
-    with pytest.raises(AssumeFailed, match=r"26\.97%.*29\.57%.*29\.47%"):
-        assume.check(build(), 2, ledger=FactLedger(), body="正文", as_of=AS_OF,
-                    decomps=[gm], base_margin=base_margin)
+    facts = assume.check(build(), 2, ledger=FactLedger(), body="正文", as_of=AS_OF,
+                decomps=[gm], base_margin=base_margin)
+    assert re.search(r"26\.97%.*29\.57%.*29\.47%", facts.text()), facts.text()
 
 
 def test_读回的分解式里过短检索词不会让第二阶段崩成traceback():
@@ -2413,8 +2417,8 @@ def test_隐含份额超限时退回让模型重算():
     for r in a.rows:
         if r.driver == "growth":
             r.bear, r.base, r.bull = D("0.9"), D("1.0"), D("1.1")
-    with pytest.raises(assume.AssumeFailed, match="隐含份额"):
-        assume.check(a, 2, ledger=_ledger_with_share(), body=BODY, as_of=AS_OF)
+    facts = assume.check(a, 2, ledger=_ledger_with_share(), body=BODY, as_of=AS_OF)
+    assert re.search("隐含份额", facts.text()), facts.text()
 
 
 def test_正常路径不被阻断且算术后果印在清单顶部():
@@ -2544,9 +2548,9 @@ class TestDeviation:
         for r in a.rows:
             if r.driver == "growth":
                 r.bear, r.bull = D("0.10"), D("0.26")
-        with pytest.raises(assume.AssumeFailed, match="偏离说明"):
-            assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
-                         balances=self._bal())
+        facts = assume.check(a, 1, ledger=FactLedger(), body=BODY, as_of=AS_OF,
+                     balances=self._bal())
+        assert re.search("偏离多空平衡点", facts.text()), facts.text()
 
     def test_不传balances时check不受影响(self):
         """这条约束只在有平衡点时生效 —— 老的调用点一个都不该因此变红。"""
diff --git a/tests/test_assume_formula.py b/tests/test_assume_formula.py
index 00963c1..24e36bd 100644
--- a/tests/test_assume_formula.py
+++ b/tests/test_assume_formula.py
@@ -76,7 +76,7 @@ def _ledger(asp="9.5"):
 
 
 def _check(a, years, model, ledger=None, base_margin=D("0.25")):
-    assume.check(a, years, ledger=ledger or _ledger(), body=f"正文 {Q} 依据原文",
+    return assume.check(a, years, ledger=ledger or _ledger(), body=f"正文 {Q} 依据原文",
                  as_of=AS_OF, decomps=model, base_margin=base_margin)
 
 
@@ -112,10 +112,10 @@ class TestLevelMode:
         assert a.base_values["价"].value == D("9.5")
         assert a.base_values["价"].bases[0].ref == "[[ccl.asp@2025FY]]"
 
-    def test_缺基期明确驳回(self):
+    def test_缺基期明确记进事实(self):
         a = assume.parse(_level_lines(base=False), 1, decomps=LEVEL)
-        with pytest.raises(AssumeFailed, match="需要第 0 年.*量.*本"):
-            _check(a, 1, LEVEL)
+        facts = _check(a, 1, LEVEL)
+        assert re.search("需要第 0 年.*量.*本", facts.text()), facts.text()
 
     def test_口径不混用_第0年由基期因子代入公式(self):
         """营收₀ = 100 × 9.5 = 950，与账本营收 20 亿无关。"""
@@ -125,12 +125,12 @@ class TestLevelMode:
         assert g == [D(110) * D(10) / (D(100) * D("9.5")) - 1]
         assert m == [(D(10) - D("7.4")) / D(10)]
 
-    def test_合成后三档不拉开被驳回(self):
+    def test_合成后三档不拉开记进事实(self):
         txt = _level_lines().replace("假设: 价 | 第1年 | 9 | 10 | 11 |",
                                      "假设: 价 | 第1年 | 11 | 10 | 9 |")
         a = assume.parse(txt, 1, decomps=LEVEL)
-        with pytest.raises(AssumeFailed, match="合成后"):
-            _check(a, 1, LEVEL)
+        facts = _check(a, 1, LEVEL)
+        assert re.search("合成后", facts.text()), facts.text()
 
 
 class TestChangeMode:
@@ -234,8 +234,8 @@ class TestPhase2:
         _check(back, 1, back._m())                       # 走因子路径，通过
         # 改动前的写法：退回两标量校验，把因子行当成营收增速/毛利率行去查
         # （`path()` 是两标量路径专用的措辞）。
-        with pytest.raises(AssumeFailed, match=r"path\(\)"):
-            _check(back, 1, back.decomps or None)
+        facts = _check(back, 1, back.decomps or None)
+        assert re.search("缺第", facts.text()), facts.text()
 
 
 def test_判断项清单顶部有方法论审核遗留():
diff --git a/tests/test_phase1_seams.py b/tests/test_phase1_seams.py
index 9073aac..05879ea 100644
--- a/tests/test_phase1_seams.py
+++ b/tests/test_phase1_seams.py
@@ -16,6 +16,7 @@
 from __future__ import annotations
 
 import json
+import re
 from datetime import date, timedelta
 from decimal import Decimal as D
 
@@ -100,15 +101,15 @@ class TestSeamStanceToAssume:
     而双方各自的单元测试都是绿的。
     """
 
-    def test_真实平衡点能让偏离的判断项被驳回(self):
+    def test_真实平衡点能让偏离的判断项记进事实(self):
         res, led = _run_b("平衡｜产能｜0.05｜0.20｜0.30｜中性取产能爬坡更快")
         assert res.balances and res.balances[0].subject == "产能"
 
         a = assume.parse(_factor_lines(), 1, decomps=_decomps())
         # 产能 中性档 10%，平衡点 20% —— 偏离 10pp 且未填 deviation
-        with pytest.raises(assume.AssumeFailed, match="偏离说明"):
-            assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
-                         decomps=_decomps(), balances=res.balances)
+        facts = assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
+                     decomps=_decomps(), balances=res.balances)
+        assert re.search("偏离多空平衡点", facts.text()), facts.text()
 
     def test_填了偏离说明就放行(self):
         res, led = _run_b("平衡｜产能｜0.05｜0.20｜0.30｜中性取产能爬坡更快")
@@ -150,7 +151,7 @@ class TestSeamStanceThroughDisk:
     `stance_b.json` 一落盘就再也读不回来。
     """
 
-    def test_落盘读回后平衡点仍能驳回偏离的判断项(self, tmp_path):
+    def test_落盘读回后平衡点仍能记进事实偏离的判断项(self, tmp_path):
         res, led = _run_b("平衡｜产能｜0.05｜0.20｜0.30｜理由")
         f = tmp_path / "stance_b.json"
         f.write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=2),
@@ -162,9 +163,9 @@ class TestSeamStanceThroughDisk:
         assert len(back.evidence) == 2, "两侧证据都要读回来"
 
         a = assume.parse(_factor_lines(), 1, decomps=_decomps())
-        with pytest.raises(assume.AssumeFailed, match="偏离说明"):
-            assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
-                         decomps=_decomps(), balances=back.balances)
+        facts = assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
+                     decomps=_decomps(), balances=back.balances)
+        assert re.search("偏离多空平衡点", facts.text()), facts.text()
 
     def test_人工补写的模板填好后走同一条路(self, tmp_path):
         """`--accept-manual` 的真实路径：模板 → 人填 → load_manual → 校验。"""
@@ -178,9 +179,9 @@ class TestSeamStanceThroughDisk:
         assert back.manual == "xhl" and "人工补写" in back.summary()
 
         a = assume.parse(_factor_lines(), 1, decomps=_decomps())
-        with pytest.raises(assume.AssumeFailed, match="偏离说明"):
-            assume.check(a, 1, ledger=_led(), body=BODY, as_of=AS_OF,
-                         decomps=_decomps(), balances=back.balances)
+        facts = assume.check(a, 1, ledger=_led(), body=BODY, as_of=AS_OF,
+                     decomps=_decomps(), balances=back.balances)
+        assert re.search("偏离多空平衡点", facts.text()), facts.text()
 
 
 # ── 接缝 C / D / E：公告入库 → 门①清单 / 因子检索 / 定性提示词 ──
```

- [ ] **Step 2: 跑测试，确认红**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`26 failed, 2387 passed, 8 skipped`。失败的是：

- `tests/test_assume.py::test_三档同值记进事实`
- `tests/test_assume.py::test_顺序颠倒记进事实`
- `tests/test_assume.py::test_漏掉某年记进事实`
- `tests/test_assume.py::test_依据查无此句记进事实`
- `tests/test_assume.py::test_读回后仍要过全部校验`
- `tests/test_assume.py::test_缺某个因子记进事实`
- `tests/test_assume.py::test_合成后不单调则记进事实`
- `tests/test_assume.py::test_check也记进事实重复行不只依赖parse`
- `tests/test_assume.py::test_两标量模式check也记进事实重复行不只依赖parse`
- `tests/test_assume.py::test_无数据估计没给理由记进事实`
- `tests/test_assume.py::test_两标量模式对同款引文场景仍记进事实_回归锁`
- `tests/test_assume.py::test_两标量模式一条依据都没给仍记进事实_全局闸门不能被顺手拆掉`
- `tests/test_assume.py::test_因子模式伪造账本占位符仍记进事实`
- `tests/test_assume.py::test_两标量模式的引文记进事实不附加因子出路`
- `tests/test_assume.py::test_读回的判断项要重过分解式闸门`
- `tests/test_assume.py::test_合成后单调性按平移后的路径查`
- `tests/test_assume.py::test_建模时只计算_平移后不单调由第一阶段审核者判`
- `tests/test_assume.py::test_两阶段用不同anchor会给出相反结论`
- `tests/test_assume.py::test_隐含份额超限时退回让模型重算`
- `tests/test_assume.py::TestDeviation::test_这条检查真的被接进了check`
- `tests/test_assume_formula.py::TestLevelMode::test_缺基期明确记进事实`
- `tests/test_assume_formula.py::TestLevelMode::test_合成后三档不拉开记进事实`
- `tests/test_assume_formula.py::TestPhase2::test_只有model时校验仍走因子路径`
- `tests/test_phase1_seams.py::TestSeamStanceToAssume::test_真实平衡点能让偏离的判断项记进事实`
- `tests/test_phase1_seams.py::TestSeamStanceThroughDisk::test_落盘读回后平衡点仍能记进事实偏离的判断项`
- `tests/test_phase1_seams.py::TestSeamStanceThroughDisk::test_人工补写的模板填好后走同一条路`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index c2ed9a1..b86cdb6 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -335,6 +335,8 @@ class AssumptionSet:
     # 算出并挂在这里，`describe()` 原样印在清单顶部。**算的人和显示的人用
     # 同一个对象**，否则清单上的数和校验用的数会分叉。
     implied: object = None
+    # 代码读数、计算、核对的事实（`Facts`），由 check() 挂上。派生量，不进 JSON。
+    facts: object = None
     # 因子结构审查结果（`factor_review.ReviewResult`）。派生量，不进 JSON。
     # 它**不是闸门**：只给结论与理由，印在清单上给批准人看。
     review: object = None
@@ -1457,7 +1459,10 @@ DEVIATION_TOL = Decimal("0.01")
 
 def check_deviation(a: AssumptionSet, balances: list,
                     tol: Decimal = DEVIATION_TOL) -> list[str]:
-    """判断项偏离平衡点时，该行必须填 `deviation`。返回问题列表。
+    """判断项偏离平衡点的行。返回事实列表，**不驳回**（子项目三）：此前这里
+    要求「第 7 字段」写偏离说明，而解析器从不读第 7 段 —— 规则实际变成「中性档
+    必须等于平衡点」，生益科技 2026-10-15 的中性档被钉在一个单位写错的平衡点
+    （pp 因子写成小数 0.035）上。现在偏离只是交给审核者 [平衡点] 条目的事实。
 
     **只查存在性。** 填得对不对由结构审查对账 —— 代码能确定判断的是「有没有
     填」，不是「这个理由站不站得住」。实测教训: 隐含份额检查想用算术判断一件
@@ -1487,155 +1492,125 @@ def check_deviation(a: AssumptionSet, balances: list,
             continue
         out.append(
             f"第 {r.year} 年「{name}」的中性档 {r.base} 偏离多空平衡点 "
-            f"{mid}（容差 {tol}），但没有给出偏离说明 —— 请在该行的第 7 字段"
-            f"填一句话说明为什么偏离；若采纳平衡点，把取值改成 {mid}。")
+            f"{mid}（容差 {tol}）—— 理由栏是否说明了为什么偏离、平衡点本身的"
+            "单位是否可信，由审核者判断")
     return out
 
 
+@dataclass
+class Facts:
+    """代码对一份判断项读数、计算、核对的结果。**只是事实，不驳回**（子项目三，
+    2026-10-15）：此前这里是 49 处 `AssumeFailed`，合格与否全由代码定；生益科技
+    第三次调用被其中三条连挂三次、整个因子分解退回两标量。现在合格与否全部由
+    判断项审核者定，代码只说它读到了什么、算出了什么、核对出了什么。
+
+    - `problems`：交给审核者的问题（单位写法、依据核验、三档、平衡点、隐含份额……）；
+    - `blocking`：真的算不下去的地方（缺年份、缺基期、重复行、公式算不动）。
+      审核者判通过、这里却非空 → 停下交人工，写明算不下去在哪（spec 一）。
+    """
+
+    problems: list[str] = field(default_factory=list)
+    blocking: list[str] = field(default_factory=list)
+
+    def text(self) -> str:
+        out = []
+        if self.blocking:
+            out += ["**算不下去的地方**："] + [f"- {x}" for x in self.blocking] + [""]
+        if self.problems:
+            out += ["**代码读数与核对发现的问题**（只是事实，是否要紧由你判断）："]
+            out += [f"- {x}" for x in self.problems]
+        return "\n".join(out) or "（代码读数与核对没有发现问题）"
+
+
 def check(a: AssumptionSet, years: int, *, ledger: FactLedger, body: str,
           as_of: date, decomps: list[Decomposition] | None = None,
           base_margin: Decimal | None = None, anchor: dict | None = None,
-          balances: list | None = None) -> None:
+          balances: list | None = None) -> Facts:
+    """读数之后的计算与核对。**不驳回**，返回 `Facts`（并挂在 `a.facts` 上）。
+
+    做的事与此前相同 —— 取账本基期、算锚定平移、合成三档、算隐含份额、核验
+    依据、比对平衡点 —— 只是结论从「抛错退回」变成「记成事实」。第二阶段
+    `run_valuation` 也调它：那里只看 `blocking`（算不下去才停），不看 `problems`。
+    """
+    facts = Facts(problems=list(a.read_notes))
     model = as_model(decomps)
     if model is not None:
         a.model = model
         if a.base_margin is None:
             a.base_margin = base_margin
         _fill_from_ledger(a, ledger, as_of)
-        _check_factors(a, years, decomps, base_margin, anchor)
+        _check_factors(a, years, decomps, base_margin, anchor, facts)
     else:
-        _check_scalar(a, years)
-    # 多空平衡点的偏离说明。**接在隐含份额之前** —— 偏离说明缺失是格式问题，
-    # 隐含份额越界是实质问题，先让模型把格式补齐，驳回信息才不会混在一起。
+        _check_scalar(a, years, facts)
     if balances:
-        if (probs := check_deviation(a, balances)):
-            raise AssumeFailed("；".join(probs))
-    # **增速路径的算术后果。** 阻断的含义是退回让模型重算（本函数抛
-    # AssumeFailed 会进 `_decide_once` 的重试循环并把理由附进提示词），
-    # 不是毙掉整轮 —— 连续失败后 `decide()` 仍会降级到两标量模式。
-    a.implied = _implied_for(a, ledger, as_of, decomps)
-    if a.implied is not None and a.implied.blocking:
-        raise AssumeFailed(
-            f"中性档营收路径的算术后果不成立：{a.implied.reason}。"
-            f"（{a.implied.describe()}）"
-            "请下调各因子的中性档取值，使隐含份额落在可能的范围内；"
-            "若分解式本身把同一个量算了两遍（例如销量×售价之外又乘了一个"
-            "收入量级的增速），请在理由里指出，并按不重复计算的口径给值。")
-
-    # 出口二（「无数据（估计）」）合法时 `Row.bases` 就是空列表——
-    # `basis.validate([])` 的原意是防"整批一条依据都没给"，但因子模式
-    # 下"每一行都被 `_factor_basis` 判定为合法的无数据估计"是一种真实
-    # 可能出现、且**应当被允许**的情形（分解式里的因子确实可能全部
-    # 缺数据），不是"没做功课"。这种情形已经在 `_factor_basis`/
-    # `_check_factors` 里逐行验过一遍（确实 MISSING、且有理由），不需要
-    # 再套用这条本来是给"完全没有产出"设的全局闸门；反过来，只要有任何
-    # 一行不是走这条出口，就仍然照常校验，不放过真正缺依据的情形。
-    all_estimated = bool(a.rows) and decomps and all(not r.bases for r in a.rows)
-    if all_estimated:
-        return
-    # 上面那条出口之外，"整批一条依据都没给"这道全局闸门必须原样保留。
-    # 放宽把整批核验（`validate(flat)`）改成了逐行逐依据核验，而逐行
-    # 循环对空 `bases` 的行直接跳过——因子模式没事（空 `bases` 是
-    # 「无数据（估计）」这条合法出口，`_check_factors` 另有"没给理由
-    # 就驳回"兜底），**两标量模式却没有任何兜底**：`_parse_scalar` 里
-    # 缺依据必然抛错，但 `check()` 会在人工核实之后、`from_json` 读回
-    # 时脱离解析器再跑一遍（`full.run_valuation`），人手把 `bases` 清空
-    # 就能一路通过。这道门守的是真实踩过的事故（见
-    # `test_依据跨阶段存活`：依据没跨阶段带过去，读回来一律"未提供任何
-    # 依据"而拒绝建模——门是对的）。这次放宽只针对因子引文，不该顺手
-    # 把它拆掉。
-    if not any(r.bases for r in a.rows):
-        raise AssumeFailed("依据不可核验：未提供任何依据。")
-    # **放宽因子模式的引文审核（用户明确决定）**：真实实跑（600584，
-    # 9 因子×5 年×3 档=45 行）显示逐字引文这道闸门约拦下一半产出，卡点
-    # 几乎都是模型对正文含义的**概括**而非**捏造**——「上游议价能力弱
-    # 意味着公司对设备与原材料价格波动的传导能力有限」「修复但未完全
-    # 追平」，这两句正文里都没有逐字对应，却也不是编出来的。规则要求
-    # 逐字，连续 3 次被拒后整个因子分解被丢掉、退回两标量模式——而
-    # 因子分解正是这条产品线的全部意义。用户判断：因子该按数值是否
-    # 合理来审，不该被引文格式卡死。
-    #
-    # 因此逐 Basis 校验（而不是像原来那样把全部 Basis 拼成一个 `flat`
-    # 列表一次性核验）：这样才能按 `BasisKind` 精确分流——只有 REPORT
-    # （引正文逐字核验不过）在因子模式下降级为标注在对应 `Row` 上；
-    # FACT（占位符指向的账本事实不存在）与 EXTERNAL（外部资料核验
-    # 不过）继续致命，理由见下面分支的注释。两标量模式（`decomps`
-    # 为空）完全不受影响——那条路径引的是正文，正文本来就覆盖增速/
-    # 毛利率两项，逐字核验在那里可满足，这次放宽只针对因子行。
-    fatal: list[str] = []
+        facts.problems += check_deviation(a, balances)
+    a.implied = None
+    if not facts.blocking:
+        try:
+            a.implied = _implied_for(a, ledger, as_of, decomps)
+        except AssumeFailed as e:
+            facts.blocking.append(str(e))
+    if a.implied is not None and a.implied.reason:
+        facts.problems.append(f"增速路径的算术后果：{a.implied.reason}"
+                              f"（{a.implied.describe()}）")
+    facts.problems += _basis_problems(a, ledger=ledger, body=body, as_of=as_of,
+                                      decomps=decomps)
+    a.facts = facts
+    return facts
+
+
+def _basis_problems(a: AssumptionSet, *, ledger: FactLedger, body: str,
+                    as_of: date, decomps) -> list[str]:
+    """逐条核验依据。核验不过的行打上 `basis_unverified`（清单上显著标 ⚠），
+    问题作为事实返回。**不分 BasisKind 一律不驳回**：此前 REPORT 已放宽为标注
+    （600584：逐字引文约拦下一半产出，卡点几乎都是概括而非捏造），FACT 与
+    EXTERNAL 仍致命 —— 现在三类都交审核者 [依据] 条目判断。"""
+    out: list[str] = []
+    if a.rows and not any(r.bases for r in a.rows) and not decomps:
+        out.append("依据：未提供任何依据（两标量模式一行可核验的依据都没有）")
     for short, bv in a.base_values.items():
         if bv.source != "AI" or not bv.bases:
             continue
-        soft_b: list[str] = []
-        for b in bv.bases:
-            problems = validate([b], ledger=ledger, body=body, as_of=as_of)
-            if not problems:
-                continue
-            if b.kind is BasisKind.REPORT:
-                soft_b.extend(problems)
-            else:
-                fatal.append(f"基期 {short}：" + "；".join(problems))
-        if soft_b:
-            bv.basis_unverified = "；".join(soft_b)
+        probs = [p for b in bv.bases
+                 for p in validate([b], ledger=ledger, body=body, as_of=as_of)]
+        if probs:
+            bv.basis_unverified = "；".join(probs)
+            out.append(f"基期 {short} 的依据核验不过：{bv.basis_unverified}")
     for r in a.rows:
         if not r.bases:
             continue
-        soft: list[str] = []
-        for b in r.bases:
-            problems = validate([b], ledger=ledger, body=body, as_of=as_of)
-            if not problems:
-                continue
-            if decomps and b.kind is BasisKind.REPORT:
-                soft.extend(problems)
-            else:
-                # FACT：占位符格式合法但指向的 key@period 在账本里不存在
-                # ——这是对账本的**假陈述**，且下游会拿占位符去做字符串
-                # 替换，编造的占位符会让替换失败或替换出错的值（实测踩
-                # 过整段估值漏成未替换占位符）。EXTERNAL：外部资料核验
-                # 不过。两者都与"对正文的转述 vs 逐字"不是一类问题，
-                # 不在这次放宽范围内，继续致命。
-                fatal.append(f"{_row_label(r, a._m() or a.decomps)} 第{r.year}年："
-                            + "；".join(problems))
-        if soft:
-            r.basis_unverified = "；".join(soft)
-    if fatal:
-        raise AssumeFailed("依据不可核验：" + "；".join(fatal))
-
-
-def _check_scalar(a: AssumptionSet, years: int) -> None:
-    # C-a 纵深防御（final-fix-D）：`_parse_scalar` 已在解析阶段拒绝
-    # 重复行，但 `check()` 也会在人工核实之后、`from_json` 读回时被
-    # 再跑一遍（`full.run_valuation`）——那时的 `AssumptionSet` 未必
-    # 经过 `_parse_scalar`，人手改 JSON 也可能引入重复行。这里再挡
-    # 一次，不依赖上游一定已经查过重（与 `_check_factors` 同一套
-    # 纵深防御思路）。
+        probs = [p for b in r.bases
+                 for p in validate([b], ledger=ledger, body=body, as_of=as_of)]
+        if probs:
+            r.basis_unverified = "；".join(probs)
+            out.append(f"{_row_label(r, a._m() or a.decomps)} 第{r.year}年的依据核验"
+                       f"不过：{r.basis_unverified}")
+    return out
+
+
+def _check_scalar(a: AssumptionSet, years: int, facts: "Facts") -> None:
+    """两标量模式的计算前提与事实。重复行、缺年份 → 算不下去（`path()` 按年份
+    排序后按下标取值，错位一格就是错数）；三档没拉开 → 事实，交审核者 [合成]。"""
     inv = {v: k for k, v in DRIVERS.items()}
     seen: set[tuple[str, int]] = set()
     for r in a.rows:
         key = (r.driver, r.year)
         if key in seen:
-            raise AssumeFailed(
-                f"{inv.get(r.driver, r.driver)} 第 {r.year} 年出现了不止"
-                "一行——重复行会让 path() 按年份排序后整体错位一格，"
-                "必须只保留一行。")
+            # `_parse_scalar` 已按最后一行去重；走到这里是人手改 JSON 引入的。
+            facts.blocking.append(
+                f"{inv.get(r.driver, r.driver)} 第 {r.year} 年出现了不止一行"
+                "（path() 会整体错位一格）")
         seen.add(key)
-
     for cn, key in DRIVERS.items():
         got = {r.year for r in a.rows if r.driver == key}
         missing = sorted(set(range(1, years + 1)) - got)
         if missing:
-            raise AssumeFailed(
-                f"{cn} 缺第 {missing} 年 —— 预测期每一年都要给，"
-                "漏掉的年份会沿用模板默认值，而那不是你的判断")
-
+            facts.blocking.append(f"{cn} 缺第 {missing} 年")
     for r in a.rows:
-        # **三档必须拉开。** 模板默认 Bull=Base=Bear，那等于没有情景；
-        # 拉不开的情景分析只是把同一个数抄三遍。
         if not (r.bear < r.base < r.bull):
-            raise AssumeFailed(
+            facts.problems.append(
                 f"第 {r.year} 年的{'营收增速' if r.driver == 'growth' else '毛利率'}"
-                f"三档未拉开（悲观 {r.bear} / 中性 {r.base} / 乐观 {r.bull}）"
-                " —— 必须严格递增，否则情景分析没有意义")
+                f"三档未拉开（悲观 {r.bear} / 中性 {r.base} / 乐观 {r.bull}，未严格递增）")
 
 
 def _fill_from_ledger(a: AssumptionSet, ledger: FactLedger, as_of: date) -> None:
@@ -1666,24 +1641,22 @@ def _fill_from_ledger(a: AssumptionSet, ledger: FactLedger, as_of: date) -> None
 
 
 def _check_factors(a: AssumptionSet, years: int, decomps,
-                   base_margin: Decimal | None, anchor: dict | None = None) -> None:
-    """分解模式下的校验。
+                   base_margin: Decimal | None, anchor: dict | None,
+                   facts: "Facts") -> None:
+    """分解模式下的计算前提与事实。
 
-    **不在这里逐因子比较 bear<base<bull。** 单个因子档位的方向性因子而异，
-    代码判断不了「这个因子往哪个方向走算悲观」。真正要卡的是**合成结果**——
-    三档营收增速 / 毛利率必须严格拉开。
+    重复行、缺年份、缺基期 → 算不下去；合成后三档不单调、「无数据（估计）」
+    没给理由 → 事实，交审核者。**不在这里逐因子比较 bear<base<bull**：单个
+    因子档位的方向因因子而异（侵蚀类因子悲观更负），由审核者 [取值] 判断。
     """
-    # **读回的旧分解式要重过闸门。** `_decomp_from_dict` 直接构造对象，预研时的
-    # 闸门在读回路径上一次都没跑。只对旧的乘/加分解式（公式化写法不跑这些字面
-    # 闸门，spec 2026-10-14 五）。
+    # 读回的旧分解式：预研时的字面闸门在读回路径上一次都没跑。只对旧的乘/加
+    # 分解式，结论交审核者（公式化写法本就不跑这些字面闸门）。
     if isinstance(decomps, list):
         for d in decomps:
             try:
                 check_dimensions(d)
             except DecompositionInvalid as e:
-                raise AssumeFailed(
-                    f"判断项里的分解式已不合法：{e}　——请重跑第一阶段，"
-                    "重新预研分解式后再填判断项。") from e
+                facts.problems.append(f"旧分解式的量纲检查不过：{e}")
     m = a._m()
     label_of = {f.key: f.label for f in m.factors}
     seen: set[tuple[str, int]] = set()
@@ -1692,68 +1665,56 @@ def _check_factors(a: AssumptionSet, years: int, decomps,
             continue
         key = (r.factor_key, r.year)
         if key in seen:
-            raise AssumeFailed(
-                f"因子「{label_of.get(r.factor_key, r.factor_key)}」"
-                f"第 {r.year} 年出现了不止一行——重复行会让清单显示的值"
-                "与实际参与合成的值不是同一行，必须只保留一行。")
+            facts.blocking.append(
+                f"因子「{label_of.get(r.factor_key, r.factor_key)}」第 {r.year} 年"
+                "出现了不止一行（清单显示的值与参与合成的值不是同一行）")
         seen.add(key)
-        # 出口二（「无数据（估计）」）落地为空的 `bases`——那不能免除「说明
-        # 依据什么估的」这一要求：空依据 + 空理由 = 一个数字凭空出现在清单上。
         if not r.bases and not r.note.strip():
-            raise AssumeFailed(
-                f"因子「{label_of.get(r.factor_key, r.factor_key)}」"
-                f"第 {r.year} 年写了「无数据（估计）」却没有给理由——"
-                "理由要说明这个估计依据什么给出（参照哪个可比公司、"
-                "哪个行业惯例……），不能只留一句「无数据」。")
-
+            facts.problems.append(
+                f"因子「{label_of.get(r.factor_key, r.factor_key)}」第 {r.year} 年"
+                "没有依据也没有给理由（一个数字凭空出现在清单上）")
     for f in m.factors:
         got = {r.year for r in a.rows if r.factor_key == f.key}
         missing = sorted(set(range(1, years + 1)) - got)
         if missing:
-            raise AssumeFailed(
-                f"因子「{f.label}」缺第 {missing} 年 —— 预测期每一年都要给，"
-                "漏掉的年份会沿用模板默认值，而那不是你的判断")
-
+            facts.blocking.append(f"因子「{f.label}」缺第 {missing} 年")
     lacking = [f for f in m.needs_base() if f.short not in a.base_values]
     if lacking:
-        raise AssumeFailed(
-            "以下因子需要第 0 年（基期）的取值，账本里没有："
-            + "、".join(f"{f.short}（{f.label}）" for f in lacking)
-            + " —— 请各补一行「基期: <短名> | <值> | <依据>」（已发生的实际值，"
-            "单值不分档）。")
-
-    # **合成后再验一次单调。** 这是本模块真正的闸门。
-    if base_margin is None:
-        base_margin = Decimal("0.10")
+        facts.blocking.append(
+            "以下因子需要第 0 年（基期）的取值，账本里没有、AI 也没补："
+            + "、".join(f"{f.short}（{f.label}）" for f in lacking))
     if isinstance(decomps, list):
         a.decomps = list(decomps)
-    # 锚定开启时查的必须是平移后的路径——那才是建模用的。
-    a.margin_bases = margin_bases(a, base_margin, anchor)
-    _check_monotonic(a, base_margin, a.margin_bases)
+    if facts.blocking:
+        return
+    if base_margin is None:
+        base_margin = Decimal("0.10")
+    try:
+        # 锚定开启时看的必须是平移后的路径——那才是建模用的。
+        a.margin_bases = margin_bases(a, base_margin, anchor)
+        facts.problems += _monotonic_problems(a, base_margin, a.margin_bases)
+    except AssumeFailed as e:
+        facts.blocking.append(str(e))
 
 
-def _check_monotonic(a: AssumptionSet, base_margin: Decimal,
-                     bases: dict | None) -> None:
-    """合成后三档必须严格拉开。check() 与 apply_to 共用：第二阶段的 check()
-    拿不到 calibrate 结果、不做锚定平移，平移发生在 apply_to，那里要再查一次，
-    不能让不合格的路径进工作簿。"""
+def _monotonic_problems(a: AssumptionSet, base_margin: Decimal,
+                        bases: dict | None) -> list[str]:
+    """合成后三档是否严格拉开。**事实，不驳回** —— 交审核者 [合成] 条目（生益
+    科技 2026-10-15：两个 pp 因子各自拉开，加总后第 4 年毛利率倒序；代码只会说
+    「不单调」，审核者能给出「怎么拉开」）。"""
     lo, lom = synthesize(a, Case.BEAR, base_margin, bases)
     mid, midm = synthesize(a, Case.BASE, base_margin, bases)
     hi, him = synthesize(a, Case.BULL, base_margin, bases)
+    out: list[str] = []
     for i in range(len(mid)):
         if not (lo[i] < mid[i] < hi[i]):
-            raise AssumeFailed(
-                f"第 {i + 1} 年的营收增速**合成后**不再单调"
-                f"（悲观 {lo[i]:.2%} / 中性 {mid[i]:.2%} / 乐观 {hi[i]:.2%}）"
-                " —— 因子各自拉开不等于合成结果拉开，请调整因子取值，"
-                "不要悄悄修正合成结果")
+            out.append(f"第 {i + 1} 年的营收增速合成后不单调"
+                       f"（悲观 {lo[i]:.2%} / 中性 {mid[i]:.2%} / 乐观 {hi[i]:.2%}）")
     for i in range(len(midm)):
         if not (lom[i] < midm[i] < him[i]):
-            raise AssumeFailed(
-                f"第 {i + 1} 年的毛利率**合成后**不再单调"
-                f"（悲观 {lom[i]:.2%} / 中性 {midm[i]:.2%} / 乐观 {him[i]:.2%}）"
-                " —— 因子各自拉开不等于合成结果拉开，请调整因子取值，"
-                "不要悄悄修正合成结果")
+            out.append(f"第 {i + 1} 年的毛利率合成后不单调"
+                       f"（悲观 {lom[i]:.2%} / 中性 {midm[i]:.2%} / 乐观 {him[i]:.2%}）")
+    return out
 
 
 PROMPT = """把前面的分析结论，落到 DCF 模型的判断项上。
@@ -2020,9 +1981,11 @@ def _decide_once(client, judg: dict, labels: list[str], context: str, *,
         try:
             a = parse(raw, len(labels), decomps=decomps,
                      factor_data=factor_data, as_of=as_of)
-            check(a, len(labels), ledger=ledger, body=body, as_of=as_of,
-                  decomps=decomps, base_margin=base_margin, anchor=anchor,
-                  balances=balances)
+            facts = check(a, len(labels), ledger=ledger, body=body, as_of=as_of,
+                          decomps=decomps, base_margin=base_margin, anchor=anchor,
+                          balances=balances)
+            if facts.blocking:
+                raise AssumeFailed("算不下去：" + "；".join(facts.blocking))
         except AssumeFailed as e:
             last = str(e)
             prompt = (f"{prompt}\n\n---\n"
@@ -2337,11 +2300,9 @@ def apply_to(cal: dict, a: AssumptionSet, case: Case = Case.BASE) -> dict:
         # 第 0 年营收按工作簿口径（亿元）——公式用到绝对金额时，ir-agent 与
         # 工作簿必须代入同一个数。
         a.base_revenue = _base_revenue_of(cal)
-        # 锚定开启时毛利率第 2 年起从各档锚定水平接着走（见 margin_bases）；
-        # 第二阶段的 check() 不做这一步，所以这里要再查一次单调。
+        # 锚定开启时毛利率第 2 年起从各档锚定水平接着走（见 margin_bases）。
+        # 这里只计算：合成后单调与否是审核者在第一阶段判过的事实（子项目三）。
         bases = margin_bases(a, base_margin, anchor_from_cal(cal))
-        if bases:
-            _check_monotonic(a, base_margin, bases)
         growth_scen, gm_scen = {}, {}
         for c in Case:
             g, m = synthesize(a, c, base_margin, bases)
diff --git a/ir_agent/full.py b/ir_agent/full.py
index 5c50aed..c6a0d51 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -2734,17 +2734,19 @@ def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
         anchor = assume.anchor_from_cal(cal)
     except dcf.DcfUnavailable:
         anchor = None
-    try:
-        # **人改过的值要重新校验。** 批准不等于豁免 —— 三档拉不开、
-        # 漏掉某一年，都会让模型算出一个看起来正常的错数。
-        assume.check(a, len(a.labels), ledger=r.ledger, body=body, as_of=as_of,
-                     # **读 `a._m()`，不读 `a.decomps`**：公式化写法下 decomps 是空
-                     # 列表，`decomps=a.decomps or None` 会让第二阶段悄悄退回两标量
-                     # 校验，人批准的公式在这里一行都不生效。
-                     decomps=a._m(), base_margin=base_margin,
-                     anchor=anchor)
-    except assume.AssumeFailed as e:
-        raise SystemExit(f"批准的假设不合法，拒绝建模：\n{e}")
+    # **只计算，不再判断**（子项目三）：人在门③ 签了字，AI 审核也已在第一阶段
+    # 做过；这里只看算不算得下去（缺年份、缺基期、重复行、公式算不动）。此前
+    # 这里会把人批准的判断项再按内容闸门拒绝一遍 —— 600584 实测，人批准了，
+    # 建模前一刻又被拒，人工门被架空。
+    facts = assume.check(a, len(a.labels), ledger=r.ledger, body=body, as_of=as_of,
+                         # **读 `a._m()`，不读 `a.decomps`**：公式化写法下 decomps
+                         # 是空列表，`decomps=a.decomps or None` 会让第二阶段悄悄
+                         # 退回两标量口径，人批准的公式在这里一行都不生效。
+                         decomps=a._m(), base_margin=base_margin,
+                         anchor=anchor)
+    if facts.blocking:
+        raise SystemExit("批准的判断项算不下去，无法建模：\n"
+                         + "\n".join(f"  - {x}" for x in facts.blocking))
 
     # I3（final-fix-D 终审）：`check()` 通过后 `a.margin_bases` 就定了——
     # 非空说明毛利率加法链第 2 年起的基期已从年报基期换成各档锚定后的
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2413 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `check` 里删掉 `facts.problems += check_deviation(a, balances)` → 偏离类测试红
- `_check_factors` 里 `_monotonic_problems(...)` 结果不并入 problems → 合成单调测试红
- `run_valuation` 里的 `if facts.blocking:` 改成 `if False:` →（此处只有源码守着，Task 8 补行为测试）

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(assume): check() 只报事实不驳回 —— 返回 Facts（problems / blocking）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 判断项审核者 assume_review

**Files:**
- Modify: `ir_agent/assume.py`
- Create: `ir_agent/assume_review.py`
- Modify: `ir_agent/methodology_review.py`
- Modify: `tests/judge_stub.py`
- Create: `tests/test_assume_review.py`

**Interfaces:**
- Consumes: `assume.parse`、`assume.check -> Facts`、`assume.build_prompt`、`assume._fill_from_ledger`、`assume._reconcile_model`、`MR.parse(kinds=)`、`MR.annotate_quotes`、`MR._feedback`、`MR._fenced`
- Produces: `assume_review.KINDS`；`checklist(a, balances) -> list[tuple[str,str]]`；`parse(raw, expected) -> list[Verdict]`；`facts_for_judge(a, *, balances, ledger, as_of) -> str`；`review(judge, *, draft, materials, facts, catalog, expected, hay, ledger, as_of) -> ReviewResult`；`fill_and_review(filler, judge, *, judg, labels, context, ledger, body, as_of, code, decomps, factor_data, base_margin, anchor, balances, catalog, method_review, max_rounds=3) -> (AssumptionSet, ReviewResult, list[Round])`；`ReviewStop(msg, rounds, last=None)`；`Round`；`fingerprint(a) -> str`；`dump_review(res, *, passed) -> str`；`review_markdown(res) -> list[str]`；`rounds_markdown(rounds) -> str`；`AssumptionSet.judge_review`

**为什么：** 新模块，结构照搬 `methodology_review`：代码生成清单（因子模式 [取值]×N、[平衡点]（有辩论② 平衡点的因子）、[依据]×N、[合成] 营收/毛利率、[后果] 营收路径；两标量模式只有 [依据][合成][后果]）；审核者看填写者看到的全部 + 代码事实（每格原文与读数并排、合成三档、算术后果、平衡点、方法论遗留、账本 key 取值、读数与核对问题）+ 账本事实目录；可附选填「补充」（判致命同样驳回、同样要建议）；`fill_and_review` 三轮：有致命带建议退回、补答不消耗轮数、审核通过但 `blocking` 非空也停下；**不退回两标量**。`MR.ledger_decl_lines` 从 `extra_facts` 抽出两边共用；`judge_stub` 条目块与类别放开、可附额外行。

- [ ] **Step 1: 写失败测试**

```diff
diff --git a/tests/judge_stub.py b/tests/judge_stub.py
index 785d4da..44a47f2 100644
--- a/tests/judge_stub.py
+++ b/tests/judge_stub.py
@@ -6,11 +6,12 @@ from __future__ import annotations
 
 import re
 
-_ITEM = re.compile(r"^- \[(模式|因子|正交|公式|常量|账本|覆盖)\] (.+)$", re.M)
+_ITEM = re.compile(r"^- \[([^\]\s]{1,4})\] (.+)$", re.M)
 
 
 def items(prompt: str) -> list[tuple[str, str]]:
-    head = prompt.split("**必须逐条作答的条目**")[-1].split("**提出者给的分解式（原文）**")[0]
+    # 条目块到下一个「空行 + **标题**」为止（方法论审核与判断项审核共用）。
+    head = prompt.split("**必须逐条作答的条目**")[-1].split("\n\n**")[0]
     return _ITEM.findall(head)
 
 
@@ -18,8 +19,9 @@ class Judge:
     """`levels`：{(类别, 条目) 或 类别: 结论}；没列的答「通过」。可给多轮剧本
     `script=[levels_1, levels_2, ...]`，第 n 次调用用第 n 份（用完沿用最后一份）。"""
 
-    def __init__(self, *script, skip=(), suggest=True):
+    def __init__(self, *script, skip=(), suggest=True, extra=()):
         self.script = list(script) or [{}]
+        self.extra = list(extra)       # 原样附在回答末尾的行（如「补充：…」）
         self.skip = set(skip)          # 故意漏答的条目
         self.suggest = suggest         # 致命时附「建议：…」（规则要求必写）
         self.prompts: list[str] = []
@@ -36,4 +38,4 @@ class Judge:
             if lv == "致命" and self.suggest:
                 why += f"　建议：改{k}{s}"
             out.append(f"[{k}] {s}：{lv}　{why}".rstrip())
-        return "\n".join(out)
+        return "\n".join(out + self.extra)
diff --git a/tests/test_assume_review.py b/tests/test_assume_review.py
new file mode 100644
index 0000000..c95587f
--- /dev/null
+++ b/tests/test_assume_review.py
@@ -0,0 +1,198 @@
+"""判断项的审核者（spec 2026-10-15 子项目三）。不调模型。"""
+from __future__ import annotations
+
+from datetime import date
+from decimal import Decimal as D
+
+import pytest
+
+from ir_agent import assume
+from ir_agent import assume_review as AR
+from ir_agent.ledger import Fact, FactLedger, Method
+from ir_agent.methodology import parse as parse_meth
+from ir_agent.stance import Balance
+from judge_stub import Judge, items
+
+AS_OF = date(2026, 10, 15)
+BODY = "行业需求结构性向上，份额温和扩张。"
+JUDG = {"g_stats": (), "gm_stats": ()}
+LABELS = ["2026E", "2027E"]
+
+MODEL = parse_meth("""模式: 变化
+因子: 行业 | 行业需求增速 | 变化率 | 研报 | 行业规模
+因子: 份额 | 市占率变化 | 变化率 | 研报 | 市占率
+因子: 提价 | 提价传导 | pp | 调价函 | 调价
+因子: 原料 | 原材料成本冲击 | pp | 年报 | 原材料价格
+常量: 基期市占率 | 0.08 | 年报
+公式: 营收 = 上期(营收) × (1 + 行业) × (1 + 份额 / 基期市占率)
+公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100
+""").model
+
+
+def _ledger() -> FactLedger:
+    led = FactLedger()
+    for key, v in (("revenue", "20000000000"), ("gross_margin", "0.2647")):
+        led.put(Fact(key=key, value=D(v), unit="x", period="2025FY",
+                     as_of=date(2026, 4, 1), source_id="t", method=Method.REPORTED))
+    return led
+
+
+def _draft(gm_bull="5.47", year2=True) -> str:
+    rows = []
+    for y in (1, 2) if year2 else (1,):
+        rows += [f"假设: 行业 | 第{y}年 | 10% | 20% | 30% | 平衡点 | 无数据（估计），参照行业",
+                 f"假设: 份额 | 第{y}年 | 0.2% | 0.6% | 1.2% | 温和扩张 | 无数据（估计），参照份额",
+                 f"假设: 提价 | 第{y}年 | 1 | 3.5 | {gm_bull} | 上半年提升 5.47 个百分点 | 无数据（估计），研报",
+                 f"假设: 原料 | 第{y}年 | -8 | -4 | -1 | 铜箔高位 | 无数据（估计），年报"]
+    return "\n".join(rows)
+
+
+class _Filler:
+    def __init__(self, *outs):
+        self.outs, self.prompts = list(outs), []
+
+    def __call__(self, p):
+        self.prompts.append(p)
+        return self.outs[min(len(self.prompts), len(self.outs)) - 1]
+
+
+def _run(filler, judge, **kw):
+    return AR.fill_and_review(filler, judge, judg=JUDG, labels=LABELS, context="上下文",
+                              ledger=_ledger(), body=BODY, as_of=AS_OF, code="600183",
+                              decomps=kw.pop("decomps", MODEL),
+                              base_margin=D("0.2647"), **kw)
+
+
+class TestChecklist:
+    def test_五类_有平衡点的因子才列平衡点(self):
+        a = assume.parse(_draft(), 2, decomps=MODEL)
+        got = AR.checklist(a, [Balance("提价传导", D("1"), D("3.5"), D("6"), "x")])
+        kinds = [k for k, _ in got]
+        assert kinds.count("取值") == 4 and kinds.count("依据") == 4
+        assert [s for k, s in got if k == "平衡点"] == ["提价（提价传导）"]
+        assert [s for k, s in got if k == "合成"] == ["营收", "毛利率"]
+        assert [s for k, s in got if k == "后果"] == ["营收路径"]
+
+    def test_两标量模式只有依据合成后果(self):
+        a = assume.AssumptionSet(code="", labels=LABELS)
+        assert {k for k, _ in AR.checklist(a, [])} == {"依据", "合成", "后果"}
+
+
+class TestParse:
+    EXP = [("取值", "提价（提价传导）"), ("后果", "营收路径")]
+
+    def test_补充选填_不会并进上一条(self):
+        raw = ("[取值] 提价（提价传导）：通过\n"
+               "补充：提醒　应付天数 17 分位，与占款叙事相反　建议：删掉占款那句\n"
+               "[后果] 营收路径：通过")
+        got = AR.parse(raw, self.EXP)
+        assert got[0].why == "" and got[-1].kind == "补充"
+        assert got[-1].suggest == "删掉占款那句"
+        assert len(AR.parse("[取值] 提价（提价传导）：通过\n[后果] 营收路径：通过",
+                            self.EXP)) == 2
+
+    def test_补充判致命也要附建议(self):
+        with pytest.raises(AR.ReviewIncomplete, match="补充"):
+            AR.parse("[取值] 提价（提价传导）：通过\n[后果] 营收路径：通过\n"
+                     "补充：致命　产能撑不住", self.EXP)
+
+
+class TestFacts:
+    def test_原文与读数并排_单位错一眼可见(self):
+        """生益科技 2026-10-15：理由栏「5.47 个百分点」、表里 0.0547。"""
+        a = assume.parse(_draft(gm_bull="0.0547"), 2, decomps=MODEL)
+        assume.check(a, 2, ledger=_ledger(), body=BODY, as_of=AS_OF,
+                     decomps=MODEL, base_margin=D("0.2647"))
+        txt = AR.facts_for_judge(a, balances=[Balance("提价传导", D("0.02"), D("0.035"),
+                                                      D("0.05"), "辩论②")])
+        assert "提价传导 第1年：原文 1 / 3.5 / 0.0547 → 读作 +1.00pp / +3.50pp / +0.05pp" in txt
+        assert "提价传导：悲观 0.02 / 平衡 0.035 / 乐观 0.05" in txt
+        assert "【代码按公式合成的三档】" in txt
+
+
+class TestRounds:
+    def test_第一轮通过(self):
+        a, res, rounds = _run(_Filler(_draft()), Judge())
+        assert res.rounds == 1 and len(rounds) == 1 and a.judge_review is res
+        assert a._m() is MODEL or a.model is MODEL
+
+    def test_致命即退回_建议交给填写者_第二轮通过(self):
+        f = _Filler(_draft(gm_bull="0.0547"), _draft())
+        j = Judge({("取值", "提价（提价传导）"): "致命"}, {})
+        a, res, rounds = _run(f, j)
+        assert res.rounds == 2 and rounds[0].review.fatal()
+        assert "建议：改取值提价（提价传导）" in f.prompts[1]
+        key = next(f.key for f in MODEL.factors if f.short == "提价")
+        assert next(r.bull for r in a.rows if r.factor_key == key and r.year == 1) \
+            == D("5.47"), "采用的是重填后的第二稿"
+
+    def test_三轮仍有致命停下_带着最后一稿(self):
+        with pytest.raises(AR.ReviewStop, match="3 轮未通过.*致命 1 条") as e:
+            _run(_Filler(_draft()), Judge({("后果", "营收路径"): "致命"}))
+        assert len(e.value.rounds) == 3 and e.value.last is not None
+
+    def test_审核者答不全就停下_不当通过(self):
+        with pytest.raises(AR.ReviewStop, match="审核未完成"):
+            _run(_Filler(_draft()), Judge(skip={("后果", "营收路径")}))
+
+    def test_补答不消耗填写者轮数(self):
+        class Once(Judge):
+            def __call__(self, prompt):
+                self.skip = {("后果", "营收路径")} if not self.prompts else set()
+                return super().__call__(prompt)
+        f, j = _Filler(_draft()), Once()
+        _, res, rounds = _run(f, j)
+        assert len(j.prompts) == 2 and len(f.prompts) == 1 and res.rounds == 1
+
+    def test_审核通过但算不下去就停下_写明哪里(self):
+        """缺第 2 年：代码不驳回，审核者却放行了 —— 停下，如实说明算不下去。"""
+        with pytest.raises(AR.ReviewStop, match="审核已通过，但无法计算.*缺第 \\[2\\] 年"):
+            _run(_Filler(_draft(year2=False)), Judge())
+
+    def test_不退回两标量模式(self):
+        """因子名全写错：代码一行没读进来，交给审核者；绝不悄悄换成两标量。"""
+        bad = _draft().replace("假设: 行业", "假设: 不存在")
+        j = Judge({("取值", "行业（行业需求增速）"): "致命"}, {})
+        a, res, _ = _run(_Filler(bad, _draft()), j)
+        assert a.model is MODEL and res.rounds == 2
+        assert "未知的因子 '不存在'" in j.prompts[0]
+
+    def test_审核者看到的比填写者多(self):
+        j = Judge()
+        _run(_Filler(_draft()), j, catalog="  营收  100亿  写作 [[revenue@2025FY]]",
+             method_review=["- [因子] 提价：提醒　x"])
+        p = j.prompts[0]
+        assert "只给你的、代码算出的事实" in p and "原文 10% / 20% / 30%" in p
+        assert "账本事实目录" in p and "[[revenue@2025FY]]" in p
+        assert "【方法论审核遗留】" in p and "填写者看到的资料" in p
+
+    def test_各轮记录(self):
+        with pytest.raises(AR.ReviewStop) as e:
+            _run(_Filler(_draft()), Judge({"后果": "致命"}))
+        md = AR.rounds_markdown(e.value.rounds)
+        assert md.count("## 第 ") == 3 and "### 填写者原文" in md
+        assert "### 代码事实" in md and "审核者原文（第 1 次作答）" in md
+        assert "[后果] 营收路径：致命" in md
+
+
+class TestFingerprint:
+    def test_改数会变_改理由不变_落盘读回不变(self):
+        a = assume.parse(_draft(), 2, decomps=MODEL)
+        fp = AR.fingerprint(a)
+        back, _ = assume.from_json(assume.to_json(a))
+        assert AR.fingerprint(back) == fp
+        a.rows[0].note = "改了理由"
+        assert AR.fingerprint(a) == fp
+        a.rows[0].base = D("0.21")
+        assert AR.fingerprint(a) != fp
+
+
+def test_清单与提示词里的条目一致():
+    j = Judge()
+    a, _, _ = _run(_Filler(_draft()), j)
+    assert items(j.prompts[0]) == AR.checklist(a, [])
+
+
+def test_规则正文不出现与清单同形的条目行():
+    import re
+    assert not re.search(r"(?m)^\s*-\s*\[(取值|平衡点|依据|合成|后果)\]", AR.RULES)
```

- [ ] **Step 2: 跑测试，确认红**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 error（收集阶段 ModuleNotFoundError: No module named 'ir_agent.assume_review'）`。

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index b86cdb6..a9aa6cc 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -368,6 +368,9 @@ class AssumptionSet:
     # 代码读数时发现的问题（读不出、单位写法、依据写法、重复行……）。**只是事实，
     # 不驳回**，交给判断项审核者（子项目三）。派生量，不进 JSON。
     read_notes: list = field(default_factory=list)
+    # 判断项审核结论（`methodology_review.ReviewResult`，子项目三）。派生量，不进
+    # JSON —— 落盘在 assumptions_review.json。
+    judge_review: object = None
 
     def _m(self) -> FormulaModel | None:
         if self.model is None and self.decomps:
diff --git a/ir_agent/assume_review.py b/ir_agent/assume_review.py
new file mode 100644
index 0000000..7ac8b81
--- /dev/null
+++ b/ir_agent/assume_review.py
@@ -0,0 +1,342 @@
+"""判断项的审核者（子项目三，spec `2026-10-15-assume-judge-design.md`）。
+
+**为什么要有审核者。** 生益科技第三次调用（2026-10-15）：第二次调用已经给出
+完整的公式化分解式，第三次调用却被代码闸门连挂三次、整个因子分解退回两标量。
+三次的判定者全部是代码（`assume.check` 的 49 处 `AssumeFailed`），且三次都与
+分解式内容无关：平衡点单位错被偏离闸门放大、两个 pp 因子加总后倒序、代码自己
+的「第 7 字段」前后矛盾。用户决定：**判断项合格与否全部由 AI 判定，代码不做
+任何驳回** —— 代码只读数、计算、核对引文，把结果作为事实交给审核者。
+
+**流程照搬子项目二**（`methodology_review`）：填写者交稿 → 代码读数与计算（不驳回）
+→ 审核者按清单逐条作答（四级结论，致命必附建议，可附「补充」）→ 有致命即带着建议
+退回重填，最多 3 轮 → 仍不通过停下交人工，**不再退回两标量模式**。
+
+**原先门③ 的两道 AI 审核并进来**：`factor_review` [对账] 由 [合成] 承接；判断项
+假设辩论由 [后果]、[依据] 与「补充」承接，审核者因此拿到完整账本事实目录。
+"""
+
+from __future__ import annotations
+
+import hashlib
+import json
+import re
+from dataclasses import dataclass, field
+from decimal import Decimal
+
+from ir_agent import methodology_review as MR
+from ir_agent.methodology_review import (LEVELS, ReviewIncomplete, ReviewResult,
+                                         Verdict)
+
+KINDS = ("取值", "平衡点", "依据", "合成", "后果")
+MAX_ROUNDS = 3              # 填写者最多写 3 轮（审核驳回才算一轮）
+JUDGE_TRIES = MR.JUDGE_TRIES  # 审核者作答 1 次 + 漏答补答 2 次，不消耗填写者轮数
+
+# 「补充：<结论>　<问题>　建议：…」—— 清单之外发现的问题（并掉假设辩论后的出口）。
+_SUPP = re.compile(r"^[ \t]*[-*•·]*[ \t]*\**补充\**[ \t]*[:：]\s*(.+)$")
+_SUGGEST = re.compile(r"\s*建议\s*[:：]\s*")
+
+
+class ReviewStop(MR.ReviewStop):
+    """停下交人工。`last` 是最后一稿（读得出来的话），供人以它为底稿改。"""
+
+    def __init__(self, msg: str, rounds: list, last=None):
+        super().__init__(msg, rounds)
+        self.last = last
+
+
+@dataclass
+class Round:
+    """一轮的完整记录：填写者原文、代码事实、审核者每次作答与逐条结论。"""
+
+    n: int
+    raw: str
+    facts: str = ""
+    review: ReviewResult | None = None
+    incomplete: str = ""
+    attempts: list = field(default_factory=list)   # [(审核者原文, 不完整原因)]
+
+
+def _subject(f) -> str:
+    short = getattr(f, "short", "") or ""
+    return f"{short}（{f.label}）" if short and short != f.label else f.label
+
+
+def checklist(a, balances=()) -> list[tuple[str, str]]:
+    """代码生成「必须作答的条目」（spec 二）。两标量模式只有 [依据]、[合成]、[后果]。"""
+    m = a._m()
+    if m is None:
+        items = [("依据", "营收增速"), ("依据", "毛利率")]
+    else:
+        mids = {b.subject for b in balances or ()
+                if getattr(b, "mid", None) is not None}
+        items = [("取值", _subject(f)) for f in m.factors]
+        items += [("平衡点", _subject(f)) for f in m.factors if f.label in mids]
+        items += [("依据", _subject(f)) for f in m.factors]
+    items += [("合成", "营收"), ("合成", "毛利率"), ("后果", "营收路径")]
+    return items
+
+
+def parse(raw: str, expected: list[tuple[str, str]]) -> list[Verdict]:
+    """解析审核者的回答：清单条目（复用 `methodology_review.parse`）+ 选填的「补充」。
+
+    补充行先从原文里摘掉再解析清单 —— 否则它会被并进上一条条目的理由里。补充判
+    致命同样要附建议，没写算漏答。"""
+    lines = raw.splitlines()
+    supp = [_SUPP.match(x) for x in lines]
+    body = "\n".join(x for x, m in zip(lines, supp) if not m)
+    verdicts = MR.parse(body, expected, kinds=KINDS)
+    extras: list[Verdict] = []
+    for i, m in enumerate((m for m in supp if m), 1):
+        tail = m.group(1).strip().lstrip("*")
+        level = next((lv for lv in sorted(LEVELS, key=len, reverse=True)
+                      if tail.startswith(lv)), None)
+        if level is None:
+            continue
+        why, sug = (_SUGGEST.split(tail[len(level):], 1) + [""])[:2]
+        why, sug = (" ".join(x.strip(" 　\t*：:").split()) for x in (why, sug))
+        extras.append(Verdict("补充", f"第 {i} 条", level, why, suggest=sug))
+    bad = [v for v in extras if v.level == "致命" and not v.suggest]
+    if bad:
+        raise ReviewIncomplete("以下补充判为致命却没给修改建议（理由之后写「建议：…」）："
+                               + "、".join(v.subject for v in bad))
+    return verdicts + extras
+
+
+RULES = """你是研究总监，正在审核一份 **DCF 判断项**（营收与毛利率驱动因子的逐年三档
+取值）能不能交给建模。你不直接改数，而是逐项给结论、理由与修改建议 —— 有一项
+「致命」，填写者就要按你的建议重填。代码不做任何判定：它只把读到的值、算出的
+路径、引文核验的结果作为事实交给你，要不要紧由你判断。
+
+**四级结论**（只能用这四个词）：
+- **通过**：逻辑完全正确，资料能够对得上；
+- **提醒**：逻辑部分正确，资料对得上，但结论较为牵强；
+- **致命**：逻辑完全无法支撑结论；
+- **待采集**：逻辑正确，资料尚未采集到。
+
+提醒、致命、待采集**必须写理由**。凡涉及资料的判断，引原文「…」或账本占位符
+[[key@期间]] —— 代码会核对你的引文能否在资料、账本里找到。
+
+**每类要判什么**（下面用「类别名：」说明，作答时才写成 [类别]）：
+- 取值：代码读到的值是不是填写者的本意、单位是否与因子声明一致 —— pp 是百分点
+  数字本身（5.47 个百分点写 5.47），变化率是百分比；对照理由栏看（理由写
+  「5.47 个百分点」、表里却是 0.0547，就是缩小了 100 倍）。三档方向与间距（侵蚀类
+  因子悲观应更负）；逐年路径是否讲得通；公式用 `基期(·)` 时填的应是相对基期的
+  累计值，用 `上期(·)` 时是逐年变化。
+- 平衡点：中性档与辩论② 平衡点的关系、偏离的理由是否成立；**平衡点本身的单位
+  是否可信**（pp 因子的平衡点写成小数也是常见错误）。不可信时在建议里写出换算后
+  的值，由填写者换算。
+- 依据：依据能否支撑取值（代码给了引文核验结果；核验不过不等于捏造，可能是
+  概括）；「无数据（估计）」是否属实、「同上」是否合理；理由与取值是否自洽；是否
+  回应了方法论审核遗留的提醒与待采集。
+- 合成：代码按公式合成的三档营收增速与毛利率是否拉开、是否单调；合成路径与理由
+  栏引用的参照（行业增速、历史 CAGR、同业水平）是否自洽；与半年报锚定的首年是否
+  衔接。
+- 后果：5 年累计倍数、隐含市占率，放到行业格局、产能、CR5、周转与杠杆（账本事实
+  目录里有）下看是否可能。
+
+**每条一行，格式严格如下**（结论、理由、建议之间用全角空格）：
+
+[类别] <条目>：<通过|提醒|致命|待采集>　<理由>　建议：<具体怎么改>
+
+**判为致命的条目必须写「建议：…」** —— 具体怎么改：哪个因子哪一年改成多少、单位
+怎么换算、哪条理由要改。提醒、待采集有建议也写上；通过不必写。条目名照上面清单
+原样抄，后面不要加公式或别的内容。
+
+清单之外若发现问题（例如账本事实与理由栏的叙事相反），另起一行写：
+
+补充：<致命|提醒>　<问题>　建议：<怎么改>
+
+补充可以不写；补充判致命同样会退回重填。
+"""
+
+
+def facts_for_judge(a, *, balances=(), ledger=None, as_of=None) -> str:
+    """只给审核者看的、代码现算的事实（spec 二）。"""
+    from ir_agent import assume
+
+    lines = ["【每格原文与代码读数】"]
+    m = a._m()
+    label = ({f.key: (f.label, f.unit) for f in m.factors} if m is not None else {})
+    for r in sorted(a.rows, key=lambda x: (x.factor_key or x.driver, x.year)):
+        name, unit = label.get(r.factor_key, ({"growth": "营收增速", "gm": "毛利率"}
+                                              .get(r.driver, r.driver), ""))
+        read = " / ".join(assume._fmt_factor(v, unit) if unit else f"{v:.2%}"
+                          for v in (r.bear, r.base, r.bull))
+        raw = " / ".join(r.raw) if r.raw else "（无原文）"
+        lines.append(f"- {name} 第{r.year}年：原文 {raw} → 读作 {read}"
+                     + (f"（单位 {unit}）" if unit else ""))
+    facts = getattr(a, "facts", None)
+    if m is not None and facts is not None and not facts.blocking:
+        lines += ["", "【代码按公式合成的三档】"]
+        try:
+            got = {c: assume.synthesize(a, c, a.base_margin
+                                        if a.base_margin is not None else Decimal(0),
+                                        a.margin_bases)
+                   for c in (assume.Case.BEAR, assume.Case.BASE, assume.Case.BULL)}
+            for y in range(len(got[assume.Case.BASE][0])):
+                g = " / ".join(f"{got[c][0][y]:.2%}" for c in got)
+                gm = " / ".join(f"{got[c][1][y]:.2%}" for c in got)
+                lines.append(f"- 第{y + 1}年 营收增速 {g}；毛利率 {gm}（悲观 / 中性 / 乐观）")
+        except assume.AssumeFailed as e:
+            lines.append(f"- 算不出来：{e}")
+    if a.implied is not None:
+        lines += ["", f"【算术后果】{a.implied.describe()}"]
+    bal = [b for b in balances or () if getattr(b, "mid", None) is not None]
+    if bal:
+        lines += ["", "【辩论② 多空平衡点】"]
+        lines += [f"- {b.subject}：悲观 {b.bear} / 平衡 {b.mid} / 乐观 {b.bull}"
+                  f"（{b.note}）" for b in bal]
+    if a.method_review:
+        lines += ["", "【方法论审核遗留】"] + list(a.method_review)
+    if m is not None and ledger is not None:
+        decl = MR.ledger_decl_lines(m, ledger=ledger, as_of=as_of)
+        if decl:
+            lines += [""] + decl
+    lines += ["", "【代码读数与核对】", facts.text() if facts else "（未核对）"]
+    return "\n".join(lines)
+
+
+def review(judge, *, draft: str, materials: str, facts: str, catalog: str,
+           expected: list, hay: str = "", ledger=None, as_of=None) -> ReviewResult:
+    """审核者作答一次（漏答补答最多 2 次，不消耗填写者轮数）。"""
+    items = "\n".join(f"- [{k}] {s}" for k, s in expected)
+    base = (f"{RULES}\n**必须逐条作答的条目**\n{items}\n\n"
+            f"**填写者交的判断项（原文）**\n{draft}\n\n"
+            f"**只给你的、代码算出的事实**\n{facts}\n\n"
+            f"**账本事实目录**（可引用的占位符）\n{catalog or '（无）'}\n\n"
+            f"**填写者看到的资料**\n{materials}\n")
+    prompt, last, attempts = base, "", []
+    for _ in range(JUDGE_TRIES):
+        raw = judge(prompt)
+        try:
+            verdicts = parse(raw, expected)
+        except ReviewIncomplete as e:
+            last = str(e)
+            attempts.append((raw, last))
+            prompt = f"{base}\n---\n**上一次回答不完整**：{last}\n请补全后重新输出全部条目。\n"
+            continue
+        attempts.append((raw, ""))
+        return ReviewResult(verdicts=MR.annotate_quotes(verdicts, hay=hay,
+                                                        ledger=ledger, as_of=as_of),
+                            attempts=attempts)
+    err = ReviewIncomplete(last)
+    err.attempts = attempts
+    raise err
+
+
+def fingerprint(a) -> str:
+    """判断项取值的指纹：只算因子、年、三档与基期值，不算理由与依据文字 —— 人在
+    门③ 改一个错别字不该让审核失效，改一个数必须看得出来（spec 三）。"""
+    rows = sorted((r.factor_key or r.driver, r.year, str(r.bear), str(r.base),
+                   str(r.bull)) for r in a.rows)
+    bases = sorted((k, str(v.value)) for k, v in a.base_values.items())
+    blob = json.dumps([rows, bases], ensure_ascii=False)
+    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
+
+
+def dump_review(res: ReviewResult, *, passed: bool) -> str:
+    return json.dumps({**res.to_json(), "passed": passed}, ensure_ascii=False, indent=2)
+
+
+def review_markdown(res: ReviewResult) -> list[str]:
+    """assumptions.md「判断项审核」一节：第几轮通过；提醒、待采集逐条列（带建议）；
+    通过只给条数。"""
+    c = res.counts()
+    lines = ["⚖️ **判断项审核**（AI 审核者，代码未做任何判定）：", "",
+             f"第 {res.rounds} 轮通过：通过 {c['通过']} 条、提醒 {c['提醒']} 条、"
+             f"待采集 {c['待采集']} 条。", ""]
+    lines += [f"- {v.line()}" for v in res.flagged()]
+    if res.flagged():
+        lines.append("")
+    return lines
+
+
+def rounds_markdown(rounds: list[Round]) -> str:
+    """`assumptions_rounds.md`：每一轮的完整记录。"""
+    lines = ["# 判断项审核：各轮记录", ""]
+    for r in rounds:
+        lines += [f"## 第 {r.n} 轮", "", "### 填写者原文", "", *MR._fenced(r.raw), ""]
+        if r.facts:
+            lines += ["### 代码事实（只是事实，不驳回）", "", r.facts, ""]
+        if r.incomplete:
+            lines += ["### 审核：未完成", "", r.incomplete, ""]
+        if r.review:
+            c = r.review.counts()
+            lines += [f"### 审核：{'驳回' if r.review.fatal() else '通过'}"
+                      f"（致命 {c['致命']}、提醒 {c['提醒']}、待采集 {c['待采集']}、"
+                      f"通过 {c['通过']}）", ""]
+            lines += [f"- {v.line()}" for v in r.review.verdicts]
+            lines.append("")
+        for i, (raw, problem) in enumerate(r.attempts, 1):
+            lines += [f"### 审核者原文（第 {i} 次作答）", "", *MR._fenced(raw), ""]
+            if problem:
+                lines += [f"→ 不完整：{problem}", ""]
+    return "\n".join(lines)
+
+
+def fill_and_review(filler, judge, *, judg: dict, labels: list[str], context: str,
+                    ledger, body: str, as_of, code: str = "", decomps=None,
+                    factor_data: dict | None = None, base_margin=None,
+                    anchor: dict | None = None, balances=None, catalog: str = "",
+                    method_review=(), max_rounds: int = MAX_ROUNDS):
+    """填写 → 代码读数与计算（不驳回）→ 审核 → 汇总。返回 (AssumptionSet, ReviewResult,
+    rounds)。
+
+    有致命即带着建议退回重填，合计最多 `max_rounds` 轮；审核者答不全、3 轮仍有致命、
+    或审核通过但代码确实算不下去 → `ReviewStop`（停下交人工）。模型接口错误原样
+    抛出，由调用方说明是接口问题。**不退回两标量模式**：两标量只在取不到行业分类
+    （`decomps` 为空）时出现，审核照跑。
+    """
+    from ir_agent import assume
+    from ir_agent.formula import as_model
+
+    model = as_model(decomps)
+    ledger_bases = {}
+    if model is not None and not model.legacy:
+        probe = assume.AssumptionSet(code="", labels=[], model=model)
+        assume._fill_from_ledger(probe, ledger, as_of)
+        ledger_bases = probe.base_values
+    prompt = assume.build_prompt(judg, labels, context, brief=body, decomps=decomps,
+                                 factor_data=factor_data, ledger_bases=ledger_bases)
+    materials = prompt
+    hay = "\n".join([body, *[getattr(fd, "source_excerpt", "") or ""
+                             for fd in (factor_data or {}).values()]])
+    rounds: list[Round] = []
+    a = None
+    for n in range(1, max_rounds + 1):
+        raw = filler(prompt)
+        a = assume.parse(raw, len(labels), decomps=decomps,
+                         factor_data=factor_data, as_of=as_of)
+        a.code, a.labels = code, labels
+        a.factor_data = dict(factor_data or {})
+        a.base_margin = base_margin
+        a.method_review = list(method_review or [])
+        facts = assume.check(a, len(labels), ledger=ledger, body=body, as_of=as_of,
+                             decomps=decomps, base_margin=base_margin, anchor=anchor,
+                             balances=balances)
+        a.decomps = list(decomps) if isinstance(decomps, list) else []
+        a.model = model
+        if model is not None and not facts.blocking:
+            a.residuals = assume._reconcile_model(model, ledger, as_of)
+        ftxt = facts_for_judge(a, balances=balances, ledger=ledger, as_of=as_of)
+        try:
+            res = review(judge, draft=raw, materials=materials, facts=ftxt,
+                         catalog=catalog, expected=checklist(a, balances),
+                         hay=hay, ledger=ledger, as_of=as_of)
+        except ReviewIncomplete as e:
+            rounds.append(Round(n, raw, ftxt, incomplete=str(e),
+                                attempts=getattr(e, "attempts", [])))
+            raise ReviewStop(f"判断项审核未完成：审核者补答 {JUDGE_TRIES - 1} 次仍不全"
+                             f"（{str(e)[:120]}）", rounds, last=a) from e
+        res.rounds = n
+        res.fingerprint = fingerprint(a)
+        rounds.append(Round(n, raw, ftxt, review=res, attempts=res.attempts))
+        if not res.fatal():
+            if facts.blocking:
+                raise ReviewStop("判断项审核已通过，但无法计算："
+                                 + "；".join(facts.blocking), rounds, last=a)
+            a.judge_review = res
+            return a, res, rounds
+        prompt = f"{prompt}\n\n---\n{MR._feedback(n, res)}\n"
+    raise ReviewStop(f"判断项审核 {max_rounds} 轮未通过（最后一轮致命 "
+                     f"{len(rounds[-1].review.fatal())} 条）", rounds, last=a)
diff --git a/ir_agent/methodology_review.py b/ir_agent/methodology_review.py
index 8a18eb1..0a42b54 100644
--- a/ir_agent/methodology_review.py
+++ b/ir_agent/methodology_review.py
@@ -244,26 +244,36 @@ def annotate_quotes(verdicts: list[Verdict], *, hay: str, ledger, as_of) -> list
     return out
 
 
+def ledger_decl_lines(model, *, ledger, as_of) -> list[str]:
+    """因子数据线索里 `[[key]]` 声明在账本里实际取到的值。方法论审核者与判断项
+    审核者（子项目三）共用 —— 第 2 轮那个 100 倍量级错误就是靠这一段对出来的
+    （叙述里 0.87pp，账本里 0.00871）。"""
+    from ir_agent import gaps
+
+    decl = [(f, k) for f in model.factors
+            for k in dict.fromkeys(_KEY_DECL.findall(f.source_hint or ""))]
+    if not decl:
+        return []
+    lines = ["【账本 key 声明的实际取值】"]
+    for f, k in decl:
+        have, _ = gaps.ledger_refs(f"[[{k}]]", ledger, as_of) if ledger else ([], [])
+        if have:
+            key, per = have[0].rsplit("@", 1)
+            lines.append(f"- {f.short} [[{k}]]：账本 {key}@{per} = "
+                         f"{ledger.get(key, per, as_of=as_of).value}")
+        else:
+            lines.append(f"- {f.short} [[{k}]]：账本里没有（或晚于研究时点才披露）")
+    return lines
+
+
 def extra_facts(model, *, ledger, as_of, sections=None, corpus=None,
                 themes=(), coverage_decl=()) -> str:
     """只给审核者看的、代码现算的事实（spec 二）。"""
     from ir_agent import gaps, methodology
     from ir_agent.formula import FormulaError, evaluate
 
-    lines: list[str] = []
     # 1. [[key]] 声明在账本里实际取到的值
-    decl = [(f, k) for f in model.factors
-            for k in dict.fromkeys(_KEY_DECL.findall(f.source_hint or ""))]
-    if decl:
-        lines += ["【账本 key 声明的实际取值】"]
-        for f, k in decl:
-            have, _ = gaps.ledger_refs(f"[[{k}]]", ledger, as_of) if ledger else ([], [])
-            if have:
-                key, per = have[0].rsplit("@", 1)
-                lines.append(f"- {f.short} [[{k}]]：账本 {key}@{per} = "
-                             f"{ledger.get(key, per, as_of=as_of).value}")
-            else:
-                lines.append(f"- {f.short} [[{k}]]：账本里没有（或晚于研究时点才披露）")
+    lines: list[str] = ledger_decl_lines(model, ledger=ledger, as_of=as_of)
     # 2. 检索词命中次数
     hits = methodology.term_hits(model, sections=sections, corpus=corpus)
     lines += ["", "【因子候选词在本轮语料里的命中次数】"]
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2430 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `if not res.fatal():` 改成 `if True:` → 「致命即退回」测试红
- 通过分支里 `if facts.blocking:` 改成 `if False:` → 「审核通过但算不下去就停下」红
- `return verdicts + extras` 改成 `return verdicts` → 补充测试红
- 补充致命的建议检查 `bad = [...]` 改成 `bad = []` → 「补充判致命也要附建议」红
- `[平衡点]` 条目的 `if f.label in mids` 改成 `if False` → 清单测试红
- `facts_for_judge` 去掉「原文 → 读作」那一行 → 「原文与读数并排」红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(assume_review): 判断项审核者 —— 5 类清单、补充、三轮、停下交人工

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: decide 改为填写者 + 审核者

**Files:**
- Modify: `ir_agent/assume.py`
- Modify: `ir_agent/assume_review.py`
- Modify: `ir_agent/full.py`
- Modify: `tests/test_assume.py`

**Interfaces:**
- Consumes: `assume_review.fill_and_review`
- Produces: `assume.decide(client, judg, labels, context, *, judge, ledger, body, as_of, code='', decomps=None, factor_data=None, base_margin=None, anchor=None, balances=None, catalog='', method_review=()) -> AssumptionSet`（失败抛 `assume_review.ReviewStop`）；`AssumptionSet.judge_rounds`

**为什么：** `assume.decide` 改成 `assume_review.fill_and_review` 的薄包装：必须显式传 `judge`（两个角色），删掉 `_decide_once` 重试循环与「因子模式连续失败退回两标量」（I4）。填写者提示词里「会被直接驳回」改为「审核者会看到」，并说明稿子由审核者逐项审核、致命带建议退回、最多 3 轮。full 先做最小接线（传 `judge=client`、接住 `ReviewStop`），Task 6 完整接线。

- [ ] **Step 1: 写失败测试**

```diff
diff --git a/tests/test_assume.py b/tests/test_assume.py
index fa901ac..3dde551 100644
--- a/tests/test_assume.py
+++ b/tests/test_assume.py
@@ -13,6 +13,7 @@ from ir_agent.assume import AssumeFailed, Case
 from ir_agent.ledger import FactLedger
 from ir_agent.sources.external import Tier
 from ir_agent.sources.web import Finding
+from judge_stub import Judge
 
 
 def _notes(a) -> str:
@@ -159,15 +160,14 @@ def test_提示词点明边界与拉开():
     assert "不是理由" in txt          # 不许复述历史当依据
 
 
-def test_连续失败则拒绝():
-    class _C:
-        def __init__(self): self.n = 0
-        def __call__(self, p):
-            self.n += 1
-            return "假设: 营收增速 | 第1年 | 5% | 5% | 5% | 「x」"
-    with pytest.raises(AssumeFailed, match="连续"):
-        assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
-                      ledger=FactLedger(), body=BODY, as_of=AS_OF)
+def test_审核三轮不过则停下交人工():
+    """子项目三：合格与否由审核者判。三轮都有致命 → 停下，带着最后一稿。"""
+    from ir_agent.assume_review import ReviewStop
+    with pytest.raises(ReviewStop, match="3 轮未通过") as e:
+        assume.decide(lambda p: _full(1), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
+                      judge=Judge({"合成": "致命"}), ledger=FactLedger(), body=BODY,
+                      as_of=AS_OF)
+    assert e.value.last is not None and len(e.value.rounds) == 3
 
 
 # ── 理由必须进清单 ───────────────────────────────────────────
@@ -603,7 +603,7 @@ def test_decide把reconcile的结果存进residuals():
             f"假设: 单价 | 第1年 | -6% | -2% | 1% | 竞争压价 「{Q}」"])
 
     a = assume.decide(client, {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
-                      ledger=led, body=BODY, as_of=AS_OF, code="600584",
+                      judge=Judge(), ledger=led, body=BODY, as_of=AS_OF, code="600584",
                       decomps=d)
     assert "revenue" in a.residuals
     res = a.residuals["revenue"]["residuals"]
@@ -635,7 +635,7 @@ def test_历史值取不到标为无历史校验而不是静默跳过():
             f"假设: 单价 | 第1年 | -6% | -2% | 1% | 竞争压价 「{Q}」"])
 
     a = assume.decide(client, {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
-                      ledger=led, body=BODY, as_of=AS_OF, code="600584",
+                      judge=Judge(), ledger=led, body=BODY, as_of=AS_OF, code="600584",
                       decomps=d)
     assert a.residuals["revenue"]["no_history"] == ["单价"]
     txt = a.describe()
@@ -1021,53 +1021,19 @@ def test_全角减号在标量模式下解析出正确符号():
 # 连 assumptions.md 都不写。硬约束「降级但继续、并如实说明降级原因」
 # 在这一层缺位：能力边界不是故障，整轮研究不该因此停摆。
 
-def test_因子模式连续失败后回落两标量模式():
-    """实测复现：因子模式怎么喂都给不出合格的行（写了个分解式里根本
-    不存在的因子名），修复前这里会直接抛 AssumeFailed，调用方拿到的
-    只有一句错误串，整段判断项凭空消失——这条测试在修复前必须失败。"""
-    class _C:
-        def __call__(self, p):
-            if "本次按驱动因子分解填写" in p:
-                # 因子模式无论怎么重试都给不出合法的因子名
-                return "假设: 不存在的因子 | 第1年 | 1% | 2% | 3% | 「x」"
-            return _full(1)     # 退回两标量模式后能正常给出
-
-    a = assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
-                      ledger=FactLedger(), body=BODY, as_of=AS_OF,
-                      decomps=_decomps())
-    assert a.decomps == [], "应已退回两标量模式，不应再带着分解式"
-    assert {r.driver for r in a.rows} == {"growth", "gm"}
-    assert a.degraded, "必须如实写明降级原因，不能假装成功"
-    assert "因子" in a.degraded
-
-
-def test_因子模式降级原因出现在待核实清单里():
-    """降级但继续——损失的是精度，不是担保，读者必须在清单上看到这件事，
-    而不是打开 assumptions.md 却看不出这轮其实是降级产出的。"""
-    class _C:
-        def __call__(self, p):
-            if "本次按驱动因子分解填写" in p:
-                return "假设: 不存在的因子 | 第1年 | 1% | 2% | 3% | 「x」"
-            return _full(1)
-
-    a = assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
-                      ledger=FactLedger(), body=BODY, as_of=AS_OF,
+def test_因子模式填不出也不退回两标量模式():
+    """生益科技 2026-10-15：因子模式被代码闸门连挂三次后退回两标量，整个因子
+    分解丢失。现在代码不驳回；因子名全写错时一行都读不进来 —— 审核者若放行，
+    就停下说明算不下去，**绝不悄悄换成两标量**。"""
+    from ir_agent.assume_review import ReviewStop
+
+    def _c(p):
+        return "假设: 不存在的因子 | 第1年 | 1% | 2% | 3% | 「x」"
+    with pytest.raises(ReviewStop, match="无法计算") as e:
+        assume.decide(_c, {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
+                      judge=Judge(), ledger=FactLedger(), body=BODY, as_of=AS_OF,
                       decomps=_decomps())
-    a.code, a.labels = "600584", ["2026E"]
-    txt = a.describe()
-    assert a.degraded in txt
-
-
-def test_两标量模式本身连续失败仍照常拒绝():
-    """回归：没有 decomps（本来就是两标量模式）时，连续失败不该被当成
-    "因子模式失败"再退化一次——那样会造成无限递归或行为混乱，必须原样
-    拒绝。"""
-    class _C:
-        def __call__(self, p):
-            return "假设: 营收增速 | 第1年 | 5% | 5% | 5% | 「x」"
-    with pytest.raises(AssumeFailed, match="连续"):
-        assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
-                      ledger=FactLedger(), body=BODY, as_of=AS_OF)
+    assert e.value.last._m() is not None, "仍是因子模式"
 
 
 # ═══════════════════════════════════════════════════════════════
@@ -1358,8 +1324,8 @@ def test_提示词列出的状态等于代码会记进读数事实的状态():
     # 因子模式的提示词才带这段规则（两标量模式没有「无数据（估计）」出口）。
     txt = assume.build_prompt({"g_stats": (), "gm_stats": ()}, ["2026E"], "上下文",
                               decomps=[d], factor_data={})
-    m = re.search(r"开放，对((?:「[^」]+」)+)的因子这么写会被直接", txt)
-    assert m, "提示词里找不到「对…的因子这么写会被直接驳回」这句枚举"
+    m = re.search(r"开放，对((?:「[^」]+」)+)的因子这么写，审核者会看到", txt)
+    assert m, "提示词里找不到「对…的因子这么写，审核者会看到…」这句枚举"
     listed = re.findall(r"「([^」]+)」", m.group(1))
     # 顺序也跟着梯子走：`gaps` 里的常量就是按 ①②②.5③④ 的顺序声明的。
     assert listed == rejected, \
@@ -1455,11 +1421,10 @@ def test_决策层面因子模式一次就能给出合格判断项不再降级()
     client = _C()
     body = "境外收入敞口大小暂无法量化。"
     a = assume.decide(client, {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
-                      ledger=FactLedger(), body=body, as_of=AS_OF,
+                      judge=Judge(), ledger=FactLedger(), body=body, as_of=AS_OF,
                       decomps=[d], factor_data=fd)
     assert client.calls == 1, "应当第一次就通过，不需要重试更不需要降级"
     assert a.decomps == [d], "不应该退回两标量模式——因子分解本该被保留"
-    assert not a.degraded
     assert a.rows[0].bases == []
```

- [ ] **Step 2: 跑测试，确认红**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`6 failed, 2422 passed, 8 skipped`。失败的是：

- `tests/test_assume.py::test_审核三轮不过则停下交人工`
- `tests/test_assume.py::test_decide把reconcile的结果存进residuals`
- `tests/test_assume.py::test_历史值取不到标为无历史校验而不是静默跳过`
- `tests/test_assume.py::test_因子模式填不出也不退回两标量模式`
- `tests/test_assume.py::test_提示词列出的状态等于代码会记进读数事实的状态`
- `tests/test_assume.py::test_决策层面因子模式一次就能给出合格判断项不再降级`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index a9aa6cc..6d18ac8 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -371,6 +371,8 @@ class AssumptionSet:
     # 判断项审核结论（`methodology_review.ReviewResult`，子项目三）。派生量，不进
     # JSON —— 落盘在 assumptions_review.json。
     judge_review: object = None
+    # 判断项审核的各轮记录（`assume_review.Round`），写 assumptions_rounds.md 用。
+    judge_rounds: list = field(default_factory=list)
 
     def _m(self) -> FormulaModel | None:
         if self.model is None and self.decomps:
@@ -1735,6 +1737,10 @@ PROMPT = """把前面的分析结论，落到 DCF 模型的判断项上。
 **各层分析口径摘要**（供你判断，**不是可引用的正文**）
 {context}
 
+**你交的稿子由审核者逐项审核**（代码不做判定，只把读到的值、算出的路径、引文
+核验结果交给审核者）。有致命项，会带着修改建议退回给你重填，最多 3 轮 ——
+被退回时按建议改，并重新输出全部行。
+
 **硬性要求**
 - 三档必须**严格拉开**（悲观 < 中性 < 乐观）。模板默认三档同值，
   那等于没有情景 —— 拉不开的情景分析只是把同一个数抄三遍。
@@ -1775,21 +1781,20 @@ _BASIS_RULES = (
               "「无数据（估计）」「同上」回指——这几类代码都能逐字反查，"
               "人工批准时不用再多看一眼。不要用省略号（…/...）缩写引文，"
               "也不要把原文里不相邻的句子拼起来当一句引文。",
-              "**引文核验不过不会被驳回，但会被标注。** 因子分解要填的"
+              "**引文核验不过会被标注，并交给审核者看。** 因子分解要填的"
               "行很多（一个分解式常有好几个因子、好几个预测年），逐字"
               "引文有时确实凑不出来——若引文没能在来源里逐字找到，这一"
-              "行仍会通过，只是会在待核实清单上被显著标出「依据未能"
+              "行仍会读进来，只是会在待核实清单上被显著标出「依据未能"
               "逐字核验」，与核验通过的行一眼可分，需要人工多担一分"
               "风险去复核。**这不等于随便写都行**：标注是有代价的——"
               "能给出逐字引文就一定给逐字引文，只有在你要表达的意思在"
               "来源里确实没有逐字对应的句子时，才退而求其次转述，并把"
               "推理写进理由列；转述得越多，清单上被标注的行就越多，人工"
               "复核的负担就越重。",
-              "**这条放宽只针对引文本身，账本占位符不受影响**："
-              "`[[key@period]]` 必须真实指向账本里存在的事实，写错的"
-              "占位符（账本里根本没有这个 key/period）依旧会被直接驳回"
-              "——那是对账本的假陈述，下游会拿它去做字符串替换，错了会"
-              "让整段估值替换失败，跟「转述 vs 逐字」不是一类问题。",
+              "**账本占位符要格外小心**："
+              "`[[key@period]]` 必须真实指向账本里存在的事实——写错的"
+              "占位符（账本里根本没有这个 key/period）是对账本的假陈述，"
+              "审核者会看到它在账本里不存在，跟「转述 vs 逐字」不是一类问题。",
               "",
               "**依据除了「正文原话」「[[占位符]]」，还有三条路——"
               "很多因子正文根本没提过，也不在账本里，这些路才是它们"
@@ -1803,8 +1808,8 @@ _BASIS_RULES = (
               "写「无数据（估计）」，但理由里**必须**说明这个估计是怎么"
               "来的（参照哪个可比公司、哪个行业惯例、哪条常识……）——"
               "「无数据」只对确实没有数据支撑的因子开放，对「已披露」"
-              "「年报披露」「公告披露」「研报测算」「外部来源」的因子这么写"
-              "会被直接驳回，不能图省事整表都写「无数据」。",
+              "「年报披露」「公告披露」「研报测算」「外部来源」的因子这么写，"
+              "审核者会看到它其实有数据——不能图省事整表都写「无数据」。",
               "③ **同一个因子在不同年份的依据经常是同一段引文**"
               "（同一份年报表述往往能支撑好几个年份的判断）——从第 2 次"
               "出现该因子起，依据可以直接写「同上」「同上引文」等"
@@ -1813,7 +1818,7 @@ _BASIS_RULES = (
               "**「同上」指的是同一因子上一年的依据，不是这一行上面紧邻"
               "的那一行**——那有可能是别的因子。**第一次出现该因子时"
               "必须给出完整依据**（①②或「正文原话」「[[占位符]]」"
-              "之一），没有上一行可回指的「同上」会被直接驳回。理由"
+              "之一），没有上一行可回指的「同上」，审核者会看到它没有依据。理由"
               "（为什么这一年这么判断）不受影响，仍然要每年单独写"
               "清楚——「同上」只省依据，不省理由。",
 )
@@ -1956,103 +1961,34 @@ def build_prompt(judg: dict, labels: list[str], context: str,
                          context=context, brief=brief or "（无）")
 
 
-def _decide_once(client, judg: dict, labels: list[str], context: str, *,
-                 ledger: FactLedger, body: str, as_of: date, code: str,
-                 max_attempts: int, decomps,
-                 factor_data: dict | None,
-                 base_margin: Decimal | None,
-                 anchor: dict | None = None,
-                 balances: list | None = None) -> AssumptionSet:
-    """单一模式（因子或两标量）内的重试循环。
-
-    只管一种模式，失败就抛 `AssumeFailed`——**模式之间要不要降级，是
-    `decide()` 的职责，不是这里的**：因子模式连续失败后可以退回两标量
-    模式再试一次，但两标量模式本身连续失败没有更低的台阶可退，必须
-    原样拒绝。两件事分开才不会在这里意外递归。
-    """
-    model = as_model(decomps)
-    ledger_bases = {}
-    if model is not None and not model.legacy:
-        probe = AssumptionSet(code="", labels=[], model=model)
-        _fill_from_ledger(probe, ledger, as_of)
-        ledger_bases = probe.base_values
-    prompt = build_prompt(judg, labels, context, brief=body, decomps=decomps,
-                          factor_data=factor_data, ledger_bases=ledger_bases)
-    last, raw = "", ""
-    for attempt in range(1, max_attempts + 1):
-        raw = client(prompt)
-        try:
-            a = parse(raw, len(labels), decomps=decomps,
-                     factor_data=factor_data, as_of=as_of)
-            facts = check(a, len(labels), ledger=ledger, body=body, as_of=as_of,
-                          decomps=decomps, base_margin=base_margin, anchor=anchor,
-                          balances=balances)
-            if facts.blocking:
-                raise AssumeFailed("算不下去：" + "；".join(facts.blocking))
-        except AssumeFailed as e:
-            last = str(e)
-            prompt = (f"{prompt}\n\n---\n"
-                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
-                      "请修正后重新输出全部行。\n")
-            continue
-        a.code, a.labels = code, labels
-        a.decomps = list(decomps) if isinstance(decomps, list) else []
-        a.model = model
-        a.factor_data = dict(factor_data or {})
-        a.base_margin = base_margin
-        if model is not None:
-            # C2：reconcile() 写得对、测试充分，缺的是「在真实流程里被
-            # 调用一次并把结果交给人看」这个动作。
-            a.residuals = _reconcile_model(model, ledger, as_of)
-        return a
-    raise AssumeFailed(
-        f"连续 {max_attempts} 次给不出可核验的假设：{last}"
-        f"\n最后一次的原始输出（末 400 字）：\n{raw.strip()[-400:]}",
-        raw=raw)
 
 
 def decide(client, judg: dict, labels: list[str], context: str, *,
-           ledger: FactLedger, body: str, as_of: date, code: str = "",
-           max_attempts: int = 3, decomps=None,
-           factor_data: dict | None = None,
-           base_margin: Decimal | None = None,
-           anchor: dict | None = None,
-           balances: list | None = None) -> AssumptionSet:
-    """AI 填判断项，因子模式连续失败时回落到两标量路径（I4）。
-
-    因子模式要填 N 因子 × Y 年 × 3 档，比两标量模式的 2×5×3 重数倍，
-    失败概率明显更高。一旦连续 `max_attempts` 次都填不出合格的因子，
-    直接向上抛错会让**整个 DCF 判断项都没有**——能力边界不是故障，
-    整轮研究不该因此停摆，这里改为退回两标量模式再试一次，并把降级
-    原因记在 `AssumptionSet.degraded` 上（`describe()` 会把它印在清单
-    顶部）：**降级但继续、如实说明降级原因，不能假装成功，也不能中断。**
-
-    两标量模式（`decomps` 为空）本身连续失败没有更低的台阶可退，原样
-    向上抛出。
+           judge, ledger: FactLedger, body: str, as_of: date, code: str = "",
+           decomps=None, factor_data: dict | None = None,
+           base_margin: Decimal | None = None, anchor: dict | None = None,
+           balances: list | None = None, catalog: str = "",
+           method_review=()) -> AssumptionSet:
+    """AI 填判断项，AI 审核（子项目三，spec 2026-10-15）。
+
+    `client` 是填写者，`judge` 是审核者（必须显式给 —— 两个角色）。流程见
+    `assume_review.fill_and_review`：代码只读数、计算、核对，把结果作为事实交给
+    审核者；有致命即带着建议退回重填，最多 3 轮；仍不通过、审核者答不全、或审核
+    通过但算不下去 → `assume_review.ReviewStop`，停下交人工。
+
+    **不再退回两标量模式。** 此前因子模式连续 3 次被代码闸门拒绝就退回两标量
+    （I4）—— 生益科技 2026-10-15 的因子分解就是这样丢的，而三次被拒都与分解式
+    内容无关。两标量只在取不到行业分类（`decomps` 为空）时出现。
     """
-    try:
-        return _decide_once(client, judg, labels, context, ledger=ledger,
-                            body=body, as_of=as_of, code=code,
-                            max_attempts=max_attempts, decomps=decomps,
-                            factor_data=factor_data, base_margin=base_margin,
-                            anchor=anchor, balances=balances)
-    except AssumeFailed as e:
-        if not decomps:
-            raise
-        a = _decide_once(client, judg, labels, context, ledger=ledger,
-                         body=body, as_of=as_of, code=code,
-                         max_attempts=max_attempts, decomps=None,
-                         # **降级路径也要带 balances。** 两标量模式下
-                         # `_row_label` 返回中文驱动名，而辩论② 在预研失败时
-                         # 正是用 `DRIVERS` 的键做 subjects —— 匹配得上。
-                         # 不传就等于「降级之后这条约束静默失效」。
-                         factor_data=None, base_margin=None,
-                         balances=balances)
-        a.degraded_raw = getattr(e, "raw", "")
-        a.degraded = (f"因子模式连续 {max_attempts} 次填不出合格的判断项"
-                      f"（{str(e)[:160]}）—— 已退回两标量模式（营收增速/"
-                      "毛利率），损失的是因子层面的精度，不是担保")
-        return a
+    from ir_agent import assume_review
+
+    a, res, rounds = assume_review.fill_and_review(
+        client, judge, judg=judg, labels=labels, context=context, ledger=ledger,
+        body=body, as_of=as_of, code=code, decomps=decomps,
+        factor_data=factor_data, base_margin=base_margin, anchor=anchor,
+        balances=balances, catalog=catalog, method_review=method_review)
+    a.judge_rounds = rounds
+    return a
 
 
 def _historical_series(ledger: FactLedger, key: str, periods: list[str],
diff --git a/ir_agent/assume_review.py b/ir_agent/assume_review.py
index 7ac8b81..d6b1104 100644
--- a/ir_agent/assume_review.py
+++ b/ir_agent/assume_review.py
@@ -173,10 +173,10 @@ def facts_for_judge(a, *, balances=(), ledger=None, as_of=None) -> str:
                                         if a.base_margin is not None else Decimal(0),
                                         a.margin_bases)
                    for c in (assume.Case.BEAR, assume.Case.BASE, assume.Case.BULL)}
-            for y in range(len(got[assume.Case.BASE][0])):
-                g = " / ".join(f"{got[c][0][y]:.2%}" for c in got)
-                gm = " / ".join(f"{got[c][1][y]:.2%}" for c in got)
-                lines.append(f"- 第{y + 1}年 营收增速 {g}；毛利率 {gm}（悲观 / 中性 / 乐观）")
+            for i, cn in ((0, "营收增速"), (1, "毛利率")):
+                for y in range(len(got[assume.Case.BASE][i])):
+                    v = " / ".join(f"{got[c][i][y]:.2%}" for c in got)
+                    lines.append(f"- 第{y + 1}年 {cn} {v}（悲观 / 中性 / 乐观）")
         except assume.AssumeFailed as e:
             lines.append(f"- 算不出来：{e}")
     if a.implied is not None:
diff --git a/ir_agent/full.py b/ir_agent/full.py
index c6a0d51..a35c3ae 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -2436,7 +2436,7 @@ def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
     采集的产出），否则退回两标量模式 —— 预研失败时的降级路径，见
     `_research_methodology`。
     """
-    from ir_agent import assume
+    from ir_agent import assume, assume_review
 
     try:
         cal = dcf.calibrate_only(code, as_of=as_of, ledger=r.ledger)
@@ -2460,7 +2460,9 @@ def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
                      # 辩论② 的平衡三元组：偏离它的行必须填 deviation。
                      balances=balances)
     try:
-        a = assume.decide(client, cal["judg"], labels, ctx, **decide_kw)
+        a = assume.decide(client, cal["judg"], labels, ctx, judge=client, **decide_kw)
+    except assume_review.ReviewStop as e:
+        return f"DCF 判断项（{e}）"
     except assume.AssumeFailed as e:
         return f"DCF 判断项（{str(e)[:200]}）"
     except ClaudeCliError as e:
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2428 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `decide` 里把 `judge` 换成 `client`（填写者兼审核者）→ 三轮测试、不退回两标量测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(assume): decide 改为填写者 + 审核者，删掉重试循环与两标量降级

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: full 接上审核者：落盘、清单、终端

**Files:**
- Modify: `ir_agent/assume.py`
- Delete: `ir_agent/factor_review.py`
- Modify: `ir_agent/formula.py`
- Modify: `ir_agent/full.py`
- Modify: `ir_agent/research.py`
- Modify: `tests/test_assume.py`
- Delete: `tests/test_assume_refill.py`
- Delete: `tests/test_factor_review.py`
- Modify: `tests/test_full_context.py`
- Modify: `tests/test_research.py`

**Interfaces:**
- Consumes: `assume.decide(judge=, catalog=, method_review=)`、`assume_review.ReviewStop/dump_review/rounds_markdown/review_markdown`
- Produces: `full.ASSUMPTIONS_REVIEW_JSON`；`research.ASSUMPTIONS_ROUNDS_MD`、`research.ASSUMPTIONS_FAILED_JSON`；`full._stop_for_assume_review(out, arts, e, *, code) -> str`

**为什么：** `_propose_assumptions` 把账本事实目录交给审核者、把方法论遗留交给 `decide`；删掉 `factor_review`（模块与测试）、判断项假设辩论、`3c23f30` 的对账矛盾重填（`_refill_on_contradiction`、`refill_note`、`tests/test_assume_refill.py`；其 pp 提示词测试挪进 `test_assume.py`）。通过 → `assumptions.json/.md` + `assumptions_review.json`（passed、指纹）+ `assumptions_rounds.md`；停下 → `_stop_for_assume_review` 只写各轮记录、最后一稿原文（`FAILED_RAW`）与 `assumptions_failed.json` 底稿，**不写 assumptions.json**。`describe()` 去掉降级、🔁、🔍、⚖️ 四节与对应字段，加「判断项审核」一节；两标量清单补 ⚠ 标注。`_assumption_caveat_note` 改报审核的提醒/待采集；`research._PREFIX` 登记「判断项审核」「判断项未经审核：」；`next_actions` 在有底稿时给第二条做法。

- [ ] **Step 1: 写失败测试**

删除：`tests/test_assume_refill.py`、`tests/test_factor_review.py`

```bash
git rm tests/test_assume_refill.py tests/test_factor_review.py
```

```diff
diff --git a/tests/test_assume.py b/tests/test_assume.py
index 3dde551..15c2edd 100644
--- a/tests/test_assume.py
+++ b/tests/test_assume.py
@@ -2581,3 +2581,18 @@ def test_七段的行_第六段起全算依据栏():
     assert [b.describe() for b in r.bases] and "revenue_yoy" in r.bases[0].describe()
     assert r.note.startswith("中性档采纳辩论平衡点20%") and not r.note.endswith("|")
     assert "缺少可核验的依据" not in _notes(a)
+
+
+def test_提示词讲清pp填百分点数字本身():
+    """模型把 5.47 个百分点填成了 0.0547。pp 的规则原先只说「不带 %」，没说
+    「填的是百分点数字本身，不是小数」。"""
+    from ir_agent.methodology import parse
+    m = parse("模式: 变化\n"
+              "因子: 提价 | 提价对毛利率的累计贡献 | pp | 调价函 | 调价\n"
+              "因子: 原料 | 原材料对毛利率的累计侵蚀 | pp | 年报 | 原材料价格\n"
+              "因子: 量 | 销量增速 | 变化率 | 年报 | 销量\n"
+              "因子: 价 | 单价增速 | 变化率 | 年报 | 单价\n"
+              "公式: 营收 = 上期(营收) × (1 + 量) × (1 + 价)\n"
+              "公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100\n").model
+    head = assume._formula_head(m, {})
+    assert "写 5.47，不写 0.0547" in head
diff --git a/tests/test_full_context.py b/tests/test_full_context.py
index b82237a..4975e3f 100644
--- a/tests/test_full_context.py
+++ b/tests/test_full_context.py
@@ -4,6 +4,7 @@ from __future__ import annotations
 
 import ast
 import inspect
+import json
 import textwrap
 from datetime import date
 from decimal import Decimal
@@ -559,16 +560,14 @@ def test_第二阶段必须重建派生事实():
 
 # ── I4：因子模式连续失败要回落两标量而非让整段判断项消失 ─────────
 
-def test_因子模式降级时判断项仍写出且降级原因入skipped(tmp_path, monkeypatch):
-    """I4 修复的落地验证：`assume.decide` 拿到 `AssumptionSet.degraded`
-    后，`_propose_assumptions` 仍要把判断项写出来（不是像
-    `assume.AssumeFailed` 那样整段消失），并把降级原因作为非致命说明
-    通过返回值交给调用方——`run_analysis` 会把它塞进 `skipped`，读者
-    在终端产物清单上就能看到这轮其实是降级产出的。"""
+def test_判断项审核停下时只写底稿与记录_不写assumptions_json(tmp_path, monkeypatch):
+    """子项目三：审核 3 轮不过 → 停下交人工。assumptions.json 是门③ 批准的对象，
+    未通过审核的稿子不能冒充它；底稿写 assumptions_failed.json，最后一稿原文
+    **完整**落盘（因子模式一次输出几十行，故障常在中段），各轮记录落盘。"""
     from datetime import date
     from decimal import Decimal
 
-    from ir_agent import assume, dcf, full
+    from ir_agent import assume, assume_review, dcf, full
     from ir_agent.ledger import FactLedger
 
     monkeypatch.setattr(
@@ -576,15 +575,60 @@ def test_因子模式降级时判断项仍写出且降级原因入skipped(tmp_pa
         lambda code, as_of, ledger: {
             "judg": {"g_stats": (), "gm_stats": ()},
             "periods": {"proj": ["2026E"]}})
+    full_raw = "行一\n" * 200 + "中段才出问题的那一行"
+
+    def _fake_decide(client, judg, labels, ctx, **kw):
+        last = assume.AssumptionSet(code="600584", labels=labels)
+        last.rows = [assume.Row(driver="growth", year=1, bear=Decimal("0.02"),
+                                base=Decimal("0.06"), bull=Decimal("0.10"),
+                                note="x", bases=[])]
+        raise assume_review.ReviewStop(
+            "判断项审核 3 轮未通过（最后一轮致命 1 条）",
+            [assume_review.Round(1, full_raw)], last=last)
+    monkeypatch.setattr(assume, "decide", _fake_decide)
+
+    class _R:
+        ledger = FactLedger()
+        period = "2025FY"
+
+    arts: dict[str, str] = {}
+    err = full._propose_assumptions(
+        "600584", _R(), "body", {}, date(2026, 9, 7), tmp_path,
+        lambda p: "", arts)
+
+    assert err.startswith("判断项审核 3 轮未通过")
+    assert not (tmp_path / full.ASSUMPTIONS_JSON).exists()
+    assert full.ASSUMPTIONS_ART not in arts
+    assert (tmp_path / full.ASSUMPTIONS_FAILED_JSON).exists()
+    assert (tmp_path / full.FAILED_RAW).read_text(encoding="utf-8") == full_raw
+    assert "中段才出问题的那一行" in (tmp_path / full.ASSUMPTIONS_ROUNDS_MD).read_text(
+        encoding="utf-8")
 
-    degraded_msg = "因子模式连续 3 次填不出合格的判断项——已退回两标量模式"
+
+def test_判断项审核通过时落审核结论与各轮记录(tmp_path, monkeypatch):
+    from datetime import date
+    from decimal import Decimal
+
+    from ir_agent import assume, assume_review, dcf, full
+    from ir_agent.ledger import FactLedger
+    from ir_agent.methodology_review import ReviewResult, Verdict
+
+    monkeypatch.setattr(
+        dcf, "calibrate_only",
+        lambda code, as_of, ledger: {
+            "judg": {"g_stats": (), "gm_stats": ()},
+            "periods": {"proj": ["2026E"]}})
 
     def _fake_decide(client, judg, labels, ctx, **kw):
+        assert kw["judge"] is client and "catalog" in kw
         a = assume.AssumptionSet(code="600584", labels=labels)
-        a.degraded = degraded_msg
         a.rows = [assume.Row(driver="growth", year=1, bear=Decimal("0.02"),
                              base=Decimal("0.06"), bull=Decimal("0.10"),
                              note="x", bases=[])]
+        a.judge_review = ReviewResult(verdicts=[Verdict(
+            "合成", "营收", "提醒", "略高于行业", suggest="下调中性档")], rounds=2,
+            fingerprint=assume_review.fingerprint(a))
+        a.judge_rounds = [assume_review.Round(1, "第一稿"), assume_review.Round(2, "第二稿")]
         return a
     monkeypatch.setattr(assume, "decide", _fake_decide)
 
@@ -592,17 +636,15 @@ def test_因子模式降级时判断项仍写出且降级原因入skipped(tmp_pa
         ledger = FactLedger()
         period = "2025FY"
 
-    arts: dict[str, str] = {}
     err = full._propose_assumptions(
         "600584", _R(), "body", {}, date(2026, 9, 7), tmp_path,
-        lambda p: "", arts)
-
-    assert degraded_msg in err, "降级原因必须作为非致命说明返回，供 skipped 使用"
-    assert (tmp_path / full.ASSUMPTIONS_MD).exists(), \
-        "判断项必须照常写出，不能因为降级就整段消失"
-    assert degraded_msg in (tmp_path / full.ASSUMPTIONS_MD).read_text(
-        encoding="utf-8"), "assumptions.md 里也要如实写明降级原因"
-    assert "DCF 判断项（待核实）" in arts
+        lambda p: "", {})
+    rev = json.loads((tmp_path / full.ASSUMPTIONS_REVIEW_JSON).read_text(encoding="utf-8"))
+    assert rev["passed"] is True and rev["rounds"] == 2 and rev["fingerprint"]
+    assert "第二稿" in (tmp_path / full.ASSUMPTIONS_ROUNDS_MD).read_text(encoding="utf-8")
+    md = (tmp_path / full.ASSUMPTIONS_MD).read_text(encoding="utf-8")
+    assert "判断项审核" in md and "建议：下调中性档" in md
+    assert "第 2 轮通过" in err and "提醒 1 条" in err
 
 
 def test_因子模式引文未核验的行数入非致命说明(tmp_path, monkeypatch):
@@ -633,6 +675,8 @@ def test_因子模式引文未核验的行数入非致命说明(tmp_path, monkey
                         base=Decimal("0.04"), bull=Decimal("0.09"), note="y",
                         factor_key="cap", bases=[Basis.report("正文里有的话")])
         a.rows = [r1, r2]
+        from ir_agent.methodology_review import ReviewResult
+        a.judge_review = ReviewResult()
         return a
     monkeypatch.setattr(assume, "decide", _fake_decide)
 
@@ -692,58 +736,6 @@ def test_判断项环节的接口错误降级而不抛出(tmp_path, monkeypatch)
     assert "session limit" in err, "原始错误信息要带上，不能只说失败"
 
 
-def test_因子模式降级时完整原始输出要落盘(tmp_path, monkeypatch):
-    """**排查工具本身也要能用。**
-
-    实测教训：因子模式一次要输出几十行（600584 真实分解式是 9 因子 ×
-    5 年 = 45 行），故障常出在中段，而降级说明里只放得下末 400 字。
-    连续几轮排查都只能靠残缺的尾巴反推模型写成了什么格式——最后一次
-    是从 400 字尾巴里认出「贡献**（无数据，估计——依据：…）」这个
-    片段，才判断出模型改用了散文式条目而不是管道分隔行。
-
-    完整草稿必须落盘，但不能混进 assumptions.md——那是给人批准的清单。
-    """
-    from datetime import date
-    from decimal import Decimal
-
-    from ir_agent import assume, dcf, full
-    from ir_agent.ledger import FactLedger
-
-    monkeypatch.setattr(
-        dcf, "calibrate_only",
-        lambda code, as_of, ledger: {
-            "judg": {"g_stats": (), "gm_stats": ()},
-            "periods": {"proj": ["2026E"]}})
-
-    full_raw = "行一\n" * 200 + "中段才出问题的那一行"
-
-    def _fake_decide(client, judg, labels, ctx, **kw):
-        a = assume.AssumptionSet(code="600584", labels=labels)
-        a.degraded = "因子模式连续 3 次填不出合格的判断项"
-        a.degraded_raw = full_raw
-        a.rows = [assume.Row(driver="growth", year=1, bear=Decimal("0.02"),
-                             base=Decimal("0.06"), bull=Decimal("0.10"),
-                             note="x", bases=[])]
-        return a
-    monkeypatch.setattr(assume, "decide", _fake_decide)
-
-    class _R:
-        ledger = FactLedger()
-        period = "2025FY"
-
-    arts: dict[str, str] = {}
-    full._propose_assumptions("600584", _R(), "body", {}, date(2026, 9, 7),
-                              tmp_path, lambda p: "", arts)
-
-    dumped = tmp_path / full.FAILED_RAW
-    assert dumped.exists(), "降级时完整原始输出必须落盘"
-    assert dumped.read_text(encoding="utf-8") == full_raw, "要完整，不能截断"
-    assert "因子模式失败时的原始输出" in arts, "产物清单要指向它"
-    assert "中段才出问题的那一行" not in (
-        tmp_path / full.ASSUMPTIONS_MD).read_text(encoding="utf-8"), \
-        "失败草稿不能混进给人批准的清单"
-
-
 def test_第二阶段先重建派生事实再校验批准的假设():
     """**重建派生事实必须排在校验之前。**
 
@@ -3148,79 +3140,35 @@ def test_判断项客户端的创建早于开销累加():
         "客户端的创建晚于它的开销被累加 —— 运行时必然 UnboundLocalError"
 
 
-def test_因子结构审查接进了判断项环节():
-    """**这条钉的是「接上了」。** 审查本身由 tests/test_factor_review.py 钉；
-    这里查三件事: 真的调了、checklist 取的是写盘前的清单（带审查结果的清单
-    喂回审核者是循环引用）、以及它失败时不毙掉判断项。
-    """
+def test_判断项审核接进了判断项环节_原门三两道审核已并入():
+    """子项目三：`factor_review` [对账] 与判断项假设辩论并进审核者 —— 不再单独跑；
+    审核者拿到账本事实目录（原辩论靠它发现过叙事与周转数据相反）。"""
     import inspect
 
     from ir_agent import full
 
     src = inspect.getsource(full._propose_assumptions)
-    assert "factor_review.review(" in src, "审查没接进来"
-    assert src.index("factor_review.review(") < \
-        src.index("(out / ASSUMPTIONS_MD).write_text"), \
-        "审查必须在写盘之前跑，否则清单上看不到它"
-    # 失败不阻断: 异常被接住、a.review 置空、流程继续写盘
-    assert "a.review = None" in src
+    assert "judge=client" in src and "catalog=catalog" in src
+    assert "factor_review" not in src.split('"""', 2)[2]
+    assert "run_debate(" not in src
 
 
-def test_审查标出的条数要进终端清单():
-    """印在 assumptions.md 顶部还不够 —— 终端产物清单是读者第一眼看到的
-    地方，这与「审计未通过」「判断项待复核」同一处理。"""
+def test_判断项审核的提醒要进终端清单():
     from ir_agent.full import _assumption_caveat_note
+    from ir_agent.methodology_review import ReviewResult, Verdict
 
     class _R:
         basis_unverified = ""
 
-    class _Rev:
-        flagged = [object(), object()]
-
-    class _A:
-        degraded = ""
-        rows = [_R()]
-        review = _Rev()
-
-    note = _assumption_caveat_note(_A())
-    assert note.startswith("判断项待复核：") and "2 项" in note
-
-
-def test_假设辩论接进了判断项环节且不改取值():
-    """第 4 道。**不设闸门、不改数**: 成立的挑战只印在清单上 —— 自动改数会
-    绕过人工门，而那道门的意义正是让人负责。"""
-    import inspect
-
-    from ir_agent import full
-
-    src = inspect.getsource(full._propose_assumptions)
-    assert "run_debate(" in src, "假设辩论没接进来"
-    assert "scale=a.implied.describe()" in src, "没把算术后果喂给质疑者"
-    assert src.index("factor_review.review(") < src.index("run_debate("), \
-        "清单式审查应排在开放式辩论之前（先用便宜且可枚举的）"
-    assert src.index("run_debate(") < \
-        src.index("(out / ASSUMPTIONS_MD).write_text"), \
-        "辩论必须在写盘之前跑，否则清单上看不到它"
-    assert "a.debate = None" in src, "辩论失败应不阻断"
-
-
-def test_假设辩论的成立挑战进终端清单():
-    from ir_agent.full import _assumption_caveat_note
-
-    class _R:
-        basis_unverified = ""
-
-    class _D:
-        upheld_count = 2
-
     class _A:
-        degraded = ""
         rows = [_R()]
-        review = None
-        debate = _D()
+        judge_review = ReviewResult(verdicts=[
+            Verdict("取值", "提价", "提醒", "x"), Verdict("依据", "提价", "待采集", "y"),
+            Verdict("后果", "营收路径", "通过", "")], rounds=2)
 
     note = _assumption_caveat_note(_A())
-    assert "假设辩论有 2 项" in note and "未因此改动" in note
+    assert note.startswith("判断项待复核：")
+    assert "第 2 轮通过" in note and "提醒 1 条" in note and "待采集 1 条" in note
 
 
 def test_预研没收到辩论结论时不做覆盖标注(tmp_path):
diff --git a/tests/test_research.py b/tests/test_research.py
index 677abaf..c126d8e 100644
--- a/tests/test_research.py
+++ b/tests/test_research.py
@@ -291,6 +291,30 @@ def test_第二次与第三次调用未完成时教人重跑自己那一次(tmp_
     assert "--accept-manual" in b2 and "stance_b.template.json" in b2
 
 
+def test_判断项审核停下时教人以底稿改好再跑第二阶段(tmp_path):
+    """子项目三：审核 3 轮不过 → 停下交人工，终端给两条做法：原样重跑 --assume；
+    以 assumptions_failed.json 为底稿改好存为 assumptions.json 再跑 --value。"""
+    import shlex
+
+    from ir_agent.__main__ import build_parser
+    from ir_agent.research import (ASSUMPTIONS_FAILED_JSON, Stage, _mark,
+                                   next_actions)
+
+    (tmp_path / ASSUMPTIONS_FAILED_JSON).write_text("{}", encoding="utf-8")
+    acts = next_actions(Stage.INCOMPLETE, "600183", out_dir=str(tmp_path),
+                        year=2025, phase="assume")
+    txt = "\n".join(acts)
+    assert "--assume" in txt and "--value" in txt and ASSUMPTIONS_FAILED_JSON in txt
+    assert "判断项未经审核" in txt
+    p = build_parser()
+    for line in txt.splitlines():
+        line = line.strip()
+        if line.startswith("python -m ir_agent"):
+            p.parse_args(shlex.split(line.replace("<你的名字>", "xhl"))[3:])
+    assert _mark("判断项审核 3 轮未通过（最后一轮致命 2 条）").strip() == "⚠"
+    assert _mark("判断项未经审核：人工修改过 3 格").strip() == "⚠"
+
+
 def test_新前缀都注册进了白名单且失败与成功分开():
     """`_PREFIX` 的兜底是「未生成」—— 最强的那个断言，所以每个新生产者都要
     来登记。**而且登记对了还不够，符号也要对。** 计划原稿的这条测试只断言
```

- [ ] **Step 2: 跑测试，确认红**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`5 failed, 2401 passed, 8 skipped`。失败的是：

- `>       from ir_agent.research import (ASSUMPTIONS_FAILED_JSON, Stage, _mark,`
- `E       ImportError: cannot import name 'ASSUMPTIONS_FAILED_JSON' from 'ir_agent.research' (/private/tmp/claude-501/-Users-ariasleo-Desktop-------/92dcc123-0768-4ee6-a660-84c4fe38b3b6/scratchpad/wt-rehearse/ir_agent/research.py)`
- `tests/test_full_context.py::test_判断项审核停下时只写底稿与记录_不写assumptions_json`
- `tests/test_full_context.py::test_判断项审核通过时落审核结论与各轮记录`
- `tests/test_full_context.py::test_判断项审核接进了判断项环节_原门三两道审核已并入`
- `tests/test_full_context.py::test_判断项审核的提醒要进终端清单`
- `tests/test_research.py::test_判断项审核停下时教人以底稿改好再跑第二阶段`

- [ ] **Step 3: 实现**

删除：`ir_agent/factor_review.py`

```bash
git rm ir_agent/factor_review.py
```

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index 6d18ac8..e419329 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -141,8 +141,8 @@ class Row:
     # 分解模式下这一行对应哪个因子。空串表示旧的两标量模式 ——
     # 预研失败时的降级路径，必须保留。
     factor_key: str = ""
-    # 偏离多空平衡点时的说明。**代码只查存在性，不查合理性** —— 填得对不对
-    # 由结构审查（factor_review）对账。它的作用是让「无视辩论」和「有理由地
+    # 偏离多空平衡点时的说明。**代码不判定**（子项目三）—— 偏离是否成立由
+    # 判断项审核者 [平衡点] 条目判断。它的作用是让「无视辩论」和「有理由地
     # 偏离」在产物上长得不一样：没有它的话，判断项中性档 18%（平衡点 12%）
     # 这件事，无论是模型看到了因子细节而上调、还是模型压根没读辩论，产物上
     # 完全一样。同构的实测案例：判断项被锚定覆盖（批准 19.12% → 实际用
@@ -337,24 +337,6 @@ class AssumptionSet:
     implied: object = None
     # 代码读数、计算、核对的事实（`Facts`），由 check() 挂上。派生量，不进 JSON。
     facts: object = None
-    # 因子结构审查结果（`factor_review.ReviewResult`）。派生量，不进 JSON。
-    # 它**不是闸门**：只给结论与理由，印在清单上给批准人看。
-    review: object = None
-    # 门③ [对账] 判出矛盾、退回重填一次的记录（第一稿的审核意见）。派生量，
-    # 不进 JSON。
-    refill_note: list = field(default_factory=list)
-    # 假设的数据合理性辩论结果（`debate.DebateResult`）。派生量，不进 JSON。
-    # **同样不是闸门**：成立的挑战不改任何取值，只印在清单上 —— 自动改数
-    # 会绕过人工门，而这道门的意义正是让人负责。
-    debate: object = None
-    # I4：因子模式连续填不出合格判断项时，decide() 回落到两标量模式，
-    # 这里记下降级原因。**不进 to_json/from_json**——降级是这一轮研究
-    # 自己的事，不需要跨阶段持久化；describe() 会把它显式印在清单顶部，
-    # 「降级但继续」不能悄悄发生，人必须一眼看到这轮其实是降级产出的。
-    degraded: str = ""
-    # 降级时最后一次的**完整**模型输出，供调用方落盘排查（见 AssumeFailed.raw）。
-    # 不进 describe()——它是给人批准用的清单，不该混进几十行失败草稿。
-    degraded_raw: str = ""
     # 公式化后的分解式（`formula.FormulaModel`）。新写法直接给；旧的 `decomps`
     # 由 `_m()` 惰性翻译。**合成、清单、落盘一律经它。**
     model: object = None
@@ -394,10 +376,6 @@ class AssumptionSet:
         让清单整页空白（C1：人工批准的核心关卡变成一张只有标题的空页）。
         """
         lines = [f"DCF 判断项（{self.code}）—— 需人工核实后方可建模", ""]
-        if self.degraded:
-            # 「降级但继续」不能悄悄发生——清单顶部就要让人看到这轮
-            # 判断项其实是降级产出的，损失的是精度，不是担保。
-            lines += [f"⚠ **降级说明**：{self.degraded}", ""]
         if self.implied is not None:
             # **增速路径的算术后果，印在最前面。** 批准人看到的是三档数值
             # 和理由，而「中性档 71.75%」这种数看不出荒谬 —— 换算成「隐含
@@ -408,24 +386,11 @@ class AssumptionSet:
         if self.method_review:
             lines += ["🧭 **方法论审核遗留**（预研阶段审核者判为提醒/待采集的条目）：", ""]
             lines += list(self.method_review) + [""]
-        if self.refill_note:
-            lines += ["🔁 **对账退回重填**：", ""] + list(self.refill_note) + [""]
-        if self.review is not None and getattr(self.review, "flagged", None):
-            # 审核意见印在清单顶部而不是末尾 —— 批准人要在看三档数值之前
-            # 先知道「有哪几条已经被质疑过」。实测那条重复计算的因子若有
-            # 这一行，批准时一眼就能看到「重叠」。
-            lines += ["🔍 **因子结构审查**（审核者意见，未改任何数值）：", ""]
-            lines += [f"- [{f.kind}] {f.subject}：**{f.verdict}** —— {f.why}"
-                      for f in self.review.flagged]
-            lines += [""]
-        up = [c for c in getattr(getattr(self, "debate", None),
-                                 "challenges", []) if c.upheld]
-        if up:
-            lines += ["⚖️ **假设辩论**（成立的挑战，**取值未因此改动**）：", ""]
-            lines += [f"- [{c.kind.value}] {c.passage[:60]} —— "
-                      f"{(c.rationale or c.objection).strip()[:140]}"
-                      for c in up]
-            lines += [""]
+        if self.judge_review is not None:
+            # 判断项审核（子项目三）：提醒与待采集印在三档数值之前 —— 批准人要
+            # 先知道「哪几条已经被审核者质疑过」。
+            from ir_agent.assume_review import review_markdown
+            lines += review_markdown(self.judge_review)
         m = self._m()
         if m is not None and not m.legacy:
             lines += self._describe_formula()
@@ -450,6 +415,10 @@ class AssumptionSet:
                 lab = self.labels[r.year - 1] if r.year <= len(self.labels) \
                     else f"第{r.year}年"
                 ref = "；".join(b.describe() for b in r.bases)
+                if r.basis_unverified:
+                    ref = "⚠ 依据未能逐字核验：" + ref
+                elif not r.bases:
+                    ref = "⚠ 没有可核验的依据"
                 lines.append(f"| {lab} | {r.bear:.2%} | {r.base:.2%} "
                              f"| {r.bull:.2%} | {r.note or '—'} | {ref} |")
             lines.append("")
diff --git a/ir_agent/formula.py b/ir_agent/formula.py
index cd264fb..c2653d8 100644
--- a/ir_agent/formula.py
+++ b/ir_agent/formula.py
@@ -392,7 +392,7 @@ def flat_factors(xs) -> list:
 
     旧调用方传 `decompositions`（每条带 `.factors`），新调用方传 `FormulaModel`；
     水平模式下同一个因子被两条公式共用，迭代时要去重。assume、gaps、
-    factor_review、methodology 共用这一份，不各写一套。"""
+    methodology 共用这一份，不各写一套。"""
     if xs is None:
         return []
     if isinstance(xs, FormulaModel):
diff --git a/ir_agent/full.py b/ir_agent/full.py
index a35c3ae..35fa408 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -22,7 +22,9 @@ from ir_agent.drivers import DecompositionInvalid
 from ir_agent.market import market_of
 from ir_agent import dcf
 from ir_agent.research import (
+    ASSUMPTIONS_FAILED_JSON,
     ASSUMPTIONS_MD,
+    ASSUMPTIONS_ROUNDS_MD,
     ResearchOutcome,
     Stage,
     assumption_template,
@@ -1079,6 +1081,8 @@ DEBATE_REPORT = "debate_report.md"
 # 早晚分叉（本仓库已经在占位符期间上踩过这种分叉）。research 不能反向
 # 导入 full（full 依赖 research），所以单一定义放在被依赖的那一侧。
 ASSUMPTIONS_JSON = "assumptions.json"
+# 判断项审核（子项目三）：最后一轮逐条结论、是否通过与取值指纹。
+ASSUMPTIONS_REVIEW_JSON = "assumptions_review.json"
 # 判断项那一步的超时。比默认的 900s 更长 —— 它是全链路输出最长的调用，
 # 而输出长度不受提示词控制。配合 `retry_timeout=True`（见下方调用处）。
 ASSUME_TIMEOUT = 1500
@@ -2430,11 +2434,16 @@ def _anchor_banner(cal: dict, labels: list[str], rebased: bool = False) -> str:
 def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
                          decomps=None, factor_data=None, balances=None,
                          method_review=None) -> str:
-    """校准 → AI 填判断项 → 写出待核实清单。返回失败说明。
+    """校准 → AI 填判断项 → AI 审核 → 写出待核实清单。返回失败或保留说明。
 
-    `decomps`/`factor_data` 给定时按因子分解填写（方法论预研 + 定向
-    采集的产出），否则退回两标量模式 —— 预研失败时的降级路径，见
-    `_research_methodology`。
+    `decomps`/`factor_data` 给定时按因子分解填写（方法论预研 + 定向采集的
+    产出），否则是两标量模式 —— 只在取不到行业分类时出现。
+
+    **判断项合格与否全部由审核者判定**（子项目三，spec 2026-10-15）：代码只读数、
+    计算、核对，把结果作为事实交给审核者；门③ 原先的两道 AI 审核（`factor_review`
+    [对账]、判断项假设辩论）已并进审核者。审核 3 轮不过 / 答不全 / 审核通过但算不
+    下去 → 停下交人工：写各轮记录与最后一稿，**不写 assumptions.json**（那是给门③
+    批准的东西，未通过审核的稿子不能冒充它）。
     """
     from ir_agent import assume, assume_review
 
@@ -2452,76 +2461,33 @@ def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
     latest_annual = _latest_annual_period(r.ledger, as_of)
     base_margin = (_fact_value(r.ledger, "gross_margin", latest_annual, as_of)
                   if latest_annual else None)
-    decide_kw = dict(ledger=r.ledger, body=body, as_of=as_of, code=code,
-                     decomps=decomps, factor_data=factor_data,
-                     base_margin=base_margin,
-                     # 锚定输入：清单上展示的毛利率路径与建模时同一套基期。
-                     anchor=assume.anchor_from_cal(cal),
-                     # 辩论② 的平衡三元组：偏离它的行必须填 deviation。
-                     balances=balances)
+    # 审核者的账本事实目录：原判断项假设辩论靠它发现过「应付账款天数在同业 17
+    # 分位，证伪了『对供应商占款强』」—— 辩论并进审核者，这份资料一起交过去。
+    try:
+        catalog = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=as_of,
+                                          extra_periods=_catalog_periods(r)
+                                          ).render_for_prompt()
+    except Exception:                                       # noqa: BLE001
+        catalog = ""
     try:
-        a = assume.decide(client, cal["judg"], labels, ctx, judge=client, **decide_kw)
+        a = assume.decide(client, cal["judg"], labels, ctx, judge=client,
+                          ledger=r.ledger, body=body, as_of=as_of, code=code,
+                          decomps=decomps, factor_data=factor_data,
+                          base_margin=base_margin,
+                          # 锚定输入：清单上展示的毛利率路径与建模时同一套基期。
+                          anchor=assume.anchor_from_cal(cal),
+                          # 辩论② 的平衡三元组：作为事实交审核者 [平衡点]。
+                          balances=balances, catalog=catalog,
+                          # 预研阶段审核者的提醒/待采集（子项目二决定 7）。
+                          method_review=list(method_review or []))
     except assume_review.ReviewStop as e:
-        return f"DCF 判断项（{e}）"
-    except assume.AssumeFailed as e:
-        return f"DCF 判断项（{str(e)[:200]}）"
+        return _stop_for_assume_review(out, arts, e, code=code)
     except ClaudeCliError as e:
-        # 模型接口本身失败（额度用尽、超时、CLI 报错）与「填不出合格的
-        # 判断项」是两件事，但处置必须一样: **记进 skipped、让整轮继续**。
-        # 此前这里只捕 AssumeFailed，于是一个额度错误会以未捕获异常的形式
-        # 把已经写好、已经付过钱的 brief.md 与 debate.md 连同整轮分析一起
-        # 带走 —— 实测 600584 第三次实跑正是这样炸的，辩论已完成并回写了
-        # 5 项挑战，却因为下一个调用撞上额度上限而一个产物都没留下。
-        # run_analysis 里其他每一个联网环节都做了这件事（见产业链、因子
-        # 采集、撰写/辩论），唯独这一处漏了。**能力边界不是故障，
-        # 整轮研究不该因此停摆。**
-        return f"DCF 判断项（{e.__class__.__name__}: {str(e)[:160]}）"
-
-    # 预研阶段审核者的提醒/待采集带进门③ 清单（子项目二决定 7）。
-    a.method_review = list(method_review or [])
-
-    # ── 因子结构审查（第 3 道：清单式，不设闸门）────────────────
-    # **必须在写盘之前跑，但 checklist 要取写盘之前的 describe()** ——
-    # 审查结果自己会进 describe()，拿带结果的清单去喂审核者是循环引用。
-    # **条件是「算得出算术后果」，不是「有分解式」。** 分解式被拒时
-    # `decomps` 为空，而那恰恰是最该审一眼的一轮（实测生益科技 2026-10-06:
-    # 分解式因重复计算被逐出，这两道检查于是一次都没跑）。
-    if a.implied is not None:
-        from ir_agent import factor_review
-        try:
-            a.review = factor_review.review(
-                client, a._m() or a.decomps, checklist=a.describe(),
-                implied=a.implied.describe(),
-                drivers=tuple(assume.DRIVERS) if not assume.has_model(a) else ())
-        except Exception as e:                              # noqa: BLE001
-            # **审查失败不该毙掉判断项。** 它是一道补充意见，不是闸门 ——
-            # 与本仓库「这一步失败不该毙掉已经写好的正文」同一条原则。
-            a.review = None
-            print(f"因子结构审查未完成：{str(e)[:120]}", file=sys.stderr)
-        contra = [f for f in (getattr(a.review, "flagged", None) or [])
-                  if f.kind == "对账"]
-        if contra:
-            a = _refill_on_contradiction(a, contra, client=client,
-                                         judg=cal["judg"], labels=labels,
-                                         ctx=ctx, decide_kw=decide_kw)
-
-    # ── 假设的数据合理性辩论（第 4 道：开放式，同样不设闸门）────────
-    # 与 factor_review 分工: 那边清单式（可枚举），这边开放式（产能、竞争
-    # 格局、周期位置 —— 规则写不出来的那部分）。输入带算术后果，否则它只是
-    # 同一个模型换个角色再说一遍。
-    if a.implied is not None:
-        from ir_agent.debate import run_debate
-        try:
-            cat = FactCatalog.from_ledger(r.ledger, period=r.period,
-                                          as_of=as_of,
-                                          extra_periods=_catalog_periods(r))
-            a.debate = run_debate(cat, draft=a.describe(), client=client,
-                                  period=r.period,
-                                  scale=a.implied.describe(), max_rounds=1)
-        except Exception as e:                              # noqa: BLE001
-            # 同一条原则：这一步失败不该毙掉判断项。
-            a.debate = None
-            print(f"假设辩论未完成：{str(e)[:120]}", file=sys.stderr)
+        # 模型接口本身失败（额度用尽、超时、CLI 报错）：**记进 skipped、让整轮
+        # 继续**，写明是接口问题、重跑即可。实测 600584 第三次实跑：辩论已完成
+        # 并回写了 5 项挑战，却因为下一个调用撞上额度上限而一个产物都没留下。
+        return (f"DCF 判断项（{e.__class__.__name__}: {str(e)[:160]}）"
+                "—— 模型接口问题，原样重跑 --assume 即可")
 
     (out / ASSUMPTIONS_MD).write_text(
         _anchor_banner(cal, labels, rebased=bool(a.margin_bases)) + a.describe()
@@ -2531,59 +2497,40 @@ def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
         json.dumps(assume.to_json(a, code=code, approved_by="",
                                   approved_at=""),
                    ensure_ascii=False, indent=2), encoding="utf-8")
+    (out / ASSUMPTIONS_REVIEW_JSON).write_text(
+        assume_review.dump_review(a.judge_review, passed=True), encoding="utf-8")
+    (out / ASSUMPTIONS_ROUNDS_MD).write_text(
+        assume_review.rounds_markdown(a.judge_rounds), encoding="utf-8")
     arts[ASSUMPTIONS_ART] = str(out / ASSUMPTIONS_MD)
-    if getattr(a, "degraded_raw", ""):
-        # 降级时把最后一次的**完整**模型输出落盘。此前只有末 400 字进了
-        # 降级说明，而因子模式一次要输出几十行、故障常在中段——实测排查
-        # 时只能靠残缺的尾巴反推格式，每一轮都是猜。这份草稿不进
-        # assumptions.md（那是给人批准的清单，不该混进失败稿），单独成文件。
-        (out / FAILED_RAW).write_text(a.degraded_raw, encoding="utf-8")
-        arts["因子模式失败时的原始输出"] = str(out / FAILED_RAW)
-    # I4：因子模式连续失败后 assume.decide 会退回两标量模式，不是抛错——
-    # 判断项照常写出，但降级这件事不能只留在 assumptions.md 里，终端的
-    # skipped 清单是读者第一眼看到的地方，同一条原因要在这里再报一次。
+    arts["判断项审核各轮记录"] = str(out / ASSUMPTIONS_ROUNDS_MD)
     return _assumption_caveat_note(a)
 
 
-def _refill_on_contradiction(a, contra, *, client, judg, labels, ctx, decide_kw):
-    """门③ [对账] 判出「矛盾」→ 把审核意见与上一稿交回填写者，重填一次。
+def _stop_for_assume_review(out: Path, arts: dict, e, *, code: str) -> str:
+    """判断项审核停下交人工：落各轮记录、最后一稿的原文与可改的 JSON 底稿。
+
+    底稿写 `assumptions_failed.json` 而不是 `assumptions.json` —— 后者是门③ 批准的
+    对象；人以底稿改好、自己存成 assumptions.json 再跑 --value，读回时会因没有通过
+    的审核记录而标注「人工修改后未经审核」（spec 一）。"""
+    from ir_agent import assume, assume_review
+
+    (out / ASSUMPTIONS_ROUNDS_MD).write_text(
+        assume_review.rounds_markdown(e.rounds), encoding="utf-8")
+    arts["判断项审核各轮记录"] = str(out / ASSUMPTIONS_ROUNDS_MD)
+    if e.rounds:
+        (out / FAILED_RAW).write_text(e.rounds[-1].raw, encoding="utf-8")
+        arts["判断项最后一稿（原文）"] = str(out / FAILED_RAW)
+    if e.last is not None and e.last.rows:
+        (out / ASSUMPTIONS_FAILED_JSON).write_text(
+            json.dumps(assume.to_json(e.last, code=code, approved_by="",
+                                      approved_at=""),
+                       ensure_ascii=False, indent=2), encoding="utf-8")
+        arts["判断项最后一稿（可改的底稿）"] = str(out / ASSUMPTIONS_FAILED_JSON)
+    return f"{e} —— 各轮记录：{out / ASSUMPTIONS_ROUNDS_MD}"
+
+
+
 
-    生益科技 2026-10-14：毛利率两个 pp 因子按小数填（理由栏「5.47 个百分点」、
-    表里 0.0547），[对账] 判出了矛盾，但它只标注不阻断，数值原样进了批准清单。
-    与方法论预研「审核者给建议、提出者改」同一个闭环：判断仍由模型做，不加代码
-    闸门；只重填一次，重填稿仍矛盾就照常标注、交人工。重填失败或退回两标量模式
-    时保留第一稿 —— 补充环节失败不该毙掉已经填好的判断项。
-    """
-    from ir_agent import assume, factor_review
-
-    first = [f"- [{f.kind}] {f.subject}：**{f.verdict}** —— {f.why}" for f in contra]
-    draft = a.describe()
-    fb = (f"{ctx}\n\n【门③ 对账审核：上一稿判为「矛盾」，请据此重填】\n"
-          "审核者意见：\n" + "\n".join(first)
-          + f"\n\n上一稿（三档取值与理由）：\n{draft}\n\n"
-          "逐条回应审核意见：数值写错单位的改数值；理由与取值对不上的，改取值或"
-          "改理由，使二者自洽。重新输出全部假设行。")
-    try:
-        b = assume.decide(client, judg, labels, fb, **decide_kw)
-    except (assume.AssumeFailed, ClaudeCliError) as e:
-        a.refill_note = [f"对账判出矛盾，退回重填失败（{str(e)[:120]}），保留第一稿。"]
-        return a
-    if b.degraded and not a.degraded:
-        a.refill_note = ["对账判出矛盾，重填稿退回了两标量模式，保留第一稿。"]
-        return a
-    b.method_review = list(a.method_review)
-    if b.implied is not None:
-        try:
-            b.review = factor_review.review(
-                client, b._m() or b.decomps, checklist=b.describe(),
-                implied=b.implied.describe(),
-                drivers=tuple(assume.DRIVERS) if not assume.has_model(b) else ())
-        except Exception as e:                              # noqa: BLE001
-            b.review = None
-            print(f"因子结构审查（重填稿）未完成：{str(e)[:120]}", file=sys.stderr)
-    # 审完再挂第一稿的意见 —— 挂在前面会进 describe()，喂给审核者就带了偏见。
-    b.refill_note = ["对账判出矛盾，已退回填写者重填一次；以下是第一稿的意见：", *first]
-    return b
 
 
 def _assumption_caveat_note(a) -> str:
@@ -2601,24 +2548,14 @@ def _assumption_caveat_note(a) -> str:
     —— 消息自己说未阻断，标签说没生成。
     """
     notes: list[str] = []
-    if a.degraded:
-        notes.append(a.degraded)
-    # 放宽因子模式引文核验之后的代价：依据没被拒绝，但也不能只悄悄
-    # 留在 assumptions.md 的表格里等人自己逐行发现——终端产物清单是
-    # 读者第一眼看到的地方，同一件事要在这里也露出来，而不是非致命
-    # 就等于"没有的事"。
-    if getattr(a, "refill_note", None):
-        notes.append("门③ 对账判出矛盾，已退回重填" if "已退回填写者重填一次"
-                     in a.refill_note[0] else a.refill_note[0].rstrip("。"))
-    upheld = getattr(getattr(a, "debate", None), "upheld_count", 0)
-    if upheld:
-        notes.append(f"假设辩论有 {upheld} 项挑战成立（数据合理性，"
-                     "逐条见清单顶部）——**取值未因此改动**，请自行判断")
-    flagged = getattr(getattr(a, "review", None), "flagged", None)
-    if flagged:
-        notes.append(f"因子结构审查标出 {len(flagged)} 项需人工复核"
-                     "（两两重叠 / 合成值与理由栏矛盾 / 不可测，"
-                     "逐条见清单顶部）")
+    # 判断项审核的提醒、待采集：通过了，但审核者认为有牵强或缺资料的地方 —— 终端
+    # 产物清单是读者第一眼看到的地方，不能只留在 assumptions.md 里。
+    res = getattr(a, "judge_review", None)
+    if res is not None and res.flagged():
+        c = res.counts()
+        notes.append(f"判断项审核第 {res.rounds} 轮通过，提醒 {c['提醒']} 条、"
+                     f"待采集 {c['待采集']} 条（逐条见清单顶部）")
+    # 引文逐字核验不过只标注、交审核者判断 —— 终端也要露出来。
     unverified = sum(1 for r in a.rows if getattr(r, "basis_unverified", ""))
     if unverified:
         notes.append(f"{unverified} 行依据未能逐字核验（已标注，未阻断，"
diff --git a/ir_agent/research.py b/ir_agent/research.py
index e4cc46c..b1f98dc 100644
--- a/ir_agent/research.py
+++ b/ir_agent/research.py
@@ -29,6 +29,9 @@ from pathlib import Path
 # 导入，不要在两处各写一遍：next_actions 要按这个文件在不在决定
 # 教人重跑哪一阶段，写岔了就会教人去覆盖已核实的判断项。
 ASSUMPTIONS_MD = "assumptions.md"
+# 判断项审核（子项目三）：每一轮的完整记录；停下时最后一稿的可改底稿。
+ASSUMPTIONS_ROUNDS_MD = "assumptions_rounds.md"
+ASSUMPTIONS_FAILED_JSON = "assumptions_failed.json"
 
 
 class Stage(str, Enum):
@@ -129,6 +132,10 @@ _PREFIX = (
     # AssumeFailed / 调用失败）以「DCF 判断项（」开头，那三条确实没产出
     # 判断项，仍落「未生成」。见 `full._assumption_caveat_note`。
     ("判断项待复核：", "  ⚠ "),
+    # 判断项审核（子项目三）停下交人工 / 第二阶段读回人改过的判断项：都是「算
+    # 出来了或停在那儿、必须被看见」，不是「未生成」。
+    ("判断项审核", "  ⚠ "),
+    ("判断项未经审核：", "  ⚠ "),
     # 第二阶段读回第一阶段落盘的公司公告：**成功**路径，为读者报告「读回了
     # 几篇、第一阶段候选与挑选各多少」——既不是警告也不是缺了什么产物。
     # 该条原先以「公司公告 N 篇…」起头、不匹配任何前缀，每一次成功的
@@ -281,6 +288,14 @@ def next_actions(stage: Stage, code: str,
                 f"     python -m ir_agent {code} {y} --assume {out} "
                 f"--reviewed-by <你的名字>",
             ]
+            if (Path(out) / ASSUMPTIONS_FAILED_JSON).exists():
+                acts.append(
+                    f"判断项审核停下。若要人工给出判断项：以 {out}/"
+                    f"{ASSUMPTIONS_FAILED_JSON} 为底稿改好，存为 {out}/assumptions.json"
+                    f"（各轮审核意见见 {out}/{ASSUMPTIONS_ROUNDS_MD}），再跑第二阶段：\n"
+                    f"     python -m ir_agent {code} {y} --value {out} "
+                    "--approved-by <你的名字>\n"
+                    "     —— 读回时会标注「判断项未经审核」。")
             if stale:
                 acts.append(
                     f"⚠ {out}/{ASSUMPTIONS_MD} 是**上一轮**留下的，不是本轮产物"
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2406 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `_stop_for_assume_review` 把底稿写到 `ASSUMPTIONS_JSON` → 「停下时不写 assumptions.json」红
- `describe()` 里 `review_markdown(...)` 换成 `pass` → 「审核通过时落审核结论」红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(full): 判断项环节接上审核者 —— 落审核结论与各轮记录，停下时只写底稿

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 第二阶段比对指纹：人改过的不送审，只标注

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `tests/test_assume_review.py`

**Interfaces:**
- Produces: `full._unreviewed_assumptions(out, a, edits) -> str`；`full._assumption_caveats(a, anchor_note='', unreviewed='') -> (str, str)`

**为什么：** `run_valuation` 读回后调 `_unreviewed_assumptions(out, a, edits)`：没有审核记录、审核未通过、取值指纹对不上 → 终端 `判断项未经审核：…`，研报附录（`_assumption_caveats(unreviewed=)`）写明并带改动格数，进提示词那句不带数字。`_APPROVE_NOTE` 去掉「改完仍会重新校验，改坏了会被挡下来」。

- [ ] **Step 1: 写失败测试**

```diff
diff --git a/tests/test_assume_review.py b/tests/test_assume_review.py
index c95587f..a58894b 100644
--- a/tests/test_assume_review.py
+++ b/tests/test_assume_review.py
@@ -196,3 +196,56 @@ def test_清单与提示词里的条目一致():
 def test_规则正文不出现与清单同形的条目行():
     import re
     assert not re.search(r"(?m)^\s*-\s*\[(取值|平衡点|依据|合成|后果)\]", AR.RULES)
+
+
+class TestPhase2:
+    """第二阶段读回人改过的判断项：不送审，只标注（spec 三、用户决定 5）。"""
+
+    def _saved(self, tmp_path, *, passed=True):
+        from ir_agent import full
+        from ir_agent.methodology_review import ReviewResult
+        a = assume.parse(_draft(), 2, decomps=MODEL)
+        res = ReviewResult(rounds=1, fingerprint=AR.fingerprint(a))
+        (tmp_path / full.ASSUMPTIONS_REVIEW_JSON).write_text(
+            AR.dump_review(res, passed=passed), encoding="utf-8")
+        return assume.to_json(a)
+
+    def test_指纹一致_不标注(self, tmp_path):
+        from ir_agent import full
+        a, edits = assume.from_json(self._saved(tmp_path))
+        assert full._unreviewed_assumptions(tmp_path, a, edits) == ""
+
+    def test_人改了数_标注并说改了几格(self, tmp_path):
+        from ir_agent import full
+        d = self._saved(tmp_path)
+        d["rows"][0]["base"] = "0.21"
+        a, edits = assume.from_json(d)
+        got = full._unreviewed_assumptions(tmp_path, a, edits)
+        assert "人工修改，修改后未经审核" in got and "1 格" in got
+
+    def test_没有审核记录或审核未通过_都标注(self, tmp_path):
+        from ir_agent import full
+        a, edits = assume.from_json(self._saved(tmp_path, passed=False))
+        assert "审核未通过" in full._unreviewed_assumptions(tmp_path, a, edits)
+        (tmp_path / full.ASSUMPTIONS_REVIEW_JSON).unlink()
+        assert "没有判断项审核记录" in full._unreviewed_assumptions(tmp_path, a, edits)
+
+    def test_研报附录写明未经审核_提示词那句不带数字(self, tmp_path):
+        import re
+
+        from ir_agent import full
+        a, _ = assume.from_json(self._saved(tmp_path))
+        line, md = full._assumption_caveats(a, unreviewed="判断项经人工修改，修改后未经审核（改动 3 格）")
+        assert "未经" in line and not re.search(r"\d", line)
+        assert "改动 3 格" in md
+
+    def test_第二阶段比对指纹且不再按内容拒绝(self):
+        import inspect
+
+        from ir_agent import full
+        src = inspect.getsource(full.run_valuation)
+        assert "_unreviewed_assumptions(" in src
+        assert src.index("_unreviewed_assumptions(") < src.index("dcf.run(")
+        assert "facts.blocking" in src
+        assert 'raise SystemExit(f"批准的假设不合法' not in src
+        assert "重新校验" not in full._APPROVE_NOTE
```

- [ ] **Step 2: 跑测试，确认红**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`5 failed, 2406 passed, 8 skipped`。失败的是：

- `tests/test_assume_review.py::TestPhase2::test_指纹一致_不标注`
- `tests/test_assume_review.py::TestPhase2::test_人改了数_标注并说改了几格`
- `tests/test_assume_review.py::TestPhase2::test_没有审核记录或审核未通过_都标注`
- `tests/test_assume_review.py::TestPhase2::test_研报附录写明未经审核_提示词那句不带数字`
- `tests/test_assume_review.py::TestPhase2::test_第二阶段比对指纹且不再按内容拒绝`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/full.py b/ir_agent/full.py
index 35fa408..dc067f3 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -1718,7 +1718,28 @@ def _review_stage(arts: dict):
         "但估值尚无从谈起")
 
 
-def _assumption_caveats(a, anchor_note: str = "") -> tuple[str, str]:
+def _unreviewed_assumptions(out: Path, a, edits: list[str]) -> str:
+    """第二阶段读回的判断项是不是审核通过的那一份。空串 = 是。
+
+    比对 `assumptions_review.json` 里审核时的取值指纹（只算因子、年、三档与基期
+    值，不算理由文字）。没有审核记录、审核未通过（人以底稿补写）、指纹对不上
+    （人改了数），都要标注 —— 不送审（子项目三决定 5）。"""
+    from ir_agent import assume_review
+
+    f = out / ASSUMPTIONS_REVIEW_JSON
+    if not f.exists():
+        return "没有判断项审核记录（未经 AI 审核，或审核停下后人工补写）"
+    d = json.loads(f.read_text(encoding="utf-8"))
+    if not d.get("passed"):
+        return "判断项审核未通过，这份是人工以底稿补写的"
+    if d.get("fingerprint") != assume_review.fingerprint(a):
+        n = f"（改动 {len(edits)} 格，逐条见「判断项经人工调整」）" if edits else ""
+        return f"判断项经人工修改，修改后未经审核{n}"
+    return ""
+
+
+def _assumption_caveats(a, anchor_note: str = "",
+                        unreviewed: str = "") -> tuple[str, str]:
     """判断项的软处要跟着交付物走：(进提示词的一句话, 追加进 report.md 的附录)。
 
     两件事，分别说：
@@ -1754,10 +1775,14 @@ def _assumption_caveats(a, anchor_note: str = "") -> tuple[str, str]:
     unverified = assume.unverified_labels(a)
     estimated = assume.estimated_labels(a)
     rebased = bool(a.margin_bases)
-    if not unverified and not estimated and not anchor_note and not rebased:
+    if (not unverified and not estimated and not anchor_note and not rebased
+            and not unreviewed):
         return "", ""
 
     says: list[str] = []
+    # 人改过、未经 AI 审核：读者必须知道这份判断项没有审核者背书（子项目三）。
+    if unreviewed:
+        says.append("**判断项经人工修改或补写，修改后未经 AI 审核**")
     # 锚定影响最大，先说：首年批准值整个没进模型，研报正文却一直按它讲。
     if anchor_note:
         says.append("**首个预测年由已披露中期锚定**，批准清单上该年的营收增速"
@@ -1781,6 +1806,8 @@ def _assumption_caveats(a, anchor_note: str = "") -> tuple[str, str]:
                    + "。不要把它们写得与有确凿出处的判断项同等确定。")
 
     body: list[str] = ["", "---", "", "## ⚠ 判断项依据核验", ""]
+    if unreviewed:
+        body += [f"**{unreviewed}。**", ""]
     if anchor_note:
         body += [f"**{anchor_note}**", "",
                  "锚定的三档（占全年营收比重、剩余期毛利率）见工作簿「1_假设」"
@@ -2573,8 +2600,9 @@ _APPROVE_NOTE = """
 不在此列，也不需要你确认。
 
 - 认可就直接进入第二阶段建模。
-- 要改数值，改 `{js}` 里的 `rows`，再进入第二阶段；改完仍会重新校验
-  （三档必须严格拉开、每年都要有值），改坏了会被挡下来而不是算出个错数。
+- 要改数值，改 `{js}` 里的 `rows`，再进入第二阶段。改过的格子**不再送 AI 审核**：
+  第二阶段比对审核时的取值指纹，在终端与研报里写明「判断项经人工修改，修改后
+  未经审核」并列出改动；代码只查算不算得下去（缺年份、缺基期等），不按内容拒绝。
 """
 
 
@@ -2627,6 +2655,11 @@ def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
     skipped: list[str] = []
     for x in edits:
         skipped.append(f"判断项经人工调整：{x}（原依据未必仍然支撑新取值）")
+    # **人改过的不再送审，只标注**（子项目三决定 5）：人在门③ 签字就是承担责任，
+    # AI 不该否决人的决定；但「这几格没经过审核」必须让读者看见。
+    unreviewed = _unreviewed_assumptions(out, a, edits)
+    if unreviewed:
+        skipped.append(f"判断项未经审核：{unreviewed}")
 
     # **重建派生事实必须排在校验之前。** industry.* / comps.* / macro.*
     # 这些 key 只存在于内存账本，由 _analyse_facts 算出来；第一阶段的
@@ -2738,7 +2771,8 @@ def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
     cost = 0.0
     # 判断项的软处（依据未逐字核验 / 取值是估计）要跟着交付物走，见 `_assumption_caveats`。
     caveat, caveats_md = _assumption_caveats(
-        a, anchor_note=(dcf_res.anchor_note if dcf_res is not None else ""))
+        a, anchor_note=(dcf_res.anchor_note if dcf_res is not None else ""),
+        unreviewed=unreviewed)
     if dcf_res is not None:
         full, cost, ferr, debate_md, dnote = _write_full_report(
             code, r, body, model, dcf_res, _prompt_bundle(pr), out,
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2411 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `_unreviewed_assumptions` 的指纹比较改成 `if False:` → 「人改了数」测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(value): 第二阶段比对判断项指纹 —— 人改过的不送审，只标注

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 端到端、生益科技回放，以及它们逼出的两处修正

**Files:**
- Modify: `ir_agent/assume.py`
- Modify: `ir_agent/full.py`
- Modify: `tests/test_assume_formula.py`
- Modify: `tests/test_assume_review.py`
- Modify: `tests/test_full_context.py`

**Interfaces:**
- Produces: `full._stop_if_uncomputable(facts) -> None`（`blocking` 非空 → `SystemExit`）

**为什么：** 端到端：第三次调用**不打桩** `_propose_assumptions`，只把模型换成按提示词分派的假模型 —— 第 1 轮驳回第 2 轮通过；3 轮不过停在 `--assume`、给两条做法。回放：生益科技 2026-10-15 三次被拒的情形（偏离一个单位写错的平衡点、两个 pp 因子加总后倒序、七段的行）都只成为审核者的事实。端到端抓到 **main 上已有的缺陷**：drivers.md 为旧「分解:」写法时，第三次调用传入 legacy 的 `FormulaModel`，`build_prompt` 当列表迭代 → TypeError（只有分解式列表才走旧头部）。变异验证抓到第二阶段「算不下去才停」只有源码字面断言守着 → 提成 `_stop_if_uncomputable` 并补行为测试。

- [ ] **Step 1: 写失败测试**

```diff
diff --git a/tests/test_assume_formula.py b/tests/test_assume_formula.py
index 24e36bd..b722c1c 100644
--- a/tests/test_assume_formula.py
+++ b/tests/test_assume_formula.py
@@ -248,3 +248,14 @@ def test_判断项清单顶部有方法论审核遗留():
     assert "方法论审核遗留" in txt and "年报未披露单位成本" in txt
     assert txt.index("方法论审核遗留") < txt.index("## 分解式")
 
+
+
+def test_旧写法翻译成的FormulaModel也能出提示词():
+    """第三次调用传的是 drivers.md 读回的 `FormulaModel`（旧「分解:」写法翻译过来，
+    legacy=True），此前 build_prompt 把它当分解式列表迭代，直接 TypeError。"""
+    m = parse_meth("分解: 营收 | 乘 | 出货量:变化率:年报:出货量 | 单价:变化率:年报:单价\n"
+                   "分解: 毛利率 | 加 | 铜箔:pp:年报:铜箔 | 稼动率:pp:年报:稼动率\n").model
+    assert m.legacy
+    txt = assume.build_prompt({"g_stats": (), "gm_stats": ()}, ["2026E"], "上下文",
+                              decomps=m, factor_data={})
+    assert "出货量" in txt and "公式" in txt
diff --git a/tests/test_assume_review.py b/tests/test_assume_review.py
index a58894b..557d380 100644
--- a/tests/test_assume_review.py
+++ b/tests/test_assume_review.py
@@ -246,6 +246,45 @@ class TestPhase2:
         src = inspect.getsource(full.run_valuation)
         assert "_unreviewed_assumptions(" in src
         assert src.index("_unreviewed_assumptions(") < src.index("dcf.run(")
-        assert "facts.blocking" in src
+        assert "_stop_if_uncomputable(facts)" in src
+        assert src.index("_stop_if_uncomputable(facts)") < src.index("dcf.run(")
         assert 'raise SystemExit(f"批准的假设不合法' not in src
+
+    def test_只在算不下去时停下_内容问题不拦(self):
+        from ir_agent import full
+        full._stop_if_uncomputable(assume.Facts(problems=["第 1 年三档未拉开"]))
+        with pytest.raises(SystemExit, match="(?s)算不下去.*缺第"):
+            full._stop_if_uncomputable(assume.Facts(blocking=["因子「提价」缺第 [2] 年"]))
         assert "重新校验" not in full._APPROVE_NOTE
+
+
+class TestShengyiReplay:
+    """生益科技 2026-10-15 第三次调用：三次被代码闸门拒绝、整个因子分解退回两标量。
+    三种情形现在都不再被代码拒绝，原拒绝理由出现在审核者手里的事实里。"""
+
+    BAL = [Balance("提价传导", D("0.02"), D("0.035"), D("0.05"), "辩论② 平衡点（单位写成了小数）")]
+
+    def _draft(self, gm1="1 | 3.5 | 4", raw1="-8 | -4 | -1") -> str:
+        return _draft().replace("假设: 提价 | 第1年 | 1 | 3.5 | 5.47",
+                                f"假设: 提价 | 第1年 | {gm1}").replace(
+            "假设: 原料 | 第1年 | -8 | -4 | -1", f"假设: 原料 | 第1年 | {raw1}")
+
+    def test_第一次_偏离一个单位写错的平衡点(self):
+        j = Judge()
+        a, res, _ = _run(_Filler(self._draft()), j, balances=self.BAL)
+        assert res.rounds == 1
+        assert "偏离多空平衡点 0.035" in j.prompts[0]
+        assert ("平衡点", "提价（提价传导）") in items(j.prompts[0])
+
+    def test_第二次_两个pp因子各自拉开_加总后倒序(self):
+        j = Judge()
+        _run(_Filler(self._draft(raw1="-8 | -4 | -6")), j)
+        assert "第 1 年的毛利率合成后不单调" in j.prompts[0]
+
+    def test_第三次_七段的行依据在第七段(self):
+        seven = self._draft().replace(
+            "假设: 行业 | 第1年 | 10% | 20% | 30% | 平衡点 | 无数据（估计），参照行业",
+            "假设: 行业 | 第1年 | 10% | 20% | 30% | 中性档采纳辩论平衡点20% | 无数据（估计），参照行业")
+        j = Judge()
+        a, _, _ = _run(_Filler(seven), j)
+        assert "缺少可核验的依据" not in j.prompts[0]
diff --git a/tests/test_full_context.py b/tests/test_full_context.py
index 4975e3f..a9daf16 100644
--- a/tests/test_full_context.py
+++ b/tests/test_full_context.py
@@ -13,7 +13,7 @@ from ir_agent.debate import DebateRefused
 from ir_agent.full import _macro_periods, _prior_period
 from ir_agent.ledger import Fact, FactLedger, Method
 from ir_agent.research import assumption_template
-from judge_stub import Judge
+from judge_stub import Judge, items
 
 
 class _R:
@@ -4312,3 +4312,71 @@ def test_人改过drivers_md后重跑_不送审_门二标注未经审核(tmp_pat
     assert any("人工修改后未经审核" in x for x in o.skipped)
     assert "人工修改后未经审核" in (tmp_path / full.REVIEW_MD).read_text(encoding="utf-8")
 
+
+
+# ══ 子项目三：判断项 = 填写者 + 审核者（端到端）════════════════════
+
+_E2E_DRAFT = "\n".join(
+    f"假设: {name} | 第{y}年 | {lo} | {mid} | {hi} | 理由 | 无数据（估计），参照行业"
+    for y in (1, 2)
+    for name, lo, mid, hi in (("高端覆铜板出货量", "2%", "8%", "15%"),
+                              ("平均单价", "-3%", "0%", "3%"),
+                              ("铜箔采购价", "-3", "-1", "0.5"),
+                              ("稼动率摊薄", "-1", "0.5", "1")))
+
+
+def _assume_e2e(monkeypatch, full, tmp_path, *script):
+    """前两次调用照 `_e2e` 跑；第三次调用**不打桩 `_propose_assumptions`**，
+    只把模型换成按提示词分派的假模型：审核提示词给假审核者，其余给假填写者。"""
+    from ir_agent import dcf
+
+    real = full._propose_assumptions
+    _e2e(monkeypatch, full, tmp_path)
+    d = date(2026, 10, 12)
+    full.run_analyse("600183", _PhaseR(), d, tmp_path)
+    full.run_brief("600183", _PhaseR(), d, tmp_path, "甲")
+    monkeypatch.setattr(full, "_propose_assumptions", real)
+    monkeypatch.setattr(dcf, "calibrate_only", lambda code, as_of, ledger: {
+        "judg": {"g_stats": (), "gm_stats": ()}, "periods": {"proj": ["2026E", "2027E"]}})
+    judge, fills = Judge(*script), []
+
+    class _C:
+        total_cost_usd = 0
+
+        def __call__(self, p):
+            if "**必须逐条作答的条目**" in p:
+                return judge(p)
+            fills.append(p)
+            return _E2E_DRAFT
+    monkeypatch.setattr(full, "make_client", lambda **k: _C())
+    return full.run_assume("600183", _PhaseR(), d, tmp_path, "乙"), judge, fills
+
+
+def test_判断项审核_第一轮驳回第二轮通过(tmp_path, monkeypatch):
+    from ir_agent import full
+
+    o, judge, fills = _assume_e2e(
+        monkeypatch, full, tmp_path, {("取值", "铜箔采购价"): "致命"}, {})
+    assert o.stage is full.Stage.AWAITING_ASSUME_REVIEW, o.skipped
+    assert len(fills) == 2 and "建议：改取值铜箔采购价" in fills[1]
+    assert ("平衡点", "高端覆铜板出货量") in items(judge.prompts[0]), \
+        "辩论② 的平衡点没进审核清单"
+    rev = json.loads((tmp_path / full.ASSUMPTIONS_REVIEW_JSON).read_text(encoding="utf-8"))
+    assert rev["passed"] is True and rev["rounds"] == 2
+    assert "## 第 2 轮" in (tmp_path / full.ASSUMPTIONS_ROUNDS_MD).read_text(encoding="utf-8")
+    assert "判断项审核" in (tmp_path / "assumptions.md").read_text(encoding="utf-8")
+
+
+def test_判断项审核三轮不过_停在第三次调用_给两条做法(tmp_path, monkeypatch):
+    from ir_agent import full
+    from ir_agent.research import next_actions
+
+    o, _, fills = _assume_e2e(monkeypatch, full, tmp_path, {"后果": "致命"})
+    assert len(fills) == 3
+    assert o.stage is full.Stage.INCOMPLETE
+    assert any(s.startswith("判断项审核 3 轮未通过") for s in o.skipped), o.skipped
+    assert not (tmp_path / "assumptions.json").exists()
+    assert (tmp_path / full.ASSUMPTIONS_FAILED_JSON).exists()
+    acts = "\n".join(next_actions(o.stage, "600183", out_dir=str(tmp_path),
+                                  year=2025, phase="assume"))
+    assert "--assume" in acts and full.ASSUMPTIONS_FAILED_JSON in acts
```

- [ ] **Step 2: 跑测试，确认红**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`5 failed, 2413 passed, 8 skipped`。失败的是：

- `tests/test_assume_formula.py::test_旧写法翻译成的FormulaModel也能出提示词`
- `tests/test_assume_review.py::TestPhase2::test_第二阶段比对指纹且不再按内容拒绝`
- `tests/test_assume_review.py::TestPhase2::test_只在算不下去时停下_内容问题不拦`
- `tests/test_full_context.py::test_判断项审核_第一轮驳回第二轮通过`
- `tests/test_full_context.py::test_判断项审核三轮不过_停在第三次调用_给两条做法`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/assume.py b/ir_agent/assume.py
index e419329..4389f73 100644
--- a/ir_agent/assume.py
+++ b/ir_agent/assume.py
@@ -1921,7 +1921,10 @@ def build_prompt(judg: dict, labels: list[str], context: str,
     m = as_model(decomps)
     if m is None:
         head = _SCALAR_HEAD
-    elif m.legacy:
+    elif m.legacy and isinstance(decomps, list):
+        # 旧的乘/加分解式列表：提示词与改动前一字不差。第三次调用从 drivers.md
+        # 读回的是 `FormulaModel`（旧写法翻译过来、legacy=True）—— 它没有 `.factors`
+        # 列表可迭代，走公式化的头部（子项目三端到端测试抓到）。
         head = _factor_head(decomps, factor_data or {})
     else:
         head = _formula_head(m, factor_data or {}, ledger_bases)
diff --git a/ir_agent/full.py b/ir_agent/full.py
index dc067f3..462796e 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -1718,6 +1718,15 @@ def _review_stage(arts: dict):
         "但估值尚无从谈起")
 
 
+def _stop_if_uncomputable(facts) -> None:
+    """第二阶段唯一会停下的情况：批准的判断项**算不下去**（缺年份、缺基期、重复
+    行、公式算不动 —— 多半是人手改坏了 JSON）。内容上的问题（三档、单调、依据、
+    平衡点）不再拦人批准过的判断项（子项目三）。"""
+    if facts.blocking:
+        raise SystemExit("批准的判断项算不下去，无法建模：\n"
+                         + "\n".join(f"  - {x}" for x in facts.blocking))
+
+
 def _unreviewed_assumptions(out: Path, a, edits: list[str]) -> str:
     """第二阶段读回的判断项是不是审核通过的那一份。空串 = 是。
 
@@ -2716,9 +2725,7 @@ def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
                          # 退回两标量口径，人批准的公式在这里一行都不生效。
                          decomps=a._m(), base_margin=base_margin,
                          anchor=anchor)
-    if facts.blocking:
-        raise SystemExit("批准的判断项算不下去，无法建模：\n"
-                         + "\n".join(f"  - {x}" for x in facts.blocking))
+    _stop_if_uncomputable(facts)
 
     # I3（final-fix-D 终审）：`check()` 通过后 `a.margin_bases` 就定了——
     # 非空说明毛利率加法链第 2 年起的基期已从年报基期换成各档锚定后的
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2418 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `build_prompt` 的 `isinstance(decomps, list)` 去掉 → 旧写法提示词测试与端到端红
- `_stop_if_uncomputable` 里 `if facts.blocking:` 改成 `if False:` → 行为测试红
- `run_valuation` 里删掉 `_stop_if_uncomputable(facts)` 调用 → 两条测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor(value): 第二阶段「算不下去才停」提成 _stop_if_uncomputable，补行为测试

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 做完之后

1. 用生益科技重跑第三次调用（`drivers.md`、辩论①② 沿用，门② 签名 xhl）：

```bash
python -m ir_agent 600183 --year 2025 --assume out/600183-p2 --reviewed-by xhl
```

2. 逐项核对 `assumptions_rounds.md`（填写者原文、代码事实、审核者每次作答）与 `assumptions.md`「判断项审核」一节；通过后跑第二阶段（门③ 签名 xhl）：

```bash
python -m ir_agent 600183 --year 2025 --value out/600183-p2 --approved-by xhl
```

3. 子项目一、二、三合在一起试跑通过后，再问用户是否推送 ir-agent。
