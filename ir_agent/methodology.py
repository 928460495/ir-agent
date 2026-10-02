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

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from ir_agent.drivers import (TARGET_LABEL, Decomposition,
                              DecompositionInvalid, Factor, Op, check_closure)
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
        # 这里只防「连目标、运算两段都凑不出来」——因子数够不够（下界
        # MIN_FACTORS、上界 MAX_FACTORS）一律交给下面的 check_closure。
        # 之前这里还硬编码过一道 `len(parts) < 4` 只管下界不管上界，
        # 与 MIN_FACTORS 语义重复却不联动、也没有测试覆盖，一半自己查
        # 一半转发的写法本身就说明它该整个删掉，而不是改成引用常量。
        if len(parts) < 2:
            raise MethodologyFailed(
                f"分解一行字段不足（至少要有 目标|运算 两段）：{hit.group(1)}")
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
            # 第 4 段 = 检索词。**此前它被静默丢弃**，所以旧的 3 段文件
            # 自然得到空串、退回 `gaps._core_candidates` 的前三级——向后
            # 兼容是免费的。
            #
            # **已知的坏情形（明账）**：`raw.split(":")` 对所有字段生效，
            # 若 `source_hint` 里出现冒号（`年报附注:汇兑损益`），一行 3 段
            # 的因子会被切成 4 段，尾巴落进这里，变成一个模型从没打算给的
            # 检索词。闸门只查长度，拦不住它。不在本次修（修法是限定
            # `split(":", 3)` 或禁止 hint 带冒号，是独立的一件事），但有
            # 测试把它钉成已知——见 `test_hint带冒号时第四段会变成假检索词`。
            term = bits[3] if len(bits) > 3 else ""
            # **`Factor.__post_init__` 抛的是 `DecompositionInvalid`，必须
            # 在这里转译成 `MethodologyFailed`。** 下面那个 try 只包着
            # `check_closure(d)`，不包构造本身；不转译的话异常会绕过本模块
            # 的重试循环（`parse` 的调用方只 `except MethodologyFailed`），
            # 模型既拿不到驳回原因、也没有重试机会。
            try:
                factors.append(Factor(key=_slug(label), label=label,
                                      unit=unit, source_hint=hint,
                                      search_term=term))
            except DecompositionInvalid as e:
                raise MethodologyFailed(str(e)) from e
        d = Decomposition(target=_TARGETS[tgt], op=_OPS[op], factors=factors)
        try:
            check_closure(d)
        except DecompositionInvalid as e:
            raise MethodologyFailed(str(e)) from e
        # I3（终审复核）：同一 target 不能出现第二条分解式。下游
        # `target_of = {f.key: d.target for d in decomps for f in
        # d.factors}` 按 target 把多条分解式的因子拍平进同一个映射，
        # 第一条会被第二条悄悄覆盖丢弃；`_describe_factors`/
        # `to_driver_block` 逐年从 `a.rows` 里按 `r.year` 取值时，两条
        # 分解式产出的行会被当成同一个 target 的不同年份处理，年份因此
        # 整体错位（预测期 2 年却合成出 4 个营收增速）。必须在这里就
        # 拒绝重复的 target，而不是留给下游悄悄合并。
        if any(x.target == d.target for x in m.decompositions):
            raise MethodologyFailed(
                f"「{TARGET_LABEL.get(d.target, d.target)}」的分解式出现了"
                "不止一条——下游按 target 把多条分解式的因子拍平进同一个"
                "映射，多出的那条会把前一条悄悄覆盖，年份因此整体错位。"
                "同一个目标只能给一条分解式，把因子都合并进这一条里。")
        m.decompositions.append(d)

    got = {d.target for d in m.decompositions}
    missing = [cn for cn, k in _TARGETS.items() if k not in got]
    if missing:
        # 半套分解比不分解更危险: 读者会以为两项都分解了。
        raise MethodologyFailed(
            f"缺少 {'、'.join(missing)} 的分解式 —— 两个目标都要给，"
            "只给一半会让读者以为两项都分解了")

    # 跨分解式的 key 唯一性校验。`check_closure` 的 seen 只在单条
    # Decomposition 内查重，但下游会把两条分解式的因子拍平进同一个
    # 命名空间（target_of = {f.key: d.target for d in decomps for f in
    # d.factors}）——营收链与毛利率链里如果各有一个因子 slug 后撞了 key，
    # 拍平时后写入的那个会悄悄覆盖前一个，把它错误地记到另一个目标下，
    # 而且不报任何错。这道校验必须放在两条分解式都解析完之后才做得了。
    seen_keys: dict[str, tuple[str, str]] = {}
    for d in m.decompositions:
        tgt_label = TARGET_LABEL.get(d.target, d.target)
        for f in d.factors:
            if f.key in seen_keys:
                other_tgt_label, other_label = seen_keys[f.key]
                raise MethodologyFailed(
                    f"「{other_tgt_label}」的因子「{other_label}」与"
                    f"「{tgt_label}」的因子「{f.label}」slug 后都是 "
                    f"{f.key!r} —— 下游会把两条分解式的因子拍平进同一个"
                    "命名空间，撞车的那个会被错误地记到另一个目标下且不"
                    "报错。换一个更有区分度的因子名称（例如给其中一个加"
                    "上「（收入端）/（成本端）」这类后缀）。")
            seen_keys[f.key] = (tgt_label, f.label)

    m.pitfalls = [h.group(1).strip() for h in _PITFALL.finditer(text)]
    return m


def _slug(label: str) -> str:
    """中文标签 → 稳定的 key。用于账本字段名与工作簿行匹配。

    **这张表只是捷径，不是主路径——真正要保证质量的是下面的 fallback。**
    实测联网跑出来的八个真实因子标签「有效产能变化率」「稼动率变化率」
    「良率变化率」「ASP变化率」「稼动率贡献」「先进封装收入占比提升贡献」
    「原材料及折旧成本变化贡献」「汇率贡献」——一个都不在表里，全部走了
    fallback。后来人别以为「表里没有就往表里加一条」能解决 key 冲突之类
    的问题，表命中只是极少数巧合，绝大多数标签从一开始就是 fallback 在兜。
    """
    table = {"产能": "capacity", "稼动率": "utilization", "单价": "asp",
             "销量": "volume", "吨价": "unit_price",
             "稼动率摊薄": "util_leverage", "折旧变动": "depreciation",
             "上游成本": "input_cost", "产品结构": "mix"}
    if label in table:
        return table[label]

    # 逐字符按 Unicode 类别判断，不用 `\W` 做正则替换：Python 默认
    # Unicode 模式下 `\w` 对「留不留」的判断口径比我们想要的更宽，稳妥
    # 的做法是显式只留「字母」「数字」两类（中文字符本身不清——下游
    # 账本 key / JSON / 工作簿行标都吃得下中文，要清的是标点与空白，
    # 中英文标点、全角括号、顿号、斜杠一律替换成下划线），其余全部
    # 替换成下划线再折叠。
    cleaned = "".join(
        ch if unicodedata.category(ch)[0] in ("L", "N") else "_"
        for ch in label)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").lower()
    if not cleaned:
        # 纯符号标签（如「→」）清完标点后什么都不剩。不能就此返回裸的
        # "f_"——那样所有纯符号标签都会撞成同一个 key，等于把「跨分解式
        # key 冲突」这个坑自己挖了一个最容易踩中的版本。用标签内容算一
        # 段稳定摘要兜底：同一标签每次结果相同（确定性），不同标签几乎
        # 不会撞车。
        cleaned = "sym" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]
    return "f_" + cleaned


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
- **营收乘法链里不要放「占比/比重/结构」类因子**：高端产品占比从 20% 到
  22.4% 是 +12% 的相对变化，但营收不会因此多长 12%。结构升级对营收的影响
  并进「平均单价/ASP」，对毛利率的影响放进加法链。市占率可以乘（营收 =
  行业规模 × 市占率）。
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


_RAW_HEADING = "## 分解式原始定义（供人工编辑与重跑读回）"

# to_markdown 反过来重建 DSL 行时要用的映射——与解析方向 _TARGETS/
# _OPS 互为逆映射，取第一个匹配到的中文标签即可（两个映射表都不会有
# 一个值对应多个键）。
_TARGET_CN = {v: k for k, v in _TARGETS.items()}
_OP_CN = {v: k for k, v in _OPS.items()}


def _to_dsl(m: Methodology) -> str:
    """把 Methodology 变回预研阶段那种 `分解: ... | ...` 的 DSL 文本——
    与 `parse()` 互为逆操作，`from_markdown` 靠它把落盘文件读回来。"""
    lines = []
    for d in m.decompositions:
        parts = [_TARGET_CN.get(d.target, d.target), _OP_CN.get(d.op, d.op.value)]
        parts += [f"{f.label}:{f.unit}:{f.source_hint}" for f in d.factors]
        lines.append("分解: " + " | ".join(parts))
    for p in m.pitfalls:
        lines.append(f"陷阱: {p}")
    return "\n".join(lines)


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
              "若你看到分解不对，**改这个文件后重跑第一阶段**即可覆盖。",
              "", _RAW_HEADING, "",
              "重跑第一阶段时会先读这里的代码块，而不是重新联网预研——"
              "**编辑下面这段文字再重跑即可覆盖**，语法与预研阶段完全一致："
              "`分解: 目标 | 运算 | 因子名:单位:数据线索 | …`、"
              "`陷阱: 一句话`。删掉这段代码块或改坏语法，重跑时会**明确"
              "报错**而不是被静默忽略——不然你会以为自己的修改生效了，"
              "实际却被悄悄丢弃、重新联网跑出一份新的分解式。", "",
              "```text", _to_dsl(m), "```", ""]
    return "\n".join(lines)


_CODE_BLOCK = re.compile(r"```(?:text)?\n(.*?)```", re.DOTALL)
_TITLE = re.compile(r"^# 驱动因子分解（(.*?)）", re.MULTILINE)


def from_markdown(text: str) -> Methodology:
    """读回人工可能已经改过的 drivers.md（见 `to_markdown` 末尾的
    `_RAW_HEADING` 代码块）。

    **解析不出来就明确报错，绝不静默回退到重新预研。** 重跑第一阶段的
    调用方（`full._research_methodology`）看到本函数抛出
    `MethodologyFailed` 时，只应该把这件事如实报给人看，不能悄悄改去
    调用 `research()` 联网重来一遍——那样人会以为自己的修改生效了，
    实际上被整个丢掉，静默覆盖比不能覆盖更危险。
    """
    if _RAW_HEADING not in text:
        raise MethodologyFailed(
            f"drivers.md 里找不到「{_RAW_HEADING}」标记，无法读回原始"
            "分解式定义——这份文件是不是本工具生成的，或者标记被"
            "删掉了？")
    tail = text.split(_RAW_HEADING, 1)[1]
    hit = _CODE_BLOCK.search(tail)
    if hit is None:
        raise MethodologyFailed(
            f"「{_RAW_HEADING}」标记之后找不到代码块，无法读回原始"
            "分解式定义——是不是代码块被删掉或改坏了？")
    m = parse(hit.group(1))
    title = _TITLE.search(text)
    if title:
        m.industry = title.group(1)
    m.rationale = "（读回自人工可能编辑过的 drivers.md，非模型原始输出）"
    return m
