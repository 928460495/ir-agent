"""相对估值通用化（spec 2026-11-21-relval-fundamentals）：驱动表、基本面观测、按业务与基本面挑可比。不联网。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import relval
from ir_agent.relval import DRIVERS, PeerObs
from ir_agent.relval_data import obs_from_facts
from tests.test_relval_data import AS_OF, F, facts_years

D = Decimal


# ── Task 1：驱动表与基本面观测 ─────────────────────────────────

def test_驱动表覆盖全部五个倍数():
    assert set(DRIVERS) == {"pe", "pe_ntm", "pb", "ev_ebitda", "ev_sales"}
    assert DRIVERS["pe"] == ("growth", "roe3y")
    assert DRIVERS["pb"] == ("roe3y", "growth")
    assert DRIVERS["ev_ebitda"] == ("ebitda_margin", "growth")
    assert DRIVERS["ev_sales"] == ("ebitda_margin", "growth")


def _with_roe(np_by_year: dict[int, float], eq=50):
    out = facts_years()
    for y, npv in np_by_year.items():
        p = f"{y}FY"
        out[("net_profit_attr_parent", p)] = F("net_profit_attr_parent", p, npv, date(y + 1, 4, 25))
        out[("equity_attr_parent", p)] = F("equity_attr_parent", p, eq, date(y + 1, 4, 25))
    return out


def test_三年平均ROE取近三个年报():
    o = obs_from_facts("002384", "某同业", _with_roe({2023: 5, 2024: 10, 2025: 15}),
                       "2025FY", D(1000), AS_OF)
    assert o.roe3y == pytest.approx(D("0.2"))          # (0.1 + 0.2 + 0.3) / 3
    assert o.roe3y_basis == "3y"


def test_三年平均ROE不足三年按实有年数并标注():
    facts = {k: v for k, v in _with_roe({2024: 10, 2025: 20}).items()
             if k[1] in ("2024FY", "2025FY")}
    o = obs_from_facts("002384", "某同业", facts, "2025FY", D(1000), AS_OF)
    assert o.roe3y == pytest.approx(D("0.3"))          # (0.2 + 0.4) / 2
    assert o.roe3y_basis == "2y"


def test_三年平均ROE跳过净资产不为正的年份():
    facts = _with_roe({2023: 5, 2024: 10, 2025: 15})
    facts[("equity_attr_parent", "2023FY")] = F("equity_attr_parent", "2023FY", -1, date(2024, 4, 25))
    o = obs_from_facts("002384", "某同业", facts, "2025FY", D(1000), AS_OF)
    assert o.roe3y == pytest.approx(D("0.25"))
    assert o.roe3y_basis == "2y"


def test_EBITDA利润率等于EBITDA除营收且可为负():
    o = obs_from_facts("002384", "某同业", facts_years(), "2025FY", D(1000), AS_OF)
    assert o.ebitda_margin == o.ebitda / o.revenue
    facts = facts_years()
    facts[("operating_profit", "2025FY")] = F("operating_profit", "2025FY", -40, date(2026, 4, 25))
    o2 = obs_from_facts("002384", "某同业", facts, "2025FY", D(1000), AS_OF)
    assert o2.ebitda_margin is not None and o2.ebitda_margin < 0


def test_A股观测带营收():
    o = obs_from_facts("002384", "某同业", facts_years(), "2025FY", D(1000), AS_OF)
    assert o.revenue is not None and o.revenue > 0


# ── Task 2：业务距离优先、基本面距离其次 ───────────────────────

from ir_agent.relval import RELVAL_K, business_distance, select_by_drivers   # noqa: E402

CN3 = ("食品饮料", "白酒Ⅱ", "白酒Ⅲ")
CN3B = ("食品饮料", "白酒Ⅱ", "其他酒类Ⅲ")


def _o(code, *, roe=0.2, g=0.1, margin=0.3, path=CN3, np_=D(100), eq=D(500), eb=D(150)):
    return PeerObs(code=code, name=code, market_cap=D(5000), net_profit=np_, np_basis="TTM",
                   equity_parent=eq, total_equity=eq + 10, debt=D(0), cash=D(50), ebitda=eb,
                   roe=None, growth=D(str(g)), growth_basis="3y", revenue=D(500),
                   roe3y=D(str(roe)), roe3y_basis="3y", ebitda_margin=D(str(margin)), path=path)


def test_A股Member路径取自申万层级():
    from ir_agent.sources.industry import peer_set
    from ir_agent.sources.taxonomy import Entry, Taxonomy

    tax = Taxonomy([Entry("600519", "贵州茅台", "食品饮料-白酒Ⅱ-白酒Ⅲ")]
                   + [Entry(f"60000{i}", f"同业{i}", "食品饮料-白酒Ⅱ-白酒Ⅲ") for i in range(6)],
                   date(2026, 11, 1))
    ps = peer_set("600519", taxonomy=tax)
    assert ps.members[0].path == CN3


def test_业务距离按层级():
    assert business_distance(CN3, CN3, "cn") == 0
    assert business_distance(CN3, CN3B, "cn") == 1
    assert business_distance(CN3, ("食品饮料", "饮料乳品", "乳品"), "cn") is None   # A 股底线二级
    assert business_distance(("3571",), ("3571",), "us") == 0
    assert business_distance(("3571",), ("3572",), "us") == 1
    assert business_distance(("3571",), ("3661",), "us") == 2
    assert business_distance((), CN3, "cn") is None


def test_同业务距离按基本面挑前k家():
    tgt = _o("600519", roe=0.30)
    sample = [_o(f"60000{i}", roe=0.30 - 0.02 * i) for i in range(9)]
    comps, rows, note = select_by_drivers("pb", sample, tgt)
    assert len(comps) == RELVAL_K
    assert [c.code for c in comps] == [f"60000{i}" for i in range(RELVAL_K)]
    assert all(bd == 0 for _, bd, _ in rows)
    assert "同申万三级" in note


def test_业务距离0够k家时不取距离1():
    tgt = _o("600519", roe=0.30)
    near0 = [_o(f"60000{i}", roe=0.10 + 0.01 * i) for i in range(6)]     # 基本面远但同三级
    near1 = [_o(f"60100{i}", roe=0.30, path=CN3B) for i in range(3)]      # 基本面近但只同二级
    comps, rows, note = select_by_drivers("pb", near0 + near1, tgt)
    assert {c.code for c in comps} == {o.code for o in near0}


def test_业务距离0不够时用1补并写明():
    tgt = _o("600519")
    near0 = [_o(f"60000{i}") for i in range(3)]
    near1 = [_o(f"60100{i}", path=CN3B) for i in range(5)]
    comps, rows, note = select_by_drivers("pb", near0 + near1, tgt)
    assert len(comps) == RELVAL_K
    assert sum(1 for _, bd, _ in rows if bd == 1) == 3
    assert "同申万二级" in note


def test_只在倍数适用且驱动齐全的样本里选():
    tgt = _o("600519")
    ok = [_o(f"60000{i}") for i in range(4)]
    loss = _o("600100", np_=D(-5))                 # PE 不适用
    no_drv = _o("600101"); no_drv.growth = None    # 缺驱动
    comps, _, _ = select_by_drivers("pe", ok + [loss, no_drv], tgt)
    assert {c.code for c in comps} == {o.code for o in ok}


def test_一家离群ROE不改变前k名():
    tgt = _o("600519", roe=0.30, g=0.10)
    base = [_o(f"60000{i}", roe=0.30 - 0.015 * i, g=0.10 + 0.01 * (i % 3)) for i in range(10)]
    picked = {c.code for c in select_by_drivers("pb", base, tgt)[0]}
    outlier = _o("600999", roe=5.0, g=0.10)
    picked2 = {c.code for c in select_by_drivers("pb", base + [outlier], tgt)[0]}
    # 入选名单不变（内部次序可随四分位距微调）；离群点本身不入选
    assert picked2 == picked and "600999" not in picked2


def test_四分位距为0的维度不参与():
    """增速全相同 → 只按 ROE 算距离：第 i 家的距离 = |ΔROE| ÷ ROE 的四分位距。"""
    import numpy as np

    tgt = _o("600519", roe=0.30, g=0.10)
    roes = [0.30 - 0.02 * i for i in range(6)]
    sample = [_o(f"60000{i}", roe=r, g=0.10) for i, r in enumerate(roes)]
    _, rows, _ = select_by_drivers("pb", sample, tgt)
    q1, q3 = np.percentile(roes, [25, 75])
    for i, (_, _, dist) in enumerate(rows):
        assert dist == pytest.approx(0.02 * i / (q3 - q1))


def test_不足四家返回空与原因():
    tgt = _o("600519")
    comps, rows, note = select_by_drivers("pb", [_o(f"60000{i}") for i in range(3)], tgt)
    assert comps == [] and "不足" in note


def test_本司缺驱动返回空与原因():
    tgt = _o("600519"); tgt.roe3y = None
    comps, _, note = select_by_drivers("pb", [_o(f"60000{i}") for i in range(6)], tgt)
    assert comps == [] and "本司" in note


def test_A股取数把申万路径带进观测(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ir_agent.relval_data import gather
    from tests.test_relval_data import _patch_quotes, fake_r

    _patch_quotes(monkeypatch, ["600183", "002384", "002463"])
    r = fake_r({"002384": facts_years(), "002463": facts_years()})
    r.peer_set.members = [SimpleNamespace(code="002384", name="甲", path=("电子", "元件", "印制电路板")),
                          SimpleNamespace(code="002463", name="乙", path=("电子", "元件", "被动元件"))]
    r.peer_set.classification.path = "电子-元件-印制电路板"
    tgt, sample, *_ = gather(r, AS_OF, tmp_path)
    assert tgt.path == ("电子", "元件", "印制电路板")
    assert {o.code: o.path for o in sample}["002463"] == ("电子", "元件", "被动元件")



# ── Task 3：可比区间用驱动可比 + 调和平均 + 退回 ─────────────────

from ir_agent.relval import HM_GAP_WARN, comps_bands   # noqa: E402


def _pe_peer(code, pe, **kw):
    o = _o(code, **kw)
    o.market_cap = D(str(pe)) * o.net_profit
    return o


def test_各倍数用各自名单():
    tgt = _o("600519")
    pe_list = [_pe_peer(f"60000{i}", pe) for i, pe in enumerate((10, 12, 14, 16))]
    pb_list = [_pe_peer(f"60100{i}", 30) for i in range(5)]
    bands, _ = comps_bands([], tgt, D(1), kinds=("pe", "pb"),
                           comps_by_kind={"pe": pe_list, "pb": pb_list})
    assert bands["pe"].n == 4 and bands["pb"].n == 5
    assert bands["pe"].mult_mid == D(13)


def test_调和平均只用正倍数():
    tgt = _o("600519")
    peers = [_pe_peer(f"60000{i}", pe) for i, pe in enumerate((10, 20, 40, 40))]
    b = comps_bands(peers, tgt, D(1), kinds=("pe",))[0]["pe"]
    assert b.mult_hm == pytest.approx(D(4) / (D(1) / 10 + D(1) / 20 + D(1) / 40 + D(1) / 40))
    assert b.hm == pytest.approx(b.mult_hm * tgt.net_profit)


def test_EV倍数为负的同业不进调和平均():
    tgt = _o("600519")
    peers = [_pe_peer(f"60000{i}", 20) for i in range(4)]
    for p in peers:
        p.ebitda = D(100)
    neg = _pe_peer("600009", 20)
    neg.cash, neg.ebitda = D(10 ** 6), D(100)           # 净现金远超市值 → EV 为负
    b = comps_bands(peers + [neg], tgt, D(1), kinds=("ev_ebitda",))[0]["ev_ebitda"]
    assert b.mult_hm is not None and b.mult_hm > 0


def test_中位数与调和平均差距大出提示():
    tgt = _o("600519")
    peers = [_pe_peer(f"60000{i}", pe) for i, pe in enumerate((2, 30, 40, 50, 60))]
    bands, why = comps_bands(peers, tgt, D(1), kinds=("pe",))
    gap = abs(bands["pe"].mult_mid - bands["pe"].mult_hm) / bands["pe"].mult_mid
    assert gap > D(str(HM_GAP_WARN))
    assert any(w.startswith("PE(TTM)") and "调和平均" in w for w in why)
    assert not any(ch.isdigit() for w in why for ch in w)     # 进摘要的说明不含数字


def test_驱动可比不足退回市值名单(monkeypatch, tmp_path):
    from ir_agent import full, relval_data
    from tests.test_relval_wiring import _r

    tgt = _o("600183")
    sample = [_o(f"60000{i}") for i in range(3)]          # 驱动可比不足四家
    mcap = [_pe_peer(f"60200{i}", 10 + i) for i in range(5)]
    monkeypatch.setattr(relval_data, "gather", lambda *a, **k: (
        tgt, sample, mcap, D(1), D(100), []))
    res, skipped = full._relative_valuation("600183", _r(), AS_OF, None, tmp_path)
    assert [p.code for p in res.comps_by_kind["pe"]] == [p.code for p in mcap]
    assert any("退回按市值" in n for n in res.comps_notes)


def test_驱动可比够时用驱动名单(monkeypatch, tmp_path):
    from ir_agent import full, relval_data
    from tests.test_relval_wiring import _r

    tgt = _o("600183")
    sample = [_pe_peer(f"60000{i}", 10 + i) for i in range(8)]
    mcap = [_pe_peer(f"60200{i}", 50) for i in range(5)]
    monkeypatch.setattr(relval_data, "gather", lambda *a, **k: (
        tgt, sample, mcap, D(1), D(100), []))
    res, _ = full._relative_valuation("600183", _r(), AS_OF, None, tmp_path)
    assert len(res.comps_by_kind["pb"]) == RELVAL_K
    assert {p.code for p in res.comps_by_kind["pb"]} <= {p.code for p in sample}
    assert res.selection["pb"] and res.selection["pb"][0][0].startswith("60000")


def test_摘要与事实带名单与调和平均(monkeypatch, tmp_path):
    from ir_agent import full, relval_data
    from tests.test_relval_wiring import _r

    tgt = _o("600183")
    sample = [_pe_peer(f"60000{i}", 10 + i) for i in range(8)]
    for p in sample:
        p.name = f"同业{chr(0x7532 + int(p.code[-1]))}"
    monkeypatch.setattr(relval_data, "gather", lambda *a, **k: (tgt, sample, [], D(1), D(100), []))
    res, _ = full._relative_valuation("600183", _r(), AS_OF, None, tmp_path)
    s = res.summary()
    assert "[[valuation.rel_pe_mult_hm@" in s and "可比：" in s
    keys = {f.key for f in relval.to_facts(res, AS_OF)}
    assert {"valuation.rel_pe_mult_hm", "valuation.rel_pe_hm"} <= keys
