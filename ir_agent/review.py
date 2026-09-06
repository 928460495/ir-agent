"""交互式假设审核 —— 把人从「填对 YAML 格式」里解放出来。

审核该花的力气在**这个假设合不合理**，不在缩进和日期格式。因此:

  · **逐项即时校验**，错了当场重问 —— 全部填完再一起报错，人得从头来过
  · **就地给上下文**（历史增速等），判断需要参照物
  · **写出的文件必须能被解析层读回**，否则交互只是换了个地方出错

填完不等于审核通过: 依据仍要对着账本与正文核验（见 stage2）。
这里只负责把人的判断准确地收集下来。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

from ir_agent.valuation.assumptions import (
    MAX_TERMINAL_GROWTH,
    Assumptions,
)
from ir_agent.valuation.basis import Basis, InvalidBasis

Prompt = Callable[[str], str]
Show = Callable[[str], None]

_QUIT = {"q", "quit", "exit", "退出"}


class ReviewAborted(Exception):
    """用户中止审核。"""


def _ask(prompt: Prompt, show: Show, question: str, parse, hint: str = ""):
    """反复询问直到输入合法。错了当场重问，不累积到最后。"""
    while True:
        raw = prompt(question).strip()
        if raw.lower() in _QUIT:
            raise ReviewAborted("用户中止审核，未写入任何假设。")
        try:
            return parse(raw)
        except (ValueError, InvalidOperation, InvalidBasis) as e:
            show(f"  ✗ {e}" + (f"　{hint}" if hint else ""))


def _rate(raw: str) -> Decimal:
    v = Decimal(raw)
    if not (Decimal("-1") < v < Decimal("1")):
        raise ValueError(f"应填小数而非百分数，例如 0.09 表示 9%（得到 {raw}）")
    return v


def _basis(prompt: Prompt, show: Show, label: str) -> list[Basis]:
    show(f"  {label}的依据类型： [1] 外部来源　[2] 账本事实　[3] 正文引句")
    kind = _ask(prompt, show, "  选择 (1/2/3)：",
                lambda s: s if s in {"1", "2", "3"} else
                (_ for _ in ()).throw(ValueError("请输入 1、2 或 3")))
    if kind == "1":
        url = _ask(prompt, show, "  来源 URL：", lambda s: s)
        quote = _ask(prompt, show, "  原文摘录：", lambda s: s)
        when = _ask(prompt, show, "  抓取日期 (YYYY-MM-DD)：",
                    lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                    hint="外部资料会变，日期决定了事后能否复核")
        return [Basis.external(url, quote, when)]
    if kind == "2":
        return [_ask(prompt, show, "  账本占位符（如 [[revenue.yoy@2025FY]]）：",
                     Basis.fact)]
    return [_ask(prompt, show, "  正文中的原句（须逐字存在）：", Basis.report)]


def interactive_review(
    prompt: Prompt = input,
    show: Show = print,
    years: int = 5,
    context: dict | None = None,
) -> Assumptions:
    show("── 估值假设审核 ──　随时输入 q 中止")
    for k, v in (context or {}).items():
        show(f"  参考：{k} {v}")

    show("\n[1/3] 折现率 WACC")
    wacc = _ask(prompt, show, "  取值（小数，如 0.09）：", _rate)
    wacc_basis = _basis(prompt, show, "WACC")

    show("\n[2/3] 永续增长率")

    def _tg(raw: str) -> Decimal:
        v = _rate(raw)
        # 当场拦住，而不是等构造对象时抛异常 —— 后者要重填全部
        if v >= wacc:
            raise ValueError(f"须低于 WACC {wacc}，否则终值发散")
        if v > MAX_TERMINAL_GROWTH:
            raise ValueError(
                f"高于长期名义 GDP 增速上限 {MAX_TERMINAL_GROWTH} —— "
                "这意味着公司最终吞掉整个经济")
        return v

    tg = _ask(prompt, show, "  取值（小数，如 0.025）：", _tg)
    tg_basis = _basis(prompt, show, "永续增长率")

    show(f"\n[3/3] 预测期营收增速（{years} 年）")
    rates = [_ask(prompt, show, f"  第 {i} 年：", _rate)
             for i in range(1, years + 1)]
    g_basis = _basis(prompt, show, "增速路径")

    return Assumptions(wacc=wacc, terminal_growth=tg, growth_rates=rates,
                       basis={"wacc": wacc_basis, "terminal_growth": tg_basis,
                              "growth_rates": g_basis})


def _basis_yaml(bases: list[Basis]) -> str:
    from ir_agent.valuation.basis import BasisKind

    out = []
    for b in bases:
        if b.kind is BasisKind.EXTERNAL:
            out.append(f"  - kind: external\n    url: {b.ref}\n"
                       f"    quote: {b.quote!r}\n    retrieved: {b.retrieved}")
        elif b.kind is BasisKind.FACT:
            out.append(f"  - kind: fact\n    ref: {b.ref!r}")
        else:
            out.append(f"  - kind: report\n    quote: {b.quote!r}")
    return "\n".join(out)


def to_yaml(a: Assumptions) -> str:
    """写回 YAML —— 必须能被 assumptions_io 读回，否则交互白做。"""
    rows = "\n".join(f"  - year: {i}\n    rate: {g}"
                     for i, g in enumerate(a.growth_rates, 1))
    return (f"# 估值假设 —— 经交互式审核录入于 {date.today()}\n\n"
            f"wacc: {a.wacc}\nwacc_basis:\n{_basis_yaml(a.basis['wacc'])}\n\n"
            f"terminal_growth: {a.terminal_growth}\n"
            f"terminal_growth_basis:\n"
            f"{_basis_yaml(a.basis['terminal_growth'])}\n\n"
            f"growth_rates:\n{rows}\n"
            f"growth_rates_basis:\n{_basis_yaml(a.basis['growth_rates'])}\n")
