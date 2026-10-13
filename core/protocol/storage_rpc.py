"""持久化数据的只读 RPC 暴露给 Electron 前端。

设计约束：
- 命名查询 + 参数化，绝不接受前端传来的裸 SQL。
- 全部以 background 方式注册到线程池，SQLite 查询不能阻塞 Qt 事件循环，
  否则 /stream 的 20 FPS 推流会跟着卡住。
- 模型 to_dict() 已把 datetime 转成 ISO 字符串，可直接 msgpack 序列化。

现有 UI 几乎不读库（告警列表、趋势、分析页多为内存态或信号驱动），所以这些
查询是把库里既有数据首次暴露给前端的新能力，而非对旧行为的复刻。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

MAX_LIMIT = 5000


def _parse_time(value: Any) -> Optional[datetime]:
    """接受 ISO 字符串或 epoch 秒，统一成带时区的 UTC datetime。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"时间格式非法: {value}")
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raise ValueError("时间参数类型不支持")


def _clamp_limit(value: Any, default: int) -> int:
    try:
        limit = int(value) if value is not None else default
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, MAX_LIMIT))


class StorageRpc:
    """按依赖是否就绪，注册一组只读查询方法。"""

    def __init__(self, db_manager: Any = None, storage: Any = None):
        self._db = db_manager
        self._storage = storage

    @property
    def available(self) -> bool:
        return self._db is not None

    def handlers(self) -> list[tuple[str, Callable[[dict], Any]]]:
        """返回 (方法名, 处理函数) 列表，全部标记为 background 执行。"""
        if not self.available:
            return []

        out: list[tuple[str, Callable[[dict], Any]]] = [
            ("device.list", self.device_list),
            ("device.get", self.device_get),
        ]
        if self._storage is not None:
            out += [
                ("storage.query_events", self.query_events),
                ("storage.query_trends", self.query_trends),
                ("storage.query_trend_series", self.query_trend_series),
                ("storage.query_alarms", self.query_alarms),
                ("storage.query_ae_hits", self.query_ae_hits),
                ("storage.device_ids", lambda _p: self._storage.query_history_device_ids()),
                ("storage.stats", lambda _p: self._storage.get_statistics()),
            ]
        return out

    # ── 设备与通道配置 ───────────────────────────────

    def _repo(self):
        from core.data.repository.device_repository import PDDeviceRepository

        return PDDeviceRepository(self._db)

    def device_list(self, _params: dict) -> list[dict]:
        devices = []
        for dev in self._repo().get_all_devices():
            record = dev.to_dict()
            record["channels"] = [ch.to_dict() for ch in (dev.channels or [])]
            devices.append(record)
        return devices

    def device_get(self, params: dict) -> Optional[dict]:
        device_id = params.get("device_id")
        if not device_id:
            raise ValueError("缺少 device_id")
        dev = self._repo().get_device(str(device_id))
        if dev is None:
            return None
        record = dev.to_dict()
        record["channels"] = [ch.to_dict() for ch in (dev.channels or [])]
        return record

    # ── 历史数据查询（委托 PDStorageService）─────────

    def query_events(self, params: dict) -> list[dict]:
        # PDStorageService.query_events 以 device_id 为必要过滤条件，缺省会得到空集，
        # 因此这里要求显式传入，而不是静默返回 []。
        device_id = params.get("device_id")
        if not device_id:
            raise ValueError("缺少 device_id")
        start = _parse_time(params.get("start_time")) or (datetime.now(timezone.utc) - timedelta(hours=1))
        end = _parse_time(params.get("end_time"))
        channel_id = params.get("channel_id")
        return self._storage.query_events(
            device_id=str(device_id),
            start_time=start,
            end_time=end,
            channel_id=int(channel_id) if channel_id is not None else None,
            limit=_clamp_limit(params.get("limit"), 1000),
        )

    def query_trends(self, params: dict) -> list[dict]:
        device_id = params.get("device_id")
        if not device_id:
            raise ValueError("缺少 device_id")
        period = str(params.get("period", "1h"))
        if period not in ("1h", "24h", "7d", "30d"):
            raise ValueError(f"非法 period: {period}")
        return self._storage.query_trends(
            device_id=str(device_id),
            period=period,
            start_time=_parse_time(params.get("start_time")),
            limit=_clamp_limit(params.get("limit"), 500),
        )

    def query_trend_series(self, params: dict) -> list[dict]:
        """按箱聚合的 trend 曲线（前端趋势页的数据源）。

        与 storage.query_trends 的区别是刻意的：query_trends 读 pd_trend_data 表，
        那里存的是旧 signal_router 每 50 帧写一次的累计值与派生常量
        （avg=max*0.6、energy=count*max*0.1、noise=0），不能当分箱统计画；
        本方法直接从 pd_events 现算，纵轴含义与图一致。
        """
        device_id = params.get("device_id")
        if not device_id:
            raise ValueError("缺少 device_id")
        window = str(params.get("window", "1h"))
        if window not in ("1h", "24h", "7d", "30d"):
            raise ValueError(f"非法 window: {window}")
        channel_id = params.get("channel_id")
        sensor_type = params.get("sensor_type")
        return self._storage.query_trend_series(
            device_id=str(device_id),
            window=window,
            bins=int(params.get("bins", 120)),
            channel_id=int(channel_id) if channel_id is not None else None,
            sensor_type=str(sensor_type) if sensor_type else None,
        )

    def query_alarms(self, params: dict) -> list[dict]:
        device_id = params.get("device_id")
        level = params.get("level")
        return self._storage.query_alarms(
            device_id=str(device_id) if device_id else None,
            level=str(level) if level else None,
            limit=_clamp_limit(params.get("limit"), 100),
        )

    def query_ae_hits(self, params: dict) -> list[dict]:
        """AE 命中历史（跨重启可追溯的那一份）。

        与前端内存里的"本次会话累计"是两码事：那个列表连接内核才有、重启即空，
        这里读的是 `ae_hits` 表。前端必须把两者显式区分开，不能让用户以为
        页面上那个累计数就是设备投运以来的总数。
        """
        device_id = params.get("device_id")
        if not device_id:
            raise ValueError("缺少 device_id")
        start = _parse_time(params.get("start_time")) or (datetime.now(timezone.utc) - timedelta(hours=1))
        end = _parse_time(params.get("end_time"))
        channel_id = params.get("channel_id")
        return self._storage.query_ae_hits(
            device_id=str(device_id),
            start_time=start,
            end_time=end,
            channel_id=int(channel_id) if channel_id is not None else None,
            limit=_clamp_limit(params.get("limit"), 1000),
        )


def register_storage_rpc(server: Any, db_manager: Any = None, storage: Any = None) -> list[str]:
    """把只读查询挂到 SidecarServer，返回实际注册的方法名。"""
    rpc = StorageRpc(db_manager=db_manager, storage=storage)
    registered = []
    for name, handler in rpc.handlers():
        server.register_rpc(name, handler, background=True)
        registered.append(name)
    if not registered:
        logger.warning("未提供 db_manager，只读查询 RPC 全部跳过")
    return registered
