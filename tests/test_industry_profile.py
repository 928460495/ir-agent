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

def test_高集中度加负增长判为衰退():
    """格局已定而总量在退 —— 需求侧退坡，不是份额洗牌。"""
    hist = {f"202{i}FY": D(100 - i * 10) for i in range(4)}
    v = lifecycle(hist, hhi=D("0.35"))
    assert v.stage is Lifecycle.DECLINE
    assert "需求侧退坡" in v.reason


def test_低集中度加负增长只判成熟末期():
    """还在洗牌，可能只是周期底部 —— 估值含义与衰退完全不同。"""
    hist = {f"202{i}FY": D(100 - i * 10) for i in range(4)}
    v = lifecycle(hist, hhi=D("0.05"))
    assert v.stage is Lifecycle.LATE_MATURE
    assert "周期底部" in v.reason


def test_高速增长判为成长期():
    hist = {f"202{i}FY": D(100) * D("1.3") ** i for i in range(4)}
    assert lifecycle(hist, hhi=D("0.1")).stage is Lifecycle.GROWTH


def test_不足三期不判定():
    v = lifecycle({"2023FY": D(100), "2024FY": D(110)}, hhi=D("0.2"))
    assert v.stage is Lifecycle.UNKNOWN
    assert "不足三期" in v.reason


def test_五年CAGR与单年同比可能给出相反印象():
    """白酒实测: 5 年 CAGR +6.7% 而最近一年 -18.26% ——
    成熟行业的周期性下行，不是结构性衰退。单看一年会判错。"""
    hist = {"2021FY": D(100), "2022FY": D(115), "2023FY": D(132),
            "2024FY": D(152), "2025FY": D(124)}      # 末年大跌
    v = lifecycle(hist, hhi=D("0.26"))
    assert v.cagr > 0
    assert v.stage in (Lifecycle.MATURE, Lifecycle.GROWTH)


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


def test_分量不全时不合成敏感度分数():
    """三缺一的合成分会误导。"""
    from ir_agent.industry_profile import CyclicalSensitivity
    s = CyclicalSensitivity(D("2"), D("1.5"), None, 10)
    assert s.score is None
    assert "分量不全" in s.describe()
    full = CyclicalSensitivity(D("2"), D("1.5"), D("1.5"), 10)
    assert full.score is not None and "周期敏感度" in full.describe()


def test_重资产行业的敏感度高于轻资产():
    """实测: 钢铁 DOL 9.51 / 敏感度 3.15（高）vs 白酒 1.66 / 1.67（中）。"""
    from ir_agent.industry_profile import CyclicalSensitivity
    steel = CyclicalSensitivity(D("9.51"), D("2.31"), D("-1.42"), 17)
    baijiu = CyclicalSensitivity(D("1.66"), D("1.54"), D("1.83"), 19)
    assert steel.score > baijiu.score
    assert "高" in steel.describe() and "中" in baijiu.describe()


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
            "industry.revenue_cagr", "industry.dol_median",
            "industry.leverage_median", "industry.roe_median"} <= keys
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
                     industry_yoy=iy, gdp_yoy=gy, metrics=["roe"])

    def test_数值写成占位符(self):
        from ir_agent.citation import audit_bare_numbers
        txt = self._prof().for_prompt()
        assert "[[industry.hhi@2024FY]]" in txt
        assert "[[industry.cyclical_score@2024FY]]" in txt
        assert audit_bare_numbers(txt) == []

    def test_定性判定直接写文字(self):
        """集中度分档与生命周期阶段不是数值，账本里没有对应事实，
        写成占位符反而解析不了。"""
        txt = self._prof().for_prompt()
        assert "成长期" in txt or "成熟期" in txt
        assert "高度集中" in txt or "中度集中" in txt or "分散" in txt

    def test_点明敏感度决定传导强度(self):
        assert "传导强度" in self._prof().for_prompt()

    def test_分量不全时不给敏感度占位符(self):
        """score 为 None 时账本里没有 cyclical_score，给了会解析失败。"""
        leds = {f"c{i}": _led({"roe": D("0.2")}) for i in range(5)}
        prof = build("甲-乙-丙", P, AS_OF,
                     revenues={f"c{i}": D(20) for i in range(5)},
                     ledgers=leds, metrics=["roe"])
        assert "cyclical_score" not in prof.for_prompt()

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

    def test_负弹性被标注而非悄悄取绝对值(self):
        """「GDP 上行时行业收入下行」多半是样本噪声，不是发现。"""
        from ir_agent.industry_profile import CyclicalSensitivity
        s = CyclicalSensitivity(D("9.51"), D("2.31"), D("-1.42"), 17)
        assert s.elasticity_suspect
        assert "GDP 弹性为负" in s.describe() and "可靠性存疑" in s.describe()
        ok = CyclicalSensitivity(D("9.51"), D("2.31"), D("1.42"), 17)
        assert not ok.elasticity_suspect and "存疑" not in ok.describe()

    def test_合成分数标注为启发式(self):
        """课程没给合成公式，阈值也是自定的 —— 不能显得像标准做法。"""
        from ir_agent.industry_profile import CyclicalSensitivity
        s = CyclicalSensitivity(D("2"), D("1.5"), D("1.5"), 10)
        assert "启发式合成" in s.describe()

    def test_三因子对应课程原文(self):
        """Bodie Ch.17: Sales(necessities/discretionary) / Operating leverage
        / Financial leverage。前两项一一对应，第一项用弹性作代理并声明。"""
        from ir_agent.industry_profile import CyclicalSensitivity
        doc = CyclicalSensitivity.__doc__
        assert "necessities" in doc and "discretionary" in doc
        assert "操作化代理" in doc and "不是课程给定的度量" in doc

    def test_HHI阈值对应司法部指南(self):
        """DOJ/FTC 横向合并指南: <1500 分散、1500–2500 中度、>2500 高度
        （0–10000 标度）。折算到 0–1 即 0.15 / 0.25。"""
        from ir_agent.industry_profile import (HHI_MODERATE,
                                               HHI_UNCONCENTRATED)
        assert HHI_UNCONCENTRATED == D("0.15") and HHI_MODERATE == D("0.25")
