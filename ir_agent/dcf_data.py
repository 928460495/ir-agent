"""把事实账本铺成 ashare-dcf 要的报表形状 —— 一套数据供两边用。

**为什么必须统一。** 此前分析层走账本（东财 × 新浪双源、带披露日、有快照），
DCF 走 ashare_data 自己的抓取。同一家公司同一年的营收两边各取一次 ——
一旦分叉，研报正文写一个数、估值模型用另一个数，而没有任何地方会报错。

**为什么统一到账本而不是反过来。** 两个方向的映射工作量相当，差别在于
会失去什么:

  · **披露日**。账本的每条事实都带披露日，as_of 守卫据此判断「这个数在
    分析时点是否已知」。ashare_data 没有披露日，这道守卫会直接失效 ——
    而它在本项目里反复拦下过未来函数。
  · **双源交叉验证**与**快照溯源**。研报末尾的「数据来源」附录、
    看板里的逐条回查，都建立在快照上。
  · **同业批量**。行业与可比模块要拉 12 家同业的完整报表，账本走东财
    批量接口；ashare_data 是 akshare 逐家抓、3 次重试带休眠。

**这一层最容易出的错是静默的零。** 科目名写错一个字，val() 返回 None，
calibrate 当成「该公司没有这一项」继续算下去，不报任何错。因此:

  1. 科目名不是我照着印象写的，是从 ashare-dcf 源码里把 val()/_v() 实际
     查找的字符串全部抽出来核对过的；
  2. `verify_against_native()` 拿「账本铺出来的表」与「ashare_data 自己
     抓的表」逐项比对 —— 映射对不对，由数据说了算，不由我说了算。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.periods import sort_key

# 期间后缀 → 报告日的月日。ashare-dcf 用 YYYYMMDD 作索引。
_SUFFIX_MMDD = {"FY": "1231", "Q1": "0331", "H1": "0630",
                "Q1-Q3": "0930", "Q3": "0930"}

# 账本规范键 → 报表中文科目名。
#
# **右侧的名字全部来自 ashare-dcf 源码里 val()/_v() 实际查找的字符串**，
# 不是我按印象写的。写错一个字就是一个静默的零。
INCOME = {
    "revenue_total": "营业总收入",
    "revenue": "营业收入",
    "cost_of_revenue": "营业成本",
    "selling_expense": "销售费用",
    "admin_expense": "管理费用",
    "rd_expense": "研发费用",
    "operating_tax_surcharge": "营业税金及附加",
    "finance_expense": "财务费用",
    "other_income": "其他收益",
    "investment_income": "投资收益",
    "fairvalue_change_income": "公允价值变动收益",
    "credit_impairment": "信用减值损失",
    "asset_impairment": "资产减值损失",
    "asset_disposal_income": "资产处置收益",
    "operating_profit": "营业利润",
    "total_profit": "利润总额",
    "income_tax": "所得税费用",
    "net_profit": "净利润",
    "minority_interest_profit": "少数股东损益",
    "net_profit_attr_parent": "归属于母公司所有者的净利润",
}

BALANCE = {
    "cash_and_equivalents": "货币资金",
    "trading_finasset": "交易性金融资产",
    "note_accounts_receivable": "应收票据及应收账款",
    "inventory": "存货",
    "current_assets": "流动资产合计",
    "fixed_asset": "固定资产净额",
    "construction_in_progress": "在建工程",
    "long_equity_invest": "长期股权投资",
    "investment_property": "投资性房地产",
    "other_noncurrent_finasset": "其他非流动金融资产",
    "noncurrent_assets": "非流动资产合计",
    "total_assets": "资产总计",
    "short_loan": "短期借款",
    "note_accounts_payable": "应付票据及应付账款",
    "noncurrent_liab_1y": "一年内到期的非流动负债",
    "current_liabilities": "流动负债合计",
    "long_loan": "长期借款",
    "bond_payable": "应付债券",
    "total_liabilities": "负债合计",
    "minority_equity": "少数股东权益",
    "equity_attr_parent": "归属于母公司所有者权益合计",
    "total_equity": "所有者权益合计",
}

CASHFLOW = {
    "cf_operating": "经营活动产生的现金流量净额",
    "cf_investing": "投资活动产生的现金流量净额",
    "cf_financing": "筹资活动产生的现金流量净额",
    "capex": "购建固定资产、无形资产和其他长期资产所支付的现金",
    "depreciation_fixed_asset": "固定资产折旧、油气资产折耗、生产性生物资产折旧",
    "amortization_intangible": "无形资产摊销",
    "amortization_long_prepaid": "长期待摊费用摊销",
    "cash_begin": "期初现金及现金等价物余额",
    "cash_end": "期末现金及现金等价物余额",
    "cash_net_change": "现金及现金等价物净增加额",
}

TABLES = {"income": INCOME, "balance": BALANCE, "cashflow": CASHFLOW}


class MappingIncomplete(Exception):
    """账本铺不出可用的报表 —— 拒绝交给 DCF 建模。"""


def report_date(period: str) -> str | None:
    """2025FY → 20251231。认不出的期间返回 None 而非猜一个。"""
    for suf, mmdd in _SUFFIX_MMDD.items():
        if period.endswith(suf) and period[:4].isdigit():
            return period[:4] + mmdd
    return None


def _periods(ledger: FactLedger, as_of: date) -> list[str]:
    pers = {p for _, p in ledger._facts}                      # noqa: SLF001
    return sorted((p for p in pers if report_date(p)), key=sort_key)


def to_frames(ledger: FactLedger, as_of: date):
    """账本 → {表名: DataFrame}，形状与 ashare_data.fetch_statements 一致。

    索引是报告日（YYYYMMDD 字符串），列是中文科目名，**值以元计** ——
    ashare_data.val() 读出后会除以 1e8，这一层不做换算，免得两处各除一次。
    """
    import pandas as pd

    out = {}
    periods = _periods(ledger, as_of)
    if not periods:
        raise MappingIncomplete("账本里没有可识别的报告期间")

    for table, mapping in TABLES.items():
        rows: dict[str, dict[str, float]] = {}
        for per in periods:
            rd = report_date(per)
            row = {}
            for key, cn in mapping.items():
                try:
                    row[cn] = float(ledger.get(key, per, as_of=as_of).value)
                except (KeyError, LookAheadError):
                    continue
            if row:
                rows[rd] = row
        if not rows:
            raise MappingIncomplete(f"账本铺不出{table}表：没有任何科目取到值")
        df = pd.DataFrame.from_dict(rows, orient="index")
        df.index.name = "报告日"
        out[table] = df.sort_index()
    return out


def coverage(frames) -> dict[str, tuple[int, int]]:
    """每张表铺出了多少科目 / 应有多少。缺得多说明映射或取数有问题。"""
    return {t: (len([c for c in frames[t].columns if frames[t][c].notna().any()]),
                len(m)) for t, m in TABLES.items() if t in frames}


def verify_against_native(frames, native, items: int = 0) -> list[str]:
    """拿账本铺的表与 ashare_data 自己抓的表逐项比对。

    **映射对不对由数据说了算，不由我说了算。** 科目名写错一个字，val()
    返回 None，calibrate 当成「该公司没有这一项」继续算，不报任何错 ——
    这类静默的零只有靠比对才看得见。

    只在两边都有值的格子上比；一边缺则记为覆盖差异而非数值差异。
    """
    import math

    probs: list[str] = []
    for table, mapping in TABLES.items():
        a, b = frames.get(table), native.get(table)
        if a is None or b is None:
            continue
        for cn in mapping.values():
            if cn not in b.columns:
                continue
            if cn not in a.columns:
                probs.append(f"{table}.{cn}：账本没铺出这一项，"
                             f"而原生接口有 —— 映射缺项或账本缺字段")
                continue
            for rd in a.index.intersection(b.index):
                x, y = a.loc[rd, cn], b.loc[rd, cn]
                try:
                    x, y = float(x), float(y)
                except (TypeError, ValueError):
                    continue
                if math.isnan(x) or math.isnan(y) or y == 0:
                    continue
                if abs(x - y) / abs(y) > 0.005:
                    probs.append(f"{table}.{cn}@{rd}：账本 {x:,.0f} vs "
                                 f"原生 {y:,.0f}")
    return probs
