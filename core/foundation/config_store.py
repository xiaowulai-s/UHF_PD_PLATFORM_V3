# -*- coding: utf-8 -*-
"""
ConfigStore - 统一配置中心

配置优先级: 命令行 > 环境变量 > 用户配置 > 默认值

配置源:
1. config.json - 全局系统配置
2. devices.json - MCGS设备配置
3. 数据库 - 设备注册信息
4. 代码默认值 - 兜底配置

使用方式:
    config = ConfigStore.instance().get_device_config("mcgs_1")
    ConfigStore.instance().update_device_config("mcgs_1", new_config)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


class ConfigStore(QObject):
    config_changed = Signal(str)

    _instance = None
    _lock = __import__("threading").Lock()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._global_config: Dict[str, Any] = {}
        self._devices_json_path: Optional[Path] = None
        self._json_devices: Dict[str, Dict] = {}
        self._db_repo = None

    @classmethod
    def instance(cls) -> "ConfigStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    def set_db_repo(self, repo):
        self._db_repo = repo

    def load_global_config(self, config_path: str) -> bool:
        try:
            path = Path(config_path)
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    self._global_config = json.load(f)
                logger.info("全局配置已加载: %s", path)
                return True
            return False
        except Exception as e:
            logger.error("加载全局配置失败: %s", e)
            return False

    def load_devices_json(self, json_path: str) -> bool:
        try:
            self._devices_json_path = Path(json_path)
            if not self._devices_json_path.exists():
                logger.warning("设备配置文件不存在: %s", json_path)
                return False

            with open(self._devices_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.error("设备配置文件格式无效: 根元素应为对象, 当前为 %s", type(data).__name__)
                return False

            self._json_devices = {}
            devices_list = data.get("devices", [])
            if not isinstance(devices_list, list):
                logger.error("设备配置文件格式无效: 'devices' 应为数组")
                return False

            for dev in devices_list:
                if not isinstance(dev, dict):
                    logger.warning("跳过非对象类型的设备条目")
                    continue
                dev_id = dev.get("id")
                if not dev_id:
                    logger.warning("跳过缺少 'id' 字段的设备条目")
                    continue
                required_fields = ["ip", "port"]
                missing = [f for f in required_fields if f not in dev]
                if missing:
                    logger.warning("设备 [%s] 缺少字段: %s", dev_id, ", ".join(missing))
                self._json_devices[dev_id] = dev

            logger.info("设备配置已加载: %s (%d个设备)", json_path, len(self._json_devices))
            return True
        except json.JSONDecodeError as e:
            logger.error("设备配置文件JSON解析失败: %s (行%d列%d)", e.msg, e.lineno, e.colno)
            return False
        except Exception as e:
            logger.error("加载设备配置失败: %s", e)
            return False

    def get_device_config(self, device_id: str) -> Optional[Dict]:
        if self._db_repo is not None:
            try:
                config = self._db_repo.get(device_id)
                if config is not None:
                    return config
            except Exception:
                pass

        config = self._json_devices.get(device_id)
        if config is not None:
            return config

        return None

    def get_all_device_ids(self) -> List[str]:
        ids = set(self._json_devices.keys())
        if self._db_repo is not None:
            try:
                for dev in self._db_repo.list_all():
                    if isinstance(dev, dict) and "id" in dev:
                        ids.add(dev["id"])
            except Exception:
                pass
        return list(ids)

    def get_devices_from_json(self) -> Dict[str, Dict]:
        return dict(self._json_devices)

    def get_global(self, key: str, default: Any = None) -> Any:
        return self._global_config.get(key, default)

    def save_devices_json(self, devices_data: Dict) -> bool:
        if self._devices_json_path is None:
            logger.error("未设置设备配置文件路径")
            return False
        try:
            import shutil

            backup_path = self._devices_json_path.with_suffix(".json.bak")
            if self._devices_json_path.exists():
                try:
                    shutil.copy2(self._devices_json_path, backup_path)
                except Exception:
                    pass

            output = {"devices": list(devices_data.values()) if isinstance(devices_data, dict) else devices_data}

            temp_path = self._devices_json_path.with_suffix(".json.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
                f.write("\n")

            temp_path.replace(self._devices_json_path)

            self._json_devices = {}
            for dev_id, dev in devices_data.items() if isinstance(devices_data, dict) else []:
                self._json_devices[dev_id] = dev

            self.config_changed.emit("devices")
            logger.info("设备配置已保存: %s", self._devices_json_path)
            return True
        except Exception as e:
            logger.error("保存设备配置失败: %s", e)
            return False
