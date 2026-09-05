from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.sources.resolve import SourceFailed, resolve

D = Decimal


def facts(source_id, **kv):
    return [Fact(key=k, value=D(str(v)), unit="元", currency="CNY",
                 period="2025FY", as_of=date(2026, 3, 20),
                 source_id=source_id, method=Method.REPORTED)
            for k, v in kv.items()]


def ok(fs):
    return lambda: fs


def boom(msg="upstream 503"):
    def _f():
        raise RuntimeError(msg)
    return _f


class TestFailover:
    def test_tier1_success_means_tier2_is_never_called(self):
        called = []

        def tier2():
            called.append(1)
            return facts("em", total_assets=100)

        r = resolve([("sina", ok(facts("sina", total_assets=100))),
                     ("eastmoney", tier2)])
        assert not called
        assert r.primary == "sina"

    def test_tier1_failure_falls_through_to_tier2(self):
        r = resolve([("sina", boom()), ("eastmoney", ok(facts("em", total_assets=100)))])
        assert r.primary == "eastmoney"
        assert r.facts[0].source_id == "em"

    def test_failed_attempt_is_recorded_with_its_error(self):
        r = resolve([("sina", boom("gbk decode failed")),
                     ("eastmoney", ok(facts("em", total_assets=100)))])
        failed = [a for a in r.attempts if a.status == "failed"]
        assert failed[0].source == "sina"
        assert "gbk decode failed" in failed[0].error

    def test_all_sources_failing_raises_with_every_error(self):
        with pytest.raises(SourceFailed) as e:
            resolve([("sina", boom("A")), ("eastmoney", boom("B"))])
        assert "A" in str(e.value) and "B" in str(e.value)

    def test_source_returning_nothing_counts_as_unavailable(self):
        r = resolve([("sina", ok([])), ("eastmoney", ok(facts("em", total_assets=1)))])
        assert r.primary == "eastmoney"
        assert [a.status for a in r.attempts if a.source == "sina"] == ["unavailable"]


class TestCrossCheck:
    def test_agreeing_sources_produce_no_disagreement(self):
        r = resolve([("sina", ok(facts("sina", total_assets=100, revenue=50))),
                     ("eastmoney", ok(facts("em", total_assets=100, revenue=50)))],
                    cross_check=True)
        assert r.disagreements == []

    def test_disagreement_is_reported_with_both_values(self):
        r = resolve([("sina", ok(facts("sina", total_assets=100))),
                     ("eastmoney", ok(facts("em", total_assets=110)))],
                    cross_check=True)
        d = r.disagreements[0]
        assert d.key == "total_assets"
        assert d.values == {"sina": D("100"), "eastmoney": D("110")}

    def test_primary_value_still_wins_on_disagreement(self):
        """分歧要暴露，但不能因此没有可用数字。Tier-1 仍然是权威。"""
        r = resolve([("sina", ok(facts("sina", total_assets=100))),
                     ("eastmoney", ok(facts("em", total_assets=110)))],
                    cross_check=True)
        assert [f.value for f in r.facts if f.key == "total_assets"] == [D("100")]

    def test_rounding_difference_within_tolerance_is_not_a_disagreement(self):
        r = resolve([("sina", ok(facts("sina", total_assets="100.00"))),
                     ("eastmoney", ok(facts("em", total_assets="100.02")))],
                    cross_check=True, rel_tol=D("0.001"))
        assert r.disagreements == []

    def test_keys_only_one_source_has_are_not_disagreements(self):
        """覆盖差异不是数据冲突。新浪没有市值，不代表两源打架。"""
        r = resolve([("sina", ok(facts("sina", total_assets=100))),
                     ("eastmoney", ok(facts("em", total_assets=100, minority_equity=5)))],
                    cross_check=True)
        assert r.disagreements == []

    def test_cross_check_survives_a_failing_secondary(self):
        r = resolve([("sina", ok(facts("sina", total_assets=100))),
                     ("eastmoney", boom())], cross_check=True)
        assert r.facts
        assert r.disagreements == []
        assert r.cross_checked is False

    def test_cross_checked_flag_marks_genuinely_verified_runs(self):
        r = resolve([("sina", ok(facts("sina", total_assets=100))),
                     ("eastmoney", ok(facts("em", total_assets=100)))],
                    cross_check=True)
        assert r.cross_checked is True

    def test_disagreement_carries_relative_size_for_triage(self):
        r = resolve([("sina", ok(facts("sina", total_assets=100))),
                     ("eastmoney", ok(facts("em", total_assets=110)))],
                    cross_check=True)
        assert r.disagreements[0].rel_diff == pytest.approx(D("0.1"))


class TestIntoLedger:
    def test_resolved_facts_go_into_a_ledger_and_reconcile(self):
        led = FactLedger()
        r = resolve([("em", ok(facts(
            "em", total_assets=1000, total_liabilities=600, total_equity=400)))])
        for f in r.facts:
            led.put(f)
        from ir_agent.validate.reconcile import reconcile
        assert reconcile(led, "2025FY", as_of=date(2026, 4, 1)).ok


class TestDisagreementTriage:
    """分歧落在分析期内还是期外，严重性完全不同。
    隆基绿能实测中两源在 2024FY 净利润上差 0.29%（疑似追溯调整），
    但分析 2025FY 时该分歧不影响任何结论。"""

    def _res(self):
        def two_periods(sid, v2024, v2025):
            return [
                Fact(key="net_profit", value=D(v2024), unit="元", currency="CNY",
                     period="2024FY", as_of=date(2026, 3, 20),
                     source_id=sid, method=Method.REPORTED),
                Fact(key="net_profit", value=D(v2025), unit="元", currency="CNY",
                     period="2025FY", as_of=date(2026, 3, 20),
                     source_id=sid, method=Method.REPORTED),
            ]
        return resolve(
            [("sina", ok(two_periods("sina", "-8677451528.22", "-6510000000"))),
             ("eastmoney", ok(two_periods("em", "-8652025422.20", "-6510000000")))],
            cross_check=True)

    def test_out_of_period_disagreement_is_reported(self):
        assert any(d.period == "2024FY" for d in self._res().disagreements)

    def test_disagreements_can_be_filtered_to_the_analysed_period(self):
        r = self._res()
        assert r.disagreements_in("2025FY") == []
        assert len(r.disagreements_in("2024FY")) == 1

    def test_blocking_check_only_considers_the_analysed_period(self):
        assert self._res().has_blocking_disagreement("2025FY") is False
        assert self._res().has_blocking_disagreement("2024FY") is True


class TestDisagreementShapeTriage:
    """分歧的**形状**能区分两类完全不同的病因:
      - 只出现在 FY 期间 → 年报追溯重述（中国神华实测: 所有中期逐项一致，
        仅 2024FY/2025FY 分歧，因同一控制下企业合并需追溯调整比较期）
      - 遍布所有期间     → 字段映射错误（中国国航实测: cash_net_change 全期为 0）
    """

    def _res(self, periods_with_gap):
        def mk(sid, bump):
            return lambda: [
                Fact(key="total_assets",
                     value=D("1000") + (D(bump) if p in periods_with_gap else D("0")),
                     unit="元", currency="CNY", period=p,
                     as_of=date(2026, 3, 20), source_id=sid,
                     method=Method.REPORTED)
                for p in ("2024FY", "2025FY", "2025H1", "2025Q1")]
        return resolve([("em", mk("em", "300")), ("sina", mk("sina", "0"))],
                       cross_check=True)

    def test_annual_only_disagreement_is_flagged_as_restatement(self):
        r = self._res({"2024FY", "2025FY"})
        assert r.disagreement_shape() == "annual_only"

    def test_disagreement_across_all_periods_is_flagged_as_mapping(self):
        r = self._res({"2024FY", "2025FY", "2025H1", "2025Q1"})
        assert r.disagreement_shape() == "all_periods"

    def test_no_disagreement_has_no_shape(self):
        r = self._res(set())
        assert r.disagreement_shape() is None

    def test_shape_appears_in_the_summary_as_a_hypothesis(self):
        r = self._res({"2024FY", "2025FY"})
        assert "追溯重述" in r.summary("2025FY")
