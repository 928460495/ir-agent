"""接 ashare-dcf —— 估值交给专门的三表 DCF 建模系统。

**为什么不自己算。** 本项目原先那套 DCF 只有 fcf_base、增速、WACC、永续
四个量，三张表压成几个汇总项，毛利率、费用率、资本开支率这些驱动因子根本
没有落脚处 —— 战略分析识别出的六条不确定性里有三条接不上，情景分析只能
在三个旋钮上打转。

ashare-dcf 是完整的三表联动模型: 逐科目预测、资产负债表强制平衡、
**用公式引擎实跑验算**（不是自己算一遍对答案，是把 xlsx 交给 formulas
引擎跑出来比对）、三档情景、蒙特卡洛、健全性警告。这不是本项目该重写的
东西。

**接通时必须守住两件事，它们都是静默出错的入口。**

一、**两套数据源。** 本项目有账本: 双源交叉验证、逐条可溯源到接口响应、
    带披露日的时点守卫。ashare-dcf 有自己的取数（ashare_data.py）。同一家
    公司的同一年营收，两边各取一次 —— 一旦不一致，研报正文写一个数、估值
    模型用另一个数，而没有任何地方会报错。这与此前「权益乘数 1.77 对 1.94」
    是同一类问题，只是跨了系统边界。因此**逐科目交叉核对并把差异报出来**。

二、**as_of。** 本项目所有取数都过时点守卫，回测时 as_of 可以是过去某天。
    ashare-dcf 取的是**当日**行情与最新报表 —— 在 as_of 早于今天的口径下，
    它用的是未来信息。这不是它的缺陷（它本就为「今天做一份模型」而设计），
    但两者接在一起时必须说出来，否则就是一个不报错的未来函数。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method

# ashare-dcf 的位置。默认取本仓库的同级目录，可用环境变量覆盖。
ENV_DIR = "ASHARE_DCF_DIR"
DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "ashare-dcf"

YI = Decimal("100000000")          # ashare-dcf 以亿元计，账本以元计

# 交叉核对的容忍度。两边取的是同一份公告数字，**差异只会来自口径或单位**，
# 不会来自舍入 —— 因此门槛设得很紧，宁可多报也不放过。
XCHECK_TOL = Decimal("0.005")

# 账本字段 → (报表, 新浪报表里的科目名)。
# 科目名取自 ashare_calibrate 实际使用的那几个 —— 不同公司列名略有出入，
# 因此给出候选并逐个试，取不到就跳过而非误报。
XCHECK_ITEMS = (
    ("revenue", "income", ("营业总收入", "营业收入")),
    ("net_profit", "income", ("净利润",)),
    ("net_profit_attr_parent", "income", ("归属于母公司所有者的净利润",)),
    ("total_assets", "balance", ("资产总计",)),
    ("total_liabilities", "balance", ("负债合计",)),
)


class DcfUnavailable(Exception):
    """拿不到 ashare-dcf，或它拒绝为本标的建模。"""


@dataclass
class DcfResult:
    code: str
    workbook: str
    price: Decimal
    value_per_share: Decimal
    wacc: Decimal
    terminal_roic: Decimal
    reinvest_rate: Decimal
    terminal_share: Decimal          # 终值占 EV
    verified: bool                   # 公式引擎是否实跑通过
    warnings: list[str] = field(default_factory=list)
    crosscheck: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def upside(self) -> Decimal | None:
        if self.price <= 0:
            return None
        return self.value_per_share / self.price - 1

    def summary(self) -> str:
        lines = [f"DCF 估值（ashare-dcf 三表模型，{Path(self.workbook).name}）"]
        if self.verified:
            lines.append("  ✓ 公式引擎实跑验算通过")
        else:
            lines.append("  ✗ **公式引擎验算未通过 —— 结果不可采信**")
        lines.append(f"  每股内在价值 [[valuation.value_per_share]]　"
                     f"现价 [[valuation.price]]")
        lines.append(f"  WACC [[valuation.wacc]]　终值 ROIC "
                     f"[[valuation.terminal_roic]]　"
                     f"终值占企业价值 [[valuation.terminal_share]]")
        for x in self.notes:
            lines.append(f"  · {x}")
        for x in self.crosscheck:
            lines.append(f"  ⚠ 与账本对不上：{x}")
        for x in self.warnings:
            lines.append(f"  ⚠ {x}")
        return "\n".join(lines)


def _load(dirpath: Path):
    if not dirpath.is_dir():
        raise DcfUnavailable(
            f"未找到 ashare-dcf（{dirpath}）。"
            f"可用环境变量 {ENV_DIR} 指定它的位置。")
    if str(dirpath) not in sys.path:
        sys.path.insert(0, str(dirpath))
    try:
        import ashare_build as B
        import ashare_calibrate as C
        import ashare_data as D
        import ashare_verify as V
    except ImportError as e:
        raise DcfUnavailable(f"ashare-dcf 无法导入：{e}") from e
    return D, C, B, V


def _asof_note(as_of: date) -> list[str]:
    """as_of 与今天不一致时必须说出来 —— 否则就是个不报错的未来函数。"""
    today = date.today()
    if as_of >= today:
        return []
    gap = (today - as_of).days
    return [f"本模型取的是**今日（{today}）**的行情与最新报表，"
            f"而本报告的分析时点是 {as_of}（相差 {gap} 天）。"
            f"估值口径因此比正文更新，两者不是同一时点上的判断。"]


def _stmt_value(D, stmts: dict, table: str, items, period: str):
    """按候选科目名逐个试。**返回值已是亿元** —— ashare_data.val() 读出
    原始值后除以 1e8，这一点必须与账本的元对齐，换算错了要么把每一项都
    报成天文数字的差异，要么把真实差异淹掉。
    """
    df = stmts.get(table)
    if df is None:
        return None
    for name in items:
        v = D.val(df, name, period)
        if v is not None:
            return v, name
    return None


def cross_check(cal: dict, ledger: FactLedger, as_of: date,
                D=None) -> list[str]:
    """把 ashare-dcf 取到的报表数与账本逐项对一遍。

    对不上不代表哪一边错了 —— 可能是口径差异（合并范围、追溯调整），
    但**必须报出来**: 研报正文写一个数、估值模型用另一个数而无人知晓，
    是这套系统最不该出现的事。
    """
    out: list[str] = []
    last = (cal.get("meta") or {}).get("last_annual")
    stmts = cal.get("stmts") or {}
    if not last:
        return ["ashare-dcf 未给出最近年报期间，无法交叉核对"]
    period = f"{last[:4]}FY"
    if D is None:
        D = _load(Path(os.environ.get(ENV_DIR) or DEFAULT_DIR))[0]

    for key, table, items in XCHECK_ITEMS:
        got = _stmt_value(D, stmts, table, items, last)
        if got is None:
            continue
        theirs, item = got
        try:
            ours = ledger.get(key, period, as_of=as_of).value / YI
        except (KeyError, LookAheadError):
            continue
        theirs = Decimal(str(theirs))
        if ours == 0:
            continue
        diff = abs(theirs - ours) / abs(ours)
        if diff > XCHECK_TOL:
            out.append(f"{item}@{period} 账本 {ours:.2f} 亿 vs 模型 "
                       f"{theirs:.2f} 亿（差 {diff:.1%}）")
    return out


def to_facts(res: DcfResult, as_of: date,
             source_id: str = "ashare_dcf") -> list[Fact]:
    """估值结果落账本，正文才能用占位符引用它。

    method=COMPUTED 而非 ESTIMATED: 这些数字是模型在给定假设下算出来的，
    确定性可复算（工作簿就在那里，公式引擎跑过）。**假设本身是判断，
    但由假设到结果这一步不是估计。**
    """
    def f(key: str, value: Decimal, unit: str) -> Fact:
        return Fact(key=key, value=value, unit=unit, period=as_of.isoformat(),
                    as_of=as_of, source_id=source_id, method=Method.COMPUTED)

    return [
        f("valuation.value_per_share", res.value_per_share, "元"),
        f("valuation.price", res.price, "元"),
        f("valuation.wacc", res.wacc, "ratio"),
        f("valuation.terminal_roic", res.terminal_roic, "ratio"),
        f("valuation.reinvest_rate", res.reinvest_rate, "ratio"),
        f("valuation.terminal_share", res.terminal_share, "ratio"),
    ]


def run(code: str, out_dir: str | Path, *, as_of: date,
        ledger: FactLedger | None = None, years: int = 5,
        dirpath: Path | None = None) -> DcfResult:
    """跑一遍 ashare-dcf，返回结果与全部警告。

    **不吞掉任何警告。** ashare-dcf 自己会报健全性问题（终值占比过高、
    再投资率失真、科目被抽干等），那些是它最有价值的部分之一。
    """
    dirpath = dirpath or Path(os.environ.get(ENV_DIR) or DEFAULT_DIR)
    D, C, B, V = _load(dirpath)

    try:
        code = D.validate_code(code)
        cal = C.calibrate(code, years)
    except getattr(D, "DataError", Exception) as e:
        raise DcfUnavailable(f"{code} 无法建模：{e}") from e

    meta = cal["meta"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # 文件名不能含 % —— ashare_verify 会因此拒绝验算。
    name = str(meta.get("name") or code).replace("%", "")
    wb = out / f"{code}_{name}_DCF_{date.today():%Y%m%d}.xlsx"
    B.build(cal, str(wb))

    fails, dv = V.verify(str(wb))
    fails += [x[1:] for x in V.verify_all_scenarios(str(wb), quiet=True)
              if x[0] != "Base"]

    inp = V.read_inputs(str(wb))
    rows, _ = V.recompute(inp)
    warns = (V.sanity(dv, rows) + V.balance_sheet_warnings(inp, rows)
             + V.assumption_warnings(inp))

    ev0 = Decimal(str(dv["ev0"]))
    res = DcfResult(
        code=code, workbook=str(wb),
        price=Decimal(str(meta["price"])),
        value_per_share=Decimal(str(dv["ps"])),
        wacc=Decimal(str(dv["wacc"])),
        terminal_roic=Decimal(str(dv["roic"])),
        reinvest_rate=Decimal(str(dv["rr"])),
        terminal_share=(Decimal(str(dv["pvtv"])) / ev0 if ev0 else Decimal(0)),
        verified=not fails,
        warnings=[str(w) for w in warns],
        notes=_asof_note(as_of) + [
            f"行情源 {meta.get('quote_source')}　β {meta.get('beta')}"
            f"（原始 {meta.get('beta_raw')}，{meta.get('beta_n')} 期）",
        ] + [f"校准被截断：{x}" for x in (cal.get("clamps") or [])],
    )
    if not res.verified:
        res.warnings.insert(0, f"公式引擎验算失败 {len(fails)} 处，"
                               "本结果不可采信")
    if ledger is not None:
        res.crosscheck = cross_check(cal, ledger, as_of, D)
    return res
