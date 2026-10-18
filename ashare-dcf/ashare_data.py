# -*- coding: utf-8 -*-
"""A股 DCF 模板 — 数据层。只负责取数与归一化，不含业务判断。"""
from __future__ import annotations
import warnings, json, urllib.request, urllib.error, datetime as dt
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd

YI = 1e8  # 亿

class DataError(RuntimeError): pass

# 金融企业特征科目——命中即判定为金融企业
# 银行/保险核心业务科目（包含式匹配——各家披露口径不同，如「发放贷款及垫款」vs
# 「发放贷款及垫款净额」、「吸收存款及同业存放」vs「吸收存款」）。
# 刻意排除「拆出资金」「客户资金存款」「存出保证金」：实业公司的财务公司子公司
# 同样会出现这些科目（如贵州茅台），但主业并非金融。
FIN_BS_MARKERS = ["发放贷款及垫款", "吸收存款", "保户储金", "向中央银行借款"]
FIN_IS_MARKERS = ["已赚保费", "赔付支出", "提取保险责任准备金", "手续费及佣金净收入"]

def _mk(code: str) -> str:
    """交易所前缀：6/9 开头沪市，其余深市/北交所"""
    return "sh" if code[0] in "69" else ("bj" if code[0] == "8" or code[:2] == "43" else "sz")

def _secid(code: str) -> str:
    return ("1." if _mk(code) == "sh" else "0.") + code

def validate_code(code: str) -> str:
    code = str(code).strip().upper()
    for suf in (".SH", ".SZ", ".BJ", ".SS"):
        if code.endswith(suf): code = code[:-3]
    if not (code.isdigit() and len(code) == 6):
        raise DataError(f"「{code}」不是 6 位 A 股代码。本模板仅支持沪深 A 股（港股/美股请用其他工具）。")
    return code

# ---------- 财务报表 ----------
def fetch_statements(code: str) -> dict[str, pd.DataFrame]:
    import akshare as ak
    sym = _mk(code) + code
    out = {}
    import time
    for key, cn in [("income", "利润表"), ("balance", "资产负债表"), ("cashflow", "现金流量表")]:
        d = None; last = None
        for attempt in range(3):       # 新浪接口偶发 ChunkedEncodingError，重试即可
            try:
                d = ak.stock_financial_report_sina(stock=sym, symbol=cn); break
            except Exception as e:
                last = e; time.sleep(1.5 * (attempt + 1))
        if d is None:
            raise DataError(f"取 {cn} 失败（新浪接口，已重试3次）：{type(last).__name__}: {str(last)[:120]}")
        if d is None or d.empty:
            raise DataError(f"{cn} 返回为空，可能是新上市或代码有误：{code}")
        d["报告日"] = d["报告日"].astype(str)
        out[key] = d.set_index("报告日")
    return out

def _cols_matching(df: pd.DataFrame, keys: list[str]) -> list[str]:
    return [c for c in df.columns if any(k in str(c) for k in keys)]

def assert_modelable(inc: pd.DataFrame, annuals: list[str]) -> None:
    """本模板以毛利率驱动。无「营业成本」的报表（保险等）不适用。"""
    have = sum(1 for p in annuals if val(inc, "营业成本", p) is not None)
    if have < len(annuals) - 1:
        raise DataError(
            "利润表缺少「营业成本」科目（近5年仅 %d 年有值）。\n"
            "本模板以毛利率驱动三表，保险等以「已赚保费/赔付支出」列报的主体不适用，"
            "请改用行业专用模型。" % have)

def assert_non_financial(bal: pd.DataFrame, last: str, inc: pd.DataFrame | None = None,
                         threshold: float = 0.15) -> None:
    """按【占总资产比重】判定，而非科目是否存在——很多实业公司有财务公司子公司，
    报表里同样出现「发放贷款及垫款」，但主业并非金融。"""
    if inc is not None:
        ins = {c: val(inc, c, last) for c in _cols_matching(inc, FIN_IS_MARKERS)}
        ins = {k: v for k, v in ins.items() if v}
        rev = val(inc, "营业总收入", last) or val(inc, "营业收入", last)
        if ins and rev and max(ins.values()) / rev > 0.20:
            top = sorted(ins.items(), key=lambda x: -x[1])[:3]
            raise DataError(
                "利润表出现金融业务科目：" + "、".join(f"{k} {v:.0f}亿" for k, v in top) +
                "。\n银行/保险/券商的自由现金流不可定义，DCF 不适用——请改用股利折现（DDM）、内含价值（EV）或剩余收益模型。")
    ta = val(bal, "资产总计", last)
    if not ta: return
    parts = {}
    for m in _cols_matching(bal, FIN_BS_MARKERS):
        v = val(bal, m, last)
        if v: parts[m] = v
    ratio = sum(parts.values()) / ta
    if ratio > threshold:
        top = sorted(parts.items(), key=lambda x: -x[1])[:3]
        raise DataError(
            f"金融资产占总资产 {ratio*100:.0f}%（阈值 {threshold*100:.0f}%），判定为金融企业：" +
            "、".join(f"{k} {v:.0f}亿" for k, v in top) +
            "。\n银行/保险/券商的自由现金流不可定义，DCF 不适用——请改用股利折现（DDM）或超额收益模型。")

def annual_periods(inc: pd.DataFrame, n: int = 5) -> list[str]:
    ann = sorted([p for p in inc.index if p.endswith("1231")], reverse=True)
    if len(ann) < n:
        raise DataError(f"仅有 {len(ann)} 个完整年度数据（需要 {n} 年）。上市时间可能不足。")
    return list(reversed(ann[:n]))

def latest_interim(inc: pd.DataFrame, annuals: list[str]) -> str | None:
    later = sorted([p for p in inc.index if p > annuals[-1]], reverse=True)
    return later[0] if later else None

def val(df: pd.DataFrame, item: str, period: str) -> float | None:
    if item not in df.columns or period not in df.index: return None
    v = df.loc[period, item]
    if isinstance(v, pd.Series): v = v.iloc[0]
    try: v = float(v)
    except (TypeError, ValueError): return None
    return None if pd.isna(v) else v / YI

# ---------- 行情 ----------
def _http(url: str, referer: str | None = None, timeout: int = 12) -> bytes:
    h = {"User-Agent": "Mozilla/5.0"}
    if referer: h["Referer"] = referer
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout).read()

def _opt(v, scale=1.0):
    """东财字段的缺值兜底：None/"-"/空 给 NaN，不给 0（理由见 `_num`）。"""
    if v is None or v == "" or v == "-":
        return np.nan
    try:
        return float(v) * scale
    except (TypeError, ValueError):
        return np.nan


def _num(parts, i, scale=1.0):
    """取第 i 个字段并乘以 scale；缺字段或空串给 NaN，不给 0。

    原先写的是 `(d.get(k) or 0)` 式的 0 兜底——0 会一路算下去（除零、
    或者算出一个「看起来只是很小」的数），NaN 会在工作簿的 NaN 检测那里
    被吼出来。**兜底值该选最弱的那个断言。**
    """
    if i >= len(parts) or not parts[i]:
        return np.nan
    try:
        return float(parts[i]) * scale
    except ValueError:
        return np.nan


def _parse_tencent(text: str) -> dict:
    """解析腾讯 qt.gtimg.cn 的 `~` 分隔行情串。

    **下标必须逐个对过账，不能照抄。** 实测事故（生益科技 600183）: 这里
    原先把 `p[38]` 当成总股本读走，而它是**换手率**——

        p[3]  = 129.96        现价
        p[38] = 1.47          换手率（%），被读成总股本后又 /1e4
        p[45] = 3156.73       总市值（亿元），被又除了一次 1e4
        p[72] = 2411748857    流通股（股）
        p[73] = 2429003670    **总股本（股）**

    于是总股本成了 0.000147 亿股，每股内在价值算出 2507 万元（现价
    129.96）。校验这种错的办法不是把下标再核对一遍（下一次字段漂移照样
    中招），而是 `_check_quote` 那条对账。
    """
    p = text.split('="')[1].split("~")
    return dict(source="腾讯", name=p[1], price=_num(p, 3),
                shares=_num(p, 73, 1 / YI),      # 总股本（股）→ 亿股
                mktcap=_num(p, 45),              # 总市值，已是亿元
                pe=_num(p, 39), pb=_num(p, 46))


# 现价 × 总股本 与总市值的允许偏差。行情源的总市值可能按另一时点的价格
# 算，留 2% 的余量；而字段漂移或单位错位造成的偏差都是**成百上千倍**，
# 两者之间有好几个数量级的空隙，阈值怎么取都不敏感。
QUOTE_GAP_TOL = 0.02


def _check_quote(d: dict) -> dict:
    """对账：现价 × 总股本 必须等于同一条响应里的总市值。

    **这条对账是免费的** —— 两个数来自同一次请求，而它们的乘积关系是
    定义式，不是经验规律。下标错位、单位错位、字段漂移都会被它抓住，
    而逐个核对下标只能抓住今天这一次。

    生益科技那次事故里，下游每一道闸门都放行了: 三表勾稽通过（股本不参与
    资产=负债+权益）、140 格独立复算通过（复算的是同一套公式，自洽不等于
    正确）、健全性检查只报了终值占比。**所有校验都在模型内部查一致性，
    没有一个拿模型外的事实去校准它** —— 而那个事实就在这条响应里。

    对不上就抛错让调用方降级到下一个通道，而不是挑一个看起来对的数：
    两个数矛盾时我们并不知道哪个对。降到最后拿到 NaN 也比拿到一个
    「看起来只是有点小」的股本好 —— NaN 会被工作簿的 NaN 检测吼出来。

    **已知的误报可能**: A+H 或 A+B 股公司，若行情源的总市值只含 A 股而
    总股本含全部股份，这条会判矛盾并拦下。真遇到了再按实际响应处理，
    不要为了避免假想的误报先把这条放宽 —— 它拦住的那种错是致命的。
    """
    px, sh, mc = d.get("price"), d.get("shares"), d.get("mktcap")
    if any(x is None or (isinstance(x, float) and np.isnan(x)) for x in (px, sh, mc)):
        return d                        # 缺数不是矛盾
    if px <= 0 or sh <= 0 or mc <= 0:
        return d
    gap = abs(px * sh - mc) / mc
    if gap > QUOTE_GAP_TOL:
        raise DataError(
            f"行情自相矛盾: 现价 {px} × 总股本 {sh:.6g} 亿股 = "
            f"{px * sh:.4g} 亿元，而同一条响应里的总市值是 {mc:.4g} 亿元"
            f"（差 {gap * 100:.0f}%）—— 股本或市值的字段/单位读错了，"
            f"此通道的数不可用。")
    return d


def fetch_quote(code: str) -> dict:
    """行情/总股本/PE/PB。push2delay 优先（东财 push2 常被代理拦截），逐级降级。"""
    errs = []
    try:
        F = "f43,f57,f58,f84,f116,f117,f162,f167"
        d = json.loads(_http(f"https://push2delay.eastmoney.com/api/qt/stock/get?secid={_secid(code)}&fields={F}",
                             "https://quote.eastmoney.com/")).get("data")
        if d and d.get("f43"):
            # 这条通道的字段本来是对的（f84 总股本/股、f116 总市值/元），
            # 但对账同样挂上去 —— 它拦的是「将来某次字段漂移」，而不是
            # 「今天这一处写错」。只在出错的那条分支加检查，等于假设其余
            # 分支不会出错。
            return _check_quote(dict(
                source="push2delay", name=d.get("f58"), price=d["f43"]/100,
                shares=_opt(d.get("f84"), 1/YI), mktcap=_opt(d.get("f116"), 1/YI),
                pe=_opt(d.get("f162"), 0.01), pb=_opt(d.get("f167"), 0.01)))
    except Exception as e: errs.append(f"push2delay: {type(e).__name__}")
    try:
        return _check_quote(_parse_tencent(
            _http(f"https://qt.gtimg.cn/q={_mk(code)}{code}").decode("gbk", "replace")))
    except Exception as e: errs.append(f"腾讯: {type(e).__name__}: {e}")
    try:
        t = _http(f"https://hq.sinajs.cn/list={_mk(code)}{code}", "https://finance.sina.com.cn").decode("gbk", "replace")
        p = t.split('="')[1].split(",")
        return dict(source="新浪", name=p[0], price=float(p[3]), shares=np.nan,
                    mktcap=np.nan, pe=np.nan, pb=np.nan)
    except Exception as e: errs.append(f"新浪: {type(e).__name__}")
    raise DataError("三个行情通道全部失败：" + "；".join(errs))

def fetch_prices(code: str, years: int = 6) -> pd.DataFrame:
    import akshare as ak
    d = ak.stock_zh_a_daily(symbol=_mk(code) + code, adjust="qfq")[["date", "close"]]
    d["date"] = pd.to_datetime(d["date"])
    return d.set_index("date").sort_index()

def fetch_index(symbol: str = "sh000001") -> pd.DataFrame:
    import akshare as ak
    d = ak.stock_zh_index_daily(symbol=symbol)[["date", "close"]]
    d["date"] = pd.to_datetime(d["date"])
    return d.set_index("date").sort_index()

def fetch_riskfree() -> tuple[float, str]:
    import akshare as ak
    try:
        start = (dt.date.today() - dt.timedelta(days=45)).strftime("%Y%m%d")
        b = ak.bond_zh_us_rate(start_date=start).dropna(subset=["中国国债收益率10年"]).tail(1)
        return float(b["中国国债收益率10年"].iloc[0]) / 100, str(b["日期"].iloc[0])
    except Exception:
        return 0.01681, "取数失败，使用常量 1.681%（请自行核对）"


# ============================================================
# 第二数据源：东财 datacenter（用于交叉校验 + 折旧摊销明细）
# ============================================================
# 新浪只给【直接法】现金流量表，没有「补充资料」——固定资产折旧与无形资产摊销
# 都在那一节里。所以原来只能从 OCF 倒推 D&A，分子含无形/使用权摊销而分母只有
# 固定资产，口径不严（README 不足 #8）。东财 datacenter 有完整的间接法补充资料。
#
# 注意：被本机代理拦截的是 push2.eastmoney.com / push2his.eastmoney.com，
# datacenter.eastmoney.com 实测可达（39KB / 0.2s），不要一竿子打翻。
EM_BASE = ("https://datacenter.eastmoney.com/securities/api/data/v1/get"
           "?reportName={rn}&columns=ALL&filter=(SECUCODE%3D%22{code}%22)"
           "&pageSize={n}&sortColumns=REPORT_DATE&sortTypes=-1")
EM_REPORTS = {"income": "RPT_F10_FINANCE_GINCOME",
              "balance": "RPT_F10_FINANCE_GBALANCE",
              "cashflow": "RPT_F10_FINANCE_GCASHFLOW"}
EM_HDR = {"User-Agent": "Mozilla/5.0",
          "Referer": "https://emweb.securities.eastmoney.com/"}


def _em_secucode(code: str) -> str:
    return f"{code}.{'SH' if _mk(code) == 'sh' else ('BJ' if _mk(code) == 'bj' else 'SZ')}"


def fetch_eastmoney(code: str, n: int = 24) -> dict:
    """东财 datacenter 三表。返回 {表: {报告期(YYYYMMDD): {字段: 值(亿元)}}}。

    取数失败不抛错 —— 它是【交叉校验源】与【折旧摊销明细源】，
    主链路仍走新浪。失败时降级并在诊断里标注，绝不静默当成"一致"。"""
    out, errs = {}, []
    for key, rn in EM_REPORTS.items():
        try:
            url = EM_BASE.format(rn=rn, code=_em_secucode(code), n=n)
            d = json.loads(_http(url, EM_HDR["Referer"], timeout=20).decode("utf-8", "replace"))
            rows = (d.get("result") or {}).get("data") or []
            # 东财用 HTTP 200 + success:false / code:9201「返回数据为空」表示查无此数据，
            # 不抛异常。只靠 try/except 判失败会把「空」误当成「取到了且没差异」。
            if d.get("success") is False or not rows:
                raise ValueError(f"empty(success={d.get('success')},code={d.get('code')})")
            per = {}
            for r in rows:
                p = str(r.get("REPORT_DATE", ""))[:10].replace("-", "")
                if len(p) != 8:
                    continue
                per[p] = {k: (v / YI if isinstance(v, (int, float)) else v)
                          for k, v in r.items() if not k.endswith("_YOY")}
            out[key] = per
        except Exception as e:
            errs.append(f"{key}: {type(e).__name__}")
            out[key] = {}
    out["_errors"] = errs
    return out


# 折旧与摊销的间接法字段（东财命名）
EM_DEPR = ["FA_IR_DEPR"]                                   # 固定资产折旧、油气折耗、生产性生物资产折旧
EM_AMORT = ["IA_AMORTIZE", "LPE_AMORTIZE", "USERIGHT_ASSET_AMORTIZE"]  # 无形/长期待摊/使用权


def em_depr_amort(em: dict, period: str) -> tuple[float | None, float | None]:
    """返回 (折旧, 摊销)，单位亿元。任一缺失返回 None —— 不填 0。"""
    row = (em.get("cashflow") or {}).get(period)
    if not row:
        return None, None
    dep = row.get("FA_IR_DEPR")
    amo = [row.get(k) for k in EM_AMORT]
    amo = [x for x in amo if isinstance(x, (int, float))]
    return (dep if isinstance(dep, (int, float)) else None,
            sum(amo) if amo else None)


# 新浪科目 ↔ 东财字段 的对照表，用于双源交叉校验
XCHECK_MAP = [
    ("income", "营业总收入", "TOTAL_OPERATE_INCOME"),
    ("income", "营业成本", "OPERATE_COST"),
    ("income", "营业利润", "OPERATE_PROFIT"),
    ("income", "利润总额", "TOTAL_PROFIT"),
    ("income", "净利润", "NETPROFIT"),
    ("income", "归属于母公司所有者的净利润", "PARENT_NETPROFIT"),
    ("income", "研发费用", "RESEARCH_EXPENSE"),
    ("income", "财务费用", "FINANCE_EXPENSE"),
    ("balance", "资产总计", "TOTAL_ASSETS"),
    ("balance", "负债合计", "TOTAL_LIABILITIES"),
    ("balance", "货币资金", "MONETARYFUNDS"),
    ("balance", "存货", "INVENTORY"),
    ("balance", "固定资产净额", "FIXED_ASSET"),
    ("cashflow", "经营活动产生的现金流量净额", "NETCASH_OPERATE"),
    ("cashflow", "购建固定资产、无形资产和其他长期资产所支付的现金", "CONSTRUCT_LONG_ASSET"),
]


def cross_check(sina: dict, em: dict, periods: list[str], tol: float = 0.005,
                min_cov: float = 0.60) -> dict:
    """新浪 ↔ 东财 双源比对。

    这【不是】锦上添花：美股版加上双源校验的当天就抓出一个静默错误
    （MSFT 的 D&A 少算 122 亿美元）。A 股版此前是单一数据源，
    README 不足 #7 说的就是这个。

    三态返回：一致 / 不一致 / 不可用。绝不因为"没发现不一致"就报"一致"。

    「不可用」由【实际比较对数】判定，不由异常判定 —— 曾经的写法要求抛过异常才降级，
    于是东财返回空、字段改名、或三张表里坏掉一张时，比较 0 次仍然报「全部一致」。
    现在：可比对数 ÷ 应比对数 < min_cov 一律 ok=None，并把 n_compared 交给调用方。"""
    rows, n_cmp, n_exp, missing = [], 0, 0, set()
    per_sheet = {sh: [0, 0] for sh, _, _ in XCHECK_MAP}     # 表 -> [已比对, 应比对]
    for sheet, sina_item, em_field in XCHECK_MAP:
        df = sina.get(sheet)
        emp = em.get(sheet) or {}
        if df is None:
            continue
        for p in periods:
            a = val(df, sina_item, p)
            if a is None:
                continue
            n_exp += 1                       # 新浪有值 = 本该被校验
            per_sheet[sheet][1] += 1
            b = (emp.get(p) or {}).get(em_field)
            if not isinstance(b, (int, float)):
                missing.add(f"{sheet}.{em_field}")
                continue
            n_cmp += 1; per_sheet[sheet][0] += 1
            if abs(a) < 1e-6 and abs(b) < 1e-6:
                continue
            den = max(abs(a), abs(b), 0.01)
            if abs(a - b) / den > tol:
                rows.append(dict(期=p, 科目=sina_item, 新浪=round(a, 4),
                                 东财=round(b, 4), 偏差=f"{abs(a-b)/den:.1%}"))
    cov = (n_cmp / n_exp) if n_exp else 0.0
    # 整张表颗粒无收 = 该表完全没被校验。全局覆盖率挡不住它（现金流量表只占 10/75），
    # 而资本开支率正是从现金流量表校准的 —— 必须按表单独设底线。
    blind = [sh for sh, (c_, e_) in per_sheet.items() if e_ > 0 and c_ == 0]
    base = dict(n_compared=n_cmp, n_expected=n_exp, coverage=round(cov, 4),
                per_sheet={k: f"{v[0]}/{v[1]}" for k, v in per_sheet.items()},
                detail=em.get("_errors") or [], missing=sorted(missing)[:8])
    if blind:
        return dict(ok=None, rows=[], **base,
                    note=(f"交叉校验【不可用】：{'、'.join(blind)} 整张表一项都没比对上"
                          f"（其余已比对 {n_cmp}/{n_exp} 项）—— 这不等于「一致」"))
    if n_cmp == 0 or cov < min_cov:
        return dict(ok=None, rows=[], **base,
                    note=(f"交叉校验【不可用】：仅比对了 {n_cmp}/{n_exp} 项"
                          f"（覆盖率 {cov:.0%} < 下限 {min_cov:.0%}）—— "
                          "这不等于「一致」，本次实质上仍是新浪单一来源"))
    if rows:
        return dict(ok=False, rows=rows, **base,
                    note=f"{len(rows)} 处不一致（已比对 {n_cmp}/{n_exp} 项）")
    return dict(ok=True, rows=[], **base,
                note=f"已比对 {n_cmp}/{n_exp} 项，新浪与东财全部一致")
