"""来源分级的第四级：上市公司法定披露。

**分级跟着来源的取回方式走，不跟着名字走。** 本文件最要紧的一条是
`test_不变式_tier_of绝不产出COMPANY`——见其文档字符串。
"""

from __future__ import annotations

import inspect

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


# ── 结构性哨兵：上面两条不变式测试是"抽样"，不是"穷举" ──────────────
#
# `test_不变式_tier_of绝不产出COMPANY` 只喂了白名单里现有的 ~20 条条目，
# 外加几个手选的探针字符串（"生益科技"等）。它证明的是**当前这份白名单
# 和这几个探针**不会触发 COMPANY，证明不了"任何输入都不会"——如果未来
# 有人往 web.py 加一条新白名单（比如 `_COMPANY_CN = ("宁德时代", ...)`）
# 并在 `tier_of` 里加一个返回 `Tier.COMPANY` 的分支，探针列表只要没有
# 恰好撞上新白名单的条目（这里只有唯一一个"生益科技"探针，撞上的概率
# 极低），两条不变式测试会保持全绿——现有测试对这类退化基本是盲的。
# `tier_of_url` 与假想的 `_COMPANY_HOSTS` 同理。
#
# 结构性哨兵换一个问法：不问"这些具体输入会不会触发 COMPANY"，而问
# "这个函数的字节码里有没有任何地方提到 COMPANY 这个名字"。`Tier.COMPANY`
# 是一次属性访问，Python 会把字符串 `"COMPANY"` 编译进发生这次访问的
# 那个函数自己的 `__code__.co_names` 里；不管分支判据用的是白名单、
# 正则还是别的什么方法，只要函数体里出现了 `Tier.COMPANY`（或
# `getattr(Tier, "COMPANY")` 这类等价写法），"COMPANY" 就必然出现在
# co_names 里。这就把"穷举所有可能输入"换成了"穷举函数体本身"，覆盖面
# 不再依赖探针列表撞没撞上未来的白名单。
#
# **不能改问"COMPANY 这个词出现在源代码文本里吗"。** 一个负责任的维护者
# 完全可能在 web.py 里写一条注释——`# COMPANY 只能由巨潮适配器赋予，
# 不要加进这里的白名单`——这条注释本身是在正确地强调约束，但会让"源码
# 文本里搜 COMPANY"这种测试跟着炸；一个测试被一句正确的注释打红，最终
# 结局是被删掉，而不是被听从。字节码不同：注释和文档字符串从不出现在
# `co_names`/`co_consts` 里，只有真正的属性访问、变量名、字面量常量才
# 会被编译进去。
#
# 嵌套函数（闭包）的 code 对象不在外层函数的 `co_names` 里，而是作为
# `types.CodeType` 常量出现在外层函数的 `co_consts` 里，所以要递归。
# 同理，`_fully_modifiers` 之类被 `functools.lru_cache` 包裹的函数，
# `inspect.isfunction` 认不出它（包裹后的类型是
# `functools._lru_cache_wrapper`），要先 `inspect.unwrap` 拿到原始
# 函数才能读到它的 `__code__`；dataclass（`Finding`/`Candidate`）生成
# 的 `__init__`/`__eq__` 等方法也一并按"类方法"扫描进来，避免只查两个
# 入口函数、放过它们调用的辅助函数或类里的方法。


def _co_names_deep(code: object, _seen: set[int] | None = None) -> set[str]:
    """`code.co_names` 与其所有嵌套 code 对象（闭包/内部 def）的
    `co_names` 之并集。

    属性访问（`Tier.COMPANY`）把字符串 `"COMPANY"` 编进**发生访问的
    那个函数自己**的 `co_names`；内部 `def` 的 code 对象嵌在外层函数的
    `co_consts` 里，不递归就看不到藏在闭包里的属性访问。`_seen` 防的是
    理论上的自引用/重复访问，不是本文件里真实存在的情形。
    """
    seen = _seen if _seen is not None else set()
    if id(code) in seen:
        return set()
    seen.add(id(code))
    names = set(code.co_names)
    for const in code.co_consts:
        if inspect.iscode(const):
            names |= _co_names_deep(const, seen)
    return names


def _defined_functions(module: object) -> list:
    """`module` 自己定义（`__module__` 等于该模块）的所有函数，包括：

    - 顶层函数；
    - 被 `functools.lru_cache` 等装饰器包裹、需要 `inspect.unwrap`
      才能拿到原始函数（及其 `__code__`）的函数；
    - 模块里定义的类的方法（含 dataclass 自动生成的
      `__init__`/`__eq__` 等）。

    用 `inspect.getmembers` 遍历、按 `__module__` 过滤，而不是手写一份
    函数名清单——这样以后有人往 web.py/openweb.py 加一个新的辅助函数或
    嵌套 helper，这里会自动捞到它，不需要回来同步维护这份清单。
    """
    seen_ids: set[int] = set()
    out: list = []

    def add(obj: object) -> None:
        obj = inspect.unwrap(obj)
        if (inspect.isfunction(obj)
                and getattr(obj, "__module__", None) == module.__name__
                and id(obj) not in seen_ids):
            seen_ids.add(id(obj))
            out.append(obj)

    for _, obj in inspect.getmembers(module):
        add(obj)
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if cls.__module__ != module.__name__:
            continue
        for _, meth in inspect.getmembers(cls):
            add(meth)
    return out


def test_结构哨兵_web模块任何函数都不得访问TierCOMPANY属性():
    """守的是与 `test_不变式_tier_of绝不产出COMPANY` 相同的约束，但换一种
    证明方式：不抽样输入，直接查字节码。

    抽样测试只证明"白名单里这~20条 + 几个手选探针不会触发 COMPANY"；
    未来有人往 `ir_agent/sources/web.py` 加一条新白名单（比如
    `_COMPANY_CN = ("宁德时代",)`）并让 `tier_of` 对它返回
    `Tier.COMPANY`，只要探针没撞上新条目，抽样测试照样全绿——这正是
    本文件顶部模块 docstring 点名的那条设计契约的漏洞。这条测试改问
    "web.py 里任何一个函数（含嵌套/被装饰过的函数）的 `__code__.
    co_names` 里出现过 `COMPANY` 这个名字吗"：`Tier.COMPANY` 不管被
    塞进哪条分支、判据用什么方法，都必然把字符串 `"COMPANY"` 编译进
    发生访问的那个函数自己的 co_names，与输入无关，因此不依赖探针是
    否撞上新增的判据。两条测试都留着：这条挡结构性退化，
    `test_不变式_tier_of绝不产出COMPANY` 挡行为回归（比如有人改坏了
    `_normalize`/`_fully_modifiers` 让某个白名单条目意外分类到别的
    Tier），二者互不替代。
    """
    from ir_agent.sources import web

    for fn in _defined_functions(web):
        names = _co_names_deep(fn.__code__)
        assert "COMPANY" not in names, (
            f"{fn.__qualname__} 的字节码里出现了 COMPANY —— "
            "COMPANY 只能由巨潮适配器赋予，web.py 里任何函数都不该"
            "访问 Tier.COMPANY")


def test_结构哨兵_openweb模块任何函数都不得访问TierCOMPANY属性():
    """`test_不变式_tier_of_url也绝不产出COMPANY` 的结构性版本，理由与
    `test_结构哨兵_web模块任何函数都不得访问TierCOMPANY属性` 完全对称：
    抽样测试挡不住未来在 `ir_agent/sources/openweb.py` 里加一份新的
    `_COMPANY_HOSTS` 注册域名单并让 `tier_of_url` 对其命中返回
    `Tier.COMPANY`（除非某条探针 URL 恰好落在新名单里，否则抽样测试
    看不见）。这条测试查的是 openweb.py 里每个函数（含 `_host_of`/
    `_matches` 这类 `tier_of_url` 调用的辅助函数、以及任何嵌套 def）
    的字节码里有没有 `COMPANY` 这个名字，与具体喂了哪些 URL 无关。
    """
    from ir_agent.sources import openweb

    for fn in _defined_functions(openweb):
        names = _co_names_deep(fn.__code__)
        assert "COMPANY" not in names, (
            f"{fn.__qualname__} 的字节码里出现了 COMPANY —— "
            "COMPANY 只能由巨潮适配器赋予，openweb.py 里任何函数都不该"
            "访问 Tier.COMPANY")
