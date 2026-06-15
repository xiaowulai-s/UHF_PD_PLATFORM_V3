"""
超高频超声波局部放电检测系统 - 程序入口

启动流程:
    1. 初始化日志系统
    2. 初始化数据库 (SQLite WAL)
    3. 创建 QApplication
    4. 应用主题样式
    5. 启动 PD 主窗口
    6. 初始化系统控制器
    7. 可选启动模拟器（用于无硬件时的演示）
用法:
    python main.py                          # 正常模式
    python main.py --simulator              # 模拟器模式（无需 FPGA）
    python main.py --simulator --sim-type ae  # AE 模拟器模式
    python main.py --simulator --pd-type corona  # 指定局放类型
"""

# ── 编码修复（必须在所有 import 之前）──
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Windows 控制台强制 UTF-8
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="超高频超声波局部放电检测系统 v1.0.7")
    parser.add_argument("--simulator", action="store_true", help="启动数据模拟器（无需 FPGA 硬件）")
    parser.add_argument(
        "--pd-type",
        type=str,
        default="internal",
        choices=["corona", "surface", "internal", "floating"],
        help="局放类型 (默认: internal)",
    )
    parser.add_argument("--fps", type=int, default=20, help="模拟器帧率 (默认: 20)")
    parser.add_argument(
        "--sim-type",
        type=str,
        default="uhf",
        choices=["uhf", "ae"],
        help="模拟器类型: uhf=超高频, ae=超声波 (默认: uhf)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)",
    )
    return parser.parse_args()


def main() -> int:
    """PD 检测系统主入口"""
    args = parse_args()

    from core.utils.logger import get_logger, setup_logging

    setup_logging(log_level=args.log_level, log_file="logs/system/monitor.log")
    logger = get_logger("system")
    logger.info("=" * 50)
    logger.info("超高频超声波局部放电检测系统 v1.0.7 启动中..")
    logger.info("=" * 50)
    logger.info("启动参数: simulator=%s, sim_type=%s, pd_type=%s, fps=%d",
                args.simulator, args.sim_type, args.pd_type, args.fps)

    _PROJECT_ROOT = Path(__file__).resolve().parent
    config_path = _PROJECT_ROOT / "config" / "default_config.json"

    config = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            logger.info("PD 系统配置加载完成: %s", config_path)
        except Exception as e:
            logger.warning("PD 配置加载失败，使用默认配置: %s", e)
    else:
        logger.info("PD 配置文件不存在，使用默认配置")

    from core.data import DatabaseManager

    try:
        db_path = config.get("database", {}).get("path", "data/monitor.db")
        db_manager = DatabaseManager(db_path)
        logger.info("PD 数据库初始化完成: %s", db_path)
    except Exception as e:
        logger.critical("数据库初始化失败: %s", e)
        return 1

    # OpenGL 加速（可选，需 PyOpenGL）
    try:
        import pyqtgraph as pg

        pg.setConfigOptions(useOpenGL=True, antialias=True)
        logger.info("PyQtGraph OpenGL 加速已启用")
    except Exception:
        logger.debug("OpenGL 加速不可用（可选依赖），使用 CPU 渲染")

    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    PD_VERSION = "1.0.7"
    app.setApplicationName("超高频超声波局部放电检测系统")
    app.setApplicationVersion(PD_VERSION)
    app.setOrganizationName("UHF-AE-PD-Detector")
    app.setWindowIcon(QIcon("assets/icons/ems.png"))

    # 应用主题
    from ui.theme_manager import ThemeManager

    theme_manager = ThemeManager()
    theme_manager.apply_theme()

    from ui.main_window import PDMainWindow

    window = PDMainWindow(db_manager=db_manager)

    # 窗口状态恢复
    settings = QSettings("UHF-PD-Monitor", "PDMainWindow")
    geometry = settings.value("geometry")
    if geometry:
        window.restoreGeometry(geometry)
    state = settings.value("windowState")
    if state:
        window.restoreState(state)

    window.show()

    # 初始化系统控制器
    from ui.controller import PDSystemController

    controller = PDSystemController(db_manager=db_manager, parent=window)
    controller.initialize(pages=window.pages)
    window.set_controller(controller)

    # 可选启动模拟器
    if args.simulator:
        controller.start_simulator(
            pd_type=args.pd_type, fps=args.fps, sim_type=args.sim_type)
        logger.info("数据模拟器已启动: type=%s/%s, %d FPS",
                    args.sim_type, args.pd_type, args.fps)

    logger.info("PD 检测系统启动完成")

    try:
        exit_code = app.exec()
    except KeyboardInterrupt:
        exit_code = 0

    # 清理
    controller.shutdown()
    logger.info("PD 检测系统关闭 (exit_code=%d)", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
