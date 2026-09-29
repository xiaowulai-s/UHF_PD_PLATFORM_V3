# -*- coding: utf-8 -*-
"""
AE 波形处理管线 — AEPipelineHandler + AEAlarmChecker

从 AcquisitionService 提取的 AE 处理流程:
  波形 → 带通滤波 → 包络 → Hit 检测 → 参数提取 → FFT/PRPD/PRPS → 报警 → 发布
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Set

import numpy as np

from core.foundation.signal_bus import PDDataBus
from core.processing.fft_processor import FFTProcessor
from core.processing.processor_manager import ProcessorManager
from core.processing.publish_throttle import PublishThrottle

logger = logging.getLogger(__name__)


class AEAlarmChecker:
    """
    AE 通道报警检测

    检测两种 AE 特有报警:
    - ae_signal_loss: 连续 N 帧无 hit → 传感器可能脱落
    - ae_noise_floor: 噪声 RMS 持续超标 → 环境噪声过大
    """

    def __init__(self, data_bus: PDDataBus):
        self._data_bus = data_bus
        self._signal_loss_counters: Dict[str, int] = {}
        self._noise_counters: Dict[str, int] = {}
        self._alarm_state: Dict[str, Set[str]] = {}

        # 默认阈值
        self.signal_loss_threshold = 20
        self.noise_threshold = 10.0
        self.noise_clear = 5.0
        self.noise_frame_threshold = 10

    def set_thresholds(
        self,
        signal_loss_frames: int = 20,
        noise_threshold_mv: float = 10.0,
        noise_clear_mv: float = 5.0,
        noise_frame_count: int = 10,
    ) -> None:
        self.signal_loss_threshold = max(1, signal_loss_frames)
        self.noise_threshold = max(0.1, noise_threshold_mv)
        self.noise_clear = max(0.1, noise_clear_mv)
        self.noise_frame_threshold = max(1, noise_frame_count)
        logger.info(
            "AE 报警阈值已更新: 丢失=%d帧, 噪声=%.1f/%.1f mV, 帧数=%d",
            self.signal_loss_threshold,
            self.noise_threshold,
            self.noise_clear,
            self.noise_frame_threshold,
        )

    def check(
        self,
        device_id: str,
        channel_id: int,
        key: str,
        hits: list,
        filtered: np.ndarray,
        env_proc: Any,
    ) -> None:
        """检测信号丢失和噪声超标，触发/清除报警"""
        if key not in self._alarm_state:
            self._alarm_state[key] = set()
        if key not in self._signal_loss_counters:
            self._signal_loss_counters[key] = 0
        if key not in self._noise_counters:
            self._noise_counters[key] = 0

        active_alarms = self._alarm_state[key]
        signal_loss_count = self._signal_loss_counters[key]
        noise_count = self._noise_counters[key]

        noise_rms = env_proc.compute_rms(filtered)

        # ── 信号丢失检测 ──
        if len(hits) == 0:
            signal_loss_count += 1
        else:
            signal_loss_count = 0
            if "signal_loss" in active_alarms:
                active_alarms.discard("signal_loss")
                self._data_bus.publish_alarm_cleared(device_id, "ae_signal_loss", "AE 信号已恢复")
                logger.info("[AE][%s] 信号恢复，清除信号丢失报警", key)

        if "signal_loss" not in active_alarms and signal_loss_count >= self.signal_loss_threshold:
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

        self._signal_loss_counters[key] = signal_loss_count

        # ── 噪声超标检测 ──
        if noise_rms > self.noise_threshold:
            noise_count += 1
        else:
            noise_count = max(0, noise_count - 1)

        if "noise_floor" not in active_alarms and noise_count >= self.noise_frame_threshold:
            active_alarms.add("noise_floor")
            self._data_bus.publish_alarm(
                device_id,
                {
                    "device_id": device_id,
                    "channel_id": channel_id,
                    "alarm_type": "ae_noise_floor",
                    "level": "warning",
                    "amplitude": noise_rms,
                    "threshold": self.noise_threshold,
                    "description": f"AE 噪声超限 - RMS {noise_rms:.1f}mV",
                    "timestamp": time.time(),
                },
            )
            logger.warning("[AE][%s] 噪声超标报警触发: RMS=%.1f mV", key, noise_rms)

        if "noise_floor" in active_alarms and noise_rms < self.noise_clear:
            active_alarms.discard("noise_floor")
            self._data_bus.publish_alarm_cleared(device_id, "ae_noise_floor", f"AE 噪声已回落至 {noise_rms:.1f}mV")
            logger.info("[AE][%s] 噪声回落，清除噪声报警 (RMS=%.1f mV)", key, noise_rms)

        self._noise_counters[key] = noise_count

    def clear(self) -> None:
        self._signal_loss_counters.clear()
        self._noise_counters.clear()
        self._alarm_state.clear()


class AEPipelineHandler:
    """
    AE 波形处理管线

    处理流程:
    1. 提取样本, 存储采样率
    2. 存入 RingBuffer
    3. 带通滤波 → 包络提取 → publish_ae_envelope
    4. Hit 检测 → 参数提取 → publish_pd_event + publish_ae_hit
    5. FFT (AE 频段) → publish_fft_result
    6. PRPD / PRPS (throttle)
    7. AE 报警检测
    8. publish_waveform
    """

    def __init__(
        self,
        proc_mgr: ProcessorManager,
        data_bus: PDDataBus,
        throttle: PublishThrottle,
        alarm_checker: AEAlarmChecker,
    ):
        self._proc_mgr = proc_mgr
        self._data_bus = data_bus
        self._throttle = throttle
        self._alarm_checker = alarm_checker

    def process(self, device_id: str, channel_id: int, wf_data: Any) -> dict:
        """
        处理 AE 波形。

        Returns:
            {samples (envelope), sample_rate, n_hits}
        """
        if not hasattr(wf_data, "samples"):
            logger.warning("[%s] AE 管线收到无效波形数据: %s", device_id, type(wf_data).__name__)
            return {"samples": np.array([]), "sample_rate": 0, "n_hits": 0}

        start_ts = time.time()

        samples = np.array(wf_data.samples, dtype=np.float64)
        if len(samples) < 2:
            return {"samples": samples, "sample_rate": 0, "n_hits": 0}

        sr = getattr(wf_data, "sample_rate", None) or 2_000_000
        key = ProcessorManager.channel_key(device_id, channel_id)
        self._proc_mgr.set_sample_rate(device_id, channel_id, sr)

        # 存入环形缓冲区
        buffer = self._proc_mgr.get_or_create_buffer(device_id, channel_id)
        buffer.extend(samples)

        # 带通滤波
        env_proc = self._proc_mgr.get_or_create_ae_envelope(device_id, channel_id)
        if sr != env_proc.sample_rate:
            env_proc.sample_rate = sr
        filtered = env_proc.bandpass_filter(samples)

        # 包络提取
        envelope = env_proc.compute_envelope(filtered, method="hilbert")
        self._data_bus.publish_ae_envelope(device_id, channel_id, envelope)

        # Hit 检测
        ae_detector = self._proc_mgr.get_or_create_ae_peak_detector(device_id, channel_id)
        hits = ae_detector.detect_hits(envelope, sr)

        # 参数提取 + 发布
        param_ext = self._proc_mgr.get_or_create_ae_param_extractor(device_id, channel_id)
        for hit in hits:
            try:
                features = param_ext.extract(samples, envelope, hit, sr)

                self._data_bus.publish_pd_event(
                    device_id,
                    channel_id,
                    {
                        "sensor_type": "ae",
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
                self._data_bus.publish_ae_hit(device_id, channel_id, features)

                # PRPD 更新
                prpd = self._proc_mgr.get_or_create_prpd(device_id, channel_id)
                prpd.add_event(
                    phase=hit.phase_deg,
                    amplitude=hit.peak_amplitude,
                    polarity=hit.polarity,
                    energy=hit.marse_energy,
                )
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                logger.warning("[%s] AE hit 处理异常: %s", device_id, e, exc_info=True)

        # FFT (AE 频段)
        try:
            fft_proc = self._proc_mgr.get_or_create_fft(device_id, channel_id)
            fft_result = fft_proc.compute(samples, detect_peaks=True)
            fft_result.band_stats = fft_proc.compute_band_stats(
                fft_result.frequencies, fft_result.magnitudes, bands=FFTProcessor.AE_BANDS
            )
            self._data_bus.publish_fft_result(device_id, channel_id, fft_result, "ae")
        except (ValueError, TypeError, RuntimeError, MemoryError) as e:
            logger.warning("[%s] AE FFT 异常: %s", device_id, e, exc_info=True)

        # PRPD 定期发布
        try:
            prpd_proc = self._proc_mgr.get_or_create_prpd(device_id, channel_id)
            max_amp = max(h.peak_amplitude for h in hits) if hits else 0.0
            self._throttle.prpd_cycle(device_id, channel_id, key, prpd_proc, max_amp)
        except (ValueError, TypeError, RuntimeError, MemoryError) as e:
            logger.warning("[%s] AE PRPD 异常: %s", device_id, e, exc_info=True)

        # PRPS 更新
        try:
            prps_proc = self._proc_mgr.get_or_create_prps(device_id, channel_id)
            phase_binned = np.zeros(360)
            for hit in hits[:360]:
                phase_idx = int(hit.phase_deg) % 360
                phase_binned[phase_idx] = max(phase_binned[phase_idx], hit.peak_amplitude)
            self._throttle.prps_cycle(device_id, channel_id, key, prps_proc, phase_binned)
        except (ValueError, TypeError, RuntimeError, MemoryError, IndexError) as e:
            logger.warning("[%s] AE PRPS 异常: %s", device_id, e, exc_info=True)

        # AE 报警检测
        try:
            self._alarm_checker.check(device_id, channel_id, key, hits, filtered, env_proc)
        except (ValueError, TypeError, AttributeError, RuntimeError) as e:
            logger.warning("[%s] AE 报警检测异常: %s", device_id, e, exc_info=True)

        # 发布包络波形
        self._data_bus.publish_waveform(device_id, channel_id, envelope, "ae")

        return {
            "samples": envelope,
            "sample_rate": sr,
            "n_hits": len(hits),
            "duration_ms": (time.time() - start_ts) * 1000,
        }
