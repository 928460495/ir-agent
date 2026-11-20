"""年报附注里的两张表 —— 非经营性金融资产的实质证据（spec 2026-11-20-nonop-financial-assets）。

「一年内到期的非流动资产」「其他流动资产」是收纳科目：茅台 2025 年报的 268.71 亿「一年内到期的非流动资产」
经附注确认是财务公司的一年内到期的债权投资（金融资产）；别的公司同一科目可能是一年内到期的长期应收款（经营性）。
归类看附注明细，不看科目名。

受限资产按「受限情况」分三类（台账裁定 2026-11-20）：
  · 法定存款准备金 —— 对应已作为负债扣除的吸收存款，保留（投行实务的例外：为偿还已计入债务的负债而设）
  · 定期 / 不能随时支取的存款 —— 只是期限限制，到期可收回，保留
  · 质押、冻结、保证金、担保等权利受限 —— 不计入净现金，扣除

与 `annual_pdf` / `annual_sections` 同一契约：PDF 全文留在模块内，只交出结构化数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

_UNIT = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000),
         "百万元": Decimal(1000000), "亿元": Decimal(100000000)}
_NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
_NCA1Y_HEAD = re.compile(r"\d+\s*[、.]\s*一年内到期的非流动资产")
_RESTR_HEAD = re.compile(r"\d+\s*[、.]\s*所有权或使用权受(?:限|到限制)(?:的)?资产")
# 一年内到期的非流动资产中属于金融资产的明细行
_FIN_NCA1Y = re.compile(r"债权投资|定期存款|大额存单|存单|理财")
# 受限资产表里属于（类）现金 / 金融资产的行 —— 只有这些行的受限金额影响净现金
_FIN_ROWS = ("货币资金", "拆出资金", "买入返售金融资产", "债权投资", "其他债权投资",
             "一年内到期的非流动资产", "交易性金融资产", "存放同业")


@dataclass
class FinNotes:
    nca1y_fin: Decimal | None = None          # 一年内到期的非流动资产中属于金融资产的期末金额（元）
    nca1y_total: Decimal | None = None
    statutory_reserve: Decimal | None = None  # 受限资产表里「法定存款准备金」合计（元）
    term_restricted: dict = field(default_factory=dict)   # {科目: 金额}，定期 / 不能随时支取
    encumbered: dict = field(default_factory=dict)        # {科目: 金额}，质押 / 冻结 / 保证金等
    missing: list = field(default_factory=list)


def _num(s: str) -> Decimal | None:
    s = s.strip()
    if not _NUM.match(s):
        return None
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def _unit(block: str) -> Decimal:
    m = re.search(r"单位\s*[:：]\s*(百万元|千元|万元|亿元|元)", block)
    return _UNIT[m.group(1)] if m else Decimal(1)


def _block(pages: list[str], head: re.Pattern, must: str) -> str | None:
    """从标题到下一个同级附注标题（「数字、」开头）为止，表格可能跨页、最多连带下一页。

    标题之后必须真有这张表的表头（`must`）—— 年报正文里有「详见……注释27、所有权或使用权受限资产」
    这样的交叉引用，它匹配标题却不是表。"""
    for i, t in enumerate(pages):
        for m in head.finditer(t):
            text = t[m.start():] + ("\n" + pages[i + 1] if i + 1 < len(pages) else "")
            nxt = re.search(r"\n\d+\s*、", text[m.end() - m.start():])
            blk = text[: (m.end() - m.start()) + nxt.start()] if nxt else text
            if must in blk:
                return blk
    return None


def _lines(block: str) -> list[str]:
    return [x.strip() for x in block.splitlines() if x.strip()]


def _nca1y(block: str, n: FinNotes) -> None:
    k = _unit(block)
    lines = _lines(block)
    try:
        start = next(i for i, x in enumerate(lines) if "期初余额" in x or x == "期初") + 1
    except StopIteration:
        n.missing.append("一年内到期的非流动资产：附注表头读不出")
        return
    fin, total, label = Decimal(0), None, None
    for x in lines[start:]:
        v = _num(x)
        if v is None:
            label = x
            continue
        if label is None:
            continue
        if label.startswith("合计"):
            total = v * k
            break
        if _FIN_NCA1Y.search(label):
            fin += v * k
        label = None                       # 只取紧跟标签的第一个数（期末）
    n.nca1y_fin = fin
    n.nca1y_total = total


def _restricted(block: str, n: FinNotes) -> None:
    k = _unit(block)
    lines = _lines(block)
    try:
        start = max(i for i, x in enumerate(lines) if x.startswith("受限情况")) + 1
    except ValueError:
        n.missing.append("受限资产：附注表头读不出")
        return
    # 记录 = 标签 + 期末(余额, 账面价值) + 期末(类型, 情况) + 期初(余额, 账面价值) + 期初(类型, 情况)
    recs: list[tuple[str, list]] = []
    body = lines[start:]
    for j, x in enumerate(body):
        if x.startswith("合计"):
            break
        v = _num(x)
        cur = recs[-1] if recs else None
        if v is None:
            # 新记录的标签：紧跟着就是数字（账面余额），且上一条已读满 4 个数或还没有记录。
            # 期初「受限类型 / 受限情况」也出现在 4 个数之后，但它们后面不是紧跟数字 —— 不能当标签。
            nxt_is_num = j + 1 < len(body) and _num(body[j + 1]) is not None
            if nxt_is_num and (cur is None or len([t for t in cur[1] if isinstance(t, Decimal)]) >= 4):
                recs.append((x, []))
            elif cur is not None:
                cur[1].append(x)
        elif cur is not None:
            cur[1].append(v)
    n.statutory_reserve = Decimal(0)
    for label, toks in recs:
        nums = [t for t in toks if isinstance(t, Decimal)]
        if len(nums) < 2 or not any(label.startswith(r) for r in _FIN_ROWS):
            continue
        # 期末账面价值后、期初余额前的文字 = 期末的受限类型 + 受限情况（PDF 折行，拼回去）
        i2 = toks.index(nums[1])
        i3 = toks.index(nums[2]) if len(nums) > 2 else len(toks)
        reason = "".join(t for t in toks[i2 + 1:i3] if isinstance(t, str))
        amt = nums[1] * k
        if "法定" in reason and "准备金" in reason:
            n.statutory_reserve += amt
        elif "不能随时支取" in reason or "定期" in reason:
            n.term_restricted[label] = n.term_restricted.get(label, Decimal(0)) + amt
        else:
            n.encumbered[label] = n.encumbered.get(label, Decimal(0)) + amt


def extract_fin_notes(pages: list[str]) -> FinNotes:
    """`pages`：年报逐页文字。读不到的表如实记进 `missing`，对应字段保持 None —— 下游保守处理。"""
    n = FinNotes()
    b = _block(pages, _NCA1Y_HEAD, "期末余额")
    if b is None:
        n.missing.append("一年内到期的非流动资产：年报附注里没找到明细表")
    else:
        _nca1y(b, n)
    b = _block(pages, _RESTR_HEAD, "受限情况")
    if b is None:
        n.missing.append("受限资产：年报附注里没找到「所有权或使用权受限资产」表")
    else:
        _restricted(b, n)
    return n


def extract_fin_notes_from_pdf(pdf: Path | str | bytes) -> FinNotes:
    from ir_agent.sources.annual_sections import _page_texts
    return extract_fin_notes(_page_texts(pdf))
