# -*- coding: utf-8 -*-
"""
UI 组件库 - 自定义 Widget 组件集合

提供统一的 Fluent Design 风格组件，替代原生 Qt 控件。
所有组件使用 design_tokens.py 的 DT 令牌，支持主题适配。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import DT

# ═══════════════════════════════════════════════════════════
# 基础按钮样式生成器（统一使用 DT.C 令牌）
# ═══════════════════════════════════════════════════════════


def _button_base_style(
    bg: str = "",
    bg_hover: str = "",
    text_color: str = DT.C.TEXT_PRIMARY,
    border: str = f"1px solid {DT.C.BORDER_DEFAULT}",
    radius: str = f"{DT.R.MD}px",
) -> str:
    """生成按钮基础 QSS"""
    lines = [
        f"QPushButton {{ background: {bg or 'transparent'}; color: {text_color}; "
        f"border: {border}; border-radius: {radius}; padding: 6px 16px; "
        f"font-size: 13px; font-weight: 500; }}",
    ]
    if bg_hover:
        lines.append(f"QPushButton:hover {{ background: {bg_hover}; }}")
    if bg:
        lines.append(f"QPushButton:pressed {{ background: {bg}; opacity: 0.8; }}")
    lines.append(f"QPushButton:disabled {{ color: {DT.C.TEXT_DISABLED}; background: {DT.C.BG_DISABLED}; }}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 按钮组件
# ═══════════════════════════════════════════════════════════


class PrimaryButton(QPushButton):
    """主要操作按钮 - 蓝色填充"""

    def __init__(self, text: str = "", parent: QWidget = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            _button_base_style(
                bg=DT.C.ACCENT_PRIMARY,
                bg_hover=DT.C.ACCENT_HOVER,
                text_color=DT.C.TEXT_ON_ACCENT,
                border="none",
            )
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)


class SecondaryButton(QPushButton):
    """次要操作按钮 - 白色背景 + 边框"""

    def __init__(self, text: str = "", parent: QWidget = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            _button_base_style(
                bg="",
                bg_hover=DT.C.BG_HOVER,
                text_color=DT.C.TEXT_PRIMARY,
            )
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)


class SuccessButton(QPushButton):
    """成功操作按钮 - 绿色填充"""

    def __init__(self, text: str = "", parent: QWidget = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            _button_base_style(
                bg=DT.C.STATUS_SUCCESS,
                bg_hover=DT.C.DEVICE_ONLINE,
                text_color=DT.C.TEXT_ON_ACCENT,
                border="none",
            )
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)


class DangerButton(QPushButton):
    """危险操作按钮 - 红色填充"""

    def __init__(self, text: str = "", parent: QWidget = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            _button_base_style(
                bg=DT.C.STATUS_ERROR,
                bg_hover="#B91C1C",
                text_color=DT.C.TEXT_ON_DANGER,
                border="none",
            )
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)


class GhostButton(QPushButton):
    """幽灵按钮 - 无背景无边框"""

    def __init__(self, text: str = "", parent: QWidget = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            _button_base_style(
                bg="",
                bg_hover=DT.C.BG_HOVER,
                text_color=DT.C.TEXT_SECONDARY,
                border="none",
            )
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)


# ═══════════════════════════════════════════════════════════
# 输入组件
# ═══════════════════════════════════════════════════════════


class LineEdit(QLineEdit):
    """带样式的单行输入框"""

    def __init__(self, placeholder: str = "", parent: QWidget = None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setStyleSheet(DT.sheet.sheet_input())
        self.setFixedHeight(36)


class ComboBox(QComboBox):
    """带样式的下拉选择框"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setStyleSheet(DT.sheet.sheet_combo(min_width=120))
        self.setFixedHeight(36)


class Checkbox(QCheckBox):
    """带样式的复选框"""

    def __init__(self, text: str = "", parent: QWidget = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            f"""
            QCheckBox {{
                font-size: 13px;
                color: {DT.C.TEXT_PRIMARY};
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 1px solid {DT.C.BORDER_DEFAULT};
                border-radius: 3px;
                background: {DT.C.BG_PRIMARY};
            }}
            QCheckBox::indicator:checked {{
                background: {DT.C.ACCENT_PRIMARY};
                border-color: {DT.C.ACCENT_PRIMARY};
            }}
            QCheckBox::indicator:hover {{
                border-color: {DT.C.BORDER_FOCUS};
            }}
        """
        )


class InputWithLabel(QWidget):
    """带标签的输入框组合"""

    def __init__(self, label: str = "", parent: QWidget = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.label = QLabel(label)
        self.label.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 12px; font-weight: 500;")

        self.input = LineEdit()
        self.input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout.addWidget(self.label)
        layout.addWidget(self.input)


# ═══════════════════════════════════════════════════════════
# 设备树组件
# ═══════════════════════════════════════════════════════════


class DeviceTree(QTreeWidget):
    """设备列表树形控件"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._context_menu_handler = None
        self.setHeaderLabels(["设备类型", "设备编号", "寄存器数量", "设备状态", "操作"])
        self.setAlternatingRowColors(True)
        self.setIndentation(0)
        self.setAnimated(True)
        self.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.setHeaderHidden(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        header = self.header()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(4, 170)

        self._apply_style()

    def _apply_style(self) -> None:
        """应用统一样式"""
        self.setStyleSheet(
            f"""
            QTreeWidget {{
                background-color: {DT.C.BG_PRIMARY};
                alternate-background-color: {DT.C.BG_SECONDARY};
                border: 1px solid {DT.C.BORDER_DEFAULT};
                border-radius: {DT.R.MD}px;
                padding: 4px;
                font-size: 13px;
                outline: none;
            }}
            QTreeWidget::item {{
                padding: 8px 4px;
                border-bottom: 1px solid {DT.C.BORDER_SUBTLE};
            }}
            QTreeWidget::item:hover {{
                background-color: {DT.C.BG_HOVER};
            }}
            QTreeWidget::item:selected {{
                background-color: {DT.C.ACCENT_SUBTLE};
                color: {DT.C.ACCENT_PRIMARY};
            }}
            QHeaderView::section {{
                background-color: {DT.C.BG_SECONDARY};
                color: {DT.C.TEXT_SECONDARY};
                border: none;
                border-bottom: 1px solid {DT.C.BORDER_DEFAULT};
                padding: 8px 6px;
                font-size: 12px;
                font-weight: 600;
            }}
        """
        )

    def _on_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu

        item = self.itemAt(pos)
        if item is None:
            return
        while item.parent():
            item = item.parent()
        device_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not device_id or not self._context_menu_handler:
            return

        menu = QMenu(self)
        edit_action = menu.addAction("配置设备信息")
        scan_action = menu.addAction("扫描设备")
        menu.addSeparator()
        copy_action = menu.addAction("复制设备")
        delete_action = menu.addAction("删除设备")

        edit_action.triggered.connect(lambda: self._context_menu_handler("edit", device_id))
        scan_action.triggered.connect(lambda: self._context_menu_handler("scan", ""))
        copy_action.triggered.connect(lambda: self._context_menu_handler("copy", device_id))
        delete_action.triggered.connect(lambda: self._context_menu_handler("delete", device_id))

        menu.exec(self.viewport().mapToGlobal(pos))


# ═══════════════════════════════════════════════════════════
# 数据表格组件
# ═══════════════════════════════════════════════════════════


class DataTable(QTableWidget):
    """数据表格控件"""

    def __init__(self, columns: list = None, parent: QWidget = None):
        super().__init__(parent)
        if columns:
            self.setColumnCount(len(columns))
            self.setHorizontalHeaderLabels(columns)

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)

        self.setStyleSheet(DT.sheet.sheet_table())


# ═══════════════════════════════════════════════════════════
# 状态徽章组件
# ═══════════════════════════════════════════════════════════


class StatusBadge(QWidget):
    """状态徽章 - 带颜色圆点和文字"""

    STATUS_COLORS = {
        "online": DT.C.STATUS_SUCCESS,
        "offline": DT.C.TEXT_TERTIARY,
        "warning": DT.C.STATUS_WARNING,
        "error": DT.C.STATUS_ERROR,
        "info": DT.C.ACCENT_PRIMARY,
        "success": DT.C.STATUS_SUCCESS,
        "default": DT.C.TEXT_TERTIARY,
    }

    def __init__(self, text: str = "", status: str = "default", parent: QWidget = None):
        super().__init__(parent)
        self._status = status
        self._text = text

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)

        self._label = QLabel(text)
        self._label.setStyleSheet(f"font-size: 12px; color: {DT.C.TEXT_SECONDARY};")

        layout.addWidget(self._dot)
        layout.addWidget(self._label)

        self.set_status(status)

    def set_status(self, status: str) -> None:
        """设置状态"""
        self._status = status
        color = self.STATUS_COLORS.get(status, self.STATUS_COLORS["default"])
        self._dot.setStyleSheet(
            f"""
            background: {color};
            border-radius: 4px;
        """
        )

    def set_text(self, text: str) -> None:
        """设置文字"""
        self._label.setText(text)


# ═══════════════════════════════════════════════════════════
# 数据卡片组件
# ═══════════════════════════════════════════════════════════


class DataCard(QFrame):
    """数据卡片 - 显示设备参数值"""

    def __init__(self, title: str = "", value: str = "0.00", parent: QWidget = None):
        super().__init__(parent)
        self._title_text = title
        self._value_text = value

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"""
            DataCard {{
                background: {DT.C.BG_PRIMARY};
                border: 1px solid {DT.C.BORDER_DEFAULT};
                border-radius: {DT.R.LG}px;
                padding: 16px;
            }}
            DataCard:hover {{
                border-color: {DT.C.BORDER_HOVER};
                background: {DT.C.BG_HOVER};
            }}
        """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: 12px; font-weight: 500;")

        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY}; font-size: 24px; font-weight: 700;")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label, 1)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.unit_label = None

    def set_value(self, value: str) -> None:
        """更新显示值"""
        self._value_text = value
        self.value_label.setText(value)


# ═══════════════════════════════════════════════════════════
# 操作卡片组件
# ═══════════════════════════════════════════════════════════


class ActionCard(QFrame):
    """操作卡片 - 带标题和按钮的快捷操作区域"""

    def __init__(self, title: str = "", parent: QWidget = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(DT.sheet.sheet_card("ActionCard"))

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 12, 16, 12)
        self._layout.setSpacing(12)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY}; font-size: 14px; font-weight: 600;")
        self._layout.addWidget(self.title_label)

    def add_widget(self, widget: QWidget) -> None:
        """添加子组件"""
        self._layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        """添加子布局"""
        self._layout.addLayout(layout)


# ═══════════════════════════════════════════════════════════
# 可视化组件 (从 visual 模块导入)
# ═══════════════════════════════════════════════════════════

# UHF PD 分析控件 (PyQtGraph)
try:
    from .waveform_widget import WaveformWidget
    from .prpd_widget import PRPDWidget, PRPDDisplayMode
    from .prps_widget import PRPSWidget
    from .fft_widget import FFTWidget
    from .trend_chart_widget import TrendChartWidget
    from .ae_parameters_widget import AEParametersWidget
    from .ae_localization_widget import AELocalizationWidget
except ImportError as e:
    import logging
    logging.getLogger(__name__).warning("部分 PD 分析控件加载失败: %s", e)

# TODO(P2-24): 统一按钮样式定义，消除4处重复
