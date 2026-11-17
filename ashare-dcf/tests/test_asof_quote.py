# -*- coding: utf-8 -*-
"""A 股 DCF 的行情、β、无风险利率一律截至分析时点（2026-11-17）。

生益科技 600183 验收：as_of=2026-10-21，但校准取的是**运行当天**的实时价 58.61
（10-21 收盘 118.99，其间股价腰斩），β 回归也用到了运行当天 —— 「现价」与 β 都掺进了
分析时点看不到的信息，内在价值与现价的比较失去意义。不联网。
"""
import datetime as dt
import os
import pickle
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_calibrate as C
import ashare_data as D

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cal_600183.pkl")
AS_OF = dt.date(2026, 10, 21)


def _series(start, n, seed):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    return idx, np.exp(np.cumsum(rng.normal(0, 0.01, n)))


def test_取分析时点当日或之前最后一个交易日的收盘():
    df = pd.DataFrame({"close": [100.0, 110.0, 118.99, 60.0]},
                      index=pd.to_datetime(["2026-10-17", "2026-10-20", "2026-10-21", "2026-11-17"]))
    assert D.last_close_on_or_before(df, AS_OF) == (dt.date(2026, 10, 21), 118.99)
    assert D.last_close_on_or_before(df, dt.date(2026, 10, 19)) == (dt.date(2026, 10, 17), 100.0)
    with pytest.raises(D.DataError):
        D.last_close_on_or_before(df, dt.date(2026, 10, 1))


def test_停牌太久的收盘不算分析时点的价格():
    df = pd.DataFrame({"close": [100.0]}, index=pd.to_datetime(["2026-09-01"]))
    with pytest.raises(D.DataError, match="停牌|距分析时点"):
        D.last_close_on_or_before(df, AS_OF)


def test_beta回归截至分析时点(monkeypatch):
    idx, ix = _series("2021-03-01", 1500, 1)
    st = ix ** 1.0
    cut = idx[idx <= pd.Timestamp(AS_OF)]
    after = len(idx) - len(cut)
    assert after > 20
    st = st.copy()
    st[len(cut):] = st[len(cut) - 1] * (ix[len(cut):] / ix[len(cut) - 1]) ** 3.0   # 时点之后 β≈3
    monkeypatch.setattr(C.D, "fetch_prices", lambda code: pd.DataFrame({"close": st}, index=idx))
    monkeypatch.setattr(C.D, "fetch_index", lambda: pd.DataFrame({"close": ix}, index=idx))
    _, raw_all, _ = C.beta_blume("600183")
    _, raw_cut, n = C.beta_blume("600183", as_of=AS_OF)
    assert abs(raw_cut - 1.0) < 1e-6 and raw_all > 1.01
    assert n < 1500


@pytest.fixture(scope="module")
def cal_dict():
    if not os.path.exists(FIX):
        pytest.skip("缺离线夹具")
    with open(FIX, "rb") as f:
        return pickle.load(f)


def _offline(monkeypatch, cal_dict, seen):
    meta = cal_dict["meta"]
    monkeypatch.setattr(C.D, "fetch_statements", lambda code: cal_dict["stmts"])
    monkeypatch.setattr(C.D, "fetch_quote", lambda code: dict(
        name=meta["name"], price=58.61, shares=24.29, mktcap=58.61 * 24.29, pe=40.0, pb=8.0,
        source="push2delay"))

    def rf(as_of=None):
        seen["rf"] = as_of
        return 0.0181, "2026-10-21"

    def beta(code, years=5, as_of=None):
        seen["beta"] = as_of
        return 1.38, 1.57, 1200

    def close(code, as_of):
        seen["close"] = as_of
        return dt.date(2026, 10, 21), 118.99
    monkeypatch.setattr(C.D, "fetch_riskfree", rf)
    monkeypatch.setattr(C, "beta_blume", beta)
    monkeypatch.setattr(C.D, "close_asof", close)
    monkeypatch.setattr(C.D, "fetch_eastmoney", lambda code, n=24: {"_errors": []})


def test_校准给了分析时点就用当时的收盘_β_无风险利率(monkeypatch, cal_dict):
    seen = {}
    _offline(monkeypatch, cal_dict, seen)
    cal = C.calibrate("600183", 10, stmts=cal_dict["stmts"], as_of=AS_OF)
    m = cal["meta"]
    assert m["price"] == 118.99 and m["asof"] == "2026-10-21" and m["quote_date"] == "2026-10-21"
    assert "2026-10-21" in m["quote_source"] and "不复权" in m["quote_source"]
    assert seen == {"rf": AS_OF, "beta": AS_OF, "close": AS_OF}
    assert cal["fin"]["issue_price"] == 118.99


def test_分析时点就是今天时仍用实时报价(monkeypatch, cal_dict):
    seen = {}
    _offline(monkeypatch, cal_dict, seen)
    cal = C.calibrate("600183", 10, stmts=cal_dict["stmts"], as_of=dt.date.today())
    assert cal["meta"]["price"] == 58.61 and "close" not in seen
