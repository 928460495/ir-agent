"""两段式占位符 [[key@period]] —— 分析师不该需要知道 source_id。

实跑暴露的问题: source_id 含时间戳（em_g_income_20260906030620_a627...），
既冗长又跨运行失效。分析师关心的是「引用哪个事实」，来源是账本的职责 ——
渲染时由账本解析并写进脚注，溯源一点不少。

三段式 [[source#key@period]] 继续支持: V0 的模板与既有测试都在用，
且当同一 key 有多个来源时它仍是显式指定的唯一手段。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.citation import REF_RE, UnresolvedReferenceError, render
from ir_agent.ledger import Fact, FactLedger, Method

D = Decimal
AS_OF = date(2026, 6, 1)


def led(source_id="em_x"):
    l = FactLedger()
    l.put(Fact(key="revenue", value=D("1688"), unit="元", currency="CNY",
               period="2025FY", as_of=date(2026, 4, 20),
               source_id=source_id, method=Method.REPORTED))
    return l


class TestRegexAcceptsBothForms:
    def test_three_part_form_still_matches(self):
        m = REF_RE.fullmatch("[[em_x#revenue@2025FY]]")
        assert m and m.group(2) == "revenue"

    def test_two_part_form_matches(self):
        m = REF_RE.fullmatch("[[revenue@2025FY]]")
        assert m and m.group(2) == "revenue"

    def test_two_part_form_has_no_source(self):
        assert REF_RE.fullmatch("[[revenue@2025FY]]").group(1) is None

    def test_dotted_keys_work_in_short_form(self):
        assert REF_RE.fullmatch("[[revenue.yoy@2025FY]]")

    def test_spot_period_works_in_short_form(self):
        assert REF_RE.fullmatch("[[pb@2026-09-04]]")


class TestRenderResolvesShortForm:
    def test_short_ref_renders_the_value(self):
        out, _ = render("营收 [[revenue@2025FY]]。", led(), as_of=AS_OF)
        assert "1,688.00" in out or "1688" in out

    def test_footnote_still_carries_the_source_id(self):
        """简写省的是分析师的输入，不是溯源。"""
        _, notes = render("营收 [[revenue@2025FY]]。", led("em_g_income_abc"),
                          as_of=AS_OF)
        assert "em_g_income_abc" in notes[0].text()

    def test_short_ref_to_a_missing_fact_still_raises(self):
        with pytest.raises(UnresolvedReferenceError, match="ebitda"):
            render("[[ebitda@2025FY]]", led(), as_of=AS_OF)

    def test_mixed_forms_resolve_to_one_footnote(self):
        """两种写法指向同一条事实，应当合并为一个脚注 ——
        脚注编号跟的是事实，不是写法。"""
        out, notes = render("A [[revenue@2025FY]] B [[em_x#revenue@2025FY]]",
                            led(), as_of=AS_OF)
        assert len(notes) == 1
        assert out.count("1688.00") == 2

    def test_three_part_form_still_validates_the_source(self):
        with pytest.raises(UnresolvedReferenceError, match="source_id"):
            render("[[wrong_src#revenue@2025FY]]", led(), as_of=AS_OF)
