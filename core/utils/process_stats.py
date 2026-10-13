"""本进程的 CPU / 驻留内存读数（零第三方依赖）。

为什么不用 psutil：目标机不装 Python，内核是 PyInstaller 冻结产物，多一个依赖就多
一份产物体积与供应链面；而这里只要两个数——本进程的 RSS 与 CPU 占用率。CPU 时间用
标准库 `time.process_time()`（跨平台，user+sys）；RSS 在 Windows 上走
`psapi.GetProcessMemoryInfo`，在 POSIX 上读 `/proc/self/statm`。

**CPU 百分比必须按两次采样求差**：`process_time()` 给的是累计 CPU 秒，单次调用拿不到
占用率。所以采样器要跨调用复用（`StatsPusher` 持有一个实例），不能每次现建一个——
现建的话第一次采样的差值退化成"自建实例以来的平均"，读数会一直偏低且滞后。

口径（与 psutil.Process.cpu_percent 一致）：`cpu_percent` 以**单核为 100%**，
多核上可以超过 100（例如 8 核满载约 800）。`cpu_count` 一并给出，便于换算成整机占比。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024


class _ProcessMemoryCounters(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS（psapi.h）。"""

    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _windows_resident_bytes() -> Optional[int]:
    """Windows 上取工作集（Working Set）字节数。

    用工作集而不是私有提交量：现场关心的是"这台机器被吃掉了多少物理内存"，
    工作集是那个口径（旧 `PerformanceMonitorWidget` 显示的 RSS 也是它）。

    **必须显式声明函数原型**：`GetCurrentProcess()` 返回的是伪句柄 `(HANDLE)-1`，
    64 位下是 0xFFFFFFFFFFFFFFFF。ctypes 默认把返回值当 `c_int`，会把它截成
    0x00000000FFFFFFFF，再传回 `GetProcessMemoryInfo` 就不是合法伪句柄了——
    表现是调用返回 0、RSS 恒为 None，而**没有任何报错**（实测踩过）。
    """
    try:
        psapi = ctypes.WinDLL("psapi.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")
    except (OSError, AttributeError) as exc:
        logger.debug("无法加载 psapi/kernel32: %s", exc)
        return None

    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int

    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
        handle = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    except (OSError, AttributeError) as exc:
        logger.debug("GetProcessMemoryInfo 调用失败: %s", exc)
        return None
    if not ok:
        logger.debug("GetProcessMemoryInfo 返回 0（伪句柄或原型不对）")
        return None
    return int(counters.WorkingSetSize)


def _posix_resident_bytes() -> Optional[int]:
    """POSIX 上读 /proc/self/statm 的第二个字段（常驻页数）× 页大小。"""
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as fh:
            fields = fh.read().split()
        return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, IndexError, ValueError, AttributeError) as exc:
        logger.debug("读取 /proc/self/statm 失败: %s", exc)
        return None


def resident_bytes() -> Optional[int]:
    """本进程的常驻内存字节数；取不到时返回 None（而不是 0——0 会被读成"没占用"）。"""
    if sys.platform == "win32":
        return _windows_resident_bytes()
    return _posix_resident_bytes()


class ProcessStatsSampler:
    """跨调用求差得到 CPU 占用率。线程安全（stats 推送在 Qt 主线程，但不假定）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_cpu = time.process_time()
        self._last_wall = time.monotonic()

    def sample(self) -> Dict[str, Any]:
        with self._lock:
            cpu = time.process_time()
            wall = time.monotonic()
            delta_cpu = cpu - self._last_cpu
            delta_wall = wall - self._last_wall
            self._last_cpu = cpu
            self._last_wall = wall

        percent = (delta_cpu / delta_wall * 100.0) if delta_wall > 0 else 0.0
        rss = resident_bytes()
        return {
            "pid": os.getpid(),
            "cpu_percent": round(percent, 1),
            "cpu_seconds": round(cpu, 2),
            "rss_bytes": rss,
            "rss_mb": round(rss / _BYTES_PER_MB, 1) if rss is not None else None,
            "cpu_count": os.cpu_count() or 1,
            # 两次采样的间隔：前端用它判断"这个百分比是多久的平均"，
            # 缺了它，stats 帧间隔一变（或某帧被丢弃）读数就无法解释。
            "interval_s": round(delta_wall, 3),
        }
