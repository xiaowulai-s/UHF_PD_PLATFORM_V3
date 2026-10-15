# -*- coding: utf-8 -*-
"""
环形缓冲区 - RingBuffer

基于 collections.deque 实现，支持：
- 1,000,000 点容量
- 线程安全的读写
- 实时读取与历史回放
- 缓存快照导出
"""

from __future__ import annotations

import json
from collections import deque
from threading import RLock
from typing import Any, Dict, List


class RingBuffer:
    """
    环形缓冲区

    用于存储实时采集的波形数据和局放事件。
    线程安全，支持并发写入和读取。

    Args:
        maxlen: 最大存储点数，默认 1,000,000
    """

    def __init__(self, maxlen: int = 1_000_000):
        self._maxlen = maxlen
        self._buffer: deque = deque(maxlen=maxlen)
        self._lock = RLock()
        self._write_count = 0

    # ── 写入 ─────────────────────────────────────────

    def append(self, value: Any) -> None:
        """追加单个数据点"""
        with self._lock:
            self._buffer.append(value)
            self._write_count += 1

    def extend(self, values: List[Any]) -> None:
        """批量追加数据点"""
        with self._lock:
            self._buffer.extend(values)
            self._write_count += len(values)

    # ── 读取 ─────────────────────────────────────────

    def get_all(self) -> List[Any]:
        """获取全部数据"""
        with self._lock:
            return list(self._buffer)

    def get_last(self, n: int) -> List[Any]:
        """获取最后 n 个数据点"""
        with self._lock:
            if n >= len(self._buffer):
                return list(self._buffer)
            return [self._buffer[i] for i in range(len(self._buffer) - n, len(self._buffer))]

    def get_range(self, start: int, end: int) -> List[Any]:
        """获取指定范围 [start, end) 的数据"""
        with self._lock:
            total = len(self._buffer)
            if start < 0:
                start = 0
            if end > total:
                end = total
            if start >= end:
                return []
            return list(self._buffer)[start:end]

    def get_slice(self, count: int, offset: int = 0) -> List[Any]:
        """
        从指定偏移获取连续数据
        Args:
            count: 获取点数
            offset: 从末尾偏移，0=最新数据
        """
        with self._lock:
            total = len(self._buffer)
            if total == 0:
                return []
            end = total - offset
            start = max(0, end - count)
            return list(self._buffer)[start:end]

    # ── 快照 ─────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """获取缓冲区快照（元数据 + 序列化数据）"""
        with self._lock:
            return {
                "maxlen": self._maxlen,
                "size": len(self._buffer),
                "write_count": self._write_count,
                "data": list(self._buffer),
            }

    def to_json(self) -> str:
        """导出为 JSON 字符串"""
        return json.dumps(self.snapshot(), default=str)

    # ── 状态 ─────────────────────────────────────────

    @property
    def size(self) -> int:
        """当前数据点数"""
        with self._lock:
            return len(self._buffer)

    @property
    def maxlen(self) -> int:
        """最大容量"""
        return self._maxlen

    @property
    def is_full(self) -> bool:
        """是否已满"""
        with self._lock:
            return len(self._buffer) >= self._maxlen

    @property
    def write_count(self) -> int:
        """累计写入计数"""
        return self._write_count

    def clear(self) -> None:
        """清空缓冲区"""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return f"RingBuffer(maxlen={self._maxlen}, size={self.size}, writes={self._write_count})"
