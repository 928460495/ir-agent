"""接 ashare-dcf 的桥。两个静默出错入口在这里锁住。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ir_agent import dcf
from ir_agent.ledger import Fact, FactLedger, Method

D = Decimal
AS_OF = date(2026, 9, 7)
YI = D("100000000")


def _led(**kv) -> FactLedger:
    led = FactLedger()
    for k, v in kv.items():
        led.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                     period="2025FY", as_of=date(2026, 4, 10),
                     source_id="em_x", method=Method.REPORTED))
    return led


def _cal(**items):
    """ashare-dcf 的 calibrate 输出片段。**它以亿元计**。"""
    return {"meta": {"last_annual": "20251231", "name": "某公司",
                     "price": 38.0, "quote_source": "tencent",
                     "beta": 1.2, "beta_raw": 1.4, "beta_n": 104},
            "stmts": {"income": {"20251231": {"营业收入": items.get("rev"),
                                              "净利润": items.get("np")}},
                      "balance": {"20251231": {"资产总计": items.get("ta"),
                                               "负债合计": items.get("tl")}}}}


# ── 一、两套数据源必须交叉核对 ───────────────────────────────

def test_单位换算正确():
    """ashare-dcf 以**亿元**计，账本以**元**计。换算错了会把每一项都
    报成天文数字的差异，或者反过来把真实差异淹掉。"""
    led = _led(revenue=97_900_000_000)          # 979 亿
    assert dcf.cross_check(_cal(rev=979.0), led, AS_OF) == []


def test_对不上就报出来():
    """研报正文写一个数、估值模型用另一个数而无人知晓，是这套系统最不该
    出现的事 —— 与此前「权益乘数 1.77 对 1.94」同一类，只是跨了系统边界。"""
    led = _led(revenue=97_900_000_000)
    out = dcf.cross_check(_cal(rev=950.0), led, AS_OF)
    assert len(out) == 1 and "营业收入" in out[0] and "979" in out[0]


def test_门槛设得紧():
    """两边取的是同一份公告数字，差异只会来自口径或单位，不会来自舍入。"""
    assert dcf.XCHECK_TOL <= D("0.01")
    led = _led(revenue=100 * 1e8)
    assert dcf.cross_check(_cal(rev=100.9), led, AS_OF)      # 差 0.9%
    assert dcf.cross_check(_cal(rev=100.2), led, AS_OF) == []  # 差 0.2%


def test_逐项核对而非只看一项():
    led = _led(revenue=100 * 1e8, net_profit=10 * 1e8,
               total_assets=200 * 1e8, total_liabilities=80 * 1e8)
    out = dcf.cross_check(_cal(rev=100, np=8, ta=200, tl=70), led, AS_OF)
    assert len(out) == 2 and any("净利润" in x for x in out) \
        and any("负债合计" in x for x in out)


def test_账本缺该项时跳过而非误报():
    assert dcf.cross_check(_cal(rev=979.0), FactLedger(), AS_OF) == []


def test_模型缺该项时跳过():
    led = _led(revenue=97_900_000_000)
    assert dcf.cross_check(_cal(), led, AS_OF) == []


def test_没有年报期间就说不能核对():
    out = dcf.cross_check({"meta": {}, "stmts": {}}, _led(revenue=1), AS_OF)
    assert out and "无法交叉核对" in out[0]


# ── 二、as_of：不报错的未来函数 ──────────────────────────────

def test_asof早于今天必须说出来():
    """ashare-dcf 取的是**当日**行情与最新报表。as_of 早于今天时它用的是
    未来信息 —— 这不是它的缺陷（它本就为「今天做一份模型」而设计），
    但两者接在一起时不说，就是一个不报错的未来函数。"""
    note = dcf._asof_note(date.today() - timedelta(days=30))
    assert note and "未来" not in note[0]        # 措辞不必吓人，但要说清
    assert "今日" in note[0] and "30 天" in note[0]


def test_asof就是今天则无需提示():
    assert dcf._asof_note(date.today()) == []


# ── 结果与落账本 ─────────────────────────────────────────────

def _res(**kw):
    base = dict(code="600584", workbook="/tmp/x.xlsx", price=D("38"),
                value_per_share=D("45.6"), wacc=D("0.0912"),
                terminal_roic=D("0.11"), reinvest_rate=D("0.42"),
                terminal_share=D("0.68"), verified=True)
    base.update(kw)
    return dcf.DcfResult(**base)


def test_空间由价值与现价算出():
    assert _res().upside == D("45.6") / D("38") - 1


def test_现价缺失时不给空间():
    assert _res(price=D(0)).upside is None


def test_摘要只用占位符():
    """与其它进提示词的摘要同一条约定 —— 提示词里出现裸数字、正文里却
    禁止引用，是自相矛盾的契约。"""
    from ir_agent.citation import audit_bare_numbers
    assert audit_bare_numbers(_res().summary()) == []


def test_验算未通过要写死在摘要里():
    txt = _res(verified=False).summary()
    assert "验算未通过" in txt and "不可采信" in txt


def test_估值结果落账本():
    fs = {f.key: f for f in dcf.to_facts(_res(), AS_OF)}
    assert fs["valuation.value_per_share"].value == D("45.6")
    assert fs["valuation.wacc"].unit == "ratio"
    assert all(f.period == AS_OF.isoformat() for f in fs.values())


def test_结果记为计算而非估计():
    """假设本身是判断，但由假设到结果这一步是确定性可复算的 ——
    工作簿就在那里，公式引擎跑过。"""
    assert all(f.method is Method.COMPUTED for f in dcf.to_facts(_res(), AS_OF))


# ── 找不到 ashare-dcf 时如实报告 ─────────────────────────────

def test_目录不存在时给出可操作的说明(tmp_path):
    with pytest.raises(dcf.DcfUnavailable) as e:
        dcf.run("600584", tmp_path, as_of=AS_OF,
                dirpath=Path(tmp_path) / "不存在")
    assert dcf.ENV_DIR in str(e.value)


def test_默认位置是同级目录():
    assert dcf.DEFAULT_DIR.name == "ashare-dcf"
