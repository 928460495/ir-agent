"""模型自主定假设 —— 取代人工审核门。

原设计要求人工逐项填写并给出依据，理由是: 无风险利率没有免费数据源，
永续增长率是对无限期的判断，两者都推不出来。取消审核门后这两个数改由
模型决定，因此**校验必须补上人走掉之后留下的空缺**。

保留的约束（代码能查的，一条不放）:

  · 永续增长率 ≤ 由账本宏观事实算出的上限，且 < WACC（否则终值发散）
  · 每项假设必须有依据，且依据可核验 —— FACT 要能在账本里解析，
    REPORT 的引文要逐字出现在正文里
  · WACC 只接受 REPORT 依据: 它的论证必须先写进研报正文，再被引用。
    这一条是刻意的 —— 模型可以自由选 WACC，但必须在正文里把理由说出来，
    而不是在一个没人读的 YAML 字段里塞个数
  · 增速路径的年数必须与预测期一致

**放弃的**: 「有人看过并负责」这件事。产物里一律标注 decided_by=模型，
不冒充人工审核 —— 看板、YAML、研报三处都要能看出这个区别。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from ir_agent.valuation.assumptions import Assumptions
from ir_agent.basis import Basis, BasisKind, InvalidBasis, validate

MODEL_MARK = "模型自主决定"


class AutoDecideFailed(Exception):
    """模型给不出合法的假设。不退回一个「差不多」的默认值 —— 那等于既没有
    人工把关，也没有模型判断，只有一串来历不明的数字。"""


_SECTION = re.compile(
    r"^\s*(wacc|terminal_growth|growth_rates)\s*[:：]\s*(.*)$",
    re.IGNORECASE | re.MULTILINE)
_NUM = re.compile(r"-?\d+(?:\.\d+)?%?")
_QUOTE = re.compile(r"[「\"'“](.+?)[」\"'”]", re.DOTALL)


def _dec(raw: str) -> Decimal:
    raw = raw.strip()
    try:
        if raw.endswith("%"):
            return Decimal(raw[:-1]) / 100
        return Decimal(raw)
    except InvalidOperation as e:
        raise AutoDecideFailed(f"无法解析为数值: {raw!r}") from e


@dataclass(frozen=True)
class Decision:
    assumptions: Assumptions
    rationale: str

    @property
    def by_model(self) -> bool:
        return True


PROMPT = """你刚刚完成了这份研究报告。现在需要**你自己**确定估值假设。

原先这一步由人工审核填写，现已改为由你自主决定。这意味着没有人会在你之后纠正这些数字 —— 请据此掂量每一项。

**必须给出三项**（严格按下面的格式，不要多写别的）：

wacc: <小数，如 0.09>
wacc_basis: 「<正文中的一句原话，逐字照抄>」
terminal_growth: <小数，如 0.025>
terminal_growth_basis: 「<正文原话>」 或 [[占位符]]
growth_rates: <{years} 个小数，逗号分隔>
growth_rates_basis: [[占位符]] 或 「<正文原话>」

**硬性约束**（违反会被驳回重来）：
- 永续增长率必须 **≤ {ceiling}**，且必须 **< WACC**，否则终值发散。
- WACC 的依据**只能是正文原话**。也就是说：你得先在正文里把 WACC 的
  理由写出来，再引用它。不许在这里给一个正文没论证过的数字。
- 引用正文时必须**逐字照抄**，一个字都不能改，否则校验通不过。
- 占位符只能用报告里已经出现过的那些。

**关于 WACC**：无风险利率和 β 都已经是账本里的事实（见上文口径），不需要你猜。
你要做的判断只有**股权风险溢价**这一项，以及债务权重与债务成本的处理方式。
正文里请把这条链写清楚：无风险利率 + β × 风险溢价 = 股权成本，再说明你
如何得到 WACC。若 β 的 R² 偏低，说明 CAPM 对本标的解释力有限，这一点
必须在正文里交代，不能装作没看见。

**可用于依据的推导**：
{derived}

**正文**：
{body}
"""


def build_prompt(body: str, derived, years: int) -> str:
    ceiling = derived.terminal_ceiling.value if derived else None
    return PROMPT.format(
        years=years,
        ceiling=f"{ceiling:.4f}" if ceiling is not None else "0.06",
        derived=derived.describe() if derived else "（无可用推导）",
        body=body)


def _parse_basis(raw: str, key: str) -> list[Basis]:
    raw = raw.strip()
    if not raw:
        raise AutoDecideFailed(f"{key} 缺少依据")
    ref = re.search(r"\[\[[^\]]+\]\]", raw)
    if ref:
        try:
            return [Basis.fact(ref.group(0))]
        except InvalidBasis as e:
            raise AutoDecideFailed(f"{key} 的占位符不合法: {e}") from e
    q = _QUOTE.search(raw)
    quote = q.group(1) if q else raw
    try:
        return [Basis.report(quote)]
    except InvalidBasis as e:
        raise AutoDecideFailed(f"{key} 的依据不合法: {e}") from e


def parse(text: str, years: int) -> tuple[dict, dict]:
    """从模型输出里取出取值与依据。返回 (values, bases)。"""
    fields = {m.group(1).lower(): m.group(2).strip() for m in _SECTION.finditer(text)}
    basis_fields = {}
    for key in ("wacc", "terminal_growth", "growth_rates"):
        # 用 [ \t]* 而非 \s*: \s 含换行，空依据会一路吃到下一行，
        # 把 terminal_growth 的值误当成 wacc 的依据。
        m = re.search(rf"^[ \t]*{key}_basis[ \t]*[:：][ \t]*(.*)$",
                      text, re.IGNORECASE | re.MULTILINE)
        basis_fields[key] = m.group(1).strip() if m else ""

    missing = [k for k in ("wacc", "terminal_growth", "growth_rates")
               if not fields.get(k)]
    if missing:
        raise AutoDecideFailed(f"模型输出缺少：{'、'.join(missing)}")

    rates = [_dec(x) for x in _NUM.findall(fields["growth_rates"])]
    if len(rates) != years:
        raise AutoDecideFailed(
            f"增速路径给了 {len(rates)} 个值，预测期是 {years} 年")

    values = {"wacc": _dec(fields["wacc"]),
              "terminal_growth": _dec(fields["terminal_growth"]),
              "growth_rates": rates}
    bases = {k: _parse_basis(basis_fields[k], k)
             for k in ("wacc", "terminal_growth", "growth_rates")}
    return values, bases


def check(values: dict, bases: dict, *, ledger, body: str, as_of: date,
          ceiling: Decimal | None) -> None:
    """人走掉之后，代码能查的全部查一遍。"""
    tg, wacc = values["terminal_growth"], values["wacc"]
    if ceiling is not None and tg > ceiling:
        raise AutoDecideFailed(
            f"永续增长率 {tg} 高于账本宏观事实算出的上限 {ceiling}")
    if tg >= wacc:
        raise AutoDecideFailed(f"永续增长率 {tg} 不低于 WACC {wacc}，终值发散")

    kinds = {b.kind for b in bases["wacc"]}
    if kinds != {BasisKind.REPORT}:
        raise AutoDecideFailed(
            "WACC 的依据只能是正文原话 —— 没有人工把关时，"
            "折现率的理由必须写在读者看得到的地方，而不是 YAML 字段里")

    # **卖方一致预期不得作为本模型假设的依据。**
    # 它可以出现在正文里作对照（因此在账本中、可用占位符引用），但拿
    # 券商的 EPS 预测去论证自己的增长率，结论绕一圈回到起点，还平白多了
    # 一层「有依据」的外衣 —— 那不是研究。
    from ir_agent.sources.reports import CONSENSUS_PREFIX

    for name, bs in bases.items():
        for b in bs:
            if b.kind is BasisKind.FACT and CONSENSUS_PREFIX in b.ref:
                raise AutoDecideFailed(
                    f"{name} 的依据引用了卖方一致预期 {b.ref} —— "
                    "一致预期只能作为本报告结论的对照，不能作为假设的来源。"
                    "请给出独立的理由。")

    flat = [b for bs in bases.values() for b in bs]
    problems = validate(flat, ledger=ledger, body=body, as_of=as_of)
    if problems:
        raise AutoDecideFailed("依据不可核验：" + "；".join(problems))


def decide(body: str, client, *, ledger, as_of: date, derived=None,
           years: int = 5, max_attempts: int = 3) -> Decision:
    """让模型定假设，逐轮校验，连续失败则拒绝出估值。"""
    ceiling = derived.terminal_ceiling.value if derived else None
    prompt = build_prompt(body, derived, years)
    last = ""
    for attempt in range(1, max_attempts + 1):
        out = client(prompt)
        try:
            values, bases = parse(out, years)
            check(values, bases, ledger=ledger, body=body, as_of=as_of,
                  ceiling=ceiling)
        except AutoDecideFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新按格式输出全部三项。\n")
            continue
        draft = Assumptions(
            wacc=values["wacc"],
            terminal_growth=values["terminal_growth"],
            growth_rates=values["growth_rates"], basis=bases)
        try:
            # 走 approve 这条既有路径而不是直接塞 reviewed_by —— 依据核验的
            # 逻辑只该有一份。决策来源不同，校验必须相同。
            # 署名如实写「模型自主决定」: 冒充人工审核比没有审核更糟，
            # 读者会据此以为有人看过。
            settled = draft.approve(MODEL_MARK, as_of, ledger=ledger,
                                    body=body, as_of=as_of)
        except Exception as e:                              # noqa: BLE001
            last = f"依据核验未通过：{e}"
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新按格式输出全部三项。\n")
            continue
        return Decision(assumptions=settled, rationale=out.strip())
    raise AutoDecideFailed(
        f"连续 {max_attempts} 次给不出合法假设，拒绝出具估值。最后一次：{last}")
