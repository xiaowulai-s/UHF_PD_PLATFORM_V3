# -*- coding: utf-8 -*-
"""PDStorageService 专项单元测试"""

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.services.storage_service import PDStorageService


class TestStorageServiceBasic:
    """基础功能测试"""

    def test_init_defaults(self):
        svc = PDStorageService()
        assert svc._flush_interval == 5.0
        assert svc._batch_size == 100
        assert svc._retention_days == 365

    def test_init_custom_params(self):
        svc = PDStorageService(flush_interval=10.0, batch_size=50, retention_days=30)
        assert svc._flush_interval == 10.0
        assert svc._batch_size == 50
        assert svc._retention_days == 30

    def test_start_without_db(self):
        svc = PDStorageService()
        result = svc.start()
        assert result is False

    def test_start_with_db(self):
        db = MagicMock()
        svc = PDStorageService(db_manager=db)
        result = svc.start()
        assert result is True
        svc.stop()

    def test_double_start(self):
        db = MagicMock()
        svc = PDStorageService(db_manager=db)
        svc.start()
        result = svc.start()  # 重复启动
        assert result is True
        svc.stop()

    def test_cleanup_not_triggered_on_first_tick(self):
        """启动后第一个 flush tick 不应立刻全表清理。

        _last_cleanup_time 若留 0.0，now-0>86400 恒真，清理会在首个 tick 无条件
        执行；返回 MagicMock 时还会在守护线程里抛 TypeError 污染整套测试。
        """
        db = MagicMock()
        svc = PDStorageService(db_manager=db, flush_interval=0.1, retention_days=365)
        with patch.object(svc, "cleanup_old_data", return_value=0) as cleanup:
            svc.start()
            assert svc._last_cleanup_time == pytest.approx(time.time(), abs=2.0)
            time.sleep(0.5)
            cleanup.assert_not_called()
            svc.stop()


class TestStorageServiceStore:
    """存储测试"""

    def test_store_pd_event(self):
        db = MagicMock()
        svc = PDStorageService(db_manager=db)
        svc.store_pd_event(
            "dev1",
            1,
            {
                "amplitude": 50.0,
                "phase": 90,
                "energy": 10.0,
                "frequency_mhz": 500,
            },
        )
        assert len(svc._event_queue) == 1

    def test_store_trend_data(self):
        db = MagicMock()
        svc = PDStorageService(db_manager=db)
        svc.store_trend_data(
            "dev1",
            1,
            "1h",
            {
                "pd_count": 100,
                "max_amplitude": 85.0,
                "avg_amplitude": 45.0,
                "total_energy": 5000.0,
                "noise_level": 2.5,
            },
        )
        assert len(svc._trend_queue) == 1

    def test_event_queue_overflow(self):
        db = MagicMock()
        svc = PDStorageService(db_manager=db, batch_size=999999)  # 禁用自动刷写
        for i in range(11000):
            svc.store_pd_event("dev1", 1, {"amplitude": float(i)})
        assert len(svc._event_queue) == 10000  # deque maxlen


class TestStorageServiceStats:
    """统计测试"""

    def test_initial_stats(self):
        db = MagicMock()
        svc = PDStorageService(db_manager=db)
        stats = svc._stats
        assert stats["events_stored"] == 0
        assert stats["trends_stored"] == 0
        assert stats["flushes"] == 0
        assert stats["errors"] == 0

    def test_stats_after_store(self):
        db = MagicMock()
        svc = PDStorageService(db_manager=db, batch_size=1)
        svc.store_pd_event("dev1", 1, {"amplitude": 50.0})
        stats = svc._stats
        # batch_size=1, 入队后立即刷写
        assert stats["events_stored"] == 1
        assert stats["flushes"] == 1


class TestStorageServiceQuery:
    """查询测试"""

    def test_query_events_no_db(self):
        svc = PDStorageService()
        result = svc.query_events("dev1", start_time=datetime.now(timezone.utc))
        assert result == []

    def test_query_trends_no_db(self):
        svc = PDStorageService()
        result = svc.query_trends("dev1")
        assert result == []


class TestStopWakesTheFlushWaiter:
    """stop() 必须能唤醒正在等周期的线程，而不是等它睡完。

    回归：`_flush_loop` 原先是 `time.sleep(self._flush_interval)`（默认 5s）睡在
    循环开头，所以 `stop()` 里那句 `join(timeout=5.0)` 是抛硬币。实测代价有两层：
    `test_start_with_db` / `test_double_start` 各花 5.81s；更糟的是 join 超时后代码
    就直接放走了线程——整套跑完 `threading.enumerate()` 里有 55 个 PDStorageFlush
    还活着。现在改成 `Event.wait`，并把 join 超时打日志而不是静默。
    """

    def test_stop_returns_promptly_with_long_flush_interval(self):
        db = MagicMock()
        svc = PDStorageService(db_manager=db, flush_interval=30.0)
        svc.start()
        thread = svc._thread

        t0 = time.monotonic()
        svc.stop()
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0, "stop() 花了 %.2fs：flush_interval=30s 的等待没被唤醒，等的是整段 sleep" % elapsed
        assert not thread.is_alive(), "stop() 返回时刷写线程还活着"

    def test_periodic_flush_still_happens(self):
        """换成 Event.wait 不能把"周期性刷写"本身弄丢。"""
        db = MagicMock()
        svc = PDStorageService(db_manager=db, flush_interval=0.05)
        calls = []
        with patch.object(svc, "_flush_all", side_effect=lambda: calls.append(1)):
            svc.start()
            time.sleep(0.28)
        # _flush_all 被 patch 掉后 stop() 里那次也不会真跑，所以先取循环里的次数
        loop_calls = len(calls)
        assert loop_calls >= 2, "周期性刷写没了：只被调用 %d 次" % loop_calls
