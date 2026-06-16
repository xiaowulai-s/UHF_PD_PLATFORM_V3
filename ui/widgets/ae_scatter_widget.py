# -*- coding: utf-8 -*-
"""
AE 特征散点图组件

显示 AE hit 参数之间的 2D 相关性:
- 持续时间 vs 幅值
- 平均频率 vs 幅值
- 振铃计数 vs MARSE 能量
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.design_tokens import DT

try:
    import pyqtgraph as pg

    pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND, background=DT.C.CHART_BACKGROUND)
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False


class AEScatterWidget(QWidget):
    """
    AE 特征散点图

    支持三种维度对切换:
    - duration_vs_amplitude: 持续时间 vs 幅值
    - frequency_vs_amplitude: 平均频率 vs 幅值
    - energy_vs_counts: 能量 vs 振铃计数
    """

    DIMENSIONS: Dict[str, tuple] = {
        "duration_vs_amplitude": ("幅值 (mV)", "持续时间 (us)"),
        "frequency_vs_amplitude": ("幅值 (mV)", "平均频率 (kHz)"),
        "energy_vs_counts": ("振铃计数", "MARSE 能量"),
    }

    # 幅值三级颜色（使用 DT 令牌）
    AMP_COLORS = {
        "low": QColor(DT.C.STATUS_SUCCESS),
        "mid": QColor(DT.C.STATUS_WARNING),
        "high": QColor(DT.C.STATUS_ERROR),
    }

    def __init__(self, title: str = "AE 特征散点图", parent: QWidget = None):
        super().__init__(parent)
        self._title = title
        self._records: List[dict] = []
        self._max_records = 2000

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._build_header(layout)
        self._build_plot(layout)

    def _build_header(self, layout: QVBoxLayout) -> None:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        title = QLabel(self._title)
        title.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 12px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()

        self._dim_combo = QComboBox()
        self._dim_combo.addItems(list(self.DIMENSIONS.keys()))
        self._dim_combo.setStyleSheet(
            f"""
            QComboBox {{
                background: {DT.C.BG_PRIMARY}; border: 1px solid {DT.C.BORDER_DEFAULT};
                border-radius: 4px; padding: 2px 8px; font-size: 11px;
                color: {DT.C.TEXT_PRIMARY}; min-width: 120px;
            }}
        """
        )
        self._dim_combo.currentTextChanged.connect(self._on_dimension_changed)
        header.addWidget(self._dim_combo)

        layout.addLayout(header)

    def _build_plot(self, layout: QVBoxLayout) -> None:
        if not HAS_PYQTGRAPH:
            placeholder = QLabel("散点图需要 pyqtgraph")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
            layout.addWidget(placeholder, 1)
            return

        frame = QFrame()
        frame.setObjectName("scatterFrame")
        frame.setStyleSheet(
            f"""
            QFrame#scatterFrame {{
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
        self._plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self._plot_widget.getViewBox().setMouseEnabled(True, True)
        self._plot_widget.getViewBox().enableAutoRange(axis="xy")

        self._scatter = pg.ScatterPlotItem(size=6, pen=None)
        self._plot_widget.addItem(self._scatter)

        fl.addWidget(self._plot_widget)
        layout.addWidget(frame, 1)

    # ── 数据接口 ─────────────────────────────────────

    def add_point(self, features: dict) -> None:
        """追加一个 AE hit 数据点"""
        self._records.append(dict(features))
        if len(self._records) > self._max_records:
            self._records.pop(0)
        self._refresh()

    def set_data(self, records: List[dict]) -> None:
        """批量设置数据"""
        self._records = records[-self._max_records :]
        self._refresh()

    def clear_data(self) -> None:
        """清空数据"""
        self._records.clear()
        self._refresh()

    # ── 内部 ─────────────────────────────────────────

    def _on_dimension_changed(self, dim_name: str) -> None:
        """切换维度时刷新"""
        self._update_axis_labels(dim_name)
        self._refresh()

    def _get_dimension_values(self, dim_name: str) -> tuple:
        """根据维度名称从记录中提取 X/Y 值"""
        xs, ys = [], []
        key_map = {
            "duration_vs_amplitude": ("amplitude", "duration_us"),
            "frequency_vs_amplitude": ("amplitude", "avg_frequency_khz"),
            "energy_vs_counts": ("counts", "marse_energy"),
        }
        x_key, y_key = key_map.get(dim_name, ("amplitude", "duration_us"))
        for r in self._records:
            x = r.get(x_key)
            y = r.get(y_key)
            if x is not None and y is not None and x > 0 and y > 0:
                xs.append(float(x))
                ys.append(float(y))
        return np.array(xs), np.array(ys)

    def _get_amplitude_colors(self) -> List[QColor]:
        """根据幅值获取颜色列表"""
        amps = [r.get("amplitude", 0) or 0 for r in self._records]
        if not amps:
            return []
        max_amp = max(amps)
        if max_amp <= 0:
            max_amp = 1
        colors = []
        for a in amps:
            ratio = a / max_amp
            if ratio < 0.3:
                colors.append(self.AMP_COLORS["low"])
            elif ratio < 0.7:
                colors.append(self.AMP_COLORS["mid"])
            else:
                colors.append(self.AMP_COLORS["high"])
        return colors

    def _refresh(self) -> None:
        """刷新散点图"""
        if not HAS_PYQTGRAPH:
            return

        dim_name = self._dim_combo.currentText()
        xs, ys = self._get_dimension_values(dim_name)
        if len(xs) == 0 or len(ys) == 0:
            self._scatter.setData([], [])
            return

        colors = self._get_amplitude_colors()
        # 确保颜色数量匹配
        while len(colors) < len(xs):
            colors.append(self.AMP_COLORS["low"])
        colors = colors[: len(xs)]

        self._scatter.setData(
            xs,
            ys,
            brush=[pg.mkBrush(c) for c in colors],
            size=6,
            pen=None,
        )

    def _update_axis_labels(self, dim_name: str) -> None:
        """更新坐标轴标签"""
        if not HAS_PYQTGRAPH:
            return
        labels = self.DIMENSIONS.get(dim_name, ("", ""))
        self._plot_widget.setLabel("bottom", labels[0])
        self._plot_widget.setLabel("left", labels[1])

    @property
    def record_count(self) -> int:
        return len(self._records)
