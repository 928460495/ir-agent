"""端到端流程 —— 产出所有能自动产出的东西，在人工审核门前停下。

独立成模块而非塞进 __main__: CLI 已经有十来个开关，再叠一段长分支会让
两种用法互相纠缠。这里的流程是线性的、可单独测试的。
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from ir_agent.analyst import AnalystRefused, FactCatalog, write_body
from ir_agent.audit import audit
from ir_agent.clients.claude_cli import ClaudeCliError, make_client
from ir_agent.dashboard import build_dashboard
from ir_agent.debate import DebateRefused, run_debate
from ir_agent.market import market_of
from ir_agent.research import (
    ResearchOutcome,
    Stage,
    assumption_template,
    next_actions,
)
from ir_agent.revision import RevisionFailed, revise_from_verdicts
from ir_agent.sources import eastmoney
from ir_agent.valuation.route import ValuationMethod, annual_series, route
from ir_agent.xlsx import build_workbook

FORECAST_YEARS = 5


def _route(code: str):
    """估值方法路由。失败不阻断流程 —— 其余产物仍然有价值。"""
    try:
        em, _ = eastmoney.fetch_statements_em(code, market=market_of(code))
        hist = annual_series(em, "net_profit")
        if not hist:
            return None, "无年度盈利序列，无法判定估值方法"
        ct = eastmoney.detect_company_type(code, market_of(code))
        return route(ct, hist, hist[-1]), ""
    except Exception as e:                                  # noqa: BLE001
        return None, f"估值方法路由（{e.__class__.__name__}）"


def _write_and_debate(r, code: str, model: str | None):
    """撰写 → 辩论 → 回写。返回 (正文, 成立挑战数, 辩论摘要, 开销, 失败说明)。"""
    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=r.as_of,
                                  extra_periods=(spot,) if spot else ())
    client = make_client(model=model)
    try:
        print(f"撰写正文（{len(cat.entries)} 条可引用事实）…", file=sys.stderr)
        body, _ = write_body(cat, client, code=code, period=r.period)

        print("多空辩论…", file=sys.stderr)
        d = run_debate(cat, draft=body, client=client, period=r.period)

        if d.upheld_count:
            print(f"回写 {d.upheld_count} 项成立的挑战…", file=sys.stderr)
            body, _ = revise_from_verdicts(cat, body, d.challenges, client)

        return body, d.upheld_count, d.summary(), client.total_cost_usd, ""
    except (AnalystRefused, DebateRefused, RevisionFailed, ClaudeCliError) as e:
        return None, 0, "", client.total_cost_usd, f"{e.__class__.__name__}: {e}"


def run_full(code: str, r, as_of: date, out_dir: str | Path,
             model: str | None = None) -> ResearchOutcome:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    arts: dict[str, str] = {}
    skipped: list[str] = []

    rt, route_err = _route(code)
    if route_err:
        skipped.append(route_err)

    body, upheld, debate_md, cost, err = _write_and_debate(code=code, r=r,
                                                           model=model)
    if err:
        print(f"\n撰写/辩论中止：{err}", file=sys.stderr)
        skipped.append("研究正文")
    if debate_md:
        (out / "debate.md").write_text(debate_md, encoding="utf-8")
        arts["辩论记录"] = str(out / "debate.md")
    if body:
        r.audit = audit(body, r.ledger, r.store, as_of=r.as_of)
        (out / "report.md").write_text(r.audit.rendered, encoding="utf-8")
        arts["研究正文"] = str(out / "report.md")

    build_dashboard(r, path=out / "dashboard.html", route=rt)
    arts["看板"] = str(out / "dashboard.html")
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)
    arts["工作簿"] = str(out / "model.xlsx")

    # 假设模板只在路由认可 DCF 时才写 —— 给一份用不上的模板会误导人去填
    if rt is not None and rt.method is ValuationMethod.DCF:
        (out / "assumptions.yaml").write_text(
            assumption_template(FORECAST_YEARS), encoding="utf-8")
        arts["假设模板"] = str(out / "assumptions.yaml")
        stage = Stage.AWAITING_REVIEW
    else:
        stage = Stage.NOT_APPLICABLE

    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=stage,
        artifacts=arts, skipped=skipped, upheld=upheld,
        cost_usd=Decimal(str(cost)),
        route_reason=rt.reason if rt else "")


def run_stage2(code: str, r, as_of: date, out_dir: str | Path,
               assumptions_path: str, reviewer: str) -> ResearchOutcome:
    """第二阶段: 读入已审核的假设 → 建模 → 重建看板与工作簿。"""
    from ir_agent.assumptions_io import AssumptionFileError, load_assumptions
    from ir_agent.stage2 import ValuationBlocked, value_from_file

    out = Path(out_dir)
    report_md = out / "report.md"
    if not report_md.exists():
        raise SystemExit(
            f"找不到 {report_md} —— 请先运行第一阶段：\n"
            f"  python -m ir_agent {code} --year <年度> --full {out}")
    body = report_md.read_text(encoding="utf-8")

    try:
        a = load_assumptions(assumptions_path)
    except AssumptionFileError as e:
        raise SystemExit(f"假设文件有误：{e}")

    spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                 if f.key == "market_cap"), None)
    try:
        res = value_from_file(assumptions=a, ledger=r.ledger, body=body,
                              period=r.period, spot_period=spot or r.period,
                              as_of=as_of, reviewer=reviewer)
    except ValuationBlocked as e:
        raise SystemExit(f"无法出具估值：\n{e}")

    rt, _ = _route(code)
    build_dashboard(r, path=out / "dashboard.html", route=rt,
                    assumptions=res.valuation.assumptions,
                    valuation=res.valuation, sensitivity=res.sensitivity)
    build_workbook(r.ledger, code=code, period=r.period, as_of=r.as_of,
                   path=out / "model.xlsx", verdicts=r.verdicts)

    return ResearchOutcome(
        code=code, period=r.period, as_of=as_of, stage=Stage.VALUED,
        artifacts={"研究正文": str(report_md),
                   "看板（含估值）": str(out / "dashboard.html"),
                   "工作簿": str(out / "model.xlsx")},
        route_reason=(f"每股价值 {res.valuation.value_per_share:,.2f} 元"
                      f"（终值占比 {res.valuation.terminal_share:.1%}，"
                      f"审核 {reviewer}）"))


def report(outcome: ResearchOutcome) -> None:
    print()
    print(outcome.summary())
    acts = next_actions(outcome.stage, outcome.code,
                        outcome.artifacts.get("假设模板"))
    if acts:
        print("\n下一步：")
        for i, act in enumerate(acts, 1):
            print(f"  {i}. {act}")
