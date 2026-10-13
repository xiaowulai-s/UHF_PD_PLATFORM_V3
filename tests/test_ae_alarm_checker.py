# -*- coding: utf-8 -*-
"""AEAlarmChecker 专项单元测试"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from core.processing.ae_pipeline import AEAlarmChecker


class TestAEAlarmCheckerSignalLoss:
    def test_no_alarm_within_threshold(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.signal_loss_threshold = 5

        env_proc = MagicMock()
        env_proc.compute_rms.return_value = 1.0

        # 4 帧无 hit，不应触发
        for _ in range(4):
            checker.check("dev1", 0, "dev1:0", [], np.zeros(100), env_proc)
        bus.publish_alarm.assert_not_called()

    def test_alarm_triggers_at_threshold(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.signal_loss_threshold = 3

        env_proc = MagicMock()
        env_proc.compute_rms.return_value = 1.0

        for _ in range(3):
            checker.check("dev1", 0, "dev1:0", [], np.zeros(100), env_proc)
        bus.publish_alarm.assert_called_once()
        alarm = bus.publish_alarm.call_args[0][1]
        assert alarm["alarm_type"] == "ae_signal_loss"

    def test_alarm_clears_on_recovery(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.signal_loss_threshold = 2

        env_proc = MagicMock()
        env_proc.compute_rms.return_value = 1.0

        # 触发报警
        for _ in range(2):
            checker.check("dev1", 0, "dev1:0", [], np.zeros(100), env_proc)
        assert bus.publish_alarm.call_count == 1

        # 有 hit，恢复
        hit = MagicMock()
        checker.check("dev1", 0, "dev1:0", [hit], np.zeros(100), env_proc)
        bus.publish_alarm_cleared.assert_called_once()


class TestAEAlarmCheckerNoise:
    def test_noise_alarm_triggers(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.noise_threshold = 10.0
        checker.noise_frame_threshold = 3

        env_proc = MagicMock()
        env_proc.compute_rms.return_value = 15.0  # 超标

        for _ in range(3):
            checker.check("dev1", 0, "dev1:0", [MagicMock()], np.zeros(100), env_proc)
        bus.publish_alarm.assert_called_once()
        alarm = bus.publish_alarm.call_args[0][1]
        assert alarm["alarm_type"] == "ae_noise_floor"

    def test_noise_alarm_no_trigger_below_threshold(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.noise_threshold = 10.0
        checker.noise_frame_threshold = 3

        env_proc = MagicMock()
        env_proc.compute_rms.return_value = 5.0  # 不超标

        for _ in range(10):
            checker.check("dev1", 0, "dev1:0", [MagicMock()], np.zeros(100), env_proc)
        # 不应触发噪声报警（可能触发信号丢失，但有 hit 所以也不会）
        for call in bus.publish_alarm.call_args_list:
            assert call[0][1]["alarm_type"] != "ae_noise_floor"

    def test_noise_clears_when回落(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.noise_threshold = 10.0
        checker.noise_clear = 5.0
        checker.noise_frame_threshold = 2

        env_proc = MagicMock()
        env_proc.compute_rms.return_value = 15.0

        # 触发噪声报警
        for _ in range(2):
            checker.check("dev1", 0, "dev1:0", [MagicMock()], np.zeros(100), env_proc)
        assert bus.publish_alarm.call_count == 1

        # 噪声回落
        env_proc.compute_rms.return_value = 3.0
        checker.check("dev1", 0, "dev1:0", [MagicMock()], np.zeros(100), env_proc)
        bus.publish_alarm_cleared.assert_called_once()


class TestAEAlarmCheckerConfig:
    def test_set_thresholds(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.set_thresholds(
            signal_loss_frames=10,
            noise_threshold_mv=20.0,
            noise_clear_mv=8.0,
            noise_frame_count=5,
        )
        assert checker.signal_loss_threshold == 10
        assert checker.noise_threshold == 20.0
        assert checker.noise_clear == 8.0
        assert checker.noise_frame_threshold == 5

    def test_set_thresholds_clamps_invalid(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.set_thresholds(signal_loss_frames=0, noise_threshold_mv=-1.0)
        assert checker.signal_loss_threshold == 1
        assert checker.noise_threshold == 0.1

    def test_clear(self):
        bus = MagicMock()
        checker = AEAlarmChecker(bus)
        checker.signal_loss_threshold = 3
        env_proc = MagicMock()
        env_proc.compute_rms.return_value = 1.0

        # 2 帧无 hit
        for _ in range(2):
            checker.check("dev1", 0, "dev1:0", [], np.zeros(100), env_proc)

        checker.clear()
        # 清除后重新计数，再发 2 帧（总计不足 3 帧）
        for _ in range(2):
            checker.check("dev1", 0, "dev1:0", [], np.zeros(100), env_proc)
        # 总计 2 帧 < 阈值 3，不应触发
        bus.publish_alarm.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
