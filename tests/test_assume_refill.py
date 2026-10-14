"""门③ 对账判出「矛盾」→ 退回填写者重填一次（生益科技 2026-10-14 试跑后）。

实测：毛利率两个因子单位是 pp（公式 `基期(毛利率) + (提价 + 原料) / 100`），
模型却按小数填（理由栏写「毛利率提升 5.47 个百分点」，表里填 0.0547），两个因子
对毛利率的影响缩小 100 倍。门③ 的 [对账] 判出了「矛盾」，但它只标注不阻断，
数值原样进了批准清单。用户决定：提示词讲清 pp 的写法；[对账] 判出矛盾时把
审核意见交回填写者重填一次 —— 与方法论预研「审核者给建议、提出者改」同一个闭环，
仍由模型判断，不加代码闸门。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import assume, dcf, factor_review, full
from ir_agent.ledger import FactLedger


class _Implied:
    def describe(self):
        return "预测期营收累计 3.07 倍"


def _set(base: str) -> assume.AssumptionSet:
    a = assume.AssumptionSet(code="600183", labels=["2026E"])
    a.rows = [assume.Row(driver="growth", year=1, bear=Decimal("0.02"),
                         base=Decimal(base), bull=Decimal("0.10"),
                         note=f"稿{base}", bases=[])]
    a.implied = _Implied()
    return a


def _finding(verdict: str) -> factor_review.ReviewResult:
    return factor_review.ReviewResult(findings=[factor_review.Finding(
        "对账", "gross_margin", verdict,
        "理由栏写 5.47 个百分点，表里填 0.0547" if verdict == "矛盾" else "自洽")])


class _R:
    ledger = FactLedger()
    period = "2025FY"


@pytest.fixture
def harness(monkeypatch, tmp_path):
    monkeypatch.setattr(dcf, "calibrate_only", lambda code, as_of, ledger: {
        "judg": {"g_stats": (), "gm_stats": ()}, "periods": {"proj": ["2026E"]}})
    import ir_agent.debate as D
    monkeypatch.setattr(D, "run_debate", lambda *a, **k: None)
    calls = {"ctx": [], "reviews": []}

    def run(drafts, verdicts, decide_error=None):
        drafts, verdicts = list(drafts), list(verdicts)

        def _decide(client, judg, labels, ctx, **kw):
            calls["ctx"].append(ctx)
            if decide_error and len(calls["ctx"]) > 1:
                raise decide_error
            return drafts.pop(0)

        def _review(client, decomps, checklist, **kw):
            calls["reviews"].append(checklist)
            return _finding(verdicts.pop(0))
        monkeypatch.setattr(assume, "decide", _decide)
        monkeypatch.setattr(factor_review, "review", _review)
        err = full._propose_assumptions("600183", _R(), "body", {"定性": "资料"},
                                        date(2026, 10, 14), tmp_path,
                                        lambda p: "", {})
        md = (tmp_path / full.ASSUMPTIONS_MD).read_text(encoding="utf-8")
        js = (tmp_path / full.ASSUMPTIONS_JSON).read_text(encoding="utf-8")
        return err, md, js
    return run, calls


def test_对账矛盾_退回重填一次_用重填稿(harness):
    run, calls = harness
    err, md, js = run([_set("0.20"), _set("0.15")], ["矛盾", "一致"])
    assert len(calls["ctx"]) == 2, "判出矛盾要退回重填一次"
    second = calls["ctx"][1]
    opinion = second.split("审核者意见：")[1].split("上一稿（")[0]
    assert "门③ 对账审核" in second and "表里填 0.0547" in opinion, "审核意见要交回填写者"
    assert "稿0.20" in second, "填写者要看到自己的上一稿"
    assert '"0.15"' in js and '"0.20"' not in js, "写出的是重填稿"
    assert len(calls["reviews"]) == 2, "重填稿也要再对一次账"
    assert "已退回填写者重填一次" in md and "表里填 0.0547" in md, "第一稿的意见留在清单上"
    assert "已退回重填" in err


def test_对账一致_不重填(harness):
    run, calls = harness
    _, md, _ = run([_set("0.20")], ["一致"])
    assert len(calls["ctx"]) == 1 and "重填" not in md


def test_重填后仍矛盾_不再退回_照常标注(harness):
    run, calls = harness
    _, md, js = run([_set("0.20"), _set("0.15")], ["矛盾", "矛盾"])
    assert len(calls["ctx"]) == 2, "只重填一次"
    assert '"0.15"' in js and "**矛盾**" in md


def test_重填调用失败_保留第一稿并写明(harness):
    run, calls = harness
    _, md, js = run([_set("0.20")], ["矛盾"],
                    decide_error=assume.AssumeFailed("填不出合格的判断项"))
    assert '"0.20"' in js, "重填失败不该毙掉第一稿"
    assert "重填失败" in md and "保留第一稿" in md


def test_重填稿降级成两标量_保留第一稿(harness):
    run, calls = harness
    b = _set("0.15")
    b.degraded = "因子模式连续 3 次填不出合格的判断项——已退回两标量模式"
    _, md, js = run([_set("0.20"), b], ["矛盾"])
    assert '"0.20"' in js and "保留第一稿" in md


def test_提示词讲清pp填百分点数字本身():
    """模型把 5.47 个百分点填成了 0.0547。pp 的规则原先只说「不带 %」，没说
    「填的是百分点数字本身，不是小数」。"""
    from ir_agent.methodology import parse
    m = parse("模式: 变化\n"
              "因子: 提价 | 提价对毛利率的累计贡献 | pp | 调价函 | 调价\n"
              "因子: 原料 | 原材料对毛利率的累计侵蚀 | pp | 年报 | 原材料价格\n"
              "因子: 量 | 销量增速 | 变化率 | 年报 | 销量\n"
              "因子: 价 | 单价增速 | 变化率 | 年报 | 单价\n"
              "公式: 营收 = 上期(营收) × (1 + 量) × (1 + 价)\n"
              "公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100\n").model
    head = assume._formula_head(m, {})
    assert "写 5.47，不写 0.0547" in head
