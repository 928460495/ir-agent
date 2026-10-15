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
# 判断项审核（子项目三）：每一轮的完整记录；停下时最后一稿的可改底稿。
ASSUMPTIONS_ROUNDS_MD = "assumptions_rounds.md"
ASSUMPTIONS_FAILED_JSON = "assumptions_failed.json"


class Stage(str, Enum):
    AWAITING_REVIEW = "awaiting_review"      # 假设待定（模型未能给出合法假设）
    AWAITING_SOURCES = "awaiting_sources"              # 停在门①：来源待核验
    AWAITING_BRIEF_REVIEW = "awaiting_brief_review"    # 停在门②：简报待审阅
    AWAITING_ASSUME_REVIEW = "awaiting_assume_review"  # 停在门③：判断项待核实
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
    # 这份结果出自哪一阶段（"analyse" / "value"）。下一步指引靠它决定教人
    # 重跑哪一阶段——**不能从目录里猜**：上一轮残留的 assumptions.md 会把
    # 第一阶段的失败伪装成第二阶段的失败（生益科技重跑实测）。
    phase: str = ""

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
        if self.stage is Stage.AWAITING_SOURCES:
            lines.append("  ⏸ 停在门①：资料来源待核验（估值未建模）")
        elif self.stage is Stage.AWAITING_BRIEF_REVIEW:
            lines.append("  ⏸ 停在门②：简报结论待审阅（估值未建模）")
        elif self.stage in (Stage.AWAITING_REVIEW, Stage.AWAITING_ASSUME_REVIEW):
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
#
# **这张表反复被发现漏登记，每次都是同一个缺陷，不是同一个条目。**
# 缺陷在 `_mark` 的默认分支：它渲染的是最强的一种断言——「未生成」，
# 即这件产物不存在。于是「新增了一类清单条目、忘了登记」不会报错，
# 而是**朝最误导人的方向失败**：一条算出来了的警告、一条采到了之后的
# 注意事项，都被印成「没产出」。下面各条各自是该缺陷「第 N 次被发现」
# 的修复记录，但 N 刻意不给具体数、也不把这些条目当成完整清单：每次
# 发现都是某个新生产者落进默认分支，写下的数字只会在下一次被发现时
# 过期，也不保证此刻没有别的生产者还落在默认分支上。
#
# **根治办法是倒置回退**：默认走中性的 `  · `，真缺口才显式登记为
# 「未生成」。那是一次有取舍的改动（哪些条目算真缺口要逐条裁决），已
# 明确推迟，**目前没有做**。`tests/test_full_context.py` 里对清单渲染的
# 逐生产者断言是针对本分支新增/改动的那几个生产者的防线，不是这个根治
# 办法，不要把它当成已经倒置。
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
    # 判断项**产出之后**的保留意见（依据未逐字核验 / 退回两标量模式）。
    # 实跑（贵州茅台第一阶段）里它被打成「– 未生成：DCF 判断项（5 行依据
    # 未能逐字核验（已标注，**未阻断**…））」，而紧上方同一份清单里写着
    # 「✓ DCF 判断项（待核实）：…/assumptions.md」——两条互相矛盾。
    # 刻意与真缺口分开: `_propose_assumptions` 另有三条返回路径（校准失败 /
    # AssumeFailed / 调用失败）以「DCF 判断项（」开头，那三条确实没产出
    # 判断项，仍落「未生成」。见 `full._assumption_caveat_note`。
    ("判断项待复核：", "  ⚠ "),
    # 判断项审核（子项目三）停下交人工 / 第二阶段读回人改过的判断项：都是「算
    # 出来了或停在那儿、必须被看见」，不是「未生成」。
    ("判断项审核", "  ⚠ "),
    ("判断项未经审核：", "  ⚠ "),
    # 第二阶段读回第一阶段落盘的公司公告：**成功**路径，为读者报告「读回了
    # 几篇、第一阶段候选与挑选各多少」——既不是警告也不是缺了什么产物。
    # 该条原先以「公司公告 N 篇…」起头、不匹配任何前缀，每一次成功的
    # `--value` 都被打成「– 未生成：公司公告 1 篇…」。
    # 用 `  · `（与 `summary()` 里的「估值方法」行同属「给读者看的信息行」）。
    ("公司公告读回：", "  · "),
    # 两条「采到了之后的注意事项清单」，被默认分支打成「未生成」就是在
    # 说没采到——而两者的 docs 都非空。
    # `full._openweb_notes`（两个阶段共用）此前就在 main 上，每一次有抓取
    # 失败的 --value 都在印「– 未生成：开放网络资料：」，下面却紧跟着
    # 「· 1 篇抓取失败」——自相矛盾。
    # 两条的前缀都刻意与各自的真缺口条目分开（「开放网络资料：一篇都没
    # 取到…」「公司公告：一篇都没入库…」仍落「未生成」，那是对的）。
    ("开放网络资料采集：", "  · "),
    ("公司公告采集：", "  · "),
    # `full._factor_host_check_note`：④外部网络分支的主机安全校验降级聚合，
    # 因子**采到了**，这是一条降级警告。同样刻意与真缺口分开：
    # 「因子数据采集（…失败）」是整体没采到，仍落「未生成」。
    ("因子外部资料采集：", "  · "),
    # ── 第一阶段拆三段（spec 2026-10-12）新增的清单生产者 ──
    # **失败的前缀必须排在通用前缀之前**（`_mark` 是 startswith 逐条匹配、
    # 先中先得）。计划原稿只登记了一条 `("多空辩论", "  · ")`，而它自己的
    # 注释写着「『人工补写』是成功路径，『未完成』才是缺口 —— 两者刻意分开
    # 前缀」：照抄的话「多空辩论B 未完成」会以「·」打出来，把一次失败渲染成
    # 一条普通说明。轮次字母（A/B）在前缀里，所以两轮各登记一条。
    ("多空辩论A 未完成", "  ⚠ "),
    ("多空辩论B 未完成", "  ⚠ "),
    ("多空辩论A 已有产物不可用", "  ⚠ "),
    ("多空辩论B 已有产物不可用", "  ⚠ "),
    ("多空辩论", "  · "),          # 人工补写 / 沿用已有产物 —— 成功路径
    # 清单产出了，只是其中一条指向的文件不在盘上。
    ("来源清单：", "  ⚠ "),
    # 预研成功了，但有辩论主题没进分解式 —— 要人在门② 看。注意与预研
    # **失败**区分：失败条目是「方法论预研（…）」（全角括号），落默认分支的
    # 「未生成」是对的；这一条是「方法论预研：」（全角冒号）。
    ("方法论预研：", "  ⚠ "),
    # 子项目二：审核 3 轮未通过 / 审核未完成 —— 停下交人工。
    ("方法论审核", "  ⚠ "),
    # 依据核验通过、但来源分级是 MEDIA（`full._media_tier_note`）。这是一条
    # 算出来了、必须被看见的警告 —— 落默认分支会被印成「未生成」，读者会去
    # 找什么东西没产出。重构前就漏登记了，生益科技 2026-10-13 试跑时在门①
    # 的终端输出里看到。
    ("依据引用财经媒体级来源", "  ⚠ "),
    # 第三次调用读回因子采集结果时，`drivers.md` 被人改过、删掉了某个因子：
    # 那份采集结果被丢弃。是人的修改造成的说明，不是缺口。另两种（未落盘、
    # 读不回、某因子没有采集结果）是真缺口，落默认分支「未生成」是对的 ——
    # 所以前缀写到「因子采集结果里有」为止，不写成「因子采集结果」。
    ("因子采集结果里有", "  · "),
)


def _mark(s: str) -> str:
    for key, mark in _PREFIX:
        if s.startswith(key):
            return mark
    return "  – 未生成："


def next_actions(stage: Stage, code: str,
                 assumptions_path: str | None = None,
                 out_dir: str | None = None,
                 year: int | None = None,
                 phase: str = "") -> list[str]:
    """下一步该做什么。空列表 = 无阻塞。

    命令必须**能直接粘贴运行** —— 给一条跑不通的命令比不给指引更糟。
    实测踩到: 估值模块换成 ashare-dcf、两阶段重构之后，这里仍在教用户跑
    已被删除的 --review / --assumptions，用户照做只会撞上
    「unrecognized arguments」。**过时的指引是会主动误导人的那种错。**
    """
    out = out_dir or f"out/{code}"
    y = f"--year {year}" if year else "--year <年度>"
    if stage is Stage.AWAITING_SOURCES:
        return [
            f"核验资料来源：{out}/sources.md\n"
            "     逐条点开出处与本地文件。清单顶部若列出「缺原始接口快照」，"
            "那是结构性限制，不是本轮故障。",
            "核验完进入第一阶段之二 —— 多空辩论、方法论预研、因子采集、"
            "撰写简报：\n"
            f"     python -m ir_agent {code} {y} --brief {out} "
            f"--verified-by <你的名字>",
        ]
    if stage is Stage.AWAITING_BRIEF_REVIEW:
        return [
            f"审阅简报结论：{out}/brief.md\n"
            f"     待核清单 {out}/review.md 汇总了代码没把握的项（证据核验"
            "不过、缺快照的引用）。",
            f"多空平衡点：{out}/stance_b.md；分解式：{out}/drivers.md"
            "（分解不对就改这个文件后重跑 --brief，已有的辩论产物会沿用，"
            "要重抽加 --refresh-stance）。",
            "审阅完进入第一阶段之三 —— AI 填 DCF 判断项：\n"
            f"     python -m ir_agent {code} {y} --assume {out} "
            f"--reviewed-by <你的名字>",
        ]
    if stage in (Stage.AWAITING_REVIEW, Stage.AWAITING_ASSUME_REVIEW):
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
        # **重跑哪一阶段，由结果自己说，不从目录里猜。** INCOMPLETE 两个阶段
        # 都可能产生，而命令完全不同：第二阶段失败时教人重跑 --analyse，会
        # 覆盖掉人已经核实过的判断项；第一阶段失败时教人跑 --value，会拿
        # 上一轮残留的判断项去建模。
        #
        # 8689861 用「assumptions.md 在不在」来猜，生益科技重跑当场翻车：
        # 第一阶段撞额度、没写出判断项，目录里却躺着 9/14 上一轮的那份（建在
        # 已被新闸门拒收的旧分解式上），于是终端说「不要重跑第一阶段，接着
        # 跑第二阶段」——与正确做法正好相反。
        stale = (Path(out) / ASSUMPTIONS_MD).exists()
        if phase == "value":
            return [
                "判断项已核实但第二阶段未跑完（模型额度、网络等）——"
                "**不要重跑第一阶段**，那会覆盖掉已核实的判断项。"
                f"接着跑第二阶段即可：\n"
                f"     python -m ir_agent {code} {y} --value {out} "
                f"--approved-by <你的名字>",
            ]
        if phase == "brief":
            acts = [
                "第一阶段之二未跑完（原因见上方清单）。原样重跑：\n"
                f"     python -m ir_agent {code} {y} --brief {out} "
                f"--verified-by <你的名字>",
            ]
            if ((Path(out) / "drivers_rounds.md").exists()
                    and not (Path(out) / "drivers.md").exists()):
                acts.append(
                    f"方法论预研停下。若要人工给出分解式：以 {out}/drivers_failed_raw.md"
                    f" 为底稿改好，存为 {out}/drivers.md（各轮审核意见见 "
                    f"{out}/drivers_rounds.md），再跑上面那条命令 —— 读回时会标注"
                    "「人工修改后未经审核」。")
            for rnd in ("a", "b"):
                tmpl = Path(out) / f"stance_{rnd}.template.json"
                if tmpl.exists():
                    acts.append(
                        f"多空辩论{rnd.upper()} 未完成。若要人工补写：按 {tmpl} "
                        f"填好 {out}/stance_{rnd}.json，然后在上面那条命令后"
                        "加 --accept-manual。")
            return acts
        if phase == "assume":
            acts = [
                "第一阶段之三未跑完（原因见上方清单）。重跑：\n"
                f"     python -m ir_agent {code} {y} --assume {out} "
                f"--reviewed-by <你的名字>",
            ]
            if (Path(out) / ASSUMPTIONS_FAILED_JSON).exists():
                acts.append(
                    f"判断项审核停下。若要人工给出判断项：以 {out}/"
                    f"{ASSUMPTIONS_FAILED_JSON} 为底稿改好，存为 {out}/assumptions.json"
                    f"（各轮审核意见见 {out}/{ASSUMPTIONS_ROUNDS_MD}），再跑第二阶段：\n"
                    f"     python -m ir_agent {code} {y} --value {out} "
                    "--approved-by <你的名字>\n"
                    "     —— 读回时会标注「判断项未经审核」。")
            if stale:
                acts.append(
                    f"⚠ {out}/{ASSUMPTIONS_MD} 是**上一轮**留下的，不是本轮产物"
                    "——**不要拿它跑第二阶段**；重跑成功后会被覆盖。")
            return acts
        if phase == "analyse":
            acts = [
                "本轮未产出估值。若因分析步骤失败（模型额度、网络等），"
                f"重跑第一阶段之一即可：\n"
                f"     python -m ir_agent {code} {y} --analyse {out}",
            ]
            if stale:
                acts.append(
                    f"⚠ {out}/{ASSUMPTIONS_MD} 是**上一轮**留下的，不是本轮产物——"
                    "本轮的分解式可能已经变了，**不要拿它跑第二阶段**；"
                    "重跑第一阶段成功后会被覆盖。")
            return acts
        # 未注明阶段的旧调用方：沿用按文件判断的旧逻辑。
        if stale:
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
