"""交互式假设审核 —— 把人从「填对 YAML 格式」里解放出来。

审核该花的力气在「这个假设合不合理」，不在缩进和日期格式。因此:

  · **逐项即时校验**，错了当场重问 —— 全部填完再一起报错，人得从头来过
  · **就地给上下文**（历史增速、当前口径），判断需要参照物
  · **写出的文件必须能被解析层读回**，否则交互只是换了个地方出错
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.review import ReviewAborted, interactive_review

D = Decimal


def scripted(*answers):
    it = iter(answers)

    def ask(_prompt: str) -> str:
        return next(it)
    return ask


# 一次完整的正常填写: WACC → 依据 → 永续增长 → 依据 → 5 年增速 → 依据
FULL = (
    "0.09", "1", "https://yield.chinabond.com.cn/", "十年期国债 2.50%", "2026-09-06",
    "0.025", "1", "https://stats.gov.cn/", "名义 GDP 约 5%", "2026-09-06",
    "0.02", "0.03", "0.04", "0.04", "0.04",
    "2", "[[revenue.yoy@2025FY]]",
)


class TestCollectsValues:
    def test_wacc(self):
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        assert a.wacc == D("0.09")

    def test_terminal_growth(self):
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        assert a.terminal_growth == D("0.025")

    def test_growth_path_in_order(self):
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        assert a.growth_rates == [D("0.02"), D("0.03"), D("0.04"),
                                  D("0.04"), D("0.04")]

    def test_external_basis_captured(self):
        from ir_agent.valuation.basis import BasisKind
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        b = a.basis["wacc"][0]
        assert b.kind is BasisKind.EXTERNAL and b.retrieved == date(2026, 9, 6)

    def test_fact_basis_captured(self):
        from ir_agent.valuation.basis import BasisKind
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        assert a.basis["growth_rates"][0].kind is BasisKind.FACT

    def test_result_is_not_yet_approved(self):
        """填完不等于审核通过 —— 依据仍要对着账本与正文核验。"""
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        assert a.approved is False


class TestImmediateValidation:
    def test_non_numeric_wacc_is_reasked(self):
        answers = ("abc", *FULL)
        a = interactive_review(prompt=scripted(*answers), show=lambda *_: None)
        assert a.wacc == D("0.09")

    def test_terminal_growth_above_wacc_is_reasked(self):
        """在输入当场拦住，而不是等构造对象时抛异常 —— 后者得重填全部。"""
        answers = ("0.09", "1", "https://a.cn/", "国债", "2026-09-06",
                   "0.12",                      # 高于 WACC，应被当场退回
                   "0.025", "1", "https://b.cn/", "GDP", "2026-09-06",
                   "0.02", "0.03", "0.04", "0.04", "0.04",
                   "2", "[[revenue.yoy@2025FY]]")
        a = interactive_review(prompt=scripted(*answers), show=lambda *_: None)
        assert a.terminal_growth == D("0.025")

    def test_bad_date_is_reasked(self):
        answers = ("0.09", "1", "https://a.cn/", "国债",
                   "2026/09/06",                # 格式错，应被退回
                   "2026-09-06",
                   "0.025", "1", "https://b.cn/", "GDP", "2026-09-06",
                   "0.02", "0.03", "0.04", "0.04", "0.04",
                   "2", "[[revenue.yoy@2025FY]]")
        a = interactive_review(prompt=scripted(*answers), show=lambda *_: None)
        assert a.basis["wacc"][0].retrieved == date(2026, 9, 6)

    def test_bad_placeholder_is_reasked(self):
        answers = (*FULL[:-1], "revenue.yoy@2025FY", "[[revenue.yoy@2025FY]]")
        a = interactive_review(prompt=scripted(*answers), show=lambda *_: None)
        assert a.basis["growth_rates"][0].ref == "[[revenue.yoy@2025FY]]"


class TestAbort:
    def test_quit_raises(self):
        with pytest.raises(ReviewAborted):
            interactive_review(prompt=scripted("q"), show=lambda *_: None)


class TestRoundTrip:
    def test_written_file_loads_back(self, tmp_path):
        """交互只是换个地方出错的话就白做了 —— 产物必须能被解析层读回。"""
        from ir_agent.assumptions_io import load_assumptions
        from ir_agent.review import to_yaml
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        p = tmp_path / "a.yaml"
        p.write_text(to_yaml(a), encoding="utf-8")
        back = load_assumptions(p)
        assert back.wacc == a.wacc
        assert back.growth_rates == a.growth_rates

    def test_round_trip_preserves_basis_kinds(self, tmp_path):
        from ir_agent.assumptions_io import load_assumptions
        from ir_agent.review import to_yaml
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        p = tmp_path / "a.yaml"
        p.write_text(to_yaml(a), encoding="utf-8")
        back = load_assumptions(p)
        assert [b.kind for b in back.basis["wacc"]] == \
               [b.kind for b in a.basis["wacc"]]

    def test_round_trip_preserves_retrieval_date(self, tmp_path):
        from ir_agent.assumptions_io import load_assumptions
        from ir_agent.review import to_yaml
        a = interactive_review(prompt=scripted(*FULL), show=lambda *_: None)
        p = tmp_path / "a.yaml"
        p.write_text(to_yaml(a), encoding="utf-8")
        assert load_assumptions(p).basis["wacc"][0].retrieved == date(2026, 9, 6)


class TestContextIsShown:
    def test_history_is_offered_as_a_reference(self):
        """判断增速合不合理需要参照物。"""
        shown = []
        interactive_review(prompt=scripted(*FULL), show=shown.append,
                           context={"历史营收增速": "-1.21%"})
        assert any("-1.21%" in str(s) for s in shown)


class TestFieldsAreValidatedOnEntry:
    """URL 必须**输入当场**校验，不能等三个字段都收完再一起检查 ——
    后者会在最后抛异常并丢掉已填的内容。实测用户把「国债收益率页面、
    原文摘录、日期」整段贴进 URL 格，程序在收完日期后才崩。
    """

    def test_bad_url_is_reasked_immediately(self):
        answers = ("0.09", "1",
                   "国债收益率页面、原文摘录、日期 2026-09-07",   # 不是 URL
                   "https://yield.chinabond.com.cn/",
                   "十年期国债 2.50%", "2026-09-06",
                   "0.025", "1", "https://stats.gov.cn/", "GDP", "2026-09-06",
                   "0.02", "0.03", "0.04", "0.04", "0.04",
                   "2", "[[revenue.yoy@2025FY]]")
        a = interactive_review(prompt=scripted(*answers), show=lambda *_: None)
        assert a.basis["wacc"][0].ref == "https://yield.chinabond.com.cn/"

    def test_the_error_message_names_the_problem(self):
        shown = []
        answers = ("0.09", "1", "不是网址", "https://a.cn/", "摘录", "2026-09-06",
                   "0.025", "1", "https://b.cn/", "GDP", "2026-09-06",
                   "0.02", "0.03", "0.04", "0.04", "0.04",
                   "2", "[[revenue.yoy@2025FY]]")
        interactive_review(prompt=scripted(*answers), show=shown.append)
        assert any("URL" in str(s) for s in shown)

    def test_blank_quote_is_reasked(self):
        answers = ("0.09", "1", "https://a.cn/", "   ", "有效摘录", "2026-09-06",
                   "0.025", "1", "https://b.cn/", "GDP", "2026-09-06",
                   "0.02", "0.03", "0.04", "0.04", "0.04",
                   "2", "[[revenue.yoy@2025FY]]")
        a = interactive_review(prompt=scripted(*answers), show=lambda *_: None)
        assert a.basis["wacc"][0].quote == "有效摘录"

    def test_blank_report_quote_is_reasked(self):
        answers = (*FULL[:15], "3", "  ", "毛利率维持在高位")
        a = interactive_review(prompt=scripted(*answers), show=lambda *_: None)
        assert a.basis["growth_rates"][0].quote == "毛利率维持在高位"
