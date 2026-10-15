# -*- coding: utf-8 -*-
from __future__ import annotations

"""
传感器类型定义

统一管理所有传感器类型枚举、能力声明和默认参数。
用于数据采集分发、处理管线选择、UI 路由等场景。
"""


from enum import Enum
from typing import Dict, FrozenSet


class SensorType(Enum):
    """传感器类型枚举"""

    UHF = "uhf"  # 超高频 (300MHz ~ 3GHz)
    AE = "ae"  # 超声波/声发射 (20kHz ~ 1MHz)
    HFCT = "hfct"  # 高频电流互感器
    TEV = "tev"  # 暂态地电压

    @classmethod
    def from_coupling(cls, coupling_type: str) -> "SensorType":
        """从耦合类型字符串解析传感器类型"""
        mapping = {
            "uhf": cls.UHF,
            "ae": cls.AE,
            "hfct": cls.HFCT,
            "tev": cls.TEV,
        }
        return mapping.get(coupling_type.lower(), cls.UHF)


class SensorCapability(Enum):
    """传感器能力声明"""

    PHASE_RESOLVED = "phase_resolved"  # 支持相位分辨 (PRPD/PRPS)
    TIME_SERIES = "time_series"  # 支持时间序列分析
    SPECTRUM = "spectrum"  # 支持频谱分析 (FFT)
    LOCALIZATION = "localization"  # 支持定位
    HIT_DETECTION = "hit_detection"  # 支持撞击检测 (AE)


# 每种传感器的能力集合
SENSOR_CAPABILITIES: Dict[SensorType, FrozenSet[SensorCapability]] = {
    SensorType.UHF: frozenset(
        {
            SensorCapability.PHASE_RESOLVED,
            SensorCapability.SPECTRUM,
            SensorCapability.TIME_SERIES,
        }
    ),
    SensorType.AE: frozenset(
        {
            SensorCapability.HIT_DETECTION,
            SensorCapability.TIME_SERIES,
            SensorCapability.LOCALIZATION,
            SensorCapability.SPECTRUM,
        }
    ),
    SensorType.HFCT: frozenset(
        {
            SensorCapability.PHASE_RESOLVED,
            SensorCapability.SPECTRUM,
            SensorCapability.TIME_SERIES,
        }
    ),
    SensorType.TEV: frozenset(
        {
            SensorCapability.TIME_SERIES,
            SensorCapability.SPECTRUM,
        }
    ),
}


# 传感器默认参数
SENSOR_DEFAULTS: Dict[SensorType, dict] = {
    SensorType.UHF: {
        "sample_rate_hz": 100_000_000,
        "frequency_min_hz": 300_000_000,
        "frequency_max_hz": 3_000_000_000,
        "alarm_thresholds": {"critical": 80.0, "high": 60.0, "medium": 40.0, "low": 20.0},
        "pipeline": ["peak_detect", "fft", "prpd", "prps"],
    },
    SensorType.AE: {
        "sample_rate_hz": 2_000_000,
        "frequency_min_hz": 20_000,
        "frequency_max_hz": 200_000,
        "alarm_thresholds": {"critical": 100.0, "high": 70.0, "medium": 45.0, "low": 25.0},
        "pipeline": ["hit_detect", "param_extract", "envelope"],
    },
    SensorType.HFCT: {
        "sample_rate_hz": 100_000_000,
        "frequency_min_hz": 1_000_000,
        "frequency_max_hz": 50_000_000,
        "alarm_thresholds": {"critical": 80.0, "high": 60.0, "medium": 40.0, "low": 20.0},
        "pipeline": ["peak_detect", "fft", "prpd", "prps"],
    },
    SensorType.TEV: {
        "sample_rate_hz": 50_000_000,
        "frequency_min_hz": 1_000_000,
        "frequency_max_hz": 80_000_000,
        "alarm_thresholds": {"critical": 80.0, "high": 60.0, "medium": 40.0, "low": 20.0},
        "pipeline": ["peak_detect", "fft"],
    },
}


def has_capability(sensor_type: SensorType, cap: SensorCapability) -> bool:
    """检查传感器是否支持指定能力"""
    return cap in SENSOR_CAPABILITIES.get(sensor_type, frozenset())


def get_sensor_defaults(sensor_type: SensorType) -> dict:
    """获取传感器默认参数"""
    return SENSOR_DEFAULTS.get(sensor_type, SENSOR_DEFAULTS[SensorType.UHF]).copy()
