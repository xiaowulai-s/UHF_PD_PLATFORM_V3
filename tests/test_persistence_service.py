# -*- coding: utf-8 -*-
"""PersistenceSubscriber 单元测试

它存在的理由：`--with-storage` 只启动了 PDStorageService 的刷写线程，
但没人往它的队列里塞事件（原先是 ui/controllers/signal_router.py 的活），
所以查询恒空。这里钉的就是"总线发事件 → 落进存储队列"这一跳。
"""

import pytest

from core.foundation.signal_bus import PDDataBus
from core.services.persistence_service import PersistenceSubscriber
from core.services.storage_service import PDStorageService


@pytest.fixture
def bus():
    # 不能用 PDDataBus.instance()：那是进程级单例，套件里 controller 测试的
    # shutdown() 会释放它的 _signals，之后任何 emit 都抛 "Signal source has been deleted"
    return PDDataBus()


@pytest.fixture
def storage():
    return PDStorageService(db_manager=object())


def test_event_from_bus_reaches_storage_queue(bus, storage):
    sub = PersistenceSubscriber(bus, storage)
    sub.start()

    bus.publish_pd_event("dev1", 2, {"amplitude": 42.5, "sensor_type": "uhf", "energy": 1.5})

    assert len(storage._event_queue) == 1
    queued = storage._event_queue[0]
    assert queued["device_id"] == "dev1"
    assert queued["channel_id"] == 2
    assert queued["amplitude"] == pytest.approx(42.5)
    assert sub.stats == {"accepted": 1, "ae_accepted": 0}


def test_stop_unsubscribes_so_events_are_no_longer_queued(bus, storage):
    sub = PersistenceSubscriber(bus, storage)
    sub.start()
    sub.stop()

    bus.publish_pd_event("dev1", 0, {"amplitude": 10.0})

    assert len(storage._event_queue) == 0
    assert sub.stats == {"accepted": 0, "ae_accepted": 0}


def test_double_start_registers_only_once(bus, storage):
    sub = PersistenceSubscriber(bus, storage)
    sub.start()
    sub.start()

    bus.publish_pd_event("dev1", 0, {"amplitude": 7.0})

    assert len(storage._event_queue) == 1, "重复 start 会重复订阅，事件被双写"


def test_invalid_event_is_dropped_by_storage_not_raised(bus, storage):
    """store_pd_event 自己会丢弃缺 amplitude 的事件，订阅器不该因此炸掉采集线程。"""
    sub = PersistenceSubscriber(bus, storage)
    sub.start()

    bus.publish_pd_event("dev1", 0, {"phase": 90.0})

    assert len(storage._event_queue) == 0
