# 开放网络文档入语料库 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让代码自己从开放网络抓回行业与公司资料的**全文**，按与券商研报同一条契约进 `Corpus`，使战略 / 产业链 / 宏观定性三节的外部依据不再只有券商研报一类来源。

**Architecture:** 新增 `ir_agent/sources/openweb.py`。模型只负责「去哪个网址」（`discover`），正文由代码用 `requests` 抓回并提取（`fetch`），来源分级按**代码跟完重定向后的最终域名**判定（`tier_of_url`），产物汇入既有的同一个 `Corpus`。因为 `discover` 要调模型，它**不能**放进 `_analyse_facts` 那一层（该层的不变量是「不调模型，因此两个阶段都要跑」）——第一阶段落盘、第二阶段读回，保证外部引文跨阶段存活。

**Tech Stack:** Python 3、`requests`（已是既有依赖）、`pytest`。设计文档：`docs/superpowers/specs/2026-09-20-openweb-corpus-design.md`。

## Global Constraints

- **不引入任何新的第三方依赖。** 不用 `bs4`、不用 `lxml`、不用 `readability`。正文提取用正则，与 `ir_agent/sources/reports.py` 的 `fetch_text` 同构。
- **模型不经手正文一个字。** `discover` 只产出网址等元数据；`Document.text` 必须来自代码自己发起的 HTTP 请求。
- **`ir_agent/sources/web.py` 的 `tier_of` 一行不动。** 它管的是模型自报的机构名字符串，是另一种输入。本计划新增的 `tier_of_url` 与它并存。
- **域名匹配必须锚在注册域边界上**：`host == 注册域` 或 `host.endswith("." + 注册域)`。绝不用裸子串、不用词边界正则、不用「媒体标识」黑名单。
- **`Document.published` 的类型保持 `date`，不得改成可空。**
- **判不出来落 `Tier.MEDIA` 是正确行为，不是缺陷。**
- **资料收集的任何失败都不得阻断整轮研究**，只记入产物清单（`skipped`）。
- 固定槽位常量，逐字照抄：`SLOTS = ("行业规模与增速", "政策与监管", "竞争格局与份额", "上下游价格与供需", "公司重大动态")`
- 规模上限，逐字照抄：每槽位最多 `PER_SLOT = 2` 篇，总量最多 `MAX_DOCS = 10` 篇，进提示词截断 `TEXT_LIMIT = 2000` 字。
- 测试函数名用中文，与 `tests/test_web_source.py`、`tests/test_gaps.py` 的既有风格一致。
- 每个任务结束时跑 `python -m pytest tests -q -m "not live"`，必须全绿再提交。

---

## File Structure

| 文件 | 职责 | 任务 |
|---|---|---|
| `ir_agent/sources/openweb.py`（新建） | 开放网络文档采集：分级、候选解析、抓取提取、编排、落盘读回 | 1–4、6 |
| `ir_agent/sources/external.py`（改） | `Document` 增加 `prompt_limit` / `date_source` 字段与 `for_prompt()` | 5 |
| `ir_agent/strategy.py`（改，1 行） | 提示词改用 `d.for_prompt()` | 5 |
| `ir_agent/value_chain.py`（改，1 行） | 同上 | 5 |
| `ir_agent/macro_view.py`（改，1 行） | 同上 | 5 |
| `ir_agent/sources/web.py`（改，2 行） | `_parse_date` 改名为公开的 `parse_date`，供 openweb 复用 | 2 |
| `ir_agent/full.py`（改） | 两个接入点：`run_analysis` 采集、`run_valuation` 读回 | 7 |
| `tests/test_openweb.py`（新建） | 全部新增行为的测试 | 1–4、6 |
| `tests/test_external.py`（改或新建） | `for_prompt()` 的测试 | 5 |
| `tests/test_full_context.py`（改） | 接入点的结构性断言 | 7 |

---

## Task 1: 按最终域名分级（`tier_of_url`）

**Files:**
- Create: `ir_agent/sources/openweb.py`
- Create: `tests/test_openweb.py`

**Interfaces:**
- Consumes: `ir_agent.sources.external.Tier`（既有枚举，成员为 `OFFICIAL` / `SELLSIDE` / `MEDIA`）
- Produces: `tier_of_url(url: str) -> Tier`；模块级常量 `_OFFICIAL_HOSTS: tuple[str, ...]`、`_SELLSIDE_HOSTS: tuple[str, ...]`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_openweb.py`：

```python
"""开放网络文档入语料库。**模型只给网址，正文由代码抓。**"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent.sources.external import Tier
from ir_agent.sources.openweb import tier_of_url

AS_OF = date(2026, 9, 20)


def test_gov_cn_后缀落OFFICIAL():
    assert tier_of_url("https://www.stats.gov.cn/sj/zxfb/x.html") is Tier.OFFICIAL
    assert tier_of_url("https://www.miit.gov.cn/a/b.html") is Tier.OFFICIAL


def test_白名单注册域及其子域落对应档():
    assert tier_of_url("https://www.semi.org/en/news/x") is Tier.OFFICIAL
    assert tier_of_url("https://semi.org/x") is Tier.OFFICIAL


def test_不认识的域名落MEDIA():
    assert tier_of_url("https://finance.example.com/x") is Tier.MEDIA
    assert tier_of_url("https://zhuanlan.zhihu.com/p/1") is Tier.MEDIA


def test_不变式_域名匹配必须锚在注册域边界上():
    """裸子串是本项目被打穿过三轮的那个洞在域名上的形态。

    `evil-semi.org.cn` 含子串 `semi.org`，`semi.org.cn.evil.com` 同样含；
    两者都必须落 MEDIA。这条不变式一旦破了，任何人注册一个含白名单
    字样的域名就能把自己拔成 OFFICIAL。
    """
    for bad in ("https://evil-semi.org/x",
                "https://evil-semi.org.cn/x",
                "https://semi.org.evil.com/x",
                "https://notgov.cn/x",
                "https://gov.cn.evil.com/x"):
        assert tier_of_url(bad) is Tier.MEDIA, bad


def test_不变式_白名单条目跨档互不为子域():
    """同一个主机名不能既命中 SELLSIDE 又命中 OFFICIAL——那种情况下
    结果取决于判别顺序，属于白名单数据本身的错误。

    **注意 `_SELLSIDE_HOSTS` 初始为空，此时这条检查的循环体不执行。**
    这是有意保留的守卫：它要防的是**将来往两张表里加条目时**撞车，而
    不是现在就有冲突。下面先断言两张表的形状（元素是非空小写注册域、
    不带协议与路径），保证这个测试在两张表都为空时也不是空跑。
    """
    from ir_agent.sources.openweb import _OFFICIAL_HOSTS, _SELLSIDE_HOSTS

    assert _OFFICIAL_HOSTS, "官方白名单不该为空"
    for entry in _OFFICIAL_HOSTS + _SELLSIDE_HOSTS:
        assert entry and entry == entry.lower().strip(), entry
        assert "/" not in entry and ":" not in entry, entry
        assert "." in entry, entry

    for a in _OFFICIAL_HOSTS:
        for b in _SELLSIDE_HOSTS:
            assert not (a == b or a.endswith("." + b) or b.endswith("." + a)), \
                f"{a} 与 {b} 跨档互为子域"


def test_畸形输入不炸():
    for bad in ("", "not a url", "http://", "://x", "ftp://x.gov.cn"):
        assert tier_of_url(bad) is Tier.MEDIA, bad
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: FAIL，`ModuleNotFoundError: No module named 'ir_agent.sources.openweb'`

- [ ] **Step 3: 写实现**

创建 `ir_agent/sources/openweb.py`：

```python
"""开放网络文档采集 —— 让券商研报之外的第三方资料也能被正文引用。

**为什么这个模块能往 Corpus 里放东西，而 web.py 的 Finding 不能。**

    Document.text    代码抓回来的全文    → Corpus.verify() 可逐字核验
    Finding.claim    模型转述的一句话    → 只有 URL 是模型给的，正文从未抓回

区别不是分级，是**证明力的种类**：一个能回答「这句话在不在那个网址上」，
另一个不能。本模块把模型限制在「去哪个网址」这一件事上，正文由代码自己
抓——与 reports.py 的 search() / fetch_text() 完全同构。不是放宽了契约，
是用同一条契约接了一个新来源。
"""

from __future__ import annotations

from urllib.parse import urlparse

from ir_agent.sources.external import Tier

# ── 来源分级 ────────────────────────────────────────────────────
#
# **这里的判别器与 web.py 的 tier_of 是两个东西，不要合并。**
#
# tier_of 吃的是**模型自报的机构名字符串**。正因为输入不可信，它被真实
# 来源名打穿过三轮（证券时报网 / 新浪证券 / Securities Daily / 中国 IDC
# 圈），最后只能退守到「白名单全称 + 修饰词」，并在函数注释里写死了禁止
# 再加任何模糊规则。
#
# 本模块的输入不是同一种东西：URL 是代码自己请求的，跟完重定向后的最终
# 域名也是代码观测到的。**模型编不了域名——编了代码就抓不到那个页面。**
# 于是这个输入允许一件字符串输入绝不允许的事：后缀匹配。`.gov.cn` 是受
# 管制注册后缀，stats.gov.cn / miit.gov.cn / csrc.gov.cn 天然全部命中，
# 一条规则顶一张白名单。
#
# 代价不对称与 tier_of 一致，且是这套分级安全的唯一理由：把媒体错拔成
# OFFICIAL，等于一篇博客单独支撑了正文里的一条事实，是**危害**；把真
# 官方错压成 MEDIA，只是需要再凑一条来源，是**召回损失**。因此判不出来
# 落 MEDIA 是正确行为，不是缺陷。

# 非 .gov.cn 的官方/协会/一手市场研究机构注册域。
#
# 为什么只有这么几个：**每一条都必须是逐条核实过的注册域**。写错一个
# 域名的后果落在「危害」那一侧（一个不相干的站点被拔成 OFFICIAL），
# 漏写一个只落在「召回损失」那一侧。要加新来源，加注册域，不要碰
# _matches / tier_of_url 的判别逻辑本身。
_OFFICIAL_HOSTS: tuple[str, ...] = (
    "semi.org",                 # SEMI 国际半导体产业协会
    "gartner.com",              # Gartner
    "idc.com",                  # IDC
    "trendforce.com",           # TrendForce（中文名集邦咨询）
    "counterpointresearch.com", # Counterpoint Research
    "yolegroup.com",            # Yole Group
    "omdia.com",                # Omdia
)

# 券商自有站点的注册域。
#
# **初始为空，这是有意的，不是没写完。** 券商研报本来就通过东财接口入库
# （reports.to_document 把 tier 硬编码成 SELLSIDE），开放网络上撞到券商
# 自有研究站点的概率很低；而中文券商的研究站域名核实成本高、猜错的代价
# 落在「危害」那一侧。留空意味着这类页面落 MEDIA——按本模块的代价不对称，
# 这是正确的默认值。要支持，逐条核实后往这里加注册域。
_SELLSIDE_HOSTS: tuple[str, ...] = ()


def _host_of(url: str) -> str:
    """取主机名，小写、去掉末尾的点（DNS 绝对域名写法）。取不到返回空串。"""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return ""
    return host.rstrip(".")


def _matches(host: str, registered: str) -> bool:
    """host 是否就是这个注册域，或它的子域。

    **只有这两种形态算命中，绝不做裸子串判断。** 裸子串正是 web.py 的
    tier_of 被打穿的第一种形态：`evil-semi.org` 含子串 `semi.org`，
    `semi.org.evil.com` 也含，两者都不是 SEMI。
    """
    return host == registered or host.endswith("." + registered)


def tier_of_url(url: str) -> Tier:
    """按**代码跟完重定向后的最终 URL** 分级。

    调用方必须传最终 URL，不是模型给的原始 URL：模型给一个 gov.cn 链接、
    实际 302 到博客，分级必须按博客走。
    """
    host = _host_of(url)
    if not host:
        return Tier.MEDIA
    if _matches(host, "gov.cn"):
        return Tier.OFFICIAL
    for entry in _SELLSIDE_HOSTS:
        if _matches(host, entry):
            return Tier.SELLSIDE
    for entry in _OFFICIAL_HOSTS:
        if _matches(host, entry):
            return Tier.OFFICIAL
    return Tier.MEDIA
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: PASS（7 passed）

- [ ] **Step 5: 逐条核实白名单域名**

对 `_OFFICIAL_HOSTS` 的每一条，用 WebFetch 访问 `https://<域名>/` 确认它确实是注释里写的那家机构的官方站点。**核实不了或指向别处的，从元组里删掉**（漏写只是召回损失，写错是危害）。删除后重跑 Step 4 确认测试仍通过（`test_白名单注册域及其子域落对应档` 用的是 `semi.org`，若该条被删则同步改用留下的任一条）。

- [ ] **Step 6: 跑全量测试并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过，无 failed

```bash
git add ir_agent/sources/openweb.py tests/test_openweb.py
git commit -m "openweb: 按代码观测到的最终域名分级，不碰 tier_of"
```

---

## Task 2: 候选行解析（`parse_candidates`）

**Files:**
- Modify: `ir_agent/sources/web.py`（`_parse_date` 改名为 `parse_date`，两处）
- Modify: `ir_agent/sources/openweb.py`
- Modify: `tests/test_openweb.py`

**Interfaces:**
- Consumes: `ir_agent.lineparse.keyed(name: str) -> re.Pattern[str]`（既有，匹配 `关键词: 其余内容` 并容忍 `- ` / `1. ` / `**加粗**` 等前缀）；`ir_agent.sources.web.parse_date(raw: str) -> date | None`（本任务改名得来）
- Produces: `SLOTS: tuple[str, ...]`；`@dataclass(frozen=True) Candidate(slot: str, title: str, url: str, publisher: str, published: date, extra: bool)`；`parse_candidates(text: str) -> list[Candidate]`；`PROMPT: str`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_openweb.py`：

```python
from ir_agent.sources.openweb import SLOTS, Candidate, parse_candidates

OUT = """候选: 行业规模与增速 | 2026年上半年全球覆铜板市场规模 | https://www.semi.org/a | SEMI | 2026-07-01
候选: 政策与监管 | 工信部印发电子材料产业行动方案 | https://www.miit.gov.cn/b | 工业和信息化部 | 2026-06-15
候选: 覆铜板批价跟踪 | 华东地区覆铜板报价周报 | https://finance.example.com/c | 某财经网 | 2026-08-01
"""


def test_解析出槽位标题网址机构日期():
    got = parse_candidates(OUT)
    assert len(got) == 3
    first = got[0]
    assert first.slot == "行业规模与增速"
    assert first.title == "2026年上半年全球覆铜板市场规模"
    assert first.url == "https://www.semi.org/a"
    assert first.publisher == "SEMI"
    assert first.published == date(2026, 7, 1)
    assert first.extra is False


def test_不在SLOTS里的槽位收下并标为额外():
    got = parse_candidates(OUT)
    extra = [c for c in got if c.extra]
    assert len(extra) == 1
    assert extra[0].slot == "覆铜板批价跟踪"


def test_少于五段整条丢弃():
    """与 parse_findings 同一条守卫：不存在「4 段也合法、日期可选」的分支。"""
    assert parse_candidates(
        "候选: 政策与监管 | 标题 | https://x.gov.cn/a | 某部委\n") == []


def test_没有网址整条丢弃():
    assert parse_candidates(
        "候选: 政策与监管 | 标题 | 见官网 | 某部委 | 2026-06-15\n") == []


def test_日期解析不出整条丢弃():
    """这是 Document.published 能保持非空 date 的前提——进到 fetch 的
    候选一定带着一个可解析的日期，任何路径上都不存在「日期不明」状态。"""
    for bad in ("2026年7月", "近期", ""):
        line = f"候选: 政策与监管 | 标题 | https://x.gov.cn/a | 某部委 | {bad}\n"
        assert parse_candidates(line) == [], bad


def test_容忍列表前缀与加粗():
    """lineparse.keyed 的既有能力，这里确认确实走的是它。"""
    got = parse_candidates(
        "1. **候选**: 政策与监管 | 标题 | https://x.gov.cn/a | 某部委 | 2026-06-15\n")
    assert len(got) == 1


def test_提示词逐字含全部固定槽位():
    from ir_agent.sources.openweb import PROMPT
    for slot in SLOTS:
        assert slot in PROMPT, slot
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: FAIL，`ImportError: cannot import name 'SLOTS'`

- [ ] **Step 3: 把 `web._parse_date` 改名为公开的 `parse_date`**

在 `ir_agent/sources/web.py` 中，把第 300 行的函数定义签名由

```python
def _parse_date(raw: str) -> date | None:
```

改为

```python
def parse_date(raw: str) -> date | None:
```

并把第 388 行的调用

```python
        when = _parse_date(parts[4])
```

改为

```python
        when = parse_date(parts[4])
```

改名的理由写进该函数的 docstring 开头（保留原有内容，在其后追加一段）：

```python
    **公开而非私有**：openweb.parse_candidates 要用同一套日期解析。两处
    对「日期解析不出就整条丢弃」的处置必须一致，抄一遍迟早会分叉。
```

- [ ] **Step 4: 写实现**

在 `ir_agent/sources/openweb.py` 顶部的 import 区补上（`re` 到本任务才第一次
被用到，Task 1 里没有它是对的）：

```python
import re
from dataclasses import dataclass
from datetime import date

from ir_agent.lineparse import keyed
from ir_agent.sources.web import parse_date
```

在 `tier_of_url` 之后追加：

```python
# ── 主题槽位 ────────────────────────────────────────────────────
#
# **固定槽位存在的唯一理由是让代码说得出缺了什么。** 这是本项目贯穿始终
# 的原则（research.py 模块注释、full._load_external 里关于一致预期为空的
# 那段长注释）：只列已完成的部分，读者会以为资料是全的。没有预期就没有
# 缺口——主题若全由模型自由发挥，代码永远报不出「这次没取到政策原文」。
#
# 代价是覆盖面被槽位限死，Candidate.extra 正是为这个代价留的出口：模型
# 可按行业特性追加槽位（白酒的批价、半导体的稼动率这类清单预先枚举不完），
# 追加的收下并标为额外，**但不参与缺口判定**——缺口口径必须固定，不能
# 随模型每轮的发挥浮动。
SLOTS: tuple[str, ...] = (
    "行业规模与增速",
    "政策与监管",
    "竞争格局与份额",
    "上下游价格与供需",
    "公司重大动态",
)

_LINE = keyed("候选")
_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Candidate:
    """模型给出的一个候选网址。**注意它不含正文** —— 正文由 fetch 抓。"""

    slot: str
    title: str
    url: str
    publisher: str
    published: date
    extra: bool          # 槽位不在 SLOTS 里 → 额外发现，不参与缺口判定


def parse_candidates(text: str) -> list[Candidate]:
    """解析 `候选:` 行。三道守卫与 web.parse_findings 逐条对齐。"""
    out: list[Candidate] = []
    for hit in _LINE.finditer(text):
        parts = [p.strip() for p in hit.group(1).split("|")]
        # 卡在 5：一行要完整给出 槽位|标题|网址|机构|日期 五段。少任何
        # 一段都不是「4 段也合法」，而是整条不合规。
        if len(parts) < 5:
            continue
        url_m = _URL.search(parts[2])
        if not url_m:
            # 没有网址就无从复核，与凭空捏造无异。
            continue
        when = parse_date(parts[4])
        if when is None:
            # **这道守卫是 Document.published 能保持非空 date 的前提。**
            # 放行「日期不明」的候选，下游迟早会写出 `published or as_of`
            # 那种兜底——而 as_of 恰好是唯一必然不触发 OutOfWindow 的取值，
            # 等于让最不可信的证据获得时点上最宽松的待遇。见 web.py 的
            # Finding.published 字段注释，那是同一条教训。
            continue
        slot = parts[0]
        out.append(Candidate(slot=slot, title=parts[1], url=url_m.group(0),
                             publisher=parts[3], published=when,
                             extra=slot not in SLOTS))
    return out


PROMPT = """为一份 A 股研究报告采集公司与行业的外部资料。

公司：{company}（{code}）
行业：{industry}

上网搜索，为下面每个主题各找**最多 {per_slot} 个**具体网页。
只给网址，不要摘抄正文——正文由我们自己抓取。

必找的主题：
- 行业规模与增速
- 政策与监管
- 竞争格局与份额
- 上下游价格与供需
- 公司重大动态

若这个行业还有别的关键外部资料（例如渠道批价、稼动率、库存周期），
可另起主题名追加。

每条一行：

`候选: <主题> | <网页标题> | <网址> | <发布机构> | <发布日期 YYYY-MM-DD>`

**硬性要求**
- **必须给出网址**。没有网址的条目会被直接丢弃 —— 无从复核的转述与凭空
  捏造无异。
- **必须给出发布日期（YYYY-MM-DD）**。没写日期、或只写到「2026年7月」
  这种月份粒度以致解析不出来的条目，同样会被直接丢弃。晚于 {as_of} 的
  会被剔除：拿未来的资料分析过去，与账本的时点守卫是同一件事。
- 优先统计局、部委、行业协会、一手市场研究机构的原始页面，其次财经媒体。
- 找不到的主题就不要输出该主题的行，**不要用推测或不相关的网页填充**。

只输出上述格式的行。
"""
```

- [ ] **Step 5: 跑测试确认通过**

```bash
python -m pytest tests/test_openweb.py tests/test_web_source.py -q
```

Expected: 全部通过（`test_web_source.py` 不得因改名而破——它没有引用 `_parse_date`）

- [ ] **Step 6: 跑全量测试并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过

```bash
git add ir_agent/sources/openweb.py ir_agent/sources/web.py tests/test_openweb.py
git commit -m "openweb: 候选行解析，三道守卫与 parse_findings 逐条对齐"
```

---

## Task 3: 抓取与正文提取（`fetch`）

**Files:**
- Modify: `ir_agent/sources/openweb.py`
- Modify: `tests/test_openweb.py`

**Interfaces:**
- Consumes: `requests`（既有依赖）；`ir_agent.sources.retry.with_retry(fn, attempts=3, base_delay=0.8)`（既有，只重试瞬时错误）；`ir_agent.sources.web.parse_date`
- Produces: `@dataclass(frozen=True) Fetched(final_url: str, text: str, meta_published: date | None)`；`fetch(url: str, *, timeout: int = TIMEOUT) -> Fetched`；`extract_text(html: str) -> str`；`meta_date(html: str) -> date | None`；常量 `TIMEOUT = 20`、`TEXT_LIMIT = 2000`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_openweb.py`：

```python
from ir_agent.sources.openweb import extract_text, meta_date

HTML = """<html><head>
<meta charset="utf-8">
<meta property="article:published_time" content="2026-07-01T08:30:00+08:00">
<title>覆铜板市场规模</title>
</head><body>
<nav><a href="/">首页</a><a href="/about">关于我们</a></nav>
<script>var ad = {track: 1};</script>
<style>.x{color:red}</style>
<div class="content"><p>2026年上半年全球覆铜板市场规模约 120 亿美元。</p>
<p>同比增长 8.4%。</p></div>
<footer>版权所有 &copy; 某站</footer>
</body></html>"""


def test_正文提取剔除脚本样式导航页脚():
    got = extract_text(HTML)
    assert "覆铜板市场规模约 120 亿美元" in got
    assert "同比增长 8.4%" in got
    for noise in ("var ad", "color:red", "关于我们", "版权所有"):
        assert noise not in got, noise


def test_正文提取还原实体并压掉空白():
    got = extract_text("<p>A&nbsp;&amp;&nbsp;B</p><p>  C  </p>")
    assert "A & B" in got
    assert "  C  " not in got
    assert "C" in got


def test_meta日期_属性在前():
    assert meta_date(HTML) == date(2026, 7, 1)


def test_meta日期_content在前也认():
    """meta 标签的属性顺序没有标准，两种写法都得认。"""
    html = ('<meta content="2026-06-15" '
            'property="article:published_time">')
    assert meta_date(html) == date(2026, 6, 15)


def test_meta日期_没有就返回None():
    assert meta_date("<html><body>x</body></html>") is None


def test_meta日期_认不出的写法返回None():
    html = '<meta property="article:published_time" content="近期">'
    assert meta_date(html) is None


def test_fetch_用最终URL并解码中文(monkeypatch):
    """两件事一起验：跟完重定向后的 final_url 必须是最终地址（分级要用
    它），以及中文页面的编码必须按页面声明解，不能留 ISO-8859-1。"""
    import ir_agent.sources.openweb as OW

    class _Resp:
        url = "https://blog.example.com/real"
        encoding = "ISO-8859-1"
        apparent_encoding = "utf-8"
        content = "<p>覆铜板报价上涨</p>".encode("utf-8")

        @property
        def text(self):
            return self.content.decode(self.encoding)

        def raise_for_status(self):
            return None

    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: _Resp())
    got = OW.fetch("https://www.miit.gov.cn/redirects-away")
    assert got.final_url == "https://blog.example.com/real"
    assert "覆铜板报价上涨" in got.text


def test_fetch_正文为空则抛(monkeypatch):
    import ir_agent.sources.openweb as OW

    class _Resp:
        url = "https://x.example.com/a"
        encoding = "utf-8"
        apparent_encoding = "utf-8"
        content = b"<html><body><script>1</script></body></html>"

        @property
        def text(self):
            return self.content.decode("utf-8")

        def raise_for_status(self):
            return None

    monkeypatch.setattr(OW.requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(OW.FetchFailed):
        OW.fetch("https://x.example.com/a")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: FAIL，`ImportError: cannot import name 'extract_text'`

- [ ] **Step 3: 写实现**

在 `ir_agent/sources/openweb.py` 的 import 区补上：

```python
import requests

from ir_agent.sources.retry import with_retry
```

在文件末尾追加：

```python
# ── 抓取与正文提取 ──────────────────────────────────────────────

TIMEOUT = 20
# 进提示词的截断长度。**入库存全文，进提示词才截断** —— Corpus.verify()
# 用的是全文，模型只能引用它看得见的那一段，而那一段必然在全文里，所以
# 截断不会让核验误杀。
TEXT_LIMIT = 2000

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}

# 整块剔除的噪声容器。研报走东财的 ctx-content 块，噪声天然被框住；
# 开放网页没有这个便利，导航、页脚、脚本、样式会被 _TAG 剥成一堆散字
# 混进正文，既污染引文核验也白白吃掉提示词长度。
_BLOCK = re.compile(
    r"<(script|style|nav|header|footer|aside|noscript)\b[^>]*>.*?</\1\s*>",
    re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t　\xa0]+")
_ENTITIES = (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
             ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"))

_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']')
# meta 里表示发布时间的键。属性顺序没有标准（content 可能写在 property
# 前面），所以逐个 meta 标签拆属性，而不是写一条把顺序焊死的正则。
_DATE_KEYS = ("article:published_time", "og:published_time",
              "article:published", "publishdate", "pubdate",
              "publish_date", "date")


class FetchFailed(Exception):
    """抓取或正文提取失败。**不静默返回空** —— 那会让「抓不到」和
    「抓到一篇空文章」长得一样。"""


@dataclass(frozen=True)
class Fetched:
    final_url: str              # 跟完重定向后的最终地址，分级用它
    text: str                   # 全文
    meta_published: date | None # 页面自己声明的发布时间，没有则 None


def extract_text(html: str) -> str:
    """HTML → 纯文本。与 reports.fetch_text 同构，额外整块剔除噪声容器。

    不引入 bs4/lxml：本仓库没有依赖声明文件，新增 import 意味着别人的
    环境可能跑不起来。
    """
    body = _BLOCK.sub(" ", html or "")
    body = _TAG.sub("\n", body)
    for src, dst in _ENTITIES:
        body = body.replace(src, dst)
    lines = [_SPACE.sub(" ", ln).strip() for ln in body.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def meta_date(html: str) -> date | None:
    """从 meta 标签里读页面自己声明的发布时间；读不到返回 None。"""
    for tag in _META_TAG.finditer(html or ""):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag.group(0))}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key not in _DATE_KEYS:
            continue
        when = parse_date(attrs.get("content", ""))
        if when is not None:
            return when
    return None


def fetch(url: str, *, timeout: int = TIMEOUT) -> Fetched:
    """代码自己把页面抓回来。**模型不经手正文一个字。**"""
    def _get():
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r

    try:
        resp = with_retry(_get)
    except Exception as e:                                  # noqa: BLE001
        raise FetchFailed(f"{url}：{e.__class__.__name__}: {e}") from e

    # requests 对没有显式 charset 的页面会猜成 ISO-8859-1，中文页面因此
    # 解出一片乱码——乱码不会报错，只会让正文提取和引文核验静默失效。
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    html = resp.text

    text = extract_text(html)
    if not text.strip():
        raise FetchFailed(f"{url}：正文提取为空")
    return Fetched(final_url=resp.url or url, text=text,
                   meta_published=meta_date(html))
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: PASS

- [ ] **Step 5: 跑全量测试并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过

```bash
git add ir_agent/sources/openweb.py tests/test_openweb.py
git commit -m "openweb: 代码自己抓正文，剔噪声容器、按页面声明解码"
```

---

## Task 4: 采集编排（`gather`）

**Files:**
- Modify: `ir_agent/sources/openweb.py`
- Modify: `tests/test_openweb.py`

**Interfaces:**
- Consumes: `ir_agent.sources.external.Corpus`（既有，方法 `add_if_visible(doc) -> bool`、属性 `docs: list[Document]`、`as_of: date`）；`ir_agent.sources.external.Document`（既有 frozen dataclass，字段顺序 `doc_id, url, title, publisher, tier, published, retrieved, text`）；本模块的 `PROMPT` / `parse_candidates` / `fetch` / `tier_of_url` / `SLOTS` / `TEXT_LIMIT`
- Produces: `@dataclass OpenWebResult(docs, missing_slots, fetch_failed, extra_slots, model_dated)`；`gather(client, *, code, company, industry, as_of, corpus) -> OpenWebResult`；`summary(result) -> str`；常量 `PER_SLOT = 2`、`MAX_DOCS = 10`

> **注意：** `Document` 在本任务里还没有 `prompt_limit` / `date_source` 两个字段，它们由 Task 5 与 Task 6 加。本任务构造 `Document` 时只传上面列出的 8 个既有字段。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_openweb.py`：

```python
from ir_agent.sources.external import Corpus
from ir_agent.sources.openweb import Fetched, OpenWebResult, gather, summary


class _Client:
    """与 tests/test_web_source.py 的 _Client 同一形状：文本进、文本出。"""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _fake_fetch(pages):
    """pages: {请求URL: Fetched}。缺的抛 FetchFailed。"""
    import ir_agent.sources.openweb as OW

    def _f(url, *, timeout=OW.TIMEOUT):
        if url not in pages:
            raise OW.FetchFailed(f"{url}：404")
        return pages[url]
    return _f


FOUR = """候选: 行业规模与增速 | 规模 | https://www.semi.org/a | SEMI | 2026-07-01
候选: 政策与监管 | 政策 | https://www.miit.gov.cn/b | 工信部 | 2026-06-15
候选: 竞争格局与份额 | 格局 | https://finance.example.com/c | 某财经网 | 2026-08-01
候选: 覆铜板批价 | 批价 | https://price.example.com/d | 某网 | 2026-08-20
"""

PAGES = {
    "https://www.semi.org/a": Fetched("https://www.semi.org/a", "规模约120亿美元", date(2026, 7, 1)),
    "https://www.miit.gov.cn/b": Fetched("https://www.miit.gov.cn/b", "印发行动方案", None),
    "https://finance.example.com/c": Fetched("https://finance.example.com/c", "前三大份额", None),
    "https://price.example.com/d": Fetched("https://price.example.com/d", "报价上涨", None),
}


def test_抓到的文档入语料库并按最终域名分级(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 4
    by_url = {d.url: d for d in cp.docs}
    assert by_url["https://www.semi.org/a"].tier is Tier.OFFICIAL
    assert by_url["https://www.miit.gov.cn/b"].tier is Tier.OFFICIAL
    assert by_url["https://finance.example.com/c"].tier is Tier.MEDIA
    assert len(res.docs) == 4


def test_未取到的固定槽位被报成缺口(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert set(res.missing_slots) == {"上下游价格与供需", "公司重大动态"}


def test_额外槽位不参与缺口判定(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert res.extra_slots == ["覆铜板批价"]
    assert "覆铜板批价" not in res.missing_slots


def test_重定向后按最终域名分级(monkeypatch):
    """模型给 gov.cn 链接、实际 302 到博客——必须按博客落 MEDIA。"""
    import ir_agent.sources.openweb as OW
    pages = {"https://www.miit.gov.cn/b":
             Fetched("https://blog.example.com/real", "正文", None)}
    monkeypatch.setattr(OW, "fetch", _fake_fetch(pages))
    cp = Corpus(as_of=AS_OF)
    gather(_Client("候选: 政策与监管 | 政策 | https://www.miit.gov.cn/b | 工信部 | 2026-06-15\n"),
           code="600183", company="生益科技", industry="电子/元件",
           as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 1
    assert cp.docs[0].tier is Tier.MEDIA
    assert cp.docs[0].url == "https://blog.example.com/real"


def test_抓取失败不牵连其余(monkeypatch):
    import ir_agent.sources.openweb as OW
    partial = {k: v for k, v in PAGES.items() if "semi.org" not in k}
    monkeypatch.setattr(OW, "fetch", _fake_fetch(partial))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 3
    assert res.fetch_failed == 1


def test_晚于as_of的候选被挡下(monkeypatch):
    import ir_agent.sources.openweb as OW
    late = "候选: 政策与监管 | 政策 | https://www.miit.gov.cn/b | 工信部 | 2026-12-31\n"
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    gather(_Client(late), code="600183", company="生益科技",
           industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert cp.docs == []
    assert cp.skipped != []


def test_每槽位最多两篇(monkeypatch):
    import ir_agent.sources.openweb as OW
    three = "".join(
        f"候选: 政策与监管 | 政策{i} | https://www.miit.gov.cn/{i} | 工信部 | 2026-06-15\n"
        for i in range(3))
    pages = {f"https://www.miit.gov.cn/{i}":
             Fetched(f"https://www.miit.gov.cn/{i}", f"正文{i}", None)
             for i in range(3)}
    monkeypatch.setattr(OW, "fetch", _fake_fetch(pages))
    cp = Corpus(as_of=AS_OF)
    gather(_Client(three), code="600183", company="生益科技",
           industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert len(cp.docs) == 2


def test_已在语料库里的URL不重复入库(monkeypatch):
    import ir_agent.sources.openweb as OW
    from ir_agent.sources.external import Document
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    cp.add(Document(doc_id="x", url="https://www.semi.org/a", title="已有",
                    publisher="SEMI", tier=Tier.SELLSIDE,
                    published=date(2026, 5, 1), retrieved=AS_OF, text="已有正文"))
    gather(_Client(FOUR), code="600183", company="生益科技",
           industry="电子/元件", as_of=AS_OF, corpus=cp)
    assert [d.url for d in cp.docs].count("https://www.semi.org/a") == 1


def test_日期来源统计_meta优先于模型(monkeypatch):
    """用户定：信模型报的日期；折中是代码能提到就以代码为准，并把
    「有几篇的日期未经代码核验」说出来。"""
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    # PAGES 里只有 semi.org 那篇带 meta 日期，其余三篇用模型报的
    assert res.model_dated == 3


def test_summary_逐项说出缺口与失败(monkeypatch):
    import ir_agent.sources.openweb as OW
    partial = {k: v for k, v in PAGES.items() if "semi.org" not in k}
    monkeypatch.setattr(OW, "fetch", _fake_fetch(partial))
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    text = summary(res)
    assert "上下游价格与供需：未取到" in text
    assert "公司重大动态：未取到" in text
    assert "1 篇抓取失败" in text
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: FAIL，`ImportError: cannot import name 'gather'`

- [ ] **Step 3: 写实现**

在 `ir_agent/sources/openweb.py` 的 import 区补上：

```python
import hashlib

from dataclasses import dataclass, field

from ir_agent.sources.external import Document, Tier
```

（把已有的 `from dataclasses import dataclass` 与 `from ir_agent.sources.external import Tier` 两行替换成上面两行。）

在文件末尾追加：

```python
# ── 编排 ────────────────────────────────────────────────────────

PER_SLOT = 2
MAX_DOCS = 10


@dataclass
class OpenWebResult:
    docs: list[Document] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    extra_slots: list[str] = field(default_factory=list)
    fetch_failed: int = 0
    # 发布日期由模型给出、未经代码核验的篇数。**必须能说出来** ——
    # 用户决定信模型报的日期，折中是把不可核验的那部分显式标出来，
    # 而不是让它隐身在一堆看起来一样的引用里。
    model_dated: int = 0


def _doc_id(final_url: str) -> str:
    return "ow_" + hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:12]


def gather(client, *, code: str, company: str, industry: str,
           as_of: date, corpus) -> OpenWebResult:
    """模型给网址 → 代码抓正文 → 入语料库。

    **调用方必须传入已显式开启 WebSearch/WebFetch 工具的 client**，
    例如 `make_client(tools=["WebSearch", "WebFetch"])`。不开不会报错，
    只会安静地什么都搜不到 —— 与 web.search 同一条前提。
    """
    res = OpenWebResult()
    raw = client(PROMPT.format(company=company or code, code=code,
                               industry=industry or "未知",
                               per_slot=PER_SLOT, as_of=as_of.isoformat()))
    seen = {d.url for d in corpus.docs}
    per_slot: dict[str, int] = {}

    for cand in parse_candidates(raw):
        if len(res.docs) >= MAX_DOCS:
            break
        if cand.published > as_of:
            # 时点守卫的第一道：候选阶段就挡，省一次网络请求。
            # 第二道在 corpus.add_if_visible 里（按最终文档再滤一遍）。
            corpus.skipped.append(f"{cand.title}（{cand.published}）")
            continue
        if per_slot.get(cand.slot, 0) >= PER_SLOT:
            continue
        if cand.url in seen:
            continue
        try:
            got = fetch(cand.url)
        except FetchFailed:
            res.fetch_failed += 1
            continue
        if got.final_url in seen:
            # 重定向之后才发现撞上已有文档 —— 抓都抓了，但不重复入库。
            continue

        # 日期：代码能从 meta 提到就**以代码为准**，提不到才用模型报的。
        # 这不增加丢弃、不损召回，只是让不可核验的那部分可见。
        published = got.meta_published or cand.published
        if got.meta_published is None:
            res.model_dated += 1

        doc = Document(
            doc_id=_doc_id(got.final_url),
            url=got.final_url,
            title=cand.title,
            publisher=cand.publisher,
            # **按代码跟完重定向后的最终 URL 分级**，不是模型给的那个。
            tier=tier_of_url(got.final_url),
            published=published,
            retrieved=as_of,
            text=got.text,
        )
        if not corpus.add_if_visible(doc):
            continue
        seen.add(doc.url)
        per_slot[cand.slot] = per_slot.get(cand.slot, 0) + 1
        res.docs.append(doc)
        if cand.extra and cand.slot not in res.extra_slots:
            res.extra_slots.append(cand.slot)

    # 缺口只按固定槽位判定 —— 额外槽位是模型的自由发挥，不能进缺口口径。
    res.missing_slots = [s for s in SLOTS if not per_slot.get(s)]
    return res


def summary(res: OpenWebResult) -> str:
    """给人看的采集结果。**缺什么必须说出来** —— 只列取到的部分，
    读者会以为资料是全的。"""
    lines = [f"开放网络资料 {len(res.docs)} 篇"]
    if res.extra_slots:
        lines.append("  · 额外主题：" + "、".join(res.extra_slots))
    if res.model_dated:
        lines.append(f"  ⚠ 其中 {res.model_dated} 篇的发布日期由模型给出，"
                     "未经代码核验")
    if res.fetch_failed:
        lines.append(f"  – {res.fetch_failed} 篇抓取失败")
    for slot in res.missing_slots:
        lines.append(f"  – {slot}：未取到")
    return "\n".join(lines)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: PASS

- [ ] **Step 5: 跑全量测试并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过

```bash
git add ir_agent/sources/openweb.py tests/test_openweb.py
git commit -m "openweb: 采集编排，缺口只按固定槽位判定"
```

---

## Task 5: 文档进提示词时截断（`Document.for_prompt`）

**Files:**
- Modify: `ir_agent/sources/external.py`（`Document` 类）
- Modify: `ir_agent/strategy.py:246`
- Modify: `ir_agent/value_chain.py:309`
- Modify: `ir_agent/macro_view.py:226`
- Modify: `ir_agent/sources/openweb.py`（构造 `Document` 时传 `prompt_limit`）
- Create: `tests/test_external_prompt.py`

**Interfaces:**
- Consumes: `ir_agent.sources.external.Document`（既有 frozen dataclass）
- Produces: `Document` 新增字段 `prompt_limit: int | None = None`；新增方法 `for_prompt(self) -> str`

**为什么不直接把三处调用点改成 `d.head(2000)`：** 那会把**券商研报**也一起截断，是一次没人要求过的行为变更，可能让现有分析质量退化。截断的需求只来自开放网页（可能几万字，且同一批文档会同时压到三个模块的提示词上），所以限制跟着文档走，而不是跟着调用点走。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_external_prompt.py`：

```python
"""文档进提示词时的截断。**入库存全文，进提示词才截断。**"""

from __future__ import annotations

from datetime import date

from ir_agent.sources.external import Corpus, Document, Tier

AS_OF = date(2026, 9, 20)


def _doc(text: str, limit: int | None = None) -> Document:
    return Document(doc_id="d1", url="https://x.example.com/a", title="标题",
                    publisher="某机构", tier=Tier.MEDIA,
                    published=date(2026, 6, 1), retrieved=AS_OF,
                    text=text, prompt_limit=limit)


def test_没有上限时原样返回():
    assert _doc("一二三四五").for_prompt() == "一二三四五"


def test_未超上限时原样返回():
    assert _doc("一二三四五", 10).for_prompt() == "一二三四五"


def test_超上限时截断并标注():
    got = _doc("一二三四五六七八九十", 5).for_prompt()
    assert got.startswith("一二三四五")
    assert "截取" in got
    assert "六七八九十" not in got


def test_截断不影响verify_核验用的是全文():
    """模型只能引用它看得见的那一段，而那一段必然在全文里 ——
    所以截断不会让引文核验误杀。"""
    cp = Corpus(as_of=AS_OF)
    cp.add(_doc("一二三四五六七八九十", 5))
    assert cp.verify("一二三四五") == ""
    # 全文里有、但模型看不见的那一段，核验一样通过（核验的是全文）
    assert cp.verify("六七八九十") == ""
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_external_prompt.py -q
```

Expected: FAIL，`TypeError: Document.__init__() got an unexpected keyword argument 'prompt_limit'`

- [ ] **Step 3: 给 `Document` 加字段与方法**

在 `ir_agent/sources/external.py` 中，把 `Document` 类改为：

```python
_TRUNCATED = "\n……（正文过长，以上为截取部分；引用请限于以上内容）"


@dataclass(frozen=True)
class Document:
    doc_id: str
    url: str
    title: str
    publisher: str
    tier: Tier
    published: date
    retrieved: date
    text: str
    # 进提示词时的截断长度。None = 不截断（研报走东财的 ctx-content 块，
    # 长度天然可控）。开放网页可能几万字，且同一批文档会同时压到战略、
    # 产业链、宏观定性三个模块的提示词上 —— 撑爆是三倍的。
    #
    # **限制跟着文档走，不跟着调用点走。** 在调用点上一刀切会把研报也
    # 截了，那是一次没人要求过的行为变更。
    prompt_limit: int | None = None

    def cite(self) -> str:
        return (f"{self.publisher}《{self.title}》"
                f"（{self.published}，{TIER_LABEL[self.tier]}）")

    def head(self, n: int = 600) -> str:
        t = self.text.strip()
        return t[:n] + ("……" if len(t) > n else "")

    def for_prompt(self) -> str:
        """进提示词的正文。**入库存全文，这里才截断。**

        Corpus.verify() 用的是 self.text（全文），模型只能引用它在提示词
        里看得见的那一段，而那一段必然是全文的前缀 —— 所以截断不会让引文
        核验误杀。截断标记要写出来，否则模型不知道后面还有内容，可能凭
        印象去补。
        """
        if self.prompt_limit is None or len(self.text) <= self.prompt_limit:
            return self.text
        return self.text[:self.prompt_limit] + _TRUNCATED
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_external_prompt.py -q
```

Expected: PASS（4 passed）

- [ ] **Step 5: 三处调用点改用 `for_prompt()`**

`ir_agent/strategy.py` 第 246 行，把

```python
            lines.append("  " + d.text.replace("\n", "\n  "))
```

改为

```python
            lines.append("  " + d.for_prompt().replace("\n", "\n  "))
```

`ir_agent/value_chain.py` 第 309 行，把

```python
            lines.append("  " + d.text.replace("\n", "\n  "))
```

改为

```python
            lines.append("  " + d.for_prompt().replace("\n", "\n  "))
```

`ir_agent/macro_view.py` 第 226 行，把

```python
            lines.append("  " + d.text.replace("\n", "\n  "))
```

改为

```python
            lines.append("  " + d.for_prompt().replace("\n", "\n  "))
```

- [ ] **Step 6: openweb 构造 Document 时带上截断长度**

在 `ir_agent/sources/openweb.py` 的 `gather` 里，`Document(...)` 的构造补一个参数（加在 `text=got.text,` 之后）：

```python
            text=got.text,
            prompt_limit=TEXT_LIMIT,
```

- [ ] **Step 7: 跑全量测试并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过

```bash
git add ir_agent/sources/external.py ir_agent/strategy.py \
        ir_agent/value_chain.py ir_agent/macro_view.py \
        ir_agent/sources/openweb.py tests/test_external_prompt.py
git commit -m "external: 文档自带进提示词的截断长度，研报行为不变"
```

---

## Task 6: 落盘与读回（`save` / `load`）

**Files:**
- Modify: `ir_agent/sources/external.py`（`Document` 增加 `date_source` 字段）
- Modify: `ir_agent/sources/openweb.py`
- Modify: `tests/test_openweb.py`

**Interfaces:**
- Consumes: 本模块的 `OpenWebResult`；`ir_agent.sources.external.Document` / `Corpus` / `Tier`
- Produces: `Document` 新增字段 `date_source: str = ""`（取值 `""` / `"meta"` / `"model"`）；`OPENWEB_JSON = "openweb.json"`；`save(res: OpenWebResult, path) -> None`；`load(path, corpus) -> OpenWebResult`

**为什么必须落盘：** `discover` 调模型，结果**重算不出来**，而 `_analyse_facts` 那一层两个阶段都跑。若第二阶段重跑 `discover`，取到的文档会与第一阶段不同，第一阶段正文里写下的外部引文因此在第二阶段核验不过——本项目已经吃过这类亏（判断项的依据没跨阶段存活，第二阶段被自己的闸门拒掉）。存全文而不只存网址，是因为照网址重抓时页面改一个字引文核验就挂。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_openweb.py`：

```python
def test_落盘读回后文档逐字还原(tmp_path, monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    res = gather(_Client(FOUR), code="600183", company="生益科技",
                 industry="电子/元件", as_of=AS_OF, corpus=cp)
    path = tmp_path / OW.OPENWEB_JSON
    OW.save(res, path)

    back_cp = Corpus(as_of=AS_OF)
    back = OW.load(path, back_cp)
    assert len(back_cp.docs) == len(cp.docs)
    for a, b in zip(cp.docs, back_cp.docs):
        assert (a.doc_id, a.url, a.title, a.publisher) == \
               (b.doc_id, b.url, b.title, b.publisher)
        assert a.tier is b.tier
        assert (a.published, a.retrieved, a.text) == \
               (b.published, b.retrieved, b.text)
        assert a.prompt_limit == b.prompt_limit
        assert a.date_source == b.date_source
    assert back.missing_slots == res.missing_slots
    assert back.extra_slots == res.extra_slots
    assert back.fetch_failed == res.fetch_failed
    assert back.model_dated == res.model_dated


def test_日期来源逐篇记录(monkeypatch):
    import ir_agent.sources.openweb as OW
    monkeypatch.setattr(OW, "fetch", _fake_fetch(PAGES))
    cp = Corpus(as_of=AS_OF)
    gather(_Client(FOUR), code="600183", company="生益科技",
           industry="电子/元件", as_of=AS_OF, corpus=cp)
    by_url = {d.url: d for d in cp.docs}
    assert by_url["https://www.semi.org/a"].date_source == "meta"
    assert by_url["https://www.miit.gov.cn/b"].date_source == "model"


def test_读回时晚于as_of的文档仍被挡下(tmp_path):
    """时点守卫不能因为「这是我们自己存的」就放行 —— as_of 可能变了。"""
    import json

    import ir_agent.sources.openweb as OW
    path = tmp_path / OW.OPENWEB_JSON
    path.write_text(json.dumps({
        "docs": [{"doc_id": "ow_x", "url": "https://x.gov.cn/a",
                  "title": "标题", "publisher": "某部委", "tier": "official",
                  "published": "2026-12-31", "retrieved": "2026-09-20",
                  "text": "正文", "prompt_limit": 2000, "date_source": "model"}],
        "missing_slots": [], "extra_slots": [], "fetch_failed": 0,
        "model_dated": 1}, ensure_ascii=False), encoding="utf-8")
    cp = Corpus(as_of=AS_OF)
    OW.load(path, cp)
    assert cp.docs == []
    assert cp.skipped != []


def test_文件不存在时返回空结果而不抛(tmp_path):
    import ir_agent.sources.openweb as OW
    cp = Corpus(as_of=AS_OF)
    back = OW.load(tmp_path / "nope.json", cp)
    assert back.docs == []
    assert cp.docs == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_openweb.py -q
```

Expected: FAIL，`AttributeError: module 'ir_agent.sources.openweb' has no attribute 'OPENWEB_JSON'`

- [ ] **Step 3: 给 `Document` 加 `date_source`**

在 `ir_agent/sources/external.py` 的 `Document` 里，`prompt_limit` 字段之后追加：

```python
    # 发布日期的来源：""=不适用（接口给出，如东财研报）/ "meta"=代码从
    # 页面 meta 标签提取 / "model"=模型给出，未经代码核验。
    #
    # 之所以要逐篇记下来而不只统计个数：第二阶段是从落盘文件读回的，
    # 那时已经没有「当时是怎么拿到这个日期的」这个上下文了。能重算的
    # 不该存，而这个恰恰重算不出来。
    date_source: str = ""
```

- [ ] **Step 4: 写落盘读回**

在 `ir_agent/sources/openweb.py` 的 import 区补上：

```python
import json
from pathlib import Path
```

在 `gather` 的 `Document(...)` 构造里，`prompt_limit=TEXT_LIMIT,` 之后追加一行：

```python
            date_source="meta" if got.meta_published is not None else "model",
```

在文件末尾追加：

```python
# ── 跨阶段存活 ──────────────────────────────────────────────────
#
# **discover 调模型，而 _analyse_facts 那一层两个阶段都跑。** 若第二阶段
# 重跑一次 discover，取到的文档会与第一阶段不同，第一阶段正文里写下的
# 外部引文因此在第二阶段核验不过 —— 本项目已经吃过这类亏（判断项的依据
# 没跨阶段存活，第二阶段被自己的闸门拒掉）。
#
# 「能重算的就不要存」在这里反向成立：discover 的结果**重算不出来**，
# 正是该存的那一类。存全文而不只存网址，是因为照网址重抓时页面改一个字
# 引文核验就挂。

OPENWEB_JSON = "openweb.json"


def _doc_to_json(d: Document) -> dict:
    return {"doc_id": d.doc_id, "url": d.url, "title": d.title,
            "publisher": d.publisher, "tier": d.tier.value,
            "published": d.published.isoformat(),
            "retrieved": d.retrieved.isoformat(), "text": d.text,
            "prompt_limit": d.prompt_limit, "date_source": d.date_source}


def _doc_from_json(raw: dict) -> Document:
    return Document(doc_id=raw["doc_id"], url=raw["url"], title=raw["title"],
                    publisher=raw["publisher"], tier=Tier(raw["tier"]),
                    published=date.fromisoformat(raw["published"]),
                    retrieved=date.fromisoformat(raw["retrieved"]),
                    text=raw["text"], prompt_limit=raw.get("prompt_limit"),
                    date_source=raw.get("date_source", ""))


def save(res: OpenWebResult, path: Path | str) -> None:
    Path(path).write_text(json.dumps({
        "docs": [_doc_to_json(d) for d in res.docs],
        "missing_slots": res.missing_slots,
        "extra_slots": res.extra_slots,
        "fetch_failed": res.fetch_failed,
        "model_dated": res.model_dated,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path: Path | str, corpus) -> OpenWebResult:
    """读回落盘的文档并入语料库。文件不存在或损坏时返回空结果，不抛。

    **时点守卫照样要过一遍**（走 corpus.add_if_visible）：as_of 可能与
    落盘时不同，「这是我们自己存的」不构成放行理由。
    """
    p = Path(path)
    if not p.exists():
        return OpenWebResult()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return OpenWebResult()

    res = OpenWebResult(
        missing_slots=list(raw.get("missing_slots", [])),
        extra_slots=list(raw.get("extra_slots", [])),
        fetch_failed=int(raw.get("fetch_failed", 0)),
        model_dated=int(raw.get("model_dated", 0)))
    seen = {d.url for d in corpus.docs}
    for item in raw.get("docs", []):
        try:
            doc = _doc_from_json(item)
        except Exception:                                   # noqa: BLE001
            continue
        if doc.url in seen or not corpus.add_if_visible(doc):
            continue
        seen.add(doc.url)
        res.docs.append(doc)
    return res
```

- [ ] **Step 5: 跑测试确认通过**

```bash
python -m pytest tests/test_openweb.py tests/test_external_prompt.py -q
```

Expected: PASS

- [ ] **Step 6: 跑全量测试并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过

```bash
git add ir_agent/sources/external.py ir_agent/sources/openweb.py tests/test_openweb.py
git commit -m "openweb: 第一阶段落盘、第二阶段读回，外部引文跨阶段存活"
```

---

## Task 7: 接入 `full.py`

**Files:**
- Modify: `ir_agent/full.py`（新增 `_company_name` / `_load_openweb` / `_restore_openweb`；`run_analysis` 与 `run_valuation` 各一个调用点）
- Modify: `tests/test_full_context.py`

**Interfaces:**
- Consumes: `ir_agent.sources.openweb` 的 `OPENWEB_JSON` / `gather` / `load` / `summary`；`ir_agent.sources.taxonomy.fetch_all()`（既有，返回带 `.code` / `.name` / `.path` 的 `Entry` 列表）；`full.py` 既有的 `research_client`（已开 `WebSearch` / `WebFetch`）、`meth.industry`、`r.corpus`、`out: Path`
- Produces: `_load_openweb(code, r, as_of, out, client, industry) -> list[str]`；`_restore_openweb(r, out) -> list[str]`

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_full_context.py`：

```python
def test_开放网络采集不在确定性层里():
    """`_analyse_facts` 的不变量是「不调模型，因此两个阶段都要跑」。
    discover 要调模型 —— 放进去会让第二阶段取到与第一阶段不同的文档，
    第一阶段写下的外部引文因此在第二阶段核验不过。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full._analyse_facts)
    assert "openweb" not in src
    assert "_load_openweb" not in src


def test_第一阶段采集排在定性三节之前():
    """战略/产业链/宏观定性要引用开放网络文档的原文，采集必须更早。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_analysis)
    assert src.index("_load_openweb") < src.index("_analyse_qualitative")
    assert src.index("_analyse_facts") < src.index("_load_openweb")


def test_第二阶段只读回不重新采集():
    """第二阶段不得引入第一阶段没有的文档。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_valuation)
    assert "_restore_openweb" in src
    assert "_load_openweb" not in src
    assert src.index("_analyse_facts") < src.index("_restore_openweb")


def test_读回排在完整研报撰写之前():
    """_build_evidence 用 r.corpus 做引文核验，读回必须更早。"""
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_valuation)
    assert src.index("_restore_openweb") < src.index("_write_full_report")


def test_采集用的是开了联网工具的client():
    """不开工具不会报错，只会安静地什么都搜不到 —— 比直接失败更难发现。

    采集必须复用 methodology/gaps 那个已显式开启 WebSearch/WebFetch 的
    `research_client`，不能用默认不开工具的 `qual_client`。
    """
    import inspect

    from ir_agent import full
    src = inspect.getsource(full.run_analysis)
    call = src[src.index("_load_openweb("):]
    call = call[:call.index(")")]
    assert "research_client" in call
    assert "qual_client" not in call
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_full_context.py -q
```

Expected: FAIL，`AttributeError: module 'ir_agent.full' has no attribute '_load_openweb'`

- [ ] **Step 3: 在 `full.py` 里加三个函数**

在 `ir_agent/full.py` 中，紧跟在 `_load_external` 函数之后（即 `def _load_sections(r):` 之前）插入：

```python
def _company_name(code: str) -> str:
    """公司简称。取不到就退回代码 —— 搜索提示词里写代码也能用。"""
    try:
        from ir_agent.sources.taxonomy import fetch_all
        rows = fetch_all()
    except Exception:                                       # noqa: BLE001
        return code
    me = next((e for e in rows if e.code == code), None)
    return me.name if me is not None else code


def _load_openweb(code: str, r, as_of: date, out: Path, client,
                  industry: str) -> list[str]:
    """开放网络文档入语料库。返回要记进产物清单的条目。

    **这里不能放进 `_analyse_facts`。** 那一层的不变量写在它自己的
    docstring 上：「不调模型，因此两个阶段都要跑」。而本函数要调模型，
    放进去有两个后果，第二个是致命的：

      1. 成本翻倍 —— 两个阶段各跑一次
      2. 第二阶段可能取到**与第一阶段不同的文档**，于是第一阶段正文里
         写下的外部引文，在第二阶段核验不过

    第 2 条是本项目已经吃过的那类亏。根因同一个：**一个不可复现的产物
    被放在了会被重跑的位置上。** corpus.gather 能待在确定性层，是因为
    东财接口同 as_of 返回同一批研报；discover 没有这个性质，网页本身
    也会变。因此第一阶段落盘、第二阶段读回（见 `_restore_openweb`）。
    """
    from ir_agent.sources import openweb
    from ir_agent.sources.external import Corpus

    if r.corpus is None:
        r.corpus = Corpus(as_of=as_of)
    try:
        res = openweb.gather(client, code=code, company=_company_name(code),
                             industry=industry, as_of=as_of, corpus=r.corpus)
    except Exception as e:                                  # noqa: BLE001
        return [f"开放网络采集未执行（{e.__class__.__name__}: {str(e)[:120]}）"]

    openweb.save(res, out / openweb.OPENWEB_JSON)
    if not res.docs:
        return ["开放网络资料：一篇都没取到，"
                "定性判断的外部依据仍只有券商研报一类来源"]

    notes: list[str] = []
    if res.fetch_failed:
        notes.append(f"{res.fetch_failed} 篇抓取失败")
    if res.model_dated:
        notes.append(f"{res.model_dated} 篇的发布日期由模型给出，未经代码核验")
    for slot in res.missing_slots:
        notes.append(f"{slot}：未取到")
    if notes:
        return ["开放网络资料（" + "；".join(notes) + "）"]
    return []


def _restore_openweb(r, out: Path) -> list[str]:
    """第二阶段：读回第一阶段落盘的开放网络文档。**不重新采集。**

    落盘文件缺失或损坏时照常继续，只记清单 —— 重跑 discover 会引入第一
    阶段没有的文档，那正是这套落盘机制要防的事。
    """
    from ir_agent.sources import openweb
    from ir_agent.sources.external import Corpus

    path = out / openweb.OPENWEB_JSON
    if not path.exists():
        return ["开放网络资料未跨阶段存活（未找到 "
                f"{openweb.OPENWEB_JSON}），外部引文可能核验不过"]
    if r.corpus is None:
        r.corpus = Corpus(as_of=r.as_of)
    res = openweb.load(path, r.corpus)
    if not res.docs:
        return ["开放网络资料未跨阶段存活（落盘文件读不出文档），"
                "外部引文可能核验不过"]
    if res.model_dated:
        return [f"开放网络资料（{res.model_dated} 篇的发布日期由模型给出，"
                "未经代码核验）"]
    return []
```

- [ ] **Step 4: 在 `run_analysis` 里插入调用**

在 `ir_agent/full.py` 的 `run_analysis` 中，把

```python
    md, pr, facts_skipped = _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += facts_skipped
    qual_client = _analyse_qualitative(code, r, as_of, model, md, pr, skipped)
```

改为

```python
    md, pr, facts_skipped = _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += facts_skipped

    # ── 开放网络文档入语料库 ────────────────────────────────────
    # 接在确定性层之后（研报已入库，去重要用）、定性三节之前（战略 /
    # 产业链 / 宏观定性要引用其中的原文）。**不能并进 _analyse_facts** ——
    # 理由见 _load_openweb 的 docstring。
    skipped += _load_openweb(
        code, r, as_of, out, research_client,
        industry=(meth.industry if meth is not None else ""))

    qual_client = _analyse_qualitative(code, r, as_of, model, md, pr, skipped)
```

- [ ] **Step 5: 在 `run_valuation` 里插入读回**

在 `ir_agent/full.py` 的 `run_valuation` 中，把

```python
    _, _, facts_skipped = _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += facts_skipped
```

改为

```python
    _, _, facts_skipped = _analyse_facts(code, r, as_of, snapshot_dir)
    skipped += facts_skipped
    # 开放网络文档：读回第一阶段落盘的，**不重新采集**。重跑 discover
    # 会引入第一阶段没有的文档，让第一阶段正文里的外部引文核验不过。
    skipped += _restore_openweb(r, out)
```

- [ ] **Step 6: 跑测试确认通过**

```bash
python -m pytest tests/test_full_context.py -q
```

Expected: PASS

- [ ] **Step 7: 跑全量测试并提交**

```bash
python -m pytest tests -q -m "not live"
```

Expected: 全部通过

```bash
git add ir_agent/full.py tests/test_full_context.py
git commit -m "full: 开放网络采集接在确定性层之后、定性三节之前"
```

---

## Task 8: 端到端实跑验证

**Files:**
- 无代码改动；本任务只跑一次真实的第一阶段并核对产物

- [ ] **Step 1: 跑一次真实的第一阶段**

```bash
cd "/Users/ariasleo/Desktop/谢海量作品集/ir-agent" && python -m ir_agent 600183 --year 2025 --analyse out/600183-openweb
```

Expected: 跑完（可能耗时数分钟并产生模型开销）。终端产物清单里应出现「开放网络资料」相关条目。

- [ ] **Step 2: 核对落盘文件**

```bash
python -c "
import json
d = json.load(open('out/600183-openweb/openweb.json'))
print('篇数', len(d['docs']))
print('缺口', d['missing_slots'])
print('额外', d['extra_slots'])
print('抓取失败', d['fetch_failed'], '模型报日期', d['model_dated'])
for x in d['docs']:
    print(' ', x['tier'], x['published'], x['date_source'], x['publisher'], x['url'][:70], len(x['text']), '字')
"
```

Expected: 至少 1 篇文档；每篇的 `tier` 与其 URL 域名对得上（`.gov.cn` 必须是 `official`，不认识的域名必须是 `media`）；`published` 全部不晚于 as_of。

- [ ] **Step 3: 核对文档确实进了提示词**

```bash
grep -c "公司之外的资料" out/600183-openweb/brief.md || \
python -c "
import json,pathlib
d=json.load(open('out/600183-openweb/openweb.json'))
brief=pathlib.Path('out/600183-openweb/brief.md').read_text(encoding='utf-8')
hits=[x['publisher'] for x in d['docs'] if x['publisher'] and x['publisher'] in brief]
print('简报里出现的开放网络来源：', hits)
"
```

Expected: 打印出结果而不报错。**若一个都没出现不算失败** —— 模型未必每篇都引用，但文档确实进了提示词（这一点由 Task 5 的单元测试保证）。

- [ ] **Step 4: 记录实跑结论**

把 Step 2 / Step 3 的实际输出（篇数、分级分布、缺口槽位、有没有抓取失败）贴进任务报告。**如实报告**：若某个固定槽位从没取到过、或全部落 MEDIA，这是要被看见的结论，不是要被藏起来的瑕疵。

- [ ] **Step 5: 清理实跑产物**

```bash
rm -rf out/600183-openweb
```

---

## Self-Review

**1. Spec coverage**

| Spec 章节 | 对应任务 |
|---|---|
| 一、现状 / Finding 与 Document 的区别 | 无需实现（Task 1 模块 docstring 记录） |
| 二、范围拆分（只做 A） | 全计划范围即 A |
| 三、架构：discover/fetch 分工 | Task 2（discover 提示词与解析）+ Task 3（fetch）+ Task 4（编排） |
| 三、主题槽位 + 模型可追加 | Task 2（`SLOTS`、`Candidate.extra`）+ Task 4（缺口只按固定槽位） |
| 三、`discover` 行格式与三道守卫 | Task 2 |
| 三、接在哪里：不能进确定性层 | Task 7（含结构性回归测试） |
| 三、跨阶段：落盘 / 读回 | Task 6 + Task 7 |
| 四、`tier_of_url` 与注册域边界 | Task 1（含两条不变式测试） |
| 四、白名单怎么建 | Task 1 Step 5（逐条核实，核实不了就删） |
| 五、日期：meta 优先、标出来源 | Task 4（`model_dated`）+ Task 6（`date_source`） |
| 六、不引新依赖 / 正文提取 | Task 3 |
| 六、去重 | Task 4（`seen`，含重定向后再查一次） |
| 六、规模：1 次调用、5 槽×2 篇、截 2000 字 | Task 4（`PER_SLOT` / `MAX_DOCS`）+ Task 5（`prompt_limit`） |
| 七、错误处理全部不阻断 | Task 4（抓取失败计数）+ Task 7（两处 try/except 与清单文案） |
| 八、测试清单 | Task 1–7 的测试步骤逐条覆盖 |
| 九、不做什么 | 计划内无对应任务，符合预期 |

无缺口。

**2. Placeholder scan**

无 TBD / TODO / 「类似 Task N」/ 无代码的代码步骤。`_SELLSIDE_HOSTS = ()` 是一个**有理由的空值**（注释写明为何为空、以及怎么加），不是占位符。

**3. Type consistency**

- `Candidate`（Task 2）字段 `slot/title/url/publisher/published/extra` — Task 4 逐个使用，名字一致
- `Fetched`（Task 3）字段 `final_url/text/meta_published` — Task 4 逐个使用，名字一致
- `OpenWebResult`（Task 4）字段 `docs/missing_slots/extra_slots/fetch_failed/model_dated` — Task 6 的 `save`/`load` 与 Task 7 的清单文案逐个使用，名字一致
- `Document` 新增字段 `prompt_limit`（Task 5）与 `date_source`（Task 6）— Task 4 构造时按任务顺序分两次补上，Task 6 的序列化逐个覆盖
- `parse_date`（Task 2 改名）— Task 3 的 `meta_date` 复用同一个函数
- `tier_of_url`（Task 1）— Task 4 传入 `got.final_url`，与其 docstring 要求一致
