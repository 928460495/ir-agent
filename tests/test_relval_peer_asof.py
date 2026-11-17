"""相对估值的同业行情截至本司报价日（2026-11-17，生益 600183 重跑：本司按 10-21 收盘，同业是
11-17 实时行情，日期对不上被当成停牌全部剔除 →「可比名单未取到」）。不联网。"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal as D

from ir_agent import relval_data
from ir_agent.relval_data import gather
from tests.test_relval_data import AS_OF, facts_years, fake_r

LIVE = datetime(2026, 11, 17, 15, 0)


def _live(mc, last="50"):
    return {"market_cap": D(mc), "last": D(last), "code": "", "name": "", "as_of": LIVE}


def _patch(monkeypatch, quotes, closes, calls):
    monkeypatch.setattr(relval_data, "fetch_quotes", lambda symbols, **kw: (quotes, [("U", "R")]))

    def close(sym, d):
        calls.append(sym)
        r = closes[sym]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(relval_data, "close_asof", close)


def test_同业实时行情换成本司报价日收盘_市值按价缩放(monkeypatch, tmp_path):
    calls = []
    _patch(monkeypatch, {"002384": _live(1000), "002463": _live(2000, "40")},
           {"sz002384": (AS_OF, D("100")), "sz002463": (AS_OF, D("20"))}, calls)
    _, sample, comps, _, _, notes = gather(
        fake_r({"002384": facts_years(), "002463": facts_years()}), AS_OF, tmp_path)
    mc = {o.code: o.market_cap for o in sample}
    assert mc == {"002384": D(2000), "002463": D(1000)}          # 1000×100/50、2000×20/40
    assert [o.code for o in comps] == ["002384"]
    assert not any("剔除" in n for n in notes)


def test_本司报价日当天没有收盘的同业剔除并说明(monkeypatch, tmp_path):
    calls = []
    _patch(monkeypatch, {"002384": _live(1000), "002463": _live(1000)},
           {"sz002384": (date(2026, 10, 14), D("100")), "sz002463": RuntimeError("超时")}, calls)
    _, sample, _, _, _, notes = gather(
        fake_r({"002384": facts_years(), "002463": facts_years()}), AS_OF, tmp_path)
    assert sample == []
    assert any("002384" in n and "2026-10-14" in n and "停牌" in n for n in notes)
    assert any("002463" in n and "收盘取不到" in n for n in notes)


def test_同业收盘按分析时点落盘_重跑不再取(monkeypatch, tmp_path):
    calls = []
    _patch(monkeypatch, {"002384": _live(1000)}, {"sz002384": (AS_OF, D("100"))}, calls)
    gather(fake_r({"002384": facts_years()}), AS_OF, tmp_path)
    cache = json.loads((tmp_path / f"peer_close_{AS_OF.isoformat()}.json").read_text("utf-8"))
    assert cache["sz002384"] == ["2026-10-21", "100"]
    _, sample, _, _, _, _ = gather(fake_r({"002384": facts_years()}), AS_OF, tmp_path)
    assert calls == ["sz002384"] and sample[0].market_cap == D(2000)


def test_同业行情就是本司报价日_不取历史收盘(monkeypatch, tmp_path):
    calls = []
    q = {**_live(1000), "as_of": datetime(2026, 10, 21, 15, 0)}
    _patch(monkeypatch, {"002384": q}, {}, calls)
    _, sample, _, _, _, _ = gather(fake_r({"002384": facts_years()}), AS_OF, tmp_path)
    assert calls == [] and sample[0].market_cap == D(1000)
