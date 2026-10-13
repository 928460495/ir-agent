"""分解式公式化的计算引擎（spec 2026-10-14 子项目一）。"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from ir_agent.drivers import Decomposition, Factor, Op, compose
from ir_agent.formula import (Constant, FormulaError, FormulaInvalid,
                              FormulaModel, from_decomps, parse_formula,
                              parse_constant, short_from_label, simulate)


def _f(short, label=None, unit="变化率"):
    return Factor(key=f"f_{short}", label=label or short, unit=unit, short=short)


def _model(mode, factors, rev, gm, consts=()):
    declared = {f.short for f in factors} | {c.name for c in consts}
    m = FormulaModel(mode=mode, factors=list(factors), constants=list(consts),
                     formulas={"revenue": parse_formula(rev, "revenue", declared),
                               "gross_margin": parse_formula(gm, "gross_margin",
                                                             declared)})
    m.validate()
    return m


class TestParse:
    def test_全角与数学符号等价(self):
        f = parse_formula("上期（营收）×（1＋量）÷ 2 − 价", "revenue", {"量", "价"})
        assert f.names() == {"量", "价", "营收"}
        assert ("上期", "营收") in f.lagged()

    @pytest.mark.parametrize("rhs,frag", [
        ("max(量, 价)", "只允许 上期"),
        ("量 ** 2", "加减乘除以外"),
        ("量 if 价 else 1", "白名单之外"),
        ("上期(量, 价)", "只能带一个名字"),
        ("上期(1)", "只能带一个名字"),
        ("营收 * 量", "裸写了「营收」"),
        ("未声明 * 量", "未声明的名字「未声明」"),
        ("上期(未声明)", "未声明的名字「未声明」"),
        ("'a' + 量", "不是数字"),
        ("量 +", "解析不了"),
        ("", "空的"),
    ])
    def test_白名单外的写法被拒绝并指明位置(self, rhs, frag):
        with pytest.raises(FormulaInvalid, match=frag):
            parse_formula(rhs, "revenue", {"量", "价"})

    def test_数字按原文转Decimal不经float(self):
        f = parse_formula("量 * 0.035", "revenue", {"量"})
        assert ("num", "0.035") in f.tokens()

    def test_记号全括号(self):
        f = parse_formula("上期(营收) * (1 + 量) - 价", "revenue", {"量", "价"})
        assert f.tokens() == [
            ("lp", "("), ("lp", "("), ("prev", "营收"), ("op", "*"),
            ("lp", "("), ("num", "1"), ("op", "+"), ("name", "量"), ("rp", ")"),
            ("rp", ")"), ("op", "-"), ("name", "价"), ("rp", ")")]

    def test_常量取值(self):
        assert parse_constant("78%") == D("0.78")
        assert parse_constant("１,２３４.5") == D("1234.5")
        with pytest.raises(FormulaInvalid):
            parse_constant("约八成")


class TestValidate:
    def test_缺一条公式(self):
        m = FormulaModel(mode="变化", factors=[_f("量")], formulas={
            "revenue": parse_formula("上期(营收)*(1+量)", "revenue", {"量"})})
        with pytest.raises(FormulaInvalid, match="缺少 毛利率"):
            m.validate()

    def test_模式只能二选一(self):
        with pytest.raises(FormulaInvalid, match="模式"):
            FormulaModel(mode="混合", factors=[]).validate()

    @pytest.mark.parametrize("bad", ["基期", "营收", "1量", "量 价", "量(吨)"])
    def test_短名不合法(self, bad):
        m = _model("变化", [_f("量")], "上期(营收)*(1+量)", "基期(毛利率)+量/100")
        m.factors.append(_f(bad))
        with pytest.raises(FormulaInvalid, match="不合法"):
            m.validate()

    def test_名字重复(self):
        m = _model("变化", [_f("量")], "上期(营收)*(1+量)", "基期(毛利率)+量/100")
        m.constants.append(Constant("量", D(1), "x"))
        with pytest.raises(FormulaInvalid, match="重复"):
            m.validate()


class TestSimulate:
    def test_变化模式第0年取账本(self):
        m = _model("变化", [_f("量"), _f("价")],
                   "上期(营收) * (1+量) * (1+价)", "上期(毛利率) + 价 * 0")
        p = simulate(m, {"量": [D("0.1"), D("0.1")], "价": [D("0"), D("0.05")]},
                     base_revenue=D(100), base_margin=D("0.2"))
        assert p.revenue == [D("110.0"), D("127.05")]
        assert p.growth == [D("0.1"), D("0.155")]
        assert p.revenue0 == D(100)

    def test_水平模式第0年由基期因子代入公式_不用账本营收(self):
        """出货量×单价未必等于报表营收 —— 分子分母必须同口径。"""
        m = _model("水平", [_f("量", unit="万吨"), _f("价", unit="元"),
                           _f("本", unit="元")],
                   "量 * 价", "(价 - 本) / 价")
        p = simulate(m, {"量": [D(11)], "价": [D(10)], "本": [D(7)]},
                     base_factors={"量": D(10), "价": D(10), "本": D(8)},
                     base_revenue=D("999999"), base_margin=D("0.9"))
        assert p.revenue0 == D(100) and p.margin0 == D("0.2")
        assert p.growth == [D("0.1")] and p.margin == [D("0.3")]

    def test_上期与基期分别取上一年与第0年(self):
        m = _model("变化", [_f("增量", unit="pp")],
                   "上期(营收) * 1", "基期(毛利率) + 增量 / 100")
        p = simulate(m, {"增量": [D(-1), D(-2), D(-3)]},
                     base_revenue=D(1), base_margin=D("0.1415"))
        assert p.margin == [D("0.1315"), D("0.1215"), D("0.1115")]
        m2 = _model("变化", [_f("增量", unit="pp")],
                    "上期(营收) * 1", "上期(毛利率) + 增量 / 100")
        p2 = simulate(m2, {"增量": [D(-1), D(-1), D(-1)]},
                      base_revenue=D(1), base_margin=D("0.1415"))
        assert p2.margin == p.margin, "逐年 −1 与相对基期累计 −1/−2/−3 等价"

    def test_常量参与计算(self):
        m = _model("变化", [_f("稼动率变动", unit="pp")],
                   "上期(营收) * (1 + 稼动率变动 / 100 / 上期稼动率)",
                   "基期(毛利率)", [Constant("上期稼动率", D("0.78"), "年报")])
        p = simulate(m, {"稼动率变动": [D("-3")]},
                     base_revenue=D(1), base_margin=D("0.2"))
        assert abs(p.growth[0] - D(-3) / 100 / D("0.78")) < D("1e-26")

    def test_上期因子在第1年取基期因子值(self):
        m = _model("变化", [_f("价", unit="元")],
                   "上期(营收) * 价 / 上期(价)", "基期(毛利率)")
        p = simulate(m, {"价": [D(11), D("12.1")]}, base_factors={"价": D(10)},
                     base_revenue=D(100), base_margin=D("0.2"))
        assert p.growth == [D("0.1"), D("0.1")]

    def test_除以零与缺取值明确报错(self):
        m = _model("水平", [_f("量"), _f("价")], "量 * 价", "(价 - 量) / 价")
        with pytest.raises(FormulaError, match="第 1 年毛利率.*除以零"):
            simulate(m, {"量": [D(1)], "价": [D(0)]},
                     base_factors={"量": D(1), "价": D(1)})
        with pytest.raises(FormulaError, match="缺「价」第 2 年"):
            simulate(m, {"量": [D(1), D(1)], "价": [D(1)]},
                     base_factors={"量": D(1), "价": D(1)})
        with pytest.raises(FormulaError, match="基期\\(价\\)|取不到|缺「价」"):
            simulate(m, {"量": [D(1)], "价": [D(1)]}, base_factors={"量": D(1)})

    def test_变化模式缺账本基期报错(self):
        m = _model("变化", [_f("量")], "上期(营收)*(1+量)", "基期(毛利率)")
        with pytest.raises(FormulaError, match="变化模式需要第 0 年"):
            simulate(m, {"量": [D(0)]})

    def test_需要基期值的因子(self):
        lv = _model("水平", [_f("量"), _f("价"), _f("闲置")], "量 * 价", "价 / 价")
        assert [f.short for f in lv.needs_base()] == ["量", "价"]
        ch = _model("变化", [_f("量"), _f("价")],
                    "上期(营收) * 价 / 上期(价)", "基期(毛利率) + 量")
        assert [f.short for f in ch.needs_base()] == ["价"]


class TestLegacy:
    """**旧格式翻译后逐位相同**（spec 五）。"""

    REV = Decomposition("revenue", Op.MULTIPLY, [
        Factor("capacity", "名义产能（万吨）", "变化率"),
        Factor("asp", "平均单价", "变化率")])
    GM = Decomposition("gross_margin", Op.ADD_PP, [
        Factor("cu", "铜箔采购价", "pp"), Factor("mix", "高端占比提升", "pp")])
    YEARS = {"capacity": ["0.035", "0.12", "-0.04"],
             "asp": ["0.02", "-0.015", "0.0725"],
             "cu": ["-1.2", "-2.5", "0.3"], "mix": ["0.8", "1.6", "2.4"]}

    def _vals(self, m):
        return {f.short: [D(x) for x in self.YEARS[f.key]] for f in m.factors}

    def test_短名生成(self):
        taken: set[str] = set()
        assert short_from_label("名义产能（万吨）", taken) == "名义产能万吨"
        assert short_from_label("名义产能(万吨)", taken) == "名义产能万吨2"
        assert short_from_label("3D封装占比", taken) == "f3D封装占比"

    def test_翻译后的公式文本(self):
        m = from_decomps([self.REV, self.GM])
        assert m.legacy and m.mode == "变化"
        assert m.describe("revenue") == "营收 = 上期(营收)*(1+名义产能万吨)*(1+平均单价)"
        assert m.describe("gross_margin") == \
            "毛利率 = 基期(毛利率)+(铜箔采购价+高端占比提升)/100"

    def test_与旧引擎逐位相同(self):
        m = from_decomps([self.REV, self.GM])
        base = D("0.2647")
        p = simulate(m, self._vals(m), base_revenue=D("20345678901.23"),
                     base_margin=base)
        for t in range(3):
            vals = {k: D(v[t]) for k, v in self.YEARS.items()}
            assert p.growth[t] == compose(self.REV, vals)
            assert p.margin[t] == compose(self.GM, vals, base=base)

    def test_锚定平移与旧margin_bases逐位相同(self):
        """旧：第 2 年起基期 = 锚定值 − 第 1 年累计 pp/100。"""
        m = from_decomps([self.REV, self.GM])
        base, a1 = D("0.2647"), D("0.306912")
        p = simulate(m, self._vals(m), base_revenue=D(1), base_margin=base,
                     anchor_y1=a1)
        cum1 = D(self.YEARS["cu"][0]) + D(self.YEARS["mix"][0])
        shifted = a1 - cum1 / 100
        vals1 = {k: D(v[0]) for k, v in self.YEARS.items()}
        assert p.margin[0] == compose(self.GM, vals1, base=base), "第 1 年不替换"
        for t in (1, 2):
            vals = {k: D(v[t]) for k, v in self.YEARS.items()}
            assert p.margin[t] == compose(self.GM, vals, base=shifted)

    def test_上期毛利率公式在锚定后自然承接(self):
        m = _model("变化", [_f("增量", unit="pp")], "上期(营收)*1",
                   "上期(毛利率) + 增量 / 100")
        p = simulate(m, {"增量": [D(0), D(1)]}, base_revenue=D(1),
                     base_margin=D("0.2"), anchor_y1=D("0.3"))
        assert p.margin == [D("0.2"), D("0.31")]
