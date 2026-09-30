"""公司公告入语料库。**模型只挑下标，正文由代码抓。**

这个契约比 openweb 的 discover/fetch 更强：openweb 里模型给的是 URL
（一个它可以凭空构造的字符串），这里模型给的是指向代码已有列表的下标，
可编造的空间从「任意网址」缩到「这些条目里的哪几条」。
"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent.sources.announce import (MAX_ANNOUNCEMENTS, PROMPT,
                                       format_candidates, parse_picks)
from ir_agent.sources.cninfo import Announcement

AS_OF = date(2026, 9, 30)


def _ann(i: int, title: str, when: date) -> Announcement:
    return Announcement(
        ann_id=f"a{i}", title=title, ann_date=when,
        url=f"http://static.cninfo.com.cn/finalpage/{when}/a{i}.PDF",
        code="600183", name="生益科技", category="其他",
        source_id=f"src{i}")


ANNS = [
    _ann(0, "生益科技关于高频高速覆铜板扩产项目投产的公告", date(2026, 8, 1)),
    _ann(1, "生益科技2026年半年度报告", date(2026, 8, 28)),
    _ann(2, "生益科技关于部分产品价格调整的公告", date(2026, 5, 20)),
]


def test_候选清单带序号标题与披露日():
    text = format_candidates(ANNS)
    assert "0" in text and "1" in text and "2" in text
    assert "高频高速覆铜板扩产项目投产" in text
    assert "2026-08-01" in text


def test_候选清单不含下载地址():
    """正文的下载地址由代码按 ann_id 取，不经模型——清单里给了 URL 反而
    让模型有机会把它改掉再交回来。"""
    text = format_candidates(ANNS)
    for a in ANNS:
        assert a.url not in text


def test_解析挑选出的下标():
    picks, ignored = parse_picks(
        "选取: 0 | 与名义产能相关\n选取: 2 | 与ASP相关\n", 3)
    assert picks == [0, 2]
    assert ignored == []


def test_越界的下标被忽略并记录():
    picks, ignored = parse_picks("选取: 0\n选取: 99\n", 3)
    assert picks == [0]
    assert len(ignored) == 1 and "99" in ignored[0]


def test_重复的下标只取一次():
    picks, ignored = parse_picks("选取: 1\n选取: 1\n选取: 1\n", 3)
    assert picks == [1]


def test_非数字的下标被忽略并记录():
    picks, ignored = parse_picks("选取: 第一条 | 相关\n选取: 2\n", 3)
    assert picks == [2]
    assert len(ignored) == 1


def test_超过上限只取前若干条():
    lines = "".join(f"选取: {i}\n" for i in range(20))
    picks, _ = parse_picks(lines, 20)
    assert len(picks) == MAX_ANNOUNCEMENTS


def test_负数下标被忽略():
    """`-1` 在 Python 里是合法索引，会静默取到最后一条——不是模型的意思。"""
    picks, ignored = parse_picks("选取: -1\n选取: 0\n", 3)
    assert picks == [0]
    assert len(ignored) == 1


def test_容忍列表前缀与加粗():
    picks, _ = parse_picks("1. **选取**: 2 | 相关\n", 3)
    assert picks == [2]


def test_一条都没挑出来时返回空():
    picks, ignored = parse_picks("我觉得都不相关。\n", 3)
    assert picks == [] and ignored == []


def test_提示词说明只给下标且逐字含上限():
    assert "序号" in PROMPT
    assert str(MAX_ANNOUNCEMENTS) in PROMPT
    assert "选取:" in PROMPT
