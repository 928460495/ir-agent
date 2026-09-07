"""Bear/Base/Bull —— 战略分析通向估值那座桥的另一端。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.strategy import Uncertainty
from ir_agent.valuation import scenarios as S
from ir_agent.valuation.assumptions import Assumptions
from ir_agent.valuation.basis import Basis
from ir_agent.valuation.scenarios import Case, ScenarioFailed

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2025FY"
BODY = "渠道去库存压力持续，参考历史最差年份的增速水平。"
FCF, ND, SH = D("1000"), D("-500"), D("100")


def _base(g=("0.08", "0.06", "0.04"), wacc="0.09", tg="0.025"):
    b = [Basis.report("参考历史最差年份")]
    return Assumptions(wacc=D(wacc), terminal_growth=D(tg),
                       growth_rates=[D(x) for x in g],
                       basis={"wacc": b, "terminal_growth": b,
                              "growth_rates": b},
                       reviewed_by="model", reviewed_at=AS_OF)


def _unc(driver="revenue_growth", direction="下调", what="渠道去库存"):
    return Uncertainty(what=what, driver=driver, direction=direction,
                       bases=[Basis.report("参考历史最差年份")])


def _shift(driver="revenue_growth", bear="0.02", bull="0.12",
           what="渠道去库存"):
    return f"情景: {what} | {driver} | {bear} | {bull} | 「参考历史最差年份」"


def _build(text, uncerts=None, base=None):
    return S.build(base or _base(), S.parse(text),
                   uncerts if uncerts is not None else [_unc()],
                   fcf_base=FCF, net_debt=ND, shares=SH,
                   ledger=FactLedger(), body=BODY, as_of=AS_OF, period=P)


# ── 解析 ─────────────────────────────────────────────────────

def test_解析两端取值():
    s = S.parse(_shift())[0]
    assert s.driver == "revenue_growth" and s.bear == D("0.02")


def test_百分号写法():
    assert S.parse(_shift(bear="2%", bull="12%"))[0].bear == D("0.02")


def test_不可建模的驱动被驳回():
    with pytest.raises(ScenarioFailed, match="无法落到估值模型"):
        S.parse(_shift(driver="gross_margin"))


def test_缺依据被驳回():
    with pytest.raises(ScenarioFailed, match="缺少可核验的依据"):
        S.parse("情景: x | wacc | 0.10 | 0.08 | 我觉得")


# ── 方向一致性: 这里最要紧的一条 ─────────────────────────────

def test_熊市朝不利方向移动则通过():
    _build(_shift(bear="0.02", bull="0.12"))


def test_熊市朝相反方向调则驳回():
    """战略说「不利时下调」，熊市反而调高 —— 那不是情景分析，是自相矛盾。"""
    with pytest.raises(ScenarioFailed, match="相反方向|脱钩"):
        _build(_shift(bear="0.12", bull="0.02"))


def test_不利方向为上调时反过来():
    """WACC 的不利方向是上调 —— 风险变大折现率上升。"""
    _build(_shift(driver="wacc", bear="0.11", bull="0.07", what="经营风险上升"),
           uncerts=[_unc(driver="wacc", direction="上调", what="经营风险上升")])


def test_熊市与基准相同则驳回():
    """不确定性若不改变任何取值，它就不构成情景。"""
    with pytest.raises(ScenarioFailed, match="不构成情景"):
        _build(_shift(bear="0.08", bull="0.12"))


def test_两端必须分居基准两侧():
    with pytest.raises(ScenarioFailed, match="同向|两侧"):
        _build(_shift(bear="0.02", bull="0.05"))


def test_方向说法无法判向时不硬判():
    """战略里写的是「变化」这类含糊说法 —— 不硬判总好过判错。"""
    _build(_shift(bear="0.02", bull="0.12"),
           uncerts=[_unc(direction="发生变化")])


# ── 同时兑现，而非逐个扰动 ───────────────────────────────────

def test_熊市里多条不利因素同时兑现():
    """需求转弱既压增速也压提价能力 —— 逐个单独动一次得到的是敏感性，
    不是情景。"""
    text = "\n".join([_shift(bear="0.02", bull="0.12"),
                      _shift(driver="wacc", bear="0.11", bull="0.08",
                             what="经营风险上升")])
    out = _build(text, uncerts=[_unc(), _unc(driver="wacc", direction="上调",
                                             what="经营风险上升")])
    bear_a = S._apply(_base(), out.shifts, Case.BEAR)
    assert bear_a.wacc == D("0.11") and bear_a.growth_rates[0] == D("0.02")


def test_增速整体平移而非只改首年():
    """只改首年而让后续年份不动，等于假设冲击一年就消失。"""
    a = S._apply(_base(g=("0.08", "0.06", "0.04")),
                 S.parse(_shift(bear="0.02")), Case.BEAR)
    assert a.growth_rates == [D("0.02"), D("0.00"), D("-0.02")]


def test_基准情景不改动假设():
    b = _base()
    assert S._apply(b, S.parse(_shift()), Case.BASE) is b


# ── 估值与自洽 ───────────────────────────────────────────────

def test_三档估值单调():
    out = _build(_shift())
    v = [out.values[c].value_per_share for c in
         (Case.BEAR, Case.BASE, Case.BULL)]
    assert v[0] < v[1] < v[2]


def test_牛熊区间可算():
    out = _build(_shift())
    assert out.spread() > 0


def test_情景不得突破永续小于wacc():
    """永续增速在任何情景下都必须低于 WACC —— 情景不能突破这条约束。"""
    with pytest.raises(ScenarioFailed, match="不自洽|永续"):
        _build(_shift(driver="terminal_growth", bear="0.01", bull="0.095",
                      what="长期空间"),
               uncerts=[_unc(driver="terminal_growth", direction="下调",
                             what="长期空间")])


def test_没有情景就是桥只搭了一半():
    with pytest.raises(ScenarioFailed, match="只搭了一半"):
        _build("")


def test_依据不可核验则驳回():
    with pytest.raises(ScenarioFailed, match="不可核验"):
        S.build(_base(), S.parse("情景: x | wacc | 0.11 | 0.07 | 「查无此句」"),
                [_unc(driver="wacc", direction="上调", what="x")],
                fcf_base=FCF, net_debt=ND, shares=SH,
                ledger=FactLedger(), body=BODY, as_of=AS_OF)


# ── 接不上的那三个驱动因子 ───────────────────────────────────

def test_毛利率类不确定性如实报告为暂不可建模():
    """硬编一个「毛利率降 1pp 对应 FCF 降多少」的系数，
    等于凭空发明一条不存在的关系。"""
    out = _build(_shift(), uncerts=[_unc(), _unc(driver="gross_margin",
                                                what="提价能力减弱")])
    assert ("提价能力减弱", "gross_margin") in out.unmodellable
    assert "无处落脚" in out.summary() and "三张表" in out.summary()


def test_六个可映射驱动里只有三个接得上():
    from ir_agent.strategy import MAPPABLE_DRIVERS
    assert set(S.MODELLABLE) < set(MAPPABLE_DRIVERS)
    assert set(MAPPABLE_DRIVERS) - set(S.MODELLABLE) == {
        "gross_margin", "opex_pct_revenue", "capex_pct_revenue"}


# ── 落账本与提示词 ───────────────────────────────────────────

def test_情景估值落账本():
    out = _build(_shift())
    fs = {f.key for f in S.scenario_facts(out, P, AS_OF)}
    assert fs == {"valuation.per_share.bear", "valuation.per_share.base",
                  "valuation.per_share.bull"}


def test_摘要无裸数字():
    from ir_agent.citation import audit_bare_numbers
    out = _build(_shift(), uncerts=[_unc(), _unc(driver="gross_margin",
                                                what="提价能力减弱")])
    assert audit_bare_numbers(out.summary()) == []


def test_锚点只给可观察的事实():
    """提供锚点，但不代模型定幅度 —— 「这次会不会更差」是判断。"""
    led = FactLedger()
    for k, p in (("revenue.yoy", "2024FY"), ("consensus.eps_low", "2026FY"),
                 ("gross_margin", "2024FY")):
        led.put(Fact(key=k, value=D("0.1"), unit="ratio", period=p,
                     as_of=date(2025, 1, 1), source_id="t",
                     method=Method.COMPUTED))
    got = S.anchors(led, AS_OF)
    assert "[[revenue.yoy@2024FY]]" in got
    assert "[[consensus.eps_low@2026FY]]" in got
    assert not any("gross_margin" in x for x in got)


def test_提示词强调同时兑现与留下理由():
    assert "同时" in S.PROMPT and "光给个数字不是" in S.PROMPT


# ── 接线: full._load_scenarios ───────────────────────────────

class _Client:
    def __init__(self, *r):
        self.replies, self.prompts = list(r), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


class _Run:
    """最小可用的 ResearchRun 替身。"""

    def __init__(self, view):
        from ir_agent.ledger import Fact as F
        self.period, self.strategy_view = P, view
        self.ledger = FactLedger()
        vals = {"cf_operating": 2000, "capex": 500,
                "cash_and_equivalents": 800, "short_loan": 300}
        for k, v in vals.items():
            self.ledger.put(F(key=k, value=D(str(v)), unit="元",
                              currency="CNY", period=P,
                              as_of=date(2025, 1, 1), source_id="t",
                              method=Method.REPORTED))
        for k, v in (("market_cap", 10000), ("last_price", 100)):
            self.ledger.put(F(key=k, value=D(str(v)), unit="元",
                              currency="CNY", period="2026-09-04",
                              as_of=date(2026, 9, 4), source_id="t",
                              method=Method.REPORTED))


class _View:
    def __init__(self, uncertainties):
        self.uncertainties = uncertainties


def test_接线_情景落账本并进摘要():
    from ir_agent.full import _load_scenarios

    r = _Run(_View([_unc()]))
    out, md, err = _load_scenarios(r, BODY, _base(), AS_OF,
                                   _Client(_shift()), "2026-09-04")
    assert err == "" and out is not None
    assert "[[valuation.per_share.bear@2025FY]]" in md
    assert r.ledger.get("valuation.per_share.bull", P, as_of=AS_OF)


def test_接线_无不确定性则不做情景():
    """战略分析没跑通时，情景没有依据可循 —— 如实报告而非退回调参数。"""
    from ir_agent.full import _load_scenarios

    out, md, err = _load_scenarios(_Run(_View([])), BODY, _base(), AS_OF,
                                   _Client(), "2026-09-04")
    assert out is None and "无战略分析产出" in err


def test_接线_驳回后重试并带回原因():
    from ir_agent.full import _load_scenarios

    r = _Run(_View([_unc()]))
    bad = _shift(bear="0.12", bull="0.02")          # 方向反了
    c = _Client(bad, _shift())
    out, _, err = _load_scenarios(r, BODY, _base(), AS_OF, c, "2026-09-04")
    assert err == "" and len(c.prompts) == 2 and "被驳回" in c.prompts[1]


def test_接线_连续失败如实报告():
    from ir_agent.full import _load_scenarios

    bad = _shift(bear="0.12", bull="0.02")
    out, _, err = _load_scenarios(_Run(_View([_unc()])), BODY, _base(), AS_OF,
                                  _Client(bad, bad, bad), "2026-09-04")
    assert out is None and "连续三次" in err


def test_接线_估值输入缺失时说清原因():
    from ir_agent.full import _load_scenarios

    r = _Run(_View([_unc()]))
    r.ledger = FactLedger()
    out, _, err = _load_scenarios(r, BODY, _base(), AS_OF,
                                  _Client(_shift()), "2026-09-04")
    assert out is None and "估值输入缺失" in err


def test_情景占位符必须带期间():
    """`[[valuation.per_share.bear]]` 缺 @period 就不是合法占位符 ——
    render() 不会替换它，会把这段坏文本原样印进研报。**比被驳回更糟**:
    驳回还能重试，这个是静默出错。写这个模块时我自己踩到了。"""
    from ir_agent.citation import REF_RE
    md = _build(_shift()).summary()
    refs = [m.group(0) for m in REF_RE.finditer(md)]
    assert refs, "摘要里应有合法占位符"
    assert "[[valuation.per_share.bear]]" not in md
    for r in refs:
        assert REF_RE.fullmatch(r)
