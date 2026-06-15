"""
UI层 - PySide6 Widgets用户界面

包含:
    - styles/: 样式系统 (主题/色彩/QSS)
    - main_window: 系统主窗口
    - dialogs/: 对话框集合
    - widgets/: 自定义可视化组件
"""

from .main_window import PDMainWindow

__all__ = ["PDMainWindow"]
