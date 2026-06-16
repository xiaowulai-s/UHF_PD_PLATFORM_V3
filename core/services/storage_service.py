# -*- coding: utf-8 -*-
"""
PD 数据存储服务 - PDStorageService

定期将 PD 监测数据持久化到 SQLite 数据库。
基于 DataPersistenceService 的模式。

职责:
1. 局放事件批量持久化
2. 趋势数据定期聚合存储 (1min/5min/15min)
3. 历史数据查询接口
4. 数据保留策略 (自动清理过期数据)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.data.monitor_models import PDAlarmModel, PDEventModel, PDTrendDataModel

logger = logging.getLogger(__name__)


class PDStorageService:
    """
    PD 数据存储服务

    缓存 PD 事件和趋势数据，定期批量写入数据库。
    减少频繁的数据库写入操作。

    Args:
        db_manager: 数据库管理器
        flush_interval: 批量写入间隔 (秒)
        batch_size: 批量写入大小
        retention_days: 数据保留天数
    """

    def __init__(
        self,
        db_manager=None,
        flush_interval: float = 5.0,
        batch_size: int = 100,
        retention_days: int = 365,
    ):
        self._db_manager = db_manager
        self._flush_interval = flush_interval
        self._batch_size = batch_size
        self._retention_days = retention_days

        # 事件缓存队列
        self._event_queue: deque = deque(maxlen=10000)
        self._trend_queue: deque = deque(maxlen=5000)

        # 重试计数
        self._retry_counts: Dict[str, int] = {}
        self._max_retries = 5
        self._dead_letter: deque = deque(maxlen=1000)

        # 趋势聚合缓存
        self._trend_buffer: Dict[str, List[dict]] = {}
        self._last_trend_time: Dict[str, float] = {}

        # 线程控制
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # 统计
        self._stats = {
            "events_stored": 0,
            "trends_stored": 0,
            "flushes": 0,
            "errors": 0,
            "start_time": 0.0,
        }

    # ── 生命周期 ─────────────────────────────────────

    def start(self) -> bool:
        """启动存储服务（后台刷线程）"""
        if not self._db_manager:
            logger.warning("DB Manager 未设置，存储服务无法启动")
            return False

        with self._lock:
            if self._is_running:
                return True
            self._is_running = True
            self._stats["start_time"] = time.time()

        self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="PDStorageFlush")
        self._thread.start()
        logger.info("PD 存储服务已启动 [间隔=%.1fs, 批量=%d]", self._flush_interval, self._batch_size)
        return True

    def stop(self) -> None:
        """停止存储服务（最终写入）"""
        with self._lock:
            self._is_running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        # 最终写入
        self._flush_all()
        logger.info("PD 存储服务已停止, 统计: %s", self._stats)

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ── 数据接收 ─────────────────────────────────────

    def store_pd_event(self, device_id: str, channel_id: int, event: dict) -> None:
        """
        缓存局放事件

        Args:
            device_id: 设备 ID
            channel_id: 通道 ID
            event: 事件字典 (需包含 amplitude)
        """
        # 基本字段校验
        if not isinstance(event, dict) or "amplitude" not in event:
            logger.warning("无效的局放事件数据: %s", type(event))
            return

        self._event_queue.append(
            {
                "device_id": device_id,
                "channel_id": channel_id,
                "timestamp": datetime.now(timezone.utc),
                "phase": event.get("phase"),
                "amplitude": event.get("amplitude", 0),
                "energy": event.get("energy"),
                "polarity": event.get("polarity"),
                "frequency_mhz": event.get("frequency_mhz"),
                "bandwidth_mhz": event.get("bandwidth_mhz"),
                "signal_quality": event.get("signal_quality", 0),
                # AE 特有字段 (nullable, UHF 事件为 None)
                "rise_time_us": event.get("rise_time_us"),
                "duration_us": event.get("duration_us"),
                "counts": event.get("counts"),
                "marse_energy": event.get("marse_energy"),
            }
        )

        # 触发批量写入
        if len(self._event_queue) >= self._batch_size:
            self._flush_events()

    def store_trend_data(
        self,
        device_id: str,
        channel_id: int,
        period: str,
        stats: dict,
    ) -> None:
        """
        缓存趋势数据

        Args:
            device_id: 设备 ID
            channel_id: 通道 ID
            period: 统计周期 (1h, 24h, 7d, 30d)
            stats: 统计数据字典
        """
        self._trend_queue.append(
            {
                "device_id": device_id,
                "channel_id": channel_id,
                "timestamp": datetime.now(timezone.utc),
                "period": period,
                "pd_count": stats.get("pd_count", 0),
                "max_amplitude": stats.get("max_amplitude", 0),
                "avg_amplitude": stats.get("avg_amplitude", 0),
                "min_amplitude": stats.get("min_amplitude", 0),
                "total_energy": stats.get("total_energy", 0),
                "noise_level": stats.get("noise_level", 0),
                "pulse_count": stats.get("pulse_count", 0),
                "positive_ratio": stats.get("positive_ratio", 0),
                "ae_hit_count": stats.get("ae_hit_count", 0),
            }
        )

    # ── 批量写入 ─────────────────────────────────────

    def _flush_events(self) -> None:
        """批量写入局放事件"""
        if not self._event_queue or not self._db_manager:
            return

        batch = []
        with self._lock:
            while self._event_queue and len(batch) < self._batch_size:
                batch.append(self._event_queue.popleft())

        if not batch:
            return

        try:
            with self._db_manager.session() as session:
                for data in batch:
                    event = PDEventModel(**data)
                    session.add(event)
            self._stats["events_stored"] += len(batch)
            self._stats["flushes"] += 1
        except Exception as e:
            logger.error("局放事件批量写入失败 (%d 条): %s", len(batch), e)
            self._stats["errors"] += 1
            # 指数退避重试：计数+1，超过上限则转入死信队列
            for item in batch:
                item_key = str(item.get("timestamp", "")) + str(item.get("amplitude", ""))
                count = self._retry_counts.get(item_key, 0) + 1
                if count >= self._max_retries:
                    self._dead_letter.append(item)
                    logger.warning("事件写入失败超过上限，转入死信队列: %s", item_key[:40])
                else:
                    self._retry_counts[item_key] = count
                    self._event_queue.append(item)

    def _flush_trends(self) -> None:
        """批量写入趋势数据"""
        if not self._trend_queue or not self._db_manager:
            return

        batch = []
        with self._lock:
            while self._trend_queue and len(batch) < 50:
                batch.append(self._trend_queue.popleft())

        if not batch:
            return

        try:
            with self._db_manager.session() as session:
                for data in batch:
                    trend = PDTrendDataModel(**data)
                    session.add(trend)
            self._stats["trends_stored"] += len(batch)
        except Exception as e:
            logger.error("趋势数据批量写入失败 (%d 条): %s", len(batch), e)
            self._stats["errors"] += 1

    def _flush_all(self) -> None:
        """写入所有缓存数据"""
        self._flush_events()
        self._flush_trends()

    def _flush_loop(self) -> None:
        """后台定期刷新循环"""
        while self._is_running:
            time.sleep(self._flush_interval)
            try:
                self._flush_all()
            except Exception as e:
                logger.error("存储刷新循环异常: %s", e)
                self._stats["errors"] += 1

    # ── 数据查询 ─────────────────────────────────────

    def query_events(
        self,
        device_id: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        channel_id: Optional[int] = None,
        limit: int = 1000,
    ) -> List[dict]:
        """
        查询局放事件历史

        Args:
            device_id: 设备 ID
            start_time: 开始时间
            end_time: 结束时间（默认当前时间）
            channel_id: 通道 ID（可选）
            limit: 最大返回条数
        """
        if not self._db_manager:
            return []

        if end_time is None:
            end_time = datetime.now(timezone.utc)

        try:
            with self._db_manager.session() as session:
                query = session.query(PDEventModel).filter(
                    PDEventModel.device_id == device_id,
                    PDEventModel.timestamp >= start_time,
                    PDEventModel.timestamp <= end_time,
                )
                if channel_id is not None:
                    query = query.filter(PDEventModel.channel_id == channel_id)
                query = query.order_by(PDEventModel.timestamp.desc()).limit(limit)
                results = [e.to_dict() for e in query.all()]
            return results
        except Exception as e:
            logger.error("查询局放事件失败: %s", e)
            return []

    def query_trends(
        self,
        device_id: str,
        period: str = "1h",
        start_time: Optional[datetime] = None,
        limit: int = 500,
    ) -> List[dict]:
        """
        查询趋势数据

        Args:
            device_id: 设备 ID
            period: 统计周期
            start_time: 起始时间
            limit: 最大返回条数
        """
        if not self._db_manager:
            return []

        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(hours=1)

        try:
            with self._db_manager.session() as session:
                query = session.query(PDTrendDataModel).filter(
                    PDTrendDataModel.device_id == device_id,
                    PDTrendDataModel.period == period,
                    PDTrendDataModel.timestamp >= start_time,
                )
                query = query.order_by(PDTrendDataModel.timestamp.asc()).limit(limit)
                results = [t.to_dict() for t in query.all()]
            return results
        except Exception as e:
            logger.error("查询趋势数据失败: %s", e)
            return []

    def query_alarms(
        self,
        device_id: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """查询报警历史"""
        if not self._db_manager:
            return []

        try:
            with self._db_manager.session() as session:
                query = session.query(PDAlarmModel).order_by(PDAlarmModel.timestamp.desc())
                if device_id:
                    query = query.filter(PDAlarmModel.device_id == device_id)
                if level:
                    query = query.filter(PDAlarmModel.level == level)
                results = [a.to_dict() for a in query.limit(limit).all()]
            return results
        except Exception as e:
            logger.error("查询报警记录失败: %s", e)
            return []

    # ── 数据维护 ─────────────────────────────────────

    def cleanup_old_data(self) -> int:
        """
        清理过期数据

        Returns:
            清理的记录数
        """
        if not self._db_manager:
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        total = 0

        try:
            with self._db_manager.session() as session:
                for model, name in [
                    (PDEventModel, "事件"),
                    (PDTrendDataModel, "趋势"),
                    (PDAlarmModel, "报警"),
                ]:
                    deleted = session.query(model).filter(model.timestamp < cutoff).delete()
                    total += deleted
                    if deleted > 0:
                        logger.info("清理 %s 数据 %d 条", name, deleted)
        except Exception as e:
            logger.error("数据清理失败: %s", e)

        return total

    def get_statistics(self) -> dict:
        """获取存储统计"""
        stats = dict(self._stats)
        stats["event_queue_size"] = len(self._event_queue)
        stats["trend_queue_size"] = len(self._trend_queue)
        return stats
