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
- 定性表述（「显著高于」「小幅回落」「处于同业前列」）可以自由使用，
  这正是你的价值所在。

**写作要求**
- 中文，卖方研报语体，不用第一人称。
- 结构：经营概览 / 盈利质量 / 财务结构 / 风险提示，各段用 ## 二级标题。
- 每段都要有**解读**而不只是复述数字：为什么 ROE 是这个水平（拆到三因子）、
  毛利率变化说明了什么、现金与利润是否匹配。
- 不做投资评级，不给目标价 —— 那需要估值模型，本阶段尚未接入。
- 只输出正文 Markdown，不要前言、不要解释你的写作过程。
"""


def build_prompt(
    catalog: FactCatalog,
    code: str,
    period: str,
    comps_summary: str | None = None,
    context: str | None = None,
) -> str:
    parts = [_RULES,
             f"\n**标的**：{code}　**报告期**：{period}\n",
             "\n**可用事实**（左为含义，中为数值仅供你判断，右为必须写入的占位符）\n",
             catalog.render_for_prompt(), "\n"]
    if comps_summary:
        parts.append(f"\n**可比公司对照**\n{comps_summary}\n")
    if context:
        parts.append(f"\n**补充背景**\n{context}\n")
    return "".join(parts)


def _violations(body: str, catalog: FactCatalog) -> list[str]:
    """返回违规说明列表；空列表表示通过。"""
    out: list[str] = []
    if not body.strip():
        out.append("输出为空。")
        return out

    bare = audit_bare_numbers(body)
    if bare:
        out.append("以下数字是字面值，必须改写为占位符：" + "、".join(bare[:10]))

    if catalog.ledger is not None and catalog.as_of is not None:
        try:
            render(body, catalog.ledger, as_of=catalog.as_of)
        except UnresolvedReferenceError as e:
            out.append(f"以下引用在事实账本中不存在，属于伪造：{e}")

    if not REF_RE.search(body):
        out.append("正文没有引用任何事实占位符 —— 研报不能没有数字。")
    return out


def write_body(
    catalog: FactCatalog,
    client: Client,
    code: str = "",
    period: str = "",
    comps_summary: str | None = None,
    context: str | None = None,
    max_attempts: int = 3,
) -> tuple[str, int]:
    """调用模型撰写正文，违规则驳回重写。返回 (正文, 实际尝试次数)。"""
    prompt = build_prompt(catalog, code=code, period=period,
                          comps_summary=comps_summary, context=context)
    last: list[str] = []

    for attempt in range(1, max_attempts + 1):
        body = client(prompt)
        last = _violations(body, catalog)
        if not last:
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
