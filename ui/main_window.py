# -*- coding: utf-8 -*-
"""
UHF PD Monitor 主窗口
基于现有 MCGS_EMS 主窗口架构重构，保留 Fluent Design 风格：
- 左侧导航菜单（可折叠）
- 右侧页面栈
- 统一的状态栏和菜单栏
"""

from __future__ import annotations

from datetime import datetime
import time

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QAction, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.utils.logger import get_logger
from ui.design_tokens import DT
from ui.pages import AlarmPage, AEAnalysisPage, AnalysisPage, DashboardPage, DevicePage, RealtimeMonitorPage, SettingsPage, TrendPage
from ui.widgets.nav_menu import NavMenu

logger = get_logger("main_window")


class PDMainWindow(QMainWindow):
    """超高频超声波局部放电检测系统主窗口"""

    APP_NAME = "UHF-AE-PD-Detector"
    WINDOW_TITLE = "超高频超声波局部放电检测系统"

    PAGE_KEYS = ["dashboard", "monitor", "analysis", "trend", "alarm", "device", "settings", "ae"]
    PAGE_TITLES = [
        "系统仪表板",
        "实时监测",
        "数据分析",
        "趋势分析",
        "报警管理",
        "设备管理",
        "系统设置",
        "AE 特征分析",
    ]

    # NavMenu key → stack index 映射
    KEY_TO_INDEX = {key: i for i, key in enumerate(PAGE_KEYS)}

    CHANGELOG = [
        {
            "version": "v1.0.7",
            "date": "2026-06-12",
            "changes": [
                "优化：MCGS 系统代码全量移除（30+个文件：Modbus协议栈、TCP/串口驱动、服务与工具模块）",
                "优化：README 重构，仅保留 PD 系统文档",
                "优化：UI 配色体系统一（Material Blue → Fluent Blue #0969DA）",
                "优化：样式代码去重，新增 DT.sheet.* 统一工厂方法，6个页面统一引用",
                "优化：Dashboard 指标卡片自适应高度、报警栏自适应、迷你图表合并工厂函数",
                "优化：Monitor 三栏拉伸比调整为 3:2:1",
                "优化：报警批量插入延迟刷新、清除确认弹窗",
                "优化：DevicePage 删除确认弹窗、耦合类型动态循环分配",
                "优化：AEAnalysisPage 时间范围按钮改用 QButtonGroup",
                "优化：关于对话框重构、状态栏样式精简、5个 __init__.py 导出清理",
            ],
        },
        {
            "version": "v1.0.6",
            "date": "2026-06-11",
            "changes": [
                "新增：AE 声发射子系统，协议层/处理层/数据层全链路集成",
                "新增：AE 包络处理器、峰值检测器、参数提取器三大信号处理模块",
                "新增：通道耦合类型切换（UHF/AE），支持自动模式识别与管线分发",
            ],
        },
        {
            "version": "v1.0.5",
            "date": "2026-06-10",
            "changes": [
                "新增：关于对话框重构，Tab式版本日志（关于页/更新日志页）",
                "新增：更新日志内嵌，CHANGELOG结构化存储，支持新增/优化/修复标签颜色区分",
                "新增：关于页展示最新版本更新内容",
                "优化：分类置信度计算，移除0.85硬性上限，改为直接使用最高占比类型投票率",
                "优化：冗余文档清理，删除CHANGELOG.md、docs/下16个旧文档等，减少12000+行冗余内容",
            ],
        },
        {
            "version": "v1.0.4",
            "date": "2026-06-10",
            "changes": [
                "新增：放电类型分类策略重设计（Profile Matching 替代规则引擎），8维特征+高斯核相似度+Softmax归一化",
                "新增：幅值分布特征（变异系数/双峰性/正半周能量比），增强类型区分能力",
                "新增：分析结果面板重构，参考结果卡片/类型统计表/幅值统计表，标记为参考结果",
                "新增：Bootstrap多次采样统计，50次有放回采样，各类型出现次数和占比",
                "新增：随机噪声(noise)识别不UI灰色显示",
                "优化：分析页面布局，幅值分布宽度与PRPD一致，分析结果高度扩展至底部",
                "优化：统一分类路径，移除classify_by_cycles，统一使用Bootstrap classify_cycles",
                "修复：DataImporter解包缺少cycles的ValueError",
            ],
        },
        {
            "version": "v1.0.3",
            "date": "2026-06-10",
            "changes": [
                "新增：PRPS 图谱（相位-脉冲-序列）",
                "新增：PRPS 3D 散点图渲染（周期/相位/幅值）",
                "优化：PRPD 热力图渲染性能（Matplotlib Canvas 懒加载+增量更新）",
                "修复：PRPS 数据错发给 PRPD 控件",
                "修复：PRPS 无节流导致 UI 卡顿",
            ],
        },
        {
            "version": "v1.0.2",
            "date": "2026-06-10",
            "changes": [
                "新增：系统设置页面",
                "新增：设备管理页面（CRUD + 通道配置）",
                "新增：报警管理页面（事件列表 + 规则配置）",
                "优化：导航菜单选中态背景色",
                "优化：趋势图数据实时推送",
                "修复：仪表盘数据永远为 0",
                "修复：趋势图永远为空",
                "修复：终端中文日志乱码",
                "修复：PeakDetector 每帧新建",
            ],
        },
        {
            "version": "v1.0.1",
            "date": "2026-06-09",
            "changes": [
                "新增：PRPD 散点图 + 热力图",
                "新增：FFT 频谱分析",
                "优化：3D 散点图渲染性能",
                "修复：PRPD 散点未传 UI",
            ],
        },
    ]

    def __init__(self, db_manager=None, parent=None):
        super().__init__(parent)
        self._controller = None
        self._db_manager = db_manager
        self._pages = {}
        self._stack_widget = None

        self._setup_ui()
        self._init_pages()
        self._load_sample_data()

        logger.info("超高频超声波局部放电检测系统主窗口初始化完成")

    # ── UI 构建 ──────────────────────────────────────

    def _setup_ui(self) -> None:
        """构建主窗口 UI"""
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setMinimumSize(1024, 680)
        self.resize(1400, 900)

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 左侧导航菜单
        self._nav_menu = NavMenu(self)
        self._nav_menu.item_selected.connect(self._on_page_changed)
        main_layout.addWidget(self._nav_menu, 0)

        # 右侧页面栈
        self._stack_widget = QStackedWidget()
        main_layout.addWidget(self._stack_widget, 1)

        # 底部状态栏
        self._setup_statusbar()

        # 系统托盘
        self._setup_tray_icon()

    def _setup_statusbar(self) -> None:
        """构建状态栏"""
        status = self.statusBar()
        status.setObjectName("statusBar")
        status.setStyleSheet("QStatusBar::item { border: none; }")

        # 在线设备数
        self._lbl_online = QLabel("在线设备: 0")
        self._lbl_online.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
        status.addPermanentWidget(self._lbl_online)

        # 快捷键提示
        self._lbl_shortcut = QLabel("F1-F8 切换页面 | Space 暂停")
        self._lbl_shortcut.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: 11px;")
        self._lbl_shortcut.setVisible(True)  # 快捷键提示
        status.addPermanentWidget(self._lbl_shortcut)

        # 最新报警
        self._lbl_alarm = QLabel("最新报警: —")
        self._lbl_alarm.setStyleSheet(f"color: {DT.C.STATUS_WARNING};")
        self._lbl_alarm.setVisible(False)
        status.addPermanentWidget(self._lbl_alarm)

        # 时间
        self._lbl_time = QLabel(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._lbl_time.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
        status.addPermanentWidget(self._lbl_time)

        # 时间定时器
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_time)
        self._timer.start(1000)

    def _update_time(self) -> None:
        """更新时间显示"""
        self._lbl_time.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # ── 系统托盘 ──────────────────────────────────────

    def _setup_tray_icon(self) -> None:
        """构建系统托盘图标"""
        self._tray_icon = QSystemTrayIcon(self)
        self._tray_icon.setIcon(QIcon("assets/icons/ems.png"))
        self._tray_icon.setToolTip(self.WINDOW_TITLE)
        self._tray_icon.show()

    def notify_alarm(self, level: str, title: str, message: str) -> None:
        """报警通知：系统托盘弹窗 + 状态栏闪烁"""
        # 托盘通知
        icon_map = {"critical": QSystemTrayIcon.MessageIcon.Critical,
                     "warning": QSystemTrayIcon.MessageIcon.Warning,
                     "info": QSystemTrayIcon.MessageIcon.Information}
        icon = icon_map.get(level, QSystemTrayIcon.MessageIcon.Information)
        self._tray_icon.showMessage(title, message, icon, 5000)

        # 状态栏闪烁（严重/一般报警）
        if level in ("critical", "warning"):
            self._flash_status_bar(level)

    def _flash_status_bar(self, level: str) -> None:
        """状态栏闪烁提示（Windows 任务栏闪烁，非 Windows 平台使用托盘图标提示）"""
        import platform
        if platform.system() == "Windows":
            try:
                import ctypes
                hwnd = int(self.winId())
                ctypes.windll.user32.FlashWindow(hwnd, True)
            except Exception as e:
                logger.debug("任务栏闪烁失败: %s", e)

    # ── 页面初始化 ───────────────────────────────────

    def _init_pages(self) -> None:
        """初始化所有页面"""
        page_classes = [
            DashboardPage,
            RealtimeMonitorPage,
            AnalysisPage,
            TrendPage,
            AlarmPage,
            DevicePage,
            SettingsPage,
            AEAnalysisPage,
        ]

        for key, cls in zip(self.PAGE_KEYS, page_classes):
            page = cls(self)
            self._pages[key] = page
            self._stack_widget.addWidget(page)

        # 页面后初始化（DevicePage.__init__ 已调用 add_sample_devices，无需重复）

        # 默认选中首页
        self._nav_menu.select_item("dashboard")

    def _load_sample_data(self) -> None:
        """加载示例数据"""
        alarm_desc = "局放超限 - 幅值 85.3mV (85%), 等级: 严重报警"
        self._pages["alarm"].add_alarm(
            {
                "device_id": "DEV-001",
                "alarm_type": "pd_over_limit",
                "level": "critical",
                "amplitude": 85.3,
                "description": alarm_desc,
                "timestamp": time.time(),
            }
        )
        self._pages["alarm"].add_alarm(
            {
                "device_id": "DEV-002",
                "alarm_type": "pd_over_limit",
                "level": "warning",
                "amplitude": 42.1,
                "description": "局放超限 - 幅值 42.1mV (42%), 等级: 一般报警",
                "timestamp": time.time() - 60,
            }
        )

    # ── 页面切换 ─────────────────────────────────────

    def _on_page_changed(self, key: str) -> None:
        """导航菜单切换页面"""
        index = self.KEY_TO_INDEX.get(key, 0)
        self._stack_widget.setCurrentIndex(index)
        title = self.PAGE_TITLES[index] if index < len(self.PAGE_TITLES) else key
        self.setWindowTitle(f"{self.WINDOW_TITLE} - {title}")

    # ── 控制器 ───────────────────────────────────────

    def set_controller(self, controller) -> None:
        """设置系统控制器"""
        self._controller = controller

    @property
    def pages(self) -> dict:
        """页面字典（供控制器访问）"""
        return self._pages

    # ── 生命周期 ─────────────────────────────────────

    def resizeEvent(self, event) -> None:
        """窗口大小变化时自动折叠/展开导航栏"""
        super().resizeEvent(event)
        should_collapse = event.size().width() < 1200
        self._nav_menu.set_collapsed(should_collapse)

    def closeEvent(self, event) -> None:
        """窗口关闭事件"""
        if self._controller:
            self._controller.shutdown()

        # 保存窗口状态
        settings = QSettings("UHF-PD-Monitor", "PDMainWindow")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())

        logger.info("窗口状态已保存")
        event.accept()

    # ── 关于对话框 ───────────────────────────────────

    def _show_about(self) -> None:
        """显示关于对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle("关于")
        dialog.setMinimumSize(600, 500)

        layout = QVBoxLayout(dialog)

        # Tab 容器
        stack = QStackedWidget()

        # Tab 1: 关于
        about_page = QWidget()
        about_layout = QVBoxLayout(about_page)
        about_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel("超高频超声波局部放电检测系统")
        title_lbl.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title_lbl.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(title_lbl)

        version_lbl = QLabel(f"版本 {self.application().applicationVersion()}")
        version_lbl.setFont(DT.T.get_font(*DT.T.BODY[:2]))
        version_lbl.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY};")
        version_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(version_lbl)

        about_layout.addSpacing(20)

        desc = QLabel("基于 PySide6 + PyQtGraph 的工业级局放检测平台\n支持超声波 / 超高频 双模态局放检测与AE定位")
        desc.setFont(DT.T.get_font(*DT.T.BODY[:2]))
        desc.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY};")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        about_layout.addWidget(desc)

        about_layout.addSpacing(20)

        tech = QLabel("技术栈: PySide6 | PyQtGraph | NumPy | SciPy | SQLAlchemy | SQLite")
        tech.setFont(DT.T.get_font(*DT.T.BODY[:2]))
        tech.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY};")
        tech.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(tech)

        stack.addWidget(about_page)

        # Tab 2: 更新日志
        changelog_page = QWidget()
        changelog_layout = QVBoxLayout(changelog_page)

        for entry in self.CHANGELOG:
            frame = QFrame()
            frame.setStyleSheet(
                f"""
                QFrame {{
                    background: {DT.C.BG_SECONDARY};
                    border-radius: {DT.R.MD}px;
                    padding: 12px;
                }}
            """
            )
            frame_layout = QVBoxLayout(frame)

            ver_lbl = QLabel(f"v{entry['version']} — {entry['date']}")
            ver_lbl.setFont(DT.T.get_font(*DT.T.BODY[:2], "SemiBold"))
            ver_lbl.setStyleSheet(f"color: {DT.C.ACCENT_PRIMARY};")
            frame_layout.addWidget(ver_lbl)

            for change in entry["changes"]:
                change_lbl = QLabel(f"• {change}")
                change_lbl.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 11px;")
                change_lbl.setWordWrap(True)
                frame_layout.addWidget(change_lbl)

            changelog_layout.addWidget(frame)

        stack.addWidget(changelog_page)

        layout.addWidget(stack)

        # 底部 Tab 按钮组
        tab_layout = QHBoxLayout()
        btn_about = QPushButton("关于")
        btn_log = QPushButton("更新日志")
        btn_close = QPushButton("关闭")

        self._about_tab_group = QButtonGroup(dialog)
        self._about_tab_group.setExclusive(True)
        self._about_tab_group.addButton(btn_about, 0)
        self._about_tab_group.addButton(btn_log, 1)

        def _apply_tab_style():
            active_id = self._about_tab_group.checkedId()
            for btn in [btn_about, btn_log]:
                active = self._about_tab_group.id(btn) == active_id
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {DT.C.ACCENT_PRIMARY if active else DT.C.BG_SECONDARY};
                        color: {'#FFFFFF' if active else DT.C.TEXT_PRIMARY};
                        border: none; border-radius: 4px; padding: 6px 16px;
                        font-size: 12px; font-weight: 600;
                    }}
                    QPushButton:hover {{ background: {DT.C.BG_HOVER if not active else DT.C.ACCENT_HOVER}; }}
                """)

        for btn in [btn_about, btn_log]:
            btn.setCheckable(True)
        btn_about.setChecked(True)
        _apply_tab_style()

        self._about_tab_group.buttonClicked.connect(lambda: _apply_tab_style())
        self._about_tab_group.buttonClicked.connect(
            lambda btn: stack.setCurrentIndex(self._about_tab_group.id(btn))
        )

        tab_layout.addWidget(btn_about)
        tab_layout.addWidget(btn_log)
        tab_layout.addStretch()

        btn_close.setStyleSheet(f"""
            QPushButton {{
                background: {DT.C.BG_SECONDARY}; color: {DT.C.TEXT_PRIMARY};
                border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px;
                padding: 6px 16px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {DT.C.BG_HOVER}; }}
        """)
        btn_close.clicked.connect(dialog.close)
        tab_layout.addWidget(btn_close)

        layout.addLayout(tab_layout)
        dialog.exec()

    # ── 键盘快捷键 ───────────────────────────────────

    def keyPressEvent(self, event) -> None:
        """键盘快捷键处理"""
        key = event.key()
        PAGE_KEYS = ["dashboard", "monitor", "analysis", "trend", "alarm", "device", "ae", "settings"]

        # F1-F8 切换页面
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F8:
            idx = key - Qt.Key.Key_F1
            if idx < len(PAGE_KEYS):
                self._nav_menu.select_item(PAGE_KEYS[idx])
                self._on_page_changed(PAGE_KEYS[idx])
                event.accept()
                return

        # Space 暂停/继续波形
        if key == Qt.Key.Key_Space:
            if "monitor" in self._pages:
                monitor = self._pages["monitor"]
                if hasattr(monitor, 'toggle_pause'):
                    monitor.toggle_pause()
                    event.accept()
                    return

        # Ctrl+Q 退出程序
        if key == Qt.Key.Key_Q and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.close()
            event.accept()
            return

        super().keyPressEvent(event)

    # ── 菜单栏 ───────────────────────────────────────

    def _setup_menu(self) -> None:
        """构建菜单栏"""
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件(&F)")
        exit_action = QAction("退出(&X)", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于(&A)", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
