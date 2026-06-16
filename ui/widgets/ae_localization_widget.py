# -*- coding: utf-8 -*-
"""
AE 声源定位展示组件

显示 TDOA 定位结果:
- 2D 平面图: 传感器位置 + 声源位置
- 定位结果详情: 坐标、置信度、残差
- 定位历史轨迹
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.design_tokens import DT

try:
    import pyqtgraph as pg

    pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND, background=DT.C.CHART_BACKGROUND)
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False


class AELocalizationWidget(QWidget):
    """
    AE 声源定位展示组件

    显示:
    - 2D 平面图: 传感器阵列位置 (蓝色方块) + 声源位置 (红色圆点)
    - 定位结果标签: 坐标、置信度、残差
    - 历史轨迹 (半透明红色点)
    """

    def __init__(self, title: str = "AE 声源定位", parent: QWidget = None):
        super().__init__(parent)
        self._title = title
        self._sensor_positions: List[tuple] = []  # [(x, y), ...]
        self._history: deque = deque(maxlen=200)
        self._latest_result: Optional[dict] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._build_header(layout)
        self._build_plot(layout)
        self._build_info(layout)

    def _build_header(self, layout: QVBoxLayout) -> None:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        title = QLabel(self._title)
        title.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()

        self._status_label = QLabel("等待定位数据")
        self._status_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 10px;")
        header.addWidget(self._status_label)

        layout.addLayout(header)

    def _build_plot(self, layout: QVBoxLayout) -> None:
        if not HAS_PYQTGRAPH:
            placeholder = QLabel("定位图需要 pyqtgraph")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
            layout.addWidget(placeholder, 1)
            self._plot_widget = None
            return

        frame = QFrame()
        frame.setObjectName("localizationFrame")
        frame.setStyleSheet(
            f"""
            QFrame#localizationFrame {{
                background: {DT.C.BG_PRIMARY};
                border: 1px solid {DT.C.BORDER_DEFAULT};
                border-radius: {DT.R.MD}px;
            }}
        """
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(4, 4, 4, 4)

        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground(DT.C.CHART_BACKGROUND)
        self._plot_widget.setLabel("bottom", "X (m)")
        self._plot_widget.setLabel("left", "Y (m)")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self._plot_widget.setAspectLocked(False)

        # 传感器散点 (蓝色方块)
        _sensor_brush = QColor(DT.C.ACCENT_PRIMARY)
        _sensor_brush.setAlpha(180)
        self._sensor_scatter = pg.ScatterPlotItem(
            size=12, symbol="s", pen=pg.mkPen(DT.C.ACCENT_PRIMARY, width=1.5),
            brush=pg.mkBrush(_sensor_brush),
        )
        self._plot_widget.addItem(self._sensor_scatter)

        # 历史轨迹 (半透明红点)
        _history_brush = QColor(DT.C.STATUS_ERROR)
        _history_brush.setAlpha(80)
        self._history_scatter = pg.ScatterPlotItem(
            size=5, symbol="o", pen=None,
            brush=pg.mkBrush(_history_brush),
        )
        self._plot_widget.addItem(self._history_scatter)

        # 最新声源位置 (红色大圆点)
        _source_brush = QColor(DT.C.STATUS_ERROR)
        _source_brush.setAlpha(200)
        self._source_scatter = pg.ScatterPlotItem(
            size=16, symbol="o", pen=pg.mkPen(DT.C.STATUS_ERROR, width=2),
            brush=pg.mkBrush(_source_brush),
        )
        self._plot_widget.addItem(self._source_scatter)

        fl.addWidget(self._plot_widget)
        layout.addWidget(frame, 1)

    def _build_info(self, layout: QVBoxLayout) -> None:
        info = QHBoxLayout()
        info.setSpacing(DT.S.MD)

        self._coord_label = QLabel("坐标: —")
        self._coord_label.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 11px;")
        info.addWidget(self._coord_label)

        self._conf_label = QLabel("置信度: —")
        self._conf_label.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 11px;")
        info.addWidget(self._conf_label)

        self._residual_label = QLabel("残差: —")
        self._residual_label.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 11px;")
        info.addWidget(self._residual_label)

        self._sensors_label = QLabel("传感器: —")
        self._sensors_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 10px;")
        info.addWidget(self._sensors_label)

        layout.addLayout(info)

    # ── 公开接口 ─────────────────────────────────────

    def set_sensor_positions(self, positions: List[tuple]) -> None:
        """
        设置传感器位置

        Args:
            positions: [(x, y), ...] 坐标列表 (m)
        """
        self._sensor_positions = positions
        if not HAS_PYQTGRAPH or self._plot_widget is None:
            return

        if positions:
            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            self._sensor_scatter.setData(xs, ys)

            # 自动调整视图范围
            margin = max(
                max(xs) - min(xs),
                max(ys) - min(ys),
                0.5,
            ) * 0.3
            self._plot_widget.setXRange(min(xs) - margin, max(xs) + margin)
            self._plot_widget.setYRange(min(ys) - margin, max(ys) + margin)

            self._sensors_label.setText(f"传感器: {len(positions)}")

    def update_localization(self, result: dict) -> None:
        """
        更新定位结果

        Args:
            result: LocalizationResult 的字典形式, 包含 x, y, residual, confidence, sensors_used
        """
        self._latest_result = result
        self._history.append(result)

        x = result.get("x", 0)
        y = result.get("y", 0)
        confidence = result.get("confidence", 0)
        residual = result.get("residual", 0)
        sensors_used = result.get("sensors_used", 0)

        # 更新标签
        self._coord_label.setText(f"坐标: ({x:.3f}, {y:.3f}) m")
        self._conf_label.setText(f"置信度: {confidence:.1%}")
        self._residual_label.setText(f"残差: {residual:.4f} m")
        self._sensors_label.setText(f"传感器: {sensors_used}")

        # 置信度颜色
        if confidence >= 0.7:
            self._conf_label.setStyleSheet(f"color: {DT.C.STATUS_SUCCESS}; font-size: {DT.FS.BODY_SMALL}px; font-weight: 600;")
        elif confidence >= 0.4:
            self._conf_label.setStyleSheet(f"color: {DT.C.STATUS_WARNING}; font-size: {DT.FS.BODY_SMALL}px; font-weight: 600;")
        else:
            self._conf_label.setStyleSheet(f"color: {DT.C.STATUS_ERROR}; font-size: {DT.FS.BODY_SMALL}px; font-weight: 600;")

        self._status_label.setText("定位中")

        if not HAS_PYQTGRAPH or self._plot_widget is None:
            return

        # 更新最新声源位置
        self._source_scatter.setData([x], [y])

        # 更新历史轨迹
        if self._history:
            hx = [r.get("x", 0) for r in self._history]
            hy = [r.get("y", 0) for r in self._history]
            self._history_scatter.setData(hx, hy)

    def clear(self) -> None:
        """清空所有数据"""
        self._history.clear()
        self._latest_result = None
        self._coord_label.setText("坐标: —")
        self._conf_label.setText("置信度: —")
        self._residual_label.setText("残差: —")
        self._status_label.setText("等待定位数据")

        if HAS_PYQTGRAPH and self._plot_widget is not None:
            self._source_scatter.setData([], [])
            self._history_scatter.setData([], [])
