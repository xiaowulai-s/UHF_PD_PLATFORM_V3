# -*- coding: utf-8 -*-
"""
结构化日志系统 (优化版 - 异步批量数据库写入)
Structured Logging System with Async Batch DB Writer
"""

import logging
import io
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import structlog
except ImportError:
    structlog = None  # type: ignore
from sqlalchemy.exc import SQLAlchemyError

from core.data.models import DatabaseManager, SystemLogModel


def _create_console_handler(log_level: str) -> logging.Handler:
    """UTF-8编码控制台处理器，兼容所有终端环境"""
    try:
        utf8_stream = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        utf8_stream = sys.stdout
    handler = logging.StreamHandler(utf8_stream)
    handler.setLevel(getattr(logging, log_level.upper()))
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
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
        except Exception as e:
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
) -> structlog.stdlib.BoundLogger:
    """
    配置结构化日志系统

    Args:
        log_level: 日志级别 DEBUG/INFO/WARNING/ERROR/CRITICAL
        log_file: 日志文件路径
        db_manager: 数据库管理器（用于写入数据库）
        console_output: 是否输出到控制台
    """

    handlers: list = []

    if console_output:
        handlers.append(_create_console_handler(log_level))

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
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


class LoggerMixin:
    """日志混入类 - 为类提供结构化日志功能"""

    @property
    def logger(self) -> structlog.stdlib.BoundLogger:
        """获取日志记录器"""
        return structlog.get_logger(self.__class__.__name__)
