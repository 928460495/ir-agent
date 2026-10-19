"""年报叙述性章节抽取 —— 战略分析的证据基础。

`annual_pdf.py` 从年报里取**数字**（用于裁决两源分歧），本模块取**文字**。
两者共用同一条约定: **PDF 全文留在模块内部，只把有界摘录交出去。**
茅台 2025 年报 143 页、招行 350 页，把 MD&A 整段倒进提示词既贵又没用 ——
模型需要的是能引用的段落，不是全文。

**章节体例并不统一，这是实测结论不是猜测。** 证监会 2021 年修订后的格式里，
「核心竞争力分析」「未来发展的讨论与分析」是**第三节管理层讨论与分析的
子章节**而非独立章节；而不同行业的年报覆盖情况又不一样:

    公司        MD&A  核心竞争力  所处行业情况  未来发展讨论
    宝钢         ✓        ✓          ✓            ✓
    宁德时代     ✓        ✓          ✓            ✗
    招商银行     ✓        ✓          ✗            ✗

因此: **按关键词全文搜索，而不是按固定的章节编号定位**；找不到的章节
如实报告，不返回空字符串让下游误以为「该公司没有护城河可讲」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 每节交出去的最大字符数。取 3000: 够覆盖一节的核心论述，
# 又不至于让四五节加起来把提示词撑到上万字。
MAX_EXCERPT = 3000

# 章节 → 可能的措辞。**一个概念多种说法**，因为体例不统一。
# 顺序即优先级: 越靠前越贴近该概念，命中即停。
SECTION_KEYS: dict[str, tuple[str, ...]] = {
    "core_competency": ("核心竞争力分析", "核心竞争力"),
    "industry_view": ("公司所处行业情况", "所处行业情况",
                      "行业格局与趋势", "行业发展趋势"),
    "outlook": ("公司关于公司未来发展的讨论与分析", "未来发展的讨论与分析",
                "公司未来发展的展望", "未来展望", "公司发展战略"),
    "business": ("报告期内公司从事的业务情况", "公司从事的业务情况",
                 "主营业务分析", "业务概述"),
    "risks": ("可能面对的风险", "风险因素", "重大风险提示"),
    "governance": ("公司治理相关情况", "公司治理情况"),
}

SECTION_LABELS = {
    "core_competency": "核心竞争力分析",
    "industry_view": "所处行业情况（管理层自述）",
    "outlook": "未来发展的讨论与分析",
    "business": "业务情况",
    "risks": "可能面对的风险",
    "governance": "公司治理",
}

# 下一节的起点。**只认同级或更高级的标题**: 「第X节」与「X、标题」。
# 不能把「（一）」算作边界 —— 正文往往就以「（一）品牌优势」开头，
# 一进正文就被截断。实测茅台核心竞争力因此只抽到 95 字。
_NEXT_HEADING = re.compile(
    r"^\s*(?:第[一二三四五六七八九十]+节"
    r"|[一二三四五六七八九十]+、\s*\S{3,30})\s*$",
    re.MULTILINE)

# 目录行: 标题后跟一串点与页码。命中目录会取到一段没有内容的索引，
# 因此定位时必须跳过。
_TOC_LINE = re.compile(r"\.{5,}\s*\d+\s*$")

# 「详见本报告第 15 页…」—— 章节存在但正文只是个指针。实测茅台的
# 「二、报告期内公司所处行业情况」整节就是一句交叉引用。这种情况不能
# 当作「找到了」（内容不在这里），也不能当作「没有」（该节确实存在），
# 正确做法是退到优先级列表的下一个措辞去找真正的内容。
_CROSS_REF = re.compile(r"详见|参见|请见|见本报告第")
CROSS_REF_MAX = 160          # 超过此长度即便含「详见」也视为有实质内容


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    heading: str
    page: int
    text: str
    truncated: bool = False

    def describe(self) -> str:
        n = len(self.text)
        return (f"{self.label}（原文第 {self.page} 页，摘录 {n} 字"
                + ("，已截断" if self.truncated else "") + "）")


@dataclass
class SectionSet:
    code: str
    pages: int
    sections: dict[str, Section] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def get(self, key: str) -> Section | None:
        return self.sections.get(key)

    def summary(self) -> str:
        lines = [f"年报叙述章节 {self.code}（全文 {self.pages} 页）"]
        for s in self.sections.values():
            lines.append(f"  ✓ {s.describe()}")
        for k in self.missing:
            lines.append(f"  – 未找到「{SECTION_LABELS.get(k, k)}」—— "
                         "该公司年报可能不设此节（银行等金融企业体例不同），"
                         "相关判断需另寻依据或明说无据")
        return "\n".join(lines)


def _page_texts(pdf: Path | str | bytes) -> list[str]:
    """接受路径或已下载的字节。走管线时 PDF 已由 annual_pdf 下载并缓存，
    再落一次盘既慢又多一份副本。"""
    import pymupdf as fitz                       # PyMuPDF（新版弃用 fitz 这个导入名）

    doc = (fitz.open(stream=pdf, filetype="pdf") if isinstance(pdf, bytes)
           else fitz.open(str(pdf)))
    try:
        return [pg.get_text() for pg in doc]
    finally:
        doc.close()


def _is_cross_ref(body: str) -> bool:
    return len(body) <= CROSS_REF_MAX and bool(_CROSS_REF.search(body))


def _locate(pages: list[str], keys: tuple[str, ...],
            skip: int = 0) -> tuple[int, int, str] | None:
    """返回 (页码0基, 页内偏移, 命中的标题行)。

    **不取第一个命中**: 关键词常在「重要提示」「目录」里作为交叉引用出现
    （实测茅台的「未来发展的讨论与分析」首次出现在第 3 页，而正文在第 20
    页附近）。这里对所有命中打分，优先**独立成行的标题**:

      · 目录行（后跟一串点与页码）—— 直接排除
      · 该行去掉序号后与关键词长度接近 —— 是标题
      · 关键词嵌在长句里 —— 是引用，降级

    同分时取**较晚出现**的那个: 正文永远在提示与目录之后。
    """
    # **按关键词优先级逐个试，命中即停** —— 与 SECTION_KEYS 的排序约定一致。
    # 早前实现是跨所有关键词打分再取「页码最大」的，结果让低优先级的关键词
    # 赢了: 茅台的「所处行业情况」在第 8 页（正式章节），而「行业格局与趋势」
    # 作为子标题出现在第 21 页，后者被选中，抽出的内容根本不是那一节。
    for kw in keys[skip:]:
        best = None
        for i, text in enumerate(pages):
            for m in re.finditer(re.escape(kw), text):
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                line = text[line_start:line_end if line_end > 0 else None]
                stripped = line.strip()
                if _TOC_LINE.search(line) or not stripped:
                    continue
                core = re.sub(r"^[一二三四五六七八九十]+、\s*", "", stripped)
                if len(core) > len(kw) + 6:
                    # 关键词嵌在长句里 —— 是正文里的交叉引用，不是章节标题。
                    # **不退而求其次地采用它**: 那会凭空造出一节内容不对的
                    # 「分析」，比缺失更糟。
                    continue
                # 同一关键词的多处标题式命中取最靠后的 —— 正文在提示与目录之后
                if best is None or i > best[0]:
                    best = (i, line_start, stripped)
        if best is not None:
            return best
    return None


def _extract_from(pages: list[str], page_idx: int, offset: int,
                  max_chars: int) -> tuple[str, bool]:
    """从命中处往后取，遇到下一个同级标题即停。"""
    buf = pages[page_idx][offset:]
    for nxt in pages[page_idx + 1:page_idx + 12]:
        if len(buf) >= max_chars:
            break
        buf += "\n" + nxt

    # 跳过标题本身再找下一个标题，否则会在起点立刻截断
    head_end = buf.find("\n") + 1
    m = _NEXT_HEADING.search(buf, head_end)
    body = buf[:m.start()] if m else buf
    truncated = len(body) > max_chars
    return body[:max_chars].strip(), truncated


def extract(pdf: Path | str | bytes, code: str = "",
            wanted: tuple[str, ...] | None = None,
            max_chars: int = MAX_EXCERPT) -> SectionSet:
    """抽取指定章节的有界摘录。找不到的如实记入 missing。"""
    pages = _page_texts(pdf)
    out = SectionSet(code=code, pages=len(pages))
    for key in (wanted or tuple(SECTION_KEYS)):
        keys = SECTION_KEYS[key]
        found = None
        note = ""
        for skip in range(len(keys)):
            hit = _locate(pages, keys, skip=skip)
            if hit is None:
                break
            idx, off, heading = hit
            text, truncated = _extract_from(pages, idx, off, max_chars)
            if _is_cross_ref(text):
                # 该节只是个指针 —— 换下一个措辞去找真正的内容
                note = "（该公司在此节仅作交叉引用，内容取自同义章节）"
                continue
            if len(text) < 80:
                continue                      # 命中了但内容空洞
            found = Section(
                key=key, label=SECTION_LABELS.get(key, key) + note,
                heading=heading, page=idx + 1, text=text,
                truncated=truncated)
            break
        if found is None:
            out.missing.append(key)
        else:
            out.sections[key] = found
    return out
