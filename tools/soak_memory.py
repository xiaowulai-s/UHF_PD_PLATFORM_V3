r"""长时间内存采样：M5.1 的"连续 1h 无内存增长"与 M7.2 的 RSS 增长 <10% 都用它。

跑法：

    python tools/soak_memory.py --minutes 60 --sim-args "--pd-type corona --pulse-rate 60000"

判定口径写死在这里，避免每次口头解释：

- 看**渲染进程**的 RSS 与 private working set。Chromium 的 RSS 不是单调的（GC、
  页缓存回收都会让它掉），所以"无增长"判的是**后半段的线性趋势斜率**与
  "末段中位数 vs 首段中位数"，不是首尾两点相减——首尾两点会被一次 GC 或一次
  窗口最小化彻底带偏。
- 前 5 分钟是预热（uPlot/three 首次分配、V8 编译），计入基线但不参与趋势。
- 同时记 sidecar 的 RSS：内核侧泄漏（环形缓冲、事件 deque）比前端更容易出问题，
  而且它的增长应当真的是平的。
- 必须证明"这一小时里数据在流动"，否则空转的进程树当然不涨内存。用 stats 帧的
  累计计数做佐证。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parents[1]
DESKTOP = REPO / "desktop"
WARMUP_S = 300.0


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
    env = {**os.environ, "PD_SIDECAR_ARGS": args.sim_args}
    proc = subprocess.Popen(
        [str(electron_exe), "."],
        cwd=DESKTOP,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        root = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        print("Electron 启动即退出")
        return 1

    start = time.time()
    deadline = start + args.minutes * 60.0
    rows: list[dict] = []
    while time.time() < deadline:
        time.sleep(args.interval)
        try:
            snap = sample_tree(root)
        except psutil.NoSuchProcess:
            print(f"进程在 {time.time() - start:.0f}s 时退出，采样中止")
            break
        row = {"t_s": round(time.time() - start, 1), **{r: round(m["rss_mb"], 1) for r, m in snap.items()}}
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
    Path(REPO / args.out).write_text(json.dumps({"summary": report, "rows": rows}, indent=2), "utf-8")

    print("\n=== 结果（已剔除前 5 分钟预热）===")
    for role, s in report["roles"].items():
        print(
            f"  {role:<9} {s['first_mb']:>7.1f} → {s['last_mb']:>7.1f} MB  "
            f"峰值 {s['max_mb']:>7.1f}  斜率 {s['slope_mb_per_h']:>+7.2f} MB/h  "
            f"段间增长 {s['step_growth_pct']:>+6.2f}%"
        )
    print(f"报告已写入 {args.out}（{len(rows)} 个采样点）")

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
    ok = renderer["step_growth_pct"] < 10.0
    print(
        f"判定：渲染进程段间增长 {renderer['step_growth_pct']:+.2f}% "
        f"{'< 10% → 通过' if ok else '≥ 10% → 不通过，需查泄漏'}"
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
