"""方法论预研：先问「这个行业靠什么赚钱」，再去采集数据。"""

from __future__ import annotations

import pytest

from ir_agent.drivers import Op
from ir_agent.methodology import (Methodology, MethodologyFailed, _slug,
                                  from_markdown, parse, research, to_markdown)

OUT = """分解: 营收 | 乘 | 产能:变化率:年报产能与在建工程 | 稼动率:变化率:研报行业稼动率测算 | 单价:变化率:研报封测价格跟踪
分解: 毛利率 | 加 | 稼动率摊薄:pp:固定成本占比 | 折旧变动:pp:在建工程转固节奏 | 上游成本:pp:基板与金线报价
陷阱: 用周期中某一点的利润率外推永续，是封测估值最常见的误用
陷阱: 产能利用率 A 股多数封测厂不直接披露，勿用固定资产周转率代替
"""


class _Client:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, p):
        self.prompts.append(p)
        return self.replies.pop(0)


def test_解析两条分解式与陷阱():
    m = parse(OUT)
    assert len(m.decompositions) == 2
    rev = m.decompositions[0]
    assert rev.target == "revenue" and rev.op is Op.MULTIPLY
    assert [f.label for f in rev.factors] == ["产能", "稼动率", "单价"]
    assert rev.factors[0].source_hint.startswith("年报")
    assert len(m.pitfalls) == 2


def test_不闭合的分解式被驳回():
    """「客户结构优化」写不出单位，因此写不进来。"""
    bad = ("分解: 营收 | 乘 | 产能:变化率:年报 | 稼动率:变化率:研报 "
           "| 客户结构优化:定性:无\n")
    with pytest.raises(MethodologyFailed, match="单位"):
        parse(bad)


def test_pp混进乘法链被驳回():
    bad = "分解: 营收 | 乘 | 产能:变化率:年报 | 稼动率:pp:研报\n"
    with pytest.raises(MethodologyFailed, match="变化率"):
        parse(bad)


def test_同一target出现两条分解式被驳回():
    """I3 实测复现：`got = {d.target for d in m.decompositions}` 用
    集合只查"有没有"、不查"是否唯一"。用 HEAD 版本验证过：预测期 2
    年，营收给两条分解式（产能/稼动率 一条，份额/行业规模 另一条），
    修复前 `assume.synthesize()` 会对每条 target=="revenue" 的分解式
    都 `growth.append(...)`，两条营收分解式产出 `growth` 长度变成
    2×年数（4，不是 2）；`to_driver_block` 按 target 覆盖写入 `out`
    字典，第一条分解式（产能/稼动率）被第二条悄悄覆盖丢弃。必须在
    `methodology.parse()` 解析阶段就拒绝同一 target 出现第二条。"""
    bad = ("分解: 营收 | 乘 | 产能:变化率:年报 | 稼动率:变化率:研报\n"
           "分解: 营收 | 乘 | 份额:变化率:研报 | 行业规模:变化率:研报\n"
           "分解: 毛利率 | 加 | 稼动率摊薄:pp:固定成本占比 | 上游成本:pp:基板报价\n")
    with pytest.raises(MethodologyFailed, match="营收"):
        parse(bad)


def test_两个目标都要给():
    """只给营收不给毛利率，毛利率就会沿用旧的两标量模式 ——
    半套分解比不分解更容易让人以为都分解了。"""
    only_rev = "分解: 营收 | 乘 | 产能:变化率:年报 | 单价:变化率:研报\n"
    with pytest.raises(MethodologyFailed, match="毛利率"):
        parse(only_rev)


def test_容忍列表前缀():
    """与四个定性模块同一套解析容错。"""
    m = parse("1. " + OUT.replace("\n分解", "\n2. 分解"))
    assert len(m.decompositions) == 2


def test_重试后成功():
    bad = "分解: 营收 | 乘 | 产能:定性:无 | 单价:变化率:研报\n"
    c = _Client(bad, OUT)
    m = research(c, industry="集成电路封测", code="600584")
    assert len(m.decompositions) == 2
    assert len(c.prompts) == 2 and "被驳回" in c.prompts[1]


def test_连续失败则拒绝():
    bad = "分解: 营收 | 乘 | 产能:定性:无\n"
    with pytest.raises(MethodologyFailed, match="连续"):
        research(_Client(bad, bad, bad), industry="x", code="600584")


def test_失败信息带上原始输出():
    """只说「格式不对」而不说「你写的是什么」，诊断就只能靠猜。"""
    bad = "分解: 营收 | 乘 | 产能:定性:无\n"
    with pytest.raises(MethodologyFailed) as e:
        research(_Client(bad, bad, bad), industry="x", code="600584")
    assert "产能:定性" in str(e.value)


def test_提示词要求先搜索再回答():
    c = _Client(OUT)
    research(c, industry="集成电路封测", code="600584")
    p = c.prompts[0]
    assert "集成电路封测" in p and "搜索" in p
    assert "闭合" in p and "写不出单位" in p


def test_落盘的markdown给人看得懂():
    md = to_markdown(parse(OUT))
    assert "营收 = 产能 × 稼动率 × 单价" in md
    assert "毛利率 = 基期 + 稼动率摊薄 + 折旧变动 + 上游成本" in md
    assert "年报产能与在建工程" in md          # 因子线索要留下
    assert "最常见的误用" in md                # 陷阱要留下
    assert "改这个文件后重跑" in md            # 可覆盖的说明


def test_单因子交给check_closure统一报因子数():
    """`parse()` 里不该再有一道硬编码「至少 4 段」的死分支——因子数
    该由谁来管、报多少个，只有 `check_closure`（联动 MIN_FACTORS）
    说了算，不是两处各管一半。"""
    bad = "分解: 营收 | 乘 | 产能:变化率:年报\n"
    with pytest.raises(MethodologyFailed, match="一个因子不叫分解"):
        parse(bad)


def test_字段太少无法提取目标和运算时报可读错误():
    """因子数校验交给 check_closure 之后，parse() 仍要防住连
    「目标」「运算」两段都凑不出来的极端输入——这不是因子数问题，
    是这一行连起步的两个字段都没有。"""
    with pytest.raises(MethodologyFailed):
        parse("分解: 营收\n")


def test_跨分解式的key冲突被拦截():
    """`check_closure` 的 seen 只在单条 Decomposition 内查重，但下游会把
    两条分解式的因子拍平进同一个命名空间（target_of = {f.key: d.target
    for d in decomps for f in d.factors}）。营收链与毛利率链里如果有一个
    因子标签撞了 slug，target_of 会把其中一个错误地指向另一个目标，而且
    不报任何错——这条测试在修复前必须失败（当前 parse() 对此毫无防备）。
    """
    dup = ("分解: 营收 | 乘 | 汇率贡献:变化率:年报汇率敏感度测算 "
           "| 稼动率:变化率:研报稼动率测算\n"
           "分解: 毛利率 | 加 | 汇率贡献:pp:年报外币负债敞口 "
           "| 折旧变动:pp:在建工程转固节奏\n")
    with pytest.raises(MethodologyFailed, match="汇率贡献"):
        parse(dup)


def test_slug对纯符号标签不再互相撞车():
    """`_slug` 的 fallback 用 `re.sub(r"\\W+", "_", label)` 清完标点后
    如果什么都不剩，会直接返回裸的 "f_"——「→」「←」「、、、」这类纯符号
    标签因此全部撞成同一个 key，比中文标点本身更容易被忽略。这条测试在
    修复前必须失败：当前实现下这三者 slug 后完全相同。
    """
    keys = {_slug("→"), _slug("←"), _slug("、、、")}
    assert len(keys) == 3, f"纯符号标签不应互相撞车，实际得到 {keys}"


def test_slug对同一标签保持确定性():
    """兜底不能引入随机性——同一个标签任何时候 slug 都必须是同一个 key，
    否则账本字段名和工作簿行标会对不上。"""
    assert _slug("→") == _slug("→")
    assert _slug("先进封装收入占比提升贡献") == _slug("先进封装收入占比提升贡献")


def test_slug清理中英文标点含全角括号顿号斜杠():
    """真实标签几乎都不在映射表里（见 _slug 注释），fallback 必须真的能
    清掉标点——包括全角括号、顿号、斜杠这类中文里常见但英文标点集里
    没有对应的符号，不能只处理 ASCII。"""
    assert "（" not in _slug("净利率（调整）")
    assert "）" not in _slug("净利率（调整）")
    assert "、" not in _slug("原材料、折旧")
    assert "/" not in _slug("ASP/良率")
    assert _slug("净利率（调整）") != ""


# ── I2：drivers.md 是只写文件，文档承诺的人工覆盖入口不存在 ──────
#
# `to_markdown` 末尾写着「改这个文件后重跑第一阶段」即可覆盖，但此前
# `parse()` 只认得预研阶段模型输出的 DSL 行（`分解: ... | ...`），
# `to_markdown` 产出的却是给人看的 Markdown 表格——两种格式互不相通，
# 落盘之后**没有任何路径能把这份文件读回来**。重跑第一阶段只会重新
# 调用 `research()`，把人的修改直接覆盖掉。

def test_落盘的markdown可以读回原始分解式():
    """to_markdown 的产出必须带着一份可被 from_markdown 解析回去的
    原始定义——这条测试在补上 from_markdown 之前必须失败（函数都
    不存在）。"""
    m = parse(OUT)
    back = from_markdown(to_markdown(m))
    assert len(back.decompositions) == 2
    rev = back.decompositions[0]
    assert rev.target == "revenue" and rev.op is Op.MULTIPLY
    assert [f.label for f in rev.factors] == ["产能", "稼动率", "单价"]
    gm = back.decompositions[1]
    assert gm.target == "gross_margin" and gm.op is Op.ADD_PP
    assert len(back.pitfalls) == 2


def test_读回后行业信息也一并保留():
    m = parse(OUT)
    m.industry = "电子-半导体-封测"
    back = from_markdown(to_markdown(m))
    assert back.industry == "电子-半导体-封测"


def test_人工改过因子后读回生效():
    """**这是「可覆盖」这句承诺真正要成立的地方。** 人改了落盘文件里的
    数据线索之后，重跑要读到改过的内容，不是模型原来给的那份。"""
    md = to_markdown(parse(OUT))
    edited = md.replace("年报产能与在建工程", "人工改过的线索：管理层交流纪要")
    back = from_markdown(edited)
    assert back.decompositions[0].factors[0].source_hint == \
        "人工改过的线索：管理层交流纪要"


def test_读不回时明确报错而不是返回空():
    """**无法解析时要明确报错，不能静默回退**——静默回退会让人以为
    自己的修改生效了，实际被丢掉。"""
    with pytest.raises(MethodologyFailed, match="标记"):
        from_markdown("# 驱动因子分解（某行业）\n\n随便写点什么，没有任何"
                      "可读回的内容。\n")


def test_代码块被删除时明确报错():
    """标记还在，但代码块本身被删掉或改坏——同样要报错，不能安静地
    解析出一个空的 Methodology 然后假装成功。"""
    md = to_markdown(parse(OUT))
    heading = "## 分解式原始定义（供人工编辑与重跑读回）"
    broken = md.split(heading)[0] + heading + "\n\n（代码块被误删了）\n"
    with pytest.raises(MethodologyFailed, match="代码块"):
        from_markdown(broken)


def test_代码块内容本身不合法时报出具体原因():
    """读回复用的是同一套 parse()，因此人改坏了语法（比如把 pp 混进
    乘法链）会得到与预研阶段完全一样的可读错误，不是一个笼统的
    「读不回」。"""
    md = to_markdown(parse(OUT))
    broken = md.replace("产能:变化率:年报产能与在建工程",
                        "产能:pp:年报产能与在建工程")
    with pytest.raises(MethodologyFailed, match="变化率"):
        from_markdown(broken)


def test_提示词讲清营收乘法链不放占比类因子():
    """错误消息就是下一轮提示词——但首轮就说清，能省一次被驳回。"""
    from ir_agent import methodology
    assert "占比" in methodology.PROMPT and "市占率" in methodology.PROMPT


def test_解析遇到过短检索词报方法论错误而非分解式错误():
    """`Factor.__post_init__` 抛的是 `DecompositionInvalid`，而 `parse`
    里 `Factor(...)` 的构造**不在任何 try 内**——不转译的话它会绕过
    `methodology.py` 的重试循环（那里只 `except MethodologyFailed`），
    模型拿不到驳回原因、也没有重试机会。

    消息里要带因子名与那个检索词，否则模型不知道该改哪一个。
    """
    import pytest

    from ir_agent.methodology import MethodologyFailed, parse

    # 营收链用合法的三因子（产能 × 稼动率 × 单价）——`drivers` 有一条
    # 领域规则：公司内部占比型因子（「结构」「高端产品收入占比」）不能进
    # 营收乘法链。混进一个违规因子会让这条测试靠另一个 MethodologyFailed
    # 通过，变成一条测不准东西的测试。
    text = ("分解: 营收 | 乘 | 产能:变化率:年报:率 | 稼动率:变化率:年报 | "
            "单价:变化率:年报\n"
            "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    with pytest.raises(MethodologyFailed) as e:
        parse(text)
    assert "产能" in str(e.value) and "率" in str(e.value)


def test_DSL第四段读成检索词且旧的三段仍然可用():
    """第 4 段**此前是被静默丢弃的**（`hint = bits[2] if len(bits) > 2`），
    所以旧的 3 段文件自然得到空检索词、退回 `_core_candidates` 的前三级
    ——向后兼容是免费的，但要有断言守住它。

    读第 4 段在 Task 1 已落地（错误转译需要一条能触发它的路径），所以
    这条是回归保护而不是驱动实现的测试。
    """
    from ir_agent.methodology import parse

    m = parse("分解: 营收 | 乘 | 出货量同比增速:变化率:年报:出货量 | "
              "稼动率:变化率:年报 | 单价:变化率:年报\n"
              "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    rev = next(d for d in m.decompositions if d.target == "revenue")
    by_label = {f.label: f for f in rev.factors}
    assert by_label["出货量同比增速"].search_term == "出货量"   # 4 段
    assert by_label["稼动率"].search_term == ""                # 3 段，兼容


def test_往返不丢检索词():
    """`_to_dsl` 是 `parse` 的逆操作，`from_markdown` 靠它把落盘文件读回。
    **不改 `_to_dsl`，人工编辑 drivers.md 后重跑会把检索词吃掉**——而那个
    往返是写进文件末尾的承诺（「编辑下面这段文字再重跑即可覆盖」）。
    """
    from ir_agent.methodology import _to_dsl, parse

    src = ("分解: 营收 | 乘 | 出货量同比增速:变化率:年报:出货量 | "
           "稼动率:变化率:年报 | 单价:变化率:年报\n"
           "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    m1 = parse(src)
    m2 = parse(_to_dsl(m1))
    got = {f.label: f.search_term
           for d in m2.decompositions for f in d.factors}
    assert got["出货量同比增速"] == "出货量", "往返丢了检索词"
    assert got["稼动率"] == ""


def test_人读表里出现检索词列():
    """`drivers.md` 是人看的。检索词决定了代码实际去搜什么词，人核实
    分解式时必须看得见它——否则一个泛词（实测「服务器」18 次巧合命中）
    会在没人注意的情况下决定一个因子的「年报披露」状态。
    """
    from ir_agent.methodology import parse, to_markdown

    m = parse("分解: 营收 | 乘 | 出货量同比增速:变化率:年报:出货量 | "
              "稼动率:变化率:年报 | 单价:变化率:年报\n"
              "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    md = to_markdown(m)
    # **断在表头行与数据行上，不断「检索词 in md」。** 后者是空断言：
    # 文件末尾的往返说明里有一句「`分解: 目标 | 运算 | 因子名:单位:
    # 数据线索:检索词 | …`」，「检索词」四个字在文档里另有出处，于是
    # 去掉整列表头后那条断言照样通过（实测突变验证时发现）。
    assert "| 因子 | 单位 | 数据线索 | 检索词 |" in md, "人读表没有检索词列"
    rows = [l for l in md.splitlines() if l.startswith("| 出货量同比增速 ")]
    assert rows, "表里没有这一行"
    assert rows[0].rstrip().endswith("| 出货量 |"), \
        f"检索词没落在那一列里：{rows[0]!r}"


def test_hint带冒号时第四段会变成假检索词():
    """**这是一条把已知坏情形钉成明账的测试，不是一条验证正确行为的测试。**

    `raw.split(":")` 对所有字段生效。`source_hint` 里出现冒号时，一行 3 段
    的因子被切成 4 段，尾巴落进检索词位——而闸门只查长度，拦不住它。

    设计早期版本把子串闸门当作这个风险的解药（hint 的碎片几乎不可能是
    label 的子串）。闸门放开之后那条解药不存在了，所以这里只能记成明账。

    它红了意味着有人修了这个 bug（限定 `split(":", 3)` 或禁止 hint 带冒号）
    ——那是好事，请连这条测试一起改掉。
    """
    from ir_agent.methodology import parse

    m = parse("分解: 营收 | 乘 | 汇兑损益:变化率:年报附注:汇兑损益 | "
              "稼动率:变化率:年报 | 单价:变化率:年报\n"
              "分解: 毛利率 | 加 | 成本:pp:年报 | 稼动:pp:年报\n")
    f = next(f for d in m.decompositions for f in d.factors
             if f.label == "汇兑损益")
    assert f.source_hint == "年报附注", "hint 被切断（这是已知行为）"
    assert f.search_term == "汇兑损益", \
        "hint 的尾巴成了检索词（这是已知的坏情形，见本测试 docstring）"


def test_被驳回的分解式带着最后一稿():
    """**存活者偏差，而不只是排查麻烦。** 被驳回的分解式不留文件，于是事后
    拿历史产物做离线复盘只能看到通过了闸门的样本 —— 实测（牧原股份 002714）:
    「公司出栏量占全国比重」被拒 3 次、drivers.md 根本没生成，后来用 10 份
    存档做消融时，两个词法修复在 56 个存活因子上的效果是 **0 个改变**，
    真正的拦截与漏报全在看不见的那一半。
    """
    from ir_agent.methodology import MethodologyFailed, research

    bad = "营收:乘:覆铜板收入增速:变化率:收入\n毛利率:加:成本:pp:成本"
    with pytest.raises(MethodologyFailed) as e:
        research(lambda p: bad, "电子-元件-印制电路板", "600183", max_attempts=2)
    assert "覆铜板收入增速" in e.value.raw, "最后一稿没带出来"


def test_落盘与读回用不同的文件名():
    """drivers.md 是可人工编辑、会被读回的往返文件；失败稿是证据。同名会让
    下一轮把失败稿当成人工修改读回去。"""
    from ir_agent import full
    assert full.DRIVERS_FAILED_RAW != full.DRIVERS_MD
