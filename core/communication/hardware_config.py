# -*- coding: utf-8 -*-
from __future__ import annotations

"""
硬件设备连接配置数据类

描述 FPGA 采集设备的网络连接参数和通道配置。
用于 HardwareManager 创建设备连接。
"""


from dataclasses import dataclass, field
from typing import List


@dataclass
class ChannelConfig:
    """通道配置"""

    index: int  # 通道索引 (0-based)
    enabled: bool = True
    coupling_type: str = "uhf"  # uhf, hfct, tev, ae
    gain: float = 1.0
    attenuation_db: float = 0.0


@dataclass
class DeviceConnectionConfig:
    """
    FPGA 设备连接配置

    Args:
        device_id: 唯一设备 ID
        name: 显示名称
        host: UDP 绑定地址 (默认 0.0.0.0)
        udp_port: UDP 绑定端口 (6000=UHF, 7000=AE)
        channel_count: 通道数
        coupling_type: 默认耦合类型，所有通道使用此类型
        sample_rate_hz: 采样率 (Hz)
        ae_frequency_min_hz: AE 频段下限 (仅 AE 设备)
        ae_frequency_max_hz: AE 频段上限 (仅 AE 设备)
        channels: 逐通道配置 (可选，覆盖默认)
    """

    device_id: str
    name: str
    host: str = "0.0.0.0"
    udp_port: int = 6000
    channel_count: int = 4
    coupling_type: str = "uhf"
    sample_rate_hz: int = 100_000_000
    ae_frequency_min_hz: float | None = None
    ae_frequency_max_hz: float | None = None
    channels: List[ChannelConfig] = field(default_factory=list)

    def get_channel_config(self, index: int) -> ChannelConfig:
        """获取指定通道的配置（若无逐通道配置则返回基于默认值的配置）"""
        if 0 <= index < len(self.channels):
            return self.channels[index]
        return ChannelConfig(
            index=index,
            enabled=True,
            coupling_type=self.coupling_type,
        )

    def get_sample_rate(self) -> int:
        """获取采样率（兼容 UHF 默认 100MHz 和 AE 默认 2MHz）"""
        if self.sample_rate_hz is not None and self.sample_rate_hz > 0:
            return self.sample_rate_hz
        return 2_000_000 if self.coupling_type == "ae" else 100_000_000
