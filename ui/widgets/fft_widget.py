# -*- coding: utf-8 -*-
"""
FFT 频谱分析控件 - FFTWidget

基于 PyQtGraph 实现频谱显示。
特性:
- 频谱曲线显示
- 峰值检测与标注
- 频段统计区域高亮
- 对数/线性 Y 轴切换
- 峰值列表显示
- 噪声底噪指示
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import DT

pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND, background=DT.C.CHART_BACKGROUND)

# 频段颜色
BAND_COLORS = {
    "VLF": QColor(100, 150, 200, 40),
    "UHF_low": QColor(80, 200, 120, 40),
    "UHF_mid": QColor(200, 180, 60, 40),
    "UHF_high": QColor(200, 80, 80, 40),
}


class FFTWidget(QWidget):
    """FFT 频谱分析控件"""

    def __init__(self, title: str = "FFT 频谱", parent: Optional[QWidget] = None, show_peak_list: bool = True):
        super().__init__(parent)
        self._title = title
        self._log_scale = False
        self._show_peaks = True
        self._show_noise = True
        self._show_bands = False
        self._band_regions: List[pg.LinearRegionItem] = []
        self._peak_texts: List[pg.TextItem] = []
        self._show_peak_list = show_peak_list

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

        self._info_label = QLabel("")
        self._info_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 11px;")
        header.addWidget(self._info_label)

        # 显示选项
        self._cb_log = QCheckBox("对数")
        self._cb_log.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 11px; spacing: 4px;")
        self._cb_log.stateChanged.connect(self._toggle_log_scale)
        header.addWidget(self._cb_log)

        self._cb_peaks = QCheckBox("峰值")
        self._cb_peaks.setChecked(True)
        self._cb_peaks.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 11px; spacing: 4px;")
        self._cb_peaks.stateChanged.connect(self._toggle_peaks)
        header.addWidget(self._cb_peaks)

        self._cb_bands = QCheckBox("频段")
        self._cb_bands.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 11px; spacing: 4px;")
        self._cb_bands.stateChanged.connect(self._toggle_bands)
        header.addWidget(self._cb_bands)

        layout.addLayout(header)

        # 主内容：图表 + 峰值列表
        content = QHBoxLayout()
        content.setSpacing(8)

        # 频谱图
        plot_container = QVBoxLayout()
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground(DT.C.CHART_BACKGROUND)
        self._plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._plot_widget.setMinimumHeight(150)

        self._plot_widget.setLabel("left", "幅值", units="dBm")
        self._plot_widget.setLabel("bottom", "频率", units="MHz")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.2)

        # 频谱曲线
        pen = pg.mkPen(QColor(DT.C.ACCENT_PRIMARY), width=1.5)
        self._curve = self._plot_widget.plot(pen=pen, clear=True)

        # 噪声底噪线
        self._noise_line = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=pg.mkPen(QColor(DT.C.STATUS_ERROR), width=1, style=Qt.PenStyle.DashLine),
        )
        self._noise_line.setVisible(False)
        self._plot_widget.addItem(self._noise_line)

        # 噪声标签
        self._noise_text = pg.TextItem("", anchor=(1, 1))
        self._noise_text.setVisible(False)
        self._plot_widget.addItem(self._noise_text)

        plot_container.addWidget(self._plot_widget)
        content.addLayout(plot_container, 3)

        # 峰值列表（可选，默认显示在右侧；若独立显示则 monitor 页面自行创建）
        if self._show_peak_list:
            list_container = QVBoxLayout()
            list_label = QLabel("峰值列表")
            list_label.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 11px; font-weight: 600;")
            list_container.addWidget(list_label)

            self._peak_table = QTableWidget()
            self._peak_table.setColumnCount(3)
            self._peak_table.setHorizontalHeaderLabels(["频率 (MHz)", "幅值 (dBm)", "类型"])
            self._peak_table.setMinimumWidth(180)
            self._peak_table.setMaximumWidth(240)
            self._peak_table.setAlternatingRowColors(True)
            self._peak_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self._peak_table.verticalHeader().setVisible(False)
            self._peak_table.setStyleSheet(
                f"""
                QTableWidget {{
                    background: {DT.C.BG_PRIMARY};
                    border: 1px solid {DT.C.BORDER_DEFAULT};
                    border-radius: 4px;
                    font-size: 10px;
                }}
                QTableWidget::item {{ padding: 2px 4px; }}
                QHeaderView::section {{
                    background: {DT.C.BG_SECONDARY};
                    color: {DT.C.TEXT_SECONDARY};
                    border: none;
                    border-bottom: 1px solid {DT.C.BORDER_DEFAULT};
                    padding: 3px;
                    font-size: 10px;
                    font-weight: 600;
                }}
            """
            )
            list_container.addWidget(self._peak_table)

            content.addLayout(list_container, 1)
        layout.addLayout(content)

    # ── 数据更新 ─────────────────────────────────────

    def update_spectrum(
        self,
        frequencies: np.ndarray,
        magnitudes: np.ndarray,
        peaks: Optional[List[dict]] = None,
        band_stats: Optional[Dict[str, dict]] = None,
        noise_floor: float = 0.0,
        snr_db: float = 0.0,
    ) -> None:
        """
        更新频谱显示

        Args:
            frequencies: 频率数组 (Hz)
            magnitudes: 幅值数组
            peaks: 峰值列表 [{frequency, magnitude, ...}]
            band_stats: 频段统计
            noise_floor: 噪声底噪
            snr_db: 信噪比
        """
        # 转换为 MHz
        freq_mhz = frequencies / 1e6

        # 显示数据
        display_mags = magnitudes.copy()
        if self._log_scale and len(display_mags) > 0:
            display_mags = 20 * np.log10(display_mags + 1e-10)

        self._curve.setData(freq_mhz, display_mags)

        # 更新峰值列表
        if peaks:
            self._update_peak_table(peaks, freq_mhz, display_mags)

        # 更新噪声线
        if self._show_noise and noise_floor > 0:
            noise_display = 20 * np.log10(noise_floor + 1e-10) if self._log_scale else noise_floor
            self._noise_line.setValue(noise_display)
            self._noise_line.setVisible(True)
            self._noise_text.setVisible(True)
            self._noise_text.setPos(freq_mhz[-1], noise_display)
            self._noise_text.setText(f" 噪声: {noise_floor:.1f}")
            self._noise_text.setColor(QColor(DT.C.STATUS_ERROR))
        else:
            self._noise_line.setVisible(False)
            self._noise_text.setVisible(False)

        # 更新频段区域
        if band_stats and self._show_bands:
            self._update_band_regions(band_stats)

        # 更新信息
        info_parts = []
        if noise_floor > 0:
            info_parts.append(f"噪声: {noise_floor:.1f}")
        if snr_db > 0:
            info_parts.append(f"SNR: {snr_db:.1f} dB")
        if peaks:
            info_parts.append(f"峰值: {len(peaks)}")
        self._info_label.setText(" | ".join(info_parts))

    def _update_peak_table(
        self,
        peaks: List[dict],
        freq_mhz: np.ndarray,
        display_mags: np.ndarray,
    ) -> None:
        """更新峰值列表"""
        if not hasattr(self, "_peak_table"):
            return
        top_peaks = sorted(peaks, key=lambda p: p.get("magnitude", 0), reverse=True)[:10]
        self._peak_table.setRowCount(len(top_peaks))

        for row, peak in enumerate(top_peaks):
            freq = peak.get("frequency", 0) / 1e6
            mag = peak.get("magnitude", 0)
            is_harmonic = peak.get("is_harmonic", False)
            order = peak.get("harmonic_order", 0)

            freq_item = QTableWidgetItem(f"{freq:.2f}")
            mag_item = QTableWidgetItem(f"{mag:.1f}")
            type_str = f"谐波{order}" if is_harmonic and order > 1 else "基频" if is_harmonic else "峰值"
            type_item = QTableWidgetItem(type_str)

            # 高亮前 3 个
            if row == 0:
                color = QColor(DT.C.STATUS_ERROR)
            elif row < 3:
                color = QColor(DT.C.STATUS_WARNING)
            else:
                color = QColor(DT.C.TEXT_SECONDARY)
            freq_item.setForeground(color)
            mag_item.setForeground(color)
            if is_harmonic:
                type_item.setForeground(QColor(DT.C.ACCENT_PRIMARY))

            self._peak_table.setItem(row, 0, freq_item)
            self._peak_table.setItem(row, 1, mag_item)
            self._peak_table.setItem(row, 2, type_item)

        if hasattr(self, "_peak_table"):
            self._peak_table.resizeColumnsToContents()

    def _update_band_regions(self, band_stats: Dict[str, dict]) -> None:
        """更新频段区域高亮"""
        self._clear_band_regions()

        for name, stats in band_stats.items():
            f_min = stats.get("f_min", 0)
            f_max = stats.get("f_max", 0)
            color = BAND_COLORS.get(name, QColor(100, 100, 100, 30))

            region = pg.LinearRegionItem(
                values=(f_min, f_max),
                orientation=pg.LinearRegionItem.Vertical,
                brush=color,
                movable=False,
            )
            self._plot_widget.addItem(region)
            self._band_regions.append(region)

    def _clear_band_regions(self) -> None:
        """清除频段区域"""
        for region in self._band_regions:
            self._plot_widget.removeItem(region)
        self._band_regions.clear()

    # ── 切换 ─────────────────────────────────────────

    def _toggle_log_scale(self) -> None:
        self._log_scale = self._cb_log.isChecked()

    def _toggle_peaks(self) -> None:
        self._show_peaks = self._cb_peaks.isChecked()
        if hasattr(self, "_peak_table"):
            self._peak_table.setVisible(self._show_peaks)

    def _toggle_bands(self) -> None:
        self._show_bands = self._cb_bands.isChecked()
        if not self._show_bands:
            self._clear_band_regions()

    # ── 配置 ─────────────────────────────────────────

    def set_title(self, title: str) -> None:
        self._title = title
        self._title_label.setText(title)

    def clear(self) -> None:
        """清空频谱"""
        self._curve.clear()
        self._peak_table.setRowCount(0)
        self._info_label.setText("")
        self._clear_band_regions()
        self._noise_line.setVisible(False)

    @property
    def plot_widget(self) -> pg.PlotWidget:
        return self._plot_widget
