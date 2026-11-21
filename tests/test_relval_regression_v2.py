"""回归按驱动配对（多元 OLS）+ 有界扩样本（spec 2026-11-21 §5）。不联网。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

from ir_agent import relval, relval_data
from ir_agent.relval import MIN_REG_N, REG_EXPAND_CAP, REG_SPEC, regress
from tests.test_relval import obs

D = Decimal


def test_回归按驱动表配对():
    assert REG_SPEC["pb_roe"][:2] == (("roe3y", "growth"), "pb")
    assert REG_SPEC["pe_growth"][:2] == (("growth", "roe3y"), "pe")
    assert REG_EXPAND_CAP == 80


def _sample(n=40, a=2.0, b1=20.0, b2=10.0, noise=0.5, seed=3):
    """PB = a + b1·ROE3y + b2·增速 + ε（净资产 1，净利润 = ROE3y）。"""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        roe, g = rng.uniform(0.02, 0.3), rng.uniform(-0.05, 0.3)
        pb = a + b1 * roe + b2 * g + rng.normal(0, noise)
        out.append(obs(code=f"{i:06d}", market_cap=D(str(round(pb, 6))), equity_parent=D(1),
                       total_equity=D(1), net_profit=D(str(round(roe, 6))),
                       roe3y=D(str(round(roe, 6))), growth=D(str(round(g, 6)))))
    return out


def _tgt(**kw):
    return obs(code="600183", **{"equity_parent": D(1), "total_equity": D(1),
                                 "roe3y": D("0.2"), "growth": D("0.1"), **kw})


def _design(r):
    X = np.column_stack([np.ones(r.n)] + [np.array([row[j] for row in r.xsample]) for j in range(2)])
    y = np.array([row[3] for row in r.sample])
    return X, y


def test_PB对ROE与增速的系数与t值与numpy手算一致():
    r = regress("pb_roe", _sample(), _tgt(), D(1))
    X, y = _design(r)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    s2 = (resid ** 2).sum() / (r.n - 3)
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    assert r.a == pytest.approx(beta[0]) and r.b == pytest.approx(beta[1])
    assert r.coefs["growth"] == pytest.approx(beta[2])
    assert r.t == pytest.approx(beta[1] / se[1]) and r.ts["growth"] == pytest.approx(beta[2] / se[2])
    assert r.usable


def test_多元预测区间():
    from scipy import stats

    r = regress("pb_roe", _sample(), _tgt(), D(1))
    X, y = _design(r)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    s = np.sqrt(((y - X @ beta) ** 2).sum() / (r.n - 3))
    x0 = np.array([1, 0.2, 0.1])
    half = stats.t.ppf(0.90, r.n - 3) * s * np.sqrt(1 + x0 @ np.linalg.inv(X.T @ X) @ x0)
    assert r.fit_mult == pytest.approx(x0 @ beta)
    assert r.hi_mult - r.lo_mult == pytest.approx(2 * half)


def test_主驱动t小于2不可用():
    for seed in range(300):
        r = regress("pb_roe", _sample(b1=0.3, noise=3.0, seed=seed), _tgt(), D(1))
        if r.b > 0 and r.t < 2:
            break
    assert not r.usable and "不显著" in r.why


def test_次驱动无差异时退化为单变量():
    s = _sample()
    for o in s:
        o.growth = D("0.1")
    r = regress("pb_roe", s, _tgt(), D(1))
    assert r.dropped == ["growth"] and "growth" not in r.coefs


# ── 有界扩样本 ────────────────────────────────────────────────

from ir_agent.sources.taxonomy import Entry, Taxonomy   # noqa: E402
from tests.test_relval_data import AS_OF, F, facts_years  # noqa: E402

L3 = "电子-元件-印制电路板"
L3B = "电子-元件-被动元件"


def _tax(n3=4, n3b=20):
    es = [Entry("600183", "生益科技", L3)]
    es += [Entry(f"6001{i:02d}", f"三级{i}", L3) for i in range(n3)]
    es += [Entry(f"0020{i:02d}", f"二级{i}", L3B) for i in range(n3b)]
    return Taxonomy(es, date(2026, 10, 1))


def _r(level=3, n3=4):
    from tests.test_relval_data import fake_r
    peers = {f"6001{i:02d}": facts_years() for i in range(n3)}
    r = fake_r(peers, comps=())
    r.peer_set.classification = SimpleNamespace(used_path=L3, path=L3, level=level)
    r.peer_set.members = [SimpleNamespace(code=c, name=c, path=tuple(L3.split("-"))) for c in peers]
    return r


def _quotes(monkeypatch):
    def fake(symbols, **kw):
        return ({s[2:] if s[:2] in ("sh", "sz") else s: {
            "market_cap": D(1000), "last": D(50), "as_of": datetime(2026, 10, 21, 15, 0)}
            for s in symbols}, [("URL", "RAW")])
    monkeypatch.setattr(relval_data, "fetch_quotes", fake)


def test_A股三级样本不足时扩到二级(monkeypatch, tmp_path):
    _quotes(monkeypatch)
    r = _r()
    tgt, sample, *_ = relval_data.gather(r, AS_OF, tmp_path)
    extra, notes, label = relval_data.expand_sample(r, AS_OF, tmp_path, tgt, sample, taxonomy=_tax(),
                                                    fetch=lambda c: list(facts_years().values()))
    assert len(sample) + len(extra) >= MIN_REG_N
    assert {o.code[:4] for o in extra} == {"0020"} and "电子-元件" in label
    assert all(o.path == tuple(L3B.split("-")) for o in extra)


def test_已是二级不扩(tmp_path):
    r = _r(level=2)
    extra, notes, _ = relval_data.expand_sample(r, AS_OF, tmp_path, obs(code="600183"), [], taxonomy=_tax())
    assert extra == [] and any("二级" in n and "底线" in n for n in notes)


def test_扩样本超上限截断并写明(monkeypatch, tmp_path):
    _quotes(monkeypatch)
    monkeypatch.setattr(relval, "REG_EXPAND_CAP", 10)
    r = _r()
    tgt, sample, *_ = relval_data.gather(r, AS_OF, tmp_path)
    extra, notes, _ = relval_data.expand_sample(r, AS_OF, tmp_path, tgt, sample, taxonomy=_tax(),
                                                fetch=lambda c: list(facts_years().values()))
    assert len(sample) + len(extra) <= 10 and any("上限" in n for n in notes)


def test_扩样本取数失败不抛出(monkeypatch, tmp_path):
    _quotes(monkeypatch)
    r = _r()
    tgt, sample, *_ = relval_data.gather(r, AS_OF, tmp_path)

    def boom(c):
        raise RuntimeError("频控")
    extra, notes, _ = relval_data.expand_sample(r, AS_OF, tmp_path, tgt, sample, taxonomy=_tax(), fetch=boom)
    assert extra == [] and any("取不到" in n for n in notes)


def test_回归用扩大后的样本且可比名单不变(monkeypatch, tmp_path):
    from ir_agent import full
    from tests.test_relval_fundamentals import _o, _pe_peer
    from tests.test_relval_wiring import _r as wr

    tgt = _o("600183")
    sample = [_pe_peer(f"60000{i}", 10 + i) for i in range(8)]
    extra = [_pe_peer(f"00200{i}", 10 + i) for i in range(9)]
    monkeypatch.setattr(relval_data, "gather", lambda *a, **k: (tgt, sample, [], D(1), D(100), []))
    monkeypatch.setattr(relval_data, "expand_sample", lambda *a, **k: (extra, ["扩了"], "申万二级「电子-元件」"))
    res, _ = full._relative_valuation("600183", wr(), AS_OF, None, tmp_path)
    assert res.regs["pb_roe"].n + len(res.regs["pb_roe"].excluded) == len(sample) + len(extra)
    assert res.regs["pb_roe"].expanded == "申万二级「电子-元件」"
    assert not any(p.code.startswith("002") for p in res.comps_by_kind["pb"])
    assert "扩了" in res.sample_notes and "申万二级「电子-元件」" in res.summary()


# ── 终审修复 ────────────────────────────────────────────────

def test_扩样本按筛选后的有效样本数触发(monkeypatch, tmp_path):
    """终审 2：原始样本够十五家、但亏损剔除后回归不足十五家，也要扩。"""
    _quotes(monkeypatch)
    r = _r()
    tgt, sample, *_ = relval_data.gather(r, AS_OF, tmp_path)
    sample = [obs(code=f"6009{i:02d}", roe3y=D("0.1"), growth=D("0.1")) for i in range(10)] + \
             [obs(code=f"6008{i:02d}", net_profit=D(-1)) for i in range(8)]       # 18 家，有效 10 家
    extra, notes, label = relval_data.expand_sample(r, AS_OF, tmp_path, tgt, sample, taxonomy=_tax(),
                                                    fetch=lambda c: list(facts_years().values()))
    assert extra and "电子-元件" in label


def test_三级成员够数但样本不足也直接扩到二级(monkeypatch, tmp_path):
    """终审 2：`widen(min_peers=15)` 数的是分类表成员，三级 16 家会让它停在三级 —— 不能用它判断。"""
    _quotes(monkeypatch)
    r = _r()
    tgt, sample, *_ = relval_data.gather(r, AS_OF, tmp_path)
    extra, notes, label = relval_data.expand_sample(r, AS_OF, tmp_path, tgt, sample, taxonomy=_tax(n3=16),
                                                    fetch=lambda c: list(facts_years().values()))
    assert any(o.code.startswith("0020") for o in extra) and "电子-元件" in label


def test_扩样本连续失败即停(monkeypatch, tmp_path):
    """终审 3：东财限流时每家都要等超时，有界不能只靠家数。"""
    _quotes(monkeypatch)
    r = _r()
    tgt, sample, *_ = relval_data.gather(r, AS_OF, tmp_path)
    calls = []

    def boom(c):
        calls.append(c)
        raise RuntimeError("频控")
    extra, notes, _ = relval_data.expand_sample(r, AS_OF, tmp_path, tgt, sample, taxonomy=_tax(), fetch=boom)
    assert len(calls) == relval_data.EXPAND_FAIL_STOP and any("连续" in n for n in notes)


def test_扩样本财报落盘重跑复用(monkeypatch, tmp_path):
    _quotes(monkeypatch)
    r = _r()
    tgt, sample, *_ = relval_data.gather(r, AS_OF, tmp_path)
    first, _, _ = relval_data.expand_sample(r, AS_OF, tmp_path, tgt, sample, taxonomy=_tax(),
                                            fetch=lambda c: list(facts_years().values()))

    def boom(c):
        raise AssertionError("应读落盘的财报")
    again, _, _ = relval_data.expand_sample(r, AS_OF, tmp_path, tgt, sample, taxonomy=_tax(), fetch=boom)
    assert {o.code for o in again} == {o.code for o in first}
