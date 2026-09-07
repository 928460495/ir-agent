"""正文的出处核验 —— 三道闸之外的第四道。

**此前的不对称。** 战略、产业链、宏观定性三个模块内部有完整的依据闭环:
每条判断都带 Basis，引文必须逐字出自年报摘录或外部语料库，核验不过就
驳回重写。但它们的结论以**摘要**形式进入撰写提示词之后，分析师写正文时
的定性表述就不再受任何约束了 —— 数字有三道闸（裸数字、伪造占位符、
至少一个占位符），文字一道都没有。

于是「据某券商研报，行业规模已达数千亿」这种句子可以畅通无阻: 格式合法、
不含裸数字、占位符齐全。**捏造一个出处比捏造一个数字容易得多，也更难
被发现** —— 读者不会去查每一句转述。

**这一闸只核验「已经作出的归属」，不强求处处标注出处。** 真实研报不会
每句话都挂脚注，硬性要求会让正文没法读，而且会造出模型无从修复的驳回项
—— 那正是此前把重试循环拖成不可满足约束的原因。因此触发条件很窄:

  · 引文前面出现归属标记（据、根据、援引、披露、指出、表示……）
  · 且两者之间没有断句

满足这两条的引文，必须逐字出现在本次实际取回的资料里。日常用于强调的
「护城河」「飞天」不会跟在「据」后面，不受影响。

**驳回信息必须可操作。** 每条都附上本次实际有哪些来源可引 —— 清单里
只要有一项模型无从修复，整个重试循环就变成不可满足的约束，连本来能改的
那几条也一起卡死。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ir_agent.sources.external import norm

# 归属标记。出现在引文之前即表示「下面这句话是别人说的」。
MARKERS = ("据", "根据", "援引", "引用", "摘自", "出自", "披露", "指出",
           "表示", "称", "写道", "显示", "提到", "报道")

_MARKER_RE = re.compile("|".join(MARKERS))
# 引文跨度: 中文书名/引号与英文引号。非贪婪，不跨段。
_QUOTE_RE = re.compile(r"[「“\"']([^」”\"'\n]{2,200})[」”\"']")
# 断句符。归属标记与引文之间隔了句号，就不是同一句话里的归属。
_BREAK_RE = re.compile(r"[。；！？\n]")

# 「X证券」这类机构名，出现在同一句的研究类词汇附近才检查 —— 否则会把
# 「中信证券」作为**可比公司**分析的那种正当用法一并误伤。
_ORG_RE = re.compile(r"[一-龥A-Za-z]{2,8}(?:证券|期货|研究所|投行)")
_RESEARCH_RE = re.compile(r"研报|研究报告|研究所|分析师|覆盖|评级|盈利预测|观点")


@dataclass
class Evidence:
    """本次实际取回的、可供引述的东西。**只有这里面的才算数。**"""

    annual: str = ""
    corpus: object = None
    peers: tuple[str, ...] = field(default=())

    def find(self, quote: str) -> str | None:
        """引文出自哪里；找不到返回 None。"""
        q = norm(quote)
        if not q:
            return None
        if self.annual and q in norm(self.annual):
            return "年报原文"
        if self.corpus is not None:
            d = self.corpus.find(quote)
            if d is not None:
                return d.cite()
        return None

    def publishers(self) -> list[str]:
        if self.corpus is None:
            return []
        return sorted({d.publisher for d in self.corpus.docs})

    def catalogue(self) -> str:
        """本次可引的来源清单。附在驳回信息里，让重试是可满足的。"""
        parts = []
        if self.annual:
            parts.append("公司年报原文")
        parts += self.publishers()
        return "、".join(parts) if parts else "（本次未取到任何可引述的资料）"

    def is_empty(self) -> bool:
        return not self.annual and not self.publishers()


def attributed_quotes(body: str) -> list[str]:
    """正文里**被归属给某个来源**的引文。

    判据是引文之前、同一句之内出现归属标记。用于强调的「护城河」不会
    跟在「据」后面，因此不受影响。
    """
    out = []
    for m in _QUOTE_RE.finditer(body):
        head = body[:m.start()]
        cut = _BREAK_RE.split(head)[-1]          # 只看同一句
        if _MARKER_RE.search(cut):
            out.append(m.group(1))
    return out


def named_sources(body: str) -> list[str]:
    """正文里点名的研究机构。只取与研究类词汇同句出现的 ——
    把「中信证券」作为可比公司分析是正当用法，不该误伤。"""
    out = []
    for sentence in _BREAK_RE.split(body):
        if not _RESEARCH_RE.search(sentence):
            continue
        out += _ORG_RE.findall(sentence)
    return sorted(set(out))


def audit(body: str, evidence: Evidence) -> list[str]:
    """正文的出处核验，返回问题列表。空列表 = 通过。"""
    out: list[str] = []

    quotes = attributed_quotes(body)
    if quotes and evidence.is_empty():
        return [f"正文引述了外部来源（如「{quotes[0][:20]}」），"
                "但本次未取到任何可引述的资料 —— 无从核验的转述与捏造无异，"
                "请改为不依赖外部来源的表述。"]

    for q in quotes:
        if evidence.find(q) is None:
            out.append(
                f"引文「{q[:30]}」在本次取回的资料中找不到 —— "
                f"归属给一个查不到的出处与捏造无异。"
                f"可引述的来源：{evidence.catalogue()}")

    known = set(evidence.publishers())
    for org in named_sources(body):
        if org in known or org in evidence.peers:
            continue
        out.append(
            f"正文点名了「{org}」的研究观点，但本次并未取回该机构的报告 —— "
            f"实际取到的是：{evidence.catalogue()}")
    return out
