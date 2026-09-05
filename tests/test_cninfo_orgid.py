from datetime import date

import pytest

from ir_agent.sources.cninfo import UnknownSecurity, lookup_org_id, parse_org_id


class TestParseOrgId:
    def test_extracts_org_id_for_the_matching_code(self):
        payload = [
            {"code": "601012", "orgId": "9900022338", "zwjc": "隆基绿能",
             "category": "A股"},
        ]
        assert parse_org_id(payload, "601012") == "9900022338"

    def test_org_id_is_not_derivable_from_the_code(self):
        """茅台是 gssh0600519，隆基是 9900022338 —— 格式不统一。
        任何 f'gssh0{code}' 式的推导都会在多数标的上失败。"""
        payload = [{"code": "600519", "orgId": "gssh0600519",
                    "zwjc": "贵州茅台", "category": "A股"}]
        assert parse_org_id(payload, "600519") == "gssh0600519"

    def test_picks_the_exact_code_not_the_first_result(self):
        payload = [
            {"code": "601012", "orgId": "wrong", "zwjc": "别的", "category": "A股"},
            {"code": "600183", "orgId": "right", "zwjc": "生益科技", "category": "A股"},
        ]
        assert parse_org_id(payload, "600183") == "right"

    def test_unknown_code_raises_rather_than_guessing(self):
        with pytest.raises(UnknownSecurity, match="999999"):
            parse_org_id([], "999999")

    def test_empty_org_id_is_treated_as_unknown(self):
        with pytest.raises(UnknownSecurity):
            parse_org_id([{"code": "601012", "orgId": "", "category": "A股"}], "601012")


@pytest.mark.live
class TestLiveOrgIdLookup:
    @pytest.mark.parametrize("code,expected", [
        ("600519", "gssh0600519"),
        ("601012", "9900022338"),
    ])
    def test_real_lookup_returns_the_documented_ids(self, code, expected):
        assert lookup_org_id(code) == expected

    def test_announcements_are_found_for_a_non_pattern_org_id(self):
        """隆基的 orgId 不符合 gssh0 模式；查不到公告就等于整条链路断掉。"""
        from ir_agent.sources import cninfo
        anns = cninfo.search("601012", date(2026, 3, 1), date(2026, 6, 30))
        assert anns
