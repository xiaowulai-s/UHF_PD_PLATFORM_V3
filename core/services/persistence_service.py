# -*- coding: utf-8 -*-
"""
持久化订阅器 — 把总线上的局放事件落到 SQLite

职责原先在 `ui/controllers/signal_router.py:116`。搬到内核是因为 sidecar 架构下
没人调用 `PDStorageService.store_pd_event`：`--with-storage` 只起了刷写线程，
队列里永远没有数据，`storage.query_events` / 趋势查询恒空。旧 `ui/` 一删，
这条通路就彻底消失。

不在此处写趋势表：旧的 `store_trend_data` 调用（signal_router:124）存的不是分箱
统计，而是累计计数与编造的常量关系（avg = max*0.6、energy = count*max*0.1、
noise = 0）。趋势应由 `pd_events` 按箱即时聚合得出，见 storage_rpc 的
`storage.query_trend_series`。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.foundation.data_bus import SubscriptionManager

logger = logging.getLogger(__name__)


class PersistenceSubscriber:
    """订阅 PDDataBus 的局放事件并交给存储服务的批量队列。"""

    def __init__(self, bus: Any, storage: Any) -> None:
        self._bus = bus
        self._storage = storage
        self._subs = SubscriptionManager()
        self._accepted = 0
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._subs.register(self._bus.pd_event_detected, self._on_pd_event)
        self._started = True
        logger.info("持久化订阅已启动")

    def stop(self) -> None:
        if not self._started:
            return
        self._subs.release_all()
        self._started = False

    @property
    def stats(self) -> Dict[str, int]:
        """已入队条数（落库是批量异步的，这个数只说明"进队列了"）。"""
        return {"accepted": self._accepted}

    def _on_pd_event(self, device_id: str, channel_id: int, event: dict) -> None:
        self._storage.store_pd_event(device_id, channel_id, event)
        self._accepted += 1
