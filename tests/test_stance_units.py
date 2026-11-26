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


# ── 终审（2026-11-26）I1–I5 与小项 ─────────────────────────────

def test_I1_相对基期累计的按定义填_不被换算():
    t = S._JUDGE_B
    assert "累计数要先换算成全年" not in t
    assert "年初至今" in t and "公式下注明的参照点" in t


class _Formulas(_Meth):
    def __init__(self, mode, rev, gm):
        class M(_Model):
            pass
        M.mode = mode
        M.describe = lambda self, t: {"revenue": rev, "gross_margin": gm}[t]
        self.model = M()


@pytest.mark.parametrize("rev,gm,want,never", [
    ("营收 = 上期(营收) × (1 + 销量)", "毛利率 = 上期(毛利率) + 毛利变动 / 100", "相对上一年的逐年变动", "相对基期的累计值"),
    ("营收 = 基期(营收) × (1 + 销量)", "毛利率 = 基期(毛利率) + 毛利变动 / 100", "相对基期的累计值", "相对上一年的逐年变动"),
])
def test_I1_参照点按公式注明_不按模式猜(rev, gm, want, never):
    t = S.factor_brief(_Formulas("变化", rev, gm))
    assert t.count(want) == 2 and never not in t
    assert "因子是相对基期的变动" not in t


def test_I1_两种参照点混用与水平模式():
    t = S.factor_brief(_Formulas("变化", "营收 = 上期(营收) × (1 + 销量)", "毛利率 = 基期(毛利率) + 毛利变动 / 100"))
    assert "相对上一年的逐年变动" in t and "相对基期的累计值" in t
    t = S.factor_brief(_Formulas("变化", "营收 = 上期(营收) × (1 + 销量) + 基期(营收) × 0", "毛利率 = 1 - 成本"))
    assert "同时用到上期与基期" in t
    t = S.factor_brief(_Formulas("水平", "营收 = 产能 × 单价", "毛利率 = 1 - 成本 / 单价"))
    assert "当年的水平值" in t and "逐年变动" not in t


def test_I2_因子清单带短名_平衡行照抄完整名():
    t = S.factor_brief(_Meth())
    assert "毛利变动（毛利率累计变动）" in t and "销量（销量同比增速）" in t
    assert "照抄完整名" in t


def test_I2_清单里不出现证据行分隔符():
    class M(_Meth):
        pitfalls = ["a｜b|c"]

        def factors(self):
            return [Factor("f9", "x", "pp", "年报｜分部表", short="x")]
    assert "｜" not in S.factor_brief(M()) and "|" not in S.factor_brief(M())


@pytest.mark.parametrize("subject", ["关税", "毛利变动", "毛利率累计变动（相对基期）", " 毛利率累计变动 "])
def test_I3_裁决者缩写因子名也核对得到(subject):
    units = S.units_of(_Meth())
    units["关税影响毛利率累计变动"] = "pp"
    b = S.Balance(subject, D("-0.015"), D("-0.005"), D("0.003"), "")
    assert any("疑似按小数" in d for d in S.unit_doubts([b], units)), subject


def test_I3_对不上任何因子的平衡点要说出来():
    out = S.unit_doubts([S.Balance("完全无关", D("1"), D("2"), D("3"), "")], S.units_of(_Meth()))
    assert len(out) == 1 and "对不上" in out[0]


def test_I3_前缀同时命中单位不同的因子不乱判():
    units = {"毛利甲": "pp", "毛利乙": "变化率"}
    out = S.unit_doubts([S.Balance("毛利", D("0.01"), D("0.01"), D("0.01"), "")], units)
    assert len(out) == 1 and "对不上" in out[0]


@pytest.mark.parametrize("unit,vals,n", [("变化率（小数）", ("6", "8", "10"), 1), ("变化率", ("6", "8", "10"), 1),
                                         ("比例", ("40", "45", "50"), 1), ("变化率（小数）", ("0.5", "1.0", "1.5"), 0),
                                         ("万吨", ("40", "45", "50"), 0)])
def test_I4_变化率比例填成百分数也标(unit, vals, n):
    out = S.unit_doubts([S.Balance("x", *map(D, vals), "")], {"x": unit})
    assert len(out) == n and (n == 0 or "百分数" in out[0])


def test_I4_小数单位给出明确填法():
    class M(_Meth):
        def factors(self):
            return [Factor("f8", "出货变化", "变化率（小数）", "", short="出货")]
    t = S.factor_brief(M())
    assert "出货（出货变化）｜单位 变化率（小数）" not in t        # 分隔符已换
    assert "不写 8" in t and "按原数填" not in t


def test_I5_阅读版按当前分解式重算疑点(tmp_path):
    from ir_agent import readable
    from ir_agent import methodology
    (tmp_path / "drivers.md").write_text(methodology.to_markdown(methodology.parse(_DRIVERS)), encoding="utf-8")
    bal = {"subject": "毛利率累计变动", "bear": "-0.3", "mid": "1.4", "bull": "2", "note": ""}
    src = tmp_path / "stance_b.json"
    src.write_text(json.dumps({"round": "B", "evidence": [], "balances": [bal],
                               "doubts": ["「毛利率累计变动」疑似按小数填写（旧）"]}, ensure_ascii=False), encoding="utf-8")
    assert "疑似按小数" not in readable._stance_doc(src, tmp_path, "t")       # 人改对了，旧疑点不再显示
    bal.update(bear="-0.003", mid="0.014", bull="0.02")
    src.write_text(json.dumps({"round": "B", "evidence": [], "balances": [bal]}, ensure_ascii=False), encoding="utf-8")
    assert "疑似按小数" in readable._stance_doc(src, tmp_path, "t")           # 旧文件没有 doubts 也照样核对


def test_小项_空端点不崩():
    b = S.Balance("x", None, D("0.01"), None, "")
    assert len(S.unit_doubts([b], {"x": "pp"})) == 1


def test_小项_两标量回退也有单位说明():
    t = S.factor_brief(None)
    assert "营收增速" in t and "毛利率" in t and "不写 8" in t
    assert S.units_of(None) == {"营收增速": "变化率", "毛利率": "比例"}


_DRIVERS = """模式: 变化
因子: 毛利变动 | 毛利率累计变动 | pp | 8-K | 毛利率
因子: 销量 | 销量同比增速 | 变化率 | 年报 | 销量
公式: 营收 = 上期(营收) * (1 + 销量)
公式: 毛利率 = 上期(毛利率) + 毛利变动 / 100
"""


def test_I3_主名相同_括号嵌套或逗号补充也认得():
    units = {"公司覆铜板销量增速相对行业出货增速的超额，按 (1+公司销量增速)/(1+行业需求增速)−1": "变化率（小数）"}
    b = S.Balance("公司覆铜板销量增速相对行业出货增速的超额（(1+公司销量)/(1+行业需求)−1）", D("6"), D("8"), D("10"), "")
    out = S.unit_doubts([b], units)
    assert len(out) == 1 and "百分数" in out[0]
