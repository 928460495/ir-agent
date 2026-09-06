"""分类索引与层级回退。

回退存在的理由: 申万三级常常细到只剩两三家同业，分位数会算出来但没有含义。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ir_agent.sources import taxonomy
from ir_agent.sources.taxonomy import Entry, Taxonomy, TaxonomyUnavailable

汽车 = [
    Entry("002594", "比亚迪", "汽车-乘用车-电动乘用车"),
    Entry("600733", "北汽蓝谷", "汽车-乘用车-电动乘用车"),
    Entry("601127", "赛力斯", "汽车-乘用车-电动乘用车"),
    Entry("600104", "上汽集团", "汽车-乘用车-综合乘用车"),
    Entry("000625", "长安汽车", "汽车-乘用车-综合乘用车"),
    Entry("601633", "长城汽车", "汽车-乘用车-综合乘用车"),
    Entry("000338", "潍柴动力", "汽车-商用车-商用载货车"),
]
白酒 = [Entry(f"00{i:04d}", f"酒{i}", "食品饮料-白酒Ⅱ-白酒Ⅲ") for i in range(9)]


def _tax(entries=None):
    return Taxonomy(entries or 汽车, date(2026, 9, 7))


# ── 层级回退 ─────────────────────────────────────────────────

def test_叶子级同业不足时向上回退一级():
    """比亚迪的真实处境: 电动乘用车只有 3 家（含自身）。"""
    peers, level, name, thin = _tax().widen("002594", min_peers=5)
    assert level == 2 and name == "汽车-乘用车" and not thin
    assert len(peers) == 5
    assert "潍柴动力" not in [p.name for p in peers]   # 商用车不在乘用车下


def test_叶子级样本够就不回退():
    peers, level, name, thin = _tax(白酒).widen("000000", min_peers=5)
    assert level == 3 and name == "食品饮料-白酒Ⅱ-白酒Ⅲ"
    assert len(peers) == 8 and not thin


def test_回退止步二级不退到一级():
    """中国平安的真实处境: 退到一级会混进券商，业务实质完全不同。

    4 家真同业好过 79 家假同业 —— 宁可样本薄，不可口径假。
    """
    保险 = [Entry("601318", "中国平安", "非银金融-保险Ⅱ-保险Ⅲ"),
            Entry("601601", "中国太保", "非银金融-保险Ⅱ-保险Ⅲ"),
            Entry("601628", "中国人寿", "非银金融-保险Ⅱ-保险Ⅲ")]
    券商 = [Entry(f"60{i:04d}", f"券商{i}", "非银金融-证券Ⅱ-证券Ⅲ")
            for i in range(20)]
    peers, level, name, thin = Taxonomy(保险 + 券商, date(2026, 9, 7)).widen(
        "601318", min_peers=5)
    assert level == 2 and name == "非银金融-保险Ⅱ"
    assert len(peers) == 2 and thin              # 样本薄，但如实标注
    assert all("券商" not in p.name for p in peers)


def test_样本仍不足时置薄标志而非报错():
    tiny = [Entry("000001", "甲", "冷门-冷门Ⅱ-冷门Ⅲ"),
            Entry("000002", "乙", "冷门-冷门Ⅱ-冷门Ⅲ")]
    peers, level, name, thin = Taxonomy(tiny, date(2026, 9, 7)).widen(
        "000001", min_peers=5)
    assert level == 2 and name == "冷门-冷门Ⅱ"
    assert len(peers) == 1 and thin


def test_查无此代码报错():
    with pytest.raises(TaxonomyUnavailable, match="查无此代码"):
        _tax().widen("999999")


def test_at_level_按前缀而非包含匹配():
    """'汽车-乘用车' 不该匹配到 '汽车-商用车'。"""
    got = {e.name for e in _tax().at_level("汽车-乘用车-电动乘用车", 2)}
    assert "潍柴动力" not in got
    assert got == {"比亚迪", "北汽蓝谷", "赛力斯", "上汽集团", "长安汽车", "长城汽车"}


# ── 缓存 ─────────────────────────────────────────────────────

def test_缓存往返(tmp_path):
    p = tmp_path / "t.json"
    _tax().dump(p)
    back = Taxonomy.read(p)
    assert back.fetched == date(2026, 9, 7)
    assert [e.name for e in back.entries] == [e.name for e in 汽车]


def test_缓存在有效期内不重新抓取(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    _tax().dump(p)
    monkeypatch.setattr(taxonomy, "fetch_all",
                        lambda: pytest.fail("不该抓取"))
    got = Taxonomy.load(p, today=date(2026, 9, 7) + taxonomy.CACHE_TTL)
    assert len(got.entries) == len(汽车)


def test_缓存过期后重新抓取(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    _tax().dump(p)
    monkeypatch.setattr(taxonomy, "fetch_all",
                        lambda: [Entry("000001", "新", "新-新Ⅱ-新Ⅲ")])
    stale = date(2026, 9, 7) + taxonomy.CACHE_TTL + timedelta(days=1)
    assert Taxonomy.load(p, today=stale).entries[0].name == "新"


def test_refresh_跳过有效缓存(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    _tax().dump(p)
    monkeypatch.setattr(taxonomy, "fetch_all",
                        lambda: [Entry("000001", "新", "新-新Ⅱ-新Ⅲ")])
    assert Taxonomy.load(p, today=date(2026, 9, 7),
                         refresh=True).entries[0].name == "新"


def test_损坏的缓存视同没有而非崩溃(tmp_path, monkeypatch):
    p = tmp_path / "t.json"
    p.write_text("{不是 json", encoding="utf-8")
    assert Taxonomy.read(p) is None
    monkeypatch.setattr(taxonomy, "fetch_all",
                        lambda: [Entry("000001", "新", "新-新Ⅱ-新Ⅲ")])
    assert Taxonomy.load(p, today=date(2026, 9, 7)).entries[0].name == "新"


def test_缓存目录不可写不影响本次使用(tmp_path, monkeypatch):
    monkeypatch.setattr(taxonomy, "fetch_all", lambda: 汽车)
    monkeypatch.setattr(Taxonomy, "dump",
                        lambda self, path: (_ for _ in ()).throw(OSError("只读")))
    assert len(Taxonomy.load(tmp_path / "x.json",
                             today=date(2026, 9, 7)).entries) == len(汽车)


# ── 抓取 ─────────────────────────────────────────────────────

def _page(rows, pages):
    return rows, pages


def test_抓取翻页并去重(monkeypatch):
    pages = {
        1: ([{"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "茅台",
              "BOARD_NAME_LEVEL": "食品饮料-白酒Ⅱ-白酒Ⅲ"}], 2),
        2: ([{"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "茅台",
              "BOARD_NAME_LEVEL": "食品饮料-白酒Ⅱ-白酒Ⅲ"},
             {"SECURITY_CODE": "000858", "SECURITY_NAME_ABBR": "五粮液",
              "BOARD_NAME_LEVEL": "食品饮料-白酒Ⅱ-白酒Ⅲ"}], 2),
    }
    monkeypatch.setattr(taxonomy, "_fetch_page", lambda p: pages[p])
    assert [e.code for e in taxonomy.fetch_all()] == ["600519", "000858"]


def test_抓取剔除非A股代码段(monkeypatch):
    monkeypatch.setattr(taxonomy, "_fetch_page", lambda p: ([
        {"SECURITY_CODE": "000596", "SECURITY_NAME_ABBR": "古井贡酒",
         "BOARD_NAME_LEVEL": "食品饮料-白酒Ⅱ-白酒Ⅲ"},
        {"SECURITY_CODE": "200596", "SECURITY_NAME_ABBR": "古井贡B",
         "BOARD_NAME_LEVEL": "食品饮料-白酒Ⅱ-白酒Ⅲ"},
        {"SECURITY_CODE": "830799", "SECURITY_NAME_ABBR": "北交所某",
         "BOARD_NAME_LEVEL": "机械设备-通用设备-其他"},
    ], 1))
    assert [e.code for e in taxonomy.fetch_all()] == ["000596"]


def test_分类为空的条目跳过(monkeypatch):
    monkeypatch.setattr(taxonomy, "_fetch_page", lambda p: ([
        {"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "茅台",
         "BOARD_NAME_LEVEL": None},
        {"SECURITY_CODE": "000858", "SECURITY_NAME_ABBR": "五粮液",
         "BOARD_NAME_LEVEL": "食品饮料-白酒Ⅱ-白酒Ⅲ"},
    ], 1))
    assert [e.code for e in taxonomy.fetch_all()] == ["000858"]


def test_全空则报错(monkeypatch):
    monkeypatch.setattr(taxonomy, "_fetch_page", lambda p: ([], 1))
    with pytest.raises(TaxonomyUnavailable):
        taxonomy.fetch_all()
