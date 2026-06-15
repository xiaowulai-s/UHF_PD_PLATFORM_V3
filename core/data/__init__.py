# -*- coding: utf-8 -*-
"""Data-layer public exports."""

from .models import (
    AlarmModel,
    AlarmRuleModel,
    Base,
    DatabaseManager,
    DeviceModel,
    HistoricalDataModel,
    RegisterMapModel,
    SystemLogModel,
    get_db_manager,
    init_database,
    utc_now,
)

# PD 监测模型
from .monitor_models import (
    PDAlarmModel,
    PDChannelModel,
    PDDeviceModel,
    PDEventModel,
    PDTrendDataModel,
)

# PD 设备仓库
try:
    from .repository.device_repository import PDDeviceRepository
except ImportError:
    PDDeviceRepository = None  # type: ignore

__all__ = [
    "Base",
    "DeviceModel",
    "RegisterMapModel",
    "HistoricalDataModel",
    "AlarmModel",
    "SystemLogModel",
    "AlarmRuleModel",
    "DatabaseManager",
    "get_db_manager",
    "init_database",
    "utc_now",
    "PDDeviceRepository",
    # PD 监测模型
    "PDDeviceModel",
    "PDChannelModel",
    "PDEventModel",
    "PDTrendDataModel",
    "PDAlarmModel",
]
