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
        assert "popovertarget=" in h and 'popover="hint"' in h      # toggletip（2026-11-20 起不靠 title）

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



def test_正文在口径之前(tmp_path, run_obj):
    """结论在前、口径在后（用户 2026-11-25；此前口径排在前面，研究正文落在一万多像素之后）。"""
    out = html(tmp_path, run_obj,
               context={"行业": "覆盖 12/18 家（67%）\n  ⚠ 低于 70%",
                        "宏观": "名义 GDP 同比 5.43%"})
    assert "分析口径" in out
    assert "覆盖 12/18 家" in out and "名义 GDP 同比" in out and "低于 70%" in out
    assert out.index('id="sec-body"') < out.index('id="sec-context"')


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


# ── 排版（spec 2026-11-25-readable-layout §4.6）─────────────────

import re as _re


def _visible(h):
    """看得见的文字：去掉样式、脚本、来源气泡与标签。"""
    h = _re.sub(r"<style.*?</style>|<script.*?</script>|<span [^>]*role=\"tooltip\".*?</span>", "", h, flags=_re.S)
    return _re.sub(r"<[^>]+>", "\n", h)


def _audit(run_obj, text):
    from ir_agent.audit import audit
    run_obj.audit = audit(text, run_obj.ledger, run_obj.store, as_of=run_obj.as_of)


def test_正文Markdown已渲染(tmp_path, run_obj):
    _audit(run_obj, "## 投资要点\n\n**粗体** 营收 [[revenue@2025FY]]：\n- 项一\n- 项二\n\n| 甲 | 乙 |\n| --- | --- |\n| 子 | 丑 |")
    h = html(tmp_path, run_obj)
    assert "<strong>粗体</strong>" in h and "<li>项一" in h and "<td>子</td>" in h
    v = _visible(h)
    assert "**" not in v and not any(x.lstrip().startswith(("- ", "|")) for x in v.split("\n"))


def test_同一数字每次都可点(tmp_path, run_obj):
    _audit(run_obj, "营收 [[revenue@2025FY]]，再提 [[revenue@2025FY]]。")
    body = html(tmp_path, run_obj).split('id="sec-body"')[1].split("<section")[0]
    assert body.count('class="num"') == 2


def test_同值不同指标各有各的来源(tmp_path, run_obj):
    from ir_agent.ledger import Fact, Method
    run_obj.ledger.put(Fact(key="net_profit_attr_parent", value=D("200"), unit="元", currency="CNY", period="2025FY",
                            as_of=date(2026, 4, 20), source_id="em_g_x", method=Method.REPORTED))
    _audit(run_obj, "净利润 [[net_profit@2025FY]]，归母 [[net_profit_attr_parent@2025FY]]。")
    h = html(tmp_path, run_obj)
    assert "net_profit@2025FY" in h and "net_profit_attr_parent@2025FY" in h.split('id="sec-src"')[0]


def test_长名单折叠_结论仍在外面(tmp_path, run_obj):
    ctx = "行业集中度较高\n" + "\n".join(f"  – 剔除 公司{i}：非本市场" for i in range(60))
    h = html(tmp_path, run_obj, context={"行业": ctx})
    assert "共 60 条" in h and "剔除 公司59" in h
    assert h.index("行业集中度较高") < h.index("共 60 条")


def test_嵌套的长名单也折叠(tmp_path, run_obj):
    ctx = "  · 样本\n" + "\n".join(f"      – 剔除 公司{i}" for i in range(30))
    assert "共 30 条" in html(tmp_path, run_obj, context={"行业": ctx})


def test_估值标题与币种(tmp_path, run_obj):
    cn = html(tmp_path, run_obj, dcf=_Dcf())
    assert "ashare-dcf 三表模型" in cn and "45.60</b> 元" in cn


def test_关键数字按账本有无(tmp_path, run_obj):
    h = html(tmp_path, run_obj)
    box = h.split('class="figs"')[1].split("</section>")[0]
    assert box.count('class="fig"') == 2 and "营业收入" in box and "ROE" in box
    run_obj.ledger._facts.clear()
    assert 'class="figs"' not in html(tmp_path, run_obj)


def test_关键数字不提前看(tmp_path, run_obj):
    from ir_agent.ledger import Fact, Method
    run_obj.ledger.put(Fact(key="pb", value=D("9"), unit="x", period="2026-07-01", as_of=date(2026, 7, 1),
                            source_id="q", method=Method.REPORTED))          # 分析时点 2026-06-01 之后
    assert "9.00x" not in html(tmp_path, run_obj)


def test_目录锚点都存在(tmp_path, run_obj):
    _audit(run_obj, "## 投资要点\n\n营收 [[revenue@2025FY]]。\n\n## 风险提示\n\n无。")
    h = html(tmp_path, run_obj, context={"行业": "甲"}, dcf=_Dcf())
    hrefs = _re.findall(r'href="#([^"]+)"', h)
    assert len(hrefs) >= 4 and all(f'id="{x}"' in h for x in hrefs)
    ids = _re.findall(r'\bid="([^"]+)"', h)
    assert len(ids) == len(set(ids))


def test_公司名进抬头(tmp_path, run_obj):
    assert "贵州茅台 600519" in html(tmp_path, run_obj, company="贵州茅台 600519")


def test_三个定性模块用阅读版_审核意见折叠(tmp_path, run_obj):
    md = "# 战略\n\n## 护城河\n\n### 转换成本 —— 宽\n\n依据甲\n\n#### 审核意见：提醒\n\n- 理由：证据偏薄\n\n### 无形资产 —— 窄\n\n依据乙"
    h = html(tmp_path, run_obj, context={"战略": "  护城河　转换成本"}, views={"strategy": (md, {})})
    sec = h.split('id="sec-strategy"')[1].split("</section>")[0]
    assert "转换成本 —— 宽" in sec and "<summary>审核意见：提醒</summary>" in sec
    det = sec.split("<summary>审核意见：提醒</summary>")[1].split("</details>")[0]
    assert "证据偏薄" in det and "无形资产" not in det
    assert "依据乙" in sec


def test_程序核对提示不随阅读版丢失(tmp_path, run_obj):
    h = html(tmp_path, run_obj, context={"产业链": "产业链\n  ⚠ 议价与账本方向相反\n  审核遗留（x）：\n    · [议价] 下游：提醒　甲"},
             views={"value_chain": ("# 产业链\n\n### 上游 —— 强\n", {})})
    assert "议价与账本方向相反" in h


def test_定性模块没有阅读版时用原摘要_都没有就写明(tmp_path, run_obj):
    h = html(tmp_path, run_obj, context={"战略": "护城河判断原摘要", "宏观定性": "", "产业链": ""}, views={})
    assert "护城河判断原摘要" in h and "本轮未取到" in h


def test_正文不用pre(tmp_path, run_obj):
    h = html(tmp_path, run_obj, context={"行业": "甲\n  · 乙"}, dcf=_Dcf())
    assert "<pre" not in h


def test_什么都没有也能生成(tmp_path, run_obj):
    h = html(tmp_path, run_obj, context={}, views={})
    assert h.lstrip().startswith("<!doctype html") and "三表勾稽" in h


def test_关键数字宽屏四列窄屏两列(tmp_path, run_obj):
    """八格在七列宽度下排成 7+1、右边留一块灰（NVDA 2026-11-26）。窄屏规则要排在后面才生效。"""
    css = html(tmp_path, run_obj).split("<style>")[1].split("</style>")[0]
    wide, narrow = css.index("repeat(4,minmax(0,1fr))"), css.index("{.figs{grid-template-columns:repeat(2,minmax(0,1fr))")
    assert wide < narrow
