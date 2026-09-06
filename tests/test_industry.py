"""行业归属与同业集合。重点在剔除与回退 —— 多算少算一家都会改变行业结论。"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent.sources.industry import IndustryUnavailable, peer_set
from ir_agent.sources.taxonomy import Entry, Taxonomy

白酒 = [Entry("600519", "贵州茅台", "食品饮料-白酒Ⅱ-白酒Ⅲ"),
        Entry("000858", "五粮液", "食品饮料-白酒Ⅱ-白酒Ⅲ"),
        Entry("000568", "泸州老窖", "食品饮料-白酒Ⅱ-白酒Ⅲ"),
        Entry("600809", "山西汾酒", "食品饮料-白酒Ⅱ-白酒Ⅲ"),
        Entry("000596", "古井贡酒", "食品饮料-白酒Ⅱ-白酒Ⅲ"),
        Entry("000995", "ST皇台", "食品饮料-白酒Ⅱ-白酒Ⅲ"),
        Entry("600696", "退市岩石", "食品饮料-白酒Ⅱ-白酒Ⅲ")]


def _tax(entries=None):
    return Taxonomy(entries or 白酒, date(2026, 9, 7))


# ── 成分构成 ─────────────────────────────────────────────────

def test_标的自身不计入同业():
    p = peer_set("600519", taxonomy=_tax())
    assert p.target == "600519" and p.target_name == "贵州茅台"
    assert "600519" not in p.codes


def test_退市公司剔除且理由入账():
    p = peer_set("600519", taxonomy=_tax())
    assert "600696" not in p.codes
    assert any(c == "600696" and "已退市" in why for c, _, why in p.excluded)


def test_ST公司保留():
    """ST 仍在上市交易，是真实的行业参与者。剔除会让行业指标系统性偏乐观。"""
    assert "000995" in peer_set("600519", taxonomy=_tax()).codes


def test_查无此代码报错():
    with pytest.raises(IndustryUnavailable, match="查无此代码"):
        peer_set("999999", taxonomy=_tax())


def test_剔除后无同业则报错():
    tax = Taxonomy([Entry("600519", "贵州茅台", "食品饮料-白酒Ⅱ-白酒Ⅲ"),
                    Entry("600696", "退市岩石", "食品饮料-白酒Ⅱ-白酒Ⅲ")],
                   date(2026, 9, 7))
    with pytest.raises(IndustryUnavailable, match="没有可用同业"):
        peer_set("600519", taxonomy=tax)


# ── 层级回退 ─────────────────────────────────────────────────

def test_叶子级样本足够时不回退():
    c = peer_set("600519", taxonomy=_tax()).classification
    assert c.level == 3 and not c.widened
    assert "食品饮料-白酒Ⅱ-白酒Ⅲ" in c.describe()
    assert "上移" not in c.describe()


def test_叶子级过少时上移并写明():
    汽车 = [Entry("002594", "比亚迪", "汽车-乘用车-电动乘用车"),
            Entry("600733", "北汽蓝谷", "汽车-乘用车-电动乘用车"),
            *[Entry(f"6001{i:02d}", f"车企{i}", "汽车-乘用车-综合乘用车")
              for i in range(5)]]
    c = peer_set("002594", taxonomy=Taxonomy(汽车, date(2026, 9, 7))).classification
    assert c.level == 2 and c.widened
    assert "汽车-乘用车" in c.describe() and "上移一级" in c.describe()


def test_样本仍偏薄时置标志并说明不再上移():
    保险 = [Entry("601318", "中国平安", "非银金融-保险Ⅱ-保险Ⅲ"),
            Entry("601601", "中国太保", "非银金融-保险Ⅱ-保险Ⅲ"),
            *[Entry(f"60{i:04d}", f"券商{i}", "非银金融-证券Ⅱ-证券Ⅲ")
              for i in range(20)]]
    p = peer_set("601318", taxonomy=Taxonomy(保险, date(2026, 9, 7)))
    assert p.thin and len(p.members) == 1
    assert all("券商" not in m.name for m in p.members)
    s = p.summary()
    assert "不具统计意义" in s and "保险与券商" in s


def test_剔除退市后跌破下限要重新置薄标志():
    """widen 是在剔除之前算的 —— 沿用它的判断会漏报。"""
    tax = Taxonomy([Entry("600519", "贵州茅台", "食品饮料-白酒Ⅱ-白酒Ⅲ"),
                    *[Entry(f"0008{i:02d}", f"酒{i}", "食品饮料-白酒Ⅱ-白酒Ⅲ")
                      for i in range(5)],
                    *[Entry(f"6006{i:02d}", f"退市酒{i}", "食品饮料-白酒Ⅱ-白酒Ⅲ")
                      for i in range(3)]], date(2026, 9, 7))
    full = peer_set("600519", taxonomy=tax, min_peers=6)
    assert len(full.members) == 5 and full.thin      # 8 家过线，剔 3 家后跌破


# ── 摘要 ─────────────────────────────────────────────────────

def test_摘要含分类口径与同业家数():
    s = peer_set("600519", taxonomy=_tax()).summary()
    assert "申万三级" in s and "同业 5 家" in s and "退市岩石" in s
