"""定性产物的只读阅读版（用户 2026-11-22 选方案 A）：占位符换成账本数值 + 脚注，源文件不动。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

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
    text = "集中度高（[[industry.cr5@2025FY]]），营收 [[revenue@2025FY]]；再提一次 [[industry.cr5@2025FY]] 的水平"
    body, notes = readable.render_readable(text, _ledger(), AS_OF)
    assert "[[" not in body and "81.72%[^1]" in body and "393.75 亿元[^2]" in body
    assert sorted(notes) == ["1", "2"]                   # 同一引用只出一条脚注
    out = readable.with_notes(body, notes)
    assert out.count("[^1]:") == 1 and "## 数字出处" in out


def test_render_keeps_unresolved_and_lookahead_marked():
    body, _ = readable.render_readable("缺 [[nope.key@2025FY]]，未来 [[future@2026FY]]", _ledger(), AS_OF)
    assert "[[nope.key@2025FY]]（账本未取到）" in body
    assert "[[future@2026FY]]（账本未取到）" in body


@pytest.mark.parametrize("tail", ["为 81.72%", "81.72%", "（81.72%）", "约81.72%", "= 81.7%"])
def test_后面就是同一个数时显示中文名(tail):
    body, _ = readable.render_readable(f"[[industry.cr5@2025FY]] {tail}，偏高", _ledger(), AS_OF)
    assert body.startswith("行业 CR5（2025FY）[^1]") and body.count("81.7") == 1


def test_金额带单位重复时也只显示一次():
    body, _ = readable.render_readable("[[revenue@2025FY]] 393.75亿，增长", _ledger(), AS_OF)
    assert body.count("393.75") == 1


def test_后面是别的数时仍显示数值():
    body, _ = readable.render_readable("[[industry.cr5@2025FY]] 高于 60%", _ledger(), AS_OF)
    assert body.startswith("81.72%[^1] 高于 60%")
    body, _ = readable.render_readable("由 [[industry.cr5@2025FY]] 升至 91.72%", _ledger(), AS_OF)
    assert "81.72%[^1]" in body


@pytest.mark.parametrize("text", ["不作依据。[[industry.cr5@2025FY]]", "[[industry.cr5@2025FY]]", "甲 | [[industry.cr5@2025FY]] | 乙"])
def test_孤零零的数补中文名(text):
    body, _ = readable.render_readable(text, _ledger(), AS_OF)
    assert "行业 CR5 81.72%[^1]" in body


def test_并列占位符照常显示数值():
    body, _ = readable.render_readable("见 [[industry.cr5@2025FY]]、[[revenue@2025FY]] 两项", _ledger(), AS_OF)
    assert "见 81.72%[^1]、393.75 亿元[^2] 两项" in body


def test_没有中文名时不硬造():
    led = _ledger()
    led.put(Fact(key="zzz.unknown", value=Decimal("5"), unit="x", period="2025FY", as_of=date(2026, 4, 30),
                 source_id="q_1", method=Method.COMPUTED))
    body, notes = readable.render_readable("见。[[zzz.unknown@2025FY]]", led, AS_OF)
    assert body == "见。5.00x[^1]" and "zzz.unknown@2025FY" in notes["1"]


def test_脚注中文并保留内部键():
    _, notes = readable.render_readable("集中度 [[industry.cr5@2025FY]] 高", _ledger(), AS_OF)
    assert notes["1"].startswith("行业 CR5（2025FY）· 来源 账本计算 · 披露日 2026-04-30")
    assert "industry.cr5@2025FY" in notes["1"] and "calc_cr5" in notes["1"]


def test_inline_has_values_without_footnotes():
    assert readable.render_inline("见 [[industry.cr5@2025FY]]", _ledger(), AS_OF) == "见 81.72%"


def test_write_readables_only_for_existing_sources(tmp_path):
    src = "传导: 利率 | ... [[industry.cr5@2025FY]]"
    (tmp_path / "macro_view.md").write_text(src, encoding="utf-8")
    written = readable.write_readables(tmp_path, _ledger(), AS_OF)
    f = tmp_path / "macro_view_阅读版.md"
    assert written == [f]
    body = f.read_text(encoding="utf-8")
    assert "> 只读阅读版" in body and "修改请改 `macro_view.md`" in body and "81.72%" in body
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


@pytest.mark.parametrize("text", ["行业 CR5 [[industry.cr5@2025FY]] 为 81.72%", "行业 CR5（[[industry.cr5@2025FY]]，81.72%）"])
def test_前面已写中文名时不重复(text):
    body, _ = readable.render_readable(text, _ledger(), AS_OF)
    assert body.count("行业 CR5") == 1 and body.count("81.72") == 1 and "[^1]" in body


def test_前面已写中文名且后面没有同值时显示数值():
    body, _ = readable.render_readable("行业 CR5 [[industry.cr5@2025FY]]，偏高", _ledger(), AS_OF)
    assert body == "行业 CR5 81.72%[^1]，偏高"
