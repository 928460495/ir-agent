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
from dataclasses import dataclass
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


EM_REPORTS: dict[str, ReportSpec] = {
    "balance": ReportSpec({
        "TOTAL_ASSETS": "total_assets",
        "TOTAL_LIABILITIES": "total_liabilities",
        "TOTAL_EQUITY": "total_equity",
        "TOTAL_PARENT_EQUITY": "equity_attr_parent",
        "MINORITY_EQUITY": "minority_equity",
    }),
    "income": ReportSpec({
        "OPERATE_INCOME": "revenue",
        "TOTAL_OPERATE_INCOME": "revenue_total",
        "OPERATE_COST": "cost_of_revenue",
        "NETPROFIT": "net_profit",
        "PARENT_NETPROFIT": "net_profit_attr_parent",
        "MINORITY_INTEREST": "minority_interest_profit",
    }),
    "cashflow": ReportSpec({
        "NETPROFIT": "cf_net_profit",
        "CCE_ADD": "cash_net_change",
        "BEGIN_CCE": "cash_begin",
        "END_CCE": "cash_end",
    }),
}

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

    for name, spec in EM_REPORTS.items():
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
        all_facts.extend(parse_em_rows(rows, spec, source_id=sid))
        time.sleep(_PAUSE)

    return all_facts, sids
