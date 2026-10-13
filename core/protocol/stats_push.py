"""定时把各服务的运行指标推成 `stats` 帧。

替代旧 UI 里 StatsManager 的 2 秒轮询（ui/controllers/stats_manager.py）。
推的是**原始数值**，不带中文标签和显示格式化——那是前端的职责。

字段只从确实能取到数据的服务聚合，不发明计数器：AE hit 数已并入
AcquisitionService 的 pd_events_detected（见 acquisition_service.py:197）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer

from core.utils.process_stats import ProcessStatsSampler

from .payloads import build_stats

logger = logging.getLogger(__name__)

ONLINE_STATES = frozenset({"running", "connected", "online"})


def collect_stats(
    acquisition: Any,
    alarm: Any = None,
    sources: Any = None,
    storage: Any = None,
    transport: Any = None,
    process: Any = None,
) -> dict[str, Any]:
    """聚合一份跨服务的指标快照。任何可选依赖为 None 时整块省略该字段。"""
    out: dict[str, Any] = dict(acquisition.get_stats()) if acquisition else {}

    start = out.get("start_time", 0.0)
    if start:
        out["acq_uptime_s"] = round(time.time() - start, 1)

    if acquisition is not None:
        observed = acquisition.get_observed_max_amp() or {}
        out["observed_max_amplitude"] = max(observed.values()) if observed else 0.0
        out["channels_observed"] = len(observed)

    if alarm is not None:
        out["alarms"] = alarm.get_stats()

    if sources is not None:
        status = sources.get_all_status() or {}
        out["sources"] = status
        out["source_count"] = len(status)
        out["online_count"] = sum(1 for entry in status.values() if entry.get("status") in ONLINE_STATES)
        out["active_source"] = sources.active_source_id

    if storage is not None:
        out["storage"] = storage.get_statistics()

    if transport is not None:
        out["transport"] = transport

    if process is not None:
        out["process"] = process

    return out


class StatsPusher(QObject):
    """按固定间隔发出 stats 帧。"""

    def __init__(
        self,
        server: Any,
        *,
        acquisition: Any = None,
        alarm: Any = None,
        sources: Any = None,
        storage: Any = None,
        interval_ms: int = 2000,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._server = server
        self._acquisition = acquisition
        self._alarm = alarm
        self._sources = sources
        self._storage = storage
        # 采样器必须跨帧复用：CPU 占用率靠两次采样的差值算，每次现建一个的话
        # 差值退化成"自建实例以来"，读数会一直偏低
        self._proc_sampler = ProcessStatsSampler()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.push_once)

    def start(self) -> None:
        self.push_once()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def push_once(self) -> None:
        try:
            snapshot = collect_stats(
                self._acquisition,
                alarm=self._alarm,
                sources=self._sources,
                storage=self._storage,
                transport=self._server.diagnostics(),
                process=self._proc_sampler.sample(),
            )
        except (AttributeError, TypeError, RuntimeError) as exc:
            logger.warning("stats 聚合失败: %s", exc, exc_info=True)
            return
        self._server.publish(build_stats("", snapshot))
