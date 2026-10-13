# -*- coding: utf-8 -*-
"""
Qt工具函数
Qt Helper Functions - Safe signal operations and object validity checks
"""

import logging
from typing import Callable

logger = logging.getLogger(__name__)

try:
    import shiboken6

    SHIBOKEN_AVAILABLE = True
except ImportError:
    SHIBOKEN_AVAILABLE = False


def is_valid_qt_object(obj) -> bool:
    """检查Qt对象是否有效（未被C++层销毁）"""
    if not SHIBOKEN_AVAILABLE:
        return obj is not None
    try:
        return shiboken6.isValid(obj)
    except (NameError, RuntimeError, AttributeError):
        return False


def safe_disconnect(signal) -> bool:
    """安全断开Qt信号连接"""
    try:
        if signal is not None:
            signal.disconnect()
            return True
    except (RuntimeError, TypeError, AttributeError):
        pass
    return False


def safe_connect(signal, slot: Callable) -> bool:
    """安全连接Qt信号"""
    try:
        if signal is not None:
            signal.connect(slot)
            return True
    except (RuntimeError, TypeError):
        pass
    return False


def safe_reconnect(signal, slot: Callable) -> bool:
    """安全断开旧连接并重新连接信号"""
    safe_disconnect(signal)
    return safe_connect(signal, slot)
