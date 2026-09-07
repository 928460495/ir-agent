"""外部资料语料库 —— 让年报之外的文字也能被引用，且引用可核验。

**为什么需要它。** 在此之前，三个模块的资料来源是这样的:

    宏观    东财 datacenter 的 GDP/CPI/PPI/PMI/M1M2 + 国债收益率   纯数字
    行业    申万分类 + A 股上市公司报表聚合                        纯数字
    战略    公司自己的年报                                        公司自述

问题不在于数据不准，在于**没有一句话来自公司和交易所之外**。行业规模只有
「上市公司营收之和」（真实行业还有大量非上市企业），产业链结构完全没有，
战略判断只听公司自己讲 —— 相当于让被评价方写自己的评价。

**为什么这件事危险，以及这个模块在防什么。** `Basis.EXTERNAL` 早就存在，
但注释写着「无法在本地核验」。此前它只用于 WACC 的无风险利率，URL 由
rates.py 生成，模型碰不到，所以安全。一旦放开外部资料，EXTERNAL 就会变成
整套依据体系的漏斗: 模型可以编一个 URL、编一段引文，校验一律放行。

因此本模块的核心不是「抓取」，是**语料库**: 只有**代码抓回来的**文档才进
语料库，模型的 EXTERNAL 引文必须逐字出现在语料库里 —— 与 REPORT 引文必须
逐字出现在年报摘录里，是同一条规矩。

**时点守卫延伸到发布日。** 一篇 2026 年 3 月的研报不能用于 as_of 为
2025 年 12 月的分析。这是 LookAheadError 的自然延伸，且必须在代码里拦 ——
接口的时间参数是我传的，传错了不会报错，只会静默地把未来讲给过去听。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from ir_agent.ledger import LookAheadError

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    """与 valuation.basis 同一套规范化 —— 引文核验必须两边一致。"""
    return _WS.sub("", s)


class Tier(str, Enum):
    """来源分级。**决定的不是可信度高低，是可引用的范围。**

    分级不是给来源打分，是区分「这段文字能拿来做什么」:

      OFFICIAL  统计局、行业协会、部委、交易所 —— 事实与政策，可直接引为依据
      SELLSIDE  券商研究所 —— 正文里转述的事实可引（注明二手）；
                但其**预测、评级、目标价不得作为本模型假设的依据**
      MEDIA     财经媒体 —— 只作线索，不作依据

    SELLSIDE 那条线是这套东西里最要紧的一条。卖方研报有立场、有时效、
    本身可能就是错的。让模型拿券商的盈利预测去论证自己的盈利预测，不是
    研究，是洗稿 —— 结论绕一圈回到起点，还平白多了一层「有依据」的外衣。
    """

    OFFICIAL = "official"
    SELLSIDE = "sellside"
    MEDIA = "media"


TIER_LABEL = {
    Tier.OFFICIAL: "官方/协会",
    Tier.SELLSIDE: "卖方研究",
    Tier.MEDIA: "财经媒体",
}

# 各级来源在报告中的可引用范围，逐字进提示词 —— 模型需要知道这条界线。
TIER_RULE = {
    Tier.OFFICIAL: "可直接引为事实依据。",
    Tier.SELLSIDE: ("正文中转述的**事实**（行业规模、产量、政策、产业链结构）"
                    "可引用，须注明系经该机构转述的二手信息；"
                    "其**盈利预测、评级、目标价一律不得作为本报告任何假设的依据**，"
                    "只能作为一致预期与本报告结论的对照。"),
    Tier.MEDIA: "仅作线索，不得作为依据。",
}


@dataclass(frozen=True)
class Document:
    doc_id: str
    url: str
    title: str
    publisher: str
    tier: Tier
    published: date
    retrieved: date
    text: str

    def cite(self) -> str:
        return (f"{self.publisher}《{self.title}》"
                f"（{self.published}，{TIER_LABEL[self.tier]}）")

    def head(self, n: int = 600) -> str:
        t = self.text.strip()
        return t[:n] + ("……" if len(t) > n else "")


class OutOfWindow(LookAheadError):
    """文档发布于 as_of 之后 —— 拿未来的资料分析过去。"""


@dataclass
class Corpus:
    """代码抓回来的外部文档。**模型只能引用这里面的东西。**"""

    as_of: date
    docs: list[Document] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def add(self, doc: Document) -> None:
        if doc.published > self.as_of:
            raise OutOfWindow(
                f"《{doc.title}》发布于 {doc.published}，晚于 as_of {self.as_of}。")
        self.docs.append(doc)

    def add_if_visible(self, doc: Document) -> bool:
        """批量抓取时用: 越界的记下来跳过，不中断整批。"""
        try:
            self.add(doc)
            return True
        except OutOfWindow:
            self.skipped.append(f"{doc.title}（{doc.published}）")
            return False

    def by_tier(self, tier: Tier) -> list[Document]:
        return [d for d in self.docs if d.tier is tier]

    def find(self, quote: str) -> Document | None:
        """引文出自哪篇。找不到 = 该引文不在任何抓回来的文档里。"""
        q = norm(quote)
        if not q:
            return None
        for d in self.docs:
            if q in norm(d.text) or q in norm(d.title):
                return d
        return None

    def verify(self, quote: str, url: str = "") -> str:
        """核验一条外部引文，返回问题描述；空串 = 通过。

        URL 也要对上: 引文确实存在、却挂在另一篇文章名下，读者顺着 URL
        去查会查不到，等同于错误归因。
        """
        d = self.find(quote)
        if d is None:
            return (f"外部引文「{quote[:30]}」在已抓取的资料中找不到 —— "
                    "外部依据只能引用本次实际取回的文档，不得凭印象或记忆撰写。")
        if url and norm(url) != norm(d.url):
            return (f"外部引文「{quote[:20]}」实际出自 {d.cite()}，"
                    f"但依据标注的 URL 是 {url} —— 归因错误。")
        return ""

    def summary(self) -> str:
        if not self.docs:
            return "外部资料: 无（本次未取到任何公司年报之外的资料）"
        lines = [f"外部资料 {len(self.docs)} 篇（截至 {self.as_of}）"]
        for t in Tier:
            got = self.by_tier(t)
            if got:
                lines.append(f"  [{TIER_LABEL[t]}] {len(got)} 篇")
                for d in got[:8]:
                    lines.append(f"    · {d.cite()}")
        if self.skipped:
            lines.append(f"  – 因发布日晚于 {self.as_of} 而排除 "
                         f"{len(self.skipped)} 篇")
        return "\n".join(lines)


def attribute(items, annual: str, corpus: "Corpus | None") -> None:
    """引文的出处由**代码**判定，不由模型声明。就地改写 items 的 bases。

    模型只写「引文」。代码先在年报摘录里找，找不到再去外部语料库找，
    找到了就改标为 EXTERNAL 并补上真实 URL。模型因此没有机会张冠李戴 ——
    与「模型只写占位符、代码填数字」是同一条思路。

    反过来也要成立: 年报里找得到的引文**不得**被改标为外部。出处错了，
    读者按图索骥会查不到，比引文造假更难发现。
    """
    if corpus is None:
        return
    from ir_agent.valuation.basis import Basis, BasisKind

    body = norm(annual)
    for item in items:
        for i, b in enumerate(item.bases):
            if b.kind is not BasisKind.REPORT or norm(b.quote) in body:
                continue
            d = corpus.find(b.quote)
            if d is not None:
                item.bases[i] = Basis.external(d.url, b.quote, d.retrieved)
