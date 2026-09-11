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



class TestNothingIsSilentlyOmitted:
    def test_skipped_reconciliation_checks_are_listed(self, tmp_path, run_obj):
        h = html(tmp_path, run_obj)
        assert "毛利" in h            # 该标的无 gross_profit，勾稽跳过

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



def test_分析口径进看板且在正文之前(tmp_path, run_obj):
    """读者应当先知道结论建立在什么样本上，再读结论本身。"""
    out = html(tmp_path, run_obj,
               context={"行业": "覆盖 12/18 家（67%）\n  ⚠ 低于 70%",
                        "宏观": "名义 GDP 同比 5.43%"})
    assert "分析口径" in out
    assert "覆盖 12/18 家" in out and "名义 GDP 同比" in out
    assert out.index("分析口径") < out.index("研究正文")


def test_口径为空时不留空壳(tmp_path, run_obj):
    out = html(tmp_path, run_obj, context={"行业": "", "宏观": ""})
    assert "<pre" not in out.split("研究正文")[0]


def test_无口径参数时行为不变(tmp_path, run_obj):
    assert "分析口径" not in html(tmp_path, run_obj)




# ── 估值改由 ashare-dcf 承担 ─────────────────────────────────

class _Dcf:
    """最小 DcfResult 替身。"""
    code = "600584"; workbook = "/tmp/x.xlsx"
    price = D("38.00"); value_per_share = D("45.60")
    wacc = D("0.0912"); terminal_roic = D("0.11")
    reinvest_rate = D("0.42"); terminal_share = D("0.68")

    def __init__(self, verified=True, crosscheck=(), warnings=(), notes=()):
        self.verified = verified
        self.crosscheck = list(crosscheck)
        self.warnings = list(warnings)
        self.notes = list(notes)

    @property
    def upside(self):
        return self.value_per_share / self.price - 1


def test_估值卡片给出价值现价与空间(tmp_path, run_obj):
    out = html(tmp_path, run_obj, dcf=_Dcf())
    assert "45.60" in out and "38.00" in out and "+20.0%" in out
    assert "终值占企业价值 68.0%" in out


def test_验算未通过要醒目(tmp_path, run_obj):
    """**一份没通过公式引擎验算的工作簿看起来和通过的一模一样** ——
    数字都在格子里。因此必须在指示灯上一眼可辨。"""
    out = html(tmp_path, run_obj, dcf=_Dcf(verified=False))
    assert "验算未通过" in out and "不可采信" in out


def test_与账本对不上不折叠(tmp_path, run_obj):
    """交叉核对与健全性警告正是「看起来算出来了」与「算对了」的差别。"""
    out = html(tmp_path, run_obj,
               dcf=_Dcf(crosscheck=["营业收入@2025FY 账本 979 亿 vs 模型 950 亿"]))
    assert "与账本对不上" in out and "979" in out
    assert "对不上" in out.split("<details")[0], "不该藏在折叠区里"


def test_健全性警告原样呈现(tmp_path, run_obj):
    out = html(tmp_path, run_obj, dcf=_Dcf(warnings=["终值占 EV 过高"]))
    assert "终值占 EV 过高" in out


def test_没有估值时不出空的估值区(tmp_path, run_obj):
    assert "ashare-dcf" not in html(tmp_path, run_obj)
