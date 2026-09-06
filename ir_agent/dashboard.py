"""HTML 看板 —— 单文件、自包含、离线可开。

**「可互动」要用在刀刃上。** 本项目的核心主张是「每个数字可反查」，
所以最该做的交互是**悬停任意数字看它的来源与披露日**；其次是敏感性矩阵。
堆一屏图表不是交互，是装饰。

三条硬约束:
  · 自包含 —— 无外部脚本/样式/字体，离线可开
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

_CSS = """
:root{--bg:#f6f7f8;--card:#fff;--ink:#141a22;--dim:#6b7885;--rule:#dde3e8;
--ok:#2c6e5c;--warn:#8f6c1e;--bad:#b8382a;--accent:#2f5d8a}
@media(prefers-color-scheme:dark){:root{--bg:#0e141a;--card:#161e26;--ink:#e3e8ed;
--dim:#8b98a5;--rule:#2a3542;--ok:#5aaf95;--warn:#c6a353;--bad:#e5705c;--accent:#7aa8d4}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:1.7rem;margin:0 0 4px}
h2{font-size:1.1rem;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--rule)}
.sub{color:var(--dim);font-size:.9rem;margin-bottom:20px}
.lights{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}
.light{padding:5px 11px;border-radius:3px;font-size:.82rem;border:1px solid}
.light.ok{color:var(--ok);border-color:var(--ok)}
.light.warn{color:var(--warn);border-color:var(--warn)}
.light.bad{color:var(--bad);border-color:var(--bad);font-weight:700}
.card{background:var(--card);border:1px solid var(--rule);padding:16px 18px;margin-bottom:12px}
.body p{margin:0 0 12px}
.body h2{margin-top:22px}
.num{border-bottom:1px dotted var(--accent);cursor:help;color:var(--accent)}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--rule)}
th{color:var(--dim);font-weight:600;font-size:.78rem}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;color:var(--dim)}
.grid td.hit{background:var(--accent);color:#fff;font-weight:700}
details{margin-top:8px}summary{cursor:pointer;color:var(--dim);font-size:.85rem}
.note{border-left:3px solid var(--warn);padding-left:12px;color:var(--dim);font-size:.9rem}
.note.bad{border-color:var(--bad)}
"""


def _e(x) -> str:
    return _html.escape(str(x), quote=True)


def _pct(v: Decimal) -> str:
    return f"{v * 100:.2f}%"


def _annotate(rendered: str, footnotes) -> str:
    """给正文里的数字挂上来源提示 —— 这是本看板最该有的交互。

    渲染后的正文已无占位符，按脚注的显示值回填 title 属性。
    """
    from ir_agent.citation import format_value
    out = _e(rendered)
    seen: set[str] = set()
    for n in footnotes:
        val = _e(n.display) if hasattr(n, "display") else None
        if not val or val in seen:
            continue
        seen.add(val)
        tip = _e(f"{n.key}@{n.period} · 来源 {n.source_id} · "
                 f"披露日 {n.as_of.isoformat()} · {n.method}")
        out = out.replace(val, f'<span class="num" title="{tip}">{val}</span>', 1)
    paras = [f"<p>{p.strip()}</p>" if not p.strip().startswith("##")
             else f"<h2>{_e(p.strip().lstrip('# ').strip())}</h2>"
             for p in out.split("\n") if p.strip()]
    return "\n".join(paras)


def build_dashboard(
    run,
    path: Path | str,
    comps=None,
    route=None,
    assumptions=None,
    valuation=None,
    sensitivity=None,
) -> Path:
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
    if assumptions is not None:
        lights.append(("ok" if assumptions.approved else "bad",
                       "估值假设已审核" if assumptions.approved
                       else "估值假设未审核 —— 不得据此出具结论"))
    parts.append('<div class="lights">' + "".join(
        f'<span class="light {c}">{_e(t)}</span>' for c, t in lights) + "</div>")

    for w in getattr(run, "warnings", []) or []:
        parts.append(f'<div class="note bad">⚠ {_e(w)}</div>')

    # ── 正文 ──────────────────────────────────────────────
    parts.append("<h2>研究正文</h2>")
    parts.append(f'<div class="card body">{_annotate(a.rendered, a.footnotes)}</div>')
    parts.append('<p class="mono">悬停正文中的数字可查看其来源与披露日。</p>')

    # ── 估值 ──────────────────────────────────────────────
    if route is not None or assumptions is not None or valuation is not None:
        parts.append("<h2>估值</h2>")
        if route is not None:
            parts.append(f'<div class="card">估值方法：<b>{_e(route.method.value)}</b>'
                         f'<br><span class="mono">{_e(route.reason)}</span></div>')
        if assumptions is not None:
            rows = "".join(
                f"<tr><td>{_e(k)}</td><td class='n'>{_e(v)}</td>"
                f"<td class='mono'>{_e(assumptions.basis.get(bk, ''))}</td></tr>"
                for k, v, bk in [
                    ("WACC", _pct(assumptions.wacc), "wacc"),
                    ("永续增长率", _pct(assumptions.terminal_growth), "terminal_growth"),
                    ("预测期", f"{len(assumptions.growth_rates)} 年", "growth_rates")])
            state = (f"已审核：{_e(assumptions.reviewed_by)} · {assumptions.reviewed_at}"
                     if assumptions.approved
                     else "<b>未审核 —— 不得据此出具估值结论</b>")
            parts.append(f'<div class="card"><div class="note">{state}</div>'
                         f"<table><tr><th>假设</th><th>取值</th><th>依据</th></tr>"
                         f"{rows}</table></div>")
        if valuation is not None:
            parts.append(
                f'<div class="card">每股价值 <b>{valuation.value_per_share:,.2f}</b> 元'
                f'　终值占企业价值 {valuation.terminal_share:.1%}</div>')
        if sensitivity is not None:
            parts.append(_sens_table(sensitivity))

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
    parts.append(f'<div class="card"><table>{rows}</table></div>')

    # ── 溯源 ──────────────────────────────────────────────
    fn = "".join(
        f"<tr><td class='mono'>{n.marker}</td><td>{_e(n.key)}@{_e(n.period)}</td>"
        f"<td class='mono'>{_e(n.source_id)}</td><td>{n.as_of.isoformat()}</td>"
        f"<td>{_e(n.method)}</td></tr>" for n in a.footnotes)
    parts.append(
        f"<h2>溯源</h2><div class='card'><details open><summary>"
        f"{len(a.footnotes)} 条引用，每条可反查到原始接口响应</summary>"
        f"<table><tr><th>#</th><th>字段</th><th>source_id</th><th>披露日</th>"
        f"<th>方法</th></tr>{fn}</table></details></div>")

    doc = (f"<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           f"<title>{_e(run.code)} {_e(run.period)} 投研看板</title>"
           f"<style>{_CSS}</style></head><body><div class=\"wrap\">"
           f"<h1>{_e(run.code)}　{_e(run.period)}</h1>"
           f"<div class=\"sub\">分析时点 {run.as_of.isoformat()}　"
           f"本看板所有数字均可反查至原始接口响应</div>"
           + "".join(parts) + "</div></body></html>\n")

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding="utf-8")
    return p


def _sens_table(sens: dict) -> str:
    """敏感性矩阵。当前假设所在格高亮 —— 让读者一眼看到结论有多脆弱。"""
    waccs, growths, grid = sens["waccs"], sens["growths"], sens["grid"]
    hit = sens.get("current")
    head = "".join(f"<th class='n'>{_pct(g)}</th>" for g in growths)
    rows = ""
    for i, w in enumerate(waccs):
        cells = ""
        for j, v in enumerate(grid[i]):
            cls = "n hit" if hit == (i, j) else "n"
            cells += f"<td class='{cls}'>{v:,.0f}</td>"
        rows += f"<tr><th class='n'>{_pct(w)}</th>{cells}</tr>"
    return (f"<div class='card'><table class='grid'>"
            f"<tr><th>WACC ＼ 永续增长</th>{head}</tr>{rows}</table>"
            f"<p class='mono'>每股价值（元）。高亮格为当前假设。</p></div>")
