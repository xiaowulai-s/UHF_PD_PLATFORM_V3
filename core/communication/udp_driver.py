# -*- coding: utf-8 -*-
"""
UDP 通信驱动

用于接收 FPGA 采集主机通过 UDP 发送的高速数据。
支持：
- 多端口监听
- 广播/组播接收
- 线程安全的数据接收
- 统计信息
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from .base_driver import BaseDriver

logger = logging.getLogger(__name__)


class UDPDriver(BaseDriver):
    """
    UDP 通信驱动

    用于接收 FPGA 通过 UDP 数据通道发送的波形、局放事件、FFT 等数据。

    Args:
        host: 绑定地址，默认 0.0.0.0（所有接口）
        port: 绑定端口，默认 6000
        buffer_size: 接收缓冲区大小 (字节)，默认 65536
        multicast_group: 可选的组播组地址
    """

    # 额外信号
    data_received_raw = Signal(bytes)  # 原始数据接收信号

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 6000,
        buffer_size: int = 65536,
        multicast_group: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._host = host
        self._port = port
        self._remote_host = "127.0.0.1"  # 目标设备地址（与绑定地址分离）
        self._remote_port = 6000
        self._buffer_size = buffer_size
        self._multicast_group = multicast_group

        self._socket: Optional[socket.socket] = None
        self._receive_thread: Optional[threading.Thread] = None
        self._is_running = False

        # 线程安全锁
        self._lock = threading.RLock()

        # 回调
        self._on_data_callback: Optional[Callable[[bytes], None]] = None

        # 统计信息
        self._packets_received = 0
        self._bytes_received = 0
        self._packets_dropped = 0
        self._last_packet_time: Optional[float] = None
        self._receive_rate = 0.0  # bytes/s
        self._rate_calc_time = time.monotonic()
        self._rate_calc_bytes = 0

    # ── 连接管理 ─────────────────────────────────────

    def connect(self) -> bool:
        """
        绑定 UDP 端口并启动接收线程

        Returns:
            是否成功
        """
        with self._lock:
            if self._is_connected:
                logger.info("UDP 驱动已连接，跳过重复连接")
                return True

            try:
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                # 设置接收缓冲区大小
                try:
                    self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self._buffer_size * 2)
                except OSError:
                    pass

                # 设置超时（用于非阻塞检查）
                self._socket.settimeout(1.0)

                # 绑定端口
                self._socket.bind((self._host, self._port))
                logger.info("UDP 绑定 [%s:%d]", self._host, self._port)

                # 加入组播
                if self._multicast_group:
                    self._join_multicast(self._socket, self._multicast_group)

                self._is_connected = True
                self._is_running = True

                # 启动接收线程
                self._receive_thread = threading.Thread(
                    target=self._receive_loop,
                    name="UDPReceiver",
                    daemon=True,
                )
                self._receive_thread.start()

                self.connected.emit()
                logger.info("UDP 驱动启动成功 [%s:%d]", self._host, self._port)
                return True

            except (OSError, socket.error) as e:
                logger.error("UDP 绑定失败 [%s:%d]: %s", self._host, self._port, e)
                self.error_occurred.emit(f"UDP 启动失败: {str(e)}")
                self._cleanup_socket()
                return False

    def disconnect(self) -> None:
        """断开 UDP 连接"""
        with self._lock:
            self._is_running = False
            self._is_connected = False
            self._cleanup_socket()

        # 等待接收线程结束
        if self._receive_thread and self._receive_thread.is_alive():
            self._receive_thread.join(timeout=2.0)
            if self._receive_thread.is_alive():
                logger.warning("UDP 接收线程未能在超时时间内退出")

        self._receive_thread = None
        self.disconnected.emit()
        logger.info("UDP 驱动已断开")

    def send_data(self, data: bytes) -> bool:
        """
        发送 UDP 数据包到远程设备

        Args:
            data: 待发送数据

        Returns:
            是否成功
        """
        with self._lock:
            if not self._socket or not self._is_connected:
                return False

            try:
                sent = self._socket.sendto(data, (self._remote_host, self._remote_port))
                self.data_sent.emit(data)
                return sent == len(data)
            except (OSError, socket.error) as e:
                logger.error("UDP 发送到 [%s:%d] 失败: %s", self._remote_host, self._remote_port, e)
                return False

    def send_to(self, data: bytes, host: str, port: int) -> bool:
        """
        发送 UDP 数据包到指定地址

        Args:
            data: 待发送数据
            host: 目标地址
            port: 目标端口

        Returns:
            是否成功
        """
        with self._lock:
            if not self._socket or not self._is_connected:
                return False

            try:
                self._socket.sendto(data, (host, port))
                self.data_sent.emit(data)
                return True
            except (OSError, socket.error) as e:
                logger.error("UDP 发送到 [%s:%d] 失败: %s", host, port, e)
                return False

    # ── 接收 ─────────────────────────────────────────

    def _receive_loop(self) -> None:
        """UDP 数据接收循环"""
        logger.info("UDP 接收线程启动")

        while self._is_running:
            try:
                if not self._socket:
                    break

                data, addr = self._socket.recvfrom(self._buffer_size)

                if data:
                    self._on_packet_received(data, addr)

            except socket.timeout:
                continue
            except (OSError, socket.error) as e:
                if self._is_running:
                    logger.warning("UDP 接收错误: %s", e)
                    self.error_occurred.emit(f"UDP 接收错误: {str(e)}")
                break
            except Exception as e:
                if self._is_running:
                    logger.exception("UDP 接收异常: %s", e)
                break

        logger.info("UDP 接收线程结束")

    def _on_packet_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """处理接收到的 UDP 数据包"""
        self._packets_received += 1
        self._bytes_received += len(data)
        self._last_packet_time = time.time()

        # 计算接收速率
        now = time.monotonic()
        self._rate_calc_bytes += len(data)
        elapsed = now - self._rate_calc_time
        if elapsed >= 1.0:
            self._receive_rate = self._rate_calc_bytes / elapsed
            self._rate_calc_bytes = 0
            self._rate_calc_time = now

        # 追加到缓冲区
        self._append_to_buffer(data)

        # 发射信号
        self.data_received.emit(data)
        self.data_received_raw.emit(data)

        # 回调通知
        if self._on_data_callback:
            try:
                self._on_data_callback(data)
            except Exception as e:
                logger.error("数据回调执行失败: %s", e)

    # ── 配置 ─────────────────────────────────────────

    def set_on_data_callback(self, callback: Optional[Callable[[bytes], None]]) -> None:
        """设置数据接收回调"""
        self._on_data_callback = callback

    def set_remote(self, host: str, port: int) -> None:
        """
        设置远程设备地址（发送目标，与绑定地址分离）

        Args:
            host: 远程设备 IP
            port: 远程设备端口
        """
        self._remote_host = host
        self._remote_port = port

    def set_host_port(self, host: str, port: int) -> None:
        """
        设置主机地址和端口（需在 connect 前调用）

        Args:
            host: 绑定地址
            port: 绑定端口
        """
        self._host = host
        self._port = port

    # ── 统计 ─────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "packets_received": self._packets_received,
            "bytes_received": self._bytes_received,
            "packets_dropped": self._packets_dropped,
            "receive_rate_kbps": self._receive_rate / 1024,
            "last_packet_time": self._last_packet_time,
            "is_connected": self._is_connected,
            "host": self._host,
            "port": self._port,
            "buffer_size": self._buffer_size,
            "buffer_usage": len(self._buffer) if hasattr(self, "_buffer") else 0,
        }

    def reset_stats(self) -> None:
        """重置统计信息"""
        self._packets_received = 0
        self._bytes_received = 0
        self._packets_dropped = 0
        self._receive_rate = 0.0
        self._rate_calc_bytes = 0
        self._rate_calc_time = time.monotonic()

    # ── 内部辅助 ─────────────────────────────────────

    def _join_multicast(self, sock: socket.socket, group: str) -> None:
        """加入组播组"""
        try:
            group_bin = socket.inet_aton(group)
            mreq = struct.pack("4s4s", group_bin, socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            logger.info("已加入组播组: %s", group)
        except (OSError, socket.error) as e:
            logger.warning("加入组播组失败 [%s]: %s", group, e)

    def _cleanup_socket(self) -> None:
        """清理 socket"""
        if self._socket:
            try:
                self._socket.close()
            except OSError as e:
                logger.debug("Socket 关闭时出错: %s", e)
            self._socket = None

    def __repr__(self) -> str:
        return (
            f"UDPDriver(bound=[{self._host}:{self._port}], "
            f"connected={self._is_connected}, "
            f"packets={self._packets_received})"
        )
