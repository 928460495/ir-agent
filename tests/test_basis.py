"""假设依据: 从自由文本改为结构化引用。

原设计要求每项假设填 `basis` 字符串，但自由文本让审核门形同虚设 ——
可以写「量价承压期后回归个位数增长」，听起来有依据，却不指向任何东西，
审核者除了凭感觉点头做不了别的。

改为三类**可核验**的引用:
  · FACT     —— 指向事实账本，用占位符寻址，必须解析得到
  · REPORT   —— 指向已经过辩论的正文，引文必须真的出现在正文里
  · EXTERNAL —— 外部来源，必须同时有 URL、原文片段与抓取日期

WACC 的无风险利率、ERP、β 本来就不在账本里，EXTERNAL 是它们的正当出口 ——
但要付出留下可核验痕迹的代价，而不是随手写一句话。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.basis import Basis, BasisKind, InvalidBasis, validate

D = Decimal
AS_OF = date(2026, 6, 1)
BODY = "## 盈利质量\n公司几乎不依赖财务杠杆，权益乘数仅 1.24x。\n"


def ledger():
    from ir_agent.ledger import Fact, FactLedger, Method
    l = FactLedger()
    l.put(Fact(key="revenue.yoy", value=D("-0.0121"), unit="ratio",
               currency=None, period="2025FY", as_of=date(2026, 4, 20),
               source_id="calc_x", method=Method.COMPUTED))
    return l


class TestConstruction:
    def test_fact_basis_needs_a_placeholder(self):
        with pytest.raises(InvalidBasis, match="占位符"):
            Basis.fact("revenue.yoy@2025FY")          # 缺方括号

    def test_external_basis_needs_a_url(self):
        with pytest.raises(InvalidBasis, match="URL"):
            Basis.external("十年期国债", "收益率 2.5%", date(2026, 6, 1))

    def test_external_basis_needs_a_quote(self):
        with pytest.raises(InvalidBasis, match="原文"):
            Basis.external("https://x.cn/a", "  ", date(2026, 6, 1))

    def test_external_basis_needs_a_retrieval_date(self):
        """外部资料会变。没有抓取日期，事后无法判断当时看到的是什么。"""
        with pytest.raises(InvalidBasis, match="抓取日期"):
            Basis.external("https://x.cn/a", "收益率 2.5%", None)

    def test_report_basis_needs_a_quote(self):
        with pytest.raises(InvalidBasis, match="引文"):
            Basis.report("   ")

    def test_valid_bases_construct(self):
        assert Basis.fact("[[revenue.yoy@2025FY]]").kind is BasisKind.FACT
        assert Basis.report("公司几乎不依赖财务杠杆").kind is BasisKind.REPORT
        assert Basis.external("https://x.cn/a", "收益率 2.5%",
                              date(2026, 6, 1)).kind is BasisKind.EXTERNAL


class TestValidateAgainstEvidence:
    def test_fact_reference_must_resolve(self):
        bad = [Basis.fact("[[ebitda@2025FY]]")]
        assert validate(bad, ledger(), BODY, AS_OF)

    def test_resolvable_fact_reference_passes(self):
        assert validate([Basis.fact("[[revenue.yoy@2025FY]]")],
                        ledger(), BODY, AS_OF) == []

    def test_report_quote_must_appear_in_the_body(self):
        """引用一句正文里没有的话，与凭空捏造无异。"""
        probs = validate([Basis.report("公司正在高速扩张")],
                         ledger(), BODY, AS_OF)
        assert probs and "正文" in probs[0]

    def test_report_rejection_says_how_to_fix_it(self):
        """真实事故: 模型引用的是正文里真实存在的一句话，但自己在中间加了
        省略号（「…广泛应用于...」），逐字核验因此判不过。同一条驳回信息
        被原样重试三次，模型都不知道问题出在省略号 —— 驳回信息必须说清
        怎么改才能通过。"""
        probs = validate([Basis.report("公司正在高速扩张")],
                         ledger(), BODY, AS_OF)
        assert probs and ("更短" in probs[0] and "连续" in probs[0]
                          and "省略" in probs[0])

    def test_report_quote_present_passes(self):
        assert validate([Basis.report("几乎不依赖财务杠杆")],
                        ledger(), BODY, AS_OF) == []

    def test_whitespace_differences_are_tolerated_in_quotes(self):
        assert validate([Basis.report("几乎 不依赖 财务杠杆")],
                        ledger(), BODY, AS_OF) == []

    def test_external_reference_is_accepted_as_is(self):
        """外部来源无法在本地核验，但留下了 URL、原文与日期可供人复核。"""
        assert validate([Basis.external("https://x.cn/a", "收益率 2.5%",
                                        date(2026, 6, 1))],
                        ledger(), BODY, AS_OF) == []

    def test_empty_basis_list_is_a_problem(self):
        assert validate([], ledger(), BODY, AS_OF)


# ── MEDIA 级来源：validate() 仍然通过，但要让 corpus 记下来 ──────
#
# TIER_RULE[Tier.MEDIA] 写死「仅作线索，不得作为依据」，此前只是提示词
# 里的一句话——validate()/verify() 对任何分级的文档一视同仁，只要引文
# 逐字出现就放行。用户决定这里警告、不拒绝：见
# ir_agent.sources.external.Corpus.media_cited 字段注释与 verify()
# 文档字符串。


def _corpus_with(tier):
    from ir_agent.sources.external import Corpus, Document, Tier as T
    c = Corpus(as_of=AS_OF)
    c.add(Document(doc_id="x", url="https://example.com/a", title="标题",
                   publisher="某来源", tier=tier, published=date(2026, 5, 1),
                   retrieved=AS_OF, text="行业规模约120亿美元"))
    return c


def test_MEDIA级引文通过validate但记入corpus的media_cited():
    from ir_agent.sources.external import Tier as T
    c = _corpus_with(T.MEDIA)
    bases = [Basis.external("https://example.com/a", "行业规模约120亿美元",
                            AS_OF)]
    assert validate(bases, ledger(), BODY, AS_OF, corpus=c) == []
    assert len(c.media_cited) == 1


def test_SELLSIDE级引文通过validate且不记入media_cited():
    from ir_agent.sources.external import Tier as T
    c = _corpus_with(T.SELLSIDE)
    bases = [Basis.external("https://example.com/a", "行业规模约120亿美元",
                            AS_OF)]
    assert validate(bases, ledger(), BODY, AS_OF, corpus=c) == []
    assert c.media_cited == []




def test_依据层不依赖估值模块():
    """**换掉 DCF 实现时才发现它一直被放错了地方。** Basis 服务的是战略、
    产业链、宏观定性、外部资料这些定性判断，估值只是使用者之一。它此前
    放在 valuation/ 包里纯属历史原因 —— 删估值模块差点把它一起删掉。"""
    import inspect

    from ir_agent import basis
    src = inspect.getsource(basis)
    assert "valuation" not in src.replace("不在 valuation 包里", "")
    assert basis.__name__ == "ir_agent.basis"


# ── 引文核验的规范化: 标点字形不同不是捏造 ────────────────────────
#
# 真实事故（600183 第四轮实跑）: 模型给宏观定性的依据引了「"十五五」，
# 年报正文里写的是「“十五五”」——同一句话，只是引号从弯的变成了直的。
# 逐字核验判不过，连续三次重试都是同一条驳回，MacroViewFailed 整段未
# 生成，级联到研究简报与 DCF 判断项一并缺失，一轮 $7.38 作废。
#
# 这与 citation.py 里那个 `\b` 是同一类缺陷: 规则是对的（引文必须真的
# 出自原文），但对字符的实际形态估计错了。PDF 抽文、模型输出、网页复制
# 三条路径各有各的标点习惯。
class TestQuoteGlyphsAreNotFabrication:
    def test_弯引号与直引号视为同一句(self):
        body = "## 宏观\n中央提出“十五五”时期的新质生产力方向。\n"
        assert validate([Basis.report('"十五五')], ledger(), body, AS_OF) == []

    def test_模型自己把片段加上引号不算捏造(self):
        """正文里本来没有引号，模型引用时自己套了一对——文字一字未改。"""
        body = "## 宏观\n中央提出十五五时期的新质生产力方向。\n"
        assert validate([Basis.report("「十五五时期」")],
                        ledger(), body, AS_OF) == []

    def test_破折号字形不同视为同一句(self):
        body = "## 宏观\n规划期为 2026—2030 年。\n"
        assert validate([Basis.report("2026-2030 年")],
                        ledger(), body, AS_OF) == []

    # ── 反面: 放宽标点不等于放宽文字 ──────────────────────────
    def test_文字不同仍然判为捏造(self):
        """折叠标点若顺手折叠了文字，这条就会变绿——而那意味着核验失效。"""
        body = "## 宏观\n中央提出“十五五”时期的新质生产力方向。\n"
        probs = validate([Basis.report('"十六五')], ledger(), body, AS_OF)
        assert probs and "正文" in probs[0]

    def test_去掉引号不得让两句不同的话合并(self):
        body = "## 宏观\n公司“不”考虑分拆上市。\n"
        probs = validate([Basis.report("公司考虑分拆上市")],
                         ledger(), body, AS_OF)
        assert probs, "剥掉引号后两句话读起来意思相反，不能判为同一句"


def test_结构哨兵_引文核验共用同一个规范化函数():
    """同一条「逐字核验」规则此前在三个文件里各写了一遍，每一份的
    docstring 都声称「与另两处同一套规范化」——而真正保证它们一致的
    只有这三句注释。第四轮事故后只修 basis.py 一处，另外四条核验路径
    （assume 的判断项依据、external 的语料库核验、openweb 的转述核验、
    attribution 的年报出处核验）会继续带着同一个缺陷，而注释照旧声称
    它们一样。

    这里钉的是**函数对象同一**，不是行为相似: 任何人再复制一份实现，
    这条测试立刻变红。
    """
    import ir_agent.assume as assume
    import ir_agent.basis as basis
    import ir_agent.sources.external as external

    assert basis.norm is external.norm
    assert assume._norm is external.norm
