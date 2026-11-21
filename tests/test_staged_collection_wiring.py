"""第一次调用：各定性模块开工前采集或读回（spec 2026-11-21-staged-collection §2、§3）。不联网。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from ir_agent import full
from ir_agent.sources import openweb
from ir_agent.sources.external import Corpus

AS_OF = date(2026, 11, 20)


def _r():
    members = [SimpleNamespace(code=f"00000{i}", name=f"对手{i}") for i in range(8)]
    return SimpleNamespace(code="600519", quotes=None, corpus=Corpus(as_of=AS_OF), as_of=AS_OF,
                           comps_codes=[f"00000{i}" for i in range(7)],
                           peer_set=SimpleNamespace(members=members,
                                                    classification=SimpleNamespace(used_path="食品饮料")))


def _fake_fetch(monkeypatch):
    monkeypatch.setattr(openweb, "fetch", lambda url, **k: openweb.Fetched(
        final_url=url, text=f"正文 {url}", meta_published=date(2026, 10, 1)))


def _stub_modules(monkeypatch, order):
    def mod(name, md_text):
        def f(*a, **k):
            order.append(f"撰写:{name}")
            return md_text, md_text, ""
        return f
    monkeypatch.setattr(full, "_load_macro_view", mod("macro", "宏观"))
    monkeypatch.setattr(full, "_load_strategy", mod(
        "strategy", "战略分析\n  五力\n    · 供应商议价能力：低 —— 上游分散\n    · 买方议价能力：低 —— 渠道依赖品牌\n    · 替代品威胁：中"))
    monkeypatch.setattr(full, "_load_value_chain", mod("value_chain", "产业链"))
    monkeypatch.setattr(full, "make_client", lambda **k: SimpleNamespace(total_cost_usd=0.0))
    monkeypatch.setattr(full, "_business_brief", lambda code, r: "生产与销售某类消费品")
    monkeypatch.setattr(full, "_company_name", lambda code: "某公司")
    monkeypatch.setattr(full, "_industry_path", lambda code: "某行业")


def _run(tmp_path, client, r=None, **kw):
    md, pr, skipped, stops = {}, {"宏观": ""}, [], []
    full._analyse_qualitative("600519", r or _r(), AS_OF, None, md, pr, skipped, out=tmp_path,
                              stops=stops, statuses=[], research_client=client, **kw)
    return md, skipped


def test_collect_runs_before_each_module_in_order(tmp_path, monkeypatch):
    _fake_fetch(monkeypatch)
    order = []
    _stub_modules(monkeypatch, order)

    def client(p):
        for m, name in openweb.MODULE_NAME.items():
            if f"「{name}」" in p:
                order.append(f"采集:{m}")
        return ""
    _run(tmp_path, client)
    firsts = list(dict.fromkeys(order))              # 补搜会让同一模块的采集出现两次，只看首次
    assert firsts == [
        "采集:macro", "撰写:macro", "采集:strategy", "撰写:strategy", "采集:value_chain", "撰写:value_chain"]
    for m in openweb.MODULES:
        assert (tmp_path / f"openweb_{m}.json").exists()


def test_collect_reuses_existing_module_file(tmp_path, monkeypatch):
    _fake_fetch(monkeypatch)
    _stub_modules(monkeypatch, [])
    first = _r()
    _run(tmp_path, lambda p: "候选: 政策与监管 | 条例 | https://gov.example/a | 某部委 | 2026-10-01", r=first)
    assert any(d.module == "macro" for d in first.corpus.docs)
    calls = []
    again = _r()
    _run(tmp_path, lambda p: calls.append(p) or "", r=again)
    assert calls == [] and any(d.module == "macro" for d in again.corpus.docs)


def test_collect_failure_does_not_stop_module(tmp_path, monkeypatch):
    _fake_fetch(monkeypatch)
    order = []
    _stub_modules(monkeypatch, order)

    def boom(p):
        raise RuntimeError("登录过期")
    _, skipped = _run(tmp_path, boom)
    assert any(s.startswith("宏观定性：开放网络采集未执行") for s in skipped)
    assert "撰写:macro" in order and "撰写:value_chain" in order


def test_strategy_context_has_rivals(tmp_path, monkeypatch):
    _stub_modules(monkeypatch, [])
    prompts = []
    _run(tmp_path, lambda p: prompts.append(p) or "")
    strat = next(p for p in prompts if "「战略分析」" in p)
    assert "对手0" in strat and "对手5" in strat and "对手6" not in strat        # 最多 6 家
    assert "生产与销售某类消费品" in strat


def test_chain_context_from_strategy(tmp_path, monkeypatch):
    _stub_modules(monkeypatch, [])
    prompts = []
    _run(tmp_path, lambda p: prompts.append(p) or "")
    vc = next(p for p in prompts if "「产业链」" in p)
    assert "供应商议价能力：低" in vc and "买方议价能力：低" in vc and "替代品威胁" not in vc


def test_chain_context_empty_when_strategy_stopped(tmp_path, monkeypatch):
    _stub_modules(monkeypatch, [])

    def stop(*a, **k):
        raise full.QualStop("战略分析审核 3 轮未通过")
    monkeypatch.setattr(full, "_load_strategy", stop)
    prompts = []
    _run(tmp_path, lambda p: prompts.append(p) or "")
    vc = next(p for p in prompts if "「产业链」" in p)
    assert "议价能力" not in vc


def test_missing_slot_notes_per_module(tmp_path, monkeypatch):
    _stub_modules(monkeypatch, [])
    _, skipped = _run(tmp_path, lambda p: "")
    text = "\n".join(skipped)
    assert "战略：替代品与新进入者 未取到" in text and "宏观：需求领先指标 未取到" in text


def test_same_url_two_modules_ingested_once(tmp_path, monkeypatch):
    _fake_fetch(monkeypatch)
    _stub_modules(monkeypatch, [])
    line = {"宏观定性": "候选: 政策与监管 | 同一篇 | https://same.example/x | 机构 | 2026-10-01",
            "战略分析": "候选: 竞争格局与份额 | 同一篇 | https://same.example/x | 机构 | 2026-10-01"}
    r = _r()
    _, skipped = _run(tmp_path, lambda p: next((v for k, v in line.items() if f"「{k}」" in p), ""), r=r)
    assert [d.module for d in r.corpus.docs if d.url == "https://same.example/x"] == ["macro"]
    assert "战略：竞争格局与份额 未取到" in "\n".join(skipped)


def test_run_analyse_no_longer_collects_upfront():
    import inspect
    src = inspect.getsource(full.run_analyse)
    assert "_load_openweb(" not in src and "research_client=research_client" in src
