"""第一次调用里三个定性模块的落盘、停下、沿用与人工接手（spec 2026-10-17 五）。不调模型。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from ir_agent import full
from ir_agent import qual_review as Q
from ir_agent.methodology_review import ReviewResult, Verdict


@dataclass
class _View:
    raw: str = ""
    read_notes: list = field(default_factory=list)
    review: object = None
    rounds: list = field(default_factory=list)


def _passed(raw="项: a"):
    v = _View(raw)
    v.review = ReviewResult(verdicts=[Verdict("五力", "现有竞争强度", "提醒", "牵强")],
                            rounds=2, fingerprint=Q.fingerprint(raw))
    v.rounds = [Q.Round(1, "第一稿"), Q.Round(2, raw)]
    return v


class _Doc:
    def __init__(self, doc_id):
        self.doc_id, self.title = doc_id, doc_id


class _Corpus:
    def __init__(self, *ids):
        self.docs = [_Doc(i) for i in ids]


def _call(tmp_path, run, *, corpus=None, facts=lambda v: []):
    calls = []

    def _run():
        calls.append(1)
        return run()
    view, status = full._qual_module(
        tmp_path, prefix="strategy", label="战略分析", corpus=corpus or _Corpus("a"),
        parse=lambda raw: _View(raw), facts=facts, run=_run)
    return view, status, calls


class TestPass:
    def test_通过_落结果_审核结论_各轮记录(self, tmp_path):
        view, st, calls = _call(tmp_path, _passed)
        assert calls == [1] and st["state"] == "审核通过" and st["rounds"] == 2
        assert (tmp_path / "strategy.md").read_text(encoding="utf-8") == "项: a"
        rev = json.loads((tmp_path / "strategy_review.json").read_text(encoding="utf-8"))
        assert rev["passed"] is True and rev["fingerprint"] == Q.fingerprint("项: a")
        assert rev["corpus"] == Q.corpus_fingerprint(_Corpus("a"))
        assert "第一稿" in (tmp_path / "strategy_rounds.md").read_text(encoding="utf-8")
        assert any("牵强" in x for x in st["flagged"])


class TestReuse:
    """用户决定 11、12：通过的沿用不调模型；人改过的标「人工修改后未经审核」。"""

    def test_指纹一致_沿用审核结论_不调模型(self, tmp_path):
        _call(tmp_path, _passed)
        view, st, calls = _call(tmp_path, _passed)
        assert calls == [] and st["state"] == "沿用审核结论"
        assert view.review is not None and view.review.flagged()

    def test_人改过_不送审_标注(self, tmp_path):
        _call(tmp_path, _passed)
        (tmp_path / "strategy.md").write_text("项: 人改过", encoding="utf-8")
        view, st, calls = _call(tmp_path, _passed)
        assert calls == [] and st["state"] == "人工修改后未经审核" and view.raw == "项: 人改过"

    def test_没有审核记录_也标注(self, tmp_path):
        (tmp_path / "strategy.md").write_text("项: 人写的", encoding="utf-8")
        _, st, calls = _call(tmp_path, _passed)
        assert calls == [] and st["state"] == "人工修改后未经审核"

    def test_语料库变了_重核依据并标出(self, tmp_path):
        _call(tmp_path, _passed, corpus=_Corpus("a"))
        _, st, _ = _call(tmp_path, _passed, corpus=_Corpus("b"),
                         facts=lambda v: ["依据核验不过：x", "依据核验不过：y", "别的事实"])
        assert "本轮语料库与审核时不同" in st["note"] and "2 处" in st["note"]


class TestStop:
    def test_三轮不过_写各轮记录与最后一稿_不写结果(self, tmp_path):
        def run():
            raise Q.ReviewStop("战略分析审核 3 轮未通过（最后一轮致命 1 条）",
                               [Q.Round(1, "稿一"), Q.Round(2, "稿二")], last="稿二")
        with pytest.raises(full.QualStop, match="战略分析审核 3 轮未通过") as e:
            _call(tmp_path, run)
        assert "strategy_rounds.md" in str(e.value) and "strategy_failed_raw.md" in str(e.value)
        assert (tmp_path / "strategy_failed_raw.md").read_text(encoding="utf-8") == "稿二"
        assert not (tmp_path / "strategy.md").exists()

    def test_模型接口失败_也停下_写明重跑即可(self, tmp_path):
        from ir_agent.clients.claude_cli import ClaudeCliError

        def run():
            raise ClaudeCliError("You've hit your session limit")
        with pytest.raises(full.QualStop, match="战略分析审核停下：模型接口问题.*重跑"):
            _call(tmp_path, run)


def test_一个模块停下_其余照常跑完(tmp_path, monkeypatch):
    """用户决定 9：三个模块照常都跑完再停。"""
    def stop(*a, **k):
        raise full.QualStop("战略分析审核 3 轮未通过（最后一轮致命 1 条）")
    monkeypatch.setattr(full, "_load_strategy", stop)
    monkeypatch.setattr(full, "_load_macro_view", lambda *a, **k: ("宏观", "宏观", ""))
    monkeypatch.setattr(full, "_load_value_chain", lambda *a, **k: ("产业链", "产业链", ""))
    monkeypatch.setattr(full, "make_client", lambda **k: object())

    class _R:
        quotes = None
    md, pr, skipped, stops = {}, {"宏观": ""}, [], []
    full._analyse_qualitative("600176", _R(), None, None, md, pr, skipped,
                              out=tmp_path, stops=stops, statuses=[])
    assert stops == ["战略分析审核 3 轮未通过（最后一轮致命 1 条）"]
    assert md["宏观定性"] == "宏观" and md["产业链"] == "产业链" and md["战略"] == ""


def test_门一清单的定性分析审核一节():
    txt = full._qual_section([
        {"label": "宏观定性", "state": "审核通过", "rounds": 1, "flagged": [], "note": ""},
        {"label": "战略分析", "state": "人工修改后未经审核", "rounds": 0, "flagged": [], "note": ""},
        {"label": "产业链", "state": "沿用审核结论", "rounds": 2,
         "flagged": ["[议价] 对上游：提醒　x"], "note": "本轮语料库与审核时不同，1 处依据本轮核对不过"}])
    assert txt.startswith("\n## 定性分析审核")
    assert "宏观定性：第 1 轮审核通过" in txt and "战略分析：⚠ 人工修改后未经审核" in txt
    assert "[议价] 对上游：提醒" in txt and "1 处依据本轮核对不过" in txt
    assert full._qual_section([]) == ""


def test_重跑通过后删掉上一轮停下留的最后一稿(tmp_path):
    (tmp_path / "strategy_failed_raw.md").write_text("上一轮的最后一稿", encoding="utf-8")
    _call(tmp_path, _passed)
    assert not (tmp_path / "strategy_failed_raw.md").exists()


def test_研报附录写明人工修改后未经审核的定性模块():
    """spec 六：第一次调用把「人工修改后未经审核」的模块名记进 state.json，第二阶段写进
    研报末尾「⚠ 判断项依据核验」附录；进提示词那句不带数字。"""
    import re

    from ir_agent import assume
    a = assume.AssumptionSet(code="600176", labels=["2026E"])
    line, md = full._assumption_caveats(a, qual_unreviewed=["战略分析", "产业链"])
    assert "战略分析" in line and "产业链" in line and "未经" in line
    assert not re.search(r"\d", line)
    assert "战略分析、产业链" in md and "人工修改后未经审核" in md


def test_第二阶段读回未经审核的定性模块():
    import inspect
    src = inspect.getsource(full.run_valuation)
    assert 'state.get("qual_unreviewed")' in src
    assert "qual_unreviewed=qual_unreviewed" in src
