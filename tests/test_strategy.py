"""战略分析：护城河、五力、以及通向估值的那座桥。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import strategy
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.annual_sections import Section, SectionSet
from ir_agent.strategy import Force, MoatKind, StrategyFailed
from judge_stub import Judge

D = Decimal
AS_OF = date(2026, 9, 7)
P = "2024FY"

QUOTE = "公司拥有品质、品牌、工艺、环境、文化组成的五大核心竞争力"
EVIDENCE = f"【核心竞争力分析】原文第 9 页\n{QUOTE}。行业集中度持续提升。"


def _led(*keys):
    led = FactLedger()
    for k in keys:
        led.put(Fact(key=k, value=D("0.5"), unit="ratio", period=P,
                     as_of=date(2025, 1, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


def _out(moats=None, forces=None, uncerts=None):
    moats = moats if moats is not None else [
        f"护城河: 无形资产 | 宽 | 「{QUOTE}」"]
    forces = forces if forces is not None else [
        f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」" for f in Force]
    uncerts = uncerts if uncerts is not None else [
        f"不确定性: 渠道去库存 | revenue_growth | 下调 | 「{QUOTE}」",
        f"不确定性: 提价能力减弱 | gross_margin | 下调 | 「{QUOTE}」"]
    return "\n".join([*moats, *forces, *uncerts])


class _Client:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


# ── 解析 ─────────────────────────────────────────────────────

def test_解析三部分():
    v = strategy.parse(_out())
    assert v.moats[0].kind is MoatKind.INTANGIBLE and v.moats[0].strength == "宽"
    assert len(v.forces) == 5
    assert {u.driver for u in v.uncertainties} == {"revenue_growth",
                                                   "gross_margin"}


def test_未知的护城河类型_记进读数事实():
    v = strategy.parse(f"护城河: 品牌溢价 | 宽 | 「{QUOTE}」")
    assert v.moats == [] and "不是规定的类型" in "\n".join(v.read_notes)


def test_强度不是规定的词_照读并记事实():
    v = strategy.parse(f"护城河: 无形资产 | 很强 | 「{QUOTE}」")
    assert v.moats and "宽/窄/无" in "\n".join(v.read_notes)
    v = strategy.parse(f"五力: 现有竞争强度 | 很激烈 | 中性 | 「{QUOTE}」")
    assert v.forces and "高/中/低" in "\n".join(v.read_notes)


def test_字段不足_记进读数事实():
    v = strategy.parse("护城河: 无形资产 | 宽")
    assert v.moats == [] and "字段不足" in "\n".join(v.read_notes)


def test_缺依据_记进读数事实():
    v = strategy.parse("护城河: 无形资产 | 宽 | 这是我的判断")
    assert v.moats and "没有给出依据" in "\n".join(v.read_notes)


def test_引研报或转述也读得进():
    """中国巨石 2026-10-17：五力引了研报，被判「需年报原文或占位符」连挂三次。"""
    v = strategy.parse("五力: 现有竞争强度 | 高 | 对本司不利 | 转述：综合 东莞证券《玻纤行业深度报告》")
    assert v.forces and v.forces[0].cites == ["玻纤行业深度报告"]
    v = strategy.parse("五力: 供应商议价能力 | 中 | 中性 | 「玻纤价格企稳回升」——东莞证券《玻纤行业深度报告》")
    assert v.forces and v.forces[0].bases and v.forces[0].bases[0].quote == "玻纤价格企稳回升"


# ── 桥：不确定性必须映射到可调参数 ───────────────────────────

def test_驱动因子不在可调参数之列_记事实():
    """「宏观需求波动」映射不到任何参数，在情景分析里没有出口。"""
    v = strategy.parse(f"不确定性: 需求波动 | 宏观环境 | 下调 | 「{QUOTE}」")
    assert v.uncertainties and "不在可调参数之列" in "\n".join(v.read_notes)


def test_可调参数与建模假设对齐():
    assert {"revenue_growth", "gross_margin", "wacc",
            "terminal_growth"} <= set(strategy.MAPPABLE_DRIVERS)


def test_不确定性少于两条记进事实():
    """少于两条，Bear/Base/Bull 就没有定性依据，只能靠调参数。"""
    v = strategy.parse(_out(uncerts=[
        f"不确定性: 渠道去库存 | revenue_growth | 下调 | 「{QUOTE}」"]))
    assert any("至少两条" in x for x in strategy.facts(v, _led(), EVIDENCE, AS_OF))


# ── 校验 ─────────────────────────────────────────────────────

def test_引文必须逐字出自年报摘录():
    v = strategy.parse(_out(moats=["护城河: 无形资产 | 宽 | 「公司品牌力极强」"]))
    assert any("核验不过" in x for x in strategy.facts(v, _led(), EVIDENCE, AS_OF))


def test_五力必须五条都给():
    v = strategy.parse(_out(forces=[
        f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」"
        for f in list(Force)[:3]]))
    assert any("五力缺" in x for x in strategy.facts(v, _led(), EVIDENCE, AS_OF))


def test_无护城河也要显式说出():
    """「无明显护城河」是结论，不是省略的理由。"""
    v = strategy.parse(_out(moats=[]))
    assert any("也是结论" in x for x in strategy.facts(v, _led(), EVIDENCE, AS_OF))
    ok = strategy.parse(_out(moats=[f"护城河: 无明显护城河 | 无 | 「{QUOTE}」"]))
    assert strategy.facts(ok, _led(), EVIDENCE, AS_OF) == []
    assert ("护城河", "护城河（未给出）") in strategy.checklist(v)


def test_合法输入通过():
    assert strategy.facts(strategy.parse(_out()), _led(), EVIDENCE, AS_OF) == []


def test_占位符依据也可核验():
    led = _led("industry.hhi")
    v = strategy.parse(_out(forces=[
        "五力: 现有竞争强度 | 高 | 对本司不利 | [[industry.hhi@2024FY]]",
        *[f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」"
          for f in list(Force)[1:]]]))
    assert strategy.facts(v, led, EVIDENCE, AS_OF) == []


def _sections():
    ss = SectionSet(code="600519", pages=143)
    ss.sections["core_competency"] = Section(
        key="core_competency", label="核心竞争力分析", heading="四、核心竞争力分析",
        page=9, text=QUOTE)
    ss.missing.append("industry_view")
    return ss


# ── 量化锚点 ─────────────────────────────────────────────────

def test_三力有账本锚点():
    got = strategy.anchors(_led("industry.hhi", "payable_days",
                                "receivable_days"), P, AS_OF)
    assert Force.RIVALRY in got and Force.SUPPLIERS in got
    assert Force.BUYERS in got


def test_替代品威胁没有锚点():
    """硬给它编一个代理指标，是把无依据的东西装扮成有依据。"""
    assert Force.SUBSTITUTES not in strategy.FORCE_ANCHORS


def test_无锚点的力被标注为纯定性():
    c = _Client(_out())
    v, _ = strategy.assess(c, _led("industry.hhi"), P, AS_OF, _sections(), judge=Judge())
    assert Force.SUBSTITUTES in v.unquantified
    assert "纯定性判断" in v.summary()


def test_账本无该事实时不给锚点():
    assert strategy.anchors(FactLedger(), P, AS_OF) == {}


# ── 提示词 ───────────────────────────────────────────────────

def test_提示词含年报摘录与缺失说明():
    txt = strategy.for_prompt(_led("industry.hhi"), P, AS_OF, _sections())
    assert QUOTE in txt
    assert "年报未设此节" in txt and "不得凭印象补足" in txt


def test_提示词点明矛盾要写出来():
    c = _Client(_out())
    strategy.assess(c, _led("industry.hhi"), P, AS_OF, _sections(), judge=Judge())
    assert "把矛盾写出来" in c.prompts[0]


def test_提示词列出可调参数():
    c = _Client(_out())
    strategy.assess(c, _led(), P, AS_OF, _sections(), judge=Judge())
    assert "revenue_growth" in c.prompts[0] and "gross_margin" in c.prompts[0]


def test_无年报摘录时引文核验不过_记进事实():
    """年报摘录缺失时，年报引文无处可核验 —— 现在作为事实交审核者。"""
    j = Judge()
    strategy.assess(_Client(_out()), _led(), P, AS_OF, None, judge=j)
    assert "核验不过" in j.prompts[0]


# ── 端到端 ───────────────────────────────────────────────────

def test_审核致命则带建议重写():
    c = _Client(_out(), _out())
    strategy.assess(c, _led(), P, AS_OF, _sections(),
                    judge=Judge({("自洽", "护城河、五力与不确定性"): "致命"}, {}))
    assert len(c.prompts) == 2 and "上一轮审核驳回" in c.prompts[1]


def test_三轮审核不过则停下():
    from ir_agent.qual_review import ReviewStop
    with pytest.raises(ReviewStop, match="战略分析审核 3 轮未通过"):
        strategy.assess(_Client(_out(), _out(), _out()), _led(), P, AS_OF, _sections(),
                        judge=Judge({"五力": "致命"}))


def test_审核清单按方法论生成():
    """用户决定 5：五力固定 5 条；不确定性不足 2 条补到 2 条。"""
    items = strategy.checklist(strategy.parse(_out(uncerts=[])))
    assert [s for k, s in items if k == "五力"] == [f.value for f in Force]
    assert [s for k, s in items if k == "不确定性"] == ["第1条（未给出）", "第2条（未给出）"]
    assert items[-1] == ("自洽", "护城河、五力与不确定性")


def test_规则写明应付天数短是议价弱():
    """生益、茅台两次成稿辩论都抓到「应付账款周转天数短 = 议价强」这一误读。"""
    assert "应付账款周转天数短" in strategy.RULES and "**弱**" in strategy.RULES


def test_摘要含三部分():
    v, _ = strategy.assess(_Client(_out()), _led(), P, AS_OF, _sections(), judge=Judge())
    txt = v.summary()
    assert "护城河" in txt and "现有竞争强度" in txt
    assert "关键不确定性 → 情景驱动因子" in txt


# ── 公司之外的视角 ───────────────────────────────────────────

EXT = "系列酒实现收入129.3亿元，同比-6%，终端库存得到有效去化。"


def _corpus():
    from datetime import date as _d

    from ir_agent.sources.external import Corpus, Document, Tier
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="A1", url="https://data.eastmoney.com/report/info/A1.html",
                   title="2026年中报点评", publisher="西南证券", tier=Tier.SELLSIDE,
                   published=_d(2026, 8, 21), retrieved=AS_OF, text=EXT))
    return c


def test_可引用年报之外的资料():
    """只看年报，战略判断的全部依据是公司对自己的描述 —— 相当于让
    被评价方写自己的评价。"""
    v = strategy.parse(_out(moats=[f"护城河: 无形资产 | 宽 | 「{EXT}」"]))
    strategy.facts(v, _led(), QUOTE, AS_OF, corpus=_corpus())


def test_出处由代码判定而非模型声明():
    """模型只写引文。代码先在年报里找，找不到再去语料库找，找到了改标
    为 EXTERNAL 并补上 URL —— 模型没有机会张冠李戴。"""
    from ir_agent.basis import BasisKind

    v = strategy.parse(_out(moats=[f"护城河: 无形资产 | 宽 | 「{EXT}」"]))
    strategy.facts(v, _led(), QUOTE, AS_OF, corpus=_corpus())
    b = v.moats[0].bases[0]
    assert b.kind is BasisKind.EXTERNAL
    assert b.ref.endswith("A1.html") and b.retrieved == AS_OF


def test_年报引文不会被改标为外部():
    from ir_agent.basis import BasisKind

    v = strategy.parse(_out())
    strategy.facts(v, _led(), QUOTE, AS_OF, corpus=_corpus())
    assert v.moats[0].bases[0].kind is BasisKind.REPORT


def test_两处都找不到记进事实():
    v = strategy.parse(_out(moats=["护城河: 无形资产 | 宽 | 「行业景气显著回升」"]))
    assert any("核验不过" in x for x in strategy.facts(v, _led(), QUOTE, AS_OF, corpus=_corpus()))


def test_提示词带上外部资料与其可引用范围():
    txt = strategy.for_prompt(_led(), P, AS_OF, _sections(), corpus=_corpus())
    assert EXT in txt and "西南证券" in txt
    assert "不得作为本报告任何假设的依据" in txt


def test_财经媒体的可引用范围出现在资料正文之前():
    """媒体资料混进语料库后，模型是先读到「仅作线索，不得作为依据」
    才看到正文，还是读完正文才补一句规矩 —— 顺序决定这条约束是否
    真的在起作用。"""
    from datetime import date as _d

    from ir_agent.sources.external import Corpus, Document, Tier, TIER_RULE

    media_text = "白酒板块情绪回暖，机构调仓迹象明显。"
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="M1", url="https://news.example.com/m1",
                   title="板块观察", publisher="某财经网", tier=Tier.MEDIA,
                   published=_d(2026, 8, 1), retrieved=AS_OF, text=media_text))

    txt = strategy.for_prompt(_led(), P, AS_OF, _sections(), corpus=c)
    rule = TIER_RULE[Tier.MEDIA]
    assert rule in txt
    assert txt.index(rule) < txt.index(media_text)


def test_多档来源同时出现时可引用范围按Tier声明顺序排列():
    """Fix 5c 回归：此前用 `{d.tier for d in corpus.docs}` 这个 set 去
    遍历可引用范围，迭代顺序取决于 PYTHONHASHSEED——「[财经媒体] 的
    可引用范围：仅作线索，不得作为依据」这条最要紧的规则落在第一条
    还是最后一条是随机的。改成 `for t in Tier:` 之后，无论语料库里的
    文档以什么顺序加入，"可引用范围"这几行必须固定按 OFFICIAL →
    SELLSIDE → MEDIA 的声明顺序出现。"""
    from datetime import date as _d

    from ir_agent.sources.external import Corpus, Document, Tier, TIER_RULE

    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="M1", url="https://news.example.com/m1",
                   title="板块观察", publisher="某财经网", tier=Tier.MEDIA,
                   published=_d(2026, 8, 1), retrieved=AS_OF, text="媒体正文"))
    c.add(Document(doc_id="A1", url="https://data.eastmoney.com/report/info/A1.html",
                   title="2026年中报点评", publisher="西南证券", tier=Tier.SELLSIDE,
                   published=_d(2026, 8, 21), retrieved=AS_OF, text=EXT))

    txt = strategy.for_prompt(_led(), P, AS_OF, _sections(), corpus=c)
    sellside_rule = TIER_RULE[Tier.SELLSIDE]
    media_rule = TIER_RULE[Tier.MEDIA]
    assert txt.index(sellside_rule) < txt.index(media_rule)


def test_没取到外部资料时明说外部视角缺位():
    """静默地只用年报，读者会以为战略判断有外部印证。"""
    from ir_agent.sources.external import Corpus

    txt = strategy.for_prompt(_led(), P, AS_OF, _sections(),
                              corpus=Corpus(as_of=AS_OF))
    assert "外部视角缺位" in txt


def test_年报正文与外部资料严格分开():
    """若把研报正文拼进年报那段，模型引研报的话会被判为「年报原文」
    通过核验 —— 引文是真的，出处是假的，比引文造假更难发现。"""
    assert EXT not in strategy.annual_text(_sections())
    assert QUOTE in strategy.annual_text(_sections())


def test_力名称写错时_事实里列出合法取值():
    """长电实测：「现有竞争者的竞争强度」与枚举只差两个字。"""
    v = strategy.parse(f"五力: 现有竞争者的竞争强度 | 高 | 中性 | 「{QUOTE}」")
    for f in Force:
        assert f.value in "\n".join(v.read_notes)


def test_护城河类型写错时_事实里也列出合法取值():
    v = strategy.parse(f"护城河: 品牌溢价 | 宽 | 「{QUOTE}」")
    notes = "\n".join(v.read_notes)
    assert "无形资产" in notes and "成本优势" in notes


def test_提示词逐字列出五力名称():
    c = _Client(_out())
    strategy.assess(c, _led(), P, AS_OF, _sections(), judge=Judge())
    for f in Force:
        assert f.value in c.prompts[0]
    assert "逐字照抄" in c.prompts[0]


def test_停下时带上最后一稿():
    from ir_agent.qual_review import ReviewStop
    bad = "五力: 替代品威胁 | 低 | 中性 | 年报未提及替代品，属定性判断"
    with pytest.raises(ReviewStop) as e:
        strategy.assess(_Client(bad, bad, bad), _led(), P, AS_OF, _sections(),
                        judge=Judge({"五力": "致命"}))
    assert "年报未提及替代品" in e.value.last


def test_五力只写了三条_清单仍列满五条():
    """变异验证补的：清单按方法论（固定 5 力）而不是按稿子生成 —— 漏写的力要出现在
    清单里，让审核者判「未给出」（用户决定 5）。"""
    v = strategy.parse(_out(forces=[f"五力: {f.value} | 中 | 中性 | 「{QUOTE}」"
                                    for f in list(Force)[:3]]))
    assert len(v.forces) == 3
    assert [s for k, s in strategy.checklist(v) if k == "五力"] == [f.value for f in Force]
