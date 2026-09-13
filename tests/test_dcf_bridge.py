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

def test_建模前必须有人批准():
    """**这是两阶段存在的理由。** 判断项（营收增速、毛利率）模型推不出来，
    只能靠分析得出 —— 也正因此必须有人看一眼再建模。第二阶段没有署名
    直接拒绝。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_valuation)
    assert "approved_by" in src
    assert "必须署名批准人" in src
    # 人改过的值要重新校验 —— 批准不等于豁免
    assert "assume.check(" in src


def test_完整研报只在建模之后产出():
    """建模之前就产出一份读起来像结论的完整研报，读者会把未经核实的假设
    当成定论。"""
    import inspect

    from ir_agent import full
    a = inspect.getsource(full.run_analysis)
    v = inspect.getsource(full.run_valuation)
    assert "BRIEF" in a and "REPORT" not in a.replace("REPORT = ", "")
    assert "REPORT" in v and "dcf.run(" in v
    assert v.index("dcf.run(") < v.index("_write_full_report(")


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


def test_分解式非空时写入drivers块():
    """Task 10 接线：判断项按因子分解时（assumptions.decomps 非空），
    dcf.run 要把 assume.to_driver_block 的产出写进 cal["judg"]["drivers"]，
    ashare_build 才会建「1b_驱动分解」表。apply_to 必须先跑一遍——
    to_driver_block 的 base_level 依赖 cal["judg"]["gm_hist"]，那是
    calibrate 的原始输出，不受 apply_to 影响，但顺序仍要摆对，避免
    后来人误以为可以颠倒。"""
    import inspect

    src = inspect.getsource(dcf.run)
    assert "assume.apply_to(" in src
    assert "to_driver_block(" in src
    assert src.index("apply_to(") < src.index("to_driver_block(")
    assert "1b_驱动分解" in src
    assert 'cal["judg"]["drivers"]' in src.replace("'", '"')


def test_估值摘要进了提示词():
    from ir_agent.analyst import FactCatalog, build_prompt
    from ir_agent.ledger import FactLedger

    cat = FactCatalog(ledger=FactLedger(), as_of=AS_OF)
    txt = build_prompt(cat, code="600584", period="2025FY",
                       valuation_summary="DCF 估值（ashare-dcf 三表模型）")
    assert "ashare-dcf 三表模型" in txt and "估值结果" in txt


def test_估值摘要里的占位符必须能被替换机制识别():
    """**产出端写的占位符，消费端必须真能解析。**

    实测教训（600584 端到端）：`summary()` 此前写的是
    `[[valuation.wacc]]`——没有 `@period`，而 `citation.REF_RE` 硬性要求
    `[[key@period]]` 两段。于是这些占位符压根不被识别为占位符，一路原样
    漏进最终交付的 report.md：整个估值段落是六个未替换的占位符，真实数字
    （每股 28.92、WACC 9.11% 等）只出现在终端摘要里，读者拿到的研报没有
    一个估值数字。

    事实本身是入了账本的（`to_facts` 以 `as_of.isoformat()` 作期间），
    占位符也是照契约写的——只是两端对格式的约定不一致。这条测试钉住
    「摘要里的每一个占位符都能被 REF_RE 识别」，而不只是「摘要里有占位符」。
    """
    from datetime import date

    from ir_agent.citation import REF_RE

    as_of = date(2026, 9, 12)
    txt = _res(as_of=as_of).summary()
    refs = REF_RE.findall(txt)
    assert refs, "摘要里应当有可识别的占位符"
    keys = {k for _, k, _ in refs}
    assert keys >= {"valuation.value_per_share", "valuation.price",
                    "valuation.wacc", "valuation.terminal_roic",
                    "valuation.terminal_share"}, \
        f"估值关键项都要能被识别，实际只认出 {keys}"
    for _, _, per in refs:
        assert per == as_of.isoformat(), \
            f"期间必须与 to_facts 落账本时一致（{as_of.isoformat()}），得到 {per}"


def test_估值占位符的期间与落账本的期间一致():
    """回归锁：占位符里的 period 与 to_facts 写进账本的 period 必须同源。
    两边各写一次字面量，早晚会分叉——这条测试让分叉立刻可见。"""
    from datetime import date

    from ir_agent.citation import REF_RE
    from ir_agent import dcf

    as_of = date(2026, 9, 12)
    fact_periods = {f.period for f in dcf.to_facts(_res(as_of=as_of), as_of)}
    ref_periods = {per for _, _, per in REF_RE.findall(_res(as_of=as_of).summary())}
    assert fact_periods == ref_periods, \
        f"账本期间 {fact_periods} 与占位符期间 {ref_periods} 不一致"


class Test批准的数字必须真的进了工作簿:
    """**闸门查的都是「用得对不对」，没有一道查「结果对不对」。**

    这是与「占位符有没有被替换」同一类的漏网。人工批准判断项是整个流程
    的核心关卡，而批准之后没有任何一道检查回头确认：工作簿里真正参与
    估值的那几个数，就是人批准的那几个数。

    此前踩过的两次都是这条链断在中间（C2 / C-a）：同一因子同一年出现
    两行，清单显示其中一行、工作簿用另一行；重复行让 path() 按年份排序
    后整体错位一格，人批准第 2 年的 7%、工作簿拿到的是第 1 年那条重复行
    的 30%。两次都是**人自己读工作簿**才发现的。修法都是"不许出现重复
    行"——仍然是在查输入用得对不对，没有查输出对不对。

    `ashare_verify.read_inputs()` 本来就会把工作簿里逐年的营收增速/毛利率
    读回来（`growth`/`gm`，已含 Δ 偏移），拿它与 `apply_to` 写进
    `cal["judg"]` 的三档比一遍即可闭环，成本几乎为零。
    """

    def _judg(self):
        return {"growth": [0.10, 0.08], "gm": [0.15, 0.16],
                "growth_scen": {"bull": [0.14, 0.12], "base": [0.10, 0.08],
                                "bear": [0.05, 0.03]},
                "gm_scen": {"bull": [0.18, 0.19], "base": [0.15, 0.16],
                            "bear": [0.12, 0.11]}}

    def test_对得上时不报任何问题(self):
        from ir_agent import dcf
        inp = {"growth": [0.10, 0.08], "gm": [0.15, 0.16], "dg": 0.0, "dm": 0.0}
        assert dcf._readback_check(self._judg(), inp) == []

    def test_Δ偏移不算对不上(self):
        """`1_假设` 里的「营收增速 Δ」是人有意加的整体偏移，`read_inputs`
        把它并进了 growth。它不是错位，扣掉再比。"""
        from ir_agent import dcf
        inp = {"growth": [0.12, 0.10], "gm": [0.15, 0.16],
               "dg": 0.02, "dm": 0.0}
        assert dcf._readback_check(self._judg(), inp) == []

    def test_工作簿的数与批准的对不上要报出来(self):
        """整体错位一格正是踩过的那次：人批准第 1 年 10%、第 2 年 8%，
        工作簿拿到的是 8%、8%。"""
        from ir_agent import dcf
        inp = {"growth": [0.08, 0.08], "gm": [0.15, 0.16],
               "dg": 0.0, "dm": 0.0}
        problems = dcf._readback_check(self._judg(), inp)
        assert problems, "工作簿用的数与批准的不一致，必须报出来"
        assert "营收增速" in problems[0]
        assert "不可采信" in "".join(problems)

    def test_选中的是别的档要说清是哪一档(self):
        """工作簿的「选择情景」被改成乐观档时，数字对得上三档里的一档、
        只是不是中性档——这与"数字错了"是两回事，要分开说，否则人会以为
        建模坏了。"""
        from ir_agent import dcf
        inp = {"growth": [0.14, 0.12], "gm": [0.18, 0.19],
               "dg": 0.0, "dm": 0.0}
        problems = dcf._readback_check(self._judg(), inp)
        assert problems
        assert "乐观" in "".join(problems) or "bull" in "".join(problems)

    def test_年数对不上也要报(self):
        from ir_agent import dcf
        inp = {"growth": [0.10], "gm": [0.15, 0.16], "dg": 0.0, "dm": 0.0}
        assert dcf._readback_check(self._judg(), inp)


def test_回读校验真的接在建表之后():
    """光有 `_readback_check` 不算数——它必须真的被 `run()` 调用，而且要
    用建完表之后回读的 `inp`（不是建表前的 cal），否则查的还是输入。

    照本仓库既有做法（见 test_预研排在数据采集之前）用源码断言接线：
    这一步要真跑一遍得先有 ashare-dcf 与一个真实工作簿，代价太大。"""
    import inspect
    from ir_agent import dcf

    src = inspect.getsource(dcf.run)
    assert "_readback_check(" in src, "回读校验没有接进 run()"
    assert src.index("read_inputs") < src.index("_readback_check("), \
        "必须用建表之后回读的 inp"
    assert "assumptions is not None" in src
