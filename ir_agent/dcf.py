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
    # 估值时点。**summary() 写占位符要用它拼 @period。** to_facts 以
    # `as_of.isoformat()` 作为期间把 valuation.* 落进账本，而替换机制
    # （citation.REF_RE）硬性要求 `[[key@period]]` 两段——此前 summary()
    # 拿不到时点、只写了 `[[valuation.wacc]]`，于是那些占位符压根不被
    # 识别为占位符，一路原样漏进最终研报（600584 端到端实测：交付的
    # report.md 里整个估值段落是六个未替换的占位符，而真实数字只出现在
    # 终端摘要里）。产出端与消费端对同一个契约的格式约定必须一致。
    as_of: date | None = None
    warnings: list[str] = field(default_factory=list)
    crosscheck: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 首年被锚定覆盖的说明（`_anchor_note`）。它同时在 warnings 里进终端；
    # 单列出来是给研报附录用的——研报正文一直按批准值讲首年。
    anchor_note: str = ""

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
        # 期间必须与 to_facts 落账本时用的一致（as_of.isoformat()），
        # 否则 REF_RE 匹配不上、占位符不会被替换。
        per = self.as_of.isoformat() if self.as_of else ""
        at = f"@{per}" if per else ""
        lines.append(f"  每股内在价值 [[valuation.value_per_share{at}]]　"
                     f"现价 [[valuation.price{at}]]")
        lines.append(f"  WACC [[valuation.wacc{at}]]　终值 ROIC "
                     f"[[valuation.terminal_roic{at}]]　"
                     f"终值占企业价值 [[valuation.terminal_share{at}]]")
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


def _calibrate(D, C, code: str, as_of: date, ledger, years: int):
    """取数 + 校准，不建模。返回 (cal, notes)。

    第一阶段只需要它: 校准值是 AI 填判断项的参照（历史均值、标准差、
    区间），而建模要等人工核实之后。
    """
    notes: list[str] = []
    stmts = None
    if ledger is not None:
        from ir_agent import dcf_data
        try:
            stmts = dcf_data.to_frames(ledger, as_of)
            cov = dcf_data.coverage(stmts)
            notes.append("报表由事实账本供数（已过双源交叉验证、带披露日）："
                         + "、".join(f"{t} {a}/{b} 项" for t, (a, b)
                                     in cov.items()))
        except dcf_data.MappingIncomplete as e:
            notes.append(f"⚠ 账本铺不出报表（{e}），已退回 DCF 自行取数 —— "
                         "此时分析层与估值层用的不是同一套数字")
    try:
        code = D.validate_code(code)
        cal = C.calibrate(code, years, stmts=stmts)
    except getattr(D, "DataError", Exception) as e:
        raise DcfUnavailable(f"{code} 无法建模：{e}") from e
    return cal, notes, stmts


def calibrate_only(code: str, *, as_of: date, ledger=None, years: int = 5,
                   dirpath: Path | None = None) -> dict:
    """只校准不建模 —— 第一阶段用它拿到历史参照与预测期标签。"""
    dirpath = dirpath or Path(os.environ.get(ENV_DIR) or DEFAULT_DIR)
    D, C, _, _ = _load(dirpath)
    return _calibrate(D, C, code, as_of, ledger, years)[0]


_SCEN_CN = {"bull": "乐观", "base": "中性", "bear": "悲观"}


def _close(a: list[float], b: list[float], tol: float) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def anchored_year1(*, rev_h: float, cogs_h: float, share: float,
                   gm_rest: float, base_rev: float) -> tuple[float, float]:
    """锚定反推：由已披露中期算出首个预测年的 (营收增速, 毛利率)。

    与 `1_假设` 的 E 列、`ashare_verify.recompute` 同一套算法——全年营收 =
    已披露营收 ÷ 占全年比重；全年成本 = 已披露成本 + 剩余期营收 ×
    (1 − 剩余期毛利率)。

    **只能有这一份。** 回读校验、研报附录、第一阶段批准清单横幅都要说
    「首年实际用的是多少」，三处各算一遍，早晚算出三个不同的数。
    """
    rev = rev_h / share
    cogs = cogs_h + (rev - rev_h) * (1 - gm_rest)
    return rev / base_rev - 1, 1 - cogs / rev


def _effective_first_year(inp: dict) -> tuple[float, float] | None:
    """锚定开启时，首年**实际生效**的 (营收增速, 毛利率)。None = 没锚定。

    `1_假设` 的生效值 E 列是

        E11 = IF($B$30=1, B35/$B$81-1, CHOOSE($B$6,B11,C11,D11)+$B$26)
        E16 = IF($B$30=1, 1-B36/B35,  CHOOSE($B$6,B16,C16,D16)+$B$27)

    锚定开关一开，首年就完全不看情景列——也就是完全不看人批准的那个数。
    这里按同一套算法重算一遍（`read_inputs` 的 `it` 字典里几个输入都在），
    好把"实际用的"与"人批准的"摆在一起给人看。
    """
    it = inp.get("it")
    if not it or not float(it.get("sw") or 0):
        return None
    share = float(it.get("share") or 0)
    base_rev = float((inp.get("o") or {}).get("基期营业总收入") or 0)
    if not share or not base_rev:
        return None
    return anchored_year1(rev_h=float(it["rev"]), cogs_h=float(it["cogs"]),
                          share=share, gm_rest=float(it["gm_rest"]),
                          base_rev=base_rev)


def _anchor_note(judg: dict, inp: dict) -> str:
    """首年被锚定覆盖时，把「人批准的」与「实际用的」并排写出来。没锚定、
    或两者恰好一致时返回空串。

    从 `_readback_check` 抽出来单独成一个出口：终端只是三处之一，研报
    附录也要说同一句话（研报正文一直按批准值讲首年）。说法沿用 cd142a1
    的取舍——**不说「不可采信」**：锚定用的是已披露中报的真实营收与成本，
    比一个判断可靠，说成错误会诱使人去关掉锚定。
    """
    eff = _effective_first_year(inp)
    if eff is None:
        return ""
    g_eff, m_eff = eff
    pairs = [("营收增速", float((judg.get("growth") or [0])[0]), g_eff),
             ("毛利率", float((judg.get("gm") or [0])[0]), m_eff)]
    diff = [f"{cn} 批准 {app:.2%} → 实际用 {act:.2%}"
            for cn, app, act in pairs if abs(app - act) > 5e-5]
    if not diff:
        return ""
    return ("首年被「锚定已披露中期实际」覆盖，人批准的值未参与建模："
            + "；".join(diff)
            + "。锚定用的是已披露中报的真实营收与成本，通常比判断"
              "可靠，因此不算错——但待核实清单、终端与研报都是按"
              "批准值讲的，这个差异必须让人看见")


def _readback_check(judg: dict, inp: dict, tol: float = 1e-9) -> list[str]:
    """建完表再回读一遍：工作簿里真正参与估值的数，是不是人批准的那几个。

    **闸门查的都是「用得对不对」，没有一道查「结果对不对」。** 人工批准
    判断项是整个流程的核心关卡，而批准之后一直没有任何一道检查回头确认
    这件事。踩过的两次（C2 / C-a）都是这条链断在中间：同一因子同一年
    两行，清单显示一行、工作簿用另一行；重复行让 path() 按年份排序后
    整体错位一格，人批准第 2 年的 7%、工作簿拿到第 1 年那条重复行的
    30%。两次都是人自己读工作簿才发现的，修法也都是"不许有重复行"
    ——仍然在查输入，没有查输出。

    `ashare_verify.read_inputs()` 本来就把工作簿里逐年的营收增速/毛利率
    读了回来（已并入「Δ」偏移），与 `assume.apply_to` 写进 `cal["judg"]`
    的三档比一遍即可闭环，成本几乎为零。

    对不上时分两种说法：数字命中了三档里的**另一档**（多半是工作簿的
    「选择情景」被改过），与数字**三档都对不上**（错位或没写进去）——
    前者是选错了档，后者是建模链断了，读者的处置完全不同。
    """
    problems: list[str] = []
    # **锚定会把首年整个换掉，而情景列看不出来。** 实测（生益科技
    # 600183）：人批准 2026 营收增速 22.31%、毛利率 27.77%，工作簿实际
    # 用的是 39.85% 与 30.69%——而下面那段比的是 `read_inputs` 的
    # growth/gm，那是 `path3()` 从 B/C/D **情景列**读的，情景列当然对得上
    # （`apply_to` 刚写进去的）。查了输入没查生效值，正是这道闸门本来要
    # 防的事，它自己犯了一遍。
    #
    # 锚定本身是对的：用已披露中报的真实营收与成本，比一个判断可靠。
    # 所以这条**不说"不可采信"**——说成错误会诱使人去关掉锚定，那才是
    # 坏事。要报的是"人批准的数没被用上"，让人知道自己批的是什么、
    # 模型跑的是什么。
    note = _anchor_note(judg, inp)
    if note:
        problems.append(note)
    for key, cn, dkey in (("growth", "营收增速", "dg"), ("gm", "毛利率", "dm")):
        # 「Δ」是 1_假设 里人有意加的整体偏移，read_inputs 已经并进了
        # 逐年值。它不是错位，扣掉再比。
        delta = float(inp.get(dkey) or 0.0)
        got = [float(x) - delta for x in (inp.get(key) or [])]
        want = [float(x) for x in (judg.get(key) or [])]
        if _close(got, want, tol):
            continue
        scen = judg.get(f"{key}_scen") or {}
        hit = next((n for n, v in scen.items()
                    if _close(got, [float(x) for x in v], tol)), "")
        if hit:
            problems.append(
                f"工作簿的{cn}用的是{_SCEN_CN.get(hit, hit)}档，不是批准"
                f"清单定案的中性档——「选择情景」被改过，估值结果对应的"
                f"不是人批准的那一档")
        else:
            problems.append(
                f"工作簿的{cn}与人工批准的对不上：批准 {want}，工作簿 "
                f"{got} —— 人批准的数没有进到真正参与估值的地方，"
                f"**本结果不可采信**")
    return problems


def run(code: str, out_dir: str | Path, *, as_of: date,
        ledger: FactLedger | None = None, years: int = 5,
        dirpath: Path | None = None, assumptions=None,
        approved_by: str = "") -> DcfResult:
    """跑一遍 ashare-dcf，返回结果与全部警告。

    **账本给定时由它供数**，DCF 不再自行取数 —— 分析层与估值层用同一套
    数字，不会悄悄分叉。账本的数已经过东财 × 新浪双源交叉验证、带披露日，
    比各取一套再事后比对更彻底。

    **不吞掉任何警告。** ashare-dcf 自己会报健全性问题（终值占比过高、
    再投资率失真、科目被抽干等），那些是它最有价值的部分之一。
    """
    dirpath = dirpath or Path(os.environ.get(ENV_DIR) or DEFAULT_DIR)
    D, C, B, V = _load(dirpath)

    cal, notes, stmts = _calibrate(D, C, code, as_of, ledger, years)

    if assumptions is not None:
        from ir_agent import assume
        cal = assume.apply_to(cal, assumptions)
        if getattr(assumptions, "decomps", None):
            # **只有方法论预研成功过才有分解式。** 预研失败退回两标量
            # 模式时 assumptions.decomps 是空列表，此时不建「1b_驱动
            # 分解」表 —— 工作簿形状与从前完全一致，不留一张空表。
            cal["judg"]["drivers"] = assume.to_driver_block(assumptions, cal)
            notes.append("工作簿含「1b_驱动分解」表，"
                         "改一个因子会带动整条估值链重算")
        notes.append(
            "营收增速与毛利率三档由分析结论填入"
            + (f"，经 {approved_by} 核实" if approved_by else
               "，**未经人工核实**")
            + "；其余假设（费用率、周转天数、折旧率、税率、β、Kd）"
            "由历史财务数据校准。")
    else:
        # 模板默认三档同值。不说出来，读者会以为那是三个不同的情景。
        notes.append("⚠ 判断项未填：营收增速与毛利率沿用模板默认值且"
                     "三档同值 —— 此时的「情景分析」只是把同一个数抄三遍")

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
    if assumptions is not None:
        # 见 `_readback_check`：人批准的数必须真的进了工作簿。**排在所有
        # 健全性告警前面** —— 数要是根本没进去，后面那些告警都是在给一个
        # 错的模型体检。
        warns = _readback_check(cal["judg"], inp) + list(warns)

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
        as_of=as_of,
        anchor_note=(_anchor_note(cal["judg"], inp) if assumptions is not None else ""),
        warnings=[str(w) for w in warns],
        notes=notes + _asof_note(as_of) + [
            f"行情源 {meta.get('quote_source')}　β {meta.get('beta')}"
            f"（原始 {meta.get('beta_raw')}，{meta.get('beta_n')} 期）",
        ] + [f"校准被截断：{x}" for x in (cal.get("clamps") or [])],
    )
    if not res.verified:
        res.warnings.insert(0, f"公式引擎验算失败 {len(fails)} 处，"
                               "本结果不可采信")
    if ledger is not None and stmts is None:
        # 只在**退回自行取数**时才需要事后比对。账本供数时两边同源，
        # 比对必然一致，跑它只是浪费 —— 真正的验证在 dcf_data 的映射
        # 核验里（账本铺的表 vs 原生接口，逐格比对）。
        res.crosscheck = cross_check(cal, ledger, as_of, D)
    return res
