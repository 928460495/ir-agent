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
/* 口径原样保留换行与缩进 —— 它本来就是分层缩进的文本 */
.ctx{white-space:pre-wrap;margin:8px 0 0;line-height:1.65;overflow-x:auto}
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
    parts.append('<p class="mono">悬停正文中的数字可查看其来源与披露日。</p>')

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


