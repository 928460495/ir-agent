"""DCF 工作簿的「9_相对估值」页（设计：五）。

追加在「8_蒙特卡洛」之后，不参与 ashare-dcf 的公式引擎验算（验算在建表后、追加前
完成，与蒙特卡洛页同一处理）。可比法的隐含每股价值写成 Excel 公式，读者改倍数能
看到价值跟着变；回归与足球场的数是 Python 算好写入的值。

金额以亿元、股本以亿股显示，所以「倍数 × 亿元 ÷ 亿股」直接得到元/股。美股按 us-dcf 工作簿口径
用百万美元、百万股，得到美元/股（`_units`）。
"""

from __future__ import annotations

from decimal import Decimal

from openpyxl import load_workbook
from openpyxl.chart import BarChart, Reference, ScatterChart, Series
from openpyxl.styles import Alignment, Font, PatternFill



def is_us(code: str) -> bool:
    return False                    # 本仓只做 A 股


from ir_agent.relval import _X_CN as _XNAME
from ir_agent.relval import LABEL, REG_SPEC, RelvalResult, multiples

SHEET = "9_相对估值"
_YI = Decimal(100_000_000)
# 配色沿用 ashare_mc.py
NAVY, LBLUE, GREY = "1F4E79", "D9E1F2", "F2F2F2"
F_HDR = Font(color="FFFFFF", bold=True, size=11)
F_COL = Font(bold=True, size=10)
P_N = PatternFill("solid", fgColor=NAVY)
P_B = PatternFill("solid", fgColor=LBLUE)
P_G = PatternFill("solid", fgColor=GREY)
NUM, X2, PCT = "#,##0.00", "0.00\"x\"", "0.00%"


def _units(res: RelvalResult) -> tuple[Decimal, str, str, str]:
    """(金额除数, 金额单位, 股本单位, 每股单位)。A 股亿元 / 亿股 / 元；美股百万美元 / 百万股 / 美元。"""
    if is_us(res.target.code):
        return Decimal(1_000_000), "百万美元", "百万股", "美元"
    return _YI, "亿元", "亿股", "元"


def _amt(v: Decimal | None, scale: Decimal) -> float | None:
    return None if v is None else float(v / scale)


class _W:
    """逐行往下写的小游标。"""

    def __init__(self, ws):
        self.ws, self.r = ws, 1

    def title(self, text: str) -> None:
        c = self.ws.cell(self.r, 1, text)
        c.font, c.fill = F_HDR, P_N
        for col in range(2, 13):
            self.ws.cell(self.r, col).fill = P_N
        self.r += 1

    def row(self, *vals, fmt: str | None = None, bold: bool = False,
            fill=None) -> int:
        for i, v in enumerate(vals, 1):
            c = self.ws.cell(self.r, i, v)
            is_formula = isinstance(v, str) and v.startswith("=")
            if fmt and ((i > 1 and isinstance(v, (int, float))) or is_formula):
                c.number_format = fmt
            if bold:
                c.font = F_COL
            if fill:
                c.fill = fill
        r, self.r = self.r, self.r + 1
        return r

    def gap(self) -> None:
        self.r += 1


def _header(res: RelvalResult, w: _W) -> None:
    w.title("相对估值 —— 与 DCF 并列交叉验证")
    w.row("估值时点", res.as_of.isoformat())
    w.row("行业（回归样本）", f"{res.industry}，样本 {res.sample_size} 家（不含本司）")
    w.row("口径", "PE = 市值 ÷ 归母净利润（优先 TTM）；PB = 市值 ÷ 归母净资产；"
                  "EV = 市值 + 有息负债 − 现金 + 少数股东权益（与可比坐标表同一口径）")
    if is_us(res.target.code):
        w.row("期间", "存量（净资产、现金、有息负债、少数股东权益）取最近一期余额；"
                      "流量（利润、EBITDA、营收）取 TTM —— 美股每季披露折旧摊销，TTM 可截到任一季。"
                      "各家财年不同，同业按期末日期对齐本司截止季，落后一季的逐家标注；"
                      "算不出时退回期末最近的财年。市值取同一报价日（Tiingo IEX）")
        w.row("NTM", "PE(NTM) = 现价 ÷ NTM EPS（FMP 一致预期，本财年剩余天数加权两个财年）；"
                     "只有本司与可比有一致预期，回归仍用 TTM。亏损公司加用 EV/Sales")
    else:
        w.row("期间", "存量（净资产、现金、有息负债、少数股东权益）取最近一期余额；"
                      "流量（利润、EBITDA、营收增速）取 TTM —— A 股折旧摊销只在中报与年报披露，"
                      "故 EBITDA 的 TTM 截止期只能是中报或年报。同业对齐本司，没出则退一期并标注；"
                      "算不出时退回最近完整财年。市值取同一报价日")
    w.row("剔除规则", "本司、净资产为负、亏损、缺数，以及倍数落在样本 P5–P95 之外的两端")
    w.row("区间", "可比法取同业 P25／中位数／P75；回归取 P10–P90 预测区间（与蒙特卡洛龙卷风同口径）")
    for n in res.sample_notes:
        w.row("样本说明", n)
    w.gap()


def _comps(res: RelvalResult, w: _W) -> None:
    scale, amt, shu, ps = _units(res)
    kinds = list(res.comps)
    ntm = "pe_ntm" in kinds
    w.title("一、可比法（各倍数按理论驱动从行业样本挑可比；不足四家时退回市值近邻）")
    _comp_lists(res, w, kinds, scale)
    w.row("市值近邻名单（分析阶段的可比坐标；驱动可比不足时的退路）", bold=True, fill=P_B)
    w.row("公司", "代码", f"市值（{amt}）", f"归母净利润（{amt}）", f"归母净资产（{amt}）",
          f"EBITDA（{amt}）", *(LABEL[k].replace("PE(TTM)", "PE") for k in kinds), "利润口径", "余额期",
          "EBITDA 口径", *(["NTM 口径"] if ntm else []), bold=True, fill=P_B)
    for p in res.comps_peers:
        m = multiples(p)
        w.row(p.name, p.code, _amt(p.market_cap, scale), _amt(p.net_profit, scale),
              _amt(p.equity_parent, scale), _amt(p.ebitda, scale),
              *(float(m[k]) if m[k] is not None else "—" for k in kinds),
              p.np_basis, p.bs_basis, p.flow_basis, *([p.ntm_basis or "—"] if ntm else []), fmt=NUM)
    w.gap()

    t = res.target
    w.row("本司指标", "", "", bold=True, fill=P_B)
    cells = {}
    rows = [("np", f"归母净利润（{amt}）", t.net_profit, scale),
            ("eq", f"归母净资产（{amt}）", t.equity_parent, scale),
            ("ebitda", f"EBITDA（{amt}）", t.ebitda, scale),
            ("debt", f"有息负债（{amt}）", t.debt, scale),
            ("cash", f"现金（{amt}）", t.cash, scale),
            ("min", f"少数股东权益（{amt}）", t.minority, scale),
            ("sh", f"总股本（{shu}）", res.shares, scale)]
    if "ev_sales" in kinds:
        rows.append(("rev", f"营收 TTM（{amt}）", t.revenue, scale))
    if ntm:
        rows.append(("eps", f"NTM EPS（{ps}/股）", t.eps_ntm, Decimal(1)))
    for key, label, val, sc in rows:
        cells[key] = f"B{w.row(label, _amt(val, sc), fmt=NUM)}"
    w.gap()

    w.row("倍数", "倍数 P25", "倍数 中位数", "倍数 P75", f"每股 P25（{ps}）", f"每股 中位数（{ps}）",
          f"每股 P75（{ps}）", "倍数 调和平均", f"每股 调和平均（{ps}）", bold=True, fill=P_B)
    formula = {"pe": "={m}*{np}/{sh}", "pb": "={m}*{eq}/{sh}",
               "ev_ebitda": "=({m}*{ebitda}-{debt}+{cash}-{min})/{sh}",
               "ev_sales": "=({m}*{rev}-{debt}+{cash}-{min})/{sh}", "pe_ntm": "={m}*{eps}"}
    for kind in kinds:
        b = res.comps.get(kind)
        if b is None:
            why = next((n for n in res.comps_notes if n.startswith(LABEL[kind])), "不适用")
            w.row(LABEL[kind], why)
            continue
        r = w.r
        exprs = [formula[kind].format(m=f"{col}{r}", **cells) for col in "BCD"]
        hm = ([float(b.mult_hm), formula[kind].format(m=f"H{r}", **cells)]
              if b.mult_hm is not None else ["—", "—"])
        w.row(LABEL[kind], float(b.mult_lo), float(b.mult_mid), float(b.mult_hi), *exprs, *hm,
              fmt=NUM)
    w.gap()


def _comp_lists(res: RelvalResult, w: _W, kinds: list[str], scale) -> None:
    """每个倍数实际用的可比名单：业务距离、各驱动取值、基本面距离、该倍数（spec 2026-11-21 §2）。"""
    from ir_agent.relval import _BD_LABEL, DRIVERS, _market

    bd_cn = _BD_LABEL[_market(res.target.code)]
    for kind in kinds:
        peers = res.comps_by_kind.get(kind)
        if not peers:
            continue
        sel = {c: (bd, ds) for c, bd, ds in res.selection.get(kind, [])}
        how = "按驱动挑选" if sel else "市值近邻"
        drv = DRIVERS[kind]
        w.row(f"可比名单：{LABEL[kind]}（{how}）", bold=True, fill=P_B)
        w.row("公司", "代码", "业务距离", *(_XNAME[d] for d in drv), "基本面距离", LABEL[kind], bold=True)
        t = res.target
        w.row("本司", t.code, "—", *(_f(getattr(t, d)) for d in drv), "—",
              _f(multiples(t)[kind]), fmt=NUM)
        for p in peers:
            bd, ds = sel.get(p.code, (None, None))
            w.row(p.name, p.code, bd_cn.get(bd, "—") if bd is not None else "—",
                  *(_f(getattr(p, d)) for d in drv), ds if ds is not None else "—",
                  _f(multiples(p)[kind]), fmt=NUM)
        w.gap()


def _f(v):
    return float(v) if v is not None else "—"


def _regression(res: RelvalResult, w: _W, kind: str) -> tuple | None:
    """一条回归的统计量、本司、样本与剔除。返回散点图要用的单元格区域。"""
    xfs, yk, name = REG_SPEC[kind]
    xf = xfs[0]
    r = res.regs.get(kind)
    w.title(f"二、回归调整：{name}（全行业横截面）")
    if r is None:
        w.row("状态", "未运行")
        w.gap()
        return None
    w.row("状态", "可用" if r.usable else f"不可用 —— {r.why}")
    for label, v, f in (("截距 a", r.a, NUM), ("斜率 b", r.b, NUM), ("R²", r.r2, PCT),
                        ("斜率 t 值", r.t, NUM), ("样本数 n", r.n, "0"),
                        ("残差标准误 s", r.s, NUM)):
        w.row(label, v, fmt=f)
    for d, c in r.coefs.items():
        w.row(f"系数：{_XNAME[d]}", c, fmt=NUM)
        w.row(f"t 值：{_XNAME[d]}", r.ts.get(d), fmt=NUM)
    for d in r.dropped:
        w.row(f"未进回归：{_XNAME[d]}", "样本里没有差异（或与主驱动共线），退化为少一个自变量")
    if r.expanded:
        w.row("样本扩大", f"行业样本不足，扩大到{r.expanded}（用于回归；三级同业不足时也补进可比名单）")
    w.row(f"本司{_XNAME[xf]}", r.x0, fmt=PCT)
    if r.fit_mult is not None:
        w.row("应得倍数", r.fit_mult, fmt=X2)
        w.row("倍数 P10–P90", r.lo_mult, r.hi_mult, fmt=X2)
    if r.usable:
        w.row(f"每股价值（{_units(res)[3]}）P10／中枢／P90", float(r.lo), float(r.fit), float(r.hi), fmt=NUM)
    if r.extrapolated:
        w.row("外推", "本司落在行业样本主体（P5–P95）之外，预测区间因此变宽")
    w.gap()

    w.row("代码", "公司", _XNAME[xf], LABEL[yk], "口径",
          bold=True, fill=P_B)
    r0 = w.r
    for code, nm, x, y in r.sample:
        row = w.row(code, nm, x, y, r.flags.get(code, ""), fmt=NUM)
        w.ws.cell(row, 3).number_format = PCT
    r1 = w.r - 1
    # 拟合线两端（散点图的折线系列）与本司点
    xs = [v[2] for v in r.sample] or [0.0]
    # 多元：拟合线画主驱动的偏效应，其余驱动取样本均值
    a0 = r.a + sum(c * (sum(row[r.xnames.index(d)] for row in r.xsample) / len(r.xsample))
                   for d, c in r.coefs.items() if d != xf and r.xsample and d in r.xnames)
    l0 = w.row("拟合线", "", min(xs), a0 + r.b * min(xs), fmt=NUM)
    l1 = w.row("拟合线", "", max(xs), a0 + r.b * max(xs), fmt=NUM)
    tm = multiples(res.target)[yk]
    tr = w.row("本司", res.target.code, r.x0, float(tm) if tm is not None else None)
    for code, nm, why in r.excluded:
        w.row(code, nm, f"剔除：{why}")
    w.gap()
    if not r.sample:
        return None
    return name, xf, yk, (r0, r1), (l0, l1), tr


def _scatter(ws, spec, anchor: str) -> None:
    name, xf, yk, (r0, r1), (l0, l1), tr = spec
    ch = ScatterChart()
    ch.title = f"{name}（全行业横截面）"
    ch.x_axis.title = _XNAME[xf]
    ch.y_axis.title = LABEL[yk]
    ch.style, ch.height, ch.width = 13, 8, 14
    for (a, b), title, marker, line in (((r0, r1), "行业样本", "circle", False),
                                        ((l0, l1), "拟合线", "none", True),
                                        ((tr, tr), "本司", "diamond", False)):
        s = Series(Reference(ws, min_col=4, min_row=a, max_row=b),
                   Reference(ws, min_col=3, min_row=a, max_row=b), title=title)
        s.marker.symbol = marker
        if not line:
            s.graphicalProperties.line.noFill = True
        ch.series.append(s)
    ws.add_chart(ch, anchor)


def _football(res: RelvalResult, dcf_res, w: _W) -> None:
    w.title(f"三、足球场图：各方法的每股价值区间（{_units(res)[3]}）")
    bars: list[tuple[str, float, float]] = []
    missing: list[str] = []
    sc = getattr(dcf_res, "scenario_ps", None) or {}
    if sc:
        bars.append(("DCF 三档（悲观–乐观）", float(min(sc.values())), float(max(sc.values()))))
    else:
        missing.append("DCF 三档未生成（验算未全部通过或未运行）")
    mc = getattr(dcf_res, "mc", None) or {}
    if mc.get("p5") is not None and mc.get("p95") is not None:
        bars.append(("蒙特卡洛 P5–P95", float(mc["p5"]), float(mc["p95"])))
    else:
        missing.append("蒙特卡洛未生成")
    for kind in res.comps:
        b = res.comps.get(kind)
        if b is None:
            missing.append(f"可比 {LABEL[kind]} 不适用")
        else:
            bars.append((f"可比 {LABEL[kind]}（P25–P75）", float(min(b.lo, b.hi)),
                         float(max(b.lo, b.hi))))
    for kind, (_, _, name) in REG_SPEC.items():
        r = res.regs.get(kind)
        if r is None or not r.usable:
            missing.append(f"回归 {name} 不可用：{r.why if r else '未运行'}")
        else:
            bars.append((f"回归 {name}（P10–P90）", float(r.lo), float(r.hi)))
    p = float(res.price)
    bars.append(("现价", p * 0.995, p * 1.005))

    hdr = w.row("方法", "下端", "宽度", "上端", bold=True, fill=P_B)
    drawn = []
    for label, lo, hi in bars:
        if lo < 0:
            # 堆叠条形图里负值会画到零的另一侧，条的位置就错了 —— 不画，写明原因
            missing.append(f"{label}：下端为负，未画入")
            continue
        drawn.append((label, lo, hi))
    for label, lo, hi in drawn:
        w.row(label, lo, hi - lo, hi, fmt=NUM)
    last = w.r - 1
    for m in missing:
        w.row("未画入", m)

    bc = BarChart()
    bc.type, bc.grouping, bc.overlap = "bar", "stacked", 100
    bc.title = f"每股价值区间（{_units(res)[3]}）—— 「现价」一行为现价位置"
    bc.add_data(Reference(w.ws, min_col=2, max_col=3, min_row=hdr, max_row=last),
                titles_from_data=True)
    bc.set_categories(Reference(w.ws, min_col=1, min_row=hdr + 1, max_row=last))
    bc.series[0].graphicalProperties.noFill = True
    bc.series[0].graphicalProperties.line.noFill = True
    bc.series[1].graphicalProperties.solidFill = NAVY
    bc.legend = None
    bc.height, bc.width = 9, 16
    w.ws.add_chart(bc, f"F{hdr}")


def write_sheet(path: str, res: RelvalResult, dcf_res) -> None:
    """在 DCF 工作簿末尾写（或重写）「9_相对估值」页。"""
    wb = load_workbook(path)
    if SHEET in wb.sheetnames:
        del wb[SHEET]
    ws = wb.create_sheet(SHEET)
    ws.sheet_properties.tabColor = "548235"
    ws.column_dimensions["A"].width = 30
    for col in "BCDEFGHIJKL":
        ws.column_dimensions[col].width = 16
    w = _W(ws)
    _header(res, w)
    _comps(res, w)
    specs = [s for kind in REG_SPEC if (s := _regression(res, w, kind))]
    _football(res, dcf_res, w)
    for s in specs:                         # 每张散点图挂在各自样本表的右侧
        _scatter(ws, s, f"F{s[3][0]}")
    for row in ws.iter_rows():
        for c in row:
            c.alignment = Alignment(vertical="center", wrap_text=False)
    wb.save(path)
