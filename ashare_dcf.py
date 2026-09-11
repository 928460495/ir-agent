#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股 DCF 通用模板 — CLI

用法:
    python3 ashare_dcf.py 600183
    python3 ashare_dcf.py 600519 --years 7 --out ~/Desktop
"""
from __future__ import annotations
import argparse, sys, os, datetime as dt, warnings
warnings.filterwarnings("ignore")

def main() -> int:
    ap=argparse.ArgumentParser(description="输入A股代码，生成可编辑的三表+DCF模型")
    ap.add_argument("code", help="6位A股代码，如 600183")
    ap.add_argument("--years", type=int, default=5, help="预测年数（默认5）")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--no-verify", action="store_true", help="跳过公式引擎验算（不推荐）")
    ap.add_argument("--mc", action="store_true", help="附加蒙特卡洛模拟页")
    ap.add_argument("--mc-paths", type=int, default=None, help="模拟路径数（默认 50000）")
    ap.add_argument("--data-table", action="store_true",
                    help="额外在「1_假设」页追加 Excel 原生模拟运算表（追加后工作簿不能再被引擎验算）")
    a=ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ashare_data as D, ashare_calibrate as C, ashare_build as B
    try:
        code=D.validate_code(a.code)
        print(f"[1/4] 取数 {code} …")
        cal=C.calibrate(code, a.years)
    except D.DataError as e:
        print(f"\n❌ {e}", file=sys.stderr); return 2
    m=cal["meta"]
    print(f"      {m['name']}  现价 ¥{m['price']}  总股本 {m['shares']:.2f} 亿股  行情源 {m['quote_source']}")
    print(f"[2/4] 校准假设 … beta {m['beta_raw']}→{m['beta']}  Rf {m['rf']*100:.3f}%  期初平衡 {cal['open']['_check']:.1e}")
    xc = cal.get("xcheck") or {}
    if xc.get("ok") is None:
        print(f"      ⚠ 交叉校验 {xc.get('note','不可用')}")
    elif xc.get("ok"):
        print(f"      ✅ 新浪 ↔ 东财 双源交叉校验：{xc['note']}")
    else:
        print(f"      ⚠ 新浪 ↔ 东财 双源交叉校验：{xc['note']}")
        for x in xc["rows"][:6]:
            print(f"         · {x}")
    print(f"      折旧摊销来源：{cal.get('da_src','?')}")
    print(f"      债务成本 Kd：{cal.get('kd_src','?')}")
    for x in (cal.get("clamps") or []):
        print(f"      ⚠ 校准被截断：{x}")
    os.makedirs(a.out, exist_ok=True)
    fn=os.path.join(a.out, f"{code}_{m['name']}_DCF_{dt.date.today():%Y%m%d}.xlsx")
    print(f"[3/4] 生成工作簿 …")
    B.build(cal, fn)
    if a.no_verify:
        print(f"[4/4] 已跳过验算\n\n✅ {fn}"); return 0
    print(f"[4/4] 公式引擎实跑验算 …")
    import ashare_verify as V
    fails, dv = V.verify(fn)
    fails += [x[1:] for x in V.verify_all_scenarios(fn, quiet=True) if x[0]!='Base']
    if fails:
        print(f"\n❌ 验算未通过，文件可能有误：{fn}", file=sys.stderr); return 1
    print(f"\n✅ {fn}")
    print(f"   每股内在价值 ¥{dv['ps']:.2f}   现价 ¥{m['price']}   空间 {(dv['ps']/m['price']-1)*100:+.1f}%")
    print(f"   WACC {dv['wacc']*100:.2f}%  终值ROIC {dv['roic']*100:.1f}%  再投资率 {dv['rr']*100:.1f}%  终值占EV {dv['pvtv']/dv['ev0']*100:.1f}%")
    inp=V.read_inputs(fn); rows,_=V.recompute(inp)
    warns=V.sanity(dv, rows) + V.balance_sheet_warnings(inp, rows) + V.assumption_warnings(inp)
    if warns:
        print("\n   ⚠ 健全性警告（结果不可直接采信）：")
        for x in warns: print(f"     · {x}")
    print(f"\n   ⚠ 营收增速与毛利率为判断项（黄底），三档初始同值，请自行复核并拉开")
    if a.mc:
        import ashare_mc as MC
        j=cal["judg"]
        hist=dict(sd_growth=j["g_stats"][1], sd_margin=j["gm_stats"][1],
                  rho=cal.get("_rho",0.0), rho_n=cal.get("_rho_n",0),
                  tax_mean=cal["gen"]["tax"], beta_mean=cal["dcf"]["beta"])
        MC.run(fn, hist=hist, n=a.mc_paths)
    if a.data_table:
        import ashare_datatable as DT
        made=DT.add(fn)
        print("\n   已追加 Excel 原生模拟运算表（在「1_假设」页底部）：")
        for x in made: print(f"     · {x}")
        print("     ⚠ 该页含 DataTable，本工作簿此后无法再被 formulas 引擎验算；")
        print("       追加发生在验算通过之后，「5_DCF」的引擎版敏感性表可作交叉核对。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
