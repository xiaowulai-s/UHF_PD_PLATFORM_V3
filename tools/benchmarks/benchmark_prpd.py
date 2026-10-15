#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PRPD 性能基准测试

测量 PRPD 处理器处理 1000 个事件的耗时和吞吐量

用法:
    python tools/benchmarks/benchmark_prpd.py
"""

import statistics
import sys
import time
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from core.processing.prpd_processor import PRPDProcessor

# 配置
N_EVENTS = 1000
N_ITERATIONS = 5  # 运行多次取平均值


def benchmark_prpd_processing(n_events: int = 1000, n_iterations: int = 5) -> dict:
    """
    基准测试 PRPD 事件处理性能

    Args:
        n_events: 每次迭代处理的事件数
        n_iterations: 迭代次数

    Returns:
        包含基准测试结果的字典
    """
    print(f"PRPD 性能基准测试: {n_events} 事件 × {n_iterations} 次迭代")
    print("=" * 60)

    times = []

    for i in range(n_iterations):
        # 创建 PRPD 处理器
        prpd = PRPDProcessor(
            phase_bins=360,
            amplitude_bins=256,
            max_amplitude=100.0,
        )

        # 生成随机事件
        events = []
        for _ in range(n_events):
            events.append(
                {
                    "phase": np.random.uniform(0, 360),
                    "amplitude": np.random.uniform(0, 100),
                    "polarity": np.random.choice([-1, 0, 1]),
                    "energy": np.random.uniform(0, 1000),
                }
            )

        # 测量处理时间
        start = time.perf_counter()
        for event in events:
            prpd.add_event(**event)
        elapsed = time.perf_counter() - start

        times.append(elapsed)
        print(f"  迭代 {i + 1}/{n_iterations}: {elapsed:.4f}s ({n_events / elapsed:.0f} events/s)")

    # 计算统计信息
    mean_time = statistics.mean(times)
    stdev_time = statistics.stdev(times) if len(times) > 1 else 0
    throughput = n_events / mean_time

    results = {
        "n_events": n_events,
        "n_iterations": n_iterations,
        "mean_time_s": mean_time,
        "stdev_s": stdev_time,
        "throughput_events_per_s": throughput,
        "min_time_s": min(times),
        "max_time_s": max(times),
        "times": times,
    }

    print("\n" + "=" * 60)
    print("结果:")
    print(f"  平均耗时: {mean_time:.4f}s ± {stdev_time:.4f}s")
    print(f"  吞吐量: {throughput:.0f} events/s")
    print(f"  最小/最大: {min(times):.4f}s / {max(times):.4f}s")
    print("=" * 60)

    return results


def benchmark_prpd_matrix_update(n_updates: int = 100, n_iterations: int = 5) -> dict:
    """
    基准测试 PRPD 矩阵更新性能（模拟实时更新）

    Args:
        n_updates: 每次迭代的更新次数
        n_iterations: 迭代次数

    Returns:
        包含基准测试结果的字典
    """
    print(f"\nPRPD 矩阵更新基准测试: {n_updates} 次更新 × {n_iterations} 次迭代")
    print("=" * 60)

    times = []

    for i in range(n_iterations):
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=100.0)

        # 预填充一些数据
        for _ in range(100):
            prpd.add_event(
                phase=np.random.uniform(0, 360),
                amplitude=np.random.uniform(0, 100),
                polarity=0,
                energy=100,
            )

        # 测量矩阵更新时间
        start = time.perf_counter()
        for _ in range(n_updates):
            prpd.matrix
        elapsed = time.perf_counter() - start

        times.append(elapsed)
        print(f"  迭代 {i + 1}/{n_iterations}: {elapsed:.4f}s ({n_updates / elapsed:.0f} updates/s)")

    mean_time = statistics.mean(times)
    stdev_time = statistics.stdev(times) if len(times) > 1 else 0
    throughput = n_updates / mean_time

    results = {
        "n_updates": n_updates,
        "n_iterations": n_iterations,
        "mean_time_s": mean_time,
        "stdev_s": stdev_time,
        "throughput_updates_per_s": throughput,
        "times": times,
    }

    print("\n" + "=" * 60)
    print("结果:")
    print(f"  平均耗时: {mean_time:.4f}s ± {stdev_time:.4f}s")
    print(f"  吞吐量: {throughput:.0f} updates/s")
    print("=" * 60)

    return results


if __name__ == "__main__":
    print("PRPD 性能基准测试套件")
    print()

    # 测试 1: 事件处理性能
    result1 = benchmark_prpd_processing(n_events=1000, n_iterations=5)

    # 测试 2: 矩阵更新性能
    result2 = benchmark_prpd_matrix_update(n_updates=100, n_iterations=5)

    print("\n[完成] PRPD 基准测试")
