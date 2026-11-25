"""用已有产物目录验收阅读版与看板的排版（spec 2026-11-25-readable-layout §6）。

    python scripts/check_views.py out/*            # 有问题退出码 1

V1 一字不丢；V2 看板正文没有可见的 Markdown 符号；V3 有标题、段落不粘连。历史产物含第三方文字，
不进仓库 —— 测试用合成样例，真实产物在本机跑这个脚本。账本用空账本：占位符照原样显示，不影响核对。
"""

from __future__ import annotations

import html as _html
import json
import re
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ir_agent import fieldview, full, mdview, readable          # noqa: E402
from ir_agent.dashboard import build_dashboard                  # noqa: E402
from ir_agent.ledger import FactLedger                          # noqa: E402

_PLAIN = re.compile(r"^(?!#|>|\||\s*[-*]\s|\s*\d{1,2}\.\s|```|\[\^)\S")


def check_text(src: str, out: str) -> list[str]:
    """V1、V3。"""
    bad = [f"丢失：{x[:40]}" for x in fieldview.lost_fine(src, out)]
    body = out.split("\n## 数字出处")[0]
    if not re.search(r"^#{1,6} ", body, re.M):
        bad.append("没有标题")
    code = False
    prev = ""
    for ln in body.split("\n"):
        if ln.startswith("```"):
            code = not code
        if not code and _PLAIN.match(ln) and _PLAIN.match(prev):
            bad.append(f"段落粘连：{ln[:40]}")
        prev = "" if code else ln
    return bad


def check_html(h: str) -> list[str]:
    """V2：看得见的文字里不该有 Markdown 符号。"""
    h = re.sub(r"<(style|script|pre|code)\b.*?</\1>|<span [^>]*role=\"tooltip\".*?</span>", "", h, flags=re.S)
    bad = []
    for blk in re.findall(r"<(?:p|li|td|th|h[1-6]|summary)\b[^>]*>(.*?)(?=</(?:p|li|td|th|h[1-6]|summary)>|<ul|<ol)", h, re.S):
        t = _html.unescape(re.sub(r"<[^>]+>", "", blk)).strip()
        if "**" in t or t.startswith(("- ", "* ", "|", "#")) or re.search(r"\[\^\w+\]", t):
            bad.append(f"残留符号：{t[:50]}")
    return bad


def check_dir(d: Path) -> tuple[int, int, list[str]]:
    """一个产物目录 → (阅读版份数, 看板份数, 问题)。"""
    d = Path(d)
    st = json.loads((d / "state.json").read_text(encoding="utf-8")) if (d / "state.json").exists() else {}
    as_of, led, notes = date.fromisoformat(st.get("as_of") or "2026-01-01"), FactLedger(), []
    views = readable.render_views(d, led, as_of, title=d.name, notes=notes)
    bad = [f"{d.name}：{n}" for n in notes]
    for stem, name, _label, _b in readable.VIEWS:
        if stem not in views:
            continue
        src = (d / name).read_text(encoding="utf-8")
        if name.endswith(".json"):
            src = "\n".join(str(x.get(k) or "") for k, xs in (("note", json.loads(src).get("balances")),
                                                             ("claim", json.loads(src).get("evidence"))) for x in xs or [])
        bad += [f"{d.name}/{name}：{x}" for x in check_text(src, views[stem][0])]
    dash = 0
    if st:
        body = next((d / x for x in ("report.md", "brief.md") if (d / x).exists()), None)
        r = SimpleNamespace(code=d.name, period=st.get("period", ""), as_of=as_of, ledger=led, warnings=[],
                            audit=full._audit_from_md(body.read_text(encoding="utf-8") if body else "（无正文）"),
                            reconcile=SimpleNamespace(ok=True, checks_run=0, results=[]))
        tmp = d / "._check_views.html"
        try:
            build_dashboard(r, tmp, context={k: v for k, v in (st.get("md") or {}).items() if isinstance(v, str)}, views=views)
            bad += [f"{d.name}/看板：{x}" for x in check_html(tmp.read_text(encoding="utf-8"))]
        finally:
            tmp.unlink(missing_ok=True)
        dash = 1
    return len(views), dash, bad


def main(argv: list[str]) -> int:
    n = m = 0
    bad: list[str] = []
    for a in argv:
        if Path(a).is_dir():
            v, h, b = check_dir(Path(a))
            n, m, bad = n + v, m + h, bad + b
    for x in bad[:60]:
        print("✗", x)
    print(f"阅读版 {n} 份、看板 {m} 份，问题 {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
