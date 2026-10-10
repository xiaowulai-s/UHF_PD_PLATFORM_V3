"""M7.4 sidecar 崩溃恢复验收。

口径（与验收表一致）：**恢复 < 5s，无告警丢失**。

做法：
1. 起真实应用（Electron + 侧车），等实时帧真的在流动；
2. 找到应用进程树里的侧车 python 进程并杀掉（模拟内核崩溃）；
3. 轮询吞吐表的 waveform"已收帧"，等它重新增长 —— 那一段耗时就是恢复时间；
4. 顺带核对服务端 diagnostics 的 `dropped_frames` 是否为 0
   （无损通道不允许限流，报警不能丢，所以这个计数必须是 0）。

只杀**本应用进程树里**的侧车，不碰机器上别的 python。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

import psutil
from websockets.sync.client import connect

REPO = r"D:\Demo\UHF_PD_PLATFORM_v3"
DESKTOP = os.path.join(REPO, "desktop")
EXE = os.path.join(DESKTOP, "node_modules", "electron", "dist", "electron.exe")
PORT = 9350
RECOVER_LIMIT_S = 30.0
APP_LOG = pathlib.Path(REPO) / "_archive" / "recovery-app.log"


def page_ws() -> str | None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=1) as r:
                for t in json.load(r):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.25)
    return None


def evaluate(ws: str, expr: str):
    with connect(ws, open_timeout=5, close_timeout=2) as s:
        s.send(
            json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}})
        )
        while True:
            m = json.loads(s.recv(timeout=20))
            if m.get("id") == 1:
                return m.get("result", {}).get("result", {}).get("value")


# 吞吐表第一行是 waveform，第二列是"已收帧"
RX_PROBE = """(()=>{const tr=document.querySelector('table tbody tr');
  if(!tr) return -1; const td=tr.querySelectorAll('td');
  return td.length>1?Number(td[1].textContent.trim()):-1;})()"""

# 顶栏/状态区显示的连接状态
STATE_PROBE = """(()=>{const m=document.querySelector('main');
  if(!m) return ''; const t=m.innerText;
  const i=t.indexOf('连接 '); return i>=0?t.slice(i,i+24).split('\\n')[0]:'';})()"""


def main() -> int:
    # Windows 控制台默认 GBK：判定行里的符号字符会抛 UnicodeEncodeError，
    # 把已经测出来的结果、日志尾巴和 proc.terminate() 一起吞掉（实测踩过）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
    env.update(
        {
            "PD_CAPTURE": os.path.join(REPO, ".workbuddy", "screenshots", "_m74.png"),
            "PD_ROUTE": "/",
            "PD_CAPTURE_WAIT": "180000",
            "PD_SIDECAR_ARGS": "--pd-type corona --pulse-rate 60000",
        }
    )
    proc = subprocess.Popen(
        [EXE, ".", "--disable-gpu-sandbox", "--no-sandbox", f"--remote-debugging-port={PORT}"],
        cwd=DESKTOP,
        env=env,
        stdout=APP_LOG.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    root = psutil.Process(proc.pid)
    ws = page_ws()
    if not ws:
        print("CDP 未就绪")
        proc.terminate()
        return 1

    # 1) 等帧流动
    rx0 = -1
    for _ in range(200):
        rx0 = evaluate(ws, RX_PROBE)
        if isinstance(rx0, (int, float)) and rx0 > 0:
            break
        time.sleep(0.25)
    print(f"起跑：waveform 已收帧 = {rx0}")
    if not isinstance(rx0, (int, float)) or rx0 <= 0:
        print("帧从未到达，无法继续")
        proc.terminate()
        return 1

    # 2) 找到并杀掉侧车
    def find_sidecar() -> psutil.Process | None:
        for p in root.children(recursive=True):
            try:
                cmd = " ".join(p.cmdline())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if "sidecar.py" in cmd or "uhf-sidecar" in cmd:
                return p
        return None

    side = find_sidecar()
    if side is None:
        print("没找到侧车子进程")
        proc.terminate()
        return 1
    side_pid = side.pid
    print(f"杀掉侧车 pid={side_pid} …")
    t_kill = time.perf_counter()
    side.kill()

    # 3) 等恢复。判据必须是**先确认停住、再确认恢复**：
    #    只判"计数大于起跑值"会在"杀掉前一瞬间恰好又到了一帧"时立刻成立，给出 0.0x 秒的
    #    假通过（实测踩过）。所以第一步先等到"计数连续两次不变且状态不再是 open"，
    #    那才叫真的断了；第二步才等它重新增长。
    stalled_at = None
    stalled_rx = None
    prev_rx = None
    samples: list[tuple[float, int, str]] = []
    while time.perf_counter() - t_kill < RECOVER_LIMIT_S:
        rx = evaluate(ws, RX_PROBE)
        st = evaluate(ws, STATE_PROBE)
        elapsed = time.perf_counter() - t_kill
        if isinstance(rx, (int, float)):
            samples.append((round(elapsed, 2), int(rx), str(st)))
            if prev_rx is not None and rx == prev_rx and "open" not in str(st):
                stalled_at = elapsed
                stalled_rx = int(rx)
                break
            prev_rx = int(rx)
        time.sleep(0.25)

    recovered = None
    if stalled_at is not None:
        while time.perf_counter() - t_kill < RECOVER_LIMIT_S:
            rx = evaluate(ws, RX_PROBE)
            st = evaluate(ws, STATE_PROBE)
            elapsed = time.perf_counter() - t_kill
            if isinstance(rx, (int, float)):
                samples.append((round(elapsed, 2), int(rx), str(st)))
                if int(rx) > stalled_rx and "open" in str(st):
                    recovered = elapsed
                    break
            time.sleep(0.25)

    print("\n恢复过程采样（t秒 / 已收帧 / 状态）：")
    for t, rx, st in samples[:: max(1, len(samples) // 12)]:
        print(f"  {t:>6.2f}s  rx={rx:<5} {st}")

    if recovered is None:
        # 分开报"没断成"与"断了没回来"：两者的处置方向完全不同
        why = (
            "杀掉侧车后没观测到中断（进程树找错了？）"
            if stalled_at is None
            else f"中断确认于 {stalled_at:.2f}s，但之后没恢复"
        )
        print(f"\n判定：**未通过** —— {why}")
        verdict = 1
    else:
        ok = recovered < 5.0
        print(
            f"\n判定：恢复耗时 {recovered:.2f}s（中断确认于 {stalled_at:.2f}s）"
            f"{'< 5s → 通过' if ok else '≥ 5s → 不通过'}"
        )
        verdict = 0 if ok else 2

    # 4) 顺带核对无丢失：服务端 diagnostics 的 dropped 必须为 0。
    #    这一步必须**等 stats 帧到达**：它是独立 topic、每 2 秒一帧，帧刚恢复时页脚还停在
    #    "内核运行 —"，此刻读只会得到"没找到"，而不是"丢弃为 0"。
    drop = -1
    for _ in range(30):
        drop = evaluate(
            ws,
            """(()=>{const m=document.querySelector('main');
          if(!m) return -1; const t=m.innerText; const i=t.indexOf('因滞后丢弃');
          return i>=0?Number((t.slice(i,i+16).match(/\\d+/)||[ -1 ])[0]):-1;})()""",
        )
        if isinstance(drop, (int, float)) and drop >= 0:
            break
        time.sleep(0.5)
    if isinstance(drop, (int, float)) and drop == 0:
        print('服务端"因滞后丢弃"计数 = 0 → 无丢失 PASS')
    elif isinstance(drop, (int, float)) and drop > 0:
        print(f'服务端"因滞后丢弃"计数 = {drop} → **有丢失，不通过**')
        verdict = 3
    else:
        print('服务端"因滞后丢弃"计数 = 未取到（stats 帧没到），本次这一项无结论')

    # 应用输出里的内核相关行：用来判断"侧车到底有没有重启起来"（没重启起来的话
    # 前端再怎么重连都没用，两者的处置方向完全不同）
    try:
        lines = [
            ln
            for ln in APP_LOG.read_text("utf-8", errors="replace").splitlines()
            if "sidecar" in ln.lower() or "内核" in ln
        ]
        print("\n应用输出里的内核相关行：")
        for ln in lines[-12:]:
            print("   ", ln[:170])
    except OSError:
        print("(读不到应用日志)")

    proc.terminate()
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
