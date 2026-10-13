"""命令行入口: python -m ir_agent 600519 --year 2025"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from ir_agent.pipeline import run, unresolved_disagreements


def build_parser() -> argparse.ArgumentParser:
    """单独成函数，好让测试拿真实解析器去校验「指引里的命令能不能跑」。

    此前那条规则靠比对字符串（「命令里含 --review」）来守，于是删掉
    --review 之后测试照样通过，而打给用户的命令已经跑不动了。
    """
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
    # 第一阶段拆成三次调用、三道人工门（spec 2026-10-12）。每家公司
    # 4 次命令、3 个签名：--analyse → --brief → --assume → --value。
    p.add_argument("--analyse", metavar="DIR", default=None,
                   help="第一阶段之一：定量采集 → 语料库入库 → 定性分析 → "
                        "门① 资料来源清单（sources.md）")
    p.add_argument("--brief", metavar="DIR", default=None,
                   help="第一阶段之二：多空辩论① → 方法论预研 → 因子采集 → "
                        "多空辩论② → 撰写简报（需 --verified-by）")
    p.add_argument("--assume", metavar="DIR", default=None,
                   help="第一阶段之三：AI 填 DCF 判断项 + 四道检查"
                        "（需 --reviewed-by）")
    p.add_argument("--value", metavar="DIR", default=None,
                   help="第二阶段：读回已核实的判断项 → 三表 DCF 建模 → 完整研报")
    p.add_argument("--verified-by", default=None,
                   help="资料来源核验人署名（--brief 必需）")
    p.add_argument("--reviewed-by", default=None,
                   help="简报结论审阅人署名（--assume 必需）")
    p.add_argument("--approved-by", default=None,
                   help="判断项核实人署名（--value 必需）")
    p.add_argument("--accept-manual", action="store_true",
                   help="读回人工补写的 stance_*.json，跳过该环节的模型调用"
                        "（只对 --brief 有意义）")
    p.add_argument("--refresh-stance", action="store_true",
                   help="重抽多空辩论，覆盖已有的 stance_a/b.json"
                        "（默认沿用已有产物，与 drivers.md 同一规矩）")
    p.add_argument("--strict", action="store_true",
                   help="本期两源分歧、跨源勾稽不一致、或存在抓取故障时判为失败")
    return p


def _check_args(a) -> str:
    """参数自洽性检查。**必须排在取数之前。**

    此前 `--approved-by` 的校验排在 `run()` 之后，于是忘了署名的人要等整轮
    取数（几千条事实、三表勾稽、每家同业各一次接口）跑完，才被告知「你没
    署名」。参数错误是在读任何数据之前就能判定的；顺带这也让签名那条单元
    测试不必连网。

    `--assume --accept-manual` **报错而不是静默忽略**：两轮辩论都在 `--brief`
    里，`--assume` 没有可读回的 stance_*.json。静默忽略一个用户显式给出的
    开关，会让人以为自己的补写生效了。
    """
    for flag, sig, why in (
            ("brief", "verified_by", "资料来源要有人核验过才谈得上可用"),
            ("assume", "reviewed_by", "简报结论要有人审阅过才谈得上可据以建模"),
            ("value", "approved_by", "假设要有人负责才谈得上「已核实」")):
        if getattr(a, flag) and not getattr(a, sig):
            return (f"--{flag} 需同时给出 --{sig.replace('_', '-')} <署名> "
                    f"—— {why}。")
    if a.assume and a.accept_manual:
        return ("--accept-manual 只对 --brief 有意义 —— 两轮多空辩论都在那一次"
                "调用里，--assume 没有可读回的 stance_*.json。")
    if a.refresh_stance and not a.brief:
        return "--refresh-stance 只对 --brief 有意义。"
    return ""


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if (err := _check_args(a)):
        print(err, file=sys.stderr)
        return 2

    as_of = date.fromisoformat(a.as_of) if a.as_of else date.today()

    try:
        r = run(a.code, year=a.year, as_of=as_of,
                snapshot_dir=a.snapshots, period_suffix=a.period)
    except Exception as e:                      # noqa: BLE001
        print(f"运行失败: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2

    if a.analyse:
        from ir_agent.full import report, run_analyse
        print(r.summary())
        report(run_analyse(a.code, r, as_of, a.analyse, model=a.model,
                            snapshot_dir=a.snapshots),
               out_dir=a.analyse, year=a.year)
        return 0

    # 签名校验已在 `_check_args` 里做过（取数之前），各分支不再重复。
    # 函数层（run_brief / run_assume / run_valuation）各自还有一道 SystemExit，
    # 给直接调 API、不经 CLI 的人。
    if a.brief:
        from ir_agent.full import report, run_brief
        print(r.summary())
        report(run_brief(a.code, r, as_of, a.brief, a.verified_by,
                         model=a.model, snapshot_dir=a.snapshots,
                         accept_manual=a.accept_manual,
                         refresh_stance=a.refresh_stance),
               out_dir=a.brief, year=a.year)
        return 0

    if a.assume:
        from ir_agent.full import report, run_assume
        print(r.summary())
        report(run_assume(a.code, r, as_of, a.assume, a.reviewed_by,
                          model=a.model, snapshot_dir=a.snapshots),
               out_dir=a.assume, year=a.year)
        return 0

    if a.value:
        from ir_agent.full import report, run_valuation
        print(r.summary())
        report(run_valuation(a.code, r, as_of, a.value, a.approved_by,
                             model=a.model, snapshot_dir=a.snapshots),
               out_dir=a.value, year=a.year)
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

    if a.html:
        from ir_agent.dashboard import build_dashboard
        out = build_dashboard(r, path=a.html)
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
