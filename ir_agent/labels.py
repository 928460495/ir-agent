"""人读文件里用的中文名：事实键、来源、建模驱动因子。查不到就给空串或原样，不硬造。"""

from __future__ import annotations

# 来源前缀 → 中文名，取最长匹配（`eastmoney_report` 不能被 `eastmoney` 先认走）
_SOURCES = {
    "sec_companyfacts": "SEC 财报数据", "sec_xbrl_instance": "SEC 财报附注数据", "sec_": "SEC",
    "eastmoney_report": "东方财富研报", "eastmoney": "东方财富财报", "em_g_": "东方财富财报",
    "em_macro": "东方财富宏观", "em_rates": "东方财富利率", "em_beta": "东方财富行情（β）", "em_": "东方财富",
    "sina": "新浪财经", "tencent": "腾讯行情", "fmp_analyst": "FMP 一致预期", "fmp_": "FMP", "tiingo": "Tiingo 行情",
    "fred_": "美联储 FRED", "calc_": "账本计算", "comps": "同业可比", "industry_agg": "行业聚合",
    "industry_profile": "行业画像", "relval": "相对估值", "ashare_dcf": "三表 DCF", "us_dcf": "三表 DCF",
    "macro_cycle": "周期判断", "usaspending": "美国联邦合同（USAspending）", "corpus_doc": "入库资料",
}


def source_label(source_id: str) -> str:
    hit = max((p for p in _SOURCES if (source_id or "").startswith(p)), key=len, default="")
    return _SOURCES[hit] if hit else (source_id or "")


def _base(key: str) -> str:
    from ir_agent.analyst import LABELS
    if key in LABELS:
        return LABELS[key]
    from ir_agent.metrics import BY_KEY
    return BY_KEY[key].label if key in BY_KEY else ""


def fact_label(key: str) -> str:
    """事实键的中文名；分位、中位、TTM 由基础指标派生。查不到返回空串。"""
    if not key:
        return ""
    if (lab := _base(key)):
        return lab
    for suf, name in ((".percentile", " 同业分位"), (".peer_median", " 同业中位数"), (".yoy", "同比"), ("_ttm", "（TTM）")):
        if key.endswith(suf) and (lab := _base(key[:-len(suf)])):
            return lab + name
    return ""


def driver_label(name: str) -> str:
    from ir_agent.strategy import MAPPABLE_DRIVERS
    return MAPPABLE_DRIVERS.get(name.strip(), name)
