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

from ir_agent.drivers import (MAX_FACTORS, MIN_FACTORS, TARGET_LABEL,
                              Decomposition, DecompositionInvalid, Factor, Op,
                              check_closure)
from ir_agent.formula import (MODES, RESERVED, TARGETS as _FORMULA_TARGETS,
                              Constant, FormulaInvalid, FormulaModel,
                              flat_factors as _flat, from_decomps,
                              parse_constant, parse_formula, valid_name)
from ir_agent.lineparse import keyed, pick

_DECOMP = keyed("分解")
_PITFALL = keyed("陷阱")
# `keyed("覆盖")` 不会误中「未覆盖:」行：行首前缀只吃列表符号与加粗，吃不掉「未」。
_COVER = keyed("覆盖")
_UNCOVER = keyed("未覆盖")
# 公式化写法（2026-10-14）。出现 `公式:` 行即按新写法解析，否则按旧的 `分解:`。
_MODE = keyed("模式")
_FACTOR = keyed("因子")
_CONST = keyed("常量")
_FORMULA = keyed("公式")

_TARGETS = {"营收": "revenue", "毛利率": "gross_margin"}
_OPS = {"乘": Op.MULTIPLY, "加": Op.ADD_PP}

# 模型把格式行里的占位词「检索词」当成字面前缀照抄（生益科技 2026-10-13：
# 6 个因子全写成「检索词Prismark」「检索词铜价」，命中次数全 0，年报与公告
# 两级全部落空、全掉到联网）。根因在提示词的格式行，已改为 `<检索词>`；
# 这里只做兜底。`:` 已经被 DSL 切掉了，剩下的分隔可能是全角冒号或等号。
_TERM_LABEL = re.compile(r"^检索词\s*[:：=]?\s*")


def _strip_term_label(term: str) -> str:
    return _TERM_LABEL.sub("", term).strip()


class MethodologyFailed(Exception):
    """预研产出不可用 —— 退回不分解模式，并说明原因。

    `raw` 带着**最后一次的完整模型输出**（没有的话是空串）。与
    `AnalystRefused.raw` / `AssumeFailed.raw` 同一处理，由调用方落盘。

    **为什么必须落盘**: 被驳回的分解式不留文件，于是事后的离线复盘只能
    看到**通过了闸门的样本**（存活者偏差）。实测: 牧原股份那个「公司出栏量
    占全国比重」被拒 3 次，`drivers.md` 根本没生成，而后来用 10 份存档做
    离线消融时，两个词法修复在 56 个存活因子上的效果是 0 个改变 ——
    真正的拦截与漏报全部发生在看不见的那一半。
    """

    def __init__(self, msg: str, raw: str = "") -> None:
        super().__init__(msg)
        self.raw = raw


@dataclass
class Methodology:
    industry: str = ""
    decompositions: list[Decomposition] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)
    rationale: str = ""
    # 辩论① 主题的覆盖声明：(「覆盖」|「未覆盖」, 主题, 因子名或理由)。
    # **写进 drivers.md 的原始定义块**，人改过再重跑不丢（spec 2026-10-13 ④）。
    coverage_decl: list[tuple[str, str, str]] = field(default_factory=list)
    # 公式化后的分解式（`formula.FormulaModel`）。**下游一律读它。** 新写法直接
    # 解析得到；旧的 `分解:` 写法解析后由 `formula.from_decomps` 翻译（`legacy`），
    # 此时 `decompositions` 仍保留原样，旧文件的往返与旧闸门不变。
    model: FormulaModel | None = None

    def factors(self) -> list[Factor]:
        """全部因子（去重、按声明顺序）。水平模式下同一个因子可被两条公式共用。"""
        return list(self.model.factors) if self.model else _flat(self.decompositions)

    def of(self, target: str) -> Decomposition | None:
        return next((d for d in self.decompositions if d.target == target),
                    None)


def parse(text: str) -> Methodology:
    """解析分解式。出现 `公式:` 行按公式化写法，否则按旧的 `分解:` 写法。"""
    m = _parse_formulas(text) if _FORMULA.search(text) else _parse_legacy(text)
    m.pitfalls = [h.group(1).strip() for h in _PITFALL.finditer(text)]
    for kind, rx in (("覆盖", _COVER), ("未覆盖", _UNCOVER)):
        for h in rx.finditer(text):
            th, _, rest = h.group(1).partition("|")
            m.coverage_decl.append((kind, th.strip(), rest.strip()))
    return m


def _fields(raw: str) -> list[str]:
    return [p.strip() for p in raw.split("|")]


def _parse_formulas(text: str) -> Methodology:
    """公式化写法。**代码只查能不能算**（spec 2026-10-14 一）：模式合法、名字
    已声明且合法、公式在白名单内、两条都有；另保留因子个数（每条公式 2–5 个、
    声明了就要用到）与 key 不撞车，待子项目二由裁决者接管。旧写法的运算匹配、
    单位白名单、占比类、营收量级四道字面闸门在这里**不跑** —— 它们建立在
    「乘法链/加法链」概念上，公式化后无意义。
    """
    modes = [h.group(1).strip() for h in _MODE.finditer(text)]
    if len(modes) != 1:
        raise MethodologyFailed(
            f"要有且只有一行「模式: 变化」或「模式: 水平」，得到 {len(modes)} 行")
    mode = pick(modes[0], list(MODES)) or modes[0]
    factors: list[Factor] = []
    keys: dict[str, str] = {}
    for h in _FACTOR.finditer(text):
        parts = _fields(h.group(1))
        if len(parts) < 3 or not all(parts[:3]):
            raise MethodologyFailed(
                "因子一行应为「短名 | 完整名 | 单位 | 数据线索 | 检索词」，"
                f"得到 {h.group(1)!r}")
        short, label, unit = parts[0], parts[1], parts[2]
        _check_name(short, "因子短名")
        key = _slug(label)
        if key in keys:
            raise MethodologyFailed(
                f"因子「{keys[key]}」与「{label}」slug 后都是 {key!r} —— 下游按"
                "这个 key 对采集结果与工作簿行，撞车的那个会被错认。换一个更有"
                "区分度的完整名。")
        keys[key] = label
        try:
            factors.append(Factor(
                key=key, label=label, unit=unit,
                source_hint=parts[3] if len(parts) > 3 else "",
                search_term=(_strip_term_label(parts[4])
                             if len(parts) > 4 else ""),
                short=short))
        except DecompositionInvalid as e:
            raise MethodologyFailed(str(e)) from e
    consts: list[Constant] = []
    for h in _CONST.finditer(text):
        parts = _fields(h.group(1))
        if len(parts) < 3 or not all(parts[:3]):
            raise MethodologyFailed(
                "常量一行应为「名字 | 数值 | 出处」，出处不能空 —— 常量是公式里"
                f"不逐年判断的数，得说清它从哪来。得到 {h.group(1)!r}")
        _check_name(parts[0], "常量名")
        try:
            consts.append(Constant(parts[0], parse_constant(parts[1]), parts[2]))
        except FormulaInvalid as e:
            raise MethodologyFailed(str(e)) from e
    declared = {f.short for f in factors} | {c.name for c in consts}
    formulas: dict = {}
    for h in _FORMULA.finditer(text):
        lhs, sep, rhs = h.group(1).replace("＝", "=").partition("=")
        tgt = _FORMULA_TARGETS.get(lhs.strip())
        if not sep or tgt is None:
            raise MethodologyFailed(
                f"公式一行应为「营收 = …」或「毛利率 = …」，得到 {h.group(1)!r}")
        if tgt in formulas:
            raise MethodologyFailed(
                f"「{lhs.strip()}」的公式出现了不止一条 —— 只能给一条")
        try:
            formulas[tgt] = parse_formula(rhs, tgt, declared)
        except FormulaInvalid as e:
            raise MethodologyFailed(str(e)) from e
    model = FormulaModel(mode=mode, factors=factors, constants=consts,
                         formulas=formulas)
    try:
        model.validate()
    except FormulaInvalid as e:
        raise MethodologyFailed(str(e)) from e
    for tgt in formulas:
        n = len(model.factors_of(tgt))
        if not MIN_FACTORS <= n <= MAX_FACTORS:
            raise MethodologyFailed(
                f"「{TARGET_LABEL[tgt]}」的公式引用了 {n} 个因子 —— 每条 "
                f"{MIN_FACTORS}–{MAX_FACTORS} 个：一个不叫分解，超过五个人"
                "核实不过来。")
    used = {f.short for t in formulas for f in model.factors_of(t)}
    unused = [f.label for f in factors if f.short not in used]
    if unused:
        raise MethodologyFailed(
            f"声明了却没有任何公式用到的因子：{'、'.join(unused)} —— 删掉，或写进"
            "公式。不进公式的因子不影响营收与毛利率，却要逐年填值、联网采集。")
    return Methodology(model=model)


def _check_name(name: str, what: str) -> None:
    """在读到声明行时就查名字 —— 否则先报出来的会是「公式引用了未声明的名字」，
    把模型引向错误的修改方向。"""
    if not valid_name(name):
        raise MethodologyFailed(
            f"{what}「{name}」不合法 —— 不能含运算符、括号、空格，不能以数字开头，"
            f"不能是 {'、'.join(sorted(RESERVED))}")


def _parse_legacy(text: str) -> Methodology:
    """旧的 `分解: 目标 | 乘/加 | 因子:单位:线索:检索词 …` 写法。旧闸门照旧。"""
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
            term = _strip_term_label(bits[3]) if len(bits) > 3 else ""
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

    m.model = from_decomps(m.decompositions)
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
{context}
下面（若有）给出了这家公司本轮已经采集到的材料。请回答：**就多空双方
争执的这几个点，这家公司的营收和毛利率该怎么分解，才能把争议落到可测的
因子上？**

**先用上面已入库的卖方研报**；只在它们不足以支撑方法论判断时才上网补。
（这不改变卖方资料的可引用范围：你读研报读的是**方法论**，产出的是分解式
的**框架**，不是任何一个因子的取值。）材料为空时就只凭行业分类回答，
那是允许的降级路径。

先选**模式**（整份分解式只用一种）：

- **变化**：每个因子逐年填「变化」（增速、pp 变动……），公式描述「本年的变化 →
  本年目标」。营收、毛利率的第 0 年取账本实际值。适合拿得到变化率、拿不到绝对
  量的行业。
- **水平**：每个因子逐年填「水平」（出货量、单价、单位成本……），公式直接写目标
  的水平，代码用相邻两年算增速。第 0 年的营收、毛利率由各因子基期值代入公式算出
  （不用报表营收，分子分母同口径）。适合量价数据拿得到的行业。

再写因子、常量与两条公式：

`模式: <变化|水平>`
`因子: <短名> | <完整名> | <单位> | <数据线索> | <检索词>`
`常量: <名字> | <数值> | <出处>`
`公式: 营收 = <右边>`
`公式: 毛利率 = <右边>`

尖括号连同里面的字一起换成你的内容。示例（某封测公司，仅示意格式）：

`模式: 变化`
`因子: 产能增速 | 有效产能同比增速 | 变化率 | 年报经营情况讨论 | 有效产能`
`因子: 单价增速 | 平均单价同比变化率 | 变化率 | 研报价格跟踪 | 平均单价`
`因子: 降价侵蚀 | 降价对毛利率的累计影响 | pp | 研报价格跟踪 | 降价`
`因子: 折旧摊薄 | 稼动率提升对毛利率的累计摊薄 | pp | 年报成本构成 | 折旧`
`公式: 营收 = 上期(营收) × (1 + 产能增速) × (1 + 单价增速)`
`公式: 毛利率 = 基期(毛利率) + (降价侵蚀 + 折旧摊薄) / 100`

**公式怎么写**（代码只管能不能算；算得对不对，由审核者与人工门看）
- 只能用加减乘除、括号、数字、因子短名、常量名，以及 `上期(名字)`（上一年的
  值）、`基期(名字)`（第 0 年的值）。名字可以是因子短名，也可以是 营收、毛利率
  —— 但营收、毛利率只能写在 上期(…)/基期(…) 里。不提供 max、min、条件判断。
- 短名不能含运算符、括号、空格，不能以数字开头。
- 每条公式引用 2–5 个因子（一个不叫分解；超过五个人核实不过来）；声明了的因子
  都要进公式。
- 毛利率按小数计（26.5% 是 0.265），pp 进公式要 /100。营收的单位是亿元（只在你
  用到绝对金额时才要紧）。
- **换算写进公式，不要让代码猜**：变化模式下稼动率变了 −3pp，要换成营收的变化率
  就写 `稼动率变动 / 100 / 上期稼动率`，`上期稼动率` 用 `常量:` 声明数值与出处。
- **「累计」还是「逐年」在公式里看得见**：`基期(毛利率) + X / 100` 表示 X 是相对
  基期累计的 pp；`上期(毛利率) + X / 100` 表示 X 是逐年再变的 pp。选一种，在
  陷阱里写清楚。
- **分母在公司内部的占比**（如高端产品收入占比）变动，不等于营收的变动 —— 它对
  营收的影响经由均价体现；市占率（分母在公司之外）可以乘：营收 = 行业规模 ×
  市占率。

**检索词**

每个因子再给一个检索词：你认为年报/公告里会**逐字出现**、且**指向这个因子
本身**的那个词。

因子名可以写完整（「服务器及通信用高端覆铜板出货量同比增速」），但那样的整串
不会出现在任何年报里——检索词才是代码真正拿去全文搜索的东西。它**不必**是
因子名里的片段：若「客户集中度」比因子名里任何一段都准，就写「客户集中度」。

**但不要给泛词。** 代码会拿这个词在年报与公告全文里搜，命中就会被当成这个
因子的文字依据。给「产能」「覆铜板」「服务器」这类词，在一家覆铜板公司的
文件里必然命中几十次，而那些命中与这个因子无关——那是把一次巧合伪装成证据。
**宁可给一个搜不到的精确词（因子落「无数据」，诚实），也不要给一个到处都能
搜到的泛词。**

**不必猜命中次数。** 代码会把你给的每个因子的候选词（整串标签 → 去括号
→ 剥后缀 → 你给的检索词，共四级）在本轮语料里的实际命中次数算出来、印在
产物顶部。宁可给一个搜不到的精确词（因子落「无数据」，诚实），也不要给
一个到处都能搜到的泛词。
- 数据线索写明你**指望它来自哪里**：年报某一节 / 语料库某一篇 / 需要新采集。
  **若这个因子的数据就是账本里已有的某个 key**（见上面「账本里已有的 key」
  那一节，若有），在数据线索里写成 `[[key]]`，例如 `[[industry.revenue_yoy]]`
  —— 代码会直接取账本数据、不再去搜。**只是相关、不是同一个量的，不要写成
  `[[ ]]`**，照常用文字描述。优先分解成已有数据能支撑的因子 —— 采集不到的
  因子会落「无数据」，那一档的三个取值就只能凭判断填。

**覆盖要求**

上面「多空平衡点」那一节（若有）列出了双方争执的主题。**每个主题都应有一个
因子承载它** —— 某个主题多空分歧最大、而分解式里没有对应因子，下一轮辩论就
无从给出三档取值、判断项也无从反映这个分歧。对每个主题写一行声明：

`覆盖: <主题> | <承载它的因子短名或完整名，与上面写的一字不差>`
`未覆盖: <主题> | <为什么不能因子化>`

代码会核对你声明的因子名是否真在分解式里，并把结果标注在产物顶部。
**不会因此驳回你**，但人会在审核时看到。

最后，针对**你刚给出的这组因子**，指出它们自身的误用方式，每条一行：

`陷阱: <一句话>`

例如：哪个因子容易被双重计数、哪两个因子实际上不独立、哪个单位换算容易错。
这正是下游结构审查要对的账。**不要写行业通用风险** —— 那些已经在上面的
定性结论与多空辩论里。

只输出上述格式的行，不要其它说明。
"""


def _tail(raw: str, n: int = 400) -> str:
    raw = (raw or "").strip()
    return raw[-n:] if len(raw) > n else raw


def research(client, industry: str, code: str,
             max_attempts: int = 3, *,
             facts: str = "", corpus_note: str = "",
             qualitative: str = "", stance_note: str = "",
             available_keys: tuple[str, ...] = ()) -> Methodology:
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
    # **新输入全部可选，不传就退回行业级预研。** 那条路是
    # `_research_methodology` 的既有降级出口（预研失败 → 两标量模式），
    # 不能因为新增输入而失效 —— 见 `test_不传新输入时仍退回行业级预研`。
    blocks = []
    if facts:
        blocks.append(f"【可用事实】\n{facts}")
    if corpus_note:
        blocks.append(f"【已入库资料】\n{corpus_note}")
    if qualitative:
        blocks.append(f"【定性结论】\n{qualitative}")
    if stance_note:
        blocks.append(f"【多空平衡点】\n{stance_note}")
    if available_keys:
        blocks.append("【账本里已有的 key】\n" + "、".join(available_keys))
    prompt = PROMPT.format(industry=industry, code=code,
                           context=("\n" + "\n\n".join(blocks) + "\n"
                                    if blocks else ""))
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
        f"\n最后一次的原始输出（末 400 字）：\n{_tail(raw)}",
        raw=raw)


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
    if m.model is not None and not m.model.legacy:
        mm = m.model
        lines.append(f"模式: {mm.mode}")
        lines += [f"因子: {f.short} | {f.label} | {f.unit} | {f.source_hint} "
                  f"| {f.search_term}" for f in mm.factors]
        lines += [f"常量: {c.name} | {c.value} | {c.source}" for c in mm.constants]
        lines += [f"公式: {mm.describe(t)}" for t in ("revenue", "gross_margin")
                  if t in mm.formulas]
    for d in (m.decompositions if (m.model is None or m.model.legacy) else []):
        parts = [_TARGET_CN.get(d.target, d.target), _OP_CN.get(d.op, d.op.value)]
        # 四段写全。**少写检索词这一段，人工编辑 drivers.md 后重跑就会把它
        # 吃掉**——而「编辑这段文字再重跑即可覆盖」是写在文件末尾的承诺。
        parts += [f"{f.label}:{f.unit}:{f.source_hint}:{f.search_term}"
                  for f in d.factors]
        lines.append("分解: " + " | ".join(parts))
    for kind, th, rest in m.coverage_decl:
        lines.append(f"{kind}: {th} | {rest}")
    for p in m.pitfalls:
        lines.append(f"陷阱: {p}")
    return "\n".join(lines)


def theme_coverage(decomps: list, themes: list[str]) -> list[tuple[str, str]]:
    """辩论① 的每个主题，有没有被某个因子覆盖。**标注用，不阻断。**

    判据只能是词面：主题词出现在因子标签、数据线索或检索词里就算覆盖。
    「份额这件事有没有被这个分解式真的覆盖」是语义判断，代码判不了 ——
    所以这个函数的产出是一行标注，交给门② 的人读，不是闸门。实测教训
    就在手边：隐含份额检查想用算术判断一件该由语义判断的事，10 份存档上
    10/10 惰性。

    **要读 `source_hint`，不是 `hint`。** `Factor` 的字段叫 `source_hint`
    （`drivers.py:48`）；写成 `getattr(f, "hint", "")` 会静默取到空串，
    数据线索于是永不参与覆盖判断 —— 而「成本」这类主题常常只出现在数据
    线索里（「年报营业成本构成」），不在任何因子标签里。

    `themes` 为空（辩论① 失败且人工补写为空）时返回空列表 —— 不能凭空
    要求覆盖。
    """
    out: list[tuple[str, str]] = []
    factors = _flat(decomps)
    for th in themes:
        hit = ""
        if th:
            for f in factors:
                blob = (f"{f.label}{getattr(f, 'source_hint', '')}"
                        f"{getattr(f, 'search_term', '')}")
                if th in blob:
                    hit = f.label
                    break
        out.append((th, hit))
    return out


@dataclass(frozen=True)
class Cover:
    """一个辩论主题的覆盖情况。

    mark：✓ 模型声明由某因子承载（代码核对过因子名真实存在）/ ✗ 模型声明
    未覆盖 / ？ 模型没交代（detail 是词面匹配到的因子名，可能为空）。
    """
    theme: str
    mark: str
    detail: str


def coverage(m: Methodology, themes: list[str]) -> tuple[list[Cover], list[str]]:
    """辩论① 主题覆盖：**模型的声明为主，词面匹配兜底**。返回 (逐主题, 问题)。

    词面匹配（`theme_coverage`）报过假阴性：生益科技「行业产值同比增速」承载
    需求、「公司市占率同比变化率」承载份额，标签里没有那两个字。「这个因子承载
    哪个主题」是语义判断，该由给出分解式的模型声明；代码只核对声明里的因子名
    真实存在。

    全部是标注：清单外主题、因子名不存在、自相矛盾只列进问题，不驳回。
    `themes` 为空（辩论① 没有结论）时什么都不做 —— 不能凭空要求覆盖。
    """
    if not themes:
        return [], []
    factors = m.factors()
    labels = {f.label for f in factors}
    # 声明里写短名也认（公式化后模型更可能写短名），显示时换成完整名。
    by_short = {f.short: f.label for f in factors if getattr(f, "short", "")}
    cov: dict[str, str] = {}
    unc: dict[str, str] = {}
    notes: list[str] = []
    for kind, th, rest in m.coverage_decl:
        if th not in themes:
            notes.append(f"声明了清单外的主题「{th}」（{kind}: {rest}）")
        elif kind == "未覆盖":
            unc.setdefault(th, rest)
        elif rest in labels or rest in by_short:
            cov.setdefault(th, by_short.get(rest, rest))
        else:
            notes.append(f"「{th}」声明由「{rest}」承载，但分解式里没有这个因子")
    word = dict(theme_coverage(factors, themes))
    out: list[Cover] = []
    for th in themes:
        if th in cov and th in unc:
            notes.append(f"「{th}」既声明了覆盖又声明了未覆盖（自相矛盾），按覆盖显示")
        if th in cov:
            out.append(Cover(th, "✓", cov[th]))
        elif th in unc:
            out.append(Cover(th, "✗", unc[th]))
        else:
            out.append(Cover(th, "？", word.get(th, "")))
    return out, notes


def term_hits(decomps: list, *, sections=None, corpus=None
              ) -> dict[str, list[tuple[str, int]]]:
    """每个因子的四级候选词，在年报分节 + 语料库里各命中几次。

    **顺序必须与 `gaps._core_candidates` 一致** —— L1 整串标签 → L2 去括号
    → L3 剥后缀 → L4 模型给的检索词。检索词在最底端：L1–L3 全部落空才轮到
    它。只给检索词的次数，等于在优化用得最少的那根杠杆。

    **不设阈值、不反馈给模型**：次数原样印进 `drivers.md`，门② 的人看得见
    就能判断。原本想按实测取「> 10 次判为泛词嫌疑」，但那组实测只有五个
    样本（生益科技 8 篇公告 8.9 万字里覆铜板 98 / 产能 31 / 服务器 18 是
    反例，原材料成本 2 / 铜箔 6 是正例），而公告篇数与字数每家公司都不同
    —— 一个绝对次数的阈值换个行业就不对。

    计数用朴素的 `str.count`，不走 `gaps._valid_hit` 的否定语境过滤：这是
    标注而不是判定，多数几次不会误伤谁，而引入过滤会让印出来的数字与
    「实际能不能命中」之间多一层解释。
    """
    from ir_agent.gaps import _core_candidates

    blobs: list[str] = []
    if isinstance(sections, str):
        blobs.append(sections)
    elif sections is not None:
        from ir_agent.strategy import annual_text
        blobs.append(annual_text(sections))
    for d in (getattr(corpus, "docs", None) or []):
        blobs.append(getattr(d, "text", "") or "")
    hay = "\n".join(x for x in blobs if x)

    out: dict[str, list[tuple[str, int]]] = {}
    for f in _flat(decomps):
        cands = _core_candidates(f.label, getattr(f, "search_term", "") or "")
        out[f.label] = [(c, hay.count(c)) for c in cands]
    return out


def to_markdown(m: Methodology, coverage=(), hits=None,
                coverage_notes=(), key_refs=None) -> str:
    """分解式落盘为 drivers.md。

    `coverage` / `hits` 是**标注**，不参与 `from_markdown` 的往返解析 ——
    后者只读 text 围栏块与 `# 驱动因子分解（…）` 标题行。人改分解式时不必
    维护这两段，下次重跑由代码重算。
    """
    lines = [f"# 驱动因子分解（{m.industry}）", "",
             "本次研究采用的分解式。**它决定了后面去采集什么数据、"
             "以及批准清单上能检查到什么** —— 清单上若没有某个因子，"
             "说明这个因素没被纳入。", ""]
    if coverage:
        lines += ["## 辩论① 主题覆盖（标注，未阻断）", ""]
        for c in coverage:
            if c.mark == "✓":
                lines.append(f"- {c.theme}：✓ 模型声明由「{c.detail}」承载")
            elif c.mark == "✗":
                lines.append(f"- {c.theme}：✗ 模型声明未覆盖 —— "
                             f"{c.detail or '（没写理由）'}")
            else:
                lines.append(f"- {c.theme}：？ 模型未交代 —— 仅词面匹配："
                             + (f"「{c.detail}」" if c.detail else "无"))
        lines += [f"- ⚠ {x}" for x in coverage_notes]
        open_ = [c.theme for c in coverage if c.mark != "✓"]
        if open_:
            lines += ["", f"⚠ {len(open_)} 个辩论主题没有声明由哪个因子承载"
                          f"（{'、'.join(open_)}）—— 请在门② 复核：这几点"
                          "多空有分歧，而判断项可能无从反映它们。"]
        lines.append("")
    if hits:
        lines += ["## 因子候选词在本轮语料里的命中次数（标注，未阻断）", "",
                  "探测顺序：整串标签 → 去括号 → 剥后缀 → **模型给的检索词**"
                  "（最后一级）。`gaps` 按这个顺序逐级试，命中即停。"
                  "次数原样印出，不设阈值 —— 公告篇数与字数每家公司都不同，"
                  "一个绝对次数的判语换个行业就不对。", ""]
        for label, pairs in hits.items():
            shown = "　".join(f"{c}:{n}" for c, n in pairs)
            lines.append(f"- {label}　{shown}")
        lines.append("")
    if key_refs:
        lines += ["## 数据线索里声明的账本 key（标注，未阻断）", ""]
        for label, (have, miss) in key_refs.items():
            parts = ([f"✓ [[{x}]]" for x in have]
                     + [f"✗ {k}（账本里没有，或晚于研究时点才披露）" for k in miss])
            lines.append(f"- {label}：" + "　".join(parts))
        lines += ["", "声明了的因子直接取账本数据、不再联网采集。**口径是否与"
                      "因子名一致（例如因子写的是某机构口径、账本是同业聚合口径）"
                      "由人判断。**", ""]
    if m.model is not None and not m.model.legacy:
        lines += _formula_markdown(m.model)
    for d in (m.decompositions if (m.model is None or m.model.legacy) else []):
        lines.append(f"## {d.describe()}")
        lines.append("")
        # 检索词要在人读表里露出来：它决定代码实际去搜什么词，而一个泛词
        # （实测「服务器」在真实公告语料里 18 次巧合命中）会在没人注意的
        # 情况下决定一个因子的「年报披露」状态。
        lines.append("| 因子 | 单位 | 数据线索 | 检索词 |")
        lines.append("| --- | --- | --- | --- |")
        for f in d.factors:
            lines.append(f"| {f.label} | {f.unit} | {f.source_hint or '—'} "
                         f"| {f.search_term or '—'} |")
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
              "`模式:`、`因子: 短名 | 完整名 | 单位 | 数据线索 | 检索词`、"
              "`常量: 名字 | 数值 | 出处`、`公式: 营收 = …`、"
              "`陷阱: 一句话`（旧的 `分解: 目标 | 运算 | …` 写法也认）。删掉这段代码块或改坏语法，重跑时会**明确"
              "报错**而不是被静默忽略——不然你会以为自己的修改生效了，"
              "实际却被悄悄丢弃、重新联网跑出一份新的分解式。", "",
              "```text", _to_dsl(m), "```", ""]
    return "\n".join(lines)


def _formula_markdown(mm: FormulaModel) -> list[str]:
    """公式化分解式的人读部分：模式、因子表、常量表、两条公式。"""
    lines = [f"## 模式：{mm.mode}", "",
             ("每个因子逐年填**变化**（增速、pp 变动…），第 0 年营收与毛利率取账本。"
              if mm.mode == "变化" else
              "每个因子逐年填**水平**（量、价、成本…），第 0 年营收与毛利率由基期"
              "因子值代入公式算出 —— 不用报表营收，分子分母同口径。"), ""]
    lines += ["## 公式", ""]
    lines += [f"- {mm.describe(t)}" for t in ("revenue", "gross_margin")
              if t in mm.formulas]
    lines.append("")
    # 检索词要在人读表里露出来：它决定代码实际去搜什么词。
    lines += ["## 因子", "",
              "| 短名 | 因子 | 单位 | 数据线索 | 检索词 |",
              "| --- | --- | --- | --- | --- |"]
    lines += [f"| {f.short} | {f.label} | {f.unit} | {f.source_hint or '—'} "
              f"| {f.search_term or '—'} |" for f in mm.factors]
    lines.append("")
    if mm.constants:
        lines += ["## 常量", "", "| 名字 | 数值 | 出处 |", "| --- | --- | --- |"]
        lines += [f"| {c.name} | {c.value} | {c.source} |" for c in mm.constants]
        lines.append("")
    return lines


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
