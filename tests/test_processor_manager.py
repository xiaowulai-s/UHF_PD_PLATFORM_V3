# -*- coding: utf-8 -*-
"""ProcessorManager 专项单元测试"""

import threading

import numpy as np
import pytest

from core.processing.processor_manager import ProcessorManager


class TestProcessorManagerBasic:
    def test_channel_key(self):
        assert ProcessorManager.channel_key("dev1", 0) == "dev1:0"
        assert ProcessorManager.channel_key("dev1", 1) == "dev1:1"

    def test_create_fft(self):
        mgr = ProcessorManager(default_sample_rate=100_000_000, waveform_points=4096)
        fft = mgr.get_or_create_fft("dev1", 0)
        assert fft is not None
        # 同一通道返回同一实例
        assert mgr.get_or_create_fft("dev1", 0) is fft

    def test_create_prpd(self):
        mgr = ProcessorManager()
        prpd = mgr.get_or_create_prpd("dev1", 0)
        assert prpd is not None
        assert mgr.get_or_create_prpd("dev1", 0) is prpd

    def test_create_prps(self):
        mgr = ProcessorManager()
        prps = mgr.get_or_create_prps("dev1", 0)
        assert prps is not None

    def test_create_buffer(self):
        mgr = ProcessorManager()
        buf = mgr.get_or_create_buffer("dev1", 0)
        assert buf is not None
        assert buf.maxlen == 1_000_000

    def test_create_peak_detector(self):
        mgr = ProcessorManager()
        pd = mgr.get_or_create_peak_detector("dev1", 0)
        assert pd is not None

    def test_create_ae_envelope(self):
        mgr = ProcessorManager()
        env = mgr.get_or_create_ae_envelope("dev1", 0)
        assert env is not None

    def test_create_ae_peak_detector(self):
        mgr = ProcessorManager()
        det = mgr.get_or_create_ae_peak_detector("dev1", 0)
        assert det is not None

    def test_create_ae_param_extractor(self):
        mgr = ProcessorManager()
        ext = mgr.get_or_create_ae_param_extractor("dev1", 0)
        assert ext is not None

    def test_different_channels_independent(self):
        mgr = ProcessorManager()
        fft0 = mgr.get_or_create_fft("dev1", 0)
        fft1 = mgr.get_or_create_fft("dev1", 1)
        assert fft0 is not fft1

    def test_different_devices_independent(self):
        mgr = ProcessorManager()
        fft0 = mgr.get_or_create_fft("dev1", 0)
        fft1 = mgr.get_or_create_fft("dev2", 0)
        assert fft0 is not fft1


class TestProcessorManagerConfig:
    def test_set_get_sample_rate(self):
        mgr = ProcessorManager(default_sample_rate=100_000_000)
        assert mgr.get_sample_rate("dev1", 0) == 100_000_000
        mgr.set_sample_rate("dev1", 0, 2_000_000)
        assert mgr.get_sample_rate("dev1", 0) == 2_000_000

    def test_set_get_coupling_type(self):
        mgr = ProcessorManager()
        assert mgr.get_channel_coupling_type("dev1", 0) == "uhf"
        mgr.set_channel_coupling_type("dev1", 0, "ae")
        assert mgr.get_channel_coupling_type("dev1", 0) == "ae"

    def test_invalid_coupling_type_ignored(self):
        mgr = ProcessorManager()
        mgr.set_channel_coupling_type("dev1", 0, "invalid")
        assert mgr.get_channel_coupling_type("dev1", 0) == "uhf"

    def test_channel_stats(self):
        mgr = ProcessorManager()
        stats = mgr.get_channel_stats("dev1", 0)
        assert stats["buffer_size"] == 0
        assert stats["fft_ready"] is False
        mgr.get_or_create_fft("dev1", 0)
        stats = mgr.get_channel_stats("dev1", 0)
        assert stats["fft_ready"] is True


class TestProcessorManagerRelease:
    def test_release_channel(self):
        mgr = ProcessorManager()
        mgr.get_or_create_fft("dev1", 0)
        mgr.get_or_create_prpd("dev1", 0)
        mgr.release_channel("dev1", 0)
        stats = mgr.get_channel_stats("dev1", 0)
        assert stats["fft_ready"] is False

    def test_release_device(self):
        mgr = ProcessorManager()
        mgr.get_or_create_fft("dev1", 0)
        mgr.get_or_create_fft("dev1", 1)
        mgr.release_device("dev1")
        assert mgr.get_channel_stats("dev1", 0)["fft_ready"] is False
        assert mgr.get_channel_stats("dev1", 1)["fft_ready"] is False

    def test_clear_all(self):
        mgr = ProcessorManager()
        mgr.get_or_create_fft("dev1", 0)
        mgr.get_or_create_fft("dev2", 0)
        mgr.clear_all()
        assert mgr.get_channel_stats("dev1", 0)["fft_ready"] is False
        assert mgr.get_channel_stats("dev2", 0)["fft_ready"] is False


class TestProcessorManagerThreadSafety:
    def test_concurrent_create(self):
        mgr = ProcessorManager()
        results = []
        errors = []

        def create_processors(dev_id):
            try:
                for i in range(100):
                    mgr.get_or_create_fft(f"dev{dev_id}", i)
                    mgr.get_or_create_prpd(f"dev{dev_id}", i)
                results.append(dev_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_processors, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 4


class TestPrpdAmplitudeAxis:
    """PRPD 幅值轴量程（缺陷 #21）

    轴只增不减：publish_throttle 只在观测幅值 > 轴*0.8 时扩轴，没有任何收缩路径。
    所以初值取大了就把 mV 级数据永久锁在第 0~1 个箱里——这几条钉的是初值口径。
    """

    def test_axis_default_is_documented_full_scale(self):
        prpd = ProcessorManager().get_or_create_prpd("dev1", 0)
        assert prpd.max_amplitude == pytest.approx(100.0)

    def test_mv_scale_events_span_many_amplitude_rows(self):
        mgr = ProcessorManager()
        prpd = mgr.get_or_create_prpd("dev1", 0)
        rng_p = np.random.default_rng(7)
        rng_a = np.random.default_rng(8)
        prpd.add_events([(float(p), float(a)) for p, a in zip(rng_p.uniform(0, 360, 500), rng_a.uniform(10, 95, 500))])
        rows = np.unique(np.nonzero(prpd.matrix)[1])
        assert len(rows) > 40, f"幅值维度没有分辨率：500 个事件只占了 {len(rows)} 个幅值箱"

    def test_raw_count_data_still_expands_to_the_old_regime(self):
        """真机裸 int16 计数（PD_data.csv 观测 max=8462）换小初值后仍自动扩到约 1 万。"""
        from unittest.mock import MagicMock

        from core.processing.publish_throttle import PublishThrottle

        prpd = ProcessorManager().get_or_create_prpd("dev1", 0)
        throttle = PublishThrottle(MagicMock())
        throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 8462.0)
        assert prpd.max_amplitude == pytest.approx(8462.0 * 1.2)

    def test_axis_is_configurable_for_uncalibrated_deployments(self):
        """未做 FPGA→mV 标定的现场要能显式给大初值，而不是改 DSP 源码。"""
        prpd = ProcessorManager(default_max_amplitude=10000.0).get_or_create_prpd("dev1", 0)
        assert prpd.max_amplitude == pytest.approx(10000.0)


if __name__ == "__main__":
    pytest.main([__file__])
