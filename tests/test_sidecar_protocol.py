# -*- coding: utf-8 -*-
"""跨进程协议与 sidecar WebSocket 服务的往返测试。

这些测试是 Electron 重写的边界契约：前端只需信任这里断言过的字段形态。
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from core.foundation.signal_bus import PDDataBus
from core.protocol import CTRL_PATH, STREAM_PATH, Frame, SidecarServer, unpack_array
from core.protocol.payloads import MAX_PRPD_EVENTS

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - 旧版 websockets 布局
    from websockets import connect as ws_connect  # type: ignore[no-redef]


WAIT_TIMEOUT = 4.0


class WsClient:
    """在独立线程跑 asyncio 的测试客户端，主线程继续驱动 Qt 事件循环。"""

    def __init__(self, url: str):
        self.url = url
        self.inbox: list[bytes] = []
        self.closed = threading.Event()
        self.ready = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ws = None

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def open(self) -> None:
        self._thread.start()
        # 不能在主线程阻塞等 ready：Qt 事件循环正跑在主线程上，握手要靠它推进
        asyncio.run_coroutine_threadsafe(self._session(), self._loop)

    async def _session(self) -> None:
        try:
            async with ws_connect(self.url, max_size=None) as ws:
                self._ws = ws
                self.ready.set()
                async for msg in ws:
                    self.inbox.append(bytes(msg))
        except Exception:
            pass
        finally:
            self.closed.set()

    def send(self, payload: bytes) -> None:
        asyncio.run_coroutine_threadsafe(self._ws.send(payload), self._loop)

    def close(self) -> None:
        """优雅关闭连接。

        websockets 把内部 future 包在 asyncio.shield 里，cancel() 对其无效，
        所以只能走真正的关闭握手；而握手要 sidecar 应答，必须在等待期间继续泵
        Qt 事件循环，否则主线程一阻塞就自锁。
        """
        ws = self._ws
        if ws is not None:
            asyncio.run_coroutine_threadsafe(ws.close(), self._loop)
            app = QCoreApplication.instance()
            deadline = time.monotonic() + 3.0
            while not self.closed.is_set() and time.monotonic() < deadline:
                if app is not None:
                    app.processEvents()
                time.sleep(0.002)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


@pytest.fixture
def spin(qapp):
    """在等待期间持续推进 Qt 事件循环。"""

    def _spin(predicate, timeout=WAIT_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            qapp.processEvents()
            if predicate():
                qapp.processEvents()
                return True
            time.sleep(0.005)
        qapp.processEvents()
        return False

    return _spin


@pytest.fixture
def sidecar(qapp, spin):
    # 必须新建实例而不是取 PDDataBus.instance()：那是进程级单例，套件里
    # test_controller_integration 的 controller.shutdown() 会释放它的 _signals，
    # 之后任何 emit 都会抛 "Signal source has been deleted"。
    bus = PDDataBus()
    server = SidecarServer(bus)
    port, token = server.start()
    yield server, bus, port, token, spin
    server.stop()


def _open(sidecar, path: str, token: str | None = "use", wait_hello: bool = True) -> WsClient:
    _server, _bus, port, good_token, spin = sidecar
    if token == "use":
        token = good_token
    url = f"ws://127.0.0.1:{port}{path}?token={token}"
    client = WsClient(url)
    client.open()
    if wait_hello:
        assert spin(lambda: len(client.inbox) >= 1), f"未收到 {path} 的 hello"
    return client


def _decode(frames: list[Frame], topic: str) -> Frame:
    hits = [f for f in frames if f.topic == topic]
    assert hits, f"未收到 topic={topic}，已收到 {sorted(f.topic for f in frames)}"
    return hits[-1]


def _frames(inbox: list[bytes]) -> list[Frame]:
    import msgpack

    out = []
    for raw in inbox:
        body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        if body.get("k") in ("tele", "event"):
            out.append(Frame.from_bytes(raw))
    return out


def test_hello_advertises_topics_and_rpc(sidecar, spin):
    client = _open(sidecar, CTRL_PATH)
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        import msgpack

        hello = msgpack.unpackb(client.inbox[0], raw=False, strict_map_key=False)
        assert hello["k"] == "hello"
        assert hello["mode"] == "ctrl"
        assert hello["topics"]["waveform"] == "lossy"
        assert hello["topics"]["alarm"] == "reliable"
        assert "system.ping" in hello["rpc"]
    finally:
        client.close()


def test_rpc_roundtrip(sidecar, spin):
    client = _open(sidecar, CTRL_PATH)
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        import msgpack

        client.send(msgpack.packb({"k": "call", "i": "r1", "m": "system.ping"}, use_bin_type=True))
        assert spin(lambda: len([m for m in client.inbox if b"pong" in m]) >= 1)
        result = msgpack.unpackb([m for m in client.inbox if b"pong" in m][0], raw=False, strict_map_key=False)
        assert result["ok"] is True
        assert result["i"] == "r1"
        assert result["d"]["version"] >= 1
    finally:
        client.close()


def test_bad_token_is_rejected(sidecar, spin):
    client = _open(sidecar, CTRL_PATH, token="wrong", wait_hello=False)
    try:
        assert not spin(lambda: len(client.inbox) >= 1, timeout=1.0)
        assert client.closed.is_set()
    finally:
        client.close()


def test_waveform_payload_survives_roundtrip(sidecar, spin):
    _server, bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)
    samples = np.linspace(-1.0, 1.0, 4096, dtype=np.float64)
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        bus.publish_waveform("dev1", 2, samples, "uhf")
        assert spin(lambda: len(_frames(client.inbox)) >= 1)

        frame = _decode(_frames(client.inbox), "waveform")
        assert frame.device == "dev1"
        assert frame.channel == 2
        assert frame.meta["sensor_type"] == "uhf"
        recovered = unpack_array(frame.payload, frame.meta)
        assert recovered.shape == (4096,)
        assert recovered.dtype == np.float32
        assert np.allclose(recovered, samples, atol=1e-5)
    finally:
        client.close()


def test_lossy_topic_coalesces_to_latest(sidecar, spin):
    """上游连发 5 帧，一个刷新周期内客户端只应收到最后一帧。"""
    _server, bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        # 阻塞事件循环，让 5 次 publish 落在同一个合并窗口里
        for i in range(5):
            bus.publish_waveform("dev1", 0, np.full(16, float(i), dtype=np.float64), "uhf")

        assert spin(lambda: len(_frames(client.inbox)) >= 1)
        time.sleep(0.2)
        spin(lambda: False, timeout=0.2)

        waves = [f for f in _frames(client.inbox) if f.topic == "waveform"]
        assert len(waves) == 1, f"期望合并为 1 帧，实收 {len(waves)}"
        assert unpack_array(waves[0].payload, waves[0].meta)[-1] == pytest.approx(4.0)
        assert waves[0].seq == 4
    finally:
        client.close()


def test_alarm_reaches_ctrl_immediately(sidecar, spin):
    _server, bus, _port, _token, spin = sidecar
    client = _open(sidecar, CTRL_PATH)
    alarm = {
        "channel_id": 3,
        "level": "critical",
        "amplitude": np.float64(97.5),
        "description": "局放幅值超限",
        "timestamp": time.time(),
    }
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        bus.publish_alarm("dev1", alarm)
        assert spin(lambda: any(f.topic == "alarm" for f in _frames(client.inbox)))

        frame = _decode(_frames(client.inbox), "alarm")
        assert frame.kind == "event"
        assert frame.meta["alarm"]["level"] == "critical"
        assert frame.meta["alarm"]["amplitude"] == pytest.approx(97.5)
        assert frame.channel == 3
    finally:
        client.close()


def test_prpd_events_are_truncated(sidecar, spin):
    """PRPD 的 events 变长，突发时必须截断，否则一帧就能撑爆连接。"""
    _server, bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)
    events = [SimpleNamespace(phase=float(i % 360), amplitude=float(i), cycle=i) for i in range(MAX_PRPD_EVENTS + 500)]
    result = SimpleNamespace(
        matrix=np.zeros((360, 256), dtype=np.float64),
        max_amplitude=np.float64(12.5),
        events=events,
    )
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        bus.publish_prpd_result("dev1", 0, result)
        assert spin(lambda: len(_frames(client.inbox)) >= 1)

        frame = _decode(_frames(client.inbox), "prpd")
        assert frame.meta["events_truncated"] == MAX_PRPD_EVENTS + 500
        assert len(frame.meta["events"]) == MAX_PRPD_EVENTS
        matrix = unpack_array(frame.payload, frame.meta)
        assert matrix.shape == (360, 256)
        assert frame.meta["max_amplitude"] == pytest.approx(12.5)
    finally:
        client.close()


def test_fft_peaks_and_nan_handling(sidecar, spin):
    _server, bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)
    peak = SimpleNamespace(frequency=1.5e6, magnitude=0.75, is_harmonic=True, harmonic_order=3)
    result = SimpleNamespace(
        frequencies=np.arange(0, 100, dtype=np.float64),
        magnitudes=np.full(100, np.nan),
        peaks=[peak],
        noise_floor=np.float64(0.1),
        snr_db=np.float64(12.0),
    )
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        bus.publish_fft_result("dev1", 0, result, "uhf")
        assert spin(lambda: len(_frames(client.inbox)) >= 1)

        frame = _decode(_frames(client.inbox), "fft")
        assert frame.meta["peaks"][0]["harmonic_order"] == 3
        pair = unpack_array(frame.payload, frame.meta)
        assert pair.shape == (100, 2)
        assert np.isnan(pair[0, 1]), "NaN 必须原样过边界，不能变成 null"
    finally:
        client.close()


def test_subscribing_to_one_topic_filters_the_rest(sidecar, spin):
    server, bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)
    try:
        client.send(_sub_msg(["prps"]))
        # 等服务端真正收下订阅，避免用固定时长制造时序依赖
        assert spin(lambda: server._clients[0].topics == {"prps"}), "订阅未生效"

        bus.publish_waveform("dev1", 0, np.ones(8), "uhf")
        bus.publish_prps_result("dev1", 0, SimpleNamespace(matrix=np.ones((4, 360)), total_cycles=4))
        assert spin(lambda: len(_frames(client.inbox)) >= 1)
        time.sleep(0.15)
        spin(lambda: False, timeout=0.15)

        topics = {f.topic for f in _frames(client.inbox)}
        assert topics == {"prps"}, f"订阅过滤失效: {topics}"
    finally:
        client.close()


def test_empty_waveforms_are_not_emitted(sidecar, spin):
    _server, bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        bus.publish_waveform("dev1", 0, np.zeros(1), "uhf")
        bus.publish_prpd_result("dev1", 0, SimpleNamespace(matrix=np.zeros(0), events=[], max_amplitude=0.0))
        assert not spin(lambda: len(_frames(client.inbox)) >= 1, timeout=0.6)
    finally:
        client.close()


def _sub_msg(topics: list[str]) -> bytes:
    import msgpack

    return msgpack.packb({"k": "sub", "topics": topics}, use_bin_type=True)


def test_payload_uses_native_bin_not_ext_type():
    """钉住线格式：负载必须是 msgpack 原生 bin（Python 侧解为 bytes）。

    JS 侧依赖 @msgpack/msgpack 默认把 bin 解成 Uint8Array、无需 ExtensionCodec。
    一旦退回 ExtType，前端会静默拿到解不出来的对象。
    """
    import msgpack

    from core.protocol.payloads import build_waveform

    frame = build_waveform("dev1", 0, "uhf", np.arange(16, dtype=np.float64))
    raw = frame.to_bytes()

    body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
    assert isinstance(body["p"], (bytes, bytearray)), type(body["p"])
    assert not isinstance(body["p"], msgpack.ExtType)

    unpacked = Frame.from_bytes(raw)
    assert unpacked.payload == body["p"]
    assert unpack_array(unpacked.payload, unpacked.meta).tolist() == list(range(16))


def test_real_processor_output_matches_contract():
    """用真实处理器产物钉住字段名——SimpleNamespace 替身挡不住未来的属性重命名。"""
    from core.processing.fft_processor import FFTProcessor
    from core.processing.prpd_processor import PRPDProcessor
    from core.processing.prps_processor import PRPSProcessor
    from core.protocol import payloads as P

    rng = np.random.default_rng(7)
    t = np.arange(4096) / 100e6
    wf = np.sin(2 * np.pi * 1e6 * t) + 0.3 * np.sin(2 * np.pi * 2e6 * t) + rng.normal(0, 0.05, t.size)

    fft_result = FFTProcessor(sample_rate=100_000_000, window_size=4096).compute(wf, detect_peaks=True)
    frame = P.build_fft("dev1", 0, "uhf", fft_result)
    assert frame is not None
    if frame.meta["peaks"]:
        assert {"frequency", "magnitude", "is_harmonic", "harmonic_order"} <= set(frame.meta["peaks"][0])
    pair = unpack_array(frame.payload, frame.meta)
    assert pair.shape == (fft_result.frequencies.size, 2)
    assert np.allclose(pair[:, 0], fft_result.frequencies, atol=1e-2)

    prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100.0)
    prpd.add_event(phase=45.0, amplitude=60.0, polarity=0, energy=12.5, cycle=3)
    prpd.add_event(phase=225.0, amplitude=70.0, polarity=1, energy=9.0, cycle=4)
    prpd_result = prpd.compute()
    frame = P.build_prpd("dev1", 0, prpd_result)
    assert frame is not None
    # compute() 会按观测峰值收缩幅值轴，所以断言"忠实映射"而不是具体数值
    assert frame.meta["max_amplitude"] == pytest.approx(prpd_result.max_amplitude)
    assert frame.meta["min_amplitude"] == pytest.approx(prpd_result.min_amplitude)
    assert frame.meta["total_events"] == len(prpd_result.events) == 2
    assert "events_truncated" not in frame.meta
    negative = frame.meta["events"][1]
    assert negative["polarity"] == 1
    assert negative["energy"] == pytest.approx(9.0)
    assert negative["cycle"] == 4
    assert unpack_array(frame.payload, frame.meta).shape == prpd_result.matrix.shape

    prps = PRPSProcessor(rows=512, cols=360)
    prps.add_cycle(np.linspace(0.0, 50.0, 360))
    frame = P.build_prps("dev1", 0, prps.compute())
    assert frame is not None
    assert frame.meta["total_cycles"] == 1
    assert unpack_array(frame.payload, frame.meta).shape == (512, 360)


# ── M1: 后台 RPC 与只读查询 ────────────────────────────


def test_background_rpc_does_not_block_stream(sidecar, spin):
    """慢查询必须在后台线程跑：阻塞 Qt 事件循环会连带卡死 20FPS 推流。"""
    server, bus, _port, _token, spin = sidecar
    stream = _open(sidecar, STREAM_PATH)
    ctrl = _open(sidecar, CTRL_PATH)
    try:
        import msgpack

        server.register_rpc("test.slow", lambda _p: time.sleep(1.0) or "done", background=True)
        ctrl.send(msgpack.packb({"k": "call", "i": "s1", "m": "test.slow"}, use_bin_type=True))

        # 慢调用挂起期间，推流仍须继续出帧
        for i in range(3):
            bus.publish_waveform("dev1", 0, np.full(64, float(i)), "uhf")
            time.sleep(0.08)

        t0 = time.monotonic()
        assert spin(lambda: len(_frames(stream.inbox)) >= 1), "后台 RPC 阻塞了实时推流"
        assert time.monotonic() - t0 < 0.9, "首帧延迟接近慢调用时长，说明查询仍在事件循环里同步执行"

        assert spin(lambda: any(b"done" in m for m in ctrl.inbox)), "后台 RPC 未返回结果"
    finally:
        stream.close()
        ctrl.close()


def test_background_rpc_runs_off_event_loop(sidecar, spin):
    """后台 handler 必须工作在线程池线程，而不是主线程。"""
    server, _bus, _port, _token, spin = sidecar
    ctrl = _open(sidecar, CTRL_PATH)
    seen = {}
    try:
        import threading

        import msgpack

        def handler(_p):
            seen["thread"] = threading.get_ident()
            return threading.get_ident()

        server.register_rpc("test.threadid", handler, background=True)
        ctrl.send(msgpack.packb({"k": "call", "i": "t1", "m": "test.threadid"}, use_bin_type=True))
        assert spin(lambda: "thread" in seen)
        assert seen["thread"] != threading.get_ident(), "background handler 仍在主线程执行"
    finally:
        ctrl.close()


def test_sync_rpc_still_works(sidecar, spin):
    """background=False 的处理器保持同步语义。"""
    server, _bus, _port, _token, spin = sidecar
    ctrl = _open(sidecar, CTRL_PATH)
    try:
        import msgpack

        server.register_rpc("test.fast", lambda p: {"echo": p.get("x")})
        ctrl.send(msgpack.packb({"k": "call", "i": "f1", "m": "test.fast", "p": {"x": 42}}, use_bin_type=True))
        assert spin(lambda: any(b"echo" in m for m in ctrl.inbox))
    finally:
        ctrl.close()


def test_rpc_handler_exception_returns_error(sidecar, spin):
    server, _bus, _port, _token, spin = sidecar
    ctrl = _open(sidecar, CTRL_PATH)
    try:
        import msgpack

        def boom(_p):
            raise ValueError("参数非法")

        server.register_rpc("test.boom", boom, background=True)
        ctrl.send(msgpack.packb({"k": "call", "i": "e1", "m": "test.boom"}, use_bin_type=True))
        assert spin(lambda: any(b"\xa2ok" in m and b"False" not in m for m in ctrl.inbox) or len(ctrl.inbox) > 1)
        results = [
            msgpack.unpackb(m, raw=False, strict_map_key=False)
            for m in ctrl.inbox
            if msgpack.unpackb(m, raw=False, strict_map_key=False).get("k") == "result"
        ]
        err = [r for r in results if r["i"] == "e1"]
        assert err and err[0]["ok"] is False
        assert "参数非法" in err[0]["e"]
    finally:
        ctrl.close()


def test_storage_rpc_param_validation():
    """只读查询的参数校验必须是显式失败，不能静默返回空集。"""
    import pytest as _pt

    from core.protocol.storage_rpc import StorageRpc, _clamp_limit, _parse_time

    assert _parse_time("2026-09-24T10:00:00Z").year == 2026
    assert _parse_time("2026-09-24T10:00:00").tzinfo is not None
    assert _parse_time(1_700_000_000).tzinfo is not None
    assert _parse_time(None) is None
    with _pt.raises(ValueError):
        _parse_time("昨天下午")
    with _pt.raises(ValueError):
        _parse_time(object())

    assert _clamp_limit(None, 500) == 500
    assert _clamp_limit(10_000_000, 500) == 5000
    assert _clamp_limit(-5, 500) == 1
    assert _clamp_limit("abc", 500) == 500

    rpc = StorageRpc(db_manager=object())
    names = [n for n, _ in rpc.handlers()]
    assert "device.list" in names
    assert "storage.query_events" not in names, "没有 storage 服务时不应注册历史查询"

    with _pt.raises(ValueError):
        rpc.query_events({})
    with _pt.raises(ValueError):
        rpc.query_trends({})
    with _pt.raises(ValueError):
        rpc.query_trends({"device_id": "d1", "period": "5m"})


def test_storage_rpc_delegates_to_storage_service():
    from unittest.mock import MagicMock

    from core.protocol.storage_rpc import StorageRpc

    storage = MagicMock()
    storage.query_events.return_value = [{"id": 1}]
    rpc = StorageRpc(db_manager=object(), storage=storage)

    out = rpc.query_events({"device_id": "d1", "channel_id": "2", "limit": "30", "start_time": 1_700_000_000})
    assert out == [{"id": 1}]
    kwargs = storage.query_events.call_args.kwargs
    assert kwargs["device_id"] == "d1"
    assert kwargs["channel_id"] == 2
    assert kwargs["limit"] == 30
    assert kwargs["start_time"].tzinfo is not None


# ── M1.4: stats 推送 ──────────────────────────────────


def test_collect_stats_only_includes_available_services():
    from types import SimpleNamespace

    from core.protocol.stats_push import collect_stats

    acq = SimpleNamespace(
        get_stats=lambda: {"waveforms_processed": 12, "pd_events_detected": 3, "start_time": time.time() - 60},
        get_observed_max_amp=lambda: {"d:0": 41.5, "d:1": 12.0},
    )

    class _Sources:
        """active_source_id 在真实 DataSourceManager 里是 property，替身必须照此建模。"""

        def get_all_status(self):
            return {
                "sim-uhf-0": {"status": "running", "generated": 99},
                "hw-1": {"status": "error"},
            }

        @property
        def active_source_id(self):
            return "sim-uhf-0"

    snap = collect_stats(acq, sources=_Sources(), transport={"sent_frames": 5})
    assert snap["waveforms_processed"] == 12
    assert snap["observed_max_amplitude"] == 41.5
    assert snap["channels_observed"] == 2
    assert snap["online_count"] == 1, "只有 running/connected 计入在线"
    assert snap["source_count"] == 2
    assert snap["active_source"] == "sim-uhf-0"
    assert snap["transport"] == {"sent_frames": 5}
    assert 59 < snap["acq_uptime_s"] < 62
    assert "alarms" not in snap and "storage" not in snap, "未提供的服务不应凭空造出字段"

    bare = collect_stats(None)
    assert bare == {"observed_max_amplitude": 0.0, "channels_observed": 0} or "observed_max_amplitude" not in bare


def test_stats_frame_reaches_client(sidecar, spin):
    """stats 帧必须真的能从 /stream 收到，而不只是单测过聚合函数。"""
    from types import SimpleNamespace

    from core.protocol.stats_push import StatsPusher

    server, _bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)
    acq = SimpleNamespace(
        get_stats=lambda: {"waveforms_processed": 7, "start_time": time.time()},
        get_observed_max_amp=lambda: {"d:0": 3.5},
    )
    pusher = StatsPusher(server, acquisition=acq)
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        pusher.push_once()
        assert spin(lambda: len(_frames(client.inbox)) >= 1)

        frame = _decode(_frames(client.inbox), "stats")
        assert frame.meta["stats"]["waveforms_processed"] == 7
        assert frame.meta["stats"]["observed_max_amplitude"] == 3.5
    finally:
        pusher.stop()
        client.close()


def test_stats_pusher_timer_fires_without_manual_call(sidecar, spin):
    """必须验证 QTimer 路径：只测 push_once() 会让启动接线错误一路漏到真进程。"""
    from types import SimpleNamespace

    from core.protocol.stats_push import StatsPusher

    server, _bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)

    class _Sources:
        def get_all_status(self):
            return {"sim-uhf-0": {"status": "running"}}

        @property
        def active_source_id(self):
            return "sim-uhf-0"

    acq = SimpleNamespace(
        get_stats=lambda: {"waveforms_processed": 1, "start_time": 0.0},
        get_observed_max_amp=lambda: {},
    )
    pusher = StatsPusher(server, acquisition=acq, sources=_Sources(), interval_ms=100)
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        before = len(_frames(client.inbox))
        pusher.start()
        assert spin(lambda: len(_frames(client.inbox)) > before + 1), "定时器未持续推送 stats"
        assert spin(lambda: _decode(_frames(client.inbox), "stats").meta["stats"]["active_source"] == "sim-uhf-0")
        assert spin(lambda: server.diagnostics()["uptime_s"] > 0), "进程 uptime 未随 start() 记录"
    finally:
        pusher.stop()
        client.close()


# ── M1.3: 日志桥接 ────────────────────────────────────


def test_log_records_arrive_batched(sidecar, spin):
    import logging

    from core.protocol.log_stream import attach

    server, _bus, _port, _token, spin = sidecar
    client = _open(sidecar, STREAM_PATH)
    log = logging.getLogger("probe1")
    handler = attach(server, logger=log, interval_ms=50, level=logging.INFO)
    try:
        assert spin(lambda: len(client.inbox) >= 1)
        before = len(_frames(client.inbox))
        log.info("hello %s", "world")
        log.warning("低电平告警")
        assert spin(lambda: len(_frames(client.inbox)) > before)

        batch = next(f for f in _frames(client.inbox) if f.topic == "log")
        lines = batch.meta["lines"]
        assert batch.meta["count"] == len(lines)
        assert "hello world" in [e["message"] for e in lines]
        assert any(e["level"] == "WARNING" and e["message"] == "低电平告警" for e in lines)
        assert all(e["logger"].startswith("probe1") for e in lines)
        assert lines[0]["seq"] >= 1
    finally:
        handler.detach()
        client.close()


def test_attach_widens_logger_level(sidecar):
    """attach() 必须同时放宽 logger 级别，否则 INFO 日志在到达 handler 前就被丢掉。"""
    import logging

    from core.protocol.log_stream import attach

    server, _bus, _port, _token, _spin = sidecar
    log = logging.getLogger("probe_level")
    log.setLevel(logging.CRITICAL)
    try:
        handler = attach(server, logger=log, level=logging.INFO)
        assert log.getEffectiveLevel() == logging.INFO
        assert handler.previous_level == logging.CRITICAL
    finally:
        handler.detach()
    assert log.level == logging.CRITICAL, "detach 应还原原级别"


def test_log_history_is_snapped_to_new_client(sidecar, spin):
    """接入前已产生的日志必须补发给新客户端，否则前端首屏只有增量。"""
    import logging

    from core.protocol.log_stream import attach

    server, _bus, _port, _token, spin = sidecar
    log = logging.getLogger("probe_hist")
    handler = attach(server, logger=log, interval_ms=20, level=logging.INFO)
    late = None
    try:
        first = _open(sidecar, STREAM_PATH)
        assert spin(lambda: len(first.inbox) >= 1)
        for i in range(5):
            log.info("历史 %d", i)
        assert spin(lambda: handler.buffered >= 5)
        assert spin(lambda: any(f.topic == "log" for f in _frames(first.inbox)))
        first.close()

        late = _open(sidecar, STREAM_PATH)
        assert spin(lambda: any(f.topic == "log" for f in _frames(late.inbox))), "新客户端未收到历史快照"
        lines = _decode(_frames(late.inbox), "log").meta["lines"]
        assert [e["message"] for e in lines] == [f"历史 {i}" for i in range(5)]
    finally:
        handler.detach()
        if late is not None:
            late.close()


def test_log_message_truncated_and_log_topic_is_lossy():
    import logging

    from core.protocol.frames import TOPICS, Qos
    from core.protocol.log_stream import MAX_MESSAGE_CHARS, WsLogHandler

    assert TOPICS["log"] is Qos.LOSSY, "日志必须是有损批量，否则刷屏会挤掉告警通道"

    sent = []

    class _FakeServer:
        def publish(self, frame):
            sent.append(frame)

    handler = WsLogHandler(_FakeServer(), interval_ms=10_000, level=logging.INFO)
    handler.emit(
        logging.makeLogRecord(
            {
                "levelno": logging.ERROR,
                "levelname": "ERROR",
                "name": "probe",
                "module": "m",
                "lineno": 1,
                "created": 0.0,
                "msg": "x" * (MAX_MESSAGE_CHARS + 500),
            }
        )
    )
    assert handler._batch[0]["message"].endswith("…<截断>")
    assert len(handler._batch[0]["message"]) <= MAX_MESSAGE_CHARS + 10


# ── M1.2: 写入 RPC 的参数校验 ─────────────────────────


def _rpc_with_services():
    from core.protocol.write_rpc import WriteRpc

    acq = MagicMock()
    alarm = MagicMock()
    return WriteRpc(db_manager=object(), acquisition=acq, alarm=alarm), acq, alarm


def test_write_rpc_validates_coupling_type():
    from core.protocol.write_rpc import COUPLING_TYPES

    rpc, acq, _alarm = _rpc_with_services()
    out = rpc.set_channel_coupling({"device_id": "d1", "channel_id": "2", "coupling_type": "ae"})
    assert out["coupling_type"] == "ae"
    assert out["channel_id"] == 2
    acq.set_channel_coupling_type.assert_called_once_with("d1", 2, "ae")

    with pytest.raises(ValueError, match="coupling_type"):
        rpc.set_channel_coupling({"device_id": "d1", "channel_id": 0, "coupling_type": "bogus"})
    for missing in ("device_id", "channel_id", "coupling_type"):
        params = {"device_id": "d1", "channel_id": 0, "coupling_type": "uhf"}
        params.pop(missing)
        with pytest.raises(ValueError, match=missing):
            rpc.set_channel_coupling(params)
    assert "ae" in COUPLING_TYPES


def test_write_rpc_rejects_non_positive_amplitude():
    rpc, _acq, alarm = _rpc_with_services()
    for bad in (0, -1, "abc", None):
        with pytest.raises(ValueError):
            rpc.set_amplitude_threshold({"device_id": "d1", "max_amplitude": bad})
    alarm.set_amplitude_threshold.assert_not_called()


def test_write_rpc_ae_thresholds_only_accepts_known_keys():
    rpc, acq, _alarm = _rpc_with_services()
    out = rpc.set_ae_thresholds({"noise_threshold_mv": "12.5", "signal_loss_frames": "30", "evil": 1})
    assert out == {"noise_threshold_mv": 12.5, "signal_loss_frames": 30}
    kwargs = acq.set_ae_alarm_thresholds.call_args.kwargs
    assert "evil" not in kwargs, "未知字段必须被丢弃，不能透传进服务"

    with pytest.raises(ValueError):
        rpc.set_ae_thresholds({})


def test_write_rpc_device_save_whitelists_columns():
    """前端不能借 dict 透传任意列——尤其是 created_at/status。"""
    from core.protocol.write_rpc import CHANNEL_FIELDS, DEVICE_FIELDS, _pick

    assert "created_at" not in DEVICE_FIELDS and "status" not in DEVICE_FIELDS
    assert "id" not in CHANNEL_FIELDS and "device_id" not in CHANNEL_FIELDS
    rpc, _acq, _alarm = _rpc_with_services()

    with pytest.raises(ValueError, match="name"):
        rpc.device_save({"device": {"id": "d1"}})
    with pytest.raises(ValueError, match="channels"):
        rpc.device_save({"device": {"id": "d1", "name": "n"}, "channels": "not-a-list"})
    with pytest.raises(ValueError, match="重复"):
        rpc.device_save(
            {
                "device": {"id": "d1", "name": "n"},
                "channels": [{"channel_index": 0}, {"channel_index": 0}],
            }
        )
    assert _pick({"channel_index": 0, "name": "a", "evil": 1}, CHANNEL_FIELDS) == {"channel_index": 0, "name": "a"}
    assert _pick({"created_at": "x", "status": 9, "id": "y"}, DEVICE_FIELDS) == {"id": "y"}


@pytest.mark.parametrize("background", [False, True])
def test_validation_error_is_not_reported_as_serialization_failure(sidecar, spin, background):
    """ValueError 必须原样回传，不能被"序列化失败"分支吞掉。"""
    import msgpack

    server, _bus, _port, _token, spin = sidecar
    ctrl = _open(sidecar, CTRL_PATH)
    try:

        def handler(_p):
            raise ValueError("coupling_type 非法")

        server.register_rpc("test.validate", handler, background=background)
        ctrl.send(msgpack.packb({"k": "call", "i": "v1", "m": "test.validate"}, use_bin_type=True))
        assert spin(lambda: any(b"validate" in m or b"\xa1e" in m for m in ctrl.inbox))
        results = [
            msgpack.unpackb(m, raw=False, strict_map_key=False)
            for m in ctrl.inbox
            if msgpack.unpackb(m, raw=False, strict_map_key=False).get("k") == "result"
        ]
        hit = [r for r in results if r["i"] == "v1"]
        assert hit and hit[0]["ok"] is False
        assert "非法" in hit[0]["e"]
        assert "序列化失败" not in hit[0]["e"]
    finally:
        ctrl.close()
