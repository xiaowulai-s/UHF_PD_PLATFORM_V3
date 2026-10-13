# -*- coding: utf-8 -*-
"""`core/utils/process_stats.py` 与 stats 帧里 `process` 块的行为测试。

要守住的是三件事，都不是"能跑就行"：

1. **CPU 百分比是两次采样的差值**，不是累计 CPU 时间。写成累计值的话读数会随进程
   存活时间单调上升，看起来像"一直在满载"——用假时钟钉死这个语义。
2. **单核为 100%**：多核上超过 100 是正常的，不能钳到 100，否则 8 核满载会显示 100%
   而不是 800%，把"跑满了"误读成"用了八分之一"。
3. **RSS 取不到时给 None 而不是 0**：0 会被前端读成"没占用内存"，是比"未知"更坏的答案。
"""

from __future__ import annotations

import os
import sys

import pytest

from core.utils import process_stats as ps


class _FakeTime:
    """受控时钟：只替换被测模块引用的那个 time，不动全局。"""

    def __init__(self, cpu: float = 100.0, wall: float = 1000.0) -> None:
        self.cpu = cpu
        self.wall = wall

    def process_time(self) -> float:
        return self.cpu

    def monotonic(self) -> float:
        return self.wall


class TestCpuIsADelta:
    def test_percent_is_delta_over_wall(self, monkeypatch):
        fake = _FakeTime()
        monkeypatch.setattr(ps, "time", fake)
        sampler = ps.ProcessStatsSampler()

        fake.cpu += 0.5
        fake.wall += 1.0
        first = sampler.sample()
        assert first["cpu_percent"] == pytest.approx(50.0)
        assert first["interval_s"] == pytest.approx(1.0)
        assert first["cpu_seconds"] == pytest.approx(100.5)

        fake.cpu += 1.5
        fake.wall += 1.0
        second = sampler.sample()
        assert second["cpu_percent"] == pytest.approx(150.0)

    def test_not_monotonic_like_a_total(self, monkeypatch):
        """累计值会单调上升；占用率必须能降下来。"""
        fake = _FakeTime()
        monkeypatch.setattr(ps, "time", fake)
        sampler = ps.ProcessStatsSampler()

        fake.cpu += 1.0
        fake.wall += 1.0
        busy = sampler.sample()["cpu_percent"]

        fake.wall += 1.0  # CPU 不动 → 空闲
        idle = sampler.sample()["cpu_percent"]

        assert busy == pytest.approx(100.0)
        assert idle == pytest.approx(0.0)
        assert idle < busy

    def test_percent_can_exceed_100_on_multicore(self, monkeypatch):
        fake = _FakeTime()
        monkeypatch.setattr(ps, "time", fake)
        sampler = ps.ProcessStatsSampler()

        fake.cpu += 4.0
        fake.wall += 1.0
        assert sampler.sample()["cpu_percent"] == pytest.approx(400.0)

    def test_zero_wall_interval_does_not_divide_by_zero(self, monkeypatch):
        fake = _FakeTime()
        monkeypatch.setattr(ps, "time", fake)
        sampler = ps.ProcessStatsSampler()
        assert sampler.sample()["cpu_percent"] == 0.0


class TestResidentBytes:
    def test_returns_a_plausible_number(self):
        rss = ps.resident_bytes()
        if rss is None:
            pytest.skip("当前平台取不到 RSS")
        # 下界防"读错结构体字段"（比如读到 PageFaultCount 得一个极小数），
        # 上界防把 32/64 位拼错成天文数字
        assert 1_000_000 < rss < 20_000_000_000

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows 专用分支")
    def test_windows_branch_is_not_none(self):
        assert ps.resident_bytes() is not None

    def test_unknown_rss_is_none_not_zero(self, monkeypatch):
        monkeypatch.setattr(ps, "_windows_resident_bytes", lambda: None)
        monkeypatch.setattr(ps, "_posix_resident_bytes", lambda: None)
        out = ps.ProcessStatsSampler().sample()
        assert out["rss_bytes"] is None
        assert out["rss_mb"] is None
        assert out["rss_bytes"] != 0


class _Server:
    def __init__(self) -> None:
        self.frames = []

    def diagnostics(self) -> dict:
        return {"sent_frames": 0, "dropped_frames": 0}

    def publish(self, frame) -> None:
        self.frames.append(frame)


class _Acq:
    def get_stats(self) -> dict:
        return {"frames_received": 3, "start_time": 0.0}

    def get_observed_max_amp(self) -> dict:
        return {}


class TestStatsFrameWiring:
    def test_collect_stats_omits_process_when_not_given(self):
        from core.protocol.stats_push import collect_stats

        assert "process" not in collect_stats(None)
        assert "process" not in collect_stats(_Acq())

    def test_collect_stats_includes_process_block(self):
        from core.protocol.stats_push import collect_stats

        snap = collect_stats(_Acq(), process={"pid": 1, "cpu_percent": 2.0})
        assert snap["process"] == {"pid": 1, "cpu_percent": 2.0}

    def test_stats_pusher_attaches_process_block(self):
        """这条链断了的话，仪表板那三张卡永远是"—"——与告警链当初的故障同型。"""
        from core.protocol.stats_push import StatsPusher

        server = _Server()
        pusher = StatsPusher(server, acquisition=_Acq())
        pusher.push_once()

        assert server.frames, "stats 帧没发出去"
        block = server.frames[-1].meta["stats"].get("process")
        assert block is not None
        assert set(block) >= {"pid", "cpu_percent", "cpu_seconds", "rss_bytes", "rss_mb", "cpu_count", "interval_s"}
        assert block["pid"] == os.getpid()
        assert block["cpu_count"] >= 1
        assert block["interval_s"] >= 0

    def test_sampler_is_reused_across_pushes(self, monkeypatch):
        """每次 push 现建采样器的话，差值会退化成"自建实例以来"，本帧间隔就丢了。

        用受控时钟钉死：复用采样器时，第一帧看到的是"自 StatsPusher 构造以来"的增量；
        每次现建的话差值≈0，百分比会变成 0。
        """
        from core.protocol.stats_push import StatsPusher

        fake = _FakeTime()
        monkeypatch.setattr(ps, "time", fake)
        server = _Server()
        pusher = StatsPusher(server, acquisition=_Acq())

        fake.cpu += 50.0
        fake.wall += 50.0
        pusher.push_once()

        first = server.frames[-1].meta["stats"]["process"]
        assert first["interval_s"] == pytest.approx(50.0)
        assert first["cpu_percent"] == pytest.approx(100.0)
