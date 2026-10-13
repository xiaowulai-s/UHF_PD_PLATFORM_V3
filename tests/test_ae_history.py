# -*- coding: utf-8 -*-
"""AE 命中持久化（`ae_hits` 表 + `store_ae_hit` + `storage.query_ae_hits`）的行为测试。

要守住的三件事：

1. **AE 独有字段真的落库了**。`pd_events` 也有 `sensor_type="ae"` 的行，但
   `rms / asl_db / signal_strength / avg_frequency_khz` 在那张表里没有列，
   只写 `pd_events` 等于把这几个量丢掉——而"AE 特征分析"要看的正是它们。
2. **上游多传的键不能让写入炸掉**。`AeHitModel(**data)` 遇到未知列会抛 TypeError，
   而命中的特征字典是上游算出来的，字段集随实现变化。
3. **跨重启可读**。查询走的是表，不是前端那个连接期内存列表。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.data.monitor_models import AeHitModel
from core.services.persistence_service import PersistenceSubscriber
from core.services.storage_service import PDStorageService


@pytest.fixture
def storage():
    from core.data import DatabaseManager

    db = DatabaseManager(":memory:")
    svc = PDStorageService(db_manager=db, flush_interval=0.05)
    # 不 start()：不启后台刷写线程，测试里显式调 _flush_ae_hits()
    yield svc, db
    svc.stop()
    db.close()


def _since(**kwargs) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kwargs)


FULL_HIT = {
    "amplitude": 12.5,
    "rise_time_us": 3.2,
    "duration_us": 41.0,
    "counts": 7,
    "marse_energy": 88.5,
    "avg_frequency_khz": 152.0,
    "rms": 1.75,
    "asl_db": 33.5,
    "signal_strength": 44.0,
    "energy": 21.5,
    "phase_deg": 271.0,
    "polarity": 1,
    "signal_quality": 82,
}


class TestStoreAndQuery:
    def test_round_trip_keeps_ae_specific_fields(self, storage):
        svc, _db = storage
        svc.store_ae_hit("sim-ae-1", 0, dict(FULL_HIT))
        svc._flush_ae_hits()

        rows = svc.query_ae_hits("sim-ae-1", _since(hours=1))
        assert len(rows) == 1
        row = rows[0]
        # 这几个就是 pd_events 装不下的字段
        assert row["rms"] == pytest.approx(1.75)
        assert row["asl_db"] == pytest.approx(33.5)
        assert row["signal_strength"] == pytest.approx(44.0)
        assert row["avg_frequency_khz"] == pytest.approx(152.0)
        assert row["amplitude"] == pytest.approx(12.5)
        assert row["counts"] == 7
        assert row["phase_deg"] == pytest.approx(271.0)
        assert row["timestamp"]  # 跨重启复盘靠它

    def test_unknown_keys_are_dropped_not_fatal(self, storage):
        """上游多传的键必须被丢弃，不能让 AeHitModel(**data) 抛 TypeError。"""
        svc, _db = storage
        svc.store_ae_hit("sim-ae-1", 0, {**FULL_HIT, "debug_trace": "x", "_internal": 1})
        svc._flush_ae_hits()
        assert len(svc.query_ae_hits("sim-ae-1", _since(hours=1))) == 1

    def test_hit_without_amplitude_is_rejected(self, storage):
        svc, _db = storage
        svc.store_ae_hit("sim-ae-1", 0, {"rms": 1.0})
        svc._flush_ae_hits()
        assert svc.query_ae_hits("sim-ae-1", _since(hours=1)) == []

    def test_missing_optional_fields_are_null(self, storage):
        svc, _db = storage
        svc.store_ae_hit("sim-ae-1", 0, {"amplitude": 5.0})
        svc._flush_ae_hits()
        row = svc.query_ae_hits("sim-ae-1", _since(hours=1))[0]
        assert row["rms"] is None
        assert row["amplitude"] == pytest.approx(5.0)

    def test_channel_filter(self, storage):
        svc, _db = storage
        svc.store_ae_hit("sim-ae-1", 0, dict(FULL_HIT))
        svc.store_ae_hit("sim-ae-1", 1, dict(FULL_HIT))
        svc._flush_ae_hits()
        assert len(svc.query_ae_hits("sim-ae-1", _since(hours=1))) == 2
        only1 = svc.query_ae_hits("sim-ae-1", _since(hours=1), channel_id=1)
        assert [r["channel_id"] for r in only1] == [1]

    def test_device_isolation(self, storage):
        svc, _db = storage
        svc.store_ae_hit("dev-a", 0, dict(FULL_HIT))
        svc.store_ae_hit("dev-b", 0, dict(FULL_HIT))
        svc._flush_ae_hits()
        assert len(svc.query_ae_hits("dev-a", _since(hours=1))) == 1

    def test_time_window_excludes_old_rows(self, storage):
        svc, db = storage
        with db.session() as session:
            session.add(
                AeHitModel(
                    device_id="dev-a",
                    channel_id=0,
                    amplitude=1.0,
                    timestamp=datetime.now(timezone.utc) - timedelta(days=3),
                )
            )
        assert svc.query_ae_hits("dev-a", _since(hours=1)) == []
        assert len(svc.query_ae_hits("dev-a", _since(days=7))) == 1

    def test_stats_report_ae_counters(self, storage):
        svc, _db = storage
        svc.store_ae_hit("sim-ae-1", 0, dict(FULL_HIT))
        svc._flush_ae_hits()
        stats = svc.get_statistics()
        assert stats["ae_hits_stored"] == 1
        assert "ae_queue_size" in stats


class TestSubscriberWiring:
    def test_subscriber_persists_ae_hit_events(self, qapp, storage):
        """这条链断了的话，`ae_hits` 表永远空——与 pd_events 当初的故障同型。"""
        from core.foundation.signal_bus import PDDataBus

        svc, _db = storage
        bus = PDDataBus.instance()
        sub = PersistenceSubscriber(bus, svc)
        sub.start()
        try:
            bus.publish_ae_hit("sim-ae-1", 0, dict(FULL_HIT))
            bus.publish_ae_hit("sim-ae-1", 0, {**FULL_HIT, "amplitude": 20.0})
        finally:
            sub.stop()
        svc._flush_ae_hits()

        assert sub.stats["ae_accepted"] == 2
        rows = svc.query_ae_hits("sim-ae-1", _since(hours=1))
        assert len(rows) == 2

    def test_subscriber_does_not_write_after_stop(self, qapp, storage):
        from core.foundation.signal_bus import PDDataBus

        svc, _db = storage
        bus = PDDataBus.instance()
        sub = PersistenceSubscriber(bus, svc)
        sub.start()
        sub.stop()
        bus.publish_ae_hit("sim-ae-1", 0, dict(FULL_HIT))
        svc._flush_ae_hits()
        assert svc.query_ae_hits("sim-ae-1", _since(hours=1)) == []


class TestCleanup:
    def test_old_ae_hits_are_cleaned(self, storage):
        """ae_hits 必须在留存清理的名单里，否则这张表只增不减。"""
        svc, db = storage
        with db.session() as session:
            session.add(
                AeHitModel(
                    device_id="dev-a",
                    channel_id=0,
                    amplitude=1.0,
                    timestamp=datetime.now(timezone.utc) - timedelta(days=400),
                )
            )
        assert svc.cleanup_old_data() >= 1
        assert svc.query_ae_hits("dev-a", _since(days=3650)) == []


class TestStorageRpc:
    def _rpc(self, storage):
        from core.protocol.storage_rpc import StorageRpc

        svc, db = storage
        return StorageRpc(db_manager=db, storage=svc)

    def test_registers_query_method(self, storage):
        names = [name for name, _ in self._rpc(storage).handlers()]
        assert "storage.query_ae_hits" in names

    def test_requires_device_id(self, storage):
        with pytest.raises(ValueError):
            self._rpc(storage).query_ae_hits({})

    def test_returns_rows(self, storage):
        svc, _db = storage
        svc.store_ae_hit("sim-ae-1", 0, dict(FULL_HIT))
        svc._flush_ae_hits()
        rows = self._rpc(storage).query_ae_hits({"device_id": "sim-ae-1", "start_time": _since(hours=1).isoformat()})
        assert len(rows) == 1
        assert rows[0]["asl_db"] == pytest.approx(33.5)

    def test_limit_is_clamped(self, storage):
        from core.protocol.storage_rpc import MAX_LIMIT

        rpc = self._rpc(storage)
        # 越界值不该报错，也不该原样透传
        assert rpc.query_ae_hits({"device_id": "x", "limit": 10**9}) == []
        assert MAX_LIMIT == 5000
