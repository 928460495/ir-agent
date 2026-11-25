"""Markdown 子集 → HTML（spec 2026-11-25-readable-layout §4.1、§7）。"""

from __future__ import annotations

import re

from ir_agent.mdview import fold, to_html


def _ids(h):
    return re.findall(r'\bid="([^"]+)"', h)


def test_基本元素():
    h = to_html("## 标题\n\n**粗** `码` [链](https://a.b)\n\n- 一\n  - 二\n\n> 引\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    for x in ("<h2", "<strong>粗</strong>", "<code>码</code>", 'href="https://a.b"', "<ul>", "<blockquote>", "<th", "<td>1</td>"):
        assert x in h


def test_嵌套列表结构闭合():
    h = to_html("- 一\n  - 二\n    - 三\n- 四\n")
    assert h.count("<ul>") == h.count("</ul>") == 3 and h.count("<li>") == h.count("</li>") == 4


def test_编号列表():
    assert "<ol>" in to_html("1. 一\n2. 二\n")


def test_转义与危险链接():
    h = to_html("<script>alert(1)</script> [x](javascript:alert(1)) <img src=x onerror=y>")
    assert "<script>" not in h and "&lt;script&gt;" in h and "<img" not in h
    assert "href" not in h and "[x](javascript:alert(1))" in h


def test_裸网址成链接_中文标点不吞进去():
    h = to_html("出处：https://a.b/c?x=1&y=2（发布 2026-11-02）。")
    assert 'href="https://a.b/c?x=1&amp;y=2"' in h and "（发布 2026-11-02）。" in h


def test_残缺语法按文本显示且不吞后文():
    h = to_html("**没闭合\n\n| a | b |\n| 1 |\n\n```\n码\n\n尾段")
    assert "**没闭合" in h and "尾段" in h and "码" in h


def test_列数不齐的表格不丢格子():
    h = to_html("| a | b |\n| --- | --- |\n| 1 | 2 | 3 |\n| 4 |\n")
    for x in ("<td>1</td>", "<td>2</td>", "<td>3</td>", "<td>4</td>"):
        assert x in h


def test_脚注每次出现都可点():
    h = to_html("甲 81.72%[^1]，乙 81.72%[^1]", {"1": "营收 · 来源 SEC"})
    assert h.count('class="num"') == 2 and ">81.72%</button>" in h and h.count("营收 · 来源 SEC") == 2
    assert len(_ids(h)) == len(set(_ids(h)))


def test_脚注前是中文名时按钮只包住名称末段或角标():
    h = to_html("行业 CR5（2025FY）[^1] 为 81.72%", {"1": "t"})
    assert h.count('class="num"') == 1 and "为 81.72%" in h


def test_没有对应说明的脚注原样显示():
    assert "[^9]" in to_html("甲[^9]", {"1": "t"})


def test_多次渲染的编号不撞():
    a, b = to_html("1%[^1]", {"1": "t"}, uid="a"), to_html("1%[^1]", {"1": "t"}, uid="b")
    assert not set(_ids(a)) & set(_ids(b))


def test_标题有锚点且级别按base():
    h = to_html("## 甲\n\n### 乙\n", base=3)
    assert "<h3" in h and "<h4" in h and all(i for i in _ids(h))


def test_长名单折叠():
    assert "<details" not in fold(["x"] * 10, "剔除") and "共 11 条" in fold(["x"] * 11, "剔除")
    assert "&lt;b&gt;" in fold(["<b>"] * 2, "剔除")


def test_列表超过上限自动折叠():
    h = to_html("\n".join(f"- 剔除 {i}" for i in range(60)) + "\n", fold_over=10)
    assert "共 60 条" in h and "剔除 59" in h


# ── 目检暴露的 ─────────────────────────────────────────────────

def test_数字只与紧随的标点绑定_不把整段绑成一行():
    h = to_html("甲 1%[^1] 中间很长的一段话 2%[^2]，后面", {"1": "t", "2": "u"})
    for seg in re.findall(r'<span class="nw">(.*?)</span></span>|<span class="nw">(.*?)</span>(?=[^<]|$)', h):
        assert "中间很长" not in "".join(seg)
    assert h.count('class="nw"') == 1


def test_各种单位都进按钮():
    for shown in ("49.30 index", "50000 条路径", "1295 千套", "82 家", "-34.39%", "1.18x", "37446.58 亿美元"):
        assert f">{shown}</button>" in to_html(f"共 {shown}[^1] 等", {"1": "t"})


def test_名称末尾的数字不当数值():
    h = to_html("行业 CR5[^1] 为 81.72%", {"1": "t"})
    assert ">5</button>" not in h and "行业 CR5" in h
