"""方法论预研 —— 先问「这个行业靠什么赚钱」，再去采集数据。

**这是整个框架此前缺的那一环。** 原先 industry_profile 算的三样东西
（集中度、生命周期、周期敏感度）全部只需要利润表和资产负债表 —— 它们之所以
「通用」，是因为回避了行业特性: 任何行业都能算 HHI，但 HHI 不告诉你封测的
毛利率由什么决定。

于是 AI 填判断项时手上只有历史统计和五力的定性判断，那是一个制造业公司的
**纯报表视角**。封测真正的驱动链是「产能 × 稼动率 × 单价」，这三个变量框架里
一个都没有。

这个模块的产出不是「更多指标」，是**一个可计算的分解式** —— 它决定了后面
去采集什么数据、以及人在批准清单上能检查到什么。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ir_agent.drivers import (Decomposition, DecompositionInvalid, Factor, Op,
                              check_closure)
from ir_agent.lineparse import keyed, pick

_DECOMP = keyed("分解")
_PITFALL = keyed("陷阱")

_TARGETS = {"营收": "revenue", "毛利率": "gross_margin"}
_OPS = {"乘": Op.MULTIPLY, "加": Op.ADD_PP}


class MethodologyFailed(Exception):
    """预研产出不可用 —— 退回不分解模式，并说明原因。"""


@dataclass
class Methodology:
    industry: str = ""
    decompositions: list[Decomposition] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)
    rationale: str = ""

    def of(self, target: str) -> Decomposition | None:
        return next((d for d in self.decompositions if d.target == target),
                    None)


def parse(text: str) -> Methodology:
    m = Methodology()
    for hit in _DECOMP.finditer(text):
        parts = [p.strip() for p in hit.group(1).split("|")]
        if len(parts) < 4:
            raise MethodologyFailed(
                f"分解一行字段不足（需 目标|运算|因子…，至少两个因子）：{hit.group(1)}")
        tgt = pick(parts[0], list(_TARGETS))
        if tgt is None:
            raise MethodologyFailed(
                f"未知的分解目标 {parts[0]!r}，只能是：" + "、".join(_TARGETS))
        op = pick(parts[1], list(_OPS))
        if op is None:
            raise MethodologyFailed(
                f"未知的运算 {parts[1]!r}，只能是：" + "、".join(_OPS))

        factors = []
        for raw in parts[2:]:
            bits = [x.strip() for x in raw.split(":")]
            if len(bits) < 2 or not bits[0]:
                raise MethodologyFailed(
                    f"因子写法应为「名称:单位:线索」，得到 {raw!r}")
            label, unit = bits[0], bits[1]
            hint = bits[2] if len(bits) > 2 else ""
            factors.append(Factor(key=_slug(label), label=label, unit=unit,
                                  source_hint=hint))
        d = Decomposition(target=_TARGETS[tgt], op=_OPS[op], factors=factors)
        try:
            check_closure(d)
        except DecompositionInvalid as e:
            raise MethodologyFailed(str(e)) from e
        m.decompositions.append(d)

    got = {d.target for d in m.decompositions}
    missing = [cn for cn, k in _TARGETS.items() if k not in got]
    if missing:
        # 半套分解比不分解更危险: 读者会以为两项都分解了。
        raise MethodologyFailed(
            f"缺少 {'、'.join(missing)} 的分解式 —— 两个目标都要给，"
            "只给一半会让读者以为两项都分解了")

    m.pitfalls = [h.group(1).strip() for h in _PITFALL.finditer(text)]
    return m


def _slug(label: str) -> str:
    """中文标签 → 稳定的 key。用于账本字段名与工作簿行匹配。"""
    table = {"产能": "capacity", "稼动率": "utilization", "单价": "asp",
             "销量": "volume", "吨价": "unit_price",
             "稼动率摊薄": "util_leverage", "折旧变动": "depreciation",
             "上游成本": "input_cost", "产品结构": "mix"}
    if label in table:
        return table[label]
    return "f_" + re.sub(r"\W+", "_", label).strip("_").lower()


PROMPT = """你正在为一份 A 股公司研究报告做**方法论预研**。

标的行业：{industry}　标的代码：{code}

**先上网搜索**这个行业的研究与估值方法论 —— 卖方怎么看这个行业、营收和
毛利率分别由什么驱动、有哪些公认的陷阱。搜完再回答，不要凭印象。

然后给出两条**算术闭合**的分解式：

`分解: 营收 | 乘 | 因子名:单位:数据线索 | 因子名:单位:数据线索 | …`
`分解: 毛利率 | 加 | 因子名:单位:数据线索 | …`

**闭合的硬性要求**
- 营收只能用「乘」，每个因子单位一律 `变化率`；毛利率只能用「加」，
  单位一律 `pp`。
- **pp 不能进乘法链**：稼动率 85%→82% 是 −3pp，但乘法因子的变化率是
  82/85−1 = −3.5%，两者不等。
- 每个因子必须写得出单位。**写不出单位的项就写不进来** ——
  「客户结构优化」听着专业但不能乘，这类一律不要。
- 每条 2–5 个因子。一个不叫分解；超过五个人核实不过来。
- 数据线索要具体到「去哪找」（年报哪一节 / 哪类研报 / 哪个行业数据源）。

再给出这个行业估值的已知陷阱，每条一行：

`陷阱: <一句话>`

只输出上述格式的行，不要其它说明。
"""


def _tail(raw: str, n: int = 400) -> str:
    raw = (raw or "").strip()
    return raw[-n:] if len(raw) > n else raw


def research(client, industry: str, code: str,
             max_attempts: int = 3) -> Methodology:
    """让模型联网研究行业方法论，产出可计算的分解式。

    **前提：`client` 是否真的联网，取决于调用方。** 本函数的提示词
    （见 PROMPT）要求模型「先上网搜索」再回答，但 `research()` 本身不
    构造 client——它只是把传进来的 client 当一个「文本进、文本出」的
    函数调用。若调用方传入的是未开启工具的 client（例如
    `make_client(model=...)` 不带 `tools=` 参数），模型就只能凭自身
    知识回答，不会真的搜索。调用方应传入
    `make_client(model=..., tools=["WebSearch", "WebFetch"])`
    这样已开启联网工具的 client，否则这里的「先搜索」要求形同虚设。
    这条前提容易被后来人忽略，因此单独写在这里。
    """
    prompt = PROMPT.format(industry=industry, code=code)
    last, raw = "", ""
    for attempt in range(1, max_attempts + 1):
        raw = client(prompt)
        try:
            m = parse(raw)
        except MethodologyFailed as e:
            last = str(e)
            prompt = (f"{prompt}\n\n---\n"
                      f"**上一次被驳回（第 {attempt} 次）**：{last}\n"
                      "请修正后重新输出全部行。\n")
            continue
        m.industry, m.rationale = industry, raw.strip()
        return m
    raise MethodologyFailed(
        f"连续 {max_attempts} 次给不出闭合的分解式：{last}"
        f"\n最后一次的原始输出（末 400 字）：\n{_tail(raw)}")


def to_markdown(m: Methodology) -> str:
    lines = [f"# 驱动因子分解（{m.industry}）", "",
             "本次研究采用的分解式。**它决定了后面去采集什么数据、"
             "以及批准清单上能检查到什么** —— 清单上若没有某个因子，"
             "说明这个因素没被纳入。", ""]
    for d in m.decompositions:
        lines.append(f"## {d.describe()}")
        lines.append("")
        lines.append("| 因子 | 单位 | 数据线索 |")
        lines.append("| --- | --- | --- |")
        for f in d.factors:
            lines.append(f"| {f.label} | {f.unit} | {f.source_hint or '—'} |")
        lines.append("")
    if m.pitfalls:
        lines += ["## 该行业估值的已知陷阱", ""]
        lines += [f"- {x}" for x in m.pitfalls]
        lines.append("")
    lines += ["---", "",
              "分解式不另设人工确认门 —— 门加多了人会开始机械点「同意」。",
              "若你看到分解不对，**改这个文件后重跑第一阶段**即可覆盖。", ""]
    return "\n".join(lines)
