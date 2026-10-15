# -*- coding: utf-8 -*-
"""
数据源管理器 - DataSourceManager

管理所有数据源的生命周期与状态:
- 统一管理 HardwareSource / SimulatorSource / (未来 FileSource)
- 智能探测: 数据库设备 → UDP 广播发现 → 回退模拟器
- 运行时切换: 添加/移除/启动/停止数据源
- 多源共存: 多个硬件 + 模拟器同时运行
- 线程安全: 所有公开方法使用 RLock 保护

信号:
    source_added(source_id)           - 数据源已添加
    source_removed(source_id)         - 数据源已移除
    source_status_changed(source_id, status) - 状态变更
    source_error(source_id, error_msg)       - 错误通知
    active_source_changed(source_id)         - 当前激活源切换
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from core.foundation.data_source import DataSource, DataSourceStatus
from core.foundation.data_source_config import DataSourceConfig

logger = logging.getLogger(__name__)


class DataSourceManager(QObject):
    """数据源管理器 - 管理所有数据源的生命周期与状态"""

    # === 信号 ===
    source_added = Signal(str)  # source_id
    source_removed = Signal(str)  # source_id
    source_status_changed = Signal(str, str)  # source_id, status
    source_error = Signal(str, str)  # source_id, error_msg
    active_source_changed = Signal(str)  # source_id (UI 显示切换)

    def __init__(self, acquisition_service, data_bus, db_manager=None, alarm_service=None, parent=None):
        super().__init__(parent)
        self._sources: Dict[str, DataSource] = {}
        self._active_source_id: Optional[str] = None
        self._acquisition = acquisition_service
        self._data_bus = data_bus
        self._db_manager = db_manager
        self._alarm_service = alarm_service
        self._lock = threading.RLock()

    # ── 生命周期 ────────────────────────────────────

    def auto_detect(self) -> bool:
        """智能检测: 已有源 → 数据库设备 → UDP 广播发现 → 回退模拟器"""
        with self._lock:
            # 0. 如果已有数据源，直接尝试启动
            if self._sources:
                started = self.start_all()
                if started > 0:
                    logger.info("auto_detect: 成功启动 %d 个已有数据源", started)
                    return True

            # 1. 从数据库加载已保存的设备配置
            db_devices = self._load_devices_from_db()
            for cfg in db_devices:
                self.add_hardware_source(cfg)

            started = self.start_all()
            if started > 0:
                logger.info("auto_detect: 成功启动 %d 个数据库设备", started)
                return True

            # 2. 尝试 UDP 广播发现
            discovered = self._discover_hardware()
            for cfg in discovered:
                self.add_hardware_source(cfg)

            started = self.start_all()
            if started > 0:
                logger.info("auto_detect: 成功启动 %d 个发现设备", started)
                return True

            # 3. 回退: 启动 UHF 模拟器
            logger.info("auto_detect: 无可用硬件设备，自动启动 UHF 模拟器")
            self.add_simulator_source("uhf", "internal", 20)
            self.start_all()
            return True

    def start_all(self) -> int:
        """启动所有已停止的数据源，返回成功启动数"""
        with self._lock:
            started = 0
            for source in list(self._sources.values()):
                if source.status == DataSourceStatus.STOPPED:
                    try:
                        if source.start():
                            started += 1
                            self.source_status_changed.emit(source.id, "running")
                            if self._active_source_id is None:
                                self._active_source_id = source.id
                                self.active_source_changed.emit(source.id)
                    except (RuntimeError, ConnectionError, OSError) as e:
                        logger.warning("数据源 %s 启动失败: %s", source.id, e)
                        self.source_error.emit(source.id, str(e))
            return started

    def stop_all(self) -> None:
        """停止所有数据源"""
        with self._lock:
            for source in list(self._sources.values()):
                source.stop()
                self.source_status_changed.emit(source.id, "stopped")
            self._active_source_id = None

    def start_source(self, source_id: str) -> bool:
        """启动单个数据源。

        与 start_all 的差别只有一处：start_all 只碰 STOPPED 的源，这里对
        ERROR/RECONNECTING 也一律拒绝——那两种状态由数据源自身（硬件重连线程）
        管理，外部强启会打断它的重连节奏，且旧 UI 也从没这么用过。

        已 RUNNING 视为成功（幂等），这样前端"启动"按钮重复点不会报错。
        """
        with self._lock:
            source = self._sources.get(source_id)
            if source is None:
                return False
            if source.status == DataSourceStatus.RUNNING:
                return True
            if source.status != DataSourceStatus.STOPPED:
                logger.debug("数据源 %s 当前状态 %s，不响应外部启动", source_id, source.status.value)
                return False
            try:
                started = bool(source.start())
            except (RuntimeError, ConnectionError, OSError) as e:
                logger.warning("数据源 %s 启动失败: %s", source_id, e)
                self.source_error.emit(source_id, str(e))
                return False
            if started:
                self.source_status_changed.emit(source_id, "running")
                if self._active_source_id is None:
                    self._active_source_id = source_id
                    self.active_source_changed.emit(source_id)
            return started

    def stop_source(self, source_id: str) -> bool:
        """停止单个数据源。

        停掉当前激活源时会把激活位让给另一个仍在运行的源；没有可接替的就把
        激活位置空（与 stop_all 一致，但不广播 active_source_changed("")——
        空 id 对前端没有意义，stats 帧里的 active_source=null 已经说明了）。
        """
        with self._lock:
            source = self._sources.get(source_id)
            if source is None:
                return False
            was_active = self._active_source_id == source_id
        # 锁外 stop()，避免长时间持锁（与 remove_source 同）
        source.stop()
        with self._lock:
            self.source_status_changed.emit(source_id, "stopped")
            if was_active:
                successor = next(
                    (
                        sid
                        for sid, other in self._sources.items()
                        if sid != source_id and other.status == DataSourceStatus.RUNNING
                    ),
                    None,
                )
                self._active_source_id = successor
                if successor:
                    self.active_source_changed.emit(successor)
                    self._data_bus.publish_active_source_changed(successor)
            logger.info("数据源已停止: %s", source_id)
            return True

    # ── 数据源管理 ───────────────────────────────────

    def add_hardware_source(self, config) -> str:
        """
        添加硬件数据源

        Args:
            config: DeviceConnectionConfig 实例

        Returns:
            source_id (即 device_id)
        """
        with self._lock:
            if config.device_id in self._sources:
                logger.debug("硬件数据源已存在: %s", config.device_id)
                return config.device_id

            from core.communication.hardware_source import HardwareSource

            source = HardwareSource(config, self._acquisition, self._data_bus)
            self._sources[config.device_id] = source
            self._connect_source_signals(source)
            self.source_added.emit(config.device_id)
            self._data_bus.publish_source_attached(config.device_id)
            logger.info("硬件数据源已添加: %s [%s:%d]", config.device_id, config.host, config.udp_port)
            return config.device_id

    def add_simulator_source(
        self, sim_type: str = "uhf", pd_type: str = "internal", fps: int = 20, pulse_rate: Optional[float] = None
    ) -> str:
        """
        添加模拟器数据源

        Args:
            sim_type: "uhf" 或 "ae"
            pd_type: 局放类型 (corona/surface/internal/floating 或 burst/continuous/mixed)
            fps: 帧率
            pulse_rate: 每秒事件数（UHF 为局放脉冲、AE 为 burst）；None 表示按类型取默认
                （UHF 50、AE 5）。调高只是让演示/验收时面板有数据可看

        Returns:
            source_id
        """
        with self._lock:
            source_id = f"sim-{sim_type}-{len(self._sources)}"

            from core.utils.simulator_source import SimulatorSource

            source = SimulatorSource(
                source_id,
                sim_type,
                pd_type,
                fps,
                self._acquisition,
                self._data_bus,
                pulse_rate=pulse_rate,
            )
            self._sources[source_id] = source
            self._connect_source_signals(source)
            self.source_added.emit(source_id)
            self._data_bus.publish_source_attached(source_id)

            # AE 模拟器幅值为 μV 级，远小于 UHF（mV 级），
            # 需要设置通道专属阈值避免误触发 UHF 报警阈值
            if sim_type == "ae" and self._alarm_service:
                try:
                    # AE 典型最大幅值 1000 μV = 1 mV，阈值设为 80% 满量程
                    self._alarm_service.set_channel_max_amplitude(source_id, 1, 800.0)
                    logger.debug("AE 模拟器 %s 已设置报警阈值: 800 μV", source_id)
                except (ValueError, TypeError, AttributeError) as e:
                    logger.warning("设置 AE 报警阈值失败（非关键功能）: %s", e)

            logger.info("模拟器数据源已添加: %s type=%s/%s, %d FPS", source_id, sim_type, pd_type, fps)
            return source_id

    def _connect_source_signals(self, source: DataSource) -> None:
        """连接数据源的 status_changed 和 error_occurred 信号"""
        source.status_changed.connect(lambda sid, status: self._on_source_status_changed(sid, status))
        source.error_occurred.connect(lambda sid, msg: self._on_source_error(sid, msg))

    def _on_source_status_changed(self, source_id: str, status: str) -> None:
        """数据源自身状态变更（如硬件断线、重连成功）"""
        self.source_status_changed.emit(source_id, status)
        logger.debug("数据源状态变更: %s → %s", source_id, status)

    def _on_source_error(self, source_id: str, error_msg: str) -> None:
        """数据源自身错误通知"""
        self.source_error.emit(source_id, error_msg)
        logger.warning("数据源错误: %s - %s", source_id, error_msg)

    def remove_source(self, source_id: str) -> bool:
        """移除数据源"""
        with self._lock:
            source = self._sources.pop(source_id, None)
            if not source:
                return False
        # 锁外执行 stop()，避免长时间持锁
        source.stop()
        with self._lock:
            if self._active_source_id == source_id:
                # 切换到下一个可用数据源
                self._active_source_id = next(iter(self._sources), None)
                if self._active_source_id:
                    self.active_source_changed.emit(self._active_source_id)
                    self._data_bus.publish_active_source_changed(self._active_source_id)
            self.source_removed.emit(source_id)
            self._data_bus.publish_source_detached(source_id)
            logger.info("数据源已移除: %s", source_id)
            return True

    # ── 查询 ─────────────────────────────────────────

    def get_sources(self) -> List[DataSource]:
        """获取所有数据源列表"""
        with self._lock:
            return list(self._sources.values())

    def get_source(self, source_id: str) -> Optional[DataSource]:
        """获取指定数据源"""
        with self._lock:
            return self._sources.get(source_id)

    def get_all_status(self) -> Dict[str, dict]:
        """获取所有数据源状态"""
        with self._lock:
            return {sid: src.get_status() for sid, src in self._sources.items()}

    def set_active_source(self, source_id: str) -> bool:
        """切换 UI 当前显示的数据源"""
        with self._lock:
            if source_id in self._sources:
                self._active_source_id = source_id
                self.active_source_changed.emit(source_id)
                self._data_bus.publish_active_source_changed(source_id)
                return True
            return False

    @property
    def active_source(self) -> Optional[DataSource]:
        """当前激活的数据源"""
        if self._active_source_id:
            return self._sources.get(self._active_source_id)
        return None

    @property
    def active_source_id(self) -> Optional[str]:
        """当前激活的数据源 ID"""
        return self._active_source_id

    @property
    def source_count(self) -> int:
        """数据源总数"""
        return len(self._sources)

    def has_running_source(self) -> bool:
        """是否有数据源正在运行"""
        return any(s.status == DataSourceStatus.RUNNING for s in self._sources.values())

    # ── 硬件发现 ─────────────────────────────────────

    def _discover_hardware(self) -> list:
        """UDP 广播发现局域网内 FPGA 设备

        协议:
          1. 发送广播包: {"cmd": "discover", "port": 7001}
          2. 设备响应: {"device_id", "host", "port", "type", "channels"}
          3. 等待 3 秒收集所有响应

        Returns:
            DeviceConnectionConfig 列表
        """
        import json

        from core.communication.hardware_config import DeviceConnectionConfig

        BROADCAST_ADDR = "255.255.255.255"
        DISCOVERY_PORT = 7001
        TIMEOUT_S = 3.0
        PAYLOAD = json.dumps({"cmd": "discover", "port": DISCOVERY_PORT}).encode("utf-8")

        discovered: list = []

        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(TIMEOUT_S)
            sock.bind(("", 0))
            sock.sendto(PAYLOAD, (BROADCAST_ADDR, DISCOVERY_PORT))

            deadline = time.time() + TIMEOUT_S
            while time.time() < deadline:
                try:
                    data, (remote_host, _remote_port) = sock.recvfrom(4096)
                except socket.timeout:
                    break

                try:
                    info = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                    logger.debug("忽略无效发现响应: %s - %s", remote_host, e)
                    continue

                if not all(k in info for k in ("device_id", "host", "port")):
                    logger.debug("发现响应缺少必要字段: %s", info)
                    continue

                try:
                    cfg = DeviceConnectionConfig(
                        device_id=info["device_id"],
                        name=info.get("name", info["device_id"]),
                        host=info.get("host", remote_host),
                        udp_port=int(info["port"]),
                        tcp_port=int(info.get("tcp_port", 5000)),
                        coupling_type=info.get("type", "uhf"),
                        channel_count=int(info.get("channels", 1)),
                    )
                    discovered.append(cfg)
                    logger.info("发现设备: %s @ %s:%d", cfg.device_id, cfg.host, cfg.udp_port)
                except (KeyError, ValueError, TypeError) as e:
                    logger.warning("解析设备配置失败: %s - %s", remote_host, e)

        except (socket.error, OSError, TimeoutError) as e:
            logger.warning("UDP 广播发现失败: %s", e)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        return discovered

    def _load_devices_from_db(self) -> list:
        """从 SQLite 加载已保存的设备配置

        Returns:
            DeviceConnectionConfig 列表
        """
        if not self._db_manager:
            return []
        try:
            from core.communication.hardware_config import DeviceConnectionConfig
            from core.data.repository.device_repository import PDDeviceRepository

            repo = PDDeviceRepository(self._db_manager)
            devices = repo.get_all_devices()
            configs = []
            for dev in devices:
                ch_count = len(dev.channels) if dev.channels else dev.channel_count
                coupling = dev.channels[0].coupling_type if dev.channels else "uhf"
                configs.append(
                    DeviceConnectionConfig(
                        device_id=dev.id,
                        name=dev.name,
                        host=dev.host or "0.0.0.0",
                        udp_port=dev.udp_port or 6000,
                        channel_count=ch_count,
                        coupling_type=coupling,
                        sample_rate_hz=dev.sample_rate_hz or 100_000_000,
                    )
                )
            logger.info("从数据库加载 %d 个设备配置", len(configs))
            return configs
        except Exception as e:
            logger.warning("数据库设备配置加载失败: %s", e, exc_info=True)
            # 数据库查询可能抛出各种异常，这里做兜底处理
            return []

    # ── 配置持久化 ───────────────────────────────────

    def load_config(self, config: DataSourceConfig) -> None:
        """从 DataSourceConfig 加载已保存设备列表"""
        with self._lock:
            for saved in config.saved_devices:
                if not saved.device_id:
                    continue
                # 构建 DeviceConnectionConfig
                try:
                    from core.communication.hardware_config import DeviceConnectionConfig

                    dev_cfg = DeviceConnectionConfig(
                        device_id=saved.device_id,
                        name=saved.name,
                        host=saved.host,
                        udp_port=saved.udp_port,
                        channel_count=saved.channel_count,
                        coupling_type=saved.coupling_type,
                        sample_rate_hz=saved.sample_rate_hz,
                    )
                    self.add_hardware_source(dev_cfg)
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning("加载已保存设备 %s 失败: %s", saved.device_id, e)
