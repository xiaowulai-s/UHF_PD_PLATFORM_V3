# -*- coding: utf-8 -*-
"""首页 / 仪表板 - 概览页面（不与 RealtimeMonitor 重复）"""

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget

from ui.design_tokens import DT

try:
    import pyqtgraph as pg

    pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND)
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False


class DashboardPage(QWidget):
    """首页仪表板 - 系统概览

    只展示摘要数据，详细分析请进入「实时监测」页面。
    """

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("dashboardPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(DT.S.XL, DT.S.LG, DT.S.XL, DT.S.LG)
        layout.setSpacing(DT.S.SM)

        self._build_header(layout)
        self._build_metric_cards(layout)
        self._build_mini_charts(layout)
        self._build_alarm_bar(layout)

    # ── 标题 ─────────────────────────────────────────

    def _build_header(self, layout: QVBoxLayout) -> None:
        title_row = QHBoxLayout()
        title = QLabel("系统仪表板")
        title.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        title_row.addWidget(title)
        title_row.addStretch()

        hint = QLabel("提示: 详细分析请进入「实时监测」页面")
        hint.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;")
        title_row.addWidget(hint)
        layout.addLayout(title_row)

    # ── 指标卡片 ─────────────────────────────────────

    def _build_metric_cards(self, layout: QVBoxLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        frame.setMinimumHeight(90)
        cards = QHBoxLayout(frame)
        cards.setContentsMargins(DT.S.LG, DT.S.MD, DT.S.LG, DT.S.MD)

        metrics = [
            ("在线设备", "0 台", DT.C.DEVICE_ONLINE),
            ("局放总数", "0 次", DT.C.ACCENT_PRIMARY),
            ("报警总数", "0", DT.C.STATUS_ERROR),
            ("今日最大幅值", "0.0 mV", DT.C.STATUS_WARNING),
            ("AE Hits", "0", DT.C.ACCENT_SECONDARY),
        ]
        self._metric_labels = {}
        for name, value, color in metrics:
            card = QFrame()
            card.setStyleSheet(f"QFrame {{ background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px; }}")
            c = QVBoxLayout(card)
            c.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
            c.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # 标题在上，数值在下，横向居中
            title_lbl = QLabel(
                name,
                alignment=Qt.AlignmentFlag.AlignCenter,
                styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;",
            )
            c.addWidget(title_lbl)
            lbl = QLabel(value)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {color}; font-size: {DT.FS.DATA_MEDIUM}px; font-weight: 700;")
            c.addWidget(lbl)
            self._metric_labels[name] = lbl
            cards.addWidget(card, 1)

        layout.addWidget(frame)

    def update_metric(self, name: str, value: str) -> None:
        """更新指标值"""
        if name in self._metric_labels:
            self._metric_labels[name].setText(value)

    # ── 迷你图表缩略图 ───────────────────────────────

    def _build_mini_charts(self, layout: QVBoxLayout) -> None:
        if not HAS_PYQTGRAPH:
            placeholder = QLabel("图表控件未加载（需要 pyqtgraph）")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
            layout.addWidget(placeholder, 1)
            return

        row = QHBoxLayout()
        row.setSpacing(DT.S.SM)

        # 左: 波形快照 (约 60% 宽度)
        self._mini_wave = self._make_mini_plot("波形快照", row, 3)

        # 右: PRPD + FFT 上下排列 (约 40% 宽度)
        right_col = QVBoxLayout()
        right_col.setSpacing(DT.S.SM)
        self._mini_prpd = self._make_mini_image("PRPD 快照", right_col, 1)
        self._mini_fft = self._make_mini_plot("FFT 快照", right_col, 1)
        # 将右列包装到一个 frame 中，确保填充父容器高度
        right_frame = QFrame()
        right_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_frame.setLayout(right_col)
        row.addWidget(right_frame, 2)

        layout.addLayout(row, 3)

    def _make_mini_plot(self, title: str, parent: QLayout, stretch: int) -> pg.PlotWidget:
        """创建迷你曲线图（无交互控件）"""
        return self._make_mini_card(title, parent, stretch, kind="plot")

    def _make_mini_image(self, title: str, parent: QLayout, stretch: int) -> pg.PlotWidget:
        """创建迷你热力图（无交互控件）"""
        return self._make_mini_card(title, parent, stretch, kind="image")

    def _make_mini_card(self, title: str, parent: QLayout, stretch: int, kind: str = "plot") -> pg.PlotWidget:
        """通用迷你卡片工厂 (plot/image)"""
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px; font-weight: 600;")
        fl.addWidget(lbl)

        pw = pg.PlotWidget()
        pw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pw.setLabel("left", "", units="")
        pw.setLabel("bottom", "", units="")
        pw.getAxis("left").setStyle(showValues=False)
        pw.getAxis("bottom").setStyle(showValues=False)
        vb = pw.getViewBox()
        vb.setMouseEnabled(False, False)
        if kind == "plot":
            pw.showGrid(x=True, y=True, alpha=0.15)
            curve = pw.plot(pen=pg.mkPen(QColor(DT.C.ACCENT_PRIMARY), width=1))
            self._mini_curves = getattr(self, "_mini_curves", {})
            self._mini_curves[title] = curve
        else:
            img = pg.ImageItem()
            pw.addItem(img)
            cmap = pg.colormap.get(DT.C.CHART_COLORMAP)
            if cmap:
                img.setColorMap(cmap)
            img.setImage(np.zeros((36, 32)))
            self._mini_images = getattr(self, "_mini_images", {})
            self._mini_images[title] = img

        fl.addWidget(pw)
        parent.addWidget(frame, stretch)
        return pw

    # ── 公开更新方法（供控制器调用） ──────────────────

    def update_waveform_snapshot(self, data: np.ndarray, sample_rate: int = 100_000_000) -> None:
        """更新波形快照"""
        if not HAS_PYQTGRAPH:
            return
        curves = getattr(self, "_mini_curves", {})
        curve = curves.get("波形快照")
        if curve is None:
            return
        n = min(len(data), 2048)
        dt_us = 1_000_000 / sample_rate
        t = np.arange(n) * dt_us
        curve.setData(t[:n], data[:n])

    def update_prpd_snapshot(self, matrix: np.ndarray) -> None:
        """更新 PRPD 快照"""
        if not HAS_PYQTGRAPH:
            return
        images = getattr(self, "_mini_images", {})
        img = images.get("PRPD 快照")
        if img is None:
            return
        img.setImage(matrix.T.copy(), autoLevels=True)

    def update_fft_snapshot(self, frequencies: np.ndarray, magnitudes: np.ndarray) -> None:
        """更新 FFT 快照"""
        if not HAS_PYQTGRAPH:
            return
        curves = getattr(self, "_mini_curves", {})
        curve = curves.get("FFT 快照")
        if curve is None:
            return
        freq_mhz = frequencies / 1e6
        # 降采样到 2048 点
        step = max(1, len(freq_mhz) // 2048)
        curve.setData(freq_mhz[::step], magnitudes[::step])

    # ── 报警栏 ───────────────────────────────────────

    def _build_alarm_bar(self, layout: QVBoxLayout) -> None:
        alarm_frame = QFrame()
        alarm_frame.setObjectName("cardContainer")
        alarm_frame.setStyleSheet(DT.sheet.sheet_card())
        alarm_frame.setMinimumHeight(42)
        alarm_layout = QHBoxLayout(alarm_frame)
        alarm_layout.setContentsMargins(DT.S.LG, DT.S.SM, DT.S.LG, DT.S.SM)

        icon = QLabel("⚠")
        icon.setStyleSheet(f"color: {DT.C.STATUS_WARNING}; font-size: {DT.FS.BODY_LARGE}px;")
        alarm_layout.addWidget(icon)

        self._alarm_label = QLabel("暂无报警信息")
        self._alarm_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px;")
        alarm_layout.addWidget(self._alarm_label, 1)

        layout.addWidget(alarm_frame)

    def set_latest_alarm(self, text: str) -> None:
        """设置最新报警文字"""
        self._alarm_label.setText(text)
