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


# **用真实结构，不用我想象的结构。** 第一版固件把 stmts 写成字典嵌套，
# 于是 cross_check 也照着字典写 —— 实跑时才发现它是 pandas DataFrame
# （按报告日索引、列为科目名、值为**元**），当场抛
# 「The truth value of a DataFrame is ambiguous」。
#
# 取值一律走 ashare_data.val()（它负责 /1e8 换算），不在这边另写一份。
_D = None
try:
    _D = dcf._load(dcf.DEFAULT_DIR)[0]
except dcf.DcfUnavailable:
    pass

needs_ashare = pytest.mark.skipif(
    _D is None, reason="未找到 ashare-dcf，跳过依赖其真实结构的用例")


def _cal(**items):
    """calibrate 输出片段。**stmts 是 DataFrame，值以元计。**"""
    import pandas as pd
    def frame(cols):
        return pd.DataFrame({k: [v * 1e8] for k, v in cols.items()
                             if v is not None},
                            index=pd.Index(["20251231"], name="报告日"))
    return {"meta": {"last_annual": "20251231", "name": "某公司",
                     "price": 38.0, "quote_source": "tencent",
                     "beta": 1.2, "beta_raw": 1.4, "beta_n": 104},
            "stmts": {"income": frame({"营业总收入": items.get("rev"),
                                       "净利润": items.get("np")}),
                      "balance": frame({"资产总计": items.get("ta"),
                                        "负债合计": items.get("tl")})}}


def _xc(cal, led):
    return dcf.cross_check(cal, led, AS_OF, _D)


# ── 一、两套数据源必须交叉核对 ───────────────────────────────

@needs_ashare
def test_单位换算正确():
    """ashare-dcf 以**亿元**计，账本以**元**计。换算错了会把每一项都
    报成天文数字的差异，或者反过来把真实差异淹掉。"""
    led = _led(revenue=97_900_000_000)          # 979 亿
    assert _xc(_cal(rev=979.0), led) == []


@needs_ashare
def test_对不上就报出来():
    """研报正文写一个数、估值模型用另一个数而无人知晓，是这套系统最不该
    出现的事 —— 与此前「权益乘数 1.77 对 1.94」同一类，只是跨了系统边界。"""
    led = _led(revenue=97_900_000_000)
    out = _xc(_cal(rev=950.0), led)
    # 报的是**实际匹配到的**科目名（营业总收入），不是候选列表里的第一个
    # —— 读者要能据此去报表里找到那一行。
    assert len(out) == 1 and "营业总收入" in out[0]
    assert "979" in out[0] and "950" in out[0] and "3.0%" in out[0]


@needs_ashare
def test_门槛设得紧():
    """两边取的是同一份公告数字，差异只会来自口径或单位，不会来自舍入。"""
    assert dcf.XCHECK_TOL <= D("0.01")
    led = _led(revenue=100 * 1e8)
    assert _xc(_cal(rev=100.9), led)      # 差 0.9%
    assert _xc(_cal(rev=100.2), led) == []  # 差 0.2%


@needs_ashare
def test_逐项核对而非只看一项():
    led = _led(revenue=100 * 1e8, net_profit=10 * 1e8,
               total_assets=200 * 1e8, total_liabilities=80 * 1e8)
    out = _xc(_cal(rev=100, np=8, ta=200, tl=70), led)
    assert len(out) == 2 and any("净利润" in x for x in out) \
        and any("负债合计" in x for x in out)


@needs_ashare
def test_账本缺该项时跳过而非误报():
    assert _xc(_cal(rev=979.0), FactLedger()) == []


@needs_ashare
def test_模型缺该项时跳过():
    led = _led(revenue=97_900_000_000)
    assert _xc(_cal(), led) == []


def test_没有年报期间就说不能核对():
    out = dcf.cross_check({"meta": {}, "stmts": {}}, _led(revenue=1), AS_OF, _D)
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


@needs_ashare
def test_取值走ashare自己的换算而不另写一份():
    """`val()` 读原始值并除以 1e8 —— 换算逻辑只此一份。在这边照抄一遍，
    就是 ebit()、parent_first() 那类「一个概念两处实现」的老毛病。"""
    import inspect
    assert "D.val(" in inspect.getsource(dcf._stmt_value)
    assert "/ YI" not in inspect.getsource(dcf._stmt_value)


@needs_ashare
def test_科目名给候选而非写死():
    """不同公司列名略有出入（营业总收入 / 营业收入）。取不到就跳过，
    不误报成差异。"""
    names = dict((k, v) for k, _, v in
                 ((a, b, c) for a, b, c in dcf.XCHECK_ITEMS))
    assert "营业总收入" in names["revenue"] and "营业收入" in names["revenue"]


# ── 估值要先算，正文才谈得上「解释这个结果」 ─────────────────

def test_估值排在撰写之前():
    """原先估值跑在撰写之后，因为假设要从正文里挑（autodecide 读 body）。
    换成 ashare-dcf 后这个依赖没了 —— 假设由 calibrate() 从历史财务数据
    算出。**顺序因此可以倒过来，而且必须倒过来**: 一篇引用不到估值结果的
    研报，第 8 节只能空谈假设。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_full)
    assert src.index("dcf.run(") < src.index("_write_and_debate("), \
        "估值仍排在撰写之后，正文引用不到它"
    assert "valuation_summary=" in src


def test_第八节要求解释结果而非重推假设():
    from ir_agent.analyst import _RULES
    assert "不是你定的" in _RULES and "不要给目标价" in _RULES
    assert "对账" in _RULES


def test_提示词点明强周期的适用边界():
    """拿周期中某一点的利润率外推永续，是 DCF 最常见的误用 ——
    而「强周期判给 PB–ROE」那道硬闸已随估值模块删除，这层提醒因此更要紧。"""
    from ir_agent.analyst import _RULES
    assert "强周期标的" in _RULES and "外推永续" in _RULES
    assert "DOL" in _RULES or "经营杠杆" in _RULES


def test_估值摘要进了提示词():
    from ir_agent.analyst import FactCatalog, build_prompt
    from ir_agent.ledger import FactLedger

    cat = FactCatalog(ledger=FactLedger(), as_of=AS_OF)
    txt = build_prompt(cat, code="600584", period="2025FY",
                       valuation_summary="DCF 估值（ashare-dcf 三表模型）")
    assert "ashare-dcf 三表模型" in txt and "估值结果" in txt
