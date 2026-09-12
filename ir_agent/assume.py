"""AI 填写 DCF 的判断项 —— 分析结论落到估值参数上。

**只填判断项，不碰校准项。** ashare-dcf 的假设分两类:

  · **校准项**（费用率、周转天数、折旧率、税率、β、Kd……）由 calibrate
    从历史财务数据算出。那些不是判断，是对历史的度量 —— AI 去动它们
    只会把已经算对的东西改坏。
  · **判断项**（逐年营收增速、逐年毛利率）是 ashare-dcf 自己标成黄底、
    并写明「三档初始同值，必须自行复核并拉开」的两项。它们**只能靠判断**，
    而判断正是前面那一整套分析的产出。

这个边界是这个模块存在的全部理由: 把宏观、行业、战略、可比的结论，
落到这两项上，并留下可核验的依据。

**三档必须拉开。** 模板默认 Bull=Base=Bear，那等于没有情景。拉不开的
情景分析只是把同一个数抄三遍。

**依据要指向分析结论，不是指向历史。** 「取近三年 CAGR」不是理由 ——
calibrate 已经这么算过了，AI 若只是复述它，这一步就没有发生。有价值的
是「行业增速 X 而本司份额在丢，因此基准取低于行业」这类能追溯到第 3、
第 4 节的链条。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum

from ir_agent import gaps
from ir_agent.basis import Basis, InvalidBasis, validate
from ir_agent.drivers import (Decomposition, Factor, Op, TARGET_LABEL,
                              compose, reconcile)
from ir_agent.gaps import FactorData
from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.lineparse import keyed, pick
from ir_agent.operators.ratios import annual_periods


class AssumeFailed(Exception):
    """假设不可核验或自相矛盾 —— 拒绝交给建模。"""


class Case(str, Enum):
    BULL = "乐观"
    BASE = "中性"
    BEAR = "悲观"


# 可填的判断项。**与 ashare-dcf 标为黄底的两项一一对应** ——
# 多填一项就是在动它已经算对的校准值。
DRIVERS = {"营收增速": "growth", "毛利率": "gm"}

_LINE = keyed("假设")
_QUOTE = re.compile(r"[「\"'“](.+?)[」”\"']", re.DOTALL)
_REF = re.compile(r"\[\[[^\]]+\]\]")
_NUM = re.compile(r"-?\d+(?:\.\d+)?%?")


def _normalize_num(raw: str) -> str:
    """`_pct` 与它的单位守卫必须共用同一套规范化，见 `_pct_token`。

    M1：全角/数学减号 U+2212（−）不是 ASCII 连字符，_NUM 认不出来，
    会把整个负号丢掉再匹配出一个正数——设计文档全文用的就是这个字符，
    模型很可能照抄，符号被悄悄翻转。规范化之后再匹配。
    I1：全角百分号（％）同理不是 ASCII %，_NUM 认不出来，会把 % 整个
    丢掉、按裸数处理——裸数对变化率因子是灾难性的（0.5% 写成 "0.5％"
    会被当成 0.5 = 50%）。全角百分号信息无损，直接规范化成 ASCII。
    I-a（final-fix-D）：数字与 % 之间若有空白（"0.5 %"），_NUM 要求
    % 紧贴数字，空白会让匹配止步于数字本身、% 被整段丢弃——"0.5 %"
    因此被当成裸数 0.5（=50%）吞下，是 I1 单位守卫本该挡住却没挡住
    的同一类致命错误，只是多了一个空格。收紧数字与 % 之间的空白后
    再匹配。
    """
    t = raw.replace(",", "").replace("−", "-").replace("％", "%")
    return re.sub(r"(\d)\s+%", r"\1%", t)


def _pct_token(raw: str) -> str | None:
    """规范化后，`_pct` 实际会解析出的那个数字 token（含它自己的 %，
    若有）。单位守卫必须复用这个 token 来判断"这个字面量到底有没有
    带 %"，不能自己去查整段字符串里有没有 % 字符。

    I-a 实测复现：字面量 "2026年+5%" 整段确实含有 %，但 `_NUM.search`
    实际抓到的第一个数字是它前面的 "2026"（后面跟着"年"，不是紧贴
    %），`_pct` 用的就是这个不带 % 的 token，取到的值是 2026 而不是
    0.05。若守卫只查"字符串里有没有 %"，会因为字符串末尾那个不相干
    的 "%" 而放行，两处判据不一致，等于形同虚设。
    """
    m = _NUM.search(_normalize_num(raw))
    return m.group(0) if m else None


def _pct(raw: str) -> Decimal:
    t = _pct_token(raw)
    if t is None:
        raise AssumeFailed(f"取值不可解析：{raw!r}")
    try:
        v = Decimal(t.rstrip("%"))
    except InvalidOperation as e:
        raise AssumeFailed(f"取值不可解析：{raw!r}") from e
    return v / 100 if t.endswith("%") else v


@dataclass
class Row:
    """一个判断项在某一年、三档下的取值。"""

    driver: str                 # growth | gm
    year: int                   # 1 起
    bull: Decimal
    base: Decimal
    bear: Decimal
    bases: list[Basis] = field(default_factory=list)
    # 第 6 字段里除引文/占位符之外的那段话 —— **这才是理由本身**。
    # 此前只抽占位符、把散文丢掉，于是待核实清单上只剩一串数字和一串
    # key，人要判断「这个增速为什么是 3%」却无从判起。
    note: str = ""
    # 分解模式下这一行对应哪个因子。空串表示旧的两标量模式 ——
    # 预研失败时的降级路径，必须保留。
    factor_key: str = ""


def _decomp_to_dict(d: Decomposition) -> dict:
    return {"target": d.target, "op": d.op.value,
            "factors": [{"key": f.key, "label": f.label, "unit": f.unit,
                        "source_hint": f.source_hint} for f in d.factors]}


def _decomp_from_dict(d: dict) -> Decomposition:
    op = next(o for o in Op if o.value == d["op"])
    return Decomposition(
        target=d["target"], op=op,
        factors=[Factor(key=f["key"], label=f["label"], unit=f["unit"],
                        source_hint=f.get("source_hint", ""))
                for f in d["factors"]])


def to_json(a: "AssumptionSet", **extra) -> dict:
    """待核实清单落盘。**连依据一起存** —— 只存数值的话，第二阶段读回来
    的假设一律「未提供任何依据」，会被自己的门挡住。

    同时记下 AI 的原始取值: 人改过之后，原来的依据未必还支撑新数字，
    那件事必须被看见而不是悄悄放行。

    **分解模式下 `decomps` 与每行的 `factor_key` 必须一起存下来。**
    漏掉它们不会立刻报错 —— 第二阶段照样能读出一个 AssumptionSet，
    只是 `factor_key` 全变空串、`decomps` 变空列表：`synthesize()` 按
    factor_key 分组，空 key 的行会被整体滤掉，`apply_to`/`to_driver_block`
    因此拿到空列表，人在第一阶段批准的因子分解在第二阶段悄悄消失，
    却不会有任何异常提示——这正是本模块要防的「静默出错」。
    """
    return {"labels": a.labels,
            "decomps": [_decomp_to_dict(d) for d in a.decomps],
            "rows": [{"driver": r.driver, "year": r.year,
                      "bear": str(r.bear), "base": str(r.base),
                      "bull": str(r.bull), "note": r.note,
                      "factor_key": r.factor_key,
                      "bases": [b.to_dict() for b in r.bases],
                      "proposed": {"bear": str(r.bear), "base": str(r.base),
                                   "bull": str(r.bull)}}
                     for r in a.rows],
            **extra}


def from_json(d: dict, code: str = "") -> tuple["AssumptionSet", list[str]]:
    """读回，并报出人改过哪些值。"""
    decomps = [_decomp_from_dict(x) for x in (d.get("decomps") or [])]
    a = AssumptionSet(code=code or d.get("code", ""),
                      labels=d.get("labels") or [], decomps=decomps)
    edits: list[str] = []
    for x in d.get("rows") or []:
        r = Row(driver=x["driver"], year=int(x["year"]),
                bear=Decimal(x["bear"]), base=Decimal(x["base"]),
                bull=Decimal(x["bull"]), note=x.get("note", ""),
                factor_key=x.get("factor_key", ""),
                bases=[Basis.from_dict(b) for b in (x.get("bases") or [])])
        a.rows.append(r)
        p = x.get("proposed") or {}
        for case in ("bear", "base", "bull"):
            if case in p and Decimal(p[case]) != getattr(r, case):
                cn = _row_label(r, decomps)
                edits.append(
                    f"{cn} 第{r.year}年 {case}：模型给 {p[case]}，"
                    f"核实后改为 {getattr(r, case)}")
    return a, edits


def _row_label(r: "Row", decomps: list[Decomposition]) -> str:
    """人改过的值要报得出名字。分解模式下 `r.driver` 是 revenue/
    gross_margin，不是因子本身 —— 要报的是具体改了哪个因子（否则改了
    「产能」和改了「单价」在提示里会长得一模一样，都写成同一个笼统的
    目标名）。"""
    if r.factor_key:
        for d in decomps:
            for f in d.factors:
                if f.key == r.factor_key:
                    return f.label
        return r.factor_key
    return "营收增速" if r.driver == "growth" else "毛利率"


@dataclass
class AssumptionSet:
    code: str
    labels: list[str]                       # 预测期年标签
    rows: list[Row] = field(default_factory=list)
    rationale: str = ""
    # 以下三项只在按因子分解模式下有内容 —— 两标量降级模式留空即可。
    decomps: list = field(default_factory=list)          # 本轮用的分解式
    factor_data: dict = field(default_factory=dict)      # 因子采集结果（Task 6）
    # 历史回算残差，报告用，**不是通过/不通过的判据** ——
    # Π(1+r) 永远不会正好等于实际增速，拿它做硬校验只会逼出假精度。
    # {target: {"residuals": [Residual], "no_history": [因子标签,...]}}
    residuals: dict = field(default_factory=dict)
    # 因子模式下合成毛利率要用的基期水平（决速于 decide() 传入的
    # base_margin）——describe() 展示「合成结果」要用同一个数，
    # 不能自己另算一个，否则清单上显示的数和实际建模用的数就会分叉。
    base_margin: Decimal | None = None
    # I4：因子模式连续填不出合格判断项时，decide() 回落到两标量模式，
    # 这里记下降级原因。**不进 to_json/from_json**——降级是这一轮研究
    # 自己的事，不需要跨阶段持久化；describe() 会把它显式印在清单顶部，
    # 「降级但继续」不能悄悄发生，人必须一眼看到这轮其实是降级产出的。
    degraded: str = ""

    def path(self, driver: str, case: Case) -> list[Decimal]:
        got = sorted((r for r in self.rows if r.driver == driver),
                     key=lambda r: r.year)
        return [getattr(r, {Case.BULL: "bull", Case.BASE: "base",
                            Case.BEAR: "bear"}[case]) for r in got]

    def describe(self) -> str:
        """给人看的核实清单。**这是人工批准的对象，必须一眼看全。**

        分解模式（`self.decomps` 非空）与两标量模式的渲染完全不同——
        分解模式下 `Row.driver` 记的是分解目标（revenue/gross_margin），
        永远匹配不上 `DRIVERS` 里的 growth/gm 两个旧键，按旧逻辑渲染会
        让清单整页空白（C1：人工批准的核心关卡变成一张只有标题的空页）。
        """
        lines = [f"DCF 判断项（{self.code}）—— 需人工核实后方可建模", ""]
        if self.degraded:
            # 「降级但继续」不能悄悄发生——清单顶部就要让人看到这轮
            # 判断项其实是降级产出的，损失的是精度，不是担保。
            lines += [f"⚠ **降级说明**：{self.degraded}", ""]
        if self.decomps:
            lines += self._describe_factors()
        else:
            lines += self._describe_scalar()
        return "\n".join(lines)

    def _describe_scalar(self) -> list[str]:
        lines: list[str] = []
        for cn, key in DRIVERS.items():
            got = sorted((r for r in self.rows if r.driver == key),
                         key=lambda r: r.year)
            if not got:
                continue
            lines.append(f"## {cn}")
            lines.append("")
            lines.append("| 年 | 悲观 | 中性 | 乐观 | 理由 | 依据 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for r in got:
                lab = self.labels[r.year - 1] if r.year <= len(self.labels) \
                    else f"第{r.year}年"
                ref = "；".join(b.describe() for b in r.bases)
                lines.append(f"| {lab} | {r.bear:.2%} | {r.base:.2%} "
                             f"| {r.bull:.2%} | {r.note or '—'} | {ref} |")
            lines.append("")
        return lines

    def _describe_factors(self) -> list[str]:
        """因子模式下的清单：按目标分组，每个因子一张表 + 该目标的
        **合成结果**（代码算出来的，不是模型自己填的那个数）。"""
        lines: list[str] = []
        for d in self.decomps:
            lines.append(f"## {TARGET_LABEL.get(d.target, d.target)}"
                        f" —— {d.describe()}")
            lines.append("")
            for f in d.factors:
                got = sorted((r for r in self.rows if r.factor_key == f.key),
                             key=lambda r: r.year)
                lines.append(f"### {f.label}（{f.unit}）")
                lines.append("")
                lines.append("| 年 | 悲观 | 中性 | 乐观 | 理由 | 依据 |")
                lines.append("| --- | --- | --- | --- | --- | --- |")
                if not got:
                    # **遗漏必须可见。** 因子在分解式里，却一行取值都
                    # 没有——不能悄悄不显示，得让人一眼看出这里空着。
                    lines.append("| — | — | — | — | **未填写** | — |")
                for r in got:
                    lab = self.labels[r.year - 1] \
                        if r.year <= len(self.labels) else f"第{r.year}年"
                    # 出口二落地为空 `bases`——不能让这一格跟"有依据"的
                    # 行看起来一样（一样都是拿 join 拼一遍，空列表拼出的
                    # 是空字符串，读者会当成"依据列忘了填"而不是"这本来
                    # 就是没有依据的估计"）。必须显式标出来，一眼分得清。
                    ref = ("；".join(b.describe() for b in r.bases)
                           if r.bases else "⚠ 估计（无数据支撑）")
                    lines.append(
                        f"| {lab} | {_fmt_factor(r.bear, f.unit)} "
                        f"| {_fmt_factor(r.base, f.unit)} "
                        f"| {_fmt_factor(r.bull, f.unit)} "
                        f"| {r.note or '—'} | {ref} |")
                lines.append("")
            lines += self._describe_composed(d)
            lines += self._describe_residuals(d)
        lines += self._describe_estimated()
        return lines

    def _describe_estimated(self) -> list[str]:
        """**遗漏必须可见。** 因子取值若完全靠估计（出口二：无数据、
        没有证据支撑），不能只淹没在各自因子的表格里等人逐行去发现——
        单独汇总一段，让人一眼看出「这几个数是估的，没有证据支撑」，
        这正是当初做因子分解要保留的东西：与其在两标量模式下笼统地填
        一个营收增速，不如把"到底哪些因子有据、哪些是拍脑袋"摊开来看。
        """
        labels = sorted({_row_label(r, self.decomps)
                         for r in self.rows if r.factor_key and not r.bases})
        if not labels:
            return []
        return ["⚠ **以下因子的取值没有证据支撑，是估计**"
                "（已如实标注，理由见各自表格的「理由」列，不是遗漏）："
                + "、".join(labels), ""]

    def _synthesized_for(self, d: Decomposition, case: Case
                         ) -> list[Decimal] | None:
        """展示用的合成结果，**按目标单独判断能不能算**，不是「任一
        分解式缺基期就全部显示不出来」（I7）。

        营收是乘法链 Π(1+r)−1，永远算得出来，不需要基期；毛利率是加法
        链，缺基期却硬要合成，会显示一个用假基期算出来、看起来正常的
        错数——比不显示更糟，所以只有这一条线才返回 None 让调用方改
        显示说明文字，而不会连坐到营收那一节。
        """
        if d.op is not Op.MULTIPLY and self.base_margin is None:
            return None
        attr = {Case.BULL: "bull", Case.BASE: "base",
                Case.BEAR: "bear"}[case]
        keys = {f.key for f in d.factors}
        years = max((r.year for r in self.rows if r.factor_key in keys),
                    default=0)
        out: list[Decimal] = []
        for y in range(1, years + 1):
            vals = {r.factor_key: getattr(r, attr) for r in self.rows
                    if r.year == y and r.factor_key in keys}
            if d.op is Op.MULTIPLY:
                out.append(compose(d, vals))
            else:
                out.append(compose(d, vals, base=self.base_margin))
        return out

    def _describe_composed(self, d: Decomposition) -> list[str]:
        lines = ["**合成结果**（代码按分解式算出，不是模型直接填的数）", ""]
        lo_s = self._synthesized_for(d, Case.BEAR)
        mid_s = self._synthesized_for(d, Case.BASE)
        hi_s = self._synthesized_for(d, Case.BULL)
        if lo_s is None or mid_s is None or hi_s is None:
            tgt = TARGET_LABEL.get(d.target, d.target)
            lines.append(f"基期{tgt}未知，暂无法显示合成后的{tgt}——"
                        "因子取值仍已生效，只是这里算不出用于展示的数。")
            lines.append("")
            return lines
        lines.append("| 年 | 悲观 | 中性 | 乐观 |")
        lines.append("| --- | --- | --- | --- |")
        for y in range(len(mid_s)):
            lab = self.labels[y] if y < len(self.labels) else f"第{y+1}年"
            lines.append(f"| {lab} | {lo_s[y]:.2%} | {mid_s[y]:.2%} "
                        f"| {hi_s[y]:.2%} |")
        lines.append("")
        return lines

    def _describe_residuals(self, d: Decomposition) -> list[str]:
        """历史回算残差（C2）。**只报告，不是通过/不通过的判据**——
        Π(1+r) 永远不会正好等于实际增速。历史值取不到的因子要显式标
        「无历史校验」，不能因为算不出残差就整段不提。"""
        info = self.residuals.get(d.target)
        if not info:
            return []
        lines = ["**历史回算残差**（仅供核对分解式抓没抓住主要矛盾，"
                "不作为通过/不通过的判据）", ""]
        res = info.get("residuals") or []
        if res:
            lines.append("| 期间 | 合成 | 实际 | 残差 |")
            lines.append("| --- | --- | --- | --- |")
            for r in res:
                lines.append(f"| {r.period} | {r.composed:.2%} "
                            f"| {r.actual:.2%} | {r.gap:+.2%} |")
            lines.append("")
        no_hist = info.get("no_history") or []
        if no_hist:
            lines.append(f"无历史校验：{'、'.join(no_hist)}"
                        "（账本里取不到足够的历史序列，不是没有偏差，"
                        "是这里没法核验）")
            lines.append("")
        return lines


def _esc_pipe(s: str) -> str:
    """Minor-3（final-fix-D）：markdown 表格单元格里的 "|" 必须转义，
    否则年报/研报原文片段里常见的表格竖线会被当成列分隔符，把这一行
    提示词表格撑成错位的更多列。"""
    return s.replace("|", "\\|")


def _fmt_factor(v: Decimal, unit: str) -> str:
    """因子取值按单位格式化。**pp 不能用 :.2% 显示**——C4 的字面量
    百分号 bug 一旦漏网，`-0.03` 用 :.2% 会显示成 "-3.00%"，和正确的
    `-3`（应显示 "-3.00pp"）看起来一模一样，人从清单上根本发现不了。
    pp 按原始数字显示，才能让写错单位的字面量露出马脚。"""
    return f"{v:+.2f}pp" if unit == "pp" else f"{v:.2%}"


def parse(text: str, years: int,
          decomps: list[Decomposition] | None = None,
          factor_data: dict | None = None,
          as_of: date | None = None) -> AssumptionSet:
    """decomps 给定时按因子解析；否则沿用两标量模式（降级路径）。

    降级路径不删 —— 预研（拆分解式、采数据）失败不该让整轮研究停摆。

    `factor_data`/`as_of` 只在因子模式下用得到——`_parse_factors` 要
    靠它们判断模型引用的一段话是不是这个因子自己采集到的证据（见
    `_factor_basis`）。两标量模式不需要，两者都留空即可；不传时因子
    模式退回旧行为（只认账本占位符与正文逐字引用），不影响既有调用方。
    """
    if not decomps:
        return _parse_scalar(text, years)
    return _parse_factors(text, years, decomps, factor_data, as_of)


def _parse_scalar(text: str, years: int) -> AssumptionSet:
    out = AssumptionSet(code="", labels=[])
    # C-a（final-fix-D）：`_parse_factors` 早就查了 (factor_key, year)
    # 重复，这条降级路径漏了同一道门——而它恰恰是因子模式连续 3 次被
    # 「请修正后重新输出全部行」驳回之后才会走到的路径，模型此时已经
    # 被连续训练成输出"修正前/修正后"两版。比因子模式更糟：`path()`
    # 把全部行按年份排序后直接返回一个列表，一条重复行会让它后面
    # 每一年整体错位一格（`ashare_build.py` 按下标 `i` 取值，不是按
    # 年份查找），人批准的某一年会被静默丢弃、多出的一项顶替了别的
    # 年份。降级模式不删，那它就必须和主路径一样硬。
    seen: set[tuple[str, int]] = set()
    for m in _LINE.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) < 6:
            raise AssumeFailed(
                f"假设一行字段不足（需 6 段：项目|年|悲观|中性|乐观|依据），"
                f"得到 {len(parts)}：{m.group(1)}")
        cn = pick(parts[0], list(DRIVERS))
        if cn is None:
            raise AssumeFailed(
                f"未知的判断项 {parts[0]!r}，只能填：{'、'.join(DRIVERS)}。"
                "费用率、周转天数、折旧率等由历史数据校准，不在此处填写。")
        try:
            year = int(re.search(r"\d+", parts[1]).group(0))
        except (AttributeError, ValueError) as e:
            raise AssumeFailed(f"年份不可解析：{parts[1]!r}") from e
        if not 1 <= year <= years:
            raise AssumeFailed(f"年份 {year} 超出预测期 1..{years}")
        if (DRIVERS[cn], year) in seen:
            raise AssumeFailed(
                f"{cn} 第 {year} 年出现了不止一行——重试提示词里的「请"
                "修正后重新输出全部行」很容易诱发模型把修正前/修正后"
                "两版都留在同一次回复里。重复行会让 path() 按年份排序"
                "后整体错位一格，后面每一年都会取到前一行的数字，请只"
                "保留一行，不要让代码替你决定该采信哪一行。")
        seen.add((DRIVERS[cn], year))
        # Minor-5（final-fix-D）：与因子侧同形的窄口径守卫——不要求
        # 必须带 %，小数记法（"0.02" = 2%）是这条路径本就认可的既有
        # 约定（见 test_百分号与小数都认），营收增速/毛利率本身没有
        # 像因子那样的 pp/% 双单位歧义，谈不上"必须带 %"。但 bp 是
        # 另一套单位，混进来会被 `_pct` 当裸数直接吞下（"-350bp" 被
        # 解析成 -350，渲染成 -35000%），必须显式拒绝。
        for label, i in (("悲观", 2), ("中性", 3), ("乐观", 4)):
            raw = parts[i]
            if "bp" in raw.replace("％", "%").lower():
                raise AssumeFailed(
                    f"{cn} 第 {year} 年 {label}档 {raw!r} 按小数或百分比"
                    "计，不认 bp —— bp 与 %/小数不是同一套单位，不做"
                    "隐式换算，请换算成百分比或小数再填。")
        out.rows.append(Row(
            driver=DRIVERS[cn], year=year,
            bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
            bases=_basis(parts[5], cn), note=_note(parts[5])))
    return out


def _parse_factors(text: str, years: int,
                   decomps: list[Decomposition],
                   factor_data: dict | None = None,
                   as_of: date | None = None) -> AssumptionSet:
    """按因子解析。每一行对应分解式里的一个因子，而不是营收增速/毛利率
    本身 —— 合成交给 `synthesize`，模型只负责填因子。

    `factor_data`/`as_of` 传给 `_factor_basis` 判断依据是否合法——见
    该函数文档，这里只负责把每个因子对应的 `FactorData` 找出来传过去。
    """
    factor_data = factor_data or {}
    by_label = {f.label: f for d in decomps for f in d.factors}
    target_of = {f.key: d.target for d in decomps for f in d.factors}
    out = AssumptionSet(code="", labels=[], decomps=list(decomps))
    # C2：同一因子同一年出现两行时，合成侧（synthesize/_synthesized_for）
    # 用字典推导式取最后一行，to_driver_block 曾经用 next() 取第一行，
    # 清单显示的是其中一行、工作簿实际建模用的是另一行——人批准的数字
    # 与真正驱动估值的数字不是同一个。重复行本身就说明模型在一次回复
    # 里生成了「修正前/修正后」两版，没想清楚，应当直接驳回重填，
    # 而不是替它决定该采信哪一行。
    seen: set[tuple[str, int]] = set()
    # 「同上」出口③要按因子回指"上一行"——不是文本里紧邻的上一行
    # （那可能是别的因子），而是同一个 `factor_key` 之前处理过的那
    # 一行。按因子分别记录，出现过的因子才有"上一行"可回指；键不存在
    # （`.get()` 返回 `None`）与"上一行是空依据"（存的是 `[]`）要能
    # 区分，前者必须驳回，后者允许原样复用一个空列表。
    last_basis: dict[str, list[Basis]] = {}
    for m in _LINE.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) < 6:
            raise AssumeFailed(
                f"假设一行字段不足（需 6 段：因子|年|悲观|中性|乐观|依据），"
                f"得到 {len(parts)}：{m.group(1)}")
        label = pick(parts[0], list(by_label))
        if label is None:
            raise AssumeFailed(
                f"未知的因子 {parts[0]!r}，本次分解式里只有："
                + "、".join(by_label))
        f = by_label[label]
        try:
            year = int(re.search(r"\d+", parts[1]).group(0))
        except (AttributeError, ValueError) as e:
            raise AssumeFailed(f"年份不可解析：{parts[1]!r}") from e
        if not 1 <= year <= years:
            raise AssumeFailed(f"年份 {year} 超出预测期 1..{years}")
        if (f.key, year) in seen:
            raise AssumeFailed(
                f"因子「{label}」第 {year} 年出现了不止一行——重试提示词"
                "里的「请修正后重新输出全部行」很容易诱发模型把修正前/"
                "修正后两版都留在同一次回复里。重复行本身说明这个因子还"
                "没想清楚，请只保留一行，不要让代码替你决定该采信哪一行。")
        seen.add((f.key, year))
        # C4：pp 与 % 不是一回事——85%→82% 是 −3pp，写成 −3% 会被 _pct
        # 解析成 −0.03，缩小 100 倍。单调性/闭合校验都抓不住这个错，
        # 只能在这里把字面量本身挡下来。
        # I1：全角 ％ 与半角 % 一视同仁，否则 "-3％" 能绕过这道守卫
        # （对 pp 而言"碰巧对"，但下一次全角换成 "-3.5％" 就会被当成
        # 裸数 -3.5，渲染成 -350%）。
        if f.unit == "pp" and any(
                "%" in parts[i].replace("％", "%") for i in (2, 3, 4)):
            raise AssumeFailed(
                f"因子「{label}」按 pp 计，不按 %：{parts[2]} / {parts[3]} "
                f"/ {parts[4]} 里带了 % —— pp 与 % 不是一回事，85%→82% "
                "是 −3pp，写成 −3% 会被当成 −0.03pp，缩小 100 倍。"
                "请直接填数字（如 −3），不要带 %。")
        # I1：单位守卫此前只挡了 pp 侧的字面量 %，变化率侧完全没有防护——
        # 裸数 0.5 会被当成小数直接使用（渲染成 50.00%），对一个扩产
        # 故事看起来完全正常，人根本看不出这里其实想表达 0.5%；
        # -3.5pp / -350bp 这类把 pp/bp 误当变化率填的写法也会被静默
        # 当裸数吞下去。变化率必须显式带 %（全角 ％ 等价，直接规范化；
        # bp 是另一套单位，不做隐式换算，明确拒绝并提示换算方法）。
        if f.unit != "pp":
            for i in (2, 3, 4):
                raw, norm = parts[i], parts[i].replace("％", "%")
                if "bp" in norm.lower():
                    raise AssumeFailed(
                        f"因子「{label}」按变化率（%）计，不认 bp：{raw} —— "
                        "bp 与 % 不是同一套单位，不做隐式换算，请直接换算"
                        f"成百分比再填（如 −350bp 请写成 −3.5%）。")
                # I-a（final-fix-D）：不能只查"整段字符串里有没有 %"——
                # 一个 "0.5 %" 这样的空格就能骗过这道守卫（_pct 实际
                # 解析出的 token 是不带 % 的裸数 "0.5"，%被空白隔开、
                # 整个丢弃），"2026年+5%" 这类字面量也会因为字符串
                # 末尾不相干的一个 % 而放行。守卫必须复用 `_pct` 自己
                # 会用到的那个 token 来判断，两处才是同一套判据。
                if not (_pct_token(raw) or "").endswith("%"):
                    raise AssumeFailed(
                        f"因子「{label}」按变化率计，必须带 %：{raw} —— "
                        "裸数会被当成小数直接使用而不是百分比（0.5 会被"
                        "解释成 50%，而不是你想表达的 0.5%），且这类错误"
                        "合成后看起来仍然合理，人从清单上发现不了。"
                        "请直接填百分比，如 −3.5%。")
        bases = _factor_basis(parts[5], label, factor_data.get(f.key),
                             as_of, prior=last_basis.get(f.key))
        last_basis[f.key] = bases
        out.rows.append(Row(
            driver=target_of[f.key], year=year,
            bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
            bases=bases, note=_note(parts[5]), factor_key=f.key))
    return out


def synthesize(a: AssumptionSet, case: Case,
              base_margin: Decimal) -> tuple[list[Decimal], list[Decimal]]:
    """把因子合成回逐年营收增速与毛利率。**代码算，不是模型填。**

    若让模型自己算出结果再填进去，分解就退化成一层装饰 —— 它可以先想好
    结果，再倒推几个因子凑出来。所以这里真的从因子值算，模型只填因子。

    注意: 本函数本身**不做单调性校验** —— 校验属于 `check`（它要跨三档
    分别调用本函数再比较），这里只管合成这一步。
    """
    attr = {Case.BULL: "bull", Case.BASE: "base", Case.BEAR: "bear"}[case]
    years = max((r.year for r in a.rows), default=0)
    growth, margin = [], []
    for y in range(1, years + 1):
        vals = {r.factor_key: getattr(r, attr)
                for r in a.rows if r.year == y and r.factor_key}
        for d in a.decomps:
            picked = {k: v for k, v in vals.items()
                      if k in {f.key for f in d.factors}}
            if d.target == "revenue":
                growth.append(compose(d, picked))
            else:
                margin.append(compose(d, picked, base=base_margin))
    return growth, margin


def _note(raw: str) -> str:
    """第 6 字段里的散文部分 —— 去掉引文与占位符后剩下的理由。"""
    t = _REF.sub("", raw)
    t = _QUOTE.sub("", t)
    return re.sub(r"\s+", " ", t).strip(" ，,。;；")


def _basis(raw: str, what: str) -> list[Basis]:
    raw = raw.strip()
    ref = _REF.search(raw)
    if ref:
        try:
            return [Basis.fact(ref.group(0))]
        except InvalidBasis as e:
            raise AssumeFailed(f"{what} 的占位符不合法: {e}") from e
    q = _QUOTE.search(raw)
    if not q:
        raise AssumeFailed(f"{what} 缺少可核验的依据（需正文原话或占位符）")
    try:
        return [Basis.report(q.group(1))]
    except InvalidBasis as e:
        raise AssumeFailed(f"{what} 的依据不合法: {e}") from e


_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    """与 basis.py/external.py 同一套规范化——引文核验必须两边一致。"""
    return _WS.sub("", s)


# 「无数据（估计）」的识别不做成一个固定短语的死匹配——模型措辞会有
# 「无数据，取值为估计」「暂无数据，只能估计」这类变体，本文件与
# gaps.py 自己的提示词/note 里就已经这么写。只要求"无数据"与"估计"
# 两个词同时出现，不要求相邻或顺序，比死匹配一个固定句式更贴近模型
# 真实产出，同时仍然远比"随便一句散文"苛刻——两个词都不沾边的普通
# 理由句不会被误判成这条出口。
def _looks_like_estimate(raw: str) -> bool:
    return "无数据" in raw and "估计" in raw


# 「同上」类回指的识别同样不做固定短语的死匹配——模型措辞会有
# 「同上」「同上引文」「同前」「见上」「同该因子上一年」这类变体。
# 这里没有沿用 `_looks_like_estimate` 那种"两个关键词任意共现"的
# 写法：「同」「上」两个单字太容易在正常理由散文里意外同时出现
# （例如"预计营收同比上升"），共现会把一句正常理由错判成回指。
# 因此改成有限几种具体写法的并列，每一种都是模型实际会写、且不会
# 被普通散文意外命中的短语；"同…上一年/上一行/上一档"允许因子名
# 之类的短语插在中间。
_DITTO = re.compile(r"同上|同前|见上|同.{0,8}上一(?:年|行|档)")


def _looks_like_ditto(raw: str) -> bool:
    return bool(_DITTO.search(raw))


def _match_finding(fd: FactorData | None, quote: str):
    """在该因子④外部网络采集到的 Finding 里找逐字引用这段话的那一条。

    找不到返回 None——调用方据此判断这条引文到底是不是这个因子自己
    采集到的证据，而不是模型编出来、恰好看着像的一句话。"""
    if fd is None:
        return None
    qn = _norm(quote)
    if not qn:
        return None
    for finding in fd.findings:
        if qn in _norm(finding.claim):
            return finding
    return None


def _match_excerpt(fd: FactorData | None, quote: str) -> bool:
    """在②年报/③研报命中时摘出的原文片段里找这段引文。

    **只认 `fd.source_excerpt`，绝不从 `fd.note` 正则反解。**
    此前这里用非贪婪正则 `_QUOTE` 从 `fd.note`（拼给人看的展示文本，
    格式是「「摘录」——出处（说明）」）里把摘录抠出来——600584 实测
    直接击穿了这个做法：年报原文恰好嵌着一对中文弯引号（"…“创新提效、
    行深致远”…"），非贪婪匹配一遇到年报原文内部的收尾引号就把摘录
    腰斩，模型逐字引用「高端先进制造产能规模化创收」（我们自己采集
    给它的证据）因此判定"匹配不到"，连续 3 次驳回后拖累整轮降级成
    两标量模式。年报正文里出现引号是常态，不是需要额外兼容的边缘
    情况——真正的问题是把机器要精确取回的数据，塞进了一段专门为
    "给人读着顺眼"设计的格式化字符串里，再指望正则把它原样抠回来。
    `note` 的格式以后可以随便改（换措辞、调整出处写法），只要没人
    想起这里还在拿正则啃它，匹配就会静默失效。所以 `gaps.collect`
    命中②③时把摘出的原文片段额外存成独立字段 `source_excerpt`，
    这里直接用它，不再看 `note` 一眼——展示文本与机器取值的数据源
    彻底分开，才不会再被 note 格式的任何改动连累。

    只对 ANNUAL/REPORT 两种状态生效——DISCLOSED/MISSING/EXTERNAL 没有
    这个字段（默认空串），该走别的出口（findings 或"无数据"）。
    """
    if fd is None or fd.status not in (gaps.ANNUAL, gaps.REPORT):
        return False
    qn = _norm(quote)
    return bool(qn) and bool(fd.source_excerpt) and qn in _norm(fd.source_excerpt)


def _factor_basis(raw: str, label: str, fd: FactorData | None,
                  as_of: date | None,
                  prior: list[Basis] | None = None) -> list[Basis]:
    """因子模式下依据的合法写法——比两标量模式（`_basis`）多三条出口。

    **这两条出口要修的契约缺口**：真实联网跑出的分解式常有一多半因子
    正文一次都没提过（600584 实测：9 因子 7 个正文零命中），此前只认
    账本占位符（`[[key@period]]`）与正文逐字引用两种写法——对这类因子
    根本不存在合法的依据写法，模型连续 3 次被拒后只能把整个分解丢掉、
    退回两标量模式。

    新增的两条出口都遵循本仓库一贯的契约——**模型写引用，代码填
    事实**，与占位符本身、`_basis` 的正文引用是同一个思路，绝不让
    模型自己声明 URL/日期/来源，那等于给捏造开一扇门：

      ① 引用该因子自己采集到的证据——命中 `fd.findings` 里某条
         `Finding.claim` 就用 `Basis.external`，出处（url、抓取日期）
         直接用那条 Finding 自带的；命中②③摘出的原文片段（`fd.
         source_excerpt`——独立字段，**不是**从 `fd.note` 里正则反解，
         见 `_match_excerpt` 文档字符串）同样用 `Basis.external`，
         出处是 `gaps.collect` 早已经代码之手记下的 `fd.source_url`/
         `source_retrieved`（②是年报公告本身的下载地址，③是研报文档
         的真实 URL——都是货真价实的网络地址，不是编出来的）。

         年报片段没有直接被归到 `Basis.report`：`Basis.report` 的核验
         (`basis.validate`) 拿的是 `check()` 当时传入的 `body`——本模块
         语境下 `body` 就是研究简报正文本身，而年报原文并不在这段正文
         里，把它标成 REPORT 会在**紧接着的这次 `check()`** 就被判定
         "在正文中找不到"而驳回，反而比不给出口更糟。EXTERNAL 的核验
         在 `corpus=None` 时只查格式不查内容（`check()`/`validate()`
         从未给这里传 corpus，与 WACC 的无风险利率是同一处先例）——
         这恰恰是这里需要的性质：年报原文只在预研当轮、`sections`
         对象还在内存里时能核验一次，两阶段之间人工批准、`assumptions.
         json` 被重新读回时未必还有 `sections`（第二阶段不会重新下载
         解析年报 PDF），若沿用 REPORT 的"每次 check() 都重新核验"，
         人工批准过的年报引用会在第二阶段因为"年报没重新加载"这个纯
         环境原因被错误打回，而不是因为引文本身有问题。EXTERNAL 的
         "验一次、代码盖章后终身信任"正好避开了这个陷阱，这也是它比
         REPORT 更诚实的地方：它如实反映了"这条依据的核验窗口只有
         一次"这个事实，而不是假装每次都还能重新核验。

      ② 「无数据（估计）」——但只对这个因子**确实**是 `MISSING`（或
         干脆没被采集到，`factor_data` 里都没有它这一项）时才开放，
         返回空的依据列表，不构造任何 Basis：没有证据就是没有证据，
         硬凑一个看似合规的 Basis 反而是在掩盖"这里其实没有依据"这
         件事。是否合法在这里（模型刚生成、`fd` 还是这一轮真实采集
         结果时）判一次，之后不再回头复查——跟出口①的"验一次、之后
         信任"是同一个道理，且第二阶段本来就没有 `factor_data` 可查。

      ③ 「同上」——**这是本次要修的失败场景本身**：真实联网跑出的
         分解式常有 9 个因子、预测期 5 年，要填 45 行。依据在语义上
         是"每个因子一份"（证明这个因子的数据来源），逐年不同的只是
         理由（判断）；要求同一段年报引文原样抄 5 遍、9 个因子就是
         45 遍，本身就是冗余。600584 实测：模型第 2 年起试图用「依据：
         同上引文」缩写，被本函数判"缺少可核验的依据"直接驳回（既
         无占位符也无「…」引号引文，也不是「无数据…估计」）。

         含义精确定为：**与该因子（同一 `factor_key`）上一行的依据
         相同**——不是"文本里紧邻的上一行"，那可能是别的因子，会有
         歧义，而歧义出现在一个负责"可核验"的机制里不能接受。由
         `_parse_factors` 按因子分别记录上一行，把那条**已经核验
         过**的 `Basis` 列表原样传进来（`prior` 参数），这里直接复用
         同一批 `Basis` 对象，审计链条不受损——与占位符、出口①的
         "模型写引用，代码填事实"是同一个契约：模型只负责说"跟哪一行
         一样"，取值仍然是代码从已核验过的数据里复制出来的，不是模型
         自己声明的。

         该因子**没有上一行**时（`prior is None`，比如第 1 年就写
         「同上」）必须驳回——第一次出现该因子必须给出完整依据，不能
         凭空回指一个不存在的"上一行"。

         **注意不要误伤**：这条出口排在 ref/quote 两条出口**之后**
         判断——一行里如果同时给了真实引文（`[[...]]` 或「…」）和
         "同上"字样，前两条出口会先命中并 return，不会走到这里，
         因此以真实引文为准。
    """
    raw = raw.strip()
    ref = _REF.search(raw)
    if ref:
        try:
            return [Basis.fact(ref.group(0))]
        except InvalidBasis as e:
            raise AssumeFailed(f"{label} 的占位符不合法: {e}") from e

    q = _QUOTE.search(raw)
    if q:
        quote = q.group(1)
        finding = _match_finding(fd, quote)
        if finding is not None:
            return [Basis.external(url=finding.url, quote=quote.strip(),
                                   retrieved=as_of)]
        if _match_excerpt(fd, quote):
            if not fd.source_url:
                # 内部一致性错误，不是模型的问题——gaps.collect 命中
                # ②③时理应同时记下 source_url（见该函数），走到这里
                # 说明采集与解析这两处对同一份 factor_data 的假设不一致。
                raise AssumeFailed(
                    f"{label} 引用的年报/研报片段缺失真实出处"
                    "（factor_data 内部不一致：source_url 为空）——"
                    "请检查 gaps.collect 是否遗漏了 source_url。")
            return [Basis.external(url=fd.source_url, quote=quote.strip(),
                                   retrieved=fd.source_retrieved or as_of)]
        try:
            return [Basis.report(quote)]
        except InvalidBasis as e:
            raise AssumeFailed(f"{label} 的依据不合法: {e}") from e

    if _looks_like_estimate(raw):
        if fd is None or fd.status == gaps.MISSING:
            return []
        raise AssumeFailed(
            f"{label} 写了「无数据（估计）」，但该因子的采集状态是"
            f"「{fd.status}」，不是「{gaps.MISSING}」——「无数据」只对"
            "确实没有数据支撑的因子开放，不能图省事对有数据的因子也这么"
            "写，那会让这条出口变成谁都能用的万能逃生口。")

    if _looks_like_ditto(raw):
        if prior is None:
            raise AssumeFailed(
                f"{label} 写了「同上」，但这是该因子出现的第一行，"
                "没有上一行可以回指——「同上」指的是同一因子（而不是"
                "文本里紧邻的上一行，那可能是别的因子）上一行的依据，"
                "第一次出现该因子时必须给出完整依据（正文原话、占位符、"
                "采集到的证据引文，或「无数据（估计）」）。")
        return list(prior)

    raise AssumeFailed(
        f"{label} 缺少可核验的依据（需正文原话、账本占位符、该因子自身"
        "采集到的证据引文、对确实无数据的因子明说「无数据（估计）」，"
        "或对同一因子上一行的依据回指「同上」）")


def check(a: AssumptionSet, years: int, *, ledger: FactLedger, body: str,
          as_of: date, decomps: list[Decomposition] | None = None,
          base_margin: Decimal | None = None) -> None:
    if decomps:
        _check_factors(a, years, decomps, base_margin)
    else:
        _check_scalar(a, years)
    flat = [b for r in a.rows for b in r.bases]
    # 出口二（「无数据（估计）」）合法时 `Row.bases` 就是空列表——
    # `basis.validate([])` 的原意是防"整批一条依据都没给"，但因子模式
    # 下"每一行都被 `_factor_basis` 判定为合法的无数据估计"是一种真实
    # 可能出现、且**应当被允许**的情形（分解式里的因子确实可能全部
    # 缺数据），不是"没做功课"。这种情形已经在 `_factor_basis`/
    # `_check_factors` 里逐行验过一遍（确实 MISSING、且有理由），不需要
    # 再套用这条本来是给"完全没有产出"设的全局闸门；反过来，只要有任何
    # 一行不是走这条出口（`flat` 非空），就仍然照常校验，不放过真正
    # 缺依据的情形。
    all_estimated = bool(a.rows) and decomps and all(not r.bases for r in a.rows)
    if not all_estimated:
        problems = validate(flat, ledger=ledger, body=body, as_of=as_of)
        if problems:
            raise AssumeFailed("依据不可核验：" + "；".join(problems))


def _check_scalar(a: AssumptionSet, years: int) -> None:
    # C-a 纵深防御（final-fix-D）：`_parse_scalar` 已在解析阶段拒绝
    # 重复行，但 `check()` 也会在人工核实之后、`from_json` 读回时被
    # 再跑一遍（`full.run_valuation`）——那时的 `AssumptionSet` 未必
    # 经过 `_parse_scalar`，人手改 JSON 也可能引入重复行。这里再挡
    # 一次，不依赖上游一定已经查过重（与 `_check_factors` 同一套
    # 纵深防御思路）。
    inv = {v: k for k, v in DRIVERS.items()}
    seen: set[tuple[str, int]] = set()
    for r in a.rows:
        key = (r.driver, r.year)
        if key in seen:
            raise AssumeFailed(
                f"{inv.get(r.driver, r.driver)} 第 {r.year} 年出现了不止"
                "一行——重复行会让 path() 按年份排序后整体错位一格，"
                "必须只保留一行。")
        seen.add(key)

    for cn, key in DRIVERS.items():
        got = {r.year for r in a.rows if r.driver == key}
        missing = sorted(set(range(1, years + 1)) - got)
        if missing:
            raise AssumeFailed(
                f"{cn} 缺第 {missing} 年 —— 预测期每一年都要给，"
                "漏掉的年份会沿用模板默认值，而那不是你的判断")

    for r in a.rows:
        # **三档必须拉开。** 模板默认 Bull=Base=Bear，那等于没有情景；
        # 拉不开的情景分析只是把同一个数抄三遍。
        if not (r.bear < r.base < r.bull):
            raise AssumeFailed(
                f"第 {r.year} 年的{'营收增速' if r.driver == 'growth' else '毛利率'}"
                f"三档未拉开（悲观 {r.bear} / 中性 {r.base} / 乐观 {r.bull}）"
                " —— 必须严格递增，否则情景分析没有意义")


def _check_factors(a: AssumptionSet, years: int, decomps: list[Decomposition],
                   base_margin: Decimal | None) -> None:
    """分解模式下的校验。

    **不在这里逐因子比较 bear<base<bull。** 单个因子档位的方向性因子而异
    （例如同一因子在悲观档被写得很极端，未必意味着它对目标的方向也是
    悲观的），代码判断不了「这个因子往哪个方向走算悲观」。真正要卡的是
    **合成结果**——三档营收增速 / 毛利率必须严格拉开，这是唯一能客观
    判定方向对不对的地方。因子层面各自拉开不等于合成后拉开：产能悲观档
    偏高、单价悲观档偏低，相乘后完全可能悲观反而高于中性，而那正是这道
    校验要抓住的错数。
    """
    # C2 纵深防御：`_parse_factors` 已经在解析阶段拒绝重复行，但
    # `check()` 也会在人工核实之后、`from_json` 读回时被再跑一遍
    # （`full.run_valuation`）——那时的 `AssumptionSet` 未必经过
    # `_parse_factors`，人手改 JSON 也可能引入重复行。这里再挡一次，
    # 不依赖上游一定已经查过重。
    label_of = {f.key: f.label for d in decomps for f in d.factors}
    seen: set[tuple[str, int]] = set()
    for r in a.rows:
        if not r.factor_key:
            continue
        key = (r.factor_key, r.year)
        if key in seen:
            raise AssumeFailed(
                f"因子「{label_of.get(r.factor_key, r.factor_key)}」"
                f"第 {r.year} 年出现了不止一行——重复行会让清单显示的值"
                "与实际参与合成的值不是同一行，必须只保留一行。")
        seen.add(key)
        # 出口二（「无数据（估计）」）落地为空的 `bases`——`_factor_basis`
        # 已经在解析阶段确认过这个因子确实是「无数据」才放行，但那不能
        # 免除「说明依据什么估的」这一要求：空依据 + 空理由 = 一个数字
        # 凭空出现在清单上，读者连"这是怎么估出来的"都无从判断。这里
        # 再挡一次而不依赖解析阶段——`check()` 在第二阶段人工核实之后
        # 会脱离 `_parse_factors` 单独重跑，人手改 JSON 同样可能把
        # note 清空。
        if not r.bases and not r.note.strip():
            raise AssumeFailed(
                f"因子「{label_of.get(r.factor_key, r.factor_key)}」"
                f"第 {r.year} 年写了「无数据（估计）」却没有给理由——"
                "理由要说明这个估计依据什么给出（参照哪个可比公司、"
                "哪个行业惯例……），不能只留一句「无数据」。")

    for d in decomps:
        for f in d.factors:
            got = {r.year for r in a.rows if r.factor_key == f.key}
            missing = sorted(set(range(1, years + 1)) - got)
            if missing:
                raise AssumeFailed(
                    f"因子「{f.label}」缺第 {missing} 年 —— 预测期每一年都要给，"
                    "漏掉的年份会沿用模板默认值，而那不是你的判断")

    # **合成后再验一次单调。** 这是本模块真正的闸门。
    if base_margin is None:
        base_margin = Decimal("0.10")
    a.decomps = list(decomps)
    lo, lom = synthesize(a, Case.BEAR, base_margin)
    mid, midm = synthesize(a, Case.BASE, base_margin)
    hi, him = synthesize(a, Case.BULL, base_margin)
    for i in range(len(mid)):
        if not (lo[i] < mid[i] < hi[i]):
            raise AssumeFailed(
                f"第 {i + 1} 年的营收增速**合成后**不再单调"
                f"（悲观 {lo[i]:.2%} / 中性 {mid[i]:.2%} / 乐观 {hi[i]:.2%}）"
                " —— 因子各自拉开不等于合成结果拉开，请调整因子取值，"
                "不要悄悄修正合成结果")
    for i in range(len(midm)):
        if not (lom[i] < midm[i] < him[i]):
            raise AssumeFailed(
                f"第 {i + 1} 年的毛利率**合成后**不再单调"
                f"（悲观 {lom[i]:.2%} / 中性 {midm[i]:.2%} / 乐观 {him[i]:.2%}）"
                " —— 因子各自拉开不等于合成结果拉开，请调整因子取值，"
                "不要悄悄修正合成结果")


PROMPT = """把前面的分析结论，落到 DCF 模型的判断项上。

{head}

**预测期**：{years} 年（{labels}）

**模型给出的历史参考**（仅供你判断，不要照抄）
{history}

**你的分析简报**（引文依据只能逐字出自这里）
{brief}

**各层分析口径摘要**（供你判断，**不是可引用的正文**）
{context}

**硬性要求**
- 三档必须**严格拉开**（悲观 < 中性 < 乐观）。模板默认三档同值，
  那等于没有情景 —— 拉不开的情景分析只是把同一个数抄三遍。
- 每一行的第 6 段要写两样东西: **理由**（为什么是这个数）+ **依据**
  （`[[账本占位符]]` 或「简报原文逐字引用」）。理由会原样出现在待核实
  清单上，是人判断这个数合不合理的唯一线索 —— 只给依据不给理由，
  清单上就只剩一串数字和一串 key。
- 依据要**指向你的分析结论**。「取近三年 CAGR」不是理由 —— 模型已经
  这么算过了，你若只是复述它，这一步就没有发生。有价值的是「行业增速 X
  而本司份额在丢，因此基准取低于行业」这类能追溯到具体某一节的链条。
- **引文只能逐字出自上面的「分析简报」**，摘要那几段不能引（它们不是正文）。
  拿不准就用账本占位符，那个一定核验得过。
- 预测期每一年都要给，漏掉的年份会沿用模板默认值，而那不是你的判断。
- 增速路径要讲得通：若你认为拐点在中后段，就让前段贴近当前现实，
  不要直接外推行业中枢。
"""


_SCALAR_HEAD = """**你只填这两项**：逐年营收增速、逐年毛利率。
费用率、周转天数、折旧率、税率、β、债务成本等由历史财务数据校准而来，
已经算好，不需要也不应该由你改动。

对每一项、每一年给出一行：
`假设: <营收增速|毛利率> | 第N年 | <悲观> | <中性> | <乐观> | 「正文原话」或 [[占位符]]`"""


def _factor_head(decomps: list[Decomposition], factor_data: dict) -> str:
    """分解模式下替换 `_SCALAR_HEAD` 的提示词头部。

    **逐因子填、代码合成**——不是把「你只填这两项」换成「你只填一堆
    因子」这么简单，还要把单位规则钉死在提示词里，否则模型很容易把 pp
    填进乘法链（稼动率 85%→82% 写成 −3pp 混进变化率链）。
    """
    lines = ["**本次按驱动因子分解填写。** 分解式："]
    for d in decomps:
        lines.append(f"  {d.describe()}")
    lines += ["", "逐个因子填，代码会把它们合成回营收增速与毛利率——",
              "**不要自己算出增速或毛利率再倒推因子**，那样分解就没有意义。",
              "", "| 因子 | 单位 | 数据 |", "| --- | --- | --- |"]
    for d in decomps:
        for f in d.factors:
            fd = factor_data.get(f.key)
            if fd is None:
                st = "未采集"
            elif fd.note:
                # I2：这里此前只印状态词、不印原文片段——模型看到「年报
                # 披露」会直接当成有数据可用去填数字。「年报披露/研报
                # 测算」只是文字命中，未必真给出了数值（甚至可能命中的
                # 是一句「未披露 XX」），必须把 gaps.collect 摘出来的
                # 原文片段一起摆出来，让模型自己判断这到底是不是能当
                # 数值依据用的原文，而不是被一个状态词代为下结论。
                st = f"{fd.status}（{fd.note}）"
            else:
                st = fd.status
            # Minor-3（final-fix-D）：`fd.note` 抄的是 gaps.collect 摘出
            # 来的年报/研报原文片段——PDF 抽出的章节带表格竖线很常见，
            # 未转义的 "|" 会被当成这一行提示词表格自己的列分隔符，把
            # 单位这一列错位到别处去。转义成 markdown 认得的 "\|"。
            lines.append(f"| {_esc_pipe(f.label)} | {_esc_pipe(f.unit)} "
                        f"| {_esc_pipe(st)} |")
    lines += ["",
              "**pp 不能进乘法链**：营收链的因子一律填变化率（含稼动率）——"
              "稼动率 85%→82% 是 −3pp，但变化率是 −3.5%。",
              "毛利率链的因子一律填 pp，且**pp 一律不带 %**——"
              "写 −3，不要写 −3%（−3% 会被当成 −0.03pp，缩小 100 倍）。",
              "**pp 的参照点是基期，且是累计值，不是逐年再变一次**："
              "毛利率 = 基期 + Σpp，基期对预测期每一年都是同一个数，"
              "所以第 N 年的 pp 要填「相对基期」累计的变动，不是「相对"
              "第 N−1 年」又变了多少。例如基期 14.15%，你判断毛利率会"
              "逐年走低到 13.15%/12.15%/11.15%，三年应分别填 "
              "−1、−2、−3（相对基期累计），而不是 −1、−1、−1"
              "（那样合成出来三年会是同一个数 13.15%，不会继续下滑）。",
              "",
              "**引文只能出自三处**：你的分析简报正文、账本占位符 "
              "[[key@period]]、或下表「数据」列里这个因子自己采集到的"
              "原文片段/发现。下面各节（宏观、行业、可比公司、外部资料、"
              "产业链……）、本表的因子名和单位这些表头，都只是给你判断"
              "用的上下文，**不是**可引用的正文——从里面摘一句当引文"
              "（哪怕摘的是某节小标题）会被直接驳回。",
              "**引文必须逐字连续摘录**：不要用省略号（…/...）缩写，"
              "也不要把原文里不相邻的句子拼起来当一句引文。理由那部分"
              "可以照常总结复述，但引文这部分要能被代码原样在来源里"
              "逐字反查到——用省略号跳过中间几个字，或者拼接两处不相邻"
              "的话，都会让这段引文在来源里找不到连续对应的片段，"
              "代码分不清这是真引用还是你自己编的，只会被当作编造直接"
              "驳回。",
              "",
              "**依据除了「正文原话」「[[占位符]]」，还有三条路——"
              "很多因子正文根本没提过，也不在账本里，这些路才是它们"
              "唯一合法的依据写法**：",
              "① 上表「数据」列若给出了具体的原文片段或「发现」，你可以"
              "**逐字**引用那一段（仍然写成「引文」的样子），出处（网址/"
              "抓取日期）由代码自动核对并填上——不要自己写网址或日期，"
              "写了也不会被采信，一个字都对不上会被直接驳回。",
              "② 上表「数据」列若是「无数据」（或整行都没有），可以直接"
              "写「无数据（估计）」，但理由里**必须**说明这个估计是怎么"
              "来的（参照哪个可比公司、哪个行业惯例、哪条常识……）——"
              "「无数据」只对确实没有数据支撑的因子开放，对「已披露」"
              "「年报披露」「研报测算」「外部来源」的因子这么写会被直接"
              "驳回，不能图省事整表都写「无数据」。",
              "③ **同一个因子在不同年份的依据经常是同一段引文**"
              "（同一份年报表述往往能支撑好几个年份的判断）——从第 2 次"
              "出现该因子起，依据可以直接写「同上」「同上引文」等"
              "（措辞不拘一格，只要能看出是回指），代码会原样复用**同一"
              "个因子**上一年已经核验过的依据，不需要把引文抄一遍。"
              "**「同上」指的是同一因子上一年的依据，不是这一行上面紧邻"
              "的那一行**——那有可能是别的因子。**第一次出现该因子时"
              "必须给出完整依据**（①②或「正文原话」「[[占位符]]」"
              "之一），没有上一行可回指的「同上」会被直接驳回。理由"
              "（为什么这一年这么判断）不受影响，仍然要每年单独写"
              "清楚——「同上」只省依据，不省理由。",
              "",
              "对每一个因子、每一年给出一行：",
              "`假设: <因子名> | 第N年 | <悲观> | <中性> | <乐观> | "
              "「正文原话/证据原文」或 [[占位符]] 或 无数据（估计）"
              "或 同上`"]
    return "\n".join(lines)


def build_prompt(judg: dict, labels: list[str], context: str,
                 brief: str = "", decomps: list[Decomposition] | None = None,
                 factor_data: dict | None = None) -> str:
    g, gm = judg.get("g_stats") or (), judg.get("gm_stats") or ()
    hist = []
    if g:
        hist.append(f"  营收增速：近3年CAGR {judg.get('cagr3', 0) * 100:.1f}%、"
                    f"历史均值 {g[0] * 100:.1f}%、标准差 {g[1] * 100:.1f}pp、"
                    f"区间 {g[2] * 100:.1f}%~{g[3] * 100:.1f}%")
    if gm:
        hist.append(f"  毛利率：最近一期 "
                    f"{(judg.get('gm_hist') or [0])[-1] * 100:.1f}%、"
                    f"历史均值 {gm[0] * 100:.1f}%、标准差 {gm[1] * 100:.1f}pp、"
                    f"区间 {gm[2] * 100:.1f}%~{gm[3] * 100:.1f}%")
    head = _factor_head(decomps, factor_data or {}) if decomps else _SCALAR_HEAD
    return PROMPT.format(head=head, years=len(labels), labels="、".join(labels),
                         history="\n".join(hist) or "  （无）",
                         context=context, brief=brief or "（无）")


def _decide_once(client, judg: dict, labels: list[str], context: str, *,
                 ledger: FactLedger, body: str, as_of: date, code: str,
                 max_attempts: int, decomps: list[Decomposition] | None,
                 factor_data: dict | None,
                 base_margin: Decimal | None) -> AssumptionSet:
    """单一模式（因子或两标量）内的重试循环。

    只管一种模式，失败就抛 `AssumeFailed`——**模式之间要不要降级，是
    `decide()` 的职责，不是这里的**：因子模式连续失败后可以退回两标量
    模式再试一次，但两标量模式本身连续失败没有更低的台阶可退，必须
    原样拒绝。两件事分开才不会在这里意外递归。
    """
    prompt = build_prompt(judg, labels, context, brief=body, decomps=decomps,
                          factor_data=factor_data)
    last, raw = "", ""
    for attempt in range(1, max_attempts + 1):
        raw = client(prompt)
        try:
            a = parse(raw, len(labels), decomps=decomps,
                     factor_data=factor_data, as_of=as_of)
            check(a, len(labels), ledger=ledger, body=body, as_of=as_of,
                  decomps=decomps, base_margin=base_margin)
        except AssumeFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新输出全部行。\n")
            continue
        a.code, a.labels = code, labels
        a.decomps = list(decomps or [])
        a.factor_data = dict(factor_data or {})
        a.base_margin = base_margin
        if a.decomps:
            # C2：reconcile() 写得对、测试充分，缺的是「在真实流程里被
            # 调用一次并把结果交给人看」这个动作——此前它只在
            # drivers.py 自己的单测里被调用过。
            a.residuals = {d.target: _reconcile_decomp(d, ledger, as_of)
                          for d in a.decomps}
        return a
    raise AssumeFailed(
        f"连续 {max_attempts} 次给不出可核验的假设：{last}"
        f"\n最后一次的原始输出（末 400 字）：\n{raw.strip()[-400:]}")


def decide(client, judg: dict, labels: list[str], context: str, *,
           ledger: FactLedger, body: str, as_of: date, code: str = "",
           max_attempts: int = 3, decomps: list[Decomposition] | None = None,
           factor_data: dict | None = None,
           base_margin: Decimal | None = None) -> AssumptionSet:
    """AI 填判断项，因子模式连续失败时回落到两标量路径（I4）。

    因子模式要填 N 因子 × Y 年 × 3 档，比两标量模式的 2×5×3 重数倍，
    失败概率明显更高。一旦连续 `max_attempts` 次都填不出合格的因子，
    直接向上抛错会让**整个 DCF 判断项都没有**——能力边界不是故障，
    整轮研究不该因此停摆，这里改为退回两标量模式再试一次，并把降级
    原因记在 `AssumptionSet.degraded` 上（`describe()` 会把它印在清单
    顶部）：**降级但继续、如实说明降级原因，不能假装成功，也不能中断。**

    两标量模式（`decomps` 为空）本身连续失败没有更低的台阶可退，原样
    向上抛出。
    """
    try:
        return _decide_once(client, judg, labels, context, ledger=ledger,
                            body=body, as_of=as_of, code=code,
                            max_attempts=max_attempts, decomps=decomps,
                            factor_data=factor_data, base_margin=base_margin)
    except AssumeFailed as e:
        if not decomps:
            raise
        a = _decide_once(client, judg, labels, context, ledger=ledger,
                         body=body, as_of=as_of, code=code,
                         max_attempts=max_attempts, decomps=None,
                         factor_data=None, base_margin=None)
        a.degraded = (f"因子模式连续 {max_attempts} 次填不出合格的判断项"
                      f"（{str(e)[:160]}）—— 已退回两标量模式（营收增速/"
                      "毛利率），损失的是因子层面的精度，不是担保")
        return a


def _historical_series(ledger: FactLedger, key: str, periods: list[str],
                       as_of: date) -> dict[str, Decimal]:
    """账本里某个 key 在给定期间序列上的取值。取不到就跳过——
    不用零补齐，补出来的残差是假的。"""
    out: dict[str, Decimal] = {}
    for p in periods:
        try:
            out[p] = ledger.get(key, p, as_of=as_of).value
        except (KeyError, LookAheadError):
            continue
    return out


def _reconcile_decomp(d: Decomposition, ledger: FactLedger,
                      as_of: date, years: int = 5) -> dict:
    """对一个分解式做历史回算，报残差 + 标出取不到历史的因子。

    账本里存的因子是**水平值**（产能、单价……的原始数字），而
    `drivers.reconcile` 要的是**期间变化**（乘法链要变化率，加法链要
    pp 变化）——按 `d.op` 把相邻两期的水平值换算成变化，换算规则与
    提示词里对模型的单位要求（见 `_factor_head`）是同一套。

    某个因子在账本里完全找不到历史序列，**不能因此把它从残差报告里
    静默拿掉**——要在 `no_history` 里明说，人得知道这里没法核验，
    而不是误以为「没提到就是没问题」。
    """
    periods = annual_periods(ledger, as_of, years=years + 1)
    if len(periods) < 2:
        return {"residuals": [], "no_history": [f.label for f in d.factors]}

    levels = {f.key: _historical_series(ledger, f.key, periods, as_of)
             for f in d.factors}
    no_history = [f.label for f in d.factors if len(levels[f.key]) < 2]

    history: dict[str, dict[str, Decimal]] = {}
    for i in range(1, len(periods)):
        cur, prev = periods[i], periods[i - 1]
        row: dict[str, Decimal] = {}
        for f in d.factors:
            lv = levels[f.key]
            if cur not in lv or prev not in lv or lv[prev] == 0:
                continue
            row[f.key] = (lv[cur] / lv[prev] - 1) if d.op is Op.MULTIPLY \
                else (lv[cur] - lv[prev]) * 100
        if row:
            history[cur] = row

    target_key = "revenue" if d.target == "revenue" else "gross_margin"
    tgt = _historical_series(ledger, target_key, periods, as_of)
    actual: dict[str, Decimal] = {}
    base: dict[str, Decimal] | None = {} if d.op is Op.ADD_PP else None
    for i in range(1, len(periods)):
        cur, prev = periods[i], periods[i - 1]
        if cur not in tgt:
            continue
        if d.op is Op.MULTIPLY:
            if prev not in tgt or tgt[prev] == 0:
                continue
            actual[cur] = tgt[cur] / tgt[prev] - Decimal(1)
        else:
            actual[cur] = tgt[cur]
            if prev in tgt:
                base[cur] = tgt[prev]

    res = reconcile(d, history, actual, base=base)
    return {"residuals": res, "no_history": no_history}


def _annual_gm_hist(cal: dict) -> list:
    """`cal["judg"]["gm_hist"]` 里去掉末尾中报、只留完整年度的那一段。

    ashare_calibrate 的 `hp = ann + ([interim] if interim else [])`，
    `gm_hist` 按 `hp` 的顺序算出来，中报（如果有）只可能出现在**最后
    一项**。

    **C1：此前靠 `len(g_hist) + 1` 反推年度期数，前提是 `gm_hist` 与
    `g_hist` 各自的过滤结果长度差恰好为 1。** 但两者是各自独立过滤的
    （`gm_hist` 按「营收 *和* 营业成本」过滤，`g_hist` 只按「营收」
    过滤），而 `ashare_data.assert_modelable` 明文容忍近 5 年缺 1 年
    营业成本（`have < len(annuals) - 1` 才拒绝）——这恰好把「差 1」这
    个前提打破: 缺 1 年营业成本时 `gm_hist` 少一项、`g_hist` 不受影响，
    反推出的期数会把中报也算进「年度」里，取到中报当基期（实测 600584
    上约 32% 估值误差）；营收缺失导致 `g_hist` 少一项时，反推期数又会
    偏小，取到比最近年度更早的一年，口径同样错，且两种情形都不报错。

    **改为直接用 `cal["periods"]["annual"]` 数一遍**，并按与 `gm_hist`
    完全一致的过滤条件（`rev.get(p) and cogs.get(p)` 同时成立才计入）
    筛选，不需要也不应该从别的序列反推。

    Minor-2（final-fix-D）：这里不引用 `ashare_calibrate.py` 里
    `gm_mean = np.mean(gm_hist[:len(ann)])`（约第 254 行）当作"这么
    数是对的"的依据——查过那一行才发现它自己用的是**未经 rev/cogs
    过滤的裸 `len(ann)`**，在缺 1 年营业成本的场景下，它自己也会把
    中报算进年度均值，这正是本函数要修的那类偏差，不是可以援引的
    正确参照。这里比那处更严格（过滤条件与 `gm_hist` 完全对齐），
    但那处偏差属于 ashare-dcf 自己的问题，不在本仓库改动范围内，
    只作只读参考记在这里，不要去改 ashare-dcf。
    """
    judg = cal.get("judg") or {}
    gm_hist = judg.get("gm_hist") or []
    if not gm_hist:
        return []
    annual = (cal.get("periods") or {}).get("annual") or []
    rev = cal.get("rev") or {}
    cogs = cal.get("cogs") or {}
    n_annual = sum(1 for p in annual if rev.get(p) and cogs.get(p))
    n_annual = min(n_annual, len(gm_hist))
    return gm_hist[:n_annual]


def _base_margin_of(cal: dict) -> Decimal:
    """基期毛利率 —— 加法链的起点，**对全部预测年、全部三档都是同一个数**
    （见 drivers.compose 的文档: 「基期」是账本口径的最近一期实绩，不随
    年份滚动）。取**最近一个完整年度**的实际毛利率（`gm_hist` 掐掉末尾
    可能有的中报之后的最后一项），不由 AI 给、也不能直接拿 `gm_hist[-1]`
    ——那一项在中报期可能是中报数字，不是完整年度。

    参数是完整的 calibrate 输出 `cal`（不是 `cal["judg"]`）——C1 修复
    后年度期数要从 `cal["periods"]["annual"]` 直接数，不再从 `judg`
    内部的 `g_hist` 反推，因此需要 `cal` 顶层的 `periods`/`rev`/`cogs`。

    **没有 fallback。** 此前取不到 `gm_hist` 时会退回 `judg["gm"][0]`，
    但 `apply_to` 先跑、`to_driver_block` 后跑时，`gm[0]` 早已经是
    「基期 + 第一年 pp 增量」的合成结果——拿它当基期，会把第一年的
    增量在 1b_驱动分解 的工作表公式里再加一遍（I6）。calibrate 正常
    产出时 `gm_hist` 不会为空，为空说明上游已经出了问题，静默凑一个
    近似值只会把错误往下游藏得更深，不如直接拒绝。
    """
    annual = _annual_gm_hist(cal)
    if not annual:
        # Minor-1（final-fix-D）：`_annual_gm_hist` 在两种截然不同的
        # 原因下都会返回空列表——`gm_hist` 本身是空的，或者 `gm_hist`
        # 有值但 `cal["periods"]["annual"]`/`rev`/`cogs` 缺失、按同一
        # 过滤条件数出来的年度期数恰好是 0。此前不管哪种原因都报同一
        # 句「judg 里没有 gm_hist」——后一种情形下 `gm_hist` 明明在，
        # 排障会被指向错误的地方。按实际原因分别给话。
        gm_hist = (cal.get("judg") or {}).get("gm_hist") or []
        if not gm_hist:
            raise AssumeFailed(
                "基期毛利率取不到（judg 里没有 gm_hist）—— 不能退回 "
                "calibrate 模板给的默认预测值当基期，那本身是需要因子分解"
                "合成出来的结果，拿它当基期会把第一年的增量重复计入一次。")
        raise AssumeFailed(
            "基期毛利率取不到——judg 里的 gm_hist 有值，不是空的；是 "
            "cal 缺 periods['annual']，或其 rev/cogs 对不上（数年度期数"
            "用的是 cal 顶层的 periods/rev/cogs，按与 gm_hist 相同的过滤"
            "条件筛出来是 0 项）。请检查 calibrate 输出里这几项是否完整，"
            "同样不能退回默认预测值当基期。")
    return Decimal(str(annual[-1]))


def apply_to(cal: dict, a: AssumptionSet, case: Case = Case.BASE) -> dict:
    """把判断项写回 calibrate 的输出，供 ashare_build 建表。

    三档分别写进 judg 的 *_scen，由 build 分列填入 Bull/Base/Bear；
    单列的 growth/gm 保留为选定档，兼容未改造的旧路径。

    **分解模式下不能再按 growth/gm 两个旧键用 `path()` 过滤。**
    `_parse_factors` 给每行 `Row.driver` 记的是分解目标本身
    （revenue/gross_margin），不是 growth/gm —— 按旧键过滤会让
    `a.path("growth", case)` 一行都匹配不上，把 calibrate 算好的模板
    悄悄覆盖成空列表（下游 ashare_build 逐年取值会直接 IndexError）。
    这里改为对每一档跑一遍 `synthesize()`，从因子合成回营收增速与
    毛利率——语义上与 to_driver_block 的 base_level 必须用同一个基期，
    否则两处各算各的，会在第一年的毛利率上重复计入一次 pp 增量。
    """
    ju = dict(cal["judg"])
    if a.decomps:
        base_margin = _base_margin_of(cal)
        growth_scen, gm_scen = {}, {}
        for c in Case:
            g, m = synthesize(a, c, base_margin)
            growth_scen[c.name.lower()] = [float(x) for x in g]
            gm_scen[c.name.lower()] = [float(x) for x in m]
        ju["growth"] = growth_scen[case.name.lower()]
        ju["gm"] = gm_scen[case.name.lower()]
        ju["growth_scen"] = growth_scen
        ju["gm_scen"] = gm_scen
    else:
        ju["growth"] = [float(x) for x in a.path("growth", case)]
        ju["gm"] = [float(x) for x in a.path("gm", case)]
        ju["growth_scen"] = {c.name.lower(): [float(x) for x in a.path("growth", c)]
                             for c in Case}
        ju["gm_scen"] = {c.name.lower(): [float(x) for x in a.path("gm", c)]
                         for c in Case}
    out = dict(cal)
    out["judg"] = ju
    return out


def to_driver_block(a: AssumptionSet, cal: dict) -> dict:
    """AssumptionSet → ashare_build 要的 `drivers` 结构。

    三档顺序固定为 bull/base/bear，与工作簿 B/C/D 列绑定 ——
    顺序写反不会让验算失败，只是「选悲观」会选出乐观值。

    **base_level 取 `_base_margin_of`，不是 `cal["judg"]["gm"][0]`。**
    `apply_to` 若已跑过，`gm[0]` 是**合成之后**的年度水平（基期 + 第一年
    的 pp 增量），拿它当基期会让 1b_驱动分解 的公式把第一年的增量算
    两遍：先被吸进 base_level，又被工作表公式再加一次。`gm_hist` 是
    calibrate 的原始历史序列，不受 apply_to 影响，才是真正「不随年份
    滚动」的基期。

    **取值规则与 synthesize()/_synthesized_for() 保持一致（C2 纵深防御）。**
    同一因子同一年出现两行本应在 `_parse_factors`/`check()` 就被拒绝，
    但这里不依赖那道门一定挡住了一切——`next(... )` 会取**第一行**，
    与合成侧的字典推导式取**最后一行**是两条不同的规则，一旦有重复行
    绕过检查混进来，清单显示的数字与这里实际写进工作簿的数字就会是
    两行不同的取值。改成与合成侧同样的「后写入覆盖先写入」，即便查重
    将来被放开，两侧至少不会各算各的。
    """
    base_margin = float(_base_margin_of(cal))
    out = {}
    years = max((r.year for r in a.rows), default=0)
    by_year_key: dict[tuple[int, str], Row] = {}
    for r in a.rows:
        if r.factor_key:
            by_year_key[(r.year, r.factor_key)] = r
    for d in a.decomps:
        keys = [f.key for f in d.factors]
        label_of = {f.key: f.label for f in d.factors}
        vals = {}
        for case in ("bull", "base", "bear"):
            rows_by_year = []
            for y in range(1, years + 1):
                vals_for_year = []
                for k in keys:
                    try:
                        row = by_year_key[(y, k)]
                    except KeyError as e:
                        # Minor-4（final-fix-D）：此前这里是裸 KeyError，
                        # 一路冒到 run_valuation 的兜底里显示成「估值
                        # （KeyError: (2, 'asp')）」——排障看不出是哪个
                        # 因子哪一年缺了取值。`check()` 用 len(a.labels)
                        # 定年数，这里用 max(r.year) 定年数，两者本应
                        # 一致，但人手改 JSON 把某个因子的年份改到超出
                        # labels 范围时会错开：那个因子多出的年份把这里
                        # 的 years 撑大，其它因子在那一年却没有取值。
                        raise AssumeFailed(
                            f"因子「{label_of.get(k, k)}」第 {y} 年缺"
                            "取值，无法写入 1b_驱动分解 工作表——多半是"
                            "人手改 JSON 把某个因子的年份改到了超出预测"
                            "期范围，让不同因子的年份对不上。") from e
                    vals_for_year.append(float(getattr(row, case)))
                rows_by_year.append(vals_for_year)
            vals[case] = rows_by_year
        block = {"op": d.op.value,
                 "factors": [{"key": f.key, "label": f.label, "unit": f.unit}
                             for f in d.factors],
                 "values": vals}
        if d.target == "gross_margin":
            block["base_level"] = base_margin
        out[d.target] = block
    return out
