"""无界面数据内核进程 — Electron 前端的 sidecar 启动入口。

不创建任何窗口，只做: 采集/模拟 -> 处理管线 -> PDDataBus -> WebSocket 推流。

    python sidecar.py --sim-type uhf --fps 20          # 跑起来，打印端口与 token
    python sidecar.py --bench --duration 30            # 压测并在退出前汇总吞吐

用途是验证跨进程边界在真实管线下撑不撑得住，因此刻意不启用存储写入
（--with-storage 才打开），避免压测把 data/monitor.db 灌满垃圾数据。
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.protocol import SidecarServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PD 处理内核 sidecar（无界面）")
    parser.add_argument("--port", type=int, default=0, help="监听端口，0=随机（默认）")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="只允许回环地址")
    parser.add_argument("--sim-type", choices=["uhf", "ae"], default="uhf")
    parser.add_argument(
        "--pd-type",
        choices=["corona", "surface", "internal", "floating", "burst", "continuous", "mixed"],
        default="internal",
    )
    parser.add_argument("--fps", type=int, default=20, help="模拟器帧率")
    parser.add_argument("--hardware", action="store_true", help="用配置里的真实设备而非模拟器")
    parser.add_argument("--db", type=str, default="data/monitor.db", help="SQLite 路径，供只读查询 RPC 使用")
    parser.add_argument("--no-db", action="store_true", help="完全不接数据库（纯推流压测）")
    parser.add_argument("--with-storage", action="store_true", help="启用 SQLite 批量写入（默认只读）")
    parser.add_argument("--bench", action="store_true", help="周期性打印吞吐统计")
    parser.add_argument("--duration", type=float, default=0.0, help="运行秒数后退出，0=一直运行")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO")
    return parser.parse_args()


def _build_services(args: argparse.Namespace, logger: logging.Logger):
    from core.foundation.signal_bus import PDDataBus
    from core.services.acquisition_service import AcquisitionService

    bus = PDDataBus.instance()
    acquisition = AcquisitionService(sample_rate=100_000_000, waveform_points=4096)

    alarm = None
    if not args.hardware:
        from core.services.alarm_service import PDAlarmService

        alarm = PDAlarmService()

    db_manager = None
    storage = None
    if not args.no_db:
        from core.data import DatabaseManager
        from core.services.storage_service import PDStorageService

        db_manager = DatabaseManager(args.db)
        storage = PDStorageService(db_manager=db_manager)
        # 只读模式下也构造 storage（query_* 走它），但不启动刷写线程
        if args.with_storage:
            storage.start()
            logger.info("SQLite 写入已启用: %s", args.db)
        else:
            logger.info("SQLite 只读模式: %s（未启动写入线程）", args.db)

    from core.foundation.data_source_manager import DataSourceManager

    sources = DataSourceManager(
        acquisition_service=acquisition,
        data_bus=bus,
        db_manager=db_manager,
        alarm_service=alarm,
    )
    return bus, acquisition, sources, storage, db_manager, alarm


def main() -> int:
    args = parse_args()

    from core.utils.logger import setup_logging

    setup_logging(log_level=args.log_level, log_file="logs/sidecar.log")
    logger = logging.getLogger("sidecar")

    from PySide6.QtCore import QCoreApplication, QTimer

    app = QCoreApplication(sys.argv)

    from core.protocol import SidecarServer
    from core.protocol.storage_rpc import register_storage_rpc

    bus, acquisition, sources, storage, db_manager, alarm = _build_services(args, logger)
    server = SidecarServer(bus)
    port, token = server.start(host=args.host, port=args.port)

    def start_simulator(params: dict) -> int:
        sources.add_simulator_source(
            params.get("sim_type", args.sim_type),
            params.get("pd_type", args.pd_type),
            int(params.get("fps", args.fps)),
        )
        return sources.start_all()

    server.register_rpc("acquisition.stats", lambda _p: acquisition.get_stats())
    server.register_rpc("acquisition.start_simulator", start_simulator)
    server.register_rpc("acquisition.stop", lambda _p: (sources.stop_all(), acquisition.stop()))

    methods = register_storage_rpc(server, db_manager=db_manager, storage=storage)
    if methods:
        logger.info("只读查询 RPC 已注册: %s", ", ".join(methods))

    from core.protocol.write_rpc import register_write_rpc

    writes = register_write_rpc(server, db_manager=db_manager, acquisition=acquisition, alarm=alarm)
    logger.info("写入 RPC 共 %d 个", len(writes))

    from core.protocol.log_stream import attach as attach_log_stream
    from core.protocol.stats_push import StatsPusher

    log_handler = attach_log_stream(server, level=getattr(logging, args.log_level))
    pusher = StatsPusher(
        server,
        acquisition=acquisition,
        alarm=alarm,
        sources=sources,
        storage=storage,
    )
    pusher.start()

    if args.hardware:
        from ui.controller_config import load_hardware_config

        configs = load_hardware_config(db_manager)
        for cfg in configs:
            sources.add_hardware_source(cfg)
        started = sources.start_all()
        if started == 0:
            logger.critical("--hardware 模式：无设备在线")
            server.stop()
            return 1
        logger.info("硬件模式: %d 个数据源在线", started)
    else:
        sources.add_simulator_source(args.sim_type, args.pd_type, args.fps)
        sources.start_all()
        logger.info("模拟器: %s/%s @ %d FPS", args.sim_type, args.pd_type, args.fps)

    print(
        f"\n  sidecar ready\n"
        f"  stream    ws://{args.host}:{port}/stream\n"
        f"  ctrl      ws://{args.host}:{port}/ctrl\n"
        f"  token     {token}\n",
        flush=True,
    )

    stopped = {"done": False}

    def shutdown(reason: str) -> None:
        if stopped["done"]:
            return
        stopped["done"] = True
        logger.info("sidecar 退出中 (%s)", reason)
        pusher.stop()
        log_handler.detach()
        sources.stop_all()
        acquisition.stop()
        if storage:
            storage.stop()
        _report(server, acquisition, logger)
        server.stop()
        # 不关引擎会残留 monitor.db-wal / -shm 边车文件
        if db_manager is not None:
            db_manager.close()
        app.quit()

    if args.bench:

        def tick() -> None:
            stats = acquisition.get_stats()
            diag = server.diagnostics()
            logger.info(
                "采集 frames=%d waveforms=%d events=%d | 推流 sent=%d dropped=%d clients=%d",
                stats.get("frames_received", 0),
                stats.get("waveforms_processed", 0),
                stats.get("pd_events_detected", 0),
                diag["sent_frames"],
                diag["dropped_frames"],
                diag["clients"],
            )

        timer = QTimer()
        timer.setInterval(5000)
        timer.timeout.connect(tick)
        timer.start()

    if args.duration > 0:
        QTimer.singleShot(int(args.duration * 1000), lambda: shutdown("--duration 到点"))

    signal.signal(signal.SIGINT, lambda *_: shutdown("SIGINT"))
    # Python 信号处理器只在主解释器回到字节码时执行，需要定时唤醒事件循环
    wake = QTimer()
    wake.setInterval(200)
    wake.timeout.connect(lambda: None)
    wake.start()

    try:
        app.exec()
    except KeyboardInterrupt:
        shutdown("KeyboardInterrupt")
    return 0


def _report(server: SidecarServer, acquisition, logger: logging.Logger) -> None:
    stats = acquisition.get_stats()
    diag = server.diagnostics()
    logger.info(
        "总计: 采集波形 %d 帧 / 检出事件 %d 个；推流 %d 帧 / 因客户端滞后丢弃 %d 帧",
        stats.get("waveforms_processed", 0),
        stats.get("pd_events_detected", 0),
        diag["sent_frames"],
        diag["dropped_frames"],
    )


if __name__ == "__main__":
    sys.exit(main())
