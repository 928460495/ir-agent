from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.operators import dcf, dupont, growth, margins, multiples

D = Decimal


def led_with(period_values: dict[str, dict[str, str]], as_of=date(2025, 10, 25)):
    """period_values: {period: {key: value}}"""
    led = FactLedger()
    for period, kv in period_values.items():
        for key, v in kv.items():
            led.put(Fact(
                key=key, value=D(v), unit="元", currency="CNY", period=period,
                as_of=as_of, source_id=f"src_{period}", method=Method.REPORTED,
            ))
    return led


class TestGrowth:
    def test_yoy_computes_the_expected_rate(self):
        led = led_with({"2025Q3": {"revenue": "1200"}, "2024Q3": {"revenue": "1000"}})
        f = growth.yoy(led, "revenue", "2025Q3", "2024Q3", as_of=date(2025, 12, 1))
        assert f.value == D("0.2")
        assert f.unit == "ratio"

    def test_result_is_written_back_as_a_computed_fact(self):
        led = led_with({"2025Q3": {"revenue": "1200"}, "2024Q3": {"revenue": "1000"}})
        growth.yoy(led, "revenue", "2025Q3", "2024Q3", as_of=date(2025, 12, 1))
        f = led.get("revenue.yoy", "2025Q3", as_of=date(2025, 12, 1))
        assert f.method is Method.COMPUTED

    def test_computed_fact_records_its_inputs(self):
        led = led_with({"2025Q3": {"revenue": "1200"}, "2024Q3": {"revenue": "1000"}})
        f = growth.yoy(led, "revenue", "2025Q3", "2024Q3", as_of=date(2025, 12, 1))
        assert set(f.derived_from) == {"revenue@2025Q3", "revenue@2024Q3"}

    def test_computed_as_of_is_the_latest_input_disclosure_not_today(self):
        """派生事实不可能比它的输入更早可知，也不该比输入更晚。"""
        led = FactLedger()
        led.put(Fact(key="revenue", value=D("1000"), unit="元", period="2024Q3",
                     as_of=date(2024, 10, 25), source_id="a", method=Method.REPORTED))
        led.put(Fact(key="revenue", value=D("1200"), unit="元", period="2025Q3",
                     as_of=date(2025, 10, 25), source_id="b", method=Method.REPORTED))
        f = growth.yoy(led, "revenue", "2025Q3", "2024Q3", as_of=date(2026, 1, 1))
        assert f.as_of == date(2025, 10, 25)

    def test_zero_base_raises_rather_than_returning_infinity(self):
        led = led_with({"2025Q3": {"revenue": "1200"}, "2024Q3": {"revenue": "0"}})
        with pytest.raises(ZeroDivisionError):
            growth.yoy(led, "revenue", "2025Q3", "2024Q3", as_of=date(2025, 12, 1))

    def test_negative_base_growth_is_refused_as_meaningless(self):
        """由负转正的增长率没有经济含义，静默返回会污染下游。"""
        led = led_with({"2025Q3": {"net_profit": "50"}, "2024Q3": {"net_profit": "-100"}})
        with pytest.raises(ValueError, match="负"):
            growth.yoy(led, "net_profit", "2025Q3", "2024Q3", as_of=date(2025, 12, 1))


class TestMargins:
    def test_gross_and_net_margin(self):
        led = led_with({"2025Q3": {
            "revenue": "1000", "cost_of_revenue": "600", "net_profit": "120",
        }})
        out = margins.compute(led, "2025Q3", as_of=date(2025, 12, 1))
        assert out["gross_margin"].value == D("0.4")
        assert out["net_margin"].value == D("0.12")


class TestDuPont:
    def test_roe_decomposes_into_three_factors(self):
        led = led_with({"2025Q3": {
            "revenue": "1000", "net_profit": "100",
            "total_assets": "2000", "total_equity": "500",
        }})
        out = dupont.compute(led, "2025Q3", as_of=date(2025, 12, 1))
        assert out["net_margin"].value == D("0.1")
        assert out["asset_turnover"].value == D("0.5")
        assert out["equity_multiplier"].value == D("4")

    def test_product_of_factors_equals_roe(self):
        led = led_with({"2025Q3": {
            "revenue": "1000", "net_profit": "100",
            "total_assets": "2000", "total_equity": "500",
        }})
        out = dupont.compute(led, "2025Q3", as_of=date(2025, 12, 1))
        product = (out["net_margin"].value * out["asset_turnover"].value
                   * out["equity_multiplier"].value)
        assert out["roe"].value == pytest.approx(product)
        assert out["roe"].value == D("0.2")


class TestMultiples:
    def test_pe_and_pb(self):
        led = led_with({"2025Q3": {
            "market_cap": "10000", "net_profit_ttm": "500", "total_equity": "2500",
        }})
        out = multiples.compute(led, "2025Q3", as_of=date(2025, 12, 1))
        assert out["pe_ttm"].value == D("20")
        assert out["pb"].value == D("4")

    def test_negative_earnings_yields_no_pe_rather_than_a_negative_multiple(self):
        led = led_with({"2025Q3": {
            "market_cap": "10000", "net_profit_ttm": "-500", "total_equity": "2500",
        }})
        out = multiples.compute(led, "2025Q3", as_of=date(2025, 12, 1))
        assert "pe_ttm" not in out
        assert out["pb"].value == D("4")


class TestDCF:
    def test_known_case_reproduces_hand_calculation(self):
        r = dcf.value(
            fcf_base=D("100"),
            growth_rates=[D("0.10")] * 3,
            terminal_growth=D("0.03"),
            wacc=D("0.10"),
            net_debt=D("271.428571428571428571428571"),
            shares=D("100"),
        )
        assert r.pv_explicit == pytest.approx(D("300"), abs=D("0.01"))
        assert r.enterprise_value == pytest.approx(D("1771.43"), abs=D("0.01"))
        assert r.value_per_share == pytest.approx(D("15"), abs=D("0.01"))

    def test_terminal_value_dominates_and_is_reported_separately(self):
        """终值占比是 DCF 最重要的诊断指标，必须单独可见。"""
        r = dcf.value(
            fcf_base=D("100"), growth_rates=[D("0.10")] * 3,
            terminal_growth=D("0.03"), wacc=D("0.10"),
            net_debt=D("0"), shares=D("100"),
        )
        assert r.terminal_share > D("0.8")

    def test_wacc_not_above_terminal_growth_is_rejected(self):
        with pytest.raises(ValueError, match="WACC"):
            dcf.value(
                fcf_base=D("100"), growth_rates=[D("0.10")],
                terminal_growth=D("0.10"), wacc=D("0.10"),
                net_debt=D("0"), shares=D("100"),
            )

    def test_sensitivity_grid_spans_wacc_and_terminal_growth(self):
        grid = dcf.sensitivity(
            fcf_base=D("100"), growth_rates=[D("0.10")] * 3,
            waccs=[D("0.09"), D("0.10"), D("0.11")],
            terminal_growths=[D("0.02"), D("0.03")],
            net_debt=D("0"), shares=D("100"),
        )
        assert len(grid) == 3 and len(grid[0]) == 2
        # 折现率越高估值越低
        assert grid[0][0] > grid[2][0]


class TestDuPontSingleSource:
    """杜邦四项只能有一份实现。

    跑长电科技（600584）时暴露: ratios 与 dupont 各算了一遍「权益乘数」，
    一个用所有者权益合计（1.77x），一个用归母权益（1.94x），**两边都往
    账本写同一个键** —— 留下哪个值取决于执行顺序，比不一致更糟。
    对外表现是同一份研报里正文引用 1.94、可比表显示 1.77。
    """

    def _led(self):
        from ir_agent.ledger import Fact, FactLedger, Method
        led = FactLedger()
        vals = {"revenue": 100, "revenue_total": 100, "total_assets": 200,
                "total_equity": 120, "equity_attr_parent": 100,
                "net_profit": 10, "net_profit_attr_parent": 9,
                "cost_of_revenue": 80}
        for k, v in vals.items():
            led.put(Fact(key=k, value=Decimal(str(v)), unit="元",
                         currency="CNY", period="2025FY",
                         as_of=date(2025, 1, 1), source_id="t",
                         method=Method.REPORTED))
        return led

    def test_两个模块给出同一个权益乘数(self):
        from ir_agent.operators import dupont, ratios
        led = self._led()
        d = dupont.compute(led, "2025FY", date(2026, 1, 1))
        r = ratios.compute(led, "2025FY", date(2026, 1, 1))
        for k in ("net_margin", "asset_turnover", "equity_multiplier", "roe"):
            assert d[k].value == r.facts[k].value, k

    def test_归母口径优先(self):
        """少数股东利润占比与权益占比常常差得很远 —— 长电实测利润端 0.3%、
        权益端 8.4%。"""
        from ir_agent.operators import ratios
        r = ratios.compute(self._led(), "2025FY", date(2026, 1, 1))
        assert r.facts["equity_multiplier"].value == Decimal(200) / Decimal(100)
        assert r.facts["net_margin"].value == Decimal(9) / Decimal(100)

    def test_杜邦恒等式在ratios里也成立(self):
        from ir_agent.operators import ratios
        f = ratios.compute(self._led(), "2025FY", date(2026, 1, 1)).facts
        prod = (f["net_margin"].value * f["asset_turnover"].value
                * f["equity_multiplier"].value)
        assert abs(prod - f["roe"].value) < Decimal("1e-12")

    def test_缺归母数据时退回全口径(self):
        from ir_agent.ledger import Fact, Method
        from ir_agent.operators import ratios
        led = self._led()
        led._facts.pop(("equity_attr_parent", "2025FY"))       # noqa: SLF001
        led._facts.pop(("net_profit_attr_parent", "2025FY"))   # noqa: SLF001
        f = ratios.compute(led, "2025FY", date(2026, 1, 1)).facts
        assert f["equity_multiplier"].value == Decimal(200) / Decimal(120)
        assert f["equity_multiplier"].basis == "total"
