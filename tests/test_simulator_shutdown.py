# -*- coding: utf-8 -*-
"""模拟器关停契约：stop() 返回时生成线程必须真的已经退出。

起因：sidecar 每次退出（以及全套 pytest 跑完时）都刷屏
`RuntimeError: Signal source has been deleted`（栈顶在
`core/foundation/signal_bus.py` 的 `publish_pd_event` / `publish_fft_result`）。
根因不在关停顺序上——`sidecar.py` 的 shutdown 里 `sources.stop_all()` 本来就排在
`acquisition.stop()` 之前——而在 `PDSimulator.stop()` / `AESimulator.stop()` **只置
`_is_running = False`，从不 join**。那个线程是 daemon，也是帧的源头：stop() 返回后
解释器进入收尾，Qt 侧的 `PDDataBusSignals`（QObject）先被销毁，而线程手上那一帧还没
跑完回调，于是往已死的信号源上 emit。

join 的写法对齐仓库既有先例：`udp_driver.py:145`、`storage_service.py:120`、
`logger.py:195`。
"""

from __future__ import annotations

import threading
import time

import pytest

from core.utils.ae_simulator import AESimulator
from core.utils.simulator import PDSimulator

CLASSES = [PDSimulator, AESimulator]
IDS = ["PDSimulator", "AESimulator"]

# 回调比帧间隔慢一个量级：不 join 的实现返回时，线程几乎必然还压在回调里。
CALLBACK_SLOW_S = 0.06
INTERVAL_MS = 10


def _running_sim(cls):
    """起一个模拟器。回调形状要贴近真实管线：先耗时，最后才 emit。

    计数**必须放在 sleep 之后**。放之前的话，不 join 的实现在 stop() 返回时那一帧
    已经计过、循环条件随即退出，emitted 不再增长——用例对正确实现和错误实现给出
    同样的结果，等于没有牙齿。放之后才能区分：不 join 时 stop() 已返回而那一帧还在
    路上，等 0.3s 它就会补上一次 emit。
    """
    sim = cls()
    state = {"emitted": 0}

    def on_waveform(device_id, channel_id, waveform):
        time.sleep(CALLBACK_SLOW_S)  # 管线耗时
        state["emitted"] += 1  # emit 到 Qt 发生在这一行

    sim.set_on_waveform_callback(on_waveform)
    sim.start(interval_ms=INTERVAL_MS)

    deadline = time.time() + 3.0
    while state["emitted"] < 1 and time.time() < deadline:
        time.sleep(0.01)
    assert state["emitted"] >= 1, "模拟器一帧都没产出，用例前提不成立"
    return sim, state


class TestStopJoinsProducer:
    @pytest.mark.parametrize("cls", CLASSES, ids=IDS)
    def test_thread_not_alive_after_stop_returns(self, cls):
        sim, _state = _running_sim(cls)
        thread = sim._thread
        assert isinstance(thread, threading.Thread) and thread.is_alive()

        sim.stop()

        assert not thread.is_alive(), (
            "stop() 返回时生成线程还活着：它是 daemon，会带着未跑完的那帧进入解释器"
            "收尾，然后往已销毁的 QObject 上 emit"
        )

    @pytest.mark.parametrize("cls", CLASSES, ids=IDS)
    def test_no_frame_emits_after_stop_returns(self, cls):
        """契约的行为面：stop() 返回之后再也不该有帧被 emit 出去。"""
        sim, state = _running_sim(cls)

        sim.stop()
        at_return = state["emitted"]

        # 给任何还没退出的线程跑完手上那帧的时间（回调 60ms + 帧间隔 10ms，取 5 倍）
        time.sleep(0.3)
        assert (
            state["emitted"] == at_return
        ), "stop() 返回后 emitted 从 %d 又涨到 %d：那一帧是在 stop() 之后才 emit 的，" "正是退出刷屏的成因" % (
            at_return,
            state["emitted"],
        )

    @pytest.mark.parametrize("cls", CLASSES, ids=IDS)
    def test_stop_is_idempotent_and_safe_before_start(self, cls):
        """shutdown 与 RPC `acquisition.stop` 都可能重复调 stop()；没 start 过也不能炸。"""
        sim = cls()
        sim.stop()
        sim.start(interval_ms=INTERVAL_MS)
        sim.stop()
        sim.stop()
        assert sim._thread is None
        assert sim._is_running is False

    @pytest.mark.parametrize("cls", CLASSES, ids=IDS)
    def test_stop_from_producer_thread_does_not_self_join(self, cls):
        """在生成线程里调 stop() 不能 join 自己——那会直接 RuntimeError。"""
        sim = cls()
        seen = {}

        def on_waveform(device_id, channel_id, waveform):
            if threading.current_thread() is sim._thread:
                sim.stop()
                seen["ok"] = True

        sim.set_on_waveform_callback(on_waveform)
        sim.start(interval_ms=INTERVAL_MS)

        deadline = time.time() + 3.0
        while not seen.get("ok") and time.time() < deadline:
            time.sleep(0.01)
        try:
            assert seen.get("ok"), "回调没能在生成线程里执行到 stop()，自 join 分支没被跑到"
            assert not sim._is_running
        finally:
            sim.stop()
