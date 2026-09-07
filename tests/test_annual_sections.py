"""年报叙述章节抽取。用例全部来自四家真实年报踩到的情况。"""

from __future__ import annotations

import pytest

from ir_agent.sources import annual_sections as asx

BODY = "内容" * 100          # 200 字，足够越过「空洞」门槛


def _pages(monkeypatch, pages):
    monkeypatch.setattr(asx, "_page_texts", lambda pdf: pages)


# ── 定位 ─────────────────────────────────────────────────────

def test_跳过目录行(monkeypatch):
    """目录里的标题后跟一串点与页码，取到的是索引不是正文。"""
    pages = ["第三节\n管理层讨论与分析......................8\n",
             f"四、报告期内核心竞争力分析\n{BODY}\n"]
    _pages(monkeypatch, pages)
    s = asx.extract("x", wanted=("core_competency",)).get("core_competency")
    assert s.page == 2


def test_拒绝句中的交叉引用(monkeypatch):
    """关键词嵌在长句里是引用不是标题。取它会凭空造出一节内容不对的分析，
    比缺失更糟 —— 实测茅台会因此多出一节根本不在讲行业的「行业分析」。"""
    pages = [f"公司的核心竞争力分析请参考下文相关章节所述内容如下{BODY}"]
    _pages(monkeypatch, pages)
    out = asx.extract("x", wanted=("core_competency",))
    assert "core_competency" in out.missing


def test_关键词按优先级命中即停(monkeypatch):
    """茅台实测: 「所处行业情况」在第 8 页是正式章节，而「行业格局与趋势」
    作为子标题在第 21 页。取页码最大的会选错。"""
    pages = [f"二、报告期内公司所处行业情况\n{BODY}\n", "", "",
             f"1.行业格局与趋势。\n{'别的内容' * 60}\n"]
    _pages(monkeypatch, pages)
    s = asx.extract("x", wanted=("industry_view",)).get("industry_view")
    assert s.page == 1


def test_同一关键词多处命中取靠后的(monkeypatch):
    """正文永远在重要提示与目录之后。"""
    pages = ["未来展望\n", "", f"未来展望\n{BODY}\n"]
    _pages(monkeypatch, pages)
    s = asx.extract("x", wanted=("outlook",)).get("outlook")
    assert s.page == 3


# ── 边界 ─────────────────────────────────────────────────────

def test_子标题不构成章节边界(monkeypatch):
    """正文常以「（一）品牌优势」开头 —— 把它当边界会在起点立刻截断。
    实测茅台核心竞争力因此只抽到 95 字。"""
    pages = [f"四、报告期内核心竞争力分析\n（一）品牌优势\n{BODY}\n"
             f"（二）工艺优势\n{BODY}\n五、报告期内主要经营情况\n别的"]
    _pages(monkeypatch, pages)
    s = asx.extract("x", wanted=("core_competency",)).get("core_competency")
    assert "品牌优势" in s.text and "工艺优势" in s.text
    assert "主要经营情况" not in s.text


def test_同级标题构成边界(monkeypatch):
    pages = [f"四、报告期内核心竞争力分析\n{BODY}\n五、报告期内主要经营情况\n不该出现"]
    _pages(monkeypatch, pages)
    s = asx.extract("x", wanted=("core_competency",)).get("core_competency")
    assert "不该出现" not in s.text


def test_超长章节截断并标注(monkeypatch):
    pages = [f"四、报告期内核心竞争力分析\n{'长' * 5000}"]
    _pages(monkeypatch, pages)
    s = asx.extract("x", wanted=("core_competency",),
                    max_chars=1000).get("core_competency")
    assert len(s.text) == 1000 and s.truncated
    assert "已截断" in s.describe()


# ── 交叉引用 ─────────────────────────────────────────────────

def test_识别纯指针章节():
    assert asx._is_cross_ref("详见本报告第15 页“行业基本情况”。")
    assert asx._is_cross_ref("参见第三节。")
    assert not asx._is_cross_ref("详见下文。" + "实质内容" * 60)


def test_指针章节回退到同义措辞(monkeypatch):
    """茅台实测: 「二、报告期内公司所处行业情况」整节就是一句
    「详见本报告第15页…」。既不能当作找到（内容不在这里），
    也不能当作没有（该节确实存在）。"""
    pages = ["二、报告期内公司所处行业情况\n详见本报告第21 页“行业格局和趋势”。\n"
             "三、经营情况讨论与分析\n",
             f"1.行业格局与趋势。\n{BODY}\n"]
    _pages(monkeypatch, pages)
    s = asx.extract("x", wanted=("industry_view",)).get("industry_view")
    assert s.page == 2 and len(s.text) > 100
    assert "交叉引用" in s.label


def test_回退无果则报缺失(monkeypatch):
    pages = ["二、报告期内公司所处行业情况\n详见本报告第99 页。\n"]
    _pages(monkeypatch, pages)
    assert "industry_view" in asx.extract("x", wanted=("industry_view",)).missing


# ── 缺失的诚实报告 ───────────────────────────────────────────

def test_找不到时报缺失而非返回空串(monkeypatch):
    """返回空串会让下游误以为「该公司没有护城河可讲」。"""
    _pages(monkeypatch, ["与本节无关的内容" * 50])
    out = asx.extract("x", wanted=("core_competency",))
    assert out.sections == {} and out.missing == ["core_competency"]
    assert "未找到" in out.summary() and "另寻依据或明说无据" in out.summary()


def test_内容空洞视同没有(monkeypatch):
    _pages(monkeypatch, ["四、报告期内核心竞争力分析\n√适用□不适用\n"])
    assert "core_competency" in asx.extract(
        "x", wanted=("core_competency",)).missing


def test_披露详略本身是信息(monkeypatch):
    """茅台核心竞争力仅 95 字、宝钢 1482 字 —— 字数要让读者看到。"""
    _pages(monkeypatch, [f"四、报告期内核心竞争力分析\n{BODY}\n"])
    s = asx.extract("x", wanted=("core_competency",)).get("core_competency")
    assert "摘录" in s.describe() and "字" in s.describe()


# ── 上下文隔离 ───────────────────────────────────────────────

def test_全文不外泄(monkeypatch):
    """与 annual_pdf 同一条约定: PDF 全文留在模块内部，只交出有界摘录。
    招行年报 350 页，整段倒进提示词既贵又没用。"""
    huge = ["四、报告期内核心竞争力分析\n" + "字" * 50000]
    _pages(monkeypatch, huge)
    out = asx.extract("x", wanted=("core_competency",))
    assert len(out.get("core_competency").text) <= asx.MAX_EXCERPT
    assert sum(len(s.text) for s in out.sections.values()) <= asx.MAX_EXCERPT


@pytest.mark.parametrize("key", list(asx.SECTION_KEYS))
def test_每个章节都有中文标签(key):
    assert key in asx.SECTION_LABELS
