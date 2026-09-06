"""无风险利率与 β。

这两个数原本被判定为「免费源拿不到，只能人工填」。前者是没找对接口，
后者根本不该抓 —— 自己回归算，口径才说得清。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import beta as B
from ir_agent.ledger import Method
from ir_agent.sources import rates as R

D = Decimal
AS_OF = date(2026, 9, 7)


# ── 国债收益率：列映射靠恒等式自证 ────────────────────────────

def _row(day="2026-09-04", y2="1.243", y5="1.4014", y10="1.6804", y30="2.1335",
         spread=None, us=True):
    r = {"SOLAR_DATE": f"{day} 00:00:00",
         R.CN[2]: float(y2), R.CN[5]: float(y5),
         R.CN[10]: float(y10), R.CN[30]: float(y30),
         R.CN_SPREAD: float(spread if spread is not None
                            else D(y10) - D(y2))}
    if us:
        r.update({R.US[2]: 4.37, R.US[5]: 4.54, R.US[10]: 4.78,
                  R.US[30]: 5.24, R.US_SPREAD: 0.41})
    return r


def test_取十年期作为无风险利率(monkeypatch):
    monkeypatch.setattr(R, "_fetch", lambda size: [_row()])
    rf = R.risk_free(R.fetch_yields(), AS_OF)
    assert rf.value == D("0.016804")
    assert rf.tenor == 10
    assert rf.ref == "[[rate.cgb_10y@2026-09-04]]"


def test_期限结构全部入账(monkeypatch):
    monkeypatch.setattr(R, "_fetch", lambda size: [_row()])
    keys = {f.key for f in R.fetch_yields()}
    assert keys == {"rate.cgb_2y", "rate.cgb_5y",
                    "rate.cgb_10y", "rate.cgb_30y"}


def test_百分数归一且为Decimal(monkeypatch):
    monkeypatch.setattr(R, "_fetch", lambda size: [_row()])
    for f in R.fetch_yields():
        assert isinstance(f.value, Decimal)
        assert f.unit == "ratio" and f.value < 1
        assert f.method is Method.REPORTED


def test_列映射错位被恒等式抓住(monkeypatch):
    """上游改列编码不会报错，只会让所有估值一起偏 —— 必须自证。"""
    monkeypatch.setattr(R, "_fetch",
                        lambda size: [_row(spread="0.9999")])
    with pytest.raises(R.RatesUnavailable, match="列映射校验失败"):
        R.fetch_yields()


def test_利差微小舍入不误报(monkeypatch):
    monkeypatch.setattr(R, "_fetch", lambda size: [_row(spread="0.44")])
    assert R.fetch_yields()          # 0.4374 vs 0.44，在容差内


def test_某组缺数不影响另一组校验(monkeypatch):
    monkeypatch.setattr(R, "_fetch", lambda size: [_row(us=False)])
    assert R.fetch_yields()


def test_行情市场数据当日即可见(monkeypatch):
    """与统计数据不同 —— 这里没有「统计期 ≠ 发布日」的滞后。"""
    monkeypatch.setattr(R, "_fetch", lambda size: [_row()])
    f = next(f for f in R.fetch_yields() if f.key == "rate.cgb_10y")
    assert f.as_of == date(2026, 9, 4) and f.period == "2026-09-04"


def test_未到观测日不可见(monkeypatch):
    monkeypatch.setattr(R, "_fetch", lambda size: [_row()])
    fs = R.fetch_yields()
    assert R.risk_free(fs, date(2026, 9, 1)) is None
    assert R.risk_free(fs, date(2026, 9, 4)) is not None


def test_取时点内最新一期(monkeypatch):
    monkeypatch.setattr(R, "_fetch", lambda size: [
        _row(day="2026-09-04", y10="1.6804"),
        _row(day="2026-09-03", y10="1.6798")])
    assert R.risk_free(R.fetch_yields(), AS_OF).observed == date(2026, 9, 4)


def test_全空报错不猜(monkeypatch):
    monkeypatch.setattr(R, "_fetch", lambda size: [
        {"SOLAR_DATE": "2026-09-04 00:00:00"}])
    with pytest.raises(R.RatesUnavailable):
        R.fetch_yields()


# ── β：自己回归 ──────────────────────────────────────────────

def _series(rets, start="100"):
    """由收益率序列造周收盘价，日期用递增的伪周标签。"""
    out, px = {}, D(start)
    out["2024-01-05"] = px
    for i, r in enumerate(rets, 1):
        px = px * (1 + D(str(r)))
        out[f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"] = px
    return out


def _patch(monkeypatch, stock, bench):
    monkeypatch.setattr(B, "_closes",
                        lambda code, as_of, years:
                        bench if code == B.BENCHMARK else stock)


def test_完全同步则beta为一(monkeypatch):
    rm = [0.01, -0.02, 0.03, -0.01] * 20
    _patch(monkeypatch, _series(rm), _series(rm))
    r = B.estimate("600519", date(2026, 9, 7))
    assert abs(r.beta - 1) < D("0.001")
    assert r.r_squared > D("0.999")


def test_两倍放大则beta为二(monkeypatch):
    rm = [0.01, -0.02, 0.03, -0.01] * 20
    _patch(monkeypatch, _series([x * 2 for x in rm]), _series(rm))
    assert abs(B.estimate("600519", date(2026, 9, 7)).beta - 2) < D("0.001")


def test_产出R平方而非只给beta(monkeypatch):
    """与大盘几乎无关的股票也会算出某个 β，R² 才说明它有多少解释力。"""
    rm = [0.01, -0.02, 0.03, -0.01] * 20
    noise = [0.05, 0.05, -0.05, -0.05] * 20
    _patch(monkeypatch, _series(noise), _series(rm))
    r = B.estimate("600519", date(2026, 9, 7))
    assert r.r_squared < D("0.5")


def test_弱拟合显式标注(monkeypatch):
    rm = [0.01, -0.02, 0.03, -0.01] * 20
    _patch(monkeypatch, _series([0.05, 0.05, -0.05, -0.05] * 20), _series(rm))
    r = B.estimate("600519", date(2026, 9, 7))
    if r.weak_fit:
        assert "解释力有限" in r.describe()


def test_观测不足不给默认值(monkeypatch):
    """给默认 β=1.0 是个看不出来的假设。"""
    short = {f"2024-01-{i:02d}": D(100 + i) for i in range(1, 10)}
    _patch(monkeypatch, short, short)
    with pytest.raises(B.BetaUnavailable, match="少于下限"):
        B.estimate("600519", date(2026, 9, 7))


def test_只用时点之前的行情(monkeypatch):
    """接口的 end 参数不足以保证边界 —— 停牌与节假日都会让它漂。"""
    rm = [0.01, -0.02, 0.03, -0.01] * 20
    s, b = _series(rm), _series(rm)
    s["2027-12-31"] = D("999999")            # 远期未来价格
    b["2027-12-31"] = D("999999")
    _patch(monkeypatch, s, b)
    r = B.estimate("600519", date(2026, 9, 7))
    assert r.as_of < date(2026, 9, 8)
    assert abs(r.beta - 1) < D("0.001")      # 未来价格没有污染回归


def test_基准无波动时报错而非除零(monkeypatch):
    flat = {f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}": D(100)
            for i in range(120)}
    _patch(monkeypatch, _series([0.01] * 80), flat)
    with pytest.raises(B.BetaUnavailable):
        B.estimate("600519", date(2026, 9, 7))


def test_口径随事实一起走(monkeypatch):
    """「β 是 0.8」脱离窗口与基准就没有意义。"""
    rm = [0.01, -0.02, 0.03, -0.01] * 20
    _patch(monkeypatch, _series(rm), _series(rm))
    facts = B.beta_facts(B.estimate("600519", date(2026, 9, 7)))
    assert {f.key for f in facts} == {"beta", "beta.r_squared"}
    src = facts[0].derived_from
    assert any("沪深300" in x for x in src)
    assert any("周频" in x for x in src)
    assert any("前复权" in x for x in src)
    assert all(f.method is Method.COMPUTED for f in facts)
