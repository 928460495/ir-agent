"""缺口识别与分级采集。**采集不到就说采集不到，绝不用代理指标顶替。**"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent.drivers import Decomposition, Factor, Op
from ir_agent.gaps import FactorData, collect, identify, summary
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.annual_sections import Section, SectionSet
from ir_agent.sources.external import Corpus, Document, Tier
from ir_agent.sources.web import Finding

D = Decimal
AS_OF = date(2026, 9, 7)


def _f(key, label, unit="变化率"):
    return Factor(key=key, label=label, unit=unit, source_hint="研报")


def _rev():
    return Decomposition(target="revenue", op=Op.MULTIPLY,
                         factors=[_f("capacity", "产能"),
                                  _f("utilization", "稼动率"),
                                  _f("asp", "单价")])


def _led(*keys):
    led = FactLedger()
    for k in keys:
        led.put(Fact(key=k, value=D("0.1"), unit="ratio", period="2025FY",
                     as_of=date(2026, 4, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


class _Client:
    def __init__(self, *r):
        self.replies, self.prompts = list(r), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0) if self.replies else ""


class _ExplodingClient:
    """②③命中时根本不该走到 search()——用会炸的假 client 钉住这一点。"""

    def __call__(self, p):
        raise AssertionError("②③已命中，不该再联网")


def _sections(*rows):
    """rows: (key, label, text)。"""
    out = SectionSet(code="test", pages=99)
    for i, (key, label, text) in enumerate(rows):
        out.sections[key] = Section(key=key, label=label, heading=label,
                                    page=i + 1, text=text)
    return out


def _doc(text, title="行业深度报告", tier=Tier.SELLSIDE, doc_id="d1"):
    return Document(doc_id=doc_id, url="https://research.example/x",
                    title=title, publisher="某券商研究所", tier=tier,
                    published=date(2026, 6, 1), retrieved=AS_OF, text=text)


def _collect_one_factor(G, corpus):
    """跑一次只有一个因子、且该因子在账本里缺失的 collect。

    ②③②.5 都不会命中（sections=None、corpus 里没有 COMPANY/SELLSIDE
    文档提到它），所以一定走到 ④。
    """
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.ledger import FactLedger

    d = Decomposition(target="revenue_growth", op=Op.MULTIPLY,
                      factors=[Factor(key="capacity", label="名义产能",
                                      unit="变化率", source_hint="")])
    return G.collect(lambda p: "", [d], FactLedger(), corpus, "电子/元件",
                     AS_OF, sections=None, annual_url="")


def test_识别账本里没有的因子():
    gaps = identify(_rev(), _led("capacity"), AS_OF)
    assert [f.key for f in gaps] == ["utilization", "asp"]


def test_账本齐全则无缺口():
    assert identify(_rev(), _led("capacity", "utilization", "asp"), AS_OF) == []


def test_采集不到标为无数据():
    """封测稼动率 A 股基本不披露，这是常态而非异常。"""
    data = collect(_Client("", "", ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["capacity"].status == "已披露"
    assert data["utilization"].status == "无数据"
    assert "估计" in data["utilization"].note


def test_不可联网时不中断只标无数据():
    """联网不可用是能力边界，不是故障 —— 整轮研究不该因此停摆。"""
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    W.WEB_CAPABLE = False
    try:
        data = collect(_Client(), [_rev()], _led("capacity"),
                       Corpus(as_of=AS_OF), "封测", AS_OF)
        assert data["utilization"].status == "无数据"
        assert "不能联网" in data["utilization"].note
    finally:
        W.WEB_CAPABLE = old


def test_摘要把无数据的因子挑出来():
    """**遗漏必须可见** —— 这正是分解的价值所在。"""
    data = collect(_Client("", "", ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    txt = summary(data)
    assert "稼动率" in txt and "无数据" in txt
    assert "绝不用代理指标" in txt


def test_已有该因子但披露时点晚于as_of仍算缺口():
    """Task 6 简报的两个用例都没盖住这条裁定：账本里"有" capacity，
    但它的 as_of 晚于请求时点，在这个时点上就该算没有 —— 时点守卫
    优先于"账本里有没有这个 key"。"""
    led = FactLedger()
    led.put(Fact(key="capacity", value=D("0.1"), unit="ratio",
                 period="2025FY", as_of=date(2026, 9, 20),
                 source_id="t", method=Method.COMPUTED))
    gaps = identify(_rev(), led, AS_OF)
    assert "capacity" in [f.key for f in gaps]


def test_年报叙述命中因子即不再联网():
    """②命中就不该走④——用会炸的 client 钉住。asp 提前放进账本，避免
    它触发额外的联网调用，干扰这条测试想验证的东西。"""
    sections = _sections(("core_competency", "核心竞争力分析",
                          "公司持续推进良率提升，稼动率维持在行业前列，"
                          "具体测算详见内部管理报表。"))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   Corpus(as_of=AS_OF), "封测", AS_OF, sections=sections)
    fd = data["utilization"]
    assert fd.status == "年报披露"
    assert "稼动率" in fd.note
    assert "核心竞争力分析" in fd.note


def test_研报语料命中因子即不再联网():
    """③命中同理，且没有年报可用（sections=None）时也要能走到这一级。"""
    corpus = Corpus(as_of=AS_OF)
    corpus.add(_doc("我们测算行业稼动率将于下半年回升，具体假设见测算表。"))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   corpus, "封测", AS_OF)
    fd = data["utilization"]
    assert fd.status == "研报测算"
    assert "稼动率" in fd.note
    assert "某券商研究所" in fd.note


def test_年报优先于研报():
    """先近后远：两级都命中时用年报，不用研报。"""
    sections = _sections(("core_competency", "核心竞争力分析", "稼动率保持稳定"))
    corpus = Corpus(as_of=AS_OF)
    corpus.add(_doc("稼动率预计回升"))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   corpus, "封测", AS_OF, sections=sections)
    assert data["utilization"].status == "年报披露"


def test_sections与corpus为None时不崩溃():
    """调用方没取到年报或语料库都要容错，直接落到④。"""
    data = collect(_Client("", ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF, sections=None)
    assert data["utilization"].status == "无数据"


# ── fix-factor-basis：②③命中要连带记下真实出处 ───────────────────
#
# assume.py 那边需要在模型逐字引用 note 里摘出的原文片段时，把真实
# 出处（而不是模型自己声称的）代码填回去——这是"模型写引用，代码填
# 事实"契约在②③两级的落地，FactorData 新增的 source_url/
# source_retrieved 就是给这件事用的。

def test_年报命中记下年报公告本身的下载地址():
    """②命中时 source_url 应该是调用方传入的 annual_url（年报公告的
    真实下载地址），不是编出来的——不传时（默认空串）也不该崩，只是
    这条因子将来引用不到"证据引用"这条出口。"""
    sections = _sections(("core_competency", "核心竞争力分析",
                          "公司持续推进良率提升，稼动率维持在行业前列。"))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   Corpus(as_of=AS_OF), "封测", AS_OF, sections=sections,
                   annual_url="https://static.cninfo.com.cn/annual.pdf")
    fd = data["utilization"]
    assert fd.source_url == "https://static.cninfo.com.cn/annual.pdf"
    assert fd.source_retrieved == AS_OF


def test_不传annual_url时年报命中的source_url为空不崩溃():
    sections = _sections(("core_competency", "核心竞争力分析", "稼动率维持在行业前列。"))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   Corpus(as_of=AS_OF), "封测", AS_OF, sections=sections)
    assert data["utilization"].source_url == ""


def test_研报命中记下该文档自己的真实url与抓取日期():
    """③命中的 source_url/source_retrieved 应该是那篇 Document 自己的
    url/retrieved，不是年报的，也不是这一轮的 as_of——不同研报可能是
    不同时间抓取的不同文档。"""
    corpus = Corpus(as_of=AS_OF)
    corpus.add(_doc("我们测算行业稼动率将于下半年回升。"))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   corpus, "封测", AS_OF)
    fd = data["utilization"]
    assert fd.source_url == "https://research.example/x"
    assert fd.source_retrieved == AS_OF          # _doc() 里 retrieved=AS_OF


def test_摘要区分文字依据与已核实数值():
    """summary() 要把「年报披露/研报测算」标成文字依据待人工确认，
    与已核实的外部数值分开——不能让读者把两者混为一谈。"""
    sections = _sections(("core_competency", "核心竞争力分析", "稼动率保持稳定"))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   Corpus(as_of=AS_OF), "封测", AS_OF, sections=sections)
    txt = summary(data)
    assert "年报披露" in txt
    assert "文字依据" in txt and "待人工确认" in txt


# ── I3：真实因子标签带单位/运算后缀，逐字匹配在②③两级必然落空 ──
#
# 真实联网跑出来的因子标签形如「有效产能变化率」「稼动率贡献」——这些
# 后缀是分解式的运算修饰（乘法链统一叫"变化率"，加法链统一叫"贡献"/
# "占比提升贡献"），不会逐字出现在年报或研报原文里，年报只会写「有效
# 产能」「稼动率」。不剥后缀，②③两级命中永远落空，四级阶梯退化成
# 「④开放网络 或 无数据」两级，先近后远、先可核验后不可核验的设计
# 形同虚设。

def test_真实因子标签剥后缀才能命中年报():
    """用报告里实测出现的真实标签复现：不剥后缀时，年报原文里的
    「有效产能」「ASP」三个字不可能匹配上「有效产能变化率」
    「ASP变化率」这种整串——这条测试在修复前必须失败。

    （final-fix-D I-b：阈值改回 2，短核心词的否定语境保护挪到
    `_valid_hit` 去做，不再靠长度阈值兜底，见下面几条否定语境的
    测试。）"""
    factors = [_f("capacity", "有效产能变化率"), _f("asp", "ASP变化率")]
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=factors)
    sections = _sections(("core_competency", "核心竞争力分析",
                          "报告期内公司有效产能维持满载运行，ASP保持稳定。"))
    data = collect(_ExplodingClient(), [d], _led(), Corpus(as_of=AS_OF),
                   "封测", AS_OF, sections=sections)
    assert data["capacity"].status == "年报披露"
    assert data["asp"].status == "年报披露"


def test_真实因子标签剥后缀才能命中研报():
    factors = [_f("util", "稼动率贡献"), _f("mix", "先进封装收入占比提升贡献")]
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=factors)
    corpus = Corpus(as_of=AS_OF)
    corpus.add(_doc("我们测算稼动率将逐季回升，先进封装收入占比持续提升，"
                    "带动毛利率修复。"))
    data = collect(_ExplodingClient(), [d], _led(), corpus, "封测", AS_OF)
    assert data["util"].status == "研报测算"
    assert data["mix"].status == "研报测算"


def test_原材料及折旧成本变化贡献剥后缀后仍能命中():
    """后缀有多种组合（"变化贡献"与单独的"贡献"都要认），且要剥最长
    的那个匹配，不能剥出「原材料及折旧成本变化」这种半吊子核心词。"""
    f = _f("input_cost", "原材料及折旧成本变化贡献")
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[f])
    sections = _sections(("md_a", "管理层讨论与分析",
                          "原材料及折旧成本较上年有所上升，拖累毛利率。"))
    data = collect(_ExplodingClient(), [d], _led(), Corpus(as_of=AS_OF),
                   "封测", AS_OF, sections=sections)
    assert data["input_cost"].status == "年报披露"


def test_汇率贡献剥后缀后仍能命中():
    f = _f("fx", "汇率贡献")
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[f])
    sections = _sections(("md_a", "管理层讨论与分析", "受汇率波动影响，出口业务毛利率承压。"))
    data = collect(_ExplodingClient(), [d], _led(), Corpus(as_of=AS_OF),
                   "封测", AS_OF, sections=sections)
    assert data["fx"].status == "年报披露"


def test_剥后缀不引入过短核心词的误判():
    """保守：剥完后核心词过短就不剥，宁可落到④也不要拿一两个字这种
    到处能命中的片段冒充"命中"。final-fix-D I-b：阈值改回 2——真正
    防"未披露"这类否定语境误判的是 `_valid_hit`，不是靠拉长这个阈值，
    见 `test_未披露被短核心词误判为年报披露`。"""
    from ir_agent.gaps import _core_label
    assert _core_label("率贡献") == "率贡献"          # 剥完只剩1字，不剥
    assert _core_label("有效产能变化率") == "有效产能"
    assert _core_label("稼动率贡献") == "稼动率"
    assert _core_label("先进封装收入占比提升贡献") == "先进封装收入"
    assert _core_label("原材料及折旧成本变化贡献") == "原材料及折旧成本"
    assert _core_label("汇率贡献") == "汇率"
    assert _core_label("产能") == "产能"              # 无后缀，原样返回


# ── final-fix-D I-b：长度阈值防不住"未披露"，失败模式是否定语义 ──
#
# 上一轮（I2）把 _core_label 的阈值从 2 提到 4，指望"核心词够长"挡住
# "未披露稼动率数据"这类否定语境的误判。审查者用报告原文实测证明这
# 防不住：「未披露**有效产能**的具体数据」里的「有效产能」有 4 字，
# 阈值再高也挡不住——失败模式跟长度无关，是否定语义本身。而且代价
# 是实打实的召回损失：本仓库自己的规范因子名（「产能变化率」「ASP
# 变化率」……）全部被挤回退到原始标签匹配，四级采集阶梯对它们退化
# 成"④或无数据"两级。改为阈值恢复 2、在命中处查否定词。

def test_未披露被短核心词误判为年报披露():
    """用报告给出的原文逐字复现：「稼动率变化率」剥出核心词「稼动率」
    （3字）、「汇率贡献」剥出核心词「汇率」（2字），阈值改回 2 后
    两者都会重新被剥出来去匹配——若不在命中处查否定词，两处都会撞上
    原文里"未披露稼动率数据"/"汇率波动"这几个字面，被误判成"年报
    披露"。`_valid_hit` 要在命中处的否定语境里把这两处都判定为不算
    命中。"""
    factors = [_f("utilization", "稼动率变化率"), _f("fx", "汇率贡献")]
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=factors)
    sections = _sections(("md_a", "管理层讨论与分析",
                          "公司本年度未披露稼动率数据。行业整体产能过剩，"
                          "汇率波动对业绩影响有限。"))
    data = collect(_Client(""), [d], _led(), Corpus(as_of=AS_OF),
                   "封测", AS_OF, sections=sections)
    assert data["utilization"].status != "年报披露", \
        "「未披露稼动率数据」不该被短核心词「稼动率」误判成年报披露"
    assert data["fx"].status != "年报披露", \
        "「汇率波动」不该被短核心词「汇率」误判成年报披露"


def test_否定语境不因核心词够长而免疫():
    """final-fix-D I-b 的核心复现：用报告给出的真实年报原文——「公司
    本年度未披露有效产能的具体数据。原材料及折旧成本未单独列示，
    无法计算其对毛利率的影响。」——两个核心词都有 4 字以上，长度
    阈值（不管是 2 还是 4）都拦不住，只有在命中处查否定词才能挡住。
    这条测试在只恢复阈值、不加否定语境检查的情况下必须失败。"""
    factors = [_f("capacity", "有效产能变化率"),
              _f("input_cost", "原材料及折旧成本变化贡献")]
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=factors)
    sections = _sections(("md_a", "管理层讨论与分析",
                          "公司本年度未披露有效产能的具体数据。原材料及"
                          "折旧成本未单独列示，无法计算其对毛利率的影响。"))
    data = collect(_Client(""), [d], _led(), Corpus(as_of=AS_OF),
                   "封测", AS_OF, sections=sections)
    assert data["capacity"].status != "年报披露", \
        "「未披露有效产能的具体数据」不该被误判成年报披露"
    assert data["input_cost"].status != "年报披露", \
        "「未单独列示」不该被误判成年报披露"


def test_否定语境之外的真实命中不受连坐():
    """同一份年报里，核心词可能既出现在否定句里，也在别处真正给出了
    数据——只检查第一次出现会让后面这种真命中被前面的否定误伤连坐。
    `_valid_hit` 要跳过否定语境的出现，继续找下一处。"""
    f = _f("capacity", "有效产能变化率")
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[f])
    # 两处出现之间垫一大段与否定语境无关的文字——否定窗口是命中点
    # 前后各 40 字，两处出现必须离得够远，测试才能真正区分"第二次
    # 出现不该被第一次的否定连坐"，而不是恰好被同一个窗口盖住。
    filler = "行业整体保持景气，下游需求平稳增长，同业公司普遍维持满产运行，" * 3
    sections = _sections(("md_a", "管理层讨论与分析",
                          "报告期内公司未披露有效产能的具体数据。" + filler +
                          "另据测算，有效产能较上年末提升约一成。"))
    data = collect(_ExplodingClient(), [d], _led(), Corpus(as_of=AS_OF),
                   "封测", AS_OF, sections=sections)
    assert data["capacity"].status == "年报披露", \
        "第二次出现不在否定语境里，应正常算命中，不该被第一次的否定连坐"


# ── M9：时点守卫是全系统最关键的防线之一，此前无测试守护 ──
# （`test_已有该因子但披露时点晚于as_of仍算缺口` 已覆盖 LookAheadError
# 分支，这里补一条 identify() 在 as_of 之前正常可见时不算缺口的对照，
# 与之前那条构成一对「时点之前可见/之后不可见」的完整守卫测试。）

# ── final-fix-2 Fix 1：openweb 分支之后语料库不再全是 SELLSIDE，
# ③命中必须跳过 MEDIA 文档，与④（web.supports_value）同一道闸门 ──

def test_MEDIA语料不产生研报测算命中(monkeypatch):
    """语料库里只有一篇 MEDIA 文档、文本命中核心词时，③不能把它判成
    「研报测算」——那等于一篇网络博客单独支撑了因子取值，是本轮修复
    要堵的洞。落空后应正常退到④，④有结论则算「外部来源」。"""
    import ir_agent.sources.conclusions as CC
    from ir_agent.sources.openweb import Fetched
    monkeypatch.setattr(CC, "fetch", lambda url, **k: Fetched(
        final_url=url, text="平均稼动率约78%", meta_published=None))
    corpus = Corpus(as_of=AS_OF)
    corpus.add(_doc("我们测算行业稼动率将于下半年回升。", tier=Tier.MEDIA))
    ok = ("文章: 1 | https://semi.org/x | SEMI | 2026-07-01\n"
          "结论: utilization | 平均稼动率约78% | 依据 1\n")
    data = collect(_Client(ok, ""), [_rev()], _led("capacity", "asp"),
                   corpus, "封测", AS_OF)
    fd = data["utilization"]
    assert fd.status != "研报测算"
    assert fd.status == "外部来源"


def test_SELLSIDE语料仍产生研报测算命中():
    """与上面 MEDIA 那条对照：加了分级闸门后，合格的 SELLSIDE 研报要
    继续正常命中③，不能被误伤一并挡下。"""
    corpus = Corpus(as_of=AS_OF)
    corpus.add(_doc("我们测算行业稼动率将于下半年回升。", tier=Tier.SELLSIDE))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   corpus, "封测", AS_OF)
    assert data["utilization"].status == "研报测算"


def test_MEDIA先于SELLSIDE入库时仍以SELLSIDE命中():
    """复现 review 描述的真实隐患：MEDIA 文档若恰好排在 SELLSIDE 前面
    入库，不做分级过滤时会抢先被 _match_report 命中——"一家合格来源
    只是靠列表顺序侥幸胜出"。加了闸门后不管顺序如何，命中的必须是
    SELLSIDE 那篇。"""
    corpus = Corpus(as_of=AS_OF)
    corpus.add(_doc("我们预计稼动率将持续偏低。", tier=Tier.MEDIA, doc_id="media1"))
    corpus.add(_doc("我们测算行业稼动率将于下半年回升。",
                    tier=Tier.SELLSIDE, doc_id="sell1"))
    data = collect(_ExplodingClient(), [_rev()], _led("capacity", "asp"),
                   corpus, "封测", AS_OF)
    fd = data["utilization"]
    assert fd.status == "研报测算"
    assert "预计稼动率将持续偏低" not in fd.note
    assert "测算行业稼动率将于下半年回升" in fd.note


def test_时点守卫对照_披露时点早于as_of时不算缺口():
    """与 test_已有该因子但披露时点晚于as_of仍算缺口 相对照：同一个
    因子，披露时点早于/等于 as_of 时应正常被判定为已披露，不是缺口。
    两条测试合起来才是对时点守卫完整的锁定——只测"晚于不算"不能
    排除"提前把整个 identify 都改成恒定返回缺口"这种退化实现。"""
    led = FactLedger()
    led.put(Fact(key="capacity", value=D("0.1"), unit="ratio",
                 period="2025FY", as_of=date(2026, 8, 1),
                 source_id="t", method=Method.COMPUTED))
    gaps = identify(_rev(), led, AS_OF)
    assert "capacity" not in [f.key for f in gaps]


def test_公告命中取出COMPANY文档的摘录():
    """这里直接调用 `_match_announce`，不经过 `collect()` 的调度——验证
    的是它能从 `Tier.COMPANY` 文档里取出摘录本身，不是 ②.5 与 ③ 谁先谁后
    （调度顺序由 `test_公告优先于研报` 通过 `collect()` 验证）。状态标签
    不能沿用「研报测算」——那会把一份公司公告说成券商测算，是错误归因。"""
    import ir_agent.gaps as G
    from ir_agent.sources.external import Corpus, Document, Tier

    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="r1", url="https://r.example.com/a", title="研报",
                    publisher="某券商", tier=Tier.SELLSIDE,
                    published=date(2026, 6, 1), retrieved=AS_OF,
                    text="本文测算行业名义产能约 1200 万平方米。"))
    cp.add(Document(doc_id="a1", url="http://static.cninfo.com.cn/a.PDF",
                    title="扩产投产公告", publisher="生益科技",
                    tier=Tier.COMPANY, published=date(2026, 8, 1),
                    retrieved=AS_OF,
                    text="本次投产新增名义产能 300 万平方米。"))
    hit = G._match_announce(cp, "名义产能")
    assert hit is not None
    excerpt, doc, _ = hit
    assert doc.tier is Tier.COMPANY
    assert "300 万平方米" in excerpt


def test_公告优先于研报():
    """②.5 与 ③ 都命中时用公告，不用研报——公告是公司权威披露、由确定性
    接口取回，比两级模型中介的来源更近（`gaps._match_announce` 文档字符
    串同一理由）。与 `test_年报优先于研报` 同一模式：经过 `collect()`
    走完整调度，而不是像 `test_公告命中取出COMPANY文档的摘录` 那样直接
    调用 `_match_announce`——只有经过 `collect()` 才钉得住"②.5 排在 ③
    之前被调用"这件事本身：把 collect() 里两次调用的先后顺序对调，这
    条测试会变红（`_match_report` 不跳过 COMPANY 文档，只跳 MEDIA，
    对调后会先命中研报文档或公告文档中语料库里排在前面的那一份，判成
    「研报测算」）。"""
    import ir_agent.gaps as G

    corpus = Corpus(as_of=AS_OF)
    corpus.add(_doc("我们测算行业产能将维持平稳", tier=Tier.SELLSIDE, doc_id="r1"))
    corpus.add(_doc("本次公告披露产能维持平稳", title="产能公告",
                    tier=Tier.COMPANY, doc_id="a1"))
    data = collect(_ExplodingClient(), [_rev()], _led("utilization", "asp"),
                   corpus, "封测", AS_OF)
    assert data["capacity"].status == G.ANNOUNCE


def test_公告命中只看COMPANY文档():
    """②.5 是公告这一级，研报/媒体文档不该从这里命中——它们各有自己
    的一级。"""
    import ir_agent.gaps as G
    from ir_agent.sources.external import Corpus, Document, Tier

    cp = Corpus(as_of=AS_OF)
    for t in (Tier.SELLSIDE, Tier.MEDIA, Tier.OFFICIAL):
        cp.add(Document(doc_id=f"d{t.value}", url=f"https://x/{t.value}",
                        title="t", publisher="p", tier=t,
                        published=date(2026, 6, 1), retrieved=AS_OF,
                        text="名义产能约 1200 万平方米。"))
    assert G._match_announce(cp, "名义产能") is None


def test_公告的否定语境不算命中():
    """与 ②③ 同一条：「未披露名义产能的具体数据」不是命中。"""
    import ir_agent.gaps as G
    from ir_agent.sources.external import Corpus, Document, Tier

    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="a1", url="http://static.cninfo.com.cn/a.PDF",
                    title="公告", publisher="生益科技", tier=Tier.COMPANY,
                    published=date(2026, 8, 1), retrieved=AS_OF,
                    text="本公司未披露名义产能的具体数据。"))
    assert G._match_announce(cp, "名义产能") is None


def test_ANNOUNCE_标签文案():
    import ir_agent.gaps as G
    assert G.ANNOUNCE == "公告披露"


# ── Task 6：B2 接线到 ④，supports_value 门槛随之从「来源等级」改为
# 「有没有被代码核实过」。核验不通过的发现降级为线索：不丢弃、不阻断，
# 但不能撑因子取值——这条契约的落地处是这里，不是 web.supports_value
# 本身（它已经不知道调用方是不是核实过）。

# ── Fix wave 1（审查发现）──────────────────────────────────────────
#
# 五条 review 发现里落在 gaps.py 这一侧的回归测试。Fix 1 的容纳测试
# 特意不 stub verify_finding 本身——只 stub 更底层的 OW.fetch——因为
# Fix 1 要证明的正是"verify_finding 自己接住了 FetchFailed 之外的异常，
# 不需要 collect() 另加一层"，如果连 verify_finding 都 stub 掉就什么都
# 证明不了。

# ── B3：因子标签的关键词提取 ──────────────────────────────────
#
# 实跑（out/600183-subB，2026-10-01）里 8 因子全落「无数据」，②②.5③
# 三级文本命中全为 0。原因不是 A 股不披露：那一轮模型给的标签形如
# 「覆铜板下游需求规模(服务器/通信/消费电子PCB用量)」，而 _core_label
# 只剥 _SUFFIXES 后缀、对这类标签原样返回，于是匹配器拿整串去 89055 字
# 正文里找。对照 ow4 那轮标签短（名义产能/ASP/良率），③ 命中 1/8。
#
# 下面的期望值全部由 spec 第四节的代码实测得出，不是推演。


def test_候选词按特指度降序且去重():
    """整串 → 去括号的头 → 头再剥后缀。调用方命中即停，所以顺序就是优先级。"""
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("先进封装收入占比提升贡献(高端CCL)") == [
        "先进封装收入占比提升贡献(高端CCL)",
        "先进封装收入占比提升贡献",
        "先进封装收入",
    ], "三级阶梯没走通——这是唯一同时用上 L2（去括号）与 L3（剥后缀）的例子"


def test_候选词绝不下探括号内():
    """**这条钉的是 spec 第三节那个决定。**

    括号里是举例/注解：`原材料成本(铜箔/…)` 的括号内是成本的**组成项**，
    拿组成项的命中当整个因子的证据是归因错位。而实测「服务器」在真实
    公告语料（8 篇，89055 字）里出现 **18 次**——下探括号内会让
    「覆铜板下游需求规模」凭 18 次纯巧合拿到它唯一的「证据」。

    将来谁想把提取「改进」成下探括号内，必须先让这条红，而不是悄悄
    多出一个 18 次命中的巧合。
    """
    from ir_agent.gaps import _core_candidates

    cands = _core_candidates("覆铜板下游需求规模(服务器/通信/消费电子PCB用量)")
    assert cands == ["覆铜板下游需求规模(服务器/通信/消费电子PCB用量)",
                     "覆铜板下游需求规模"]
    for inner in ("服务器", "通信", "消费电子PCB用量"):
        assert inner not in cands, f"括号内的词「{inner}」被当成候选了"

    cands2 = _core_candidates("原材料成本(铜箔/电子级玻纤布/环氧树脂价格)")
    assert cands2 == ["原材料成本(铜箔/电子级玻纤布/环氧树脂价格)", "原材料成本"]
    assert "铜箔" not in cands2, \
        "「铜箔」实测命中 6 次、是真信号——但它是成本的组成项、不是成本本身"


def test_本轮实跑的其余五个真实标签():
    """另两个（覆铜板下游需求规模、原材料成本）在
    `test_候选词绝不下探括号内` 里断言——那条的重点是括号内不入候选，
    顺带把它们的完整期望列表也钉住了，这里不重复。七个标签合起来全覆盖。

    fixture 取自真实运行的产物，不是手写的理想形状。

    **这一点是本任务存在的理由本身**：原先所有测试都用「产能」「稼动率」
    这类我自己写的短标签，而生产里模型吐出的是下面这些。测试用的输入
    形状与生产中实际出现的形状不是同一个分布，于是三级归零不报错、
    不抛异常、套件全绿。
    """
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("公司覆铜板市占率") == ["公司覆铜板市占率"]
    assert _core_candidates("覆铜板平均单价ASP") == ["覆铜板平均单价ASP"]
    assert _core_candidates("产能利用率(稼动率同比变化)") == [
        "产能利用率(稼动率同比变化)", "产能利用率"]
    assert _core_candidates("产品结构升级(高端CCL如M6/M7/AI服务器用板占比提升)") == [
        "产品结构升级(高端CCL如M6/M7/AI服务器用板占比提升)", "产品结构升级"]
    assert _core_candidates("汇兑损益(出口业务人民币兑美元波动)") == [
        "汇兑损益(出口业务人民币兑美元波动)", "汇兑损益"]


def test_仓库规范因子名多试一次整串后退回现状():
    """无括号的标签：比现状多试一次整串，再落到 `_core_label` 那一级。

    整串若真的逐字出现，那是更强的命中；不出现就退到现状那一级，
    净结果与改动前一致。
    """
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("有效产能变化率") == ["有效产能变化率", "有效产能"]
    assert _core_candidates("稼动率贡献") == ["稼动率贡献", "稼动率"]
    assert _core_candidates("先进封装收入占比提升贡献") == [
        "先进封装收入占比提升贡献", "先进封装收入"]
    assert _core_candidates("原材料及折旧成本变化贡献") == [
        "原材料及折旧成本变化贡献", "原材料及折旧成本"]
    assert _core_candidates("汇率贡献") == ["汇率贡献", "汇率"]


def test_候选词的退化情形都不崩且方向保守():
    """退化时宁可落空退 ④，不可拿一两个字去冒充命中。"""
    from ir_agent.gaps import _core_candidates

    # 无括号无后缀：与现状完全一致
    assert _core_candidates("产能") == ["产能"]
    # 剥完只剩 1 字则不剥——沿用 `_core_label` 既有行为
    assert _core_candidates("率贡献") == ["率贡献"]
    # 括号不闭合：正则不匹配，头等于整串，不崩
    assert _core_candidates("产能(稼动率") == ["产能(稼动率"]
    # 括号占满整个标签：去括号后为空串，被 2 字下限挡掉
    assert _core_candidates("(稼动率)") == ["(稼动率)"]
    # 全角括号同样处理
    assert _core_candidates("（全角括号）产能") == ["（全角括号）产能", "产能"]
    # 2 字恰好在下限上，保留
    assert _core_candidates("AB") == ["AB"]


def test_候选词在外层容器在内层():
    """**这条钉住循环嵌套的方向，把它写反会红。**

    构造：两篇 SELLSIDE 文档，**靠前**那篇只含最不具体的候选词
    （L3「有效产能」），**靠后**那篇含更具体的 L2「有效产能变化率」。

    · 候选词在外层（正确）：先拿 L2 扫完所有文档 → 命中靠后那篇 → d2
    · 容器在外层（写反）：先扫靠前那篇 → L1 不中、L2 不中、L3 中 → d1

    若容器在外层，一个较泛的候选词在靠前的章节/文档里命中，会压过一个
    更具体的候选词在靠后的容器里的命中——那等于悄悄放弃了「按特指度
    排序」这整个决定，而套件会全绿。
    """
    import ir_agent.gaps as G

    cp = Corpus(as_of=AS_OF)
    cp.add(_doc("公司有效产能保持稳定，不再披露细分口径。",
                tier=Tier.SELLSIDE, doc_id="d1"))
    cp.add(_doc("我们测算其有效产能变化率为 12%。",
                tier=Tier.SELLSIDE, doc_id="d2"))

    hit = G._match_report(cp, "有效产能变化率(含新建产线)")
    assert hit is not None, "两篇都没命中——候选词阶梯没接上"
    _, doc, _core = hit
    assert doc.doc_id == "d2", (
        f"命中了 {doc.doc_id}，应为 d2——容器在外层了：靠前那篇只含最不"
        "具体的候选词「有效产能」，却压过了靠后那篇更具体的「有效产能变化率」")


def test_带括号注解的标签能命中年报章节():
    """改动前：`_core_label` 对带括号的标签原样返回，整串不会逐字出现在
    年报里，② 必然落空。改动后：去括号的头「原材料成本」能命中。"""
    import ir_agent.gaps as G

    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期原材料成本上升，其中铜箔采购均价同比上涨 18%。"))
    hit = G._match_annual(sections, "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)")
    assert hit is not None, "去括号的头没能命中——候选词阶梯没接进 _match_annual"
    excerpt, where, _ = hit
    assert "原材料成本" in excerpt and where == "《经营情况讨论与分析》"


def test_带括号注解的标签能命中公告():
    """②.5 走同一条阶梯。实跑里 8 篇公告、89055 字，「原材料成本」命中 2 次。"""
    import ir_agent.gaps as G

    cp = Corpus(as_of=AS_OF)
    cp.add(_doc("本期原材料成本同比上升 7.2 个百分点。", title="年度报告",
                tier=Tier.COMPANY, doc_id="a1"))
    hit = G._match_announce(cp, "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)")
    assert hit is not None, "②.5 没接上候选词阶梯"
    _, doc, _core = hit
    assert doc.tier is Tier.COMPANY


def test_括号内的词不会让因子误命中():
    """**反向**：语料里只有括号内那个泛词时，不得命中。

    实测「服务器」在真实公告语料里出现 18 次。没有这条断言，一个下探
    括号内的实现会让「覆铜板下游需求规模」凭纯巧合拿到唯一的「证据」，
    而正向测试全部照绿。
    """
    import ir_agent.gaps as G

    cp = Corpus(as_of=AS_OF)
    cp.add(_doc("AI 服务器需求旺盛，通信板块回暖。",
                tier=Tier.SELLSIDE, doc_id="s1"))
    assert G._match_report(
        cp, "覆铜板下游需求规模(服务器/通信/消费电子PCB用量)") is None, \
        "凭括号内的泛词「服务器」命中了——这正是「把巧合伪装成证据」"

    sections = _sections(("mgmt", "经营情况讨论与分析", "AI 服务器出货增长。"))
    assert G._match_annual(
        sections, "覆铜板下游需求规模(服务器/通信/消费电子PCB用量)") is None


def test_否定语境对每个候选词都生效():
    """`_valid_hit` 一行不动，所以否定判断逐候选词成立——不是只对第一个。

    这里 L1 整串不出现，L2「原材料成本」唯一的出现落在「未披露」窗口里，
    必须判不命中、退到下一级或落空，而不是把否定句当证据摘出来。
    """
    import ir_agent.gaps as G

    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期未披露原材料成本的具体构成。"))
    assert G._match_annual(
        sections, "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)") is None, \
        "否定语境里的命中被当成了证据"


def test_检索词是候选阶梯的最后一级():
    """**不是第一级。** 「出货量」比整串标签更泛，不是更具体；放顶端会
    压过更具体的候选，颠倒「按特指度排序」这条原则。检索词存在的理由
    恰恰是前三级全部落空。
    """
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("服务器及通信用高端覆铜板出货量同比增速",
                            "出货量") == [
        "服务器及通信用高端覆铜板出货量同比增速",
        "服务器及通信用高端覆铜板出货量同比",
        "出货量",
    ]
    # 不传检索词时与 B3 第一版完全一致
    assert _core_candidates("服务器及通信用高端覆铜板出货量同比增速") == [
        "服务器及通信用高端覆铜板出货量同比增速",
        "服务器及通信用高端覆铜板出货量同比",
    ]


def test_检索词与结构候选重复时只出现一次():
    """`add` 去重。这条同时是 Task 4 那个「按字符串比较而非位置」决定的
    依据：标签本身就是「产能」、检索词也给「产能」时，检索词**不会**作为
    第四项出现——按位置判断「是否靠 L4 命中」会在这里说错话。
    """
    from ir_agent.gaps import _core_candidates

    assert _core_candidates("产能", "产能") == ["产能"]


def test_前三级落空时检索词兜底命中():
    """这是整个字段存在的理由：不带括号的冗长标签，前三级都不会逐字出现
    在年报里，只有模型给的检索词能命中。"""
    import ir_agent.gaps as G

    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期出货量同比增长 23%，高端产品占比提升。"))
    label = "服务器及通信用高端覆铜板出货量同比增速"
    assert G._match_annual(sections, label) is None, \
        "不给检索词时本该落空——否则这条测试证明不了检索词起了作用"
    hit = G._match_annual(sections, label, search_term="出货量")
    assert hit is not None, "检索词没能兜底"
    excerpt, where, _ = hit
    assert "出货量" in excerpt and where == "《经营情况讨论与分析》"


def test_更具体的结构候选优先于检索词():
    """L2 能命中时就不该用 L4——「按特指度排序」。

    构造：L2（去括号的头「原材料成本」）在**靠后**的章节里命中，
    L4（检索词「成本」）在**靠前**的章节里命中。候选在外层循环，
    所以 L2 先扫完所有章节、命中靠后那篇。
    """
    import ir_agent.gaps as G

    sections = _sections(
        ("a", "第一节", "本期成本控制良好。"),
        ("b", "第二节", "本期原材料成本上升 7%。"))
    hit = G._match_annual(
        sections, "原材料成本(铜箔/电子级玻纤布/环氧树脂价格)",
        search_term="成本")
    assert hit is not None
    _, where, _core = hit
    assert where == "《第二节》", \
        "用了检索词「成本」而不是更具体的「原材料成本」——L4 被提前了"


def test_三个匹配器都收检索词():
    """②、②.5、③ 走同一条阶梯，三处都要接上，漏一处那一级就得不到兜底。"""
    import ir_agent.gaps as G

    label = "服务器及通信用高端覆铜板出货量同比增速"

    cp_sell = Corpus(as_of=AS_OF)
    cp_sell.add(_doc("我们测算其出货量同比增长 23%。",
                     tier=Tier.SELLSIDE, doc_id="s1"))
    assert G._match_report(cp_sell, label) is None
    assert G._match_report(cp_sell, label, search_term="出货量") is not None

    cp_co = Corpus(as_of=AS_OF)
    cp_co.add(_doc("本期出货量同比增长 23%。", title="年度报告",
                   tier=Tier.COMPANY, doc_id="a1"))
    assert G._match_announce(cp_co, label) is None
    assert G._match_announce(cp_co, label, search_term="出货量") is not None


def test_靠检索词命中要在清单渲染里说出来():
    """**断言落在 `G.summary(data)` 的渲染输出上，不是 `fd.note` 字段上。**

    子项目 B 的终审用一个阻断项换来这条教训：一条要求是「人看得见」，而
    两条测试都断在 `FactorData.note` 字段上，渲染器把那个字段整行丢掉，
    两条测试照样通过。要求是人看得见，断言就必须落在人看到的那个字符串上。
    """
    import ir_agent.gaps as G
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.ledger import FactLedger

    f = Factor(key="shipment", label="服务器及通信用高端覆铜板出货量同比增速",
               unit="变化率", source_hint="年报", search_term="出货量")
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[f])
    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期出货量同比增长 23%。"))
    data = G.collect(_ExplodingClient(), [d], FactLedger(), None,
                     "覆铜板", AS_OF, sections=sections)

    assert data["shipment"].status == G.ANNUAL
    rendered = G.summary(data)
    assert "靠模型给的检索词" in rendered, \
        "靠检索词命中这件事没出现在人看到的清单里"
    assert "出货量" in rendered


def test_靠标签结构命中时不加这句标注():
    """**反向。** 没有它，一个无条件追加这句话的实现也能让正向测试通过，
    而那会让这条标注失去全部信息量——每一行都写着「靠检索词命中」，
    等于没写。
    """
    import ir_agent.gaps as G
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.ledger import FactLedger

    f = Factor(key="cost", label="原材料成本(铜箔/电子级玻纤布/环氧树脂价格)",
               unit="pp", source_hint="年报", search_term="成本")
    d = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[f])
    sections = _sections(("mgmt", "经营情况讨论与分析",
                          "本期原材料成本上升 7%。"))
    data = G.collect(_ExplodingClient(), [d], FactLedger(), None,
                     "覆铜板", AS_OF, sections=sections)

    assert data["cost"].status == G.ANNUAL
    assert "靠模型给的检索词" not in G.summary(data), \
        "L2（去括号的头「原材料成本」）命中却被标成了靠检索词命中"


def test_标注按字符串比较而非按位置():
    """标签本身就是「产能」、检索词也给「产能」时，候选列表去重后它**不会**
    作为第四项出现。按位置判断（「第 4 项就是检索词」）会在这里说错话——
    把一次 L1 命中标成「靠检索词命中」，或反过来漏标。

    这条红了意味着有人改成了数位置。
    """
    import ir_agent.gaps as G
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.ledger import FactLedger

    f = Factor(key="capacity", label="产能", unit="变化率",
               source_hint="年报", search_term="产能")
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[f])
    sections = _sections(("mgmt", "经营情况讨论与分析", "本期产能提升 10%。"))
    data = G.collect(_ExplodingClient(), [d], FactLedger(), None,
                     "覆铜板", AS_OF, sections=sections)
    assert data["capacity"].status == G.ANNUAL
    # 命中的候选词确实等于 search_term，所以这里**应当**标注——
    # 本测试要钉住的是实现方式（比较字符串，不数位置），不是标不标。
    assert "靠模型给的检索词" in G.summary(data)


def test_放宽条数带来的召回就是本次改动的全部收益():
    """**同一个因子，在 8 篇池子里搜不到、在 20 篇池子里搜到。**

    若这条测不出差别，放宽条数就没有收益，整个 Task 的理由不成立。
    牧原股份那次因子搜不到证据，有一半原因是可搜的池子太小。
    """
    from datetime import date

    from ir_agent.gaps import _match_announce
    from ir_agent.sources.external import Corpus, Document, Tier

    def _cp(n):
        cp = Corpus(as_of=date(2026, 10, 12))
        for i in range(n):
            # 只有第 12 篇提到「铜箔采购价」
            txt = "本期铜箔采购价同比下降 8%。" if i == 12 else "例行披露内容。"
            cp.docs.append(Document(
                doc_id=f"a{i}", url=f"http://x/{i}", title=f"公告{i}",
                publisher="某公司", tier=Tier.COMPANY,
                published=date(2026, 1, 1 + i), retrieved=date(2026, 10, 12),
                text=txt))
        return cp

    assert _match_announce(_cp(8), "铜箔采购价", search_term="铜箔") is None
    assert _match_announce(_cp(20), "铜箔采购价", search_term="铜箔") is not None


# ══ 第二次调用试跑后的修订 ⑤：数据线索里声明的账本 key ══════════════

def test_显式声明的账本key_标已披露且不联网():
    from ir_agent.gaps import DISCLOSED
    f = Factor(key="f_x", label="行业产值同比增速", unit="变化率",
               source_hint="[[industry.revenue_yoy]]（同业聚合口径）")
    d = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[f])
    data = collect(_ExplodingClient(), [d], _led("industry.revenue_yoy"),
                   Corpus(as_of=AS_OF), "电子", AS_OF)
    assert data["f_x"].status == DISCLOSED
    assert "[[industry.revenue_yoy@2025FY]]" in data["f_x"].note
    assert "口径" in data["f_x"].note, "口径是否一致要交给人"


def test_只是提到key不算声明():
    """生益科技 2026-10-13 第 60 行原文：提到 capex 不等于稼动率就是 capex。"""
    from ir_agent.gaps import ledger_refs
    hint = "capex、fixed_asset等账本key及在建工程投资公告，需采集稼动率数据"
    assert ledger_refs(hint, _led("capex", "fixed_asset"), AS_OF) == ([], [])


def test_声明的key前缀不误中():
    from ir_agent.gaps import ledger_refs
    assert ledger_refs("[[industry.revenue]]", _led("industry.revenue_yoy"),
                       AS_OF) == ([], ["industry.revenue"])


def test_研究时点之后才披露的不算():
    from ir_agent.gaps import ledger_refs
    led = FactLedger()
    led.put(Fact(key="industry.revenue_yoy", value=D("0.1"), unit="ratio",
                 period="2026FY", as_of=date(2026, 12, 1), source_id="t",
                 method=Method.COMPUTED))
    assert ledger_refs("[[industry.revenue_yoy]]", led, AS_OF) == (
        [], ["industry.revenue_yoy"])


# ══ 第二次调用试跑后的修订 ②：④改为模型综合结论 + 引用文章 ═══════════

from ir_agent.sources import conclusions as _CC
from ir_agent.sources.openweb import Fetched, FetchFailed

_REPLY = ("文章: 1 | https://www.stats.gov.cn/a | 国家统计局 | 2026-07-01\n"
          "文章: 2 | https://news.example.com/b | 某媒体 | 2026-08-01\n"
          "结论: capacity | 名义产能约120万吨，同比提升3个百分点 | 依据 1,2\n")


def _pages(pages: dict, degraded=False):
    def f(url, **k):
        if url not in pages:
            raise FetchFailed("404")
        return Fetched(final_url=url, text=pages[url], meta_published=None,
                       host_check_degraded=degraded)
    return f


def _one(client, corpus=None):
    d = Decomposition(target="revenue_growth", op=Op.MULTIPLY,
                      factors=[Factor(key="capacity", label="名义产能",
                                      unit="变化率", source_hint="")])
    return collect(client, [d], FactLedger(), corpus or Corpus(as_of=AS_OF),
                   "电子/元件", AS_OF, sections=None, annual_url="")


class TestExternalConclusions:
    PAGES = {"https://www.stats.gov.cn/a": "名义产能约120万吨。",
             "https://news.example.com/b": "同比提升3个百分点。"}

    def test_年报研报都未命中才联网(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(self.PAGES))
        cl = _Client(_REPLY)
        fd = _one(cl)["capacity"]
        assert fd.status == "外部来源"
        assert "名义产能" in cl.prompts[0]

    def test_打开的文章全文进语料库(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(self.PAGES))
        cp = Corpus(as_of=AS_OF)
        _one(_Client(_REPLY), cp)
        texts = {d.text for d in cp.docs}
        assert texts == set(self.PAGES.values()), "入库的是代码抓回的全文"
        assert all(d.date_source == "model" for d in cp.docs)

    def test_文章按最终域名分级(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(self.PAGES))
        c = _one(_Client(_REPLY))["capacity"].findings[0]
        assert c.articles[0].tier is Tier.OFFICIAL
        assert c.tier is Tier.OFFICIAL

    def test_外部来源状态不填source_url_结论版(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(self.PAGES))
        fd = _one(_Client(_REPLY))["capacity"]
        # 前提：真的走到了外部来源。没有这一行，旧代码解析不了新格式、
        # 落「无数据」时 source_url 也是空串 —— 实现前就通过的空心测试。
        assert fd.status == "外部来源"
        assert fd.source_url == ""

    def test_一篇打不开不牵连同结论的其余文章(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(
            {"https://www.stats.gov.cn/a": "名义产能约120万吨。"}))
        fd = _one(_Client(_REPLY))["capacity"]
        c = fd.findings[0]
        assert fd.status == "外部来源"
        assert c.articles[0].opened and not c.articles[1].opened
        assert "代码无法打开" in c.flags()
        assert "其中 1 条带 ⚠" in fd.note

    def test_抓取抛任意异常不逃出collect(self, monkeypatch):
        def boom(url, **k):
            raise RuntimeError("连接被重置")
        monkeypatch.setattr(_CC, "fetch", boom)
        fd = _one(_Client(_REPLY))["capacity"]
        assert fd.status == "外部来源"
        assert all("抓取失败" in a.why_not for a in fd.findings[0].articles)

    def test_数字找不到出处时note计数(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(
            {"https://www.stats.gov.cn/a": "名义产能约120万吨。",
             "https://news.example.com/b": "同比有所提升。"}))
        fd = _one(_Client(_REPLY))["capacity"]
        assert fd.findings[0].missing_numbers() == ["3"]
        assert "其中 1 条带 ⚠" in fd.note and "待人工复核" in fd.note

    def test_引的文章都晚于as_of时落无数据并说明(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(self.PAGES))
        late = ("文章: 1 | https://www.stats.gov.cn/a | 国家统计局 | 2026-12-01\n"
                "结论: capacity | x | 依据 1\n")
        fd = _one(_Client(late))["capacity"]
        assert fd.status == "无数据" and "晚于" in fd.note

    def test_主机安全校验降级写进结构化字段与note(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(self.PAGES, degraded=True))
        fd = _one(_Client(_REPLY))["capacity"]
        assert fd.host_check_degraded and "主机安全校验降级" in fd.note
        monkeypatch.setattr(_CC, "fetch", _pages(self.PAGES, degraded=False))
        fd2 = _one(_Client(_REPLY))["capacity"]
        assert not fd2.host_check_degraded and "主机安全校验" not in fd2.note


class TestFactorsJsonV2:
    def _data(self, monkeypatch):
        monkeypatch.setattr(_CC, "fetch", _pages(TestExternalConclusions.PAGES))
        d = Decomposition(target="revenue_growth", op=Op.MULTIPLY,
                          factors=[Factor(key="capacity", label="名义产能",
                                          unit="变化率", source_hint="")])
        return d, collect(_Client(_REPLY), [d], FactLedger(), Corpus(as_of=AS_OF),
                          "电子/元件", AS_OF)

    def test_往返保留结论与文章正文(self, monkeypatch):
        from ir_agent.gaps import from_json, to_json
        d, data = self._data(monkeypatch)
        raw = to_json(data)
        assert raw["schema"] == 2
        back, notes = from_json(raw, [d])
        c = back["capacity"].findings[0]
        assert c.claim == data["capacity"].findings[0].claim
        assert c.articles[0].text == "名义产能约120万吨。"
        assert c.articles[0].tier is Tier.OFFICIAL and notes == []

    def test_人改了结论后数字标注按改后文字重算(self, monkeypatch):
        """决定 B：人在门② 直接改 factors.json；标注不能停在改之前。"""
        from ir_agent.gaps import from_json, to_json
        d, data = self._data(monkeypatch)
        raw = to_json(data)
        raw["factors"]["capacity"]["conclusions"][0]["claim"] = "产能约999万吨"
        back, _ = from_json(raw, [d])
        assert back["capacity"].findings[0].missing_numbers() == ["999"]

    def test_人删光结论后落无数据并说明(self, monkeypatch):
        from ir_agent.gaps import from_json, to_json
        d, data = self._data(monkeypatch)
        raw = to_json(data)
        raw["factors"]["capacity"]["conclusions"] = []
        back, _ = from_json(raw, [d])
        assert back["capacity"].status == "无数据"
        assert "门②" in back["capacity"].note

    def test_年报摘录跨调用存活(self):
        """此前 to_json 不存 source_excerpt，第三次调用里模型逐字引用年报
        摘录会被 `_match_excerpt` 判「匹配不到」。"""
        from ir_agent.gaps import ANNUAL, from_json, to_json
        f = Factor(key="capacity", label="名义产能", unit="变化率")
        d = Decomposition(target="revenue_growth", op=Op.MULTIPLY, factors=[f])
        data = {"capacity": FactorData(factor=f, status=ANNUAL, note="n",
                                       source_url="http://a", source_excerpt="产能提升")}
        back, _ = from_json(to_json(data), [d])
        assert back["capacity"].source_excerpt == "产能提升"

    def test_旧格式抛ValueError(self):
        from ir_agent.gaps import from_json
        with pytest.raises(ValueError, match="schema"):
            from_json({"capacity": {"status": "无数据"}}, [])
