# -*- coding: utf-8 -*-
"""
AE 数据模拟器 - AESimulator

生成模拟的超声波声发射突发波形数据，用于系统测试和演示。
无需硬件即可验证 AE 处理管线和 UI。

模拟数据特性:
- 阻尼正弦振荡脉冲 (50-300kHz)
- 机械振动噪声 + 白噪声
- 突发类型: burst / continuous / mixed
- 工频 50Hz 相位关联 (PRPD 显示用)
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from typing import Callable, List, Optional

import numpy as np

from core.communication.fpga_protocol import AEWaveformData
from core.processing import PeakDetector

logger = logging.getLogger(__name__)


class AESimulator:
    """
    AE 数据模拟器

    模拟 AE 传感器生成测试数据。

    Args:
        sample_rate: 采样率 (Hz), 默认 2MHz
        waveform_points: 波形点数, 默认 4096
        burst_rate: 每秒突发数, 默认 5.0
        noise_level: 噪声水平 (mV), 默认 0.5
    """

    # AE 突发类型
    BURST_TYPES = {
        "burst": {
            "amplitude_range": (10, 50),
            "frequency_range": (80_000, 200_000),
            "damping_range": (0.1, 0.3),
            "description": "间歇性突发",
        },
        "continuous": {
            "amplitude_range": (2, 10),
            "frequency_range": (50_000, 150_000),
            "damping_range": (0.01, 0.05),
            "description": "连续型信号",
        },
        "mixed": {
            "amplitude_range": (5, 40),
            "frequency_range": (50_000, 250_000),
            "damping_range": (0.05, 0.2),
            "description": "混合型信号",
        },
    }

    def __init__(
        self,
        sample_rate: int = 2_000_000,
        waveform_points: int = 4096,
        burst_rate: float = 5.0,
        noise_level: float = 0.5,
    ):
        self._sample_rate = sample_rate
        self._waveform_points = waveform_points
        self._burst_rate = burst_rate
        self._noise_level = noise_level
        self._device_id = "AE-SIM-001"
        self._channel_id = 1
        self._phase_offset = random.uniform(0, 360)

        # 默认突发类型
        self._burst_type = "burst"
        self._amplitude_scale = 1.0

        # 回调
        self._on_waveform: Optional[Callable] = None
        self._on_pd_event: Optional[Callable] = None

        # 运行状态
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_count = 0
        self._power_line_freq = 50.0  # 工频 50Hz
        self._time = 0.0  # 模拟时间

    # ── 配置 ─────────────────────────────────────────

    def set_burst_type(self, burst_type: str) -> None:
        """设置 AE 突发类型: burst, continuous, mixed"""
        if burst_type in self.BURST_TYPES:
            self._burst_type = burst_type
            logger.info("AE 模拟器类型: %s (%s)", burst_type, self.BURST_TYPES[burst_type]["description"])

    def set_burst_rate(self, rate: float) -> None:
        """设置每秒突发数"""
        self._burst_rate = max(0, rate)

    def set_noise_level(self, level: float) -> None:
        """设置噪声水平 (mV)"""
        self._noise_level = max(0.1, level)

    def set_amplitude_scale(self, scale: float) -> None:
        """设置幅值缩放系数 0.1~2.0"""
        self._amplitude_scale = max(0.1, min(2.0, scale))

    def set_device_info(self, device_id: str, channel_id: int) -> None:
        """设置设备信息"""
        self._device_id = device_id
        self._channel_id = channel_id

    # ── 波形生成 ─────────────────────────────────────

    def generate_waveform(self) -> AEWaveformData:
        """
        生成一帧模拟 AE 波形数据

        Returns:
            AEWaveformData 包含模拟的 AE 突发波形
        """
        n = self._waveform_points
        dt = 1.0 / self._sample_rate
        t = np.arange(n) * dt
        self._time += n * dt

        # 基线: 机械振动噪声 (0.5-5kHz) + 白噪声
        # 机械振动: 低频正弦 + 随机相位调制
        vib_freq = random.uniform(0.5, 5) * 1000  # 0.5-5 kHz
        mechanical_noise = np.sin(2 * np.pi * vib_freq * t + random.uniform(0, 2 * math.pi)) * self._noise_level * 0.5
        white_noise = np.random.randn(n) * self._noise_level
        waveform = mechanical_noise + white_noise

        # AE 突发脉冲
        config = self.BURST_TYPES[self._burst_type]
        amp_range = config["amplitude_range"]
        freq_range = config["frequency_range"]
        damping_range = config["damping_range"]

        # 每帧突发数 (泊松分布)
        n_bursts = np.random.poisson(self._burst_rate * n / self._sample_rate)
        n_bursts = min(n_bursts, 10)

        for _ in range(n_bursts):
            # 相位 -> 时间位置
            phase_deg = random.uniform(0, 360)
            phase_rad = math.radians(phase_deg)
            pos = int((phase_rad / (2 * math.pi)) * n + random.gauss(0, n * 0.02)) % n

            # 突发参数
            amplitude = random.uniform(amp_range[0], amp_range[1]) * self._amplitude_scale
            frequency = random.uniform(freq_range[0], freq_range[1])
            damping = random.uniform(damping_range[0], damping_range[1])

            # 阻尼正弦振荡: A * exp(-damping * t) * sin(2*pi*f*t)
            burst_duration = int(min(n / 4, self._sample_rate / frequency * 20))  # ~20 cycles
            for i in range(burst_duration):
                idx = (pos + i) % n
                env = math.exp(-damping * i / (self._sample_rate / frequency))
                oscillation = math.sin(2 * math.pi * frequency * i / self._sample_rate)
                waveform[idx] += amplitude * env * oscillation

        # 触发位置 (最大峰值)
        trigger_pos = int(np.argmax(np.abs(waveform)))

        self._frame_count += 1
        self._phase_offset = (self._phase_offset + 360 * n / self._sample_rate * self._power_line_freq) % 360

        return AEWaveformData(
            samples=waveform.tolist(),
            sample_rate=self._sample_rate,
            trigger_position=trigger_pos,
        )

    def generate_waveform_batch(self, n_frames: int = 10) -> List[AEWaveformData]:
        """批量生成波形"""
        return [self.generate_waveform() for _ in range(n_frames)]

    # ── 连续运行 ─────────────────────────────────────

    def start(self, interval_ms: int = 50) -> None:
        """
        启动模拟数据生成 (每 interval_ms 生成一帧)

        Args:
            interval_ms: 帧间隔 (ms)
        """
        self._is_running = True
        self._thread = threading.Thread(target=self._run_loop, args=(interval_ms,), daemon=True)
        self._thread.start()
        logger.info(
            "AE 模拟器已启动 [%s, %d FPS]",
            self._burst_type,
            1000 // max(interval_ms, 1),
        )

    def stop(self) -> None:
        """停止模拟，并等生成线程真的退出。

        与 PDSimulator 同一问题：daemon 生成线程只置标志位不 join，退出时它还在
        回调 AE 管线 → `PDDataBusSignals.emit()`，而 QObject 已被销毁。
        """
        self._is_running = False
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
            if thread.is_alive():
                logger.warning("AE 模拟器生成线程 2.0s 内未退出，继续关停流程")
        self._thread = None
        logger.info("AE 模拟器已停止 (帧数: %d)", self._frame_count)

    def _run_loop(self, interval_ms: int) -> None:
        """后台运行循环"""
        while self._is_running:
            start = time.time()
            try:
                wf = self.generate_waveform()

                # 回调通知
                if self._on_waveform:
                    self._on_waveform(self._device_id, self._channel_id, wf)
                if self._on_pd_event:
                    # 提取峰值作为 PD 事件 (用于 PRPD)
                    detector = PeakDetector(adaptive=True)
                    peaks = detector.detect_peaks_simple(np.array(wf.samples))
                    for p in peaks[:5]:
                        phase = (p.position / len(wf.samples) * 360 + self._phase_offset) % 360
                        self._on_pd_event(
                            self._device_id,
                            self._channel_id,
                            {
                                "phase": phase,
                                "amplitude": abs(p.amplitude),
                                "energy": abs(p.amplitude) * 0.5,
                                "polarity": 0 if p.amplitude > 0 else 1,
                                "frequency_mhz": random.uniform(0.02, 0.2),
                                "signal_quality": random.randint(60, 100),
                            },
                        )
            except Exception as e:
                logger.debug("AE 模拟器生成异常: %s", e)

            # 维持帧率
            elapsed = (time.time() - start) * 1000
            sleep_ms = max(1, interval_ms - elapsed)
            time.sleep(sleep_ms / 1000)

    # ── 回调 ─────────────────────────────────────────

    def set_on_waveform_callback(self, cb: Optional[Callable]) -> None:
        """设置波形回调"""
        self._on_waveform = cb

    def set_on_pd_event_callback(self, cb: Optional[Callable]) -> None:
        """设置 PD 事件回调"""
        self._on_pd_event = cb

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def is_running(self) -> bool:
        return self._is_running
