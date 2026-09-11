"""财报源: 东方财富 datacenter F10 —— **规范化接口**。

相比按文本行标签匹配的新浪，它有三项关键优势:
  1. **字段名规范化且跨行业一致**。通用/银行/保险/证券四套模板里，
     TOTAL_ASSETS、NETPROFIT、PARENT_NETPROFIT 都是同一个名字 ——
     换行业只换模板名，映射表不用动，标签变体问题整类消失。
     （对比新浪: 「所有者权益(或股东权益)合计」/「股东权益合计」/
     「所有者权益合计」三家三个写法，只能靠别名表+正则去追。）
  2. 自带 NOTICE_DATE（公告日），可独立提供 as_of 并校验巨潮的披露日。
  3. 结构化 JSON，不依赖 GBK 文本解析。

注意东财有反爬与频控，本模块串行请求且带间隔，不做并发。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

import requests

from ir_agent.sources.retry import with_retry

API = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
TIMEOUT = 20
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_PAUSE = 0.5


class CompanyType(str, Enum):
    """报表模板类型。东财按行业提供四套模板，字段名一致、行项目不同。"""
    GENERAL = "G"       # 通用（工商业）
    BANK = "B"          # 银行
    INSURANCE = "I"     # 保险
    SECURITIES = "S"    # 证券


_STMT_SUFFIX = {"balance": "BALANCE", "income": "INCOME", "cashflow": "CASHFLOW"}


def report_name(stmt: str, company_type: CompanyType) -> str:
    return f"RPT_F10_FINANCE_{company_type.value}{_STMT_SUFFIX[stmt]}"


@dataclass(frozen=True)
class ReportSpec:
    fields: dict[str, str]          # 东财字段 -> 规范化 key
    # 规范化 key -> 同义 key。用于一个模板缺某个概念、但另一个字段在该模板下
    # 语义等同的情形。实测: 银行/保险的利润表没有 TOTAL_OPERATE_INCOME，
    # 因为对它们而言「营业收入」就是全部营业收入，不存在「营业总收入」这一层。
    # 不做这个别名，所有以 revenue_total 为分母的指标都会误报为「数据缺失」。
    aliases: dict = field(default_factory=dict)


# ── 字段映射 ──────────────────────────────────────────────────
# **按模板分设，不能一套通吃。** 实测四类公司的资产负债表:
#
#   字段                     茅台   招行   平安   中信证券
#   TOTAL_CURRENT_ASSETS     有     —      —      —
#   INVENTORY                有     —      —      —
#   MONETARYFUNDS            有     —      有     有
#   SHORT_LOAN               —      —      有     有
#
# 银行缺流动资产/存货**不是数据缺失，是会计上不存在** —— 银行资产负债表
# 按流动性排序列示，不分流动与非流动。早期一套映射套四套模板，能用是因为
# 只映射了四套都有的汇总项；一旦补明细科目，银行就会全空，而系统分不清
# 「本就没有」和「取数失败」。适用性判定见 metrics.py。

# 四套模板共有的骨架
_COMMON_BALANCE = {
    "TOTAL_ASSETS": "total_assets",
    "TOTAL_LIABILITIES": "total_liabilities",
    "TOTAL_EQUITY": "total_equity",
    "TOTAL_PARENT_EQUITY": "equity_attr_parent",
    "MINORITY_EQUITY": "minority_equity",
    "FIXED_ASSET": "fixed_asset",
    "CIP": "construction_in_progress",
    "INTANGIBLE_ASSET": "intangible_asset",
    "USERIGHT_ASSET": "useright_asset",
    "SHARE_CAPITAL": "share_capital",
    "CAPITAL_RESERVE": "capital_reserve",
    "SURPLUS_RESERVE": "surplus_reserve",
    "UNASSIGN_RPOFIT": "retained_earnings",     # Altman Z 的 B 项
    "BOND_PAYABLE": "bond_payable",
    "LEASE_LIAB": "lease_liability",
    "TAX_PAYABLE": "tax_payable",
    "STAFF_SALARY_PAYABLE": "staff_salary_payable",
    "DEFER_TAX_ASSET": "deferred_tax_asset",
    "DEFER_TAX_LIAB": "deferred_tax_liability",
}

_COMMON_INCOME = {
    "OPERATE_INCOME": "revenue",
    "TOTAL_OPERATE_INCOME": "revenue_total",
    "OPERATE_PROFIT": "operating_profit",
    "TOTAL_PROFIT": "total_profit",
    "INCOME_TAX": "income_tax",
    "OPERATE_TAX_ADD": "operating_tax_surcharge",
    "NETPROFIT": "net_profit",
    "PARENT_NETPROFIT": "net_profit_attr_parent",
    "MINORITY_INTEREST": "minority_interest_profit",
    "DEDUCT_PARENT_NETPROFIT": "net_profit_deducted",
    "BASIC_EPS": "basic_eps",
    "INVEST_INCOME": "investment_income",
}

_COMMON_CASHFLOW = {
    "NETPROFIT": "cf_net_profit",
    "CCE_ADD": "cash_net_change",
    "BEGIN_CCE": "cash_begin",
    "END_CCE": "cash_end",
    "NETCASH_OPERATE": "cf_operating",
    "NETCASH_INVEST": "cf_investing",
    "NETCASH_FINANCE": "cf_financing",
    "CONSTRUCT_LONG_ASSET": "capex",
    "PAY_ALL_TAX": "tax_paid",
    # 分配股利、利润或偿付利息支付的现金 —— 中国准则把它放在**筹资活动**，
    # 因此 A 股的经营活动现金流是未扣利息的。派息率与 kd 的交叉验证都用它。
    "ASSIGN_DIVIDEND_PORFIT": "dividend_interest_paid",
    # 折旧摊销四件套 —— FCF 直接法必需，此前全部缺失
    "FA_IR_DEPR": "depreciation_fixed_asset",
    "IA_AMORTIZE": "amortization_intangible",
    "LPE_AMORTIZE": "amortization_long_prepaid",
    "USERIGHT_ASSET_AMORTIZE": "amortization_useright",
    # 营运资本变动三件套 —— 接口直接给了 ΔNWC 的分解
    "INVENTORY_REDUCE": "wc_inventory_decrease",
    "OPERATE_RECE_REDUCE": "wc_receivable_decrease",
    "OPERATE_PAYABLE_ADD": "wc_payable_increase",
}

# 工商业专有 —— 流动/非流动划分、存货、应收应付、三费
_GENERAL_BALANCE = {
    **_COMMON_BALANCE,
    # 下面四项是接 ashare-dcf 时补的: 三表模型要算非经营性资产与类现金，
    # 缺了就只能当 0，净负债会系统性偏高。**字段名实测过** ——
    # TRADE_FINASSET 恒为 None，真正有值的是 TRADE_FINASSET_NOTFVTPL。
    "TRADE_FINASSET_NOTFVTPL": "trading_finasset",
    "LONG_EQUITY_INVEST": "long_equity_invest",
    "OTHER_NONCURRENT_FINASSET": "other_noncurrent_finasset",
    "OTHER_NONCURRENT_ASSET": "other_noncurrent_asset",
    "MONETARYFUNDS": "cash_and_equivalents",
    "TOTAL_CURRENT_ASSETS": "current_assets",
    "TOTAL_CURRENT_LIAB": "current_liabilities",
    "TOTAL_NONCURRENT_ASSETS": "noncurrent_assets",
    "TOTAL_NONCURRENT_LIAB": "noncurrent_liabilities",
    "INVENTORY": "inventory",
    "ACCOUNTS_RECE": "accounts_receivable",
    "NOTE_ACCOUNTS_RECE": "note_accounts_receivable",
    "PREPAYMENT": "prepayment",
    "ACCOUNTS_PAYABLE": "accounts_payable",
    "NOTE_ACCOUNTS_PAYABLE": "note_accounts_payable",
    "CONTRACT_LIAB": "contract_liability",      # 合同负债（原预收款项）
    "SHORT_LOAN": "short_loan",
    "LONG_LOAN": "long_loan",
    "NONCURRENT_LIAB_1YEAR": "noncurrent_liab_1y",
    "GOODWILL": "goodwill",
    "INVEST_REALESTATE": "investment_property",
}

_GENERAL_INCOME = {
    **_COMMON_INCOME,
    # 同上: 其他收益/公允价值变动/资产处置/资产减值是营业利润的组成项，
    # 三表模型要逐项预测，缺了会让营业利润对不上。
    "OTHER_INCOME": "other_income",
    "FAIRVALUE_CHANGE_INCOME": "fairvalue_change_income",
    "ASSET_DISPOSAL_INCOME": "asset_disposal_income",
    "ASSET_IMPAIRMENT_INCOME": "asset_impairment",
    "OPERATE_COST": "cost_of_revenue",
    "TOTAL_OPERATE_COST": "cost_total",
    "SALE_EXPENSE": "selling_expense",
    "MANAGE_EXPENSE": "admin_expense",
    "RESEARCH_EXPENSE": "rd_expense",
    "FINANCE_EXPENSE": "finance_expense",
    # 财务费用中的利息费用 —— 算 kd 与利息保障倍数的分母
    "FE_INTEREST_EXPENSE": "interest_expense_fe",
    "FE_INTEREST_INCOME": "interest_income_fe",
    "CREDIT_IMPAIRMENT_INCOME": "credit_impairment",
}

# 银行专有 —— 生息资产/负债、利息与手续费净收入、业务及管理费
_BANK_BALANCE = {
    **_COMMON_BALANCE,
    "CASH_DEPOSIT_PBC": "cash_deposit_pbc",
    "DEPOSIT_INTERBANK": "deposit_interbank",
    "LOAN_ADVANCE": "loan_advance",             # 发放贷款和垫款
    "ACCEPT_DEPOSIT": "accept_deposit",         # 吸收存款
    "BORROW_FUND": "borrow_fund",
    "LEND_FUND": "lend_fund",
}

_BANK_INCOME = {
    **_COMMON_INCOME,
    "INTEREST_NI": "interest_net_income",
    "INTEREST_INCOME": "interest_income",
    "INTEREST_EXPENSE": "interest_expense",
    "FEE_COMMISSION_NI": "fee_commission_net_income",
    "BUSINESS_MANAGE_EXPENSE": "business_manage_expense",
    "CREDIT_IMPAIRMENT_LOSS": "credit_impairment_loss",
}

# 保险与证券: 有货币资金与借款，但无流动性划分与存货
_FINANCIAL_BALANCE = {
    **_COMMON_BALANCE,
    "MONETARYFUNDS": "cash_and_equivalents",
    "SHORT_LOAN": "short_loan",
    "LONG_LOAN": "long_loan",
    "ACCOUNTS_PAYABLE": "accounts_payable",
    "ACCOUNTS_RECE": "accounts_receivable",
}

_FINANCIAL_INCOME = {
    **_COMMON_INCOME,
    "INTEREST_INCOME": "interest_income",
    "INTEREST_EXPENSE": "interest_expense",
    "FEE_COMMISSION_NI": "fee_commission_net_income",
}

_BY_TYPE: dict[CompanyType, dict[str, ReportSpec]] = {
    CompanyType.GENERAL: {
        "balance": ReportSpec(_GENERAL_BALANCE),
        "income": ReportSpec(_GENERAL_INCOME),
        "cashflow": ReportSpec(_COMMON_CASHFLOW),
    },
    CompanyType.BANK: {
        "balance": ReportSpec(_BANK_BALANCE),
        "income": ReportSpec(_BANK_INCOME, {"revenue_total": "revenue"}),
        "cashflow": ReportSpec(_COMMON_CASHFLOW),
    },
    CompanyType.INSURANCE: {
        "balance": ReportSpec(_FINANCIAL_BALANCE),
        "income": ReportSpec(_FINANCIAL_INCOME, {"revenue_total": "revenue"}),
        "cashflow": ReportSpec(_COMMON_CASHFLOW),
    },
    CompanyType.SECURITIES: {
        "balance": ReportSpec(_FINANCIAL_BALANCE),
        "income": ReportSpec(_FINANCIAL_INCOME, {"revenue_total": "revenue"}),
        "cashflow": ReportSpec(_COMMON_CASHFLOW),
    },
}

STATEMENTS = ("balance", "income", "cashflow")


def specs_for(ct: CompanyType) -> dict[str, ReportSpec]:
    return _BY_TYPE[ct]


# 兼容旧调用 —— 通用模板即原先那一套的超集
EM_REPORTS: dict[str, ReportSpec] = _BY_TYPE[CompanyType.GENERAL]

_MMDD = {"1231": "FY", "0930": "Q1-Q3", "0630": "H1", "0331": "Q1"}


def period_from_report(report_date: str) -> str:
    """'2025-12-31 00:00:00' → '2025FY'，与新浪适配器同一套标签。"""
    d = datetime.strptime(report_date[:10], "%Y-%m-%d").date()
    suffix = _MMDD.get(f"{d.month:02d}{d.day:02d}")
    if suffix is None:
        raise ValueError(f"无法识别的报表日期: {report_date}")
    return f"{d.year}{suffix}"


_TYPE_CACHE: dict[str, CompanyType] = {}


def _probe(code: str, market: str, ct: CompanyType,
           session: requests.Session | None = None) -> bool:
    """该模板下是否有数据。有 = 该公司属于这个行业类别。"""
    s = session or requests
    params = {
        "reportName": report_name("balance", ct),
        "columns": "SECUCODE,REPORT_DATE",
        "filter": f'(SECUCODE="{code}.{market}")',
        "pageSize": 1, "sortColumns": "REPORT_DATE", "sortTypes": "-1",
        "source": "HSF10", "client": "PC",
    }
    try:
        r = with_retry(lambda: s.get(API, params=params, headers=_HEADERS,
                                     timeout=TIMEOUT))
        payload = r.json()
    except Exception:                               # noqa: BLE001
        return False
    return bool((payload.get("result") or {}).get("data"))


def detect_company_type(code: str, market: str,
                        session: requests.Session | None = None) -> CompanyType:
    """依次试探四套模板。通用型最常见，放在最前。

    探测不到时退回 GENERAL —— 宁可用通用模板拿到部分字段，
    也好过因为无法归类而完全没有数据。
    """
    if code in _TYPE_CACHE:
        return _TYPE_CACHE[code]
    for ct in (CompanyType.GENERAL, CompanyType.BANK,
               CompanyType.INSURANCE, CompanyType.SECURITIES):
        if _probe(code, market, ct, session):
            _TYPE_CACHE[code] = ct
            return ct
        time.sleep(_PAUSE / 2)
    return CompanyType.GENERAL


def parse_em_rows(rows: list[dict], spec: ReportSpec, source_id: str):
    from ir_agent.ledger import Fact, Method

    facts: list[Fact] = []
    for row in rows:
        notice = row.get("NOTICE_DATE")
        if not notice:
            continue                       # 没有公告日就没有 as_of，丢弃
        as_of = datetime.strptime(notice[:10], "%Y-%m-%d").date()
        try:
            period = period_from_report(row["REPORT_DATE"])
        except (KeyError, ValueError):
            continue

        for em_field, key in spec.fields.items():
            value = row.get(em_field)
            if value is None:
                continue                   # 缺失就是缺失，不补 0
            facts.append(Fact(
                key=key, value=Decimal(str(value)), unit="元", currency="CNY",
                period=period, as_of=as_of, source_id=source_id,
                method=Method.REPORTED,
            ))
    return facts


def _alias_facts(facts, spec: ReportSpec):
    """按模板声明补出同义事实。

    只在目标 key 该期**确实没有**时才补，绝不覆盖真值。补出的事实标记为
    COMPUTED 并写明 derived_from —— 它是一次口径映射，不是原始披露，
    脚注里必须能看出这个区别。
    """
    from ir_agent.ledger import Fact, Method

    if not spec.aliases:
        return []
    have = {(f.key, f.period) for f in facts}
    by = {(f.key, f.period): f for f in facts}
    out = []
    for target, src in spec.aliases.items():
        for (key, period), f in by.items():
            if key != src or (target, period) in have:
                continue
            out.append(Fact(
                key=target, value=f.value, unit=f.unit, period=period,
                as_of=f.as_of, currency=f.currency, source_id=f.source_id,
                method=Method.COMPUTED,
                derived_from=(f"{src}@{period}",)))
    return out


def fetch_statements_em(
    code: str,
    market: str = "SH",
    # 40 期约覆盖 10 个年度。默认 12 只剩 3 个年度，
    # 不足以判定周期性（见 valuation/route.py）。
    page_size: int = 40,
    store=None,
    session: requests.Session | None = None,
    company_type: CompanyType | None = None,
):
    """返回 (facts, {表名: source_id})。自动识别行业模板。"""
    s = session or requests.Session()
    ct = company_type or detect_company_type(code, market, s)
    all_facts = []
    sids: dict[str, str] = {}

    # 按公司类型取对应模板的字段映射 —— 银行取银行的科目，
    # 而不是拿工商业的科目去银行报表里找、找不到再当成缺数据。
    for name, spec in specs_for(ct).items():
        params = {
            "reportName": report_name(name, ct),
            "columns": "ALL",
            "filter": f'(SECUCODE="{code}.{market}")',
            "pageSize": page_size,
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "source": "HSF10",
            "client": "PC",
        }
        fetched_at = datetime.now()
        def _get():
            r = s.get(API, params=params, headers=_HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()

        payload = with_retry(_get)
        rows = (payload.get("result") or {}).get("data") or []

        sid = (store.save(source=f"em_{ct.value.lower()}_{name}", payload=payload, url=API,
                          fetched_at=fetched_at, params=params)
               if store else f"em_{ct.value.lower()}_{name}_{code}_{fetched_at:%Y%m%d%H%M%S}")
        sids[name] = sid
        parsed = parse_em_rows(rows, spec, source_id=sid)
        all_facts.extend(parsed)
        all_facts.extend(_alias_facts(parsed, spec))
        time.sleep(_PAUSE)

    return all_facts, sids
