# -*- coding: utf-8 -*-
"""
DataBus - 全局事件总线（v2.0 网关化重构版）

核心机制:
- 发布/订阅模式: 生产者不关心谁消费
- 死区过滤(Deadband): 数值变化超过阈值才触发UI刷新，优化CPU性能
- 生命周期管理: shutdown()依次释放订阅、发射停机信号
- 线程安全: Qt Signal/Slot自动跨线程排队 + 双重检查锁单例
- 类型安全: 每个主题有明确的数据类型

数据流（规范控制点⑥）:
    通信层 → 解析层 → DataBus.publish() → [死区过滤] → Signal.emit() → UI/DB订阅者

使用方式:
    # 发布者（通信/解析层）
    DataBus.instance().publish_device_data(device_id, data)

    # 订阅者（UI/DB层）
    DataBus.instance().subscribe('device_data_updated', self._on_data_updated)

    # 优雅停机
    DataBus.instance().shutdown()
"""

from __future__ import annotations

import logging
import math
import threading
import warnings
from typing import Any, Callable, Dict, Optional, Set, Tuple

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class DeadbandFilter:
    """
    数据死区过滤器

    规范要求: 在DataBus发布前增加过滤逻辑，只有数值变化超过设定阈值
    或状态变更时才触发UI刷新信号，以优化CPU性能。

    策略:
    - 数值型参数: |new_value - last_value| > deadband 才通过
    - 状态型参数: 值发生变化即通过
    - 首次数据: 始终通过（无历史值对比）
    """

    DEFAULT_DEADBAND = 0.0

    def __init__(self):
        self._last_values: Dict[str, Dict[str, Any]] = {}
        self._deadband_config: Dict[str, Dict[str, float]] = {}
        self._global_deadband: float = self.DEFAULT_DEADBAND
        self._lock = threading.RLock()

    def set_global_deadband(self, threshold: float) -> None:
        self._global_deadband = max(0.0, threshold)
        logger.info("全局死区阈值已设置为: %.4f", self._global_deadband)

    def set_device_deadband(self, device_id: str, param_name: str, threshold: float) -> None:
        with self._lock:
            if device_id not in self._deadband_config:
                self._deadband_config[device_id] = {}
            self._deadband_config[device_id][param_name] = max(0.0, threshold)

    def set_device_deadbands(self, device_id: str, config: Dict[str, float]) -> None:
        with self._lock:
            if device_id not in self._deadband_config:
                self._deadband_config[device_id] = {}
            self._deadband_config[device_id].update(config)

    def filter_and_get_keys(self, device_id: str, data: Dict[str, Any]) -> Tuple[bool, set]:
        """Atomically check if data should be published and get changed keys."""
        if not data:
            return False, set()

        with self._lock:
            last = self._last_values.get(device_id)

            if last is None:
                self._last_values[device_id] = dict(data)
                return True, set(data.keys())

            changed_keys = set()
            filtered_data = {}

            for key, new_value in data.items():
                old_value = last.get(key)
                if old_value is None:
                    changed_keys.add(key)
                    filtered_data[key] = new_value
                elif self._value_changed(device_id, key, old_value, new_value):
                    changed_keys.add(key)
                    filtered_data[key] = new_value
                else:
                    filtered_data[key] = old_value

            if changed_keys:
                self._last_values[device_id] = filtered_data
                return True, changed_keys

            return False, set()

    def should_publish(self, device_id: str, data: Dict[str, Any]) -> bool:
        if not data:
            return False

        with self._lock:
            last = self._last_values.get(device_id)

        if last is None:
            with self._lock:
                self._last_values[device_id] = dict(data)
            return True

        has_change = False
        filtered_data = {}

        for key, new_value in data.items():
            old_value = last.get(key)

            if old_value is None:
                has_change = True
                filtered_data[key] = new_value
                continue

            if self._value_changed(device_id, key, old_value, new_value):
                has_change = True
                filtered_data[key] = new_value
            else:
                filtered_data[key] = old_value

        if has_change:
            with self._lock:
                self._last_values[device_id] = filtered_data

        return has_change

    def get_changed_keys(self, device_id: str, data: Dict[str, Any]) -> Set[str]:
        with self._lock:
            last = self._last_values.get(device_id)

        if last is None:
            return set(data.keys())

        changed = set()
        for key, new_value in data.items():
            old_value = last.get(key)
            if old_value is None or self._value_changed(device_id, key, old_value, new_value):
                changed.add(key)

        return changed

    def _value_changed(self, device_id: str, key: str, old_value: Any, new_value: Any) -> bool:
        if isinstance(new_value, dict) and isinstance(old_value, dict):
            if "value" in new_value and "value" in old_value:
                return self._numeric_changed(device_id, key, old_value["value"], new_value["value"])
            return new_value != old_value

        if isinstance(new_value, (int, float)) and isinstance(old_value, (int, float)):
            return self._numeric_changed(device_id, key, float(old_value), float(new_value))

        return new_value != old_value

    def _numeric_changed(self, device_id: str, key: str, old_val: float, new_val: float) -> bool:
        if math.isnan(old_val) or math.isnan(new_val):
            return True
        if math.isinf(old_val) or math.isinf(new_val):
            return old_val != new_val

        threshold = self._get_deadband(device_id, key)
        if threshold <= 0.0:
            return old_val != new_val

        return abs(new_val - old_val) > threshold

    def _get_deadband(self, device_id: str, key: str) -> float:
        with self._lock:
            device_cfg = self._deadband_config.get(device_id)
            if device_cfg:
                param_threshold = device_cfg.get(key)
                if param_threshold is not None:
                    return param_threshold

        return self._global_deadband

    def clear_device(self, device_id: str) -> None:
        with self._lock:
            self._last_values.pop(device_id, None)
            self._deadband_config.pop(device_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._last_values.clear()
            self._deadband_config.clear()

    def get_last_value(self, device_id: str, key: str) -> Optional[Any]:
        with self._lock:
            last = self._last_values.get(device_id)
            if last:
                return last.get(key)
        return None

    def get_last_data(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            last = self._last_values.get(device_id)
            return dict(last) if last else None


class SubscriptionManager:
    """
    订阅管理器 — 跟踪所有DataBus连接，支持优雅停机时批量释放

    规范要求: 程序关闭时依次释放DataBus订阅
    """

    def __init__(self):
        self._subscriptions: Dict[tuple, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def register(self, signal: Signal, slot: Callable) -> None:
        with self._lock:
            key = (id(signal), id(slot))
            if key in self._subscriptions:
                return
            try:
                signal.connect(slot)
                self._subscriptions[key] = {"signal": signal, "slot": slot}
            except (RuntimeError, TypeError) as e:
                logger.warning("订阅注册失败: %s", e)

    def unregister(self, signal: Signal, slot: Callable) -> None:
        with self._lock:
            key = (id(signal), id(slot))
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError) as e:
                logger.debug("取消订阅失败: %s", e)
            self._subscriptions.pop(key, None)

    def release_all(self) -> int:
        with self._lock:
            count = len(self._subscriptions)
            for sub in self._subscriptions.values():
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        sub["signal"].disconnect(sub["slot"])
                except (RuntimeError, TypeError):
                    pass
            self._subscriptions.clear()
            return count

    @property
    def subscription_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)


class DataBus(QObject):
    """
    全局事件总线（v2.0 网关化重构版）

    核心改进:
    1. 死区过滤: publish_device_data() 内置 DeadbandFilter
    2. 订阅管理: subscribe()/unsubscribe() 通过 SubscriptionManager 跟踪
    3. 生命周期: shutdown() 依次释放订阅、发射停机信号
    4. 向后兼容: 所有原有 Signal 保留，直接 emit 仍可用

    数据流（规范控制点⑥ — 单向数据流）:
        通信层 → 解析层 → DataBus.publish_device_data()
                                   ↓
                          [DeadbandFilter]
                                   ↓ (通过)
                          device_data_updated.emit()
                                   ↓
                     UI订阅者 / DB订阅者 / 报警订阅者
    """

    _instance = None
    _lock = threading.Lock()

    # ==================== 信号定义（规范控制点⑥）====================

    device_connected = Signal(str)
    device_disconnected = Signal(str)
    device_status_changed = Signal(str, str)

    device_data_updated = Signal(str, dict)
    device_raw_updated = Signal(str, dict)
    device_raw_bytes_received = Signal(str, bytes)

    alarm_triggered = Signal(str, str, str, float)
    alarm_cleared = Signal(str, str)

    comm_error = Signal(str, str)
    comm_quality_updated = Signal(str, dict)

    config_changed = Signal(str)
    device_config_updated = Signal(str)

    system_shutdown = Signal()

    # v2.0 新增: 死区过滤后的数据信号（仅数值变化超过阈值时发射）
    device_data_changed = Signal(str, dict, set)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._deadband_filter = DeadbandFilter()
        self._subscription_mgr = SubscriptionManager()
        self._is_shutdown = False
        self._publish_count = 0
        self._filter_count = 0

    @classmethod
    def instance(cls) -> "DataBus":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance._deadband_filter.clear_all()
                    cls._instance._subscription_mgr.release_all()
                except Exception as e:
                    import logging

                    logging.getLogger(__name__).debug("DataBus reset 清理异常: %s", e)
            cls._instance = None

    # ==================== 发布API（规范 Step 5）====================

    def publish_device_data(self, device_id: str, data: Dict[str, Any]) -> bool:
        """
        发布设备数据（核心方法 — 带死区过滤）

        数据流: 通信层解析后调用此方法 → 死区过滤 → emit信号

        Args:
            device_id: 设备/网关ID
            data: 解析后的数据字典

        Returns:
            True=数据通过死区过滤并已发布, False=被死区过滤拦截
        """
        if self._is_shutdown:
            logger.debug("DataBus已停机，忽略数据发布: %s", device_id)
            return False

        if not data:
            return False

        self._publish_count += 1

        should_publish, changed_keys = self._deadband_filter.filter_and_get_keys(device_id, data)

        if should_publish:
            self.device_data_updated.emit(device_id, data)
            self.device_data_changed.emit(device_id, data, changed_keys)
            return True
        else:
            self._filter_count += 1
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("数据被死区过滤拦截 [%s], 变更键=%s", device_id, changed_keys)
            return False

    def publish_device_raw(self, device_id: str, raw_data: Dict[str, Any]) -> None:
        if self._is_shutdown:
            return
        self.device_raw_updated.emit(device_id, raw_data)

    def publish_device_connected(self, device_id: str) -> None:
        if self._is_shutdown:
            return
        self.device_connected.emit(device_id)
        self.device_status_changed.emit(device_id, "connected")
        self._deadband_filter.clear_device(device_id)

    def publish_device_disconnected(self, device_id: str) -> None:
        if self._is_shutdown:
            return
        self.device_disconnected.emit(device_id)
        self.device_status_changed.emit(device_id, "disconnected")
        self._deadband_filter.clear_device(device_id)

    def publish_comm_error(self, device_id: str, error_msg: str) -> None:
        if self._is_shutdown:
            return
        self.comm_error.emit(device_id, error_msg)

    def publish_alarm(self, device_id: str, param_name: str, alarm_type: str, value: float) -> None:
        if self._is_shutdown:
            return
        self.alarm_triggered.emit(device_id, param_name, alarm_type, value)

    def publish_alarm_cleared(self, device_id: str, param_name: str) -> None:
        if self._is_shutdown:
            return
        self.alarm_cleared.emit(device_id, param_name)

    # ==================== 订阅API（规范 Step 5）====================

    def subscribe(self, topic: str, slot: Callable) -> bool:
        """
        订阅DataBus主题（规范 Step 5 发布/订阅机制）

        Args:
            topic: 主题名称，对应信号名
            slot: 槽函数

        Returns:
            是否订阅成功
        """
        signal = getattr(self, topic, None)
        if signal is None or not isinstance(signal, Signal):
            logger.warning("未知的DataBus主题: %s", topic)
            return False

        self._subscription_mgr.register(signal, slot)
        logger.debug("DataBus订阅: %s → %s", topic, slot.__name__ if hasattr(slot, "__name__") else str(slot))
        return True

    def unsubscribe(self, topic: str, slot: Callable) -> None:
        """
        取消订阅DataBus主题
        """
        signal = getattr(self, topic, None)
        if signal is None:
            return

        self._subscription_mgr.unregister(signal, slot)

    # ==================== 死区配置API ====================

    def set_global_deadband(self, threshold: float) -> None:
        self._deadband_filter.set_global_deadband(threshold)

    def set_device_deadband(self, device_id: str, param_name: str, threshold: float) -> None:
        self._deadband_filter.set_device_deadband(device_id, param_name, threshold)

    def set_device_deadbands(self, device_id: str, config: Dict[str, float]) -> None:
        self._deadband_filter.set_device_deadbands(device_id, config)

    def get_last_data(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self._deadband_filter.get_last_data(device_id)

    # ==================== 生命周期管理（规范 — 优雅停机）====================

    def shutdown(self) -> None:
        """
        优雅停机（规范要求）

        执行顺序:
        1. 标记停机状态（拒绝新的发布）
        2. 发射 system_shutdown 信号（通知所有订阅者）
        3. 释放所有订阅连接
        4. 清理死区过滤缓存
        """
        if self._is_shutdown:
            return

        logger.info(
            "DataBus开始停机 [发布总数=%d, 过滤拦截=%d, 活跃订阅=%d]",
            self._publish_count,
            self._filter_count,
            self._subscription_mgr.subscription_count,
        )

        self._is_shutdown = True

        try:
            self.system_shutdown.emit()
        except (RuntimeError, TypeError) as e:
            logger.debug("发射停机信号异常: %s", e)

        released = self._subscription_mgr.release_all()
        logger.info("已释放 %d 个DataBus订阅", released)

        self._deadband_filter.clear_all()

        logger.info("DataBus停机完成")

    @property
    def is_shutdown(self) -> bool:
        return self._is_shutdown

    # ==================== 统计信息 ====================

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "publish_count": self._publish_count,
            "filter_count": self._filter_count,
            "filter_rate": (self._filter_count / self._publish_count * 100 if self._publish_count > 0 else 0.0),
            "active_subscriptions": self._subscription_mgr.subscription_count,
            "is_shutdown": self._is_shutdown,
            "global_deadband": self._deadband_filter._global_deadband,
        }
