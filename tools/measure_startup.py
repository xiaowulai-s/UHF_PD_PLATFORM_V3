r"""M7.5 启动时间测量：从进程 spawn 到 stdout 出现 `sidecar ready` 的耗时。

验收线是冷启动 < 3s（docs/ELECTRON_REWRITE_PLAN.md 的 M7 表）。

为什么量"到 ready"而不是"到进程存活"：desktop/src/main/sidecar.ts 必须 scrape 到
stream URL 与 token 才能连上内核，所以对用户而言"可交互"的时刻就是这一行。

冷启动在这台机器上没法可靠强制（清空 Windows 备用缓存需要 Sysinternals 的
EmptyStandbyList 并带调试权限），所以这里分开报两组数并如实标注：
  - warm：连跑 N 次，文件缓存必然已被前几次热好；
  - first：先把系统工作集压一压（跑一个大文件读）再测，作为"较冷"的近似下界。
两组都给出 min/median/max，避免用单次数字下结论——本项目吃过只看一个数的亏。

用法：
    python tools/measure_startup.py                     # 冻结产物，warm ×7
    python tools/measure_startup.py --runs 12
    python tools/measure_startup.py --dev               # 对比 python sidecar.py
"""

from __future__ import annotations

import argparse
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE = REPO / "dist" / "uhf-sidecar" / "uhf-sidecar.exe"

# 对齐 desktop/src/main/sidecar.ts 的实际契约：先见 port 行，再见独立 token 行
READY_RE = re.compile(r"sidecar\s+ready", re.I)
URL_RE = re.compile(r"ws://(127\.0\.0\.1|\[::1\]|localhost):(\d+)/stream")
TOKEN_RE = re.compile(r"^\s*(?:token|auth token)\s+([A-Za-z0-9_\-\.\+/=]{8,})\s*$", re.M)

# 预热用的大文件（读它会把备用缓存挤掉，近似"变冷"）
EVICT_TARGET_BYTES = 300 * 1024 * 1024


def run_once(cmd: list[str], timeout: float, env: dict | None = None) -> tuple[float, str] | tuple[None, str]:
    """返回 (耗时秒, 错误说明)。耗时 = spawn → 解析出 ready+url+token。"""
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )
    url = token = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if URL_RE.search(line):
                url = line.strip()
            m = TOKEN_RE.search(line)
            if m:
                token = m.group(1)
            if url and token:
                return time.perf_counter() - t0, ""
            if READY_RE.search(line) and url is None:
                # ready 行在 URL 之前出现，继续等
                pass
            if time.perf_counter() - t0 > timeout:
                return None, "超时 %ss 未就绪" % timeout
        rc = proc.wait(timeout=10)
        return None, "进程已退出 rc=%s（未打印端点）" % rc
    except OSError as e:
        return None, "spawn 失败: %s" % e
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def evict_cache() -> None:
    """尽力把文件缓存挤冷：顺序读几个大 DLL/pickle，纯 I/O 不占 CPU。"""
    cands = sorted(
        (REPO / "dist" / "uhf-sidecar" / "_internal").rglob("*.pyd"),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    read = 0
    for p in cands:
        with open(p, "rb") as f:
            while read < EVICT_TARGET_BYTES:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                read += len(chunk)
        if read >= EVICT_TARGET_BYTES:
            break


def measure(cmd: list[str], runs: int, label: str, cool: bool, env: dict | None = None) -> list[float]:
    out = []
    for i in range(runs):
        if cool:
            evict_cache()
            time.sleep(0.2)
        dt, err = run_once(cmd, timeout=90.0, env=env)
        if dt is None:
            print("  [%s %d/%d] 失败: %s" % (label, i + 1, runs, err))
            continue
        out.append(dt)
        print("  [%s %2d/%d] %6.2f s" % (label, i + 1, runs, dt), flush=True)
    return out


def report(name: str, vals: list[float], line: float) -> None:
    if not vals:
        print("%s: 无有效样本" % name)
        return
    med = statistics.median(vals)
    verdict = "达标" if med < line else "不达标"
    print(
        "\n%s  n=%d  min=%.2f  median=%.2f  max=%.2f  → 按 median %s（线 %.1fs）"
        % (name, len(vals), min(vals), med, max(vals), verdict, line)
    )
    if len(vals) > 1:
        print("   样本: %s" % " ".join("%.2f" % v for v in sorted(vals)))


def fresh_copy_run(idx: int, prod_args: list[str]) -> float | None:
    """把整个 dist 目录复制成一份"从没运行过"的产物，量它的首次启动。

    这是在不重新构建的前提下最接近"装机后第一次启动"的办法：新文件、不在文件缓存里、
    杀软要对这个新路径上的 exe 做一次实时扫描。PyInstaller onedir 要求 _internal 与
    exe 同级，所以必须整目录复制。
    """
    import tempfile

    dst = Path(tempfile.gettempdir()) / ("uhf-cold-probe-%d" % idx)
    shutil.rmtree(dst, ignore_errors=True)
    t_copy = time.perf_counter()
    shutil.copytree(EXE.parent, dst)
    copy_s = time.perf_counter() - t_copy
    try:
        cmd = [str(dst / EXE.name)] + prod_args
        dt, err = run_once(cmd, timeout=120.0)
        if dt is None:
            print("  [cold %d] 失败: %s（复制耗时 %.1fs）" % (idx + 1, err, copy_s))
            return None
        print("  [cold %2d] 首次启动 %6.2f s   （复制 185MB 耗时 %.1fs，不计入）" % (idx + 1, dt, copy_s), flush=True)
        # 紧接着再跑一次同一份拷贝，作为"这份拷贝已热"的对照
        dt2, _ = run_once(cmd, timeout=90.0)
        if dt2 is not None:
            print("  [cold %2d] 同拷贝第二次 %5.2f s  → 首启溢价 %+.2f s" % (idx + 1, dt2, dt - dt2))
        return dt
    finally:
        shutil.rmtree(dst, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--dev", action="store_true", help="测 python sidecar.py 而不是冻结产物")
    ap.add_argument("--line", type=float, default=3.0, help="验收线，秒")
    ap.add_argument(
        "--cold-copies",
        type=int,
        default=0,
        help="额外量 N 次「整目录拷到临时位置后的首次执行」，近似装机后第一次启动",
    )
    args = ap.parse_args()

    exe = [sys.executable, "sidecar.py"] if args.dev else None
    if not args.dev:
        if not EXE.exists():
            print("找不到冻结产物 %s\n先跑: cd desktop && npm run build:kernel" % EXE)
            return 2
        exe = [str(EXE)]

    # 生产形态（desktop/src/main/sidecar.ts 就是这么起的）：只加 --duration 0，
    # **不加 --no-db**，所以 SQLAlchemy 建引擎 + create_all 在关键路径上。
    prod = exe + ["--duration", "0"]

    def state_env(scratch: Path) -> dict:
        import os

        return {**os.environ, "PD_STATE_DIR": str(scratch), "PYTHONIOENCODING": "utf-8"}

    print("== %s ==" % (" ".join(exe)))
    print("   先跑一次放行（钩子/杀软/建库），不计入")
    scratch0 = REPO / ".startup_scratch"
    shutil.rmtree(scratch0, ignore_errors=True)
    scratch0.mkdir(exist_ok=True)
    run_once(prod, timeout=90.0, env=state_env(scratch0))

    # A. 无库（等价 --no-db 的纯推流路径，作为下限参照）
    nodb = measure(exe + ["--no-db", "--duration", "0"], args.runs, "nodb", cool=False)
    report("A 无库 --no-db（下限参照）", nodb, args.line)

    # B. 生产形态，库已存在
    exists = measure(prod, args.runs, "db", cool=False, env=state_env(scratch0))
    report("B 生产形态·库已存在", exists, args.line)

    # C. 首次运行：每次换一个新的空状态目录，把建表成本算进去
    fresh = []
    for i in range(max(3, args.runs // 2)):
        d = REPO / (".startup_fresh_%d" % i)
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(exist_ok=True)
        dt, err = run_once(prod, timeout=90.0, env=state_env(d))
        if dt is None:
            print("  [fresh %d] 失败: %s" % (i + 1, err))
        else:
            fresh.append(dt)
            print("  [fresh %2d] %6.2f s" % (i + 1, dt), flush=True)
        shutil.rmtree(d, ignore_errors=True)
    report("C 首次运行·需建库建表", fresh, args.line)

    # D. 尽力挤缓存后的生产形态（冷启近似下界；Windows 无权限真清 standby list）
    cool = measure(prod, 3, "cool", cool=True, env=state_env(scratch0))
    report("D 生产形态·尽力挤文件缓存", cool, args.line)

    # E. 全新拷贝的首次执行（装机后第一次启动的近似；不重新构建就能复现）
    cold_vals: list[float] = []
    if args.cold_copies:
        print("\n  冷启动探针：整目录复制到临时位置，量这份拷贝的第一次执行")
        for i in range(args.cold_copies):
            v = fresh_copy_run(i, ["--duration", "0"])
            if v is not None:
                cold_vals.append(v)
    report("E 全新产物·首次执行（≈装机后第一次启动）", cold_vals, args.line)

    shutil.rmtree(scratch0, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
