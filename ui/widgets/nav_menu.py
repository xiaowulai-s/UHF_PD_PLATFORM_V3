# -*- coding: utf-8 -*-
"""
导航菜单组件 - NavMenu

左侧垂直导航栏，支持：
- 图标 + 文字标签
- 选中态高亮（蓝色强调）
- 悬停态背景反馈
- 折叠/展开模式
- 符合 Fluent Design 设计规范
"""

from typing import List, Optional, Tuple

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout, QWidget

from ui.design_tokens import DT

# 菜单项定义: (key, 标签, 图标字符)
MenuItem = Tuple[str, str, str]


DEFAULT_MENU_ITEMS: List[MenuItem] = [
    ("dashboard", "首页", "⌂"),  # ⌂
    ("monitor", "实时监测", "◉"),  # ◉
    ("analysis", "数据分析", "▦"),  # ▦
    ("trend", "趋势分析", "↗"),  # ↗
    ("alarm", "报警管理", "⚠"),  # ⚠
    ("device", "设备管理", "⊞"),  # ⊞
    ("ae", "AE 分析", "◎"),  # ◎
    ("settings", "系统设置", "⚙"),  # ⚙
]


class NavItem(QWidget):
    """单个导航项"""

    clicked = Signal(str)  # key

    def __init__(self, key: str, label: str, icon: str, parent: QWidget = None):
        super().__init__(parent)
        self._key = key
        self._label = label
        self._icon = icon
        self._selected = False
        self._hovered = False
        self._collapsed = False
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # 选中态左侧指示条
        if self._selected:
            painter.fillRect(0, 0, 3, h, QColor(DT.C.ACCENT_PRIMARY))

        # 背景
        if self._selected:
            painter.fillRect(3, 0, w - 3, h, QColor(DT.C.ACCENT_SUBTLE))
        elif self._hovered:
            painter.fillRect(3, 0, w - 3, h, QColor(DT.C.BG_HOVER))

        # 图标
        icon_font = QFont("Segoe UI Symbol", 16)
        painter.setFont(icon_font)
        if self._selected:
            painter.setPen(QColor(DT.C.ACCENT_PRIMARY))
        else:
            painter.setPen(QColor(DT.C.TEXT_SECONDARY))
        painter.drawText(14, 0, 28, h, Qt.AlignmentFlag.AlignCenter, self._icon)

        # 文字（折叠模式不显示）
        if not self._collapsed:
            label_font = QFont("Segoe UI Variable", 13)
            painter.setFont(label_font)
            if self._selected:
                painter.setPen(QColor(DT.C.ACCENT_PRIMARY))
            else:
                painter.setPen(QColor(DT.C.TEXT_PRIMARY))
            painter.drawText(48, 0, w - 56, h, Qt.AlignmentFlag.AlignVCenter, self._label)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)

    @property
    def key(self) -> str:
        return self._key

    @property
    def selected(self) -> bool:
        return self._selected

    @selected.setter
    def selected(self, value: bool) -> None:
        if self._selected != value:
            self._selected = value
            self.update()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    @collapsed.setter
    def collapsed(self, value: bool) -> None:
        if self._collapsed != value:
            self._collapsed = value
            self.update()


class NavMenu(QFrame):
    """左侧导航菜单"""

    item_selected = Signal(str)  # 发出选中项的 key

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._items: List[NavItem] = []
        self._selected_key: Optional[str] = None
        self._collapsed = False
        self._animating = False

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"""
            NavMenu {{
                background-color: {DT.C.BG_PRIMARY};
                border-right: 1px solid {DT.C.BORDER_DEFAULT};
            }}
        """
        )

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 8, 0, 0)
        self._layout.setSpacing(2)

        self._populate_items()

        # 折叠按钮
        from ui.widgets import GhostButton

        self._collapse_btn = GhostButton("«", self)  # «
        self._collapse_btn.setFixedHeight(40)
        self._collapse_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 0;
                color: {DT.C.TEXT_TERTIARY};
                font-size: 18px;
                padding: 0;
            }}
            QPushButton:hover {{
                background: {DT.C.BG_HOVER};
                color: {DT.C.TEXT_SECONDARY};
            }}
        """
        )
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        self._layout.addStretch()
        self._layout.addWidget(self._collapse_btn, 0, Qt.AlignmentFlag.AlignCenter)

        self.setFixedWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def _populate_items(self) -> None:
        """创建菜单项"""
        for key, label, icon in DEFAULT_MENU_ITEMS:
            item = NavItem(key, label, icon, self)
            item.clicked.connect(self._on_item_clicked)
            self._items.append(item)
            self._layout.addWidget(item)

    def _on_item_clicked(self, key: str) -> None:
        self.select_item(key)
        self.item_selected.emit(key)

    def select_item(self, key: str) -> None:
        """选中指定项"""
        self._selected_key = key
        for item in self._items:
            item.selected = item.key == key

    def set_collapsed(self, collapsed: bool) -> None:
        """设置折叠/展开状态（供外部调用，如窗口 resizeEvent）"""
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        target_width = 60 if collapsed else 200

        self._animating = True
        self._animation = QPropertyAnimation(self, b"menu_width")
        self._animation.setDuration(200)
        self._animation.setStartValue(self.width())
        self._animation.setEndValue(target_width)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_done)
        self._animation.start()

        for item in self._items:
            item.collapsed = collapsed

        # 更新折叠按钮图标
        self._collapse_btn.setText("»" if collapsed else "«")

    def _toggle_collapse(self) -> None:
        """切换折叠/展开"""
        self.set_collapsed(not self._collapsed)

    def _on_animation_done(self) -> None:
        self._animating = False

    def _get_menu_width(self) -> int:
        return self.width()

    def _set_menu_width(self, w: int) -> None:
        self.setFixedWidth(w)

    menu_width = Property(int, _get_menu_width, _set_menu_width)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    @property
    def selected_key(self) -> Optional[str]:
        return self._selected_key
