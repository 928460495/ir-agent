"""按分解式识别数据缺口，并分级采集。

采集顺序先近后远、先可核验后不可核验:

    ① 账本          已有事实
    ② 年报叙述章节   产能/产量的文字披露
    ③ 研报语料库    深度报告里的行业产能测算
    ④ 开放网络      SEMI 出货、基板报价、协会统计

**采集不到就说采集不到。** 封测稼动率 A 股基本不披露 —— 这是常态而非异常。
此时该因子标「无数据，取值为估计」，**绝不用代理指标偷偷顶替**（比如拿固定
资产周转率冒充稼动率）: 那会让一个估计伪装成测量，而读者分辨不出来。

②③是文本检索，命中因子的 `label`（如「稼动率」）**不等于**拿到了它的数值——
年报里出现这三个字，可能只是一句「稼动率保持稳定」而没有任何数字。因此命中
时只标「文字依据，待人工确认」，并把原文片段连同出处一并交出去，不代人下
结论：不然就是本模块要防的那种失败本身——让一个估计伪装成测量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ir_agent.drivers import Decomposition, Factor
from ir_agent.ledger import FactLedger, LookAheadError
from ir_agent.sources.external import Document, Tier
from ir_agent.sources.web import (Finding, WebUnavailable, search,
                                  supports_value)

DISCLOSED = "已披露"
ANNUAL = "年报披露"     # ②年报叙述章节命中——文字依据，未必给出数值
REPORT = "研报测算"     # ③研报语料库命中——同上
EXTERNAL = "外部来源"
MISSING = "无数据"

# 命中关键词后，往前后各摘这么多字给人看。摘的是原文，不是结论——
# 让读者自己判断这段话到底有没有给出数字，比我们代为下结论更诚实。
_EXCERPT_WINDOW = 40

# I3：真实联网跑出来的因子标签形如「有效产能变化率」「稼动率贡献」
# 「先进封装收入占比提升贡献」「原材料及折旧成本变化贡献」——这些
# 后缀是分解式的运算修饰（乘法链统一叫"变化率"，加法链统一叫"贡献"/
# "占比提升贡献"），根子是标签里带着单位/运算后缀，不是同义词问题：
# 年报只会写「有效产能」「稼动率」，不会逐字写「有效产能变化率」。
# 顺序按长度降序——「先进封装收入占比提升贡献」要整体剥掉「占比提升
# 贡献」，不能先被短的「贡献」剥成半吊子的「先进封装收入占比提升」。
_SUFFIXES = ("占比提升贡献", "变化贡献", "占比提升", "变化率", "贡献", "增速")


def _core_label(label: str) -> str:
    """剥掉因子标签里的单位/运算后缀，取核心词做文本匹配。

    方向仍要保守：**宁可漏判落到④，不可误判成"已披露"**。剥完后核心词
    过短就不剥，用原始标签去匹配——大概率落空退到④，但不会拿一两个字
    这种几乎到处都能命中的片段冒充"命中"，把一个巧合伪装成证据（这正
    是本模块要防的最严重失败模式）。

    I2（终审复核）：阈值原为 2，「稼动率」（3字）「汇率」（2字）这类
    短核心词都会被剥出来直接做包含匹配——年报原文一句「公司本年度
    **未**披露稼动率数据」照样会命中"稼动率"三个字，把"未披露"读反
    成"年报披露"（把一句否定读成了肯定，正是本模块最怕的那种失败）。
    阈值提到 4 字后，这类短词回退用原始标签（如"稼动率变化率"）去匹
    配——真实年报几乎不会逐字出现这种带单位后缀的整串，宁可因此在少
    数真命中的场景里多退到③④，也不要在"未披露"这种否定语境里被三个
    字的片段撞上。
    """
    for suf in _SUFFIXES:
        if label.endswith(suf) and len(label) > len(suf):
            core = label[: -len(suf)]
            if len(core) >= 4:
                return core
    return label


@dataclass
class FactorData:
    factor: Factor
    status: str = MISSING
    findings: list[Finding] = field(default_factory=list)
    note: str = ""


def identify(d: Decomposition, ledger: FactLedger, as_of: date) -> list[Factor]:
    """分解式里账本还没有的因子。

    账本里存在该 key 但值的披露时点晚于 as_of（LookAheadError）也按「没有」
    处理 —— 时点守卫是全系统最关键的防线，一个 as_of 之后才披露的数字，
    在这个时点上就是不存在的，不能因为账本里"有"就当作已披露。
    """
    out = []
    for f in d.factors:
        try:
            ledger.get(f.key, _latest(ledger, f.key), as_of=as_of)
        except (KeyError, LookAheadError):
            out.append(f)
    return out


def _latest(ledger: FactLedger, key: str) -> str:
    pers = sorted(p for k, p in ledger._facts if k == key)   # noqa: SLF001
    return pers[-1] if pers else ""


def _excerpt(text: str, label: str) -> str:
    """摘出命中处前后的原文——只给片段，不代人下结论。"""
    i = text.find(label)
    if i < 0:
        return ""
    start = max(0, i - _EXCERPT_WINDOW)
    end = min(len(text), i + len(label) + _EXCERPT_WINDOW)
    body = text[start:end].replace("\n", "")
    return ("…" if start > 0 else "") + body + ("…" if end < len(text) else "")


def _match_annual(sections, label: str) -> tuple[str, str] | None:
    """②年报叙述章节里是否提到该因子。

    只做包含判断，不抽数字——命中「稼动率」三个字不等于拿到了稼动率的
    数值，抽错一个数比没抽更糟。返回值交给调用方判断，不在这里下结论。

    **匹配用剥掉后缀的核心词，不是原始标签。** 原始标签「有效产能变化
    率」这种带单位/运算后缀的整串不会逐字出现在年报里，见 `_core_label`。
    """
    if sections is None:
        return None
    core = _core_label(label)
    for s in sections.sections.values():
        if core in s.text:
            return _excerpt(s.text, core), f"《{s.label}》"
    return None


def _match_report(corpus, label: str) -> tuple[str, str] | None:
    """③研报语料库里是否提到该因子，判据与 _match_annual 同理。"""
    if corpus is None:
        return None
    core = _core_label(label)
    for d in corpus.docs:
        if core in d.text:
            return _excerpt(d.text, core), d.cite()
    return None


def collect(client, decomps: list[Decomposition], ledger: FactLedger,
            corpus, industry: str, as_of: date,
            sections=None) -> dict[str, FactorData]:
    """逐因子采集。已在账本的跳过；缺的依次尝试②年报、③研报、④外部网络。

    ②或③命中就不再往下走——先近后远，同时省一次联网调用。`sections`/
    `corpus` 都可能是 None（调用方没取到年报或语料库），两级匹配函数
    各自容错，None 时直接落空、继续试下一级。
    """
    out: dict[str, FactorData] = {}
    for d in decomps:
        gaps = {f.key for f in identify(d, ledger, as_of)}
        for f in d.factors:
            if f.key in out:
                continue
            if f.key not in gaps:
                out[f.key] = FactorData(factor=f, status=DISCLOSED)
                continue

            hit = _match_annual(sections, f.label)
            if hit is not None:
                excerpt, src = hit
                out[f.key] = FactorData(
                    factor=f, status=ANNUAL,
                    note=f"「{excerpt}」——{src}"
                         "（文字依据，是否给出具体数值待人工确认）")
                continue

            hit = _match_report(corpus, f.label)
            if hit is not None:
                excerpt, src = hit
                out[f.key] = FactorData(
                    factor=f, status=REPORT,
                    note=f"「{excerpt}」——{src}"
                         "（文字依据，是否给出具体数值待人工确认）")
                continue

            try:
                found = search(client, f.label, f.key, industry, as_of)
            except WebUnavailable as e:
                # 能力边界不是故障，整轮研究不该因此停摆。措辞里显式写
                # 「不能联网」而不是只转述异常原文 —— WebUnavailable 的
                # 实际文案说的是"开放网络采集当前被禁用"，读者要能一眼
                # 看出这是联网能力缺失，而不用去解读措辞。
                out[f.key] = FactorData(
                    factor=f, status=MISSING,
                    note=f"不能联网 —— {e} —— 取值为估计，无数据支撑")
                continue

            ok, why = supports_value(found)
            if ok:
                for x in found:
                    corpus.add_if_visible(Document(
                        doc_id=f"web_{f.key}_{len(corpus.docs)}", url=x.url,
                        title=f"{f.label}：{x.claim[:30]}",
                        publisher=x.publisher, tier=x.tier,
                        # Finding.published 永远是真实日期（日期不明的发现
                        # 在 web.parse_findings 里已被丢弃），这里直接用，
                        # 不写 `x.published or as_of` 这类兜底 —— as_of 恰好
                        # 是唯一必然不触发时点守卫的取值，一旦用它顶替，就
                        # 等于把"日期不明"洗成"时点上最合规"，是在编排层
                        # 重新打开 Task 5 刚堵掉的那个坑。
                        published=x.published, retrieved=as_of,
                        text=x.claim))
                out[f.key] = FactorData(factor=f, status=EXTERNAL,
                                        findings=found)
            else:
                # 「找到了但不够格」与「什么都没找到」要分开说：前者对
                # 读者的含义是"证据链已经起步，只是等级不够"，后者是
                # "连线索都没有"。找到的发现若恰好只有一条 MEDIA 来源，
                # 如实说明"单一媒体"；若已经是多条却仍全为 MEDIA，
                # 沿用 supports_value 的措辞——不能写"单一"，那会误导
                # 读者以为多凑几家媒体互相印证就能过关，而这正是本模块
                # 要挡住的事（门槛是来源等级，不是数量）。
                if (len(found) == 1
                        and found[0].tier is Tier.MEDIA):
                    note = (f"仅单一媒体来源（{found[0].publisher}）—— "
                            "不足以支撑因子取值，取值为估计")
                else:
                    note = f"{why} —— 取值为估计"
                out[f.key] = FactorData(
                    factor=f, status=MISSING, findings=found, note=note)
    return out


def summary(data: dict[str, FactorData]) -> str:
    """给人看的采集结果。**遗漏必须可见** —— 这正是分解的价值所在。"""
    lines = ["因子数据采集结果", ""]
    lines.append("| 因子 | 状态 | 来源 / 说明 |")
    lines.append("| --- | --- | --- |")
    for fd in data.values():
        if fd.status == EXTERNAL:
            src = "；".join(f"{x.publisher}（{x.url}）" for x in fd.findings[:2])
        else:
            src = fd.note or "—"
        lines.append(f"| {fd.factor.label} | {fd.status} | {src} |")

    # ②③是文字命中，不是已核实的数值——必须与「已披露」「外部来源」分开
    # 摆出来，不能让状态列里挨在一起的几行字看起来证明力一样。
    textual = [fd.factor.label for fd in data.values()
               if fd.status in (ANNUAL, REPORT)]
    if textual:
        lines += ["",
                  f"📄 以下因子仅命中年报叙述或研报文字（年报披露/研报测算），"
                  f"是文字依据，待人工确认原文是否给出具体数值——"
                  f"命中关键词不等于拿到了数值：{'、'.join(textual)}。"]

    gaps = [fd.factor.label for fd in data.values() if fd.status == MISSING]
    if gaps:
        lines += ["", f"⚠ 以下因子无数据支撑，取值为估计：{'、'.join(gaps)}。",
                  "**绝不用代理指标顶替** —— 拿固定资产周转率冒充稼动率会让"
                  "一个估计伪装成测量，而读者分辨不出来。"]
    return "\n".join(lines)
