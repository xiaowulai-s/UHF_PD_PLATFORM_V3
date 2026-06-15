# -*- coding: utf-8 -*-
"""
PD DataBus - 局放检测专用事件总线

扩展自 DataBus 的核心模式，增加 PD 专用信号类型。
采用组合方式包装 DataBus 单例，不修改原 DataBus 代码。

PD 专用事件:
- waveform_received: 波形数据到达
- pd_event_detected: 局放事件检测
- fft_result_ready: FFT 计算完成
- prpd_result_ready: PRPD 图谱更新
- prps_result_ready: PRPS 图谱更新
- pd_alarm_triggered: 局放报警触发
- acquisition_state_changed: 采集状态变化
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from core.foundation.data_bus import DataBus, SubscriptionManager

logger = logging.getLogger(__name__)


class PDDataBusSignals(QObject):
    """PD 专用信号定义

    信号签名: (device_id, channel_id, data) 或 (device_id, channel_id, sensor_type, data)
    sensor_type 为 "uhf"/"ae"/"hfct"/"tev"，用于路由数据到对应 UI 面板。
    """

    # 波形数据 (增加 sensor_type 参数)
    waveform_received = Signal(str, int, str, object)  # device_id, channel_id, sensor_type, waveform_data

    # 局放事件
    pd_event_detected = Signal(str, int, dict)  # device_id, channel_id, event_dict

    # FFT 结果 (增加 sensor_type 参数)
    fft_result_ready = Signal(str, int, str, object)  # device_id, channel_id, sensor_type, fft_result

    # PRPD 结果
    prpd_result_ready = Signal(str, int, object)  # device_id, channel_id, prpd_result

    # PRPS 结果
    prps_result_ready = Signal(str, int, object)  # device_id, channel_id, prps_result

    # 报警
    pd_alarm_triggered = Signal(str, dict)  # device_id, alarm_dict
    pd_alarm_cleared = Signal(str, str, str)  # device_id, alarm_type, description

    # 设备状态
    device_status_changed = Signal(str, int, dict)  # device_id, status, status_data

    # 采集控制
    acquisition_started = Signal(str)  # device_id
    acquisition_stopped = Signal(str)  # device_id
    acquisition_error = Signal(str, str)  # device_id, error_msg

    # AE 信号
    ae_hit_detected = Signal(str, int, object)  # device_id, channel_id, ae_hit_dict
    ae_envelope_ready = Signal(str, int, object)  # device_id, channel_id, envelope_array


class PDDataBus:
    """
    PD 事件总线（组合模式）

    包装 DataBus 单例 + 扩展 PD 专用信号。
    通过如下方式访问:
        PDDataBus.instance().waveform_received.connect(slot)
        PDDataBus.instance().publish_waveform(device_id, ch, data)
    """

    _instance = None
    _instance_lock = __import__("threading").Lock()

    def __init__(self):
        self._data_bus = DataBus.instance()
        self._signals = PDDataBusSignals()
        self._sub_mgr = SubscriptionManager()

    @classmethod
    def instance(cls) -> PDDataBus:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 信号访问 ─────────────────────────────────────

    @property
    def waveform_received(self) -> Signal:
        return self._signals.waveform_received

    @property
    def pd_event_detected(self) -> Signal:
        return self._signals.pd_event_detected

    @property
    def fft_result_ready(self) -> Signal:
        return self._signals.fft_result_ready

    @property
    def prpd_result_ready(self) -> Signal:
        return self._signals.prpd_result_ready

    @property
    def prps_result_ready(self) -> Signal:
        return self._signals.prps_result_ready

    @property
    def pd_alarm_triggered(self) -> Signal:
        return self._signals.pd_alarm_triggered

    @property
    def pd_alarm_cleared(self) -> Signal:
        return self._signals.pd_alarm_cleared

    @property
    def device_status_changed(self) -> Signal:
        return self._signals.device_status_changed

    @property
    def acquisition_started(self) -> Signal:
        return self._signals.acquisition_started

    @property
    def acquisition_stopped(self) -> Signal:
        return self._signals.acquisition_stopped

    @property
    def acquisition_error(self) -> Signal:
        return self._signals.acquisition_error

    @property
    def ae_hit_detected(self) -> Signal:
        return self._signals.ae_hit_detected

    @property
    def ae_envelope_ready(self) -> Signal:
        return self._signals.ae_envelope_ready

    # ── 原始 DataBus 代理 ────────────────────────────

    @property
    def raw_bus(self) -> DataBus:
        return self._data_bus

    # ── 发布方法 ─────────────────────────────────────

    def publish_waveform(self, device_id: str, channel_id: int, waveform_data: Any, sensor_type: str = "uhf") -> None:
        """发布波形数据"""
        self._signals.waveform_received.emit(device_id, channel_id, sensor_type, waveform_data)

    def publish_pd_event(self, device_id: str, channel_id: int, event_dict: dict) -> None:
        """发布局放事件"""
        self._signals.pd_event_detected.emit(device_id, channel_id, event_dict)

    def publish_fft_result(self, device_id: str, channel_id: int, fft_result: Any, sensor_type: str = "uhf") -> None:
        """发布 FFT 结果"""
        self._signals.fft_result_ready.emit(device_id, channel_id, sensor_type, fft_result)

    def publish_prpd_result(self, device_id: str, channel_id: int, prpd_result: Any) -> None:
        """发布 PRPD 结果"""
        self._signals.prpd_result_ready.emit(device_id, channel_id, prpd_result)

    def publish_prps_result(self, device_id: str, channel_id: int, prps_result: Any) -> None:
        """发布 PRPS 结果"""
        self._signals.prps_result_ready.emit(device_id, channel_id, prps_result)

    def publish_alarm(self, device_id: str, alarm_dict: dict) -> None:
        """发布报警"""
        self._signals.pd_alarm_triggered.emit(device_id, alarm_dict)
        # 同步发布到原始 DataBus 的通用报警信号
        self._data_bus.publish_alarm(
            device_id,
            alarm_dict.get("alarm_type", "pd_alarm"),
            alarm_dict.get("level", "info"),
            alarm_dict.get("amplitude", 0.0),
        )

    def publish_alarm_cleared(self, device_id: str, alarm_type: str, description: str = "") -> None:
        """发布报警消除"""
        self._signals.pd_alarm_cleared.emit(device_id, alarm_type, description)

    def publish_device_status(self, device_id: str, status: int, status_data: dict) -> None:
        """发布设备状态变化"""
        self._signals.device_status_changed.emit(device_id, status, status_data)

    def publish_acquisition_started(self, device_id: str) -> None:
        """发布采集开始"""
        self._signals.acquisition_started.emit(device_id)
        self._data_bus.publish_device_connected(device_id)

    def publish_acquisition_stopped(self, device_id: str) -> None:
        """发布采集停止"""
        self._signals.acquisition_stopped.emit(device_id)
        self._data_bus.publish_device_disconnected(device_id)

    def publish_ae_hit(self, device_id: str, channel_id: int, ae_hit_dict: dict) -> None:
        """发布 AE hit 事件"""
        self._signals.ae_hit_detected.emit(device_id, channel_id, ae_hit_dict)

    def publish_ae_envelope(self, device_id: str, channel_id: int, envelope: Any) -> None:
        """发布 AE 包络波形"""
        self._signals.ae_envelope_ready.emit(device_id, channel_id, envelope)

    def publish_acquisition_error(self, device_id: str, error_msg: str) -> None:
        """发布采集错误"""
        self._signals.acquisition_error.emit(device_id, error_msg)
        self._data_bus.publish_comm_error(device_id, error_msg)

    # ── 订阅管理 ─────────────────────────────────────

    def subscribe(self, signal: Signal, slot: Callable) -> None:
        """订阅 PD 信号"""
        self._sub_mgr.register(signal, slot)

    def subscribe_raw(self, topic: str, slot: Callable) -> bool:
        """订阅原始 DataBus 主题"""
        return self._data_bus.subscribe(topic, slot)

    # ── 生命周期 ─────────────────────────────────────

    def shutdown(self) -> None:
        """优雅停机"""
        released = self._sub_mgr.release_all()
        logger.info("PD DataBus 停机, 释放 %d 个订阅", released)

    @classmethod
    def reset(cls) -> None:
        """重置单例（测试用）"""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.shutdown()
            cls._instance = None
