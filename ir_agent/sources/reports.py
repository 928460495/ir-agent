"""卖方研报 —— 行业深度资料的主要来源，以及一致预期的对照物。

**两种完全不同的用途，必须分开。** 一篇券商研报里同时有:

  · **事实**: 行业规模、产量、开工率、政策原文、产业链上下游结构、
    竞争格局。这些是分析师从统计局、行业协会、草根调研里搬来的二手信息，
    可以引用，注明二手即可 —— 真实的研究员也是这么干的。
  · **观点**: 盈利预测、评级、目标价。这些**不得作为本报告任何假设的依据**。
    让模型拿券商的 EPS 预测去论证自己的 EPS 预测，结论绕一圈回到起点，
    还平白多了一层「有依据」的外衣。

观点的正当用途只有一个: **一致预期，作为本报告结论的对照**。教材里的标准
动作是「把你的估计与一致预期比较，并解释差异从何而来」—— 差异本身是研报
里最有价值的段落，但前提是两者独立得出。

**财年锚定是这里最容易静默出错的地方。** 接口的 predictThisYearEps 是
**相对发布日**的:

    2024-06 发布  本年 EPS 96.47   → FY2024
    2026-08 发布  本年 EPS 69.83   → FY2026（标题即「2026年中报点评」）

把两者混进同一个「本年一致预期」求中位数，得到的是把不同年度平均起来的
无意义数字，而且不会报任何错。因此每条预测都按其发布日折算到**绝对财年**。

顺带落出一个有分析价值的副产品: 同一财年在不同时点的预期变化。茅台
FY2026 的 EPS 一致预期，2025 年发布的研报给 84.49，2026 年发布的已降到
68.12 —— **卖方下修的轨迹本身就是信息**，比某个时点的静态数字更能说明问题。
"""

from __future__ import annotations

import json
import re
import ssl
import statistics
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ir_agent.sources.external import Document, Tier

_LIST = "https://reportapi.eastmoney.com/report/list"
_BK = "https://reportapi.eastmoney.com/report/bk?bkCode=016"
# 宏观研究走另一个端点。list 接口的 qType=3 返回空，jg 接口才有数据。
_JG = "https://reportapi.eastmoney.com/report/jg"
_PAGE = "https://data.eastmoney.com/report/info/{code}.html"
# 宏观研报没有 infoCode，只有 encodeUrl，详情页也是另一条路径。
_MACRO_PAGE = ("https://data.eastmoney.com/report/"
               "zw_macresearch.jshtml?encodeUrl={enc}")
_REF = "https://data.eastmoney.com/report/"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 只有正文里的这一块是研报内容，其余是免责声明与评级说明。
_CTX = re.compile(r'<div[^>]*class="ctx-content"[^>]*>(.*?)</div>', re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t　\xa0]+")

# 一致预期只采纳「近期」研报。半年前的预测早已被事件推翻，混进来会把
# 中位数往陈旧的方向拖 —— 上面茅台那组数字正是这个现象。
CONSENSUS_WINDOW = timedelta(days=180)

# 单次取回上限。研报数量极多（煤炭行业近两年 564 篇），全取既无必要
# 也会把提示词撑爆；按发布日倒序取最近的若干篇。
MAX_FETCH = 40


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Referer": _REF,
                      "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return r.read().decode("utf-8", "replace")


def _num(raw) -> Decimal | None:
    s = str(raw or "").strip()
    if not s or s == "0":
        return None
    try:
        v = Decimal(s)
    except Exception:
        return None
    return v if v != 0 else None


@dataclass(frozen=True)
class Report:
    info_code: str
    title: str
    org: str
    published: date
    rating: str = ""
    rating_change: str = ""
    industry: str = ""
    # 预测已折算到绝对财年: {2026: EPS, 2027: EPS, ...}
    eps: dict[int, Decimal] = field(default_factory=dict)
    pe: dict[int, Decimal] = field(default_factory=dict)
    encode_url: str = ""

    @property
    def url(self) -> str:
        if not self.info_code and self.encode_url:
            return _MACRO_PAGE.format(
                enc=urllib.parse.quote(self.encode_url, safe=""))
        return _PAGE.format(code=self.info_code)


_RATING_CHANGE = {1: "调高", 2: "调低", 3: "维持", 4: "首次"}


def _row_to_report(row: dict) -> Report | None:
    pub = str(row.get("publishDate", ""))[:10]
    try:
        published = date.fromisoformat(pub)
    except ValueError:
        return None

    # **相对发布年折算到绝对财年** —— 见模块开头。
    y = published.year
    eps, pe = {}, {}
    for field_, off in (("predictThisYearEps", 0), ("predictNextYearEps", 1),
                        ("predictNextTwoYearEps", 2)):
        v = _num(row.get(field_))
        if v is not None:
            eps[y + off] = v
    for field_, off in (("predictThisYearPe", 0), ("predictNextYearPe", 1),
                        ("predictNextTwoYearPe", 2)):
        v = _num(row.get(field_))
        if v is not None:
            pe[y + off] = v

    return Report(
        info_code=str(row.get("infoCode", "") or ""),
        encode_url=str(row.get("encodeUrl", "") or ""),
        title=str(row.get("title", "")).strip(),
        org=str(row.get("orgSName") or row.get("orgName") or "").strip(),
        published=published,
        rating=str(row.get("emRatingName", "") or "").strip(),
        rating_change=_RATING_CHANGE.get(row.get("ratingChange"), ""),
        # 行业研报给 industryName；个股研报那一栏是空的，行业名在
        # indvInduName（个股所属行业）。两边用的是同一套名称。
        industry=str(row.get("industryName") or row.get("indvInduName")
                     or "").strip(),
        eps=eps, pe=pe)


def search(*, as_of: date, code: str = "", industry_code: str = "",
           months: int = 24, limit: int = MAX_FETCH) -> list[Report]:
    """检索研报。qType=0 个股，qType=1 行业。

    **endTime 传 as_of 之外还要在代码里再滤一遍。** 时间参数是我传的，
    传错了接口不会报错，只会静默地把 as_of 之后的研报交回来。
    """
    begin = (as_of - timedelta(days=int(months * 30.5))).isoformat()
    q = {"industryCode": industry_code or "*", "industry": "*",
         "rating": "*", "ratingChange": "*", "beginTime": begin,
         "endTime": as_of.isoformat(), "pageNo": "1", "fields": "",
         "pageSize": str(limit),
         "qType": "1" if industry_code and not code else "0"}
    if code:
        q["code"] = code
    url = _LIST + "?" + "&".join(f"{k}={v}" for k, v in q.items())
    try:
        payload = json.loads(_get(url))
    except Exception:
        return []
    out = []
    for row in payload.get("data") or []:
        r = _row_to_report(row)
        if r is not None and r.published <= as_of:
            out.append(r)
    return sorted(out, key=lambda r: r.published, reverse=True)


def search_macro(*, as_of: date, months: int = 6,
                 limit: int = MAX_FETCH) -> list[Report]:
    """宏观研究报告。

    补的是宏观分析里此前完全没有的那一半: GDP/CPI/PMI 这些**读数**接口
    早就有了，但「这些读数意味着什么、政策在往哪个方向走」一句都没有。
    读数能定位周期象限，定不出政策取向。
    """
    begin = (as_of - timedelta(days=int(months * 30.5))).isoformat()
    url = (f"{_JG}?pageSize={limit}&beginTime={begin}"
           f"&endTime={as_of.isoformat()}&pageNo=1&fields=&qType=3")
    try:
        payload = json.loads(_get(url))
    except Exception:
        return []
    out = []
    for row in payload.get("data") or []:
        r = _row_to_report(row)
        if r is not None and r.published <= as_of:
            out.append(r)
    return sorted(out, key=lambda r: r.published, reverse=True)


def fetch_text(report: Report) -> str:
    """取研报正文。取不到返回空串 —— 标题本身仍有价值，不因此丢弃整条。"""
    try:
        html = _get(report.url)
    except Exception:
        return ""
    m = _CTX.search(html)
    if not m:
        return ""
    text = _TAG.sub("", m.group(1))
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))
    lines = [_SPACE.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def to_document(report: Report, text: str, retrieved: date) -> Document:
    return Document(
        doc_id=report.info_code or report.encode_url[:16], url=report.url, title=report.title,
        publisher=report.org, tier=Tier.SELLSIDE,
        published=report.published, retrieved=retrieved,
        text=text or report.title)


# ── 一致预期 ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Estimate:
    fiscal_year: int
    n: int
    median: Decimal
    low: Decimal
    high: Decimal

    @property
    def spread(self) -> Decimal:
        """离散度。**分歧本身是信息** —— 券商之间谈不拢的地方，
        正是本报告的情景分析该展开的地方。"""
        return (self.high - self.low) / abs(self.median) if self.median else Decimal(0)


@dataclass
class Consensus:
    as_of: date
    window_days: int
    eps: dict[int, Estimate] = field(default_factory=dict)
    ratings: dict[str, int] = field(default_factory=dict)
    n_reports: int = 0
    # 同一财年在更早时点的预期，用于看下修/上修轨迹
    drift: dict[int, tuple[Estimate, Estimate]] = field(default_factory=dict)

    def has_data(self) -> bool:
        return bool(self.eps)


def _estimate(year: int, vals: list[Decimal]) -> Estimate | None:
    if len(vals) < 2:
        # 单家券商不构成「一致」预期。
        return None
    return Estimate(fiscal_year=year, n=len(vals),
                    median=Decimal(str(statistics.median(vals))),
                    low=min(vals), high=max(vals))


def consensus(reports: list[Report], as_of: date,
              window: timedelta = CONSENSUS_WINDOW) -> Consensus:
    """近窗口内研报的一致预期，并与更早的一批对比看修正方向。"""
    cutoff = as_of - window
    recent = [r for r in reports if r.published >= cutoff]
    older = [r for r in reports if r.published < cutoff]

    out = Consensus(as_of=as_of, window_days=window.days,
                    n_reports=len(recent))
    buckets: dict[int, list[Decimal]] = {}
    for r in recent:
        for y, v in r.eps.items():
            buckets.setdefault(y, []).append(v)
    for y, vals in sorted(buckets.items()):
        est = _estimate(y, vals)
        if est:
            out.eps[y] = est

    for r in recent:
        if r.rating:
            out.ratings[r.rating] = out.ratings.get(r.rating, 0) + 1

    old_buckets: dict[int, list[Decimal]] = {}
    for r in older:
        for y, v in r.eps.items():
            old_buckets.setdefault(y, []).append(v)
    for y, est in out.eps.items():
        prev = _estimate(y, old_buckets.get(y, []))
        if prev:
            out.drift[y] = (prev, est)
    return out


# ── 行业代码 ─────────────────────────────────────────────────

_BK_CACHE: dict[str, str] = {}


def industry_map() -> dict[str, str]:
    """行业名 → 研报接口的 industryCode（497 个）。进程内缓存。

    实测 report/bk 的 bkCode 与列表接口的 industryCode 是同一套:
    白酒Ⅱ→1277、煤炭开采→1250、白色家电→1239、保险Ⅱ→474，双向对得上。
    """
    if _BK_CACHE:
        return _BK_CACHE
    try:
        payload = json.loads(_get(_BK))
    except Exception:
        return {}
    for row in payload.get("data") or []:
        name, code = row.get("bkName"), row.get("bkCode")
        if name and code:
            _BK_CACHE[str(name)] = str(code)
    return _BK_CACHE


def industry_code_of(reports: list[Report]) -> tuple[str, str]:
    """从公司自己的研报里读出它属于哪个行业，返回 (代码, 行业名)。

    **这是东财研报口径的行业，与本项目用于选同业的申万分类是两套东西。**
    它只用来决定「去读哪个行业的研报」，不参与同业集合的构建 —— 两套
    分类混着用来算行业聚合数会是实打实的错误，用来挑阅读材料则无妨。
    """
    names = [r.industry for r in reports if r.industry]
    if not names:
        return "", ""
    name = max(set(names), key=names.count)
    return industry_map().get(name, ""), name


# 一致预期在账本里的键前缀。**这些事实禁止作为估值假设的依据** ——
# 见 valuation.autodecide 的守卫。它们是对照物，不是论据。
CONSENSUS_PREFIX = "consensus."


def consensus_facts(c: Consensus, source_id: str = "eastmoney_report"):
    """一致预期落账本，好让正文能用占位符引用它。

    **不落账本就没法在正文里提。** 提示词里出现裸数字、正文里却禁止引用，
    是自相矛盾的契约 —— 这一点此前踩过八次。

    as_of 取分析时点而非某篇研报的发布日: 窗口内的中位数只有在该时点
    才算得出来，用任何一篇研报的日期都会声称它比实际更早可知。

    方法记为 REPORTED —— 这些数字是券商实际报出来的，不是估算；
    「不该拿它当论据」是**用途**上的限制，不是可靠性上的。
    """
    from ir_agent.ledger import Fact, Method

    out = []

    def put(key, value, unit, period):
        out.append(Fact(key=key, value=Decimal(str(value)), unit=unit,
                        period=period, as_of=c.as_of, source_id=source_id,
                        method=Method.REPORTED))

    for y, e in c.eps.items():
        p = f"{y}FY"
        put("consensus.eps", e.median, "currency", p)
        put("consensus.eps_low", e.low, "currency", p)
        put("consensus.eps_high", e.high, "currency", p)
        put("consensus.brokers", e.n, "count", p)
    for y, (prev, now) in c.drift.items():
        if prev.median:
            put("consensus.eps_revision",
                (now.median - prev.median) / prev.median, "ratio", f"{y}FY")
    return out
