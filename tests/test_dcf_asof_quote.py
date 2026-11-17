"""第④步把分析时点交给 A 股校准（2026-11-17，生益 600183：as_of 10-21，却用运行当天实时价
58.61 与截至当天的 β）。不联网。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from ir_agent import dcf


def test_校准拿到分析时点():
    seen = {}

    def calibrate(code, years, stmts=None, as_of=None):
        seen.update(code=code, years=years, as_of=as_of)
        return {"meta": {}}
    D = SimpleNamespace(validate_code=lambda c: c, DataError=RuntimeError)
    C = SimpleNamespace(calibrate=calibrate)
    dcf._calibrate(D, C, "600183", date(2026, 10, 21), None, 10)
    assert seen == {"code": "600183", "years": 10, "as_of": date(2026, 10, 21)}


def test_校准按分析时点取了行情就不再说取的是今日行情():
    """生益 2026-11-17 重跑：行情已按 10-21 收盘，注释仍说「本模型取的是今日行情」，
    撰写者照写进风险一节「模型取行情时点晚于分析时点」。"""
    from datetime import timedelta
    past = date.today() - timedelta(days=27)
    assert dcf._asof_note(past, meta={"asof": past.isoformat()}) == []
    assert dcf._asof_note(past, meta={"asof": date.today().isoformat()})       # 校准没按时点，照旧提醒
    assert dcf._asof_note(past)
