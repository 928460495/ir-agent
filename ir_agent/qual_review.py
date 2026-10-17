"""第一次调用三个定性模块（宏观定性、战略分析、产业链）的审核引擎（子项目四，
spec `2026-10-17-qualitative-judge-design.md`）。

**为什么要有它。** 三个模块是同一套写法、同一类代码闸门：可选词、条数、账本外
数字、依据必须逐字出自原文 —— 不过就重试，三次仍不过整节缺失。中国巨石
（600176）第一次调用：五力引了研报，连挂三次，战略分析整节空着。用户决定：
三个模块都改由审核者审核，**沿用各模块的方法论**，只改审核标准。

**流程照搬判断项环节**（`assume_review`）：撰写者交稿 → 代码只读、只核对，结果作为
事实交给审核者 → 审核者按清单逐条作答（四级结论、致命必附建议、可附「补充」）→
有致命带建议退回重写，最多 3 轮；仍不通过 / 答不全 → `ReviewStop`。各模块的读数、
事实、清单、规则由模块自己给，这里只管循环。
"""

from __future__ import annotations

import hashlib
import json
import re

from ir_agent import assume_review as AR
from ir_agent import methodology_review as MR
from ir_agent.basis import Basis, InvalidBasis
from ir_agent.methodology_review import ReviewIncomplete, ReviewResult

ReviewStop = AR.ReviewStop
Round = AR.Round
MAX_ROUNDS = 3
JUDGE_TRIES = MR.JUDGE_TRIES

_REF = re.compile(r"\[\[[^\]]+\]\]")
_QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)
_TITLE = re.compile(r"《([^》]{2,})》")
_PARAPHRASE = re.compile(r"转述|综合")

# 撰写者提示词里统一的依据写法（三个模块共用，用户决定 3）。
BASIS_RULES = """**依据的三种写法**（任选其一，写在每行最后一段）：
- 账本占位符：`[[key@期间]]`（只能用资料里给出的）；
- 原文引用：`「逐字原文」——出处`，年报、公告、研报、外部文档都可以；能逐字引就逐字引；
- 转述/综合：`转述：综合 机构《文章标题》、机构《文章标题》`—— 写明采纳了哪几篇文章的
  观点，标题照资料里的写，由人复核。
代码会核对引文能否在资料里找到、转述指明的文章是否在资料里，结果交给审核者看。"""

REVIEW_NOTE = """**你写的稿子由审核者逐项审核**（代码不做判定，只把读到的内容与依据核对
结果交给审核者）。有致命项，会带着修改建议退回给你重写，最多 3 轮 —— 被退回时按
建议改，并重新输出全部行。"""


_LEVELS_TEXT = """**四级结论**（只能用这四个词）：
- **通过**：逻辑完全正确，资料能够对得上；
- **提醒**：逻辑部分正确，资料对得上，但结论较为牵强；
- **致命**：逻辑完全无法支撑结论；
- **待采集**：逻辑正确，资料尚未采集到。"""


def make_rules(subject: str, what_to_judge: str) -> str:
    """审核规则提示词：各模块只写「每类判什么」，其余三个模块共用（四级定义、依据、
    账本外数字、作答格式、补充）。说明一律用「类别名：」，不写成 `- [类别]` —— 那会被
    当成要作答的条目（子项目二的教训）。"""
    return f"""你是研究总监，正在审核一份**{subject}**稿子能不能用。你不直接改写，而是逐项
给结论、理由与修改建议 —— 有一项「致命」，撰写者就要按你的建议重写。代码不做判定：它
只把读到的内容、依据核对结果、描述里的账本外数字作为事实交给你，要不要紧由你判断。

{_LEVELS_TEXT}

提醒、致命、待采集**必须写理由**。凡涉及资料的判断，引原文「…」或账本占位符
[[key@期间]] —— 代码会核对你的引文能否在资料、账本里找到。

**每类要判什么**（下面用「类别名：」说明，作答时才写成 [类别]）：
{what_to_judge}

**依据**：三种写法都合法 —— 账本占位符、原文引用（年报、公告、研报、外部文档）、写明
采纳了哪几篇文章的转述。代码给了核对结果：引文找不到不等于捏造（可能是概括），转述指向
的文章不在资料里要当心。来源分级（如财经媒体「仅作线索，不得作为依据」）由你判断。

**账本外数字**：描述会原样进入后两次调用（辩论、简报、研报撰写），那里的数字必须可
溯源。代码列出了描述里的账本外数字 —— 撰写者指明了出处的判提醒（建议改成不带数字的
说法或换成占位符）；没出处、又支撑了关键结论的判致命。

**每条一行，格式严格如下**（结论、理由、建议之间用全角空格）：

[类别] <条目>：<通过|提醒|致命|待采集>　<理由>　建议：<具体怎么改>

**判为致命的条目必须写「建议：…」** —— 具体怎么改。提醒、待采集有建议也写上；通过
不必写。条目名照上面清单原样抄，后面不要加别的内容。

清单之外若发现问题，另起一行写：

补充：<致命|提醒>　<问题>　建议：<怎么改>

补充可以不写；补充判致命同样会退回重写。
"""


def review_tail(res) -> list[str]:
    """审核的提醒、待采集附在模块 summary 末尾（spec 六）—— 辩论、简报读得到。"""
    if res is None or not res.flagged():
        return []
    return ["  审核遗留（审核者判为提醒/待采集）："] + [f"    · {v.line()}"
                                                     for v in res.flagged()]


_NOISE = re.compile(r"[\s\W_]|关于|的")
_NEAR = 0.6       # 相似度达到它才写「最接近的是」；再低就是不相干的文章，写了反而误导


def _norm(title: str) -> str:
    """去掉标点、空白与「关于」「的」：公告原标题常带重复的「关于」、千分位逗号，撰写者
    转述时照抄不全（中国巨石 2026-10-17 试跑：差一个「关于」就被报成不在语料库）。"""
    return _NOISE.sub("", title or "")


def _titles(corpus) -> list[str]:
    return [d.title or "" for d in (getattr(corpus, "docs", None) or [])]


def _in_corpus(title: str, corpus) -> bool:
    t = _norm(title)
    return len(t) >= 4 and any(t in _norm(d) for d in _titles(corpus))


def _nearest(title: str, corpus) -> str:
    """语料库里与之最接近的标题（相似度不足 `_NEAR` 给空串），交审核者判断是不是同一篇。"""
    from difflib import SequenceMatcher
    t = _norm(title)
    best = max(((SequenceMatcher(None, t, _norm(d)).ratio(), d) for d in _titles(corpus)),
               default=(0.0, ""))
    return best[1] if best[0] >= _NEAR else ""


def basis(raw: str, corpus=None) -> tuple[list[Basis], str, list[str]]:
    """读一条依据。返回 (依据, 问题, 转述指向的文章标题)。**不驳回**：问题只作事实。

    - 占位符 → `Basis.fact`；
    - 写了「转述/综合」，或只给了《标题》没有引文 → 转述：记下标题、核对是否在语料库；
    - 原文引用 → `Basis.report`（出处由 `attribute` 补）；
    - 什么都没有 → 问题「没有给出依据」。
    """
    raw = (raw or "").strip()
    ref = _REF.search(raw)
    if ref:
        try:
            return [Basis.fact(ref.group(0))], "", []
        except InvalidBasis as e:
            return [], f"占位符不合法：{e}", []
    quote = _QUOTE.search(raw)
    # 转述标记与文章标题只在引号之外找：「综合毛利率」等年报常见词出现在引文里不算转述。
    outside = _QUOTE.sub("", raw)
    if _PARAPHRASE.search(outside) or (quote is None and _TITLE.search(outside)):
        titles = _TITLE.findall(outside)
        if not titles:
            return [], "写了转述/综合，但没有写明采纳了哪几篇文章（《标题》）", []
        miss = [t for t in titles if not _in_corpus(t, corpus)]
        prob = ("转述指向的文章不在语料库里：" + "、".join(
            f"《{t}》" + (f"（语料库里最接近的是《{n}》）" if (n := _nearest(t, corpus)) else "")
            for t in miss) if miss else "")
        return [], prob, titles
    if quote is not None:
        try:
            return [Basis.report(quote.group(1))], "", []
        except InvalidBasis as e:
            return [], f"依据不合法：{e}", []
    return [], "没有给出依据（占位符、原文引用或写明出处的转述）", []


def fingerprint(raw: str) -> str:
    """撰写者原文的指纹（去掉空白）。重跑读回时比对，变了 = 人改过（用户决定 12）。"""
    return hashlib.sha256(re.sub(r"\s+", "", raw or "").encode("utf-8")).hexdigest()[:16]


def corpus_fingerprint(corpus) -> str:
    """审核时语料库的指纹（文档 id 集合）。沿用时资料可能已变（spec 五），便于对照。"""
    ids = sorted(getattr(d, "doc_id", "") for d in (getattr(corpus, "docs", None) or []))
    if not ids:
        return ""
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:16]


def dump(res: ReviewResult, *, passed: bool, corpus_fp: str = "") -> str:
    return json.dumps({**res.to_json(), "passed": passed, "corpus": corpus_fp},
                      ensure_ascii=False, indent=2)


def review(judge, *, rules: str, kinds: tuple, expected: list, draft: str,
           materials: str, facts: str, catalog: str = "", hay: str = "",
           ledger=None, as_of=None) -> ReviewResult:
    """审核者作答一次（漏答补答最多 2 次，不消耗撰写者轮数）。"""
    items = "\n".join(f"- [{k}] {s}" for k, s in expected)
    base = (f"{rules}\n**必须逐条作答的条目**\n{items}\n\n"
            f"**撰写者交的稿子（原文）**\n{draft}\n\n"
            f"**只给你的、代码核对出的事实**\n{facts}\n\n"
            f"**账本事实目录**（可引用的占位符）\n{catalog or '（无）'}\n\n"
            f"**撰写者看到的资料**\n{materials}\n")
    prompt, last, attempts = base, "", []
    for _ in range(JUDGE_TRIES):
        raw = judge(prompt)
        try:
            verdicts = AR.parse(raw, expected, kinds=kinds)
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


def run(writer, judge, *, name: str, prompt: str, materials: str, read, facts,
        checklist, kinds: tuple, rules: str, catalog: str = "", hay: str = "",
        ledger=None, as_of=None, max_rounds: int = MAX_ROUNDS):
    """撰写 → 代码读与核对（不驳回）→ 审核 → 汇总。返回 (view, ReviewResult, rounds)。

    `read(raw) -> view`（`view.read_notes` 是读数事实）；`facts(view) -> list[str]`；
    `checklist(view) -> [(类别, 条目)]`。有致命即带建议退回重写，最多 `max_rounds` 轮；
    仍不通过或审核者答不全 → `ReviewStop`（`last` 为最后一稿原文）。模型接口错误原样
    抛出，由调用方按「接口问题」停下（用户决定 9）。
    """
    rounds: list = []
    raw = ""
    if catalog:
        # 撰写者与审核者看同一份账本目录（中国巨石 2026-10-17 试跑：审核者以撰写者从没
        # 见过的 [[company.market_share]] 判致命，三轮都卡在这里）。
        prompt = (f"{prompt}\n\n**账本事实目录**（可引用的占位符 [[key@期间]]，与审核者"
                  f"看到的是同一份；用得上的就引用）\n{catalog}\n")
    for n in range(1, max_rounds + 1):
        raw = writer(prompt)
        view = read(raw)
        notes = list(getattr(view, "read_notes", []) or []) + list(facts(view))
        ftxt = "\n".join(f"- {x}" for x in notes) or "（代码读数与核对没有发现问题）"
        try:
            res = review(judge, rules=rules, kinds=kinds, expected=checklist(view),
                         draft=raw, materials=materials, facts=ftxt, catalog=catalog,
                         hay=hay, ledger=ledger, as_of=as_of)
        except ReviewIncomplete as e:
            rounds.append(Round(n, raw, ftxt, incomplete=str(e),
                                attempts=getattr(e, "attempts", [])))
            raise ReviewStop(f"{name}审核未完成：审核者补答 {JUDGE_TRIES - 1} 次仍不全"
                             f"（{str(e)[:120]}）", rounds, last=raw) from e
        res.rounds = n
        res.fingerprint = fingerprint(raw)
        rounds.append(Round(n, raw, ftxt, review=res, attempts=res.attempts))
        if not res.fatal():
            return view, res, rounds
        prompt = f"{prompt}\n\n---\n{MR._feedback(n, res)}\n"
    raise ReviewStop(f"{name}审核 {max_rounds} 轮未通过（最后一轮致命 "
                     f"{len(rounds[-1].review.fatal())} 条）", rounds, last=raw)
