"""巨潮资讯公告适配器 —— 投研时效性的上游。

关键: announcementTime（披露日）才是 as_of，抓取日不是。把抓取日当 as_of
会让回测凭空多出几天信息优势，而且不会报错。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime

import requests

from ir_agent.sources.retry import with_retry

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_BASE = "http://static.cninfo.com.cn/"
TIMEOUT = 20

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
}

_CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    ("定期报告", re.compile(r"(年度报告|半年度报告|第[一三]季度报告|季度报告)(?!.*摘要)")),
    ("业绩预告", re.compile(r"业绩(预告|快报|预增|预减|预亏)")),
    ("股东大会", re.compile(r"股东大会")),
    ("股份变动", re.compile(r"(增持|减持|回购|股份变动|限售股|解禁)")),
    ("重大事项", re.compile(r"(重大|收购|重组|合并|资产出售|对外投资)")),
]


@dataclass(frozen=True)
class Announcement:
    ann_id: str
    title: str
    ann_date: date          # 披露日 —— 下游一律以此为 as_of
    url: str
    code: str
    name: str
    category: str
    source_id: str


def classify(title: str) -> str:
    for label, pattern in _CATEGORIES:
        if pattern.search(title):
            return label
    return "其他"


def parse_announcements(payload: dict, source_id: str) -> list[Announcement]:
    items = payload.get("announcements") or []
    out: list[Announcement] = []
    for a in items:
        ts = a["announcementTime"]
        ann_date = datetime.fromtimestamp(ts / 1000).date()
        out.append(Announcement(
            ann_id=str(a["announcementId"]),
            title=a["announcementTitle"],
            ann_date=ann_date,
            url=STATIC_BASE + a["adjunctUrl"],
            code=a.get("secCode", ""),
            name=a.get("secName", ""),
            category=classify(a["announcementTitle"]),
            source_id=source_id,
        ))
    return out


SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
_ORG_CACHE: dict[str, str] = {}


class UnknownSecurity(Exception):
    """cninfo 查不到该证券代码。"""


def parse_org_id(payload: list[dict], code: str) -> str:
    """从 topSearch 结果中取出精确匹配该代码的 orgId。

    orgId 是 cninfo 内部标识，**格式不统一也不可从代码推导**:
    贵州茅台是 gssh0600519，隆基绿能是 9900022338。
    任何 f'gssh0{code}' 式的猜测只会在部分标的上碰巧成立。
    """
    for row in payload or []:
        if str(row.get("code", "")).strip() == code:
            org = str(row.get("orgId", "")).strip()
            if org:
                return org
    raise UnknownSecurity(f"cninfo 未收录证券代码 {code}，无法确定 orgId。")


def lookup_org_id(code: str, session: requests.Session | None = None) -> str:
    if code in _ORG_CACHE:
        return _ORG_CACHE[code]
    s = session or requests
    def _post():
        r = s.post(SEARCH_URL, headers=_HEADERS,
                   data={"keyWord": code, "maxNum": 10}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    org = parse_org_id(with_retry(_post), code)
    _ORG_CACHE[code] = org
    return org


def _stock_param(code: str, session: requests.Session | None = None) -> str:
    return f"{code},{lookup_org_id(code, session)}"


class TruncatedResult(Exception):
    """达到分页上限但上游仍有数据 —— 结果不完整。"""


def paginate(fetch_page, max_pages: int = 10, strict: bool = False):
    """翻页直到上游说没有了。返回 (条目, 是否被我们的上限截断)。

    截断必须显式回报: 静默截断会让「我们没抓到」伪装成「数据不存在」，
    下游只会看到某个章节缺失，而查不出真正原因。
    """
    items: list = []
    for page in range(1, max_pages + 1):
        payload = fetch_page(page)
        items.extend(payload.get("announcements") or [])
        if not payload.get("hasMore"):
            return items, False

    if strict:
        raise TruncatedResult(
            f"已达 {max_pages} 页上限但上游仍有数据，结果不完整。"
            "请收窄日期区间或提高 max_pages。"
        )
    return items, True


def search_with_status(
    code: str,
    start: date,
    end: date,
    page_size: int = 30,
    max_pages: int = 40,
    store=None,
    session: requests.Session | None = None,
) -> tuple[list[Announcement], bool]:
    """返回 (公告列表, 是否被截断)。"""
    s = session or requests.Session()
    results: list[Announcement] = []
    stock_param = _stock_param(code, s)
    truncated = False

    for page in range(1, max_pages + 1):
        data = {
            "pageNum": page,
            "pageSize": page_size,
            "column": "szse",
            "tabName": "fulltext",
            "stock": stock_param,
            "seDate": f"{start:%Y-%m-%d}~{end:%Y-%m-%d}",
            "isHLtitle": "true",
        }
        fetched_at = datetime.now()

        def _post():
            r = s.post(QUERY_URL, headers=_HEADERS, data=data, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()

        payload = with_retry(_post)

        source_id = (
            store.save(source="cninfo", payload=payload, url=QUERY_URL,
                       fetched_at=fetched_at, params=data)
            if store else f"cninfo_live_{fetched_at:%Y%m%d%H%M%S}_p{page}"
        )

        results.extend(parse_announcements(payload, source_id=source_id))
        if not payload.get("hasMore"):
            break
        time.sleep(0.4)        # 官方源也要限流，别把自己搞成爬虫
    else:
        # 循环跑满 max_pages 且从未 break —— 上游还有数据被我们截断
        truncated = True

    return results, truncated


def search(
    code: str,
    start: date,
    end: date,
    page_size: int = 30,
    max_pages: int = 40,
    store=None,
    session: requests.Session | None = None,
) -> list[Announcement]:
    """按日期区间拉取公告。截断信息见 search_with_status。"""
    results, _ = search_with_status(code, start, end, page_size, max_pages,
                                    store, session)
    return results
