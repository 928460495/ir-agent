"""可比公司分析。

两个设计立场:
  1. **可比名单是显式输入，不是按行业自动推导。** 同行业 ≠ 可比 ——
     茅台与顺鑫农业同属白酒板块却不可比。选谁做可比本身就是分析判断，
     应当带理由写进研报，而不是藏在代码的行业映射里。
  2. **不同公司披露时间不同。** 某个 as_of 上部分同行还没出年报；
     此时必须显式排除并说明，绝不用它们的上一期数据来凑 —— 混用期间的
     可比表看起来完整，实际在拿去年的同行比今年的自己。
"""
from datetime import date
from decimal import Decimal

import pytest

from ir_agent.comps import (
    CompsRow,
    InsufficientPeers,
    PeerSet,
    build_comps,
    percentile_of,
)

D = Decimal


def row(code, name, **m):
    return CompsRow(code=code, name=name, period="2025FY",
                    metrics={k: D(str(v)) for k, v in m.items()})


class TestPeerSet:
    def test_peer_set_requires_a_stated_reason(self):
        """选谁做可比是判断，必须留下理由。"""
        with pytest.raises(ValueError, match="理由"):
            PeerSet(target="600519", peers=["000858"], rationale="")

    def test_target_cannot_be_its_own_peer(self):
        with pytest.raises(ValueError, match="自身"):
            PeerSet(target="600519", peers=["600519", "000858"],
                    rationale="高端白酒")

    def test_duplicate_peers_are_rejected(self):
        with pytest.raises(ValueError, match="重复"):
            PeerSet(target="600519", peers=["000858", "000858"],
                    rationale="高端白酒")

    def test_valid_set_keeps_order(self):
        ps = PeerSet(target="600519", peers=["000858", "000568"],
                     rationale="高端白酒，营收与渠道结构可比")
        assert ps.peers == ["000858", "000568"]


class TestPercentile:
    def test_middle_value(self):
        assert percentile_of(D("20"), [D("10"), D("20"), D("30")]) == D("50")

    def test_lowest_and_highest(self):
        vals = [D("10"), D("20"), D("30")]
        assert percentile_of(D("10"), vals) == D("0")
        assert percentile_of(D("30"), vals) == D("100")

    def test_value_outside_the_range_is_clamped_to_the_ends(self):
        assert percentile_of(D("5"), [D("10"), D("20")]) == D("0")

    def test_single_peer_gives_no_meaningful_percentile(self):
        assert percentile_of(D("10"), [D("10")]) is None


class TestBuildComps:
    ROWS = [
        row("600519", "贵州茅台", pb=D("6.80"), roe=D("0.3365"), gross_margin=D("0.9118")),
        row("000858", "五粮液", pb=D("2.33"), roe=D("0.0747"), gross_margin=D("0.7754")),
        row("000568", "泸州老窖", pb=D("4.10"), roe=D("0.2100"), gross_margin=D("0.8600")),
    ]

    def test_target_row_is_marked(self):
        t = build_comps("600519", self.ROWS, period="2025FY", as_of=date(2026, 6, 1))
        assert t.target_row.name == "贵州茅台"

    def test_percentiles_are_computed_per_metric(self):
        t = build_comps("600519", self.ROWS, period="2025FY", as_of=date(2026, 6, 1))
        assert t.percentile("pb") == D("100")        # 茅台 PB 最高
        assert t.percentile("roe") == D("100")

    def test_median_is_reported_for_each_metric(self):
        t = build_comps("600519", self.ROWS, period="2025FY", as_of=date(2026, 6, 1))
        assert t.median("pb") == D("4.10")

    def test_metric_missing_from_some_peers_uses_only_those_that_have_it(self):
        rows = self.ROWS + [row("000596", "古井贡酒", pb=D("3.00"))]
        t = build_comps("600519", rows, period="2025FY", as_of=date(2026, 6, 1))
        assert t.coverage("roe") == 3
        assert t.coverage("pb") == 4

    def test_fewer_than_three_names_refuses_to_compute_percentiles(self):
        """两家公司的「分位数」没有意义，给出会误导。"""
        with pytest.raises(InsufficientPeers, match="至少"):
            build_comps("600519", self.ROWS[:2], period="2025FY",
                        as_of=date(2026, 6, 1))

    def test_target_absent_from_rows_is_an_error(self):
        with pytest.raises(ValueError, match="目标"):
            build_comps("601398", self.ROWS, period="2025FY",
                        as_of=date(2026, 6, 1))


class TestDisclosureTimingIsRespected:
    """可比分析天然的时点陷阱: 同行披露时间不同。"""

    def test_peer_not_yet_disclosed_is_excluded_with_a_reason(self):
        from ir_agent.comps import Exclusion
        t = build_comps("600519", TestBuildComps.ROWS, period="2025FY",
                        as_of=date(2026, 6, 1),
                        excluded=[Exclusion("000596", "古井贡酒",
                                            "2025FY 于 2026-06-15 才披露")])
        assert t.excluded[0].code == "000596"
        assert "2026-06-15" in t.excluded[0].reason

    def test_excluded_peers_do_not_enter_the_statistics(self):
        from ir_agent.comps import Exclusion
        t = build_comps("600519", TestBuildComps.ROWS, period="2025FY",
                        as_of=date(2026, 6, 1),
                        excluded=[Exclusion("000596", "古井贡酒", "尚未披露")])
        assert t.coverage("pb") == 3

    def test_summary_names_the_excluded_peers(self):
        from ir_agent.comps import Exclusion
        t = build_comps("600519", TestBuildComps.ROWS, period="2025FY",
                        as_of=date(2026, 6, 1),
                        excluded=[Exclusion("000596", "古井贡酒", "尚未披露")])
        s = t.summary()
        assert "古井贡酒" in s and "尚未披露" in s

    def test_all_rows_must_share_the_same_period(self):
        """混用期间的可比表看起来完整，实际在拿去年的同行比今年的自己。"""
        mixed = TestBuildComps.ROWS[:2] + [
            CompsRow(code="000568", name="泸州老窖", period="2024FY",
                     metrics={"pb": D("4.1")})]
        with pytest.raises(ValueError, match="期间"):
            build_comps("600519", mixed, period="2025FY", as_of=date(2026, 6, 1))


class TestFetchPeerRow:
    """取数层: 时点不可得必须变成显式排除，而不是静默略过。"""

    def test_undisclosed_peer_becomes_an_exclusion(self):
        from ir_agent.comps import fetch_row
        row, exc = fetch_row("000858", period="2025FY", as_of=date(2026, 1, 1),
                             fetch=_stub_undisclosed)
        assert row is None
        assert exc and "披露" in exc.reason

    def test_fetch_failure_is_distinguished_from_not_disclosed(self):
        """「抓取失败」是故障，「尚未披露」是正常 —— 混为一谈会掩盖故障。"""
        from ir_agent.comps import fetch_row
        _, exc = fetch_row("000858", period="2025FY", as_of=date(2026, 6, 1),
                           fetch=_stub_boom)
        assert "抓取失败" in exc.reason

    def test_successful_fetch_yields_a_row_with_metrics(self):
        from ir_agent.comps import fetch_row
        row, exc = fetch_row("000858", period="2025FY", as_of=date(2026, 6, 1),
                             fetch=_stub_ok)
        assert exc is None
        assert row.name == "五粮液"
        assert row.metrics["roe"] == D("0.0747")


def _stub_undisclosed(code, period, as_of):
    from ir_agent.ledger import LookAheadError
    raise LookAheadError(f"{code} 的 {period} 于 2026-04-25 才披露")


def _stub_boom(code, period, as_of):
    raise RuntimeError("connection reset")


def _stub_ok(code, period, as_of):
    return {"name": "五粮液",
            "metrics": {"roe": D("0.0747"), "pb": D("2.33")}}


class TestReadableOutput:
    """指标量纲不同: 比率要显示成百分比，倍数要带 x，且必须限精度。
    直接打 Decimal 原始精度（0.5556903885022351092507088465）没法看。"""

    ROWS = TestBuildComps.ROWS

    def test_ratio_metrics_render_as_percent(self):
        t = build_comps("600519", self.ROWS, period="2025FY", as_of=date(2026, 6, 1))
        assert "33.65%" in t.summary(["roe"])

    def test_multiple_metrics_render_with_x(self):
        t = build_comps("600519", self.ROWS, period="2025FY", as_of=date(2026, 6, 1))
        assert "6.80x" in t.summary(["pb"])

    def test_no_raw_decimal_precision_leaks(self):
        t = build_comps("600519", self.ROWS, period="2025FY", as_of=date(2026, 6, 1))
        s = t.summary()
        assert "0.3364977" not in s and len(max(s.split(), key=len)) < 30

    def test_metric_labels_are_chinese(self):
        t = build_comps("600519", self.ROWS, period="2025FY", as_of=date(2026, 6, 1))
        assert "ROE" in t.summary(["roe"]) or "净资产收益率" in t.summary(["roe"])

    def test_percentile_reads_as_position_not_bare_number(self):
        t = build_comps("600519", self.ROWS, period="2025FY", as_of=date(2026, 6, 1))
        assert "分位" in t.summary(["pb"])


class TestCompsFactsEnterTheLedger:
    """分位数必须进事实账本，否则分析师无法在正文中引用 ——
    实跑时正文写「毛利率处于 100 分位」被审计判为裸数字，正是因为
    这个数字在账本里不存在。"""

    def _table(self):
        return build_comps("600519", TestBuildComps.ROWS,
                           period="2025FY", as_of=date(2026, 6, 1))

    def test_percentile_is_written_as_a_computed_fact(self):
        from ir_agent.comps import comps_facts
        from ir_agent.ledger import FactLedger, Method
        led = FactLedger()
        comps_facts(self._table(), led)
        f = led.get("pb.percentile", "2025FY", as_of=date(2026, 6, 1))
        assert f.value == D("100")
        assert f.method is Method.COMPUTED

    def test_median_is_also_recorded(self):
        from ir_agent.comps import comps_facts
        from ir_agent.ledger import FactLedger
        led = FactLedger()
        comps_facts(self._table(), led)
        assert led.get("pb.peer_median", "2025FY",
                       as_of=date(2026, 6, 1)).value == D("4.10")

    def test_peer_count_is_recorded_so_the_reader_knows_the_base(self):
        """「100 分位」在 3 家里和在 30 家里说服力完全不同。"""
        from ir_agent.comps import comps_facts
        from ir_agent.ledger import FactLedger
        led = FactLedger()
        comps_facts(self._table(), led)
        assert led.get("peer_count", "2025FY",
                       as_of=date(2026, 6, 1)).value == D("3")

    def test_facts_are_referenceable_by_the_analyst(self):
        from ir_agent.analyst import FactCatalog
        from ir_agent.comps import comps_facts
        from ir_agent.ledger import FactLedger
        led = FactLedger()
        comps_facts(self._table(), led)
        cat = FactCatalog.from_ledger(led, "2025FY", as_of=date(2026, 6, 1))
        assert "[[pb.percentile@2025FY]]" in cat.tokens

    def test_metric_absent_from_the_target_produces_no_percentile(self):
        from ir_agent.comps import comps_facts
        from ir_agent.ledger import FactLedger, LookAheadError
        led = FactLedger()
        comps_facts(self._table(), led)
        with pytest.raises(KeyError):
            led.get("pe_ttm.percentile", "2025FY", as_of=date(2026, 6, 1))
