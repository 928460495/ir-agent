"""提示词契约的统一守卫。

**这个文件因为同一类错误出现五次而存在。**

五次分别是:
  1. 摘要里的裸数字（可比口径「覆盖 7/7」、同业市值「2,794 亿」）
  2. 标签里的裸数字（「沪深300」「10 年期国债」）
  3. 解析不了的占位符（[[pe_ttm@2025FY]] —— 即期指标挂在报价日上）
  4. 生命周期判据里的读数（「3 年营收 CAGR 10.0%」）
  5. 货币层次名称（「M1−M2 剪刀差」里的 1 和 2）

每一次都是**我往提示词里放了模型用不了的东西**，而不是模型不听话。
后果不只是多一轮驳回: 清单里只要有一个模型无从修复的项，整个重试循环
就变成不可满足的约束，连本来能改的那几个也一起卡死 —— 实测连挂三次、
白烧 $0.99。

因此这里不再逐个模块补测试，而是把规则本身立成断言:

  **凡是要进提示词的文本，(a) 不得含账本外的裸数字，
  (b) 其中的占位符必须都能解析。**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.citation import REF_RE, audit_bare_numbers, resolves
from ir_agent.ledger import Fact, FactLedger, Method

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2024FY"


def _fact(led, key, value="1", period=P, unit="ratio"):
    led.put(Fact(key=key, value=D(str(value)), unit=unit, period=period,
                 as_of=date(2025, 1, 1), source_id="t",
                 method=Method.COMPUTED))


def assert_prompt_safe(text: str, ledger: FactLedger | None = None) -> None:
    """提示词文本的两条硬性要求。"""
    bare = audit_bare_numbers(text)
    assert not bare, f"提示词含账本外的裸数字：{bare}\n---\n{text}"
    if ledger is not None:
        refs = [m.group(0) for m in REF_RE.finditer(text)]
        bad = [r for r in refs if not resolves(r, ledger, AS_OF)]
        assert not bad, f"提示词含解析不了的占位符：{bad}\n---\n{text}"


# ── 宏观 ─────────────────────────────────────────────────────

def test_宏观口径():
    from ir_agent.sources.macro import MacroSnapshot

    led = FactLedger()
    facts = []
    for key, per, unit in (
            ("macro.pmi_manufacturing", "2026-07", "index"),
            ("macro.pmi_services", "2026-07", "index"),
            ("macro.m1_yoy", "2026-07", "ratio"),
            ("macro.m2_yoy", "2026-07", "ratio"),
            ("macro.m1_m2_gap", "2026-07", "ratio"),
            ("macro.gdp_nominal_yoy", "2026H1", "ratio"),
            ("macro.gdp_real_yoy", "2026H1", "ratio"),
            ("macro.cpi_yoy", "2026-07", "ratio"),
            ("macro.ppi_yoy", "2026-07", "ratio")):
        f = Fact(key=key, value=D("49.2") if unit == "index" else D("0.05"),
                 unit=unit, period=per, as_of=date(2026, 1, 1),
                 source_id="t", method=Method.REPORTED)
        facts.append(f)
        led.put(f)
    assert_prompt_safe(MacroSnapshot(facts, AS_OF).for_prompt(), led)


# ── 行业 ─────────────────────────────────────────────────────

def test_行业聚合口径():
    from ir_agent.industry_facts import IndustryAggregate, industry_facts

    agg = IndustryAggregate(
        target="600519", target_name="标的", industry="甲-乙-丙",
        period=P, prior_period="2023FY", as_of=date(2025, 4, 1),
        revenues={"600519": D(100), "a": D(50), "b": D(30), "c": D(20)},
        prior={"600519": D(80), "a": D(50), "b": D(30), "c": D(20)})
    led = FactLedger()
    for f in industry_facts(agg):
        led.put(f)
    assert_prompt_safe(agg.for_prompt(), led)


def test_行业画像口径():
    from ir_agent.industry_profile import build, profile_facts

    leds = {}
    for i in range(5):
        L = FactLedger()
        for k, v, u in (("roe", "0.2", "ratio"),
                        ("operating_profit", "100", "元"),
                        ("admin_expense", "50", "元"),
                        ("equity_multiplier", "1.5", "x")):
            _fact(L, k, v, unit=u)
        leds[f"c{i}"] = L
    prof = build("甲-乙-丙", P, AS_OF,
                 revenues={f"c{i}": D(20) for i in range(5)}, ledgers=leds,
                 revenue_history={f"202{i}FY": D(100) * D("1.1") ** i
                                  for i in range(4)},
                 cr5=D("0.8"),
                 industry_yoy={f"202{i}FY": D("0.1") for i in range(1, 4)},
                 gdp_yoy={f"202{i}FY": D("0.05") for i in range(1, 4)},
                 metrics=["roe"])
    led = FactLedger()
    for f in profile_facts(prof):
        led.put(f)
    assert_prompt_safe(prof.for_prompt(), led)


# ── 可比 ─────────────────────────────────────────────────────

def test_可比口径():
    from ir_agent.comps import CompsRow, build_comps, comps_facts

    rows = [CompsRow(code=f"00000{i}", name=f"c{i}", period=P,
                     metrics={"roe": D("0.1") + D(i) / 100,
                              "current_ratio": D(1) + D(i) / 10,
                              "receivable_days": D(30) + D(i)})
            for i in range(4)]
    table = build_comps("000000", rows, P, AS_OF)
    led = FactLedger()
    for k in ("roe", "current_ratio", "receivable_days"):
        _fact(led, k)
    comps_facts(table, led)
    assert_prompt_safe(table.for_prompt(ledger=led, as_of=AS_OF), led)


def test_可比公司选择口径():
    from ir_agent.comparables import Selection, Sized

    sel = Selection(target="600519",
                    chosen=[Sized("000858", "五粮液", D("2794e8")),
                            Sized("600809", "山西汾酒", D("1495e8"))],
                    universe=18, dropped=[], target_cap=D("16626e8"))
    assert_prompt_safe(sel.for_prompt())


# ── 标签 ─────────────────────────────────────────────────────

def test_全部事实标签():
    """标签原样进提示词。标签里写「10 年期国债」，模型照抄就被驳回。"""
    from ir_agent.analyst import LABELS

    bad = {k: v for k, v in LABELS.items() if audit_bare_numbers(v)}
    assert not bad, f"这些标签含裸数字：{bad}"


def test_指标登记表的标签与理由():
    from ir_agent.metrics import REGISTRY

    for m in REGISTRY:
        assert not audit_bare_numbers(m.label), f"{m.key} 标签含裸数字"
        assert not audit_bare_numbers(m.why_not), f"{m.key} 不适用理由含裸数字"


# ── 规则本身 ─────────────────────────────────────────────────

def test_守卫能抓到裸数字():
    with pytest.raises(AssertionError, match="裸数字"):
        assert_prompt_safe("行业增速 -18.26%")


def test_守卫能抓到解析不了的占位符():
    led = FactLedger()
    _fact(led, "roe")
    with pytest.raises(AssertionError, match="解析不了"):
        assert_prompt_safe("PE 为 [[pe_ttm@2025FY]]", led)
