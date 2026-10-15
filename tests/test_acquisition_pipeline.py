# -*- coding: utf-8 -*-
"""AcquisitionService 拆分后管线 + 协调器单元测试"""

import math
from unittest.mock import MagicMock

import numpy as np
import pytest

from core.processing.ae_pipeline import AEAlarmChecker, AEPipelineHandler
from core.processing.processor_manager import ProcessorManager
from core.processing.publish_throttle import PublishThrottle
from core.processing.uhf_pipeline import UHFPipelineHandler, detect_zero_crossings, position_to_phase
from core.services.acquisition_service import AcquisitionResult, AcquisitionService

# ═════════════════════════════════════════════════════
# 零交叉 / 相位工具测试
# ═════════════════════════════════════════════════════


class TestPhaseUtils:
    def test_detect_zero_crossings(self):
        # 50Hz, 4 个完整周期, 不含端点
        t = np.linspace(0, 0.08, 4000, endpoint=False)
        samples = np.sin(2 * math.pi * 50 * t)
        crossings = detect_zero_crossings(samples)
        assert len(crossings) >= 3  # 每周期 1 个负→正过零点

    def test_detect_zero_crossings_empty(self):
        assert len(detect_zero_crossings(np.array([]))) == 0

    def test_position_to_phase_linear_fallback(self):
        # 无过零点，回退到线性映射
        phase = position_to_phase(250, 1000, np.array([]))
        assert 89 < phase < 91

    def test_position_to_phase_with_crossings(self):
        crossings = np.array([100, 300, 500, 700])
        phase = position_to_phase(200, 1000, crossings)
        assert 0 <= phase < 360


# ═════════════════════════════════════════════════════
# UHFPipelineHandler 测试
# ═════════════════════════════════════════════════════


class TestUHFPipeline:
    def test_process_short_waveform(self):
        proc_mgr = ProcessorManager()
        data_bus = MagicMock()
        throttle = PublishThrottle(data_bus)
        handler = UHFPipelineHandler(proc_mgr, data_bus, throttle)

        wf = MagicMock()
        wf.samples = [1.0]
        result = handler.process("dev1", 0, wf)
        assert result["n_peaks"] == 0

    def test_process_basic_waveform(self):
        proc_mgr = ProcessorManager()
        data_bus = MagicMock()
        throttle = PublishThrottle(data_bus)
        handler = UHFPipelineHandler(proc_mgr, data_bus, throttle)

        # 100kHz 采样，1ms 数据
        sr = 100_000
        t = np.linspace(0, 0.001, sr // 1000)
        samples = np.sin(2 * math.pi * 10_000 * t) * 50.0

        wf = MagicMock()
        wf.samples = samples
        wf.sample_rate = sr
        result = handler.process("dev1", 0, wf)

        assert result["sample_rate"] == sr
        assert len(result["samples"]) == len(samples)
        # 应该发布了波形
        data_bus.publish_waveform.assert_called_once()

    def test_published_waveform_carries_sample_rate(self):
        """波形负载必须自带时间基，不能发裸 ndarray。

        裸数组会让 `payloads.build_waveform` 的 `getattr(obj, "sample_rate", 0)` 恒取到 0，
        前端再把 0 兜底成 100 MHz —— 对 UHF 恰好蒙对，对别的采样率就是一条静默的错刻度
        （图照样画得出来，只是横轴差几十倍）。

        采样率刻意取 2 MHz 而不是 100 MHz：取 100 MHz 的话，"恰好兜底对了"的实现也能过。
        """
        proc_mgr = ProcessorManager()
        data_bus = MagicMock()
        handler = UHFPipelineHandler(proc_mgr, data_bus, PublishThrottle(data_bus))

        sr = 2_000_000
        wf = MagicMock()
        wf.samples = np.sin(np.linspace(0, 0.01, 512)) * 10.0
        wf.sample_rate = sr
        handler.process("dev1", 0, wf)

        # publish_waveform(device_id, channel_id, waveform_data, sensor_type)
        payload = data_bus.publish_waveform.call_args.args[2]
        assert getattr(payload, "sample_rate", 0) == sr, "波形负载没带时间基，前端只能靠猜"


# ═════════════════════════════════════════════════════
# AEPipelineHandler 测试
# ═════════════════════════════════════════════════════


class TestAEPipeline:
    def test_process_short_waveform(self):
        proc_mgr = ProcessorManager()
        data_bus = MagicMock()
        throttle = PublishThrottle(data_bus)
        alarm_checker = AEAlarmChecker(data_bus)
        handler = AEPipelineHandler(proc_mgr, data_bus, throttle, alarm_checker)

        wf = MagicMock()
        wf.samples = [1.0]
        result = handler.process("dev1", 0, wf)
        assert result["n_hits"] == 0

    def test_process_basic_ae_waveform(self):
        proc_mgr = ProcessorManager()
        data_bus = MagicMock()
        throttle = PublishThrottle(data_bus)
        alarm_checker = AEAlarmChecker(data_bus)
        handler = AEPipelineHandler(proc_mgr, data_bus, throttle, alarm_checker)

        sr = 2_000_000
        t = np.linspace(0, 0.001, sr // 1000)
        samples = np.sin(2 * math.pi * 100_000 * t) * np.exp(-t * 10_000) * 50.0

        wf = MagicMock()
        wf.samples = samples
        wf.sample_rate = sr
        result = handler.process("dev1", 0, wf)

        assert result["sample_rate"] == sr
        assert "duration_ms" in result
        # 应该发布了包络和波形
        data_bus.publish_ae_envelope.assert_called_once()
        data_bus.publish_waveform.assert_called_once()


# ═════════════════════════════════════════════════════
# AcquisitionService 协调器测试
# ═════════════════════════════════════════════════════


class TestAcquisitionServiceCoordinator:
    def test_init(self):
        svc = AcquisitionService(sample_rate=100_000_000, waveform_points=4096)
        assert svc._proc_mgr is not None
        assert svc._uhf_handler is not None
        assert svc._ae_handler is not None
        assert svc._alarm_checker is not None
        assert svc._throttle is not None

    def test_start_stop(self):
        svc = AcquisitionService()
        assert svc.start() is True
        assert svc.is_running is True
        svc.stop()
        assert svc.is_running is False

    def test_get_stats(self):
        svc = AcquisitionService()
        stats = svc.get_stats()
        assert stats["frames_received"] == 0
        assert stats["errors"] == 0

    def test_reset_stats(self):
        svc = AcquisitionService()
        svc._inc_stat("frames_received", 10)
        svc.reset_stats()
        stats = svc.get_stats()
        assert stats["frames_received"] == 0

    def test_channel_key_compat(self):
        svc = AcquisitionService()
        assert svc._channel_key("dev1", 0) == "dev1:0"

    def test_observed_max_amp_compat(self):
        svc = AcquisitionService()
        assert isinstance(svc._observed_max_amp, dict)

    def test_delegate_get_or_create(self):
        svc = AcquisitionService()
        fft = svc.get_or_create_fft("dev1", 0)
        assert fft is not None
        assert svc.get_or_create_fft("dev1", 0) is fft

    def test_set_channel_coupling_type(self):
        svc = AcquisitionService()
        svc.set_channel_coupling_type("dev1", 0, "ae")
        assert svc._proc_mgr.get_channel_coupling_type("dev1", 0) == "ae"

    def test_set_ae_alarm_thresholds(self):
        svc = AcquisitionService()
        svc.set_ae_alarm_thresholds(signal_loss_frames=10, noise_threshold_mv=20.0)
        assert svc._alarm_checker.signal_loss_threshold == 10
        assert svc._alarm_checker.noise_threshold == 20.0

    def test_release_channel(self):
        svc = AcquisitionService()
        svc.get_or_create_fft("dev1", 0)
        svc.release_channel("dev1", 0)
        assert svc.get_channel_stats("dev1", 0)["fft_ready"] is False

    def test_release_device(self):
        svc = AcquisitionService()
        svc.get_or_create_fft("dev1", 0)
        svc.release_device("dev1")
        assert svc.get_channel_stats("dev1", 0)["fft_ready"] is False

    def test_set_protocol(self):
        svc = AcquisitionService()
        svc.set_protocol(MagicMock())
        assert svc._protocol is not None

    def test_set_callbacks(self):
        svc = AcquisitionService()
        svc.set_on_waveform_callback(lambda: None)
        svc.set_on_pd_event_callback(lambda: None)
        assert svc._on_waveform_cb is not None
        assert svc._on_pd_event_cb is not None

    def test_process_data_frame_uhf(self):
        svc = AcquisitionService()
        svc.start()

        # 创建类名包含 "WaveformData" 的 mock
        class WaveformData:
            def __init__(self):
                self.samples = np.sin(np.linspace(0, 2 * math.pi, 1000, endpoint=False)) * 50.0
                self.sample_rate = 100_000_000

        result = svc.process_data_frame("dev1", 0, WaveformData())

        assert result.success is True
        assert result.frame_type == "waveform"
        assert result.duration_ms > 0
        svc.stop()

    def test_process_data_frame_ae(self):
        svc = AcquisitionService()
        svc.start()
        svc.set_channel_coupling_type("dev1", 0, "ae")

        class WaveformData:
            def __init__(self):
                self.samples = np.sin(np.linspace(0, 2 * math.pi, 4000, endpoint=False)) * 50.0
                self.sample_rate = 2_000_000

        result = svc.process_data_frame("dev1", 0, WaveformData())

        assert result.success is True
        assert result.frame_type == "ae_waveform"
        svc.stop()

    def test_process_data_frame_pd_event(self):
        svc = AcquisitionService()
        svc.start()

        event = {"phase": 90, "amplitude": 50.0}
        result = svc.process_data_frame("dev1", 0, event)

        assert result.success is True
        assert result.frame_type == "pd_event"
        svc.stop()

    def test_get_channel_stats(self):
        svc = AcquisitionService()
        stats = svc.get_channel_stats("dev1", 0)
        assert "buffer_size" in stats
        assert "sample_rate" in stats


class TestAcquisitionResult:
    def test_repr_success(self):
        r = AcquisitionResult("dev1", 0)
        r.success = True
        r.frame_type = "waveform"
        assert "OK" in repr(r)

    def test_repr_failure(self):
        r = AcquisitionResult("dev1", 0)
        r.success = False
        r.error_message = "timeout"
        assert "FAIL(timeout)" in repr(r)


if __name__ == "__main__":
    pytest.main([__file__])
