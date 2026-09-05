"""年报 PDF 提取器 —— 两源分歧的裁决依据。

为什么需要它: 接口给的数字未必对应它声称的公告日。中国神华 2025FY，
东财给 903,830 百万并标注 NOTICE_DATE=2026-03-31，而 2026-03-31 发布的
年报原文写的是 627,761 百万 —— 东财供的是**重述后**数字却挂了**原始公告日**，
等于在数据源层面内建了未来函数。这类分歧只有回到公告原文才能裁定。

上下文隔离: 本模块只把**结构化结论**返回给调用方，PDF 全文（神华 2025 年报
310 页、5.8MB）始终留在本模块内部。V1 的影子 Agent 会沿用同一契约 ——
它替换的是「怎么在 PDF 里找到那一页」，而不是「谁持有全文」。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from ir_agent.sources.retry import with_retry

TIMEOUT = 120
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# 合并报表才是我们要的口径。母公司报表数值完全不同，混用会造成量级错误。
_CONSOLIDATED = re.compile(r"合并资产负债表")
_PARENT_ONLY = re.compile(r"母公司资产负债表")

_UNITS = [
    (re.compile(r"单位[：:]\s*(?:人民币)?\s*百万元"), Decimal("1000000")),
    (re.compile(r"单位[：:]\s*(?:人民币)?\s*千元"), Decimal("1000")),
    (re.compile(r"单位[：:]\s*(?:人民币)?\s*万元"), Decimal("10000")),
    (re.compile(r"单位[：:]\s*(?:人民币)?\s*元"), Decimal("1")),
]

# 年报的行标签与接口又不一样:「归属于**本**公司股东权益合计」。
_LABELS: dict[str, tuple[str, ...]] = {
    "total_assets": ("资产总计", "资产合计"),
    "total_liabilities": ("负债合计",),
    "total_equity": ("股东权益合计", "所有者权益合计",
                     "所有者权益(或股东权益)合计"),
    "equity_attr_parent": ("归属于本公司股东权益合计",
                           "归属于母公司股东权益合计",
                           "归属于母公司所有者权益合计"),
    "minority_equity": ("少数股东权益",),
}

_YEAR = re.compile(r"^(20\d{2})\s*年$")
_AMOUNT = re.compile(r"^[（(]?-?[\d,]+(?:\.\d+)?[)）]?$")


class ExtractionFailed(Exception):
    """无法从 PDF 中可靠提取 —— 宁可报错，不猜。"""


def parse_unit(text: str) -> Decimal:
    for pattern, mult in _UNITS:
        if pattern.search(text):
            return mult
    raise ExtractionFailed(
        "未找到金额单位声明。猜错量级会让数字差 6 个数量级，"
        "而三表勾稽照样通过（分子分母同比例）—— 拒绝猜测。"
    )


def parse_amount(raw: str) -> Decimal | None:
    s = raw.strip()
    if not s or not _AMOUNT.match(s):
        return None
    negative = s[0] in "（(" and s[-1] in ")）"
    s = s.strip("（()）").replace(",", "")
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if negative else v


def column_years(text: str) -> list[int]:
    """按出现顺序返回各数据列对应的年份。「(已重述）」不是一列。"""
    out: list[int] = []
    for line in text.splitlines():
        m = _YEAR.match(line.strip())
        if m:
            y = int(m.group(1))
            if y not in out:
                out.append(y)
    return out


def extract_rows(text: str, keys: list[str]) -> dict[str, list[Decimal]]:
    """从一页合并资产负债表文本中提取指定字段的各列数值（已换算为元）。"""
    if _PARENT_ONLY.search(text):
        raise ExtractionFailed("这是母公司资产负债表，不是合并报表 —— 拒绝使用。")
    if not _CONSOLIDATED.search(text):
        raise ExtractionFailed("未在该页找到「合并资产负债表」标题。")

    mult = parse_unit(text)
    n_cols = max(len(column_years(text)), 1)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    wanted = {alias: key for key in keys
              for alias in _LABELS.get(key, ())}

    out: dict[str, list[Decimal]] = {}
    for i, line in enumerate(lines):
        key = wanted.get(line)
        if key is None or key in out:
            continue
        vals: list[Decimal] = []
        for nxt in lines[i + 1:]:
            v = parse_amount(nxt)
            if v is None:
                break
            vals.append(v * mult)
            if len(vals) == n_cols:
                break
        if vals:
            out[key] = vals
    return out


def _download(url: str, cache_dir: Path | str | None = None) -> bytes:
    if cache_dir is not None:
        cache = Path(cache_dir) / (url.rsplit("/", 1)[-1] or "report.pdf")
        if cache.exists():
            return cache.read_bytes()
    data = with_retry(lambda: _get(url))
    if cache_dir is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
    return data


def _get(url: str) -> bytes:
    r = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def fetch_and_extract(
    url: str,
    keys: list[str],
    cache_dir: Path | str | None = None,
) -> dict[str, list[Decimal]]:
    """下载年报并提取合并资产负债表字段。

    只返回结构化结果 —— PDF 全文不离开本函数。
    """
    import fitz                                   # PyMuPDF

    data = _download(url, cache_dir)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        best: dict[str, list[Decimal]] = {}
        for page in doc:
            text = page.get_text()
            if not _CONSOLIDATED.search(text) or _PARENT_ONLY.search(text):
                continue
            try:
                rows = extract_rows(text, keys)
            except ExtractionFailed:
                continue
            for k, v in rows.items():
                best.setdefault(k, v)               # 首个命中的页获胜
            if len(best) == len(keys):
                break
    finally:
        doc.close()

    if not best:
        raise ExtractionFailed(f"未能在该 PDF 中定位合并资产负债表: {url}")
    return best
