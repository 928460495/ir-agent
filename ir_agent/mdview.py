"""人读文件的排版：任何 Markdown → 规整 Markdown（`normalize`）→ HTML（`to_html`）。

spec 2026-11-25-readable-layout。模型的输出是「大体按格式、夹带自由发挥」：小标题至少三种写法，
一行一条却不空行（渲染后粘成一段），还有把每行包在反引号或代码围栏里的。这一层**不认识任何字段、
公司或行业**，只管分块与空行；认不出的就是普通段落，原样显示。

**一字不丢优先于好看。** 语法残缺（没闭合的 `**`、列数不齐的表格、没收尾的围栏）一律按普通文本处理。
"""

from __future__ import annotations

import html as _html
import re

LEVELS = ("通过", "提醒", "致命", "待采集")
# 审核意见的一行（`Verdict.line()` 的写法）：[类别] 条目：级别　理由（⚠ …）　建议：…
REVIEW_RX = re.compile(rf"^\s*(?:[-*•·]\s*)?\[(?P<kind>[^\]\[|]{{1,8}})\]\s*(?P<subj>.+?)[：:]\s*"
                       rf"(?P<level>{'|'.join(LEVELS)})(?:[\s　]+(?P<rest>.*))?$", re.S)
_SUGGEST = re.compile(r"[\s　]*建议\s*[:：]\s*")
_QUOTE_NOTE = re.compile(r"（(⚠[^（）]*(?:（[^（）]*）[^（）]*)*)）\s*$", re.S)

HEADING_RX = re.compile(r"^(#{1,6})\s+(\S.*)$")
_BOLD_LINE = re.compile(r"^\*\*([^*]{1,30})\*\*[:：]?$")
_BRACKET_LINE = re.compile(r"^【([^】]{1,30})】$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^\s*```(\w*)\s*$")          # 围栏行上另有文字的不算围栏，按普通行排（不丢字）
_ITEM = re.compile(r"^(\s*)(?:[-*•·]|(\d{1,2})[.、)）])\s+(.*)$")
_DOT_ITEM = re.compile(r"^(\s*)(?:[·•]|–(?!\d))\s*(\S.*)$")      # 「–5.3%」是负号不是列表
_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_FIELD_LIKE = re.compile(r"^[`*\s]*[一-鿿]{2,6}[*\s]*[:：]")
_WRAPPED = re.compile(r"^\s*`([^`]+)`\s*$")


MAX_NEST = 8                      # 列表最多嵌这么多级，再深的并到这一级


def _review(m: re.Match[str]) -> list[str]:
    rest = (m.group("rest") or "").strip()
    out = [f"- **[{m.group('kind')}] {m.group('subj').strip()}**：{m.group('level')}"]
    why, *sug = _SUGGEST.split(rest, maxsplit=1)
    note = ""
    if (q := _QUOTE_NOTE.search(why.strip())):
        note, why = q.group(1), why.strip()[:q.start()]
    for lab, val in (("理由", why), ("引文核对", note), ("建议", sug[0] if sug else "")):
        if val.strip(" 　"):
            out.append(f"  - {lab}：{' '.join(val.split())}")
    return out


def _is_sentence(s: str) -> bool:
    return s.rstrip().endswith(("。", "！", "？", "；", "."))


def _unwrap(lines: list[str]) -> list[str]:
    """去掉包住字段行的围栏与整行反引号；没收尾的围栏当普通文本；其他围栏原样。"""
    out: list[str] = []
    bare = [k for k, x in enumerate(lines) if (f := _FENCE.match(x)) and not f.group(1)]
    i = 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if m:
            while bare and bare[0] <= i:
                bare.pop(0)
            j = bare[0] if bare else None
            if j is None:                                  # 没收尾：丢掉裸围栏行，内容照排
                if m.group(1).strip():
                    out.append(lines[i])
                i += 1
                continue
            inner = lines[i + 1:j]
            body = [x for x in inner if x.strip()]
            if not m.group(1).strip() and body and all(_FIELD_LIKE.match(x) for x in body):
                out += inner
            else:
                out += ["\x00" + x for x in lines[i:j + 1]]         # 标记：代码块，原样
            i = j + 1
            continue
        w = _WRAPPED.match(lines[i])
        out.append(w.group(1) if w and _FIELD_LIKE.match(w.group(1)) else lines[i])
        i += 1
    return out


def blocks(text: str) -> list[tuple[str, list[str]]]:
    """切成 [(种类, 行)]，种类 ∈ head|list|quote|table|code|para。"""
    out: list[tuple[str, list[str]]] = []

    def put(kind: str, lines: list[str], join: bool) -> None:
        if join and out and out[-1][0] == kind and not fresh[0]:
            out[-1][1].extend(lines)
        else:
            out.append((kind, list(lines)))
        fresh[0] = False

    fresh = [True]
    for ln in _unwrap((text or "").replace("\x00", "").replace("\r\n", "\n").split("\n")):
        if ln.startswith("\x00"):
            put("code", [ln[1:]], True)
            continue
        s = ln.strip()
        if not s:
            if out and out[-1][0] not in ("list",):         # 列表项之间的空行不打断列表
                fresh[0] = True
            continue
        if _RULE.match(ln):
            fresh[0] = True
            continue
        if (h := HEADING_RX.match(s)) and (t := re.sub(r"\s#+$", "", h.group(2)).strip("*").strip()):
            put("head", [f"{h.group(1)} {t}"], False)
        elif (b := _BOLD_LINE.match(s)) and not _is_sentence(b.group(1)) and b.group(1).strip():
            put("head", [f"## {b.group(1).strip()}"], False)
        elif (b := _BRACKET_LINE.match(s)) and b.group(1).strip():
            put("head", [f"## {b.group(1).strip()}"], False)
        elif _TABLE.match(ln):
            put("table", [s], True)
        elif s.startswith(">"):
            put("quote", [s], True)
        elif (r := REVIEW_RX.match(ln)):
            put("list", _review(r), True)
        elif (it := _ITEM.match(ln)) or (it := _DOT_ITEM.match(ln)):
            indent = len(it.group(1).expandtabs(4))
            num = it.group(2) if it.re is _ITEM else None
            body = it.group(it.re.groups)
            put("list", [(indent, f"{num}." if num else "-", body)], True)
        elif out and out[-1][0] == "list" and not fresh[0] and ln[:2] in ("  ", "\t") and ln.lstrip() != ln \
                and isinstance(out[-1][1][-1], tuple) and len(ln) - len(ln.lstrip()) > out[-1][1][-1][0]:
            ind, mark, body = out[-1][1][-1]                # 列表项的续行
            out[-1][1][-1] = (ind, mark, f"{body} {s}")
        else:
            put("para", [s], False)
        fresh[0] = False
    return out


def _list_lines(items: list) -> list[str]:
    """列表项缩进归一：按出现的缩进排级，每级两个空格。审核条目已是成品行。"""
    levels: list[int] = []
    out: list[str] = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
            levels = []
            continue
        ind, mark, body = it
        while levels and levels[-1] > ind:
            levels.pop()
        if not levels or levels[-1] < ind:
            levels.append(ind)
        out.append("  " * min(len(levels) - 1, MAX_NEST) + f"{mark} {body}")
    return out


def normalize(text: str) -> str:
    """任何 Markdown → 规整 Markdown：块与块之间恰有一个空行，相邻的普通行各成一段。"""
    parts = []
    for kind, lines in blocks(text):
        parts.append("\n".join(_list_lines(lines) if kind == "list" else lines))
    return "\n\n".join(parts) + "\n" if parts else ""


# ── HTML ──────────────────────────────────────────────────────

# 行内记号一次扫完，不做多趟替换：多趟替换时后一趟会把前一趟生成的片段吞进网址里，造出可执行的属性
_TOKEN = re.compile(
    r"`(?P<code>[^`\n]{1,2000})`"
    r"|\[(?P<lab>[^\]\[\n]{1,500})\]\((?P<href>https?://[^\s()<>\"'`\[\]]{1,2000})\)"
    r"|(?P<url>https?://[^\s<>\"'`*\[\]「」（）()《》，。；、）】]{1,2000})"
    r"|\[\^(?P<foot>\w{1,8})\]"
    r"|\*\*(?=\S)(?P<bold>[^*\n]{1,3000}?)(?<=\S)\*\*")
# 脚注紧跟的显示值：数字连同单位（单位多种多样，不逐个列）；前面是字母的（CR5）不是数值
_VALUE = re.compile(r"(?<![A-Za-z\d.,])(-?\d[\d,]*(?:\.\d+)?(?:\s?[^\s\d，。；：、（）()|/*]{0,6})?)$")
_TAIL = 60                        # 只在脚注前这么多字里找数值
_BIND = "，。、；：！？）」』》%"    # 紧随数字的标点与它绑在一行，免得「，」挤到下一行行首


def _e(x) -> str:
    return _html.escape(str(x), quote=True)


class _Ids:
    def __init__(self, uid: str) -> None:
        self.uid, self.n = uid, 0

    def next(self) -> str:
        self.n += 1
        return f"{self.uid}-{self.n}"


def source_button(value: str, tip: str, sid: str) -> str:
    """数字来源 toggletip：点击、回车、悬停、聚焦都能打开，键盘与读屏可用。参数已转义。"""
    return (f'<button type="button" class="num" interestfor="{sid}" popovertarget="{sid}" '
            f'popovertargetaction="show" aria-expanded="false">{value}</button>'
            f'<span id="{sid}" popover="hint" role="tooltip" class="tip">{tip}</span>')


def inline(text: str, notes: dict[str, str], ids: _Ids) -> str:
    """一行文字 → HTML。认得的记号逐个展开，其余整段转义；网址只放行 http/https。"""
    out: list[str] = []
    plain, at = "", 0                     # plain：上一个记号之后、还没写出去的普通文字
    for m in _TOKEN.finditer(text):
        if m.start() < at:
            continue                      # 脚注把紧随的标点并走了
        plain += text[at:m.start()]
        at = m.end()
        kind = m.lastgroup if m.lastgroup != "lab" else "href"
        if kind == "foot":
            tip = notes.get(m.group("foot"))
            if tip is None:
                plain += m.group(0)
                continue
            v = _VALUE.search(plain[-_TAIL:])
            shown = v.group(1) if v else f"[{m.group('foot')}]"
            out.append(_e(plain[:len(plain) - len(v.group(1))] if v else plain))
            btn = source_button(_e(shown), _e(tip), f"src-{ids.next()}")
            if text[at:at + 1] and text[at] in _BIND:
                btn, at = f'<span class="nw">{btn}{_e(text[at])}</span>', at + 1
            out.append(btn)
        else:
            out.append(_e(plain))
            if kind == "code":
                out.append(f"<code>{_e(m.group('code'))}</code>")
            elif kind == "bold":
                out.append(f"<strong>{inline(m.group('bold'), notes, ids)}</strong>")
            else:
                url = m.group("href") or m.group("url")
                label = re.sub(r"[*`]", "", m.group("lab")) if m.group("lab") else url
                out.append(f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(label)}</a>')
        plain = ""
    return "".join(out) + _e(plain + text[at:])


def _ul(items: list[tuple[int, str, str]], notes, ids, fold_over: int) -> str:
    """[(级, 标记, 文字)] → 嵌套列表。任何一层超过 `fold_over` 条都折叠。"""
    top = min(lv for lv, _m, _b in items)
    starts = [i for i, (lv, _m, _b) in enumerate(items) if lv <= top]
    if starts[0] != 0:
        starts = [0, *starts]
    tag = "ol" if items[0][1][:-1].isdigit() else "ul"
    lis = []
    for a, b in zip(starts, [*starts[1:], len(items)]):
        kids, mark, body = items[a + 1:b], items[a][1], items[a][2]
        num = mark[:-1] if mark[:-1].isdigit() else ""
        if num and tag == "ul":                    # 圆点列表里夹的编号项：编号留在文字里
            num, body = "", f"{mark} {body}"
        lis.append((f'<li value="{num}">' if num else "<li>") + inline(body, notes, ids) + (_ul(kids, notes, ids, fold_over) if kids else "") + "</li>")
    h = f"<{tag}>" + "".join(lis) + f"</{tag}>"
    return f"<details><summary>共 {len(lis)} 条，点开看</summary>{h}</details>" if fold_over and len(lis) > fold_over else h


def _cells(row: str) -> list[str]:
    s = row.strip()
    s = s[1:] if s.startswith("|") else s
    return [c.strip() for c in (s[:-1] if s.endswith("|") else s).split("|")]


def _table(rows: list[str], notes, ids) -> str:
    if len(rows) < 2 or not all(re.fullmatch(r":?-+:?", c) for c in _cells(rows[1])):
        return "".join(f"<p>{inline(r, notes, ids)}</p>" for r in rows)
    head = "".join(f'<th scope="col">{inline(c, notes, ids)}</th>' for c in _cells(rows[0]))
    body = "".join("<tr>" + "".join(f"<td>{inline(c, notes, ids)}</td>" for c in _cells(r)) + "</tr>" for r in rows[2:])
    return (f'<div class="tbl" tabindex="0" role="region"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def to_html(text: str, notes: dict[str, str] | None = None, *, base: int = 2, uid: str = "v",
            fold_over: int = 0, fold_heads: tuple[str, ...] = ()) -> str:
    """规整 Markdown → HTML。`notes` 是脚注号 → 来源说明；`base` 为正文最高标题级别；`uid` 区分同页多次渲染。
    `fold_over` > 0 时，超过这么多条的列表折叠；标题以 `fold_heads` 之一开头的小节（到下一个标题为止）折叠。全部文字先转义，链接只放行 http/https。"""
    notes, ids = notes or {}, _Ids(uid)
    bl = blocks(text)
    heads = [len(HEADING_RX.match(b[1][0]).group(1)) for b in bl if b[0] == "head"]
    shift = base - min(heads) if heads else 0
    out: list[str] = []
    folding = False
    for kind, lines in bl:
        if kind == "head":
            m = HEADING_RX.match(lines[0])
            if folding:
                out.append("</details>")
            folding = bool(fold_heads) and m.group(2).startswith(fold_heads)
            if folding:
                out.append(f"<details><summary>{inline(m.group(2), notes, ids)}</summary>")
                continue
            lv = max(1, min(6, len(m.group(1)) + shift))
            out.append(f'<h{lv} id="h-{ids.next()}">{inline(m.group(2), notes, ids)}</h{lv}>')
        elif kind == "list":
            items = []
            for ln in _list_lines(lines):
                m = re.match(r"^(\s*)(-|\d{1,2}\.)\s(.*)$", ln, re.S)
                items.append((len(m.group(1)) // 2, m.group(2), m.group(3)))
            out.append(_ul(items, notes, ids, fold_over))
        elif kind == "quote":
            inner = "".join(f"<p>{inline(x.lstrip('> ').strip(), notes, ids)}</p>" for x in lines if x.lstrip("> ").strip())
            out.append(f"<blockquote>{inner}</blockquote>")
        elif kind == "table":
            out.append(_table(lines, notes, ids))
        elif kind == "code":
            body = [x for x in lines if not _FENCE.match(x)]
            out.append(f"<pre><code>{_e(chr(10).join(body))}</code></pre>")
        else:
            out.append(f"<p>{inline(lines[0], notes, ids)}</p>")
    if folding:
        out.append("</details>")
    return "\n".join(out)


def fold(items: list[str], label: str, limit: int = 10) -> str:
    """名单：不超过 `limit` 条直接列；超过就折叠成「label 共 N 条」。"""
    h = "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in items) + "</ul>"
    return h if len(items) <= limit else f"<details><summary>{_e(label)}：共 {len(items)} 条，点开看</summary>{h}</details>"
