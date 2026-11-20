"""费用率分项与稳态值判断项（spec 2026-11-16-dcf-opex-horizon-design）。

PLTR 2026-11-16：费用率取近 3 年均值、不经判断 —— 销售及管理费用率五年间 79.5% → 38.3%，模型却按 49.0%
固定五年，FY2025 实际营业利润率 31.6%，模型 2026 年退回 19%。对处在经营杠杆释放期的公司，费用率恰是最要紧
的判断项。这里把它与稳态值（第 6–10 年过渡期收敛的目标、终值 ROIC 溢价）交给第③步：AI 填、审核者审、人签。

写法（与营收增速、毛利率同一行格式，解析器复用 `assume` 的读数件）：
    假设: 研发费用率 | 第N年 | 悲观 | 中性 | 乐观 | 理由与依据      （第 1–5 年）
    假设: 稳态研发费用率 | 稳态 | 悲观 | 中性 | 乐观 | 理由与依据   （一行）

`Row.driver` 记 `opex:<科目>` / `steady:<科目>`；稳态行 `year = 0`。与因子行分开存放，互不干扰。
"""

from __future__ import annotations

OPEX_ITEMS: dict[str, tuple[str, ...]] = {
    "us": ("研发费用率", "销售及管理费用率"),
    "cn": ("销售费用率", "管理费用率", "研发费用率"),
}
JUDGED_YEARS = 5
ROIC_PREMIUM = "终值ROIC溢价"
STEADY_GM = "稳态毛利率"


def steady_items(market: str) -> tuple[str, ...]:
    return (STEADY_GM, *("稳态" + x for x in OPEX_ITEMS[market]), ROIC_PREMIUM)


def _is_cost(item: str) -> bool:
    """成本项：悲观 ≥ 中性 ≥ 乐观。毛利率与 ROIC 溢价是收益项，方向相反。"""
    return item.endswith("费用率")


def _name(cell: str, allowed: tuple[str, ...]) -> str | None:
    """按最长的合法名匹配开头（「稳态研发费用率」不能被「研发费用率」先认走）。"""
    txt = cell.strip().lstrip("*＊ ").replace(" ", "")
    return next((x for x in sorted(allowed, key=len, reverse=True) if txt.startswith(x)), None)


def split_lines(text: str, market: str) -> tuple[str, str]:
    """把费用率与稳态行从判断项原文里分出来：(其余原文, 本模块的行)。"""
    from ir_agent.assume import _LINE

    allowed = OPEX_ITEMS[market] + steady_items(market)
    mine: list[str] = []
    spans = []
    for m in _LINE.finditer(text):
        if _name(m.group(1).split("|", 1)[0], allowed):
            mine.append(m.group(0))
            spans.append(m.span())
    rest, last = [], 0
    for a, b in spans:
        rest.append(text[last:a])
        last = b
    rest.append(text[last:])
    return "".join(rest), "\n".join(mine)


def parse(text: str, market: str, years: int = JUDGED_YEARS):
    """→ (opex_rows, steady_rows, read_notes)。只读不驳回，读不出的记进 notes（与 assume 一致）。"""
    from ir_agent.assume import (_LINE, Row, _basis, _cells, _note, _three,
                                 _unit_notes, _year)

    opex: dict[tuple[str, int], Row] = {}
    steady: dict[str, Row] = {}
    notes: list[str] = []
    allowed = OPEX_ITEMS[market] + steady_items(market)
    for m in _LINE.finditer(text):
        parts = _cells(m.group(1))
        if len(parts) < 6:
            notes.append(f"一行字段不足 6 段（项目|年|悲观|中性|乐观|依据），没读进来：{m.group(1)}")
            continue
        name = _name(parts[0], allowed)
        if name is None:
            continue
        is_steady = name in steady_items(market)
        if is_steady:
            year = 0
            where = name
        else:
            year = _year(parts[1], years, f"{name} ", notes)
            if year is None:
                continue
            where = f"{name} 第 {year} 年"
        vals = _three(parts[2:5], where, notes)
        if vals is None:
            continue
        notes += _unit_notes("%", where, parts[2:5])
        bases, prob = _basis(parts[5])
        if prob:
            notes.append(f"{where}：{prob}")
        row = Row(driver=("steady:" if is_steady else "opex:") + name, year=year,
                  bear=vals[0], base=vals[1], bull=vals[2], bases=bases,
                  note=_note(parts[5]), raw=tuple(parts[2:5]))
        key = (name, year)
        if (key in opex) or (is_steady and name in steady):
            notes.append(f"{where}出现了不止一行，按最后一行读")
        if is_steady:
            steady[name] = row
        else:
            opex[key] = row
    opex_rows = [opex[k] for k in sorted(opex, key=lambda k: (OPEX_ITEMS[market].index(k[0]), k[1]))]
    steady_rows = [steady[k] for k in steady_items(market) if k in steady]
    return opex_rows, steady_rows, notes


def check(a) -> tuple[list[str], list[str]]:
    """(算不下去, 交审核者的问题)。缺科目、缺年份、缺稳态项 → 算不下去；三档方向反了 → 问题。"""
    market = getattr(a, "market", "") or ""
    if market not in OPEX_ITEMS:
        return [], []
    blocking: list[str] = []
    problems: list[str] = []
    if not a.opex_rows and not a.steady_rows:
        # 整块没填（旧文件、实跑回放、或 AI 完全漏掉）：不在这里拦 —— 交审核者，第④步读到没有费用率的
        # 判断项文件时明确停下（见 full 的建模入口）。只填了一部分才是「算不下去」。
        return [], [f"没有填费用率（{'、'.join(OPEX_ITEMS[market])}）与稳态值 —— 第④步会停下，"
                    "需要补填（不会按近 3 年均值顶替）"]
    have = {(r.driver.split(":", 1)[1], r.year) for r in a.opex_rows}
    for item in OPEX_ITEMS[market]:
        miss = [y for y in range(1, JUDGED_YEARS + 1) if (item, y) not in have]
        if miss:
            blocking.append(f"{item} 缺第 {miss} 年")
    got = {r.driver.split(":", 1)[1] for r in a.steady_rows}
    for item in steady_items(market):
        if item not in got:
            blocking.append(f"缺 {item}（稳态值，第 6–10 年过渡期收敛的目标）")
    for r in list(a.opex_rows) + list(a.steady_rows):
        item = r.driver.split(":", 1)[1]
        where = item if r.year == 0 else f"{item} 第 {r.year} 年"
        if _is_cost(item):
            if not (r.bear >= r.base >= r.bull):
                problems.append(f"{where}三档方向不对（成本项应 悲观 ≥ 中性 ≥ 乐观：{r.bear} / {r.base} / {r.bull}）")
        elif not (r.bear <= r.base <= r.bull):
            problems.append(f"{where}三档方向不对（应 悲观 ≤ 中性 ≤ 乐观：{r.bear} / {r.base} / {r.bull}）")
        if item == ROIC_PREMIUM and min(r.bear, r.base, r.bull) < 0:
            # 负溢价 = 终值 ROIC 低于终值 WACC，增长毁灭价值；A 股工作簿没有降级保护，终值会失真
            problems.append(f"{ROIC_PREMIUM}不应为负（{r.bear} / {r.base} / {r.bull}）：成熟后 ROIC 低于 WACC "
                            "时增长不创造价值，应给 0 或正数")
    return blocking, problems


def judg_block(a) -> dict:
    """写进 `cal["judg"]` 的两块：opex_scen（科目 → 三档逐年）、steady（科目 → 三档）。键为中文科目名，
    与工作簿行标签一致。没有判断项时返回空 dict（建表按校准值三档同值）。"""
    out: dict = {}
    if a.opex_rows:
        scen: dict = {}
        for r in sorted(a.opex_rows, key=lambda r: r.year):
            item = r.driver.split(":", 1)[1]
            d = scen.setdefault(item, {"bull": [], "base": [], "bear": []})
            for c in ("bull", "base", "bear"):
                d[c].append(float(getattr(r, c)))
        out["opex_scen"] = scen
    if a.steady_rows:
        out["steady"] = {r.driver.split(":", 1)[1]: {c: float(getattr(r, c)) for c in ("bull", "base", "bear")}
                         for r in a.steady_rows}
    return out


def row_label(r) -> str:
    return r.driver.split(":", 1)[1]


def row_to_dict(r) -> dict:
    return {"driver": r.driver, "year": r.year, "bear": str(r.bear), "base": str(r.base),
            "bull": str(r.bull), "note": r.note, "bases": [b.to_dict() for b in r.bases],
            "proposed": {"bear": str(r.bear), "base": str(r.base), "bull": str(r.bull)}}


def row_from_dict(x: dict, edits: list[str]):
    from decimal import Decimal

    from ir_agent.assume import Row
    from ir_agent.basis import Basis

    r = Row(driver=x["driver"], year=int(x["year"]), bear=Decimal(x["bear"]), base=Decimal(x["base"]),
            bull=Decimal(x["bull"]), note=x.get("note", ""),
            bases=[Basis.from_dict(b) for b in (x.get("bases") or [])])
    p = x.get("proposed") or {}
    for case in ("bear", "base", "bull"):
        if case in p and Decimal(p[case]) != getattr(r, case):
            when = "稳态" if r.year == 0 else f"第{r.year}年"
            edits.append(f"{row_label(r)} {when} {case}：模型给 {p[case]}，核实后改为 {getattr(r, case)}")
    return r



# ── 第③步：提示词段落与审核事实 ───────────────────────────────────


def _pct(x) -> str:
    return f"{float(x) * 100:.1f}%"


def history_lines(market: str, judg: dict) -> list[str]:
    """各科目逐年历史费用率、最新一年、近 3 年均值（旧的默认口径，作对照）、同业中位数。"""
    hist = (judg or {}).get("opex_hist") or {}
    years = hist.get("years") or []
    peers = (judg or {}).get("opex_peer_median") or {}
    out = []
    for item in OPEX_ITEMS[market]:
        xs = [x for x in (hist.get(item) or []) if x is not None]
        if not xs:
            out.append(f"  - {item}：历史：无" + (f"；同业中位数 {_pct(peers[item])}" if item in peers else ""))
            continue
        series = "、".join(f"{y} {_pct(x)}" for y, x in zip(years[-len(xs):], xs))
        tail = xs[-3:]
        out.append(f"  - {item}：{series}；最新 {_pct(xs[-1])}；近3年均值 {_pct(sum(tail) / len(tail))}"
                   + (f"；同业中位数 {_pct(peers[item])}" if item in peers else ""))
    return out


def prompt_section(market: str, judg: dict) -> str:
    items = OPEX_ITEMS[market]
    steady = steady_items(market)
    return "\n".join([
        "**费用率与稳态值（也由你判断）**",
        "",
        f"费用率分项（{'、'.join(items)}）第 1–{JUDGED_YEARS} 年逐年三档。下面是逐年历史，"
        "**看趋势，不要简单取均值**：近 3 年均值在费用率单向变化的公司身上会抹掉经营杠杆"
        "（或掩盖费用率回升）。",
        *history_lines(market, judg),
        "",
        f"稳态值（各一行，年份写「稳态」）：{'、'.join(steady)}。第 6–10 年是过渡期，代码按规则把"
        "增速线性回落到永续增长率、把毛利率与各费用率线性收敛到你给的稳态值 —— **你不用填第 6–10 年**。"
        "稳态值是公司成熟后的长期水平，参照同业成熟公司；终值ROIC溢价是成熟后投入资本回报率超出 WACC"
        "的部分（护城河越宽越高，参照 3%）。",
        "",
        "**三档方向**：费用率是成本项，**悲观 ≥ 中性 ≥ 乐观**（悲观费用率更高）；稳态毛利率与终值ROIC溢价"
        "是收益项，悲观 ≤ 中性 ≤ 乐观。单位一律带 %（如 38.3%）。",
        "",
        "`假设: <费用率科目> | 第N年 | <悲观> | <中性> | <乐观> | 理由与依据`",
        "`假设: <稳态项> | 稳态 | <悲观> | <中性> | <乐观> | 理由与依据`",
    ])


def margin_lines(a, gm_by_case: dict, judg: dict | None = None) -> list[str]:
    """【营业利润率】三档逐年 = 毛利率 − Σ费用率（不含代码校准的其他经营损益）；稳态同式。

    `gm_by_case`：{"bear"|"base"|"bull": [第1..5年毛利率]}。"""
    if not a.opex_rows:
        return []
    by = {}
    for r in a.opex_rows:
        by.setdefault(r.year, []).append(r)
    # A 股的营业利润扣税金及附加（会计准则口径；茅台约占营收 16%）—— 建模一直扣，展示此前漏了（2026-11-20）
    tsur = float((judg or {}).get("tsur") or 0) if a.market == "cn" else 0.0
    if a.market == "cn" and tsur:
        head = f"（毛利率 − 税金及附加率 {tsur:.2%} − 各费用率，悲观 / 中性 / 乐观；不含其他经营损益）"
    elif a.market == "cn":
        head = "（毛利率 − 各费用率，悲观 / 中性 / 乐观；未扣税金及附加，建模另扣；不含其他经营损益）"
    else:
        head = "（毛利率 − 各费用率，悲观 / 中性 / 乐观；不含其他经营损益）"
    out = ["", "【营业利润率】" + head]
    for y in sorted(by):
        vals = []
        for c in ("bear", "base", "bull"):
            gm = gm_by_case.get(c) or []
            if len(gm) < y:
                break
            vals.append(float(gm[y - 1]) - tsur - sum(float(getattr(r, c)) for r in by[y]))
        if len(vals) == 3:
            out.append(f"- 第{y}年 营业利润率 " + " / ".join(f"{v:.2%}" for v in vals))
    st = {r.driver.split(":", 1)[1]: r for r in a.steady_rows}
    if STEADY_GM in st and all(("稳态" + x) in st for x in OPEX_ITEMS.get(a.market, ())):
        vals = [float(getattr(st[STEADY_GM], c)) - tsur
                - sum(float(getattr(st["稳态" + x], c)) for x in OPEX_ITEMS[a.market])
                for c in ("bear", "base", "bull")]
        out.append("- 稳态 营业利润率 " + " / ".join(f"{v:.2%}" for v in vals))
    hist = (judg or {}).get("opex_hist") or {}
    gmh = (judg or {}).get("gm_hist") or []
    if gmh and all(hist.get(x) for x in OPEX_ITEMS.get(a.market, ())):
        last = float(gmh[-1]) - tsur - sum(float(hist[x][-1]) for x in OPEX_ITEMS[a.market])
        yrs = hist.get("years") or ["最新"]
        out.append(f"- {yrs[-1]} 营业利润率参照（历史实际，同口径）{last:.2%}")
    return out


def describe_lines(a) -> list[str]:
    """核实清单（assumptions.md）里的费用率与稳态值 —— 人签门③ 时必须看得到（终审 C1，2026-11-17）。"""
    if not a.opex_rows and not a.steady_rows:
        return []
    from ir_agent.assume import Case

    def ref(r):
        refs = "；".join(b.describe() for b in r.bases)
        return refs or "⚠ 没有可核验的依据"
    lines = ["## 费用率（三档；成本项 悲观 ≥ 中性 ≥ 乐观）", ""]
    for item in OPEX_ITEMS.get(a.market, ()):
        got = sorted((r for r in a.opex_rows if row_label(r) == item), key=lambda r: r.year)
        if not got:
            continue
        lines += [f"### {item}", "", "| 年 | 悲观 | 中性 | 乐观 | 理由 | 依据 |", "| --- | --- | --- | --- | --- | --- |"]
        for r in got:
            lab = a.labels[r.year - 1] if r.year <= len(a.labels) else f"第{r.year}年"
            lines.append(f"| {lab} | {r.bear:.2%} | {r.base:.2%} | {r.bull:.2%} | {r.note or '—'} | {ref(r)} |")
        lines.append("")
    if a.steady_rows:
        lines += ["## 稳态值（第 6–10 年过渡期线性收敛的目标；终值 ROIC = 终值 WACC + 溢价）", "",
                  "| 稳态项 | 悲观 | 中性 | 乐观 | 理由 | 依据 |", "| --- | --- | --- | --- | --- | --- |"]
        for r in a.steady_rows:
            lines.append(f"| {row_label(r)} | {r.bear:.2%} | {r.base:.2%} | {r.bull:.2%} | {r.note or '—'} | {ref(r)} |")
        lines.append("")
    gm_case: dict = {}
    try:
        if a._m() is not None and a.base_margin is not None:
            from ir_agent.assume import synthesize
            for c, k in ((Case.BEAR, "bear"), (Case.BASE, "base"), (Case.BULL, "bull")):
                gm_case[k] = synthesize(a, c, a.base_margin, a.margin_bases)[1]
        elif a._m() is None:
            gm_case = {k: a.path("gm", c) for c, k in ((Case.BEAR, "bear"), (Case.BASE, "base"), (Case.BULL, "bull"))}
    except Exception:                                   # noqa: BLE001
        gm_case = {}
    ml = margin_lines(a, gm_case)
    if ml:
        lines += [x for x in ml if x] + [""]
    return lines
