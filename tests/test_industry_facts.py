"""行业聚合。重点在两个会静默出错的地方: 同比口径、覆盖率。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.industry_facts import (
    IndustryAggregate,
    Missing,
    aggregate,
    industry_facts,
)
from ir_agent.sources.industry import Classification, Member, Peers

D = Decimal
AS_OF = date(2026, 4, 30)


def _peers(target="600519", n=5, thin=False):
    return Peers(
        target=target, target_name="贵州茅台",
        classification=Classification(target, "贵州茅台",
                                      "食品饮料-白酒Ⅱ-白酒Ⅲ",
                                      "食品饮料-白酒Ⅱ-白酒Ⅲ", 3),
        members=[Member(f"00000{i}", f"同业{i}") for i in range(n)],
        thin=thin)


def _agg(revenues, prior=None, missing=(), target="600519", thin=False):
    return IndustryAggregate(
        target=target, target_name="贵州茅台", industry="白酒Ⅲ",
        period="2025FY", prior_period="2024FY", as_of=AS_OF,
        revenues={k: D(v) for k, v in revenues.items()},
        prior={k: D(v) for k, v in (prior or {}).items()},
        missing=[Missing(c, c, "无数据") for c in missing], thin=thin)


# ── 同比口径 ─────────────────────────────────────────────────

def test_同比在两期都有数的交集上计算():
    """新增公司只有本期数，计入分子不计入分母会虚增行业增速。"""
    a = _agg(revenues={"600519": 100, "000001": 100, "新公司": 500},
             prior={"600519": 90, "000001": 90})
    # 正确: (100+100)/(90+90)-1 = 11.1%
    assert D("0.111") < a.yoy < D("0.112")
    assert a.yoy_basis == 2
    # 错法会是 (100+100+500)/(90+90)-1 = 288%
    assert a.yoy < D("1")


def test_同比基数写进摘要():
    a = _agg({"600519": 100, "x": 100, "y": 500}, {"600519": 90, "x": 90})
    assert "两期都有数的 2 家" in a.summary()


def test_缺上年同期则无同比():
    assert _agg({"600519": 100}).yoy is None


def test_上年同期合计为零不除零():
    assert _agg({"600519": 100}, {"600519": 0}).yoy is None


# ── 覆盖率 ───────────────────────────────────────────────────

def test_覆盖不足时不出行业总量与市占率():
    """残缺的分母会系统性高估市占率 —— 宁可不给。"""
    a = _agg({"600519": 100, "x": 50}, {"600519": 90, "x": 45},
             missing=["a", "b", "c", "d"])
    assert a.coverage == D("2") / 6 and not a.covered
    assert a.share is None and a.cr5 is None
    assert "不出行业总量" in a.summary()


def test_覆盖充足时给出市占率与排名():
    rev = {"600519": 100, "a": 50, "b": 30, "c": 20}
    a = _agg(rev, missing=[])
    assert a.covered
    assert a.share == D("0.5")
    assert a.rank == 1
    assert a.cr5 == D(1)                      # 4 家不足 5 家，全取即 100%


def test_覆盖率恰好在阈值上视为达标():
    a = _agg({str(i): 10 for i in range(7)}, missing=["x", "y", "z"])
    assert a.coverage == D("0.7") and a.covered


# ── 份额与超额增速 ───────────────────────────────────────────

def test_份额变化在同一组公司上比较():
    """份额的分子分母都必须来自同一交集，否则「份额提升」可能是同业退出造成的。"""
    a = _agg(revenues={"600519": 120, "x": 80, "新": 100},
             prior={"600519": 100, "x": 100})
    # 交集口径: 120/200 - 100/200 = +10pp
    assert a.share_delta == D("0.1")


def test_超额增速等于公司减行业():
    a = _agg({"600519": 120, "x": 100}, {"600519": 100, "x": 100})
    assert a.target_yoy == D("0.2")
    assert a.yoy == D("0.1")
    assert a.excess_growth == D("0.1")


def test_标的缺数据时相关指标全部为空():
    a = _agg({"x": 100}, {"x": 90})
    assert a.share is None and a.rank is None
    assert a.target_yoy is None and a.excess_growth is None
    assert a.share_delta is None


# ── 取数 ─────────────────────────────────────────────────────

def test_取数覆盖标的自身():
    data = {("600519", "2025FY"): D(100), ("600519", "2024FY"): D(80),
            ("000000", "2025FY"): D(50), ("000000", "2024FY"): D(50)}
    a = aggregate(_peers(n=1), "2025FY", "2024FY", AS_OF,
                  lambda c, p, d: data.get((c, p)))
    assert set(a.revenues) == {"600519", "000000"}
    assert a.target_yoy == D("0.25")


def test_取数异常记为缺失而非中断():
    def fetch(code, period, as_of):
        if code == "000001":
            raise RuntimeError("接口挂了")
        return D(10)

    a = aggregate(_peers(n=3), "2025FY", "2024FY", AS_OF, fetch)
    assert "000001" not in a.revenues
    assert any(m.code == "000001" and "RuntimeError" in m.reason
               for m in a.missing)
    assert len(a.revenues) == 3            # 标的 + 另两家


def test_本期有数上年抓取失败仍计入本期():
    def fetch(code, period, as_of):
        if period == "2024FY":
            raise RuntimeError("上年挂了")
        return D(10)

    a = aggregate(_peers(n=1), "2025FY", "2024FY", AS_OF, fetch)
    assert len(a.revenues) == 2 and a.prior == {}
    assert a.yoy is None


def test_样本薄标志透传():
    a = aggregate(_peers(n=1, thin=True), "2025FY", "2024FY", AS_OF,
                  lambda c, p, d: D(10))
    assert a.thin and "样本偏薄" in a.summary()


# ── 入账本 ───────────────────────────────────────────────────

def test_行业结论写入账本供正文引用():
    """不入账本，分位数和市占率在正文里就是裸数字，会被审计驳回。"""
    a = _agg({"600519": 100, "a": 50, "b": 30, "c": 20},
             {"600519": 80, "a": 50, "b": 30, "c": 20})
    keys = {f.key for f in industry_facts(a)}
    assert keys >= {"industry.revenue", "industry.revenue_yoy", "industry.cr5",
                    "company.market_share", "company.market_share_delta",
                    "company.industry_rank", "industry.peer_count",
                    "industry.coverage", "company.revenue_yoy_vs_industry"}


def test_覆盖不足时行业口径的事实不入账():
    a = _agg({"600519": 100, "a": 50}, {"600519": 80, "a": 50},
             missing=["x", "y", "z", "w"])
    keys = {f.key for f in industry_facts(a)}
    assert "industry.revenue" not in keys
    assert "company.market_share" not in keys
    # 同比与超额增速不依赖全覆盖，仍应给出
    assert "industry.revenue_yoy" in keys
    assert "industry.coverage" in keys


def test_入账事实全部标记为computed且带derived_from():
    from ir_agent.ledger import Method
    a = _agg({"600519": 100, "a": 50, "b": 30, "c": 20},
             {"600519": 80, "a": 50, "b": 30, "c": 20})
    for f in industry_facts(a):
        assert f.method is Method.COMPUTED
        assert isinstance(f.value, Decimal)
        if f.key not in ("industry.peer_count", "industry.coverage"):
            assert f.derived_from, f.key


def test_空聚合不产出垃圾事实():
    a = _agg({})
    assert a.coverage == D(0)
    keys = {f.key for f in industry_facts(a)}
    assert "industry.revenue" not in keys
