# -*- coding: utf-8 -*-
"""DeadbandFilter + SubscriptionManager 专项单元测试"""

from unittest.mock import MagicMock

from core.foundation.data_bus import DeadbandFilter, SubscriptionManager

# ═════════════════════════════════════════════════════
# DeadbandFilter 测试
# ═════════════════════════════════════════════════════


class TestDeadbandFilterBasic:
    """基础过滤测试"""

    def test_first_publish_always_passes(self):
        f = DeadbandFilter()
        should, keys = f.filter_and_get_keys("dev1", {"temp": 25.0})
        assert should is True
        assert "temp" in keys

    def test_no_change_filtered(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"temp": 25.0})
        should, keys = f.filter_and_get_keys("dev1", {"temp": 25.0})
        assert should is False
        assert len(keys) == 0

    def test_beyond_deadband_passes(self):
        f = DeadbandFilter()
        f.set_global_deadband(1.0)
        f.filter_and_get_keys("dev1", {"temp": 25.0})
        should, keys = f.filter_and_get_keys("dev1", {"temp": 27.0})
        assert should is True
        assert "temp" in keys

    def test_within_deadband_filtered(self):
        f = DeadbandFilter()
        f.set_global_deadband(5.0)
        f.filter_and_get_keys("dev1", {"temp": 25.0})
        should, keys = f.filter_and_get_keys("dev1", {"temp": 25.3})
        assert should is False

    def test_partial_change(self):
        """部分参数变化，只有变化的 key 通过"""
        f = DeadbandFilter()
        f.set_global_deadband(1.0)
        f.filter_and_get_keys("dev1", {"temp": 25.0, "humid": 60.0})
        should, keys = f.filter_and_get_keys("dev1", {"temp": 25.0, "humid": 65.0})
        assert should is True
        assert "humid" in keys
        assert "temp" not in keys


class TestDeadbandFilterConfig:
    """配置测试"""

    def test_global_deadband(self):
        f = DeadbandFilter()
        f.set_global_deadband(10.0)
        f.filter_and_get_keys("dev1", {"val": 100.0})
        # 变化 5.0 < 10.0, 应被过滤
        should, _ = f.filter_and_get_keys("dev1", {"val": 105.0})
        assert should is False
        # 变化 15.0 > 10.0, 应通过
        should, _ = f.filter_and_get_keys("dev1", {"val": 120.0})
        assert should is True

    def test_device_deadband_overrides_global(self):
        f = DeadbandFilter()
        f.set_global_deadband(1.0)
        f.set_device_deadband("dev1", "temp", 10.0)
        f.filter_and_get_keys("dev1", {"temp": 100.0})
        # 变化 5.0 < 设备死区 10.0, 应被过滤
        should, _ = f.filter_and_get_keys("dev1", {"temp": 105.0})
        assert should is False

    def test_device_deadbands_batch(self):
        f = DeadbandFilter()
        f.set_device_deadbands("dev1", {"temp": 5.0, "humid": 10.0})
        f.filter_and_get_keys("dev1", {"temp": 100.0, "humid": 50.0})
        # 变化 3 < 死区 5, 变化 5 < 死区 10, 均不应通过
        should, keys = f.filter_and_get_keys("dev1", {"temp": 103.0, "humid": 55.0})
        assert "temp" not in keys  # 3 < 5
        assert "humid" not in keys  # 5 < 10
        # 变化 15 > 死区 10, humid 应通过
        should, keys = f.filter_and_get_keys("dev1", {"temp": 103.0, "humid": 65.0})
        assert "humid" in keys  # 65 - 50 = 15 > 10


class TestDeadbandFilterSpecialValues:
    """特殊值测试"""

    def test_nan_always_passes(self):
        f = DeadbandFilter()
        f.set_global_deadband(1.0)
        f.filter_and_get_keys("dev1", {"val": float("nan")})
        should, keys = f.filter_and_get_keys("dev1", {"val": float("nan")})
        assert should is True

    def test_inf_handling(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"val": float("inf")})
        should, _ = f.filter_and_get_keys("dev1", {"val": float("inf")})
        assert should is False  # same inf

    def test_inf_vs_neg_inf(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"val": float("inf")})
        should, keys = f.filter_and_get_keys("dev1", {"val": float("-inf")})
        assert should is True

    def test_string_value_changed(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"status": "running"})
        should, keys = f.filter_and_get_keys("dev1", {"status": "stopped"})
        assert should is True
        assert "status" in keys

    def test_string_value_unchanged(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"status": "running"})
        should, _ = f.filter_and_get_keys("dev1", {"status": "running"})
        assert should is False

    def test_dict_with_value_key(self):
        """带 'value' 键的字典应走数值比较"""
        f = DeadbandFilter()
        f.set_global_deadband(1.0)
        f.filter_and_get_keys("dev1", {"temp": {"value": 25.0, "unit": "C"}})
        should, _ = f.filter_and_get_keys("dev1", {"temp": {"value": 25.3, "unit": "C"}})
        assert should is False  # 0.3 < 1.0


class TestDeadbandFilterCleanup:
    """清理测试"""

    def test_clear_device(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"temp": 25.0})
        f.clear_device("dev1")
        # 清除后首次应通过
        should, _ = f.filter_and_get_keys("dev1", {"temp": 25.0})
        assert should is True

    def test_clear_all(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"temp": 25.0})
        f.filter_and_get_keys("dev2", {"temp": 30.0})
        f.clear_all()
        should1, _ = f.filter_and_get_keys("dev1", {"temp": 25.0})
        should2, _ = f.filter_and_get_keys("dev2", {"temp": 30.0})
        assert should1 is True
        assert should2 is True

    def test_get_last_value(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"temp": 25.0})
        assert f.get_last_value("dev1", "temp") == 25.0
        assert f.get_last_value("dev1", "nonexistent") is None
        assert f.get_last_value("nonexistent", "temp") is None

    def test_get_last_data(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"temp": 25.0, "humid": 60.0})
        data = f.get_last_data("dev1")
        assert data == {"temp": 25.0, "humid": 60.0}
        assert f.get_last_data("nonexistent") is None


class TestDeadbandFilterShouldPublish:
    """should_publish 方法测试"""

    def test_should_publish_first(self):
        f = DeadbandFilter()
        assert f.should_publish("dev1", {"temp": 25.0}) is True

    def test_should_publish_no_change(self):
        f = DeadbandFilter()
        f.should_publish("dev1", {"temp": 25.0})
        assert f.should_publish("dev1", {"temp": 25.0}) is False

    def test_should_publish_empty(self):
        f = DeadbandFilter()
        assert f.should_publish("dev1", {}) is False


class TestDeadbandFilterGetChangedKeys:
    """get_changed_keys 方法测试"""

    def test_first_call_returns_all_keys(self):
        f = DeadbandFilter()
        keys = f.get_changed_keys("dev1", {"a": 1, "b": 2})
        assert keys == {"a", "b"}

    def test_no_change_returns_empty(self):
        f = DeadbandFilter()
        f.filter_and_get_keys("dev1", {"a": 1, "b": 2})
        keys = f.get_changed_keys("dev1", {"a": 1, "b": 2})
        assert len(keys) == 0


# ═════════════════════════════════════════════════════
# SubscriptionManager 测试
# ═════════════════════════════════════════════════════


class TestSubscriptionManager:
    """SubscriptionManager 测试"""

    def test_initial_count(self):
        mgr = SubscriptionManager()
        assert mgr.subscription_count == 0

    def test_register_and_count(self):
        mgr = SubscriptionManager()
        signal = MagicMock()
        slot = MagicMock()
        mgr.register(signal, slot)
        assert mgr.subscription_count == 1
        signal.connect.assert_called_once_with(slot)

    def test_unregister(self):
        mgr = SubscriptionManager()
        signal = MagicMock()
        slot = MagicMock()
        mgr.register(signal, slot)
        mgr.unregister(signal, slot)
        assert mgr.subscription_count == 0
        signal.disconnect.assert_called_once_with(slot)

    def test_release_all(self):
        mgr = SubscriptionManager()
        signals = [MagicMock() for _ in range(5)]
        slots = [MagicMock() for _ in range(5)]
        for sig, slot in zip(signals, slots):
            mgr.register(sig, slot)

        assert mgr.subscription_count == 5
        count = mgr.release_all()
        assert count == 5
        assert mgr.subscription_count == 0
        for sig, slot in zip(signals, slots):
            sig.disconnect.assert_called_with(slot)

    def test_register_duplicate(self):
        """重复注册同一信号+槽应去重，不增加计数"""
        mgr = SubscriptionManager()
        signal = MagicMock()
        slot = MagicMock()
        mgr.register(signal, slot)
        mgr.register(signal, slot)  # 重复
        # dict 去重，只保留一条
        assert mgr.subscription_count == 1

    def test_release_all_empty(self):
        mgr = SubscriptionManager()
        count = mgr.release_all()
        assert count == 0

    def test_register_handles_error(self):
        """注册失败不应崩溃"""
        mgr = SubscriptionManager()
        signal = MagicMock()
        signal.connect.side_effect = RuntimeError("signal destroyed")
        slot = MagicMock()
        mgr.register(signal, slot)
        assert mgr.subscription_count == 0  # 失败不计入
