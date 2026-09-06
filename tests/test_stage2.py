"""第二阶段: 审核 → 建模 → 补进看板与 Excel。

这一阶段的每一步都可能正当地失败，而**失败必须说清楚下一步做什么**:
依据核验不过、路由不适用 DCF、FCF 为负 —— 三者的处理方式完全不同。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.stage2 import ValuationBlocked, value_from_file

D = Decimal


def _assumptions():
    from ir_agent.valuation.assumptions import Assumptions
    from ir_agent.valuation.basis import Basis
    return Assumptions(
        wacc=D("0.09"), terminal_growth=D("0.025"),
        growth_rates=[D("0.03")] * 5,
        basis={"wacc": [Basis.external("https://x.cn/gz", "国债 2.5%",
                                       date(2026, 6, 1))],
               "terminal_growth": [Basis.external("https://x.cn/g", "GDP 5%",
                                                  date(2026, 6, 1))],
               "growth_rates": [Basis.report("增速回落")]})


def _ledger(fcf=100, cash=50, price=10, cap=1000):
    from ir_agent.ledger import Fact, FactLedger, Method
    l = FactLedger()
    vals = {"cf_operating": fcf + 30, "capex": 30,
            "cash_and_equivalents": cash, "cash_end": cash}
    for k, v in vals.items():
        l.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                   period="2025FY", as_of=date(2026, 4, 20),
                   source_id="em_x", method=Method.REPORTED))
    for k, v in (("market_cap", cap), ("last_price", price)):
        l.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                   period="2026-09-04", as_of=date(2026, 9, 4),
                   source_id="tencent", method=Method.REPORTED))
    return l


BODY = "## 盈利质量\n增速回落，但盈利能力稳固。\n"
AS_OF = date(2026, 10, 1)


class TestHappyPath:
    def _run(self, **kw):
        return value_from_file(
            assumptions=_assumptions(), ledger=_ledger(), body=BODY,
            period="2025FY", spot_period="2026-09-04", as_of=AS_OF,
            reviewer="谢海量", **kw)

    def test_returns_a_valuation(self):
        assert self._run().valuation.value_per_share > 0

    def test_reviewer_is_recorded(self):
        assert self._run().valuation.reviewed_by == "谢海量"

    def test_sensitivity_grid_is_produced(self):
        s = self._run().sensitivity
        assert len(s["grid"]) == len(s["waccs"])

    def test_current_assumption_cell_is_marked(self):
        s = self._run().sensitivity
        i, j = s["current"]
        assert s["waccs"][i] == D("0.09") and s["growths"][j] == D("0.025")

    def test_assumptions_enter_the_ledger_as_estimated(self):
        from ir_agent.ledger import Method
        r = self._run()
        assert r.ledger.get("wacc", "2025FY",
                            as_of=AS_OF).method is Method.ESTIMATED


class TestBlockedPaths:
    def test_unverifiable_basis_blocks_with_the_reason(self):
        from ir_agent.valuation.basis import Basis
        a = _assumptions()
        a = type(a)(wacc=a.wacc, terminal_growth=a.terminal_growth,
                    growth_rates=a.growth_rates,
                    basis={**a.basis, "growth_rates": [Basis.report("并不存在的句子")]})
        with pytest.raises(ValuationBlocked, match="正文"):
            value_from_file(assumptions=a, ledger=_ledger(), body=BODY,
                            period="2025FY", spot_period="2026-09-04",
                            as_of=AS_OF, reviewer="谢海量")

    def test_negative_fcf_blocks_with_a_pointer_to_pb_roe(self):
        with pytest.raises(ValuationBlocked, match="PB"):
            value_from_file(assumptions=_assumptions(),
                            ledger=_ledger(fcf=-200), body=BODY,
                            period="2025FY", spot_period="2026-09-04",
                            as_of=AS_OF, reviewer="谢海量")

    def test_missing_input_blocks_with_the_missing_key(self):
        from ir_agent.ledger import FactLedger
        with pytest.raises(ValuationBlocked, match="cf_operating|capex"):
            value_from_file(assumptions=_assumptions(), ledger=FactLedger(),
                            body=BODY, period="2025FY",
                            spot_period="2026-09-04", as_of=AS_OF,
                            reviewer="谢海量")

    def test_empty_reviewer_is_refused(self):
        with pytest.raises(ValuationBlocked, match="审核人"):
            value_from_file(assumptions=_assumptions(), ledger=_ledger(),
                            body=BODY, period="2025FY",
                            spot_period="2026-09-04", as_of=AS_OF,
                            reviewer="  ")
