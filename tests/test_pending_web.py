"""审核判「待采集」、只靠模型检索词命中的因子照样联网（用户 2026-11-26）。
历史 55 个待采集因子里 21 个因为检索词碰巧命中一句话就没联网；NVDA 7 个因子 0 个联网。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from ir_agent import gaps as G
from ir_agent.drivers import Factor
from ir_agent.ledger import FactLedger
from ir_agent.sources import conclusions
from ir_agent.sources.conclusions import Article, Conclusion
from ir_agent.sources.external import Corpus, Document, Tier

AS_OF = date(2026, 11, 25)
BY_TERM = Factor("f_a", "存储采购成本同比变化率的代理值", "变化率", "需新采集", "procurement of memory supply", "存储涨幅")
BY_LABEL = Factor("f_b", "边缘计算", "变化率", "", "Edge Computing", "边缘")


def _corpus():
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="a", url="https://sec/x", title="8-K", publisher="NVIDIA", tier=Tier.COMPANY, published=AS_OF,
                   retrieved=AS_OF, text="commitments primarily related to the multi-year procurement of memory supply. 边缘计算收入增长。"))
    return c


def _web(monkeypatch, items=1, boom=None):
    calls = []

    def fake(client, label, key, industry, as_of):
        calls.append(label)
        if boom:
            raise boom
        art = Article(no="1", url="https://t.example/x", publisher="TrendForce", published=AS_OF, final_url="https://t.example/x",
                      opened=True, text="合约价环比上涨 10%")
        return conclusions.Searched(items=[Conclusion(key, "合约价环比上涨 10%", [art])] * items, dropped=[] if items else ["没搜到"])
    monkeypatch.setattr(conclusions, "search", fake)
    return calls


def _collect(factors, pending=()):
    return G.collect(None, factors, FactLedger(), _corpus(), "x", AS_OF, pending=frozenset(pending))


def test_待采集且靠检索词命中_照样联网_线索保留(monkeypatch):
    calls = _web(monkeypatch)
    fd = _collect([BY_TERM], ["f_a"])["f_a"]
    assert calls == [BY_TERM.label] and fd.status == G.EXTERNAL and len(fd.findings) == 1
    assert "procurement of memory supply" in fd.note and fd.source_excerpt and fd.source_url == "https://sec/x"


def test_不是待采集的不联网(monkeypatch):
    calls = _web(monkeypatch)
    assert _collect([BY_TERM])["f_a"].status == G.ANNOUNCE and calls == []


def test_待采集但按标签结构命中的不联网(monkeypatch):
    calls = _web(monkeypatch)
    assert _collect([BY_LABEL], ["f_b"])["f_b"].status == G.ANNOUNCE and calls == []


@pytest.mark.parametrize("kw", [dict(items=0), dict(boom=G.WebUnavailable("不能联网"))])
def test_联网没结果时保留原命中并说明(monkeypatch, kw):
    _web(monkeypatch, **kw)
    fd = _collect([BY_TERM], ["f_a"])["f_a"]
    assert fd.status == G.ANNOUNCE and "待采集" in fd.note and "procurement" in fd.note


def test_引用线索原文仍认得出():
    from ir_agent import assume
    fd = G.FactorData(factor=BY_TERM, status=G.EXTERNAL, source_excerpt="…multi-year procurement of memory supply…")
    assert assume._match_excerpt(fd, "procurement of memory supply")


def test_落盘往返保留线索(monkeypatch):
    _web(monkeypatch)
    data = _collect([BY_TERM], ["f_a"])
    back, _ = G.from_json(json.loads(json.dumps(G.to_json(data))), [BY_TERM])
    assert back["f_a"].status == G.EXTERNAL and back["f_a"].source_excerpt == data["f_a"].source_excerpt


def test_从审核记录认出待采集的因子(tmp_path):
    from types import SimpleNamespace

    from ir_agent import full
    from ir_agent.methodology_review import ReviewResult, Verdict
    rv = ReviewResult(verdicts=[Verdict("因子", f"存储涨幅（{BY_TERM.label}）", "待采集", "x", "", ""),
                                Verdict("因子", "边缘（边缘计算）", "通过", "", "", ""),
                                Verdict("常量", "存储成本率", "待采集", "x", "", "")])
    (tmp_path / "drivers_review.json").write_text(json.dumps(rv.to_json()), encoding="utf-8")
    m = SimpleNamespace(factors=lambda: [BY_TERM, BY_LABEL])
    assert full._pending_factors(tmp_path, m) == frozenset({"f_a"})
    (tmp_path / "drivers_review.json").write_text("{坏", encoding="utf-8")
    assert full._pending_factors(tmp_path, m) == frozenset()
    assert full._pending_factors(tmp_path / "无", m) == frozenset()


def test_流程里传了待采集因子():
    import inspect

    from ir_agent import full
    assert "pending=" in inspect.getsource(full._collect_factors) and "_pending_factors(out, meth)" in inspect.getsource(full.run_brief)
