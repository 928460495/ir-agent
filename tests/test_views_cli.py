"""`--views`：不调模型重新生成阅读版与看板（spec 2026-11-25-readable-layout §4.7）。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ir_agent import __main__ as cli
from ir_agent import full
from ir_agent.audit import AuditResult
from ir_agent.ledger import Fact, FactLedger, Method

AS_OF = date(2026, 11, 20)


@pytest.fixture
def env(tmp_path, monkeypatch):
    led = FactLedger()
    led.put(Fact(key="industry.cr5", value=Decimal("0.8172"), unit="ratio", period="2025FY",
                 as_of=date(2026, 4, 30), source_id="calc_cr5", method=Method.COMPUTED))
    r = SimpleNamespace(code="600519", period="2025FY", as_of=AS_OF, ledger=led, audit=AuditResult(rendered="草稿"),
                        warnings=[], reconcile=SimpleNamespace(ok=True, checks_run=0, results=[]), store=None,
                        verdicts=None, peer_set=SimpleNamespace(target_name="贵州茅台"), sec_meta=None)
    seen: list[str] = []
    monkeypatch.setattr(full, "_analyse_facts", lambda *a, **k: seen.append("facts") or ({}, {}, []))
    monkeypatch.setattr(full, "_restore_corpus", lambda *a, **k: seen.append("corpus") or [])
    monkeypatch.setattr(full, "make_client", lambda *a, **k: pytest.fail("不该调模型"))
    (tmp_path / "state.json").write_text(json.dumps({"md": {"战略": "摘要 [[industry.cr5@2025FY]]"}, "pr": {}}, ensure_ascii=False),
                                         encoding="utf-8")
    (tmp_path / "strategy.md").write_text("护城河: 转换成本 | 宽 | 集中 [[industry.cr5@2025FY]]", encoding="utf-8")
    return r, tmp_path, seen


def test_写出阅读版与看板_不改状态文件(env):
    r, out, seen = env
    before = (out / "state.json").read_bytes()
    got = {p.name for p in full.run_views("600519", r, AS_OF, out)}
    assert got == {"strategy_阅读版.md", "dashboard.html"} and seen == ["facts", "corpus"]
    assert (out / "state.json").read_bytes() == before
    t = (out / "strategy_阅读版.md").read_text(encoding="utf-8")
    assert "# 贵州茅台 600519 · 战略分析" in t and "81.72%" in t


def test_已有简报时看板正文用简报并挂来源(env):
    r, out, _ = env
    (out / "brief.md").write_text("## 投资要点\n\n集中度 81.72%，再提 81.72%。\n\n---\n\n## 数据来源\n\n"
                                  "| 数值 | 字段@期间 | 来源 | 披露日 | 取得方式 |\n| --- | --- | --- | --- | --- |\n"
                                  "| 81.72% | industry.cr5@2025FY | calc_cr5 | 2026-04-30 | computed |\n", encoding="utf-8")
    full.run_views("600519", r, AS_OF, out)
    h = (out / "dashboard.html").read_text(encoding="utf-8")
    body = h.split('id="sec-body"')[1].split("</section>")[0]
    assert "投资要点" in body and body.count('class="num"') == 2 and "草稿" not in body and "数据来源" not in body


def test_已出研报的目录不覆盖看板(env):
    """研报看板含估值卡片，不重跑建模就重出不了 —— 只更新阅读版，并说明。"""
    r, out, _ = env
    (out / "report.md").write_text("## 投资要点\n\n甲", encoding="utf-8")
    (out / "dashboard.html").write_text("旧", encoding="utf-8")
    notes: list[str] = []
    got = {p.name for p in full.run_views("600519", r, AS_OF, out, notes=notes)}
    assert "report_阅读版.md" in got and "dashboard.html" not in got
    assert (out / "dashboard.html").read_text(encoding="utf-8") == "旧" and any("看板" in n for n in notes)


def test_命令行不要求签名_没有状态文件报错(tmp_path, capsys):
    a = cli.build_parser().parse_args(["600519", "--year", "2025", "--views", str(tmp_path)])
    assert "state.json" in cli._check_args(a)
    (tmp_path / "state.json").write_text("{}", encoding="utf-8")
    assert cli._check_args(a) == ""
    assert cli.main(["600519", "--year", "2025", "--views", str(tmp_path / "没有")]) == 2


def test_同一显示值对应两个指标时两个来源都列出():
    from ir_agent.citation import Footnote
    from ir_agent.dashboard import _marked
    f = lambda n, k: Footnote(n, k, "2025FY", "s", AS_OF, "reported", display="200.00 元")
    text, notes = _marked(AuditResult(rendered="净利润 200.00 元，另 1200.00 元", footnotes=[f(1, "net_profit"), f(2, "net_profit_attr_parent")]))
    assert text.count("[^") == 1 and "1200.00 元[^" not in text
    only = next(v for k, v in notes.items() if f"[^{k}]" in text)
    assert "net_profit@2025FY" in only and "net_profit_attr_parent@2025FY" in only


def test_取数失败要说出来(env, monkeypatch):
    r, out, _ = env
    monkeypatch.setattr(full, "_analyse_facts", lambda *a, **k: ({}, {}, ["宏观（某序列取数失败）"]))
    notes: list[str] = []
    full.run_views("600519", r, AS_OF, out, notes=notes)
    assert "宏观（某序列取数失败）" in notes
