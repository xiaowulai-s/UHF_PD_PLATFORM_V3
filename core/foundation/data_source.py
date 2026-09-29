# -*- coding: utf-8 -*-
"""
数据源抽象层 - DataSource

定义所有数据源的统一接口，包括：
- DataSourceType: 数据源类型枚举
- DataSourceStatus: 数据源运行状态枚举
- DataSource: 抽象基类

所有数据源（硬件、模拟器、文件回放）必须实现此接口。
"""

from __future__ import annotations

from abc import abstractmethod
from enum import Enum

from PySide6.QtCore import QObject, Signal


class DataSourceType(Enum):
    """数据源类型"""

    HARDWARE = "hardware"  # FPGA 实际硬件
    SIMULATOR = "simulator"  # 内置模拟器
    FILE = "file"  # 文件回放 (未来)


class DataSourceStatus(Enum):
    """数据源运行状态"""

    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"
    RECONNECTING = "reconnecting"


class DataSource(QObject):
    """数据源抽象基类 - 所有数据源遵循的统一接口"""

    # 信号
    status_changed = Signal(str, str)  # source_id, status
    error_occurred = Signal(str, str)  # source_id, error_msg

    def __init__(self, parent=None):
        super().__init__(parent)

    @property
    @abstractmethod
    def id(self) -> str:
        """唯一标识"""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """显示名称"""
        ...

    @property
    @abstractmethod
    def source_type(self) -> DataSourceType:
        """数据源类型"""
        ...

    @property
    @abstractmethod
    def sensor_type(self) -> str:
        """传感器类型: 'uhf' / 'ae'"""
        ...

    @property
    @abstractmethod
    def status(self) -> DataSourceStatus:
        """当前状态"""
        ...

    @abstractmethod
    def start(self) -> bool:
        """启动数据源。返回 True 表示启动成功。"""
        ...

    @abstractmethod
    def stop(self) -> None:
        """停止数据源，释放资源。可安全重复调用。"""
        ...

    @abstractmethod
    def get_status(self) -> dict:
        """获取详细状态信息（用于 UI 显示）"""
        ...

    @abstractmethod
    def get_stats(self) -> dict:
        """获取运行统计（包数、错误数、吞吐量等）"""
        ...
