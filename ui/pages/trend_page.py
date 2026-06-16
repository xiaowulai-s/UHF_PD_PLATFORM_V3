# -*- coding: utf-8 -*-
"""趋势分析页面 - 集成 TrendChartWidget"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.design_tokens import DT
from ui.widgets import TrendChartWidget


class TrendPage(QWidget):
    """趋势分析 - 趋势曲线 (UHF + AE)"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("trendPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(DT.S.XL, DT.S.LG, DT.S.XL, DT.S.LG)
        layout.setSpacing(DT.S.MD)

        # 页面标题
        title = QLabel("趋势分析")
        title.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        layout.addWidget(title)

        subtitle = QLabel("局放次数 · 最大幅值 · 平均幅值 · 放电能量 · 噪声水平 · AE 趋势")
        subtitle.setFont(DT.T.get_font(*DT.T.BODY[:2]))
        subtitle.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
        layout.addWidget(subtitle)

        # UHF 趋势图
        uhf_frame = QFrame()
        uhf_frame.setObjectName("cardContainer")
        uhf_frame.setStyleSheet(DT.sheet.sheet_card())
        uhf_layout = QVBoxLayout(uhf_frame)
        uhf_layout.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        self._trend_chart = TrendChartWidget(title="UHF 趋势曲线")
        self._trend_chart.add_series("局放次数")
        self._trend_chart.add_series("最大幅值")
        self._trend_chart.add_series("平均幅值")
        uhf_layout.addWidget(self._trend_chart)

        layout.addWidget(uhf_frame, 1)

        # AE 趋势图
        ae_frame = QFrame()
        ae_frame.setObjectName("cardContainer")
        ae_frame.setStyleSheet(DT.sheet.sheet_card())
        ae_layout = QVBoxLayout(ae_frame)
        ae_layout.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        self._ae_trend = TrendChartWidget(title="AE 趋势曲线")
        self._ae_trend.add_series("AE Hit 速率", QColor(DT.C.ACCENT_PRIMARY))
        self._ae_trend.add_series("AE 幅值", QColor(DT.C.STATUS_WARNING))
        self._ae_trend.add_series("AE 能量", QColor(DT.C.STATUS_SUCCESS))
        ae_layout.addWidget(self._ae_trend)

        layout.addWidget(ae_frame, 1)

        # 空状态提示（有数据时自动隐藏）
        self._empty_hint = QLabel("暂无趋势数据，启动模拟器或连接 FPGA 后将自动填充")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px; padding: 20px;")
        layout.addWidget(self._empty_hint)

    @property
    def trend_chart(self) -> TrendChartWidget:
        return self._trend_chart

    @property
    def ae_trend(self) -> TrendChartWidget:
        return self._ae_trend
