"""开放网络定向采集。**外部数据的证明力低一个量级，必须在代码里体现。**"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent.sources.external import Tier
from ir_agent.sources.web import (PROMPT, Finding, parse_findings, search,
                                  supports_value, tier_of)

AS_OF = date(2026, 9, 7)

OUT = """发现: utilization | 2026年上半年国内封测厂平均稼动率约78% | https://www.semi.org/x | SEMI | 2026-07-01
发现: utilization | 行业稼动率较去年同期下降5个百分点 | https://finance.example.com/y | 某财经网 | 2026-08-15
"""


class _Client:
    def __init__(self, *r):
        self.replies, self.prompts = list(r), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


@pytest.fixture
def restore_web_capable():
    """三处测试都要临时改写模块级开关 WEB_CAPABLE 再复位，此前每处各抄
    一遍 try/finally；抽成 fixture 后测试体只管改值，复位交给 yield 后的
    finally，改哪个值（True 还是 False）由各测试自己决定。"""
    import ir_agent.sources.web as W
    old = W.WEB_CAPABLE
    try:
        yield W
    finally:
        W.WEB_CAPABLE = old


# ══════════════════════════════════════════════════════════════════════
# tier_of() —— 第三次换设计后的测试，钉的是「白名单全称 + 允许修饰词」
# 这一条机制本身，不是钉某几个具体名字。
#
# 前两轮的表驱动测试都只钉住了名字：复审发现，只要从 `_MEDIA_MARKERS`
# 删掉 "网" 这一个词，50 项测试全绿，而 "凤凰网证券" 当场被判成 SELLSIDE。
# 这次的核心是「危害方向穷举」——凡是不在白名单里的名字，不管长得多像
# 官方/卖方，都必须落 MEDIA；以及一条「机制回归哨兵」，专门堵「改回子串
# 匹配后测试还能全绿」这条退路。
# ══════════════════════════════════════════════════════════════════════

# 危害方向：把媒体错拔成 SELLSIDE/OFFICIAL 会让一篇文章单独决定因子取值，
# 这些必须全部判 MEDIA，一个都不能拔高。
MEDIA_NAMES = [
    # 证券类报纸/门户——"证券"在这些名字里是前缀/频道名，不是机构类型后缀
    "证券时报", "证券时报网", "中国证券报", "中国证券报·中证网",
    "证券日报", "证券日报网", "上海证券报", "中国证券报社",
    "每日经济新闻", "第一财经", "新浪财经", "界面新闻", "中证网", "e公司",
    "新浪证券", "腾讯证券", "搜狐证券", "同花顺证券", "雪球证券", "凤凰网证券",
    # 英文同类陷阱
    "Securities Daily", "China Securities Journal", "Shanghai Securities News",
    "Securities Times", "Investment Weekly",
    # SEMI / IDC / Gartner 词形陷阱——前缀撞上、中段撞上、词边界规则堵不住的
    "Semiconductor Engineering", "SemiAnalysis", "Semiconductor Digest",
    "MidCap Research", "中国 IDC 圈", "IDC Frontier", "SEMI-Insights",
    "The SEMI Times", "Not-Gartner", "Gartner-like Research",
    "Pseudo IDC Weekly", "Anti-IDC Blog",
    # 白名单全称 + 非修饰词后缀的"前缀陷阱"——命中前缀不等于是那家机构
    "Gartner 观点转载", "转引自 IDC", "央行观察", "协会视角",
    "统计局数据搬运工", "赛迪观察", "中金公司观察",
    "Goldman Sachs Rumors Blog", "Morgan Stanley Fanpage", "UBS Daily",
]

# 召回方向：真正的官方/协会/权威一手来源，以及卖方研究所，必须判对，
# 否则实跑会频繁误报「证据不足」。
OFFICIAL_NAMES = [
    "国家统计局", "国家统计局官网", "统计局公报", "工信部", "工信部官网",
    "上交所", "上海证券交易所官网", "中国人民银行", "证监会",
    "中国半导体行业协会网站",
    "SEMI", "SEMI 官网", "Gartner", "TrendForce", "IDC",
    "集邦咨询", "赛迪顾问",
]

SELLSIDE_NAMES = [
    "中信证券", "中信建投", "中金公司",
    "国泰君安证券研究所", "华泰证券研究所", "招商证券股份有限公司",
    "广发证券发展研究中心", "申万宏源研究", "中信证券研究部官网",
    "Morgan Stanley", "Goldman Sachs", "J.P. Morgan", "JP Morgan",
]


@pytest.mark.parametrize("name", MEDIA_NAMES)
def test_危害方向穷举_一律判为媒体(name):
    assert tier_of(name) is Tier.MEDIA


@pytest.mark.parametrize("name", OFFICIAL_NAMES)
def test_召回方向_官方与权威一手来源判对(name):
    assert tier_of(name) is Tier.OFFICIAL


@pytest.mark.parametrize("name", SELLSIDE_NAMES)
def test_召回方向_卖方判对(name):
    assert tier_of(name) is Tier.SELLSIDE


# 兜底：异常输入一律 MEDIA。断言必须写 `is Tier.MEDIA`，不能写
# `is not Tier.SELLSIDE`——弱否定断言连 OFFICIAL 都能蒙混过关，钉不住
# "判不出来就该落在 MEDIA" 这条规则本身。
@pytest.mark.parametrize("name", [
    "", "   ", "　　", "！＠＃￥%^&*", "@#$%^&*()", "x" * 5000,
])
def test_兜底_异常输入一律判为媒体(name):
    assert tier_of(name) is Tier.MEDIA


def test_兜底_None一律判为媒体():
    assert tier_of(None) is Tier.MEDIA  # type: ignore[arg-type]


# 繁体：只需覆盖白名单条目里实际出现的字，覆盖不到的繁体名落 MEDIA，
# 方向安全。
def test_繁体白名单命中():
    assert tier_of("中信證券") is Tier.SELLSIDE
    assert tier_of("國泰君安證券") is Tier.SELLSIDE
    assert tier_of("證券時報") is Tier.MEDIA


# 机制回归哨兵：任何不在白名单里的名字都不得被判为 SELLSIDE/OFFICIAL。
# 构造方式是「白名单全称 + 一个不在『允许的修饰词』集合里的后缀」——
# 例如 "中金公司观察"、"UBS Fanpage"。子串匹配、词尾规则、词边界正则
# 都会因为命中了这个前缀就直接判定通过；只有"整名必须能被『全称+
# 修饰词』完全解释"这一条机制会正确地把它们打回 MEDIA。如果有人把
# tier_of 改回任何一种模糊匹配，这项参数化测试必须变红。
_SENTINEL_ENTRIES_CN = [
    "中信证券", "中金公司", "华泰证券", "招商证券", "国泰君安证券",
    "国家统计局", "统计局", "工信部", "证监会", "上交所",
    "中国人民银行", "中国半导体行业协会", "赛迪顾问", "集邦咨询",
]
_SENTINEL_SUFFIXES_CN = ["观察", "视角", "评论", "解读", "搬运工"]

_SENTINEL_ENTRIES_ASCII = [
    "SEMI", "GARTNER", "IDC", "TRENDFORCE", "Morgan Stanley",
    "Goldman Sachs", "UBS",
]
_SENTINEL_SUFFIXES_ASCII = [" Blog", " Fanpage", " Rumors", "-Insights",
                            " Weekly", " Frontier"]


def _sentinel_names() -> list[str]:
    names = []
    for entry in _SENTINEL_ENTRIES_CN:
        for suf in _SENTINEL_SUFFIXES_CN:
            names.append(entry + suf)
    for entry in _SENTINEL_ENTRIES_ASCII:
        for suf in _SENTINEL_SUFFIXES_ASCII:
            names.append(entry + suf)
    return names


@pytest.mark.parametrize("name", _sentinel_names())
def test_机制回归哨兵_白名单外一律不得拔高(name):
    assert tier_of(name) is Tier.MEDIA


# 结构哨兵：白名单里不许出现「关键词」性质的短片段。
#
# 上面那条哨兵能挡住把实现改回子串匹配，却挡不住另一种更隐蔽的退化——
# 实现原封不动，有人往白名单元组里塞一个裸片段（"证券""期货""协会"
# "央行"），tier_of 用的是 startswith，这样的片段会让白名单退化回
# **前缀**匹配，不是子串匹配（子串匹配是 `in`，会命中任意位置；这里
# 只会让以该片段**开头**的名字被误判，比如塞进"证券"之后"新浪证券"
# 这类"证券"在中间/结尾的名字完全不受影响）。web.py 里对此只有一句
# 注释警告，注释拦不住任何人，所以在这里变成可执行的守卫。
#
# 诚实地说，这条守卫是一个**粗略的代理检查**，不是机制保证：它管的
# 属性是"条目够不够长"，真正危险的属性是"条目+修饰词的拼接会不会
# 撞上一个真实媒体品牌名"，这两者并不等价。已经实测过反例：把"证券"
# 塞进白名单后，60 条对抗名 + 105 条哨兵名一条都没被拔高，只有
# "证券研究""证券中国""证券网"这类恰好被修饰词表拼满的名字出问题
# ——阈值挡不住这个真正的失效模式，行为层测试（危害方向穷举 + 机制
# 回归哨兵）对它也基本是盲的。这条测试仍然值得留着，因为它确实能挡住
# "证券""期货"这类明显的关键词化退化；但它不是万能的，且阈值本身有
# 已知的合法反例——`EY`（安永的法定简称）和`央行`（中国人民银行最
# 常见的书面简称，模型很可能直接这么写）都是 2 字符、都是能唯一指向
# 一家机构的合法全称。在"前缀+修饰词"机制下加`央行`并不会把"央行
# 观察"错误拔高（剩余的"观察"拼不满任何修饰词组合）——阈值管的属性
# 和真正危险的属性是两回事。将来要加`EY`/`央行`这类合法的 2 字符
# 全称，就该同时改这条测试的阈值并在这里说明理由，而不是让它默默
# 拦下一个合法条目。
def test_结构哨兵_白名单不得含短片段():
    from ir_agent.sources import web as _web

    entries = (_web._SELLSIDE_CN + _web._SELLSIDE_ASCII
               + _web._OFFICIAL_CN + _web._OFFICIAL_ASCII)
    短片段 = [e for e in entries if len(_web._normalize(e)) < 3]
    assert 短片段 == [], (
        f"白名单里混进了短片段 {短片段} —— 这会让白名单退化回**前缀**匹配"
        "（tier_of 用的是 startswith，不是子串匹配），见 web.py 中"
        "tier_of 的设计契约"
    )


# ══════════════════════════════════════════════════════════════════════
# Task 5 证伪审查：Critical 1——"中国"/"国际"不是修饰词，是机构名成分。
#
# 用的是审查者给出的原始反例：白名单全称接上"（中国网）"这类写法，
# 此前会因为"中国"能被 _fully_modifiers 拼满而错误拔高成 OFFICIAL/
# SELLSIDE。"统计局研究网"是同一类问题的另一种触发方式——不靠"中国"/
# "国际"，靠的是裸"网"本身太通用，"研究"（合法修饰词）+ 裸"网"就能
# 拼出一个读起来像媒体品牌的名字，所以修法里连带把裸"网"也从
# _MODIFIERS 里删掉了（保留"官网"/"网站"/"官方网站"这些更具体的
# 复合词）。
_CRITICAL1_MEDIA_NAMES = [
    "国家统计局（中国网）", "工信部（中国网）", "证监会（中国网）",
    "商务部·中国网", "海关总署（中国网）", "中国人民银行·中国网",
    "IDC·中国网", "SEMI（中国网）", "中信证券·中国网",
    "国家统计局·国际网", "统计局研究网", "国家统计局中国官网",
]


@pytest.mark.parametrize("name", _CRITICAL1_MEDIA_NAMES)
def test_critical1_中国国际不是修饰词而是机构名成分(name):
    assert tier_of(name) is Tier.MEDIA


# "中国"/"国际"删掉之后，真实存在的 SEMI/IDC/Gartner 中国分支需要作为
# 全称补进白名单——按本模块自己的契约，判不出来的应对方式是加全称，
# 不是留一个通用修饰词兜底。
@pytest.mark.parametrize("name", ["SEMI中国", "IDC中国", "Gartner中国",
                                   "SEMI 中国", "IDC 中国"])
def test_critical1_真实中国分支补进白名单后仍判对(name):
    assert tier_of(name) is Tier.OFFICIAL


# ══════════════════════════════════════════════════════════════════════
# Task 5 证伪审查：Critical 2——_normalize 里的 s.upper() 是全 Unicode
# 大写折叠，不是注释断言的"ASCII 转大写"。ı(U+0131)/ſ(U+017F) 会被
# Python 的 str.upper() 折成 ASCII 的 I/S，让"ıdc""ſemi""ſemi中国"
# 这种肉眼不像白名单条目的字符串被错误拔高成 OFFICIAL。
@pytest.mark.parametrize("name", ["ıdc", "ſemi", "ſemi中国"])
def test_critical2_非ascii大写折叠不得被拔高(name):
    assert tier_of(name) is Tier.MEDIA


# 方向对照：真正的同形字（西里尔 І、希腊 Ι、全角 ＩＤＣ/ｓｅｍｉ）不会
# 被 Python 的大写折叠变成 ASCII，本来就应该落 MEDIA——这条测试确认
# 修复没有反而误伤这些本就安全的输入。
@pytest.mark.parametrize("name", ["Іdc", "Ιdc", "ＩＤＣ", "ｓｅｍｉ"])
def test_critical2_真正同形字继续判为媒体(name):
    assert tier_of(name) is Tier.MEDIA


# ══════════════════════════════════════════════════════════════════════
# Task 5 证伪审查：Important 3——_MODIFIERS 英文侧此前一个词都没有，
# 英文机构的正式写法（法定全称、"Research"部门名、官方域名后缀）反而
# 落 MEDIA。补的这组 ASCII 修饰词经过保守选取，不包含 NEWS/DAILY/
# TIMES/WEEKLY/JOURNAL/BLOG/REPORT/INSIGHTS/WATCH/CHINA 这些媒体名
# 构件。
_IMPORTANT3_RECALL = [
    ("Gartner, Inc.", Tier.OFFICIAL),
    ("Gartner Research", Tier.OFFICIAL),
    ("IDC Research", Tier.OFFICIAL),
    ("Counterpoint Research", Tier.OFFICIAL),
    ("Omdia Research", Tier.OFFICIAL),
    ("semi.org", Tier.OFFICIAL),
    ("Goldman Sachs Research", Tier.SELLSIDE),
    ("Morgan Stanley Research", Tier.SELLSIDE),
    ("UBS Research", Tier.SELLSIDE),
]


@pytest.mark.parametrize("name, expected", _IMPORTANT3_RECALL)
def test_important3_英文修饰词补齐后召回(name, expected):
    assert tier_of(name) is expected


# 方向对照：明确不该加的媒体名构件（NEWS/DAILY/...）依然不是修饰词，
# 这些英文机构名 + 媒体构件的组合应当继续落 MEDIA——不是因为白名单
# 挡不住，而是因为它们本来就该被挡住。
@pytest.mark.parametrize("name", [
    "IDC China",  # CHINA 不是修饰词，理由与"中国"被删除相同
    "IC Insights Report",  # REPORT 是媒体名构件，明确不加
])
def test_important3_明确不加的修饰词继续落媒体(name):
    assert tier_of(name) is Tier.MEDIA


# ══════════════════════════════════════════════════════════════════════
# Task 5 证伪审查：Important 5——中信建投/申万宏源/国泰海通/海通国际
# 是市场惯用简称，但公告和研报封面落款用的是全称，此前全部落 MEDIA。
_IMPORTANT5_SELLSIDE_FULLNAMES = [
    "中信建投证券", "中信建投证券股份有限公司", "申万宏源证券",
    "国泰海通证券", "国泰海通证券研究所", "海通国际证券",
]


@pytest.mark.parametrize("name", _IMPORTANT5_SELLSIDE_FULLNAMES)
def test_important5_券商全称召回(name):
    assert tier_of(name) is Tier.SELLSIDE


# ══════════════════════════════════════════════════════════════════════
# Task 5 证伪审查：Important 4——_fully_modifiers 从递归回溯改成迭代
# 后缀可达性 DP。递归版本每多吃一层修饰词就多两个栈帧，约 500 个
# 修饰词 token 拼接就会撞上 RecursionError；publisher 来自模型输出，
# 不是可信输入，模型退化重复生成同一个字符是已知的失败模式。这里用
# "IDC" + "网" 除以数百次构造超长 publisher，走完整条 parse_findings
# 路径（而不是只测 _fully_modifiers 本身），断言不抛异常——危害本来
# 就不是"这一条发现被丢"，而是整批 parse_findings 被 RecursionError
# 冲掉，连同批次里正常的发现一起丢失。
def test_important4_超长publisher不引发递归错误():
    huge_publisher = "IDC" + "网" * 600
    text = (f"发现: utilization | 稼动率约78% | https://example.com/x | "
            f"{huge_publisher} | 2026-07-01\n")
    got = parse_findings(text)  # 不应抛 RecursionError
    assert len(got) == 1
    # "网"已从 _MODIFIERS 删除（见 Critical 1 的连带修法），600 个裸
    # "网"拼不满，落 MEDIA 是正确结果，不只是"没崩"。
    assert got[0].tier is Tier.MEDIA


def test_important4_超长remainder直接调用不引发递归错误():
    from ir_agent.sources import web as _web

    # 直接测 _fully_modifiers 本身，覆盖 parse_findings 之外可能的
    # 其它调用路径；用一个能被拼满的长串（全是"研究"）确认 DP 实现
    # 在深度很大时依然给出正确答案，不只是不崩溃。
    assert _web._fully_modifiers("研究" * 400) is True
    assert _web._fully_modifiers("网" * 600) is False


# ══════════════════════════════════════════════════════════════════════
# Task 5 证伪审查：补一条真正的不变式测试——白名单条目两两互不为
# **跨档**前缀（SELLSIDE 与 OFFICIAL 之间）。tier_of 的 candidates
# 排序与"优先试最长前缀"逻辑依赖这条不变式：一旦有一条 SELLSIDE 全称
# 是某条 OFFICIAL 全称的前缀（或反过来），同一个名字会同时命中两档，
# 结果取决于哪个候选先被排到、哪个候选的剩余部分先被修饰词拼满，
# 而不是取决于这家机构真正属于哪一档。
#
# 范围特意限定在**跨档**：同一档内的前缀对（比如 SELLSIDE 里"中信
# 建投"是"中信建投证券"的前缀，OFFICIAL 里"SEMI"是"SEMI中国"的
# 前缀）是安全的——它们是同一家机构的简称与全称/分支名，谁先命中都
# 给出相同的 Tier，不会导致误判方向。Important 5 与 Critical 1 的
# 修法都刻意引入了这类同档前缀对，所以不能把"两两互不为前缀"当成
# 对全部条目的无条件要求，只对跨档情形要求。
def test_不变式_白名单条目跨档互不为前缀():
    from ir_agent.sources import web as _web

    sell = {_web._normalize(x) for x in
            _web._SELLSIDE_CN + _web._SELLSIDE_ASCII}
    off = {_web._normalize(x) for x in
           _web._OFFICIAL_CN + _web._OFFICIAL_ASCII}

    冲突 = []
    for a in sell:
        for b in off:
            if a and b and a != b and (a.startswith(b) or b.startswith(a)):
                冲突.append((a, b))
    assert 冲突 == [], (
        f"SELLSIDE 与 OFFICIAL 之间存在互为前缀的条目 {冲突} —— tier_of "
        "会依据排序细节而非机构真实归属来判定分级，见 tier_of 里 "
        "candidates.sort 附近的说明"
    )


# 端到端：单条来源为"证券时报网"的发现走完 search() 后，
# supports_value() 必须判定不足以支撑取值——这是本模块存在的唯一理由，
# 分级机制的任何退化最终都要在这一步露馅。
def test_端到端_证券时报网单源不支撑取值(restore_web_capable):
    restore_web_capable.WEB_CAPABLE = True
    raw = ("发现: utilization | 稼动率约80% | https://example.com/x | "
           "证券时报网 | 2026-07-01\n")
    got = search(_Client(raw), "稼动率", "utilization", "封测", AS_OF)
    ok, why = supports_value(got)
    assert ok is False


def test_解析发现():
    got = parse_findings(OUT)
    assert len(got) == 2
    assert got[0].factor_key == "utilization" and "78%" in got[0].claim
    assert got[0].url.startswith("https://")
    assert got[0].published == date(2026, 7, 1)


def test_缺网址的发现被丢弃():
    """外部依据必须有 URL —— 没有网址就无从复核，与凭空捏造无异。"""
    assert parse_findings("发现: utilization | 稼动率很低 | 无 | 某处 | 2026-01-01") == []


def test_无日期的发现被丢弃():
    """发现二：无日期的发现如果被放行成 published=None，编排层将来会用
    `published=x.published or as_of` 把它装配成"发布日期恰好等于 as_of"
    ——这是唯一必然不会触发 OutOfWindow 的取值，等于给最不可信的证据
    最宽松的时点待遇。提示词里"必须给出网址"与"必须给出发布日期"是并列
    的硬性要求，两者应对称处理：日期缺失就该像网址缺失一样直接丢弃。"""
    got = parse_findings("发现: utilization | 稼动率很低 | https://a/b | 某处")
    assert got == []


def test_日期解析失败的发现被丢弃():
    """日期字段存在但解析不出来（既非 ISO 也非本模块认得的其它写法），
    同样不能放行成 None，必须整条丢弃。"""
    got = parse_findings(
        "发现: utilization | 稼动率很低 | https://a/b | 某处 | 说不清哪天")
    assert got == []


def test_日期兼容常见写法():
    """发现三：只认 ISO 会让模型写"2026年7月1日"这类常见写法时被误杀。
    配合发现二"解析失败即丢弃"的修法，容错太差会连累大量本来合格的发现。"""
    text = "\n".join([
        "发现: a | 说法一 | https://x/1 | 某处 | 2026-07-01",
        "发现: b | 说法二 | https://x/2 | 某处 | 2026/07/01",
        "发现: c | 说法三 | https://x/3 | 某处 | 2026年7月1日",
        "发现: d | 说法四 | https://x/4 | 某处 | 2026.07.01",
    ])
    got = parse_findings(text)
    assert len(got) == 4
    assert all(f.published == date(2026, 7, 1) for f in got)


def test_全为媒体来源不问数量都不足以支撑取值():
    """发现四：三条发现分别来自三家不同的财经媒体，仍然全是 MEDIA 级，
    不该通过 —— 门槛是来源等级，不是数量。措辞里若写"单一媒体来源"，
    会误导读者以为多找几家媒体互相印证就能过关，而真正的门槛是来源
    等级，不是数量。"""
    media = [
        Finding("utilization", "稼动率下降", "https://a/1", "第一财经",
                Tier.MEDIA, date(2026, 1, 1)),
        Finding("utilization", "稼动率下降", "https://a/2", "界面新闻",
                Tier.MEDIA, date(2026, 1, 2)),
        Finding("utilization", "稼动率下降", "https://a/3", "澎湃新闻",
                Tier.MEDIA, date(2026, 1, 3)),
    ]
    ok, why = supports_value(media)
    assert ok is False
    assert "单一" not in why
    assert ("等级" in why or "数量" in why)


def test_单一媒体来源不足以支撑取值():
    """现有分级管的是「能不能引用」；因子取值是要进模型算钱的，门槛更高。
    一条财经媒体报道可以作为线索，不该单独决定 DCF 里的产能增速。"""
    media = [Finding("utilization", "稼动率下降", "https://a/b", "某财经网",
                     Tier.MEDIA, date(2026, 1, 1))]
    ok, why = supports_value(media)
    assert ok is False and "媒体" in why and "单一" not in why


def test_有官方或卖方佐证则可支撑():
    mixed = [Finding("utilization", "约78%", "https://semi/x", "SEMI",
                     Tier.OFFICIAL, date(2026, 1, 1)),
             Finding("utilization", "下降5pp", "https://a/b", "某财经网",
                     Tier.MEDIA, date(2026, 1, 2))]
    ok, _ = supports_value(mixed)
    assert ok is True


def test_无发现时明确说无():
    ok, why = supports_value([])
    assert ok is False and "未找到" in why


def test_不可联网时抛出而非静默返回空(restore_web_capable):
    """静默返回空会让「搜不到」和「没搜」长得一样。

    发现五：异常信息不应硬编码"claude -p 不能联网"——探测的是默认行为，
    不是能力上限。措辞该指向真正该查的地方（谁关了 WEB_CAPABLE 这个
    开关），而不是把排查者引向"怀疑网络能力"这个错误方向。
    """
    restore_web_capable.WEB_CAPABLE = False
    with pytest.raises(restore_web_capable.WebUnavailable, match="WEB_CAPABLE"):
        search(_Client(OUT), "稼动率", "utilization", "封测", AS_OF)


def test_提示词要求给出网址与日期(restore_web_capable):
    restore_web_capable.WEB_CAPABLE = True
    c = _Client(OUT)
    got = search(c, "稼动率", "utilization", "集成电路封测", AS_OF)
    assert len(got) == 2
    assert "网址" in c.prompts[0] and "发布日期" in c.prompts[0]
    assert "集成电路封测" in c.prompts[0] and "稼动率" in c.prompts[0]


def test_提示词对日期缺失的后果说明与网址对称():
    """发现：URL 那条明写"没有网址的条目会被直接丢弃"，日期那条此前只写
    "晚于 as_of 的会被剔除"，没说清"日期缺失/写不全会被整条丢弃"这个
    实际存在的后果 —— 模型按提示词字面理解，会以为"2026年7月"这种月份
    粒度的日期能蒙混过关，实际上会被 parse_findings 静默丢弃，召回率
    下降却没有任何信号。两条硬性要求的后果说明必须对称。

    注意：不能只在整段 PROMPT 里搜"丢弃"存不存在——网址那条的"丢弃"
    出现在日期那条前面，若只做整体包含检查，即便日期那条完全没提"丢弃"
    也会因为读到网址那条的文字而误判通过。必须把"必须给出发布日期"这
    一条硬性要求单独切出来（到下一条硬性要求或段落结尾为止）再检查。
    """
    prompt = PROMPT.format(industry="封测", factor="稼动率",
                           key="utilization", as_of=AS_OF.isoformat())
    start = prompt.index("**必须给出发布日期")
    end = prompt.index("- 数据要具体")
    date_bullet = prompt[start:end]
    assert "丢弃" in date_bullet


def test_晚于as_of的发现被剔除(restore_web_capable):
    """拿未来的资料分析过去 —— 与账本的时点守卫是同一件事。"""
    restore_web_capable.WEB_CAPABLE = True
    late = ("发现: utilization | x | https://a/b | SEMI | 2026-12-01\n"
            "发现: utilization | y | https://a/c | SEMI | 2026-01-01\n")
    got = search(_Client(late), "稼动率", "utilization", "封测", AS_OF)
    assert len(got) == 1 and got[0].claim == "y"
