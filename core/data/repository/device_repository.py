# -*- coding: utf-8 -*-
"""PD 采集设备/通道持久化仓库"""

from __future__ import annotations

import logging
from typing import List, Optional

from core.data.monitor_models import PDChannelModel, PDDeviceModel

logger = logging.getLogger(__name__)


class PDDeviceRepository:
    """
    PD 采集设备持久化仓库

    封装 PDDeviceModel / PDChannelModel 的增删改查。
    与其他 Repository 不同，PD 设备使用独立的 pd_ 表且不需 BaseRepository
    的通用 CRUD（pd_models 使用独立的 Base 但共享同一 DatabaseManager 引擎）。
    """

    def __init__(self, db_manager):
        self._db = db_manager

    # ── 设备 ─────────────────────────────────────────

    def save_device(self, device: PDDeviceModel) -> PDDeviceModel:
        """
        保存设备及关联通道（创建或更新）

        使用 with_for_update 确保并发安全。
        通过 cascade 自动处理通道的增删改。
        """
        with self._db.session() as session:
            existing = session.get(PDDeviceModel, device.id)
            if existing:
                # 更新设备字段
                for key, value in device.to_dict().items():
                    if key not in ("id", "created_at"):
                        setattr(existing, key, value)

                # 替换通道: 删除旧通道，添加新通道
                if device.channels is not None:
                    existing.channels.clear()
                    for ch in device.channels:
                        ch.device_id = device.id
                        existing.channels.append(ch)

                session.flush()
                return existing
            else:
                # 新建设备（通道通过 cascade 自动保存）
                session.add(device)
                session.flush()
                return device

    def delete_device(self, device_id: str) -> bool:
        """删除设备及关联通道"""
        with self._db.session() as session:
            dev = session.get(PDDeviceModel, device_id)
            if dev is None:
                return False
            session.delete(dev)
            return True

    def get_all_devices(self) -> List[PDDeviceModel]:
        """获取所有 PD 设备（含通道关系，即时加载避免 detached 访问）"""
        from sqlalchemy.orm import joinedload

        with self._db.session() as session:
            devices = session.query(PDDeviceModel).options(joinedload(PDDeviceModel.channels)).all()
            # 在 session 内遍历以触发 lazy load
            result = []
            for d in devices:
                _ = d.channels  # 确保 channels 在 session 内加载
                result.append(d)
            return result

    def get_device(self, device_id: str) -> Optional[PDDeviceModel]:
        """获取单个设备（含通道关系）"""
        from sqlalchemy.orm import joinedload

        with self._db.session() as session:
            dev = (
                session.query(PDDeviceModel)
                .options(joinedload(PDDeviceModel.channels))
                .filter(PDDeviceModel.id == device_id)
                .first()
            )
            if dev:
                _ = dev.channels  # 确保在 session 内加载
            return dev

    # ── 通道 ─────────────────────────────────────────

    def save_channels(self, device_id: str, channels: List[PDChannelModel]) -> None:
        """
        批量保存通道（通过 save_device 委托）

        Args:
            device_id: 设备 ID
            channels: 通道模型列表
        """
        device = PDDeviceModel(id=device_id)
        device.channels = channels
        self.save_device(device)

    def get_channels(self, device_id: str) -> List[PDChannelModel]:
        """获取设备的所有通道"""
        with self._db.session() as session:
            return list(
                session.query(PDChannelModel)
                .filter(PDChannelModel.device_id == device_id)
                .order_by(PDChannelModel.channel_index)
                .all()
            )

    def clear_all(self) -> None:
        """清空所有设备/通道（测试用）"""
        with self._db.session() as session:
            session.query(PDChannelModel).delete()
            session.query(PDDeviceModel).delete()

    @staticmethod
    def from_dict(device_id: str, data: dict, channels: Optional[List[dict]] = None) -> PDDeviceModel:
        """
        从字典构建设备模型

        Args:
            device_id: 设备 ID
            data: 设备数据字典
            channels: 可选的通道配置列表 [{"index": 0, "coupling_type": "uhf", ...}]

        Returns:
            PDDeviceModel（含 channels 关系）
        """
        dev = PDDeviceModel(
            id=device_id,
            name=data.get("name", device_id),
            device_type=data.get("device_type", "fpga_acq"),
            host=data.get("host", "0.0.0.0"),
            tcp_port=data.get("tcp_port", 5000),
            udp_port=data.get("udp_port", 6000),
            status=data.get("status", 0),
            channel_count=data.get("channel_count", 4),
            sample_rate_hz=data.get("sample_rate_hz", 100_000_000),
        )

        if channels:
            dev.channels = []
            for ch_data in channels:
                dev.channels.append(
                    PDChannelModel(
                        device_id=device_id,
                        channel_index=ch_data.get("index", 0),
                        name=ch_data.get("name", f"通道 {ch_data.get('index', 0) + 1}"),
                        enabled=ch_data.get("enabled", True),
                        coupling_type=ch_data.get("coupling_type", "uhf"),
                        gain=ch_data.get("gain", 1.0),
                        attenuation_db=ch_data.get("attenuation_db", 0),
                        frequency_min_mhz=ch_data.get("frequency_min_mhz"),
                        frequency_max_mhz=ch_data.get("frequency_max_mhz"),
                        frequency_min_hz=ch_data.get("frequency_min_hz"),
                        frequency_max_hz=ch_data.get("frequency_max_hz"),
                        ae_sample_rate_hz=ch_data.get("ae_sample_rate_hz"),
                    )
                )

        return dev
