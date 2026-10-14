# -*- coding: utf-8 -*-
"""报警阈值一致性测试。

**为什么单独一个文件**：这里钉的是一个跨方法不变量——界面回显（`get_thresholds`）
与实际判定（`check_amplitude`）在默认阈值下必须是同一套。2026-10-14 的 GUI 体检发现
两者各写了一份回退值（判定取 40/20、回显写死 50/30），默认状态下设置页告诉现场
"中级 = 50 mV"而报警在 40 mV 触发，差 10 个百分点。单元测试全绿也发现不了这类问题，
因为它不跨方法。

注意 `check_amplitude` 有防抖：同一 (设备, 通道, 类型) 连续 3 次同等级才真的触发，
所以下面用 `fire()` 统一打 3 次。
"""

from __future__ import annotations

import pytest

from core.services.alarm_service import ALARM_LEVELS, PDAlarmService, default_percent_thresholds

DEBOUNCE = 3  # PDAlarmService._hysteresis_threshold 的默认值


@pytest.fixture
def service():
    # 不接 db_manager：这里只验阈值口径，不验落库
    return PDAlarmService(db_manager=None)


def fire(service, amplitude, times=DEBOUNCE, device="DEV-001", channel=1):
    """连续打 times 次同等级，返回最后一次的结果（None 表示没触发）。"""
    out = None
    for _ in range(times):
        out = service.check_amplitude(device, channel, amplitude)
    return out


# ── 1. 回退值必须是单一来源 ───────────────────────────────


def test_default_percent_matches_alarm_levels():
    """默认百分比常量必须从 ALARM_LEVELS 取，不能有两份。"""
    assert default_percent_thresholds() == {k: v["threshold_pct"] for k, v in ALARM_LEVELS.items()}


def test_get_thresholds_default_equals_default_percent(service):
    """界面回显的默认百分比 == 判定用的默认百分比（本次 bug 的直接回归）。"""
    shown = service.get_thresholds()["custom_percent"]
    assert shown == default_percent_thresholds()
    # 曾经的错误值，逐项钉住不让它回来
    assert shown["medium"] == 40.0
    assert shown["low"] == 20.0


def test_get_thresholds_absolute_matches_percent(service):
    """换算回 mV 必须与百分比自洽（默认 max_amplitude=100 时数值相等）。"""
    t = service.get_thresholds()
    assert t["max_amplitude"] == 100.0
    for level, pct in t["custom_percent"].items():
        assert t["custom_absolute_mv"][level] == pytest.approx(100.0 * pct / 100.0)


# ── 2. 跨方法不变量：回显的等级阈值 == 判定触发的阈值 ────────

ORDER = ["low", "medium", "high", "critical"]


@pytest.mark.parametrize("level", ORDER)
def test_level_boundary_agrees_with_readback(service, level):
    """界面报"某等级阈值是 X mV"，那么 X 必须触发该等级，且 X 之下一点就不该触发它。

    边界值**必须取自界面回显**（`get_thresholds`），不能自己用常量算——否则测的就只是
    "常量与常量自洽"，抓不到"回显与判定脱节"这类偏差。曾经回显写 50 而实际判定 40 时，
    拿常量算边界照样全绿。

    后半句同样关键：只测"X 能触发"抓不到"回显值偏高"（回显 50 而实际 40 时，打 50 照样
    触发 medium，看起来是对的）。必须再证明 X 以下不再触发同一等级。
    """
    abs_mv = service.get_thresholds()["custom_absolute_mv"]
    boundary = abs_mv[level]

    fired = fire(service, boundary)
    assert fired is not None, f"界面声称 {level} 阈值是 {boundary} mV，但该幅值连打 {DEBOUNCE} 次都没触发"
    assert fired["level"] == level

    just_below = boundary - 0.5
    got = fire(service, just_below)
    assert got is None or got["level"] != level, (
        f"{just_below} mV（界面声称的 {level} 阈值 {boundary} 之下）仍被判成 {level}，"
        "说明界面回显的阈值比实际判定阈值高"
    )

    idx = ORDER.index(level)
    if idx > 0:
        below = abs_mv[ORDER[idx - 1]] - 0.5
        got = fire(service, below)
        assert (
            got is None or got["level"] != level
        ), f"{below} mV 被判成 {level}，但按回显口径它属于 {ORDER[idx - 1]} 档"


def test_readback_absolute_is_exactly_the_trigger_threshold(service):
    """默认阈值下，界面显示的绝对 mV 必须逐档等于判定触发的那个值。"""
    abs_mv = service.get_thresholds()["custom_absolute_mv"]
    for level in ORDER:
        fired = fire(service, abs_mv[level])
        assert (
            fired is not None and fired["level"] == level
        ), f"回显说 {level} = {abs_mv[level]} mV，但该幅值判出的等级不是 {level}"


# ── 3. 自定义阈值后仍保持一致 ──────────────────────────────


def test_custom_thresholds_stay_consistent(service):
    """UI 下发自定义阈值后，回显与判定仍须同源。"""
    service.set_amplitude_thresholds(critical=90.0, high=70.0, medium=50.0, low=25.0, max_amplitude=100.0)
    shown = service.get_thresholds()
    assert shown["custom_percent"]["medium"] == pytest.approx(50.0)
    assert shown["custom_absolute_mv"]["medium"] == pytest.approx(50.0)

    assert fire(service, 50.0)["level"] == "medium"
    got = fire(service, 49.0)
    assert got is None or got["level"] == "low"


def test_custom_absolute_values_round_trip(service):
    """自定义阈值换算出的绝对 mV 必须与判定阈值逐档吻合。"""
    service.set_amplitude_thresholds(critical=88.0, high=66.0, medium=44.0, low=22.0, max_amplitude=100.0)
    abs_mv = service.get_thresholds()["custom_absolute_mv"]
    for level in ORDER:
        fired = fire(service, abs_mv[level])
        assert (
            fired is not None and fired["level"] == level
        ), f"回显说 {level} = {abs_mv[level]} mV，但该幅值判出的等级不是 {level}"
