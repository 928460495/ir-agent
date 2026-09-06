"""命令行入口: python -m ir_agent 600519 --year 2025"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from ir_agent.pipeline import run, unresolved_disagreements


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ir_agent", description="V0 投研流水线：事实账本 + 三表勾稽 + 可溯源成稿")
    p.add_argument("code", help="股票代码，如 600519")
    p.add_argument("--year", type=int, required=True, help="报告年度")
    p.add_argument("--period", default="FY", choices=["FY", "H1", "Q1-Q3", "Q1"])
    p.add_argument("--as-of", default=None, help="分析时点 YYYY-MM-DD，默认今天")
    p.add_argument("--snapshots", default="snapshots", help="快照目录")
    p.add_argument("--show-draft", action="store_true", help="打印占位符草稿")
    p.add_argument("--write", action="store_true",
                   help="用 claude -p 撰写正文（无需 API key，走现有登录）")
    p.add_argument("--debate", action="store_true",
                   help="正文写完后进行多空辩论（需配合 --write）")
    p.add_argument("--model", default=None, help="指定模型，如 claude-opus-5")
    p.add_argument("--html", metavar="PATH", default=None,
                   help="生成可交互 HTML 看板")
    p.add_argument("--xlsx", metavar="PATH", default=None,
                   help="导出 Excel 工作簿到指定路径")
    p.add_argument("--full", metavar="DIR", default=None,
                   help="端到端：撰写+辩论+回写+看板+Excel+假设模板，产物写入该目录")
    p.add_argument("--assumptions", metavar="PATH", default=None,
                   help="已审核的假设文件（第二阶段，需配合 --full 与 --reviewer）")
    p.add_argument("--reviewer", default=None, help="假设审核人署名")
    p.add_argument("--strict", action="store_true",
                   help="本期两源分歧、跨源勾稽不一致、或存在抓取故障时判为失败")
    a = p.parse_args(argv)

    as_of = date.fromisoformat(a.as_of) if a.as_of else date.today()

    try:
        r = run(a.code, year=a.year, as_of=as_of,
                snapshot_dir=a.snapshots, period_suffix=a.period)
    except Exception as e:                      # noqa: BLE001
        print(f"运行失败: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2

    if a.assumptions:
        if not (a.full and a.reviewer):
            print("--assumptions 需同时给出 --full <目录> 与 --reviewer <署名>",
                  file=sys.stderr)
            return 2
        from ir_agent.full import report, run_stage2
        print(r.summary())
        report(run_stage2(a.code, r, as_of, a.full, a.assumptions, a.reviewer))
        return 0

    if a.full:
        from ir_agent.full import report, run_full
        print(r.summary())
        report(run_full(a.code, r, as_of, a.full, model=a.model))
        return 0

    print(r.summary())
    if a.show_draft:
        print("\n──── 草稿（占位符） ────")
        print(r.draft)
    print("\n──── 成稿 ────")
    print(r.audit.rendered)
    print("──── 脚注 ────")
    for n in r.audit.footnotes:
        print(n.text())

    if a.write:
        from ir_agent.analyst import AnalystRefused, FactCatalog, write_body
        from ir_agent.clients.claude_cli import ClaudeCliError, make_client
        from ir_agent.audit import audit

        spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                     if f.key == "market_cap"), None)
        cat = FactCatalog.from_ledger(r.ledger, period=r.period, as_of=r.as_of,
                                      extra_periods=(spot,) if spot else ())
        client = make_client(model=a.model)
        print(f"\n正在撰写正文（{len(cat.entries)} 条可引用事实）…", file=sys.stderr)
        try:
            body, attempts = write_body(cat, client, code=a.code, period=r.period)
        except (AnalystRefused, ClaudeCliError) as e:
            print(f"\n撰写失败：{e}", file=sys.stderr)
            return 2
        written = audit(body, r.ledger, r.store, as_of=r.as_of)
        print(f"撰写完成：{attempts} 次尝试 · "
              f"${client.total_cost_usd:.4f} · {client.calls} 次调用", file=sys.stderr)
        print("\n──── 分析师正文 ────")
        print(written.rendered)
        print("──── 脚注 ────")
        for n in written.footnotes:
            print(n.text())
        print()
        print(written.summary())

        if a.debate:
            from ir_agent.debate import DebateRefused, run_debate
            print("\n正在进行多空辩论…", file=sys.stderr)
            try:
                d = run_debate(cat, draft=body, client=client)
                print(f"辩论完成：累计 ${client.total_cost_usd:.4f} · "
                      f"{client.calls} 次调用", file=sys.stderr)
                print("\n──── 多空辩论 ────")
                print(d.summary())

                if d.upheld_count:
                    from ir_agent.revision import RevisionFailed, revise_from_verdicts
                    print("\n正在按裁决修订正文…", file=sys.stderr)
                    try:
                        body2, changed = revise_from_verdicts(
                            cat, body, d.challenges, client)
                        if changed:
                            final = audit(body2, r.ledger, r.store, as_of=r.as_of)
                            print(f"修订完成：累计 ${client.total_cost_usd:.4f} · "
                                  f"{client.calls} 次调用", file=sys.stderr)
                            print("\n──── 修订后正文 ────")
                            print(final.rendered)
                            print()
                            print(final.summary())
                    except RevisionFailed as e:
                        print(f"\n修订失败：{e}", file=sys.stderr)
            except DebateRefused as e:
                print(f"\n辩论中止：{e}", file=sys.stderr)

    route_result = None
    try:
        from ir_agent.market import market_of
        from ir_agent.sources import eastmoney
        from ir_agent.valuation.route import annual_series, route as route_fn
        em, _ = eastmoney.fetch_statements_em(a.code, market=market_of(a.code))
        hist = annual_series(em, "net_profit")
        if hist:
            route_result = route_fn(
                eastmoney.detect_company_type(a.code, market_of(a.code)),
                hist, hist[-1])
            print(f"\n估值方法：{route_result.describe()}")
    except Exception as e:                          # noqa: BLE001
        print(f"\n估值方法路由未完成：{e.__class__.__name__}", file=sys.stderr)

    if a.html:
        from ir_agent.dashboard import build_dashboard
        out = build_dashboard(r, path=a.html, route=route_result)
        print(f"已生成看板: {out}")

    if a.xlsx:
        from ir_agent.xlsx import build_workbook
        out = build_workbook(r.ledger, code=a.code, period=r.period,
                             as_of=r.as_of, path=a.xlsx, verdicts=r.verdicts)
        print(f"\n已导出 Excel: {out}")

    # 验收门槛: 勾稽无失败 且 可溯源率 100%
    passed = r.reconcile.ok and r.audit.traceability == 1.0
    if a.strict:
        if r.financials is not None:
            open_ds = unresolved_disagreements(
                r.financials.disagreements_in(r.period), r.verdicts)
            if open_ds:
                keys = "、".join(sorted({d.key for d in open_ds}))
                print(f"\n本期存在未裁定的两源分歧: {keys}", file=sys.stderr)
                passed = False
        if r.cross is not None and not r.cross.ok:
            print("\n跨源勾稽有源未通过，疑似字段映射错位。", file=sys.stderr)
            passed = False
        if r.skips.has_defects:
            print("\n存在抓取故障（非数据缺失）。", file=sys.stderr); passed = False
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
