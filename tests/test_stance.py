# -*- coding: utf-8 -*-
"""多空辩论 —— 两个子 agent 采集对立证据，裁决出带数值区间的平衡点。

**为什么不复用 debate.py。** 那边批判的是**已成稿的正文**（逻辑错误/无据断言/
反面证据被忽略），抓不住「这批证据被单向挑选过」。多空双方各自采证，才可能
暴露证据选择本身的偏向。debate.py 仍服务第二阶段的成稿辩论，不动。
"""
from datetime import date
from decimal import Decimal as D

import pytest

from ir_agent.stance import (
    Balance,
    Evidence,
    StanceFailed,
    StanceResult,
    parse_balance,
    parse_evidence,
)


class TestParseEvidence:
    def test_管道分隔四字段(self):
        raw = "多方｜覆铜板销量｜AI 服务器需求放量支撑出货｜[[revenue.yoy@2025FY]]"
        evs = parse_evidence(raw)
        assert len(evs) == 1
        e = evs[0]
        assert e.side == "多方" and e.subject == "覆铜板销量"
        assert "AI 服务器" in e.claim
        assert e.bases and e.bases[0].ref == "[[revenue.yoy@2025FY]]"

    def test_两侧混在一起也能分开(self):
        raw = ("多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]\n"
               "空方｜销量｜周期下行｜[[industry.revenue_yoy@2025FY]]")
        evs = parse_evidence(raw)
        assert [e.side for e in evs] == ["多方", "空方"]

    def test_侧别写错直接报错(self):
        """只有「多方」「空方」两个合法值 —— 认不出来的侧别不能静默丢掉。"""
        with pytest.raises(StanceFailed, match="侧别"):
            parse_evidence("中立｜销量｜看法｜[[revenue.yoy@2025FY]]")

    def test_缺依据的那条报错并指名(self):
        """依据是这套机制的全部价值所在，缺了不能收。"""
        with pytest.raises(StanceFailed, match="销量"):
            parse_evidence("多方｜销量｜需求放量｜")

    def test_字段数不对报错(self):
        with pytest.raises(StanceFailed, match="四段"):
            parse_evidence("多方｜销量｜需求放量")

    def test_一条都没有报错(self):
        with pytest.raises(StanceFailed, match="一条"):
            parse_evidence("（无可用证据）")


class TestParseBalance:
    def test_带数值的三元组(self):
        b = parse_balance("平衡｜覆铜板销量｜-0.02｜0.08｜0.18｜中性取份额小幅提升")
        assert len(b) == 1
        assert b[0].subject == "覆铜板销量"
        assert (b[0].bear, b[0].mid, b[0].bull) == (D("-0.02"), D("0.08"), D("0.18"))
        assert "份额" in b[0].note

    def test_辩论一的纯文字形态用破折号占位(self):
        """辩论① 在预研之前，还没有因子，只能给文字结论。"""
        b = parse_balance("平衡｜需求侧｜—｜—｜—｜量增价稳")
        assert b[0].bear is None and b[0].mid is None and b[0].bull is None
        assert b[0].note == "量增价稳"

    def test_百分号写法也接受(self):
        b = parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由")
        assert b[0].mid == D("0.08")

    def test_数值写不出来就报错而不是当成文字(self):
        """「约 8%」这种模糊写法不能静默变成 None —— 那会让区间校验失效。"""
        with pytest.raises(StanceFailed, match="约 8%"):
            parse_balance("平衡｜销量｜-2%｜约 8%｜18%｜理由")

    def test_三个值要么全给要么全不给(self):
        with pytest.raises(StanceFailed, match="三档"):
            parse_balance("平衡｜销量｜-2%｜—｜18%｜理由")


class TestStanceResult:
    def _evs(self):
        return parse_evidence(
            "多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]\n"
            "空方｜销量｜周期下行｜[[industry.revenue_yoy@2025FY]]")

    def test_摘要分两侧列出并统计(self):
        r = StanceResult(round_="B", evidence=self._evs(),
                         balances=parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由"))
        s = r.summary()
        assert "多方 1 条" in s and "空方 1 条" in s and "销量" in s

    def test_人工补写要在摘要里留痕(self):
        r = StanceResult(round_="B", evidence=self._evs(), balances=[], manual="xhl")
        assert "人工补写" in r.summary() and "xhl" in r.summary()

    def test_json往返(self):
        r = StanceResult(round_="B", evidence=self._evs(),
                         balances=parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由"))
        back = StanceResult.from_json(r.to_json())
        assert back.round_ == "B"
        assert [e.claim for e in back.evidence] == [e.claim for e in r.evidence]
        assert back.balances[0].mid == D("0.08")


class TestCheckInterval:
    """**这是这套设计里唯一能确定性保证的东西：裁决者不能自己造数。**

    平衡值必须落在两侧划出的区间内 —— 否则「平衡点」就是第三个独立观点，
    对两侧不可问责。
    """

    def test_平衡值在区间内通过(self):
        from ir_agent.stance import check_interval
        assert check_interval(parse_balance("平衡｜销量｜-2%｜8%｜18%｜理由")) == []

    def test_平衡值高于多方被拦(self):
        from ir_agent.stance import check_interval
        probs = check_interval(parse_balance("平衡｜销量｜-2%｜25%｜18%｜理由"))
        assert probs and "销量" in probs[0] and "区间" in probs[0]

    def test_平衡值低于空方被拦(self):
        from ir_agent.stance import check_interval
        probs = check_interval(parse_balance("平衡｜销量｜-2%｜-5%｜18%｜理由"))
        assert probs

    def test_两侧值本身倒挂也被拦(self):
        """空方给的比多方还高 —— 那不是区间，是两侧搞反了。"""
        from ir_agent.stance import check_interval
        probs = check_interval(parse_balance("平衡｜销量｜20%｜10%｜5%｜理由"))
        assert probs and "倒挂" in probs[0]

    def test_边界值算通过(self):
        """平衡点取在端点上是合法的结论（一侧完全说服了裁决者）。"""
        from ir_agent.stance import check_interval
        assert check_interval(parse_balance("平衡｜销量｜-2%｜18%｜18%｜理由")) == []

    def test_纯文字的平衡点不校验区间(self):
        """辩论① 没有数值，这条检查对它无从适用。"""
        from ir_agent.stance import check_interval
        assert check_interval(parse_balance("平衡｜需求侧｜—｜—｜—｜量增价稳")) == []

    def test_问题列表逐项指名(self):
        from ir_agent.stance import check_interval
        probs = check_interval(parse_balance(
            "平衡｜销量｜-2%｜25%｜18%｜理由\n平衡｜售价｜0%｜5%｜10%｜理由"))
        assert len(probs) == 1 and "销量" in probs[0]


class TestAnnotateNotBlock:
    """**核验但不阻断。** 实测三场辩论被契约杀掉（占位符缺失、裸数字、
    分位数后多写一个单位），每次损失一整场。而「核验 + 标注」是这个项目
    已在用的模式 —— 判断项那边写着「N 行依据未能逐字核验（已标注，未阻断，
    需人工复核）」。它不降低成功率，又保留检出能力。

    不阻断的代价是真实的：若某条证据是编的，人要到门②才看到标注。所以标注
    必须进产物、进摘要，而不是只写日志。
    """

    def _led(self):
        from ir_agent.ledger import Fact, FactLedger, Method
        l = FactLedger()
        l.put(Fact(key="revenue.yoy", value=D("-0.0121"), unit="ratio",
                   currency=None, period="2025FY", as_of=date(2026, 4, 20),
                   source_id="calc_x", method=Method.COMPUTED))
        return l

    def test_账本里有的依据不标注(self):
        from ir_agent.stance import annotate
        evs = parse_evidence("多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]")
        got = annotate(evs, ledger=self._led(), body="", as_of=date(2026, 6, 1))
        assert got[0].unverified == ""

    def test_账本里没有的依据被标注但保留(self):
        from ir_agent.stance import annotate
        evs = parse_evidence("多方｜销量｜需求放量｜[[fabricated.key@2025FY]]")
        got = annotate(evs, ledger=self._led(), body="", as_of=date(2026, 6, 1))
        assert len(got) == 1, "核验不过的证据不能被丢掉"
        assert got[0].unverified and "账本" in got[0].unverified
        assert got[0].claim == "需求放量", "原内容必须原样保留"

    def test_正文引文核验走同一套规范化(self):
        """`basis.norm` 剥引号、折叠破折号 —— 茅台那次 `\"十五五` vs
        `“十五五”` 就是在这里判死的。"""
        from ir_agent.stance import annotate
        evs = parse_evidence("空方｜政策｜十五五提法｜「\"十五五」")
        got = annotate(evs, ledger=self._led(),
                       body="中央提出“十五五”时期的新质生产力方向。",
                       as_of=date(2026, 6, 1))
        assert got[0].unverified == ""

    def test_摘要把标注数报出来(self):
        from ir_agent.stance import annotate
        evs = parse_evidence(
            "多方｜销量｜需求放量｜[[fabricated.key@2025FY]]\n"
            "空方｜销量｜周期下行｜[[revenue.yoy@2025FY]]")
        got = annotate(evs, ledger=self._led(), body="", as_of=date(2026, 6, 1))
        s = StanceResult(round_="B", evidence=got).summary()
        assert "1 条证据代码核验不过" in s and "未阻断" in s


class TestRunStance:
    BULL = "多方｜销量｜需求放量｜[[revenue.yoy@2025FY]]"
    BEAR = "空方｜销量｜周期下行｜[[revenue.yoy@2025FY]]"
    BAL = "平衡｜销量｜-2%｜8%｜18%｜中性取份额小幅提升"
    BAD_BAL = "平衡｜销量｜-2%｜25%｜18%｜越界的平衡值"

    def _client(self, *rs):
        seen = []

        def c(p):
            seen.append(p)
            return rs[min(len(seen) - 1, len(rs) - 1)]
        c.seen = seen
        return c

    def _cat(self):
        from ir_agent.analyst import FactCatalog
        from ir_agent.ledger import Fact, FactLedger, Method
        l = FactLedger()
        l.put(Fact(key="revenue.yoy", value=D("-0.0121"), unit="ratio",
                   currency=None, period="2025FY", as_of=date(2026, 4, 20),
                   source_id="calc_x", method=Method.COMPUTED))
        return FactCatalog.from_ledger(l, "2025FY", as_of=date(2026, 6, 1)), l

    def test_三次调用各司其职(self):
        from ir_agent.stance import run_stance
        cat, led = self._cat()
        c = self._client(self.BULL, self.BEAR, self.BAL)
        r = run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                       catalog=cat, ledger=led, as_of=date(2026, 6, 1))
        assert len(c.seen) == 3, "多方、空方、裁决各一次"
        assert "多方" in c.seen[0] and "空方" in c.seen[1]
        assert len(r.evidence) == 2 and len(r.balances) == 1

    def test_裁决者拿到两侧的材料(self):
        """**裁决者必须看到两侧，否则它不是在裁决。**"""
        from ir_agent.stance import run_stance
        cat, led = self._cat()
        c = self._client(self.BULL, self.BEAR, self.BAL)
        run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                   catalog=cat, ledger=led, as_of=date(2026, 6, 1))
        assert "需求放量" in c.seen[2] and "周期下行" in c.seen[2]

    def test_越界的平衡值被驳回重写(self):
        from ir_agent.stance import run_stance
        cat, led = self._cat()
        c = self._client(self.BULL, self.BEAR, self.BAD_BAL, self.BAL)
        r = run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                       catalog=cat, ledger=led, as_of=date(2026, 6, 1))
        assert len(c.seen) == 4, "裁决被驳回一次后重写"
        assert "区间" in c.seen[3], "驳回信息要说清为什么"
        assert r.balances[0].mid == D("0.08")

    def test_反复越界则抛错并带最后一稿(self):
        from ir_agent.stance import StanceFailed, run_stance
        cat, led = self._cat()
        c = self._client(self.BULL, self.BEAR, self.BAD_BAL)
        with pytest.raises(StanceFailed) as e:
            run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                       catalog=cat, ledger=led, as_of=date(2026, 6, 1),
                       max_attempts=2)
        assert "25" in e.value.raw, "最后一稿要带出来给人工补写做底本"

    def test_核验不过的依据不影响流程(self):
        """annotate 不阻断 —— 这条钉住编排层没有把它变成阻断。"""
        from ir_agent.stance import run_stance
        cat, led = self._cat()
        bad = "多方｜销量｜需求放量｜[[fabricated.key@2025FY]]"
        c = self._client(bad, self.BEAR, self.BAL)
        r = run_stance(c, round_="B", subjects=["销量"], context="（证据）",
                       catalog=cat, ledger=led, as_of=date(2026, 6, 1))
        assert len(r.unverified) == 1 and r.balances


class TestTemplate:
    def test_模板按主题列出待填行(self):
        """人工补写的底本。**代码给模板，而不是让人猜格式** —— 格式猜错
        的失败模式是「填完还是读不回」。"""
        from ir_agent.stance import template_json
        t = template_json("B", ["销量", "售价"])
        assert t["round"] == "B"
        subjects = {x["subject"] for x in t["balances"]}
        assert subjects == {"销量", "售价"}
        assert all(x["mid"] is None for x in t["balances"])
        assert t["manual"] == ""


class TestLoadManual:
    """**取消自动降级之后，人工补写是唯一的兜底。** 不存在「跳过」——
    每份研报的多空输入要么来自模型、要么来自人，不会「没有」。
    """

    def _write(self, tmp_path, d):
        import json
        p = tmp_path / "stance_b.json"
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return p

    def test_读回人填的平衡点并记下署名(self, tmp_path):
        from ir_agent.stance import load_manual, template_json
        t = template_json("B", ["销量"])
        t["balances"][0].update(bear="-0.02", mid="0.08", bull="0.18",
                                note="人工判断")
        p = self._write(tmp_path, t)
        r = load_manual(p, round_="B", by="xhl")
        assert r.manual == "xhl" and r.balances[0].mid == D("0.08")
        assert "人工补写" in r.summary() and "xhl" in r.summary()

    def test_人填的平衡点同样要过区间校验(self):
        """**人也会填错。** 区间校验对人和模型一视同仁 —— 它守的是
        「平衡点对两侧可问责」，与谁填的无关。"""
        from ir_agent.stance import StanceFailed, load_manual, template_json
        import json, pathlib, tempfile
        t = template_json("B", ["销量"])
        t["balances"][0].update(bear="-0.02", mid="0.25", bull="0.18", note="越界")
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "stance_b.json").write_text(json.dumps(t, ensure_ascii=False),
                                         encoding="utf-8")
        with pytest.raises(StanceFailed, match="区间"):
            load_manual(d / "stance_b.json", round_="B", by="xhl")

    def test_模板没填就报错(self, tmp_path):
        """未填的模板读回去会得到一组全 None 的平衡点 —— 那等于没有输入，
        而「没有」这条路已经被取消了。"""
        from ir_agent.stance import StanceFailed, load_manual, template_json
        p = self._write(tmp_path, template_json("B", ["销量"]))
        with pytest.raises(StanceFailed, match="还没填"):
            load_manual(p, round_="B", by="xhl")

    def test_署名缺失就报错(self, tmp_path):
        from ir_agent.stance import StanceFailed, load_manual, template_json
        t = template_json("B", ["销量"])
        t["balances"][0].update(bear="-0.02", mid="0.08", bull="0.18", note="x")
        p = self._write(tmp_path, t)
        with pytest.raises(StanceFailed, match="署名"):
            load_manual(p, round_="B", by="")
