"""HTML 看板 —— 单文件、自包含、离线可开。

**「可互动」要用在刀刃上。** 本项目的核心主张是「每个数字可反查」，
所以最该做的交互是**悬停任意数字看它的来源与披露日**；其次是敏感性矩阵。
堆一屏图表不是交互，是装饰。

三条硬约束:
  · 自包含 —— 无外部脚本/样式/字体，离线可开（兼容脚本内联，不引 polyfill）
  · 未审核的估值必须显著标注，不能和已审核的长一样
  · 缺失的部分不静默省略 —— 看板上看不到的东西，读者会以为不存在
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

from ir_agent.citation import REF_RE, format_value
from ir_agent.labels import fact_label, source_label
from ir_agent.market import engine_name, money_unit
from ir_agent.mdview import to_html
from ir_agent.readable import note_text

# 按 Google modern-web-guidance 标准化（2026-11-20）：dark-mode（color-scheme）、respect-os-text-scale
# （rem 字号）、improve-text-layout-and-legibility（text-wrap）、responsive-table、
# defer-rendering-heavy-content、interest-triggered-tooltips（toggletip）。
_CSS = """
:root{color-scheme:light dark;--bg:#f6f7f8;--card:#fff;--ink:#141a22;--dim:#5d6a77;--rule:#dde3e8;
--ok:#2c6e5c;--warn:#8f6c1e;--bad:#b8382a;--accent:#2f5d8a;--soft:#eef2f5}
@media(prefers-color-scheme:dark){:root{--bg:#0e141a;--card:#161e26;--ink:#e3e8ed;
--dim:#8b98a5;--rule:#2a3542;--ok:#5aaf95;--warn:#c6a353;--bad:#e5705c;--accent:#7aa8d4;--soft:#1b2530}}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:16px}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--bg);color:var(--ink);overflow-wrap:anywhere;
font:.9375rem/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:32px 24px 80px}
.cols{display:grid;grid-template-columns:200px minmax(0,1fr);gap:40px;align-items:start}
.toc{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto;font-size:.85rem}
.toc ul{list-style:none;margin:0;padding:0}.toc li{margin:0}
.toc ul ul{padding-left:12px;border-left:1px solid var(--rule)}
.toc a{display:block;padding:4px 0;color:var(--dim);text-decoration:none}
.toc a:hover,.toc a:focus-visible{color:var(--accent)}
@media(max-width:899px){.wrap{padding:20px 16px 64px}.cols{display:block}
.toc{position:static;max-height:none;margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid var(--rule)}
.toc>ul{display:flex;flex-wrap:wrap;gap:0 16px}.toc ul ul{display:none}.toc a{padding:8px 0}}
h1,h2,h3,h4{text-wrap:balance;line-height:1.4}
h1{font-size:1.7rem;margin:0 0 4px}
h2{font-size:1.25rem;margin:44px 0 14px;padding-bottom:8px;border-bottom:2px solid var(--rule)}
section:first-of-type>h2{margin-top:0}
h3{font-size:1.06rem;margin:30px 0 10px}h4{font-size:.98rem;margin:22px 0 8px}
h5,h6{font-size:.92rem;margin:18px 0 6px;color:var(--dim)}
.sub{color:var(--dim);font-size:.9rem;margin-bottom:20px}
.lights{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.light{padding:5px 11px;border-radius:3px;font-size:.82rem;border:1px solid}
.light.ok{color:var(--ok);border-color:var(--ok)}
.light.warn{color:var(--warn);border-color:var(--warn)}
.light.bad{color:var(--bad);border-color:var(--bad);font-weight:700}
.figs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;margin:0 0 28px;
background:var(--rule);border:1px solid var(--rule)}
@media(max-width:899px){.figs{grid-template-columns:repeat(2,minmax(0,1fr))}}
.fig{background:var(--card);padding:12px 14px}
.fig b{display:block;font-size:1.1rem;overflow-wrap:normal;word-break:keep-all;font-variant-numeric:tabular-nums}
.fig span{color:var(--dim);font-size:.78rem}
.card{background:var(--card);border:1px solid var(--rule);padding:18px 22px;margin-bottom:14px;overflow-wrap:anywhere}
.card>:first-child{margin-top:0}.card>:last-child{margin-bottom:0}
.read p,.read li{max-width:46em;text-wrap:pretty}
.read p{margin:0 0 12px}.read ul,.read ol{margin:0 0 14px;padding-left:1.4em}.read li{margin:3px 0}li.dot{list-style:disc}
blockquote{margin:0 0 14px;padding:8px 14px;border-left:3px solid var(--rule);background:var(--soft);color:var(--dim)}
blockquote p{margin:0 0 4px}blockquote p:last-child{margin:0}
/* 数字来源：toggletip —— 点击、回车、悬停、聚焦都能打开，键盘与读屏可用 */
button.num{font:inherit;color:var(--accent);background:none;border:0;padding:0;
border-bottom:1px dotted var(--accent);cursor:help}
button.num:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tip{white-space:normal;max-width:28rem;padding:.5rem .75rem;font-size:.8rem;line-height:1.5;color:var(--ink);
background:var(--card);border:1px solid var(--rule);box-shadow:0 2px 8px rgb(0 0 0/.15)}
@supports (position-area:block-start){[popover].tip{position-area:block-start;
position-try-fallbacks:flip-block;inset:auto;margin:0}}
.nw{white-space:nowrap;max-width:100%}
.tbl{overflow:auto;margin:0 0 14px}
.tbl:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
caption{caption-side:top;text-align:left;color:var(--dim);font-size:.8rem;padding:0 0 6px}
/* 首屏以下的长表：滚到附近再排版绘制；auto 记住真实高度，避免滚动条跳动 */
.defer{content-visibility:auto;contain-intrinsic-size:auto 600px}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--rule);vertical-align:top;
overflow-wrap:normal;min-width:4.5em}
th{color:var(--dim);font-weight:600;font-size:.78rem;white-space:nowrap}
.mono{font-size:.8rem;color:var(--dim)}
code{font:.88em ui-monospace,Menlo,monospace;background:var(--soft);padding:1px 4px}
pre{overflow:auto;background:var(--soft);padding:12px}
details{margin:8px 0 14px}summary{cursor:pointer;color:var(--dim);font-size:.88rem;padding:4px 0}
details[open]>summary{margin-bottom:6px}
.note{border-left:3px solid var(--warn);padding:2px 0 2px 12px;margin:0 0 12px;color:var(--dim);font-size:.9rem}
.note.bad{border-color:var(--bad)}
a{color:var(--accent);overflow-wrap:anywhere}
"""


# toggletip 的两段兼容：不支持 popover="hint" 时打开前改成 auto（否则点开的永远关不掉）；
# 用 toggle 事件同步触发按钮的 aria-expanded（toggle/beforetoggle 不冒泡，捕获阶段委托）。
_JS = """(()=>{const p=document.createElement("div");p.popover="hint";
const own=t=>document.querySelectorAll('[popovertarget="'+CSS.escape(t.id)+'"]');
if(p.popover!=="hint"){document.addEventListener("beforetoggle",({target:t,newState:s})=>{
if(s==="open"&&t.getAttribute("popover")==="hint"&&own(t).length)t.popover="auto";},true);}
document.addEventListener("toggle",({target:t,newState:s})=>{if(!t.id)return;
for(const b of own(t))b.setAttribute("aria-expanded",s==="open"?"true":"false");},true);})();"""


def _e(x) -> str:
    return _html.escape(str(x), quote=True)


# 抬头的关键数字：账本里有哪些就显示哪些（spec §4.6）
FIGURES = ("market_cap", "revenue", "revenue.yoy", "gross_margin", "net_margin", "roe", "pe_ttm", "pb")
# 定性模块：看板上下文的名字 → 阅读版名
QUAL = (("宏观定性", "macro_view"), ("战略", "strategy"), ("产业链", "value_chain"))
FOLD = 10                           # 任何名单超过这么多条就折叠


def _marked(audit) -> tuple[str, dict[str, str]]:
    """研究正文：占位符 → 数值 + `[^n]`。按原稿逐处替换，同值的两个指标各挂各的来源。"""
    notes = {str(n.marker): note_text(n.key, n.period, n.source_id, n.as_of, n.method) for n in audit.footnotes}
    by = {(n.key, n.period): n for n in audit.footnotes}

    def sub(m):
        n = by.get((m.group(2), m.group(3)))
        return f"{n.display}[^{n.marker}]" if n is not None else m.group(0)
    if audit.draft:
        return REF_RE.sub(sub, re.sub(r"\[\^(?=\w)", "[＾", audit.draft)), notes
    # 没有原稿（由落盘的成稿重出）：按显示值找，同值的几个指标把来源都列出来
    text, by_val = re.sub(r"\[\^(?=\w)", "[＾", audit.rendered), {}
    for n in audit.footnotes:
        if n.display:
            by_val.setdefault(n.display, []).append(str(n.marker))
    marks = {v: ms[0] for v, ms in by_val.items()}
    for ms in by_val.values():
        notes[ms[0]] = "；或 ".join(dict.fromkeys(notes[m] for m in ms))
    if marks:
        rx = "|".join(re.escape(v) for v in sorted(marks, key=len, reverse=True))
        text = re.sub(rf"(?<![\d.,])(?:{rx})(?![\d])", lambda m: f"{m.group(0)}[^{marks[m.group(0)]}]", text)
    return text, notes


def _latest(run, key: str):
    """某个指标在报告期的值；没有就取分析时点前最新一期。时点之后披露的不看。"""
    facts = [f for (k, _p), fs in run.ledger._facts.items() if k == key for f in fs      # noqa: SLF001
             if f.as_of <= run.as_of]
    same = [f for f in facts if f.period == run.period]
    return max(same or facts, key=lambda f: (f.as_of, f.period), default=None)


def _figures(run) -> str:
    cells = []
    for key in FIGURES:
        f = _latest(run, key)
        if f is not None:
            cells.append(f'<div class="fig"><b>{_e(format_value(f))}</b>'
                         f"<span>{_e(fact_label(key) or key)} · {_e(f.period)}</span></div>")
    return f'<section class="figs" aria-label="关键数字">{"".join(cells)}</section>' if cells else ""


def _alerts(text: str) -> list[str]:
    """原摘要里程序核对出的 ⚠ 提示（审核遗留之前的部分）—— 阅读版由源文件排出，不含这些。"""
    out = []
    for ln in (text or "").split("\n"):
        if ln.strip().startswith("审核遗留"):
            break
        if ln.strip().startswith("⚠"):
            out.append(ln.strip())
    return out


def build_dashboard(
    run,
    path: Path | str,
    comps=None,
    dcf=None,
    context=None,
    views: dict | None = None,
    company: str = "",
) -> Path:
    """context: {标题: 摘要文本}，宏观/行业/可比的**口径**；views: {阅读版名: (Markdown, 脚注)}。

    口径不进看板，读者就只能看到「市占率 47%」这个结论，看不到它建立在
    19 家全覆盖之上还是 12 家残缺样本之上 —— 后者会系统性高估。
    """
    a, rec = run.audit, run.reconcile
    context, has_ctx = dict(context or {}), context is not None
    views = views or {}
    toc: list[tuple[str, str, list[tuple[str, str]]]] = []
    secs: list[str] = []

    def section(sid: str, title: str, inner: str, *, subs: bool = False) -> None:
        kids = re.findall(r'<h3 id="([^"]+)">(.*?)</h3>', inner, re.S) if subs else []
        toc.append((f"sec-{sid}", title, [(i, re.sub(r"<[^>]+>", "", re.sub(r"<span [^>]*tooltip.*?</span>", "", t, flags=re.S)))
                                          for i, t in kids]))
        secs.append(f'<section id="sec-{sid}"><h2>{_e(title)}</h2>{inner}</section>')

    # ── 状态灯 ────────────────────────────────────────────
    lights = []
    lights.append(("ok" if rec.ok else "bad",
                   f"三表勾稽 {'通过' if rec.ok else '失败'}"
                   f"（{rec.checks_run}/{len(rec.results)} 项）"))
    tr = a.traceability
    if getattr(a, "partial", False):
        lights.append(("warn" if tr == 1 else "bad", "由落盘成稿重出：裸数字未重新核对"
                       + ("" if tr == 1 else f"，另有 {len(a.missing_snapshots)} 条引用缺快照、{len(a.unrendered)} 处占位符未落地")))
    else:
        lights.append(("ok" if tr == 1 else "bad", f"数字可溯源率 {tr:.0%}"))
    if a.bare_numbers:
        lights.append(("bad", f"裸数字 {len(a.bare_numbers)} 处"))
    if dcf is not None:
        # **验算是否实跑通过必须一眼可辨。** 一份没通过公式引擎验算的
        # 工作簿看起来和通过的一模一样 —— 数字都在格子里。
        if not dcf.verified:
            lights.append(("bad", "DCF 公式引擎验算未通过 —— 结果不可采信"))
        elif dcf.crosscheck:
            lights.append(("warn", f"DCF 取数与账本有 {len(dcf.crosscheck)} 处对不上"))
        else:
            lights.append(("ok", "DCF 公式引擎验算通过"))
    top = ['<div class="lights">' + "".join(f'<span class="light {c}">{_e(t)}</span>' for c, t in lights) + "</div>"]
    top += [f'<div class="note bad">⚠ {_e(w)}</div>' for w in getattr(run, "warnings", []) or []]
    top.append(_figures(run))

    # ── 正文 ──────────────────────────────────────────────
    body, notes = _marked(a)
    section("body", "研究正文",
            f'<div class="card read">{to_html(body, notes, base=3, uid="b")}</div>'
            '<p class="mono">点击、悬停或用键盘聚焦正文中的数字，可查看其来源与披露日。</p>', subs=True)

    # ── 估值 ──────────────────────────────────────────────
    if dcf is not None:
        unit, up = money_unit(run.code), dcf.upside
        val = (f'<div class="card">每股内在价值 <b>{dcf.value_per_share:,.2f}</b> {unit}　现价 {dcf.price:,.2f} {unit}'
               + (f"　空间 <b>{up:+.1%}</b>" if up is not None else "")
               + f'<br><span class="mono">WACC {dcf.wacc:.2%}　终值 ROIC {dcf.terminal_roic:.1%}　'
               f"稳态再投资率 {dcf.reinvest_rate:.1%}　终值占企业价值 {dcf.terminal_share:.1%}</span>"
               f'<br><span class="mono">工作簿：{_e(dcf.workbook)}</span></div>')
        val += "".join(f'<div class="note">{_e(x)}</div>' for x in dcf.notes)
        # 交叉核对与健全性警告**不折叠**: 它们正是「看起来算出来了」与「算对了」之间的差别。
        val += "".join(f'<div class="note bad">⚠ 与账本对不上：{_e(x)}</div>' for x in dcf.crosscheck)
        val += "".join(f'<div class="note bad">⚠ {_e(x)}</div>' for x in dcf.warnings)
        section("dcf", f"估值（{engine_name(run.code)} 三表模型）", val)

    # ── 宏观 · 战略 · 产业链 ───────────────────────────────
    for name, stem in QUAL:
        text = context.pop(name, None)
        if stem in views:
            md, foot = views[stem]
            md = re.sub(r"\A# [^\n]*\n", "", md.lstrip())           # 总标题由分节标题代替
            inner = "".join(f'<div class="note">{_e(x)}</div>' for x in _alerts(text))
            inner += f'<div class="card read">{to_html(md, foot, base=3, uid=stem, fold_heads=("审核意见",))}</div>'
            section(stem, {"战略": "战略分析"}.get(name, name), inner, subs=True)
        elif text:
            section(stem, name, f'<div class="card read">{to_html(text, base=3, uid=stem, fold_over=FOLD)}</div>')
        elif text is not None:
            section(stem, name, '<div class="note">本轮未取到（没有生成或审核未通过，见该模块的各轮记录）。</div>')

    # ── 分析口径 ──────────────────────────────────────────
    if has_ctx and any(context.values()):
        inner = ""
        for n, (title, text) in enumerate(context.items()):
            if text:
                inner += (f'<h3 id="ctx-{n}">{_e(title)}</h3><div class="card read">'
                          f'{to_html(text, base=4, uid=f"c{n}", fold_over=FOLD)}</div>')
        section("context", "分析口径", inner, subs=True)

    # ── 可比 ──────────────────────────────────────────────
    if comps is not None:
        section("comps", "可比公司",
                f'<div class="card read">{to_html(comps.summary(), base=3, uid="cmp", fold_over=FOLD)}</div>')

    # ── 勾稽明细 ──────────────────────────────────────────
    rows = "".join(
        f"<tr><td>{'✓' if r.status=='passed' else '✗' if r.status=='failed' else '–'}"
        f"</td><td>{_e(r.name)}</td><td class='mono'>{_e(r.detail)}</td></tr>"
        for r in rec.results)
    section("rec", "三表勾稽",
            '<div class="card"><div class="tbl" tabindex="0" role="region" aria-labelledby="cap-rec">'
            '<table><caption id="cap-rec">三表勾稽明细</caption><thead><tr><th scope="col">结果</th>'
            '<th scope="col">检查项</th><th scope="col">明细</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></div>')

    # ── 溯源 ──────────────────────────────────────────────
    how = {"reported": "披露值", "computed": "计算值"}
    fn = "".join(
        f"<tr><td class='mono'>{n.marker}</td><td>{_e(fact_label(n.key) or n.key)}（{_e(n.period)}）</td>"
        f"<td>{_e(source_label(n.source_id))}</td><td>{n.as_of.isoformat()}</td>"
        f"<td>{_e(how.get(str(n.method), n.method))}</td>"
        f"<td class='mono'>{_e(n.key)}@{_e(n.period)}<br>{_e(n.source_id)}</td></tr>" for n in a.footnotes)
    section("src", "溯源",
            f'<div class="card defer"><details><summary>'
            f"{len(a.footnotes)} 条引用，每条可反查到原始接口响应</summary>"
            f'<div class="tbl" tabindex="0" role="region" aria-labelledby="cap-fn">'
            f'<table><caption id="cap-fn">引用溯源明细</caption><thead><tr>'
            f'<th scope="col">序号</th><th scope="col">指标</th><th scope="col">来源</th>'
            f'<th scope="col">披露日</th><th scope="col">取得方式</th><th scope="col">内部键</th></tr></thead>'
            f"<tbody>{fn}</tbody></table></div></details></div>")

    nav = "".join(f'<li><a href="#{i}">{_e(t)}</a>'
                  + (f'<ul>{"".join(f"""<li><a href="#{ki}">{kt}</a></li>""" for ki, kt in kids)}</ul>' if kids else "")
                  + "</li>" for i, t, kids in toc)
    head = company or run.code
    doc = (f"<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           f"<meta name=\"color-scheme\" content=\"light dark\">"
           f"<meta name=\"text-scale\" content=\"scale\">"
           f"<title>{_e(run.code)} {_e(run.period)} 投研看板</title>"
           f"<style>{_CSS}</style></head><body><div class=\"wrap\">"
           f"<header><h1>{_e(head)}　{_e(run.period)}</h1>"
           f"<div class=\"sub\">分析时点 {run.as_of.isoformat()}　"
           f"本看板所有数字均可反查至原始接口响应</div>{''.join(top)}</header>"
           f'<div class="cols"><nav class="toc" aria-label="目录"><ul>{nav}</ul></nav><main>'
           + "".join(secs) + f"</main></div></div><script>{_JS}</script></body></html>\n")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p
