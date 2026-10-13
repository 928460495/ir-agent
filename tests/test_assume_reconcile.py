"""旧乘/加分解式经公式引擎做历史对账，与改动前逐位相同（spec 2026-10-14 五）。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from ir_agent import assume
from ir_agent.ledger import Fact, FactLedger, Method

AS_OF = date(2026, 10, 14)


class TestLegacyReconcile:
    def test_旧乘法链历史对账与旧compose逐位相同_真实量级(self):
        """真实营收是十几位的数，因子历史变化率是 28 位长小数 —— 若按真实营收
        作尺度去乘再除，Decimal 末位舍入会让结果与旧 `drivers.compose` 不再逐位
        相同。旧乘法链与尺度无关，对账时按 1 代入（`_reconcile_model` 里的
        `scale`）。夹具数值位数短时乘除无舍入，这条必须用真实量级的数。"""
        from ir_agent.drivers import Decomposition, Factor, Op, compose

        d = Decomposition("revenue", Op.MULTIPLY, [Factor("cap", "产能", "变化率"),
                                                    Factor("asp", "单价", "变化率")])
        led = FactLedger()
        # 随机抽样实测：按真实营收作尺度时约 45% 的样本末位不同；这一组是其中之一。
        data = {"cap": ("224015.726", "627805.789"),
                "asp": ("947761.234", "577525.846"),
                "revenue": ("40271366990.43", "42110587245.18")}
        for k, (v0, v1) in data.items():
            for per, v in (("2024FY", v0), ("2025FY", v1)):
                led.put(Fact(key=k, value=D(v), unit="x", period=per,
                             as_of=date(2026, 4, 1), source_id="t",
                             method=Method.REPORTED))
        res = assume._reconcile_decomp(d, led, AS_OF)["residuals"]
        vals = {k: D(data[k][1]) / D(data[k][0]) - 1 for k in ("cap", "asp")}
        assert res[0].composed == compose(d, vals)
