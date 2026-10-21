from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.operators import multiples, ttm

D = Decimal
AS_OF = date(2026, 4, 30)
LATER = date(2026, 5, 1)


def led_singles(key="net_profit", **by_period) -> FactLedger:
    led = FactLedger()
    for period, v in by_period.items():
        led.put(Fact(key=key, value=D(str(v)), unit="元", currency="CNY",
                     period=period, as_of=AS_OF, source_id="calc_x",
                     method=Method.COMPUTED))
    return led


class TestTTM:
    def test_sums_the_trailing_four_single_quarters(self):
        led = led_singles(**{"2024Q4S": 10, "2025Q1S": 20,
                             "2025Q2S": 30, "2025Q3S": 40})
        f = ttm.compute(led, "net_profit", 2025, 3, as_of=LATER)
        assert f.value == D("100")

    def test_ttm_at_year_end_equals_the_full_year(self):
        led = led_singles(**{"2025Q1S": 10, "2025Q2S": 20,
                             "2025Q3S": 30, "2025Q4S": 40})
        f = ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        assert f.value == D("100")

    def test_result_key_is_suffixed_ttm(self):
        led = led_singles(**{"2025Q1S": 10, "2025Q2S": 20,
                             "2025Q3S": 30, "2025Q4S": 40})
        f = ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        assert f.key == "net_profit_ttm"

    def test_missing_any_quarter_returns_none_rather_than_a_partial_sum(self):
        """三个季度加总冒充 TTM 会系统性低估，比没有 TTM 更糟。"""
        led = led_singles(**{"2025Q2S": 20, "2025Q3S": 30, "2025Q4S": 40})
        assert ttm.compute(led, "net_profit", 2025, 4, as_of=LATER) is None

    def test_derived_from_lists_all_four_quarters(self):
        led = led_singles(**{"2025Q1S": 10, "2025Q2S": 20,
                             "2025Q3S": 30, "2025Q4S": 40})
        f = ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        assert len(f.derived_from) == 4

    def test_trailing_window_crosses_the_year_boundary(self):
        led = led_singles(**{"2024Q3S": 5, "2024Q4S": 10,
                             "2025Q1S": 20, "2025Q2S": 30})
        f = ttm.compute(led, "net_profit", 2025, 2, as_of=LATER)
        assert f.value == D("65")


class TestPEIsNowReachable:
    """修复前 multiples 支持 pe_ttm，但没有任何路径写入 net_profit_ttm，
    PE 从未被算出过 —— 而且是静默跳过。"""

    def _led(self, q4=40):
        led = led_singles(**{"2025Q1S": 10, "2025Q2S": 20,
                             "2025Q3S": 30, "2025Q4S": q4})
        for k, v in (("market_cap", 2000), ("equity_attr_parent", 500)):
            led.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                         period="2025FY", as_of=AS_OF, source_id="src",
                         method=Method.REPORTED))
        return led

    def test_pe_is_produced_once_ttm_exists(self):
        led = self._led()
        ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        out = multiples.compute(led, "2025FY", as_of=LATER)
        assert out["pe_ttm"].value == D("20")

    def test_pe_still_absent_when_ttm_earnings_are_negative(self):
        led = self._led(q4=-200)
        ttm.compute(led, "net_profit", 2025, 4, as_of=LATER)
        out = multiples.compute(led, "2025FY", as_of=LATER)
        assert "pe_ttm" not in out


# ── 最新 TTM：PE 的分母配当天已知的最新利润，不停在报告期 ─────────────────
# 用年报期跑（--year 2025）时，原先 TTM 截到 2025 年报，2026 中报出来后 PE 仍按年报
# 利润算 —— 生益科技上半年利润 +50%，PE 被高估约一半。德赛西威、中国巨石同理。

def _cum_ledger(h1_disclosed=date(2026, 8, 20)):
    """归母净利润累计数：2025 全年四期 + 2026 Q1、H1。单季 2025 = 10/20/30/40，2026 = 25/35。"""
    led = FactLedger()
    rows = {"2025Q1": (10, date(2025, 4, 28)), "2025H1": (30, date(2025, 8, 20)),
            "2025Q1-Q3": (60, date(2025, 10, 28)), "2025FY": (100, date(2026, 4, 25)),
            "2026Q1": (25, date(2026, 4, 28)), "2026H1": (60, h1_disclosed)}
    for per, (v, disc) in rows.items():
        led.put(Fact(key="net_profit_attr_parent", value=D(v), unit="元", currency="CNY",
                     period=per, as_of=disc, source_id="em_x", method=Method.REPORTED))
    return led


class TestLatestTTM:
    def test_找到已披露的最新季度(self):
        assert ttm.latest_quarter(_cum_ledger(), "net_profit_attr_parent",
                                  date(2026, 10, 21)) == (2026, 2)

    def test_最新季度遵守时点(self):
        """中报 8 月 20 日才披露 —— 8 月 1 日只能看到 2026 一季报。"""
        assert ttm.latest_quarter(_cum_ledger(), "net_profit_attr_parent",
                                  date(2026, 8, 1)) == (2026, 1)

    def test_截至中报的TTM(self):
        """2025 下半年（30+40）+ 2026 上半年（25+35）= 130，不是 2025 年报的 100。"""
        f = ttm.ttm_at(_cum_ledger(), "net_profit_attr_parent", 2026, 2,
                       as_of=date(2026, 10, 21))
        assert f.value == D(130) and f.period == "2026H1"
        assert f.key == "net_profit_attr_parent_ttm"

    def test_缺季度时算不出而不是凑数(self):
        led = _cum_ledger()
        assert ttm.ttm_at(led, "net_profit_attr_parent", 2026, 3,
                          as_of=date(2026, 10, 21)) is None

    def test_取最新一期的TTM事实(self):
        led = FactLedger()
        for per, v in (("2025FY", 100), ("2026H1", 130)):
            led.put(Fact(key="net_profit_ttm", value=D(v), unit="元", period=per,
                         as_of=date(2026, 8, 20), source_id="calc", method=Method.COMPUTED))
        assert ttm.latest_visible(led, "net_profit_ttm", date(2026, 10, 21)).period == "2026H1"
        # 5 月 1 日两期都还没披露（as_of 都是 8 月 20 日）
        assert ttm.latest_visible(led, "net_profit_ttm", date(2026, 5, 1)) is None


def _pe_ledger():
    led = FactLedger()
    for key, per, v, disc in (("net_profit_ttm", "2025FY", 100, date(2026, 4, 25)),
                              ("net_profit_ttm", "2026H1", 150, date(2026, 8, 20)),
                              ("market_cap", "2026-10-21", 3000, date(2026, 10, 21)),
                              ("equity_attr_parent", "2025FY", 600, date(2026, 4, 25))):
        led.put(Fact(key=key, value=D(v), unit="元", period=per, as_of=disc,
                     source_id="x", method=Method.REPORTED))
    return led


def test_倍数算子的PE用最新TTM():
    out = multiples.compute(_pe_ledger(), "2025FY", as_of=date(2026, 10, 21),
                            spot_period="2026-10-21")
    assert out["pe_ttm"].value == D(20)          # 3000 / 150，不是 3000 / 100
    assert out["pb"].value == D(5)               # PB 不动，仍用报告期净资产


def test_比率模块的PE用最新TTM():
    from ir_agent.operators import ratios
    from ir_agent.sources.eastmoney import CompanyType
    rs = ratios.compute(_pe_ledger(), "2025FY", date(2026, 10, 21), CompanyType.GENERAL,
                        spot="2026-10-21")
    assert rs.facts["pe_ttm"].value == D(20)


def test_pipeline算出最新TTM():
    import inspect

    from ir_agent import pipeline
    src = inspect.getsource(pipeline)
    assert "ttm.latest_quarter(" in src and "ttm.ttm_at(" in src
