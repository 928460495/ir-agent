"""从行业同业中挑选**可比公司** —— 同业不等于可比。

行业成分可以有几十家（白酒 18 家，汽车零部件 94 家），但真实研报的可比表
只列五到八家。首钢那份首次覆盖报告列了 6 家。这不是排版取舍:

  · 可比公司的用途是**估值坐标**。规模差两个数量级的公司，PE/PB 不可比 ——
    龙头有流动性溢价和确定性溢价，小市值公司有壳价值和成长期权。
  · 每多一家可比公司要多跑一遍完整取数流程（财报 + 行情 + 交叉验证）。
    18 家要几分钟，而其中十几家对结论没有贡献。

挑选规则: **按市值排序后取标的的近邻**。既处理标的是龙头的情形（取次大的
几家），也处理标的居中的情形（两边各取几家）。取市值最大的 N 家是错的 ——
标的自己若是小市值公司，拿它和一堆龙头比分位数，结论只会是「垫底」，
而那反映的是规模差异，不是经营差异。

行情用腾讯接口批量取（一次调用拿全部同业市值），这一步很便宜；贵的是
之后对选中的少数几家做完整取数。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import requests

from ir_agent.market import quote_symbol
from ir_agent.sources.quotes import ParseError, parse_tencent_quote

URL = "http://qt.gtimg.cn/q={symbols}"
TIMEOUT = 15
BATCH = 40                       # 单次请求的代码数上限，超出分批

DEFAULT_N = 6                    # 可比公司家数，与卖方研报惯例一致


class NoComparables(Exception):
    """一家可比公司都挑不出来。"""


@dataclass(frozen=True)
class Sized:
    code: str
    name: str
    market_cap: Decimal


def fetch_caps(codes: list[str],
               session: requests.Session | None = None) -> tuple[list[Sized],
                                                                 list[tuple[str, str]]]:
    """批量取市值。返回 (成功, [(代码, 原因)])。

    单家解析失败不影响其余 —— 停牌、退市、代码变更都会让某一条为空，
    为此放弃整批是不必要的。
    """
    s = session or requests
    ok: list[Sized] = []
    bad: list[tuple[str, str]] = []

    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i + BATCH]
        try:
            resp = s.get(URL.format(symbols=",".join(
                quote_symbol(c) for c in chunk)), timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = "gbk"
            payload = resp.text
        except Exception as e:                          # noqa: BLE001
            bad.extend((c, f"行情批次失败（{e.__class__.__name__}）") for c in chunk)
            continue

        # 响应是 v_sh600519="…"; v_sz000858="…"; 的串联
        parts = [p for p in payload.split(";") if "=" in p]
        by_code: dict[str, str] = {}
        for p in parts:
            for c in chunk:
                if f"_{quote_symbol(c)}=" in p:
                    by_code[c] = p
                    break
        for c in chunk:
            raw = by_code.get(c)
            if raw is None:
                bad.append((c, "行情响应中无此代码"))
                continue
            try:
                q = parse_tencent_quote(raw)
            except ParseError as e:
                bad.append((c, f"行情解析失败: {e}"))
                continue
            cap = q.get("market_cap")
            if cap is None or cap <= 0:
                bad.append((c, "无总市值"))
                continue
            ok.append(Sized(c, q.get("name") or c, cap))
    return ok, bad


@dataclass
class Selection:
    target: str
    chosen: list[Sized]
    universe: int
    dropped: list[tuple[str, str]]
    target_cap: Decimal | None = None

    @property
    def codes(self) -> list[str]:
        return [s.code for s in self.chosen]

    def summary(self) -> str:
        yi = lambda v: f"{v / Decimal('1e8'):,.0f} 亿"      # noqa: E731
        lines = [f"可比公司 {len(self.chosen)} 家"
                 f"（自 {self.universe} 家同业中按市值就近选取）"]
        if self.target_cap is not None:
            lines.append(f"  标的市值 {yi(self.target_cap)}")
        for s in self.chosen:
            lines.append(f"  · {s.name}（{s.code}） {yi(s.market_cap)}")
        for code, why in self.dropped[:4]:
            lines.append(f"  – 未取市值 {code}: {why}")
        if len(self.dropped) > 4:
            lines.append(f"  – 另有 {len(self.dropped) - 4} 家未取到市值")
        return "\n".join(lines)


def select(peers, n: int = DEFAULT_N,
           session: requests.Session | None = None) -> Selection:
    """从 Peers 中挑出 n 家市值近邻。

    标的自身取不到市值时退化为「取市值最大的 n 家」并在摘要中说明 ——
    没有基准就没有「就近」，此时行业龙头是次优的坐标系。
    """
    codes = [peers.target] + peers.codes
    sized, bad = fetch_caps(codes, session=session)
    if not sized:
        raise NoComparables(f"{peers.target}: 同业市值全部取不到，无法挑选可比公司")

    ordered = sorted(sized, key=lambda s: s.market_cap, reverse=True)
    me = next((s for s in ordered if s.code == peers.target), None)
    others = [s for s in ordered if s.code != peers.target]

    if me is None:
        chosen = others[:n]
    else:
        idx = ordered.index(me)
        # 以标的为中心向两侧扩张，先近后远
        chosen = sorted(others,
                        key=lambda s: abs(ordered.index(s) - idx))[:n]
        chosen.sort(key=lambda s: s.market_cap, reverse=True)

    if not chosen:
        raise NoComparables(f"{peers.target}: 剔除后无可比公司")
    return Selection(target=peers.target, chosen=chosen,
                     universe=len(peers.members), dropped=bad,
                     target_cap=me.market_cap if me else None)
