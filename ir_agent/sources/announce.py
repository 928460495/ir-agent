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

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ir_agent.lineparse import keyed
from ir_agent.sources.annual_pdf import _download
from ir_agent.sources.cninfo import Announcement
from ir_agent.sources.cninfo import classify as _classify
from ir_agent.sources.cninfo import search as _search
from ir_agent.sources.external import Document, Tier
from ir_agent.sources.openweb import (TEXT_LIMIT, _doc_from_json, _doc_to_json,
                                      _str_list_field)

# 公告检索窗口（月）。比 corpus.gather 的研报窗口（24 个月）短：公告的
# 时效性更强，两年前的扩产公告对当期因子取值的参考价值已经很低，而条数
# 会翻倍、把模型要读的候选清单撑长。
ANNOUNCE_MONTHS = 12

# 单轮最多**入库**几篇。**这是入库上限，不是提示词上限** —— 后者在
# `Corpus.prompt_docs` 里，由 `PROMPT_DOCS` 控制。两者分开的理由：入库的
# 文档供 `gaps._match_announce` 按因子做全文检索（池子越大召回越高），而
# 进提示词的文档要控长度。此前这两件事共用一个 `MAX_ANNOUNCEMENTS = 8`，
# 而那个数的注释里写的理由是「同一批文档会同时压到战略/产业链/宏观定性
# 三个模块的提示词上」—— **消费方的约束被实现成了生产方的上限**。
MAX_INGEST = 20

# 三个定性模块的提示词里，COMPANY 级最多给几篇。沿用旧的 8。
PROMPT_DOCS = 8

# `classify` 判成这两类的直接拒绝。
#   定期报告 —— 动辄数百页，且年报正文已经由 `r.sections` 单独走一条路
#               （`_load_sections` 从 annual_url 抓 PDF 切节）。放它进来的
#               代价不是「多一篇无用文档」，是下载一个几十 MB 的 PDF 并
#               全文抽取。
#   股东大会 —— 纯程序性。
# 其余三类（业绩预告 / 股份变动 / 重大事项）是正面类别，直接接受。
_REJECT_CATEGORIES = ("定期报告", "股东大会")

# 落进 `classify` 的「其他」时再过一道。**这些是 `classify` 认不出来的
# 程序类** —— 它只有五个正面类别，董事会决议/监事会/独董/会计师改聘/
# 业绩说明会全归「其他」。
#
# **不另写一个并行的分类器。** `cninfo.classify` 已经在用（`Announcement.
# category` 就是它算的），再写一套词表意味着两套会漂 —— 它们现在就已经
# 不一致：`classify` 的定期报告模式带 `(?!.*摘要)`，所以「年度报告摘要」
# 归「其他」。这条黑名单因此要接住 `报告摘要`。
_PROCEDURAL = re.compile(
    r"董事会[^，。]{0,12}决议|监事会|独立董事|会计师事务所"
    r"|业绩说明会|业绩预约披露|投资者关系活动"
    r"|报告摘要|公司章程|章程修订|持股计划[^，。]{0,6}摘要"
    r"|募集资金[^，。]{0,8}存放|内部控制[^，。]{0,6}评价")


def is_substantive(title: str) -> bool:
    """这条公告值不值得下载正文入库。**确定性，不调模型。**

    判的是**公告类型**，与本轮有哪些因子无关 —— 因子定向发生在消费端
    （`gaps._match_announce` 按因子做全文检索），那时因子已经知道了。

    **黑名单而非白名单。** 本项目有一条相反方向的教训：`analyst._ask` 的
    过滤器曾是黑名单，每放宽一次就撞上下一类，最后改成 `is_fabrication`
    白名单。那条教训在**拒绝模型输出**的方向上成立（漏放一条伪造是实质
    损害）。这里方向相反，要的是**高召回**：误判成实质类 = 多下一篇无用
    PDF（便宜）；误判成程序类 = 丢掉一个可直接引为依据的 COMPANY 级来源
    （贵，而且无声）。所以 `classify` 认不出来的一律放过去。
    """
    t = (title or "").strip()
    if not t:
        return False
    cat = _classify(t)
    if cat in _REJECT_CATEGORIES:
        return False
    if cat != "其他":                   # 业绩预告 / 股份变动 / 重大事项
        return True
    return not _PROCEDURAL.search(t)

# PDF 取文的页数上限。多数公告只有一两页，但「定期报告」可能两百页以上，
# 全文取回既无必要也会让单篇挤掉其余七篇的额度。
PDF_MAX_PAGES = 30

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
    # 按标题判为程序类而被跳过的条数。**必须能说出来** —— 与 `candidates`
    # 同一条理由：读者看到「入库 14 篇」时要能知道另外多少篇是被筛掉的、
    # 筛掉的理由是类型而不是故障。
    rejected: int = 0
    fetch_failed: int = 0
    fetch_failed_reasons: list[str] = field(default_factory=list)
    # 保留但不再有模型挑选那条来源。`_load_announce` 仍读它。
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


def gather(*, code: str, company: str, as_of: date, corpus,
           cache_dir: Path | str | None = None) -> AnnounceResult:
    """公告入语料库：代码取清单 → **确定性按标题筛** → 代码抓 PDF。

    **没有 `client` 参数。** 挑选改为 `is_substantive`，与本轮因子无关
    （见该函数）。此前它需要 `factors=`，而那个值来自方法论预研的分解式
    —— 预研挪到辩论① 之后以后，那条依赖会让挑选的唯一判据恒为空。

    确定性化的附带性质：整步变得可复现（同一个 as_of 下 `_search` 返回同
    一份清单、筛选无随机性）。**但本函数仍然不该放进 `_analyse_facts`**
    ——搬进去会让第二阶段每轮重抓 PDF，而收益只是省掉一套正在正常工作的
    落盘读回机制（`_restore_announce`）。那是独立的清理。
    """
    res = AnnounceResult()
    start = as_of - timedelta(days=int(ANNOUNCE_MONTHS * 30.5))
    anns = _search(code, start, as_of)
    res.candidates = len(anns)
    if not anns:
        return res

    # 实质类按披露日倒序取前 MAX_INGEST 篇。
    picked = [a for a in anns if is_substantive(a.title)]
    res.rejected = len(anns) - len(picked)
    picked.sort(key=lambda a: a.ann_date, reverse=True)
    picked = picked[:MAX_INGEST]
    res.picked = len(picked)

    seen = {d.url for d in corpus.docs}
    # 去重键集合：存的是**未截断**、由异常结构算出的原因键
    # （见 `_exception_cause_key`），跟 `res.fetch_failed_reasons` 里存的
    # （已截断、给人看的展示字符串）解耦——与 openweb.gather 的
    # `seen_diagnoses` 同一处置。
    seen_failure_keys: set[str] = set()
    for a in picked:
        if a.url in seen:
            continue
        try:
            data = _download(a.url, cache_dir)
            text = pdf_text(data)
        except Exception as e:                              # noqa: BLE001
            _note_failure(res, seen_failure_keys,
                          f"{a.ann_id}：{e.__class__.__name__}: {e}",
                          _exception_cause_key(e))
            continue
        if not text.strip():
            _note_failure(res, seen_failure_keys, f"{a.ann_id}：PDF 取文为空",
                          _EMPTY_TEXT_KEY)
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


# 第五次在这类去重上翻车了：前几轮各自往「剥掉消息文本里的一种模式」
# 加一条正则（先是整条 URL，后来还想过加 `/\S+\.PDF`），每次都被下一种
# 没预料到的消息形状打穿。最狠的一例：`annual_pdf._download` 把请求包
# 在 `with_retry` 里，瞬时网络失败会先变成 `requests.ConnectionError`
# （由 urllib3 的 `MaxRetryError` 构造），重试三次耗尽后再包一层
# `retry.RetryExhausted`；这条链子上 `ConnectionError` 的消息里嵌的是
# **`request.path_url`——只有路径和查询串，没有 scheme、没有 host**
# （例如 `Max retries exceeded with url: /finalpage/2026-08-01/a0.PDF`），
# `https?://` 起手的正则在这种消息里永远匹配不到，三次连接失败换三个
# ann_id/URL 就得到三个互不相同的"字符串"，去重形同虚设。
#
# 教训：**别再往消息文本里加一条新模式**——代理错误里带端口号、SSL
# 错误这类形状，一旦真的撞见，接法仍然是同一套（读结构，不读
# `str(e)`），不是回头再加一条正则。
# 改成直接读异常对象的**结构**——类型链（`__cause__`/`__context__`，
# 这条链正是用来把「`RetryExhausted` 包着 `ConnectionError`」和「裸的
# `HTTPError`」分开的信息）、链上每个节点的 `errno`
# （`getattr(node, "errno", None)`，用来接住「裸 `OSError`，只能靠
# errno 分辨」这类——磁盘满 `ENOSPC` 和单文件 I/O 错误 `EIO`、网络不可达
# `ENETUNREACH` 和主机不可达 `EHOSTUNREACH`，CPython 都没有把它们提升
# 成专门的子类，是 `ECONNREFUSED` → `ConnectionRefusedError` 那种提升
# 覆盖不到的空档）、以及 HTTP 失败时从 `response.status_code` 读到的
# 整数状态码（不是从渲染出的文本里抠数字）。这三样都不经过 `str(e)`，
# 因此这个失败模式和以后任何新形状都不会再打穿它。
#
# 本模块自己判定的本地条件（目前只有「PDF 取文为空」）没有异常对象，
# 用一个字面量键（`_EMPTY_TEXT_KEY`）代替——这条件的字符串是我们自己
# 写的常量，不含每次调用都不同的信息，本身就适合直接当 key。
_EMPTY_TEXT_KEY = "local:empty_pdf_text"


def _exception_cause_key(e: BaseException) -> str:
    """从异常对象的**结构**——不是 `str(e)`——算出去重键。见上方模块级
    注释：这是为了不再重蹈"剥消息文本里的一种模式"的覆辙。

    组成：
    - 类型链：`e` 自己的类型名，沿 `__cause__`/`__context__` 一路走到
      链尾，逐个记录类型名。这是把「`RetryExhausted` 包着
      `ConnectionError` 包着 `MaxRetryError` 包着 `NewConnectionError`」
      与「裸的 `requests.HTTPError`」区分开的关键信息——单看最外层类型
      名有时还不够（比如同样是 `RetryExhausted`，包的可能是连接失败也
      可能是包了个持续 5xx 的 `HTTPError`），把整条链都记下来才稳。
    - 每个节点的 `errno`：`getattr(node, "errno", None)`，有值就拼进
      该节点的类型名里（例如 `OSError[errno=28]`）。裸 `OSError`/
      `PermissionError` 只靠 `.errno` 就能表达完全不同的系统性原因——
      磁盘满（`ENOSPC`）和单文件 I/O 错误（`EIO`）、网络不可达
      （`ENETUNREACH`）和主机不可达（`EHOSTUNREACH`），CPython 都没有
      把它们提升成专门的异常子类（不像 `ECONNREFUSED` 有
      `ConnectionRefusedError`），不读 `errno` 的话类型链会把它们全部
      记成同一个 `"OSError"`，八个不同原因的失败会被合并成一条、其余
      七条静默消失。没有 `errno` 的节点（多数 `requests`/`urllib3`
      异常）取到 `None`，类型名不受影响。
    - HTTP 状态码：链上第一个带 `.response.status_code` 的节点，读的
      是 `Response` 对象上的整数字段，不是从消息里拿正则抠出来的
      "404"/"403" 这类子串。这一项存在，404 和 403 才不会被合并成
      同一个 key——两者的类型链完全相同（都只是一个 `HTTPError`），
      不看状态码就区分不出来。

    `str(e)` 里的东西（哪个 ann_id、哪个 URL、"Max retries exceeded"
    这类描述文字）一个字节都不进这个函数——它们只用来拼**展示用**的
    `reason` 字符串，由调用方单独处理。
    """
    types: list[str] = []
    status: int | None = None
    node: BaseException | None = e
    visited: set[int] = set()
    while node is not None and id(node) not in visited:
        visited.add(id(node))
        name = type(node).__name__
        node_errno = getattr(node, "errno", None)
        if node_errno is not None:
            name = f"{name}[errno={node_errno}]"
        types.append(name)
        if status is None:
            resp = getattr(node, "response", None)
            code = getattr(resp, "status_code", None)
            if code is not None:
                status = code
        node = node.__cause__ or node.__context__
    key = ">".join(types)
    return f"{key}:{status}" if status is not None else key


def _note_failure(res: AnnounceResult, seen_keys: set[str], reason: str,
                   key: str) -> None:
    """记一条失败。**按系统性原因去重，不按整条消息、也不按已截断的
    存储副本去重**——两处坑，缺一处不修都会让去重形同虚设：

    1）去重键必须从 `seen_keys`（**未截断**）里查，不能从
       `res.fetch_failed_reasons`（**已截断**）反推。第一次撞见某种
       诊断时，被截断保留的尾巴长度取决于当时那个 ann_id 有多长——同
       一种诊断换一个长度不同的 ann_id，切出来的尾巴就不一样，去重
       失效。`seen_keys` 是单独维护的一个集合，与「存进
       `fetch_failed_reasons` 的字符串会被截断」解耦，与
       openweb.gather 的 `seen_diagnoses` 同一处置。

    2）`key` 由调用方从异常对象的**结构**算出（见
       `_exception_cause_key`）或是一个本模块自定义的字面量
       （`_EMPTY_TEXT_KEY`），不是从 `reason` 文本里解析出来的——`reason`
       只用于展示，`key` 完全独立于它，因此 `reason` 里嵌了什么样的
       URL/路径都不影响去重是否生效。

    列表里存的仍是**完整消息**（带着第一次撞见这种原因时的那个
    ann_id/URL，只是显示用的字符串按 `_REASON_LIMIT` 截断），不是把
    ann_id/URL 直接丢掉：读者除了「这是哪一种失败」，还想「至少举一个
    例子」——与 openweb 同一条道理。
    """
    res.fetch_failed += 1
    if key in seen_keys:
        return
    seen_keys.add(key)
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


ANNOUNCE_JSON = "announce.json"


def save(res: AnnounceResult, path: Path | str) -> None:
    """落盘，供第二阶段读回。形状与 `openweb.save` 对齐。"""
    Path(path).write_text(json.dumps({
        "docs": [_doc_to_json(d) for d in res.docs],
        "candidates": res.candidates,
        "picked": res.picked,
        "fetch_failed": res.fetch_failed,
        "fetch_failed_reasons": res.fetch_failed_reasons,
        "ignored": res.ignored,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path: Path | str, corpus) -> AnnounceResult:
    """读回落盘的公告文档并入语料库。容忍两个层级的损坏，两者都不抛。

    与 `openweb.load` 同一套处置（那里的 docstring 有完整论证）：

    - **整份损坏**——文件不存在、JSON 语法错、顶层不是 dict、或头部字段
      类型烂到没法安全解读。整份退化成空 `AnnounceResult()`：任何一处都
      可能污染后面所有字段的读取，没有「只信一半」这回事。
    - **单条文档损坏**——`docs` 是合法列表但某条不是合法 Document JSON。
      只跳过那一条，其余照常读回入库。

    **时点守卫照样走一遍 `corpus.add_if_visible`。** as_of 可能与落盘时
    不同，「这是我们自己存的」不构成放行理由——否则第二阶段换一个更早的
    as_of 就能看见未来的公告。没能入库的不进 `docs`。
    """
    p = Path(path)
    if not p.exists():
        return AnnounceResult()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return AnnounceResult()

    try:
        res = AnnounceResult(
            candidates=int(raw.get("candidates", 0)),
            picked=int(raw.get("picked", 0)),
            fetch_failed=int(raw.get("fetch_failed", 0)),
            fetch_failed_reasons=_str_list_field(raw, "fetch_failed_reasons"),
            ignored=_str_list_field(raw, "ignored"))
        docs_raw = list(raw.get("docs", []))
    except Exception:                                       # noqa: BLE001
        # 顶层不是 dict（没有 .get）、docs 不可迭代、或某个计数字段不是
        # 数字。这三类都让后面的读取不可信，整份降级。
        return AnnounceResult()

    # 与 `openweb.load` 同一道去重：若哪天确定性层也摄入了同一批巨潮公告
    # URL，没有这道保险，读回的公告会被重复追加，「外部资料 N 篇」与
    # COMPANY 档计数都会被悄悄放大。
    seen = {d.url for d in corpus.docs}
    for item in docs_raw:
        try:
            doc = _doc_from_json(item)
        except Exception:                                   # noqa: BLE001
            continue
        if doc.url in seen or not corpus.add_if_visible(doc):
            continue
        seen.add(doc.url)
        res.docs.append(doc)
    return res
