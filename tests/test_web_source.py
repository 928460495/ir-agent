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
# "央行"），白名单就悄悄退化回子串匹配的效果。web.py 里对此只有一句
# 注释警告，注释拦不住任何人，所以在这里变成可执行的守卫。
#
# 阈值取 3 是有实测依据的，不是拍脑袋：现有全部条目最短就是 3
# （IDC / SIA / UBS / 上交所 / 北交所 / 发改委），而所有危险裸片段都
# 恰好是 2。真要加一个 2 字符的合法机构全称，改的应该是这条测试并
# 说明理由，而不是让它默默通过。
def test_结构哨兵_白名单不得含短片段():
    from ir_agent.sources import web as _web

    entries = (_web._SELLSIDE_CN + _web._SELLSIDE_ASCII
               + _web._OFFICIAL_CN + _web._OFFICIAL_ASCII)
    短片段 = [e for e in entries if len(_web._normalize(e)) < 3]
    assert 短片段 == [], (
        f"白名单里混进了短片段 {短片段} —— 这会让白名单退化回子串匹配，"
        "见 web.py 中 tier_of 的设计契约"
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
