# -*- coding: utf-8 -*-
"""
AE 声发射 Hit 检测器

在包络信号上检测 AE hits (声发射突发):
- 浮动阈值 + 迟滞比较
- 可配置静寂时间 / 最大持续时间 / 重新准备时间
- 返回 AEHitInfo 数据类包含标准 AE 特征

与传统 UHF 峰值检测的区别:
- UHF PeakDetector: 在原始波形上找单个 RF 脉冲峰值
- AE AEPeakDetector: 在包络信号上找声发射突发 (hit)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AEHitInfo:
    """AE hit 信息"""

    peak_amplitude: float  # 包络峰值幅值 (mV)
    peak_position: int  # 峰值样本索引
    rise_time_us: float  # 10%-90% 上升时间 (us)
    duration_us: float  # hit 持续时间 (us)
    counts: int  # 振铃计数
    marse_energy: float  # MARSE 能量 (包络平方积分)
    avg_frequency_khz: float  # 平均频率 (kHz) = counts / duration
    rms: float  # hit 期间 RMS (mV)
    arrival_time: float  # 到达时间 (秒, 绝对或相对)
    start_position: int = 0  # hit 起始样本索引
    end_position: int = 0  # hit 结束样本索引
    phase_deg: float = 0.0  # 工频相位 (0-360)
    polarity: int = 0  # 首次运动极性 (0=正, 1=负)


class AEPeakDetector:
    """
    AE Hit 检测器

    基于浮动阈值检测包络信号中的声发射突发。

    Args:
        threshold_mode: 阈值模式, "relative" (相对最大峰值) / "absolute" (绝对值) / "noise" (相对噪声)
        threshold_value: 阈值数值, 取决于模式
        holdoff_time_us: 静寂时间 (us), hit 内忽略短时跌落
        max_duration_us: 最大持续时间 (us), 超过则强制结束 hit
        rearm_time_us: 重新准备时间 (us), hit 结束后忽略的恢复时间
        min_amplitude: 最小幅值 (mV), 低于此值的 hit 被过滤
    """

    def __init__(
        self,
        threshold_mode: str = "relative",
        threshold_value: float = 0.1,
        holdoff_time_us: float = 1000.0,
        max_duration_us: float = 50000.0,
        rearm_time_us: float = 5000.0,
        min_amplitude: float = 1.0,
    ):
        self.threshold_mode = threshold_mode
        self.threshold_value = threshold_value
        self.holdoff_time_us = holdoff_time_us
        self.max_duration_us = max_duration_us
        self.rearm_time_us = rearm_time_us
        self.min_amplitude = min_amplitude

    def detect_hits(self, envelope: np.ndarray, sample_rate: int) -> List[AEHitInfo]:
        """
        在包络信号上检测 AE hits

        Args:
            envelope: 包络信号数组 (1D)
            sample_rate: 采样率 (Hz)

        Returns:
            AEHitInfo 列表
        """
        if len(envelope) < 10:
            return []

        # 计算阈值
        threshold = self._compute_threshold(envelope, sample_rate)
        if threshold <= 0:
            return []

        hits: List[AEHitInfo] = []
        i = 0
        n = len(envelope)

        holdoff_samples = max(1, int(self.holdoff_time_us * sample_rate / 1_000_000))
        max_dur_samples = int(self.max_duration_us * sample_rate / 1_000_000)
        rearm_samples = max(1, int(self.rearm_time_us * sample_rate / 1_000_000))

        while i < n:
            # 寻找阈值交叉 (hit 起始)
            if envelope[i] <= threshold:
                i += 1
                continue

            # hit 开始
            start = i
            # 回退到噪底交叉点 (更精确的起始)
            while start > 0 and envelope[start] > threshold * 0.5:
                start -= 1

            # 向前找 hit 结束 (低于阈值超过 holdoff 时间)
            end = start
            below_count = 0
            for j in range(start, min(n, start + max_dur_samples + holdoff_samples)):
                end = j
                if envelope[j] <= threshold:
                    below_count += 1
                    if below_count >= holdoff_samples:
                        break
                else:
                    below_count = 0

            # 确保至少跨越 holdoff 长度
            if below_count < holdoff_samples and end < n - 1:
                end = min(n - 1, start + max_dur_samples)

            # 提取 hit 段
            hit_segment = envelope[start : end + 1]
            if len(hit_segment) < 3:
                i = end + rearm_samples
                continue

            # 寻找峰值
            peak_idx_local = int(np.argmax(hit_segment))
            peak_idx_global = start + peak_idx_local
            peak_amp = float(hit_segment[peak_idx_local])

            # 最小幅值过滤
            if peak_amp < self.min_amplitude:
                i = end + rearm_samples
                continue

            # 计算 AE 参数
            hit_info = self._compute_hit_params(envelope, start, end, peak_idx_global, peak_amp, sample_rate, threshold)
            hits.append(hit_info)

            # 跳过后面的静寂期
            i = end + rearm_samples

        return hits

    def _compute_threshold(self, envelope: np.ndarray, sample_rate: int) -> float:
        """计算检测阈值"""
        if self.threshold_mode == "absolute":
            return self.threshold_value
        elif self.threshold_mode == "noise":
            # 基于噪声水平: threshold = noise_rms * threshold_value
            # 取信号前 10% 或能量最低的 10% 窗口估算噪声
            n_windows = max(10, len(envelope) // 1000)
            window_size = len(envelope) // n_windows
            rms_values = []
            for i in range(n_windows):
                seg = envelope[i * window_size : (i + 1) * window_size]
                if len(seg) > 0:
                    rms_values.append(float(np.sqrt(np.mean(seg**2))))
            noise_floor = float(np.percentile(rms_values, 10))
            return max(noise_floor * self.threshold_value, 0.01)
        else:  # relative
            # 相对峰值: threshold = 最大值 * threshold_value
            max_val = float(np.max(envelope))
            return max_val * self.threshold_value

    def _compute_hit_params(
        self,
        envelope: np.ndarray,
        start: int,
        end: int,
        peak_idx: int,
        peak_amp: float,
        sample_rate: int,
        threshold: float,
    ) -> AEHitInfo:
        """计算单个 hit 的 AE 参数"""

        duration_us = (end - start) / sample_rate * 1_000_000

        # 上升时间: 10% -> 90% 峰值
        rise_start = peak_idx
        for k in range(peak_idx, start - 1, -1):
            if envelope[k] <= peak_amp * 0.1:
                rise_start = k
                break
        rise_end = peak_idx
        for k in range(peak_idx, start - 1, -1):
            if envelope[k] >= peak_amp * 0.9:
                rise_end = k
                break
        rise_time_us = max(0, (rise_end - rise_start)) / sample_rate * 1_000_000

        # MARSE 能量: 包络平方的积分
        hit_segment = envelope[start : end + 1]
        if len(hit_segment) > 1:
            try:
                marse_energy = float(np.trapz(hit_segment**2)) / sample_rate
            except AttributeError:
                marse_energy = float(np.trapezoid(hit_segment**2)) / sample_rate
        else:
            marse_energy = 0.0

        # RMS
        rms = float(np.sqrt(np.mean(hit_segment**2))) if len(hit_segment) > 0 else 0.0

        # 振铃计数: 包络超过阈值 50% 的次数
        half_thresh = threshold * 0.5
        counts = 0
        crossed = False
        for val in hit_segment:
            if val > half_thresh and not crossed:
                counts += 1
                crossed = True
            elif val <= half_thresh:
                crossed = False

        # 平均频率
        avg_freq_khz = (counts / max(duration_us, 1)) * 1000 if duration_us > 0 else 0.0

        return AEHitInfo(
            peak_amplitude=peak_amp,
            peak_position=peak_idx,
            rise_time_us=rise_time_us,
            duration_us=duration_us,
            counts=counts,
            marse_energy=marse_energy,
            avg_frequency_khz=avg_freq_khz,
            rms=rms,
            arrival_time=start / sample_rate,
            start_position=start,
            end_position=end,
            phase_deg=0.0,
            polarity=0,
        )
