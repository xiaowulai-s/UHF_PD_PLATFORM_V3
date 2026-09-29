# -*- coding: utf-8 -*-
"""
结果缓存管理器 - 高性能缓存系统

提供 FFT 和 PRPD 结果的智能缓存，避免重复计算:
- LRU 缓存策略
- 内存使用限制
- 自动过期机制
- 缓存统计和监控
"""

from __future__ import annotations

import hashlib
import logging
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CacheStats:
    """缓存统计"""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    current_size_bytes: int = 0
    max_size_bytes: int = 0


@dataclass
class CacheEntry:
    """缓存条目"""

    key: str
    data: Any
    timestamp: float
    size_bytes: int
    access_count: int = 0
    last_access: float = field(default_factory=time.time)


class ResultCache:
    """
    高性能结果缓存

    特性:
    - LRU（最近最少使用）淘汰策略
    - 内存使用限制
    - 可配置过期时间
    - 缓存统计
    """

    def __init__(
        self,
        max_size_bytes: int = 100 * 1024 * 1024,  # 100 MB
        default_ttl: float = 300.0,  # 5 分钟
        enabled: bool = True,
    ):
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size_bytes = max_size_bytes
        self._default_ttl = default_ttl
        self._enabled = enabled
        self._stats = CacheStats(max_size_bytes=max_size_bytes)
        self._lock = None  # 如果需要在多线程环境使用，可以添加 threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def stats(self) -> CacheStats:
        return self._stats

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def memory_usage(self) -> int:
        return self._stats.current_size_bytes

    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存项

        Args:
            key: 缓存键

        Returns:
            缓存的数据，未命中返回 None
        """
        if not self._enabled:
            self._stats.misses += 1
            return None

        if key not in self._cache:
            self._stats.misses += 1
            return None

        entry = self._cache[key]

        # 检查是否过期
        if time.time() - entry.timestamp > self._default_ttl:
            self._remove(key)
            self._stats.misses += 1
            return None

        # 更新访问信息
        entry.access_count += 1
        entry.last_access = time.time()

        # 移动到末尾（LRU）
        self._cache.move_to_end(key)

        self._stats.hits += 1
        return entry.data

    def put(self, key: str, data: Any, ttl: Optional[float] = None, size_bytes: Optional[int] = None) -> bool:
        """
        存入缓存项

        Args:
            key: 缓存键
            data: 要缓存的数据
            ttl: 过期时间（秒），None 则使用默认值
            size_bytes: 数据大小（字节），None 则自动估算

        Returns:
            是否成功存入
        """
        if not self._enabled:
            return False

        # 估算数据大小
        if size_bytes is None:
            size_bytes = self._estimate_size(data)

        # 检查是否有足够空间
        if size_bytes > self._max_size_bytes:
            logger.warning("数据大小超过缓存限制: %d bytes > %d bytes", size_bytes, self._max_size_bytes)
            return False

        # 如果键已存在，先移除
        if key in self._cache:
            self._remove(key)

        # 确保有足够空间
        self._make_space(size_bytes)

        # 创建新条目
        entry = CacheEntry(
            key=key,
            data=data,
            timestamp=time.time(),
            size_bytes=size_bytes,
            access_count=1,
            last_access=time.time(),
        )

        self._cache[key] = entry
        self._stats.current_size_bytes += size_bytes

        return True

    def invalidate(self, key: str) -> bool:
        """
        使缓存项失效

        Args:
            key: 缓存键

        Returns:
            是否成功
        """
        if key in self._cache:
            self._remove(key)
            return True
        return False

    def clear(self) -> None:
        """清空所有缓存"""
        self._cache.clear()
        self._stats = CacheStats(max_size_bytes=self._max_size_bytes)

    def cleanup(self) -> int:
        """
        清理过期和超限缓存

        Returns:
            清理的条目数
        """
        removed = 0
        current_time = time.time()

        # 移除过期条目
        expired_keys = [key for key, entry in self._cache.items() if current_time - entry.timestamp > self._default_ttl]
        for key in expired_keys:
            self._remove(key)
            removed += 1

        # 如果仍然超限，移除最旧的条目
        while self._stats.current_size_bytes > self._max_size_bytes and len(self._cache) > 0:
            oldest_key = next(iter(self._cache))
            self._remove(oldest_key)
            removed += 1
            self._stats.evictions += 1

        return removed

    def _remove(self, key: str) -> None:
        """移除缓存项（内部）"""
        if key in self._cache:
            entry = self._cache.pop(key)
            self._stats.current_size_bytes -= entry.size_bytes

    def _make_space(self, needed_bytes: int) -> None:
        """确保有足够空间（LRU 淘汰）"""
        while self._stats.current_size_bytes + needed_bytes > self._max_size_bytes and len(self._cache) > 0:
            oldest_key = next(iter(self._cache))
            self._remove(oldest_key)
            self._stats.evictions += 1

    @staticmethod
    def _estimate_size(data: Any) -> int:
        """估算数据大小（字节）"""
        if isinstance(data, np.ndarray):
            return data.nbytes
        elif isinstance(data, dict):
            return sum(ResultCache._estimate_size(v) for v in data.values())
        elif isinstance(data, (list, tuple)):
            return sum(ResultCache._estimate_size(item) for item in data)
        else:
            # 粗略估算
            return sys.getsizeof(data)


class FFTCache:
    """FFT 结果缓存专用"""

    def __init__(self, max_entries: int = 100):
        self._cache: ResultCache = ResultCache(
            max_size_bytes=50 * 1024 * 1024,  # 50 MB
            default_ttl=600.0,  # 10 分钟
        )
        self._max_entries = max_entries

    def _make_key(self, signal: np.ndarray, sample_rate: int) -> str:
        """生成缓存键"""
        # 使用信号哈希和采样率
        signal_hash = hashlib.md5(signal.tobytes()).hexdigest()[:16]
        return f"fft:{sample_rate}:{signal_hash}"

    def get_fft(self, signal: np.ndarray, sample_rate: int) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """
        获取 FFT 结果

        Returns:
            (freqs, magnitudes) 或 None
        """
        key = self._make_key(signal, sample_rate)
        return self._cache.get(key)

    def put_fft(self, signal: np.ndarray, sample_rate: int, freqs: np.ndarray, mags: np.ndarray) -> None:
        """缓存 FFT 结果"""
        key = self._make_key(signal, sample_rate)
        data = {"freqs": freqs, "mags": mags}
        size = freqs.nbytes + mags.nbytes
        self._cache.put(key, data, size_bytes=size)

    def invalidate(self) -> None:
        """使所有 FFT 缓存失效"""
        self._cache.clear()


class PRPDCache:
    """PRPD 结果缓存专用"""

    def __init__(self, max_entries: int = 50):
        self._cache: ResultCache = ResultCache(
            max_size_bytes=200 * 1024 * 1024,  # 200 MB
            default_ttl=300.0,  # 5 分钟
        )
        self._max_entries = max_entries

    def _make_key(self, phases: np.ndarray, amplitudes: np.ndarray, phase_bins: int, amp_bins: int) -> str:
        """生成缓存键"""
        phases_hash = hashlib.md5(phases.tobytes()).hexdigest()[:16]
        amps_hash = hashlib.md5(amplitudes.tobytes()).hexdigest()[:16]
        return f"prpd:{phase_bins}:{amp_bins}:{phases_hash}:{amps_hash}"

    def get_heatmap(
        self,
        phases: np.ndarray,
        amplitudes: np.ndarray,
        phase_bins: int,
        amp_bins: int,
    ) -> Optional[np.ndarray]:
        """获取 PRPD 热力图"""
        key = self._make_key(phases, amplitudes, phase_bins, amp_bins)
        data = self._cache.get(key)
        if data is not None:
            return data.get("matrix")
        return None

    def put_heatmap(
        self,
        phases: np.ndarray,
        amplitudes: np.ndarray,
        phase_bins: int,
        amp_bins: int,
        matrix: np.ndarray,
    ) -> None:
        """缓存 PRPD 热力图"""
        key = self._make_key(phases, amplitudes, phase_bins, amp_bins)
        data = {"matrix": matrix}
        size = matrix.nbytes
        self._cache.put(key, data, size_bytes=size)

    def invalidate(self) -> None:
        """使所有 PRPD 缓存失效"""
        self._cache.clear()
