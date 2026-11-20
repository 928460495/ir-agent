"""人工修改并在闸门签名的内容就是已确认的输入（用户 2026-10-19 / 2026-11-20 决定）：
不再标「未经审核」，研报不写「未经 AI 审核……打折看待」；只留一条中性的改动记录。不联网。"""

from __future__ import annotations

import inspect

from ir_agent import full, research


def test_分解式人工修改后不写未经审核横幅(tmp_path, monkeypatch):
    (tmp_path / full.DRIVERS_MD).write_text("模式: 变化\n", encoding="utf-8")
    monkeypatch.setattr(full, "_method_review", lambda out, m: (None, True))
    note = full._unreviewed_note(tmp_path, object())
    assert "未经审核" not in note and "签名确认" in note
    assert "未经审核" not in (tmp_path / full.DRIVERS_MD).read_text(encoding="utf-8")
    lines = full._method_review_lines(tmp_path, object())
    assert lines and not any("未经审核" in x for x in lines)


def test_旧横幅读回时去掉(tmp_path, monkeypatch):
    (tmp_path / full.DRIVERS_MD).write_text(full.UNREVIEWED_BANNER + "\n\n模式: 变化\n", encoding="utf-8")
    monkeypatch.setattr(full, "_method_review", lambda out, m: (None, True))
    full._unreviewed_note(tmp_path, object())
    assert "未经审核" not in (tmp_path / full.DRIVERS_MD).read_text(encoding="utf-8")


def test_定性模块人工修改记为签名确认():
    md = full._qual_section([{"label": "战略分析", "state": full.HUMAN_SIGNED, "rounds": 0,
                              "flagged": [], "note": ""}])
    assert "未经审核" not in md and "签名确认" in md


def test_判断项人工改动记录不再说未经审核或原依据未必支撑():
    lines = full._human_edit_lines(["研发费用率 第1年 base：模型给 0.1，核实后改为 0.11"])
    assert lines == ["判断项经人工调整：研发费用率 第1年 base：模型给 0.1，核实后改为 0.11（签名确认）"]


def test_各阶段不再产出未经审核():
    src = inspect.getsource(full)
    for s in ("判断项未经审核：", "定性分析未经审核：", "修改后未经 AI 审核", "原依据未必仍然支撑新取值"):
        assert s not in src, s
    assert "未经审核" not in inspect.getsource(research)
