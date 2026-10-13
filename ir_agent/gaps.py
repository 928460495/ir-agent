"""按分解式识别数据缺口，并分级采集。

采集顺序先近后远、先可核验后不可核验:

    ① 账本          已有事实
    ② 年报叙述章节   产能/产量的文字披露
    ②.5 公司公告     扩产/投产/提价等法定披露
    ③ 研报语料库    深度报告里的行业产能测算
    ④ 开放网络      SEMI 出货、基板报价、协会统计

**采集不到就说采集不到。** 封测稼动率 A 股基本不披露 —— 这是常态而非异常。
此时该因子标「无数据，取值为估计」，**绝不用代理指标偷偷顶替**（比如拿固定
资产周转率冒充稼动率）: 那会让一个估计伪装成测量，而读者分辨不出来。

②③是文本检索，命中因子的 `label`（如「稼动率」）**不等于**拿到了它的数值——
年报里出现这三个字，可能只是一句「稼动率保持稳定」而没有任何数字。因此命中
时只标「文字依据，待人工确认」，并把原文片段连同出处一并交出去，不代人下
结论：不然就是本模块要防的那种失败本身——让一个估计伪装成测量。

③的语料库不再只装券商研报。openweb 分支之后，corpus 里混入了代码抓回来的
开放网页，其中一部分分级落 `Tier.MEDIA`。`_match_report` 因此跳过 MEDIA
文档——把媒体错拔成"研报测算"是危害（一篇博客单独支撑了 DCF 里的一个假设），
漏判落到④或"无数据"只是召回损失。**这道闸门只在③自己身上**：Task 6 之后
④的门槛不再是来源等级（见 `web.supports_value` 文档字符串），而是有没有
被 `openweb.verify_finding` 核实过——③没有逐条核验 `Finding.claim` 那样的
动作可做（corpus 里的文档本来就是代码抓回来的全文，不是模型转述），所以
③继续用它原来那道基于 tier 的闸门，不能因为④换了门槛就跟着卸掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ir_agent.drivers import Decomposition, Factor
from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.sources.external import Document, Tier
from ir_agent.sources import conclusions
from ir_agent.sources.conclusions import Article, Conclusion, number_marks
from ir_agent.sources.openweb import TEXT_LIMIT
from ir_agent.sources.web import Finding, WebUnavailable

DISCLOSED = "已披露"
ANNUAL = "年报披露"     # ②年报叙述章节命中——文字依据，未必给出数值
ANNOUNCE = "公告披露"   # ②.5 公司公告命中——同上，文字依据未必给出数值
REPORT = "研报测算"     # ③研报语料库命中——同上
EXTERNAL = "外部来源"
MISSING = "无数据"

# 命中关键词后，往前后各摘这么多字给人看。摘的是原文，不是结论——
# 让读者自己判断这段话到底有没有给出数字，比我们代为下结论更诚实。
_EXCERPT_WINDOW = 40

# I3：真实联网跑出来的因子标签形如「有效产能变化率」「稼动率贡献」
# 「先进封装收入占比提升贡献」「原材料及折旧成本变化贡献」——这些
# 后缀是分解式的运算修饰（乘法链统一叫"变化率"，加法链统一叫"贡献"/
# "占比提升贡献"），根子是标签里带着单位/运算后缀，不是同义词问题：
# 年报只会写「有效产能」「稼动率」，不会逐字写「有效产能变化率」。
# 顺序按长度降序——「先进封装收入占比提升贡献」要整体剥掉「占比提升
# 贡献」，不能先被短的「贡献」剥成半吊子的「先进封装收入占比提升」。
_SUFFIXES = ("占比提升贡献", "变化贡献", "占比提升", "变化率", "贡献", "增速")


def _core_label(label: str) -> str:
    """剥掉因子标签里的单位/运算后缀，取核心词做文本匹配。

    方向仍要保守：**宁可漏判落到④，不可误判成"已披露"**。剥完后核心词
    过短（<2 字）就不剥，用原始标签去匹配——大概率落空退到④，但不会
    拿一两个字这种几乎到处都能命中的片段冒充"命中"，把一个巧合伪装成
    证据（这正是本模块要防的最严重失败模式）。

    终审复核第二轮（final-fix-D I-b）：上一轮把这个阈值从 2 提到 4，
    想靠"核心词够长"去挡"未披露稼动率数据"这类否定语境的误判——但
    实测证明这防不住：失败模式跟长度无关，是**否定语义**。「未披露
    **有效产能**的具体数据」里的「有效产能」有 4 字，够长，照样会被
    误判成"年报披露"。长度阈值防不住否定句，代价却是把「产能变化率」
    「单价变化率」「ASP变化率」……这批本仓库自己的规范因子名全部挤
    回退到原始标签匹配，四级采集阶梯对它们退化成「④或无数据」两级。
    阈值改回 2，否定语境的判断挪到真正命中的地方去做——见
    `_valid_hit`：在命中处的前后窗口里查否定词，命中在否定语境里就
    不算数，不管核心词剥得多长。
    """
    for suf in _SUFFIXES:
        if label.endswith(suf) and len(label) > len(suf):
            core = label[: -len(suf)]
            if len(core) >= 2:
                return core
    return label


# B3：标签里的括号是**举例或注解**，不是因子的名字。实跑（out/600183-subB）
# 里模型给的标签形如「覆铜板下游需求规模(服务器/通信/消费电子PCB用量)」，
# `_core_label` 只剥 `_SUFFIXES` 后缀、对这类标签原样返回，于是三个匹配器
# 拿整串去 89055 字正文里找，②②.5③ 三级同时归零 —— 而同一批正文里
# 「产能」出现 31 次、「覆铜板」98 次：**材料在，匹配器伸不到。**
#
# 「[^）)]*」而不是「.*?」：括号不闭合（`产能(稼动率`）时整体不匹配，
# 头退化成整串、落空退 ④ —— 退化方向保守。
_PAREN = re.compile(r"[（(][^）)]*[）)]")


def _core_candidates(label: str, search_term: str = "") -> list[str]:
    """按特指度降序的候选词，调用方命中即停。**绝不下探括号内。**

    L1 整串标签 → L2 去掉括号及其内容 → L3 头再剥 `_SUFFIXES` 后缀。

    **为什么不下探括号内**：括号里写的是举例或注解——
    `原材料成本(铜箔/电子级玻纤布/环氧树脂价格)` 的括号内枚举的是成本的
    **组成项**，`覆铜板下游需求规模(服务器/通信/消费电子PCB用量)` 的括号内
    是下游**行业名**。拿一个组成项或行业名的命中当成整个因子的证据，是
    归因错位。而实测「服务器」在真实公告语料里出现 **18 次**、「通信」4 次
    —— 下探括号内会让这个因子凭纯巧合拿到它唯一的「证据」，正是本模块
    注释里写的「把一个巧合伪装成证据」。见 `test_候选词绝不下探括号内`。

    **为什么判据不是长度**：实测「铜箔」2 字是真信号（命中 6 次，覆铜板的
    原材料）、「通信」2 字是巧合——长度分不开具体与泛化。`_core_label` 的
    文档字符串里已经记着一条同形教训：上一轮把阈值从 2 提到 4 想挡否定语境
    误判，防不住，因为失败模式跟长度无关。这次失败模式是泛化程度。所以这里
    不加新的过滤条件，只按特指度排序、让调用方命中即停。

    2 字下限沿用 `_core_label` 的理由：一两个字的片段几乎到处都能命中。

    **L4 = 模型给的检索词，在最底端不是顶端。** 「出货量」比整串标签更泛、
    不是更具体；放顶端会压过更具体的候选，颠倒本函数「按特指度排序」这条
    原则。它存在的理由恰恰是 L1–L3 全部落空（不带括号的冗长标签没有结构
    信号可切）。由模型决定、代码只查长度，见 `drivers.Factor.search_term`。
    """
    out: list[str] = []

    def add(c: str) -> None:
        if len(c) >= 2 and c not in out:
            out.append(c)

    add(label)                              # L1 整串标签
    head = _PAREN.sub("", label).strip()    # L2 去掉括号及其内容
    add(head)
    add(_core_label(head))                  # L3 头再剥后缀
    add(search_term)                        # L4 模型给的检索词，最后才试
    return out


# final-fix-D I-b：命中处如果落在否定语境里，就不算命中——「未披露」
# 「未单独列示」这类短语几乎总是紧贴着被否定的那个词，用命中点前后
# 一个摘要窗口的宽度去查就够了，不需要更复杂的句法分析。宁可因为
# 窗口覆盖了不相关的另一句话而多退一次（④或无数据），也不可能把
# "未披露"读成"已披露"。
_NEGATIONS = ("未披露", "未单独列示", "未公开", "不适用", "无法计算",
              "未予披露", "不予披露", "未予公开", "无法确定")


def _negated(window: str) -> bool:
    return any(neg in window for neg in _NEGATIONS)


@dataclass
class FactorData:
    factor: Factor
    status: str = MISSING
    # EXTERNAL：`conclusions.Conclusion` 列表（模型综合结论 + 引用文章，2026-10-13
    # 起）。`assume._match_finding` / `summary` 按 claim/url/publisher 读，与旧的
    # `web.Finding` 同名，两种都能放。
    findings: list = field(default_factory=list)
    note: str = ""
    # ANNUAL/ANNOUNCE/REPORT 命中时的真实出处——供 assume.py 在模型引用摘出的
    # 原文片段时，代码自动补上可核验的 URL/抓取日期（模型自己不接触
    # 这两样，同一条"模型写引用，代码填事实"的契约）。
    # EXTERNAL 状态不用这两个字段：那种情况下 findings 里可能有好几条，
    # 各自带自己的 url/日期，模型引的是哪条要看它引了哪条 claim，
    # 没有一个"唯一"来源可以预先填在这里。
    source_url: str = ""
    source_retrieved: date | None = None
    # fix-excerpt-match：命中②③时摘出的原文片段本身，独立于 `note`。
    #
    # `note` 是拼给人看的展示文本——「「摘录」——出处（说明）」这个格式
    # 是为了在待核实清单/提示词表格里读着顺眼，不是为了让机器从里面
    # 反解。600584 实测：`note` 里的年报原文恰好嵌着一对中文弯引号
    # （"…“创新提效、行深致远”…"），`assume._match_excerpt` 曾经用
    # 非贪婪正则从 `note` 里把摘录抠出来，一遇到年报原文内部的收尾
    # 引号就把摘录腰斩，模型逐字引用的证据（恰恰是我们自己采集给它
    # 的那一段）因此被判定"匹配不到"，连续 3 次驳回后拖累整轮降级成
    # 两标量模式。教训是：**给人看的展示文本不该同时承担"可被机器
    # 精确取回"的职责**——年报正文里出现引号是常态，把结构化数据塞进
    # 格式化字符串、再指望正则把它原样抠回来，迟早会被这类边缘情况
    # 击穿。机器要用的原文必须是独立字段，跟着数据走，`_match_excerpt`
    # 只认这个字段，不再碰 `note` 一个字符。
    source_excerpt: str = ""
    # Task 6 fix wave 2（复审 Fix C）：这个因子的④证据里，有没有哪条
    # 命中在核验时经历过主机安全校验降级——与下面 collect() 里拼进
    # note 那句人话（「其中 N 条命中时主机安全校验降级…」）算的是
    # 同一个 `any(v.host_check_degraded for v in passed)`，只是这里
    # 存成结构化字段，不是字符串。理由见 assume.py `_match_excerpt`
    # 上面那段（这里同一份，往回看几行）：下游代码不该也不能去解析
    # `note` 文本，凡是要让机器读到的事实，都得有自己独立的字段。
    # 只在 EXTERNAL 分支赋值。**消费者是 `full._factor_host_check_note`**
    # （行号会漂，按函数名找），它把本字段聚合成产物清单里「N 个因子采集时
    # 主机安全校验降级」那一行——与 `_openweb_notes` 的「N 篇入库时…」
    # 是同一份事实、同一句措辞，分开计数只因为计数单位不同（因子 vs
    # 文档）、且来自两个不同的 fetch 调用点。
    # 它读的是这个布尔字段而**不是** note 文本：理由见 assume.py
    # `_match_excerpt` 上面那段真实事故（下游拿正则啃 note，被年报里的
    # 引号截断）。要改 note 的措辞时不必担心这条聚合会跟着坏。
    host_check_degraded: bool = False


def identify(d: Decomposition, ledger: FactLedger, as_of: date) -> list[Factor]:
    """分解式里账本还没有的因子。

    账本里存在该 key 但值的披露时点晚于 as_of（LookAheadError）也按「没有」
    处理 —— 时点守卫是全系统最关键的防线，一个 as_of 之后才披露的数字，
    在这个时点上就是不存在的，不能因为账本里"有"就当作已披露。
    """
    out = []
    for f in d.factors:
        try:
            ledger.get(f.key, _latest(ledger, f.key), as_of=as_of)
        except (KeyError, LookAheadError):
            out.append(f)
    return out


def _latest(ledger: FactLedger, key: str) -> str:
    pers = sorted(p for k, p in ledger._facts if k == key)   # noqa: SLF001
    return pers[-1] if pers else ""


# 数据线索里的账本 key 声明：**只认显式的 `[[key]]`**，不认线索里顺带提到的
# 英文词。生益科技 2026-10-13 的线索写着「capex、fixed_asset等账本key……需采集
# 稼动率数据」—— 提到 capex 不等于「稼动率就是 capex」，按出现就绑定会把因子
# 错绑到一个不相干的量上、并跳过采集。spec (c) 要的「优先分解成已有数据能支撑
# 的因子、直接决定 gaps.collect 的命中率」由这一级兑现。
_KEY_DECL = re.compile(r"\[\[([A-Za-z_][\w.]*)(?:@[^\]]*)?\]\]")


def ledger_refs(hint: str, ledger: FactLedger, as_of: date
                ) -> tuple[list[str], list[str]]:
    """数据线索里 `[[key]]` 声明的账本 key。返回 (有的 `key@期间`, 没有的 key)。

    时点守卫与 `identify` 同一条：as_of 之后才披露的视为没有。按整词比对，
    `industry.revenue` 不会误中 `industry.revenue_yoy`。
    """
    have: list[str] = []
    miss: list[str] = []
    for k in dict.fromkeys(_KEY_DECL.findall(hint or "")):
        per = _latest(ledger, k)
        try:
            ledger.get(k, per, as_of=as_of)
            have.append(f"{k}@{per}")
        except (KeyError, LookAheadError):
            miss.append(k)
    return have, miss


def _valid_hit(text: str, core: str) -> str | None:
    """在 text 里找 core 一处**不落在否定语境里**的出现，摘出前后原文
    片段；找不到（含"所有出现都在否定语境里"）返回 None。

    **不能只看第一次出现就下结论。** 同一段文字里，核心词可能既出现在
    「未披露 XX」这种否定句里，也在别处真正给出了数据——只看第一次
    命中，真命中会被前面一次巧合的否定命中连坐拖累（宁可漏判，但不该
    漏成这样）；反过来，若唯一的出现就落在否定窗口里，就不能算数——
    这正是 final-fix-D I-b 要堵的失败模式：核心词剥得再长也挡不住
    「未披露有效产能的具体数据」这种紧贴否定词的命中。
    """
    start = 0
    while True:
        i = text.find(core, start)
        if i < 0:
            return None
        lo = max(0, i - _EXCERPT_WINDOW)
        hi = min(len(text), i + len(core) + _EXCERPT_WINDOW)
        window = text[lo:hi]
        if not _negated(window):
            body = window.replace("\n", "")
            return ("…" if lo > 0 else "") + body + ("…" if hi < len(text) else "")
        start = i + len(core)


def _match_annual(sections, label: str,
                  search_term: str = "") -> tuple[str, str, str] | None:
    """②年报叙述章节里是否提到该因子。

    只做包含判断，不抽数字——命中「稼动率」三个字不等于拿到了稼动率的
    数值，抽错一个数比没抽更糟。返回值交给调用方判断，不在这里下结论。

    **匹配用按特指度降序的候选词，命中即停，不是单一核心词。** 原始标签
    「有效产能变化率」这种带单位/运算后缀的整串不会逐字出现在年报里；
    「原材料成本(铜箔/…)」这种带括号注解的更不会——见 `_core_candidates`。
    落在否定语境（"未披露"等）里的出现不算命中，见 `_valid_hit`。

    **第三个返回值是命中的那个候选词。** `collect` 靠它判断命中来自哪一级
    ——相等于 `Factor.search_term` 就说明是靠模型给的检索词命中的，那件事
    要在产物清单上说出来（见 `_via_note`）。
    """
    if sections is None:
        return None
    # **候选词在外层，章节在内层。** 顺序写反的话，一个较泛的候选词在
    # 靠前的章节里命中会压过更具体的候选词在靠后章节里的命中——等于
    # 放弃了「按特指度排序」这整个决定，且套件会全绿。
    # 见 `test_候选词在外层容器在内层`。
    for core in _core_candidates(label, search_term):
        for s in sections.sections.values():
            hit = _valid_hit(s.text, core)
            if hit is not None:
                return hit, f"《{s.label}》", core
    return None


def _match_report(corpus, label: str,
                  search_term: str = "") -> tuple[str, Document, str] | None:
    """③研报语料库里是否提到该因子，判据与 _match_annual 同理。

    **返回命中的原文片段与它所在的 Document 本身**，不只是格式化好的
    引用字符串——调用方（collect）需要 Document 的 url/retrieved 去
    构造 assume.py 那边可以直接核验的 EXTERNAL 依据：模型引用 note
    里摘出的这段原文时，代码要能把真实出处（而不是模型自己声称的）
    填回去，同一条"模型写引用，代码填事实"的契约。

    **跳过 Tier.MEDIA 文档。** 这个函数写下来的时候语料库里只有券商
    研报（全部 SELLSIDE），没有分级判断也无妨。openweb 分支之后，
    corpus 混入了代码抓回来的开放网页，其中相当一部分分级落 MEDIA——
    这里若不设一道闸门，一篇分级落 MEDIA 的网络博客只要文本命中，就会
    被当成"研报测算"直接喂给 assume.py 当 EXTERNAL 依据写进 DCF 假设，
    且比④更糟：③命中即终止（先近后远，不会再往下走④）。**这道闸门
    与④（`web.supports_value`）不是同一个机制**——Task 6 之后④的门槛
    是"有没有被 `openweb.verify_finding` 核实过"，不是来源等级；③这里
    没有逐条核验 `Finding.claim` 那样的动作可做（corpus 里的文档是代码
    抓回来的全文本身），所以继续按 tier 把关，两道闸门各自成立，不是
    互为依据。命中该跳过的 MEDIA 文档时不返回 None 就直接结束，而是
    继续扫描其余文档——同一因子仍可能在别的 SELLSIDE/OFFICIAL 文档里
    正常命中，落空了才退到④或"无数据"，这正是代价不对称原则要求的
    方向：宁可多退一级，不可把媒体错拔成研报依据。

    **第三个返回值是命中的那个候选词。** `collect` 靠它判断命中来自哪一级
    ——相等于 `Factor.search_term` 就说明是靠模型给的检索词命中的，那件事
    要在产物清单上说出来（见 `_via_note`）。
    """
    if corpus is None:
        return None
    # 候选词在外层，文档在内层——理由同 `_match_annual`，见
    # `test_候选词在外层容器在内层`。
    for core in _core_candidates(label, search_term):
        for d in corpus.docs:
            if d.tier is Tier.MEDIA:
                continue
            hit = _valid_hit(d.text, core)
            if hit is not None:
                return hit, d, core
    return None


def _match_announce(corpus, label: str,
                    search_term: str = "") -> tuple[str, Document, str] | None:
    """②.5 公司公告里是否提到该因子，判据与 `_match_report` 同理。

    **只看 `Tier.COMPANY` 文档。** ②.5 是公告这一级；研报与媒体各有
    自己的一级（③ 与 ④），从这里命中会让状态标签说错话。

    公告排在研报之前，因为它是公司权威披露、且由确定性接口取回（模型只
    挑下标、不给网址），比两级模型中介的来源更近。

    **第三个返回值是命中的那个候选词。** `collect` 靠它判断命中来自哪一级
    ——相等于 `Factor.search_term` 就说明是靠模型给的检索词命中的，那件事
    要在产物清单上说出来（见 `_via_note`）。
    """
    if corpus is None:
        return None
    # 候选词在外层，文档在内层——理由同 `_match_annual`，见
    # `test_候选词在外层容器在内层`。
    for core in _core_candidates(label, search_term):
        for d in corpus.docs:
            if d.tier is not Tier.COMPANY:
                continue
            hit = _valid_hit(d.text, core)
            if hit is not None:
                return hit, d, core
    return None


def _via_note(core: str, f: Factor) -> str:
    """命中来自模型给的检索词时，在 `note` 末尾说出来；否则空串。

    **这是放开检索词闸门的对价。** 闸门只查长度（见
    `drivers.Factor.search_term`），所以一个泛词会一路走到底：实测
    「覆铜板」在真实公告语料里命中 98 次、「产能」31 次、「服务器」18 次，
    那些命中与因子无关，却会被标成「年报披露」。代码不再拦，那就至少要
    让读者分得清「这条证据是代码按标签结构找到的」与「这条是靠模型指的
    路找到的」。同源做法见 `Corpus.media_cited` 的「警告不驳回」与
    `host_check_degraded` 的「不阻断但必须一路传到产物清单」。

    **按字符串比较，不按位置。** `_core_candidates` 会去重：若标签本身就是
    「产能」、检索词也给「产能」，它根本不会作为第四项出现，数位置会在这种
    情形下说错话。见 `test_标注按字符串比较而非按位置`。
    """
    if f.search_term and core == f.search_term:
        return f"（靠模型给的检索词「{f.search_term}」命中，非标签结构）"
    return ""


def collect(client, decomps: list[Decomposition], ledger: FactLedger,
            corpus, industry: str, as_of: date,
            sections=None, annual_url: str = "") -> dict[str, FactorData]:
    """逐因子采集。已在账本的跳过；缺的依次尝试②年报、③研报、④外部网络。

    ②或③命中就不再往下走——先近后远，同时省一次联网调用。`sections`/
    `corpus` 都可能是 None（调用方没取到年报或语料库），两级匹配函数
    各自容错，None 时直接落空、继续试下一级。

    `annual_url` 是年报公告本身的下载地址（`r.annual_url`）——②命中时
    连同摘出的原文片段一起记进 `FactorData.source_url`，供 assume.py
    在模型逐字引用这段原文时补上真实、可点开核验的出处。不传（默认
    空串）时行为不变，只是②命中的因子将来引用不到这条出口，不影响
    现有调用方。
    """
    out: dict[str, FactorData] = {}
    for d in decomps:
        gaps = {f.key for f in identify(d, ledger, as_of)}
        for f in d.factors:
            if f.key in out:
                continue
            if f.key not in gaps:
                out[f.key] = FactorData(factor=f, status=DISCLOSED)
                continue
            have, _ = ledger_refs(f.source_hint, ledger, as_of)
            if have:
                out[f.key] = FactorData(
                    factor=f, status=DISCLOSED,
                    note="账本 " + "、".join(f"[[{x}]]" for x in have)
                         + "（模型在数据线索中声明，口径是否与因子一致待人工确认）")
                continue

            hit = _match_annual(sections, f.label, f.search_term)
            if hit is not None:
                excerpt, src, core = hit
                out[f.key] = FactorData(
                    factor=f, status=ANNUAL,
                    note=f"「{excerpt}」——{src}"
                         "（文字依据，是否给出具体数值待人工确认）"
                         + _via_note(core, f),
                    source_url=annual_url, source_retrieved=as_of,
                    # 与 note 里包在「」中的那段是同一份文字——note 是
                    # 拼给人看的，这里是给 assume._match_excerpt 用的，
                    # 两者必须来自同一个 excerpt 变量，不能事后各改各的。
                    source_excerpt=excerpt)
                continue

            hit = _match_announce(corpus, f.label, f.search_term)
            if hit is not None:
                excerpt, doc, core = hit
                out[f.key] = FactorData(
                    factor=f, status=ANNOUNCE,
                    note=f"「{excerpt}」——{doc.cite()}"
                         "（文字依据，是否给出具体数值待人工确认）"
                         + _via_note(core, f),
                    source_url=doc.url, source_retrieved=doc.retrieved,
                    source_excerpt=excerpt)
                continue

            hit = _match_report(corpus, f.label, f.search_term)
            if hit is not None:
                excerpt, doc, core = hit
                out[f.key] = FactorData(
                    factor=f, status=REPORT,
                    note=f"「{excerpt}」——{doc.cite()}"
                         "（文字依据，是否给出具体数值待人工确认）"
                         + _via_note(core, f),
                    source_url=doc.url, source_retrieved=doc.retrieved,
                    source_excerpt=excerpt)
                continue

            try:
                got = conclusions.search(client, f.label, f.key, industry, as_of)
            except WebUnavailable as e:
                # 能力边界不是故障，整轮研究不该因此停摆。措辞里显式写
                # 「不能联网」而不是只转述异常原文。
                out[f.key] = FactorData(
                    factor=f, status=MISSING,
                    note=f"不能联网 —— {e} —— 取值为估计，无数据支撑")
                continue

            # ── ④：模型综合结论 + 引用文章（spec 2026-10-13 ②）─────
            # 结论可以综合、可以改写；代码只做了机械比对，结果全是标注。
            # 有结论就落 EXTERNAL（决定 A：带 ⚠ 的也能作依据，门③ 那一行
            # 带标记），交给门② 的人复核（决定 B）。
            if not got.items:
                why = "；".join(got.dropped) or "没搜到可用的结论"
                out[f.key] = FactorData(factor=f, status=MISSING,
                                        note=f"{why} —— 取值为估计")
                continue
            arts: list[Article] = []
            for c in got.items:
                for a in c.articles:
                    if all(a is not x for x in arts):
                        arts.append(a)
            for a in arts:
                if a.opened:
                    # **入库的是代码抓回的全文，不是模型的结论。** 辩论② 与
                    # 撰写简报引的是文章原文 ——「不得引用本系统的分析结论」
                    # 那条规矩照样成立。
                    corpus.add_if_visible(Document(
                        doc_id=f"web_{f.key}_{len(corpus.docs)}",
                        url=a.final_url, title=f"{f.label}：文章{a.no}",
                        publisher=a.publisher, tier=a.tier,
                        published=a.published, retrieved=as_of,
                        text=a.text, prompt_limit=TEXT_LIMIT,
                        date_source="model"))
            flagged = sum(1 for c in got.items if c.flags())
            note = (f"{len(got.items)} 条模型综合结论，待人工复核"
                    + (f"（其中 {flagged} 条带 ⚠）" if flagged else ""))
            if got.dropped:
                note += "；" + "；".join(got.dropped)
            degraded = any(a.degraded for a in arts)
            if degraded:
                n = sum(1 for a in arts if a.degraded)
                note += (f"；其中 {n} 篇文章抓取时主机安全校验降级（本机 DNS "
                         "被代理劫持，IP 层校验无从进行）——内网地址拦截本轮"
                         "依赖代理侧")
            out[f.key] = FactorData(factor=f, status=EXTERNAL,
                                    findings=got.items, note=note,
                                    host_check_degraded=degraded)
    return out


SCHEMA = 2


def _article_json(a: Article) -> dict:
    return {"no": a.no, "url": a.url, "publisher": a.publisher,
            "published": a.published.isoformat(), "final_url": a.final_url,
            "tier": a.tier.value, "opened": a.opened, "why_not": a.why_not,
            "degraded": a.degraded, "text": a.text}


def to_json(data: dict[str, FactorData]) -> dict:
    """因子采集结果落盘（`factors.json`），schema 2。键是 `Factor.key`。

    **为什么要落盘。** 采集发生在第二次调用（`run_brief`），用它的判断项在第三
    次调用（`run_assume`），中间隔一个人工门、是两个进程。

    **schema 2（2026-10-13）**：EXTERNAL 存模型综合结论 + 引用文章（含代码
    抓回的正文 —— 第三次调用要拿它重算数字标注）；数字标注本身**不存**，读回
    时按（人可能改过的）结论文字重算，存了就会在人改完之后停在改之前。另补存
    `source_excerpt`：此前不存，第三次调用里模型逐字引用年报摘录会匹配不到。
    """
    def _fd(fd: FactorData) -> dict:
        return {"status": fd.status, "note": fd.note,
                "source_url": fd.source_url,
                "source_retrieved": (fd.source_retrieved.isoformat()
                                     if fd.source_retrieved else None),
                "source_excerpt": fd.source_excerpt,
                "host_check_degraded": fd.host_check_degraded,
                "conclusions": [{"claim": c.claim, "dropped": list(c.dropped),
                                 "articles": [_article_json(a) for a in c.articles]}
                                for c in fd.findings
                                if isinstance(c, Conclusion)]}
    return {"schema": SCHEMA, "factors": {k: _fd(fd) for k, fd in data.items()}}


def from_json(raw: dict, decomps: list[Decomposition]
              ) -> tuple[dict[str, FactorData], list[str]]:
    """读回 `factors.json`，按因子键与**当前**分解式对上。返回 (数据, 说明)。

    人可以在两次调用之间改 `drivers.md`（I2）与 `factors.json`（决定 B）——
    分解式里没有的因子丢弃并说出来；没采集过的按「未采集」处理；结论被人删光
    的 EXTERNAL 落「无数据」。旧格式（没有 schema 2）抛 ValueError，由
    `full._read_factors` 接住、说「读不回，重跑 --brief」。
    """
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ValueError(f"factors.json 是旧格式（没有 \"schema\": {SCHEMA}）"
                         "—— 重跑 --brief 生成新格式")
    factors = {f.key: f for d in decomps for f in d.factors}
    out: dict[str, FactorData] = {}
    notes: list[str] = []
    for key, x in (raw.get("factors") or {}).items():
        f = factors.get(key)
        if f is None:
            notes.append(f"因子采集结果里有「{key}」，但当前分解式没有这个因子"
                         "（drivers.md 在两次调用之间被改过？）—— 已丢弃")
            continue
        concl = []
        for y in x.get("conclusions") or []:
            arts = [Article(no=str(z["no"]), url=z["url"],
                            publisher=z.get("publisher", ""),
                            published=date.fromisoformat(z["published"]),
                            final_url=z.get("final_url", ""),
                            tier=Tier(z.get("tier", Tier.MEDIA.value)),
                            opened=bool(z.get("opened")), text=z.get("text", ""),
                            why_not=z.get("why_not", ""),
                            degraded=bool(z.get("degraded")))
                    for z in y.get("articles") or []]
            c = Conclusion(factor_key=key, claim=y["claim"], articles=arts,
                           dropped=list(y.get("dropped") or []))
            c.marks = number_marks(c.claim, c.articles)
            concl.append(c)
        status, note = x.get("status", MISSING), x.get("note", "")
        if status == EXTERNAL and not concl:
            status, note = MISSING, "门② 删除了全部联网结论 —— 取值为估计"
        sr = x.get("source_retrieved")
        out[key] = FactorData(
            factor=f, status=status, note=note, findings=concl,
            source_url=x.get("source_url", ""),
            source_retrieved=date.fromisoformat(sr) if sr else None,
            source_excerpt=x.get("source_excerpt", ""),
            host_check_degraded=bool(x.get("host_check_degraded")))
    for key, f in factors.items():
        if key not in out:
            notes.append(f"因子「{f.label}」没有采集结果 —— 按「未采集」处理")
    return out, notes


def summary(data: dict[str, FactorData]) -> str:
    """给人看的采集结果。**遗漏必须可见** —— 这正是分解的价值所在。"""
    lines = ["因子数据采集结果", ""]
    lines.append("| 因子 | 状态 | 来源 / 说明 |")
    lines.append("| --- | --- | --- |")
    for fd in data.values():
        if fd.status == EXTERNAL:
            src = "；".join(
                (f"「{x.claim}」（文章 "
                 + "、".join(f"{a.no}:{a.publisher}" for a in x.articles)
                 + ("，⚠" if x.flags() else "") + "）")
                if isinstance(x, Conclusion) else f"{x.publisher}（{x.url}）"
                for x in fd.findings[:3]).replace("|", "｜")
            # 复审 Fix A：`note` 在 EXTERNAL 分支上装的是代码观测到的
            # 事实（仅媒体支撑、主机安全校验是否降级、还有几条转述没
            # 核实住）——这些是 Task 6 明确要求人在批准清单上看得见的
            # 东西（见 task-6-brief.md「仅媒体支撑的因子在清单上被
            # 标注」）。此前这里只渲染模型自报的 publisher/url，note
            # 被整个丢在地上，人读到的只剩模型自己的说法。两者都要摆
            # 出来，不能顶替，也不能只留一个。
            if fd.note:
                src = f"{src}｜{fd.note}"
        else:
            src = fd.note or "—"
        lines.append(f"| {fd.factor.label} | {fd.status} | {src} |")

    # ②②.5③是文字命中，不是已核实的数值——必须与「已披露」「外部来源」
    # 分开摆出来，不能让状态列里挨在一起的几行字看起来证明力一样。
    textual = [fd.factor.label for fd in data.values()
               if fd.status in (ANNUAL, ANNOUNCE, REPORT)]
    if textual:
        lines += ["",
                  f"📄 以下因子仅命中年报叙述、公司公告或研报文字"
                  f"（年报披露/公告披露/研报测算），"
                  f"是文字依据，待人工确认原文是否给出具体数值——"
                  f"命中关键词不等于拿到了数值：{'、'.join(textual)}。"]

    gaps = [fd.factor.label for fd in data.values() if fd.status == MISSING]
    if gaps:
        lines += ["", f"⚠ 以下因子无数据支撑，取值为估计：{'、'.join(gaps)}。",
                  "**绝不用代理指标顶替** —— 拿固定资产周转率冒充稼动率会让"
                  "一个估计伪装成测量，而读者分辨不出来。"]
    return "\n".join(lines)
