"""非经营性金融资产 —— ir-agent 接线、门①现金提示、营业利润率口径（spec 2026-11-20，Task 5）。不联网。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

from ir_agent import dcf, full
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.operators.quality import cash_gap_warning

AS = date(2026, 11, 20)


def _led(**vals):
    led = FactLedger()
    for k, v in vals.items():
        led.put(Fact(key=k, value=D(str(v)), unit="元", currency="CNY", period="2025FY",
                     as_of=date(2026, 4, 17), source_id="em", method=Method.REPORTED))
    return led


def test_门一现金提示_能被拆出资金等解释时说已加回():
    led = _led(cash_and_equivalents=51691e6, cash_end=126426e6, lend_fund=99096e6, buy_resale_finasset=8880e6)
    msg = cash_gap_warning(led, "2025FY", AS)
    assert msg and "拆出资金" in msg and "已" in msg and "加回" in msg and "偏保守" not in msg


def test_门一现金提示_解释不了时请人工核对_不说偏保守():
    led = _led(cash_and_equivalents=51691e6, cash_end=126426e6)
    msg = cash_gap_warning(led, "2025FY", AS)
    assert msg and "人工核对" in msg and "偏保守" not in msg


def test_A股传年报网址_校准拿到附注(monkeypatch):
    r = SimpleNamespace(code="600519", annual_url="http://x/1225114741.PDF")
    assert full._dcf_kw(r) == {"annual_url": "http://x/1225114741.PDF"}
    seen = {}

    def calibrate(code, years, stmts=None, as_of=None, fin_notes=None):
        seen["fin_notes"] = fin_notes
        return {"meta": {}, "fin_bridge": {"net": 1179.2, "items": [("拆出资金", 990.96, "报表")]}}
    monkeypatch.setattr(dcf, "_fin_notes", lambda url, notes: {"nca1y_fin": D("26871114612.71")})
    D_ = SimpleNamespace(validate_code=lambda c: c, DataError=RuntimeError)
    cal, notes, _ = dcf._calibrate(D_, SimpleNamespace(calibrate=calibrate), "600519", AS, None, 10,
                                   annual_url="http://x/1225114741.PDF")
    assert seen["fin_notes"] == {"nca1y_fin": D("26871114612.71")}
    assert any("非经营性金融资产" in n and "1179.20" in n for n in notes)


def test_附注读失败不阻断(monkeypatch):
    import ir_agent.sources.annual_notes as AN
    monkeypatch.setattr(AN, "extract_fin_notes_from_pdf", lambda p: (_ for _ in ()).throw(ValueError("坏 PDF")))
    import ir_agent.sources.annual_pdf as AP
    monkeypatch.setattr(AP, "_download", lambda url, d=None: b"%PDF")
    notes = []
    assert dcf._fin_notes("http://x/a.PDF", notes) is None
    assert notes and "附注" in notes[0]


def test_A股营业利润率扣税金及附加():
    from ir_agent import assume, opex_judg as OJ
    from tests.test_assume_opex_prompt import JUDG
    text = "\n".join(f"假设: {k} | 第{y}年 | 6% | 5% | 4% | 理由" for k in OJ.OPEX_ITEMS["cn"] for y in range(1, 6))
    a = assume.parse(text, 5, us=False)
    gm = {c: [0.9] * 5 for c in ("bear", "base", "bull")}
    with_t = OJ.margin_lines(a, gm, dict(JUDG, tsur=0.16))
    assert any("第1年 营业利润率 56.00% / 59.00% / 62.00%" in x for x in with_t)      # 90 − 16 − 3×(6/5/4)
    without = OJ.margin_lines(a, gm, None)
    assert "未扣税金及附加" in without[1]
