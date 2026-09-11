"""方法论预研：先问「这个行业靠什么赚钱」，再去采集数据。"""

from __future__ import annotations

import pytest

from ir_agent.drivers import Op
from ir_agent.methodology import (Methodology, MethodologyFailed, parse,
                                  research, to_markdown)

OUT = """分解: 营收 | 乘 | 产能:变化率:年报产能与在建工程 | 稼动率:变化率:研报行业稼动率测算 | 单价:变化率:研报封测价格跟踪
分解: 毛利率 | 加 | 稼动率摊薄:pp:固定成本占比 | 折旧变动:pp:在建工程转固节奏 | 上游成本:pp:基板与金线报价
陷阱: 用周期中某一点的利润率外推永续，是封测估值最常见的误用
陷阱: 产能利用率 A 股多数封测厂不直接披露，勿用固定资产周转率代替
"""


class _Client:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


def test_解析两条分解式与陷阱():
    m = parse(OUT)
    assert len(m.decompositions) == 2
    rev = m.decompositions[0]
    assert rev.target == "revenue" and rev.op is Op.MULTIPLY
    assert [f.label for f in rev.factors] == ["产能", "稼动率", "单价"]
    assert rev.factors[0].source_hint.startswith("年报")
    assert len(m.pitfalls) == 2


def test_不闭合的分解式被驳回():
    """「客户结构优化」写不出单位，因此写不进来。"""
    bad = ("分解: 营收 | 乘 | 产能:变化率:年报 | 稼动率:变化率:研报 "
           "| 客户结构优化:定性:无\n")
    with pytest.raises(MethodologyFailed, match="单位"):
        parse(bad)


def test_pp混进乘法链被驳回():
    bad = "分解: 营收 | 乘 | 产能:变化率:年报 | 稼动率:pp:研报\n"
    with pytest.raises(MethodologyFailed, match="变化率"):
        parse(bad)


def test_两个目标都要给():
    """只给营收不给毛利率，毛利率就会沿用旧的两标量模式 ——
    半套分解比不分解更容易让人以为都分解了。"""
    only_rev = "分解: 营收 | 乘 | 产能:变化率:年报 | 单价:变化率:研报\n"
    with pytest.raises(MethodologyFailed, match="毛利率"):
        parse(only_rev)


def test_容忍列表前缀():
    """与四个定性模块同一套解析容错。"""
    m = parse("1. " + OUT.replace("\n分解", "\n2. 分解"))
    assert len(m.decompositions) == 2


def test_重试后成功():
    bad = "分解: 营收 | 乘 | 产能:定性:无 | 单价:变化率:研报\n"
    c = _Client(bad, OUT)
    m = research(c, industry="集成电路封测", code="600584")
    assert len(m.decompositions) == 2
    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]


def test_连续失败则拒绝():
    bad = "分解: 营收 | 乘 | 产能:定性:无\n"
    with pytest.raises(MethodologyFailed, match="连续"):
        research(_Client(bad, bad, bad), industry="x", code="600584")


def test_失败信息带上原始输出():
    """只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。"""
    bad = "分解: 营收 | 乘 | 产能:定性:无\n"
    with pytest.raises(MethodologyFailed) as e:
        research(_Client(bad, bad, bad), industry="x", code="600584")
    assert "产能:定性" in str(e.value)


def test_提示词要求先搜索再回答():
    c = _Client(OUT)
    research(c, industry="集成电路封测", code="600584")
    p = c.prompts[0]
    assert "集成电路封测" in p and "搜索" in p
    assert "闭合" in p and "写不出单位" in p


def test_落盘的markdown给人看得懂():
    md = to_markdown(parse(OUT))
    assert "营收 = 产能 × 稼动率 × 单价" in md
    assert "毛利率 = 基期 + 稼动率摊薄 + 折旧变动 + 上游成本" in md
    assert "年报产能与在建工程" in md          # 因子线索要留下
    assert "最常见的误用" in md                # 陷阱要留下
    assert "改这个文件后重跑" in md            # 可覆盖的说明
