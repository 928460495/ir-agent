"""占位符解析 —— LLM 写 [[source_id#key@period]]，代码填真值。

这是「模型物理上无法编造数字」的实现点。正文里出现裸数字即视为幻觉入口，
由 audit_bare_numbers 在渲染前拦截。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ir_agent.ledger import Fact, FactLedger

# 两种写法都支持:
#   [[key@period]]            分析师用 —— 来源由账本解析，短且跨运行稳定
#   [[source#key@period]]     显式指定来源 —— 同一 key 有多个来源时的唯一手段
REF_RE = re.compile(
    r"\[\[(?:([A-Za-z0-9_\-]+)#)?([A-Za-z0-9_.]+)@([A-Za-z0-9_\-]+)\]\]")

_YI = Decimal("100000000")
_WAN = Decimal("10000")


class UnresolvedReferenceError(Exception):
    """正文引用了账本里不存在、或 source_id 对不上的事实。"""


@dataclass(frozen=True)
class Footnote:
    marker: int
    key: str
    period: str
    source_id: str
    as_of: date
    method: str
    display: str = ""      # 该引用在正文中渲染成的字符串

    def text(self) -> str:
        return (
            f"[{self.marker}] {self.key}@{self.period} · "
            f"来源 {self.source_id} · 披露日 {self.as_of.isoformat()} · {self.method}"
        )


def format_value(fact: Fact) -> str:
    v = fact.value
    if fact.unit == "ratio":
        return f"{v * 100:.2f}%"
    if fact.unit == "%":
        return f"{v:.2f}%"
    if fact.unit == "元":
        if abs(v) >= _YI:
            return f"{v / _YI:.2f} 亿元"
        if abs(v) >= _WAN:
            return f"{v / _WAN:.2f} 万元"
        return f"{v:.2f} 元"
    if fact.unit == "x":
        return f"{v:.2f}x"
    if fact.unit == "分位":
        return f"{v:.0f} 分位"
    if fact.unit == "家":
        return f"{v:.0f} 家"
    # 兜底也要限精度 —— 未知单位不该把 Decimal 原始精度打进研报
    return f"{v:.2f} {fact.unit}".replace(".00 ", " ")


def render(
    draft: str, ledger: FactLedger, as_of: date
) -> tuple[str, list[Footnote]]:
    """把草稿里的占位符替换成真值，并返回脚注表。

    LookAheadError 不在此处捕获 —— 时点违规必须冒泡到调用方。
    """
    notes: list[Footnote] = []
    seen: dict[tuple[str, str, str], int] = {}

    def _sub(m: re.Match[str]) -> str:
        source_id, key, period = m.group(1), m.group(2), m.group(3)
        try:
            fact = ledger.get(key, period, as_of=as_of)
        except KeyError as e:
            raise UnresolvedReferenceError(
                f"账本中没有 {key}@{period}，但正文引用了它。"
            ) from e

        # 简写形式不指定来源 —— 由账本解析。省的是分析师的输入，不是溯源:
        # 脚注里仍然写入真实 source_id。
        if source_id is not None and fact.source_id != source_id:
            raise UnresolvedReferenceError(
                f"{key}@{period} 的 source_id 不匹配：正文写的是 {source_id!r}，"
                f"账本记录为 {fact.source_id!r}。"
            )

        ident = (fact.source_id, key, period)
        if ident not in seen:
            seen[ident] = len(seen) + 1
            notes.append(Footnote(
                marker=seen[ident], key=key, period=period,
                source_id=fact.source_id, as_of=fact.as_of,
                method=fact.method.value, display=format_value(fact),
            ))
        return format_value(fact)

    return REF_RE.sub(_sub, draft), notes


# 排除：年份标签(2025年/2025年报)、季度标签(2025Q3/Q3)、纯序号
_LABELS = re.compile(
    # ISO 日期（即期期间标签，如 2026-09-04）—— 必须排在年份规则之前
    r"(19|20)\d{2}-\d{2}-\d{2}"
    # 年-月（宏观月度期间标签，如 2026-07）。必须整体匹配掉:
    # 只靠下面的「年份后跟连字符」规则会剥掉 2026 却留下 -07，
    # 残留的 07 随后被判成裸数字。宏观层引入月度标签后实测踩到。
    r"|(19|20)\d{2}[-/]\d{1,2}\b"
    # 期间标签: 2025FY / 2025H1 / 2025Q1-Q3 / 2025Q3S / 2025年 / 2025年度 /
    # 2025财年 / 2026上半年 —— 「财年」「上半年」必须排在「年」之前，
    # 否则「年」先匹配掉，剩下的「财」「上半」不成标签
    r"|(19|20)\d{2}\s*(FY|H[12]|Q[1-4]S?(-Q[1-4])?"
    r"|财年度|财年|年度|年报|上半年|下半年|全年|年)"
    r"|(19|20)\d{2}(?=[\-/])"
    r"|\bQ[1-4]S?\b"
    r"|第[一二三四]季度"
    # 月份指代。「8月制造业PMI回升」里的 8 是日历月不是数值 —— 与上面的
    # 「第一季度」同一条道理。跑长电时实测: 宏观定性的传导路径里写
    # 「8月制造业PMI回升」「9月加息预期升温」，两个月份都被判成裸数字。
    # 用负前瞻排除「3个月」这类时长表述。
    r"|(?<![\d.])\d{1,2}\s*月份?(?![\d])"
    # 指数名称。「沪深300」里的 300 是名字的一部分，不是数值 —— 实测正文
    # 写「对沪深300 的敏感度」时被判成裸数字，而白名单里又没有任何占位符
    # 能代替一个指数的名字。
    r"|(?:沪深|中证|上证|深证|科创|创业板指|北证|恒生)\s*\d{2,4}"
    # 期限标签。「10年期国债」的 10 是期限，不是财务数值。
    r"|\d{1,2}\s*年期"
    # 指标名称里的数字不是数值。CR5 是「前五大集中度」这个指标的名字，
    # 实测正文写「行业CR5为 …」时那个 5 被判成裸数字。
    # 用前后瞻而非 \b: 中文是 Unicode 词字符，「行业CR5为」两侧都不成词边界
    r"|(?<![A-Za-z])CR\d{1,2}(?!\d)"
    # 文献引用的章节号。「Bodie Ch.17」「第 8 章」里的数字是出处不是数值。
    r"|(?:Ch\.?|Chapter)\s*\d{1,2}"
    r"|第\s*\d{1,2}\s*章"
    # 本报告自身的章节号。**撰写规则明确要求交叉引用**（「第 8 节的每一条
    # 假设都应能追溯到第 2–7 节」），而白名单只放行「第 X 章」不放行
    # 「第 X 节」—— 提示词让写、审计不让写，自相矛盾。实测踩到:
    # 正文写「第 4 节」「第 6 节」，4 与 6 被判成裸数字，且无占位符可替代。
    r"|第\s*\d{1,2}\s*[节部分]"
    r"|第\s*\d{1,2}\s*[-–—~至]\s*\d{1,2}\s*节"
    # 货币层次的名称。M1、M2 是口径名不是数值 ——「M1−M2 剪刀差」里的
    # 1 和 2 被判成裸数字，而账本里没有任何占位符能代替一个口径的名字。
    r"|(?<![A-Za-z])M[0-3](?!\d)"
    # 段落序号。研报的风险提示惯用（1）（2）；括号内孤立的一两位数
    # 在这个语境里只可能是序号 —— 真实数值必带单位。
    r"|[（(]\s*\d{1,2}\s*[）)]"
    # 顿号序号 / markdown 有序列表。中文写作里"1、2、3……"比括号序号
    # 更常见，markdown 的"1. 2. 3. ……"同理，但白名单此前只放行了
    # 括号写法——同一条理由完全适用：顿号/句点后紧跟空白是列举标点，
    # 不是小数分隔符，真实数值必带单位。600584 实测：辩论环节模型用
    # 顿号列出论点优先级（"4、行业产能过剩……2、需求走弱……1、成本
    # 上升……5、政策收紧……3、估值高位"，按重要性排序而非数字顺序），
    # 连续两轮被判"以下数字是字面值，必须改写为占位符：4、2、1、5、3"
    # 「3（账本中无对应事实）」，整轮辩论因此被丢弃、正文未经证伪。
    # 前面不能是数字或小数点，避免把 "1.5" 的尾数 "5" 当成序号；
    # 顿号写法后面不能紧跟数字，避免把 "3、4季度" 这类相邻的两个真实
    # 数值当成序号列表吞掉——真正的列表项后面接的是文字说明，不会是
    # 另一个数字。
    r"|(?<![\d.])\d{1,2}、(?!\d)"
    r"|(?<![\d.])\d{1,2}\.\s+"
    # 证券代码 —— 不是需要溯源的财务数字。实测招行首稿因「600036」被驳回，
    # 白白多花一到两轮调用，且实质是在训练模型回避提及标的本身。
    r"|\b(?:sh|sz|bj|SH|SZ|BJ)?\d{6}\b"
    # 裸年份（1990–2099）。带后缀的已由上面的期间规则覆盖，这里管
    # 「2025 年度公司…」被拆开后残留的、以及「2021 至 2025 年间」这类区间。
    r"|(?<![\d.])(?:19[9]\d|20\d{2})(?![\d.%])"
)
_NUMERIC = re.compile(r"\d+(?:[.,]\d+)*\s*(?:%|亿|万|元|倍|x|个百分点)?")


def resolves(placeholder: str, ledger: FactLedger, as_of: date) -> bool:
    """该占位符此刻能否解析成真值。

    存在的理由: 递给模型一个解析不了的占位符，等于教它伪造引用 —— 而伪造
    引用比裸数字更隐蔽（格式完全合法，只是账本里没有那条事实）。实测踩到的
    是 [[pe_ttm@2025FY]]: PB / PE 是**即期**指标，挂在报价日上，不在会计期，
    而拼提示词的代码一律用了报告期。

    因此凡是要写进提示词的占位符，一律先过这道验证，而不是逐个字段打补丁。
    """
    m = REF_RE.fullmatch(placeholder.strip())
    if m is None:
        return False
    try:
        ledger.get(m.group(2), m.group(3), as_of=as_of)
    except Exception:                                        # noqa: BLE001
        return False
    return True


def audit_bare_numbers(text: str) -> list[str]:
    """返回正文中未经账本的裸数字。空列表 = 全部数字可溯源。"""
    stripped = REF_RE.sub(" ", text)
    stripped = _LABELS.sub(" ", stripped)
    return [m.group(0).strip() for m in _NUMERIC.finditer(stripped) if m.group(0).strip()]


def audit_prose(fields: dict[str, str]) -> list[str]:
    """核验模型自由撰写的描述字段里有没有账本外的数字。

    **这是第九次同类错误的守卫。** 前八次都是我往提示词里放了模型用不了的
    东西；这一次不同 —— 是模型写进结构化产出的**描述字段**，那些字段随后
    原样进了分析师提示词。跑长电时实测: 宏观定性的一条传导路径写着
    「当前处于91%历史估值分位的A股半导体板块……」，91% 在账本里没有任何
    对应事实。

    危害与前八次相同且更隐蔽: 分析师看到它、抄进正文、被裸数字审计驳回，
    而它**无从修复** —— 没有占位符可以替代它。于是整个重试循环变成不可
    满足的约束。

    拦在这里的好处是: 此刻仍在**产出该字段的那个模块**的重试循环里，
    模型改得动 —— 只要把那句话改成不带具体数字的说法即可。
    """
    bad = []
    for name, text in fields.items():
        for n in audit_bare_numbers(text or ""):
            bad.append(f"{name}「{n}」")
    return bad


# 渲染值末尾可能带的单位词。用于检测「占位符后又补了一遍单位」。
_UNIT_TAILS = ("分位", "亿元", "万元", "个百分点", "天", "家", "元", "倍", "x", "%")


def audit_unit_duplication(body: str, ledger: FactLedger,
                           as_of: date) -> list[str]:
    """占位符渲染后自带单位，其后又紧跟同一个单位词 —— 重复。

    **跑长电时实测，几乎每处分位数都中招。** format_value 渲染出「17 分位」
    「57.02 天」「4 家」，而中文的自然写法是「处于同业 X 分位」「周转 X 天」，
    模型于是在占位符后又写了一遍单位:

        处于同业17 分位分位　　同业中位数66.25 天天　　有效竞争者4 家家

    根因是规则里从没说过「占位符渲染后自带单位」。这类错误模型改得动，
    因此驳回是可满足的 —— 但事前在提示词里说清更省一轮。

    不做自动删除: 「[[receivable_days]]天数较长」删掉那个「天」会剩下
    「数较长」。判成违规让模型重写整句，比代码替人改字安全。
    """
    out: list[str] = []
    for m in REF_RE.finditer(body):
        try:
            fact = ledger.get(m.group(2), m.group(3), as_of=as_of)
        except Exception:                                    # noqa: BLE001
            continue
        shown = format_value(fact)
        tail = next((u for u in _UNIT_TAILS if shown.endswith(u)), None)
        if tail and body[m.end():m.end() + len(tail)] == tail:
            out.append(f"{m.group(0)} 之后紧跟「{tail}」")
    return out
