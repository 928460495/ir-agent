"""资料采集按模块拆分（spec 2026-11-21-staged-collection §1、§5.3）。不联网。"""

from __future__ import annotations

from datetime import date

import pytest

from ir_agent.sources import openweb
from ir_agent.sources.external import Corpus

AS_OF = date(2026, 11, 20)


def _fake_fetch(monkeypatch):
    def fake(url, **k):
        return openweb.Fetched(final_url=url, text=f"正文 {url}", meta_published=date(2026, 10, 1))
    monkeypatch.setattr(openweb, "fetch", fake)


def _line(slot, i, scope=None):
    tail = f" | {scope}" if scope is not None else ""
    return f"候选: {slot} | 标题{i} | https://example{i}.org/a{i} | 某机构 | 2026-10-0{i % 9 + 1}{tail}"


def test_module_slots_cover_all_old_slots():
    cn = set().union(*(openweb.module_slots(m, "600519") for m in openweb.MODULES))
    assert set(openweb.SLOTS) <= cn
    assert "需求领先指标" in openweb.module_slots("macro", "600519")
    assert "替代品与新进入者" in openweb.module_slots("strategy", "600519")
    for s in cn:
        assert openweb.THEME_GUIDE_CN[s]


def test_gather_module_retries_empty_slots_once(monkeypatch):
    _fake_fetch(monkeypatch)
    prompts = []

    def client(p):
        prompts.append(p)
        return _line("竞争格局与份额", 1) if len(prompts) == 1 else ""
    res = openweb.gather_module(client, "strategy", code="600519", company="贵州茅台", industry="白酒",
                                as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert len(prompts) == 2
    assert "替代品与新进入者" in prompts[1] and "竞争格局与份额：" not in prompts[1]
    assert set(res.missing_slots) == {"替代品与新进入者", "公司重大动态"}


def test_gather_module_no_retry_when_all_slots_filled(monkeypatch):
    _fake_fetch(monkeypatch)
    calls = []

    def client(p):
        calls.append(p)
        return "\n".join([_line("政策与监管", 1), _line("需求领先指标", 2)])
    openweb.gather_module(client, "macro", code="600519", company="贵州茅台", industry="白酒",
                          as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    assert len(calls) == 1


def test_gather_module_tags_docs_and_scope(monkeypatch):
    _fake_fetch(monkeypatch)
    raw = "\n".join([_line("竞争格局与份额", 1, "规模以上，月度"), _line("公司重大动态", 2),
                     _line("替代品与新进入者", 3)])
    c = Corpus(as_of=AS_OF)
    res = openweb.gather_module(lambda p: raw, "strategy", code="600519", company="贵州茅台",
                                industry="白酒", as_of=AS_OF, corpus=c)
    assert {d.module for d in res.docs} == {"strategy"}
    assert {d.scope for d in res.docs} == {"规模以上，月度", ""}


def test_module_prompt_has_context_and_rules():
    kw = dict(company="贵州茅台", code="600519", industry="白酒", as_of=AS_OF)
    p = openweb.module_prompt("strategy", context={"主要对手": "五粮液、山西汾酒"},
                              slots=openweb.module_slots("strategy", "600519"), **kw)
    assert "五粮液、山西汾酒" in p and "数据发布页优于对它的解读文章" in p
    assert "两套都给" in p and "最近一期" in p and "<统计口径与频率>" in p
    assert "本行业专属主题" not in p
    vc = openweb.module_prompt("value_chain", context={}, slots=openweb.module_slots("value_chain", "600519"), **kw)
    assert "本行业专属主题" in vc


def test_doc_module_roundtrip(monkeypatch, tmp_path):
    _fake_fetch(monkeypatch)
    raw = _line("政策与监管", 1, "全口径，季度")
    res = openweb.gather_module(lambda p: raw, "macro", code="600519", company="x", industry="y",
                                as_of=AS_OF, corpus=Corpus(as_of=AS_OF))
    openweb.save(res, tmp_path / "m.json")
    back = openweb.load(tmp_path / "m.json", Corpus(as_of=AS_OF))
    assert back.docs[0].module == "macro" and back.docs[0].scope == "全口径，季度"
    # 旧文件没有这两个键
    import json
    j = json.loads((tmp_path / "m.json").read_text("utf-8"))
    for d in j["docs"]:
        d.pop("module"); d.pop("scope")
    (tmp_path / "old.json").write_text(json.dumps(j, ensure_ascii=False), "utf-8")
    old = openweb.load(tmp_path / "old.json", Corpus(as_of=AS_OF))
    assert old.docs[0].module == "" and old.docs[0].scope == ""


def test_prompts_neutral():
    banned = ("白酒", "封测", "覆铜板", "批价")
    texts = [openweb.module_prompt(m, context={}, slots=openweb.module_slots(m, code),
                                   company="X", code=code, industry="Y", as_of=AS_OF)
             for m in openweb.MODULES for code in ("600000",)]
    texts += list(openweb.THEME_GUIDE_CN.values())
    for t in texts:
        assert not any(b in t for b in banned)


def test_cn_rules_professional():
    p = openweb.module_prompt("macro", context={}, slots=openweb.module_slots("macro", "600519"),
                              company="X", code="600519", industry="Y", as_of=AS_OF)
    for k in ("国家统计局", "行业协会", "只作线索", "两套都给", "最近一期", "非数据"):
        assert k in p


def test_legacy_prompts_neutral():
    """旧的整池采集提示词（改动前产物的路径与测试仍在用）同样不得只举单一行业。"""
    for t in (openweb.PROMPT,):
        assert not any(b in t for b in ("白酒", "封测", "覆铜板", "批价"))
