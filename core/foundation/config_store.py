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

from __future__ import annotations

import json
import logging
from datetime import datetime
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

    def set_db_repo(self, repo: Any) -> None:
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
        except json.JSONDecodeError as e:
            logger.error("全局配置JSON解析失败: %s (行%d列%d)", e.msg, e.lineno, e.colno)
            return False
        except FileNotFoundError:
            logger.warning("全局配置文件不存在: %s", config_path)
            return False
        except PermissionError as e:
            logger.error("全局配置文件权限不足: %s - %s", config_path, e)
            return False
        except Exception as e:
            logger.error("加载全局配置失败: %s", e, exc_info=True)
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
        except FileNotFoundError:
            logger.warning("设备配置文件不存在: %s", json_path)
            return False
        except PermissionError as e:
            logger.error("设备配置文件权限不足: %s - %s", json_path, e)
            return False
        except Exception as e:
            logger.error("加载设备配置失败: %s", e, exc_info=True)
            return False

    def get_device_config(self, device_id: str) -> Optional[Dict]:
        if self._db_repo is not None:
            try:
                config = self._db_repo.get(device_id)
                if config is not None:
                    return config
            except Exception as e:
                logger.debug("DB 查询设备配置失败: %s", e, exc_info=True)

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
            except Exception as e:
                logger.debug("DB 查询设备列表失败: %s", e, exc_info=True)
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
                except (PermissionError, OSError) as e:
                    logger.debug("备份文件复制失败（非关键）: %s", e)

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
        except (PermissionError, IsADirectoryError, OSError) as e:
            logger.error("保存设备配置失败（IO错误）: %s", e)
            return False
        except (TypeError, ValueError) as e:
            logger.error("保存设备配置失败（数据格式错误）: %s", e)
            return False
        except Exception as e:
            logger.error("保存设备配置失败: %s", e, exc_info=True)
            return False

    # ── 配置档案管理（Phase 10.2 新增） ─────────────────────

    def save_profile(self, profile_name: str, profile_path: str) -> bool:
        """
        保存配置档案（包含全局配置和设备配置）

        Args:
            profile_name: 档案名称（用于元数据）
            profile_path: 保存路径

        Returns:
            是否保存成功
        """
        try:
            profile_data = {
                "metadata": {
                    "name": profile_name,
                    "created_at": datetime.now().isoformat(),
                    "version": "1.0",
                },
                "global_config": self._global_config,
                "devices": self._json_devices,
            }

            path = Path(profile_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            temp_path = path.with_suffix(".json.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(profile_data, f, indent=2, ensure_ascii=False)
                f.write("\n")

            temp_path.replace(path)
            logger.info("配置档案已保存: %s -> %s", profile_name, path)
            return True
        except Exception as e:
            logger.error("保存配置档案失败: %s", e)
            return False

    def load_profile(self, profile_path: str) -> bool:
        """
        加载配置档案并应用

        Args:
            profile_path: 档案路径

        Returns:
            是否加载成功
        """
        try:
            path = Path(profile_path)
            if not path.exists():
                logger.error("配置档案不存在: %s", profile_path)
                return False

            with open(path, "r", encoding="utf-8") as f:
                profile_data = json.load(f)

            if not isinstance(profile_data, dict):
                logger.error("配置档案格式无效")
                return False

            # 恢复全局配置
            if "global_config" in profile_data:
                self._global_config = profile_data["global_config"]
                logger.info("全局配置已恢复")

            # 恢复设备配置
            if "devices" in profile_data:
                self._json_devices = profile_data["devices"]
                # 保存到设备配置文件
                if self._devices_json_path:
                    self.save_devices_json(self._json_devices)
                logger.info("设备配置已恢复 (%d 个设备)", len(self._json_devices))

            # 发送配置变更信号
            self.config_changed.emit("profile_loaded")
            logger.info("配置档案已加载: %s", profile_path)
            return True
        except json.JSONDecodeError as e:
            logger.error("配置档案JSON解析失败: %s (行%d列%d)", e.msg, e.lineno, e.colno)
            return False
        except Exception as e:
            logger.error("加载配置档案失败: %s", e)
            return False

    def export_config(self, export_path: str, include_devices: bool = True) -> bool:
        """
        导出当前配置到文件

        Args:
            export_path: 导出路径
            include_devices: 是否包含设备配置

        Returns:
            是否导出成功
        """
        try:
            export_data = {
                "metadata": {
                    "exported_at": datetime.now().isoformat(),
                    "version": "1.0",
                    "description": "PD 监测系统配置档案",
                },
                "global_config": self._global_config,
            }

            if include_devices:
                export_data["devices"] = self._json_devices

            path = Path(export_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
                f.write("\n")

            logger.info("配置已导出: %s", path)
            return True
        except PermissionError as e:
            logger.error("导出配置失败（权限不足）: %s", e)
            return False
        except OSError as e:
            logger.error("导出配置失败（IO错误）: %s", e)
            return False
        except (TypeError, ValueError) as e:
            logger.error("导出配置失败（数据序列化错误）: %s", e)
            return False
        except Exception as e:
            logger.error("导出配置失败: %s", e, exc_info=True)
            return False

    def import_config(self, import_path: str, merge: bool = False) -> bool:
        """
        从文件导入配置

        Args:
            import_path: 导入路径
            merge: 是否合并（False 则覆盖）

        Returns:
            是否导入成功
        """
        try:
            path = Path(import_path)
            if not path.exists():
                logger.error("配置文件不存在: %s", import_path)
                return False

            with open(path, "r", encoding="utf-8") as f:
                import_data = json.load(f)

            if not isinstance(import_data, dict):
                logger.error("配置文件格式无效")
                return False

            # 更新全局配置
            if "global_config" in import_data:
                if merge:
                    # 深度合并
                    self._deep_merge(self._global_config, import_data["global_config"])
                else:
                    self._global_config = import_data["global_config"]
                logger.info("全局配置已%s", "合并" if merge else "覆盖")

            # 更新设备配置
            if "devices" in import_data:
                if merge:
                    self._deep_merge(self._json_devices, import_data["devices"])
                else:
                    self._json_devices = import_data["devices"]

                # 保存到设备配置文件
                if self._devices_json_path:
                    self.save_devices_json(self._json_devices)
                logger.info("设备配置已%s (%d 个设备)", "合并" if merge else "覆盖", len(self._json_devices))

            # 发送配置变更信号
            self.config_changed.emit("config_imported")
            logger.info("配置已导入: %s", path)
            return True
        except json.JSONDecodeError as e:
            logger.error("配置文件JSON解析失败: %s (行%d列%d)", e.msg, e.lineno, e.colno)
            return False
        except FileNotFoundError:
            logger.error("配置文件不存在: %s", import_path)
            return False
        except PermissionError as e:
            logger.error("配置文件权限不足: %s - %s", import_path, e)
            return False
        except Exception as e:
            logger.error("导入配置失败: %s", e, exc_info=True)
            return False

    def _deep_merge(self, base: Dict, override: Dict) -> None:
        """深度合并字典"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def list_profiles(self, profiles_dir: str) -> List[str]:
        """
        列出指定目录下的所有配置档案

        Args:
            profiles_dir: 档案目录

        Returns:
            档案文件路径列表
        """
        try:
            path = Path(profiles_dir)
            if not path.exists():
                return []

            profiles = []
            for file in path.glob("*.json"):
                if file.name != "config.json" and file.name != "devices.json":
                    profiles.append(str(file))

            return sorted(profiles)
        except Exception as e:
            logger.error("列出配置档案失败: %s", e)
            return []

    def get_profile_info(self, profile_path: str) -> Optional[Dict]:
        """
        获取配置档案元信息（不加载完整配置）

        Args:
            profile_path: 档案路径

        Returns:
            元信息字典，失败返回 None
        """
        try:
            path = Path(profile_path)
            if not path.exists():
                return None

            with open(path, "r", encoding="utf-8") as f:
                profile_data = json.load(f)

            if not isinstance(profile_data, dict):
                return None

            return profile_data.get("metadata", {})
        except Exception as e:
            logger.error("读取配置档案元信息失败: %s", e)
            return None
