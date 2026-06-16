# -*- coding: utf-8 -*-
"""
# TODO: 小窗口适配 - 声源定位图在1024px下过小
AE 特征分析页面

集中展示 AE hit 的特征散点图、趋势图、统计摘要和历史记录。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import DT
from ui.widgets import AELocalizationWidget, TrendChartWidget
from ui.widgets.ae_scatter_widget import AEScatterWidget

try:
    import pyqtgraph as pg

    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False


class AEAnalysisPage(QWidget):
    """AE 特征分析页面"""

    TIME_RANGES = ["1h", "24h", "7d", "30d"]

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("aeAnalysisPage")

        self._hit_history: deque = deque(maxlen=5000)
        self._current_device: Optional[str] = None
        self._current_channel: Optional[int] = None
        self._db_manager: Any = None
        self._localizer: Any = None  # AELocalizer 实例

        layout = QVBoxLayout(self)
        layout.setContentsMargins(DT.S.XL, DT.S.LG, DT.S.XL, DT.S.LG)
        layout.setSpacing(DT.S.MD)

        self._build_header(layout)
        self._build_charts(layout)
        self._build_stats(layout)
        self._build_history_table(layout)

    def set_db_manager(self, db_manager: Any) -> None:
        """设置数据库管理器"""
        self._db_manager = db_manager

    # ── 构建 ─────────────────────────────────────────

    def _build_header(self, layout: QVBoxLayout) -> None:
        header = QHBoxLayout()

        title = QLabel("AE 特征分析")
        title.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        header.addWidget(title)
        header.addStretch()

        # 通道选择
        header.addWidget(QLabel("通道:", styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px;"))
        self._ch_combo = QComboBox()
        self._ch_combo.setStyleSheet(DT.sheet.sheet_combo())
        self._ch_combo.addItems(["全部", "CH-01", "CH-02", "CH-03", "CH-04"])
        header.addWidget(self._ch_combo)

        # 时间范围按钮组
        self._range_group = QButtonGroup(self)
        self._range_group.setExclusive(True)
        self._range_buttons = {}
        for i, r in enumerate(self.TIME_RANGES):
            btn = QPushButton(r)
            btn.setCheckable(True)
            btn.setChecked(r == "1h")
            btn.setStyleSheet(self._range_btn_style(r == "1h"))
            self._range_group.addButton(btn, i)
            header.addWidget(btn)
            self._range_buttons[r] = btn
        self._range_group.buttonClicked.connect(
            lambda btn: self._on_range_changed(btn.text())
        )

        self._current_range = "1h"
        layout.addLayout(header)

    def _build_charts(self, layout: QVBoxLayout) -> None:
        charts = QHBoxLayout()
        charts.setSpacing(DT.S.MD)

        # 左: 特征散点图
        scatter_frame = QFrame()
        scatter_frame.setObjectName("cardContainer")
        scatter_frame.setStyleSheet(DT.sheet.sheet_card())
        sl = QVBoxLayout(scatter_frame)
        sl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
        self._scatter = AEScatterWidget()
        sl.addWidget(self._scatter)
        scatter_frame.setMinimumWidth(280)
        charts.addWidget(scatter_frame, 2)

        # 中: AE 趋势图
        trend_frame = QFrame()
        trend_frame.setObjectName("cardContainer")
        trend_frame.setStyleSheet(DT.sheet.sheet_card())
        tl = QVBoxLayout(trend_frame)
        tl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
        self._trend = TrendChartWidget(title="AE 趋势")
        self._trend.add_series("Hit 速率", QColor(DT.C.ACCENT_PRIMARY))
        self._trend.add_series("幅值", QColor(DT.C.STATUS_WARNING))
        self._trend.add_series("能量", QColor(DT.C.STATUS_SUCCESS))
        tl.addWidget(self._trend)
        trend_frame.setMinimumWidth(250)
        charts.addWidget(trend_frame, 2)

        # 右: AE 声源定位图
        loc_frame = QFrame()
        loc_frame.setObjectName("cardContainer")
        loc_frame.setStyleSheet(DT.sheet.sheet_card())
        ll = QVBoxLayout(loc_frame)
        ll.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
        self._localization = AELocalizationWidget(title="TDOA 声源定位")
        ll.addWidget(self._localization)
        loc_frame.setMinimumWidth(200)
        loc_frame.setMaximumWidth(320)  # 正方形定位图无需过大宽度
        charts.addWidget(loc_frame, 1)

        layout.addLayout(charts, 2)

    def _build_stats(self, layout: QVBoxLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        frame.setFixedHeight(72)
        cards = QHBoxLayout(frame)
        cards.setContentsMargins(DT.S.LG, DT.S.SM, DT.S.LG, DT.S.SM)

        stats = [
            ("总 Hits", "0", DT.C.ACCENT_PRIMARY),
            ("当前速率", "0/s", DT.C.STATUS_SUCCESS),
            ("平均幅值", "0.0 mV", DT.C.STATUS_WARNING),
            ("平均持续时间", "0.0 us", DT.C.TEXT_SECONDARY),
        ]
        self._stat_labels = {}
        for name, default, color in stats:
            card = QFrame()
            card.setStyleSheet(f"QFrame {{ background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px; }}")
            c = QVBoxLayout(card)
            c.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
            c.setAlignment(Qt.AlignmentFlag.AlignCenter)

            title = QLabel(name, alignment=Qt.AlignmentFlag.AlignCenter,
                           styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;")
            c.addWidget(title)

            lbl = QLabel(default)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {color}; font-size: {DT.FS.DATA}px; font-weight: 700;")
            c.addWidget(lbl)
            self._stat_labels[name] = lbl
            cards.addWidget(card, 1)

        layout.addWidget(frame)

    def _build_history_table(self, layout: QVBoxLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        title = QLabel("Hit 历史记录")
        title.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        fl.addWidget(title)

        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["时间", "幅值 (mV)", "上升 (us)", "持续 (us)", "计数", "能量", "频率 (kHz)"]
        )
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setStyleSheet(DT.sheet.sheet_table())
        self._table.horizontalHeader().setStretchLastSection(True)
        fl.addWidget(self._table)

        # 空状态提示
        self._ae_empty_hint = QLabel("暂无 Hit 数据，连接 AE 设备或启动模拟器后将自动填充")
        self._ae_empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ae_empty_hint.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px; padding: 16px;")
        fl.addWidget(self._ae_empty_hint)

        layout.addWidget(frame, 1)

    # ── 公开接口 ─────────────────────────────────────

    def new_hit(self, features: dict) -> None:
        """
        追加实时 AE hit 数据

        Args:
            features: AEParameterExtractor.extract() 返回的特征字典
        """
        self._hit_history.append(features)

        # 散点图
        self._scatter.add_point(features)

        # 趋势图
        now = datetime.now()
        self._trend.update_series("Hit 速率", now, features.get("counts", 0))
        self._trend.update_series("幅值", now, features.get("amplitude", 0))
        self._trend.update_series("能量", now, features.get("marse_energy", 0))

        # 统计摘要
        self._update_stats()

        # 历史表 (每 5 个 hit 刷新一次减少开销)
        if len(self._hit_history) % 5 == 0:
            self._refresh_table()

    def load_history(self, records: List[dict]) -> None:
        """
        批量加载历史 AE 事件

        Args:
            records: query_events() 返回的字典列表
        """
        for r in records:
            self._hit_history.append(r)
        self._scatter.set_data(list(self._hit_history))
        self._update_stats()
        self._refresh_table()

    def set_channel(self, device_id: Optional[str], channel_id: Optional[int]) -> None:
        """切换当前通道"""
        self._current_device = device_id
        self._current_channel = channel_id

    def set_localizer(self, localizer: Any) -> None:
        """
        设置 AELocalizer 实例

        Args:
            localizer: AELocalizer 实例
        """
        self._localizer = localizer
        if localizer and hasattr(localizer, "sensor_positions"):
            self._localization.set_sensor_positions(localizer.sensor_positions)

    def update_localization(self, arrival_times: Dict[int, float]) -> None:
        """
        执行 TDOA 定位并更新 UI

        Args:
            arrival_times: {sensor_id: 到达时间 (秒)}
        """
        if self._localizer is None:
            return

        try:
            result = self._localizer.locate(arrival_times)
            if result is not None:
                self._localization.update_localization({
                    "x": result.x,
                    "y": result.y,
                    "residual": result.residual,
                    "confidence": result.confidence,
                    "sensors_used": result.sensors_used,
                })
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("TDOA 定位计算异常: %s", e)

    # ── 内部 ─────────────────────────────────────────

    def _on_range_changed(self, period: str) -> None:
        self._current_range = period
        for p, btn in self._range_buttons.items():
            btn.setChecked(p == period)
            btn.setStyleSheet(self._range_btn_style(p == period))

    def _update_stats(self) -> None:
        """更新统计摘要卡片"""
        n = len(self._hit_history)
        self._stat_labels["总 Hits"].setText(str(n))

        if n == 0:
            self._stat_labels["当前速率"].setText("0/s")
            self._stat_labels["平均幅值"].setText("0.0 mV")
            self._stat_labels["平均持续时间"].setText("0.0 us")
            return

        amps = [r.get("amplitude", 0) or 0 for r in self._hit_history]
        durs = [r.get("duration_us", 0) or 0 for r in self._hit_history]

        avg_amp = sum(amps) / n
        avg_dur = sum(durs) / n

        self._stat_labels["平均幅值"].setText(f"{avg_amp:.1f} mV")
        self._stat_labels["平均持续时间"].setText(f"{avg_dur:.1f} us")

    def _refresh_table(self) -> None:
        """刷新历史表"""
        shown = list(self._hit_history)[-100:]
        self._table.setRowCount(len(shown))

        for row, h in enumerate(shown):
            items = [
                datetime.now().strftime("%H:%M:%S"),
                f'{h.get("amplitude", 0):.1f}',
                f'{h.get("rise_time_us", 0):.1f}',
                f'{h.get("duration_us", 0):.1f}',
                str(h.get("counts", 0)),
                f'{h.get("marse_energy", 0):.2e}',
                f'{h.get("avg_frequency_khz", 0):.1f}',
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                if col == 1:
                    amp = h.get("amplitude", 0)
                    if amp > 30:
                        item.setForeground(QColor(DT.C.STATUS_ERROR))
                    elif amp > 10:
                        item.setForeground(QColor(DT.C.STATUS_WARNING))
                    else:
                        item.setForeground(QColor(DT.C.STATUS_SUCCESS))
                self._table.setItem(row, col, item)

        self._table.resizeColumnsToContents()
        self._ae_empty_hint.setVisible(len(shown) == 0)

    # ── 样式 ─────────────────────────────────────────

    def _range_btn_style(self, active: bool) -> str:
        """时间范围按钮样式（委托给共享 ButtonStyles）"""
        from ui.widgets import ButtonStyles
        return ButtonStyles.range_selector(active)
