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


def test_采集到官方来源标为外部来源():
    ok = ("发现: utilization | 平均稼动率约78% | https://semi.org/x "
          "| SEMI | 2026-07-01\n")
    data = collect(_Client(ok, ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["utilization"].status == "外部来源"
    assert data["utilization"].findings[0].tier is Tier.OFFICIAL


def test_仅媒体来源仍算无数据但说明原因():
    """MEDIA 单源不足以支撑取值 —— 但要说清是「找到了但不够格」
    而不是「什么都没找到」。"""
    media = ("发现: utilization | 稼动率下滑 | https://a/b "
             "| 某财经网 | 2026-07-01\n")
    data = collect(_Client(media, ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF)
    assert data["utilization"].status == "无数据"
    assert "单一媒体" in data["utilization"].note


def test_采集到的进语料库供核验():
    ok = ("发现: utilization | 平均稼动率约78% | https://semi.org/x "
          "| SEMI | 2026-07-01\n")
    corpus = Corpus(as_of=AS_OF)
    collect(_Client(ok, ""), [_rev()], _led("capacity"), corpus, "封测", AS_OF)
    assert any("78%" in d.text for d in corpus.docs)


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


def test_年报研报都未命中才落到外部网络():
    """②③都没命中，现有④的行为不变。"""
    sections = _sections(("core_competency", "核心竞争力分析", "公司专注于主业发展"))
    ok = ("发现: utilization | 平均稼动率约78% | https://semi.org/x "
          "| SEMI | 2026-07-01\n")
    data = collect(_Client(ok, ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF, sections=sections)
    assert data["utilization"].status == "外部来源"


def test_sections与corpus为None时不崩溃():
    """调用方没取到年报或语料库都要容错，直接落到④。"""
    data = collect(_Client("", ""), [_rev()], _led("capacity"),
                   Corpus(as_of=AS_OF), "封测", AS_OF, sections=None)
    assert data["utilization"].status == "无数据"


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
    「ASP变化率」这种整串——这条测试在修复前必须失败。"""
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
    到处能命中的片段冒充"命中"。构造一个剥完只剩 1 个字的极端标签，
    确认它不会被剥短——用会炸的 client 钉住"根本没剥、也没命中，
    落到④走了 search()"这件事本身不该发生在这条用例里，改为直接断言
    _core_label 的行为。"""
    from ir_agent.gaps import _core_label
    assert _core_label("率贡献") == "率贡献"          # 剥完只剩1字，不剥
    assert _core_label("有效产能变化率") == "有效产能"
    assert _core_label("稼动率贡献") == "稼动率"
    assert _core_label("先进封装收入占比提升贡献") == "先进封装收入"
    assert _core_label("原材料及折旧成本变化贡献") == "原材料及折旧成本"
    assert _core_label("汇率贡献") == "汇率"
    assert _core_label("产能") == "产能"              # 无后缀，原样返回


# ── M9：时点守卫是全系统最关键的防线之一，此前无测试守护 ──
# （`test_已有该因子但披露时点晚于as_of仍算缺口` 已覆盖 LookAheadError
# 分支，这里补一条 identify() 在 as_of 之前正常可见时不算缺口的对照，
# 与之前那条构成一对「时点之前可见/之后不可见」的完整守卫测试。）

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
