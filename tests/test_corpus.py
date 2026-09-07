"""语料库装配: 篇型排序、行业代码推导、一致预期的措辞。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import corpus as C
from ir_agent.sources import reports as R

D = Decimal
AS_OF = date(2026, 9, 7)


def _rep(title, pub="2026-08-01", industry="", eps=None):
    return R.Report(info_code="I" + title[:4], title=title, org="甲证券",
                    published=date.fromisoformat(pub), industry=industry,
                    eps=eps or {})


# ── 篇型排序 ─────────────────────────────────────────────────

def test_深度排在周报之前():
    """周报讲的是本周煤价涨了多少，对「产业链结构是什么」没有帮助。"""
    rs = [_rep("煤炭行业周报：动力煤大涨", "2026-09-07"),
          _rep("煤炭行业深度：产业链全景梳理", "2026-01-05")]
    assert sorted(rs, key=C._rank, reverse=True)[0].title.startswith("煤炭行业深度")


def test_只排序不剔除():
    """深度报告不足时仍会用到点评类，只是排在后面 ——
    这是分辨体裁，不是给机构打分。"""
    rs = [_rep("周报一", "2026-09-01"), _rep("周报二", "2026-09-02")]
    assert len(sorted(rs, key=C._rank, reverse=True)) == 2


def test_同型按发布日倒序():
    rs = [_rep("深度A", "2026-01-01"), _rep("深度B", "2026-08-01")]
    assert sorted(rs, key=C._rank, reverse=True)[0].title == "深度B"


# ── 行业代码推导 ─────────────────────────────────────────────

def test_从个股研报读出所属行业(monkeypatch):
    """个股研报的 industryName 是空的，行业名在 indvInduName。"""
    row = {"infoCode": "A", "title": "点评", "orgSName": "甲",
           "publishDate": "2026-08-01 00:00:00.000", "ratingChange": 3,
           "industryName": "", "indvInduName": "白酒Ⅱ"}
    r = R._row_to_report(row)
    assert r.industry == "白酒Ⅱ"
    monkeypatch.setattr(R, "industry_map", lambda: {"白酒Ⅱ": "1277"})
    assert R.industry_code_of([r]) == ("1277", "白酒Ⅱ")


def test_取出现最多的行业(monkeypatch):
    monkeypatch.setattr(R, "industry_map", lambda: {"白酒Ⅱ": "1277", "食品": "9"})
    rs = [_rep("a", industry="白酒Ⅱ"), _rep("b", industry="白酒Ⅱ"),
          _rep("c", industry="食品")]
    assert R.industry_code_of(rs)[0] == "1277"


def test_无行业信息时不猜():
    assert R.industry_code_of([_rep("a")]) == ("", "")


def test_研报行业与申万分类是两套(monkeypatch):
    """研报口径的行业只用来决定读哪个行业的研报，不参与同业集合构建 ——
    两套分类混着算行业聚合数会是实打实的错误。"""
    import inspect
    doc = inspect.getdoc(R.industry_code_of)
    assert "不参与同业集合的构建" in doc


# ── 一致预期的措辞 ───────────────────────────────────────────

def test_一致预期必须说死是对照物():
    """措辞上不说死，下游就会拿它当论据。"""
    c = R.Consensus(as_of=AS_OF, window_days=180, n_reports=3,
                    eps={2026: R.Estimate(2026, 3, D("68"), D("65"), D("72"))})
    md = C.consensus_md(c)
    assert "对照" in md and "不得作为本报告任何假设的依据" in md


def test_无跟踪时如实为空():
    """多数 A 股公司没有券商跟踪 —— 那时如实为空而非编造。"""
    assert "无从对照" in C.consensus_md(None)
    assert "无从对照" in C.consensus_md(
        R.Consensus(as_of=AS_OF, window_days=180))


def test_修正轨迹写进摘要():
    c = R.Consensus(as_of=AS_OF, window_days=180, n_reports=2,
                    eps={2026: R.Estimate(2026, 2, D("68"), D("66"), D("70"))},
                    drift={2026: (R.Estimate(2026, 5, D("85"), D("80"), D("90")),
                                  R.Estimate(2026, 2, D("68"), D("66"), D("70")))})
    md = C.consensus_md(c)
    assert "-20.0%" in md and "修正方向本身是信息" in md


def test_离散度出现在摘要():
    """券商之间谈不拢的地方，正是情景分析该展开的地方。"""
    c = R.Consensus(as_of=AS_OF, window_days=180, n_reports=3,
                    eps={2026: R.Estimate(2026, 3, D("68"), D("60"), D("80"))})
    assert "离散" in C.consensus_md(c)


# ── 装配 ─────────────────────────────────────────────────────

def test_装配时正文取回失败不丢整条(monkeypatch):
    monkeypatch.setattr(R, "search", lambda **k: [_rep("深度", industry="白酒Ⅱ")]
                        if k.get("code") else [])
    monkeypatch.setattr(R, "search_macro", lambda **k: [])
    monkeypatch.setattr(R, "fetch_text", lambda r: "")
    monkeypatch.setattr(R, "industry_map", lambda: {})
    cp, cs = C.gather(as_of=AS_OF, code="600519")
    assert len(cp.docs) == 1 and cp.docs[0].text == "深度"


def test_无券商跟踪时语料库为空而非报错(monkeypatch):
    monkeypatch.setattr(R, "search", lambda **k: [])
    monkeypatch.setattr(R, "search_macro", lambda **k: [])
    cp, cs = C.gather(as_of=AS_OF, code="000001")
    assert cp.docs == [] and cs is None
    assert "无" in cp.summary()


# ── 一致预期: 可引用，但不可作依据 ───────────────────────────

def test_一致预期落账本才能被正文引用():
    """不落账本就没法在正文里提。提示词里出现裸数字、正文里却禁止引用，
    是自相矛盾的契约。"""
    from ir_agent.ledger import Method
    c = R.Consensus(as_of=AS_OF, window_days=180, n_reports=42,
                    eps={2026: R.Estimate(2026, 42, D("68.43"), D("65.77"),
                                          D("77.75"))})
    fs = {f.key: f for f in R.consensus_facts(c)}
    assert fs["consensus.eps"].value == D("68.43")
    assert fs["consensus.brokers"].value == D("42")
    assert all(f.period == "2026FY" for f in fs.values())
    # 券商实际报出来的数字，不是估算 —— 「不该当论据」是用途限制
    assert fs["consensus.eps"].method is Method.REPORTED


def test_一致预期的as_of是分析时点():
    """窗口内的中位数只有在该时点才算得出来，用某篇研报的日期会声称
    它比实际更早可知。"""
    c = R.Consensus(as_of=AS_OF, window_days=180,
                    eps={2026: R.Estimate(2026, 3, D("68"), D("65"), D("70"))})
    assert all(f.as_of == AS_OF for f in R.consensus_facts(c))


def test_修正幅度也落账本():
    c = R.Consensus(as_of=AS_OF, window_days=180,
                    eps={2026: R.Estimate(2026, 2, D("68"), D("66"), D("70"))},
                    drift={2026: (R.Estimate(2026, 5, D("85"), D("80"), D("90")),
                                  R.Estimate(2026, 2, D("68"), D("66"), D("70")))})
    rev = next(f for f in R.consensus_facts(c)
               if f.key == "consensus.eps_revision")
    assert rev.value == (D("68") - D("85")) / D("85")


def test_一致预期不得作为估值假设的依据():
    """拿券商的 EPS 预测论证自己的增长率，结论绕一圈回到起点。"""
    from ir_agent.ledger import FactLedger
    from ir_agent.valuation.autodecide import AutoDecideFailed, check
    from ir_agent.valuation.basis import Basis

    with pytest.raises(AutoDecideFailed, match="一致预期"):
        check({"terminal_growth": D("0.02"), "wacc": D("0.09")},
              {"wacc": [Basis.report("原话")],
               "terminal_growth": [Basis.fact("[[consensus.eps@2026FY]]")]},
              ledger=FactLedger(), body="原话", as_of=AS_OF, ceiling=None)


def test_给模型的一致预期只有占位符():
    from ir_agent.citation import audit_bare_numbers
    c = R.Consensus(as_of=AS_OF, window_days=180, n_reports=42,
                    eps={2026: R.Estimate(2026, 42, D("68.43"), D("65.77"),
                                          D("77.75"))})
    assert audit_bare_numbers(C.consensus_prompt(c)) == []


def test_宏观报告也进语料库(monkeypatch):
    """GDP/CPI/PMI 这些读数早就有了，但「政策在往哪个方向走」一句都没有。"""
    monkeypatch.setattr(R, "search", lambda **k: [])
    monkeypatch.setattr(R, "search_macro",
                        lambda **k: [_rep("宏观深度：政策取向展望")])
    monkeypatch.setattr(R, "fetch_text", lambda r: "政策延续宽松")
    cp, _ = C.gather(as_of=AS_OF, code="600519")
    assert len(cp.docs) == 1 and cp.docs[0].text == "政策延续宽松"


def test_境外主题的宏观报告排在后面():
    """「美联储议息」对判断某家 A 股白酒公司帮助有限。"""
    rs = [_rep("宏观周报：美联储议息前瞻", "2026-09-07"),
          _rep("宏观深度：国内财政发力路径", "2026-09-06")]
    assert "国内财政" in sorted(rs, key=C._rank_macro, reverse=True)[0].title


def test_区域研究排在全国性宏观之后():
    """实测《海南省经济发展研究》因标题含「研究」排到了前面 ——
    它属于宏观类，但要的是全国层面的政策取向。"""
    rs = [_rep("海南省经济发展研究", "2026-09-07"),
          _rep("宏观深度：货币政策取向展望", "2026-09-01")]
    assert "货币政策" in sorted(rs, key=C._rank_macro, reverse=True)[0].title
