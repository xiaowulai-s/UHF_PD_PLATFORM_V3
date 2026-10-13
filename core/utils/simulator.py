# -*- coding: utf-8 -*-
"""
PD 数据模拟器 - PDSimulator

生成模拟的局放波形和事件数据，用于系统测试和演示。
无需 FPGA 硬件即可验证 UI 和数据处理管线。

模拟数据特性:
- 真实局放脉冲波形 (指数衰减振荡)
- 可控信噪比
- 周期工频相位 (50Hz)
- PRPD 模式 (相位集中)
- 可配置幅值和脉冲率
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from typing import Callable, List, Optional

import numpy as np

from core.communication import WaveformData
from core.processing import PeakDetector, PRPDProcessor

logger = logging.getLogger(__name__)


class PDSimulator:
    """
    PD 数据模拟器

    模拟 FPGA 采集主机生成测试数据。

    Args:
        sample_rate: 采样率 (Hz)
        waveform_points: 波形点数
        pulse_rate: 每秒脉冲数
        noise_level: 噪声水平 (mV)
    """

    #: 局放脉冲类型。
    #:
    #: **相位分布是双侧的**，不是单个窄窗。原先每种类型只给一个 `phase_range`
    #: （corona 270~330、surface 30~90、internal 0~60），于是四种类型在
    #: `DischargeClassifier` 眼里长得一模一样：top2≈0.99、cv≈0.3，实测四种 `--pd-type`
    #: **全部**被判成"沿面放电"。参考剖面描述的是"内部气隙：正负半周都集中、S1>S4"、
    #: "电晕：负半周能量占绝对优势"、"沿面：S1≈S4"、"悬浮：相位宽、cv 极高"——
    #: 单一窄窗一个都对不上。
    #:
    #: 窗口按 **60° 扇区**切（S1=0~60° … S6=300~360°），权重是该扇区的计数占比，
    #: 取值按参考剖面的 quad13 / spread / neg_tail / s1_s4_ratio 反推：
    #:   internal  S1 .45 / S4 .30 → quad13 .75、S1/S4=1.5、symmetry .67
    #:   surface   S1 .44 / S4 .44 → quad13 .88、S1/S4=1.0
    #:   corona    六扇区近均分     → quad13 .32、spread .68、neg_tail .33
    #:   floating  S1 .19 / S4 .19 → quad13 .38、spread .62
    #: `amp_scale` 是窗口内的幅值倍率，用来把 **pos_energy**（幅值加权的正半周占比）
    #: 压到参考值：电晕 3.1（负半周能量占绝对优势）、内部 1.27、其余 1.0。
    #:
    #: 幅值用**对数正态**而不是均匀分布：参考剖面的 cv 目标是 0.50~2.50，而均匀分布的
    #: cv 上限只有 1/√3≈0.58，internal(0.80)/corona(1.50)/floating(2.50) 都够不到。
    #: `amplitude_sigma` 由 cv = sqrt(exp(σ²)-1) 反解；`amplitude_cap` 压住对数正态的
    #: 右尾，避免把 PRPD 幅值轴撑大——那根轴**只增不减**，撑大了就再也回不来。
    PD_TYPES = {
        "corona": {
            "description": "电晕放电",
            "phase_windows": (
                (0, 60, 0.160, 1.0),
                (60, 120, 0.175, 1.0),
                (120, 180, 0.175, 1.0),
                (180, 240, 0.160, 3.1),
                (240, 300, 0.165, 3.1),
                (300, 360, 0.165, 3.1),
            ),
            "amplitude_median": 9.0,
            "amplitude_sigma": 1.09,
            "amplitude_cap": 120.0,
        },
        "surface": {
            "description": "沿面放电",
            "phase_windows": (
                (0, 60, 0.44, 1.0),
                (60, 120, 0.03, 1.0),
                (120, 180, 0.03, 1.0),
                (180, 240, 0.44, 1.0),
                (240, 300, 0.03, 1.0),
                (300, 360, 0.03, 1.0),
            ),
            "amplitude_median": 25.0,
            "amplitude_sigma": 0.47,
            "amplitude_cap": 120.0,
        },
        "internal": {
            "description": "内部放电",
            "phase_windows": (
                (0, 60, 0.45, 1.0),
                (60, 120, 0.055, 1.0),
                (120, 180, 0.055, 1.0),
                (180, 240, 0.30, 1.27),
                (240, 300, 0.07, 1.27),
                (300, 360, 0.07, 1.27),
            ),
            "amplitude_median": 30.0,
            "amplitude_sigma": 0.70,
            "amplitude_cap": 120.0,
        },
        "floating": {
            "description": "悬浮放电",
            "phase_windows": (
                (0, 60, 0.19, 1.0),
                (60, 120, 0.155, 1.0),
                (120, 180, 0.155, 1.0),
                (180, 240, 0.19, 1.0),
                (240, 300, 0.155, 1.0),
                (300, 360, 0.155, 1.0),
            ),
            "amplitude_median": 20.0,
            # cv 目标 2.5 会让对数正态的右尾过长，而 cap 一压，cv 反而掉下来。
            # 扫过 6 组 (median, σ, cap)：σ=1.25 + cap=400 是唯一既逃出分类器的
            # 噪声闸（要求 max_amp ≥ 噪声阈值×5 ≈ 195）、cv 又最高的组合（实测 cv≈1.5）。
            # cap 400 会让 PRPD 幅值轴扩到约 480——这与剖面"幅值大且分散"一致，
            # 且那根轴本来只增不减，重启才回落。
            "amplitude_sigma": 1.25,
            "amplitude_cap": 400.0,
        },
    }

    @staticmethod
    def _sample_amplitude(config: dict) -> float:
        """按类型抽一个脉冲幅值。对数正态，cv 由 `amplitude_sigma` 决定。"""
        amp = random.lognormvariate(math.log(config["amplitude_median"]), config["amplitude_sigma"])
        return min(amp, config["amplitude_cap"])

    def __init__(
        self,
        sample_rate: int = 100_000_000,
        waveform_points: int = 4096,
        pulse_rate: float = 50.0,
        noise_level: float = 2.0,
    ):
        self._sample_rate = sample_rate
        self._waveform_points = waveform_points
        self._pulse_rate = pulse_rate
        self._noise_level = noise_level
        self._device_id = "SIM-001"
        self._channel_id = 1
        self._phase_offset = random.uniform(0, 360)

        # 默认 PD 类型
        self._pd_type = "internal"
        self._amplitude_scale = 1.0

        # PRPD 处理器 (用于生成一致的相位分布)
        self._prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100)

        # 回调
        self._on_waveform: Optional[Callable] = None
        self._on_pd_event: Optional[Callable] = None

        # 运行状态
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_count = 0
        self._power_line_freq = 50.0  # 工频 50Hz

    # ── 配置 ─────────────────────────────────────────

    def set_pd_type(self, pd_type: str) -> None:
        """设置局放类型: corona, surface, internal, floating"""
        if pd_type in self.PD_TYPES:
            self._pd_type = pd_type
            logger.info("模拟器 PD 类型: %s (%s)", pd_type, self.PD_TYPES[pd_type]["description"])

    def set_pulse_rate(self, rate: float) -> None:
        """设置每秒脉冲数"""
        self._pulse_rate = max(0, rate)

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

    def generate_waveform(self) -> WaveformData:
        """
        生成一帧模拟波形数据

        Returns:
            WaveformData 包含模拟的局放波形
        """
        n = self._waveform_points
        dt = 1.0 / self._sample_rate
        t = np.arange(n) * dt

        # 基线: 50Hz 工频 + 噪声
        # 拆成两行而不是把 + 放在行首/行尾：本仓库 .flake8 的 ignore 覆盖了默认忽略表，
        # W503 与 W504 同时生效，跨行的二元运算符无论放哪一侧都会被报。
        baseline = np.sin(2 * np.pi * self._power_line_freq * t + self._phase_offset) * 0.5
        waveform = baseline + np.random.randn(n) * self._noise_level

        # 局放脉冲
        pd_config = self.PD_TYPES[self._pd_type]
        windows = pd_config["phase_windows"]
        window_weights = [w[2] for w in windows]

        # 每帧脉冲数 (泊松分布)
        n_pulses = np.random.poisson(self._pulse_rate * n / self._sample_rate)
        n_pulses = min(n_pulses, 20)

        for _ in range(n_pulses):
            # 先按扇区权重挑窗口，再在窗口内均匀取相位：直接对全相位均匀取样
            # 得不到"两侧都有计数、但某一侧更密"的分布（见 PD_TYPES 的说明）。
            start, end, _weight, amp_scale = random.choices(windows, weights=window_weights, k=1)[0]
            phase_deg = random.uniform(start, end)
            phase_rad = math.radians(phase_deg)
            pos = int((phase_rad / (2 * math.pi)) * n + random.gauss(0, n * 0.01)) % n

            # 幅值
            amplitude = self._sample_amplitude(pd_config) * amp_scale * self._amplitude_scale

            # 脉冲宽度
            pulse_width = int(random.uniform(5, 20))

            # 指数衰减振荡脉冲
            for i in range(-pulse_width, pulse_width):
                idx = (pos + i) % n
                if idx < 0:
                    idx += n
                envelope = math.exp(-abs(i) / (pulse_width * 0.3))
                # 用 cos 而不是 sin：sin(0)=0 让脉冲中心恰好过零，实测峰值只有标称
                # 幅值的 0.30~0.68（随 pulse_width 变），于是"注入 100 mV、检出 35 mV"
                # 的差额会被误读成标定系数。cos 在 i=0 取 1，峰值即标称幅值。
                oscillation = math.cos(2 * math.pi * i / pulse_width * 2)
                waveform[idx] += amplitude * envelope * oscillation

        # 触发位置 (最大峰值)
        trigger_pos = int(np.argmax(np.abs(waveform)))

        self._frame_count += 1
        self._phase_offset = (self._phase_offset + 360 * n / self._sample_rate * self._power_line_freq) % 360

        return WaveformData(
            samples=waveform.tolist(),
            sample_rate=self._sample_rate,
            trigger_position=trigger_pos,
        )

    def generate_waveform_batch(self, n_frames: int = 10) -> List[WaveformData]:
        """批量生成波形"""
        return [self.generate_waveform() for _ in range(n_frames)]

    # ── 连续运行 ─────────────────────────────────────

    def start(self, interval_ms: int = 50) -> None:
        """
        启动模拟数据生成 (每 interval_ms 生成一帧)

        Args:
            interval_ms: 帧间隔 (ms), 50ms = 20FPS
        """
        self._is_running = True
        self._thread = threading.Thread(target=self._run_loop, args=(interval_ms,), daemon=True)
        self._thread.start()
        logger.info("PD 模拟器已启动 [%s, %d FPS]", self._pd_type, 1000 // max(interval_ms, 1))

    def stop(self) -> None:
        """停止模拟，并等生成线程真的退出。

        join 是必需的，不是保险：这个线程 daemon=True，而且它就是帧的源头。只置
        标志位就返回的话，解释器开始收尾时它还在跑 `_on_waveform` → uhf_pipeline →
        `PDDataBusSignals.emit()`，而那个 QObject 的 C++ 侧此时已被销毁，于是每次
        退出都刷屏 `RuntimeError: Signal source has been deleted`（全套 pytest 跑完
        时同样会刷）。超时上限取 2s：一帧最多 50ms 间隔 + 一次管线，2s 已经宽到
        只可能在真卡死时才触发，而关停绝不能被一个卡死的线程拖住。
        """
        self._is_running = False
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
            if thread.is_alive():
                logger.warning("PD 模拟器生成线程 2.0s 内未退出，继续关停流程")
        self._thread = None
        logger.info("PD 模拟器已停止 (帧数: %d)", self._frame_count)

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
                    # 提取峰值作为 PD 事件
                    detector = PeakDetector(adaptive=True)
                    peaks = detector.detect_peaks_simple(np.array(wf.samples))
                    for p in peaks[:10]:
                        phase = (p.position / len(wf.samples) * 360 + self._phase_offset) % 360
                        self._on_pd_event(
                            self._device_id,
                            self._channel_id,
                            {
                                "phase": phase,
                                "amplitude": abs(p.amplitude),
                                "energy": abs(p.amplitude) * 0.5,
                                "polarity": 0 if p.amplitude > 0 else 1,
                                "frequency_mhz": random.uniform(300, 1500),
                                "signal_quality": random.randint(60, 100),
                            },
                        )
            except (ValueError, KeyError, TypeError, RuntimeError) as e:
                logger.debug("模拟器生成异常: %s", e, exc_info=True)

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
