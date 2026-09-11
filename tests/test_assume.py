"""AI 填 DCF 判断项。边界与三档拉开是这个模块的全部要害。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ir_agent import assume
from ir_agent.assume import AssumeFailed, Case
from ir_agent.ledger import FactLedger

D = Decimal
AS_OF = date(2026, 9, 7)
BODY = "行业营收同比高于本司，份额在丢，因此基准增速取低于行业。"
Q = "份额在丢，因此基准增速取低于行业"


def _line(item="营收增速", y=1, bear="2%", base="6%", bull="10%", q=Q):
    return f"假设: {item} | 第{y}年 | {bear} | {base} | {bull} | 「{q}」"


def _full(years=2):
    out = []
    for y in range(1, years + 1):
        out.append(_line("营收增速", y, f"{y}%", f"{y+4}%", f"{y+8}%"))
        out.append(_line("毛利率", y, "12%", "14%", "16%"))
    return "\n".join(out)


def _ok(a, years=2):
    assume.check(a, years, ledger=FactLedger(), body=BODY, as_of=AS_OF)


# ── 边界：只填判断项 ─────────────────────────────────────────

def test_只认两个判断项():
    """费用率、周转天数、折旧率由 calibrate 从历史算出 —— 那些是对历史的
    度量不是判断，AI 去动只会把已经算对的东西改坏。"""
    assert set(assume.DRIVERS) == {"营收增速", "毛利率"}


def test_填校准项被驳回且说明原因():
    with pytest.raises(AssumeFailed) as e:
        assume.parse(_line(item="销售费用率"), 2)
    assert "由历史数据校准" in str(e.value)


# ── 三档必须拉开 ─────────────────────────────────────────────

def test_三档同值被驳回():
    """模板默认 Bull=Base=Bear，那等于没有情景 —— 拉不开的情景分析
    只是把同一个数抄三遍。"""
    a = assume.parse("\n".join([
        _line("营收增速", 1, "5%", "5%", "5%"),
        _line("毛利率", 1, "12%", "14%", "16%")]), 1)
    with pytest.raises(AssumeFailed, match="三档未拉开"):
        _ok(a, 1)


def test_顺序颠倒被驳回():
    a = assume.parse("\n".join([
        _line("营收增速", 1, "10%", "6%", "2%"),
        _line("毛利率", 1, "12%", "14%", "16%")]), 1)
    with pytest.raises(AssumeFailed, match="三档未拉开|严格递增"):
        _ok(a, 1)


def test_拉开则通过():
    _ok(assume.parse(_full(2), 2), 2)


# ── 每一年都要给 ─────────────────────────────────────────────

def test_漏掉某年被驳回():
    """漏掉的年份会沿用模板默认值，而那不是你的判断。"""
    a = assume.parse("\n".join([_line("营收增速", 1),
                                _line("毛利率", 1, "12%", "14%", "16%")]), 3)
    with pytest.raises(AssumeFailed, match="缺第"):
        _ok(a, 3)


def test_年份越界被驳回():
    with pytest.raises(AssumeFailed, match="超出预测期"):
        assume.parse(_line(y=9), 5)


# ── 依据 ─────────────────────────────────────────────────────

def test_缺依据被驳回():
    with pytest.raises(AssumeFailed, match="缺少可核验的依据"):
        assume.parse("假设: 营收增速 | 第1年 | 2% | 6% | 10% | 我的判断", 1)


def test_依据查无此句被驳回():
    a = assume.parse("\n".join([
        _line("营收增速", 1, q="行业景气全面回升"),
        _line("毛利率", 1, "12%", "14%", "16%")]), 1)
    with pytest.raises(AssumeFailed, match="不可核验"):
        _ok(a, 1)


# ── 解析容错（与四个定性模块同一套） ─────────────────────────

def test_容忍列表前缀与括号补注():
    a = assume.parse("1. " + _line(item="营收增速（判断项）"), 1)
    assert a.rows[0].driver == "growth"


def test_百分号与小数都认():
    a = assume.parse(_line(bear="0.02", base="0.06", bull="0.10"), 1)
    assert a.rows[0].bear == D("0.02")


# ── 写回 calibrate ───────────────────────────────────────────

def test_三档分别写回():
    a = assume.parse(_full(2), 2)
    a.labels = ["2026E", "2027E"]
    cal = {"judg": {"growth": [0.0, 0.0], "gm": [0.0, 0.0], "cagr3": 0.1}}
    out = assume.apply_to(cal, a)
    assert out["judg"]["growth_scen"]["bear"] == [0.01, 0.02]
    assert out["judg"]["growth_scen"]["bull"] == [0.09, 0.10]
    assert out["judg"]["growth"] == [0.05, 0.06]      # 选定档=中性
    assert cal["judg"]["growth"] == [0.0, 0.0], "不应就地改坏原 cal"


def test_列顺序对应BullBaseBear():
    """B=Bull C=Base D=Bear 与 CHOOSE($B$SCEN,...) 绑定。顺序写反会让
    「选悲观」选出乐观值，**而模型照样验算通过** —— 公式本身没错。"""
    a = assume.parse(_full(1), 1)
    assert a.path("growth", Case.BEAR)[0] < a.path("growth", Case.BASE)[0] \
        < a.path("growth", Case.BULL)[0]


# ── 待核实清单 ───────────────────────────────────────────────

def test_清单给人看得懂的表():
    a = assume.parse(_full(2), 2)
    a.code, a.labels = "600584", ["2026E", "2027E"]
    txt = a.describe()
    assert "需人工核实后方可建模" in txt
    assert "2026E" in txt and "悲观" in txt and "依据" in txt


def test_提示词点明边界与拉开():
    txt = assume.build_prompt({"g_stats": (0.1, 0.05, 0.0, 0.2), "cagr3": 0.12,
                               "gm_stats": (0.15, 0.02, 0.12, 0.18),
                               "gm_hist": [0.14]}, ["2026E"], "上下文")
    assert "你只填这两项" in txt and "严格拉开" in txt
    assert "不是理由" in txt          # 不许复述历史当依据


def test_连续失败则拒绝():
    class _C:
        def __init__(self): self.n = 0
        def __call__(self, p):
            self.n += 1
            return "假设: 营收增速 | 第1年 | 5% | 5% | 5% | 「x」"
    with pytest.raises(AssumeFailed, match="连续"):
        assume.decide(_C(), {"g_stats": (), "gm_stats": ()}, ["2026E"], "",
                      ledger=FactLedger(), body=BODY, as_of=AS_OF)
