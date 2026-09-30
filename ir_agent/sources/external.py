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
      MEDIA     财经媒体 —— 默认只作线索，不作依据；`openweb.
                verify_finding` 把网址抓回来逐字核实过 `claim` 之后，
                `web.supports_value` 才允许它支撑一个因子的取值——
                变的是"这句话有没有被核实过"，不是"媒体"这个等级本身
                变得可信了
      COMPANY   上市公司法定披露 —— 公告中的事实可直接引为依据；
                管理层对前景的表述不得作为本模型假设的依据

    SELLSIDE 那条线是这套东西里最要紧的一条。卖方研报有立场、有时效、
    本身可能就是错的。让模型拿券商的盈利预测去论证自己的盈利预测，不是
    研究，是洗稿 —— 结论绕一圈回到起点，还平白多了一层「有依据」的外衣。
    """

    OFFICIAL = "official"
    SELLSIDE = "sellside"
    MEDIA = "media"
    COMPANY = "company"


TIER_LABEL = {
    Tier.OFFICIAL: "官方/协会",
    Tier.SELLSIDE: "卖方研究",
    Tier.MEDIA: "财经媒体",
    Tier.COMPANY: "公司披露",
}

# 各级来源在报告中的可引用范围，逐字进提示词 —— 模型需要知道这条界线。
TIER_RULE = {
    Tier.OFFICIAL: "可直接引为事实依据。",
    Tier.SELLSIDE: ("正文中转述的**事实**（行业规模、产量、政策、产业链结构）"
                    "可引用，须注明系经该机构转述的二手信息；"
                    "其**盈利预测、评级、目标价一律不得作为本报告任何假设的依据**，"
                    "只能作为一致预期与本报告结论的对照。"),
    Tier.MEDIA: "仅作线索，不得作为依据。",
    Tier.COMPANY: ("公告中的**事实**（产能、投产进度、订单、股权变动、"
                   "提价）可直接引为依据；其中**管理层对前景、战略意图、"
                   "市场判断的表述不得作为本报告任何假设的依据**——"
                   "披露义务保证的是事实的真实性，不是展望的准确性。"),
}


# **为什么不用领头省略号标注截断，尽管那样看起来更"整洁"。**
# 这条提示词紧接着的规矩是「引用必须逐字照抄下面的资料，一个字都不能
# 改」——而省略号（……）本身就是一种常见的、人在转述/引用时会主动加进去
# 的引用标点。旧写法 `"\n……（正文过长，以上为截取部分；引用请限于以上
# 内容）"` 把这个标点缝在文档正文的末尾，和真实引文长得几乎一样：模型在
# 别处引用同一份资料时，完全可能把这个省略号当成"资料里本来就有的省略
# 写法"一并抄进引文——而这恰恰是逐字核验要抓的那类错误。真实事故里，抓
# 回来的正文本身几乎不含省略号（19080 字符里只出现 1 次），这条标记却在
# 10 篇里 9 篇触发，把 9 处「……」和「以上为截取部分」平白塞进了一个要求
# 逐字不改的证据区；同一次事故里模型确实在年报引文中自己写了省略号，被
# 核验正确打回——标记是不是诱因没法证明，但它是这份提示词里唯一新增的、
# 语义就是"可以省略"的東西，改掉它的成本几乎为零。
#
# 新写法要求做到两件事：其一，读起来不像资料本身的一部分，而是**帯外**
# 的系统批注——用方括号整句包住、以"系统提示"起头，且不以任何标点开头
# （旧写法开头就是"……"，本身就是一个可被模仿的引用符号）；其二，仍然要
# 把"正文被截断"和"引用必须留在这以内"这两件事讲清楚，不能为了不像引文
# 而语焉不详。**以后如果有人觉得这句话啰嗦、想"精简"回省略号，请先重读
# 这段注释——那不是精简，是把已知的诱因加回来。**
_TRUNCATED = ("\n\n[系统提示：以上为该文档因长度限制被截断后的部分内容，"
             "原文更长，但只有以上文字可供引用；请勿引用本提示，"
             "引用范围须限于以上部分]")


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
    # 进提示词时的截断长度。None = 不截断（研报走东财的 ctx-content 块，
    # 长度天然可控）。开放网页可能几万字，且同一批文档会同时压到战略、
    # 产业链、宏观定性三个模块的提示词上 —— 撑爆是三倍的。
    #
    # **限制跟着文档走，不跟着调用点走。** 在调用点上一刀切会把研报也
    # 截了，那是一次没人要求过的行为变更。
    prompt_limit: int | None = None
    # 发布日期的来源：""=不适用（接口给出，如东财研报）/ "meta"=代码从
    # 页面 meta 标签提取 / "model"=模型给出，未经代码核验。
    #
    # 之所以要逐篇记下来而不只统计个数：第二阶段是从落盘文件读回的，
    # 那时已经没有「当时是怎么拿到这个日期的」这个上下文了。能重算的
    # 不该存，而这个恰恰重算不出来。
    date_source: str = ""

    def cite(self) -> str:
        return (f"{self.publisher}《{self.title}》"
                f"（{self.published}，{TIER_LABEL[self.tier]}）")

    def head(self, n: int = 600) -> str:
        t = self.text.strip()
        return t[:n] + ("……" if len(t) > n else "")

    def for_prompt(self) -> str:
        """进提示词的正文。**入库存全文，这里才截断。**

        Corpus.verify() 用的是 self.text（全文），模型只能引用它在提示词
        里看得见的那一段，而那一段必然是全文的前缀 —— 所以截断不会让引文
        核验误杀。截断标记要写出来，否则模型不知道后面还有内容，可能凭
        印象去补。
        """
        if self.prompt_limit is None or len(self.text) <= self.prompt_limit:
            return self.text
        return self.text[:self.prompt_limit] + _TRUNCATED


class OutOfWindow(LookAheadError):
    """文档发布于 as_of 之后 —— 拿未来的资料分析过去。"""


@dataclass
class Corpus:
    """代码抓回来的外部文档。**模型只能引用这里面的东西。**"""

    as_of: date
    docs: list[Document] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # 核验通过、但引文出自 Tier.MEDIA 文档的记录。**警告，不拒绝。**
    #
    # `TIER_RULE[Tier.MEDIA]` 写死「仅作线索，不得作为依据」，但这条
    # 规矩此前只是提示词里的一句话——`verify()` 对任何分级的文档一视
    # 同仁，只要引文逐字出现就放行。两轮评审都指出这个洞：模型完全
    # 可以拿一篇财经媒体的转述去支撑一条本该有官方/卖方来源的判断，
    # 代码毫无察觉。
    #
    # 用户的决定是警告、不是硬拒绝——产业链分析已经因为一次引文核验
    # 失败整节夭折过三次，「引文逐字核验不过」和「引文核验过了但来源
    # 分级不够格」是两种严重程度截然不同的问题，不能用同一种「拒绝」
    # 处置。但也不能因为不拒绝就让它隐身：读者必须能看到"这条判断的
    # 依据只是线索级来源"。
    #
    # **计数按 (文档, 规范化引文) 去重，不按 verify() 被调用的次数。**
    # `validate()` 从 strategy.assess/value_chain.analyse/macro_view.assess
    # 的重试循环里调用（`max_attempts=3`），同一条 MEDIA 引文可能在被
    # 打回重试的草稿里、以及最终定稿里各被 verify() 命中一次——不去重，
    # 这个数字数的是"重试了几次"而不是"最终依赖了几条 MEDIA 依据"，
    # 会随重试次数虚高，是产物清单里一条会说谎的肯定性声明（比不提
    # 更糟）。去重键选 (doc_id, 引文) 而不是 (doc_id,)：同一篇文档内
    # 两条不同的真实引文必须分别计数，压成按文档去重会漏报真实的引用
    # 条数。见 verify() 里的实现与更详细的说明。
    #
    # 记在 corpus 对象上、不通过 verify() 的返回值传出去——`verify()`
    # 从 `basis.validate()` 调用，而 `validate()` 又从 strategy.py/
    # value_chain.py/macro_view.py/assume.py 好几个模块调用，返回值的
    # 语义早就固定成"问题描述，空串=通过"；硬塞一个"通过但要注意"的
    # 中间态会让每一个调用方都要多分支处理。跟 `skipped` 同一个形状：
    # 调用方（这里是 full.py）在整轮跑完之后统一读一次、汇总成一条
    # 产物清单条目，而不是每次 verify() 都要单独处理。
    media_cited: list[str] = field(default_factory=list)
    # `media_cited` 的去重键：(doc_id, 规范化后的引文)。**不暴露给
    # dataclass 的公开字段名单之外的调用方** —— 纯粹是 verify() 自己
    # 记账用，见 verify() 里的说明。
    _media_cited_seen: set[tuple[str, str]] = field(default_factory=set,
                                                     repr=False)

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

        **引文出自 Tier.MEDIA 文档时仍然通过，但要记下来。**
        `TIER_RULE[Tier.MEDIA]` 写死「仅作线索，不得作为依据」——这是
        一条关于**分级**的规矩，不是关于「引文是否逐字出现」的规矩，
        两者是可以分别成立的两件事：一条引文完全可以逐字出自某篇文档
        （核验该过），但那篇文档的分级决定了它本不该被当作依据。用户
        决定这里只警告、不拒绝（见 `Corpus.media_cited` 字段注释），
        所以仍然返回空串，只是顺带记一笔，交给 full.py 在产物清单里
        统一汇总。
        """
        d = self.find(quote)
        if d is None:
            return (f"外部引文「{quote[:30]}」在已抓取的资料中找不到 —— "
                    "外部依据只能引用本次实际取回的文档，不得凭印象或记忆撰写。")
        if url and norm(url) != norm(d.url):
            return (f"外部引文「{quote[:20]}」实际出自 {d.cite()}，"
                    f"但依据标注的 URL 是 {url} —— 归因错误。")
        if d.tier is Tier.MEDIA:
            # **去重，按 (文档, 规范化引文) 计数——"处" 数的是不同的引用，
            # 不是不同的文档，也不是 verify() 被调用的次数。**
            #
            # `validate()` 从 strategy.assess/value_chain.analyse/
            # macro_view.assess 的重试循环里调用（`max_attempts=3`，每次
            # `check()` 都会重跑 `validate()`）：模型第一次交的草稿没
            # 通过某个不相关的核验、被整体打回重试，第二次交的草稿即便
            # 引用了同一条 MEDIA 引文，也会让 verify() 再命中一次
            # ——不去重，`media_cited` 数的其实是"这条引文在多少次尝试
            # 里出现过"，而不是"最终定稿实际依赖了多少条 MEDIA 依据"，
            # 重试次数越多，报给读者的数字越夸大，且与 `docs`/实际引文
            # 无关地上涨。
            #
            # 键选 (doc_id, 规范化引文) 而不是 (doc_id,)：同一篇文档里
            # 两个不同的真实引文必须分别计数——"1 篇文档支撑 2 条不同
            # 判断"和"1 篇文档支撑 1 条判断"是不同的风险敞口，压成
            # 按文档去重会把前者也报成 1，反而漏报。同一条引文只在
            # *不同* 依据对象上被重复核验（例如两处依据抄了同一句话）
            # 也只算一次——它们指向同一个可核验的事实，不是两条独立
            # 依据。
            key = (d.doc_id, norm(quote))
            if key not in self._media_cited_seen:
                self._media_cited_seen.add(key)
                self.media_cited.append(d.cite())
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
    from ir_agent.basis import Basis, BasisKind

    body = norm(annual)
    for item in items:
        for i, b in enumerate(item.bases):
            if b.kind is not BasisKind.REPORT or norm(b.quote) in body:
                continue
            d = corpus.find(b.quote)
            if d is not None:
                item.bases[i] = Basis.external(d.url, b.quote, d.retrieved)
