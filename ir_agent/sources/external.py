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

# 引号类字符一律剥掉、破折号类字形折叠成一个代表字符。
#
# 真实事故（600183 第四轮实跑）: 模型给宏观定性的依据引了「"十五五」，
# 年报正文里是「“十五五”」——同一句话，只是引号从弯的变成了直的。逐字
# 核验判不过，三次重试都是同一条驳回，宏观定性整段未生成，级联到研究
# 简报与 DCF 判断项一并缺失。PDF 抽文、模型输出、网页复制三条路径各有
# 各的标点习惯，把字形差异当成「凭空捏造」是误伤。
#
# 引号为什么是**剥掉**而不是折叠成同一个字符: 还有一种同样常见的写法
# 是正文里本来没有引号，模型引用时自己套了一对（提示词自己就用「」来
# 界定引文）。折叠只能让弯的等于直的，剥掉才能让「有」等于「没有」。
#
# 破折号为什么是**折叠**而不是剥掉: 破折号在中文里承载连接含义
# （「2026—2030 年」），剥掉会让两个相邻的数字黏成一个新数字。
#
# 放宽到此为止 —— 放宽多少，就等于给捏造留出多大的缝。折叠标点不会让
# 两段不同的文字变成同一段；一旦开始折叠文字本身（繁简、同义词、数字
# 写法），「引文逐字出自原文」这个承诺就什么都不剩了。tests/test_basis.py
# 里两条反面测试钉住这条界线。
_QUOTE_DROP = dict.fromkeys(map(ord, "\"'“”„‟‘’‚‛′″〝〞＂＇`｀「」『』"), None)
_DASH_FOLD = {ord(c): "-" for c in "—–‒―－−"}
_PUNCT = {**_QUOTE_DROP, **_DASH_FOLD}


def norm(s: str) -> str:
    """引文核验的规范化 —— 去空白、剥引号、折叠破折号字形。

    **这是全流程唯一一份实现。** basis.py（正文引文）、assume.py（判断项
    依据）、openweb.py（模型转述）、attribution.py（年报出处）都从这里
    导入: 同一条「逐字核验」规则此前在三个文件里各写了一份，每份 docstring
    都声称「与另两处同一套规范化」，而保证它们一致的只有那三句注释 ——
    第四轮事故后若只修一处，另外几条核验路径会继续带着同一个缺陷。
    """
    return _WS.sub("", s).translate(_PUNCT)


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
    # 与上面 Tier.MEDIA 枚举成员文档字符串（本文件 :52）不矛盾，管的是
    # 两条不同的路径：这一条是 `Corpus.verify()`/`basis.validate()` 那
    # 条路径的提示词文案——正文引用 Document 时的分级规矩（`full.
    # _media_tier_note` 原样引用它）；枚举文档字符串说的是另一条路径
    # ——`gaps.collect` ④分支里 `web.supports_value` 判断 Finding 能不能
    # 支撑一个 DCF 因子取值的门槛，那条门槛不读这个字典。两句话各管各
    # 的路径，不是一句过时了要拿另一句去"统一"。
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
    # 公司公告的档位（`announce.type_tier`）：1 = 对估值因子有实质信息，
    # 2 = 其余。**只用于排序**（`Corpus.prompt_docs` 先给第一档），不影响
    # 引用与核验。非公告文档一律 2。
    rank: int = 2

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

    def prompt_docs(self, company_limit: int | None = None) -> list[Document]:
        """进提示词的文档。**只截断 COMPANY 级，其余分级照旧全给。**

        入库的公告不再限 8 篇之后，三个定性模块若仍
        `for d in corpus.docs` 无差别全喂，提示词会是原先的 2.5 倍、而且是
        三个模块各一份。

        **截断放在这里而不是入库处。** 入库的文档供 `gaps._match_announce`
        按因子做全文检索，池子越大召回越高 —— 那是放宽条数的全部收益。
        此前 `MAX_ANNOUNCEMENTS = 8` 这个数的注释自己写着上限的理由是
        「同一批文档会同时压到战略/产业链/宏观定性三个模块的提示词上」：
        **消费方的约束被实现成了生产方的上限。**

        只动 COMPANY 级，因为只有它放宽了（研报走 `corpus.gather` 的 24 个
        月窗口、开放网络本来就最多 10 篇，两者条数没变）。

        **保持 `self.docs` 的原顺序。** 下游三个模块按顺序渲染，而
        `_external_md` 统计的篇数读的是 `self.docs` —— 这里重排会让提示词
        里的次序和清单上的次序对不上。
        """
        from ir_agent.sources.announce import PROMPT_DOCS   # noqa: PLC0415

        if company_limit is None:
            company_limit = PROMPT_DOCS
        # **先按档、再按新旧。** 上一版只按新旧取最近 8 篇，生益科技实跑时
        # 定性三节读到的是股东会决议与律师意见书，而投资建设覆铜板项目、
        # 年度业绩快报排在后面被截掉。档位来自交易所分类代码，见
        # `announce.type_tier`。
        company = sorted((d for d in self.docs if d.tier is Tier.COMPANY),
                         key=lambda d: (d.rank, -d.published.toordinal())
                         )[:company_limit]
        keep = {id(d) for d in company}
        return [d for d in self.docs
                if d.tier is not Tier.COMPANY or id(d) in keep]

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
