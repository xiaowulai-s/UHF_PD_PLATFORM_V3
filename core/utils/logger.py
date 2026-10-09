# -*- coding: utf-8 -*-
"""
结构化日志系统 (优化版 - 异步批量数据库写入)
Structured Logging System with Async Batch DB Writer
"""

from __future__ import annotations

import io
import logging
import queue
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

try:
    import structlog
except ImportError:
    structlog = None  # type: ignore

if TYPE_CHECKING:
    from core.data.models import DatabaseManager

logger = logging.getLogger(__name__)


def _create_console_handler(log_level: str) -> logging.Handler:
    """UTF-8编码控制台处理器，兼容所有终端环境"""
    try:
        utf8_stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        utf8_stream = sys.stdout
    handler = logging.StreamHandler(utf8_stream)
    handler.setLevel(getattr(logging, log_level.upper()))
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    return handler


class AsyncDatabaseLogHandler(logging.Handler):
    """异步批量数据库日志处理器 - 高性能版本"""

    def __init__(
        self,
        db_manager: DatabaseManager,
        batch_size: int = 10,
        flush_interval: float = 5.0,
        max_queue_size: int = 1000,
    ):
        super().__init__()
        self._db_manager = db_manager
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._running = True

        # 启动后台写入线程
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name="DBLogWriter",
            daemon=True,
        )
        self._flush_thread.start()

        logger = logging.getLogger(__name__)
        logger.info(
            "AsyncDatabaseLogHandler initialized: batch_size=%d, interval=%.1fs",
            batch_size,
            flush_interval,
        )

    def emit(self, record: logging.LogRecord) -> None:
        """将日志记录放入队列（非阻塞）"""
        try:
            exception_info = None
            if record.exc_info:
                try:
                    exception_info = self.formatException(record.exc_info)
                except Exception:
                    logging.getLogger(__name__).debug("格式化异常信息失败")
                    exception_info = "无法格式化异常信息"

            log_entry_data = {
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
                "exception": exception_info,
                "timestamp": datetime.now(),
            }

            # 非阻塞放入队列，队列满时丢弃最旧的
            try:
                self._queue.put_nowait(log_entry_data)
            except queue.Full:
                # 队列满时丢弃并警告
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(log_entry_data)
                except queue.Empty:
                    pass

        except Exception as e:
            logging.getLogger(__name__).error("AsyncDatabaseLogHandler emit失败: %s", e, exc_info=True)
            self.handleError(record)

    def _flush_loop(self):
        """后台刷新循环"""
        batch = []
        last_flush_time = time.monotonic()

        while self._running or not self._queue.empty():
            try:
                # 等待新数据或超时
                try:
                    entry = self._queue.get(timeout=1.0)
                    batch.append(entry)
                except queue.Empty:
                    pass

                current_time = time.monotonic()

                # 检查是否需要刷新
                should_flush = len(batch) >= self._batch_size or (
                    batch and (current_time - last_flush_time) >= self._flush_interval
                )

                if should_flush and batch:
                    self._write_batch(batch)
                    batch = []
                    last_flush_time = current_time

            except Exception as e:
                logging.getLogger(__name__).exception("刷新循环发生错误: %s", e)
                time.sleep(1.0)  # 错误后等待避免风暴

        # 最终刷新剩余数据
        if batch:
            self._write_batch(batch)

    def _write_batch(self, batch: list[dict]):
        """批量写入数据库"""
        if not batch:
            return

        # 这两个符号只在本函数用到，所以放函数内：logger 被 core/ 到处 import，
        # 顶层引 sqlalchemy 会让纯推流模式(--no-db)也常驻 37 MB。
        # 签名里的 DatabaseManager 不需要运行时符号——本文件有 __future__.annotations。
        from sqlalchemy.exc import SQLAlchemyError

        from core.data.models import SystemLogModel

        session = None
        try:
            session = self._db_manager.get_session()

            entries = []
            for data in batch:
                entry = SystemLogModel(
                    level=data["level"],
                    logger=data["logger"],
                    message=data["message"],
                    module=data["module"],
                    function=data["function"],
                    line=data["line"],
                    exception=data["exception"],
                )
                entries.append(entry)

            session.add_all(entries)
            session.commit()

        except SQLAlchemyError as e:
            logging.getLogger(__name__).warning("批量数据库写入失败(回滚 %d 条): %s", len(batch), e)
            if session:
                session.rollback()
        except (ValueError, TypeError, AttributeError, RuntimeError, OSError) as e:
            logging.getLogger(__name__).error("批量写入时发生未预期错误(%d 条): %s", len(batch), e, exc_info=True)
            if session:
                session.rollback()
        finally:
            if session:
                session.close()

    def close(self):
        """关闭处理器，确保所有日志已写入"""
        self._running = False
        if self._flush_thread.is_alive():
            self._flush_thread.join(timeout=10.0)
            if self._flush_thread.is_alive():
                logging.getLogger(__name__).warning("DB日志写入线程未能正常退出")
        super().close()


class DatabaseLogHandler(AsyncDatabaseLogHandler):
    """向后兼容的别名"""


def _render_event_dict(_, __, event_dict):
    """渲染事件字典为可读字符串，避免 JSON repr 编码问题"""
    event = event_dict.pop("event", "")
    extras = []
    for k, v in sorted(event_dict.items()):
        if k not in ("logger", "level", "timestamp") and v:
            extras.append(f"{k}={v}")
    if extras:
        return f"{event} | {' '.join(extras)}"
    return event


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    db_manager: Optional[DatabaseManager] = None,
    console_output: bool = True,
    log_rotation: str = "size",  # "size" or "time"
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    rotation_interval: str = "midnight",  # 用于时间轮转
) -> structlog.stdlib.BoundLogger:
    """
    配置结构化日志系统

    Args:
        log_level: 日志级别 DEBUG/INFO/WARNING/ERROR/CRITICAL
        log_file: 日志文件路径
        db_manager: 数据库管理器（用于写入数据库）
        console_output: 是否输出到控制台
        log_rotation: 日志轮转方式 ("size"-按大小, "time"-按时间)
        max_bytes: 按大小轮转时的最大文件大小（字节）
        backup_count: 保留的备份文件数量
        rotation_interval: 按时间轮转的间隔 ("S"-秒, "H"-小时, "D"-天, "midnight"-午夜, "W0"-周一)
    """

    handlers: list = []

    if console_output:
        handlers.append(_create_console_handler(log_level))

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

        if log_rotation == "time":
            # 按时间轮转
            file_handler = TimedRotatingFileHandler(
                log_path, when=rotation_interval, backupCount=backup_count, encoding="utf-8"
            )
            # 时间轮转使用不同文件名格式
            file_handler.suffix = "%Y-%m-%d_%H-%M-%S.log"
        else:
            # 按大小轮转（默认）
            file_handler = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")

        file_handler.setLevel(getattr(logging, log_level.upper()))
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)

    if db_manager:
        db_handler = AsyncDatabaseLogHandler(db_manager)
        db_handler.setLevel(logging.WARNING)
        handlers.append(db_handler)

    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        handlers=handlers,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            _render_event_dict,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    return structlog.get_logger()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """获取结构化日志记录器"""
    return structlog.get_logger(name)


def archive_logs(log_dir: str, archive_dir: str, days_to_keep: int = 30, compress: bool = True) -> Dict[str, Any]:
    """
    归档旧日志文件

    Args:
        log_dir: 日志目录
        archive_dir: 归档目录
        days_to_keep: 保留最近N天的日志在原位，其余归档
        compress: 是否压缩归档文件

    Returns:
        归档统计信息
    """
    import shutil

    stats = {
        "archived": 0,
        "deleted": 0,
        "errors": 0,
        "total_size_bytes": 0,
    }

    try:
        log_path = Path(log_dir)
        archive_path = Path(archive_dir)
        archive_path.mkdir(parents=True, exist_ok=True)

        if not log_path.exists():
            logger.warning("日志目录不存在: %s", log_dir)
            return stats

        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_timestamp = cutoff_date.timestamp()

        # 查找所有日志文件
        log_files = []
        for pattern in ["*.log", "*.log.*"]:
            log_files.extend(log_path.glob(pattern))

        for log_file in log_files:
            try:
                # 检查文件修改时间
                file_mtime = log_file.stat().st_mtime

                if file_mtime < cutoff_timestamp:
                    # 文件超过保留天数，归档
                    if compress:
                        import gzip

                        archive_name = archive_path / f"{log_file.name}.gz"
                        with open(log_file, "rb") as f_in:
                            with gzip.open(archive_name, "wb") as f_out:
                                f_out.writelines(f_in)
                        log_file.unlink()
                        stats["archived"] += 1
                        stats["total_size_bytes"] += archive_name.stat().st_size
                    else:
                        archive_name = archive_path / log_file.name
                        shutil.move(str(log_file), str(archive_name))
                        stats["archived"] += 1
                        stats["total_size_bytes"] += archive_name.stat().st_size

            except (OSError, PermissionError, IOError, shutil.Error, ValueError) as e:
                logger.error("归档日志文件失败 %s: %s", log_file, e, exc_info=True)
                stats["errors"] += 1

        logger.info(
            "日志归档完成: 归档%d个, 删除%d个, 错误%d个, 总大小%.2f MB",
            stats["archived"],
            stats["deleted"],
            stats["errors"],
            stats["total_size_bytes"] / (1024 * 1024),
        )

    except Exception as e:
        logger.error("日志归档失败: %s", e, exc_info=True)
        stats["errors"] += 1

    return stats


def cleanup_old_archives(archive_dir: str, days_to_keep: int = 90) -> int:
    """
    清理过期的归档日志

    Args:
        archive_dir: 归档目录
        days_to_keep: 保留最近N天的归档

    Returns:
        删除的文件数
    """
    deleted_count = 0

    try:
        archive_path = Path(archive_dir)
        if not archive_path.exists():
            return 0

        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_timestamp = cutoff_date.timestamp()

        for archive_file in archive_path.glob("*.gz"):
            try:
                if archive_file.stat().st_mtime < cutoff_timestamp:
                    archive_file.unlink()
                    deleted_count += 1
                    logger.debug("删除过期归档: %s", archive_file)
            except (OSError, PermissionError, FileNotFoundError, ValueError) as e:
                logger.error("删除归档失败 %s: %s", archive_file, e, exc_info=True)

        if deleted_count > 0:
            logger.info("清理旧归档完成: 删除%d个文件", deleted_count)

    except Exception as e:
        logger.error("清理归档失败: %s", e, exc_info=True)

    return deleted_count


class LoggerMixin:
    """日志混入类 - 为类提供结构化日志功能"""

    @property
    def logger(self) -> structlog.stdlib.BoundLogger:
        """获取日志记录器"""
        return structlog.get_logger(self.__class__.__name__)
