"""A 股相对估值补 EV/Sales（亏损公司）与预期 PE（spec 2026-11-21 §4）。不联网。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ir_agent import relval
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.reports import Consensus, Estimate, Report
from tests.test_relval_fundamentals import _o, _pe_peer

D = Decimal
AS_OF = date(2026, 11, 21)


def test_A股本司亏损时启用EV_Sales且含预期PE():
    loss = _o("600519", np_=D(-5))
    assert "ev_sales" in relval.kinds_for(loss, us=False)
    assert "ev_sales" not in relval.kinds_for(_o("600519"), us=False)
    assert "pe_ntm" in relval.kinds_for(_o("600519"), us=False)


def _cons(e0, e1, y0=2026):
    return Consensus(as_of=AS_OF, window_days=180, eps={
        y0: Estimate(fiscal_year=y0, n=5, median=D(e0), low=D(e0), high=D(e0)),
        y0 + 1: Estimate(fiscal_year=y0 + 1, n=5, median=D(e1), low=D(e1), high=D(e1))})


def test_NTM按十二月三十一日财年末加权():
    from ir_agent.relval_ntm import ntm_eps

    v, basis = ntm_eps(_cons("10", "12"), "1231", AS_OF, source="东财研报")
    w = D(40) / D(365)                        # 2026-11-21 到 2026-12-31
    assert v == pytest.approx(w * 10 + (1 - w) * 12)
    assert "东财研报" in basis and "FY2026" in basis


def _eps_fact(y, v):
    return Fact(key="consensus.eps", value=D(v), unit="元", period=f"{y}FY", as_of=AS_OF,
                source_id="eastmoney_report", method=Method.REPORTED)


def test_本司NTM取账本两年预期():
    from ir_agent.relval_ntm import ntm_from_ledger

    led = FactLedger()
    led.put(_eps_fact(2026, "50"))
    led.put(_eps_fact(2027, "60"))
    v, basis = ntm_from_ledger(led, "1231", AS_OF)
    w = D(40) / D(365)
    assert v == pytest.approx(w * 50 + (1 - w) * 60)
    led2 = FactLedger()
    led2.put(_eps_fact(2026, "50"))
    assert ntm_from_ledger(led2, "1231", AS_OF) is None        # 缺下一财年不拿一年顶两年


def _rep(code, eps0, eps1, published):
    return Report(info_code=f"R{code}{published}", title="t", org="某券商", published=published,
                  eps={2026: D(eps0), 2027: D(eps1)})


def test_同业一致预期按时点取并落快照(monkeypatch, tmp_path):
    from ir_agent import relval_data
    from ir_agent.sources import reports

    asked = []

    def fake_search(*, as_of, code="", **kw):
        asked.append((code, as_of))
        return [_rep(code, "1", "2", date(2026, 10, 1)), _rep(code, "1.2", "2.2", date(2026, 9, 1))]
    monkeypatch.setattr(reports, "search", fake_search)
    got = relval_data.peer_ntm(["000001", "000002"], AS_OF, tmp_path)
    assert set(got) == {"000001", "000002"} and all(c[1] == AS_OF for c in asked)
    snap = json.loads((tmp_path / f"peer_consensus_{AS_OF.isoformat()}.json").read_text("utf-8"))
    assert set(snap) == {"000001", "000002"}
    # 重跑同一时点读快照，不再请求
    monkeypatch.setattr(reports, "search", lambda **kw: pytest.fail("应读快照"))
    again = relval_data.peer_ntm(["000001", "000002"], AS_OF, tmp_path)
    assert again == got


def test_同业缺预期剔除(monkeypatch, tmp_path):
    from ir_agent import relval_data
    from ir_agent.sources import reports

    monkeypatch.setattr(reports, "search", lambda *, as_of, code="", **kw: (
        [] if code == "000002" else [_rep(code, "1", "2", date(2026, 10, 1)),
                                      _rep(code, "1", "2", date(2026, 10, 2))]))
    got = relval_data.peer_ntm(["000001", "000002"], AS_OF, tmp_path)
    assert set(got) == {"000001"}


def _gather_with(sample, tgt):
    return lambda *a, **k: (tgt, sample, [], D(1), D(100), [])


def test_NTM可比不足四家写覆盖不足(monkeypatch, tmp_path):
    from ir_agent import full, relval_data
    from tests.test_relval_wiring import _r

    tgt = _o("600183")
    tgt.price, tgt.eps_ntm = D(100), D(5)
    sample = [_pe_peer(f"60000{i}", 10 + i) for i in range(8)]
    for p in sample:
        p.price = D(50)
    monkeypatch.setattr(relval_data, "gather", _gather_with(sample, tgt))
    monkeypatch.setattr(relval_data, "peer_ntm", lambda codes, as_of, snap: {
        c: (D(2), "NTM") for c in list(codes)[:2]})
    res, _ = full._relative_valuation("600183", _r(), AS_OF, None, tmp_path)
    assert res.comps["pe_ntm"] is None
    assert any(n.startswith("PE(NTM)") and "一致预期覆盖不足" in n for n in res.comps_notes)


def test_NTM可比取PE选中名单的一致预期(monkeypatch, tmp_path):
    from ir_agent import full, relval_data
    from tests.test_relval_wiring import _r

    tgt = _o("600183")
    tgt.price, tgt.eps_ntm = D(100), D(5)
    sample = [_pe_peer(f"60000{i}", 10 + i) for i in range(8)]
    for p in sample:
        p.price = D(50)
    asked = []

    def fake(codes, as_of, snap):
        asked.extend(codes)
        return {c: (D(2), "NTM") for c in codes}
    monkeypatch.setattr(relval_data, "gather", _gather_with(sample, tgt))
    monkeypatch.setattr(relval_data, "peer_ntm", fake)
    res, _ = full._relative_valuation("600183", _r(), AS_OF, None, tmp_path)
    assert set(asked) == {p.code for p in res.comps_by_kind["pe"]}
    assert res.comps["pe_ntm"] is not None and res.comps["pe_ntm"].mult_mid == D(25)   # 50 ÷ 2


def test_同业一致预期取数失败不落快照(monkeypatch, tmp_path):
    """终审 4：网络失败当成「没有预期」落盘，同一时点重跑就永远不再重试。"""
    from ir_agent import relval_data
    from ir_agent.sources import reports

    def boom(**kw):
        raise ConnectionError("断网")
    monkeypatch.setattr(reports, "search", boom)
    assert relval_data.peer_ntm(["000001"], AS_OF, tmp_path) == {}
    monkeypatch.setattr(reports, "search", lambda *, as_of, code="", **kw: [
        _rep(code, "1", "2", date(2026, 10, 1)), _rep(code, "1", "2", date(2026, 10, 2))])
    assert set(relval_data.peer_ntm(["000001"], AS_OF, tmp_path)) == {"000001"}
