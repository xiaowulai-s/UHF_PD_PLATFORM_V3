# -*- coding: utf-8 -*-
"""
PD 数据采集服务 - AcquisitionService

管理 FPGA 采集主机的数据采集流程。

Fixes applied (P0/P1):
- Thread-safe get_or_create_* (RLock)
- Per-channel sample rate storage
- Processor cleanup on device stop
- PRPD matrix exponential decay
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from core.foundation.signal_bus import PDDataBus
from core.processing import (
    AEEnvelopeProcessor,
    AEParameterExtractor,
    AEPeakDetector,
    FFTProcessor,
    PeakDetector,
    PRPDProcessor,
    PRPSProcessor,
    RingBuffer,
)

logger = logging.getLogger(__name__)


class AcquisitionResult:
    """单次采集结果"""

    __slots__ = (
        "device_id",
        "channel_id",
        "success",
        "timestamp",
        "duration_ms",
        "frame_type",
        "data",
        "error_message",
    )

    def __init__(self, device_id: str, channel_id: int = 0):
        self.device_id = device_id
        self.channel_id = channel_id
        self.success = False
        self.timestamp = datetime.now()
        self.duration_ms = 0.0
        self.frame_type: Optional[str] = None
        self.data: Any = None
        self.error_message: str = ""

    def __repr__(self):
        status = "OK" if self.success else f"FAIL({self.error_message})"
        return f"AcquisitionResult({self.device_id}, ch{self.channel_id}, {self.frame_type}, {status})"


class AcquisitionService:
    """
    数据采集服务

    管理从 FPGA 采集主机接收数据的完整流程。
    线程安全，支持多设备多通道并发。

    Args:
        sample_rate: 默认采样率 (Hz)
        waveform_points: 波形点数
    """

    PRPD_DECAY_FACTOR = 0.995  # PRPD 矩阵指数衰减系数
    PRPD_DECAY_INTERVAL = 20    # 衰减帧间隔
    PRPD_PUBLISH_INTERVAL = 50  # PRPD 发布帧间隔

    def __init__(
        self,
        sample_rate: int = 100_000_000,
        waveform_points: int = 4096,
    ):
        self._default_sample_rate = sample_rate
        self._waveform_points = waveform_points

        # 协议解析器
        self._protocol: Any = None

        # 信号处理器（每个通道一个实例）
        self._fft_processors: Dict[str, FFTProcessor] = {}
        self._prpd_processors: Dict[str, PRPDProcessor] = {}
        self._prps_processors: Dict[str, PRPSProcessor] = {}
        self._ring_buffers: Dict[str, RingBuffer] = {}
        self._peak_detectors: Dict[str, PeakDetector] = {}
        self._sample_rates: Dict[str, int] = {}  # 按通道存储采样率

        # AE 处理器（每个通道一个实例）
        self._ae_envelope_processors: Dict[str, AEEnvelopeProcessor] = {}
        self._ae_peak_detectors: Dict[str, AEPeakDetector] = {}
        self._ae_param_extractors: Dict[str, AEParameterExtractor] = {}
        self._channel_coupling_types: Dict[str, str] = {}  # "dev:ch" -> "uhf" / "ae"

        # AE 报警状态
        self._ae_signal_loss_counters: Dict[str, int] = {}  # 连续无 hit 帧计数
        self._ae_noise_counters: Dict[str, int] = {}  # 噪声超标连续帧计数
        self._ae_alarm_state: Dict[str, set] = {}  # "dev:ch" -> {"signal_loss", "noise_floor"}
        self._AE_SIGNAL_LOSS_THRESHOLD = 20  # 连续 20 帧无 hit → 信号丢失报警
        self._AE_NOISE_THRESHOLD = 10.0  # RMS > 10mV → 噪声超标
        self._AE_NOISE_CLEAR = 5.0  # RMS < 5mV → 清除噪声报警
        self._AE_NOISE_FRAME_THRESHOLD = 10  # 持续 10 帧 → 触发/清除

        # 数据回调
        self._on_waveform_cb: Optional[Callable] = None
        self._on_pd_event_cb: Optional[Callable] = None

        # 采集状态
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # PRPD 帧计数（用于衰减），按通道独立
        self._prpd_frame_count: Dict[str, int] = {}  # PRPD 发布帧计数（每50帧发布一次）
        self._prpd_decay_count: Dict[str, int] = {}  # PRPD 衰减帧计数（每20帧衰减一次）
        self._observed_max_amp: Dict[str, float] = {}  # 每通道观测到的最大幅值，用于动态调整 max_amplitude

        # PRPS 发布节流（每N帧发布一次）
        self._prps_frame_count: Dict[str, int] = {}
        self._prps_publish_interval = 10  # PRPS 每10帧发布一次

        # 统计
        self._stats = {
            "frames_received": 0,
            "waveforms_processed": 0,
            "pd_events_detected": 0,
            "fft_computed": 0,
            "errors": 0,
            "start_time": 0.0,
        }

        self._data_bus = PDDataBus.instance()

    # ── 连接管理 ─────────────────────────────────────

    def set_protocol(self, protocol: Any) -> None:
        self._protocol = protocol

    def set_channel_coupling_type(self, device_id: str, channel_id: int, coupling_type: str) -> None:
        """
        设置通道耦合类型，决定数据处理管线

        Args:
            device_id: 设备 ID
            channel_id: 通道 ID
            coupling_type: "uhf" 或 "ae"
        """
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if coupling_type in ("uhf", "ae"):
                self._channel_coupling_types[key] = coupling_type
                logger.debug("通道 %s 耦合类型: %s", key, coupling_type)

    def start(self) -> bool:
        with self._lock:
            self._is_running = True
            self._stats["start_time"] = time.time()
            logger.info("采集服务已启动")
            return True

    def stop(self) -> None:
        with self._lock:
            self._is_running = False
            # 清理所有处理器
            self._fft_processors.clear()
            self._prpd_processors.clear()
            self._prps_processors.clear()
            self._ring_buffers.clear()
            self._peak_detectors.clear()
            self._sample_rates.clear()
            self._prpd_frame_count.clear()
            self._prpd_decay_count.clear()
            self._observed_max_amp.clear()
            self._prps_frame_count.clear()
            self._ae_envelope_processors.clear()
            self._ae_peak_detectors.clear()
            self._ae_param_extractors.clear()
            self._channel_coupling_types.clear()
            self._ae_signal_loss_counters.clear()
            self._ae_noise_counters.clear()
            self._ae_alarm_state.clear()
        logger.info("采集服务已停止")

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ── 通道管理器（线程安全）─────────────────────────

    def _channel_key(self, device_id: str, channel_id: int) -> str:
        return f"{device_id}:{channel_id}"

    def get_or_create_fft(self, device_id: str, channel_id: int) -> FFTProcessor:
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if key not in self._fft_processors:
                sr = self._sample_rates.get(key, self._default_sample_rate)
                self._fft_processors[key] = FFTProcessor(
                    sample_rate=sr,
                    window_size=min(self._waveform_points, 4096),
                )
            return self._fft_processors[key]

    def get_or_create_prpd(self, device_id: str, channel_id: int) -> PRPDProcessor:
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if key not in self._prpd_processors:
                # 初始 max_amplitude 设为 10000 mV (10V)，覆盖典型 PD 信号范围(kV级)
                # 运行中若检测到更大幅值会动态扩展并重建矩阵
                self._prpd_processors[key] = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=10000.0)
            return self._prpd_processors[key]

    def get_or_create_prps(self, device_id: str, channel_id: int) -> PRPSProcessor:
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if key not in self._prps_processors:
                self._prps_processors[key] = PRPSProcessor(rows=512, cols=360)
            return self._prps_processors[key]

    def get_or_create_buffer(self, device_id: str, channel_id: int) -> RingBuffer:
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if key not in self._ring_buffers:
                self._ring_buffers[key] = RingBuffer(maxlen=1_000_000)
            return self._ring_buffers[key]

    def get_or_create_peak_detector(self, device_id: str, channel_id: int) -> PeakDetector:
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if key not in self._peak_detectors:
                self._peak_detectors[key] = PeakDetector(adaptive=True)
            return self._peak_detectors[key]

    def get_or_create_ae_envelope(self, device_id: str, channel_id: int) -> AEEnvelopeProcessor:
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if key not in self._ae_envelope_processors:
                sr = self._sample_rates.get(key, 2_000_000)
                self._ae_envelope_processors[key] = AEEnvelopeProcessor(sample_rate=sr)
            return self._ae_envelope_processors[key]

    def get_or_create_ae_peak_detector(self, device_id: str, channel_id: int) -> AEPeakDetector:
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if key not in self._ae_peak_detectors:
                self._ae_peak_detectors[key] = AEPeakDetector(
                    threshold_mode="relative", threshold_value=0.1
                )
            return self._ae_peak_detectors[key]

    def get_or_create_ae_param_extractor(self, device_id: str, channel_id: int) -> AEParameterExtractor:
        with self._lock:
            key = self._channel_key(device_id, channel_id)
            if key not in self._ae_param_extractors:
                self._ae_param_extractors[key] = AEParameterExtractor()
            return self._ae_param_extractors[key]

    # ── 数据处理 Pipeline ────────────────────────────

    def process_data_frame(self, device_id: str, channel_id: int, data: Any) -> AcquisitionResult:
        start = time.time()
        result = AcquisitionResult(device_id, channel_id)

        try:
            data_type = type(data).__name__

            if "WaveformData" in data_type:
                # 检测是否为 AE 波形数据或通道
                is_ae_type = "AEWaveformData" in data_type
                key = self._channel_key(device_id, channel_id)
                coupling_type = self._channel_coupling_types.get(key, "uhf")
                if is_ae_type or coupling_type == "ae":
                    result = self._process_ae_waveform(device_id, channel_id, data)
                else:
                    result = self._process_uhf_waveform(device_id, channel_id, data)
            elif "FFTData" in data_type:
                result = self._process_fft(device_id, channel_id, data)
            elif isinstance(data, dict) and "phase" in data:
                result = self._process_pd_event(device_id, channel_id, data)
            else:
                result.success = True
                result.frame_type = data_type
                result.data = data

            result.duration_ms = (time.time() - start) * 1000
            self._stats["frames_received"] += 1

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            self._stats["errors"] += 1
            logger.error("[%s] 数据处理失败: %s", device_id, e)

        return result

    def _process_uhf_waveform(self, device_id: str, channel_id: int, wf_data: Any) -> AcquisitionResult:
        result = AcquisitionResult(device_id, channel_id)
        result.frame_type = "waveform"

        samples = np.array(getattr(wf_data, "samples", []), dtype=np.float64)
        if len(samples) < 2:
            result.error_message = "波形数据不足"
            return result

        # 按通道独立存储采样率
        sr = getattr(wf_data, "sample_rate", self._default_sample_rate) or self._default_sample_rate
        key = self._channel_key(device_id, channel_id)
        with self._lock:
            self._sample_rates[key] = sr

        # 存入环形缓冲区
        buffer = self.get_or_create_buffer(device_id, channel_id)
        buffer.extend(samples.tolist())

        # 峰值检测
        detector = self.get_or_create_peak_detector(device_id, channel_id)
        pd_events = []
        try:
            pd_events = detector.detect_pd_events(samples)
            for peak in pd_events:
                self._data_bus.publish_pd_event(
                    device_id,
                    channel_id,
                    {
                        "amplitude": peak.amplitude,
                        "position": peak.position,
                        "energy": peak.energy,
                        "width": peak.width,
                        "timestamp": time.time(),
                    },
                )
                self._stats["pd_events_detected"] += 1
        except Exception as e:
            logger.warning("[%s] 峰值检测异常: %s", device_id, e)

        # FFT 计算
        try:
            fft_proc = self.get_or_create_fft(device_id, channel_id)
            fft_result = fft_proc.compute(samples, detect_peaks=True)
            self._data_bus.publish_fft_result(device_id, channel_id, fft_result, "uhf")
            self._stats["fft_computed"] += 1
        except Exception as e:
            logger.warning("[%s] FFT 异常: %s", device_id, e)

        # PRPD 更新 + 指数衰减 + 动态幅值范围
        try:
            prpd_proc = self.get_or_create_prpd(device_id, channel_id)
            prpd_proc.add_waveform(samples, phase_offset=0)
            max_amp = max(abs(p.amplitude) for p in pd_events) if pd_events else 0.0
            self._prpd_publish_cycle(device_id, channel_id, key, prpd_proc, max_amp)
        except Exception as e:
            logger.warning("[%s] PRPD 异常: %s", device_id, e)

        # PRPS 更新（每N帧发布一次，避免UI过载）
        try:
            prps_proc = self.get_or_create_prps(device_id, channel_id)
            phase_binned = np.zeros(360)
            for peak in pd_events[:360]:
                # TODO: 相位映射假设波形恰好一个工频周期，需过零检测
                phase_deg = (peak.position / max(len(samples), 1)) * 360.0
                phase_binned[int(phase_deg) % 360] = max(phase_binned[int(phase_deg) % 360], abs(peak.amplitude))
            self._prps_publish_cycle(device_id, channel_id, key, prps_proc, phase_binned)
        except Exception as e:
            logger.warning("[%s] PRPS 异常: %s", device_id, e)

        # 发布波形到 UI
        self._data_bus.publish_waveform(device_id, channel_id, samples, "uhf")

        result.success = True
        result.data = {
            "samples": samples,
            "sample_rate": sr,
            "n_peaks": len(pd_events),
        }
        return result

    def _process_ae_waveform(self, device_id: str, channel_id: int, wf_data: Any) -> AcquisitionResult:
        """
        AE 波形处理流水线

        与 UHF 不同的处理路径:
        1. 带通滤波 (20-200kHz)
        2. Hilbert 包络提取
        3. 包络 hit 检测
        4. AE 参数提取
        5. PRPD / PRPS / FFT (复用)
        6. 发布包络波形到 UI
        """
        result = AcquisitionResult(device_id, channel_id)
        result.frame_type = "ae_waveform"
        start_ts = time.time()

        # 提取样本
        samples = np.array(getattr(wf_data, "samples", []), dtype=np.float64)
        if len(samples) < 2:
            result.error_message = "AE 波形数据不足"
            return result

        # 采样率
        sr = getattr(wf_data, "sample_rate", 2_000_000) or 2_000_000
        key = self._channel_key(device_id, channel_id)
        with self._lock:
            self._sample_rates[key] = sr

        # 存入环形缓冲区
        buffer = self.get_or_create_buffer(device_id, channel_id)
        buffer.extend(samples.tolist())

        # 带通滤波
        env_proc = self.get_or_create_ae_envelope(device_id, channel_id)
        if sr != env_proc.sample_rate:
            env_proc.sample_rate = sr
        filtered = env_proc.bandpass_filter(samples)

        # 包络提取
        envelope = env_proc.compute_envelope(filtered, method="hilbert")

        # 发布包络到 UI
        self._data_bus.publish_ae_envelope(device_id, channel_id, envelope)

        # Hit 检测
        ae_detector = self.get_or_create_ae_peak_detector(device_id, channel_id)
        hits = ae_detector.detect_hits(envelope, sr)

        # 参数提取 + 发布
        param_ext = self.get_or_create_ae_param_extractor(device_id, channel_id)
        for hit in hits:
            try:
                features = param_ext.extract(samples, envelope, hit, sr)

                # 发布 PD 事件 (复用告警/存储链路)
                self._data_bus.publish_pd_event(
                    device_id,
                    channel_id,
                    {
                        "amplitude": hit.peak_amplitude,
                        "phase": features.get("phase_deg", 0),
                        "energy": hit.marse_energy,
                        "polarity": features.get("polarity", 0),
                        "rise_time_us": hit.rise_time_us,
                        "duration_us": hit.duration_us,
                        "counts": hit.counts,
                        "marse_energy": hit.marse_energy,
                        "avg_frequency_khz": hit.avg_frequency_khz,
                        "frequency_mhz": hit.avg_frequency_khz / 1000,
                        "signal_quality": features.get("signal_quality", 50),
                        "timestamp": time.time(),
                    },
                )

                # 发布 AE hit (专用于 UI AE 参数面板)
                self._data_bus.publish_ae_hit(device_id, channel_id, features)

                # PRPD 更新
                prpd = self.get_or_create_prpd(device_id, channel_id)
                prpd.add_event(
                    phase=hit.phase_deg,
                    amplitude=hit.peak_amplitude,
                    polarity=hit.polarity,
                    energy=hit.marse_energy,
                )
                self._stats["pd_events_detected"] += 1
            except Exception as e:
                logger.warning("[%s] AE hit 处理异常: %s", device_id, e)

        # FFT 计算 (AE 频段)
        try:
            fft_proc = self.get_or_create_fft(device_id, channel_id)
            fft_result = fft_proc.compute(samples, detect_peaks=True)
            # 覆盖频段统计为 AE 频段
            fft_result.band_stats = fft_proc.compute_band_stats(
                fft_result.frequencies, fft_result.magnitudes, bands=FFTProcessor.AE_BANDS
            )
            self._data_bus.publish_fft_result(device_id, channel_id, fft_result, "ae")
            self._stats["fft_computed"] += 1
        except Exception as e:
            logger.warning("[%s] AE FFT 异常: %s", device_id, e)

        # PRPD 定期发布（复用 UHF 管线）
        try:
            prpd_proc = self.get_or_create_prpd(device_id, channel_id)
            max_amp = max(h.peak_amplitude for h in hits) if hits else 0.0
            self._prpd_publish_cycle(device_id, channel_id, key, prpd_proc, max_amp)
        except Exception as e:
            logger.warning("[%s] AE PRPD 异常: %s", device_id, e)

        # PRPS 更新（复用 UHF 管线）
        try:
            prps_proc = self.get_or_create_prps(device_id, channel_id)
            phase_binned = np.zeros(360)
            for hit in hits[:360]:
                phase_idx = int(hit.phase_deg) % 360
                phase_binned[phase_idx] = max(phase_binned[phase_idx], hit.peak_amplitude)
            self._prps_publish_cycle(device_id, channel_id, key, prps_proc, phase_binned)
        except Exception as e:
            logger.warning("[%s] AE PRPS 异常: %s", device_id, e)

        # ── AE 报警检测 ────────────────────────────
        try:
            self._check_ae_alarms(device_id, channel_id, key, hits, filtered, env_proc)
        except Exception as e:
            logger.warning("[%s] AE 报警检测异常: %s", device_id, e)

        # 发布包络波形到波形控件
        self._data_bus.publish_waveform(device_id, channel_id, envelope, "ae")

        self._stats["waveforms_processed"] += 1
        result.success = True
        result.duration_ms = (time.time() - start_ts) * 1000
        result.data = {
            "samples": envelope,
            "sample_rate": sr,
            "n_hits": len(hits),
        }
        return result

    def _check_ae_alarms(
        self,
        device_id: str,
        channel_id: int,
        key: str,
        hits: list,
        filtered: np.ndarray,
        env_proc: AEEnvelopeProcessor,
    ) -> None:
        """
        AE 通道报警检测（信号丢失 + 噪声超标）

        - ae_signal_loss: 连续 N 帧无 hit → warning
        - ae_noise_floor: 噪声 RMS 持续超标 → warning
        """
        if key not in self._ae_alarm_state:
            self._ae_alarm_state[key] = set()
        if key not in self._ae_signal_loss_counters:
            self._ae_signal_loss_counters[key] = 0
        if key not in self._ae_noise_counters:
            self._ae_noise_counters[key] = 0

        active_alarms = self._ae_alarm_state[key]
        signal_loss_count = self._ae_signal_loss_counters[key]
        noise_count = self._ae_noise_counters[key]

        noise_rms = env_proc.compute_rms(filtered)

        # ── 信号丢失检测 ──────────────────────────
        if len(hits) == 0:
            signal_loss_count += 1
        else:
            signal_loss_count = 0
            # 恢复信号 → 清除信号丢失报警
            if "signal_loss" in active_alarms:
                active_alarms.discard("signal_loss")
                self._data_bus.publish_alarm_cleared(
                    device_id, "ae_signal_loss", "AE 信号已恢复"
                )
                logger.info("[AE][%s] 信号恢复，清除信号丢失报警", key)

        if "signal_loss" not in active_alarms and signal_loss_count >= self._AE_SIGNAL_LOSS_THRESHOLD:
            active_alarms.add("signal_loss")
            self._data_bus.publish_alarm(
                device_id,
                {
                    "device_id": device_id,
                    "channel_id": channel_id,
                    "alarm_type": "ae_signal_loss",
                    "level": "warning",
                    "amplitude": 0,
                    "threshold": 0,
                    "description": "AE 信号丢失 - 传感器可能脱落或损坏",
                    "timestamp": time.time(),
                },
            )
            logger.warning("[AE][%s] 信号丢失报警触发 (%d 帧无 hit)", key, signal_loss_count)

        self._ae_signal_loss_counters[key] = signal_loss_count

        # ── 噪声超标检测 ──────────────────────────
        if noise_rms > self._AE_NOISE_THRESHOLD:
            noise_count += 1
        else:
            noise_count = max(0, noise_count - 1)

        if "noise_floor" not in active_alarms and noise_count >= self._AE_NOISE_FRAME_THRESHOLD:
            active_alarms.add("noise_floor")
            self._data_bus.publish_alarm(
                device_id,
                {
                    "device_id": device_id,
                    "channel_id": channel_id,
                    "alarm_type": "ae_noise_floor",
                    "level": "warning",
                    "amplitude": noise_rms,
                    "threshold": self._AE_NOISE_THRESHOLD,
                    "description": f"AE 噪声超限 - RMS {noise_rms:.1f}mV",
                    "timestamp": time.time(),
                },
            )
            logger.warning("[AE][%s] 噪声超标报警触发: RMS=%.1f mV", key, noise_rms)

        # 噪声回落 → 清除噪声报警
        if "noise_floor" in active_alarms and noise_rms < self._AE_NOISE_CLEAR:
            active_alarms.discard("noise_floor")
            self._data_bus.publish_alarm_cleared(
                device_id, "ae_noise_floor", f"AE 噪声已回落至 {noise_rms:.1f}mV"
            )
            logger.info("[AE][%s] 噪声回落，清除噪声报警 (RMS=%.1f mV)", key, noise_rms)

        self._ae_noise_counters[key] = noise_count

    def _process_fft(self, device_id: str, channel_id: int, fft_data: Any) -> AcquisitionResult:
        result = AcquisitionResult(device_id, channel_id)
        result.frame_type = "fft"
        result.success = True
        result.data = fft_data
        self._data_bus.publish_fft_result(device_id, channel_id, fft_data, "uhf")
        self._stats["fft_computed"] += 1
        return result

    def _process_pd_event(self, device_id: str, channel_id: int, event: dict) -> AcquisitionResult:
        result = AcquisitionResult(device_id, channel_id)
        result.frame_type = "pd_event"
        result.success = True
        result.data = event

        if "phase" in event and "amplitude" in event:
            prpd = self.get_or_create_prpd(device_id, channel_id)
            prpd.add_event(
                phase=event["phase"],
                amplitude=event["amplitude"],
                polarity=event.get("polarity", 0),
                energy=event.get("energy", 0.0),
            )

        self._data_bus.publish_pd_event(device_id, channel_id, event)
        self._stats["pd_events_detected"] += 1
        return result

    # ── 回调 ─────────────────────────────────────────

    def set_on_waveform_callback(self, cb: Optional[Callable]) -> None:
        self._on_waveform_cb = cb

    def set_on_pd_event_callback(self, cb: Optional[Callable]) -> None:
        self._on_pd_event_cb = cb

    # ── 统计 ─────────────────────────────────────────

    def get_stats(self) -> dict:
        s = dict(self._stats)
        if s["start_time"] > 0:
            s["uptime_seconds"] = time.time() - s["start_time"]
        return s

    def get_channel_stats(self, device_id: str, channel_id: int) -> dict:
        key = self._channel_key(device_id, channel_id)
        buf = self._ring_buffers.get(key)
        return {
            "buffer_size": buf.size if buf else 0,
            "sample_rate": self._sample_rates.get(key, self._default_sample_rate),
            "fft_ready": key in self._fft_processors,
            "prpd_ready": key in self._prpd_processors,
            "prps_ready": key in self._prps_processors,
        }

    def reset_stats(self) -> None:
        for k in self._stats:
            if k != "start_time":
                self._stats[k] = 0

    # ── PRPD/PRPS 公共管线 (UHF/AE 共用) ────────────

    def _prpd_publish_cycle(self, device_id: str, channel_id: int, key: str, prpd_proc: Any, max_amplitude_val: float) -> None:
        """PRPD 衰减 + 动态幅值扩展 + 发布节流 (UHF/AE 共用)"""
        # 动态幅值扩展
        prev_max = self._observed_max_amp.get(key, 0.0)
        if max_amplitude_val > prev_max:
            self._observed_max_amp[key] = max_amplitude_val
            if max_amplitude_val > prpd_proc.max_amplitude * 0.8:
                prpd_proc.max_amplitude = max_amplitude_val * 1.2
                prpd_proc.rebuild_matrix()

        # 每 N 帧衰减
        if key not in self._prpd_decay_count:
            self._prpd_decay_count[key] = 0
        self._prpd_decay_count[key] += 1
        if self._prpd_decay_count[key] >= self.PRPD_DECAY_INTERVAL:
            self._prpd_decay_count[key] = 0
            prpd_proc.decay(self.PRPD_DECAY_FACTOR)

        # 每 N 帧发布
        if key not in self._prpd_frame_count:
            self._prpd_frame_count[key] = 0
        self._prpd_frame_count[key] += 1
        if self._prpd_frame_count[key] >= self.PRPD_PUBLISH_INTERVAL:
            self._prpd_frame_count[key] = 0
            self._data_bus.publish_prpd_result(device_id, channel_id, prpd_proc.compute())

    def _prps_publish_cycle(self, device_id: str, channel_id: int, key: str, prps_proc: Any, phase_binned: np.ndarray) -> None:
        """PRPS 节流发布 (UHF/AE 共用)"""
        prps_proc.add_cycle(phase_binned)
        if key not in self._prps_frame_count:
            self._prps_frame_count[key] = 0
        self._prps_frame_count[key] += 1
        if self._prps_frame_count[key] >= self._prps_publish_interval:
            self._prps_frame_count[key] = 0
            self._data_bus.publish_prps_result(device_id, channel_id, prps_proc.compute())

    # ═══════════════════════════════════════════════════

    def release_channel(self, device_id: str, channel_id: int) -> None:
        key = self._channel_key(device_id, channel_id)
        with self._lock:
            self._fft_processors.pop(key, None)
            self._prpd_processors.pop(key, None)
            self._prps_processors.pop(key, None)
            self._ring_buffers.pop(key, None)
            self._peak_detectors.pop(key, None)
            self._sample_rates.pop(key, None)
            self._prpd_frame_count.pop(key, None)
            self._prps_frame_count.pop(key, None)
            self._ae_envelope_processors.pop(key, None)
            self._ae_peak_detectors.pop(key, None)
            self._ae_param_extractors.pop(key, None)
            self._channel_coupling_types.pop(key, None)
            self._ae_signal_loss_counters.pop(key, None)
            self._ae_noise_counters.pop(key, None)
            self._ae_alarm_state.pop(key, None)

    def release_device(self, device_id: str) -> None:
        """释放设备所有资源"""
        prefix = f"{device_id}:"
        dicts = [
            self._fft_processors, self._prpd_processors, self._prps_processors,
            self._ring_buffers, self._peak_detectors, self._sample_rates,
            self._prpd_frame_count, self._prps_frame_count,
            self._ae_envelope_processors, self._ae_peak_detectors,
            self._ae_param_extractors, self._channel_coupling_types,
            self._ae_signal_loss_counters, self._ae_noise_counters,
            self._ae_alarm_state,
        ]
        with self._lock:
            for dct in dicts:
                for key in list(dct.keys()):
                    if key.startswith(prefix):
                        del dct[key]
