# -*- coding: utf-8 -*-
"""
数据源集成单元测试

覆盖:
- DataSource 抽象基类
- DataSourceConfig 配置序列化/反序列化
- SimulatorSource 生命周期
- DataSourceManager 添加/移除/启动/停止
- auto_detect 回退逻辑
"""

import pytest


# 确保 QApplication 实例存在（PySide6 信号系统需要）
@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ── DataSource 抽象基类 ──────────────────────────────


class TestDataSource:
    """DataSource 抽象基类测试"""

    def test_datasource_type_enum(self):
        from core.foundation.data_source import DataSourceType

        assert DataSourceType.HARDWARE.value == "hardware"
        assert DataSourceType.SIMULATOR.value == "simulator"
        assert DataSourceType.FILE.value == "file"

    def test_datasource_status_enum(self):
        from core.foundation.data_source import DataSourceStatus

        assert DataSourceStatus.STOPPED.value == "stopped"
        assert DataSourceStatus.RUNNING.value == "running"
        assert DataSourceStatus.ERROR.value == "error"
        assert DataSourceStatus.RECONNECTING.value == "reconnecting"

    def test_datasource_is_qobject(self):
        """DataSource 应继承 QObject 以支持信号"""
        from PySide6.QtCore import QObject

        from core.foundation.data_source import DataSource

        assert issubclass(DataSource, QObject)

    def test_datasource_has_signals(self):
        from core.foundation.data_source import DataSource

        assert hasattr(DataSource, "status_changed")
        assert hasattr(DataSource, "error_occurred")


# ── DataSourceConfig ─────────────────────────────────


class TestDataSourceConfig:
    """DataSourceConfig 配置类测试"""

    def test_default_config(self):
        from core.foundation.data_source_config import DataSourceConfig

        cfg = DataSourceConfig()
        assert cfg.startup_policy.mode == "auto_detect"
        assert cfg.startup_policy.discovery_port == 7001
        assert cfg.startup_policy.discovery_timeout_s == 3
        assert cfg.saved_devices == []

    def test_config_to_dict(self):
        from core.foundation.data_source_config import DataSourceConfig, SavedDevice

        cfg = DataSourceConfig()
        cfg.saved_devices.append(
            SavedDevice(
                device_id="TEST-001",
                name="测试设备",
                host="192.168.1.100",
                udp_port=6000,
                coupling_type="uhf",
            )
        )
        d = cfg.to_dict()
        assert "startup_policy" in d
        assert "saved_devices" in d
        assert len(d["saved_devices"]) == 1
        assert d["saved_devices"][0]["device_id"] == "TEST-001"

    def test_config_from_dict(self):
        from core.foundation.data_source_config import DataSourceConfig

        data = {
            "startup_policy": {
                "mode": "simulator_only",
                "discovery_port": 8000,
                "discovery_timeout_s": 5,
            },
            "saved_devices": [
                {
                    "device_id": "HW-001",
                    "name": "硬件1",
                    "host": "10.0.0.1",
                    "udp_port": 6000,
                    "channel_count": 4,
                    "coupling_type": "ae",
                    "sample_rate_hz": 2000000,
                }
            ],
        }
        cfg = DataSourceConfig.from_dict(data)
        assert cfg.startup_policy.mode == "simulator_only"
        assert cfg.startup_policy.discovery_port == 8000
        assert len(cfg.saved_devices) == 1
        assert cfg.saved_devices[0].device_id == "HW-001"
        assert cfg.saved_devices[0].coupling_type == "ae"

    def test_config_roundtrip(self):
        """序列化 → 反序列化 → 值一致"""
        from core.foundation.data_source_config import DataSourceConfig, SavedDevice

        original = DataSourceConfig()
        original.startup_policy.mode = "hardware_only"
        original.saved_devices.append(SavedDevice(device_id="R-001", name="回测"))
        d = original.to_dict()
        restored = DataSourceConfig.from_dict(d)
        assert restored.startup_policy.mode == "hardware_only"
        assert len(restored.saved_devices) == 1
        assert restored.saved_devices[0].name == "回测"


# ── DataSourceManager ────────────────────────────────


class TestDataSourceManager:
    """DataSourceManager 管理器测试"""

    @pytest.fixture
    def manager(self, qapp):
        from unittest.mock import MagicMock

        from core.foundation.data_source_manager import DataSourceManager
        from core.foundation.signal_bus import PDDataBus

        acquisition = MagicMock()
        data_bus = PDDataBus.instance()
        mgr = DataSourceManager(acquisition, data_bus, db_manager=None)
        yield mgr
        mgr.stop_all()

    def test_manager_initial_state(self, manager):
        assert manager.source_count == 0
        assert manager.active_source is None
        assert manager.active_source_id is None
        assert not manager.has_running_source()

    def test_add_simulator_source(self, manager):
        sid = manager.add_simulator_source("uhf", "internal", 20)
        assert sid.startswith("sim-uhf-")
        assert manager.source_count == 1
        source = manager.get_source(sid)
        assert source is not None
        assert source.sensor_type == "uhf"

    def test_add_multiple_sources(self, manager):
        sid1 = manager.add_simulator_source("uhf", "internal", 20)
        sid2 = manager.add_simulator_source("ae", "burst", 20)
        assert sid1 != sid2
        assert manager.source_count == 2

    def test_remove_source(self, manager):
        sid = manager.add_simulator_source("uhf", "internal", 20)
        assert manager.remove_source(sid) is True
        assert manager.source_count == 0
        assert manager.get_source(sid) is None

    def test_remove_nonexistent_source(self, manager):
        assert manager.remove_source("nonexistent") is False

    def test_get_all_status(self, manager):
        manager.add_simulator_source("uhf", "internal", 20)
        status = manager.get_all_status()
        assert len(status) == 1
        for sid, info in status.items():
            assert "status" in info

    def test_set_active_source(self, manager):
        sid = manager.add_simulator_source("uhf", "internal", 20)
        assert manager.set_active_source(sid) is True
        assert manager.active_source_id == sid

    def test_set_active_source_invalid(self, manager):
        assert manager.set_active_source("invalid") is False

    def test_start_all(self, manager):
        manager.add_simulator_source("uhf", "internal", 20)
        started = manager.start_all()
        assert started >= 1
        assert manager.has_running_source()

    def test_stop_all(self, manager):
        manager.add_simulator_source("uhf", "internal", 20)
        manager.start_all()
        manager.stop_all()
        assert not manager.has_running_source()
        assert manager.active_source_id is None

    def test_auto_detect_fallback_to_simulator(self, manager):
        """无硬件时 auto_detect 应回退到模拟器"""
        result = manager.auto_detect()
        assert result is True
        assert manager.source_count >= 1
        # 应至少有一个模拟器
        sources = manager.get_sources()
        assert any(s.source_type.value == "simulator" for s in sources)


# ── PDDataBus 信号扩展 ───────────────────────────────


class TestPDDataBusSignals:
    """PDDataBus 数据源信号测试"""

    def test_bus_has_source_signals(self, qapp):
        from core.foundation.signal_bus import PDDataBus

        bus = PDDataBus.instance()
        assert hasattr(bus, "source_attached")
        assert hasattr(bus, "source_detached")
        assert hasattr(bus, "active_source_changed")

    def test_publish_source_attached(self, qapp):
        from core.foundation.signal_bus import PDDataBus

        bus = PDDataBus.instance()
        received = []
        bus.source_attached.connect(lambda sid: received.append(sid))
        bus.publish_source_attached("TEST-SRC")
        assert "TEST-SRC" in received

    def test_publish_source_detached(self, qapp):
        from core.foundation.signal_bus import PDDataBus

        bus = PDDataBus.instance()
        received = []
        bus.source_detached.connect(lambda sid: received.append(sid))
        bus.publish_source_detached("TEST-SRC")
        assert "TEST-SRC" in received

    def test_publish_active_source_changed(self, qapp):
        from core.foundation.signal_bus import PDDataBus

        bus = PDDataBus.instance()
        received = []
        bus.active_source_changed.connect(lambda sid: received.append(sid))
        bus.publish_active_source_changed("ACTIVE-SRC")
        assert "ACTIVE-SRC" in received


# ── 模拟器脉冲幅值口径（缺陷 #20）──────────────────────


class TestSimulatorPulseAmplitude:
    def test_pulse_peak_reaches_nominal_amplitude(self, monkeypatch):
        """标称注入 100 mV，检出峰值就该是 100 mV。

        原先振荡项用 sin，i=0 处正好过零，脉冲峰值只有标称的 0.30~0.68
        （随 pulse_width 变），于是"注入 100、检出 35"的差额会被误读成标定系数。

        必须钉成**单个脉冲、零噪声**：幅值和相位都取自 random.uniform，若只钉幅值
        则 20 个脉冲全落在同一位置叠加，峰值上千，sin 一样能过——第一版就这么假绿过。
        """
        import random as py_random

        import numpy as np

        from core.utils.simulator import PDSimulator

        sim = PDSimulator(sample_rate=100_000_000, waveform_points=4096, noise_level=0.0)
        monkeypatch.setattr(py_random, "uniform", lambda *_a, **_k: 100.0)
        monkeypatch.setattr(np.random, "poisson", lambda *_a, **_k: 1)
        monkeypatch.setattr(py_random, "gauss", lambda *_a, **_k: 0.0)
        peak = max(abs(x) for x in sim.generate_waveform().samples)
        # 允许基线工频正弦的 ±0.5 余量
        assert 99.0 <= peak <= 101.0, f"脉冲峰值 {peak:.1f} 偏离标称 100，包络与振荡未对齐"
