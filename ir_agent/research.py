"""端到端编排 —— 一次跑完，估值假设由模型自主决定。

原设计在人工审核门前停下，理由是无风险利率与永续增长率推不出来。现在:
无风险利率有了逐日的国债收益率曲线，β 由回归自算，只剩风险溢价与永续
增速是判断 —— 这两项改由模型自己拿主意（见 valuation/autodecide.py）。

    数据 → 路由 → 宏观/行业/可比/折现率输入 → 撰写 → 辩论 → 修订
         → 模型自定假设（代码校验）→ 建模 → 看板 / Excel

**取消的是审核门，不是校验。** 永续 ≤ 宏观事实算出的上限、永续 < WACC、
依据必须可核验、WACC 必须能引用正文里的论证原话 —— 这些一条没少。
少掉的是「有人看过并负责」，因此产物一律标注为模型决定，不冒充人工审核。
人工路径仍然保留（--review / --assumptions），只是不再是必经之路。

产物清单必须**显式列出缺什么**。只列已完成的部分，读者会以为报告是完整的 ——
这与 V0 的「缺数据时跳过而非通过」是同一条原则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class Stage(str, Enum):
    AWAITING_REVIEW = "awaiting_review"      # 假设待定（模型未能给出合法假设）
    NOT_APPLICABLE = "not_applicable"        # 路由判定不适用 DCF
    VALUED = "valued"                        # 已建模
    FAILED = "failed"


@dataclass
class ResearchOutcome:
    code: str
    period: str
    as_of: date
    stage: Stage
    artifacts: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    upheld: int = 0
    cost_usd: Decimal = Decimal(0)
    route_reason: str = ""

    @property
    def complete(self) -> bool:
        return self.stage is Stage.VALUED

    def summary(self) -> str:
        lines = [f"{self.code} {self.period} @ {self.as_of.isoformat()}"]
        for name, path in self.artifacts.items():
            lines.append(f"  ✓ {name}：{path}")
        if self.upheld:
            lines.append(f"  · 辩论：{self.upheld} 项挑战成立并已回写正文")
        if self.cost_usd:
            lines.append(f"  · 模型开销：${self.cost_usd:.2f}")
        if self.route_reason:
            lines.append(f"  · 估值方法：{self.route_reason}")
        if self.stage is Stage.AWAITING_REVIEW:
            lines.append("  ⚠ 估值未建模 —— 假设尚未定案，报告尚不完整")
        elif self.stage is Stage.NOT_APPLICABLE:
            lines.append("  ⚠ 估值未建模 —— 本标的不适用 DCF")
        for s in self.skipped:
            lines.append(f"  – 未生成：{s}")
        return "\n".join(lines)


def next_actions(stage: Stage, code: str,
                 assumptions_path: str | None = None,
                 out_dir: str | None = None,
                 year: int | None = None) -> list[str]:
    """下一步该做什么。空列表 = 无阻塞。

    命令必须**能直接粘贴运行** —— 给一条跑不通的命令比不给指引更糟。
    """
    if stage is Stage.AWAITING_REVIEW:
        out = out_dir or f"out/{code}"
        path = assumptions_path or f"{out}/assumptions.yaml"
        y = f"--year {year}" if year else "--year <年度>"
        return [
            "审核估值假设 —— 推荐交互式录入，逐项问答、当场校验，不必手写 YAML：\n"
            f"     python -m ir_agent {code} {y} --full {out} "
            f"--review --reviewer <你的名字>",
            f"或手工编辑 {path} 后：\n"
            f"     python -m ir_agent {code} {y} --full {out} "
            f"--assumptions {path} --reviewer <你的名字>",
            "依据须为三类之一：账本占位符 / 已辩论正文的引文 / "
            "外部来源（URL + 原文片段 + 抓取日期）。",
        ]
    if stage is Stage.NOT_APPLICABLE:
        return ["本标的经路由判定不适用 DCF，应改用 PB–ROE 或情景法；"
                "现有产物中的基本面分析与可比对照仍然有效。"]
    return []


_TEMPLATE = """# 估值假设 —— 每项都必须人工填写并给出可核验的依据
#
# 依据（basis）须为三类之一：
#   fact:     账本占位符，如 [[revenue.yoy@2025FY]]
#   report:   已辩论正文中的原句（必须逐字出现在正文里）
#   external: 外部来源，必须同时有 url、quote、retrieved（抓取日期）
#             —— 外部资料会变，没有抓取日期事后无法判断当时看到的是什么
#
# 留空即无法通过审核。预填数字会让人直接点通过，因此这里一律不预填。

wacc: null            # 待填，例如 0.09
wacc_basis:
  - kind: external
    url: ""
    quote: ""
    retrieved: ""     # YYYY-MM-DD

terminal_growth: null # 待填，不得高于长期名义 GDP 增速
terminal_growth_basis:
  - kind: external
    url: ""
    quote: ""
    retrieved: ""

growth_rates:
{years}
growth_rates_basis:
  - kind: fact
    ref: ""           # 例如 [[revenue.yoy@2025FY]]
"""


def assumption_template(years: int, derived=None) -> str:
    """假设模板。derived 为 valuation.derive.Derived 时附上推导建议值。

    建议值以**注释**形式出现，不落到 value 上 —— 预填数字会让人直接点通过，
    审核就退化成一个回车。注释既给出分析锚点，又保留「必须自己填」这个动作。
    仅用于保留下来的人工路径（--review / --assumptions）；主流程已改为
    模型自主决定，不经过这个模板。
    """
    rows = "\n".join(f"  - year: {i}\n    rate: null" for i in range(1, years + 1))
    body = _TEMPLATE.format(years=rows)
    if derived is None:
        return body
    head = "\n".join("# " + ln for ln in derived.describe().split("\n"))
    return f"{head}\n#\n# 上列为系统据宏观与行业事实推导的建议值，仅供参考。\n#\n{body}"
