"""辩论② 看得到分解式与单位（用户 2026-11-26）。此前只传因子名，提示词还要求「数值写成 0.08 或 8%」——
两仓历史 14 个百分点因子 9 个被填成小数，公式里的 /100 再除一次，差 100 倍；也看不到分解式写明的陷阱。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from ir_agent import stance as S
from ir_agent.drivers import Factor
from ir_agent.ledger import FactLedger

D = Decimal
F = [Factor("f1", "毛利率累计变动", "pp", "8-K 毛利率说明", short="毛利变动"),
     Factor("f2", "销量同比增速", "变化率", "年报分产品表", short="销量"),
     Factor("f3", "成本转嫁比例", "比例", "", short="传导率"),
     Factor("f4", "年末产能", "万吨", "公告", short="产能")]


class _Model:
    factors = F
    constants = []

    def describe(self, target):
        return {"revenue": "营收 = 上期(营收) × (1 + 销量)", "gross_margin": "毛利率 = 上期(毛利率) + 毛利变动 / 100"}[target]


class _Meth:
    model = _Model()
    pitfalls = ["毛利变动单位是 pp，公式里除以 100", "季度贡献不能直接当全年"]

    def factors(self):
        return F


def test_辩论二看得到公式单位线索与陷阱():
    t = S.factor_brief(_Meth())
    for x in ("毛利率累计变动", "单位 pp", "1.4", "不写 0.014", "销量同比增速", "单位 变化率", "8%", "单位 比例", "单位 万吨",
              "8-K 毛利率说明", "营收 = 上期(营收) × (1 + 销量)", "公式里除以 100", "季度贡献不能直接当全年"):
        assert x in t, x


class _Cat:
    evidence = None

    def render_for_prompt(self):
        return "目录"


def _run(round_, **kw):
    seen = []

    def client(p):
        seen.append(p)
        return ("多方｜销量同比增速｜好｜[[a@2025FY]]\n空方｜销量同比增速｜坏｜[[a@2025FY]]" if len(seen) < 3 else
                "平衡｜毛利率累计变动｜-0.002｜0.005｜0.014｜理由" if round_ == "B" else "平衡｜需求｜—｜—｜—｜理由")
    res = S.run_stance(client, round_=round_, subjects=["毛利率累计变动"], context="c", catalog=_Cat(), ledger=FactLedger(),
                       as_of=date(2026, 11, 25), **kw)
    return res, seen


def test_三个角色都拿到分解式_辩论一不受影响():
    res, seen = _run("B", brief="【分解式】内容", units={"毛利率累计变动": "pp"})
    assert all("【分解式】内容" in p for p in seen) and len(seen) == 3
    _res, seen_a = _run("A")
    assert not any("分解式】" in p for p in seen_a)


def test_裁决提示按单位填_不再一律要求小数():
    assert "0.08 或 8% 这类" not in S._JUDGE_B
    for x in ("按它自己标明的单位填", "1.4pp", "全年口径", "单季"):
        assert x in S._JUDGE_B


def test_百分点量级可疑只标注不驳回():
    res, _ = _run("B", units={"毛利率累计变动": "pp"})
    assert len(res.balances) == 1 and len(res.doubts) == 1
    d = res.doubts[0]
    assert "毛利率累计变动" in d and "0.014" in d and "1.4" in d
    assert d in res.summary()
    back = S.StanceResult.from_json(json.loads(json.dumps(res.to_json())))
    assert back.doubts == res.doubts


@pytest.mark.parametrize("unit,vals,n", [("pp", ("-3", "1.4", "2"), 0), ("pp", ("0", "0", "0"), 0), ("pp", ("-0.3", "0.1", "0.5"), 0),
                                         ("pp", ("0.005", "0", "-0.002"), 1), ("百分点", ("0.04", "0.06", "0.08"), 1),
                                         ("变化率", ("0.04", "0.06", "0.08"), 0), ("比例", ("0.1", "0.1", "0.1"), 0)])
def test_量级判断(unit, vals, n):
    b = S.Balance("x", *map(D, vals), "理由")
    assert len(S.unit_doubts([b], {"x": unit})) == n


def test_没有单位信息或纯文字结论不判():
    assert S.unit_doubts([S.Balance("x", D("0.01"), D("0.01"), D("0.01"), "")], {}) == []
    assert S.unit_doubts([S.Balance("x", None, None, None, "")], {"x": "pp"}) == []


def test_旧文件没有doubts也能读回():
    assert S.StanceResult.from_json({"round": "B", "evidence": [], "balances": []}).doubts == []


def test_判断项审核者看得到可疑提示():
    import inspect

    from ir_agent import assume_review
    assert "unit_doubts" in inspect.getsource(assume_review.facts_for_judge)


def test_阅读版列出可疑提示():
    from ir_agent.readable import stance_markdown
    assert "疑似" in stance_markdown({"balances": [], "evidence": [], "doubts": ["「x」疑似按小数填写"]})


def test_流程里辩论二传了分解式与单位():
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_brief)
    assert "factor_brief(meth)" in src and "units=" in src
