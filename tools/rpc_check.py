import asyncio
import sys

import msgpack
from websockets.asyncio.client import connect

# (标签, 方法名, 参数)
CALLS = [
    ("device.list", "device.list", {}),
    ("device.get", "device.get", {"device_id": "DEV-001"}),
    ("query_alarms", "storage.query_alarms", {"device_id": "@dev", "limit": 3}),
    (
        "query_trends",
        "storage.query_trends",
        {"device_id": "@dev", "period": "1h", "start_time": "2026-06-01T00:00:00Z", "limit": 5},
    ),
    (
        "events/wide",
        "storage.query_events",
        {"device_id": "@dev", "start_time": "2026-06-01T00:00:00Z", "limit": 500},
    ),
    # 窄窗口 + 未来窗口：验证时间过滤真的能区分，而不是恒返回全表或恒空
    (
        "events/narrow",
        "storage.query_events",
        {"device_id": "@dev", "start_time": "2026-07-08T01:55:00Z", "limit": 500},
    ),
    (
        "events/future",
        "storage.query_events",
        {"device_id": "@dev", "start_time": "2027-01-01T00:00:00Z", "limit": 500},
    ),
    (
        "events/other-dev",
        "storage.query_events",
        {"device_id": "NOSUCH", "start_time": "2026-01-01T00:00:00Z", "limit": 500},
    ),
    ("storage.stats", "storage.stats", {}),
    # 历史页的设备候选必须来自库里真实出现过的 device_id，见下方 resolve_device
    ("device_ids", "storage.device_ids", {}),
    ("trend/1h", "storage.query_trend_series", {"device_id": "@dev", "window": "1h", "bins": 60}),
    ("trend/24h", "storage.query_trend_series", {"device_id": "@dev", "window": "24h", "bins": 96}),
    ("trend/30d", "storage.query_trend_series", {"device_id": "@dev", "window": "30d", "bins": 90}),
    # 不存在的设备：应返回等长的全 0 序列，而不是报错或空数组
    ("trend/nodata", "storage.query_trend_series", {"device_id": "NOSUCH", "window": "1h", "bins": 60}),
    # 非法 window 必须被拒绝：这条是"期望失败"用例，见下方 expect_err
    ("trend/badwin", "storage.query_trend_series", {"device_id": "@dev", "window": "5m"}, True),
]

#: params 里的 "@dev" 在发批次前替换成库里真实出现过的设备号。
DEV_SENTINEL = "@dev"
#: 库里一条事件都没有时用的占位——此时只验证往返与空结果，不验证读数。
DEV_FALLBACK = "sim-uhf-0"


async def resolve_device(ws) -> str:
    """从 storage.device_ids 里挑条数最多的设备号，替代写死的样例设备号。

    模拟器事件的设备 id 命名在 2026-10-09 改过（PDSimulator 的类默认 SIM-001 →
    数据源 id sim-uhf-N），既有库里两种都可能有；写死任一半都会让另一半恒返回 0 行，
    看起来像查询 RPC 坏了。
    """
    await ws.send(msgpack.packb({"k": "call", "i": "devprobe", "m": "storage.device_ids", "p": {}}, use_bin_type=True))
    while True:
        body = msgpack.unpackb(await ws.recv(), raw=False, strict_map_key=False)
        if body.get("k") != "result" or body.get("i") != "devprobe":
            continue
        if not body.get("ok"):
            print(f"storage.device_ids 失败（{body['e'][:80]}），样例设备号退回 {DEV_FALLBACK}")
            return DEV_FALLBACK
        rows = body["d"] or []
        if not rows:
            print(f"库里还没有事件，设备号退回占位 {DEV_FALLBACK}（只验往返，不验读数）")
            return DEV_FALLBACK
        print("库中真实设备:", ", ".join(f"{r['device_id']}({r['events']} 条)" for r in rows))
        return max(rows, key=lambda r: int(r.get("events", 0)))["device_id"]


async def watch_stream(port: int, token: str, seconds: float) -> dict:
    """订阅 /stream，统计这段时间内真实到达的 topic 与 stats 内容。"""
    seen: dict[str, int] = {}
    last_stats = None
    url = f"ws://127.0.0.1:{port}/stream?token={token}"
    async with connect(url, max_size=None) as ws:
        await ws.recv()  # hello
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            remain = deadline - asyncio.get_running_loop().time()
            if remain <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remain)
            except (TimeoutError, asyncio.TimeoutError):
                break
            body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
            if body.get("k") not in ("tele", "event"):
                continue
            topic = body.get("t", "?")
            seen[topic] = seen.get(topic, 0) + 1
            if topic == "stats":
                last_stats = (body.get("m") or {}).get("stats")
    return {"topics": seen, "stats": last_stats}


async def run(port: int, token: str) -> int:
    results = {}
    async with connect(f"ws://127.0.0.1:{port}/ctrl?token={token}", max_size=None) as ws:
        hello = msgpack.unpackb(await ws.recv(), raw=False, strict_map_key=False)
        print("服务端暴露的 RPC:", ", ".join(hello["rpc"]))
        print()

        dev = await resolve_device(ws)
        print("样例查询使用的设备号:", dev)
        print()

        for i, entry in enumerate(CALLS):
            method = entry[1]
            params = {k: (dev if v == DEV_SENTINEL else v) for k, v in entry[2].items()}
            await ws.send(msgpack.packb({"k": "call", "i": str(i), "m": method, "p": params}, use_bin_type=True))

        while len(results) < len(CALLS):
            body = msgpack.unpackb(await ws.recv(), raw=False, strict_map_key=False)
            if body.get("k") == "result":
                results[int(body["i"])] = body

    bad = 0
    for i in sorted(results):
        r = results[i]
        entry = CALLS[i]
        label, expect_err = entry[0], bool(entry[3]) if len(entry) > 3 else False
        if not r["ok"]:
            if expect_err:
                print(f"  OK*   {label:<18} 按预期被拒绝: {str(r['e'])[:80]}")
                continue
            bad += 1
            print(f"  FAIL  {label:<18} {r['e'][:110]}")
            continue
        if expect_err:
            bad += 1
            print(f"  FAIL  {label:<18} 本应被拒绝，却成功返回")
            continue
        d = r["d"]
        if label.startswith("trend/") and isinstance(d, list):
            total = sum(int(row.get("count", 0)) for row in d)
            filled = sum(1 for row in d if row.get("count"))
            peak = max((float(row.get("max_amplitude", 0)) for row in d), default=0.0)
            print(
                f"  OK    {label:<18} {len(d):>4} 箱  事件={total:<5} 非空箱={filled:<4} "
                f"峰值={peak:.1f} mV  箱宽={d[0]['bin_seconds'] if d else '-'}s"
            )
            continue
        if isinstance(d, list):
            preview = d[0] if d else "-"
            print(f"  OK    {label:<18} {len(d):>4} 行  {str(preview)[:96]}")
        elif isinstance(d, dict):
            print(f"  OK    {label:<18} {str(d)[:120]}")
        else:
            print(f"  OK    {label:<18} {d}")

    print("\n--- /stream 收流 5 秒 ---")
    stream = await watch_stream(port, token, 5.0)
    for topic, count in sorted(stream["topics"].items()):
        print(f"  {topic:<14} {count} 帧")
    stats = stream["stats"]
    if stats is None:
        print("  !! 未收到 stats 帧")
        bad += 1
    else:
        for key in ("waveforms_processed", "observed_max_amplitude", "source_count", "online_count"):
            print(f"  stats.{key:<24} = {stats.get(key)}")
        transport = stats.get("transport") or {}
        print(f"  stats.transport.uptime_s       = {transport.get('uptime_s')}")
        print(f"  stats.transport.sent_frames    = {transport.get('sent_frames')}")
    return bad


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(int(sys.argv[1]), sys.argv[2])))
