"""三份定性稿的原地排版（spec 2026-11-25-readable-layout §4.4）。样例是合成的，写法取自历史产物的各种形状。"""

from __future__ import annotations

import pytest

from ir_agent.fieldview import layout, lost, order_broken
from ir_agent.methodology_review import ReviewResult, Verdict

OLD4 = "传导: 加息 | 资金成本上升 → 需求走弱 | 利空 | 「利率上调至区间上沿」——央行公告"
NINE = "传导: 利率 | 加息且偏鹰 | 折现率上行 → 估值承压 | 估值 | 利空 | 中：杠杆低 | 即期 | 新增 | 「The Committee decided to raise」——FOMC《声明》（2026-09-16）"
MACRO_TAIL = "跟踪: 实际利率 | 加息且偏鹰 | 回落则不成立\n排除: 汇率 | 波动不大 [[macro.usd@2026-10]]"
STRAT = ("护城河: 转换成本 | 宽 | 生态粘性。「large installed base」——10-K\n"
         "五力: 供应商议价能力 | 高 | 对本司不利 | 依赖第三方 | 另有 | 竖线\n"
         "不确定性: 需求放缓 | revenue_growth | 下调 | 年报\n"
         "公告: 8-K 高管变动（2026-11-12） | 采用 | 影响第一条")
CHAIN = ("环节: 上游 | 晶圆 | 产能是约束 | 「wafer capacity」——某机构《文》\n环节: 上游 | 存储 | 涨价 | 「memory」——公司\n"
         "环节: 本环节 | 设计 | 平台 | 甲\n议价: 上游 | 均衡\n- 应付天数处中位\n  1. 毛利率高\n  2. 预收增加\n议价: 下游 | 强\n"
         "行业规模: 样本合计（非全行业） | 6666.24 | 亿元 | 2025FY | [[industry.revenue@2025FY]]")
SAMPLES = [
    ("macro_view", f"政策取向: 收紧 | 理由一。\n\n{OLD4}\n{MACRO_TAIL}"),
    ("macro_view", f"政策取向: 收紧 | 理由一。\n\n{NINE}\n\n{MACRO_TAIL}"),
    ("macro_view", f"**一、政策取向**\n\n政策取向: 中性 | 甲\n\n> **核对**：与利差一致\n\n**二、传导路径**\n\n{NINE}\n\n---\n\n注：\n- 卖方预测没有用"),
    ("macro_view", f"`政策取向: 收紧 | 甲`\n\n`{NINE}`\n\n**提请审核者注意**：只见于媒体"),
    ("macro_view", "```\n政策取向: 收紧 | 甲\n" + NINE + "\n```\n尾段"),
    ("macro_view", "政策取向：收紧\n传导: 利率 | 缺字段的 | 一半 | x | y\n自由段落（人工补）\n传导:只有一段"),
    ("strategy", STRAT), ("strategy", "## 一、护城河\n" + STRAT + "\n\n**说明（供审核者复核）**\n\n1. **行业：** 无据\n2. **矛盾：**\n   - 子项一\n   - 子项二"),
    ("strategy", "1. 护城河：无形资产 | 窄 | 甲\n- **五力**: 替代品威胁 | 中 | 中性 | 乙"),
    ("value_chain", CHAIN), ("value_chain", "## 一、完整产业链\n\n" + CHAIN.replace("\n", "\n\n") + "\n\n**和账本的交叉核对：**\n- **甲。** 乙"),
    ("value_chain", "完全自由的一段文字，没有任何字段。\n第二行 <b>标签</b> 与 https://a.b/c?x=1"),
    ("strategy", "护城河: 无明显护城河\n五力: | | |"),
]


@pytest.mark.parametrize("name,src", SAMPLES)
def test_一字不丢且顺序不变(name, src):
    out = layout(name, src, None)
    assert lost(src, out) == [] and order_broken(src, out) == []


@pytest.mark.parametrize("name,src", SAMPLES)
def test_带审核意见时同样不丢(name, src):
    rv = ReviewResult(verdicts=[Verdict("补充", "第 1 条", "提醒", "漏了一项", "⚠ 引文「x」找不到", "补上")])
    out = layout(name, src, rv)
    assert lost(src, out) == [] and all(x in out for x in ("漏了一项", "补上", "⚠ 引文「x」找不到"))


def test_lost能发现丢失():
    assert lost("护城河: 转换成本 | 宽 | 独有的一段话", "## 转换成本 宽") == ["独有的一段话"]


def test_结论进标题_原文成引用块():
    out = layout("strategy", "五力: 供应商议价能力 | 高 | 对本司不利 | 依赖第三方。「Dependency on suppliers.」——年报", None)
    assert "供应商议价能力 —— 高，对本司不利" in out
    assert "> 「Dependency on suppliers.」\n> —— 年报" in out


def test_连续两条引文各自成块():
    out = layout("strategy", "护城河: 转换成本 | 宽 | 甲。「first quote」——A《x》（2026-01-01）；「second quote」——B", None)
    assert "> 「first quote」\n> —— A《x》（2026-01-01）" in out and "> 「second quote」\n> —— B" in out


def test_正文里的中文引号词不拆():
    out = layout("strategy", "护城河: 转换成本 | 宽 | 判为「宽」而不是「窄」，因为有粘性", None)
    assert "判为「宽」而不是「窄」，因为有粘性" in out


def test_九段的其余字段有名字():
    out = layout("macro_view", NINE, None)
    assert "加息且偏鹰 —— 利空" in out
    for x in ("渠道", "落点", "时滞", "传导路径", "强度"):
        assert f"**{x}**" in out


def test_补充说明留在所说明的条目下面():
    out = layout("value_chain", "议价: 上游 | 均衡\n- 应付天数处中位\n议价: 下游 | 强", None).split("结论速览")[-1]
    body = out[out.index("### "):]
    assert body.index("均衡") < body.index("应付天数处中位") < body.index("强")


def test_驱动因子显示中文名并保留原词():
    out = layout("strategy", "不确定性: 需求 | revenue_growth | 下调 | 年报", None)
    assert "收入增速" in out


def _body(out):
    return out[out.index("### "):]


def test_审核意见挂到条目_对不上的在文末():
    rv = ReviewResult(verdicts=[Verdict("五力", "供应商议价能力", "提醒", "证据弱", "", "补证"),
                                Verdict("补充", "第 1 条", "提醒", "漏了关税", "", "")])
    out = _body(layout("strategy", "五力: 供应商议价能力 | 高 | 不利 | 甲\n五力: 替代品威胁 | 中 | 中性 | 乙", rv))
    assert out.index("证据弱") < out.index("替代品威胁 —— ") and out.index("其他审核意见") < out.index("漏了关税")


def test_同类多条按名称再按序号挂():
    two = NINE + "\n" + NINE.replace("加息且偏鹰", "信用收紧").replace("The Committee", "Second")
    rv = ReviewResult(verdicts=[Verdict("传导", "第2条 信用收紧", "提醒", "第二条的意见", "", ""),
                                Verdict("传导", "第1条 名字被截断过的", "提醒", "第一条的意见", "", "")])
    out = _body(layout("macro_view", two, rv))
    assert out.index("第一条的意见") < out.index("信用收紧 —— ") < out.index("第二条的意见")


def test_环节的意见挂在该位置最后一条后面():
    rv = ReviewResult(verdicts=[Verdict("环节", "上游", "提醒", "上游意见", "", ""), Verdict("议价", "对上游", "通过", "", "", "")])
    out = _body(layout("value_chain", CHAIN, rv))
    assert out.index("存储") < out.index("上游意见") < out.index("本环节") and "审核意见：通过" in out


def test_速览表只列认得的():
    assert "结论速览" in layout("strategy", "护城河: 转换成本 | 宽 | 甲", None)
    assert "结论速览" not in layout("strategy", "自由文字", None)


def test_文首说明与标题():
    out = layout("strategy", "护城河: 转换成本 | 宽 | 甲", None, note="没有审核记录", title="NVDA · 战略分析")
    assert out.startswith("# NVDA · 战略分析\n") and "> 没有审核记录" in out


def test_没有小标题时按类别补_已有时不重复补():
    assert "## 护城河" in layout("strategy", "护城河: 转换成本 | 宽 | 甲", None)
    out = layout("strategy", "**一、护城河**\n护城河: 转换成本 | 宽 | 甲", None)
    assert "## 一、护城河" in out and "## 护城河" not in out


def test_空文件():
    assert "本轮未生成" in layout("strategy", "", None)


# ── 历史产物上暴露的 ───────────────────────────────────────────

def test_依据只有引文时不变成标题():
    out = layout("macro_view", "传导: 加息 | 路径 | 利空 | 「原文一句」——央行《公告》", None)
    assert "# 依据" not in out and "依据" in out and "> 「原文一句」" in out


def test_多出来的段各自成段_不留竖线():
    out = layout("macro_view", "政策取向: 宽松 | 理由一。 | 「原文一句」——某机构《题》", None)
    assert " |\n" not in out[out.index("### "):] and "> 「原文一句」" in out


def test_审核意见里截断的占位符不影响核对():
    rv = ReviewResult(verdicts=[Verdict("补充", "第 1 条", "提醒", "看 [[macro.cpi", "引文「[[rate.ust」找不到", "改")])
    src = "政策取向: 宽松 | 理由一。\n\n**二、传导路径**\n\n传导: 加息 | 路径 | 利空 | 甲 [[x.y@2025FY]]"
    assert lost(src, layout("macro_view", src, rv)) == []


def test_速览表里的长名称缩短_正文保留全文():
    long = "很长的不确定性描述" * 8
    out = layout("strategy", f"不确定性: {long} | revenue_growth | 下调 | 年报", None)
    table, body = out.split("\n### ", 1)
    assert long not in table and "…" in table and long in body


def test_速览表不列没有结论的行():
    out = layout("macro_view", "政策取向: 收紧 | 甲\n排除: 汇率 | 波动不大\n跟踪: 指标 | 事件 | 证伪", None)
    table = out.split("\n### ")[0]
    assert "收紧" in table and "汇率" not in table and "指标" not in table


def test_都没有结论时不出速览表():
    assert "结论速览" not in layout("macro_view", "排除: 汇率 | 波动不大", None)
