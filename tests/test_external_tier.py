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
