/**
 * 启动并守护 Python 处理内核 sidecar 子进程。
 *
 * 端口与 token 由 sidecar 自己在 stdout 上打印后解析——token 每次启动随机，
 * 不能作为参数传进去。sidecar 崩溃要自动重启并把状态报给渲染进程，否则前端
 * 只会看到画面静止而不知道为什么。
 */

import { spawn, type ChildProcessByStdio } from "node:child_process";
import type { Readable } from "node:stream";

type SidecarChild = ChildProcessByStdio<null, Readable, Readable>;
import { EventEmitter } from "node:events";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const PORT_RE = /ws:\/\/127\.0\.0\.1:(\d+)\/stream/;
const TOKEN_RE = /^token\s+(\S+)/;

export type SidecarState = "starting" | "ready" | "restarting" | "dead";

export interface SidecarEndpoints {
  port: number;
  token: string;
}

export function repoRoot(): string {
  // desktop/src/main/sidecar.ts -> 上溯三级到仓库根
  return fileURLToPath(new URL("../../../", import.meta.url));
}

export class SidecarSupervisor extends EventEmitter {
  private child: SidecarChild | null = null;
  private endpoint: SidecarEndpoints | null = null;
  private state: SidecarState = "starting";
  private stopping = false;
  private restartTimer: NodeJS.Timeout | null = null;
  private restarts = 0;

  constructor(private readonly pythonPath = process.env.PD_SIDECAR_PYTHON ?? "python") {
    super();
  }

  get endpoints(): SidecarEndpoints | null {
    return this.endpoint;
  }

  get current(): SidecarState {
    return this.state;
  }

  start(extraArgs: string[] = []): void {
    this.stopping = false;
    this.spawnChild(extraArgs);
  }

  stop(): void {
    this.stopping = true;
    if (this.restartTimer) clearTimeout(this.restartTimer);
    this.child?.kill();
    this.child = null;
  }

  private setState(next: SidecarState): void {
    this.state = next;
    this.emit("state", next);
  }

  private spawnChild(extraArgs: string[]): void {
    const child: SidecarChild = spawn(this.pythonPath, ["sidecar.py", "--duration", "0", ...extraArgs], {
      cwd: repoRoot(),
      env: { ...process.env, PYTHONIOENCODING: "utf-8", QT_QPA_PLATFORM: "offscreen" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.child = child;

    const lines = createInterface({ input: child.stdout });
    let pendingPort: number | null = null;
    lines.on("line", (line) => {
      const port = line.match(PORT_RE)?.[1];
      if (port) pendingPort = Number(port);
      const token = line.match(TOKEN_RE)?.[1];
      if (token && pendingPort !== null) {
        this.endpoint = { port: pendingPort, token };
        this.restarts = 0;
        this.setState("ready");
        this.emit("endpoint", this.endpoint);
      }
    });

    child.stderr.on("data", (buf: Buffer) => {
      // sidecar 走 logging 到 stderr，保留下来便于排查
      this.emit("log", buf.toString("utf-8").trimEnd());
    });

    child.on("error", (err) => {
      this.emit("log", `sidecar 启动失败: ${err.message}`);
      this.scheduleRestart(extraArgs);
    });

    child.on("exit", (code, signal) => {
      if (this.stopping) return;
      this.emit("log", `sidecar 退出 code=${code} signal=${signal}`);
      this.scheduleRestart(extraArgs);
    });
  }

  /**
   * 退避重启。连续失败要封顶间隔并停止无限重试——比如 python 解释器路径不对时，
   * 无脑重启会变成刷屏 + CPU 空转，不如直接标记 dead 让前端提示用户。
   */
  private scheduleRestart(extraArgs: string[]): void {
    this.endpoint = null;
    this.restarts += 1;
    if (this.restarts > 5) {
      this.setState("dead");
      this.emit("log", "sidecar 连续 5 次启动失败，已放弃自动重启");
      return;
    }
    this.setState("restarting");
    const delay = Math.min(1000 * 2 ** (this.restarts - 1), 15000);
    this.restartTimer = setTimeout(() => this.spawnChild(extraArgs), delay);
  }
}
