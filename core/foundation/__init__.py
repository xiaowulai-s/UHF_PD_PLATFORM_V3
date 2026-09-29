# -*- coding: utf-8 -*-
"""
Foundation层 - 系统基础设施

提供全局共享的核心机制:
- DataBus: 事件总线（模块间解耦通信）
- ConfigStore: 配置中心（统一配置入口）
- SignalBus: PD专用信号总线（已整合到foundation层）
"""

from core.foundation.config_store import ConfigStore
from core.foundation.data_bus import DataBus, DeadbandFilter, SubscriptionManager

# Signal Bus（信号总线）
try:
    from core.foundation.signal_bus import PDDataBus, PDDataBusSignals
except ImportError:
    PDDataBus = None  # type: ignore
    PDDataBusSignals = None  # type: ignore

__all__ = [
    "DataBus",
    "DeadbandFilter",
    "SubscriptionManager",
    "ConfigStore",
    "PDDataBus",
    "PDDataBusSignals",
]
