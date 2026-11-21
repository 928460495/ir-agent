"""模块可见资料与按模块的审核指纹（spec 2026-11-21-staged-collection §6）。不联网。"""

from __future__ import annotations

from datetime import date

from ir_agent import qual_review as Q
from ir_agent.ledger import FactLedger
from ir_agent.sources.external import Corpus, Document, Tier

AS_OF = date(2026, 11, 20)


def _doc(i, module="", tier=Tier.MEDIA, scope=""):
    return Document(doc_id=f"d{i}", url=f"https://e{i}.org", title=f"标题{module or '共用'}{i}",
                    publisher="某机构", tier=tier, published=date(2026, 10, 1), retrieved=AS_OF,
                    text=f"正文{i}", module=module, scope=scope)


def _corpus():
    c = Corpus(as_of=AS_OF)
    for d in (_doc(1), _doc(2, "macro"), _doc(3, "strategy"), _doc(4, "value_chain", scope="规模以上，月度"),
              _doc(5, "macro_track")):
        c.add_if_visible(d)
    return c


def test_prompt_docs_filters_by_module():
    c = _corpus()
    assert {d.doc_id for d in c.prompt_docs(module="macro")} == {"d1", "d2"}
    assert {d.doc_id for d in c.prompt_docs(module="strategy")} == {"d1", "d3"}
    assert {d.doc_id for d in c.prompt_docs()} == {"d1", "d2", "d3", "d4", "d5"}
    assert {d.doc_id for d in c.visible("value_chain")} == {"d1", "d4"}


def test_fingerprint_ignores_other_modules():
    c = _corpus()
    fp = Q.corpus_fingerprint(c, "macro")
    c.add_if_visible(_doc(9, "value_chain"))
    assert Q.corpus_fingerprint(c, "macro") == fp
    assert Q.corpus_fingerprint(c) != Q.corpus_fingerprint(_corpus())     # 不给模块仍按整池


def test_three_modules_prompt_only_own_docs():
    from ir_agent import macro_view, strategy, value_chain

    c = _corpus()
    led = FactLedger()
    mv = macro_view.for_prompt(c, "")
    st = strategy.for_prompt(led, "2025FY", AS_OF, None, corpus=c)
    vc = value_chain.for_prompt(c, led, "2025FY", AS_OF)
    assert "标题macro2" in mv and "标题strategy3" not in mv and "标题macro_track5" not in mv
    assert "标题strategy3" in st and "标题macro2" not in st
    assert "标题value_chain4" in vc and "标题strategy3" not in vc
    assert all("标题共用1" in t for t in (mv, st, vc))
    assert "口径（采集时自述，未核验）：规模以上，月度" in vc


def test_沿用审核结论按模块比对指纹(tmp_path):
    """宏观审核时只有共用 + 宏观资料；之后战略、产业链又采了资料，宏观沿用时不应报「语料库已变」。"""
    from types import SimpleNamespace

    from ir_agent import full
    from ir_agent.methodology_review import ReviewResult

    c = _corpus()
    raw = "政策取向: 中性 | 依据"
    (tmp_path / "macro_view.md").write_text(raw, encoding="utf-8")
    rr = ReviewResult(fingerprint=Q.fingerprint(raw))
    (tmp_path / "macro_view_review.json").write_text(
        Q.dump(rr, passed=True, corpus_fp=Q.corpus_fingerprint(c, "macro")), encoding="utf-8")
    c.add_if_visible(_doc(8, "strategy"))
    called = []

    def facts(v):
        called.append("facts")
        return []
    _, st = full._qual_module(tmp_path, prefix="macro_view", label="宏观定性", corpus=c,
                              parse=lambda r: SimpleNamespace(read_notes=[], review=None),
                              facts=facts, run=lambda: None)
    assert st["state"] == "沿用审核结论" and called == []       # 指纹相同，不走「重核依据」
