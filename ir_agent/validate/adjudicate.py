"""用公告原文裁定两源分歧。

Tier 顺序解决的是「默认信谁」，裁决解决的是「这一次谁对」。
两者不能互相替代 —— 中国神华实测中 Tier-1(东财) 恰恰是错的那个。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

DEFAULT_REL_TOL = Decimal("0.0005")


@dataclass(frozen=True)
class Verdict:
    pdf_value: Decimal
    winner: str | None                  # 与原文一致的源；都不一致时为 None
    losers: list[str]
    rel_diffs: dict[str, Decimal]

    def describe(self) -> str:
        if self.winner is None:
            return (f"原文 {self.pdf_value}：无任何数据源与之一致 —— "
                    f"提取或口径可能有误，需人工复核")
        if not self.losers:
            return "各源均与原文一致"
        gaps = "，".join(f"{s} 偏离 {self.rel_diffs[s]:.2%}" for s in self.losers)
        return f"原文判定 {self.winner} 正确；{gaps}"


def adjudicate(
    pdf_value: Decimal,
    candidates: dict[str, Decimal],
    rel_tol: Decimal = DEFAULT_REL_TOL,
) -> Verdict:
    scale = abs(pdf_value) or Decimal(1)
    diffs = {s: abs(v - pdf_value) / scale for s, v in candidates.items()}
    matches = [s for s, d in diffs.items() if d <= rel_tol]
    winner = min(matches, key=lambda s: diffs[s]) if matches else None
    # 只有**未与原文一致**的源才算输家 —— 两源都对时不该有输家
    losers = sorted(s for s, d in diffs.items() if d > rel_tol)
    return Verdict(pdf_value=pdf_value, winner=winner,
                   losers=losers, rel_diffs=diffs)


def resolve_disagreements(
    facts_by_source: dict[str, list],
    pdf_rows: dict[str, dict[int, Decimal]],
    period: str,
    primary: str | None = None,
    rel_tol: Decimal = DEFAULT_REL_TOL,
) -> tuple[list, dict[str, Verdict]]:
    """用年报原文裁定分歧，返回 (采用的事实, {字段: 裁定})。

    只处理 `period` 这一期、且 PDF 能给出数值的字段；其余一律沿用主源。
    胜出的是**数据源的原始事实**（method 保持 REPORTED），PDF 只当裁判 ——
    这样溯源仍指向接口快照，而裁定过程另行记录。
    """
    sources = list(facts_by_source)
    primary = primary or sources[0]

    by_key: dict[tuple[str, str], dict[str, object]] = {}
    for src, facts in facts_by_source.items():
        for f in facts:
            by_key.setdefault((f.key, f.period), {})[src] = f

    verdicts: dict[str, Verdict] = {}
    chosen: list = []

    for (key, per), per_source in by_key.items():
        # 按年份取值，不按列位置 —— 摘要页的列序与主表相反
        year = int(period[:4]) if per == period and period[:4].isdigit() else None
        pdf_val = (pdf_rows.get(key) or {}).get(year) if year else None
        if pdf_val is not None:
            v = adjudicate(pdf_val,
                           {s: f.value for s, f in per_source.items()},
                           rel_tol=rel_tol)
            verdicts[key] = v
            if v.winner and v.winner in per_source:
                chosen.append(per_source[v.winner])
                continue
        # 裁不出来、或本就不需要裁 —— 沿用主源，主源缺失时取任一可得
        chosen.append(per_source.get(primary) or next(iter(per_source.values())))

    return chosen, verdicts
