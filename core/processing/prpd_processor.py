# -*- coding: utf-8 -*-
"""
PRPD 分析处理器 - Phase Resolved Partial Discharge

基于工频同步信号 + 局放事件，生成相位分布图谱。
支持三种显示模式：
- 散点图 (Scatter): 每个放电事件一个点
- 热力图 (Heatmap): 相位-幅值二维直方图
- 密度图 (Density): 高斯核密度估计
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple

import numpy as np

from core.utils.logger import get_logger

logger = get_logger("prpd_processor")


class PRPDMode(Enum):
    """PRPD 显示模式"""

    SCATTER = "scatter"  # 散点图
    HEATMAP = "heatmap"  # 热力图
    DENSITY = "density"  # 密度图


@dataclass
class PRPDResult:
    """PRPD 分析结果"""

    phase_bins: np.ndarray  # 相位轴 (0~360°)
    amplitude_bins: np.ndarray  # 幅值轴
    matrix: np.ndarray  # 二维矩阵 (phase_bins × amplitude_bins)
    events: List[PDEvent] = field(default_factory=list)  # 原始事件
    mode: PRPDMode = PRPDMode.HEATMAP
    total_events: int = 0
    max_amplitude: float = 0.0
    min_amplitude: float = 0.0


@dataclass
class PDEvent:
    """局放事件点"""

    phase: float  # 相位 (0~360°)
    amplitude: float  # 幅值 (mV)
    polarity: int = 0  # 极性: 0=正, 1=负
    energy: float = 0.0  # 能量 (pC)
    timestamp: float = 0.0  # 时间戳
    cycle: int = 0  # 工频周期编号（从1开始，0表示未知）


class PRPDProcessor:
    """
    PRPD 图谱处理器

    将工频周期 (0~360°) 与放电幅值映射到二维矩阵，
    每个单元格存储该 (相位, 幅值) 区间内的放电次数或能量。

    Args:
        phase_bins: 相位分辨率 (默认 360, 即每度 1 个 bin)
        amplitude_bins: 幅值分辨率 (默认 256)
        max_amplitude: 最大幅值范围 (mV)
        min_amplitude: 最小幅值范围
    """

    def __init__(
        self,
        phase_bins: int = 360,
        amplitude_bins: int = 256,
        max_amplitude: float = 100.0,
        min_amplitude: float = 0.0,
        sparse_mode: bool = False,  # 稀疏模式（事件稀疏时使用）
    ):
        self._phase_bins = phase_bins
        self._amplitude_bins = amplitude_bins
        self._max_amplitude = max_amplitude
        self._min_amplitude = min_amplitude
        self._sparse_mode = sparse_mode

        if sparse_mode:
            # 稀疏模式：使用字典存储非零事件
            # key: (phase_idx, amplitude_idx), value: count
            self._sparse_matrix: Dict[Tuple[int, int], float] = {}
        else:
            # 密集模式：使用 numpy 数组
            self._matrix = np.zeros((phase_bins, amplitude_bins), dtype=np.float64)

        self._max_events = 100000  # 最大缓存事件数
        self._events: deque = deque(maxlen=self._max_events)
        self._cycle_count = 0  # 工频周期计数器

    # ── 属性 ─────────────────────────────────────────

    @property
    def phase_bins(self) -> int:
        return self._phase_bins

    @property
    def amplitude_bins(self) -> int:
        return self._amplitude_bins

    @property
    def matrix(self) -> np.ndarray:
        """获取 PRPD 矩阵（如果是稀疏模式则转换为密集矩阵）"""
        if self._sparse_mode:
            dense = np.zeros((self._phase_bins, self._amplitude_bins), dtype=np.float64)
            for (p_idx, a_idx), count in self._sparse_matrix.items():
                if 0 <= p_idx < self._phase_bins and 0 <= a_idx < self._amplitude_bins:
                    dense[p_idx, a_idx] = count
            return dense
        else:
            return self._matrix

    @property
    def max_amplitude(self) -> float:
        return self._max_amplitude

    @max_amplitude.setter
    def max_amplitude(self, value: float) -> None:
        self._max_amplitude = value

    # ── 数据更新 ─────────────────────────────────────

    def add_event(self, phase: float, amplitude: float, polarity: int = 0, energy: float = 0.0, cycle: int = 0) -> None:
        """
        添加单个局放事件

        Args:
            phase: 相位 (0~360°)
            amplitude: 幅值 (mV)
            polarity: 极性 (0=正, 1=负)
            energy: 放电能量 (pC)
            cycle: 工频周期编号（0=未知/使用当前内部计数）
        """
        actual_cycle = cycle if cycle > 0 else self._cycle_count
        event = PDEvent(
            phase=phase % 360,
            amplitude=amplitude,
            polarity=polarity,
            energy=energy,
            cycle=actual_cycle,
        )

        # deque maxlen 自动淘汰旧事件
        # 注意：不再从矩阵中减1，因为多线程/衰减场景下会产生负值误差
        # 矩阵的"过期"通过 decay() 方法的指数衰减统一处理
        self._events.append(event)

        # 更新矩阵
        p_idx = self._phase_to_index(event.phase)
        a_idx = self._amplitude_to_index(event.amplitude)

        if 0 <= p_idx < self._phase_bins and 0 <= a_idx < self._amplitude_bins:
            if self._sparse_mode:
                key = (p_idx, a_idx)
                self._sparse_matrix[key] = self._sparse_matrix.get(key, 0.0) + 1.0
            else:
                self._matrix[p_idx, a_idx] += 1.0

    def add_events(self, events: List[Tuple[float, float]]) -> None:
        """
        批量添加事件 (phase, amplitude)

        Args:
            events: (相位, 幅值) 元组列表
        """
        for phase, amplitude in events:
            self.add_event(phase, amplitude)

    def add_detected_events(self, peaks: list, n_samples: int, phase_offset: float = 0.0) -> None:
        """把**已经检测好的** PeakInfo 列表并入图谱。

        这是首选入口：`uhf_pipeline` 每帧本来就跑过一次 `detect_pd_events`（事件流、
        报警、入库都用它），再让 PRPD 用另一个检测器重测一遍就会出现两套真相。
        实测过分歧有多大：注入 3 正 3 负共 6 个脉冲时，事件流找到 6 个（3 正 3 负），
        而 PRPD 找到 7 个、全部标成"负极性"、其中 5 个是负极性脉冲的正旁瓣被当成
        独立事件（幅值只有真实值的一半不到，相位也偏）。

        `detect_pd_events` 给的 amplitude 是幅值大小（负极性也返回正数），极性只在
        `polarity` 字段里（0=正、1=负），所以这里直接透传，绝不再从幅值符号反推。

        Args:
            peaks: PeakInfo 列表
            n_samples: 这些峰值所在波形的总点数（用于位置→相位换算）
            phase_offset: 相位偏移 (度)
        """
        if n_samples <= 0:
            return
        self._cycle_count += 1
        for peak in peaks:
            phase = (peak.position / n_samples * 360 + phase_offset) % 360
            self.add_event(
                phase,
                float(peak.amplitude),
                polarity=int(getattr(peak, "polarity", 0)),
                energy=float(getattr(peak, "energy", 0.0) or 0.0),
                cycle=self._cycle_count,
            )

    def add_waveform(self, waveform: np.ndarray, phase_offset: float = 0.0) -> None:
        """
        从波形数据中提取 PRPD 事件

        通过对波形进行峰值检测，提取超过阈值的峰值点作为局放事件。
        每次调用视为一个工频周期。

        用 `detect_pd_events` 而不是 `detect_peaks_simple`：后者只找 `> +阈值` 的峰，
        等于在图谱里结构性地丢掉所有负极性放电，还会把负脉冲的正旁瓣当成独立事件。
        管线里请优先用 `add_detected_events`，避免同一帧检测两遍。

        Args:
            waveform: 波形数据
            phase_offset: 相位偏移 (度)
        """
        from .peak_detector import PeakDetector

        peaks = PeakDetector().detect_pd_events(np.asarray(waveform))
        self.add_detected_events(peaks, len(waveform), phase_offset=phase_offset)

    # ── 分析 ─────────────────────────────────────────

    def compute(self, mode: PRPDMode = PRPDMode.HEATMAP) -> PRPDResult:
        """
        计算 PRPD 结果

        这里刻意不套结果缓存。`add_event` 已经增量维护 `self._matrix`，compute() 的
        全部开销只有一次 `.copy()` 和一遍 max/min，缓存命中也省不下任何真实计算；
        而图谱的输入是**只增不减**的累积事件流，任何以事件集合为键的缓存必然每帧
        换键、永远不命中。实测过这个反例：连续事件下 60 次 compute() 命中 0 次，
        却让每帧多花两个 md5 + 一份 720 KB 副本，sidecar 因此在 200 MB 处永久封顶。

        Args:
            mode: 显示模式

        Returns:
            PRPDResult 对象
        """
        amplitude_bins = np.linspace(self._min_amplitude, self._max_amplitude, self._amplitude_bins)
        phase_bins = np.linspace(0, 360, self._phase_bins)

        result_matrix = self._matrix.copy()

        # 密度图模式: 高斯核平滑
        if mode == PRPDMode.DENSITY:
            from scipy.ndimage import gaussian_filter

            result_matrix = gaussian_filter(result_matrix, sigma=1.5)

        amplitudes = [e.amplitude for e in self._events]
        max_amp = max(amplitudes) if amplitudes else 0
        min_amp = min(amplitudes) if amplitudes else 0

        return PRPDResult(
            phase_bins=phase_bins,
            amplitude_bins=amplitude_bins,
            matrix=result_matrix,
            events=list(self._events)[-5000:],  # 最多保留 5000 个散点
            mode=mode,
            total_events=len(self._events),
            max_amplitude=max_amp,
            min_amplitude=min_amp,
        )

    def compute_stats(self) -> dict:
        """
        计算 PRPD 统计特征

        Returns:
            包含统计特征的字典
        """
        if len(self._events) == 0:
            return {
                "total_events": 0,
                "max_amplitude": 0,
                "avg_amplitude": 0,
                "positive_ratio": 0,
                "phase_concentration": 0,
            }

        amplitudes = np.array([e.amplitude for e in self._events])
        phases = np.array([e.phase for e in self._events])
        polarities = np.array([e.polarity for e in self._events])

        # 相位集中度 (0~1, 越接近 1 表示越集中在特定相位)
        phase_hist, _ = np.histogram(phases, bins=36, range=(0, 360))
        phase_concentration = float(np.max(phase_hist) / (np.sum(phase_hist) + 1e-10))

        return {
            "total_events": len(self._events),
            "max_amplitude": float(np.max(amplitudes)),
            "avg_amplitude": float(np.mean(amplitudes)),
            "positive_ratio": float(np.sum(polarities == 0) / len(polarities)),
            "phase_concentration": phase_concentration,
        }

    def reset(self) -> None:
        """重置所有数据"""
        self._matrix.fill(0)
        self._events.clear()
        self._cycle_count = 0

    def decay(self, factor: float = 0.995) -> None:
        """
        指数衰减矩阵，模拟旧数据权重降低

        Args:
            factor: 衰减系数 (0~1)，0.995 表示每次衰减 0.5%
        """
        self._matrix *= factor
        # 钳制负浮点误差
        np.clip(self._matrix, 0, None, out=self._matrix)

    def rebuild_matrix(self) -> None:
        """
        用当前 _max_amplitude 从事件缓存重新构建矩阵。

        当 max_amplitude 被动态扩展后调用此方法，
        避免旧数据因分箱截断而集中在顶部行。
        """
        self._matrix.fill(0)
        for event in self._events:
            p_idx = self._phase_to_index(event.phase)
            a_idx = self._amplitude_to_index(event.amplitude)
            if 0 <= p_idx < self._phase_bins and 0 <= a_idx < self._amplitude_bins:
                self._matrix[p_idx, a_idx] += 1.0

    # ── 内部辅助 ────────────────────────────────────

    def _phase_to_index(self, phase: float) -> int:
        """相位值 → 矩阵列索引"""
        idx = int(phase / 360.0 * self._phase_bins)
        return max(0, min(self._phase_bins - 1, idx))

    def _amplitude_to_index(self, amplitude: float) -> int:
        """幅值 → 矩阵行索引"""
        normalized = (amplitude - self._min_amplitude) / (self._max_amplitude - self._min_amplitude + 1e-10)
        idx = int(normalized * (self._amplitude_bins - 1))
        return max(0, min(self._amplitude_bins - 1, idx))

    def __repr__(self) -> str:
        return f"PRPDProcessor(bins={self._phase_bins}×{self._amplitude_bins}, events={len(self._events)})"
