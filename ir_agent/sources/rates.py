"""国债收益率曲线 —— WACC 的无风险利率来源。

早前判定「无风险利率不在免费数据源内」是**错的**，只是没找对接口: 中债官网
的 queryGjqxInfo 返回 404，东财 quote 的 TMBMKCN-10Y 代码取不到数，但
datacenter 的 RPTA_WEB_TREASURYYIELD 报表一直都在，且是逐日的。

**列名是不透明的 EM 代码**（EMM00166466 之类），官方没有字段文档。这里不靠
外部资料认领字段，而是用**利差恒等式自证**: 表里同时给了各期限收益率和
10Y-2Y 利差，若映射正确则 `10Y - 2Y == 利差` 必然成立。实测:

    中国  1.6804 - 1.2430 = 0.4374  == EMM01276014 ✓
    美国  4.78   - 4.37   = 0.41    == EMG01339436 ✓

上游一旦改动列编码，这条恒等式立刻不成立，于是报错而不是把某个别的期限
当成十年期用 —— 那种错误不会报警，只会让所有估值一起偏。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import requests

from ir_agent.ledger import Fact, Method
from ir_agent.sources.retry import with_retry

API = "https://datacenter.eastmoney.com/api/data/get"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 20

SOURCE_ID = "em_rates"

# 期限 → 列编码。靠下面的恒等式校验，不靠文档。
CN = {2: "EMM00588704", 5: "EMM00166462", 10: "EMM00166466", 30: "EMM00166469"}
CN_SPREAD = "EMM01276014"          # 10Y - 2Y
US = {2: "EMG00001306", 5: "EMG00001308", 10: "EMG00001310", 30: "EMG00001312"}
US_SPREAD = "EMG01339436"

# 恒等式容差。接口各列的小数位不一致（利差 4 位、美债 2 位），
# 放到 2 个基点足以容纳舍入，又远小于任何一次真实的列错位。
TOL = Decimal("0.02")

RISK_FREE_TENOR = 10               # 十年期是 A 股估值惯用的无风险利率口径


class RatesUnavailable(Exception):
    """收益率曲线取不到，或列映射校验不通过。"""


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _fetch(size: int) -> list[dict]:
    def call():
        r = requests.get(API, params={
            "type": "RPTA_WEB_TREASURYYIELD", "sty": "ALL",
            "st": "SOLAR_DATE", "sr": -1, "p": 1, "ps": size,
        }, headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    try:
        payload = with_retry(call)
    except Exception as e:                                   # noqa: BLE001
        raise RatesUnavailable(f"{e.__class__.__name__}: {e}") from e
    rows = ((payload.get("result") or {}).get("data")) or []
    if not rows:
        raise RatesUnavailable(f"返回空结果（{payload.get('message')}）")
    return rows


def _verify(row: dict) -> None:
    """利差恒等式自证。不通过即拒绝出数 —— 列错位不会报警，只会静默压低/抬高
    所有估值。"""
    for label, cols, spread_col in (("中国", CN, CN_SPREAD), ("美国", US, US_SPREAD)):
        y10, y2, spread = (_dec(row.get(cols[10])), _dec(row.get(cols[2])),
                           _dec(row.get(spread_col)))
        if None in (y10, y2, spread):
            continue                       # 该组当日缺数，交给调用方判断
        if abs((y10 - y2) - spread) > TOL:
            raise RatesUnavailable(
                f"{label}收益率列映射校验失败: 10Y({y10}) - 2Y({y2}) = "
                f"{y10 - y2}，而表中利差为 {spread}。上游列编码可能已变更 —— "
                "拒绝在不确定哪一列是十年期的情况下出具无风险利率。")


def fetch_yields(size: int = 30) -> list[Fact]:
    """中国国债各期限到期收益率。

    period 用观测日（ISO 日期），as_of 同日 —— 这是**市场数据**，当日即可见，
    不存在统计数据那种「统计期 ≠ 发布日」的滞后问题。
    """
    rows = _fetch(size)
    _verify(rows[0])

    facts: list[Fact] = []
    for row in rows:
        raw = str(row.get("SOLAR_DATE") or "")[:10]
        if not raw:
            continue
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            continue
        for tenor, col in CN.items():
            v = _dec(row.get(col))
            if v is None:
                continue
            facts.append(Fact(
                key=f"rate.cgb_{tenor}y", value=v / 100, unit="ratio",
                period=day.isoformat(), as_of=day,
                source_id=SOURCE_ID, method=Method.REPORTED))
    if not facts:
        raise RatesUnavailable("收益率曲线各列均为空")
    return facts


@dataclass(frozen=True)
class RiskFree:
    value: Decimal
    observed: date
    tenor: int = RISK_FREE_TENOR

    @property
    def ref(self) -> str:
        return f"[[rate.cgb_{self.tenor}y@{self.observed.isoformat()}]]"

    def describe(self) -> str:
        return (f"无风险利率（{self.tenor} 年期国债，{self.observed}）"
                f"{self.value * 100:.2f}%")


def risk_free(facts: list[Fact], as_of: date,
              tenor: int = RISK_FREE_TENOR) -> RiskFree | None:
    """时点内可见的最新一期收益率。取不到返回 None，不猜。"""
    key = f"rate.cgb_{tenor}y"
    seen = [f for f in facts if f.key == key and f.as_of <= as_of]
    if not seen:
        return None
    f = max(seen, key=lambda x: x.as_of)
    return RiskFree(value=f.value, observed=f.as_of, tenor=tenor)
