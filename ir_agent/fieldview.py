"""三份定性稿（宏观、战略、产业链）的原地排版：字段行 → 小节，审核意见挂到对应条目。

spec 2026-11-25-readable-layout §4.4。源文件一行一条、用竖线分段，是给程序读回的格式；这里把它排成人读
的样子，**不改源文件**。历史 66 份稿里 41% 的行不是字段行（小标题、补充说明、与账本的核对）——
它们留在原位，跟在所说明的条目下面；认不出的写法按原顺序成段，不猜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ir_agent import mdview
from ir_agent.labels import driver_label
from ir_agent.lineparse import keyed, pick

TITLES = {"macro_view": "宏观定性", "strategy": "战略分析", "value_chain": "产业链"}
REVIEW_HEAD = "审核意见"
LONG, CHUNK = 220, 160            # 超过 LONG 字的段按句拆成每段约 CHUNK 字

_REVIEW_KIND = {"取向": "政策取向", "规模": "行业规模"}


@dataclass
class _Spec:
    """一种字段行：各段的名字；标题取哪几段；标题之后合成一行的短段。"""
    labels: tuple[str, ...]
    name: tuple[int, ...]
    verdict: tuple[int, ...] = ()
    meta: tuple[int, ...] = ()
    fmt: str = "{name}"


_NINE = _Spec(("渠道", "事件", "传导路径", "落点", "影响", "强度", "时滞", "新增或延续", "依据"), (1,), (4,), (0, 3, 6, 7))
_OLD4 = _Spec(("事件", "传导路径", "影响", "依据"), (0,), (2,))
_SPECS: dict[str, _Spec] = {
    "政策取向": _Spec(("取向", "理由"), (), (0,), fmt="政策取向"),
    "传导": _OLD4,
    "跟踪": _Spec(("指标", "对应传导", "证伪读数"), (0,)),
    "排除": _Spec(("渠道", "理由"), (0,), fmt="排除：{name}"),
    "护城河": _Spec(("类型", "宽度", "依据"), (0,), (1,)),
    "五力": _Spec(("力", "强度", "对本公司", "依据"), (0,), (1, 2)),
    "不确定性": _Spec(("描述", "驱动因子", "不利时", "依据"), (0,), (1,)),
    "公告": _Spec(("标题", "处理", "说明"), (0,), (1,)),
    "环节": _Spec(("位置", "名称", "说明", "依据"), (0, 1)),
    "议价": _Spec(("位置", "强弱"), (0,), (1,), fmt="对{name}议价"),
    "行业规模": _Spec(("口径", "数值", "单位", "期间", "依据"), (), (1, 2, 3), fmt="行业规模"),
}
_FIELDS = {"macro_view": ("政策取向", "传导", "跟踪", "排除"),
           "strategy": ("护城河", "五力", "不确定性", "公告"),
           "value_chain": ("环节", "议价", "行业规模")}
_GROUP = {"传导": "传导路径", "跟踪": "跟踪指标", "排除": "排除的渠道", "不确定性": "关键不确定性",
          "公告": "重大公告回应", "五力": "波特五力", "环节": "产业链环节", "议价": "议价能力"}
_CH = ("利率", "信贷", "资产价格与财富", "汇率", "预期与信心", "财政", "监管与产业政策", "大宗商品与成本", "外需与贸易")

# 原文 + 出处：「…」——某机构《题》（日期）
_CITE = re.compile(r"[「“]([^」”]+)[」”]\s*(?:——|—|--)\s*([^；;。「“\n]*?(?:《[^》]*》)?(?:[（(][^（）()]*[）)])?)(?=[；;。\s]*(?:[「“]|$)|[；;。])")


@dataclass
class _Section:
    kind: str
    cells: list[str]
    name: str
    verdict: str
    lines: list[str] = field(default_factory=list)
    reviews: list = field(default_factory=list)


def _cells(body: str) -> list[str]:
    """按竖线分段；占位符 `[[…]]` 里不含竖线。"""
    return [p.strip() for p in body.split("|")]


def _spec_of(kind: str, p: list[str]) -> _Spec:
    if kind == "传导":
        if len(p) >= 9:
            return _NINE
        if len(p) >= 5 and pick(p[0], list(_CH)) is not None:        # 九段漏字段：不猜各段是什么
            return _Spec(("",) * len(p), (1,))
    return _SPECS[kind]


def _chunks(text: str) -> list[str]:
    """长段按句拆开。"""
    if len(text) <= LONG:
        return [text]
    out, cur = [], ""
    for s in re.split(r"(?<=[。！？；])", text):
        if cur and len(cur) + len(s) > CHUNK:
            out.append(cur)
            cur = ""
        cur += s
    return [x for x in [*out, cur] if x.strip()]


def _prose(text: str, label: str = "") -> list[str]:
    """一段文字 → 段落与引用块。`「…」——出处` 独立成引用块；正文里的「强」「宽」不动。"""
    out: list[str] = []
    lead = f"**{label}**：" if label else ""

    def para(s: str) -> None:
        nonlocal lead
        s = s.strip(" ；;　")
        if s in ("", "。"):
            return
        for c in _chunks(s):
            out.extend([lead + c.strip(), ""])
            lead = ""

    at = 0
    for m in _CITE.finditer(text):
        if not m.group(2).strip():
            continue
        para(text[at:m.start()])
        if lead:
            out.extend([f"{label}：", ""])              # 不能用独占一行的粗体，那会被当成小标题
            lead = ""
        out.extend([f"> 「{m.group(1)}」", f"> —— {m.group(2).strip()}", ""])
        at = m.end()
    para(text[at:])
    return out


def _section(kind: str, body: str) -> _Section:
    p = _cells(body)
    sp = _spec_of(kind, p)
    n = len(sp.labels)
    get = lambda i: p[i] if i < len(p) else ""
    tr = lambda i: driver_label(get(i)) if kind == "不确定性" and i == 1 else get(i)
    name = "：".join(x for x in (get(i) for i in sp.name) if x)
    verdict = ("，" if kind == "五力" else " ").join(x for x in (tr(i) for i in sp.verdict) if x)
    sec = _Section(kind, p, name, verdict)
    shown = sp.fmt.format(name=name) if name or "{name}" not in sp.fmt else kind
    used = set(sp.name) | set(sp.verdict)
    if kind == "不确定性" and tr(1) != get(1):
        used.discard(1)                                   # 标题用中文名，原词在下面保留
    if kind == "行业规模" and all(get(i) for i in (1, 2, 3)):
        verdict = f"{get(1)} {get(2)}（{get(3)}）"
    sec.lines = [f"### {shown}" + (f" —— {verdict}" if verdict else ""), ""]
    meta = [f"**{sp.labels[i]}**：{get(i)}" for i in sp.meta if get(i)]
    if meta:
        sec.lines += ["　".join(meta), ""]
    for i, c in enumerate(p):
        if i in used or i in sp.meta or not c:
            continue
        sec.lines += _prose(c, sp.labels[i] if i < n else "")
    return sec


def _scan(name: str, text: str) -> list:
    """逐行：字段行 → _Section，其余原样字符串。"""
    rx = {k: keyed(k) for k in _FIELDS[name]}
    out: list = []
    for ln in mdview._unwrap((text or "").replace("\r\n", "\n").split("\n")):
        raw = ln[1:] if ln.startswith("\x00") else ln
        hit = None if ln.startswith("\x00") else next(
            ((k, m) for k, r in rx.items() if (m := r.match(raw))), None)
        out.append(_section(hit[0], hit[1].group(1).strip()) if hit else raw)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s)


def _attach(secs: list[_Section], review) -> list:
    """审核意见按（类别, 名称 → 序号）挂到小节，返回对不上的。"""
    rest = []
    for v in (review.verdicts if review else []):
        kind = _REVIEW_KIND.get(v.kind, v.kind)
        pool = [s for s in secs if s.kind == kind]
        m = re.match(r"第\s*(\d+)\s*条\s*", v.subject)
        key = _norm(re.sub(r"^对(?=上游|下游)", "", v.subject[m.end():] if m else v.subject))
        same = [s for s in pool if kind in ("政策取向", "行业规模") or (
            key and (_norm(s.name).startswith(key) or (_norm(s.name) and key.startswith(_norm(s.name)))))]
        if kind == "环节":
            same = [s for s in pool if s.cells and s.cells[0].startswith(v.subject)][-1:]
        hit = same[0] if same else None
        if hit is None and m and kind != "补充" and 0 < int(m.group(1)) <= len(pool):
            hit = pool[int(m.group(1)) - 1]
        (hit.reviews if hit else rest).append(v)
    return rest


def _review_lines(v, head: str = "####") -> list[str]:
    out = [f"{head} {REVIEW_HEAD}：{v.level}", ""]
    for lab, val in (("理由", v.why), ("引文核对", f"⚠ {v.quote_note.lstrip('⚠ ')}" if v.quote_note else ""), ("建议", v.suggest)):
        if val:
            out.append(f"- {lab}：{' '.join(val.split())}")
    return [*out, ""]


def _short(s: str, n: int = 36) -> str:
    """速览表用：占位符算一个字，太长的截到 n 字（全文在下面的小节里）。"""
    if not s:
        return "—"
    out, w = "", 0
    for tok in re.findall(r"\[\[[\w.:@\-]+\]\]|.", s):
        if w >= n:
            return out + "…"
        out, w = out + tok, w + 1
    return out


def layout(name: str, text: str, review, *, note: str = "", title: str = "") -> str:
    """源文件 → 人读 Markdown。占位符原样保留，由 `readable` 随后换成数值。"""
    head = [f"# {title or TITLES[name]}", ""] + ([f"> {note}", ""] if note else [])
    if not (text or "").strip():
        return "\n".join([*head, "本轮未生成。"]) + "\n"
    items = _scan(name, text)
    secs = [x for x in items if isinstance(x, _Section)]
    rest = _attach(secs, review)
    if any(s.verdict for s in secs):
        head += ["## 结论速览", "", "| 类别 | 名称 | 结论 |", "| --- | --- | --- |"]
        head += [f"| {s.kind} | {_short(s.name)} | {_short(s.verdict)} |" for s in secs if s.verdict]
        head += [""]
    body: list[str] = []
    group, headed = "", False
    for x in items:
        if isinstance(x, str):
            s = x.strip()
            if mdview.HEADING_RX.match(s) or mdview._BOLD_LINE.match(s) or mdview._BRACKET_LINE.match(s):
                headed = True
            body.append(x)
            continue
        if x.kind != group and not headed:
            body += ["", f"## {_GROUP.get(x.kind, x.kind)}", ""]
        group, headed = x.kind, False
        body += ["", *x.lines]
        for v in x.reviews:
            body += _review_lines(v)
    if rest:
        body += ["", f"## 其他{REVIEW_HEAD}", ""]
        for v in rest:
            body += [f"### {v.kind}：{v.subject}", "", *_review_lines(v)]
    return mdview.normalize("\n".join([*head, *body]))


# ── 一字不丢（spec V1）────────────────────────────────────────

_REF = re.compile(r"\[\[[\w.:@\-]+\]\]")          # 审核意见里有截断的「[[macro.cpi」，不能跨行去找收尾


def squash(s: str) -> str:
    """只留文字：去掉占位符、Markdown 符号、空白与标点。"""
    return re.sub(r"[\s\W_]+", "", _REF.sub("", s))


def segments(text: str) -> list[list[str]]:
    """每行切成文字段（按竖线与引号），空行略。返回 [[段, …], …]。"""
    out = []
    for ln in (text or "").split("\n"):
        segs = [squash(x) for x in re.split(r"[|「」“”`]|\[\[[\w.:@\-]+\]\]", ln)]
        segs = [x for x in segs if x]
        if segs:
            out.append(segs)
    return out


_FINE = re.compile(r"[|「」“”`，。；：！？、（）()\[\]【】《》⚠—…*#>\s]+")
# 排版时换了写法、不要求逐字出现的词：取得方式译成中文，表头改名，「同上」随整列合并
REWORDED = frozenset({"computed", "reported", "字段期间", "同上"})


def lost_fine(src: str, out: str) -> list[str]:
    """通用层用的核对：按标点切成小段（审核意见会被拆成「理由 / 建议」几行，整行对不上不算丢）。"""
    o = squash(out)
    return [s for x in _FINE.split(_REF.sub("|", src or "")) if len(s := squash(x)) >= 2 and s not in o and s not in REWORDED]


def _strip_keys(segs: list[str]) -> list[str]:
    """行首的字段名在排版里变成标题，不要求逐字出现。"""
    keys = [k for ks in _FIELDS.values() for k in ks]
    first = segs[0]
    for k in keys:
        m = re.match(rf"^\d*{k}", first)
        if m:
            first = first[m.end():]
            break
    return [x for x in [first, *segs[1:]] if x]


def lost(src: str, out: str) -> list[str]:
    """源文件里有、排版结果里没有的文字段。"""
    o = squash(out)
    return [s for segs in segments(src) for s in _strip_keys(segs) if s not in o]


def order_broken(src: str, out: str) -> list[str]:
    """行与行的先后是否保持：每行最长的一段在正文（速览表之后）里的位置须不减。"""
    cut = out.find("\n### ") if "## 结论速览" in out else 0
    o, at, bad = squash(out[max(cut, 0):]), 0, []
    head = squash(out[:max(cut, 0)])
    for segs in segments(src):
        s = max(_strip_keys(segs) or [""], key=len)
        if len(s) < 4:
            continue
        i = o.find(s, at)
        if i < 0 and s not in head:
            bad.append(s)
        elif i >= 0:
            at = i
    return bad
