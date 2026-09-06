"""估值方法路由 —— DCF 不是默认路径。

30 家实测样本里相当一部分不适用 DCF:

  · 银行 / 保险 / 证券 —— 资本本身是经营资源，没有传统意义的自由现金流
  · 强周期 —— 现金流随周期剧烈波动，永续增长假设失去意义
  · 当期亏损 —— FCF 为负，模型退化成对终值的猜测

**强周期用盈利波动性判定，不查行业表。** 免费源拿不到可靠的行业分类，
而多期盈利数据本来就有 —— 变异系数与盈亏翻转次数比行业标签更有依据，
也更诚实: 同一行业里穿越周期的公司和不穿越的公司本就该分开对待。

判不了周期性时返回 UNDETERMINED 而非 DCF —— **缺证据不等于证据支持**。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ir_agent.sources.eastmoney import CompanyType

MIN_PERIODS = 4                       # 少于 4 期判不了周期性
CYCLIC_THRESHOLD = Decimal("0.45")    # 超过此值视为强周期


class ValuationMethod(str, Enum):
    DCF = "dcf"
    PB_ROE = "pb_roe"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class Route:
    method: ValuationMethod
    reason: str                       # 要写进研报的一句话
    cyclicality: Decimal | None = None

    def describe(self) -> str:
        tail = f"（周期性 {self.cyclicality:.2f}）" if self.cyclicality is not None else ""
        return f"{self.method.value}：{self.reason}{tail}"


def cyclicality(net_profit_history: list[Decimal]) -> Decimal | None:
    """0–1 的周期性分数。数据不足或全为零时返回 None。

    两个分量:
      · 变异系数 —— 盈利围绕均值的摆幅
      · 盈亏翻转次数 —— 由盈转亏是比单纯波动更强的周期信号
    """
    vals = [v for v in net_profit_history]
    if len(vals) < MIN_PERIODS:
        return None
    if all(v == 0 for v in vals):
        return None

    mean = sum(vals) / len(vals)
    scale = abs(mean) if mean != 0 else max(abs(v) for v in vals)
    if scale == 0:
        return None

    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    cv = var.sqrt() / scale

    flips = sum(1 for a, b in zip(vals, vals[1:])
                if (a < 0) != (b < 0))
    flip_score = Decimal(flips) / Decimal(len(vals) - 1)

    # 权重取 0.8 / 0.4 而非 0.6 / 0.4: 前者会让**纯振幅型**周期股
    # （盈利摆动十倍但从不亏损）分数上限被压在 0.6 以下，与事实不符。
    # 两项之和可超过 1，由 min 收口 —— 既亏损又剧烈波动本就该顶格。
    score = cv * Decimal("0.8") + flip_score * Decimal("0.4")
    return min(Decimal(1), score)


def level_shift(net_profit_history: list[Decimal]) -> Decimal | None:
    """后三期均值 ÷ 前三期均值。远离 1 表示**水平位移**而非围绕均值波动。

    用来区分高波动的两种来源: 比亚迪十年盈利 55→338、后三年均值是前三年的
    7.6 倍，属增长跃迁；温氏在均值附近反复盈亏，属真周期。两者都不适用 DCF，
    但写进研报的理由必须不同。
    """
    vals = net_profit_history
    if len(vals) < 6:
        return None
    head = sum(vals[:3]) / 3
    tail = sum(vals[-3:]) / 3
    if head == 0:
        return None
    ratio = tail / head
    return ratio if ratio > 0 else None


def annual_series(facts, key: str) -> list[Decimal]:
    """从事实列表中抽出某 key 的**年度**序列，按年份从旧到新。

    只取 FY 期间: 中期数据混进来会把季节性当成周期性。
    """
    rows = {f.period: f.value for f in facts
            if f.key == key and f.period.endswith("FY")}
    return [rows[p] for p in sorted(rows)]


_FINANCIAL = {CompanyType.BANK, CompanyType.INSURANCE, CompanyType.SECURITIES}


def route(
    company_type: CompanyType,
    net_profit_history: list[Decimal],
    current_net_profit: Decimal,
) -> Route:
    score = cyclicality(net_profit_history)

    if company_type in _FINANCIAL:
        return Route(
            ValuationMethod.PB_ROE,
            "金融业的资本本身是经营资源，没有传统意义的自由现金流，"
            "DCF 不适用；采用 PB–ROE 框架",
            score)

    if current_net_profit < 0:
        return Route(
            ValuationMethod.PB_ROE,
            "当期亏损，自由现金流为负会使 DCF 退化为对终值的猜测；"
            "改用 PB–ROE 与情景法",
            score)

    if score is None:
        return Route(
            ValuationMethod.UNDETERMINED,
            f"可用盈利期数不足 {MIN_PERIODS} 期，无法判定周期属性 —— "
            "在补足历史前不认定 DCF 适用",
            None)

    if score >= CYCLIC_THRESHOLD:
        shift = level_shift(net_profit_history)
        if shift is not None and (shift > 3 or shift < Decimal("0.33")):
            return Route(
                ValuationMethod.PB_ROE,
                "历史盈利经历量级跃升，当前基数不足以支撑永续增长假设；"
                "改用 PB–ROE 并以可比公司交叉验证",
                score)
        return Route(
            ValuationMethod.PB_ROE,
            "历史盈利围绕中枢大幅摆动、具备强周期特征，永续增长假设失去意义；"
            "改用 PB–ROE 与跨周期盈利中枢",
            score)

    return Route(
        ValuationMethod.DCF,
        "非金融业、当期盈利为正、历史盈利波动可控，适用 DCF；"
        "建议同时以可比公司交叉验证",
        score)
