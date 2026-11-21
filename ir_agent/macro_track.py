"""宏观跟踪指标定向检索（spec 2026-11-21-staged-collection §4）。

宏观定性写出 `跟踪:` 行（领先指标 | 对应传导 | 证伪读数），审核通过后按指标逐个上网取最新读数。
复用因子级检索 `conclusions.search`（上网 → 打开文章 → 标出数字出处），指标名当因子名；代码打开过
的文章以 `module="macro_track"` 入库，第二次调用起辩论、预研、撰写都能引用。

**不由代码判定是否证伪。** 读数与证伪条件并列写进宏观产物，交门①的人和后续撰写去判 —— 证伪条件是
一句话（「成交回升而竣工持续下滑」），把它机械地比对成真假，等于让代码替人下结论。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ir_agent.macro_view import MACRO_TRACK_MAX

TRACK_JSON = "macro_track.json"


@dataclass
class TrackItem:
    indicator: str
    driver: str
    falsifier: str
    conclusion: str = ""                 # 检索结论原文（多条用「；」连）；空 = 未取到
    sources: list[str] = field(default_factory=list)   # 代码打开过的文章网址


@dataclass
class TrackResult:
    items: list[TrackItem] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)   # 超过上限没检索的指标
    failed: list[str] = field(default_factory=list)      # 检索调用失败的指标（含原因）
    removed: list[str] = field(default_factory=list)     # 人工删掉、不再展示的指标
    docs: list = field(default_factory=list)             # 入库的文章（落盘读回用）


def _slug(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


def track(client, view, *, industry: str, as_of: date, corpus, us: bool,
          done: TrackResult | None = None) -> TrackResult:
    """按 `view.tracks` 逐个检索（最多 `MACRO_TRACK_MAX` 个）。`done` 是上一轮的结果：已检索过的指标沿用，
    只补搜新增的，人工删掉的写进 `removed`。单个指标失败只记进 `failed`，不抛。

    **调用方必须传入已开启 WebSearch/WebFetch 的 client**（同 `conclusions.search`）。"""
    from ir_agent.sources import conclusions
    from ir_agent.sources.external import Document
    from ir_agent.sources.openweb import TEXT_LIMIT

    tracks = list(view.tracks)[:MACRO_TRACK_MAX]
    res = TrackResult(truncated=[t.indicator for t in list(view.tracks)[MACRO_TRACK_MAX:]])
    # 上一轮检索失败（没有结论也没有出处）的指标当作新的，重跑时再搜一次 —— 一次超时不能永久冻结
    prev = {i.indicator: i for i in (done.items if done else []) if i.conclusion or i.sources}
    names = {t.indicator for t in tracks}
    res.removed = [i.indicator for i in (done.items if done else []) if i.indicator not in names]
    keep_urls = {u for k, i in prev.items() if k in names for u in i.sources}
    res.docs = [d for d in (done.docs if done else []) if d.url in keep_urls]
    have = {d.url for d in corpus.docs} if corpus is not None else set()
    for d in res.docs:                       # 沿用的文章重新入库（读回时没入库）
        if corpus is not None and d.url not in have and corpus.add_if_visible(d):
            have.add(d.url)
    for t in tracks:
        if t.indicator in prev:
            old = prev[t.indicator]
            res.items.append(TrackItem(t.indicator, t.driver, t.falsifier, old.conclusion, list(old.sources)))
            continue
        item = TrackItem(t.indicator, t.driver, t.falsifier)
        res.items.append(item)
        try:
            got = conclusions.search(client, t.indicator, f"macro_track.{_slug(t.indicator)}",
                                     industry, as_of)          # 本仓只做 A 股（us-ir-agent 传 us=）
        except Exception as e:                              # noqa: BLE001
            res.failed.append(f"{t.indicator}（{e.__class__.__name__}: {str(e)[:80]}）")
            continue
        item.conclusion = "；".join(c.claim for c in got.items)
        for c in got.items:
            for a in c.articles:
                if not a.opened or a.link in item.sources:
                    continue
                item.sources.append(a.link)
                if a.link in have:              # 两个指标打开同一篇、或与已入库网页同址：只入库一次
                    continue
                have.add(a.link)
                doc = Document(doc_id="mt_" + _slug(a.link), url=a.link,
                               title=f"{t.indicator}（{a.publisher}）", publisher=a.publisher,
                               tier=a.tier, published=a.published, retrieved=as_of, text=a.text,
                               prompt_limit=TEXT_LIMIT, date_source="model", module="macro_track")
                if corpus is not None and corpus.add_if_visible(doc):
                    res.docs.append(doc)
    return res


def summary(res: TrackResult) -> str:
    """追加到宏观定性产物（进门①、state.json 与研报上下文）的一节。"""
    lines = ["  跟踪指标最新读数（证伪与否交人判断）"]
    for i in res.items:
        lines.append(f"    · {i.indicator}（对应「{i.driver}」；证伪条件：{i.falsifier}）："
                     + (i.conclusion or "未取到"))
        for u in i.sources:
            lines.append(f"      出处：{u}")
    for x in res.failed:
        lines.append(f"    ⚠ 检索失败：{x}")
    if res.truncated:
        lines.append(f"    ⚠ 超过上限未检索：{'、'.join(res.truncated)}")
    if res.removed:
        lines.append(f"    · 人工删去、不再展示：{'、'.join(res.removed)}")
    return "\n".join(lines)


def writer_summary(res: TrackResult) -> str:
    """进撰写提示词的版本：只列指标、对应传导与出处篇数，**不带网页结论原文与网址** —— 原文里的数字
    不在账本里，会触发裸数字审计；撰写要用读数时引用已入库的原文。"""
    lines = ["  跟踪指标（读数原文见已入库资料，引用请照原文）"]
    for i in res.items:
        lines.append(f"    · {i.indicator}（对应「{i.driver}」）："
                     + (f"最新读数已入库，见标题为「{i.indicator}（…）」的资料" if i.sources else "未取到"))
    return "\n".join(lines)


def save(res: TrackResult, path: Path | str) -> None:
    from ir_agent.sources.openweb import _doc_to_json

    Path(path).write_text(json.dumps({
        "items": [vars(i) for i in res.items], "truncated": res.truncated, "failed": res.failed,
        "removed": res.removed, "docs": [_doc_to_json(d) for d in res.docs],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path: Path | str, corpus) -> TrackResult | None:
    """读回并把文章重新入库（走时点守卫）。文件不存在或坏了返回 None。"""
    from ir_agent.sources.openweb import _doc_from_json

    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        res = TrackResult(items=[TrackItem(**i) for i in raw.get("items", [])],
                          truncated=list(raw.get("truncated", [])), failed=list(raw.get("failed", [])),
                          removed=list(raw.get("removed", [])))
    except Exception:                                       # noqa: BLE001
        return None
    seen = {d.url for d in corpus.docs} if corpus is not None else set()
    for d in raw.get("docs", []):
        try:
            doc = _doc_from_json(d)
        except Exception:                                   # noqa: BLE001
            continue
        res.docs.append(doc)
        if corpus is not None and doc.url not in seen and corpus.add_if_visible(doc):
            seen.add(doc.url)
    return res
