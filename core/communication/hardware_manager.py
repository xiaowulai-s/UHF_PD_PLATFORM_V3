# -*- coding: utf-8 -*-
"""
硬件连接管理器 - HardwareManager

管理多个 FPGA 设备的 UDP 连接生命周期：
- 每个设备一个 UDPDriver + FpgaProtocol
- 自动路由解析后的数据帧到 AcquisitionService
- 按设备配置设置通道耦合类型

数据流:
    FPGA 硬件
      ↓ (UDP packets)
    UDPDriver
      ↓ data_received(bytes)
    FpgaProtocol.feed(bytes) → List[DataFrame]
      ↓ 逐帧分派
    AcquisitionService.process_data_frame(dev_id, ch_id, parsed_data)
      ↓ coupling_type 感知
    _process_uhf_waveform() / _process_ae_waveform()
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from core.communication.fpga_protocol import FpgaProtocol
from core.communication.udp_driver import UDPDriver
from core.services.acquisition_service import AcquisitionService

from .hardware_config import DeviceConnectionConfig

logger = logging.getLogger(__name__)


class HardwareManager(QObject):
    """
    硬件连接管理器

    Args:
        acquisition_service: 数据采集服务
        parent: Qt 父对象
    """

    # 信号
    device_connected = Signal(str)  # device_id
    device_disconnected = Signal(str)  # device_id
    connection_error = Signal(str, str)  # device_id, error_msg

    def __init__(
        self,
        acquisition_service: AcquisitionService,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._acquisition = acquisition_service

        # {device_id: {"driver": UDPDriver, "protocol": FpgaProtocol, "config": DeviceConnectionConfig}}
        self._devices: Dict[str, Dict[str, Any]] = {}

    # ── 设备管理 ─────────────────────────────────────

    def add_device(self, config: DeviceConnectionConfig) -> bool:
        """
        添加并连接一个 FPGA 设备

        创建 UDPDriver + FpgaProtocol 并启动接收。

        Args:
            config: 设备连接配置

        Returns:
            是否成功
        """
        if config.device_id in self._devices:
            logger.warning("设备 %s 已存在", config.device_id)
            return False

        # 每个设备可能需要不同的 buffer_size (AE 帧较小, UHF 帧较大)
        # UHF 使用更大缓冲区，AE 正常即可
        buffer_size = 65536 if config.coupling_type == "uhf" else 16384

        # 创建 UDP 驱动
        driver = UDPDriver(
            host=config.host,
            port=config.udp_port,
            buffer_size=buffer_size,
        )

        # 创建协议解析器
        protocol = FpgaProtocol()

        # 配置通道耦合类型
        for ch in range(config.channel_count):
            ch_cfg = config.get_channel_config(ch)
            self._acquisition.set_channel_coupling_type(config.device_id, ch, ch_cfg.coupling_type)

        # 连接驱动 → 协议解析 → 采集服务
        driver.set_on_data_callback(
            lambda data, dev_id=config.device_id, proto=protocol: (self._on_data_received(dev_id, proto, data))
        )

        # 驱动信号连接
        driver.connected.connect(lambda: self._on_device_connected(config.device_id))
        driver.disconnected.connect(lambda: self._on_device_disconnected(config.device_id))
        driver.error_occurred.connect(lambda msg, dev_id=config.device_id: self._on_device_error(dev_id, msg))

        self._devices[config.device_id] = {
            "driver": driver,
            "protocol": protocol,
            "config": config,
        }

        logger.info(
            "设备 %s [%s:%d, %s] 已注册",
            config.device_id,
            config.host,
            config.udp_port,
            config.coupling_type.upper(),
        )
        return True

    def remove_device(self, device_id: str) -> None:
        """移除并断开设备"""
        device = self._devices.pop(device_id, None)
        if device is None:
            return

        try:
            device["driver"].disconnect()
        except Exception as e:
            logger.debug("断开设备 %s 驱动时异常: %s", device_id, e)

        logger.info("设备 %s 已移除", device_id)

    def start_all(self) -> int:
        """
        启动所有已注册设备的 UDP 连接

        Returns:
            成功连接数
        """
        success = 0
        for dev_id, device in self._devices.items():
            driver: UDPDriver = device["driver"]
            if driver.connect():
                success += 1
                logger.info("设备 %s UDP 连接成功 [%s:%d]", dev_id, driver._host, driver._port)
            else:
                logger.error("设备 %s UDP 连接失败 [%s:%d]", dev_id, driver._host, driver._port)
                self.connection_error.emit(dev_id, f"UDP 连接失败 {driver._host}:{driver._port}")
        return success

    def stop_all(self) -> None:
        """断开所有设备连接"""
        for dev_id in list(self._devices.keys()):
            self.remove_device(dev_id)

    # ── 数据路由 ─────────────────────────────────────

    def _on_data_received(self, device_id: str, protocol: FpgaProtocol, data: bytes) -> None:
        """
        UDP 数据回调 → 协议解析 → 采集服务

        Args:
            device_id: 原始设备 ID（创建时闭包绑定）
            protocol: 对应的协议解析器
            data: 原始字节数据
        """
        try:
            frames = protocol.feed(data)
            for frame in frames:
                # 优先使用帧内携带的设备 ID，否则用配置中的 device_id
                dev_id = str(frame.device_id) if frame.device_id else device_id
                self._acquisition.process_data_frame(
                    dev_id,
                    frame.channel_id,
                    frame.data,
                )
        except Exception as e:
            logger.error("[%s] 数据帧解析/处理异常: %s", device_id, e)

    # ── 信号处理 ─────────────────────────────────────

    def _on_device_connected(self, device_id: str) -> None:
        logger.info("设备 %s 已连接", device_id)
        self.device_connected.emit(device_id)

    def _on_device_disconnected(self, device_id: str) -> None:
        logger.info("设备 %s 已断开", device_id)
        self.device_disconnected.emit(device_id)

    def _on_device_error(self, device_id: str, error_msg: str) -> None:
        logger.error("设备 %s 异常: %s", device_id, error_msg)
        self.connection_error.emit(device_id, error_msg)

    # ── 状态查询 ─────────────────────────────────────

    @property
    def device_ids(self) -> List[str]:
        """获取所有已注册设备 ID"""
        return list(self._devices.keys())

    def get_device_status(self, device_id: str) -> Optional[dict]:
        """获取设备运行状态"""
        device = self._devices.get(device_id)
        if device is None:
            return None

        driver: UDPDriver = device["driver"]
        protocol: FpgaProtocol = device["protocol"]
        config: DeviceConnectionConfig = device["config"]

        return {
            "device_id": config.device_id,
            "name": config.name,
            "coupling_type": config.coupling_type,
            "host": driver._host,
            "port": driver._port,
            "connected": driver._is_connected,
            "driver_stats": driver.get_stats(),
            "protocol_stats": protocol.stats,
        }

    def get_all_status(self) -> List[dict]:
        """获取所有设备状态"""
        return [self.get_device_status(did) for did in self._devices if self.get_device_status(did)]

    # ── 清理 ─────────────────────────────────────────

    def shutdown(self) -> None:
        """关闭所有设备"""
        self.stop_all()
        logger.info("HardwareManager 已关闭")
