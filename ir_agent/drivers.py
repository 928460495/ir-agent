"""驱动因子分解式 —— 「这个行业靠什么赚钱」的可计算表达。

**闭合是硬约束，不是精度问题。** 「营收 = 产能 × 稼动率 × 单价」闭合，
三项能乘回营收；「营收 = 产能 × 品牌力」不闭合 —— 它是个比喻。一旦代码
要从因子合成营收增速，不闭合的分解式就根本算不出来。

而 AI 很容易产出听着专业但不闭合的分解:「产能 × 稼动率 × 单价 × 客户结构
优化」前三项能乘、第四项不能。**要求每个因子声明单位，写不出单位的项就
写不进来** —— 这是这里真正的闸门。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Op(str, Enum):
    MULTIPLY = "乘"      # 营收增速 = Π(1 + 因子变化率) − 1
    ADD_PP = "加"        # 毛利率   = 基期 + Σ 因子贡献(pp)


# 单位白名单**只有两个值**，与 op 严格绑定。
#
# pp 不得进乘法链: 稼动率 85%→82% 是 −3pp，但作为乘法因子它的变化率是
# 82/85 − 1 = −3.5%。两者不等；混用会让合成结果系统性偏错，而偏差不大、
# 不容易被发现 —— 正是那种会一直藏着的错。
UNITS: dict[Op, str] = {Op.MULTIPLY: "变化率", Op.ADD_PP: "pp"}

TARGETS: dict[str, Op] = {"revenue": Op.MULTIPLY, "gross_margin": Op.ADD_PP}
TARGET_LABEL = {"revenue": "营收", "gross_margin": "毛利率"}

MIN_FACTORS = 2      # 一个因子不叫分解
MAX_FACTORS = 5      # 超过五个人核实不过来


class DecompositionInvalid(Exception):
    """分解式不闭合 —— 代码算不出来，拒绝往下走。"""


@dataclass(frozen=True)
class Factor:
    key: str            # capacity | utilization | asp
    label: str          # 产能 / 稼动率 / 单价
    unit: str           # 必须是 UNITS 里的值
    source_hint: str = ""   # 这个数去哪找


@dataclass
class Decomposition:
    target: str                 # revenue | gross_margin
    op: Op
    factors: list[Factor] = field(default_factory=list)

    def describe(self) -> str:
        sep = " × " if self.op is Op.MULTIPLY else " + "
        body = sep.join(f.label for f in self.factors)
        head = TARGET_LABEL.get(self.target, self.target)
        return f"{head} = {body}" if self.op is Op.MULTIPLY \
            else f"{head} = 基期 + {body}"


# 乘法链里的「占比/比重/结构/…」是**组合**变量，不是营收的乘数：高端产品收入
# 占比 20%→22.4% 是 +12% 的相对变化，但营收不会因此多长 12%——结构升级对营收
# 的影响只经由均价体现（占比变化 × 高端溢价），对毛利率的影响走加法链。
# 生益科技实跑出过「营收 = 产能 × 利用率 × 铜价传导 × 高端产品收入占比」，四个
# 乘数里单项贡献最大的就是这个量纲错误，而这里原本只查单位与算术闭合，放行了。
# 市占率不在此列：营收 = 行业规模 × 市占率 是合法的乘法分解，分母在公司之外。
#
# **I1（final-fix-D 终审）：判据要按量纲收，不能按用词收。** 审查者实跑
# 22 个标签发现两个洞：
# 1. 旧版只认「占比|比重|结构」，「比例/构成/渗透率/化率/贡献度」这些
#    同义写法（分母同样是公司自己的总量）完全漏网——模型下一轮改一个字
#    就能绕过闸门。这里把同义词都收进来，判据仍然是同一件事：拿公司
#    自己收入的一部分除以公司自己的总量，不是合法的营收乘数。
# 2. 旧版 `_SHARE_OK` 在**整条标签**上做豁免，只要标签里出现「份额」二字
#    就整条放行——「客户份额占比」这种既含「占比」又含「份额」的写法，
#    量纲错误一个字都没改，照样通过。现在改成：先把「市占率/市场份额」
#    这类**真正合法**的市场份额措辞从标签里剥掉，再看剩下的部分还有没有
#    占比类同义词——「客户份额占比」剥掉之后（它根本不含"市占率"或
#    "市场份额"这两个完整措辞）仍是原样，照样会被「占比」挡住；而
#    「市场份额」整条被剥空，自然放行。
#
# 「结构」还会误伤真实产品品类名——「结构件」（如"精密结构件"）本身是
# 名词，不是"产品结构"这种组合变量，用负向前瞻把「结构件」摘出去。
_MIX = re.compile(r"占比|比重|比例|构成|渗透率|化率|贡献度|份额|结构(?!件)")
_SHARE_OK = re.compile(r"市占率|市场份额")


def _is_mix_factor(label: str) -> bool:
    """量纲错误判据：剥掉「市占率/市场份额」这类合法措辞之后，标签里
    还有没有占比类同义词。不能反过来"整条标签只要出现份额就豁免"——
    那会放行「客户份额占比」这种量纲错误一字未改的写法（I1）。"""
    return bool(_MIX.search(_SHARE_OK.sub("", label)))


def _check_target_op(d: Decomposition) -> None:
    want_op = TARGETS.get(d.target)
    if want_op is None:
        raise DecompositionInvalid(
            f"未知的分解目标 {d.target!r}，只能是："
            + "、".join(TARGETS))
    if d.op is not want_op:
        raise DecompositionInvalid(
            f"{d.target} 只能用 {want_op.value}（{UNITS[want_op]}），"
            f"得到 {d.op.value}")


def _check_factor_dims(d: Decomposition) -> None:
    want_unit = UNITS[d.op]
    seen: set[str] = set()
    for f in d.factors:
        if f.unit != want_unit:
            # 补充说明必须按「实际传入的 unit 到底错在哪」分支，不能按
            # 「目标链是什么」分支 —— 否则「定性」这种压根不是 pp 的单位
            # 也会被套上「pp 不能进乘法链」的解释，把人导向一个不存在的
            # 问题。误导性的提示比没有提示更糟：用户会去检查一个不存在的
            # 问题，真正的原因（写不出单位的因子本就不该出现）反而被掩盖。
            if f.unit == "pp" and want_unit == "变化率":
                detail = ("pp 不能进乘法链 —— 稼动率 85%→82% 是 −3pp，"
                          "但乘法因子的变化率是 −3.5%。")
            elif f.unit == "变化率" and want_unit == "pp":
                detail = "比率的变化只能加减，不能相乘。"
            else:
                # 传入的单位既不是「变化率」也不是「pp」，两条链都不认——
                # 这不是选错了链，是这个因子从一开始就写不出可量化的单位，
                # 而写不出单位的因子本就不该出现在分解式里，这正是这道
                # 校验存在的理由。
                detail = (
                    f"{f.unit!r} 不在单位白名单里，可用的只有 '变化率' 与 "
                    "'pp'；写不出单位的因子（比如定性描述）本就不该出现在"
                    "分解式里。")
            raise DecompositionInvalid(
                f"因子「{f.label}」的单位是 {f.unit!r}，"
                f"而 {d.op.value} 法链只接受 {want_unit!r}。" + detail)
        # 放在单位校验之后：「客户结构优化:定性」这类仍按单位报错，原因更根本。
        #
        # I1：这句驳回消息会被 `methodology.research` 原样拼进下一轮提示词
        # 逼模型重新输出——**不能在这里写出可以照抄的安全词**。此前结尾写
        # 「市占率可以乘」，模型最省力的"修正"就是把标签里的字换成「份额」，
        # 量纲错误一个字都没改，闸门却因为整条标签含"份额"被放行。这里改成
        # 讲道理（分母在公司内部 vs 在公司外部），不给一个可以直接复制的词。
        if d.op is Op.MULTIPLY and _is_mix_factor(f.label):
            raise DecompositionInvalid(
                f"因子「{f.label}」衡量的是「本公司内部某项收入/客户占公司"
                "总量的比重」，分母是公司自己的总量，不能进营收乘法链——"
                "这类因子的变化是相对占比的变化，不是营收本身的变化率"
                "（高端产品收入占比 20%→22.4% 是 +12% 的相对变化，营收不会"
                "因此多长 12%）。这类因子对营收的影响请并进「平均单价/ASP」，"
                "对毛利率的影响放进毛利率加法链。只有分母在**公司之外**的"
                "份额型指标（该指标 × 行业规模 = 营收）才是合法的营收乘数。")
        if f.key in seen:
            raise DecompositionInvalid(f"因子 key 重复：{f.key}")
        seen.add(f.key)


def check_dimensions(d: Decomposition) -> None:
    """只查**会算错数**的那几条：目标与运算配对、因子单位、占比类因子进
    乘法链、key 重复。不查因子个数。

    给读回路径用（`assume._check_factors`）：判断项从 assumptions.json 读
    回来时，`_decomp_from_dict` 直接构造对象，预研时的闸门一次都没跑——
    生益科技目录里残留的 9/14 那份判断项，营收链里还有「高端产品收入占比」，
    能一路走进建模。读回这道闸门防的是算错数（pp 进乘法链差一百倍、
    占比当增速乘），而「一个因子不叫分解」是方法论质量规则，预研时把关
    即可，读回时再拦只会误伤。
    """
    _check_target_op(d)
    _check_factor_dims(d)


def check_closure(d: Decomposition) -> None:
    """闭合校验。不合法抛 DecompositionInvalid，**不返回布尔值** ——
    调用方拿到的必须是「为什么不行」，不是一个 False。"""
    _check_target_op(d)

    n = len(d.factors)
    if n < MIN_FACTORS:
        raise DecompositionInvalid(
            f"至少 {MIN_FACTORS} 个因子，得到 {n} —— 一个因子不叫分解")
    if n > MAX_FACTORS:
        raise DecompositionInvalid(
            f"至多 {MAX_FACTORS} 个因子，得到 {n} —— "
            "分解的价值在于人能检查，不在于穷举")

    _check_factor_dims(d)


# ============================================================================
# 合成与历史回算
# ============================================================================

def compose(d: Decomposition, values: dict[str, Decimal],
            base: Decimal | None = None) -> Decimal:
    """按 op 把因子合成回目标。

    MULTIPLY → 返回**增速**（Π(1+r) − 1）
    ADD_PP   → 返回**绝对水平**（基期 + Σpp/100），因此必须给 base

    取值缺失一律报错而不当零: 当零会让一个漏填悄悄变成「该因子无影响」。

    **契约：`d` 必须已经过 `check_closure` 校验，本函数不重复校验闭合性。**
    这是分层设计，不是疏漏 —— 闭合性（op 与 target 匹配、单位在白名单内、
    pp 不得进乘法链等）是 `Decomposition` **构造期**该确认一次的事；如果
    每次 compose（尤其是 reconcile 里对每个历史期间循环调用）都重新跑一遍
    闭合校验，就是把一次性的检查摊进了热路径。调用方若绕过构造流程手拼一个
    Decomposition 直接传进来，责任在调用方——先 check_closure，再 compose。
    """
    missing = [f.key for f in d.factors if f.key not in values]
    if missing:
        raise DecompositionInvalid(
            f"因子取值缺失：{'、'.join(missing)} —— 缺项当零会让漏填"
            "悄悄变成「该因子无影响」")

    if d.op is Op.MULTIPLY:
        out = Decimal(1)
        for f in d.factors:
            out *= (Decimal(1) + values[f.key])
        return out - Decimal(1)

    if base is None:
        raise DecompositionInvalid(
            "加法链需要基期水平 —— 毛利率是绝对值，没有基期算不出来，"
            "默认成 0 会得到一个看起来正常的错数")
    return base + sum(values[f.key] for f in d.factors) / Decimal(100)


@dataclass(frozen=True)
class Residual:
    period: str
    composed: Decimal
    actual: Decimal
    gap: Decimal          # actual − composed


def reconcile(d: Decomposition,
              history: dict[str, dict[str, Decimal]],
              actual: dict[str, Decimal],
              base: dict[str, Decimal] | None = None) -> list[Residual]:
    """历史回算 —— **报残差，不判对错**。

    Π(1+r_i) 几乎永远不会精确等于实际营收增速: 产品结构、汇率、并表范围都
    会留下残差。若要求严格相等，没有一个分解式能通过。

    残差**小且稳定** → 分解抓住了主要矛盾。
    残差**大或跳动** → 有重要因子没被纳入，这件事要让人看见。

    数据不全的期间**跳过而非用零补齐** —— 补出来的残差是假的。

    base 缺失是与「因子缺失」同一类事: ADD_PP 链没有基期就算不出绝对水平，
    这里用显式前置检查跳过该期间，而不是指望 compose 抛 DecompositionInvalid
    再拿 except 吞掉 —— 拿异常做预期内的正常分支控制，会把「base 缺失」和
    「真正的闭合性错误」混进同一个 except；compose 的校验逻辑将来一变，
    没有测试能发现这里悄悄吞掉了新的错误。
    """
    out: list[Residual] = []
    for period in sorted(history):
        vals = history[period]
        if any(f.key not in vals for f in d.factors) or period not in actual:
            continue
        if d.op is Op.ADD_PP and (base is None or period not in base):
            continue
        c = compose(d, vals, (base or {}).get(period))
        a = actual[period]
        out.append(Residual(period=period, composed=c, actual=a, gap=a - c))
    return out


def pp_hint(rate: Decimal, base_level: Decimal | None) -> str:
    """变化率 → 便于阅读的 pp 换算。**基期未知就不换算。**

    倒推出来的 pp 是个凭空的数 —— 显示它会让读者以为我们知道基期水平。
    """
    txt = f"{rate:.2%}"
    if base_level is None:
        return txt
    return f"{txt}（约 {base_level * rate * 100:+.2f}pp）"
