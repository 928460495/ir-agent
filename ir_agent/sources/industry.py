"""行业归属与同业集合 —— 同业从分类索引反查，不再靠手工列名单。

为什么不用东财**概念板块**: 实测「白酒」概念板块（BK 896）有 45 家成分，
里面混着巨力索具、广誉远这类沾边即入的公司。概念板块是主题标签，不是行业
分类，拿它当同业集合会把行业增速和集中度全部算歪。

改用**申万行业分类**（字段 BOARD_NAME_LEVEL，形如「食品饮料-白酒Ⅱ-白酒Ⅲ」）
—— 卖方研报实际使用的口径。同一口径下白酒是 18 家同业，干净。

分类与成分都走 taxonomy 的本地索引: 接口不支持 like 查询，无法按分类前缀
检索，而按叶子级精确匹配又常常只剩两三家。索引在内存里就能向上回退一级。

B 股与北交所在索引构建时已被 market_of 白名单滤掉（古井贡酒 000596 与
古井贡B 200596 是同一家公司，并入会重复计权）。本模块只负责剔除退市公司，
并把剔除项如实回报 —— 同业集合少了谁会直接改变中位数，读者有权知道。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ir_agent.sources.taxonomy import (
    MIN_PEERS,
    Taxonomy,
    TaxonomyUnavailable,
)

SOURCE_ID = "em_industry"

_LEVEL_NAME = {1: "一级", 2: "二级", 3: "三级", 4: "四级"}


class IndustryUnavailable(Exception):
    """拿不到行业分类或成分。调用方降级为「无行业分析」而非崩溃。"""


@dataclass(frozen=True)
class Classification:
    code: str
    name: str
    path: str                   # 标的自身的完整申万路径
    used_path: str              # 实际采用的分类路径（可能已向上回退）
    level: int                  # 采用的级数

    @property
    def levels(self) -> list[str]:
        return [p for p in self.path.split("-") if p]

    @property
    def widened(self) -> bool:
        return self.level < len(self.levels)

    def describe(self) -> str:
        s = f"申万{_LEVEL_NAME.get(self.level, self.level)}「{self.used_path}」"
        if self.widened:
            s += f"（标的实为 {self.path}，因该级同业过少已上移一级）"
        return s


@dataclass(frozen=True)
class Member:
    code: str
    name: str


@dataclass
class Peers:
    target: str
    target_name: str
    classification: Classification
    members: list[Member] = field(default_factory=list)
    excluded: list[tuple[str, str, str]] = field(default_factory=list)
    thin: bool = False

    @property
    def codes(self) -> list[str]:
        return [m.code for m in self.members]

    def summary(self) -> str:
        lines = [f"行业 {self.classification.describe()}",
                 f"同业 {len(self.members)} 家（不含标的自身）"]
        if self.thin:
            lines.append(
                f"  ⚠ 同业不足 {MIN_PEERS} 家 —— 分位数不具统计意义，只列个体对比。"
                "不再向上取一级: 一级分类会混入业务实质不同的公司"
                "（如保险与券商同属非银金融）。")
        for code, name, why in self.excluded:
            lines.append(f"  – 剔除 {name}（{code}）: {why}")
        return "\n".join(lines)


def peer_set(code: str, taxonomy: Taxonomy | None = None,
             cache: Path | str | None = None,
             min_peers: int = MIN_PEERS) -> Peers:
    """标的的行业归属与同业集合。

    ST 公司保留 —— 它们仍在上市交易，是真实的行业参与者，剔除会让行业指标
    系统性偏乐观。退市公司剔除 —— 已无有效交易与持续披露。
    """
    try:
        tax = taxonomy or (Taxonomy.load(cache) if cache else Taxonomy.load())
        me = tax.entry(code)
        if me is None:
            raise IndustryUnavailable(f"{code}: 申万分类表中查无此代码")
        raw, level, used, thin = tax.widen(code, min_peers=min_peers)
    except TaxonomyUnavailable as e:
        raise IndustryUnavailable(str(e)) from e

    cls = Classification(code=code, name=me.name, path=me.path,
                         used_path=used, level=level)
    out = Peers(target=code, target_name=me.name, classification=cls, thin=thin)
    for e in raw:
        if "退市" in e.name:
            out.excluded.append((e.code, e.name, "已退市"))
            continue
        out.members.append(Member(e.code, e.name))

    if not out.members:
        raise IndustryUnavailable(f"{code}: 行业「{used}」下没有可用同业")
    # 剔除退市后可能重新跌破下限 —— 重算而非沿用 widen 的判断
    out.thin = len(out.members) < min_peers
    return out
