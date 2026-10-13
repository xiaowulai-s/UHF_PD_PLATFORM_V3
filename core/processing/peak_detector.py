# -*- coding: utf-8 -*-
"""
峰值检测器 - PeakDetector

用于从波形数据中检测局放脉冲峰值。
支持：
- 阈值峰值检测（绝对阈值 / 自适应阈值）
- 峰值参数计算（幅值、宽度、面积、能量）
- 噪声抑制与基线校正
- 多峰分离
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class PeakInfo:
    """峰值信息"""

    position: int  # 波形中的索引位置
    amplitude: float  # 峰值幅值
    width: int = 0  # 半高宽 (点数)
    area: float = 0.0  # 峰面积
    energy: float = 0.0  # 脉冲能量
    rise_time: float = 0.0  # 上升时间 (点数)
    fall_time: float = 0.0  # 下降时间 (点数)
    snr: float = 0.0  # 信噪比
    polarity: int = 0  # 极性: 0=正, 1=负


class PeakDetector:
    """
    峰值检测器

    Args:
        threshold: 检测阈值（绝对值或相对于噪声的倍数）
        adaptive: 是否使用自适应阈值
        min_distance: 峰之间的最小距离（点数）
        window_size: 用于局部极大值检测的窗口大小
    """

    def __init__(
        self,
        threshold: float = 10.0,
        adaptive: bool = True,
        min_distance: int = 10,
        window_size: int = 5,
    ):
        self._threshold = threshold
        self._adaptive = adaptive
        self._min_distance = min_distance
        self._window_size = window_size
        self._noise_level: float = 1.0
        self._lock = threading.Lock()

    # ── 核心检测 ─────────────────────────────────────

    def detect_peaks(
        self,
        waveform: np.ndarray,
        threshold: Optional[float] = None,
    ) -> List[PeakInfo]:
        """
        检测波形中的峰值

        Args:
            waveform: 输入波形 (1D numpy array)
            threshold: 检测阈值，默认使用内部阈值

        Returns:
            检测到的峰值列表（按幅值降序）
        """
        if len(waveform) < self._min_distance:
            return []

        waveform = self._remove_baseline(waveform)
        noise_level = self._estimate_noise(waveform)
        with self._lock:
            self._noise_level = noise_level

        # 确定阈值
        effective_threshold = threshold if threshold is not None else self._threshold
        if self._adaptive:
            effective_threshold = max(effective_threshold, noise_level * 3.0)

        # 寻找局部极大值
        from scipy.signal import find_peaks

        # 正峰值
        pos_peaks_idx, pos_props = find_peaks(
            waveform,
            height=effective_threshold,
            distance=self._min_distance,
            prominence=noise_level,
            width=1,
        )

        # 负峰值
        neg_peaks_idx, neg_props = find_peaks(
            -waveform,
            height=effective_threshold,
            distance=self._min_distance,
            prominence=noise_level,
            width=1,
        )

        peaks = []
        for idx in pos_peaks_idx:
            peak = self._extract_peak_info(waveform, idx, noise_level, polarity=0)
            peaks.append(peak)

        for idx in neg_peaks_idx:
            peak = self._extract_peak_info(waveform, idx, noise_level, polarity=1)
            peak.amplitude = -peak.amplitude  # 负峰值保持负值
            peaks.append(peak)

        # 按幅值绝对值降序
        peaks.sort(key=lambda p: abs(p.amplitude), reverse=True)
        return peaks

    def detect_pd_events(
        self,
        waveform: np.ndarray,
        threshold: Optional[float] = None,
    ) -> List[PeakInfo]:
        """
        专门用于局放检测的峰值检测

        与 detect_peaks 的区别：
        - 更保守的噪声抑制
        - 合并相邻峰值
        - 计算脉冲能量

        Args:
            waveform: 输入波形
            threshold: 检测阈值

        Returns:
            局放事件列表
        """
        peaks = self.detect_peaks(waveform, threshold)

        # 合并距离过近的峰值（取幅值较大的）
        merged = []
        if not peaks:
            return merged

        peaks.sort(key=lambda p: p.position)
        current = peaks[0]

        for next_peak in peaks[1:]:
            if next_peak.position - current.position < self._min_distance:
                # 保留幅值较大的
                if abs(next_peak.amplitude) > abs(current.amplitude):
                    current = next_peak
            else:
                merged.append(current)
                current = next_peak
        merged.append(current)

        # 按幅值绝对值排序
        merged.sort(key=lambda p: abs(p.amplitude), reverse=True)
        return merged

    def detect_peaks_simple(self, data: np.ndarray) -> List[PeakInfo]:
        """
        简化峰值检测（不依赖 scipy）

        Args:
            data: 输入数据

        Returns:
            峰值列表
        """
        # 自适应阈值: max(固定阈值, 噪声水平*3)
        noise = self._estimate_noise(data)
        effective_threshold = max(self._threshold, noise * 3.0)
        with self._lock:
            self._noise_level = noise

        peaks = []
        half_win = self._window_size // 2
        n = len(data)

        for i in range(half_win, n - half_win):
            if data[i] > effective_threshold:
                segment = data[i - half_win : i + half_win + 1]
                if data[i] == np.max(segment):
                    # 检查是否确实是局部极大值
                    left_ok = all(data[i] > data[i - j - 1] for j in range(min(3, i)))
                    right_ok = all(data[i] > data[i + j + 1] for j in range(min(3, n - i - 1)))
                    if left_ok or right_ok:
                        peaks.append(
                            PeakInfo(
                                position=i,
                                amplitude=float(data[i]),
                            )
                        )

        # 去重（相邻峰值只保留一个）
        if len(peaks) > 1:
            filtered = [peaks[0]]
            for p in peaks[1:]:
                if p.position - filtered[-1].position >= self._min_distance:
                    filtered.append(p)
            peaks = filtered

        peaks.sort(key=lambda p: abs(p.amplitude), reverse=True)
        return peaks

    # ── 内部辅助 ─────────────────────────────────────

    def _extract_peak_info(
        self,
        waveform: np.ndarray,
        idx: int,
        noise_level: float,
        polarity: int = 0,
    ) -> PeakInfo:
        """提取峰值的详细信息"""
        n = len(waveform)
        amplitude = float(waveform[idx])

        # 半高宽
        half_amp = abs(amplitude) / 2
        left = idx
        right = idx
        while left > 0 and abs(waveform[left]) > half_amp:
            left -= 1
        while right < n - 1 and abs(waveform[right]) > half_amp:
            right += 1
        width = right - left

        # 上升/下降时间
        rise_time = idx - left
        fall_time = right - idx

        # 峰面积（绝对值积分）
        area = float(np.sum(np.abs(waveform[left:right])))

        # 脉冲能量（幅值平方积分）
        energy = float(np.sum(waveform[left:right] ** 2))

        # SNR
        snr = abs(amplitude) / (noise_level + 1e-10)

        return PeakInfo(
            position=idx,
            amplitude=amplitude,
            width=width,
            area=area,
            energy=energy,
            rise_time=rise_time,
            fall_time=fall_time,
            snr=snr,
            polarity=polarity,
        )

    def _estimate_noise(self, waveform: np.ndarray) -> float:
        """估计噪声水平（使用中位数绝对偏差）"""
        if len(waveform) < 100:
            return 1.0
        # 使用后 50% 或不含峰值的区域估计噪声
        median = np.median(waveform)
        mad = np.median(np.abs(waveform - median))
        noise = mad / 0.6745  # 高斯噪声的标准差估计
        return max(noise, 0.1)

    def _remove_baseline(self, waveform: np.ndarray) -> np.ndarray:
        """去除基线漂移"""
        if len(waveform) < 100:
            return waveform.copy()
        baseline = np.median(waveform)
        return waveform - baseline

    def set_threshold(self, threshold: float) -> None:
        """设置检测阈值"""
        self._threshold = threshold

    def set_adaptive(self, adaptive: bool) -> None:
        """设置是否使用自适应阈值"""
        self._adaptive = adaptive

    @property
    def noise_level(self) -> float:
        """当前估计的噪声水平"""
        with self._lock:
            return self._noise_level

    def __repr__(self) -> str:
        return f"PeakDetector(threshold={self._threshold}, adaptive={self._adaptive})"
