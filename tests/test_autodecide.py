"""模型自主定假设。人走掉之后，代码能查的必须一条不放。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.valuation.autodecide import (
    MODEL_MARK,
    AutoDecideFailed,
    check,
    decide,
    parse,
)
from ir_agent.valuation.basis import Basis, BasisKind

D = Decimal
AS_OF = date(2026, 9, 7)
BODY = ("公司经营稳健。综合无风险利率与行业风险溢价，折现率取 9%。"
        "行业营收同比为负，但公司份额持续提升。")


def _led():
    led = FactLedger()
    for k, per, v in (("industry.revenue_yoy", "2025FY", "-0.1826"),
                      ("macro.gdp_nominal_yoy", "2026H1", "0.0543")):
        led.put(Fact(key=k, value=D(v), unit="ratio", period=per,
                     as_of=date(2025, 1, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


def _out(wacc="0.09", tg="0.025", rates="-0.012, 0.000, 0.010, 0.018, 0.025",
         wb="「综合无风险利率与行业风险溢价，折现率取 9%」",
         tb="[[macro.gdp_nominal_yoy@2026H1]]",
         gb="[[industry.revenue_yoy@2025FY]]"):
    return (f"wacc: {wacc}\nwacc_basis: {wb}\n"
            f"terminal_growth: {tg}\nterminal_growth_basis: {tb}\n"
            f"growth_rates: {rates}\ngrowth_rates_basis: {gb}\n")


# ── 解析 ─────────────────────────────────────────────────────

def test_解析取值与依据():
    v, b = parse(_out(), years=5)
    assert v["wacc"] == D("0.09") and v["terminal_growth"] == D("0.025")
    assert len(v["growth_rates"]) == 5
    assert b["wacc"][0].kind is BasisKind.REPORT
    assert b["terminal_growth"][0].kind is BasisKind.FACT


def test_接受百分号写法():
    v, _ = parse(_out(wacc="9%", tg="2.5%"), years=5)
    assert v["wacc"] == D("0.09") and v["terminal_growth"] == D("0.025")


def test_增速个数与预测期不符则驳回():
    with pytest.raises(AutoDecideFailed, match="预测期是 5 年"):
        parse(_out(rates="0.01, 0.02"), years=5)


def test_缺项直接驳回():
    with pytest.raises(AutoDecideFailed, match="缺少"):
        parse("wacc: 0.09\nwacc_basis: 「x」", years=5)


def test_依据为空驳回():
    with pytest.raises(AutoDecideFailed, match="缺少依据"):
        parse(_out(wb=""), years=5)


def test_不合法的占位符驳回():
    with pytest.raises(AutoDecideFailed, match="占位符不合法"):
        parse(_out(tb="[[乱写的]]"), years=5)


# ── 校验 ─────────────────────────────────────────────────────

def _checked(**kw):
    v, b = parse(_out(**{k: kw.pop(k) for k in list(kw)
                         if k in ("wacc", "tg", "rates", "wb", "tb", "gb")}),
                 years=5)
    check(v, b, ledger=_led(), body=BODY, as_of=AS_OF,
          ceiling=kw.get("ceiling", D("0.0543")))


def test_永续超上限被驳回():
    with pytest.raises(AutoDecideFailed, match="高于账本宏观事实算出的上限"):
        _checked(tg="0.08", wacc="0.12")


def test_永续不低于WACC被驳回():
    with pytest.raises(AutoDecideFailed, match="终值发散"):
        _checked(tg="0.05", wacc="0.04")


def test_WACC依据必须是正文原话():
    """折现率的理由必须写在读者看得到的地方，不是 YAML 字段里。"""
    with pytest.raises(AutoDecideFailed, match="只能是正文原话"):
        _checked(wb="[[macro.gdp_nominal_yoy@2026H1]]")


def test_正文引文必须逐字():
    with pytest.raises(AutoDecideFailed, match="不可核验"):
        _checked(wb="「折现率大约取九个百分点」")


def test_账本里没有的占位符被驳回():
    with pytest.raises(AutoDecideFailed, match="不可核验"):
        _checked(tb="[[industry.cr5@2025FY]]")


def test_合法输入通过校验():
    _checked()          # 不抛异常即通过


def test_无上限时跳过上限检查():
    _checked(tg="0.055", wacc="0.09", ceiling=None)


# ── 端到端 ───────────────────────────────────────────────────

class _Client:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []
        self.total_cost_usd = 0.0

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _derived():
    from ir_agent.valuation.derive import derive
    return derive(_led(), "2025FY", AS_OF, years=5)


def test_一次通过():
    c = _Client(_out())
    d = decide(BODY, c, ledger=_led(), as_of=AS_OF, derived=_derived(), years=5)
    assert d.assumptions.wacc == D("0.09")
    assert d.assumptions.approved                 # 结构上算「已定」
    assert d.assumptions.reviewed_by == MODEL_MARK


def test_标注为模型决定而非人工审核():
    """冒充人工审核比没有审核更糟 —— 读者会据此以为有人看过。"""
    d = decide(BODY, _Client(_out()), ledger=_led(), as_of=AS_OF,
               derived=_derived(), years=5)
    assert "模型" in d.assumptions.reviewed_by
    assert d.by_model


def test_驳回后重试并把原因带回去():
    c = _Client(_out(tg="0.09", wacc="0.12"), _out())
    decide(BODY, c, ledger=_led(), as_of=AS_OF, derived=_derived(), years=5)
    assert len(c.prompts) == 2
    assert "上限" in c.prompts[1] and "被驳回" in c.prompts[1]


def test_连续失败则拒绝出估值而非退回默认值():
    """退回一个「差不多」的默认值，等于既无人工把关也无模型判断。"""
    c = _Client(*[_out(tg="0.09", wacc="0.12")] * 3)
    with pytest.raises(AutoDecideFailed, match="拒绝出具估值"):
        decide(BODY, c, ledger=_led(), as_of=AS_OF, derived=_derived(), years=5)


def test_提示词写明没有人会在之后纠正():
    c = _Client(_out())
    decide(BODY, c, ledger=_led(), as_of=AS_OF, derived=_derived(), years=5)
    p = c.prompts[0]
    assert "没有人会在你之后纠正" in p
    assert "0.0543" in p               # 上限写进提示词
    assert BODY in p                   # 正文给全，引文才可能逐字
