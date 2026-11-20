"""看板按 Google modern-web-guidance 标准化（用户 2026-11-20）：暗色模式声明、可键盘/触屏/读屏使用的
来源提示、表格语义与窄屏滚动、地标、长表延迟渲染、相对字号与排版。不联网。"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from tests.test_dashboard import html, run_obj  # noqa: F401  (fixture)


def test_暗色模式声明color_scheme(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    assert '<meta name="color-scheme" content="light dark">' in h
    assert re.search(r":root\{[^}]*color-scheme:light dark", h)


def test_跟随系统字号_不用像素写死正文字号(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    assert '<meta name="text-scale" content="scale">' in h
    assert not re.search(r"font:\s*\d+px", h) and not re.search(r"font-size:\s*\d+px", h)


def test_数字来源是可操作的提示_不靠title(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    btns = re.findall(r'<button type="button" class="num" interestfor="(src-\d+)" '
                      r'popovertarget="\1" popovertargetaction="show" aria-expanded="false">', h)
    assert btns, h[:2000]
    for i in btns:
        tip = re.search(rf'<span id="{i}" popover="hint" role="tooltip" class="tip">([^<]*)</span>', h)
        assert tip and "来源" in tip.group(1) and "披露日" in tip.group(1)
    assert 'class="num" title=' not in h


def test_提示框的兼容脚本内联_不引外部(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    scripts = re.findall(r"<script>(.*?)</script>", h, re.S)
    assert scripts and "beforetoggle" in scripts[0] and "aria-expanded" in scripts[0]
    assert "<script src" not in h


def test_表格有表头范围标题与可滚动区域(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    assert h.count("<caption") >= 2 and "<thead>" in h and 'scope="col"' in h
    for cap in re.findall(r'<caption id="([^"]+)"', h):
        assert f'tabindex="0" role="region" aria-labelledby="{cap}"' in h


def test_地标_唯一main(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    assert h.count("<main") == 1 and "<header" in h


def test_首屏以下的溯源长表延迟渲染(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    assert re.search(r"\.defer\{content-visibility:auto;contain-intrinsic-size:auto \d+px\}", h)
    assert 'class="card defer"' in h


def test_排版_标题平衡_正文防孤字(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    assert "text-wrap:balance" in h and "text-wrap:pretty" in h


class _Check(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.ids, self.nested_btn = [], [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "button" and "button" in self.stack:
            self.nested_btn = True
        if "id" in a:
            self.ids.append(a["id"])
        if tag not in ("meta", "br", "link"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass


def test_来源文字里含其他数字时不被二次替换(tmp_path, run_obj):
    """旧写法逐个 str.replace：前一个数字的来源文字（含日期、数值）里若出现后一个数字的显示值，
    会被套进第二层标记。改为占位符一次展开。"""
    h = html(tmp_path, run_obj)
    c = _Check()
    c.feed(h)
    assert not c.nested_btn and len(c.ids) == len(set(c.ids))
    assert h.count('class="num"') == h.count('popover="hint" role="tooltip"')


def test_表头保持单行(tmp_path, run_obj):
    """浏览器实测：窄宽度下勾稽表头「结果」被挤成竖排（responsive-table：保持单元格文字单行）。"""
    assert re.search(r"\nth\{[^}]*white-space:nowrap", html(tmp_path, run_obj))


def test_数字与紧随的中文标点不断开(tmp_path, run_obj):
    """浏览器实测（375px）：「38.92 亿元」后的「，」被挤到下一行行首。"""
    h = html(tmp_path, run_obj)
    assert re.search(r'<span class="nw"><button[^>]*class="num".*?</button><span id="src-\d+"[^>]*>[^<]*</span>[，。]</span>', h), \
        re.findall(r"</span>.{0,3}", h)[:5]
    assert re.search(r"\.nw\{white-space:nowrap\}", h)
    assert re.search(r"\.tip\{[^}]*white-space:normal", h)       # 提示框在 .nw 里，不能继承 nowrap
