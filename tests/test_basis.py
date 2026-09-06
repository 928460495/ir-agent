"""假设依据: 从自由文本改为结构化引用。

原设计要求每项假设填 `basis` 字符串，但自由文本让审核门形同虚设 ——
可以写「量价承压期后回归个位数增长」，听起来有依据，却不指向任何东西，
审核者除了凭感觉点头做不了别的。

改为三类**可核验**的引用:
  · FACT     —— 指向事实账本，用占位符寻址，必须解析得到
  · REPORT   —— 指向已经过辩论的正文，引文必须真的出现在正文里
  · EXTERNAL —— 外部来源，必须同时有 URL、原文片段与抓取日期

WACC 的无风险利率、ERP、β 本来就不在账本里，EXTERNAL 是它们的正当出口 ——
但要付出留下可核验痕迹的代价，而不是随手写一句话。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.valuation.basis import Basis, BasisKind, InvalidBasis, validate

D = Decimal
AS_OF = date(2026, 6, 1)
BODY = "## 盈利质量\n公司几乎不依赖财务杠杆，权益乘数仅 1.24x。\n"


def ledger():
    from ir_agent.ledger import Fact, FactLedger, Method
    l = FactLedger()
    l.put(Fact(key="revenue.yoy", value=D("-0.0121"), unit="ratio",
               currency=None, period="2025FY", as_of=date(2026, 4, 20),
               source_id="calc_x", method=Method.COMPUTED))
    return l


class TestConstruction:
    def test_fact_basis_needs_a_placeholder(self):
        with pytest.raises(InvalidBasis, match="占位符"):
            Basis.fact("revenue.yoy@2025FY")          # 缺方括号

    def test_external_basis_needs_a_url(self):
        with pytest.raises(InvalidBasis, match="URL"):
            Basis.external("十年期国债", "收益率 2.5%", date(2026, 6, 1))

    def test_external_basis_needs_a_quote(self):
        with pytest.raises(InvalidBasis, match="原文"):
            Basis.external("https://x.cn/a", "  ", date(2026, 6, 1))

    def test_external_basis_needs_a_retrieval_date(self):
        """外部资料会变。没有抓取日期，事后无法判断当时看到的是什么。"""
        with pytest.raises(InvalidBasis, match="抓取日期"):
            Basis.external("https://x.cn/a", "收益率 2.5%", None)

    def test_report_basis_needs_a_quote(self):
        with pytest.raises(InvalidBasis, match="引文"):
            Basis.report("   ")

    def test_valid_bases_construct(self):
        assert Basis.fact("[[revenue.yoy@2025FY]]").kind is BasisKind.FACT
        assert Basis.report("公司几乎不依赖财务杠杆").kind is BasisKind.REPORT
        assert Basis.external("https://x.cn/a", "收益率 2.5%",
                              date(2026, 6, 1)).kind is BasisKind.EXTERNAL


class TestValidateAgainstEvidence:
    def test_fact_reference_must_resolve(self):
        bad = [Basis.fact("[[ebitda@2025FY]]")]
        assert validate(bad, ledger(), BODY, AS_OF)

    def test_resolvable_fact_reference_passes(self):
        assert validate([Basis.fact("[[revenue.yoy@2025FY]]")],
                        ledger(), BODY, AS_OF) == []

    def test_report_quote_must_appear_in_the_body(self):
        """引用一句正文里没有的话，与凭空捏造无异。"""
        probs = validate([Basis.report("公司正在高速扩张")],
                         ledger(), BODY, AS_OF)
        assert probs and "正文" in probs[0]

    def test_report_quote_present_passes(self):
        assert validate([Basis.report("几乎不依赖财务杠杆")],
                        ledger(), BODY, AS_OF) == []

    def test_whitespace_differences_are_tolerated_in_quotes(self):
        assert validate([Basis.report("几乎 不依赖 财务杠杆")],
                        ledger(), BODY, AS_OF) == []

    def test_external_reference_is_accepted_as_is(self):
        """外部来源无法在本地核验，但留下了 URL、原文与日期可供人复核。"""
        assert validate([Basis.external("https://x.cn/a", "收益率 2.5%",
                                        date(2026, 6, 1))],
                        ledger(), BODY, AS_OF) == []

    def test_empty_basis_list_is_a_problem(self):
        assert validate([], ledger(), BODY, AS_OF)


class TestApprovalRequiresValidBasis:
    def _a(self, basis):
        from ir_agent.valuation.assumptions import Assumptions
        return Assumptions(
            wacc=D("0.09"), terminal_growth=D("0.025"),
            growth_rates=[D("0.04")] * 5, basis=basis)

    def _good(self):
        return {"wacc": [Basis.external("https://x.cn/gz", "十年期国债 2.5%",
                                        date(2026, 6, 1))],
                "terminal_growth": [Basis.external("https://x.cn/gdp",
                                                   "名义 GDP 增速 5%",
                                                   date(2026, 6, 1))],
                "growth_rates": [Basis.fact("[[revenue.yoy@2025FY]]")]}

    def test_cannot_approve_with_an_unresolvable_fact_reference(self):
        from ir_agent.valuation.assumptions import UnsupportedAssumption
        b = self._good(); b["growth_rates"] = [Basis.fact("[[ebitda@2025FY]]")]
        with pytest.raises(UnsupportedAssumption, match="ebitda"):
            self._a(b).approve("谢海量", AS_OF, ledger=ledger(),
                               body=BODY, as_of=AS_OF)

    def test_cannot_approve_with_a_fabricated_report_quote(self):
        from ir_agent.valuation.assumptions import UnsupportedAssumption
        b = self._good(); b["wacc"] = [Basis.report("公司正在高速扩张")]
        with pytest.raises(UnsupportedAssumption, match="正文"):
            self._a(b).approve("谢海量", AS_OF, ledger=ledger(),
                               body=BODY, as_of=AS_OF)

    def test_valid_basis_approves(self):
        a = self._a(self._good()).approve("谢海量", AS_OF, ledger=ledger(),
                                          body=BODY, as_of=AS_OF)
        assert a.approved

    def test_approval_without_evidence_is_refused(self):
        """不给证据就不能审核 —— 否则契约又退回成走过场。"""
        from ir_agent.valuation.assumptions import UnsupportedAssumption
        with pytest.raises(UnsupportedAssumption, match="证据"):
            self._a(self._good()).approve("谢海量", AS_OF)

    def test_revision_still_revokes_approval(self):
        a = self._a(self._good()).approve("谢海量", AS_OF, ledger=ledger(),
                                          body=BODY, as_of=AS_OF)
        nb = {"wacc": [Basis.external("https://x.cn/b", "β 0.9",
                                      date(2026, 6, 1))]}
        assert a.revise(basis_note="下调 β", wacc=D("0.08"),
                        basis=nb).approved is False
