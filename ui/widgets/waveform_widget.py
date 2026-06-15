# -*- coding: utf-8 -*-
"""
实时波形控件 - WaveformWidget

基于 PyQtGraph 实现的高性能实时波形显示。
特性:
- ≥20 FPS 实时刷新
- 缩放/拖拽 (pyqtgraph 内置)
- 游标测量 (十字光标读数)
- 自动量程 / 固定量程切换
- 暂停/继续
- 触发位置标记
- Fluent Design 风格
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ui.design_tokens import DT

pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND, background=DT.C.CHART_BACKGROUND)


class WaveformWidget(QWidget):
    """实时波形显示控件"""

    # 信号
    paused = Signal(bool)
    cursor_position = Signal(float, float)  # (时间, 幅值)
    trigger_changed = Signal(int)  # 触发位置

    # 波形颜色
    WAVEFORM_COLOR = QColor(DT.C.CHART_WAVEFORM)  # 波形颜色
    TRIGGER_COLOR = QColor(DT.C.STATUS_ERROR)  # 红色触发标记
    CURSOR_COLOR = QColor(DT.C.ACCENT_PRIMARY)  # 蓝色游标
    GRID_COLOR = QColor(DT.C.CHART_GRID)  # 网格

    def __init__(
        self,
        title: str = "实时波形",
        max_points: int = 4096,
        sample_rate: int = 100_000_000,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._title = title
        self._max_points = max_points
        self._sample_rate = sample_rate
        self._paused = False
        self._auto_range = True
        self._cursor_enabled = False
        self._cursor_vline: Optional[pg.InfiniteLine] = None
        self._cursor_hline: Optional[pg.InfiniteLine] = None
        self._trigger_pos: Optional[int] = None
        self._trigger_line: Optional[pg.InfiniteLine] = None

        self._data: Optional[np.ndarray] = None
        self._time_axis: Optional[np.ndarray] = None
        self._update_counter = 0

        self._setup_ui()
        self._setup_cursor()
        self._setup_trigger_marker()

    def _setup_ui(self) -> None:
        """构建 UI 布局"""
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

        # 状态指示
        self._status_label = QLabel("● 实时")
        self._status_label.setStyleSheet(f"color: {DT.C.STATUS_SUCCESS}; font-size: 11px; font-weight: 600;")
        header.addWidget(self._status_label)

        # 信息标签
        self._info_label = QLabel("")
        self._info_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 11px;")
        header.addWidget(self._info_label)

        # 工具栏按钮
        self._btn_auto_range = QPushButton("自动量程")
        self._btn_auto_range.setFixedHeight(32)
        self._btn_auto_range.setCheckable(True)
        self._btn_auto_range.setChecked(True)
        self._btn_auto_range.setStyleSheet(self._button_style(DT.C.ACCENT_PRIMARY))
        self._btn_auto_range.clicked.connect(self._toggle_auto_range)
        header.addWidget(self._btn_auto_range)

        self._btn_cursor = QPushButton("游标")
        self._btn_cursor.setFixedHeight(32)
        self._btn_cursor.setCheckable(True)
        self._btn_cursor.setStyleSheet(self._button_style())
        self._btn_cursor.clicked.connect(self._toggle_cursor)
        header.addWidget(self._btn_cursor)

        self._btn_pause = QPushButton("暂停")
        self._btn_pause.setFixedHeight(32)
        self._btn_pause.setCheckable(True)
        self._btn_pause.setStyleSheet(self._button_style())
        self._btn_pause.clicked.connect(self._toggle_pause)
        header.addWidget(self._btn_pause)

        layout.addLayout(header)

        # PyQtGraph 绘图控件
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground(DT.C.CHART_BACKGROUND)
        self._plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._plot_widget.setMinimumHeight(150)

        # 坐标轴标签（默认 UHF: mV）
        self._y_unit = "mV"
        self._x_unit = "μs"
        self._plot_widget.setLabel("left", "幅值", units=self._y_unit)
        self._plot_widget.setLabel("bottom", "时间", units=self._x_unit)

        # 网格
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)

        # 鼠标移动事件
        self._proxy = pg.SignalProxy(
            self._plot_widget.scene().sigMouseMoved,
            slot=self._on_mouse_moved,
        )

        # 波形曲线
        pen = pg.mkPen(self.WAVEFORM_COLOR, width=1.5)
        self._curve = self._plot_widget.plot(pen=pen, clear=True)

        # 视图框设置
        self._view_box = self._plot_widget.getViewBox()
        self._view_box.setMouseMode(pg.ViewBox.RectMode)

        layout.addWidget(self._plot_widget)

    def _setup_cursor(self) -> None:
        """初始化游标线（隐藏）"""
        self._cursor_vline = pg.InfiniteLine(angle=90, movable=True, pen=pg.mkPen(self.CURSOR_COLOR, width=1))
        self._cursor_hline = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen(self.CURSOR_COLOR, width=1))
        self._cursor_vline.setVisible(False)
        self._cursor_hline.setVisible(False)
        self._plot_widget.addItem(self._cursor_vline)
        self._plot_widget.addItem(self._cursor_hline)

    def _setup_trigger_marker(self) -> None:
        """初始化触发位置标记"""
        self._trigger_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen(self.TRIGGER_COLOR, width=1.5, style=Qt.PenStyle.DashLine),
        )
        self._trigger_line.setVisible(False)
        self._plot_widget.addItem(self._trigger_line)

    # ── 数据更新 ─────────────────────────────────────

    def update_waveform(self, waveform: np.ndarray, sample_rate: Optional[int] = None) -> None:
        """
        更新波形数据

        Args:
            waveform: 一维数组波形数据
            sample_rate: 采样率 (Hz)，可选
        """
        if self._paused:
            return

        if sample_rate is not None:
            self._sample_rate = sample_rate

        n = len(waveform)
        if n == 0:
            return

        # 生成时间轴 (μs)
        dt_us = 1_000_000 / self._sample_rate
        time_axis = np.arange(n) * dt_us

        self._data = waveform
        self._time_axis = time_axis

        # 更新曲线
        self._curve.setData(time_axis, waveform)

        # 自动量程
        if self._auto_range:
            self._view_box.autoRange(padding=0.05)

        # 更新触发标记
        if self._trigger_pos is not None and self._trigger_pos < n:
            trigger_time = self._trigger_pos * dt_us
            self._trigger_line.setValue(trigger_time)

        # 更新信息
        self._update_counter += 1
        if self._update_counter % 10 == 0:
            v_max = float(np.max(np.abs(waveform)))
            time_range_us = n * dt_us
            self._info_label.setText(f"{v_max:.1f} {self._y_unit}pk | {time_range_us:.1f} μs | {n} pts")

    # ── 单位切换 ─────────────────────────────────────

    def set_y_unit(self, unit: str) -> None:
        """切换 Y 轴单位 (UHF: mV, AE: μV)"""
        self._y_unit = unit
        self._plot_widget.setLabel("left", "幅值", units=unit)

    # ── 控件切换 ─────────────────────────────────────

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self._btn_pause.setText("继续" if self._paused else "暂停")
        self._btn_pause.setStyleSheet(
            self._button_style(DT.C.STATUS_WARNING if self._paused else DT.C.ACCENT_PRIMARY)
            if self._paused
            else self._button_style()
        )
        self._status_label.setText("● 暂停" if self._paused else "● 实时")
        self._status_label.setStyleSheet(
            f"color: {DT.C.STATUS_WARNING if self._paused else DT.C.STATUS_SUCCESS}; font-size: 11px; font-weight: 600;"
        )
        self.paused.emit(self._paused)

    def _toggle_auto_range(self) -> None:
        self._auto_range = self._btn_auto_range.isChecked()
        self._btn_auto_range.setStyleSheet(
            self._button_style(DT.C.ACCENT_PRIMARY) if self._auto_range else self._button_style()
        )
        if self._auto_range and self._data is not None:
            self._view_box.autoRange(padding=0.05)

    def _toggle_cursor(self) -> None:
        self._cursor_enabled = self._btn_cursor.isChecked()
        self._btn_cursor.setStyleSheet(
            self._button_style(DT.C.ACCENT_PRIMARY) if self._cursor_enabled else self._button_style()
        )
        self._cursor_vline.setVisible(self._cursor_enabled)
        self._cursor_hline.setVisible(self._cursor_enabled)

    def _on_mouse_moved(self, evt) -> None:
        """鼠标移动时更新游标读数"""
        if not self._cursor_enabled:
            return
        pos = evt[0]
        if self._view_box.sceneBoundingRect().contains(pos):
            mouse_point = self._view_box.mapSceneToView(pos)
            x, y = mouse_point.x(), mouse_point.y()
            if self._cursor_vline:
                self._cursor_vline.setValue(x)
            if self._cursor_hline:
                self._cursor_hline.setValue(y)
            self.cursor_position.emit(float(x), float(y))

    # ── 配置 ─────────────────────────────────────────

    def set_trigger_position(self, pos: Optional[int]) -> None:
        """设置触发位置（用于标记）"""
        self._trigger_pos = pos
        self._trigger_line.setVisible(pos is not None)
        if pos is not None and self._time_axis is not None and pos < len(self._time_axis):
            self._trigger_line.setValue(self._time_axis[pos])

    def set_title(self, title: str) -> None:
        self._title = title
        self._title_label.setText(title)

    def set_sample_rate(self, rate: int) -> None:
        """设置采样率"""
        self._sample_rate = rate

    def clear(self) -> None:
        """清空波形"""
        self._curve.clear()
        self._data = None
        self._time_axis = None
        self._info_label.setText("")
        self._update_counter = 0

    def fit_to_view(self) -> None:
        """自适应视图"""
        if self._data is not None:
            self._view_box.autoRange(padding=0.05)

    # ── 属性 ─────────────────────────────────────────

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def plot_widget(self) -> pg.PlotWidget:
        return self._plot_widget

    @staticmethod
    def _button_style(accent: str = None) -> str:
        """按钮样式"""
        if accent:
            return (
                f"QPushButton {{ background: {accent}; color: white; border: none; "
                f"border-radius: 4px; padding: 4px 10px; font-size: 11px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: {DT.C.ACCENT_HOVER}; }}"
            )
        return (
            f"QPushButton {{ background: transparent; color: {DT.C.TEXT_SECONDARY}; "
            f"border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px; padding: 4px 10px; "
            f"font-size: 11px; }}"
            f"QPushButton:hover {{ background: {DT.C.BG_HOVER}; border-color: {DT.C.BORDER_HOVER}; }}"
            f"QPushButton:checked {{ background: {DT.C.ACCENT_SUBTLE}; color: {DT.C.ACCENT_PRIMARY}; "
            f"border-color: {DT.C.ACCENT_PRIMARY}; }}"
        )
