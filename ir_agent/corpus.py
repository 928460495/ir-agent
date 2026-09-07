"""把外部资料装配成语料库 —— 各适配器的汇流处。

**取多少、取哪些，本身是判断。** 煤炭行业近两年 564 篇研报，全取既无必要
也会把提示词撑爆。两条筛选:

1. **按篇型排序。** 周报、日报讲的是本周煤价涨了多少，对「产业链结构是
   什么」没有帮助；深度、专题、年度策略才有结构性论述。这不是给机构打
   分，是分辨文章体裁 —— 因此**只排序不剔除**，深度报告不足时仍会用到
   点评类，只是排在后面。

2. **正文按需取。** 列表接口一次拿回全部元数据（标题、机构、评级、盈利
   预测），正文要逐篇请求。因此一致预期用全部元数据算，正文只取排序靠前
   的若干篇。

**时点守卫贯穿始终。** 检索时 endTime 传 as_of，入库时 Corpus 再滤一遍
发布日 —— 时间参数是我传的，传错了接口不报错，只会静默地把未来讲给过去。
"""

from __future__ import annotations

import re
from datetime import date

from ir_agent.sources import reports as R
from ir_agent.sources.external import Corpus

# 篇型权重。数值只用于排序，不构成任何分析结论。
_DEEP = re.compile(r"深度|专题|框架|年度策略|投资策略|行业报告|全景|梳理|研究")
_THIN = re.compile(r"周报|日报|晨报|月报|快报|数据点评|跟踪")
# 宏观报告里，境外主题对 A 股单个标的的分析帮助有限，排在后面。
_OFFSHORE = re.compile(r"美国|美联储|海外|欧洲|日本|全球")


def _rank(r: R.Report) -> tuple[int, date]:
    score = 0
    if _DEEP.search(r.title):
        score += 2
    if _THIN.search(r.title):
        score -= 2
    return (score, r.published)


def _rank_macro(r: R.Report) -> tuple[int, date]:
    score, _ = _rank(r)
    if _OFFSHORE.search(r.title):
        score -= 1
    return (score, r.published)


def gather(*, as_of: date, code: str = "", industry_code: str = "",
           company_texts: int = 3, industry_texts: int = 4,
           macro_texts: int = 2,
           months: int = 24) -> tuple[Corpus, R.Consensus | None]:
    """取回公司与行业研报，装配语料库并算出一致预期。

    返回 (语料库, 一致预期)。一致预期可能为 None —— 覆盖研报的公司只是
    少数，多数 A 股公司没有券商跟踪，那时如实为空而非编造。
    """
    corpus = Corpus(as_of=as_of)
    consensus = None

    if code:
        rs = R.search(as_of=as_of, code=code, months=months, limit=200)
        if rs:
            consensus = R.consensus(rs, as_of)
            for r in sorted(rs, key=_rank, reverse=True)[:company_texts]:
                _ingest(corpus, r, as_of)
            if not industry_code:
                # 行业代码从公司自己的研报里读出来，不必另建映射表。
                industry_code, _ = R.industry_code_of(rs)

    if industry_code:
        rs = R.search(as_of=as_of, industry_code=industry_code,
                      months=months, limit=200)
        for r in sorted(rs, key=_rank, reverse=True)[:industry_texts]:
            _ingest(corpus, r, as_of)

    if macro_texts:
        # 宏观报告时效性强得多 —— 半年前对政策取向的判断多半已经作废，
        # 因此窗口比行业与个股短。
        rs = R.search_macro(as_of=as_of, months=6, limit=100)
        for r in sorted(rs, key=_rank_macro, reverse=True)[:macro_texts]:
            _ingest(corpus, r, as_of)

    return corpus, consensus


def _ingest(corpus: Corpus, r: R.Report, as_of: date) -> None:
    text = R.fetch_text(r)
    corpus.add_if_visible(R.to_document(r, text, as_of))


def consensus_prompt(c: R.Consensus | None) -> str:
    """给模型看的一致预期: **只有占位符，没有读数**。

    与宏观、行业、可比三处同一条约定 —— 提示词里出现裸数字、正文里却
    禁止引用，是自相矛盾的契约。
    """
    if c is None or not c.has_data():
        return ("一致预期：本次未取到足够的券商盈利预测"
                "（多数 A 股公司无券商跟踪）—— 无从对照")
    lines = ["一致预期（窗口内若干篇研报，覆盖若干家券商）",
             "  ⚠ 卖方预测仅作本报告结论的**对照**，"
             "不得作为本报告任何假设的依据。"]
    for y in sorted(c.eps):
        lines.append(
            f"  FY{y}　EPS 中位 [[consensus.eps@{y}FY]]"
            f"（[[consensus.brokers@{y}FY]] 家，区间 "
            f"[[consensus.eps_low@{y}FY]]~[[consensus.eps_high@{y}FY]]）")
    for y in sorted(c.drift):
        lines.append(f"  FY{y}　较早前的一致预期已修正 "
                     f"[[consensus.eps_revision@{y}FY]]"
                     f" —— 修正方向本身是信息，值得在正文中解释")
    if c.ratings:
        lines.append("  评级分布见看板（评级是他人观点，不入账本）")
    return "\n".join(lines)


def consensus_md(c: R.Consensus | None) -> str:
    """一致预期的人读版。**这是对照物，不是依据** —— 措辞上必须说死，
    否则下游会拿它当论据。"""
    if c is None or not c.has_data():
        return ("一致预期：本次未取到足够的券商盈利预测"
                "（多数 A 股公司无券商跟踪）—— 无从对照")
    lines = [f"一致预期（近 {c.window_days} 天 {c.n_reports} 篇研报）",
             "  ⚠ 卖方预测仅作本报告结论的**对照**，"
             "不得作为本报告任何假设的依据。"]
    for y, e in sorted(c.eps.items()):
        lines.append(f"  FY{y} EPS 中位 {e.median}（{e.n} 家，"
                     f"区间 {e.low}~{e.high}，离散 {e.spread:.0%}）")
    for y, (prev, now) in sorted(c.drift.items()):
        if prev.median:
            d = (now.median - prev.median) / prev.median
            lines.append(f"  FY{y} 修正：{prev.median}（{prev.n} 家）"
                         f"→ {now.median}（{now.n} 家），{d:+.1%}"
                         f" —— 修正方向本身是信息")
    if c.ratings:
        lines.append("  评级分布：" +
                     "、".join(f"{k} {v} 家" for k, v in
                               sorted(c.ratings.items(), key=lambda x: -x[1])))
    return "\n".join(lines)
