# -*- coding: utf-8 -*-
"""
FPGA 协议数据结构 - ProtocolDataStructures

从 fpga_protocol.py 拆分为独立模块。
包含所有数据类和枚举定义。

数据结构:
- DataType: 数据类型枚举
- DataFrame: 解析后的数据帧
- WaveformData / AEWaveformData: 波形数据
- FFTData: FFT 频谱数据
- PRPDData: PRPD 图谱数据
- PRPSData: PRPS 图谱数据
- DeviceStatusData: 设备状态数据
- AEParametersData: AE 参数数据
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Dict, List


class DataType(IntEnum):
    """数据类型枚举"""

    WAVEFORM = 0x01  # 波形数据
    PD_EVENT = 0x02  # 局放事件
    FFT_RESULT = 0x03  # FFT 频谱
    PRPD_RESULT = 0x04  # PRPD 图谱
    PRPS_RESULT = 0x05  # PRPS 图谱
    DEVICE_STATUS = 0x06  # 设备状态
    AE_WAVEFORM = 0x07  # AE 波形数据
    AE_PARAMETERS = 0x08  # AE 参数数据


# 数据类型名称映射
DATA_TYPE_NAMES: Dict[int, str] = {
    DataType.WAVEFORM: "波形数据",
    DataType.PD_EVENT: "局放事件",
    DataType.FFT_RESULT: "FFT频谱",
    DataType.PRPD_RESULT: "PRPD图谱",
    DataType.PRPS_RESULT: "PRPS图谱",
    DataType.DEVICE_STATUS: "设备状态",
    DataType.AE_WAVEFORM: "AE波形",
    DataType.AE_PARAMETERS: "AE参数",
}


@dataclass
class DataFrame:
    """解析后的数据帧"""

    frame_type: DataType
    device_id: int
    channel_id: int
    timestamp: float  # Unix timestamp
    data: Any  # 解析后的数据
    raw_length: int = 0

    @property
    def type_name(self) -> str:
        return DATA_TYPE_NAMES.get(self.frame_type, f"未知(0x{self.frame_type:02X})")

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]


@dataclass
class WaveformData:
    """波形数据"""

    samples: List[float]  # 采样点幅值 (mV)
    sample_rate: int  # 采样率 (Hz)
    trigger_position: int = 0  # 触发点位置


@dataclass
class FFTData:
    """FFT 频谱数据"""

    frequencies: List[float]  # 频率 (Hz)
    magnitudes: List[float]  # 幅值
    peak_frequencies: List[float] = field(default_factory=list)  # 峰值频率
    peak_magnitudes: List[float] = field(default_factory=list)  # 峰值幅值
    sample_rate: int = 0


@dataclass
class PRPDData:
    """PRPD 图谱数据"""

    matrix: List[List[float]]  # 二维矩阵
    phase_bins: int = 360
    amplitude_bins: int = 256


@dataclass
class PRPSData:
    """PRPS 图谱数据"""

    matrix: List[List[float]]  # 二维矩阵
    rows: int = 512
    cols: int = 360


@dataclass
class DeviceStatusData:
    """设备状态数据"""

    status: int  # 0=离线, 1=在线, 2=告警, 3=错误
    temperature: float = 0.0  # 温度 (°C)
    humidity: float = 0.0  # 湿度 (%)
    voltage: float = 0.0  # 电压 (V)
    current: float = 0.0  # 电流 (A)
    adc_status: int = 0  # ADC 状态
    optical_status: int = 0  # 光模块状态
    sync_status: int = 0  # 同步状态
    memory_usage: float = 0.0  # 内存使用率 (%)
    cpu_usage: float = 0.0  # CPU 使用率 (%)
    uptime: int = 0  # 运行时间 (s)
    error_code: int = 0  # 错误码
    error_message: str = ""  # 错误信息


@dataclass
class AEWaveformData:
    """AE 波形数据"""

    samples: List[float]  # 采样点幅值 (mV)
    sample_rate: int  # 采样率 (Hz)
    trigger_position: int = 0  # 触发点位置


@dataclass
class AEParametersData:
    """AE 参数数据（预计算的 AE 特征）"""

    peak_amplitude: float = 0.0  # 峰值幅值 (mV)
    rise_time_us: float = 0.0  # 上升时间 (us)
    duration_us: float = 0.0  # 持续时间 (us)
    counts: int = 0  # 振铃计数
    marse_energy: float = 0.0  # MARSE 能量
    rms: float = 0.0  # RMS 值 (mV)
    arrival_time: float = 0.0  # 到达时间 (s)
    phase_deg: float = 0.0  # 工频相位 (0-360)
    avg_frequency_khz: float = 0.0  # 平均频率 (kHz)
