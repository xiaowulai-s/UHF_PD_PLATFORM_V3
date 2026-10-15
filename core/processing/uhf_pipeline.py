# -*- coding: utf-8 -*-
"""
UHF 波形处理管线 — UHFPipelineHandler

从 AcquisitionService 提取的 UHF 处理流程:
  波形 → 峰值检测 → FFT → PRPD → PRPS → 发布
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from core.communication.protocol_data_structures import WaveformData
from core.foundation.signal_bus import PDDataBus
from core.processing.processor_manager import ProcessorManager
from core.processing.publish_throttle import PublishThrottle

logger = logging.getLogger(__name__)


# ── 相位计算工具 (模块级函数) ────────────────────────


def detect_zero_crossings(samples: np.ndarray) -> np.ndarray:
    """检测过零点位置（负→正方向）"""
    signs = np.sign(samples)
    return np.where((signs[:-1] < 0) & (signs[1:] > 0))[0]


def position_to_phase(position: int, waveform_len: int, zero_crossings: np.ndarray, grid_freq: float = 50.0) -> float:
    """将波形位置映射到工频相位 (0-360°)

    Args:
        position: 波形中的索引位置
        waveform_len: 波形总长度
        zero_crossings: 过零点位置数组
        grid_freq: 工频 (Hz), 默认 50.0, 用于无过零点时的相位估计
    """
    if len(zero_crossings) < 2:
        return (position / max(waveform_len, 1)) * 360.0

    idx = np.searchsorted(zero_crossings, position)
    if idx == 0:
        cycle_start, cycle_end = 0, zero_crossings[0]
    elif idx >= len(zero_crossings):
        cycle_start, cycle_end = zero_crossings[-1], waveform_len
    else:
        cycle_start, cycle_end = zero_crossings[idx - 1], zero_crossings[idx]

    cycle_len = cycle_end - cycle_start
    if cycle_len <= 0:
        return 0.0

    return ((position - cycle_start) / cycle_len) * 360.0 % 360.0


# ── UHF 管线 ─────────────────────────────────────────


class UHFPipelineHandler:
    """
    UHF 波形处理管线

    处理流程:
    1. 提取样本, 存储采样率
    2. 存入 RingBuffer
    3. 峰值检测 → publish_pd_event
    4. FFT → publish_fft_result
    5. PRPD (add_detected_events + throttle)
    6. PRPS (phase binning + throttle)
    7. publish_waveform
    """

    def __init__(
        self, proc_mgr: ProcessorManager, data_bus: PDDataBus, throttle: PublishThrottle, grid_freq: float = 50.0
    ):
        self._proc_mgr = proc_mgr
        self._data_bus = data_bus
        self._throttle = throttle
        self._grid_freq = grid_freq

    def process(self, device_id: str, channel_id: int, wf_data: Any) -> dict:
        """
        处理 UHF 波形。

        Returns:
            {samples, sample_rate, n_peaks}
        """
        if not hasattr(wf_data, "samples"):
            logger.warning("[%s] UHF 管线收到无效波形数据: %s", device_id, type(wf_data).__name__)
            return {"samples": np.array([]), "sample_rate": 0, "n_peaks": 0}

        samples = np.array(wf_data.samples, dtype=np.float64)
        if len(samples) < 2:
            return {"samples": samples, "sample_rate": 0, "n_peaks": 0}

        sr = getattr(wf_data, "sample_rate", None) or self._proc_mgr._default_sample_rate
        self._proc_mgr.set_sample_rate(device_id, channel_id, sr)

        # 存入环形缓冲区
        buffer = self._proc_mgr.get_or_create_buffer(device_id, channel_id)
        buffer.extend(samples)

        # 峰值检测
        pd_events = []
        try:
            detector = self._proc_mgr.get_or_create_peak_detector(device_id, channel_id)
            pd_events = detector.detect_pd_events(samples)
            for peak in pd_events:
                self._data_bus.publish_pd_event(
                    device_id,
                    channel_id,
                    {
                        "sensor_type": "uhf",
                        "amplitude": peak.amplitude,
                        "position": peak.position,
                        "energy": peak.energy,
                        "width": peak.width,
                        "timestamp": time.time(),
                    },
                )
        except (ValueError, TypeError, AttributeError, RuntimeError) as e:
            logger.warning("[%s] 峰值检测异常: %s", device_id, e, exc_info=True)

        # FFT
        try:
            fft_proc = self._proc_mgr.get_or_create_fft(device_id, channel_id)
            fft_result = fft_proc.compute(samples, detect_peaks=True)
            self._data_bus.publish_fft_result(device_id, channel_id, fft_result, "uhf")
        except (ValueError, TypeError, RuntimeError, MemoryError) as e:
            logger.warning("[%s] FFT 异常: %s", device_id, e, exc_info=True)

        # PRPD
        key = ProcessorManager.channel_key(device_id, channel_id)
        try:
            prpd_proc = self._proc_mgr.get_or_create_prpd(device_id, channel_id)
            # 复用上面已经检测出的 pd_events，不再让 PRPD 用第二个检测器重测一遍：
            # 两遍检测的阈值与极性口径不同，图谱会和事件流/报警互相矛盾。
            prpd_proc.add_detected_events(pd_events, len(samples))
            max_amp = max(abs(p.amplitude) for p in pd_events) if pd_events else 0.0
            self._throttle.prpd_cycle(device_id, channel_id, key, prpd_proc, max_amp)
        except (ValueError, TypeError, RuntimeError, MemoryError) as e:
            logger.warning("[%s] PRPD 异常: %s", device_id, e, exc_info=True)

        # PRPS
        try:
            prps_proc = self._proc_mgr.get_or_create_prps(device_id, channel_id)
            phase_binned = np.zeros(360)
            zero_crossings = detect_zero_crossings(samples)
            for peak in pd_events[:360]:
                phase_deg = position_to_phase(peak.position, len(samples), zero_crossings, self._grid_freq)
                phase_binned[int(phase_deg) % 360] = max(phase_binned[int(phase_deg) % 360], abs(peak.amplitude))
            self._throttle.prps_cycle(device_id, channel_id, key, prps_proc, phase_binned)
        except (ValueError, TypeError, RuntimeError, MemoryError, IndexError) as e:
            logger.warning("[%s] PRPS 异常: %s", device_id, e, exc_info=True)

        # 发布波形。
        #
        # 必须把 samples 与 sample_rate 一起包成 WaveformData，不能发裸 ndarray：
        # `payloads.build_waveform` 用 `getattr(obj, "sample_rate", 0)` 取时间基，
        # 裸数组恒取到 0，而前端 waveform-chart 又把 0 兜底成 100 MHz。对 UHF 恰好
        # 蒙对，对任何非 100MHz 的采集（以及 AE 的 2MHz）就是一条静默的错刻度——
        # 图照样画得出来，只是横轴差几十倍。AE 通路已在 M5.1 按同一理由修过。
        #
        # 换负载类型不破坏回滚路径：旧 UI 的 `signal_router._on_waveform_for_ui`
        # 写的是 `waveform.samples if hasattr(waveform, "samples") else waveform`，
        # 两种形状都吃；`_on_waveform_for_storage` 只读统计计数、不碰负载。
        self._data_bus.publish_waveform(
            device_id,
            channel_id,
            WaveformData(samples=samples, sample_rate=int(sr)),
            "uhf",
        )

        return {"samples": samples, "sample_rate": sr, "n_peaks": len(pd_events)}
