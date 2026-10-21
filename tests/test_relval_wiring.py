"""相对估值接入第二阶段：顺序、降级、进提示词、研报规则与辩论。"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from ir_agent import debate, full, relval_data
from ir_agent.analyst import _RULES, FactCatalog, build_prompt
from ir_agent.ledger import FactLedger

D = Decimal
AS_OF = date(2026, 10, 21)


def test_相对估值接在落账本之后写研报之前():
    src = inspect.getsource(full.run_valuation)
    assert (src.index("dcf.to_facts(") < src.index("_relative_valuation(")
            < src.index("_write_full_report("))
    assert "write_sheet(" in src and "relval.to_facts(" in src


def _r():
    return SimpleNamespace(peer_set=SimpleNamespace(
        classification=SimpleNamespace(used_path="电子-元件-印制电路板")))


def test_相对估值失败不拖垮研报(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("接口挂了")
    monkeypatch.setattr(relval_data, "gather", boom)
    res, skipped = full._relative_valuation("600183", _r(), AS_OF, None, tmp_path)
    assert res is None and skipped and skipped[0].startswith("相对估值")


def test_行业样本缺失时说明原因(monkeypatch, tmp_path):
    def unavailable(*a, **k):
        raise relval_data.RelvalUnavailable("行业样本未取到")
    monkeypatch.setattr(relval_data, "gather", unavailable)
    res, skipped = full._relative_valuation("600183", _r(), AS_OF, None, tmp_path)
    assert res is None and "行业样本未取到" in skipped[0]


def test_正常路径组装出结果并报出不可用项(monkeypatch, tmp_path):
    from tests.test_relval import _pb_sample, _peers_pe, _tgt
    tgt = _tgt(roe=D("0.2"), growth=None, net_profit=D("0.2"))
    monkeypatch.setattr(relval_data, "gather", lambda *a, **k: (
        tgt, _pb_sample(), _peers_pe(10, 20, 30, 40, 50, 60), D(1), D(100), ["样本说明"]))
    res, skipped = full._relative_valuation("600183", _r(), AS_OF, None, tmp_path)
    assert res is not None and set(res.regs) == {"pb_roe", "pe_growth"}
    assert res.regs["pb_roe"].usable
    assert res.sample_notes == ["样本说明"] and len(res.comps_peers) == 6
    # 本司没有增速 → PE 回归不可用，要报出来
    assert any("PE 对增速" in s for s in skipped)


def test_相对估值摘要进了估值结果(monkeypatch):
    from ir_agent import analyst
    captured = {}

    def fake_write_body(cat, client, **kw):
        captured.update(kw)
        return "正文", None
    monkeypatch.setattr(analyst, "write_body", fake_write_body)
    monkeypatch.setattr(analyst.FactCatalog, "from_ledger",
                        classmethod(lambda cls, *a, **k: SimpleNamespace(evidence=None)))
    monkeypatch.setattr(full, "_catalog_periods", lambda r: ())
    monkeypatch.setattr(full, "_build_evidence", lambda r, code: None)
    monkeypatch.setattr(full, "make_client", lambda model=None: SimpleNamespace(total_cost_usd=0.0))
    monkeypatch.setattr(full, "_debate_and_revise", lambda cat, body, client, period: (body, 0, "", ""))
    r = SimpleNamespace(ledger=FactLedger(), period="2025FY", as_of=AS_OF)
    dcf_res = SimpleNamespace(summary=lambda: "DCF_SUMMARY")
    full._write_full_report("600183", r, "简报", None, dcf_res, {}, caveat="CAVEAT",
                            relval_summary="SENTINEL_RELVAL")
    vs = captured["valuation_summary"]
    assert vs.index("DCF_SUMMARY") < vs.index("SENTINEL_RELVAL") < vs.index("CAVEAT")


def test_第八节要求多方法与综合区间():
    assert "多方法交叉验证" in _RULES and "综合区间" in _RULES
    assert "不等于绝对便宜" in _RULES and "不得写平均数" in _RULES
    assert "那是下一步估值环节的产物" not in _RULES


def test_估值结果块提到相对估值():
    cat = FactCatalog(ledger=FactLedger(), as_of=AS_OF)
    txt = build_prompt(cat, code="600183", period="2025FY",
                       valuation_summary="DCF 估值（ashare-dcf 三表模型）")
    after = txt.split("**估值结果**")[1]
    assert "相对估值" in after and "综合区间" in after


def test_质疑者会挑战综合区间():
    assert "综合估值区间" in debate._CHALLENGER
