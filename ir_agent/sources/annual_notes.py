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
    # 管理层讨论与分析「研发投入」表（spec 2026-11-24-rd-capitalization §4.6）：金额元，比例为小数
    rd_total: Decimal | None = None
    rd_capitalized: Decimal | None = None
    rd_cap_ratio: Decimal | None = None
    rd_note: str = ""                         # 研发投入表读到了但未采用时的说明
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


# 受限资产表的科目名（记录锚点）。按科目名分记录，不靠数数 —— 期初为空的行会让「读满 4 个数」的数法
# 吞掉下一条记录（终审 I4）。资产负债表科目名多以这些字结尾；受限原因（质押、保证金……）不会。
_ASSET_LABEL = re.compile(r"^(?:[一-龥（）()]{1,16}(?:资产|资金|投资|票据|账款|存货|工程|款项|房地产|使用权|"
                          r"款|权益|存单|存款))$")
# 分类顺序（终审 I4）：先判权利受限（「定期存单质押」是质押，不是定期），再判准备金（「存款准备金」「存放中央
# 银行准备金」不一定带「法定」二字），最后才是期限限制；都不是 → 按权利受限保守扣除。
_ENCUMBER = ("质押", "冻结", "保证金", "担保", "抵押", "诉讼", "查封", "司法", "监管")


def _classify(reason: str) -> str:
    if any(k in reason for k in _ENCUMBER):
        return "encumbered"
    if "准备金" in reason or "存放中央银行" in reason:
        return "statutory"
    if "不能随时支取" in reason or "定期" in reason:
        return "term"
    return "encumbered"


def _restricted(block: str, n: FinNotes) -> None:
    k = _unit(block)
    lines = _lines(block)
    try:
        start = max(i for i, x in enumerate(lines) if x.startswith("受限情况")) + 1
    except ValueError:
        n.missing.append("受限资产：附注表头读不出")
        return
    body = []
    for x in lines[start:]:
        if x.startswith("合计"):
            break
        body.append(x)
    # 记录 = 科目名（后面紧跟数字）+ 期末(余额, 账面价值) + 期末(类型, 情况) + [期初(余额, 账面价值, 类型, 情况)]
    recs: list[tuple[str, list]] = []
    for j, x in enumerate(body):
        v = _num(x)
        if v is None and _ASSET_LABEL.match(x) and j + 1 < len(body) and _num(body[j + 1]) is not None:
            recs.append((x, []))
        elif recs:
            recs[-1][1].append(v if v is not None else x)
    n.statutory_reserve = Decimal(0)
    for label, toks in recs:
        pos = [i for i, t in enumerate(toks) if isinstance(t, Decimal)]   # 按位置，不按值（余额常与账面价值相等）
        if len(pos) < 2 or not any(label.startswith(r) for r in _FIN_ROWS):
            continue
        # 期末账面价值之后、下一个数（期初余额）之前的文字 = 期末的受限类型 + 受限情况（PDF 折行，拼回去）
        i3 = pos[2] if len(pos) > 2 else len(toks)
        reason = "".join(t for t in toks[pos[1] + 1:i3] if isinstance(t, str))
        amt = toks[pos[1]] * k
        kind = _classify(reason)
        if kind == "statutory":
            n.statutory_reserve += amt
        elif kind == "term":
            n.term_restricted[label] = n.term_restricted.get(label, Decimal(0)) + amt
        else:
            n.encumbered[label] = n.encumbered.get(label, Decimal(0)) + amt


# 研发投入表的两种版式：深市（研发投入金额（元）/ 研发投入资本化的金额（元）/ 资本化研发投入占研发投入的比例）、
# 沪市（本期费用化研发投入 / 本期资本化研发投入 / 研发投入合计 / 研发投入资本化的比重（%））。
# PDF 抽文字后可能逐行（标签、值、标签、值）也可能按列（先全部标签、后全部值）—— 两种都要读对（终审 I2）。
_RD_TOTAL = ("研发投入金额（元）", "研发投入合计")
_RD_CAP = ("研发投入资本化的金额（元）", "本期资本化研发投入")
_RD_RATIO = ("资本化研发投入占研发投入的比例", "研发投入资本化的比重（%）")
_RD_ROWS = ("研发投入金额（元）", "研发投入占营业收入比例", "研发投入资本化的金额（元）",
            "资本化研发投入占研发投入的比例", "资本化研发投入占当期净利润的比重",
            "本期费用化研发投入", "本期资本化研发投入", "研发投入合计", "研发投入总额占营业收入比例（%）",
            "研发投入资本化的比重（%）")
_TOKEN = re.compile(r"-?[\d,]+(?:\.\d+)?%?")
_UNIT_AT = re.compile(r"单位\s*[:：]\s*(百万元|千元|万元|亿元|元)")
_RATIO_TOL = Decimal("0.005")


def _rd_values(text: str) -> dict:
    """{标签: 本期数（原文字符串）}。逐行排布取标签与下一个标签之间的第一个数；按列排布（标签之间没有数）
    则把最后一个标签之后的数依次配给各标签。"""
    pos = sorted((text.find(l), l) for l in _RD_ROWS if text.find(l) >= 0)
    if not pos:
        return {}
    out = {}
    for k, (at, lab) in enumerate(pos):
        end = pos[k + 1][0] if k + 1 < len(pos) else at + len(lab) + 200
        m = _TOKEN.search(text, at + len(lab), end)
        if m:
            out[lab] = m.group(0)
    tail = text[pos[-1][0] + len(pos[-1][1]):]
    toks = _TOKEN.findall(tail)
    if len(out) < len(pos) and len(toks) >= len(pos):      # 有标签之间没有数 → 按列排布
        return {lab: toks[k] for k, (_, lab) in enumerate(pos)}
    return out


def _rd_unit(text: str, label: str) -> Decimal:
    """标签自带（元）时就是元；否则取标签**之前**最近的「单位：」—— 同页别的表的「万元」不能带偏（终审 I2）。"""
    if "（元）" in label:
        return Decimal(1)
    at = text.find(label)
    ms = list(_UNIT_AT.finditer(text, 0, at if at >= 0 else len(text)))
    return _UNIT[ms[-1].group(1)] if ms else Decimal(1)


def _rd(pages: list[str], n: FinNotes) -> None:
    text = next((p for p in pages if any(l in p for l in _RD_TOTAL + _RD_CAP)), None)
    if text is None:
        # 不进 missing：missing 是「附注缺失 → 相应口径按保守处理」，研发投入表缺失只是资本化率不披露（字段留 None）
        return
    vals = _rd_values(text)

    def amount(labels) -> Decimal | None:
        for lab in labels:
            if lab in vals and _num(vals[lab].rstrip("%")) is not None:
                return _num(vals[lab].rstrip("%")) * _rd_unit(text, lab)
        return None

    def ratio() -> Decimal | None:
        for lab in _RD_RATIO:
            if lab in vals and _num(vals[lab].rstrip("%")) is not None:
                return _num(vals[lab].rstrip("%")) / Decimal(100)        # 表里的比例是百分数
        return None

    tot, cap, rat = amount(_RD_TOTAL), amount(_RD_CAP), ratio()
    bad = ((tot is not None and tot <= 0) or (cap is not None and cap < 0)
           or (tot is not None and cap is not None and cap > tot)
           or (rat is not None and not (Decimal(0) <= rat <= Decimal(1))))
    if not bad and tot and cap is not None and rat is not None and abs(cap / tot - rat) > _RATIO_TOL:
        bad = True
    if bad:
        n.rd_note = "年报研发投入表：资本化比例与金额推算的比例不一致或越界，未采用（资本化率记为未取到）"
        return
    n.rd_total, n.rd_capitalized = tot, cap
    n.rd_cap_ratio = rat if rat is not None else (cap / tot if tot and cap is not None else None)


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
    _rd(pages, n)
    return n


def extract_fin_notes_from_pdf(pdf: Path | str | bytes) -> FinNotes:
    from ir_agent.sources.annual_sections import _page_texts
    return extract_fin_notes(_page_texts(pdf))
