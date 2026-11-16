"""第③步：费用率参照表、填写规则、审核清单与营业利润率合成事实（2026-11-16）。不联网。"""

from __future__ import annotations

from ir_agent import assume, assume_review as AR, opex_judg as OJ
from tests.test_assume_opex import US_TEXT

PLTR_HIST = {"years": ["FY2021", "FY2022", "FY2023", "FY2024", "FY2025"],
             "研发费用率": [0.251, 0.189, 0.182, 0.177, 0.125],
             "销售及管理费用率": [0.795, 0.681, 0.570, 0.517, 0.383]}
JUDG = {"g_stats": (0.3, 0.1, 0.1, 0.56), "gm_stats": (0.8, 0.01, 0.78, 0.82), "gm_hist": [0.82],
        "cagr3": 0.33, "opex_hist": PLTR_HIST}


def _prompt(us=True, judg=JUDG):
    return assume.build_prompt(judg, ["FY2026E", "FY2027E", "FY2028E", "FY2029E", "FY2030E"],
                               context="ctx", brief="brief", us=us)


def test_美股提示词要费用率分项与稳态值_并给逐年历史():
    p = _prompt()
    for k in ("研发费用率", "销售及管理费用率", "稳态毛利率", "稳态研发费用率", "稳态销售及管理费用率", "终值ROIC溢价"):
        assert k in p
    assert "悲观 ≥ 中性 ≥ 乐观" in p and "第 6–10 年" in p
    assert "79.5%" in p and "38.3%" in p and "12.5%" in p           # 逐年历史，看得出单向下降
    assert "近3年均值" in p and "49.0%" in p                          # 旧口径作为对照，不再是默认
    assert "费用率、周转天数" not in p                                  # 不再说费用率由代码校准


def test_没有历史时照样要求填写():
    p = _prompt(judg={k: v for k, v in JUDG.items() if k != "opex_hist"})
    assert "研发费用率" in p and "历史：无" in p


def test_A股三项():
    p = _prompt(us=False)
    for k in ("销售费用率", "管理费用率", "研发费用率", "稳态管理费用率"):
        assert k in p
    assert "销售及管理费用率" not in p


def test_填了费用率才扩充审核清单():
    a = assume.parse(US_TEXT, 5, us=True)
    items = AR.checklist(a)
    for it in (("取值", "研发费用率"), ("依据", "研发费用率"), ("取值", "销售及管理费用率"),
               ("取值", "稳态毛利率"), ("取值", "终值ROIC溢价"), ("依据", "稳态值"),
               ("合成", "营业利润率"), ("后果", "稳态营业利润率")):
        assert it in items, it
    bare = "\n".join(l for l in US_TEXT.splitlines() if "费用率" not in l and "稳态" not in l and "ROIC" not in l)
    assert not any(s in ("研发费用率", "营业利润率") for _, s in AR.checklist(assume.parse(bare, 5, us=True)))


def test_审核事实给出三档营业利润率路径与稳态值():
    a = assume.parse(US_TEXT, 5, us=True)
    txt = AR.facts_for_judge(a, judg=JUDG)
    assert "【营业利润率】" in txt
    # 两标量模式：毛利率 80/82/84，费用率 14+40 / 12+36 / 10+32 → 26% / 34% / 42%
    assert "第1年 营业利润率 26.00% / 34.00% / 42.00%" in txt
    # 稳态：78−12−30 / 80−10−25 / 82−8−20 → 36% / 45% / 54%
    assert "稳态 营业利润率 36.00% / 45.00% / 54.00%" in txt
    assert "FY2025 营业利润率参照" in txt or "历史" in txt


def test_审核者短名作答能认领新条目():
    from ir_agent.methodology_review import parse
    exp = [("取值", "研发费用率"), ("取值", "稳态毛利率"), ("依据", "稳态值")]
    vs = parse("[取值] 研发费用率：通过\n[取值] 稳态毛利率：提醒　偏高\n[依据] 稳态值：通过", exp,
               kinds=("取值", "依据"))
    assert [v.level for v in vs] == ["通过", "提醒", "通过"]
