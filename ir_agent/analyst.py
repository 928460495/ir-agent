"""分析师 Agent —— V1 第一步: 正文从模板换成 LLM 撰写。

V0 建立的占位符契约在这里第一次发挥它真正的作用。模型**可以看到数值**
（否则说不出「毛利率显著高于同业」这类有分析含量的话，只能堆砌占位符），
但输出里的数字位必须写 `[[source#key@period]]`。三道闸口:

  1. 裸数字 → `audit_bare_numbers` 拦截
  2. 伪造的占位符 → `render()` 抛 `UnresolvedReferenceError` 拦截
     （这比裸数字更隐蔽: 格式完全合法，只是账本里没有那条事实）
  3. 违规不是警告而是**驳回重写**，把具体违规点回传给模型

重试用尽则报错，**绝不出报告** —— 宁可没有，不要一份掺假的。
这条闭环才是「模型物理上编不了数字」在实践中成立的原因；
只有契约没有强制执行，等于没有契约。

本模块与具体 LLM 供应商无关: client 是任意 `str -> str` 可调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable

from ir_agent.citation import (
    REF_RE,
    UnresolvedReferenceError,
    audit_bare_numbers,
    audit_unit_duplication,
    format_value,
    render,
)
from ir_agent.ledger import Fact, FactLedger, LookAheadError

Client = Callable[[str], str]

# 账本 key → 中文行标签。模型看 key 猜不出含义，也就写不出像样的中文。
LABELS = {
    "revenue": "营业收入", "revenue_total": "营业总收入",
    "cost_of_revenue": "营业成本", "net_profit": "净利润",
    "net_profit_attr_parent": "归母净利润",
    "minority_interest_profit": "少数股东损益",
    "total_assets": "资产总计", "total_liabilities": "负债合计",
    "total_equity": "所有者权益合计", "equity_attr_parent": "归母权益",
    "minority_equity": "少数股东权益",
    "cash_begin": "期初现金", "cash_end": "期末现金",
    "cash_net_change": "现金净增加额", "cf_net_profit": "现金流量表净利润",
    "gross_margin": "毛利率", "net_margin": "净利率", "roe": "ROE",
    "asset_turnover": "总资产周转率", "equity_multiplier": "权益乘数",
    "revenue.yoy": "营业收入同比", "net_profit.yoy": "净利润同比",
    "market_cap": "总市值", "pb": "PB", "pe_ttm": "PE(TTM)",
    "last_price": "最新价", "net_profit_ttm": "归母净利润(TTM)",
    "net_profit_attr_parent_ttm": "归母净利润(TTM)",
    "revenue_ttm": "营业收入(TTM)", "ps_ttm": "PS(TTM)",
    # 估值假设 —— method=ESTIMATED，脚注里与实测数据区分
    "wacc": "折现率 WACC（假设）", "terminal_growth": "永续增长率（假设）",
    # 序数用中文: 标签会原样进提示词，「第 1 年」里的 1 会被审计判成裸数字，
    # 而它根本不是数值，账本里也没有任何占位符能代替它。
    **{f"growth_y{i}": f"第{cn}年营收增速（假设）" for i, cn in enumerate(
        "一二三四五六七八九十", start=1)},
    # 宏观 —— 名义/实际必须分列，永续增长率对齐的是名义
    "macro.gdp_nominal": "名义 GDP", "macro.gdp_nominal_yoy": "名义 GDP 同比",
    "macro.gdp_real_yoy": "实际 GDP 同比",
    "macro.cpi_yoy": "CPI 同比", "macro.ppi_yoy": "PPI 同比",
    # 行业
    "industry.revenue": "行业营业收入", "industry.revenue_yoy": "行业营收同比",
    "industry.cr5": "行业 CR5", "industry.peer_count": "行业样本家数",
    "industry.coverage": "行业数据覆盖率",
    "company.market_share": "本司市占率",
    "company.market_share_delta": "本司市占率变化",
    "company.industry_rank": "本司行业排名",
    "company.revenue_yoy_vs_industry": "本司相对行业超额增速",
    # 行业画像
    "industry.hhi": "行业 HHI 集中度",
    "industry.effective_players": "行业有效竞争者数",
    "industry.revenue_cagr": "行业营收多年 CAGR",
    "industry.dol_median": "行业经营杠杆 DOL 中位",
    "industry.dfl_median": "行业财务杠杆 DFL 中位",
    "industry.dtl": "行业总杠杆 DTL",
    "industry.income_elasticity": "行业收入弹性",
    "industry.leverage_median": "行业权益乘数中位",
    # 折现率输入 —— β 脱离窗口与基准没有意义，标签里点明口径
    "beta": "β（对沪深300，周频两年）", "beta.r_squared": "β 回归 R²",
    **{f"rate.cgb_{t}y": f"{cn}年期国债到期收益率"
       for t, cn in ((2, "两"), (5, "五"), (10, "十"), (30, "三十"))},
    # 可比 —— comps_facts 写入的键。没有标签就会在提示词里显示成裸 key，
    # 模型看到 "pb.percentile" 而不是「PB 同业分位」，用错的概率明显更高。
    "peer_count": "可比公司家数",
    **{f"{m}.percentile": f"{lab} 同业分位" for m, lab in (
        ("pb", "PB"), ("pe_ttm", "PE(TTM)"), ("roe", "ROE"),
        ("net_margin", "净利率"), ("gross_margin", "毛利率"),
        ("asset_turnover", "总资产周转率"), ("equity_multiplier", "权益乘数"))},
    **{f"{m}.peer_median": f"{lab} 同业中位数" for m, lab in (
        ("pb", "PB"), ("pe_ttm", "PE(TTM)"), ("roe", "ROE"),
        ("net_margin", "净利率"), ("gross_margin", "毛利率"),
        ("asset_turnover", "总资产周转率"), ("equity_multiplier", "权益乘数"))},
}

# 同义 key: 保留前者，后者不重复进清单 —— 两条都摆出来会让模型
# 以为是两个不同指标，从而写出「归母净利润TTM 与 净利润TTM 基本持平」这类废话。
_ALIASES = {"net_profit_attr_parent_ttm": "net_profit_ttm"}


class AnalystRefused(Exception):
    """反复违反占位符契约，拒绝出具报告。"""


@dataclass
class CatalogEntry:
    key: str
    period: str
    token: str
    label: str
    display: str


@dataclass
class FactCatalog:
    entries: list[CatalogEntry] = field(default_factory=list)
    ledger: FactLedger | None = None
    as_of: date | None = None
    # 本次实际取回的、可供正文引述的资料。挂在这里而不是当参数传，
    # 是因为 revision.py 复用 _violations —— 撰写与修订两条路径要用同
    # 一副闸门，否则修订稿可以把撰写时被拦下的转述再写回去。
    evidence: object = None

    @classmethod
    def from_ledger(cls, ledger: FactLedger, period: str, as_of: date,
                    extra_periods: tuple[str, ...] = ()) -> "FactCatalog":
        entries: list[CatalogEntry] = []
        seen: set[tuple[str, str]] = set()
        for key, per in sorted(ledger._facts):          # noqa: SLF001
            if per != period and per not in extra_periods:
                continue
            canonical = _ALIASES.get(key, key)
            if (canonical, per) in seen:
                continue
            try:
                f: Fact = ledger.get(key, per, as_of=as_of)
            except (KeyError, LookAheadError):
                continue
            seen.add((canonical, per))
            entries.append(CatalogEntry(
                # 给分析师的是**简写** —— 来源由账本解析。
                # 三段式的 source_id 含时间戳，既冗长又跨运行失效。
                key=key, period=per, token=f"[[{canonical}@{per}]]",
                label=LABELS.get(key, key),
                display=format_value(f)))
        return cls(entries=entries, ledger=ledger, as_of=as_of)

    @property
    def tokens(self) -> list[str]:
        return [e.token for e in self.entries]

    def render_for_prompt(self) -> str:
        w = max((len(e.label) for e in self.entries), default=8)
        return "\n".join(
            f"  {e.label:<{w}}  {e.display:>14}   写作 {e.token}"
            for e in self.entries)


_RULES = """你是一名卖方分析师，正在撰写研究报告正文。

**数字的写法（唯一的硬性规则）**
报告中的每一个数字都必须写成占位符，格式为 [[来源#字段@期间]]，
渲染阶段由系统填入真值并生成脚注。

- 下面「可用事实」列出了你能引用的全部数字，以及各自的占位符写法。
- **不得写出任何字面数字**（包括金额、比率、倍数、增速）。需要提到数字时，
  一律引用占位符原样照抄。
- **不得自行拼造占位符**。清单里没有的字段就是没有 —— 该说「数据未披露」，
  不要编造。
- **占位符渲染后自带单位**，其后不要再补一遍。清单「数值」一列显示的就是
  渲染结果 —— 显示「17 分位」，就写「处于同业 [[…]]」而不是
  「处于同业 [[…]]分位」；显示「4 家」，就写「有效竞争者 [[…]]」而不是
  「有效竞争者 [[…]]家」。否则正文会出现「17 分位分位」「4 家家」。
- 定性表述（「显著高于」「小幅回落」「处于同业前列」）可以自由使用，
  这正是你的价值所在。

**结构（自上而下，每一层的结论都要被下一层用到）**

这个顺序不是排版偏好，是论证顺序。顶级投行的首次覆盖报告都遵循同一条链：
宏观景气 → 行业格局 → 公司在行业中的位置 → 同业估值坐标 → 盈利预测假设。
每一段用 ## 二级标题。

1. **投资要点** —— 三到五句话讲清核心判断：这家公司值得关注的理由是什么，
   最关键的一个变量是什么。这是全文的结论，写在最前面。
2. **宏观环境** —— 只写与本行业需求相关的部分。价格类（CPI/PPI）对应
   成本与提价能力，GDP 增速对应总需求。**不相关就明说不相关**，
   不要为了凑段落硬拉关系。
3. **行业格局与产业链位置** —— 行业增速、集中度、本司份额及其变化；
   本司处在产业链的哪一环，上下游分别是谁。
   核心问题：这是一个扩张的行业还是收缩的行业？本司在其中是得份额还是失份额？
   **产业链不是背景介绍**：它要解释本司的议价能力从何而来 ——
   上游若高度集中而本司分散，成本就压不下去；下游若是少数几个大客户，
   价格就由不得自己。这一段要为第 4 节的五力判断提供因果，
   而不是各写各的。
4. **竞争格局与护城河** —— 波特五力与护城河判断。
   **每一条都要落到可观察的证据上**：说「现有竞争激烈」，就要能指向
   集中度或价格走势；说「买方议价强」，就要能指向应收账款天数或
   客户集中度。给出的战略分析里哪几力有账本锚点、哪几力是纯定性判断，
   **原样交代清楚**，不要把定性判断写得像有数据支撑。
   判断与数字打架时（例如自述成本优势而毛利率处于同业低分位），
   **把矛盾写出来** —— 那正是研报该说的东西。
5. **公司经营** —— 收入与利润的构成和趋势；ROE 拆到三因子（净利率 ×
   周转率 × 权益乘数），说明是哪一项在驱动。
6. **财务结构** —— 资产负债与现金。利润和现金是否匹配，差在哪里。
7. **同业比较与一致预期对照** —— 本司在同业中的估值与盈利能力位置。
   注意分位数的样本量：三家里的「第一」和三十家里的「第一」不是一回事。
   若给出了卖方一致预期，**说明本报告的判断与它的差异在哪、为什么** ——
   差异本身是本节最有价值的部分；但**不得**反过来用一致预期当作本报告
   任何假设的依据。
8. **估值假设的依据** —— **本节是全文的枢纽，必须写，而且必须写实。**

   下一步你要**亲自定下** WACC、永续增长率和预测期增速，没有人会在你之后
   复核。本节就是你为那些数字所做的论证，届时会被逐字引用作为依据 ——
   写不出来的，下一步就用不了。

   必须交代三条：

   - **折现率**：无风险利率与 β 都在上面的可用事实里，不必猜。把链条写清楚：
     无风险利率 + β × 股权风险溢价 = 股权成本，再说明如何得到 WACC。
     风险溢价是你的判断，要说出理由。**若 β 的 R² 偏低**，说明 CAPM 对
     本标的解释力有限，必须在此点明，不能装作没看见。
   - **收入增速**：应锚定在什么之上。典型写法：「以行业增速
     [[industry.revenue_yoy@…]] 为基准，考虑本司份额变化
     [[company.market_share_delta@…]] 作方向性调整」。若行业在收缩而你
     认为公司仍能增长，**必须在此说明理由** —— 这是最容易被质疑的地方。
   - **永续增长率**：它受名义 GDP 增速的硬约束（公司的永续增速高于名义 GDP，
     等于说它最终吞掉整个经济）。说明你打算取在什么水平、为什么。

   本节不必写出最终数值，但论证要完整到能支撑那些数值。
9. **风险提示** —— 具体到会证伪上述假设的事件，不写「市场波动」这类套话。
   给出的战略分析已经识别了**关键不确定性及其对应的建模驱动因子**，
   本节应当覆盖它们 —— 那几条正是下一步 Bear/Base/Bull 三情景的定性依据，
   在这里写清楚，情景分析才不是凭空调参数。

**写作要求**
- 中文，卖方研报语体，不用第一人称。
- 每段都要有**解读**而不只是复述数字。数字本身脚注里都有，正文的价值在于
  说明它意味着什么。
- 上下层要真的咬合：第 8 节的每一条假设，都应能追溯到第 2–7 节的某个结论。
  写不出这条链，说明前面的分析没做到位。
- **引号只用于逐字引述**。给术语加引号会被出处核验误判 ——
  需要强调时直接写，不要加引号。确要引述年报或研报原文时，
  逐字照抄并在紧邻处点明出处（「据年报披露，……」「某某证券研报指出，……」）。
- 不做投资评级，不给目标价 —— 那是下一步估值环节的产物，本稿只做分析。
- 只输出正文 Markdown，不要前言、不要解释你的写作过程。
"""


def build_prompt(
    catalog: FactCatalog,
    code: str,
    period: str,
    comps_summary: str | None = None,
    context: str | None = None,
    macro_summary: str | None = None,
    industry_summary: str | None = None,
    strategy_summary: str | None = None,
    chain_summary: str | None = None,
    consensus_summary: str | None = None,
) -> str:
    """组装提示词。

    宏观/行业/可比三段以**摘要**形式给出口径说明（覆盖率、样本量、分类层级），
    数值本身仍只能通过占位符引用。摘要里出现的数字是给模型判断用的上下文，
    与「可用事实」表同源 —— 提示词里出现裸数字、正文里却禁止引用，
    是自相矛盾的契约，这一点此前踩过。
    """
    parts = [_RULES,
             f"\n**标的**：{code}　**报告期**：{period}\n",
             "\n**可用事实**（左为含义，中为数值仅供你判断，右为必须写入的占位符）\n",
             catalog.render_for_prompt(), "\n"]
    if macro_summary:
        parts.append(f"\n**宏观口径**\n{macro_summary}\n")
    if industry_summary:
        parts.append(f"\n**行业口径**（决定第 3 节能下多强的结论）\n"
                     f"{industry_summary}\n")
    if chain_summary:
        parts.append(f"\n**产业链与公司所处环节**\n{chain_summary}\n")
    if strategy_summary:
        parts.append(f"\n**战略分析结论**（护城河、五力、关键不确定性）\n"
                     f"{strategy_summary}\n")
    if comps_summary:
        parts.append(f"\n**可比公司对照**\n{comps_summary}\n")
    if consensus_summary:
        parts.append(
            f"\n**卖方一致预期**（仅作本报告结论的对照）\n{consensus_summary}\n"
            "本报告的判断必须独立得出。若你的结论与一致预期方向不同，"
            "**说明差异从何而来** —— 那是本节最有价值的部分；"
            "但**不得**以一致预期为依据反推本报告的任何假设。\n")
    if context:
        parts.append(f"\n**补充背景**\n{context}\n")
    ev = getattr(catalog, "evidence", None)
    if ev is not None:
        parts.append(
            f"\n**出处规则**\n"
            f"凡是写成「据……」「根据……披露」这类**归属给外部来源**的引述，"
            f"引文必须逐字出自本次实际取回的资料，点名的机构也必须确实在其中。"
            f"本次可引述的来源：{ev.catalogue()}。\n"
            f"不必处处标注出处 —— 只有你**主动作出归属**时才受此约束；"
            f"用「」作强调不受影响。\n")
    return "".join(parts)


import re as _re

# 取字符串里的数字本体。裸数字提取出的是「19」，而账本显示值是「19 家」
# —— 单位不在 audit 的识别列表里，直接比对必然对不上。
_NUM_CORE = _re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _hint(literal: str, catalog: FactCatalog) -> str:
    """能对上账本的数字，直接把占位符告诉模型。

    只报「17.05% 是裸数字」，模型得自己在几十条事实里找对应项，常常找错或
    换个说法绕开。指出确切的占位符，一轮就能改对。

    更要紧的是别把**不可能满足**的项混进来: 清单里只要有一个模型无从修复的
    数字（如指数名称里的 300），整个重试循环就变成不可满足的约束，连本来
    能改的那几个也一起卡死。实测正是这样连挂三次。
    """
    def core(x: str) -> str | None:
        m = _NUM_CORE.search(x)
        return m.group(0).replace(",", "") if m else None

    want = core(literal)
    if want is not None:
        for e in catalog.entries:
            if core(e.display) == want:
                return f"{literal}（应写作 {e.token}）"
    return literal


# 排版类违规: 值得驳回重写，但**不值得为它毙掉整篇报告**。
# 「17 分位分位」读着别扭，与「引用了账本里不存在的事实」不是一个量级 ——
# 后者是造假，前者是错字。实测踩到: 加上单位检查那一轮，模型在重写中
# 反而伪造了一个占位符，三次耗尽，最终一个字都没产出 —— 而上一轮至少
# 出了一份带错字的完整报告。**为排版问题阻断整篇报告是比例失当。**
COSMETIC = ("重复写了单位",)


def is_cosmetic(v: str) -> bool:
    return any(k in v for k in COSMETIC)


def _violations(body: str, catalog: FactCatalog) -> list[str]:
    """返回违规说明列表；空列表表示通过。"""
    out: list[str] = []
    if not body.strip():
        out.append("输出为空。")
        return out

    bare = audit_bare_numbers(body)
    if bare:
        out.append("以下数字是字面值，必须改写为占位符："
                   + "、".join(_hint(b, catalog) for b in bare[:10]))

    if catalog.ledger is not None and catalog.as_of is not None:
        try:
            render(body, catalog.ledger, as_of=catalog.as_of)
        except UnresolvedReferenceError as e:
            out.append(f"以下引用在事实账本中不存在，属于伪造：{e}")

    if catalog.ledger is not None and catalog.as_of is not None:
        dup = audit_unit_duplication(body, catalog.ledger, catalog.as_of)
        if dup:
            out.append(
                "以下占位符后重复写了单位（渲染值已自带单位）："
                + "、".join(dup[:8])
                + " —— 请把紧跟其后的那个单位词删掉并调整句子。")

    if not REF_RE.search(body):
        out.append("正文没有引用任何事实占位符 —— 研报不能没有数字。")

    # 第四道闸: 出处。数字有三道闸，文字此前一道都没有 ——
    # 捏造一个出处比捏造一个数字容易得多，也更难被发现。
    if catalog.evidence is not None:
        from ir_agent.attribution import audit as audit_attribution

        out += audit_attribution(body, catalog.evidence)
    return out


def write_body(
    catalog: FactCatalog,
    client: Client,
    code: str = "",
    period: str = "",
    comps_summary: str | None = None,
    context: str | None = None,
    macro_summary: str | None = None,
    industry_summary: str | None = None,
    strategy_summary: str | None = None,
    chain_summary: str | None = None,
    consensus_summary: str | None = None,
    max_attempts: int = 3,
) -> tuple[str, int]:
    """调用模型撰写正文，违规则驳回重写。返回 (正文, 实际尝试次数)。"""
    prompt = build_prompt(catalog, code=code, period=period,
                          comps_summary=comps_summary, context=context,
                          macro_summary=macro_summary,
                          industry_summary=industry_summary,
                          strategy_summary=strategy_summary,
                          chain_summary=chain_summary,
                          consensus_summary=consensus_summary)
    last: list[str] = []

    for attempt in range(1, max_attempts + 1):
        body = client(prompt)
        last = _violations(body, catalog)
        if not last:
            return body, attempt
        if attempt == max_attempts and all(is_cosmetic(v) for v in last):
            # 最后一轮只剩排版问题 —— 收下这一稿。带几个错字的完整报告
            # 好过没有报告；问题本身仍会出现在验收审计里，不会被藏起来。
            return body, attempt
        prompt = (
            f"{prompt}\n\n---\n"
            f"**上一稿被驳回（第 {attempt} 次），原因如下：**\n"
            + "\n".join(f"- {v}" for v in last)
            + "\n\n请据此重写全文。数字一律使用上面「可用事实」中列出的占位符。\n"
        )

    raise AnalystRefused(
        f"连续 {max_attempts} 次违反占位符契约，拒绝出具报告。"
        f"最后一次的问题：{'；'.join(last)}"
    )
