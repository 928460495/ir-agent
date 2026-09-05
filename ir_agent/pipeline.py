"""V0 端到端流水线。

数据流:
    巨潮公告 ──────────→ 披露日 (as_of)
                              │
    东财三表 (Tier-1) ────────┤
    新浪三表 (Tier-2) ────────┼──→ 事实账本 ──→ 确定性算子 ──→ 占位符草稿
    腾讯/新浪行情 ────────────┘                                   │
                                                            渲染 + 审计
本阶段的「草稿」是模板生成的，不涉及 LLM。V1 起才把这一步交给分析师 Agent —
但占位符契约不变，所以那时模型依然无法编造数字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from ir_agent.audit import AuditResult, audit
from ir_agent.ledger import Fact, FactLedger, LookAheadError, Method
from ir_agent.market import market_of, quote_symbol
from ir_agent.periods_spot import spot_period
from ir_agent.operators import dupont, growth, margins, multiples, periods, ttm
from ir_agent.sources import cninfo, eastmoney, financials, quotes, resolve
from ir_agent.sources.financials import Statement
from ir_agent.sources.snapshot import SnapshotStore
from ir_agent.skips import SkipLog, SkipReason
from ir_agent.sources.annual_pdf import ExtractionFailed, fetch_and_extract
from ir_agent.validate.adjudicate import resolve_disagreements
from ir_agent.validate.cross import CrossReport, cross_reconcile
from ir_agent.validate.reconcile import ReconcileReport, reconcile

# 「第一季度报告」与「一季度报告」两种写法在 A 股都常见（五粮液用后者）。
# 后缀不能用 $ 锚死 —— 实测存在「（更新前）」「（更新后）」「（更正后）」等版本标记。
_PERIODIC = [
    (re.compile(r"(\d{4})年度?年?度?报告"), "FY"),
    (re.compile(r"(\d{4})年半年度报告"), "H1"),
    (re.compile(r"(\d{4})年(?:第)?三季度报告"), "Q1-Q3"),
    (re.compile(r"(\d{4})年(?:第)?一季度报告"), "Q1"),
]

# 这些不是定期报告本身:摘要是节选，英文版是同内容重复件，提示性/延期公告只是通知。
_NOT_A_REPORT = re.compile(
    r"摘要|英文版|提示性公告|关于.*的公告|说明|问询|更正公告"
    r"|第三支柱|分红|派息|利润分配|资本充足"   # 银行/保险的专项披露，不是定期财报
)


def disclosure_dates(announcements) -> dict[str, date]:
    """从定期报告公告中提取 {期间: 该期间报告的最晚披露日}。

    取**最晚**而非最早，是因为数据源（新浪/东财）给出的是**重述后**的数字。
    五粮液 2025 半年报有「更新前」(2025-08-28) 与「更新后」(2026-04-30) 两版；
    把重述后的数字配上最早披露日，等于凭空获得 8 个月的信息优势 —— 未来函数。

    代价是对「仅修订错别字」的重发过于保守。要做得更准，需要解析两版 PDF
    的原始数字分别入账（V1 影子 Agent 的工作），V0 选择宁可保守。
    """
    out: dict[str, date] = {}
    for a in announcements:
        title = a.title.strip()
        if _NOT_A_REPORT.search(title):
            continue
        for pattern, suffix in _PERIODIC:
            m = pattern.search(title)
            if not m:
                continue
            period = f"{m.group(1)}{suffix}"
            if period not in out or a.ann_date > out[period]:
                out[period] = a.ann_date
            break
    return out


@dataclass
class ResearchRun:
    code: str
    as_of: date
    period: str
    ledger: FactLedger
    store: SnapshotStore
    reconcile: ReconcileReport
    audit: AuditResult
    draft: str
    warnings: list[str] = None
    financials: object = None      # Resolution: 降级与交叉验证结果
    quotes: object = None
    cross: object = None            # CrossReport: 各源独立勾稽
    verdicts: dict = None           # {字段: Verdict} 年报原文裁定
    skips: object = None            # SkipLog: 按原因分类

    def __post_init__(self) -> None:
        if self.skips is None:
            self.skips = SkipLog()
        if self.warnings is None:
            self.warnings = []
        if self.verdicts is None:
            self.verdicts = {}

    def summary(self) -> str:
        lines = [f"{self.code} {self.period} @ {self.as_of.isoformat()}"]
        if self.financials is not None:
            lines.append(self.financials.summary(self.period))
        if self.quotes is not None:
            lines.append(self.quotes.summary(self.period))
        lines += [self.reconcile.summary()]
        if self.cross is not None and len(self.cross.per_source) > 1:
            # 交叉验证失效时更要说出来 —— 藏起来等于谎称验证过
            lines.append(self.cross.summary())
        if self.verdicts:
            lines.append("年报原文裁定:")
            for k, v in self.verdicts.items():
                lines.append(f"  · {k}: {v.describe()}")
        lines.append(self.audit.summary())
        if self.skips.entries:
            lines.append(self.skips.summary())
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


def unresolved_disagreements(disagreements, verdicts) -> list:
    """筛出**未被原文裁定解决**的分歧。

    已裁定且有明确胜者的分歧不该再拦 —— 否则裁决白做。
    裁不出胜者（原文与任何源都对不上）的必须继续拦。
    """
    return [d for d in disagreements
            if not (verdicts.get(d.key) and verdicts[d.key].winner)]


def _find_annual_report(announcements, year: int):
    """在公告列表中找该年度的正式年报（排除摘要与英文版）。"""
    want = re.compile(rf"{year}\s*年度?年?度?报告")
    best = None
    for a in announcements:
        t = a.title.strip()
        if not want.search(t) or _NOT_A_REPORT.search(t):
            continue
        if best is None or a.ann_date < best.ann_date:
            best = a                      # 取首发件，不取更正重发
    return best


def statement_sources(eastmoney_fetch, sina_fetch):
    """三表的 Tier 顺序 —— 东财 Tier-1，新浪 Tier-2。

    东财是规范化接口: 字段名跨行业一致、自带公告日、覆盖金融业模板，
    因此字段映射错位这类**静默出错**的风险低得多。
    新浪解析路径完全独立（GBK 文本标签），降为 Tier-2 后交叉验证价值不变；
    且东财反爬更严时它仍是可用的兜底 —— 那属于**大声失败**，有降级兜得住。
    """
    return [("eastmoney", eastmoney_fetch), ("sina", sina_fetch)]


def _quote_facts(code: str, store: SnapshotStore,
                 cross_check: bool = True):
    """腾讯为 Tier-1（含市值/PE/PB），新浪为 Tier-2（仅价格）。

    新浪缺市值，降级后估值章节会缺失 —— 这是有意的:
    谎称有市值比没有市值更危险。
    """
    symbol = quote_symbol(code)

    def _mk(source: str, fetch, url: str):
        def _f() -> list[Fact]:
            fetched_at = datetime.now()
            q = fetch(symbol)
            sid = store.save(source=source,
                             payload={k: str(v) for k, v in q.items()},
                             url=url, fetched_at=fetched_at,
                             params={"symbol": symbol})
            as_of = q["as_of"].date()
            # 行情是即期量 —— period 用报价日，不挂在会计期上
            sp = spot_period(as_of)
            out = []
            for key, value in (("market_cap", q.get("market_cap")),
                               ("last_price", q.get("last"))):
                if value is not None:
                    out.append(Fact(key=key, value=value, unit="元",
                                    currency="CNY", period=sp, as_of=as_of,
                                    source_id=sid, method=Method.REPORTED))
            return out
        return _f

    return resolve.resolve([
        ("tencent", _mk("tencent", quotes.fetch_quote,
                        quotes.URL.format(symbol=symbol))),
        ("sina_hq", _mk("sina_hq", quotes.fetch_quote_sina,
                        quotes.SINA_URL.format(symbol=symbol))),
    ], cross_check=cross_check)


def _compose(period: str, prior: str | None, led: FactLedger, as_of: date,
             skips: SkipLog, spot: str | None = None) -> str:
    """模板草稿。所有数字位一律写占位符，绝不写字面值。

    **逐段容错**: 银行没有营业成本、亏损公司没有 PE —— 一个字段缺失不该
    杀掉整份报告。每段独立 try，缺哪段记哪段，其余照常输出。
    """
    def ref(key: str, p: str) -> str:
        return led.get(key, p, as_of=as_of).ref

    parts: list[str] = []

    def section(name: str, build, required: bool = False) -> None:
        try:
            parts.append(build())
        except KeyError as e:
            if required:
                raise
            skips.add(name, SkipReason.DATA_NOT_DISCLOSED, str(e).strip("'"))
        except LookAheadError:
            skips.add(name, SkipReason.NOT_APPLICABLE, "数据晚于分析时点")

    # 经营概览是唯一必需段 —— 连营收净利都没有就没有报告可言
    section("经营概览", lambda: (
        f"## 经营概览（{period}）\n"
        f"报告期内实现营业收入 {ref('revenue', period)}，"
        f"净利润 {ref('net_profit', period)}，"
        f"其中归属于母公司所有者的净利润 {ref('net_profit_attr_parent', period)}。\n"
    ), required=True)

    section("营业成本", lambda: (
        f"营业成本 {ref('cost_of_revenue', period)}。\n"))

    # 毛利率与杜邦拆成两段: 银行/保险没有毛利率，但 ROE 是它们最核心的指标，
    # 绑在一段会让一个不适用的字段带走一个关键结论。
    parts.append("\n## 盈利质量\n")
    section("毛利率", lambda: (
        f"毛利率 {ref('gross_margin', period)}。"))
    section("杜邦分解", lambda: (
        f"净利率 {ref('net_margin', period)}，ROE 为 {ref('roe', period)}，"
        f"由净利率 {ref('net_margin', period)}、"
        f"总资产周转率 {ref('asset_turnover', period)} 与"
        f"权益乘数 {ref('equity_multiplier', period)} 三因子构成。\n"))

    section("资产负债结构", lambda: (
        f"\n## 资产负债结构\n"
        f"期末资产总计 {ref('total_assets', period)}，"
        f"负债合计 {ref('total_liabilities', period)}，"
        f"所有者权益合计 {ref('total_equity', period)}。"
        f"期末现金及现金等价物余额 {ref('cash_end', period)}。\n"))

    if prior:
        section("同比", lambda: (
            f"\n## 同比\n营业收入同比 {ref('revenue.yoy', period)}，"
            f"净利润同比 {ref('net_profit.yoy', period)}。\n"))

    if spot is not None:
        def valuation() -> str:
            val = (f"\n## 估值（截至 {spot}）\n当前市值 {ref('market_cap', spot)}，"
                   f"对应 PB {ref('pb', spot)}")
            try:
                val += f"，PE(TTM) {ref('pe_ttm', spot)}"
            except KeyError:
                skips.add("PE(TTM)", SkipReason.NOT_APPLICABLE,
                          "TTM 盈利为负或不足四个单季")
            return val + "。\n"
        section("估值", valuation)

    return "".join(parts)


def run(
    code: str,
    year: int,
    as_of: date,
    snapshot_dir: Path | str = "snapshots",
    period_suffix: str = "FY",
    cross_check: bool = True,
    adjudicate_on_conflict: bool = True,
) -> ResearchRun:
    store = SnapshotStore(snapshot_dir)
    led = FactLedger()
    period = f"{year}{period_suffix}"
    prior_period = f"{year - 1}{period_suffix}"

    # 1) 公告 → 披露日。年报在次年发布，故区间跨到 year+1。
    anns, truncated = cninfo.search_with_status(
        code, date(year, 1, 1), date(year + 1, 12, 31), store=store)
    as_of_map = disclosure_dates(anns)
    warnings: list[str] = []
    if truncated:
        warnings.append(
            "公告分页被截断，部分期间的披露日可能缺失 —— "
            "任何「跳过」都可能是抓取不全而非数据不存在。")
    if period not in as_of_map:
        raise RuntimeError(
            f"未找到 {period} 的定期报告公告，无法确定 as_of。"
            "拒绝用抓取日代替披露日。"
        )

    # 2) 三表 → 事实。Tier-1 新浪 / Tier-2 东财，两源都成功时交叉验证。
    market = market_of(code)

    def _sina_facts() -> list[Fact]:
        out: list[Fact] = []
        for y in (year, year - 1):
            tables, sids = financials.fetch_statements_with_provenance(
                code, y, store=store)
            out.extend(financials.to_facts(
                tables, as_of_by_period=as_of_map, source_id=sids))
        return out

    def _em_facts() -> list[Fact]:
        facts, _ = eastmoney.fetch_statements_em(code, market=market, store=store)
        return facts

    fin = resolve.resolve(
        statement_sources(_em_facts, _sina_facts),
        cross_check=cross_check,
    )
    for f in fin.facts:
        led.put(f)

    # 2b) 本期存在分歧时，用年报原文裁定。
    #     年报动辄数百页数 MB，只在真的有分歧时才下载。
    verdicts: dict = {}
    if adjudicate_on_conflict and fin.has_blocking_disagreement(period) \
            and period_suffix == "FY":
        annual = _find_annual_report(anns, year)
        if annual is None:
            warnings.append(f"{period} 存在两源分歧，但未找到 {year} 年报公告，无法裁定。")
        else:
            try:
                pdf_rows = fetch_and_extract(
                    annual.url,
                    keys=["total_assets", "total_liabilities", "total_equity",
                          "equity_attr_parent", "minority_equity",
                          "revenue", "cost_of_revenue", "net_profit",
                          "net_profit_attr_parent", "minority_interest_profit",
                          "cash_net_change", "cash_begin", "cash_end",
                          "cf_net_profit"],
                    cache_dir=Path(snapshot_dir) / "pdf")
                picked, verdicts = resolve_disagreements(
                    fin.raw, pdf_rows, period, primary=fin.primary)
                led = FactLedger()
                for f in picked:
                    led.put(f)
            except (ExtractionFailed, OSError) as e:
                warnings.append(f"年报裁定失败（{e.__class__.__name__}），"
                                f"沿用 Tier-1 取值: {str(e)[:80]}")

    # 3) 行情。Tier-1 腾讯（含市值/PE/PB）/ Tier-2 新浪（仅价格）。
    quote_res = _quote_facts(code, store, cross_check=cross_check)
    for f in quote_res.facts:
        led.put(f)
    spot = next((f.period for f in quote_res.facts if f.key == "market_cap"), None)

    # 4) 确定性算子
    skips = SkipLog()
    if truncated:
        skips.add("公告抓取", SkipReason.FETCH_INCOMPLETE, "分页达上限，上游仍有数据")

    def _try(name: str, fn) -> None:
        """算子也要容错: 银行没有营业成本、保险没有毛利，
        在 run() 里裸抛会让整轮以一个无信息量的 KeyError 收场。"""
        try:
            fn()
        except KeyError as e:
            skips.add(name, SkipReason.DATA_NOT_DISCLOSED, str(e).strip("'"))
        except ZeroDivisionError as e:
            skips.add(name, SkipReason.NOT_APPLICABLE, str(e))
        except LookAheadError:
            skips.add(name, SkipReason.NOT_APPLICABLE, "数据晚于分析时点")

    _try("利润率", lambda: margins.compute(led, period, as_of=as_of))
    _try("杜邦分解", lambda: dupont.compute(led, period, as_of=as_of))

    # 单季拆分 → TTM → 激活 PE。累计数不能直接相加，必须先拆单季。
    for key in ("revenue", "net_profit", "net_profit_attr_parent"):
        for y in (year, year - 1):
            periods.decumulate(led, key, y, as_of=as_of)
    quarter = {"FY": 4, "Q1-Q3": 3, "H1": 2, "Q1": 1}[period_suffix]
    for key in ("net_profit_attr_parent", "revenue"):
        if ttm.compute(led, key, year, quarter, as_of=as_of,
                       target_period=period) is None:
            skips.add(f"{key} TTM", SkipReason.FETCH_INCOMPLETE
                      if truncated else SkipReason.DATA_NOT_DISCLOSED,
                      "最近四个单季不齐")
    # PE 惯例用归母口径
    try:
        f = led.get("net_profit_attr_parent_ttm", period, as_of=as_of)
        led.put(Fact(key="net_profit_ttm", value=f.value, unit=f.unit,
                     currency=f.currency, period=period, as_of=f.as_of,
                     source_id=f.source_id, method=f.method,
                     derived_from=f.derived_from, basis="parent"))
    except (KeyError, LookAheadError):
        pass

    if spot is None:
        skips.add("估值倍数", SkipReason.DATA_NOT_DISCLOSED, "行情源未提供市值")
    else:
        try:
            multiples.compute(led, period, as_of=as_of, spot_period=spot)
        except KeyError:
            skips.add("估值倍数", SkipReason.DATA_NOT_DISCLOSED, "行情数据缺失")
        except LookAheadError:
            skips.add("估值倍数", SkipReason.NOT_APPLICABLE, "行情快照晚于分析时点")

    has_prior = True
    for key in ("revenue", "net_profit"):
        try:
            growth.yoy(led, key, period, prior_period, as_of=as_of)
        except ValueError:
            has_prior = False
            skips.add(f"同比（{key}）", SkipReason.NOT_APPLICABLE, "基期为负")
            break
        except (KeyError, ZeroDivisionError, LookAheadError):
            has_prior = False
            skips.add(f"同比（{key}）",
                      SkipReason.FETCH_INCOMPLETE if truncated
                      else SkipReason.DATA_NOT_DISCLOSED,
                      f"缺 {prior_period}")
            break

    # 5) 校验 + 草稿 + 审计
    rec = reconcile(led, period, as_of=as_of)
    by_source: dict[str, list] = {}
    for name, fs in fin.raw.items():
        by_source[name] = fs
    xr = cross_reconcile(by_source, period, as_of=as_of) if by_source else None
    draft = _compose(period, prior_period if has_prior else None,
                     led, as_of, skips, spot=spot)
    aud = audit(draft, led, store, as_of=as_of)

    return ResearchRun(code=code, as_of=as_of, period=period, ledger=led,
                       store=store, reconcile=rec, audit=aud, draft=draft,
                       financials=fin, quotes=quote_res, skips=skips,
                       cross=xr, warnings=warnings, verdicts=verdicts)


