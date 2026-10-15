# -*- coding: utf-8 -*-
"""
PRPD/PRPS 发布节流 — PublishThrottle

控制 PRPD 矩阵衰减、动态幅值扩展、以及 PRPD/PRPS 向 DataBus 的发布频率。
UHF 和 AE 管线共用。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np

from core.foundation.signal_bus import PDDataBus
from core.processing.prpd_processor import PRPDMode

logger = logging.getLogger(__name__)


class PublishThrottle:
    """
    PRPD/PRPS 发布节流器

    - PRPD: 每 PRPD_DECAY_INTERVAL 帧衰减一次，每 PRPD_PUBLISH_INTERVAL 帧发布一次
    - PRPS: 每 _prps_publish_interval 帧发布一次
    - 动态幅值扩展: 当观测到的峰值超过 PRPD 矩阵范围 80% 时自动扩展
    """

    PRPD_DECAY_FACTOR = 0.995
    PRPD_DECAY_INTERVAL = 20
    PRPD_PUBLISH_INTERVAL = 50

    def __init__(self, data_bus: PDDataBus):
        self._data_bus = data_bus
        self._prpd_frame_count: Dict[str, int] = {}
        self._prpd_decay_count: Dict[str, int] = {}
        self._prps_frame_count: Dict[str, int] = {}
        self._observed_max_amp: Dict[str, float] = {}
        self._prps_publish_interval = 10
        # 密度图与热力图用的是同一份计数矩阵，区别只在 compute 时是否做高斯平滑，
        # 所以这个开关必须落在内核侧——前端自己切只会拿到同样的字节。
        self._prpd_mode: PRPDMode = PRPDMode.HEATMAP

    def set_prpd_mode(self, mode: str) -> str:
        """切换 PRPD 发布模式；返回实际生效的模式，便于调用方核对。"""
        try:
            self._prpd_mode = PRPDMode(mode)
        except ValueError:
            logger.warning("未知的 PRPD 模式 %r，保持 %s", mode, self._prpd_mode.value)
        return self._prpd_mode.value

    @property
    def prpd_mode(self) -> str:
        return self._prpd_mode.value

    def prpd_cycle(
        self,
        device_id: str,
        channel_id: int,
        key: str,
        prpd_proc: Any,
        max_amplitude_val: float,
    ) -> None:
        """PRPD 衰减 + 动态幅值扩展 + 发布节流"""
        # 动态幅值扩展
        prev_max = self._observed_max_amp.get(key, 0.0)
        if max_amplitude_val > prev_max:
            self._observed_max_amp[key] = max_amplitude_val
            if max_amplitude_val > prpd_proc.max_amplitude * 0.8:
                prpd_proc.max_amplitude = max_amplitude_val * 1.2
                prpd_proc.rebuild_matrix()

        # 每 N 帧衰减
        if key not in self._prpd_decay_count:
            self._prpd_decay_count[key] = 0
        self._prpd_decay_count[key] += 1
        if self._prpd_decay_count[key] >= self.PRPD_DECAY_INTERVAL:
            self._prpd_decay_count[key] = 0
            prpd_proc.decay(self.PRPD_DECAY_FACTOR)

        # 每 N 帧发布
        if key not in self._prpd_frame_count:
            self._prpd_frame_count[key] = 0
        self._prpd_frame_count[key] += 1
        if self._prpd_frame_count[key] >= self.PRPD_PUBLISH_INTERVAL:
            self._prpd_frame_count[key] = 0
            self._data_bus.publish_prpd_result(device_id, channel_id, prpd_proc.compute(mode=self._prpd_mode))

    def prps_cycle(
        self,
        device_id: str,
        channel_id: int,
        key: str,
        prps_proc: Any,
        phase_binned: np.ndarray,
    ) -> None:
        """PRPS 节流发布"""
        prps_proc.add_cycle(phase_binned)
        if key not in self._prps_frame_count:
            self._prps_frame_count[key] = 0
        self._prps_frame_count[key] += 1
        if self._prps_frame_count[key] >= self._prps_publish_interval:
            self._prps_frame_count[key] = 0
            self._data_bus.publish_prps_result(device_id, channel_id, prps_proc.compute())

    def get_observed_max_amp(self, key: str) -> float:
        return self._observed_max_amp.get(key, 0.0)

    def clear(self) -> None:
        self._prpd_frame_count.clear()
        self._prpd_decay_count.clear()
        self._prps_frame_count.clear()
        self._observed_max_amp.clear()
