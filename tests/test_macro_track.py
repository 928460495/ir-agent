"""宏观跟踪指标定向检索（spec 2026-11-21-staged-collection §4）。不联网（monkeypatch conclusions.search）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from ir_agent import macro_track as MT
from ir_agent import macro_view as MV
from ir_agent.sources import conclusions
from ir_agent.sources.external import Corpus, Tier

AS_OF = date(2026, 11, 20)


def _view(n):
    return MV.MacroView(tracks=[MV.Track(f"指标{i}", f"事件{i}", f"证伪{i}") for i in range(n)])


def _fake_search(monkeypatch, asked, fail=()):
    def fake(client, label, key, industry, as_of, us=False):
        asked.append(label)
        if label in fail:
            raise RuntimeError("检索失败")
        art = conclusions.Article(no="1", url=f"https://stats.example/{len(asked)}", publisher="统计局",
                                  published=date(2026, 10, 15), tier=Tier.OFFICIAL, opened=True,
                                  text=f"{label} 同比增长", final_url=f"https://stats.example/{len(asked)}")
        return conclusions.Searched(items=[conclusions.Conclusion(key, f"{label} 最新同比回升", [art])], dropped=[])
    monkeypatch.setattr(conclusions, "search", fake)


def test_track_caps_at_four(monkeypatch):
    asked = []
    _fake_search(monkeypatch, asked)
    res = MT.track(object(), _view(6), industry="某行业", as_of=AS_OF, corpus=Corpus(as_of=AS_OF), us=False)
    assert asked == [f"指标{i}" for i in range(MV.MACRO_TRACK_MAX)]
    assert res.truncated == ["指标4", "指标5"]


def test_track_uses_conclusions_search_and_ingests(monkeypatch):
    asked = []
    _fake_search(monkeypatch, asked)
    c = Corpus(as_of=AS_OF)
    res = MT.track(object(), _view(2), industry="某行业", as_of=AS_OF, corpus=c, us=False)
    assert [i.conclusion for i in res.items] == ["指标0 最新同比回升", "指标1 最新同比回升"]
    assert {d.module for d in c.docs} == {"macro_track"} and len(c.docs) == 2
    assert res.items[0].sources == ["https://stats.example/1"]


def test_track_failure_is_noted(monkeypatch):
    asked = []
    _fake_search(monkeypatch, asked, fail=("指标0",))
    res = MT.track(object(), _view(2), industry="x", as_of=AS_OF, corpus=Corpus(as_of=AS_OF), us=False)
    assert res.failed and res.failed[0].startswith("指标0")
    assert res.items[1].conclusion


def test_track_reuse_and_delta(monkeypatch, tmp_path):
    asked = []
    _fake_search(monkeypatch, asked)
    v1 = MV.MacroView(tracks=[MV.Track("A", "e", "f"), MV.Track("B", "e", "f")])
    first = MT.track(object(), v1, industry="x", as_of=AS_OF, corpus=Corpus(as_of=AS_OF), us=False)
    MT.save(first, tmp_path / MT.TRACK_JSON)
    done = MT.load(tmp_path / MT.TRACK_JSON, Corpus(as_of=AS_OF))
    asked.clear()
    v2 = MV.MacroView(tracks=[MV.Track("B", "e", "f"), MV.Track("C", "e", "f")])
    res = MT.track(object(), v2, industry="x", as_of=AS_OF, corpus=Corpus(as_of=AS_OF), us=False, done=done)
    assert asked == ["C"] and [i.indicator for i in res.items] == ["B", "C"] and res.removed == ["A"]
    assert "A" in MT.summary(res) and "不再展示" in MT.summary(res)


def test_save_load_roundtrip_reingests_docs(monkeypatch, tmp_path):
    _fake_search(monkeypatch, [])
    res = MT.track(object(), _view(1), industry="x", as_of=AS_OF, corpus=Corpus(as_of=AS_OF), us=False)
    MT.save(res, tmp_path / MT.TRACK_JSON)
    c = Corpus(as_of=AS_OF)
    back = MT.load(tmp_path / MT.TRACK_JSON, c)
    assert back.items[0].conclusion == res.items[0].conclusion and [d.module for d in c.docs] == ["macro_track"]
    assert MT.load(tmp_path / "none.json", Corpus(as_of=AS_OF)) is None


def test_macro_summary_has_track_section(monkeypatch):
    _fake_search(monkeypatch, [], fail=("指标1",))
    res = MT.track(object(), _view(2), industry="x", as_of=AS_OF, corpus=Corpus(as_of=AS_OF), us=False)
    s = MT.summary(res)
    assert s.startswith("  跟踪指标最新读数") and "指标0 最新同比回升" in s and "未取到" in s
    assert "证伪0" in s and "已证伪" not in s and "未证伪" not in s


# ── 接线 ──────────────────────────────────────────────────────

from ir_agent import full   # noqa: E402


def _stub(monkeypatch, macro_md="宏观"):
    monkeypatch.setattr(full, "_collect_module", lambda *a, **k: [])
    monkeypatch.setattr(full, "_business_brief", lambda code, r: "")
    monkeypatch.setattr(full, "_industry_path", lambda code: "某行业")
    monkeypatch.setattr(full, "make_client", lambda **k: SimpleNamespace(total_cost_usd=0.0))
    monkeypatch.setattr(full, "_load_strategy", lambda *a, **k: ("战略", "战略", ""))
    monkeypatch.setattr(full, "_load_value_chain", lambda *a, **k: ("产业链", "产业链", ""))


def _run(tmp_path, client="client"):
    md, pr, skipped, stops = {}, {"宏观": ""}, [], []
    r = SimpleNamespace(code="600519", quotes=None, corpus=Corpus(as_of=AS_OF), as_of=AS_OF)
    full._analyse_qualitative("600519", r, AS_OF, None, md, pr, skipped, out=tmp_path, stops=stops,
                              statuses=[], research_client=client)
    return md, skipped, stops


def test_track_runs_after_macro_passes(monkeypatch, tmp_path):
    _stub(monkeypatch)

    def macro(*a, **k):
        (tmp_path / "macro_view.md").write_text("跟踪: 指标0 | 事件 | 证伪", encoding="utf-8")
        return "宏观", "宏观", ""
    monkeypatch.setattr(full, "_load_macro_view", macro)
    _fake_search(monkeypatch, [])
    md, _, _ = _run(tmp_path)
    assert "跟踪指标最新读数" in md["宏观定性"] and (tmp_path / MT.TRACK_JSON).exists()


def test_track_skipped_when_review_fails(monkeypatch, tmp_path):
    _stub(monkeypatch)

    def stop(*a, **k):
        raise full.QualStop("宏观定性审核 3 轮未通过")
    monkeypatch.setattr(full, "_load_macro_view", stop)
    asked = []
    _fake_search(monkeypatch, asked)
    _run(tmp_path)
    assert asked == [] and not (tmp_path / MT.TRACK_JSON).exists()


def test_restore_reads_track_docs(tmp_path, monkeypatch):
    _fake_search(monkeypatch, [])
    res = MT.track(object(), _view(1), industry="x", as_of=AS_OF, corpus=Corpus(as_of=AS_OF), us=False)
    MT.save(res, tmp_path / MT.TRACK_JSON)
    from ir_agent.sources import openweb
    openweb.save(openweb.OpenWebResult(), tmp_path / "openweb_macro.json")
    r = SimpleNamespace(corpus=None, as_of=AS_OF, code="600519")
    full._restore_openweb(r, tmp_path)
    assert any(d.module == "macro_track" for d in r.corpus.docs)
