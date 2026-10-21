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


# ── 横截面回归 ────────────────────────────────────────────────

import numpy as np  # noqa: E402

from ir_agent.relval import regress  # noqa: E402


def _pb_sample(n=40, a=4.84, b=28.08, noise=1.0, seed=1, roe_lo=0.0, roe_hi=0.3):
    """PB = a + b·ROE + ε 的合成行业样本（净资产 1、净利润 = ROE，均为正）。"""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        roe = rng.uniform(roe_lo, roe_hi)
        pb = a + b * roe + rng.normal(0, noise)
        out.append(obs(code=f"{i:06d}", market_cap=D(str(round(pb, 6))),
                       equity_parent=D(1), total_equity=D(1),
                       net_profit=D(str(round(roe, 6))), roe=D(str(round(roe, 6)))))
    return out


def _tgt(**kw):
    return obs(code="600183", **{"equity_parent": D(1), "total_equity": D(1), **kw})


def test_系数与numpy一致():
    r = regress("pb_roe", _pb_sample(), _tgt(roe=D("0.2")), D(1))
    xs = np.array([x for _, _, x, _ in r.sample])
    ys = np.array([y for _, _, _, y in r.sample])
    b, a = np.polyfit(xs, ys, 1)
    assert r.a == pytest.approx(a) and r.b == pytest.approx(b)
    assert r.n == len(xs) and r.usable


def test_预测区间与scipy独立计算一致():
    from scipy import stats
    r = regress("pb_roe", _pb_sample(), _tgt(roe=D("0.2")), D(1))
    xs = np.array([x for _, _, x, _ in r.sample])
    ys = np.array([y for _, _, _, y in r.sample])
    lr = stats.linregress(xs, ys)
    n = len(xs)
    s = np.sqrt(((ys - (lr.intercept + lr.slope * xs)) ** 2).sum() / (n - 2))
    half = stats.t.ppf(0.90, n - 2) * s * np.sqrt(
        1 + 1 / n + (0.2 - xs.mean()) ** 2 / ((xs - xs.mean()) ** 2).sum())
    fit = lr.intercept + lr.slope * 0.2
    assert r.fit_mult == pytest.approx(fit)
    assert r.lo_mult == pytest.approx(fit - half) and r.hi_mult == pytest.approx(fit + half)
    assert r.r2 == pytest.approx(lr.rvalue ** 2)
    assert r.t == pytest.approx(lr.slope / lr.stderr)


def test_隐含每股价值由应得倍数换算():
    r = regress("pb_roe", _pb_sample(), _tgt(roe=D("0.2"), equity_parent=D(10),
                                              total_equity=D(10)), shares=D(2))
    assert float(r.fit) == pytest.approx(r.fit_mult * 10 / 2)


def test_外推时区间变宽且标记():
    near = regress("pb_roe", _pb_sample(), _tgt(roe=D("0.15")), D(1))
    far = regress("pb_roe", _pb_sample(), _tgt(roe=D("0.6")), D(1))
    assert (far.hi_mult - far.lo_mult) > (near.hi_mult - near.lo_mult)
    assert far.extrapolated and not near.extrapolated


def test_剔除本司亏损负净资产与两端():
    s = _pb_sample()
    s.append(obs(code="600183"))                                         # 本司
    s.append(obs(code="900001", net_profit=D(-1), roe=D("-0.1")))        # 亏损
    s.append(obs(code="900002", equity_parent=D(-5), total_equity=D(-5)))  # 负净资产
    r = regress("pb_roe", s, _tgt(roe=D("0.2")), D(1))
    why = {c: w for c, _, w in r.excluded}
    assert why["600183"] == "本司"
    assert "亏损" in why["900001"] and "净资产" in why["900002"]
    assert any("P5–P95" in w for w in why.values())
    assert "600183" not in {c for c, _, _, _ in r.sample}


def _search(cond, **kw):
    """找一个满足前提的随机种子 —— 让「不显著」这类用例不靠运气。"""
    for seed in range(500):
        s = _pb_sample(seed=seed, **kw)
        r = regress("pb_roe", s, _tgt(roe=D("0.15")), D(1))
        if cond(r):
            return r
    raise AssertionError("找不到满足前提的样本")


def test_样本不足十五家不可用():
    r = regress("pb_roe", _pb_sample(n=15), _tgt(roe=D("0.15")), D(1))
    assert r.n < 15 and not r.usable and "样本" in r.why and r.fit is None


def test_斜率方向相反不可用():
    r = regress("pb_roe", _pb_sample(b=-20.0), _tgt(roe=D("0.15")), D(1))
    assert not r.usable and "斜率" in r.why and r.fit is None


def test_斜率不显著不可用():
    r = _search(lambda r: r.b > 0 and r.t < 2, b=0.5, noise=3.0)
    assert not r.usable and "不显著" in r.why and r.fit is None


def test_应得倍数不为正不可用():
    r = regress("pb_roe", _pb_sample(), _tgt(roe=D("-0.5")), D(1))
    assert not r.usable and "倍数" in r.why and r.fit is None


def test_不可用时仍保留统计量供页面展示():
    r = regress("pb_roe", _pb_sample(b=-20.0), _tgt(roe=D("0.15")), D(1))
    assert r.n > 0 and r.b < 0 and r.sample


def test_不可用原因不含数字():
    """原因会进研报摘要，而摘要不许有裸数字。"""
    from ir_agent.citation import audit_bare_numbers
    for r in (regress("pb_roe", _pb_sample(n=15), _tgt(roe=D("0.15")), D(1)),
              regress("pb_roe", _pb_sample(b=-20.0), _tgt(roe=D("0.15")), D(1)),
              regress("pb_roe", _pb_sample(), _tgt(roe=D("-0.5")), D(1))):
        assert audit_bare_numbers(r.why) == []


def test_本司亏损时PE回归不出价格():
    s = [obs(code=f"{i:06d}", market_cap=D(10 + 2 * i), net_profit=D(1),
             growth=D(str(0.01 * i))) for i in range(40)]
    r = regress("pe_growth", s, _tgt(net_profit=D(-1), growth=D("0.2")), D(1))
    assert r.fit is None and "不适用" in r.why


def test_PE回归用增速():
    s = [obs(code=f"{i:06d}", market_cap=D(str(10 + 50 * 0.01 * i + (i % 3))),
             net_profit=D(1), growth=D(str(0.01 * i))) for i in range(40)]
    r = regress("pe_growth", s, _tgt(growth=D("0.2"), net_profit=D(1)), D(1))
    assert r.b > 0 and r.kind == "pe_growth"


# ── 结果对象：摘要只用占位符，且每个都落了账本 ─────────────────────

from datetime import date  # noqa: E402

from ir_agent.citation import REF_RE, audit_bare_numbers  # noqa: E402
from ir_agent.relval import Regression, RelvalResult, to_facts  # noqa: E402

AS_OF = date(2026, 10, 21)


def make_result(pe_unusable=False, comps_missing=False) -> RelvalResult:
    tgt = _tgt(roe=D("0.2"), growth=D("0.2"), net_profit=D("0.2"))
    bands, notes = comps_bands(_peers_pe(10, 20, 30, 40, 50, 60), tgt, D(1))
    if comps_missing:
        bands["ev_ebitda"] = None
        notes.append("EV/EBITDA：同业有效值不足四家，不适用")
    pe_s = [obs(code=f"{i:06d}", market_cap=D(str(10 + 50 * 0.01 * i + (i % 3))),
                net_profit=D(1), growth=D(str(0.01 * i))) for i in range(40)]
    regs = {"pb_roe": regress("pb_roe", _pb_sample(), tgt, D(1)),
            "pe_growth": (Regression(kind="pe_growth", why="斜率不显著") if pe_unusable
                          else regress("pe_growth", pe_s, tgt, D(1)))}
    return RelvalResult(as_of=AS_OF, price=D(100), shares=D(1), target=tgt,
                        comps=bands, comps_notes=notes, regs=regs,
                        sample_size=46, industry="电子-元件-印制电路板")


def test_摘要每个占位符都落了账本():
    res = make_result()
    refs = {(k, p) for _, k, p in REF_RE.findall(res.summary())}
    assert refs and refs <= {(f.key, f.period) for f in to_facts(res, AS_OF)}


def test_摘要过裸数字审计():
    for res in (make_result(), make_result(pe_unusable=True, comps_missing=True)):
        assert audit_bare_numbers(res.summary()) == []


def test_键都以valuation开头且期间为估值时点():
    fs = to_facts(make_result(), AS_OF)
    assert fs and all(f.key.startswith("valuation.") for f in fs)
    assert {f.period for f in fs} == {AS_OF.isoformat()}


def test_可比与回归的关键数都落账本():
    fs = {f.key: f for f in to_facts(make_result(), AS_OF)}
    assert fs["valuation.rel_pe_mid"].unit == "元"
    assert fs["valuation.rel_pe_mult_mid"].value == D(35)
    assert {"valuation.rel_reg_pb_fit", "valuation.rel_reg_pb_lo",
            "valuation.rel_reg_pb_hi", "valuation.rel_reg_pb_mult",
            "valuation.rel_reg_pb_r2", "valuation.rel_reg_pb_n"} <= set(fs)


def test_不可用回归只写原因不落数():
    res = make_result(pe_unusable=True)
    assert not [f for f in to_facts(res, AS_OF) if f.key.startswith("valuation.rel_reg_pe")]
    assert "斜率不显著" in res.summary()


def test_不适用的可比倍数写原因不落数():
    res = make_result(comps_missing=True)
    assert not [f for f in to_facts(res, AS_OF) if f.key.startswith("valuation.rel_ev_ebitda")]
    assert "不足四家" in res.summary()


def test_外推要在摘要里说出来():
    res = make_result()
    res.regs["pb_roe"].extrapolated = True
    assert "外推" in res.summary()


# ── 审查意见 1：退路口径要显示，本司口径不一致不能静默算价 ─────────────

def _growth_sample(one_year=()):
    return [obs(code=f"{i:06d}", market_cap=D(str(10 + 50 * 0.01 * i + (i % 3))),
                net_profit=D(1), growth=D(str(0.01 * i)),
                growth_basis="1y" if i in one_year else "3y") for i in range(40)]


def test_样本里用一年增速的公司带标记():
    r = regress("pe_growth", _growth_sample(one_year={5, 6}), _tgt(growth=D("0.2"), net_profit=D(1)), D(1))
    assert r.flags.get("000005") == "一年增速" and "000007" not in r.flags


def test_本司只有一年增速时PE回归不可用():
    r = regress("pe_growth", _growth_sample(),
                _tgt(growth=D("0.2"), growth_basis="1y", net_profit=D(1)), D(1))
    assert not r.usable and "口径" in r.why and r.fit is None
    assert audit_bare_numbers(r.why) == []


def test_可比名单为空时原因不说成不足四家():
    bands, why = comps_bands([], obs(), shares=D(1))
    assert all(b is None for b in bands.values())
    assert all("可比名单未取到" in w for w in why) and len(why) == 3


# ── 试跑暴露：不可用回归的统计量也要落账本 ────────────────────────
# 生益科技第三次试跑：摘要写「PE 对增速：不可用 —— 斜率不显著」，但 t 值不在账本里，
# 辩论裁判判「无据断言」成立，回写把 PE 回归的话全删了、综合区间的取法也跟着改了。

def _t_low():
    return _search(lambda r: r.b > 0 and r.t < 2, b=0.5, noise=3.0)


def test_不可用回归的统计量也落账本():
    res = make_result()
    res.regs["pe_growth"] = _t_low()
    keys = {f.key for f in to_facts(res, AS_OF)}
    assert {"valuation.rel_reg_pe_t", "valuation.rel_reg_pe_slope",
            "valuation.rel_reg_pe_n", "valuation.rel_reg_pe_r2"} <= keys
    assert "valuation.rel_reg_pe_fit" not in keys


def test_不可用回归在摘要里给出可核验的t值():
    res = make_result()
    res.regs["pe_growth"] = _t_low()
    line = next(x for x in res.summary().splitlines() if "PE 对增速" in x)
    assert "不可用" in line and "valuation.rel_reg_pe_t" in line
    refs = {(k, p) for _, k, p in REF_RE.findall(res.summary())}
    assert refs <= {(f.key, f.period) for f in to_facts(res, AS_OF)}
    assert audit_bare_numbers(res.summary()) == []


def test_可用回归也给出斜率与t值():
    keys = {f.key for f in to_facts(make_result(), AS_OF)}
    assert {"valuation.rel_reg_pb_t", "valuation.rel_reg_pb_slope"} <= keys
