"""端到端流程 —— 一次跑完，估值假设由模型自主决定（见 research.py 模块说明）。

独立成模块而非塞进 __main__: CLI 已经有十来个开关，再叠一段长分支会让
两种用法互相纠缠。这里的流程是线性的、可单独测试的。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from ir_agent.analyst import AnalystRefused, FactCatalog, write_body
from ir_agent.audit import audit
from ir_agent.clients.claude_cli import ClaudeCliError, make_client
from ir_agent.dashboard import build_dashboard
from ir_agent.debate import DebateRefused, run_debate
from ir_agent.drivers import DecompositionInvalid
from ir_agent.market import market_of
from ir_agent import dcf
from ir_agent.research import (
    ASSUMPTIONS_FAILED_JSON,
    ASSUMPTIONS_MD,
    ASSUMPTIONS_ROUNDS_MD,
    ResearchOutcome,
    Stage,
    assumption_template,
    next_actions,
)
from ir_agent.revision import RevisionFailed, revise_from_verdicts
from ir_agent.sources import eastmoney
from ir_agent.xlsx import build_workbook

FORECAST_YEARS = 5


def _prior_period(period: str) -> str | None:
    """上年同期。2025FY → 2024FY，2025H1 → 2024H1。"""
    if len(period) < 5 or not period[:4].isdigit():
        return None
    return f"{int(period[:4]) - 1}{period[4:]}"


def _load_macro(r, as_of: date) -> tuple[str, str, str]:
    """宏观事实入账本。返回 (给人看的摘要, 给模型看的口径, 失败说明)。

    两份摘要不能合一: 看板要读数，提示词要占位符。给模型看数值而它又
    无法引用，就是在诱导它写裸数字然后驳回它 —— 自相矛盾的契约。
    """
    from ir_agent.sources.macro import MacroSnapshot, MacroUnavailable, fetch_macro

    try:
        facts = fetch_macro()
    except MacroUnavailable as e:
        return "", "", f"宏观事实（{e}）"
    for f in facts:
        r.ledger.put(f)
    snap = MacroSnapshot(facts, as_of=as_of)

    # 周期定位需要利率曲线（期限利差是政策维度的信号），而利率在
    # _load_capm 里加载 —— 那一步在本函数之后。因此这里只出指标，
    # 周期定位交给 _load_cycle 在两者都就绪后再做。
    return snap.describe(), snap.for_prompt(), ""


def _external_md(corpus, consensus) -> str:
    """外部资料清单的人读版。**唯一一处渲染口径。**

    此前这段拼接只写在 `_load_external` 里，于是它渲染的是「券商研报入库
    那一刻」的语料库；`_load_openweb` 随后往**同一个** Corpus 里再加最多
    10 篇文档，清单却不会重算。实测（600183 第一阶段）终端与看板都写
    「外部资料 9 篇 / [卖方研究] 9 篇」，而进了战略/产业链/宏观定性三节
    提示词的是 19 篇、其中 10 篇【财经媒体】——清单于是成了一句**主动的
    假陈述**：读者据此以为本轮的外部依据全部来自卖方研究。

    这正是本项目「缺什么必须显式列出来」的反面：只列一部分，读者会以为
    那就是全部。渲染口径收在这一个函数里，两个阶段共用。
    """
    from ir_agent import corpus as C

    if corpus is None:
        return ""
    return corpus.summary() + "\n\n" + C.consensus_md(consensus)


def _refresh_external_md(r, md: dict) -> None:
    """开放网络文档入库之后，把清单按**语料库的实际内容**重渲染一遍。

    `md` 会原样写进 state.json、进看板、并在第二阶段读回——不重渲染，
    这三处说的都是采集之前的那个语料库。
    """
    if getattr(r, "corpus", None) is None:
        return
    md["外部资料"] = _external_md(r.corpus, getattr(r, "consensus", None))


def _load_external(code: str, r, as_of: date) -> tuple[str, str, str]:
    """公司之外的资料入语料库。返回 (给人看的, 给模型看的, 失败说明)。

    此前宏观、行业、战略三块的资料全部来自数字接口与公司自己的年报 ——
    没有一句话来自公司和交易所之外。取不到时如实说「外部视角缺位」，
    不静默降级: 读者会以为那些判断有外部印证。
    """
    from ir_agent import corpus as C

    try:
        cp, cs = C.gather(as_of=as_of, code=code)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"外部资料（{e.__class__.__name__}: {e}）"
    r.corpus, r.consensus = cp, cs
    if cs is not None and cs.has_data():
        from ir_agent.sources.reports import consensus_facts
        for f in consensus_facts(cs):
            r.ledger.put(f)
    md = _external_md(cp, cs)
    # **缺什么都要说出来，一致预期也不例外。** 原来只为「一篇研报都没
    # 取到」设了话，一致预期为空时直接静默返回——实测（生益科技 600183
    # 第一阶段）研报取到 9 篇、一致预期一条没有，于是 brief.md 里
    # 零条 `consensus.*`（长电那轮有 14 条），「同业比较与一致预期对照」
    # 整节失去外部校验，而终端产物清单一个字没提。
    #
    # 研报有、一致预期没有，是很常见的组合（多数 A 股没有券商跟踪 EPS
    # 预测），正因为常见才更要说出来：读者会默认那一节有外部数据背书。
    notes: list[str] = []
    if not cp.docs:
        notes.append("未取到任何券商研报，外部视角缺位")
    if cs is None or not cs.has_data():
        notes.append("未取到一致预期（券商 EPS 预测），"
                     "同业比较与一致预期对照一节缺外部校验")
    if notes:
        prompt = "" if not cp.docs else C.consensus_prompt(cs)
        return md, prompt, "外部资料（" + "；".join(notes) + "）"
    return md, C.consensus_prompt(cs), ""


def _company_name(code: str) -> str:
    """公司简称。取不到就退回代码 —— 搜索提示词里写代码也能用。"""
    try:
        from ir_agent.sources.taxonomy import fetch_all
        rows = fetch_all()
    except Exception:                                       # noqa: BLE001
        return code
    me = next((e for e in rows if e.code == code), None)
    return me.name if me is not None else code


def _load_openweb(code: str, r, as_of: date, out: Path, client,
                  industry: str) -> list[str]:
    """开放网络文档入语料库。返回要记进产物清单的条目。

    **这里不能放进 `_analyse_facts`。** 那一层的不变量写在它自己的
    docstring 上：「不调模型，因此两个阶段都要跑」。而本函数要调模型，
    放进去有两个后果，第二个是致命的：

      1. 成本翻倍 —— 两个阶段各跑一次
      2. 第二阶段可能取到**与第一阶段不同的文档**，于是第一阶段正文里
         写下的外部引文，在第二阶段核验不过

    第 2 条是本项目已经吃过的那类亏。根因同一个：**一个不可复现的产物
    被放在了会被重跑的位置上。** corpus.gather 能待在确定性层，是因为
    东财接口同 as_of 返回同一批研报；discover 没有这个性质，网页本身
    也会变。因此第一阶段落盘、第二阶段读回（见 `_restore_openweb`）。
    """
    from ir_agent.sources import openweb
    from ir_agent.sources.external import Corpus

    if r.corpus is None:
        r.corpus = Corpus(as_of=as_of)
    try:
        res = openweb.gather(client, code=code, company=_company_name(code),
                             industry=industry, as_of=as_of, corpus=r.corpus)
    except Exception as e:                                  # noqa: BLE001
        return [f"开放网络采集未执行（{e.__class__.__name__}: {str(e)[:120]}）"]

    skipped: list[str] = []
    try:
        openweb.save(res, out / openweb.OPENWEB_JSON)
    except Exception as e:                                  # noqa: BLE001
        # **落盘失败不能连带毁掉本轮研究。** `save()` 底层是
        # `Path.write_text`，输出目录被回收、磁盘写满、权限被收回都会在
        # 这里炸——这些是运行环境的事，不是采集失败，不该把整轮 analyse
        # 一起拖下水。
        #
        # 但也不能只说「保存失败」了事：`res.docs` 里的文档此刻已经在
        # `r.corpus` 里，本轮简报仍能引用它们、核验也能通过；跨阶段却
        # 存活不了——`_restore_openweb` 读的正是这里本该写出的文件，
        # 读不到就是空语料库。于是简报正文里写下的开放网络引文，到了
        # 第二阶段会被自己的核验闸门拒掉。这个具体后果必须说出来，
        # 不能让读者以为"保存失败"只是无关痛痒的一句话。
        skipped.append(
            f"开放网络资料未能落盘（{e.__class__.__name__}: {str(e)[:120]}）"
            "——本阶段采集到的文档仍在本轮语料库中、可支撑当前简报，"
            "但不会跨阶段存活：第二阶段读不到这份文件，"
            "简报中写下的相关外部引文到时可能核验不过")

    if not res.docs:
        # **「一篇都没取到」不等于「没有可说的细节」。** 候选一个都没
        # 抓成功、与候选找到了但逐个抓取失败，是两种不同的现场——前者
        # 该去查采集提示词或联网工具，后者该去查抓取环节；不把
        # fetch_failed/missing_slots 带出来，两者在终端上长得一模一样。
        extra: list[str] = []
        if res.fetch_failed:
            extra.append(f"{res.fetch_failed} 篇抓取失败")
        if res.missing_slots:
            extra.append("缺失主题：" + "、".join(res.missing_slots))
        detail = "（" + "；".join(extra) + "）" if extra else ""
        skipped.append(f"开放网络资料：一篇都没取到{detail}，"
                       "定性判断的外部依据仍只有券商研报一类来源")
        return skipped

    return skipped + _openweb_notes(res)


def _openweb_notes(res) -> list[str]:
    """采集结果里要记进产物清单的条目。**两个阶段共用同一份措辞。**

    两处各写一遍同一组说明，早晚会分叉——而且已经分叉过：
    `_restore_openweb` 此前只报 `model_dated`，把 `missing_slots` 与
    `fetch_failed` 整个丢掉，而第二阶段产出的恰恰是 report.md 这个最终
    交付物。第一阶段说了「竞争格局与份额：未取到」，第二阶段的读者却
    什么都看不到，只能以为资料是全的。

    缺口口径是产品承诺的一部分，不是某一个阶段的内部细节，因此单一定义
    放在这里，两个阶段都调它。

    **格式：一行一条，缩进的 `·`，跟 `openweb.summary()` 同构，不是
    `；` 拼成一整行。** 此前这里把「N 篇抓取失败：原因 1；原因
    2」「缺口 1：未取到」「缺口 2：未取到」全部揉进同一段用 `；` 连
    的文字，而失败原因本身也带着 `：`（`f"{url}：{诊断}"`）——三层
    「用『；』分隔的条目」「条目内部又用『：』分隔的字段」叠在一行，
    读者（以及任何做字符串切分的下游）分不出「行业规模与增速：未取到」
    到底是第四条抓取失败原因，还是缺口清单里独立的一条。十条失败原因
    时这行能长到一千六百字符，直接糊进 md/state.json/看板。`openweb.
    summary()` 早就用「每行一条、缩进 `·`」的格式解决了同一个问题——
    这里改成同一种格式，携带的信息一个字都没少，只是换成人和程序都能
    分清边界的排布。仍然只返回**一个**列表元素（内部用换行分隔），不
    改变 `_load_openweb`/`_restore_openweb` 把它拼进 `skipped`（每个
    元素对应清单里一行）的既有语义。
    """
    # 前缀与「开放网络资料：一篇都没取到…」刻意分开。那条是真缺口、
    # 该落 `_mark` 的「未生成」；本条是**采到了之后**的注意事项清单
    # （抓取失败几篇、哪个槽位没取到、主机校验是否降级），把它也
    # 标成「未生成」就是在说没采到——而 docs 里明明有东西。
    # 见 `research._PREFIX` 的注释：这是同一个失败的第四次。
    lines: list[str] = ["开放网络资料采集："]
    if res.fetch_failed:
        # `fetch_failed` 曾经是个不透明的数字——「模型给了个 PDF」「模型
        # 给了个内网地址」「这个域名 DNS 解析不出来」「正文提取为空」
        # 四种截然不同的诊断，打印出来长得一模一样。带代理的机器上尤其
        # 会撞见其中一种把全部候选一次性打光（见 openweb.OpenWebResult.
        # fetch_failed_reasons 的注释），读者必须能分清是哪一种，而不是
        # 只看到一个数字猜。
        lines.append(f"  · {res.fetch_failed} 篇抓取失败")
        for reason in res.fetch_failed_reasons:
            lines.append(f"    - {reason}")
    if res.model_dated:
        lines.append(f"  · {res.model_dated} 篇的发布日期由模型给出，"
                     "未经代码核验")
    if res.host_check_degraded:
        # 环境决定的降级，不是缺陷——但读者无从从「N 篇入库」里看出
        # 「这一轮的内网地址拦截靠的是代理、不是本代码」。见
        # `openweb.OpenWebResult.host_check_degraded` 与
        # `openweb._reject_unsafe_host` 里那段实跑事故记录。
        lines.append(f"  · {res.host_check_degraded} 篇入库时主机安全校验"
                     "降级（本机 DNS 被代理劫持，IP 层校验无从进行）"
                     "——内网地址拦截本轮依赖代理侧")
    for slot in res.missing_slots:
        lines.append(f"  · {slot}：未取到")
    if len(lines) == 1:
        return []
    return ["\n".join(lines)]


def _media_tier_note(corpus) -> list[str]:
    """依据引用了 Tier.MEDIA 级来源、核验仍然通过——不拒绝，但必须在
    产物清单里说出来。

    `TIER_RULE[Tier.MEDIA]` 写死「仅作线索，不得作为依据」，但
    `Corpus.verify()`（`basis.validate()` 的核验基准）对任何分级的
    文档一视同仁地放行，只要引文逐字出现——这条分级规矩此前只活在
    提示词里，代码毫无察觉。两轮评审都指出这个洞。用户的决定是警告、
    不是硬拒绝：产业链分析已经因为一次引文核验失败整节夭折过三次，
    「引文找不到」和「引文找得到但来源分级不够格」是两种严重程度
    截然不同的问题，不能用同一种「拒绝」处置。

    `Corpus.verify()` 每命中一次**此前没见过的** (文档, 引文) 组合就往
    `corpus.media_cited` 追加一条记录，按这个键去重（见该字段与
    `verify()` 的文档字符串）——`validate()` 从 strategy.py/
    value_chain.py/macro_view.py/assume.py 好几个模块调用，且每个模块
    自己的 `check()` 循环里 `max_attempts=3`，被打回重试的草稿也会
    重新跑一遍 `validate()`。不去重的话，这里报的「N 处」数的是"重试
    了几次"，跟读者关心的"最终定稿实际依赖了几条 MEDIA 依据"是两件
    不同的事，且会随重试次数虚高——这行文案是产物清单里一条肯定性
    声明，报错比不报更糟。去重之后，`len(corpus.media_cited)` 数的是
    不同的 (文档, 引文) 组合数，即下文「N 处」里的**处**：同一篇文档
    的两条不同引文算两处，同一条引文（哪怕来自不同依据对象、不同
    重试轮次）只算一处。这里不去每个调用点分别处理，只在本阶段研究
    流程收尾、写 `ResearchOutcome.skipped` 之前读一次
    `corpus.media_cited` 的总数，与 `_openweb_notes` 汇总
    `res.fetch_failed_reasons` 是同一个思路：调用方各自往同一个
    累加器上记账，产物清单在最后统一渲染一次。
    """
    if corpus is None or not corpus.media_cited:
        return []
    from ir_agent.sources.external import TIER_RULE, Tier
    return [f"依据引用财经媒体级来源 {len(corpus.media_cited)} 处，"
           f"核验通过但分级规则是「{TIER_RULE[Tier.MEDIA]}」"]


def _factor_host_check_note(factor_data: dict) -> list[str]:
    """`gaps.collect` ④外部网络分支——轮次级主机安全校验降级聚合。

    **与 `_openweb_notes` 里「N 篇入库时主机安全校验降级」说的是同一件
    事**（`Fetched.host_check_degraded` 的契约，见 openweb.py:770-773：
    「降级不阻断抓取，但必须一路传到产物清单」），但两条聚合覆盖的是
    两个不同的 fetch 调用点：`_openweb_notes` 读的是 `openweb.gather`
    自己记的 `OpenWebResult.host_check_degraded`；`gaps.collect` ④分支
    走的是 `openweb.verify_finding` 这第二个调用点，从来没有往
    `OpenWebResult` 记过账，此前因此一直是真实的欠计数（见
    `gaps.py` 第 394-410 行、`FactorData.host_check_degraded` 字段
    上的注释）——不是已经解决、只是换了个地方展示。

    **不并进 `_openweb_notes` 那一条，两条分开报。** 那一条数的单位是
    **文档**（`res.docs` 里入库的篇数）；这里数的单位是**因子**
    （`gaps.FactorData`，一个因子的证据可能对应好几条 finding/文档，
    只要其中任意一条降级过，这个因子就算一次）。两种不同的计数单位
    拼进同一句话，算出来的数字谁都解释不清是「几篇」还是「几个」。

    **只读结构化字段 `FactorData.host_check_degraded`，不碰
    `note` 文本。** `note` 是拼给人看的展示字符串，`gaps.collect` 已经
    把同一个谓词写成人话塞进它自己那条 note 里——但这里不能从 note
    正则反解同一个事实：`assume._match_excerpt` 上面记着这类下游代码
    从 `note` 里正则反解结构化事实、note 措辞一变就悄悄失效的实跑事故。
    `host_check_degraded` 这个布尔字段正是为了让这次聚合不必重蹈覆辙
    才加的（见该字段自己的注释）。
    """
    n = sum(1 for fd in factor_data.values() if fd.host_check_degraded)
    if not n:
        return []
    # **这个前缀是 `research._PREFIX` 的一个键，不是装饰。** 此前整句以
    # 数字起头，任何字面前缀都匹配不上，`research._mark` 落到默认分支，
    # 把一条降级提示印成「– 未生成：3 个因子采集时…」——而这些因子明明
    # 采到了。前缀与 `gaps.collect` 的真缺口条目「因子数据采集（…失败）」
    # 刻意分开（后者是整体没采到，该落「未生成」）；冒号之后的正文与
    # `_openweb_notes` 的「N 篇入库时主机安全校验降级（…）——…」同一份
    # 措辞，只换计数单位。
    return [f"因子外部资料采集：{n} 个因子采集时主机安全校验降级（本机 DNS "
           "被代理劫持，IP 层校验无从进行）——内网地址拦截本轮依赖代理侧"]


def _restore_openweb(r, out: Path) -> list[str]:
    """第二阶段：读回第一阶段落盘的开放网络文档。**不重新采集。**

    落盘文件缺失或损坏时照常继续，只记清单 —— 重跑 discover 会引入第一
    阶段没有的文档，那正是这套落盘机制要防的事。
    """
    from ir_agent.sources import openweb
    from ir_agent.sources.external import Corpus

    path = out / openweb.OPENWEB_JSON
    if not path.exists():
        return ["开放网络资料未跨阶段存活（未找到 "
                f"{openweb.OPENWEB_JSON}），外部引文可能核验不过"]
    if r.corpus is None:
        r.corpus = Corpus(as_of=r.as_of)
    res = openweb.load(path, r.corpus)
    if not res.docs:
        # **零文档有两种截然不同的成因，不能用同一句话交代。**
        #
        #   (a) 第一阶段 gather() 确实一篇都没采到——这不是本次读取丢了
        #       什么，语料库从一开始就是空的。
        #   (b) 落盘文件存在但已损坏/为空——openweb.load() 对结构性损坏
        #       的 JSON 会整体降级为空 `OpenWebResult()`，这时才是真的
        #       "跨阶段没存活"，本该在的文档丢了。
        #
        # 用 missing_slots 分辨两者：gather() 在零文档时，SLOTS 里每个
        # 固定槽位都没有命中任何候选，`missing_slots` 因此必然等于
        # `SLOTS` 全集（见 openweb.gather 末尾的
        # `res.missing_slots = [s for s in SLOTS if not per_slot.get(s)]`）。
        # 而 load() 对损坏文件的降级路径构造的是一个全新的
        # `OpenWebResult()`，其 `missing_slots` 用的是 dataclass 默认值
        # 空列表——不会凑巧等于 SLOTS 全集。因此
        # `set(missing_slots) == set(SLOTS)` 是「第一阶段真的什么都没
        # 采到」独有的信号，损坏文件产生不出这个信号。
        if set(res.missing_slots) == set(openweb.SLOTS):
            return ["开放网络资料：第一阶段确实一篇都没采到（并非本次读取"
                    "丢失），定性判断的外部依据仍只有券商研报一类来源"]
        return ["开放网络资料未跨阶段存活（落盘文件读不出文档，"
                "可能已损坏或为空），外部引文可能核验不过"]
    # **第一阶段记下的缺口在这里同样要报。** 落盘文件里
    # `missing_slots`/`fetch_failed` 都在，此前只挑了 `model_dated` 出来讲，
    # 其余两项读回后被丢掉——第二阶段的产物是 report.md，读者不会再回头
    # 去看第一阶段的终端输出。措辞与第一阶段同源，见 `_openweb_notes`。
    return _openweb_notes(res)


def _restore_announce(r, out: Path) -> list[str]:
    """第二阶段：读回第一阶段落盘的公司公告。**不重新采集。**

    与 `_restore_openweb` 同一条理由：公告的挑选要调模型，重跑会挑出
    与第一阶段不同的公告，第一阶段正文里的公告引文因此核验不过。公告
    又是 `Tier.COMPANY`——这套设计里唯一够格直接支撑因子取值的第一手
    披露——最好的证据不能是最不耐久的那一类。

    读回照样走时点守卫（`announce.load` 内部走 `corpus.add_if_visible`）。

    **零文档的成因这里只能分辨一部分，不能照抄 `_restore_openweb`。**
    那边靠 `set(missing_slots) == set(SLOTS)` 区分「第一阶段真的什么都
    没采到」与「文件损坏」，该信号之所以存在，是因为 `openweb.gather`
    在零文档时把 `missing_slots` 填成槽位全集，而损坏文件降级出来的
    `OpenWebResult()` 里它是空列表。公告没有槽位，`candidates` 在
    「第一阶段确实没有候选」与「文件损坏」两种情形下都是 0——那个信号
    不存在。于是分三种情形：

      (a) 文件不存在——没落过盘。
      (b) 有候选但没入库——可分辨：数字是第一阶段亲手记下的。
      (c) 候选为零——**分辨不出**，如实把两种可能都说出来。

    不去找一个替代信号，也不挑更可能的那一种猜：本项目的原则是
    「缺什么必须显式列出来」，一份声称了自己并不知道的成因的清单，
    比一句「不知道」更糟。
    """
    from ir_agent.sources import announce
    from ir_agent.sources.external import Corpus

    path = out / announce.ANNOUNCE_JSON
    if not path.exists():
        # 文件不存在也有不止一种成因：第一阶段采集抛了异常没走到落盘、
        # 落盘失败（第一阶段已有清单条目）、或这是本机制上线之前产出的
        # 阶段一产物。这里同样分辨不出，列出来而不是挑一种。
        return ["公司公告未跨阶段存活（未找到 "
                f"{announce.ANNOUNCE_JSON}——第一阶段可能采集未执行或落盘"
                "失败，也可能是本机制上线前产出的阶段一产物），"
                "公告引文可能核验不过"]
    if r.corpus is None:
        r.corpus = Corpus(as_of=r.as_of)
    res = announce.load(path, r.corpus)
    if res.docs:
        # 第二阶段的产物是 report.md，读者不会再回头去看第一阶段的终端
        # 输出——候选/挑选/取文失败这些计数要在这里再说一遍。
        # **前缀不能省**：`research._mark` 按前缀给标记，不认识的一律打成
        # 「– 未生成：」——而这条是成功读回。键见 `research._PREFIX`。
        #
        # **子行的缩进与符号也要处理。** `announce.summary` 的多行输出里
        # 子行是 `  – …`（`–` 在本清单里就是「未生成」的符号），而
        # `research.summary` 只给条目的第一行加前缀：不处理的话，读回成功
        # 的子细节（取文失败、被忽略的挑选）会以 `  – ` 起头、缩进还比
        # 父行浅，读起来像一条条顶层的「未生成」。这里把子行整体再缩进
        # 一层（落在父行之下）、把 `–` 换成 `·`。`announce.summary(res,
        # lead=…)` 才是不做字符串手术的做法，已明确推迟。
        head, *kids = announce.summary(res).removeprefix("公司公告 ") \
            .split("\n")
        kids = ["    " + re.sub(r"^(\s*)– ", r"\1· ", k) for k in kids]
        return ["公司公告读回：" + "\n".join([head, *kids])]
    if res.candidates:
        return [f"公司公告：一篇都没入库（候选 {res.candidates} 条，"
                f"按分类代码排除 {res.rejected} 条、入库 {res.picked} 条，"
                f"取文失败 {res.fetch_failed} 篇）"]
    return ["公司公告：读回 0 篇且候选数为 0——分辨不出是第一阶段确实没有候选"
            f"公告，还是落盘文件（{announce.ANNOUNCE_JSON}）已损坏或为空"
            "（损坏的文件整份降级为零候选，与前者读回来一模一样）；"
            "若是后者，公告引文可能核验不过"]


def _load_announce(code: str, r, as_of: date, out: Path,
                   md: dict) -> list[str]:
    """公司公告入语料库并落盘。返回要记进产物清单的条目。

    **没有 `client`，也没有 `factors`。** 挑选改为 `announce.is_substantive`
    的确定性标题分类：`factors` 此前来自方法论预研的分解式，而预研已挪到
    辩论① 之后 —— 那条依赖会让挑选的唯一判据恒为空。因子定向挪到消费端，
    由 `gaps._match_announce` 按因子做全文检索，那时因子已经知道了。

    **仍然不能放进 `_analyse_facts`。** 挑选确定性化之后，原本禁止它的
    理由（模型会在第二阶段重新挑、挑出与第一阶段不同的公告）已经消失；
    但搬进去会让第二阶段每轮重抓 PDF，而收益只是省掉一套正在正常工作的
    落盘读回机制（`_restore_announce`）。那是独立的清理，不混进这次重构。
    下面这段原有的说明保留，因为它记着那个洞当初是怎么出现的：

    这个失败在 Task 7.5 之前**无条件成立**——公告当时既不落盘也无读回，
    第二阶段的语料库里压根没有它们，不是什么假想的理由。是本函数的落盘
    加 `_restore_announce` 的读回才把它消除。消除的前提是第二阶段**只读回、
    不重采**，所以它仍然不能进 `_analyse_facts`：那样读回就形同虚设。
    根因与 `_load_openweb` 同一个：不可复现的产物不能放在会被重跑的位置。

    与 `_load_openweb` 不同的是**本函数不需要联网工具的 client**：
    模型只从代码取回的清单里挑下标，不上网。
    """
    from ir_agent.sources import announce
    from ir_agent.sources.external import Corpus

    if r.corpus is None:
        r.corpus = Corpus(as_of=as_of)
    try:
        res = announce.gather(code=code, company=_company_name(code),
                              as_of=as_of, corpus=r.corpus,
                              cache_dir=out / "pdf")
    except Exception as e:                                  # noqa: BLE001
        return [f"公司公告采集未执行（{e.__class__.__name__}: {str(e)[:120]}）"]

    skipped: list[str] = []
    try:
        announce.save(res, out / announce.ANNOUNCE_JSON)
    except Exception as e:                                  # noqa: BLE001
        # **落盘失败不能连带毁掉本轮研究，也不能只说「保存失败」**——
        # 理由与 `_load_openweb` 里同一处完全相同：`save()` 底层是
        # `Path.write_text`，输出目录被回收/磁盘写满/权限被收回都是运行
        # 环境的事，不是采集失败；而 `res.docs` 此刻已在 `r.corpus` 里，
        # 本轮简报仍能引用、核验也能通过，跨阶段却存活不了——
        # `_restore_announce` 读的正是这里本该写出的文件。简报里写下的
        # 公告引文到第二阶段会被自己的核验闸门拒掉，这个具体后果必须说出来。
        skipped.append(
            f"公司公告未能落盘（{e.__class__.__name__}: {str(e)[:120]}）"
            "——本阶段采集到的公告仍在本轮语料库中、可支撑当前简报，"
            "但不会跨阶段存活：第二阶段读不到这份文件，"
            "简报中写下的公告引文到时可能核验不过")

    md["公司公告"] = announce.summary(res)
    if not res.docs:
        return skipped + [
            f"公司公告：一篇都没入库（候选 {res.candidates} 条，"
            f"按分类代码排除 {res.rejected} 条、入库 {res.picked} 条，"
                f"取文失败 {res.fetch_failed} 篇）"]

    notes: list[str] = []
    if res.fetch_failed:
        notes.append(f"{res.fetch_failed} 篇取文失败")
        notes += [f"  - {x}" for x in res.fetch_failed_reasons]
    notes += [f"忽略了一条挑选：{x}" for x in res.ignored]
    if not notes:
        return skipped
    # 同上，与「公司公告：一篇都没入库…」分开：走到这里 res.docs 非空，
    # 公告是**采到了**的，notes 只是取文失败与被忽略挑选的注意事项。
    return skipped + ["公司公告采集：\n"
                      + "\n".join(f"  · {x}" for x in notes)]


def _annual_cache_dir() -> Path:
    """年报 PDF 的缓存目录。**`_load_sections` 与门① 清单共用这一处。**

    文件名由 `annual_pdf._download` 取网址末段。两处若各写一遍目录，门①
    清单列出的路径与实际缓存的路径会悄悄分开。
    """
    return Path("snapshots") / "pdf"


def _load_sections(r):
    """年报叙述章节 —— 战略与产业链分析的证据基础。"""
    from ir_agent.sources.annual_sections import extract
    from ir_agent.sources.annual_pdf import _download

    if not r.annual_url:
        return None, "年报叙述章节（未找到年报公告）"
    try:
        data = _download(r.annual_url, _annual_cache_dir())
        return extract(data, code=r.code), ""
    except Exception as e:                                  # noqa: BLE001
        return None, f"年报叙述章节（{e.__class__.__name__}: {e}）"


def _load_macro_view(r, as_of: date, client, macro_pr: str):
    """宏观定性: 政策取向与传导路径。补的是读数之外的那一半。"""
    from ir_agent import macro_view as MV

    if r.corpus is None or not r.corpus.docs:
        return "", "", "宏观定性（无外部宏观资料，政策取向缺文字依据）"
    try:
        view, _ = MV.assess(client, r.corpus, r.ledger, as_of, macro_pr)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"宏观定性（{e.__class__.__name__}: {str(e)[:120]}）"
    return view.summary(), view.summary(), ""


def _load_strategy(r, as_of: date, client, spot=None) -> tuple[str, str, str]:
    """战略分析: 护城河、五力、关键不确定性 → 情景驱动因子。"""
    from ir_agent import strategy

    sections, sec_err = _load_sections(r)
    if sections is None and r.corpus is None:
        return "", "", sec_err or "战略分析（无任何定性证据）"
    try:
        view, _ = strategy.assess(client, r.ledger, r.period, as_of,
                                  sections, spot, corpus=r.corpus)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"战略分析（{e.__class__.__name__}: {str(e)[:120]}）"
    r.sections = sections
    r.strategy_view = view          # 情景分析要用它识别出的关键不确定性
    return view.summary(), view.summary(), sec_err


def _load_value_chain(r, as_of: date, client) -> tuple[str, str, str]:
    """产业链与行业真实规模。"""
    from ir_agent import value_chain as VC

    sections = getattr(r, "sections", None)
    if r.corpus is None and sections is None:
        return "", "", "产业链分析（无任何资料）"
    try:
        view, _ = VC.analyse(client, r.corpus, r.ledger, r.period, as_of,
                             sections)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"产业链分析（{e.__class__.__name__}: {str(e)[:120]}）"
    if view.size:
        r.ledger.put(view.size.to_fact(
            r.corpus.docs[0].doc_id if r.corpus and r.corpus.docs else "ext",
            as_of))
    return view.summary(), view.summary(), ""


def _load_cycle(r, as_of: date) -> tuple[str, str, str]:
    """商业周期定位。必须在宏观与利率都入账之后调用。"""
    from ir_agent import cycle

    try:
        view = cycle.assess(r.ledger, as_of)
    except Exception as e:                                  # noqa: BLE001
        return "", "", f"周期定位（{e.__class__.__name__}: {e}）"
    if not view.scores:
        return "", "", "周期定位（无可用宏观信号）"
    for f in cycle.cycle_facts(view, r.period):
        r.ledger.put(f)
    r.cycle_view = view                       # 供行业传导取用
    return view.describe(), view.for_prompt(), ""


def _load_capm(code: str, r, as_of: date) -> tuple[str, str, str]:
    """无风险利率与 β 入账本。返回 (给人看的, 给模型看的, 失败说明)。

    这两个数原本被判定为「免费源拿不到，只能人工填」。前者是没找对接口
    （中债官网 404，但东财 RPTA_WEB_TREASURYYIELD 一直都在，且逐日）；
    后者根本不该抓 —— 自己回归算，窗口、基准、频率、复权才说得清楚，
    还能过 as_of 守卫。

    有了这两条，WACC 里只剩风险溢价是判断。
    """
    from ir_agent.beta import BetaUnavailable, beta_facts, estimate
    from ir_agent.sources.rates import RatesUnavailable, fetch_yields, risk_free

    human, prompt, errs = [], [], []

    try:
        ys = fetch_yields()
        for f in ys:
            r.ledger.put(f)
        rf = risk_free(ys, as_of)
        if rf is None:
            errs.append("无风险利率（该时点无可见收益率）")
        else:
            human.append("  " + rf.describe())
            # 期限用中文数字: 提示词里出现「10」而正文又无处引用它，
            # 等于诱导模型写裸数字再驳回它。
            prompt.append(f"  无风险利率（{_CN_TENOR.get(rf.tenor, '')}年期国债）"
                          f"　{rf.ref}")
    except RatesUnavailable as e:
        errs.append(f"无风险利率（{e}）")

    try:
        b = estimate(code, as_of)
        for f in beta_facts(b):
            r.ledger.put(f)
        human.append("  " + b.describe())
        per = b.as_of.isoformat()
        # 观测期数与窗口年数同样不能以裸数字出现在提示词里
        line = (f"  β（对沪深300，周频，两年窗口，前复权）"
                f"　[[beta@{per}]]　R²　[[beta.r_squared@{per}]]")
        if b.weak_fit:
            line += "\n    ⚠ R² 偏低，β 对本标的解释力有限，CAPM 适用性存疑，"\
                    "若据此定 WACC 必须在正文中说明"
        prompt.append(line)
    except BetaUnavailable as e:
        errs.append(f"β（{e}）")

    return ("\n".join(human), "\n".join(prompt), "；".join(errs))


def _load_industry(code: str, r, as_of: date) -> tuple[str, str, str]:
    """行业聚合入账本。返回 (给人看的摘要, 给模型看的口径, 失败说明)。

    逐家取同业营收要走十几到几十次网络调用，是全流程里最慢的一段。失败不
    阻断 —— 没有行业分析的报告仍然有价值，但必须在 skipped 里说清楚缺了什么，
    因为缺了它，增速假设就退回人工填写。
    """
    from ir_agent.industry_facts import aggregate, industry_facts
    from ir_agent.market import market_of
    from ir_agent.sources.eastmoney import fetch_statements_em
    from ir_agent.sources.industry import IndustryUnavailable, peer_set

    prior = _prior_period(r.period)
    if prior is None:
        return "", "", f"行业聚合（无法推出 {r.period} 的上年同期）"

    try:
        peers = peer_set(code)
    except IndustryUnavailable as e:
        return "", "", f"行业聚合（{e}）"

    cache: dict[str, dict] = {}

    def fetch(c: str, period: str, when: date):
        if c not in cache:
            facts, _ = fetch_statements_em(c, market=market_of(c))
            cache[c] = {(f.key, f.period): f for f in facts}
        f = cache[c].get(("revenue", period))
        return None if f is None or f.as_of > when else f.value

    print(f"行业聚合：{peers.classification.used_path}"
          f"（{len(peers.members) + 1} 家）…", file=sys.stderr)
    agg = aggregate(peers, r.period, prior, as_of, fetch)
    for f in industry_facts(agg):
        r.ledger.put(f)

    # ── 行业画像 ──────────────────────────────────────────
    # 逐家同业的完整报表在上面已经取过一次，cache 里躺着全部字段而只用了
    # revenue。这里复用同一批数据算集中度、生命周期与周期敏感度，
    # **不发起任何新的网络请求**。
    prof_md = prof_pr = ""
    try:
        prof_md, prof_pr = _industry_profile(peers, agg, cache, r, as_of)
    except Exception as e:                                  # noqa: BLE001
        prof_md = f"（行业画像未生成：{e.__class__.__name__}）"

    return (f"{peers.summary()}\n{agg.summary()}\n{prof_md}",
            f"{agg.for_prompt()}\n{prof_pr}".rstrip(), "")


def _latest_key(ledger, key: str, as_of: date) -> str:
    """该 key 在时点内可见的最新期间标签。"""
    from ir_agent.ledger import LookAheadError
    from ir_agent.periods import UnknownPeriod
    from ir_agent.periods import sort_key as pk

    best, bk = "", None
    for k, per in ledger._facts:                         # noqa: SLF001
        if k != key:
            continue
        try:
            ledger.get(k, per, as_of=as_of)
            s = pk(per)
        except (KeyError, LookAheadError, UnknownPeriod):
            continue
        if bk is None or s > bk:
            best, bk = per, s
    return best


def _fact_value(ledger, key: str, period: str, as_of: date):
    from ir_agent.ledger import LookAheadError
    try:
        return ledger.get(key, period, as_of=as_of).value
    except (KeyError, LookAheadError):
        return None


def _latest_annual_period(ledger, as_of: date) -> str | None:
    """最近一个完整年度的期间——**不跟着 `r.period` 走**。

    I4（终审复核）：`r.period = f"{year}{period_suffix}"`
    （`__main__.py` 的 `--period` 允许 H1/Q1-Q3/Q1），`--period H1`
    时 `r.period` 就是「2025H1」。加法链（毛利率）的基期必须是**最近
    一个完整年度**，与 `assume.apply_to`/`to_driver_block` 走的
    `_base_margin_of`（从 calibrate 的年度历史序列取）必须是同一个
    数——直接把 `r.period` 传给 `_fact_value`，会在 `--period H1` 下
    取到中报毛利率，与 apply_to/to_driver_block 用的年度值分叉，正是
    C3 注释里「必须是同一个数，而实际不是」的原话在另一个入口复现。
    """
    from ir_agent.operators.ratios import annual_periods
    periods = annual_periods(ledger, as_of, years=1)
    return periods[-1] if periods else None


def _industry_profile(peers, agg, cache, r, as_of: date) -> tuple[str, str]:
    """由已缓存的同业事实组装行业画像。"""
    from ir_agent import industry_profile as ip
    from ir_agent.ledger import FactLedger
    from ir_agent.operators import ratios
    from ir_agent.sources.eastmoney import CompanyType

    # cache 是 {code: {(key, period): Fact}}，转成账本以便算比率
    ledgers: dict[str, FactLedger] = {}
    for code, facts in cache.items():
        led = FactLedger()
        for f in facts.values():
            led.put(f)
        try:
            ratios.compute(led, r.period, as_of, CompanyType.GENERAL)
        except Exception:                                   # noqa: BLE001
            pass
        ledgers[code] = led

    # 行业营收的历史序列 —— 生命周期判据。同一组公司口径，
    # 否则成分变化会被误读成行业增长。
    history: dict[str, Decimal] = {}
    consistent = sorted(set(agg.revenues) & set(agg.prior))
    for per in ratios.annual_periods(r.ledger, as_of, years=6):
        vals = [v for c in consistent
                if (fs := cache.get(c)) and (f := fs.get(("revenue", per)))
                and f.as_of <= as_of and (v := f.value) is not None]
        if len(vals) == len(consistent) and vals:
            history[per] = sum(vals, Decimal(0))

    # 行业增速与名义 GDP 增速的历史序列 —— 弹性的输入。
    # 宏观事实在 run_full 里先于行业加载，此刻已在账本中。
    ind_yoy: dict[str, Decimal] = {}
    pers = sorted(history)
    for a, b in zip(pers, pers[1:]):
        if history[a]:
            ind_yoy[b] = history[b] / history[a] - 1
    gdp_yoy: dict[str, Decimal] = {}
    for per in ind_yoy:
        v = _fact_value(r.ledger, "macro.gdp_nominal_yoy", per, as_of)
        if v is not None:
            gdp_yoy[per] = v

    # 生命周期要拿行业增速与名义 GDP 增速比 —— 「快不快」必须有参照系。
    gdp_cagr = _fact_value(r.ledger, "macro.gdp_nominal_cagr",
                           _latest_key(r.ledger, "macro.gdp_nominal_cagr",
                                       as_of), as_of)
    # 行业整体利润率的历史 —— CFA 用它区分 growth（盈利改善）与 shakeout
    margins: dict[str, Decimal] = {}
    for per in history:
        num = den = Decimal(0)
        for c in consistent:
            fs = cache.get(c) or {}
            pf, rv = fs.get(("net_profit", per)), fs.get(("revenue", per))
            if pf and rv and pf.as_of <= as_of and rv.as_of <= as_of:
                num += pf.value
                den += rv.value
        if den:
            margins[per] = num / den

    prof = ip.build(
        industry=peers.classification.used_path, period=r.period, as_of=as_of,
        revenues=agg.revenues, ledgers=ledgers,
        revenue_history=history, cr5=agg.cr5,
        industry_yoy=ind_yoy, gdp_yoy=gdp_yoy,
        gdp_cagr=gdp_cagr, margin_history=margins)
    for f in ip.profile_facts(prof):
        r.ledger.put(f)

    # ── 传导 ──────────────────────────────────────────────
    # 课程的要求: 说清楚**行业结构将如何抑制或放大经济中的预期发展**。
    # 知道处于后周期还不够，还要知道这个行业对周期有多敏感 ——
    # 同样的宏观下行，DOL 9.5 的钢铁与 DOL 1.7 的白酒不是一回事。
    trans = ""
    view = getattr(r, "cycle_view", None)
    if view is not None:
        from ir_agent import cycle as cy

        sen = prof.sensitivity
        trans = cy.transmission(
            view, sen.total_leverage if sen else None,
            peers.classification.used_path,
            elasticity=sen.gdp_elasticity if sen else None)
    human = prof.summary() + (f"\n  传导　{trans}" if trans else "")
    model = prof.for_prompt() + (f"\n  宏观传导　{trans}" if trans else "")
    return human, model


def _load_comps(code: str, r, as_of: date, snapshot_dir) -> tuple[str, str, str]:
    """可比公司分析入账本。返回 (给人看的摘要, 给模型看的口径, 失败说明)。

    这是全流程最贵的一段: 每家可比公司都要跑一遍完整取数（财报 + 行情 +
    交叉验证）。因此先按市值就近挑出六家再取数 —— 对 18 家同业逐一跑完，
    多花的时间买不到任何结论，规模差两个数量级的 PE/PB 本来就不可比。
    """
    from ir_agent.comparables import NoComparables, select
    from ir_agent.comps import (
        InsufficientPeers,
        build_comps,
        comps_facts,
        fetch_row,
        peer_metrics_from_ledger,
    )
    from ir_agent.pipeline import run as run_pipeline
    from ir_agent.sources.industry import IndustryUnavailable, peer_set

    try:
        sel = select(peer_set(code))
    except (IndustryUnavailable, NoComparables) as e:
        return "", "", f"可比分析（{e}）"

    from ir_agent.sources.eastmoney import detect_company_type

    def fetch(c: str, period: str, when: date):
        pr = run_pipeline(c, year=int(period[:4]), as_of=when,
                          snapshot_dir=snapshot_dir,
                          period_suffix=period[4:] or "FY",
                          adjudicate_on_conflict=False)
        spot = next((f.period for f in (pr.quotes.facts if pr.quotes else [])
                     if f.key == "market_cap"), None)
        ct = detect_company_type(c, market_of(c))
        return {"name": c, "company_type": ct,
                "metrics": peer_metrics_from_ledger(
                    pr.ledger, period, when, spot, company_type=ct)}

    print(f"可比分析：{len(sel.chosen)} 家…", file=sys.stderr)
    rows, excluded = [], []
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    # 标的自身直接用主流程已有的账本，不重复取数
    from ir_agent.comps import CompsRow
    target_ct = detect_company_type(code, market_of(code))
    rows.append(CompsRow(code=code, name=code, period=r.period,
                         company_type=target_ct,
                         metrics=peer_metrics_from_ledger(
                             r.ledger, r.period, as_of, spot,
                             company_type=target_ct)))
    for peer in sel.chosen:
        row, exc = fetch_row(peer.code, r.period, as_of, fetch)
        (rows.append(row) if row else excluded.append(exc))

    try:
        table = build_comps(code, rows, r.period, as_of, excluded)
    except (InsufficientPeers, ValueError) as e:
        return "", "", f"可比分析（{e}）"
    comps_facts(table, r.ledger)
    return (f"{sel.summary()}\n{table.summary()}",
            f"{sel.for_prompt()}\n"
            + table.for_prompt(ledger=r.ledger, as_of=as_of, spot=spot), "")



# 挂在自己期间上（而非公司报告期）的事实前缀
_CONTEXT_PREFIXES = ("macro.", "rate.", "beta")

_CN_TENOR = {2: "两", 5: "五", 10: "十", 30: "三十"}


def _macro_periods(r) -> tuple[str, ...]:
    """宏观/利率/β 挂在自己的期间上（2026H1、2026-07、2026-09-04），
    不是公司报告期。

    不把这些期间放进目录，正文就引用不到它们 —— 而提示词里又要求写宏观
    一节、要求用 β 论证 WACC，模型只能写裸数字，然后被审计驳回。两边必须对齐。
    """
    from ir_agent.periods import UnknownPeriod
    from ir_agent.periods import sort_key as pk

    # **按 key 取各自最新一期，不是按期间取最新的几个。** 两者在有逐日
    # 序列时天差地别：`rates.fetch_yields()` 是逐日的，原来「最新 6 个
    # 期间」的写法会被最近六个交易日占满，月度的 2026-07（PMI/CPI/PPI/
    # M1/M2）与半年度的 2026H1（GDP）一个都进不去。
    #
    # 实测（生益科技 600183）：多空辩论一口气判了 4 项「无据断言」，
    # 理由是「清单中通篇检索，制造业 PMI…七个字段均不存在」——而这些
    # 事实就在账本里，摘要里也印着并进了提示词。摘要给模型看了数、
    # 清单里却引不到，模型只能照着摘要写占位符，然后被判无据。仓库自己
    # 的话：**「我往提示词里放了模型用不了的东西」**。
    #
    # 这个函数原本的文档写的就是「每个 key 只需最新一期」——意图一直是
    # 对的，是实现按期间去重把意图丢了。改回按 key 去重之后期间数天然
    # 很少（逐日利率全部收敛到同一个最新交易日），不需要再截断；留一个
    # 宽松上限只是防病理情况。
    newest: dict[str, tuple[str, tuple]] = {}
    for key, per in r.ledger._facts:                     # noqa: SLF001
        if not key.startswith(_CONTEXT_PREFIXES):
            continue
        try:
            k = pk(per)
        except UnknownPeriod:
            continue
        cur = newest.get(key)
        if cur is None or k > cur[1]:
            newest[key] = (per, k)
    out: dict[str, tuple] = {per: k for per, k in newest.values()}
    return tuple(sorted(out, key=lambda p: out[p], reverse=True)[:12])


def _industry_path(code: str) -> str:
    """申万行业分类路径。**确定性查表，与预研无关。**

    此前这个值从 `meth.industry` 取，于是「开放网络检索」隐式依赖了方法论
    预研先跑完。预研挪到辩论① 之后以后，那条依赖会让 `industry=` 恒为
    空串。它本来就不该经过模型 —— `_research_methodology` 自己也是这么
    拿的（`taxonomy.fetch_all()` 的 `me.path`）。
    """
    try:
        from ir_agent.sources.taxonomy import fetch_all
        rows = fetch_all()
    except Exception:                                       # noqa: BLE001
        return ""
    me = next((e for e in rows if e.code == code), None)
    return me.path if me is not None else ""


def _peer_names(code: str) -> tuple[str, ...]:
    """本标的与同业的公司名，用于豁免出处核验里的机构名误伤。

    分析中信证券这类券商时，正文本来就会反复出现「中信证券」——
    若因为语料库里没有中信证券的研报就判它捏造出处，那是把正当用法误伤。
    """
    try:
        from ir_agent.sources.taxonomy import fetch_all
        rows = fetch_all()
    except Exception:                                       # noqa: BLE001
        return ()
    me = next((e for e in rows if e.code == code), None)
    if me is None:
        return ()
    return tuple(e.name for e in rows if e.path == me.path)


def _build_evidence(r, code: str):
    """正文可引述的资料面 —— 年报原文 + 外部语料库。"""
    from ir_agent.attribution import Evidence
    from ir_agent.strategy import annual_text

    return Evidence(annual=annual_text(getattr(r, "sections", None)),
                    corpus=r.corpus, peers=_peer_names(code))


def _debate_and_revise(cat, body: str, client, period: str):
    """辩论 → 回写。返回 (正文, 成立挑战数, 辩论记录, 失败说明)。

    **两个阶段共用这一份。** 第一阶段辩论简报、第二阶段辩论完整研报，而
    「这一步失败不该毙掉已经写好的正文」这条保证两处都要有 —— 同一条道理
    写两遍，迟早只有一处被修。本会话已经三次撞上这种缝（规范化的三份副本、
    两处 except 的元组、辩论三方的指令），不再添第四处。
    """
    print("多空辩论…", file=sys.stderr)
    try:
        d = run_debate(cat, draft=body, client=client, period=period)
    except (DebateRefused, ClaudeCliError) as e:
        # **辩论失败不该毙掉整篇报告。** 挑战者要算账才能质疑（投资现金流
        # 与资本开支的差额、占比），而账本里没有「差额」这条事实 —— 禁止
        # 派生算术等于禁止它提出有力的质疑。
        #
        # 而且正文有独立的验收审计，辩论里的裸数字进不了正文: 修订稿仍要过
        # _violations。因此这一步失败时保留正文并说明辩论未完成，比连正文
        # 一起丢掉诚实得多。
        #
        # **ClaudeCliError 也接在这里**（生益科技第五轮踩到）: 辩论调用断流
        # （`Response stalled mid-stream`）时，这条异常原先从这个 except 旁边
        # 绕过去，落到调用方最外层那个 `except (AnalystRefused,
        # ClaudeCliError)` 上，把已经写好、已经付过钱、已经过完审计的正文
        # 一起丢掉。上面那条道理对传输故障**更**成立: DebateRefused 至少说明
        # 挑战者确实写了东西而它不合规，断流什么都没说明。调用方最外层那个
        # except 要留着 —— 撰写本身断流时手里根本没有正文可保。
        print(f"辩论未完成，保留正文：{str(e)[:80]}", file=sys.stderr)
        return body, 0, "", (
            f"多空辩论未完成（{str(e)[:160]}）—— "
            "正文**未经证伪环节**，其结论未被交叉质疑过，阅读时请留意")

    note = ""
    if d.upheld_count:
        print(f"回写 {d.upheld_count} 项成立的挑战…", file=sys.stderr)
        try:
            body, _ = revise_from_verdicts(cat, body, d.challenges, client)
        except (RevisionFailed, ClaudeCliError) as e:
            # **回写失败不该毙掉整篇报告。** 正文已通过全部闸门、辩论也已
            # 留痕，只是某条挑战没能落实到文字上。丢掉全部产出，读者既拿
            # 不到分析、也看不到那条挑战 —— 反而更不透明。
            #
            # 保留未修订稿并**如实标注哪条没落实**: 辩论记录里有完整的质疑
            # 与裁决，读者可以自己判断。这与「缺什么必须显式说出来」是同一
            # 条原则。
            #
            # ClaudeCliError 同样接在这里，理由与上面辩论那处一样 —— 回写
            # 调用断流不该让前两步的产出一起归零。
            note = (f"辩论回写未完成（{e}）—— "
                    "正文为**未修订稿**，上述挑战未落实到文字，"
                    "阅读时请对照辩论记录自行判断")
            print(f"回写失败，保留未修订稿：{str(e)[:80]}", file=sys.stderr)

    return body, d.upheld_count, d.summary(), note


# 两阶段的产物名。**简报与完整研报是两个文件，不是同一个文件的两稿** ——
# 建模之前不该存在一份读起来像结论的完整研报。
BRIEF = "brief.md"
REPORT = "report.md"
# 第二阶段完整研报的辩论记录。第一阶段的成稿辩论已删除（spec「要改的
# 既有不变量」第 2 条：简报改由门② 人工审阅），它原先的 `debate.md`
# 随之不再产出 —— 但旧目录里可能还留着，所以这里仍不用那个名字。
DEBATE_REPORT = "debate_report.md"
# ASSUMPTIONS_MD 定义在 research.py 并从那里导入——`next_actions` 要按
# 「判断项文件在不在」决定教人重跑哪一阶段，两处各写一遍同一个文件名
# 早晚分叉（本仓库已经在占位符期间上踩过这种分叉）。research 不能反向
# 导入 full（full 依赖 research），所以单一定义放在被依赖的那一侧。
ASSUMPTIONS_JSON = "assumptions.json"
# 判断项审核（子项目三）：最后一轮逐条结论、是否通过与取值指纹。
ASSUMPTIONS_REVIEW_JSON = "assumptions_review.json"
# 判断项那一步的超时。比默认的 900s 更长 —— 它是全链路输出最长的调用，
# 而输出长度不受提示词控制。配合 `retry_timeout=True`（见下方调用处）。
ASSUME_TIMEOUT = 1500

FAILED_RAW = "assumptions_failed_raw.txt"
# 撰写被驳回时最后一稿的落盘名。两个阶段各一个 —— 同一个目录，同名就会
# 把另一阶段的证据冲掉（与 debate.md / debate_report.md 同一处理）。
DRIVERS_FAILED_RAW = "drivers_failed_raw.md"
# 方法论审核（子项目二）：最后一轮逐项结论与指纹；每一轮的完整记录。
DRIVERS_REVIEW_JSON = "drivers_review.json"
DRIVERS_ROUNDS_MD = "drivers_rounds.md"
UNREVIEWED_BANNER = "> ⚠ 本分解式人工修改后未经审核（与审核时的分解式不一致，或没有审核记录）。"


class MethodologyStop(Exception):
    """方法论预研停下交人工（子项目二决定 2）：审核 3 轮未通过、审核者答不完整、
    模型接口失败。**不再退回两标量模式** —— 那条路让因子采集、辩论② 的逐因子
    取值整套悄悄消失。"""
WRITE_FAILED_BRIEF = "brief_failed_raw.md"
WRITE_FAILED_REPORT = "report_failed_raw.md"


def _dump_refused(out, raw: str, name: str) -> str:
    """把被驳回的最后一稿落盘，返回路径（没稿子就返回空串）。

    **不落盘的诊断信息等于没诊断。** 撰写是整条链上最贵的一次调用，而
    `AnalystRefused` 原先只带一句问题清单: 贵州茅台第一阶段重跑连续三次被判
    「正文没有引用任何事实占位符」，烧掉三次调用之后无从判断模型写了什么。
    assume 环节早就这么做了（assumptions_failed_raw.txt），这里是把同一条
    教训推广过来。
    """
    if not raw or not raw.strip():
        return ""
    path = Path(out) / name
    path.write_text(raw, encoding="utf-8")
    return str(path)
STATE = "state.json"
# 方法论预研（Task 4）的产出 —— 分解式落盘为独立文件，而不是塞进
# brief.md 的某一段：它要在数据采集之前就能被人看到、改坏了可以直接
# 改这个文件覆盖重跑，不必等简报写完。
DRIVERS_MD = "drivers.md"
# 第一阶段拆成三次调用之后新增的产物（Task 10）。门① 的来源清单、两轮
# 多空辩论、门② 的待核清单。
SOURCES_MD = "sources.md"
SOURCES_JSON = "sources.json"
STANCE_A = "stance_a.json"
STANCE_B = "stance_b.json"
REVIEW_MD = "review.md"
FACTORS_JSON = "factors.json"
# 多空辩论① 的五个驱动主题。预研的覆盖标注逐个核对它们 —— 同一份清单
# 两处用（辩论① 的 subjects、覆盖标注的 themes），不各写一遍。
STANCE_THEMES = ("需求", "价格", "成本", "份额", "政策")


def _analyse_facts(code: str, r, as_of: date, snapshot_dir):
    """确定性分析层：宏观 / 折现率 / 周期 / 行业 / 可比 / 外部资料。

    **不调模型，因此两个阶段都要跑。** 第二阶段跑它不是为了摘要（摘要从
    state.json 读回），是为了**把派生事实重新放回账本** —— industry.*、
    macro.*、comps.* 这些 key 只存在于内存账本里，不重建的话，喂给撰写的
    摘要里那些 [[industry.xxx]] 占位符一个都解析不了，模型照抄就被判伪造。

    实测踩到: 第二阶段写完整研报时连挂三次，
    「账本中没有 industry.revenue_yoy@2025FY，但正文引用了它」。
    这是「我往提示词里放了模型用不了的东西」的又一次。
    """
    skipped: list[str] = []
    md: dict[str, str] = {}
    pr: dict[str, str] = {}

    def take(name, fn):
        try:
            a, b, err = fn()
        except Exception as e:                              # noqa: BLE001
            md[name] = pr[name] = ""
            skipped.append(f"{name}（{e.__class__.__name__}: {str(e)[:120]}）")
            return
        md[name], pr[name] = a, b
        if err:
            skipped.append(err)

    # 顺序有讲究: 宏观 → 利率 → 周期（政策维度用期限利差）→ 行业 → 可比；
    # 外部资料必须在战略与产业链之前，它们的定性判断要引用其中的原文。
    take("宏观", lambda: _load_macro(r, as_of))
    take("折现率输入", lambda: _load_capm(code, r, as_of))
    take("商业周期", lambda: _load_cycle(r, as_of))
    take("行业", lambda: _load_industry(code, r, as_of))
    take("可比", lambda: _load_comps(code, r, as_of, snapshot_dir))
    take("外部资料", lambda: _load_external(code, r, as_of))
    return md, pr, skipped


def _analyse_qualitative(code: str, r, as_of: date, model,
                         md: dict, pr: dict, skipped: list[str]):
    """需要模型的定性层（宏观定性 / 战略 / 产业链）。**就地**写入调用方的
    md/pr/skipped，只返回本层用的 client —— 开销要累计到总开销里。

    独立于 `_analyse_facts` 拆开（此前两者合在一个 `_analyse` 里）：确定性层
    两阶段都要重跑，定性层要调模型、只在第一阶段之一（`run_analyse`）跑一次，
    结果落进 state.json 供后两次调用读回。
    """
    def take(name, fn):
        try:
            a, b, err = fn()
        except Exception as e:                              # noqa: BLE001
            md[name] = pr[name] = ""
            skipped.append(f"{name}（{e.__class__.__name__}: {str(e)[:120]}）")
            return
        md[name], pr[name] = a, b
        if err:
            skipped.append(err)

    qual_client = make_client(model=model)
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    take("宏观定性", lambda: _load_macro_view(r, as_of, qual_client, pr["宏观"]))
    take("战略", lambda: _load_strategy(r, as_of, qual_client, spot))
    take("产业链", lambda: _load_value_chain(r, as_of, qual_client))
    return qual_client


def _restore_state(out: Path) -> tuple[dict, dict]:
    """读回上一次调用落盘的定性分析结果（`state.json` 的 `md` / `pr`）。

    与 `run_valuation` 读 `state.json` 的那两行同源 —— 第一阶段拆成三次
    调用之后有三处要读它，形状必须是同一个：缺文件时给空壳而不是抛错，
    因为「上一阶段没留下 md」与「state.json 不存在」对调用方是同一件事。
    `or {}` 不能省：人手改过的 state.json 里 `"md": null` 是合法 JSON，
    `.get("md")` 会返回 None，下游 `md.items()` 当场崩。
    """
    f = out / STATE
    state = json.loads(f.read_text(encoding="utf-8")) if f.exists() \
        else {"md": {}, "pr": {}}
    return state.get("md") or {}, state.get("pr") or {}


def _save_state(out: Path, code: str, r, as_of: date, md: dict, pr: dict,
                *, upheld: int | None = None, **signoff: str) -> None:
    """落盘 `state.json`。三次调用都写它，每次把自己那道门的签名补进去。

    **先读旧的再 update，不能整份覆盖。** `upheld` 只有跑过辩论的那次调用
    算得出来；若无条件按默认值写入，后一次调用会把它清成 0 —— 读回时
    「这轮确实是 0」和「这轮没算」长得一样。这正是本项目反复踩的那条缝：
    产物上两种情形不可区分，人就看不见。
    """
    f = out / STATE
    old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    old.update({"code": code, "period": r.period, "as_of": as_of.isoformat(),
                "md": md, "pr": pr})
    if upheld is not None:
        old["upheld"] = upheld
    old.update({k: v for k, v in signoff.items() if v})
    f.write_text(json.dumps(old, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _restore_corpus(r, out: Path, md: dict, *, sections: bool) -> list[str]:
    """第二、三次调用：读回第一次调用落盘的语料库（开放网络、公告）与年报
    分节。**不重新采集。**

    **第一版漏了这一步。** `_restore_openweb` / `_restore_announce` 只在第二
    阶段 `run_valuation` 里调，第二、三次调用里的语料库于是只剩确定性层重新
    抓的东财研报 —— 生益科技试跑时那 51 篇公告、17 篇网页、年报正文都不在：
    两轮辩论看不到它们，预研的命中次数几乎全是 0，因子采集搜不到公告。

    读回后按实际篇数重渲染 `md["外部资料"]`。两个读回函数在「文件在而读回
    0 篇」时各自已有措辞说出来，**必须 `skipped +=`**（裸调用会把那些措辞
    丢掉）。年报分节只在有消费方时读（第二次调用：辩论与撰写的核验面、
    命中次数、因子采集的年报一级）。

    第二阶段 `run_valuation` 里有同形的三行 —— 那一段按「第二阶段一行不改」
    保持原样，没有改成调用本函数。
    """
    skipped = _restore_openweb(r, out)
    skipped += _restore_announce(r, out)
    if sections:
        secs, err = _load_sections(r)
        if secs is not None:
            r.sections = secs
        elif err:
            skipped.append(err)
    _refresh_external_md(r, md)
    return skipped


def _read_factors(out: Path, meth) -> tuple[dict | None, list[str]]:
    """读回第二次调用落盘的因子采集结果。没有分解式（两标量模式）时返回 None。"""
    from ir_agent import gaps

    if meth is None:
        return None, []
    f = out / FACTORS_JSON
    if not f.exists():
        return None, [f"因子采集结果未落盘（{f} 不存在）—— 判断项按「未采集」处理："
                      "「无数据（估计）」对所有因子开放、采集到的网络发现不能"
                      "作依据。重跑 --brief 可补"]
    try:
        data, notes = gaps.from_json(json.loads(f.read_text(encoding="utf-8")),
                                     meth.model)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, [f"因子采集结果读不回（{e.__class__.__name__}: "
                      f"{str(e)[:120]}）—— 判断项按「未采集」处理"]
    return data, notes


def _read_drivers(out: Path):
    """只读 `drivers.md`，读不回就说为什么。**不联网。**

    这段逻辑原本内联在 `_research_methodology` 开头：文件在就读回、不在就
    往下走去联网预研。`run_assume` 需要的是**只有读回那一半** —— 它绝不能
    在 drivers.md 缺失时悄悄重新联网预研一个新分解式：那样人在门② 审过的
    简报与接下来要填的判断项，依据的是两个不同的分解式，而产物上看不出来。

    返回 `(分解式或 None, 说明)`。文件不存在时返回 `(None, "")` —— 调用方
    据此区分「不存在」（`_research_methodology` 往下走去预研）与「存在但读
    不回」（停下，不覆盖人的修改）。
    """
    from ir_agent import methodology

    path = out / DRIVERS_MD
    if not path.exists():
        return None, ""
    try:
        return methodology.from_markdown(path.read_text(encoding="utf-8")), ""
    except methodology.MethodologyFailed as e:
        return None, (f"方法论预研（{path} 已存在但读不回：{str(e)[:160]}）"
                      "—— 未重新联网预研（避免悄悄覆盖你的修改），"
                      "退回不分解模式，判断项仍按营收增速/毛利率两项填写")


def _collect_factors(r, as_of: date, client, md: dict, decomps,
                     industry: str, skipped: list[str]) -> dict:
    """按分解式定向采集因子数据。整段从 `run_analysis` 原样搬出。

    **`_refresh_external_md` 必须留在 `finally` 里，不能紧跟在 try 块成功
    路径的末尾。** `gaps.collect` 按分解出的因子逐个循环，④外部网络分支
    命中时会在循环内部调 `corpus.add_if_visible` 往 `r.corpus` 里加文档 ——
    这发生在循环体里，早于整个函数 return 或 raise。真实故障形状：第 N 个
    因子已经把文档塞进 `r.corpus`，第 N+1 个因子采集时才报错，`gaps.collect`
    整体以异常退出。此前把 `_refresh_external_md(r, md)` 放在 try 块内、
    `md["因子数据"] = ...` 之后，异常一抛，这行跟着被跳过，第 N 个因子已经
    写进语料库的文档，`md["外部资料"]` 从未被告知过，读者看到的篇数与分级
    构成停在这次采集之前那一刻。`finally` 保证不论成功、部分成功后抛异常、
    还是从一开始就抛异常，都会重渲染一次。
    """
    from ir_agent import gaps
    from ir_agent.sources.external import Corpus

    factor_data: dict = {}
    try:
        factor_data = gaps.collect(
            client, decomps, r.ledger, r.corpus or Corpus(as_of=as_of),
            industry, as_of, sections=getattr(r, "sections", None),
            annual_url=getattr(r, "annual_url", ""))
        md["因子数据"] = gaps.summary(factor_data)
    except Exception as e:                                  # noqa: BLE001
        skipped.append(f"因子数据采集（{e.__class__.__name__}: {e}）")
    finally:
        _refresh_external_md(r, md)
    # 轮次级主机安全校验降级聚合。不必放进上面的 `finally`：这里读的是已经
    # 算好的 `factor_data`。**异常路径上确有欠计数，且这一层治不好** ——
    # `gaps.collect` 跑到第 N 个因子才抛异常时，前 N-1 个因子里任何一次
    # `openweb.verify_finding` 记下的降级，都只活在那个从未 return 出来、
    # 被异常丢弃的局部字典里；`factor_data` 在这条路径上是空字典，
    # `_factor_host_check_note` 对空字典自然返回空列表，但那不是「没有漏报」，
    # 是「漏了也报不出来」。详见 `_factor_host_check_note` 自己的文档字符串。
    skipped += _factor_host_check_note(factor_data)
    return factor_data


def _factor_ctx(factor_data: dict | None) -> str:
    """把因子采集结果渲染进辩论②的提示词。

    **采集失败时必须显式说出来**，不能给一个空段落 —— 见
    `test_因子采集失败时上下文显式说出来而不是留空段落`。
    """
    if not factor_data:
        return ("（本轮因子数据采集未成功；以下取值只能基于上面的定量事实"
                "与语料库，双方都应据此降低置信度）")
    from ir_agent import gaps
    return gaps.summary(factor_data)


def _stop_for_manual(code: str, r, as_of: date, out: Path, arts: dict,
                     skipped: list[str], client, *,
                     round_: str) -> ResearchOutcome:
    """多空环节失败时停下，给出两条可粘贴的命令。

    **没有「跳过」这条路。** 每份研报的多空输入要么来自模型、要么来自人。
    已知代价：某家公司反复失败时流程会卡住，直到有人手写那份 JSON。

    年度取自 `r.period` 而不是 `as_of.year`：as_of 是今天，报告期是上一个
    年度，拼错了这条命令就把人引到一个空账本上。
    """
    low = round_.lower()
    cmd = (f"python -m ir_agent {code} --year {r.period[:4]} "
           f"--brief {out} --verified-by <名字>")
    print(f"\n多空辩论{round_} 未完成。最后一稿与待填模板已落盘：\n"
          f"  {out}/stance_{low}_failed_raw.md\n"
          f"  {out}/stance_{low}.template.json\n\n"
          f"  ① 原样重试（模型重抽）\n     {cmd}\n\n"
          f"  ② 人工补写：按模板填好 {out}/stance_{low}.json，然后\n"
          f"     {cmd} --accept-manual\n", file=sys.stderr)
    skipped.append(f"多空辩论{round_} 未完成 —— 需重跑或人工补写")
    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=Stage.INCOMPLETE,
        artifacts=arts, skipped=skipped,
        cost_usd=Decimal(str(client.total_cost_usd)), phase="brief")


def _methodology_failed(out: Path, e) -> tuple[None, str]:
    """预研给不出闭合的分解式 —— 退回不分解模式，整轮继续。

    从 `_research_methodology` 的 `except methodology.MethodologyFailed`
    分支整段搬出。**下面那段关于存活者偏差的注释逐字带走，一个字都没删** ——
    它记着牧原股份那次实跑事故的根因。
    """
    # **被驳回的分解式必须落盘。** 不落盘的后果不是"排查麻烦"，是**存活者
    # 偏差**: 事后拿历史产物做离线复盘时，只能看到通过了闸门的样本。实测
    # （牧原股份 002714）: 「公司出栏量占全国比重」被拒 3 次、drivers.md
    # 根本没生成，于是用 10 份存档做消融时，两个词法修复在 56 个存活因子
    # 上的效果是 0 个改变 —— 真正的拦截与漏报全在看不见的那一半。
    dumped = _dump_refused(out, getattr(e, "raw", ""), DRIVERS_FAILED_RAW)
    tail = f"（最后一稿已落盘：{dumped}）" if dumped else ""
    return None, (f"方法论预研（{str(e)[:160]}）{tail}—— "
                  "退回不分解模式，判断项仍按营收增速/毛利率两项填写")


def _methodology_api_failed(e) -> tuple[None, str]:
    """模型接口失败（额度用尽/超时/CLI 报错）—— 不是这个行业分解不了。

    从 `except ClaudeCliError` 分支整段搬出，注释同样逐字带走。
    """
    # **与 5db4ca5 修过的是同一个洞，只是换了个调用点。** 实测
    # （600584 --analyse，确定性层已跑完 2930 条事实、三表与跨源勾稽
    # 全部通过）第一个联网调用就撞上「You've hit your session limit」，
    # 而这里当时只捕 MethodologyFailed，于是 ClaudeCliError 一路冒到
    # __main__，整轮退出码 1、产物一个都没写出来。run_analysis 里其他
    # 每一个联网环节都已经把这类错误记进 skipped 后继续，唯独预研这一
    # 处漏了。**能力边界不是故障，整轮研究不该因此停摆。**
    #
    # 降级理由要说清这是**接口失败**而不是「这个行业分解不了」：
    # 两者都退回不分解模式，但前者重跑就能拿回因子分解，后者重跑
    # 多少次都一样。降级到两标量是这条产品线最不该悄悄发生的事，
    # 起码要让人一眼看出这次降级是可恢复的。
    return None, (f"方法论预研（{e.__class__.__name__}: {str(e)[:160]}）"
                  "—— 模型接口失败（额度用尽/超时/CLI 报错），不是这个"
                  # 预研已挪进第二次调用，该重跑的是 --brief 而不是
                  # --analyse。**过时的指引是会主动误导人的那种错。**
                  "行业分解不了：本轮退回不分解模式，判断项按营收增速/"
                  "毛利率两项填写；接口恢复后重跑 --brief 即可拿回"
                  "因子分解")


def _ledger_keys(r) -> tuple[str, ...]:
    """账本里已有的 key（去重、排序）。让预研优先分解成有数据的因子。

    **拿不到就返回空元组，不抛。** 与紧邻的 `_facts_digest` 一条理由：
    这两个值都是预研提示词的**可选**输入，缺了只是退回行业级预研 ——
    而 `_research_methodology` 整体的设计就是「降级但继续」（预研失败不
    中断整轮研究）。为一个可选输入炸掉整轮，与那条设计相反。
    """
    led = getattr(r, "ledger", None)
    keys = {k for k, _ in getattr(led, "_facts", {})}        # noqa: SLF001
    return tuple(sorted(keys))


def _facts_digest(r) -> str:
    """喂给预研的事实摘要。**复用撰写方那份清单，不另造一份口径。**

    另造一份的后果是预研看到的事实与简报能引用的事实不是同一批 —— 预研
    据以设计分解式的某个数字，撰写时引用不了。
    """
    try:
        cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=r.as_of,
                                      extra_periods=_catalog_periods(r))
        return cat.render_for_prompt()
    except Exception:                                       # noqa: BLE001
        return ""


def _research_methodology(code: str, r, out: Path, client, *, judge,
                          md: dict | None = None, stance_note: str = ""):
    """方法论预研 —— **就多空争执的这几点，这家公司该怎么分解**。

    **它现在排在辩论① 之后，手里有全部证据与多空结论。** 旧位置排在所有
    采集之前，所以只能问一个行业级的通用问题（「这个行业靠什么赚钱」），
    产出是「这个行业怎么运转」而不是「这家公司此刻的争议在哪」。

    `md` 是定性分析的结果（`state.json` 的 `md`），`stance_note` 是辩论①
    的平衡结论。**`stance_note` 是「辩论① 排在预研之前」唯一的兑现处** ——
    不传它，辩论① 的结论就到不了预研，流程图上它在那儿而数据流上它不在。

    两者都可选：不传就退回行业级预研，那是两标量降级路径要走的出口。

    **不另设人工确认门。** 门的价值会被数量稀释: 现有那道判断项的门之所以
    有效，是因为它一次只要求人看一件事；再加一道，人看两次的注意力未必比
    看一次多。改用「立刻落盘 + 可中断 + 可覆盖」换取早期干预 —— 落盘为
    drivers.md，看不对就改这个文件后重跑第一阶段。

    **I2：这个「可覆盖」的承诺需要一条读回路径才成立。** drivers.md 若
    已经存在（无论是不是被人改过），说明这不是第一次跑第一阶段——直接
    读回它，不再重新联网预研，否则重跑会用新的预研结果把人的修改悄悄
    覆盖掉。读不懂就**明确报错**：这里不吞掉异常改去调用 `research()`
    静默重来一遍，那样人会以为自己的修改生效了，实际却被整个丢弃。

    **预研失败（MethodologyFailed）不中断整轮研究。** 联网拿不到闭合的
    分解式是常态而非故障（比如模型答非所问、行业太冷门搜不到方法论），
    此时退回不分解模式，判断项仍按营收增速/毛利率两项填写——降级但继续，
    损失的是精度，不是担保。
    """
    from ir_agent import methodology

    path = out / DRIVERS_MD
    from ir_agent import methodology_review as MR

    m, derr = _read_drivers(out)
    if m is not None:
        # 重跑读回：指纹一致沿用审核结论；不一致或没有审核记录 = 人改过，不送审，
        # 只标注（子项目二决定 8）。
        return m, _unreviewed_note(out, m)
    if derr:
        return m, derr

    industry = _industry_path(code)
    if not industry:
        return None, "方法论预研（取不到申万行业分类）"

    themes = list(STANCE_THEMES) if stance_note.strip() else []
    prompt = methodology.build_prompt(
        industry, code,
        facts=_facts_digest(r),
        corpus_note=(md or {}).get("外部资料", ""),
        qualitative="\n\n".join(
            f"【{k}】\n{v}" for k, v in (md or {}).items()
            if k in ("宏观定性", "战略", "产业链") and v),
        stance_note=stance_note,
        available_keys=_ledger_keys(r))
    try:
        # 提出者联网（`client`）；审核者不联网（`judge`）。审核者看到的「与提出
        # 者相同的资料」就是提出者的提示词本身。
        m, review, rounds = MR.propose_and_review(
            client, judge, industry=industry, code=code, prompt=prompt,
            materials=prompt, themes=themes, ledger=getattr(r, "ledger", None),
            as_of=getattr(r, "as_of", None), sections=getattr(r, "sections", None),
            corpus=getattr(r, "corpus", None))
    except MR.ReviewStop as e:
        (out / DRIVERS_ROUNDS_MD).write_text(MR.rounds_markdown(e.rounds),
                                             encoding="utf-8")
        dumped = _dump_refused(out, e.rounds[-1].raw if e.rounds else "",
                               DRIVERS_FAILED_RAW)
        raise MethodologyStop(
            f"{e} —— 各轮记录：{out / DRIVERS_ROUNDS_MD}"
            + (f"；最后一稿：{dumped}" if dumped else "")) from e
    except ClaudeCliError as e:
        raise MethodologyStop(_methodology_api_failed(e)[1]) from e
    (out / DRIVERS_ROUNDS_MD).write_text(MR.rounds_markdown(rounds), encoding="utf-8")
    (out / DRIVERS_REVIEW_JSON).write_text(MR.dump_review(review), encoding="utf-8")

    # ── 两段标注（都不阻断）─────────────────────────────────────
    # **没有辩论① 的结论就不做覆盖标注。** spec 对 `theme_coverage` 写的是
    # 「`themes` 为空时返回空列表 —— 不能凭空要求覆盖」，那条逻辑在调用方
    # 同样成立：没传 `stance_note`（辩论① 失败、或这是降级路径）时，模型
    # 压根没看到过主题清单，拿五个主题去核对它、再在产物上标「4 个未覆盖」
    # 就是凭空问责。
    themes = list(STANCE_THEMES) if stance_note.strip() else []
    cov, cov_notes = methodology.coverage(m, themes)
    hits = methodology.term_hits(m.model,
                                 sections=getattr(r, "sections", None),
                                 corpus=getattr(r, "corpus", None))
    refs: dict = {}
    led, as_of = getattr(r, "ledger", None), getattr(r, "as_of", None)
    if led is not None and as_of is not None:
        from ir_agent import gaps
        for f in m.factors():
            have, miss = gaps.ledger_refs(f.source_hint, led, as_of)
            if have or miss:
                refs[f.label] = (have, miss)
    path.write_text(methodology.to_markdown(m, coverage=cov, hits=hits,
                                                coverage_notes=cov_notes,
                                                key_refs=refs, review=review),
                    encoding="utf-8")
    note = ""
    if (open_ := [c.theme for c in cov if c.mark != "✓"]):
        note = (f"方法论预研：{len(open_)} 个辩论主题没有声明由哪个因子承载"
                f"（{'、'.join(open_)}）—— 已标注，未阻断，需在门② 复核")
    return m, note


def _method_review(out: Path, m):
    """读回方法论审核结论。返回 `(ReviewResult 或 None, 是否未经审核)`。

    指纹 = 当前 drivers.md 分解式定义（经解析再输出，空白差异不算改动）的哈希；
    与 `drivers_review.json` 记录的一致 → 沿用；否则视为人改过。"""
    from ir_agent import methodology
    from ir_agent import methodology_review as MR

    if m is None:
        return None, False
    f = out / DRIVERS_REVIEW_JSON
    fp = MR.fingerprint(methodology._to_dsl(m))
    if f.exists():
        try:
            res = MR.ReviewResult.from_json(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            res = None
        if res is not None and res.fingerprint == fp:
            return res, False
    return None, True


def _unreviewed_note(out: Path, m) -> str:
    """人改过的 drivers.md：不送审，标注（drivers.md 顶部、终端清单）。"""
    _, unreviewed = _method_review(out, m)
    if not unreviewed:
        return ""
    path = out / DRIVERS_MD
    text = path.read_text(encoding="utf-8")
    if UNREVIEWED_BANNER not in text:
        path.write_text(UNREVIEWED_BANNER + "\n\n" + text, encoding="utf-8")
    return ("方法论预研：drivers.md 人工修改后未经审核（与审核时的分解式不一致，"
            "或没有审核记录）—— 人的判断优先，未送审，已标注")


def _method_review_lines(out: Path, m) -> list[str]:
    """门② 待核清单与门③ 判断项清单共用：方法论审核遗留的提醒与待采集。"""
    if m is None:
        return []
    res, unreviewed = _method_review(out, m)
    if unreviewed:
        return ["⚠ 分解式人工修改后未经审核"]
    return [f"- {v.line()}" for v in res.flagged()] if res else []


def _stop_for_methodology(code: str, r, as_of: date, out: Path, arts: dict,
                          skipped: list[str], client, why: str) -> ResearchOutcome:
    """方法论预研停下交人工。两条做法：重跑，或以最后一稿为底手写 drivers.md。"""
    cmd = (f"python -m ir_agent {code} --year {r.period[:4]} "
           f"--brief {out} --verified-by <名字>")
    print(f"\n方法论预研停下：{why}\n\n"
          f"  ① 原样重试（重新预研）\n     {cmd}\n\n"
          f"  ② 人工给出分解式：以 {out}/{DRIVERS_FAILED_RAW} 为底稿改好，存为 "
          f"{out}/{DRIVERS_MD}（各轮审核意见见 {out}/{DRIVERS_ROUNDS_MD}），再跑\n"
          f"     {cmd}\n     读回时会标注「人工修改后未经审核」。\n", file=sys.stderr)
    skipped.append(why)
    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=Stage.INCOMPLETE,
        artifacts=arts, skipped=skipped,
        cost_usd=Decimal(str(client.total_cost_usd)), phase="brief")


def _catalog_periods(r) -> tuple[str, ...]:
    """可引用清单要收进哪些期间（公司报告期之外）。**写作方与辩论裁判用同一份。**

    实测（生益科技两次第一阶段）：简报里一致预期 0 条。写作方的提示词
    （`corpus.consensus_prompt`）直接给出 `[[consensus.eps@2026FY]]` 这类占位符，
    正文校验走 `render()` 查账本，一致预期在账本里，引用能通过；辩论裁判拿的却是
    这份清单，而清单只收公司报告期与 `_macro_periods`，没有 2026FY 起的预测年。
    于是裁判判「均不在本轮可用事实清单内」、无据断言成立、回写时把一致预期的
    句子删掉——每次辩论成功就删光（长电辩论失败没回写，反而留下 14 条）。

    **同一个坑第二次（贵州茅台 600519 第二阶段）。** 当时只为 `consensus.*`
    修，没有推广。第二阶段把估值结果写进研报时，`dcf_res.summary()` 直接给出
    `[[valuation.value_per_share@2026-10-03]]` 这类占位符（期间是估值时点），
    而清单收不到它 —— 裁判判「六个占位符均系自行拼造」、无据断言成立、回写
    把估值数字全删掉，研报的估值一节变成「具体数值账本未披露，此处不作字面
    引用」。那句话是假的: `dcf.to_facts` 在撰写之前就把它们 put 进账本了。
    第一阶段不跑估值，所以这个坑一直等到辩论接进第二阶段才被踩出来。

    一般原则: **凡是提示词能递给模型的占位符，其期间都必须在裁判的清单里。**
    下面这张「提示词侧事实族」表每加一个摘要类提示词就要跟着加一条 ——
    漏一条的后果不是报错，是那一族数字在辩论成功后被静默删光。

    - 宏观/利率/β：每个 key 取最新一期（`_macro_periods`）
    - 一致预期：**全部**期间——提示词三个预测年都给了，只收最新一期等于只给
      裁判看一年
    - 估值：全部期间（实际只有一个估值时点）
    - 行情：市值所在的报价日
    """
    # 提示词侧事实族：由摘要类提示词（corpus.consensus_prompt、
    # dcf_res.summary()）直接递给模型的占位符，其期间清单里必须有。
    PROMPT_SIDE = ("consensus.", "valuation.")
    extra = sorted({per for key, per in r.ledger._facts          # noqa: SLF001
                    if key.startswith(PROMPT_SIDE)})
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    return _macro_periods(r) + tuple(extra) + ((spot,) if spot else ())


def _prompt_bundle(pr: dict) -> dict:
    """分析结论 → 撰写用的各段摘要。"""
    return dict(
        comps_summary=pr.get("可比", ""),
        macro_summary=(pr.get("宏观", "")
                       + ("\n\n" + pr["宏观定性"] if pr.get("宏观定性") else "")),
        industry_summary=pr.get("行业", ""),
        strategy_summary=pr.get("战略", ""),
        chain_summary=pr.get("产业链", ""),
        consensus_summary=pr.get("外部资料", ""))


# 判断项产出后登记的产物名——`_propose_assumptions` 写成功才登记，
# 因此它就是"判断项到底写出来没有"唯一可信的信号。
ASSUMPTIONS_ART = "DCF 判断项（待核实）"


def _review_stage(arts: dict):
    """按**实际产物**定状态与指引，不按预期路径。

    实测现场（600584 --analyse，18:08）：额度用尽 → 简报没写成 → 判断项
    根本没产出，目录里只有 dashboard/model/state 三个文件。而这里当时
    无条件 `stage = AWAITING_REVIEW`，于是终端照样打出「分析完成，DCF
    判断项待人工核实。核实 out/600584/assumptions.md」以及「下一步：
    1. 核实 …/assumptions.md　3. 确认后 --value」——那个文件不存在，
    第 3 步照做只会拿模板默认值（三档同值）去建模。

    `research.next_actions` 的文档自己写着「给一条跑不通的命令比不给
    指引更糟…过时的指引是会主动误导人的那种错」，指向一个不存在的文件
    同样如此。判断项没写出来时正确的状态是 `NOT_APPLICABLE`，它给的
    指引正是「重跑第一阶段即可」——额度用尽要的就是这句。

    与「占位符有没有被替换」「批准的数有没有进工作簿」同一类：
    **只按预期路径下结论，不查结果。**
    """
    if ASSUMPTIONS_ART in arts:
        return Stage.AWAITING_ASSUME_REVIEW, (
            "分析完成，DCF 判断项待人工核实。核实 "
            f"{arts[ASSUMPTIONS_ART]}（需改数值就改 {ASSUMPTIONS_JSON}），"
            "确认后运行第二阶段建模")
    return Stage.INCOMPLETE, (
        "本轮未产出 DCF 判断项（原因见上方未生成清单）——"
        "确定性层的产物（看板、工作簿、事实账本）仍然可用，"
        "但估值尚无从谈起")


def _unreviewed_assumptions(out: Path, a, edits: list[str]) -> str:
    """第二阶段读回的判断项是不是审核通过的那一份。空串 = 是。

    比对 `assumptions_review.json` 里审核时的取值指纹（只算因子、年、三档与基期
    值，不算理由文字）。没有审核记录、审核未通过（人以底稿补写）、指纹对不上
    （人改了数），都要标注 —— 不送审（子项目三决定 5）。"""
    from ir_agent import assume_review

    f = out / ASSUMPTIONS_REVIEW_JSON
    if not f.exists():
        return "没有判断项审核记录（未经 AI 审核，或审核停下后人工补写）"
    d = json.loads(f.read_text(encoding="utf-8"))
    if not d.get("passed"):
        return "判断项审核未通过，这份是人工以底稿补写的"
    if d.get("fingerprint") != assume_review.fingerprint(a):
        n = f"（改动 {len(edits)} 格，逐条见「判断项经人工调整」）" if edits else ""
        return f"判断项经人工修改，修改后未经审核{n}"
    return ""


def _assumption_caveats(a, anchor_note: str = "",
                        unreviewed: str = "") -> tuple[str, str]:
    """判断项的软处要跟着交付物走：(进提示词的一句话, 追加进 report.md 的附录)。

    两件事，分别说：

    - **依据未能逐字核验**：因子依据从「核验不过就驳回」改成「标注后
      放行」（用户明确决定的取舍）之后留下的代价。
    - **取值本身是估计**：模型自己在理由里写明「无数据（估计）」的因子。
      600584 实跑里这是 **8 个因子全中**——而第一版声明只写了未核验，
      读者会以为问题仅仅是"出处是转述"，真相更重：这些数字本身就没有
      数据支撑。后者比前者更该被看见。
    - **第 2 年起毛利率的基期被换过**（I3，final-fix-D 终审）：`a.margin_bases`
      非空说明因子分解 + 锚定开启，加法链第 2 年起的基期从年报基期换成
      了各档锚定后的首年水平。这与上面「首年被锚定覆盖」（`anchor_note`）
      是两件不同的事——即便首年批准值恰好等于生效值（`anchor_note` 为
      空），第 2 年起的基期替换依然可能发生，不能指望其中一个把另一个
      带出来。不说这件事，读者会拿「年报基期 + 累计 pp」去手算复现清单
      上的毛利率，怎么都对不上，却查不到原因。

    三者都只到 `assumptions.md` 与终端是不够的——对外传阅的是
    `report.md`。本仓库自己的标准（`AuditResult.with_sources` 的文档）：
    算出来了不交付，等于没兑现。

    两个出口分开给，因为约束不同：

    - **进提示词的那句不能带数字。** 研报正文的每个数字都必须写成占位符，
      模型照抄一句「8 个」会被裸数字审计判掉——等于自己给自己埋雷。
      所以只讲事实、不讲数量，具体几项去看 assumptions.md。
    - **附录在 `audit()` 之后追加**（与数据来源附录同一处待遇：代码写的，
      不是模型写的），因此可以带准确条数与具体是哪些因子哪些年份。
    """
    from ir_agent import assume

    unverified = assume.unverified_labels(a)
    estimated = assume.estimated_labels(a)
    rebased = bool(a.margin_bases)
    if (not unverified and not estimated and not anchor_note and not rebased
            and not unreviewed):
        return "", ""

    says: list[str] = []
    # 人改过、未经 AI 审核：读者必须知道这份判断项没有审核者背书（子项目三）。
    if unreviewed:
        says.append("**判断项经人工修改或补写，修改后未经 AI 审核**")
    # 锚定影响最大，先说：首年批准值整个没进模型，研报正文却一直按它讲。
    if anchor_note:
        says.append("**首个预测年由已披露中期锚定**，批准清单上该年的营收增速"
                    "与毛利率不进模型，估值用的是按中期实际反推的全年值")
    if rebased:
        # I3：这句不能带阿拉伯数字（进提示词，会被裸数字审计判掉）——
        # 「第二年」用汉字数字，不写成「第 2 年」。具体数字在
        # assumptions.md 与下面的 body 附录里给。
        says.append("**从第二年起，毛利率的合成基期已从年报基期改为各档"
                    "锚定后的首年水平**，不能用年报基期加累计变化手算复现")
    if unverified:
        says.append("部分 DCF 判断项的依据**未能逐字核验**——出处是转述"
                    "而非原文引用")
    if estimated:
        says.append("部分驱动因子**没有任何数据支撑、取值是估计**（该因子"
                    "在公开渠道本就无口径数据），依据列里的东西只是方向性"
                    "参照，不是这个取值的证据")
    prompt_line = ("**注意：以下关于判断项的保留必须写进估值一节**（具体"
                   "条目见待核实清单 assumptions.md 上的逐行标注）："
                   + "；".join(says)
                   + "。不要把它们写得与有确凿出处的判断项同等确定。")

    body: list[str] = ["", "---", "", "## ⚠ 判断项依据核验", ""]
    if unreviewed:
        body += [f"**{unreviewed}。**", ""]
    if anchor_note:
        body += [f"**{anchor_note}**", "",
                 "锚定的三档（占全年营收比重、剩余期毛利率）见工作簿「1_假设」"
                 "第三小节。", ""]
    if rebased:
        body += ["**第 2 年起毛利率的加法链基期已改为各档锚定后的首年水平**"
                 "（不是年报基期），因此合成结果无法用「年报基期 + 累计 pp」"
                 "手算复现——这是刻意的处理（保留模型判断的逐年变化，起点换成"
                 "已披露中期反推出的实际水平），不是取值出错。", ""]
    if estimated:
        body += [f"本报告估值所用的驱动因子中，有 {len(estimated)} 个**取值"
                 "没有任何数据支撑、是估计**（模型在待核实清单的「理由」列"
                 "已逐条写明该因子无公开口径数据）：",
                 "", "、".join(estimated), ""]
    if unverified:
        body += [f"另有 {len(unverified)} 项判断项的依据**未能在来源里逐字"
                 "核验**（已在 assumptions.md 上逐行标注，人工批准时可见）：",
                 "", "、".join(unverified), ""]
    # 这段讲的是转述与估计，只剩锚定时对不上号，不能照印。
    if unverified or estimated:
        body += ["这是一次有意的取舍：因子分解的依据允许转述、允许在确实没有"
                 "数据时给出估计，换来分解能真正跑通；代价是上述条目无法被逐字"
                 "反查。请据此调整对相应判断项的信任度——它们未必错，但没有"
                 "原文或数据替它们背书。", ""]
    return prompt_line, "\n".join(body)


def _report_stage(stage, arts: dict):
    """完整研报没写出来就不算「已建模完成」。

    第二阶段的同一个洞：`run_valuation` 建模成功就把 stage 设成 VALUED，
    而完整研报是**之后**才写的——`_write_full_report` 撞上额度上限时返回
    None、原因进 skipped，stage 却仍是 VALUED，于是 `complete` 为真、
    `next_actions` 返回空列表，等于告诉人「全做完了，没有待办」，而
    report.md 根本不存在。与第一阶段那次是同一类：只按预期路径下结论，
    不查结果。

    只降 VALUED 这一档：验算未通过本来就是 AWAITING_REVIEW，不该顺手
    改掉它自己的语义。
    """
    if stage is Stage.VALUED and "完整研报" not in arts:
        return Stage.INCOMPLETE
    return stage


def _audit_note(what: str, res) -> str:
    """审计发现的问题要作为非致命说明返回，供 `skipped` 用。

    **写进文件附录不等于交付了。** 两次实测都栽在这条上：600584 那次
    整段估值漏成未替换的占位符，是人读 report.md 才发现的；第二阶段那
    份研报的附录如实写了「以下引用缺少原始接口快照，无法完整回查：
    em_macro×7、eastmoney_report×13」（`audit.ok` 因此是 False），而终端
    产物清单一个字没提。终端才是读者第一眼看到的地方——降级原因、估值
    健全性告警、依据未逐字核验都往那里报，唯独审计自己发现的问题没有。

    三类一条都不吞：占位符没被替换（读者那一段根本没有数字）、引用缺
    快照（数字在，但反查不到原始接口响应）、裸数字（数字没经过账本）。
    来源名去重后再列——原始列表按脚注逐条追加，em_macro 重复 7 次，
    照抄只会刷屏；读者要知道的是哪几个来源、总共多少条。
    """
    parts: list[str] = []
    if res.unrendered:
        parts.append(f"{len(res.unrendered)} 个未替换的占位符（正文里仍是"
                     "原样的占位符，读者看不到真实数字）："
                     + "、".join(res.unrendered[:6]))
    if res.missing_snapshots:
        seen: list[str] = []
        for x in res.missing_snapshots:
            if x not in seen:
                seen.append(x)
        parts.append(f"{len(res.missing_snapshots)} 条引用缺少原始接口快照"
                     "（数字在，但反查不到当时的接口响应），来源："
                     + "、".join(seen[:6]))
    if res.bare_numbers:
        parts.append(f"{len(res.bare_numbers)} 个未经账本的裸数字："
                     + "、".join(res.bare_numbers[:6]))
    if not parts:
        return ""
    # 前缀固定以「审计未通过」开头——`research._mark` 按前缀给标记，
    # 把 what 放在前面会让它落到默认的「未生成」上，而研报其实生成了。
    return f"审计未通过（{what}）：" + "；".join(parts)


def _source_name(sid: str) -> str:
    """source_id → 来源名。快照库给的 id 形如 `{source}_{%Y%m%d%H%M%S}_{12位摘要}`
    （`SnapshotStore.save`）；不存快照的来源直接用来源名（em_macro）。"""
    return re.sub(r"_\d{14}_[0-9a-f]{12}$", "", sid.split("#")[0])


def _snapshot_gaps(r) -> list[tuple[str, int]]:
    """门①：账本里缺原始接口快照的事实，按来源汇总条数。

    **按实际查到的，不按写死的名单。** 此前按 `manifest.KNOWN_GAPS` 的两个
    来源名数前缀；生益科技第二次调用的审计报了 em_rates —— 名单里没有它，门①
    的清单于是漏了一个来源。判据与 `audit.py` 同一条：非 COMPUTED 的事实逐个
    `store.load(source_id)`，同一 source_id 只查一次。`KNOWN_GAPS` 降为「为什么
    缺」的说明表（`manifest.render` 里查不到说明的用默认措辞）。

    **`_facts` 的值是 `list[Fact]`，不是单条 Fact**（`ledger.py`）——按 bucket
    再展开，Task 6 实跑时在同形的 `manifest.collect_rows` 上踩到过。
    """
    from collections import Counter

    from ir_agent.ledger import Method

    store, led = getattr(r, "store", None), getattr(r, "ledger", None)
    if store is None or led is None:
        return []
    have: dict[str, bool] = {}
    c: Counter = Counter()
    for bucket in led._facts.values():                      # noqa: SLF001
        for f in bucket:
            if f.method is Method.COMPUTED:
                continue
            if f.source_id not in have:
                try:
                    store.load(f.source_id)
                    have[f.source_id] = True
                except KeyError:
                    have[f.source_id] = False
            if not have[f.source_id]:
                c[_source_name(f.source_id)] += 1
    return sorted(c.items())


def _missing_snapshot_notes(res) -> list:
    """门②：简报里缺原始接口快照的那些引用，逐条（同一 key@期间@来源只列一次）。

    快照有没有只取决于 source_id（`audit.py` 逐条 `store.load(n.source_id)`、
    跳过 COMPUTED），所以拿审计给出的缺失集合回头筛脚注就是精确的 —— 不改
    `audit.py`，第二阶段不受影响。
    """
    from ir_agent.ledger import Method

    miss = set(getattr(res, "missing_snapshots", None) or ())
    out, seen = [], set()
    for n in getattr(res, "footnotes", None) or ():
        ident = (n.key, n.period, n.source_id)
        if (n.method != Method.COMPUTED.value and n.source_id in miss
                and ident not in seen):
            seen.add(ident)
            out.append(n)
    return out


def _write_dashboard(r, out: Path, md: dict, code: str, arts: dict) -> None:
    """看板与工作簿。**第①②两次调用各生成一次**（spec ④）。

    它们不调模型、成本近零。门① 核来源的人需要一个交叉核对的入口；第②次
    再生成一遍是为了把「因子数据」那一节补进看板。此前它们在 `run_analysis`
    末尾生成 —— 拆函数时若没人接着做，它们会无声消失：没有任何测试断言
    这两个文件，全量绿不代表它们还在。
    """
    build_dashboard(r, path=out / "dashboard.html", context=md)
    arts["看板"] = str(out / "dashboard.html")
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)
    arts["工作簿"] = str(out / "model.xlsx")


def run_analyse(code: str, r, as_of: date, out_dir: str | Path, *,
                model: str | None = None,
                snapshot_dir: str | Path = "snapshots") -> ResearchOutcome:
    """第一阶段之一：定量采集 → 语料库入库 → 定性分析 → 门① 来源清单。

    **这一次调用不预研、不辩论、不撰写。** 它的交付物是 `sources.md`：人在
    门① 逐条核验资料的出处与本地文件，签名之后才进入第②次调用。
    """
    from ir_agent.sources.manifest import collect_rows, missing_files, render

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    arts: dict[str, str] = {}
    skipped: list[str] = []

    md, pr, facts_skipped = _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += facts_skipped

    # ── 语料库入库 ──────────────────────────────────────────────
    # 开放网络要联网工具 —— 不开的话不会报错，只会安静地什么都搜不到，比
    # 直接失败更容易被忽略。`industry` 走确定性查表（`_industry_path`）：
    # 预研已挪到辩论① 之后，旧写法 `meth.industry` 在这里会恒为空。
    research_client = make_client(model=model, tools=["WebSearch", "WebFetch"])
    skipped += _load_openweb(code, r, as_of, out, research_client,
                             industry=_industry_path(code))
    # **清单要按语料库的实际内容重渲染。** 不重渲染，md（进看板、进
    # state.json、后两次调用读回）说的就是加文档之前的篇数与分级构成。
    _refresh_external_md(r, md)
    # 公告入库**不需要 client**：挑选是确定性的标题分类（`announce.
    # is_substantive`），因子定向挪到消费端由 `gaps._match_announce` 做。
    skipped += _load_announce(code, r, as_of, out, md)
    _refresh_external_md(r, md)

    qual_client = _analyse_qualitative(code, r, as_of, model, md, pr, skipped)

    # ── 门①：来源清单 ───────────────────────────────────────────
    # `peers` 不在 `r` 上 —— `peer_set(code)` 的结果是 `_load_industry` 的
    # 局部变量，从未挂到 r。写成 `getattr(r, "peers", ())` 会恒取到空元组，
    # 门① 的同业样本名单就永远是空的，而 spec 要的正是让人看见「牧原 11 家
    # vs 生益 46 家」这个代表性差异。用 `_build_evidence` 已经在用的那条路。
    annual_url = getattr(r, "annual_url", "") or ""
    rows = collect_rows(
        r.ledger, corpus=getattr(r, "corpus", None), pdf_dir=out / "pdf",
        peers=_peer_names(code),
        annual=(annual_url, (_annual_cache_dir() / annual_url.rsplit("/", 1)[-1])
                if annual_url else None))
    (out / SOURCES_MD).write_text(render(rows, _snapshot_gaps(r)),
                                  encoding="utf-8")
    (out / SOURCES_JSON).write_text(
        json.dumps({"rows": [vars(x) for x in rows], "verified_by": ""},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    arts["资料来源清单"] = str(out / SOURCES_MD)
    for bad in missing_files(rows):
        skipped.append(f"来源清单：{bad}")

    _write_dashboard(r, out, md, code, arts)
    skipped += _media_tier_note(getattr(r, "corpus", None))
    _save_state(out, code, r, as_of, md, pr)
    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=Stage.AWAITING_SOURCES,
        artifacts=arts, skipped=skipped,
        cost_usd=Decimal(str(research_client.total_cost_usd
                             + qual_client.total_cost_usd)),
        phase="analyse")


def _stance_round(client, out, fname, round_, subjects, ctx, cat, r,
                  as_of, accept_manual, by, skipped, refresh=False):
    """跑一轮多空辩论，或读回已有产物 / 人工补写。失败则落盘模板，返回 None。

    **不存在「跳过」**：每份研报的多空输入要么来自模型、要么来自人。

    **已有产物算数（spec ③）**：与 `drivers.md` 同一条规矩 —— 文件在就读回，
    不再重抽。两条理由：门② 打回来重跑一次 `--brief`，重抽就是 6 次模型
    调用全做一遍；而人若手改过 `stance_b.json` 的某个三元组，重抽会把那次
    修改悄悄冲掉，正是 `drivers.md` 当初要堵的洞。要重抽给 `--refresh-stance`。
    """
    from ir_agent import stance

    path = out / fname
    low = round_.lower()
    if path.exists() and not refresh:
        try:
            if accept_manual:
                res = stance.load_manual(path, round_=round_, by=by)
                skipped.append(f"多空辩论{round_}：由 {by} 人工补写")
            else:
                # 模型产出的已有稿。**区间校验照样走** —— 它守的是「平衡点
                # 对两侧可问责」，与这份 JSON 是谁写的、什么时候写的无关；
                # 文件可能被人手改过。
                res = stance.StanceResult.from_json(
                    json.loads(path.read_text(encoding="utf-8")))
                if (probs := stance.check_interval(res.balances)):
                    raise stance.StanceFailed("；".join(probs))
                skipped.append(f"多空辩论{round_}：沿用已有产物"
                               f"（要重抽加 --refresh-stance）")
        except (stance.StanceFailed, ValueError, KeyError) as e:
            skipped.append(f"多空辩论{round_} 已有产物不可用（{str(e)[:160]}）"
                           f"—— 请修正 {path}，或加 --refresh-stance 重抽")
            return None
        # **`.md` 要按读回的内容重写，`.json` 不动。** 人手改过 JSON 之后，
        # 旁边那份 `.md` 还是上一轮模型的摘要 —— 两个文件说的不是同一件事，
        # 而人读的是 `.md`。`.json` 是人的输入，不该被 `to_json()` 往返一遍
        # （会把人加的未知字段抹掉）。
        (out / f"stance_{low}.md").write_text(res.summary(), encoding="utf-8")
        return res

    try:
        res = stance.run_stance(client, round_=round_, subjects=subjects,
                                context=ctx, catalog=cat, ledger=r.ledger,
                                as_of=as_of, corpus=getattr(r, "corpus", None))
    except stance.StanceFailed as e:
        _dump_refused(out, getattr(e, "raw", ""), f"stance_{low}_failed_raw.md")
        (out / f"stance_{low}.template.json").write_text(
            json.dumps(stance.template_json(round_, subjects),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        skipped.append(f"多空辩论{round_} 未完成（{str(e)[:160]}）")
        return None
    path.write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    (out / f"stance_{low}.md").write_text(res.summary(), encoding="utf-8")
    return res


def _review_factor_lines(factor_data) -> list[str]:
    """门②：因子联网结论（模型综合，待人工复核）。带 ⚠ 的排最前。

    用户决定 B：门② 一次签名覆盖全部结论，有异议直接改或删 `factors.json`
    里对应条目后再签 —— 所以这里要把文件名说出来。
    """
    from ir_agent.sources.conclusions import Conclusion

    rows = [(fd.factor.label, c) for fd in (factor_data or {}).values()
            for c in fd.findings if isinstance(c, Conclusion)]
    if not rows:
        return []
    rows.sort(key=lambda x: not x[1].flags())               # 稳定排序：⚠ 在前
    lines = [f"## 因子联网结论（模型综合，待人工复核）：{len(rows)} 条", "",
             "结论可以综合多篇文章、可以改写；代码只核对了文章打不打得开、结论里"
             "的数字在引用文章里有没有出处。**有异议的，直接改或删 `factors.json`"
             " 里对应条目后再签门②。**", ""]
    for label, c in rows:
        flags = c.flags()
        lines.append(f"- {'⚠ ' if flags else ''}**{label}**：{c.claim}")
        if flags:
            lines.append(f"  - ⚠ {flags}")
        for a in c.articles:
            st = "" if a.opened else f"（{a.why_not}）"
            lines.append(f"  - 文章{a.no}：[{a.publisher or a.link}]({a.link})"
                         f"　{a.tier.value}　{a.published}{st}")
        if c.marks:
            lines.append("  - 数字出处：" + "　".join(
                f"{m.number} " + ("✓ 文章" + "、".join(m.found_in)
                                  if m.found_in else "⚠ 找不到")
                for m in c.marks))
        if c.dropped:
            lines.append(f"  - 已剔除：{'；'.join(c.dropped)}")
    lines.append("")
    return lines


def _write_review(out: Path, sa, sb, missing=(), factor_data=None,
                  method=None) -> None:
    """门② 的待核清单：把所有「代码没把握」的东西聚到一处。"""
    from ir_agent.sources.manifest import KNOWN_GAPS

    lines = ["# 待核清单（门②：请在批准简报结论前过一遍）", ""]
    for name, s in (("多空辩论①", sa), ("多空辩论②", sb)):
        if s is not None and s.unverified:
            lines += [f"## {name}：{len(s.unverified)} 条证据代码核验不过", ""]
            lines += [f"- [{e.side}] {e.subject}：{e.unverified}"
                      for e in s.unverified] + [""]
    if method is not None:
        lines += ["## 方法论审核（预研阶段审核者的提醒与待采集）", ""]
        lines += (method or ["提醒、待采集 0 条。"]) + [""]
    lines += _review_factor_lines(factor_data)
    if missing:
        groups: dict[str, list] = {}
        for n in missing:
            groups.setdefault(_source_name(n.source_id), []).append(n)
        lines += [f"## 简报引用缺原始接口快照：{len(missing)} 条"
                  "（数字在，但反查不到当时的接口响应）", ""]
        for name, ns in groups.items():
            why = KNOWN_GAPS.get(name, "未保留原始接口响应")
            lines += [f"### {name}（{len(ns)} 条）—— {why}", ""]
            lines += [f"- {n.display or '—'}　`{n.key}@{n.period}`"
                      for n in ns] + [""]
    if len(lines) == 2:
        lines.append("本轮没有需要人工复核的标注项。")
    (out / REVIEW_MD).write_text("\n".join(lines), encoding="utf-8")


def _sign_sources(out: Path, verified_by: str) -> None:
    """把门① 的署名回填进 `sources.json`。

    `run_analyse` 落盘时写的是 `"verified_by": ""` —— 那时还没人核验。署名
    是下一次调用（`--brief --verified-by`）才带进来的，所以回填发生在这里。
    **读不回就跳过、不崩**：清单本身已经在盘上，签名写不进去是个瑕疵，不该
    让整次调用失败。
    """
    f = out / SOURCES_JSON
    if not f.exists() or not verified_by.strip():
        return
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    d["verified_by"] = verified_by.strip()
    f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _sign_header(path: Path, out: Path) -> None:
    """在产物顶部印出它的上游经谁核过（spec ①B：签名只进第一阶段产物）。

    `brief.md` 产出时门② 还没开，能印的只有门① 的核验人；`assumptions.md`
    产出时门①② 都已签过。签名从 `state.json` 读（`_save_state` 写进去的），
    不另传参数 —— 同一个事实只有一个来源。

    **最终研报的附录不改**：第二阶段一行都不动。已知代价（spec 写明）：
    半年后读最终研报的人，翻不到资料来源与简报结论有没有人核过。
    """
    if not path.exists():
        return
    try:
        st = json.loads((out / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    parts = []
    if (v := st.get("verified_by")):
        parts.append(f"资料来源经 {v} 核验")
    if (v := st.get("reviewed_by")):
        parts.append(f"简报结论经 {v} 审阅")
    if not parts:
        return
    body = path.read_text(encoding="utf-8")
    line = "> " + "；".join(parts) + "\n\n"
    if not body.startswith("> "):
        path.write_text(line + body, encoding="utf-8")


def run_brief(code: str, r, as_of: date, out_dir: str | Path,
              verified_by: str, *, model: str | None = None,
              snapshot_dir: str | Path = "snapshots",
              accept_manual: bool = False,
              refresh_stance: bool = False) -> ResearchOutcome:
    """第一阶段之二：辩论① → 方法论预研 → 因子采集 → 辩论② → 撰写简报。

    **五个模型环节逐个立刻落盘。** 实测里这五个各自都失败过；没有中间落盘点
    的话，失败会丢掉已完成的部分。
    """
    # `assume` 必须在这里导入：`full.py` 顶部没有它（全仓库都在函数内导），
    # 下面 `list(assume.DRIVERS)` 不导就是 NameError。
    from ir_agent import assume

    if not verified_by.strip():
        raise SystemExit("必须署名核验人 —— 资料来源要有人核验过才谈得上可用。")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    arts: dict[str, str] = {}
    skipped: list[str] = []
    _sign_sources(out, verified_by)

    # **两个 client，不能合成一个。** 预研要联网补方法论、`gaps.collect` 的
    # ④分支要联网找因子数据 —— 不开联网工具不会报错，只会安静地什么都搜
    # 不到。辩论与撰写**不该**开：它们只能用已入库的材料，联网会让引文出处
    # 跑到语料库之外、核验不过。见
    # `test_第一阶段之二的预研与因子采集用的是开了联网工具的client`。
    client = make_client(model=model)
    research_client = make_client(model=model, tools=["WebSearch", "WebFetch"])

    md, pr = _restore_state(out)
    _analyse_facts(code, r, as_of, snapshot_dir)      # 重建派生事实，不调模型
    # **读回必须排在 `_build_evidence` 之前**：它把年报正文按调用时刻抄成
    # 字符串，晚了核验面就没有年报。
    skipped += _restore_corpus(r, out, md, sections=True)
    cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=as_of,
                                  extra_periods=_catalog_periods(r))
    cat.evidence = _build_evidence(r, code)
    ctx = "\n\n".join(f"【{k}】\n{v}" for k, v in md.items() if v)

    def _cost() -> Decimal:
        return Decimal(str(client.total_cost_usd
                           + research_client.total_cost_usd))

    # ── 多空辩论①：定方向 ───────────────────────────────────────
    sa = _stance_round(client, out, STANCE_A, "A", list(STANCE_THEMES), ctx,
                       cat, r, as_of, accept_manual, verified_by, skipped,
                       refresh=refresh_stance)
    if sa is None:
        o = _stop_for_manual(code, r, as_of, out, arts, skipped, client,
                             round_="A")
        o.cost_usd = _cost()
        return o
    arts["多空辩论①"] = str(out / "stance_a.md")

    # ── 方法论预研：现在能看到全部证据与多空结论 ────────────────
    # `stance_note=sa.summary()` 是「辩论① 排在预研之前」唯一的兑现处 ——
    # 不传它，辩论① 的结论就到不了预研，流程图上它在那儿而数据流上它不在。
    try:
        meth, merr = _research_methodology(code, r, out, research_client,
                                           judge=client, md=md,
                                           stance_note=sa.summary())
    except MethodologyStop as e:
        o = _stop_for_methodology(code, r, as_of, out, arts, skipped, client,
                                  str(e))
        o.cost_usd = _cost()
        return o
    if merr:
        skipped.append(merr)
    if meth is not None:
        arts["驱动因子分解"] = str(out / DRIVERS_MD)

    # ── 因子定向采集 ───────────────────────────────────────────
    factor_data: dict = {}
    if meth is not None:
        factor_data = _collect_factors(r, as_of, research_client, md,
                                       meth.model, meth.industry,
                                       skipped)
        # 落盘给第三次调用。空字典也写 —— 让「采过、一个都没采到」与「没落盘」
        # 在第三次调用里可区分。
        from ir_agent import gaps
        (out / FACTORS_JSON).write_text(
            json.dumps(gaps.to_json(factor_data), ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ── 多空辩论②：定取值 ─────────────────────────────────────
    subjects = ([f.label for f in meth.factors()]
                if meth is not None else list(assume.DRIVERS))
    sb = _stance_round(client, out, STANCE_B, "B", subjects,
                       ctx + "\n\n【因子采集】\n" + _factor_ctx(factor_data),
                       cat, r, as_of, accept_manual, verified_by, skipped,
                       refresh=refresh_stance)
    if sb is None:
        o = _stop_for_manual(code, r, as_of, out, arts, skipped, client,
                             round_="B")
        o.cost_usd = _cost()
        return o
    arts["多空辩论②"] = str(out / "stance_b.md")

    # ── 撰写简报：应用多空平衡点 ────────────────────────────────
    print(f"撰写研究简报（{len(cat.entries)} 条可引用事实）…", file=sys.stderr)
    body = None
    try:
        body, _ = write_body(cat, client, code=code, period=r.period, brief=True,
                             context=f"**多空平衡点**\n\n{sb.summary()}",
                             **{k: v or None
                                for k, v in _prompt_bundle(pr).items()})
    except (AnalystRefused, ClaudeCliError) as e:
        dumped = _dump_refused(out, getattr(e, "raw", ""), WRITE_FAILED_BRIEF)
        skipped.append(f"研究简报（{e.__class__.__name__}: {str(e)[:160]}）"
                       + (f"（最后一稿已落盘：{dumped}）" if dumped else ""))
    if body:
        r.audit = audit(body, r.ledger, r.store, as_of=r.as_of)
        (out / BRIEF).write_text(r.audit.with_sources(), encoding="utf-8")
        arts["研究简报"] = str(out / BRIEF)
        if (note := _audit_note("研究简报", r.audit)):
            skipped.append(note)
        _write_review(out, sa, sb, _missing_snapshot_notes(r.audit), factor_data,
                      method=(_method_review_lines(out, meth)
                              if meth is not None else None))
        arts["待核清单"] = str(out / REVIEW_MD)

    _write_dashboard(r, out, md, code, arts)
    skipped += _media_tier_note(getattr(r, "corpus", None))
    _save_state(out, code, r, as_of, md, pr, verified_by=verified_by)
    if body:
        _sign_header(out / BRIEF, out)
    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of,
        stage=Stage.AWAITING_BRIEF_REVIEW if body else Stage.INCOMPLETE,
        artifacts=arts, skipped=skipped, cost_usd=_cost(), phase="brief")


def run_assume(code: str, r, as_of: date, out_dir: str | Path,
               reviewed_by: str, *, model: str | None = None,
               snapshot_dir: str | Path = "snapshots") -> ResearchOutcome:
    """第一阶段之三：AI 填 DCF 判断项 + 四道检查。

    判断项的三档候选输入来自 `stance_b.json` 的平衡三元组；偏离该三元组的
    行必须填 `deviation`（只查存在性）。

    **没有 `accept_manual`。** 两轮辩论都在 `run_brief` 里，这一次调用没有
    任何多空环节，那个开关在这儿无物可读。
    """
    from ir_agent import stance

    out = Path(out_dir)
    brief = out / BRIEF
    sb_path = out / STANCE_B
    for f in (brief, sb_path):
        if not f.exists():
            raise SystemExit(
                f"找不到 {f} —— 请先运行第一阶段之二：\n"
                f"  python -m ir_agent {code} --year {r.period[:4]} "
                f"--brief {out} --verified-by <名字>")
    if not reviewed_by.strip():
        raise SystemExit("必须署名审阅人 —— 简报结论要有人审过才谈得上可据以建模。")

    arts: dict[str, str] = {}
    skipped: list[str] = []
    body = brief.read_text(encoding="utf-8")
    sb = stance.StanceResult.from_json(
        json.loads(sb_path.read_text(encoding="utf-8")))
    md, pr = _restore_state(out)
    # **重建派生事实必须排在填判断项之前。** industry.* / comps.* / macro.*
    # 只存在于内存账本；判断项的依据完全可以合法地引用它们，而本次调用是
    # 一个新进程。与 `run_valuation` 里那条同源的实测教训（600584 端到端）：
    # 不重建的话，校验时账本还没建好，合法的依据会被判为不存在。
    _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += _restore_corpus(r, out, md, sections=False)

    # **单独一个客户端，开超时重试、给更长的超时。** 这一步是全链路输出最长
    # 的一次调用，瓶颈在输出而不在提示词长度。实测（生益科技 2026-10-06）：
    # 它 900s 超时，整轮 $11.09 作废。
    assume_client = make_client(model=model, timeout=ASSUME_TIMEOUT,
                                retry_timeout=True)

    # **只读 drivers.md，不联网重新预研。** `_research_methodology` 在文件
    # 缺失时会联网预研一个新分解式 —— 若这里那样做，人在门② 审过的简报与
    # 接下来要填的判断项依据的是两个不同的分解式，而产物上看不出来。
    meth, derr = _read_drivers(out)
    if derr:
        skipped.append(derr)
    # **判断项要用第二次调用采集的同一份数据**，不在这里重新采集 —— 简报与
    # 辩论② 用的就是那一份。第一版传的是 `factor_data=None`。
    factor_data, fnotes = _read_factors(out, meth)
    skipped += fnotes
    aerr = _propose_assumptions(
        code, r, body, md, as_of, out, assume_client, arts,
        decomps=(meth.model if meth is not None else None),
        factor_data=factor_data, balances=sb.balances,
        method_review=_method_review_lines(out, meth))
    if aerr:
        skipped.append(aerr)
    _save_state(out, code, r, as_of, md, pr, reviewed_by=reviewed_by)
    if ASSUMPTIONS_ART in arts:
        _sign_header(out / ASSUMPTIONS_MD, out)
    # **按本轮登记的产物定状态，不看磁盘上有没有那个文件。** 上一轮残留的
    # assumptions.json 会让这一轮 `_propose_assumptions` 的失败伪装成成功 ——
    # 与「上一轮残留的 assumptions.md 把第一阶段的失败伪装成第二阶段的失败」
    # （生益科技重跑实测）同一个洞。`_review_stage` 看的是 `arts`。
    stage, reason = _review_stage(arts)
    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=stage,
        artifacts=arts, skipped=skipped, route_reason=reason,
        cost_usd=Decimal(str(assume_client.total_cost_usd)), phase="assume")


def _anchor_banner(cal: dict, labels: list[str], rebased: bool = False) -> str:
    """批准清单顶部的锚定横幅：首个预测年会被已披露中期覆盖。

    人在第一阶段批准首年的营收增速与毛利率，要到第二阶段建模时才会被
    锚定覆盖——那时已经批完了。生益科技实跑正是如此：批准 22.31% /
    27.77%，生效 39.85% / 30.69%，清单上一个字没提。横幅给 Base 档的
    生效值（占比取历史均值、剩余期毛利率沿用已披露期），Bull/Bear 两档
    见工作簿。反推公式用 `dcf.anchored_year1`，与回读校验、研报附录同源。

    `rebased`（I3，final-fix-D 终审）：因子分解模式下锚定开启时，毛利率
    加法链第 2 年起的基期也已从年报基期换成各档锚定后的首年水平
    （`assume.margin_bases`）——不只是首年被覆盖这么简单。此前这里说
    「批准时请把注意力放在后续年份」，暗示只有首年不可信、后续年份维持
    原样，恰恰说反了：后续年份的**水平**同样被锚定抬走，清单上会出现
    「2026 27.77% → 2027 32.09%」这类用「年报基期 + 累计 pp」怎么手算
    都对不上的跳变。调用方（`_propose_assumptions`）判断项定下来之后
    从 `a.margin_bases` 是否非空得到这个信号，传进来决定要不要多说这句。
    """
    it = cal.get("interim")
    if not it or not it.get("share") or not it.get("rev") or not it.get("cogs"):
        return ""
    annual = (cal.get("periods") or {}).get("annual") or []
    base_rev = (cal.get("rev") or {}).get(annual[-1]) if annual else None
    if not base_rev:
        return ""
    g1, m1 = dcf.anchored_year1(rev_h=float(it["rev"]), cogs_h=float(it["cogs"]),
                                share=float(it["share"]), gm_rest=float(it["gm"]),
                                base_rev=float(base_rev))
    first = labels[0] if labels else "首个预测年"
    rebase_note = (
        "**第 2 年起毛利率的加法链基期也已从年报基期改为各档锚定后的首年"
        "水平**，因此这些年份同样不能用「年报基期 + 累计 pp」手算复现——"
        "批准时请核对下表显示的合成结果本身，而不是自己重新加总；"
        if rebased else "批准时请把注意力放在后续年份；")
    return (f"> ⚠ **{first} 将由{it.get('label') or '已披露中期'}锚定**：按已披露"
            f"实际反推，Base 档生效营收增速约 {g1:.2%}、毛利率约 {m1:.2%}。"
            f"下表 {first} 的取值**不进模型**，{rebase_note}"
            "锚定的三档（占全年营收比重、剩余期毛利率）见工作簿「1_假设」"
            "第三小节。\n\n")


def _propose_assumptions(code, r, body, md, as_of, out, client, arts,
                         decomps=None, factor_data=None, balances=None,
                         method_review=None) -> str:
    """校准 → AI 填判断项 → AI 审核 → 写出待核实清单。返回失败或保留说明。

    `decomps`/`factor_data` 给定时按因子分解填写（方法论预研 + 定向采集的
    产出），否则是两标量模式 —— 只在取不到行业分类时出现。

    **判断项合格与否全部由审核者判定**（子项目三，spec 2026-10-15）：代码只读数、
    计算、核对，把结果作为事实交给审核者；门③ 原先的两道 AI 审核（`factor_review`
    [对账]、判断项假设辩论）已并进审核者。审核 3 轮不过 / 答不全 / 审核通过但算不
    下去 → 停下交人工：写各轮记录与最后一稿，**不写 assumptions.json**（那是给门③
    批准的东西，未通过审核的稿子不能冒充它）。
    """
    from ir_agent import assume, assume_review

    try:
        cal = dcf.calibrate_only(code, as_of=as_of, ledger=r.ledger)
    except dcf.DcfUnavailable as e:
        return f"DCF 判断项（校准失败：{e}）"

    labels = (cal.get("periods") or {}).get("proj") or []
    ctx = "\n\n".join(f"【{k}】\n{v}" for k, v in md.items() if v)
    # 加法链（毛利率）的基期水平——与 assume.apply_to/to_driver_block
    # 里用的必须是同一个数，否则第一阶段判断项的口径与第二阶段建模的
    # 口径就各算各的了。I4：取「最近一个完整年度」，不跟着 r.period
    # 走——`--period H1` 时 r.period 是「year+H1」，直接用会取到中报。
    latest_annual = _latest_annual_period(r.ledger, as_of)
    base_margin = (_fact_value(r.ledger, "gross_margin", latest_annual, as_of)
                  if latest_annual else None)
    # 审核者的账本事实目录：原判断项假设辩论靠它发现过「应付账款天数在同业 17
    # 分位，证伪了『对供应商占款强』」—— 辩论并进审核者，这份资料一起交过去。
    try:
        catalog = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=as_of,
                                          extra_periods=_catalog_periods(r)
                                          ).render_for_prompt()
    except Exception:                                       # noqa: BLE001
        catalog = ""
    try:
        a = assume.decide(client, cal["judg"], labels, ctx, judge=client,
                          ledger=r.ledger, body=body, as_of=as_of, code=code,
                          decomps=decomps, factor_data=factor_data,
                          base_margin=base_margin,
                          # 锚定输入：清单上展示的毛利率路径与建模时同一套基期。
                          anchor=assume.anchor_from_cal(cal),
                          # 辩论② 的平衡三元组：作为事实交审核者 [平衡点]。
                          balances=balances, catalog=catalog,
                          # 预研阶段审核者的提醒/待采集（子项目二决定 7）。
                          method_review=list(method_review or []))
    except assume_review.ReviewStop as e:
        return _stop_for_assume_review(out, arts, e, code=code)
    except ClaudeCliError as e:
        # 模型接口本身失败（额度用尽、超时、CLI 报错）：**记进 skipped、让整轮
        # 继续**，写明是接口问题、重跑即可。实测 600584 第三次实跑：辩论已完成
        # 并回写了 5 项挑战，却因为下一个调用撞上额度上限而一个产物都没留下。
        return (f"DCF 判断项（{e.__class__.__name__}: {str(e)[:160]}）"
                "—— 模型接口问题，原样重跑 --assume 即可")

    (out / ASSUMPTIONS_MD).write_text(
        _anchor_banner(cal, labels, rebased=bool(a.margin_bases)) + a.describe()
        + _APPROVE_NOTE.format(js=ASSUMPTIONS_JSON),
        encoding="utf-8")
    (out / ASSUMPTIONS_JSON).write_text(
        json.dumps(assume.to_json(a, code=code, approved_by="",
                                  approved_at=""),
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out / ASSUMPTIONS_REVIEW_JSON).write_text(
        assume_review.dump_review(a.judge_review, passed=True), encoding="utf-8")
    (out / ASSUMPTIONS_ROUNDS_MD).write_text(
        assume_review.rounds_markdown(a.judge_rounds), encoding="utf-8")
    arts[ASSUMPTIONS_ART] = str(out / ASSUMPTIONS_MD)
    arts["判断项审核各轮记录"] = str(out / ASSUMPTIONS_ROUNDS_MD)
    return _assumption_caveat_note(a)


def _stop_for_assume_review(out: Path, arts: dict, e, *, code: str) -> str:
    """判断项审核停下交人工：落各轮记录、最后一稿的原文与可改的 JSON 底稿。

    底稿写 `assumptions_failed.json` 而不是 `assumptions.json` —— 后者是门③ 批准的
    对象；人以底稿改好、自己存成 assumptions.json 再跑 --value，读回时会因没有通过
    的审核记录而标注「人工修改后未经审核」（spec 一）。"""
    from ir_agent import assume, assume_review

    (out / ASSUMPTIONS_ROUNDS_MD).write_text(
        assume_review.rounds_markdown(e.rounds), encoding="utf-8")
    arts["判断项审核各轮记录"] = str(out / ASSUMPTIONS_ROUNDS_MD)
    if e.rounds:
        (out / FAILED_RAW).write_text(e.rounds[-1].raw, encoding="utf-8")
        arts["判断项最后一稿（原文）"] = str(out / FAILED_RAW)
    if e.last is not None and e.last.rows:
        (out / ASSUMPTIONS_FAILED_JSON).write_text(
            json.dumps(assume.to_json(e.last, code=code, approved_by="",
                                      approved_at=""),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        arts["判断项最后一稿（可改的底稿）"] = str(out / ASSUMPTIONS_FAILED_JSON)
    return f"{e} —— 各轮记录：{out / ASSUMPTIONS_ROUNDS_MD}"






def _assumption_caveat_note(a) -> str:
    """判断项**产出之后**的保留意见。空串 = 没有保留。

    前缀是「判断项待复核：」而不是「DCF 判断项（…）」，这是必须的:
    `_propose_assumptions` 另有三条返回路径（校准失败 / AssumeFailed /
    调用失败）也以「DCF 判断项（」开头，而那三条是**真的没产出判断项**，
    `research._mark` 把它们标成「未生成」是对的。两种状态共用一个前缀时，
    白名单无从区分 —— 注册它会把三条真失败一起降级成警告。

    实测（贵州茅台 600519 第一阶段）: 同一份输出里一行写着
    「✓ DCF 判断项（待核实）：…/assumptions.md」，另一行写着
    「– 未生成：DCF 判断项（5 行依据未能逐字核验（已标注，**未阻断**…））」
    —— 消息自己说未阻断，标签说没生成。
    """
    notes: list[str] = []
    # 判断项审核的提醒、待采集：通过了，但审核者认为有牵强或缺资料的地方 —— 终端
    # 产物清单是读者第一眼看到的地方，不能只留在 assumptions.md 里。
    res = getattr(a, "judge_review", None)
    if res is not None and res.flagged():
        c = res.counts()
        notes.append(f"判断项审核第 {res.rounds} 轮通过，提醒 {c['提醒']} 条、"
                     f"待采集 {c['待采集']} 条（逐条见清单顶部）")
    # 引文逐字核验不过只标注、交审核者判断 —— 终端也要露出来。
    unverified = sum(1 for r in a.rows if getattr(r, "basis_unverified", ""))
    if unverified:
        notes.append(f"{unverified} 行依据未能逐字核验（已标注，未阻断，"
                     "需人工复核）")
    return f"判断项待复核：{'；'.join(notes)}" if notes else ""


_APPROVE_NOTE = """
---

## 如何批准

这些是**判断项**，模型无法从历史数据推出来，只能靠分析得出 —— 也正因此
必须有人看一眼再建模。费用率、周转天数、折旧率、税率、β 等由历史校准，
不在此列，也不需要你确认。

- 认可就直接进入第二阶段建模。
- 要改数值，改 `{js}` 里的 `rows`，再进入第二阶段。改过的格子**不再送 AI 审核**：
  第二阶段比对审核时的取值指纹，在终端与研报里写明「判断项经人工修改，修改后
  未经审核」并列出改动；代码只查算不算得下去（缺年份、缺基期等），不按内容拒绝。
"""


def run_valuation(code: str, r, as_of: date, out_dir: str | Path,
                  approved_by: str, model: str | None = None,
                  snapshot_dir: str | Path = "snapshots") -> ResearchOutcome:
    """第二阶段：人工批准的假设 → 三表 DCF 建模 → 完整研报。"""
    from ir_agent import assume

    out = Path(out_dir)
    brief = out / BRIEF
    js = out / ASSUMPTIONS_JSON
    for f in (brief, js):
        if not f.exists():
            raise SystemExit(
                f"找不到 {f} —— 请先运行第一阶段：\n"
                f"  python -m ir_agent {code} --year <年度> --analyse {out}")
    if not approved_by.strip():
        raise SystemExit("必须署名批准人 —— 假设要有人负责才谈得上「已核实」。")

    arts: dict[str, str] = {}
    body = brief.read_text(encoding="utf-8")
    saved = json.loads(js.read_text(encoding="utf-8"))
    state = json.loads((out / STATE).read_text(encoding="utf-8")) \
        if (out / STATE).exists() else {"md": {}, "pr": {}}

    # **`from_json` 里 `_decomp_from_dict` 直接构造 `Factor`，构造即校验。**
    # 这里不兜的话，一份人手改坏的 assumptions.json 会以 traceback 的形式
    # 崩掉整条第二阶段命令——`run_valuation` 对 `AssumeFailed` 的捕获在
    # 下面（约 1809 行），在这一行之后，兜不到这里。措辞与上下两处
    # `SystemExit` 同源：这份文件不可用，请做什么。
    try:
        a, edits = assume.from_json(saved, code=code)
    except DecompositionInvalid as e:
        raise SystemExit(
            f"{js} 里的分解式已不合法：{e}　——请重跑第一阶段，"
            "重新预研分解式后再填判断项。")
    if not a.rows:
        raise SystemExit(
            f"{js} 里没有判断项 —— 请先跑完第一阶段。")
    # 分解模式下重新校验要用同一套 decomps/base_margin，否则 check()
    # 会退回两标量分支，把因子模式的行误判成「营收增速缺第 N 年」而
    # 拒绝建模——那不是假设真的不合法，是校验用错了口径。
    # **人改过的数字要说出来。** 依据是为模型原来那个数写的，改了之后
    # 它未必还支撑新数字 —— 这件事代码判断不了，但必须让读者看见。
    # skipped 要累加而非被重建派生事实的结果整体覆盖——此前这里曾经是
    # `_, _, skipped = _analyse_facts(...)`，edits 非空时人工调整的说明
    # 会被那一句悄悄丢掉（edits 为空时侥幸不报错，是因为空列表压根不会
    # 走进这个 for 循环）。
    skipped: list[str] = []
    for x in edits:
        skipped.append(f"判断项经人工调整：{x}（原依据未必仍然支撑新取值）")
    # **人改过的不再送审，只标注**（子项目三决定 5）：人在门③ 签字就是承担责任，
    # AI 不该否决人的决定；但「这几格没经过审核」必须让读者看见。
    unreviewed = _unreviewed_assumptions(out, a, edits)
    if unreviewed:
        skipped.append(f"判断项未经审核：{unreviewed}")

    # **重建派生事实必须排在校验之前。** industry.* / comps.* / macro.*
    # 这些 key 只存在于内存账本，由 _analyse_facts 算出来；第一阶段的
    # 判断项完全可以合法地引用它们（依据写成 [[industry.dtl@2025FY]]），
    # 而第二阶段是一个新进程，账本刚从接口重建、还没跑派生层。
    #
    # 实测踩到（600584 端到端）：人已经批准，第二阶段却在建模前一刻报
    # 「批准的假设不合法，拒绝建模：依据引用的事实 industry.dtl@2025FY
    # 在账本中不存在」——理由不是假设有问题，是**校验时账本还没建好**。
    # 人工批准这道关卡因此被架空：批准的东西下一步就不合法了。
    #
    # 这一层不调模型，代价只是几次网络抓取，提前跑没有副作用。
    print("重建分析层事实（不调模型）…", file=sys.stderr)
    _, _, facts_skipped = _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += facts_skipped
    # 开放网络文档：读回第一阶段落盘的，**不重新采集**。重跑 discover
    # 会引入第一阶段没有的文档，让第一阶段正文里的外部引文核验不过。
    skipped += _restore_openweb(r, out)
    # 公司公告同理：读回，**不重新采集**。挑选要调模型，重跑会挑出与第一
    # 阶段不同的公告，第一阶段正文里的公告引文就核验不过——而公告是
    # `Tier.COMPANY`，这套设计里证明力最高的证据。理由见 `_load_announce`
    # 与 `_restore_announce` 的 docstring。**必须是 `skipped +=`**：
    # 裸调用时文档照样读回，但三种零文档措辞永远进不了产物清单。
    skipped += _restore_announce(r, out)

    # 基期毛利率也要在重建之后再取——gross_margin 同样来自派生层。
    # I4：取「最近一个完整年度」，不跟着 r.period 走——同一个理由见
    # `_propose_assumptions`，第二阶段这里必须与第一阶段用同一个基期。
    latest_annual = _latest_annual_period(r.ledger, as_of)
    base_margin = (_fact_value(r.ledger, "gross_margin", latest_annual, as_of)
                  if latest_annual else None)
    # C1（final-fix-D 终审）：check() 必须与第一阶段用同一个 anchor，否则
    # 查的是两条不同的毛利率路径——`_propose_assumptions` 传
    # anchor=anchor_from_cal(cal)，查锚定平移后的路径；这里此前压根没取
    # cal，anchor 恒为 None，查的是未平移的年报基期路径。600183 实跑
    # 复现：同一组已批准的判断项，第一阶段 PASS，第二阶段却因为查错了
    # 路径而报「第 2 年的毛利率合成后不再单调」并 SystemExit 拒绝建模——
    # 人工批准这道关卡因此被架空，见 test_assume.py 的
    # `test_两阶段用不同anchor会给出相反结论`（数字与实跑现场一致）。
    # 校准失败时退回 anchor=None（维持未平移路径）：这不是新增的失败
    # 方式，dcf.run 随后同样会遇到 DcfUnavailable，处置口径要一致。
    try:
        cal = dcf.calibrate_only(code, as_of=as_of, ledger=r.ledger)
        anchor = assume.anchor_from_cal(cal)
    except dcf.DcfUnavailable:
        anchor = None
    # **只计算，不再判断**（子项目三）：人在门③ 签了字，AI 审核也已在第一阶段
    # 做过；这里只看算不算得下去（缺年份、缺基期、重复行、公式算不动）。此前
    # 这里会把人批准的判断项再按内容闸门拒绝一遍 —— 600584 实测，人批准了，
    # 建模前一刻又被拒，人工门被架空。
    facts = assume.check(a, len(a.labels), ledger=r.ledger, body=body, as_of=as_of,
                         # **读 `a._m()`，不读 `a.decomps`**：公式化写法下 decomps
                         # 是空列表，`decomps=a.decomps or None` 会让第二阶段悄悄
                         # 退回两标量口径，人批准的公式在这里一行都不生效。
                         decomps=a._m(), base_margin=base_margin,
                         anchor=anchor)
    if facts.blocking:
        raise SystemExit("批准的判断项算不下去，无法建模：\n"
                         + "\n".join(f"  - {x}" for x in facts.blocking))

    # I3（final-fix-D 终审）：`check()` 通过后 `a.margin_bases` 就定了——
    # 非空说明毛利率加法链第 2 年起的基期已从年报基期换成各档锚定后的
    # 首年水平。这与上面 `dcf_res.anchor_note`（首年批准值没参与建模）
    # 是两件不同的事，终端要能分别看到，不能指望其中一条捎带上另一条。
    if a.margin_bases:
        skipped.append("判断项被锚定覆盖：第 2 年起毛利率的加法链基期已改为"
                       "各档锚定后的首年水平（不是年报基期），不能用「年报"
                       "基期 + 累计 pp」手算复现待核实清单上的毛利率")

    md = state.get("md") or {}
    pr = state.get("pr") or {}
    # state.json 里那份清单是第一阶段渲染的；本阶段刚重建了研报语料库、
    # 又读回了开放网络文档与公司公告（后者是 `Tier.COMPANY`，直接改变分级
    # 构成），篇数与分级构成要按**现在**的语料库重算，
    # 否则看板与终端还在讲第一阶段那个口径（见 `_external_md`）。
    _refresh_external_md(r, md)

    dcf_res = None
    stage = Stage.NOT_APPLICABLE
    try:
        print("三表 DCF 建模（ashare-dcf）…", file=sys.stderr)
        dcf_res = dcf.run(code, out, as_of=as_of, ledger=r.ledger,
                          assumptions=a, approved_by=approved_by)
    except dcf.DcfUnavailable as e:
        skipped.append(f"估值（{e}）")
    except Exception as e:                                  # noqa: BLE001
        skipped.append(f"估值（{e.__class__.__name__}: {str(e)[:160]}）")
    else:
        arts["DCF 模型"] = dcf_res.workbook
        md["估值"] = dcf_res.summary()
        for f in dcf.to_facts(dcf_res, as_of):
            r.ledger.put(f)
        stage = Stage.VALUED if dcf_res.verified else Stage.AWAITING_REVIEW
        if not dcf_res.verified:
            skipped.append("估值（公式引擎验算未通过，结果不可采信）")
        for x in dcf_res.crosscheck:
            skipped.append(f"估值口径与账本不一致：{x}")
        # **健全性警告同样一条都不吞。** 它们此前只进了看板，没进终端
        # 产物清单 —— 而终端是读者第一眼看到的地方。实测: 终值占 EV
        # 77.7%（>75%）的警告算出来了却没交付。
        for x in dcf_res.warnings:
            # 锚定说明混在 warnings 里（回读校验报的），但它不是健全性问题，
            # 是「人批准的数没被用上」——换个前缀，不另起一条重复的。
            if dcf_res.anchor_note and x == dcf_res.anchor_note:
                skipped.append(f"判断项被锚定覆盖：{x}")
            else:
                skipped.append(f"估值健全性：{x}")

    cost = 0.0
    # 判断项的软处（依据未逐字核验 / 取值是估计）要跟着交付物走，见 `_assumption_caveats`。
    caveat, caveats_md = _assumption_caveats(
        a, anchor_note=(dcf_res.anchor_note if dcf_res is not None else ""),
        unreviewed=unreviewed)
    if dcf_res is not None:
        full, cost, ferr, debate_md, dnote = _write_full_report(
            code, r, body, model, dcf_res, _prompt_bundle(pr), out,
            caveat=caveat)
        if ferr:
            skipped.append(ferr)
        if dnote:
            skipped.append(dnote)
        if debate_md:
            (out / DEBATE_REPORT).write_text(debate_md, encoding="utf-8")
            arts["辩论记录（完整研报）"] = str(out / DEBATE_REPORT)
        if full:
            r.audit = audit(full, r.ledger, r.store, as_of=r.as_of)
            # 附录追加在 `audit()` **之后**：它是代码写的、不是模型写的，
            # 里面的行数不该被当成正文里的裸数字（与数据来源附录同一处
            # 待遇）。
            (out / REPORT).write_text(r.audit.with_sources() + caveats_md,
                                      encoding="utf-8")
            arts["完整研报"] = str(out / REPORT)
            if (note := _audit_note("完整研报", r.audit)):
                skipped.append(note)
    else:
        skipped.append("完整研报（无估值结果，第 8 节无从写起）")

    # 依据核验过、但来源分级是 MEDIA 的记录——警告，不拒绝，见
    # `_media_tier_note`。本阶段会调 `basis.validate()` 的环节
    # （`assume.check()`、完整研报的 `audit()`）此时都已跑完。
    skipped += _media_tier_note(r.corpus)

    # **没写出研报就不算做完。** 见 `_report_stage`。
    stage = _report_stage(stage, arts)

    build_dashboard(r, path=out / "dashboard.html", context=md, dcf=dcf_res)
    arts["看板"] = str(out / "dashboard.html")

    reason = ""
    if dcf_res is not None:
        up = dcf_res.upside
        reason = (f"每股内在价值 {dcf_res.value_per_share:,.2f} 元 vs 现价 "
                  f"{dcf_res.price:,.2f} 元"
                  + (f"，空间 {up:+.1%}" if up is not None else "")
                  + f"（WACC {dcf_res.wacc:.2%}，终值占企业价值 "
                  f"{dcf_res.terminal_share:.1%}；判断项经 {approved_by} 核实）")

    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=stage,
        artifacts=arts, skipped=skipped, upheld=0,
        cost_usd=Decimal(str(cost)), route_reason=reason, phase="value")


def _write_full_report(code, r, brief, model, dcf_res, bundle, out=None, caveat=""):
    """在简报之上补出完整研报 —— 第 8 节要拿估值结果与前面各节对账。"""
    from ir_agent.analyst import FactCatalog, write_body

    cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=r.as_of,
                                  extra_periods=_catalog_periods(r))
    cat.evidence = _build_evidence(r, code)
    client = make_client(model=model)
    try:
        print("撰写完整研报（含估值一节）…", file=sys.stderr)
        full, _ = write_body(cat, client, code=code, period=r.period,
                             valuation_summary=(dcf_res.summary()
                                                + ("\n\n" + caveat if caveat else "")),
                             context=f"**已完成的分析简报**\n\n{brief}",
                             **{k: v or None for k, v in bundle.items()})
        # **估值结论同样要过证伪环节。** 辩论此前只跑在第一阶段的简报上，
        # 而第二阶段把 DCF 结果写进第 8 节就直接产出 —— 整条链上最需要被
        # 质疑的一层反而没有质疑者。实测代价（生益科技）: 总股本错一个数量
        # 级，每股内在价值算出 2507 万元、结论从「低估」翻成「高估 59%」，
        # 而三表勾稽、140 格复算、健全性检查、引文审计四道闸门全部放行 ——
        # 它们都在模型内部查一致性。质疑者是唯一被要求「找出站不住的地方」
        # 的角色，荒谬的量级恰恰是最容易被看出来的那种站不住。
        full, _upheld, debate_md, dnote = _debate_and_revise(
            cat, full, client, r.period)
        return full, client.total_cost_usd, "", debate_md, dnote
    except (AnalystRefused, ClaudeCliError) as e:
        dumped = _dump_refused(out, getattr(e, "raw", ""), WRITE_FAILED_REPORT) if out else ""
        tail = f"（最后一稿已落盘：{dumped}）" if dumped else ""
        return None, client.total_cost_usd, \
            f"完整研报（{e.__class__.__name__}: {str(e)[:160]}）{tail}", "", ""


def report(outcome: ResearchOutcome, out_dir: str | None = None,
           year: int | None = None) -> None:
    print()
    print(outcome.summary())
    acts = next_actions(outcome.stage, outcome.code,
                        outcome.artifacts.get("假设模板"),
                        out_dir=out_dir, year=year, phase=outcome.phase)
    if acts:
        print("\n下一步：")
        for i, act in enumerate(acts, 1):
            print(f"  {i}. {act}")
