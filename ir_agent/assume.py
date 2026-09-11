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


@dataclass
class AssumptionSet:
    code: str
    labels: list[str]                       # 预测期年标签
    rows: list[Row] = field(default_factory=list)
    rationale: str = ""

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


def parse(text: str, years: int) -> AssumptionSet:
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
          as_of: date) -> None:
    for cn, key in DRIVERS.items():
        got = {r.year for r in a.rows if r.driver == key}
        missing = sorted(set(range(1, years + 1)) - got)
        if missing:
            raise AssumeFailed(
                f"{cn} 缺第 {missing} 年 —— 预测期每一年都要给，"
                "漏掉的年份会沿用模板默认值，而那不是你的判断")

    flat = []
    for r in a.rows:
        # **三档必须拉开。** 模板默认 Bull=Base=Bear，那等于没有情景；
        # 拉不开的情景分析只是把同一个数抄三遍。
        if not (r.bear < r.base < r.bull):
            raise AssumeFailed(
                f"第 {r.year} 年的{'营收增速' if r.driver == 'growth' else '毛利率'}"
                f"三档未拉开（悲观 {r.bear} / 中性 {r.base} / 乐观 {r.bull}）"
                " —— 必须严格递增，否则情景分析没有意义")
        flat += r.bases

    problems = validate(flat, ledger=ledger, body=body, as_of=as_of)
    if problems:
        raise AssumeFailed("依据不可核验：" + "；".join(problems))


PROMPT = """把前面的分析结论，落到 DCF 模型的判断项上。

**你只填这两项**：逐年营收增速、逐年毛利率。
费用率、周转天数、折旧率、税率、β、债务成本等由历史财务数据校准而来，
已经算好，不需要也不应该由你改动。

**预测期**：{years} 年（{labels}）

**模型给出的历史参考**（仅供你判断，不要照抄）
{history}

**你的分析简报**（引文依据只能逐字出自这里）
{brief}

**各层分析口径摘要**（供你判断，**不是可引用的正文**）
{context}

对每一项、每一年给出一行：
`假设: <营收增速|毛利率> | 第N年 | <悲观> | <中性> | <乐观> | 「正文原话」或 [[占位符]]`

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


def build_prompt(judg: dict, labels: list[str], context: str,
                 brief: str = "") -> str:
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
    return PROMPT.format(years=len(labels), labels="、".join(labels),
                         history="\n".join(hist) or "  （无）",
                         context=context, brief=brief or "（无）")


def decide(client, judg: dict, labels: list[str], context: str, *,
           ledger: FactLedger, body: str, as_of: date, code: str = "",
           max_attempts: int = 3) -> AssumptionSet:
    prompt = build_prompt(judg, labels, context, brief=body)
    last = ""
    for attempt in range(1, max_attempts + 1):
        raw = client(prompt)
        try:
            a = parse(raw, len(labels))
            check(a, len(labels), ledger=ledger, body=body, as_of=as_of)
        except AssumeFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新输出全部行。\n")
            continue
        a.code, a.labels = code, labels
        return a
    raise AssumeFailed(f"连续 {max_attempts} 次给不出可核验的假设：{last}")


def apply_to(cal: dict, a: AssumptionSet, case: Case = Case.BASE) -> dict:
    """把判断项写回 calibrate 的输出，供 ashare_build 建表。

    三档分别写进 judg 的 *_scen，由 build 分列填入 Bull/Base/Bear；
    单列的 growth/gm 保留为选定档，兼容未改造的旧路径。
    """
    ju = dict(cal["judg"])
    ju["growth"] = [float(x) for x in a.path("growth", case)]
    ju["gm"] = [float(x) for x in a.path("gm", case)]
    ju["growth_scen"] = {c.name.lower(): [float(x) for x in a.path("growth", c)]
                         for c in Case}
    ju["gm_scen"] = {c.name.lower(): [float(x) for x in a.path("gm", c)]
                     for c in Case}
    out = dict(cal)
    out["judg"] = ju
    return out
