# -*- coding: utf-8 -*-
"""
大数据集懒加载管理器

针对大型数据集（历史记录、波形数据）的懒加载和分页加载:
- 分页查询历史事件
- 波形数据懒加载（仅加载可视范围）
- 预加载策略（预测用户行为）
- 内存使用监控
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PageResult:
    """分页查询结果"""

    data: List[Any]
    total_count: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool


class LazyDataLoader:
    """
    懒加载数据管理器

    用于大型数据集的分页加载和缓存:
    - 支持任意数据源的分页
    - 自动缓存已加载的页面
    - 内存使用监控
    - 预加载下一页
    """

    def __init__(
        self,
        page_size: int = 100,
        max_cached_pages: int = 10,
        prefetch_enabled: bool = True,
    ):
        self._page_size = page_size
        self._max_cached_pages = max_cached_pages
        self._prefetch_enabled = prefetch_enabled

        self._cache: Dict[int, List[Any]] = {}
        self._total_count: Optional[int] = None
        self._data_source: Optional[Callable[[int, int], List[Any]]] = None
        self._current_page: Optional[int] = None

    def initialize(self, data_source: Callable[[int, int], List[Any]], total_count: int) -> None:
        """
        初始化数据源

        Args:
            data_source: 数据加载函数 (page, page_size) -> List[data]
            total_count: 总数据条数
        """
        self._data_source = data_source
        self._total_count = total_count
        self._cache.clear()
        self._current_page = None

    def get_page(self, page: int) -> Optional[PageResult]:
        """
        获取指定页

        Args:
            page: 页码（从0开始）

        Returns:
            PageResult 或 None（未初始化）
        """
        if self._data_source is None or self._total_count is None:
            logger.warning("LazyDataLoader 未初始化")
            return None

        # 检查缓存
        if page in self._cache:
            logger.debug("懒加载缓存命中: page %d", page)
            return self._create_page_result(page, self._cache[page])

        # 加载新页面
        logger.debug("懒加载从数据源加载: page %d", page)
        data = self._data_source(page, self._page_size)

        # 缓存页面
        self._cache[page] = data
        self._current_page = page

        # 限制缓存大小
        self._trim_cache()

        # 预加载下一页
        if self._prefetch_enabled:
            self._prefetch_next_page(page)

        return self._create_page_result(page, data)

    def get_current_page(self) -> Optional[PageResult]:
        """获取当前页"""
        if self._current_page is not None:
            return self.get_page(self._current_page)
        return None

    def next_page(self) -> Optional[PageResult]:
        """获取下一页"""
        if self._current_page is None:
            return self.get_page(0)
        next_p = self._current_page + 1
        if next_p * self._page_size >= self._total_count:
            return None
        return self.get_page(next_p)

    def prev_page(self) -> Optional[PageResult]:
        """获取上一页"""
        if self._current_page is None or self._current_page == 0:
            return None
        return self.get_page(self._current_page - 1)

    def invalidate_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()
        logger.debug("懒加载缓存已清空")

    def _create_page_result(self, page: int, data: List[Any]) -> PageResult:
        """创建分页结果"""
        total = self._total_count or 0
        has_next = (page + 1) * self._page_size < total
        has_prev = page > 0

        return PageResult(
            data=data,
            total_count=total,
            page=page,
            page_size=self._page_size,
            has_next=has_next,
            has_prev=has_prev,
        )

    def _prefetch_next_page(self, current_page: int) -> None:
        """预加载下一页（后台线程）"""
        next_page = current_page + 1
        if next_page not in self._cache and self._data_source is not None:
            try:
                import threading

                def prefetch():
                    data = self._data_source(next_page, self._page_size)
                    self._cache[next_page] = data
                    logger.debug("预加载完成: page %d", next_page)

                thread = threading.Thread(target=prefetch, daemon=True)
                thread.start()
            except (RuntimeError, OSError, AttributeError) as e:
                logger.debug("预加载失败: %s", e)

    def _trim_cache(self) -> None:
        """裁剪缓存大小"""
        while len(self._cache) > self._max_cached_pages:
            # 移除最旧的页面
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
            logger.debug("缓存页面已淘汰: page %d", oldest_key)

    @property
    def cache_stats(self) -> Dict[str, int]:
        """缓存统计信息"""
        return {
            "cached_pages": len(self._cache),
            "max_cached_pages": self._max_cached_pages,
            "current_page": self._current_page,
            "total_count": self._total_count or 0,
        }


class WaveformLazyLoader:
    """
    波形数据懒加载器

    用于大型波形文件的分段加载:
    - 仅加载可视范围内的数据
    - 自动缓存最近访问的段
    """

    def __init__(
        self,
        segment_size: int = 4096,
        max_cached_segments: int = 20,
    ):
        self._segment_size = segment_size
        self._max_cached_segments = max_cached_segments
        self._cache: Dict[int, np.ndarray] = {}
        self._data_source: Optional[Callable[[int, int], np.ndarray]] = None
        self._total_samples: Optional[int] = None

    def initialize(
        self,
        data_source: Callable[[int, int], np.ndarray],
        total_samples: int,
    ) -> None:
        """
        初始化数据源

        Args:
            data_source: 数据加载函数 (offset, length) -> np.ndarray
            total_samples: 总样本数
        """
        self._data_source = data_source
        self._total_samples = total_samples
        self._cache.clear()

    def get_segment(self, offset: int, length: int) -> Optional[np.ndarray]:
        """
        获取指定范围的数据段

        Args:
            offset: 起始偏移
            length: 长度

        Returns:
            数据数组或 None
        """
        if self._data_source is None:
            return None

        # 计算段索引
        segment_idx = offset // self._segment_size

        # 检查缓存
        if segment_idx in self._cache:
            return self._cache[segment_idx]

        # 加载数据段
        try:
            data = self._data_source(offset, length)
            self._cache[segment_idx] = data

            # 限制缓存
            while len(self._cache) > self._max_cached_segments:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

            return data
        except (IOError, OSError, ValueError, TypeError, KeyError) as e:
            logger.error("加载波形数据失败: %s", e)
            return None

    def get_visible_range(self, view_start: int, view_end: int) -> np.ndarray:
        """
        获取可视范围内的数据（可能跨多个段）

        Args:
            view_start: 可视起始位置
            view_end: 可视结束位置

        Returns:
            拼接后的数据数组
        """
        if self._data_source is None or self._total_samples is None:
            return np.array([])

        # 限制范围
        view_start = max(0, view_start)
        view_end = min(self._total_samples, view_end)

        if view_start >= view_end:
            return np.array([])

        # 计算需要加载的段
        start_segment = view_start // self._segment_size
        end_segment = (view_end - 1) // self._segment_size

        segments = []
        for seg_idx in range(start_segment, end_segment + 1):
            seg_offset = seg_idx * self._segment_size
            seg_data = self.get_segment(seg_offset, self._segment_size)

            if seg_data is not None:
                # 提取可视部分
                local_start = max(0, view_start - seg_offset)
                local_end = min(len(seg_data), view_end - seg_offset)
                segments.append(seg_data[local_start:local_end])

        if not segments:
            return np.array([])

        return np.concatenate(segments)

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()
