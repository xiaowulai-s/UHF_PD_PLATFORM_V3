# -*- coding: utf-8 -*-
"""AE 报警阈值与单通道量程的 RPC 口径测试。

**为什么单独一个文件**：这两项此前只有 setter 没有 getter（AE 阈值），
或者有 getter 但前端没接线（单通道量程）。补 UI 的前提是"回显的数必须就是判定用的数"——
`PDAlarmService` 上已经因为回显与判定各写一份而出过一次事故
（界面说中级 50 mV、实际 40 mV 触发，见 docs/AUDIT_AND_ROADMAP.md §4-①）。
这里把同样的不变量钉在 AE 侧。
"""

from __future__ import annotations

import pytest

from core.processing.ae_pipeline import AEAlarmChecker
from core.protocol.write_rpc import WriteRpc
from core.services.alarm_service import PDAlarmService


class _FakeBus:
    """AEAlarmChecker 只用它发告警，这里不关心事件。"""

    def publish_alarm(self, *a, **k) -> None:
        pass

    def publish_alarm_cleared(self, *a, **k) -> None:
        pass


class _FakeAcq:
    """最小采集服务替身：只保留 AE 阈值与 PRPD 模式这两条被 RPC 用到的路径。"""

    def __init__(self) -> None:
        self.checker = AEAlarmChecker(_FakeBus())
        self._mode = "heatmap"

    def set_ae_alarm_thresholds(self, **kwargs) -> None:
        self.checker.set_thresholds(**kwargs)

    def get_ae_alarm_thresholds(self) -> dict:
        return self.checker.get_thresholds()

    def set_prpd_mode(self, mode: str) -> str:
        self._mode = mode
        return mode

    def get_prpd_mode(self) -> str:
        return self._mode


# ── 1. AE 阈值的默认值与回显 ──────────────────────────────


def test_ae_defaults_are_reported():
    c = AEAlarmChecker(_FakeBus())
    assert c.get_thresholds() == {
        "signal_loss_frames": 20,
        "noise_threshold_mv": 10.0,
        "noise_clear_mv": 5.0,
        "noise_frame_count": 10,
    }


def test_get_thresholds_returns_effective_not_requested():
    """回显必须是**夹紧后**的生效值：请求 0 帧会被存成 1，回显 0 就是骗人。"""
    c = AEAlarmChecker(_FakeBus())
    c.set_thresholds(signal_loss_frames=0, noise_threshold_mv=0.0, noise_clear_mv=0.0, noise_frame_count=0)
    t = c.get_thresholds()
    assert t["signal_loss_frames"] == 1, "请求 0 帧被夹到 1，回显也必须是 1"
    assert t["noise_threshold_mv"] == pytest.approx(0.1)
    assert t["noise_clear_mv"] == pytest.approx(0.1)
    assert t["noise_frame_count"] == 1


def test_set_thresholds_round_trips_normal_values():
    c = AEAlarmChecker(_FakeBus())
    c.set_thresholds(signal_loss_frames=7, noise_threshold_mv=12.5, noise_clear_mv=3.5, noise_frame_count=4)
    assert c.get_thresholds() == {
        "signal_loss_frames": 7,
        "noise_threshold_mv": 12.5,
        "noise_clear_mv": 3.5,
        "noise_frame_count": 4,
    }


# ── 2. RPC 层 ─────────────────────────────────────────────


@pytest.fixture
def rpc():
    return WriteRpc(db_manager=None, acquisition=_FakeAcq(), alarm=PDAlarmService())


def test_get_ae_thresholds_rpc_registered_and_reads_back(rpc):
    names = dict(rpc.runtime_handlers())
    assert "config.get_ae_thresholds" in names, "缺 getter 的话设置页只能硬编码默认值回显"

    applied = rpc.set_ae_thresholds({"signal_loss_frames": 5, "noise_threshold_mv": 8.0})
    assert applied == {"signal_loss_frames": 5, "noise_threshold_mv": 8.0}

    shown = names["config.get_ae_thresholds"]({})
    assert shown["signal_loss_frames"] == 5
    assert shown["noise_threshold_mv"] == pytest.approx(8.0)
    # 没改的两项保持默认
    assert shown["noise_clear_mv"] == pytest.approx(5.0)
    assert shown["noise_frame_count"] == 10


def test_set_ae_thresholds_requires_at_least_one_field(rpc):
    with pytest.raises(ValueError, match="至少要提供"):
        rpc.set_ae_thresholds({})


def test_set_ae_thresholds_rejects_unknown_fields_only(rpc):
    """只给未知字段等价于什么都没给，必须报错而不是静默成功。"""
    with pytest.raises(ValueError, match="至少要提供"):
        rpc.set_ae_thresholds({"not_a_field": 1})


def test_ae_rpc_not_registered_without_acquisition():
    """没有采集服务时这两条 RPC 不该出现（条件注册，前端要能容忍）。"""
    names = dict(WriteRpc(db_manager=None, acquisition=None, alarm=PDAlarmService()).runtime_handlers())
    assert "config.set_ae_thresholds" not in names
    assert "config.get_ae_thresholds" not in names


# ── 3. 单通道量程：写入后必须能从 get_thresholds 读回 ───────


def test_channel_max_amplitude_round_trips_through_get_thresholds(rpc):
    """界面要在通道表里回显量程，读回路径就是 config.get_thresholds 的 per_device。

    这里钉住键名格式 `{device}:{channel}`——界面按它取值，改了就对不上。
    """
    alarm: PDAlarmService = rpc._alarm  # noqa: SLF001 - 测试内直取，避免再造一个服务
    rpc.set_channel_max_amplitude({"device_id": "sim-uhf-0", "channel_id": 1, "max_amplitude": 250.0})
    rpc.set_channel_max_amplitude({"device_id": "sim-uhf-0", "channel_id": 2, "max_amplitude": 800.0})

    per_device = alarm.get_thresholds()["per_device"]
    assert per_device["channel_max"]["sim-uhf-0:1"] == pytest.approx(250.0)
    assert per_device["channel_max"]["sim-uhf-0:2"] == pytest.approx(800.0)


def test_channel_max_amplitude_rejects_non_positive(rpc):
    with pytest.raises(ValueError):
        rpc.set_channel_max_amplitude({"device_id": "d", "channel_id": 1, "max_amplitude": 0})
    with pytest.raises(ValueError):
        rpc.set_channel_max_amplitude({"device_id": "d", "channel_id": 1, "max_amplitude": -5})


def test_channel_max_amplitude_overrides_global_in_judgement(rpc):
    """写入的单通道量程必须真的参与判定——否则又是一个"回显与判定脱节"。"""
    alarm: PDAlarmService = rpc._alarm  # noqa: SLF001
    # 全局量程 100 mV，把通道 1 的量程设成 200 mV：40 mV 只占 20%，不该报警
    rpc.set_channel_max_amplitude({"device_id": "dev-x", "channel_id": 1, "max_amplitude": 200.0})
    assert alarm.check_amplitude("dev-x", 1, 40.0) is None

    # 同一幅值落在通道 2（沿用全局 100 mV）就是 40%，正好达到 medium 档
    # （medium 的阈值是 40%，不是 50%——见 default_percent_thresholds）
    for _ in range(3):  # 防抖要连续 3 次
        got = alarm.check_amplitude("dev-x", 2, 40.0)
    assert got is not None and got["level"] == "medium"
