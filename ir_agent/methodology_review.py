"""方法论预研的审核者（子项目二，spec `2026-10-14-methodology-judge-design.md`）。

**为什么要有审核者。** 子项目一把分解式改成「模型写公式、代码求值」，代码只查
能不能算；「分母在公司内外」「是不是同一个量被算两遍」这类判断原先靠
`drivers` 里的字面闸门近似 —— 生益科技第二次调用被「非公司内部占比」里一个带
「非」的「占比」拦了三次。这类判断改由第二个模型角色按清单逐项审。

**清单由代码出，不由审核者出**（沿用 `factor_review`）：否则可以通过少列条目
回避问题。**每条都必须作答**，沉默不等于通过。

**四级结论**（用户决定，原话）：
- 通过：逻辑完全正确，资料能够对得上；
- 提醒：逻辑部分正确，资料对得上，但结论较为牵强；
- 致命：逻辑完全无法支撑结论；
- 待采集：逻辑正确，资料尚未采集到。

有一项致命即驳回、交回提出者重写；最多 3 轮；仍不通过停下交人工 —— 不再退回
两标量模式（那条路让整套因子链路悄悄消失）。

**审核者的输入比提出者多**（门③ `factor_review` 的教训：只给同样的资料，审核
只是同一个模型换个角色再说一遍）：代码额外算出账本实际取值、检索词命中次数、
覆盖核对、水平模式第 0 年试算。审核者不联网，资料不够由「待采集」承接。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from itertools import combinations

LEVELS = ("通过", "提醒", "致命", "待采集")
KINDS = ("模式", "因子", "正交", "公式", "常量", "账本", "覆盖")
MAX_ROUNDS = 3          # 提出者最多写 3 轮（代码检查不过、审核驳回都算一轮）
JUDGE_TRIES = 3         # 审核者作答 1 次 + 漏答补答 2 次，不消耗提出者轮数

# 行首允许列表符号与加粗（模型常写 `- ` 或 `**[因子] …**`）。类别集合由调用方
# 给（判断项审核 `assume_review` 用另一套类别），默认是本模块的 7 类。
def _item_rx(kinds) -> re.Pattern[str]:
    return re.compile(rf"(?m)^[ \t]*[-*•·]*[ \t]*(?=\[(?:{'|'.join(kinds)})\])")


def _head_rx(kinds) -> re.Pattern[str]:
    return re.compile(rf"\[({'|'.join(kinds)})\]\s*(.*)", re.S)

_QUOTE = re.compile(r"「([^」]{4,})」")
_REF = re.compile(r"\[\[([^\]@]+)@([^\]]+)\]\]")
_KEY_DECL = re.compile(r"\[\[([A-Za-z_][\w.]*)(?:@[^\]]*)?\]\]")
# [账本] 条目照提出者原文列（带不带 @期间 原样保留）：审核者照它看到的原文作答。
_KEY_AS_WRITTEN = re.compile(r"\[\[([A-Za-z_][\w.]*(?:@[^\]]*)?)\]\]")
_SUGGEST = re.compile(r"\s*建议\s*[:：]\s*")


class ReviewIncomplete(Exception):
    """审核者没有覆盖全部条目，或提醒/致命/待采集没写理由 —— 沉默不能等于通过。"""


class ReviewStop(Exception):
    """停下交人工：3 轮仍有致命项，或审核者始终答不完整。`rounds` 是全部轮次。"""

    def __init__(self, msg: str, rounds: list):
        super().__init__(msg)
        self.rounds = rounds


@dataclass(frozen=True)
class Verdict:
    kind: str
    subject: str
    level: str
    why: str
    quote_note: str = ""     # 引文核验不过时的标注（只标注，不改结论）
    suggest: str = ""        # 审核者给提出者的修改建议（致命必写，其余可选）

    def line(self) -> str:
        tail = f"（⚠ {self.quote_note}）" if self.quote_note else ""
        sug = f"　建议：{self.suggest}" if self.suggest else ""
        return f"[{self.kind}] {self.subject}：{self.level}　{self.why}{tail}{sug}"


@dataclass
class ReviewResult:
    verdicts: list = field(default_factory=list)
    rounds: int = 1
    fingerprint: str = ""
    # 审核者每次作答的原文与问题（补答也算一次）。只进 drivers_rounds.md，不进 json。
    attempts: list = field(default_factory=list, compare=False)

    def fatal(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.level == "致命"]

    def flagged(self) -> list[Verdict]:
        """要人看的：提醒与待采集。"""
        return [v for v in self.verdicts if v.level in ("提醒", "待采集")]

    def counts(self) -> dict[str, int]:
        return {lv: sum(1 for v in self.verdicts if v.level == lv) for lv in LEVELS}

    def to_json(self) -> dict:
        return {"fingerprint": self.fingerprint, "rounds": self.rounds,
                "verdicts": [{"kind": v.kind, "subject": v.subject,
                              "level": v.level, "why": v.why,
                              "quote_note": v.quote_note, "suggest": v.suggest}
                             for v in self.verdicts]}

    @classmethod
    def from_json(cls, d: dict) -> "ReviewResult":
        return cls(verdicts=[Verdict(x["kind"], x["subject"], x["level"],
                                     x.get("why", ""), x.get("quote_note", ""),
                                     x.get("suggest", ""))
                             for x in d.get("verdicts") or []],
                   rounds=int(d.get("rounds", 1)),
                   fingerprint=d.get("fingerprint", ""))

    def markdown(self) -> list[str]:
        """drivers.md「方法论审核」一节：通过只给条数，提醒/待采集逐条列。"""
        c = self.counts()
        lines = ["## 方法论审核", "",
                 f"第 {self.rounds} 轮通过：通过 {c['通过']} 条、提醒 {c['提醒']} 条、"
                 f"待采集 {c['待采集']} 条。", ""]
        lines += [f"- {v.line()}" for v in self.flagged()]
        if self.flagged():
            lines.append("")
        return lines


@dataclass
class Round:
    """一轮的完整记录：提出者原文、代码检查结果、审核者逐项结论。"""

    n: int
    raw: str
    code_error: str = ""
    review: ReviewResult | None = None
    incomplete: str = ""
    attempts: list = field(default_factory=list)   # [(审核者原文, 不完整原因)]


def fingerprint(dsl: str) -> str:
    """分解式定义的指纹。重跑读回时比对，变了 = 人改过（决定 8）。"""
    return hashlib.sha256(dsl.strip().encode("utf-8")).hexdigest()[:16]


def checklist(model, themes: list[str]) -> list[tuple[str, str]]:
    """代码生成「必须作答的条目」（spec 二）。`themes` 为空时不列 [覆盖]。"""
    items: list[tuple[str, str]] = [("模式", "模式")]
    items += [("因子", f"{f.short}（{f.label}）") for f in model.factors]
    seen: set[tuple[str, str]] = set()
    for t in ("revenue", "gross_margin"):
        if t not in model.formulas:
            continue
        for a, b in combinations([f.short for f in model.factors_of(t)], 2):
            if (a, b) not in seen:
                seen.add((a, b))
                items.append(("正交", f"{a} × {b}"))
    items += [("公式", {"revenue": "营收", "gross_margin": "毛利率"}[t])
              for t in ("revenue", "gross_margin") if t in model.formulas]
    items += [("常量", c.name) for c in model.constants]
    items += [("账本", f"{f.short} [[{k}]]") for f in model.factors
              for k in dict.fromkeys(_KEY_AS_WRITTEN.findall(f.source_hint or ""))]
    items += [("覆盖", th) for th in themes]
    return items


def _subject_rx(kind: str, subject: str) -> re.Pattern[str]:
    """条目名的匹配式。审核者照它看到的原文写条目名（生益科技第二次调用第 3 轮漏掉
    [公式] 与 [账本] 共 4 条，审核未完成）：`[[key]]` 带不带 `@期间` 都认；[公式]
    条目名后面抄了「= …」也认（判断项审核的 [合成] 同理）。"""
    base = re.sub(r"@[^\]]*(?=\]\])", "", subject)
    pat = re.escape(base).replace(r"\]\]", r"(?:@[^\]]*)?\]\]")
    if kind in ("公式", "合成"):
        pat += r"(?:\s*[=＝][^：:\n]*)?"
    return re.compile(pat)


def parse(raw: str, expected: list[tuple[str, str]],
          kinds: tuple = KINDS) -> list[Verdict]:
    """解析审核者的回答。**按预期条目名匹配**，不靠正则切条目名 —— 完整名里
    可能带冒号、括号。缺条目、或提醒/致命/待采集没写理由 → ReviewIncomplete。"""
    by_kind: dict[str, list[str]] = {}
    for k, s in expected:
        by_kind.setdefault(k, []).append(s)
    got: dict[tuple[str, str], Verdict] = {}
    item_rx, head_rx = _item_rx(kinds), _head_rx(kinds)
    for block in item_rx.split(raw):
        m = head_rx.match(block.strip())
        if not m:
            continue
        kind, rest = m.group(1), m.group(2).strip()
        hit = next(((s, h) for s in sorted(by_kind.get(kind, []), key=len, reverse=True)
                    if (h := _subject_rx(kind, s).match(rest))), None)
        if hit is None:
            continue
        subj, h = hit
        tail = rest[h.end():].lstrip()
        if not tail or tail[0] not in "：:":
            continue
        tail = tail[1:].strip().lstrip("*")
        level = next((lv for lv in sorted(LEVELS, key=len, reverse=True)
                      if tail.startswith(lv)), None)
        if level is None:
            continue
        why, sug = (_SUGGEST.split(tail[len(level):], 1) + [""])[:2]
        why, sug = (" ".join(x.strip(" 　\t*：:").split()) for x in (why, sug))
        got[(kind, subj)] = Verdict(kind, subj, level, why, suggest=sug)
    missing = [e for e in expected if e not in got]
    no_reason = [v for v in got.values() if v.level != "通过" and not v.why]
    no_suggest = [v for v in got.values() if v.level == "致命" and v.why and not v.suggest]
    if missing or no_reason or no_suggest:
        parts = []
        if missing:
            parts.append("以下条目没有给出结论（每一条都必须回答，沉默不等于通过）："
                         + "、".join(f"[{k}] {s}" for k, s in missing[:10]))
        if no_reason:
            parts.append("以下条目判为提醒/致命/待采集却没写理由："
                         + "、".join(f"[{v.kind}] {v.subject}" for v in no_reason[:10]))
        if no_suggest:
            parts.append("以下条目判为致命却没给修改建议（理由之后写「建议：…」）："
                         + "、".join(f"[{v.kind}] {v.subject}" for v in no_suggest[:10]))
        raise ReviewIncomplete("；".join(parts))
    return [got[e] for e in expected]


def annotate_quotes(verdicts: list[Verdict], *, hay: str, ledger, as_of) -> list[Verdict]:
    """理由里引的原文「…」与账本占位符能否找到。**只标注，不改结论**（与多空
    辩论同一处理）。"""
    from ir_agent.sources.external import norm

    h = norm(hay or "")
    out: list[Verdict] = []
    for v in verdicts:
        notes = []
        for q in _QUOTE.findall(v.why):
            if norm(q) not in h:
                notes.append(f"引文「{q[:20]}」在语料与年报里找不到")
        for key, per in _REF.findall(v.why):
            try:
                ledger.get(key, per, as_of=as_of)
            except Exception:                              # noqa: BLE001
                notes.append(f"[[{key}@{per}]] 账本里没有")
        out.append(replace(v, quote_note="；".join(notes)))
    return out


def extra_facts(model, *, ledger, as_of, sections=None, corpus=None,
                themes=(), coverage_decl=()) -> str:
    """只给审核者看的、代码现算的事实（spec 二）。"""
    from ir_agent import gaps, methodology
    from ir_agent.formula import FormulaError, evaluate

    lines: list[str] = []
    # 1. [[key]] 声明在账本里实际取到的值
    decl = [(f, k) for f in model.factors
            for k in dict.fromkeys(_KEY_DECL.findall(f.source_hint or ""))]
    if decl:
        lines += ["【账本 key 声明的实际取值】"]
        for f, k in decl:
            have, _ = gaps.ledger_refs(f"[[{k}]]", ledger, as_of) if ledger else ([], [])
            if have:
                key, per = have[0].rsplit("@", 1)
                lines.append(f"- {f.short} [[{k}]]：账本 {key}@{per} = "
                             f"{ledger.get(key, per, as_of=as_of).value}")
            else:
                lines.append(f"- {f.short} [[{k}]]：账本里没有（或晚于研究时点才披露）")
    # 2. 检索词命中次数
    hits = methodology.term_hits(model, sections=sections, corpus=corpus)
    lines += ["", "【因子候选词在本轮语料里的命中次数】"]
    lines += [f"- {lab}：" + "　".join(f"{c}:{n}" for c, n in pairs)
              for lab, pairs in hits.items()]
    # 3. 覆盖核对
    if themes:
        cov, notes = methodology.coverage(
            methodology.Methodology(model=model,
                                    coverage_decl=list(coverage_decl)), list(themes))
        lines += ["", "【辩论主题覆盖（代码核对提出者的声明；未交代的按字面匹配）】"]
        lines += [f"- {c.theme}：{c.mark} {c.detail}" for c in cov]
        lines += [f"- ⚠ {x}" for x in notes]
    # 4. 水平模式第 0 年试算
    if model.mode == "水平":
        lines += ["", "【水平模式第 0 年试算（账本基期值代入公式，与报表并排）】"]
        base, lacking = {}, []
        for f in model.needs_base():
            have, _ = gaps.ledger_refs(f.source_hint, ledger, as_of) if ledger else ([], [])
            if have:
                key, per = have[0].rsplit("@", 1)
                base[f.short] = ledger.get(key, per, as_of=as_of).value
            else:
                lacking.append(f.short)
        if lacking:
            lines.append(f"- 缺 {'、'.join(lacking)} 的基期值（账本里没有），无法试算")
        else:
            consts = {c.name: c.value for c in model.constants}
            for t, cn, key, scale in (("revenue", "营收", "revenue", Decimal(10) ** 8),
                                      ("gross_margin", "毛利率", "gross_margin", 1)):
                try:
                    got = evaluate(model.formulas[t], base, None, base, consts)
                except FormulaError as e:
                    lines.append(f"- {cn}：试算失败（{e}）")
                    continue
                actual = _latest_annual(ledger, key, as_of)
                act = (f"{actual / scale}" if actual is not None else "报表里没有")
                lines.append(f"- {cn}：公式第 0 年 = {got}；报表 = {act}"
                             + ("（亿元）" if t == "revenue" else ""))
    return "\n".join(lines)


def _latest_annual(ledger, key: str, as_of):
    from ir_agent.operators.ratios import annual_periods

    if ledger is None:
        return None
    pers = annual_periods(ledger, as_of, years=1)
    try:
        return ledger.get(key, pers[-1], as_of=as_of).value if pers else None
    except Exception:                                     # noqa: BLE001
        return None


RULES = """你是研究总监，正在审核一份**驱动因子分解式**能不能用。你不直接改写
分解式，而是逐项给结论、理由与修改建议 —— 有一项「致命」，提出者就要按你的建议
重写。

**四级结论**（只能用这四个词）：
- **通过**：逻辑完全正确，资料能够对得上；
- **提醒**：逻辑部分正确，资料对得上，但结论较为牵强；
- **致命**：逻辑完全无法支撑结论；
- **待采集**：逻辑正确，资料尚未采集到。

提醒、致命、待采集**必须写理由**。凡涉及资料的判断，引原文「…」或账本占位符
[[key@期间]] —— 代码会核对你的引文能否在语料、年报、账本里找到。

**每类要判什么**（下面用「类别名：」说明，作答时才写成 [类别]）：
- 模式：选「变化」还是「水平」，与这个行业的特性、手里拿得到的资料是否相符。
- 因子：有无公开可查的口径；单位对不对；分母在公司内部还是外部（分母在内部的
  占比变动不等于营收变动）；现有资料能否支撑；是否进了公式；检索词是否过短或
  过泛（下面给了实际命中次数）。
- 正交：这两个因子是不是同一个量被重复计算（销量×售价已是收入，再乘一个营收
  增速就算了两遍）。只看量是否重复：两个不同的量共用同一条证据来源（比如都只能
  引同一个季度数字），是资料问题，判提醒或待采集，不判致命。
- 公式：量纲是否自洽（pp 换成变化率要除以上期水平、毛利率按小数计等）；能否
  算回营收与毛利率；`基期(·)`（相对基期累计）还是 `上期(·)`（逐年）与因子的填法
  是否一致；因子个数是否合适（一个不叫分解，超过五个人核实不过来）。
- 常量：数值与写的出处是否对得上。
- 账本：账本里这个 key 与因子是不是同一个量、口径是否一致（下面给了实际取值）。
- 覆盖：这个多空主题有没有因子承载；声明「未覆盖」的理由是否成立。

**每条一行，格式严格如下**（结论、理由、建议之间用全角空格）：

[类别] <条目>：<通过|提醒|致命|待采集>　<理由>　建议：<具体怎么改>

**判为致命的条目必须写「建议：…」** —— 具体怎么改：换掉哪个因子、公式怎么写、
哪两个因子合并或删掉哪一个。提醒、待采集有建议也写上；通过不必写。条目名照上面
清单原样抄，后面不要加公式或别的内容。
"""


def review(judge, *, proposal: str, model, materials: str, extra: str,
           themes=(), hay: str = "", ledger=None, as_of=None) -> ReviewResult:
    """审核者作答一次（漏答补答最多 2 次，不消耗提出者轮数）。"""
    expected = checklist(model, list(themes))
    items = "\n".join(f"- [{k}] {s}" for k, s in expected)
    base = (f"{RULES}\n**必须逐条作答的条目**\n{items}\n\n"
            f"**提出者给的分解式（原文）**\n{proposal}\n\n"
            f"**提出者看到的资料**\n{materials}\n\n"
            f"**只给你的、代码算出的事实**\n{extra}\n")
    prompt, last, attempts = base, "", []
    for _ in range(JUDGE_TRIES):
        raw = judge(prompt)
        try:
            verdicts = parse(raw, expected)
        except ReviewIncomplete as e:
            last = str(e)
            attempts.append((raw, last))
            prompt = f"{base}\n---\n**上一次回答不完整**：{last}\n请补全后重新输出全部条目。\n"
            continue
        attempts.append((raw, ""))
        return ReviewResult(verdicts=annotate_quotes(verdicts, hay=hay,
                                                     ledger=ledger, as_of=as_of),
                            attempts=attempts)
    err = ReviewIncomplete(last)
    err.attempts = attempts
    raise err


def _feedback(n: int, res: ReviewResult) -> str:
    lines = [f"**上一轮审核驳回（第 {n} 轮）**：以下条目判为致命，必须修改"
             "（每条附审核者的修改建议）："]
    lines += [f"- {v.line()}" for v in res.fatal()]
    if res.flagged():
        lines += ["以下为提醒/待采集，可改可不改："]
        lines += [f"- {v.line()}" for v in res.flagged()]
    lines.append("请修改后重新输出全部行。")
    return "\n".join(lines)


def propose_and_review(proposer, judge, *, industry: str, code: str,
                       prompt: str, materials: str, themes=(),
                       ledger=None, as_of=None, sections=None, corpus=None,
                       max_rounds: int = MAX_ROUNDS):
    """提出 → 代码查能不能算 → 审核 → 汇总。返回 (Methodology, ReviewResult, rounds)。

    代码检查不过、审核驳回都算一轮，合计最多 `max_rounds` 轮；审核者答不完整、
    或 3 轮仍有致命项 → `ReviewStop`（停下交人工）。模型接口错误原样抛出，由
    调用方说明是接口问题。
    """
    from ir_agent import methodology
    from ir_agent.strategy import annual_text

    hay = "\n".join(x for x in [
        sections if isinstance(sections, str)
        else (annual_text(sections) if sections is not None else ""),
        *[getattr(d, "text", "") or "" for d in (getattr(corpus, "docs", None) or [])]]
        if x)
    rounds: list[Round] = []
    for n in range(1, max_rounds + 1):
        raw = proposer(prompt)
        try:
            m = methodology.parse(raw)
        except methodology.MethodologyFailed as e:
            rounds.append(Round(n, raw, code_error=str(e)))
            prompt = (f"{prompt}\n\n---\n**上一轮代码检查不过（第 {n} 轮）**："
                      f"{e}\n请修正后重新输出全部行。\n")
            continue
        m.industry, m.rationale = industry, raw.strip()
        extra = extra_facts(m.model, ledger=ledger, as_of=as_of, sections=sections,
                            corpus=corpus, themes=themes,
                            coverage_decl=m.coverage_decl)
        try:
            res = review(judge, proposal=methodology._to_dsl(m), model=m.model,
                         materials=materials, extra=extra, themes=themes,
                         hay=hay, ledger=ledger, as_of=as_of)
        except ReviewIncomplete as e:
            rounds.append(Round(n, raw, incomplete=str(e),
                                attempts=getattr(e, "attempts", [])))
            raise ReviewStop(f"方法论审核未完成：审核者补答 {JUDGE_TRIES - 1} 次仍不全"
                             f"（{str(e)[:120]}）", rounds) from e
        res.rounds = n
        res.fingerprint = fingerprint(methodology._to_dsl(m))
        rounds.append(Round(n, raw, review=res, attempts=res.attempts))
        if not res.fatal():
            return m, res, rounds
        prompt = f"{prompt}\n\n---\n{_feedback(n, res)}\n"
    last = rounds[-1]
    why = (f"最后一轮致命 {len(last.review.fatal())} 条" if last.review
           else f"最后一轮代码检查不过：{last.code_error[:80]}")
    raise ReviewStop(f"方法论审核 {max_rounds} 轮未通过（{why}）", rounds)


def rounds_markdown(rounds: list[Round]) -> str:
    """`drivers_rounds.md`：每一轮的完整记录。此前只存最后一稿，前两次为什么被
    驳回无从查证。"""
    lines = ["# 方法论预研：各轮记录", ""]
    for r in rounds:
        lines += [f"## 第 {r.n} 轮", "", "### 提出者原文", "", *_fenced(r.raw), ""]
        if r.code_error:
            lines += ["### 代码检查：不过", "", r.code_error, ""]
        if r.incomplete:
            lines += ["### 审核：未完成", "", r.incomplete, ""]
        if r.review:
            c = r.review.counts()
            lines += [f"### 审核：{'驳回' if r.review.fatal() else '通过'}"
                      f"（致命 {c['致命']}、提醒 {c['提醒']}、待采集 {c['待采集']}、"
                      f"通过 {c['通过']}）", ""]
            lines += [f"- {v.line()}" for v in r.review.verdicts]
            lines.append("")
        for i, (raw, problem) in enumerate(r.attempts, 1):
            lines += [f"### 审核者原文（第 {i} 次作答）", "", *_fenced(raw), ""]
            if problem:
                lines += [f"→ 不完整：{problem}", ""]
    return "\n".join(lines)


def _fenced(text: str) -> list[str]:
    """围栏比原文里最长的一串反引号多一个 —— 提示词改用代码块后，模型的回答
    里也可能带 ```，三个反引号的围栏会被提前闭合。"""
    run = max((len(x) for x in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, run + 1)
    return [f"{fence}text", text.strip(), fence]


def dump_review(res: ReviewResult) -> str:
    return json.dumps(res.to_json(), ensure_ascii=False, indent=2)
