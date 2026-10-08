# -*- coding: utf-8 -*-
"""sidecar 常驻基线归因探针。

回答两个分开的问题（混在一起会得出错误结论）：

A. **库**要多少？每个前缀起独立子进程测，因为同一进程里后一步会把前一步的
   惰性导入/编译缓存混进来，减出来的差值不可信。
B. **启动路径与数据结构**要多少？在同一进程里按 sidecar 真实调用顺序逐步测
   RSS 差值（总线 / 采集服务 / 数据库 / 告警 / 数据源 / WS 服务 / 仿真起流）。

注意 RSS 在 Windows 上是 WorkingSetSize，含与别的进程共享的 DLL 页；所以同时报
PrivateWorkingSet，判定"能不能省"要看后者。

用法：
    python tools/mem_baseline_probe.py            # A+B
    python tools/mem_baseline_probe.py --only a   # 只跑 A
    python tools/mem_baseline_probe.py --only b --minutes 3
"""

from __future__ import annotations

import argparse
import gc
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MB = 1048576

# 直接跑脚本时 sys.path[0] 是 tools/，不补上仓库根就 import 不到 core
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CHILD = r"""
import gc, json, sys
for m in sys.argv[1].split("|"):
    if m:
        __import__(m)
gc.collect()
import psutil
p = psutil.Process()
mi = p.memory_info()
try:
    uss = p.memory_full_info().uss
except Exception:
    uss = 0
print(json.dumps({"rss": mi.rss, "private": mi.private, "uss": uss}))
"""

BASE = ["psutil"]

# 只写"这一步新增加什么"，前缀由程序累加——这样 Δ 不可能被写错成非累加行。
ADDITIONS = [
    ("numpy", ["numpy"]),
    ("numpy.fft + linalg", ["numpy.fft", "numpy.linalg"]),
    ("scipy.signal", ["scipy.signal"]),
    ("scipy.ndimage", ["scipy.ndimage"]),
    ("PySide6.QtCore", ["PySide6.QtCore"]),
    ("msgpack", ["msgpack"]),
    ("websockets", ["websockets"]),
    ("sqlalchemy", ["sqlalchemy"]),
    ("core.utils.logger", ["core.utils.logger"]),
    ("core.processing (DSP 全集)", ["core.processing"]),
    ("core.protocol", ["core.protocol"]),
    ("core.foundation.signal_bus", ["core.foundation.signal_bus"]),
    ("core.data (数据库层)", ["core.data"]),
    ("core.services.*", ["core.services.acquisition_service", "core.services.alarm_service"]),
    ("core.foundation.data_source_manager", ["core.foundation.data_source_manager"]),
]


def measure(modules):
    r = subprocess.run(
        [sys.executable, "-c", CHILD, "|".join(modules)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        return None, tail[-1][:90] if tail else "failed"
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
        return d, None
    except Exception:
        return None, "unparsable"


def part_a():
    print("=" * 92)
    print("A. 库的常驻成本（累加导入，每行独立子进程）")
    print("   RSS=WorkingSet(含共享 DLL 页)  USS=独占集(不含共享页，更接近真能还多少)")
    print("=" * 92)
    print("%-34s %8s %8s | %8s %8s" % ("累加到", "RSS", "USS", "ΔRSS", "ΔUSS"))
    prefix = list(BASE)
    base = measure(prefix)
    if base[0] is None:
        print("基线探测失败:", base[1])
        return
    pr, pu = base[0]["rss"] / MB, base[0]["uss"] / MB
    d_uss = pu
    print("%-34s %8.1f %8.1f | %8s %8s" % ("解释器+psutil", pr, pu, "-", "-"))
    for label, mods in ADDITIONS:
        prefix = prefix + mods
        d, err = measure(prefix)
        if d is None:
            print("%-34s  探测失败: %s" % (label, err))
            continue
        rss, uss = d["rss"] / MB, d["uss"] / MB
        print("%-34s %8.1f %8.1f | %8.1f %8.1f" % (label, rss, uss, rss - pr, uss - d_uss), flush=True)
        d_uss = uss
        pr = rss
    print()
    print("注: psutil 在 Windows 的 `private` 字段是 private page bytes，可以大于 RSS，别当工作集用。")


def part_b(minutes: float, trace: bool = False):
    """按 sidecar.main() 的真实调用顺序起服务，并在 **Qt 事件循环里**采样。

    这一步踩过坑：早先的写法只 `QCoreApplication(...)` 而不 `exec()`，于是
    SidecarServer 的 10ms flush 定时器永远不触发，待发帧在内部队列里越堆越多，
    15 秒涨了 200 MB——那是夹具的毛病，不是应用的。所以现在必须真的跑事件循环，
    并且把 RPC/log-stream/StatsPusher 这些 main() 里实际会装的东西一起装上。
    """
    print()
    print("=" * 78)
    print("B. sidecar 启动路径逐步归因（事件循环在跑，仿真 @60000 pulse/s）")
    print("=" * 78)

    import psutil
    from PySide6.QtCore import QCoreApplication, QTimer

    # tracemalloc 会给每次分配挂 15 帧回溯：实测把同一负载的 RSS 从 249 MB 顶到
    # 594 MB，而且 take_snapshot() 在这个量级上要跑几分钟直接把探针卡死。
    # 所以默认不开；开了就只读 get_traced_memory()（它不含 tracemalloc 自己的域，
    # 仍可当"可达 Python 对象总量"用），但那一轮的 RSS 一律作废，不能当基线。
    if trace:
        import tracemalloc

        # 1 帧就够做文件/行归因；15 帧既把 RSS 撑到 2.4 倍，又让 take_snapshot()
        # 在 160 MB 存活分配上直接卡死（实测超时）。
        tracemalloc.start(1)

    proc = psutil.Process()
    holds = {}
    out = {}

    app = QCoreApplication.instance() or QCoreApplication([sys.argv[0]])

    def step(name, fn=None):
        gc.collect()
        before = proc.memory_info().rss / MB
        val = fn() if fn else None
        if val is not None:
            holds[name] = val
        gc.collect()
        after = proc.memory_info().rss / MB
        print("%-42s %8.1f MB  (Δ %+.1f)" % (name, after, after - before), flush=True)
        return val

    step("解释器 + psutil + 本探针")
    step(
        "setup_logging(含文件 handler)",
        lambda: __import__("core.utils.logger", fromlist=["setup_logging"]).setup_logging(
            log_level="WARNING", log_file=str(REPO / "logs" / "probe.log")
        ),
    )
    step("QCoreApplication", lambda: app)
    step(
        "PDDataBus.instance()",
        lambda: __import__("core.foundation.signal_bus", fromlist=["PDDataBus"]).PDDataBus.instance(),
    )
    step(
        "AcquisitionService",
        lambda: __import__("core.services.acquisition_service", fromlist=["AcquisitionService"]).AcquisitionService(
            sample_rate=100_000_000, waveform_points=4096
        ),
    )
    step(
        "PDAlarmService",
        lambda: __import__("core.services.alarm_service", fromlist=["PDAlarmService"]).PDAlarmService(db_manager=None),
    )
    step(
        "DataSourceManager",
        lambda: __import__("core.foundation.data_source_manager", fromlist=["DataSourceManager"]).DataSourceManager(
            acquisition_service=holds["AcquisitionService"],
            data_bus=holds["PDDataBus.instance()"],
            db_manager=None,
            alarm_service=holds["PDAlarmService"],
        ),
    )
    step("SidecarServer.start()", lambda: _server())
    step(
        "register_storage_rpc（无 db）",
        lambda: __import__("core.protocol.storage_rpc", fromlist=["register_storage_rpc"]).register_storage_rpc(
            holds["SidecarServer.start()"], db_manager=None, storage=None
        ),
    )
    step(
        "register_write_rpc（无 db）",
        lambda: __import__("core.protocol.write_rpc", fromlist=["register_write_rpc"]).register_write_rpc(
            holds["SidecarServer.start()"],
            db_manager=None,
            acquisition=holds["AcquisitionService"],
            alarm=holds["PDAlarmService"],
        ),
    )
    step(
        "attach_log_stream",
        lambda: __import__("core.protocol.log_stream", fromlist=["attach"]).attach(
            holds["SidecarServer.start()"], level=30
        ),
    )
    step("StatsPusher.start()", lambda: _pusher(holds))
    startup_mb = proc.memory_info().rss / MB
    step("加 UHF 仿真源并 start", lambda: _sim(holds["AcquisitionService"], holds["DataSourceManager"]))

    print()
    print("启动完成 %.1f MB，开始采样 %.1f 分钟（无客户端）" % (proc.memory_info().rss / MB, minutes))

    t0 = time.time()
    samples = []

    def tick():
        rss = proc.memory_info().rss / MB
        samples.append(rss)
        print(
            "   t=%4.1f min  RSS=%7.1f MB  (Δ from 启动 %+.1f)" % ((time.time() - t0) / 60, rss, rss - startup_mb),
            flush=True,
        )

    def census():
        print()
        print("   已知容器实测（这些是能被裁剪的部分，库的常驻不是）:")
        try:
            pm = getattr(holds["AcquisitionService"], "_proc_mgr", None)
            if pm is None:
                print("      AcquisitionService 上没有 _proc_mgr，属性名变了")
            else:
                for k, b in getattr(pm, "_ring_buffers", {}).items():
                    buf = getattr(b, "_buffer", None)
                    n = len(buf) if buf is not None else 0
                    # deque 里存的是 Python float：对象 24B + 块指针 8B
                    print(
                        "      RingBuffer[%s] 长度=%s/%s  估算=%.1f MB"
                        % (k, n, getattr(b, "_maxlen", "?"), n * 32 / MB)
                    )
                for k, p in getattr(pm, "_prpd_processors", {}).items():
                    print(
                        "      PRPD[%s] events=%s/%s  matrix=%.1f MB  事件对象估算=%.1f MB"
                        % (k, len(p._events), p._max_events, p._matrix.nbytes / MB, len(p._events) * 200 / MB)
                    )
                for k, p in getattr(pm, "_prps_processors", {}).items():
                    arr = getattr(p, "_stack", None) or getattr(p, "_matrix", None)
                    if arr is not None:
                        print(
                            "      PRPS[%s] 数组=%s %.1f MB"
                            % (k, getattr(arr, "shape", "?"), getattr(arr, "nbytes", 0) / MB)
                        )
        except Exception as e:
            print("      容器枚举失败:", type(e).__name__, e)

        # 区分两种性质完全不同的开销：可达的 Python 对象，还是 numpy/scipy 每帧
        # 分配-释放后 arenas 不还（前者能靠减容器解决，后者只能减每帧分配量）。
        if trace:
            try:
                import tracemalloc

                cur, peak = tracemalloc.get_traced_memory()
                print()
                msg = "      [traced 轮] 存活 Python 对象分配 %.1f MB（峰值 %.1f MB）"
                print(msg % (cur / MB, peak / MB))
                print("      本轮 RSS %.1f MB 是被 tracemalloc 撑起来的，不作基线。" % (proc.memory_info().rss / MB))
                snap = tracemalloc.take_snapshot()
                # linecache 是探针自己读源码造成的：statistics() 渲染 traceback 会逐行
                # 读源文件，动辄几千万条。别把它当成应用的占用报出去，所以先量一次。
                import linecache

                # linecache 条目是 (size, mtime, lines, fullname)——第二项是 mtime(float)，
                # 别当源码长度用。
                lc_bytes = sum(
                    v[0] for v in linecache.cache.values() if isinstance(v, tuple) and v and isinstance(v[0], int)
                )
                print(
                    "      linecache（渲染前）: %d 条 / %.1f MB —— 探针产物，不计入应用"
                    % (len(linecache.cache), lc_bytes / MB)
                )
                print("      存活分配按行 top12:")
                for st in snap.statistics("traceback")[:12]:
                    tb = str(st.traceback).replace("\n", " ")
                    tb = tb.replace(str(REPO) + "\\", "").replace(str(REPO), "")
                    print("        %7.1f MB n=%-8d %s" % (st.size / MB, st.count, tb[:130]), flush=True)
                print("      按文件 top8:")
                for st in snap.statistics("filename")[:8]:
                    fn = str(st.traceback).replace(str(REPO) + "\\", "").replace(str(REPO), "")
                    print("        %7.1f MB n=%-8d %s" % (st.size / MB, st.count, fn[:110]), flush=True)
            except Exception as e:
                print("      tracemalloc 读失败:", type(e).__name__, e)
        else:
            print()
            print("      想要「可达 Python 对象总量」就加 --trace（那一轮 RSS 作废）。")
        if samples:
            tail = samples[-max(1, len(samples) // 3) :]
            print()
            print(
                "   启动 %.1f MB → 末次 %.1f MB；后 1/3 均值 %.1f MB；斜率 %+.1f MB/min"
                % (startup_mb, samples[-1], sum(tail) / len(tail), (samples[-1] - samples[0]) / max(0.1, (minutes)))
            )
        out["done"] = True
        app.quit()

    tmr = QTimer()
    tmr.setInterval(15000)
    tmr.timeout.connect(tick)
    tmr.start()
    end = QTimer()
    end.setSingleShot(True)
    end.setInterval(int(minutes * 60000))
    end.timeout.connect(census)
    end.start()
    app.exec()
    return holds


def _pusher(holds):
    from core.protocol.stats_push import StatsPusher

    p = StatsPusher(
        holds["SidecarServer.start()"],
        acquisition=holds["AcquisitionService"],
        alarm=holds["PDAlarmService"],
        sources=holds["DataSourceManager"],
        storage=None,
    )
    p.start()
    return p


def _qapp():
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    return app


def _server():
    from core.protocol import SidecarServer

    bus = __import__("core.foundation.signal_bus", fromlist=["PDDataBus"]).PDDataBus.instance()
    server = SidecarServer(bus)
    server.start(host="127.0.0.1", port=0)
    return server


def _sim(acquisition, sources):
    sources.add_simulator_source("uhf", "corona", 20, 60000.0)
    sources.start_all()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["a", "b", "ab"], default="ab")
    ap.add_argument("--minutes", type=float, default=2.0)
    ap.add_argument(
        "--trace", action="store_true", help="开 tracemalloc 只读存活对象总量；注意这一轮 RSS 会被撑大而作废"
    )
    args = ap.parse_args()
    if args.only in ("a", "ab"):
        part_a()
    if args.only in ("b", "ab"):
        part_b(args.minutes, args.trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
