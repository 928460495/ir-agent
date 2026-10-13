# -*- coding: utf-8 -*-
"""因子结构审查 —— 对分解式本身做逐项核验。

**实测事故（生益科技 600183，2026-10-06）。** 分解式是「营收 = 覆铜板销量
同比增速 × 覆铜板平均售价同比增速 × 高频高速覆铜板收入同比增速」，销量 ×
售价已经是收入，第三项把同一个量又算了一遍。中性档合成 71.75%，五年累计
8.05 倍、隐含市占率 39.2%（当前 CR5 才 47.13%）。全链路没有一道闸门检查
因子之间是否重复计算。

**与辩论的分工**: 审核者被迫逐项回答，辩论者自由寻找。同一轮里简报的辩论
提了 0 条挑战 —— 自由寻找可以什么都不找，逐项回答没有沉默的余地。这正是
本模块唯一的硬性要求（覆盖全部条目，缺项退回重写）所守的东西。
"""
import pytest

from ir_agent.drivers import Decomposition, Factor, Op
from ir_agent.factor_review import (
    Finding,
    ReviewIncomplete,
    ReviewResult,
    expected_items,
    parse,
    review,
)


def _decomp():
    return Decomposition(
        target="revenue", op=Op.MULTIPLY,
        factors=[Factor(key="vol", label="销量增速", unit="变化率"),
                 Factor(key="px", label="售价增速", unit="变化率"),
                 Factor(key="seg", label="分部收入增速", unit="变化率")])


FULL = """
[因子] 销量增速：可测　年报产销量章节有口径。
[因子] 售价增速：可测　分产品销售数据可算。
[因子] 分部收入增速：可测　分部报告有。
[正交] 销量增速 × 售价增速：独立　一个是量一个是价。
[正交] 销量增速 × 分部收入增速：重叠　分部收入里已含该产品的销量。
[正交] 售价增速 × 分部收入增速：重叠　分部收入里已含该产品的价。
[对账] revenue：矛盾　理由栏引用行业 CAGR 12.8%，合成值 71.75% 是其 5.6 倍。
"""


class TestExpectedItems:
    def test_条目清单由代码生成(self):
        """**清单由代码出，不由模型出** —— 否则模型可以靠少列条目回避问题。"""
        items = expected_items([_decomp()])
        assert ("因子", "销量增速") in items
        assert ("正交", "销量增速 × 分部收入增速") in items
        assert ("对账", "revenue") in items

    def test_三个因子要三对正交(self):
        items = expected_items([_decomp()])
        assert len([1 for k, _ in items if k == "正交"]) == 3

    def test_没有分解式就不审(self):
        assert expected_items([]) == []


class TestParse:
    def test_完整回答解析出全部条目(self):
        got = parse(FULL, expected_items([_decomp()]))
        assert len(got) == 7

    def test_缺一条就退回(self):
        """沉默不等于通过。"""
        partial = "\n".join(FULL.strip().splitlines()[:4])
        with pytest.raises(ReviewIncomplete, match="正交"):
            parse(partial, expected_items([_decomp()]))

    def test_需要人看的条目被挑出来(self):
        r = ReviewResult(findings=parse(FULL, expected_items([_decomp()])))
        assert len(r.flagged) == 3                 # 两条重叠 + 一条矛盾
        assert "重叠" in r.summary() and "矛盾" in r.summary()

    def test_认不出来的结论词算需要人看(self):
        """兜底值选最弱的那个断言 —— 与 research._mark 那次事故同一条教训:
        认不出来时不能默认通过。"""
        assert Finding("正交", "A × B", "也许吧", "含糊其辞").flagged

    def test_全部通过时摘要说清楚(self):
        ok = FULL.replace("重叠", "独立").replace("矛盾", "一致")
        r = ReviewResult(findings=parse(ok, expected_items([_decomp()])))
        assert r.flagged == [] and "全部通过" in r.summary()


class TestReview:
    def _client(self, *rs):
        seen = []

        def c(p):
            seen.append(p)
            return rs[min(len(seen) - 1, len(rs) - 1)]
        c.seen = seen
        return c

    def test_回答不完整会被退回重写(self):
        partial = "\n".join(FULL.strip().splitlines()[:3])
        c = self._client(partial, FULL)
        r = review(c, [_decomp()], checklist="（清单）")
        assert len(r.findings) == 7 and len(c.seen) == 2
        assert "不完整" in c.seen[1]

    def test_反复不完整则抛错(self):
        partial = "\n".join(FULL.strip().splitlines()[:3])
        with pytest.raises(ReviewIncomplete):
            review(self._client(partial), [_decomp()], checklist="（清单）",
                   max_attempts=2)

    def test_算术后果要进提示词(self):
        """**给审核者的输入必须比作者多**，否则它只是同一个模型换个角色再说
        一遍。真正把问题说穿的那句话是「隐含份额 39.2%，而当前 CR5 才
        47.13%」，而写出分解式的模型没看过它。"""
        c = self._client(FULL)
        review(c, [_decomp()], checklist="（清单）",
               implied="隐含份额 39.2%（当前份额 8.9%，CR5 47.1%）")
        assert "39.2%" in c.seen[0] and "作者未见过" in c.seen[0]

    def test_没有分解式时不调模型(self):
        c = self._client("不该被调用")
        assert review(c, [], checklist="x").findings == [] and c.seen == []


class TestTwoScalarMode:
    """**分解式被拒时仍要审。** 实测（生益科技 2026-10-06）: 分解式因重复
    计算被逐出，`decomps` 变空，于是结构审查与假设辩论一次都没跑 —— 而那
    正是最该看一眼的一轮。「分解式越可疑，检查越不运行」是个盲区。"""

    def test_没有分解式时按判断项出对账条目(self):
        items = expected_items([], drivers=("营收增速", "毛利率"))
        assert items == [("对账", "营收增速"), ("对账", "毛利率")]

    def test_两标量模式下只对账不谈正交(self):
        """没有因子就谈不上两两重叠 —— 强行出题会逼模型编一个结论。"""
        items = expected_items([], drivers=("营收增速", "毛利率"))
        assert not any(k == "正交" for k, _ in items)

    def test_两标量模式真的会调模型(self):
        raw = ("[对账] 营收增速：矛盾　理由栏说向行业中枢 12.8% 收敛，"
               "而给的是 20%。\n"
               "[对账] 毛利率：一致　与锚定后的首年水平吻合。")
        seen = []

        def c(p):
            seen.append(p)
            return raw

        r = review(c, [], checklist="（清单）", implied="累计 2.03 倍",
                   drivers=("营收增速", "毛利率"))
        assert len(seen) == 1 and len(r.findings) == 2
        assert len(r.flagged) == 1


def test_公式化分解式的条目按公式分组且共用因子不重复():
    from ir_agent.factor_review import expected_items
    from ir_agent.methodology import parse as parse_meth

    m = parse_meth("""模式: 水平
因子: 量 | 出货量 | 万吨 | 年报 | 出货量
因子: 价 | 单价 | 元 | 年报 | 单价
因子: 本 | 单位成本 | 元 | 年报 | 单位成本
公式: 营收 = 量 × 价
公式: 毛利率 = (价 − 本) ÷ 价
""").model
    items = expected_items(m)
    assert items.count(("因子", "单价")) == 1
    assert ("正交", "出货量 × 单价") in items and ("正交", "单价 × 单位成本") in items
    assert ("对账", "revenue") in items and ("对账", "gross_margin") in items
