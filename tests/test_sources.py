# -*- coding: utf-8 -*-
"""门① 的来源清单 —— 人要能逐条点开核。

**为什么要有这道门。** 实测（生益科技 2026-10-06）: 批准人只在最后一道门
介入，而那时资料与结论都已固定；而「em_macro / eastmoney_report 缺原始接口
快照」这条告警连续六轮只在终端一闪而过 —— 它是**来源质量的事实**，归这道
门，不是本轮的异常。
"""
import pytest

from ir_agent.sources.manifest import (
    KNOWN_GAPS,
    SourceRow,
    missing_files,
    render,
)


def _rows(tmp_path):
    real = tmp_path / "600183_2025.pdf"
    real.write_text("x", encoding="utf-8")
    return [
        SourceRow(kind="账本事实", label="revenue@2025FY", origin="eastmoney",
                  local=str(real), retrieved="2026-04-20", note=""),
        SourceRow(kind="外部文档", label="覆铜板行业深度",
                  origin="https://example.cn/a", local="", retrieved="2026-10-06",
                  note="SELLSIDE"),
        SourceRow(kind="年报 PDF", label="2025 年年度报告",
                  origin="http://static.cninfo.com.cn/x.PDF", local=str(real),
                  retrieved="2026-04-25", note="30 页截断"),
    ]


class TestMissingFiles:
    def test_列出的本地文件必须真的在盘上(self, tmp_path):
        """**防的是「清单引用了一个从未保存的文件」。** 人点开发现打不开，
        这道门就白设了。"""
        rows = _rows(tmp_path)
        assert missing_files(rows) == []

    def test_文件不存在就报出来(self, tmp_path):
        rows = _rows(tmp_path)
        rows.append(SourceRow(kind="公司公告", label="业绩预增公告",
                              origin="http://x", local=str(tmp_path / "nope.pdf"),
                              retrieved="2026-10-06", note=""))
        probs = missing_files(rows)
        assert len(probs) == 1 and "nope.pdf" in probs[0]

    def test_没有本地文件的行不算缺失(self, tmp_path):
        """外部网页只有 URL，没有本地缓存 —— 那不是缺失。"""
        rows = [SourceRow(kind="外部文档", label="x", origin="https://a",
                          local="", retrieved="2026-10-06", note="")]
        assert missing_files(rows) == []


class TestRender:
    def test_按类别分组并给出可点开的路径(self, tmp_path):
        out = render(_rows(tmp_path), gaps=[])
        assert "账本事实" in out and "外部文档" in out and "年报 PDF" in out
        assert "600183_2025.pdf" in out
        assert "2026-04-20" in out

    def test_已知缺口显式列出且说明不是本轮故障(self, tmp_path):
        """这条每轮必响的告警终于有了正确的归属地。"""
        out = render(_rows(tmp_path),
                     gaps=[("em_macro", 12), ("eastmoney_report", 6)])
        assert "不具备完整可溯源性" in out and "非本轮故障" in out
        assert "em_macro" in out and "12" in out

    def test_同业样本量要进清单(self, tmp_path):
        """牧原 11 家 vs 生益 46 家 —— 代表性差异必须让人看到，因为行业
        中位数、CR5、分位数都建立在它上面。"""
        rows = _rows(tmp_path) + [
            SourceRow(kind="同业样本", label="生猪养殖（11 家）",
                      origin="东财行业分类", local="", retrieved="2026-10-07",
                      note="样本偏小，行业中位数代表性弱")]
        out = render(rows, gaps=[])
        assert "11 家" in out and "代表性" in out


def test_已知缺口表里有那两个来源():
    """em_macro / eastmoney_report 的数字从文本抽出，未保留原始接口响应。"""
    assert "em_macro" in KNOWN_GAPS and "eastmoney_report" in KNOWN_GAPS
    assert "文本" in KNOWN_GAPS["em_macro"]


class TestCollectRows:
    """**`collect_rows` 是这个模块的主体，而计划原本一条测试都没给它。**

    那个缺口当场放过了一个真缺陷：`kind` 的判据写成 `str(d.tier) == "COMPANY"`，
    而 `Tier` 是 `(str, Enum)` 混入类 —— `str(Tier.COMPANY)` 是
    `'Tier.COMPANY'`，比较恒为 False，于是「公司公告」那一节永远是空的。
    无异常、无告警，只有读清单的人看到一个空小节。
    """

    def _corpus(self):
        from datetime import date

        from ir_agent.sources.external import Corpus, Document, Tier

        cp = Corpus(as_of=date(2026, 10, 12))
        cp.docs.append(Document(
            doc_id="ann1", url="http://static.cninfo.com.cn/a.PDF",
            title="关于全资子公司投资建设项目的公告", publisher="生益科技",
            tier=Tier.COMPANY, published=date(2026, 3, 1),
            retrieved=date(2026, 10, 12), text="正文"))
        cp.docs.append(Document(
            doc_id="rep1", url="https://example.cn/r", title="覆铜板行业深度",
            publisher="某证券", tier=Tier.SELLSIDE, published=date(2026, 2, 1),
            retrieved=date(2026, 10, 12), text="正文"))
        return cp

    def _ledger(self):
        from datetime import date
        from decimal import Decimal

        from ir_agent.ledger import Fact, FactLedger, Method

        led = FactLedger()
        led.put(Fact(key="revenue", value=Decimal("2.03E10"), unit="CNY",
                     currency="CNY", period="2025FY", as_of=date(2026, 4, 20),
                     source_id="eastmoney#stmt", method=Method.REPORTED))
        return led

    def test_公告归公司公告研报归外部文档(self):
        from ir_agent.sources.manifest import collect_rows

        rows = collect_rows(self._ledger(), corpus=self._corpus())
        kinds = {r.kind for r in rows}
        assert "公司公告" in kinds, "公告被错归了 —— 门① 的公告小节会是空的"
        assert "外部文档" in kinds
        ann = next(r for r in rows if r.kind == "公司公告")
        assert "投资建设项目" in ann.label
        assert ann.origin.endswith(".PDF")

    def test_账本事实带出接口名(self):
        from ir_agent.sources.manifest import collect_rows

        rows = collect_rows(self._ledger())
        fact = next(r for r in rows if r.kind == "账本事实")
        assert fact.origin == "eastmoney#stmt"
        assert "revenue" in fact.label and "2025FY" in fact.label

    def test_年报PDF按缓存目录列出(self, tmp_path):
        from ir_agent.sources.manifest import collect_rows

        (tmp_path / "600183_2025.pdf").write_text("x", encoding="utf-8")
        rows = collect_rows(self._ledger(), pdf_dir=tmp_path)
        pdf = next(r for r in rows if r.kind == "年报 PDF")
        assert pdf.local.endswith("600183_2025.pdf")

    def test_同业样本量小的要标出代表性弱(self):
        """牧原 11 家 vs 生益 46 家 —— 行业中位数、CR5、分位数都建立在它上面。"""
        from ir_agent.sources.manifest import collect_rows

        small = collect_rows(self._ledger(), peers=tuple(f"p{i}" for i in range(11)))
        big = collect_rows(self._ledger(), peers=tuple(f"p{i}" for i in range(46)))
        s = next(r for r in small if r.kind == "同业样本")
        b = next(r for r in big if r.kind == "同业样本")
        assert "11 家" in s.label and "代表性" in s.note
        assert "46 家" in b.label and b.note == ""

    def test_渲染出来的清单带上公告小节(self):
        """端到端：collect_rows → render，公告必须出现在清单里。"""
        from ir_agent.sources.manifest import collect_rows, render

        out = render(collect_rows(self._ledger(), corpus=self._corpus()), gaps=[])
        assert "## 公司公告" in out, out[:400]

    def test_多个来源各出一行而同一来源只出一行(self):
        """**去重键必须是真的 source_id。**

        `_facts` 的值是 `list[Fact]` 而不是单条 Fact，读成单条时
        `getattr(list, "source_id", "")` 恒得空串 —— 所有行都撞在 "" 上，
        整个账本事实小节塌成一行。单条事实的测试测不出这一点。
        """
        from datetime import date
        from decimal import Decimal

        from ir_agent.ledger import Fact, FactLedger, Method
        from ir_agent.sources.manifest import collect_rows

        led = FactLedger()
        for key, sid in (("revenue", "eastmoney#stmt"),
                         ("net_profit", "eastmoney#stmt"),   # 同源
                         ("pe_ttm", "tencent#quote"),        # 异源
                         ("industry.dtl", "em_macro#ind")):
            led.put(Fact(key=key, value=Decimal("1"), unit="ratio",
                         currency=None, period="2025FY",
                         as_of=date(2026, 4, 20), source_id=sid,
                         method=Method.COMPUTED))
        rows = [r for r in collect_rows(led) if r.kind == "账本事实"]
        assert len(rows) == 3, [r.origin for r in rows]
        assert {r.origin for r in rows} == {
            "eastmoney#stmt", "tencent#quote", "em_macro#ind"}
