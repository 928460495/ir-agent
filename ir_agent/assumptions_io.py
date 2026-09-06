"""读取人工填好的 assumptions.yaml。

解析层的职责是**把填写错误变成清楚的报错**，而不是让它悄悄穿到估值里。
留空、日期格式错、依据类型不认识 —— 每一种都指名道姓地说出哪一项有问题。

**未填完的模板必须报错而非报「已审核」**：审核门的价值全在这里。
读文件也不等于审核通过 —— 审核是带署名的独立动作。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from ir_agent.valuation.assumptions import Assumptions
from ir_agent.valuation.basis import Basis, InvalidBasis


class AssumptionFileError(Exception):
    """假设文件填写有误 —— 指明是哪一项。"""


def _dec(value, what: str) -> Decimal:
    if value is None or value == "":
        raise AssumptionFileError(f"{what} 未填写。")
    try:
        # 经 str() 再进 Decimal —— YAML 把 0.09 解析成 float，
        # 直接转会把浮点误差带进估值。
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as e:
        raise AssumptionFileError(f"{what} 不是合法数字：{value!r}") from e


def _date(value, what: str) -> date:
    if not value:
        raise AssumptionFileError(f"{what} 缺少抓取日期。")
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as e:
        raise AssumptionFileError(
            f"{what} 的日期格式应为 YYYY-MM-DD，得到 {value!r}") from e


def _basis(raw, what: str) -> list[Basis]:
    if not raw:
        raise AssumptionFileError(f"{what} 未填写依据。")
    out: list[Basis] = []
    for i, item in enumerate(raw, 1):
        kind = str((item or {}).get("kind", "")).strip()
        tag = f"{what} 第 {i} 条依据"
        try:
            if kind == "external":
                out.append(Basis.external(
                    str(item.get("url", "")),
                    str(item.get("quote", "")),
                    _date(item.get("retrieved"), tag)))
            elif kind == "fact":
                out.append(Basis.fact(str(item.get("ref", ""))))
            elif kind == "report":
                out.append(Basis.report(str(item.get("quote", ""))))
            else:
                raise AssumptionFileError(
                    f"{tag} 的 kind 为 {kind!r}，只支持 fact / report / external。")
        except InvalidBasis as e:
            raise AssumptionFileError(f"{tag}：{e}") from e
    return out


def load_assumptions(path: Path | str) -> Assumptions:
    p = Path(path)
    if not p.exists():
        raise AssumptionFileError(f"找不到假设文件：{p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise AssumptionFileError(f"YAML 解析失败：{str(e)[:120]}") from e

    wacc = _dec(data.get("wacc"), "wacc（折现率）")
    tg = _dec(data.get("terminal_growth"), "terminal_growth（永续增长率）")

    rows = data.get("growth_rates") or []
    if not rows:
        raise AssumptionFileError("growth_rates（预测期增速）未填写。")
    rates: list[tuple[int, Decimal]] = []
    for row in rows:
        year = int((row or {}).get("year", 0))
        rates.append((year, _dec((row or {}).get("rate"),
                                 f"growth_rates 第 {year} 年增速")))
    rates.sort(key=lambda x: x[0])

    return Assumptions(
        wacc=wacc, terminal_growth=tg,
        growth_rates=[r for _, r in rates],
        basis={
            "wacc": _basis(data.get("wacc_basis"), "wacc"),
            "terminal_growth": _basis(data.get("terminal_growth_basis"),
                                      "terminal_growth"),
            "growth_rates": _basis(data.get("growth_rates_basis"),
                                   "growth_rates"),
        })
