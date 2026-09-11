"""账本 → 报表形状。这一层最容易出的错是**静默的零**。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import dcf_data as M
from ir_agent.ledger import Fact, FactLedger, Method

D = Decimal
AS_OF = date(2026, 9, 7)


def _led(period="2025FY", **kv):
    """**三张表都要有东西。** 三表模型缺任何一张都该报错，因此固件必须
    各表都喂一项，否则测的就不是我想测的那件事。"""
    led = FactLedger()
    kv = {"total_assets": 1, "cf_operating": 1, **kv}
    for k, v in kv.items():
        led.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                     period=period, as_of=date(2026, 4, 10),
                     source_id="em_x", method=Method.REPORTED))
    return led


# ── 期间换算 ─────────────────────────────────────────────────

@pytest.mark.parametrize("period,rd", [
    ("2025FY", "20251231"), ("2026H1", "20260630"),
    ("2025Q1", "20250331"), ("2025Q1-Q3", "20250930"),
])
def test_期间转报告日(period, rd):
    assert M.report_date(period) == rd


def test_认不出的期间返回空而不猜():
    """猜一个报告日会让那一期的数据落到错误的行上，而且不报错。"""
    assert M.report_date("2025TTM") is None
    assert M.report_date("不是期间") is None


# ── 铺表 ─────────────────────────────────────────────────────

def test_铺出三张表():
    led = _led(revenue=100, total_assets=200, cf_operating=30)
    f = M.to_frames(led, AS_OF)
    assert set(f) == {"income", "balance", "cashflow"}
    assert f["income"].loc["20251231", "营业收入"] == 100
    assert f["balance"].loc["20251231", "资产总计"] == 200
    assert f["cashflow"].loc["20251231", "经营活动产生的现金流量净额"] == 30


def test_值以元计不在这层换算():
    """ashare_data.val() 读出后会除以 1e8。这一层再除一次就是两处各除
    一次 —— 那类错误算出来的结果小一亿倍，但格式完全正常。"""
    f = M.to_frames(_led(revenue=97_900_000_000), AS_OF)
    assert f["income"].loc["20251231", "营业收入"] == 97_900_000_000


def test_多期间按报告日排序():
    led = _led("2024FY", revenue=90)
    for p, v in (("2025FY", 100), ("2023FY", 80)):
        led.put(Fact(key="revenue", value=D(v), unit="元", period=p,
                     as_of=date(2024, 4, 1), source_id="t",
                     method=Method.REPORTED))
    idx = list(M.to_frames(led, AS_OF)["income"].index)
    assert idx == sorted(idx)


def test_时点守卫仍然生效():
    """账本供数不等于绕过 as_of —— 尚未披露的期间不该出现在表里。"""
    led = _led(revenue=2)
    led.put(Fact(key="revenue", value=D(1), unit="元", period="2026FY",
                 as_of=date(2027, 4, 1), source_id="t",
                 method=Method.REPORTED))
    f = M.to_frames(led, AS_OF)
    assert "20261231" not in f["income"].index or \
        f["income"].loc["20261231"].isna().all()


def test_一项都铺不出就报错而非交空表():
    """空表交给 calibrate，它会把每一项都当成「该公司没有」继续算下去。"""
    with pytest.raises(M.MappingIncomplete):
        M.to_frames(FactLedger(), AS_OF)


def test_覆盖率可查():
    got = M.coverage(M.to_frames(_led(revenue=1), AS_OF))
    assert got["income"][1] == len(M.INCOME)
    assert got["income"][0] == 1
    assert got["balance"][0] == 1 and got["cashflow"][0] == 1


def test_缺任何一张表都报错():
    """三表模型缺一张就没法平衡，交空表给 calibrate 它会一路当零算下去。"""
    led = FactLedger()
    led.put(Fact(key="revenue", value=D(1), unit="元", period="2025FY",
                 as_of=date(2026, 4, 1), source_id="t",
                 method=Method.REPORTED))
    with pytest.raises(M.MappingIncomplete, match="balance"):
        M.to_frames(led, AS_OF)


# ── 科目名不能是我按印象写的 ─────────────────────────────────

def test_科目名取自ashare_dcf实际查找的字符串():
    """写错一个字，val() 返回 None，calibrate 当成「该公司没有这一项」
    继续算，不报任何错。**这类静默的零只有靠比对才看得见** ——
    verify_against_native() 就是为此存在的。"""
    assert "营业总收入" in M.INCOME.values()
    assert "购建固定资产、无形资产和其他长期资产所支付的现金" in M.CASHFLOW.values()
    assert "一年内到期的非流动负债" in M.BALANCE.values()
    # 交易性金融资产实测字段是 TRADE_FINASSET_NOTFVTPL，不是 TRADE_FINASSET
    assert "交易性金融资产" in M.BALANCE.values()


def test_逐格比对能发现映射错误():
    import pandas as pd
    ours = {"income": pd.DataFrame({"营业收入": [100.0]},
                                   index=pd.Index(["20251231"], name="报告日"))}
    same = {"income": pd.DataFrame({"营业收入": [100.0]},
                                   index=pd.Index(["20251231"], name="报告日"))}
    diff = {"income": pd.DataFrame({"营业收入": [120.0]},
                                   index=pd.Index(["20251231"], name="报告日"))}
    assert M.verify_against_native(ours, same) == []
    probs = M.verify_against_native(ours, diff)
    assert len(probs) == 1 and "营业收入" in probs[0]


def test_账本缺项而原生有则报出来():
    """静默的零正是这样产生的 —— 这一项必须被看见。"""
    import pandas as pd
    ours = {"income": pd.DataFrame({"营业收入": [100.0]},
                                   index=pd.Index(["20251231"], name="报告日"))}
    native = {"income": pd.DataFrame({"营业收入": [100.0], "研发费用": [8.0]},
                                     index=pd.Index(["20251231"], name="报告日"))}
    probs = M.verify_against_native(ours, native)
    assert any("研发费用" in p and "账本没铺出" in p for p in probs)
