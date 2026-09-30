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
    # 去重键集合：存的是**未截断**、且已把 URL 挖掉之后的系统性原因
    # （见 `_cause_key`），跟 `res.fetch_failed_reasons` 里存的（已截断、
    # 保留原始 URL 的展示用字符串）解耦——与 openweb.gather 的
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
                          f"{a.ann_id}：{e.__class__.__name__}: {e}")
            continue
        if not text.strip():
            _note_failure(res, seen_failure_keys, f"{a.ann_id}：PDF 取文为空")
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


# `_download` 抛出的真实异常（`requests.HTTPError`/`ConnectionError`/
# `retry.RetryExhausted`）的 `str(e)` 里嵌着失败的那个 URL——见调用点
# `f"{a.ann_id}：{e.__class__.__name__}: {e}"`。每篇公告的下载地址都
# 不一样，同一种系统性原因（巨潮整体挡 PDF 请求、代理宕了、DNS 解不出
# 来）换一个 ann_id/URL 就长得不一样，靠原样字符串去重形同虚设——旗舰
# 场景「八篇都下载超时」会印八遍。这里把 `http(s)://` 起始、到下一个
# 空白为止的这一段换成占位符再当 key：诊断里其余部分（状态码、异常类
# 名、"Client Error"/"Max retries exceeded" 这类描述错误种类的文字）
# 原样保留，所以两种真正不同的原因（404 与「PDF 取文为空」、404 与
# 内容类型问题）依然会留下不同的 key，不会被过度合并——过度合并会把
# 「这是哪一种失败」这条诊断价值直接抹掉，比没去重更糟。
_URL_RE = re.compile(r"https?://\S+")


def _cause_key(diagnosis: str) -> str:
    """把诊断文本里的 URL 换成占位符，只留下“是哪一种失败”。见上方
    模块级注释。"""
    return _URL_RE.sub("<url>", diagnosis)


def _note_failure(res: AnnounceResult, seen_keys: set[str], reason: str
                   ) -> None:
    """记一条失败。**按系统性原因去重，不按整条消息、也不按已截断的
    存储副本去重**——两处坑，缺一处不修都会让去重形同虚设：

    1）去重键必须从 `seen_keys`（**未截断**）里查，不能从
       `res.fetch_failed_reasons`（**已截断**）反推。第一次撞见某种
       诊断时，被截断保留的尾巴长度取决于当时那个 ann_id 有多长——同
       一种诊断换一个长度不同的 ann_id，切出来的尾巴就不一样，去重
       失效。`seen_keys` 是单独维护的一个集合，与「存进
       `fetch_failed_reasons` 的字符串会被截断」解耦，与
       openweb.gather 的 `seen_diagnoses` 同一处置。

    2）诊断本身不能拿「原样字符串」当 key——见 `_cause_key`。

    列表里存的仍是**完整消息**（带着第一次撞见这种原因时的那个
    ann_id/URL，只是显示用的字符串按 `_REASON_LIMIT` 截断），不是把
    ann_id/URL 直接丢掉：读者除了「这是哪一种失败」，还想「至少举一个
    例子」——与 openweb 同一条道理。
    """
    res.fetch_failed += 1
    diagnosis = reason.split("：", 1)[-1]
    key = _cause_key(diagnosis)
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
