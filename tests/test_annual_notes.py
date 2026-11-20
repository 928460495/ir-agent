"""年报附注：一年内到期的非流动资产明细、所有权或使用权受限资产（spec 2026-11-20，Task 2）。不联网。"""

from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

from ir_agent.sources.annual_notes import extract_fin_notes

FIX = Path(__file__).parent / "fixtures" / "notes_600519_2025.txt"


def _pages():
    return FIX.read_text(encoding="utf-8").split("<<<PAGE>>>")[1:]


def test_茅台_一年内到期中的金融部分():
    n = extract_fin_notes(_pages())
    assert n.nca1y_fin == D("26871114612.71") and n.nca1y_total == D("26871114612.71")


def test_茅台_受限资产三类():
    n = extract_fin_notes(_pages())
    assert n.statutory_reserve == D("7254499309.26")              # 法定准备金：保留
    assert n.term_restricted == {"拆出资金": D("23309929811.40")}  # 不能随时支取：保留（期限限制）
    assert n.encumbered == {} and n.missing == []                  # 质押/冻结类：无


SYN = """12、一年内到期的非流动资产
单位：万元
项目
期末余额
期初余额
一年内到期的长期应收款
1,000.00
800.00
一年内到期的大额存单
5,000.00
0.00
合计
6,000.00
800.00
<<<PAGE>>>
30、所有权或使用权受限资产
单位：万元
项目
期末
期初
账面余额
账面价值
受限类
型
受限情况
货币资金
2,345.67
2,345.67
保证金
银行承兑汇票
保证金
1,000.00
1,000.00
保证金
银行承兑汇票
保证金
固定资产
9,999.00
8,888.00
抵押
借款抵押
1.00
1.00
抵押
借款抵押
合计
"""


def test_万元换算_经营性项目不计入_质押保证金扣除():
    n = extract_fin_notes(SYN.split("<<<PAGE>>>"))
    assert n.nca1y_fin == D("50000000") and n.nca1y_total == D("60000000")     # 长期应收款不算金融
    assert n.encumbered == {"货币资金": D("23456700")}                        # 只认金融资产行；固定资产不在此列
    assert n.statutory_reserve == D("0") and n.term_restricted == {}


def test_两张表都没有时如实说缺():
    n = extract_fin_notes(["第八节 财务报告\n一、审计报告\n…"])
    assert n.nca1y_fin is None and n.statutory_reserve is None
    assert any("一年内到期的非流动资产" in m for m in n.missing) and any("受限资产" in m for m in n.missing)


def test_只交出结构化数字_不带全文():
    n = extract_fin_notes(_pages())
    assert all(len(str(v)) < 200 for v in vars(n).values())


def test_跳过正文里的交叉引用_找真正的附注表():
    """完整年报第 15 页有「详见第八节……注释27、所有权或使用权受限资产」，不是表本身。"""
    xref = "3、截至报告期末主要资产受限情况\n√适用\n详见第八节财务报告七、合并财务报表项目注释27、所有权或使用权受限资产\n(四) 行业经营性信息分析"
    n = extract_fin_notes([xref, *_pages()])
    assert n.statutory_reserve == D("7254499309.26") and n.missing == []


# ── 终审 I4：分类顺序、期初为空、准备金措辞 ──

SYN2 = """30、所有权或使用权受限资产
单位：元
项目
期末
期初
账面余额
账面价值
受限类
型
受限情况
货币资金
500.00
500.00
质押
定期存单质押用于短期借款
拆出资金
200.00
200.00
冻结
司法冻结
300.00
300.00
冻结
司法冻结
货币资金
80.00
80.00
其他
存放中央银行准备金
80.00
80.00
其他
存放中央银行准备金
合计
"""


def test_质押先判_期初为空不吞下一条_准备金不带法定二字():
    n = extract_fin_notes(["", SYN2])
    assert n.encumbered == {"货币资金": D("500.00"), "拆出资金": D("200.00")}
    assert n.statutory_reserve == D("80.00") and n.term_restricted == {}
