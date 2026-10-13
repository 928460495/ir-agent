"""因子联网结论（spec 2026-10-13 ②）。

样本是实验② 的真实输出与代码抓回的原页正文：
`tests/fixtures/factor_web_experiment_600183.json`。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ir_agent.sources import conclusions as C
from ir_agent.sources.conclusions import (Article, Conclusion, number_marks,
                                          open_articles, parse)
from ir_agent.sources.external import Tier
from ir_agent.sources.openweb import Fetched, FetchFailed, tier_of_url

AS_OF = date(2026, 10, 13)
FX = json.loads((Path(__file__).parent / "fixtures"
                 / "factor_web_experiment_600183.json").read_text(encoding="utf-8"))
SINA = "https://finance.sina.com.cn/wm/2026-06-17/doc-inicthnr3825185.shtml"
JIEPEI = "https://www.jiepei.com/design/7573.html"


def _opened(no: str, text: str) -> Article:
    return Article(no=no, url=f"https://x/{no}", publisher="某媒体",
                   published=date(2026, 6, 17), final_url=f"https://x/{no}",
                   opened=True, text=text)


class TestParse:
    def test_一条结论引多篇文章(self):
        got = parse("文章: 1 | https://a.cn/1 | 甲 | 2026-07-01\n"
                    "文章: 2 | https://b.cn/2 | 乙 | 2026-08-01\n"
                    "结论: k | 稼动率约78% | 依据 1,2\n", AS_OF)
        assert [a.no for a in got.items[0].articles] == ["1", "2"]
        assert got.items[0].claim == "稼动率约78%"

    def test_晚于as_of的文章剔除_部分剔除的结论保留并注明(self):
        got = parse("文章: 1 | https://a.cn/1 | 甲 | 2026-07-01\n"
                    "文章: 2 | https://b.cn/2 | 乙 | 2026-11-01\n"
                    "结论: k | x | 依据 1,2\n", AS_OF)
        c = got.items[0]
        assert [a.no for a in c.articles] == ["1"]
        assert any("晚于" in x for x in c.dropped)

    def test_引的文章全部被剔除的结论丢弃(self):
        got = parse("文章: 2 | https://b.cn/2 | 乙 | 2026-11-01\n"
                    "结论: k | x | 依据 2\n", AS_OF)
        assert got.items == []
        assert any("晚于" in x for x in got.dropped)

    def test_没有网址或日期的文章作废(self):
        got = parse("文章: 1 | 无网址 | 甲 | 2026-07-01\n"
                    "文章: 2 | https://b.cn/2 | 乙 | 2026年7月\n"
                    "结论: k | x | 依据 1,2\n", AS_OF)
        assert got.items == []
        assert any("没有网址" in x for x in got.dropped)
        assert any("发布日期" in x for x in got.dropped)

    def test_没写依据的结论作废(self):
        got = parse("文章: 1 | https://a.cn/1 | 甲 | 2026-07-01\n"
                    "结论: k | x\n", AS_OF)
        assert got.items == [] and any("没写依据" in x for x in got.dropped)

    def test_结论里自带竖线不会挤掉整条(self):
        got = parse("文章: 1 | https://a.cn/1 | 甲 | 2026-07-01\n"
                    "结论: k | 铜箔占比约40% | 玻纤布25% | 依据 1\n", AS_OF)
        assert got.items[0].claim == "铜箔占比约40%|玻纤布25%"


class TestNumberMarks:
    def test_真实样本_只有原页没有的7628被标出(self):
        """实验② 第 1 条：模型加了「7628型规格」，7628 在那一页上没有。"""
        claim = ("电子布(玻纤布)7628型规格已完成2026年内第5轮提价，均价达到"
                 "7.4元/米，较2025年第三季度低点涨幅达100%")
        c = Conclusion(factor_key="k", claim=claim,
                       articles=[_opened("1", FX["pages"][SINA])])
        c.marks = number_marks(c.claim, c.articles)
        assert c.missing_numbers() == ["7628"]

    def test_中文数字回退(self):
        a = [_opened("1", "已完成年内第五轮提价")]
        assert number_marks("第5轮", a)[0].found_in == ("1",)

    def test_中文数字不误中更大的数(self):
        assert number_marks("5家", [_opened("1", "共五十家")])[0].found_in == ()

    def test_数字边界_2025里的5不算(self):
        assert number_marks("5%", [_opened("1", "2025年")])[0].found_in == ()

    def test_全角数字与千分位(self):
        a = [_opened("1", "涨幅１００％，营收1,234亿元")]
        got = {m.number: m.found_in for m in number_marks("100%、1234亿元", a)}
        assert got == {"100": ("1",), "1234": ("1",)}

    def test_打不开的文章不参与比对(self):
        a = Article(no="1", url="u", publisher="p", published=AS_OF,
                    opened=False, text="78%")
        assert number_marks("78%", [a])[0].found_in == ()


class TestOpenArticles:
    def test_反爬挑战页标为打不开而不是结论未核实(self, monkeypatch):
        monkeypatch.setattr(C, "fetch", lambda url, **k: Fetched(
            final_url=JIEPEI, text=FX["pages"][JIEPEI], meta_published=None))
        c = Conclusion(factor_key="k", claim="上调10%", articles=[Article(
            no="1", url=JIEPEI, publisher="捷配", published=date(2026, 3, 10))])
        open_articles([c])
        a = c.articles[0]
        assert not a.opened and "没有中文" in a.why_not
        assert "代码无法打开" in c.flags()
        assert "未能核实" not in c.flags()

    def test_抓取失败记原因_分级按最终域名(self, monkeypatch):
        def fake(url, **k):
            if "bad" in url:
                raise FetchFailed("404")
            return Fetched(final_url="https://www.stats.gov.cn/x",
                           text="平均稼动率约78%", meta_published=None)
        monkeypatch.setattr(C, "fetch", fake)
        ok = Article(no="1", url="https://short.link/a", publisher="自称券商",
                     published=date(2026, 7, 1))
        bad = Article(no="2", url="https://bad.cn/b", publisher="乙",
                      published=date(2026, 7, 1))
        c = Conclusion(factor_key="k", claim="稼动率约78%", articles=[ok, bad])
        open_articles([c])
        assert ok.opened and ok.tier is tier_of_url("https://www.stats.gov.cn/x")
        assert not bad.opened and "抓取失败" in bad.why_not
        assert c.url == "https://www.stats.gov.cn/x", "主出处取第一篇打开了的"
        assert c.missing_numbers() == []

    def test_同一篇文章被两条结论引用只抓一次(self, monkeypatch):
        calls = []

        def fake(url, **k):
            calls.append(url)
            return Fetched(final_url=url, text="稼动率78%", meta_published=None)
        monkeypatch.setattr(C, "fetch", fake)
        got = parse("文章: 1 | https://a.cn/1 | 甲 | 2026-07-01\n"
                    "结论: k | 稼动率78% | 依据 1\n结论: k | 约78% | 依据 1\n", AS_OF)
        open_articles(got.items)
        assert calls == ["https://a.cn/1"]


class TestFlags:
    def test_数字找不到出处的措辞(self):
        c = Conclusion(factor_key="k", claim="7628规格涨价20%",
                       articles=[_opened("1", "涨价20%")])
        c.marks = number_marks(c.claim, c.articles)
        assert c.flags() == "依据含 1 个在引用文章里找不到出处的数字（7628）"

    def test_都有出处且都打开时没有标记(self):
        c = Conclusion(factor_key="k", claim="涨价20%",
                       articles=[_opened("1", "涨价20%")])
        c.marks = number_marks(c.claim, c.articles)
        assert c.flags() == ""


class TestSearch:
    def test_联网被禁用时抛WebUnavailable(self, monkeypatch):
        from ir_agent.sources import web
        monkeypatch.setattr(web, "WEB_CAPABLE", False)
        with pytest.raises(web.WebUnavailable):
            C.search(lambda p: "", "稼动率", "k", "封测", AS_OF)

    def test_提示词带因子与时点_结果已打开并标好数字(self, monkeypatch):
        monkeypatch.setattr(C, "fetch", lambda url, **k: Fetched(
            final_url=url, text="平均稼动率约78%", meta_published=None))
        seen = []

        def client(p):
            seen.append(p)
            return ("文章: 1 | https://a.cn/1 | 甲 | 2026-07-01\n"
                    "结论: k | 稼动率约78%，提升3个百分点 | 依据 1\n")
        got = C.search(client, "稼动率", "k", "封测", AS_OF)
        assert "稼动率" in seen[0] and "2026-10-13" in seen[0]
        assert "WebFetch" in seen[0], "要求打开文章再下结论"
        assert got.items[0].missing_numbers() == ["3"]
