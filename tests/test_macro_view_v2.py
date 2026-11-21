"""宏观传导专业化（spec 2026-11-21-staged-collection §5.1）：九段传导、跟踪与排除、敏感度段、公司暴露事实。不联网。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import macro_view as MV
from ir_agent.ledger import Fact, FactLedger, Method

D = Decimal
AS_OF = date(2026, 11, 20)

NINE = ("传导: 利率 | 按揭利率下调 | 月供下降 → 成交回升 → 装修需求释放 | 需求量 | 利好 | "
        "中：收入弹性大于一 | 中长期 | 延续 | 转述：综合 某机构《某报告》")
FX = ("传导: 汇率 | 本币升值 | 外币报价折算收入减少 → 毛利率受压 | 价格、单位成本 | 利空 | "
      "强：境外收入占比高 | 即期 | 新增 | 转述：综合 某机构《某报告》")


def test_parse_nine_fields():
    v = MV.parse("政策取向: 中性 | 转述：综合 某机构《某报告》\n" + NINE)
    c = v.channels[0]
    assert (c.channel, c.driver, c.target, c.impact, c.lag, c.novelty) == (
        "利率", "按揭利率下调", "需求量", MV.Impact.POSITIVE, "中长期", "延续")
    assert c.path.startswith("月供下降") and c.sensitivity.startswith("中")
    bad = MV.parse(NINE.replace("传导: 利率", "传导: 汇市"))
    assert bad.channels == [] and any("渠道" in n for n in bad.read_notes)


def test_parse_legacy_four_fields():
    v = MV.parse("传导: 宽松 | 收入预期改善 → 需求回升 | 利好 | 转述：综合 某机构《某报告》")
    c = v.channels[0]
    assert c.driver == "宽松" and c.impact is MV.Impact.POSITIVE and c.channel == "" and c.lag == ""


def test_parse_track_and_exclude_lines():
    v = MV.parse(NINE + "\n跟踪: 商品房销售面积 | 按揭利率下调 | 成交回升而竣工持续下滑"
                 "\n排除: 外需与贸易 | 本行业几乎没有出口")
    assert [(t.indicator, t.driver, t.falsifier) for t in v.tracks] == [
        ("商品房销售面积", "按揭利率下调", "成交回升而竣工持续下滑")]
    assert v.excluded == [("外需与贸易", "本行业几乎没有出口")]


def test_facts_require_two_channels_and_tracks():
    led = FactLedger()
    one = MV.parse("政策取向: 中性 | 转述：综合 某机构《某报告》\n" + NINE + "\n"
                   + NINE.replace("按揭利率下调", "存款利率下调"))
    fs = MV.facts(one, led, AS_OF)
    assert any("只涉及 1 个渠道" in f for f in fs)
    assert any("按揭利率下调" in f and "跟踪" in f for f in fs)
    two = MV.parse("政策取向: 中性 | 转述：综合 某机构《某报告》\n" + NINE + "\n" + FX
                   + "\n跟踪: 商品房销售面积 | 按揭利率下调 | 竣工下滑\n跟踪: 出口报价 | 本币升值 | 报价回升")
    fs2 = MV.facts(two, led, AS_OF)
    assert not any("渠道" in f and "只涉及" in f for f in fs2) and not any("没有对应的跟踪" in f for f in fs2)


def test_facts_cap_tracks():
    text = NINE + "\n" + FX + "".join(f"\n跟踪: 指标{i} | 按揭利率下调 | 证伪{i}" for i in range(6))
    assert any("跟踪指标" in f and "超过" in f for f in MV.facts(MV.parse(text), FactLedger(), AS_OF))


def _f(key, v, period="2025FY", unit="元"):
    return Fact(key=key, value=D(str(v)), unit=unit, period=period, as_of=AS_OF, source_id="x",
                method=Method.REPORTED)


def test_sensitivity_block_placeholders():
    led = FactLedger()
    for k in ("industry.dol_median", "industry.income_elasticity", "company.net_debt_ratio"):
        led.put(_f(k, "1.5", unit="x"))
    txt = MV.sensitivity_block(led, "2025FY", AS_OF)
    assert "[[industry.dol_median@2025FY]]" in txt and "[[company.net_debt_ratio@2025FY]]" in txt
    assert "财务杠杆 DFL：本次未取到" in txt and "经营杠杆 DOL（本公司）：本次未取到" in txt


def test_company_net_debt_ratio():
    from ir_agent.industry_profile import company_exposure_facts

    led = FactLedger()
    for k, v in (("short_loan", 10), ("long_loan", 20), ("cash_and_equivalents", 130),
                 ("equity_attr_parent", 400)):
        led.put(_f(k, v))
    got = {f.key: f.value for f in company_exposure_facts(led, "2025FY", AS_OF)}
    assert got["company.net_debt_ratio"] == D("-0.25")           # (30 − 130) ÷ 400，净现金为负


def test_prompt_examples_neutral():
    for b in ("白酒", "封测", "覆铜板", "批价"):
        assert b not in MV.PROMPT
    ex = [l for l in MV.PROMPT.splitlines() if l.startswith("传导: ")]
    assert len({l.split("|")[0] for l in ex}) >= 4
    assert "{sensitivity}" in MV.PROMPT and "跟踪:" in MV.PROMPT and "排除:" in MV.PROMPT


# ── 终审修复 ────────────────────────────────────────────────

def test_证伪读数里的数字交审核者():
    """终审 2：证伪读数会进 summary → 撰写提示词，裸数字要被审计到。"""
    v = MV.parse(NINE + "\n" + FX + "\n跟踪: 新订单指数 | 按揭利率下调 | 跌破50"
                 "\n跟踪: 出口报价 | 本币升值 | 回升")
    assert any("账本外的数字" in f and "50" in f for f in MV.facts(v, FactLedger(), AS_OF))


def test_九段缺一段不当成旧写法():
    """终审 3：模型漏写一段（八段）时，不能落进旧四段解析把渠道当事件。"""
    eight = NINE.replace(" | 延续 |", " |")
    v = MV.parse(eight)
    assert v.channels == []
    assert any("九段" in n and "缺" in n for n in v.read_notes)
    assert not any("不是 利好/中性/利空" in n for n in v.read_notes)
