from datetime import date
from decimal import Decimal

import pytest

from ir_agent.ledger import Fact, Method
from ir_agent.validate.cross import cross_reconcile

D = Decimal
AS_OF = date(2026, 3, 20)
P = "2025FY"


def facts(sid, **kv):
    return [Fact(key=k, value=D(str(v)), unit="元", currency="CNY", period=P,
                 as_of=AS_OF, source_id=sid, method=Method.REPORTED)
            for k, v in kv.items()]


BALANCED = dict(total_assets=1000, total_liabilities=600, total_equity=400,
                net_profit=80, net_profit_attr_parent=75,
                minority_interest_profit=5)


class TestBothSourcesClean:
    def test_agreeing_and_balanced_sources_produce_no_finding(self):
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": facts("e", **BALANCED)},
                            P, as_of=date(2026, 4, 1))
        assert r.ok
        assert r.per_source["sina"].ok and r.per_source["eastmoney"].ok


class TestFieldMappingBug:
    """两源数值可以逐项一致却仍有错 —— 比如某源的字段映射错位。
    单纯比数值抓不到，独立勾稽能抓到。"""

    def test_secondary_failing_reconciliation_is_flagged(self):
        broken = dict(BALANCED, total_equity=390)     # 资产负债表不平
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": facts("e", **broken)},
                            P, as_of=date(2026, 4, 1))
        assert not r.ok
        assert "eastmoney" in r.failing_sources

    def test_primary_passing_does_not_mask_a_broken_secondary(self):
        broken = dict(BALANCED, total_equity=390)
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": facts("e", **broken)},
                            P, as_of=date(2026, 4, 1))
        assert r.per_source["sina"].ok is True
        assert r.per_source["eastmoney"].ok is False

    def test_disagreement_on_which_checks_pass_is_reported(self):
        broken = dict(BALANCED, minority_interest_profit=9)
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": facts("e", **broken)},
                            P, as_of=date(2026, 4, 1))
        assert "净利润=归母+少数股东损益" in r.divergent_checks

    def test_both_broken_the_same_way_is_still_reported(self):
        broken = dict(BALANCED, total_equity=390)
        r = cross_reconcile({"sina": facts("s", **broken),
                             "eastmoney": facts("e", **broken)},
                            P, as_of=date(2026, 4, 1))
        assert not r.ok
        assert set(r.failing_sources) == {"sina", "eastmoney"}
        assert r.divergent_checks == []        # 两源一致地错，不是分歧


class TestSingleSource:
    def test_one_source_still_reconciles(self):
        r = cross_reconcile({"sina": facts("s", **BALANCED)},
                            P, as_of=date(2026, 4, 1))
        assert r.ok
        assert r.cross_checked is False

    def test_summary_mentions_each_source(self):
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": facts("e", **BALANCED)},
                            P, as_of=date(2026, 4, 1))
        assert "sina" in r.summary() and "eastmoney" in r.summary()


class TestLowCoverageIsNotValidation:
    """只跑了 1/5 项校验的源「通过」，几乎没有验证价值。
    把它和跑满 4/5 的源同等呈现，是虚假安慰 —— 实测中东财对银行/保险
    只能跑 1 项（通用报表模板不适用金融业），却显示「一致通过」。"""

    def _sparse(self):
        # 只有资产负债表三项，其余校验全部 skip
        return facts("e", total_assets=1000, total_liabilities=600,
                     total_equity=400)

    def test_coverage_is_exposed_per_source(self):
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": self._sparse()},
                            P, as_of=date(2026, 4, 1))
        assert r.coverage["sina"] > r.coverage["eastmoney"]

    def test_a_source_below_the_threshold_is_not_counted_as_corroborating(self):
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": self._sparse()},
                            P, as_of=date(2026, 4, 1), min_checks=2)
        assert r.corroborating_sources == ["sina"]
        assert r.cross_checked is False      # 没有第二个有效源 = 未真正交叉验证

    def test_summary_flags_the_weak_source_rather_than_claiming_agreement(self):
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": self._sparse()},
                            P, as_of=date(2026, 4, 1), min_checks=2)
        assert "覆盖不足" in r.summary()
        assert "一致通过" not in r.summary()

    def test_two_well_covered_sources_still_count_as_cross_checked(self):
        r = cross_reconcile({"sina": facts("s", **BALANCED),
                             "eastmoney": facts("e", **BALANCED)},
                            P, as_of=date(2026, 4, 1), min_checks=2)
        assert r.cross_checked is True
        assert set(r.corroborating_sources) == {"sina", "eastmoney"}
