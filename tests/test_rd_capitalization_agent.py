"""研发资本化的供数与落账本（spec 2026-11-24-rd-capitalization §4.5、§4.6）。不联网。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.rd_life import life_for_sw

AS_OF = date(2026, 11, 20)


@pytest.mark.parametrize("path,life", [
    ("医药生物-化学制药-化学制剂", 10),
    ("医药生物-医疗器械-医疗设备", 5),
    ("医药生物", 10),
    ("计算机-软件开发-垂直应用软件", 3),
    ("计算机-计算机设备-其他计算机设备", 5),
    ("电子-半导体-数字芯片设计", 5),
    ("通信-通信设备-通信网络设备及器件", 10),
    ("传媒-游戏Ⅱ-游戏Ⅲ", 3),
    ("汽车-汽车零部件-车身附件及饰件", 10),
    ("食品饮料-白酒Ⅱ-白酒Ⅲ", 5),
    ("", 5),
])
def test_申万年限映射(path, life):
    assert life_for_sw(path) == life


def test_开发支出无形资产进报表列():
    from ir_agent import dcf_data
    from ir_agent.sources import eastmoney
    assert dcf_data.BALANCE["development_expense"] == "开发支出"
    assert dcf_data.BALANCE["intangible_asset"] == "无形资产"
    assert eastmoney._COMMON_BALANCE["DEVELOP_EXPENSE"] == "development_expense"


_SZ = """四、主营业务分析
4、研发投入
公司研发投入情况
2025 年
2024 年
变动比例
研发投入金额（元）
8,228,000,000.00
7,000,000,000.00
17.54%
研发投入占营业收入比例
29.40%
28.00%
1.40%
研发投入资本化的金额（元）
1,645,000,000.00
1,200,000,000.00
37.08%
资本化研发投入占研发投入的比例
19.99%
17.14%
2.85%
"""

_SH = """（四）研发投入情况表
单位：元
本期费用化研发投入
465,000,000.00
本期资本化研发投入
35,000,000.00
研发投入合计
500,000,000.00
研发投入总额占营业收入比例（%）
6.20
研发投入资本化的比重（%）
7.00
"""


def test_年报研发投入表_深市格式():
    from ir_agent.sources.annual_notes import extract_fin_notes
    n = extract_fin_notes(["无关页", _SZ])
    assert n.rd_total == Decimal("8228000000.00") and n.rd_capitalized == Decimal("1645000000.00")
    assert n.rd_cap_ratio == Decimal("0.1999")


def test_年报研发投入表_沪市格式():
    from ir_agent.sources.annual_notes import extract_fin_notes
    n = extract_fin_notes([_SH])
    assert n.rd_total == Decimal("500000000.00") and n.rd_capitalized == Decimal("35000000.00")
    assert n.rd_cap_ratio == Decimal("0.07")


def test_年报研发投入表_缺表():
    from ir_agent.sources.annual_notes import extract_fin_notes
    n = extract_fin_notes(["没有研发投入表的一页"])
    assert n.rd_total is None and n.rd_capitalized is None and n.rd_cap_ratio is None
    assert n.missing == [m for m in n.missing if "研发投入" not in m]      # 不进 missing（那是「按保守处理」清单）


def test_资本化率落账本():
    from ir_agent import dcf
    import dataclasses
    kw = {f.name: (f.default if f.default is not dataclasses.MISSING else
                   (f.default_factory() if f.default_factory is not dataclasses.MISSING else Decimal(0)))
          for f in dataclasses.fields(dcf.DcfResult)}
    kw.update(code="600276", rd_total=Decimal("8228000000"), rd_capitalized=Decimal("1645000000"),
              rd_cap_rate=Decimal("0.1999"), scenario_ps={}, mc=None)
    facts = {f.key: f for f in dcf.to_facts(dcf.DcfResult(**kw), AS_OF)}
    assert facts["valuation.rd_cap_rate"].unit == "ratio" and facts["valuation.rd_cap_rate"].value == Decimal("0.1999")
    assert facts["valuation.rd_total"].unit == "元"
    assert facts["valuation.rd_capitalized"].value == Decimal("1645000000")


def test_calibrate收到行业年限与研发披露(monkeypatch):
    from ir_agent import dcf
    seen = {}

    class FakeC:
        @staticmethod
        def calibrate(code, years, **kw):
            seen.update(kw)
            raise RuntimeError("stop")

    class FakeD:
        DataError = RuntimeError

        @staticmethod
        def validate_code(c):
            return c

    monkeypatch.setattr(dcf, "_fin_notes", lambda url, notes: {
        "nca1y_fin": None, "statutory_reserve": None, "term_restricted": {}, "encumbered": {},
        "rd_total": Decimal("10"), "rd_capitalized": Decimal("2"), "rd_cap_ratio": Decimal("0.2")})
    with pytest.raises(Exception):
        dcf._calibrate(FakeD, FakeC, "600276", AS_OF, None, 10, "http://x/a.pdf",
                       industry="医药生物-化学制药-化学制剂")
    assert seen["rd_life"] == 10
    assert seen["rd_disclosure"] == {"total": Decimal("10"), "capitalized": Decimal("2"), "rate": Decimal("0.2")}
    assert "rd_total" not in seen["fin_notes"]


def test_dcf_kw带上申万路径():
    from types import SimpleNamespace

    from ir_agent import full
    r = SimpleNamespace(code="600276", annual_url="http://x/a.pdf",
                        peer_set=SimpleNamespace(classification=SimpleNamespace(used_path="医药生物-化学制药-化学制剂")))
    assert full._dcf_kw(r) == {"annual_url": "http://x/a.pdf", "industry": "医药生物-化学制药-化学制剂"}


def test_无行业无研发表时不传新参数(monkeypatch):
    """旧调用路径（无行业路径、年报没有研发投入表）与改动前完全相同。"""
    from ir_agent import dcf
    seen = {}

    class FakeC:
        @staticmethod
        def calibrate(code, years, **kw):
            seen.update(kw)
            raise RuntimeError("stop")

    class FakeD:
        DataError = RuntimeError

        @staticmethod
        def validate_code(c):
            return c

    monkeypatch.setattr(dcf, "_fin_notes", lambda url, notes: {
        "nca1y_fin": None, "statutory_reserve": None, "term_restricted": {}, "encumbered": {},
        "rd_total": None, "rd_capitalized": None, "rd_cap_ratio": None})
    with pytest.raises(Exception):
        dcf._calibrate(FakeD, FakeC, "600519", AS_OF, None, 10, "http://x/a.pdf")
    assert "rd_life" not in seen and "rd_disclosure" not in seen


# ── 终审 I2：研发投入表解析的健壮性 ─────────────────────────────

_SH_COLMAJOR = """（四）研发投入情况表
单位：元
本期费用化研发投入
本期资本化研发投入
研发投入合计
研发投入总额占营业收入比例（%）
研发投入资本化的比重（%）
465,000,000.00
35,000,000.00
500,000,000.00
6.20
7.00
"""

_MIXED_UNIT = """五、主要子公司 单位：万元 营业收入 12,345.67
4、研发投入
研发投入金额（元）
800,000,000.00
研发投入资本化的金额（元）
80,000,000.00
资本化研发投入占研发投入的比例
10.00%
"""

_BAD_RATIO = """研发投入金额（元）
100,000,000.00
研发投入资本化的金额（元）
20,000,000.00
资本化研发投入占研发投入的比例
35.00%
"""


def test_I2_沪市按列排布也读对():
    from ir_agent.sources.annual_notes import extract_fin_notes
    n = extract_fin_notes([_SH_COLMAJOR])
    assert n.rd_total == Decimal("500000000.00") and n.rd_capitalized == Decimal("35000000.00")
    assert n.rd_cap_ratio == Decimal("0.07")


def test_I2_标签自带元时不被同页万元带偏():
    from ir_agent.sources.annual_notes import extract_fin_notes
    n = extract_fin_notes([_MIXED_UNIT])
    assert n.rd_total == Decimal("800000000.00") and n.rd_capitalized == Decimal("80000000.00")
    assert n.rd_cap_ratio == Decimal("0.1")


def test_I2_比例与金额对不上时整组丢弃并说明():
    from ir_agent.sources.annual_notes import extract_fin_notes
    n = extract_fin_notes([_BAD_RATIO])
    assert n.rd_total is None and n.rd_capitalized is None and n.rd_cap_ratio is None
    assert "研发投入表" in n.rd_note and "不一致" in n.rd_note


# ── 终审 I3：提示进 DCF 说明；摘要行常显（年限、完整性用占位符与中文） ──────────

def test_I3_摘要行常显年限与完整性():
    import dataclasses
    from ir_agent import dcf
    from ir_agent.citation import REF_RE
    kw = {f.name: (f.default if f.default is not dataclasses.MISSING else
                   (f.default_factory() if f.default_factory is not dataclasses.MISSING else Decimal(0)))
          for f in dataclasses.fields(dcf.DcfResult)}
    kw.update(code="600276", workbook="w.xlsx", as_of=AS_OF, rd_life=Decimal(10), rd_complete=False,
              scenario_ps={}, mc=None, verified=True)
    res = dcf.DcfResult(**kw)
    s = res.summary()
    assert "[[valuation.rd_life@2026-11-20]]" in s and "不足" in s
    from ir_agent.citation import audit_bare_numbers
    line = next(x for x in s.split("\n") if "研发" in x)
    assert audit_bare_numbers(line) == []
    facts = {f.key: f for f in dcf.to_facts(res, AS_OF)}
    assert facts["valuation.rd_life"].unit == "年"


def test_I3_校准提示进入DCF说明(monkeypatch):
    from ir_agent import dcf

    class FakeC:
        @staticmethod
        def calibrate(code, years, **kw):
            return {"rdcap": {"notes": ["资产负债表缺「开发支出」，按 0 计入投入资本（ROIC 可能偏高）"]}}

    class FakeD:
        DataError = RuntimeError

        @staticmethod
        def validate_code(c):
            return c
    cal, notes, _ = dcf._calibrate(FakeD, FakeC, "600276", AS_OF, None, 10)
    assert any("开发支出" in n for n in notes)
