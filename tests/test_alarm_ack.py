# -*- coding: utf-8 -*-
"""告警确认写路径测试（M3.3）。

覆盖的是此前完全缺失的一环：旧版 UI 的"确认"只改表格行状态、重启即丢，
因为 pd_alarms.acknowledged 三列虽有索引却无人写入。
"""

from __future__ import annotations

import pytest

from core.data import DatabaseManager
from core.services.alarm_service import PDAlarmService


def _alarm(device_id: str = "DEV-001", alarm_type: str = "pd_over_limit", level: str = "critical") -> dict:
    return {
        "device_id": device_id,
        "channel_id": 1,
        "alarm_type": alarm_type,
        "level": level,
        "amplitude": 96.0,
        "threshold": 80.0,
        "description": "局放幅值超限",
        "timestamp": 1_700_000_000.0,
    }


@pytest.fixture
def db():
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def service(db):
    return PDAlarmService(db_manager=db)


def test_published_alarm_carries_addressable_id(service, db):
    """发布的告警帧必须带数据库主键，否则前端点"确认"时定位不到行。"""
    received = []
    service._data_bus.pd_alarm_triggered.connect(lambda dev, payload: received.append(payload))

    service.trigger_alarm(_alarm())

    assert len(received) == 1
    alarm_id = received[0]["alarm_id"]
    assert isinstance(alarm_id, int) and alarm_id > 0

    from core.data.monitor_models import PDAlarmModel

    with db.session() as session:
        row = session.get(PDAlarmModel, alarm_id)
        assert row is not None
        assert row.acknowledged is False


def test_acknowledge_persists_and_is_idempotent(service, db):
    received = []
    service._data_bus.pd_alarm_triggered.connect(lambda dev, payload: received.append(payload))
    service.trigger_alarm(_alarm())
    alarm_id = received[0]["alarm_id"]

    result = service.acknowledge([alarm_id], acknowledged_by="张工")
    assert result == {"requested": 1, "acknowledged": 1}

    from core.data.monitor_models import PDAlarmModel

    with db.session() as session:
        row = session.get(PDAlarmModel, alarm_id)
        assert row.acknowledged is True
        assert row.acknowledged_by == "张工"
        assert row.acknowledged_at is not None

    # 重复确认不应再改动任何行
    assert service.acknowledge([alarm_id])["acknowledged"] == 0


def test_acknowledge_reports_unknown_ids(service):
    result = service.acknowledge([999_999])
    assert result["requested"] == 1
    assert result["acknowledged"] == 0


def test_acknowledge_rejects_garbage_input(service):
    with pytest.raises(ValueError):
        service.acknowledge([])
    with pytest.raises(ValueError):
        service.acknowledge(["abc", None])


def test_acknowledge_without_db_raises_clearly():
    """没配数据库时必须明确报错，而不是静默"确认成功"骗过操作员。"""
    svc = PDAlarmService()
    with pytest.raises(RuntimeError, match="数据库"):
        svc.acknowledge([1])


def test_trigger_still_publishes_when_persistence_unavailable():
    """落库失败不能吞掉告警本身——宁可发一条不可确认的告警，也不能不发。"""
    svc = PDAlarmService()
    received = []
    svc._data_bus.pd_alarm_triggered.connect(lambda dev, payload: received.append(payload))

    svc.trigger_alarm(_alarm())

    assert len(received) == 1
    assert received[0]["alarm_id"] is None


def test_rpc_batch_validation():
    from core.protocol.write_rpc import MAX_ACK_BATCH, WriteRpc

    rpc = WriteRpc(alarm=PDAlarmService())
    with pytest.raises(ValueError, match="非空数组"):
        rpc.acknowledge_alarms({})
    with pytest.raises(ValueError, match="非空数组"):
        rpc.acknowledge_alarms({"alarm_ids": []})
    with pytest.raises(ValueError, match="最多"):
        rpc.acknowledge_alarms({"alarm_ids": list(range(MAX_ACK_BATCH + 1))})
