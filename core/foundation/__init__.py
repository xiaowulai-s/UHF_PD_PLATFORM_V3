# -*- coding: utf-8 -*-
"""
Foundation层 - 系统基础设施

提供全局共享的核心机制:
- DataBus: 事件总线（模块间解耦通信）
- ConfigStore: 配置中心（统一配置入口）
- PluginRegistry: 插件注册表（设备扩展机制）
"""

from core.foundation.data_bus import DataBus, DeadbandFilter, SubscriptionManager
from core.foundation.config_store import ConfigStore
from core.foundation.plugin_registry import PluginRegistry, DevicePlugin

# Signal Bus（信号总线）
try:
    from core.foundation.signal_bus import PDDataBus, PDDataBusSignals
except ImportError:
    PDDataBus = None  # type: ignore
    PDDataBusSignals = None  # type: ignore

__all__ = [
    'DataBus', 'DeadbandFilter', 'SubscriptionManager', 'ConfigStore',
    'PluginRegistry', 'DevicePlugin',
    'PDDataBus', 'PDDataBusSignals',
]
