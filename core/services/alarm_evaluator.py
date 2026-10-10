"""幅值告警判定订阅器：把总线上的 PD 事件接给 AlarmService 判阈值。

为什么要有这个模块：`PDAlarmService.check_amplitude()` 是唯一的幅值告警入口，但它在
生产路径上**没有任何调用点**——旧架构里做这件事的是
`ui/controllers/signal_router.py::_on_pd_event_for_alarm`。M8 把 `ui/` 下线时这条线没人
接管，于是 `check_amplitude` 只剩测试在直接调，全套测试仍然全绿，而 sidecar 里 UHF 幅值
告警一条都不会产生（实测：`--pd-type corona --pulse-rate 60000`、全局阈值压到 1 mV，
25 秒内 0 条告警帧）。

**不要照搬旧代码的两次调用**：`check_amplitude` 在去抖满足时是"锁内构造 + 自己调
trigger_alarm"（`alarm_service.py:172` 起），返回的已经是发布过的告警。旧 signal_router
紧接着又调一次 `trigger_alarm`，只是被 `trigger_alarm` 里的同级别去抖兜住了（相同
alarm_type + level 只更新幅值与计数、不再发布），并没有产生第二条告警——那纯属巧合，
不是可以照抄的语义。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.foundation.data_bus import SubscriptionManager

logger = logging.getLogger(__name__)


class AlarmEvaluator:
    """订阅 `pd_event_detected`，逐条做幅值阈值判定。

    Args:
        bus: PDDataBus 实例（事件来源）
        alarm: PDAlarmService；为 None 时不订阅（`--hardware` 模式当前不构造告警服务）
    """

    def __init__(self, bus: Any, alarm: Any) -> None:
        self._bus = bus
        self._alarm = alarm
        self._subs = SubscriptionManager()
        self._evaluated = 0
        self._over_threshold = 0
        self._started = False

    def start(self) -> None:
        """接上总线。重复调用不会二次订阅。"""
        if self._started:
            return
        if self._alarm is None:
            logger.warning("未提供 AlarmService，幅值告警判定不会运行")
            return
        self._subs.register(self._bus.pd_event_detected, self._on_pd_event)
        self._started = True
        logger.info("幅值告警判定已接入总线")

    def stop(self) -> None:
        """断开订阅。必须在 `storage.stop()` / `db_manager.close()` 之前调用——
        告警是检出线程里直写 DB 的，撞上已关闭的引擎会抛异常。
        """
        if not self._started:
            return
        self._subs.release_all()
        self._started = False

    @property
    def stats(self) -> Dict[str, int]:
        """`evaluated`=送进阈值判定的事件数。

        `over_threshold`=越过阈值且过了去抖的事件数，**不等于新增告警条数**：
        同一 (device, type) 在同一级别下只落一条库行，后续同级别越限事件被
        `trigger_alarm` 去抖（只更新幅值与计数）。所以持续超阈时这个数会一直涨、
        而库里只有一条——用它判断"链路有没有接上"，不要当告警数量读。

        两个数分开报的意义：`evaluated=0` 说明判定链没接上（就是 ui/ 归档后那段
        时间的状态），而 `evaluated>0, over_threshold=0` 才是"真的没超阈值"。
        """
        return {"evaluated": self._evaluated, "over_threshold": self._over_threshold}

    def _on_pd_event(self, device_id: str, channel_id: int, event: dict) -> None:
        amplitude = event.get("amplitude")
        if amplitude is None:
            return
        self._evaluated += 1
        # check_amplitude 已经自己完成"去抖 + 落库 + 发布"，这里绝不能再调 trigger_alarm
        if self._alarm.check_amplitude(device_id, channel_id, amplitude) is not None:
            self._over_threshold += 1
