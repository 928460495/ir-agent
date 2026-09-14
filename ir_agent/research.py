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
from pathlib import Path


# 第一阶段写出的待核实清单文件名。**单一定义**——full.py 从这里
# 导入，不要在两处各写一遍：next_actions 要按这个文件在不在决定
# 教人重跑哪一阶段，写岔了就会教人去覆盖已核实的判断项。
ASSUMPTIONS_MD = "assumptions.md"


class Stage(str, Enum):
    AWAITING_REVIEW = "awaiting_review"      # 假设待定（模型未能给出合法假设）
    NOT_APPLICABLE = "not_applicable"        # 路由判定不适用 DCF
    # 这一轮没跑完（模型额度、网络、接口失败……），与「不适用 DCF」
    # **不是一回事**：前者重跑就能好，后者重跑多少次都一样。实测踩到：
    # 额度用尽那轮借用 NOT_APPLICABLE，终端打出「本标的不适用 DCF」
    # —— 对长电科技来说这是一句假话。状态是给人看的结论，不能为了省一个
    # 枚举值就说错。
    INCOMPLETE = "incomplete"
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
        elif self.stage is Stage.INCOMPLETE:
            lines.append("  ⚠ 估值未建模 —— 本轮分析未跑完（原因见下方"
                         "清单），重跑即可")
        for s in self.skipped:
            lines.append(f"  {_mark(s)}{s}")
        return "\n".join(lines)


# 产物清单里混着三种东西，标成同一个词会误导读者。实测: 健全性警告
# 「终值占 EV 78%（>75%）」被标成「未生成」—— 读者会去找什么东西没生成，
# 而那分明是一条算出来了、且必须被看见的警告。
_PREFIX = (
    ("估值健全性：", "  ⚠ "),
    ("估值口径与账本不一致：", "  ⚠ "),
    ("判断项经人工调整：", "  ✎ "),
    # 审计未通过是"算出来了、必须被看见"的一条，不是"没生成"。实跑
    # （600584 第二阶段）里它被打成「– 未生成：完整研报审计未通过：…」，
    # 与上面那条注释记的健全性警告是同一种误导。
    ("审计未通过", "  ⚠ "),
    # 首年被锚定覆盖：批准值没进模型，必须被看见，也不是「未生成」。
    ("判断项被锚定覆盖：", "  ⚠ "),
)


def _mark(s: str) -> str:
    for key, mark in _PREFIX:
        if s.startswith(key):
            return mark
    return "  – 未生成："


def next_actions(stage: Stage, code: str,
                 assumptions_path: str | None = None,
                 out_dir: str | None = None,
                 year: int | None = None) -> list[str]:
    """下一步该做什么。空列表 = 无阻塞。

    命令必须**能直接粘贴运行** —— 给一条跑不通的命令比不给指引更糟。
    实测踩到: 估值模块换成 ashare-dcf、两阶段重构之后，这里仍在教用户跑
    已被删除的 --review / --assumptions，用户照做只会撞上
    「unrecognized arguments」。**过时的指引是会主动误导人的那种错。**
    """
    out = out_dir or f"out/{code}"
    y = f"--year {year}" if year else "--year <年度>"
    if stage is Stage.AWAITING_REVIEW:
        return [
            f"核实 DCF 判断项（营收增速与毛利率三档）：{out}/assumptions.md\n"
            "     这两项模型推不出来，只能靠分析判断 —— 其余假设"
            "（费用率、周转天数、折旧率、税率、β、Kd）由历史数据校准，"
            "不需要你确认。",
            f"要改数值就改 {out}/assumptions.json 的 rows，"
            "改完仍会重新校验（三档须严格拉开、每年都要有值）。",
            "确认后进入第二阶段 —— 建模并产出完整研报：\n"
            f"     python -m ir_agent {code} {y} --value {out} "
            f"--approved-by <你的名字>",
        ]
    if stage in (Stage.NOT_APPLICABLE, Stage.INCOMPLETE):
        # **重跑哪一阶段，看目录里到底有什么。** INCOMPLETE 两个阶段都
        # 可能产生，而命令完全不同：第二阶段失败时教人重跑 --analyse 是
        # 有害的指引——那会重做第一阶段、把人已经核实过的判断项覆盖掉。
        # 判断项还在，就说明该接着跑第二阶段。
        if (Path(out) / ASSUMPTIONS_MD).exists():
            return [
                "判断项已核实但本轮未跑完（模型额度、网络等）——"
                "**不要重跑第一阶段**，那会覆盖掉已核实的判断项。"
                f"接着跑第二阶段即可：\n"
                f"     python -m ir_agent {code} {y} --value {out} "
                f"--approved-by <你的名字>",
            ]
        return [
            "本轮未产出估值。若因分析步骤失败（模型额度、网络等），"
            f"重跑第一阶段即可：\n"
            f"     python -m ir_agent {code} {y} --analyse {out}",
        ]
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
