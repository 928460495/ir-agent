# -*- coding: utf-8 -*-
"""Task 1–8 的**接缝**测试 —— 一个 Task 的真实输出能不能当下一个的输入。

**这组测试守的东西，每个 Task 自己的测试都守不到。** 到目前为止每个 Task
的测试喂的都是手搭的输入：Task 7 的 `check_deviation` 测试用 `parse_balance`
现造平衡点，Task 6 的 `collect_rows` 测试用手写的 `Document`。接缝 ——
`run_stance` 真实产出的 `Balance` 能不能被 `check_deviation` 认出来、
`announce.gather` 真实灌进去的语料库能不能被 `manifest.collect_rows` 正确
归类 —— 一条都没测。

**这组测试不证明「第一阶段跑得通」。** `run_analyse` / `run_brief` /
`run_assume` 还不存在（Task 10），编排层的行为测试在 Task 13。这里只证明
模块之间的数据形状对得上。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from ir_agent import assume, stance
from ir_agent.drivers import Decomposition, Factor, Op
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.cninfo import Announcement
from ir_agent.sources.external import Corpus, Tier

AS_OF = date(2026, 9, 30)
BODY = "行业营收同比高于本司，份额在丢，因此基准增速取低于行业。"
Q = "份额在丢，因此基准增速取低于行业"


# ── 共用夹具 ──────────────────────────────────────────────────

def _led() -> FactLedger:
    led = FactLedger()
    led.put(Fact(key="revenue.yoy", value=D("-0.0121"), unit="ratio",
                 currency=None, period="2025FY", as_of=date(2026, 4, 20),
                 source_id="eastmoney#stmt", method=Method.COMPUTED))
    return led


def _decomps() -> list[Decomposition]:
    """与 tests/test_assume.py 的同名夹具一致（刻意重复而不是跨文件导入：
    跨测试文件导入夹具会让两个文件的演进互相牵扯）。"""
    rev = Decomposition(target="revenue", op=Op.MULTIPLY, factors=[
        Factor(key="capacity", label="产能", unit="变化率"),
        Factor(key="asp", label="单价", unit="变化率")])
    gm = Decomposition(target="gross_margin", op=Op.ADD_PP, factors=[
        Factor(key="util_leverage", label="稼动率摊薄", unit="pp"),
        Factor(key="input_cost", label="上游成本", unit="pp")])
    return [rev, gm]


def _factor_lines() -> str:
    return "\n".join([
        f"假设: 产能 | 第1年 | 5% | 10% | 14% | 新厂投产 「{Q}」",
        f"假设: 单价 | 第1年 | -6% | -2% | 1% | 竞争压价 「{Q}」",
        f"假设: 稼动率摊薄 | 第1年 | -2.5 | -1.0 | 0.5 | 扩产摊薄 「{Q}」",
        f"假设: 上游成本 | 第1年 | -1.5 | -0.5 | 0.3 | 基板涨价 「{Q}」"])


def _stance_client(bal_line: str):
    """三次调用：多方采证 → 空方采证 → 裁决。返回真实的管道分隔行。"""
    seen: list[str] = []
    replies = [
        "多方｜产能｜新厂投产带来名义产能提升｜[[revenue.yoy@2025FY]]",
        "空方｜产能｜下游需求疲软，新产能可能闲置｜[[revenue.yoy@2025FY]]",
        bal_line,
    ]

    def c(prompt):
        seen.append(prompt)
        return replies[min(len(seen) - 1, len(replies) - 1)]
    c.seen = seen
    return c


def _run_b(bal_line: str, subjects=("产能",)):
    from ir_agent.analyst import FactCatalog

    led = _led()
    cat = FactCatalog.from_ledger(led, "2025FY", as_of=AS_OF)
    return stance.run_stance(_stance_client(bal_line), round_="B",
                             subjects=list(subjects), context="（证据面）",
                             catalog=cat, ledger=led, as_of=AS_OF), led


# ── 接缝 A：辩论② 的平衡点 → 判断项的偏离校验 ────────────────

class TestSeamStanceToAssume:
    """**Task 1–5 → Task 7。**

    真实风险是**名字对不上**：`run_stance` 的 subjects 来自因子标签
    （`run_brief` 里 `[f.label for d in meth.decompositions for f in d.factors]`），
    而 `check_deviation` 认的是 `_row_label(r, decomps)` —— 它由 `factor_key`
    反查 `Factor.label`。两者若不是同一个字符串，这条约束就恒不生效，
    而双方各自的单元测试都是绿的。
    """

    def test_真实平衡点能让偏离的判断项被驳回(self):
        res, led = _run_b("平衡｜产能｜0.05｜0.20｜0.30｜中性取产能爬坡更快")
        assert res.balances and res.balances[0].subject == "产能"

        a = assume.parse(_factor_lines(), 1, decomps=_decomps())
        # 产能 中性档 10%，平衡点 20% —— 偏离 10pp 且未填 deviation
        with pytest.raises(assume.AssumeFailed, match="偏离说明"):
            assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
                         decomps=_decomps(), balances=res.balances)

    def test_填了偏离说明就放行(self):
        res, led = _run_b("平衡｜产能｜0.05｜0.20｜0.30｜中性取产能爬坡更快")
        a = assume.parse(_factor_lines(), 1, decomps=_decomps())
        for r in a.rows:
            if r.factor_key == "capacity":
                r.deviation = "平衡点未计入二期技改提前投产"
        assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
                     decomps=_decomps(), balances=res.balances)

    def test_平衡点与判断项一致时不要求说明(self):
        # 平衡点 0.10 = 产能的中性档，容差之内
        res, led = _run_b("平衡｜产能｜0.05｜0.10｜0.14｜双方都接受 10%")
        a = assume.parse(_factor_lines(), 1, decomps=_decomps())
        assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
                     decomps=_decomps(), balances=res.balances)

    def test_名字对不上时这条约束会静默失效(self):
        """**反证。** 若 subject 用的是因子 key（capacity）而不是标签（产能），
        `check_deviation` 查不到、偏离就不会被驳回 —— 没有任何报错。
        这条测试把那个失效钉成**预期行为**，是为了让「名字必须一致」这件事
        在测试里有一处明文记录。
        """
        res, led = _run_b("平衡｜capacity｜0.05｜0.20｜0.30｜用 key 而不是标签")
        a = assume.parse(_factor_lines(), 1, decomps=_decomps())
        assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
                     decomps=_decomps(), balances=res.balances)   # 不抛


# ── 接缝 B：平衡点经磁盘往返（run_brief → run_assume 的真实路径）──

class TestSeamStanceThroughDisk:
    """**Task 1 的 to_json/from_json → Task 7。**

    `run_brief` 把 `stance_b.json` 落盘，`run_assume` 在另一个进程里读回 ——
    中间经过 `json.dumps`/`loads`，`Decimal` 变成字符串再变回来。这条路上
    已经埋过一个缺陷：占位符类依据的 `quote` 是空串，而 `from_json` 一律走
    `Basis.report(q)` 会抛 `InvalidBasis`（`basis.py:75`）—— 等于
    `stance_b.json` 一落盘就再也读不回来。
    """

    def test_落盘读回后平衡点仍能驳回偏离的判断项(self, tmp_path):
        res, led = _run_b("平衡｜产能｜0.05｜0.20｜0.30｜理由")
        f = tmp_path / "stance_b.json"
        f.write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=2),
                     encoding="utf-8")

        back = stance.StanceResult.from_json(
            json.loads(f.read_text(encoding="utf-8")))
        assert back.balances[0].mid == D("0.20"), "Decimal 往返丢精度"
        assert len(back.evidence) == 2, "两侧证据都要读回来"

        a = assume.parse(_factor_lines(), 1, decomps=_decomps())
        with pytest.raises(assume.AssumeFailed, match="偏离说明"):
            assume.check(a, 1, ledger=led, body=BODY, as_of=AS_OF,
                         decomps=_decomps(), balances=back.balances)

    def test_人工补写的模板填好后走同一条路(self, tmp_path):
        """`--accept-manual` 的真实路径：模板 → 人填 → load_manual → 校验。"""
        t = stance.template_json("B", ["产能"])
        t["balances"][0].update(bear="0.05", mid="0.20", bull="0.30",
                                note="人工判断：二期技改提前投产")
        f = tmp_path / "stance_b.json"
        f.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")

        back = stance.load_manual(f, round_="B", by="xhl")
        assert back.manual == "xhl" and "人工补写" in back.summary()

        a = assume.parse(_factor_lines(), 1, decomps=_decomps())
        with pytest.raises(assume.AssumeFailed, match="偏离说明"):
            assume.check(a, 1, ledger=_led(), body=BODY, as_of=AS_OF,
                         decomps=_decomps(), balances=back.balances)


# ── 接缝 C / D / E：公告入库 → 门①清单 / 因子检索 / 定性提示词 ──

def _ann(i: int, title: str, when: date) -> Announcement:
    return Announcement(
        ann_id=f"a{i}", title=title, ann_date=when,
        url=f"http://static.cninfo.com.cn/finalpage/{when}/a{i}.PDF",
        code="600183", name="生益科技", category="其他", source_id=f"src{i}")


def _gather_into(monkeypatch, cp, n=20, needle_at=None, needle="铜箔采购价"):
    """用真实的 `announce.gather` 往语料库里灌 n 条实质类公告。"""
    import ir_agent.sources.announce as A

    anns = [_ann(i, f"关于第{i}号产线投产的公告",
                 date(2026, 1, 1) + timedelta(days=i)) for i in range(n)]
    pages = {}
    for i, a in enumerate(anns):
        pages[a.url] = (f"本次投产新增产能若干。{needle}同比下降 8%。"
                        if i == needle_at else f"第{i}号产线例行披露。")
    monkeypatch.setattr(A, "_search", lambda *a, **k: anns)
    monkeypatch.setattr(A, "_download",
                        lambda url, cache_dir=None: pages[url].encode("utf-8"))
    monkeypatch.setattr(A, "pdf_text", lambda b: b.decode("utf-8"))
    return A.gather(code="600183", company="生益科技", as_of=AS_OF, corpus=cp)


class TestSeamAnnounceToManifest:
    """**Task 8 → Task 6。** 新 `gather` 灌出来的语料库，门① 清单认不认。

    Task 6 的 `collect_rows` 测试用的是手写 `Document`。那次漏掉了
    `str(d.tier) == "COMPANY"` 恒为 False 这个缺陷（`Tier` 是 `(str, Enum)`
    混入类），直到补了 `collect_rows` 的测试才发现。这里接的是真实产物。
    """

    def test_真实入库的公告在门一清单里归公司公告(self, monkeypatch, tmp_path):
        from ir_agent.sources.manifest import collect_rows, render

        cp = Corpus(as_of=AS_OF)
        res = _gather_into(monkeypatch, cp, n=12)
        assert res.picked == 12 and len(cp.docs) == 12

        (tmp_path / "600183_2025.pdf").write_text("x", encoding="utf-8")
        rows = collect_rows(_led(), corpus=cp, pdf_dir=tmp_path,
                            peers=("生益科技", "南亚新材"))
        anns = [r for r in rows if r.kind == "公司公告"]
        assert len(anns) == 12, [r.kind for r in rows]
        assert all(r.origin.startswith("http://static.cninfo.com.cn")
                   for r in anns), "出处要是能点开的巨潮地址"

        out = render(rows, gaps=[("em_macro", 12)])
        assert "## 公司公告（12 项）" in out
        assert "## 年报 PDF" in out and "## 同业样本" in out
        assert "不具备完整可溯源性" in out and "em_macro" in out

    def test_清单列出的本地文件都真的在盘上(self, monkeypatch, tmp_path):
        """门① 的代码职责就这一条：清单引用的文件必须点得开。"""
        from ir_agent.sources.manifest import collect_rows, missing_files

        cp = Corpus(as_of=AS_OF)
        _gather_into(monkeypatch, cp, n=5)
        (tmp_path / "600183_2025.pdf").write_text("x", encoding="utf-8")
        assert missing_files(collect_rows(_led(), corpus=cp,
                                          pdf_dir=tmp_path)) == []


class TestSeamIngestVsPrompt:
    """**Task 8 的架构主张，端到端。**

    「入库的池子供因子检索（越大越好），进提示词的池子要控长度」——
    这两件事此前共用一个 `MAX_ANNOUNCEMENTS = 8`。拆开之后必须能证明：
    同一篇文档**搜得到、但不进提示词**。
    """

    def test_第15篇搜得到但不进提示词(self, monkeypatch):
        from ir_agent.gaps import _match_announce

        cp = Corpus(as_of=AS_OF)
        _gather_into(monkeypatch, cp, n=20, needle_at=5)   # 第5号最早的那几条之一
        assert len(cp.docs) == 20

        hit = _match_announce(cp, "铜箔采购价", search_term="铜箔")
        assert hit is not None, "入库了就该搜得到 —— 这是放宽条数的收益"

        titles = {d.title for d in cp.prompt_docs()}
        assert len(titles) == 8, "进提示词的只有 8 篇"
        assert "关于第5号产线投产的公告" not in titles, \
            "它是较早的一篇，不该进提示词 —— 但上一条断言它仍搜得到"

    def test_旧上限下这篇会彻底消失(self, monkeypatch):
        """**反证：若入库上限仍是 8，那篇文档根本不在库里，自然也搜不到。**

        这条才是「放宽条数带来召回」的证据。计划里原本那条测试直接拿手搭的
        两个语料库调 `_match_announce`，证明的是「文档在库里就搜得到」——
        而那在改动之前就成立（`_match_announce` 从来没有条数上限）。
        """
        import ir_agent.sources.announce as A
        from ir_agent.gaps import _match_announce

        monkeypatch.setattr(A, "MAX_INGEST", 8)
        cp = Corpus(as_of=AS_OF)
        _gather_into(monkeypatch, cp, n=20, needle_at=5)
        assert len(cp.docs) == 8
        assert _match_announce(cp, "铜箔采购价", search_term="铜箔") is None


class TestSeamPromptTruncation:
    """**Task 8 → 三个定性模块。** 提示词里真的只出现 8 篇公告。"""

    def test_战略分析的证据面只拿到八篇公告(self, monkeypatch):
        from ir_agent.strategy import for_prompt

        cp = Corpus(as_of=AS_OF)
        _gather_into(monkeypatch, cp, n=20)
        text = for_prompt(_led(), "2025FY", AS_OF, None, None, cp)
        n = sum(1 for i in range(20) if f"关于第{i}号产线投产的公告" in text)
        assert n == 8, f"提示词里出现了 {n} 篇公告，应为 8"
