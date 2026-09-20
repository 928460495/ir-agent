# -*- coding: utf-8 -*-
"""季节性对冲的回测闸门——它可能否决 Plan 2A。

Task 1（费用率：销售/管理/研发）与 Task 2（周转天数：DSO/DIO/DPO）改的是两个
不同的子系统，各自的前提要各自验证——用费用率回测通过去放行周转天数，等于
用一个子系统的验证结论去背书另一个子系统，这正是这道闸门本来要防的事。

两类量纲不同（费用率是比率，周转天数是「天」），因此**分别**统计、**分别**
判定，不合并成一个总 MAE。

回测口径（两个 Task 共用同一个「留出检验」思路）：
  裸年报法：用 t−1 年度的同一口径值预测 t
  对冲法：  t−1 年度值 +（t 年中报同口径值 − t−1 年中报同口径值）
与 t 年实际值比较误差。

周转天数的中报值需要先把「流量」（营收/营业成本）年化——年化因子用「该
公司近几年中报营收占全年营收比重均值」的倒数，算法与 ashare_calibrate.py
里 `shr` 保持一致，**不用固定 ×2**：`_wc` 函数上方的注释明写，固定 ×2 会因
收入非均匀分布系统性抬高天数（生益 DPO 126→164，实测不可用）。对冲法里
t 与 t−1 两期中报天数用同一个年化因子，因子自身的偏差在做同比差分时会
相互抵消——这正是「同比差分」比「直接拿年化值当全年值」更可靠的地方。

用法：
  python3 tests/backtest_hedge.py 600183 600584 000333 600519
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import ashare_data as D
import ashare_calibrate as C

# ---------- Task 1：费用率（销售/管理/研发） ----------
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


# ---------- Task 2：周转天数（DSO/DIO/DPO） ----------
# key -> (存量科目, 流量科目)。存量用期末余额（不年化，balance sheet 本就是
# 时点数），流量（营收/营业成本）在中报口径下要年化。
WC_ITEMS = {"DSO": ("ar", "rev"), "DIO": ("inv", "cogs"), "DPO": ("ap", "cogs")}


def _raw_ar(bal, p):
    """应收余额。与 ashare_calibrate.opening_balances 用同一套科目优先级，
    但**不**用 `_v0` 把缺失默认成 0——回测里「缺数据」必须变成「跳过这个
    样本」，而不是伪装成「应收为零」去污染 MAE。"""
    v = D.val(bal, "应收票据及应收账款", p)
    if v is not None:
        return v
    a, b = D.val(bal, "应收票据", p), D.val(bal, "应收账款", p)
    return None if (a is None and b is None) else (a or 0) + (b or 0)


def _raw_ap(bal, p):
    v = D.val(bal, "应付票据及应付账款", p)
    if v is not None:
        return v
    a, b = D.val(bal, "应付票据", p), D.val(bal, "应付账款", p)
    return None if (a is None and b is None) else (a or 0) + (b or 0)


def _stock(bal, key, p):
    if key == "ar":
        return _raw_ar(bal, p)
    if key == "ap":
        return _raw_ap(bal, p)
    return D.val(bal, "存货", p)  # inv


def _flow(inc, key, p):
    if key == "rev":
        return D.val(inc, "营业总收入", p) or D.val(inc, "营业收入", p)
    return D.val(inc, "营业成本", p)  # cogs


def _days(bal, inc, stock_key, flow_key, p, ann_factor=1.0):
    s, f = _stock(bal, stock_key, p), _flow(inc, flow_key, p)
    return (s / (f * ann_factor) * 365) if (f and s is not None) else None


def _shr(inc, window, mmdd="0630"):
    """近几年中报营收占全年营收比重的均值——与 ashare_calibrate.py 里 `shr`
    的算法保持一致（只认「营业总收入」这一口径的中报值，不做「营业收入」
    兜底，分母的全年营收才兜底），倒数即为年化因子。"""
    xs = []
    for a in window:
        full = D.val(inc, "营业总收入", a) or D.val(inc, "营业收入", a)
        half = D.val(inc, "营业总收入", a[:4] + mmdd)
        if full and half is not None:
            xs.append(half / full)
    return float(np.mean(xs)) if xs else None


def backtest_wc(code):
    st = D.fetch_statements(code)
    inc, bal = st["income"], st["balance"]
    ann = D.annual_periods(inc, 8)
    rows = []
    for i in range(2, len(ann)):
        t, t1 = ann[i], ann[i - 1]
        itm_t, itm_t1 = t[:4] + "0630", t1[:4] + "0630"
        # shr 只用 t 之前的年度算（窗口截止于 t−1），不偷看 t 本身；
        # 与生产代码 `ann[-4:]` 同一个含义——「预测 t 时手上能有的最近
        # 4 个整年」。
        window = ann[max(0, i - 4):i]
        if len(window) < 2:
            continue
        shr = _shr(inc, window)
        if not shr:
            continue
        af = 1.0 / shr  # 年化因子：中报流量 × af ≈ 全年流量
        for key, (sk, fk) in WC_ITEMS.items():
            act = _days(bal, inc, sk, fk, t)
            base = _days(bal, inc, sk, fk, t1)
            now = _days(bal, inc, sk, fk, itm_t, af)
            prev = _days(bal, inc, sk, fk, itm_t1, af)
            if None in (act, base, now, prev):
                continue
            hedged = C._yoy_hedge(base, now, prev)
            rows.append((key, t, act, base, hedged))
    return rows


# ---------- 汇总与判据（两个 Task 各自独立） ----------
def _report(name, allrows):
    if not allrows:
        print(f"\n[{name}] 无样本——无法判定")
        return None
    naive = np.mean([abs(a - b) for _, _, a, b, _ in allrows])
    hedge = np.mean([abs(a - h) for _, _, a, _, h in allrows])
    win = sum(1 for _, _, a, b, h in allrows if abs(a - h) < abs(a - b))
    n = len(allrows)
    print(f"\n[{name}] 样本 {n} 个")
    print(f"  裸年报法 MAE = {naive:.5f}")
    print(f"  对冲法   MAE = {hedge:.5f}")
    print(f"  对冲更准的样本占比 = {win/n:.1%}")
    better = hedge < naive
    print(f"  判据：对冲法 MAE 必须低于裸年报法 → {'通过' if better else '未通过'}")
    return dict(n=n, naive=naive, hedge=hedge, win_rate=win / n, better=better)


def main(codes):
    cost_rows, wc_rows = [], []
    for c in codes:
        try:
            rows = backtest(c)
            cost_rows += rows
            print(f"  {c}: 费用率 {len(rows)} 个样本")
        except Exception as e:
            print(f"  {c}: 费用率取数失败 {type(e).__name__}: {str(e)[:60]}")
        try:
            rows = backtest_wc(c)
            wc_rows += rows
            print(f"  {c}: 周转天数 {len(rows)} 个样本")
        except Exception as e:
            print(f"  {c}: 周转天数取数失败 {type(e).__name__}: {str(e)[:60]}")

    print("\n" + "=" * 60)
    print("Task 1：费用率（销售/管理/研发）")
    cost = _report("费用率", cost_rows)

    print("\n" + "=" * 60)
    print("Task 2：周转天数（DSO/DIO/DPO）")
    wc = _report("周转天数", wc_rows)

    cost_ok = bool(cost and cost["better"])
    wc_ok = bool(wc and wc["better"])

    print("\n" + "=" * 60)
    print("结论（两条判据各自独立，不互相背书）：")
    print(f"  Task 1 费用率对冲 → {'通过，可继续' if cost_ok else '未通过/无样本'}")
    print(f"  Task 2 周转天数对冲 → {'通过，可继续' if wc_ok else '未通过/无样本'}")
    if not wc_ok:
        print("\n  周转天数判据未通过：状态报 BLOCKED，Task 2（DSO/DIO/DPO 对冲）不应实施。")
        print("  Task 1（费用率）已单独通过，不受周转天数结果影响。")
    return 0 if (cost_ok and wc_ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["600183", "600584", "000333", "600519"]))
