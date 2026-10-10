r"""M7.3 多通道线性度与背压丢弃实测（验收线：双/四通道，无背压丢弃）。

为什么要单独测这个：单通道达标不代表 8 通道（生产配置是 4 UHF + 4 AE）也达标，
而"看起来线性"有一个很容易踩的假通过——**多通道其实没建起来**，测的还是一路。
所以脚本先硬性核对"观察到的设备数 == 请求的通道数"，不等就判失败，绝不默默给出
一条漂亮的聚合吞吐。

怎么造多通道：SimulatorSource 的 channel_id 是硬编码 1（core/utils/simulator_source.py:83），
但 source_id = f"sim-{sim_type}-{len(sources)}"，所以每调一次 acquisition.start_simulator
就多一个设备。也就是说这里的"通道"= 一路独立设备，走的正是同一套 per-channel 管线。

背压语义（core/protocol/sidecar_server.py）：
  - lossy 帧进 client.pending，按 (topic, device, channel) 合并；_flush_pending 里若
    ws.bytesToWrite() > BACKPRESSURE_BYTES(4MB) 就整批丢掉并计 dropped_frames；
  - reliable 帧（alarm 等）走 _send 立即发，**代码里没有丢弃分支**，超过 8MB 只告警。
所以"无背压丢弃"判的是 dropped_frames 恒为 0。

客户端**真的解码** payload（unpack_array）。这很重要：只数不解码的话客户端比真实前端
快得多，背压永远堆不起来，测出来的"0 丢弃"是自欺。

用法：
    python tools/measure_channel_linearity.py                    # 1/2/4 通道，各测 20s
    python tools/measure_channel_linearity.py --channels 1,2,4,8 --seconds 30
    python tools/measure_channel_linearity.py --pulse-rate 60000 --fps 20
"""

from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import msgpack
import psutil

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - 环境缺库时给出明确指引而不是 ImportError 裸栈
    print("需要 websockets 与 msgpack：注意 venv/ 里缺 msgpack，请用全局 python 跑")
    raise

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.protocol.frames import Frame, unpack_array  # noqa: E402

# 与 desktop/src/main/sidecar.ts 同口径：先见到 stream URL 拿端口，再单独一行拿 token
PORT_RE = re.compile(r"ws://(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)/stream")
TOKEN_RE = re.compile(r"token\s+([A-Za-z0-9_\-\.\+/=]{8,})")


async def read_endpoints(proc: asyncio.subprocess.Process, timeout: float = 60.0) -> tuple[int, str]:
    """解析 stdout 拿 (port, token)。超时未就绪就直接失败——后面全是空转。"""
    assert proc.stdout is not None
    port = None
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
        except asyncio.TimeoutError:
            continue
        if not line_bytes:
            raise RuntimeError("内核在打印端点前就退出了 rc=%s" % proc.returncode)
        line = line_bytes.decode("utf-8", "replace")
        m = PORT_RE.search(line)
        if m and port is None:
            port = int(m.group(1))
            continue
        m = TOKEN_RE.search(line)
        if m and port is not None:
            return port, m.group(1)
    raise RuntimeError("等 sidecar ready 端点超时 %ss" % timeout)


async def rpc(ws, req_id: str, method: str, params: dict, timeout: float = 15.0):
    await ws.send(msgpack.packb({"k": "call", "i": req_id, "m": method, "p": params}, use_bin_type=True))
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - asyncio.get_running_loop().time()))
        body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        if body.get("k") == "result" and body.get("i") == req_id:
            if not body.get("ok"):
                raise RuntimeError("RPC %s 失败: %s" % (method, body.get("e")))
            return body.get("d")
        # 其他帧忽略：/ctrl 上理论上只有结果，但 hello/事件混进来时不该把测量打断


async def pump(ws, stop: asyncio.Event, topic_counts: Counter, device_counts: Counter, decode: bool):
    try:
        while not stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
            if body.get("k") == "hello":
                await ws.send(
                    msgpack.packb({"k": "sub", "topics": sorted(body.get("topics") or {})}, use_bin_type=True)
                )
                continue
            if "t" not in body:
                continue
            frame = Frame.from_bytes(raw)
            topic_counts[frame.topic] += 1
            device_counts[frame.device] += 1
            if decode and frame.payload is not None:
                # 真实前端要解压 + 反量化；不做就等于给客户端开了外挂
                unpack_array(frame.payload, frame.meta)
    except Exception as exc:  # noqa: BLE001 - 探针尾线程，异常记录下来就行
        errors.append("%s: %s" % (type(exc).__name__, exc))


errors: list[str] = []


async def run_config(n_channels: int, args) -> dict:
    env_cmd = [
        sys.executable,
        "sidecar.py",
        "--no-db",
        "--pd-type",
        args.pd_type,
        "--fps",
        str(args.fps),
        "--pulse-rate",
        str(args.pulse_rate),
    ]
    proc = await asyncio.create_subprocess_exec(
        *env_cmd, cwd=str(REPO), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        port, token = await read_endpoints(proc)
        url = "ws://127.0.0.1:%d" % port

        async with ws_connect(url + "/ctrl?token=" + token, max_size=None) as ctrl:
            # 第一路由 sidecar 自己起，剩下 n-1 路用 RPC 扩
            for _ in range(n_channels - 1):
                await rpc(
                    ctrl,
                    "s%d" % time.monotonic_ns(),
                    "acquisition.start_simulator",
                    {"sim_type": "uhf", "pd_type": args.pd_type, "fps": args.fps, "pulse_rate": float(args.pulse_rate)},
                )

            if args.alarm_floor:
                # 默认阈值下 check_amplitude 在 "level 为 None" 处就返回了，量不到告警判定的
                # 成本；压到 1 mV 让每条检出事件都越限、都进锁、都被去抖兜住
                await rpc(
                    ctrl,
                    "f",
                    "config.set_amplitude_threshold",
                    {
                        "scope": "global",
                        "max_amplitude": 100.0,
                        "critical": 1.0,
                        "high": 1.0,
                        "medium": 1.0,
                        "low": 1.0,
                    },
                )

            async with ws_connect(url + "/stream?token=" + token, max_size=None) as stream:
                topic_counts: Counter = Counter()
                device_counts: Counter = Counter()
                stop = asyncio.Event()
                reader = asyncio.create_task(pump(stream, stop, topic_counts, device_counts, not args.no_decode))

                await asyncio.sleep(args.settle)  # 让启动期与缓冲区合并稳定下来
                before_t: Counter = Counter(topic_counts)
                stats_a = await rpc(ctrl, "a", "acquisition.stats", {})
                diag_a = await rpc(ctrl, "b", "system.diagnostics", {})
                t0 = time.monotonic()

                rss_samples = []
                ps = psutil.Process(proc.pid)
                while time.monotonic() - t0 < args.seconds:
                    await asyncio.sleep(2.0)
                    rss_samples.append(ps.memory_info().rss / 1048576)

                elapsed = time.monotonic() - t0
                stats_b = await rpc(ctrl, "c", "acquisition.stats", {})
                diag_b = await rpc(ctrl, "d", "system.diagnostics", {})
                after_t: Counter = Counter(topic_counts)

                stop.set()
                await asyncio.wait_for(reader, timeout=5.0)

        return summarize(
            n_channels,
            elapsed,
            before_t,
            after_t,
            device_counts,
            stats_a,
            stats_b,
            diag_a,
            diag_b,
            rss_samples,
            ps,
            args,
        )
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


def summarize(n_req, elapsed, before, after, devices, sa, sb, da, db, rss, ps, args) -> dict:
    gained = {t: after[t] - before[t] for t in set(after) | set(before)}
    wf = gained.get("waveform", 0)
    # 只数真带设备号的帧：stats/log 这类聚合帧 device 是空串，算进去会把"1 通道"
    # 数成 2 通道，于是每通道速率被凭空除以 2——第一版就栽在这里。
    observed = {d for d, c in devices.items() if c > 0 and d}
    frames_recv = (sb.get("frames_received", 0) or 0) - (sa.get("frames_received", 0) or 0)
    wave_done = (sb.get("waveforms_processed", 0) or 0) - (sa.get("waveforms_processed", 0) or 0)
    return {
        "请求通道": n_req,
        "观察到的设备数": len(observed),
        "设备": sorted(observed),
        "客户端 fps/通道": wf / elapsed / max(1, len(observed)),
        "聚合 fps": wf / elapsed,
        "内核收到 fps": frames_recv / elapsed,
        "内核处理 fps": wave_done / elapsed,
        "sent": db.get("sent_frames", 0) - da.get("sent_frames", 0),
        "coalesced": db.get("coalesced_frames", 0) - da.get("coalesced_frames", 0),
        "throttled": db.get("throttled_frames", 0) - da.get("throttled_frames", 0),
        "dropped": db.get("dropped_frames", 0) - da.get("dropped_frames", 0),
        "RSS 末 MB": rss[-1] if rss else 0.0,
        "RSS 峰 MB": max(rss) if rss else 0.0,
        "帧数": gained,
        "客户端异常": list(errors),
    }


def verdict(rows: list[dict], args) -> bool:
    ok = True
    base = next((r for r in rows if r["请求通道"] == 1), None)
    for r in rows:
        prob = []
        if r["观察到的设备数"] != r["请求通道"]:
            prob.append(
                "只观察到 %d 路设备（请求 %d）→ 多通道没建起来，线性结论无效" % (r["观察到的设备数"], r["请求通道"])
            )
        if r["dropped"] != 0:
            prob.append("背压丢弃 %d 帧（要求为 0）" % r["dropped"])
        if r["客户端异常"]:
            prob.append("客户端解码异常: %s" % r["客户端异常"][:2])
        ratio = (r["内核处理 fps"] / base["内核处理 fps"]) if base and base["内核处理 fps"] else 0.0
        ideal = r["请求通道"]
        r["处理fps/N"] = r["内核处理 fps"] / ideal
        r["扩展比"] = ratio / ideal if ideal else 0.0
        if base and r["请求通道"] > 1 and ratio < args.min_scale * ideal:
            prob.append(
                "聚合处理 fps 只到单通道的 %.2f×（理想 %d×，阈值 %.2f×）" % (ratio, ideal, args.min_scale * ideal)
            )
        print(
            "\n  N=%d  设备=%d  内核处理 %.1f fps/通道（聚合 %.1f，扩展比 %.2f/通道）"
            "  客户端 %.1f/通道  sent=%d coalesced=%d throttled=%d dropped=%d  RSS %.0f/%.0f MB"
            % (
                r["请求通道"],
                r["观察到的设备数"],
                r["内核处理 fps"] / max(1, r["观察到的设备数"]),
                r["内核处理 fps"],
                r["扩展比"],
                r["客户端 fps/通道"],
                r["sent"],
                r["coalesced"],
                r["throttled"],
                r["dropped"],
                r["RSS 末 MB"],
                r["RSS 峰 MB"],
            )
        )
        if prob:
            ok = False
            for p in prob:
                print("     !! %s" % p)
    return ok


async def main() -> int:
    # Windows 控制台默认 GBK，判定行里的任何非 GBK 字符都会抛 UnicodeEncodeError，
    # 把已经跑完的实测结果连同退出码一起吞掉（真踩过：数据全对，最后一行崩）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", default="1,2,4", help="逗号分隔的通道数")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--settle", type=float, default=6.0)
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--pulse-rate", type=float, default=60000.0)
    ap.add_argument("--pd-type", default="corona")
    ap.add_argument("--no-decode", action="store_true", help="客户端不解码（会让背压假性为 0）")
    ap.add_argument(
        "--alarm-floor",
        action="store_true",
        help="把四级阈值全压到 1 mV：每条检出事件都越限，用来量告警判定的最坏成本",
    )
    ap.add_argument(
        "--min-scale", type=float, default=0.8, help="每通道处理 fps 相对单通道的最低保持率（0.8=允许 20%% 退化）"
    )
    args = ap.parse_args()

    if args.no_decode:
        print("[警告] --no-decode：客户端不解码，dropped 会被系统性低估")
    ns = [int(x) for x in args.channels.split(",") if x.strip()]
    rows = []
    for n in ns:
        print(
            "\n=== N=%d 通道，测 %.0fs（另有 %.0fs 预热不计入）%s ==="
            % (n, args.seconds, args.settle, "，阈值压到 1 mV" if args.alarm_floor else "")
        )
        rows.append(await run_config(n, args))

    print("\n判定（线：无背压丢弃 + 每通道处理速率保持 ≥%.0f%%）：" % (args.min_scale * 100))
    ok = verdict(rows, args)
    per = [r.get("处理fps/N", 0.0) for r in rows if r.get("处理fps/N")]
    if len(per) > 1:
        print(
            "   每通道处理 fps：min=%.1f max=%.1f 极差=%.1f%%（median %.1f）"
            % (
                min(per),
                max(per),
                (max(per) - min(per)) / max(1e-9, statistics.median(per)) * 100,
                statistics.median(per),
            )
        )
    print("\nM7.3 %s" % ("PASS 通过" if ok else "FAIL 不通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
