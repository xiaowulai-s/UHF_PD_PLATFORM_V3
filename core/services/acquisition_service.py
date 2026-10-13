# -*- coding: utf-8 -*-
"""
PD 数据采集服务 — AcquisitionService (薄协调器)

职责: 路由数据帧到 UHF/AE 管线，管理生命周期和统计。

原 832 行 God Class 已拆分为:
- ProcessorManager: 处理器工厂
- UHFPipelineHandler: UHF 处理管线
- AEPipelineHandler: AE 处理管线
- AEAlarmChecker: AE 报警检测
- PublishThrottle: PRPD/PRPS 发布节流
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from core.foundation.signal_bus import PDDataBus
from core.processing.ae_pipeline import AEAlarmChecker, AEPipelineHandler
from core.processing.processor_manager import ProcessorManager
from core.processing.publish_throttle import PublishThrottle
from core.processing.uhf_pipeline import UHFPipelineHandler

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
    数据采集服务 — 协调器

    路由数据帧到 UHF 或 AE 处理管线，管理生命周期和统计。
    保持与原有外部 API 完全兼容。
    """

    def __init__(
        self,
        sample_rate: int = 100_000_000,
        waveform_points: int = 4096,
    ):
        self._default_sample_rate = sample_rate

        # 核心组件
        self._proc_mgr = ProcessorManager(sample_rate, waveform_points)
        self._data_bus = PDDataBus.instance()
        self._throttle = PublishThrottle(self._data_bus)
        self._alarm_checker = AEAlarmChecker(self._data_bus)
        self._uhf_handler = UHFPipelineHandler(self._proc_mgr, self._data_bus, self._throttle)
        self._ae_handler = AEPipelineHandler(self._proc_mgr, self._data_bus, self._throttle, self._alarm_checker)

        # 协议解析器
        self._protocol: Any = None

        # 数据回调
        self._on_waveform_cb: Optional[Callable] = None
        self._on_pd_event_cb: Optional[Callable] = None

        # 采集状态
        self._is_running = False
        self._lock = threading.RLock()

        # 统计
        self._stats = {
            "frames_received": 0,
            "waveforms_processed": 0,
            "pd_events_detected": 0,
            "fft_computed": 0,
            "errors": 0,
            "start_time": 0.0,
        }

    # ── 属性 (向后兼容) ───────────────────────────────

    @property
    def _observed_max_amp(self) -> dict:
        return self._throttle._observed_max_amp

    def get_observed_max_amp(self) -> dict:
        """获取各通道观测最大幅值"""
        return dict(self._throttle._observed_max_amp)

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ── 委托: 处理器访问 (保持外部 API) ──────────────

    def _channel_key(self, device_id: str, channel_id: int) -> str:
        return ProcessorManager.channel_key(device_id, channel_id)

    def get_or_create_fft(self, device_id: str, channel_id: int):
        return self._proc_mgr.get_or_create_fft(device_id, channel_id)

    def get_or_create_prpd(self, device_id: str, channel_id: int):
        return self._proc_mgr.get_or_create_prpd(device_id, channel_id)

    def get_or_create_prps(self, device_id: str, channel_id: int):
        return self._proc_mgr.get_or_create_prps(device_id, channel_id)

    def get_or_create_buffer(self, device_id: str, channel_id: int):
        return self._proc_mgr.get_or_create_buffer(device_id, channel_id)

    def get_or_create_peak_detector(self, device_id: str, channel_id: int):
        return self._proc_mgr.get_or_create_peak_detector(device_id, channel_id)

    def get_or_create_ae_envelope(self, device_id: str, channel_id: int):
        return self._proc_mgr.get_or_create_ae_envelope(device_id, channel_id)

    def get_or_create_ae_peak_detector(self, device_id: str, channel_id: int):
        return self._proc_mgr.get_or_create_ae_peak_detector(device_id, channel_id)

    def get_or_create_ae_param_extractor(self, device_id: str, channel_id: int):
        return self._proc_mgr.get_or_create_ae_param_extractor(device_id, channel_id)

    # ── 委托: 通道配置 ───────────────────────────────

    def set_protocol(self, protocol: Any) -> None:
        self._protocol = protocol

    def set_channel_coupling_type(self, device_id: str, channel_id: int, coupling_type: str) -> None:
        self._proc_mgr.set_channel_coupling_type(device_id, channel_id, coupling_type)

    def set_ae_alarm_thresholds(self, **kwargs) -> None:
        self._alarm_checker.set_thresholds(**kwargs)

    # ── 生命周期 ──────────────────────────────────────

    def start(self) -> bool:
        with self._lock:
            self._is_running = True
            self._stats["start_time"] = time.time()
            logger.info("采集服务已启动")
            return True

    def stop(self) -> None:
        with self._lock:
            self._is_running = False
            self._proc_mgr.clear_all()
            self._throttle.clear()
            self._alarm_checker.clear()
        logger.info("采集服务已停止")

    # ── 数据处理路由 ──────────────────────────────────

    def process_data_frame(self, device_id: str, channel_id: int, data: Any) -> AcquisitionResult:
        start = time.time()
        result = AcquisitionResult(device_id, channel_id)

        try:
            data_type = type(data).__name__

            if "WaveformData" in data_type:
                is_ae_type = "AEWaveformData" in data_type
                coupling_type = self._proc_mgr.get_channel_coupling_type(device_id, channel_id)

                if is_ae_type or coupling_type == "ae":
                    result.frame_type = "ae_waveform"
                    info = self._ae_handler.process(device_id, channel_id, data)
                    self._inc_stat("waveforms_processed")
                else:
                    result.frame_type = "waveform"
                    info = self._uhf_handler.process(device_id, channel_id, data)
                    self._inc_stat("waveforms_processed")

                result.success = True
                result.data = info
                self._inc_stat("pd_events_detected", info.get("n_peaks", info.get("n_hits", 0)))

            elif "FFTData" in data_type:
                result.frame_type = "fft"
                result.success = True
                result.data = data
                self._data_bus.publish_fft_result(device_id, channel_id, data, "uhf")
                self._inc_stat("fft_computed")

            elif isinstance(data, dict) and "phase" in data:
                result.frame_type = "pd_event"
                result.success = True
                result.data = data
                prpd = self._proc_mgr.get_or_create_prpd(device_id, channel_id)
                prpd.add_event(
                    phase=data["phase"],
                    amplitude=data["amplitude"],
                    polarity=data.get("polarity", 0),
                    energy=data.get("energy", 0.0),
                )
                self._data_bus.publish_pd_event(device_id, channel_id, data)
                self._inc_stat("pd_events_detected")

            else:
                result.success = True
                result.frame_type = data_type
                result.data = data

            result.duration_ms = (time.time() - start) * 1000
            self._inc_stat("frames_received")

        except (KeyError, ValueError, TypeError, AttributeError) as e:
            result.success = False
            result.error_message = str(e)
            self._inc_stat("errors")
            logger.error("[%s] 数据处理失败: %s", device_id, e, exc_info=True)

        return result

    # ── 回调 ──────────────────────────────────────────

    def set_on_waveform_callback(self, cb: Optional[Callable]) -> None:
        self._on_waveform_cb = cb

    def set_on_pd_event_callback(self, cb: Optional[Callable]) -> None:
        self._on_pd_event_cb = cb

    # ── PRPD 显示模式 ─────────────────────────────────

    def set_prpd_mode(self, mode: str) -> str:
        """切换 PRPD 发布模式（heatmap / density / scatter），返回实际生效值。

        公开委托而不是让调用方碰 `_throttle`：密度图与热力图共用同一份计数矩阵，
        差别在内核 compute 时才引入，前端切不动它。
        """
        return self._throttle.set_prpd_mode(mode)

    def get_prpd_mode(self) -> str:
        return self._throttle.prpd_mode

    # ── 统计 ──────────────────────────────────────────

    def get_stats(self) -> dict:
        with self._lock:
            s = dict(self._stats)
        if s["start_time"] > 0:
            s["uptime_seconds"] = time.time() - s["start_time"]
        return s

    def get_channel_stats(self, device_id: str, channel_id: int) -> dict:
        return self._proc_mgr.get_channel_stats(device_id, channel_id)

    def reset_stats(self) -> None:
        with self._lock:
            for k in self._stats:
                if k != "start_time":
                    self._stats[k] = 0

    def _inc_stat(self, key: str, delta: int = 1) -> None:
        with self._lock:
            self._stats[key] = self._stats.get(key, 0) + delta

    # ── 资源管理 ──────────────────────────────────────

    def release_channel(self, device_id: str, channel_id: int) -> None:
        self._proc_mgr.release_channel(device_id, channel_id)

    def release_device(self, device_id: str) -> None:
        self._proc_mgr.release_device(device_id)
