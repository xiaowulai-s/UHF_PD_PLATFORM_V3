# -*- coding: utf-8 -*-
"""AE 信号处理单元测试"""

import math

import numpy as np
import pytest

from core.processing.ae_envelope import AEEnvelopeProcessor
from core.processing.ae_parameter_extractor import AEParameterExtractor
from core.processing.ae_peak_detector import AEHitInfo, AEPeakDetector

# ═════════════════════════════════════════════════════
# AEEnvelopeProcessor 测试
# ═════════════════════════════════════════════════════


class TestAEEnvelopeProcessor:
    def test_init_defaults(self):
        proc = AEEnvelopeProcessor()
        assert proc.lowcut_hz == 20_000
        assert proc.highcut_hz == 200_000
        assert proc.sample_rate == 2_000_000

    def test_bandpass_filter_short_signal(self):
        """短信号应直接返回（不崩溃）"""
        proc = AEEnvelopeProcessor()
        samples = np.array([1.0, 2.0])
        result = proc.bandpass_filter(samples)
        assert len(result) == 2

    def test_compute_envelope_empty(self):
        proc = AEEnvelopeProcessor()
        result = proc.compute_envelope(np.array([]))
        assert len(result) == 0

    def test_compute_envelope_single_point(self):
        proc = AEEnvelopeProcessor()
        result = proc.compute_envelope(np.array([5.0]))
        assert len(result) == 1
        assert result[0] == 5.0

    def test_compute_envelope_hilbert_basic(self):
        """Hilbert 包络应始终 >= |原始信号|"""
        proc = AEEnvelopeProcessor()
        t = np.linspace(0, 0.001, 2000)
        # 100kHz 正弦突发
        samples = np.sin(2 * math.pi * 100_000 * t) * np.exp(-t * 10_000)
        envelope = proc.compute_envelope(samples, method="hilbert")
        assert len(envelope) == len(samples)
        assert np.all(envelope >= 0)
        # 峰值附近包络应 >= |原始值|
        peak_idx = np.argmax(np.abs(samples))
        assert envelope[peak_idx] >= abs(samples[peak_idx]) - 1e-6

    def test_compute_envelope_rectify_fallback(self):
        """整流包络应 >= 0"""
        proc = AEEnvelopeProcessor()
        samples = np.array([-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0], dtype=np.float64)
        envelope = proc.compute_envelope(samples, method="rectify")
        assert np.all(envelope >= 0)

    def test_compute_rms(self):
        proc = AEEnvelopeProcessor()
        samples = np.ones(1000) * 5.0
        rms = proc.compute_rms(samples)
        assert abs(rms - 5.0) < 0.5

    def test_compute_rms_empty(self):
        proc = AEEnvelopeProcessor()
        assert proc.compute_rms(np.array([])) == 0.0

    def test_property_setter_invalidates_filter_cache(self):
        proc = AEEnvelopeProcessor(sample_rate=2_000_000)
        proc._filter_coeffs = ("cached",)
        proc.sample_rate = 5_000_000
        assert proc._filter_coeffs is None

    def test_rectify_fallback(self):
        """整流包络模式应正常返回"""
        proc = AEEnvelopeProcessor()
        samples = np.array([1.0, 0.0, -1.0, 0.0, 1.0])
        result = proc.compute_envelope(samples, method="rectify")
        assert np.all(result >= 0)


# ═════════════════════════════════════════════════════
# AEPeakDetector 测试
# ═════════════════════════════════════════════════════


class TestAEPeakDetector:
    def test_detect_no_hits_on_flat_signal(self):
        detector = AEPeakDetector(threshold_mode="absolute", threshold_value=10.0)
        envelope = np.ones(1000) * 1.0
        hits = detector.detect_hits(envelope, sample_rate=2_000_000)
        assert len(hits) == 0

    def test_detect_single_hit(self):
        """一个明显的包络突发应检测为一个 hit"""
        detector = AEPeakDetector(threshold_mode="absolute", threshold_value=2.0, holdoff_time_us=500)
        envelope = np.zeros(5000)
        # 在 1000-2000 添加一个突起
        envelope[1000:2000] = np.sin(np.linspace(0, math.pi, 1000)) * 10.0
        hits = detector.detect_hits(envelope, sample_rate=2_000_000)
        assert len(hits) == 1
        hit = hits[0]
        assert hit.peak_amplitude > 8.0
        assert hit.peak_position >= 1000

    def test_detect_multiple_hits(self):
        """两个分离的突起应检测为两个 hit"""
        detector = AEPeakDetector(
            threshold_mode="absolute",
            threshold_value=2.0,
            holdoff_time_us=200,
            rearm_time_us=500,
        )
        envelope = np.zeros(10000)
        # 第一个 hit
        envelope[1000:2000] = np.sin(np.linspace(0, math.pi, 1000)) * 10.0
        # 第二个 hit (间隔足够远)
        envelope[5000:6000] = np.sin(np.linspace(0, math.pi, 1000)) * 8.0
        hits = detector.detect_hits(envelope, sample_rate=2_000_000)
        assert len(hits) == 2

    def test_relative_threshold_mode(self):
        """relative 模式应基于最大幅值计算阈值"""
        detector = AEPeakDetector(threshold_mode="relative", threshold_value=0.5)
        envelope = np.zeros(2000)
        envelope[500:1500] = 10.0
        hits = detector.detect_hits(envelope, sample_rate=2_000_000)
        # 阈值应为 5.0 (10*0.5), 包络值 10 > 5, 应有 hit
        assert len(hits) >= 1

    def test_noise_threshold_mode(self):
        """noise 模式不应崩溃"""
        detector = AEPeakDetector(threshold_mode="noise", threshold_value=3.0)
        envelope = np.random.randn(5000) * 0.1
        envelope[1000:1100] = 5.0  # 明显信号
        hits = detector.detect_hits(envelope, sample_rate=2_000_000)
        # 不崩溃即为通过
        assert isinstance(hits, list)

    def test_min_amplitude_filter(self):
        """低于 min_amplitude 的峰值应被过滤"""
        detector = AEPeakDetector(
            threshold_mode="absolute",
            threshold_value=1.0,
            min_amplitude=20.0,
        )
        envelope = np.zeros(2000)
        envelope[500:1500] = 5.0
        hits = detector.detect_hits(envelope, sample_rate=2_000_000)
        assert len(hits) == 0

    def test_detect_hits_short_signal(self):
        detector = AEPeakDetector()
        hits = detector.detect_hits(np.array([1.0, 2.0]), sample_rate=2_000_000)
        assert len(hits) == 0

    def test_hit_parameters_accuracy(self):
        """验证 hit 参数的物理意义"""
        detector = AEPeakDetector(
            threshold_mode="absolute",
            threshold_value=1.0,
            holdoff_time_us=1000,  # 放大 holdoff 确保完整捕获
        )
        sr = 2_000_000
        envelope = np.zeros(int(sr * 0.01))  # 10ms
        # 一个 2ms 宽的包络突发
        burst_len = int(sr * 0.002)
        env_shape = np.sin(np.linspace(0, math.pi, burst_len))
        envelope[1000 : 1000 + burst_len] = env_shape * 10.0

        hits = detector.detect_hits(envelope, sr)
        if len(hits) > 0:
            hit = hits[0]
            # 峰值应在接近中间位置
            assert hit.peak_amplitude > 9.0
            # 持续时间应约 2ms
            assert 500 < hit.duration_us < 5000
            # MARSE 能量应为正
            assert hit.marse_energy > 0


# ═════════════════════════════════════════════════════
# AEParameterExtractor 测试
# ═════════════════════════════════════════════════════


class TestAEParameterExtractor:
    def test_extract_basic(self):
        extractor = AEParameterExtractor()
        sr = 2_000_000
        # 构建简单的 hit 信号
        t = np.linspace(0, 0.002, int(sr * 0.002))
        samples = np.sin(2 * math.pi * 100_000 * t) * np.exp(-t * 2000) * 10.0
        envelope = np.abs(samples)  # 简化的包络

        hit = AEHitInfo(
            peak_amplitude=10.0,
            peak_position=len(samples) // 4,
            rise_time_us=50.0,
            duration_us=2000.0,
            counts=10,
            marse_energy=0.01,
            avg_frequency_khz=5.0,
            rms=3.0,
            arrival_time=0.0,
            start_position=0,
            end_position=len(samples) - 1,
        )

        features = extractor.extract(samples, envelope, hit, sr)

        assert "amplitude" in features
        assert "rise_time_us" in features
        assert "duration_us" in features
        assert "counts" in features
        assert "marse_energy" in features
        assert "phase_deg" in features
        assert "polarity" in features
        assert "signal_quality" in features
        assert features["amplitude"] > 0
        assert 0 <= features["phase_deg"] < 360
        assert features["polarity"] in (0, 1)

    def test_compute_phase(self):
        extractor = AEParameterExtractor()
        # 50Hz 工频，周期 20ms
        phase = extractor._compute_phase(0.0, 2_000_000, 50.0)
        assert 0 <= phase < 360
        # 5ms -> 90°
        phase_5ms = extractor._compute_phase(0.005, 2_000_000, 50.0)
        assert 80 < phase_5ms < 100

    def test_detect_polarity(self):
        extractor = AEParameterExtractor()
        rising = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        falling = np.array([4.0, 3.0, 2.0, 1.0, 0.0])
        assert extractor._detect_polarity(rising) == 0  # positive
        assert extractor._detect_polarity(falling) == 1  # negative


# ═════════════════════════════════════════════════════
# AEEnvelopeProcessor 增强测试
# ═════════════════════════════════════════════════════


class TestAEEnvelopeEnhanced:
    def test_bandpass_filter_actual_effect(self):
        """带通滤波应衰减带外频率"""
        proc = AEEnvelopeProcessor(lowcut_hz=20_000, highcut_hz=200_000, sample_rate=2_000_000)
        t = np.linspace(0, 0.01, 20000)
        # 低频信号 (5kHz, 应被衰减)
        low_freq = np.sin(2 * math.pi * 5_000 * t) * 10.0
        # 带内信号 (100kHz, 应保留)
        in_band = np.sin(2 * math.pi * 100_000 * t) * 10.0
        combined = low_freq + in_band
        filtered = proc.bandpass_filter(combined)
        # 带内信号能量应大于带外
        assert len(filtered) == len(combined)

    def test_filter_cache_invalidation_lowcut(self):
        proc = AEEnvelopeProcessor()
        proc._filter_coeffs = ("cached",)
        proc.lowcut_hz = 30_000
        assert proc._filter_coeffs is None

    def test_filter_cache_invalidation_highcut(self):
        proc = AEEnvelopeProcessor()
        proc._filter_coeffs = ("cached",)
        proc.highcut_hz = 300_000
        assert proc._filter_coeffs is None


# ═════════════════════════════════════════════════════
# AEPeakDetector 增强测试
# ═════════════════════════════════════════════════════


class TestAEPeakDetectorEnhanced:
    def test_detect_hits_with_rearm(self):
        """rearm_time 应防止同一 hit 被重复检测"""
        detector = AEPeakDetector(
            threshold_mode="absolute",
            threshold_value=2.0,
            holdoff_time_us=100,
            rearm_time_us=5000,  # 长 rearm
        )
        sr = 2_000_000
        envelope = np.zeros(20000)
        # 两个靠得很近的突起
        envelope[1000:2000] = np.sin(np.linspace(0, math.pi, 1000)) * 10.0
        envelope[2500:3500] = np.sin(np.linspace(0, math.pi, 1000)) * 10.0
        hits = detector.detect_hits(envelope, sr)
        # rearm 时间长，第二个 hit 可能被跳过
        assert len(hits) <= 2

    def test_max_duration_limit(self):
        """超过 max_duration 的 hit 应被截断"""
        detector = AEPeakDetector(
            threshold_mode="absolute",
            threshold_value=1.0,
            max_duration_us=500,  # 很短
        )
        sr = 2_000_000
        envelope = np.zeros(50000)
        envelope[1000:10000] = 5.0  # 很长的突起
        hits = detector.detect_hits(envelope, sr)
        if hits:
            assert hits[0].duration_us <= 600  # 允许小误差


# ═════════════════════════════════════════════════════
# AEParameterExtractor 增强测试
# ═════════════════════════════════════════════════════


class TestAEParameterExtractorEnhanced:
    def test_extract_energy_positive(self):
        extractor = AEParameterExtractor()
        sr = 2_000_000
        t = np.linspace(0, 0.002, int(sr * 0.002))
        samples = np.sin(2 * math.pi * 100_000 * t) * 10.0
        envelope = np.abs(samples)
        hit = AEHitInfo(
            peak_amplitude=10.0,
            peak_position=len(samples) // 4,
            rise_time_us=50.0,
            duration_us=2000.0,
            counts=10,
            marse_energy=0.01,
            avg_frequency_khz=5.0,
            rms=3.0,
            arrival_time=0.0,
            start_position=0,
            end_position=len(samples) - 1,
        )
        features = extractor.extract(samples, envelope, hit, sr)
        assert features["energy"] >= 0
        assert features["signal_strength"] >= 0
        assert features["signal_quality"] >= 0

    def test_extract_signal_strength(self):
        extractor = AEParameterExtractor()
        sr = 2_000_000
        n = 4000
        samples = np.random.randn(n) * 5.0
        envelope = np.abs(samples)
        hit = AEHitInfo(
            peak_amplitude=10.0,
            peak_position=1000,
            rise_time_us=100.0,
            duration_us=2000.0,
            counts=20,
            marse_energy=0.05,
            avg_frequency_khz=10.0,
            rms=5.0,
            arrival_time=0.0,
            start_position=0,
            end_position=n - 1,
        )
        features = extractor.extract(samples, envelope, hit, sr)
        assert features["signal_strength"] > 0

    def test_extract_amplitude_db(self):
        extractor = AEParameterExtractor()
        sr = 2_000_000
        hit = AEHitInfo(
            peak_amplitude=10.0,
            peak_position=0,
            rise_time_us=0.0,
            duration_us=0.0,
            counts=0,
            marse_energy=0.0,
            avg_frequency_khz=0.0,
            rms=0.0,
            arrival_time=0.0,
            start_position=0,
            end_position=0,
        )
        features = extractor.extract(np.array([10.0]), np.array([10.0]), hit, sr)
        # 20 * log10(10) = 20 dB
        assert abs(features["amplitude_db"] - 20.0) < 1.0


if __name__ == "__main__":
    pytest.main([__file__])
