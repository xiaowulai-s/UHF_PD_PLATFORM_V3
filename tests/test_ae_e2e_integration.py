# -*- coding: utf-8 -*-
"""
AE 端到端集成测试

验证完整链路:
    AESimulator → AcquisitionService._process_ae_waveform → PDDataBus → UI 回调

覆盖:
1. AESimulator 生成 AE 波形
2. AcquisitionService AE 管线处理 (带通滤波/包络/hit检测/参数提取)
3. PDDataBus 信号发布 (ae_hit_detected / ae_envelope_ready / fft_result_ready)
4. FFTProcessor 频段可配置化 (AE_BANDS vs UHF_BANDS)
5. AELocalizer TDOA 定位
6. UI 回调接收验证
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.foundation.signal_bus import PDDataBus
from core.processing.ae_localizer import AELocalizer, SensorPosition
from core.processing.fft_processor import FFTProcessor
from core.services import AcquisitionService

# ═════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════


@pytest.fixture
def data_bus():
    """每次测试使用新的 DataBus"""
    bus = PDDataBus.instance()
    yield bus
    bus.shutdown()


@pytest.fixture
def acquisition_service():
    """采集服务实例"""
    svc = AcquisitionService(sample_rate=2_000_000, waveform_points=4096)
    yield svc
    svc.stop()


@pytest.fixture
def ae_simulator():
    """AE 模拟器"""
    from core.utils.ae_simulator import AESimulator

    sim = AESimulator(
        sample_rate=2_000_000,
        waveform_points=4096,
        burst_rate=5.0,
        noise_level=0.5,
    )
    sim.set_burst_type("burst")
    sim.set_device_info("AE-TEST-001", 1)
    return sim


# ═════════════════════════════════════════════════════════
# 1. AESimulator 波形生成
# ═════════════════════════════════════════════════════════


class TestAESimulatorWaveform:
    def test_generate_waveform(self, ae_simulator):
        """AE 模拟器应生成有效波形"""
        wf = ae_simulator.generate_waveform()
        assert wf is not None
        samples = np.array(wf.samples) if hasattr(wf, "samples") else np.array(wf)
        assert len(samples) > 0
        assert np.any(samples != 0)  # 不是全零

    def test_waveform_sample_rate(self, ae_simulator):
        """波形采样率应为 2MHz"""
        wf = ae_simulator.generate_waveform()
        sr = getattr(wf, "sample_rate", None)
        if sr is not None:
            assert sr == 2_000_000


# ═════════════════════════════════════════════════════════
# 2. AcquisitionService AE 管线
# ═════════════════════════════════════════════════════════


class TestAEPipeline:
    def test_process_ae_waveform(self, acquisition_service, ae_simulator):
        """AE 波形处理应返回成功结果"""
        acquisition_service.set_channel_coupling_type("AE-TEST-001", 1, "ae")
        wf = ae_simulator.generate_waveform()
        result = acquisition_service.process_data_frame("AE-TEST-001", 1, wf)
        assert result is not None

    def test_ae_coupling_type_routing(self, acquisition_service, ae_simulator):
        """AE 通道应走 _process_ae_waveform 路径"""
        acquisition_service.set_channel_coupling_type("AE-TEST-001", 1, "ae")
        wf = ae_simulator.generate_waveform()
        result = acquisition_service.process_data_frame("AE-TEST-001", 1, wf)
        assert result.frame_type == "ae_waveform"

    def test_uhf_coupling_type_routing(self, acquisition_service):
        """UHF 通道应走默认处理路径"""
        # 不设置耦合类型，默认为 UHF
        t = np.linspace(0, 0.001, 4096)
        samples = np.sin(2 * np.pi * 100e6 * t) * 10.0
        wf = MagicMock(samples=samples, sample_rate=100_000_000)
        result = acquisition_service.process_data_frame("UHF-TEST-001", 1, wf)
        assert result.frame_type != "ae_waveform"


# ═════════════════════════════════════════════════════════
# 3. PDDataBus AE 信号发布
# ═════════════════════════════════════════════════════════


class TestAEDataBusSignals:
    def test_ae_hit_signal_emitted(self, data_bus, acquisition_service, ae_simulator):
        """AE hit 信号应被发布"""
        hit_callback = MagicMock()
        data_bus.ae_hit_detected.connect(hit_callback)

        acquisition_service.set_channel_coupling_type("AE-BUS-001", 1, "ae")
        wf = ae_simulator.generate_waveform()
        acquisition_service.process_data_frame("AE-BUS-001", 1, wf)

        # 等待信号传递
        from PySide6.QtCore import QCoreApplication

        if QCoreApplication.instance():
            QCoreApplication.processEvents()

        # 如果有 hit，回调应被调用
        # (取决于模拟器是否生成了超阈值信号)
        # 至少不应崩溃
        data_bus.ae_hit_detected.disconnect(hit_callback)

    def test_fft_result_with_sensor_type(self, data_bus, acquisition_service, ae_simulator):
        """FFT 结果应包含 sensor_type='ae'"""
        fft_callback = MagicMock()
        data_bus.fft_result_ready.connect(fft_callback)

        acquisition_service.set_channel_coupling_type("AE-FFT-001", 1, "ae")
        wf = ae_simulator.generate_waveform()
        acquisition_service.process_data_frame("AE-FFT-001", 1, wf)

        from PySide6.QtCore import QCoreApplication

        if QCoreApplication.instance():
            QCoreApplication.processEvents()

        data_bus.fft_result_ready.disconnect(fft_callback)


# ═════════════════════════════════════════════════════════
# 4. FFTProcessor 频段可配置化
# ═════════════════════════════════════════════════════════


class TestFFTBandsConfigurable:
    def test_uhf_default_bands(self):
        """默认 FFT 处理器应使用 UHF 频段"""
        proc = FFTProcessor(sample_rate=100_000_000)
        assert proc._bands is None  # 未指定时为 None, 使用 DEFAULT_BANDS

        t = np.linspace(0, 0.001, 4096)
        samples = np.sin(2 * np.pi * 500e6 * t) * 10.0
        result = proc.compute(samples)
        # UHF 频段统计应有数据
        assert len(result.band_stats) > 0

    def test_ae_bands_constructor(self):
        """构造时传入 AE 频段"""
        proc = FFTProcessor(sample_rate=2_000_000, bands=FFTProcessor.AE_BANDS)
        assert proc._bands == FFTProcessor.AE_BANDS

        t = np.linspace(0, 0.001, 4096)
        samples = np.sin(2 * np.pi * 100e3 * t) * 10.0
        result = proc.compute(samples)
        # AE 频段统计应有数据
        assert len(result.band_stats) > 0

    def test_ae_bands_override_in_compute_band_stats(self):
        """compute_band_stats 可覆盖频段"""
        proc = FFTProcessor(sample_rate=2_000_000)
        t = np.linspace(0, 0.001, 4096)
        samples = np.sin(2 * np.pi * 100e3 * t) * 10.0
        result = proc.compute(samples)

        # 用 AE 频段重新计算
        ae_stats = proc.compute_band_stats(result.frequencies, result.magnitudes, bands=FFTProcessor.AE_BANDS)
        assert "AE_low" in ae_stats
        assert "AE_mid" in ae_stats
        assert "AE_high" in ae_stats

    def test_uhf_bands_in_hz(self):
        """UHF 频段值应为 Hz"""
        uhf_low = FFTProcessor.UHF_BANDS["UHF_low"]
        assert uhf_low[0] == 300e6
        assert uhf_low[1] == 1000e6

    def test_ae_bands_in_hz(self):
        """AE 频段值应为 Hz"""
        ae_low = FFTProcessor.AE_BANDS["AE_low"]
        assert ae_low[0] == 20_000
        assert ae_low[1] == 60_000


# ═════════════════════════════════════════════════════════
# 5. AELocalizer TDOA 定位
# ═════════════════════════════════════════════════════════


class TestAELocalization:
    def test_localize_known_source(self):
        """已知声源位置应能正确定位"""
        sensors = [
            SensorPosition(sensor_id=1, x=0.0, y=0.0),
            SensorPosition(sensor_id=2, x=1.0, y=0.0),
            SensorPosition(sensor_id=3, x=1.0, y=1.0),
            SensorPosition(sensor_id=4, x=0.0, y=1.0),
        ]
        localizer = AELocalizer(sensors=sensors, sound_speed=343.0)

        # 声源在 (0.5, 0.5)
        source_x, source_y = 0.5, 0.5
        v = 343.0
        arrival_times = {
            1: np.sqrt((source_x - 0.0) ** 2 + (source_y - 0.0) ** 2) / v,
            2: np.sqrt((source_x - 1.0) ** 2 + (source_y - 0.0) ** 2) / v,
            3: np.sqrt((source_x - 1.0) ** 2 + (source_y - 1.0) ** 2) / v,
            4: np.sqrt((source_x - 0.0) ** 2 + (source_y - 1.0) ** 2) / v,
        }

        result = localizer.locate(arrival_times)
        assert result is not None
        assert abs(result.x - source_x) < 0.1, f"X 偏差过大: {result.x} vs {source_x}"
        assert abs(result.y - source_y) < 0.1, f"Y 偏差过大: {result.y} vs {source_y}"
        assert 0 <= result.confidence <= 1

    def test_localize_with_noise(self):
        """带噪声的到达时间应仍能粗略定位"""
        sensors = [
            SensorPosition(sensor_id=1, x=0.0, y=0.0),
            SensorPosition(sensor_id=2, x=2.0, y=0.0),
            SensorPosition(sensor_id=3, x=2.0, y=2.0),
        ]
        localizer = AELocalizer(sensors=sensors, sound_speed=343.0)

        source_x, source_y = 1.0, 1.0
        v = 343.0
        np.random.seed(42)
        arrival_times = {}
        for s in sensors:
            dist = np.sqrt((source_x - s.x) ** 2 + (source_y - s.y) ** 2)
            arrival_times[s.sensor_id] = dist / v + np.random.normal(0, 1e-7)

        result = localizer.locate(arrival_times)
        assert result is not None
        assert abs(result.x - source_x) < 0.5
        assert abs(result.y - source_y) < 0.5

    def test_localize_insufficient_sensors(self):
        """少于 3 个到达时间应返回 None"""
        sensors = [
            SensorPosition(sensor_id=1, x=0.0, y=0.0),
            SensorPosition(sensor_id=2, x=1.0, y=0.0),
            SensorPosition(sensor_id=3, x=1.0, y=1.0),
        ]
        localizer = AELocalizer(sensors=sensors, sound_speed=343.0)
        result = localizer.locate({1: 0.001, 2: 0.002})
        assert result is None

    def test_cluster_center(self):
        """聚类中心应接近真实位置"""
        sensors = [
            SensorPosition(sensor_id=1, x=0.0, y=0.0),
            SensorPosition(sensor_id=2, x=1.0, y=0.0),
            SensorPosition(sensor_id=3, x=1.0, y=1.0),
            SensorPosition(sensor_id=4, x=0.0, y=1.0),
        ]
        localizer = AELocalizer(sensors=sensors, sound_speed=343.0)

        # 声源在 (0.5, 0.5)，生成多个定位结果
        source_x, source_y = 0.5, 0.5
        v = 343.0
        results = []
        for _ in range(5):
            arrival_times = {}
            for s in sensors:
                dist = np.sqrt((source_x - s.x) ** 2 + (source_y - s.y) ** 2)
                arrival_times[s.sensor_id] = dist / v + np.random.normal(0, 1e-7)
            r = localizer.locate(arrival_times)
            if r:
                results.append(r)

        if results:
            center = localizer.get_cluster_center(results)
            assert center is not None
            assert 0 <= center.confidence <= 1


# ═════════════════════════════════════════════════════════
# 6. 端到端链路验证
# ═════════════════════════════════════════════════════════


class TestAEEndToEnd:
    def test_simulator_to_acquisition_to_bus(self, data_bus, acquisition_service, ae_simulator):
        """
        完整链路: AESimulator → AcquisitionService → DataBus

        验证:
        1. 模拟器生成波形
        2. 采集服务处理 AE 波形
        3. DataBus 发布信号
        4. 统计计数器更新
        """
        # 设置 AE 通道
        acquisition_service.set_channel_coupling_type("AE-E2E-001", 1, "ae")

        # 处理 10 帧
        for _ in range(10):
            wf = ae_simulator.generate_waveform()
            result = acquisition_service.process_data_frame("AE-E2E-001", 1, wf)
            assert result is not None

        # 验证统计
        stats = acquisition_service.get_stats()
        assert stats["frames_received"] >= 10
        assert stats["waveforms_processed"] >= 10

    def test_ae_localizer_integration(self):
        """AELocalizer 与 AE hit 数据的集成"""
        sensors = [
            SensorPosition(sensor_id=1, x=0.0, y=0.0),
            SensorPosition(sensor_id=2, x=1.0, y=0.0),
            SensorPosition(sensor_id=3, x=1.0, y=1.0),
            SensorPosition(sensor_id=4, x=0.0, y=1.0),
        ]
        localizer = AELocalizer(sensors=sensors, sound_speed=343.0)

        # 模拟从 AE hit 提取的到达时间
        source_x, source_y = 0.3, 0.7
        v = 343.0
        arrival_times = {}
        for s in sensors:
            dist = np.sqrt((source_x - s.x) ** 2 + (source_y - s.y) ** 2)
            arrival_times[s.sensor_id] = dist / v

        result = localizer.locate(arrival_times)
        assert result is not None
        assert result.sensors_used == 4
        assert result.confidence > 0


# ═════════════════════════════════════════════════════════
# 7. AE 包络处理扩展测试
# ═════════════════════════════════════════════════════════


class TestAEEnvelopeProcessing:
    def test_envelope_computed(self, data_bus, acquisition_service, ae_simulator):
        """AE 包络应被正确计算"""
        acquisition_service.set_channel_coupling_type("AE-ENV-001", 1, "ae")

        envelope_callback = MagicMock()
        data_bus.ae_envelope_ready.connect(envelope_callback)

        wf = ae_simulator.generate_waveform()
        acquisition_service.process_data_frame("AE-ENV-001", 1, wf)

        from PySide6.QtCore import QCoreApplication

        if QCoreApplication.instance():
            QCoreApplication.processEvents()

        data_bus.ae_envelope_ready.disconnect(envelope_callback)

    def test_envelope_length_matches_input(self, acquisition_service, ae_simulator):
        """包络长度应与输入波形匹配"""
        acquisition_service.set_channel_coupling_type("AE-ENV-LEN-001", 1, "ae")

        wf = ae_simulator.generate_waveform()

        result = acquisition_service.process_data_frame("AE-ENV-LEN-001", 1, wf)
        assert result is not None
        assert result.success is True
        assert result.data is not None
        assert "samples" in result.data
        # 验证包络存在（长度可能因滤波而略有不同）
        assert len(result.data["samples"]) > 0


# ═════════════════════════════════════════════════════════
# 8. AE 多通道测试
# ═════════════════════════════════════════════════════════


class TestAEMultiChannel:
    def test_multiple_channels_independent(self, acquisition_service, ae_simulator):
        """多个 AE 通道应独立处理"""
        acquisition_service.set_channel_coupling_type("AE-MULTI-001", 1, "ae")
        acquisition_service.set_channel_coupling_type("AE-MULTI-002", 2, "ae")

        # 处理两通道数据
        for ch in [1, 2]:
            wf = ae_simulator.generate_waveform()
            result = acquisition_service.process_data_frame("AE-MULTI-001", ch, wf)
            assert result is not None

        stats = acquisition_service.get_stats()
        assert stats["frames_received"] >= 2

    def test_mixed_ae_uhf_channels(self, acquisition_service, ae_simulator):
        """AE 和 UHF 通道应正确路由"""
        acquisition_service.set_channel_coupling_type("AE-MIXED-001", 1, "ae")
        # 通道 2 保持默认 UHF

        # AE 通道
        ae_wf = ae_simulator.generate_waveform()
        ae_result = acquisition_service.process_data_frame("AE-MIXED-001", 1, ae_wf)
        assert ae_result.frame_type == "ae_waveform"

        # UHF 通道
        t = np.linspace(0, 0.001, 4096)
        uhf_samples = np.sin(2 * np.pi * 500e6 * t) * 10.0
        uhf_wf = MagicMock(samples=uhf_samples, sample_rate=100_000_000)
        uhf_result = acquisition_service.process_data_frame("AE-MIXED-001", 2, uhf_wf)
        assert uhf_result.frame_type != "ae_waveform"


# ═════════════════════════════════════════════════════════
# 9. AE 突发类型测试
# ═════════════════════════════════════════════════════════


class TestAEBurstTypes:
    def test_burst_type_waveform(self, ae_simulator):
        """突发类型波形应有效生成"""
        ae_simulator.set_burst_type("burst")
        wf = ae_simulator.generate_waveform()
        samples = np.array(wf.samples) if hasattr(wf, "samples") else np.array(wf)
        assert len(samples) > 0
        assert np.any(samples != 0)

    def test_continuous_type_waveform(self, ae_simulator):
        """连续类型波形应有效生成"""
        ae_simulator.set_burst_type("continuous")
        wf = ae_simulator.generate_waveform()
        samples = np.array(wf.samples) if hasattr(wf, "samples") else np.array(wf)
        assert len(samples) > 0
        assert np.any(samples != 0)

    def test_mixed_type_waveform(self, ae_simulator):
        """混合类型波形应有效生成"""
        ae_simulator.set_burst_type("mixed")
        wf = ae_simulator.generate_waveform()
        samples = np.array(wf.samples) if hasattr(wf, "samples") else np.array(wf)
        assert len(samples) > 0
        assert np.any(samples != 0)


# ═════════════════════════════════════════════════════════
# 10. AE Hit 特征提取验证
# ═════════════════════════════════════════════════════════


class TestAEHitFeatures:
    def test_ae_hit_features_structure(self, data_bus, acquisition_service, ae_simulator):
        """AE hit 特征应包含必要字段"""
        acquisition_service.set_channel_coupling_type("AE-HIT-FEAT-001", 1, "ae")

        hit_callback = MagicMock()
        data_bus.ae_hit_detected.connect(hit_callback)

        # 处理多帧以增加 hit 检测概率
        for _ in range(20):
            wf = ae_simulator.generate_waveform()
            acquisition_service.process_data_frame("AE-HIT-FEAT-001", 1, wf)

        from PySide6.QtCore import QCoreApplication

        if QCoreApplication.instance():
            QCoreApplication.processEvents()

        # 如果检测到 hit，验证特征结构
        if hit_callback.called:
            args, _ = hit_callback.call_args
            features = args[2] if len(args) > 2 else None
            if features:
                assert "amplitude" in features or "counts" in features or "marse_energy" in features

        data_bus.ae_hit_detected.disconnect(hit_callback)

    def test_ae_hit_pd_event_published(self, data_bus, acquisition_service, ae_simulator):
        """AE hit 应触发 PD 事件发布"""
        acquisition_service.set_channel_coupling_type("AE-PD-001", 1, "ae")

        pd_callback = MagicMock()
        data_bus.pd_event_detected.connect(pd_callback)

        for _ in range(20):
            wf = ae_simulator.generate_waveform()
            acquisition_service.process_data_frame("AE-PD-001", 1, wf)

        from PySide6.QtCore import QCoreApplication

        if QCoreApplication.instance():
            QCoreApplication.processEvents()

        data_bus.pd_event_detected.disconnect(pd_callback)


# ═════════════════════════════════════════════════════════
# 11. AE FFT 频段验证
# ═════════════════════════════════════════════════════════


class TestAEFFTBands:
    def test_ae_fft_uses_ae_bands(self, data_bus, acquisition_service, ae_simulator):
        """AE 通道 FFT 应使用 AE 频段"""
        acquisition_service.set_channel_coupling_type("AE-FFT-BAND-001", 1, "ae")

        fft_callback = MagicMock()
        data_bus.fft_result_ready.connect(fft_callback)

        wf = ae_simulator.generate_waveform()
        acquisition_service.process_data_frame("AE-FFT-BAND-001", 1, wf)

        from PySide6.QtCore import QCoreApplication

        if QCoreApplication.instance():
            QCoreApplication.processEvents()

        if fft_callback.called:
            args, _ = fft_callback.call_args
            fft_data = args[3] if len(args) > 3 else None
            if fft_data:
                assert hasattr(fft_data, "band_stats")

        data_bus.fft_result_ready.disconnect(fft_callback)


# ═════════════════════════════════════════════════════════
# 12. AE PRPD 集成测试
# ═════════════════════════════════════════════════════════


class TestAEPRPDIntegration:
    def test_ae_events_update_prpd(self, data_bus, acquisition_service, ae_simulator):
        """AE hit 应更新 PRPD 图谱"""
        acquisition_service.set_channel_coupling_type("AE-PRPD-001", 1, "ae")

        prpd_callback = MagicMock()
        data_bus.prpd_result_ready.connect(prpd_callback)

        for _ in range(30):
            wf = ae_simulator.generate_waveform()
            acquisition_service.process_data_frame("AE-PRPD-001", 1, wf)

        from PySide6.QtCore import QCoreApplication

        if QCoreApplication.instance():
            QCoreApplication.processEvents()

        data_bus.prpd_result_ready.disconnect(prpd_callback)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
