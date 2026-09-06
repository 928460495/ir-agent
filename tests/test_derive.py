"""假设推导。建议值必须落在可核验的事实上，推不出来时不得编默认值。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.valuation.basis import BasisKind
from ir_agent.valuation.derive import (
    FALLBACK_CEILING,
    Proposal,
    derive,
    growth_rates,
    terminal_ceiling,
)

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2025FY"


def _led(pairs, as_of=date(2025, 1, 1)):
    led = FactLedger()
    for key, per, val in pairs:
        led.put(Fact(key=key, value=D(val), unit="ratio", period=per,
                     as_of=as_of, source_id="t", method=Method.COMPUTED))
    return led


def _full(yoy="-0.0121", ind="-0.1826", excess="0.1705", share="0.0813",
          gdp="0.0543", cagr="0.0626"):
    return _led([("revenue.yoy", P, yoy),
                 ("industry.revenue_yoy", P, ind),
                 ("company.revenue_yoy_vs_industry", P, excess),
                 ("company.market_share_delta", P, share),
                 ("macro.gdp_nominal_yoy", "2026H1", gdp),
                 ("macro.gdp_nominal_cagr", "2025FY", cagr)])


# ── 永续增长率: 只给上限 ──────────────────────────────────────

def test_上限取当期同比与CAGR中的较小者():
    """两个参考值都有偏 —— 点值受周期位置影响，CAGR 受基期影响。取小的保守。"""
    p = terminal_ceiling(_full(gdp="0.0543", cagr="0.0626"), AS_OF)
    assert p.value == D("0.0543")
    assert len(p.bases) == 2
    assert all(b.kind is BasisKind.FACT for b in p.bases)
    assert "取较小者" in p.note


def test_CAGR更小时取CAGR():
    assert terminal_ceiling(_full(gdp="0.08", cagr="0.04"), AS_OF).value == D("0.04")


def test_上限本身受兜底值约束():
    """名义 GDP 异常高企时不能把线划得比兜底还松。"""
    assert terminal_ceiling(_full(gdp="0.12", cagr="0.11"),
                            AS_OF).value == FALLBACK_CEILING


def test_不给永续增速的建议值只给上限():
    """永续增速刻画无限期之后的状态，任何单期数据都推不出它。"""
    note = terminal_ceiling(_full(), AS_OF).note
    assert "只给上限不给建议值" in note
    assert "推不出" in note


def test_无宏观事实时退回兜底上限并说明():
    p = terminal_ceiling(FactLedger(), AS_OF)
    assert p.value == FALLBACK_CEILING
    assert "兜底" in p.note and p.bases == []


def test_只有一个宏观口径时也能给上限():
    led = _led([("macro.gdp_nominal_yoy", "2026H1", "0.045")])
    p = terminal_ceiling(led, AS_OF)
    assert p.value == D("0.045") and len(p.bases) == 1


def test_取账本中最新一期的宏观事实():
    led = _full()
    led.put(Fact(key="macro.gdp_nominal_yoy", value=D("0.02"), unit="ratio",
                 period="2026Q1", as_of=date(2025, 1, 1),
                 source_id="t", method=Method.COMPUTED))
    # 2026H1 比 2026Q1 新 —— 字典序会选错
    refs = {b.ref for b in terminal_ceiling(led, AS_OF).bases}
    assert "[[macro.gdp_nominal_yoy@2026H1]]" in refs


def test_未到披露日的宏观事实不参与():
    led = _led([("macro.gdp_nominal_yoy", "2026H1", "0.05")],
               as_of=date(2026, 7, 30))
    assert terminal_ceiling(led, date(2026, 7, 1)).value == FALLBACK_CEILING
    assert terminal_ceiling(led, date(2026, 8, 1)).value == D("0.05")


# ── 预测期增速: 衰减期 ────────────────────────────────────────

def test_自公司当期增速线性衰减至永续():
    p = growth_rates(_full(yoy="0.20"), P, AS_OF, years=5, terminal=D("0.04"))
    assert p.values[0] == D("0.20")
    assert p.values[-1] == D("0.04")
    assert p.values == sorted(p.values, reverse=True)      # 单调，无拐点


def test_路径单调不出现拐点():
    """原设计外推行业增速再与永续平均，末年会反弹 —— 那是个拐点，不是模型。"""
    v = growth_rates(_full(), P, AS_OF, years=5, terminal=D("0.03")).values
    diffs = [v[i + 1] - v[i] for i in range(len(v) - 1)]
    assert all(d > 0 for d in diffs) or all(d < 0 for d in diffs)


def test_行业事实进入依据但不参与外推():
    """行业 -18.26% 不该成为未来五年的增速。"""
    p = growth_rates(_full(yoy="-0.0121"), P, AS_OF, years=5, terminal=D("0.03"))
    refs = {b.ref for b in p.bases}
    assert f"[[industry.revenue_yoy@{P}]]" in refs
    assert f"[[company.market_share_delta@{P}]]" in refs
    assert p.values[0] == D("-0.0121")           # 起点是公司增速，不是行业增速
    assert all(r > D("-0.18") for r in p.values)  # 没有一年退到行业当期增速


def test_行业口径写进说明供审核判断起点():
    note = growth_rates(_full(), P, AS_OF, terminal=D("0.03")).note
    assert "行业增速 -18.26%" in note
    assert "份额变化 +8.13%" in note
    assert "供审核判断起点" in note


def test_无永续增速时以起点平推并说明():
    p = growth_rates(_full(yoy="0.10"), P, AS_OF, years=4)
    assert p.values == [D("0.10")] * 4


def test_缺行业事实时仍可推导但点明缺失():
    led = _led([("revenue.yoy", P, "0.10")])
    p = growth_rates(led, P, AS_OF, years=3, terminal=D("0.04"))
    assert p.ok and len(p.bases) == 1
    assert "行业事实缺失" in p.note


def test_无公司增速时不给默认值():
    """给一个「看起来合理」的默认值等于绕过人工审核门。"""
    p = growth_rates(_led([("industry.revenue_yoy", P, "0.05")]), P, AS_OF)
    assert not p.ok and p.bases == []
    assert "需人工填写" in p.describe()


def test_单年预测不触发除零():
    assert len(growth_rates(_full(), P, AS_OF, years=1).values) == 1


# ── 汇总 ─────────────────────────────────────────────────────

def test_汇总声明WACC仍需人工():
    d = derive(_full(), P, AS_OF)
    assert "人工填写" in d.wacc_note and "无风险利率" in d.wacc_note
    assert "建议而非决定" in d.describe()


def test_汇总接受已定的永续增速用于收敛():
    d = derive(_full(yoy="0.20"), P, AS_OF, years=5, terminal=D("0.03"))
    assert d.growth.values[-1] == D("0.03")


def test_汇总在事实缺失时如实报告而非崩溃():
    d = derive(FactLedger(), P, AS_OF)
    assert not d.growth.ok
    assert d.terminal_ceiling.value == FALLBACK_CEILING
    assert "无法推导" in d.describe()


def test_建议值全部为Decimal():
    d = derive(_full(), P, AS_OF, terminal=D("0.03"))
    assert isinstance(d.terminal_ceiling.value, Decimal)
    assert all(isinstance(v, Decimal) for v in d.growth.values)


def test_proposal_ok_判据():
    assert not Proposal("k").ok
    assert Proposal("k", value=D("0.1")).ok
    assert Proposal("k", values=[D("0.1")]).ok


def test_永续未定时说明必须说平推而非衰减():
    """说明与实际不符会误导审核者以为已经收敛过。"""
    p = growth_rates(_full(yoy="0.10"), P, AS_OF, years=4)
    assert "平推" in p.note and "线性衰减至永续" not in p.note
    q = growth_rates(_full(yoy="0.10"), P, AS_OF, years=4, terminal=D("0.03"))
    assert "线性衰减至永续" in q.note
