# -*- coding: utf-8 -*-
"""FFTProcessor 专项单元测试"""

import numpy as np
import pytest

from core.processing.fft_processor import BandStats, FFTPeakInfo, FFTProcessor, FFTResult


class TestFFTCompute:
    """FFT 计算测试"""

    def test_compute_basic(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        t = np.linspace(0, 40.96e-6, 4096)
        waveform = np.sin(2 * np.pi * 50e6 * t) * 10.0
        result = proc.compute(waveform)
        assert len(result.frequencies) > 0
        assert len(result.magnitudes) > 0
        assert isinstance(result, FFTResult)

    def test_compute_empty_input(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        result = proc.compute(np.array([]))
        assert len(result.frequencies) == 0
        assert len(result.magnitudes) == 0

    def test_compute_nan_input(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        waveform = np.full(4096, np.nan)
        result = proc.compute(waveform)
        assert len(result.frequencies) == 0

    def test_compute_inf_input(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        waveform = np.full(4096, np.inf)
        result = proc.compute(waveform)
        assert len(result.frequencies) == 0

    def test_compute_short_input(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        result = proc.compute(np.array([1.0]))
        assert len(result.frequencies) == 0

    def test_compute_no_peak_detection(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        t = np.linspace(0, 40.96e-6, 4096)
        waveform = np.sin(2 * np.pi * 50e6 * t) * 10.0
        result = proc.compute(waveform, detect_peaks=False)
        assert len(result.peaks) == 0


class TestFFTProperties:
    """属性测试"""

    def test_frequency_resolution(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        expected = 100_000_000 / 4096
        assert abs(proc.frequency_resolution - expected) < 1

    def test_nyquist_frequency(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        assert proc.nyquist_frequency == 50_000_000

    def test_sample_rate_setter(self):
        proc = FFTProcessor(sample_rate=100_000_000)
        proc.sample_rate = 2_000_000
        assert proc.sample_rate == 2_000_000

    def test_window_size(self):
        proc = FFTProcessor(window_size=2048)
        assert proc.window_size == 2048


class TestFFTSNR:
    """信噪比测试"""

    def test_snr_positive_with_signal(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        t = np.linspace(0, 40.96e-6, 4096)
        waveform = np.sin(2 * np.pi * 50e6 * t) * 10.0 + np.random.randn(4096) * 0.1
        result = proc.compute(waveform)
        assert result.snr_db > 0

    def test_noise_floor_estimation(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        t = np.linspace(0, 40.96e-6, 4096)
        waveform = np.sin(2 * np.pi * 50e6 * t) * 10.0
        result = proc.compute(waveform)
        assert result.noise_floor > 0


class TestFFTPeakDetection:
    """峰值检测测试"""

    def test_detect_peaks_basic(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        t = np.linspace(0, 40.96e-6, 4096)
        waveform = np.sin(2 * np.pi * 50e6 * t) * 10.0
        result = proc.compute(waveform, detect_peaks=True)
        assert isinstance(result.peaks, list)

    def test_detect_peaks_returns_peak_info(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        t = np.linspace(0, 40.96e-6, 4096)
        waveform = np.sin(2 * np.pi * 50e6 * t) * 10.0
        result = proc.compute(waveform)
        for peak in result.peaks:
            assert isinstance(peak, FFTPeakInfo)
            assert peak.frequency > 0
            assert peak.magnitude > 0

    def test_peaks_sorted_by_magnitude(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        t = np.linspace(0, 40.96e-6, 4096)
        # 多频率叠加
        waveform = np.sin(2 * np.pi * 10e6 * t) * 5.0
        waveform = waveform + np.sin(2 * np.pi * 50e6 * t) * 10.0
        waveform = waveform + np.sin(2 * np.pi * 100e6 * t) * 3.0
        result = proc.compute(waveform)
        if len(result.peaks) > 1:
            for i in range(len(result.peaks) - 1):
                assert result.peaks[i].magnitude >= result.peaks[i + 1].magnitude


class TestFFTSpectrogram:
    """频谱图测试"""

    def test_compute_spectrogram(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=1024)
        waveform = np.random.randn(10000)
        times, freqs, spectrogram = proc.compute_spectrogram(waveform)
        assert len(times) > 0
        assert len(freqs) > 0
        assert spectrogram.shape[0] == len(freqs)
        assert spectrogram.shape[1] == len(times)

    def test_compute_spectrogram_no_time_axis(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=1024)
        waveform = np.random.randn(10000)
        times, freqs, spectrogram = proc.compute_spectrogram(waveform, time_axis=False)
        assert len(times) == 0
        assert len(freqs) > 0


class TestFFTBandStats:
    """频段统计测试"""

    def test_band_stats_uhf(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        t = np.linspace(0, 40.96e-6, 4096)
        waveform = np.sin(2 * np.pi * 500e6 * t) * 10.0
        result = proc.compute(waveform, detect_peaks=False)
        assert "UHF_low" in result.band_stats
        assert "UHF_mid" in result.band_stats
        assert isinstance(result.band_stats["UHF_low"], BandStats)

    def test_band_stats_ae(self):
        proc = FFTProcessor(sample_rate=2_000_000, window_size=4096, bands=FFTProcessor.AE_BANDS)
        t = np.linspace(0, 0.002, 4096)
        waveform = np.sin(2 * np.pi * 100e3 * t) * 10.0
        result = proc.compute(waveform, detect_peaks=False)
        assert "AE_low" in result.band_stats
        assert "AE_mid" in result.band_stats
        assert "AE_high" in result.band_stats

    def test_custom_bands(self):
        custom = {"my_band": (1e6, 10e6)}
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096, bands=custom)
        t = np.linspace(0, 40.96e-6, 4096)
        waveform = np.sin(2 * np.pi * 5e6 * t) * 10.0
        result = proc.compute(waveform, detect_peaks=False)
        assert "my_band" in result.band_stats


class TestFFTFilter:
    """滤波器测试"""

    def test_apply_filter(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        mags = np.ones(2049)
        freqs = np.linspace(0, 50e6, 2049)
        filtered = proc.apply_filter(mags, freqs, f_min=10e6, f_max=30e6)
        assert filtered[0] == 0  # DC 被过滤
        assert filtered[-1] == 0  # > 30MHz 被过滤
        # 20MHz 附近应保留
        mid_idx = len(freqs) // 2
        assert filtered[mid_idx] > 0

    def test_smooth_spectrum(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        mags = np.random.randn(100)
        smoothed = proc.smooth_spectrum(mags, window_size=5)
        assert len(smoothed) == len(mags)
        # 平滑后方差应减小
        assert np.var(smoothed) <= np.var(mags) + 0.01


class TestFFTEmptyResult:
    """空结果测试"""

    def test_empty_result(self):
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        result = proc._empty_result()
        assert len(result.frequencies) == 0
        assert len(result.magnitudes) == 0
        assert result.sample_rate == 100_000_000


class TestFFTCacheRemoved:
    """回归：以波形字节为键的 FFT 结果缓存对本项目是净亏损，已整层删除。

    波形逐帧不重复，所以键每帧必变、命中恒为 0；而它唯一的"命中"路径既会
    丢掉 band_stats，又会因为模块里没有 logger 直接 NameError。
    """

    def test_compute_has_no_cache_knob(self):
        import inspect

        sig = inspect.signature(FFTProcessor.__init__)
        assert "use_cache" not in sig.parameters, "缓存层已判定为净亏损，不应有开关"

        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        assert not hasattr(proc, "_cache"), "实例上不该再挂着任何结果缓存"

    def test_result_cache_module_is_gone(self):
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("core.processing.result_cache")

    def test_no_peak_detection_still_returns_band_stats(self):
        """缓存命中路径曾返回一个没有频段统计的结果，前端频段带会整块消失。"""
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        rng = np.random.default_rng(3)
        wf = (rng.standard_normal(4096) * np.sin(np.linspace(0, 40, 4096))).astype(np.float64)

        result = proc.compute(wf, detect_peaks=False)

        assert len(result.band_stats) > 0, "detect_peaks=False 也要给出频段统计"
        assert len(result.phases) == len(result.frequencies), "相位不该是空的"

    def test_fftw_plan_failure_degrades_instead_of_crashing(self):
        """ "FFTW 初始化失败"分支用到过模块里并不存在的 logger，会抛 NameError。"""
        proc = FFTProcessor(sample_rate=100_000_000, window_size=4096)
        proc._use_fftw = True

        class Broken:
            def zeros_aligned(self, *a, **kw):
                raise RuntimeError("no FFTW")

        proc._pyfftw = Broken()
        proc._build_fft_plans()  # 不得抛异常

        assert proc._use_fftw is False, "构建计划失败必须回退到 numpy.fft"
