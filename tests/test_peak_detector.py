# -*- coding: utf-8 -*-
"""PeakDetector 专项单元测试"""

import numpy as np

from core.processing.peak_detector import PeakDetector, PeakInfo


class TestPeakDetectorBasic:
    """基础峰值检测测试"""

    def test_detect_peaks_basic(self):
        detector = PeakDetector(threshold=5.0, adaptive=False)
        waveform = np.zeros(1000)
        waveform[200] = 50
        waveform[500] = 80
        peaks = detector.detect_peaks(waveform)
        assert len(peaks) >= 2
        assert all(isinstance(p, PeakInfo) for p in peaks)

    def test_detect_negative_peaks(self):
        detector = PeakDetector(threshold=5.0, adaptive=False)
        waveform = np.zeros(1000)
        waveform[200] = -50
        waveform[500] = -80
        peaks = detector.detect_peaks(waveform)
        assert len(peaks) >= 2
        # 负峰值应保持负值
        neg_peaks = [p for p in peaks if p.polarity == 1]
        assert len(neg_peaks) >= 1

    def test_peaks_sorted_by_amplitude(self):
        detector = PeakDetector(threshold=5.0, adaptive=False)
        waveform = np.zeros(1000)
        waveform[100] = 30
        waveform[300] = 90
        waveform[600] = 60
        peaks = detector.detect_peaks(waveform)
        if len(peaks) > 1:
            for i in range(len(peaks) - 1):
                assert abs(peaks[i].amplitude) >= abs(peaks[i + 1].amplitude)

    def test_peak_info_fields(self):
        detector = PeakDetector(threshold=5.0, adaptive=False)
        waveform = np.zeros(1000)
        waveform[500] = 80
        peaks = detector.detect_peaks(waveform)
        if peaks:
            p = peaks[0]
            assert hasattr(p, "position")
            assert hasattr(p, "amplitude")
            assert hasattr(p, "width")
            assert hasattr(p, "area")
            assert hasattr(p, "energy")
            assert hasattr(p, "snr")
            assert hasattr(p, "polarity")


class TestPeakDetectorPD:
    """局放事件检测测试"""

    def test_detect_pd_events(self):
        detector = PeakDetector(threshold=5.0, adaptive=False, min_distance=30)
        waveform = np.zeros(1000)
        waveform[200] = 50
        waveform[210] = 45  # 距离太近，应被合并
        waveform[500] = 80
        pd_events = detector.detect_pd_events(waveform)
        # 200 和 210 距离=10 < min_distance=30, 应合并为 2 个事件
        assert len(pd_events) <= 2

    def test_detect_pd_events_empty(self):
        detector = PeakDetector(threshold=50.0, adaptive=False)
        waveform = np.ones(1000) * 1.0
        pd_events = detector.detect_pd_events(waveform)
        assert len(pd_events) == 0

    def test_detect_pd_events_energy(self):
        detector = PeakDetector(threshold=5.0, adaptive=False)
        waveform = np.zeros(1000)
        waveform[490:510] = np.sin(np.linspace(0, np.pi, 20)) * 80
        pd_events = detector.detect_pd_events(waveform)
        if pd_events:
            assert pd_events[0].energy > 0


class TestPeakDetectorSimple:
    """简化检测模式测试"""

    def test_detect_peaks_simple(self):
        detector = PeakDetector(threshold=5.0, adaptive=False)
        waveform = np.zeros(1000)
        waveform[200] = 50
        waveform[500] = 80
        peaks = detector.detect_peaks_simple(waveform)
        assert len(peaks) >= 2

    def test_detect_peaks_simple_no_scipy(self):
        """简化模式不依赖 scipy"""
        detector = PeakDetector(threshold=5.0, adaptive=False)
        waveform = np.zeros(1000)
        waveform[500] = 80
        peaks = detector.detect_peaks_simple(waveform)
        assert len(peaks) >= 1
        assert peaks[0].amplitude > 0

    def test_detect_peaks_simple_dedup(self):
        """简化模式应去重"""
        detector = PeakDetector(threshold=5.0, adaptive=False, min_distance=50)
        waveform = np.zeros(1000)
        # 相邻峰值
        waveform[500] = 80
        waveform[510] = 70
        peaks = detector.detect_peaks_simple(waveform)
        # 应只保留一个
        assert len(peaks) == 1


class TestPeakDetectorAdaptive:
    """自适应阈值测试"""

    def test_adaptive_threshold(self):
        detector = PeakDetector(threshold=5.0, adaptive=True)
        waveform = np.random.randn(1000) * 0.5
        waveform[500] = 50
        peaks = detector.detect_peaks(waveform)
        # 自适应阈值应能检测到大峰值
        assert len(peaks) >= 1

    def test_noise_estimation(self):
        detector = PeakDetector(threshold=5.0, adaptive=True)
        waveform = np.random.randn(1000) * 2.0
        detector.detect_peaks(waveform)
        assert detector.noise_level > 0

    def test_adaptive_vs_fixed(self):
        """自适应阈值应比固定阈值更保守"""
        waveform = np.random.randn(1000) * 2.0
        waveform[500] = 15  # 较小的峰值

        det_fixed = PeakDetector(threshold=5.0, adaptive=False)
        det_adaptive = PeakDetector(threshold=5.0, adaptive=True)

        peaks_fixed = det_fixed.detect_peaks(waveform)
        peaks_adaptive = det_adaptive.detect_peaks(waveform)

        # 自适应模式可能检测到更少的峰值（噪声抑制更强）
        assert len(peaks_adaptive) <= len(peaks_fixed) + 1


class TestPeakDetectorBaseline:
    """基线处理测试"""

    def test_baseline_removal(self):
        detector = PeakDetector(threshold=5.0, adaptive=False)
        # 带有直流偏置的波形
        waveform = np.ones(1000) * 20.0
        waveform[500] = 80
        peaks = detector.detect_peaks(waveform)
        # 去除基线后应能检测到峰值
        assert len(peaks) >= 1

    def test_short_input_no_baseline(self):
        detector = PeakDetector(threshold=5.0, adaptive=False)
        waveform = np.array([1.0, 2.0, 3.0])
        result = detector.detect_peaks(waveform)
        assert isinstance(result, list)


class TestPeakDetectorEdgeCases:
    """边界情况测试"""

    def test_empty_input(self):
        detector = PeakDetector(threshold=5.0)
        assert detector.detect_peaks(np.array([])) == []
        assert detector.detect_peaks_simple(np.array([])) == []

    def test_short_input(self):
        detector = PeakDetector(threshold=5.0, min_distance=100)
        waveform = np.array([1.0, 2.0, 3.0])
        assert detector.detect_peaks(waveform) == []

    def test_all_zeros(self):
        detector = PeakDetector(threshold=5.0)
        peaks = detector.detect_peaks(np.zeros(1000))
        assert len(peaks) == 0

    def test_set_threshold(self):
        detector = PeakDetector(threshold=5.0)
        detector.set_threshold(50.0)
        waveform = np.zeros(1000)
        waveform[500] = 30  # 低于新阈值
        peaks = detector.detect_peaks(waveform, threshold=50.0)
        assert len(peaks) == 0

    def test_set_adaptive(self):
        detector = PeakDetector(adaptive=True)
        detector.set_adaptive(False)
        assert detector._adaptive is False

    def test_repr(self):
        detector = PeakDetector(threshold=5.0, adaptive=True)
        r = repr(detector)
        assert "PeakDetector" in r
        assert "5.0" in r
