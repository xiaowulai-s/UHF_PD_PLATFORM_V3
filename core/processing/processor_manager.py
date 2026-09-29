# -*- coding: utf-8 -*-
"""
处理器工厂 — ProcessorManager

线程安全的 per-channel 处理器生命周期管理。
从 AcquisitionService 提取，职责: 创建/缓存/释放各通道处理器实例。
"""

from __future__ import annotations

import logging
import threading
from typing import Dict

from core.processing.ae_envelope import AEEnvelopeProcessor
from core.processing.ae_parameter_extractor import AEParameterExtractor
from core.processing.ae_peak_detector import AEPeakDetector
from core.processing.fft_processor import FFTProcessor
from core.processing.peak_detector import PeakDetector
from core.processing.prpd_processor import PRPDProcessor
from core.processing.prps_processor import PRPSProcessor
from core.processing.ring_buffer import RingBuffer

logger = logging.getLogger(__name__)


class ProcessorManager:
    """
    线程安全的 per-channel 处理器工厂

    每个 (device_id, channel_id) 组合对应一组独立的处理器实例，
    使用 RLock 保证并发安全。

    Args:
        default_sample_rate: 数据帧未带采样率时的兜底值
        waveform_points: 波形点数
        default_max_amplitude: PRPD 幅值轴的**初始**量程，单位与波形样本一致。
            默认 100 mV——本仓库的"满量程"共识（PRPDProcessor 类默认值、
            alarm_service 全局基准、模拟器自身 PRPD 与 floating 类幅值上限都是 100）。
    """

    def __init__(
        self,
        default_sample_rate: int = 100_000_000,
        waveform_points: int = 4096,
        default_max_amplitude: float = 100.0,
    ):
        self._default_sample_rate = default_sample_rate
        self._waveform_points = waveform_points
        self._default_max_amplitude = default_max_amplitude
        self._lock = threading.RLock()

        # UHF 处理器
        self._fft_processors: Dict[str, FFTProcessor] = {}
        self._prpd_processors: Dict[str, PRPDProcessor] = {}
        self._prps_processors: Dict[str, PRPSProcessor] = {}
        self._ring_buffers: Dict[str, RingBuffer] = {}
        self._peak_detectors: Dict[str, PeakDetector] = {}
        self._sample_rates: Dict[str, int] = {}

        # AE 处理器
        self._ae_envelope_processors: Dict[str, AEEnvelopeProcessor] = {}
        self._ae_peak_detectors: Dict[str, AEPeakDetector] = {}
        self._ae_param_extractors: Dict[str, AEParameterExtractor] = {}
        self._channel_coupling_types: Dict[str, str] = {}

    # ── 通道 key ──────────────────────────────────────

    @staticmethod
    def channel_key(device_id: str, channel_id: int) -> str:
        return f"{device_id}:{channel_id}"

    # ── 采样率 / 耦合类型 ────────────────────────────

    def set_sample_rate(self, device_id: str, channel_id: int, sr: int) -> None:
        key = self.channel_key(device_id, channel_id)
        with self._lock:
            self._sample_rates[key] = sr

    def get_sample_rate(self, device_id: str, channel_id: int) -> int:
        key = self.channel_key(device_id, channel_id)
        with self._lock:
            return self._sample_rates.get(key, self._default_sample_rate)

    def set_channel_coupling_type(self, device_id: str, channel_id: int, coupling_type: str) -> None:
        key = self.channel_key(device_id, channel_id)
        if coupling_type in ("uhf", "ae"):
            with self._lock:
                self._channel_coupling_types[key] = coupling_type

    def get_channel_coupling_type(self, device_id: str, channel_id: int) -> str:
        key = self.channel_key(device_id, channel_id)
        with self._lock:
            return self._channel_coupling_types.get(key, "uhf")

    # ── 处理器工厂 ────────────────────────────────────

    def get_or_create_fft(self, device_id: str, channel_id: int) -> FFTProcessor:
        with self._lock:
            key = self.channel_key(device_id, channel_id)
            if key not in self._fft_processors:
                sr = self._sample_rates.get(key, self._default_sample_rate)
                self._fft_processors[key] = FFTProcessor(
                    sample_rate=sr,
                    window_size=min(self._waveform_points, 4096),
                )
            return self._fft_processors[key]

    def get_or_create_prpd(self, device_id: str, channel_id: int) -> PRPDProcessor:
        with self._lock:
            key = self.channel_key(device_id, channel_id)
            if key not in self._prpd_processors:
                # 初值必须是该通道的真实满量程，不能图省事取大值：
                # publish_throttle.prpd_cycle 只在观测幅值 > 轴*0.8 时把轴扩到观测值*1.2，
                # **只增不减**。所以初值取大了（此前是 10000）就没有任何代码路径能把它
                # 降回来，mV 级数据会永久挤在第 0~1 个幅值箱里；取小了则第一帧就自动
                # 扩展到位（原始 int16 计数数据观测到 8462 → 扩到约 10154，恰好回到
                # 今天这个值），因此改成 100 对真机零代价、对 mV 口径是修复。
                self._prpd_processors[key] = PRPDProcessor(
                    phase_bins=360,
                    amplitude_bins=256,
                    max_amplitude=self._default_max_amplitude,
                )
            return self._prpd_processors[key]

    def get_or_create_prps(self, device_id: str, channel_id: int) -> PRPSProcessor:
        with self._lock:
            key = self.channel_key(device_id, channel_id)
            if key not in self._prps_processors:
                self._prps_processors[key] = PRPSProcessor(rows=512, cols=360)
            return self._prps_processors[key]

    def get_or_create_buffer(self, device_id: str, channel_id: int) -> RingBuffer:
        with self._lock:
            key = self.channel_key(device_id, channel_id)
            if key not in self._ring_buffers:
                self._ring_buffers[key] = RingBuffer(maxlen=1_000_000)
            return self._ring_buffers[key]

    def get_or_create_peak_detector(self, device_id: str, channel_id: int) -> PeakDetector:
        with self._lock:
            key = self.channel_key(device_id, channel_id)
            if key not in self._peak_detectors:
                self._peak_detectors[key] = PeakDetector(adaptive=True)
            return self._peak_detectors[key]

    def get_or_create_ae_envelope(self, device_id: str, channel_id: int) -> AEEnvelopeProcessor:
        with self._lock:
            key = self.channel_key(device_id, channel_id)
            if key not in self._ae_envelope_processors:
                sr = self._sample_rates.get(key, 2_000_000)
                self._ae_envelope_processors[key] = AEEnvelopeProcessor(sample_rate=sr)
            return self._ae_envelope_processors[key]

    def get_or_create_ae_peak_detector(self, device_id: str, channel_id: int) -> AEPeakDetector:
        with self._lock:
            key = self.channel_key(device_id, channel_id)
            if key not in self._ae_peak_detectors:
                self._ae_peak_detectors[key] = AEPeakDetector(threshold_mode="relative", threshold_value=0.1)
            return self._ae_peak_detectors[key]

    def get_or_create_ae_param_extractor(self, device_id: str, channel_id: int) -> AEParameterExtractor:
        with self._lock:
            key = self.channel_key(device_id, channel_id)
            if key not in self._ae_param_extractors:
                self._ae_param_extractors[key] = AEParameterExtractor()
            return self._ae_param_extractors[key]

    # ── 通道统计 ──────────────────────────────────────

    def get_channel_stats(self, device_id: str, channel_id: int) -> dict:
        key = self.channel_key(device_id, channel_id)
        with self._lock:
            buf = self._ring_buffers.get(key)
            return {
                "buffer_size": buf.size if buf else 0,
                "sample_rate": self._sample_rates.get(key, self._default_sample_rate),
                "fft_ready": key in self._fft_processors,
                "prpd_ready": key in self._prpd_processors,
                "prps_ready": key in self._prps_processors,
            }

    # ── 资源释放 ──────────────────────────────────────

    def release_channel(self, device_id: str, channel_id: int) -> None:
        key = self.channel_key(device_id, channel_id)
        with self._lock:
            self._fft_processors.pop(key, None)
            self._prpd_processors.pop(key, None)
            self._prps_processors.pop(key, None)
            self._ring_buffers.pop(key, None)
            self._peak_detectors.pop(key, None)
            self._sample_rates.pop(key, None)
            self._ae_envelope_processors.pop(key, None)
            self._ae_peak_detectors.pop(key, None)
            self._ae_param_extractors.pop(key, None)
            self._channel_coupling_types.pop(key, None)

    def release_device(self, device_id: str) -> None:
        prefix = f"{device_id}:"
        dicts = [
            self._fft_processors,
            self._prpd_processors,
            self._prps_processors,
            self._ring_buffers,
            self._peak_detectors,
            self._sample_rates,
            self._ae_envelope_processors,
            self._ae_peak_detectors,
            self._ae_param_extractors,
            self._channel_coupling_types,
        ]
        with self._lock:
            for dct in dicts:
                for key in list(dct.keys()):
                    if key.startswith(prefix):
                        del dct[key]

    def clear_all(self) -> None:
        with self._lock:
            self._fft_processors.clear()
            self._prpd_processors.clear()
            self._prps_processors.clear()
            self._ring_buffers.clear()
            self._peak_detectors.clear()
            self._sample_rates.clear()
            self._ae_envelope_processors.clear()
            self._ae_peak_detectors.clear()
            self._ae_param_extractors.clear()
            self._channel_coupling_types.clear()
