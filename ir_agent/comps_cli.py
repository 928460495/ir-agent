"""可比公司分析 CLI。

    python -m ir_agent.comps_cli 600519 \
        --peers 000858,000568,000596 --year 2025 \
        --rationale "高端白酒，渠道结构与提价能力可比"

可比名单必须显式给出并说明理由 —— 选谁做可比是分析判断，要写进研报。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from ir_agent.comps import (
    Exclusion,
    InsufficientPeers,
    PeerSet,
    build_comps,
    fetch_row,
    peer_metrics_from_ledger,
)
from ir_agent.pipeline import run


def _fetch(snapshot_dir: str, period_suffix: str):
    def _f(code: str, period: str, as_of: date):
        year = int(period[:4])
        r = run(code, year=year, as_of=as_of, snapshot_dir=snapshot_dir,
                period_suffix=period_suffix, adjudicate_on_conflict=False)
        spot = next((f.period for f in (r.quotes.facts if r.quotes else [])
                     if f.key == "market_cap"), None)
        return {"name": code,
                "metrics": peer_metrics_from_ledger(r.ledger, period, as_of, spot)}
    return _f


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ir_agent.comps_cli", description="可比公司分析")
    p.add_argument("target", help="目标公司代码")
    p.add_argument("--peers", required=True, help="可比公司代码，逗号分隔")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--period", default="FY", choices=["FY", "H1", "Q1-Q3", "Q1"])
    p.add_argument("--rationale", required=True, help="为什么这几家可比（会写进研报）")
    p.add_argument("--as-of", default=None)
    p.add_argument("--snapshots", default="snapshots")
    a = p.parse_args(argv)

    as_of = date.fromisoformat(a.as_of) if a.as_of else date.today()
    period = f"{a.year}{a.period}"

    try:
        ps = PeerSet(target=a.target, peers=[c.strip() for c in a.peers.split(",")],
                     rationale=a.rationale)
    except ValueError as e:
        print(f"可比名单不合法: {e}", file=sys.stderr)
        return 2

    fetch = _fetch(a.snapshots, a.period)
    rows, excluded = [], []
    for code in ps.all_codes:
        row, exc = fetch_row(code, period, as_of, fetch)
        (rows if row else excluded).append(row or exc)
        print(f"  {'✓' if row else '✗'} {code}"
              + ("" if row else f"  {exc.reason[:60]}"), file=sys.stderr)

    if not any(r.code == a.target for r in rows):
        print(f"\n目标公司 {a.target} 数据不可得，无法出可比表。", file=sys.stderr)
        return 1

    try:
        table = build_comps(a.target, rows, period=period, as_of=as_of,
                            excluded=excluded)
    except InsufficientPeers as e:
        print(f"\n{e}", file=sys.stderr)
        return 1

    print()
    print(f"可比理由: {ps.rationale}")
    print(table.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
