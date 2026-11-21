"""第二、三次调用：读回按模块落盘的采集结果，不重采；旧 openweb.json 照读（spec 2026-11-21-staged-collection §3）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from ir_agent import full
from ir_agent.sources import openweb
from ir_agent.sources.external import Corpus, Document, Tier

AS_OF = date(2026, 11, 20)


def _doc(i, module=""):
    return Document(doc_id=f"ow_{i}", url=f"https://e{i}.org", title=f"标题{i}", publisher="机构",
                    tier=Tier.MEDIA, published=date(2026, 10, 1), retrieved=AS_OF, text=f"正文{i}",
                    module=module)


def _r():
    return SimpleNamespace(corpus=None, as_of=AS_OF, code="600519")


def _save(tmp_path, module, docs, missing=()):
    openweb.save(openweb.OpenWebResult(docs=docs, missing_slots=list(missing)),
                 tmp_path / f"openweb_{module}.json")


def test_restore_reads_all_module_files_in_order(tmp_path):
    _save(tmp_path, "macro", [_doc(1, "macro")])
    _save(tmp_path, "strategy", [_doc(2, "strategy")])
    _save(tmp_path, "value_chain", [_doc(3, "value_chain")])
    r = _r()
    full._restore_openweb(r, tmp_path)
    assert [(d.doc_id, d.module) for d in r.corpus.docs] == [
        ("ow_1", "macro"), ("ow_2", "strategy"), ("ow_3", "value_chain")]


def test_restore_legacy_openweb_json(tmp_path):
    openweb.save(openweb.OpenWebResult(docs=[_doc(1)], missing_slots=["公司重大动态"]),
                 tmp_path / openweb.OPENWEB_JSON)
    r = _r()
    notes = full._restore_openweb(r, tmp_path)
    assert [d.doc_id for d in r.corpus.docs] == ["ow_1"] and r.corpus.docs[0].module == ""
    assert any("· 公司重大动态：未取到" in n for n in notes)          # 旧措辞不变


def test_restore_distinguishes_empty_vs_corrupt_per_module(tmp_path):
    _save(tmp_path, "macro", [], missing=openweb.module_slots("macro", "600519"))
    (tmp_path / "openweb_strategy.json").write_text("{坏了", encoding="utf-8")
    _save(tmp_path, "value_chain", [_doc(3, "value_chain")])
    notes = "\n".join(full._restore_openweb(_r(), tmp_path))
    assert "宏观：第一阶段确实一篇都没采到" in notes
    assert "战略：未跨阶段存活" in notes


def test_restore_notes_missing_module_file(tmp_path):
    _save(tmp_path, "macro", [_doc(1, "macro")])
    notes = "\n".join(full._restore_openweb(_r(), tmp_path))
    assert "openweb_strategy.json" in notes and "openweb_value_chain.json" in notes


def test_rerun_stage1_module_sees_same_docs(tmp_path, monkeypatch):
    """Review Focus 1：第一次调用重跑时，宏观看到的资料与首跑逐字相同。"""
    from ir_agent import macro_view

    monkeypatch.setattr(openweb, "fetch", lambda url, **k: openweb.Fetched(
        final_url=url, text=f"正文 {url}", meta_published=date(2026, 10, 1)))
    monkeypatch.setattr(full, "_company_name", lambda code: "某公司")
    monkeypatch.setattr(full, "_industry_path", lambda code: "某行业")
    line = "候选: 政策与监管 | 条例 | https://gov.example/a | 某部委 | 2026-10-01"
    r1 = SimpleNamespace(corpus=Corpus(as_of=AS_OF), code="600519")
    full._collect_module("macro", "600519", r1, AS_OF, tmp_path, lambda p: line, {})
    first = macro_view.for_prompt(r1.corpus, "")
    r2 = SimpleNamespace(corpus=Corpus(as_of=AS_OF), code="600519")
    full._collect_module("macro", "600519", r2, AS_OF, tmp_path, lambda p: (_ for _ in ()).throw(AssertionError), {})
    assert macro_view.for_prompt(r2.corpus, "") == first
