# -*- coding: utf-8 -*-
"""
通信驱动基类

所有通信驱动（UDP、TCP、Serial）的抽象基类，
定义统一的连接管理、数据收发接口。
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class BaseDriver(QObject):
    """
    通信驱动基类

    提供统一的连接状态管理、信号定义和生命周期控制。
    子类需实现 connect() / disconnect() / send() 方法。
    """

    # ── 通用信号 ─────────────────────────────────────
    connected = Signal()  # 连接成功
    disconnected = Signal()  # 连接断开
    connection_error = Signal(str)  # 连接错误
    data_received = Signal(bytes)  # 数据接收

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._is_connected = False

    # ── 连接管理（子类实现）─────────────────────────

    def connect(self) -> bool:
        """建立连接，子类必须实现"""
        raise NotImplementedError

    def disconnect(self) -> None:
        """断开连接，子类必须实现"""
        raise NotImplementedError

    def send(self, data: bytes) -> bool:
        """发送数据，子类必须实现"""
        raise NotImplementedError

    # ── 通用属性 ─────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        """连接状态"""
        return self._is_connected

    @property
    def host(self) -> str:
        """主机地址"""
        return getattr(self, "_host", "")

    @property
    def port(self) -> int:
        """端口号"""
        return getattr(self, "_port", 0)
