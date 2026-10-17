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


def test_周期定位口径():
    from ir_agent.cycle import CycleView, Direction, Phase, Signal, cycle_facts

    view = CycleView(
        signals=[Signal("活动", D("49.2"), Direction.DOWN,
                        "制造业 PMI 49.2（收缩区）",
                        "制造业 PMI [[macro.pmi_manufacturing@2026-07]]（收缩区）")],
        scores={Phase.LATE: D("0.88"), Phase.RECESSION: D("0.88"),
                Phase.MID: D("0.5"), Phase.EARLY: D("0.2")},
        as_of=AS_OF)
    led = FactLedger()
    for f in cycle_facts(view, "2026-07"):
        led.put(f)
    _fact(led, "macro.pmi_manufacturing", "49.2", period="2026-07", unit="index")
    # 判据必须让模型看到读数，但读数要写成占位符 —— 账本里有 PMI，
    # 没有理由给一个不可引用的字面值。
    assert_prompt_safe(view.for_prompt(), led)
    txt = view.for_prompt()
    assert "阶段交界" in txt and "未覆盖" in txt
    assert "[[macro.pmi_manufacturing@2026-07]]" in txt


# ── 第六至八次的候选: 外部资料带进来的三段新提示词文本 ──────────

def test_一致预期段只有占位符():
    """卖方 EPS 预测不在账本里就无法引用。此前八次踩的都是这一件事:
    我往提示词里放了模型用不了的东西。"""
    from ir_agent import corpus as C
    from ir_agent.sources import reports as R
    from ir_agent.sources.reports import consensus_facts

    c = R.Consensus(as_of=AS_OF, window_days=180, n_reports=42,
                    eps={2026: R.Estimate(2026, 42, D("68.43"), D("65.77"),
                                          D("77.75"))},
                    drift={2026: (R.Estimate(2026, 56, D("84.8"), D("80"),
                                             D("90")),
                                  R.Estimate(2026, 42, D("68.43"), D("65.77"),
                                             D("77.75")))})
    led = FactLedger()
    for f in consensus_facts(c):
        led.put(f)
    assert_prompt_safe(C.consensus_prompt(c), led)


def test_无一致预期时的说明也安全():
    from ir_agent import corpus as C
    assert_prompt_safe(C.consensus_prompt(None), FactLedger())


def test_战略摘要安全():
    from ir_agent import strategy
    from ir_agent.strategy import Force

    q = "公司拥有品质品牌工艺环境文化组成的五大核心竞争力"
    out = "\n".join(
        [f"护城河: 无形资产 | 宽 | 「{q}」"]
        + [f"五力: {f.value} | 中 | 中性 | 「{q}」" for f in Force]
        + [f"不确定性: 渠道去库存 | revenue_growth | 下调 | 「{q}」",
           f"不确定性: 提价能力减弱 | gross_margin | 下调 | 「{q}」"])
    v = strategy.parse(out)
    v.unquantified = [Force.SUBSTITUTES]
    assert_prompt_safe(v.summary(), FactLedger())


def test_产业链摘要与矛盾提示安全():
    """矛盾提示里必须用占位符指向分位数，不能写读数。"""
    from ir_agent import value_chain as VC

    led = FactLedger()
    _fact(led, "payable_days.percentile", "0.17")
    q = "上游由数万家农户分散种植"
    v = VC.parse("\n".join([f"环节: {r.value} | n | note | 「{q}」"
                            for r in VC.Role]
                           + ["议价: 上游 | 强", "议价: 下游 | 强"]))
    v.conflicts = VC.cross_check(v, led, P, AS_OF)
    assert v.conflicts, "用例本身要触发矛盾，否则这条测试是空的"
    assert_prompt_safe(v.summary(), led)


def test_产业链缺规模时的说明安全():
    from ir_agent import value_chain as VC

    q = "上游由数万家农户分散种植"
    v = VC.parse("\n".join([f"环节: {r.value} | n | note | 「{q}」"
                            for r in VC.Role]))
    assert_prompt_safe(v.summary(), FactLedger())


def test_宏观定性摘要安全():
    """含交叉印证的矛盾提示 —— 里面绝不能出现利差读数。"""
    from ir_agent import macro_view as MV

    q = "央行下调政策利率，货币环境延续宽松"
    v = MV.parse("\n".join([
        f"政策取向: 宽松 | 「{q}」",
        f"传导: 政策利率下调 | 收入预期改善→场景恢复 | 利好 | 「{q}」",
        f"传导: 流动性宽松 | 折现率下行→估值中枢抬升 | 利好 | 「{q}」"]))
    led = FactLedger()
    for p_, d in (("2026-09-01", date(2026, 9, 1)),
                  ("2026-09-05", date(2026, 9, 5))):
        for k, val in (("rate.cgb_10y", "0.020"), ("rate.cgb_2y", "0.026")):
            led.put(Fact(key=k, value=D(val), unit="ratio", period=p_,
                         as_of=d, source_id="t", method=Method.REPORTED))
    v.conflicts = MV.cross_check(v, led, AS_OF)
    assert v.conflicts, "用例本身要触发矛盾"
    assert_prompt_safe(v.summary(), led)


# ── 第九次: 模型写进结构化产出的描述字段 ─────────────────────

def test_月份指代不算裸数字():
    """跑长电时实测: 宏观定性写「8月制造业PMI回升」「9月加息预期升温」，
    两个月份都被判成裸数字。月份是日历指代不是财务数值 ——
    与已在白名单里的「第一季度」同一条道理。"""
    from ir_agent.citation import audit_bare_numbers
    assert audit_bare_numbers("8月制造业PMI回升，9月加息预期升温") == []
    assert audit_bare_numbers("12月订单回补") == []


def test_描述字段里的数字被拦在模块自己的重试循环里():
    """**这是第九次同类错误，但性质与前八次不同。** 前八次是我往提示词里
    放了模型用不了的东西；这次是模型写进结构化产出的描述字段，那些字段
    随后原样进了分析师提示词。实测长电: 传导路径写着「当前处于91%历史
    估值分位的A股半导体板块……」，91% 在账本里没有对应事实。

    危害更隐蔽: 分析师抄进正文 → 被裸数字审计驳回 → 而它**无从修复**，
    没有占位符可以替代。整个重试循环变成不可满足的约束。

    拦在模块自己的循环里，模型改得动 —— 换个不带数字的说法即可。"""
    from ir_agent.citation import audit_prose
    bad = audit_prose({"传导": "当前处于91%历史估值分位的半导体板块承压"})
    assert bad and "91%" in bad[0]
    assert audit_prose({"传导": "当前估值分位偏高的半导体板块承压"}) == []


@pytest.mark.parametrize("mod,exc,mk", [
    ("macro_view", "MacroViewFailed", "macro"),
    ("strategy", "StrategyFailed", "strategy"),
    ("value_chain", "ChainFailed", "chain"),
])
def test_三个定性模块都装了这道闸(mod, exc, mk):
    import importlib
    import inspect
    m = importlib.import_module(f"ir_agent.{mod}")
    # 子项目四起账本外数字只作事实（`facts()`），不再驳回。
    src = inspect.getsource(m.facts)
    assert "audit_prose" in src, f"{mod} 没有核对描述字段里的账本外数字"
    assert "不要写具体数字" in m.PROMPT, f"{mod} 的提示词没有讲清这条"


def test_占位符后重复单位被拦下():
    """**跑长电时实测，几乎每处分位数都中招。** format_value 渲染出
    「17 分位」「57.02 天」「4 家」，而中文自然写法是「处于同业 X 分位」，
    模型于是又补了一遍单位，正文出现「17 分位分位」「4 家家」「66.25 天天」。

    根因是规则里从没说过「占位符渲染后自带单位」。"""
    from ir_agent.citation import audit_unit_duplication

    led = FactLedger()
    _fact(led, "gross_margin.percentile", "17", unit="分位")
    _fact(led, "industry.effective_players", "4", unit="家")
    bad = audit_unit_duplication(
        "处于同业[[gross_margin.percentile@2024FY]]分位，"
        "有效竞争者[[industry.effective_players@2024FY]]家", led, AS_OF)
    assert len(bad) == 2
    ok = audit_unit_duplication(
        "处于同业[[gross_margin.percentile@2024FY]]，"
        "有效竞争者[[industry.effective_players@2024FY]]", led, AS_OF)
    assert ok == []


def test_撰写闸门接上了单位重复检查():
    from ir_agent.analyst import FactCatalog, _violations

    led = FactLedger()
    _fact(led, "gross_margin.percentile", "17", unit="分位")
    cat = FactCatalog(ledger=led, as_of=AS_OF)
    v = _violations("处于同业[[gross_margin.percentile@2024FY]]分位。", cat)
    assert any("重复写了单位" in x for x in v)


def test_规则里讲清了占位符自带单位():
    from ir_agent.analyst import _RULES
    assert "占位符渲染后自带单位" in _RULES and "17 分位分位" in _RULES
