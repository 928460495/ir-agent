"""判断项的审核者（子项目三，spec `2026-10-15-assume-judge-design.md`）。

**为什么要有审核者。** 生益科技第三次调用（2026-10-15）：第二次调用已经给出
完整的公式化分解式，第三次调用却被代码闸门连挂三次、整个因子分解退回两标量。
三次的判定者全部是代码（`assume.check` 的 49 处 `AssumeFailed`），且三次都与
分解式内容无关：平衡点单位错被偏离闸门放大、两个 pp 因子加总后倒序、代码自己
的「第 7 字段」前后矛盾。用户决定：**判断项合格与否全部由 AI 判定，代码不做
任何驳回** —— 代码只读数、计算、核对引文，把结果作为事实交给审核者。

**流程照搬子项目二**（`methodology_review`）：填写者交稿 → 代码读数与计算（不驳回）
→ 审核者按清单逐条作答（四级结论，致命必附建议，可附「补充」）→ 有致命即带着建议
退回重填，最多 3 轮 → 仍不通过停下交人工，**不再退回两标量模式**。

**原先门③ 的两道 AI 审核并进来**：`factor_review` [对账] 由 [合成] 承接；判断项
假设辩论由 [后果]、[依据] 与「补充」承接，审核者因此拿到完整账本事实目录。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

from ir_agent import methodology_review as MR
from ir_agent.methodology_review import (LEVELS, ReviewIncomplete, ReviewResult,
                                         Verdict)

KINDS = ("取值", "平衡点", "依据", "合成", "后果")
MAX_ROUNDS = 3              # 填写者最多写 3 轮（审核驳回才算一轮）
JUDGE_TRIES = MR.JUDGE_TRIES  # 审核者作答 1 次 + 漏答补答 2 次，不消耗填写者轮数

# 「补充：<结论>　<问题>　建议：…」—— 清单之外发现的问题（并掉假设辩论后的出口）。
_SUPP = re.compile(r"^[ \t]*[-*•·]*[ \t]*\**补充\**[ \t]*[:：]\s*(.+)$")
_SUGGEST = re.compile(r"\s*建议\s*[:：]\s*")


class ReviewStop(MR.ReviewStop):
    """停下交人工。`last` 是最后一稿（读得出来的话），供人以它为底稿改。"""

    def __init__(self, msg: str, rounds: list, last=None):
        super().__init__(msg, rounds)
        self.last = last


@dataclass
class Round:
    """一轮的完整记录：填写者原文、代码事实、审核者每次作答与逐条结论。"""

    n: int
    raw: str
    facts: str = ""
    review: ReviewResult | None = None
    incomplete: str = ""
    attempts: list = field(default_factory=list)   # [(审核者原文, 不完整原因)]


def _subject(f) -> str:
    short = getattr(f, "short", "") or ""
    return f"{short}（{f.label}）" if short and short != f.label else f.label


def checklist(a, balances=()) -> list[tuple[str, str]]:
    """代码生成「必须作答的条目」（spec 二）。两标量模式只有 [依据]、[合成]、[后果]。"""
    m = a._m()
    if m is None:
        items = [("依据", "营收增速"), ("依据", "毛利率")]
    else:
        mids = {b.subject for b in balances or ()
                if getattr(b, "mid", None) is not None}
        items = [("取值", _subject(f)) for f in m.factors]
        items += [("平衡点", _subject(f)) for f in m.factors if f.label in mids]
        items += [("依据", _subject(f)) for f in m.factors]
    items += [("合成", "营收"), ("合成", "毛利率"), ("后果", "营收路径")]
    return items


def parse(raw: str, expected: list[tuple[str, str]]) -> list[Verdict]:
    """解析审核者的回答：清单条目（复用 `methodology_review.parse`）+ 选填的「补充」。

    补充行先从原文里摘掉再解析清单 —— 否则它会被并进上一条条目的理由里。补充判
    致命同样要附建议，没写算漏答。"""
    lines = raw.splitlines()
    supp = [_SUPP.match(x) for x in lines]
    body = "\n".join(x for x, m in zip(lines, supp) if not m)
    verdicts = MR.parse(body, expected, kinds=KINDS)
    extras: list[Verdict] = []
    for i, m in enumerate((m for m in supp if m), 1):
        tail = m.group(1).strip().lstrip("*")
        level = next((lv for lv in sorted(LEVELS, key=len, reverse=True)
                      if tail.startswith(lv)), None)
        if level is None:
            continue
        why, sug = (_SUGGEST.split(tail[len(level):], 1) + [""])[:2]
        why, sug = (" ".join(x.strip(" 　\t*：:").split()) for x in (why, sug))
        extras.append(Verdict("补充", f"第 {i} 条", level, why, suggest=sug))
    bad = [v for v in extras if v.level == "致命" and not v.suggest]
    if bad:
        raise ReviewIncomplete("以下补充判为致命却没给修改建议（理由之后写「建议：…」）："
                               + "、".join(v.subject for v in bad))
    return verdicts + extras


RULES = """你是研究总监，正在审核一份 **DCF 判断项**（营收与毛利率驱动因子的逐年三档
取值）能不能交给建模。你不直接改数，而是逐项给结论、理由与修改建议 —— 有一项
「致命」，填写者就要按你的建议重填。代码不做任何判定：它只把读到的值、算出的
路径、引文核验的结果作为事实交给你，要不要紧由你判断。

**四级结论**（只能用这四个词）：
- **通过**：逻辑完全正确，资料能够对得上；
- **提醒**：逻辑部分正确，资料对得上，但结论较为牵强；
- **致命**：逻辑完全无法支撑结论；
- **待采集**：逻辑正确，资料尚未采集到。

提醒、致命、待采集**必须写理由**。凡涉及资料的判断，引原文「…」或账本占位符
[[key@期间]] —— 代码会核对你的引文能否在资料、账本里找到。

**每类要判什么**（下面用「类别名：」说明，作答时才写成 [类别]）：
- 取值：代码读到的值是不是填写者的本意、单位是否与因子声明一致 —— pp 是百分点
  数字本身（5.47 个百分点写 5.47），变化率是百分比；对照理由栏看（理由写
  「5.47 个百分点」、表里却是 0.0547，就是缩小了 100 倍）。三档方向与间距（侵蚀类
  因子悲观应更负）；逐年路径是否讲得通；公式用 `基期(·)` 时填的应是相对基期的
  累计值，用 `上期(·)` 时是逐年变化。
- 平衡点：辩论② 的平衡点是**固定输入**（已在门② 给人审过），填写者改不了。判的
  是填写者的中性档与平衡点的关系、偏离的理由是否成立。平衡点本身的单位可疑时
  （pp 因子写成小数是常见错误），在理由里写出换算后的值，并按换算后的值判断填写者
  是否对齐 —— 不要因为平衡点自身的错误判致命，也不要建议填写者重写平衡点。
- 依据：依据能否支撑取值（代码给了引文核验结果；核验不过不等于捏造，可能是
  概括）；「无数据（估计）」是否属实、「同上」是否合理；理由与取值是否自洽；是否
  回应了方法论审核遗留的提醒与待采集。
- 合成：代码按公式合成的三档营收增速与毛利率是否拉开、是否单调；合成路径与理由
  栏引用的参照（行业增速、历史 CAGR、同业水平）是否自洽；与半年报锚定的首年是否
  衔接。有半年报锚定时（见代码事实【半年报锚定】），第 2 年起的毛利率已含基期平移，
  手算时要加上平移量 —— 那是代码的处理，不要当成引擎错误。
- 后果：5 年累计倍数、隐含市占率，放到行业格局、产能、CR5、周转与杠杆（账本事实
  目录里有）下看是否可能。

**每条一行，格式严格如下**（结论、理由、建议之间用全角空格）：

[类别] <条目>：<通过|提醒|致命|待采集>　<理由>　建议：<具体怎么改>

**判为致命的条目必须写「建议：…」** —— 具体怎么改：哪个因子哪一年改成多少、单位
怎么换算、哪条理由要改。提醒、待采集有建议也写上；通过不必写。条目名照上面清单
原样抄，后面不要加公式或别的内容。

清单之外若发现问题（例如账本事实与理由栏的叙事相反），另起一行写：

补充：<致命|提醒>　<问题>　建议：<怎么改>

补充可以不写；补充判致命同样会退回重填。
"""


def facts_for_judge(a, *, balances=(), ledger=None, as_of=None, anchor=None) -> str:
    """只给审核者看的、代码现算的事实（spec 二）。"""
    from ir_agent import assume

    lines = ["【每格原文与代码读数】"]
    m = a._m()
    label = ({f.key: (f.label, f.unit) for f in m.factors} if m is not None else {})
    for r in sorted(a.rows, key=lambda x: (x.factor_key or x.driver, x.year)):
        name, unit = label.get(r.factor_key, ({"growth": "营收增速", "gm": "毛利率"}
                                              .get(r.driver, r.driver), ""))
        read = " / ".join(assume._fmt_factor(v, unit) if unit else f"{v:.2%}"
                          for v in (r.bear, r.base, r.bull))
        raw = " / ".join(r.raw) if r.raw else "（无原文）"
        lines.append(f"- {name} 第{r.year}年：原文 {raw} → 读作 {read}"
                     + (f"（单位 {unit}）" if unit else ""))
    facts = getattr(a, "facts", None)
    if m is not None and facts is not None and not facts.blocking:
        lines += ["", "【代码按公式合成的三档】"]
        try:
            got = {c: assume.synthesize(a, c, a.base_margin
                                        if a.base_margin is not None else Decimal(0),
                                        a.margin_bases)
                   for c in (assume.Case.BEAR, assume.Case.BASE, assume.Case.BULL)}
            for i, cn in ((0, "营收增速"), (1, "毛利率")):
                for y in range(len(got[assume.Case.BASE][i])):
                    v = " / ".join(f"{got[c][i][y]:.2%}" for c in got)
                    lines.append(f"- 第{y + 1}年 {cn} {v}（悲观 / 中性 / 乐观）")
        except assume.AssumeFailed as e:
            lines.append(f"- 算不出来：{e}")
    if anchor:
        lines += _anchor_lines(a, anchor)
    if a.implied is not None:
        lines += ["", f"【算术后果】{a.implied.describe()}"]
    bal = [b for b in balances or () if getattr(b, "mid", None) is not None]
    if bal:
        lines += ["", "【辩论② 多空平衡点】"]
        lines += [f"- {b.subject}：悲观 {b.bear} / 平衡 {b.mid} / 乐观 {b.bull}"
                  f"（{b.note}）" for b in bal]
    if a.method_review:
        lines += ["", "【方法论审核遗留】"] + list(a.method_review)
    if m is not None and ledger is not None:
        decl = MR.ledger_decl_lines(m, ledger=ledger, as_of=as_of)
        if decl:
            lines += [""] + decl
    lines += ["", "【代码读数与核对】", facts.text() if facts else "（未核对）"]
    return "\n".join(lines)


def _anchor_lines(a, anchor: dict) -> list[str]:
    """半年报锚定是代码的处理，填写者改不了 —— 不告诉审核者，它用「年报基期 + 累计
    pp」手算第 2 年起的毛利率，怎么都对不上（生益科技 2026-10-17 第 1–3 轮）。"""
    from ir_agent import assume

    y1 = assume.anchor_year1(anchor)
    order = (("bear", "悲观"), ("base", "中性"), ("bull", "乐观"))
    g = " / ".join(f"{cn} {y1[k][0]:.2%}" for k, cn in order)
    m = " / ".join(f"{cn} {y1[k][1]:.2%}" for k, cn in order)
    lines = ["", "【半年报锚定】（代码的处理，填写者改不了）",
             f"- 首个预测年由已披露中期锚定，建模时第 1 年用这组值替换合成表的第 1 年：营收增速 "
             f"{g}；毛利率 {m}"]
    mb = a.margin_bases or {}
    if all(len(mb.get(k) or []) > 1 for k, _ in order):
        sh = "、".join(f"{cn} {(mb[k][1] - mb[k][0]) * 100:+.2f}pp" for k, cn in order)
        lines.append("- 毛利率公式用 基期(毛利率)，第 2 年起各档的基期平移为「年报基期 + "
                     f"(锚定值 − 第 1 年公式值)」：{sh} —— 合成表第 2 年起已含这个平移，"
                     "用「年报基期 + 累计 pp」手算时要加上它。")
    return lines


def review(judge, *, draft: str, materials: str, facts: str, catalog: str,
           expected: list, hay: str = "", ledger=None, as_of=None) -> ReviewResult:
    """审核者作答一次（漏答补答最多 2 次，不消耗填写者轮数）。"""
    items = "\n".join(f"- [{k}] {s}" for k, s in expected)
    base = (f"{RULES}\n**必须逐条作答的条目**\n{items}\n\n"
            f"**填写者交的判断项（原文）**\n{draft}\n\n"
            f"**只给你的、代码算出的事实**\n{facts}\n\n"
            f"**账本事实目录**（可引用的占位符）\n{catalog or '（无）'}\n\n"
            f"**填写者看到的资料**\n{materials}\n")
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
        return ReviewResult(verdicts=MR.annotate_quotes(verdicts, hay=hay,
                                                        ledger=ledger, as_of=as_of),
                            attempts=attempts)
    err = ReviewIncomplete(last)
    err.attempts = attempts
    raise err


def fingerprint(a) -> str:
    """判断项取值的指纹：只算因子、年、三档与基期值，不算理由与依据文字 —— 人在
    门③ 改一个错别字不该让审核失效，改一个数必须看得出来（spec 三）。"""
    rows = sorted((r.factor_key or r.driver, r.year, str(r.bear), str(r.base),
                   str(r.bull)) for r in a.rows)
    bases = sorted((k, str(v.value)) for k, v in a.base_values.items())
    blob = json.dumps([rows, bases], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def dump_review(res: ReviewResult, *, passed: bool) -> str:
    return json.dumps({**res.to_json(), "passed": passed}, ensure_ascii=False, indent=2)


def review_markdown(res: ReviewResult) -> list[str]:
    """assumptions.md「判断项审核」一节：第几轮通过；提醒、待采集逐条列（带建议）；
    通过只给条数。"""
    c = res.counts()
    lines = ["⚖️ **判断项审核**（AI 审核者，代码未做任何判定）：", "",
             f"第 {res.rounds} 轮通过：通过 {c['通过']} 条、提醒 {c['提醒']} 条、"
             f"待采集 {c['待采集']} 条。", ""]
    lines += [f"- {v.line()}" for v in res.flagged()]
    if res.flagged():
        lines.append("")
    return lines


def rounds_markdown(rounds: list[Round]) -> str:
    """`assumptions_rounds.md`：每一轮的完整记录。"""
    lines = ["# 判断项审核：各轮记录", ""]
    for r in rounds:
        lines += [f"## 第 {r.n} 轮", "", "### 填写者原文", "", *MR._fenced(r.raw), ""]
        if r.facts:
            lines += ["### 代码事实（只是事实，不驳回）", "", r.facts, ""]
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
            lines += [f"### 审核者原文（第 {i} 次作答）", "", *MR._fenced(raw), ""]
            if problem:
                lines += [f"→ 不完整：{problem}", ""]
    return "\n".join(lines)


def fill_and_review(filler, judge, *, judg: dict, labels: list[str], context: str,
                    ledger, body: str, as_of, code: str = "", decomps=None,
                    factor_data: dict | None = None, base_margin=None,
                    anchor: dict | None = None, balances=None, catalog: str = "",
                    method_review=(), max_rounds: int = MAX_ROUNDS):
    """填写 → 代码读数与计算（不驳回）→ 审核 → 汇总。返回 (AssumptionSet, ReviewResult,
    rounds)。

    有致命即带着建议退回重填，合计最多 `max_rounds` 轮；审核者答不全、3 轮仍有致命、
    或审核通过但代码确实算不下去 → `ReviewStop`（停下交人工）。模型接口错误原样
    抛出，由调用方说明是接口问题。**不退回两标量模式**：两标量只在取不到行业分类
    （`decomps` 为空）时出现，审核照跑。
    """
    from ir_agent import assume
    from ir_agent.formula import as_model

    model = as_model(decomps)
    ledger_bases = {}
    if model is not None and not model.legacy:
        probe = assume.AssumptionSet(code="", labels=[], model=model)
        assume._fill_from_ledger(probe, ledger, as_of)
        ledger_bases = probe.base_values
    prompt = assume.build_prompt(judg, labels, context, brief=body, decomps=decomps,
                                 factor_data=factor_data, ledger_bases=ledger_bases)
    materials = prompt
    hay = "\n".join([body, *[getattr(fd, "source_excerpt", "") or ""
                             for fd in (factor_data or {}).values()]])
    rounds: list[Round] = []
    a = None
    for n in range(1, max_rounds + 1):
        raw = filler(prompt)
        a = assume.parse(raw, len(labels), decomps=decomps,
                         factor_data=factor_data, as_of=as_of)
        a.code, a.labels = code, labels
        a.factor_data = dict(factor_data or {})
        a.base_margin = base_margin
        a.method_review = list(method_review or [])
        facts = assume.check(a, len(labels), ledger=ledger, body=body, as_of=as_of,
                             decomps=decomps, base_margin=base_margin, anchor=anchor,
                             balances=balances)
        a.decomps = list(decomps) if isinstance(decomps, list) else []
        a.model = model
        if model is not None and not facts.blocking:
            a.residuals = assume._reconcile_model(model, ledger, as_of)
        ftxt = facts_for_judge(a, balances=balances, ledger=ledger, as_of=as_of,
                               anchor=anchor)
        try:
            res = review(judge, draft=raw, materials=materials, facts=ftxt,
                         catalog=catalog, expected=checklist(a, balances),
                         hay=hay, ledger=ledger, as_of=as_of)
        except ReviewIncomplete as e:
            rounds.append(Round(n, raw, ftxt, incomplete=str(e),
                                attempts=getattr(e, "attempts", [])))
            raise ReviewStop(f"判断项审核未完成：审核者补答 {JUDGE_TRIES - 1} 次仍不全"
                             f"（{str(e)[:120]}）", rounds, last=a) from e
        res.rounds = n
        res.fingerprint = fingerprint(a)
        rounds.append(Round(n, raw, ftxt, review=res, attempts=res.attempts))
        if not res.fatal():
            if facts.blocking:
                raise ReviewStop("判断项审核已通过，但无法计算："
                                 + "；".join(facts.blocking), rounds, last=a)
            a.judge_review = res
            return a, res, rounds
        prompt = f"{prompt}\n\n---\n{MR._feedback(n, res)}\n"
    raise ReviewStop(f"判断项审核 {max_rounds} 轮未通过（最后一轮致命 "
                     f"{len(rounds[-1].review.fatal())} 条）", rounds, last=a)
