"""可比公司挑选。同业不等于可比 —— 规模差两个数量级的 PE/PB 不可比。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.comparables import NoComparables, Sized, select
from ir_agent.sources.industry import Classification, Member, Peers

D = Decimal
YI = D("1e8")


def _peers(target="600519", members=()):
    return Peers(target=target, target_name="标的",
                 classification=Classification(target, "标的", "甲-乙-丙",
                                               "甲-乙-丙", 3),
                 members=[Member(c, n) for c, n in members])


def _caps(mapping):
    """mapping: code -> 市值(亿)。返回可注入 select 的假 fetch。"""
    def fake(codes, session=None):
        ok = [Sized(c, f"公司{c}", D(str(mapping[c])) * YI)
              for c in codes if c in mapping]
        bad = [(c, "无总市值") for c in codes if c not in mapping]
        return ok, bad
    return fake


def _sel(target, mapping, members=None, n=3, monkeypatch=None):
    import ir_agent.comparables as m
    monkeypatch.setattr(m, "fetch_caps", _caps(mapping))
    mem = members or [(c, f"公司{c}") for c in mapping if c != target]
    return select(_peers(target, mem), n=n)


# ── 就近选取 ─────────────────────────────────────────────────

def test_龙头标的取次大的几家(monkeypatch):
    caps = {"600519": 20000, "a": 3000, "b": 2000, "c": 900, "d": 50}
    s = _sel("600519", caps, n=3, monkeypatch=monkeypatch)
    assert s.codes == ["a", "b", "c"]
    assert "d" not in s.codes                 # 市值差 400 倍，不可比


def test_居中标的两侧各取(monkeypatch):
    caps = {"t": 1000, "big1": 9000, "big2": 5000,
            "near_up": 1500, "near_dn": 700, "tiny": 20}
    s = _sel("t", caps, n=2, monkeypatch=monkeypatch)
    assert set(s.codes) == {"near_up", "near_dn"}


def test_小市值标的不与龙头比(monkeypatch):
    """取市值最大的 N 家是错的 —— 小公司对龙头永远垫底，
    那反映的是规模差异，不是经营差异。"""
    caps = {"t": 30, "x": 50, "y": 25, "huge1": 9000, "huge2": 8000}
    s = _sel("t", caps, n=2, monkeypatch=monkeypatch)
    assert set(s.codes) == {"x", "y"}


def test_结果按市值降序便于阅读(monkeypatch):
    caps = {"t": 1000, "a": 1200, "b": 900, "c": 1500}
    s = _sel("t", caps, n=3, monkeypatch=monkeypatch)
    assert [x.market_cap for x in s.chosen] == sorted(
        [x.market_cap for x in s.chosen], reverse=True)


def test_同业不足n家时全取(monkeypatch):
    s = _sel("t", {"t": 100, "a": 90}, n=6, monkeypatch=monkeypatch)
    assert s.codes == ["a"]


# ── 降级 ─────────────────────────────────────────────────────

def test_标的无市值时退化为取最大的几家并保留说明(monkeypatch):
    caps = {"a": 900, "b": 800, "c": 10}        # 标的 t 不在其中
    s = _sel("t", caps, members=[(c, c) for c in caps], n=2,
             monkeypatch=monkeypatch)
    assert s.codes == ["a", "b"]
    assert s.target_cap is None
    assert "标的市值" not in s.summary()


def test_取不到市值的同业记入dropped(monkeypatch):
    caps = {"t": 100, "a": 90}
    s = _sel("t", caps, members=[("a", "a"), ("停牌", "停牌")], n=3,
             monkeypatch=monkeypatch)
    assert ("停牌", "无总市值") in s.dropped
    assert "停牌" in s.summary()


def test_全部取不到市值时报错(monkeypatch):
    import ir_agent.comparables as m
    monkeypatch.setattr(m, "fetch_caps",
                        lambda codes, session=None: ([], [(c, "挂了") for c in codes]))
    with pytest.raises(NoComparables, match="无法挑选"):
        select(_peers("t", [("a", "a")]))


def test_只有标的自己时报错(monkeypatch):
    import ir_agent.comparables as m
    monkeypatch.setattr(m, "fetch_caps", _caps({"t": 100}))
    with pytest.raises(NoComparables, match="无可比公司"):
        select(_peers("t", []))


# ── 摘要 ─────────────────────────────────────────────────────

def test_摘要说明选取范围与口径(monkeypatch):
    caps = {"t": 1000, "a": 1200, "b": 900, "c": 5}
    s = _sel("t", caps, n=2, monkeypatch=monkeypatch)
    txt = s.summary()
    assert "可比公司 2 家" in txt
    assert "3 家同业中按市值就近选取" in txt
    assert "标的市值" in txt


def test_市值以亿元呈现(monkeypatch):
    s = _sel("t", {"t": 1000, "a": 1234}, n=1, monkeypatch=monkeypatch)
    assert "1,234 亿" in s.summary()
