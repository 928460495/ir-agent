"""通用 Markdown 规整（spec 2026-11-25-readable-layout §4.1）。"""

from __future__ import annotations

import pytest

from ir_agent.mdview import normalize


@pytest.mark.parametrize("src", ["**一、护城河**\n正文", "## 一、护城河\n正文", "【护城河】\n正文"])
def test_三种小标题归一(src):
    lines = normalize(src).split("\n")
    assert lines[0].startswith("## ") and "护城河" in lines[0] and lines[1] == "" and lines[2] == "正文"


def test_长粗体与整句粗体不当标题():
    assert not normalize("**这是一句完整的话。**").startswith("#")
    assert not normalize("**" + "长" * 31 + "**").startswith("#")


def test_标题级别保持相对高低():
    out = normalize("# 总\n## 分\n### 细\n文")
    assert [l.split()[0] for l in out.split("\n") if l.startswith("#")] == ["#", "##", "###"]


def test_相邻行各成一段():
    assert normalize("甲\n乙\n丙") == "甲\n\n乙\n\n丙\n"


def test_列表保持连续且前后空行():
    assert normalize("甲\n- 一\n- 二\n乙") == "甲\n\n- 一\n- 二\n\n乙\n"


def test_缩进的点号条目成列表():
    assert normalize("  · 需求：多\n  · 价格：空") == "- 需求：多\n- 价格：空\n"


def test_审核条目拆开():
    out = normalize("- [五力] 供应商：提醒　证据不足（⚠ 引文「x」在语料里找不到）　建议：补证据")
    assert "- **[五力] 供应商**：提醒" in out
    for x in ("  - 理由：证据不足", "  - 引文核对：⚠ 引文「x」在语料里找不到", "  - 建议：补证据"):
        assert x in out


def test_审核条目没有理由或建议时不造():
    out = normalize("- [五力] 供应商：通过")
    assert out == "- **[五力] 供应商**：通过\n"


def test_分隔线与包字段行的围栏去掉_其他围栏保留():
    assert "---" not in normalize("甲\n\n---\n\n乙")
    assert normalize("```\n环节: 上游 | 晶圆\n```").strip() == "环节: 上游 | 晶圆"
    assert "```" in normalize("```python\nx = 1\n```")
    assert normalize("`政策取向: 收紧 | 理由`").strip() == "政策取向: 收紧 | 理由"


def test_没收尾的围栏不吞后文():
    assert "乙" in normalize("```\n甲\n\n乙")


def test_表格原样且前后空行():
    t = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert normalize("甲\n" + t + "\n乙") == f"甲\n\n{t}\n\n乙\n"


def test_引用块连续():
    assert normalize("甲\n> 一\n> 二\n乙") == "甲\n\n> 一\n> 二\n\n乙\n"


def test_幂等():
    s = "**一、x**\n甲\n- 一\n| a |\n| --- |\n| 1 |\n> 引\n- [五力] 供应商：提醒　证据不足　建议：补"
    assert normalize(normalize(s)) == normalize(s)


def test_空文本():
    assert normalize("") == "" and normalize("\n\n") == ""
