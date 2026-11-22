"""茅台实跑验收（2026-11-22）暴露的 agent 层问题。不联网。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from ir_agent.sources import openweb
from ir_agent.sources.external import Corpus

AS_OF = date(2026, 11, 20)


def _fake_fetch(monkeypatch):
    def fake(url, **k):
        return openweb.Fetched(final_url=url, text=f"正文 {url}", meta_published=date(2026, 10, 1))
    monkeypatch.setattr(openweb, "fetch", fake)


def _prompt(code, module="value_chain"):
    return openweb.module_prompt(module, company="X", code=code, industry="Y", as_of=AS_OF,
                                 context={}, slots=openweb.module_slots(module, code))


# ── 问题 1、6：转引的数据给原发布页；同一份文件不给转载 ──────────────────
@pytest.mark.parametrize("code", ["600519"])
def test_prompt_asks_for_original_release(code):
    p = _prompt(code)
    assert "转引" in p and "原发布页" in p and "不要给转载" in p
    assert "同一份文件" in p


# ── 问题 5：HTML 与 PDF 两个市场都接受 ─────────────────────────────────
@pytest.mark.parametrize("code", ["600519"])
def test_prompt_accepts_pdf(code):
    p = _prompt(code)
    assert "不要 PDF" not in p and "PDF 都可以" in p


# ── 问题 2：文档记下所属主题，落盘读回不丢；清单按模块 · 主题标注 ──────────
def test_slot_persisted_and_roundtrip(monkeypatch, tmp_path):
    _fake_fetch(monkeypatch)
    raw = "候选: 竞争格局与份额 | 标题1 | https://example1.org/a1 | 某机构 | 2026-10-02"
    res = openweb.gather_module(lambda p: raw, "strategy", code="600519", company="X", industry="Y",
                                as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert [d.slot for d in res.docs] == ["竞争格局与份额"]
    openweb.save(res, tmp_path / "ow.json")
    back = openweb.load(tmp_path / "ow.json", Corpus(as_of=AS_OF))
    assert [d.slot for d in back.docs] == ["竞争格局与份额"]


def test_manifest_labels_module_and_slot(monkeypatch):
    from ir_agent.sources import manifest
    _fake_fetch(monkeypatch)
    raw = "\n".join([
        "候选: 竞争格局与份额 | 标题2 | https://example2.org/a2 | 机构乙 | 2026-10-02",
        "候选: 公司重大动态 | 标题1 | https://example1.org/a1 | 机构甲 | 2026-10-02"])
    c = Corpus(as_of=AS_OF)
    openweb.gather_module(lambda p: raw, "strategy", code="600519", company="X", industry="Y",
                          as_of=AS_OF, corpus=c)
    rows = [r for r in manifest.collect_rows(SimpleNamespace(_facts={}), corpus=c) if r.kind == "外部文档"]
    assert [r.note for r in rows] == ["机构乙｜战略分析 · 竞争格局与份额", "机构甲｜战略分析 · 公司重大动态"]


# ── 问题 3：落点变量可以是利息与投资收益 ───────────────────────────────
def test_macro_landing_includes_interest_income():
    from ir_agent import macro_view
    assert "利息与投资收益" in macro_view.PROMPT


# ── 问题 4：跟踪指标不取账本已有的序列；读数标出处日期 ─────────────────
def test_macro_prompt_excludes_ledger_series_from_tracks():
    from ir_agent import macro_view
    assert "账本里已有" in macro_view.PROMPT and "不要列为跟踪指标" in macro_view.PROMPT


def test_track_summary_shows_source_dates(monkeypatch):
    from ir_agent import macro_track
    from ir_agent.sources import conclusions

    art = SimpleNamespace(opened=True, link="https://stats.example.org/r", publisher="统计局",
                          tier=openweb.Tier.OFFICIAL, published=date(2026, 9, 24), text="读数")
    got = SimpleNamespace(items=[SimpleNamespace(claim="最新读数", articles=[art])])
    monkeypatch.setattr(conclusions, "search", lambda *a, **k: got)
    view = SimpleNamespace(tracks=[SimpleNamespace(indicator="某指标", driver="某传导", falsifier="回升")])
    res = macro_track.track(None, view, industry="Y", as_of=AS_OF, corpus=Corpus(as_of=AS_OF), us=False)
    assert res.items[0].published == {"https://stats.example.org/r": "2026-09-24"}
    assert "出处：https://stats.example.org/r（发布 2026-09-24）" in macro_track.summary(res)


def test_track_item_loads_old_json_without_dates(tmp_path):
    from ir_agent import macro_track
    p = tmp_path / "t.json"
    p.write_text('{"items": [{"indicator": "a", "driver": "b", "falsifier": "c", "conclusion": "x",'
                 ' "sources": ["https://e.org/1"]}], "docs": []}', encoding="utf-8")
    res = macro_track.load(p, None)
    assert res.items[0].published == {}
    assert "出处：https://e.org/1\n" in macro_track.summary(res) + "\n"


# ── 问题 7：多行清单条目的续行缩进在标题之下 ──────────────────────────
def test_multiline_skipped_entry_indents_under_header():
    from ir_agent import research
    txt = research._format_skipped("开放网络资料采集（宏观）：\n  · 1 篇抓取失败\n    - 原因")
    head, *rest = txt.split("\n")
    assert head == "    · 开放网络资料采集（宏观）："
    assert rest == ["        · 1 篇抓取失败", "          - 原因"]
