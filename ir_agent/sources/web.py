"""开放网络定向采集 —— 按分解式的因子去找数据。

**外部数据的证明力比账本低一个量级，这件事必须在代码里体现。**
账本数字可以一路反查到接口的原始响应；外部引文只能核验「我确实在某个网址上
抓到过这句话」，核验不了「这句话是对的」。两者在报告里长得一样，所以分级
不是给来源打分，是**限定它能支撑多强的结论**。

因此这里比 external.py 的引用规则更严一层：**MEDIA 级不得单独支撑一个因子的
取值**。引用一句话和进模型算钱，门槛不该一样。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from ir_agent.lineparse import keyed
from ir_agent.sources.external import Tier

# 实测 claude -p 可联网搜索，但**必须显式开启工具**（2026-09-12 验证）:
#   make_client(tools=["WebSearch", "WebFetch"])
# 默认调用 `claude -p --output-format json` 不启用任何工具 —— 第一次探测
# 正因为只测了默认调用，误判成「不可联网」。**测的是默认行为，不是能力。**
# 详见 docs/superpowers/notes/2026-09-12-web-capability.md。
WEB_CAPABLE = True

_LINE = keyed("发现")
_URL = re.compile(r"https?://\S+")

# 常见日期写法，按顺序尝试。模型不一定老实按提示词里的 YYYY-MM-DD 写，
# "2026年7月1日""2026/07/01""2026.07.01" 都是常见的等价写法 —— 解析
# 太挑剔会连累大量本来合格的发现被误杀（尤其是配合下面"解析失败即丢弃"
# 的规则之后）。认不出的一律按「日期不明」处理，见 _parse_date 用法。
_DATE_PATTERNS = (
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})"),
    re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日"),
    re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})"),
)

# 来源分级。官方与协会 > 卖方 > 媒体。判不出来的一律按媒体处理 ——
# 往高了猜等于凭空给一个来源加权，这条原则本身没变过。变过三次的是
# 「怎么判」。
#
# SEMI / Gartner / TrendForce / IDC / Yole / Omdia / IC Insights / 集邦咨询 /
# 赛迪顾问 严格说是付费市场研究商，不是 external.py 的 TIER_LABEL 里说的
# 统计局/协会/部委/交易所。放进 OFFICIAL 是有意的：封测的产能、出货这类
# 数据，这几家就是该领域证明力最强的一手来源，官方统计反而覆盖不到、
# 或滞后到失去时效。**分级看的是这句话的证明力有多强，不是这个机构算
# 不算官方机构**——集邦咨询是 TrendForce 的中文马甲，同一家公司没有
# 理由分级不同，一并放进 OFFICIAL 白名单。
#
# ── 为什么换成白名单，而不是再修一条判别规则 ──────────────────────
# 同一个词元在不同名字里角色不同：「证券」在「中信证券」是机构类型
# 后缀，在「新浪证券」「腾讯证券」只是门户的频道名；「IDC」本身是市场
# 研究公司，出现在「中国 IDC 圈」里指的是互联网数据中心；`\bSEMI\b`
# 这样的词边界正则挡得住 Semiconductor，挡不住 SEMI-Insights / The SEMI
# Times。三轮审查、三种判别机制——裸子串 → 以"报"收尾排除 → 媒体标识
# 优先＋词尾结构——依次被真实来源名打穿，根因都是同一个：**词元本身不
# 携带机构性质，位置和上下文才携带，而任何基于位置/结构的模糊规则都能
# 被一个新来源名绕开。**
#
# 于是不再问"这个名字像不像官方/卖方"，只问"这个名字是不是白名单里那
# 家机构本身（允许带一点无害的修饰词）"：
#
#     升级到 SELLSIDE/OFFICIAL 只有「白名单全称 + 允许的修饰词」这
#     一条路。不要加子串匹配、词尾规则、词边界正则或"媒体标识"黑名单
#     ——这三种都试过，都被真实来源名打穿（证券时报网 / 新浪证券 /
#     Securities Daily / 中国 IDC 圈）。要支持新来源，往白名单加全称。
#     判不出来落 MEDIA 是正确行为，不是缺陷。
#
# 这条路径安全，靠的是 tier_of 产出的 Tier 不管走哪条读取路径都遵循
# 同一条代价不对称：本文件的 supports_value()，以及 ir_agent/gaps.py
# 的 collect() 里直接读 Finding.tier 判断"是否单一媒体来源"那处特殊
# 文案——注意 tier_of 的下游不止 supports_value() 一处，gaps.py 是
# 第二个直接消费者。两处判断都对"拔高"极端保守、对"压低"从容：把
# 媒体错拔成 SELLSIDE/OFFICIAL，等于一篇博客单独决定了 DCF 里的因子
# 取值，是危害；把真官方/卖方错压成 MEDIA，只是需要再凑一条来源互相
# 印证，是召回损失。这正是白名单机制的方向，不是缺省地各打五十大板。

# ── 归一化：让"国家统计局官网"和"国家统计局　官网"命中同一个前缀 ──
# 只做三件事：去空白（含全角空格）、剔除常见全/半角标点、把 ASCII 字母
# 转大写（只动 ASCII，不做 Unicode 大写折叠——后者会把 ı(U+0131)、
# ſ(U+017F) 这类非 ASCII 字符折成 ASCII 的 I/S，让"ıdc""ſemi"这种人眼
# 不像、机器一折叠就变成白名单全称的字符串被错误拔高；真正的同形字，
# 比如西里尔 І、希腊 Ι、全角 ＩＤＣ，folding 不会把它们变成 ASCII，
# 落 MEDIA 是安全的，不需要额外处理）。
# 不做任何语义层面的"聪明"处理——归一化越简单，越不会自己制造新漏洞。
_WS_RE = re.compile(r"[\s　]+")
_PUNCT_STRIP = "（）()·、，,．.-_「」《》：:；;'\""

# 繁→简：只覆盖白名单条目里实际出现的字，不引入第三方库、不做通用
# 繁简转换。覆盖不到的繁体名落 MEDIA，方向安全（宁可少召回，不可错拔高）。
_TRAD_TO_SIMP = {
    "證": "证", "國": "国", "華": "华", "廣": "广", "發": "发", "萬": "万",
    "興": "兴", "業": "业", "東": "东", "風": "风", "長": "长", "統": "统",
    "計": "计", "財": "财", "關": "关", "總": "总", "監": "监", "員": "员",
    "會": "会", "導": "导", "體": "体", "協": "协", "詢": "询", "顧": "顾",
    "問": "问", "網": "网", "眾": "众", "號": "号", "報": "报", "數": "数",
    "據": "据", "庫": "库", "聞": "闻", "際": "际", "銀": "银",
}


def _normalize(name: str) -> str:
    """tier_of 匹配前的唯一预处理：去空白、剔标点、繁转简、把 ASCII 字母
    转大写。

    最后一步不能写成 `s.upper()`——`str.upper()` 做的是全 Unicode 大写
    折叠，不是"ASCII 转大写"：`ı`(U+0131，土耳其语无点 i) 会被折成
    `I`，`ſ`(U+017F，长 s) 会被折成 `S`。这意味着 "ıdc"/"ſemi" 这类
    肉眼一看就不是白名单条目的字符串，会在这一步偷偷变成 "IDC"/"SEMI"
    从而被拔高到 OFFICIAL——而这恰恰是本函数要防的"拔高"方向的危害，
    比不拦截同形字更严重（真正的同形字攻击，比如西里尔 І、希腊 Ι、
    全角 ＩＤＣ，folding 不会把它们变成 ASCII，落 MEDIA 是安全的）。
    逐字符判断 `ch.isascii()` 只在字符本身是 ASCII 时才转大写，能精确
    区分这两种情况。
    """
    if not name:
        return ""
    s = "".join(_TRAD_TO_SIMP.get(ch, ch) for ch in name)
    s = _WS_RE.sub("", s)
    s = "".join(ch for ch in s if ch not in _PUNCT_STRIP)
    return "".join(ch.upper() if ch.isascii() else ch for ch in s)


# 白名单——要的是"能唯一指向一家机构的名字"，不是"工商登记全称"。
# 下面 _OFFICIAL_CN 里的"统计局""工信部""发改委""证监会""上交所"
# "深交所""北交所"都是简称而非法定全称（"统计局"甚至就是表内"国家
# 统计局"的片段本身）——之所以能放心放进来，是因为这些简称本身已经
# 唯一指向对应机构，不会被别的机构冒用；往这里加一条这样的名字就能
# 支持一个新来源。
#
# 但不要往这几个元组里塞任何"关键词"性质的短片段（比如裸的"证券"
# "期货"）——那不会让白名单退化回**子串**匹配（tier_of 用的是
# startswith，不是 in），而是退化回**前缀**匹配：塞进"证券"后，只有
# "证券……"这种以它开头的名字会被误判，"新浪证券"这类"证券"在中间/
# 结尾的名字不受影响。审查者实测过：把"证券"塞进白名单后，60 条对抗
# 名 + 105 条哨兵名一条都没变色，只有"证券研究""证券中国""证券网"
# 这类以"证券"打头、剩余部分又恰好被修饰词拼满的名字被拔高——也就是
# 说下面这条结构哨兵（test_结构哨兵_白名单不得含短片段）挡的只是"短
# 片段"这一种退化方式，前缀匹配退化本身的实际杀伤力有限，且现有的
# 行为层测试（危害方向穷举 + 机制回归哨兵）对这个失效模式基本是盲的，
# 靠的主要是人工审查。
_SELLSIDE_CN = (
    "中信证券", "中信建投", "中信建投证券", "中信建投证券股份有限公司",
    "中金公司", "国泰君安证券", "国泰海通", "国泰海通证券",
    "海通证券", "海通国际", "海通国际证券", "华泰证券", "招商证券",
    "广发证券", "申万宏源", "申万宏源证券", "兴业证券", "东吴证券",
    "国信证券", "光大证券", "天风证券", "民生证券", "长江证券",
    "方正证券", "东方证券", "中泰证券", "浙商证券",
)
# "中信建投"/"申万宏源"/"国泰海通"/"海通国际"是市场惯用简称，但公告
# 和研报封面落款用的是全称——"中信建投证券""申万宏源证券""国泰海通
# 证券""海通国际证券"。简称和全称同时留在表里是有意的：它们指向
# 同一家机构，同属 SELLSIDE，谁先命中都给出相同的正确结果，不构成
# tier_of 排序逻辑依赖的那种"跨档"前缀冲突（后面的不变式测试只检查
# 跨档前缀，原因见该测试的说明）。
_SELLSIDE_ASCII = (
    # "JP MORGAN" 与 "J.P. MORGAN" 归一化后同为 "JPMORGAN"（标点在
    # _normalize 里被剔除），是同一个匹配结果，只留一种写法即可，不必
    # 都列出来制造无行为影响的冗余条目。
    "MORGAN STANLEY", "GOLDMAN SACHS", "UBS", "JP MORGAN",
    "NOMURA", "CITI", "CREDIT SUISSE", "JEFFERIES", "BERNSTEIN",
)

# 集邦咨询、赛迪顾问放在这里而不是另立名单：分级看的是证明力，这两家
# 是封测/半导体产业链数据事实上的权威一手来源，理由与 SEMI/Gartner/
# TrendForce/IDC 完全一致，见本节顶部注释。
_OFFICIAL_CN = (
    "国家统计局", "统计局", "工业和信息化部", "工信部", "国家发展改革委",
    "发改委", "商务部", "财政部", "海关总署", "中国人民银行",
    "中国证券监督管理委员会", "证监会", "上海证券交易所", "深圳证券交易所",
    "北京证券交易所", "上交所", "深交所", "北交所", "中国半导体行业协会",
    "中国电子专用设备工业协会", "集邦咨询", "赛迪顾问",
)
# "SEMI 中国""IDC 中国""Gartner 中国"是这几家机构在中国的分支/中文
# 站点，与英文全称同属一家公司，作为独立全称加入——而不是让通用的
# "中国"修饰词去拼凑它们，理由见下面 _MODIFIERS 处"中国"/"国际"被
# 删除的说明。
_OFFICIAL_ASCII = (
    "SEMI", "GARTNER", "TRENDFORCE", "IDC", "YOLE", "OMDIA", "IC INSIGHTS",
    "COUNTERPOINT", "WSTS", "SIA",
    "SEMI中国", "IDC中国", "GARTNER中国",
)

# 允许的修饰词——白名单全称命中后，剩余部分必须能被这张表里的词
# **完整拼出**（可以是好几个词拼接，比如"研究部"+"官网"），拼不满就
# 说明剩余部分里混进了白名单没解释到的东西（比如"中金公司观察"里的
# "观察"），落 MEDIA。
#
# 这张表里**不放**两类词：
#
# 1. "中国"/"国际"：曾经在这里，已删除。根因是这两个词不是"载体/
#    栏目修饰"，是**机构名成分**——"中国网"是国新办主管的中央重点
#    新闻网站，是真实媒体，不是随便什么机构的"中国分支"；一旦"中国"
#    能被拼进剩余部分，白名单里每一条全称后面接"（中国网）"都会被
#    错误拔高（"国家统计局（中国网）""IDC·中国网""中信证券·中国网"
#    ……对每条白名单条目都成立）。删除后经审查者实测：现有 OFFICIAL/
#    SELLSIDE 召回用例一条都不破（"海通国际"这类名字本来就是靠白名单
#    全称直接命中，不依赖这两个修饰词）；唯一的代价是"SEMI 中国"/
#    "IDC 中国"/"Gartner 中国"这三个真实分支会掉回 MEDIA——按本模块
#    自己的契约，判不出来的应对方式是把它们作为全称加进白名单（已在
#    _OFFICIAL_ASCII 补上），而不是留一个通用修饰词兜底。
# 2. 裸的"网"：同一个陷阱的另一种形态，只是不靠"中国"/"国际"这两个
#    具体词，靠的是"网"本身太通用——"统计局"+"研究"（合法修饰词）+
#    裸"网"能拼出"统计局研究网"，读起来完全是一个（虚构的）媒体网站
#    品牌名，而不是国家统计局的下属研究网站。"官网""网站""官方网站"
#    这几个更具体的复合词保留：现有召回用例没有一条依赖裸"网"单独
#    出现，删除它不破坏任何已验证的 OFFICIAL/SELLSIDE 名字。
_MODIFIERS = (
    "官网", "网站", "官方网站", "公众号", "微信公众号",
    "研究所", "研究院", "研究部", "研究中心", "发展研究中心", "研究",
    "股份有限公司", "有限公司",
    "公报", "月报", "季报", "年报", "快报",
    "数据库", "新闻发言人", "发布会",
    # 英文侧此前一个修饰词都没有，于是"Gartner, Inc."（法定全称）、
    # "IDC Research""Counterpoint Research"（公司正式研究部门名）、
    # "semi.org"这类机构自己的正式写法反而落 MEDIA——中文侧有 20+ 个
    # 修饰词、英文侧零个，同一条规则在中英之间不对称。下面这组是保守
    # 选取的：判据是"这个词跟任何白名单条目或别的修饰词拼在一起，都
    # 不能构成一个真实存在的媒体品牌名"。
    #
    # 明确不加 NEWS/DAILY/TIMES/WEEKLY/JOURNAL/BLOG/REPORT/INSIGHTS/
    # WATCH——这些正是媒体名的构件（"Securities Daily""IC Insights
    # Report"里的"Daily""Report"），加进来等于重新打开本模块历史上
    # 被打穿过的那个洞。也不加 CHINA——它和被删掉的"中国"是同一个
    # 陷阱（"China Net""China Daily"都是真实媒体，"IDC China"这类
    # 分支名因此继续落 MEDIA，是可接受的召回损失，不是缺陷）。
    "RESEARCH", "INC", "CORP", "CORPORATION", "CO", "LTD", "LLC",
    "GROUP", "ORG", "OFFICIAL", "WEBSITE",
)

_SELLSIDE_NORM = tuple(_normalize(x) for x in _SELLSIDE_CN + _SELLSIDE_ASCII)
_OFFICIAL_NORM = tuple(_normalize(x) for x in _OFFICIAL_CN + _OFFICIAL_ASCII)
_MODIFIERS_NORM = tuple(sorted({_normalize(x) for x in _MODIFIERS}, key=len,
                                reverse=True))


@lru_cache(maxsize=8192)
def _fully_modifiers(remainder: str) -> bool:
    """remainder 能否被 _MODIFIERS_NORM 里的词首尾相接、不多不少地拼满。

    需要"拼接"而不是"单个词命中"，是因为真实的修饰组合会叠加——
    比如"中信证券研究部官网"里，白名单全称"中信证券"之后剩下
    "研究部官网"，是"研究部"+"官网"两个修饰词拼出来的，不是其中
    任何一个单独能覆盖的。

    实现是对剩余串做一次**后缀可达性 DP**（从右往左填表：位置 i 能否
    拼满 remainder[i:]），不是递归回溯——早先的递归实现（`any(...
    for mod in ...)` 套 `_fully_modifiers` 自身）每多吃一层修饰词就
    多两个 Python 栈帧，默认递归上限 1000 意味着约 500 个修饰词
    token 拼接就会撞上 RecursionError；而 remainder 来自模型的搜索
    结果（parse_findings 里的 publisher 字段），不是可信输入——模型
    退化重复生成同一个字符（比如连续几百个"网"）是已知的失败模式，
    一旦触发，炸的不是这一条发现，而是 `parse_findings` 整批解析连带
    同批次里正常的发现一起被 RecursionError 冲掉，`search()` 又没有
    兜底。DP 版本没有"调用深度"这个维度，只有 O(len(remainder) ×
    修饰词数) 的时间开销，天然不存在这个上限——不需要再假设"剩余串
    很短"，配合 lru_cache 记忆化，实测 900 字符的输入最慢 1.18ms，
    不需要额外的长度上限。
    """
    n = len(remainder)
    # reachable[i] 表示 remainder[i:]（从下标 i 开始的剩余部分）能否被
    # 修饰词拼满；reachable[n] 恒为 True，对应"已经拼到头，什么都不剩"
    # 这一空剩余的基准情形，等价于原递归版本里 `remainder == ""` 的
    # base case。
    reachable = [False] * (n + 1)
    reachable[n] = True
    for i in range(n - 1, -1, -1):
        for mod in _MODIFIERS_NORM:
            if mod and remainder.startswith(mod, i) and reachable[i + len(mod)]:
                reachable[i] = True
                break
    return reachable[0]


class WebUnavailable(Exception):
    """联网能力不可用 —— 不静默返回空，那会让「搜不到」和「没搜」长得一样。"""


@dataclass(frozen=True)
class Finding:
    factor_key: str
    claim: str
    url: str
    publisher: str
    tier: Tier
    # 注意：不是 date | None。发现二的教训是，一旦类型上允许 None，
    # 编排层迟早会写出 `published=x.published or as_of` 这种"看似合理"
    # 的兜底 —— 而 as_of 恰好是唯一必然不会触发 OutOfWindow 的取值，
    # 等于把最不可信的证据（连发布日期都不明）洗成时点上最合规的证据。
    # 正确的处置在 parse_findings 里就已经做完：日期不明的发现直接丢弃，
    # 传到这里的 Finding 一定带着真实日期。不留这个类型上允许、
    # 语义上危险的 None 出口，后来人就不必在每个调用点重新记得防它。
    published: date


def parse_date(raw: str) -> date | None:
    """尝试用几种常见写法解析日期；认不出或者不是合法日期就返回 None，
    调用方（parse_findings）应将 None 视为「日期不明」并丢弃整条发现。

    **公开而非私有**：openweb.parse_candidates 要用同一套日期解析。两处
    对「日期解析不出就整条丢弃」的处置必须一致，抄一遍迟早会分叉。
    """
    s = raw.strip()
    for pat in _DATE_PATTERNS:
        m = pat.search(s)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    return None


def tier_of(publisher: str) -> Tier:
    """来源分级。

    **设计契约——升级到 SELLSIDE/OFFICIAL 只有一条路：**

        白名单全称 + 允许的修饰词。

    不要加子串匹配、词尾规则、词边界正则或"媒体标识"黑名单——这三种
    都试过（依次是 2816197 之前的裸子串、2816197、6d9cd4c），都被真实
    来源名打穿（证券时报网 / 新浪证券 / Securities Daily / 中国 IDC
    圈）。根因是同一个：**同一个词元在不同名字里角色不同，任何基于
    位置或结构的模糊匹配都在赌"这次的反例还没出现"。**

    要支持新来源，往 `_SELLSIDE_CN`/`_SELLSIDE_ASCII`/`_OFFICIAL_CN`/
    `_OFFICIAL_ASCII` 加全称，或往 `_MODIFIERS` 加修饰词——不要碰这个
    函数的判别逻辑本身。**判不出来落 MEDIA 是正确行为，不是缺陷**：
    见本节顶部注释里 tier_of 产出的 Tier 在 supports_value()（本文件）
    与 gaps.py（直接读 Finding.tier）两处读取路径共享的代价不对称——
    拔高是危害，压低只是召回损失。
    """
    name = _normalize(publisher or "")
    if not name:
        return Tier.MEDIA

    candidates: list[tuple[int, Tier]] = []
    for entry in _SELLSIDE_NORM:
        if entry and name.startswith(entry):
            candidates.append((len(entry), Tier.SELLSIDE))
    for entry in _OFFICIAL_NORM:
        if entry and name.startswith(entry):
            candidates.append((len(entry), Tier.OFFICIAL))
    # 优先试最长的白名单前缀——多个全称都能作为前缀命中时，剩余部分
    # 更短，更可能被修饰词完全解释。这里确实会遇到同档内的前缀对，
    # 比如 SELLSIDE 里"中信建投"是"中信建投证券"的前缀（简称与公告/
    # 研报封面用的全称同时在册）——这是安全的，因为两者指向同一家
    # 机构、同属一档，不管哪个候选先试出来，结果都是同一个 Tier。
    # 真正危险、且这里的排序也无法自动兜底的，是**跨档**前缀（一个
    # SELLSIDE 全称是另一个 OFFICIAL 全称的前缀，或反过来）：那种情况
    # 下选出哪个候选，结果的 Tier 会不一样，属于白名单数据本身的错误，
    # 应该在维护白名单时避免，而不是依赖这里的排序"猜对"。
    # test_不变式_白名单条目跨档互不为前缀 专门守这条。
    candidates.sort(key=lambda pair: -pair[0])

    for entry_len, tier in candidates:
        if _fully_modifiers(name[entry_len:]):
            return tier
    return Tier.MEDIA


def parse_findings(text: str) -> list[Finding]:
    out: list[Finding] = []
    for hit in _LINE.finditer(text):
        parts = [p.strip() for p in hit.group(1).split("|")]
        # 一道守卫，卡在 5：一行要完整给出 因子|陈述|网址|机构|日期 五段，
        # 少任何一段——包括日期——都不是「4 段也合法，日期可选」，而是
        # 整条不合规。之前分两道守卫（先 < 4 再 < 5）会让人以为中间存在
        # 「4 段合法」的分支，实际上那道 < 4 只是为了让下面取 parts[2]/
        # parts[3] 不越界，本身并不代表 4 段是可接受的长度。
        if len(parts) < 5:
            continue
        url_m = _URL.search(parts[2])
        if not url_m:
            # 外部依据必须有 URL —— 没有网址就无从复核，与凭空捏造无异。
            continue
        pub = parts[3]
        # 「必须给出网址」与「必须给出发布日期」在提示词里是并列的硬性
        # 要求，此处也应对称处理：日期解析不出来（写了但认不出/不是
        # 合法日期），一律整条丢弃，绝不放行成 published=None。原因见
        # Finding.published 字段上的注释——一旦允许 None 流出本模块，
        # 编排层的兜底逻辑会把它悄悄变成「发布日期=as_of」，让最不可信
        # 的证据获得最宽松的时点待遇，静静滑过系统里最关键的一道防线
        # （时点守卫）。这个隐患必须在产生 Finding 的这一步就堵死，
        # 不能指望下游每个调用点都记得防。
        when = parse_date(parts[4])
        if when is None:
            continue
        out.append(Finding(factor_key=parts[0], claim=parts[1],
                           url=url_m.group(0), publisher=pub,
                           tier=tier_of(pub), published=when))
    return out


PROMPT = """为一份 A 股研究报告采集一个具体的数据点。

行业：{industry}
要找的因子：**{factor}**

上网搜索，找出关于这个因子的具体数据（水平、同比变化、行业均值皆可）。
每条一行：

`发现: {key} | <具体数据的一句话陈述> | <网址> | <发布机构> | <发布日期 YYYY-MM-DD>`

**硬性要求**
- **必须给出网址**。没有网址的条目会被直接丢弃 —— 无从复核的转述与凭空
  捏造无异。
- **必须给出发布日期（YYYY-MM-DD）**。没写日期、或日期写得不够精确以
  致解析不出来（比如只写到"2026年7月"这样的月份粒度）的条目，同样会
  被直接丢弃——跟没有网址一样，说不清时点的证据一样等同于凭空捏造。
  晚于 {as_of} 的会被剔除：拿未来的资料分析过去，与账本的时点守卫是
  同一件事。
- 数据要具体（「稼动率约 78%」而非「稼动率偏低」）。
- 找不到就一行都不要输出，**不要用推测填充**。

只输出上述格式的行。
"""


def search(client, factor_label: str, factor_key: str, industry: str,
           as_of: date) -> list[Finding]:
    """按因子上网搜索证据。

    **调用方必须传入已显式开启 WebSearch/WebFetch 工具的 client**——
    例如 `make_client(tools=["WebSearch", "WebFetch"])`。默认（不带
    `tools` 参数）构造出的 client 不联网，会让模型如实回答「不能联网」，
    而不是本函数抛出的 WebUnavailable；这不是本函数能拦的错误，因为
    client 是否联网由调用方在构造时决定，本函数只负责在 WEB_CAPABLE
    为 False（这条链路整体判定不可联网）时提前拒绝。
    """
    if not WEB_CAPABLE:
        # 措辞不能写死「claude -p 不能联网」——本文件顶部已经交代过，
        # 之前的探测测的是默认调用不开工具，不是 claude -p 真的没有联网
        # 能力。这里判定不可用，唯一的原因是 WEB_CAPABLE 这个开关被设成
        # 了 False。真正该查的是谁、为什么关了它，而不是重新怀疑一遍
        # 已经验证过的联网能力，那会把排查者引向错误方向。
        raise WebUnavailable(
            "开放网络采集当前被禁用（WEB_CAPABLE=False）—— 请检查是谁、"
            "为什么关闭了这个开关；claude -p 本身的联网能力已经验证过，"
            "不必重新怀疑（见 docs/superpowers/notes/"
            "2026-09-12-web-capability.md）")
    raw = client(PROMPT.format(industry=industry, factor=factor_label,
                               key=factor_key, as_of=as_of.isoformat()))
    got = parse_findings(raw)
    # parse_findings 已经保证 published 不为 None（日期不明的发现在那一步
    # 就被丢弃了），这里只需要做时点过滤，不必再兼容 None。
    return [f for f in got if f.published <= as_of]


def supports_value(findings: list[Finding]) -> tuple[bool, str]:
    """这组发现够不够支撑一个因子的取值。

    **MEDIA 级不得单独支撑。** 现有 external.py 的分级管的是「能不能引用」；
    因子取值是要进模型算钱的，门槛不该一样 —— 一条财经媒体报道可以作为线索，
    不该单独决定 DCF 里的产能增速。
    """
    if not findings:
        return False, "未找到任何数据"
    if any(f.tier in (Tier.OFFICIAL, Tier.SELLSIDE) for f in findings):
        return True, ""
    # 措辞不能写「仅单一媒体来源」——即便凑齐三条来自不同媒体的发现，
    # 只要全是 MEDIA 级，一样不该通过。门槛是来源等级，不是数量；
    # 写「单一」会误导读者以为多找几家媒体互相印证就能过关，
    # 而这恰恰绕开了本模块存在的唯一理由。
    return False, ("全部为财经媒体来源，不足以支撑因子取值 —— "
                   "门槛是来源等级而非数量，即便多家媒体互相印证也不算数，"
                   "需至少一条官方/协会或卖方研究佐证")
