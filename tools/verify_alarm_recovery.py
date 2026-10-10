r"""M7.4 的后半条验收线：**崩溃恢复后无告警丢失**。

为什么恢复时间不够说明问题：`verify_sidecar_recovery.py` 判的是恢复耗时与
`dropped_frames`，而 `dropped_frames` 只统计**有损**通道（`sidecar_server._flush_pending`
里超 4MB 整批丢 pending）。告警是 RELIABLE，走 `_send` 立即发、代码里没有丢弃分支，
所以 dropped=0 对"告警有没有丢"没有任何信息量。这条脚本单独量告警。

什么才算"没丢"：告警一旦发布就必须持久（`alarm_service.trigger_alarm` 是**先落库拿主键、
再 publish**，见 core/services/alarm_service.py:308 的注释），所以判据有两层——
  1) 客户端收到的每条告警都带非空 `alarm_id`（否则前端连"确认"都做不了，等于丢了）；
  2) 用 SIGKILL 打死侧车（不给优雅退出机会，绕开 shutdown 路径的清理），
     换一个新进程打开同一个库，崩溃前那些 id 必须全部查得到。
库是 WAL 模式，崩溃时 -wal 里可能有未 checkpoint 的页，所以这一步是真的在验
"提交过的事务会不会随进程一起没了"，不是验空壳。

用法：
    python tools/verify_alarm_recovery.py
    python tools/verify_alarm_recovery.py --keep   # 保留临时库以便手工核对
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import shutil
import sys
import tempfile

import msgpack

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover
    print("需要 websockets 与 msgpack：venv/ 里缺 msgpack，请用全局 python 跑")
    raise

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.protocol.frames import Frame  # noqa: E402

PORT_RE = re.compile(r"ws://(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)/stream")
TOKEN_RE = re.compile(r"token\s+([A-Za-z0-9_\-\.\+/=]{8,})")


async def read_endpoints(proc: asyncio.subprocess.Process, timeout: float = 90.0) -> tuple[int, str]:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    port = None
    while loop.time() < end:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            continue
        if not line:
            break
        text = line.decode("utf-8", errors="replace")
        m = PORT_RE.search(text)
        if m and port is None:
            port = int(m.group(1))
            continue
        if port is not None:
            t = TOKEN_RE.search(text)
            if t:
                return port, t.group(1)
    raise RuntimeError("侧车未在 90s 内报出端口与 token")


async def call(ws, req_id: str, method: str, params: dict, timeout: float = 20.0):
    await ws.send(msgpack.packb({"k": "call", "i": req_id, "m": method, "p": params}, use_bin_type=True))
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - loop.time()))
        body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        if body.get("k") != "result" or body.get("i") != req_id:
            continue
        if not body.get("ok"):
            raise RuntimeError(f"{method} 失败: {body.get('e')}")
        return body["d"]
    raise TimeoutError(f"{method} 超时")


async def start_sidecar(db_path: pathlib.Path) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "sidecar.py",
        "--sim-type",
        "uhf",
        "--pd-type",
        "corona",
        "--pulse-rate",
        "60000",
        "--fps",
        "20",
        "--db",
        str(db_path),
        "--duration",
        "0",
        "--log-level",
        "ERROR",
        cwd=str(REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    return proc


async def collect_alarms(ws, seconds: float) -> list[dict]:
    """在 /ctrl 上收告警帧，返回 meta.alarm 列表。"""
    out: list[dict] = []
    end = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < end:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        if body.get("k") not in ("event", "tele"):
            continue
        if body.get("t") != "alarm":
            continue
        f = Frame.from_bytes(raw)
        alarm = (f.meta or {}).get("alarm") or {}
        if alarm:
            out.append(alarm)
    return out


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # Windows 控制台默认 GBK，符号字符会在判定行抛 UnicodeEncodeError 并吞掉退出码
        sys.stdout.reconfigure(errors="replace")

    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-alarms", type=float, default=25.0, help="制造并收集告警的秒数")
    ap.add_argument("--keep", action="store_true", help="保留临时库")
    args = ap.parse_args()

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="m74-alarm-"))
    db = tmpdir / "alarm.db"
    print(f"临时库：{db}")

    proc = await start_sidecar(db)
    port, token = await read_endpoints(proc)
    async with ws_connect(f"ws://127.0.0.1:{port}/ctrl?token={token}", max_size=None) as ws:
        await ws.recv()  # hello
        await ws.send(msgpack.packb({"k": "sub", "topics": ["alarm"]}, use_bin_type=True))

        # 把量程压到 1 mV 触发 critical：模拟器幅值 ~36 mV，先要连续 3 次才去抖
        await call(
            ws,
            "1",
            "config.set_amplitude_threshold",
            {"scope": "global", "max_amplitude": 100.0, "critical": 1.0, "high": 2.0, "medium": 3.0, "low": 4.0},
        )
        print("阈值已压到 critical=1 mV，等待告警…")
        pre = await collect_alarms(ws, args.wait_alarms)

    print(f"\n崩溃前收到告警 {len(pre)} 条")
    if not pre:
        print("判定：FAIL —— 一条告警都没触发，后面无从验证")
        await _cleanup(proc, tmpdir, args.keep)
        return 1

    missing_id = [a for a in pre if a.get("alarm_id") is None]
    print(f"其中 alarm_id 为空（未落库就发布）的：{len(missing_id)} 条")
    ids = sorted({int(a["alarm_id"]) for a in pre if a.get("alarm_id") is not None})
    print(f"待核验的主键：{ids[:12]}{' ...' if len(ids) > 12 else ''}")

    # SIGKILL：不给优雅退出机会，才叫崩溃
    proc.kill()
    await proc.wait()
    wal = tmpdir / "alarm.db-wal"
    # 必须在重启**之前**取：新进程打开同一个库会把 WAL 恢复掉，之后再问就永远答"没有"
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    print(f"\n已 SIGKILL 侧车（pid={proc.pid}）；崩溃现场 alarm.db-wal = {wal_bytes} 字节")

    proc2 = await start_sidecar(db)
    try:
        port2, token2 = await read_endpoints(proc2)
        async with ws_connect(f"ws://127.0.0.1:{port2}/ctrl?token={token2}", max_size=None) as ws2:
            await ws2.recv()
            rows = await call(ws2, "2", "storage.query_alarms", {"limit": 500})
    finally:
        await _cleanup(proc2, tmpdir, args.keep)

    after = {int(r["id"]) for r in rows if r.get("id") is not None}
    lost = [i for i in ids if i not in after]

    print(f"重启后新进程查到告警 {len(rows)} 条，其中覆盖崩溃前主键 {len(ids) - len(lost)}/{len(ids)}")

    if missing_id:
        print(f"FAIL：{len(missing_id)} 条告警发布时没有主键，前端无法确认、也无从追溯")
        return 1
    if lost:
        print(f"FAIL：崩溃后丢失 {len(lost)} 条告警，主键 {lost[:10]}")
        return 1

    print(f"\n判定：PASS —— 崩溃前收到的 {len(ids)} 条告警在重启后全部可查，且每条都带主键")
    return 0


async def _cleanup(proc: asyncio.subprocess.Process, tmpdir: pathlib.Path, keep: bool) -> None:
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
    if not keep:
        shutil.rmtree(tmpdir, ignore_errors=True)
    else:
        print(f"（保留临时目录 {tmpdir}）")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
