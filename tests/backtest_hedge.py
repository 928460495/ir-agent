# -*- coding: utf-8 -*-
"""季节性对冲的回测闸门——它可能否决 Plan 2A。

Task 1/2 的前提是「同比差分比裸年报均值更接近真实全年值」。这句话听起来
合理，但没被验证过。用历史年份做留出检验：对每个有中报的年度 t，比较
  裸年报法：用 t−1 年报值预测 t
  对冲法：  t−1 年报值 +（t 年中报 − t−1 年中报）
与 t 年实际值的误差。

用法：
  python3 tests/backtest_hedge.py 600183 600584 000333 600519
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import ashare_data as D
import ashare_calibrate as C

ITEMS = {"销售费用": "sell", "管理费用": "admin", "研发费用": "rd"}


def _ratio(inc, item, p):
    rv = D.val(inc, "营业总收入", p)
    v = D.val(inc, item, p)
    return (v / rv) if (rv and v is not None) else None


def backtest(code):
    st = D.fetch_statements(code)
    inc = st["income"]
    ann = D.annual_periods(inc, 8)
    rows = []
    for i in range(2, len(ann)):
        t, t1 = ann[i], ann[i - 1]
        itm_t, itm_t1 = t[:4] + "0630", t1[:4] + "0630"
        for item in ITEMS:
            act = _ratio(inc, item, t)
            base = _ratio(inc, item, t1)
            now, prev = _ratio(inc, item, itm_t), _ratio(inc, item, itm_t1)
            if None in (act, base, now, prev):
                continue
            hedged = C._yoy_hedge(base, now, prev)
            rows.append((item, t, act, base, hedged))
    return rows


def main(codes):
    allrows = []
    for c in codes:
        try:
            rows = backtest(c)
            allrows += rows
            print(f"  {c}: {len(rows)} 个样本")
        except Exception as e:
            print(f"  {c}: 取数失败 {type(e).__name__}: {str(e)[:60]}")
    if not allrows:
        print("\n无样本——无法判定，**不要继续 Plan 2A 的 Task 1/2**")
        return 2
    naive = np.mean([abs(a - b) for _, _, a, b, _ in allrows])
    hedge = np.mean([abs(a - h) for _, _, a, _, h in allrows])
    win = sum(1 for _, _, a, b, h in allrows if abs(a - h) < abs(a - b))
    print(f"\n样本 {len(allrows)} 个")
    print(f"  裸年报法 MAE = {naive:.5f}")
    print(f"  对冲法   MAE = {hedge:.5f}")
    print(f"  对冲更准的样本占比 = {win/len(allrows):.1%}")
    better = hedge < naive
    print(f"\n判据：对冲法 MAE 必须低于裸年报法 → {'✅ 通过，可继续' if better else '❌ 未通过'}")
    if not better:
        print("  **停下来告诉用户**：季节性对冲在历史数据上并不更准，")
        print("  Task 1/2 的前提不成立，不应实施。")
    return 0 if better else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["600183", "600584"]))
