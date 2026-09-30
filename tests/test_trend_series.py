# -*- coding: utf-8 -*-
"""query_trend_series 与 storage.query_trend_series RPC 的测试

趋势页的数据源。刻意不读 pd_trend_data 表——那里的行是旧 signal_router 每 50 帧
写一次的累计值与派生常量（avg=max*0.6、energy=count*max*0.1、noise=0），
不是分箱统计，画到趋势图上会把"累计计数"当成"每箱事件数"。
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.data.models import DatabaseManager
from core.data.monitor_models import PDEventModel
from core.protocol.storage_rpc import StorageRpc
from core.services.storage_service import PDStorageService


def _utc_naive_now():
    """列里存的就是 naive UTC（SQLite 的 DATETIME 会丢掉 tzinfo）。

    必须在播种时现取，不能放模块级：全套跑约 100 秒，模块级常量到本测试执行时
    已陈旧几十秒，"5 秒前"的事件会漂到别的箱里，单跑绿、全套红。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def storage():
    db = DatabaseManager(":memory:")
    svc = PDStorageService(db_manager=db)
    yield svc
    db.close()


def seed(storage, events):
    """events: [(age_seconds, amplitude, channel_id, sensor_type), ...]"""
    now = _utc_naive_now()
    with storage._db_manager.session() as session:
        for age, amp, ch, st in events:
            session.add(
                PDEventModel(
                    device_id="dev1",
                    channel_id=ch,
                    sensor_type=st,
                    timestamp=now - timedelta(seconds=age),
                    amplitude=amp,
                    energy=abs(amp) * 2.0,
                    polarity=0 if amp > 0 else 1,
                )
            )


def test_bins_are_contiguous_and_total_count_matches(storage):
    # 取样点刻意离箱边界远一些：查询用的 now 比模块级 NOW 晚几毫秒，
    # 贴着边界（如 age=60、age=3540）会因这点漂移落到相邻箱，测的就不是补齐逻辑了。
    seed(storage, [(5, 12.0, 1, "uhf"), (1800, 20.0, 1, "uhf"), (3595, 9.5, 1, "uhf")])
    out = storage.query_trend_series("dev1", window="1h", bins=60)

    assert len(out) == 60, "必须补齐空箱，否则前端横轴会按事件数而不是时间铺开"
    assert sum(b["count"] for b in out) == 3
    assert sum(1 for b in out if b["count"] == 0) == 57
    # 箱宽 = 3600/60 = 60s；age 5s -> 最后一箱，age 3595s -> 第一箱
    assert out[-1]["count"] == 1
    assert out[0]["count"] == 1
    assert out[-1]["bin_seconds"] == pytest.approx(60.0)


def test_max_amplitude_uses_absolute_value_per_bin(storage):
    # 同一箱内一正一负，负的那条幅值更大
    seed(storage, [(300, 8.0, 1, "uhf"), (310, -25.0, 1, "uhf")])
    out = storage.query_trend_series("dev1", window="1h", bins=60)
    filled = [b for b in out if b["count"]]

    assert len(filled) == 1
    assert filled[0]["count"] == 2
    assert filled[0]["max_amplitude"] == pytest.approx(25.0)


def test_events_outside_the_window_are_excluded(storage):
    seed(storage, [(60, 10.0, 1, "uhf"), (7200, 99.0, 1, "uhf")])
    out = storage.query_trend_series("dev1", window="1h", bins=60)

    assert sum(b["count"] for b in out) == 1, "2 小时前的事件不该出现在 1h 窗口里"


def test_channel_and_sensor_filters_narrow_the_set(storage):
    seed(
        storage,
        [
            (60, 10.0, 1, "uhf"),
            (70, 11.0, 2, "uhf"),
            (80, 12.0, 1, "ae"),
        ],
    )
    by_channel = storage.query_trend_series("dev1", window="1h", bins=60, channel_id=2)
    by_sensor = storage.query_trend_series("dev1", window="1h", bins=60, sensor_type="ae")

    assert sum(b["count"] for b in by_channel) == 1
    assert sum(b["count"] for b in by_sensor) == 1


def test_bins_are_clamped_into_a_usable_range(storage):
    seed(storage, [(60, 10.0, 1, "uhf")])

    assert len(storage.query_trend_series("dev1", window="1h", bins=1)) == 10
    assert len(storage.query_trend_series("dev1", window="1h", bins=99999)) == 500


def test_timestamp_is_epoch_and_iso_of_the_bin_start(storage):
    out = storage.query_trend_series("dev1", window="1h", bins=60)
    first, last = out[0], out[-1]

    assert last["t"] - first["t"] == pytest.approx(3600 - 60, abs=1.0)
    assert first["iso"].startswith(_utc_naive_now().strftime("%Y-%m-%d"))


# ── RPC 层 ───────────────────────────────────────────


def test_rpc_requires_device_id_and_valid_window(storage):
    rpc = StorageRpc(db_manager=storage._db_manager, storage=storage)

    with pytest.raises(ValueError, match="device_id"):
        rpc.query_trend_series({})
    with pytest.raises(ValueError, match="window"):
        rpc.query_trend_series({"device_id": "dev1", "window": "5m"})


def test_rpc_returns_a_bare_list_for_the_frontend(storage):
    seed(storage, [(120, 15.0, 1, "uhf")])
    rpc = StorageRpc(db_manager=storage._db_manager, storage=storage)
    out = rpc.query_trend_series({"device_id": "dev1", "window": "1h", "bins": 30})

    assert isinstance(out, list) and len(out) == 30
    assert sum(b["count"] for b in out) == 1


def test_rpc_is_only_registered_when_storage_is_present():
    rpc = StorageRpc(db_manager=object(), storage=None)
    names = [n for n, _ in rpc.handlers()]

    assert "storage.query_trend_series" not in names
    assert "device.list" in names, "无 storage 时设备查询仍应可用"


def test_history_device_ids_reports_what_actually_has_data(storage):
    """历史页的设备候选只能来自库里真实出现过的 device_id。

    命名空间错配是实测踩出来的：模拟器以采集设备 id（SIM-001）写事件，
    而 stats.sources 的键是另一套命名、pd_devices 在纯模拟器下是空的。
    用错来源的后果是查询条件全对、结果恒为 0，看起来像"没有数据"。
    """
    seed(storage, [(60, 10.0, 1, "uhf"), (120, 20.0, 2, "uhf"), (300, 30.0, 1, "ae")])
    rows = storage.query_history_device_ids()

    assert [r["device_id"] for r in rows] == ["dev1"]
    assert rows[0]["events"] == 3
    assert rows[0]["first_ts"] and rows[0]["first_ts"] < rows[0]["last_ts"]


def test_history_device_ids_is_empty_not_error_without_data(storage):
    assert storage.query_history_device_ids() == []


def test_history_device_ids_rpc_registered_alongside_storage(storage):
    rpc = StorageRpc(db_manager=storage._db_manager, storage=storage)
    names = [n for n, _ in rpc.handlers()]

    assert "storage.device_ids" in names
    assert "storage.device_ids" not in [n for n, _ in StorageRpc(db_manager=object()).handlers()]
