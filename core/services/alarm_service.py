# -*- coding: utf-8 -*-
"""
局放报警服务 - PDAlarmService

四级报警体系 (ISA-18.2):
- critical (紧急): 红色 #DC2626, 幅值 ≥ 80% 量程
- high (高级):   橙色 #EA580C, 幅值 ≥ 60% 量程
- medium (中级): 黄色 #CA8A04, 幅值 ≥ 40% 量程
- low (低级):    蓝色 #2563EB, 幅值 ≥ 20% 量程

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
from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List, Optional

from core.data.monitor_models import PDAlarmModel
from core.foundation.signal_bus import PDDataBus

try:
    from sqlalchemy.exc import DatabaseError
except ImportError:
    DatabaseError = Exception  # 回退

logger = logging.getLogger(__name__)


# 报警等级配置 (ISA-18.2 四级标准)
ALARM_LEVELS = {
    "critical": {"name": "紧急", "threshold_pct": 80.0, "color": "#DC2626", "db_level": 4},
    "high": {"name": "高级", "threshold_pct": 60.0, "color": "#EA580C", "db_level": 3},
    "medium": {"name": "中级", "threshold_pct": 40.0, "color": "#CA8A04", "db_level": 2},
    "low": {"name": "低级", "threshold_pct": 20.0, "color": "#2563EB", "db_level": 1},
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
        # NaN/Inf 防护。拆成两条 if 而不是一个多行布尔表达式：本仓库 .flake8 的 ignore
        # 是覆盖默认表而非追加，W503 与 W504 同时生效，运算符放行首或行尾都会被报。
        if not isinstance(amplitude, (int, float)):
            return None
        if amplitude != amplitude or amplitude in (float("inf"), float("-inf")):
            return None

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
        elif pct >= level_thresholds.get("high", ALARM_LEVELS["high"]["threshold_pct"]):
            level = "high"
        elif pct >= level_thresholds.get("medium", ALARM_LEVELS["medium"]["threshold_pct"]):
            level = "medium"
        elif pct >= level_thresholds.get("low", ALARM_LEVELS["low"]["threshold_pct"]):
            level = "low"

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
                "level": "high",
                "amplitude": 0,
                "threshold": 0,
                "level_name": ALARM_LEVELS["high"]["name"],
                "description": f"{ALARM_TYPE_NAMES['adc_abnormal']} - ADC 状态码: {status_data.adc_status}",
                "timestamp": time.time(),
            }
        elif hasattr(status_data, "optical_status") and status_data.optical_status > 0:
            alarm = {
                "device_id": device_id,
                "channel_id": 0,
                "alarm_type": "optical_abnormal",
                "level": "high",
                "amplitude": 0,
                "threshold": 0,
                "level_name": ALARM_LEVELS["high"]["name"],
                "description": f"{ALARM_TYPE_NAMES['optical_abnormal']} - 光模块状态码: {status_data.optical_status}",
                "timestamp": time.time(),
            }
        elif hasattr(status_data, "sync_status") and status_data.sync_status > 0:
            alarm = {
                "device_id": device_id,
                "channel_id": 0,
                "alarm_type": "sync_abnormal",
                "level": "medium",
                "amplitude": 0,
                "threshold": 0,
                "level_name": ALARM_LEVELS["medium"]["name"],
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
                "level": "medium",
                "amplitude": 0,
                "threshold": 90,
                "level_name": ALARM_LEVELS["medium"]["name"],
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

        # 先落库拿到主键再发布：告警帧必须带 id，否则前端无法确认某一条。
        # 落库失败时 alarm_id 为 None，前端据此把该条显示为"未持久化"并禁用确认，
        # 而不是给一个永远 ack 不掉的假标识。
        alarm_id: Optional[int] = None
        if self._db_manager:
            try:
                alarm_id = self._save_alarm_to_db(alarm)
            except (OSError, DatabaseError) as e:
                logger.error("报警记录持久化失败 - 数据库错误: %s", e, exc_info=True)
            except Exception as e:
                logger.error("报警记录持久化失败: %s", e, exc_info=True)

        published = {**alarm, "alarm_id": alarm_id}
        self._data_bus.publish_alarm(device_id, published)
        logger.warning("[ALARM] [%s] %s", device_id, alarm.get("description", alarm_type))

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

    def set_amplitude_thresholds(
        self, critical: float, high: float, medium: float, low: float, max_amplitude: float
    ) -> None:
        """
        直接设置各等级的绝对阈值（替代百分比体系）

        Args:
            critical: 紧急报警阈值 (mV)
            high: 高级报警阈值 (mV)
            medium: 中级报警阈值 (mV)
            low: 低级报警阈值 (mV)
            max_amplitude: 最大幅值
        """
        self._global_max_amplitude = max_amplitude
        if max_amplitude > 0:
            self._custom_thresholds = {
                "critical": critical / max_amplitude * 100,
                "high": high / max_amplitude * 100,
                "medium": medium / max_amplitude * 100,
                "low": low / max_amplitude * 100,
            }
        logger.info(
            "报警阈值已更新: critical=%.1f, high=%.1f, medium=%.1f, low=%.1f, max=%.1f",
            critical,
            high,
            medium,
            low,
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

    def get_thresholds(self) -> dict:
        """当前生效的阈值配置，供前端设置页回显。

        _custom_thresholds 存的是相对 max_amplitude 的百分比，这里同时换算回绝对
        mV，避免前端自己猜单位。
        """
        max_amp = self._global_max_amplitude
        percent = self._custom_thresholds or {"critical": 80.0, "high": 60.0, "medium": 50.0, "low": 30.0}
        absolute = {k: round(max_amp * v / 100.0, 3) for k, v in percent.items()}
        return {
            "max_amplitude": max_amp,
            "custom_percent": dict(percent),
            "custom_absolute_mv": absolute,
            "hysteresis": self._hysteresis_threshold,
            "per_device": {dev: dict(cfg) for dev, cfg in self._amplitude_thresholds.items()},
        }

    # ── 持久化 ───────────────────────────────────────

    def _save_alarm_to_db(self, alarm: dict) -> Optional[int]:
        """保存报警到数据库，返回新行的自增主键。

        返回 id 是为了让发布出去的告警帧带上可寻址的标识：前端"确认"一条告警时
        必须能定位到数据库行，而告警字典里原本只有业务字段、没有任何主键。
        """
        if not self._db_manager:
            return None
        try:
            with self._db_manager.session() as session:
                model = PDAlarmModel(
                    device_id=alarm.get("device_id", ""),
                    device_name=f"设备 {alarm.get('device_id', '')}",
                    channel_id=alarm.get("channel_id"),
                    alarm_type=alarm.get("alarm_type", "unknown"),
                    level=alarm.get("level", "low"),
                    amplitude=alarm.get("amplitude", 0.0),
                    threshold=alarm.get("threshold", 0.0),
                    description=alarm.get("description", ""),
                )
                session.add(model)
                session.flush()
                return int(model.id)
        except (OSError, DatabaseError) as e:
            logger.error("报警持久化异常 - 数据库错误: %s", e, exc_info=True)
        except (ValueError, TypeError, KeyError) as e:
            logger.error("报警持久化异常 - 数据格式错误: %s", e, exc_info=True)
        except Exception as e:
            logger.error("报警持久化异常: %s", e, exc_info=True)
        return None

    def acknowledge(self, alarm_ids: list[int], acknowledged_by: str = "operator") -> dict:
        """把告警标记为已确认。

        只动 acknowledged / acknowledged_at / acknowledged_by 三列——这三列在
        pd_alarms 里早就建好并加了索引（idx_pd_alarm_ack），但全仓此前没有任何
        代码写入它们，旧 UI 的"确认"只改表格行状态、重启即丢。
        """
        ids = []
        for raw in alarm_ids or []:
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if not ids:
            raise ValueError("alarm_ids 为空或全部非法")

        if not self._db_manager:
            raise RuntimeError("未配置数据库，告警无法确认")

        with self._db_manager.session() as session:
            updated = (
                session.query(PDAlarmModel)
                .filter(PDAlarmModel.id.in_(ids), PDAlarmModel.acknowledged.is_(False))
                .update(
                    {
                        "acknowledged": True,
                        "acknowledged_at": datetime.now(timezone.utc),
                        "acknowledged_by": acknowledged_by,
                    },
                    synchronize_session=False,
                )
            )
        return {"requested": len(ids), "acknowledged": int(updated or 0)}

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
