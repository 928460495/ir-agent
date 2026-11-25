"""人读产物的只读阅读版（用户 2026-11-22 选方案 A；2026-11-25 四道门都有，spec readable-layout）。

`macro_view.md`、`strategy.md`、`value_chain.md` 是**可编辑的源文件**：门① 的人改稿签名后程序读回
（按指纹判断是否改过），后续撰写引用的也是其中的占位符。把数字直接写进去，人一改，数字就与占位符
脱钩，裸数字审计也会把它们当成账本外数字。所以数字只进另一份文件：每次运行用账本把源文件渲染成
`<名>_阅读版.md`（占位符 → 数值 + 脚注），程序从不读回它。看板的定性部分用 `render_inline`。

与研报的 `citation.render` 同一套取值与格式（`format_value`），区别只在：取不到的占位符**不抛**，原样
保留并标「账本未取到」—— 阅读版是给人看的，不能因为一处引用缺数就整份不出。时点守卫照旧：分析时点
之后才披露的数字同样算取不到。
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ir_agent import mdview
from ir_agent.citation import REF_RE, format_value
from ir_agent.labels import fact_label, source_label
from ir_agent.ledger import LookAheadError

SUFFIX = "_阅读版.md"
_MISSING = "（账本未取到）"


def _lookup(m: re.Match[str], ledger, as_of: date):
    source_id, key, period = m.group(1), m.group(2), m.group(3)
    try:
        fact = ledger.get(key, period, as_of=as_of)
    except (KeyError, LookAheadError):
        return None
    if source_id is not None and fact.source_id != source_id:
        return None
    return fact


# 占位符后面隔这些字就算「紧跟」：模型常写「[[CPI]] 为 3.72%」「[[营收]] 393.75亿」（历史产物里一成多如此）
_NEXT_NUM = re.compile(r"[\s（(，,]{0,2}(?:为|是|约|仅|即|＝|=)?\s?(-?\d[\d,]*(?:\.\d+)?)")
_ALONE_BEFORE = re.compile(r"(?:^|[。！？；|\n])\s*$")
_ALONE_AFTER = re.compile(r"^\s*(?:$|[。！？；|\n])")


def _same_number(shown: str, other: str) -> bool:
    """「81.72%」与「81.7」算同一个数：按较短那个的小数位四舍五入后相等。"""
    m = re.match(r"-?\d[\d,]*(?:\.\d+)?", shown)
    if not m:
        return False
    a, b = Decimal(m.group(0).replace(",", "")), Decimal(other.replace(",", ""))
    places = min(-a.as_tuple().exponent, -b.as_tuple().exponent)
    if abs(a) < 10 and places < 1:                     # 「1」与「1.04x」不算同一个数
        return a == b
    return round(a, places) == round(b, places)


def _display(m: re.Match[str], fact, text: str) -> str:
    """一处占位符显示成什么（spec 2026-11-25-readable-layout §4.3）。"""
    shown, label = format_value(fact), fact_label(fact.key)
    if not label:
        return shown
    nxt = _NEXT_NUM.match(text[m.end():m.end() + 24])
    if nxt and nxt.start(1) <= 8 and _same_number(shown, nxt.group(1)):
        # 前面已经写了这个名字（「高收益债利差 [[…]] 仅 3.63%」）：只留脚注
        return "" if text[:m.start()].rstrip(" 　（(：:").endswith(label) else f"{label}（{fact.period}）"
    if _ALONE_BEFORE.search(text[:m.start()]) and _ALONE_AFTER.match(text[m.end():]):
        return f"{label} {shown}"
    return shown


def render_inline(text: str, ledger, as_of: date) -> str:
    """占位符 → 数值或中文名，不加脚注。取不到的原样保留并标注。"""
    text = text or ""

    def _sub(m):
        fact = _lookup(m, ledger, as_of)
        return _display(m, fact, text) if fact is not None else m.group(0) + _MISSING
    return REF_RE.sub(_sub, text)


def render_readable(text: str, ledger, as_of: date) -> tuple[str, dict[str, str]]:
    """占位符 → 数值或中文名 + `[^n]`。返回（正文, 脚注号 → 来源说明）；同一引用只编一个号。"""
    text = text or ""
    seen: dict[tuple[str, str, str], str] = {}
    notes: dict[str, str] = {}

    def _sub(m):
        fact = _lookup(m, ledger, as_of)
        if fact is None:
            return m.group(0) + _MISSING
        ident = (fact.source_id, fact.key, fact.period)
        if ident not in seen:
            seen[ident] = str(len(seen) + 1)
            notes[seen[ident]] = note_text(fact.key, fact.period, fact.source_id, fact.as_of, fact.method.value)
        return f"{_display(m, fact, text)}[^{seen[ident]}]"

    return REF_RE.sub(_sub, text), notes


def note_text(key: str, period: str, source_id: str, as_of, method: str) -> str:
    """来源说明：中文在前，内部键与 source_id 在后（反查要用）。"""
    how = {"reported": "披露值", "computed": "计算值"}.get(str(method), str(method))
    name = fact_label(key)
    head = f"{name}（{period}）· " if name else ""
    return (f"{head}来源 {source_label(source_id)} · 披露日 {as_of.isoformat()} · {how}"
            f" · {key}@{period} · {source_id}")


def with_notes(body: str, notes: dict[str, str]) -> str:
    """正文 + 文末「数字出处」。"""
    body = body.rstrip("\n") + "\n"
    if not notes:
        return body
    return body + "\n## 数字出处\n\n" + "\n".join(f"[^{k}]: {v}" for k, v in notes.items()) + "\n"


# ── 各文件的排版 ───────────────────────────────────────────────

EMPTY = "本轮未生成。"


def _table_rows(block: list[str]) -> list[list[str]]:
    return [mdview._cells(r) for r in block]


def _merge_same_columns(text: str) -> str:
    """表里「理由」「依据」整列相同（或「同上」「—」）的，从表里拿出来写成表下一行；数值列一格不动。"""
    out: list[str] = []
    for kind, lines in mdview.blocks(text):
        if kind == "list":
            lines = mdview._list_lines(lines)
        if kind != "table" or len(lines) < 4:
            out.append("\n".join(lines))
            continue
        rows = _table_rows(lines)
        head, body, below, drop = rows[0], rows[2:], [], set()
        for c, name in enumerate(head):
            if name not in ("理由", "依据") or any(len(r) != len(head) for r in body):
                continue
            real = list(dict.fromkeys(r[c] for r in body if r[c] not in ("同上", "—", "")))
            if len(real) <= 1:
                drop.add(c)
                if real:
                    below.append(f"- {name}（各年相同）：{real[0]}")
        keep = [c for c in range(len(head)) if c not in drop]
        fmt = lambda r: "| " + " | ".join(r[c] for c in keep) + " |"
        out.append("\n".join([fmt(head), fmt(["---"] * len(head)), *map(fmt, body)]) if drop else "\n".join(lines))
        if below:
            out.append("\n".join(below))
    return "\n\n".join(out)


def _source_table(text: str) -> str:
    """研报末尾「数据来源」表：字段与来源换中文名，内部键留在末列。"""
    out = []
    for ln in text.split("\n"):
        c = mdview._cells(ln) if ln.strip().startswith("|") else []
        if c == ["数值", "字段@期间", "来源", "披露日", "取得方式"]:
            out.append("| 数值 | 指标 | 来源 | 披露日 | 取得方式 | 内部键 |")
        elif len(c) == 5 and set("".join(c)) <= set("-: "):
            out.append("| --- | --- | --- | --- | --- | --- |")
        elif len(c) == 5 and re.fullmatch(r"[\w.\-]+@[\w\-]+", c[1]):
            key, _, per = c[1].partition("@")
            name = fact_label(key)
            how = {"reported": "披露值", "computed": "计算值"}.get(c[4], c[4])
            out.append(f"| {c[0]} | {f'{name}（{per}）' if name else c[1]} | {source_label(c[2])} | {c[3]} | {how} "
                       f"| {c[1]} · {c[2]} |")
        else:
            out.append(ln)
    return "\n".join(out)


def stance_markdown(d: dict) -> str:
    """多空辩论（`stance_*.json`）→ 全文 Markdown：平衡点、两侧证据、核验不过的、被跳过的行。"""
    lines: list[str] = []
    if d.get("manual"):
        lines += [f"> ⚠ 本轮多空输入由 {d['manual']} **人工补写**", ""]
    lines += ["## 平衡点", ""]
    for b in d.get("balances") or []:
        tri = "" if b.get("mid") is None else f" —— 空 {b.get('bear')} / 平 {b.get('mid')} / 多 {b.get('bull')}"
        lines += [f"### {b.get('subject', '')}{tri}", "", " ".join(str(b.get("note") or "").split()), ""]
    for side in dict.fromkeys(e.get("side", "") for e in d.get("evidence") or []):
        lines += [f"## {side}证据", ""]
        for e in d["evidence"]:
            if e.get("side", "") != side:
                continue
            lines += [f"### {e.get('subject', '')}", "", " ".join(str(e.get("claim") or "").split()), ""]
            for b in [x for x in e.get("bases") or [] if x]:
                lines += [f"- 账本依据：{b}"]
            lines.append("")
            for q in [x for x in e.get("quotes") or [] if x]:
                lines += [f"> 「{q}」"] + ([f"> —— {e['source']}"] if e.get("source") else []) + [""]
            if not any(e.get("quotes") or []) and e.get("source"):
                lines += [f"出处：{e['source']}", ""]
            if e.get("unverified"):
                lines += [f"⚠ 代码核验不过：{e['unverified']}", ""]
    if d.get("dropped"):
        lines += ["## 因格式错误被跳过的行", "", *(f"- {x}" for x in d["dropped"]), ""]
    return "\n".join(lines)


_CHALLENGE = re.compile(r"^\s*(✗ 成立|· 不成立|· 未裁决)\s*\[([^\]]+)\]\s*(.*)$")


def debate_markdown(text: str) -> str:
    """成稿辩论（`debate_report.md`）→ 每项挑战一个小节：类别与裁决作标题，原文作引用，理由成段。"""
    out: list[str] = []
    n = 0
    for ln in text.split("\n"):
        m = _CHALLENGE.match(ln)
        if m:
            n += 1
            out += ["", f"### {n}. {m.group(2)} —— {m.group(1).strip('✗· ')}", "", f"> {m.group(3)}", ""]
        elif ln.strip():
            out += [ln.strip(), ""]
    return "\n".join(out)


def _track_section(out: Path) -> str:
    """跟踪指标的最新读数（`macro_track.json`）。证伪与否交人判断，读数与证伪条件并列。"""
    from ir_agent import macro_track

    res = macro_track.load(out / macro_track.TRACK_JSON, None)
    if res is None or not (res.items or res.failed):
        return ""
    lines = ["", "## 跟踪指标最新读数", ""]
    for i in res.items:
        lines += [f"### {i.indicator}", "", f"**对应传导**：{i.driver}", "", f"**证伪条件**：{i.falsifier}", "",
                  f"**最新读数**：{i.conclusion or '未取到'}", ""]
        for u in i.sources:
            lines.append(f"- 出处：{u}" + (f"（发布 {d}）" if (d := i.published.get(u)) else ""))
        lines.append("")
    lines += [f"⚠ 检索失败：{x}" for x in res.failed]
    if res.truncated:
        lines.append(f"⚠ 超过上限未检索：{'、'.join(res.truncated)}")
    return "\n" + mdview.normalize("\n".join(lines))


def _field_doc(name: str):
    def build(src: Path, out: Path, title: str) -> str:
        from ir_agent import fieldview
        from ir_agent import qual_review as Q
        from ir_agent.methodology_review import ReviewResult

        raw = src.read_text(encoding="utf-8")
        rf, review, note = out / f"{name}_review.json", None, "没有审核记录"
        if rf.exists():
            rec = json.loads(rf.read_text(encoding="utf-8"))
            if rec.get("fingerprint") == Q.fingerprint(raw):
                review, note = ReviewResult.from_json(rec), ""
            else:
                note = "这份稿经人工修改，以签名确认的版本为准；改动前的审核意见不再列出"
        doc = fieldview.layout(name, raw, review, note=note, title=title)
        return doc + _track_section(out) if name == "macro_view" else doc
    return build


def _plain(pre=lambda t: t):
    def build(src: Path, out: Path, title: str) -> str:
        body = mdview.normalize(pre(src.read_text(encoding="utf-8")))
        first = next((ln for ln in body.split("\n") if ln.strip()), "")
        if first.startswith("# "):                     # 文件自带一级标题：并进总标题下面
            body = body.replace(first, "#" + first, 1)
        return f"# {title}\n\n" + (body or EMPTY + "\n")
    return build


def _stance_doc(src: Path, out: Path, title: str) -> str:
    return f"# {title}\n\n" + mdview.normalize(stance_markdown(json.loads(src.read_text(encoding="utf-8"))))


# (阅读版名, 源文件, 中文名, 排版)。按四道门的顺序（spec 2026-11-25-readable-layout §4.5）
VIEWS: tuple[tuple[str, str, str, object], ...] = (
    ("macro_view", "macro_view.md", "宏观定性", _field_doc("macro_view")),
    ("strategy", "strategy.md", "战略分析", _field_doc("strategy")),
    ("value_chain", "value_chain.md", "产业链", _field_doc("value_chain")),
    ("stance_a", "stance_a.json", "多空辩论①", _stance_doc),
    ("drivers", "drivers.md", "驱动因子分解", _plain()),
    ("stance_b", "stance_b.json", "多空辩论②", _stance_doc),
    ("brief", "brief.md", "研究简报", _plain()),
    ("review", "review.md", "待核清单", _plain()),
    ("assumptions", "assumptions.md", "DCF 判断项", _plain(_merge_same_columns)),
    ("report", "report.md", "完整研报", _plain(_source_table)),
    ("debate_report", "debate_report.md", "成稿辩论", _plain(debate_markdown)),
)
LABELS = {stem: label for stem, _src, label, _b in VIEWS}


def render_views(out: Path | str, ledger, as_of: date, *, title: str = "", notes: list[str] | None = None
                 ) -> dict[str, tuple[str, dict[str, str]]]:
    """目录里已有的源文件 → {阅读版名: (正文, 脚注)}。单份失败只记说明，其余照出。"""
    out = Path(out)
    got: dict[str, tuple[str, dict[str, str]]] = {}
    for stem, name, label, build in VIEWS:
        src = out / name
        if not src.exists():
            continue
        try:
            text = build(src, out, f"{title} · {label}" if title else label)
            got[stem] = render_readable(text, ledger, as_of)
        except Exception as e:                              # noqa: BLE001
            if notes is not None:
                notes.append(f"阅读版 {stem} 未生成（{e.__class__.__name__}: {str(e)[:120]}）")
    return got


def write_readables(out: Path | str, ledger, as_of: date, *, title: str = "",
                    notes: list[str] | None = None, views: dict | None = None) -> list[Path]:
    """为目录里已有的源文件各写一份阅读版；源文件已不在（或这次没排出来）的，删掉过时的。返回写出的文件。
    `views` 给出时直接落盘（看板与阅读版共用一次渲染）。"""
    out = Path(out)
    got = render_views(out, ledger, as_of, title=title, notes=notes) if views is None else views
    written: list[Path] = []
    for stem, name, _label, _b in VIEWS:
        dst = out / f"{stem}{SUFFIX}"
        if stem not in got:
            dst.unlink(missing_ok=True)
            continue
        body, foot = got[stem]
        first, _, rest = body.partition("\n")
        tip = (f"> 只读阅读版：占位符已换成账本数值（分析时点 {as_of.isoformat()}），脚注列出处。"
               f"修改请改 `{name}`，本文件每次运行重新生成。")
        dst.write_text(with_notes(f"{first}\n\n{tip}\n{rest}", foot), encoding="utf-8")
        written.append(dst)
    return written
