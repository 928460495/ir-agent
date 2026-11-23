"""未核实常量（用户 2026-11-23 选折中方案）：不阻断，门② 单列、门③ 附 ±20% 敏感度。

茅台实跑：方法论预研的四个常量出处写「估计值；定稿前按年报重算」，它们经公式进入第 2 年起的
营收增速与毛利率。判致命会诱导模型编出处；这里只让人看见「哪几个没核实、各自影响多大」。
"""
from __future__ import annotations

from decimal import Decimal as D

from ir_agent import assume, formula
from ir_agent.methodology import parse as parse_meth

from tests.test_assume_formula import Q, _check

CONST = parse_meth("""模式: 变化
因子: 量增 | 出货量同比增速 | 变化率 | 年报产销量 | 产销量
因子: 价增 | 平均单价同比变化率 | 变化率 | 研报价格跟踪 | 平均单价
常量: 材料率 | 0.55 | 估计值；定稿前按上年年报重算
常量: 固定率 | 0.1 | 上年年报营业成本构成：折旧 ÷ 营业收入
公式: 营收 = 上期(营收) × (1 + 量增) × (1 + 价增)
公式: 毛利率 = 上期(毛利率) - 材料率 × 价增 + 固定率 × 量增 / (1 + 量增)
""").model


def _lines():
    return "\n".join([f"假设: 量增 | 第1年 | 2% | 5% | 8% | 扩产 {Q}",
                      f"假设: 价增 | 第1年 | -1% | 2% | 3% | 价格 {Q}"])


def test_识别出处写估计的常量():
    assert [c.name for c in formula.unverified_constants(CONST)] == ["材料率"]


def test_门二清单单列未核实常量():
    assert formula.unverified_lines(CONST) == [
        "- ⚠ 未核实常量：材料率 = 0.55（估计值；定稿前按上年年报重算）—— 门③ 判断项清单附 ±20% 敏感度"]
    assert formula.unverified_lines(None) == []


def test_没有估计常量时不出这一节():
    m = parse_meth("""模式: 变化
因子: 量增 | 出货量同比增速 | 变化率 | 年报产销量 | 产销量
因子: 价增 | 平均单价同比变化率 | 变化率 | 研报价格跟踪 | 平均单价
常量: 固定率 | 0.1 | 上年年报营业成本构成
公式: 营收 = 上期(营收) × (1 + 量增) × (1 + 价增)
公式: 毛利率 = 上期(毛利率) + 固定率 × 量增 / (1 + 量增) - 价增 / 100
""").model
    assert formula.unverified_lines(m) == []


def test_敏感度按中性档逐年最大变动():
    a = assume.parse(_lines(), 1, decomps=CONST)
    _check(a, 1, CONST)
    [(c, dg, dm)] = a.constant_sensitivity()
    # 毛利率里 材料率 × 价增：0.55 × 20% × 2% = 0.22pp；营收公式不含材料率
    assert c.name == "材料率" and dg == 0 and abs(dm - D("0.0022")) < D("1e-9")


def test_门三清单印敏感度表():
    a = assume.parse(_lines(), 1, decomps=CONST)
    _check(a, 1, CONST)
    a.labels = ["2026E"]
    txt = a.describe()
    assert "🔎 **未核实常量**" in txt
    assert "| 材料率 | 0.55 | 估计值；定稿前按上年年报重算 | ±0.00pp | ±0.22pp |" in txt
    assert txt.index("未核实常量") < txt.index("## 分解式")


def test_门二待核清单把未核实常量放在审核遗留之前(monkeypatch, tmp_path):
    from ir_agent import full
    monkeypatch.setattr(full, "_method_review_lines", lambda out, m: ["- [公式] 营收：提醒"])
    assert full._gate2_method_lines(tmp_path, CONST) == [
        formula.unverified_lines(CONST)[0], "- [公式] 营收：提醒"]
