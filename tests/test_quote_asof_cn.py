"""A 股研报行情截至分析时点（2026-11-17，生益 600183：as_of 10-21，账本里只有 11-17 的实时
行情，`ledger.get(as_of=10-21)` 取不到 → 相对估值「本司市值或现价缺失」）。不联网。"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from ir_agent import pipeline
from ir_agent.sources import quotes
from ir_agent.sources.snapshot import SnapshotStore

KL = {"code": 0, "data": {"sh600183": {"day": [
    ["2026-10-17", "1", "114.60", "1", "1", "1"],
    ["2026-10-20", "1", "117.63", "1", "1", "1"],
    ["2026-10-21", "1", "118.99", "1", "1", "1"]]}}}


class _Resp:
    def __init__(self, payload): self.text = json.dumps(payload)
    def raise_for_status(self): pass
    def json(self): return json.loads(self.text)


class _S:
    def __init__(self): self.params = None
    def get(self, url, params=None, timeout=None):
        self.params = params
        return _Resp(KL)


def test_分析时点收盘取当日或之前最近一笔_不复权():
    s = _S()
    assert quotes.close_asof("sh600183", date(2026, 10, 21), session=s) == (date(2026, 10, 21), Decimal("118.99"))
    assert s.params["param"].endswith(",")                       # 末段空 = 不复权
    assert quotes.close_asof("sh600183", date(2026, 10, 19), session=s) == (date(2026, 10, 17), Decimal("114.60"))
    with pytest.raises(quotes.ParseError):
        quotes.close_asof("sh600183", date(2026, 11, 30), session=s)   # 距最近收盘太久


def _live():
    return {"code": "600183", "as_of": datetime(2026, 11, 17, 15, 0), "last": Decimal("58.61"),
            "market_cap": Decimal("58.61") * Decimal("2429000000")}


def test_分析时点早于今天_账本记当时收盘与按价缩放的市值(monkeypatch, tmp_path):
    monkeypatch.setattr(quotes, "fetch_quote", lambda sym: _live())
    monkeypatch.setattr(quotes, "close_asof", lambda sym, d: (date(2026, 10, 21), Decimal("118.99")))
    res = pipeline._quote_facts("600183", SnapshotStore(tmp_path), cross_check=False, as_of=date(2026, 10, 21))
    f = {x.key: x for x in res.facts}
    assert f["last_price"].value == Decimal("118.99") and f["last_price"].as_of == date(2026, 10, 21)
    assert f["market_cap"].value == Decimal("118.99") * Decimal("2429000000")
    assert f["market_cap"].period == "2026-10-21"


def test_分析时点就是今天_照用实时行情(monkeypatch, tmp_path):
    monkeypatch.setattr(quotes, "fetch_quote", lambda sym: _live())
    monkeypatch.setattr(quotes, "close_asof", lambda sym, d: pytest.fail("不该取历史收盘"))
    res = pipeline._quote_facts("600183", SnapshotStore(tmp_path), cross_check=False, as_of=date(2026, 11, 17))
    assert {x.key: x.value for x in res.facts}["last_price"] == Decimal("58.61")


SINA = [{"day": "2026-10-20", "close": "117.630"}, {"day": "2026-10-21", "close": "118.990"},
        {"day": "2026-11-17", "close": "58.610"}]


class _Down:
    """腾讯 fqkline 整体 501（2026-11-17 实测），新浪日 K 正常。"""
    def __init__(self): self.urls = []
    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        if "gtimg" in url:
            import requests
            r = requests.Response(); r.status_code = 501; r.url = url
            return r
        return _Resp(SINA)


def test_腾讯日K不可用时降级新浪日K():
    s = _Down()
    assert quotes.close_asof("sh600183", date(2026, 10, 21), session=s) == (date(2026, 10, 21), Decimal("118.990"))
    assert any("sina" in u for u in s.urls)


def test_两个日K源都不可用时报错写明两者():
    class _Both(_Down):
        def get(self, url, params=None, timeout=None):
            if "sina" in url:
                raise ConnectionError("断开")
            return super().get(url, params, timeout)
    with pytest.raises(quotes.ParseError, match="腾讯.*新浪"):
        quotes.close_asof("sh600183", date(2026, 10, 21), session=_Both())
