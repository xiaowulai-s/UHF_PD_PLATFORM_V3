# -*- coding: utf-8 -*-
from __future__ import annotations

"""
数据源配置数据类 - DataSourceConfig

定义启动策略、已保存设备、模拟器参数的配置结构。
支持 JSON 序列化/反序列化，用于持久化到 config/pd_default_config.json。

配置合并优先级:
    命令行参数 (最高) → UI 设置 → config JSON → 代码默认值 (最低)
"""


from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class StartupPolicy:
    """启动策略配置"""

    mode: str = "auto_detect"  # "auto_detect" | "simulator_only" | "hardware_only"
    discovery_port: int = 7001
    discovery_timeout_s: int = 3


@dataclass
class SavedDevice:
    """已保存的硬件设备配置"""

    device_id: str = ""
    name: str = ""
    host: str = ""
    udp_port: int = 6000
    channel_count: int = 4
    coupling_type: str = "uhf"
    sample_rate_hz: int = 100_000_000


@dataclass
class DataSourceConfig:
    """数据源完整配置"""

    startup_policy: StartupPolicy = field(default_factory=StartupPolicy)
    saved_devices: List[SavedDevice] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """导出为字典"""
        return {
            "startup_policy": asdict(self.startup_policy),
            "saved_devices": [asdict(d) for d in self.saved_devices],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DataSourceConfig":
        """从字典加载配置"""
        sp_data = data.get("startup_policy", {})
        devices_data = data.get("saved_devices", [])
        return cls(
            startup_policy=StartupPolicy(
                mode=sp_data.get("mode", "auto_detect"),
                discovery_port=sp_data.get("discovery_port", 7001),
                discovery_timeout_s=sp_data.get("discovery_timeout_s", 3),
            ),
            saved_devices=[
                SavedDevice(
                    device_id=d.get("device_id", ""),
                    name=d.get("name", ""),
                    host=d.get("host", ""),
                    udp_port=d.get("udp_port", 6000),
                    channel_count=d.get("channel_count", 4),
                    coupling_type=d.get("coupling_type", "uhf"),
                    sample_rate_hz=d.get("sample_rate_hz", 100_000_000),
                )
                for d in devices_data
            ],
        )
