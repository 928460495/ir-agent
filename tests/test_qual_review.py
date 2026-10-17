"""三个定性模块共用的审核引擎（spec 2026-10-17 子项目四）。不调模型。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ir_agent import qual_review as Q
from judge_stub import Judge


class _Doc:
    def __init__(self, title, doc_id="d1"):
        self.title, self.doc_id, self.text = title, doc_id, ""


class _Corpus:
    def __init__(self, *titles):
        self.docs = [_Doc(t, f"d{i}") for i, t in enumerate(titles)]


CORPUS = _Corpus("东莞证券：玻纤行业深度报告", "国信证券《建材月报》")


class TestBasis:
    """用户决定 3：依据可以是占位符、原文引用，或写明出处的转述。都读得进，
    核对结果只作事实。"""

    def test_占位符(self):
        bases, prob, docs = Q.basis("[[payable_days@2025FY]]", CORPUS)
        assert bases[0].describe() and prob == "" and docs == []

    def test_原文引用_研报也行(self):
        bases, prob, _ = Q.basis("「玻纤价格企稳回升」——东莞证券《玻纤行业深度报告》", CORPUS)
        assert bases and bases[0].quote == "玻纤价格企稳回升" and prob == ""

    def test_转述_写明文章_在语料库里(self):
        bases, prob, docs = Q.basis("转述：综合 东莞证券《玻纤行业深度报告》", CORPUS)
        assert bases == [] and prob == "" and docs == ["玻纤行业深度报告"]

    def test_转述_文章不在语料库_只记事实(self):
        _, prob, docs = Q.basis("转述：综合 中信证券《不存在的报告》", CORPUS)
        assert "不在语料库" in prob and docs == ["不存在的报告"]

    def test_转述没写明文章(self):
        _, prob, _ = Q.basis("转述：行业普遍认为如此", CORPUS)
        assert "没有写明" in prob

    def test_什么都没给(self):
        _, prob, _ = Q.basis("我的判断", CORPUS)
        assert "没有给出依据" in prob


@dataclass
class _View:
    lines: list = field(default_factory=list)
    read_notes: list = field(default_factory=list)


def _read(raw):
    v = _View(lines=[x for x in raw.splitlines() if x.startswith("项:")])
    if "坏行" in raw:
        v.read_notes.append("一行没读进来：坏行")
    return v


def _run(writer, judge, **kw):
    return Q.run(writer, judge, name="战略分析", prompt="撰写提示词", materials="资料",
                 read=_read, facts=lambda v: ["描述里有账本外数字：91%"],
                 checklist=lambda v: [("五力", "现有竞争强度"), ("自洽", "三部分")],
                 kinds=("五力", "自洽"), rules="审核规则", catalog="账本目录",
                 **kw)


class _Writer:
    def __init__(self, *outs):
        self.outs, self.prompts = list(outs), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.outs[min(len(self.prompts), len(self.outs)) - 1]


class TestRun:
    def test_第一轮通过(self):
        view, res, rounds = _run(_Writer("项: a"), Judge())
        assert res.rounds == 1 and view.lines == ["项: a"] and res.fingerprint

    def test_致命退回带建议_第二轮通过(self):
        w = _Writer("项: a", "项: b")
        view, res, rounds = _run(w, Judge({("五力", "现有竞争强度"): "致命"}, {}))
        assert res.rounds == 2 and view.lines == ["项: b"]
        assert "建议：改五力现有竞争强度" in w.prompts[1]

    def test_审核者看到读数事实_代码事实_账本目录(self):
        j = Judge()
        _run(_Writer("项: a\n坏行"), j)
        p = j.prompts[0]
        assert "一行没读进来：坏行" in p and "账本外数字：91%" in p and "账本目录" in p
        assert "审核规则" in p and "资料" in p

    def test_三轮不过停下_带最后一稿(self):
        with pytest.raises(Q.ReviewStop, match="战略分析审核 3 轮未通过.*致命 1 条") as e:
            _run(_Writer("项: a", "项: b", "项: c"), Judge({"自洽": "致命"}))
        assert e.value.last == "项: c" and len(e.value.rounds) == 3

    def test_审核者答不全停下(self):
        with pytest.raises(Q.ReviewStop, match="战略分析审核未完成"):
            _run(_Writer("项: a"), Judge(skip={("自洽", "三部分")}))

    def test_补答不消耗撰写者轮数(self):
        class Once(Judge):
            def __call__(self, prompt):
                self.skip = {("自洽", "三部分")} if not self.prompts else set()
                return super().__call__(prompt)
        w, j = _Writer("项: a"), Once()
        _, res, rounds = _run(w, j)
        assert len(j.prompts) == 2 and len(w.prompts) == 1 and res.rounds == 1
        assert "上一次回答不完整" in j.prompts[1] and len(rounds) == 1


def test_指纹不计空白_语料库指纹按文档集合():
    assert Q.fingerprint("项: a\n\n") == Q.fingerprint("  项: a")
    assert Q.fingerprint("项: a") != Q.fingerprint("项: b")
    two, three = _Corpus("a", "b"), _Corpus("a", "b", "c")
    assert Q.corpus_fingerprint(two) == Q.corpus_fingerprint(_Corpus("x", "y")), \
        "按文档 id 集合算（d0、d1），与标题无关"
    assert Q.corpus_fingerprint(two) != Q.corpus_fingerprint(three)
    assert Q.corpus_fingerprint(None) == ""


def test_规则共用段_四级定义_依据_账本外数字_格式():
    r = Q.make_rules("战略分析", "- 五力：……")
    for s in ("通过", "提醒", "致命", "待采集", "逻辑完全无法支撑结论", "转述",
              "账本外数字", "建议：", "补充：", "- 五力：……"):
        assert s in r, s


def test_审核遗留附在摘要末尾_只列提醒与待采集():
    from ir_agent.methodology_review import ReviewResult, Verdict
    res = ReviewResult(verdicts=[Verdict("五力", "现有竞争强度", "提醒", "牵强", suggest="补依据"),
                                 Verdict("自洽", "三部分", "通过", "")])
    tail = Q.review_tail(res)
    assert tail[0].startswith("  审核遗留") and "建议：补依据" in tail[1] and len(tail) == 2
    assert Q.review_tail(None) == []
