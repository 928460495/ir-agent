# -*- coding: utf-8 -*-
"""A股 DCF 模板 — 假设校准层。从历史推导可推导的假设；判断项只给保守默认。"""
from __future__ import annotations
import warnings, datetime as dt
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import ashare_data as D

def _v(df, item, p): return D.val(df, item, p)
def _v0(df, item, p):
    x = D.val(df, item, p)
    return 0.0 if x is None else x
# 所有被上下限截断 / 落到默认值的校准项，集中记录在这里。
# 静默 clamp 会把「口径错误」伪装成「推导结果」——均胜电子实测税率 29.75%
# 被 hi=.25 悄悄截断，工作簿里只看得到 25%，看不出它曾被动过手脚。
_CLAMPS: list = []


def _mean(xs, lo=None, hi=None, default=None, what=None):
    xs = [x for x in xs if x is not None and np.isfinite(x)]
    if not xs:
        if what is not None and default is not None:
            _CLAMPS.append(f"{what}：无可用历史数据，取默认值 {default:.4g}")
        return default
    m = raw = float(np.mean(xs))
    if lo is not None: m = max(m, lo)
    if hi is not None: m = min(m, hi)
    if what is not None and abs(m - raw) > 1e-12:
        rng = f"{'' if lo is None else f'{lo:.4g}'}–{'' if hi is None else f'{hi:.4g}'}"
        _CLAMPS.append(f"{what}：原值 {raw:.4g} 被截断至 {m:.4g}（允许区间 {rng}，样本 {len(xs)} 个）")
    return m


def _yoy_hedge(last_annual, itm_now, itm_prev, lo=None, hi=None, what=None):
    """季节性对冲：最近年报值 +（本期中期 − 去年同期中期）。

    **为什么不直接用中期值当全年。** 两个方向的季节性都会系统性带偏：
    年末计提奖金、研发集中在下半年，H1 费用率偏低；而中期"×2 年化"会
    因收入非均匀分布抬高周转天数（生益 DPO 126→164，见下方 `_wc` 的注释）。
    同比差分只取"今年比去年同期多/少了多少"这一份增量，加到季节性干净的
    年报口径上，两种偏差各自抵消。

    任一中期缺失 → 退回年报原值。**缺数据不是"同比没变"**，但在没有更好
    信息时，年报口径本身是可辩护的；而硬凑一个差分会把噪声当增量。
    """
    if last_annual is None:
        return None
    if itm_now is None or itm_prev is None:
        return float(last_annual)
    m = raw = float(last_annual) + (float(itm_now) - float(itm_prev))
    if lo is not None: m = max(m, lo)
    if hi is not None: m = min(m, hi)
    if what is not None and abs(m - raw) > 1e-12:
        rng = f"{'' if lo is None else f'{lo:.4g}'}–{'' if hi is None else f'{hi:.4g}'}"
        _CLAMPS.append(f"{what}（季节性对冲后）：原值 {raw:.4g} 被截断至 {m:.4g}（允许区间 {rng}）")
    return m


# 费用率三项**不设上下限**——原 `_mean(ratio("销售费用"), default=.02, ...)` 调用
# 本来就没有 lo/hi，只有 default。写计划时一度给了 (0, 0.30) 这类区间，那是
# 凭空引入的新截断：对冲只是换了个估计口径，没有理由顺手给它加一道原先不存在
# 的闸门。真出现异常值，应该在 _hedge_note 里看得见，而不是被悄悄夹住。
_EXPENSE_BOUNDS = {"sell": (None, None), "admin": (None, None), "rd": (None, None)}
_EXPENSE_LABEL = {"sell": "销售费用率", "admin": "管理费用率", "rd": "研发费用率"}


def _apply_expense_hedge(gen, itm_now, itm_prev, note):
    """把 gen 里的 sell/admin/rd 三项就地换成季节性对冲值，并在 note 里留痕。

    **留痕是硬要求。** 交付物写着这些参数「由历史校准」，读者有权知道
    校准用的是裸年报均值还是对冲后的值——没对冲就不留痕，留了痕就必须
    是真对冲过，两者不能含糊。
    """
    if not itm_now or not itm_prev:
        return
    for k, (lo, hi) in _EXPENSE_BOUNDS.items():
        base = gen.get(k)
        if base is None or itm_now.get(k) is None or itm_prev.get(k) is None:
            continue
        hedged = _yoy_hedge(base, itm_now[k], itm_prev[k],
                            lo=lo, hi=hi, what=_EXPENSE_LABEL[k])
        if hedged is None:
            continue
        note[k] = {"annual": base, "hedged": round(hedged, 6)}
        gen[k] = hedged


def opening_balances(bal, p) -> dict:
    """最近年报期初余额，8 项资产 + 8 项负债权益，保证恒等。"""
    g = lambda k: _v0(bal, k, p)
    ca, nca = g("流动资产合计"), g("非流动资产合计")
    o = dict(
        cash=g("货币资金"), tfa=g("交易性金融资产"),
        ar=g("应收票据及应收账款") or (g("应收票据") + g("应收账款")),
        inv=g("存货"))
    o["oca"] = ca - o["cash"] - o["tfa"] - o["ar"] - o["inv"]
    o["ppe"] = g("固定资产净额") or g("固定资产")
    o["cip"] = g("在建工程合计") or g("在建工程")
    o["onca"] = nca - o["ppe"] - o["cip"]
    o["st"] = g("短期借款")
    o["ap"] = g("应付票据及应付账款") or (g("应付票据") + g("应付账款"))
    o["cur"] = g("一年内到期的非流动负债")
    o["ocl"] = g("流动负债合计") - o["st"] - o["ap"] - o["cur"]
    o["lt"] = g("长期借款")
    o["oncl"] = g("负债合计") - g("流动负债合计") - o["lt"]
    o["mi"] = g("少数股东权益")
    o["eq"] = g("资产总计") - g("负债合计") - o["mi"]
    o["_check"] = (o["cash"]+o["tfa"]+o["ar"]+o["inv"]+o["oca"]+o["ppe"]+o["cip"]+o["onca"]) - \
                  (o["st"]+o["ap"]+o["ocl"]+o["cur"]+o["lt"]+o["oncl"]+o["mi"]+o["eq"])
    return o

def implied_da(inc, bal, cf, periods) -> list[float]:
    """OCF 倒推 D&A：OCF − 净利润 + Δ经营性营运资本。累计折旧增量会被处置冲减，不用。"""
    out = []
    for i in range(1, len(periods)):
        p0, p1 = periods[i-1], periods[i]
        def nwc(p):
            return (_v0(bal,"流动资产合计",p) - _v0(bal,"货币资金",p) - _v0(bal,"交易性金融资产",p)) - \
                   (_v0(bal,"流动负债合计",p) - _v0(bal,"短期借款",p) - _v0(bal,"一年内到期的非流动负债",p))
        ocf = _v(cf, "经营活动产生的现金流量净额", p1)
        ni = _v(inc, "净利润", p1)
        if ocf is None or ni is None: continue
        out.append(ocf - ni + (nwc(p1) - nwc(p0)))
    return out

def _gm_std_annual(gm_hist, ann, rev, cogs):
    """历史年度毛利率标准差——按与 gm_mean 相同的过滤条件（`n_ann_gm`）数
    年度项数，不把 gm_hist 末项（半年报）算进"历史年度"的离散度。

    I2（final-fix-D 终审）：ashare_build.py 写进 F 列给人看的是「Bull/Bear
    ＝±1 个历史年度毛利率标准差」，实际却一直拿 `round(np.std(gm_hist), 4)`
    喂给 anchor_three——gm_hist 最后一项就是本期中报毛利率，σ 因此混进了
    中报，且把 Base 值自己（往往正是那个离群的中报数）算进了它自己的
    离散度。600183 实跑：σ(全部 6 项) = 0.0383，σ(年度 5 项) = 0.0292，
    宽 31%。`gm_mean`（`gm_stats[0]`）早就用 `n_ann_gm` 过滤过同一件事
    （见 b13b276），这里复用同一套判据，不新写一份。
    """
    n_ann_gm = sum(1 for p_ in ann if rev.get(p_) and cogs.get(p_))
    return float(np.std(gm_hist[:n_ann_gm])) if n_ann_gm else float(np.std(gm_hist))


def _capex_from_growth(growth3, base_rev, fa_turnover, maint_rate):
    """由各档营收增速推出各档资本开支（以占当年营收比表示）。

    **三档同值是情景分析的作弊方式。** 原实现三档都用「近三年 capex/营收
    均值线性收敛到折旧率」，于是 Bull 档 45% 的营收增速也配同样的资本
    开支——等于假设增长不需要投入，只把有利项调高。

    当年资本开支 = Δ营收 ÷ 目标固定资产周转率 + 维持性投资。
    前一项是扩产所需的新增固定资产，后一项≈折旧（维持现有产能）。
    营收下滑时增量项归零而非转负：**收缩不等于卖厂房**。
    """
    out = {}
    for case, gs in growth3.items():
        rev_prev = float(base_rev)
        seq = []
        for g in gs:
            rev_now = rev_prev * (1.0 + float(g))
            delta = max(rev_now - rev_prev, 0.0)
            capex = delta / fa_turnover + rev_prev * maint_rate
            seq.append(round(capex / rev_now, 4) if rev_now else 0.0)
            rev_prev = rev_now
        out[case] = seq
    return out


def anchor_three(share_hist, gm, gm_std, share_mean=None):
    """锚定两格的三档 (Bull, Base, Bear)：已披露期占全年营收比重、剩余期毛利率。

    **规则只在这里定义一份。** 工作簿按它填 1_假设 第三小节；ir-agent 按它算
    「各档锚定后的首年毛利率」，好让第 2 年起的毛利率路径从各档实际起点接着
    走。两边各写一遍规则，早晚分叉成工作簿一个数、批准清单另一个数。

    - 占比：近几年同期占比的最小/均值/最大。占比越低 → 隐含全年营收越高 →
      Bull，所以 Bull 取最小值。
    - 剩余期毛利率：Base 沿用已披露期毛利率，Bull/Bear 各偏离一个历史年度
      毛利率标准差（下限 0.5pp，防历史过于平稳时三档重合）。
    """
    sd = max(float(gm_std), 0.005)
    # share_mean：调用方手里只有**已取整**的历史序列时（build 读旧形状的中期块），
    # 直接传 calibrate 当初用原始序列算好的均值——在取整后的数据上重新求均值会差
    # 一位小数（生益：0.47845→0.4784，而原值是 0.4785），工作簿与清单就不是同一个数。
    mean = float(share_mean) if share_mean is not None else float(np.mean(share_hist))
    share3 = (round(min(share_hist), 4), round(mean, 4), round(max(share_hist), 4))
    gm3 = (min(round(gm + sd, 4), 0.95), round(gm, 4), max(round(gm - sd, 4), 0.0))
    return share3, gm3


def beta_blume(code: str, years: int = 5) -> tuple[float, float, int]:
    px = D.fetch_prices(code); ix = D.fetch_index()
    m = px.join(ix, lsuffix="_s", rsuffix="_i").dropna()
    r = np.log(m).diff().dropna()
    r = r[r.index >= r.index[-1] - pd.DateOffset(years=years)]
    if len(r) < 200: raise D.DataError(f"可用交易日仅 {len(r)} 天，不足以回归 beta（需 ≥200）。")
    raw = float(np.polyfit(r["close_i"], r["close_s"], 1)[0])
    return 0.67*raw + 0.33, raw, len(r)

def calibrate(code: str, nyears: int = 5, stmts: dict | None = None) -> dict:
    """stmts 给定时不再自行取数 —— 供上游（如 ir-agent 的事实账本）注入
    已经过双源交叉验证、带披露日的报表，避免分析层与估值层各取一套数据
    而悄悄分叉。形状须与 fetch_statements 一致: {表: DataFrame}，
    索引为报告日 YYYYMMDD，列为中文科目名，值以元计。"""
    _CLAMPS.clear()      # 本次校准的截断/默认值记录，随 cal 一起交付
    st = stmts if stmts is not None else D.fetch_statements(code)
    inc, bal, cf = st["income"], st["balance"], st["cashflow"]
    ann0 = D.annual_periods(inc, 5)
    D.assert_non_financial(bal, ann0[-1], inc)
    D.assert_modelable(inc, ann0)
    ann = D.annual_periods(inc, 5)
    interim = D.latest_interim(inc, ann)
    hp = ann + ([interim] if interim else [])
    last = ann[-1]
    q = D.fetch_quote(code); rf, rfd = D.fetch_riskfree()
    beta, beta_raw, nobs = beta_blume(code)

    last_ann = ann[-1]
    rev = {p: _v(inc, "营业总收入", p) or _v(inc, "营业收入", p) for p in hp}
    cogs = {p: _v(inc, "营业成本", p) for p in hp}
    a3 = ann[-3:]
    ratio = lambda item: [(_v(inc, item, p) or 0)/rev[p] for p in a3 if rev.get(p)]

    # 中期费用率：本期中期与**去年同期**中期，用于季节性对冲（见 _yoy_hedge）。
    # 去年同期 = 把本期中期的报告日年份换成上一年（如 20260630 → 20250630）。
    def _ratio_at(p):
        rv = _v(inc, "营业总收入", p)
        if not rv:
            return None
        return {"sell": (_v(inc, "销售费用", p) or 0)/rv,
                "admin": (_v(inc, "管理费用", p) or 0)/rv,
                "rd": (_v(inc, "研发费用", p) or 0)/rv}
    _itm_now = _ratio_at(interim) if interim else None
    _itm_prev = None
    if interim:
        _prev_p = str(int(interim[:4]) - 1) + interim[4:]
        _itm_prev = _ratio_at(_prev_p)

    gm_hist = [1 - cogs[p]/rev[p] for p in hp if rev.get(p) and cogs.get(p)]
    g_hist = [rev[ann[i]]/rev[ann[i-1]] - 1 for i in range(1, len(ann)) if rev.get(ann[i-1])]
    cagr3 = (rev[ann[-1]]/rev[ann[-4]])**(1/3) - 1 if rev.get(ann[-4]) else _mean(g_hist, default=0.05)

    def _other_op(p):
        tot = 0.0; hit = False
        for k in ["信用减值损失","资产减值损失","公允价值变动收益","资产处置收益"]:
            x = _v(inc, k, p)
            if x is not None: tot += x; hit = True
        return tot if hit else None
    other_op = _mean([(_other_op(p) or 0)/rev[p] for p in a3 if rev.get(p)], lo=-.05, hi=.02, default=0.0,
                      what="其他经营损益 / 营收")

    # ---------- 折旧与摊销：改用东财【间接法补充资料】的申报明细 ----------
    # 原来只能从 OCF 倒推 D&A（新浪不给补充资料），那个残差把减值、公允价值变动、
    # 递延税等【全部非现金项】都吸收了进来，分子虚高而分母只有固定资产。
    # 实测：长电 18.04% 对上了（巧合），生益 13.13% vs 真值 9.89% —— 高估 32.8%，
    # 远超 README 不足 #8 原先记的「6–8%」。
    em = D.fetch_eastmoney(code)
    ppe_open = [_v0(bal, "固定资产净额", ann[i-1]) for i in range(1, len(ann))]
    _dep, _amo, _src = [], [], "东财间接法补充资料"
    for i in range(1, len(ann)):
        d_, a_ = D.em_depr_amort(em, ann[i])
        _dep.append(d_); _amo.append(a_)
    # 折旧与摊销【分别】设门槛。合在一起判会造出不存在的摊销：
    # 只要折旧字段在、摊销字段缺，amortrate 就会拿到 default=.002 —— 而 em_depr_amort
    # 的约定是「任一缺失返回 None，不填 0」，被上一层的 default 推翻了。
    if sum(1 for x in _dep if x) >= 2:
        deprate = _mean([d/p for d, p in zip(_dep, ppe_open) if d and p], .02, .35, None, "折旧率")
    else:
        # 降级：东财不可用时回到 OCF 倒推，并【显式标注口径不严】
        da = implied_da(inc, bal, cf, ann)
        deprate = _mean([d/p for d, p in zip(da, ppe_open) if p], .04, .30, None, "折旧率")
        _src = "⚠ 东财不可用，回退 OCF 倒推（分子含减值等非现金项，口径不严）"
    if deprate is None: deprate = .09
    if sum(1 for x in _amo if x is not None) >= 2:
        amortrate = _mean([a/rev[ann[i+1]] for i, a in enumerate(_amo)
                           if a is not None and rev.get(ann[i+1])], 0.0, .06, None, "摊销率")
        if amortrate is None: amortrate = 0.0
    else:
        # 摊销明细不可得 → 置 0 并标注，绝不用「0.2%」这种凭空默认值：
        # 它会无税盾地全额加进 FCFF，还会抽干「其他非流动资产」。
        amortrate = 0.0
        _src += "｜⚠ 摊销明细不可得，摊销率置 0（未凭空默认）"

    # 资本开支的去向拆分。现金流量表那一行是「购建固定资产、【无形资产】和其他长期资产」，
    # 本来就同时买了折旧资产与摊销资产；若全额记入固定资产，摊销就成了只出不进的单向抽干
    # （实测中免 10 年把「其他非流动资产」耗掉 98.5%）。
    # 默认按【申报折旧 : 申报摊销】的比例分配 —— 即假设两类资产以各自的消耗速度同比重置，
    # 稳态下该科目自然稳定。摊销率为 0 时该比重为 0，行为与拆分前完全一致。
    _d_ok = [d for d in _dep if d]; _a_ok = [a for a in _amo if a]
    if _d_ok and _a_ok:
        _dm = sum(_d_ok)/len(_d_ok); _am = sum(_a_ok)/len(_a_ok)
        _cx_int = _am/(_dm+_am) if (_dm+_am) > 0 else 0.0
    else:
        _cx_int = 0.0
    if _cx_int > 0.50:      # 与 _mean 一致：截断必须留痕，不能悄悄发生
        _CLAMPS.append(f"资本开支投向无形/长期待摊的比重：原值 {_cx_int:.4g} 被截断至 0.5（允许区间 0–0.5）")
    cx_intan = round(float(min(max(_cx_int, 0.0), 0.50)), 4)
    # 减值加回现金的同时必须减记资产，否则表不平。但「减记哪一项」在本模型里是真歧义：
    # 信用减值对应应收、存货跌价对应存货（两者都被周转天数驱动，动不得），
    # 资产减值/商誉/公允价值变动则分散在固定资产与其他非流动资产。
    # 折中：按【期初账面余额比例】分摊。全额压固定资产会把轻资产公司的固定资产吃掉
    # （实测中免 10 年 58 → 32.5 亿，终值 ROIC 被推到 28.4%）。
    _p0 = _v0(bal, "固定资产净额", last_ann)
    _cip0 = _v0(bal, "在建工程合计", last_ann) or _v0(bal, "在建工程", last_ann)   # 与 opening_balances 同口径
    _n0 = _v0(bal, "非流动资产合计", last_ann) - _p0 - _cip0
    imp_ppe = round(float(min(max(_p0/(_p0+_n0) if (_p0+_n0) > 0 else 1.0, 0.0), 1.0)), 4)
    capex_r = _mean([abs(_v0(cf, "购建固定资产、无形资产和其他长期资产所支付的现金", p))/rev[p] for p in a3 if rev.get(p)],
                    lo=.01, hi=.25, default=.06, what="资本开支 / 营收")
    debt_open = [_v0(bal,"短期借款",ann[i-1])+_v0(bal,"一年内到期的非流动负债",ann[i-1])+_v0(bal,"长期借款",ann[i-1])
                 for i in range(1, len(ann))]
    fin = [_v0(inc, "财务费用", ann[i]) for i in range(1, len(ann))]
    irate = _mean([f/d for f, d in zip(fin, debt_open) if d > 0.5], lo=.015, hi=.08, default=.035,
                   what="有息负债利率")
    # ---------- 债务成本 Kd ----------
    # 原来是硬编码 rf+120bp，与公司无关；而利润表用的是实测利率，两者从不相通。
    # 均胜电子实测付息 3.95%，折现却按 2.88% 算 —— 45.7% 的债务权重下 WACC 被低估 37bp。
    # 但实测值【不是随时可用】：现金远超有息负债时财务费用净额为负，倒推出的"利率"没有意义
    # （中免实测 −13.19%）。故设前置条件：样本年份必须同时满足 有息负债>0 且 财务费用>0。
    _kd_floor = round(rf + .012, 4)
    _kd_obs = [f/d for f, d in zip(fin, debt_open) if d > 0.5 and f > 0]
    _n_bad = sum(1 for f, d in zip(fin, debt_open) if d > 0.5 and f <= 0)
    if len(_kd_obs) >= 2:
        _obs = float(np.mean(_kd_obs))
        kd = round(max(_kd_floor, min(_obs, .12)), 4)     # 上限 12% 防脏数据
        if _obs > .12:
            _CLAMPS.append(f"实测有息负债利率：原值 {_obs:.4g} 被截断至 0.12（允许区间 –0.12）")
        kd_src = (f"实测付息成本 {_obs*100:.2f}%（{len(_kd_obs)} 个年度）"
                  + ("" if kd > _kd_floor else f"，低于下限 中债10Y+120bp={_kd_floor*100:.2f}%，取下限"))
    else:
        kd = _kd_floor
        kd_src = (f"中债10Y+120bp={_kd_floor*100:.2f}%（"
                  + ("实测不可用：财务费用净额为负，说明利息收入大于利息支出"
                     if _n_bad else "实测不可用：样本期有息负债过小") + "）")
    # 25% 是法定税率，超过它一定有原因（境外业务、以前年度亏损、递延所得税）。
    # 那是【使用者该判断的事】，模型不该替他抹平 —— 改为按实际值采用并显式提示。
    # hi=.45 只防脏数据（单年亏损会让有效税率发散）。
    taxr = _mean([(_v0(inc,"所得税费用",p))/(_v(inc,"利润总额",p) or np.nan) for p in a3], lo=.05, hi=.45, default=.15,
                  what="实际所得税率")
    if taxr is not None and taxr > .25:
        _CLAMPS.append(f"实际所得税率 {taxr*100:.2f}% 高于 25% 法定税率：常见于境外业务占比高、"
                       f"以前年度亏损或递延所得税。已按实际值采用（未截断），请复核是否可持续")
    # 少数股东占比是结构性比率（哪些子公司并表），不是周期项。
    # 用 3 年均值会在比例单向抬升时严重滞后（生益 2023 -1.3% → 2025 14.3%，均值仅 6.6%）。
    _ni_last = _v(inc, "净利润", last_ann)
    _mi_last = (_v0(inc, "少数股东损益", last_ann) / _ni_last) if _ni_last else None
    mish = _mean([_mi_last], lo=0.0, hi=.45, default=.03, what="少数股东损益占比")
    mish_hist = [round((_v0(inc,"少数股东损益",p))/(_v(inc,"净利润",p)),4)
                 for p in ann if _v(inc,"净利润",p)]

    # 增速与毛利率的历史相关系数（蒙特卡洛用）。
    # 用报表里【全部可得年度】而非建模窗口的 5 年——4 个观测算相关系数不稳健。
    _rho=0.0; _rho_n=0
    try:
        _all=sorted(p for p in inc.index if p.endswith("1231"))
        _r=[_v(inc,"营业总收入",p) or _v(inc,"营业收入",p) for p in _all]
        _c=[_v(inc,"营业成本",p) for p in _all]
        _g=[]; _m=[]
        for i in range(1,len(_all)):
            if _r[i] and _r[i-1] and _c[i]:
                _g.append(_r[i]/_r[i-1]-1); _m.append(1-_c[i]/_r[i])
        if len(_g)>=5:
            _rho=float(np.corrcoef(np.array(_g),np.array(_m))[0,1]); _rho_n=len(_g)
            if not np.isfinite(_rho): _rho=0.0; _rho_n=0
    except Exception:
        pass

    o = opening_balances(bal, last)
    # 营运资本天数：优先用最近中期年化（最新信息），否则用最近年报
    def _wc(p, ann_factor):
        b = opening_balances(bal, p)
        rv = rev[p]*ann_factor; cg = cogs[p]*ann_factor
        return dict(dso=round(b["ar"]/rv*365,1), dio=round(b["inv"]/cg*365,1),
                    dpo=round(b["ap"]/cg*365,1), ocap=b["oca"]/rv, oclp=b["ocl"]/rv)
    # 用年报口径：年末余额 ÷ 全年流量，季节性干净。
    # 中期"×2 年化"会因收入非均匀分布而系统性抬高天数（生益 DPO 126→164），不可用。
    wc = _wc(last, 1.0); wc["fy"] = dict(wc)
    wc["interim_note"] = None
    if interim:
        try:
            b = opening_balances(bal, interim)
            shr = float(np.mean([ (_v(inc,"营业总收入",a[:4]+interim[4:]) or 0)/rev[a]
                                  for a in ann[-4:] if rev.get(a) and _v(inc,"营业总收入",a[:4]+interim[4:]) ]))
            rv = rev[interim]/shr; cg = cogs[interim]/shr
            wc["interim_note"] = dict(dso=round(b["ar"]/rv*365,1), dio=round(b["inv"]/cg*365,1),
                                      dpo=round(b["ap"]/cg*365,1))
        except Exception: pass
    g_term = 0.03
    growth = [round(cagr3 + (g_term - cagr3)*(i+1)/nyears, 4) for i in range(nyears)]
    gm_last = gm_hist[-1]
    # gm_hist 是按「营收 *和* 营业成本都有」过滤出来的（见上面的推导式），
    # 而 assert_modelable 明文容忍近 5 年缺 1 年营业成本。所以它的长度不等于
    # len(ann) —— 直接切 [:len(ann)] 会在缺年的情形下把末尾的中报也算进
    # 「年度均值」，让一个半年度数字污染年度口径。年度项数要按与 gm_hist
    # 完全相同的过滤条件数一遍。
    n_ann_gm = sum(1 for p_ in ann if rev.get(p_) and cogs.get(p_))
    gm_mean = float(np.mean(gm_hist[:n_ann_gm])) if n_ann_gm else float(np.mean(gm_hist))
    # I2：σ 与 gm_mean 用同一套「只算年度、不含中报」的判据，见 _gm_std_annual。
    gm_std_ann = _gm_std_annual(gm_hist, ann, rev, cogs)
    gm = [round(gm_last + (gm_mean - gm_last)*(i+1)/nyears, 4) for i in range(nyears)]
    dep_over_rev = (deprate * o["ppe"] + amortrate * rev[last]) / rev[last]
    capex = [round(capex_r + (dep_over_rev - capex_r)*(i)/max(nyears-1,1), 4) for i in range(nyears)]
    # 目标固定资产周转率：近三年实际值的均值。用它把「多长的营收」换算成
    # 「需要多少固定资产」——比手填三档资本开支更难只调有利项。三档资本
    # 开支由 build 侧调用 _capex_from_growth 按各档营收增速推出，这里只
    # 交付 fa_turn / maint_rate 两个参数；上面的 capex 单序列保留不动，
    # 它是 build 里 _scen 的 fallback，上游没给三档增速时仍要能建表。
    fa_turn = _mean([rev[p]/_v0(bal, "固定资产净额", p) for p in a3
                     if rev.get(p) and _v0(bal, "固定资产净额", p)],
                    lo=0.3, hi=10.0, default=2.0, what="固定资产周转率")
    maint_rate = round(dep_over_rev, 5)

    # 已披露中期：占全年比重（用于锁定首个预测年）
    itm_block = None
    if interim:
        mmdd = interim[4:]
        sh_hist = []
        for a in ann[-4:]:
            pr = _v(inc, "营业总收入", a[:4]+mmdd) or _v(inc, "营业收入", a[:4]+mmdd)
            fy = rev.get(a)
            if pr and fy: sh_hist.append(pr/fy)
        if sh_hist:
            itm_rev = rev.get(interim); itm_cogs = cogs.get(interim)
            itm_block = dict(period=interim, rev=itm_rev, cogs=itm_cogs,
                             gm=(1-itm_cogs/itm_rev) if itm_rev and itm_cogs else None,
                             share=round(float(np.mean(sh_hist)), 4),
                             share_hist=[round(x,4) for x in sh_hist],
                             share_std=round(float(np.std(sh_hist)), 4),
                             label="半年报" if mmdd=="0630" else ("一季报" if mmdd=="0331" else "三季报"))
            if itm_block["gm"] is not None:
                # σ 与 gm_stats 同源（_gm_std_annual，只算年度不含中报），
                # 不另起口径——I2 之前这里直接 np.std(gm_hist)，把中报也
                # 算了进去，与 gm_stats[1] 的口径分叉过。
                itm_block["share3"], itm_block["gm_rest3"] = anchor_three(
                    sh_hist, itm_block["gm"], round(gm_std_ann, 4))
    # 债务权重按公司【实际】资本结构算，不能硬编码。
    # 原来固定 1%：生益碰巧接近真值(1.01%)，长电实际 7.23% → WACC 被高估 0.43pp。
    _debt = o["st"] + o["cur"] + o["lt"]
    _shares = q.get("shares") if q.get("shares") and np.isfinite(q["shares"]) else np.nan
    _mktcap = (q["price"] * _shares) if _shares == _shares else np.nan
    _wd = round(float(_debt/(_debt+_mktcap)), 4) if _mktcap == _mktcap and (_debt+_mktcap) > 0 else 0.02
    _wd = float(min(max(_wd, 0.0), 0.60))

    today = dt.date.today()
    y_end = dt.date(int(last[:4]), 12, 31)
    roll = round((today - y_end).days / 365.0, 3)

    # 销售/管理/研发三项改用季节性对冲（年报 + 本期中期 − 去年同期中期），
    # 不再只看近三年年报均值——最新半年的费用结构变化能进来了。
    # _hedge_note 留痕：交付物写着这些参数「由历史校准」，读者有权知道
    # 用的是裸年报均值还是对冲后的值。
    _gen = dict(sell=_mean(ratio("销售费用"), default=.02, what="销售费用率"),
                admin=_mean(ratio("管理费用"), default=.04, what="管理费用率"),
                rd=_mean(ratio("研发费用"), default=.03, what="研发费用率"))
    _hedge_note = {}
    _apply_expense_hedge(_gen, _itm_now, _itm_prev, _hedge_note)

    return dict(
        meta=dict(code=code, name=q.get("name") or code, price=q["price"],
                  shares=q.get("shares") if q.get("shares") and np.isfinite(q["shares"]) else np.nan,
                  quote_source=q["source"], asof=today.isoformat(), rf=rf, rf_date=rfd,
                  beta=round(beta,4), beta_raw=round(beta_raw,4), beta_n=nobs,
                  nyears=nyears, last_annual=last),
        periods=dict(annual=ann, interim=interim, all=hp,
                     labels=[p[:4]+"A" for p in ann] + ([interim[:4]+"H1A" if interim[4:6]=="06" else interim[:4]+"Q"+str(int(interim[4:6])//3)+"A"] if interim else []),
                     proj=[f"{int(last[:4])+i+1}E" for i in range(nyears)]),
        stmts=st,
        _rho=round(_rho,3), _rho_n=_rho_n,
        interim=itm_block,
        open=o,
        gen=dict(sell=_gen["sell"], admin=_gen["admin"], rd=_gen["rd"],
                 _hedge_note=_hedge_note,
                 tsur=_mean(ratio("营业税金及附加"), default=.005, what="税金及附加率"),
                 other_inc=_mean(ratio("其他收益"), default=.005, what="其他收益 / 营收"), inv_inc=_mean(ratio("投资收益"), default=.003, what="投资收益 / 营收"),
                 irate=irate, cash_yield=.015, tax=taxr, mi_share=mish,
                 ocap=wc["ocap"], oclp=wc["oclp"],
                 dso=wc["dso"], dio=wc["dio"], dpo=wc["dpo"], deprate=round(deprate, 4),
                 amortrate=round(amortrate, 5), cx_intan=cx_intan, imp_ppe=imp_ppe,
                 other_op=round(other_op, 5),
                 payout=.40, mipay=.30, mincash=round(max(o["cash"]*0.6, rev[last]*0.02), 1),
                 sweep=round(rev[last]*0.06, 1)),
        xcheck=D.cross_check(st, em, ann),
        da_src=_src, cx_intan=cx_intan, kd_src=kd_src, clamps=list(_CLAMPS), da_detail=[(ann[i+1], _dep[i], _amo[i]) for i in range(len(_dep))],
        judg=dict(growth=growth, gm=gm, capex=capex, fa_turn=round(fa_turn, 4), maint_rate=round(maint_rate, 5),
                  g_hist=[round(x,4) for x in g_hist], gm_hist=[round(x,4) for x in gm_hist],
                  g_stats=(round(float(np.mean(g_hist)),4), round(float(np.std(g_hist)),4), round(min(g_hist),4), round(max(g_hist),4)),
                  gm_stats=(round(gm_mean,4), round(gm_std_ann,4), round(min(gm_hist),4), round(max(gm_hist),4)),
                  cagr3=round(cagr3,4), mish_hist=mish_hist, wc_interim=wc.get("interim_note"),
                  other_op_hist=[round((_other_op(p) or 0)/rev[p],5) for p in a3 if rev.get(p)]),
        # 融资/股本变动模块：默认全部关停（增发股数=0 → 整块对估值无影响）。
        # 发行价默认取现价：定增实务上多为定价基准日前20日均价×80%，需用户按方案改。
        fin=dict(new_shares=0.0, issue_price=round(float(q["price"]), 2), fee=.015,
                 year=1, use_capex=0.0, use_debt=0.0, build_years=min(3, nyears),
                 disc_years=0.0, buyback_rate=0.0, buyback_price=round(float(q["price"]),2)),
        dcf=dict(rf=rf, erp=.060, beta=round(beta,4), kd=kd,
                 wd=_wd, g=g_term, t_term=.15, midyear=1, roll=roll,
                 nonop=round(_v0(bal,"长期股权投资",last)+_v0(bal,"投资性房地产",last)+_v0(bal,"其他非流动金融资产",last), 4),
                 mi_mode=2),
        rev=rev, cogs=cogs)
