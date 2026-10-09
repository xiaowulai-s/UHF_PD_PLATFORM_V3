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
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.data.monitor_models import PDAlarmModel, PDEventModel, PDTrendDataModel

try:
    from sqlalchemy.exc import DatabaseError
except ImportError:
    DatabaseError = Exception  # 回退到通用异常

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
        db_manager: Any = None,
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

        # 数据留存清理周期 (秒, 默认每天检查一次)
        self._cleanup_interval = 86400
        self._last_cleanup_time: float = 0.0

        # 线程控制
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        # 刷写周期用 Event.wait 而不是 time.sleep：见 _flush_loop
        self._stop_event = threading.Event()

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
            # 初值必须是当前时间：留 0.0 会让 now-0 > 86400 恒成立，
            # 于是第一个 flush tick 就无条件全表清理，而不是按天。
            self._last_cleanup_time = time.time()

        self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="PDStorageFlush")
        self._stop_event.clear()
        self._thread.start()
        logger.info("PD 存储服务已启动 [间隔=%.1fs, 批量=%d]", self._flush_interval, self._batch_size)
        return True

    def stop(self) -> None:
        """停止存储服务（最终写入）"""
        with self._lock:
            self._is_running = False

        # 先唤醒正在等周期的线程，join 才能在毫秒级返回而不是等满 timeout
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("PDStorageFlush 线程 5.0s 内未退出，可能残留")

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
                "sensor_type": event.get("sensor_type", "uhf"),
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
                    # 移除内部使用的重试 key，避免污染模型构造
                    data.pop("_retry_key", None)
                    event = PDEventModel(**data)
                    session.add(event)
            self._stats["events_stored"] += len(batch)
            self._stats["flushes"] += 1
        except (OSError, PermissionError) as e:
            logger.error("局放事件批量写入失败 - IO错误 (%d 条): %s", len(batch), e, exc_info=True)
            self._stats["errors"] += 1
            # IO错误直接转入死信队列，无法通过重试恢复
            for item in batch:
                self._dead_letter.append(item)
        except (ValueError, TypeError, KeyError) as e:
            logger.error("局放事件批量写入失败 - 数据格式错误 (%d 条): %s", len(batch), e, exc_info=True)
            self._stats["errors"] += 1
            # 数据格式错误无法通过重试恢复，直接丢弃
        except Exception as e:
            logger.error("局放事件批量写入失败 - 未知错误 (%d 条): %s", len(batch), e, exc_info=True)
            self._stats["errors"] += 1
            # 指数退避重试：计数+1，超过上限则转入死信队列
            for item in batch:
                # 使用 uuid 作为重试 key，避免同秒同幅值事件冲突
                item_key = item.setdefault("_retry_key", uuid.uuid4().hex)
                count = self._retry_counts.get(item_key, 0) + 1
                if count >= self._max_retries:
                    self._dead_letter.append(item)
                    logger.warning("事件写入失败超过上限，转入死信队列: %s", item_key[:8])
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
        except (OSError, PermissionError) as e:
            logger.error("趋势数据批量写入失败 - IO错误 (%d 条): %s", len(batch), e, exc_info=True)
            self._stats["errors"] += 1
        except (ValueError, TypeError, KeyError) as e:
            logger.error("趋势数据批量写入失败 - 数据格式错误 (%d 条): %s", len(batch), e, exc_info=True)
            self._stats["errors"] += 1
        except Exception as e:
            logger.error("趋势数据批量写入失败 - 未知错误 (%d 条): %s", len(batch), e, exc_info=True)
            self._stats["errors"] += 1

    def _flush_all(self) -> None:
        """写入所有缓存数据"""
        self._flush_events()
        self._flush_trends()

    def _flush_loop(self) -> None:
        """后台定期刷新循环

        等周期用 `Event.wait` 而不是 `time.sleep`：后者要在睡眠里把
        `flush_interval`（默认 5s）整段睡完才回头看 `_is_running`，于是 `stop()`
        的 `join(timeout=5.0)` 变成抛硬币——实测 `test_start_with_db` /
        `test_double_start` 各花 5.81s，而整套跑完还留着 55 个 `PDStorageFlush`
        线程没退干净（join 超时后就直接放走了）。
        """
        while self._is_running:
            if self._stop_event.wait(self._flush_interval):
                break
            try:
                self._flush_all()
                # 定期清理过期数据
                now = time.time()
                if now - self._last_cleanup_time > self._cleanup_interval:
                    self._last_cleanup_time = now
                    deleted = self.cleanup_old_data()
                    if deleted > 0:
                        logger.info("数据留存清理: 清除 %d 条过期记录", deleted)
            except (OSError, DatabaseError) as e:
                logger.error("存储服务刷新循环异常: %s", e, exc_info=True)
                self._stop_event.wait(1)  # 出错后暂停1秒再试（同样可被 stop 打断）
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
        except (OSError, DatabaseError) as e:
            logger.error("查询局放事件失败 - 数据库错误: %s", e, exc_info=True)
            return []
        except Exception as e:
            logger.error("查询局放事件失败: %s", e, exc_info=True)
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
        except (OSError, DatabaseError) as e:
            logger.error("查询趋势数据失败 - 数据库错误: %s", e, exc_info=True)
            return []
        except Exception as e:
            logger.error("查询趋势数据失败: %s", e, exc_info=True)
            return []

    #: 时窗 -> 秒数。与旧版 trend_chart_widget.TIME_RANGES 的四个窗口一致。
    TREND_WINDOWS = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}

    def query_history_device_ids(self) -> List[dict]:
        """列出 pd_events 里真实出现过的 device_id，以及各自的条数与时间跨度。

        历史/趋势页的设备候选必须来自这里，而**不能**用 device.list 或
        stats.sources 的键：前者在纯模拟器场景下是空的，后者是运行时数据源 id，
        与"实际写过事件的设备"不等价——采集侧从未产生事件的源照样出现在那里。
        另外模拟器的事件设备 id 在 2026-10-09 之前一直是 PDSimulator 的类默认
        "SIM-001"（与源 id sim-uhf-N 不同名，且多源互相覆盖），此后才与源 id 统一；
        所以既有库里同时存在两种命名，硬编码任一名字都会让另一半查不出来。
        """
        from sqlalchemy import func

        if not self._db_manager:
            return []
        try:
            with self._db_manager.session() as session:
                rows = (
                    session.query(
                        PDEventModel.device_id,
                        func.count(PDEventModel.id),
                        func.min(PDEventModel.timestamp),
                        func.max(PDEventModel.timestamp),
                    )
                    .group_by(PDEventModel.device_id)
                    .order_by(func.count(PDEventModel.id).desc())
                    .all()
                )
            out: List[dict] = []
            for device_id, count, first_ts, last_ts in rows:
                out.append(
                    {
                        "device_id": str(device_id),
                        "events": int(count or 0),
                        "first_ts": first_ts.isoformat(sep=" ", timespec="seconds") if first_ts else None,
                        "last_ts": last_ts.isoformat(sep=" ", timespec="seconds") if last_ts else None,
                    }
                )
            return out
        except (OSError, DatabaseError) as e:
            logger.error("查询历史设备号失败 - 数据库错误: %s", e, exc_info=True)
            return []
        except Exception as e:
            logger.error("查询历史设备号失败: %s", e, exc_info=True)
            return []

    def query_trend_series(
        self,
        device_id: str,
        window: str = "1h",
        bins: int = 120,
        channel_id: Optional[int] = None,
        sensor_type: Optional[str] = None,
    ) -> List[dict]:
        """按等宽时间箱即时聚合 pd_events，返回连续序列（空箱补 0）。

        为什么不读 pd_trend_data 表：那里的行是旧 signal_router 每 50 帧写一次的
        **累计值与派生常量**（avg=max*0.6、energy=count*max*0.1、noise=0），
        不是分箱统计。直接画会把"累计计数"当成"每箱事件数"，读数不可信。
        从事件表现算，数值含义和图上的纵轴天然一致。

        Args:
            device_id: 设备 ID（必要过滤条件，缺省会得到空集）
            window: 1h / 24h / 7d / 30d
            bins: 分箱数，钳制在 [10, 500]
            channel_id: 可选通道过滤
            sensor_type: 可选 'uhf' / 'ae' 过滤

        Returns:
            每箱 {t, iso, count, max_amplitude, avg_amplitude, total_energy,
            positive_count, negative_count}，按时间升序。t 是箱起点的 epoch 秒，
            iso 是同一点的 UTC ISO 串——列里存的是 naive UTC，这里统一按 UTC 解释，
            换算本地时间交给前端，避免服务端猜时区。
        """
        from sqlalchemy import Integer as _Int
        from sqlalchemy import case, func

        if not self._db_manager:
            return []
        seconds = self.TREND_WINDOWS.get(window)
        if seconds is None:
            raise ValueError(f"非法 window: {window}")

        try:
            bins = max(10, min(int(bins), 500))
        except (TypeError, ValueError):
            bins = 120
        width = seconds / bins

        now = datetime.now(timezone.utc)
        start = now - timedelta(seconds=seconds)
        # 列里是 naive UTC：比较与 julianday 锚点都用 naive，避免 tz-aware 参数
        # 被渲染成带 +00:00 的字符串后与 naive 值做字典序比较。
        start_naive = start.replace(tzinfo=None)
        end_naive = now.replace(tzinfo=None)
        anchor = start_naive.strftime("%Y-%m-%d %H:%M:%S.%f")

        ts = PDEventModel.timestamp
        abs_amp = func.abs(PDEventModel.amplitude)
        bin_idx = func.cast((func.julianday(ts) - func.julianday(anchor)) * 86400.0 / width, _Int)

        try:
            with self._db_manager.session() as session:
                query = session.query(
                    bin_idx.label("b"),
                    func.count(),
                    func.max(abs_amp),
                    func.avg(abs_amp),
                    func.sum(func.coalesce(PDEventModel.energy, 0.0)),
                    func.sum(case((PDEventModel.polarity == 0, 1), else_=0)),
                    func.sum(case((PDEventModel.polarity == 1, 1), else_=0)),
                ).filter(
                    PDEventModel.device_id == device_id,
                    ts >= start_naive,
                    ts < end_naive,
                )
                if channel_id is not None:
                    query = query.filter(PDEventModel.channel_id == int(channel_id))
                if sensor_type:
                    query = query.filter(PDEventModel.sensor_type == str(sensor_type))
                grouped = {int(r[0]): r for r in query.group_by("b").all()}

            out: List[dict] = []
            for i in range(bins):
                row = grouped.get(i)
                t = start + timedelta(seconds=i * width)
                out.append(
                    {
                        "t": round(t.timestamp(), 3),
                        "iso": t.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds"),
                        "count": int(row[1]) if row else 0,
                        "max_amplitude": float(row[2]) if row and row[2] is not None else 0.0,
                        "avg_amplitude": float(row[3]) if row and row[3] is not None else 0.0,
                        "total_energy": float(row[4]) if row and row[4] is not None else 0.0,
                        "positive_count": int(row[5]) if row else 0,
                        "negative_count": int(row[6]) if row else 0,
                        "bin_seconds": round(width, 3),
                        "window": window,
                    }
                )
            return out
        except (OSError, DatabaseError) as e:
            logger.error("查询趋势序列失败 - 数据库错误: %s", e, exc_info=True)
            return []
        except Exception as e:
            logger.error("查询趋势序列失败: %s", e, exc_info=True)
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
        except (OSError, DatabaseError) as e:
            logger.error("查询报警记录失败 - 数据库错误: %s", e, exc_info=True)
            return []
        except Exception as e:
            logger.error("查询报警记录失败: %s", e, exc_info=True)
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
        except (OSError, DatabaseError) as e:
            logger.error("数据清理失败 - 数据库错误: %s", e, exc_info=True)
        except Exception as e:
            logger.error("数据清理失败: %s", e, exc_info=True)

        return total

    def get_statistics(self) -> dict:
        """获取存储统计"""
        stats = dict(self._stats)
        stats["event_queue_size"] = len(self._event_queue)
        stats["trend_queue_size"] = len(self._trend_queue)
        return stats
