"""Excel 导出 —— 活公式，不是死数字。

若三张表与比率全写成硬编码值，工作簿就只是一张打印件: 改一个输入不会有
任何反应，勾稽也无法重算，「可复用」是空话。因此:

  · **REPORTED 事实写字面值** —— 它们是输入，来自数据源，不该由公式生成
  · **比率、勾稽、可比一律写 Excel 公式**，引用三表单元格
  · **溯源不在导出环节丢失** —— 每个字面值都有对应的 source_id 与披露日

「假设」表现在是空的（DCF 尚未接线），但结构先留好: 事后再往一个已经成型
的工作簿里塞假设区，等于重做。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ir_agent.ledger import FactLedger, LookAheadError

SHEETS = ("说明", "利润表", "资产负债表", "现金流量表",
          "比率", "勾稽校验", "假设", "溯源")

# 每张表的行: (账本 key, 中文行标签)
_INCOME = [("revenue", "营业收入"), ("cost_of_revenue", "营业成本"),
           ("net_profit", "净利润"),
           ("net_profit_attr_parent", "归属于母公司所有者的净利润"),
           ("minority_interest_profit", "少数股东损益")]
_BALANCE = [("total_assets", "资产总计"), ("total_liabilities", "负债合计"),
            ("total_equity", "所有者权益合计"),
            ("equity_attr_parent", "归属于母公司所有者权益"),
            ("minority_equity", "少数股东权益")]
_CASHFLOW = [("cf_net_profit", "净利润"), ("cash_begin", "期初现金及现金等价物"),
             ("cash_net_change", "现金及现金等价物净增加额"),
             ("cash_end", "期末现金及现金等价物")]

_STATEMENTS = [("利润表", _INCOME), ("资产负债表", _BALANCE),
               ("现金流量表", _CASHFLOW)]

_HEAD = Font(bold=True, color="FFFFFF")
_HEAD_BG = PatternFill("solid", fgColor="3D4A58")
_LABEL = Font(bold=True)
_BOX = Border(*[Side(style="thin", color="D2D8DE")] * 4)


@dataclass
class _Ref:
    """某个 key 在工作簿中的位置，供公式引用。"""
    sheet: str
    row: int

    def cell(self, col: int = 2) -> str:
        return f"{self.sheet}!{get_column_letter(col)}{self.row}"


def _style_header(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font, cell.fill = _HEAD, _HEAD_BG
        cell.alignment = Alignment(horizontal="center")


def _autosize(ws, widths: dict[int, int]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def build_workbook(
    ledger: FactLedger,
    code: str,
    period: str,
    as_of: date,
    path: Path | str,
    comps=None,
    verdicts: dict | None = None,
) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    refs: dict[str, _Ref] = {}
    provenance: list[tuple] = []

    def get(key: str):
        try:
            return ledger.get(key, period, as_of=as_of)
        except (KeyError, LookAheadError):
            return None

    # ── 三张表: REPORTED 事实写字面值 ──────────────────────
    for sheet_name, rows in _STATEMENTS:
        ws = wb.create_sheet(sheet_name)
        ws.append(["项目", period, "单位", "披露日"])
        _style_header(ws, 1, 4)
        for key, label in rows:
            f = get(key)
            ws.append([label,
                       float(f.value) if f else None,
                       f.unit if f else "",
                       f.as_of.isoformat() if f else "数据缺失"])
            ws.cell(row=ws.max_row, column=1).font = _LABEL
            ws.cell(row=ws.max_row, column=2).number_format = "#,##0.00"
            refs[key] = _Ref(sheet_name, ws.max_row)
            if f:
                provenance.append((key, label, sheet_name, ws.max_row,
                                   str(f.value), f.source_id,
                                   f.as_of.isoformat(), f.method.value))
        _autosize(ws, {1: 30, 2: 20, 3: 8, 4: 14})

    def ref(key: str) -> str | None:
        r = refs.get(key)
        return r.cell() if r and get(key) is not None else None

    # ── 比率: 一律公式 ────────────────────────────────────
    ws = wb.create_sheet("比率")
    ws.append(["指标", "值", "公式含义"])
    _style_header(ws, 1, 3)
    ratios = [
        ("毛利率", ("revenue", "cost_of_revenue"),
         lambda r, c: f"=({r}-{c})/{r}", "（营业收入−营业成本）÷ 营业收入", "0.00%"),
        ("净利率", ("net_profit_attr_parent", "revenue"),
         lambda p, r: f"={p}/{r}", "归母净利润 ÷ 营业收入", "0.00%"),
        ("ROE", ("net_profit_attr_parent", "equity_attr_parent"),
         lambda p, e: f"={p}/{e}", "归母净利润 ÷ 归母权益", "0.00%"),
        ("总资产周转率", ("revenue", "total_assets"),
         lambda r, a: f"={r}/{a}", "营业收入 ÷ 资产总计", "0.00"),
        ("权益乘数", ("total_assets", "equity_attr_parent"),
         lambda a, e: f"={a}/{e}", "资产总计 ÷ 归母权益", "0.00"),
        ("资产负债率", ("total_liabilities", "total_assets"),
         lambda l, a: f"={l}/{a}", "负债合计 ÷ 资产总计", "0.00%"),
    ]
    for label, keys, make, meaning, fmt in ratios:
        cs = [ref(k) for k in keys]
        ws.append([label, make(*cs) if all(cs) else "数据缺失", meaning])
        ws.cell(row=ws.max_row, column=1).font = _LABEL
        if all(cs):
            ws.cell(row=ws.max_row, column=2).number_format = fmt
    _autosize(ws, {1: 18, 2: 16, 3: 34})

    # ── 勾稽校验: 公式 + 可见的通过/失败 ──────────────────
    ws = wb.create_sheet("勾稽校验")
    ws.append(["校验项", "差额", "结论", "恒等式"])
    _style_header(ws, 1, 4)
    checks = [
        ("资产=负债+所有者权益",
         ("total_assets", "total_liabilities", "total_equity"),
         lambda a, l, e: f"={a}-{l}-{e}", "资产总计 − 负债合计 − 所有者权益合计"),
        ("净利润=归母+少数股东损益",
         ("net_profit", "net_profit_attr_parent", "minority_interest_profit"),
         lambda n, p, m: f"={n}-{p}-{m}", "净利润 − 归母 − 少数股东损益"),
        ("期末现金=期初+净增加额",
         ("cash_end", "cash_begin", "cash_net_change"),
         lambda e, b, c: f"={e}-{b}-{c}", "期末 − 期初 − 净增加额"),
        ("利润表净利润=现金流量表起点",
         ("net_profit", "cf_net_profit"),
         lambda a, b: f"={a}-{b}", "利润表净利润 − 现金流量表净利润"),
    ]
    for label, keys, make, ident in checks:
        cs = [ref(k) for k in keys]
        if all(cs):
            ws.append([label, make(*cs), None, ident])
            r = ws.max_row
            ws.cell(row=r, column=2).number_format = "#,##0.00"
            ws.cell(row=r, column=3).value = (
                f'=IF(ABS(B{r})<=0.01,"✓ 通过","✗ 失败 —— 检查上游数据")')
        else:
            ws.append([label, None, "– 跳过（数据缺失）", ident])
        ws.cell(row=ws.max_row, column=1).font = _LABEL
    _autosize(ws, {1: 30, 2: 16, 3: 26, 4: 34})

    # ── 假设: DCF 尚未接线，先留结构 ──────────────────────
    ws = wb.create_sheet("假设")
    ws.append(["假设项", "取值", "依据 / 来源", "审核状态"])
    _style_header(ws, 1, 4)
    for label, note in [
        ("折现率 WACC", "需人工填写并说明依据"),
        ("永续增长率 g", "通常不超过长期名义 GDP 增速"),
        ("预测期（年）", "常见 5–10 年"),
        ("营收增速（逐年）", "应与行业分析结论一致"),
        ("目标营业利润率", "应与历史区间及同业对比一致"),
        ("资本开支 / 营收", "参考历史与产能规划"),
    ]:
        ws.append([label, None, note, "未审核"])
        ws.cell(row=ws.max_row, column=1).font = _LABEL
    ws.append([])
    ws.append(["注", "假设未经人工审核前不得据此出具估值结论。"
                     "DCF 计算表将在假设来源确定后接入。"])
    _autosize(ws, {1: 22, 2: 16, 3: 40, 4: 12})

    # ── 溯源 ──────────────────────────────────────────────
    ws = wb.create_sheet("溯源")
    ws.append(["字段", "行标签", "所在表", "行号", "数值", "source_id", "披露日", "方法"])
    _style_header(ws, 1, 8)
    for row in provenance:
        ws.append(list(row))
    _autosize(ws, {1: 24, 2: 28, 3: 14, 4: 8, 5: 20, 6: 34, 7: 14, 8: 10})

    # ── 可比（可选）────────────────────────────────────────
    if comps is not None:
        ws = wb.create_sheet("可比")
        ws.append(["可比理由", getattr(comps, "rationale", "")])
        ws.append([])
        metrics = sorted({k for r in comps.rows for k in r.metrics})
        ws.append(["公司", "代码", *[_cn(m) for m in metrics]])
        _style_header(ws, 3, 2 + len(metrics))
        for r in comps.rows:
            ws.append([r.name, r.code,
                       *[float(r.metrics[m]) if m in r.metrics else None
                         for m in metrics]])
        for e in comps.excluded:
            ws.append([e.name, e.code, f"已排除: {e.reason}"])
        _autosize(ws, {1: 18, 2: 12})

    # ── 说明（放最前）──────────────────────────────────────
    ws = wb.create_sheet("说明", 0)
    ws.append(["项目", "内容"])
    _style_header(ws, 1, 2)
    meta = [("证券代码", code), ("报告期", period),
            ("分析时点 as_of", as_of.isoformat()),
            ("生成时间", date.today().isoformat()),
            ("数字口径", "三张表为数据源原值；比率与勾稽为 Excel 公式，改输入即联动"),
            ("溯源", "见「溯源」表，每个数字可反查 source_id 与披露日"),
            ("估值", "DCF 尚未接入 —— 假设来源确定后再建模，见「假设」表")]
    if verdicts:
        for k, v in verdicts.items():
            meta.append((f"原文裁定 {k}", v.describe()))
    for a, b in meta:
        ws.append([a, b])
        ws.cell(row=ws.max_row, column=1).font = _LABEL
    _autosize(ws, {1: 20, 2: 72})

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _cn(metric: str) -> str:
    from ir_agent.comps import label
    return label(metric)
