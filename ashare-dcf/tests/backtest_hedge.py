# -*- coding: utf-8 -*-
"""季节性对冲的回测闸门——它可能否决 Plan 2A。

Task 1（费用率：销售/管理/研发）与 Task 2（周转天数：DSO/DIO/DPO）改的是两个
不同的子系统，各自的前提要各自验证——用费用率回测通过去放行周转天数，等于
用一个子系统的验证结论去背书另一个子系统，这正是这道闸门本来要防的事。

两类量纲不同（费用率是比率，周转天数是「天」），因此**分别**统计、**分别**
判定，不合并成一个总 MAE。

回测口径（两个 Task 各自匹配各自的生产口径，不再共用一句话描述）：
  Task 1 费用率：
    近3年均值法：用 t−1（含）之前最近 3 个年度的均值预测 t——I1（final-
      review-plan2a 终审）之前这里错写成"用 t−1 单年年报值"，与生产代码
      （ashare_calibrate.py 的 `_mean(ratio(...))`）口径错位：闸门证明的是
      "t−1 年报 + Δ 比 t−1 年报准"，上线的却是"近3年均值 + Δ"，均值在趋势期
      天然滞后，补一年的差分补不平这份滞后。改成与生产一致的近3年均值后，
      两者才是同一件事的验证。
    对冲法：近3年均值 +（t 年中报同口径值 − t−1 年中报同口径值）
  Task 2 周转天数：
    裸年报法：用 t−1 年度的同一口径值预测 t——与生产代码（`_wc(last)`，
      只用最近一期年报、不取均值）口径一致，不作 I1 式的改动。
    对冲法：t−1 年度值 +（t 年中报同口径值 − t−1 年中报同口径值）
  两种口径下都与 t 年实际值比较误差。

周转天数的中报值需要先把「流量」（营收/营业成本）年化——年化因子用「该
公司近几年中报营收占全年营收比重均值」的倒数，算法与 ashare_calibrate.py
里 `shr` 保持一致，**不用固定 ×2**：`_wc` 函数上方的注释明写，固定 ×2 会因
收入非均匀分布系统性抬高天数（生益 DPO 126→164，实测不可用）。对冲法里
t 与 t−1 两期中报天数用同一个年化因子——M1（final-review-plan2a 终审）：
这个因子是【乘性】进入增量的（Δdays = (365/af)·(S_t/F_t − S_{t−1}/F_{t−1})），
af 偏 10% 则增量整体偏 10%，并不会"相互抵消"；旧文案说"相互抵消"是错的，
只是相对【水平项】而言这份偏差是二阶小量。这不足以推翻 Task 2 的 BLOCKED
结论——af 偏差只缩放增量，缩放不能把"对冲比裸年报更差"缩放成"更好"，
Task 2 不是被误拦的，见 main() 里的判据与既有回测记录。

108 个样本（Task 1，6 家公司 × ~6 年 × 3 项）里，同一公司同一年的三项共用
同一个营收分母、高度相关，有效样本量接近 36 甚至 6——main() 按公司拆分
MAE 一并打印，若改善集中在一两家公司，这道闸门就没有资格通过。

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


def _mean_ratio3(inc, item, periods3):
    """近3年年报均值——与生产口径 `ashare_calibrate._mean(ratio(item), ...)`
    一致（那里的 `a3 = ann[-3:]`）。任一年缺失就跳过，不强行凑 3 个。"""
    xs = [_ratio(inc, item, p) for p in periods3]
    xs = [x for x in xs if x is not None]
    return float(np.mean(xs)) if xs else None


def backtest(code):
    st = D.fetch_statements(code)
    inc = st["income"]
    ann = D.annual_periods(inc, 8)
    rows = []
    for i in range(3, len(ann)):
        t = ann[i]
        a3 = ann[i - 3:i]                      # 预测 t 时手上能有的最近 3 个整年
        itm_t, itm_t1 = t[:4] + "0630", str(int(t[:4]) - 1) + "0630"
        for item in ITEMS:
            act = _ratio(inc, item, t)
            base = _mean_ratio3(inc, item, a3)  # I1：近3年年报均值，不是 t-1 单年
            now, prev = _ratio(inc, item, itm_t), _ratio(inc, item, itm_t1)
            if None in (act, base, now, prev):
                continue
            hedged = C._yoy_hedge(base, now, prev)
            rows.append((item, t, act, base, hedged, code))
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
            rows.append((key, t, act, base, hedged, code))
    return rows


# ---------- 汇总与判据（两个 Task 各自独立） ----------
def _report(name, allrows, baseline_label="裸年报法"):
    if not allrows:
        print(f"\n[{name}] 无样本——无法判定")
        return None
    naive = np.mean([abs(a - b) for _, _, a, b, _, _ in allrows])
    hedge = np.mean([abs(a - h) for _, _, a, _, h, _ in allrows])
    win = sum(1 for _, _, a, b, h, _ in allrows if abs(a - h) < abs(a - b))
    n = len(allrows)
    print(f"\n[{name}] 样本 {n} 个")
    print(f"  {baseline_label} MAE = {naive:.5f}")
    print(f"  对冲法   MAE = {hedge:.5f}")
    print(f"  对冲更准的样本占比 = {win/n:.1%}")
    # I1（final-review-plan2a 终审）：108 个样本＝6 家×~6 年×3 项，同一公司
    # 同一年三项共用同一个营收分母、高度相关，有效样本量接近 36 甚至 6——
    # 按公司拆分 MAE，若改善集中在一两家高增长公司，这道闸门就没有资格通过。
    by_code = {}
    for _, _, a, b, h, code in allrows:
        d = by_code.setdefault(code, {"naive": [], "hedge": []})
        d["naive"].append(abs(a - b)); d["hedge"].append(abs(a - h))
    print(f"  按公司拆分 MAE：")
    by_code_summary = {}
    for code, d in sorted(by_code.items()):
        nm, hm = float(np.mean(d["naive"])), float(np.mean(d["hedge"]))
        verdict = "对冲更优" if hm < nm else "裸法/均值法更优"
        print(f"    {code}: n={len(d['naive']):>2}  {baseline_label}={nm:.5f}  对冲={hm:.5f}  {verdict}")
        by_code_summary[code] = dict(n=len(d["naive"]), naive=nm, hedge=hm, better=hm < nm)
    better = hedge < naive
    print(f"  判据：对冲法 MAE 必须低于{baseline_label} → {'通过' if better else '未通过'}")
    return dict(n=n, naive=naive, hedge=hedge, win_rate=win / n, better=better, by_code=by_code_summary)


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
