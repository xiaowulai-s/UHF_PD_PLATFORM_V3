"""运行时数据源（source）管理的 RPC。

和 `device.*` 的分工要说清楚，否则很容易把两者当成同一件事：

- `device.*` 管的是 **SQLite 里的持久化设备配置**（`pd_devices` / `pd_channels`）。
- `source.*` 管的是 **此刻正在跑的运行时来源**（内存里的 `DataSourceManager`）。

两者刻意不共用主键语义：移除一个运行时来源**不会**删掉同名设备配置，
删设备配置也**不会**停掉正在采集的来源。旧 `device_page.py` 把这两套按钮放在
同一页，但走的是不同对象（`source_manager` vs 设备仓储），这里保持同样的切分。

全部走主线程（`register_rpc(..., background=False)`）：`DataSourceManager` 的启停会
创建/回收采集线程并 emit Qt 信号，丢到 `QThreadPool` 上会让信号跨线程投递、
启停顺序不可控，而它本身不碰 SQLite，没有放到后台的理由。

已知边界（不在这层解决）：数据源自身状态变化（如硬件断线重连）走的是
`DataSourceManager.source_status_changed`，这条信号目前没有接到总线上；
前端看到的运行态来自每 2 秒一帧的 `stats.sources`。所以 `source.start/stop`
之后前端应当立即重拉一次 `source.list`，不要等 stats。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _require(params: dict, key: str) -> Any:
    value = params.get(key)
    if value is None or value == "":
        raise ValueError(f"缺少必填参数: {key}")
    return value


class SourceRpc:
    """把运行时来源的读写映射到 DataSourceManager，不自己实现任何业务规则。"""

    def __init__(self, sources: Any = None):
        self._sources = sources

    def handlers(self) -> list[tuple[str, Callable[[dict], Any]]]:
        if self._sources is None:
            return []
        return [
            ("source.list", self.source_list),
            ("source.start", self.source_start),
            ("source.stop", self.source_stop),
            ("source.remove", self.source_remove),
            ("source.set_active", self.source_set_active),
        ]

    # ── 读 ───────────────────────────────────────────

    def source_list(self, _params: dict) -> dict:
        """运行中/已注册的来源清单。

        数据来自 `DataSourceManager.get_all_status()`，与 `stats` 帧里的
        `sources` 是同一个来源，所以两处不会各说各话。
        """
        status = self._sources.get_all_status() or {}
        return {
            "sources": status,
            "count": len(status),
            "active_source": self._sources.active_source_id,
        }

    # ── 写 ───────────────────────────────────────────

    def source_start(self, params: dict) -> dict:
        source_id = str(_require(params, "source_id"))
        if self._sources.get_source(source_id) is None:
            raise ValueError(f"未知数据源: {source_id}")
        if not self._sources.start_source(source_id):
            # 走到这里只有两种可能：源处于 ERROR/RECONNECTING（由它自己管理），
            # 或 start() 真的失败了。两者都要让前端看见，不能静默当成功。
            raise RuntimeError(f"数据源 {source_id} 未能启动（正在重连或启动失败）")
        return {"source_id": source_id, "started": True, "active_source": self._sources.active_source_id}

    def source_stop(self, params: dict) -> dict:
        source_id = str(_require(params, "source_id"))
        if self._sources.get_source(source_id) is None:
            raise ValueError(f"未知数据源: {source_id}")
        self._sources.stop_source(source_id)
        return {"source_id": source_id, "stopped": True, "active_source": self._sources.active_source_id}

    def source_remove(self, params: dict) -> dict:
        """移除运行时来源。

        注意不会连带删除同名设备配置——现场语义是"这次采集不要它了"，
        不是"把这台设备从系统里删掉"。要删配置走 `device.delete`。
        """
        source_id = str(_require(params, "source_id"))
        if not self._sources.remove_source(source_id):
            raise ValueError(f"未知数据源: {source_id}")
        return {"source_id": source_id, "removed": True, "active_source": self._sources.active_source_id}

    def source_set_active(self, params: dict) -> dict:
        """切换前端当前显示的数据源（只影响 UI 语义，不影响采集）。"""
        source_id = str(_require(params, "source_id"))
        if not self._sources.set_active_source(source_id):
            raise ValueError(f"未知数据源: {source_id}")
        return {"source_id": source_id, "active_source": source_id}


def register_source_rpc(server: Any, *, sources: Any = None) -> list[str]:
    rpc = SourceRpc(sources=sources)
    registered = []
    for name, handler in rpc.handlers():
        server.register_rpc(name, handler)
        registered.append(name)
    logger.info("运行时来源 RPC 已注册: %s", ", ".join(registered) or "<无>")
    return registered
