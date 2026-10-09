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


class TestSimulatorDeviceIdUniqueness:
    """回归：多个 UHF 模拟源必须各自独立成一路，不能共用 PDSimulator 的类默认 id。

    原先 `SimulatorSource.start()` 只在 AE 分支调 `set_device_info`，UHF 分支漏了，
    于是每个 UHF 源都退回类默认 "SIM-001"。后果是两层串台：
      - ProcessorManager 以 (device, channel) 为键 → 四路共用同一套 PRPD/FFT/RingBuffer；
      - 推流合并键 (topic, device, channel) 也跟着撞 → 同一 flush 窗口后帧顶掉前帧。
    实测 N=4 时内核处理 79.5 fps、客户端只收到 50.6 fps，而 dropped=0、coalesced=706
    ——这笔缺口会被误读成"背压丢弃"，所以必须先修命名才能谈 M7.3 多通道线性度。
    """

    @pytest.fixture
    def manager(self, qapp):
        from unittest.mock import MagicMock

        from core.foundation.data_source_manager import DataSourceManager
        from core.foundation.signal_bus import PDDataBus

        acquisition = MagicMock()
        mgr = DataSourceManager(acquisition, PDDataBus.instance(), db_manager=None)
        yield mgr, acquisition
        # 生成线程是 daemon，不 stop_all 就会漏到会话结束（本仓库踩过两次）
        mgr.stop_all()

    def test_two_uhf_sources_emit_two_device_ids(self, manager):
        import time

        mgr, acquisition = manager
        ids = [mgr.add_simulator_source("uhf", "corona", 20, 60000.0) for _ in range(2)]
        assert len(set(ids)) == 2, "两个源的 source_id 就撞了"
        assert mgr.start_all() == 2

        seen = set()
        deadline = time.time() + 6.0
        while time.time() < deadline and len(seen) < 2:
            seen = {c[0][0] for c in acquisition.process_data_frame.call_args_list}
            time.sleep(0.05)

        mgr.stop_all()

        msg = "只观察到 %d 个设备号 %s：两路源共用采集设备 id，处理器与合并键都会撞"
        assert len(seen) >= 2, msg % (len(seen), sorted(seen))
        assert "SIM-001" not in seen, "UHF 源仍在用 PDSimulator 的类默认 id SIM-001"
        assert seen == set(ids), "设备号应当就是数据源 id（%s），实际 %s" % (sorted(ids), sorted(seen))

    def test_device_id_follows_source_id_not_class_default(self, manager):
        """单源也要用 source_id，否则命名空间与 stats.sources 仍对不上。"""
        import time

        mgr, acquisition = manager
        sid = mgr.add_simulator_source("uhf", "corona", 20, 60000.0)
        mgr.start_all()

        deadline = time.time() + 6.0
        first = None
        while time.time() < deadline and first is None:
            calls = acquisition.process_data_frame.call_args_list
            if calls:
                first = calls[0][0][0]
            time.sleep(0.05)

        mgr.stop_all()
        assert first == sid, "采集设备 id 应为数据源 id %s，实际 %s" % (sid, first)
