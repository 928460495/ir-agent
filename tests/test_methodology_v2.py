"""分解预研提示词专业化（spec 2026-11-21-staged-collection §5.2）。不联网。"""

from __future__ import annotations

import pytest

from ir_agent import methodology as M


def test_examples_parse_both_markets(code="600519"):
    prompt = M.build_prompt("某行业", code)
    exs = M.examples_for(code)
    assert len(exs) == 3
    for ex in exs:
        assert ex in prompt
        m = M.parse(ex)
        assert m.model is not None and len(m.model.factors) >= 4 and m.approach
    unit = "/ 1000" if code == "AAPL" else "/ 10000"
    assert unit in exs[1] and unit in exs[2]


def test_approach_line_roundtrip():
    m = M.parse(M.examples_for("600519")[0])
    assert m.approach.startswith("自下而上")
    back = M.from_markdown(M.to_markdown(m))
    assert back.approach == m.approach
    no = M.parse(M.examples_for("600519")[0].replace(next(l for l in M.examples_for("600519")[0].splitlines()
                                                         if l.startswith("路径:")) + "\n", ""))
    assert no.approach == ""


def test_cn_prompt_keeps_cn_terms():
    p = M.build_prompt("某行业", "600519")
    assert "亿元" in p and "年报" in p and "卖方研报" in p


def test_methodology_prompt_neutral():
    for code in ("600519",):
        p = M.build_prompt("某行业", code)
        for b in ("白酒", "封测", "覆铜板", "批价"):
            assert b not in p
        for k in ("自下而上", "自上而下", "同口径", "结构", "固定成本", "传导率", "路径:"):
            assert k in p


def test_review_rules_cover_new_checks():
    from ir_agent.methodology_review import RULES
    for k in ("预测路径", "价与结构", "同一个传导率"):
        assert k in RULES
