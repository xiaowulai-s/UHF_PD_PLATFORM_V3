# -*- coding: utf-8 -*-
"""`core/services/discharge_type_service.py` 与 `build_discharge_type` 的行为测试。

背景：`DischargeClassifier.classify()` 此前在生产路径上只有 `data.import_file`
一个调用点，实时链路上没人跑它，"实时事件的放电类型标签"一直空着。

这层测试要守住的**不是分类准不准**（那是领域问题，取决于参考剖面标定），而是：

- 服务发布的就是分类器对**同一批事件**给出的结论（不掺自己编的字段、不改口径）；
- 事件不足或分类抛异常时**不发**，而不是发一个 unknown 冒充结果；
- 负载里不能混进 numpy 标量——msgpack 序列化不了，会在传输层才炸；
- `discharge_type` 必须是可靠通道：标签跳变被合并掉就等于前端停在旧类型上。
"""

from __future__ import annotations

import msgpack
import numpy as np
import pytest

from core.data.discharge_classifier import DischargeClassifier
from core.foundation.signal_bus import PDDataBus
from core.processing.prpd_processor import PRPDProcessor
from core.protocol.frames import Qos, qos_of
from core.protocol.payloads import build_discharge_type
from core.services import discharge_type_service as dts


def _corona_points(count: int = 400) -> list[tuple[float, float]]:
    """负半周（270°~330°）集中、幅值稳定的放电——电晕的典型形态。"""
    return [(300.0 + (i % 11) - 5, 20.0 + (i % 5)) for i in range(count)]


def _result(points: list[tuple[float, float]]):
    proc = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100.0)
    for phase, amplitude in points:
        proc.add_event(phase, amplitude)
    return proc.compute()


@pytest.fixture
def env(qapp):
    bus = PDDataBus.instance()
    received: list[tuple[str, int, dict]] = []

    def _record(device: str, channel: int, info: dict) -> None:
        received.append((device, channel, info))

    bus.pd_type_classified.connect(_record)
    service = dts.DischargeTypeService(bus)
    yield bus, service, received
    service.stop()
    try:
        bus.pd_type_classified.disconnect(_record)
    except (RuntimeError, TypeError):
        pass


class TestPublishesClassifierVerdict:
    def test_payload_matches_classifier_output(self, env):
        """发布内容必须逐字等于分类器对同一批事件的输出，不掺服务自己的加工。"""
        bus, service, received = env
        result = _result(_corona_points())
        expected = DischargeClassifier.classify(
            np.array([e.phase for e in result.events], dtype=np.float64),
            np.array([e.amplitude for e in result.events], dtype=np.float64),
        )

        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, result)

        assert len(received) == 1
        device, channel, info = received[0]
        assert (device, channel) == ("sim-uhf-0", 0)
        assert info["type"] == expected["type"]
        assert info["name"] == expected["name"]
        assert info["confidence"] == pytest.approx(expected["confidence"])
        assert info["samples"] == len(result.events)

    def test_clear_pattern_is_not_unknown(self, env):
        """链路要真的产出一个判断，而不是恒 unknown（那等于没接）。"""
        bus, service, received = env
        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points()))
        assert received[0][2]["type"] != "unknown"
        assert received[0][2]["confidence"] > 0

    def test_payload_is_msgpack_serializable(self, env):
        """负载里混进 np.float64 会在传输层才炸，这里先拦住。"""
        bus, service, received = env
        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points()))
        packed = msgpack.packb(received[0][2], use_bin_type=True)
        assert msgpack.unpackb(packed, raw=False)["type"] == received[0][2]["type"]

    def test_multiple_channels_do_not_collide(self, env):
        bus, service, received = env
        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points()))
        bus.publish_prpd_result("sim-uhf-0", 1, _result(_corona_points()))
        assert [c for _, c, _ in received] == [0, 1]


class TestSkips:
    def test_too_few_events_is_skipped(self, env):
        bus, service, received = env
        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points(5)))
        assert received == []
        assert service.stats == {"classified": 0, "published": 0, "skipped": 1}

    def test_empty_result_is_skipped(self, env):
        bus, service, received = env
        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, _result([]))
        assert received == []

    def test_result_without_events_attribute_is_skipped(self, env):
        bus, service, received = env
        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, object())
        assert received == []

    def test_classifier_failure_is_swallowed(self, env, monkeypatch):
        """分类炸了不能拖垮 PRPD 发布，也不能发一个假结果出去。"""

        class _Boom:
            @staticmethod
            def classify(phases, amplitudes):
                raise RuntimeError("boom")

        monkeypatch.setattr(dts, "DischargeClassifier", _Boom)
        bus, service, received = env
        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points()))
        assert received == []
        assert service.stats["skipped"] == 1


class TestLifecycle:
    def test_not_subscribed_before_start(self, env):
        bus, _service, received = env
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points()))
        assert received == []

    def test_stop_releases_subscription(self, env):
        bus, service, received = env
        service.start()
        service.start()  # 重复 start 不该二次订阅
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points()))
        assert len(received) == 1
        service.stop()
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points()))
        assert len(received) == 1

    def test_stats_counts_published(self, env):
        bus, service, _received = env
        service.start()
        bus.publish_prpd_result("sim-uhf-0", 0, _result(_corona_points()))
        assert service.stats == {"classified": 1, "published": 1, "skipped": 0}


class TestFrameShape:
    def test_frame_fields(self):
        frame = build_discharge_type("dev-1", 2, {"type": "corona", "confidence": 0.8})
        assert frame.topic == "discharge_type"
        assert frame.kind == "event"
        assert frame.device == "dev-1"
        assert frame.channel == 2
        assert frame.meta["type"]["type"] == "corona"

    def test_topic_is_reliable(self):
        """标签跳变是状态变更，被有损通道合并掉就等于前端停在旧类型上。"""
        assert qos_of("discharge_type") == Qos.RELIABLE
