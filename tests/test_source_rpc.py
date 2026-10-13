# -*- coding: utf-8 -*-
"""`core/protocol/source_rpc.py` 与 DataSourceManager 单源启停的行为测试。

背景：旧 `device_page.py` 把 `start_source_requested` / `stop_source_requested` /
`remove_source_requested` 接到 `source_manager`，`ui/` 归档后这条线断了，设备页
只剩持久化配置 CRUD。这里测的就是补回来的那一层：运行时来源的读 / 启 / 停 / 移除。

重点不在 `DataSourceManager` 本身（`test_data_source.py` 已覆盖），而在 RPC 口径：
- 未知 `source_id` 必须报错，不能静默当成功；
- `start()` 真的失败、或源处于 ERROR/RECONNECTING 时要回传失败，不能假装启动；
- 停掉当前激活源后，激活位要落到另一个**仍在运行**的源上，没有就置空；
- 移除运行时来源**不碰**数据库设备配置（两条路径刻意分开）。
"""

from __future__ import annotations

import pytest

from core.foundation.data_source import DataSource, DataSourceStatus, DataSourceType
from core.protocol.source_rpc import SourceRpc, register_source_rpc


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def db_mock():
    from unittest.mock import MagicMock

    return MagicMock()


@pytest.fixture
def manager(qapp, db_mock):
    from unittest.mock import MagicMock

    from core.foundation.data_source_manager import DataSourceManager
    from core.foundation.signal_bus import PDDataBus

    mgr = DataSourceManager(MagicMock(), PDDataBus.instance(), db_manager=db_mock)
    yield mgr
    mgr.stop_all()


class _StubSource(DataSource):
    """可精确控制 start() 成败与初始状态的假数据源。

    用来测模拟器走不到的两条边界：start() 返回 False、以及源处于 ERROR 态
    （此时外部不该强启，得让数据源自己的重连逻辑管）。
    """

    def __init__(
        self,
        source_id: str,
        start_ok: bool = True,
        status: DataSourceStatus = DataSourceStatus.STOPPED,
    ):
        super().__init__()
        self._id = source_id
        self._status = status
        self._start_ok = start_ok
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._id

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.SIMULATOR

    @property
    def sensor_type(self) -> str:
        return "uhf"

    @property
    def status(self) -> DataSourceStatus:
        return self._status

    def start(self) -> bool:
        self.start_calls += 1
        if not self._start_ok:
            return False
        self._status = DataSourceStatus.RUNNING
        return True

    def stop(self) -> None:
        self.stop_calls += 1
        self._status = DataSourceStatus.STOPPED

    def get_status(self) -> dict:
        return {"id": self._id, "status": self._status.value}

    def get_stats(self) -> dict:
        return {}


def _inject(manager, source: _StubSource) -> str:
    """把假数据源直接写进管理器注册表。

    管理器没有"注册任意来源"的公开入口（正常路径是 add_simulator_source /
    add_hardware_source），而这里要测的正是模拟器复现不了的两条边界。
    """
    manager._sources[source.id] = source
    return source.id


class TestSourceList:
    def test_empty(self, manager):
        assert SourceRpc(manager).source_list({}) == {"sources": {}, "count": 0, "active_source": None}

    def test_matches_manager_status(self, manager):
        """list 与 stats 帧同源：必须逐字等于 get_all_status()，否则两处会各说各话。"""
        sid = manager.add_simulator_source("uhf", "internal", 20)
        out = SourceRpc(manager).source_list({})
        assert out["count"] == 1
        assert set(out["sources"]) == {sid}
        assert out["sources"][sid] == manager.get_all_status()[sid]
        assert out["active_source"] == manager.active_source_id


class TestSourceStart:
    def test_missing_param_raises(self, manager):
        with pytest.raises(ValueError):
            SourceRpc(manager).source_start({})

    def test_unknown_source_raises(self, manager):
        with pytest.raises(ValueError):
            SourceRpc(manager).source_start({"source_id": "nope"})

    def test_start_sets_running_and_claims_active(self, manager):
        sid = manager.add_simulator_source("uhf", "internal", 20)
        out = SourceRpc(manager).source_start({"source_id": sid})
        assert out["started"] is True
        assert out["active_source"] == sid
        assert manager.get_source(sid).status == DataSourceStatus.RUNNING

    def test_start_is_idempotent(self, manager):
        """重复点"启动"不该报错。"""
        sid = manager.add_simulator_source("uhf", "internal", 20)
        rpc = SourceRpc(manager)
        assert rpc.source_start({"source_id": sid})["started"] is True
        assert rpc.source_start({"source_id": sid})["started"] is True

    def test_failed_start_is_reported_not_swallowed(self, manager):
        _inject(manager, _StubSource("sim-bad", start_ok=False))
        with pytest.raises(RuntimeError):
            SourceRpc(manager).source_start({"source_id": "sim-bad"})

    def test_error_state_source_is_not_force_started(self, manager):
        """ERROR 态由数据源自己的重连逻辑管，外部强启要拒绝而不是打断它。"""
        stub = _StubSource("sim-err", status=DataSourceStatus.ERROR)
        _inject(manager, stub)
        with pytest.raises(RuntimeError):
            SourceRpc(manager).source_start({"source_id": "sim-err"})
        assert stub.start_calls == 0


class TestSourceStop:
    def test_unknown_source_raises(self, manager):
        with pytest.raises(ValueError):
            SourceRpc(manager).source_stop({"source_id": "nope"})

    def test_stop_marks_stopped(self, manager):
        sid = manager.add_simulator_source("uhf", "internal", 20)
        rpc = SourceRpc(manager)
        rpc.source_start({"source_id": sid})
        out = rpc.source_stop({"source_id": sid})
        assert out["stopped"] is True
        assert manager.get_source(sid).status == DataSourceStatus.STOPPED

    def test_stop_active_hands_over_to_running_source(self, manager):
        first = manager.add_simulator_source("uhf", "internal", 20)
        second = manager.add_simulator_source("uhf", "internal", 20)
        rpc = SourceRpc(manager)
        rpc.source_start({"source_id": first})
        rpc.source_start({"source_id": second})
        assert manager.active_source_id == first
        out = rpc.source_stop({"source_id": first})
        assert out["active_source"] == second

    def test_stop_last_running_source_clears_active(self, manager):
        sid = manager.add_simulator_source("uhf", "internal", 20)
        rpc = SourceRpc(manager)
        rpc.source_start({"source_id": sid})
        assert rpc.source_stop({"source_id": sid})["active_source"] is None
        assert manager.active_source_id is None


class TestSourceRemove:
    def test_unknown_source_raises(self, manager):
        with pytest.raises(ValueError):
            SourceRpc(manager).source_remove({"source_id": "nope"})

    def test_remove_drops_runtime_source_only(self, manager, db_mock):
        sid = manager.add_simulator_source("uhf", "internal", 20)
        out = SourceRpc(manager).source_remove({"source_id": sid})
        assert out["removed"] is True
        assert manager.get_source(sid) is None
        assert manager.source_count == 0
        # 运行时来源与持久化设备配置是两套对象：移除来源不该动数据库
        assert db_mock.call_count == 0


class TestSourceSetActive:
    def test_set_active(self, manager):
        manager.add_simulator_source("uhf", "internal", 20)
        second = manager.add_simulator_source("uhf", "internal", 20)
        out = SourceRpc(manager).source_set_active({"source_id": second})
        assert out["active_source"] == second
        assert manager.active_source_id == second

    def test_set_active_unknown_raises(self, manager):
        with pytest.raises(ValueError):
            SourceRpc(manager).source_set_active({"source_id": "nope"})


class TestRegistration:
    def test_registers_all_methods_on_main_thread(self, manager):
        """全部走主线程：启停会创建/回收采集线程并 emit Qt 信号，不能丢到线程池。"""

        class _Server:
            def __init__(self):
                self.calls: list[tuple[str, bool]] = []

            def register_rpc(self, name, handler, background=False):
                self.calls.append((name, background))

        server = _Server()
        names = register_source_rpc(server, sources=manager)
        assert set(names) == {
            "source.list",
            "source.start",
            "source.stop",
            "source.remove",
            "source.set_active",
        }
        assert all(background is False for _, background in server.calls)

    def test_no_sources_registers_nothing(self):
        assert SourceRpc(None).handlers() == []
