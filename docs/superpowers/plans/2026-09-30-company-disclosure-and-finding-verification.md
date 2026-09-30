# 公司披露入语料库 + Finding 引文的代码核验 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把上市公司法定披露（巨潮公告）接进研究语料库作为第四个来源等级，并让开放网络搜到的每条 `Finding` 都由代码回原页核实过，从而使「来源等级」这个代理指标不再需要承担它承担不了的职责。

**Architecture:** 新增 `Tier.COMPANY`，只由巨潮适配器赋予、绝不进 `web.tier_of` 白名单。新建 `ir_agent/sources/announce.py`：代码从 `cninfo.search` 取回真实公告清单，模型只挑**下标**，代码按 `ann_id` 下载 PDF 并用 PyMuPDF 取文入库。`gaps` 的采集阶梯插进 ②.5（公告，排在研报之前）。`openweb.verify_finding` 用既有的 `fetch` 把 `Finding` 的网址抓回来核验 claim，核不上的降级为线索；`supports_value` 随之放宽到 MEDIA 也够格——**这两件事必须同一个任务落地**。

**Tech Stack:** Python 3.13、既有依赖 `requests` 与 PyMuPDF（`fitz`）、既有适配器 `ir_agent/sources/cninfo.py`、`pytest`。设计文档：`docs/superpowers/specs/2026-09-30-company-disclosure-and-finding-verification-design.md`。

## Global Constraints

- **不引入任何新的第三方依赖。** `requests`、PyMuPDF（`fitz`）、`cninfo` 都是既有的；`fitz` 在 `annual_pdf.py` 与 `annual_sections.py` 里已在用（函数内惰性 import，沿用这个写法）。
- **`Tier.COMPANY` 只能由巨潮适配器赋予，绝不加进 `web.tier_of(publisher)` 的任何白名单。** `tier_of` 吃模型自报的机构名；模型写一个公司名就能拔级，而 COMPANY 够格支撑因子取值。Task 1 有专门的不变式测试守这条。
- **`openweb.tier_of_url` 一行不动。** 公司自有 IR 站点不映射为 COMPANY；公告只从巨潮这一条确定性路径进来。
- **「事实 vs 管理层展望」的区分写在 `TIER_RULE` 提示词里，不加代码闸门**——与 SELLSIDE 现有做法一致。
- **Task 5 与 Task 6 不得分开提交。** 门槛放宽只在 B2 落地时成立，理由见 spec 第六节。
- 公告规模，逐字照抄：窗口 `ANNOUNCE_MONTHS = 12`、上限 `MAX_ANNOUNCEMENTS = 8`、进提示词截断沿用 `openweb.TEXT_LIMIT = 2000`、PDF 取文页数上限 `PDF_MAX_PAGES = 30`。
- **时点守卫按 `Announcement.ann_date`（披露日），不是抓取日。** `cninfo.py` 模块注释写着：把抓取日当 as_of 会让回测凭空多出几天信息优势，且不会报错。
- 资料收集的任何失败都不得阻断整轮研究，只记入产物清单（`skipped`）。
- 测试函数名用中文，与 `tests/test_web_source.py`、`tests/test_gaps.py`、`tests/test_openweb.py` 的既有风格一致。
- 每个任务结束时跑 `python -m pytest tests -q -m "not live"`，必须全绿再提交。基线：`main` 上 1848 passed, 19 deselected。

---

## File Structure

| 文件 | 职责 | 任务 |
|---|---|---|
| `ir_agent/sources/external.py`（改） | `Tier.COMPANY` 成员 + `TIER_LABEL` + `TIER_RULE` | 1 |
| `ir_agent/sources/announce.py`（新建） | 公告采集：候选清单渲染、挑选解析、PDF 取文、入库编排 | 2、3 |
| `ir_agent/sources/openweb.py`（改） | `verify_finding`：用既有 `fetch` 回原页核实 `Finding.claim` | 5 |
| `ir_agent/sources/web.py`（改） | `supports_value` 放宽 + `findings` 按 tier 排序 | 6 |
| `ir_agent/gaps.py`（改） | ②.5 公告阶梯、`ANNOUNCE` 状态标签、B2 接线、仅媒体支撑的标注 | 4、6 |
| `ir_agent/full.py`（改） | 公告采集接入点 + 产物清单文案 | 7 |
| `tests/test_external_tier.py`（新建） | `Tier.COMPANY` 与不变式守卫 | 1 |
| `tests/test_announce.py`（新建） | 公告采集全部行为 | 2、3 |
| `tests/test_openweb.py`（改） | `verify_finding` | 5 |
| `tests/test_gaps.py`（改） | ②.5 阶梯、B2 接线、标注 | 4、6 |
| `tests/test_web_source.py`（改） | `supports_value` 新口径 | 6 |
| `tests/test_full_context.py`（改） | 接入点的结构性断言 | 7 |

---

## Task 1: `Tier.COMPANY` 与它的不变式守卫

**Files:**
- Modify: `ir_agent/sources/external.py`（`Tier` 类、`TIER_LABEL`、`TIER_RULE`）
- Test: `tests/test_external_tier.py`（新建）

**Interfaces:**
- Consumes: 无（本任务是基础）
- Produces: `Tier.COMPANY`（枚举值字符串 `"company"`）；`TIER_LABEL[Tier.COMPANY] == "公司披露"`；`TIER_RULE[Tier.COMPANY]` 为一段中文规则文本

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_external_tier.py`：

```python
"""来源分级的第四级：上市公司法定披露。

**分级跟着来源的取回方式走，不跟着名字走。** 本文件最要紧的一条是
`test_不变式_tier_of绝不产出COMPANY`——见其文档字符串。
"""

from __future__ import annotations

from ir_agent.sources.external import TIER_LABEL, TIER_RULE, Tier


def test_COMPANY_是第四个成员且值稳定():
    assert Tier.COMPANY.value == "company"
    assert Tier("company") is Tier.COMPANY
    assert set(Tier) == {Tier.OFFICIAL, Tier.SELLSIDE, Tier.MEDIA,
                         Tier.COMPANY}


def test_COMPANY_有标签与可引用范围():
    assert TIER_LABEL[Tier.COMPANY] == "公司披露"
    rule = TIER_RULE[Tier.COMPANY]
    # 事实可引、管理层展望不得作为依据——照 SELLSIDE 的形状
    assert "事实" in rule
    assert "展望" in rule
    assert "不得作为" in rule


def test_每个成员都有标签与规则():
    """新增成员时漏配 TIER_LABEL/TIER_RULE 会让三个提示词拼装点 KeyError
    ——它们按 `for t in Tier` 遍历。"""
    for t in Tier:
        assert t in TIER_LABEL, t
        assert t in TIER_RULE, t
        assert TIER_LABEL[t] and TIER_RULE[t]


def test_不变式_tier_of绝不产出COMPANY():
    """`web.tier_of` 吃的是**模型自报的机构名字符串**。

    一旦某个公司名（或修饰词组合）能让它返回 COMPANY，模型在 `发现:` 行
    里写一个 `| 生益科技 |` 就能把任意网页拔到 COMPANY 级——而 COMPANY
    够格支撑因子取值（见 supports_value），那等于一句转述直接决定 DCF
    里的一个数。

    COMPANY 只能由巨潮适配器赋予：来源是代码亲手请求的确定性接口，分级
    跟着取回方式走。这与 `tier_of_url` 和 `tier_of` 必须并存不能合并是
    同一条理由。
    """
    from ir_agent.sources.web import (_OFFICIAL_ASCII, _OFFICIAL_CN,
                                      _SELLSIDE_ASCII, _SELLSIDE_CN,
                                      tier_of)

    # 白名单里任何条目、以及公司名/交易所名这类容易被误加的字符串
    probes = list(_OFFICIAL_CN) + list(_OFFICIAL_ASCII) \
        + list(_SELLSIDE_CN) + list(_SELLSIDE_ASCII) \
        + ["生益科技", "生益科技股份有限公司", "生益科技公告",
           "巨潮资讯", "巨潮资讯网", "上海证券交易所", "深圳证券交易所",
           "公司公告", "上市公司公告", "", "   "]
    for name in probes:
        assert tier_of(name) is not Tier.COMPANY, name


def test_不变式_tier_of_url也绝不产出COMPANY():
    """公司自有 IR 站点不映射为 COMPANY——公告只从巨潮进来。"""
    from ir_agent.sources.openweb import (_OFFICIAL_HOSTS, _SELLSIDE_HOSTS,
                                          tier_of_url)

    probes = [f"https://{h}/x" for h in _OFFICIAL_HOSTS + _SELLSIDE_HOSTS]
    probes += ["https://www.shengyi.com/x", "http://www.cninfo.com.cn/x",
               "http://static.cninfo.com.cn/a.PDF",
               "https://www.sse.com.cn/x", "https://www.szse.cn/x",
               "https://www.stats.gov.cn/x", "not a url", ""]
    for url in probes:
        assert tier_of_url(url) is not Tier.COMPANY, url
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_external_tier.py -q
```

Expected: FAIL，`AttributeError: COMPANY`（`Tier` 没有这个成员）

- [ ] **Step 3: 加枚举成员与两张表**

在 `ir_agent/sources/external.py` 的 `Tier` 类里，`MEDIA = "media"` 之后追加：

```python
    COMPANY = "company"
```

并在该类的文档字符串里那份分级说明的末尾（`MEDIA      财经媒体 —— 只作线索，不作依据` 之后）追加一行：

```
      COMPANY   上市公司法定披露 —— 公告中的事实可直接引为依据；
                管理层对前景的表述不得作为本模型假设的依据
```

`TIER_LABEL` 追加一项：

```python
    Tier.COMPANY: "公司披露",
```

`TIER_RULE` 追加一项：

```python
    Tier.COMPANY: ("公告中的**事实**（产能、投产进度、订单、股权变动、"
                   "提价）可直接引为依据；其中**管理层对前景、战略意图、"
                   "市场判断的表述不得作为本报告任何假设的依据**——"
                   "披露义务保证的是事实的真实性，不是展望的准确性。"),
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_external_tier.py -q
```

Expected: PASS（5 passed）

- [ ] **Step 5: 确认三个提示词拼装点自动打印新规则**

```bash
python -m pytest tests -q -m "not live" -k "tier or TIER or 分级"
```

Expected: 全部通过。这三处（`ir_agent/strategy.py`、`ir_agent/value_chain.py`、`ir_agent/macro_view.py`）按 `for t in Tier` 遍历，新成员会自动出现，无需改动。

- [ ] **Step 6: 跑全量并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 1848 + 5 = 1853 passed

```bash
git add ir_agent/sources/external.py tests/test_external_tier.py
git commit -m "external: 新增 Tier.COMPANY，并用不变式守住它只能由代码赋予"
```

---

## Task 2: 公告候选清单与挑选解析

**Files:**
- Create: `ir_agent/sources/announce.py`
- Create: `tests/test_announce.py`

**Interfaces:**
- Consumes: `ir_agent.lineparse.keyed(name) -> re.Pattern[str]`（既有，匹配 `关键词: 其余内容` 并容忍 `- ` / `1. ` / `**加粗**` 前缀）；`ir_agent.sources.cninfo.Announcement`（既有 frozen dataclass，字段 `ann_id, title, ann_date, url, code, name, category, source_id`）
- Produces: 常量 `ANNOUNCE_MONTHS = 12`、`MAX_ANNOUNCEMENTS = 8`、`PDF_MAX_PAGES = 30`；`format_candidates(anns: list[Announcement]) -> str`；`parse_picks(text: str, n_candidates: int) -> tuple[list[int], list[str]]`；`PROMPT: str`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_announce.py`：

```python
"""公司公告入语料库。**模型只挑下标，正文由代码抓。**

这个契约比 openweb 的 discover/fetch 更强：openweb 里模型给的是 URL
（一个它可以凭空构造的字符串），这里模型给的是指向代码已有列表的下标，
可编造的空间从「任意网址」缩到「这些条目里的哪几条」。
"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent.sources.announce import (MAX_ANNOUNCEMENTS, PROMPT,
                                       format_candidates, parse_picks)
from ir_agent.sources.cninfo import Announcement

AS_OF = date(2026, 9, 30)


def _ann(i: int, title: str, when: date) -> Announcement:
    return Announcement(
        ann_id=f"a{i}", title=title, ann_date=when,
        url=f"http://static.cninfo.com.cn/finalpage/{when}/a{i}.PDF",
        code="600183", name="生益科技", category="其他",
        source_id=f"src{i}")


ANNS = [
    _ann(0, "生益科技关于高频高速覆铜板扩产项目投产的公告", date(2026, 8, 1)),
    _ann(1, "生益科技2026年半年度报告", date(2026, 8, 28)),
    _ann(2, "生益科技关于部分产品价格调整的公告", date(2026, 5, 20)),
]


def test_候选清单带序号标题与披露日():
    text = format_candidates(ANNS)
    assert "0" in text and "1" in text and "2" in text
    assert "高频高速覆铜板扩产项目投产" in text
    assert "2026-08-01" in text


def test_候选清单不含下载地址():
    """正文的下载地址由代码按 ann_id 取，不经模型——清单里给了 URL 反而
    让模型有机会把它改掉再交回来。"""
    text = format_candidates(ANNS)
    for a in ANNS:
        assert a.url not in text


def test_解析挑选出的下标():
    picks, ignored = parse_picks(
        "选取: 0 | 与名义产能相关\n选取: 2 | 与ASP相关\n", 3)
    assert picks == [0, 2]
    assert ignored == []


def test_越界的下标被忽略并记录():
    picks, ignored = parse_picks("选取: 0\n选取: 99\n", 3)
    assert picks == [0]
    assert len(ignored) == 1 and "99" in ignored[0]


def test_重复的下标只取一次():
    picks, ignored = parse_picks("选取: 1\n选取: 1\n选取: 1\n", 3)
    assert picks == [1]


def test_非数字的下标被忽略并记录():
    picks, ignored = parse_picks("选取: 第一条 | 相关\n选取: 2\n", 3)
    assert picks == [2]
    assert len(ignored) == 1


def test_超过上限只取前若干条():
    lines = "".join(f"选取: {i}\n" for i in range(20))
    picks, _ = parse_picks(lines, 20)
    assert len(picks) == MAX_ANNOUNCEMENTS


def test_负数下标被忽略():
    """`-1` 在 Python 里是合法索引，会静默取到最后一条——不是模型的意思。"""
    picks, ignored = parse_picks("选取: -1\n选取: 0\n", 3)
    assert picks == [0]
    assert len(ignored) == 1


def test_容忍列表前缀与加粗():
    picks, _ = parse_picks("1. **选取**: 2 | 相关\n", 3)
    assert picks == [2]


def test_一条都没挑出来时返回空():
    picks, ignored = parse_picks("我觉得都不相关。\n", 3)
    assert picks == [] and ignored == []


def test_提示词说明只给下标且逐字含上限():
    assert "序号" in PROMPT
    assert str(MAX_ANNOUNCEMENTS) in PROMPT
    assert "选取:" in PROMPT
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_announce.py -q
```

Expected: FAIL，`ModuleNotFoundError: No module named 'ir_agent.sources.announce'`

- [ ] **Step 3: 写实现**

创建 `ir_agent/sources/announce.py`：

```python
"""上市公司法定披露入语料库 —— 公司级事实的权威来源。

**为什么需要它。** 实跑（600183，2026-09-30）里 8 个分解式因子有 7 个落
「无数据」，而其中 6 个**其实搜到了**东西、只是全为财经媒体级被门槛挡下。
瓶颈不是搜得不够广——搜得更广只会搜到更多财经媒体。本模块往采集阶梯里
加一个**高等级的确定性来源**，而不是设法让模型搜得更好：模型挑什么网址
不可核验，一个确定性接口可核验。

**模型只挑下标，正文由代码抓。** 代码先用 `cninfo.search` 取回真实公告
清单，只把「序号 + 标题 + 披露日」交给模型；模型回一批序号，代码按对应
的 `Announcement.ann_id`/`url` 去巨潮下载 PDF。模型编不出一条不存在的
公告，正文的下载地址也不经它的手。

这个契约比 openweb 的 discover/fetch 分工**更强**：那里模型给的还是一个
它可以凭空构造的 URL，这里给的是指向代码已有列表的下标——可编造的空间
从「任意网址」缩到「这些条目里的哪几条」。最坏情况是挑了一条不相关的
公告，代价是浪费提示词长度，不是把假证据放进语料库。

**覆盖面要如实说。** 公告是**公司级事实**的权威来源，不是**行业级数据**
的来源。扩产公告能给「名义产能」，提价公告能给公司侧的「ASP」，但
「产能利用率」「良率」A 股基本不披露（见 gaps.py 模块注释），「原材料
成本冲击」这类上游价格公告不会给。本模块把「七个因子无数据」挪到
「四五个因子无数据」，剩下那几个属于确实不披露——那种情况下 gaps 标
「取值为估计、绝不用代理指标顶替」本来就是正确行为。
"""

from __future__ import annotations

import re

from ir_agent.lineparse import keyed
from ir_agent.sources.cninfo import Announcement

# 公告检索窗口（月）。比 corpus.gather 的研报窗口（24 个月）短：公告的
# 时效性更强，两年前的扩产公告对当期因子取值的参考价值已经很低，而条数
# 会翻倍、把模型要读的候选清单撑长。
ANNOUNCE_MONTHS = 12

# 单轮最多入库几篇。同一批文档会同时压到战略/产业链/宏观定性三个模块的
# 提示词上，长度是三倍的。
MAX_ANNOUNCEMENTS = 8

# PDF 取文的页数上限。多数公告只有一两页，但「定期报告」可能两百页以上，
# 全文取回既无必要也会让单篇挤掉其余七篇的额度。
PDF_MAX_PAGES = 30

_LINE = keyed("选取")
_INT = re.compile(r"^-?\d+$")


def format_candidates(anns: list[Announcement]) -> str:
    """渲染给模型看的候选清单。**只有序号、标题、披露日，没有下载地址。**

    给了 URL 反而让模型有机会把它改掉再交回来——正文的地址必须始终来自
    `cninfo` 的接口响应，不经模型的手。
    """
    return "\n".join(
        f"{i}. {a.ann_date:%Y-%m-%d}　{a.title}" for i, a in enumerate(anns))


def parse_picks(text: str, n_candidates: int) -> tuple[list[int], list[str]]:
    """解析 `选取:` 行，返回 (下标列表, 被忽略项的说明列表)。

    四道守卫，每一道被触发时都记一条说明——**被忽略的挑选要说得出来**，
    否则读者看到「入库 3 篇」无从判断模型是只挑了 3 条，还是挑了 8 条
    但 5 条不合法。

    负数单独拦：`-1` 在 Python 里是合法索引，不拦会静默取到最后一条
    公告，而那不是模型的意思。
    """
    picks: list[int] = []
    ignored: list[str] = []
    for hit in _LINE.finditer(text):
        raw = hit.group(1).split("|")[0].strip()
        if not _INT.match(raw):
            ignored.append(f"「{raw[:20]}」不是序号")
            continue
        idx = int(raw)
        if idx < 0 or idx >= n_candidates:
            ignored.append(f"序号 {idx} 超出候选范围（0~{n_candidates - 1}）")
            continue
        if idx in picks:
            continue
        if len(picks) >= MAX_ANNOUNCEMENTS:
            ignored.append(f"序号 {idx} 超出单轮上限 {MAX_ANNOUNCEMENTS} 篇")
            continue
        picks.append(idx)
    return picks, ignored


PROMPT = """从下面这家公司的法定披露公告里，挑出与本次估值分解式相关的几条。

公司：{company}（{code}）
本次分解式关注的驱动因子：{factors}

候选公告（代码已从巨潮资讯取回，**只能从这个清单里挑**）：
{candidates}

每条一行，只给序号：

`选取: <序号> | <一句话：与哪个因子相关>`

**硬性要求**
- **只能给上面清单里的序号**，最多 {limit} 条。序号之外的内容会被忽略。
- 优先挑能给出**具体数字**的：扩产/投产、产能、价格调整、重大合同、
  对外投资。纯程序性公告（股东大会通知、董事会决议、独立董事声明、
  会计师事务所改聘）对因子取值没有帮助，不要挑。
- **不要挑「关于召开业绩说明会的公告」这类只是通知会议将要举行的公告**
  ——它们没有内容。
- 一条都不相关就不要输出任何 `选取:` 行，不要为了凑数而挑。

只输出上述格式的行。
"""
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_announce.py -q
```

Expected: PASS（11 passed）

- [ ] **Step 5: 跑全量并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 1853 + 11 = 1864 passed

```bash
git add ir_agent/sources/announce.py tests/test_announce.py
git commit -m "announce: 候选清单与挑选解析，模型只给下标不给网址"
```

---

## Task 3: 公告抓取入库

**Files:**
- Modify: `ir_agent/sources/announce.py`
- Modify: `tests/test_announce.py`

**Interfaces:**
- Consumes: `ir_agent.sources.cninfo.search(code, start, end, page_size=30, max_pages=40, store=None, session=None) -> list[Announcement]`（既有）；`ir_agent.sources.annual_pdf._download(url, cache_dir=None) -> bytes`（既有，内含 `with_retry`）；`ir_agent.sources.external.Document` / `Corpus` / `Tier`；`ir_agent.sources.openweb.TEXT_LIMIT`；本模块的 `format_candidates` / `parse_picks` / `PROMPT` / 三个常量
- Produces: `@dataclass AnnounceResult(docs, candidates, picked, fetch_failed, fetch_failed_reasons, ignored)`；`pdf_text(data: bytes) -> str`；`gather(client, *, code, company, factors, as_of, corpus, cache_dir=None) -> AnnounceResult`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_announce.py`：

```python
from ir_agent.sources.announce import AnnounceResult, gather, pdf_text
from ir_agent.sources.external import Corpus, Tier


class _Client:
    """与 tests/test_openweb.py 的 _Client 同一形状：文本进、文本出。"""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _patch(monkeypatch, anns, pages):
    """anns 作为 cninfo.search 的返回；pages 是 {url: 文本} 的假 PDF。"""
    import ir_agent.sources.announce as A

    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)

    def _fake_download(url, cache_dir=None):
        if url not in pages:
            raise OSError("404")
        return pages[url].encode("utf-8")

    monkeypatch.setattr(A, "_download", _fake_download)
    monkeypatch.setattr(A, "pdf_text", lambda data: data.decode("utf-8"))


PAGES = {a.url: f"正文{i}：本公司本次扩产新增产能若干万平方米。"
         for i, a in enumerate(ANNS)}


def test_挑中的公告入库且分级为COMPANY(monkeypatch):
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0 | 名义产能\n选取: 2 | ASP\n"),
                 code="600183", company="生益科技",
                 factors=["名义产能", "ASP"], as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 2
    assert all(d.tier is Tier.COMPANY for d in cp.docs)
    assert res.candidates == 3 and res.picked == 2
    assert len(res.docs) == 2


def test_入库文档的时点用披露日不是抓取日(monkeypatch):
    """把抓取日当 as_of 会让回测凭空多出几天信息优势，且不会报错——
    cninfo.py 模块注释记着这条。"""
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    gather(_Client("选取: 0\n"), code="600183", company="生益科技",
           factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert cp.docs[0].published == ANNS[0].ann_date
    assert cp.docs[0].retrieved == AS_OF


def test_披露日晚于as_of的公告被挡下(monkeypatch):
    late = [_ann(9, "生益科技关于扩产的公告", date(2026, 12, 31))]
    _patch(monkeypatch, late, {late[0].url: "正文"})
    cp = Corpus(as_of=AS_OF)
    gather(_Client("选取: 0\n"), code="600183", company="生益科技",
           factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert cp.docs == []
    assert cp.skipped != []


def test_单篇下载失败不牵连其余(monkeypatch):
    partial = {k: v for k, v in PAGES.items() if "a0" not in k}
    _patch(monkeypatch, ANNS, partial)
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0\n选取: 1\n选取: 2\n"),
                 code="600183", company="生益科技",
                 factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 2
    assert res.fetch_failed == 1
    assert res.fetch_failed_reasons and "a0" in res.fetch_failed_reasons[0]


def test_取文为空的公告不入库(monkeypatch):
    _patch(monkeypatch, ANNS, {ANNS[0].url: "   \n  "})
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client("选取: 0\n"), code="600183", company="生益科技",
                 factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert cp.docs == []
    assert res.fetch_failed == 1
    assert "取文为空" in res.fetch_failed_reasons[0]


def test_被忽略的挑选传到结果里(monkeypatch):
    _patch(monkeypatch, ANNS, PAGES)
    res = gather(_Client("选取: 0\n选取: 99\n"),
                 code="600183", company="生益科技",
                 factors=["名义产能"], as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert len(res.ignored) == 1 and "99" in res.ignored[0]


def test_一条公告都没取到时不调模型(monkeypatch):
    """候选为空就没有可挑的，省一次模型调用。"""
    _patch(monkeypatch, [], {})
    c = _Client()          # 没有预备回复：一旦被调用就 IndexError
    res = gather(c, code="600183", company="生益科技",
                 factors=["名义产能"], as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.candidates == 0 and res.docs == []
    assert c.prompts == []


def test_入库文档带截断长度(monkeypatch):
    from ir_agent.sources.openweb import TEXT_LIMIT
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    gather(_Client("选取: 0\n"), code="600183", company="生益科技",
           factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert cp.docs[0].prompt_limit == TEXT_LIMIT


def test_已在语料库里的URL不重复入库(monkeypatch):
    from ir_agent.sources.external import Document
    _patch(monkeypatch, ANNS, PAGES)
    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="x", url=ANNS[0].url, title="已有",
                    publisher="生益科技", tier=Tier.COMPANY,
                    published=ANNS[0].ann_date, retrieved=AS_OF, text="已有"))
    gather(_Client("选取: 0\n选取: 1\n"), code="600183", company="生益科技",
           factors=["名义产能"], as_of=AS_OF, corpus=cp)
    assert [d.url for d in cp.docs].count(ANNS[0].url) == 1


def test_pdf取文按页数上限截断():
    """多数公告一两页，但定期报告可能两百页以上，全取会挤掉其余篇的额度。"""
    import ir_agent.sources.announce as A
    pages = []

    class _FakePage:
        def __init__(self, i):
            self.i = i

        def get_text(self):
            return f"第{self.i}页"

    class _FakeDoc:
        def __iter__(self):
            return iter([_FakePage(i) for i in range(100)])

        def close(self):
            pages.append("closed")

    import sys
    import types
    fake = types.ModuleType("fitz")
    fake.open = lambda **k: _FakeDoc()
    sys.modules["fitz"] = fake
    try:
        got = A.pdf_text(b"%PDF-1.4 fake")
        assert "第0页" in got
        assert f"第{A.PDF_MAX_PAGES - 1}页" in got
        assert f"第{A.PDF_MAX_PAGES}页" not in got
        assert pages == ["closed"], "PDF 句柄必须关闭"
    finally:
        del sys.modules["fitz"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_announce.py -q
```

Expected: FAIL，`ImportError: cannot import name 'AnnounceResult'`

- [ ] **Step 3: 写实现**

在 `ir_agent/sources/announce.py` 的 import 区补上：

```python
import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ir_agent.sources.annual_pdf import _download
from ir_agent.sources.cninfo import search as _search
from ir_agent.sources.external import Document, Tier
from ir_agent.sources.openweb import TEXT_LIMIT
```

> `_search` / `_download` 用模块级别名而不是在函数里 import：测试要
> monkeypatch 它们，别名让 patch 点稳定在本模块上。

在文件末尾追加：

```python
# 失败原因单条的存储上限，与 openweb 的同名约定一致：清单是给人看的，
# 一条原因长到几百字就没人读了。
_REASON_LIMIT = 160


@dataclass
class AnnounceResult:
    docs: list[Document] = field(default_factory=list)
    # 巨潮取回的候选条数。**必须能说出来**：读者看到「入库 3 篇」时，
    # 「候选 220 条里挑了 3 条」与「候选 3 条全挑了」含义完全不同。
    candidates: int = 0
    picked: int = 0
    fetch_failed: int = 0
    fetch_failed_reasons: list[str] = field(default_factory=list)
    # 模型给了但不合法的挑选（越界/非数字/超上限）。不说出来的话，
    # 读者无从判断是模型只挑了几条，还是挑了很多但大半不合法。
    ignored: list[str] = field(default_factory=list)


def pdf_text(data: bytes) -> str:
    """公告 PDF → 纯文本。页数上限见 `PDF_MAX_PAGES`。

    `fitz` 在函数内惰性 import，与 `annual_pdf.py`/`annual_sections.py`
    的既有写法一致。
    """
    import fitz                                   # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        parts: list[str] = []
        for i, page in enumerate(doc):
            if i >= PDF_MAX_PAGES:
                break
            parts.append(page.get_text())
    finally:
        doc.close()
    lines = [ln.strip() for ln in "\n".join(parts).splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _doc_id(ann_id: str) -> str:
    return "ann_" + hashlib.sha256(ann_id.encode("utf-8")).hexdigest()[:12]


def gather(client, *, code: str, company: str, factors: list[str],
           as_of: date, corpus, cache_dir: Path | str | None = None
           ) -> AnnounceResult:
    """公告入语料库：代码取清单 → 模型挑下标 → 代码抓 PDF。

    `client` 不需要联网工具——模型只从代码给的清单里挑，不上网。
    """
    res = AnnounceResult()
    start = as_of - timedelta(days=int(ANNOUNCE_MONTHS * 30.5))
    anns = _search(code, start, as_of)
    res.candidates = len(anns)
    if not anns:
        # 没有候选就没有可挑的，省一次模型调用。
        return res

    raw = client(PROMPT.format(
        company=company or code, code=code,
        factors="、".join(factors) or "（未产出分解式）",
        candidates=format_candidates(anns), limit=MAX_ANNOUNCEMENTS))
    picks, res.ignored = parse_picks(raw, len(anns))
    res.picked = len(picks)

    seen = {d.url for d in corpus.docs}
    for idx in picks:
        a = anns[idx]
        if a.url in seen:
            continue
        try:
            data = _download(a.url, cache_dir)
            text = pdf_text(data)
        except Exception as e:                              # noqa: BLE001
            _note_failure(res, f"{a.ann_id}：{e.__class__.__name__}: {e}")
            continue
        if not text.strip():
            _note_failure(res, f"{a.ann_id}：PDF 取文为空")
            continue
        doc = Document(
            doc_id=_doc_id(a.ann_id), url=a.url, title=a.title,
            publisher=a.name or company or code,
            # **分级由取回方式决定，不由名字决定。** 这条路径是代码
            # 亲手请求的巨潮接口，因此可以硬编码 COMPANY——与
            # reports.to_document 硬编码 SELLSIDE 同一个道理。
            # 绝不把公司名加进 web.tier_of 的白名单让它去"判"，
            # 那条路上模型自报一个名字就能拔级。
            tier=Tier.COMPANY,
            # **时点用披露日**（announcementTime），不是抓取日。
            published=a.ann_date, retrieved=as_of,
            text=text, prompt_limit=TEXT_LIMIT)
        if not corpus.add_if_visible(doc):
            continue
        seen.add(doc.url)
        res.docs.append(doc)
    return res


def _note_failure(res: AnnounceResult, reason: str) -> None:
    """记一条失败。**按诊断去重，不按整条消息去重**——每条消息都带着
    自己的 ann_id 前缀，拿整条去重的话「八篇都下载超时」会印八遍。
    与 openweb.gather 的同名处置一致。
    """
    res.fetch_failed += 1
    key = reason.split("：", 1)[-1]
    if any(r.split("：", 1)[-1] == key for r in res.fetch_failed_reasons):
        return
    res.fetch_failed_reasons.append(reason[:_REASON_LIMIT])


def summary(res: AnnounceResult) -> str:
    """给人看的采集结果。**缺什么必须说出来。**"""
    lines = [f"公司公告 {len(res.docs)} 篇"
             f"（候选 {res.candidates} 条，模型挑 {res.picked} 条）"]
    if res.fetch_failed:
        lines.append(f"  – {res.fetch_failed} 篇取文失败")
        for r in res.fetch_failed_reasons:
            lines.append(f"    · {r}")
    for x in res.ignored:
        lines.append(f"  – 忽略了一条挑选：{x}")
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_announce.py -q
```

Expected: PASS（21 passed）

- [ ] **Step 5: 跑全量并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 1864 + 10 = 1874 passed

```bash
git add ir_agent/sources/announce.py tests/test_announce.py
git commit -m "announce: 代码按 ann_id 抓 PDF 入库，时点用披露日"
```

---

## Task 4: `gaps` 阶梯插进 ②.5

**Files:**
- Modify: `ir_agent/gaps.py`
- Modify: `tests/test_gaps.py`

**Interfaces:**
- Consumes: `ir_agent.sources.external.Tier`（含 Task 1 的 `COMPANY`）；`gaps` 既有的 `_core_label(label) -> str`、`_valid_hit(text, core) -> str | None`
- Produces: 常量 `ANNOUNCE = "公告披露"`；`_match_announce(corpus, label) -> tuple[str, Document] | None`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_gaps.py`：

```python
def test_公告命中排在研报之前并标公告披露(monkeypatch):
    """②.5 排在 ③ 之前：公告是公司权威披露且由确定性接口取回，比两级
    模型中介的来源更近。状态标签不能沿用「研报测算」——那会把一份公司
    公告说成券商测算，是错误归因。"""
    import ir_agent.gaps as G
    from ir_agent.sources.external import Corpus, Document, Tier

    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="r1", url="https://r.example.com/a", title="研报",
                    publisher="某券商", tier=Tier.SELLSIDE,
                    published=date(2026, 6, 1), retrieved=AS_OF,
                    text="本文测算行业名义产能约 1200 万平方米。"))
    cp.add(Document(doc_id="a1", url="http://static.cninfo.com.cn/a.PDF",
                    title="扩产投产公告", publisher="生益科技",
                    tier=Tier.COMPANY, published=date(2026, 8, 1),
                    retrieved=AS_OF,
                    text="本次投产新增名义产能 300 万平方米。"))
    hit = G._match_announce(cp, "名义产能")
    assert hit is not None
    excerpt, doc = hit
    assert doc.tier is Tier.COMPANY
    assert "300 万平方米" in excerpt


def test_公告命中只看COMPANY文档():
    """②.5 是公告这一级，研报/媒体文档不该从这里命中——它们各有自己
    的一级。"""
    import ir_agent.gaps as G
    from ir_agent.sources.external import Corpus, Document, Tier

    cp = Corpus(as_of=AS_OF)
    for t in (Tier.SELLSIDE, Tier.MEDIA, Tier.OFFICIAL):
        cp.add(Document(doc_id=f"d{t.value}", url=f"https://x/{t.value}",
                        title="t", publisher="p", tier=t,
                        published=date(2026, 6, 1), retrieved=AS_OF,
                        text="名义产能约 1200 万平方米。"))
    assert G._match_announce(cp, "名义产能") is None


def test_公告的否定语境不算命中():
    """与 ②③ 同一条：「未披露名义产能的具体数据」不是命中。"""
    import ir_agent.gaps as G
    from ir_agent.sources.external import Corpus, Document, Tier

    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="a1", url="http://static.cninfo.com.cn/a.PDF",
                    title="公告", publisher="生益科技", tier=Tier.COMPANY,
                    published=date(2026, 8, 1), retrieved=AS_OF,
                    text="本公司未披露名义产能的具体数据。"))
    assert G._match_announce(cp, "名义产能") is None


def test_ANNOUNCE_标签文案():
    import ir_agent.gaps as G
    assert G.ANNOUNCE == "公告披露"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_gaps.py -q -k 公告 or ANNOUNCE
```

Expected: FAIL，`AttributeError: module 'ir_agent.gaps' has no attribute '_match_announce'`

- [ ] **Step 3: 写实现**

在 `ir_agent/gaps.py` 的状态常量区，`REPORT = "研报测算"` 之后追加：

```python
ANNOUNCE = "公告披露"   # ②.5 公司公告命中——同上，文字依据未必给出数值
```

并把该区顶部的模块级采集顺序注释（`① 账本 … ④ 开放网络`）更新为：

```python
#     ① 账本          已有事实
#     ② 年报叙述章节   产能/产量的文字披露
#     ②.5 公司公告     扩产/投产/提价等法定披露
#     ③ 研报语料库    深度报告里的行业产能测算
#     ④ 开放网络      SEMI 出货、基板报价、协会统计
```

在 `_match_report` 之后追加：

```python
def _match_announce(corpus, label: str) -> tuple[str, Document] | None:
    """②.5 公司公告里是否提到该因子，判据与 `_match_report` 同理。

    **只看 `Tier.COMPANY` 文档。** ②.5 是公告这一级；研报与媒体各有
    自己的一级（③ 与 ④），从这里命中会让状态标签说错话。

    公告排在研报之前，因为它是公司权威披露、且由确定性接口取回（模型只
    挑下标、不给网址），比两级模型中介的来源更近。
    """
    if corpus is None:
        return None
    core = _core_label(label)
    for d in corpus.docs:
        if d.tier is not Tier.COMPANY:
            continue
        hit = _valid_hit(d.text, core)
        if hit is not None:
            return hit, d
    return None
```

在 `collect` 里，`_match_report` 那一段**之前**插入：

```python
            hit = _match_announce(corpus, f.label)
            if hit is not None:
                excerpt, doc = hit
                out[f.key] = FactorData(
                    factor=f, status=ANNOUNCE,
                    note=f"「{excerpt}」——{doc.cite()}"
                         "（文字依据，是否给出具体数值待人工确认）",
                    source_url=doc.url, source_retrieved=doc.retrieved,
                    source_excerpt=excerpt)
                continue
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_gaps.py -q
```

Expected: PASS

- [ ] **Step 5: 确认 `summary` 把「公告披露」归进「文字依据」那一类**

```bash
python -m pytest tests/test_gaps.py -q -k summary
```

Expected: 通过。若 `gaps.summary` 用一个硬编码的状态集合来决定哪些算「仅命中文字」，把 `ANNOUNCE` 加进那个集合——它与 `ANNUAL`/`REPORT` 同类（命中关键词不等于拿到数值）。用 `grep -n "ANNUAL\|REPORT" ir_agent/gaps.py` 找出所有这类集合并逐个补上。

- [ ] **Step 6: 跑全量并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 1874 + 4 = 1878 passed

```bash
git add ir_agent/gaps.py tests/test_gaps.py
git commit -m "gaps: 公司公告插进 ②.5，状态标签「公告披露」不沿用研报测算"
```

---

## Task 5: B2 —— `Finding` 引文的代码核验

**Files:**
- Modify: `ir_agent/sources/openweb.py`
- Modify: `tests/test_openweb.py`

**Interfaces:**
- Consumes: `ir_agent.sources.web.Finding`（既有 frozen dataclass，字段 `factor_key, claim, url, publisher, tier, published`）；本模块既有的 `fetch(url, *, timeout=TIMEOUT) -> Fetched`、`FetchFailed`、`tier_of_url(url) -> Tier`；`ir_agent.sources.external.norm(s) -> str`（既有，去掉所有空白）
- Produces: `@dataclass(frozen=True) Verified(finding, ok, reason, text, final_url, tier)`；`verify_finding(finding) -> Verified`

> **为什么放在 `openweb.py`：** `fetch` 在这里，而 `web.py` 不 import
> openweb（`openweb` 反过来 import 了 `web.parse_date`）——把核验放进
> `web.py` 会成环。`gaps.py` import `openweb` 不成环。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_openweb.py`：

```python
# ── B2：Finding 引文的代码核验 ────────────────────────────────────
#
# 在此之前，一条 Finding 的证明力只有「模型说它在这个网址上见过」：
# claim 是模型转述、代码从没核实过，tier 来自模型自报的机构名。
# gaps 甚至把它包成 Document（text=claim）放进语料库，与代码亲手抓回
# 的文档躺在一起，Corpus.verify 分不出来。

def _finding(claim, url="https://finance.example.com/a",
             publisher="某财经网", tier=None):
    from datetime import date as _d

    from ir_agent.sources.external import Tier as _T
    from ir_agent.sources.web import Finding
    return Finding(factor_key="capacity", claim=claim, url=url,
                   publisher=publisher, tier=tier or _T.MEDIA,
                   published=_d(2026, 6, 1))


def test_claim在原页则核验通过并带回全文(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url=url, text="公司名义产能约 300 万平方米，同比增长 12%。",
        meta_published=None))
    got = OW.verify_finding(_finding("名义产能约 300 万平方米"))
    assert got.ok is True
    assert "同比增长 12%" in got.text, "入库要用抓回的全文，不是 claim"


def test_核验忽略空白差异(monkeypatch):
    """页面里可能是全角空格或换行——与 Corpus.verify 同一套 norm。"""
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url=url, text="名义产能约　300 万\n平方米", meta_published=None))
    assert OW.verify_finding(_finding("名义产能约 300 万平方米")).ok is True


def test_claim不在原页则核验不通过(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url=url, text="本文讨论覆铜板行业的竞争格局。",
        meta_published=None))
    got = OW.verify_finding(_finding("名义产能约 300 万平方米"))
    assert got.ok is False
    assert "原页" in got.reason


def test_抓不到也算核验不通过(monkeypatch):
    import ir_agent.sources.openweb as OW

    def _boom(url, **k):
        raise OW.FetchFailed(f"{url}：内容类型不是网页（application/pdf）")

    monkeypatch.setattr(OW, "fetch", _boom)
    got = OW.verify_finding(_finding("名义产能约 300 万平方米"))
    assert got.ok is False
    assert "application/pdf" in got.reason


def test_通过时tier按最终域名重判(monkeypatch):
    """模型自报 publisher 只能走 tier_of（白名单全称）；抓回来之后
    域名是代码观测到的，tier_of_url 比它可信。"""
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Tier
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url="https://www.miit.gov.cn/real", text="名义产能 300 万",
        meta_published=None))
    got = OW.verify_finding(_finding("名义产能 300 万", publisher="某财经网"))
    assert got.ok is True
    assert got.tier is Tier.OFFICIAL, "模型报的是媒体，实际落在 .gov.cn"
    assert got.final_url == "https://www.miit.gov.cn/real"


def test_核验不通过时不改tier也不带正文(monkeypatch):
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Tier
    monkeypatch.setattr(OW, "fetch", lambda url, **k: OW.Fetched(
        final_url="https://www.miit.gov.cn/real", text="无关正文",
        meta_published=None))
    got = OW.verify_finding(_finding("名义产能 300 万", tier=Tier.MEDIA))
    assert got.ok is False
    assert got.tier is Tier.MEDIA, "没核实的发现不该借重定向拿到升级"
    assert got.text == ""


def test_空claim直接算不通过(monkeypatch):
    """空串在任何正文里都"找得到"——那是 `in` 的性质，不是证据。"""
    import ir_agent.sources.openweb as OW

    def _never(url, **k):
        raise AssertionError("空 claim 不该发请求")

    monkeypatch.setattr(OW, "fetch", _never)
    assert OW.verify_finding(_finding("")).ok is False
    assert OW.verify_finding(_finding("   ")).ok is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_openweb.py -q -k verify
```

Expected: FAIL，`AttributeError: module 'ir_agent.sources.openweb' has no attribute 'verify_finding'`

- [ ] **Step 3: 写实现**

在 `ir_agent/sources/openweb.py` 的 import 区补上：

```python
from ir_agent.sources.external import norm
```

（若 `Document, Tier` 已从 `external` 导入，把 `norm` 并进那一行。）

在文件末尾追加：

```python
# ── B2：Finding 引文的代码核验 ────────────────────────────────────
#
# 在此之前，一条 `Finding` 的全部证明力是「模型说它在这个网址上见过」：
# `claim` 是模型转述、代码从没核实过，`tier` 来自 `web.tier_of(模型自报
# 的机构名)`。`gaps.collect` 甚至把它包成 `Document(text=x.claim)` 放进
# 语料库，与代码亲手抓回来的文档躺在同一个 `Corpus` 里，`Corpus.verify()`
# 分不出来。
#
# **这也是 `supports_value` 的 MEDIA 门槛真正在防的东西。** 那条门槛的
# 注释写的是「一条财经媒体报道不该单独决定 DCF 里的产能增速」，但危险
# 不在「媒体」这个等级——在于那句话无从核实，等级只是不可核验性的一个
# 代理指标。核验落地之后，「媒体 + 已核实」与「媒体 + 未核实」不是同一
# 个东西，门槛才可以放宽（见 web.supports_value 与本计划 Task 6）。


@dataclass(frozen=True)
class Verified:
    """一条 `Finding` 的核验结果。

    `ok=False` 时 `text` 为空串、`tier` 原样保留模型自报的那个：**没被
    核实的发现不该借重定向拿到升级**。
    """

    finding: object             # web.Finding；不标注类型以免引入循环导入
    ok: bool
    reason: str                 # ok=True 时为空串
    text: str                   # 代码抓回的全文；ok=False 时为空串
    final_url: str              # 跟完重定向后的地址；ok=False 时沿用原 URL
    tier: Tier


def verify_finding(finding) -> Verified:
    """把 `finding.url` 抓回来，核验 `finding.claim` 是否真的在正文里。

    核验用 `external.norm`（去掉所有空白）后做子串判断——与
    `Corpus.verify()` 同一套规范化，因为页面里可能是全角空格或换行，
    而模型转述时不会照抄空白。

    **空 claim 直接判不通过，且不发请求。** 空串在任何正文里都「找得
    到」，那是 `in` 运算的性质，不是证据。
    """
    claim = norm(finding.claim or "")
    if not claim:
        return Verified(finding=finding, ok=False, reason="转述为空，无从核实",
                        text="", final_url=finding.url, tier=finding.tier)
    try:
        got = fetch(finding.url)
    except FetchFailed as e:
        return Verified(finding=finding, ok=False, reason=str(e),
                        text="", final_url=finding.url, tier=finding.tier)
    if claim not in norm(got.text):
        return Verified(
            finding=finding, ok=False,
            reason=f"{finding.url}：模型转述「{finding.claim[:30]}」"
                   "未能在原页核实",
            text="", final_url=got.final_url, tier=finding.tier)
    return Verified(
        finding=finding, ok=True, reason="", text=got.text,
        final_url=got.final_url,
        # **分级按代码观测到的最终域名重判。** 模型自报的 publisher 只能
        # 走 tier_of 的全称白名单；域名是代码亲手请求到的，可信度高一个
        # 量级——见 tier_of_url 的文档字符串。
        tier=tier_of_url(got.final_url))
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: PASS

- [ ] **Step 5: 跑全量并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 1878 + 7 = 1885 passed

```bash
git add ir_agent/sources/openweb.py tests/test_openweb.py
git commit -m "openweb: verify_finding 回原页核实模型转述，通过后按最终域名重判分级"
```

---

## Task 6: B2 接线 + `supports_value` 放宽

**Files:**
- Modify: `ir_agent/sources/web.py`（`supports_value`）
- Modify: `ir_agent/gaps.py`（`collect` 的 ④ 分支）
- Modify: `tests/test_web_source.py`
- Modify: `tests/test_gaps.py`

**Interfaces:**
- Consumes: Task 5 的 `openweb.verify_finding(finding) -> Verified`
- Produces: `web.supports_value(findings) -> tuple[bool, str]`（语义变更）；`web.by_tier(findings) -> list[Finding]`（按 tier 降序，媒体排末尾）；`gaps` 的 `FactorData.note` 在仅媒体支撑时含「仅财经媒体支撑」

> **这个任务不得拆开提交。** 门槛放宽只在 B2 接线落地时成立：今天
> MEDIA 不够格的理由是 `claim` 不可核验，而核验正是本任务接上的东西。
> 若只留放宽、把接线拆走，一句只在财经媒体上出现过且无人核实的转述就能
> 直接进 DCF，而代码不会报任何错。见 spec 第六节。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_web_source.py`：

```python
def test_全媒体但都通过核验则够格():
    """门槛放宽的前提是 B2：核验之后「媒体 + 已核实」与「媒体 + 未核实」
    不是同一个东西。等级只是不可核验性的代理指标。"""
    from ir_agent.sources.web import supports_value
    ok, why = supports_value([
        Finding(factor_key="k", claim="产能 300 万", url="https://a.example/1",
                publisher="某财经网", tier=Tier.MEDIA, published=AS_OF),
    ])
    assert ok is True and why == ""


def test_一条都没有仍然不够格():
    from ir_agent.sources.web import supports_value
    ok, why = supports_value([])
    assert ok is False and "未找到" in why


def test_按tier降序排列媒体在末尾():
    from ir_agent.sources.web import by_tier
    f = [
        Finding(factor_key="k", claim="c", url="https://a/1", publisher="p",
                tier=Tier.MEDIA, published=AS_OF),
        Finding(factor_key="k", claim="c", url="https://a/2", publisher="p",
                tier=Tier.OFFICIAL, published=AS_OF),
        Finding(factor_key="k", claim="c", url="https://a/3", publisher="p",
                tier=Tier.SELLSIDE, published=AS_OF),
    ]
    assert [x.tier for x in by_tier(f)] == [Tier.OFFICIAL, Tier.SELLSIDE,
                                            Tier.MEDIA]
```

追加到 `tests/test_gaps.py`：

```python
def test_核验不通过的发现不算合格来源(monkeypatch):
    """降级为线索：不丢弃、不阻断，但不能撑因子取值。"""
    import ir_agent.gaps as G
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Corpus, Tier
    from ir_agent.sources.web import Finding

    f = Finding(factor_key="capacity", claim="产能 300 万",
                url="https://m.example.com/a", publisher="某财经网",
                tier=Tier.MEDIA, published=date(2026, 6, 1))
    monkeypatch.setattr(G, "search", lambda *a, **k: [f])
    monkeypatch.setattr(G, "verify_finding", lambda x: OW.Verified(
        finding=x, ok=False, reason="…未能在原页核实", text="",
        final_url=x.url, tier=x.tier))
    data = _collect_one_factor(G, corpus=Corpus(as_of=AS_OF))
    fd = data["capacity"]
    assert fd.status == G.MISSING
    assert "核实" in fd.note


def test_核验通过的发现入库用抓回的全文(monkeypatch):
    """在此之前入库的 Document.text 是模型转述的一句话，与代码抓回的
    文档在同一个 Corpus 里分不出来（子项目 A 终审 NEW-3）。"""
    import ir_agent.gaps as G
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Corpus, Tier
    from ir_agent.sources.web import Finding

    f = Finding(factor_key="capacity", claim="产能 300 万",
                url="https://m.example.com/a", publisher="某财经网",
                tier=Tier.MEDIA, published=date(2026, 6, 1))
    monkeypatch.setattr(G, "search", lambda *a, **k: [f])
    monkeypatch.setattr(G, "verify_finding", lambda x: OW.Verified(
        finding=x, ok=True, reason="",
        text="产能 300 万平方米，全年满产。",
        final_url="https://m.example.com/a", tier=Tier.MEDIA))
    cp = Corpus(as_of=AS_OF)
    data = _collect_one_factor(G, corpus=cp)
    assert data["capacity"].status == G.EXTERNAL
    doc = next(d for d in cp.docs if d.url == "https://m.example.com/a")
    assert "全年满产" in doc.text, "入库要用抓回的全文"


def test_仅媒体支撑的因子在清单上被标注(monkeypatch):
    """放宽之后媒体能撑因子，但证据强度要让人在批准时看得见。"""
    import ir_agent.gaps as G
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Corpus, Tier
    from ir_agent.sources.web import Finding

    f = Finding(factor_key="capacity", claim="产能 300 万",
                url="https://m.example.com/a", publisher="某财经网",
                tier=Tier.MEDIA, published=date(2026, 6, 1))
    monkeypatch.setattr(G, "search", lambda *a, **k: [f])
    monkeypatch.setattr(G, "verify_finding", lambda x: OW.Verified(
        finding=x, ok=True, reason="", text="产能 300 万平方米。",
        final_url=x.url, tier=Tier.MEDIA))
    data = _collect_one_factor(G, corpus=Corpus(as_of=AS_OF))
    assert "仅财经媒体支撑" in data["capacity"].note
```

在 `tests/test_gaps.py` 顶部（其它辅助函数旁）加上这个夹具——**它被上面
三条测试共用，必须存在**：

```python
def _collect_one_factor(G, corpus):
    """跑一次只有一个因子、且该因子在账本里缺失的 collect。

    ②③②.5 都不会命中（sections=None、corpus 里没有 COMPANY/SELLSIDE
    文档提到它），所以一定走到 ④。
    """
    from ir_agent.drivers import Decomposition, Factor, Op
    from ir_agent.ledger import FactLedger

    d = Decomposition(target="revenue_growth", op=Op.MULTIPLY,
                      factors=[Factor(key="capacity", label="名义产能",
                                      unit="变化率", source_hint="")])
    return G.collect(lambda p: "", [d], FactLedger(), corpus, "电子/元件",
                     AS_OF, sections=None, annual_url="")
```

> 这三个构造签名已核实（写本计划时实际查过）：
> `Decomposition(target: str, op: Op, factors: list[Factor])`、
> `Factor(key: str, label: str, unit: str, source_hint: str = "")`、
> `Op` 的成员是 `MULTIPLY` 与 `ADD_PP`。照抄即可。

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_web_source.py tests/test_gaps.py -q -k "核验 or 仅媒体 or 全媒体 or by_tier or 降序"
```

Expected: FAIL，`ImportError: cannot import name 'by_tier'`

- [ ] **Step 3: 改 `supports_value` 并加 `by_tier`**

在 `ir_agent/sources/web.py` 中，把 `supports_value` 整个替换为：

```python
# 排序用的等级次序。仅用于**呈现**：把证明力强的排在前面，媒体排末尾。
# 不用于任何门槛判断——门槛见 supports_value。
_TIER_ORDER = {Tier.OFFICIAL: 0, Tier.COMPANY: 1, Tier.SELLSIDE: 2,
               Tier.MEDIA: 3}


def by_tier(findings: list[Finding]) -> list[Finding]:
    """按来源等级降序排列，媒体排末尾。**只影响呈现顺序。**

    放宽门槛之后媒体级发现也能撑因子取值，但读者（以及读提示词的模型）
    应该先看到证明力强的那几条。稳定排序：同级内保留原有先后。
    """
    return sorted(findings, key=lambda f: _TIER_ORDER.get(f.tier, 99))


def supports_value(findings: list[Finding]) -> tuple[bool, str]:
    """这组发现够不够支撑一个因子的取值。

    **门槛是「有没有被代码在原页核实过」，不再是来源等级。**

    此前这里要求至少一条 OFFICIAL/SELLSIDE，注释写的是「一条财经媒体
    报道可以作为线索，不该单独决定 DCF 里的产能增速」。但真正危险的
    不是「媒体」这个等级——是那条 `Finding.claim` **是模型转述、代码
    从没核实过**。等级只是不可核验性的一个代理指标。
    `openweb.verify_finding` 把网址抓回来逐字核实之后，「媒体 + 已核实」
    与「媒体 + 未核实」不是同一个东西，代理指标就不必再承担它承担不了
    的职责。

    **调用方必须只把核验通过的发现传进来。** 核验不通过的已在
    `gaps.collect` 里降级为线索、不进这个列表——否则这条放宽就是裸的。
    这个契约在 `gaps` 那一侧，本函数无从自己检查：见本计划 Task 6 的
    「不得拆开提交」一段。

    仅靠媒体支撑时**仍然要在批准清单上标注**（`gaps` 负责），让人看得见
    证据强度——放宽的是门槛，不是知情。
    """
    if not findings:
        return False, "未找到任何数据"
    return True, ""
```

- [ ] **Step 4: 在 `gaps.collect` 的 ④ 分支接上核验**

在 `ir_agent/gaps.py` 的 import 区，把既有的 web 那一行扩为：

```python
from ir_agent.sources.openweb import verify_finding
from ir_agent.sources.web import (Finding, WebUnavailable, by_tier, search,
                                  supports_value)
```

把 `collect` 里 `found = search(...)` 之后、`ok, why = supports_value(found)`
之前的部分改为：

```python
            # ── B2：每条发现都回原页核实 ────────────────────────────
            # 核验不通过的**降级为线索**：不丢弃、不阻断，但不算进
            # supports_value。这是门槛得以放宽到 MEDIA 的前提——
            # 见 web.supports_value 的文档字符串。
            verified = [verify_finding(x) for x in found]
            passed = [v for v in verified if v.ok]
            unverified = [v for v in verified if not v.ok]
            found = by_tier([v.finding for v in passed])
```

再把 `ok` 分支里入库那一段的 `text=x.claim` 与 `tier=x.tier` 改为用核验
结果（把 `for x in found:` 换成按 `passed` 遍历）：

```python
            ok, why = supports_value(found)
            if ok:
                for v in passed:
                    x = v.finding
                    corpus.add_if_visible(Document(
                        doc_id=f"web_{f.key}_{len(corpus.docs)}",
                        url=v.final_url, title=f"{f.label}：{x.claim[:30]}",
                        publisher=x.publisher,
                        # 核验通过后分级按**代码观测到的最终域名**重判，
                        # 不用模型自报的机构名。
                        tier=v.tier,
                        published=x.published, retrieved=as_of,
                        # **入库的是代码抓回的全文，不是模型转述的那句话。**
                        # 此前这里是 text=x.claim——一句从没被抓取过的
                        # 转述，和代码亲手抓回来的文档躺在同一个 Corpus
                        # 里，Corpus.verify() 分不出来（子项目 A 终审
                        # NEW-3 记的就是这条）。
                        text=v.text, prompt_limit=TEXT_LIMIT))
                note = ""
                if all(v.tier is Tier.MEDIA for v in passed):
                    # 放宽的是门槛，不是知情：仅靠媒体支撑时证据强度
                    # 要让人在批准时看得见。
                    note = "仅财经媒体支撑（已在原页逐字核实）"
                if unverified:
                    note += ("；" if note else "") + \
                        f"另有 {len(unverified)} 条模型转述未能在原页核实，" \
                        "已降级为线索"
                out[f.key] = FactorData(factor=f, status=EXTERNAL,
                                        findings=found, note=note)
            else:
```

`else` 分支（没有任何合格来源）改为区分两种情形：

```python
            else:
                if unverified and not passed:
                    # 「搜到了但核实不过」与「什么都没搜到」对读者的含义
                    # 不同：前者是证据链起步了但站不住，后者是连线索都
                    # 没有。
                    note = (f"{len(unverified)} 条模型转述均未能在原页核实"
                            f"（{unverified[0].reason[:60]}）—— 取值为估计")
                else:
                    note = f"{why} —— 取值为估计"
                out[f.key] = FactorData(
                    factor=f, status=MISSING,
                    findings=[v.finding for v in verified], note=note)
```

在 `gaps.py` 的 import 区补上 `TEXT_LIMIT`：

```python
from ir_agent.sources.openweb import TEXT_LIMIT, verify_finding
```

- [ ] **Step 5: 跑测试确认通过**

```bash
python -m pytest tests/test_web_source.py tests/test_gaps.py -q
```

Expected: PASS。既有测试里若有断言「全媒体不够格」的，那是本任务**有意
改变**的行为——改写它去断言新口径（核验不通过才不够格），并在报告里
逐条说明改了哪些、为什么。

- [ ] **Step 6: 跑全量并提交（两处一起提交）**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过

```bash
git add ir_agent/sources/web.py ir_agent/gaps.py \
        tests/test_web_source.py tests/test_gaps.py
git commit -m "gaps/web: Finding 回原页核实，门槛随之从来源等级改为是否已核实"
```

---

## Task 7: 接入 `full.py`

**Files:**
- Modify: `ir_agent/full.py`
- Modify: `tests/test_full_context.py`

**Interfaces:**
- Consumes: `ir_agent.sources.announce.gather(client, *, code, company, factors, as_of, corpus, cache_dir=None) -> AnnounceResult`、`announce.summary(res) -> str`；`full.py` 既有的 `_company_name(code) -> str`、`_refresh_external_md(r, md) -> None`、`out: Path`、`meth`（可能为 `None`）、`qual_client`
- Produces: `_load_announce(code, r, as_of, client, md, industry_factors) -> list[str]`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_full_context.py`：

```python
def test_公告采集排在定性三节之前():
    """战略/产业链/宏观定性要引用公告原文，采集必须更早。"""
    import ast
    import inspect
    import textwrap

    from ir_agent import full
    tree = ast.parse(textwrap.dedent(inspect.getsource(full.run_analysis)))
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.setdefault(node.func.id, node.lineno)
    assert "_load_announce" in names, "公告采集没有被调用"
    assert names["_load_announce"] < names["_analyse_qualitative"]
    assert names["_analyse_facts"] < names["_load_announce"]


def test_公告采集不在确定性层里():
    """`_analyse_facts` 的不变量是「不调模型，因此两个阶段都要跑」。
    公告采集要调模型挑标题——放进去会让第二阶段挑出不同的公告，
    第一阶段写下的公告引文因此在第二阶段核验不过。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full._analyse_facts)
    assert "announce" not in src
    assert "_load_announce" not in src


def test_公告采集之后重渲染外部资料清单():
    """公告入的是同一个 Corpus。不重渲染，清单说的是加公告之前的篇数
    ——与子项目 A 终审 C1 是同一个失败模式。"""
    import ast
    import inspect
    import textwrap

    from ir_agent import full
    tree = ast.parse(textwrap.dedent(inspect.getsource(full.run_analysis)))
    calls = [(n.func.id, n.lineno) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    ann = [ln for name, ln in calls if name == "_load_announce"]
    ref = [ln for name, ln in calls if name == "_refresh_external_md"]
    assert ann and ref
    assert any(r > ann[0] for r in ref), \
        "_load_announce 之后必须再重渲染一次外部资料清单"


def test_公告采集失败不阻断整轮研究(monkeypatch):
    from datetime import date

    from ir_agent import full
    from ir_agent.sources.external import Corpus

    class _R:
        as_of = date(2026, 9, 30)
        corpus = Corpus(as_of=date(2026, 9, 30))

    import ir_agent.sources.announce as A
    monkeypatch.setattr(A, "gather",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("巨潮超时")))
    skipped = full._load_announce("600183", _R(), date(2026, 9, 30),
                                  lambda p: "", {}, ["名义产能"])
    assert skipped and "公司公告采集未执行" in skipped[0]
    assert "巨潮超时" in skipped[0]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_full_context.py -q -k 公告
```

Expected: FAIL，`AttributeError: module 'ir_agent.full' has no attribute '_load_announce'`

- [ ] **Step 3: 加 `_load_announce`**

在 `ir_agent/full.py` 中，紧跟在 `_restore_openweb` 之后插入：

```python
def _load_announce(code: str, r, as_of: date, client, md: dict,
                   factors: list[str]) -> list[str]:
    """公司公告入语料库。返回要记进产物清单的条目。

    **不能放进 `_analyse_facts`。** 那一层的不变量写在它自己的
    docstring 上：「不调模型，因此两个阶段都要跑」。本函数要调模型从
    候选清单里挑标题，放进去会让第二阶段挑出**与第一阶段不同的公告**，
    第一阶段正文里写下的公告引文因此在第二阶段核验不过——与
    `_load_openweb` 是同一条理由。

    与 `_load_openweb` 不同的是**本函数不需要联网工具的 client**：
    模型只从代码取回的清单里挑下标，不上网。
    """
    from ir_agent.sources import announce
    from ir_agent.sources.external import Corpus

    if r.corpus is None:
        r.corpus = Corpus(as_of=as_of)
    try:
        res = announce.gather(client, code=code, company=_company_name(code),
                              factors=factors, as_of=as_of, corpus=r.corpus)
    except Exception as e:                                  # noqa: BLE001
        return [f"公司公告采集未执行（{e.__class__.__name__}: {str(e)[:120]}）"]

    md["公司公告"] = announce.summary(res)
    if not res.docs:
        return [f"公司公告：一篇都没入库（候选 {res.candidates} 条，"
                f"模型挑 {res.picked} 条，取文失败 {res.fetch_failed} 篇）"]

    notes: list[str] = []
    if res.fetch_failed:
        notes.append(f"{res.fetch_failed} 篇取文失败")
        notes += [f"  - {x}" for x in res.fetch_failed_reasons]
    notes += [f"忽略了一条挑选：{x}" for x in res.ignored]
    if not notes:
        return []
    return ["公司公告：\n" + "\n".join(f"  · {x}" for x in notes)]
```

- [ ] **Step 4: 在 `run_analysis` 里插入调用**

在 `ir_agent/full.py` 的 `run_analysis` 中，找到 `_load_openweb(...)` 调用与
紧随其后的 `_refresh_external_md(r, md)`，在**那一对之后**插入：

```python
    # ── 公司公告入语料库 ────────────────────────────────────────
    # 接在开放网络之后、定性三节之前。公告是公司权威披露且由确定性接口
    # 取回，与 gaps 的 ②.5 一致地排在研报之前；这里的位置只需满足
    # 「定性三节之前」。用 qual_client 而非 research_client——模型只从
    # 代码取回的清单里挑下标，不需要联网工具。
    skipped += _load_announce(
        code, r, as_of, qual_client, md,
        [f.label for d in (meth.decompositions if meth is not None else [])
         for f in d.factors])
    # 公告入的是同一个 Corpus，清单要按语料库的实际内容重渲染一遍
    # ——不重渲染，md（进看板、进 state.json、第二阶段读回）说的就是
    # 加公告之前的篇数与分级构成。与子项目 A 终审 C1 是同一个失败模式。
    _refresh_external_md(r, md)
```

> **`qual_client` 必须在此之前创建。** 用
> `grep -n "qual_client = " ir_agent/full.py` 确认它的创建行在插入点
> 之前；若不是，把插入点移到它之后、`_analyse_qualitative` 之前。

- [ ] **Step 5: 跑测试确认通过**

```bash
python -m pytest tests/test_full_context.py -q
```

Expected: PASS

- [ ] **Step 6: 逐条确认结构性测试会红**

对每条新增的结构性测试，临时改动 `ir_agent/full.py` 制造它要防的那个违反
（把 `_load_announce` 挪到 `_analyse_qualitative` 之后；删掉后面那次
`_refresh_external_md`；把调用塞进 `_analyse_facts`），确认对应测试变红，
然后 `git checkout ir_agent/full.py` 恢复并用 `git diff` 确认没有残留。
在报告里逐条写明改了什么、看到了什么。

- [ ] **Step 7: 跑全量并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过

```bash
git add ir_agent/full.py tests/test_full_context.py
git commit -m "full: 公司公告采集接在开放网络之后、定性三节之前"
```

---

## Task 8: 端到端实跑验证

**Files:**
- 无代码改动；本任务只跑一次真实的第一阶段并核对产物

- [ ] **Step 1: 确认网络可用**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && claude -p "只回复两个字：可用"
```

Expected: `可用`。若挂住或报 `UNKNOWN_CERTIFICATE_VERIFICATION_ERROR`，本机代理链路不通，**不要开跑**——那会白花约 $12 且什么都验不到（这在子项目 A 的 Task 8 上真实发生过一次）。

- [ ] **Step 2: 跑一次真实的第一阶段**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m ir_agent 600183 --year 2025 --analyse out/600183-subB
```

Expected: 跑完（数分钟，约 $12）。终端产物清单里应出现「公司公告」相关条目。

- [ ] **Step 3: 核对公告入库情况**

```bash
python -c "
import json, pathlib
md = json.loads(pathlib.Path('out/600183-subB/state.json').read_text())['md']
print(md.get('公司公告', '(无此键)'))
print()
print('外部资料清单：')
for l in md.get('外部资料','').splitlines()[:8]: print(' ', l)
"
```

Expected: 打印出「公司公告 N 篇（候选 M 条，模型挑 K 条）」；「外部资料」清单里出现 `[公司披露] N 篇` 这一档，且总篇数等于各档之和。

- [ ] **Step 4: 核对 B2 的效果**

```bash
python -c "
import json, pathlib
md = json.loads(pathlib.Path('out/600183-subB/state.json').read_text())['md']
t = md.get('因子数据','')
print(t)
" | head -40
```

Expected: 打印因子数据表。核对三件事，**如实记录，不设通过线**：

1. 有几个因子的状态是「公告披露」——这是 ②.5 的直接实证
2. 有几个因子的说明里出现「未能在原页核实」——这是 B2 在真实网页上的
   拒绝率
3. 有几个因子的说明里出现「仅财经媒体支撑」——这是门槛放宽的实际效果

对照基线：2026-09-30 的实跑（`out/600183-ow4`）里 8 个因子是
「1 研报测算 + 7 无数据」，其中 6 个无数据其实搜到了但全为媒体级。

- [ ] **Step 5: 如实记录实跑结论**

把 Step 3 / Step 4 的实际输出贴进任务报告。**如实报告**：

- 若公告一篇都没入库，写清是候选为零、模型没挑、还是取文全失败
- 若 B2 的拒绝率很高（比如过半转述核实不过），那是一条**重要发现**而
  不是待藏起来的瑕疵——它说明模型转述与原页的偏离程度，而这正是 B2
  存在的理由
- 若放宽门槛之后因子数据仍然大面积「无数据」，也照实写：spec 第一节
  已经预告公告只能覆盖 3/8，剩下的属于 A 股确实不披露

**不要为了让这一步"通过"而回头调宽 `tier_of_url` 的白名单或放松核验**
——那会反过来侵蚀本子项目全部的设计前提。

- [ ] **Step 6: 清理实跑产物（保留结论）**

结论已记进报告后：

```bash
rm -rf out/600183-subB
```

若 Step 4 发现了需要进一步排查的问题，**先不要删**——那个目录是唯一的
证据。

---

## Self-Review

**1. Spec coverage**

| Spec 章节 | 对应任务 |
|---|---|
| 一、实跑推翻「扩大搜索面」（背景） | 无需实现；`announce.py` 模块 docstring 记录 |
| 一、公告覆盖面要如实说 | Task 8 Step 5 的验收口径 |
| 二、`Tier.COMPANY` 与 `TIER_RULE` | Task 1 |
| 二、唯一的安全支点（不进 `tier_of`） | Task 1 的两条不变式测试 |
| 二、`tier_of_url` 一行不动 | Task 1 第二条不变式测试 |
| 三、公告采集：模型挑下标、代码抓正文 | Task 2 + Task 3 |
| 三、时点守卫按披露日 | Task 3（含专门测试） |
| 三、规模（12 月 / 8 篇 / 截 2000 / 30 页） | Task 2 常量 + Task 3 |
| 四、`gaps` ②.5 与「公告披露」标签 | Task 4 |
| 五、B2 核验与三种处置 | Task 5 + Task 6 |
| 五、补掉 NEW-3（入库用抓回的全文） | Task 6 |
| 六、`supports_value` 放宽与耦合 | Task 6（一个提交，含「不得拆开」说明） |
| 六、有意保留的不一致（③ 仍跳 MEDIA） | 无改动即满足；Task 4 的 `_match_announce` 只看 COMPANY，不触碰 `_match_report` |
| 七、错误处理（七种情形） | Task 3（四种）+ Task 6（两种）+ Task 7（一种） |
| 八、测试清单 | Task 1–7 的测试步骤逐条覆盖 |
| 九、不做什么 | 计划内无对应任务，符合预期 |

一处需要补的：spec 第六节说「仅媒体支撑的因子在**批准清单**上标注」，而
Task 6 把标注写进 `FactorData.note`。批准清单（`assumptions.md`）的依据
来自 `assume._factor_basis` 读 `FactorData`，`note` 是它的展示文本——
标注确实会到达批准清单。Task 8 Step 4 第 3 项实跑核对这一条。

**2. Placeholder scan**

无 TBD / TODO / 「类似 Task N」/ 无代码的代码步骤。

写完后逐个核实了计划引用的既有符号确实存在且签名如所写：
`external.norm`、`web._OFFICIAL_CN`/`_SELLSIDE_ASCII`、`openweb.TEXT_LIMIT`
（= 2000）、`openweb.Fetched` 的字段、`annual_pdf._download`、
`gaps._valid_hit`/`_core_label`、`drivers.Decomposition`/`Factor`/`Op`。
初稿里对 `Decomposition` 签名留了一条「若不符怎么查」的对冲，核实后
签名完全一致，已删掉那条对冲——**为已经查清的事留对冲是噪音**，会让
实现者以为这里有不确定性。

一处值得实现者注意：`openweb.Fetched` 有第四个字段
`host_check_degraded`（子项目 A 的 SSRF 修复加的，有默认值）。Task 5 的
测试用关键字参数构造它，因此不受影响。

**3. Type consistency**

- `Tier.COMPANY`（Task 1）— Task 3 构造 `Document(tier=Tier.COMPANY)`、
  Task 4 `_match_announce` 过滤它、Task 6 `_TIER_ORDER` 含它，名字一致
- `AnnounceResult` 字段 `docs/candidates/picked/fetch_failed/fetch_failed_reasons/ignored`
  （Task 3）— Task 7 的 `_load_announce` 逐个使用，名字一致
- `Verified` 字段 `finding/ok/reason/text/final_url/tier`（Task 5）—
  Task 6 的 `gaps.collect` 与两条测试逐个使用，名字一致
- `verify_finding`（Task 5）— Task 6 在 `gaps.py` 里 import 并 monkeypatch
  同名属性，patch 点一致（`G.verify_finding`）
- `by_tier`（Task 6）— 在 `web.py` 定义、`gaps.py` import，一处定义
- `format_candidates` / `parse_picks` / `PROMPT` / `MAX_ANNOUNCEMENTS` /
  `PDF_MAX_PAGES` / `ANNOUNCE_MONTHS`（Task 2）— Task 3 逐个使用
- `TEXT_LIMIT` 从 `openweb` 导入（Task 3、Task 6），不重新定义
