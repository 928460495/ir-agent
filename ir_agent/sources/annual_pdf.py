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
from enum import Enum
from pathlib import Path

import requests

from ir_agent.sources.retry import with_retry

TIMEOUT = 120
_HEADERS = {"User-Agent": "Mozilla/5.0"}

class StatementKind(str, Enum):
    BALANCE = "balance"
    INCOME = "income"
    CASHFLOW = "cashflow"


# 合并报表才是我们要的口径。母公司报表数值完全不同，混用会造成量级错误。
_KIND_PATTERNS = [
    (StatementKind.BALANCE, re.compile(r"资产负债表")),
    (StatementKind.INCOME, re.compile(r"利润表")),
    (StatementKind.CASHFLOW, re.compile(r"现金流量表")),
]
_CONSOLIDATED = re.compile(r"合并(?:资产负债表|利润表|现金流量表)")
_PARENT_ONLY = re.compile(r"母公司(?:资产负债表|利润表|现金流量表)")

# 附注编号列夹在标签与数值之间（'五、35'、'五、47(1)'），扫描时必须跳过。
# 判别关键: 「、」后面是数字才是附注，是中文就是行标签（'一、营业收入'）。
_NOTE_REF = re.compile(r"^[一二三四五六七八九十]+、[\d()（）.\-]+$")

# 行标签前缀: 序号、加/减/其中。「其中：」不剥 —— 它标记从属行。
_PDF_PREFIX = re.compile(r"^\s*(?:[一二三四五六七八九十]+、|[加减][：:])\s*")


def is_note_ref(line: str) -> bool:
    return bool(_NOTE_REF.match(line.strip()))


def normalize_pdf_label(label: str) -> str:
    prev, out = None, label.strip()
    while prev != out:
        prev, out = out, _PDF_PREFIX.sub("", out, count=1).strip()
    return out


def detect_statement(text: str) -> StatementKind | None:
    """返回合并报表类型；母公司报表或无法识别时返回 None。"""
    if _PARENT_ONLY.search(text) or not _CONSOLIDATED.search(text):
        return None
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(text):
            return kind
    return None

_UNITS = [
    (re.compile(r"单位[：:]\s*(?:人民币)?\s*百万元"), Decimal("1000000")),
    (re.compile(r"单位[：:]\s*(?:人民币)?\s*千元"), Decimal("1000")),
    (re.compile(r"单位[：:]\s*(?:人民币)?\s*万元"), Decimal("10000")),
    (re.compile(r"单位[：:]\s*(?:人民币)?\s*元"), Decimal("1")),
]

# 年报的行标签与接口又不一样:「归属于**本**公司股东权益合计」。
_LABELS: dict[StatementKind, dict[str, tuple[str, ...]]] = {
    StatementKind.BALANCE: {
        "total_assets": ("资产总计", "资产合计"),
        "total_liabilities": ("负债合计",),
        "total_equity": ("股东权益合计", "所有者权益合计",
                         "所有者权益(或股东权益)合计"),
        "equity_attr_parent": ("归属于本公司股东权益合计",
                               "归属于母公司股东权益合计",
                               "归属于母公司所有者权益合计"),
        "minority_equity": ("少数股东权益",),
    },
    StatementKind.INCOME: {
        "revenue": ("营业收入",),
        "revenue_total": ("营业总收入",),
        "cost_of_revenue": ("营业成本",),
        "net_profit": ("净利润",),
        "net_profit_attr_parent": ("归属于本公司股东的净利润",
                                   "归属于母公司股东的净利润",
                                   "归属于母公司所有者的净利润"),
        "minority_interest_profit": ("少数股东损益",),
    },
    StatementKind.CASHFLOW: {
        # 净额标签随正负变化: 增加时「净增加额」，减少时「净减少额」。
        # 只认一个会在半数公司上失配。
        "cash_net_change": ("现金及现金等价物净增加额",
                            "现金及现金等价物净减少额",
                            "现金及现金等价物的净增加额",
                            "现金及现金等价物的净减少额"),
        # 年报用「年初/年末」，中报用「期初/期末」。
        "cash_begin": ("年初现金及现金等价物余额", "期初现金及现金等价物余额"),
        "cash_end": ("年末现金及现金等价物余额", "期末现金及现金等价物余额"),
        "cf_net_profit": ("净利润",),
    },
}

_YEAR = re.compile(r"^(20\d{2})\s*年(?:度)?$")
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


def extract_rows(text: str, keys: list[str]) -> dict[str, dict[int, Decimal]]:
    """从一页合并报表文本中提取指定字段的各列数值（已换算为元）。

    返回 {字段: {年份: 金额}} —— **按年份而非列位置**。
    年报末尾的「五年财务摘要」列序是从旧到新，主表是从新到旧；
    按位置取值会在摘要页上取到最早那一年（神华实测取到了 2021 年）。

    只取该页所属报表类型下的字段 ——「净利润」在利润表与现金流量表都出现，
    不按表区分会让跨表勾稽变成自己跟自己比。
    """
    if _PARENT_ONLY.search(text):
        raise ExtractionFailed("这是母公司报表，不是合并报表 —— 拒绝使用。")
    kind = detect_statement(text)
    if kind is None:
        raise ExtractionFailed("未在该页找到合并报表标题。")

    mult = parse_unit(text)
    years = column_years(text)
    if not years:
        raise ExtractionFailed("未能从表头识别出年份列，无法按年归属数值。")
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    table = _LABELS[kind]
    wanted = {alias: key for key in keys
              for alias in table.get(key, ())}

    out: dict[str, dict[int, Decimal]] = {}
    for i, line in enumerate(lines):
        key = wanted.get(normalize_pdf_label(line))
        if key is None or key in out:
            continue
        vals: list[Decimal] = []
        for nxt in lines[i + 1:]:
            if is_note_ref(nxt):
                continue                    # 附注编号列，跳过继续找数值
            v = parse_amount(nxt)
            if v is None:
                break
            vals.append(v * mult)
            if len(vals) == len(years):
                break
        if vals:
            out[key] = dict(zip(years, vals))
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
) -> dict[str, dict[int, Decimal]]:
    """下载年报并提取合并资产负债表字段。

    只返回结构化结果 —— PDF 全文不离开本函数。
    """
    import fitz                                   # PyMuPDF

    data = _download(url, cache_dir)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        best: dict[str, dict[int, Decimal]] = {}
        for page in doc:
            text = page.get_text()
            if detect_statement(text) is None:
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
