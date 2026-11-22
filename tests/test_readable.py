"""定性产物的只读阅读版（用户 2026-11-22 选方案 A）：占位符换成账本数值 + 脚注，源文件不动。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent import readable
from ir_agent.ledger import Fact, FactLedger, Method

AS_OF = date(2026, 11, 20)


def _ledger():
    led = FactLedger()
    led.put(Fact(key="industry.cr5", value=Decimal("0.8172"), unit="ratio", period="2025FY",
                 as_of=date(2026, 4, 30), source_id="calc_cr5", method=Method.COMPUTED))
    led.put(Fact(key="revenue", value=Decimal("39374532100"), unit="元", currency="CNY", period="2025FY",
                 as_of=date(2026, 3, 30), source_id="eastmoney_a1", method=Method.REPORTED))
    # 分析时点之后才披露的 —— 阅读版也不能提前看
    led.put(Fact(key="future", value=Decimal("1"), unit="ratio", period="2026FY",
                 as_of=date(2027, 3, 30), source_id="x", method=Method.REPORTED))
    return led


def test_render_replaces_placeholders_and_adds_footnotes():
    text = "集中度高（[[industry.cr5@2025FY]]），营收 [[revenue@2025FY]]；再提一次 [[industry.cr5@2025FY]]"
    out = readable.render_readable(text, _ledger(), AS_OF)
    assert "[[" not in out.split("\n## 数字出处")[0]
    assert "81.72%[^1]" in out and "393.75 亿元[^2]" in out
    assert out.count("[^1]:") == 1                       # 同一引用只出一条脚注
    assert "[^1]: industry.cr5@2025FY · 来源 calc_cr5 · 披露日 2026-04-30 · computed" in out


def test_render_keeps_unresolved_and_lookahead_marked():
    text = "缺 [[nope.key@2025FY]]，未来 [[future@2026FY]]"
    out = readable.render_readable(text, _ledger(), AS_OF)
    assert "[[nope.key@2025FY]]（账本未取到）" in out
    assert "[[future@2026FY]]（账本未取到）" in out


def test_inline_has_values_without_footnotes():
    assert readable.render_inline("见 [[industry.cr5@2025FY]]", _ledger(), AS_OF) == "见 81.72%"


def test_write_readables_only_for_existing_sources(tmp_path):
    src = "传导: 利率 | ... [[industry.cr5@2025FY]]"
    (tmp_path / "macro_view.md").write_text(src, encoding="utf-8")
    written = readable.write_readables(tmp_path, _ledger(), AS_OF)
    f = tmp_path / "macro_view_阅读版.md"
    assert written == [f]
    body = f.read_text(encoding="utf-8")
    assert body.startswith("> 只读阅读版") and "修改请改 `macro_view.md`" in body and "81.72%" in body
    assert (tmp_path / "macro_view.md").read_text(encoding="utf-8") == src      # 源文件不动
    assert not (tmp_path / "strategy_阅读版.md").exists()


def test_write_readables_removes_stale_copy(tmp_path):
    (tmp_path / "strategy_阅读版.md").write_text("旧", encoding="utf-8")
    readable.write_readables(tmp_path, _ledger(), AS_OF)
    assert not (tmp_path / "strategy_阅读版.md").exists()


# ── 接入：每次生成看板时写阅读版；看板拿到的是数值，state.json 里的原文不动 ────────
def test_write_dashboard_writes_readables_and_renders_context(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ir_agent import full
    got = {}
    monkeypatch.setattr(full, "build_dashboard", lambda r, path, context=None, **k: got.update(ctx=context))
    monkeypatch.setattr(full, "build_workbook", lambda *a, **k: None)
    (tmp_path / "strategy.md").write_text("集中度 [[industry.cr5@2025FY]]", encoding="utf-8")
    r = SimpleNamespace(ledger=_ledger(), as_of=AS_OF, period="2025FY", verdicts=None)
    md = {"战略分析": "集中度 [[industry.cr5@2025FY]]"}
    arts: dict = {}
    full._write_dashboard(r, tmp_path, md, "600519", arts)
    assert got["ctx"] == {"战略分析": "集中度 81.72%"}
    assert md == {"战略分析": "集中度 [[industry.cr5@2025FY]]"}            # 原文不动
    assert arts["战略分析（阅读版）"] == str(tmp_path / "strategy_阅读版.md")
    assert (tmp_path / "strategy_阅读版.md").exists()


def test_valuation_dashboard_also_uses_readable_views():
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_valuation)
    assert "_readable_views(" in src and "context=md" not in src
