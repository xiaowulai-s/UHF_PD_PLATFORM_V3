"""放电类型判定订阅器：把 PRPD 结果接给 `DischargeClassifier`。

为什么要有这个模块：`DischargeClassifier.classify()` 此前在生产路径上只有一个调用点
——`data.import_file`（分析页导入实验数据时分类）。实时链路上没有任何地方跑它，
所以"实时事件的放电类型标签"一直是空的（`migration_coverage.md` 记作 M6 缺项）。
分类器的输入本来就是"相位 + 幅值"两组数组，而 `PRPDProcessor` 累积的正是这两样，
接在 `prpd_result_ready` 上是天然挂点，不需要为它新开一条特征通路。

**不额外节流**：PRPD 结果的发布节奏由 `PublishThrottle.PRPD_PUBLISH_INTERVAL = 50`
帧决定（单通道实测约 0.33 Hz，8 通道合计约 2.7 帧/秒），远低于无损通道的承受力。
再加一层节流只会让"标签多久更新一次"变成不可解释的数，而它本来就跟 PRPD 图谱同步。

**只读 `result.events`**：`PRPDResult.events` 是处理器事件 deque 的尾部快照（≤5000 条），
不反向持有处理器——本服务不改变 PRPD 的任何状态。

**已知边界（不要当成已验证的能力）**：`prpd_result_ready` 同时被 UHF 与 AE 两条管线
发布（`ae_pipeline.py` 也调 `publish_throttle.prpd_cycle`），而这条信号**不带 sensor_type**，
所以本服务对两者一视同仁。但 `DischargeClassifier` 的参考剖面是按 UHF PRPD 校准的，
AE 通道判出来的类型属于"口径未验证"，不能与 UHF 通道的结论同等对待。要收窄这个边界，
得先把 sensor_type 打进 PRPD 契约（会动信号元数与既有订阅者），属于单独一笔改动。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np

from core.data.discharge_classifier import DischargeClassifier
from core.foundation.data_bus import SubscriptionManager

logger = logging.getLogger(__name__)


class DischargeTypeService:
    """订阅 `prpd_result_ready`，逐帧跑分类器并发布 `pd_type_classified`。

    Args:
        bus: PDDataBus 实例
        min_events: 少于这么多事件就不判。分类器自身对 <10 条返回 unknown，
            这里提前拦一道是为了让 stats 里"跳过"和"判出 unknown"能分开计数。
    """

    MIN_EVENTS = 10

    def __init__(self, bus: Any, min_events: int = MIN_EVENTS) -> None:
        self._bus = bus
        self._min_events = int(min_events)
        self._subs = SubscriptionManager()
        self._classified = 0
        self._published = 0
        self._skipped = 0
        self._started = False

    def start(self) -> None:
        """接上总线。重复调用不会二次订阅。"""
        if self._started:
            return
        self._subs.register(self._bus.prpd_result_ready, self._on_prpd)
        self._started = True
        logger.info("放电类型判定已接入总线")

    def stop(self) -> None:
        if not self._started:
            return
        self._subs.release_all()
        self._started = False

    @property
    def stats(self) -> Dict[str, int]:
        """`classified`=真的跑过分类器的帧数；`skipped`=事件不足或分类抛异常而跳过的帧数。"""
        return {"classified": self._classified, "published": self._published, "skipped": self._skipped}

    def _on_prpd(self, device_id: str, channel_id: int, result: Any) -> None:
        events = list(getattr(result, "events", None) or [])
        if len(events) < self._min_events:
            self._skipped += 1
            return

        try:
            phases = np.array([float(e.phase) for e in events], dtype=np.float64)
            amplitudes = np.array([float(e.amplitude) for e in events], dtype=np.float64)
        except (TypeError, ValueError, AttributeError) as exc:
            logger.debug("[%s:%s] PRPD 事件无法转成数组，跳过分类: %s", device_id, channel_id, exc)
            self._skipped += 1
            return

        try:
            info = DischargeClassifier.classify(phases, amplitudes)
        except Exception as exc:  # 分类失败绝不能拖垮 PRPD 发布，这是尽力而为的一层
            logger.warning("[%s:%s] 放电类型判定失败: %s", device_id, channel_id, exc, exc_info=True)
            self._skipped += 1
            return

        self._classified += 1
        payload = {
            "type": str(info.get("type", "unknown")),
            "name": str(info.get("name", "未知")),
            "name_en": str(info.get("name_en", "Unknown")),
            "confidence": float(info.get("confidence", 0.0) or 0.0),
            "severity": str(info.get("severity", "normal")),
            "pos_ratio": float(info.get("pos_ratio", 0.5) or 0.5),
            "description": str(info.get("description", "")),
            "samples": len(events),
        }
        self._bus.publish_pd_type(device_id, channel_id, payload)
        self._published += 1
