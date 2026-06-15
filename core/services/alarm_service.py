# -*- coding: utf-8 -*-
"""
局放报警服务 - PDAlarmService

三级报警体系:
- critical (严重): 红色 #CF222E, 幅值 ≥ 80% 量程
- warning (一般): 黄色 #D29922, 幅值 ≥ 50% 量程
- info (提示): 绿色 #1A7F37, 幅值 ≥ 30% 量程

六种报警类型:
- pd_over_limit:    局放超限
- device_offline:   设备离线
- optical_abnormal: 光模块异常
- adc_abnormal:     ADC 异常
- sync_abnormal:    同步异常
- storage_low:      存储空间不足

遵循 AnomalyService 的设计模式，通过 DataBus 发布报警事件。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from threading import RLock
from typing import Any, Dict, List, Optional, Set

from core.data.monitor_models import PDAlarmModel
from core.foundation.signal_bus import PDDataBus

logger = logging.getLogger(__name__)


# 报警等级配置
ALARM_LEVELS = {
    "critical": {"name": "严重报警", "threshold_pct": 80.0, "color": "#CF222E", "db_level": 3},
    "warning": {"name": "一般报警", "threshold_pct": 50.0, "color": "#D29922", "db_level": 2},
    "info": {"name": "提示报警", "threshold_pct": 30.0, "color": "#1A7F37", "db_level": 1},
}

ALARM_TYPE_NAMES = {
    "pd_over_limit": "局放超限",
    "device_offline": "设备离线",
    "optical_abnormal": "光模块异常",
    "adc_abnormal": "ADC 异常",
    "sync_abnormal": "同步异常",
    "storage_low": "存储空间不足",
}


class PDAlarmService:
    """
    局放报警服务

    支持:
    - 基于阈值的幅值报警
    - 设备离线检测
    - 设备状态异常检测
    - 报警防抖 (hysteresis)
    - 报警确认/消除
    - 报警记录持久化
    """

    def __init__(self, db_manager=None):
        self._db_manager = db_manager
        self._data_bus = PDDataBus.instance()
        self._lock = RLock()

        # 活跃报警 {device_id: {alarm_type: alarm_info}}
        self._active_alarms: Dict[str, Dict[str, dict]] = {}

        # 设备在线状态
        self._device_online: Dict[str, bool] = {}

        # 报警防抖配置 {device_id: {alarm_type: count}}
        self._hysteresis_counters: Dict[str, Dict[str, int]] = {}
        self._hysteresis_threshold = 3  # 连续触发 3 次才确认报警

        # 幅值阈值配置 (可覆盖)
        self._amplitude_thresholds: Dict[str, Dict[str, float]] = {}
        self._global_max_amplitude = 100.0  # mV
        self._custom_thresholds: Optional[Dict[str, float]] = None  # UI 自定义阈值

        # 报警计数
        self._stats = {
            "total_alarms": 0,
            "active_count": 0,
            "critical_count": 0,
            "warning_count": 0,
            "info_count": 0,
            "cleared_count": 0,
        }

    # ── 报警检测 ─────────────────────────────────────

    def set_channel_max_amplitude(self, device_id: str, channel_id: int, max_amplitude: float) -> None:
        """设置指定通道的最大幅值阈值（AE 通道幅值通常小于 UHF）"""
        with self._lock:
            key = f"{device_id}:{channel_id}"
            if "channel_max" not in self._amplitude_thresholds:
                self._amplitude_thresholds["channel_max"] = {}
            self._amplitude_thresholds["channel_max"][key] = max_amplitude

    def check_amplitude(self, device_id: str, channel_id: int, amplitude: float) -> Optional[dict]:
        """
        检测幅值是否超标

        Args:
            device_id: 设备 ID
            channel_id: 通道 ID
            amplitude: 当前幅值 (mV)

        Returns:
            报警字典，无报警则返回 None
        """
        # 按通道阈值优先 (AE 通道独立配置), 再设备阈值, 最后全局默认
        ch_key = f"{device_id}:{channel_id}"
        ch_max = self._amplitude_thresholds.get("channel_max", {}).get(ch_key)
        if ch_max is not None:
            max_amp = ch_max
        else:
            max_amp = self._amplitude_thresholds.get(device_id, {}).get("max_amplitude", self._global_max_amplitude)
        pct = (amplitude / max_amp) * 100 if max_amp > 0 else 0

        # 自定义阈值优先，否则使用模块级百分比阈值
        level_thresholds = self._custom_thresholds or {k: v["threshold_pct"] for k, v in ALARM_LEVELS.items()}

        level = None
        if pct >= level_thresholds.get("critical", ALARM_LEVELS["critical"]["threshold_pct"]):
            level = "critical"
        elif pct >= level_thresholds.get("warning", ALARM_LEVELS["warning"]["threshold_pct"]):
            level = "warning"
        elif pct >= level_thresholds.get("info", ALARM_LEVELS["info"]["threshold_pct"]):
            level = "info"

        if level is None:
            return None

        # 防抖 + 阈值检查原子操作（防 TOCTOU 竞态）
        key = f"{device_id}:{channel_id}:pd_over_limit"
        with self._lock:
            if key not in self._hysteresis_counters:
                self._hysteresis_counters[key] = {"count": 0, "level": None}

            counter = self._hysteresis_counters[key]
            if counter["level"] != level:
                counter["count"] = 0
                counter["level"] = level

            counter["count"] += 1
            if counter["count"] < self._hysteresis_threshold:
                return None

            # 在锁内直接触发报警，消除 TOCTOU 窗口
            return self._build_and_trigger(
                {
                    "device_id": device_id,
                    "channel_id": channel_id,
                    "alarm_type": "pd_over_limit",
                    "level": level,
                    "amplitude": amplitude,
                    "threshold": max_amp * ALARM_LEVELS[level]["threshold_pct"] / 100,
                    "description": f"{ALARM_TYPE_NAMES['pd_over_limit']} - "
                    f"幅值 {amplitude:.1f}mV ({pct:.0f}%), "
                    f"等级: {ALARM_LEVELS[level]['name']}",
                }
            )

    def _build_and_trigger(self, alarm: dict) -> dict:
        """构建报警字典并触发（须在锁内调用）"""
        alarm["level_name"] = ALARM_LEVELS[alarm["level"]]["name"]
        alarm["timestamp"] = time.time()
        self.trigger_alarm(alarm)
        return alarm

    def check_device_status(self, device_id: str, status_data: dict) -> Optional[dict]:
        """
        检测设备状态异常

        Args:
            device_id: 设备 ID
            status_data: 设备状态数据 (DeviceStatusData 或 dict)

        Returns:
            报警字典，无异常则返回 None
        """
        if hasattr(status_data, "status"):
            status = status_data.status
        elif isinstance(status_data, dict):
            status = status_data.get("status", 0)
        else:
            return None

        alarm = None
        if status == 3:  # 错误
            alarm = {
                "device_id": device_id,
                "channel_id": 0,
                "alarm_type": "device_offline",
                "level": "critical",
                "amplitude": 0,
                "threshold": 0,
                "level_name": ALARM_LEVELS["critical"]["name"],
                "description": f"{ALARM_TYPE_NAMES['device_offline']} - 设备状态异常",
                "timestamp": time.time(),
            }
        elif hasattr(status_data, "adc_status") and status_data.adc_status > 0:
            alarm = {
                "device_id": device_id,
                "channel_id": 0,
                "alarm_type": "adc_abnormal",
                "level": "warning",
                "amplitude": 0,
                "threshold": 0,
                "level_name": ALARM_LEVELS["warning"]["name"],
                "description": f"{ALARM_TYPE_NAMES['adc_abnormal']} - ADC 状态码: {status_data.adc_status}",
                "timestamp": time.time(),
            }
        elif hasattr(status_data, "optical_status") and status_data.optical_status > 0:
            alarm = {
                "device_id": device_id,
                "channel_id": 0,
                "alarm_type": "optical_abnormal",
                "level": "warning",
                "amplitude": 0,
                "threshold": 0,
                "level_name": ALARM_LEVELS["warning"]["name"],
                "description": f"{ALARM_TYPE_NAMES['optical_abnormal']} - 光模块状态码: {status_data.optical_status}",
                "timestamp": time.time(),
            }
        elif hasattr(status_data, "sync_status") and status_data.sync_status > 0:
            alarm = {
                "device_id": device_id,
                "channel_id": 0,
                "alarm_type": "sync_abnormal",
                "level": "warning",
                "amplitude": 0,
                "threshold": 0,
                "level_name": ALARM_LEVELS["warning"]["name"],
                "description": f"{ALARM_TYPE_NAMES['sync_abnormal']} - 同步状态码: {status_data.sync_status}",
                "timestamp": time.time(),
            }

        return alarm

    def check_storage(self, usage_pct: float) -> Optional[dict]:
        """检测存储空间"""
        if usage_pct > 90:
            return {
                "device_id": "system",
                "channel_id": 0,
                "alarm_type": "storage_low",
                "level": "warning",
                "amplitude": 0,
                "threshold": 90,
                "level_name": ALARM_LEVELS["warning"]["name"],
                "description": f"{ALARM_TYPE_NAMES['storage_low']} - 使用率: {usage_pct:.0f}%",
                "timestamp": time.time(),
            }
        return None

    # ── 报警生命周期 ─────────────────────────────────

    def trigger_alarm(self, alarm: dict) -> None:
        """
        触发报警（发布到 DataBus + 持久化）

        Args:
            alarm: 报警字典
        """
        device_id = alarm["device_id"]
        alarm_type = alarm["alarm_type"]
        key = f"{device_id}:{alarm_type}"

        with self._lock:
            device_alarms = self._active_alarms.setdefault(device_id, {})

            # 避免重复触发相同报警
            if alarm_type in device_alarms:
                existing = device_alarms[alarm_type]
                if existing.get("level") == alarm["level"]:
                    # 更新幅值但不重复触发
                    existing["amplitude"] = max(existing.get("amplitude", 0), alarm.get("amplitude", 0))
                    existing["count"] = existing.get("count", 1) + 1
                    return

            device_alarms[alarm_type] = {**alarm, "count": 1}
            self._stats["total_alarms"] += 1
            self._stats[f"{alarm['level']}_count"] = self._stats.get(f"{alarm['level']}_count", 0) + 1
            self._stats["active_count"] = len(device_alarms)

        # 发布到 DataBus
        self._data_bus.publish_alarm(device_id, alarm)
        logger.warning("[ALARM] [%s] %s", device_id, alarm.get("description", alarm_type))

        # 持久化到数据库
        if self._db_manager:
            try:
                self._save_alarm_to_db(alarm)
            except Exception as e:
                logger.error("报警记录持久化失败: %s", e)

    def clear_alarm(self, device_id: str, alarm_type: str) -> None:
        """
        消除报警

        Args:
            device_id: 设备 ID
            alarm_type: 报警类型
        """
        with self._lock:
            device_alarms = self._active_alarms.get(device_id)
            if device_alarms and alarm_type in device_alarms:
                desc = device_alarms[alarm_type].get("description", "")
                del device_alarms[alarm_type]
                self._stats["cleared_count"] += 1
                self._stats["active_count"] = sum(len(a) for a in self._active_alarms.values())
            else:
                return

        self._data_bus.publish_alarm_cleared(device_id, alarm_type, desc)
        logger.info("[CLEAR] [%s] %s", device_id, alarm_type)

    def clear_device_alarms(self, device_id: str) -> None:
        """消除设备所有报警"""
        with self._lock:
            device_alarms = self._active_alarms.pop(device_id, {})
            for alarm_type in list(device_alarms.keys()):
                self._data_bus.publish_alarm_cleared(device_id, alarm_type, "设备报警全部消除")
                self._stats["cleared_count"] += 1
            self._stats["active_count"] = sum(len(a) for a in self._active_alarms.values())

    # ── 配置 ─────────────────────────────────────────

    def set_amplitude_threshold(self, device_id: str, max_amplitude: float) -> None:
        """设置设备幅值阈值"""
        with self._lock:
            if device_id not in self._amplitude_thresholds:
                self._amplitude_thresholds[device_id] = {}
            self._amplitude_thresholds[device_id]["max_amplitude"] = max_amplitude

    def set_global_max_amplitude(self, max_amp: float) -> None:
        """设置全局最大幅值"""
        self._global_max_amplitude = max_amp

    def set_amplitude_thresholds(self, critical: float, warning: float, info: float, max_amplitude: float) -> None:
        """
        直接设置各等级的绝对阈值（替代百分比体系）

        Args:
            critical: 严重报警阈值 (mV)
            warning: 一般报警阈值 (mV)
            info: 提示报警阈值 (mV)
            max_amplitude: 最大幅值
        """
        self._global_max_amplitude = max_amplitude
        if max_amplitude > 0:
            import copy as _copy

            self._custom_thresholds = {
                "critical": critical / max_amplitude * 100,
                "warning": warning / max_amplitude * 100,
                "info": info / max_amplitude * 100,
            }
        logger.info(
            "报警阈值已更新: critical=%.1f, warning=%.1f, info=%.1f, max=%.1f",
            critical,
            warning,
            info,
            max_amplitude,
        )

    def set_hysteresis(self, threshold: int) -> None:
        """设置防抖次数"""
        self._hysteresis_threshold = max(1, threshold)

    # ── 状态查询 ─────────────────────────────────────

    def get_active_alarms(self, device_id: Optional[str] = None) -> List[dict]:
        """获取活跃报警列表"""
        with self._lock:
            if device_id:
                alarms = self._active_alarms.get(device_id, {})
                return list(alarms.values())
            result = []
            for d_alarms in self._active_alarms.values():
                result.extend(d_alarms.values())
            return result

    def has_active_alarm(self, device_id: str, alarm_type: str) -> bool:
        """检查是否有指定类型的活跃报警"""
        with self._lock:
            device_alarms = self._active_alarms.get(device_id)
            return device_alarms is not None and alarm_type in device_alarms

    def get_stats(self) -> dict:
        """获取报警统计"""
        return dict(self._stats)

    # ── 持久化 ───────────────────────────────────────

    def _save_alarm_to_db(self, alarm: dict) -> None:
        """保存报警到数据库"""
        if not self._db_manager:
            return
        try:
            with self._db_manager.session() as session:
                model = PDAlarmModel(
                    device_id=alarm.get("device_id", ""),
                    device_name=f"设备 {alarm.get('device_id', '')}",
                    channel_id=alarm.get("channel_id"),
                    alarm_type=alarm.get("alarm_type", "unknown"),
                    level=alarm.get("level", "info"),
                    amplitude=alarm.get("amplitude", 0.0),
                    threshold=alarm.get("threshold", 0.0),
                    description=alarm.get("description", ""),
                )
                session.add(model)
        except Exception as e:
            logger.error("报警持久化异常: %s", e)

    # ── 生命周期 ─────────────────────────────────────

    def shutdown(self) -> None:
        """停止报警服务"""
        self.clear_all_alarms()
        logger.info("报警服务已停止")

    def clear_all_alarms(self) -> None:
        """消除所有报警"""
        with self._lock:
            for device_id in list(self._active_alarms.keys()):
                self.clear_device_alarms(device_id)
