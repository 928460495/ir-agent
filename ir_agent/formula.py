"""分解式公式化 —— 模型写公式，代码求值。

**为什么从「乘/加」两种形状改成公式**（2026-10-14，spec
`docs/superpowers/specs/2026-10-14-formula-decomposition-design.md`）。旧的分解式
只有两种形状：营收 = Π(1+变化率) − 1、毛利率 = 基期 + Σpp/100。形状之外的一切
（单位、分母在哪、是不是营收量级）都靠 `drivers` 里的字面闸门去近似，生益科技
第二次调用被「本司市占率（占全行业规模，非公司内部占比）同比增速」里一个带「非」
的「占比」挡了三次。公式化把「怎么合成」交给提出分解式的模型、由裁决者审，代码
只管**能不能算**。

**本模块只做两件事，都不是内容判断**：
1. 静态校验：公式能否解析、只用了白名单写法、引用的名字都已声明
   （`FormulaInvalid`）；
2. 逐年推演：把取值代进去算出营收水平/增速与毛利率（`FormulaError`：除以零、
   缺取值）。

**模式二选一**（用户决定 8）：同一家公司只能「变化」或「水平」。变化模式下
因子填每年的变化，第 0 年的营收、毛利率取账本；水平模式下因子填每年的水平，
第 0 年的营收、毛利率用基期因子值代入公式算出 —— 出货量×单价未必等于报表营收，
分子分母必须同口径。

**`上期(x)` 与 `基期(x)`**：前者取上一年，后者取第 0 年。旧毛利率加法链的 pp 是
**相对基期的累计值**（`assume._factor_head` 提示词明文），翻译成
`基期(毛利率) + (…)/100`，不是 `上期`。
"""

from __future__ import annotations

import ast
import keyword
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, DivisionByZero, InvalidOperation

TARGETS = {"营收": "revenue", "毛利率": "gross_margin"}
TARGET_CN = {v: k for k, v in TARGETS.items()}
MODES = ("变化", "水平")
PREV, BASE = "上期", "基期"
RESERVED = frozenset(TARGETS) | {PREV, BASE}


class FormulaInvalid(Exception):
    """公式写法本身算不了 —— 解析不了、用了白名单外的写法、名字未声明。"""


class FormulaError(Exception):
    """公式写法没问题，但代入取值后算不下去 —— 除以零、缺某年取值。"""


# 模型常写的全角/数学符号。只换写法，不改含义。
_SYMBOLS = str.maketrans({"×": "*", "÷": "/", "（": "(", "）": ")",
                          "−": "-", "＋": "+", "－": "-", "＊": "*", "／": "/"})


def _normalize(src: str) -> str:
    return src.translate(_SYMBOLS).strip()


def valid_name(name: str) -> bool:
    """短名、常量名的合法性：能当标识符、不撞保留字。

    「不得含运算符、括号、空格」（spec 一）由 `isidentifier` 一并保证。"""
    return (bool(name) and name.isidentifier() and name not in RESERVED
            and not keyword.iskeyword(name))


@dataclass(frozen=True)
class Formula:
    """一条已校验的公式。`target` 为 revenue / gross_margin。"""

    target: str
    text: str                 # 右边原文（给人看）
    tree: ast.expr = field(compare=False, repr=False)
    src: str = field(compare=False, repr=False, default="")   # 规范化后的右边

    def names(self) -> set[str]:
        """引用到的全部名字（含 上期/基期 的参数）。"""
        out: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name) and node.id not in (PREV, BASE):
                out.add(node.id)
        return out

    def lagged(self) -> set[tuple[str, str]]:
        """`(上期|基期, 名字)` 的全部出现。"""
        out: set[tuple[str, str]] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                out.add((node.func.id, node.args[0].id))
        return out

    def tokens(self) -> list[tuple[str, str]]:
        """给工作簿的记号序列，**全括号**以免依赖 Excel 的运算优先级。

        记号：("num", "100") / ("name", 短名或常量名) / ("prev", 名字) /
        ("base", 名字) / ("op", "+-*/") / ("lp", "(") / ("rp", ")")。
        `ashare-dcf` 只把记号换成单元格引用，**不做解析** —— 解析只在这里一处。
        """
        out: list[tuple[str, str]] = []
        _emit(self.tree, self.src, out)
        return out


def _emit(node, src: str, out: list) -> None:
    if isinstance(node, ast.BinOp):
        out.append(("lp", "("))
        _emit(node.left, src, out)
        out.append(("op", _OPS[type(node.op)]))
        _emit(node.right, src, out)
        out.append(("rp", ")"))
    elif isinstance(node, ast.UnaryOp):
        out += [("lp", "("), ("op", "-")]
        _emit(node.operand, src, out)
        out.append(("rp", ")"))
    elif isinstance(node, ast.Constant):
        out.append(("num", str(_num(node, src))))
    elif isinstance(node, ast.Name):
        out.append(("name", node.id))
    else:                                                    # ast.Call
        kind = "prev" if node.func.id == PREV else "base"
        out.append((kind, node.args[0].id))


_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


def _num(node: ast.Constant, src: str) -> Decimal:
    """数字按**源码原文**转 Decimal —— 经 float 会把 0.035 变成 0.0350000000…01。"""
    seg = ast.get_source_segment(src, node)
    return Decimal(seg if seg is not None else str(node.value))


def parse_formula(rhs: str, target: str, declared: set[str]) -> Formula:
    """把公式右边解析成 `Formula`。`declared` 是已声明的因子短名与常量名。

    白名单：加减乘除、负号、括号、数字、已声明的名字、只带一个名字参数的
    `上期(…)` / `基期(…)`。目标名（营收、毛利率）只能出现在 上期/基期 里 ——
    裸写本年的目标是循环定义。
    """
    src = _normalize(rhs)
    if not src:
        raise FormulaInvalid(f"{TARGET_CN.get(target, target)}的公式右边是空的")
    try:
        tree = ast.parse(src, mode="eval").body
    except SyntaxError as e:
        raise FormulaInvalid(
            f"{TARGET_CN.get(target, target)}的公式解析不了：{rhs!r}"
            f"（{e.msg}）") from e
    args = {id(n.args[0]) for n in ast.walk(tree)
            if isinstance(n, ast.Call) and n.args}
    for node in ast.walk(tree):
        _check_node(node, src, target, declared, rhs, args)
    return Formula(target=target, text=rhs.strip(), tree=tree, src=src)


def _check_node(node, src, target, declared, rhs, args) -> None:
    where = ast.get_source_segment(src, node) or ""
    cn = TARGET_CN.get(target, target)
    if isinstance(node, (ast.BinOp, ast.UnaryOp)):
        if isinstance(node, ast.BinOp) and type(node.op) not in _OPS:
            raise FormulaInvalid(f"{cn}的公式里「{where}」用了加减乘除以外的运算")
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, ast.USub):
            raise FormulaInvalid(f"{cn}的公式里「{where}」用了负号以外的一元运算")
    elif isinstance(node, (ast.operator, ast.unaryop, ast.Load)):
        pass
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise FormulaInvalid(f"{cn}的公式里「{where}」不是数字")
    elif isinstance(node, ast.Call):
        fn = node.func.id if isinstance(node.func, ast.Name) else ""
        if fn not in (PREV, BASE):
            raise FormulaInvalid(
                f"{cn}的公式里「{where}」调用了函数 —— 只允许 上期(…) 与 基期(…)，"
                "不提供 max/min/条件")
        if (len(node.args) != 1 or node.keywords
                or not isinstance(node.args[0], ast.Name)):
            raise FormulaInvalid(f"{cn}的公式里「{where}」：{fn}(…) 只能带一个名字")
        arg = node.args[0].id
        if arg not in declared and arg not in TARGETS:
            raise FormulaInvalid(f"{cn}的公式里「{where}」引用了未声明的名字「{arg}」")
    elif isinstance(node, ast.Name):
        if node.id in (PREV, BASE):
            return                                   # 作为 Call.func 出现
        if node.id in TARGETS:
            if id(node) not in args:
                raise FormulaInvalid(
                    f"{cn}的公式里裸写了「{node.id}」—— 目标只能出现在 上期(…)/"
                    "基期(…) 里，裸写本年的目标是循环定义")
            return
        if node.id not in declared:
            raise FormulaInvalid(f"{cn}的公式里引用了未声明的名字「{node.id}」"
                                 f"（公式：{rhs.strip()}）")
    else:
        raise FormulaInvalid(f"{cn}的公式里「{where}」是白名单之外的写法")


def parse_constant(raw: str) -> Decimal:
    """常量取值：「78%」→ 0.78，「0.78」「58.2」原样。"""
    t = unicodedata.normalize("NFKC", raw).strip().replace(",", "")
    try:
        if t.endswith("%"):
            return Decimal(t[:-1]) / 100
        return Decimal(t)
    except InvalidOperation as e:
        raise FormulaInvalid(f"常量取值不是数字：{raw!r}") from e


@dataclass(frozen=True)
class Constant:
    name: str
    value: Decimal
    source: str


@dataclass
class FormulaModel:
    """一家公司的分解式：模式、因子、常量、两条公式。

    `factors` 的元素是 `drivers.Factor`（带 `short`）。`legacy=True` 表示由旧的
    乘/加分解式翻译而来（`from_decomps`）——读回旧文件时用，行为与旧引擎逐位相同。
    """

    mode: str
    factors: list
    constants: list[Constant] = field(default_factory=list)
    formulas: dict[str, Formula] = field(default_factory=dict)
    legacy: bool = False

    def factor(self, short: str):
        return next((f for f in self.factors if f.short == short), None)

    def const(self, name: str) -> Constant | None:
        return next((c for c in self.constants if c.name == name), None)

    def factors_of(self, target: str) -> list:
        """某条公式引用到的因子，按声明顺序。"""
        used = self.formulas[target].names() if target in self.formulas else set()
        used |= {n for _, n in (self.formulas[target].lagged()
                                if target in self.formulas else ())}
        return [f for f in self.factors if f.short in used]

    def needs_base(self) -> list:
        """需要第 0 年取值的因子：水平模式下公式引用到的全部因子；变化模式下
        公式里出现 上期(因子)/基期(因子) 的。"""
        need: set[str] = set()
        for fm in self.formulas.values():
            if self.mode == "水平":
                need |= {n for n in fm.names() if self.factor(n) is not None}
            need |= {n for _, n in fm.lagged() if self.factor(n) is not None}
        return [f for f in self.factors if f.short in need]

    def describe(self, target: str) -> str:
        fm = self.formulas.get(target)
        return f"{TARGET_CN[target]} = {fm.text}" if fm else ""

    def validate(self) -> None:
        """能不能算：模式合法、两条公式都有、名字合法且不重复。"""
        if self.mode not in MODES:
            raise FormulaInvalid(f"模式只能是「变化」或「水平」，得到 {self.mode!r}")
        missing = [cn for cn, t in TARGETS.items() if t not in self.formulas]
        if missing:
            raise FormulaInvalid(f"缺少 {'、'.join(missing)} 的公式 —— 两条都要给")
        seen: set[str] = set()
        for n in [f.short for f in self.factors] + [c.name for c in self.constants]:
            if not valid_name(n):
                raise FormulaInvalid(
                    f"名字「{n}」不合法 —— 短名与常量名不能含运算符、括号、空格，"
                    f"不能以数字开头，不能是 {'、'.join(sorted(RESERVED))}")
            if n in seen:
                raise FormulaInvalid(f"名字「{n}」重复声明")
            seen.add(n)


@dataclass(frozen=True)
class Path:
    """一档的推演结果。`revenue` 是营收水平（第 1..N 年），`growth` 是营收增速，
    `margin` 是毛利率水平。`revenue0`/`margin0` 是第 0 年。"""

    revenue0: Decimal
    margin0: Decimal
    revenue: list
    growth: list
    margin: list


def _eval(node, src, cur, prev, base, consts, where) -> Decimal:
    if isinstance(node, ast.BinOp):
        a = _eval(node.left, src, cur, prev, base, consts, where)
        b = _eval(node.right, src, cur, prev, base, consts, where)
        try:
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            return a / b
        except (DivisionByZero, InvalidOperation, ZeroDivisionError) as e:
            raise FormulaError(f"{where}：「{ast.get_source_segment(src, node)}」"
                               "除以零") from e
    if isinstance(node, ast.UnaryOp):
        return -_eval(node.operand, src, cur, prev, base, consts, where)
    if isinstance(node, ast.Constant):
        return _num(node, src)
    if isinstance(node, ast.Call):
        name = node.args[0].id
        table, what = (prev, "上期") if node.func.id == PREV else (base, "基期")
        if table is None or name not in table or table[name] is None:
            raise FormulaError(f"{where}：取不到 {what}({name})")
        return table[name]
    name = node.id
    if name in consts:
        return consts[name]
    if name not in cur or cur[name] is None:
        raise FormulaError(f"{where}：缺「{name}」的取值")
    return cur[name]


def evaluate(fm: Formula, cur: dict, prev: dict | None, base: dict | None,
             consts: dict, where: str = "") -> Decimal:
    return _eval(fm.tree, fm.src, cur, prev, base, consts,
                 where or TARGET_CN.get(fm.target, fm.target))


def simulate(model: FormulaModel, values: dict[str, list], *,
             base_factors: dict | None = None,
             base_revenue: Decimal | None = None,
             base_margin: Decimal | None = None,
             anchor_y1: Decimal | None = None) -> Path:
    """一档的逐年推演。`values[短名]` 是第 1..N 年的取值（已按单位换算）。

    第 0 年：变化模式取 `base_revenue`/`base_margin`（账本）；水平模式用
    `base_factors` 代入公式算出（口径不混用）。

    `anchor_y1`（半年报锚定后该档第 1 年毛利率）给定时，第 2 年起：`上期(毛利率)`
    取锚定值；`基期(毛利率)` 平移为 `基期 + (锚定值 − 第 1 年公式值)` —— 与旧
    `assume.margin_bases` 的平移逐位一致（spec「写计划时的补充」2）。第 1 年的
    合成值本身不被锚定替换（工作簿另行锚定覆盖）。
    """
    base_factors = dict(base_factors or {})
    consts = {c.name: c.value for c in model.constants}
    years = max((len(v) for v in values.values()), default=0)
    rev_f, gm_f = model.formulas["revenue"], model.formulas["gross_margin"]
    if model.mode == "水平":
        y0 = dict(base_factors)
        r0 = evaluate(rev_f, y0, None, y0, consts, "第 0 年营收")
        m0 = evaluate(gm_f, y0, None, y0, consts, "第 0 年毛利率")
    else:
        if base_revenue is None or base_margin is None:
            raise FormulaError("变化模式需要第 0 年的营收与毛利率（账本），没有给")
        r0, m0 = Decimal(base_revenue), Decimal(base_margin)
    base = dict(base_factors)
    base["营收"], base["毛利率"] = r0, m0
    prev = dict(base)
    revenue, growth, margin = [], [], []
    for t in range(1, years + 1):
        cur = {}
        for short, seq in values.items():
            if len(seq) < t:
                raise FormulaError(f"缺「{short}」第 {t} 年的取值")
            cur[short] = seq[t - 1]
        r = evaluate(rev_f, cur, prev, base, consts, f"第 {t} 年营收")
        m = evaluate(gm_f, cur, prev, base, consts, f"第 {t} 年毛利率")
        if prev["营收"] == 0:
            raise FormulaError(f"第 {t} 年：上一年营收为零，增速算不出来")
        growth.append(r / prev["营收"] - 1)
        revenue.append(r)
        margin.append(m)
        prev = dict(cur)
        prev["营收"], prev["毛利率"] = r, m
        if t == 1 and anchor_y1 is not None:
            prev["毛利率"] = Decimal(anchor_y1)
            base = dict(base)
            base["毛利率"] = m0 + (Decimal(anchor_y1) - m)
    return Path(revenue0=r0, margin0=m0, revenue=revenue, growth=growth,
                margin=margin)


# ── 旧格式翻译 ────────────────────────────────────────────────────

_STRIP = re.compile(r"[^\w]")


def short_from_label(label: str, taken: set[str]) -> str:
    """由完整名生成短名：去括号、标点、空格；以数字开头加「f」；撞了加序号。"""
    s = _STRIP.sub("", unicodedata.normalize("NFKC", label)) or "因子"
    if s[0].isdigit():
        s = "f" + s
    if not valid_name(s):
        s = "因子" + s
    out, i = s, 2
    while out in taken or not valid_name(out):
        out, i = f"{s}{i}", i + 1
    taken.add(out)
    return out


def from_decomps(decomps: list) -> FormulaModel:
    """旧的乘/加分解式 → 变化模式公式。**结果与旧 `drivers.compose` 逐位相同。**

    乘 → `营收 = 上期(营收) × (1+A) × (1+B)…`（增速 = Π(1+r) − 1）；
    加 → `毛利率 = 基期(毛利率) + (A + B…)/100`（旧加法链 pp 是相对基期累计值）。
    """
    from dataclasses import replace

    from ir_agent.drivers import Op

    taken: set[str] = set()
    factors, formulas = [], {}
    for d in decomps:
        shorts = []
        for f in d.factors:
            s = f.short if getattr(f, "short", "") else short_from_label(f.label, taken)
            taken.add(s)
            factors.append(replace(f, short=s))
            shorts.append(s)
        if d.op is Op.MULTIPLY:
            rhs = "上期(营收)" + "".join(f"*(1+{s})" for s in shorts)
        else:
            rhs = "基期(毛利率)+(" + "+".join(shorts) + ")/100"
        declared = set(shorts)
        formulas[d.target] = parse_formula(rhs, d.target, declared)
    return FormulaModel(mode="变化", factors=factors, formulas=formulas,
                        legacy=True)
