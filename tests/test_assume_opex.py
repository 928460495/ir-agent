"""费用率分项与稳态值成为第③步判断项（2026-11-16，PLTR：费用率取近 3 年均值、不经判断，营业利润率从
FY2025 实际 31.6% 退回 19% 并五年不变）。不联网。"""

from __future__ import annotations

from decimal import Decimal as D

from ir_agent import assume, opex_judg as OJ

US_TEXT = "\n".join(
    [f"假设: 营收增速 | 第{y}年 | 10% | 20% | 30% | 理由" for y in range(1, 6)]
    + [f"假设: 毛利率 | 第{y}年 | 80% | 82% | 84% | 理由" for y in range(1, 6)]
    + [f"假设: 研发费用率 | 第{y}年 | 14% | 12% | 10% | 理由 [[revenue@2025FY]]" for y in range(1, 6)]
    + [f"假设: 销售及管理费用率 | 第{y}年 | 40% | 36% | 32% | 理由" for y in range(1, 6)]
    + ["假设: 稳态毛利率 | 稳态 | 78% | 80% | 82% | 同业成熟中位数",
       "假设: 稳态研发费用率 | 稳态 | 12% | 10% | 8% | 理由",
       "假设: 稳态销售及管理费用率 | 稳态 | 30% | 25% | 20% | 理由",
       "假设: 终值ROIC溢价 | 稳态 | 1% | 3% | 5% | 护城河"])


def test_两市场的判断科目():
    assert OJ.OPEX_ITEMS["us"] == ("研发费用率", "销售及管理费用率")
    assert OJ.OPEX_ITEMS["cn"] == ("销售费用率", "管理费用率", "研发费用率")
    assert OJ.steady_items("cn") == ("稳态毛利率", "稳态销售费用率", "稳态管理费用率", "稳态研发费用率",
                                     "终值ROIC溢价")
    assert OJ.JUDGED_YEARS == 5


def test_解析费用率与稳态行_不干扰原有两标量解析():
    a = assume.parse(US_TEXT, 5, us=True)
    assert a.market == "us"
    assert len([r for r in a.rows if r.driver == "growth"]) == 5        # 原有路径照旧
    rd = [r for r in a.opex_rows if r.driver == "opex:研发费用率"]
    assert [r.year for r in rd] == [1, 2, 3, 4, 5]
    assert (rd[0].bear, rd[0].base, rd[0].bull) == (D("0.14"), D("0.12"), D("0.10"))
    st = {r.driver: r for r in a.steady_rows}
    assert st["steady:终值ROIC溢价"].base == D("0.03") and st["steady:稳态毛利率"].year == 0
    assert not [n for n in a.read_notes if "未知的判断项" in n]


def test_缺科目或缺年份是算不下去():
    text = "\n".join(l for l in US_TEXT.splitlines() if "第3年" not in l or "研发费用率" not in l)
    text = "\n".join(l for l in text.splitlines() if "稳态销售及管理费用率" not in l)
    a = assume.parse(text, 5, us=True)
    blocking, _ = OJ.check(a)
    assert any("研发费用率" in b and "3" in b for b in blocking)
    assert any("稳态销售及管理费用率" in b for b in blocking)


def test_成本项三档方向反了是问题_不是算不下去():
    text = US_TEXT.replace("假设: 研发费用率 | 第1年 | 14% | 12% | 10%", "假设: 研发费用率 | 第1年 | 10% | 12% | 14%")
    a = assume.parse(text, 5, us=True)
    blocking, problems = OJ.check(a)
    assert not blocking
    assert any("研发费用率" in p and "第 1 年" in p and "悲观 ≥ 中性 ≥ 乐观" in p for p in problems)


def test_稳态毛利率与ROIC溢价方向按收益项():
    text = US_TEXT.replace("稳态毛利率 | 稳态 | 78% | 80% | 82%", "稳态毛利率 | 稳态 | 82% | 80% | 78%")
    _, problems = OJ.check(assume.parse(text, 5, us=True))
    assert any("稳态毛利率" in p and "悲观 ≤ 中性 ≤ 乐观" in p for p in problems)


def test_落盘往返():
    a = assume.parse(US_TEXT, 5, us=True)
    b, edits = assume.from_json(assume.to_json(a))
    assert b.market == "us" and edits == []
    assert [(r.driver, r.year, r.base) for r in b.opex_rows] == [(r.driver, r.year, r.base) for r in a.opex_rows]
    assert [(r.driver, r.base) for r in b.steady_rows] == [(r.driver, r.base) for r in a.steady_rows]


def test_人改过的费用率要报出来():
    a = assume.parse(US_TEXT, 5, us=True)
    d = assume.to_json(a)
    d["opex_rows"][0]["base"] = "0.11"
    _, edits = assume.from_json(d)
    assert edits and "研发费用率" in edits[0] and "第1年" in edits[0]


def test_写入judg的结构():
    a = assume.parse(US_TEXT, 5, us=True)
    blk = OJ.judg_block(a)
    assert blk["opex_scen"]["研发费用率"]["bear"] == [0.14] * 5
    assert blk["opex_scen"]["销售及管理费用率"]["bull"] == [0.32] * 5
    assert blk["steady"]["稳态毛利率"] == {"bull": 0.82, "base": 0.80, "bear": 0.78}
    assert blk["steady"]["终值ROIC溢价"]["base"] == 0.03


def test_A股三项():
    text = "\n".join(f"假设: {k} | 第{y}年 | 6% | 5% | 4% | 理由" for k in OJ.OPEX_ITEMS["cn"] for y in range(1, 6))
    a = assume.parse(text, 5, us=False)
    assert a.market == "cn" and len(a.opex_rows) == 15


def test_整块没填不判算不下去_但记成问题():
    text = "\n".join(l for l in US_TEXT.splitlines() if "费用率" not in l and "稳态" not in l and "ROIC" not in l)
    blocking, problems = OJ.check(assume.parse(text, 5, us=True))
    assert blocking == [] and any("没有填费用率" in p and "第④步会停下" in p for p in problems)
