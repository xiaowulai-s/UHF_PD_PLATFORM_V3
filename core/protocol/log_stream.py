"""把 Python logging 的记录引到 WebSocket 的 `log` topic。

旧 UI 的日志页是直接挂一个 logging.Handler 到根 logger（ui/pages/log_page.py:32-41）；
跨进程之后改成：环形缓冲保留最近若干条，新客户端接入时先补历史，之后按固定
间隔成批推送增量。

日志按批而非逐行推送，配合 `log` 的 LOSSY 等级：客户端落后时丢掉的是最旧的
一批，不会像逐行无损那样把告警通道一起拖慢。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

from PySide6.QtCore import QObject, QTimer

from .payloads import build_log

MAX_MESSAGE_CHARS = 2000
SNAPSHOT_BATCH = 200


class WsLogHandler(QObject, logging.Handler):
    """线程安全的 logging.Handler， emit 可从任意工作线程进来。"""

    def __init__(
        self,
        server: Any,
        capacity: int = 2000,
        interval_ms: int = 500,
        level: int = logging.INFO,
        parent: QObject | None = None,
    ):
        QObject.__init__(self, parent)
        logging.Handler.__init__(self, level=level)
        self._server = server
        self._ring: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._batch: list[dict[str, Any]] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._dropped = 0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.flush)
        self.owner: logging.Logger | None = None
        self.previous_level: int = logging.NOTSET

    def detach(self) -> None:
        """摘掉 handler 并把 logger 级别还原成 attach() 之前的值。"""
        self.stop()
        if self.owner is not None:
            self.owner.removeHandler(self)
            self.owner.setLevel(self.previous_level)
            self.owner = None

    # ── logging.Handler ──────────────────────────────

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # 格式化失败不应影响日志调用方
            self.handleError(record)
            return
        if len(message) > MAX_MESSAGE_CHARS:
            message = message[:MAX_MESSAGE_CHARS] + "…<截断>"

        entry = {
            "seq": self._next_seq(),
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "message": message,
        }
        with self._lock:
            overflow = len(self._ring) == self._ring.maxlen
            self._ring.append(entry)
            if overflow:
                self._dropped += 1
            self._batch.append(entry)

    # ── 推送 ─────────────────────────────────────────

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.flush()

    def flush(self) -> None:
        with self._lock:
            batch, self._batch = self._batch, []
        if batch:
            self._server.publish(build_log(batch))

    def snapshot(self, client: Any) -> list[bytes]:
        """给新接入客户端补历史。返回待发送的报文列表。"""
        if client.topics and "log" not in client.topics:
            return []
        with self._lock:
            ring = list(self._ring)
        if not ring:
            return []
        frames = []
        total = len(ring)
        for start in range(0, total, SNAPSHOT_BATCH):
            stop = min(start + SNAPSHOT_BATCH, total)
            frames.append(build_log(ring[start:stop]).to_bytes())
        return frames

    @property
    def buffered(self) -> int:
        with self._lock:
            return len(self._ring)

    @property
    def ring_dropped(self) -> int:
        return self._dropped

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq


def attach(
    server: Any,
    *,
    logger: logging.Logger | None = None,
    capacity: int = 2000,
    interval_ms: int = 500,
    level: int = logging.INFO,
) -> WsLogHandler:
    """创建 handler、挂到指定 logger（默认根 logger）、并注册补历史回调。

    只设 handler 级别是不够的：记录要先通过**调用方 logger 的 effective level**
    才会到达任何 handler。root 常为 WARNING，于是 INFO 日志会被静默丢弃，前端
    永远看不到——所以这里把目标 logger 的级别一并放宽，并交回原值供恢复。
    """
    handler = WsLogHandler(server, capacity=capacity, interval_ms=interval_ms, level=level)
    target = logger or logging.getLogger()
    handler.owner = target
    handler.previous_level = target.level
    if target.level == logging.NOTSET or target.level > level:
        target.setLevel(level)
    target.addHandler(handler)
    server.register_catchup(handler.snapshot)
    handler.start()
    return handler
