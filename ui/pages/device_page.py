# -*- coding: utf-8 -*-
"""设备管理页面 - 完整实现设备 CRUD、通道配置、参数设置"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import DT
from ui.widgets import GhostButton, PrimaryButton, SecondaryButton


class DevicePage(QWidget):
    """设备管理 - 设备列表 + 通道配置 + 参数设置"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("devicePage")
        self._db_manager = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(DT.S.XL, DT.S.LG, DT.S.XL, DT.S.LG)
        layout.setSpacing(DT.S.MD)

        self._build_header(layout)
        self._build_content(layout)

        # 设备内存存储
        self._devices: dict = {}
        self._selected_device_id: str = None

        # 添加示例设备
        self.add_sample_devices()

    def set_db_manager(self, db_manager) -> None:
        """设置数据库管理器"""
        self._db_manager = db_manager

    # ── 标题 ─────────────────────────────────────────

    def _build_header(self, layout: QVBoxLayout) -> None:
        h = QHBoxLayout()
        title = QLabel("设备管理")
        title.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        h.addWidget(title)
        h.addStretch()

        # 统计
        self._ch_info = QLabel("")
        self._ch_info.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px;")
        h.addWidget(self._ch_info)

        layout.addLayout(h)

    # ── 内容 ─────────────────────────────────────────

    def _build_content(self, layout: QVBoxLayout) -> None:
        main = QHBoxLayout()

        # ── 左侧：设备列表 ──
        left = QFrame()
        left.setObjectName("cardContainer")
        left.setStyleSheet(DT.sheet.sheet_card())
        ll = QVBoxLayout(left)
        ll.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        dev_label = QLabel("设备列表")
        dev_label.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        dev_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        ll.addWidget(dev_label)

        self._device_table = QTableWidget()
        self._device_table.setColumnCount(3)
        self._device_table.setHorizontalHeaderLabels(["设备编号", "设备名称", "状态"])
        self._device_table.setAlternatingRowColors(True)
        self._device_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._device_table.setStyleSheet(DT.sheet.sheet_table())
        self._device_table.horizontalHeader().setStretchLastSection(True)
        self._device_table.itemSelectionChanged.connect(self._on_device_selected)
        ll.addWidget(self._device_table)

        # 设备操作按钮
        btn_layout = QHBoxLayout()
        self._btn_add = PrimaryButton("添加设备")
        self._btn_add.clicked.connect(self._on_add_device)
        btn_layout.addWidget(self._btn_add)

        self._btn_delete = GhostButton("删除设备")
        self._btn_delete.setStyleSheet(f"color: {DT.C.STATUS_ERROR}; font-size: {DT.FS.BODY_SMALL}px;")
        self._btn_delete.clicked.connect(self._on_delete_device)
        btn_layout.addWidget(self._btn_delete)

        btn_layout.addStretch()
        ll.addLayout(btn_layout)

        main.addWidget(left, 1)  # 设备列表左侧 1 份空间

        # ── 右侧：设备参数 + 通道配置 ──
        right = QFrame()
        right.setObjectName("cardContainer")
        right.setStyleSheet(DT.sheet.sheet_card())
        rl = QVBoxLayout(right)
        rl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        param_label = QLabel("设备参数")
        param_label.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        param_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        rl.addWidget(param_label)

        # 参数输入区
        form = QFrame()
        form.setFrameShape(QFrame.Shape.StyledPanel)
        form.setStyleSheet(f"background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px;")
        fl = QVBoxLayout(form)

        # 创建输入控件（存储 widget 引用而非 layout）
        self._inp_name, lay = self._create_input("设备名称:", "输入设备名称")
        fl.addLayout(lay)
        self._inp_host, lay = self._create_input("主机地址:", "192.168.1.100")
        fl.addLayout(lay)
        self._inp_tcp, lay = self._create_spin("TCP 端口:", 502, 1, 65535)
        fl.addLayout(lay)
        self._inp_udp, lay = self._create_spin("UDP 端口:", 6000, 1, 65535)
        fl.addLayout(lay)
        self._inp_sample_rate, lay = self._create_spin("采样率 (MHz):", 100, 1, 500)
        fl.addLayout(lay)
        self._inp_channels, lay = self._create_spin("通道数:", 4, 1, 16)
        fl.addLayout(lay)

        rl.addWidget(form)

        # 通道配置表
        ch_label = QLabel("通道配置")
        ch_label.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        ch_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        rl.addWidget(ch_label)

        self._ch_table = QTableWidget()
        self._ch_table.setColumnCount(5)
        self._ch_table.setHorizontalHeaderLabels(["编号", "名称", "耦合类型", "增益 (dB)", "状态"])
        self._ch_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._ch_table.setAlternatingRowColors(True)
        self._ch_table.setStyleSheet(DT.sheet.sheet_table())
        self._ch_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._ch_table.setColumnWidth(0, 60)
        self._ch_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._ch_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._ch_table.setColumnWidth(2, 120)
        self._ch_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._ch_table.setColumnWidth(3, 80)
        self._ch_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        rl.addWidget(self._ch_table)

        # 通道操作按钮
        ch_btn_layout = QHBoxLayout()
        self._btn_scan = SecondaryButton("扫描设备")
        self._btn_scan.clicked.connect(self._on_scan)
        ch_btn_layout.addWidget(self._btn_scan)
        ch_btn_layout.addStretch()
        self._btn_save = PrimaryButton("保存参数")
        self._btn_save.clicked.connect(self._on_save_params)
        ch_btn_layout.addWidget(self._btn_save)
        rl.addLayout(ch_btn_layout)

        main.addWidget(right, 1)
        layout.addLayout(main, 1)

    # ── 辅助 ─────────────────────────────────────────

    def _create_input(self, label_text: str, placeholder: str):
        """创建带标签的输入框，返回 (QLineEdit, QHBoxLayout)"""
        h = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: {DT.FS.BODY_SMALL}px; font-weight: 500;")
        h.addWidget(lbl)
        inp = QLineEdit()
        inp.setPlaceholderText(placeholder)
        inp.setStyleSheet(DT.sheet.sheet_input())
        h.addWidget(inp, 1)
        return inp, h

    def _create_spin(self, label_text: str, default: int, min_val: int, max_val: int):
        """创建带标签的 SpinBox，返回 (QSpinBox, QHBoxLayout)"""
        h = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: {DT.FS.BODY_SMALL}px; font-weight: 500;")
        h.addWidget(lbl)
        sp = QSpinBox()
        sp.setRange(min_val, max_val)
        sp.setValue(default)
        sp.setStyleSheet(DT.sheet.sheet_spin())
        h.addWidget(sp, 0)
        return sp, h

    # ── 设备操作 ─────────────────────────────────────

    def _on_device_selected(self) -> None:
        """设备选择变更"""
        selected = self._device_table.selectedItems()
        if selected:
            row = selected[0].row()
            self._selected_device_id = self._device_table.item(row, 0).text()
            dev = self._devices.get(self._selected_device_id, {})
            self._inp_name.setText(dev.get("name", ""))
            self._inp_host.setText(dev.get("host", ""))
            self._inp_tcp.setValue(dev.get("tcp_port", 502))
            self._inp_udp.setValue(dev.get("udp_port", 6000))
            self._inp_sample_rate.setValue(dev.get("sample_rate", 100))
            self._inp_channels.setValue(dev.get("channel_count", 4))
            self._update_channel_table(dev.get("channel_count", 4))
        else:
            self._selected_device_id = None

    def add_sample_devices(self) -> None:
        """添加示例设备"""
        sample_devices = [
            {"id": "DEV-001", "name": "UHF-超高频采集-01", "host": "192.168.1.101", "tcp_port": 502, "udp_port": 6000, "sample_rate": 100, "channel_count": 4, "status": 1},
            {"id": "DEV-002", "name": "UHF-超高频采集-02", "host": "192.168.1.102", "tcp_port": 502, "udp_port": 6001, "sample_rate": 100, "channel_count": 4, "status": 0},
            {"id": "DEV-003", "name": "UHF-超高频采集-03", "host": "192.168.1.103", "tcp_port": 502, "udp_port": 6002, "sample_rate": 100, "channel_count": 4, "status": 0},
            {"id": "AE001", "name": "AE-超声波采集-01", "host": "192.168.1.104", "tcp_port": 502, "udp_port": 6003, "sample_rate": 2, "channel_count": 4, "status": 0},
        ]
        for dev in sample_devices:
            self.add_device(dev)

    def add_device(self, dev: dict) -> None:
        """添加设备到列表"""
        dev_id = dev.get("id", f"DEV_{len(self._devices)+1:03d}")
        self._devices[dev_id] = dev

        row = self._device_table.rowCount()
        self._device_table.insertRow(row)
        self._device_table.setItem(row, 0, QTableWidgetItem(dev_id))
        self._device_table.setItem(row, 1, QTableWidgetItem(dev.get("name", "")))

        status_map = {0: "离线", 1: "在线", 2: "告警", 3: "错误"}
        status_text = status_map.get(dev.get("status", 0), "未知")
        status_item = QTableWidgetItem(status_text)
        status_color = {0: DT.C.TEXT_TERTIARY, 1: DT.C.STATUS_SUCCESS, 2: DT.C.STATUS_WARNING, 3: DT.C.STATUS_ERROR}
        status_item.setForeground(QColor(status_color.get(dev.get("status", 0), DT.C.TEXT_TERTIARY)))
        self._device_table.setItem(row, 2, status_item)

    def _on_add_device(self) -> None:
        """添加新设备"""
        name = self._inp_name.text().strip()
        if not name:
            self._inp_name.setPlaceholderText("请输入设备名称!")
            return
        new_id = f"DEV_{len(self._devices) + 1:03d}"
        self.add_device(
            {
                "id": new_id,
                "name": name,
                "host": self._inp_host.text(),
                "tcp_port": self._inp_tcp.value(),
                "udp_port": self._inp_udp.value(),
                "sample_rate": self._inp_sample_rate.value(),
                "channel_count": self._inp_channels.value(),
                "status": 0,
            }
        )

    def _on_delete_device(self) -> None:
        """删除选中设备"""
        if not self._selected_device_id:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除设备 {self._selected_device_id} 吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        row = self._device_table.currentRow()
        if row >= 0:
            self._device_table.removeRow(row)
        self._devices.pop(self._selected_device_id, None)
        self._selected_device_id = None
        self._inp_name.setText("")
        self._inp_host.setText("")
        self._inp_tcp.setValue(502)
        self._inp_udp.setValue(6000)
        self._inp_sample_rate.setValue(100)
        self._inp_channels.setValue(4)

    def _update_channel_table(self, n_channels: int) -> None:
        """更新通道表"""
        self._ch_table.setRowCount(0)
        coupling_options = ["UHF", "AE", "HFCT", "TEV"]
        for i in range(min(n_channels, 16)):
            row = self._ch_table.rowCount()
            self._ch_table.insertRow(row)
            self._ch_table.setItem(row, 0, QTableWidgetItem(f"CH-{i+1:02d}"))
            self._ch_table.setItem(row, 1, QTableWidgetItem(f"通道 {i+1}"))
            # 耦合类型使用 QComboBox 可编辑
            coupling_combo = QComboBox()
            coupling_combo.addItems(coupling_options)
            coupling_combo.setCurrentIndex(i % len(coupling_options))
            coupling_combo.setStyleSheet(
                f"QComboBox {{ background: {DT.C.BG_PRIMARY}; border: 1px solid {DT.C.BORDER_DEFAULT}; "
                f"border-radius: 3px; padding: 2px 4px; font-size: {DT.FS.CAPTION}px; color: {DT.C.TEXT_PRIMARY}; }}"
            )
            self._ch_table.setCellWidget(row, 2, coupling_combo)
            self._ch_table.setItem(row, 3, QTableWidgetItem(f"{20 + i * 2}"))
            status_item = QTableWidgetItem("正常")
            status_item.setForeground(QColor(DT.C.STATUS_SUCCESS))
            self._ch_table.setItem(row, 4, status_item)
        self._ch_info.setText(f"共 {n_channels} 个通道 | 已配置 {min(n_channels, 16)} 个")

    def _on_save_params(self) -> None:
        """保存设备参数（内存 + 数据库持久化）"""
        if not self._selected_device_id:
            return
        dev = self._devices.get(self._selected_device_id)
        if not dev:
            return

        dev_id = self._selected_device_id
        dev["name"] = self._inp_name.text()
        dev["host"] = self._inp_host.text()
        dev["tcp_port"] = self._inp_tcp.value()
        dev["udp_port"] = self._inp_udp.value()
        dev["sample_rate"] = self._inp_sample_rate.value()
        dev["channel_count"] = self._inp_channels.value()

        # 读取通道耦合类型（从表格 QComboBox widget）
        channels = []
        for row in range(self._ch_table.rowCount()):
            combo = self._ch_table.cellWidget(row, 2)
            coupling_text = combo.currentText() if combo and hasattr(combo, "currentText") else "UHF"
            coupling_map = {"UHF": "uhf", "AE": "ae", "HFCT": "hfct", "TEV": "tev"}
            coupling_type = coupling_map.get(coupling_text, "uhf")
            channels.append({
                "index": row,
                "name": f"通道 {row + 1}",
                "enabled": True,
                "coupling_type": coupling_type,
                "gain": 1.0,
                "frequency_min_mhz": 300 if coupling_type == "uhf" else None,
                "frequency_max_mhz": 3000 if coupling_type == "uhf" else None,
                "frequency_min_hz": 20_000 if coupling_type == "ae" else None,
                "frequency_max_hz": 200_000 if coupling_type == "ae" else None,
                "ae_sample_rate_hz": 2_000_000 if coupling_type == "ae" else None,
            })

        # 更新内存设备记录的通道类型
        dev["channels"] = channels

        # 持久化到数据库
        if self._db_manager:
            try:
                from core.data.monitor_models import PDDeviceModel
                from sqlalchemy import select

                session = self._db_manager.get_session()
                existing = session.execute(
                    select(PDDeviceModel).where(PDDeviceModel.id == dev_id)
                ).scalar_one_or_none()

                if existing:
                    existing.name = dev["name"]
                    existing.host = dev["host"]
                    existing.channel_count = dev["channel_count"]
                    existing.sample_rate_hz = dev["sample_rate"] * 1_000_000
                    existing.udp_port = dev["udp_port"]
                else:
                    existing = PDDeviceModel(
                        id=dev_id,
                        name=dev["name"],
                        host=dev["host"],
                        channel_count=dev["channel_count"],
                        sample_rate_hz=dev["sample_rate"] * 1_000_000,
                        udp_port=dev["udp_port"],
                    )
                    session.add(existing)

                session.commit()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("设备参数持久化失败: %s", e)

        import logging
        logging.getLogger(__name__).info("设备 %s 参数已保存", dev_id)

    def _on_scan(self) -> None:
        """扫描设备（占位实现）"""
        QMessageBox.information(self, "扫描设备", "设备扫描功能待实现（需要 LAN 扫描/UDP 广播发现逻辑）")

    # ── 属性 ─────────────────────────────────────────

    @property
    def device_count(self) -> int:
        return len(self._devices)

    @property
    def online_device_count(self) -> int:
        return sum(1 for d in self._devices.values() if d.get("status") == 1)
