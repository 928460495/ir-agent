"""指标登记表 —— 每个指标声明它需要什么、对哪类公司成立、不成立时为什么。

**这是「通用」的实现点，不是一张查找表。**

早期的隐含假设是「所有公司的报表长得一样」。实测下来不成立:

    字段                     茅台   招行   平安   中信证券
    TOTAL_CURRENT_ASSETS     有     —      —      —
    TOTAL_CURRENT_LIAB       有     —      —      —
    INVENTORY                有     —      —      —
    ACCOUNTS_RECE            有     —      有     —
    MONETARYFUNDS            有     —      有     有

银行缺这些**不是数据缺失，是会计上根本不存在** —— 银行的资产负债表按
流动性排序列示，不区分流动/非流动。对银行算「流动比率」不是算不出来，
是这个数没有含义。

因此每个指标必须能回答三件事:

  1. **需要哪些账本 key**（inputs）
  2. **对哪类公司成立**（applies_to）
  3. **不成立时的理由**（why_not）—— 这句话要能直接写进研报

于是「算不出来」被拆成两种，绝不混为一谈:

  · NOT_MEANINGFUL —— 该类公司下这个指标无意义，**本就不该算**
  · UNAVAILABLE    —— 应当有意义，但数据缺失，**是个问题**

这与项目既有的 SkipReason（「尚未披露」与「抓取失败」必须分开）是同一条
原则。混为一谈的后果很具体: 银行报告里出现一行「流动比率 —— 数据缺失」，
读者会以为是取数失败，而实际上任何人都取不到，因为它不存在。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.sources.eastmoney import CompanyType

ALL_TYPES = frozenset(CompanyType)
NONFINANCIAL = frozenset({CompanyType.GENERAL})
FINANCIAL = frozenset({CompanyType.BANK, CompanyType.INSURANCE,
                       CompanyType.SECURITIES})


class Category(str, Enum):
    """FINC6600 要求的五类。每类至少两个指标，趋势 + 同业对比。"""

    LIQUIDITY = "流动性"
    SOLVENCY = "偿债能力"
    PROFITABILITY = "盈利能力"
    EFFICIENCY = "运营效率"
    MARKET = "市场前景"


class Applicability(str, Enum):
    OK = "ok"
    NOT_MEANINGFUL = "not_meaningful"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    category: Category
    inputs: tuple[str, ...]
    applies_to: frozenset = field(default=ALL_TYPES)
    why_not: str = ""
    unit: str = "ratio"
    note: str = ""                  # 口径说明，随事实一起呈现

    def applicable_to(self, ct: CompanyType) -> bool:
        return ct in self.applies_to


@dataclass(frozen=True)
class MetricResult:
    spec: MetricSpec
    status: Applicability
    value: Decimal | None = None
    missing: tuple[str, ...] = ()

    def describe(self) -> str:
        if self.status is Applicability.OK:
            return f"{self.spec.label} {self.value}"
        if self.status is Applicability.NOT_MEANINGFUL:
            return f"{self.spec.label} —— 不适用：{self.spec.why_not}"
        return f"{self.spec.label} —— 数据缺失：{'、'.join(self.missing)}"


# ── 登记表 ────────────────────────────────────────────────────
# 分母为零、口径不符的处理放在算子里；这里只声明「需要什么、对谁成立」。

_NO_CURRENT_SPLIT = ("银行/保险/证券的资产负债表按流动性排序列示，"
                     "不区分流动与非流动 —— 该比率在会计上不存在")
_NO_INVENTORY = "金融企业无存货科目"
_NO_COGS = "金融企业无营业成本科目，成本结构是利息支出与业务管理费"

REGISTRY: tuple[MetricSpec, ...] = (
    # ── 流动性 ────────────────────────────────────────────
    MetricSpec("current_ratio", "流动比率", Category.LIQUIDITY,
               ("current_assets", "current_liabilities"),
               NONFINANCIAL, _NO_CURRENT_SPLIT, unit="x"),
    MetricSpec("quick_ratio", "速动比率", Category.LIQUIDITY,
               ("current_assets", "inventory", "current_liabilities"),
               NONFINANCIAL, _NO_CURRENT_SPLIT, unit="x",
               note="(流动资产 − 存货) / 流动负债"),
    MetricSpec("nwc_to_assets", "营运资本占总资产", Category.LIQUIDITY,
               ("current_assets", "current_liabilities", "total_assets"),
               NONFINANCIAL, _NO_CURRENT_SPLIT),

    # ── 偿债能力 ──────────────────────────────────────────
    MetricSpec("debt_to_equity", "负债权益比", Category.SOLVENCY,
               ("total_liabilities", "total_equity"), ALL_TYPES, unit="x",
               note="金融企业该比率天然远高于工商业，只可同业内比较"),
    MetricSpec("interest_coverage", "利息保障倍数", Category.SOLVENCY,
               ("operating_profit", "interest_expense_fe"),
               NONFINANCIAL,
               "金融企业的利息支出是经营性成本而非融资成本，该倍数无意义",
               unit="x", note="营业利润 / 利息费用"),
    MetricSpec("equity_multiplier", "权益乘数", Category.SOLVENCY,
               ("total_assets", "total_equity"), ALL_TYPES, unit="x"),

    # ── 盈利能力 ──────────────────────────────────────────
    MetricSpec("gross_margin", "毛利率", Category.PROFITABILITY,
               ("revenue", "cost_of_revenue"), NONFINANCIAL, _NO_COGS),
    MetricSpec("operating_margin", "营业利润率", Category.PROFITABILITY,
               ("operating_profit", "revenue_total"), ALL_TYPES),
    MetricSpec("net_margin", "净利率", Category.PROFITABILITY,
               ("net_profit", "revenue_total"), ALL_TYPES),
    MetricSpec("roa", "ROA", Category.PROFITABILITY,
               ("net_profit", "total_assets"), ALL_TYPES),
    MetricSpec("roe", "ROE", Category.PROFITABILITY,
               ("net_profit_attr_parent", "equity_attr_parent"), ALL_TYPES,
               note="归母口径 —— 少数股东权益占比高的公司，全口径会失真"),

    # ── 运营效率 ──────────────────────────────────────────
    MetricSpec("asset_turnover", "总资产周转率", Category.EFFICIENCY,
               ("revenue_total", "total_assets"), ALL_TYPES, unit="x"),
    MetricSpec("receivable_days", "应收账款周转天数", Category.EFFICIENCY,
               ("accounts_receivable", "revenue"), NONFINANCIAL,
               "金融企业的应收不构成经营循环", unit="天"),
    MetricSpec("inventory_days", "存货周转天数", Category.EFFICIENCY,
               ("inventory", "cost_of_revenue"), NONFINANCIAL,
               _NO_INVENTORY, unit="天"),
    MetricSpec("payable_days", "应付账款周转天数", Category.EFFICIENCY,
               ("accounts_payable", "cost_of_revenue"), NONFINANCIAL,
               _NO_COGS, unit="天"),
    MetricSpec("cash_conversion_cycle", "现金转换周期", Category.EFFICIENCY,
               ("accounts_receivable", "inventory", "accounts_payable",
                "revenue", "cost_of_revenue"),
               NONFINANCIAL, _NO_COGS, unit="天",
               note="应收天数 + 存货天数 − 应付天数"),
    MetricSpec("fixed_asset_turnover", "固定资产周转率", Category.EFFICIENCY,
               ("revenue_total", "fixed_asset"), NONFINANCIAL,
               "金融企业固定资产占比极低，该周转率无判别力", unit="x"),

    # ── 市场前景 ──────────────────────────────────────────
    MetricSpec("pe_ttm", "PE(TTM)", Category.MARKET,
               ("market_cap", "net_profit_ttm"), ALL_TYPES, unit="x"),
    MetricSpec("pb", "PB", Category.MARKET,
               ("market_cap", "equity_attr_parent"), ALL_TYPES, unit="x"),
    MetricSpec("roic", "ROIC", Category.MARKET,
               ("operating_profit", "interest_expense_fe", "income_tax",
                "total_profit", "total_equity", "cash_and_equivalents"),
               NONFINANCIAL,
               "金融企业的「投入资本」与经营资产不可分离，ROIC 无意义"),
    MetricSpec("ev_ebitda", "EV/EBITDA", Category.MARKET,
               ("market_cap", "operating_profit", "interest_expense_fe",
                "depreciation_amortization", "total_equity",
                "equity_attr_parent", "cash_and_equivalents"),
               NONFINANCIAL,
               "金融企业无 EBITDA 口径（折旧摊销不是主要成本）", unit="x",
               note="EV = 市值 + 有息负债 − 现金 + 少数股东权益"
                    "（所有者权益合计 − 归母权益）"),

    # ── 金融企业专有 ──────────────────────────────────────
    # 「不适用」不等于「无话可说」。银行有它自己该看的指标。
    MetricSpec("cost_income_ratio", "成本收入比", Category.PROFITABILITY,
               ("business_manage_expense", "revenue_total"),
               frozenset({CompanyType.BANK}),
               "该指标为银行专有", note="业务及管理费 / 营业收入"),
    MetricSpec("net_interest_income_ratio", "利息净收入占比",
               Category.PROFITABILITY,
               ("interest_net_income", "revenue_total"),
               frozenset({CompanyType.BANK}),
               "该指标为银行专有", note="衡量收入结构对息差的依赖度"),
)

BY_KEY: dict[str, MetricSpec] = {m.key: m for m in REGISTRY}


def by_category(ct: CompanyType) -> dict[Category, list[MetricSpec]]:
    """该公司类型下，各类别实际适用的指标。"""
    out: dict[Category, list[MetricSpec]] = {c: [] for c in Category}
    for m in REGISTRY:
        if m.applicable_to(ct):
            out[m.category].append(m)
    return out


def coverage_report(ct: CompanyType) -> str:
    """各类别是否满足 FINC6600「每类至少两个指标」的要求。

    不满足时如实说明 —— 银行的流动性类必然为零，这是结论不是缺陷。
    """
    lines = []
    for cat, ms in by_category(ct).items():
        if len(ms) >= 2:
            lines.append(f"  {cat.value:<6} {len(ms)} 个：" + "、".join(m.label for m in ms))
        elif ms:
            lines.append(f"  {cat.value:<6} 仅 {len(ms)} 个（{ms[0].label}）"
                         f"—— 少于两个，该类结论强度有限")
        else:
            why = next((m.why_not for m in REGISTRY
                        if m.category is cat and m.why_not), "该类指标均不适用")
            lines.append(f"  {cat.value:<6} 无适用指标 —— {why}")
    return "\n".join(lines)


def evaluate(ledger: FactLedger, period: str, as_of: date,
             ct: CompanyType, spot: str | None = None) -> list[MetricResult]:
    """逐个指标判定状态。**不计算数值** —— 计算在 operators/ratios.py。

    这里只回答「该不该算」和「能不能算」，因为这两个判断需要在多处复用:
    提示词要用它决定写什么，看板要用它决定显示什么，同业对比要用它决定
    比什么。把判定与计算分开，三处才不会各写一遍、各写错一遍。
    """
    spot_keys = {"market_cap", "pe_ttm", "pb", "last_price"}
    out: list[MetricResult] = []
    for m in REGISTRY:
        if not m.applicable_to(ct):
            out.append(MetricResult(m, Applicability.NOT_MEANINGFUL))
            continue
        missing = []
        for k in m.inputs:
            per = spot if (k in spot_keys and spot) else period
            try:
                ledger.get(k, per, as_of=as_of)
            except (KeyError, LookAheadError):
                missing.append(k)
        out.append(MetricResult(m, Applicability.OK) if not missing
                   else MetricResult(m, Applicability.UNAVAILABLE,
                                     missing=tuple(missing)))
    return out


def summarize(results: list[MetricResult]) -> str:
    ok = [r for r in results if r.status is Applicability.OK]
    na = [r for r in results if r.status is Applicability.NOT_MEANINGFUL]
    miss = [r for r in results if r.status is Applicability.UNAVAILABLE]
    lines = [f"指标可用性：{len(ok)} 可算 / {len(na)} 不适用 / {len(miss)} 缺数据"]
    if miss:
        lines.append("  ⚠ 应有而缺（需查取数）：")
        for r in miss:
            lines.append(f"      {r.spec.label} ← 缺 {'、'.join(r.missing)}")
    if na:
        lines.append("  · 该类公司不适用（非缺陷）：" +
                     "、".join(r.spec.label for r in na))
    return "\n".join(lines)
