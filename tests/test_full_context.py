"""流程接线: 宏观/行业事实必须在撰写之前入账本，且它们的期间要进目录。"""

from __future__ import annotations

import ast
import inspect
import textwrap
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
    # **这条此前只查了 crosscheck，于是 warnings 被吞了三个月没人发现。**
    # 实测: 终值占 EV 77.7%（>75%）的健全性警告算出来了，只进了看板，
    # 没进终端产物清单 —— 而终端是读者第一眼看到的地方。
    assert "dcf_res.warnings" in src, "健全性警告被吞掉了"
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


# ── Task 10: 方法论预研 → 定向采集 → 按因子填判断项 ─────────────

def test_预研排在数据采集之前():
    """**这是整个改造的要点。** 先问「这个行业靠什么赚钱」，再按答案去
    采集数据 —— 顺序反了就退化成「先收集再解释」。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_analysis)
    assert "methodology.research(" in src or "_research_methodology(" in src
    assert src.index("methodology") < src.index("_analyse_facts("), \
        "方法论预研必须在数据采集之前"


def test_分解式立刻落盘且说明可覆盖():
    """不另设人工门，改用「立刻落盘 + 可中断 + 可覆盖」换早期干预 ——
    门加多了人会开始机械点同意。"""
    import inspect

    from ir_agent import full
    assert full.DRIVERS_MD == "drivers.md"
    src = inspect.getsource(full.run_analysis)
    assert "DRIVERS_MD" in src


def test_预研失败退回不分解而非中断():
    """降级但继续 —— 损失的是精度，不是担保。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_analysis)
    assert "MethodologyFailed" in src
    assert "退回" in src or "不分解" in src


def test_采集结果进批准清单():
    import inspect

    from ir_agent import full
    src = inspect.getsource(full._propose_assumptions)
    assert "factor_data" in src and "decomps" in src


def test_预研与采集使用已开工具的client():
    """`methodology.research`/`gaps.collect`（经 `sources/web.search`）
    都要求传入已显式开启 WebSearch/WebFetch 的 client，否则会安静地
    什么都搜不到而不报错 —— 这条最容易被漏掉，因为它不报错。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_analysis)
    assert "WebSearch" in src and "WebFetch" in src


def test_研究方法论产出落盘为drivers_md(tmp_path, monkeypatch):
    """`_research_methodology` 成功时把分解式写成 drivers.md，供人随时
    打开查看、也可直接改这个文件覆盖后重跑第一阶段。"""
    import ir_agent.sources.taxonomy as tax
    from ir_agent import full
    from ir_agent.sources.taxonomy import Entry

    monkeypatch.setattr(tax, "fetch_all", lambda: [
        Entry(code="600584", name="长电科技", path="电子-半导体-封测")])

    lines = ("分解: 营收 | 乘 | 产能:变化率:年报产能数据 | "
             "单价:变化率:研报测算\n"
             "分解: 毛利率 | 加 | 稼动率摊薄:pp:年报 | 上游成本:pp:研报\n")

    class _R:
        code = "600584"

    m, err = full._research_methodology("600584", _R(), tmp_path,
                                        lambda prompt: lines)
    assert err == ""
    assert m is not None and len(m.decompositions) == 2
    assert (tmp_path / full.DRIVERS_MD).exists()
    assert "封测" in (tmp_path / full.DRIVERS_MD).read_text(encoding="utf-8")


def test_研究方法论解析失败时退回不分解(tmp_path, monkeypatch):
    """联网拿不到闭合分解式是常态（比如模型答非所问），不该让整轮
    研究停摆 —— 退回不分解模式并如实说明原因。"""
    import ir_agent.sources.taxonomy as tax
    from ir_agent import full
    from ir_agent.sources.taxonomy import Entry

    monkeypatch.setattr(tax, "fetch_all", lambda: [
        Entry(code="600584", name="长电科技", path="电子-半导体-封测")])

    class _R:
        code = "600584"

    m, err = full._research_methodology(
        "600584", _R(), tmp_path, lambda prompt: "胡言乱语，没有任何分解行")
    assert m is None
    assert "退回不分解" in err
    assert not (tmp_path / full.DRIVERS_MD).exists()


def test_drivers_md已存在时读回而不重新联网(tmp_path, monkeypatch):
    """I2：drivers.md 末尾承诺「改这个文件后重跑第一阶段即可覆盖」，
    但此前完全没有读回路径——重跑会重新调用 client 预研，把人的修改
    直接盖掉。这条测试在修复前必须失败：drivers.md 已经存在时，
    `fetch_all`/`client` 都不该再被调用。"""
    import ir_agent.sources.taxonomy as tax
    from ir_agent import full, methodology

    def _boom_tax():
        raise AssertionError("drivers.md 已存在，不该再查询行业分类")
    monkeypatch.setattr(tax, "fetch_all", _boom_tax)

    lines = ("分解: 营收 | 乘 | 产能:变化率:年报产能数据 | "
             "单价:变化率:研报测算\n"
             "分解: 毛利率 | 加 | 稼动率摊薄:pp:年报 | 上游成本:pp:研报\n")
    m0 = methodology.parse(lines)
    m0.industry = "电子-半导体-封测"
    (tmp_path / full.DRIVERS_MD).write_text(methodology.to_markdown(m0),
                                            encoding="utf-8")

    def _boom_client(prompt):
        raise AssertionError("drivers.md 已存在，不该再联网预研")

    class _R:
        code = "600584"

    m, err = full._research_methodology("600584", _R(), tmp_path, _boom_client)
    assert err == ""
    assert m is not None and len(m.decompositions) == 2
    assert m.industry == "电子-半导体-封测"


def test_drivers_md人工改过的内容读回后生效(tmp_path, monkeypatch):
    """**这是「可覆盖」这句承诺真正要验证的地方。** 人打开 drivers.md
    改了一处数据线索，重跑第一阶段读回来的必须是改过的那份，不是模型
    原来给的。"""
    import ir_agent.sources.taxonomy as tax
    from ir_agent import full, methodology

    monkeypatch.setattr(tax, "fetch_all",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("不该查询行业分类")))

    lines = ("分解: 营收 | 乘 | 产能:变化率:年报产能数据 | "
             "单价:变化率:研报测算\n"
             "分解: 毛利率 | 加 | 稼动率摊薄:pp:年报 | 上游成本:pp:研报\n")
    m0 = methodology.parse(lines)
    m0.industry = "电子-半导体-封测"
    md = methodology.to_markdown(m0)
    edited = md.replace("年报产能数据", "人工改过：管理层交流纪要")
    (tmp_path / full.DRIVERS_MD).write_text(edited, encoding="utf-8")

    class _R:
        code = "600584"

    m, err = full._research_methodology(
        "600584", _R(), tmp_path, lambda p: (_ for _ in ()).throw(
            AssertionError("不该再联网预研")))
    assert err == ""
    assert m.decompositions[0].factors[0].source_hint == "人工改过：管理层交流纪要"


def test_drivers_md读不回时明确报错且不重新联网(tmp_path, monkeypatch):
    """无法解析时要明确报错，不能静默回退到重新预研——静默回退会让人
    以为自己的修改生效了，实际被丢掉。"""
    import ir_agent.sources.taxonomy as tax
    from ir_agent import full

    monkeypatch.setattr(tax, "fetch_all",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("不该查询行业分类")))

    (tmp_path / full.DRIVERS_MD).write_text("乱写的内容，不含任何标记\n",
                                            encoding="utf-8")

    def _boom_client(prompt):
        raise AssertionError("不该重新联网预研")

    class _R:
        code = "600584"

    m, err = full._research_methodology("600584", _R(), tmp_path, _boom_client)
    assert m is None
    assert "drivers.md" in err
    assert "未重新联网" in err or "不重新" in err or "不会再联网" in err


def test_研究方法论取不到行业分类时降级(tmp_path, monkeypatch):
    """没有申万行业路径就无从预研 —— 降级说明原因，不抛异常中断。"""
    import ir_agent.sources.taxonomy as tax
    from ir_agent import full

    def _boom():
        raise tax.TaxonomyUnavailable("网络不可用")

    monkeypatch.setattr(tax, "fetch_all", _boom)

    class _R:
        code = "600584"

    m, err = full._research_methodology(
        "600584", _R(), tmp_path, lambda prompt: "不会被调用")
    assert m is None
    assert "方法论预研" in err
    assert not (tmp_path / full.DRIVERS_MD).exists()


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


def test_第二阶段必须重建派生事实():
    """**实测踩到，完整研报连挂三次。** 第二阶段不重跑分析层，账本里就没有
    industry.* / macro.* / comps.* 这些派生事实；而喂给撰写的摘要里全是
    [[industry.xxx]] 占位符 —— 一个都解析不了，模型照抄就被判伪造。

    又一次「我往提示词里放了模型用不了的东西」。重建这一层不调模型，
    代价只是几次网络抓取。
    """
    import inspect

    from ir_agent import full
    v = inspect.getsource(full.run_valuation)
    assert "_analyse_facts(" in v, "第二阶段没有重建账本事实"
    assert v.index("_analyse_facts(") < v.index("_write_full_report("), \
        "必须在撰写之前重建，否则占位符仍然解析不了"
    # 确定性层不该调模型 —— 否则第二阶段开销会凭空多一倍
    assert "make_client" not in inspect.getsource(full._analyse_facts)


# ── I4：因子模式连续失败要回落两标量而非让整段判断项消失 ─────────

def test_因子模式降级时判断项仍写出且降级原因入skipped(tmp_path, monkeypatch):
    """I4 修复的落地验证：`assume.decide` 拿到 `AssumptionSet.degraded`
    后，`_propose_assumptions` 仍要把判断项写出来（不是像
    `assume.AssumeFailed` 那样整段消失），并把降级原因作为非致命说明
    通过返回值交给调用方——`run_analysis` 会把它塞进 `skipped`，读者
    在终端产物清单上就能看到这轮其实是降级产出的。"""
    from datetime import date
    from decimal import Decimal

    from ir_agent import assume, dcf, full
    from ir_agent.ledger import FactLedger

    monkeypatch.setattr(
        dcf, "calibrate_only",
        lambda code, as_of, ledger: {
            "judg": {"g_stats": (), "gm_stats": ()},
            "periods": {"proj": ["2026E"]}})

    degraded_msg = "因子模式连续 3 次填不出合格的判断项——已退回两标量模式"

    def _fake_decide(client, judg, labels, ctx, **kw):
        a = assume.AssumptionSet(code="600584", labels=labels)
        a.degraded = degraded_msg
        a.rows = [assume.Row(driver="growth", year=1, bear=Decimal("0.02"),
                             base=Decimal("0.06"), bull=Decimal("0.10"),
                             note="x", bases=[])]
        return a
    monkeypatch.setattr(assume, "decide", _fake_decide)

    class _R:
        ledger = FactLedger()
        period = "2025FY"

    arts: dict[str, str] = {}
    err = full._propose_assumptions(
        "600584", _R(), "body", {}, date(2026, 9, 7), tmp_path,
        lambda p: "", arts)

    assert degraded_msg in err, "降级原因必须作为非致命说明返回，供 skipped 使用"
    assert (tmp_path / full.ASSUMPTIONS_MD).exists(), \
        "判断项必须照常写出，不能因为降级就整段消失"
    assert degraded_msg in (tmp_path / full.ASSUMPTIONS_MD).read_text(
        encoding="utf-8"), "assumptions.md 里也要如实写明降级原因"
    assert "DCF 判断项（待核实）" in arts


def test_因子模式引文未核验的行数入非致命说明(tmp_path, monkeypatch):
    """放宽因子模式引文审核之后的代价：核验不过的行不再致命，但也不能
    只悄悄留在 assumptions.md 的表格里——`_propose_assumptions` 要把
    「N 行依据未能逐字核验」作为非致命说明通过返回值交给调用方，
    `run_analysis` 会把它塞进 `skipped`，终端产物清单上也要看得见。"""
    from datetime import date
    from decimal import Decimal

    from ir_agent import assume, dcf, full
    from ir_agent.basis import Basis
    from ir_agent.ledger import FactLedger

    monkeypatch.setattr(
        dcf, "calibrate_only",
        lambda code, as_of, ledger: {
            "judg": {"g_stats": (), "gm_stats": ()},
            "periods": {"proj": ["2026E"]}})

    def _fake_decide(client, judg, labels, ctx, **kw):
        a = assume.AssumptionSet(code="600584", labels=labels)
        r1 = assume.Row(driver="revenue", year=1, bear=Decimal("-0.02"),
                        base=Decimal("0.03"), bull=Decimal("0.08"), note="x",
                        factor_key="cap", bases=[Basis.report("修复但未完全追平")])
        r1.basis_unverified = "依据引用的正文片段「修复但未完全追平」在正文中找不到"
        r2 = assume.Row(driver="revenue", year=2, bear=Decimal("-0.01"),
                        base=Decimal("0.04"), bull=Decimal("0.09"), note="y",
                        factor_key="cap", bases=[Basis.report("正文里有的话")])
        a.rows = [r1, r2]
        return a
    monkeypatch.setattr(assume, "decide", _fake_decide)

    class _R:
        ledger = FactLedger()
        period = "2025FY"

    arts: dict[str, str] = {}
    err = full._propose_assumptions(
        "600584", _R(), "body", {}, date(2026, 9, 7), tmp_path,
        lambda p: "", arts)

    assert "1 行依据未能逐字核验" in err, \
        "只有 r1 被标注，说明里要报出准确的行数，不是笼统一句话"
    assert "未阻断" in err or "已标注" in err


def test_判断项环节的接口错误降级而不抛出(tmp_path, monkeypatch):
    """模型接口本身失败（额度用尽/超时/CLI 报错）必须降级，不能让整轮炸掉。

    实测教训（600584 第三次真实实跑）：辩论已完成并回写了 5 项成立的
    挑战，brief.md 与 debate.md 都已落盘、钱也花了，紧接着填判断项的
    调用撞上「You've hit your session limit」，而这一处当时只捕
    `AssumeFailed`，于是 ClaudeCliError 以未捕获异常的形式把整轮分析
    连同已有产物一起带走——state/dashboard 全没写出来。

    `run_analysis` 里其他每个联网环节（产业链、因子采集、撰写/辩论）
    都把这类错误记进 skipped 后继续，唯独这一处漏了。**能力边界不是
    故障，整轮研究不该因此停摆。**
    """
    from datetime import date

    from ir_agent import assume, dcf, full
    from ir_agent.clients.claude_cli import ClaudeCliError
    from ir_agent.ledger import FactLedger

    monkeypatch.setattr(
        dcf, "calibrate_only",
        lambda code, as_of, ledger: {
            "judg": {"g_stats": (), "gm_stats": ()},
            "periods": {"proj": ["2026E"]}})

    def _boom(*a, **kw):
        raise ClaudeCliError("claude 返回错误：You've hit your session limit")
    monkeypatch.setattr(assume, "decide", _boom)

    class _R:
        ledger = FactLedger()
        period = "2025FY"

    err = full._propose_assumptions(
        "600584", _R(), "body", {}, date(2026, 9, 7), tmp_path,
        lambda p: "", {})

    assert err, "接口错误必须作为非致命说明返回，而不是抛出"
    assert "ClaudeCliError" in err, "说明里要点明是接口错误，便于排查"
    assert "session limit" in err, "原始错误信息要带上，不能只说失败"


def test_因子模式降级时完整原始输出要落盘(tmp_path, monkeypatch):
    """**排查工具本身也要能用。**

    实测教训：因子模式一次要输出几十行（600584 真实分解式是 9 因子 ×
    5 年 = 45 行），故障常出在中段，而降级说明里只放得下末 400 字。
    连续几轮排查都只能靠残缺的尾巴反推模型写成了什么格式——最后一次
    是从 400 字尾巴里认出「贡献**（无数据，估计——依据：…）」这个
    片段，才判断出模型改用了散文式条目而不是管道分隔行。

    完整草稿必须落盘，但不能混进 assumptions.md——那是给人批准的清单。
    """
    from datetime import date
    from decimal import Decimal

    from ir_agent import assume, dcf, full
    from ir_agent.ledger import FactLedger

    monkeypatch.setattr(
        dcf, "calibrate_only",
        lambda code, as_of, ledger: {
            "judg": {"g_stats": (), "gm_stats": ()},
            "periods": {"proj": ["2026E"]}})

    full_raw = "行一\n" * 200 + "中段才出问题的那一行"

    def _fake_decide(client, judg, labels, ctx, **kw):
        a = assume.AssumptionSet(code="600584", labels=labels)
        a.degraded = "因子模式连续 3 次填不出合格的判断项"
        a.degraded_raw = full_raw
        a.rows = [assume.Row(driver="growth", year=1, bear=Decimal("0.02"),
                             base=Decimal("0.06"), bull=Decimal("0.10"),
                             note="x", bases=[])]
        return a
    monkeypatch.setattr(assume, "decide", _fake_decide)

    class _R:
        ledger = FactLedger()
        period = "2025FY"

    arts: dict[str, str] = {}
    full._propose_assumptions("600584", _R(), "body", {}, date(2026, 9, 7),
                              tmp_path, lambda p: "", arts)

    dumped = tmp_path / full.FAILED_RAW
    assert dumped.exists(), "降级时完整原始输出必须落盘"
    assert dumped.read_text(encoding="utf-8") == full_raw, "要完整，不能截断"
    assert "因子模式失败时的原始输出" in arts, "产物清单要指向它"
    assert "中段才出问题的那一行" not in (
        tmp_path / full.ASSUMPTIONS_MD).read_text(encoding="utf-8"), \
        "失败草稿不能混进给人批准的清单"


def test_第二阶段先重建派生事实再校验批准的假设():
    """**重建派生事实必须排在校验之前。**

    实测教训（600584 端到端）：第一阶段的判断项合法引用了
    `[[industry.dtl@2025FY]]`（行业画像算出的派生事实，只存在于内存
    账本）。第二阶段是新进程，账本刚从接口重建、派生层还没跑，而假设
    校验排在重建之前——于是人已经批准，系统却在建模前一刻报「批准的
    假设不合法，拒绝建模：依据引用的事实 industry.dtl@2025FY 在账本中
    不存在」。理由不是假设有问题，是校验时账本还没建好，**人工批准这道
    关卡因此被架空**。

    用源码顺序断言，而不是跑一遍真流程——真流程要联网抓数、建三表模型，
    在单测里跑不动；而这个 bug 的本质就是两行代码的先后。
    """
    import inspect

    from ir_agent import full

    src = inspect.getsource(full.run_valuation)
    i_rebuild = src.index("_analyse_facts(code, r, as_of, snapshot_dir)")
    i_check = src.index("assume.check(")
    assert i_rebuild < i_check, (
        "重建派生事实必须排在 assume.check 之前，否则第一阶段合法引用的 "
        "industry.* / comps.* / macro.* 派生事实在第二阶段校验时还不存在，"
        "人工批准过的假设会被拒绝建模"
    )
    i_base = src.index("_latest_annual_period(r.ledger, as_of)")
    assert i_rebuild < i_base, \
        "基期毛利率取自派生层的 gross_margin，也要在重建之后再取"


def test_第二阶段check必须与第一阶段用同一个anchor():
    """**C1（final-fix-D 终审）实跑复现。** `_propose_assumptions`（第一
    阶段）给 `assume.check()` 传 `anchor=assume.anchor_from_cal(cal)`，
    查的是锚定平移后的路径；`run_valuation`（第二阶段）此前调 `check()`
    时漏传 `anchor`，`margin_bases` 因此为 `None`，查的是未平移的年报基期
    路径。

    600183 真实数据实测：同一组已批准的 `AssumptionSet`（单因子毛利率
    累计 pp：第 1 年 悲观 0.5/中性 1.3/乐观 2.0，第 2 年 悲观 0.5/中性
    3.1/乐观 3.0，base_margin=0.2647），第一阶段 PASS，第二阶段却报
    「第 2 年的毛利率合成后不再单调（悲观 26.97% / 中性 29.57% / 乐观
    29.47%）」并以 `SystemExit` 拒绝建模——人已经批准的东西下一步就不
    合法了。数字见 `tests/test_assume.py` 的
    `test_两阶段用不同anchor会给出相反结论`。

    用源码断言，不跑真流程——真流程要联网抓数、建三表模型，在单测里跑
    不动（同上一条 `test_第二阶段先重建派生事实再校验批准的假设`）。
    """
    import inspect

    from ir_agent import full

    src = inspect.getsource(full.run_valuation)
    i_check = src.index("assume.check(")
    call = src[i_check:src.index(")", src.index("base_margin=base_margin", i_check))]
    assert "anchor=" in call, (
        "assume.check() 必须传 anchor，否则第二阶段查的是与第一阶段不同的"
        "（未平移的）毛利率路径"
    )
    before_check = src[:i_check]
    assert "calibrate_only(" in before_check, (
        "算 anchor 需要先拿到 calibrate 结果——第二阶段此前从未调用过 "
        "calibrate_only，手里没有 cal，也就传不出正确的 anchor"
    )
    assert "anchor_from_cal(" in before_check, (
        "anchor 必须由 anchor_from_cal(cal) 算出——这与第一阶段 "
        "_propose_assumptions 同源，两处不能各算一套"
    )


def test_未替换的占位符要进终端产物清单():
    """**文件附录里写了不等于交付了。** 600584 实测那次，整段估值漏成
    未替换的占位符，人是读 report.md 才发现的——而终端的 skipped 清单
    才是读者第一眼看到的地方，降级原因、健全性告警、依据未核验都往那里
    报，唯独审计发现的"占位符没被替换"没有。

    这里钉住的是那条通道本身：审计不通过时必须给出一句非致命说明，供
    `run_analysis`/`run_valuation` 塞进 `skipped`。"""
    from ir_agent import full
    from ir_agent.audit import AuditResult

    bad = AuditResult(rendered="每股 [[valuation.value_per_share]]",
                      unrendered=["[[valuation.value_per_share]]"])
    note = full._audit_note("完整研报", bad)
    assert "完整研报" in note and "未替换" in note
    assert "[[valuation.value_per_share]]" in note

    good = AuditResult(rendered="每股 28.92 元")
    assert full._audit_note("完整研报", good) == ""


def test_预研撞上额度上限时降级而不是把整轮炸掉(tmp_path, monkeypatch):
    """**今天 17:53 的实跑现场复现。** `python -m ir_agent 600584 --year
    2025 --analyse out/600584` 跑了 25 秒，确定性层全部完成——eastmoney
    2930 条事实、sina 95 条、三表勾稽通过、跨源勾稽一致、可溯源率
    100%——紧接着第一个联网调用（方法论预研）撞上

        claude 返回错误：You've hit your session limit · resets 8:20pm

    `_research_methodology` 当时只捕 `MethodologyFailed`，于是
    `ClaudeCliError` 以未捕获异常的形式一路冒到 `__main__`，整轮退出码
    1，**产物一个都没写出来**：state、dashboard、简报全没有，前面那
    2930 条事实白抓。

    这与 5db4ca5 修过的是同一个洞、同一个错误消息，只是换了个调用点
    ——那次是填判断项，这次是预研。`run_analysis` 里每一个联网环节都已
    经把这类错误记进 skipped 后继续，唯独预研这一处漏了。**能力边界
    不是故障，整轮研究不该因此停摆。**

    另一层要求：降级理由必须说清这是**接口失败**而不是「这个行业分解
    不了」——两者都退回不分解模式，但前者重跑就能拿回因子分解，后者
    重跑多少次都一样。降级到两标量正是这条产品线最不该悄悄发生的事。
    """
    import ir_agent.sources.taxonomy as tax
    from ir_agent import full
    from ir_agent.clients.claude_cli import ClaudeCliError
    from ir_agent.sources.taxonomy import Entry

    monkeypatch.setattr(tax, "fetch_all", lambda: [
        Entry(code="600584", name="长电科技", path="电子-半导体-封测")])

    def _limit(prompt):
        raise ClaudeCliError(
            "claude 返回错误：You've hit your session limit · resets 8:20pm "
            "(Asia/Singapore)")

    class _R:
        code = "600584"

    m, err = full._research_methodology("600584", _R(), tmp_path, _limit)

    assert m is None
    assert "session limit" in err, "原始错误要如实带出来，人才知道该等额度还是改代码"
    assert "接口" in err, "要说清这是接口失败，不是这个行业分解不了"
    assert "重跑" in err, "要告诉人接口恢复后重跑就能拿回因子分解"
    assert not (tmp_path / full.DRIVERS_MD).exists()


ASSUMPTIONS_ART_KEY = "DCF 判断项（待核实）"


def test_判断项没写出来时不能说待人工核实(tmp_path):
    """**今天 18:08 的实跑现场。** 额度用尽 → 简报没写成 → 判断项根本
    没产出，`out/600584/` 里只有 dashboard.html / model.xlsx / state.json
    三个文件。终端却照样打出

        · 估值方法：分析完成，DCF 判断项待人工核实。核实
          out/600584/assumptions.md（需改数值就改 assumptions.json）
        下一步：
          1. 核实 DCF 判断项…：out/600584/assumptions.md
          3. 确认后进入第二阶段：python -m ir_agent … --value …

    **那个文件不存在**，第 3 步照做只会拿模板默认值（三档同值）去建模。
    `run_analysis` 把 `stage` 无条件设成 `AWAITING_REVIEW`，没有回头看
    判断项到底写出来没有——与「占位符有没有被替换」「批准的数有没有进
    工作簿」是同一类：只按预期路径下结论，不查结果。

    `research.next_actions` 的 docstring 自己写着：「命令必须能直接粘贴
    运行——给一条跑不通的命令比不给指引更糟…**过时的指引是会主动误导人
    的那种错**」。指向一个不存在的文件同样如此。

    判断项没写出来时正确的状态是 `INCOMPLETE`，它给出的指引正是
    「重跑第一阶段即可」——额度用尽这一场景要的就是这句。
    """
    from ir_agent import full
    from ir_agent.research import Stage

    stage, reason = full._review_stage({"看板": "x", "工作簿": "y"})
    assert stage is Stage.INCOMPLETE
    assert "assumptions" not in reason, "没写出来的文件不能出现在指引里"
    assert "待人工核实" not in reason

    # 不能借 NOT_APPLICABLE 顶替：那一档的文案是「本标的不适用 DCF」，
    # 对长电科技来说是一句**假话**——这轮没跑出来的原因是额度用尽，
    # 不是这家公司不适合 DCF。状态是给人看的结论，不能为了省一个枚举
    # 值就说错。
    from ir_agent.research import ResearchOutcome, next_actions
    from datetime import date
    from decimal import Decimal
    o = ResearchOutcome(code="600584", period="2025FY", as_of=date(2026, 9, 13),
                        stage=stage, artifacts={"看板": "x"},
                        skipped=["研究简报"], upheld=0,
                        cost_usd=Decimal("0"), route_reason=reason)
    assert "不适用 DCF" not in o.summary()
    # out_dir 必须用 tmp_path：指真实的 out/600584 会让这条断言依赖仓库
    # 里当时有没有跑出 assumptions.md——实跑产出它之后，这里就会拿到
    # 「接着跑第二阶段」那条指引而不是重跑第一阶段。测试不该被工作目录
    # 的残留状态左右。
    acts = next_actions(stage, "600584", out_dir=str(tmp_path), year=2025)
    assert acts and "--analyse" in acts[0], "要给一条能直接粘贴重跑的命令"

    stage2, reason2 = full._review_stage(
        {ASSUMPTIONS_ART_KEY: "out/600584/assumptions.md"})
    assert stage2 is Stage.AWAITING_REVIEW
    assert "assumptions.md" in reason2 and "待人工核实" in reason2


def test_研报没写出来时不能算已完成(tmp_path):
    """**第二阶段的同一个洞。** `run_valuation` 在建模成功后就把 stage 设成
    `VALUED`，而完整研报是**之后**才写的：`_write_full_report` 撞上额度
    上限（今晚极可能发生）时返回 None、原因进 skipped，stage 却仍是
    `VALUED` —— `complete` 为真、`next_actions` 返回空列表，等于告诉人
    「全做完了，没有待办」，而 report.md 根本不存在。

    与第一阶段那次是同一类：**只按预期路径下结论，不查结果**。"""
    from ir_agent import full
    from ir_agent.research import Stage

    assert full._report_stage(Stage.VALUED, {"DCF 模型": "x"}) is Stage.INCOMPLETE
    assert full._report_stage(
        Stage.VALUED, {"DCF 模型": "x", "完整研报": "y"}) is Stage.VALUED
    # 验算未通过本来就不是 VALUED，不要顺手把它改掉
    assert full._report_stage(
        Stage.AWAITING_REVIEW, {"DCF 模型": "x"}) is Stage.AWAITING_REVIEW


def test_未跑完时的重跑指引要看目录里到底有什么(tmp_path):
    """`INCOMPLETE` 可能来自两个阶段，重跑的命令完全不同——第二阶段
    失败时让人去重跑 `--analyse` 是**有害**的指引：那会重做第一阶段、
    覆盖掉人已经核实过的判断项。

    判据同样是「实际产物」：判断项还在，就说明该接着跑第二阶段。"""
    from ir_agent.research import Stage, next_actions

    acts = next_actions(Stage.INCOMPLETE, "600584",
                        out_dir=str(tmp_path), year=2025)
    assert "--analyse" in acts[0]

    (tmp_path / "assumptions.md").write_text("判断项", encoding="utf-8")
    acts2 = next_actions(Stage.INCOMPLETE, "600584",
                        out_dir=str(tmp_path), year=2025)
    joined = "".join(acts2)
    assert "--value" in joined and "--approved-by" in joined
    assert "--analyse" not in joined, "判断项还在，不能教人重做第一阶段把它覆盖掉"


def _unverified_set():
    from decimal import Decimal as D
    from ir_agent import assume
    from ir_agent.drivers import Decomposition, Factor, Op
    cap = Factor(key="cap", label="产能规模", unit="变化率")
    rev = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[cap])
    a = assume.AssumptionSet(code="600584", labels=["2026E"], decomps=[rev])
    a.rows = [
        assume.Row(driver="revenue", year=1, bear=D("-0.02"), base=D("0.03"),
                   bull=D("0.08"), note="理由", factor_key="cap",
                   bases=[assume.Basis.report("修复但未完全追平")],
                   basis_unverified="引文在正文中找不到"),
        assume.Row(driver="revenue", year=2, bear=D("-0.01"), base=D("0.04"),
                   bull=D("0.09"), note="理由", factor_key="cap",
                   bases=[assume.Basis.report("真引文")]),
    ]
    return a


def test_依据未核验要写进最终研报而不是只留在批准清单():
    """**放宽的代价必须跟着交付物走。** 因子依据从「核验不过就驳回」改成
    「标注后放行」之后，`basis_unverified` 只到 assumptions.md 与终端
    skipped——而对外传阅的是 report.md，读者在那里看不到「这份估值有几个
    判断项的依据没能逐字核验」。

    本仓库自己的原则（`AuditResult.with_sources` 的文档）：脚注「算了不
    交付等于没兑现」。同一条标准适用于这里。

    两个出口分开给，因为约束不同：
    - 进提示词的那句**不能带数字**——研报正文的每个数字都必须是占位符，
      模型照抄一句「12 行」会被裸数字审计判掉，等于我自己给自己埋雷。
    - 进 report.md 的附录块在 `audit()` 之后追加（与数据来源附录同一处
      待遇），可以带准确行数与具体是哪些因子哪些年份。
    """
    import re
    from ir_agent import full

    prompt_line, appendix = full._assumption_caveats(_unverified_set())

    assert prompt_line and "逐字核验" in prompt_line
    assert not re.search(r"\d", prompt_line), \
        "进提示词的这句不能带数字，否则模型照抄会被裸数字审计判掉"

    assert "1" in appendix, "附录要给准确行数"
    assert "产能规模第1年" in appendix
    assert "assumptions.md" in appendix, "要指路到标注在哪"
    assert "产能规模第2年" not in appendix, "核验通过的行不该被写成未核验"


def test_全部依据都核验通过时不加这一段():
    from ir_agent import full
    a = _unverified_set()
    a.rows[0].basis_unverified = ""
    assert full._assumption_caveats(a) == ("", "")


def test_未核验声明真的接进了第二阶段():
    """光有函数不算数：附录要追加进 report.md（且在 audit 之后，附录里的
    数字不该被当成正文裸数字），那句话要真的进撰写提示词。"""
    import inspect
    from ir_agent import full

    src = inspect.getsource(full.run_valuation)
    assert "_assumption_caveats(" in src
    assert src.index("audit(full") < src.index("REPORT"), "附录必须追加在审计之后"
    assert "_assumption_caveats(" in src

    wsrc = inspect.getsource(full._write_full_report)
    assert "caveat" in wsrc and "valuation_summary" in wsrc


def _estimate_only_set():
    """理由自称「无数据（估计）」、依据挂着方向性参照——600584 实跑里
    8 个因子全是这个形状。"""
    from decimal import Decimal as D
    from ir_agent import assume
    from ir_agent.drivers import Decomposition, Factor, Op
    cap = Factor(key="cap", label="产能规模", unit="变化率")
    rev = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[cap])
    a = assume.AssumptionSet(code="600584", labels=["2026E"], decomps=[rev])
    a.rows = [assume.Row(
        driver="revenue", year=1, bear=D("0.03"), base=D("0.06"),
        bull=D("0.10"), factor_key="cap",
        note="理由：资本开支强度处于同业前列。依据：无数据（估计）。产能"
             "规模无官方口径数据，取值为估计",
        bases=[assume.Basis.fact("[[depreciation_amortization@2025FY]]")])]
    return a


def test_取值是估计这件事也要进研报():
    """**实跑当场暴露的缺口。** 600584 那份研报末尾如实写了「8 项依据
    未能逐字核验」，却没写「8 个因子的取值本身就是估计」——而后者更重：
    读者会以为问题只是"出处是转述"，真相是这些数字本身没有数据支撑。

    同一条原则（放宽/软性的代价要跟着交付物走）对它同样适用。约束也
    一样：进提示词的那句不能带数字，进 report.md 的附录可以带。"""
    import re
    from ir_agent import full

    prompt_line, appendix = full._assumption_caveats(_estimate_only_set())

    assert prompt_line and "估计" in prompt_line
    assert not re.search(r"\d", prompt_line), "进提示词的话不能带数字"
    assert "1" in appendix and "产能规模" in appendix
    assert "估计" in appendix


def test_未核验与估计两件事都在时各说各的():
    from ir_agent import full
    a = _estimate_only_set()
    a.rows[0].basis_unverified = "引文在正文中找不到"
    prompt_line, appendix = full._assumption_caveats(a)
    assert "逐字核验" in prompt_line and "估计" in prompt_line
    assert "逐字核验" in appendix and "估计" in appendix


def test_两件事都没有时不加任何声明():
    from decimal import Decimal as D
    from ir_agent import full, assume
    a = _estimate_only_set()
    a.rows[0].note = "理由：资本开支强度处于同业前列，产能投放节奏明确"
    assert full._assumption_caveats(a) == ("", "")


def test_缺快照的引用也要进终端清单():
    """**实跑当场暴露的第二条。** 研报附录如实写了「以下引用缺少原始
    接口快照，无法完整回查：em_macro×7、eastmoney_report×13」，
    `audit.ok` 因此是 False——而终端产物清单一个字没提。

    与「未替换的占位符」同一个函数、同一条原则：算出来了、写进文件了，
    不等于交付了；终端清单才是读者第一眼看到的地方。

    来源名按去重后列出：原始列表里 em_macro 重复 7 次，照抄只会刷屏，
    读者要知道的是「哪几个来源、总共多少条引用」。"""
    from ir_agent import full
    from ir_agent.audit import AuditResult

    res = AuditResult(rendered="正文",
                      missing_snapshots=["em_macro"] * 7
                                        + ["eastmoney_report"] * 13)
    note = full._audit_note("完整研报", res)
    assert "快照" in note
    assert "em_macro" in note and "eastmoney_report" in note
    assert note.count("em_macro") == 1, "来源要去重，不要照抄 7 遍"
    assert "20" in note, "要给出总条数"


def test_审计三类问题同时存在时一条都不吞():
    from ir_agent import full
    from ir_agent.audit import AuditResult
    res = AuditResult(rendered="正文", unrendered=["[[x]]"],
                      missing_snapshots=["em_macro"], bare_numbers=["3.5"])
    note = full._audit_note("完整研报", res)
    assert "未替换" in note and "快照" in note and "裸数字" in note


def test_审计全过时不加说明():
    from ir_agent import full
    from ir_agent.audit import AuditResult
    assert full._audit_note("完整研报", AuditResult(rendered="正文")) == ""


def test_可引用清单不能被逐日利率挤掉整条宏观序列():
    """**生益科技（600183）实跑抓到。** 多空辩论一口气判了 4 项「无据
    断言」，理由是「清单中通篇检索，制造业 PMI、非制造业 PMI、M1 同比、
    M2 同比、M1−M2 剪刀差、PPI 同比、CPI 同比**七个字段均不存在**」。

    但这些事实是**在账本里**的——`_load_macro` 逐条 `ledger.put`，摘要
    里也印着「制造业 PMI（2026-07）49.2」并进了提示词。进不了可引用清单
    的原因在这里：`_macro_periods` 取的是「最新的 6 个**期间**」，而
    `rates.fetch_yields()` 是**逐日**的，六个名额全被最近六个交易日占满，
    月度的 2026-07 与半年度的 2026H1 一个都进不去。

    于是链条变成：摘要给模型看了数、清单里却引不到 → 模型只能照着摘要
    写占位符 → 被判无据断言。仓库自己的话：**「我往提示词里放了模型用
    不了的东西」**。

    这个函数的文档写的本来就是「**每个 key 只需最新一期**」——意图是
    对的，实现按期间去重，把意图丢了。改成按 key 取各自最新一期。
    """
    from datetime import date
    from decimal import Decimal
    from ir_agent import full
    from ir_agent.ledger import Fact, FactLedger, Method

    led = FactLedger()

    def put(key, per, day):
        led.put(Fact(key=key, value=Decimal("1"), unit="ratio", currency=None,
                     period=per, as_of=day, source_id="x",
                     method=Method.REPORTED))

    # 逐日利率：10 个交易日 × 2 个期限，全都比月度/半年度"新"
    for d in range(2, 12):
        day = date(2026, 9, d)
        put("rate.cgb_10y", day.isoformat(), day)
        put("rate.cgb_2y", day.isoformat(), day)
    put("macro.pmi_manufacturing", "2026-07", date(2026, 7, 31))
    put("macro.cpi_yoy", "2026-07", date(2026, 7, 31))
    put("macro.gdp_real_yoy", "2026H1", date(2026, 8, 30))
    put("beta", "2026-09-11", date(2026, 9, 11))

    class _R:
        ledger = led

    pers = full._macro_periods(_R())
    assert "2026-07" in pers, "月度宏观整条序列被逐日利率挤掉了"
    assert "2026H1" in pers, "半年度宏观同样不能被挤掉"
    assert "2026-09-11" in pers, "β 自己那一期也要在"
    assert "2026-09-11" in pers and any(p.startswith("2026-09") for p in pers), \
        "利率最新一期仍要保留"


def test_一致预期缺失时要如实喊出来():
    """**同一轮实跑的第二条。** 生益第一阶段 `brief.md` 里一条
    `consensus.*` 都没有（长电那轮有 14 条），「同业比较与一致预期对照」
    整节失去外部校验——而终端产物清单**一个字没提**。

    `_load_external` 只为「没取到任何券商研报」设了话，一致预期为空时
    直接 `return md, prompt, ""`。研报取到了、一致预期没取到，是很常见的
    组合（多数 A 股没有券商跟踪 EPS 预测），正因为常见才更要说出来，
    否则读者会以为那一节有外部数据背书。
    """
    from datetime import date
    from ir_agent import full, corpus as C

    class _Cs:
        def has_data(self):
            return False

    class _Cp:
        docs = ["一篇研报"]

        def summary(self):
            return "外部资料 1 篇"

    class _R:
        corpus = None
        consensus = None
        ledger = None

    import types
    monk = types.SimpleNamespace()
    orig_gather, orig_md, orig_prompt = C.gather, C.consensus_md, C.consensus_prompt
    C.gather = lambda **kw: (_Cp(), _Cs())
    C.consensus_md = lambda cs: "（无一致预期）"
    C.consensus_prompt = lambda cs: ""
    try:
        _, _, err = full._load_external("600183", _R(), date(2026, 9, 14))
    finally:
        C.gather, C.consensus_md, C.consensus_prompt = orig_gather, orig_md, orig_prompt

    assert err, "一致预期为空必须给出非致命说明"
    assert "一致预期" in err


_ANCHOR = ("首年被「锚定已披露中期实际」覆盖，人批准的值未参与建模：营收增速 批准 "
           "22.31% → 实际用 39.85%；毛利率 批准 27.77% → 实际用 30.69%。")


def _plain_set():
    """依据齐全、不是估计、没有未核验——只剩锚定这一件事。"""
    a = _estimate_only_set()
    a.rows[0].note = "理由：资本开支强度处于同业前列，产能投放节奏明确"
    return a


def test_锚定覆盖首年要进研报附录与提示词():
    """研报正文一直按批准值讲首年，而模型用的是锚定值——必须说出来。
    进提示词那句不带数字，附录里给准确数字。"""
    import re
    from ir_agent import full
    prompt_line, appendix = full._assumption_caveats(_plain_set(), anchor_note=_ANCHOR)
    assert "锚定" in prompt_line
    assert not re.search(r"\d", prompt_line), "进提示词的话不能带数字"
    assert "39.85%" in appendix and "22.31%" in appendix


def test_只有锚定时附录不谈转述与估计():
    from ir_agent import full
    _, appendix = full._assumption_caveats(_plain_set(), anchor_note=_ANCHOR)
    assert "允许转述" not in appendix


def test_年份二起基期被替换要进研报附录与提示词():
    """**I3（final-fix-D 终审）。** `margin_bases` 非空说明毛利率加法链
    第 2 年起的基期已经从年报基期换成各档锚定后的首年水平——这与「首年
    被锚定覆盖」（`anchor_note`）是两件不同的事：即便首年批准值恰好与
    生效值一致（`anchor_note` 为空），第 2 年起的基期替换依然可能发生。
    此前 `_assumption_caveats` 完全不认 `margin_bases`，这件事在研报里
    一个字都没有。约束同样是：提示词那句不带数字，附录可以带。"""
    import re
    from decimal import Decimal
    from ir_agent import full
    a = _plain_set()
    a.margin_bases = {"base": [Decimal("0.2647"), Decimal("0.3069")]}
    prompt_line, appendix = full._assumption_caveats(a)
    assert prompt_line, "只要 margin_bases 非空就该有话要说，不能仍是空串"
    assert "年报基期" in prompt_line
    assert not re.search(r"\d", prompt_line), "进提示词的话不能带数字"
    assert "年报基期" in appendix and "手算" in appendix


def test_两件事都没有也没有基期替换时不加任何声明():
    """三个触发条件（未核验/估计/基期替换）都不成立时，函数仍要能返回
    空串——不能因为新增了 margin_bases 判据就总是有话可说。"""
    from ir_agent import full
    a = _plain_set()
    assert a.margin_bases is None
    assert full._assumption_caveats(a) == ("", "")


def test_锚定覆盖接进第二阶段且终端不按健全性归类():
    """cd142a1 的锚定说明混在 warnings 里，终端打成「估值健全性：首年被…」。
    它不是健全性问题，是人批准的数没被用上——换个前缀，不另起一条。"""
    import inspect
    from ir_agent import full
    src = inspect.getsource(full.run_valuation)
    assert "判断项被锚定覆盖：" in src
    assert "anchor_note=" in src


def test_第二阶段基期替换也要进终端清单():
    """**I3（final-fix-D 终审）第三处披露：终端。** `anchor_note` 只报
    「首年批准值没参与建模」，`margin_bases` 报的是另一件事——第 2 年起
    的毛利率加法链基期也被换掉了。两者互不包含（`anchor_note` 是标量
    首年 approved-vs-effective 的比较，`margin_bases` 是因子分解模式下
    的按档按年基期），终端必须分别能看到，不能指望其中一个顺带把另一个
    带出来。用源码断言：`run_valuation` 里要有一条独立于 `dcf_res.warnings`
    循环、只看 `a.margin_bases` 的 skipped 提示。"""
    import inspect
    from ir_agent import full
    src = inspect.getsource(full.run_valuation)
    assert "a.margin_bases" in src
    i_margin = src.index("a.margin_bases")
    # 必须真的写进 skipped，不能只是算出来没处使用
    tail = src[i_margin:i_margin + 400]
    assert "skipped.append(" in tail


def _cal_with_interim():
    """生益科技 calibrate 返回值里与锚定有关的几项（2026H1 实跑数据）。"""
    return {"interim": {"period": "20260630", "label": "半年报", "rev": 190.2561,
                        "cogs": 131.8622, "share": 0.4785, "gm": 0.3069},
            "rev": {"20251231": 284.3113845944},
            "periods": {"annual": ["20251231"], "proj": ["2026E", "2027E"]}}


def test_批准前就要告诉人首年会被锚定():
    """人在第一阶段批准首年的数，第二阶段建模时才被锚定覆盖——那时已经批完
    了。生益科技实跑：批准 22.31% / 27.77%，生效 39.85% / 30.69%，清单上一个
    字没提。横幅要在批准清单最顶上，给 Base 档生效值并指明哪一年不进模型。"""
    from ir_agent import full
    txt = full._anchor_banner(_cal_with_interim(), ["2026E", "2027E"])
    assert "2026E" in txt and "半年报" in txt
    assert "39.85%" in txt and "30.69%" in txt
    assert "不进模型" in txt


def test_横幅不该说后续年份不受影响():
    """**I3（final-fix-D 终审）。** 因子分解 + 锚定开启时，第 2 年起毛利率
    加法链的基期也已从年报基期换成各档锚定后的首年水平（`margin_bases`）。
    此前横幅说「批准时请把注意力放在后续年份」，暗示后续年份没有被锚定
    影响、只有首年需要留意——这本身就是误导：后续年份的**水平**也被
    锚定抬走了，清单上会出现「2026 27.77% → 2027 32.09%」这种手算复现
    不出来的跳变（年报基期 26.47% + 累计 pp 对不上）。

    `rebased=True` 时横幅必须多说一句：第 2 年起的基期已经换过，不能
    用「年报基期 + 累计 pp」手算复现。"""
    from ir_agent import full
    txt = full._anchor_banner(_cal_with_interim(), ["2026E", "2027E"],
                              rebased=True)
    assert "第 2 年起" in txt
    assert "年报基期" in txt
    assert "手算" in txt or "复现" in txt


def test_没有因子分解锚定时横幅维持原文():
    """两标量模式（或分解式里没有毛利率加法链）不会发生按档按年基期替换，
    `rebased` 默认 False 时横幅不该无中生有地加这句话。"""
    from ir_agent import full
    txt = full._anchor_banner(_cal_with_interim(), ["2026E", "2027E"])
    assert "第 2 年起" not in txt


def test_没有中期数据就不加横幅():
    from ir_agent import full
    cal = _cal_with_interim()
    cal["interim"] = None
    assert full._anchor_banner(cal, ["2026E"]) == ""


def test_横幅真的写进批准清单顶部():
    import inspect
    from ir_agent import full
    src = inspect.getsource(full._propose_assumptions)
    assert "_anchor_banner(cal, labels" in src
    # I3：横幅需要知道毛利率加法链是否被按档按年基期替换过（`a.margin_bases`），
    # 才能决定要不要多说「第 2 年起基期已换」那句话——不能凭 cal/labels 自己
    # 判断（那是 a.decomps 与 anchor 一起解出来的派生量）。
    assert "a.margin_bases" in src


def test_第一阶段未跑完时目录里残留的旧判断项不能当成本轮的(tmp_path):
    """**生益科技重跑（9/15 00:57）当场抓到，是 8689861 自己埋的。** 第一阶段
    撞额度、没写出判断项，终端却说「判断项已核实但本轮未跑完——**不要重跑第一
    阶段**…接着跑第二阶段即可」。目录里那份 assumptions.md 是 9/14 09:18 上一轮
    留下的，建在已被新闸门拒收的旧分解式上（营收链里还有「高端产品收入占比」）。

    按「文件在不在」猜失败的是哪一阶段，就会把残留当成本轮产物，而且给出的
    指引与正确做法正好相反。**阶段必须由结果自己带着，不能从目录里猜。**"""
    from ir_agent.research import Stage, next_actions
    (tmp_path / "assumptions.md").write_text("上一轮的判断项", encoding="utf-8")
    acts = "".join(next_actions(Stage.INCOMPLETE, "600183", out_dir=str(tmp_path),
                                year=2025, phase="analyse"))
    assert "--analyse" in acts and "--value" not in acts
    assert "上一轮" in acts, "残留的旧判断项要点明，免得人拿它去跑第二阶段"


def test_第二阶段未跑完时仍教人接着跑第二阶段(tmp_path):
    from ir_agent.research import Stage, next_actions
    acts = "".join(next_actions(Stage.INCOMPLETE, "600183", out_dir=str(tmp_path),
                                year=2025, phase="value"))
    assert "--value" in acts and "--analyse" not in acts


def test_两个阶段都把自己是哪一阶段带进结果():
    import inspect
    from ir_agent import full
    assert 'phase="analyse"' in inspect.getsource(full.run_analysis)
    assert 'phase="value"' in inspect.getsource(full.run_valuation)
    assert "phase=outcome.phase" in inspect.getsource(full.report)


def _ledger_with_consensus():
    from datetime import date
    from decimal import Decimal
    from ir_agent.ledger import Fact, FactLedger, Method
    led = FactLedger()

    def put(key, per, day):
        led.put(Fact(key=key, value=Decimal("1"), unit="ratio", currency=None,
                     period=per, as_of=day, source_id="x", method=Method.REPORTED))
    for y in ("2026FY", "2027FY", "2028FY"):
        put("consensus.eps", y, date(2026, 9, 1))
        put("consensus.brokers", y, date(2026, 9, 1))
    put("macro.pmi_manufacturing", "2026-08", date(2026, 8, 31))
    for d in range(8, 15):
        put("rate.cgb_10y", date(2026, 9, d).isoformat(), date(2026, 9, d))
    return led


class _Quotes:
    def __init__(self):
        from datetime import date
        from decimal import Decimal
        from ir_agent.ledger import Fact, Method
        self.facts = [Fact(key="market_cap", value=Decimal("1"), unit="元",
                           currency="CNY", period="2026-09-14",
                           as_of=date(2026, 9, 14), source_id="q",
                           method=Method.REPORTED)]


def test_写作方与辩论裁判看的是同一份可引用清单():
    """**生益科技两次第一阶段都抓到的：简报里一致预期 0 条。**

    - 写作方：`corpus.consensus_prompt` 在提示词里直接给出
      `[[consensus.eps@2026FY]]` 这类占位符；正文校验走 `render()` 查**账本**，
      一致预期在账本里，引用能通过。
    - 裁判方：`run_debate` 拿的是 `catalog.render_for_prompt()`——可引用清单，
      而清单只收公司报告期与 `_macro_periods`，**没有 2026FY/2027FY/2028FY**。
      于是裁判判「consensus.eps… 均不在本轮可用事实清单内」→ 无据断言成立 →
      回写时把一致预期的句子删掉。

    每次辩论成功，一致预期就被删光：长电 9/13 辩论失败没回写，简报留下 14 条；
    生益两次辩论成功，0 条；第二阶段研报不跑辩论，13 条。**写作方与裁判方
    必须用同一份「可用事实」。**

    一致预期要收**全部**期间，不能像宏观那样只取每个 key 最新一期——提示词
    三个预测年都给了。"""
    from ir_agent import full

    class _R:
        ledger = _ledger_with_consensus()
        quotes = _Quotes()

    pers = full._catalog_periods(_R())
    for y in ("2026FY", "2027FY", "2028FY"):
        assert y in pers, f"一致预期 {y} 不在清单期间里，裁判会判它无据"
    assert "2026-08" in pers and "2026-09-14" in pers


def test_一致预期的占位符都进了可引用清单():
    from datetime import date
    from ir_agent import full
    from ir_agent.analyst import FactCatalog

    class _R:
        ledger = _ledger_with_consensus()
        quotes = _Quotes()
        period = "2025FY"

    cat = FactCatalog.from_ledger(_R.ledger, period=_R.period, as_of=date(2026, 9, 15),
                                  extra_periods=full._catalog_periods(_R()))
    for y in ("2026FY", "2027FY", "2028FY"):
        assert f"[[consensus.eps@{y}]]" in cat.tokens


def test_两处建清单都用同一个期间来源():
    import inspect
    from ir_agent import full
    for fn in (full._write_and_debate, full._write_full_report):
        src = inspect.getsource(fn)
        assert "_catalog_periods(r)" in src, fn.__name__
        assert "_macro_periods(r) + ((spot" not in src, fn.__name__


def test_第一阶段填判断项时带上锚定输入():
    import inspect
    from ir_agent import full
    assert "anchor=assume.anchor_from_cal(cal)" in inspect.getsource(full._propose_assumptions)


def _call_name(node: "ast.Call") -> str:
    """把一个 `ast.Call` 节点的被调用者还原成点号连接的名字
    （裸名字如 `"_load_openweb"`，属性访问链如 `"openweb.gather"`）。

    只处理这两种形态——本文件的用法只关心「源码里写的是哪个具名调用」，
    被调用者本身是另一个表达式的返回值（如 `foo()()`）不在这组测试
    要覆盖的场景里，返回空串即可，不会被误判成任何一个真实调用名。
    """
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        parts = [f.attr]
        cur = f.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _call_arg_names(call: "ast.Call") -> set[str]:
    """这一个调用点**自己**的实参里出现的变量名（位置参数与关键字参数
    的值表达式里的 `ast.Name`）。按语法树取，不对源码文本做子串定位。"""
    used: set[str] = set()
    for part in list(call.args) + [kw.value for kw in call.keywords]:
        used |= {n.id for n in ast.walk(part) if isinstance(n, ast.Name)}
    return used


def _calls(fn) -> list[tuple[str, int]]:
    """`fn` 函数体里每一个调用点的 (被调用者名字, 源码行号)——按**解析出
    的语法树**取，不按源码文本取。

    **这是这组"调用顺序/调用参数"测试的核心防线。** 这几个测试此前直接
    对 `inspect.getsource(fn)` 做 `str.index()`，那命中的是源码文本里
    **任意位置**第一次出现的同名字符串，注释里的一句话就能命中。真实
    反例（代码评审时验证过）：把 `_load_openweb(...)` 的真实调用挪到
    `_analyse_qualitative(...)` 之后，只在函数早先的位置留一行提到
    `_load_openweb` 的注释——`str.index()` 版本量出的仍是那行注释的
    位置，判「顺序正确」，而真实调用顺序已经错了。

    注释在解析后的语法树里根本不存在节点，`ast.walk` 天然看不见它们，
    只能走到真正的调用表达式上——顺序错了就是错了，不会被同名注释糊弄。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return [(_call_name(n), n.lineno) for n in ast.walk(tree)
            if isinstance(n, ast.Call)]


def test_开放网络采集不在确定性层里():
    """`_analyse_facts` 的不变量是「不调模型，因此两个阶段都要跑」。
    discover 要调模型 —— 放进去会让第二阶段取到与第一阶段不同的文档，
    第一阶段写下的外部引文因此在第二阶段核验不过。"""
    from ir_agent import full

    names = [n for n, _ in _calls(full._analyse_facts)]
    assert not any(n == "_load_openweb" or n.startswith("openweb.")
                   for n in names), names


def test_第一阶段采集排在定性三节之前():
    """战略/产业链/宏观定性要引用开放网络文档的原文，采集必须更早。"""
    from ir_agent import full

    calls = _calls(full.run_analysis)

    def first(name: str) -> int:
        lines = [ln for n, ln in calls if n == name]
        assert lines, f"run_analysis 里没有对 {name} 的真实调用"
        return min(lines)

    assert first("_analyse_facts") < first("_load_openweb"), \
        "_load_openweb 必须排在 _analyse_facts 之后"
    assert first("_load_openweb") < first("_analyse_qualitative"), \
        "_load_openweb 必须排在 _analyse_qualitative 之前"


def test_第二阶段只读回不重新采集():
    """第二阶段不得引入第一阶段没有的文档。"""
    from ir_agent import full

    calls = _calls(full.run_valuation)
    names = [n for n, _ in calls]
    assert "_restore_openweb" in names
    assert "_load_openweb" not in names
    restore_ln = min(ln for n, ln in calls if n == "_restore_openweb")
    facts_ln = min(ln for n, ln in calls if n == "_analyse_facts")
    assert facts_ln < restore_ln


def test_读回排在完整研报撰写之前():
    """_build_evidence 用 r.corpus 做引文核验，读回必须更早。"""
    from ir_agent import full

    calls = _calls(full.run_valuation)
    restore_ln = min(ln for n, ln in calls if n == "_restore_openweb")
    write_ln = min(ln for n, ln in calls if n == "_write_full_report")
    assert restore_ln < write_ln


def test_因子数据采集之后重渲染外部资料清单():
    """真实事故（同一类问题的第二个入口）：`gaps.collect` 的④外部网络
    分支命中时会调 `corpus.add_if_visible` 往 `r.corpus` 里加文档——与
    `_load_openweb` 是同一类"污染了语料库却没让 md 知道"。`md["外部
    资料"]` 在 `gaps.collect` 之前就渲染过一次（紧跟在 `_load_openweb`
    后面），`gaps.collect` 成功之后必须再重渲染一次，否则读者看到的
    篇数与分级构成停留在这次采集之前那一刻。"""
    from ir_agent import full

    calls = _calls(full.run_analysis)
    names = [n for n, _ in calls]
    assert "gaps.collect" in names, names
    collect_ln = min(ln for n, ln in calls if n == "gaps.collect")
    refresh_lns = [ln for n, ln in calls if n == "_refresh_external_md"]
    assert refresh_lns, "run_analysis 里没有对 _refresh_external_md 的真实调用"
    assert any(ln > collect_ln for ln in refresh_lns), \
        "_refresh_external_md 必须在 gaps.collect 成功之后再调一次"


def test_因子采集抛异常时重渲染仍必须发生():
    """上一条测试只按**行号**判断"之后"，看不见异常路径——`gaps.collect`
    在循环里逐因子调用 `corpus.add_if_visible`，往 `r.corpus` 塞文档
    发生在异常抛出**之前**：第 N 个因子已经命中④外部网络分支、写进了
    语料库，第 N+1 个因子采集时才炸，`gaps.collect` 整体以异常退出。
    此前的实现把 `_refresh_external_md(r, md)` 放在 try 块内、紧跟在
    `md["因子数据"] = gaps.summary(factor_data)` 后面——`gaps.collect`
    一抛异常，这两行都被跳过，第 N 个因子已经污染的语料库从未让
    `md["外部资料"]` 知道。把行号往后挪到 try 块末尾也能骗过上一条
    测试（这正是本条要单独存在的理由）。真正堵住这个洞要求刷新落在
    `finally` 分支——`finally` 是 Python 语义保证"不论 try 块成功、
    异常退出都会执行"的唯一位置，因此直接在语法树上定位包住
    `gaps.collect` 调用的 `try` 节点，断言 `_refresh_external_md` 出现
    在它的 `finally` 分支里，而不是 `try`/`else` 分支——只有落在
    `finally` 才能对异常路径生效，行号判断天然分不清这个区别。"""
    from ir_agent import full

    tree = ast.parse(textwrap.dedent(inspect.getsource(full.run_analysis)))
    target = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body_calls = {_call_name(c) for stmt in node.body
                     for c in ast.walk(stmt) if isinstance(c, ast.Call)}
        if "gaps.collect" in body_calls:
            target = node
            break
    assert target is not None, "run_analysis 里没找到包住 gaps.collect 的 try 块"

    finally_calls = {_call_name(c) for stmt in target.finalbody
                     for c in ast.walk(stmt) if isinstance(c, ast.Call)}
    assert "_refresh_external_md" in finally_calls, (
        "_refresh_external_md 必须落在包住 gaps.collect 的 try 块的 "
        "finally 分支里，否则 gaps.collect 抛异常时会被跳过，读者看到"
        "的外部资料清单停在这次采集之前那一刻")


def test_采集用的是开了联网工具的client():
    """不开工具不会报错，只会安静地什么都搜不到 —— 比直接失败更难发现。

    采集必须复用 methodology/gaps 那个已显式开启 WebSearch/WebFetch 的
    `research_client`，不能用默认不开工具的 `qual_client`。

    按语法树取这个调用点**自己**的实参名字，不对源码文本做子串定位——
    否则同一行/同一段里若有别的地方也写着 research_client 或
    qual_client，字符串切片式的定位方式分不清那是不是这次调用自己的参数。
    """
    from ir_agent import full

    tree = ast.parse(textwrap.dedent(inspect.getsource(full.run_analysis)))
    call = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and _call_name(n) == "_load_openweb")
    used: set[str] = set()
    for part in list(call.args) + [kw.value for kw in call.keywords]:
        used |= {n.id for n in ast.walk(part) if isinstance(n, ast.Name)}
    assert "research_client" in used
    assert "qual_client" not in used


def test_开放网络落盘失败不阻断整轮研究(tmp_path, monkeypatch):
    """资料收集的任何失败都不得阻断整轮研究——`openweb.save` 落地是
    `Path.write_text`，输出目录被回收、磁盘写满、权限被收走都会在这里
    抛 `OSError`。此前这一行不在 `openweb.gather` 那个 try/except 的
    保护范围内：一旦炸，异常会一路冒出 `_load_openweb`、冒出
    `run_analysis`，把已经写好的简报、已经花的钱全部一起带走。

    落盘失败还有一个具体到「跨阶段核验」层面的后果，不是随便一句
    "保存失败"能概括的：`res.docs` 此刻已经在 `r.corpus` 里、本轮简报
    仍能引用并核验通过；但 `_restore_openweb` 读的正是这里本该写出的
    文件，读不到就是空语料库——简报里写下的开放网络引文，到了第二阶段
    会被自己的核验闸门拒掉。skipped 里必须说清楚这一点，而不是只说
    "保存失败"。
    """
    from datetime import date

    from ir_agent import full
    from ir_agent.sources import openweb
    from ir_agent.sources.external import Corpus, Document, Tier

    doc = Document(doc_id="ow_1", url="https://example.com/a", title="示例",
                   publisher="示例机构", tier=Tier.MEDIA,
                   published=date(2026, 1, 1), retrieved=date(2026, 1, 1),
                   text="正文", prompt_limit=None, date_source="meta")
    res = openweb.OpenWebResult(docs=[doc])

    monkeypatch.setattr(openweb, "gather",
                        lambda *a, **k: res)

    def _boom(*_a, **_k):
        raise OSError("设备已满")

    monkeypatch.setattr(openweb, "save", _boom)

    class _R:
        corpus = None

    r = _R()
    # 不能让异常传出 _load_openweb —— 这一行本身就是断言：调用若抛出，
    # 测试直接因未捕获异常失败，就是本测试要守住的那条不变量。
    skipped = full._load_openweb("600000", r, date(2026, 1, 1), tmp_path,
                                 client=None, industry="")

    assert isinstance(r.corpus, Corpus)
    joined = "；".join(skipped)
    assert "落盘" in joined or "保存" in joined, skipped
    # 必须说出具体后果：本阶段的文档还在、但读不回，第二阶段核验可能不过——
    # 不能只留一句无信息量的"保存失败"。
    assert "跨阶段" in joined and "核验" in joined, skipped


def test_开放网络采集失败不阻断整轮研究(monkeypatch, tmp_path):
    """spec §七 错误对照表：「discover 整体失败 → 研报照常入库，清单记
    『开放网络采集未执行』」。`_load_openweb` 已经把 `openweb.gather` 包
    在 try/except 里实现了这一条——它调模型、调网络，比落盘（上一条测试
    覆盖的姊妹分支）更容易炸：联网工具没开、模型超时、网络本身抽风都会
    在这里抛异常。但 gather() 本身抛异常这条分支此前一个测试都没有：
    它是「一次联网故障拖垮整轮付费分析」与「报告照常出、清单里记一笔」
    之间唯一的防线。"""
    from datetime import date

    from ir_agent import full
    from ir_agent.sources import openweb
    from ir_agent.sources.external import Corpus

    def _boom(*_a, **_k):
        raise RuntimeError("模型超时")

    monkeypatch.setattr(openweb, "gather", _boom)

    class _R:
        corpus = None

    r = _R()
    # 不能让异常传出 _load_openweb —— 这一行本身就是断言：调用若抛出，
    # 测试直接因未捕获异常失败，就是本测试要守住的那条不变量。
    skipped = full._load_openweb("600000", r, date(2026, 1, 1), tmp_path,
                                 client=None, industry="")

    assert isinstance(r.corpus, Corpus)
    joined = "；".join(skipped)
    assert "开放网络采集未执行" in joined, skipped
    assert "RuntimeError" in joined, skipped


def test_外部资料清单按语料库实际内容重渲染():
    """真实事故（600183 第一阶段）: `_load_external` 渲染完清单之后，
    `_load_openweb` 又往同一个 Corpus 塞了 10 篇【财经媒体】文档 ——
    但 `md["外部资料"]` 没有重算，终端与看板停留在旧口径「外部资料 9 篇 /
    [卖方研究] 9 篇」，而进了三节提示词的实际是 19 篇、其中 10 篇媒体。
    `_refresh_external_md` 必须让 md 反映语料库此刻的真实内容。"""
    from ir_agent import full
    from ir_agent.sources.external import Corpus, Document, Tier

    as_of = date(2026, 1, 1)
    cp = Corpus(as_of=as_of)
    for i in range(9):
        cp.add(Document(doc_id=f"s{i}", url=f"https://x.example.com/s{i}",
                        title=f"研报{i}", publisher="甲证券",
                        tier=Tier.SELLSIDE, published=as_of, retrieved=as_of,
                        text="正文"))

    class _R:
        consensus = None

    r = _R()
    r.corpus = cp
    md = {"外部资料": "外部资料 9 篇（截至 2026-01-01）\n  [卖方研究] 9 篇"}

    # 模拟 _load_openweb 又往同一个语料库塞了 10 篇媒体文档，
    # 但没人告诉 md 这件事发生过。
    for i in range(10):
        cp.add(Document(doc_id=f"ow_{i}", url=f"https://news.example.com/{i}",
                        title=f"报道{i}", publisher="某财经网", tier=Tier.MEDIA,
                        published=as_of, retrieved=as_of, text="正文"))

    full._refresh_external_md(r, md)

    assert "外部资料 19 篇" in md["外部资料"]
    assert "[卖方研究] 9 篇" in md["外部资料"]
    assert "[财经媒体] 10 篇" in md["外部资料"]


def test_openweb_notes两阶段措辞一致且不只报model_dated():
    """真实事故: `_restore_openweb`（第二阶段）此前只报 `model_dated`，
    把 `missing_slots` 与 `fetch_failed` 整个丢掉 —— 而第二阶段产出的
    恰恰是 report.md 这个最终交付物。`_openweb_notes` 是两个阶段共用
    的唯一措辞来源，必须同时带出三样东西，且两次调用（模拟两个阶段）
    产出完全一样的文案。"""
    from ir_agent import full
    from ir_agent.sources import openweb

    res = openweb.OpenWebResult(
        docs=[], missing_slots=["竞争格局与份额"], fetch_failed=2,
        model_dated=1)

    notes_phase1 = full._openweb_notes(res)
    notes_phase2 = full._openweb_notes(res)

    assert notes_phase1 == notes_phase2
    joined = "；".join(notes_phase1)
    assert "2 篇抓取失败" in joined
    assert "1 篇的发布日期由模型给出" in joined
    assert "竞争格局与份额：未取到" in joined


def test_openweb_notes带上具体失败原因区分不同诊断():
    """Fix 4：`fetch_failed` 曾经是个不透明的计数——「模型给了个 PDF」
    「这个域名 DNS 解析不出来」等四种截然不同的诊断打印出来长得一模
    一样，本机代理环境下甚至会让全部候选倒在同一个原因上、读者只看到
    一个数字。`_openweb_notes` 必须把去重后的具体原因带出来。"""
    from ir_agent import full
    from ir_agent.sources import openweb

    res = openweb.OpenWebResult(
        docs=[], fetch_failed=2,
        fetch_failed_reasons=["https://a.example.com/x：DNS 解析失败（模拟）",
                              "https://b.example.com/y：内容类型不是网页"
                              "（application/pdf）"])

    joined = "；".join(full._openweb_notes(res))
    assert "2 篇抓取失败" in joined
    assert "DNS 解析失败" in joined
    assert "内容类型不是网页" in joined


def test_openweb_notes没有失败原因时不报多余内容():
    """回归用例：`fetch_failed_reasons` 为空（旧落盘文件、或
    `OpenWebResult()` 默认值）时不能凭空多出一个冒号或空段——
    `test_openweb_notes两阶段措辞一致且不只报model_dated` 已经在断言
    `"2 篇抓取失败"` 这个精确子串，这里单独补一条更直接的形状断言。"""
    from ir_agent import full
    from ir_agent.sources import openweb

    res = openweb.OpenWebResult(docs=[], fetch_failed=3)
    joined = "；".join(full._openweb_notes(res))
    assert "3 篇抓取失败" in joined
    assert "3 篇抓取失败：" not in joined


def test_openweb_notes失败原因与缺失主题分行不会被误读成同一类():
    """真实事故：`_openweb_notes` 曾经把「N 篇抓取失败：原因1；原因2」
    「缺口1：未取到」「缺口2：未取到」全部揉进用 `；` 连接的同一行，
    而失败原因本身又带着 `：`（`f"{url}：{诊断}"`）——三层分隔符叠在
    一起，读者（以及任何做字符串切分的下游）分不清 "行业规模与增速：
    未取到" 到底是缺失主题清单里独立的一条，还是被算进了抓取失败的第
    四条原因。十条失败原因时这一整行能长到约 1600 字符，直接糊进
    md/state.json/看板。这里用一个刻意让两类信息挨在一起的构造
    （失败原因列表恰好在最后一条也含"：未取到"字样的诊断），验证修复
    后两类信息落在互不相邻、可以按行区分的位置，而不是同一段用 "；"
    分隔的文字。"""
    from ir_agent import full
    from ir_agent.sources import openweb

    res = openweb.OpenWebResult(
        docs=[], fetch_failed=1,
        fetch_failed_reasons=["https://site.example.com/a：内容类型不是"
                              "网页（application/pdf）"],
        missing_slots=["行业规模与增速", "政策与监管"])

    notes = full._openweb_notes(res)
    # 仍然只产出一个 skipped 列表元素——不改变 `_load_openweb`/
    # `_restore_openweb` 把它拼进 skipped 的既有语义（一个元素对应
    # 清单里一行/一条）。
    assert len(notes) == 1
    lines = notes[0].splitlines()
    # 失败原因与缺失主题必须落在不同的行上，才谈得上"能分清"。
    reason_lines = [ln for ln in lines if "内容类型不是网页" in ln]
    slot_lines = [ln for ln in lines
                 if ln.strip().startswith("· ") and "：未取到" in ln]
    assert len(reason_lines) == 1
    assert {"  · 行业规模与增速：未取到",
            "  · 政策与监管：未取到"} <= set(slot_lines)
    # 缺失主题各自独立成行，不与抓取失败那一条挤在同一行、同一段
    # 用 "；" 连接的文字里。
    assert reason_lines[0] not in slot_lines
    for slot_line in slot_lines:
        assert "内容类型不是网页" not in slot_line


# ── _media_tier_note：MEDIA 级引文核验通过，但要在产物清单里警告 ──
#
# `TIER_RULE[Tier.MEDIA]` 写死「仅作线索，不得作为依据」，`Corpus.
# verify()` 却对任何分级的文档一视同仁地放行——用户的决定是警告、不是
# 硬拒绝（产业链分析已经因一次引文核验失败整节夭折过三次）。`verify()`
# 命中 MEDIA 文档时往 `corpus.media_cited` 记一笔，`full._media_tier_note`
# 在两处 `ResearchOutcome` 收尾前读一次、汇总成一条 skipped 条目——与
# `_openweb_notes` 汇总 `fetch_failed_reasons` 是同一个思路。


def test_media_tier_note_MEDIA引文产出产物清单条目():
    from ir_agent import full
    from ir_agent.sources.external import TIER_RULE, Corpus, Tier

    c = Corpus(as_of=date(2026, 1, 1))
    c.media_cited = ["某财经网《标题》（2026-01-01，财经媒体）"]
    notes = full._media_tier_note(c)
    assert len(notes) == 1
    assert "1 处" in notes[0]
    assert TIER_RULE[Tier.MEDIA] in notes[0]


def test_media_tier_note_SELLSIDE引文不产出条目():
    """SELLSIDE/OFFICIAL 引文核验通过时 `verify()` 根本不往
    `media_cited` 记账——`_media_tier_note` 收到一个空列表，必须
    产出空的 skipped 附加内容，不能凭空多出一条。"""
    from ir_agent import full
    from ir_agent.sources.external import Corpus

    c = Corpus(as_of=date(2026, 1, 1))
    assert c.media_cited == []
    assert full._media_tier_note(c) == []


def test_media_tier_note_corpus为None时不报():
    """`r.corpus` 理论上不该是 None（两处调用点之前都已经保证赋值），
    但 `_media_tier_note` 独立作为一个函数，防御性地接住 None，不炸。"""
    from ir_agent import full

    assert full._media_tier_note(None) == []


def test_MEDIA引文经validate后能在corpus上累计并被full汇总():
    """端到端串联三层：`Corpus.verify()` 记账 → `basis.validate()`
    透传 → `full._media_tier_note()` 汇总成产物清单条目。这是测试
    契约要求的用例：MEDIA 级引文通过 `validate()` 并产出产物清单条目；
    SELLSIDE 级引文通过 `validate()` 但不产出条目。"""
    from ir_agent import full
    from ir_agent.basis import Basis, validate
    from ir_agent.ledger import FactLedger
    from ir_agent.sources.external import Corpus, Document, Tier

    as_of = date(2026, 1, 1)
    body = "正文无关紧要"

    media_corpus = Corpus(as_of=as_of)
    media_corpus.add(Document(
        doc_id="m1", url="https://finance.example.com/a", title="行业快讯",
        publisher="某财经网", tier=Tier.MEDIA, published=as_of,
        retrieved=as_of, text="行业规模约120亿美元"))
    media_bases = [Basis.external("https://finance.example.com/a",
                                  "行业规模约120亿美元", as_of)]
    assert validate(media_bases, FactLedger(), body, as_of,
                    corpus=media_corpus) == []
    media_notes = full._media_tier_note(media_corpus)
    assert len(media_notes) == 1

    sell_corpus = Corpus(as_of=as_of)
    sell_corpus.add(Document(
        doc_id="s1", url="https://research.example.com/a", title="行业点评",
        publisher="某券商", tier=Tier.SELLSIDE, published=as_of,
        retrieved=as_of, text="行业规模约120亿美元"))
    sell_bases = [Basis.external("https://research.example.com/a",
                                 "行业规模约120亿美元", as_of)]
    assert validate(sell_bases, FactLedger(), body, as_of,
                    corpus=sell_corpus) == []
    assert full._media_tier_note(sell_corpus) == []


# ── _load_announce：公司公告入语料库（Task 7）────────────────────
#
# 采集本身（候选发现、模型挑选、PDF 取文）都在 `ir_agent.sources.
# announce.gather` 里；这里只测接线——调用时机、失败处置、清单措辞，
# 与 `_load_openweb` 这组测试是同一个思路。

def test_公告采集排在定性三节之前():
    """战略/产业链/宏观定性要引用公告原文，采集必须更早。

    两层要求：（1）`_load_announce` 的返回值必须真的拼进 `skipped`
    ——把 `skipped += _load_announce(...)` 改成裸调用，调用点还在、
    位置也没变，只看「调用是否存在」的写法照样判「顺序正确」，但清单
    条目再也生不出来，要求被悄悄删掉了。这里改为定位 `skipped += ...`
    这个 `ast.AugAssign` 节点本身，确认 `_load_announce` 出现在它的值
    表达式里；（2）位置必须在 `_analyse_facts` 之后、
    `_analyse_qualitative` 之前——用**这个 AugAssign 节点自己的行号**
    判断，不用调用表达式的行号，绑定和位置一起测。`_analyse_facts`/
    `_analyse_qualitative` 的调用行改用 `_calls`（取最小行号），不再
    手写 `names.setdefault(...)`——那种写法记的是遍历顺序里先出现的
    那一行，不是最小行号，两个名字目前都只有一处调用所以暂时无害，
    但这不该是测试正确的理由。
    """
    from ir_agent import full

    calls = _calls(full.run_analysis)

    def first(name: str) -> int:
        lines = [ln for n, ln in calls if n == name]
        assert lines, f"{name} 没有被调用"
        return min(lines)

    tree = ast.parse(textwrap.dedent(inspect.getsource(full.run_analysis)))
    binding = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AugAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "skipped"):
            continue
        if any(isinstance(c, ast.Call) and _call_name(c) == "_load_announce"
              for c in ast.walk(node.value)):
            binding = node
            break
    assert binding is not None, (
        "_load_announce 的返回值没有拼进 skipped（需要 skipped +="
        " _load_announce(...)，裸调用不算）")
    # （3）**目录实参**：「第一阶段写到哪、第二阶段读哪」是整个跨阶段机制
    # 的要害，两处都必须是 `out`（`Path(out_dir)`）。把这里换成别的在作用域
    # 里的目录变量（如 `snapshot_dir`），调用名、返回值绑定、位置全都不变，
    # 行为测试又都是直接拿 tmp_path 调函数——只有断这个调用点自己的实参才
    # 抓得到。
    call = next(c for c in ast.walk(binding.value)
                if isinstance(c, ast.Call) and _call_name(c) == "_load_announce")
    assert "out" in _call_arg_names(call), (
        "_load_announce 的目录实参不是 out——第一阶段写到的位置与第二阶段"
        f"读回的位置对不上（实参名：{sorted(_call_arg_names(call))}）")
    assert binding.lineno < first("_analyse_qualitative")
    assert first("_analyse_facts") < binding.lineno


def test_公告采集不在确定性层里():
    """`_analyse_facts` 的不变量是「不调模型，因此两个阶段都要跑」。
    公告采集要调模型挑标题——放进去会让第二阶段挑出不同的公告，
    第一阶段写下的公告引文因此在第二阶段核验不过。

    用 `_calls` 而不是 `inspect.getsource` 子串匹配——子串匹配对
    「不存在」这类断言不会被蒙混过关（子串命中就是命中），但会对着一个
    正确实现误报：`_analyse_facts` 里随便一句提到「公告」或
    `announce` 的注释就会让它假红。跟着它的姊妹测试
    `test_开放网络采集不在确定性层里` 的写法走。"""
    from ir_agent import full

    names = [n for n, _ in _calls(full._analyse_facts)]
    assert not any(n == "_load_announce" or n.startswith("announce.")
                   for n in names), names


def test_公告采集之后重渲染外部资料清单():
    """公告入的是同一个 Corpus。不重渲染，清单说的是加公告之前的篇数
    ——与子项目 A 终审 C1 是同一个失败模式。"""
    from ir_agent import full

    calls = _calls(full.run_analysis)
    ann = [ln for name, ln in calls if name == "_load_announce"]
    ref = [ln for name, ln in calls if name == "_refresh_external_md"]
    qual = [ln for name, ln in calls if name == "_analyse_qualitative"]
    assert ann and ref and qual
    assert any(r > ann[0] for r in ref), \
        "_load_announce 之后必须再重渲染一次外部资料清单"
    # **上面这条断言本身会通过一个没做这件事的实现**：`gaps.collect`
    # 的 finally 分支里早就有一次跟公告采集完全无关的
    # `_refresh_external_md`（Task 4 那次改动留下的），它的行号天然
    # 大于 `_load_announce`——删掉公告采集后面那次真正的重渲染，
    # `any(r > ann[0] for r in ref)` 照样为真，测试却应该红。踩过这个
    # 坑之后（Step 6 逐条验证结构性测试时实测复现），补一条更紧的
    # 断言：重渲染必须落在「公告采集」与「定性三节」这两个调用点**之间**
    # ——`gaps.collect` 那次晚于 `_analyse_qualitative`，落不进这个区间，
    # 不会再帮一个删掉了正确调用的实现蒙混过关。
    assert any(ann[0] < r < qual[0] for r in ref), \
        ("_load_announce 与 _analyse_qualitative 之间必须有一次"
         "_refresh_external_md，不能靠 gaps.collect 那次晚于"
         "定性三节的重渲染顶替")


def test_公告采集失败不阻断整轮研究(monkeypatch, tmp_path):
    from datetime import date

    from ir_agent import full
    from ir_agent.sources.external import Corpus

    class _R:
        as_of = date(2026, 9, 30)
        corpus = Corpus(as_of=date(2026, 9, 30))

    # `_company_name(code)` 是 `announce.gather` 的一个*实参*，求值发生
    # 在被打桩的 `gather` 真正跑起来之前——不挡住它，这条纯接线测试就会
    # 去调 `taxonomy.fetch_all()`（跨网络分页拉巨潮），拖慢这个文件却
    # 测不出任何东西：`_company_name` 自己吞掉所有异常，取不到真实公司名
    # 不会让测试失败，只会让它变慢。
    monkeypatch.setattr(full, "_company_name", lambda c: "某公司")
    import ir_agent.sources.announce as A
    monkeypatch.setattr(A, "gather",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("巨潮超时")))
    skipped = full._load_announce("600183", _R(), date(2026, 9, 30),
                                  tmp_path, lambda p: "", {}, ["名义产能"])
    assert skipped and "公司公告采集未执行" in skipped[0]
    assert "巨潮超时" in skipped[0]


def test_公告采集成功但全部取文失败清单区分候选挑选与入库(monkeypatch,
                                                         tmp_path):
    """`_load_announce` 的成功路径此前完全没测——四条既有测试是三条
    AST 结构检查加一条异常路径。0 篇入库（PDF 全部取文失败）恰恰是真实
    运行最容易撞见的样子，也是最需要区分清楚的：「候选 220 条里挑了
    3 条」与「候选 3 条全挑了」含义完全不同，`md["公司公告"]` 与拼进
    `skipped` 的说明都必须把候选、模型挑选、入库三个数字分开说。
    字段名取自 `announce.py` 的 `AnnounceResult`（candidates/picked/
    fetch_failed/fetch_failed_reasons/ignored/docs）。"""
    from datetime import date

    from ir_agent import full
    from ir_agent.sources.external import Corpus
    import ir_agent.sources.announce as A

    class _R:
        as_of = date(2026, 9, 30)
        corpus = Corpus(as_of=date(2026, 9, 30))

    res = A.AnnounceResult(
        candidates=220, picked=3, fetch_failed=1,
        fetch_failed_reasons=["巨潮 PDF 下载超时：2026年半年度报告摘要.pdf"],
        ignored=["选取: 99（越界）"], docs=[])
    monkeypatch.setattr(full, "_company_name", lambda c: "某公司")
    monkeypatch.setattr(A, "gather", lambda *a, **k: res)

    md: dict = {}
    skipped = full._load_announce("600183", _R(), date(2026, 9, 30),
                                  tmp_path, lambda p: "", md, ["名义产能"])

    assert skipped == [
        "公司公告：一篇都没入库（候选 220 条，模型挑 3 条，取文失败 1 篇）"]
    assert md["公司公告"] == (
        "公司公告 0 篇（候选 220 条，模型挑 3 条）\n"
        "  – 1 篇取文失败\n"
        "    · 巨潮 PDF 下载超时：2026年半年度报告摘要.pdf\n"
        "  – 忽略了一条挑选：选取: 99（越界）")


# ── ④外部网络分支主机安全校验降级——轮次级聚合（Task 7 附加需求）──
#
# `gaps.FactorData.host_check_degraded` 由 Task 5/6 引入，只在 EXTERNAL
# 分支赋值（该因子的证据里有没有哪条命中时经历过主机安全校验降级），
# 但从未被聚合进产物清单——`gaps.py:135-146` 的字段注释原话说这件事
# 属于本任务。`_openweb_notes` 已经有同一件事的文档级聚合（读
# `OpenWebResult.host_check_degraded`），但那条聚合只覆盖
# `openweb.gather` 这一个 fetch 调用点；`gaps.collect` ④分支走的是
# `verify_finding` 这第二个调用点，此前一直没有对应的轮次级汇总，见
# `gaps.py` 第 394-410 行的注释。

def test_factor_host_check_note_有因子降级时产出条目():
    """正例：至少一个因子的 `host_check_degraded` 为真时必须产出一条
    产物清单条目，且措辞与 `_openweb_notes` 那句同源——只换计数单位。"""
    from ir_agent import full
    from ir_agent import gaps
    from ir_agent.drivers import Factor

    fd_ok = gaps.FactorData(factor=Factor(key="capacity", label="产能",
                                          unit="吨"),
                            status=gaps.EXTERNAL, host_check_degraded=True)
    fd_clean = gaps.FactorData(factor=Factor(key="asp", label="单价",
                                             unit="元"),
                               status=gaps.EXTERNAL,
                               host_check_degraded=False)
    notes = full._factor_host_check_note({"capacity": fd_ok, "asp": fd_clean})
    assert len(notes) == 1
    assert "1 个因子" in notes[0]
    assert "主机安全校验降级" in notes[0]
    assert "本机 DNS 被代理劫持，IP 层校验无从进行" in notes[0]
    assert "内网地址拦截本轮依赖代理侧" in notes[0]


def test_factor_host_check_note_没有因子降级时不产出条目():
    """反例（正例的必要配对，防住 `if any(...)` 被改成 `if True`
    这类突变仍能通过测试）：全部因子 `host_check_degraded` 均为假——
    哪怕因子数据本身不为空——必须返回空列表，不能凭空多出一条。"""
    from ir_agent import full
    from ir_agent import gaps
    from ir_agent.drivers import Factor

    fd_clean_1 = gaps.FactorData(factor=Factor(key="capacity", label="产能",
                                               unit="吨"),
                                 status=gaps.EXTERNAL,
                                 host_check_degraded=False)
    fd_clean_2 = gaps.FactorData(factor=Factor(key="asp", label="单价",
                                               unit="元"),
                                 status=gaps.MISSING)
    assert full._factor_host_check_note(
        {"capacity": fd_clean_1, "asp": fd_clean_2}) == []


def test_factor_host_check_note_空字典不报():
    """`meth is None` 时 `run_analysis` 里 `factor_data` 保持默认的
    空字典（`gaps.collect` 压根没跑）——防御性地接住空字典，不炸。"""
    from ir_agent import full

    assert full._factor_host_check_note({}) == []


def test_factor_host_check_note_只读结构化字段不碰note文本():
    """`assume.py` 记录过的事故：下游代码曾经从 `note`（给人看的展示
    文本）里正则反解结构化事实，note 措辞一变就悄悄失效。
    `_factor_host_check_note` 必须只读 `FactorData.host_check_degraded`
    这个布尔字段——构造一个 `note` 里写着「降级」字样、但结构化字段为
    `False` 的因子，确认不会被误判为降级。"""
    from ir_agent import full
    from ir_agent import gaps
    from ir_agent.drivers import Factor

    fd = gaps.FactorData(factor=Factor(key="capacity", label="产能",
                                       unit="吨"),
                         status=gaps.EXTERNAL,
                         note="其中 3 条命中时主机安全校验降级（本机 DNS "
                              "被代理劫持，IP 层校验无从进行）",
                         host_check_degraded=False)
    assert full._factor_host_check_note({"capacity": fd}) == []


def test_gaps_collect调用点会把降级聚合追加进skipped():
    """接线本身：`run_analysis` 里 `gaps.collect` 那段必须真的调用
    `_factor_host_check_note` **并把结果拼进 `skipped`**——只有
    `_factor_host_check_note` 自身正确、调用点没接上，效果一样是零。

    **只断言调用点存在还不够。** 把 `skipped += _factor_host_check_note(
    factor_data)` 改成裸调用 `_factor_host_check_note(factor_data)`，
    调用还在、行号没变，只找 `ast.Call` 节点、只看行号落在 try 块之后
    的写法照样判「接上了」，但聚合结果再也没有任何地方接住，产物清单
    里永远不会出现这条条目——要求被悄悄删掉了。这里改为定位
    `skipped += ...` 这个 `ast.AugAssign` 节点本身，确认
    `_factor_host_check_note` 出现在它的值表达式里，再用**这个节点
    自己的行号**判断位置，绑定和位置一起测。"""
    import ast
    import inspect
    import textwrap

    from ir_agent import full
    tree = ast.parse(textwrap.dedent(inspect.getsource(full.run_analysis)))
    target = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body_calls = {_call_name(c) for stmt in node.body
                     for c in ast.walk(stmt) if isinstance(c, ast.Call)}
        if "gaps.collect" in body_calls:
            target = node
            break
    assert target is not None, "run_analysis 里没找到包住 gaps.collect 的 try 块"

    # `_factor_host_check_note` 的调用点不要求恰好落在 try/finally
    # 哪个分支——它读的是 factor_data 这个已经算出来的字典，不像
    # `_refresh_external_md` 那样需要在异常路径上也执行（因子级
    # 降级信息本就只在 gaps.collect 成功返回时才有意义）。只要求
    # 调用点在包住 gaps.collect 的这个 try 节点之后。
    try_end_ln = max(n.lineno for n in ast.walk(target)
                     if hasattr(n, "lineno"))

    binding = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AugAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "skipped"):
            continue
        if any(isinstance(c, ast.Call)
              and _call_name(c) == "_factor_host_check_note"
              for c in ast.walk(node.value)):
            binding = node
            break
    assert binding is not None, (
        "run_analysis 里没有把 _factor_host_check_note(...) 的返回值拼进"
        " skipped（需要 skipped += _factor_host_check_note(...)，裸调用"
        "不算）")
    assert binding.lineno > try_end_ln, (
        "_factor_host_check_note 的 skipped 聚合必须落在 gaps.collect 的"
        " try 块之后")


def test_公告落盘失败不阻断整轮研究(monkeypatch, tmp_path):
    """`save()` 底层是 Path.write_text，输出目录被回收/磁盘写满/权限被收回
    都会在这里炸——那是运行环境的事，不是采集失败，不该把整轮 analyse
    拖下水。但也不能只说「保存失败」：文档此刻已在 r.corpus 里、本轮简报
    仍能引用，跨阶段却存活不了，第二阶段的核验会拒掉那些引文。这个具体
    后果必须说出来。"""
    from datetime import date

    from ir_agent import full
    from ir_agent.sources import announce as A
    from ir_agent.sources.external import Document, Tier

    doc = Document(doc_id="a1", url="http://static.cninfo.com.cn/a.PDF",
                   title="投产公告", publisher="生益科技", tier=Tier.COMPANY,
                   published=date(2026, 8, 1), retrieved=date(2026, 9, 30),
                   text="正文")
    monkeypatch.setattr(full, "_company_name", lambda c: "某公司")
    monkeypatch.setattr(A, "gather", lambda *a, **k: A.AnnounceResult(
        docs=[doc], candidates=9, picked=1))

    def boom(res, path):
        raise PermissionError("只读目录")
    monkeypatch.setattr(A, "save", boom)

    class _R:
        corpus = None
        as_of = date(2026, 9, 30)

    md = {}
    skipped = full._load_announce("600183", _R(), date(2026, 9, 30),
                                  tmp_path, object(), md, ["名义产能"])
    joined = "；".join(skipped)
    assert "落盘" in joined or "存活" in joined, "落盘失败没进清单"
    assert "核验" in joined, \
        "只说了保存失败，没说出「第二阶段引文会核验不过」这个具体后果"
    assert md.get("公司公告"), "落盘失败不该连带抹掉本轮的公告面板"


def test_第二阶段读回公告而不重新采集(monkeypatch, tmp_path):
    """重跑挑选会挑出**不同的**公告，第一阶段正文里的公告引文因此核验
    不过。这正是落盘机制要防的事——所以第二阶段只读回，绝不调 gather。"""
    from datetime import date

    from ir_agent import full
    from ir_agent.sources import announce as A
    from ir_agent.sources.external import Corpus, Document, Tier

    doc = Document(doc_id="a1", url="http://static.cninfo.com.cn/a.PDF",
                   title="投产公告", publisher="生益科技", tier=Tier.COMPANY,
                   published=date(2026, 8, 1), retrieved=date(2026, 9, 30),
                   text="本次投产新增名义产能 300 万平方米。")
    A.save(A.AnnounceResult(docs=[doc], candidates=220, picked=3),
           tmp_path / A.ANNOUNCE_JSON)

    def never(*a, **k):
        raise AssertionError("第二阶段重新采集了公告——落盘机制形同虚设")
    monkeypatch.setattr(A, "gather", never)

    class _R:
        corpus = Corpus(as_of=date(2026, 9, 30))
        as_of = date(2026, 9, 30)

    r = _R()
    notes = full._restore_announce(r, tmp_path)
    assert any(d.tier is Tier.COMPANY for d in r.corpus.docs), \
        "公告没读进第二阶段的语料库"
    # 不能只断 `"3" in text`：单个数字会撞上措辞里别处出现的同一个字符。
    # 断完整短语，「候选 220 条」与「挑 3 条」各自是一个不会偶然出现的片段。
    assert "候选 220 条" in "；".join(notes) and \
        "挑 3 条" in "；".join(notes), \
        "第二阶段的清单要说得出「候选 220 条里挑了 3 条」"
    # **需求是读者看到什么，不是函数返回了什么。** 上面断的是返回值，
    # 而 `research._mark` 按前缀给标记、不认识的一律打成「– 未生成：」——
    # 每次成功的 `--value` 都把这条成功读回渲染成「未生成：公司公告 1 篇」，
    # 返回值断言全绿。这里走真正的渲染路径（`ResearchOutcome.summary()`），
    # 断**渲染出的那一行**。
    from decimal import Decimal

    from ir_agent.research import ResearchOutcome, Stage
    rendered = ResearchOutcome(
        code="600183", period="2025FY", as_of=date(2026, 9, 30),
        stage=Stage.VALUED, skipped=notes, cost_usd=Decimal(0)).summary()
    line = next(ln for ln in rendered.splitlines() if "候选 220 条" in ln)
    assert "未生成" not in line, f"成功读回被渲染成了没生成：{line!r}"
    assert line.lstrip().startswith("·"), \
        f"成功读回应是信息行（· 开头），实际渲染：{line!r}"


def test_读回零文档时三种成因分开交代(tmp_path):
    """**这里不能照抄 `_restore_openweb`。** 它用
    `set(missing_slots) == set(SLOTS)` 区分「第一阶段真的没采到」与
    「文件损坏」。公告没有槽位，而 `candidates` 在两种情形下都是 0——
    那个信号在这里不存在。所以诚实的做法是分三种情形，并且在分辨不出来
    的那一种里**说出两种可能**，而不是挑一种猜。"""
    from datetime import date

    from ir_agent import full
    from ir_agent.sources import announce as A
    from ir_agent.sources.external import Corpus

    class _R:
        def __init__(self):
            self.corpus = Corpus(as_of=date(2026, 9, 30))
            self.as_of = date(2026, 9, 30)

    # (a) 文件不存在 —— 没落过盘
    n = n_a = "；".join(full._restore_announce(_R(), tmp_path))
    assert "未跨阶段存活" in n and A.ANNOUNCE_JSON in n

    # (b) 有候选但一篇都没入库 —— 可分辨，要说出候选数
    A.save(A.AnnounceResult(docs=[], candidates=220, picked=3, fetch_failed=3),
           tmp_path / A.ANNOUNCE_JSON)
    n = n_b = "；".join(full._restore_announce(_R(), tmp_path))
    assert "候选 220 条" in n, \
        "有候选这件事没说出来，读者无从判断是没搜到还是没取到"
    assert "损坏" not in n, "可分辨的情形不该混进「可能损坏」的措辞"

    # (c) 候选为零 —— **分辨不出**「第一阶段确实没候选」与「文件损坏」
    A.save(A.AnnounceResult(docs=[], candidates=0), tmp_path / A.ANNOUNCE_JSON)
    n = "；".join(full._restore_announce(_R(), tmp_path))
    assert "确实没有候选" in n and "损坏" in n, \
        "分辨不出来的情形必须把两种可能都说出来，不能挑一种猜"
    # 反向也要断：(a)(b) 是可分辨的情形，不能把 (c) 那句「两种都可能」
    # 的措辞抄过去——否则一个对所有零文档情形都说同一句话的实现也能过。
    for case, text in (("a", n_a), ("b", n_b)):
        assert "确实没有候选" not in text, f"({case}) 混进了 (c) 的措辞"


def test_第一阶段落盘第二阶段读回_含零入库也要落盘(monkeypatch, tmp_path):
    """上面三条分别测了落盘失败、读回、零文档措辞，**中间那一环没人测**：
    `_load_announce` 在成功路径上真的把文件写出来了，且读回的就是它写的。

    零入库（PDF 全部取文失败）也必须落盘——不是只在 `res.docs` 非空时
    才写。第二阶段靠文件里的 `candidates` 才能把「候选 220 条但一篇没取到」
    与「根本没落过盘」分开；只在有文档时才写，这个区分就丢了。

    同时钉住 `cache_dir=out / "pdf"`：Task 7 的审查把「从不传 cache_dir，
    公告 PDF 缓存静默关闭」记为 Minor，现在拿得到 `out` 了，必须真的传进去；
    且要进专用子目录——与 `pipeline.py` 的 `Path(snapshot_dir) / "pdf"` 同
    一个惯例，原始 PDF 不能散落在 report.md/state.json 旁边的交付目录里。"""
    from datetime import date

    from ir_agent import full
    from ir_agent.sources import announce as A
    from ir_agent.sources.external import Corpus

    seen: dict = {}

    def fake_gather(*a, **k):
        seen.update(k)
        return A.AnnounceResult(candidates=220, picked=3, fetch_failed=3,
                                fetch_failed_reasons=["超时"])
    monkeypatch.setattr(full, "_company_name", lambda c: "某公司")
    monkeypatch.setattr(A, "gather", fake_gather)

    class _R:
        corpus = Corpus(as_of=date(2026, 9, 30))
        as_of = date(2026, 9, 30)

    full._load_announce("600183", _R(), date(2026, 9, 30), tmp_path,
                        object(), {}, ["名义产能"])
    assert seen.get("cache_dir") == tmp_path / "pdf", \
        "没把 out/pdf 作为 cache_dir 传给 announce.gather——PDF 缓存仍是关的，" \
        "或原始 PDF 落进了交付目录"
    assert (tmp_path / A.ANNOUNCE_JSON).exists(), "零入库时没落盘"

    class _R2:
        corpus = Corpus(as_of=date(2026, 9, 30))
        as_of = date(2026, 9, 30)
    notes = "；".join(full._restore_announce(_R2(), tmp_path))
    assert "候选 220 条" in notes and "挑 3 条" in notes, \
        "第二阶段读回的不是第一阶段写出的那份头部计数"
    assert "未跨阶段存活" not in notes, "落过盘的情形被报成了没落盘"


def test_第二阶段读回公告的返回值汇入skipped():
    """`run_valuation` 里的 `_restore_announce(r, out)` 必须是
    `skipped += _restore_announce(...)`。裸调用时文档照样读回、语料库照样
    有内容，但三种零文档措辞与「候选 220 条挑 3 条」永远进不了产物清单
    ——本支线上一代结构性测试就栽在这里：调用存在、返回值被丢弃，测试全绿。

    定位 `skipped +=` 这个 `ast.AugAssign` 节点本身，要求调用名出现在
    它的值表达式里，位置检查落在**这个节点**的行号上（不是调用表达式的
    行号，更不是 `_restore_openweb` 那一处）。"""
    from ir_agent import full

    calls = _calls(full.run_valuation)
    tree = ast.parse(textwrap.dedent(inspect.getsource(full.run_valuation)))
    binding = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AugAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "skipped"):
            continue
        if any(isinstance(c, ast.Call) and _call_name(c) == "_restore_announce"
               for c in ast.walk(node.value)):
            binding = node
            break
    assert binding is not None, (
        "_restore_announce 的返回值没有拼进 skipped（需要 skipped +="
        " _restore_announce(...)，裸调用不算）")
    # 目录实参：必须与第一阶段落盘用的是同一个 `out`（见
    # `test_公告采集排在定性三节之前` 里同样的断言）。
    call = next(c for c in ast.walk(binding.value)
                if isinstance(c, ast.Call)
                and _call_name(c) == "_restore_announce")
    assert "out" in _call_arg_names(call), (
        "_restore_announce 的目录实参不是 out——第二阶段读的位置与第一阶段"
        f"写的位置对不上（实参名：{sorted(_call_arg_names(call))}）")
    facts_ln = min(ln for n, ln in calls if n == "_analyse_facts")
    write_ln = min(ln for n, ln in calls if n == "_write_full_report")
    assert facts_ln < binding.lineno < write_ln, \
        "读回必须在重建事实之后、完整研报撰写（引文核验）之前"
    # 第二阶段绝不能重新采集——重跑挑选会挑出不同的公告
    assert "_load_announce" not in [n for n, _ in calls]


def test_采到了之后的注意事项清单不被渲染成未生成():
    """`research._mark` 是白名单：认识的前缀给标记，不认识的一律打成
    「– 未生成：」。于是每新增一类清单条目，忘了登记的后果不是报错，
    而是**默认落到最强的那个断言上**。

    这条测试同时钉住两个方向，缺任一个都验不出东西：

      正向——「采到了之后的注意事项清单」不得被标成未生成。
      `_openweb_notes` 与 `_load_announce` 走到这两条时 `docs` 都非空，
      说「未生成」是在说没采到，而紧接着的下一行写着「· N 篇抓取失败」，
      自相矛盾。
      反向——真缺口条目**仍然**要标未生成。否则把前缀登记得太宽
      （比如直接登记「开放网络资料：」）会把「一篇都没取到」也变成
      informational，那是反向的同一个错误。
    """
    from datetime import date

    from ir_agent import full, research
    from ir_agent.sources.external import Document, Tier
    from ir_agent.sources.openweb import OpenWebResult

    def rendered(entry: str) -> str:
        return f"  {research._mark(entry)}{entry}"

    doc = Document(doc_id="x", url="https://a.example/1", title="t",
                   publisher="p", tier=Tier.MEDIA, published=date(2026, 6, 1),
                   retrieved=date(2026, 9, 30), text="正文")
    # 真实生产者，不是手搓的字符串形状
    notes = full._openweb_notes(OpenWebResult(
        docs=[doc], fetch_failed=3, fetch_failed_reasons=["超时"],
        missing_slots=["公司重大动态"]))
    assert len(notes) == 1
    line = rendered(notes[0])
    assert "未生成" not in line, \
        f"采到 1 篇、失败 3 篇的注意事项清单被渲染成了未生成：{line!r}"
    assert "3 篇抓取失败" in line, "注意事项的内容在渲染后丢了"

    # 反向：真缺口仍须落「未生成」
    for gap in ("开放网络资料：一篇都没取到（3 篇抓取失败），定性判断的"
                "外部依据仍只有券商研报一类来源",
                "公司公告：一篇都没入库（候选 220 条，模型挑 0 条，"
                "取文失败 0 篇）"):
        assert "未生成" in rendered(gap), \
            f"真缺口条目不再标未生成了——前缀登记得太宽：{gap[:24]!r}"


def test_公告部分取文失败的清单条目不被渲染成未生成(monkeypatch, tmp_path):
    """与上一条同一件事，走 `_load_announce` 的真实返回值而不是手搓前缀
    ——前缀一旦在代码里改动而忘了同步 `_PREFIX`，这条会红。"""
    from datetime import date

    from ir_agent import full, research
    from ir_agent.sources import announce as A
    from ir_agent.sources.external import Document, Tier

    doc = Document(doc_id="a1", url="http://static.cninfo.com.cn/a.PDF",
                   title="投产公告", publisher="生益科技", tier=Tier.COMPANY,
                   published=date(2026, 8, 1), retrieved=date(2026, 9, 30),
                   text="正文")
    monkeypatch.setattr(full, "_company_name", lambda c: "某公司")
    monkeypatch.setattr(A, "gather", lambda *a, **k: A.AnnounceResult(
        docs=[doc], candidates=220, picked=3, fetch_failed=2,
        fetch_failed_reasons=["超时", "PDF 取文为空"]))

    class _R:
        corpus = None
        as_of = date(2026, 9, 30)

    entries = full._load_announce("600183", _R(), date(2026, 9, 30),
                                  tmp_path, object(), {}, ["名义产能"])
    caveat = [e for e in entries if "取文失败" in e]
    assert caveat, "部分取文失败没进清单"
    line = f"  {research._mark(caveat[0])}{caveat[0]}"
    assert "未生成" not in line, \
        f"公告采到了 1 篇、只是 2 篇取文失败，却被渲染成未生成：{line!r}"
