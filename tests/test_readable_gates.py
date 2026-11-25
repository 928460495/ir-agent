"""四道门的阅读版（spec 2026-11-25-readable-layout §4.5）。"""

from __future__ import annotations

import inspect
import json
from datetime import date
from decimal import Decimal

import pytest

from ir_agent import qual_review as Q
from ir_agent import readable
from ir_agent.ledger import Fact, FactLedger, Method

AS_OF = date(2026, 11, 20)
LONG = "这是一条很长的结论，" * 30 + "结论到此为止。"
STANCE = {"round": "A", "manual": "", "dropped": ["格式不对的一行"],
          "evidence": [{"side": "多方", "subject": "需求", "claim": "需求回升", "bases": ["[[industry.cr5@2025FY]]"],
                        "quotes": [""], "unverified": "", "source": ""},
                       {"side": "空方", "subject": "需求", "claim": "需求走弱", "bases": [""], "quotes": ["原文一句"],
                        "unverified": "引文在年报与语料库里都找不到", "source": "某报《题》（2026-01-01）"}],
          "balances": [{"subject": "需求", "bear": None, "mid": None, "bull": None, "note": LONG},
                       {"subject": "销量", "bear": "0.01", "mid": "0.02", "bull": "0.03", "note": "取实绩"}]}


def _ledger():
    led = FactLedger()
    led.put(Fact(key="industry.cr5", value=Decimal("0.8172"), unit="ratio", period="2025FY",
                 as_of=date(2026, 4, 30), source_id="calc_cr5", method=Method.COMPUTED))
    return led


def _write(d, name, text):
    (d / name).write_text(text, encoding="utf-8")


@pytest.fixture
def out(tmp_path):
    _write(tmp_path, "brief.md", "> 资料来源经 某人 核验\n\n## 投资要点\n\n集中度 81.72%。\n- 甲\n- 乙")
    _write(tmp_path, "drivers.md", "# 驱动因子分解（行业）\n\n- 需求：✓ 由「[[industry.cr5@2025FY]]」承载\n\n## 分解式原始定义\n\n```text\n模式: 变化\n```")
    _write(tmp_path, "review.md", "# 待核清单\n\n- [因子] 出货：待采集　没数据　建议：去采")
    _write(tmp_path, "assumptions.md", "DCF 判断项（600519）\n\n### 增速\n\n| 年 | 悲观 | 中性 | 乐观 | 理由 | 依据 |\n| --- | --- | --- | --- | --- | --- |\n"
           "| 2026E | 1% | 2% | 3% | 同上 | 外部 https://a.b/c「很长的一句原文依据」 |\n| 2027E | 2% | 3% | 4% | 同上 | 外部 https://a.b/c「很长的一句原文依据」 |")
    _write(tmp_path, "report.md", "## 投资要点\n\n**要点**：毛利率 91.18%。\n\n---\n\n## 数据来源\n\n| 数值 | 字段@期间 | 来源 | 披露日 | 取得方式 |\n| --- | --- | --- | --- | --- |\n"
           "| 81.72% | industry.cr5@2025FY | calc_cr5 | 2026-04-30 | computed |")
    _write(tmp_path, "debate_report.md", "多空辩论：2 项挑战，1 项成立（1 轮）\n  ✗ 成立　[逻辑错误] 被质疑的原文\n        （裁决理由）\n  · 不成立　[无据断言] 另一处")
    _write(tmp_path, "stance_a.json", json.dumps(STANCE, ensure_ascii=False))
    _write(tmp_path, "stance_b.json", json.dumps({**STANCE, "round": "B"}, ensure_ascii=False))
    _write(tmp_path, "strategy.md", "护城河: 转换成本 | 宽 | 甲")
    return tmp_path


def _view(out, stem):
    return (out / f"{stem}_阅读版.md").read_text(encoding="utf-8")


def test_各门阅读版齐全(out):
    names = {p.name for p in readable.write_readables(out, _ledger(), AS_OF)}
    assert names == {f"{n}_阅读版.md" for n in ("strategy", "brief", "drivers", "review", "stance_a", "stance_b",
                                             "assumptions", "report", "debate_report")}


def test_每份都有标题且写明改哪个源文件(out):
    for p in readable.write_readables(out, _ledger(), AS_OF, title="贵州茅台 600519"):
        t = p.read_text(encoding="utf-8")
        stem = p.name[:-len(readable.SUFFIX)]
        src = f"{stem}.json" if stem.startswith("stance") else f"{stem}.md"
        assert t.startswith("# 贵州茅台 600519 · ") and "只读阅读版" in t and f"`{src}`" in t, stem


def test_多空辩论全文不截断(out):
    t = readable.stance_markdown(STANCE)
    assert LONG in t and "空 0.01 / 平 0.02 / 多 0.03" in t
    for x in ("多方", "空方", "需求走弱", "原文一句", "某报《题》（2026-01-01）", "核验不过", "格式不对的一行", "[[industry.cr5@2025FY]]"):
        assert x in t


def test_人工补写的辩论写明():
    assert "人工补写" in readable.stance_markdown({**STANCE, "manual": "某人"})


def test_成稿辩论分条(out):
    readable.write_readables(out, _ledger(), AS_OF)
    t = _view(out, "debate_report")
    assert "### " in t and "被质疑的原文" in t and "裁决理由" in t and "另一处" in t


def test_判断项整列相同的依据合并到表下_数值不动(out):
    readable.write_readables(out, _ledger(), AS_OF)
    t = _view(out, "assumptions")
    assert t.count("很长的一句原文依据") == 1
    assert "| 2026E | 1% | 2% | 3% |" in t and "| 2027E | 2% | 3% | 4% |" in t


def test_判断项依据各年不同时不合并(out):
    s = (out / "assumptions.md").read_text(encoding="utf-8").replace("原文依据」 |\n| 2027E", "原文依据甲」 |\n| 2027E")
    _write(out, "assumptions.md", s)
    readable.write_readables(out, _ledger(), AS_OF)
    assert "| 依据 |" in _view(out, "assumptions")


def test_研报数据来源表换中文名并保留内部键(out):
    readable.write_readables(out, _ledger(), AS_OF)
    t = _view(out, "report")
    assert "行业 CR5（2025FY）" in t and "账本计算" in t and "industry.cr5@2025FY" in t and "calc_cr5" in t


def test_占位符换成数值(out):
    readable.write_readables(out, _ledger(), AS_OF)
    assert "81.72%[^1]" in _view(out, "drivers") and "[[" not in _view(out, "drivers")


def test_分解式原始定义整节保留(out):
    readable.write_readables(out, _ledger(), AS_OF)
    assert "模式: 变化" in _view(out, "drivers")


def test_单份失败不影响其余(out, monkeypatch):
    monkeypatch.setattr(readable, "stance_markdown", lambda d: 1 / 0)
    notes: list[str] = []
    names = {p.name for p in readable.write_readables(out, _ledger(), AS_OF, notes=notes)}
    assert "brief_阅读版.md" in names and "stance_a_阅读版.md" not in names
    assert any("stance_a" in n for n in notes)


def test_坏的JSON记说明(out):
    _write(out, "stance_a.json", "{坏")
    notes: list[str] = []
    readable.write_readables(out, _ledger(), AS_OF, notes=notes)
    assert any("stance_a" in n for n in notes)


def test_源文件字节不变(out):
    before = {p: p.read_bytes() for p in out.iterdir()}
    readable.write_readables(out, _ledger(), AS_OF)
    assert all(p.read_bytes() == b for p, b in before.items())


def test_空源文件写本轮未生成(out):
    _write(out, "brief.md", "\n")
    readable.write_readables(out, _ledger(), AS_OF)
    assert "本轮未生成" in _view(out, "brief")


def test_源文件没了删掉旧阅读版(out):
    readable.write_readables(out, _ledger(), AS_OF)
    (out / "brief.md").unlink()
    (out / "stance_a.json").unlink()
    readable.write_readables(out, _ledger(), AS_OF)
    assert not (out / "brief_阅读版.md").exists() and not (out / "stance_a_阅读版.md").exists()


def _review(out, raw, **kw):
    (out / "strategy_review.json").write_text(json.dumps({
        "fingerprint": Q.fingerprint(raw), "rounds": 1, "passed": True, "corpus": "",
        "verdicts": [{"kind": "护城河", "subject": "第1条 转换成本", "level": "提醒", "why": "证据偏薄", "suggest": "补证"}], **kw},
        ensure_ascii=False), encoding="utf-8")


def test_审核意见进阅读版(out):
    _review(out, (out / "strategy.md").read_text(encoding="utf-8"))
    readable.write_readables(out, _ledger(), AS_OF)
    assert "证据偏薄" in _view(out, "strategy")


def test_人工改过的稿不挂旧审核意见(out):
    _review(out, "改之前的原文")
    readable.write_readables(out, _ledger(), AS_OF)
    t = _view(out, "strategy")
    assert "证据偏薄" not in t and "人工修改" in t


def test_没有审核记录时写明(out):
    readable.write_readables(out, _ledger(), AS_OF)
    assert "没有审核记录" in _view(out, "strategy")


def test_判断项阶段也写阅读版():
    from ir_agent import full
    assert "_readable_views(" in inspect.getsource(full.run_assume)
    assert "_readable_views(" in inspect.getsource(full.run_valuation)


def test_宏观跟踪读数进阅读版(out):
    """跟踪指标的最新读数不在 macro_view.md 里，只有 macro_track.json —— 阅读版要带上，否则门①看不到。"""
    _write(out, "macro_view.md", "政策取向: 收紧 | 甲\n跟踪: 实际利率 | 加息 | 回落则不成立")
    (out / "macro_track.json").write_text(json.dumps({"items": [
        {"indicator": "实际利率", "driver": "加息", "falsifier": "回落则不成立", "conclusion": "11 月为 2.79%",
         "sources": ["https://a.b/c"], "published": {"https://a.b/c": "2026-11-23"}},
        {"indicator": "另一个", "driver": "d", "falsifier": "f", "conclusion": "", "sources": [], "published": {}}],
        "truncated": [], "failed": [], "removed": [], "docs": []}, ensure_ascii=False), encoding="utf-8")
    readable.write_readables(out, _ledger(), AS_OF)
    t = _view(out, "macro_view")
    for x in ("跟踪指标最新读数", "11 月为 2.79%", "https://a.b/c", "2026-11-23", "未取到"):
        assert x in t


def test_跟踪文件坏了不影响宏观阅读版(out):
    _write(out, "macro_view.md", "政策取向: 收紧 | 甲")
    _write(out, "macro_track.json", "{坏")
    readable.write_readables(out, _ledger(), AS_OF)
    assert "政策取向" in _view(out, "macro_view")
