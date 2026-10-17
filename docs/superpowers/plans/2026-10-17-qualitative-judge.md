# 第一次调用的三个定性模块：撰写者 + 审核者（子项目四）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 宏观定性、战略分析、产业链三个模块的合格与否改由 AI 审核者判定，沿用各模块的方法论；任一模块审核没通过，第一次调用停下交人工。

**Architecture:** 新的共用引擎 `ir_agent/qual_review.py`（依据三种写法、撰写 → 审核循环、指纹、审核规则模板）；三个模块各自只改读数（只读不驳回）、事实（原驳回理由）、清单（按方法论生成）、规则与提示词；`full.py` 负责落盘、沿用、人工接手与停下，门① 清单与研报附录写明审核状态。

**Tech Stack:** Python 3、pytest；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-17-qualitative-judge-design.md`

**演练：** 每个任务的 diff 取自开发分支 `dev/qual-judge`，并已在干净的 main（`612ee23`）上按「先放测试 → 再放实现」逐任务演练；下面的红绿数字是演练实测。（主仓库与 worktree 里另有一条 `tests/test_openweb.py::test_fetch_重定向到内网地址后仍被拒绝` 受本机 DNS 被代理劫持影响时好时坏，与本计划无关：下面的失败**数**可能含它，失败**清单**里不列它。） 跑测试统一用：

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```

## Global Constraints

- 三个模块都改由审核者审核；**沿用各模块的方法论**（分析框架、输出格式、可选词不动），改的是审核标准。
- 取消「依据必须逐字出自原文」：依据可以是账本占位符、原文引用（年报、公告、研报、外部文档），或写明采纳了哪几篇文章的转述。
- 四级结论原话：通过（逻辑完全正确，资料能够对得上）/ 提醒（逻辑部分正确，资料对得上，但结论较为牵强）/ 致命（逻辑完全无法支撑结论）/ 待采集（逻辑正确，资料尚未采集到）；致命必附「建议：…」；可附选填「补充」；最多 3 轮。
- 清单按方法论生成，不按稿子生成。
- 审核者资料：撰写者看到的全部 + 代码事实 + 完整账本事实目录，不联网；来源分级由审核者判断。
- 描述里的账本外数字只作事实，不驳回（研报撰写的占位符契约仍是交付底线）。
- 任一模块 3 轮未通过、审核者答不全、模型接口失败 → 整个第一次调用停下；三个模块照常都跑完再停；不写来源清单、不进门①。
- 没有可用资料（数据缺失）照旧跳过，不触发停下。
- 重跑时通过的模块沿用、只重做没通过的；人工以最后一稿为底稿改好存成 `<模块>.md`，读回标注「人工修改后未经审核」。
- 测试不调模型；变异验证用复制备份还原；**zsh 里测试文件列表放进变量要写 `${=T}`**（否则不拆分，pytest 收到一个不存在的路径、输出为空 —— 本子项目演练前踩过）。

## Review Focus

- 撰写者把格式行包进代码块或行首加反引号：`lineparse.keyed` 已兼容（子项目二起），三个模块沿用同一个解析器 —— 不必新加测试，读数测试照常覆盖。
- 人工改写的 `<模块>.md` 本身有格式问题（读不进的行）：读回时不送审，只标「人工修改后未经审核」；读不进的行不会出现在门① 清单里。实现者如愿意，可把 `view.read_notes` 并入该模块的状态 `note`（当前计划没做，属于可选增强）。
- 沿用时语料库已变：代码重核依据并标出处数（Task 6 有测试），但**不重审** —— 这是用户决定，不是遗漏。
- 宏观定性没有外部资料时整节跳过（数据缺失）：不触发停下；端到端测试的空语料库正好走这条路。
- 撰写者给的转述只写了机构、没写《标题》：读成「没有写明采纳了哪几篇文章」的事实（Task 2 有测试）。
- **与 spec 措辞的一处偏离**：spec 五写沿用时标「本模块引用的 N 篇文章本轮已不在语料库」；实现写「本轮语料库与审核时不同，N 处依据本轮核对不过」—— 引文核对不过与转述文章不在语料库都计入，比 spec 宽。spec 这一节标明是「写设计时的判断」，取实现的写法。

## 文件结构

| 文件 | 职责 |
|---|---|
| `ir_agent/qual_review.py`（新） | 依据读取、撰写→审核循环、指纹、审核规则模板、审核遗留 |
| `ir_agent/macro_view.py`、`strategy.py`、`value_chain.py` | 只读不驳回的 `parse`、`facts`、`checklist`、`KINDS`/`RULES`、提示词、`assess/analyse(judge=)` |
| `ir_agent/assume_review.py` | `parse(kinds=)`、`rounds_markdown(title=, author=)` |
| `ir_agent/full.py` | `_qual_module`、`QualStop`、`_qual_section`、`_analyse_qualitative`、`run_analyse`、第二阶段附录 |
| `ir_agent/research.py` | `_PREFIX` 登记、`next_actions` 人工接手做法 |
| `tests/test_qual_review.py`、`tests/test_qual_stage.py`（新） | 引擎与编排 |

---

### Task 1: 判断项审核的作答解析与各轮记录参数化

**Files:**
- Modify: `ir_agent/assume_review.py`
- Modify: `tests/test_assume_review.py`

**Interfaces:**
- Produces: `assume_review.parse(raw, expected, kinds=KINDS) -> list[Verdict]`；`assume_review.rounds_markdown(rounds, *, title='判断项审核', author='填写者') -> str`

**为什么：** 三个定性模块要复用判断项审核的作答解析（含「补充」）与各轮记录渲染；把类别、标题、撰写者称呼做成参数，默认值不变。

- [ ] **Step 1: 写测试**

```diff
diff --git a/tests/test_assume_review.py b/tests/test_assume_review.py
index 724ad19..2eb1a8e 100644
--- a/tests/test_assume_review.py
+++ b/tests/test_assume_review.py
@@ -340,3 +340,12 @@ class TestBalanceIsFixedInput:
         txt = assume.build_prompt(JUDG, LABELS, "上下文", decomps=MODEL, factor_data={})
         assert "平衡点由辩论② 给出，你改不了" in txt
         assert "不要另写平衡点行" in txt
+
+
+def test_作答解析与各轮记录可换类别与称呼():
+    """子项目四（三个定性模块）复用判断项审核的作答解析与各轮记录。"""
+    got = AR.parse("[五力] 现有竞争强度：通过\n补充：提醒　x　建议：y",
+                   [("五力", "现有竞争强度")], kinds=("护城河", "五力"))
+    assert [v.kind for v in got] == ["五力", "补充"]
+    md = AR.rounds_markdown([AR.Round(1, "原文")], title="战略分析审核", author="撰写者")
+    assert md.startswith("# 战略分析审核：各轮记录") and "### 撰写者原文" in md
```

- [ ] **Step 2: 跑测试**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`2 failed, 2427 passed, 8 skipped`。失败的是：

- `tests/test_assume_review.py::test_作答解析与各轮记录可换类别与称呼`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/assume_review.py b/ir_agent/assume_review.py
index 7e6984a..003f863 100644
--- a/ir_agent/assume_review.py
+++ b/ir_agent/assume_review.py
@@ -76,7 +76,8 @@ def checklist(a, balances=()) -> list[tuple[str, str]]:
     return items
 
 
-def parse(raw: str, expected: list[tuple[str, str]]) -> list[Verdict]:
+def parse(raw: str, expected: list[tuple[str, str]],
+          kinds: tuple = KINDS) -> list[Verdict]:
     """解析审核者的回答：清单条目（复用 `methodology_review.parse`）+ 选填的「补充」。
 
     补充行先从原文里摘掉再解析清单 —— 否则它会被并进上一条条目的理由里。补充判
@@ -84,7 +85,7 @@ def parse(raw: str, expected: list[tuple[str, str]]) -> list[Verdict]:
     lines = raw.splitlines()
     supp = [_SUPP.match(x) for x in lines]
     body = "\n".join(x for x, m in zip(lines, supp) if not m)
-    verdicts = MR.parse(body, expected, kinds=KINDS)
+    verdicts = MR.parse(body, expected, kinds=kinds)
     extras: list[Verdict] = []
     for i, m in enumerate((m for m in supp if m), 1):
         tail = m.group(1).strip().lstrip("*")
@@ -276,11 +277,12 @@ def review_markdown(res: ReviewResult) -> list[str]:
     return lines
 
 
-def rounds_markdown(rounds: list[Round]) -> str:
+def rounds_markdown(rounds: list[Round], *, title: str = "判断项审核",
+                    author: str = "填写者") -> str:
     """`assumptions_rounds.md`：每一轮的完整记录。"""
-    lines = ["# 判断项审核：各轮记录", ""]
+    lines = [f"# {title}：各轮记录", ""]
     for r in rounds:
-        lines += [f"## 第 {r.n} 轮", "", "### 填写者原文", "", *MR._fenced(r.raw), ""]
+        lines += [f"## 第 {r.n} 轮", "", f"### {author}原文", "", *MR._fenced(r.raw), ""]
         if r.facts:
             lines += ["### 代码事实（只是事实，不驳回）", "", r.facts, ""]
         if r.incomplete:
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2428 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `parse` 里把 `kinds=kinds` 改回 `kinds=KINDS` → 新测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor(assume_review): 作答解析的类别、各轮记录的标题与称呼可参数化，供定性模块复用

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 共用审核引擎 `qual_review`

**Files:**
- Create: `ir_agent/qual_review.py`
- Create: `tests/test_qual_review.py`

**Interfaces:**
- Consumes: `assume_review.parse(kinds=)`、`assume_review.Round/ReviewStop`、`methodology_review.annotate_quotes/_feedback`
- Produces: `qual_review.basis(raw, corpus=None) -> (list[Basis], str, list[str])`；`run(writer, judge, *, name, prompt, materials, read, facts, checklist, kinds, rules, catalog='', hay='', ledger=None, as_of=None, max_rounds=3) -> (view, ReviewResult, rounds)`；`review(...)`；`fingerprint(raw) -> str`；`corpus_fingerprint(corpus) -> str`；`dump(res, *, passed, corpus_fp='') -> str`；`make_rules(subject, what_to_judge) -> str`；`review_tail(res) -> list[str]`；常量 `BASIS_RULES`、`REVIEW_NOTE`、`MAX_ROUNDS`；别名 `ReviewStop`、`Round`

**为什么：** 依据三种写法的读取（占位符 / 原文引用 / 写明文章的转述，核对结果只作事实）；撰写 → 审核循环（照搬判断项环节：致命带建议退回、补答不耗轮数、最多 3 轮、答不全停下，`ReviewStop.last` 是最后一稿原文）；撰写者原文与语料库的指纹；三个模块共用的审核规则提示词 `make_rules` 与 summary 末尾的「审核遗留」。

- [ ] **Step 1: 写测试**

```diff
diff --git a/tests/test_qual_review.py b/tests/test_qual_review.py
new file mode 100644
index 0000000..dfa138c
--- /dev/null
+++ b/tests/test_qual_review.py
@@ -0,0 +1,135 @@
+"""三个定性模块共用的审核引擎（spec 2026-10-17 子项目四）。不调模型。"""
+from __future__ import annotations
+
+from dataclasses import dataclass, field
+
+import pytest
+
+from ir_agent import qual_review as Q
+from judge_stub import Judge
+
+
+class _Doc:
+    def __init__(self, title, doc_id="d1"):
+        self.title, self.doc_id, self.text = title, doc_id, ""
+
+
+class _Corpus:
+    def __init__(self, *titles):
+        self.docs = [_Doc(t, f"d{i}") for i, t in enumerate(titles)]
+
+
+CORPUS = _Corpus("东莞证券：玻纤行业深度报告", "国信证券《建材月报》")
+
+
+class TestBasis:
+    """用户决定 3：依据可以是占位符、原文引用，或写明出处的转述。都读得进，
+    核对结果只作事实。"""
+
+    def test_占位符(self):
+        bases, prob, docs = Q.basis("[[payable_days@2025FY]]", CORPUS)
+        assert bases[0].describe() and prob == "" and docs == []
+
+    def test_原文引用_研报也行(self):
+        bases, prob, _ = Q.basis("「玻纤价格企稳回升」——东莞证券《玻纤行业深度报告》", CORPUS)
+        assert bases and bases[0].quote == "玻纤价格企稳回升" and prob == ""
+
+    def test_转述_写明文章_在语料库里(self):
+        bases, prob, docs = Q.basis("转述：综合 东莞证券《玻纤行业深度报告》", CORPUS)
+        assert bases == [] and prob == "" and docs == ["玻纤行业深度报告"]
+
+    def test_转述_文章不在语料库_只记事实(self):
+        _, prob, docs = Q.basis("转述：综合 中信证券《不存在的报告》", CORPUS)
+        assert "不在语料库" in prob and docs == ["不存在的报告"]
+
+    def test_转述没写明文章(self):
+        _, prob, _ = Q.basis("转述：行业普遍认为如此", CORPUS)
+        assert "没有写明" in prob
+
+    def test_什么都没给(self):
+        _, prob, _ = Q.basis("我的判断", CORPUS)
+        assert "没有给出依据" in prob
+
+
+@dataclass
+class _View:
+    lines: list = field(default_factory=list)
+    read_notes: list = field(default_factory=list)
+
+
+def _read(raw):
+    v = _View(lines=[x for x in raw.splitlines() if x.startswith("项:")])
+    if "坏行" in raw:
+        v.read_notes.append("一行没读进来：坏行")
+    return v
+
+
+def _run(writer, judge, **kw):
+    return Q.run(writer, judge, name="战略分析", prompt="撰写提示词", materials="资料",
+                 read=_read, facts=lambda v: ["描述里有账本外数字：91%"],
+                 checklist=lambda v: [("五力", "现有竞争强度"), ("自洽", "三部分")],
+                 kinds=("五力", "自洽"), rules="审核规则", catalog="账本目录",
+                 **kw)
+
+
+class _Writer:
+    def __init__(self, *outs):
+        self.outs, self.prompts = list(outs), []
+
+    def __call__(self, p):
+        self.prompts.append(p)
+        return self.outs[min(len(self.prompts), len(self.outs)) - 1]
+
+
+class TestRun:
+    def test_第一轮通过(self):
+        view, res, rounds = _run(_Writer("项: a"), Judge())
+        assert res.rounds == 1 and view.lines == ["项: a"] and res.fingerprint
+
+    def test_致命退回带建议_第二轮通过(self):
+        w = _Writer("项: a", "项: b")
+        view, res, rounds = _run(w, Judge({("五力", "现有竞争强度"): "致命"}, {}))
+        assert res.rounds == 2 and view.lines == ["项: b"]
+        assert "建议：改五力现有竞争强度" in w.prompts[1]
+
+    def test_审核者看到读数事实_代码事实_账本目录(self):
+        j = Judge()
+        _run(_Writer("项: a\n坏行"), j)
+        p = j.prompts[0]
+        assert "一行没读进来：坏行" in p and "账本外数字：91%" in p and "账本目录" in p
+        assert "审核规则" in p and "资料" in p
+
+    def test_三轮不过停下_带最后一稿(self):
+        with pytest.raises(Q.ReviewStop, match="战略分析审核 3 轮未通过.*致命 1 条") as e:
+            _run(_Writer("项: a", "项: b", "项: c"), Judge({"自洽": "致命"}))
+        assert e.value.last == "项: c" and len(e.value.rounds) == 3
+
+    def test_审核者答不全停下(self):
+        with pytest.raises(Q.ReviewStop, match="战略分析审核未完成"):
+            _run(_Writer("项: a"), Judge(skip={("自洽", "三部分")}))
+
+
+def test_指纹不计空白_语料库指纹按文档集合():
+    assert Q.fingerprint("项: a\n\n") == Q.fingerprint("  项: a")
+    assert Q.fingerprint("项: a") != Q.fingerprint("项: b")
+    two, three = _Corpus("a", "b"), _Corpus("a", "b", "c")
+    assert Q.corpus_fingerprint(two) == Q.corpus_fingerprint(_Corpus("x", "y")), \
+        "按文档 id 集合算（d0、d1），与标题无关"
+    assert Q.corpus_fingerprint(two) != Q.corpus_fingerprint(three)
+    assert Q.corpus_fingerprint(None) == ""
+
+
+def test_规则共用段_四级定义_依据_账本外数字_格式():
+    r = Q.make_rules("战略分析", "- 五力：……")
+    for s in ("通过", "提醒", "致命", "待采集", "逻辑完全无法支撑结论", "转述",
+              "账本外数字", "建议：", "补充：", "- 五力：……"):
+        assert s in r, s
+
+
+def test_审核遗留附在摘要末尾_只列提醒与待采集():
+    from ir_agent.methodology_review import ReviewResult, Verdict
+    res = ReviewResult(verdicts=[Verdict("五力", "现有竞争强度", "提醒", "牵强", suggest="补依据"),
+                                 Verdict("自洽", "三部分", "通过", "")])
+    tail = Q.review_tail(res)
+    assert tail[0].startswith("  审核遗留") and "建议：补依据" in tail[1] and len(tail) == 2
+    assert Q.review_tail(None) == []
```

- [ ] **Step 2: 跑测试**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 error`。

失败的是收集阶段：`tests/test_qual_review.py` 导入 `ir_agent.qual_review` 报 `ModuleNotFoundError`（模块还不存在）—— 这就是本任务的红。

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/qual_review.py b/ir_agent/qual_review.py
new file mode 100644
index 0000000..99f6cac
--- /dev/null
+++ b/ir_agent/qual_review.py
@@ -0,0 +1,221 @@
+"""第一次调用三个定性模块（宏观定性、战略分析、产业链）的审核引擎（子项目四，
+spec `2026-10-17-qualitative-judge-design.md`）。
+
+**为什么要有它。** 三个模块是同一套写法、同一类代码闸门：可选词、条数、账本外
+数字、依据必须逐字出自原文 —— 不过就重试，三次仍不过整节缺失。中国巨石
+（600176）第一次调用：五力引了研报，连挂三次，战略分析整节空着。用户决定：
+三个模块都改由审核者审核，**沿用各模块的方法论**，只改审核标准。
+
+**流程照搬判断项环节**（`assume_review`）：撰写者交稿 → 代码只读、只核对，结果作为
+事实交给审核者 → 审核者按清单逐条作答（四级结论、致命必附建议、可附「补充」）→
+有致命带建议退回重写，最多 3 轮；仍不通过 / 答不全 → `ReviewStop`。各模块的读数、
+事实、清单、规则由模块自己给，这里只管循环。
+"""
+
+from __future__ import annotations
+
+import hashlib
+import json
+import re
+
+from ir_agent import assume_review as AR
+from ir_agent import methodology_review as MR
+from ir_agent.basis import Basis, InvalidBasis
+from ir_agent.methodology_review import ReviewIncomplete, ReviewResult
+
+ReviewStop = AR.ReviewStop
+Round = AR.Round
+MAX_ROUNDS = 3
+JUDGE_TRIES = MR.JUDGE_TRIES
+
+_REF = re.compile(r"\[\[[^\]]+\]\]")
+_QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)
+_TITLE = re.compile(r"《([^》]{2,})》")
+_PARAPHRASE = re.compile(r"转述|综合")
+
+# 撰写者提示词里统一的依据写法（三个模块共用，用户决定 3）。
+BASIS_RULES = """**依据的三种写法**（任选其一，写在每行最后一段）：
+- 账本占位符：`[[key@期间]]`（只能用资料里给出的）；
+- 原文引用：`「逐字原文」——出处`，年报、公告、研报、外部文档都可以；能逐字引就逐字引；
+- 转述/综合：`转述：综合 机构《文章标题》、机构《文章标题》`—— 写明采纳了哪几篇文章的
+  观点，标题照资料里的写，由人复核。
+代码会核对引文能否在资料里找到、转述指明的文章是否在资料里，结果交给审核者看。"""
+
+REVIEW_NOTE = """**你写的稿子由审核者逐项审核**（代码不做判定，只把读到的内容与依据核对
+结果交给审核者）。有致命项，会带着修改建议退回给你重写，最多 3 轮 —— 被退回时按
+建议改，并重新输出全部行。"""
+
+
+_LEVELS_TEXT = """**四级结论**（只能用这四个词）：
+- **通过**：逻辑完全正确，资料能够对得上；
+- **提醒**：逻辑部分正确，资料对得上，但结论较为牵强；
+- **致命**：逻辑完全无法支撑结论；
+- **待采集**：逻辑正确，资料尚未采集到。"""
+
+
+def make_rules(subject: str, what_to_judge: str) -> str:
+    """审核规则提示词：各模块只写「每类判什么」，其余三个模块共用（四级定义、依据、
+    账本外数字、作答格式、补充）。说明一律用「类别名：」，不写成 `- [类别]` —— 那会被
+    当成要作答的条目（子项目二的教训）。"""
+    return f"""你是研究总监，正在审核一份**{subject}**稿子能不能用。你不直接改写，而是逐项
+给结论、理由与修改建议 —— 有一项「致命」，撰写者就要按你的建议重写。代码不做判定：它
+只把读到的内容、依据核对结果、描述里的账本外数字作为事实交给你，要不要紧由你判断。
+
+{_LEVELS_TEXT}
+
+提醒、致命、待采集**必须写理由**。凡涉及资料的判断，引原文「…」或账本占位符
+[[key@期间]] —— 代码会核对你的引文能否在资料、账本里找到。
+
+**每类要判什么**（下面用「类别名：」说明，作答时才写成 [类别]）：
+{what_to_judge}
+
+**依据**：三种写法都合法 —— 账本占位符、原文引用（年报、公告、研报、外部文档）、写明
+采纳了哪几篇文章的转述。代码给了核对结果：引文找不到不等于捏造（可能是概括），转述指向
+的文章不在资料里要当心。来源分级（如财经媒体「仅作线索，不得作为依据」）由你判断。
+
+**账本外数字**：描述会原样进入后两次调用（辩论、简报、研报撰写），那里的数字必须可
+溯源。代码列出了描述里的账本外数字 —— 撰写者指明了出处的判提醒（建议改成不带数字的
+说法或换成占位符）；没出处、又支撑了关键结论的判致命。
+
+**每条一行，格式严格如下**（结论、理由、建议之间用全角空格）：
+
+[类别] <条目>：<通过|提醒|致命|待采集>　<理由>　建议：<具体怎么改>
+
+**判为致命的条目必须写「建议：…」** —— 具体怎么改。提醒、待采集有建议也写上；通过
+不必写。条目名照上面清单原样抄，后面不要加别的内容。
+
+清单之外若发现问题，另起一行写：
+
+补充：<致命|提醒>　<问题>　建议：<怎么改>
+
+补充可以不写；补充判致命同样会退回重写。
+"""
+
+
+def review_tail(res) -> list[str]:
+    """审核的提醒、待采集附在模块 summary 末尾（spec 六）—— 辩论、简报读得到。"""
+    if res is None or not res.flagged():
+        return []
+    return ["  审核遗留（审核者判为提醒/待采集）："] + [f"    · {v.line()}"
+                                                     for v in res.flagged()]
+
+
+def _in_corpus(title: str, corpus) -> bool:
+    t = title.strip()
+    return any(t and (t == (d.title or "").strip() or t in (d.title or ""))
+               for d in (getattr(corpus, "docs", None) or []))
+
+
+def basis(raw: str, corpus=None) -> tuple[list[Basis], str, list[str]]:
+    """读一条依据。返回 (依据, 问题, 转述指向的文章标题)。**不驳回**：问题只作事实。
+
+    - 占位符 → `Basis.fact`；
+    - 写了「转述/综合」，或只给了《标题》没有引文 → 转述：记下标题、核对是否在语料库；
+    - 原文引用 → `Basis.report`（出处由 `attribute` 补）；
+    - 什么都没有 → 问题「没有给出依据」。
+    """
+    raw = (raw or "").strip()
+    ref = _REF.search(raw)
+    if ref:
+        try:
+            return [Basis.fact(ref.group(0))], "", []
+        except InvalidBasis as e:
+            return [], f"占位符不合法：{e}", []
+    quote = _QUOTE.search(raw)
+    if _PARAPHRASE.search(raw) or (quote is None and _TITLE.search(raw)):
+        titles = _TITLE.findall(raw)
+        if not titles:
+            return [], "写了转述/综合，但没有写明采纳了哪几篇文章（《标题》）", []
+        miss = [t for t in titles if not _in_corpus(t, corpus)]
+        prob = ("转述指向的文章不在语料库里：" + "、".join(f"《{t}》" for t in miss)
+                if miss else "")
+        return [], prob, titles
+    if quote is not None:
+        try:
+            return [Basis.report(quote.group(1))], "", []
+        except InvalidBasis as e:
+            return [], f"依据不合法：{e}", []
+    return [], "没有给出依据（占位符、原文引用或写明出处的转述）", []
+
+
+def fingerprint(raw: str) -> str:
+    """撰写者原文的指纹（去掉空白）。重跑读回时比对，变了 = 人改过（用户决定 12）。"""
+    return hashlib.sha256(re.sub(r"\s+", "", raw or "").encode("utf-8")).hexdigest()[:16]
+
+
+def corpus_fingerprint(corpus) -> str:
+    """审核时语料库的指纹（文档 id 集合）。沿用时资料可能已变（spec 五），便于对照。"""
+    ids = sorted(getattr(d, "doc_id", "") for d in (getattr(corpus, "docs", None) or []))
+    if not ids:
+        return ""
+    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:16]
+
+
+def dump(res: ReviewResult, *, passed: bool, corpus_fp: str = "") -> str:
+    return json.dumps({**res.to_json(), "passed": passed, "corpus": corpus_fp},
+                      ensure_ascii=False, indent=2)
+
+
+def review(judge, *, rules: str, kinds: tuple, expected: list, draft: str,
+           materials: str, facts: str, catalog: str = "", hay: str = "",
+           ledger=None, as_of=None) -> ReviewResult:
+    """审核者作答一次（漏答补答最多 2 次，不消耗撰写者轮数）。"""
+    items = "\n".join(f"- [{k}] {s}" for k, s in expected)
+    base = (f"{rules}\n**必须逐条作答的条目**\n{items}\n\n"
+            f"**撰写者交的稿子（原文）**\n{draft}\n\n"
+            f"**只给你的、代码核对出的事实**\n{facts}\n\n"
+            f"**账本事实目录**（可引用的占位符）\n{catalog or '（无）'}\n\n"
+            f"**撰写者看到的资料**\n{materials}\n")
+    prompt, last, attempts = base, "", []
+    for _ in range(JUDGE_TRIES):
+        raw = judge(prompt)
+        try:
+            verdicts = AR.parse(raw, expected, kinds=kinds)
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
+def run(writer, judge, *, name: str, prompt: str, materials: str, read, facts,
+        checklist, kinds: tuple, rules: str, catalog: str = "", hay: str = "",
+        ledger=None, as_of=None, max_rounds: int = MAX_ROUNDS):
+    """撰写 → 代码读与核对（不驳回）→ 审核 → 汇总。返回 (view, ReviewResult, rounds)。
+
+    `read(raw) -> view`（`view.read_notes` 是读数事实）；`facts(view) -> list[str]`；
+    `checklist(view) -> [(类别, 条目)]`。有致命即带建议退回重写，最多 `max_rounds` 轮；
+    仍不通过或审核者答不全 → `ReviewStop`（`last` 为最后一稿原文）。模型接口错误原样
+    抛出，由调用方按「接口问题」停下（用户决定 9）。
+    """
+    rounds: list = []
+    raw = ""
+    for n in range(1, max_rounds + 1):
+        raw = writer(prompt)
+        view = read(raw)
+        notes = list(getattr(view, "read_notes", []) or []) + list(facts(view))
+        ftxt = "\n".join(f"- {x}" for x in notes) or "（代码读数与核对没有发现问题）"
+        try:
+            res = review(judge, rules=rules, kinds=kinds, expected=checklist(view),
+                         draft=raw, materials=materials, facts=ftxt, catalog=catalog,
+                         hay=hay, ledger=ledger, as_of=as_of)
+        except ReviewIncomplete as e:
+            rounds.append(Round(n, raw, ftxt, incomplete=str(e),
+                                attempts=getattr(e, "attempts", [])))
+            raise ReviewStop(f"{name}审核未完成：审核者补答 {JUDGE_TRIES - 1} 次仍不全"
+                             f"（{str(e)[:120]}）", rounds, last=raw) from e
+        res.rounds = n
+        res.fingerprint = fingerprint(raw)
+        rounds.append(Round(n, raw, ftxt, review=res, attempts=res.attempts))
+        if not res.fatal():
+            return view, res, rounds
+        prompt = f"{prompt}\n\n---\n{MR._feedback(n, res)}\n"
+    raise ReviewStop(f"{name}审核 {max_rounds} 轮未通过（最后一轮致命 "
+                     f"{len(rounds[-1].review.fatal())} 条）", rounds, last=raw)
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2442 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `run` 里 `if not res.fatal():` 改成 `if True:` → 致命退回、三轮停下的测试红
- `basis` 里 `miss = [...]` 改成 `miss = []` → 「转述文章不在语料库」测试红
- `basis` 里 `if quote is not None:` 改成 `if False:` → 「原文引用（研报也行）」测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(qual_review): 定性模块的审核引擎 —— 依据三种写法、撰写→审核循环、指纹

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 宏观定性接上引擎

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `ir_agent/macro_view.py`
- Modify: `tests/test_macro_view.py`
- Modify: `tests/test_prompt_contract.py`

**Interfaces:**
- Consumes: Task 2 的 `qual_review`
- Produces: `macro_view.parse(text, corpus=None)`；`facts(view, ledger, as_of, corpus=None) -> list[str]`；`checklist(view)`；`KINDS`、`RULES`；`assess(client, corpus, ledger, as_of, macro_prompt='', *, judge, catalog='', max_rounds=3) -> (MacroView, str)`；`MacroView.read_notes/review/rounds`

**为什么：** `parse` 只读不驳回（取向/影响不是规定的词、段数不够跳过并记事实；依据问题照读）；`check` 换成 `facts`（没给取向、传导少于两条、账本外数字、依据核验不过、与期限利差方向相反都只作事实）；清单 [取向] 一条、[传导] 每条（一条没读进来列「传导路径（未给出）」）、[遗漏] 一条；提示词不再要求逐字，附依据三种写法与「由审核者逐项审核」；`assess` 必须给 `judge`。`full._load_macro_view` 先做最小接线（`judge=client`）。`test_prompt_contract` 那条按模块分别找 `facts`/`check`（战略、产业链在 Task 4、5 跟上）。

- [ ] **Step 1: 写测试**

```diff
diff --git a/tests/test_macro_view.py b/tests/test_macro_view.py
index 5bf52ab..48e9a43 100644
--- a/tests/test_macro_view.py
+++ b/tests/test_macro_view.py
@@ -11,6 +11,7 @@ from ir_agent import macro_view as MV
 from ir_agent.ledger import Fact, FactLedger, Method
 from ir_agent.macro_view import Impact, MacroViewFailed, Stance
 from ir_agent.sources.external import Corpus, Document, Tier
+from judge_stub import Judge
 
 D = Decimal
 AS_OF = date(2026, 9, 7)
@@ -61,55 +62,54 @@ def test_解析取向与传导():
     assert v.channels[0].impact is Impact.POSITIVE
 
 
-def test_取向取值受限():
-    with pytest.raises(MacroViewFailed, match="宽松/中性/收紧"):
-        MV.parse(f"政策取向: 偏松 | 「{Q}」")
+def test_取向不是规定的词_记进读数事实():
+    v = MV.parse(f"政策取向: 偏松 | 「{Q}」")
+    assert v.stance is None and "宽松/中性/收紧" in "\n".join(v.read_notes)
 
 
-def test_影响取值受限():
-    with pytest.raises(MacroViewFailed, match="利好/中性/利空"):
-        MV.parse(f"传导: a | b | 略有帮助 | 「{Q}」")
+def test_影响不是规定的词_记进读数事实():
+    v = MV.parse(f"传导: a | b | 略有帮助 | 「{Q}」")
+    assert v.channels == [] and "利好/中性/利空" in "\n".join(v.read_notes)
 
 
-def test_缺依据被驳回():
-    with pytest.raises(MacroViewFailed, match="缺少可核验的依据"):
-        MV.parse("政策取向: 宽松 | 我的判断")
+def test_缺依据_记进读数事实():
+    v = MV.parse("政策取向: 宽松 | 我的判断")
+    assert v.stance is Stance.EASING and "没有给出依据" in "\n".join(v.read_notes)
 
 
 # ── 传导路径是重点 ───────────────────────────────────────────
 
-def test_未给取向被驳回():
-    """「中性」也是判断，须显式说出。"""
+def test_未给取向_记进事实():
+    """「中性」也是判断，须显式说出 —— 现在交审核者 [取向] 判。"""
     v = MV.parse(_out(stance=None))
-    with pytest.raises(MacroViewFailed, match="中性.*也是判断"):
-        MV.check(v, FactLedger(), DOC, AS_OF, corpus=_corpus())
+    assert any("中性" in x and "也是判断" in x
+               for x in MV.facts(v, FactLedger(), AS_OF, corpus=_corpus()))
 
 
-def test_传导少于两条被驳回():
+def test_传导少于两条_记进事实():
     """「政策转向宽松」写在哪篇研报里都成立，对判断本标的没有帮助 ——
-    有帮助的是那条链。"""
+    有帮助的是那条链。现在交审核者 [传导] 判。"""
     v = MV.parse(_out(channels=[f"传导: a | b | 利好 | 「{Q}」"]))
-    with pytest.raises(MacroViewFailed, match="至少 2 条|等于没写"):
-        MV.check(v, FactLedger(), DOC, AS_OF, corpus=_corpus())
+    assert any("至少 2 条" in x for x in MV.facts(v, FactLedger(), AS_OF, corpus=_corpus()))
 
 
-def test_合法输入通过():
-    MV.check(MV.parse(_out()), FactLedger(), DOC, AS_OF, corpus=_corpus())
+def test_合法输入没有事实():
+    v = MV.parse(_out())
+    assert v.read_notes == [] and MV.facts(v, FactLedger(), AS_OF, corpus=_corpus()) == []
 
 
-def test_引文必须出自资料():
+def test_引文找不到_记进事实():
     v = MV.parse(_out(stance="宽松", channels=[
         "传导: a | b | 利好 | 「央行大幅加息」",
         f"传导: c | d | 利好 | 「{Q}」"]))
-    with pytest.raises(MacroViewFailed, match="不可核验|找不到"):
-        MV.check(v, FactLedger(), "", AS_OF, corpus=_corpus())
+    assert any("核验不过" in x for x in MV.facts(v, FactLedger(), AS_OF, corpus=_corpus()))
 
 
 def test_出处由代码判定():
     from ir_agent.basis import BasisKind
 
     v = MV.parse(_out())
-    MV.check(v, FactLedger(), "", AS_OF, corpus=_corpus())
+    MV.facts(v, FactLedger(), AS_OF, corpus=_corpus())
     assert v.stance_bases[0].kind is BasisKind.EXTERNAL
 
 
@@ -147,8 +147,9 @@ def test_形态判定与cycle共用一份实现():
 
 def test_提示词强调链条():
     c = _Client(_out())
-    MV.assess(c, _corpus(), FactLedger(), AS_OF)
+    MV.assess(c, _corpus(), FactLedger(), AS_OF, judge=Judge())
     assert "写不出链条的政策判断等于没写" in c.prompts[0]
+    assert "转述" in c.prompts[0] and "审核者逐项审核" in c.prompts[0]
 
 
 def test_无外部宏观资料时明说只能由读数推断():
@@ -202,19 +203,30 @@ def test_多档来源同时出现时可引用范围按Tier声明顺序排列():
 
 def test_摘要安全无裸数字():
     from ir_agent.citation import audit_bare_numbers
-    v, _ = MV.assess(_Client(_out()), _corpus(), _rates("0.020", "0.026"), AS_OF)
+    v, _ = MV.assess(_Client(_out()), _corpus(), _rates("0.020", "0.026"), AS_OF,
+                     judge=Judge())
     assert v.conflicts
     assert audit_bare_numbers(v.summary()) == []
 
 
-def test_驳回后重试():
-    bad = _out(channels=[f"传导: a | b | 利好 | 「{Q}」"])
-    c = _Client(bad, _out())
-    MV.assess(c, _corpus(), FactLedger(), AS_OF)
-    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]
+def test_审核致命则带建议重写():
+    c = _Client(_out(), _out())
+    MV.assess(c, _corpus(), FactLedger(), AS_OF,
+              judge=Judge({("遗漏", "宏观因素"): "致命"}, {}))
+    assert len(c.prompts) == 2 and "上一轮审核驳回" in c.prompts[1]
+    assert "建议：" in c.prompts[1]
+
+
+def test_三轮审核不过则停下():
+    from ir_agent.qual_review import ReviewStop
+    with pytest.raises(ReviewStop, match="宏观定性审核 3 轮未通过"):
+        MV.assess(_Client(_out(), _out(), _out()), _corpus(), FactLedger(), AS_OF,
+                  judge=Judge({"取向": "致命"}))
 
 
-def test_连续失败则拒绝出结论():
-    bad = _out(stance=None)
-    with pytest.raises(MacroViewFailed, match="连续"):
-        MV.assess(_Client(bad, bad, bad), _corpus(), FactLedger(), AS_OF)
+def test_审核清单按方法论生成():
+    """用户决定 5：取向一条、每条传导一条、遗漏一条；一条传导都没读进来时也列。"""
+    assert MV.checklist(MV.parse(_out())) == [
+        ("取向", "政策取向"), ("传导", "第1条 政策利率下调"), ("传导", "第2条 流动性宽松"),
+        ("遗漏", "宏观因素")]
+    assert ("传导", "传导路径（未给出）") in MV.checklist(MV.parse(f"政策取向: 宽松 | 「{Q}」"))
diff --git a/tests/test_prompt_contract.py b/tests/test_prompt_contract.py
index 29af27a..b5df917 100644
--- a/tests/test_prompt_contract.py
+++ b/tests/test_prompt_contract.py
@@ -322,8 +322,10 @@ def test_三个定性模块都装了这道闸(mod, exc, mk):
     import importlib
     import inspect
     m = importlib.import_module(f"ir_agent.{mod}")
-    src = inspect.getsource(m.check)
-    assert "audit_prose" in src, f"{mod}.check 没有核验描述字段"
+    # 子项目四起账本外数字只作事实（`facts()`），不再在 `check()` 里驳回。
+    fn = getattr(m, "facts", None) or m.check
+    src = inspect.getsource(fn)
+    assert "audit_prose" in src, f"{mod} 没有核对描述字段里的账本外数字"
     assert "不要写具体数字" in m.PROMPT, f"{mod} 的提示词没有讲清这条"
```

- [ ] **Step 2: 跑测试**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`14 failed, 2430 passed, 8 skipped`。失败的是：

- `tests/test_macro_view.py::test_取向不是规定的词_记进读数事实`
- `tests/test_macro_view.py::test_影响不是规定的词_记进读数事实`
- `tests/test_macro_view.py::test_缺依据_记进读数事实`
- `tests/test_macro_view.py::test_未给取向_记进事实`
- `tests/test_macro_view.py::test_传导少于两条_记进事实`
- `tests/test_macro_view.py::test_合法输入没有事实`
- `tests/test_macro_view.py::test_引文找不到_记进事实`
- `tests/test_macro_view.py::test_出处由代码判定`
- `tests/test_macro_view.py::test_提示词强调链条`
- `tests/test_macro_view.py::test_摘要安全无裸数字`
- `tests/test_macro_view.py::test_审核致命则带建议重写`
- `tests/test_macro_view.py::test_三轮审核不过则停下`
- `tests/test_macro_view.py::test_审核清单按方法论生成`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/full.py b/ir_agent/full.py
index 462796e..5f91790 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -568,7 +568,7 @@ def _load_macro_view(r, as_of: date, client, macro_pr: str):
     if r.corpus is None or not r.corpus.docs:
         return "", "", "宏观定性（无外部宏观资料，政策取向缺文字依据）"
     try:
-        view, _ = MV.assess(client, r.corpus, r.ledger, as_of, macro_pr)
+        view, _ = MV.assess(client, r.corpus, r.ledger, as_of, macro_pr, judge=client)
     except Exception as e:                                  # noqa: BLE001
         return "", "", f"宏观定性（{e.__class__.__name__}: {str(e)[:120]}）"
     return view.summary(), view.summary(), ""
diff --git a/ir_agent/macro_view.py b/ir_agent/macro_view.py
index 61a24f9..a8eb61c 100644
--- a/ir_agent/macro_view.py
+++ b/ir_agent/macro_view.py
@@ -27,11 +27,13 @@ from enum import Enum
 from ir_agent.lineparse import keyed, pick
 from ir_agent.ledger import FactLedger
 from ir_agent.sources.external import attribute
-from ir_agent.basis import Basis, InvalidBasis, validate
+from ir_agent.basis import Basis, validate
+from ir_agent import qual_review as Q
 
 
 class MacroViewFailed(Exception):
-    """宏观定性判断不可核验 —— 拒绝出结论。"""
+    """宏观定性没有可用资料等无从进行的情形。**内容是否成立不再由代码驳回**
+    （子项目四，spec 2026-10-17）：交给审核者，审核不过抛 `qual_review.ReviewStop`。"""
 
 
 class Stance(str, Enum):
@@ -55,15 +57,6 @@ _REF = re.compile(r"\[\[[^\]]+\]\]")
 
 
 
-def _tail(raw: str, n: int = 400) -> str:
-    """失败信息里带上模型最后写了什么。
-
-    **只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。** 前几轮
-    排查全跑失败时，每次都要单独再跑一次去抓原始输出 —— 而那几次的根因
-    （列表前缀、括号补注、写「无据」）看一眼原文就清楚了。
-    """
-    raw = (raw or "").strip()
-    return raw[-n:] if len(raw) > n else raw
 
 @dataclass
 class Channel:
@@ -71,6 +64,7 @@ class Channel:
     path: str            # 传导路径
     impact: Impact
     bases: list[Basis] = field(default_factory=list)
+    cites: list[str] = field(default_factory=list)    # 转述指向的文章标题
 
 
 @dataclass
@@ -79,6 +73,11 @@ class MacroView:
     stance_bases: list[Basis] = field(default_factory=list)
     channels: list[Channel] = field(default_factory=list)
     conflicts: list[str] = field(default_factory=list)
+    # 子项目四：代码读数时的事实（读不出的行……）、审核结论、各轮记录。不进 summary
+    # 以外的地方，读回时由 full 重建。
+    read_notes: list[str] = field(default_factory=list)
+    review: object = None
+    rounds: list = field(default_factory=list)
 
     @property
     def bases(self) -> list[Basis]:
@@ -93,57 +92,53 @@ class MacroView:
             lines.append(f"    · {c.driver} → {c.path} → {c.impact.value}")
         for x in self.conflicts:
             lines.append(f"  ⚠ {x}")
+        lines += Q.review_tail(self.review)
         return "\n".join(lines)
 
 
-def _basis(raw: str, what: str) -> list[Basis]:
-    raw = raw.strip()
-    ref = _REF.search(raw)
-    if ref:
-        try:
-            return [Basis.fact(ref.group(0))]
-        except InvalidBasis as e:
-            raise MacroViewFailed(f"{what} 的占位符不合法: {e}") from e
-    q = _QUOTE.search(raw)
-    if not q:
-        raise MacroViewFailed(f"{what} 缺少可核验的依据（需原文引用或占位符）")
-    try:
-        return [Basis.report(q.group(1))]
-    except InvalidBasis as e:
-        raise MacroViewFailed(f"{what} 的依据不合法: {e}") from e
-
-
-def _split(line: str, n: int, what: str) -> list[str]:
-    parts = [p.strip() for p in line.split("|")]
-    if len(parts) < n:
-        raise MacroViewFailed(
-            f"{what} 字段不足（需 {n} 段，得到 {len(parts)}）：{line}")
-    return parts
-
-
-def parse(text: str) -> MacroView:
-    view = MacroView()
 
+
+
+
+def _cells(line: str) -> list[str]:
+    return [p.strip() for p in line.split("|")]
+
+
+def parse(text: str, corpus=None) -> MacroView:
+    """读数。**只读，不驳回**（子项目四）：段数不够、取向/影响不是规定的词的行跳过，
+    依据的问题照读 —— 都记进 `read_notes`，作为事实交给审核者。"""
+    view = MacroView()
     m = _STANCE.search(text)
     if m:
-        p = _split(m.group(1), 2, "政策取向")
+        p = _cells(m.group(1))
         got = pick(p[0], [x.value for x in Stance])
-        if got is None:
-            raise MacroViewFailed(
-                "政策取向须为 " + "/".join(x.value for x in Stance)
-                + f"，得到 {p[0]!r}")
-        view.stance = Stance(got)
-        view.stance_bases = _basis(p[1], "政策取向")
-
+        if len(p) < 2:
+            view.read_notes.append(f"政策取向一行字段不足（需 2 段），没读进来：{m.group(1)}")
+        elif got is None:
+            view.read_notes.append(
+                f"政策取向「{p[0]}」不是 " + "/".join(x.value for x in Stance)
+                + " 之一，没读进来")
+        else:
+            view.stance = Stance(got)
+            bases, prob, cites = Q.basis(" | ".join(p[1:]), corpus)
+            view.stance_bases = bases
+            if prob:
+                view.read_notes.append(f"政策取向的依据：{prob}")
     for m in _CHANNEL.finditer(text):
-        p = _split(m.group(1), 4, "传导")
+        p = _cells(m.group(1))
+        if len(p) < 4:
+            view.read_notes.append(f"传导一行字段不足（需 4 段），没读进来：{m.group(1)}")
+            continue
         got = pick(p[2], [x.value for x in Impact])
         if got is None:
-            raise MacroViewFailed(
-                "传导影响须为 " + "/".join(x.value for x in Impact)
-                + f"，得到 {p[2]!r}")
-        impact = Impact(got)
-        view.channels.append(Channel(p[0], p[1], impact, _basis(p[3], "传导")))
+            view.read_notes.append(
+                f"传导「{p[0]}」的影响「{p[2]}」不是 " + "/".join(x.value for x in Impact)
+                + " 之一，没读进来")
+            continue
+        bases, prob, cites = Q.basis(" | ".join(p[3:]), corpus)
+        if prob:
+            view.read_notes.append(f"传导「{p[0]}」的依据：{prob}")
+        view.channels.append(Channel(p[0], p[1], Impact(got), bases, cites))
     return view
 
 
@@ -164,51 +159,68 @@ def cross_check(view: MacroView, ledger: FactLedger, as_of: date) -> list[str]:
             f"要么判断有误，要么利差被别的因素主导，正文需说明是哪一种"]
 
 
-def check(view: MacroView, ledger: FactLedger, evidence: str, as_of: date,
-          corpus=None) -> None:
+def facts(view: MacroView, ledger: FactLedger, as_of: date, corpus=None) -> list[str]:
+    """代码核对出的事实，交审核者（子项目四：此前这些是驳回理由）。"""
+    out: list[str] = []
     if view.stance is None:
-        raise MacroViewFailed("未给出政策取向 —— 「中性」也是判断，须显式说出")
+        out.append("没有给出政策取向（「中性」也是判断，方法论要求显式说出）")
     if len(view.channels) < MIN_CHANNELS:
-        raise MacroViewFailed(
-            f"传导路径至少 {MIN_CHANNELS} 条 —— 「政策转向宽松」写在哪篇研报里"
-            "都成立，对判断本标的没有帮助；有帮助的是那条链")
-
+        out.append(f"传导路径只读到 {len(view.channels)} 条（方法论要求至少 "
+                   f"{MIN_CHANNELS} 条：写不出链条的政策判断等于没写）")
     from ir_agent.citation import audit_prose
 
-    prose = {"政策取向描述": "", **{f"传导「{c.driver[:12]}」": c.driver + c.path
-                                    for c in view.channels}}
-    bad = audit_prose(prose)
+    bad = audit_prose({f"传导「{c.driver[:12]}」": c.driver + c.path
+                       for c in view.channels})
     if bad:
-        raise MacroViewFailed(
-            "描述里出现了账本外的数字：" + "、".join(bad) +
-            " —— 这些描述会原样进入研报撰写环节，而那里的数字必须可溯源；"
-            "请改成不带具体数字的说法，或改用占位符")
-
+        out.append("描述里有账本外的数字：" + "、".join(bad))
     holder = [view] + view.channels
-    attribute(holder, evidence, corpus)
+    attribute(holder, "", corpus)
     flat = [b for x in holder for b in x.bases]
-    problems = validate(flat, ledger=ledger, body=evidence, as_of=as_of,
-                        corpus=corpus)
-    if problems:
-        raise MacroViewFailed("依据不可核验：" + "；".join(problems))
+    out += [f"依据核验不过：{p}" for p in validate(flat, ledger=ledger, body="",
+                                                  as_of=as_of, corpus=corpus)]
+    out += [f"与期限利差交叉核对：{x}" for x in cross_check(view, ledger, as_of)]
+    return out
+
+
+KINDS = ("取向", "传导", "遗漏")
+
+
+def checklist(view: MacroView) -> list[tuple[str, str]]:
+    """按方法论生成（用户决定 5）：取向一条、每条传导一条（一条没读进来时列「传导路径
+    （未给出）」）、遗漏一条。"""
+    items = [("取向", "政策取向")]
+    items += [("传导", f"第{i}条 {c.driver}") for i, c in enumerate(view.channels, 1)] \
+        or [("传导", "传导路径（未给出）")]
+    return items + [("遗漏", "宏观因素")]
+
+
+RULES = Q.make_rules("宏观定性", """\
+- 取向：宽松/中性/收紧与证据是否一致；与账本里的利率、信用数据（如期限利差形态，代码
+  给了交叉核对结果）是否矛盾。没给出政策取向也在这条判。
+- 传导：链条每一环是否成立、是否真的传到本行业；利好/中性/利空与链条方向是否一致；
+  依据能否支撑。读到的传导少于两条时，在「传导路径（未给出）」或任一条里指出。
+- 遗漏：有没有漏掉对本行业最要紧的宏观因素。""")
 
 
 PROMPT = """基于下面的宏观资料，给出政策取向与它到本行业的传导路径。
 
 **一、政策取向**（一条）
-格式：`政策取向: <宽松|中性|收紧> | 「原文逐字引用」或 [[占位符]]`
+格式：`政策取向: <宽松|中性|收紧> | <依据>`
 
 **二、传导路径**（至少两条）
 「货币政策转向宽松」写在任何一篇研报里都成立，对判断本标的没有帮助。
 有帮助的是那条链：宽松 → 居民收入预期改善 → 商务宴请场景恢复 →
 高端白酒动销回升。**写不出链条的政策判断等于没写。**
-格式：`传导: <政策或宏观事件> | <传导到本行业的路径> | <利好|中性|利空> | 「原文」或 [[占位符]]`
+格式：`传导: <政策或宏观事件> | <传导到本行业的路径> | <利好|中性|利空> | <依据>`
+
+{basis_rules}
 
-**硬性要求**
-- 引用必须**逐字照抄**下面的资料，一个字都不能改。
+**要求**
 - 资料里没讲到的，不要凭印象补。
-- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本 —— 一个无处可查的数字会让整篇报告卡在无法修复的驳回上。需要数字时用给你的占位符，否则改成不带数字的说法。
-- 你的政策取向会与期限利差形态交叉核对，方向相反时需在正文说明缘由。
+- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本。需要数字时用给你的占位符，否则改成不带数字的说法。
+- 你的政策取向会与期限利差形态交叉核对，方向相反时需说明缘由。
+
+{review_note}
 
 {evidence}
 """
@@ -255,23 +267,19 @@ def for_prompt(corpus, macro_prompt: str = "") -> str:
 
 
 def assess(client, corpus, ledger: FactLedger, as_of: date,
-           macro_prompt: str = "", max_attempts: int = 3):
+           macro_prompt: str = "", *, judge, catalog: str = "",
+           max_rounds: int = Q.MAX_ROUNDS):
+    """撰写者写、审核者审（子项目四）。返回 (结论, 证据文本)；审核不过抛
+    `qual_review.ReviewStop`，模型接口错误原样抛出。"""
     evidence = for_prompt(corpus, macro_prompt)
-    prompt = PROMPT.format(evidence=evidence)
-    last, raw = "", ""
-    for attempt in range(1, max_attempts + 1):
-        out = raw = client(prompt)
-        try:
-            view = parse(out)
-            check(view, ledger, "", as_of, corpus)
-        except MacroViewFailed as e:
-            last = str(e)
-            prompt = (f"{prompt}\n\n---\n"
-                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
-                      "请修正后按格式重新输出。\n")
-            continue
-        view.conflicts = cross_check(view, ledger, as_of)
-        return view, evidence
-    raise MacroViewFailed(
-            f"连续 {max_attempts} 次给不出可核验的宏观定性判断：{last}"
-            f"\n最后一次的原始输出（末 400 字）：\n{_tail(raw)}")
+    prompt = PROMPT.format(evidence=evidence, basis_rules=Q.BASIS_RULES,
+                           review_note=Q.REVIEW_NOTE)
+    view, res, rounds = Q.run(
+        client, judge, name="宏观定性", prompt=prompt, materials=evidence,
+        read=lambda raw: parse(raw, corpus),
+        facts=lambda v: facts(v, ledger, as_of, corpus),
+        checklist=checklist, kinds=KINDS, rules=RULES, catalog=catalog,
+        hay=evidence, ledger=ledger, as_of=as_of, max_rounds=max_rounds)
+    view.conflicts = cross_check(view, ledger, as_of)
+    view.review, view.rounds = res, rounds
+    return view, evidence
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2443 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `checklist` 里传导条目改为不按条数列 → 清单测试红
- `facts` 里删掉「没有给出政策取向」那一句 → 「未给取向记进事实」测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(macro_view): 宏观定性改为撰写者 + 审核者 —— 只读不驳回，按方法论出清单

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 战略分析接上引擎

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `ir_agent/strategy.py`
- Modify: `tests/test_strategy.py`

**Interfaces:**
- Produces: `strategy.parse(text, corpus=None)`；`facts(view, ledger, evidence, as_of, corpus=None)`；`checklist(view)`；`KINDS`、`RULES`；`assess(client, ledger, period, as_of, sections, spot=None, *, judge, catalog='', corpus=None, max_rounds=3)`；`StrategyView.read_notes/review/rounds`；各 view 项的 `cites`

**为什么：** 同 Task 3 的改法。清单：[护城河] 每条（一条都没有列「护城河（未给出）」）、[五力] 固定 5 条、[不确定性] 每条（不足 2 条补「第N条（未给出）」）、[自洽] 一条；规则写明「应付账款周转天数短 = 对上游议价**弱**」（生益、茅台两次成稿辩论都抓到这一误读）。依据可引研报、可转述 —— 中国巨石 2026-10-17 就卡在这里。

- [ ] **Step 1: 写测试**

```diff
diff --git a/tests/test_strategy.py b/tests/test_strategy.py
index 8ba8abf..5ba8f14 100644
--- a/tests/test_strategy.py
+++ b/tests/test_strategy.py
@@ -11,6 +11,7 @@ from ir_agent import strategy
 from ir_agent.ledger import Fact, FactLedger, Method
 from ir_agent.sources.annual_sections import Section, SectionSet
 from ir_agent.strategy import Force, MoatKind, StrategyFailed
+from judge_stub import Judge
 
 D = Decimal
 AS_OF = date(2026, 9, 7)
@@ -59,34 +60,40 @@ def test_解析三部分():
                                                    "gross_margin"}
 
 
-def test_未知的护城河类型被驳回():
-    with pytest.raises(StrategyFailed, match="未知的护城河类型"):
-        strategy.parse(f"护城河: 品牌溢价 | 宽 | 「{QUOTE}」")
+def test_未知的护城河类型_记进读数事实():
+    v = strategy.parse(f"护城河: 品牌溢价 | 宽 | 「{QUOTE}」")
+    assert v.moats == [] and "不是规定的类型" in "\n".join(v.read_notes)
 
 
-def test_强度取值受限():
-    with pytest.raises(StrategyFailed, match="宽/窄/无"):
-        strategy.parse(f"护城河: 无形资产 | 很强 | 「{QUOTE}」")
-    with pytest.raises(StrategyFailed, match="高/中/低"):
-        strategy.parse(f"五力: 现有竞争强度 | 很激烈 | 中性 | 「{QUOTE}」")
+def test_强度不是规定的词_照读并记事实():
+    v = strategy.parse(f"护城河: 无形资产 | 很强 | 「{QUOTE}」")
+    assert v.moats and "宽/窄/无" in "\n".join(v.read_notes)
+    v = strategy.parse(f"五力: 现有竞争强度 | 很激烈 | 中性 | 「{QUOTE}」")
+    assert v.forces and "高/中/低" in "\n".join(v.read_notes)
 
 
-def test_字段不足直接驳回():
-    with pytest.raises(StrategyFailed, match="字段不足"):
-        strategy.parse("护城河: 无形资产 | 宽")
+def test_字段不足_记进读数事实():
+    v = strategy.parse("护城河: 无形资产 | 宽")
+    assert v.moats == [] and "字段不足" in "\n".join(v.read_notes)
 
 
-def test_缺依据驳回():
-    with pytest.raises(StrategyFailed, match="缺少可核验的依据"):
-        strategy.parse("护城河: 无形资产 | 宽 | 这是我的判断")
+def test_缺依据_记进读数事实():
+    v = strategy.parse("护城河: 无形资产 | 宽 | 这是我的判断")
+    assert v.moats and "没有给出依据" in "\n".join(v.read_notes)
+
+
+def test_引研报或转述也读得进():
+    """中国巨石 2026-10-17：五力引了研报，被判「需年报原文或占位符」连挂三次。"""
+    v = strategy.parse("五力: 现有竞争强度 | 高 | 对本司不利 | 转述：综合 东莞证券《玻纤行业深度报告》")
+    assert v.forces and v.forces[0].cites == ["玻纤行业深度报告"]
 
 
 # ── 桥：不确定性必须映射到可调参数 ───────────────────────────
 
-def test_驱动因子必须在可调参数之列():
+def test_驱动因子不在可调参数之列_记事实():
     """「宏观需求波动」映射不到任何参数，在情景分析里没有出口。"""
-    with pytest.raises(StrategyFailed, match="不在可调参数之列"):
-        strategy.parse(f"不确定性: 需求波动 | 宏观环境 | 下调 | 「{QUOTE}」")
+    v = strategy.parse(f"不确定性: 需求波动 | 宏观环境 | 下调 | 「{QUOTE}」")
+    assert v.uncertainties and "不在可调参数之列" in "\n".join(v.read_notes)
 
 
 def test_可调参数与建模假设对齐():
@@ -94,41 +101,38 @@ def test_可调参数与建模假设对齐():
             "terminal_growth"} <= set(strategy.MAPPABLE_DRIVERS)
 
 
-def test_不确定性少于两条驳回():
+def test_不确定性少于两条记进事实():
     """少于两条，Bear/Base/Bull 就没有定性依据，只能靠调参数。"""
     v = strategy.parse(_out(uncerts=[
         f"不确定性: 渠道去库存 | revenue_growth | 下调 | 「{QUOTE}」"]))
-    with pytest.raises(StrategyFailed, match="至少两条"):
-        strategy.check(v, _led(), EVIDENCE, AS_OF)
+    assert any("至少两条" in x for x in strategy.facts(v, _led(), EVIDENCE, AS_OF))
 
 
 # ── 校验 ─────────────────────────────────────────────────────
 
 def test_引文必须逐字出自年报摘录():
     v = strategy.parse(_out(moats=["护城河: 无形资产 | 宽 | 「公司品牌力极强」"]))
-    with pytest.raises(StrategyFailed, match="不可核验"):
-        strategy.check(v, _led(), EVIDENCE, AS_OF)
+    assert any("核验不过" in x for x in strategy.facts(v, _led(), EVIDENCE, AS_OF))
 
 
 def test_五力必须五条都给():
     v = strategy.parse(_out(forces=[
         f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」"
         for f in list(Force)[:3]]))
-    with pytest.raises(StrategyFailed, match="五力须五条都给"):
-        strategy.check(v, _led(), EVIDENCE, AS_OF)
+    assert any("五力缺" in x for x in strategy.facts(v, _led(), EVIDENCE, AS_OF))
 
 
 def test_无护城河也要显式说出():
     """「无明显护城河」是结论，不是省略的理由。"""
     v = strategy.parse(_out(moats=[]))
-    with pytest.raises(StrategyFailed, match="也是结论"):
-        strategy.check(v, _led(), EVIDENCE, AS_OF)
+    assert any("也是结论" in x for x in strategy.facts(v, _led(), EVIDENCE, AS_OF))
     ok = strategy.parse(_out(moats=[f"护城河: 无明显护城河 | 无 | 「{QUOTE}」"]))
-    strategy.check(ok, _led(), EVIDENCE, AS_OF)
+    assert strategy.facts(ok, _led(), EVIDENCE, AS_OF) == []
+    assert ("护城河", "护城河（未给出）") in strategy.checklist(v)
 
 
 def test_合法输入通过():
-    strategy.check(strategy.parse(_out()), _led(), EVIDENCE, AS_OF)
+    assert strategy.facts(strategy.parse(_out()), _led(), EVIDENCE, AS_OF) == []
 
 
 def test_占位符依据也可核验():
@@ -137,7 +141,7 @@ def test_占位符依据也可核验():
         "五力: 现有竞争强度 | 高 | 对本司不利 | [[industry.hhi@2024FY]]",
         *[f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」"
           for f in list(Force)[1:]]]))
-    strategy.check(v, led, EVIDENCE, AS_OF)
+    assert strategy.facts(v, led, EVIDENCE, AS_OF) == []
 
 
 def _sections():
@@ -165,7 +169,7 @@ def test_替代品威胁没有锚点():
 
 def test_无锚点的力被标注为纯定性():
     c = _Client(_out())
-    v, _ = strategy.assess(c, _led("industry.hhi"), P, AS_OF, _sections())
+    v, _ = strategy.assess(c, _led("industry.hhi"), P, AS_OF, _sections(), judge=Judge())
     assert Force.SUBSTITUTES in v.unquantified
     assert "纯定性判断" in v.summary()
 
@@ -184,43 +188,54 @@ def test_提示词含年报摘录与缺失说明():
 
 def test_提示词点明矛盾要写出来():
     c = _Client(_out())
-    strategy.assess(c, _led("industry.hhi"), P, AS_OF, _sections())
+    strategy.assess(c, _led("industry.hhi"), P, AS_OF, _sections(), judge=Judge())
     assert "把矛盾写出来" in c.prompts[0]
 
 
 def test_提示词列出可调参数():
     c = _Client(_out())
-    strategy.assess(c, _led(), P, AS_OF, _sections())
+    strategy.assess(c, _led(), P, AS_OF, _sections(), judge=Judge())
     assert "revenue_growth" in c.prompts[0] and "gross_margin" in c.prompts[0]
 
 
-def test_无年报摘录时引文无处可核验():
-    """没有年报就没有可引用的原文 —— 此时应当失败，而不是放行一段
-    无从核验的「战略分析」。"""
-    with pytest.raises(StrategyFailed):
-        strategy.assess(_Client(_out(), _out(), _out()),
-                        _led(), P, AS_OF, None)
+def test_无年报摘录时引文核验不过_记进事实():
+    """年报摘录缺失时，年报引文无处可核验 —— 现在作为事实交审核者。"""
+    j = Judge()
+    strategy.assess(_Client(_out()), _led(), P, AS_OF, None, judge=j)
+    assert "核验不过" in j.prompts[0]
 
 
 # ── 端到端 ───────────────────────────────────────────────────
 
-def test_驳回后重试并带回原因():
-    bad = _out(uncerts=[f"不确定性: 需求波动 | 宏观环境 | 下调 | 「{QUOTE}」",
-                        f"不确定性: 提价 | gross_margin | 下调 | 「{QUOTE}」"])
-    c = _Client(bad, _out())
-    strategy.assess(c, _led(), P, AS_OF, _sections())
-    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]
+def test_审核致命则带建议重写():
+    c = _Client(_out(), _out())
+    strategy.assess(c, _led(), P, AS_OF, _sections(),
+                    judge=Judge({("自洽", "护城河、五力与不确定性"): "致命"}, {}))
+    assert len(c.prompts) == 2 and "上一轮审核驳回" in c.prompts[1]
+
+
+def test_三轮审核不过则停下():
+    from ir_agent.qual_review import ReviewStop
+    with pytest.raises(ReviewStop, match="战略分析审核 3 轮未通过"):
+        strategy.assess(_Client(_out(), _out(), _out()), _led(), P, AS_OF, _sections(),
+                        judge=Judge({"五力": "致命"}))
+
+
+def test_审核清单按方法论生成():
+    """用户决定 5：五力固定 5 条；不确定性不足 2 条补到 2 条。"""
+    items = strategy.checklist(strategy.parse(_out(uncerts=[])))
+    assert [s for k, s in items if k == "五力"] == [f.value for f in Force]
+    assert [s for k, s in items if k == "不确定性"] == ["第1条（未给出）", "第2条（未给出）"]
+    assert items[-1] == ("自洽", "护城河、五力与不确定性")
 
 
-def test_连续失败则拒绝出结论():
-    """没有依据的战略判断在研报里比空白更危险。"""
-    bad = _out(moats=["护城河: 无形资产 | 宽 | 「查无此句」"])
-    with pytest.raises(StrategyFailed, match="连续"):
-        strategy.assess(_Client(bad, bad, bad), _led(), P, AS_OF, _sections())
+def test_规则写明应付天数短是议价弱():
+    """生益、茅台两次成稿辩论都抓到「应付账款周转天数短 = 议价强」这一误读。"""
+    assert "应付账款周转天数短" in strategy.RULES and "**弱**" in strategy.RULES
 
 
 def test_摘要含三部分():
-    v, _ = strategy.assess(_Client(_out()), _led(), P, AS_OF, _sections())
+    v, _ = strategy.assess(_Client(_out()), _led(), P, AS_OF, _sections(), judge=Judge())
     txt = v.summary()
     assert "护城河" in txt and "现有竞争强度" in txt
     assert "关键不确定性 → 情景驱动因子" in txt
@@ -246,7 +261,7 @@ def test_可引用年报之外的资料():
     """只看年报，战略判断的全部依据是公司对自己的描述 —— 相当于让
     被评价方写自己的评价。"""
     v = strategy.parse(_out(moats=[f"护城河: 无形资产 | 宽 | 「{EXT}」"]))
-    strategy.check(v, _led(), QUOTE, AS_OF, corpus=_corpus())
+    strategy.facts(v, _led(), QUOTE, AS_OF, corpus=_corpus())
 
 
 def test_出处由代码判定而非模型声明():
@@ -255,7 +270,7 @@ def test_出处由代码判定而非模型声明():
     from ir_agent.basis import BasisKind
 
     v = strategy.parse(_out(moats=[f"护城河: 无形资产 | 宽 | 「{EXT}」"]))
-    strategy.check(v, _led(), QUOTE, AS_OF, corpus=_corpus())
+    strategy.facts(v, _led(), QUOTE, AS_OF, corpus=_corpus())
     b = v.moats[0].bases[0]
     assert b.kind is BasisKind.EXTERNAL
     assert b.ref.endswith("A1.html") and b.retrieved == AS_OF
@@ -265,14 +280,13 @@ def test_年报引文不会被改标为外部():
     from ir_agent.basis import BasisKind
 
     v = strategy.parse(_out())
-    strategy.check(v, _led(), QUOTE, AS_OF, corpus=_corpus())
+    strategy.facts(v, _led(), QUOTE, AS_OF, corpus=_corpus())
     assert v.moats[0].bases[0].kind is BasisKind.REPORT
 
 
-def test_两处都找不到仍然驳回():
+def test_两处都找不到记进事实():
     v = strategy.parse(_out(moats=["护城河: 无形资产 | 宽 | 「行业景气显著回升」"]))
-    with pytest.raises(StrategyFailed, match="不可核验|找不到"):
-        strategy.check(v, _led(), QUOTE, AS_OF, corpus=_corpus())
+    assert any("核验不过" in x for x in strategy.facts(v, _led(), QUOTE, AS_OF, corpus=_corpus()))
 
 
 def test_提示词带上外部资料与其可引用范围():
@@ -342,38 +356,31 @@ def test_年报正文与外部资料严格分开():
     assert QUOTE in strategy.annual_text(_sections())
 
 
-def test_力名称写错时列出合法取值():
-    """**长电实测: 战略分析因此整个没能产出。** 模型写「现有竞争者的竞争
-    强度」，与枚举值「现有竞争强度」只差两个字，而驳回信息只说「未知的力」，
-    不给对照表 —— 连挂三次。护城河那一行的提示词列了取值，五力这一行没列，
-    是纯粹的疏漏。"""
-    with pytest.raises(StrategyFailed) as e:
-        strategy.parse(f"五力: 现有竞争者的竞争强度 | 高 | 中性 | 「{QUOTE}」")
+def test_力名称写错时_事实里列出合法取值():
+    """长电实测：「现有竞争者的竞争强度」与枚举只差两个字。"""
+    v = strategy.parse(f"五力: 现有竞争者的竞争强度 | 高 | 中性 | 「{QUOTE}」")
     for f in Force:
-        assert f.value in str(e.value)
+        assert f.value in "\n".join(v.read_notes)
 
 
-def test_护城河类型写错时也列出合法取值():
-    with pytest.raises(StrategyFailed) as e:
-        strategy.parse(f"护城河: 品牌溢价 | 宽 | 「{QUOTE}」")
-    assert "无形资产" in str(e.value) and "成本优势" in str(e.value)
+def test_护城河类型写错时_事实里也列出合法取值():
+    v = strategy.parse(f"护城河: 品牌溢价 | 宽 | 「{QUOTE}」")
+    notes = "\n".join(v.read_notes)
+    assert "无形资产" in notes and "成本优势" in notes
 
 
 def test_提示词逐字列出五力名称():
     c = _Client(_out())
-    strategy.assess(c, _led(), P, AS_OF, _sections())
+    strategy.assess(c, _led(), P, AS_OF, _sections(), judge=Judge())
     for f in Force:
         assert f.value in c.prompts[0]
     assert "逐字照抄" in c.prompts[0]
 
 
-def test_失败信息带上模型最后写了什么():
-    """**只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。**
-    前几轮排查全跑失败时，每次都要单独再跑一次去抓原始输出 —— 而那几次的
-    根因（列表前缀、括号补注）看一眼原文就清楚了。"""
+def test_停下时带上最后一稿():
+    from ir_agent.qual_review import ReviewStop
     bad = "五力: 替代品威胁 | 低 | 中性 | 年报未提及替代品，属定性判断"
-    with pytest.raises(StrategyFailed) as e:
-        strategy.assess(_Client(bad, bad, bad), _led(), P, AS_OF, _sections())
-    msg = str(e.value)
-    assert "最后一次的原始输出" in msg
-    assert "年报未提及替代品" in msg
+    with pytest.raises(ReviewStop) as e:
+        strategy.assess(_Client(bad, bad, bad), _led(), P, AS_OF, _sections(),
+                        judge=Judge({"五力": "致命"}))
+    assert "年报未提及替代品" in e.value.last
```

- [ ] **Step 2: 跑测试**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`30 failed, 2417 passed, 8 skipped`。失败的是：

- `tests/test_strategy.py::test_未知的护城河类型_记进读数事实`
- `tests/test_strategy.py::test_强度不是规定的词_照读并记事实`
- `tests/test_strategy.py::test_字段不足_记进读数事实`
- `tests/test_strategy.py::test_缺依据_记进读数事实`
- `tests/test_strategy.py::test_引研报或转述也读得进`
- `tests/test_strategy.py::test_驱动因子不在可调参数之列_记事实`
- `tests/test_strategy.py::test_不确定性少于两条记进事实`
- `tests/test_strategy.py::test_引文必须逐字出自年报摘录`
- `tests/test_strategy.py::test_五力必须五条都给`
- `tests/test_strategy.py::test_无护城河也要显式说出`
- `tests/test_strategy.py::test_合法输入通过`
- `tests/test_strategy.py::test_占位符依据也可核验`
- `tests/test_strategy.py::test_无锚点的力被标注为纯定性`
- `tests/test_strategy.py::test_提示词点明矛盾要写出来`
- `tests/test_strategy.py::test_提示词列出可调参数`
- `tests/test_strategy.py::test_无年报摘录时引文核验不过_记进事实`
- `tests/test_strategy.py::test_审核致命则带建议重写`
- `tests/test_strategy.py::test_三轮审核不过则停下`
- `tests/test_strategy.py::test_审核清单按方法论生成`
- `tests/test_strategy.py::test_规则写明应付天数短是议价弱`
- `tests/test_strategy.py::test_摘要含三部分`
- `tests/test_strategy.py::test_可引用年报之外的资料`
- `tests/test_strategy.py::test_出处由代码判定而非模型声明`
- `tests/test_strategy.py::test_年报引文不会被改标为外部`
- `tests/test_strategy.py::test_两处都找不到记进事实`
- `tests/test_strategy.py::test_力名称写错时_事实里列出合法取值`
- `tests/test_strategy.py::test_护城河类型写错时_事实里也列出合法取值`
- `tests/test_strategy.py::test_提示词逐字列出五力名称`
- `tests/test_strategy.py::test_停下时带上最后一稿`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/full.py b/ir_agent/full.py
index 5f91790..5357a06 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -583,7 +583,7 @@ def _load_strategy(r, as_of: date, client, spot=None) -> tuple[str, str, str]:
         return "", "", sec_err or "战略分析（无任何定性证据）"
     try:
         view, _ = strategy.assess(client, r.ledger, r.period, as_of,
-                                  sections, spot, corpus=r.corpus)
+                                  sections, spot, judge=client, corpus=r.corpus)
     except Exception as e:                                  # noqa: BLE001
         return "", "", f"战略分析（{e.__class__.__name__}: {str(e)[:120]}）"
     r.sections = sections
diff --git a/ir_agent/strategy.py b/ir_agent/strategy.py
index b0915d4..94524af 100644
--- a/ir_agent/strategy.py
+++ b/ir_agent/strategy.py
@@ -31,7 +31,8 @@ from enum import Enum
 from ir_agent.lineparse import keyed, pick
 from ir_agent.ledger import FactLedger, LookAheadError
 from ir_agent.sources.external import attribute
-from ir_agent.basis import Basis, InvalidBasis, validate
+from ir_agent.basis import Basis, validate
+from ir_agent import qual_review as Q
 
 
 class MoatKind(str, Enum):
@@ -80,21 +81,13 @@ STRENGTH = ("宽", "窄", "无")
 
 
 
-def _tail(raw: str, n: int = 400) -> str:
-    """失败信息里带上模型最后写了什么。
-
-    **只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。** 前几轮
-    排查全跑失败时，每次都要单独再跑一次去抓原始输出 —— 而那几次的根因
-    （列表前缀、括号补注、写「无据」）看一眼原文就清楚了。
-    """
-    raw = (raw or "").strip()
-    return raw[-n:] if len(raw) > n else raw
 
 @dataclass(frozen=True)
 class MoatView:
     kind: MoatKind
     strength: str                       # 宽 / 窄 / 无
     bases: list[Basis] = field(default_factory=list)
+    cites: list = field(default_factory=list)       # 转述指向的文章标题
 
     def describe(self) -> str:
         if self.kind is MoatKind.NONE:
@@ -108,6 +101,7 @@ class ForceView:
     intensity: str                      # 高 / 中 / 低
     verdict: str                        # 对本司有利 / 中性 / 不利
     bases: list[Basis] = field(default_factory=list)
+    cites: list = field(default_factory=list)
 
     def describe(self) -> str:
         return f"{self.force.value} {self.intensity}　→ {self.verdict}"
@@ -125,6 +119,7 @@ class Uncertainty:
     driver: str                         # 对应的建模驱动因子
     direction: str                      # 恶化时该因子往哪个方向走
     bases: list[Basis] = field(default_factory=list)
+    cites: list = field(default_factory=list)
 
     def describe(self) -> str:
         return f"{self.what} → {self.driver}（不利时{self.direction}）"
@@ -149,6 +144,10 @@ class StrategyView:
     forces: list[ForceView] = field(default_factory=list)
     uncertainties: list[Uncertainty] = field(default_factory=list)
     unquantified: list[Force] = field(default_factory=list)
+    # 子项目四：代码读数时的事实、审核结论、各轮记录。
+    read_notes: list[str] = field(default_factory=list)
+    review: object = None
+    rounds: list = field(default_factory=list)
 
     def summary(self) -> str:
         lines = ["战略分析"]
@@ -163,12 +162,13 @@ class StrategyView:
             lines.append("  关键不确定性 → 情景驱动因子")
             for u in self.uncertainties:
                 lines.append(f"    {u.describe()}")
+        lines += Q.review_tail(self.review)
         return "\n".join(lines)
 
 
 class StrategyFailed(Exception):
-    """模型给不出可核验的战略判断。不退回一段「看起来像分析」的文字 ——
-    没有依据的战略判断在研报里比空白更危险。"""
+    """战略分析无从进行（没有任何定性证据等）。**内容是否成立不再由代码驳回**
+    （子项目四，spec 2026-10-17）：交给审核者，审核不过抛 `qual_review.ReviewStop`。"""
 
 
 # ── 量化锚点 ──────────────────────────────────────────────────
@@ -240,7 +240,7 @@ def for_prompt(ledger: FactLedger, period: str, as_of: date,
 
     if corpus is not None and corpus.docs:
         from ir_agent.sources.external import TIER_LABEL, TIER_RULE, Tier
-        lines.append("\n  公司之外的资料（引用时同样须逐字照抄）：")
+        lines.append("\n  公司之外的资料（可引用原文，也可转述 —— 转述要写明采纳了哪几篇）：")
         seen_tiers = {d.tier for d in corpus.docs}
         # 可引用范围要先说清楚，模型才是带着这条界线去读下面的资料 ——
         # 放在资料读完之后才说，等于让它先斩后奏。
@@ -282,25 +282,28 @@ PROMPT = """基于下面的年报原文与账本事实，做战略分析。
 后续的估值练习提供输入**。因此本节的产出不是几段文字，而是三样东西。
 
 **一、护城河**（至多两条，宁缺毋滥）
-格式：`护城河: <成本优势|网络效应|无形资产|转换成本|有效规模|无明显护城河> | <宽|窄|无> | 「年报原文逐字引用」`
+格式：`护城河: <成本优势|网络效应|无形资产|转换成本|有效规模|无明显护城河> | <宽|窄|无> | <依据>`
 
 **二、波特五力**（五条都要给，名称必须逐字照抄，不要改写）
-格式：`五力: <{forces}> | <高|中|低> | <对本司有利|中性|对本司不利> | 「年报原文」或 [[占位符]]`
+格式：`五力: <{forces}> | <高|中|低> | <对本司有利|中性|对本司不利> | <依据>`
 
 **三、关键不确定性 → 情景驱动因子**（两到三条）
 这一条是战略分析与估值之间的桥。每条不确定性必须映射到一个**可调的
 建模参数**，否则它在情景分析里没有出口。
 可选驱动因子：{drivers}
-格式：`不确定性: <描述> | <驱动因子> | <不利时该因子如何变化> | 「年报原文」或 [[占位符]]`
+格式：`不确定性: <描述> | <驱动因子> | <不利时该因子如何变化> | <依据>`
 
-**硬性要求**
-- 引年报时必须**逐字照抄**，一个字都不能改，否则核验通不过。
-- 占位符只能用上面列出的那些。
+{basis_rules}
+
+**要求**
+- 占位符只能用下面列出的那些。
 - 年报未设的章节，相关判断**明说无据**，不要凭印象补足。
-- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本 —— 一个无处可查的数字会让整篇报告卡在无法修复的驳回上。需要数字时用给你的占位符，否则改成不带数字的说法。
+- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本。需要数字时用给你的占位符，否则改成不带数字的说法。
 - 判断与数字打架时（例如年报自述成本优势、而毛利率处于同业低分位），
   **把矛盾写出来**，那正是研报该说的东西。
 
+{review_note}
+
 {evidence}
 """
 
@@ -311,141 +314,145 @@ _QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)
 _REF = re.compile(r"\[\[[^\]]+\]\]")
 
 
-def _basis(raw: str, what: str) -> list[Basis]:
-    raw = raw.strip()
-    ref = _REF.search(raw)
-    if ref:
-        try:
-            return [Basis.fact(ref.group(0))]
-        except InvalidBasis as e:
-            raise StrategyFailed(f"{what} 的占位符不合法: {e}") from e
-    q = _QUOTE.search(raw)
-    if not q:
-        raise StrategyFailed(f"{what} 缺少可核验的依据（需年报原文或占位符）")
-    try:
-        return [Basis.report(q.group(1))]
-    except InvalidBasis as e:
-        raise StrategyFailed(f"{what} 的依据不合法: {e}") from e
-
-
-def _split(line: str, n: int, what: str) -> list[str]:
-    parts = [p.strip() for p in line.split("|")]
-    if len(parts) < n:
-        raise StrategyFailed(f"{what} 字段不足（需 {n} 段，得到 {len(parts)}）：{line}")
-    return parts
-
-
-def parse(text: str) -> StrategyView:
+
+
+
+
+def _cells(line: str) -> list[str]:
+    return [p.strip() for p in line.split("|")]
+
+
+def parse(text: str, corpus=None) -> StrategyView:
+    """读数。**只读，不驳回**（子项目四）：段数不够、护城河类型/力的名称不是规定的词
+    的行跳过；强弱等级不是规定的词、驱动因子不在可调参数之列的照读 —— 都记进
+    `read_notes`，作为事实交给审核者。"""
     view = StrategyView()
+    note = view.read_notes.append
+
+    def _b(parts, what):
+        bases, prob, cites = Q.basis(" | ".join(parts), corpus)
+        if prob:
+            note(f"{what}的依据：{prob}")
+        return bases, cites
 
     for m in _MOAT.finditer(text):
-        p = _split(m.group(1), 3, "护城河")
+        p = _cells(m.group(1))
+        if len(p) < 3:
+            note(f"护城河一行字段不足（需 3 段），没读进来：{m.group(1)}")
+            continue
         try:
             kind = MoatKind(p[0])
-        except ValueError as e:
-            raise StrategyFailed(
-                f"未知的护城河类型 {p[0]!r}，必须逐字使用以下之一："
-                + "、".join(k.value for k in MoatKind)) from e
+        except ValueError:
+            note(f"护城河类型「{p[0]}」不是规定的类型（"
+                 + "、".join(k.value for k in MoatKind) + "），没读进来")
+            continue
         strength = pick(p[1], STRENGTH)
         if kind is not MoatKind.NONE and strength is None:
-            raise StrategyFailed(
-                f"护城河强度须为 {'/'.join(STRENGTH)}，得到 {p[1]!r}")
-        view.moats.append(MoatView(kind, strength or p[1],
-                                   _basis(p[2], "护城河")))
+            note(f"护城河「{kind.value}」的宽度「{p[1]}」不是 {'/'.join(STRENGTH)} 之一")
+        bases, cites = _b(p[2:], f"护城河「{kind.value}」")
+        view.moats.append(MoatView(kind, strength or p[1], bases, cites))
 
     for m in _FORCE.finditer(text):
-        p = _split(m.group(1), 4, "五力")
+        p = _cells(m.group(1))
+        if len(p) < 4:
+            note(f"五力一行字段不足（需 4 段），没读进来：{m.group(1)}")
+            continue
         try:
             force = Force(p[0])
-        except ValueError as e:
-            # **驳回信息必须列出合法取值。** 长电实测: 模型写
-            # 「现有竞争者的竞争强度」，与枚举值「现有竞争强度」只差两个字，
-            # 而原信息只说「未知的力」，不给对照表 —— 模型连挂三次，
-            # 整个战略分析没能产出。护城河那一行的提示词列了取值，
-            # 五力这一行没列，是纯粹的疏漏。
-            raise StrategyFailed(
-                f"未知的力 {p[0]!r}，必须逐字使用以下五个名称之一："
-                + "、".join(f.value for f in Force)) from e
+        except ValueError:
+            # 长电实测：模型写「现有竞争者的竞争强度」，与「现有竞争强度」只差两字。
+            note(f"五力名称「{p[0]}」不是规定的五个名称之一（"
+                 + "、".join(f.value for f in Force) + "），没读进来")
+            continue
         intensity = pick(p[1], INTENSITY)
         if intensity is None:
-            raise StrategyFailed(
-                f"五力强度须为 {'/'.join(INTENSITY)}，得到 {p[1]!r}")
-        view.forces.append(ForceView(force, intensity, p[2],
-                                     _basis(p[3], "五力")))
+            note(f"五力「{force.value}」的强度「{p[1]}」不是 {'/'.join(INTENSITY)} 之一")
+        bases, cites = _b(p[3:], f"五力「{force.value}」")
+        view.forces.append(ForceView(force, intensity or p[1], p[2], bases, cites))
 
     for m in _UNCERT.finditer(text):
-        p = _split(m.group(1), 4, "不确定性")
+        p = _cells(m.group(1))
+        if len(p) < 4:
+            note(f"不确定性一行字段不足（需 4 段），没读进来：{m.group(1)}")
+            continue
         if p[1] not in MAPPABLE_DRIVERS:
-            raise StrategyFailed(
-                f"驱动因子 {p[1]!r} 不在可调参数之列 —— "
-                f"映射不到参数的不确定性在情景分析里没有出口。"
-                f"可选：{'、'.join(MAPPABLE_DRIVERS)}")
-        view.uncertainties.append(
-            Uncertainty(p[0], p[1], p[2], _basis(p[3], "不确定性")))
-
+            note(f"不确定性「{p[0]}」的驱动因子「{p[1]}」不在可调参数之列（可选："
+                 + "、".join(MAPPABLE_DRIVERS) + "）")
+        bases, cites = _b(p[3:], f"不确定性「{p[0]}」")
+        view.uncertainties.append(Uncertainty(p[0], p[1], p[2], bases, cites))
     return view
 
 
-def check(view: StrategyView, ledger: FactLedger, evidence: str,
-          as_of: date, corpus=None) -> None:
-    """依据必须可核验。REPORT 引文要逐字出现在年报摘录里，
-    EXTERNAL 引文要逐字出现在语料库里。"""
+def facts(view: StrategyView, ledger: FactLedger, evidence: str, as_of: date,
+          corpus=None) -> list[str]:
+    """代码核对出的事实，交审核者（子项目四：此前这些是驳回理由）。`evidence` 是
+    年报摘录原文 —— REPORT 引文对着它核验，外部引文对着语料库。"""
+    out: list[str] = []
     if not view.moats:
-        raise StrategyFailed("未给出护城河判断 —— 「无明显护城河」也是结论，"
-                             "但必须显式说出并给出依据")
+        out.append("没有给出护城河判断（「无明显护城河」也是结论，方法论要求显式说出）")
     got = {f.force for f in view.forces}
     if got != set(Force):
-        raise StrategyFailed("五力须五条都给，缺：" +
-                             "、".join(f.value for f in set(Force) - got))
+        out.append("五力缺：" + "、".join(f.value for f in Force if f not in got))
     if len(view.uncertainties) < 2:
-        raise StrategyFailed(
-            "关键不确定性至少两条 —— 少于两条，Bear/Base/Bull 三情景就没有"
-            "定性依据，只能靠调参数")
-
+        out.append(f"关键不确定性只读到 {len(view.uncertainties)} 条（方法论要求至少两条 —— "
+                   "少于两条，三情景就没有定性依据）")
     from ir_agent.citation import audit_prose
 
     bad = audit_prose({f"不确定性「{u.what[:12]}」": u.what + u.direction
                        for u in view.uncertainties})
     if bad:
-        raise StrategyFailed(
-            "描述里出现了账本外的数字：" + "、".join(bad) +
-            " —— 这些描述会原样进入研报撰写环节，而那里的数字必须可溯源；"
-            "请改成不带具体数字的说法")
+        out.append("描述里有账本外的数字：" + "、".join(bad))
+    items = (*view.moats, *view.forces, *view.uncertainties)
+    attribute(items, evidence, corpus)
+    flat = [b for x in items for b in x.bases]
+    out += [f"依据核验不过：{p}" for p in validate(flat, ledger=ledger, body=evidence,
+                                                  as_of=as_of, corpus=corpus)]
+    return out
+
 
-    attribute((*view.moats, *view.forces, *view.uncertainties),
-              evidence, corpus)
-    flat = [b for x in (*view.moats, *view.forces, *view.uncertainties)
-            for b in x.bases]
-    problems = validate(flat, ledger=ledger, body=evidence, as_of=as_of,
-                        corpus=corpus)
-    if problems:
-        raise StrategyFailed("依据不可核验：" + "；".join(problems))
+KINDS = ("护城河", "五力", "不确定性", "自洽")
+
+
+def checklist(view: StrategyView) -> list[tuple[str, str]]:
+    """按方法论生成（用户决定 5）：护城河每条（一条都没有列「未给出」）、五力固定 5 条、
+    不确定性每条（不足 2 条补「未给出」到 2 条）、自洽一条。"""
+    items = [("护城河", f"第{i}条 {m.kind.value}") for i, m in enumerate(view.moats, 1)] \
+        or [("护城河", "护城河（未给出）")]
+    items += [("五力", f.value) for f in Force]
+    unc = [("不确定性", f"第{i}条 {u.what[:16]}")
+           for i, u in enumerate(view.uncertainties, 1)]
+    unc += [("不确定性", f"第{i}条（未给出）") for i in range(len(unc) + 1, 3)]
+    return items + unc + [("自洽", "护城河、五力与不确定性")]
+
+
+RULES = Q.make_rules("战略分析", """\
+- 护城河：类型与宽度有没有证据支撑；「无明显护城河」也判是否成立。一条都没给也在这里判。
+- 五力：高/中/低与「对本司有利/中性/不利」是否一致；依据能否支撑；**与账本数据是否
+  矛盾** —— 应付账款周转天数短，常规理解是对上游议价**弱**（无力占用供应商资金），
+  不是强势；应收天数短、合同负债多才是对下游强势；毛利率分位也要对照。没给出的力判「未给出」。
+- 不确定性：是不是真正的关键变量；映射的驱动因子对不对（要能在情景分析里调）；「不利时
+  如何变化」方向对不对。
+- 自洽：护城河、五力、不确定性三部分是否彼此矛盾（例如宽护城河却五力全不利）。""")
 
 
 def assess(client, ledger: FactLedger, period: str, as_of: date,
-           sections, spot: str | None = None,
-           max_attempts: int = 3, corpus=None) -> tuple[StrategyView, str]:
-    """让模型做战略分析，逐轮校验。返回 (结论, 证据文本)。"""
+           sections, spot: str | None = None, *, judge, catalog: str = "",
+           corpus=None, max_rounds: int = Q.MAX_ROUNDS) -> tuple[StrategyView, str]:
+    """撰写者写、审核者审（子项目四）。返回 (结论, 证据文本)；审核不过抛
+    `qual_review.ReviewStop`，模型接口错误原样抛出。"""
     evidence = for_prompt(ledger, period, as_of, sections, spot, corpus)
     annual = annual_text(sections)
     prompt = PROMPT.format(drivers="、".join(MAPPABLE_DRIVERS),
                            forces="|".join(f.value for f in Force),
-                           evidence=evidence)
-    last, raw = "", ""
-    for attempt in range(1, max_attempts + 1):
-        out = raw = client(prompt)
-        try:
-            view = parse(out)
-            check(view, ledger, annual, as_of, corpus)
-        except StrategyFailed as e:
-            last = str(e)
-            prompt = (f"{prompt}\n\n---\n"
-                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
-                      "请修正后按格式重新输出全部三部分。\n")
-            continue
-        view.unquantified = [f for f in Force
-                             if f not in anchors(ledger, period, as_of, spot)]
-        return view, evidence
-    raise StrategyFailed(
-            f"连续 {max_attempts} 次给不出可核验的战略判断：{last}"
-            f"\n最后一次的原始输出（末 400 字）：\n{_tail(raw)}")
+                           evidence=evidence, basis_rules=Q.BASIS_RULES,
+                           review_note=Q.REVIEW_NOTE)
+    view, res, rounds = Q.run(
+        client, judge, name="战略分析", prompt=prompt, materials=evidence,
+        read=lambda raw: parse(raw, corpus),
+        facts=lambda v: facts(v, ledger, annual, as_of, corpus),
+        checklist=checklist, kinds=KINDS, rules=RULES, catalog=catalog,
+        hay=evidence, ledger=ledger, as_of=as_of, max_rounds=max_rounds)
+    view.unquantified = [f for f in Force
+                         if f not in anchors(ledger, period, as_of, spot)]
+    view.review, view.rounds = res, rounds
+    return view, evidence
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2446 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `checklist` 的五力改成按稿子生成 → Task 8 补的「五力只写了三条，清单仍列满五条」测试红
- `parse` 里研报引用当成缺依据（`Q.basis` 的引文分支关掉）→ 「引研报或转述也读得进」测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(strategy): 战略分析改为撰写者 + 审核者 —— 只读不驳回，按方法论出清单

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 产业链接上引擎

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `ir_agent/value_chain.py`
- Modify: `tests/test_prompt_contract.py`
- Modify: `tests/test_value_chain.py`

**Interfaces:**
- Produces: `value_chain.parse(text, corpus=None)`；`facts(view, ledger, evidence, as_of, corpus=None, period='')`；`checklist(view)`；`KINDS`、`RULES`；`analyse(client, corpus, ledger, period, as_of, sections=None, *, judge, catalog='', max_rounds=3)`；`ChainView.read_notes/review/rounds`

**为什么：** 同 Task 3 的改法。清单：[环节] 上游/本环节/下游固定 3 条、[议价] 对上游/对下游固定 2 条、[规模] 给了才列；与周转天数的交叉核对作为事实。`test_prompt_contract` 统一找 `facts`。

- [ ] **Step 1: 写测试**

```diff
diff --git a/tests/test_prompt_contract.py b/tests/test_prompt_contract.py
index b5df917..06e253f 100644
--- a/tests/test_prompt_contract.py
+++ b/tests/test_prompt_contract.py
@@ -322,9 +322,8 @@ def test_三个定性模块都装了这道闸(mod, exc, mk):
     import importlib
     import inspect
     m = importlib.import_module(f"ir_agent.{mod}")
-    # 子项目四起账本外数字只作事实（`facts()`），不再在 `check()` 里驳回。
-    fn = getattr(m, "facts", None) or m.check
-    src = inspect.getsource(fn)
+    # 子项目四起账本外数字只作事实（`facts()`），不再驳回。
+    src = inspect.getsource(m.facts)
     assert "audit_prose" in src, f"{mod} 没有核对描述字段里的账本外数字"
     assert "不要写具体数字" in m.PROMPT, f"{mod} 的提示词没有讲清这条"
 
diff --git a/tests/test_value_chain.py b/tests/test_value_chain.py
index 3b4f96d..b9f3c0c 100644
--- a/tests/test_value_chain.py
+++ b/tests/test_value_chain.py
@@ -11,6 +11,7 @@ from ir_agent import value_chain as VC
 from ir_agent.ledger import Fact, FactLedger, Method
 from ir_agent.sources.external import Corpus, Document, Tier
 from ir_agent.value_chain import ChainFailed, Power, Role
+from judge_stub import Judge
 
 D = Decimal
 AS_OF = date(2026, 9, 7)
@@ -67,41 +68,39 @@ def test_解析三部分():
     assert v.size.value == D("7803") and v.size.period == "2025FY"
 
 
-def test_未知环节位置被驳回():
-    with pytest.raises(ChainFailed, match="未知的环节位置"):
-        VC.parse(f"环节: 中游 | x | y | 「{Q_UP}」")
+def test_未知环节位置_记进读数事实():
+    v = VC.parse(f"环节: 中游 | x | y | 「{Q_UP}」")
+    assert "上游/本环节/下游" in "\n".join(v.read_notes)
 
 
-def test_本环节没有议价对象():
-    with pytest.raises(ChainFailed, match="无议价对象"):
-        VC.parse("议价: 本环节 | 强")
+def test_本环节给了议价_记进读数事实():
+    v = VC.parse("议价: 本环节 | 强")
+    assert "议价只对上游与下游" in "\n".join(v.read_notes)
 
 
-def test_行业规模无数值被驳回():
-    with pytest.raises(ChainFailed, match="缺少数值"):
-        VC.parse(f"行业规模: 口径 | 若干 | 亿元 | 2025FY | 「{Q_SIZE}」")
+def test_行业规模无数值_记进读数事实():
+    v = VC.parse(f"行业规模: 口径 | 若干 | 亿元 | 2025FY | 「{Q_SIZE}」")
+    assert "读不出数值" in "\n".join(v.read_notes)
 
 
-def test_环节缺依据被驳回():
-    with pytest.raises(ChainFailed, match="缺少可核验的依据"):
-        VC.parse("环节: 上游 | 种植 | 分散 | 我的判断")
+def test_环节缺依据_记进读数事实():
+    v = VC.parse("环节: 上游 | 种植 | 分散 | 我的判断")
+    assert "没有给出依据" in "\n".join(v.read_notes)
 
 
 # ── 链条必须连起来 ───────────────────────────────────────────
 
 @pytest.mark.parametrize("drop", list(Role))
-def test_缺任一环即驳回(drop):
+def test_缺任一环_记进事实(drop):
     """链条断了就谈不上「完整产业链」，也无从判断议价能力从何而来。"""
     keep = [f"环节: {r.value} | n | note | 「{Q_UP}」" for r in Role if r is not drop]
     v = VC.parse(_out(links=keep, size=False))
-    with pytest.raises(ChainFailed, match=drop.value):
-        VC.check(v, _led(), DOC, AS_OF, corpus=_corpus())
+    assert any(drop.value in x for x in VC.facts(v, _led(), DOC, AS_OF, corpus=_corpus()))
 
 
 def test_议价须对上下游都给():
     v = VC.parse(_out(powers=["议价: 上游 | 强"], size=False))
-    with pytest.raises(ChainFailed, match="上游与对下游"):
-        VC.check(v, _led(), DOC, AS_OF, corpus=_corpus())
+    assert any("对下游的议价" in x for x in VC.facts(v, _led(), DOC, AS_OF, corpus=_corpus()))
 
 
 # ── 依据核验与出处判定 ───────────────────────────────────────
@@ -110,7 +109,7 @@ def test_引文出自外部资料时自动标为外部():
     from ir_agent.basis import BasisKind
 
     v = VC.parse(_out())
-    VC.check(v, _led(), "", AS_OF, corpus=_corpus())
+    VC.facts(v, _led(), "", AS_OF, corpus=_corpus())
     b = v.links[0].bases[0]
     assert b.kind is BasisKind.EXTERNAL and b.ref.endswith("R1.html")
 
@@ -121,8 +120,7 @@ def test_编造的环节被驳回():
            f"环节: 本环节 | 酿造 | x | 「{Q_UP}」",
            f"环节: 下游 | 经销 | y | 「{Q_DOWN}」"]
     v = VC.parse(_out(links=bad, size=False))
-    with pytest.raises(ChainFailed, match="不可核验|找不到"):
-        VC.check(v, _led(), "", AS_OF, corpus=_corpus())
+    assert any("核验不过" in x for x in VC.facts(v, _led(), "", AS_OF, corpus=_corpus()))
 
 
 def test_发布日晚于as_of的研报进不了语料库():
@@ -184,7 +182,7 @@ def test_没有外部规模时明说上市公司口径高估市占率():
 
 def test_提示词禁止凭常识补全():
     c = _Client(_out())
-    VC.analyse(c, _corpus(), _led(), P, AS_OF)
+    VC.analyse(c, _corpus(), _led(), P, AS_OF, judge=Judge())
     assert "不要凭常识补全" in c.prompts[0] and "比缺一环更糟" in c.prompts[0]
 
 
@@ -238,20 +236,28 @@ def test_多档来源同时出现时可引用范围按Tier声明顺序排列():
     assert txt.index(sellside_rule) < txt.index(media_rule)
 
 
-def test_驳回后重试并带回原因():
-    bad = _out(powers=["议价: 上游 | 强"], size=False)
-    c = _Client(bad, _out())
-    VC.analyse(c, _corpus(), _led(), P, AS_OF)
-    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]
+def test_审核致命则带建议重写():
+    c = _Client(_out(), _out())
+    VC.analyse(c, _corpus(), _led(), P, AS_OF, judge=Judge({("议价", "对上游"): "致命"}, {}))
+    assert len(c.prompts) == 2 and "上一轮审核驳回" in c.prompts[1]
 
 
-def test_连续失败则拒绝出结论():
-    bad = _out(links=[f"环节: 上游 | x | y | 「查无此句」"], size=False)
-    with pytest.raises(ChainFailed, match="连续"):
-        VC.analyse(_Client(bad, bad, bad), _corpus(), _led(), P, AS_OF)
+def test_三轮审核不过则停下():
+    from ir_agent.qual_review import ReviewStop
+    with pytest.raises(ReviewStop, match="产业链审核 3 轮未通过"):
+        VC.analyse(_Client(_out(), _out(), _out()), _corpus(), _led(), P, AS_OF,
+                   judge=Judge({"环节": "致命"}))
+
+
+def test_审核清单按方法论生成():
+    """用户决定 5：环节固定 3 条、议价固定 2 条、行业规模给了才列。"""
+    assert VC.checklist(VC.parse(_out(size=False))) == [
+        ("环节", "上游"), ("环节", "本环节"), ("环节", "下游"),
+        ("议价", "对上游"), ("议价", "对下游")]
+    assert ("规模", "行业规模") in VC.checklist(VC.parse(_out()))
 
 
 def test_端到端带出矛盾():
     v, _ = VC.analyse(_Client(_out()), _corpus(),
-                      _led(payable_days__percentile="0.17"), P, AS_OF)
+                      _led(payable_days__percentile="0.17"), P, AS_OF, judge=Judge())
     assert v.conflicts and "⚠" in v.summary()
```

- [ ] **Step 2: 跑测试**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`17 failed, 2431 passed, 8 skipped`。失败的是：

- `tests/test_prompt_contract.py::test_三个定性模块都装了这道闸[value_chain-ChainFailed-chain]`
- `tests/test_value_chain.py::test_未知环节位置_记进读数事实`
- `tests/test_value_chain.py::test_本环节给了议价_记进读数事实`
- `tests/test_value_chain.py::test_行业规模无数值_记进读数事实`
- `tests/test_value_chain.py::test_环节缺依据_记进读数事实`
- `tests/test_value_chain.py::test_缺任一环_记进事实[\u4e0a\u6e38]`
- `tests/test_value_chain.py::test_缺任一环_记进事实[\u672c\u73af\u8282]`
- `tests/test_value_chain.py::test_缺任一环_记进事实[\u4e0b\u6e38]`
- `tests/test_value_chain.py::test_议价须对上下游都给`
- `tests/test_value_chain.py::test_引文出自外部资料时自动标为外部`
- `tests/test_value_chain.py::test_编造的环节被驳回`
- `tests/test_value_chain.py::test_提示词禁止凭常识补全`
- `tests/test_value_chain.py::test_审核致命则带建议重写`
- `tests/test_value_chain.py::test_三轮审核不过则停下`
- `tests/test_value_chain.py::test_审核清单按方法论生成`
- `tests/test_value_chain.py::test_端到端带出矛盾`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/full.py b/ir_agent/full.py
index 5357a06..1c57620 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -600,7 +600,7 @@ def _load_value_chain(r, as_of: date, client) -> tuple[str, str, str]:
         return "", "", "产业链分析（无任何资料）"
     try:
         view, _ = VC.analyse(client, r.corpus, r.ledger, r.period, as_of,
-                             sections)
+                             sections, judge=client)
     except Exception as e:                                  # noqa: BLE001
         return "", "", f"产业链分析（{e.__class__.__name__}: {str(e)[:120]}）"
     if view.size:
diff --git a/ir_agent/value_chain.py b/ir_agent/value_chain.py
index ece7b18..4fb15bf 100644
--- a/ir_agent/value_chain.py
+++ b/ir_agent/value_chain.py
@@ -28,17 +28,19 @@ from __future__ import annotations
 import re
 from dataclasses import dataclass, field
 from datetime import date
-from decimal import Decimal, InvalidOperation
+from decimal import Decimal
 from enum import Enum
 
 from ir_agent.lineparse import keyed, pick
 from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method
 from ir_agent.sources.external import attribute
-from ir_agent.basis import Basis, InvalidBasis, validate
+from ir_agent.basis import Basis, validate
+from ir_agent import qual_review as Q
 
 
 class ChainFailed(Exception):
-    """产业链分析不可核验 —— 拒绝出结论。"""
+    """产业链分析无从进行（没有任何资料等）。**内容是否成立不再由代码驳回**
+    （子项目四，spec 2026-10-17）：交给审核者，审核不过抛 `qual_review.ReviewStop`。"""
 
 
 class Role(str, Enum):
@@ -75,15 +77,6 @@ _NUM = re.compile(r"-?\d+(?:\.\d+)?")
 
 
 
-def _tail(raw: str, n: int = 400) -> str:
-    """失败信息里带上模型最后写了什么。
-
-    **只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。** 前几轮
-    排查全跑失败时，每次都要单独再跑一次去抓原始输出 —— 而那几次的根因
-    （列表前缀、括号补注、写「无据」）看一眼原文就清楚了。
-    """
-    raw = (raw or "").strip()
-    return raw[-n:] if len(raw) > n else raw
 
 @dataclass
 class Link:
@@ -91,6 +84,7 @@ class Link:
     name: str
     note: str
     bases: list[Basis] = field(default_factory=list)
+    cites: list = field(default_factory=list)       # 转述指向的文章标题
 
 
 @dataclass
@@ -103,6 +97,7 @@ class SizeClaim:
     unit: str
     period: str
     bases: list[Basis] = field(default_factory=list)
+    cites: list = field(default_factory=list)
 
     def to_fact(self, source_id: str, as_of: date) -> Fact:
         """落账本。由 LLM 从文本抽取，因此 method=EXTRACTED、confidence<1，
@@ -119,6 +114,10 @@ class ChainView:
     power: dict[Role, Power] = field(default_factory=dict)
     size: SizeClaim | None = None
     conflicts: list[str] = field(default_factory=list)
+    # 子项目四：代码读数时的事实、审核结论、各轮记录。
+    read_notes: list[str] = field(default_factory=list)
+    review: object = None
+    rounds: list = field(default_factory=list)
 
     def of(self, role: Role) -> list[Link]:
         return [x for x in self.links if x.role is role]
@@ -143,71 +142,63 @@ class ChainView:
                          "非上市企业未计入，据此算出的市占率系高估")
         for c in self.conflicts:
             lines.append(f"  ⚠ {c}")
+        lines += Q.review_tail(self.review)
         return "\n".join(lines)
 
 
-def _basis(raw: str, what: str) -> list[Basis]:
-    raw = raw.strip()
-    ref = _REF.search(raw)
-    if ref:
-        try:
-            return [Basis.fact(ref.group(0))]
-        except InvalidBasis as e:
-            raise ChainFailed(f"{what} 的占位符不合法: {e}") from e
-    q = _QUOTE.search(raw)
-    if not q:
-        raise ChainFailed(f"{what} 缺少可核验的依据（需原文引用或占位符）")
-    try:
-        return [Basis.report(q.group(1))]
-    except InvalidBasis as e:
-        raise ChainFailed(f"{what} 的依据不合法: {e}") from e
-
-
-def _split(line: str, n: int, what: str) -> list[str]:
-    parts = [p.strip() for p in line.split("|")]
-    if len(parts) < n:
-        raise ChainFailed(f"{what} 字段不足（需 {n} 段，得到 {len(parts)}）：{line}")
-    return parts
-
-
-def parse(text: str) -> ChainView:
-    view = ChainView()
 
+
+
+
+def _cells(line: str) -> list[str]:
+    return [p.strip() for p in line.split("|")]
+
+
+def parse(text: str, corpus=None) -> ChainView:
+    """读数。**只读，不驳回**（子项目四）：段数不够、环节位置/议价强弱不是规定的词、
+    对本环节给议价、行业规模读不出数的行跳过；依据问题照读 —— 都记进 `read_notes`，
+    作为事实交给审核者。"""
+    view = ChainView()
+    note = view.read_notes.append
     for m in _LINK.finditer(text):
-        p = _split(m.group(1), 4, "环节")
+        p = _cells(m.group(1))
+        if len(p) < 4:
+            note(f"环节一行字段不足（需 4 段），没读进来：{m.group(1)}")
+            continue
         got = pick(p[0], [x.value for x in Role])
         if got is None:
-            raise ChainFailed(
-                f"未知的环节位置 {p[0]!r}（须为 "
-                + "/".join(x.value for x in Role) + "）")
-        view.links.append(Link(Role(got), p[1], p[2], _basis(p[3], "环节")))
-
+            note(f"环节位置「{p[0]}」不是 " + "/".join(x.value for x in Role) + " 之一，没读进来")
+            continue
+        bases, prob, cites = Q.basis(" | ".join(p[3:]), corpus)
+        if prob:
+            note(f"环节「{p[1]}」的依据：{prob}")
+        view.links.append(Link(Role(got), p[1], p[2], bases, cites))
     for m in _POWER.finditer(text):
-        p = _split(m.group(1), 2, "议价")
-        r_, p_ = (pick(p[0], [x.value for x in Role]),
-                  pick(p[1], [x.value for x in Power]))
+        p = _cells(m.group(1))
+        r_ = pick(p[0], [x.value for x in Role]) if p else None
+        p_ = pick(p[1], [x.value for x in Power]) if len(p) > 1 else None
         if r_ is None or p_ is None:
-            raise ChainFailed(
-                f"议价一行取值不合法：{m.group(1)} —— 位置须为 "
-                + "/".join(x.value for x in Role) + "，强弱须为 "
-                + "/".join(x.value for x in Power))
-        role, power = Role(r_), Power(p_)
-        if role is Role.FOCAL:
-            raise ChainFailed("议价只对上游与下游而言，本环节无议价对象")
-        view.power[role] = power
-
+            note(f"议价一行读不出：{m.group(1)}（位置须为 " + "/".join(x.value for x in Role)
+                 + "，强弱须为 " + "/".join(x.value for x in Power) + "）")
+            continue
+        if Role(r_) is Role.FOCAL:
+            note("议价给了「本环节」—— 议价只对上游与下游而言，这一行没读进来")
+            continue
+        view.power[Role(r_)] = Power(p_)
     m = _SIZE.search(text)
     if m:
-        p = _split(m.group(1), 5, "行业规模")
-        num = _NUM.search(p[1].replace(",", ""))
-        if not num:
-            raise ChainFailed(f"行业规模缺少数值：{p[1]!r}")
-        try:
-            val = Decimal(num.group(0))
-        except InvalidOperation as e:
-            raise ChainFailed(f"行业规模数值不可解析：{p[1]!r}") from e
-        view.size = SizeClaim(label=p[0], value=val, unit=p[2], period=p[3],
-                              bases=_basis(p[4], "行业规模"))
+        p = _cells(m.group(1))
+        num = _NUM.search(p[1].replace(",", "")) if len(p) > 1 else None
+        if len(p) < 5:
+            note(f"行业规模一行字段不足（需 5 段），没读进来：{m.group(1)}")
+        elif not num:
+            note(f"行业规模「{p[1]}」读不出数值，没读进来")
+        else:
+            bases, prob, cites = Q.basis(" | ".join(p[4:]), corpus)
+            if prob:
+                note(f"行业规模的依据：{prob}")
+            view.size = SizeClaim(label=p[0], value=Decimal(num.group(0)), unit=p[2],
+                                  period=p[3], bases=bases, cites=cites)
     return view
 
 
@@ -238,16 +229,16 @@ def cross_check(view: ChainView, ledger: FactLedger, period: str,
     return out
 
 
-def check(view: ChainView, ledger: FactLedger, evidence: str, as_of: date,
-          corpus=None) -> None:
+def facts(view: ChainView, ledger: FactLedger, evidence: str, as_of: date,
+          corpus=None, period: str = "") -> list[str]:
+    """代码核对出的事实，交审核者（子项目四：此前这些是驳回理由）。"""
+    out: list[str] = []
     for role in Role:
         if not view.of(role):
-            raise ChainFailed(
-                f"产业链缺少「{role.value}」环节 —— 链条断了就谈不上"
-                f"「完整产业链」，也无从判断公司的议价能力从何而来")
-    if Role.UPSTREAM not in view.power or Role.DOWNSTREAM not in view.power:
-        raise ChainFailed("须同时给出对上游与对下游的议价判断")
-
+            out.append(f"产业链缺少「{role.value}」环节")
+    for role in (Role.UPSTREAM, Role.DOWNSTREAM):
+        if role not in view.power:
+            out.append(f"没有给出对{role.value}的议价判断")
     from ir_agent.citation import audit_prose
 
     prose = {f"环节「{x.name[:12]}」": x.name + x.note for x in view.links}
@@ -255,41 +246,58 @@ def check(view: ChainView, ledger: FactLedger, evidence: str, as_of: date,
         prose["行业规模口径"] = view.size.label
     bad = audit_prose(prose)
     if bad:
-        raise ChainFailed(
-            "描述里出现了账本外的数字：" + "、".join(bad) +
-            " —— 这些描述会原样进入研报撰写环节，而那里的数字必须可溯源；"
-            "请改成不带具体数字的说法")
-
+        out.append("描述里有账本外的数字：" + "、".join(bad))
     items = list(view.links) + ([view.size] if view.size else [])
     attribute(items, evidence, corpus)
-    flat = [b for x in view.links for b in x.bases]
-    if view.size:
-        flat += view.size.bases
-    problems = validate(flat, ledger=ledger, body=evidence, as_of=as_of,
-                        corpus=corpus)
-    if problems:
-        raise ChainFailed("依据不可核验：" + "；".join(problems))
+    flat = [b for x in items for b in x.bases]
+    out += [f"依据核验不过：{p}" for p in validate(flat, ledger=ledger, body=evidence,
+                                                  as_of=as_of, corpus=corpus)]
+    if period:
+        out += [f"与账本交叉核对：{x}" for x in cross_check(view, ledger, period, as_of)]
+    return out
+
+
+KINDS = ("环节", "议价", "规模")
+
+
+def checklist(view: ChainView) -> list[tuple[str, str]]:
+    """按方法论生成（用户决定 5）：环节固定 3 条、议价固定 2 条、行业规模给了才列。"""
+    items = [("环节", r.value) for r in Role]
+    items += [("议价", "对上游"), ("议价", "对下游")]
+    return items + ([("规模", "行业规模")] if view.size else [])
+
+
+RULES = Q.make_rules("产业链", """\
+- 环节：上游/本环节/下游的识别是否正确；对本公司意味着什么是否说清；有没有凭常识补出来
+  的环节（看起来完整、其中一环是编的，比缺一环更糟）。没给出的环节判「未给出」。
+- 议价：强/均衡/弱与证据是否一致；与账本周转天数是否一致 —— 对上游议价强应体现为
+  应付天数高于同业中位（占用供应商资金），对下游议价强应体现为应收天数低于同业中位
+  （先款后货）；代码给了交叉核对结果。没给出的判「未给出」。
+- 规模：口径、数值、单位、期间是否对得上原文；口径不明的规模不能用。""")
 
 
 PROMPT = """基于下面的资料，梳理本公司所处的产业链。
 
 **一、完整产业链**（上游、本环节、下游都要有，各一到三条）
-格式：`环节: <上游|本环节|下游> | <环节名> | <该环节对本公司意味着什么> | 「原文逐字引用」`
+格式：`环节: <上游|本环节|下游> | <环节名> | <该环节对本公司意味着什么> | <依据>`
 
 **二、议价能力**（对上游、对下游各一条）
 格式：`议价: <上游|下游> | <强|均衡|弱>`
 
 **三、行业真实规模**（资料里有明确口径的才写，没有就整行不要）
 口径必须写清楚——「规模以上企业营业收入」和「终端零售额」差得很远。
-格式：`行业规模: <口径描述> | <数值> | <单位> | <期间如2025FY> | 「原文逐字引用」`
+格式：`行业规模: <口径描述> | <数值> | <单位> | <期间如2025FY> | <依据>`
 
-**硬性要求**
-- 引用必须**逐字照抄**下面的资料，一个字都不能改，否则核验通不过。
+{basis_rules}
+
+**要求**
 - 资料里没讲到的环节，**不要凭常识补全**。产业链看起来完整但其中一环
   是编的，比缺一环更糟。
-- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本 —— 一个无处可查的数字会让整篇报告卡在无法修复的驳回上。需要数字时用给你的占位符，否则改成不带数字的说法。
+- 描述性文字里**不要写具体数字**（如「处于 91% 历史分位」）。这些描述会原样进入研报撰写环节，而那里的数字一律要求可溯源到账本。需要数字时用给你的占位符，否则改成不带数字的说法。
 - 议价判断会与账本里的应付/应收天数同业分位交叉核对，方向相反时你需要
-  在正文里说明缘由。
+  说明缘由。
+
+{review_note}
 
 {evidence}
 """
@@ -348,25 +356,21 @@ def for_prompt(corpus, ledger: FactLedger, period: str, as_of: date,
 
 
 def analyse(client, corpus, ledger: FactLedger, period: str, as_of: date,
-            sections=None, max_attempts: int = 3) -> tuple[ChainView, str]:
+            sections=None, *, judge, catalog: str = "",
+            max_rounds: int = Q.MAX_ROUNDS) -> tuple[ChainView, str]:
+    """撰写者写、审核者审（子项目四）。返回 (结论, 证据文本)；审核不过抛
+    `qual_review.ReviewStop`，模型接口错误原样抛出。"""
     evidence = for_prompt(corpus, ledger, period, as_of, sections)
     body = "\n".join(
         [s.text for s in (sections.sections.values() if sections else [])])
-    prompt = PROMPT.format(evidence=evidence)
-    last, raw = "", ""
-    for attempt in range(1, max_attempts + 1):
-        out = raw = client(prompt)
-        try:
-            view = parse(out)
-            check(view, ledger, body, as_of, corpus)
-        except ChainFailed as e:
-            last = str(e)
-            prompt = (f"{prompt}\n\n---\n"
-                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
-                      "请修正后按格式重新输出。\n")
-            continue
-        view.conflicts = cross_check(view, ledger, period, as_of)
-        return view, evidence
-    raise ChainFailed(
-            f"连续 {max_attempts} 次给不出可核验的产业链分析：{last}"
-            f"\n最后一次的原始输出（末 400 字）：\n{_tail(raw)}")
+    prompt = PROMPT.format(evidence=evidence, basis_rules=Q.BASIS_RULES,
+                           review_note=Q.REVIEW_NOTE)
+    view, res, rounds = Q.run(
+        client, judge, name="产业链", prompt=prompt, materials=evidence,
+        read=lambda raw: parse(raw, corpus),
+        facts=lambda v: facts(v, ledger, body, as_of, corpus, period),
+        checklist=checklist, kinds=KINDS, rules=RULES, catalog=catalog,
+        hay=evidence, ledger=ledger, as_of=as_of, max_rounds=max_rounds)
+    view.conflicts = cross_check(view, ledger, period, as_of)
+    view.review, view.rounds = res, rounds
+    return view, evidence
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2447 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `checklist` 里去掉 [规模] 条目 → 清单测试红
- `facts` 里删掉缺环节的事实 → 「缺任一环记进事实」测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(value_chain): 产业链改为撰写者 + 审核者 —— 只读不驳回，按方法论出清单

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 第一次调用的编排：落盘、沿用、人工接手、停下、门①

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `ir_agent/research.py`
- Modify: `tests/test_full_context.py`
- Create: `tests/test_qual_stage.py`
- Modify: `tests/test_research.py`

**Interfaces:**
- Consumes: Task 3–5 的 `assess/analyse(judge=, catalog=)`、`parse`、`facts`
- Produces: `full.QualStop`；`full._qual_module(out, *, prefix, label, corpus, parse, facts, run) -> (view, status)`；`full._qual_section(statuses) -> str`；`full._analyse_qualitative(..., *, out=None, stops=None, statuses=None)`；`_save_state(..., qual_unreviewed=None)`

**为什么：** `_qual_module` 管一个模块的落盘与沿用：`<模块>.md` 在就读回不调模型（指纹与审核记录一致 → 沿用；语料库指纹变了 → 重核依据、标出处数；不一致或无记录 → 「人工修改后未经审核」）；否则撰写 + 审核，通过写 `<模块>.md`/`_review.json`/`_rounds.md`（并删掉上一轮的 `_failed_raw.md`），没通过写 `_rounds.md`/`_failed_raw.md` 并抛 `QualStop`，模型接口失败也抛 `QualStop`（写明重跑即可）。`_analyse_qualitative` 用列表参数 `stops`/`statuses` 带出停下与审核状态（端到端外壳仍可只返回 client）。`run_analyse`：有停下 → 未完成、不写来源清单；否则来源清单附「定性分析审核」一节；未经审核的模块记进 `state.json` 的 `qual_unreviewed`。终端措辞登记 `_PREFIX`，`next_actions` 给「以底稿改好存成 `<模块>.md` 再重跑」。

- [ ] **Step 1: 写测试**

```diff
diff --git a/tests/test_full_context.py b/tests/test_full_context.py
index a9daf16..1b4b0b0 100644
--- a/tests/test_full_context.py
+++ b/tests/test_full_context.py
@@ -4380,3 +4380,51 @@ def test_判断项审核三轮不过_停在第三次调用_给两条做法(tmp_p
     acts = "\n".join(next_actions(o.stage, "600183", out_dir=str(tmp_path),
                                   year=2025, phase="assume"))
     assert "--assume" in acts and full.ASSUMPTIONS_FAILED_JSON in acts
+
+
+# ══ 子项目四：第一次调用的三个定性模块 = 撰写者 + 审核者 ══════════════════
+
+def test_定性模块审核停下_第一次调用停在这里_不出来源清单(tmp_path, monkeypatch):
+    """用户决定 9：任一模块审核没通过 → 整个第一次调用停下交人工；不写 sources.md、
+    不进门①；终端写明哪个模块、各轮记录在哪；「下一步」给两条做法。"""
+    from ir_agent import full
+    from ir_agent.research import next_actions
+
+    _offline(monkeypatch, full)
+
+    def qual(code, r, as_of, model, md, pr, skipped, *, out=None, stops=None,
+             statuses=None):
+        (out / "strategy_failed_raw.md").write_text("最后一稿", encoding="utf-8")
+        stops.append("战略分析审核 3 轮未通过（最后一轮致命 2 条） —— 各轮记录：x")
+        return _PhaseClient()
+    monkeypatch.setattr(full, "_analyse_qualitative", qual)
+    o = full.run_analyse("600176", _PhaseR(), date(2026, 10, 17), tmp_path)
+    assert o.stage is full.Stage.INCOMPLETE
+    assert not (tmp_path / full.SOURCES_MD).exists()
+    assert any(s.startswith("战略分析审核 3 轮未通过") for s in o.skipped)
+    acts = "\n".join(next_actions(o.stage, "600176", out_dir=str(tmp_path),
+                                  year=2025, phase="analyse"))
+    assert "--analyse" in acts and "strategy_failed_raw.md" in acts and "strategy.md" in acts
+
+
+def test_定性模块人工修改过_门一清单与终端标注_记进state(tmp_path, monkeypatch):
+    from ir_agent import full
+
+    _offline(monkeypatch, full)
+
+    def qual(code, r, as_of, model, md, pr, skipped, *, out=None, stops=None,
+             statuses=None):
+        statuses += [{"label": "战略分析", "state": "人工修改后未经审核", "rounds": 0,
+                      "flagged": [], "note": ""},
+                     {"label": "产业链", "state": "沿用审核结论", "rounds": 1,
+                      "flagged": [], "note": "本轮语料库与审核时不同，2 处依据本轮核对不过"}]
+        return _PhaseClient()
+    monkeypatch.setattr(full, "_analyse_qualitative", qual)
+    o = full.run_analyse("600176", _PhaseR(), date(2026, 10, 17), tmp_path)
+    assert o.stage is full.Stage.AWAITING_SOURCES
+    src = (tmp_path / full.SOURCES_MD).read_text(encoding="utf-8")
+    assert "## 定性分析审核" in src and "战略分析：⚠ 人工修改后未经审核" in src
+    assert any(s.startswith("定性分析未经审核：战略分析") for s in o.skipped)
+    assert any(s.startswith("定性分析沿用：产业链") for s in o.skipped)
+    st = json.loads((tmp_path / full.STATE).read_text(encoding="utf-8"))
+    assert st["qual_unreviewed"] == ["战略分析"]
diff --git a/tests/test_qual_stage.py b/tests/test_qual_stage.py
new file mode 100644
index 0000000..e61a4ea
--- /dev/null
+++ b/tests/test_qual_stage.py
@@ -0,0 +1,144 @@
+"""第一次调用里三个定性模块的落盘、停下、沿用与人工接手（spec 2026-10-17 五）。不调模型。"""
+from __future__ import annotations
+
+import json
+from dataclasses import dataclass, field
+
+import pytest
+
+from ir_agent import full
+from ir_agent import qual_review as Q
+from ir_agent.methodology_review import ReviewResult, Verdict
+
+
+@dataclass
+class _View:
+    raw: str = ""
+    read_notes: list = field(default_factory=list)
+    review: object = None
+    rounds: list = field(default_factory=list)
+
+
+def _passed(raw="项: a"):
+    v = _View(raw)
+    v.review = ReviewResult(verdicts=[Verdict("五力", "现有竞争强度", "提醒", "牵强")],
+                            rounds=2, fingerprint=Q.fingerprint(raw))
+    v.rounds = [Q.Round(1, "第一稿"), Q.Round(2, raw)]
+    return v
+
+
+class _Doc:
+    def __init__(self, doc_id):
+        self.doc_id, self.title = doc_id, doc_id
+
+
+class _Corpus:
+    def __init__(self, *ids):
+        self.docs = [_Doc(i) for i in ids]
+
+
+def _call(tmp_path, run, *, corpus=None, facts=lambda v: []):
+    calls = []
+
+    def _run():
+        calls.append(1)
+        return run()
+    view, status = full._qual_module(
+        tmp_path, prefix="strategy", label="战略分析", corpus=corpus or _Corpus("a"),
+        parse=lambda raw: _View(raw), facts=facts, run=_run)
+    return view, status, calls
+
+
+class TestPass:
+    def test_通过_落结果_审核结论_各轮记录(self, tmp_path):
+        view, st, calls = _call(tmp_path, _passed)
+        assert calls == [1] and st["state"] == "审核通过" and st["rounds"] == 2
+        assert (tmp_path / "strategy.md").read_text(encoding="utf-8") == "项: a"
+        rev = json.loads((tmp_path / "strategy_review.json").read_text(encoding="utf-8"))
+        assert rev["passed"] is True and rev["fingerprint"] == Q.fingerprint("项: a")
+        assert rev["corpus"] == Q.corpus_fingerprint(_Corpus("a"))
+        assert "第一稿" in (tmp_path / "strategy_rounds.md").read_text(encoding="utf-8")
+        assert any("牵强" in x for x in st["flagged"])
+
+
+class TestReuse:
+    """用户决定 11、12：通过的沿用不调模型；人改过的标「人工修改后未经审核」。"""
+
+    def test_指纹一致_沿用审核结论_不调模型(self, tmp_path):
+        _call(tmp_path, _passed)
+        view, st, calls = _call(tmp_path, _passed)
+        assert calls == [] and st["state"] == "沿用审核结论"
+        assert view.review is not None and view.review.flagged()
+
+    def test_人改过_不送审_标注(self, tmp_path):
+        _call(tmp_path, _passed)
+        (tmp_path / "strategy.md").write_text("项: 人改过", encoding="utf-8")
+        view, st, calls = _call(tmp_path, _passed)
+        assert calls == [] and st["state"] == "人工修改后未经审核" and view.raw == "项: 人改过"
+
+    def test_没有审核记录_也标注(self, tmp_path):
+        (tmp_path / "strategy.md").write_text("项: 人写的", encoding="utf-8")
+        _, st, calls = _call(tmp_path, _passed)
+        assert calls == [] and st["state"] == "人工修改后未经审核"
+
+    def test_语料库变了_重核依据并标出(self, tmp_path):
+        _call(tmp_path, _passed, corpus=_Corpus("a"))
+        _, st, _ = _call(tmp_path, _passed, corpus=_Corpus("b"),
+                         facts=lambda v: ["依据核验不过：x", "依据核验不过：y", "别的事实"])
+        assert "本轮语料库与审核时不同" in st["note"] and "2 处" in st["note"]
+
+
+class TestStop:
+    def test_三轮不过_写各轮记录与最后一稿_不写结果(self, tmp_path):
+        def run():
+            raise Q.ReviewStop("战略分析审核 3 轮未通过（最后一轮致命 1 条）",
+                               [Q.Round(1, "稿一"), Q.Round(2, "稿二")], last="稿二")
+        with pytest.raises(full.QualStop, match="战略分析审核 3 轮未通过") as e:
+            _call(tmp_path, run)
+        assert "strategy_rounds.md" in str(e.value) and "strategy_failed_raw.md" in str(e.value)
+        assert (tmp_path / "strategy_failed_raw.md").read_text(encoding="utf-8") == "稿二"
+        assert not (tmp_path / "strategy.md").exists()
+
+    def test_模型接口失败_也停下_写明重跑即可(self, tmp_path):
+        from ir_agent.clients.claude_cli import ClaudeCliError
+
+        def run():
+            raise ClaudeCliError("You've hit your session limit")
+        with pytest.raises(full.QualStop, match="战略分析审核停下：模型接口问题.*重跑"):
+            _call(tmp_path, run)
+
+
+def test_一个模块停下_其余照常跑完(tmp_path, monkeypatch):
+    """用户决定 9：三个模块照常都跑完再停。"""
+    def stop(*a, **k):
+        raise full.QualStop("战略分析审核 3 轮未通过（最后一轮致命 1 条）")
+    monkeypatch.setattr(full, "_load_strategy", stop)
+    monkeypatch.setattr(full, "_load_macro_view", lambda *a, **k: ("宏观", "宏观", ""))
+    monkeypatch.setattr(full, "_load_value_chain", lambda *a, **k: ("产业链", "产业链", ""))
+    monkeypatch.setattr(full, "make_client", lambda **k: object())
+
+    class _R:
+        quotes = None
+    md, pr, skipped, stops = {}, {"宏观": ""}, [], []
+    full._analyse_qualitative("600176", _R(), None, None, md, pr, skipped,
+                              out=tmp_path, stops=stops, statuses=[])
+    assert stops == ["战略分析审核 3 轮未通过（最后一轮致命 1 条）"]
+    assert md["宏观定性"] == "宏观" and md["产业链"] == "产业链" and md["战略"] == ""
+
+
+def test_门一清单的定性分析审核一节():
+    txt = full._qual_section([
+        {"label": "宏观定性", "state": "审核通过", "rounds": 1, "flagged": [], "note": ""},
+        {"label": "战略分析", "state": "人工修改后未经审核", "rounds": 0, "flagged": [], "note": ""},
+        {"label": "产业链", "state": "沿用审核结论", "rounds": 2,
+         "flagged": ["[议价] 对上游：提醒　x"], "note": "本轮语料库与审核时不同，1 处依据本轮核对不过"}])
+    assert txt.startswith("\n## 定性分析审核")
+    assert "宏观定性：第 1 轮审核通过" in txt and "战略分析：⚠ 人工修改后未经审核" in txt
+    assert "[议价] 对上游：提醒" in txt and "1 处依据本轮核对不过" in txt
+    assert full._qual_section([]) == ""
+
+
+def test_重跑通过后删掉上一轮停下留的最后一稿(tmp_path):
+    (tmp_path / "strategy_failed_raw.md").write_text("上一轮的最后一稿", encoding="utf-8")
+    _call(tmp_path, _passed)
+    assert not (tmp_path / "strategy_failed_raw.md").exists()
diff --git a/tests/test_research.py b/tests/test_research.py
index 4abceda..1b6dd30 100644
--- a/tests/test_research.py
+++ b/tests/test_research.py
@@ -370,3 +370,10 @@ def test_门三指引不再说改完会重新校验():
                                  out_dir="out/x", year=2025))
     assert "重新校验" not in txt and "三档须严格拉开" not in txt
     assert "未经审核" in txt
+
+
+def test_定性分析审核的新措辞都登记了():
+    from ir_agent.research import _mark
+    for s in ("宏观定性审核 3 轮未通过（…）", "战略分析审核未完成：…", "产业链审核停下：模型接口问题",
+              "定性分析未经审核：战略分析（人工修改后未经审核）", "定性分析沿用：产业链 本轮语料库…"):
+        assert _mark(s).strip() == "⚠", s
```

- [ ] **Step 2: 跑测试**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`14 failed, 2447 passed, 8 skipped`。失败的是：

- `tests/test_full_context.py::test_定性模块审核停下_第一次调用停在这里_不出来源清单`
- `tests/test_full_context.py::test_定性模块人工修改过_门一清单与终端标注_记进state`
- `tests/test_qual_stage.py::TestPass::test_通过_落结果_审核结论_各轮记录`
- `tests/test_qual_stage.py::TestReuse::test_指纹一致_沿用审核结论_不调模型`
- `tests/test_qual_stage.py::TestReuse::test_人改过_不送审_标注`
- `tests/test_qual_stage.py::TestReuse::test_没有审核记录_也标注`
- `tests/test_qual_stage.py::TestReuse::test_语料库变了_重核依据并标出`
- `tests/test_qual_stage.py::TestStop::test_三轮不过_写各轮记录与最后一稿_不写结果`
- `tests/test_qual_stage.py::TestStop::test_模型接口失败_也停下_写明重跑即可`
- `tests/test_qual_stage.py::test_一个模块停下_其余照常跑完`
- `tests/test_qual_stage.py::test_门一清单的定性分析审核一节`
- `tests/test_qual_stage.py::test_重跑通过后删掉上一轮停下留的最后一稿`
- `tests/test_research.py::test_定性分析审核的新措辞都登记了`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/full.py b/ir_agent/full.py
index 1c57620..655d2e9 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -561,48 +561,84 @@ def _load_sections(r):
         return None, f"年报叙述章节（{e.__class__.__name__}: {e}）"
 
 
-def _load_macro_view(r, as_of: date, client, macro_pr: str):
-    """宏观定性: 政策取向与传导路径。补的是读数之外的那一半。"""
+def _load_macro_view(r, as_of: date, client, macro_pr: str, *, out: Path | None = None,
+                     sink: list | None = None, catalog: str = ""):
+    """宏观定性: 政策取向与传导路径。补的是读数之外的那一半。撰写者 + 审核者（子项目四）。"""
     from ir_agent import macro_view as MV
 
     if r.corpus is None or not r.corpus.docs:
         return "", "", "宏观定性（无外部宏观资料，政策取向缺文字依据）"
-    try:
-        view, _ = MV.assess(client, r.corpus, r.ledger, as_of, macro_pr, judge=client)
-    except Exception as e:                                  # noqa: BLE001
-        return "", "", f"宏观定性（{e.__class__.__name__}: {str(e)[:120]}）"
+
+    def run():
+        return MV.assess(client, r.corpus, r.ledger, as_of, macro_pr,
+                         judge=client, catalog=catalog)[0]
+    if out is None:
+        view = run()
+    else:
+        view, st = _qual_module(
+            out, prefix="macro_view", label="宏观定性", corpus=r.corpus,
+            parse=lambda raw: MV.parse(raw, r.corpus),
+            facts=lambda v: MV.facts(v, r.ledger, as_of, r.corpus), run=run)
+        if sink is not None:
+            sink.append(st)
+    view.conflicts = MV.cross_check(view, r.ledger, as_of)
     return view.summary(), view.summary(), ""
 
 
-def _load_strategy(r, as_of: date, client, spot=None) -> tuple[str, str, str]:
-    """战略分析: 护城河、五力、关键不确定性 → 情景驱动因子。"""
+def _load_strategy(r, as_of: date, client, spot=None, *, out: Path | None = None,
+                   sink: list | None = None, catalog: str = "") -> tuple[str, str, str]:
+    """战略分析: 护城河、五力、关键不确定性 → 情景驱动因子。撰写者 + 审核者（子项目四）。"""
     from ir_agent import strategy
 
     sections, sec_err = _load_sections(r)
     if sections is None and r.corpus is None:
         return "", "", sec_err or "战略分析（无任何定性证据）"
-    try:
-        view, _ = strategy.assess(client, r.ledger, r.period, as_of,
-                                  sections, spot, judge=client, corpus=r.corpus)
-    except Exception as e:                                  # noqa: BLE001
-        return "", "", f"战略分析（{e.__class__.__name__}: {str(e)[:120]}）"
+
+    def run():
+        return strategy.assess(client, r.ledger, r.period, as_of, sections, spot,
+                               judge=client, catalog=catalog, corpus=r.corpus)[0]
+    if out is None:
+        view = run()
+    else:
+        view, st = _qual_module(
+            out, prefix="strategy", label="战略分析", corpus=r.corpus,
+            parse=lambda raw: strategy.parse(raw, r.corpus),
+            facts=lambda v: strategy.facts(v, r.ledger, strategy.annual_text(sections),
+                                           as_of, r.corpus),
+            run=run)
+        if sink is not None:
+            sink.append(st)
+    view.unquantified = [f for f in strategy.Force
+                         if f not in strategy.anchors(r.ledger, r.period, as_of, spot)]
     r.sections = sections
     r.strategy_view = view          # 情景分析要用它识别出的关键不确定性
     return view.summary(), view.summary(), sec_err
 
 
-def _load_value_chain(r, as_of: date, client) -> tuple[str, str, str]:
-    """产业链与行业真实规模。"""
+def _load_value_chain(r, as_of: date, client, *, out: Path | None = None,
+                      sink: list | None = None, catalog: str = "") -> tuple[str, str, str]:
+    """产业链与行业真实规模。撰写者 + 审核者（子项目四）。"""
     from ir_agent import value_chain as VC
 
     sections = getattr(r, "sections", None)
     if r.corpus is None and sections is None:
         return "", "", "产业链分析（无任何资料）"
-    try:
-        view, _ = VC.analyse(client, r.corpus, r.ledger, r.period, as_of,
-                             sections, judge=client)
-    except Exception as e:                                  # noqa: BLE001
-        return "", "", f"产业链分析（{e.__class__.__name__}: {str(e)[:120]}）"
+    body = "\n".join(s.text for s in (sections.sections.values() if sections else []))
+
+    def run():
+        return VC.analyse(client, r.corpus, r.ledger, r.period, as_of, sections,
+                          judge=client, catalog=catalog)[0]
+    if out is None:
+        view = run()
+    else:
+        view, st = _qual_module(
+            out, prefix="value_chain", label="产业链", corpus=r.corpus,
+            parse=lambda raw: VC.parse(raw, r.corpus),
+            facts=lambda v: VC.facts(v, r.ledger, body, as_of, r.corpus, r.period),
+            run=run)
+        if sink is not None:
+            sink.append(st)
+    view.conflicts = VC.cross_check(view, r.ledger, r.period, as_of)
     if view.size:
         r.ledger.put(view.size.to_fact(
             r.corpus.docs[0].doc_id if r.corpus and r.corpus.docs else "ext",
@@ -1176,17 +1212,30 @@ def _analyse_facts(code: str, r, as_of: date, snapshot_dir):
 
 
 def _analyse_qualitative(code: str, r, as_of: date, model,
-                         md: dict, pr: dict, skipped: list[str]):
+                         md: dict, pr: dict, skipped: list[str], *,
+                         out: Path | None = None, stops: list | None = None,
+                         statuses: list | None = None):
     """需要模型的定性层（宏观定性 / 战略 / 产业链）。**就地**写入调用方的
     md/pr/skipped，只返回本层用的 client —— 开销要累计到总开销里。
 
     独立于 `_analyse_facts` 拆开（此前两者合在一个 `_analyse` 里）：确定性层
     两阶段都要重跑，定性层要调模型、只在第一阶段之一（`run_analyse`）跑一次，
     结果落进 state.json 供后两次调用读回。
+
+    **子项目四（spec 2026-10-17）**：三个模块各自「撰写者 + 审核者」。审核没通过或
+    模型接口失败 → `QualStop`，记进 `stops`（调用方据此停下第一次调用），其余模块
+    照常跑完（用户决定 9）。`statuses` 收集各模块的审核状态，供门① 清单。
     """
+    stops = stops if stops is not None else []
+    statuses = statuses if statuses is not None else []
+
     def take(name, fn):
         try:
             a, b, err = fn()
+        except QualStop as e:
+            md[name] = pr[name] = ""
+            stops.append(str(e))
+            return
         except Exception as e:                              # noqa: BLE001
             md[name] = pr[name] = ""
             skipped.append(f"{name}（{e.__class__.__name__}: {str(e)[:120]}）")
@@ -1198,12 +1247,100 @@ def _analyse_qualitative(code: str, r, as_of: date, model,
     qual_client = make_client(model=model)
     spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                  if f.key == "market_cap"), None)
-    take("宏观定性", lambda: _load_macro_view(r, as_of, qual_client, pr["宏观"]))
-    take("战略", lambda: _load_strategy(r, as_of, qual_client, spot))
-    take("产业链", lambda: _load_value_chain(r, as_of, qual_client))
+    catalog = _qual_catalog(r, as_of)
+    kw = dict(out=out, sink=statuses, catalog=catalog)
+    take("宏观定性", lambda: _load_macro_view(r, as_of, qual_client, pr["宏观"], **kw))
+    take("战略", lambda: _load_strategy(r, as_of, qual_client, spot, **kw))
+    take("产业链", lambda: _load_value_chain(r, as_of, qual_client, **kw))
     return qual_client
 
 
+class QualStop(Exception):
+    """定性模块审核没通过（或模型接口失败）—— 第一次调用停下交人工（子项目四决定 9）。
+    消息里带各轮记录与最后一稿的路径。"""
+
+
+def _qual_catalog(r, as_of: date) -> str:
+    """审核者的账本事实目录（用户决定 6）。取不到时给空串，不拦审核。"""
+    try:
+        return FactCatalog.from_ledger(r.ledger, period=r.period, as_of=as_of,
+                                       extra_periods=_catalog_periods(r)
+                                       ).render_for_prompt()
+    except Exception:                                       # noqa: BLE001
+        return ""
+
+
+def _qual_module(out: Path, *, prefix: str, label: str, corpus, parse, facts, run):
+    """一个定性模块的落盘、沿用与停下（spec 五）。返回 (结论, 审核状态)。
+
+    - `<prefix>.md` 已在 → 读回、**不调模型**：指纹与审核记录一致则沿用审核结论（语料库
+      若已变，重核依据并标出核对不过的处数）；不一致或没有审核记录 → 「人工修改后未经
+      审核」（用户决定 11、12）。
+    - 否则调 `run()`（撰写 + 审核）：通过 → 写结果、审核结论、各轮记录；审核没通过 →
+      写各轮记录与最后一稿，抛 `QualStop`；模型接口失败 → 抛 `QualStop`，写明重跑即可。
+    """
+    from ir_agent import qual_review as Q
+    from ir_agent.methodology_review import ReviewResult
+
+    res_f, rounds_f = out / f"{prefix}.md", out / f"{prefix}_rounds.md"
+    review_f, failed_f = out / f"{prefix}_review.json", out / f"{prefix}_failed_raw.md"
+    status = {"label": label, "state": "", "rounds": 0, "flagged": [], "note": ""}
+    if res_f.exists():
+        raw = res_f.read_text(encoding="utf-8")
+        view = parse(raw)
+        rec = json.loads(review_f.read_text(encoding="utf-8")) if review_f.exists() else {}
+        if rec.get("passed") and rec.get("fingerprint") == Q.fingerprint(raw):
+            view.review = ReviewResult.from_json(rec)
+            status.update(state="沿用审核结论", rounds=view.review.rounds,
+                          flagged=[v.line() for v in view.review.flagged()])
+            if rec.get("corpus", "") != Q.corpus_fingerprint(corpus):
+                n = sum(1 for x in facts(view)
+                        if x.startswith("依据核验不过") or "不在语料库" in x)
+                if n:
+                    status["note"] = f"本轮语料库与审核时不同，{n} 处依据本轮核对不过"
+        else:
+            status["state"] = "人工修改后未经审核"
+        return view, status
+    try:
+        view = run()
+    except Q.ReviewStop as e:
+        rounds_f.write_text(Q.AR.rounds_markdown(e.rounds, title=f"{label}审核",
+                                                 author="撰写者"), encoding="utf-8")
+        failed_f.write_text(e.last or "", encoding="utf-8")
+        raise QualStop(f"{e} —— 各轮记录：{rounds_f}；最后一稿：{failed_f}") from e
+    except ClaudeCliError as e:
+        raise QualStop(f"{label}审核停下：模型接口问题（{e.__class__.__name__}: "
+                       f"{str(e)[:120]}）—— 原样重跑 --analyse 即可") from e
+    raw = view.rounds[-1].raw
+    res_f.write_text(raw, encoding="utf-8")
+    failed_f.unlink(missing_ok=True)       # 上一轮停下留的底稿已过时
+    review_f.write_text(Q.dump(view.review, passed=True,
+                               corpus_fp=Q.corpus_fingerprint(corpus)), encoding="utf-8")
+    rounds_f.write_text(Q.AR.rounds_markdown(view.rounds, title=f"{label}审核",
+                                             author="撰写者"), encoding="utf-8")
+    status.update(state="审核通过", rounds=view.review.rounds,
+                  flagged=[v.line() for v in view.review.flagged()])
+    return view, status
+
+
+def _qual_section(statuses: list) -> str:
+    """门① `sources.md` 的「定性分析审核」一节（spec 六）。"""
+    if not statuses:
+        return ""
+    lines = ["", "## 定性分析审核", ""]
+    for s in statuses:
+        if s["state"] == "人工修改后未经审核":
+            lines.append(f"- {s['label']}：⚠ 人工修改后未经审核")
+        else:
+            head = "沿用上轮审核结论（" if s["state"] == "沿用审核结论" else ""
+            lines.append(f"- {s['label']}：{head}第 {s['rounds']} 轮审核通过"
+                         + ("）" if head else ""))
+        lines += [f"  - {x}" for x in s.get("flagged") or []]
+        if s.get("note"):
+            lines.append(f"  - ⚠ {s['note']}")
+    return "\n".join(lines) + "\n"
+
+
 def _restore_state(out: Path) -> tuple[dict, dict]:
     """读回上一次调用落盘的定性分析结果（`state.json` 的 `md` / `pr`）。
 
@@ -1220,7 +1357,8 @@ def _restore_state(out: Path) -> tuple[dict, dict]:
 
 
 def _save_state(out: Path, code: str, r, as_of: date, md: dict, pr: dict,
-                *, upheld: int | None = None, **signoff: str) -> None:
+                *, upheld: int | None = None, qual_unreviewed: list | None = None,
+                **signoff: str) -> None:
     """落盘 `state.json`。三次调用都写它，每次把自己那道门的签名补进去。
 
     **先读旧的再 update，不能整份覆盖。** `upheld` 只有跑过辩论的那次调用
@@ -1234,6 +1372,9 @@ def _save_state(out: Path, code: str, r, as_of: date, md: dict, pr: dict,
                 "md": md, "pr": pr})
     if upheld is not None:
         old["upheld"] = upheld
+    # 子项目四：人工修改后未经审核的定性模块，第二阶段写进研报附录。
+    if qual_unreviewed is not None:
+        old["qual_unreviewed"] = list(qual_unreviewed)
     old.update({k: v for k, v in signoff.items() if v})
     f.write_text(json.dumps(old, ensure_ascii=False, indent=2),
                  encoding="utf-8")
@@ -2010,7 +2151,26 @@ def run_analyse(code: str, r, as_of: date, out_dir: str | Path, *,
     skipped += _load_announce(code, r, as_of, out, md)
     _refresh_external_md(r, md)
 
-    qual_client = _analyse_qualitative(code, r, as_of, model, md, pr, skipped)
+    stops: list[str] = []
+    statuses: list[dict] = []
+    qual_client = _analyse_qualitative(code, r, as_of, model, md, pr, skipped,
+                                       out=out, stops=stops, statuses=statuses)
+    cost = Decimal(str(research_client.total_cost_usd + qual_client.total_cost_usd))
+    for st in statuses:
+        if st["state"] == "人工修改后未经审核":
+            skipped.append(f"定性分析未经审核：{st['label']}（人工修改后未经审核）")
+        if st.get("note"):
+            skipped.append(f"定性分析沿用：{st['label']} {st['note']}")
+    unreviewed = [st["label"] for st in statuses if st["state"] == "人工修改后未经审核"]
+    if stops:
+        # **任一定性模块审核没通过 → 第一次调用停下交人工**（子项目四决定 9）：
+        # 三个模块都已跑完（通过的已落盘，重跑沿用）；不写来源清单、不进门①。
+        skipped += stops
+        _save_state(out, code, r, as_of, md, pr, qual_unreviewed=unreviewed)
+        return ResearchOutcome(
+            code=code, period=r.period, as_of=as_of, stage=Stage.INCOMPLETE,
+            artifacts=arts, skipped=skipped, cost_usd=cost, phase="analyse",
+            route_reason="定性分析审核没通过，第一次调用停下（原因见上方清单）")
 
     # ── 门①：来源清单 ───────────────────────────────────────────
     # `peers` 不在 `r` 上 —— `peer_set(code)` 的结果是 `_load_industry` 的
@@ -2023,8 +2183,8 @@ def run_analyse(code: str, r, as_of: date, out_dir: str | Path, *,
         peers=_peer_names(code),
         annual=(annual_url, (_annual_cache_dir() / annual_url.rsplit("/", 1)[-1])
                 if annual_url else None))
-    (out / SOURCES_MD).write_text(render(rows, _snapshot_gaps(r)),
-                                  encoding="utf-8")
+    (out / SOURCES_MD).write_text(render(rows, _snapshot_gaps(r))
+                                  + _qual_section(statuses), encoding="utf-8")
     (out / SOURCES_JSON).write_text(
         json.dumps({"rows": [vars(x) for x in rows], "verified_by": ""},
                    ensure_ascii=False, indent=2), encoding="utf-8")
@@ -2034,13 +2194,10 @@ def run_analyse(code: str, r, as_of: date, out_dir: str | Path, *,
 
     _write_dashboard(r, out, md, code, arts)
     skipped += _media_tier_note(getattr(r, "corpus", None))
-    _save_state(out, code, r, as_of, md, pr)
+    _save_state(out, code, r, as_of, md, pr, qual_unreviewed=unreviewed)
     return ResearchOutcome(
         code=code, period=r.period, as_of=as_of, stage=Stage.AWAITING_SOURCES,
-        artifacts=arts, skipped=skipped,
-        cost_usd=Decimal(str(research_client.total_cost_usd
-                             + qual_client.total_cost_usd)),
-        phase="analyse")
+        artifacts=arts, skipped=skipped, cost_usd=cost, phase="analyse")
 
 
 def _stance_round(client, out, fname, round_, subjects, ctx, cat, r,
diff --git a/ir_agent/research.py b/ir_agent/research.py
index 1a1642b..afb4e70 100644
--- a/ir_agent/research.py
+++ b/ir_agent/research.py
@@ -135,6 +135,12 @@ _PREFIX = (
     # 判断项审核（子项目三）停下交人工 / 第二阶段读回人改过的判断项：都是「算
     # 出来了或停在那儿、必须被看见」，不是「未生成」。
     ("判断项审核", "  ⚠ "),
+    # 定性模块审核（子项目四）：停下 / 人工修改后未经审核 / 沿用时语料库已变。
+    ("宏观定性审核", "  ⚠ "),
+    ("战略分析审核", "  ⚠ "),
+    ("产业链审核", "  ⚠ "),
+    ("定性分析未经审核：", "  ⚠ "),
+    ("定性分析沿用：", "  ⚠ "),
     ("判断项未经审核：", "  ⚠ "),
     # 第二阶段读回第一阶段落盘的公司公告：**成功**路径，为读者报告「读回了
     # 几篇、第一阶段候选与挑选各多少」——既不是警告也不是缺了什么产物。
@@ -308,6 +314,14 @@ def next_actions(stage: Stage, code: str,
                 f"重跑第一阶段之一即可：\n"
                 f"     python -m ir_agent {code} {y} --analyse {out}",
             ]
+            # 子项目四：定性模块审核停下 —— 通过的模块重跑时沿用，只重做没通过的。
+            for pre, cn in (("macro_view", "宏观定性"), ("strategy", "战略分析"),
+                            ("value_chain", "产业链")):
+                if (Path(out) / f"{pre}_failed_raw.md").exists():
+                    acts.append(
+                        f"{cn}审核停下。若要人工给出：以 {out}/{pre}_failed_raw.md 为底稿"
+                        f"改好，存为 {out}/{pre}.md（各轮审核意见见 {out}/{pre}_rounds.md），"
+                        "再跑上面那条命令 —— 读回时会标注「人工修改后未经审核」。")
             if stale:
                 acts.append(
                     f"⚠ {out}/{ASSUMPTIONS_MD} 是**上一轮**留下的，不是本轮产物——"
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2460 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `_qual_module` 沿用条件去掉指纹比较 → 「人改过不送审、标注」测试红
- `run_analyse` 的 `if stops:` 改成 `if False:` → 「审核停下，不出来源清单」测试红
- `take()` 把 `QualStop` 记进 `skipped` 而不是 `stops` → 「一个模块停下其余照常跑完」测试红
- `ClaudeCliError` 分支改成别的异常 → 「模型接口失败也停下」测试红
- 来源清单不拼 `_qual_section` → 门① 一节的测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(full): 第一次调用接上定性审核 —— 落盘、沿用、人工接手、任一模块不过即停下

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 研报附录写明未经审核的定性模块

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `tests/test_qual_stage.py`

**Interfaces:**
- Produces: `full._assumption_caveats(a, anchor_note='', unreviewed='', qual_unreviewed=None) -> (str, str)`

**为什么：** 第二阶段读回 `state.json` 的 `qual_unreviewed`：终端「定性分析未经审核」；研报末尾「⚠ 判断项依据核验」附录写明，进提示词那句不带数字。

- [ ] **Step 1: 写测试**

```diff
diff --git a/tests/test_qual_stage.py b/tests/test_qual_stage.py
index e61a4ea..2cd90cd 100644
--- a/tests/test_qual_stage.py
+++ b/tests/test_qual_stage.py
@@ -142,3 +142,23 @@ def test_重跑通过后删掉上一轮停下留的最后一稿(tmp_path):
     (tmp_path / "strategy_failed_raw.md").write_text("上一轮的最后一稿", encoding="utf-8")
     _call(tmp_path, _passed)
     assert not (tmp_path / "strategy_failed_raw.md").exists()
+
+
+def test_研报附录写明人工修改后未经审核的定性模块():
+    """spec 六：第一次调用把「人工修改后未经审核」的模块名记进 state.json，第二阶段写进
+    研报末尾「⚠ 判断项依据核验」附录；进提示词那句不带数字。"""
+    import re
+
+    from ir_agent import assume
+    a = assume.AssumptionSet(code="600176", labels=["2026E"])
+    line, md = full._assumption_caveats(a, qual_unreviewed=["战略分析", "产业链"])
+    assert "战略分析" in line and "产业链" in line and "未经" in line
+    assert not re.search(r"\d", line)
+    assert "战略分析、产业链" in md and "人工修改后未经审核" in md
+
+
+def test_第二阶段读回未经审核的定性模块():
+    import inspect
+    src = inspect.getsource(full.run_valuation)
+    assert 'state.get("qual_unreviewed")' in src
+    assert "qual_unreviewed=qual_unreviewed" in src
```

- [ ] **Step 2: 跑测试**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`3 failed, 2460 passed, 8 skipped`。失败的是：

- `tests/test_qual_stage.py::test_研报附录写明人工修改后未经审核的定性模块`
- `tests/test_qual_stage.py::test_第二阶段读回未经审核的定性模块`

- [ ] **Step 3: 实现**

```diff
diff --git a/ir_agent/full.py b/ir_agent/full.py
index 655d2e9..0e9e5aa 100644
--- a/ir_agent/full.py
+++ b/ir_agent/full.py
@@ -1888,8 +1888,8 @@ def _unreviewed_assumptions(out: Path, a, edits: list[str]) -> str:
     return ""
 
 
-def _assumption_caveats(a, anchor_note: str = "",
-                        unreviewed: str = "") -> tuple[str, str]:
+def _assumption_caveats(a, anchor_note: str = "", unreviewed: str = "",
+                        qual_unreviewed: list | None = None) -> tuple[str, str]:
     """判断项的软处要跟着交付物走：(进提示词的一句话, 追加进 report.md 的附录)。
 
     两件事，分别说：
@@ -1925,14 +1925,19 @@ def _assumption_caveats(a, anchor_note: str = "",
     unverified = assume.unverified_labels(a)
     estimated = assume.estimated_labels(a)
     rebased = bool(a.margin_bases)
+    qual_unreviewed = list(qual_unreviewed or [])
     if (not unverified and not estimated and not anchor_note and not rebased
-            and not unreviewed):
+            and not unreviewed and not qual_unreviewed):
         return "", ""
 
     says: list[str] = []
     # 人改过、未经 AI 审核：读者必须知道这份判断项没有审核者背书（子项目三）。
     if unreviewed:
         says.append("**判断项经人工修改或补写，修改后未经 AI 审核**")
+    # 子项目四：第一次调用的定性模块人工修改后未经审核 —— 研报引用它们的结论时要说明。
+    if qual_unreviewed:
+        says.append("**定性分析中的" + "、".join(qual_unreviewed)
+                    + "经人工修改，修改后未经 AI 审核**")
     # 锚定影响最大，先说：首年批准值整个没进模型，研报正文却一直按它讲。
     if anchor_note:
         says.append("**首个预测年由已披露中期锚定**，批准清单上该年的营收增速"
@@ -1958,6 +1963,9 @@ def _assumption_caveats(a, anchor_note: str = "",
     body: list[str] = ["", "---", "", "## ⚠ 判断项依据核验", ""]
     if unreviewed:
         body += [f"**{unreviewed}。**", ""]
+    if qual_unreviewed:
+        body += ["**第一次调用的定性分析中，" + "、".join(qual_unreviewed)
+                 + " 经人工修改，人工修改后未经审核。**", ""]
     if anchor_note:
         body += [f"**{anchor_note}**", "",
                  "锚定的三档（占全年营收比重、剩余期毛利率）见工作簿「1_假设」"
@@ -2826,6 +2834,10 @@ def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
     unreviewed = _unreviewed_assumptions(out, a, edits)
     if unreviewed:
         skipped.append(f"判断项未经审核：{unreviewed}")
+    # 子项目四：第一次调用里人工修改后未经审核的定性模块，研报附录与终端都要写明。
+    qual_unreviewed = list(state.get("qual_unreviewed") or [])
+    for x in qual_unreviewed:
+        skipped.append(f"定性分析未经审核：{x}（人工修改后未经审核）")
 
     # **重建派生事实必须排在校验之前。** industry.* / comps.* / macro.*
     # 这些 key 只存在于内存账本，由 _analyse_facts 算出来；第一阶段的
@@ -2936,7 +2948,7 @@ def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
     # 判断项的软处（依据未逐字核验 / 取值是估计）要跟着交付物走，见 `_assumption_caveats`。
     caveat, caveats_md = _assumption_caveats(
         a, anchor_note=(dcf_res.anchor_note if dcf_res is not None else ""),
-        unreviewed=unreviewed)
+        unreviewed=unreviewed, qual_unreviewed=qual_unreviewed)
     if dcf_res is not None:
         full, cost, ferr, debate_md, dnote = _write_full_report(
             code, r, body, model, dcf_res, _prompt_bundle(pr), out,
```

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2462 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- 附录不写定性模块（`if qual_unreviewed:` 改成 `if False:`）→ 附录测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat(value): 研报附录写明人工修改后未经审核的定性模块

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 端到端与中国巨石回放（验收测试）

**Files:**
- Modify: `tests/test_full_context.py`
- Modify: `tests/test_qual_review.py`
- Modify: `tests/test_strategy.py`

**Interfaces:**
（无新接口）

**为什么：** 第一次调用**不打桩** `_analyse_qualitative`，只把模型换成按提示词分派的假模型；战略稿子按中国巨石那次的写法引研报。三个场景：都通过 → 进门①、引研报的战略不再缺失；战略三轮不过 → 停下、产业链照常落盘、不写来源清单；人工以底稿改好后重跑 → 不调模型、标未经审核。外加两条补测：变异验证补的「五力只写了三条，清单仍列满五条」；对照 spec 七补的「补答不消耗撰写者轮数」（审核者第一次漏答、补答后通过，撰写者只写一稿）。功能在 Task 1–7 已完成，这一组**预期直接通过**。

- [ ] **Step 1: 写测试**

```diff
diff --git a/tests/test_full_context.py b/tests/test_full_context.py
index 1b4b0b0..306c014 100644
--- a/tests/test_full_context.py
+++ b/tests/test_full_context.py
@@ -4428,3 +4428,77 @@ def test_定性模块人工修改过_门一清单与终端标注_记进state(tmp
     assert any(s.startswith("定性分析沿用：产业链") for s in o.skipped)
     st = json.loads((tmp_path / full.STATE).read_text(encoding="utf-8"))
     assert st["qual_unreviewed"] == ["战略分析"]
+
+
+# 中国巨石 600176 2026-10-17：五力引了研报，被代码判「需年报原文或占位符」连挂三次，
+# 战略分析整节缺失。下面的战略稿子按同样的写法引研报。
+_QUAL_STRATEGY = "\n".join([
+    "护城河: 成本优势 | 宽 | 转述：综合 东莞证券《玻纤行业深度报告》",
+    *[f"五力: {f} | 中 | 中性 | 「产品价格回升低于预期」——东莞证券《玻纤行业深度报告》"
+      for f in ("现有竞争强度", "新进入者威胁", "供应商议价能力", "买方议价能力", "替代品威胁")],
+    "不确定性: 玻纤价格回落 | revenue_growth | 下调 | 转述：综合 东莞证券《玻纤行业深度报告》",
+    "不确定性: 能源成本上行 | gross_margin | 下调 | 转述：综合 东莞证券《玻纤行业深度报告》"])
+_QUAL_CHAIN = "\n".join([
+    "环节: 上游 | 叶腊石与能源 | 成本端 | 转述：综合 东莞证券《玻纤行业深度报告》",
+    "环节: 本环节 | 玻纤纱与制品 | 本公司所处环节 | 转述：综合 东莞证券《玻纤行业深度报告》",
+    "环节: 下游 | 风电与电子布 | 需求端 | 转述：综合 东莞证券《玻纤行业深度报告》",
+    "议价: 上游 | 均衡", "议价: 下游 | 均衡"])
+
+
+def _qual_e2e(monkeypatch, full, tmp_path, judge):
+    """第一次调用**不打桩 `_analyse_qualitative`**，只把模型换成按提示词分派的假模型。"""
+    real = full._analyse_qualitative
+    _offline(monkeypatch, full)
+    monkeypatch.setattr(full, "_analyse_qualitative", real)
+    calls = {"战略": 0, "产业链": 0}
+
+    class _C:
+        total_cost_usd = 0.0
+
+        def __call__(self, p):
+            if "**必须逐条作答的条目**" in p:
+                return judge(p)
+            if "做战略分析" in p:
+                calls["战略"] += 1
+                return _QUAL_STRATEGY
+            if "梳理本公司所处的产业链" in p:
+                calls["产业链"] += 1
+                return _QUAL_CHAIN
+            raise AssertionError("不该有别的模型调用")
+    monkeypatch.setattr(full, "make_client", lambda **k: _C())
+    return calls
+
+
+def test_定性审核_端到端_都通过_进门一(tmp_path, monkeypatch):
+    from ir_agent import full
+
+    calls = _qual_e2e(monkeypatch, full, tmp_path, Judge())
+    o = full.run_analyse("600176", _PhaseR(), date(2026, 10, 17), tmp_path)
+    assert o.stage is full.Stage.AWAITING_SOURCES, o.skipped
+    assert calls == {"战略": 1, "产业链": 1}
+    for f in ("strategy.md", "strategy_review.json", "strategy_rounds.md", "value_chain.md"):
+        assert (tmp_path / f).exists(), f
+    assert "## 定性分析审核" in (tmp_path / full.SOURCES_MD).read_text(encoding="utf-8")
+    st = json.loads((tmp_path / full.STATE).read_text(encoding="utf-8"))
+    assert "现有竞争强度" in st["md"]["战略"], "引研报的战略分析不再缺失"
+
+
+def test_定性审核_端到端_战略三轮不过停下_产业链照常落盘_人工接手后沿用(tmp_path, monkeypatch):
+    from ir_agent import full
+
+    calls = _qual_e2e(monkeypatch, full, tmp_path, Judge({"自洽": "致命"}))
+    o = full.run_analyse("600176", _PhaseR(), date(2026, 10, 17), tmp_path)
+    assert o.stage is full.Stage.INCOMPLETE and calls["战略"] == 3
+    assert (tmp_path / "value_chain.md").exists() and not (tmp_path / "strategy.md").exists()
+    assert (tmp_path / "strategy_failed_raw.md").exists()
+    assert not (tmp_path / full.SOURCES_MD).exists()
+
+    # 人工以底稿改好存成 strategy.md，重跑：战略不调模型、产业链沿用，标未经审核。
+    (tmp_path / "strategy.md").write_text(
+        (tmp_path / "strategy_failed_raw.md").read_text(encoding="utf-8"), encoding="utf-8")
+    calls.update(战略=0, 产业链=0)
+    o2 = full.run_analyse("600176", _PhaseR(), date(2026, 10, 17), tmp_path)
+    assert calls == {"战略": 0, "产业链": 0}, "通过的与人工接手的都不该再调模型"
+    assert o2.stage is full.Stage.AWAITING_SOURCES, o2.skipped
+    assert any(s.startswith("定性分析未经审核：战略分析") for s in o2.skipped)
+    assert "战略分析：⚠ 人工修改后未经审核" in (tmp_path / full.SOURCES_MD).read_text(encoding="utf-8")
diff --git a/tests/test_qual_review.py b/tests/test_qual_review.py
index dfa138c..f993d75 100644
--- a/tests/test_qual_review.py
+++ b/tests/test_qual_review.py
@@ -108,6 +108,16 @@ class TestRun:
         with pytest.raises(Q.ReviewStop, match="战略分析审核未完成"):
             _run(_Writer("项: a"), Judge(skip={("自洽", "三部分")}))
 
+    def test_补答不消耗撰写者轮数(self):
+        class Once(Judge):
+            def __call__(self, prompt):
+                self.skip = {("自洽", "三部分")} if not self.prompts else set()
+                return super().__call__(prompt)
+        w, j = _Writer("项: a"), Once()
+        _, res, rounds = _run(w, j)
+        assert len(j.prompts) == 2 and len(w.prompts) == 1 and res.rounds == 1
+        assert "上一次回答不完整" in j.prompts[1] and len(rounds) == 1
+
 
 def test_指纹不计空白_语料库指纹按文档集合():
     assert Q.fingerprint("项: a\n\n") == Q.fingerprint("  项: a")
diff --git a/tests/test_strategy.py b/tests/test_strategy.py
index 5ba8f14..f703d1b 100644
--- a/tests/test_strategy.py
+++ b/tests/test_strategy.py
@@ -384,3 +384,12 @@ def test_停下时带上最后一稿():
         strategy.assess(_Client(bad, bad, bad), _led(), P, AS_OF, _sections(),
                         judge=Judge({"五力": "致命"}))
     assert "年报未提及替代品" in e.value.last
+
+
+def test_五力只写了三条_清单仍列满五条():
+    """变异验证补的：清单按方法论（固定 5 力）而不是按稿子生成 —— 漏写的力要出现在
+    清单里，让审核者判「未给出」（用户决定 5）。"""
+    v = strategy.parse(_out(forces=[f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」"
+                                    for f in list(Force)[:3]]))
+    assert len(v.forces) == 3
+    assert [s for k, s in strategy.checklist(v) if k == "五力"] == [f.value for f in Force]
```

- [ ] **Step 2: 跑测试**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2466 passed, 8 skipped`。

（验收测试：功能在 Task 1–7 已完成，放进来即通过；失败数里那 1 条是上面说的 openweb。）

- [ ] **Step 3: 实现**

本任务没有实现改动（验收测试，功能在前面的任务里已完成）。

- [ ] **Step 4: 跑测试，确认绿**

```bash
ASHARE_DCF_DIR="/Users/ariasleo/Desktop/谢海量作品集/ashare-dcf" python -m pytest -q
```
演练实测：`1 failed, 2466 passed, 8 skipped`。

- [ ] **Step 5: 变异验证**（复制备份 → 改一处 → 跑相关测试应红 → 用备份还原）

- `checklist` 的五力改成按稿子生成 → 「五力只写了三条」测试红
- `qual_review.JUDGE_TRIES` 改成 `1` → 「补答不消耗撰写者轮数」测试红

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "test(qual): 第一次调用定性审核端到端，含中国巨石引研报回放

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 做完之后

用中国巨石重跑第一次调用（`out/600176-p3`），由 Claude 代替人工审核各道门（签名「Claude（受 xhl 委托）」）：

```bash
python -m ir_agent 600176 --year 2025 --analyse out/600176-p3
```

跑通后接着第二、三次调用与第二阶段；全流程通过后再问用户是否推送。
