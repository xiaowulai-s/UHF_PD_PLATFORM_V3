# -*- coding: utf-8 -*-
"""
硬件数据源 - HardwareSource

封装单个 FPGA 设备的 UDP 连接 + 协议解析 + 健康检测。
实现 DataSource 抽象接口，可被 DataSourceManager 统一管理。

数据流:
    FPGA 硬件 → UDPDriver → FpgaProtocol → AcquisitionService
"""

from __future__ import annotations

import logging
import struct
import threading
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QTimer

from core.communication.fpga_protocol import FpgaProtocol
from core.communication.udp_driver import UDPDriver
from core.foundation.data_source import DataSource, DataSourceStatus, DataSourceType

if TYPE_CHECKING:
    from core.communication.hardware_config import DeviceConnectionConfig

logger = logging.getLogger(__name__)


class HardwareSource(DataSource):
    """FPGA 硬件数据源 - 封装单设备的 UDP 连接 + 协议解析 + 健康检测"""

    def __init__(
        self,
        config: "DeviceConnectionConfig",
        acquisition,
        data_bus,
        max_reconnect: int = 3,
    ):
        super().__init__()
        self._config = config
        self._acquisition = acquisition
        self._data_bus = data_bus
        self._driver: Optional[UDPDriver] = None
        self._protocol = FpgaProtocol()
        self._status = DataSourceStatus.STOPPED
        self._health_timer: Optional[QTimer] = None
        self._reconnect_count = 0
        self._max_reconnect = max_reconnect
        self._stats = {"packets": 0, "errors": 0, "bytes": 0, "reconnects": 0}
        self._stats_lock = threading.Lock()

        # 预设通道耦合类型
        for ch_idx in range(config.channel_count):
            coupling = config.get_channel_config(ch_idx).coupling_type
            acquisition.set_channel_coupling_type(config.device_id, ch_idx, coupling)

    # ── DataSource 属性 ──────────────────────────────

    @property
    def id(self) -> str:
        return self._config.device_id

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.HARDWARE

    @property
    def sensor_type(self) -> str:
        return self._config.coupling_type

    @property
    def status(self) -> DataSourceStatus:
        return self._status

    # ── 生命周期 ─────────────────────────────────────

    def start(self) -> bool:
        """启动 UDP 连接"""
        if self._status == DataSourceStatus.RUNNING:
            return True

        buffer_size = 16384 if self._config.coupling_type == "ae" else 65536
        self._driver = UDPDriver(
            host="0.0.0.0",  # UDP 接收端绑定所有接口
            port=self._config.udp_port,
            buffer_size=buffer_size,
        )

        # 设置对端设备地址，用于发送控制指令（如启动/停止采集）
        # UDPDriver 默认 remote 为 127.0.0.1:6000，需根据实际设备配置覆盖
        self._driver.set_remote(self._config.host, self._config.udp_port)

        # 信号连接
        self._driver.set_on_data_callback(self._on_data_received)
        self._driver.connected.connect(self._on_connected)
        self._driver.disconnected.connect(self._on_disconnected)
        self._driver.connection_error.connect(self._on_error)

        if not self._driver.connect():
            self._status = DataSourceStatus.ERROR
            return False

        # 健康检测定时器（10 秒间隔）
        self._health_timer = QTimer()
        self._health_timer.timeout.connect(self._health_check)
        self._health_timer.start(10000)

        self._status = DataSourceStatus.RUNNING
        logger.info(
            "HardwareSource 已启动: %s [%s:%d]", self._config.device_id, self._config.host, self._config.udp_port
        )
        return True

    def stop(self) -> None:
        """停止 UDP 连接"""
        if self._health_timer:
            self._health_timer.stop()
            self._health_timer = None
        if self._driver:
            self._driver.disconnect()
            self._driver = None
        self._status = DataSourceStatus.STOPPED
        self._reconnect_count = 0
        logger.info("HardwareSource 已停止: %s", self._config.device_id)

    # ── 状态查询 ─────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "id": self._config.device_id,
            "name": self._config.name,
            "type": "hardware",
            "sensor": self._config.coupling_type,
            "host": self._config.host,
            "port": self._config.udp_port,
            "status": self._status.value,
            "channels": self._config.channel_count,
            "packets": self._stats["packets"],
            "errors": self._stats["errors"],
            "reconnects": self._stats["reconnects"],
        }

    def get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)

    # ── 数据接收 ─────────────────────────────────────

    def _on_data_received(self, data: bytes) -> None:
        """UDP 数据 → FpgaProtocol 解析 → AcquisitionService"""
        try:
            frames = self._protocol.feed(data)
            with self._stats_lock:
                self._stats["packets"] += 1
                self._stats["bytes"] += len(data)
            for frame in frames:
                self._acquisition.process_data_frame(self._config.device_id, frame.channel_id, frame.data)
        except (ValueError, TypeError, KeyError, struct.error) as e:
            with self._stats_lock:
                self._stats["errors"] += 1
            logger.error("HardwareSource 数据解析失败 - 数据格式错误: %s", e, exc_info=True)
        except Exception as e:
            with self._stats_lock:
                self._stats["errors"] += 1
            logger.error("HardwareSource 数据解析失败: %s", e, exc_info=True)

    # ── 连接事件 ─────────────────────────────────────

    def _on_connected(self) -> None:
        self._status = DataSourceStatus.RUNNING
        logger.info("HardwareSource 已连接: %s", self._config.device_id)

    def _on_disconnected(self) -> None:
        self._status = DataSourceStatus.ERROR
        logger.warning("HardwareSource 连接断开: %s", self._config.device_id)
        self._attempt_reconnect()

    def _on_error(self, error_msg: str) -> None:
        logger.error("HardwareSource 错误: %s - %s", self._config.device_id, error_msg)
        self.error_occurred.emit(self._config.device_id, error_msg)

    # ── 健康检测 ─────────────────────────────────────

    def _health_check(self) -> None:
        """定期健康检测

        UDP 是无连接协议，`is_connected` 仅表示 socket 已绑定，无法反映对端是否在线。
        因此通过 `last_packet_time` 判断数据流是否中断：超过 30 秒未收到任何报文即视为断线。
        """
        if not self._driver:
            return

        # socket 已关闭 → 直接判定断线
        if not self._driver.is_connected:
            self._handle_health_failure("连接已关闭")
            return

        # 检查数据流活跃度
        last_packet = self._driver.get_stats().get("last_packet_time")
        if last_packet is None:
            # 启动后从未收到数据，但 socket 正常，可能是设备尚未发送
            return

        import time as _time

        silence_seconds = _time.time() - last_packet
        if silence_seconds > 30:
            self._handle_health_failure(f"数据流中断 {silence_seconds:.1f}s (超过 30s 阈值)")

    def _handle_health_failure(self, reason: str) -> None:
        """健康检测失败统一处理"""
        self._status = DataSourceStatus.ERROR
        self.status_changed.emit(self._config.device_id, "error")
        self.error_occurred.emit(self._config.device_id, reason)
        logger.warning("HardwareSource 健康检测失败: %s - %s", self._config.device_id, reason)
        self._attempt_reconnect()

    def _attempt_reconnect(self) -> None:
        """指数退避重连（最多 3 次）"""
        if self._reconnect_count >= self._max_reconnect:
            logger.error("HardwareSource 重连失败: %s (已达上限 %d)", self._config.device_id, self._max_reconnect)
            return

        self._reconnect_count += 1
        delay = min(2**self._reconnect_count, 10)  # 2s, 4s, 8s
        self._status = DataSourceStatus.RECONNECTING
        logger.info(
            "HardwareSource 重连 %s (%d/%d) 等待 %ds",
            self._config.device_id,
            self._reconnect_count,
            self._max_reconnect,
            delay,
        )

        QTimer.singleShot(delay * 1000, self._do_reconnect)

    def _do_reconnect(self) -> None:
        """执行重连"""
        # 先断开旧连接，释放 socket 和接收线程，否则 connect() 会因 _is_connected=True 直接返回
        if self._driver:
            try:
                self._driver.disconnect()
            except (OSError, RuntimeError) as e:
                logger.warning("重连前断开旧连接异常: %s", e)
            except Exception as e:
                logger.warning("重连前断开旧连接异常（其他）: %s", e, exc_info=True)

        if self._driver and self._driver.connect():
            self._status = DataSourceStatus.RUNNING
            self._reconnect_count = 0
            with self._stats_lock:
                self._stats["reconnects"] += 1
            self.status_changed.emit(self._config.device_id, "running")
            logger.info("HardwareSource 重连成功: %s", self._config.device_id)
        else:
            logger.warning("HardwareSource 重连尝试失败: %s", self._config.device_id)
            self._attempt_reconnect()
