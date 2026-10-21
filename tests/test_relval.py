"""相对估值：倍数口径、可比法区间、横截面回归、结果落账本。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ir_agent.relval import PeerObs, comps_bands, implied_price, multiples

D = Decimal


def obs(code="000001", name="某公司", **kw) -> PeerObs:
    base = dict(code=code, name=name, market_cap=D(100), net_profit=D(10),
                np_basis="TTM", equity_parent=D(50), total_equity=D(60),
                debt=D(20), cash=D(5), ebitda=D(23), roe=D("0.2"),
                growth=D("0.1"), growth_basis="3y")
    base.update(kw)
    return PeerObs(**base)


# ── 倍数口径 ──────────────────────────────────────────────────

def test_三个倍数的定义():
    m = multiples(obs())
    assert m["pe"] == D(10) and m["pb"] == D(2)


def test_EV含少数股东权益():
    # EV = 市值 + 有息负债 − 现金 + 少数股东权益(60 − 50)
    assert multiples(obs())["ev_ebitda"] == (D(100) + 20 - 5 + 10) / 23


def test_少数股东权益取不到时EV判缺而非按零():
    assert multiples(obs(total_equity=None))["ev_ebitda"] is None


def test_亏损时PE为空():
    assert multiples(obs(net_profit=D(-1)))["pe"] is None


def test_净资产为负时PB为空():
    assert multiples(obs(equity_parent=D(-3)))["pb"] is None


def test_EBITDA为负时EV倍数为空():
    assert multiples(obs(ebitda=D(-2)))["ev_ebitda"] is None


# ── 隐含每股价值：桥接与倍数同口径 ─────────────────────────────

@pytest.mark.parametrize("kind", ["pe", "pb", "ev_ebitda"])
def test_按自身倍数桥接回自身市值(kind):
    """本司套用自己的倍数，必须还原出自己的市值 —— 三条桥接各自闭环。"""
    t = obs()
    assert implied_price(kind, multiples(t)[kind], t, shares=D(10)) == D(10)


def test_本司指标为负时不出价格():
    assert implied_price("pe", D(20), obs(net_profit=D(-1)), shares=D(10)) is None


# ── 可比法区间 ────────────────────────────────────────────────

def _peers_pe(*pes):
    return [obs(code=f"00000{i}", market_cap=D(p), net_profit=D(1))
            for i, p in enumerate(pes)]


def test_可比区间取四分位与中位数():
    bands, why = comps_bands(_peers_pe(10, 20, 30, 40, 50, 60),
                             obs(market_cap=D(1), net_profit=D(1)), shares=D(1))
    b = bands["pe"]
    # 与行业画像同一算法：Q1 = v[n//4]、Q3 = v[3n//4]
    assert (b.mult_lo, b.mult_mid, b.mult_hi) == (D(20), D(35), D(50))
    assert (b.lo, b.mid, b.hi) == (D(20), D(35), D(50))
    assert b.n == 6


def test_本司亏损则PE行不适用():
    bands, why = comps_bands(_peers_pe(10, 20, 30, 40, 50, 60),
                             obs(net_profit=D(-5)), shares=D(1))
    assert bands["pe"] is None
    assert any(w.startswith("PE") and "不适用" in w for w in why)


def test_同业有效值不足四家不适用():
    """四分位至少要 4 个值；三家里的「P25」没有意义。"""
    peers = _peers_pe(10, 20, 30) + [obs(code="000009", net_profit=D(-1))]
    bands, why = comps_bands(peers, obs(), shares=D(1))
    assert bands["pe"] is None
    assert any(w.startswith("PE") and "不足" in w for w in why)
