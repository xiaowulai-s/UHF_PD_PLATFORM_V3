# -*- coding: utf-8 -*-
"""PRPDProcessor 专项单元测试"""

import numpy as np
import pytest

from core.processing.prpd_processor import PRPDMode, PRPDProcessor, PRPDResult


class TestPRPDAddEvent:
    """事件添加测试"""

    def test_add_event_basic(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50)
        assert prpd.matrix[90, :].sum() > 0

    def test_add_events_batch(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        events = [(i * 10, 30 + i) for i in range(36)]
        prpd.add_events(events)
        result = prpd.compute()
        assert result.total_events == 36

    def test_phase_wraparound(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=370, amplitude=50)  # > 360
        assert prpd.matrix[10, :].sum() > 0  # 370 % 360 = 10

    def test_amplitude_clipping(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=200)  # 超过 max
        prpd.add_event(phase=90, amplitude=-10)  # 负值
        # 不应崩溃
        assert prpd.matrix.sum() > 0

    def test_event_cycle_tracking(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50, cycle=5)
        result = prpd.compute()
        assert result.events[0].cycle == 5

    def test_auto_cycle_when_zero(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50, cycle=0)
        result = prpd.compute()
        assert result.events[0].cycle == 0  # 使用内部计数器


class TestPRPDDecay:
    """衰减测试"""

    def test_decay_factor(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50)
        val_before = prpd.matrix[90, :].max()
        prpd.decay(factor=0.9)
        val_after = prpd.matrix[90, :].max()
        assert val_after < val_before
        assert abs(val_after - val_before * 0.9) < 0.01

    def test_decay_clips_negative(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50)
        # 极端衰减不会产生负值
        prpd.decay(factor=0.001)
        assert np.all(prpd.matrix >= 0)

    def test_decay_no_events(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.decay(factor=0.9)  # 空矩阵衰减不崩溃
        assert prpd.matrix.sum() == 0


class TestPRPDRebuild:
    """矩阵重建测试"""

    def test_rebuild_matrix(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        for i in range(100):
            prpd.add_event(phase=i * 3.6, amplitude=50)

        matrix_before = prpd.matrix.copy()
        prpd.rebuild_matrix()
        np.testing.assert_array_equal(prpd.matrix, matrix_before)

    def test_rebuild_after_max_amplitude_change(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50)
        prpd.add_event(phase=180, amplitude=80)

        # 扩展 max_amplitude 后重建
        prpd.max_amplitude = 200
        prpd.rebuild_matrix()

        # 事件应重新分布到新的分箱中
        assert prpd.matrix.sum() > 0


class TestPRPDCompute:
    """计算模式测试"""

    def test_compute_heatmap_mode(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50)
        result = prpd.compute(mode=PRPDMode.HEATMAP)
        assert isinstance(result, PRPDResult)
        assert result.matrix.shape == (360, 256)
        assert result.mode == PRPDMode.HEATMAP

    def test_compute_scatter_mode(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        for i in range(50):
            prpd.add_event(phase=i * 7.2, amplitude=30 + i)
        result = prpd.compute(mode=PRPDMode.SCATTER)
        assert len(result.events) == 50

    def test_compute_density_mode(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50)
        result = prpd.compute(mode=PRPDMode.DENSITY)
        assert result.matrix.shape == (360, 256)
        # 高斯平滑后矩阵应有值
        assert result.matrix.sum() > 0

    def test_compute_empty(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        result = prpd.compute()
        assert result.total_events == 0
        assert result.max_amplitude == 0
        assert result.matrix.sum() == 0


class TestPRPDStats:
    """统计特征测试"""

    def test_compute_stats_empty(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        stats = prpd.compute_stats()
        assert stats["total_events"] == 0
        assert stats["max_amplitude"] == 0
        assert stats["avg_amplitude"] == 0

    def test_compute_stats_with_data(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        for i in range(100):
            prpd.add_event(phase=i * 3.6, amplitude=50)
        stats = prpd.compute_stats()
        assert stats["total_events"] == 100
        assert stats["max_amplitude"] == 50
        assert stats["avg_amplitude"] == 50
        assert 0 <= stats["positive_ratio"] <= 1
        assert 0 <= stats["phase_concentration"] <= 1

    def test_positive_ratio(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50, polarity=0)
        prpd.add_event(phase=180, amplitude=50, polarity=1)
        stats = prpd.compute_stats()
        assert stats["positive_ratio"] == 0.5


class TestPRPDReset:
    """重置测试"""

    def test_reset(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50)
        prpd.reset()
        assert prpd.matrix.sum() == 0
        assert len(prpd.compute().events) == 0

    def test_reset_then_add(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_event(phase=90, amplitude=50)
        prpd.reset()
        prpd.add_event(phase=180, amplitude=80)
        result = prpd.compute()
        assert result.total_events == 1


class TestPRPDAddWaveform:
    """波形添加测试"""

    def test_add_waveform(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        # 包含峰值的波形
        waveform = np.zeros(1000)
        waveform[200] = 50
        waveform[500] = 80
        waveform[800] = 30
        prpd.add_waveform(waveform)
        result = prpd.compute()
        # 应检测到至少 1 个事件
        assert result.total_events >= 1

    def test_add_waveform_empty(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.add_waveform(np.zeros(1000))
        # 空波形不应崩溃
        assert prpd.compute().total_events == 0


class TestPRPDMaxAmplitudeDynamic:
    """动态 max_amplitude 测试"""

    def test_dynamic_expand(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        prpd.max_amplitude = 200
        assert prpd.max_amplitude == 200

    def test_repr(self):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)
        r = repr(prpd)
        assert "PRPDProcessor" in r
        assert "360" in r


class TestPrpdPolarityRegression:
    """PRPD 曾经用 detect_peaks_simple（只找正峰）且从幅值符号反推极性，
    结果是负极性放电在图谱里缺失、其正旁瓣被当独立事件、全部事件被标成"负"。
    这三条用同一个双极性波形一起钉住。
    """

    @staticmethod
    def bipolar_waveform(n=4096, seed=3):
        """注入 3 正 3 负共 6 个脉冲。"""
        import numpy as np

        rng = np.random.RandomState(seed)
        wf = rng.randn(n) * 2.0
        for j, amp in enumerate([60.0, -55.0, 70.0, -65.0, 50.0, -58.0]):
            pos = int((j + 1) * n / 7)
            w = 12
            for i in range(-w, w):
                wf[(pos + i) % n] += amp * np.exp(-abs(i) / (w * 0.3)) * np.cos(2 * np.pi * i / w * 2)
        return wf

    def test_add_waveform_sees_both_polarities(self):
        from core.processing.prpd_processor import PRPDProcessor

        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100.0)
        prpd.add_waveform(self.bipolar_waveform())
        pols = {e.polarity for e in prpd._events}

        assert pols == {0, 1}, f"图谱里极性只剩 {pols}，说明有一极没被检出"
        assert len(prpd._events) == 6, "注入 6 个脉冲，多出来的就是负脉冲的正旁瓣被当成事件"

    def test_polarity_labels_match_the_event_stream(self):
        """同一帧波形，PRPD 与事件流必须给出同一套极性——只能有一个检测真相。"""
        from core.processing.peak_detector import PeakDetector
        from core.processing.prpd_processor import PRPDProcessor

        wf = self.bipolar_waveform()
        stream = PeakDetector().detect_pd_events(wf.copy())
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100.0)
        prpd.add_detected_events(stream, len(wf))

        assert sorted(e.polarity for e in prpd._events) == sorted(p.polarity for p in stream)
        # amplitude 是幅值大小（负极性也为正），极性只在 polarity 里
        assert all(e.amplitude > 0 for e in prpd._events)

    def test_add_detected_events_maps_position_to_phase(self):
        from core.processing.peak_detector import PeakInfo
        from core.processing.prpd_processor import PRPDProcessor

        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100.0)
        prpd.add_detected_events([PeakInfo(position=1024, amplitude=40.0, polarity=1)], 4096)

        ev = prpd._events[0]
        assert ev.phase == pytest.approx(90.0, abs=1.0)
        assert ev.polarity == 1, "polarity 被从幅值符号反推了"
        assert ev.amplitude == pytest.approx(40.0)

    def test_zero_length_waveform_is_ignored(self):
        from core.processing.prpd_processor import PRPDProcessor

        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100.0)
        prpd.add_detected_events([], 0)
        assert len(prpd._events) == 0


class TestPRPDNoResultRetention:
    """回归：PRPD 结果曾经被塞进一层"缓存"，实测既省不了计算又常驻内存。

    起因是长稳测试里 sidecar RSS 前 10.5 分钟以 17.4 MB/min 线性上升，到 +182.6 MB
    后骤降为 0.7 MB/min 走平——正好停在 PRPDCache 的 200 MB LRU 天花板上。
    缓存键取整条累积事件流的 md5，而事件只增不减，所以键每帧必变：连续事件下
    60 次 compute() 命中 0 次，反而每帧多两次 md5 加一份矩阵副本。
    """

    PHASE_BINS = 120
    AMP_BINS = 64

    def _proc(self):
        return PRPDProcessor(
            phase_bins=self.PHASE_BINS,
            amplitude_bins=self.AMP_BINS,
            max_amplitude=100.0,
        )

    @staticmethod
    def _prpd_retained(snapshot):
        """只统计 prpd_processor.py 自己那些行上仍然活着的分配。

        一开始写的是"全进程 traced memory 前后差值"，单跑很稳，但整套跑时会偶发
        越限——别的用例在本用例两次取样之间也会分配、或延迟到此刻才释放。按文件名
        过滤把这部分噪声摘掉，代价只是多两次 snapshot。
        """
        return sum(st.size for st in snapshot.statistics("filename") if "prpd_processor.py" in str(st.traceback))

    def test_repeated_compute_does_not_retain_matrices(self):
        """结果被丢弃后不得留在内存里——包括任何形式的"结果缓存"。"""
        import gc
        import tracemalloc

        proc = self._proc()
        one_matrix = self.PHASE_BINS * self.AMP_BINS * 8  # float64

        for i in range(5):  # 预热：首次 compute 会分配 scipy/linspace 等一次性对象
            proc.add_event(phase=float(i), amplitude=float(20 + i))
            proc.compute(PRPDMode.HEATMAP)
        gc.collect()

        tracemalloc.start()
        try:
            base = self._prpd_retained(tracemalloc.take_snapshot())
            for i in range(60):
                # 每帧都有新事件，所以任何以事件集合为键的缓存必然换键、必然 miss
                proc.add_event(phase=float(i % 120), amplitude=float(10 + (i % 50)))
                proc.compute(PRPDMode.HEATMAP)
            gc.collect()
            leaked = self._prpd_retained(tracemalloc.take_snapshot()) - base
        finally:
            tracemalloc.stop()

        # 滞留一份以上矩阵即视为回归：四份以下算瞬时对象噪声
        assert leaked < one_matrix * 4, "重复 compute() 滞留了 %.0f KB，约 %.1f 份矩阵" % (
            leaked / 1024,
            leaked / one_matrix,
        )

    def test_compute_has_no_cache_knob(self):
        """缓存开关必须彻底消失，不能被"顺手打开"。"""
        import inspect

        sig = inspect.signature(PRPDProcessor.__init__)
        assert "use_cache" not in sig.parameters, "以累积事件流为键的结果缓存对本项目是净亏损，见类内 compute() 说明"
        proc = self._proc()
        assert not hasattr(proc, "_cache"), "实例上不该再挂着任何结果缓存"

    def test_result_cache_module_is_gone(self):
        """`add_event` 已增量维护矩阵，compute() 只剩一次 copy，无处可缓存。"""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("core.processing.result_cache")
