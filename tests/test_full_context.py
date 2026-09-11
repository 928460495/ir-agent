"""流程接线: 宏观/行业事实必须在撰写之前入账本，且它们的期间要进目录。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ir_agent.full import _macro_periods, _prior_period
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.research import assumption_template


class _R:
    def __init__(self, ledger):
        self.ledger = ledger


def _led(*pairs):
    led = FactLedger()
    for key, per in pairs:
        led.put(Fact(key=key, value=Decimal("0.05"), unit="ratio", period=per,
                     as_of=date(2025, 1, 1), source_id="t",
                     method=Method.COMPUTED))
    return led


# ── 上年同期 ─────────────────────────────────────────────────

def test_上年同期保留期间后缀():
    assert _prior_period("2025FY") == "2024FY"
    assert _prior_period("2025H1") == "2024H1"
    assert _prior_period("2025Q1-Q3") == "2024Q1-Q3"


def test_无法推出上年同期时返回空而非猜测():
    assert _prior_period("") is None
    assert _prior_period("abcd") is None


# ── 宏观期间进目录 ───────────────────────────────────────────

def test_宏观期间被纳入可引用范围():
    """不进目录，正文就引用不到宏观事实 —— 而提示词又要求写宏观一节，
    模型只能写裸数字然后被审计驳回。两边必须对齐。"""
    r = _R(_led(("macro.cpi_yoy", "2026-07"), ("macro.gdp_nominal_yoy", "2026H1"),
                ("revenue", "2025FY")))
    got = _macro_periods(r)
    assert "2026-07" in got and "2026H1" in got
    assert "2025FY" not in got          # 公司报告期由主流程单独传


def test_宏观期间按时间倒序且有上限():
    r = _R(_led(*[("macro.cpi_yoy", f"2026-{m:02d}") for m in range(1, 9)]))
    got = _macro_periods(r)
    assert len(got) <= 6
    assert got[0] == "2026-08"          # 最新在前
    assert list(got) == sorted(got, reverse=True)


def test_无法解析的期间被跳过而非崩溃():
    r = _R(_led(("macro.cpi_yoy", "垃圾期间"), ("macro.cpi_yoy", "2026-07")))
    assert _macro_periods(r) == ("2026-07",)


def test_无宏观事实时返回空():
    assert _macro_periods(_R(_led(("revenue", "2025FY")))) == ()


def test_利率与β的期间也进目录():
    """它们挂在观测日上，不是公司报告期。不进目录，正文就引用不到，
    而提示词又要求用 β 论证 WACC —— 模型只能写裸数字然后被驳回。"""
    r = _R(_led(("rate.cgb_10y", "2026-09-04"), ("beta", "2026-09-04"),
                ("revenue", "2025FY")))
    assert "2026-09-04" in _macro_periods(r)
    assert "2025FY" not in _macro_periods(r)


# ── 假设模板 ─────────────────────────────────────────────────

def test_模板不带推导时保持原样():
    t = assumption_template(5)
    assert "wacc: null" in t and "建议" not in t


def test_行业口径给模型时写成占位符():
    """往 prompt 里塞裸数字再惩罚模型引用它，是自相矛盾的契约。"""
    from decimal import Decimal as Dc

    from ir_agent.citation import audit_bare_numbers
    from ir_agent.industry_facts import IndustryAggregate

    agg = IndustryAggregate(
        target="600519", target_name="贵州茅台", industry="白酒Ⅲ",
        period="2025FY", prior_period="2024FY", as_of=date(2026, 4, 30),
        revenues={"600519": Dc(100), "a": Dc(50), "b": Dc(30), "c": Dc(20)},
        prior={"600519": Dc(80), "a": Dc(50), "b": Dc(30), "c": Dc(20)})

    assert audit_bare_numbers(agg.for_prompt()) == []
    # 给人看的那份仍然是读数，两者不能合一
    assert audit_bare_numbers(agg.summary()) != []


def test_可比口径给模型时不含同业市值():
    """同业市值不是标的的财务事实，没进账本，写进提示词就不可引用。"""
    from decimal import Decimal as Dc

    from ir_agent.citation import audit_bare_numbers
    from ir_agent.comparables import Selection, Sized

    sel = Selection(target="600519",
                    chosen=[Sized("000858", "五粮液", Dc("2794e8")),
                            Sized("600809", "山西汾酒", Dc("1495e8"))],
                    universe=18, dropped=[], target_cap=Dc("16626e8"))
    txt = sel.for_prompt()
    assert "2,794" not in txt and "1,495" not in txt
    assert "五粮液" in txt and "山西汾酒" in txt
    assert audit_bare_numbers(txt) == []


def test_覆盖不足时口径明确禁止下结论():
    from decimal import Decimal as Dc

    from ir_agent.industry_facts import IndustryAggregate, Missing

    agg = IndustryAggregate(
        target="t", target_name="标的", industry="某行业",
        period="2025FY", prior_period="2024FY", as_of=date(2026, 4, 30),
        revenues={"t": Dc(100), "a": Dc(50)},
        prior={"t": Dc(80), "a": Dc(50)},
        missing=[Missing(c, c, "无数据") for c in "wxyz"])
    txt = agg.for_prompt()
    assert "不得给出这三类结论" in txt
    assert "market_share" not in txt          # 不给不成立的占位符


def test_待核实与未出估值是两回事():
    """「AI 填好了判断项等人核实」与「这一轮压根没出估值」要给不同的指引：
    前者下一步是核实并建模，后者下一步是重跑第一阶段。"""
    from ir_agent.research import Stage, next_actions
    a = next_actions(Stage.AWAITING_REVIEW, "600519", out_dir="out/x", year=2025)
    b = next_actions(Stage.NOT_APPLICABLE, "600519", out_dir="out/x", year=2025)
    assert a and "核实" in a[0] and "--value out/x" in "\n".join(a)
    assert b and "--analyse out/x" in b[0]
    assert a != b


def test_折现率口径给模型时不含裸数字():
    """同一个自相矛盾的契约出现过三次: 摘要里、标签里、口径行里。
    这条测试把「我自己的提示词不许有账本外的数字」变成可执行的约束。"""
    from ir_agent.citation import audit_bare_numbers
    from ir_agent.full import _CN_TENOR

    line = (f"  无风险利率（{_CN_TENOR[10]}年期国债）　[[rate.cgb_10y@2026-09-04]]\n"
            "  β（对沪深300，周频，两年窗口，前复权）"
            "　[[beta@2026-09-04]]　R²　[[beta.r_squared@2026-09-04]]")
    assert audit_bare_numbers(line) == []



def test_估值改由ashare_dcf承担且不吞警告():
    """**估值模块整体换掉了。** 原先那套只有 fcf_base、增速、WACC、永续四个
    旋钮，三张表压成几个汇总项 —— 战略分析识别出的六条驱动因子里有三条根本
    没有落脚处。现在交给 ashare-dcf 的三表联动模型。

    随之消失的是「强周期标的判给 PB–ROE」这道硬闸。**周期性判断本身没有
    消失** —— DOL/DFL/DTL 与收入弹性仍在行业画像里，只是不再拦住估值；
    结果可信度改由 ashare-dcf 自己的健全性警告承担（终值占比过高、再投资率
    失真、科目被抽干等）。

    因此这里要守住的是: 那些警告一条都不能被吞掉。
    """
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_valuation)
    assert "dcf.run(" in src
    assert "dcf_res.crosscheck" in src, "交叉核对结果被吞掉了"
    assert "验算未通过" in src, "公式引擎验算失败没有被报出来"

def test_回写失败不丢弃正文():
    """**跑长电第五轮时踩到。** 撰写通过了全部闸门、辩论也跑完并留痕
    （5 项挑战成立），只是有一条挑战没能落实到文字上，整篇报告就被丢掉了。

    读者因此既拿不到分析、也看不到那条挑战 —— 反而更不透明。正确处置是
    保留未修订稿并如实标注哪条没落实，辩论记录里有完整的质疑与裁决。
    """
    import inspect

    from ir_agent import full
    src = inspect.getsource(full._write_and_debate)
    assert "except RevisionFailed" in src, "回写失败仍会连正文一起丢掉"
    assert "未修订稿" in src
    # RevisionFailed 不应再出现在「整轮放弃」的那个 except 里
    fatal = src.split("except (")[-1].split(")")[0]
    assert "RevisionFailed" not in fatal

    run = inspect.getsource(full.run_analysis)
    assert "err and body is None" in run, "有正文时不应报「研究简报」缺失"


def test_辩论失败不丢弃正文():
    """**跑长电第八轮时踩到。** 撰写通过全部闸门、战略分析也过了，辩论的
    挑战者写了派生数字（投资现金流与资本开支的差额 27.6 亿、占比 44%）——
    账本里没有「差额」这条事实，禁止派生算术等于禁止它提出有力的质疑。
    整篇报告因此归零。

    正文有独立的验收审计，辩论里的裸数字进不了正文（修订稿仍要过
    _violations）。保留正文并说明「未经证伪环节」，比连正文一起丢掉诚实。
    """
    import inspect

    from ir_agent import full
    src = inspect.getsource(full._write_and_debate)
    assert "except DebateRefused" in src, "辩论失败仍会连正文一起丢掉"
    assert "未经证伪环节" in src
    fatal = src.split("except (")[-1].split(")")[0]
    assert "DebateRefused" not in fatal
