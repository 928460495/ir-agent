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
from datetime import date
from decimal import Decimal
from pathlib import Path

from ir_agent.citation import REF_RE

# 按 Google modern-web-guidance 标准化（2026-11-20）：dark-mode（color-scheme）、respect-os-text-scale
# （rem 字号）、improve-text-layout-and-legibility（text-wrap）、responsive-table、
# defer-rendering-heavy-content、interest-triggered-tooltips（toggletip）。
_CSS = """
:root{color-scheme:light dark;--bg:#f6f7f8;--card:#fff;--ink:#141a22;--dim:#6b7885;--rule:#dde3e8;
--ok:#2c6e5c;--warn:#8f6c1e;--bad:#b8382a;--accent:#2f5d8a}
@media(prefers-color-scheme:dark){:root{--bg:#0e141a;--card:#161e26;--ink:#e3e8ed;
--dim:#8b98a5;--rule:#2a3542;--ok:#5aaf95;--warn:#c6a353;--bad:#e5705c;--accent:#7aa8d4}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:.9375rem/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:32px 24px 80px}
h1,h2{text-wrap:balance}
h1{font-size:1.7rem;margin:0 0 4px}
h2{font-size:1.1rem;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--rule)}
.sub{color:var(--dim);font-size:.9rem;margin-bottom:20px}
.lights{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}
.light{padding:5px 11px;border-radius:3px;font-size:.82rem;border:1px solid}
.light.ok{color:var(--ok);border-color:var(--ok)}
.light.warn{color:var(--warn);border-color:var(--warn)}
.light.bad{color:var(--bad);border-color:var(--bad);font-weight:700}
.card{background:var(--card);border:1px solid var(--rule);padding:16px 18px;margin-bottom:12px}
.body p{margin:0 0 12px;text-wrap:pretty}
.body h2{margin-top:22px}
/* 数字来源：toggletip —— 点击、回车、悬停、聚焦都能打开，键盘与读屏可用 */
button.num{font:inherit;color:var(--accent);background:none;border:0;padding:0;
border-bottom:1px dotted var(--accent);cursor:help}
button.num:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tip{white-space:normal;max-width:28rem;padding:.5rem .75rem;font-size:.8rem;line-height:1.5;color:var(--ink);
background:var(--card);border:1px solid var(--rule);box-shadow:0 2px 8px rgb(0 0 0/.15)}
@supports (position-area:block-start){[popover].tip{position-area:block-start;
position-try-fallbacks:flip-block;inset:auto;margin:0}}
.nw{white-space:nowrap}
.tbl{overflow:auto}
.tbl:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
caption{caption-side:top;text-align:left;color:var(--dim);font-size:.8rem;padding:0 0 6px}
/* 首屏以下的长表：滚到附近再排版绘制；auto 记住真实高度，避免滚动条跳动 */
.defer{content-visibility:auto;contain-intrinsic-size:auto 600px}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--rule)}
th{color:var(--dim);font-weight:600;font-size:.78rem;white-space:nowrap}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;color:var(--dim)}
/* 口径原样保留换行与缩进 —— 它本来就是分层缩进的文本 */
.ctx{white-space:pre-wrap;margin:8px 0 0;line-height:1.65;overflow-x:auto}
.grid td.hit{background:var(--accent);color:#fff;font-weight:700}
details{margin-top:8px}summary{cursor:pointer;color:var(--dim);font-size:.85rem}
.note{border-left:3px solid var(--warn);padding-left:12px;color:var(--dim);font-size:.9rem}
.note.bad{border-color:var(--bad)}
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


def _pct(v: Decimal) -> str:
    return f"{v * 100:.2f}%"


def _annotate(rendered: str, footnotes) -> str:
    """给正文里的数字挂上来源提示 —— 这是本看板最该有的交互。

    渲染后的正文已无占位符，按脚注的显示值把数字换成 toggletip 按钮，来源说明紧跟其后
    （`popover="hint"`）—— 键盘、触屏、读屏都能打开，不再只靠鼠标悬停的 title。

    **先换成私用区占位符、最后一次展开。** 此前逐个 str.replace 插入标记，后一个数字的
    显示值可能命中前一个来源说明里的日期或数值，被套进第二层标记。
    """
    out = _e(rendered)
    seen: set[str] = set()
    marks: list[tuple[str, str]] = []
    for n in footnotes:
        val = _e(n.display) if hasattr(n, "display") else None
        if not val or val in seen or val not in out:
            continue
        seen.add(val)
        tip = _e(f"{n.key}@{n.period} · 来源 {n.source_id} · "
                 f"披露日 {n.as_of.isoformat()} · {n.method}")
        out = out.replace(val, chr(0xE000 + len(marks)), 1)
        marks.append((val, tip))
    paras = [f"<p>{p.strip()}</p>" if not p.strip().startswith("##")
             else f"<h2>{p.strip().lstrip('# ').strip()}</h2>"
             for p in out.split("\n") if p.strip()]
    body = "\n".join(paras)
    for i, (val, tip) in enumerate(marks):
        sid = f"src-{i + 1}"
        mark = (f'<button type="button" class="num" interestfor="{sid}" popovertarget="{sid}" '
                f'popovertargetaction="show" aria-expanded="false">{val}</button>'
                f'<span id="{sid}" popover="hint" role="tooltip" class="tip">{tip}</span>')
        # 紧随的中文标点与数字绑在一行：按钮与文字之间有断行机会，「，」会被挤到下一行行首
        body = re.sub(re.escape(chr(0xE000 + i)) + r"([，。、；：！？）」』》%])",
                      lambda m, mk=mark: f'<span class="nw">{mk}{m.group(1)}</span>', body)
        body = body.replace(chr(0xE000 + i), mark)
    return body


def build_dashboard(
    run,
    path: Path | str,
    comps=None,
    dcf=None,
    context=None,
) -> Path:
    """context: {标题: 摘要文本}，用于呈现宏观/行业/可比的**口径**。

    口径不进看板，读者就只能看到「市占率 47%」这个结论，看不到它建立在
    19 家全覆盖之上还是 12 家残缺样本之上 —— 后者会系统性高估。
    """
    a, rec = run.audit, run.reconcile
    parts: list[str] = []

    # ── 状态灯 ────────────────────────────────────────────
    lights = []
    lights.append(("ok" if rec.ok else "bad",
                   f"三表勾稽 {'通过' if rec.ok else '失败'}"
                   f"（{rec.checks_run}/{len(rec.results)} 项）"))
    tr = a.traceability
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
    parts.append('<div class="lights">' + "".join(
        f'<span class="light {c}">{_e(t)}</span>' for c, t in lights) + "</div>")

    for w in getattr(run, "warnings", []) or []:
        parts.append(f'<div class="note bad">⚠ {_e(w)}</div>')

    # ── 分析口径 ──────────────────────────────────────────
    # 放在正文之前: 读者应当先知道结论建立在什么样本和什么分类层级上，
    # 再去读结论本身。
    if context:
        parts.append("<h2>分析口径</h2>")
        for title, text in context.items():
            if not text:
                continue
            parts.append(f'<div class="card"><b>{_e(title)}</b>'
                         f'<pre class="mono ctx">{_e(text)}</pre></div>')

    # ── 正文 ──────────────────────────────────────────────
    parts.append("<h2>研究正文</h2>")
    parts.append(f'<div class="card body">{_annotate(a.rendered, a.footnotes)}</div>')
    parts.append('<p class="mono">点击、悬停或用键盘聚焦正文中的数字，可查看其来源与披露日。</p>')

    # ── 估值 ──────────────────────────────────────────────
    if dcf is not None:
        parts.append("<h2>估值（ashare-dcf 三表模型）</h2>")
        up = dcf.upside
        parts.append(
            f'<div class="card">每股内在价值 '
            f'<b>{dcf.value_per_share:,.2f}</b> 元　现价 {dcf.price:,.2f} 元'
            + (f'　空间 <b>{up:+.1%}</b>' if up is not None else "")
            + f'<br><span class="mono">WACC {dcf.wacc:.2%}　'
            f'终值 ROIC {dcf.terminal_roic:.1%}　'
            f'稳态再投资率 {dcf.reinvest_rate:.1%}　'
            f'终值占企业价值 {dcf.terminal_share:.1%}</span>'
            f'<br><span class="mono">工作簿：{_e(dcf.workbook)}</span></div>')
        for x in dcf.notes:
            parts.append(f'<div class="note">{_e(x)}</div>')
        # 交叉核对与健全性警告**不折叠**: 它们正是「看起来算出来了」与
        # 「算对了」之间的差别。
        for x in dcf.crosscheck:
            parts.append(f'<div class="note bad">⚠ 与账本对不上：{_e(x)}</div>')
        for x in dcf.warnings:
            parts.append(f'<div class="note bad">⚠ {_e(x)}</div>')

    # ── 可比 ──────────────────────────────────────────────
    if comps is not None:
        parts.append("<h2>可比公司</h2>")
        parts.append(f'<div class="card"><pre class="mono">{_e(comps.summary())}</pre></div>')

    # ── 勾稽明细 ──────────────────────────────────────────
    parts.append("<h2>三表勾稽</h2>")
    rows = "".join(
        f"<tr><td>{'✓' if r.status=='passed' else '✗' if r.status=='failed' else '–'}"
        f"</td><td>{_e(r.name)}</td><td class='mono'>{_e(r.detail)}</td></tr>"
        for r in rec.results)
    parts.append(
        '<div class="card"><div class="tbl" tabindex="0" role="region" aria-labelledby="cap-rec">'
        '<table><caption id="cap-rec">三表勾稽明细</caption><thead><tr><th scope="col">结果</th>'
        '<th scope="col">检查项</th><th scope="col">明细</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div></div>')

    # ── 溯源 ──────────────────────────────────────────────
    fn = "".join(
        f"<tr><td class='mono'>{n.marker}</td><td>{_e(n.key)}@{_e(n.period)}</td>"
        f"<td class='mono'>{_e(n.source_id)}</td><td>{n.as_of.isoformat()}</td>"
        f"<td>{_e(n.method)}</td></tr>" for n in a.footnotes)
    parts.append(
        f"<h2>溯源</h2><div class=\"card defer\"><details open><summary>"
        f"{len(a.footnotes)} 条引用，每条可反查到原始接口响应</summary>"
        f'<div class="tbl" tabindex="0" role="region" aria-labelledby="cap-fn">'
        f'<table><caption id="cap-fn">引用溯源明细</caption><thead><tr>'
        f'<th scope="col">#</th><th scope="col">字段</th><th scope="col">source_id</th>'
        f'<th scope="col">披露日</th><th scope="col">方法</th></tr></thead>'
        f"<tbody>{fn}</tbody></table></div></details></div>")

    doc = (f"<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           f"<meta name=\"color-scheme\" content=\"light dark\">"
           f"<meta name=\"text-scale\" content=\"scale\">"
           f"<title>{_e(run.code)} {_e(run.period)} 投研看板</title>"
           f"<style>{_CSS}</style></head><body><div class=\"wrap\">"
           f"<header><h1>{_e(run.code)}　{_e(run.period)}</h1>"
           f"<div class=\"sub\">分析时点 {run.as_of.isoformat()}　"
           f"本看板所有数字均可反查至原始接口响应</div></header><main>"
           + "".join(parts) + f"</main></div><script>{_JS}</script></body></html>\n")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p


