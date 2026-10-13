# -*- coding: utf-8 -*-
"""RingBuffer 线程安全专项测试"""

import json
import threading

from core.processing.ring_buffer import RingBuffer


class TestRingBufferBasic:
    """基础功能测试"""

    def test_append_and_size(self):
        buf = RingBuffer(maxlen=100)
        for i in range(50):
            buf.append(i)
        assert buf.size == 50
        assert len(buf) == 50

    def test_overflow(self):
        buf = RingBuffer(maxlen=100)
        for i in range(200):
            buf.append(i)
        assert buf.size == 100
        assert buf.is_full
        assert buf.write_count == 200

    def test_extend(self):
        buf = RingBuffer(maxlen=100)
        buf.extend(list(range(50)))
        assert buf.size == 50

    def test_get_all(self):
        buf = RingBuffer(maxlen=100)
        buf.extend(list(range(50)))
        data = buf.get_all()
        assert len(data) == 50
        assert data == list(range(50))

    def test_get_last(self):
        buf = RingBuffer(maxlen=100)
        buf.extend(list(range(50)))
        last = buf.get_last(10)
        assert last == list(range(40, 50))

    def test_get_last_more_than_size(self):
        buf = RingBuffer(maxlen=100)
        buf.extend(list(range(50)))
        last = buf.get_last(100)
        assert len(last) == 50

    def test_get_range(self):
        buf = RingBuffer(maxlen=100)
        buf.extend(list(range(50)))
        data = buf.get_range(10, 20)
        assert data == list(range(10, 20))

    def test_get_slice(self):
        buf = RingBuffer(maxlen=100)
        buf.extend(list(range(50)))
        data = buf.get_slice(10, offset=5)
        assert len(data) == 10

    def test_snapshot(self):
        buf = RingBuffer(maxlen=100)
        buf.extend(list(range(50)))
        snap = buf.snapshot()
        assert snap["maxlen"] == 100
        assert snap["size"] == 50
        assert snap["write_count"] == 50

    def test_clear(self):
        buf = RingBuffer(maxlen=100)
        buf.extend(list(range(50)))
        buf.clear()
        assert buf.size == 0

    def test_repr(self):
        buf = RingBuffer(maxlen=100)
        r = repr(buf)
        assert "RingBuffer" in r
        assert "100" in r


class TestRingBufferJSON:
    """JSON 导出测试"""

    def test_to_json(self):
        buf = RingBuffer(maxlen=100)
        buf.extend([1, 2, 3])
        j = buf.to_json()
        data = json.loads(j)
        assert data["maxlen"] == 100
        assert data["size"] == 3
        assert data["data"] == [1, 2, 3]


class TestRingBufferThreadSafety:
    """线程安全测试"""

    def test_concurrent_write(self):
        """多线程并发写入不崩溃"""
        buf = RingBuffer(maxlen=10000)
        n_threads = 4
        n_items = 5000

        def writer(start):
            for i in range(n_items):
                buf.append(start + i)

        threads = [threading.Thread(target=writer, args=(t * n_items,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert buf.size == 10000  # maxlen
        assert buf.write_count == n_threads * n_items

    def test_concurrent_read_write(self):
        """并发读写不崩溃"""
        buf = RingBuffer(maxlen=10000)
        errors = []

        def writer():
            for i in range(5000):
                buf.append(i)

        def reader():
            try:
                for _ in range(1000):
                    buf.get_last(100)
                    buf.size
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_snapshot_during_write(self):
        """快照期间写入不崩溃"""
        buf = RingBuffer(maxlen=1000)
        errors = []

        def writer():
            for i in range(2000):
                buf.append(i)

        def snapshotter():
            try:
                for _ in range(100):
                    snap = buf.snapshot()
                    assert "size" in snap
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=snapshotter),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_extend_thread_safe(self):
        """批量写入线程安全"""
        buf = RingBuffer(maxlen=10000)
        n_threads = 4

        def batch_writer(tid):
            buf.extend([tid] * 2500)

        threads = [threading.Thread(target=batch_writer, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert buf.size == 10000

    def test_size_consistency(self):
        """并发写入后 size 一致"""
        buf = RingBuffer(maxlen=5000)

        def writer(n):
            for i in range(n):
                buf.append(i)

        threads = [
            threading.Thread(target=writer, args=(2000,)),
            threading.Thread(target=writer, args=(2000,)),
            threading.Thread(target=writer, args=(2000,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # size 应等于 min(total_written, maxlen)
        assert buf.size == 5000
        assert buf.write_count == 6000
