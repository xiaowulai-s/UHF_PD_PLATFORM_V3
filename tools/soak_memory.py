r"""长时间内存采样：M5.1 的"连续 1h 无内存增长"与 M7.2 的 RSS 增长 <10% 都用它。

跑法：

    python tools/soak_memory.py --minutes 60 --sim-args "--pd-type corona --pulse-rate 60000"

本机（开发机）必须再加 `--electron-args "--disable-gpu-sandbox --no-sandbox"`：不传时
Electron 的 GPU 进程与渲染进程沙箱都起不来，30 秒内就 `FATAL: GPU process isn't usable`
（或渲染进程 `crashed exit=-2147483645`）并退出，一个采样点都留不下。详见该参数的 help。

另外：**同一时间只跑一个 Electron 实例**。两个实例共用同一个 userData 目录时，
后起的那个会刷 `Unable to move the cache: 拒绝访问 (0x5)` 并把渲染进程带崩——
想并行做截图验收就先把长稳停掉。

判定口径写死在这里，避免每次口头解释：

- 看**渲染进程**的 RSS 与 private working set。Chromium 的 RSS 不是单调的（GC、
  页缓存回收都会让它掉），所以"无增长"判的是**后半段的线性趋势斜率**与
  "末段中位数 vs 首段中位数"，不是首尾两点相减——首尾两点会被一次 GC 或一次
  窗口最小化彻底带偏。
- 前 5 分钟是预热（uPlot/three 首次分配、V8 编译），计入基线但不参与趋势。
- 同时记 sidecar 的 RSS：内核侧泄漏（环形缓冲、事件 deque）比前端更容易出问题，
  而且它的增长应当真的是平的。
- 必须证明"这一小时里数据在流动"，否则空转的进程树当然不涨内存。工具会给侧车自动
  追加 `--bench`，再从**内核自己的日志文件** `logs/sidecar.log` 里把侧车每 5 秒一行的
  累计吞吐（采集帧数/事件数/推流帧数）取出来，逐点记进报告，并做首末之差。取不到就是
  本次无效——不允许"内存很平但没有任何流动证据"被判成通过。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parents[1]
DESKTOP = REPO / "desktop"
WARMUP_S = 300.0

# 侧车 `--bench` 每 5 秒用 logger.info 打一行吞吐统计。这行**不在** electron 的启动日志里：
# `core/utils/logger.py` 的 StreamHandler 挂在 **stdout** 上（不是 sidecar.ts 注释里写的
# stderr），而主进程把 stdout 只用于正则解析端口/token，其余行全部丢弃。所以数据源是
# 内核自己写的日志文件 `logs/sidecar.log`（sidecar.py:124 用 state_path("logs", ...) 锚定，
# 开发态即仓库根，冻结态在 %LOCALAPPDATA%\UHF-PD 下）。
#
# 字段之间用 \s+ / \s*\|\s* 而不是单个空格：日志行前有 "时间 - sidecar - INFO - [文件:行] - "
# 前缀，且文件写入可能按块落盘，锚死单空格会漏。
BENCH_RE = re.compile(
    r"采集\s+frames=(\d+)\s+waveforms=(\d+)\s+events=(\d+)\s*\|\s*" r"推流\s+sent=(\d+)\s+dropped=(\d+)\s+clients=(\d+)"
)


def sidecar_log_path() -> Path:
    """内核日志文件的位置。

    优先问内核自己的 `runtime_paths.state_path`——**不要另写一套路径推导**，否则两边
    一漂移，工具就会安静地读到空文件，把"没数据"误报成"没流动"。问不到再退回仓库根。
    """
    try:
        sys.path.insert(0, str(REPO))
        from core.foundation.runtime_paths import state_path

        return Path(state_path("logs", "sidecar.log"))
    except (ImportError, AttributeError, TypeError, OSError):
        return REPO / "logs" / "sidecar.log"


def latest_bench(log_path: Path, offset: int = 0) -> dict[str, int] | None:
    """取 --bench 最后一条统计；取不到返回 None（不猜、也不填 0）。

    offset 是本次运行开始时的文件大小。sidecar.log 是**追加**写的，不掐掉上一轮的
    历史，第一次采样就会把上一次运行的数字当成自己的基线。
    """
    try:
        with log_path.open("rb") as fh:
            if offset > 0:
                fh.seek(offset)
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    matches = list(BENCH_RE.finditer(text))
    if not matches:
        return None
    frames, waveforms, events, sent, dropped, clients = (int(g) for g in matches[-1].groups())
    return {
        "frames": frames,
        "waveforms": waveforms,
        "events": events,
        "sent": sent,
        "dropped": dropped,
        "clients": clients,
    }


def sample_tree(root: psutil.Process) -> dict[str, dict[str, float]]:
    """按角色归组子进程内存。角色从命令行参数认，比按 PID 猜可靠。"""
    out: dict[str, dict[str, float]] = {}
    procs = [root] + root.children(recursive=True)
    for p in procs:
        try:
            mem = p.memory_info()
            cmd = " ".join(p.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        mb = mem.rss / 1048576.0
        if "--type=renderer" in cmd:
            role = "renderer"
        elif "--type=gpu-process" in cmd:
            role = "gpu"
        elif "sidecar.py" in cmd or "uhf-sidecar" in cmd:
            role = "sidecar"
        elif "--type=" in cmd:
            continue  # utility/crashpad 之类，噪声大且与泄漏无关
        else:
            role = "main"
        cur = out.setdefault(role, {"rss_mb": 0.0, "private_mb": 0.0})
        cur["rss_mb"] += mb
        try:
            uss = p.memory_full_info()
            cur["private_mb"] += getattr(uss, "private_working_set", 0) / 1048576.0
        except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
            pass
    return out


def trend(series: list[float], interval_s: float) -> dict[str, float]:
    """最小二乘斜率（MB/小时）+ 首末段中位数差。

    斜率的自变量是**采样序号**，所以换成 MB/小时必须除以真实采样间隔；
    早先这里把间隔当成 1 秒，--interval 一改结论就差一个倍数。
    """
    if len(series) < 4:
        return {"slope_mb_per_h": 0.0, "step_growth_pct": 0.0}
    n = len(series)
    xs = list(range(n))
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(series)
    denom = sum((x - mean_x) ** 2 for x in xs) or 1.0
    slope_per_sample = sum((xs[i] - mean_x) * (series[i] - mean_y) for i in range(n)) / denom
    cut = max(1, n // 5)
    first = statistics.median(series[:cut])
    last = statistics.median(series[-cut:])
    return {
        "slope_mb_per_h": slope_per_sample * (3600.0 / interval_s),
        "step_growth_pct": ((last - first) / first * 100.0) if first else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=30.0, help="采样间隔秒")
    ap.add_argument("--sim-args", default="--pd-type corona --pulse-rate 60000")
    ap.add_argument("--out", default="soak-report.json")
    ap.add_argument(
        "--electron-args",
        default="",
        help=(
            "追加给 electron.exe 的参数（空格分隔）。值以 -- 开头时必须用 --electron-args=... "
            "的等号形式，否则 argparse 会把它当成选项并报 'expected one argument'。"
            "本机两个沙箱都起不来，必须传 '--disable-gpu-sandbox --no-sandbox'："
            "只传前者时 GPU 进程活了，但渲染进程仍以 exit=-2147483645 崩溃，"
            "报告里会整档缺 renderer 角色而被判无效。"
            "--no-sandbox 只用于本地验收，不要带进装机版（安全基线见 AGENTS.md）。"
        ),
    )
    args = ap.parse_args()

    # 不能 spawn "npx"：Windows 上它是 npx.cmd，CreateProcess 找不到就直接
    # FileNotFoundError。这里用 electron 包里的真实 exe，等价于 `npx electron .`。
    electron_exe = DESKTOP / "node_modules" / "electron" / "dist" / "electron.exe"
    if not electron_exe.exists():
        print(f"未找到 {electron_exe}；先在 desktop/ 里跑 npm ci")
        return 1
    print(f"启动 Electron（dev 模式，会自动 spawn sidecar）… 采样 {args.minutes:.0f} 分钟")
    # 仿真参数走 PD_SIDECAR_ARGS：dev 下是 Electron 主进程读这个环境变量再拼给
    # sidecar 的，把它们当 electron 的 argv 传进去会被整个丢掉（不报错，只是不生效）。
    # --bench 不是可选装饰，它是"数据在流动"的唯一证据来源（见文件头），所以这里补上。
    sim_args = args.sim_args if "--bench" in args.sim_args else f"{args.sim_args} --bench"
    env = {**os.environ, "PD_SIDECAR_ARGS": sim_args}
    extra = shlex.split(args.electron_args)
    # 内核日志是**追加**写的：先记下启动前的大小，只认本次运行新落盘的那一段，
    # 否则第一次采样就会把上一轮的数字当成自己的基线。
    slog = sidecar_log_path()
    slog_offset = slog.stat().st_size if slog.exists() else 0
    # electron 的 stdout/stderr 绝不能丢进 DEVNULL：内核起不来、GPU 进程崩掉时，
    # 唯一线索就在这两路输出里。曾经因为丢弃输出，把一个"30 秒即退出"的失败
    # 误读成"跑得太短、预热吃掉了样本"，白跑一轮。
    elog = REPO / "soak-electron.log"
    with elog.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            [str(electron_exe), ".", *extra],
            cwd=DESKTOP,
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
    print(f"electron 参数: {extra or '（无）'}；启动日志写入 {elog}")
    print(f"帧计数取自内核日志: {slog}（从偏移 {slog_offset} 起）")
    try:
        root = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        print("Electron 启动即退出")
        return 1

    start = time.time()
    deadline = start + args.minutes * 60.0
    rows: list[dict] = []
    last_bench: dict[str, int] | None = None
    while time.time() < deadline:
        time.sleep(args.interval)
        try:
            snap = sample_tree(root)
        except psutil.NoSuchProcess:
            print(f"进程在 {time.time() - start:.0f}s 时退出，采样中止")
            break
        bench = latest_bench(slog, slog_offset)
        if bench:
            last_bench = bench
        row = {"t_s": round(time.time() - start, 1), **{r: round(m["rss_mb"], 1) for r, m in snap.items()}}
        if bench:
            # 帧计数与内存同列一行，才能看出"内存平"是不是发生在有数据流动的时候
            row.update({"frames": bench["frames"], "events": bench["events"], "sent": bench["sent"]})
        rows.append(row)
        # 每小时只写一次报告的话，最后一步出异常就把一小时的采样全赔进去。
        # 这里每个点都覆写，代价是几十 KB 的顺序写，可以忽略。
        Path(REPO / args.out).write_text(json.dumps({"partial": True, "rows": rows}, indent=2), "utf-8")
        if len(rows) % 10 == 0:
            print(f"  [{row['t_s'] / 60:5.1f} min] " + "  ".join(f"{k}={v}" for k, v in row.items() if k != "t_s"))

    # 只收尾自己起的进程树，不碰别的 electron 实例
    try:
        for child in root.children(recursive=True):
            child.terminate()
        root.terminate()
        _, alive = psutil.wait_procs([root] + root.children(recursive=True), timeout=10)
        for p in alive:
            p.kill()
    except psutil.NoSuchProcess:
        pass

    post = [r for r in rows if r["t_s"] > WARMUP_S]
    report = {"minutes": round((rows[-1]["t_s"] if rows else 0) / 60.0, 2), "samples": len(rows), "roles": {}}
    # 直接从 rows 派生序列：按角色各自累积的话，某角色晚出现就会与 rows 错位，
    # 后面 zip 出来的 (值, 时刻) 对不上，剔除预热的判断就全错了。
    for role in ("main", "renderer", "gpu", "sidecar"):
        vals = [r[role] for r in post if role in r]
        if not vals:
            continue
        report["roles"][role] = {
            "first_mb": round(vals[0], 1),
            "last_mb": round(vals[-1], 1),
            "max_mb": round(max(vals), 1),
            **{k: round(v, 3) for k, v in trend(vals, args.interval).items()},
        }
    # 数据流动证据：--bench 累计计数的首末之差。没有它，"内存不涨"可能只是"什么都没干"。
    flow_rows = [r for r in rows if "frames" in r]
    if flow_rows:
        first, last = flow_rows[0], flow_rows[-1]
        report["flow"] = {
            "source": f"--bench（内核 logger 每 5 秒一行，落在 {slog}）",
            "first": {k: first[k] for k in ("frames", "events", "sent")},
            "last": {k: last[k] for k in ("frames", "events", "sent")},
            "delta": {k: last[k] - first[k] for k in ("frames", "events", "sent")},
            "span_s": round(last["t_s"] - first["t_s"], 1),
            "clients": (last_bench or {}).get("clients"),
            "dropped": (last_bench or {}).get("dropped"),
        }
    Path(REPO / args.out).write_text(json.dumps({"summary": report, "rows": rows}, indent=2), "utf-8")

    print("\n=== 结果（已剔除前 5 分钟预热）===")
    for role, s in report["roles"].items():
        print(
            f"  {role:<9} {s['first_mb']:>7.1f} → {s['last_mb']:>7.1f} MB  "
            f"峰值 {s['max_mb']:>7.1f}  斜率 {s['slope_mb_per_h']:>+7.2f} MB/h  "
            f"段间增长 {s['step_growth_pct']:>+6.2f}%"
        )
    print(f"报告已写入 {args.out}（{len(rows)} 个采样点）")

    flow = report.get("flow")
    print("\n=== 数据流动证据（--bench）===")
    if flow:
        delta = flow["delta"]
        print(
            f"  {flow['span_s'] / 60:.1f} 分钟内：采集 +{delta['frames']} 帧 · 事件 +{delta['events']} 次 · "
            f"推流 +{delta['sent']} 帧 · 客户端 {flow['clients']} · 背压丢弃 {flow['dropped']}"
        )
    else:
        print(f"  !! 没取到 --bench 统计（侧车没起来，或 {slog} 里没有新行）")

    renderer = report["roles"].get("renderer")
    if not post:
        # 别把"跑得太短"报成"没采到渲染进程"：两者的处置方向完全不同，
        # 前者只要加长时间，后者要去查进程识别逻辑。
        print(
            f"!! 预热窗口 {WARMUP_S / 60:.0f} 分钟吃掉了全部 {len(rows)} 个采样点，"
            f"无法判定趋势；请把 --minutes 提到远大于 {WARMUP_S / 60:.0f}"
        )
        return 1
    if renderer is None:
        print("!! 没采到渲染进程（进程识别或启动失败），本次无效")
        return 1
    if flow is None:
        # 没有流动证据时，"内存很平"与"进程空转"无法区分，这种通过没有意义
        print("!! 没有数据流动证据，本次无效——内存平也可能是空转，请查 --bench 是否生效")
        return 1
    ok = renderer["step_growth_pct"] < 10.0
    print(
        f"判定：渲染进程段间增长 {renderer['step_growth_pct']:+.2f}% "
        f"{'< 10% → 通过' if ok else '≥ 10% → 不通过，需查泄漏'}"
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
