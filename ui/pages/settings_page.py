# -*- coding: utf-8 -*-
"""系统设置页面 - 完整实现"""

import json
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import DT
from ui.widgets import PrimaryButton, SecondaryButton


class SettingsPage(QWidget):
    """系统设置 - 网络/数据库/报警/显示 配置"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._config_path = self._resolve_config_path()
        self._config: dict = {}

        self._load_config()

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}")

        container = QWidget()
        container.setStyleSheet(f"background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(DT.S.XL, DT.S.LG, DT.S.XL, DT.S.LG)
        layout.setSpacing(DT.S.MD)

        self._build_header(layout)
        self._build_network_section(layout)
        self._build_database_section(layout)
        self._build_display_section(layout)
        self._build_save_section(layout)

        scroll.setWidget(container)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    @staticmethod
    def _resolve_config_path() -> Path:
        """解析配置文件路径（兼容 PyInstaller 打包环境）"""
        try:
            base = Path(sys._MEIPASS)  # PyInstaller 打包路径
        except AttributeError:
            base = Path(__file__).resolve().parent.parent.parent
        return base / "config" / "default_config.json"

    # ── 配置加载 ─────────────────────────────────────

    def _load_config(self) -> None:
        try:
            if self._config_path.exists():
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
        except Exception as e:
            self._config = {}

    def _save_config(self) -> None:
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"配置保存失败: {e}")

    def _get(self, *keys: str, default=None):
        """安全获取嵌套配置值"""
        val = self._config
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
        return val if val is not None else default

    def _set(self, value, *keys: str) -> None:
        """安全设置嵌套配置值"""
        d = self._config
        for k in keys[:-1]:
            if k not in d or not isinstance(d[k], dict):
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value

    # ── 标题 ─────────────────────────────────────────

    def _build_header(self, layout: QVBoxLayout) -> None:
        header = QHBoxLayout()
        title = QLabel("系统设置")
        title.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        header.addWidget(title)
        header.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {DT.C.STATUS_SUCCESS}; font-size: {DT.FS.BODY_SMALL}px;")
        header.addWidget(self._status_label)

        layout.addLayout(header)
        subtitle = QLabel("网络配置 · 数据库配置 · 显示配置")
        subtitle.setFont(DT.T.get_font(*DT.T.BODY[:2]))
        subtitle.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
        layout.addWidget(subtitle)

    # ── 网络配置 ─────────────────────────────────────

    def _build_network_section(self, layout: QVBoxLayout) -> None:
        group = QGroupBox("网络配置")
        group.setStyleSheet(self._group_style())
        form = QFormLayout(group)
        form.setSpacing(10)
        form.setContentsMargins(DT.S.LG, DT.S.LG, DT.S.LG, DT.S.LG)

        self._net_host = QLineEdit(self._get("communication", "host", default="0.0.0.0"))
        self._net_host.setStyleSheet(DT.sheet.sheet_input())
        form.addRow("绑定地址:", self._net_host)

        self._net_tcp = QSpinBox()
        self._net_tcp.setRange(1, 65535)
        self._net_tcp.setValue(self._get("communication", "tcp_port", default=5000))
        self._net_tcp.setStyleSheet(DT.sheet.sheet_spin())
        form.addRow("TCP 端口:", self._net_tcp)

        self._net_udp = QSpinBox()
        self._net_udp.setRange(1, 65535)
        self._net_udp.setValue(self._get("communication", "udp_port", default=6000))
        self._net_udp.setStyleSheet(DT.sheet.sheet_spin())
        form.addRow("UDP 端口:", self._net_udp)

        self._net_timeout = QSpinBox()
        self._net_timeout.setRange(1, 60)
        self._net_timeout.setValue(int(self._get("communication", "default_timeout", default=5)))
        self._net_timeout.setSuffix(" s")
        self._net_timeout.setStyleSheet(DT.sheet.sheet_spin())
        form.addRow("超时时间:", self._net_timeout)

        self._net_buffer = QSpinBox()
        self._net_buffer.setRange(8, 256)
        self._net_buffer.setValue(self._get("communication", "buffer_size", default=64) // 1024)
        self._net_buffer.setSuffix(" KB")
        self._net_buffer.setStyleSheet(DT.sheet.sheet_spin())
        form.addRow("缓冲区:", self._net_buffer)

        layout.addWidget(group)

    # ── 数据库配置 ───────────────────────────────────

    def _build_database_section(self, layout: QVBoxLayout) -> None:
        group = QGroupBox("数据库配置")
        group.setStyleSheet(self._group_style())
        form = QFormLayout(group)
        form.setSpacing(10)
        form.setContentsMargins(DT.S.LG, DT.S.LG, DT.S.LG, DT.S.LG)

        db_path = self._get("database", "path", default="data/monitor.db")
        db_row = QHBoxLayout()
        self._db_path = QLineEdit(db_path)
        self._db_path.setStyleSheet(DT.sheet.sheet_input())
        db_row.addWidget(self._db_path)
        btn_browse = QPushButton("浏览...")
        btn_browse.setFixedHeight(28)
        btn_browse.setStyleSheet(self._btn_mini_style())
        btn_browse.clicked.connect(self._on_browse_db)
        db_row.addWidget(btn_browse)
        form.addRow("数据库路径:", db_row)

        self._db_backup = QCheckBox("启动时自动备份")
        self._db_backup.setChecked(self._get("database", "backup_on_startup", default=True))
        self._db_backup.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY}; font-size: {DT.FS.BODY}px;")
        form.addRow("", self._db_backup)

        self._db_retention = QSpinBox()
        self._db_retention.setRange(30, 730)
        self._db_retention.setValue(self._get("storage", "retention_days", default=365))
        self._db_retention.setSuffix(" 天")
        self._db_retention.setStyleSheet(DT.sheet.sheet_spin())
        form.addRow("数据保留:", self._db_retention)

        self._db_max_events = QSpinBox()
        self._db_max_events.setRange(1, 100)
        self._db_max_events.setValue(self._get("storage", "max_pd_events", default=10000000) // 1000000)
        self._db_max_events.setSuffix(" 百万条")
        self._db_max_events.setStyleSheet(DT.sheet.sheet_spin())
        form.addRow("最大事件数:", self._db_max_events)

        layout.addWidget(group)

    # ── 显示配置 ─────────────────────────────────────

    def _build_display_section(self, layout: QVBoxLayout) -> None:
        group = QGroupBox("显示配置")
        group.setStyleSheet(self._group_style())
        form = QFormLayout(group)
        form.setSpacing(10)
        form.setContentsMargins(DT.S.LG, DT.S.LG, DT.S.LG, DT.S.LG)

        theme_row = QHBoxLayout()
        theme_label = QLabel("深色模式")
        theme_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY}; font-size: {DT.FS.BODY}px;")
        theme_row.addWidget(theme_label)
        theme_row.addStretch()

        self._theme_toggle = QCheckBox()
        self._theme_toggle.setChecked(DT.Theme.is_dark())
        self._theme_toggle.setStyleSheet(
            f"QCheckBox {{ spacing: 0; }}"
            f"QCheckBox::indicator {{ width: 16px; height: 16px; }}"
        )
        self._theme_toggle.toggled.connect(self._on_theme_toggle)
        theme_row.addWidget(self._theme_toggle)
        form.addRow("", theme_row)

        layout.addWidget(group)

    # ── 保存 ─────────────────────────────────────────

    def _build_save_section(self, layout: QVBoxLayout) -> None:
        save_layout = QHBoxLayout()
        save_layout.addStretch()

        btn_reset = SecondaryButton("恢复默认")
        btn_reset.clicked.connect(self._on_reset_defaults)
        save_layout.addWidget(btn_reset)

        btn_save = PrimaryButton("保存设置")
        btn_save.clicked.connect(self._on_save)
        save_layout.addWidget(btn_save)

        layout.addLayout(save_layout)
        layout.addStretch()

    # ── 事件 ─────────────────────────────────────────

    def _on_theme_toggle(self, checked: bool) -> None:
        """切换深色/亮色主题"""
        DT.Theme.set_dark(checked)
        # 提示用户重启以完整应用主题
        QMessageBox.information(
            self,
            "主题已切换",
            "主题已切换，部分样式需要重启应用后完整生效。",
        )

    def _on_browse_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "选择数据库路径", "data/monitor.db", "SQLite DB (*.db)")
        if path:
            self._db_path.setText(path)

    def _on_save(self) -> None:
        """保存所有配置"""
        # 网络
        self._set(self._net_host.text(), "communication", "host")
        self._set(self._net_tcp.value(), "communication", "tcp_port")
        self._set(self._net_udp.value(), "communication", "udp_port")
        self._set(self._net_timeout.value(), "communication", "default_timeout")
        self._set(self._net_buffer.value() * 1024, "communication", "buffer_size")

        # 数据库
        self._set(self._db_path.text(), "database", "path")
        self._set(self._db_backup.isChecked(), "database", "backup_on_startup")
        self._set(self._db_retention.value(), "storage", "retention_days")
        self._set(self._db_max_events.value() * 1000000, "storage", "max_pd_events")

        self._save_config()
        self._status_label.setText("✓ 设置已保存")
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3000, lambda: self._status_label.setText(""))

    def _on_reset_defaults(self) -> None:
        reply = QMessageBox.question(
            self,
            "恢复默认",
            "确定恢复所有设置为默认值？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._net_host.setText("0.0.0.0")
            self._net_tcp.setValue(5000)
            self._net_udp.setValue(6000)
            self._net_timeout.setValue(5)
            self._net_buffer.setValue(64)
            self._db_path.setText("data/monitor.db")
            self._db_backup.setChecked(True)
            self._db_retention.setValue(365)
            self._db_max_events.setValue(10)
            self._status_label.setText("✓ 已恢复默认设置")

    # ── 样式 ─────────────────────────────────────────

    @staticmethod
    def _group_style() -> str:
        return f"""
            QGroupBox {{
                background: {DT.C.BG_PRIMARY};
                border: 1px solid {DT.C.BORDER_DEFAULT};
                border-radius: {DT.R.LG}px;
                margin-top: 12px;
                padding-top: 20px;
                font-size: {DT.FS.BODY_LARGE}px;
                font-weight: 600;
                color: {DT.C.TEXT_PRIMARY};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 4px 12px;
                background: {DT.C.BG_PRIMARY};
                border: 1px solid {DT.C.BORDER_DEFAULT};
                border-radius: {DT.R.SM}px;
                left: 16px;
            }}
        """

    @staticmethod
    def _btn_mini_style() -> str:
        return f"""
            QPushButton {{
                background: {DT.C.BG_PRIMARY}; color: {DT.C.TEXT_SECONDARY};
                border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px;
                padding: 4px 10px; font-size: {DT.FS.CAPTION}px;
            }}
            QPushButton:hover {{ background: {DT.C.BG_HOVER}; }}
        """
