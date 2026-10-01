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
from ir_agent.sources.cninfo import search as _search
from ir_agent.sources.external import Document, Tier
from ir_agent.sources.openweb import TEXT_LIMIT

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

    四道守卫，其中三道（非数字、越界、超过单轮上限）被触发时记一条
    说明——**被忽略的挑选要说得出来**，否则读者看到「入库 3 篇」无从
    判断模型是只挑了 3 条，还是挑了 8 条但 5 条不合法。

    第四道（重复下标）不记说明，是刻意的：重复不是一次无效挑选，该
    公告最终仍会入库一次，「挑了哪些文档」这件事没有信息被丢——这与
    越界/超限那种「模型想要的东西被丢弃、必须让读者看见」的情况性质
    不同。给每条重复都记一句「序号 1 重复」只会是提示词里的噪音。

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


# `PROMPT` 混用了两种模板机制，是刻意的：
# - `{{company}}`/`{{code}}`/`{{factors}}`/`{{candidates}}` 用双花括号转义，
#   在本模块里保持字面量，留给调用方在运行期用 `.format()` 填入——每次
#   调用的公司、因子、候选清单都不同，不能在模块加载时定死。
# - `MAX_ANNOUNCEMENTS` 用 f-string 在模块加载时就烧成字面量，因为它是
#   跨模块共享的常量（`parse_picks` 的上限判断也读它），提示词里写的
#   数字必须和代码实际执行的上限永远一致；写成 `{limit}` 交给调用方会
#   打开两者失配的口子——调用方传错 `limit=` 或忘记传，提示词里的数字
#   就会和 `parse_picks` 实际执行的上限对不上。
# 因此：**不要**把 `MAX_ANNOUNCEMENTS` 改回 `{{limit}}`（会关闭上面那个
# 一致性保证）；也**不要**在调用 `.format()` 时传入 `limit=`（模板里没
# 有 `{{limit}}` 占位符，这个参数不会生效）。
PROMPT = f"""从下面这家公司的法定披露公告里，挑出与本次估值分解式相关的几条。

公司：{{company}}（{{code}}）
本次分解式关注的驱动因子：{{factors}}

候选公告（代码已从巨潮资讯取回，**只能从这个清单里挑**）：
{{candidates}}

每条一行，只给序号：

`选取: <序号> | <一句话：与哪个因子相关>`

**硬性要求**
- **只能给上面清单里的序号**，最多 {MAX_ANNOUNCEMENTS} 条。序号之外的内容会被忽略。
- 优先挑能给出**具体数字**的：扩产/投产、产能、价格调整、重大合同、
  对外投资。纯程序性公告（股东大会通知、董事会决议、独立董事声明、
  会计师事务所改聘）对因子取值没有帮助，不要挑。
- **不要挑「关于召开业绩说明会的公告」这类只是通知会议将要举行的公告**
  ——它们没有内容。
- 一条都不相关就不要输出任何 `选取:` 行，不要为了凑数而挑。

只输出上述格式的行。
"""

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
        candidates=format_candidates(anns)))
    picks, res.ignored = parse_picks(raw, len(anns))
    res.picked = len(picks)

    seen = {d.url for d in corpus.docs}
    # 去重键集合：存的是**未截断**、由异常结构算出的原因键
    # （见 `_exception_cause_key`），跟 `res.fetch_failed_reasons` 里存的
    # （已截断、给人看的展示字符串）解耦——与 openweb.gather 的
    # `seen_diagnoses` 同一处置。
    seen_failure_keys: set[str] = set()
    for idx in picks:
        a = anns[idx]
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
    from ir_agent.sources.openweb import _doc_to_json
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
    from ir_agent.sources.openweb import _doc_from_json, _str_list_field

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

    for item in docs_raw:
        try:
            doc = _doc_from_json(item)
        except Exception:                                   # noqa: BLE001
            continue
        if corpus.add_if_visible(doc):
            res.docs.append(doc)
    return res
