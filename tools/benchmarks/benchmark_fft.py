#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FFT 计算吞吐量基准测试

测量 FFTProcessor 的计算性能

用法:
    python tools/benchmarks/benchmark_fft.py
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

from core.processing.fft_processor import FFTProcessor

# 配置
N_ITERATIONS = 20
WAVEFORM_SIZES = [1024, 2048, 4096, 8192]


def benchmark_fft_computation(
    sample_rate: int = 100_000_000,
    waveform_size: int = 4096,
    n_iterations: int = 20,
) -> dict:
    """
    基准测试 FFT 计算性能

    Args:
        sample_rate: 采样率 (Hz)
        waveform_size: 波形点数
        n_iterations: 迭代次数

    Returns:
        包含基准测试结果的字典
    """
    print(f"FFT 计算基准测试: {waveform_size} 点 × {n_iterations} 次迭代 @ {sample_rate / 1e6:.0f} Msps")
    print("=" * 60)

    # 创建 FFT 处理器
    fft_proc = FFTProcessor(sample_rate=sample_rate)

    # 生成测试波形
    t = np.linspace(0, waveform_size / sample_rate, waveform_size)
    # 添加多个频率分量
    signal = np.zeros(waveform_size)
    for freq in [10e6, 30e6, 50e6, 70e6]:
        signal += np.sin(2 * np.pi * freq * t) * np.random.uniform(0.5, 1.0)

    times = []
    results = []

    for i in range(n_iterations):
        start = time.perf_counter()
        result = fft_proc.compute(signal)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
        results.append(result)

        if (i + 1) % 5 == 0:
            print(f"  迭代 {i + 1}/{n_iterations}: {elapsed:.4f}s ({1 / elapsed:.0f} FFT/s)")

    # 计算统计信息
    mean_time = statistics.mean(times)
    stdev_time = statistics.stdev(times) if len(times) > 1 else 0
    throughput = 1 / mean_time

    benchmark_results = {
        "waveform_size": waveform_size,
        "sample_rate": sample_rate,
        "n_iterations": n_iterations,
        "mean_time_s": mean_time,
        "stdev_s": stdev_time,
        "throughput_fft_per_s": throughput,
        "min_time_s": min(times),
        "max_time_s": max(times),
        "times": times,
    }

    print("\n" + "=" * 60)
    print("结果:")
    print(f"  平均耗时: {mean_time:.6f}s ± {stdev_time:.6f}s")
    print(f"  吞吐量: {throughput:.0f} FFT/s")
    print(f"  最小/最大: {min(times):.6f}s / {max(times):.6f}s")
    print("=" * 60)

    return benchmark_results


def benchmark_fft_band_stats(
    sample_rate: int = 100_000_000,
    waveform_size: int = 4096,
    n_iterations: int = 20,
) -> dict:
    """
    基准测试 FFT 频段统计计算性能

    Args:
        sample_rate: 采样率 (Hz)
        waveform_size: 波形点数
        n_iterations: 迭代次数

    Returns:
        包含基准测试结果的字典
    """
    print(f"\nFFT 频段统计基准测试: {waveform_size} 点 × {n_iterations} 次迭代")
    print("=" * 60)

    fft_proc = FFTProcessor(sample_rate=sample_rate)

    # 生成测试波形
    t = np.linspace(0, waveform_size / sample_rate, waveform_size)
    signal = np.zeros(waveform_size)
    for freq in [10e6, 30e6, 50e6, 70e6]:
        signal += np.sin(2 * np.pi * freq * t) * np.random.uniform(0.5, 1.0)

    times = []

    for i in range(n_iterations):
        # 先计算 FFT
        fft_result = fft_proc.compute(signal)

        # 再计算频段统计
        start = time.perf_counter()
        fft_proc.compute_band_stats(
            fft_result.frequencies,
            fft_result.magnitudes,
        )
        elapsed = time.perf_counter() - start
        times.append(elapsed)

        if (i + 1) % 5 == 0:
            print(f"  迭代 {i + 1}/{n_iterations}: {elapsed:.6f}s ({1 / elapsed:.0f} band_stats/s)")

    mean_time = statistics.mean(times)
    stdev_time = statistics.stdev(times) if len(times) > 1 else 0
    throughput = 1 / mean_time

    results = {
        "waveform_size": waveform_size,
        "mean_time_s": mean_time,
        "stdev_s": stdev_time,
        "throughput_band_stats_per_s": throughput,
        "times": times,
    }

    print("\n" + "=" * 60)
    print("结果:")
    print(f"  平均耗时: {mean_time:.6f}s ± {stdev_time:.6f}s")
    print(f"  吞吐量: {throughput:.0f} band_stats/s")
    print("=" * 60)

    return results


def benchmark_multiple_sizes():
    """测试不同波形大小的 FFT 性能"""
    print("\nFFT 性能 vs 波形大小")
    print("=" * 60)

    sample_rate = 100_000_000
    results = []

    for size in WAVEFORM_SIZES:
        result = benchmark_fft_computation(
            sample_rate=sample_rate,
            waveform_size=size,
            n_iterations=10,
        )
        results.append(result)
        print()

    # 汇总对比
    print("=" * 60)
    print("性能对比汇总:")
    print(f"{'波形大小':>10} {'平均耗时 (ms)':>15} {'吞吐量 (FFT/s)':>15}")
    print("-" * 60)
    for r in results:
        print(f"{r['waveform_size']:>10} {r['mean_time_s'] * 1000:>15.2f} {r['throughput_fft_per_s']:>15.0f}")
    print("=" * 60)

    return results


if __name__ == "__main__":
    print("FFT 性能基准测试套件")
    print()

    # 测试 1: 标准 FFT 计算
    result1 = benchmark_fft_computation(n_iterations=N_ITERATIONS)

    # 测试 2: 频段统计
    result2 = benchmark_fft_band_stats(n_iterations=N_ITERATIONS)

    # 测试 3: 不同波形大小
    result3 = benchmark_multiple_sizes()

    print("\n[完成] FFT 基准测试")
