"""五类财务比率 —— FINC6600 要求的流动性/偿债/盈利/效率/市场前景。

**适用性判定不在这里** —— 它在 metrics.py 的登记表里，这里只负责算。
分开的理由: 「该不该算」要在提示词、看板、同业对比三处复用，
写三遍就会错三遍。这里拿到登记表的判定结果，只处理能算的。

算子层要处理的是登记表管不了的三件事:

  1. **分母为零** —— 不抛异常打断整批，记为「无定义」并说明是哪个分母
  2. **口径选择** —— 归母 vs 全口径。少数股东权益占比高时两者差异很大，
     沿用 dupont 的 basis 标注机制
  3. **多期序列** —— 课程要求四年趋势，不是单期快照。趋势本身是结论
     （「连续三年下滑」比「今年 1.2」有信息量得多）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, LookAheadError
from ir_agent.metrics import (
    BY_KEY,
    Applicability,
    Category,
    MetricSpec,
    evaluate,
)
from ir_agent.operators.base import derive
from ir_agent.periods import UnknownPeriod
from ir_agent.periods import period_end_date
from ir_agent.periods import sort_key as period_key
from ir_agent.sources.eastmoney import CompanyType

DAYS = Decimal(365)

# 课程要求「最近四年」的趋势分析
TREND_YEARS = 4


@dataclass(frozen=True)
class Undefined:
    """能取到全部输入，但算不出来 —— 几乎总是分母为零。

    与「缺数据」分开: 缺数据要去查取数，分母为零是标的本身的状态
    （新上市公司权益为负、亏损公司 PE 无意义）。
    """

    spec: MetricSpec
    reason: str


def _get(ledger: FactLedger, key: str, period: str, as_of: date,
         spot: str | None) -> Fact | None:
    per = spot if (key in _SPOT_KEYS and spot) else period
    try:
        return ledger.get(key, per, as_of=as_of)
    except (KeyError, LookAheadError):
        return None


_SPOT_KEYS = {"market_cap", "last_price"}


def _div(num: Fact, den: Fact) -> Decimal | None:
    return None if den.value == 0 else num.value / den.value


# ── 各比率的算法 ──────────────────────────────────────────────
# 每个函数拿到已确认可取的输入，返回 (值, 输入列表) 或 None（无定义）。

def _liquidity(f) -> dict:
    ca, cl, inv, ta = f("current_assets"), f("current_liabilities"), \
        f("inventory"), f("total_assets")
    out = {}
    if ca and cl:
        out["current_ratio"] = (_div(ca, cl), [ca, cl])
        if inv is not None:
            v = None if cl.value == 0 else (ca.value - inv.value) / cl.value
            out["quick_ratio"] = (v, [ca, inv, cl])
    if ca and cl and ta:
        v = None if ta.value == 0 else (ca.value - cl.value) / ta.value
        out["nwc_to_assets"] = (v, [ca, cl, ta])
    return out


def _solvency(f) -> dict:
    tl, te, ta = f("total_liabilities"), f("total_equity"), f("total_assets")
    op, ie = f("operating_profit"), f("interest_expense_fe")
    out = {}
    if tl and te:
        out["debt_to_equity"] = (_div(tl, te), [tl, te])
    # 权益乘数、净利率、总资产周转率、ROE 一律交给 dupont 算 ——
    # 它们必须满足 ROE = 净利率 × 周转率 × 权益乘数 这条恒等式，
    # 在这里另算一遍必然与之打架。见 compute() 末尾。
    if op and ie:
        # 利息费用可能为负（利息收入大于支出）—— 此时倍数无意义
        v = None if ie.value <= 0 else op.value / ie.value
        out["interest_coverage"] = (v, [op, ie])
    return out


def _profitability(f) -> dict:
    rev, cost = f("revenue"), f("cost_of_revenue")
    rt, op, np_ = f("revenue_total"), f("operating_profit"), f("net_profit")
    ta = f("total_assets")
    npp, eqp = f("net_profit_attr_parent"), f("equity_attr_parent")
    bme, ini = f("business_manage_expense"), f("interest_net_income")
    out = {}
    if rev and cost:
        v = None if rev.value == 0 else (rev.value - cost.value) / rev.value
        out["gross_margin"] = (v, [rev, cost])
    if op and rt:
        out["operating_margin"] = (_div(op, rt), [op, rt])
    if np_ and ta:
        out["roa"] = (_div(np_, ta), [np_, ta])
    if bme and rt:
        # 业务及管理费在接口中为正值列示；成本收入比取绝对值口径
        v = None if rt.value == 0 else abs(bme.value) / rt.value
        out["cost_income_ratio"] = (v, [bme, rt])
    if ini and rt:
        out["net_interest_income_ratio"] = (_div(ini, rt), [ini, rt])
    return out


def _efficiency(f) -> dict:
    rt, rev, cost = f("revenue_total"), f("revenue"), f("cost_of_revenue")
    ta, fa = f("total_assets"), f("fixed_asset")
    ar, inv, ap = f("accounts_receivable"), f("inventory"), f("accounts_payable")
    out = {}
    if rt and fa:
        out["fixed_asset_turnover"] = (_div(rt, fa), [rt, fa])
    if ar and rev:
        v = None if rev.value == 0 else ar.value / rev.value * DAYS
        out["receivable_days"] = (v, [ar, rev])
    if inv and cost:
        v = None if cost.value == 0 else inv.value / cost.value * DAYS
        out["inventory_days"] = (v, [inv, cost])
    if ap and cost:
        v = None if cost.value == 0 else ap.value / cost.value * DAYS
        out["payable_days"] = (v, [ap, cost])
    if ar and inv and ap and rev and cost:
        if rev.value and cost.value:
            v = (ar.value / rev.value * DAYS + inv.value / cost.value * DAYS
                 - ap.value / cost.value * DAYS)
        else:
            v = None
        out["cash_conversion_cycle"] = (v, [ar, inv, ap, rev, cost])
    return out


def _market(f) -> dict:
    mc, ttm = f("market_cap"), f("net_profit_ttm")
    eqp, cash = f("equity_attr_parent"), f("cash_and_equivalents")
    op, ie, tax, tp, te = (f("operating_profit"), f("interest_expense_fe"),
                           f("income_tax"), f("total_profit"), f("total_equity"))
    da = f("depreciation_amortization")
    out = {}
    if mc and ttm:
        # 亏损公司的 PE 无意义 —— 不是算不出，是这个数不该被解读
        v = None if ttm.value <= 0 else mc.value / ttm.value
        out["pe_ttm"] = (v, [mc, ttm])
    if mc and eqp:
        v = None if eqp.value <= 0 else mc.value / eqp.value
        out["pb"] = (v, [mc, eqp])
    if op and ie and tax and tp and te and cash:
        # EBIT 与 DOL / Altman Z 同一口径 —— 中国准则下营业利润已扣息
        ebit = op.value + max(ie.value, Decimal(0))
        rate = Decimal(0) if tp.value == 0 else tax.value / tp.value
        invested = te.value + _debt_of(f) - cash.value
        v = None if invested <= 0 else ebit * (Decimal(1) - rate) / invested
        out["roic"] = (v, [op, ie, tax, tp, te, cash])
    if mc and op and da and cash and te and eqp:
        ebitda = op.value + da.value + (max(ie.value, Decimal(0)) if ie else 0)
        # EBITDA 是合并口径，含少数股东那一份 —— EV 必须加回少数股东权益，
        # 分子分母才是同一群出资人（Damodaran / CFA 标准口径）。
        # 少数股东权益取「所有者权益合计 − 归母权益」，与相对估值模块同一定义；
        # 为负（子公司累计亏损）照常加回，不截成 0。
        minority = te.value - eqp.value
        ev = mc.value + _debt_of(f) - cash.value + minority
        v = None if ebitda <= 0 else ev / ebitda
        out["ev_ebitda"] = (v, [mc, op, da, te, eqp, cash])
    return out


_DEBT_PARTS = ("short_loan", "long_loan", "bond_payable", "noncurrent_liab_1y")


def _debt_of(f) -> Decimal:
    """有息负债合计。分量缺失视为 0 —— 公司确实可能没有应付债券。"""
    return sum((x.value for k in _DEBT_PARTS if (x := f(k)) is not None),
               Decimal(0))


_BLOCKS = (_liquidity, _solvency, _profitability, _efficiency, _market)


@dataclass
class RatioSet:
    period: str
    company_type: CompanyType
    facts: dict[str, Fact]
    undefined: list[Undefined]
    not_meaningful: list[MetricSpec]
    unavailable: list[tuple[MetricSpec, tuple[str, ...]]]

    def by_category(self) -> dict[Category, list[Fact]]:
        out: dict[Category, list[Fact]] = {c: [] for c in Category}
        for key, f in self.facts.items():
            out[BY_KEY[key].category].append(f)
        return out

    def summary(self) -> str:
        from ir_agent.citation import format_value

        lines = [f"财务比率 {self.period}（{self.company_type.name} 模板）"]
        for cat, fs in self.by_category().items():
            if not fs:
                continue
            items = "、".join(f"{BY_KEY[f.key].label} {format_value(f)}"
                              for f in fs)
            lines.append(f"  {cat.value}　{items}")
        for u in self.undefined:
            lines.append(f"  · {u.spec.label} 无定义 —— {u.reason}")
        if self.not_meaningful:
            lines.append("  · 该类公司不适用：" +
                         "、".join(m.label for m in self.not_meaningful))
        for spec, missing in self.unavailable:
            lines.append(f"  ⚠ {spec.label} 缺数据：{'、'.join(missing)}")
        return "\n".join(lines)


def compute(ledger: FactLedger, period: str, as_of: date,
            company_type: CompanyType = CompanyType.GENERAL,
            spot: str | None = None) -> RatioSet:
    """算出该期所有可算的比率，并如实分类未算出的原因。"""
    # 先把分量合计出来 —— 折旧摊销与有息负债在报表里只有分量没有合计，
    # 而 EV/EBITDA、ROIC 都以它们为输入。必须在适用性判定之前算，
    # 否则会被判成「缺数据」。
    from ir_agent.operators import aggregates
    aggregates.compute(ledger, period, as_of)

    status = {r.spec.key: r for r in evaluate(ledger, period, as_of,
                                              company_type, spot=spot)}

    def f(key: str) -> Fact | None:
        return _get(ledger, key, period, as_of, spot)

    facts: dict[str, Fact] = {}
    undefined: list[Undefined] = []
    for block in _BLOCKS:
        for key, (value, inputs) in block(f).items():
            spec = BY_KEY[key]
            if not spec.applicable_to(company_type):
                continue
            if value is None:
                den = inputs[-1]
                undefined.append(Undefined(
                    spec, f"{den.key}@{den.period} 为 {den.value}，该比率无定义"))
                continue
            facts[key] = derive(ledger, key, value, spec.unit, period, inputs)

    # 杜邦四项由 dupont 统一算，保证 ROE = 净利率 × 周转率 × 权益乘数。
    # 缺分量时跳过 —— 银行等模板可能取不到其中某项。
    try:
        from ir_agent.operators import dupont
        for k, v in dupont.compute(ledger, period, as_of).items():
            if BY_KEY[k].applicable_to(company_type):
                facts[k] = v
    except (KeyError, LookAheadError, ZeroDivisionError):
        pass

    return RatioSet(
        period=period, company_type=company_type, facts=facts,
        undefined=undefined,
        not_meaningful=[r.spec for r in status.values()
                        if r.status is Applicability.NOT_MEANINGFUL],
        unavailable=[(r.spec, r.missing) for r in status.values()
                     if r.status is Applicability.UNAVAILABLE
                     and r.spec.key not in facts],
    )


# ── 趋势 ──────────────────────────────────────────────────────

def annual_periods(ledger: FactLedger, as_of: date,
                   years: int = TREND_YEARS) -> list[str]:
    """**已经结束**的最近若干个年度期间，早到晚 —— 不是预测期。

    课程要求四年趋势。次新股不足四年时**返回实际有的**，
    由调用方说明样本长度 —— 凑不出四年是标的的事实，不是缺陷。

    教训（实测复现过一次）：这里原来只按「披露时点 ≤ as_of」过滤
    （挡 `LookAheadError`，即未来函数），没有再问一句「这个年度是不是
    已经走完」。「披露时点」与「期间是否已经结束」是两件不同的事 ——
    一份对 2028 年的卖方一致预期完全可以披露于 2026 年、合法可见，
    但 2028FY 这个会计年度显然没结束。此前的写法会把 `consensus.*`
    这类对未来年度的键当成「最近的年度」选中，取到的毛利率基期就变成
    了「未来年度」（对应实绩事实根本不存在，取到 None），与
    `assume._base_margin_of`（走 calibrate 的年度历史序列，天然只有
    已完成年度）分叉——正是这个分支反复在防的那类「人批准的和模型
    用的不是同一个数」的失败。所以在披露时点过滤之外，再按
    `period_end_date` 挡一道：期间的日历终止日必须 ≤ as_of。

    **不要因为要放行更多期间就删掉这道过滤** —— 四个生产调用方
    （本模块的 6 年趋势、`full._latest_annual_period`、
    `assume._historical_series` 残差回算、比率趋势）要的全是已完成
    年度，没有一个想要预测期；一致预期功能不经过这里，它按显式
    `key@period` 直接从账本取（见 `sources/reports.py` 的
    `consensus_facts` 与 `corpus.py` 的引用），过滤掉预测期不影响它。
    """
    seen: set[str] = set()
    for key, per in ledger._facts:                       # noqa: SLF001
        if not per.endswith("FY"):
            continue
        try:
            if period_end_date(per) > as_of:
                continue
        except UnknownPeriod:
            continue
        try:
            ledger.get(key, per, as_of=as_of)
        except (KeyError, LookAheadError):
            continue
        seen.add(per)
    try:
        ordered = sorted(seen, key=period_key)
    except UnknownPeriod:
        ordered = sorted(seen)
    return ordered[-years:]


@dataclass
class Trend:
    key: str
    label: str
    periods: list[str]
    values: list[Decimal]

    @property
    def direction(self) -> str:
        """趋势本身是结论 ——「连续三年下滑」比「今年 1.2」有信息量。"""
        if len(self.values) < 2:
            return "样本不足"
        diffs = [b - a for a, b in zip(self.values, self.values[1:])]
        if all(d > 0 for d in diffs):
            return "持续上升"
        if all(d < 0 for d in diffs):
            return "持续下降"
        return "上升" if self.values[-1] > self.values[0] else (
            "下降" if self.values[-1] < self.values[0] else "基本持平")

    def describe(self) -> str:
        n = len(self.values)
        return (f"{self.label} {self.periods[0]}→{self.periods[-1]} "
                f"{self.direction}（{n} 期）")


def trend(ledger: FactLedger, key: str, periods: list[str],
          as_of: date) -> Trend | None:
    vals, pers = [], []
    for p in periods:
        try:
            vals.append(ledger.get(key, p, as_of=as_of).value)
            pers.append(p)
        except (KeyError, LookAheadError):
            continue
    if len(vals) < 2:
        return None
    return Trend(key=key, label=BY_KEY[key].label, periods=pers, values=vals)


def compute_trend(ledger: FactLedger, as_of: date,
                  company_type: CompanyType = CompanyType.GENERAL,
                  years: int = TREND_YEARS,
                  spot: str | None = None) -> tuple[list[RatioSet], list[Trend]]:
    """逐年算比率，再抽出趋势。这是课程要求的最小分析单元。"""
    periods = annual_periods(ledger, as_of, years)
    sets = [compute(ledger, p, as_of, company_type,
                    spot=spot if p == periods[-1] else None)
            for p in periods]
    keys = sorted({k for s in sets for k in s.facts})
    trends = [t for k in keys if (t := trend(ledger, k, periods, as_of))]
    return sets, trends
