# -*- coding: utf-8 -*-
"""
趋势曲线控件 - TrendChartWidget

基于 PyQtGraph 实现多系列趋势曲线显示。
特性:
- 多指标曲线叠加
- 时间范围切换 (1h/24h/7d/30d)
- 自动缩放
- 图例显示
- 鼠标悬停读数
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ui.design_tokens import DT

pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND, background=DT.C.CHART_BACKGROUND)


class TimeAxisItem(pg.AxisItem):
    """时间轴项 - 将 Unix 时间戳格式化为 HH:MM:SS"""

    def tickStrings(self, values, scale, spacing):
        return [datetime.fromtimestamp(v).strftime("%H:%M:%S") for v in values]


# 曲线颜色序列（使用 DT 令牌）
SERIES_COLORS = [
    QColor(DT.C.ACCENT_PRIMARY),     # 蓝色
    QColor(DT.C.STATUS_ERROR),       # 红色
    QColor(DT.C.STATUS_SUCCESS),     # 绿色
    QColor(DT.C.STATUS_WARNING),     # 黄色
    QColor(DT.C.ACCENT_SECONDARY),   # 紫色
    QColor(DT.C.CHART_WAVEFORM),     # 青色
]

TIME_RANGES = {
    "1h": ("最近 1 小时", 3600),
    "24h": ("最近 24 小时", 86400),
    "7d": ("最近 7 天", 604800),
    "30d": ("最近 30 天", 2592000),
}


class TrendChartWidget(QWidget):
    """趋势曲线控件"""

    range_changed = Signal(str)  # 时间范围变化

    def __init__(self, title: str = "趋势曲线", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._title = title
        self._current_range = "1h"
        self._series: Dict[str, dict] = {}  # name -> {curve, color, data}
        self._auto_range = True

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 标题栏 + 时间选择
        header = QHBoxLayout()
        self._title_label = QLabel(self._title)
        self._title_label.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        self._title_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        header.addWidget(self._title_label)

        header.addStretch()

        # 时间范围按钮
        self._range_buttons: Dict[str, QPushButton] = {}
        for key, (label, _) in TIME_RANGES.items():
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setCheckable(True)
            btn.setChecked(key == self._current_range)
            btn.setStyleSheet(self._range_button_style(key == self._current_range))
            btn.clicked.connect(lambda checked, k=key: self._set_time_range(k))
            header.addWidget(btn)
            self._range_buttons[key] = btn

        layout.addLayout(header)

        # PyQtGraph 绘图控件（使用 TimeAxisItem 格式化 X 轴时间戳）
        self._plot_widget = pg.PlotWidget(axisItems={'bottom': TimeAxisItem(orientation='bottom')})
        self._plot_widget.setBackground(DT.C.CHART_BACKGROUND)
        self._plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._plot_widget.setMinimumHeight(150)

        self._plot_widget.setLabel("left", "幅值", units="mV")
        self._plot_widget.setLabel("bottom", "时间")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.2)

        # 启用图例
        self._plot_widget.addLegend()

        # X 轴时间格式
        self._plot_widget.getAxis("bottom").setTickFont(DT.T.get_font("Segoe UI", 9))

        layout.addWidget(self._plot_widget)

    # ── 数据管理 ─────────────────────────────────────

    def add_series(self, name: str, color: Optional[QColor] = None) -> None:
        """
        添加数据系列

        Args:
            name: 系列名称
            color: 线条颜色（自动分配）
        """
        if name in self._series:
            return

        if color is None:
            color = SERIES_COLORS[len(self._series) % len(SERIES_COLORS)]

        pen = pg.mkPen(color, width=1.5)
        curve = self._plot_widget.plot(pen=pen, name=name)

        self._series[name] = {
            "curve": curve,
            "color": color,
            "data": deque(),  # [(timestamp_seconds, value), ...]
        }

    def remove_series(self, name: str) -> None:
        """移除数据系列"""
        if name in self._series:
            self._plot_widget.removeItem(self._series[name]["curve"])
            del self._series[name]

    def update_series(self, name: str, timestamp: datetime, value: float) -> None:
        """
        更新系列数据点

        Args:
            name: 系列名称
            timestamp: 时间戳
            value: 数值
        """
        if name not in self._series:
            self.add_series(name)

        ts = timestamp.timestamp()
        data = self._series[name]["data"]
        data.append((ts, value))

        # 裁剪超出时间范围的数据
        range_seconds = TIME_RANGES.get(self._current_range, (None, 3600))[1]
        cutoff = ts - range_seconds * 2  # 保留 2 倍范围用于过渡
        while data and data[0][0] < cutoff:
            data.popleft()

        self._refresh_plot()

    def set_data(self, name: str, data: List[Tuple[datetime, float]]) -> None:
        """
        批量设置系列数据

        Args:
            name: 系列名称
            data: [(datetime, value), ...]
        """
        if name not in self._series:
            self.add_series(name)

        dq = self._series[name]["data"]
        dq.clear()
        for dt, v in data:
            dq.append((dt.timestamp(), v))
        self._refresh_plot()

    def clear_series(self, name: Optional[str] = None) -> None:
        """清空系列数据"""
        if name:
            if name in self._series:
                self._series[name]["data"].clear()
        else:
            for s in self._series.values():
                s["data"].clear()
        self._refresh_plot()

    def clear_all(self) -> None:
        """清空所有"""
        for name in list(self._series.keys()):
            self.remove_series(name)
        self._series.clear()

    # ── 时间范围 ─────────────────────────────────────

    def _set_time_range(self, key: str) -> None:
        self._current_range = key
        for k, btn in self._range_buttons.items():
            btn.setChecked(k == key)
            btn.setStyleSheet(self._range_button_style(k == key))

        self._refresh_plot()
        self.range_changed.emit(key)

    def _refresh_plot(self) -> None:
        """刷新所有曲线"""
        range_seconds = TIME_RANGES.get(self._current_range, (None, 3600))[1]
        now = datetime.now().timestamp()
        t_start = now - range_seconds

        for name, info in self._series.items():
            data = info["data"]
            # 过滤时间范围
            visible = [(t, v) for t, v in data if t >= t_start]

            if visible:
                times = np.array([t for t, _ in visible])
                values = np.array([v for _, v in visible])
                info["curve"].setData(times, values)
            else:
                info["curve"].clear()

        # X 轴范围
        self._plot_widget.setXRange(t_start, now)

        # 自动 Y 轴
        if self._auto_range:
            self._plot_widget.getViewBox().autoRange(padding=0.1)

    # ── 配置 ─────────────────────────────────────────

    def set_title(self, title: str) -> None:
        self._title = title
        self._title_label.setText(title)

    @property
    def current_range(self) -> str:
        return self._current_range

    @property
    def plot_widget(self) -> pg.PlotWidget:
        return self._plot_widget

    @staticmethod
    def _range_button_style(active: bool) -> str:
        if active:
            return (
                f"QPushButton {{ background: {DT.C.ACCENT_PRIMARY}; color: white; border: none; "
                f"border-radius: 4px; padding: 3px 10px; font-size: 11px; font-weight: 600; }}"
            )
        return (
            f"QPushButton {{ background: transparent; color: {DT.C.TEXT_SECONDARY}; "
            f"border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px; padding: 3px 10px; "
            f"font-size: 11px; }}"
            f"QPushButton:hover {{ background: {DT.C.BG_HOVER}; }}"
        )
