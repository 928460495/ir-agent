import json
from datetime import date, datetime

import pytest

from ir_agent.sources.snapshot import SnapshotStore


@pytest.fixture
def store(tmp_path):
    return SnapshotStore(tmp_path)


class TestWriteAndRead:
    def test_save_returns_a_source_id(self, store):
        sid = store.save(source="cninfo", payload={"a": 1},
                         url="http://x/y", fetched_at=datetime(2025, 10, 25, 9, 0))
        assert isinstance(sid, str) and sid

    def test_raw_payload_round_trips(self, store):
        sid = store.save(source="cninfo", payload={"a": 1},
                         url="http://x/y", fetched_at=datetime(2025, 10, 25, 9, 0))
        assert store.load(sid).payload == {"a": 1}

    def test_snapshot_records_where_and_when_it_came_from(self, store):
        sid = store.save(source="cninfo", payload={"a": 1}, url="http://x/y",
                         fetched_at=datetime(2025, 10, 25, 9, 0),
                         params={"code": "600519"})
        snap = store.load(sid)
        assert snap.url == "http://x/y"
        assert snap.params == {"code": "600519"}
        assert snap.fetched_at == datetime(2025, 10, 25, 9, 0)

    def test_source_id_encodes_the_source_name_for_readability(self, store):
        sid = store.save(source="cninfo", payload={}, url="u",
                         fetched_at=datetime(2025, 10, 25, 9, 0))
        assert sid.startswith("cninfo_")

    def test_unknown_source_id_raises(self, store):
        with pytest.raises(KeyError):
            store.load("cninfo_nonexistent")


class TestContentAddressing:
    def test_identical_payload_from_same_fetch_dedupes(self, store):
        a = store.save(source="s", payload={"x": 1}, url="u",
                       fetched_at=datetime(2025, 10, 25, 9, 0))
        b = store.save(source="s", payload={"x": 1}, url="u",
                       fetched_at=datetime(2025, 10, 25, 9, 0))
        assert a == b

    def test_different_payload_gets_a_different_id(self, store):
        a = store.save(source="s", payload={"x": 1}, url="u",
                       fetched_at=datetime(2025, 10, 25, 9, 0))
        b = store.save(source="s", payload={"x": 2}, url="u",
                       fetched_at=datetime(2025, 10, 25, 9, 0))
        assert a != b

    def test_refetch_at_a_later_time_is_a_distinct_snapshot(self, store):
        """数据源会修订。同一 URL 不同时间的响应必须各自留痕。"""
        a = store.save(source="s", payload={"x": 1}, url="u",
                       fetched_at=datetime(2025, 10, 25, 9, 0))
        b = store.save(source="s", payload={"x": 1}, url="u",
                       fetched_at=datetime(2025, 11, 1, 9, 0))
        assert a != b


class TestPersistence:
    def test_snapshots_survive_a_new_store_instance(self, tmp_path):
        sid = SnapshotStore(tmp_path).save(
            source="s", payload={"x": 1}, url="u",
            fetched_at=datetime(2025, 10, 25, 9, 0))
        assert SnapshotStore(tmp_path).load(sid).payload == {"x": 1}

    def test_stored_file_is_plain_json_for_manual_audit(self, tmp_path):
        store = SnapshotStore(tmp_path)
        sid = store.save(source="s", payload={"x": 1}, url="u",
                         fetched_at=datetime(2025, 10, 25, 9, 0))
        path = store.path_for(sid)
        assert path.suffix == ".json"
        json.loads(path.read_text(encoding="utf-8"))
