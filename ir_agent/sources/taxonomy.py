"""全市场行业分类索引 —— 一次取回，本地缓存，支持层级回退。

为什么要整张表: 接口**不支持 like 查询**（实测返回「不支持like查询」），
只能按完整分类路径精确匹配。而申万三级分类经常细到没有统计意义 ——
实测比亚迪落在「汽车-乘用车-电动乘用车」，同业只有北汽蓝谷、赛力斯两家。
拿 2 个样本算中位数和分位数，数字会出现，但没有任何含义。

有了整张表就能向上回退到二级（乘用车）、一级（汽车），直到同业数够。
用了哪一级必须写进结论 —— 「汽车行业中位数」和「电动乘用车中位数」
是两个不同的论断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import requests

from ir_agent.market import UnsupportedMarket, market_of
from ir_agent.sources.retry import with_retry

API = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 30
PAGE = 500
MAX_PAGES = 80                    # 实测 50 页；留余量，超出即视为异常

# 分类表变动很慢（行业调整以年计），一周足够新。
CACHE_TTL = timedelta(days=7)
DEFAULT_CACHE = Path.home() / ".cache" / "ir-agent" / "taxonomy.json"

# 同业数下限。低于这个数就向上回退一级 —— 分位数需要样本量才有意义。
MIN_PEERS = 5


class TaxonomyUnavailable(Exception):
    """分类表取不到。"""


@dataclass(frozen=True)
class Entry:
    code: str
    name: str
    path: str

    @property
    def levels(self) -> list[str]:
        return [p for p in self.path.split("-") if p]


def _fetch_page(page: int) -> tuple[list[dict], int]:
    def call():
        r = requests.get(API, params={
            "reportName": "RPT_F10_BASIC_ORGINFO",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME_LEVEL",
            "pageSize": PAGE, "pageNumber": page,
            "source": "HSF10", "client": "PC",
        }, headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    try:
        payload = with_retry(call)
    except Exception as e:                                   # noqa: BLE001
        raise TaxonomyUnavailable(f"第 {page} 页: {e.__class__.__name__}: {e}") from e
    result = payload.get("result")
    if not result:
        raise TaxonomyUnavailable(f"第 {page} 页返回空: {payload.get('message')}")
    return result.get("data") or [], int(result.get("pages") or 0)


def fetch_all() -> list[Entry]:
    """整张分类表。只保留本项目支持的 A 股代码段。"""
    out: list[Entry] = []
    seen: set[str] = set()
    page, total = 1, 1
    while page <= total and page <= MAX_PAGES:
        rows, total = _fetch_page(page)
        if not rows:
            break
        for row in rows:
            code = str(row.get("SECURITY_CODE") or "").strip()
            path = row.get("BOARD_NAME_LEVEL")
            if not code or not path or code in seen:
                continue
            try:
                market_of(code)              # B 股 / 北交所 / 非 A 股在此落选
            except UnsupportedMarket:
                continue
            seen.add(code)
            out.append(Entry(code, str(row.get("SECURITY_NAME_ABBR") or code),
                             str(path)))
        page += 1
    if not out:
        raise TaxonomyUnavailable("分类表为空")
    return out


@dataclass
class Taxonomy:
    entries: list[Entry]
    fetched: date

    # ── 缓存 ──────────────────────────────────────────────

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "fetched": self.fetched.isoformat(),
            "entries": [[e.code, e.name, e.path] for e in self.entries],
        }, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> "Taxonomy | None":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            fetched = date.fromisoformat(raw["fetched"])
            entries = [Entry(*row) for row in raw["entries"]]
        except (OSError, ValueError, KeyError, TypeError):
            return None                       # 缓存坏了就当没有，不阻断
        return cls(entries, fetched) if entries else None

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CACHE,
             today: date | None = None, refresh: bool = False) -> "Taxonomy":
        path = Path(path)
        today = today or date.today()
        if not refresh:
            cached = cls.read(path)
            if cached and today - cached.fetched <= CACHE_TTL:
                return cached
        tax = cls(fetch_all(), today)
        try:
            tax.dump(path)
        except OSError:
            pass                              # 缓存写不进去不影响本次使用
        return tax

    # ── 查询 ──────────────────────────────────────────────

    def entry(self, code: str) -> Entry | None:
        return next((e for e in self.entries if e.code == code), None)

    def at_level(self, path: str, level: int) -> list[Entry]:
        """同一分类前 level 级下的全部公司。level=3 即叶子级。"""
        prefix = [p for p in path.split("-") if p][:level]
        if not prefix:
            return []
        return [e for e in self.entries if e.levels[:level] == prefix]

    def widen(self, code: str, min_peers: int = MIN_PEERS
              ) -> tuple[list[Entry], int, str, bool]:
        """从叶子级向上回退，直到同业数达标。**最多退到二级。**

        返回 (成分, 采用的级数, 该级分类名, 是否样本偏薄)。

        为什么卡在二级: 一级是「非银金融」「汽车」这种大类，把业务实质完全
        不同的公司拌在一起。实测中国平安 —— 保险Ⅲ 只有 4 家同业，退到一级
        变成 79 家，里面全是券商和多元金融。拿保险公司和券商比 PB/ROE 中位数，
        数字算得出来，但论断是假的。**4 家真同业好过 79 家假同业。**

        所以样本不足时不再稀释，而是照实返回并置 thin —— 下游据此不出分位数。
        """
        me = self.entry(code)
        if me is None:
            raise TaxonomyUnavailable(f"{code}: 分类表中查无此代码")
        floor = min(2, len(me.levels))
        for level in range(len(me.levels), floor - 1, -1):
            others = [e for e in self.at_level(me.path, level) if e.code != code]
            if len(others) >= min_peers or level == floor:
                return (others, level, "-".join(me.levels[:level]),
                        len(others) < min_peers)
        return [], floor, "-".join(me.levels[:floor]), True
