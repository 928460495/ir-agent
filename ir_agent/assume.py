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
from ir_agent.basis import Basis, BasisKind, InvalidBasis, validate
from ir_agent.formula import (FormulaError, FormulaInvalid, FormulaModel,
                              as_model, evaluate, flat_factors, from_decomps,
                              parse_formula, simulate, Constant as _Constant,
                              parse_constant)
from ir_agent.drivers import (Decomposition, DecompositionInvalid, Factor, Op,
                              TARGET_LABEL, Residual, check_dimensions, compose,
                              reconcile)
from ir_agent.gaps import FactorData
from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.lineparse import keyed, pick
from ir_agent.operators.ratios import annual_periods
from ir_agent.sources.external import norm


class AssumeFailed(Exception):
    """假设不可核验或自相矛盾 —— 拒绝交给建模。

    `raw` 带着最后一次的**完整**模型输出（没有的话是空串）。消息里只放
    得下末 400 字，而因子模式一次要输出几十行，故障往往在中段——只看
    尾巴等于让每一次排查都变成靠数字形状猜。调用方拿到它应落盘。
    """

    def __init__(self, *args, raw: str = ""):
        super().__init__(*args)
        self.raw = raw


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
# 水平模式（或公式用到 上期/基期(因子) 时）因子第 0 年的取值，AI 在门③ 补填。
_BASE_LINE = keyed("基期")
# 按百分比填的单位：必须带 %。pp 不带 %；其他单位（万吨、元…）按原数、不带 %。
_PCT_UNITS = frozenset({"变化率", "%", "百分比"})


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
    # 偏离多空平衡点时的说明。**代码只查存在性，不查合理性** —— 填得对不对
    # 由结构审查（factor_review）对账。它的作用是让「无视辩论」和「有理由地
    # 偏离」在产物上长得不一样：没有它的话，判断项中性档 18%（平衡点 12%）
    # 这件事，无论是模型看到了因子细节而上调、还是模型压根没读辩论，产物上
    # 完全一样。同构的实测案例：判断项被锚定覆盖（批准 19.12% → 实际用
    # −1.58%）之所以能被人看见，唯一原因是代码专门为它生成了一行说明。
    deviation: str = ""
    # 因子模式的取舍（放宽因子依据审核）：引文类依据（BasisKind.REPORT）
    # 逐字核验不过时不再致命——真实实跑（600584，9 因子×5 年×3 档=45
    # 行）显示这道闸门约拦下一半产出，卡点几乎都是模型对正文的**概括**
    # 而非**捏造**。非空时记下核验失败的具体原因，`describe()` 用它
    # 显著标出「依据未能逐字核验」，`check()` 不再因此 raise。
    # **不进 to_json/from_json**——它是 check() 每次重新核验时的派生
    # 结果，不是模型输出或人的决定，跨阶段没有持久化的必要，第二阶段
    # `run_valuation` 重跑 check() 会重新算出同样的标记。
    basis_unverified: str = ""
    # 门③ 标记（用户决定 A，2026-10-13）：依据引的是因子联网结论、而那条结论
    # 有数字在引用文章里找不到出处 / 引用的文章代码打不开。**可以作依据，但
    # 这一行必须带着标记** —— 与 `basis_unverified` 同一处理：派生结果，不进
    # to_json；第二阶段没有 factor_data，算出来恒为空，行为不变。
    basis_flag: str = ""


@dataclass
class BaseValue:
    """一个因子第 0 年的取值。`source`：「账本」（数据线索里 `[[key]]` 声明且
    账本有上年值，代码取）或「AI」（门③ 由 AI 补填并给依据）。"""

    value: Decimal
    bases: list = field(default_factory=list)
    note: str = ""
    source: str = "AI"
    basis_unverified: str = ""


def _decomp_to_dict(d: Decomposition) -> dict:
    return {"target": d.target, "op": d.op.value,
            "factors": [{"key": f.key, "label": f.label, "unit": f.unit,
                        "source_hint": f.source_hint,
                        "search_term": f.search_term} for f in d.factors]}


def _decomp_from_dict(d: dict) -> Decomposition:
    op = next(o for o in Op if o.value == d["op"])
    return Decomposition(
        target=d["target"], op=op,
        factors=[Factor(key=f["key"], label=f["label"], unit=f["unit"],
                        source_hint=f.get("source_hint", ""),
                        search_term=f.get("search_term", ""))
                for f in d["factors"]])


def to_json(a: "AssumptionSet", **extra) -> dict:
    """待核实清单落盘。**连依据一起存** —— 只存数值的话，第二阶段读回来
    的假设一律「未提供任何依据」，会被自己的门挡住。

    同时记下 AI 的原始取值: 人改过之后，原来的依据未必还支撑新数字，
    那件事必须被看见而不是悄悄放行。

    **分解模式下分解式与每行的 `factor_key` 必须一起存下来。** 漏掉它们
    不会立刻报错 —— 第二阶段照样能读出一个 AssumptionSet，只是因子分解
    悄悄消失，这正是本模块要防的「静默出错」。

    **schema 2（2026-10-14 公式化）**：公式化的分解式存进 `model`（模式、因子、
    常量、两条公式），因子第 0 年取值存进 `base_values`。旧的乘/加分解式仍存
    `decomps`，读回时翻译成公式（`formula.from_decomps`）。
    """
    m = a.model
    out = {"schema": 2, "labels": a.labels,
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
    if m is not None and not m.legacy:
        out["model"] = _model_to_dict(m)
    if a.base_values:
        out["base_values"] = {
            k: {"value": str(v.value), "bases": [b.to_dict() for b in v.bases],
                "note": v.note, "source": v.source}
            for k, v in a.base_values.items()}
    return out


def _model_to_dict(m: FormulaModel) -> dict:
    return {"mode": m.mode,
            "factors": [{"key": f.key, "label": f.label, "unit": f.unit,
                         "source_hint": f.source_hint,
                         "search_term": f.search_term, "short": f.short}
                        for f in m.factors],
            "constants": [{"name": c.name, "value": str(c.value),
                           "source": c.source} for c in m.constants],
            "formulas": {t: fm.text for t, fm in m.formulas.items()}}


def _model_from_dict(d: dict) -> FormulaModel:
    """读回公式化分解式。**构造即校验**：人手改坏的公式在这里给一句看得懂的
    话（「请重跑第一阶段」），不让第二阶段崩成 traceback。"""
    factors = [Factor(key=f["key"], label=f["label"], unit=f["unit"],
                      source_hint=f.get("source_hint", ""),
                      search_term=f.get("search_term", ""), short=f["short"])
               for f in d.get("factors") or []]
    consts = [_Constant(c["name"], Decimal(c["value"]), c.get("source", ""))
              for c in d.get("constants") or []]
    declared = {f.short for f in factors} | {c.name for c in consts}
    try:
        m = FormulaModel(
            mode=d.get("mode", ""), factors=factors, constants=consts,
            formulas={t: parse_formula(txt, t, declared)
                      for t, txt in (d.get("formulas") or {}).items()})
        m.validate()
    except FormulaInvalid as e:
        raise AssumeFailed(f"判断项里的分解式已不合法：{e}　——请重跑第一阶段，"
                           "重新预研分解式后再填判断项。") from e
    return m


def from_json(d: dict, code: str = "") -> tuple["AssumptionSet", list[str]]:
    """读回，并报出人改过哪些值。旧格式（没有 `model`）的乘/加分解式照旧读回。"""
    decomps = [_decomp_from_dict(x) for x in (d.get("decomps") or [])]
    model = _model_from_dict(d["model"]) if d.get("model") else None
    a = AssumptionSet(code=code or d.get("code", ""),
                      labels=d.get("labels") or [], decomps=decomps,
                      model=model)
    for k, v in (d.get("base_values") or {}).items():
        a.base_values[k] = BaseValue(
            value=Decimal(v["value"]),
            bases=[Basis.from_dict(b) for b in (v.get("bases") or [])],
            note=v.get("note", ""), source=v.get("source", "AI"))
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
                cn = _row_label(r, a._m() or decomps)
                edits.append(
                    f"{cn} 第{r.year}年 {case}：模型给 {p[case]}，"
                    f"核实后改为 {getattr(r, case)}")
    return a, edits


def _row_label(r: "Row", decomps) -> str:
    """人改过的值要报得出名字。分解模式下 `r.driver` 是 revenue/
    gross_margin，不是因子本身 —— 要报的是具体改了哪个因子（否则改了
    「产能」和改了「单价」在提示里会长得一模一样，都写成同一个笼统的
    目标名）。`decomps` 可以是旧分解式列表，也可以是 `FormulaModel`。"""
    if r.factor_key:
        for f in flat_factors(decomps):
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
    # 锚定开启时毛利率的按档、按年基期（`margin_bases`）。派生量，不进 JSON：
    # 第一阶段由 check() 按锚定输入算出，给清单展示用；第二阶段由 apply_to
    # 按当时的 calibrate 结果重算，给工作簿用——两处用的是同一个函数。
    margin_bases: dict | None = None
    # 增速路径的算术后果（`implied_scale`）。派生量，不进 JSON —— 由 check()
    # 算出并挂在这里，`describe()` 原样印在清单顶部。**算的人和显示的人用
    # 同一个对象**，否则清单上的数和校验用的数会分叉。
    implied: object = None
    # 因子结构审查结果（`factor_review.ReviewResult`）。派生量，不进 JSON。
    # 它**不是闸门**：只给结论与理由，印在清单上给批准人看。
    review: object = None
    # 假设的数据合理性辩论结果（`debate.DebateResult`）。派生量，不进 JSON。
    # **同样不是闸门**：成立的挑战不改任何取值，只印在清单上 —— 自动改数
    # 会绕过人工门，而这道门的意义正是让人负责。
    debate: object = None
    # I4：因子模式连续填不出合格判断项时，decide() 回落到两标量模式，
    # 这里记下降级原因。**不进 to_json/from_json**——降级是这一轮研究
    # 自己的事，不需要跨阶段持久化；describe() 会把它显式印在清单顶部，
    # 「降级但继续」不能悄悄发生，人必须一眼看到这轮其实是降级产出的。
    degraded: str = ""
    # 降级时最后一次的**完整**模型输出，供调用方落盘排查（见 AssumeFailed.raw）。
    # 不进 describe()——它是给人批准用的清单，不该混进几十行失败草稿。
    degraded_raw: str = ""
    # 公式化后的分解式（`formula.FormulaModel`）。新写法直接给；旧的 `decomps`
    # 由 `_m()` 惰性翻译。**合成、清单、落盘一律经它。**
    model: object = None
    # 因子第 0 年的取值 {短名: BaseValue}（水平模式，或公式用到 上期/基期(因子)）。
    base_values: dict = field(default_factory=dict)
    # 第 0 年营收（亿元，与工作簿同口径）。旧的乘法链与尺度无关，缺省按 1。
    base_revenue: Decimal | None = None

    def _m(self) -> FormulaModel | None:
        if self.model is None and self.decomps:
            self.model = from_decomps(list(self.decomps))
        return self.model

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
        if self.implied is not None:
            # **增速路径的算术后果，印在最前面。** 批准人看到的是三档数值
            # 和理由，而「中性档 71.75%」这种数看不出荒谬 —— 换算成「隐含
            # 市占率 39.2%，而当前前五名合计 47.13%」就看得出。生益科技
            # 实测: 那条路径过了单位检查、三表勾稽、140 格复算、每股价值
            # 量级对账与人工门，没有一道闸门把假设换算成它的后果。
            lines += [f"📐 **算术后果**：{self.implied.describe()}", ""]
        if self.review is not None and getattr(self.review, "flagged", None):
            # 审核意见印在清单顶部而不是末尾 —— 批准人要在看三档数值之前
            # 先知道「有哪几条已经被质疑过」。实测那条重复计算的因子若有
            # 这一行，批准时一眼就能看到「重叠」。
            lines += ["🔍 **因子结构审查**（审核者意见，未改任何数值）：", ""]
            lines += [f"- [{f.kind}] {f.subject}：**{f.verdict}** —— {f.why}"
                      for f in self.review.flagged]
            lines += [""]
        up = [c for c in getattr(getattr(self, "debate", None),
                                 "challenges", []) if c.upheld]
        if up:
            lines += ["⚖️ **假设辩论**（成立的挑战，**取值未因此改动**）：", ""]
            lines += [f"- [{c.kind.value}] {c.passage[:60]} —— "
                      f"{(c.rationale or c.objection).strip()[:140]}"
                      for c in up]
            lines += [""]
        m = self._m()
        if m is not None and not m.legacy:
            lines += self._describe_formula()
        elif m is not None:
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
                lines += self._factor_table(f)
            lines += self._describe_composed(d)
            lines += self._describe_residuals(d)
        lines += self._describe_estimated()
        lines += self._describe_unverified()
        return lines

    def _factor_table(self, f) -> list[str]:
        """一个因子的逐年表。新旧两种清单共用。"""
        lines: list[str] = []
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
            #
            # `basis_unverified` 非空——引文逐字核验不过、但因子
            # 模式下已放宽为标注而非驳回：这一格同样不能跟"核验
            # 通过"的行长得一样，否则放宽换来的就是"审核形同虚
            # 设却连标注都看不见"。引文本身（quote）仍然原样显示
            # ——它不是编造，只是没能逐字对应，人要看到具体写的
            # 是什么才能判断这段转述靠不靠谱。
            #
            # **理由自称「无数据（估计）」的行，挂了占位符也还是
            # 估计。** 600584 实跑当场抓到：4 个因子的理由白纸黑字
            # 写着无数据、取值为估计，依据列却挂着一个真实存在、
            # 方向上沾边的账本占位符（折旧摊销之于固定成本分摊、
            # 营收增速之于扩产节奏），于是判据 `not r.bases` 认为
            # 它们"有依据"，底部那份「没有证据支撑」的名单里五个
            # 该在的因子只剩一个。等于用一个合法但不直接支撑取值的
            # 占位符，把"这个数是拍的"从清单上抹掉——而清单是人工
            # 批准唯一能看的东西。占位符本身照常显示（它是真的、
            # 有参考价值），只是改称「方向性参照」，不再冒充
            # 「这个取值的证据」。
            est = _looks_like_estimate(r.note)
            parts: list[str] = []
            if est or not r.bases:
                parts.append("⚠ 估计（无数据支撑）")
            if r.basis_flag:
                parts.append("⚠ " + r.basis_flag)
            if r.bases:
                body = "；".join(b.describe() for b in r.bases)
                if r.basis_unverified:
                    parts.append("⚠ 依据未能逐字核验：" + body)
                elif est:
                    parts.append("方向性参照：" + body)
                else:
                    parts.append(body)
            ref = "；".join(parts)
            lines.append(
                f"| {lab} | {_fmt_factor(r.bear, f.unit)} "
                f"| {_fmt_factor(r.base, f.unit)} "
                f"| {_fmt_factor(r.bull, f.unit)} "
                f"| {r.note or '—'} | {ref} |")
        lines.append("")
        return lines

    def _describe_formula(self) -> list[str]:
        """公式化分解式的清单（spec 2026-10-14 三）：公式、常量、基期值、每个
        因子一张逐年表、三档逐年的合成结果、历史对账。"""
        m = self.model
        lines = [f"## 分解式（{m.mode}模式）", ""]
        lines += [f"- {m.describe(t)}" for t in ("revenue", "gross_margin")
                  if t in m.formulas]
        lines.append("")
        if m.constants:
            lines += ["**常量**", ""]
            lines += [f"- {c.name} = {c.value}（{c.source}）" for c in m.constants]
            lines.append("")
        need = m.needs_base()
        if need:
            lines += ["**基期值（第 0 年）**", "",
                      "| 因子 | 取值 | 来源 | 依据 |", "| --- | --- | --- | --- |"]
            for f in need:
                bv = self.base_values.get(f.short)
                if bv is None:
                    lines.append(f"| {f.label} | **未填写** | — | — |")
                    continue
                ref = "；".join(b.describe() for b in bv.bases)
                if not bv.bases or _looks_like_estimate(bv.note):
                    ref = "⚠ 估计（无数据支撑）" + (f"；{ref}" if ref else "")
                if bv.basis_unverified:
                    ref = "⚠ 依据未能逐字核验：" + ref
                lines.append(f"| {f.label} | {_fmt_factor(bv.value, f.unit)} "
                             f"| {bv.source} | {ref}{('（' + bv.note + '）') if bv.note else ''} |")
            lines.append("")
        for f in m.factors:
            lines += self._factor_table(f)
        lines += self._describe_synthesis()
        for t in ("revenue", "gross_margin"):
            lines += self._residual_lines(t)
        lines += self._describe_estimated()
        lines += self._describe_unverified()
        return lines

    def _describe_synthesis(self) -> list[str]:
        lines = ["## 合成结果（代码按公式算出，不是模型直接填的数）", ""]
        m = self.model
        if m.mode == "变化" and self.base_margin is None:
            return lines + ["基期毛利率未知，暂无法显示合成结果——因子取值仍已"
                            "生效，只是这里算不出用于展示的数。", ""]
        try:
            got = {c: synthesize(self, c, self.base_margin, self.margin_bases)
                   for c in (Case.BEAR, Case.BASE, Case.BULL)}
        except AssumeFailed as e:
            return lines + [f"算不出来：{e}", ""]
        lines += ["| 年 | 营收增速 悲观 | 中性 | 乐观 | 毛利率 悲观 | 中性 | 乐观 |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for y in range(len(got[Case.BASE][0])):
            lab = self.labels[y] if y < len(self.labels) else f"第{y+1}年"
            g = [got[c][0][y] for c in (Case.BEAR, Case.BASE, Case.BULL)]
            mg = [got[c][1][y] for c in (Case.BEAR, Case.BASE, Case.BULL)]
            lines.append(f"| {lab} | " + " | ".join(f"{x:.2%}" for x in g)
                         + " | " + " | ".join(f"{x:.2%}" for x in mg) + " |")
        lines.append("")
        lagged = {n for _, n in m.formulas["gross_margin"].lagged()}
        if self.margin_bases and "毛利率" not in lagged:
            lines += ["⚠ 半年报锚定只影响第 1 年：毛利率公式不读 上期/基期(毛利率)，"
                      "第 2 年起承接不上锚定值。", ""]
        return lines

    def _residual_lines(self, target: str) -> list[str]:
        info = self.residuals.get(target)
        if not info:
            return []
        cn = TARGET_LABEL.get(target, target)
        lines = [f"**{cn}历史回算残差**（仅供核对分解式抓没抓住主要矛盾，"
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

    def _describe_unverified(self) -> list[str]:
        """**遗漏必须可见**——这条约束不能因为放宽引文审核而丢。

        引文核验不过在因子模式下不再致命（见 `check()` 的取舍说明），
        但"哪些依据是软的"不能因此悄悄消失在各自因子的表格里：单独
        汇总一段，按因子、按年份列出，让人一眼看出哪些判断项的依据
        没有通过逐字核验、需要多担一分心去复核，而不是与核验通过的
        行混在一起被当成同等可信。"""
        labels = unverified_labels(self)
        if not labels:
            return []
        return ["⚠ **以下因子的依据未能逐字核验**"
                "（已如实标注，不阻断，需人工复核是否可信）："
                + "、".join(labels), ""]

    def _describe_estimated(self) -> list[str]:
        """**遗漏必须可见。** 因子取值若完全靠估计（出口二：无数据、
        没有证据支撑），不能只淹没在各自因子的表格里等人逐行去发现——
        单独汇总一段，让人一眼看出「这几个数是估的，没有证据支撑」，
        这正是当初做因子分解要保留的东西：与其在两标量模式下笼统地填
        一个营收增速，不如把"到底哪些因子有据、哪些是拍脑袋"摊开来看。
        """
        labels = estimated_labels(self)
        if not labels:
            return []
        return ["⚠ **以下因子的取值没有证据支撑，是估计**"
                "（已如实标注，理由见各自表格的「理由」列，不是遗漏）："
                + "、".join(labels), ""]

    def _synthesized_for(self, d: Decomposition, case: Case
                         ) -> list[Decimal] | None:
        """展示用的合成结果，**按目标单独判断能不能算**，不是「任一
        分解式缺基期就全部显示不出来」（I7）。

        营收增速永远算得出来，不需要基期毛利率；毛利率缺基期却硬要合成，会
        显示一个用假基期算出来、看起来正常的错数——比不显示更糟，所以只有
        这一条线才返回 None 让调用方改显示说明文字。
        """
        if d.target != "revenue" and self.base_margin is None:
            return None
        bm = self.base_margin if self.base_margin is not None else Decimal(0)
        g, m = synthesize(self, case, bm, self.margin_bases)
        return g if d.target == "revenue" else m

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
    pp 按原始数字显示，才能让写错单位的字面量露出马脚。
    其他单位（万吨、元…，公式化后允许）按「数值 + 单位」显示。"""
    if unit == "pp":
        return f"{v:+.2f}pp"
    if unit in _PCT_UNITS:
        return f"{v:.2%}"
    return f"{v:,} {unit}"


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


def _unit_guard(unit: str, label: str, cells: list[str]) -> None:
    """按单位挡字面量。C4：pp 与 % 不是一回事；I1：变化率必须显式带 %、不认 bp；
    公式化后的其他单位（万吨、元…）按原数，不带 % 也不带 bp。"""
    norm = [c.replace("％", "%") for c in cells]
    if unit == "pp":
        if any("%" in c for c in norm):
            raise AssumeFailed(
                f"因子「{label}」按 pp 计，不按 %：{' / '.join(cells)} 里带了 % "
                "—— pp 与 % 不是一回事，85%→82% 是 −3pp，写成 −3% 会被当成 "
                "−0.03pp，缩小 100 倍。请直接填数字（如 −3），不要带 %。")
        return
    if unit in _PCT_UNITS:
        for raw, n in zip(cells, norm):
            if "bp" in n.lower():
                raise AssumeFailed(
                    f"因子「{label}」按变化率（%）计，不认 bp：{raw} —— "
                    "bp 与 % 不是同一套单位，不做隐式换算，请直接换算"
                    f"成百分比再填（如 −350bp 请写成 −3.5%）。")
            # I-a（final-fix-D）：守卫必须复用 `_pct` 自己会用到的那个 token
            # 来判断，"0.5 %" 这样的空格骗不过去。
            if not (_pct_token(raw) or "").endswith("%"):
                raise AssumeFailed(
                    f"因子「{label}」按变化率计，必须带 %：{raw} —— "
                    "裸数会被当成小数直接使用而不是百分比（0.5 会被"
                    "解释成 50%，而不是你想表达的 0.5%），且这类错误"
                    "合成后看起来仍然合理，人从清单上发现不了。"
                    "请直接填百分比，如 −3.5%。")
        return
    for raw, n in zip(cells, norm):
        if "%" in n or "bp" in n.lower():
            raise AssumeFailed(
                f"因子「{label}」的单位是「{unit}」，按原数填，不要带 % 或 bp："
                f"{raw}")


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
                   decomps,
                   factor_data: dict | None = None,
                   as_of: date | None = None) -> AssumptionSet:
    """按因子解析。每一行对应分解式里的一个因子，而不是营收增速/毛利率
    本身 —— 合成交给 `synthesize`，模型只负责填因子。

    `decomps` 是旧分解式列表或 `FormulaModel`。因子按**短名或完整名**认（公式化
    后提示词让模型写短名）。另认 `基期: <短名> | <值> | <依据>` 行：需要第 0 年
    取值、而账本里没有的因子由 AI 补填（spec 2026-10-14 三）。
    """
    factor_data = factor_data or {}
    model = as_model(decomps)
    names: dict = {}
    for f in model.factors:
        names[f.label] = f
        if f.short:
            names.setdefault(f.short, f)
    target_of: dict[str, str] = {}
    for t in ("revenue", "gross_margin"):
        for f in model.factors_of(t):
            target_of.setdefault(f.key, t)
    out = AssumptionSet(code="", labels=[],
                        decomps=list(decomps) if isinstance(decomps, list) else [],
                        model=model)
    # C2：同一因子同一年出现两行，重复行本身说明模型在一次回复里生成了
    # 「修正前/修正后」两版，应当直接驳回重填，而不是替它决定该采信哪一行。
    seen: set[tuple[str, int]] = set()
    # 「同上」出口③按因子回指同一个 `factor_key` 之前处理过的那一行。
    last_basis: dict[str, list[Basis]] = {}
    for m in _LINE.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) < 6:
            raise AssumeFailed(
                f"假设一行字段不足（需 6 段：因子|年|悲观|中性|乐观|依据），"
                f"得到 {len(parts)}：{m.group(1)}")
        hit = pick(parts[0], list(names))
        if hit is None:
            raise AssumeFailed(
                f"未知的因子 {parts[0]!r}，本次分解式里只有："
                + "、".join(f.short or f.label for f in model.factors))
        f = names[hit]
        label = f.label
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
        _unit_guard(f.unit, label, parts[2:5])
        bases = _factor_basis(parts[5], label, factor_data.get(f.key),
                             as_of, prior=last_basis.get(f.key))
        last_basis[f.key] = bases
        out.rows.append(Row(
            driver=target_of.get(f.key, "factor"), year=year,
            bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
            bases=bases, note=_note(parts[5]), factor_key=f.key,
            basis_flag=_conclusion_flags(bases, factor_data.get(f.key))))
    for m in _BASE_LINE.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) < 3:
            raise AssumeFailed(
                f"基期一行字段不足（需 3 段：因子|取值|依据），得到：{m.group(1)}")
        hit = pick(parts[0], list(names))
        if hit is None:
            raise AssumeFailed(f"基期行里未知的因子 {parts[0]!r}")
        f = names[hit]
        if f.short in out.base_values:
            raise AssumeFailed(f"因子「{f.label}」的基期出现了不止一行，只保留一行。")
        _unit_guard(f.unit, f.label, [parts[1]])
        bases = _factor_basis(parts[2], f.label, factor_data.get(f.key), as_of)
        out.base_values[f.short] = BaseValue(
            value=_pct(parts[1]), bases=bases, note=_note(parts[2]), source="AI")
    return out


def anchor_from_cal(cal: dict) -> dict | None:
    """锚定反推各档首年毛利率所需的输入；没有中期、或 calibrate 没给三档时返回 None。

    三档（`share3` / `gm_rest3`）由 `ashare_calibrate.anchor_three` 定义，只在
    那里算一份——工作簿第三小节填的也是它，两边不会各算各的。
    """
    it = cal.get("interim") or {}
    annual = (cal.get("periods") or {}).get("annual") or []
    base_rev = (cal.get("rev") or {}).get(annual[-1]) if annual else None
    if not (it.get("rev") and it.get("cogs") and it.get("share3")
            and it.get("gm_rest3") and base_rev):
        return None
    return {"rev": float(it["rev"]), "cogs": float(it["cogs"]),
            "share3": tuple(it["share3"]), "gm_rest3": tuple(it["gm_rest3"]),
            "base_rev": float(base_rev)}


def _anchor_values(anchor: dict) -> dict[str, Decimal]:
    """半年报锚定后三档各自的第 1 年毛利率。四舍五入到 6 位，与旧 `margin_bases`
    逐位一致（工作簿用的也是这一份）。"""
    from ir_agent.dcf import anchored_year1

    out: dict[str, Decimal] = {}
    for i, name in enumerate(("bull", "base", "bear")):
        _, gm1 = anchored_year1(rev_h=anchor["rev"], cogs_h=anchor["cogs"],
                                share=float(anchor["share3"][i]),
                                gm_rest=float(anchor["gm_rest3"][i]),
                                base_rev=anchor["base_rev"])
        out[name] = Decimal(str(round(gm1, 6)))
    return out


def margin_bases(a: AssumptionSet, base_margin: Decimal,
                 anchor: dict | None) -> dict[str, list[Decimal]] | None:
    """锚定开启时 `基期(毛利率)` 的按档、按年取值；没有锚定返回 None。

    **生益科技实跑**：Base 档 2026 被半年报锚定在 30.69%，2027 却回到「年报
    基期 26.47% + 累计 pp」= 25.87%，一年凭空掉 4.8pp。加法链对全部年份用同
    一个年报基期，而首年已被实际数覆盖——两个起点混用了。

    第 1 年仍用基期（该年由工作簿锚定覆盖）；第 2 年起把基期平移为
    `基期 + (锚定值 − 第 1 年公式值)`，即「让第 1 年公式结果正好等于锚定值」的
    基期（spec 2026-10-14「写计划时的补充」2）。旧加法链下就是「锚定值 − 第 1 年
    累计 pp/100」，与改动前逐位一致。
    """
    if anchor is None or a._m() is None:
        return None
    anchors = _anchor_values(anchor)
    out: dict[str, list[Decimal]] = {}
    for c in (Case.BULL, Case.BASE, Case.BEAR):
        p = _simulate(a, c, base_margin)
        if not p.margin:
            return None
        name = c.name.lower()
        shifted = p.margin0 + (anchors[name] - p.margin[0])
        out[name] = [p.margin0] + [shifted] * (len(p.margin) - 1)
    return out


def _values_for(a: AssumptionSet, case: Case) -> dict[str, list[Decimal]]:
    """某一档每个因子第 1..N 年的取值 {短名: [...]}。同一因子同一年若有重复行，
    后写入覆盖先写入（与改动前的字典推导式同一规则）。"""
    attr = {Case.BULL: "bull", Case.BASE: "base", Case.BEAR: "bear"}[case]
    m = a._m()
    short_of = {f.key: f.short for f in m.factors}
    by: dict[tuple[str, int], Decimal] = {}
    for r in a.rows:
        if r.factor_key in short_of:
            by[(short_of[r.factor_key], r.year)] = getattr(r, attr)
    years = max((y for _, y in by), default=0)
    out: dict[str, list[Decimal]] = {}
    for f in m.factors:
        seq: list[Decimal] = []
        for y in range(1, years + 1):
            if (f.short, y) not in by:
                break
            seq.append(by[(f.short, y)])
        out[f.short] = seq
    return out


def _simulate(a: AssumptionSet, case: Case, base_margin: Decimal | None,
              anchor_y1: Decimal | None = None):
    m = a._m()
    base_rev = a.base_revenue
    if base_rev is None and m.legacy:
        base_rev = Decimal(1)          # 旧乘法链与尺度无关
    try:
        return simulate(m, _values_for(a, case),
                        base_factors={k: v.value for k, v in a.base_values.items()},
                        base_revenue=base_rev, base_margin=base_margin,
                        anchor_y1=anchor_y1)
    except FormulaError as e:
        raise AssumeFailed(f"合成算不下去：{e}") from e


def synthesize(a: AssumptionSet, case: Case, base_margin: Decimal,
               bases: dict | None = None) -> tuple[list[Decimal], list[Decimal]]:
    """把因子合成回逐年营收增速与毛利率。**代码算，不是模型填。**

    若让模型自己算出结果再填进去，分解就退化成一层装饰 —— 它可以先想好
    结果，再倒推几个因子凑出来。所以这里真的从因子值算，模型只填因子。

    `bases`（`margin_bases` 的结果，锚定开启时给）第 2 年起的值是平移后的
    `基期(毛利率)`；这里把它换回该档的锚定值再推演，两者是同一件事的两种写法。

    注意: 本函数本身**不做单调性校验** —— 校验属于 `check`。
    """
    p = _simulate(a, case, base_margin)
    if bases:
        seq = bases[case.name.lower()]
        if len(seq) > 1 and p.margin:
            p = _simulate(a, case, base_margin,
                          anchor_y1=seq[1] - p.margin0 + p.margin[0])
    return p.growth, p.margin


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


# 引文核验的规范化从 sources/external.py 导入——「同一套规范化」此前
# 是三份各自为政的实现靠 docstring 互相声称，见那边的注释。
_norm = norm


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


def _conclusion_flags(bases: list[Basis], fd: FactorData | None) -> str:
    """依据若引的是因子联网结论，带出那条结论的 ⚠（`Conclusion.flags()`）。

    按 Basis 反查而不是在 `_factor_basis` 里顺手记：「同上」的行复用上一行的
    Basis 列表，按 Basis 查自然得到同一份标记。"""
    if fd is None:
        return ""
    out: list[str] = []
    for b in bases:
        if b.kind is not BasisKind.EXTERNAL:
            continue
        flags = getattr(_match_finding(fd, b.quote), "flags", None)
        if flags is not None and (s := flags()) and s not in out:
            out.append(s)
    return "；".join(out)


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

    只对 ANNUAL/ANNOUNCE/REPORT 三种状态生效——DISCLOSED/MISSING/EXTERNAL
    没有这个字段（默认空串），该走别的出口（findings 或"无数据"）。
    """
    if fd is None or fd.status not in (gaps.ANNUAL, gaps.ANNOUNCE, gaps.REPORT):
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


# 隐含份额的阻断阈值。
#
# **生益科技 600183 实测。** 分解式「营收 = 销量增速 × 售价增速 × 高频高速
# 收入增速」重复计算（销量×售价已是收入），三个约 20% 的因子相乘得到中性档
# 71.75%，五年累计 8.05 倍 —— 而它过了全部闸门: 单位检查、三表勾稽、140 格
# 复算、每股价值量级对账（183/130 = 1.4 倍，在容差内）、人工门。
#
# **71.75% 看不出荒谬，「隐含市占率 39.2%」看得出。** 这道检查的全部作用是
# 把见解之争换成算术之争: 不问「这个增速高不高」，问「这个份额可能吗」。
# 份额相对倍数的上限：隐含份额 ÷ 当前份额。
#
# **判据从「份额的绝对水平」改成「份额的相对倍数」。** 离线消融（10 份历史
# 判断项）证明旧判据是惰性的: 按「隐含份额 > 2×当前份额 或 > CR5」，10 份
# **一次都不触发** —— 因为阈值随份额水平走:
#
#     生益科技 份额 8.9%  → 阈值 17.8%，实测最高隐含 14.8%  永不触发
#     贵州茅台 份额 47.1% → 阈值 94.2%/CR5 81.7%，实测 46%  永不触发
#     牧原股份 份额 45.1% → 阈值 90.3%，实测 35%            永不触发
#
# 同一条规则对龙头极宽松、对小公司极严苛，而这完全是「2 倍当前份额」这个
# 形式造成的，不是想要的语义。改用相对倍数后与份额水平无关:
#
#     10 份存活样本的相对倍数 0.78 ~ 1.66   全部通过
#     那个五年 8.05 倍营收的灾难案例  4.4   触发
#
# 阈值 2.0 = 「五年里份额相对行业翻一倍」。再高的增速不是不可能，但它需要
# 人看一眼，而不是默默进模型。
SHARE_RATIO_MAX = Decimal(2)


@dataclass(frozen=True)
class ImpliedScale:
    """增速路径的算术后果。`None` 表示账本缺份额事实，算不出来就不算。"""

    rates: tuple
    multiple: Decimal               # 预测期营收累计倍数 Π(1+r)
    share_now: Decimal
    implied_share: Decimal
    industry_multiple: Decimal
    industry_growth: Decimal        # 实际用于外推的行业增速（已取下限 0）
    clamped: bool                   # 行业增速是否被下限截断
    cr5: Decimal | None

    @property
    def years(self) -> int:
        return len(self.rates)

    @property
    def blocking(self) -> bool:
        return bool(self.reason)

    @property
    def share_ratio(self) -> Decimal:
        """份额相对倍数 = 隐含份额 ÷ 当前份额。**与份额水平无关的判据。**"""
        return self.implied_share / self.share_now

    @property
    def reason(self) -> str:
        """空串 = 通过。非空 = 阻断理由，会原样进驳回信息。"""
        if self.implied_share > 1:
            return (f"隐含份额 {self.implied_share:.0%} 超过 100% —— "
                    "数学上不可能，这条增速路径自相矛盾")
        if self.cr5 is not None and self.implied_share > self.cr5:
            return (f"隐含份额 {self.implied_share:.1%} 超过当前前五名合计 "
                    f"CR5 {self.cr5:.1%} —— 等于一家吃掉接近整个头部")
        if self.share_ratio > SHARE_RATIO_MAX:
            return (f"份额相对倍数 {self.share_ratio:.2f}（隐含份额 "
                    f"{self.implied_share:.1%} ÷ 当前份额 {self.share_now:.1%}）"
                    f"超过 {SHARE_RATIO_MAX:.0f} —— 这条增速路径要求公司在"
                    f"五年内把相对行业的份额翻一倍以上")
        return ""

    def describe(self) -> str:
        """给人核对的中间量。**只给结论无从复核。**"""
        ind = (f"行业增速按 {self.industry_growth:.1%} 外推"
               if not self.clamped else
               "行业增速按 0 外推（账本里最近一期为负，照它外推五年会让行业"
               "凭空缩水、隐含份额虚高，那是外推假设的误报）")
        cr5 = (f"CR5 {self.cr5:.1%}" if self.cr5 is not None else "CR5 未取到")
        return (f"预测期营收累计 {self.multiple:.2f} 倍（{self.years} 年），"
                f"{ind}，隐含份额 {self.implied_share:.1%}"
                f"（当前份额 {self.share_now:.1%}，相对倍数 "
                f"{self.share_ratio:.2f}，{cr5}）")


def implied_scale(rates: list[Decimal], *, share_now: Decimal | None,
                  cr5: Decimal | None,
                  industry_growth: Decimal | None) -> ImpliedScale | None:
    """把增速路径换算成隐含市占率。缺份额事实则返回 None。

    行业增速取下限 0: 账本里最近一期为负时照它外推五年，行业会缩掉一半、
    隐含份额凭空翻倍 —— 那是外推假设造成的误报，不是公司的问题。取 0 的
    方向是**让隐含份额更小**，也就是更不容易阻断，与「宁可漏报不可误报」
    一致（一个常响的阻断会被学会绕过）。
    """
    if share_now is None or share_now <= 0 or not rates:
        return None
    mult = Decimal(1)
    for r in rates:
        mult *= (Decimal(1) + Decimal(r))
    g = Decimal(industry_growth or 0)
    clamped = g < 0
    if clamped:
        g = Decimal(0)
    ind = (Decimal(1) + g) ** len(rates)
    return ImpliedScale(rates=tuple(rates), multiple=mult, share_now=share_now,
                        implied_share=share_now * mult / ind,
                        industry_multiple=ind, industry_growth=g,
                        clamped=clamped, cr5=cr5)


def _latest(ledger: FactLedger, key: str, as_of: date) -> Decimal | None:
    """账本里某个 key 最新一期的值；取不到返回 None（不猜）。"""
    pers = sorted({p for k, p in ledger._facts if k == key})   # noqa: SLF001
    for per in reversed(pers):
        try:
            return ledger.get(key, per, as_of=as_of).value
        except Exception:                                      # noqa: BLE001
            continue
    return None


def _implied_for(a: AssumptionSet, ledger: FactLedger, as_of: date,
                 decomps=None):
    """中性档营收路径的隐含份额。算不出来就返回 None。"""
    if as_model(decomps) is None:
        rates = [r.base for r in sorted(a.rows, key=lambda x: x.year)
                 if r.driver == "growth"]
    else:
        bm = a.base_margin if a.base_margin is not None else Decimal(0)
        rates, _ = synthesize(a, Case.BASE, bm)
    if not rates:
        return None
    return implied_scale(
        list(rates),
        share_now=_latest(ledger, "company.market_share", as_of),
        cr5=_latest(ledger, "industry.cr5", as_of),
        # **优先用多年 CAGR，而不是最近单年。** 实测（生益科技
        # 2026-10-06）: `industry.revenue_yoy` 取到的是 AI 爆发那一年的
        # 25.8%，把一个景气年外推五年会让行业涨 3.15 倍，分母被撑大，
        # 隐含份额算出来反而低于当前份额 —— 这道检查在景气行业里接近失效。
        industry_growth=(_latest(ledger, "industry.revenue_cagr", as_of)
                         or _latest(ledger, "industry.revenue_yoy", as_of)))


# 判断项与平衡点的容差。小于它视为「采纳了平衡点」，不要求说明 ——
# 模型为了对齐小数位微调一两个基点，不该被当成偏离。
DEVIATION_TOL = Decimal("0.01")


def check_deviation(a: AssumptionSet, balances: list,
                    tol: Decimal = DEVIATION_TOL) -> list[str]:
    """判断项偏离平衡点时，该行必须填 `deviation`。返回问题列表。

    **只查存在性。** 填得对不对由结构审查对账 —— 代码能确定判断的是「有没有
    填」，不是「这个理由站不站得住」。实测教训: 隐含份额检查想用算术判断一件
    该由语义判断的事，10 份存档上 10/10 惰性。

    没有平衡点（辩论失败后人工补写为空）或平衡点是纯文字（辩论①）时，这条
    检查不生效 —— 不能凭空要求说明。

    **按 `_row_label` 取名字，不要直接读 `r.factor_key`。** 两标量降级模式下
    `factor_key` 是空串，而那时辩论② 的 subjects 用的是 `DRIVERS` 的键
    （「营收增速」/「毛利率」）—— 直接比 `factor_key` 会让这条检查在降级
    路径上恒不生效，而 Global Constraints 要求它一律生效。`_row_label`
    已经做了这件事：分解模式下把 factor_key 解析成因子标签，两标量模式下
    返回中文驱动名。
    """
    mids = {b.subject: b.mid for b in balances
            if getattr(b, "mid", None) is not None}
    if not mids:
        return []
    out: list[str] = []
    for r in sorted(a.rows, key=lambda x: (x.factor_key, x.year)):
        name = _row_label(r, a._m() or a.decomps or [])
        mid = mids.get(name)
        if mid is None:
            continue
        if abs(r.base - mid) <= tol or r.deviation.strip():
            continue
        out.append(
            f"第 {r.year} 年「{name}」的中性档 {r.base} 偏离多空平衡点 "
            f"{mid}（容差 {tol}），但没有给出偏离说明 —— 请在该行的第 7 字段"
            f"填一句话说明为什么偏离；若采纳平衡点，把取值改成 {mid}。")
    return out


def check(a: AssumptionSet, years: int, *, ledger: FactLedger, body: str,
          as_of: date, decomps: list[Decomposition] | None = None,
          base_margin: Decimal | None = None, anchor: dict | None = None,
          balances: list | None = None) -> None:
    model = as_model(decomps)
    if model is not None:
        a.model = model
        if a.base_margin is None:
            a.base_margin = base_margin
        _fill_from_ledger(a, ledger, as_of)
        _check_factors(a, years, decomps, base_margin, anchor)
    else:
        _check_scalar(a, years)
    # 多空平衡点的偏离说明。**接在隐含份额之前** —— 偏离说明缺失是格式问题，
    # 隐含份额越界是实质问题，先让模型把格式补齐，驳回信息才不会混在一起。
    if balances:
        if (probs := check_deviation(a, balances)):
            raise AssumeFailed("；".join(probs))
    # **增速路径的算术后果。** 阻断的含义是退回让模型重算（本函数抛
    # AssumeFailed 会进 `_decide_once` 的重试循环并把理由附进提示词），
    # 不是毙掉整轮 —— 连续失败后 `decide()` 仍会降级到两标量模式。
    a.implied = _implied_for(a, ledger, as_of, decomps)
    if a.implied is not None and a.implied.blocking:
        raise AssumeFailed(
            f"中性档营收路径的算术后果不成立：{a.implied.reason}。"
            f"（{a.implied.describe()}）"
            "请下调各因子的中性档取值，使隐含份额落在可能的范围内；"
            "若分解式本身把同一个量算了两遍（例如销量×售价之外又乘了一个"
            "收入量级的增速），请在理由里指出，并按不重复计算的口径给值。")

    # 出口二（「无数据（估计）」）合法时 `Row.bases` 就是空列表——
    # `basis.validate([])` 的原意是防"整批一条依据都没给"，但因子模式
    # 下"每一行都被 `_factor_basis` 判定为合法的无数据估计"是一种真实
    # 可能出现、且**应当被允许**的情形（分解式里的因子确实可能全部
    # 缺数据），不是"没做功课"。这种情形已经在 `_factor_basis`/
    # `_check_factors` 里逐行验过一遍（确实 MISSING、且有理由），不需要
    # 再套用这条本来是给"完全没有产出"设的全局闸门；反过来，只要有任何
    # 一行不是走这条出口，就仍然照常校验，不放过真正缺依据的情形。
    all_estimated = bool(a.rows) and decomps and all(not r.bases for r in a.rows)
    if all_estimated:
        return
    # 上面那条出口之外，"整批一条依据都没给"这道全局闸门必须原样保留。
    # 放宽把整批核验（`validate(flat)`）改成了逐行逐依据核验，而逐行
    # 循环对空 `bases` 的行直接跳过——因子模式没事（空 `bases` 是
    # 「无数据（估计）」这条合法出口，`_check_factors` 另有"没给理由
    # 就驳回"兜底），**两标量模式却没有任何兜底**：`_parse_scalar` 里
    # 缺依据必然抛错，但 `check()` 会在人工核实之后、`from_json` 读回
    # 时脱离解析器再跑一遍（`full.run_valuation`），人手把 `bases` 清空
    # 就能一路通过。这道门守的是真实踩过的事故（见
    # `test_依据跨阶段存活`：依据没跨阶段带过去，读回来一律"未提供任何
    # 依据"而拒绝建模——门是对的）。这次放宽只针对因子引文，不该顺手
    # 把它拆掉。
    if not any(r.bases for r in a.rows):
        raise AssumeFailed("依据不可核验：未提供任何依据。")
    # **放宽因子模式的引文审核（用户明确决定）**：真实实跑（600584，
    # 9 因子×5 年×3 档=45 行）显示逐字引文这道闸门约拦下一半产出，卡点
    # 几乎都是模型对正文含义的**概括**而非**捏造**——「上游议价能力弱
    # 意味着公司对设备与原材料价格波动的传导能力有限」「修复但未完全
    # 追平」，这两句正文里都没有逐字对应，却也不是编出来的。规则要求
    # 逐字，连续 3 次被拒后整个因子分解被丢掉、退回两标量模式——而
    # 因子分解正是这条产品线的全部意义。用户判断：因子该按数值是否
    # 合理来审，不该被引文格式卡死。
    #
    # 因此逐 Basis 校验（而不是像原来那样把全部 Basis 拼成一个 `flat`
    # 列表一次性核验）：这样才能按 `BasisKind` 精确分流——只有 REPORT
    # （引正文逐字核验不过）在因子模式下降级为标注在对应 `Row` 上；
    # FACT（占位符指向的账本事实不存在）与 EXTERNAL（外部资料核验
    # 不过）继续致命，理由见下面分支的注释。两标量模式（`decomps`
    # 为空）完全不受影响——那条路径引的是正文，正文本来就覆盖增速/
    # 毛利率两项，逐字核验在那里可满足，这次放宽只针对因子行。
    fatal: list[str] = []
    for short, bv in a.base_values.items():
        if bv.source != "AI" or not bv.bases:
            continue
        soft_b: list[str] = []
        for b in bv.bases:
            problems = validate([b], ledger=ledger, body=body, as_of=as_of)
            if not problems:
                continue
            if b.kind is BasisKind.REPORT:
                soft_b.extend(problems)
            else:
                fatal.append(f"基期 {short}：" + "；".join(problems))
        if soft_b:
            bv.basis_unverified = "；".join(soft_b)
    for r in a.rows:
        if not r.bases:
            continue
        soft: list[str] = []
        for b in r.bases:
            problems = validate([b], ledger=ledger, body=body, as_of=as_of)
            if not problems:
                continue
            if decomps and b.kind is BasisKind.REPORT:
                soft.extend(problems)
            else:
                # FACT：占位符格式合法但指向的 key@period 在账本里不存在
                # ——这是对账本的**假陈述**，且下游会拿占位符去做字符串
                # 替换，编造的占位符会让替换失败或替换出错的值（实测踩
                # 过整段估值漏成未替换占位符）。EXTERNAL：外部资料核验
                # 不过。两者都与"对正文的转述 vs 逐字"不是一类问题，
                # 不在这次放宽范围内，继续致命。
                fatal.append(f"{_row_label(r, a._m() or a.decomps)} 第{r.year}年："
                            + "；".join(problems))
        if soft:
            r.basis_unverified = "；".join(soft)
    if fatal:
        raise AssumeFailed("依据不可核验：" + "；".join(fatal))


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


def _fill_from_ledger(a: AssumptionSet, ledger: FactLedger, as_of: date) -> None:
    """第 0 年取值里账本拿得到的部分（spec 2026-10-14 决定 9：账本有就用账本）。

    - 需要基期值的因子：数据线索里 `[[key]]` 声明、账本有上年值的，代码取；
    - 营收₀：账本最近一个完整年度的营收，换算成亿元（与工作簿同口径）。
    """
    from ir_agent import gaps

    m = a._m()
    for f in m.needs_base():
        have, _ = gaps.ledger_refs(f.source_hint, ledger, as_of)
        if not have:
            continue
        key, per = have[0].rsplit("@", 1)
        a.base_values[f.short] = BaseValue(
            value=ledger.get(key, per, as_of=as_of).value,
            bases=[Basis.fact(f"[[{key}@{per}]]")], source="账本")
    if a.base_revenue is None:
        pers = annual_periods(ledger, as_of, years=1)
        if pers:
            try:
                a.base_revenue = (ledger.get("revenue", pers[-1], as_of=as_of).value
                                  / Decimal(10 ** 8))
            except (KeyError, LookAheadError):
                pass


def _check_factors(a: AssumptionSet, years: int, decomps,
                   base_margin: Decimal | None, anchor: dict | None = None) -> None:
    """分解模式下的校验。

    **不在这里逐因子比较 bear<base<bull。** 单个因子档位的方向性因子而异，
    代码判断不了「这个因子往哪个方向走算悲观」。真正要卡的是**合成结果**——
    三档营收增速 / 毛利率必须严格拉开。
    """
    # **读回的旧分解式要重过闸门。** `_decomp_from_dict` 直接构造对象，预研时的
    # 闸门在读回路径上一次都没跑。只对旧的乘/加分解式（公式化写法不跑这些字面
    # 闸门，spec 2026-10-14 五）。
    if isinstance(decomps, list):
        for d in decomps:
            try:
                check_dimensions(d)
            except DecompositionInvalid as e:
                raise AssumeFailed(
                    f"判断项里的分解式已不合法：{e}　——请重跑第一阶段，"
                    "重新预研分解式后再填判断项。") from e
    m = a._m()
    label_of = {f.key: f.label for f in m.factors}
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
        # 出口二（「无数据（估计）」）落地为空的 `bases`——那不能免除「说明
        # 依据什么估的」这一要求：空依据 + 空理由 = 一个数字凭空出现在清单上。
        if not r.bases and not r.note.strip():
            raise AssumeFailed(
                f"因子「{label_of.get(r.factor_key, r.factor_key)}」"
                f"第 {r.year} 年写了「无数据（估计）」却没有给理由——"
                "理由要说明这个估计依据什么给出（参照哪个可比公司、"
                "哪个行业惯例……），不能只留一句「无数据」。")

    for f in m.factors:
        got = {r.year for r in a.rows if r.factor_key == f.key}
        missing = sorted(set(range(1, years + 1)) - got)
        if missing:
            raise AssumeFailed(
                f"因子「{f.label}」缺第 {missing} 年 —— 预测期每一年都要给，"
                "漏掉的年份会沿用模板默认值，而那不是你的判断")

    lacking = [f for f in m.needs_base() if f.short not in a.base_values]
    if lacking:
        raise AssumeFailed(
            "以下因子需要第 0 年（基期）的取值，账本里没有："
            + "、".join(f"{f.short}（{f.label}）" for f in lacking)
            + " —— 请各补一行「基期: <短名> | <值> | <依据>」（已发生的实际值，"
            "单值不分档）。")

    # **合成后再验一次单调。** 这是本模块真正的闸门。
    if base_margin is None:
        base_margin = Decimal("0.10")
    if isinstance(decomps, list):
        a.decomps = list(decomps)
    # 锚定开启时查的必须是平移后的路径——那才是建模用的。
    a.margin_bases = margin_bases(a, base_margin, anchor)
    _check_monotonic(a, base_margin, a.margin_bases)


def _check_monotonic(a: AssumptionSet, base_margin: Decimal,
                     bases: dict | None) -> None:
    """合成后三档必须严格拉开。check() 与 apply_to 共用：第二阶段的 check()
    拿不到 calibrate 结果、不做锚定平移，平移发生在 apply_to，那里要再查一次，
    不能让不合格的路径进工作簿。"""
    lo, lom = synthesize(a, Case.BEAR, base_margin, bases)
    mid, midm = synthesize(a, Case.BASE, base_margin, bases)
    hi, him = synthesize(a, Case.BULL, base_margin, bases)
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


# 依据的写法规则。旧的乘/加清单与公式化清单共用这一段，不各写一份。
_BASIS_RULES = (
              "**引文只能出自三处**：你的分析简报正文、账本占位符 "
              "[[key@period]]、或下表「数据」列里这个因子自己采集到的"
              "原文片段/发现。下面各节（宏观、行业、可比公司、外部资料、"
              "产业链……）、本表的因子名和单位这些表头，都只是给你判断"
              "用的上下文，**不是**可引用的正文——从里面摘一句当引文"
              "（哪怕摘的是某节小标题）核验不过，会被标注为「依据未能"
              "逐字核验」，交人工额外复核。",
              "**优先给出可逐字核验的依据**：账本占位符 [[key@period]]、"
              "正文原话、下表这个因子自己采集到的证据原文，或"
              "「无数据（估计）」「同上」回指——这几类代码都能逐字反查，"
              "人工批准时不用再多看一眼。不要用省略号（…/...）缩写引文，"
              "也不要把原文里不相邻的句子拼起来当一句引文。",
              "**引文核验不过不会被驳回，但会被标注。** 因子分解要填的"
              "行很多（一个分解式常有好几个因子、好几个预测年），逐字"
              "引文有时确实凑不出来——若引文没能在来源里逐字找到，这一"
              "行仍会通过，只是会在待核实清单上被显著标出「依据未能"
              "逐字核验」，与核验通过的行一眼可分，需要人工多担一分"
              "风险去复核。**这不等于随便写都行**：标注是有代价的——"
              "能给出逐字引文就一定给逐字引文，只有在你要表达的意思在"
              "来源里确实没有逐字对应的句子时，才退而求其次转述，并把"
              "推理写进理由列；转述得越多，清单上被标注的行就越多，人工"
              "复核的负担就越重。",
              "**这条放宽只针对引文本身，账本占位符不受影响**："
              "`[[key@period]]` 必须真实指向账本里存在的事实，写错的"
              "占位符（账本里根本没有这个 key/period）依旧会被直接驳回"
              "——那是对账本的假陈述，下游会拿它去做字符串替换，错了会"
              "让整段估值替换失败，跟「转述 vs 逐字」不是一类问题。",
              "",
              "**依据除了「正文原话」「[[占位符]]」，还有三条路——"
              "很多因子正文根本没提过，也不在账本里，这些路才是它们"
              "免于被标注、能拿到确凿出处的依据写法**：",
              "① 上表「数据」列若给出了具体的原文片段或「发现」，你可以"
              "**逐字**引用那一段（仍然写成「引文」的样子），出处（网址/"
              "抓取日期）由代码自动核对并填上——不要自己写网址或日期，"
              "写了也不会被采信；一个字都对不上会退回按正文引用处理，"
              "适用上面「核验不过会被标注」的规则。",
              "② 上表「数据」列若是「无数据」（或整行都没有），可以直接"
              "写「无数据（估计）」，但理由里**必须**说明这个估计是怎么"
              "来的（参照哪个可比公司、哪个行业惯例、哪条常识……）——"
              "「无数据」只对确实没有数据支撑的因子开放，对「已披露」"
              "「年报披露」「公告披露」「研报测算」「外部来源」的因子这么写"
              "会被直接驳回，不能图省事整表都写「无数据」。",
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
)


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
              *_BASIS_RULES,
              "",
              "对每一个因子、每一年给出一行：",
              "`假设: <因子名> | 第N年 | <悲观> | <中性> | <乐观> | "
              "「正文原话/证据原文」或 [[占位符]] 或 无数据（估计）"
              "或 同上`"]
    return "\n".join(lines)


def _formula_head(m: FormulaModel, factor_data: dict,
                  ledger_bases: dict | None = None) -> str:
    """公式化分解式的提示词头部（spec 2026-10-14 三）。旧的乘/加分解式仍用
    `_factor_head`，提示词与改动前一字不差。"""
    ledger_bases = ledger_bases or {}
    lines = [f"**本次按公式化分解填写（{m.mode}模式）。**",
             ("每个因子逐年填**变化**（增速、pp 变动…），营收与毛利率的第 0 年取账本。"
              if m.mode == "变化" else
              "每个因子逐年填**水平**（量、价、成本…），第 0 年的营收与毛利率由基期"
              "因子值代入公式算出。"),
             "", "公式："]
    lines += [f"  {m.describe(t)}" for t in ("revenue", "gross_margin")
              if t in m.formulas]
    if m.constants:
        lines += ["", "常量："]
        lines += [f"  {c.name} = {c.value}（{c.source}）" for c in m.constants]
    lines += ["", "逐个因子填，代码会按公式把它们合成回营收增速与毛利率——",
              "**不要自己算出增速或毛利率再倒推因子**，那样分解就没有意义。",
              "", "| 短名 | 因子 | 单位 | 数据 |", "| --- | --- | --- | --- |"]
    for f in m.factors:
        fd = factor_data.get(f.key)
        st = "未采集" if fd is None else (f"{fd.status}（{fd.note}）"
                                         if fd.note else fd.status)
        lines.append(f"| {_esc_pipe(f.short)} | {_esc_pipe(f.label)} "
                     f"| {_esc_pipe(f.unit)} | {_esc_pipe(st)} |")
    need = m.needs_base()
    if need:
        have = [f for f in need if f.short in ledger_bases]
        todo = [f for f in need if f.short not in ledger_bases]
        lines += ["", "**基期值（第 0 年）**："]
        lines += [f"  - {f.short} = {ledger_bases[f.short].value}（账本，已取到）"
                  for f in have]
        if todo:
            lines.append("  - 需要你补：" + "、".join(f.short for f in todo)
                         + " —— 各补一行 `基期: <短名> | <值> | <依据>`。基期是"
                         "已经发生的实际值，单值，不分三档；依据规则与下面相同。")
    lines += ["",
              "**单位规则**：单位是变化率或 % 的，取值带 %（如 −3.5%）；单位是 pp "
              "的，填数字不带 %（如 −3）；其他单位（万吨、元/平方米…）按原数填，"
              "不带 %。",
              "",
              *_BASIS_RULES,
              "",
              "对每一个因子、每一年给出一行（因子写短名）：",
              "`假设: <短名> | 第N年 | <悲观> | <中性> | <乐观> | "
              "「正文原话/证据原文」或 [[占位符]] 或 无数据（估计）或 同上`"]
    return "\n".join(lines)


def build_prompt(judg: dict, labels: list[str], context: str,
                 brief: str = "", decomps=None,
                 factor_data: dict | None = None,
                 ledger_bases: dict | None = None) -> str:
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
    m = as_model(decomps)
    if m is None:
        head = _SCALAR_HEAD
    elif m.legacy:
        head = _factor_head(decomps, factor_data or {})
    else:
        head = _formula_head(m, factor_data or {}, ledger_bases)
    return PROMPT.format(head=head, years=len(labels), labels="、".join(labels),
                         history="\n".join(hist) or "  （无）",
                         context=context, brief=brief or "（无）")


def _decide_once(client, judg: dict, labels: list[str], context: str, *,
                 ledger: FactLedger, body: str, as_of: date, code: str,
                 max_attempts: int, decomps,
                 factor_data: dict | None,
                 base_margin: Decimal | None,
                 anchor: dict | None = None,
                 balances: list | None = None) -> AssumptionSet:
    """单一模式（因子或两标量）内的重试循环。

    只管一种模式，失败就抛 `AssumeFailed`——**模式之间要不要降级，是
    `decide()` 的职责，不是这里的**：因子模式连续失败后可以退回两标量
    模式再试一次，但两标量模式本身连续失败没有更低的台阶可退，必须
    原样拒绝。两件事分开才不会在这里意外递归。
    """
    model = as_model(decomps)
    ledger_bases = {}
    if model is not None and not model.legacy:
        probe = AssumptionSet(code="", labels=[], model=model)
        _fill_from_ledger(probe, ledger, as_of)
        ledger_bases = probe.base_values
    prompt = build_prompt(judg, labels, context, brief=body, decomps=decomps,
                          factor_data=factor_data, ledger_bases=ledger_bases)
    last, raw = "", ""
    for attempt in range(1, max_attempts + 1):
        raw = client(prompt)
        try:
            a = parse(raw, len(labels), decomps=decomps,
                     factor_data=factor_data, as_of=as_of)
            check(a, len(labels), ledger=ledger, body=body, as_of=as_of,
                  decomps=decomps, base_margin=base_margin, anchor=anchor,
                  balances=balances)
        except AssumeFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新输出全部行。\n")
            continue
        a.code, a.labels = code, labels
        a.decomps = list(decomps) if isinstance(decomps, list) else []
        a.model = model
        a.factor_data = dict(factor_data or {})
        a.base_margin = base_margin
        if model is not None:
            # C2：reconcile() 写得对、测试充分，缺的是「在真实流程里被
            # 调用一次并把结果交给人看」这个动作。
            a.residuals = _reconcile_model(model, ledger, as_of)
        return a
    raise AssumeFailed(
        f"连续 {max_attempts} 次给不出可核验的假设：{last}"
        f"\n最后一次的原始输出（末 400 字）：\n{raw.strip()[-400:]}",
        raw=raw)


def decide(client, judg: dict, labels: list[str], context: str, *,
           ledger: FactLedger, body: str, as_of: date, code: str = "",
           max_attempts: int = 3, decomps=None,
           factor_data: dict | None = None,
           base_margin: Decimal | None = None,
           anchor: dict | None = None,
           balances: list | None = None) -> AssumptionSet:
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
                            factor_data=factor_data, base_margin=base_margin,
                            anchor=anchor, balances=balances)
    except AssumeFailed as e:
        if not decomps:
            raise
        a = _decide_once(client, judg, labels, context, ledger=ledger,
                         body=body, as_of=as_of, code=code,
                         max_attempts=max_attempts, decomps=None,
                         # **降级路径也要带 balances。** 两标量模式下
                         # `_row_label` 返回中文驱动名，而辩论② 在预研失败时
                         # 正是用 `DRIVERS` 的键做 subjects —— 匹配得上。
                         # 不传就等于「降级之后这条约束静默失效」。
                         factor_data=None, base_margin=None,
                         balances=balances)
        a.degraded_raw = getattr(e, "raw", "")
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


def _change(cur: Decimal, prev: Decimal, unit: str) -> Decimal | None:
    """相邻两期水平值 → 变化（变化模式历史对账用）。换算规则与提示词里的单位
    规则同一套；其他单位换算不出来，返回 None（列入「无历史」）。"""
    if unit == "pp":
        return (cur - prev) * 100
    if unit in _PCT_UNITS:
        return cur / prev - 1 if prev != 0 else None
    return None


def _history_key(f) -> str:
    """因子在账本里的历史序列用哪个 key：数据线索里 `[[key]]` 声明的优先，
    否则按因子 key（旧分解式的口径）。"""
    from ir_agent.gaps import _KEY_DECL

    have = _KEY_DECL.findall(f.source_hint or "")
    return have[0] if have else f.key


def _reconcile_model(model: FormulaModel, ledger: FactLedger, as_of: date,
                     years: int = 5) -> dict:
    """对公式化分解式做历史回算，报残差 + 标出取不到历史的因子。

    每个历史年份按**单步预测**核对：`上期(·)`、`基期(·)` 都取上一期实际值
    （spec 2026-10-14「写计划时的补充」3）。变化模式下因子历史按单位把相邻两期
    水平换成变化；水平模式直接用水平，营收增速按公式在本期与上期的值之比算。
    旧乘/加分解式下与改动前的 `_reconcile_decomp` 逐位一致。

    某个因子在账本里完全找不到历史序列，**不能因此把它从残差报告里
    静默拿掉**——要在 `no_history` 里明说。
    """
    periods = annual_periods(ledger, as_of, years=years + 1)
    consts = {c.name: c.value for c in model.constants}
    out: dict = {}
    levels = {f.short: _historical_series(ledger, _history_key(f), periods, as_of)
              for f in model.factors}
    rev = _historical_series(ledger, "revenue", periods, as_of)
    gm = _historical_series(ledger, "gross_margin", periods, as_of)
    for target, fm in model.formulas.items():
        used = model.factors_of(target)
        if len(periods) < 2:
            out[target] = {"residuals": [], "no_history": [f.label for f in used]}
            continue
        no_history = [f.label for f in used if len(levels[f.short]) < 2]
        res: list[Residual] = []
        for i in range(1, len(periods)):
            cur_p, prev_p = periods[i], periods[i - 1]
            cur: dict = {}
            prev: dict = {"营收": rev.get(prev_p), "毛利率": gm.get(prev_p)}
            ok = True
            for f in used:
                lv = levels[f.short]
                if model.mode == "水平":
                    if cur_p not in lv:
                        ok = False
                        break
                    cur[f.short] = lv[cur_p]
                    if prev_p in lv:
                        prev[f.short] = lv[prev_p]
                else:
                    if cur_p not in lv or prev_p not in lv or lv[prev_p] == 0:
                        ok = False
                        break
                    ch = _change(lv[cur_p], lv[prev_p], f.unit)
                    if ch is None:
                        ok = False
                        break
                    cur[f.short] = ch
            if not ok:
                continue
            try:
                if target == "revenue":
                    if cur_p not in rev or not rev.get(prev_p):
                        continue
                    actual = rev[cur_p] / rev[prev_p] - Decimal(1)
                    if model.mode == "水平":
                        denom = evaluate(fm, prev, None, prev, consts)
                        composed = evaluate(fm, cur, prev, prev, consts) / denom - 1
                    else:
                        # 旧乘法链与尺度无关：上期营收按 1 代入，与旧 compose 逐位一致。
                        scale = Decimal(1) if model.legacy else rev[prev_p]
                        pv = dict(prev, 营收=scale)
                        composed = evaluate(fm, cur, pv, pv, consts) / scale - 1
                else:
                    if cur_p not in gm or (model.mode == "变化" and prev_p not in gm):
                        continue
                    actual = gm[cur_p]
                    composed = evaluate(fm, cur, prev, prev, consts)
            except FormulaError:
                continue
            res.append(Residual(period=cur_p, composed=composed, actual=actual,
                                gap=actual - composed))
        out[target] = {"residuals": res, "no_history": no_history}
    return out


def _reconcile_decomp(d: Decomposition, ledger: FactLedger,
                      as_of: date, years: int = 5) -> dict:
    """单条旧分解式的历史回算（保留给旧调用方）。翻译成公式后走同一个实现。"""
    return _reconcile_model(from_decomps([d]), ledger, as_of, years)[d.target]


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


def estimated_labels(a: AssumptionSet) -> list[str]:
    """哪些因子的取值没有证据支撑、是估计。空列表 = 都有依据。

    判据是**按理由自述归类**，不是"有没有挂依据"：理由自己写了无数据、
    取值为估计，那它就是估计，挂不挂账本占位符都一样。

    原判据 `not r.bases` 被 600584 实跑当场绕过——4 个因子的理由白纸
    黑字写着「无数据（估计）」，依据列却挂着一个真实存在、方向上沾边的
    占位符，于是它们从「没有证据支撑」的名单里消失了，五个只剩一个。
    占位符指向的事实是真的、四道闸门也都该放行，但它不支撑这个取值，
    不能拿它把"这个数是拍的"从清单上抹掉——清单是人工批准唯一能看的
    东西，**遗漏必须可见**这条约束正是在这里被绕过的。
    """
    return sorted({_row_label(r, a._m() or a.decomps) for r in a.rows
                   if r.factor_key and (not r.bases
                                        or _looks_like_estimate(r.note))})


def unverified_labels(a: AssumptionSet) -> list[str]:
    """哪些因子的哪些年份，依据没能逐字核验。空列表 = 全部核验通过。

    **单一定义。** 待核实清单（`describe()`）与最终研报的声明
    （`full._unverified_disclosure`）都用它——两处各写一遍"怎么给这些行
    起名"，早晚会分叉成人在清单上看到的和研报里写的对不上号。
    """
    return sorted({f"{_row_label(r, a._m() or a.decomps)}第{r.year}年"
                   for r in a.rows if r.factor_key and r.basis_unverified})


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
    if a._m() is not None:
        base_margin = _base_margin_of(cal)
        # 第 0 年营收按工作簿口径（亿元）——公式用到绝对金额时，ir-agent 与
        # 工作簿必须代入同一个数。
        a.base_revenue = _base_revenue_of(cal)
        # 锚定开启时毛利率第 2 年起从各档锚定水平接着走（见 margin_bases）；
        # 第二阶段的 check() 不做这一步，所以这里要再查一次单调。
        bases = margin_bases(a, base_margin, anchor_from_cal(cal))
        if bases:
            _check_monotonic(a, base_margin, bases)
        growth_scen, gm_scen = {}, {}
        for c in Case:
            g, m = synthesize(a, c, base_margin, bases)
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


def has_model(a) -> bool:
    """这份判断项是不是按分解式填的（新写法或旧的乘/加）。"""
    m = getattr(a, "_m", None)
    return bool(m and m() is not None)


def _base_revenue_of(cal: dict) -> Decimal | None:
    """第 0 年营收（亿元）：calibrate 的最近完整年度营收。取不到返回 None。"""
    annual = (cal.get("periods") or {}).get("annual") or []
    v = (cal.get("rev") or {}).get(annual[-1]) if annual else None
    return Decimal(str(v)) if v is not None else None


def to_driver_block(a: AssumptionSet, cal: dict) -> dict:
    """AssumptionSet → ashare_build 要的 `drivers` 结构（schema 2，公式化）。

    公式以**已校验的记号序列**交付（`Formula.tokens()`）：`ashare-dcf` 只把记号
    换成单元格引用，**不做解析** —— 解析只在 ir-agent 一处（spec 2026-10-14 四）。

    三档顺序固定为 bull/base/bear，与工作簿 B/C/D 列绑定 ——
    顺序写反不会让验算失败，只是「选悲观」会选出乐观值。

    **基期毛利率取 `_base_margin_of`，不是 `cal["judg"]["gm"][0]`。** `apply_to`
    若已跑过，`gm[0]` 是合成之后的年度水平，拿它当基期会把第一年的增量算两遍。

    **取值规则与 synthesize 保持一致（C2 纵深防御）**：同一因子同一年若有重复行，
    后写入覆盖先写入。
    """
    m = a._m()
    base_margin = _base_margin_of(cal)
    base_rev = _base_revenue_of(cal)
    years = max((r.year for r in a.rows if r.factor_key), default=0)
    by_year_key: dict[tuple[int, str], Row] = {}
    for r in a.rows:
        if r.factor_key:
            by_year_key[(r.year, r.factor_key)] = r
    vals: dict[str, list] = {}
    for case in ("bull", "base", "bear"):
        rows_by_year = []
        for y in range(1, years + 1):
            vals_for_year = []
            for f in m.factors:
                try:
                    row = by_year_key[(y, f.key)]
                except KeyError as e:
                    # Minor-4（final-fix-D）：排障要看得出是哪个因子哪一年缺了取值。
                    raise AssumeFailed(
                        f"因子「{f.label}」第 {y} 年缺取值，无法写入 1b_驱动分解 "
                        "工作表——多半是人手改 JSON 把某个因子的年份改到了超出"
                        "预测期范围，让不同因子的年份对不上。") from e
                vals_for_year.append(float(getattr(row, case)))
            rows_by_year.append(vals_for_year)
        vals[case] = rows_by_year
    block = {
        "schema": 2, "mode": m.mode,
        "factors": [{"key": f.key, "short": f.short, "label": f.label,
                     "unit": f.unit} for f in m.factors],
        "constants": [{"name": c.name, "value": float(c.value),
                       "source": c.source} for c in m.constants],
        "formulas": {t: [list(tok) for tok in fm.tokens()]
                     for t, fm in m.formulas.items()},
        "formula_text": {t: m.describe(t) for t in m.formulas},
        "base": {"revenue": float(base_rev) if base_rev is not None else None,
                 "margin": float(base_margin),
                 "factors": {k: float(v.value) for k, v in a.base_values.items()}},
        "values": vals,
    }
    anchor = anchor_from_cal(cal)
    bases = margin_bases(a, base_margin, anchor)
    if bases:
        # 锚定开启时 `基期(毛利率)` 的按档按年取值、各档锚定后的第 1 年毛利率
        # （`上期(毛利率)` 在第 2 年取它）—— 与 apply_to 同一个函数算出，否则
        # 工作簿三档列与 apply_to 写进 judg 的数就对不上。
        block["base_levels"] = {k: [float(x) for x in v] for k, v in bases.items()}
        block["anchor_y1"] = {k: float(v) for k, v in _anchor_values(anchor).items()}
    return block


