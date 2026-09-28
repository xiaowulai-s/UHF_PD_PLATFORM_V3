"""Electron 前端的本地 WebSocket sidecar 服务端。

单端口、双通道语义，由连接路径区分：

- ``/stream``  有损遥测。同一 (topic, device, channel) 在一个刷新周期内只保留最新帧，
  上游突发频率再高也不会拖垮连接；客户端可用 seq 跳变自行检测丢帧。
- ``/ctrl``    无损事件（报警、设备状态、采集错误、数据源变更）与 RPC 调用。

安全边界：只绑定 127.0.0.1，且要求连接携带 ``?token=<token>``。本机能起 Web 服务，
仅靠回环地址不足以阻止任意本机网页发起 WebSocket 握手，而 PySide6 6.10 的
QWebSocket 又读不到 Origin 头，因此用 token 兜底。token 由 start() 生成，
通过 Electron 的 spawn 环境变量传下去。
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Callable
from urllib.parse import parse_qs

import msgpack
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from ..foundation.data_bus import SubscriptionManager
from ..foundation.signal_bus import PDDataBus
from . import payloads as P
from .frames import (
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    TOPICS,
    Frame,
    Qos,
    RpcRequest,
    encode_error,
    encode_result,
    qos_of,
)

logger = logging.getLogger(__name__)

STREAM_PATH = "/stream"
CTRL_PATH = "/ctrl"

FLUSH_INTERVAL_MS = 50
# 客户端落后超过此字节数时，有损帧直接丢弃，等下一帧合并
BACKPRESSURE_BYTES = 4 * 1024 * 1024
# 无损帧的容忍上限，超过只告警不丢弃——报警不能静默消失
RELIABLE_WARN_BYTES = 8 * 1024 * 1024

_PACK_ERRORS = (msgpack.PackException, ValueError, TypeError)
_UNPACK_ERRORS = (msgpack.UnpackException, msgpack.FormatError, ValueError, TypeError)


class _Client(QObject):
    def __init__(self, ws: QWebSocket, ctrl: bool, parent: QObject | None = None):
        super().__init__(parent)
        self.ws = ws
        self.ctrl = ctrl
        self.topics: set[str] = set()
        self.pending: dict[tuple[str, str, int], Frame] = {}


class _RpcResults(QObject):
    """工作线程向主线程回投 RPC 结果。

    QWebSocket 不是线程安全的，只能由创建它的事件循环线程写；同时把 msgpack
    序列化也放到工作线程，避免大结果集卡住 /stream 的推流节拍。
    """

    ready = Signal(object, bytes)


class _RpcJob(QRunnable):
    def __init__(self, client: _Client, req_id: str, handler: Callable, params: dict, emitter: _RpcResults):
        super().__init__()
        self._client = client
        self._req_id = req_id
        self._handler = handler
        self._params = params
        self._emitter = emitter
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102 - 工作线程入口
        # 先执行、再序列化：两类的错误来源完全不同，混在一起会让前端把
        # 参数校验失败误读成服务端故障。
        try:
            data = self._handler(self._params)
        except Exception as exc:
            logger.warning("后台 RPC 执行失败: %s", exc, exc_info=True)
            self._emitter.ready.emit(self._client, encode_result(self._req_id, False, error=str(exc)))
            return
        try:
            raw = encode_result(self._req_id, True, data=data)
        except _PACK_ERRORS as exc:
            logger.error("RPC 返回值无法序列化: %s", exc)
            raw = encode_result(self._req_id, False, error=f"序列化失败: {exc}")
        self._emitter.ready.emit(self._client, raw)


class SidecarServer(QObject):
    """把 PDDataBus 的 Qt 信号扇出到 WebSocket 客户端。"""

    def __init__(self, bus: PDDataBus | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._bus = bus or PDDataBus.instance()
        self._server: QWebSocketServer | None = None
        self._sub_mgr: SubscriptionManager | None = None
        self._clients: list[_Client] = []
        self._seq: dict[str, int] = {}
        self._rpc: dict[str, tuple[Callable[[dict[str, Any]], Any], bool]] = {}
        self._token = ""
        self._started_at = 0.0
        self._catchups: list[Callable[[_Client], list[bytes]]] = []
        self._sent_frames = 0
        self._dropped_frames = 0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._results = _RpcResults(self)
        self._results.ready.connect(self._deliver_result)
        self._flush = QTimer(self)
        self._flush.setInterval(FLUSH_INTERVAL_MS)
        self._flush.timeout.connect(self._flush_pending)
        self._register_builtin_rpc()

    # ── 生命周期 ─────────────────────────────────────

    def start(self, host: str = "127.0.0.1", port: int = 0) -> tuple[int, str]:
        """开始监听，返回 (端口, token)。"""
        if self._server is not None:
            return int(self._server.serverPort()), self._token

        self._token = secrets.token_urlsafe(24)
        server = QWebSocketServer("uhf-pd-sidecar", QWebSocketServer.SslMode.NonSecureMode, self)
        server.newConnection.connect(self._on_new_connection)

        if not server.listen(QHostAddress(host), port):
            error = server.errorString()
            server.deleteLater()
            raise RuntimeError(f"sidecar 监听失败 {host}:{port} - {error}")

        self._server = server
        self._started_at = time.time()
        # PDDataBus 的 _signals 归属主线程，跨线程 emit 经队列投递回这里，
        # 所以 _dispatch 总在创建套件的线程执行，直接写 QWebSocket 是安全的。
        self._subscribe_bus()
        self._flush.start()

        bound = int(server.serverPort())
        logger.info("sidecar 已监听 %s:%d (协议 v%d)", host, bound, PROTOCOL_VERSION)
        return bound, self._token

    def stop(self) -> None:
        self._flush.stop()
        self._pool.waitForDone(5000)
        for client in list(self._clients):
            client.ws.close()
        self._clients.clear()
        self._unsubscribe_bus()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        logger.info("sidecar 已停止，累计发送 %d 帧 / 丢弃 %d 帧", self._sent_frames, self._dropped_frames)

    @property
    def port(self) -> int:
        return int(self._server.serverPort()) if self._server else -1

    @property
    def token(self) -> str:
        return self._token

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def register_rpc(self, method: str, handler: Callable[[dict[str, Any]], Any], background: bool = False) -> None:
        """注册 RPC 方法。background=True 时在工作线程执行，适用于 SQLite 查询。"""
        self._rpc[method] = (handler, background)

    def register_catchup(self, provider: Callable[[_Client], list[bytes]]) -> None:
        """注册"补历史"回调。

        新客户端接入后调用一次，用于把环形缓冲里的既有内容（如日志）发给它，
        否则前端首屏只会看到增量、看不到历史。
        """
        self._catchups.append(provider)

    def _deliver_result(self, client: _Client, raw: bytes) -> None:
        if client in self._clients:
            self._send_raw(client, raw)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "sent_frames": self._sent_frames,
            "dropped_frames": self._dropped_frames,
            "clients": len(self._clients),
            "seq": dict(self._seq),
            # 采集服务的 start_time 只在显式 start() 时才写入，模拟器直推路径下恒为 0，
            # 所以进程运行时长由服务端自己记。
            "uptime_s": round(time.time() - self._started_at, 1) if self._started_at else 0.0,
        }

    # ── 总线订阅 ─────────────────────────────────────

    def _subscribe_bus(self) -> None:
        self._sub_mgr = SubscriptionManager()
        bus = self._bus
        reg = self._sub_mgr.register
        reg(bus.waveform_received, self._on_waveform)
        reg(bus.fft_result_ready, self._on_fft)
        reg(bus.prpd_result_ready, self._on_prpd)
        reg(bus.prps_result_ready, self._on_prps)
        reg(bus.ae_envelope_ready, self._on_ae_envelope)
        reg(bus.ae_hit_detected, self._on_ae_hit)
        reg(bus.pd_alarm_triggered, self._on_alarm)
        reg(bus.pd_alarm_cleared, self._on_alarm_cleared)
        reg(bus.device_status_changed, self._on_device_status)
        reg(bus.acquisition_started, self._on_acq_started)
        reg(bus.acquisition_stopped, self._on_acq_stopped)
        reg(bus.acquisition_error, self._on_acq_error)
        reg(bus.source_attached, self._on_source_attached)
        reg(bus.source_detached, self._on_source_detached)
        reg(bus.active_source_changed, self._on_source_active)
        logger.debug("sidecar 已订阅 %d 个总线信号", self._sub_mgr.subscription_count)

    def _unsubscribe_bus(self) -> None:
        if self._sub_mgr is not None:
            self._sub_mgr.release_all()
            self._sub_mgr = None

    @Slot(str, int, str, object)
    def _on_waveform(self, device: str, channel: int, sensor_type: str, data: Any) -> None:
        self._emit(P.build_waveform(device, channel, sensor_type, data))

    @Slot(str, int, str, object)
    def _on_fft(self, device: str, channel: int, sensor_type: str, result: Any) -> None:
        self._emit(P.build_fft(device, channel, sensor_type, result))

    @Slot(str, int, object)
    def _on_prpd(self, device: str, channel: int, result: Any) -> None:
        self._emit(P.build_prpd(device, channel, result))

    @Slot(str, int, object)
    def _on_prps(self, device: str, channel: int, result: Any) -> None:
        self._emit(P.build_prps(device, channel, result))

    @Slot(str, int, object)
    def _on_ae_envelope(self, device: str, channel: int, data: Any) -> None:
        self._emit(P.build_ae_envelope(device, channel, data))

    @Slot(str, int, object)
    def _on_ae_hit(self, device: str, channel: int, hit: Any) -> None:
        self._emit(P.build_ae_hit(device, channel, hit))

    @Slot(str, dict)
    def _on_alarm(self, device: str, alarm: dict) -> None:
        self._emit(P.build_alarm(device, alarm))

    @Slot(str, str, str)
    def _on_alarm_cleared(self, device: str, alarm_type: str, description: str) -> None:
        self._emit(P.build_alarm_cleared(device, alarm_type, description))

    @Slot(str, int, dict)
    def _on_device_status(self, device: str, status: int, data: dict) -> None:
        self._emit(P.build_device_status(device, status, data))

    @Slot(str)
    def _on_acq_started(self, device: str) -> None:
        self._emit(P.build_acquisition("started", device))

    @Slot(str)
    def _on_acq_stopped(self, device: str) -> None:
        self._emit(P.build_acquisition("stopped", device))

    @Slot(str, str)
    def _on_acq_error(self, device: str, message: str) -> None:
        self._emit(P.build_acquisition("error", device, message))

    @Slot(str)
    def _on_source_attached(self, source_id: str) -> None:
        self._emit(P.build_source("attached", source_id))

    @Slot(str)
    def _on_source_detached(self, source_id: str) -> None:
        self._emit(P.build_source("detached", source_id))

    @Slot(str)
    def _on_source_active(self, source_id: str) -> None:
        self._emit(P.build_source("active", source_id))

    # ── 扇出 ─────────────────────────────────────────

    def publish(self, frame: Frame | None) -> None:
        """推送非总线来源的帧（如定时统计快照）。"""
        self._emit(frame)

    def _emit(self, frame: Frame | None) -> None:
        if frame is None:
            return
        frame.seq = self._seq.get(frame.topic, 0)
        self._seq[frame.topic] = frame.seq + 1

        lossy = qos_of(frame.topic) is Qos.LOSSY
        for client in self._clients:
            if client.topics and frame.topic not in client.topics:
                continue
            if lossy and client.ctrl:
                continue
            if lossy:
                client.pending[frame.key()] = frame
            else:
                self._send(client, frame)

    def _flush_pending(self) -> None:
        for client in self._clients:
            if not client.pending:
                continue
            if client.ws.bytesToWrite() > BACKPRESSURE_BYTES:
                self._dropped_frames += len(client.pending)
                client.pending.clear()
                continue
            for frame in client.pending.values():
                self._send(client, frame)
            client.pending.clear()

    def _send(self, client: _Client, frame: Frame) -> None:
        try:
            raw = frame.to_bytes()
        except _PACK_ERRORS as exc:
            logger.error("帧编码失败 topic=%s: %s", frame.topic, exc)
            return
        if frame.kind == "event" and client.ws.bytesToWrite() > RELIABLE_WARN_BYTES:
            logger.warning("无损通道积压 %d 字节，客户端严重滞后", client.ws.bytesToWrite())
        client.ws.sendBinaryMessage(raw)
        self._sent_frames += 1

    # ── 连接管理 ─────────────────────────────────────

    @Slot()
    def _on_new_connection(self) -> None:
        server = self._server
        if server is None:
            return
        ws = server.nextPendingConnection()
        if ws is None:
            return

        url = ws.requestUrl()
        path = url.path() if url.isValid() else ""
        params = parse_qs(url.query() or "")
        client = _Client(ws, ctrl=path == CTRL_PATH, parent=self)

        if not self._authorize(ws, params):
            logger.warning("拒绝连接 %s：token 不匹配或非回环来源", path or "<no path>")
            ws.close()
            return
        if path not in (STREAM_PATH, CTRL_PATH):
            ws.close()
            return

        self._clients.append(client)
        ws.binaryMessageReceived.connect(lambda data: self._on_client_message(client, data))
        ws.disconnected.connect(lambda: self._drop_client(client))
        self._send_raw(client, self._hello(client))
        for provider in self._catchups:
            try:
                for raw in provider(client):
                    self._send_raw(client, raw)
            except Exception as exc:
                logger.warning("补历史失败: %s", exc, exc_info=True)
        logger.info("前端接入 %s (%s)", path, "ctrl" if client.ctrl else "stream")

    def _authorize(self, ws: QWebSocket, params: dict[str, list[str]]) -> bool:
        peer = ws.peerAddress()
        if peer is None or not peer.toString().startswith(("127.", "::1")):
            return False
        supplied = (params.get("token") or [""])[0]
        return self._token != "" and secrets.compare_digest(supplied, self._token)

    def _hello(self, client: _Client) -> bytes:
        return msgpack.packb(
            {
                "v": PROTOCOL_VERSION,
                "k": "hello",
                "mode": "ctrl" if client.ctrl else "stream",
                "topics": {name: q.value for name, q in TOPICS.items()},
                "rpc": sorted(self._rpc),
            },
            use_bin_type=True,
        )

    def _drop_client(self, client: _Client) -> None:
        if client in self._clients:
            self._clients.remove(client)
        client.deleteLater()

    def _on_client_message(self, client: _Client, data: Any) -> None:
        raw = bytes(data)
        if len(raw) > MAX_MESSAGE_BYTES:
            self._send_raw(client, encode_error("报文超出长度上限"))
            return
        try:
            body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        except _UNPACK_ERRORS as exc:
            self._send_raw(client, encode_error(f"报文解析失败: {exc}"))
            return
        if not isinstance(body, dict):
            self._send_raw(client, encode_error("报文必须是映射"))
            return

        if body.get("k") == "sub":
            client.topics = {t for t in (body.get("topics") or []) if t in TOPICS}
            return
        self._handle_call(client, body)

    def _handle_call(self, client: _Client, body: dict[str, Any]) -> None:
        try:
            request = RpcRequest.from_body(body)
        except (KeyError, TypeError):
            self._send_raw(client, encode_error("RPC 报文缺少 method 字段"))
            return

        entry = self._rpc.get(request.method)
        if entry is None:
            self._send_raw(client, encode_result(request.id, False, error=f"未知方法: {request.method}"))
            return
        handler, background = entry

        if background:
            self._pool.start(_RpcJob(client, request.id, handler, request.params, self._results))
            return

        try:
            data = handler(request.params)
        except Exception as exc:
            # 前端传入非法参数与内部故障都要回传，不能让事件循环里的槽崩掉
            logger.warning("RPC %s 执行失败: %s", request.method, exc, exc_info=True)
            self._send_raw(client, encode_result(request.id, False, error=str(exc)))
            return
        try:
            raw = encode_result(request.id, True, data=data)
        except _PACK_ERRORS as exc:
            logger.error("RPC %s 返回值无法序列化: %s", request.method, exc)
            raw = encode_result(request.id, False, error=f"序列化失败: {exc}")
        self._send_raw(client, raw)

    @staticmethod
    def _send_raw(client: _Client, raw: bytes) -> None:
        client.ws.sendBinaryMessage(raw)

    # ── 内建 RPC ─────────────────────────────────────

    def _register_builtin_rpc(self) -> None:
        self.register_rpc("system.ping", lambda _p: {"pong": True, "version": PROTOCOL_VERSION})
        self.register_rpc("system.topics", lambda _p: {n: q.value for n, q in TOPICS.items()})
        self.register_rpc(
            "system.diagnostics",
            lambda _p: self.diagnostics(),
        )
