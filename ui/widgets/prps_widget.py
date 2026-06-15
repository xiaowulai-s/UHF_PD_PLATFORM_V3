# -*- coding: utf-8 -*-
"""
PRPS 图谱控件 - PRPSWidget

Phase Resolved Pulse Sequence 图谱。
以 512×360 矩阵表示连续工频周期的局放分布：
- X 轴: 相位 (0~360°)
- Y 轴: 工频周期编号（最新在顶部）
- 颜色: 放电幅值

支持滚动刷新和交互缩放。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QTransform
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui.design_tokens import DT

pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND, background=DT.C.CHART_BACKGROUND)


class PRPSWidget(QWidget):
    """PRPS 图谱控件"""

    COLORMAP = DT.C.CHART_COLORMAP

    def __init__(self, title: str = "PRPS 图谱", rows: int = 512, cols: int = 360, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._title = title
        self._rows = rows
        self._cols = cols
        self._max_amplitude = 100.0
        self._cycle_count = 0  # 追踪已追加的周期数，用于首次 autoLevels 判断

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 标题栏
        header = QHBoxLayout()
        self._title_label = QLabel(self._title)
        self._title_label.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        self._title_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        header.addWidget(self._title_label)

        header.addStretch()

        self._info_label = QLabel("0 周期")
        self._info_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 11px;")
        header.addWidget(self._info_label)

        layout.addLayout(header)

        # PyQtGraph 绘图控件
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground(DT.C.CHART_BACKGROUND)
        self._plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._plot_widget.setMinimumHeight(150)

        self._plot_widget.setLabel("left", "周期")
        self._plot_widget.setLabel("bottom", "相位", units="°")
        self._plot_widget.setXRange(0, 360)

        # 隐藏 Y 轴标签（周期编号无实际意义）
        self._plot_widget.getAxis("left").setTickFont(DT.T.get_font("Segoe UI", 8))
        self._plot_widget.getAxis("bottom").setTickFont(DT.T.get_font("Segoe UI", 8))

        # ImageItem 用于显示 PRPS 矩阵
        self._image_item = pg.ImageItem()
        self._plot_widget.addItem(self._image_item)

        # 设置 colormap
        cmap = pg.colormap.get(self.COLORMAP)
        self._image_item.setColorMap(cmap)

        # 初始空矩阵
        empty = np.zeros((self._rows, self._cols))
        self._image_item.setImage(empty, autoLevels=True)

        # 坐标映射
        tr = QTransform()
        tr.scale(360 / self._cols, 1)
        self._image_item.setTransform(tr)

        # 隐藏 Y 轴
        self._plot_widget.getAxis("left").setStyle(showValues=False)

        layout.addWidget(self._plot_widget)

    # ── 数据更新 ─────────────────────────────────────

    def update_matrix(self, matrix: np.ndarray, cycle_count: int = 0) -> None:
        """
        更新 PRPS 矩阵

        Args:
            matrix: 二维数组 [rows, cols]，值域 0~max_amplitude
            cycle_count: 累计周期数
        """
        if matrix.shape != (self._rows, self._cols):
            # 适配不同尺寸
            self._rows, self._cols = matrix.shape

        # 仅首次或矩阵结构变化时自动色阶
        self._image_item.setImage(matrix, autoLevels=True)
        self._plot_widget.setYRange(0, self._rows)

        if cycle_count > 0:
            self._info_label.setText(f"{cycle_count} 周期")

    def append_cycle_data(self, cycle_data: np.ndarray) -> None:
        """
        追加一个周期数据（滚动模式）

        Args:
            cycle_data: 长度为 360 的数组
        """
        if len(cycle_data) != self._cols:
            return

        # 获取当前图像数据并滚动（原地操作避免分配）
        current = self._image_item.image
        if current is None or current.shape != (self._rows, self._cols):
            current = np.zeros((self._rows, self._cols))
        else:
            # 原地滚动: 避免 np.roll 的分配开销
            current[1:] = current[:-1]
        current[0] = cycle_data

        # 仅首次自动色阶，后续使用固定色阶避免重算
        self._image_item.setImage(current, autoLevels=(self._cycle_count < 5))

        if self._cycle_count < 5:
            self._cycle_count += 1

    def update_from_prps_processor(self, matrix: np.ndarray, total_cycles: int) -> None:
        """从 PRPSProcessor 更新数据"""
        self.update_matrix(matrix, total_cycles)

    # ── 配置 ─────────────────────────────────────────

    def set_max_amplitude(self, max_amp: float) -> None:
        self._max_amplitude = max_amp

    def set_title(self, title: str) -> None:
        self._title = title
        self._title_label.setText(title)

    def clear(self) -> None:
        """清空图谱"""
        empty = np.zeros((self._rows, self._cols))
        self._image_item.setImage(empty, autoLevels=True)
        self._cycle_count = 0
        self._info_label.setText("0 周期")

    @property
    def plot_widget(self) -> pg.PlotWidget:
        return self._plot_widget

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def cols(self) -> int:
        return self._cols
