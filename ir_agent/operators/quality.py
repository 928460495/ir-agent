"""盈余质量与违约风险 —— 报表说的是不是真的。

FINC6600 用一整节讲这件事，CFA 教材给了三个提问:

  1. 经营现金流能否覆盖资本开支？
  2. 经营现金流高于还是低于净利润？
  3. 经营现金流的**一致性**如何？

第三问最容易被漏掉。前两问看的是**水平**，第三问看的是**波动** ——
课程明确说「经营现金流的波动性本身就与业务的风险相关，并相应影响其估值」。

项目原有的三表勾稽只验证恒等式（利润表净利润 = 现金流量表起点），
那是**账对不对**；这里问的是**账好不好**，两件事。

**应计异象**（accrual anomaly）: 应计项占比高的公司未来回报较差，
有学术支撑。应计率 = (净利润 − 经营现金流) / 总资产 —— 净利润远高于
经营现金流，意味着利润里现金含量低。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger, LookAheadError
from ir_agent.operators.base import derive
from ir_agent.sources.eastmoney import CompanyType

# 应计率超过此值即提示 —— 经验阈值，不是硬判据
ACCRUAL_WARN = Decimal("0.10")
# 经营现金流/净利润 低于此值即提示利润现金含量不足
CASH_CONVERSION_WARN = Decimal("0.7")


def _get(ledger: FactLedger, key: str, period: str, as_of: date) -> Fact | None:
    try:
        return ledger.get(key, period, as_of=as_of)
    except (KeyError, LookAheadError):
        return None


@dataclass
class QualityReport:
    period: str
    facts: dict[str, Fact]
    flags: list[str]
    skipped: list[str]

    def summary(self) -> str:
        from ir_agent.citation import format_value

        labels = {
            "accrual_ratio": "应计率",
            "cash_conversion": "利润现金含量",
            "capex_coverage": "资本开支覆盖倍数",
            "ocf_volatility": "经营现金流波动",
            "altman_z": "Altman Z",
        }
        lines = [f"盈余质量 {self.period}"]
        for k, f in self.facts.items():
            lines.append(f"  {labels.get(k, k)}　{format_value(f)}")
        for w in self.flags:
            lines.append(f"  ⚠ {w}")
        for s in self.skipped:
            lines.append(f"  – 未计算：{s}")
        return "\n".join(lines)


def compute(ledger: FactLedger, period: str, as_of: date,
            company_type: CompanyType = CompanyType.GENERAL,
            history: list[str] | None = None,
            spot: str | None = None) -> QualityReport:
    """spot 是市值所在的即期期间 —— 市值挂在报价日上，不在会计期。
    不传就取不到，Altman Z 的 D 项会误报为缺数据。"""
    facts: dict[str, Fact] = {}
    flags: list[str] = []
    skipped: list[str] = []

    np_ = _get(ledger, "net_profit", period, as_of)
    ocf = _get(ledger, "cf_operating", period, as_of)
    ta = _get(ledger, "total_assets", period, as_of)
    capex = _get(ledger, "capex", period, as_of)

    # ── 应计率 ────────────────────────────────────────────
    if np_ and ocf and ta and ta.value != 0:
        v = (np_.value - ocf.value) / ta.value
        facts["accrual_ratio"] = derive(ledger, "accrual_ratio", v, "ratio",
                                        period, [np_, ocf, ta])
        if v > ACCRUAL_WARN:
            flags.append(
                f"应计率 {v:.1%} 偏高 —— 净利润显著高于经营现金流，"
                "利润的现金含量不足，需核对收入确认与应收账款")
    else:
        skipped.append("应计率（缺净利润/经营现金流/总资产）")

    # ── 利润的现金含量 ────────────────────────────────────
    if np_ and ocf:
        if np_.value <= 0:
            skipped.append("利润现金含量（净利润非正，该比值无意义）")
        else:
            v = ocf.value / np_.value
            facts["cash_conversion"] = derive(ledger, "cash_conversion", v, "x",
                                              period, [ocf, np_])
            if v < CASH_CONVERSION_WARN:
                flags.append(
                    f"经营现金流仅为净利润的 {v:.0%} —— 有利润但现金没进来")
    else:
        skipped.append("利润现金含量（缺净利润或经营现金流）")

    # ── 资本开支覆盖 ──────────────────────────────────────
    if ocf and capex:
        c = abs(capex.value)
        if c == 0:
            skipped.append("资本开支覆盖（当期无资本开支）")
        else:
            v = ocf.value / c
            facts["capex_coverage"] = derive(ledger, "capex_coverage", v, "x",
                                             period, [ocf, capex])
            if v < 1:
                flags.append(
                    f"经营现金流不足以覆盖资本开支（{v:.2f}x）—— 扩张依赖外部融资")
    else:
        skipped.append("资本开支覆盖（缺经营现金流或资本开支）")

    # ── 经营现金流的一致性 ────────────────────────────────
    # 课程三问里的第三问。看的是波动而非水平，必须要有多期。
    if history and len(history) >= 3:
        ratios = []
        inputs = []
        for p in history:
            o, r = _get(ledger, "cf_operating", p, as_of), \
                _get(ledger, "revenue_total", p, as_of)
            if o and r and r.value != 0:
                ratios.append(o.value / r.value)
                inputs.extend([o, r])
        if len(ratios) >= 3:
            mean = sum(ratios) / len(ratios)
            var = sum((x - mean) ** 2 for x in ratios) / len(ratios)
            facts["ocf_volatility"] = derive(
                ledger, "ocf_volatility", var.sqrt(), "ratio", period, inputs)
        else:
            skipped.append("经营现金流波动（可用期数不足三期）")
    else:
        skipped.append("经营现金流波动（未提供多期区间或不足三期）")

    # ── Altman Z ──────────────────────────────────────────
    z, why = altman_z(ledger, period, as_of, company_type, spot=spot)
    if z is not None:
        facts["altman_z"] = z
        if z.value < Decimal("1.81"):
            flags.append(f"Altman Z {z.value:.2f} 落在困境区（< 1.81）")
        elif z.value < Decimal("2.99"):
            flags.append(f"Altman Z {z.value:.2f} 落在灰区（1.81–2.99）")
    elif why:
        skipped.append(f"Altman Z（{why}）")

    return QualityReport(period=period, facts=facts, flags=flags,
                         skipped=skipped)


def altman_z(ledger: FactLedger, period: str, as_of: date,
             company_type: CompanyType = CompanyType.GENERAL,
             spot: str | None = None) -> tuple[Fact | None, str]:
    """标准 Altman Z（上市制造业口径）。

        Z = 1.2·A + 1.4·B + 3.3·C + 0.6·D + 1.0·E
        A = 营运资本 / 总资产      B = 留存收益 / 总资产
        C = EBIT / 总资产          D = 股权市值 / 总负债
        E = 销售额 / 总资产

    **只实现变量定义完整的标准版。** 讲义另给了中国版
    （Hu, Shi, Wang & Yu, 2020, JBF）:

        Z_China = 0.517 − 0.460·X1 + 9.320·X2 + 0.388·X3 + 1.158·X4

    但该页**只有系数没有 X1–X4 的定义**（幻灯片上仅公式与论文的两张图），
    检索亦未找到。凭系数猜变量等于编一个模型，因此不实现，留待查证原论文。

    A 项需要流动资产与流动负债 —— 金融企业不区分流动性，该模型不适用。
    """
    if company_type is not CompanyType.GENERAL:
        return None, "金融企业的资产负债表不区分流动性，A 项无定义，该模型不适用"

    need = {
        "ca": "current_assets", "cl": "current_liabilities",
        "ta": "total_assets", "re": "retained_earnings",
        "op": "operating_profit", "tl": "total_liabilities",
        "rev": "revenue_total",
    }
    got = {k: _get(ledger, v, period, as_of) for k, v in need.items()}
    mc = _get(ledger, "market_cap", spot or period, as_of)
    missing = [v for k, v in need.items() if got[k] is None]
    if mc is None:
        missing.append("market_cap")
    if missing:
        return None, "缺 " + "、".join(missing)

    ta, tl = got["ta"].value, got["tl"].value
    if ta == 0 or tl == 0:
        return None, "总资产或总负债为 0，该模型无定义"

    from ir_agent.operators.aggregates import ebit as _ebit

    e = _ebit(ledger, period, as_of)      # 与 DOL/ROIC 同一口径
    if e is None:
        return None, "无法计算 EBIT"

    a = (got["ca"].value - got["cl"].value) / ta
    b = got["re"].value / ta
    c = e / ta
    d = mc.value / tl
    e = got["rev"].value / ta
    z = (Decimal("1.2") * a + Decimal("1.4") * b + Decimal("3.3") * c
         + Decimal("0.6") * d + Decimal("1.0") * e)

    inputs = [f for f in got.values() if f is not None] + [mc]
    return derive(ledger, "altman_z", z, "x", period, inputs), ""
