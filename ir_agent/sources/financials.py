"""三表适配器（新浪财报下载接口）。

两个必须记住的口径问题:
  1. 新浪返回的是**累计**口径。20250930 列是前三季度累计，不是 Q3 单季。
     period_label 因此把它标为 2025Q1-Q3，防止有人拿它当单季做同比。
  2. 这份数据里**没有披露日**。as_of 必须由调用方从巨潮公告日传入。
     用抓取日代替披露日 = 未来函数，且不会报错。
"""

from __future__ import annotations

from datetime import date, datetime
import re
from decimal import Decimal, InvalidOperation
from enum import Enum

import requests

from ir_agent.sources.retry import with_retry

BASE = ("https://money.finance.sina.com.cn/corp/go.php/{path}"
        "/displaytype/4/stockid/{code}/ctrl/{year}/all.phtml")
TIMEOUT = 25
_HEADERS = {"User-Agent": "Mozilla/5.0"}


class Statement(str, Enum):
    BALANCE = "vDOWN_BalanceSheet"
    PROFIT = "vDOWN_ProfitStatement"
    CASHFLOW = "vDOWN_CashFlow"


# 每张表各自的映射 —— 「净利润」在利润表和现金流量表都出现，必须分开落键
_MAP: dict[Statement, dict[str, str]] = {
    Statement.BALANCE: {
        "资产总计": "total_assets",
        "负债合计": "total_liabilities",
        # 权益行的写法各行业不同: 制造业 / 银行 / 保险各一套
        "所有者权益(或股东权益)合计": "total_equity",
        "股东权益合计": "total_equity",
        "所有者权益合计": "total_equity",
        "归属于母公司股东权益合计": "equity_attr_parent",
        "归属于母公司股东的权益": "equity_attr_parent",
        "归属于母公司的股东权益合计": "equity_attr_parent",
        "归属于母公司所有者权益合计": "equity_attr_parent",
        "少数股东权益": "minority_equity",
        "货币资金": "cash_and_equivalents",
        "流动资产合计": "current_assets",
        "流动负债合计": "current_liabilities",
    },
    Statement.PROFIT: {
        "营业收入": "revenue",
        "营业总收入": "revenue_total",
        "营业成本": "cost_of_revenue",
        "营业总成本": "cost_total",
        "净利润": "net_profit",
        "归属于母公司所有者的净利润": "net_profit_attr_parent",
        "少数股东损益": "minority_interest_profit",
    },
    Statement.CASHFLOW: {
        "净利润": "cf_net_profit",
        "现金及现金等价物净增加额": "cash_net_change",
        "现金及现金等价物的净增加额": "cash_net_change",
        "期初现金及现金等价物余额": "cash_begin",
        "期末现金及现金等价物余额": "cash_end",
    },
}

_QUARTER_LABEL = {"0331": "Q1", "0630": "H1", "0930": "Q1-Q3", "1231": "FY"}

# CAS 报表行标签带中文序号前缀，且各行业写法不同:
#   制造业「一、营业总收入」 vs 保险「一、营业收入」 vs 现金流量表「加:期初…」
# 精确匹配会在跨行业时大面积失配，先规范化再映射。
_PREFIX = re.compile(r"^\s*(?:[一二三四五六七八九十]+、|加[:：]|减[:：])\s*")


def normalize_label(label: str) -> str:
    """剥掉行标签的中文序号/加减前缀。仅剥首部，行内标点保留。"""
    prev = None
    out = label.strip()
    while prev != out:
        prev, out = out, _PREFIX.sub("", out, count=1).strip()
    return out


def period_label(report_date: str) -> str:
    """20250930 → 2025Q1-Q3（累计口径显式写进标签）。"""
    year, mmdd = report_date[:4], report_date[4:]
    suffix = _QUARTER_LABEL.get(mmdd)
    if suffix is None:
        raise ValueError(f"无法识别的报表日期: {report_date}")
    return f"{year}{suffix}"


def parse_sina_table(text: str) -> dict[str, dict[str, Decimal]]:
    """GBK TSV → {period: {行项目: Decimal}}。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("报表日期"):
        raise ValueError("不是新浪财报表格式（首行应为「报表日期」）。")

    raw_dates = [c.strip() for c in lines[0].split("\t")[1:] if c.strip()]
    cols: list[tuple[int, str]] = []
    for i, rd in enumerate(raw_dates):
        if rd.startswith("1970"):        # 新浪的占位列
            continue
        try:
            cols.append((i, period_label(rd)))
        except ValueError:
            continue

    out: dict[str, dict[str, Decimal]] = {label: {} for _, label in cols}

    for line in lines[1:]:
        cells = line.split("\t")
        label = cells[0].strip()
        if not label or label == "单位":
            continue
        for idx, period in cols:
            if idx + 1 >= len(cells):
                continue
            raw = cells[idx + 1].strip()
            if not raw:
                continue
            try:
                out[period][label] = Decimal(raw)
            except InvalidOperation:
                continue

    return out


# 形状正则 —— 别名表之外的兜底。发行人写法太多，穷举追不完:
#   「归属于母公司所有者的净利润」/「…的净利润」/「…股东的净利润」/「…普通股东的净利润」
# 只对**结构稳定**的行使用，且必须足够窄，不能吃掉相近的行（如综合收益总额）。
_SHAPES: dict[Statement, list[tuple[re.Pattern[str], str]]] = {
    Statement.BALANCE: [
        (re.compile(r"^归属于母公司.{0,6}(?:股东|所有者)权益(?:合计)?$"),
         "equity_attr_parent"),
    ],
    Statement.PROFIT: [
        (re.compile(r"^归属于母公司.{0,8}净利润$"), "net_profit_attr_parent"),
    ],
    Statement.CASHFLOW: [],
}


def map_label(label: str, stmt: "Statement") -> str | None:
    """先查别名表（精确、优先），再走形状正则（泛化、兜底）。"""
    clean = normalize_label(label)
    key = _MAP[stmt].get(clean)
    if key is not None:
        return key
    for pattern, mapped in _SHAPES.get(stmt, []):
        if pattern.match(clean):
            return mapped
    return None


def to_facts(
    tables: dict[Statement, dict[str, dict[str, Decimal]]],
    as_of_by_period: dict[str, date],
    source_id: str | dict[Statement, str],
):
    """映射为规范化事实。没有披露日的期间直接丢弃 —— 不猜 as_of。

    source_id 传 dict 时按表分别归属。指向错误快照的溯源比没有溯源更危险，
    因为它看起来是通过的 —— 生产路径务必传 dict。
    """
    from ir_agent.ledger import Fact, Method

    def sid_for(stmt: Statement) -> str:
        return source_id[stmt] if isinstance(source_id, dict) else source_id

    facts: list[Fact] = []
    for stmt, table in tables.items():
        sid = sid_for(stmt)
        for period, rows in table.items():
            as_of = as_of_by_period.get(period)
            if as_of is None:
                continue
            # 同一 key 可能被多行命中: 主表行与「补充资料」（间接法）行都叫
            # 「现金及现金等价物净增加额」。主表行在前，**首个命中获胜** ——
            # 后写覆盖会让未填写的补充资料（0）盖掉正确值（中国国航实测）。
            seen: set[str] = set()
            for label, value in rows.items():
                key = map_label(label, stmt)
                if key is None or key in seen:
                    continue
                seen.add(key)
                facts.append(Fact(
                    key=key, value=value, unit="元", currency="CNY",
                    period=period, as_of=as_of, source_id=sid,
                    method=Method.REPORTED,
                ))
    return facts


def fetch_statements(
    code: str,
    year: int,
    store=None,
    session: requests.Session | None = None,
) -> dict[Statement, dict[str, dict[str, Decimal]]]:
    """只返回表。需要溯源请用 fetch_statements_with_provenance。"""
    tables, _ = fetch_statements_with_provenance(code, year, store, session)
    return tables


def fetch_statements_with_provenance(
    code: str,
    year: int,
    store=None,
    session: requests.Session | None = None,
) -> tuple[dict[Statement, dict[str, dict[str, Decimal]]], dict[Statement, str]]:
    """返回 (表, 每张表对应的快照 source_id)。"""
    s = session or requests.Session()
    tables: dict[Statement, dict[str, dict[str, Decimal]]] = {}
    sids: dict[Statement, str] = {}

    for stmt in Statement:
        url = BASE.format(path=stmt.value, code=code, year=year)
        fetched_at = datetime.now()
        def _get():
            r = s.get(url, headers=_HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            r.encoding = "gbk"
            return r.text

        text = with_retry(_get)

        if store is not None:
            sids[stmt] = store.save(
                source=f"sina_{stmt.name.lower()}", payload=text,
                url=url, fetched_at=fetched_at,
                params={"code": code, "year": year})
        else:
            sids[stmt] = f"sina_{stmt.name.lower()}_{code}_{year}"

        tables[stmt] = parse_sina_table(text)

    return tables, sids
