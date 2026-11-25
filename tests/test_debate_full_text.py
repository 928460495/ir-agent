"""辩论结论传给下一步时不截断（用户 2026-11-25）。两仓历史 151 条平衡点结论全被截到 50 / 70 字，
20 条断在占位符中间；后一步的模型只看到半句话。"""

from __future__ import annotations

from decimal import Decimal

from ir_agent.debate import Challenge, ChallengeKind, DebateResult
from ir_agent.stance import Balance, StanceResult

NOTE = "多方成立的是韧性：[[revenue.yoy@2025FY]] 远好于 [[industry.revenue_yoy@2025FY]]。" + "空方成立的是总量偏弱。" * 30 + "合起来方向中性偏弱。"


def test_定性话题的结论不截断():
    s = StanceResult(round_="A", balances=[Balance("需求", None, None, None, NOTE)]).summary()
    assert NOTE in s


def test_取值话题的结论不截断():
    s = StanceResult(round_="B", balances=[Balance("销量", Decimal("0.01"), Decimal("0.02"), Decimal("0.03"), NOTE)]).summary()
    assert NOTE in s and "空 0.01 / 平 0.02 / 多 0.03" in s


def test_结论里的换行并成一行_清单仍是一条一行():
    s = StanceResult(round_="A", balances=[Balance("需求", None, None, None, "甲\n乙")]).summary()
    assert "需求：甲 乙" in s


def test_成稿辩论的原文与理由不截断():
    c = Challenge(index=1, kind=ChallengeKind.LOGIC, passage="正文" * 60, objection="o", verdict="成立", rationale="理由" * 80)
    s = DebateResult(challenges=[c], rounds=1).summary()
    assert "正文" * 60 in s and "理由" * 80 in s


def test_简报撰写提示说明结论里的数字不能照抄():
    import inspect

    from ir_agent import full
    assert "没有占位符的数字" in inspect.getsource(full._balance_context)
