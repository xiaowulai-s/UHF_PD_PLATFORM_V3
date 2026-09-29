r"""冻结产物冒烟测试：确认 dist/uhf-sidecar/ 里的 exe 真的在出帧，而不只是能启动。

起因是一次真实事故：sidecar.spec 误排了 stdlib 的 unittest，冻结产物里
scipy.signal -> numpy.testing -> unittest 断链，peak_detector 每帧抛
ModuleNotFoundError，而 sidecar 照样打印 "sidecar ready"、进程也一直活着，
只有"处理波形数"是 0。"能启动"和"能干活"是两件事，这个脚本量后者。

用法：

    python -m PyInstaller sidecar.spec --noconfirm
    python tools/verify_frozen_sidecar.py            # 默认 8 秒
    python tools/verify_frozen_sidecar.py --seconds 20 --expect-prps

退出码非 0 表示产物不可交付。检查项都对齐 desktop/src/main/sidecar.ts 的实际契约
（同一套 stdout 端口/token 正则），所以它也顺带验证了守护逻辑能不能解析到端点。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

import msgpack
from websockets.asyncio.client import connect as ws_connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.protocol.frames import unpack_array  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
EXE = REPO / "dist" / "uhf-sidecar" / "uhf-sidecar.exe"

# 别把冒烟数据写进真实仓库的 data/monitor.db
_SCRATCH_STATE = tempfile.mkdtemp(prefix="uhf-frozen-smoke-")

TOPICS = ["waveform", "fft", "prpd", "prps", "ae_envelope", "ae_hit", "stats"]

# 与 desktop/src/main/sidecar.ts 的 PORT_RE / TOKEN_RE 一致
PORT_RE = re.compile(r"ws://127\.0\.0\.1:(\d+)/stream")
TOKEN_RE = re.compile(r"^\s*token\s+(\S+)\s*$")


async def _collect(url: str, seconds: float) -> list[dict]:
    frames: list[dict] = []
    async with ws_connect(url, max_size=None) as ws:
        await ws.send(msgpack.packb({"k": "sub", "topics": TOPICS}))
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return frames
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                return frames
            msg = msgpack.unpackb(bytes(raw), raw=False)
            # 连上时服务端先推一条 hello（握手描述），它没有 topic 字段
            if isinstance(msg, dict) and "t" in msg:
                frames.append(msg)


def main() -> int:
    parser = argparse.ArgumentParser(description="冻结 sidecar 产物冒烟测试")
    parser.add_argument("--seconds", type=float, default=8.0, help="订阅采样时长")
    parser.add_argument("--expect-prps", action="store_true", help="额外要求收到 PRPS 帧")
    args = parser.parse_args()

    if not EXE.is_file():
        print(f"[FAIL] 找不到产物 {EXE}，先跑 python -m PyInstaller sidecar.spec")
        return 1

    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONIOENCODING": "utf-8", "PD_STATE_DIR": _SCRATCH_STATE}
    return asyncio.run(_run(env=env, seconds=args.seconds, expect_prps=args.expect_prps))


async def _run(*, env: dict, seconds: float, expect_prps: bool) -> int:
    child = await asyncio.create_subprocess_exec(
        str(EXE),
        "--duration",
        "0",
        "--fps",
        "20",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    port: int | None = None
    token: str | None = None
    try:
        while port is None or token is None:
            line_bytes = await asyncio.wait_for(child.stdout.readline(), timeout=30)
            if not line_bytes:
                print("[FAIL] 产物在打印端点之前就退出了")
                return 1
            line = line_bytes.decode("utf-8", "replace").rstrip()
            if (hit := PORT_RE.search(line)) is not None:
                port = int(hit.group(1))
            elif (hit := TOKEN_RE.match(line)) is not None:
                token = hit.group(1)

        print(f"[ok] 端点解析成功 port={port}（与 sidecar.ts 同一套正则）")
        frames = await _collect(f"ws://127.0.0.1:{port}/stream?token={token}", seconds)
    finally:
        child.terminate()
        try:
            await asyncio.wait_for(child.wait(), timeout=10)
        except (asyncio.TimeoutError, TimeoutError):
            child.kill()

    if not frames:
        print("[FAIL] 一帧都没收到 —— 推流路径断了")
        return 1

    by_topic: dict[str, int] = {}
    for f in frames:
        by_topic[f["t"]] = by_topic.get(f["t"], 0) + 1
    print("[ok] 收到帧:", by_topic)

    rc = 0
    if "waveform" not in by_topic:
        print("[FAIL] 没有 waveform 帧：采集或管线未跑通")
        rc = 1

    for topic in ("prpd", "prps"):
        sample = next((f for f in frames if f["t"] == topic and f.get("p")), None)
        if sample is None:
            if topic == "prps" and expect_prps:
                print("[FAIL] 期望 prps 帧但没有收到")
                rc = 1
            else:
                print(f"[skip] 未收到 {topic} 帧（采样窗口内可能确实没有）")
            continue
        arr = unpack_array(sample["p"], sample["m"])
        if arr.size == 0:
            print(f"[FAIL] {topic} 解出空数组")
            rc = 1
        else:
            print(f"[ok] {topic} 解码 shape={arr.shape} dtype={arr.dtype} codec={sample['m'].get('codec', '-')}")

    wire = sum(len(f.get("p") or b"") + len(msgpack.packb({k: v for k, v in f.items() if k != "p"})) for f in frames)
    print(f"[ok] {seconds:.0f}s 内 {len(frames)} 帧 / {wire / 1024:.0f} KB ≈ {wire / seconds / 1024:.0f} KB/s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
