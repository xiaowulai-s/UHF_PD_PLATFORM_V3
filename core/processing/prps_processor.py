# -*- coding: utf-8 -*-
"""
PRPS 分析处理器 - Phase Resolved Pulse Sequence

PRPS 以 512×360 矩阵表示连续工频周期的局放分布：
- X 轴: 相位 (0~360°), 360 列
- Y 轴: 工频周期序号, 512 行（滚动刷新）
- 每个单元格: 放电幅值或能量

每来一个新周期，数据向下滚动一行，新数据填入第一行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class PRPSResult:
    """PRPS 分析结果"""

    matrix: np.ndarray  # 512×360 矩阵
    phase_axis: np.ndarray  # 相位轴
    cycle_axis: np.ndarray  # 周期序号轴
    total_cycles: int = 0
    current_cycle: int = 0


class PRPSProcessor:
    """
    PRPS 图谱处理器

    Args:
        rows: 行数 (周期数)，默认 512
        cols: 列数 (相位分辨率)，默认 360
        max_amplitude: 最大幅值范围 (mV)
    """

    def __init__(self, rows: int = 512, cols: int = 360, max_amplitude: float = 100.0):
        self._rows = rows
        self._cols = cols
        self._max_amplitude = max_amplitude

        # PRPS 矩阵 [rows, cols]
        self._matrix = np.zeros((rows, cols), dtype=np.float64)
        self._cycle_count = 0

    # ── 属性 ─────────────────────────────────────────

    @property
    def matrix(self) -> np.ndarray:
        return self._matrix

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def max_amplitude(self) -> float:
        return self._max_amplitude

    @max_amplitude.setter
    def max_amplitude(self, value: float) -> None:
        self._max_amplitude = value

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    # ── 数据更新 ─────────────────────────────────────

    def add_cycle(self, phase_data: np.ndarray) -> None:
        """
        添加一个工频周期的 PRPS 数据

        Args:
            phase_data: 长度为 360 的数组，每个元素对应该相位的放电幅值

        Raises:
            ValueError: 数据长度与列数不匹配
        """
        if len(phase_data) != self._cols:
            raise ValueError(f"数据长度 {len(phase_data)} 与列数 {self._cols} 不匹配")

        # 向下滚动一行
        self._matrix = np.roll(self._matrix, 1, axis=0)

        # 新数据填入第一行
        self._matrix[0] = np.clip(phase_data, 0, self._max_amplitude)

        self._cycle_count += 1

    def add_pulse(self, phase: float, amplitude: float) -> None:
        """
        添加单个脉冲到当前周期

        Args:
            phase: 相位 (0~360°)
            amplitude: 幅值 (mV)
        """
        col = int((phase % 360) / 360 * self._cols)
        col = max(0, min(self._cols - 1, col))
        self._matrix[0, col] = max(self._matrix[0, col], amplitude)

    def add_pulses(self, pulses: List[tuple]) -> None:
        """
        批量添加脉冲

        Args:
            pulses: [(phase, amplitude), ...] 列表
        """
        for phase, amplitude in pulses:
            self.add_pulse(phase, amplitude)

    def new_cycle(self) -> None:
        """开始新周期（滚动并将第一行置零）"""
        self._matrix = np.roll(self._matrix, 1, axis=0)
        self._matrix[0].fill(0)
        self._cycle_count += 1

    # ── 分析 ─────────────────────────────────────────

    def compute(self) -> PRPSResult:
        """
        计算 PRPS 结果

        Returns:
            PRPSResult 对象
        """
        phase_axis = np.linspace(0, 360, self._cols)
        cycle_axis = np.arange(self._rows)

        return PRPSResult(
            matrix=self._matrix.copy(),
            phase_axis=phase_axis,
            cycle_axis=cycle_axis,
            total_cycles=self._cycle_count,
            current_cycle=self._cycle_count % self._rows,
        )

    def get_latest_cycle(self) -> np.ndarray:
        """获取最新一个周期的数据"""
        return self._matrix[0].copy()

    def get_cycle_slice(self, n_cycles: int) -> np.ndarray:
        """获取最近 n 个周期的数据"""
        n = min(n_cycles, self._rows)
        return self._matrix[:n].copy()

    def compute_average_prpd(self, n_cycles: Optional[int] = None) -> np.ndarray:
        """
        计算平均 PRPD（相当于多周期叠加的 PRPD 图谱）

        Args:
            n_cycles: 参与平均的周期数，默认全部

        Returns:
            平均后的 PRPD 向量 (360,)
        """
        n = min(n_cycles or self._rows, self._rows)
        return np.mean(self._matrix[:n], axis=0)

    def compute_cycle_stats(self) -> dict:
        """
        计算 PRPS 统计特征

        Returns:
            统计信息字典
        """
        active = self._matrix[self._matrix > 0]
        return {
            "total_cycles": self._cycle_count,
            "active_cells": int(np.count_nonzero(self._matrix)),
            "density": float(np.count_nonzero(self._matrix) / self._matrix.size),
            "max_value": float(np.max(self._matrix)),
            "mean_active": float(np.mean(active)) if len(active) > 0 else 0.0,
            "latest_cycle_max": float(np.max(self._matrix[0])),
        }

    def reset(self) -> None:
        """重置所有数据"""
        self._matrix.fill(0)
        self._cycle_count = 0

    def __repr__(self) -> str:
        return f"PRPSProcessor(rows={self._rows}, cols={self._cols}, cycles={self._cycle_count})"
