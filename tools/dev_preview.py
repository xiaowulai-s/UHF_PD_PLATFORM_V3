r"""起一对配套的 sidecar + vite 预览环境，一条命令。

为什么需要它：sidecar 的 token 每次启动都随机（这是设计，不能当参数传），
而浏览器预览（非 Electron）没有 preload，只能靠 vite 的 VITE_SIDECAR_TOKEN 注入。
于是每次重启内核都要跟着重启 vite，还要手工对齐两个值；漏掉一步的表现是
sidecar 日志里一行"拒绝连接：token 不匹配"，而页面上只显示"未连接"，看不出原因。
M4 要在浏览器里反复看图，这个舞蹈会跳几十次。

    python tools/dev_preview.py                 # 端口 5610 / 前端 5199
    python tools/dev_preview.py --fps 20 --duration 3600

Ctrl-C 同时收掉两个子进程。
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path
from queue import Empty, Queue

REPO = Path(__file__).resolve().parents[1]
PORT_RE = re.compile(r"ws://127\.0\.0\.1:(\d+)/stream")
TOKEN_RE = re.compile(r"^\s*token\s+(\S+)\s*$")


def _pump(pipe, sink: list[str], lines: Queue[str]) -> None:
    for raw in iter(pipe.readline, ""):
        line = raw.rstrip()
        sink.append(line)
        lines.put(line)
    pipe.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="sidecar + vite 联调预览")
    parser.add_argument("--port", type=int, default=5610, help="sidecar 监听端口")
    parser.add_argument("--web-port", type=int, default=5199, help="vite 端口")
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--duration", type=int, default=3600, help="sidecar 运行秒数")
    parser.add_argument("--sim-type", choices=["uhf", "ae"], default="uhf")
    args = parser.parse_args()

    procs: list[subprocess.Popen] = []

    def shutdown(*_a) -> None:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    sidecar = subprocess.Popen(
        [
            sys.executable,
            "sidecar.py",
            "--port",
            str(args.port),
            "--fps",
            str(args.fps),
            "--duration",
            str(args.duration),
            "--sim-type",
            args.sim_type,
        ],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "QT_QPA_PLATFORM": "offscreen"},
    )
    procs.append(sidecar)

    lines: Queue[str] = Queue()
    tail: list[str] = []
    threading.Thread(target=_pump, args=(sidecar.stdout, tail, lines), daemon=True).start()

    token = None
    port = None
    try:
        while token is None or port is None:
            line = lines.get(timeout=60)
            print(f"[sidecar] {line}", flush=True)
            if (m := PORT_RE.search(line)) is not None:
                port = int(m.group(1))
            elif (m := TOKEN_RE.match(line)) is not None:
                token = m.group(1)
    except Empty:
        print("[FAIL] 60s 内没等到 sidecar 的端口与 token，见上面日志")
        shutdown()
        return 1

    vite = subprocess.Popen(
        ["npx", "vite", "--port", str(args.web_port)],
        cwd=REPO / "desktop",
        stdout=None,
        stderr=None,
        shell=os.name == "nt",
        env={**os.environ, "VITE_SIDECAR_PORT": str(port), "VITE_SIDECAR_TOKEN": token},
    )
    procs.append(vite)

    print(f"\n  预览   http://localhost:{args.web_port}/#/monitor")
    print(f"  内核   ws://127.0.0.1:{port}/stream  token={token}\n", flush=True)

    # sidecar 的后续日志继续打出来；vite 直接继承本进程 stdio
    while sidecar.poll() is None and vite.poll() is None:
        try:
            print(f"[sidecar] {lines.get(timeout=1)}", flush=True)
        except Empty:
            continue
    shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
