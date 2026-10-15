#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DataBus 发布延迟基准测试 (p99)

测量 PDDataBus 信号发布和接收的端到端延迟

用法:
    python tools/benchmarks/benchmark_databus.py
"""

import statistics
import sys
import time
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QCoreApplication, Slot

from core.foundation.signal_bus import PDDataBus

# 配置
N_MESSAGES = 1000
N_ITERATIONS = 3


class LatencyMeasurement:
    """延迟测量辅助类"""

    def __init__(self):
        self.latencies = []
        self.start_time = None
        self.end_time = None


def benchmark_waveform_publish_latency(n_messages: int = 1000, n_iterations: int = 3) -> dict:
    """
    基准测试波形信号发布延迟

    Args:
        n_messages: 每次迭代发送的消息数
        n_iterations: 迭代次数

    Returns:
        包含基准测试结果的字典
    """
    print(f"DataBus 波形发布延迟基准测试: {n_messages} 消息 × {n_iterations} 次迭代")
    print("=" * 60)

    all_latencies = []

    for iteration in range(n_iterations):
        app = QCoreApplication.instance()
        if app is None:
            app = QCoreApplication(sys.argv)

        bus = PDDataBus.instance()
        meas = LatencyMeasurement()
        meas.latencies = []

        @Slot(str, int, str, object)
        def on_waveform(device_id: str, channel_id: int, sensor_type: str, waveform: object):
            # 计算延迟
            if hasattr(waveform, "_send_time"):
                latency = time.perf_counter() - waveform._send_time
                meas.latencies.append(latency)

        # 连接信号
        bus.waveform_received.connect(on_waveform)

        # 发送波形
        for i in range(n_messages):
            # 创建带时间戳的波形
            waveform = type(
                "WaveformData",
                (),
                {
                    "samples": np.random.randn(4096),
                    "sample_rate": 100_000_000,
                    "_send_time": time.perf_counter(),
                },
            )()
            bus.publish_waveform("bench-dev", 0, "uhf", waveform)

            # 处理事件
            app.processEvents()

        # 断开连接
        bus.waveform_received.disconnect(on_waveform)

        all_latencies.extend(meas.latencies)

        mean_latency = statistics.mean(meas.latencies) if meas.latencies else 0
        print(
            f"  迭代 {iteration + 1}/{n_iterations}: 平均 {mean_latency * 1000:.3f}ms, 收到 {len(meas.latencies)}/{n_messages}"
        )

    # 计算统计信息
    if not all_latencies:
        print("警告: 未收到任何消息")
        return {}

    mean_latency = statistics.mean(all_latencies)
    stdev_latency = statistics.stdev(all_latencies) if len(all_latencies) > 1 else 0
    p50 = statistics.median(all_latencies)
    p95 = sorted(all_latencies)[int(len(all_latencies) * 0.95)]
    p99 = sorted(all_latencies)[int(len(all_latencies) * 0.99)]
    min_latency = min(all_latencies)
    max_latency = max(all_latencies)

    results = {
        "n_messages": n_messages,
        "n_iterations": n_iterations,
        "total_messages_received": len(all_latencies),
        "mean_latency_ms": mean_latency * 1000,
        "stdev_ms": stdev_latency * 1000,
        "p50_ms": p50 * 1000,
        "p95_ms": p95 * 1000,
        "p99_ms": p99 * 1000,
        "min_ms": min_latency * 1000,
        "max_ms": max_latency * 1000,
        "throughput_msg_per_s": 1 / mean_latency if mean_latency > 0 else 0,
    }

    print("\n" + "=" * 60)
    print("结果:")
    print(f"  总消息数: {len(all_latencies)}/{n_messages * n_iterations}")
    print(f"  平均延迟: {mean_latency * 1000:.3f}ms ± {stdev_latency * 1000:.3f}ms")
    print(f"  中位数 (p50): {p50 * 1000:.3f}ms")
    print(f"  p95: {p95 * 1000:.3f}ms")
    print(f"  p99: {p99 * 1000:.3f}ms")
    print(f"  最小/最大: {min_latency * 1000:.3f}ms / {max_latency * 1000:.3f}ms")
    print(f"  吞吐量: {results['throughput_msg_per_s']:.0f} msg/s")
    print("=" * 60)

    return results


def benchmark_alarm_publish_latency(n_messages: int = 1000, n_iterations: int = 3) -> dict:
    """
    基准测试报警信号发布延迟

    Args:
        n_messages: 每次迭代发送的消息数
        n_iterations: 迭代次数

    Returns:
        包含基准测试结果的字典
    """
    print(f"\nDataBus 报警发布延迟基准测试: {n_messages} 消息 × {n_iterations} 次迭代")
    print("=" * 60)

    all_latencies = []

    for iteration in range(n_iterations):
        app = QCoreApplication.instance()
        if app is None:
            app = QCoreApplication(sys.argv)

        bus = PDDataBus.instance()
        meas = LatencyMeasurement()
        meas.latencies = []

        @Slot(str, dict)
        def on_alarm(device_id: str, alarm: dict):
            if "_send_time" in alarm:
                latency = time.perf_counter() - alarm["_send_time"]
                meas.latencies.append(latency)

        bus.pd_alarm_triggered.connect(on_alarm)

        # 发送报警
        for i in range(n_messages):
            alarm = {
                "device_id": "bench-dev",
                "channel_id": 0,
                "level": "high",
                "description": "Benchmark alarm",
                "_send_time": time.perf_counter(),
            }
            bus.publish_alarm("bench-dev", alarm)
            app.processEvents()

        bus.pd_alarm_triggered.disconnect(on_alarm)

        all_latencies.extend(meas.latencies)

        mean_latency = statistics.mean(meas.latencies) if meas.latencies else 0
        print(
            f"  迭代 {iteration + 1}/{n_iterations}: 平均 {mean_latency * 1000:.3f}ms, 收到 {len(meas.latencies)}/{n_messages}"
        )

    if not all_latencies:
        print("警告: 未收到任何消息")
        return {}

    mean_latency = statistics.mean(all_latencies)
    stdev_latency = statistics.stdev(all_latencies) if len(all_latencies) > 1 else 0
    p50 = statistics.median(all_latencies)
    p95 = sorted(all_latencies)[int(len(all_latencies) * 0.95)]
    p99 = sorted(all_latencies)[int(len(all_latencies) * 0.99)]

    results = {
        "n_messages": n_messages,
        "n_iterations": n_iterations,
        "total_messages_received": len(all_latencies),
        "mean_latency_ms": mean_latency * 1000,
        "stdev_ms": stdev_latency * 1000,
        "p50_ms": p50 * 1000,
        "p95_ms": p95 * 1000,
        "p99_ms": p99 * 1000,
    }

    print("\n" + "=" * 60)
    print("结果:")
    print(f"  总消息数: {len(all_latencies)}/{n_messages * n_iterations}")
    print(f"  平均延迟: {mean_latency * 1000:.3f}ms ± {stdev_latency * 1000:.3f}ms")
    print(f"  中位数 (p50): {p50 * 1000:.3f}ms")
    print(f"  p95: {p95 * 1000:.3f}ms")
    print(f"  p99: {p99 * 1000:.3f}ms")
    print("=" * 60)

    return results


def benchmark_ae_hit_publish_latency(n_messages: int = 1000, n_iterations: int = 3) -> dict:
    """
    基准测试 AE hit 信号发布延迟

    Args:
        n_messages: 每次迭代发送的消息数
        n_iterations: 迭代次数

    Returns:
        包含基准测试结果的字典
    """
    print(f"\nDataBus AE Hit 发布延迟基准测试: {n_messages} 消息 × {n_iterations} 次迭代")
    print("=" * 60)

    all_latencies = []

    for iteration in range(n_iterations):
        app = QCoreApplication.instance()
        if app is None:
            app = QCoreApplication(sys.argv)

        bus = PDDataBus.instance()
        meas = LatencyMeasurement()
        meas.latencies = []

        @Slot(str, int, dict)
        def on_ae_hit(device_id: str, channel_id: int, hit_data: dict):
            if "_send_time" in hit_data:
                latency = time.perf_counter() - hit_data["_send_time"]
                meas.latencies.append(latency)

        bus.ae_hit_detected.connect(on_ae_hit)

        # 发送 AE hit
        for i in range(n_messages):
            hit_data = {
                "amplitude": 50.0,
                "counts": 3,
                "marse_energy": 100.0,
                "_send_time": time.perf_counter(),
            }
            bus.publish_ae_hit("bench-dev", 0, hit_data)
            app.processEvents()

        bus.ae_hit_detected.disconnect(on_ae_hit)

        all_latencies.extend(meas.latencies)

        mean_latency = statistics.mean(meas.latencies) if meas.latencies else 0
        print(
            f"  迭代 {iteration + 1}/{n_iterations}: 平均 {mean_latency * 1000:.3f}ms, 收到 {len(meas.latencies)}/{n_messages}"
        )

    if not all_latencies:
        print("警告: 未收到任何消息")
        return {}

    mean_latency = statistics.mean(all_latencies)
    stdev_latency = statistics.stdev(all_latencies) if len(all_latencies) > 1 else 0
    p50 = statistics.median(all_latencies)
    p95 = sorted(all_latencies)[int(len(all_latencies) * 0.95)]
    p99 = sorted(all_latencies)[int(len(all_latencies) * 0.99)]

    results = {
        "n_messages": n_messages,
        "n_iterations": n_iterations,
        "total_messages_received": len(all_latencies),
        "mean_latency_ms": mean_latency * 1000,
        "stdev_ms": stdev_latency * 1000,
        "p50_ms": p50 * 1000,
        "p95_ms": p95 * 1000,
        "p99_ms": p99 * 1000,
    }

    print("\n" + "=" * 60)
    print("结果:")
    print(f"  总消息数: {len(all_latencies)}/{n_messages * n_iterations}")
    print(f"  平均延迟: {mean_latency * 1000:.3f}ms ± {stdev_latency * 1000:.3f}ms")
    print(f"  中位数 (p50): {p50 * 1000:.3f}ms")
    print(f"  p95: {p95 * 1000:.3f}ms")
    print(f"  p99: {p99 * 1000:.3f}ms")
    print("=" * 60)

    return results


if __name__ == "__main__":
    import numpy as np

    print("DataBus 发布延迟基准测试套件")
    print()

    # 测试 1: 波形发布延迟
    result1 = benchmark_waveform_publish_latency(n_messages=1000, n_iterations=3)

    # 测试 2: 报警发布延迟
    result2 = benchmark_alarm_publish_latency(n_messages=1000, n_iterations=3)

    # 测试 3: AE hit 发布延迟
    result3 = benchmark_ae_hit_publish_latency(n_messages=1000, n_iterations=3)

    print("\n" + "=" * 60)
    print("DataBus 延迟基准测试完成")
    print("=" * 60)
