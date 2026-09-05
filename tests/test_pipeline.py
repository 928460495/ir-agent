from datetime import date, datetime
from decimal import Decimal

import pytest

from ir_agent.audit import audit
from ir_agent.ledger import Fact, FactLedger, Method
from ir_agent.pipeline import disclosure_dates
from ir_agent.sources.cninfo import Announcement
from ir_agent.sources.snapshot import SnapshotStore


def ann(title, d):
    return Announcement(ann_id="1", title=title, ann_date=d, url="u",
                        code="600519", name="贵州茅台",
                        category="定期报告", source_id="s")


class TestDisclosureDates:
    def test_annual_report_maps_to_the_full_year_period(self):
        got = disclosure_dates([ann("贵州茅台2025年年度报告", date(2026, 3, 28))])
        assert got["2025FY"] == date(2026, 3, 28)

    def test_third_quarter_report_maps_to_cumulative_period(self):
        got = disclosure_dates([ann("贵州茅台2025年第三季度报告", date(2025, 10, 25))])
        assert got["2025Q1-Q3"] == date(2025, 10, 25)

    def test_half_year_report(self):
        got = disclosure_dates([ann("贵州茅台2025年半年度报告", date(2025, 8, 8))])
        assert got["2025H1"] == date(2025, 8, 8)

    def test_summary_is_ignored_in_favour_of_the_full_report(self):
        got = disclosure_dates([ann("贵州茅台2025年年度报告摘要", date(2026, 3, 20))])
        assert "2025FY" not in got

    def test_latest_version_wins_when_a_report_is_amended(self):
        """语义已修正: 取最晚而非最早。

        原设计取最早披露日，理由是「更正重发不改变最早可知时点」——
        但这个理由只在数字未变时成立。我们从新浪/东财拿到的是**重述后**的
        数字，把它配上原始披露日 = 凭空获得信息优势。详见 disclosure_dates 文档。
        """
        got = disclosure_dates([
            ann("贵州茅台2025年年度报告（更正后）", date(2026, 5, 10)),
            ann("贵州茅台2025年年度报告", date(2026, 3, 28)),
        ])
        assert got["2025FY"] == date(2026, 5, 10)

    def test_non_periodic_announcements_are_ignored(self):
        got = disclosure_dates([ann("关于变更注册地址的公告", date(2025, 5, 1))])
        assert got == {}


@pytest.fixture
def wired(tmp_path):
    """一个含真实快照的最小账本。"""
    store = SnapshotStore(tmp_path)
    sid = store.save(source="sina", payload={"资产总计": "100"}, url="u",
                     fetched_at=datetime(2026, 3, 28, 9, 0))
    led = FactLedger()
    led.put(Fact(key="revenue", value=Decimal("168838102514.79"), unit="元",
                 currency="CNY", period="2025FY", as_of=date(2026, 3, 28),
                 source_id=sid, method=Method.REPORTED))
    return led, store, sid


class TestAudit:
    def test_fully_referenced_draft_passes(self, wired):
        led, store, sid = wired
        r = audit(f"营业收入 [[{sid}#revenue@2025FY]]。", led, store,
                  as_of=date(2026, 4, 1))
        assert r.ok
        assert r.traceability == 1.0

    def test_bare_number_fails_the_audit(self, wired):
        led, store, sid = wired
        r = audit("营业收入 1688.38 亿元。", led, store, as_of=date(2026, 4, 1))
        assert not r.ok
        assert "1688.38" in " ".join(r.bare_numbers)

    def test_reference_whose_snapshot_is_missing_fails(self, wired):
        led, store, _ = wired
        led.put(Fact(key="orphan", value=Decimal("1"), unit="元", period="2025FY",
                     as_of=date(2026, 3, 28), source_id="ghost_snapshot",
                     method=Method.REPORTED))
        r = audit("[[ghost_snapshot#orphan@2025FY]]", led, store,
                  as_of=date(2026, 4, 1))
        assert not r.ok
        assert "ghost_snapshot" in " ".join(r.missing_snapshots)

    def test_audit_reports_the_rendered_text(self, wired):
        led, store, sid = wired
        r = audit(f"收入 [[{sid}#revenue@2025FY]]。", led, store,
                  as_of=date(2026, 4, 1))
        assert "1688.38 亿元" in r.rendered

    def test_every_number_resolves_to_a_footnote_with_a_disclosure_date(self, wired):
        led, store, sid = wired
        r = audit(f"收入 [[{sid}#revenue@2025FY]]。", led, store,
                  as_of=date(2026, 4, 1))
        assert r.footnotes[0].as_of == date(2026, 3, 28)


@pytest.mark.live
class TestEndToEnd:
    def test_full_run_reconciles_and_passes_audit(self, tmp_path):
        from ir_agent.pipeline import run
        r = run("600519", year=2025, as_of=date.today(),
                snapshot_dir=tmp_path)
        assert r.reconcile.ok, r.reconcile.summary()
        assert r.audit.ok, r.audit.summary()
        assert r.audit.traceability == 1.0
