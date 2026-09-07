"""行业画像：集中度、生命周期、周期敏感度、比率分布。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.industry_profile import (
    Lifecycle,
    build,
    concentration,
    dol,
    gdp_elasticity,
    lifecycle,
    profile_facts,
)
from ir_agent.ledger import Fact, FactLedger, Method

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2024FY"


def _led(pairs, period=P):
    led = FactLedger()
    for k, v in pairs.items():
        led.put(Fact(key=k, value=D(str(v)), unit="元", period=period,
                     as_of=date(2025, 4, 1), source_id="t",
                     method=Method.REPORTED))
    return led


# ── 集中度：HHI 与 CR5 不可互相替代 ──────────────────────────

def test_HHI能区分CR5相同但格局不同的行业():
    """五家各 16% 与 一家 60%+四家各 5%，CR5 都是 80%，
    但竞争格局完全不同 —— CR5 看不出来，HHI 看得出来。"""
    even = concentration({f"c{i}": D(16) for i in range(5)}
                         | {"rest": D(20)})
    skew = concentration({"big": D(60)} | {f"c{i}": D(5) for i in range(4)}
                         | {"rest": D(20)})
    assert even.hhi < skew.hhi
    assert even.level != skew.level


def test_有效竞争者数量():
    """19 家公司但实际只有三四家在竞争 —— 这比「有 19 家」有用。"""
    c = concentration({"a": D(50), "b": D(30), "c": D(10),
                       "d": D(5), "e": D(5)})
    assert D("2.5") < c.effective_players < D("3.5")
    assert "有效竞争者" in c.describe()


def test_分档():
    assert concentration({f"c{i}": D(1) for i in range(20)}).level == "分散"
    assert concentration({"a": D(90), "b": D(10)}).level == "高度集中"


def test_营收为零或空返回None():
    assert concentration({}) is None
    assert concentration({"a": D(0)}) is None


# ── 生命周期：不能单看增速 ───────────────────────────────────

GDP = D("0.055")          # 名义 GDP 增速参照


def test_负增长且格局已整合判为衰退():
    """CFA decline: 负增长、产能过剩、竞争激烈。"""
    hist = {f"202{i}FY": D(100 - i * 10) for i in range(4)}
    v = lifecycle(hist, hhi=D("0.35"), gdp_cagr=GDP)
    assert v.stage is Lifecycle.DECLINE


def test_负增长但格局未整合判为洗牌期():
    """CFA shakeout: 增长放缓、竞争激烈、盈利下滑 —— 尚未整合完成。"""
    hist = {f"202{i}FY": D(100 - i * 10) for i in range(4)}
    v = lifecycle(hist, hhi=D("0.05"), gdp_cagr=GDP)
    assert v.stage is Lifecycle.SHAKEOUT
    assert "尚未整合" in v.reason


def test_增速快于名义GDP判为成长期():
    """CFA growth:「需求快速增长」。快慢的参照系是名义 GDP，
    不是自定的绝对阈值。"""
    hist = {f"202{i}FY": D(100) * D("1.3") ** i for i in range(4)}
    assert lifecycle(hist, hhi=D("0.1"),
                     gdp_cagr=GDP).stage is Lifecycle.GROWTH


def test_增速与名义GDP相当判为成熟期():
    hist = {f"202{i}FY": D(100) * (D(1) + GDP) ** i for i in range(4)}
    assert lifecycle(hist, hhi=D("0.3"), gdp_cagr=GDP).stage is Lifecycle.MATURE


def test_增长快但盈利下滑判为洗牌期():
    """CFA 用盈利趋势区分 growth（改善）与 shakeout（下滑）。"""
    hist = {f"202{i}FY": D(100) * D("1.3") ** i for i in range(4)}
    m = {f"202{i}FY": D("0.30") - D(i) / 100 for i in range(4)}
    v = lifecycle(hist, hhi=D("0.1"), gdp_cagr=GDP, margin_by_period=m)
    assert v.stage is Lifecycle.SHAKEOUT
    assert "盈利能力同时下滑" in v.reason


def test_无GDP参照时不判成长或成熟():
    """「快不快」必须有参照系 —— 没有就不该给出成长/成熟的判断。"""
    hist = {f"202{i}FY": D(100) * D("1.3") ** i for i in range(4)}
    v = lifecycle(hist, hhi=D("0.1"), gdp_cagr=None)
    assert v.stage is Lifecycle.UNKNOWN
    assert "缺名义 GDP 参照" in v.reason


def test_不足三期不判定():
    v = lifecycle({"2023FY": D(100), "2024FY": D(110)}, hhi=D("0.2"))
    assert v.stage is Lifecycle.UNKNOWN
    assert "不足三期" in v.reason


def test_多年CAGR与单年同比可能给出相反印象():
    """白酒实测: 5 年 CAGR +6.7% 而最近一年 -18.26%。"""
    hist = {"2021FY": D(100), "2022FY": D(115), "2023FY": D(132),
            "2024FY": D(152), "2025FY": D(124)}      # 末年大跌
    v = lifecycle(hist, hhi=D("0.26"), gdp_cagr=GDP)
    assert v.cagr > 0
    assert v.stage is not Lifecycle.DECLINE


# ── 周期敏感度 ───────────────────────────────────────────────

def test_DOL由固定成本与营业利润构成():
    led = _led({"operating_profit": 100, "admin_expense": 30,
                "rd_expense": 20, "depreciation_amortization": 50})
    assert dol(led, P, AS_OF) == D(2)               # 1 + 100/100


def test_销售费用不计入固定成本():
    """销售费用含大量随收入变动的渠道返利与运费。"""
    a = dol(_led({"operating_profit": 100, "admin_expense": 50}), P, AS_OF)
    b = dol(_led({"operating_profit": 100, "admin_expense": 50,
                  "selling_expense": 999}), P, AS_OF)
    assert a == b


def test_亏损公司不给DOL():
    assert dol(_led({"operating_profit": -10, "admin_expense": 5}),
               P, AS_OF) is None


def test_无固定成本分量返回None():
    assert dol(_led({"operating_profit": 100}), P, AS_OF) is None


def test_GDP弹性需要至少三个观测():
    ind = {"2023FY": D("0.10"), "2024FY": D("0.12")}
    gdp = {"2023FY": D("0.05"), "2024FY": D("0.06")}
    assert gdp_elasticity(ind, gdp) is None
    ind["2025FY"], gdp["2025FY"] = D("0.08"), D("0.04")
    assert gdp_elasticity(ind, gdp) == D(2)


def test_分量缺失时不给DTL():
    from ir_agent.industry_profile import CyclicalSensitivity
    assert CyclicalSensitivity(D("2"), None, D("1.5"), 10).total_leverage is None


def test_重资产行业的总杠杆高于轻资产():
    """实测: 钢铁 DOL 4.15 vs 白酒 1.77。"""
    from ir_agent.industry_profile import CyclicalSensitivity
    steel = CyclicalSensitivity(D("4.15"), D("1.6"), D("-1.42"), 17)
    baijiu = CyclicalSensitivity(D("1.77"), D("1.0"), D("1.83"), 19)
    assert steel.total_leverage > baijiu.total_leverage


# ── 比率分布 ─────────────────────────────────────────────────

def test_四分位距过大时标注中位数代表性有限():
    leds = {f"c{i}": _led({"roe": v}) for i, v in
            enumerate(["0.01", "0.02", "0.05", "0.30", "0.60", "0.90"])}
    prof = build("某行业", P, AS_OF, revenues={f"c{i}": D(10) for i in range(6)},
                 ledgers=leds, metrics=["roe"])
    assert prof.spreads["roe"].dispersed
    assert "代表性有限" in prof.summary()


def test_分布集中时不标注():
    leds = {f"c{i}": _led({"roe": v}) for i, v in
            enumerate(["0.18", "0.19", "0.20", "0.21", "0.22", "0.23"])}
    prof = build("某行业", P, AS_OF, revenues={f"c{i}": D(10) for i in range(6)},
                 ledgers=leds, metrics=["roe"])
    assert not prof.spreads["roe"].dispersed


def test_不足四家不算四分位():
    leds = {f"c{i}": _led({"roe": D("0.2")}) for i in range(3)}
    prof = build("某行业", P, AS_OF, revenues={f"c{i}": D(10) for i in range(3)},
                 ledgers=leds, metrics=["roe"])
    assert prof.spreads["roe"].q1 is None


# ── 入账本 ───────────────────────────────────────────────────

def test_画像结论写入账本供正文引用():
    leds = {f"c{i}": _led({"roe": D("0.2"), "operating_profit": D(100),
                           "admin_expense": D(50), "equity_multiplier": D("1.5")})
            for i in range(5)}
    hist = {f"202{i}FY": D(100) * D("1.1") ** i for i in range(4)}
    prof = build("某行业", P, AS_OF,
                 revenues={f"c{i}": D(20) for i in range(5)},
                 ledgers=leds, revenue_history=hist, cr5=D("0.8"),
                 metrics=["roe"])
    keys = {f.key for f in profile_facts(prof)}
    assert {"industry.hhi", "industry.effective_players",
            "industry.dol_median", "industry.roe_median"} <= keys
    assert all(f.method is Method.COMPUTED for f in profile_facts(prof))


def test_缺失项不入账本():
    prof = build("某行业", P, AS_OF, revenues={}, ledgers={})
    assert profile_facts(prof) == []


class Test提示词口径:
    def _prof(self):
        leds = {f"c{i}": _led({"roe": D("0.2"), "operating_profit": D(100),
                               "admin_expense": D(50),
                               "equity_multiplier": D("1.5")})
                for i in range(5)}
        hist = {f"202{i}FY": D(100) * D("1.1") ** i for i in range(4)}
        iy = {f"202{i}FY": D("0.1") for i in range(1, 4)}
        gy = {f"202{i}FY": D("0.05") for i in range(1, 4)}
        return build("甲-乙-丙", P, AS_OF,
                     revenues={f"c{i}": D(20) for i in range(5)},
                     ledgers=leds, revenue_history=hist, cr5=D("0.8"),
                     industry_yoy=iy, gdp_yoy=gy, gdp_cagr=D("0.055"),
                     metrics=["roe"])

    def test_数值写成占位符(self):
        from ir_agent.citation import audit_bare_numbers
        txt = self._prof().for_prompt()
        assert "[[industry.hhi@2024FY]]" in txt
        assert "[[industry.dol_median@2024FY]]" in txt
        assert audit_bare_numbers(txt) == []

    def test_定性判定直接写文字(self):
        """集中度分档与生命周期阶段不是数值，账本里没有对应事实，
        写成占位符反而解析不了。"""
        txt = self._prof().for_prompt()
        assert "成长期" in txt or "成熟期" in txt
        assert "高度集中" in txt or "中度集中" in txt or "分散" in txt

    def test_点明三者不合成(self):
        txt = self._prof().for_prompt()
        assert "不合成" in txt and "教材未给合成公式" in txt

    def test_分量不全时不给对应占位符(self):
        """账本里没有的键给了会解析失败。"""
        leds = {f"c{i}": _led({"roe": D("0.2")}) for i in range(5)}
        prof = build("甲-乙-丙", P, AS_OF,
                     revenues={f"c{i}": D(20) for i in range(5)},
                     ledgers=leds, metrics=["roe"])
        txt = prof.for_prompt()
        assert "dtl" not in txt and "income_elasticity" not in txt

    def test_分化标注进入提示词并要求说明(self):
        leds = {f"c{i}": _led({"roe": v}) for i, v in
                enumerate(["0.01", "0.02", "0.05", "0.30", "0.60", "0.90"])}
        prof = build("甲-乙-丙", P, AS_OF,
                     revenues={f"c{i}": D(10) for i in range(6)},
                     ledgers=leds, metrics=["roe"])
        txt = prof.for_prompt()
        assert "代表性有限" in txt and "必须说明" in txt


class Test方法论一致性:
    """逐条对照教材，防止「看起来合理」的偏离。"""

    def test_DOL分母必须加回利息(self):
        """中国准则把财务费用列在营业总成本内，营业利润是**扣息后**的。
        直接拿它当分母会把财务杠杆混进经营杠杆 —— 而周期敏感度另有
        独立的财务杠杆因子，等于算两遍。"""
        no_debt = _led({"operating_profit": 100, "admin_expense": 50})
        with_debt = _led({"operating_profit": 100, "admin_expense": 50,
                          "interest_expense_fe": 100})
        # 同样的固定成本与营业利润，有息公司的 EBIT 更高 → DOL 更低
        assert dol(with_debt, P, AS_OF) < dol(no_debt, P, AS_OF)
        assert dol(with_debt, P, AS_OF) == D(1) + D(50) / D(200)

    def test_利息收入为负不倒扣EBIT(self):
        """利息费用为负（利息收入大于支出）时不应把 EBIT 扣低。"""
        a = dol(_led({"operating_profit": 100, "admin_expense": 50}), P, AS_OF)
        b = dol(_led({"operating_profit": 100, "admin_expense": 50,
                      "interest_expense_fe": -30}), P, AS_OF)
        assert a == b

    def test_EBIT只有一份实现(self):
        """同一个概念不能两处两种算法 —— 此前 altman_z 加回了利息、
        dol 没加回。改为共享算子，用行为验证而非比对源码。"""
        from ir_agent.operators.aggregates import ebit
        led = _led({"operating_profit": 100, "interest_expense_fe": 40})
        assert ebit(led, P, AS_OF) == D(140)
        # DOL 的分母确实用的是它
        led2 = _led({"operating_profit": 100, "interest_expense_fe": 40,
                     "admin_expense": 70})
        assert dol(led2, P, AS_OF) == D(1) + D(70) / D(140)

    def test_负弹性标注为劣等品含义存疑(self):
        """负弹性在经济学上是「劣等品」，有明确含义，但对多数行业不成立。"""
        from ir_agent.industry_profile import CyclicalSensitivity
        s = CyclicalSensitivity(D("4.15"), D("1.5"), D("-1.42"), 17)
        assert s.elasticity_suspect
        assert "劣等品" in s.describe() and "样本过短" in s.describe()
        ok = CyclicalSensitivity(D("4.15"), D("1.5"), D("1.42"), 17)
        assert not ok.elasticity_suspect

    def test_不再合成单一分数(self):
        """Bodie 只并列三个因素、未给合成公式；检索也无标准做法。
        早前版本用几何平均自行合成并配 2.0/1.3 阈值，那是编的。"""
        from ir_agent.industry_profile import CyclicalSensitivity
        s = CyclicalSensitivity(D("2"), D("1.5"), D("1.5"), 10)
        assert not hasattr(s, "score")
        assert "启发式" not in s.describe()

    def test_收入弹性用标准分界(self):
        """需求收入弹性 >1 为周期品、0–1 为非周期品、<0 为劣等品。
        这是标准经济学分界，不是自定阈值。"""
        from ir_agent.industry_profile import (ELASTICITY_CYCLIC,
                                               CyclicalSensitivity)
        assert ELASTICITY_CYCLIC == D(1)
        assert "周期品" in CyclicalSensitivity(None, None, D("1.8"), 5).demand_type
        assert "非周期" in CyclicalSensitivity(None, None, D("0.6"), 5).demand_type
        assert "劣等品" in CyclicalSensitivity(None, None, D("-1"), 5).demand_type

    def test_DTL是标准构造(self):
        """DTL = DOL × DFL 是合并经营与财务杠杆的通行做法。"""
        from ir_agent.industry_profile import CyclicalSensitivity
        s = CyclicalSensitivity(D("2"), D("1.5"), D("1.2"), 10)
        assert s.total_leverage == D(3)
        assert CyclicalSensitivity(None, D("1.5"), None, 5).total_leverage is None

    def test_DFL用标准定义(self):
        """DFL = EBIT/(EBIT − 利息)。无息公司为 1，即不放大波动。"""
        from ir_agent.industry_profile import dfl
        assert dfl(_led({"operating_profit": 100}), P, AS_OF) == D(1)
        # EBIT=150, 利息=50, EBT=100 → 1.5
        assert dfl(_led({"operating_profit": 100,
                         "interest_expense_fe": 50}), P, AS_OF) == D("1.5")
        # 利息吞掉九成以上 EBIT —— 数值失真，不给
        assert dfl(_led({"operating_profit": 5,
                         "interest_expense_fe": 100}), P, AS_OF) is None

    def test_三因子对应课程原文(self):
        """Bodie: Sales(necessities/discretionary) / Operating leverage
        / Financial leverage。三项分列，不合成。"""
        from ir_agent.industry_profile import CyclicalSensitivity
        doc = CyclicalSensitivity.__doc__
        assert "necessities" in doc and "discretionary" in doc
        assert "不做三者的合成分数" in doc

    def test_HHI阈值对应司法部指南(self):
        """DOJ/FTC 横向合并指南: <1500 分散、1500–2500 中度、>2500 高度
        （0–10000 标度）。折算到 0–1 即 0.15 / 0.25。"""
        from ir_agent.industry_profile import (HHI_MODERATE,
                                               HHI_UNCONCENTRATED)
        assert HHI_UNCONCENTRATED == D("0.15") and HHI_MODERATE == D("0.25")
