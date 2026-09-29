"""设备配置加载（数据库优先，JSON 回退）。

从 `ui/controller_config.py` 迁入：sidecar 的 `--hardware` 分支需要它，而它原先住在
即将于 M8 删除的 `ui/` 包里——冻结 sidecar 时会把整个 Qt 前端目录一起拖进产物。
只依赖 core，故放在 core。`ui/` 尚未下线，那边仍用自己的副本。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.communication.hardware_config import DeviceConnectionConfig
from core.foundation.runtime_paths import resource_path

logger = logging.getLogger(__name__)


def load_hardware_config(db_manager: Any = None) -> list[DeviceConnectionConfig]:
    """加载硬件设备配置：优先数据库，回退 config/hardware_config.json。"""
    if db_manager:
        try:
            from core.data.repository.device_repository import PDDeviceRepository

            db_devices = PDDeviceRepository(db_manager).get_all_devices()
            if db_devices:
                configs = [
                    DeviceConnectionConfig(
                        device_id=dev.id,
                        name=dev.name,
                        host=dev.host or "0.0.0.0",
                        udp_port=dev.udp_port,
                        channel_count=len(dev.channels) if dev.channels else dev.channel_count,
                        coupling_type=dev.channels[0].coupling_type if dev.channels else "uhf",
                        sample_rate_hz=dev.sample_rate_hz or 100_000_000,
                    )
                    for dev in db_devices
                ]
                logger.info("从数据库加载 %d 个硬件设备配置", len(configs))
                return configs
        except Exception as e:
            logger.debug("数据库硬件配置加载失败，回退到 JSON: %s", e)

    return _load_from_json()


def _load_from_json() -> list[DeviceConnectionConfig]:
    config_path = resource_path("config", "hardware_config.json")
    if not config_path.exists():
        return []

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return [
            DeviceConnectionConfig(
                device_id=item["device_id"],
                name=item["name"],
                host=item.get("host", "0.0.0.0"),
                udp_port=item.get("udp_port", 6000),
                channel_count=item.get("channel_count", 4),
                coupling_type=item.get("coupling_type", "uhf"),
                sample_rate_hz=item.get("sample_rate_hz", 100_000_000),
                ae_frequency_min_hz=item.get("ae_frequency_min_hz"),
                ae_frequency_max_hz=item.get("ae_frequency_max_hz"),
            )
            for item in data.get("devices", [])
        ]
    except Exception as e:
        logger.error("硬件配置加载失败: %s", e)
        return []
