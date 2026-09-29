# -*- coding: utf-8 -*-
"""PublishThrottle 专项单元测试"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from core.processing.publish_throttle import PublishThrottle


class TestPublishThrottlePRPD:
    def test_prpd_no_publish_before_interval(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prpd = MagicMock()
        prpd.max_amplitude = 10000.0
        prpd.compute.return_value = MagicMock()

        # 前 49 帧不应发布
        for _ in range(49):
            throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 100.0)
        bus.publish_prpd_result.assert_not_called()

    def test_prpd_publish_at_interval(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prpd = MagicMock()
        prpd.max_amplitude = 10000.0
        prpd.compute.return_value = MagicMock()

        for _ in range(50):
            throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 100.0)
        bus.publish_prpd_result.assert_called_once()

    def test_prpd_decay_at_interval(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prpd = MagicMock()
        prpd.max_amplitude = 10000.0

        for _ in range(20):
            throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 100.0)
        prpd.decay.assert_called_once_with(0.995)

    def test_prpd_dynamic_amplitude_expand(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prpd = MagicMock()
        prpd.max_amplitude = 10000.0

        # 发送超过 80% 阈值的幅值
        throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 9000.0)
        assert prpd.max_amplitude == 9000.0 * 1.2
        prpd.rebuild_matrix.assert_called_once()

    def test_prpd_no_expand_below_threshold(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prpd = MagicMock()
        prpd.max_amplitude = 10000.0

        throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 5000.0)
        prpd.rebuild_matrix.assert_not_called()


class TestPublishThrottlePRPS:
    def test_prps_publish_at_interval(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prps = MagicMock()
        prps.compute.return_value = MagicMock()

        phase_binned = np.zeros(360)
        for _ in range(10):
            throttle.prps_cycle("dev1", 0, "dev1:0", prps, phase_binned)
        bus.publish_prps_result.assert_called_once()

    def test_prps_no_publish_before_interval(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prps = MagicMock()

        phase_binned = np.zeros(360)
        for _ in range(9):
            throttle.prps_cycle("dev1", 0, "dev1:0", prps, phase_binned)
        bus.publish_prps_result.assert_not_called()


class TestPublishThrottleObservedMax:
    def test_observed_max_amp(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prpd = MagicMock()
        prpd.max_amplitude = 10000.0

        throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 500.0)
        assert throttle.get_observed_max_amp("dev1:0") == 500.0

        throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 300.0)
        assert throttle.get_observed_max_amp("dev1:0") == 500.0  # 不减小

        throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 800.0)
        assert throttle.get_observed_max_amp("dev1:0") == 800.0

    def test_clear(self):
        bus = MagicMock()
        throttle = PublishThrottle(bus)
        prpd = MagicMock()
        prpd.max_amplitude = 10000.0

        throttle.prpd_cycle("dev1", 0, "dev1:0", prpd, 500.0)
        throttle.clear()
        assert throttle.get_observed_max_amp("dev1:0") == 0.0


if __name__ == "__main__":
    pytest.main([__file__])
