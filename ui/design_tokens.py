# -*- coding: utf-8 -*-
"""
Windows 11 Fluent Design 设计令牌系统

提供统一的设计规范，包括颜色、字体、间距、圆角等。
所有UI组件应使用此模块的常量，避免硬编码。

使用示例:
    from ui.design_tokens import DT

    label.setStyleSheet(f"color: {DT.Colors.TEXT_PRIMARY};")
    btn.setStyleSheet(f"background: {DT.Colors.ACCENT_PRIMARY}; border-radius: {DT.Radius.MD}px;")
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple, Optional


class Colors:
    """
    颜色系统 - Windows 11 Fluent Design 色板

    基于 Fluent UI 2 的语义化颜色命名:
    - TEXT_*: 文字颜色
    - ACCENT_*: 强调色/品牌色
    - STATUS_*: 状态色 (成功/警告/错误)
    - BORDER_*: 边框颜色
    - BG_*: 背景颜色
    """

    # === 文字颜色 (Text Colors) ===
    TEXT_PRIMARY = "#24292F"  # 主要文字 - 深灰/近黑
    TEXT_SECONDARY = "#57606A"  # 次要文字 - 中灰
    TEXT_TERTIARY = "#8B949E"  # 三级文字/占位符 - 浅灰
    TEXT_DISABLED = "#9CA3AF"  # 禁用状态文字
    TEXT_INVERSE = "#FFFFFF"  # 反色文字（深色背景上）
    TEXT_LINK = "#0969DA"  # 链接文字
    TEXT_ON_ACCENT = "#FFFFFF"  # 强调色上的文字
    TEXT_ON_DANGER = "#FFFFFF"  # 危险色上的文字

    # === 强调色/品牌色 (Accent Colors) ===
    ACCENT_PRIMARY = "#0969DA"  # 主强调色 - 蓝色
    ACCENT_SECONDARY = "#6E7681"  # 次强调色
    ACCENT_TERTIARY = "#D0D7DE"  # 三级强调色
    ACCENT_HOVER = "#0550AE"  # 悬停态
    ACCENT_ACTIVE = "#032D62"  # 激活/按下态
    ACCENT_SUBTLE = "#DDF4FF"  # 微妙的强调背景

    # === 状态色 (Status Colors) ===
    STATUS_SUCCESS = "#1A7F37"  # 成功 - 绿色
    STATUS_SUCCESS_BG = "#DAEFDF"  # 成功背景
    STATUS_WARNING = "#BF8700"  # 警告 - 橙色/金色
    STATUS_WARNING_BG = "#FFF8C5"  # 警告背景
    STATUS_ERROR = "#CF222E"  # 错误 - 红色
    STATUS_ERROR_BG = "#FFEDEB"  # 错误背景
    STATUS_INFO = "#0969DA"  # 信息 - 蓝色
    STATUS_INFO_BG = "#DDF4FF"  # 信息背景

    # === 设备状态专用色 (Device Status) - Fluent Design 2.0 ===
    DEVICE_ONLINE = "#2DA44E"  # 设备在线 - Fluent绿色
    DEVICE_ONLINE_LIGHT = "#54AE76"
    DEVICE_OFFLINE = "#F6F8FA"  # 设备离线 - 浅灰（配合深色文字）
    DEVICE_OFFLINE_LIGHT = "#E5E7EB"
    DEVICE_WARNING = "#D29922"  # 设备警告 - Fluent金色
    DEVICE_WARNING_LIGHT = "#E6B34D"
    DEVICE_ERROR = "#CF222E"  # 设备错误 - Fluent红色
    DEVICE_ERROR_LIGHT = "#E85B65"
    DEVICE_IDLE = "#0969DA"  # 设备空闲 - Fluent蓝色
    DEVICE_IDLE_LIGHT = "#4CA1ED"

    # === 边框颜色 (Border Colors) ===
    BORDER_DEFAULT = "#D0D7DE"  # 默认边框
    BORDER_FOCUS = "#0969DA"  # 聚焦边框
    BORDER_HOVER = "#B0B7C0"  # 悬停边框
    BORDER_STRONG = "#8C959F"  # 强边框
    BORDER_SUBTLE = "#E5E7EB"  # 微妙边框/分割线
    BORDER_TRANSPARENT = "transparent"

    # === 背景颜色 (Background Colors) ===
    BG_PRIMARY = "#FFFFFF"  # 主背景 - 白色
    BG_SECONDARY = "#F6F8FA"  # 次背景 - 浅灰
    BG_TERTIARY = "#EAEDF0"  # 三级背景
    BG_HOVER = "#F3F4F6"  # 悬停背景
    BG_ACTIVE = "#E5E7EB"  # 激活/选中背景
    BG_DISABLED = "#F3F4F6"  # 禁用背景
    BG_OVERLAY = "rgba(0, 0, 0, 0.4)"  # 遮罩层
    BG_MODAL = "#FFFFFF"  # 弹窗背景
    BG_TOOLTIP = "#24292F"  # 提示框背景
    BG_BASE = "#FAFBFC"  # 基础底色

    # === 特殊用途色 (Special Purpose) ===
    SHADOW_COLOR = "rgba(0, 0, 0, 0.10)"  # 阴影颜色
    DIVIDER = "#E5E7EB"  # 分割线

    # === 图表专用色 (Chart Colors) ===
    CHART_BACKGROUND = "#FAFBFC"  # 图表背景
    CHART_GRID = "rgba(0, 0, 0, 0.08)"  # 网格线
    CHART_FOREGROUND = "#24292F"  # 图表前景/坐标轴
    CHART_COLORMAP = "inferno"  # 默认色彩映射（色盲友好）
    CHART_WAVEFORM = "#0969DA"  # 波形颜色
    CHART_SPECTRUM = "#1A7F37"  # 频谱颜色

    # === ISA-18.2 报警色 (Alarm Colors) ===
    ALARM_UNACKNOWLEDGED = "#FF4444"  # 未确认报警（需闪烁）
    ALARM_ACKNOWLEDGED = "#FF8800"  # 已确认报警（静态）
    ALARM_RETURN_NORMAL = "#44AA44"  # 恢复正常


class Typography:
    """
    字体系统 - 基于 Segoe UI Variable (Windows 11 默认字体)

    每个条目格式: (字体族, 字号, 字重)

    使用示例:
        font_info = Typography.TITLE_LARGE
        label.setFont(QFont(font_info[0], font_info[1], QFont.Weight[font_info[2]]))
    """

    # === 标题层级 (Heading Hierarchy) ===
    TITLE_XLARGE = ("Segoe UI Variable", 28, "Bold")  # 页面主标题
    TITLE_LARGE = ("Segoe UI Variable", 20, "SemiBold")  # 区域标题
    TITLE_MEDIUM = ("Segoe UI Variable", 16, "SemiBold")  # 卡片/区块标题
    TITLE_SMALL = ("Segoe UI Variable", 14, "SemiBold")  # 小标题
    TITLE_XSMALL = ("Segoe UI Variable", 12, "SemiBold")  # 最小标题

    # === 正文 (Body Text) ===
    BODY_LARGE = ("Segoe UI Variable", 14, "Regular")  # 大正文
    BODY = ("Segoe UI Variable", 13, "Regular")  # 标准正文 (最常用)
    BODY_SMALL = ("Segoe UI Variable", 12, "Regular")  # 小正文
    BODY_XSMALL = ("Segoe UI Variable", 11, "Regular")  # 最小正文

    # === 标签/说明文字 (Caption/Label) ===
    LABEL = ("Segoe UI Variable", 12, "Medium")  # 标签
    CAPTION = ("Segoe UI Variable", 11, "Regular")  # 说明文字
    CAPTION_SMALL = ("Segoe UI Variable", 10, "Regular")  # 最小说明

    # === 数据显示 (Data Display) ===
    DATA_LARGE = ("Segoe UI Variable", 28, "Bold")  # 大数值显示
    DATA_MEDIUM = ("Segoe UI Variable", 22, "Bold")  # 中等数值
    DATA = ("Segoe UI Variable", 18, "SemiBold")  # 标准数值
    DATA_SMALL = ("Segoe UI Variable", 14, "Medium")  # 小数值
    CODE = ("Consolas", 12, "Regular")  # 代码/等宽字体

    @staticmethod
    def get_font(name: str, size: int, weight: str = "Regular"):
        """
        创建 QFont 对象

        Args:
            name: 字体名称
            size: 字号
            weight: 字重 ('Thin', 'Light', 'Regular', 'Medium', 'SemiBold', 'Bold')

        Returns:
            QFont: 配置好的字体对象
        """
        from PySide6.QtGui import QFont

        weight_map = {
            "Thin": QFont.Weight.Thin,
            "Light": QFont.Weight.Light,
            "Regular": QFont.Weight.Normal,
            "Medium": QFont.Weight.Medium,
            "SemiBold": QFont.Weight.DemiBold,
            "Bold": QFont.Weight.Bold,
        }

        font = QFont(name, size)
        font.setWeight(weight_map.get(weight, QFont.Weight.Normal))
        return font


class Spacing:
    """
    间距系统 - 基于 4px 基础单位

    使用 8pt 栅格系统，所有间距应为 4 的倍数。

    命名规则:
    - XS: Extra Small (极小)
    - SM: Small (小)
    - MD: Medium (中 - 默认)
    - LG: Large (大)
    - XL: Extra Large (极大)
    - XXL: 2X Large (超大)
    """

    XS = 4  # 极小间距 (行内元素)
    SM = 8  # 小间距 (紧密关联元素)
    MD = 16  # 中间距 (标准间距 - **最常用**)
    LG = 24  # 大间距 (区块之间)
    XL = 32  # 极大间距 (主要区块之间)
    XXL = 48  # 超大间距 (页面级分隔)

    # === 特殊间距 ===
    ZERO = 0  # 无间距
    TIGHT = 2  # 极紧密 (特殊场景)

    # === 组件内边距 (Component Padding) ===
    PADDING_XS = (4, 4)  # (4px, 4px)
    PADDING_SM = (8, 8)  # (8px, 8px)
    PADDING_MD = (16, 16)  # (16px, 16px) - **标准**
    PADDING_LG = (24, 24)  # (24px, 24px)
    PADDING_XL = (32, 32)  # (32px, 32px)


class Radius:
    """
    圆角系统 - 统一的圆角半径

    命名规则:
    - NONE: 无圆角
    - SM: 小圆角 (按钮、输入框)
    - MD: 中圆角 (卡片、面板)
    - LG: 大圆角 (弹窗、对话框)
    - XL: 极大圆角 (特殊容器)
    - FULL: 完全圆角 (胶囊形、头像)
    """

    NONE = 0  # 无圆角 (直角)
    SM = 4  # 小圆角 (按钮、标签、输入框)
    MD = 8  # 中圆角 (**最常用** - 卡片、面板)
    LG = 12  # 大圆角 (弹窗、对话框)
    XL = 16  # 极大圆角 (大型容器)
    XXL = 24  # 超大圆角 (特殊形状)
    FULL = 9999  # 完全圆角/胶囊形 (徽章、标签)


class Shadows:
    """
    阴影系统 - 层级阴影效果

    注意: PySide6 不支持 CSS box-shadow，
    需要使用 QGraphicsDropShadowEffect 实现。

    使用示例:
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(Shadows.SM.blur)
        shadow.setOffset(Shadows.SM.offset_x, Shadows.SM.offset_y)
        shadow.setColor(QColor(Shadows.SM.color))
        widget.setGraphicsEffect(shadow)
    """

    @dataclass
    class ShadowConfig:
        """阴影配置"""

        blur: int  # 模糊半径
        offset_x: int  # X轴偏移
        offset_y: int  # Y轴偏移
        color: str  # 阴影颜色
        opacity: float = 1.0  # 不透明度

    NONE = ShadowConfig(blur=0, offset_x=0, offset_y=0, color="transparent")

    XS = ShadowConfig(blur=2, offset_x=0, offset_y=1, color="rgba(0, 0, 0, 0.05)")

    SM = ShadowConfig(blur=4, offset_x=0, offset_y=2, color="rgba(0, 0, 0, 0.08)")

    MD = ShadowConfig(blur=8, offset_x=0, offset_y=4, color="rgba(0, 0, 0, 0.10)")

    LG = ShadowConfig(blur=16, offset_x=0, offset_y=8, color="rgba(0, 0, 0, 0.12)")

    XL = ShadowConfig(blur=24, offset_x=0, offset_y=16, color="rgba(0, 0, 0, 0.15)")


class Transitions:
    """
    过渡动画系统 - 统一的动画时长和缓动函数

    使用示例:
        animation.setDuration(Transitions.FAST)
        animation.setEasingCurve(Transitions.EASE_OUT)
    """

    # === 动画时长 (Duration) ===
    INSTANT = 100  # 即时 (ms)
    FAST = 150  # 快速 (悬停、焦点)
    NORMAL = 250  # 正常 (展开、切换)
    SLOW = 400  # 慢速 (页面过渡)
    VERY_SLOW = 600  # 很慢 (复杂动画)

    # === 缓动函数 (Easing Curves) ===
    LINEAR = "linear"
    EASE_IN = "ease-in"
    EASE_OUT = "ease-out"
    EASE_IN_OUT = "ease-in-out"
    EASE_OUT_BACK = "cubic-bezier(0.34, 1.56, 0.64, 1)"  # 弹性效果

    # === CSS transition 快捷方式 ===
    @staticmethod
    def transition(property_name: str, duration: int = None, timing: str = None):
        """
        生成 CSS transition 声明

        Args:
            property_name: CSS属性名
            duration: 时长(ms)，默认 NORMAL
            timing: 缓动函数，默认 EASE_OUT

        Returns:
            str: CSS transition 字符串
        """
        d = duration or Transitions.NORMAL
        t = timing or Transitions.EASE_OUT
        return f"{property_name} {d}ms {t}"


class ZIndex:
    """Z-index 层级系统"""

    BASE = 0
    DROPDOWN = 100
    STICKY = 200
    OVERLAY = 300
    MODAL = 400
    POPOVER = 500
    TOOLTIP = 600


# ═══════════════════════════════════════════════════════════
# 便捷别名 (Convenience Aliases)
# ═══════════════════════════════════════════════════════════


class DarkColors:
    """
    深色主题色板 - 工业控制室 24h 运行场景

    基于 Fluent Design 2.0 Dark Theme + 工业监控优化
    """

    # === 文字颜色 ===
    TEXT_PRIMARY = "#E0E0E0"
    TEXT_SECONDARY = "#A0A0A0"
    TEXT_TERTIARY = "#707070"
    TEXT_DISABLED = "#505050"
    TEXT_INVERSE = "#1A1A2E"
    TEXT_LINK = "#4DA6FF"
    TEXT_ON_ACCENT = "#FFFFFF"
    TEXT_ON_DANGER = "#FFFFFF"

    # === 强调色 ===
    ACCENT_PRIMARY = "#4DA6FF"
    ACCENT_SECONDARY = "#8B949E"
    ACCENT_TERTIARY = "#30363D"
    ACCENT_HOVER = "#79B8FF"
    ACCENT_ACTIVE = "#B0D4FF"
    ACCENT_SUBTLE = "#1A3A5C"

    # === 状态色 ===
    STATUS_SUCCESS = "#3FB950"
    STATUS_SUCCESS_BG = "#1A3320"
    STATUS_WARNING = "#D29922"
    STATUS_WARNING_BG = "#3D2E00"
    STATUS_ERROR = "#F85149"
    STATUS_ERROR_BG = "#3D1418"
    STATUS_INFO = "#4DA6FF"
    STATUS_INFO_BG = "#1A3A5C"

    # === 设备状态色 ===
    DEVICE_ONLINE = "#3FB950"
    DEVICE_ONLINE_LIGHT = "#2EA043"
    DEVICE_OFFLINE = "#30363D"
    DEVICE_OFFLINE_LIGHT = "#21262D"
    DEVICE_WARNING = "#D29922"
    DEVICE_WARNING_LIGHT = "#BB8009"
    DEVICE_ERROR = "#F85149"
    DEVICE_ERROR_LIGHT = "#DA3633"
    DEVICE_IDLE = "#4DA6FF"
    DEVICE_IDLE_LIGHT = "#388BFD"

    # === 边框颜色 ===
    BORDER_DEFAULT = "#30363D"
    BORDER_FOCUS = "#4DA6FF"
    BORDER_HOVER = "#484F58"
    BORDER_STRONG = "#6E7681"
    BORDER_SUBTLE = "#21262D"
    BORDER_TRANSPARENT = "transparent"

    # === 背景颜色 ===
    BG_PRIMARY = "#1A1A2E"
    BG_SECONDARY = "#16213E"
    BG_TERTIARY = "#0F3460"
    BG_HOVER = "#1E2A4A"
    BG_ACTIVE = "#2D3A5A"
    BG_DISABLED = "#21262D"
    BG_OVERLAY = "rgba(0, 0, 0, 0.6)"
    BG_MODAL = "#1A1A2E"
    BG_TOOLTIP = "#E0E0E0"
    BG_BASE = "#0F1629"

    # === 特殊用途色 ===
    SHADOW_COLOR = "rgba(0, 0, 0, 0.30)"
    DIVIDER = "#21262D"

    # === 图表专用色 ===
    CHART_BACKGROUND = "#0F1629"
    CHART_GRID = "rgba(255, 255, 255, 0.06)"
    CHART_FOREGROUND = "#A0A0A0"
    CHART_COLORMAP = "inferno"
    CHART_WAVEFORM = "#4DA6FF"
    CHART_SPECTRUM = "#3FB950"

    # === ISA-18.2 报警色 ===
    ALARM_UNACKNOWLEDGED = "#FF4444"
    ALARM_ACKNOWLEDGED = "#FF8800"
    ALARM_RETURN_NORMAL = "#3FB950"


class ThemeManager:
    """主题管理器 - 运行时切换亮色/深色主题"""

    _instance = None
    _is_dark = False

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def is_dark(cls) -> bool:
        return cls._is_dark

    @classmethod
    def set_dark(cls, dark: bool = True) -> None:
        """切换主题"""
        cls._is_dark = dark
        cls._apply_theme()

    @classmethod
    def toggle(cls) -> None:
        """切换主题"""
        cls.set_dark(not cls._is_dark)

    @classmethod
    def _apply_theme(cls) -> None:
        """应用主题到 DT.C"""
        if cls._is_dark:
            for attr in dir(DarkColors):
                if attr.isupper() and not attr.startswith("_"):
                    setattr(Colors, attr, getattr(DarkColors, attr))
        else:
            # 恢复默认亮色（重新导入模块级常量）
            _restore_light_colors()

    @classmethod
    def get_colors(cls):
        """获取当前主题色板"""
        return DarkColors if cls._is_dark else Colors


# 保存亮色默认值，用于主题切换恢复
_LIGHT_DEFAULTS = {}
for _attr in dir(Colors):
    if _attr.isupper() and not _attr.startswith("_"):
        _LIGHT_DEFAULTS[_attr] = getattr(Colors, _attr)


def _restore_light_colors() -> None:
    """恢复亮色主题默认值"""
    for attr, value in _LIGHT_DEFAULTS.items():
        setattr(Colors, attr, value)


class DT:
    """
    Design Tokens 便捷访问类

    提供简短的命名空间访问所有设计令牌。

    使用示例:
        from ui.design_tokens import DT

        # 颜色
        DT.C.TEXT_PRIMARY
        DT.C.ACCENT_PRIMARY

        # 字体
        DT.T.BODY
        DT.T.TITLE_MEDIUM

        # 间距
        DT.S.MD
        DT.S.PADDING_MD

        # 圆角
        DT.R.MD
    """

    C = Colors
    T = Typography
    S = Spacing
    R = Radius
    SH = Shadows
    TR = Transitions
    Z = ZIndex
    Theme = ThemeManager

    # 字号快捷访问 (纯数字，用于内联样式)
    class FS:
        """Font Sizes - 纯数字字号令牌"""
        # 标题
        TITLE_XLARGE = 28
        TITLE_LARGE = 20
        TITLE_MEDIUM = 16
        TITLE_SMALL = 14
        TITLE_XSMALL = 12
        # 正文
        BODY_LARGE = 14
        BODY = 13
        BODY_SMALL = 12
        BODY_XSMALL = 11
        # 标签/说明
        LABEL = 12
        CAPTION = 11
        CAPTION_SMALL = 10
        # 数据显示
        DATA_LARGE = 28
        DATA_MEDIUM = 22
        DATA = 18
        DATA_SMALL = 14
        # 按钮
        BTN_LARGE = 36
        BTN_MEDIUM = 32
        BTN_SMALL = 28
        # 工具栏
        TOOLBAR_BTN = 32


# ═══════════════════════════════════════════════════════════
# 预定义样式字符串 (Predefined Style Strings)
# ═══════════════════════════════════════════════════════════


class Stylesheets:
    """
    预定义的常用样式表片段

    提供常用的样式组合，避免重复编写。
    """

    @staticmethod
    def label(color: str = Colors.TEXT_PRIMARY, font_size: int = None, font_weight: str = None):
        """基础标签样式"""
        style = f"color: {color};"
        if font_size:
            style += f" font-size: {font_size}px;"
        if font_weight:
            style += f" font-weight: {font_weight};"
        return style

    @staticmethod
    def button(base_color: str = Colors.ACCENT_PRIMARY, text_color: str = Colors.TEXT_INVERSE, radius: int = Radius.MD):
        """基础按钮样式"""
        return f"""
            QPushButton {{
                background-color: {base_color};
                color: {text_color};
                border: none;
                border-radius: {radius}px;
                padding: 8px 16px;
                font-size: 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {Colors.adjust_color(base_color, -20)};
            }}
            QPushButton:pressed {{
                background-color: {Colors.adjust_color(base_color, -30)};
            }}
            QPushButton:disabled {{
                background-color: {Colors.BG_DISABLED};
                color: {Colors.TEXT_DISABLED};
            }}
        """

    @staticmethod
    def card(radius: int = Radius.LG, border: bool = True):
        """卡片容器样式"""
        border_style = f"border: 1px solid {Colors.BORDER_DEFAULT};" if border else "border: none;"
        return f"""
            QFrame#cardContainer {{
                background-color: {Colors.BG_PRIMARY};
                {border_style}
                border-radius: {radius}px;
                padding: {Spacing.MD}px;
            }}
            QFrame#cardContainer:hover {{
                border-color: {Colors.BORDER_HOVER};
                background-color: {Colors.BG_HOVER};
            }}
        """

    @staticmethod
    def input_field():
        """输入框样式"""
        return f"""
            QLineEdit, QComboBox {{
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Radius.SM}px;
                padding: 6px 12px;
                background-color: {Colors.BG_PRIMARY};
                selection-background-color: {Colors.ACCENT_SUBTLE};
                font-size: {Typography.BODY[1]}px;
            }}
            QLineEdit:focus, QComboBox:focus, QComboBox:on {{
                border-color: {Colors.BORDER_FOCUS};
                border-width: 2px;
            }}
        """

    @staticmethod
    def table():
        """表格样式"""
        return f"""
            QTableWidget {{
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Radius.MD}px;
                gridline-color: {Colors.DIVIDER};
                selection-background-color: {Colors.ACCENT_SUBTLE};
                font-size: {Typography.BODY[1]}px;
            }}
            QTableWidget::item {{
                padding: {Spacing.SM}px;
            }}
            QTableWidget::item:selected {{
                background-color: {Colors.ACCENT_SUBTLE};
                color: {Colors.ACCENT_PRIMARY};
            }}
            QHeaderView::section {{
                background-color: {Colors.BG_SECONDARY};
                color: {Colors.TEXT_SECONDARY};
                padding: {Spacing.SM}px;
                border: none;
                border-bottom: 2px solid {Colors.BORDER_DEFAULT};
                font-weight: 600;
            }}
        """

    # ── 统一样式工厂方法 (页面复用) ──────────────────

    @staticmethod
    def sheet_card(object_name: str = "cardContainer") -> str:
        """卡片容器样式 — 所有页面统一使用"""
        return f"""
            QFrame#{object_name} {{
                background: {Colors.BG_PRIMARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Radius.LG}px;
            }}
        """

    @staticmethod
    def sheet_table() -> str:
        """表格样式 — 数据表格统一使用"""
        return f"""
            QTableWidget {{
                background: {Colors.BG_PRIMARY};
                alternate-background-color: {Colors.BG_SECONDARY};
                border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: {Radius.MD}px;
                font-size: 11px; outline: none;
            }}
            QTableWidget::item {{ padding: 4px 6px; border-bottom: 1px solid {Colors.DIVIDER}; }}
            QHeaderView::section {{
                background: {Colors.BG_SECONDARY}; color: {Colors.TEXT_SECONDARY};
                border: none; border-bottom: 2px solid {Colors.BORDER_DEFAULT};
                padding: 4px; font-size: 10px; font-weight: 600;
            }}
        """

    @staticmethod
    def sheet_combo(min_width: int = 80) -> str:
        """下拉框样式"""
        return f"""
            QComboBox {{
                background: {Colors.BG_PRIMARY}; border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: 4px; padding: 4px 8px; font-size: 12px;
                color: {Colors.TEXT_PRIMARY}; min-width: {min_width}px;
            }}
            QComboBox:hover {{ border-color: {Colors.BORDER_HOVER}; }}
            QComboBox QAbstractItemView {{
                background: {Colors.BG_PRIMARY}; border: 1px solid {Colors.BORDER_DEFAULT};
                selection-background-color: {Colors.ACCENT_SUBTLE};
            }}
        """

    @staticmethod
    def sheet_spin() -> str:
        """数值输入框样式"""
        return f"""
            QSpinBox, QDoubleSpinBox {{
                background: {Colors.BG_PRIMARY}; border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: 4px; padding: 4px 8px; font-size: 12px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {Colors.BORDER_FOCUS}; }}
            QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {Colors.BORDER_HOVER}; }}
        """

    @staticmethod
    def sheet_input() -> str:
        """文本输入框样式"""
        return f"""
            QLineEdit {{
                background: {Colors.BG_PRIMARY}; border: 1px solid {Colors.BORDER_DEFAULT};
                border-radius: 4px; padding: 4px 8px; font-size: 12px;
                color: {Colors.TEXT_PRIMARY};
            }}
            QLineEdit:focus {{ border-color: {Colors.BORDER_FOCUS}; }}
            QLineEdit:hover {{ border-color: {Colors.BORDER_HOVER}; }}
        """

    @staticmethod
    def section_header(text_color: str = Colors.TEXT_PRIMARY) -> str:
        """区域标题标签样式"""
        return f"color: {text_color};"

    @staticmethod
    def sub_header(text_color: str = Colors.TEXT_TERTIARY) -> str:
        """次级标签样式"""
        return f"color: {text_color}; font-size: 13px;"


@staticmethod
def adjust_color(hex_color: str, amount: int) -> str:
    """
    调整颜色亮度

    Args:
        hex_color: 十六进制颜色值 (如 "#FF0000")
        amount: 调整量 (-255 到 255)，正数变亮，负数变暗

    Returns:
        str: 调整后的十六进制颜色值
    """
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)

    r = max(0, min(255, r + amount))
    g = max(0, min(255, g + amount))
    b = max(0, min(255, b + amount))

    return f"#{r:02x}{g:02x}{b:02x}"


Colors.adjust_color = staticmethod(adjust_color)

# 绑定到 DT 便捷别名（必须在 Stylesheets 类定义之后）
DT.sheet = Stylesheets


if __name__ == "__main__":
    print("[OK] Design Tokens 系统加载成功")
    print(f"\n[STATS] 统计信息:")
    print(f"  - 颜色常量: {len([attr for attr in dir(Colors) if not attr.startswith('_')])} 个")
    print(f"  - 字体样式: {len([attr for attr in dir(Typography) if not attr.startswith('_') and attr.isupper()])} 个")
    print(f"  - 间距单位: {len([attr for attr in dir(Spacing) if not attr.startswith('_') and attr.isupper()])} 个")
    print(f"  - 圆角半径: {len([attr for attr in dir(Radius) if not attr.startswith('_')])} 个")
    print(f"\n[USAGE] 示例用法:")
    print(f'  DT.C.TEXT_PRIMARY → "{DT.C.TEXT_PRIMARY}"')
    print(f"  DT.T.BODY → {DT.T.BODY}")
    print(f"  DT.S.MD → {DT.S.MD}px")
    print(f"  DT.R.MD → {DT.R.MD}px")
