"""判断项的审核者（spec 2026-10-15 子项目三）。不调模型。"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent import assume
from ir_agent import assume_review as AR
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.methodology import parse as parse_meth
from ir_agent.stance import Balance
from judge_stub import Judge, items

AS_OF = date(2026, 10, 15)
BODY = "行业需求结构性向上，份额温和扩张。"
JUDG = {"g_stats": (), "gm_stats": ()}
LABELS = ["2026E", "2027E"]

MODEL = parse_meth("""模式: 变化
因子: 行业 | 行业需求增速 | 变化率 | 研报 | 行业规模
因子: 份额 | 市占率变化 | 变化率 | 研报 | 市占率
因子: 提价 | 提价传导 | pp | 调价函 | 调价
因子: 原料 | 原材料成本冲击 | pp | 年报 | 原材料价格
常量: 基期市占率 | 0.08 | 年报
公式: 营收 = 上期(营收) × (1 + 行业) × (1 + 份额 / 基期市占率)
公式: 毛利率 = 基期(毛利率) + (提价 + 原料) / 100
""").model


def _ledger() -> FactLedger:
    led = FactLedger()
    for key, v in (("revenue", "20000000000"), ("gross_margin", "0.2647")):
        led.put(Fact(key=key, value=D(v), unit="x", period="2025FY",
                     as_of=date(2026, 4, 1), source_id="t", method=Method.REPORTED))
    return led


def _draft(gm_bull="5.47", year2=True) -> str:
    rows = []
    for y in (1, 2) if year2 else (1,):
        rows += [f"假设: 行业 | 第{y}年 | 10% | 20% | 30% | 平衡点 | 无数据（估计），参照行业",
                 f"假设: 份额 | 第{y}年 | 0.2% | 0.6% | 1.2% | 温和扩张 | 无数据（估计），参照份额",
                 f"假设: 提价 | 第{y}年 | 1 | 3.5 | {gm_bull} | 上半年提升 5.47 个百分点 | 无数据（估计），研报",
                 f"假设: 原料 | 第{y}年 | -8 | -4 | -1 | 铜箔高位 | 无数据（估计），年报"]
    return "\n".join(rows)


class _Filler:
    def __init__(self, *outs):
        self.outs, self.prompts = list(outs), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.outs[min(len(self.prompts), len(self.outs)) - 1]


def _run(filler, judge, **kw):
    return AR.fill_and_review(filler, judge, judg=JUDG, labels=LABELS, context="上下文",
                              ledger=_ledger(), body=BODY, as_of=AS_OF, code="600183",
                              decomps=kw.pop("decomps", MODEL),
                              base_margin=D("0.2647"), **kw)


class TestChecklist:
    def test_五类_有平衡点的因子才列平衡点(self):
        a = assume.parse(_draft(), 2, decomps=MODEL)
        got = AR.checklist(a, [Balance("提价传导", D("1"), D("3.5"), D("6"), "x")])
        kinds = [k for k, _ in got]
        assert kinds.count("取值") == 4 and kinds.count("依据") == 4
        assert [s for k, s in got if k == "平衡点"] == ["提价（提价传导）"]
        assert [s for k, s in got if k == "合成"] == ["营收", "毛利率"]
        assert [s for k, s in got if k == "后果"] == ["营收路径"]

    def test_两标量模式只有依据合成后果(self):
        a = assume.AssumptionSet(code="", labels=LABELS)
        assert {k for k, _ in AR.checklist(a, [])} == {"依据", "合成", "后果"}


class TestParse:
    EXP = [("取值", "提价（提价传导）"), ("后果", "营收路径")]

    def test_补充选填_不会并进上一条(self):
        raw = ("[取值] 提价（提价传导）：通过\n"
               "补充：提醒　应付天数 17 分位，与占款叙事相反　建议：删掉占款那句\n"
               "[后果] 营收路径：通过")
        got = AR.parse(raw, self.EXP)
        assert got[0].why == "" and got[-1].kind == "补充"
        assert got[-1].suggest == "删掉占款那句"
        assert len(AR.parse("[取值] 提价（提价传导）：通过\n[后果] 营收路径：通过",
                            self.EXP)) == 2

    def test_补充判致命也要附建议(self):
        with pytest.raises(AR.ReviewIncomplete, match="补充"):
            AR.parse("[取值] 提价（提价传导）：通过\n[后果] 营收路径：通过\n"
                     "补充：致命　产能撑不住", self.EXP)


class TestFacts:
    def test_原文与读数并排_单位错一眼可见(self):
        """生益科技 2026-10-15：理由栏「5.47 个百分点」、表里 0.0547。"""
        a = assume.parse(_draft(gm_bull="0.0547"), 2, decomps=MODEL)
        assume.check(a, 2, ledger=_ledger(), body=BODY, as_of=AS_OF,
                     decomps=MODEL, base_margin=D("0.2647"))
        txt = AR.facts_for_judge(a, balances=[Balance("提价传导", D("0.02"), D("0.035"),
                                                      D("0.05"), "辩论②")])
        assert "提价传导 第1年：原文 1 / 3.5 / 0.0547 → 读作 +1.00pp / +3.50pp / +0.05pp" in txt
        assert "提价传导：悲观 0.02 / 平衡 0.035 / 乐观 0.05" in txt
        assert "【代码按公式合成的三档】" in txt


class TestRounds:
    def test_第一轮通过(self):
        a, res, rounds = _run(_Filler(_draft()), Judge())
        assert res.rounds == 1 and len(rounds) == 1 and a.judge_review is res
        assert a._m() is MODEL or a.model is MODEL

    def test_致命即退回_建议交给填写者_第二轮通过(self):
        f = _Filler(_draft(gm_bull="0.0547"), _draft())
        j = Judge({("取值", "提价（提价传导）"): "致命"}, {})
        a, res, rounds = _run(f, j)
        assert res.rounds == 2 and rounds[0].review.fatal()
        assert "建议：改取值提价（提价传导）" in f.prompts[1]
        key = next(f.key for f in MODEL.factors if f.short == "提价")
        assert next(r.bull for r in a.rows if r.factor_key == key and r.year == 1) \
            == D("5.47"), "采用的是重填后的第二稿"

    def test_三轮仍有致命停下_带着最后一稿(self):
        with pytest.raises(AR.ReviewStop, match="3 轮未通过.*致命 1 条") as e:
            _run(_Filler(_draft()), Judge({("后果", "营收路径"): "致命"}))
        assert len(e.value.rounds) == 3 and e.value.last is not None

    def test_审核者答不全就停下_不当通过(self):
        with pytest.raises(AR.ReviewStop, match="审核未完成"):
            _run(_Filler(_draft()), Judge(skip={("后果", "营收路径")}))

    def test_补答不消耗填写者轮数(self):
        class Once(Judge):
            def __call__(self, prompt):
                self.skip = {("后果", "营收路径")} if not self.prompts else set()
                return super().__call__(prompt)
        f, j = _Filler(_draft()), Once()
        _, res, rounds = _run(f, j)
        assert len(j.prompts) == 2 and len(f.prompts) == 1 and res.rounds == 1

    def test_审核通过但算不下去就停下_写明哪里(self):
        """缺第 2 年：代码不驳回，审核者却放行了 —— 停下，如实说明算不下去。"""
        with pytest.raises(AR.ReviewStop, match="审核已通过，但无法计算.*缺第 \\[2\\] 年"):
            _run(_Filler(_draft(year2=False)), Judge())

    def test_不退回两标量模式(self):
        """因子名全写错：代码一行没读进来，交给审核者；绝不悄悄换成两标量。"""
        bad = _draft().replace("假设: 行业", "假设: 不存在")
        j = Judge({("取值", "行业（行业需求增速）"): "致命"}, {})
        a, res, _ = _run(_Filler(bad, _draft()), j)
        assert a.model is MODEL and res.rounds == 2
        assert "未知的因子 '不存在'" in j.prompts[0]

    def test_审核者看到的比填写者多(self):
        j = Judge()
        _run(_Filler(_draft()), j, catalog="  营收  100亿  写作 [[revenue@2025FY]]",
             method_review=["- [因子] 提价：提醒　x"])
        p = j.prompts[0]
        assert "只给你的、代码算出的事实" in p and "原文 10% / 20% / 30%" in p
        assert "账本事实目录" in p and "[[revenue@2025FY]]" in p
        assert "【方法论审核遗留】" in p and "填写者看到的资料" in p

    def test_各轮记录(self):
        with pytest.raises(AR.ReviewStop) as e:
            _run(_Filler(_draft()), Judge({"后果": "致命"}))
        md = AR.rounds_markdown(e.value.rounds)
        assert md.count("## 第 ") == 3 and "### 填写者原文" in md
        assert "### 代码事实" in md and "审核者原文（第 1 次作答）" in md
        assert "[后果] 营收路径：致命" in md


class TestFingerprint:
    def test_改数会变_改理由不变_落盘读回不变(self):
        a = assume.parse(_draft(), 2, decomps=MODEL)
        fp = AR.fingerprint(a)
        back, _ = assume.from_json(assume.to_json(a))
        assert AR.fingerprint(back) == fp
        a.rows[0].note = "改了理由"
        assert AR.fingerprint(a) == fp
        a.rows[0].base = D("0.21")
        assert AR.fingerprint(a) != fp


def test_清单与提示词里的条目一致():
    j = Judge()
    a, _, _ = _run(_Filler(_draft()), j)
    assert items(j.prompts[0]) == AR.checklist(a, [])


def test_规则正文不出现与清单同形的条目行():
    import re
    assert not re.search(r"(?m)^\s*-\s*\[(取值|平衡点|依据|合成|后果)\]", AR.RULES)


class TestPhase2:
    """第二阶段读回人改过的判断项：不送审，只标注（spec 三、用户决定 5）。"""

    def _saved(self, tmp_path, *, passed=True):
        from ir_agent import full
        from ir_agent.methodology_review import ReviewResult
        a = assume.parse(_draft(), 2, decomps=MODEL)
        res = ReviewResult(rounds=1, fingerprint=AR.fingerprint(a))
        (tmp_path / full.ASSUMPTIONS_REVIEW_JSON).write_text(
            AR.dump_review(res, passed=passed), encoding="utf-8")
        return assume.to_json(a)

    def test_指纹一致_不标注(self, tmp_path):
        from ir_agent import full
        a, edits = assume.from_json(self._saved(tmp_path))
        assert full._unreviewed_assumptions(tmp_path, a, edits) == ""

    def test_人改了数_标注并说改了几格(self, tmp_path):
        from ir_agent import full
        d = self._saved(tmp_path)
        d["rows"][0]["base"] = "0.21"
        a, edits = assume.from_json(d)
        got = full._unreviewed_assumptions(tmp_path, a, edits)
        assert "人工修改，修改后未经审核" in got and "1 格" in got

    def test_没有审核记录或审核未通过_都标注(self, tmp_path):
        from ir_agent import full
        a, edits = assume.from_json(self._saved(tmp_path, passed=False))
        assert "审核未通过" in full._unreviewed_assumptions(tmp_path, a, edits)
        (tmp_path / full.ASSUMPTIONS_REVIEW_JSON).unlink()
        assert "没有判断项审核记录" in full._unreviewed_assumptions(tmp_path, a, edits)

    def test_研报附录写明未经审核_提示词那句不带数字(self, tmp_path):
        import re

        from ir_agent import full
        a, _ = assume.from_json(self._saved(tmp_path))
        line, md = full._assumption_caveats(a, unreviewed="判断项经人工修改，修改后未经审核（改动 3 格）")
        assert "未经" in line and not re.search(r"\d", line)
        assert "改动 3 格" in md

    def test_第二阶段比对指纹且不再按内容拒绝(self):
        import inspect

        from ir_agent import full
        src = inspect.getsource(full.run_valuation)
        assert "_unreviewed_assumptions(" in src
        assert src.index("_unreviewed_assumptions(") < src.index("dcf.run(")
        assert "_stop_if_uncomputable(facts)" in src
        assert src.index("_stop_if_uncomputable(facts)") < src.index("dcf.run(")
        assert 'raise SystemExit(f"批准的假设不合法' not in src

    def test_只在算不下去时停下_内容问题不拦(self):
        from ir_agent import full
        full._stop_if_uncomputable(assume.Facts(problems=["第 1 年三档未拉开"]))
        with pytest.raises(SystemExit, match="(?s)算不下去.*缺第"):
            full._stop_if_uncomputable(assume.Facts(blocking=["因子「提价」缺第 [2] 年"]))
        assert "重新校验" not in full._APPROVE_NOTE


class TestShengyiReplay:
    """生益科技 2026-10-15 第三次调用：三次被代码闸门拒绝、整个因子分解退回两标量。
    三种情形现在都不再被代码拒绝，原拒绝理由出现在审核者手里的事实里。"""

    BAL = [Balance("提价传导", D("0.02"), D("0.035"), D("0.05"), "辩论② 平衡点（单位写成了小数）")]

    def _draft(self, gm1="1 | 3.5 | 4", raw1="-8 | -4 | -1") -> str:
        return _draft().replace("假设: 提价 | 第1年 | 1 | 3.5 | 5.47",
                                f"假设: 提价 | 第1年 | {gm1}").replace(
            "假设: 原料 | 第1年 | -8 | -4 | -1", f"假设: 原料 | 第1年 | {raw1}")

    def test_第一次_偏离一个单位写错的平衡点(self):
        j = Judge()
        a, res, _ = _run(_Filler(self._draft()), j, balances=self.BAL)
        assert res.rounds == 1
        assert "偏离多空平衡点 0.035" in j.prompts[0]
        assert ("平衡点", "提价（提价传导）") in items(j.prompts[0])

    def test_第二次_两个pp因子各自拉开_加总后倒序(self):
        j = Judge()
        _run(_Filler(self._draft(raw1="-8 | -4 | -6")), j)
        assert "第 1 年的毛利率合成后不单调" in j.prompts[0]

    def test_第三次_七段的行依据在第七段(self):
        seven = self._draft().replace(
            "假设: 行业 | 第1年 | 10% | 20% | 30% | 平衡点 | 无数据（估计），参照行业",
            "假设: 行业 | 第1年 | 10% | 20% | 30% | 中性档采纳辩论平衡点20% | 无数据（估计），参照行业")
        j = Judge()
        a, _, _ = _run(_Filler(seven), j)
        assert "缺少可核验的依据" not in j.prompts[0]


# ══ 生益科技 2026-10-17 试跑后：审核者判得对、填写者却改不了的两类致命 ═══════

ANCHOR = {"label": "半年报", "k": 2, "fy_rev": 426.596379280956,
          "fy_cogs": 293.2044922458345, "base_rev": 284.3113845944,
          "g_rev": 0.50045479145881, "g_cogs": 0.40258038341900093}


class TestAnchorFacts:
    """A：审核者不知道半年报锚定，三轮都把「第 2 年起毛利率比手算高约 4.22pp」判成
    致命、还在补充里建议技术侧排查合成引擎 —— 它手算得没错，缺的是这条事实。"""

    def test_事实里写明首年锚定值与第二年起的基期平移(self):
        j = Judge()
        _run(_Filler(_draft()), j, anchor=ANCHOR)
        p = j.prompts[0].split("**只给你的、代码算出的事实**")[1].split("**账本事实目录**")[0]
        assert "【首年锚定】" in p
        assert "三档相同" in p and "营收增速 50.05%" in p and "毛利率 31.27%" in p
        assert "Q1–Q2" in p and "+50.05%" in p and "+40.26%" in p, "规则与两个同比要写给审核者"
        assert re.search(r"第 2 年起.*基期平移.*中性 \+\d+\.\d+pp", p, re.S)

    def test_没有锚定就不写(self):
        j = Judge()
        _run(_Filler(_draft()), j)
        facts = j.prompts[0].split("**只给你的、代码算出的事实**")[1].split("**账本事实目录**")[0]
        assert "【首年锚定】" not in facts

    def test_规则让审核者手算时加上平移量(self):
        rule = [l for l in AR.RULES.splitlines() if l.startswith("- 合成：")][0]
        block = rule + AR.RULES.split(rule)[1].split("\n- 后果：")[0]
        assert "首年锚定" in block and "不要当成引擎" in block


class TestBalanceIsFixedInput:
    """B：平衡点是辩论② 的固定输入（门② 已给人审过），填写者改不了。生益科技第 3
    轮：填写者照审核建议写了「假设: 提价传导 | 平衡点 | 2.0 | 3.5 | 5.47」，代码读
    不进来，审核者又以「没被系统读取」判致命。"""

    def test_审核规则_平衡点自身的错不判致命_不建议重写(self):
        rule = [l for l in AR.RULES.splitlines() if l.startswith("- 平衡点：")][0]
        block = rule + AR.RULES.split(rule)[1].split("\n- 依据：")[0]
        assert "固定输入" in block and "改不了" in block
        assert "不要因为平衡点自身的错误判致命" in block
        assert "不要建议填写者重写平衡点" in block

    def test_填写者提示词_平衡点改不了_按换算后的值对齐(self):
        txt = assume.build_prompt(JUDG, LABELS, "上下文", decomps=MODEL, factor_data={})
        assert "平衡点由辩论② 给出，你改不了" in txt
        assert "不要另写平衡点行" in txt


def test_作答解析与各轮记录可换类别与称呼():
    """子项目四（三个定性模块）复用判断项审核的作答解析与各轮记录。"""
    got = AR.parse("[五力] 现有竞争强度：通过\n补充：提醒　x　建议：y",
                   [("五力", "现有竞争强度")], kinds=("护城河", "五力"))
    assert [v.kind for v in got] == ["五力", "补充"]
    md = AR.rounds_markdown([AR.Round(1, "原文")], title="战略分析审核", author="撰写者")
    assert md.startswith("# 战略分析审核：各轮记录") and "### 撰写者原文" in md
