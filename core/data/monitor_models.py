# -*- coding: utf-8 -*-
"""UHF-AE PD Detector 数据库模型

局放监测专用数据模型，复用现有 Base 和 DatabaseManager。
所有模型自动被 DatabaseManager 的 create_all() 识别。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from .models import Base, utc_now


class PDDeviceModel(Base):
    """局放采集设备模型"""

    __tablename__ = "pd_devices"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    device_type = Column(String(32), default="fpga_acq")
    host = Column(String(64), nullable=True)
    tcp_port = Column(Integer, default=5000)
    udp_port = Column(Integer, default=6000)
    firmware_version = Column(String(32), nullable=True)
    status = Column(Integer, default=0)  # 0=离线, 1=在线, 2=告警, 3=错误
    channel_count = Column(Integer, default=4)
    sample_rate_hz = Column(Integer, default=100000000)
    use_simulator = Column(Boolean, default=False)
    last_connected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    channels = relationship("PDChannelModel", back_populates="device", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_pd_device_name", "name"),
        Index("idx_pd_device_status", "status"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "device_type": self.device_type,
            "host": self.host,
            "tcp_port": self.tcp_port,
            "udp_port": self.udp_port,
            "firmware_version": self.firmware_version,
            "status": self.status,
            "channel_count": self.channel_count,
            "sample_rate_hz": self.sample_rate_hz,
            "use_simulator": self.use_simulator,
            "last_connected_at": self.last_connected_at.isoformat() if self.last_connected_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PDChannelModel(Base):
    """局放采集通道模型"""

    __tablename__ = "pd_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), ForeignKey("pd_devices.id", ondelete="CASCADE"), nullable=False)
    channel_index = Column(Integer, nullable=False)  # 0-based
    name = Column(String(64), default="")
    enabled = Column(Boolean, default=True)
    status = Column(Integer, default=0)  # 0=正常, 1=异常
    coupling_type = Column(String(32), default="uhf")  # uhf, hfct, tev, ae
    sensor_type = Column(String(64), nullable=True)
    gain = Column(Float, default=1.0)
    attenuation_db = Column(Float, default=0)
    frequency_min_mhz = Column(Float, default=300)
    frequency_max_mhz = Column(Float, default=3000)
    frequency_min_hz = Column(Float, nullable=True)  # AE: 最低频率 Hz (如 20000)
    frequency_max_hz = Column(Float, nullable=True)  # AE: 最高频率 Hz (如 200000)
    ae_sample_rate_hz = Column(Integer, nullable=True)  # AE: 采样率 Hz (如 2000000)

    device = relationship("PDDeviceModel", back_populates="channels")

    __table_args__ = (
        Index("idx_pd_channel_device", "device_id"),
        Index("idx_pd_channel_index", "device_id", "channel_index", unique=True),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "channel_index": self.channel_index,
            "name": self.name,
            "enabled": self.enabled,
            "status": self.status,
            "coupling_type": self.coupling_type,
            "sensor_type": self.sensor_type,
            "gain": self.gain,
            "attenuation_db": self.attenuation_db,
            "frequency_min_mhz": self.frequency_min_mhz,
            "frequency_max_mhz": self.frequency_max_mhz,
            "frequency_min_hz": self.frequency_min_hz,
            "frequency_max_hz": self.frequency_max_hz,
            "ae_sample_rate_hz": self.ae_sample_rate_hz,
        }


class PDEventModel(Base):
    """局放事件记录"""

    __tablename__ = "pd_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), nullable=False, index=True)
    channel_id = Column(Integer, nullable=True)  # 逻辑通道号，非 FK（避免与 pd_channels 耦合）
    sensor_type = Column(String(16), default="uhf")  # uhf / ae / hfct / tev
    timestamp = Column(DateTime, default=utc_now, index=True)
    phase = Column(Float, nullable=True)  # 相位 0~360°
    amplitude = Column(Float, nullable=False)  # 幅值 mV
    energy = Column(Float, nullable=True)  # 放电能量 pC
    polarity = Column(Integer, nullable=True)  # 0=正, 1=负
    frequency_mhz = Column(Float, nullable=True)  # 主频 MHz
    bandwidth_mhz = Column(Float, nullable=True)
    signal_quality = Column(Integer, default=0)  # 0-100
    # AE 特有参数 (仅 AE 事件填充, UHF 事件为 null)
    rise_time_us = Column(Float, nullable=True)  # AE: 上升时间 us
    duration_us = Column(Float, nullable=True)  # AE: 持续时间 us
    counts = Column(Integer, nullable=True)  # AE: 振铃计数
    marse_energy = Column(Float, nullable=True)  # AE: MARSE 能量
    temperature = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    raw_data = Column(Text, nullable=True)  # JSON 原始数据

    __table_args__ = (
        Index("idx_pd_event_device_time", "device_id", "timestamp"),
        Index("idx_pd_event_amp", "amplitude"),
        Index("idx_pd_event_time", "timestamp"),
        Index("idx_pd_event_device_channel", "device_id", "channel_id"),
        Index("idx_pd_event_sensor_type", "sensor_type"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "channel_id": self.channel_id,
            "sensor_type": self.sensor_type,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "phase": self.phase,
            "amplitude": self.amplitude,
            "energy": self.energy,
            "polarity": self.polarity,
            "frequency_mhz": self.frequency_mhz,
            "bandwidth_mhz": self.bandwidth_mhz,
            "signal_quality": self.signal_quality,
            "rise_time_us": self.rise_time_us,
            "duration_us": self.duration_us,
            "counts": self.counts,
            "marse_energy": self.marse_energy,
        }


class PDTrendDataModel(Base):
    """局放趋势统计数据"""

    __tablename__ = "pd_trend_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), nullable=False, index=True)
    channel_id = Column(Integer, nullable=True)
    timestamp = Column(DateTime, default=utc_now, index=True)
    period = Column(String(16), default="1h")  # 1h, 24h, 7d, 30d
    pd_count = Column(Integer, default=0)  # 局放次数
    max_amplitude = Column(Float, default=0)  # 最大幅值
    avg_amplitude = Column(Float, default=0)  # 平均幅值
    min_amplitude = Column(Float, default=0)  # 最小幅值
    total_energy = Column(Float, default=0)  # 总放电能量
    noise_level = Column(Float, default=0)  # 噪声水平
    pulse_count = Column(Integer, default=0)  # 脉冲计数
    positive_ratio = Column(Float, default=0)  # 正极性比例
    ae_hit_count = Column(Integer, default=0)  # AE hit 计数

    __table_args__ = (
        Index("idx_pd_trend_device_time", "device_id", "timestamp"),
        Index("idx_pd_trend_period", "device_id", "period", "timestamp"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "channel_id": self.channel_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "period": self.period,
            "pd_count": self.pd_count,
            "max_amplitude": self.max_amplitude,
            "avg_amplitude": self.avg_amplitude,
            "min_amplitude": self.min_amplitude,
            "total_energy": self.total_energy,
            "noise_level": self.noise_level,
            "pulse_count": self.pulse_count,
            "positive_ratio": self.positive_ratio,
            "ae_hit_count": self.ae_hit_count,
        }


class PDAlarmModel(Base):
    """局放报警记录"""

    __tablename__ = "pd_alarms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), nullable=False, index=True)
    device_name = Column(String(128), nullable=True)
    channel_id = Column(Integer, nullable=True)
    alarm_type = Column(
        String(32), nullable=False
    )  # pd_over_limit, device_offline, optical_abnormal, adc_abnormal, sync_abnormal, storage_low
    level = Column(String(16), nullable=False)  # critical, warning, info
    amplitude = Column(Float, nullable=True)
    threshold = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=utc_now, index=True)
    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_pd_alarm_device_time", "device_id", "timestamp"),
        Index("idx_pd_alarm_level", "level"),
        Index("idx_pd_alarm_ack", "acknowledged"),
    )

    LEVEL_COLORS = {
        "critical": "#CF222E",
        "warning": "#D29922",
        "info": "#1A7F37",
    }

    LEVEL_NAMES = {
        "critical": "严重报警",
        "warning": "一般报警",
        "info": "提示报警",
    }

    ALARM_TYPE_NAMES = {
        "pd_over_limit": "局放超限",
        "device_offline": "设备离线",
        "optical_abnormal": "光模块异常",
        "adc_abnormal": "ADC异常",
        "sync_abnormal": "同步异常",
        "storage_low": "存储空间不足",
        "ae_signal_loss": "AE信号丢失",
        "ae_noise_floor": "AE噪声超限",
    }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "channel_id": self.channel_id,
            "alarm_type": self.alarm_type,
            "alarm_type_name": self.ALARM_TYPE_NAMES.get(self.alarm_type, self.alarm_type),
            "level": self.level,
            "level_name": self.LEVEL_NAMES.get(self.level, self.level),
            "level_color": self.LEVEL_COLORS.get(self.level, "#666"),
            "amplitude": self.amplitude,
            "threshold": self.threshold,
            "description": self.description,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "acknowledged_by": self.acknowledged_by,
        }
