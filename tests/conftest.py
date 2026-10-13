# -*- coding: utf-8 -*-
"""
Pytest 配置和共享 Fixtures
"""

import sys
from pathlib import Path

import pytest

# 确保项目根目录在sys.path中
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="session")
def project_root():
    """项目根目录路径"""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def db_manager():
    """内存数据库管理器(用于测试)"""
    from core.data import DatabaseManager

    db = DatabaseManager(":memory:")

    # DatabaseManager.__init__ 已自动创建所有表，无需手动 create_all

    yield db

    db.close()


@pytest.fixture
def session(db_manager):
    """数据库会话(每个测试独立)"""
    sess = db_manager.get_session()
    yield sess
    sess.rollback()
    sess.close()


@pytest.fixture
def sample_device_data():
    """示例设备数据字典"""
    return {
        "name": "TestDevice",
        "device_type": "DMT143",
        "device_number": "001",
        "protocol_type": "modbus_tcp",
        "host": "127.0.0.1",
        "port": 502,
        "unit_id": 1,
        "use_simulator": True,
    }


class MockDevice:
    """模拟设备对象(用于单元测试)"""

    def __init__(self, device_id="TEST001", name="MockDevice"):
        self._device_id = device_id
        self._name = name
        self._connected = False

    def get_device_id(self):
        return self._device_id

    def connect(self):
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    def reconnect(self):
        return self.connect()

    def reset(self):
        return True


@pytest.fixture
def mock_device():
    """模拟设备fixture"""
    return MockDevice()
