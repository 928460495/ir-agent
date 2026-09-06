"""HTML 看板。

**「可互动」要用在刀刃上。** 本项目的核心主张是「每个数字可反查」，
所以最该做的交互是**点任意数字看它的来源与披露日**，其次是敏感性矩阵
（改 WACC / 永续增长看每股价值如何变）。堆一屏图表不是交互，是装饰。

三条硬约束:
  · **自包含** —— 单个 HTML 文件，无外部依赖，离线可开
  · **未审核的估值必须显著标注**，不能和已审核的长一样
  · **缺失的部分不静默省略** —— 看板上看不到的东西，读者会以为不存在
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.dashboard import build_dashboard

D = Decimal


@pytest.fixture
def run_obj():
    from ir_agent.ledger import Fact, FactLedger, Method
    from ir_agent.audit import audit
    from ir_agent.sources.snapshot import SnapshotStore
    from ir_agent.validate.reconcile import reconcile
    import tempfile

    led = FactLedger()
    for k, v, u in [("revenue", 1000, "元"), ("net_profit", 200, "元"),
                    ("total_assets", 5000, "元"), ("total_liabilities", 3000, "元"),
                    ("total_equity", 2000, "元"), ("roe", "0.18", "ratio")]:
        led.put(Fact(key=k, value=D(str(v)), unit=u,
                     currency="CNY" if u == "元" else None,
                     period="2025FY", as_of=date(2026, 4, 20),
                     source_id="em_g_x", method=Method.REPORTED))

    class R:
        code, period, as_of = "600519", "2025FY", date(2026, 6, 1)
        ledger = led
        warnings, verdicts, skips = [], {}, None
        financials = quotes = cross = None
    r = R()
    with tempfile.TemporaryDirectory() as d:
        r.store = SnapshotStore(d)
        r.reconcile = reconcile(led, "2025FY", as_of=r.as_of)
        r.audit = audit("营收 [[revenue@2025FY]]，ROE [[roe@2025FY]]。",
                        led, r.store, as_of=r.as_of)
        yield r


def html(tmp_path, run_obj, **kw) -> str:
    p = build_dashboard(run_obj, path=tmp_path / "d.html", **kw)
    return p.read_text(encoding="utf-8")


class TestSelfContained:
    def test_no_external_scripts_or_styles(self, tmp_path, run_obj):
        h = html(tmp_path, run_obj)
        assert "src=\"http" not in h and "href=\"http" not in h

    def test_is_a_complete_document(self, tmp_path, run_obj):
        h = html(tmp_path, run_obj)
        assert h.lstrip().startswith("<!doctype html") and "</html>" in h

    def test_declares_utf8(self, tmp_path, run_obj):
        assert "charset" in html(tmp_path, run_obj).lower()


class TestProvenanceIsTheInteraction:
    def test_every_footnote_source_appears(self, tmp_path, run_obj):
        assert "em_g_x" in html(tmp_path, run_obj)

    def test_disclosure_date_appears(self, tmp_path, run_obj):
        assert "2026-04-20" in html(tmp_path, run_obj)

    def test_numbers_in_the_body_carry_a_hover_source(self, tmp_path, run_obj):
        """点/悬停看来源 —— 这是本项目最该有的交互。"""
        h = html(tmp_path, run_obj)
        assert "title=" in h or "data-source" in h

    def test_traceability_rate_is_shown(self, tmp_path, run_obj):
        assert "100" in html(tmp_path, run_obj)


class TestValuationGateIsVisible:
    def _assumptions(self, approved: bool):
        from ir_agent.valuation.assumptions import Assumptions
        a = Assumptions(wacc=D("0.09"), terminal_growth=D("0.025"),
                        growth_rates=[D("0.05")] * 5,
                        basis={"wacc": "无风险利率+β×ERP",
                               "terminal_growth": "不超过名义 GDP",
                               "growth_rates": "行业分析"})
        return a.approve("谢海量", date(2026, 6, 1)) if approved else a

    def test_unreviewed_assumptions_are_flagged_prominently(self, tmp_path, run_obj):
        h = html(tmp_path, run_obj, assumptions=self._assumptions(False))
        assert "未审核" in h

    def test_reviewed_assumptions_name_the_reviewer(self, tmp_path, run_obj):
        h = html(tmp_path, run_obj, assumptions=self._assumptions(True))
        assert "谢海量" in h

    def test_assumption_basis_is_shown_not_just_the_number(self, tmp_path, run_obj):
        """只给数字不给依据，读者无从判断假设是否合理。"""
        h = html(tmp_path, run_obj, assumptions=self._assumptions(True))
        assert "无风险利率" in h

    def test_no_valuation_section_when_absent(self, tmp_path, run_obj):
        assert "每股价值" not in html(tmp_path, run_obj)


class TestNothingIsSilentlyOmitted:
    def test_skipped_reconciliation_checks_are_listed(self, tmp_path, run_obj):
        h = html(tmp_path, run_obj)
        assert "毛利" in h            # 该标的无 gross_profit，勾稽跳过

    def test_routing_reason_is_shown_when_given(self, tmp_path, run_obj):
        from ir_agent.valuation.route import Route, ValuationMethod
        rt = Route(ValuationMethod.PB_ROE, "金融业没有传统自由现金流", D("0.2"))
        assert "金融业没有传统自由现金流" in html(tmp_path, run_obj, route=rt)

    def test_warnings_surface(self, tmp_path, run_obj):
        run_obj.warnings = ["公告分页被截断"]
        assert "公告分页被截断" in html(tmp_path, run_obj)


class TestEscaping:
    def test_html_in_the_body_is_escaped(self, tmp_path, run_obj):
        from ir_agent.audit import audit
        run_obj.audit = audit("<script>alert(1)</script> 营收 [[revenue@2025FY]]。",
                              run_obj.ledger, run_obj.store, as_of=run_obj.as_of)
        h = html(tmp_path, run_obj)
        assert "<script>alert(1)</script>" not in h
        assert "&lt;script&gt;" in h
