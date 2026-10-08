# -*- coding: utf-8 -*-
"""
AE 声发射信号包络处理器

实现超声波局部放电信号的包络检波预处理:
- 带通滤波 (20-200kHz 典型 AE 频段)
- Hilbert 变换包络提取
- RMS 噪声基线估计

AE 信号特点:
- 频率范围: 20-200kHz
- 采样率: 1-10MHz
- 包络线代表声发射突发的能量轮廓
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class AEEnvelopeProcessor:
    """
    AE 信号包络处理器

    Args:
        lowcut_hz: 带通下限 (Hz), 默认 20_000 (20kHz)
        highcut_hz: 带通上限 (Hz), 默认 200_000 (200kHz)
        sample_rate: 采样率 (Hz), 默认 2_000_000 (2MHz)
        order: 滤波器阶数, 默认 4
    """

    def __init__(
        self,
        lowcut_hz: float = 20_000,
        highcut_hz: float = 200_000,
        sample_rate: int = 2_000_000,
        order: int = 4,
    ):
        self._lowcut = lowcut_hz
        self._highcut = highcut_hz
        self._sample_rate = sample_rate
        self._order = order
        self._filter_coeffs: Optional[tuple] = None  # (b, a) 滤波器系数缓存

    @property
    def lowcut_hz(self) -> float:
        return self._lowcut

    @lowcut_hz.setter
    def lowcut_hz(self, value: float) -> None:
        if value != self._lowcut:
            self._lowcut = value
            self._filter_coeffs = None  # 缓存失效

    @property
    def highcut_hz(self) -> float:
        return self._highcut

    @highcut_hz.setter
    def highcut_hz(self, value: float) -> None:
        if value != self._highcut:
            self._highcut = value
            self._filter_coeffs = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, value: int) -> None:
        if value != self._sample_rate:
            self._sample_rate = value
            self._filter_coeffs = None

    # ── 滤波 ─────────────────────────────────────────

    def _ensure_filter(self) -> tuple:
        """缓存 Butterworth 滤波器系数（使用 SOS 二阶节以保证数值稳定性）"""
        if self._filter_coeffs is not None:
            return self._filter_coeffs

        from scipy.signal import butter, sosfreqz

        nyquist = 0.5 * self._sample_rate
        low = self._lowcut / nyquist
        high = self._highcut / nyquist
        # 限幅到有效范围
        low = max(1e-6, low)
        high = min(0.9999, high)
        if low >= high:
            logger.warning("AE 带通参数异常: low=%f, high=%f, 使用全通", low, high)
            self._filter_coeffs = (np.array([1.0]), np.array([1.0]))
            return self._filter_coeffs

        # 使用 sos (second-order sections) 代替 b/a 以提高数值稳定性
        sos = butter(self._order, [low, high], btype="band", output="sos")
        # 稳定性检查：极点必须在单位圆内
        try:
            import numpy as np

            w, h = sosfreqz(sos)
            if np.any(np.abs(h) > 1.0 + 1e-6):
                logger.warning("AE 带通滤波器不稳定，降阶至 2 阶")
                sos = butter(2, [low, high], btype="band", output="sos")
        except (ValueError, TypeError, ImportError, RuntimeError):
            logger.warning("AE 滤波器稳定性检查异常，使用 2 阶保底")
            sos = butter(2, [low, high], btype="band", output="sos")
        self._filter_coeffs = sos
        return self._filter_coeffs

    def bandpass_filter(self, samples: np.ndarray) -> np.ndarray:
        """
        4 阶 Butterworth 带通滤波

        Args:
            samples: 原始信号数组

        Returns:
            滤波后信号数组
        """
        if len(samples) < self._order * 2:
            return samples.copy()

        try:
            from scipy.signal import sosfilt

            sos = self._ensure_filter()
            # sosfilt 零相位滤波（二阶节串联，数值稳定）
            return sosfilt(sos, samples)
        except ImportError:
            logger.debug("scipy 不可用，跳过带通滤波")
            return samples.copy()
        except (ValueError, TypeError, RuntimeError) as e:
            logger.debug("带通滤波异常: %s", e, exc_info=True)
            return samples.copy()

    # ── 包络提取 ─────────────────────────────────────

    def compute_envelope(self, samples: np.ndarray, method: str = "hilbert") -> np.ndarray:
        """
        计算 AE 信号包络

        Args:
            samples: 输入信号 (原始或滤波后)
            method: 包络算法, "hilbert" (Hilbert 变换) 或 "rectify" (全波整流+低通)

        Returns:
            包络信号数组 (长度同 samples)
        """
        if len(samples) < 2:
            return np.abs(samples) if len(samples) > 0 else np.array([])

        if method == "hilbert":
            return self._envelope_hilbert(samples)
        else:
            return self._envelope_rectify(samples)

    def _envelope_hilbert(self, samples: np.ndarray) -> np.ndarray:
        """Hilbert 变换包络（首选方法）"""
        try:
            from scipy.signal import hilbert

            return np.abs(hilbert(samples))
        except ImportError:
            logger.debug("scipy 不可用，回退到整流包络")
            return self._envelope_rectify(samples)

    def _envelope_rectify(self, samples: np.ndarray) -> np.ndarray:
        """
        全波整流 + 低通滤波包络

        作为 Hilbert 变换的回退方案。
        """
        rectified = np.abs(samples)
        if len(rectified) < 10:
            return rectified
        try:
            from scipy.signal import butter, sosfilt

            nyquist = 0.5 * self._sample_rate
            # 低通截止: 50kHz（足够提取 AE 包络轮廓）
            cutoff = min(50_000, nyquist * 0.95)
            sos = butter(2, cutoff / nyquist, btype="low", output="sos")
            return sosfilt(sos, rectified)
        except ImportError:
            # 无 scipy: 简单移动平均
            window = max(1, self._sample_rate // 100_000)  # ~10us @ 10MHz
            kernel = np.ones(window) / window
            return np.convolve(rectified, kernel, mode="same")

    # ── RMS ──────────────────────────────────────────

    def compute_rms(self, samples: np.ndarray, window_ms: float = 1.0) -> float:
        """
        计算信号 RMS 值（用于噪声基线估计）

        Args:
            samples: 输入信号
            window_ms: 滑动窗口长度 (ms)

        Returns:
            RMS 值 (float)
        """
        if len(samples) < 1:
            return 0.0

        window_samples = max(1, int(self._sample_rate * window_ms / 1000))
        n_windows = max(1, len(samples) // window_samples)

        rms_values = []
        for i in range(n_windows):
            segment = samples[i * window_samples : (i + 1) * window_samples]
            if len(segment) > 0:
                rms_values.append(float(np.sqrt(np.mean(segment**2))))

        return float(np.median(rms_values)) if rms_values else 0.0
