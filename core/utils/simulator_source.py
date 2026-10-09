# -*- coding: utf-8 -*-
"""
模拟器数据源 - SimulatorSource

包装 PDSimulator / AESimulator，实现 DataSource 抽象接口。
可由 DataSourceManager 统一管理，支持运行时切换局放类型。

模拟器实例之间完全独立（独立线程、独立设备 ID），
支持同时运行 UHF 模拟器 + AE 模拟器。
"""

from __future__ import annotations

import logging
from typing import Any

from core.foundation.data_source import DataSource, DataSourceStatus, DataSourceType

logger = logging.getLogger(__name__)


class SimulatorSource(DataSource):
    """模拟器数据源 - 包装 PDSimulator / AESimulator"""

    def __init__(
        self,
        source_id: str,
        sim_type: str,
        pd_type: str,
        fps: int,
        acquisition,
        data_bus,
        pulse_rate: float | None = None,
    ):
        super().__init__()
        self._source_id = source_id
        self._sim_type = sim_type
        self._pd_type = pd_type
        self._fps = fps
        # 每帧 4096 点 @100MHz 只覆盖 41μs，脉冲数按 poisson(pulse_rate*n/sr) 抽，
        # 所以 pulse_rate 是"每秒"时默认 50 会让 488 帧里才落进 1 个脉冲。
        # None = 按仿真类型取各自默认（UHF 50/s、AE 5/s）：两种仿真的真实量级差一个
        # 数量级，共用一个默认值必然有一边不对。
        self._pulse_rate = pulse_rate
        self._acquisition = acquisition
        self._data_bus = data_bus
        self._simulator: Any = None
        self._status = DataSourceStatus.STOPPED
        self._generated = 0

    # ── DataSource 属性 ──────────────────────────────

    @property
    def id(self) -> str:
        return self._source_id

    @property
    def name(self) -> str:
        return f"模拟器-{'UHF' if self._sim_type == 'uhf' else 'AE'}"

    @property
    def source_type(self) -> DataSourceType:
        return DataSourceType.SIMULATOR

    @property
    def sensor_type(self) -> str:
        return self._sim_type

    @property
    def status(self) -> DataSourceStatus:
        return self._status

    # ── 生命周期 ─────────────────────────────────────

    def _on_waveform(self, dev, ch, wf) -> None:
        """波形回调 - 转发到采集服务并计数"""
        self._generated += 1
        self._acquisition.process_data_frame(dev, ch, wf)

    def start(self) -> bool:
        """启动模拟器"""
        device_id = self._source_id
        channel_id = 1

        if self._sim_type == "ae":
            from core.utils.ae_simulator import AESimulator

            sim = AESimulator(
                sample_rate=2_000_000,
                waveform_points=4096,
                # 原先这里写死 5.0，--pulse-rate 对 AE 完全无效，ae_hit 约 5 秒才一个，
                # 前端 AE 面板没法验收。默认仍是 5.0，但显式传值现在能落下来。
                burst_rate=self._pulse_rate if self._pulse_rate is not None else 5.0,
                noise_level=0.5,
            )
            sim.set_burst_type(self._pd_type)
            sim.set_device_info(device_id, channel_id)
            self._acquisition.set_channel_coupling_type(device_id, channel_id, "ae")
        else:
            from core.utils.simulator import PDSimulator

            sim = PDSimulator(
                sample_rate=100_000_000,
                waveform_points=4096,
                # 显式传 None 会覆盖 PDSimulator 自己的默认值并把 poisson 弄崩，
                # 所以这里必须落地成数字。
                pulse_rate=self._pulse_rate if self._pulse_rate is not None else 50.0,
                noise_level=2.0,
            )
            sim.set_pd_type(self._pd_type)
            # AE 分支一直在设，UHF 分支原先漏了这行：每个 UHF 模拟源都退回
            # PDSimulator 的类默认 "SIM-001"。多源时后果有两层——ProcessorManager 以
            # (device, channel) 为键，四路源共用同一套 PRPD/FFT/RingBuffer，图谱是混着
            # 累积的；推流侧合并键 (topic, device, channel) 也跟着撞，同一 flush 窗口里
            # 后帧顶掉前帧，客户端少收的帧会被算成"背压丢弃"。实测 N=4 时内核 79.5 fps、
            # 客户端只有 50.6 fps，而 dropped=0、coalesced=706。
            sim.set_device_info(device_id, channel_id)

        sim.set_on_waveform_callback(self._on_waveform)
        sim.start(interval_ms=1000 // self._fps)
        self._simulator = sim
        self._status = DataSourceStatus.RUNNING
        self.status_changed.emit(self._source_id, "running")
        logger.info(
            "SimulatorSource 已启动: %s type=%s/%s, %d FPS",
            self._source_id,
            self._sim_type,
            self._pd_type,
            self._fps,
        )
        return True

    def stop(self) -> None:
        """停止模拟器"""
        if self._simulator:
            self._simulator.stop()
            self._simulator = None
        self._status = DataSourceStatus.STOPPED
        self.status_changed.emit(self._source_id, "stopped")
        logger.info("SimulatorSource 已停止: %s", self._source_id)

    # ── 状态查询 ─────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "id": self._source_id,
            "name": self.name,
            "type": "simulator",
            "sensor": self._sim_type,
            "pd_type": self._pd_type,
            "fps": self._fps,
            "status": self._status.value,
            "generated": self._generated,
        }

    def get_stats(self) -> dict:
        return {"generated": self._generated}

    # ── 运行时调整 ───────────────────────────────────

    def set_pd_type(self, pd_type: str) -> None:
        """运行时更改局放类型（无需重启模拟器）"""
        self._pd_type = pd_type
        if self._simulator:
            if hasattr(self._simulator, "set_pd_type"):
                self._simulator.set_pd_type(pd_type)
            elif hasattr(self._simulator, "set_burst_type"):
                self._simulator.set_burst_type(pd_type)
            logger.info("SimulatorSource 局放类型已切换: %s → %s", self._source_id, pd_type)
