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

    print(r.summary())
    if a.show_draft:
        print("\n──── 草稿（占位符） ────")
        print(r.draft)
    print("\n──── 成稿 ────")
    print(r.audit.rendered)
    print("──── 脚注 ────")
    for n in r.audit.footnotes:
        print(n.text())

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
