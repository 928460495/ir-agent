"""文档进提示词时的截断。**入库存全文，进提示词才截断。**"""

from __future__ import annotations

from datetime import date

from ir_agent.sources.external import Corpus, Document, Tier

AS_OF = date(2026, 9, 20)


def _doc(text: str, limit: int | None = None) -> Document:
    return Document(doc_id="d1", url="https://x.example.com/a", title="标题",
                    publisher="某机构", tier=Tier.MEDIA,
                    published=date(2026, 6, 1), retrieved=AS_OF,
                    text=text, prompt_limit=limit)


def test_没有上限时原样返回():
    assert _doc("一二三四五").for_prompt() == "一二三四五"


def test_未超上限时原样返回():
    assert _doc("一二三四五", 10).for_prompt() == "一二三四五"


def test_超上限时截断并标注():
    """final-fix-3 Fix 3：截断标记改成方括号包住的系统批注，不再用领头
    省略号——省略号本身是模型在"引用必须逐字照抄"这条规矩下可能模仿的
    引用标点，见 external._TRUNCATED 定义处的注释。"""
    got = _doc("一二三四五六七八九十", 5).for_prompt()
    assert got.startswith("一二三四五")
    assert "截断" in got
    assert "……" not in got
    assert "六七八九十" not in got


def test_截断不影响verify_核验用的是全文():
    """模型只能引用它看得见的那一段，而那一段必然在全文里 ——
    所以截断不会让引文核验误杀。"""
    cp = Corpus(as_of=AS_OF)
    cp.add(_doc("一二三四五六七八九十", 5))
    assert cp.verify("一二三四五") == ""
    # 全文里有、但模型看不见的那一段，核验一样通过（核验的是全文）
    assert cp.verify("六七八九十") == ""
