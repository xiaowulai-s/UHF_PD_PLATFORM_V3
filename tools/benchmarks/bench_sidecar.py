"""端到端吞吐探针：真实启动 sidecar + 模拟器，用 WebSocket 客户端收帧并解码校验。

    python tools/benchmarks/bench_sidecar.py --fps 20 --duration 8

验证三件事：
  1. 目标 FPS 下波形/FFT/PRPD/PRPS 全 topic 能否稳定送达；
  2. seq 是否跳变（跳变即丢帧，有损 topic 属预期，reliable topic 不可接受）；
  3. float32 负载能否原样解出声明的形状。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import msgpack

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover
    from websockets import connect as ws_connect  # type: ignore[no-redef]

_DTYPES = {"f4": "float32", "f8": "float64", "i4": "int32"}


@dataclass
class TopicStat:
    count: int = 0
    bytes_total: int = 0
    last_seq: int = -1
    gaps: int = 0
    shapes: set = field(default_factory=set)
    decode_errors: int = 0


class Collector:
    """在独立线程跑 asyncio 的收流客户端，主线程继续驱动 Qt 事件循环。"""

    def __init__(self, url: str, token: str):
        self.url = f"{url}?token={token}"
        self.stats: dict[str, TopicStat] = defaultdict(TopicStat)
        self.hello: dict = {}
        self.errors: list[str] = []
        self.done = threading.Event()
        self._ws = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start(self) -> None:
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._session(), self._loop)

    async def _session(self) -> None:
        try:
            async with ws_connect(self.url, max_size=None) as ws:
                self._ws = ws
                async for raw in ws:
                    self._handle(bytes(raw))
        except Exception as exc:
            self.errors.append(f"连接中断: {exc!r}")
        finally:
            self._ws = None
            self.done.set()

    def _handle(self, raw: bytes) -> None:
        try:
            body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        except Exception as exc:
            self.errors.append(f"解包失败: {exc}")
            return

        kind = body.get("k")
        if kind == "hello":
            self.hello = body
            return
        if kind not in ("tele", "event"):
            return

        stat = self.stats[body.get("t", "?")]
        stat.count += 1
        stat.bytes_total += len(raw)

        seq = int(body.get("q", 0))
        if stat.last_seq >= 0 and seq != stat.last_seq + 1:
            stat.gaps += 1
        stat.last_seq = seq

        payload = body.get("p")
        if payload is None:
            return
        meta = body.get("m") or {}
        code = meta.get("dtype", "f4")
        if code not in _DTYPES:
            stat.decode_errors += 1
            self.errors.append(f"未知 dtype: {code}")
            return
        try:
            import numpy as np

            arr = np.frombuffer(payload, dtype=np.dtype(_DTYPES[code]))
            shape = tuple(meta.get("shape") or [])
            if shape:
                arr = arr.reshape(shape)
            if arr.size * arr.itemsize != len(payload):
                stat.decode_errors += 1
                self.errors.append("形状与字节数不一致")
            stat.shapes.add(arr.shape)
        except Exception as exc:
            stat.decode_errors += 1
            self.errors.append(f"reshape {meta.get('shape')}: {exc}")

    def close(self, pump=None) -> None:
        """优雅关闭需要 sidecar 的 Qt 事件循环继续推进，否则关闭握手会卡死。"""
        ws = self._ws
        if ws is not None:
            asyncio.run_coroutine_threadsafe(ws.close(), self._loop)
            deadline = time.monotonic() + 3.0
            while not self.done.is_set() and time.monotonic() < deadline:
                if pump is not None:
                    pump()
                time.sleep(0.002)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


def _pump_for(app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)


def main() -> int:
    parser = argparse.ArgumentParser(description="sidecar 端到端吞吐探针")
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--sim-type", choices=["uhf", "ae"], default="uhf")
    parser.add_argument("--pd-type", default="internal")
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QCoreApplication

    from core.foundation.data_source_manager import DataSourceManager
    from core.foundation.signal_bus import PDDataBus
    from core.protocol import SidecarServer
    from core.services.acquisition_service import AcquisitionService

    app = QCoreApplication([])
    bus = PDDataBus.instance()
    acquisition = AcquisitionService(sample_rate=100_000_000, waveform_points=4096)
    server = SidecarServer(bus)
    port, token = server.start(port=0)

    client = Collector(f"ws://127.0.0.1:{port}/stream", token)
    client.start()

    sources = DataSourceManager(acquisition_service=acquisition, data_bus=bus)
    sources.add_simulator_source(args.sim_type, args.pd_type, args.fps)
    sources.start_all()

    print(f"sidecar:{port}  收流 {args.duration:g}s @ {args.fps} FPS ...", flush=True)
    t0 = time.monotonic()
    deadline = t0 + args.duration
    while time.monotonic() < deadline:
        # Qt 事件循环停住，服务端就一帧都发不出去
        app.processEvents()
        time.sleep(0.002)
    elapsed = time.monotonic() - t0

    diag = server.diagnostics()
    stats = acquisition.get_stats()

    sources.stop_all()
    acquisition.stop()
    client.close(pump=app.processEvents)
    server.stop()
    _pump_for(app, 0.2)

    return _report(client, stats, diag, elapsed)


def _report(client: Collector, stats: dict, diag: dict, elapsed: float) -> int:
    print(f"\n{'topic':<15}{'frames':>7}{'avgBytes':>11}{'KB/s':>10}{'seqGaps':>10}{'decErr':>9}  shapes")
    print("-" * 76)

    frames = 0
    payload_bytes = 0
    reliable_gaps = 0
    qos = client.hello.get("topics", {})
    for topic, st in sorted(client.stats.items()):
        frames += st.count
        payload_bytes += st.bytes_total
        if qos.get(topic) == "reliable":
            reliable_gaps += st.gaps
        avg = st.bytes_total // st.count if st.count else 0
        shapes = ", ".join(str(s) for s in sorted(st.shapes)[:3]) or "-"
        print(
            f"{topic:<15}{st.count:>7}{avg:>11}"
            f"{st.bytes_total / elapsed / 1024:>10.1f}{st.gaps:>10}{st.decode_errors:>9}  {shapes}"
        )

    print("-" * 76)
    print(
        f"received {frames} frames / {payload_bytes / 1024:.0f} KB"
        f" = {payload_bytes / elapsed / 1024:.1f} KB/s, {frames / elapsed:.1f} fps"
    )
    print(
        f"server sent {diag['sent_frames']}, dropped-for-backpressure {diag['dropped_frames']}"
        f"; acquisition processed {stats.get('waveforms_processed', 0)} waveforms"
    )

    decode_errors = sum(s.decode_errors for s in client.stats.values())
    if client.errors:
        counts = Counter(e.split(":")[0] for e in client.errors)
        print(f"errors {len(client.errors)} {dict(counts)}, samples: {client.errors[:2]}")

    print("payload decode: " + ("all shapes verified" if decode_errors == 0 else f"{decode_errors} failures"))
    if reliable_gaps:
        print(f"RELIABLE topic frame loss detected: {reliable_gaps} gaps")
    return 0 if decode_errors == 0 and reliable_gaps == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
