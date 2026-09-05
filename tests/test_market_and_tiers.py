from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, Method
from ir_agent.market import UnsupportedMarket, market_of, quote_symbol

D = Decimal


class TestMarketDetection:
    @pytest.mark.parametrize("code,market", [
        ("600519", "SH"),   # 沪主板
        ("601318", "SH"),   # 沪主板
        ("688981", "SH"),   # 科创板
        ("000858", "SZ"),   # 深主板
        ("002594", "SZ"),   # 深主板（原中小板）
        ("300498", "SZ"),   # 创业板
        ("001979", "SZ"),   # 深主板 00 段
    ])
    def test_known_boards(self, code, market):
        assert market_of(code) == market

    @pytest.mark.parametrize("code", ["835185", "833819", "430047", "870508"])
    def test_beijing_exchange_is_explicitly_unsupported(self, code):
        """东财 F10 与新浪都不覆盖北交所财报（新浪只返回 19700101 占位列）。
        明确报错好过用错误的交易所后缀去拿一份空数据。"""
        with pytest.raises(UnsupportedMarket, match="北交所"):
            market_of(code)

    def test_unrecognised_code_raises_rather_than_guessing(self):
        with pytest.raises(UnsupportedMarket):
            market_of("999999")

    @pytest.mark.parametrize("code,symbol", [
        ("600519", "sh600519"), ("688981", "sh688981"),
        ("000858", "sz000858"), ("300498", "sz300498"),
    ])
    def test_quote_symbol_prefix(self, code, symbol):
        assert quote_symbol(code) == symbol


class TestTierOrder:
    """东财提为 Tier-1: 字段规范化、行业感知、金融股覆盖更高。
    新浪降为 Tier-2 —— 它解析路径完全独立，交叉验证价值不变。"""

    def test_eastmoney_is_tried_first(self):
        from ir_agent.sources.resolve import resolve
        order = []

        def mk(name):
            def _f():
                order.append(name)
                return [Fact(key="total_assets", value=D("1"), unit="元",
                             currency="CNY", period="2025FY",
                             as_of=date(2026, 3, 20), source_id=name,
                             method=Method.REPORTED)]
            return _f

        from ir_agent.pipeline import statement_sources
        r = resolve(statement_sources(mk("eastmoney"), mk("sina")),
                    cross_check=True)
        assert order[0] == "eastmoney"
        assert r.primary == "eastmoney"

    def test_eastmoney_values_win_on_disagreement(self):
        from ir_agent.pipeline import statement_sources
        from ir_agent.sources.resolve import resolve

        def mk(name, v):
            return lambda: [Fact(key="total_assets", value=D(v), unit="元",
                                 currency="CNY", period="2025FY",
                                 as_of=date(2026, 3, 20), source_id=name,
                                 method=Method.REPORTED)]

        r = resolve(statement_sources(mk("em", "100"), mk("sina", "110")),
                    cross_check=True)
        assert r.facts[0].value == D("100")
        assert r.disagreements[0].values["eastmoney"] == D("100")

    def test_sina_still_covers_when_eastmoney_fails(self):
        from ir_agent.pipeline import statement_sources
        from ir_agent.sources.resolve import resolve

        def boom():
            raise RuntimeError("em 反爬拦截")

        def sina():
            return [Fact(key="total_assets", value=D("7"), unit="元",
                         currency="CNY", period="2025FY",
                         as_of=date(2026, 3, 20), source_id="sina",
                         method=Method.REPORTED)]

        r = resolve(statement_sources(boom, sina), cross_check=True)
        assert r.primary == "sina"
        assert r.facts[0].value == D("7")
