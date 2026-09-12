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
