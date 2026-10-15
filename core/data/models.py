# -*- coding: utf-8 -*-
"""Database models and session management."""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, create_engine, event
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

Base = declarative_base()
logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class DeviceModel(Base):
    """Persistent device model."""

    __tablename__ = "devices"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False, index=True)
    device_type = Column(String(64), nullable=False)
    device_number = Column(String(64), nullable=True)
    protocol_type = Column(String(32), nullable=False)
    host = Column(String(64), nullable=True)
    port = Column(Integer, nullable=True)
    unit_id = Column(Integer, default=1)
    use_simulator = Column(Boolean, default=False)
    status = Column(Integer, default=0)
    last_connected_at = Column(DateTime, nullable=True)
    connection_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    register_maps = relationship("RegisterMapModel", back_populates="device", cascade="all, delete-orphan")
    historical_data = relationship("HistoricalDataModel", back_populates="device", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_device_type", "device_type"),
        Index("idx_device_name", "name"),
    )

    def __init__(self, **kwargs: Any) -> None:
        alias_map = {
            "protocol": "protocol_type",
            "ip": "host",
            "slave_id": "unit_id",
        }
        normalized: Dict[str, Any] = {}
        for key, value in kwargs.items():
            normalized[alias_map.get(key, key)] = value

        normalized.setdefault("id", uuid.uuid4().hex[:8])
        normalized.setdefault("protocol_type", "modbus_tcp")
        normalized.setdefault("unit_id", 1)
        super().__init__(**normalized)

    @property
    def protocol(self) -> str:
        return self.protocol_type

    @protocol.setter
    def protocol(self, value: str) -> None:
        self.protocol_type = value

    @property
    def ip(self) -> Optional[str]:
        return self.host

    @ip.setter
    def ip(self, value: Optional[str]) -> None:
        self.host = value

    @property
    def slave_id(self) -> int:
        return self.unit_id

    @slave_id.setter
    def slave_id(self, value: int) -> None:
        self.unit_id = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "device_type": self.device_type,
            "device_number": self.device_number,
            "protocol_type": self.protocol_type,
            "host": self.host,
            "port": self.port,
            "unit_id": self.unit_id,
            "use_simulator": self.use_simulator,
            "status": self.status,
            "last_connected_at": self.last_connected_at.isoformat() if self.last_connected_at else None,
            "connection_count": self.connection_count,
            "error_count": self.error_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RegisterMapModel(Base):
    """Register map model."""

    __tablename__ = "register_maps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    address = Column(Integer, nullable=False)
    function_code = Column(Integer, default=3)
    data_type = Column(String(32), default="uint16")
    read_write = Column(String(8), default="R")
    scale = Column(Float, default=1.0)
    unit = Column(String(32), default="")
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)

    device = relationship("DeviceModel", back_populates="register_maps")

    __table_args__ = (
        Index("idx_register_device", "device_id"),
        Index("idx_register_address", "device_id", "address"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "name": self.name,
            "address": self.address,
            "function_code": self.function_code,
            "data_type": self.data_type,
            "read_write": self.read_write,
            "scale": self.scale,
            "unit": self.unit,
            "description": self.description,
            "enabled": self.enabled,
        }


class HistoricalDataModel(Base):
    """Historical data model."""

    __tablename__ = "historical_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    parameter_name = Column(String(128), nullable=False)
    value = Column(Float, nullable=False)
    raw_value = Column(Integer, nullable=True)
    unit = Column(String(32), default="")
    timestamp = Column(DateTime, default=utc_now, index=True)
    quality = Column(Integer, default=0)

    device = relationship("DeviceModel", back_populates="historical_data")

    __table_args__ = (
        Index("idx_hist_device_time", "device_id", "timestamp"),
        Index("idx_hist_param_time", "device_id", "parameter_name", "timestamp"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "parameter_name": self.parameter_name,
            "value": self.value,
            "raw_value": self.raw_value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "quality": self.quality,
        }


class AlarmModel(Base):
    """Alarm history model."""

    __tablename__ = "alarms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(String(64), nullable=False)
    device_id = Column(String(64), nullable=False, index=True)
    device_name = Column(String(128), nullable=True)
    parameter = Column(String(128), nullable=False)
    alarm_type = Column(String(32), nullable=False)
    level = Column(Integer, nullable=False)
    value = Column(Float, nullable=False)
    threshold_high = Column(Float, nullable=True)
    threshold_low = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=utc_now, index=True)
    acknowledged = Column(Boolean, default=False)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_alarm_device_time", "device_id", "timestamp"),
        Index("idx_alarm_level", "level"),
        Index("idx_alarm_ack", "acknowledged"),
    )

    def to_dict(self) -> Dict[str, Any]:
        level_names = {
            0: "信息",
            1: "警告",
            2: "错误",
            3: "严重",
        }
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "parameter": self.parameter,
            "alarm_type": self.alarm_type,
            "level": self.level,
            "level_name": level_names.get(self.level, "未知"),
            "value": self.value,
            "threshold_high": self.threshold_high,
            "threshold_low": self.threshold_low,
            "description": self.description,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "acknowledged": self.acknowledged,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "acknowledged_by": self.acknowledged_by,
        }


class AlarmRuleModel(Base):
    """Alarm rule model."""

    __tablename__ = "alarm_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(String(64), nullable=False, unique=True, index=True)
    device_id = Column(String(64), nullable=False, index=True)
    device_name = Column(String(128), nullable=False)
    parameter = Column(String(64), nullable=False)
    alarm_type = Column(String(32), nullable=False)
    level = Column(Integer, nullable=False)
    threshold_high = Column(Float, nullable=True)
    threshold_low = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("idx_rule_device_param", "device_id", "parameter"),
        Index("idx_rule_enabled", "enabled"),
    )

    def __init__(self, **kwargs: Any) -> None:
        register_address = kwargs.pop("register_address", None)
        condition = kwargs.pop("condition", None)
        threshold = kwargs.pop("threshold", None)

        if register_address is not None and "parameter" not in kwargs:
            kwargs["parameter"] = self._parameter_from_register(register_address)
        if threshold is not None:
            alarm_type = kwargs.get("alarm_type")
            condition_name = condition or alarm_type
            if condition_name in {"greater_than", "high_limit", "threshold_high"}:
                kwargs.setdefault("threshold_high", threshold)
            elif condition_name in {"less_than", "low_limit", "threshold_low"}:
                kwargs.setdefault("threshold_low", threshold)

        kwargs.setdefault("rule_id", uuid.uuid4().hex[:12])
        kwargs.setdefault("device_name", kwargs.get("device_id", ""))
        kwargs.setdefault("level", 1)
        kwargs.setdefault("alarm_type", "threshold_high")
        kwargs.setdefault("parameter", "register_0")
        super().__init__(**kwargs)

    @staticmethod
    def _parameter_from_register(register_address: Any) -> str:
        return f"register_{register_address}"

    @property
    def register_address(self) -> Optional[int]:
        if self.parameter.startswith("register_"):
            try:
                return int(self.parameter.split("_", 1)[1])
            except ValueError:
                return None
        return None

    @register_address.setter
    def register_address(self, value: int) -> None:
        self.parameter = self._parameter_from_register(value)

    @property
    def condition(self) -> str:
        if self.threshold_high is not None:
            return "greater_than"
        if self.threshold_low is not None:
            return "less_than"
        return "custom"

    @property
    def threshold(self) -> Optional[float]:
        return self.threshold_high if self.threshold_high is not None else self.threshold_low

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "parameter": self.parameter,
            "alarm_type": self.alarm_type,
            "level": self.level,
            "threshold_high": self.threshold_high,
            "threshold_low": self.threshold_low,
            "description": self.description,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SystemLogModel(Base):
    """System log persistence model."""

    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(String(16), nullable=False, index=True)
    logger = Column(String(64), nullable=False)
    message = Column(Text, nullable=False)
    module = Column(String(128), nullable=True)
    function = Column(String(128), nullable=True)
    line = Column(Integer, nullable=True)
    exception = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=utc_now, index=True)

    __table_args__ = (Index("idx_log_level_time", "level", "timestamp"),)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
            "module": self.module,
            "function": self.function,
            "line": self.line,
            "exception": self.exception,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class DeviceStatusHistoryModel(Base):
    """Device status history record.

    Tracks device status changes for history and trend analysis.
    """

    __tablename__ = "device_status_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)  # connected, disconnected, error
    status_code = Column(Integer, nullable=True)
    message = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    port = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    timestamp = Column(DateTime, default=utc_now, nullable=False, index=True)

    device = relationship("DeviceModel", backref="status_history")

    __table_args__ = (
        Index("idx_status_history_device_time", "device_id", "timestamp"),
        Index("idx_status_history_status_time", "status", "timestamp"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "status": self.status,
            "status_code": self.status_code,
            "message": self.message,
            "ip_address": self.ip_address,
            "port": self.port,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class DatabaseManager:
    """SQLite database/session manager."""

    _instances: Dict[str, "DatabaseManager"] = {}

    def __new__(cls, db_path: Optional[str] = None) -> "DatabaseManager":
        resolved_db_path = cls._resolve_db_path(db_path)
        if resolved_db_path == ":memory:":
            instance = super().__new__(cls)
            instance._initialized = False
            return instance
        if resolved_db_path not in cls._instances:
            instance = super().__new__(cls)
            instance._initialized = False
            cls._instances[resolved_db_path] = instance
        return cls._instances[resolved_db_path]

    def __init__(self, db_path: Optional[str] = None) -> None:
        if self._initialized:
            return

        db_path = self._resolve_db_path(db_path)
        if db_path != ":memory:":
            db_dir = os.path.dirname(db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

        self._db_path = db_path
        if db_path == ":memory:":
            self._engine = create_engine(
                "sqlite://",
                echo=False,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
                future=True,
            )
        else:
            self._engine = create_engine(
                f"sqlite:///{db_path}",
                echo=False,
                connect_args={"check_same_thread": False},
                future=True,
            )

        event.listen(self._engine, "connect", self._configure_sqlite_connection)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        Base.metadata.create_all(self._engine)
        self._initialized = True

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Provide a transactional session scope."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("数据库事务执行失败，已回滚: %s", self._db_path)
            raise
        finally:
            session.close()

    def get_session(self) -> Session:
        """Create a raw session; caller is responsible for closing it."""
        return self._session_factory()

    def close(self) -> None:
        """Dispose the engine."""
        if self._engine:
            self._engine.dispose()
        if getattr(self, "_db_path", None) == ":memory:":
            self._initialized = False

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton cache, mainly for tests."""
        cls._instances = {}

    @staticmethod
    def _resolve_db_path(db_path: Optional[str]) -> str:
        if db_path:
            return db_path

        env_db_path = os.getenv("EQUIPMENT_MANAGEMENT_DB_PATH")
        if env_db_path:
            return env_db_path

        if os.getenv("PYTEST_CURRENT_TEST"):
            return ":memory:"

        return os.path.join("data", "equipment_management.db")

    @staticmethod
    def _configure_sqlite_connection(dbapi_connection: Any, _: Any) -> None:
        """Apply SQLite pragmas for integrity and concurrency."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:  # pragma: no cover - depends on SQLite backend
            logger.debug("journal_mode=WAL 设置失败，继续使用默认模式")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def get_db_manager(db_path: Optional[str] = None) -> DatabaseManager:
    """Return a database manager instance."""
    return DatabaseManager(db_path)


def init_database(db_path: Optional[str] = None) -> DatabaseManager:
    """Initialize the database and return the manager."""
    return DatabaseManager(db_path)
