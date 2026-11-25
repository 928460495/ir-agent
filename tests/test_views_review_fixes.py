"""阅读版与看板终审（2026-11-25）的致命与重要问题。"""

from __future__ import annotations

import json
import random
import time
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from ir_agent import fieldview, full, mdview, readable
from ir_agent.audit import AuditResult
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.methodology_review import ReviewResult, Verdict

AS_OF = date(2026, 11, 20)
ALLOWED = {"href", "rel", "id", "class", "type", "interestfor", "popovertarget", "popovertargetaction", "aria-expanded",
           "popover", "role", "tabindex", "scope", "value"}


class _Check(HTMLParser):
    VOID = {"br", "img", "meta", "hr", "input", "link"}

    def __init__(self):
        super().__init__()
        self.stack, self.bad, self.ids = [], [], []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k not in ALLOWED:
                self.bad.append(f"属性 {k}={v}")
            if k == "href" and not (v or "").startswith(("http://", "https://", "#")):
                self.bad.append(f"href {v}")
            if k == "id":
                self.ids.append(v)
        if tag in ("script", "style", "iframe", "img", "object"):
            self.bad.append(f"标签 {tag}")
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append(f"标签不配对 {tag}")


def ok_html(h):
    c = _Check()
    c.feed(h)
    c.close()
    assert not c.bad and not c.stack, (c.bad, c.stack, h[:300])
    assert len(c.ids) == len(set(c.ids))


# ── C1 空标题 ────────────────────────────────────────────────

@pytest.mark.parametrize("s", ["# *", "## **", "# **", "** **", "## ** **", "【 】", "#", "# ", "######  "])
def test_C1_空标题不崩(s):
    h = mdview.to_html(f"甲\n{s}\n乙")
    ok_html(h)
    assert "甲" in h and "乙" in h
    mdview.normalize(s)


def test_C1_看板失败不中断阶段(monkeypatch, tmp_path):
    monkeypatch.setattr(full, "build_dashboard", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(full, "build_workbook", lambda *a, **k: None)
    r = SimpleNamespace(ledger=FactLedger(), as_of=AS_OF, period="2025FY", verdicts=None, code="600519")
    skipped: list[str] = []
    full._write_dashboard(r, tmp_path, {}, "600519", {}, skipped)
    assert any("看板" in x for x in skipped)


def test_C1_阅读版失败不中断阶段且记说明(monkeypatch, tmp_path):
    monkeypatch.setattr(readable, "render_views", lambda *a, **k: 1 / 0)
    r = SimpleNamespace(ledger=FactLedger(), as_of=AS_OF, period="2025FY", verdicts=None, code="600519")
    skipped: list[str] = []
    kw = full._readable_views(r, tmp_path, {"行业": "甲"}, {}, skipped)
    assert kw["context"]["行业"] == "甲" and any("阅读版" in x for x in skipped)


# ── C2 表格分隔行 ────────────────────────────────────────────

@pytest.mark.parametrize("row", ["| " + "-" * 60 + " | 备注 |", "|" + "----------|" * 8 + "-|x|", "| :-: | - | --: |"])
def test_C2_分隔行判断不卡死(row):
    t = time.time()
    ok_html(mdview.to_html(f"| a | b |\n{row}\n| 1 | 2 |"))
    assert time.time() - t < 1


def test_C2_单横线与居中写法也认():
    assert "<table>" in mdview.to_html("| a | b |\n| - | :-: |\n| 1 | 2 |")


@pytest.mark.parametrize("s", ["「" * 20000, "#" + " " * 60000 + "x", "**" * 20000, "（⚠" * 8000, "[五力] " + "：" * 20000,
                               "- " * 20000, "\n".join(" " * i + "- x" for i in range(1500))])
def test_C2_大输入不卡死不崩(s):
    t = time.time()
    ok_html(mdview.to_html(s))
    fieldview.layout("strategy", "护城河: 转换成本 | 宽 | " + s[:20000], None)
    assert time.time() - t < 8


# ── C3 注入 ──────────────────────────────────────────────────

@pytest.mark.parametrize("s", [
    "https://a.com/[x](https://b.com/onmouseover=location=name//)", "https://a.com/`code`", "[`c`](https://a.b)",
    "[a](https://a.b/`c`)", "https://a.com/**b**", "[x](https://a.b/\"onclick=1)", "https://a.b/'onclick='x", "`<b>`",
    "甲乙", "[[^1]](https://a.b)", "https://a.com[^1]", "[x](javascript:alert(1))", "<a href=x onclick=y>z</a>"])
def test_C3_只有白名单属性(s):
    ok_html(mdview.to_html(s, {"1": '"><img src=x onerror=1>'}))


def test_C3_随机拼接():
    rnd = random.Random(7)
    frag = ["https://a.b/", "[x](", "https://c.d/e=f//)", "`", "**", "[^1]", "「", "」", "|", "\n", "- ", "# ", "> ", "```", "x=1",
            "", "<", ">", '"', "'", "onload=", "[", "]", "(", ")", "  ", "1. ", "| --- |", "——", "；", "[五力] 甲：提醒　"]
    for _ in range(4000):
        s = "".join(rnd.choice(frag) for _ in range(rnd.randint(1, 18)))
        ok_html(mdview.to_html(s, {"1": "t"}, fold_over=3, fold_heads=("审核意见",)))


# ── I1 补充说明不被收进审核意见 ──────────────────────────────

def test_I1_补充说明在审核意见之前_且不被折叠():
    rv = ReviewResult(verdicts=[Verdict("议价", "对上游", "提醒", "意见甲", "", ""), Verdict("议价", "对下游", "提醒", "意见乙", "", "")])
    out = fieldview.layout("value_chain", "议价: 上游 | 均衡\n- 应付天数处中位\n\n议价说明：补充一段\n\n议价: 下游 | 强\n\n末尾总说明", rv)
    body = out[out.index("### "):]
    assert body.index("补充一段") < body.index("意见甲") < body.index("对下游议价") < body.index("末尾总说明") < body.index("意见乙")
    h = mdview.to_html(out, fold_heads=("审核意见",))
    for det in h.split("<details>")[1:]:
        assert "补充一段" not in det.split("</details>")[0] and "末尾总说明" not in det.split("</details>")[0]
    assert "下游" in body[body.index("意见乙") - 60:body.index("意见乙")]          # 隔着说明时标题带上条目名


# ── I2 挂对条目 ──────────────────────────────────────────────

def _two(a, b):
    return f"护城河: {a} | 宽 | 依据一\n护城河: {b} | 窄 | 依据二"


@pytest.mark.parametrize("a,b", [("无形资产", "无形资产"), ("关税上调", "关税上调扩围"), ("关税上调扩围", "关税上调")])
def test_I2_同名或前缀相同按序号挂对(a, b):
    rv = ReviewResult(verdicts=[Verdict("护城河", f"第1条 {a}", "提醒", "意见一", "", ""),
                                Verdict("护城河", f"第2条 {b}", "致命", "意见二", "", "改")])
    body = fieldview.layout("strategy", _two(a, b), rv)
    body = body[body.index("### "):]
    assert body.index("依据一") < body.index("意见一") < body.index("依据二") < body.index("意见二")


def test_I2_没有序号时精确名优先():
    rv = ReviewResult(verdicts=[Verdict("五力", "替代品威胁", "提醒", "意见", "", "")])
    body = fieldview.layout("strategy", "五力: 替代品威胁加剧 | 高 | 不利 | 甲\n五力: 替代品威胁 | 中 | 中性 | 乙", rv)
    assert body.rindex("替代品威胁 —— 中") < body.index("意见")


# ── I3 同值判断 ──────────────────────────────────────────────

def _led(**kv):
    led = FactLedger()
    for k, (v, u) in kv.items():
        led.put(Fact(key=k.replace("__", "."), value=Decimal(v), unit=u, period="2025FY", as_of=date(2026, 4, 30),
                     source_id="calc_x", method=Method.COMPUTED))
    return led


@pytest.mark.parametrize("text,keep", [
    ("毛利率 [[gross_margin@2025FY]]（75 分位）", "75.20%"),
    ("PE 为 [[pe_ttm@2025FY]]，23 年以来最低", "23.12x"),
    ("营收 [[revenue@2025FY]]，394 家门店", "393.75 亿元"),
    ("ROE [[roe@2025FY]]（15 家同业第 2）", "15.30%"),
    ("[[revenue@2025FY]] 393.75 万元", "393.75 亿元"),
    ("[[gross_margin@2025FY]] 75 家", "75.20%"),
    ("[[gross_margin@2025FY]]（2025 年）", "75.20%"),
    ("[[net_margin@2025FY]] 5.10%", "-5.10%"),
])
def test_I3_不同量纲不同数时数值保留(text, keep):
    led = _led(gross_margin=("0.752", "ratio"), pe_ttm=("23.12", "x"), revenue=("39375000000", "元"), roe=("0.153", "ratio"),
               net_margin=("-0.051", "ratio"))
    assert keep in readable.render_inline(text, led, AS_OF)


@pytest.mark.parametrize("text", ["[[gross_margin@2025FY]] 75.20%", "[[gross_margin@2025FY]] 为 75.2%", "[[revenue@2025FY]] 393.75亿",
                                  "[[revenue@2025FY]] 393.75 亿元", "[[pe_ttm@2025FY]] 23.12 倍", "[[gross_margin@2025FY]]（75.20%）"])
def test_I3_真的重复时仍只显示一次(text):
    led = _led(gross_margin=("0.752", "ratio"), pe_ttm=("23.12", "x"), revenue=("39375000000", "元"))
    out = readable.render_inline(text, led, AS_OF)
    assert "（2025FY）" in out and out.count("75.2") + out.count("393.75") + out.count("23.12") == 1


# ── I4 重出时不亮假绿灯 ──────────────────────────────────────

_BRIEF = ("## 投资要点\n\n营收 393.75 亿元，另有 [[x.y@2025FY]]。\n\n---\n\n## 数据来源\n\n| 数值 | 字段@期间 | 来源 | 披露日 | 取得方式 |\n"
          "| --- | --- | --- | --- | --- |\n| 393.75 亿元 | revenue@2025FY | em_1 | 2026-04-30 | reported |\n")


def test_I4_缺快照与残留占位符重新核对_裸数字写明没核():
    class S:
        def load(self, sid):
            raise KeyError(sid)
    a = full._audit_from_md(_BRIEF, S())
    assert a.missing_snapshots == ["em_1"] and a.unrendered == ["[[x.y@2025FY]]"] and a.partial and a.traceability < 1


# ── I10 ─────────────────────────────────────────────────────

def test_I10_正文里的数据来源标题不截断():
    t = "## 要点\n\n甲\n\n## 数据来源与口径说明\n\n乙\n\n## 数据来源\n\n丙\n" + _BRIEF.split("。\n", 1)[1]
    a = full._audit_from_md(t, None)
    assert "乙" in a.rendered and "丙" in a.rendered and len(a.footnotes) == 1


# ── I5、I6 --views ───────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    r = SimpleNamespace(code="600519", period="2025FY", as_of=AS_OF, ledger=FactLedger(), audit=AuditResult(rendered="草稿"),
                        warnings=[], reconcile=SimpleNamespace(ok=True, checks_run=0, results=[]), store=None, verdicts=None,
                        peer_set=None, sec_meta=None)
    monkeypatch.setattr(full, "_analyse_facts", lambda *a, **k: ({}, {}, []))
    monkeypatch.setattr(full, "_restore_corpus", lambda *a, **k: [])
    (tmp_path / "state.json").write_text(json.dumps({"code": "600519", "period": "2025FY", "as_of": "2026-11-20", "md": {}, "pr": {}}),
                                         encoding="utf-8")
    return r, tmp_path


def test_I5_有DCF工作簿就不重出看板(env):
    r, out = env
    (out / "600519_贵州茅台_DCF_20261120.xlsx").write_bytes(b"x")
    (out / "dashboard.html").write_text("旧", encoding="utf-8")
    notes: list[str] = []
    full.run_views("600519", r, AS_OF, out, notes=notes)
    assert (out / "dashboard.html").read_text(encoding="utf-8") == "旧" and any("看板" in n for n in notes)


def test_I5_缺估值事实时不拿残缺的盖掉已有阅读版(env):
    r, out = env
    (out / "debate_report.md").write_text("多空辩论：1 项挑战\n  ✗ 成立　[逻辑错误] 中性值 [[valuation.dcf_base@2026-11-20]]", encoding="utf-8")
    old = out / "debate_report_阅读版.md"
    old.write_text("中性值 1458.60 元", encoding="utf-8")
    notes: list[str] = []
    full.run_views("600519", r, AS_OF, out, notes=notes)
    assert old.read_text(encoding="utf-8") == "中性值 1458.60 元" and any("debate_report" in n for n in notes)


@pytest.mark.parametrize("argv,word", [(["000001", "--year", "2025"], "代码"), (["600519", "--year", "2024"], "报告期"),
                                       (["600519", "--year", "2025", "--as-of", "2026-12-01"], "时点")])
def test_I6_参数与状态文件不符时拒绝(env, argv, word, capsys):
    from ir_agent import __main__ as cli
    _r, out = env
    assert cli.main([*argv, "--views", str(out)]) == 2
    assert word in capsys.readouterr().err


def test_I6_没给时点就用状态文件的(env, monkeypatch):
    from ir_agent import __main__ as cli
    _r, out = env
    seen = {}
    monkeypatch.setattr(cli, "run", lambda code, year, as_of, **k: seen.update(as_of=as_of) or SimpleNamespace())
    monkeypatch.setattr(full, "run_views", lambda *a, **k: [])
    assert cli.main(["600519", "--year", "2025", "--views", str(out)]) == 0 and seen["as_of"] == AS_OF


# ── I7 ──────────────────────────────────────────────────────

def test_I7_门一门二的阅读版失败也记说明():
    import inspect
    for fn in (full.run_analyse, full.run_brief):
        assert "_write_dashboard(r, out, md, code, arts, skipped)" in inspect.getsource(fn)


# ── I8 ──────────────────────────────────────────────────────

def _tbl(*why):
    return "| 年 | 中性 | 理由 |\n| --- | --- | --- |\n" + "\n".join(f"| 202{i} | {i}% | {w} |" for i, w in enumerate(why))


def test_I8_理由全空要写明未填写():
    out = readable._merge_same_columns(_tbl("—", "同上", "同上"))
    assert "理由：未填写" in out and "| 2020 | 0% |" in out


@pytest.mark.parametrize("why", [("—", "因为A", "因为A"), ("因为A", "", "同上")])
def test_I8_有空有实时整列保留(why):
    out = readable._merge_same_columns(_tbl(*why))
    assert "| 理由 |" in out and "各年相同" not in out


def test_I8_全部相同才合并():
    assert "理由（各年相同）：因为A" in readable._merge_same_columns(_tbl("因为A", "同上", "因为A"))


# ── I9 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("s,keep", [("```营收=1```\n中间\n```毛利=2```", ["营收=1", "中间", "毛利=2"]),
                                    ("``` 开头文字\n码\n``` 收尾文字", ["开头文字", "码", "收尾文字"]),
                                    ("```python\nx = 1\n```\n后", ["x = 1", "后"])])
def test_I9_围栏行上的文字不丢(s, keep):
    h = mdview.to_html(s)
    ok_html(h)
    n = mdview.normalize(s)
    assert all(k in h and k in n for k in keep)


# ── 顺手修的小问题 ────────────────────────────────────────────

def test_M_负号与编号与井号():
    assert "–5.3%" in mdview.normalize("–5.3% 下滑")
    assert 'value="2"' in mdview.to_html("1. 甲\n\n2. 乙") or "<li>乙" in mdview.to_html("1. 甲\n2. 乙")
    assert "C#" in mdview.to_html("## C#")
    assert mdview._cells("||2024|2025|") == ["", "2024", "2025"]


def test_M_模型自己写的脚注记号不挂来源():
    body, notes = readable.render_readable("甲[^1] 与 [[gross_margin@2025FY]] 高", _led(gross_margin=("0.752", "ratio")), AS_OF)
    assert mdview.to_html(body, notes).count('class="num"') == 1


def test_M_一行上万个片段不出乱码():
    h = mdview.to_html("`x` " * 7000)
    assert not any("" <= c <= "" or "豈" <= c <= "﫿" for c in h) and h.count("x") >= 7000
