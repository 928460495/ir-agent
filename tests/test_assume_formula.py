"""判断项环节的公式化写法（spec 2026-10-14 子项目一 三、四）。

旧的乘/加分解式由 `tests/test_assume.py` 守着（翻译后逐位等价）；这里只测新写法。
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent import assume
from ir_agent.assume import AssumeFailed, Case
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.methodology import parse as parse_meth


def _notes(a) -> str:
    """代码读数/核对时记下的事实（子项目三：只报事实，不驳回）。"""
    return "\n".join(a.read_notes + list(getattr(getattr(a, "facts", None), "problems", [])))

AS_OF = date(2026, 10, 14)

CHANGE = parse_meth("""模式: 变化
因子: 量增 | 出货量同比增速 | 变化率 | 年报产销量 | 产销量
因子: 价增 | 平均单价同比变化率 | 变化率 | 研报价格跟踪 | 平均单价
因子: 提价 | 提价对毛利率的累计贡献 | pp | 调价函 | 调价
因子: 原料 | 原材料对毛利率的累计侵蚀 | pp | 年报风险因素 | 原材料价格
公式: 营收 = 上期(营收) × (1 + 量增) × (1 + 价增)
公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100
""").model

LEVEL = parse_meth("""模式: 水平
因子: 量 | 覆铜板出货量 | 万平方米 | 年报产销量 | 产销量
因子: 价 | 覆铜板平均单价 | 元/平方米 | [[ccl.asp]] | 平均单价
因子: 本 | 覆铜板单位成本 | 元/平方米 | 年报成本构成 | 单位成本
公式: 营收 = 量 × 价
公式: 毛利率 = (价 − 本) ÷ 价
""").model

Q = "「依据原文」"


def _change_lines(years=2):
    out = []
    for y in range(1, years + 1):
        out += [f"假设: 量增 | 第{y}年 | 2% | 5% | 8% | 扩产 {Q}",
                f"假设: 价增 | 第{y}年 | -3% | 0% | 2% | 价格 {Q}",
                f"假设: 提价 | 第{y}年 | -1 | 0.5 | 1.5 | 调价 {Q}",
                f"假设: 原料 | 第{y}年 | -2 | -1 | -0.5 | 原料 {Q}"]
    return "\n".join(out)


def _level_lines(years=1, base=True):
    out = []
    for y in range(1, years + 1):
        out += [f"假设: 量 | 第{y}年 | 100 | 110 | 120 | 扩产 {Q}",
                f"假设: 价 | 第{y}年 | 9 | 10 | 11 | 价格 {Q}",
                f"假设: 本 | 第{y}年 | 7.5 | 7.4 | 7.3 | 成本 {Q}"]
    if base:
        out += [f"基期: 量 | 100 | 年报产销量 {Q}",
                f"基期: 本 | 7.2 | 年报成本构成 {Q}"]
    return "\n".join(out)


def _ledger(asp="9.5"):
    led = FactLedger()
    for key, per, v in (("revenue", "2025FY", "2000000000"),
                        ("gross_margin", "2025FY", "0.25"),
                        ("ccl.asp", "2025FY", asp)):
        led.put(Fact(key=key, value=D(v), unit="x", period=per,
                     as_of=date(2026, 4, 1), source_id="t",
                     method=Method.REPORTED))
    return led


def _check(a, years, model, ledger=None, base_margin=D("0.25")):
    return assume.check(a, years, ledger=ledger or _ledger(), body=f"正文 {Q} 依据原文",
                 as_of=AS_OF, decomps=model, base_margin=base_margin)


class TestParse:
    def test_按短名解析_也认完整名(self):
        txt = _change_lines(1).replace("假设: 量增 |", "假设: 出货量同比增速 |")
        a = assume.parse(txt, 1, decomps=CHANGE)
        assert {r.factor_key for r in a.rows} == {f.key for f in CHANGE.factors}
        assert a.model is CHANGE and a.decomps == []

    def test_基期行(self):
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        assert a.base_values["量"].value == D(100)
        assert a.base_values["本"].source == "AI"

    def test_基期行重复记进读数事实(self):
        a = assume.parse(_level_lines() + f"\n基期: 量 | 99 | {Q}", 1, decomps=LEVEL)
        assert re.search("基期出现了不止一行", _notes(a)), _notes(a)

    @pytest.mark.parametrize("bad", ["110%", "110bp"])
    def test_其他单位不带百分号(self, bad):
        txt = _level_lines().replace("假设: 量 | 第1年 | 100 | 110 |",
                                     f"假设: 量 | 第1年 | 100 | {bad} |")
        a = assume.parse(txt, 1, decomps=LEVEL)
        assert re.search("单位是「|bp", _notes(a)), _notes(a)


PCT_LEVEL = parse_meth("""模式: 水平
因子: 稼动 | 产能利用率 | % | [[utilization]] | 稼动率
因子: 价 | 平均单价 | 元 | 年报 | 单价
公式: 营收 = 稼动 × 价
公式: 毛利率 = (价 − 稼动) ÷ 价
""").model

RATIO_LEVEL = parse_meth("""模式: 水平
因子: 稼动 | 产能利用率 | 变化率 | [[utilization]] | 稼动率
因子: 价 | 平均单价 | 元 | 年报 | 单价
公式: 营收 = 稼动 × 价
公式: 毛利率 = (价 − 稼动) ÷ 价
""").model


def _pct_ledger():
    led = _ledger()
    led.put(Fact(key="utilization", value=D("0.85"), unit="ratio", period="2025FY",
                 as_of=date(2026, 4, 1), source_id="t", method=Method.REPORTED))
    return led


def _pct_lines():
    return "\n".join([f"假设: 稼动 | 第1年 | 85 | 80 | 90 | 产能 {Q}",
                       f"假设: 价 | 第1年 | 9.5 | 9 | 10 | 单价 {Q}"])


class TestRatioUnitBase:
    """中国巨石 2026-10-18 试跑：账本 ratio 原值与因子声明的单位差 100 倍。"""

    def test_因子声明百分数_账本比率按声明换算(self):
        a = assume.parse(_pct_lines(), 1, decomps=PCT_LEVEL)
        _check(a, 1, PCT_LEVEL, ledger=_pct_ledger())
        bv = a.base_values["稼动"]
        assert bv.source == "账本" and bv.value == D(85), bv.value

    def test_因子声明变化率_账本比率原样取(self):
        txt = _pct_lines().replace("| 85 | 80 | 90 |", "| 0.85 | 0.8 | 0.9 |")
        a = assume.parse(txt, 1, decomps=RATIO_LEVEL)
        _check(a, 1, RATIO_LEVEL, ledger=_pct_ledger())
        assert a.base_values["稼动"].value == D("0.85")

    def test_账本不是比率单位_原样取(self):
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        _check(a, 1, LEVEL)
        assert a.base_values["价"].value == D("9.5"), "ccl.asp 单位是 x，不该被换算"


class TestLevelMode:
    def test_账本有的基期由代码取_其余AI补(self):
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        _check(a, 1, LEVEL)
        assert a.base_values["价"].source == "账本"
        assert a.base_values["价"].value == D("9.5")
        assert a.base_values["价"].bases[0].ref == "[[ccl.asp@2025FY]]"

    def test_缺基期明确记进事实(self):
        a = assume.parse(_level_lines(base=False), 1, decomps=LEVEL)
        facts = _check(a, 1, LEVEL)
        assert re.search("需要第 0 年.*量.*本", facts.text()), facts.text()

    def test_口径不混用_第0年由基期因子代入公式(self):
        """营收₀ = 100 × 9.5 = 950，与账本营收 20 亿无关。"""
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        _check(a, 1, LEVEL)
        g, m = assume.synthesize(a, Case.BASE, D("0.25"))
        assert g == [D(110) * D(10) / (D(100) * D("9.5")) - 1]
        assert m == [(D(10) - D("7.4")) / D(10)]

    def test_合成后三档不拉开记进事实(self):
        txt = _level_lines().replace("假设: 价 | 第1年 | 9 | 10 | 11 |",
                                     "假设: 价 | 第1年 | 11 | 10 | 9 |")
        a = assume.parse(txt, 1, decomps=LEVEL)
        facts = _check(a, 1, LEVEL)
        assert re.search("合成后", facts.text()), facts.text()


class TestChangeMode:
    def test_营收0取账本_亿元(self):
        a = assume.parse(_change_lines(), 2, decomps=CHANGE)
        _check(a, 2, CHANGE)
        assert a.base_revenue == D(20)
        g, m = assume.synthesize(a, Case.BASE, D("0.25"))
        assert g == [D("0.05"), D("0.05")]
        assert m == [D("0.25") + D("-0.5") / 100] * 2


class TestDescribe:
    def test_清单有公式_基期_合成_按单位格式化(self):
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        _check(a, 1, LEVEL)
        a.labels = ["2026E"]
        txt = a.describe()
        assert "## 分解式（水平模式）" in txt and "营收 = 量 × 价" in txt
        assert "| 覆铜板平均单价 | 9.5 元/平方米 | 账本 |" in txt
        assert "## 合成结果" in txt and "15.79%" in txt
        assert "110 万平方米" in txt


class TestPrompt:
    def test_提示词头部(self):
        p = assume.build_prompt({}, ["2026E"], "", decomps=LEVEL,
                                ledger_bases={"价": assume.BaseValue(D("9.5"))})
        assert "公式化分解填写（水平模式）" in p and "营收 = 量 × 价" in p
        assert "价 = 9.5（账本，已取到）" in p
        assert "需要你补：量、本" in p
        assert "`假设: <短名> |" in p and "**引文只能出自三处**" in p

    def test_旧分解式的提示词不变(self):
        from ir_agent.drivers import Decomposition, Factor, Op
        d = Decomposition("revenue", Op.MULTIPLY, [Factor("a", "产能", "变化率"),
                                                   Factor("b", "单价", "变化率")])
        p = assume.build_prompt({}, ["2026E"], "", decomps=[d])
        assert "**本次按驱动因子分解填写。**" in p and "公式化" not in p


class TestJson:
    def test_schema2往返保留公式与基期(self):
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        _check(a, 1, LEVEL)
        a.labels = ["2026E"]
        d = assume.to_json(a)
        assert d["schema"] == 2 and d["model"]["mode"] == "水平"
        back, edits = assume.from_json(d, code="600183")
        assert back.model.describe("gross_margin") == LEVEL.describe("gross_margin")
        assert back.base_values["价"].source == "账本"
        assert back.base_values["量"].value == D(100)
        assert edits == []
        assert assume.synthesize(back, Case.BASE, D("0.25")) == \
            assume.synthesize(a, Case.BASE, D("0.25"))

    def test_人手改坏公式给看得懂的话(self):
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        d = assume.to_json(a)
        d["model"]["formulas"]["revenue"] = "量 × 未声明"
        with pytest.raises(AssumeFailed, match="请重跑第一阶段"):
            assume.from_json(d)


class TestReconcile:
    def test_按数据线索的key取历史(self):
        led = FactLedger()
        for per, asp, rev in (("2023FY", "8", "800"), ("2024FY", "9", "990"),
                              ("2025FY", "9.5", "1100")):
            for k, v in (("ccl.asp", asp), ("revenue", rev)):
                led.put(Fact(key=k, value=D(v), unit="x", period=per,
                             as_of=date(2026, 4, 1), source_id="t",
                             method=Method.REPORTED))
        res = assume._reconcile_model(LEVEL, led, AS_OF)
        assert "覆铜板出货量" in res["revenue"]["no_history"]
        assert "覆铜板平均单价" not in res["revenue"]["no_history"]


class TestDriverBlock:
    def test_水平模式带基期因子(self):
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        _check(a, 1, LEVEL)
        cal = {"periods": {"annual": ["2025"]}, "rev": {"2025": 20.0},
               "cogs": {"2025": 15.0}, "judg": {"gm_hist": [0.25]}}
        drv = assume.to_driver_block(a, cal)
        assert drv["mode"] == "水平"
        assert drv["base"]["factors"] == {"量": 100.0, "价": 9.5, "本": 7.2}
        assert drv["values"]["base"] == [[110.0, 10.0, 7.4]]
        assert ["name", "量"] in drv["formulas"]["revenue"]


class TestPhase2:
    def test_只有model时校验仍走因子路径(self):
        """第二阶段从 assumptions.json 读回，公式化写法下只有 `model`、没有
        `decomps`。传 `decomps=a._m()` 才走因子校验；传 `a.decomps or None`
        （改动前的写法）会退回两标量校验 —— 那条路找不到营收增速/毛利率行。"""
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        _check(a, 1, LEVEL)
        back, _ = assume.from_json(assume.to_json(a))
        assert back.decomps == [] and back._m() is not None
        _check(back, 1, back._m())                       # 走因子路径，通过
        # 改动前的写法：退回两标量校验，把因子行当成营收增速/毛利率行去查
        # （`path()` 是两标量路径专用的措辞）。
        facts = _check(back, 1, back.decomps or None)
        assert re.search("缺第", facts.text()), facts.text()


def test_判断项清单顶部有方法论审核遗留():
    """子项目二决定 7：预研阶段审核者的提醒/待采集，人在批准判断项时要看得到。"""
    a = assume.parse(_level_lines(), 1, decomps=LEVEL)
    _check(a, 1, LEVEL)
    a.labels = ["2026E"]
    a.method_review = ["- [因子] 本（覆铜板单位成本）：待采集　年报未披露单位成本"]
    txt = a.describe()
    assert "方法论审核遗留" in txt and "年报未披露单位成本" in txt
    assert txt.index("方法论审核遗留") < txt.index("## 分解式")



def test_旧写法翻译成的FormulaModel也能出提示词():
    """第三次调用传的是 drivers.md 读回的 `FormulaModel`（旧「分解:」写法翻译过来，
    legacy=True），此前 build_prompt 把它当分解式列表迭代，直接 TypeError。"""
    m = parse_meth("分解: 营收 | 乘 | 出货量:变化率:年报:出货量 | 单价:变化率:年报:单价\n"
                   "分解: 毛利率 | 加 | 铜箔:pp:年报:铜箔 | 稼动率:pp:年报:稼动率\n").model
    assert m.legacy
    txt = assume.build_prompt({"g_stats": (), "gm_stats": ()}, ["2026E"], "上下文",
                              decomps=m, factor_data={})
    assert "出货量" in txt and "公式" in txt


class TestMarginRebased:
    """牧原股份 2026-10-18：水平模式下毛利率公式不读 上期/基期(毛利率)，锚定的基期
    平移对它不起作用；可批准清单横幅、终端与研报附录都按 `margin_bases` 非空说
    「第 2 年起毛利率的基期已换成锚定后的首年水平」——对人说了一句假话。"""

    def test_水平模式毛利率不读上期基期_平移不生效(self):
        a = assume.parse(_level_lines(), 1, decomps=LEVEL)
        a.margin_bases = {c: [D("0.30"), D("0.31")] for c in ("bull", "base", "bear")}
        assert not a.margin_rebased()

    def test_变化模式读基期毛利率_平移生效(self):
        a = assume.parse(_change_lines(1), 1, decomps=CHANGE)
        a.margin_bases = {c: [D("0.30"), D("0.31")] for c in ("bull", "base", "bear")}
        assert a.margin_rebased()

    def test_没锚定就谈不上平移(self):
        a = assume.parse(_change_lines(1), 1, decomps=CHANGE)
        assert not a.margin_rebased()
