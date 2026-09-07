"""结构化行的解析。用例来自跑长电时真实卡住的那份输出。"""

from __future__ import annotations

import pytest

from ir_agent.lineparse import keyed


@pytest.mark.parametrize("line", [
    "传导: a | b | 利好 | 「x」",
    "1. 传导: a | b | 利好 | 「x」",          # 模型实际写的就是这个
    "2、传导：a | b | 利好 | 「x」",
    "  3) 传导: a | b | 利好 | 「x」",
    "- 传导: a | b | 利好 | 「x」",
    "* 传导：a | b | 利好 | 「x」",
    "**传导**: a | b | 利好 | 「x」",
    "一、传导: a | b | 利好 | 「x」",
])
def test_容忍列表前缀与加粗(line):
    """**跑长电时宏观定性连挂三次，就挂在这里。** 模型输出:

        传导路径：

        1. 传导: 7月政治局会议加大逆周期调节 | 稳增长发力 → … | 利好 | 「原文」
        2. 传导: 日本收紧光刻胶出口管制 | … | 利好 | 「原文」

    内容完全合格 —— 三条完整传导链、逐字引文俱全。但「1. 」这个前缀让行首
    不再是关键词，正则一条都匹配不上，于是报「传导路径至少 2 条」。要求
    「至少两条」时用编号列表是最自然的写法，**模型每次都这么写，于是每次
    都失败** —— 确定性的不可满足，重试多少次都一样。"""
    assert keyed("传导").search(line).group(1).startswith("a")


@pytest.mark.parametrize("line", [
    "传导路径：",                    # 小标题，没有内容
    "这里提到传导: 不该匹配",         # 关键词嵌在句中
    "传导",                         # 没有冒号
])
def test_不吃内容只吃噪声(line):
    assert keyed("传导").search(line) is None


def test_四个模块共用同一份实现():
    """一处修不如一处定义 —— 与 aggregates.ebit()、parent_first() 同理。"""
    import inspect

    from ir_agent import macro_view, strategy, value_chain
    from ir_agent.valuation import scenarios
    for m in (macro_view, strategy, value_chain, scenarios):
        assert "keyed" in inspect.getsource(m), m.__name__


def test_真实输出能解析出全部传导链():
    """把当初卡住的那份原始输出原样喂回去。"""
    from ir_agent import macro_view as MV

    raw = (
        '政策取向: 宽松 | 「7月政治局会议明确要求加大逆周期调节力度」\n'
        '\n传导路径：\n\n'
        '1. 传导: 稳增长政策发力 | 内需回补→制造业新订单回升→封测订单能见度提升'
        ' | 利好 | 「新订单指数明显回升」\n'
        '2. 传导: 日本收紧光刻胶出口管制 | 审批周期延长→国产替代加速'
        ' | 利好 | 「审批周期从30天延长至90天」\n'
        '3. 传导: 美联储偏鹰 | 全球流动性收紧→高估值板块承压'
        ' | 利空 | 「加息概率上升」\n')
    v = MV.parse(raw)
    assert v.stance is MV.Stance.EASING
    assert len(v.channels) == 3
    assert v.channels[1].impact is MV.Impact.POSITIVE
