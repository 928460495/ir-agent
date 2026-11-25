"""历史产物验收脚本（spec 2026-11-25-readable-layout §6 V1–V3）。"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("check_views", Path(__file__).parent.parent / "scripts" / "check_views.py")
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)


def test_V1_丢了一段会报出():
    assert any("独有的一段" in x for x in cv.check_text("甲。独有的一段。乙", "# 题\n\n甲\n\n乙\n"))
    assert cv.check_text("甲一。乙二", "# 题\n\n甲一\n\n乙二\n") == []


def test_V3_没有标题或段落粘连会报出():
    assert any("标题" in x for x in cv.check_text("甲甲", "甲甲\n"))
    assert any("粘连" in x for x in cv.check_text("甲甲\n乙乙", "# 题\n\n甲甲\n乙乙\n"))


def test_V2_看板残留符号会报出():
    ok = '<section id="sec-body"><p>甲 <strong>粗</strong></p><ul><li>项</li></ul></section>'
    assert cv.check_html(ok) == []
    for bad in ("<p>**粗**</p>", "<p>- 项</p>", "<p>| 甲 | 乙 |</p>"):
        assert cv.check_html(bad)
    assert cv.check_html("<pre><code>- 代码里的不算 ** </code></pre>") == []


def test_目录验收(tmp_path):
    d = tmp_path / "600519"
    d.mkdir()
    (d / "state.json").write_text('{"period": "2025FY", "as_of": "2026-11-20", "md": {"行业": "甲\\n  · 乙"}}', encoding="utf-8")
    (d / "strategy.md").write_text("护城河: 转换成本 | 宽 | 依据一段", encoding="utf-8")
    (d / "report.md").write_text("## 投资要点\n\n**甲**：乙\n- 项", encoding="utf-8")
    assert cv.check_dir(d) == (2, 1, [])
