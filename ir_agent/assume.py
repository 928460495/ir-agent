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

from ir_agent.basis import Basis, InvalidBasis, validate
from ir_agent.drivers import Decomposition, Factor, Op, compose
from ir_agent.ledger import FactLedger
from ir_agent.lineparse import keyed, pick


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


def _pct(raw: str) -> Decimal:
    m = _NUM.search(raw.replace(",", ""))
    if not m:
        raise AssumeFailed(f"取值不可解析：{raw!r}")
    t = m.group(0)
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
    residuals: dict = field(default_factory=dict)

    def path(self, driver: str, case: Case) -> list[Decimal]:
        got = sorted((r for r in self.rows if r.driver == driver),
                     key=lambda r: r.year)
        return [getattr(r, {Case.BULL: "bull", Case.BASE: "base",
                            Case.BEAR: "bear"}[case]) for r in got]

    def describe(self) -> str:
        """给人看的核实清单。**这是人工批准的对象，必须一眼看全。**"""
        lines = [f"DCF 判断项（{self.code}）—— 需人工核实后方可建模", ""]
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
        return "\n".join(lines)


def parse(text: str, years: int,
          decomps: list[Decomposition] | None = None) -> AssumptionSet:
    """decomps 给定时按因子解析；否则沿用两标量模式（降级路径）。

    降级路径不删 —— 预研（拆分解式、采数据）失败不该让整轮研究停摆。
    """
    if not decomps:
        return _parse_scalar(text, years)
    return _parse_factors(text, years, decomps)


def _parse_scalar(text: str, years: int) -> AssumptionSet:
    out = AssumptionSet(code="", labels=[])
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
        out.rows.append(Row(
            driver=DRIVERS[cn], year=year,
            bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
            bases=_basis(parts[5], cn), note=_note(parts[5])))
    return out


def _parse_factors(text: str, years: int,
                   decomps: list[Decomposition]) -> AssumptionSet:
    """按因子解析。每一行对应分解式里的一个因子，而不是营收增速/毛利率
    本身 —— 合成交给 `synthesize`，模型只负责填因子。"""
    by_label = {f.label: f for d in decomps for f in d.factors}
    target_of = {f.key: d.target for d in decomps for f in d.factors}
    out = AssumptionSet(code="", labels=[], decomps=list(decomps))
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
        out.rows.append(Row(
            driver=target_of[f.key], year=year,
            bear=_pct(parts[2]), base=_pct(parts[3]), bull=_pct(parts[4]),
            bases=_basis(parts[5], label), note=_note(parts[5]),
            factor_key=f.key))
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


def check(a: AssumptionSet, years: int, *, ledger: FactLedger, body: str,
          as_of: date, decomps: list[Decomposition] | None = None,
          base_margin: Decimal | None = None) -> None:
    if decomps:
        _check_factors(a, years, decomps, base_margin)
    else:
        _check_scalar(a, years)
    flat = [b for r in a.rows for b in r.bases]
    problems = validate(flat, ledger=ledger, body=body, as_of=as_of)
    if problems:
        raise AssumeFailed("依据不可核验：" + "；".join(problems))


def _check_scalar(a: AssumptionSet, years: int) -> None:
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
            st = fd.status if fd else "未采集"
            lines.append(f"| {f.label} | {f.unit} | {st} |")
    lines += ["",
              "**pp 不能进乘法链**：营收链的因子一律填变化率（含稼动率）——"
              "稼动率 85%→82% 是 −3pp，但变化率是 −3.5%。",
              "毛利率链的因子一律填 pp。",
              "标「无数据」或「年报披露/研报测算」（文字依据，未必有具体数值，"
              "待人工确认）的因子取值是**估计**，理由里要说明依据什么估的。",
              "",
              "对每一个因子、每一年给出一行：",
              "`假设: <因子名> | 第N年 | <悲观> | <中性> | <乐观> | "
              "「正文原话」或 [[占位符]]`"]
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


def decide(client, judg: dict, labels: list[str], context: str, *,
           ledger: FactLedger, body: str, as_of: date, code: str = "",
           max_attempts: int = 3, decomps: list[Decomposition] | None = None,
           factor_data: dict | None = None,
           base_margin: Decimal | None = None) -> AssumptionSet:
    prompt = build_prompt(judg, labels, context, brief=body, decomps=decomps,
                          factor_data=factor_data)
    last, raw = "", ""
    for attempt in range(1, max_attempts + 1):
        raw = client(prompt)
        try:
            a = parse(raw, len(labels), decomps=decomps)
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
        return a
    raise AssumeFailed(
        f"连续 {max_attempts} 次给不出可核验的假设：{last}"
        f"\n最后一次的原始输出（末 400 字）：\n{raw.strip()[-400:]}")


def _base_margin_of(judg: dict) -> Decimal:
    """基期毛利率 —— 加法链的起点，**对全部预测年、全部三档都是同一个数**
    （见 drivers.compose 的文档: 「基期」是账本口径的最近一期实绩，不随
    年份滚动）。取 `gm_hist` 的最后一项（calibrate 给的历史毛利率序列，
    不受 apply_to 影响）；取不到才退回 `gm[0]`（calibrate 模板的默认
    首年预测值，只是一个近似）。
    """
    gm_hist = judg.get("gm_hist") or []
    if gm_hist:
        return Decimal(str(gm_hist[-1]))
    gm = judg.get("gm") or [0.0]
    return Decimal(str(gm[0]))


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
        base_margin = _base_margin_of(ju)
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
    """
    base_margin = float(_base_margin_of(cal.get("judg") or {}))
    out = {}
    years = max((r.year for r in a.rows), default=0)
    for d in a.decomps:
        keys = [f.key for f in d.factors]
        vals = {}
        for case in ("bull", "base", "bear"):
            vals[case] = [[float(getattr(
                next(r for r in a.rows
                     if r.year == y and r.factor_key == k), case))
                for k in keys] for y in range(1, years + 1)]
        block = {"op": d.op.value,
                 "factors": [{"key": f.key, "label": f.label, "unit": f.unit}
                             for f in d.factors],
                 "values": vals}
        if d.target == "gross_margin":
            block["base_level"] = base_margin
        out[d.target] = block
    return out
