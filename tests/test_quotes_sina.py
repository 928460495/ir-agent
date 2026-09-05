from datetime import datetime
from decimal import Decimal

import pytest

from ir_agent.sources.quotes import ParseError, parse_sina_quote

# 2026-09-04 从 hq.sinajs.cn 实抓
SINA_RAW = (
    'var hq_str_sh600183="生益科技,145.200,143.190,137.500,147.800,135.470,'
    '137.500,137.510,67020788,9420477343.000,'
    '859,137.500,100,137.480,300,137.470,100,137.460,200,137.450,'
    '100,137.510,100,137.520,900,137.530,800,137.550,500,137.560,'
    '2026-09-04,15:34:59,00,D|51400|7067500.00";'
)


class TestSinaQuoteParsing:
    def test_extracts_identity_and_price(self):
        q = parse_sina_quote(SINA_RAW)
        assert q["name"] == "生益科技"
        assert q["last"] == Decimal("137.500")

    def test_open_high_low_prev_close(self):
        q = parse_sina_quote(SINA_RAW)
        assert q["open"] == Decimal("145.200")
        assert q["prev_close"] == Decimal("143.190")
        assert q["high"] == Decimal("147.800")
        assert q["low"] == Decimal("135.470")

    def test_change_is_derived_since_sina_does_not_report_it(self):
        q = parse_sina_quote(SINA_RAW)
        assert q["change"] == Decimal("137.500") - Decimal("143.190")

    def test_as_of_combines_date_and_time_columns(self):
        q = parse_sina_quote(SINA_RAW)
        assert q["as_of"] == datetime(2026, 9, 4, 15, 34, 59)

    def test_market_cap_is_absent_because_sina_does_not_supply_it(self):
        """Tier-2 覆盖是子集。谎称有市值会让降级后的估值凭空出现。"""
        q = parse_sina_quote(SINA_RAW)
        assert q.get("market_cap") is None
        assert q.get("pe_ttm") is None

    def test_empty_response_raises(self):
        with pytest.raises(ParseError):
            parse_sina_quote('var hq_str_sh600183="";')

    def test_truncated_response_raises(self):
        with pytest.raises(ParseError, match="字段"):
            parse_sina_quote('var hq_str_sh600183="生益科技,145.200";')

    def test_suspended_stock_with_zero_price_raises(self):
        """停牌时新浪返回价格全 0，静默当成 0 元会污染整条链路。"""
        raw = ('var hq_str_sh600183="生益科技,0.000,143.190,0.000,0.000,0.000,'
               '0.000,0.000,0,0.000,' + '0,0.000,' * 10 +
               '2026-09-04,15:34:59,00";')
        with pytest.raises(ParseError, match="停牌"):
            parse_sina_quote(raw)
