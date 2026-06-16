# -*- coding: utf-8 -*-
"""报警管理页面 - 完整实现"""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import DT
from ui.widgets import DangerButton, PrimaryButton, SecondaryButton


class AlarmPage(QWidget):
    """报警管理 - 报警事件列表与规则配置"""

    LEVEL_COLORS = {
        "critical": DT.C.STATUS_ERROR,
        "warning": DT.C.STATUS_WARNING,
        "info": DT.C.STATUS_SUCCESS,
    }

    LEVEL_NAMES = {
        "critical": "严重",
        "warning": "一般",
        "info": "提示",
    }

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("alarmPage")
        self._alarm_service = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(DT.S.XL, DT.S.LG, DT.S.XL, DT.S.LG)
        layout.setSpacing(DT.S.MD)

        self._build_header(layout)
        self._build_stats_bar(layout)
        self._build_content(layout)

    # ── 标题 ─────────────────────────────────────────

    def _build_header(self, layout: QVBoxLayout) -> None:
        header = QHBoxLayout()
        title = QLabel("报警管理")
        title.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        header.addWidget(title)
        header.addStretch()

        layout.addLayout(header)

        subtitle = QLabel("报警事件列表 · 规则配置 · 报警确认")
        subtitle.setFont(DT.T.get_font(*DT.T.BODY[:2]))
        subtitle.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
        layout.addWidget(subtitle)

    # ── 统计栏 ───────────────────────────────────────

    def _build_stats_bar(self, layout: QVBoxLayout) -> None:
        stats_frame = QFrame()
        stats_frame.setObjectName("cardContainer")
        stats_frame.setStyleSheet(DT.sheet.sheet_card())
        stats_frame.setFixedHeight(80)
        s_layout = QHBoxLayout(stats_frame)
        s_layout.setContentsMargins(DT.S.LG, DT.S.MD, DT.S.LG, DT.S.MD)

        self._stat_labels = {}
        for key, color in [("critical", DT.C.STATUS_ERROR), ("warning", DT.C.STATUS_WARNING), ("info", DT.C.STATUS_SUCCESS)]:
            card = QFrame()
            card.setStyleSheet(
                f"""
                QFrame {{ background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px; }}
            """
            )
            c = QVBoxLayout(card)
            c.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
            c.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl = QLabel(self.LEVEL_NAMES[key])
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;")
            val = QLabel("0")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setStyleSheet(f"color: {color}; font-size: {DT.FS.DATA_MEDIUM}px; font-weight: 700;")
            c.addWidget(lbl)
            c.addWidget(val)
            s_layout.addWidget(card, 1)
            self._stat_labels[key] = val

        # 总数
        card_total = QFrame()
        card_total.setStyleSheet(f"QFrame {{ background: {DT.C.ACCENT_SUBTLE}; border-radius: {DT.R.MD}px; }}")
        c_total = QVBoxLayout(card_total)
        c_total.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
        c_total.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t_lbl = QLabel("今日总数")
        t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t_lbl.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;")
        self._total_label = QLabel("0")
        self._total_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._total_label.setStyleSheet(f"color: {DT.C.ACCENT_PRIMARY}; font-size: {DT.FS.DATA_MEDIUM}px; font-weight: 700;")
        c_total.addWidget(t_lbl)
        c_total.addWidget(self._total_label)
        s_layout.addWidget(card_total, 1)

        s_layout.addStretch(2)

        layout.addWidget(stats_frame)

    # ── 内容区 ───────────────────────────────────────

    def _build_content(self, layout: QVBoxLayout) -> None:
        content = QHBoxLayout()
        content.setSpacing(DT.S.MD)

        # 左: 报警列表
        list_frame = QFrame()
        list_frame.setObjectName("cardContainer")
        list_frame.setStyleSheet(DT.sheet.sheet_card())
        list_layout = QVBoxLayout(list_frame)
        list_layout.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        l_title = QLabel("报警事件列表")
        l_title.setFont(DT.T.get_font(*DT.T.TITLE_MEDIUM[:2], "SemiBold"))
        l_title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        l_title_row = QHBoxLayout()
        l_title_row.addWidget(l_title)
        l_title_row.addStretch()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["全部级别", "严重报警", "一般报警", "提示报警"])
        self._filter_combo.setFixedHeight(28)
        self._filter_combo.setStyleSheet(DT.sheet.sheet_combo(100))
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        l_title_row.addWidget(self._filter_combo)
        list_layout.addLayout(l_title_row)

        self._alarm_table = QTableWidget()
        self._alarm_table.setColumnCount(7)
        self._alarm_table.setHorizontalHeaderLabels(["时间", "级别", "设备", "类型", "幅值(mV)", "描述", "状态"])
        self._alarm_table.setAlternatingRowColors(True)
        self._alarm_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._alarm_table.verticalHeader().setVisible(False)
        self._alarm_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._alarm_table.setStyleSheet(DT.sheet.sheet_table())
        self._alarm_table.horizontalHeader().setStretchLastSection(True)
        self._alarm_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._alarm_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        list_layout.addWidget(self._alarm_table)

        # 按钮行
        btn_row = QHBoxLayout()
        self._btn_ack = SecondaryButton("确认选中")
        self._btn_ack.clicked.connect(self._on_acknowledge)
        btn_row.addWidget(self._btn_ack)
        self._btn_delete = SecondaryButton("删除选中")
        self._btn_delete.clicked.connect(self._on_delete_selected)
        btn_row.addWidget(self._btn_delete)
        btn_row.addWidget(QLabel("", styleSheet=f"color: {DT.C.DIVIDER}; max-width: 1px; max-height: 20px;"))
        self._clear_btn = DangerButton("清除全部")
        self._clear_btn.clicked.connect(self._on_clear_all)
        btn_row.addWidget(self._clear_btn)
        self._refresh_btn = SecondaryButton("刷新")
        self._refresh_btn.clicked.connect(self._on_refresh)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addStretch()
        list_layout.addLayout(btn_row)

        content.addWidget(list_frame, 3)

        # 右: 规则配置
        rule_frame = QFrame()
        rule_frame.setObjectName("cardContainer")
        rule_frame.setStyleSheet(DT.sheet.sheet_card())
        rule_layout = QVBoxLayout(rule_frame)
        rule_layout.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        r_title = QLabel("报警规则配置")
        r_title.setFont(DT.T.get_font(*DT.T.TITLE_MEDIUM[:2], "SemiBold"))
        r_title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        rule_layout.addWidget(r_title)

        # 阈值配置表单
        form_frame = QFrame()
        form_frame.setStyleSheet(f"background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px;")
        form = QVBoxLayout(form_frame)
        form.setContentsMargins(DT.S.MD, DT.S.MD, DT.S.MD, DT.S.MD)

        # 临界阈值
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("严重阈值(mV):"))
        self._sp_critical = QSpinBox()
        self._sp_critical.setRange(10, 1000)
        self._sp_critical.setValue(80)
        self._sp_critical.setFixedHeight(28)
        self._sp_critical.setStyleSheet(DT.sheet.sheet_spin())
        row1.addWidget(self._sp_critical)
        form.addLayout(row1)

        # 警告阈值
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("一般阈值(mV):"))
        self._sp_warning = QSpinBox()
        self._sp_warning.setRange(10, 1000)
        self._sp_warning.setValue(50)
        self._sp_warning.setFixedHeight(28)
        self._sp_warning.setStyleSheet(DT.sheet.sheet_spin())
        row2.addWidget(self._sp_warning)
        form.addLayout(row2)

        # 提示阈值
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("提示阈值(mV):"))
        self._sp_info = QSpinBox()
        self._sp_info.setRange(10, 1000)
        self._sp_info.setValue(30)
        self._sp_info.setFixedHeight(28)
        self._sp_info.setStyleSheet(DT.sheet.sheet_spin())
        row3.addWidget(self._sp_info)
        form.addLayout(row3)

        # 防抖次数
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("防抖次数:"))
        self._sp_hysteresis = QSpinBox()
        self._sp_hysteresis.setToolTip("连续超阈值次数，达到后才触发报警（防止瞬时干扰误报）")
        self._sp_hysteresis.setRange(1, 20)
        self._sp_hysteresis.setValue(3)
        self._sp_hysteresis.setFixedHeight(28)
        self._sp_hysteresis.setStyleSheet(DT.sheet.sheet_spin())
        row4.addWidget(self._sp_hysteresis)
        form.addLayout(row4)

        # 启用报警
        self._cb_alarm_enabled = QCheckBox("启用报警检测")
        self._cb_alarm_enabled.setChecked(True)
        self._cb_alarm_enabled.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY}; font-size: {DT.FS.BODY}px; spacing: 6px;")
        form.addWidget(self._cb_alarm_enabled)

        rule_layout.addWidget(form_frame)

        self._btn_apply = PrimaryButton("应用规则")
        self._btn_apply.clicked.connect(self._on_apply_rules)
        rule_layout.addWidget(self._btn_apply)

        rule_layout.addStretch()

        content.addWidget(rule_frame, 1)
        layout.addLayout(content, 1)

        # 定时刷新
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_stats)
        self._refresh_timer.start(5000)

    # ── 操作方法 ─────────────────────────────────────

    def set_alarm_service(self, service) -> None:
        self._alarm_service = service

    def add_alarm(self, alarm: dict) -> None:
        """添加报警到表格（批量调用时延迟刷新统计）"""
        row = self._alarm_table.rowCount()
        self._alarm_table.insertRow(0)

        # 时间
        ts = alarm.get("timestamp", "")
        if isinstance(ts, (int, float)):
            ts = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        self._alarm_table.setItem(0, 0, QTableWidgetItem(str(ts)))

        # 级别
        level = alarm.get("level", "info")
        level_item = QTableWidgetItem(self.LEVEL_NAMES.get(level, level))
        color = self.LEVEL_COLORS.get(level, DT.C.TEXT_TERTIARY)
        level_item.setForeground(QColor(color))
        level_item.setFont(DT.T.get_font("Segoe UI Variable", 12, "Bold"))
        self._alarm_table.setItem(0, 1, level_item)

        # 设备
        self._alarm_table.setItem(0, 2, QTableWidgetItem(alarm.get("device_id", "")))

        # 类型
        atype = alarm.get("alarm_type", "")
        type_names = {
            "pd_over_limit": "局放超限",
            "device_offline": "设备离线",
            "optical_abnormal": "光模块异常",
            "adc_abnormal": "ADC异常",
            "sync_abnormal": "同步异常",
            "storage_low": "存储不足",
        }
        self._alarm_table.setItem(0, 3, QTableWidgetItem(type_names.get(atype, atype)))

        # 幅值
        amp = alarm.get("amplitude", 0)
        self._alarm_table.setItem(0, 4, QTableWidgetItem(f"{amp:.1f}"))

        # 描述
        desc = alarm.get("description", "")
        if len(desc) > 40:
            desc = desc[:40] + "..."
        self._alarm_table.setItem(0, 5, QTableWidgetItem(desc))

        # 状态
        status_item = QTableWidgetItem("未确认")
        status_item.setForeground(QColor(DT.C.STATUS_WARNING))
        self._alarm_table.setItem(0, 6, status_item)

        # 批量模式下延迟刷新；定时器也会周期性刷新
        if not getattr(self, '_batch_mode', False):
            self._refresh_stats()

    def begin_batch(self) -> None:
        """开始批量插入模式"""
        self._batch_mode = True

    def end_batch(self) -> None:
        """结束批量插入并刷新统计"""
        self._batch_mode = False
        self._refresh_stats()

    def _on_acknowledge(self) -> None:
        """确认选中报警"""
        selected = self._alarm_table.selectedItems()
        if not selected:
            return
        rows = set()
        for item in selected:
            rows.add(item.row())
        for row in rows:
            item = self._alarm_table.item(row, 6)
            if item:
                item.setText("已确认")
                item.setForeground(QColor(DT.C.STATUS_SUCCESS))
                item.setFont(DT.T.get_font("Segoe UI Variable", 12, "Medium"))

    def _on_delete_selected(self) -> None:
        """删除选中报警"""
        selected = self._alarm_table.selectedItems()
        if not selected:
            return
        rows = set()
        for item in selected:
            rows.add(item.row())
        for row in sorted(rows, reverse=True):
            self._alarm_table.removeRow(row)

    def _on_clear_all(self) -> None:
        reply = QMessageBox.question(
            self, "确认清除", "确定要清除全部报警记录吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._alarm_table.setRowCount(0)
        if self._alarm_service:
            self._alarm_service.clear_all_alarms()
        self._refresh_stats()

    def _on_refresh(self) -> None:
        self._refresh_stats()

    def _on_filter_changed(self, idx: int) -> None:
        levels = {0: None, 1: "critical", 2: "warning", 3: "info"}
        level = levels.get(idx)
        for row in range(self._alarm_table.rowCount()):
            hide = False
            if level:
                item = self._alarm_table.item(row, 1)
                if item:
                    hide = item.text() != self.LEVEL_NAMES[level]
            self._alarm_table.setRowHidden(row, hide)

    def _on_apply_rules(self) -> None:
        """应用报警规则"""
        if self._alarm_service:
            # 将 UI 的 3 个绝对阈值传递给服务
            max_amp = float(max(self._sp_critical.value(), self._sp_warning.value(), self._sp_info.value()))
            if max_amp > 0:
                self._alarm_service.set_amplitude_thresholds(
                    critical=float(self._sp_critical.value()),
                    warning=float(self._sp_warning.value()),
                    info=float(self._sp_info.value()),
                    max_amplitude=max_amp,
                )
            self._alarm_service.set_hysteresis(self._sp_hysteresis.value())
        self._btn_apply.setText("规则已应用 ✓")

    def _refresh_stats(self) -> None:
        """刷新统计"""
        critical = 0
        warning = 0
        info = 0
        for row in range(self._alarm_table.rowCount()):
            item = self._alarm_table.item(row, 1)
            if item:
                t = item.text()
                if t == "严重":
                    critical += 1
                elif t == "一般":
                    warning += 1
                elif t == "提示":
                    info += 1

        self._stat_labels["critical"].setText(str(critical))
        self._stat_labels["warning"].setText(str(warning))
        self._stat_labels["info"].setText(str(info))
        self._total_label.setText(str(critical + warning + info))
