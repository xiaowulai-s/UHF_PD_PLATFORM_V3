/**
 * 启动并守护处理内核子进程。
 *
 * 装机版跑 PyInstaller 冻结的 uhf-sidecar.exe，开发版跑 `python sidecar.py`，
 * 两者的可执行文件解析见 resolveLaunch()。
 *
 * 端口与 token 由内核自己在 stdout 上打印后解析——token 每次启动随机，
 * 不能作为参数传进去。内核崩溃要自动重启并把状态报给渲染进程，否则前端
 * 只会看到画面静止而不知道为什么。
 */

import { spawn, type ChildProcessByStdio } from "node:child_process";
import type { Readable } from "node:stream";
import { EventEmitter } from "node:events";
import { createInterface } from "node:readline";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { app } from "electron";

type SidecarChild = ChildProcessByStdio<null, Readable, Readable>;

const PORT_RE = /ws:\/\/127\.0\.0\.1:(\d+)\/stream/;
// sidecar 打印的是 "  token     XXX"，带前导缩进，锚定行首会永远匹配不到
const TOKEN_RE = /^\s*token\s+(\S+)\s*$/;

export type SidecarState = "starting" | "ready" | "restarting" | "dead";

export interface SidecarEndpoints {
  port: number;
  token: string;
}

export function repoRoot(): string {
  // desktop/src/main/sidecar.ts -> 上溯三级到仓库根
  return fileURLToPath(new URL("../../../", import.meta.url));
}

export interface SidecarLaunch {
  exe: string;
  args: string[];
  cwd: string;
  /** 出错的提示语要能区分"没装内核"和"内核自己崩了"，现场排障方向完全不同 */
  kind: "bundled" | "source";
}

export interface LaunchEnv {
  isPackaged: boolean;
  /** 打包后的 resources 目录，electron-builder 的 extraResources 就往这里铺 */
  resourcesPath: string;
  repoRoot: string;
  override?: string;
  /** 透给内核的额外命令行参数，见 resolveLaunch 里的 PD_SIDECAR_ARGS */
  extraArgs?: string[];
}

/**
 * 内核可执行文件解析（纯函数，便于把 electron-builder 的铺盘约定和这里的拼接
 * 放在一起比对——两边名字对不上时，症状是"装机版静默起不来"，最难查）。
 *
 * 装机版跑 PyInstaller 冻结的 uhf-sidecar.exe，目标机不需要装 Python；开发版跑
 * `python sidecar.py`。仓库根上溯三级是**开发树布局**的假设，打进 asar 后那个位置
 * 是 resources/，里面既没有 sidecar.py 也没有 core/，所以装机版必须换一条路径。
 */
export function launchFrom(env: LaunchEnv): SidecarLaunch {
  const extra = env.extraArgs ?? [];
  if (env.override) {
    // override 既能指另一个解释器，也能直接指冻结 exe——后者才是"不装 Python 的机器
    // 上到底跑什么"的最省事验证路径。冻结 exe 不能再塞 sidecar.py：PyInstaller 会把
    // argv[1:] 原样交给脚本，argparse 于是收到一个多余的位置参数并以 exit 2 退出，
    // 表现是"内核起不来"而日志只有 argparse 的 usage。
    //
    // 判据必须是"是不是冻结产物本身"（uhf-sidecar.exe），**不能**用 /\.exe$/ 判：
    // 那会把 python.exe 也当成冻结 exe，sidecar.py 被吞掉，解释器 override 这条路
    // 整条废掉（实测：exit 2 + usage，且每次重启都同样失败）。
    if (/uhf-sidecar\.exe$/i.test(env.override)) {
      return { exe: env.override, args: [...extra], cwd: dirname(env.override), kind: "bundled" };
    }
    return { exe: env.override, args: ["sidecar.py", ...extra], cwd: env.repoRoot, kind: "source" };
  }
  if (env.isPackaged) {
    const dir = join(env.resourcesPath, KERNEL_DIR);
    return { exe: join(dir, "uhf-sidecar.exe"), args: [...extra], cwd: dir, kind: "bundled" };
  }
  return { exe: "python", args: ["sidecar.py", ...extra], cwd: env.repoRoot, kind: "source" };
}

/** 必须与 electron-builder.json 里 extraResources[].to 完全一致 */
export const KERNEL_DIR = "uhf-sidecar";

export function resolveLaunch(): SidecarLaunch {
  return launchFrom({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    repoRoot: repoRoot(),
    override: process.env.PD_SIDECAR_PYTHON,
    // 现场排障要能改内核参数（例如 --pd-type floating 造密集放电来看热图），
    // 否则只能去改源码里的 start() 调用。
    extraArgs: (process.env.PD_SIDECAR_ARGS ?? "").trim().split(/\s+/).filter(Boolean),
  });
}

/** 交给 PATH 解析的裸命令名，不带任何目录分隔符。 */
function isBareCommand(exe: string): boolean {
  return !exe.includes("/") && !exe.includes("\\");
}

export class SidecarSupervisor extends EventEmitter {
  private child: SidecarChild | null = null;
  private endpoint: SidecarEndpoints | null = null;
  private state: SidecarState = "starting";
  private stopping = false;
  private restartTimer: NodeJS.Timeout | null = null;
  private restarts = 0;

  constructor(private readonly launch: SidecarLaunch = resolveLaunch()) {
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
    const { exe, args, cwd, kind } = this.launch;

    // 只对带路径的可执行文件做存在性检查。裸命令名（开发态的 "python"）要靠 PATH
    // 解析，existsSync("python") 恒为 false，拿它当判据会把正常开发环境误报成
    // "未找到解释器"——这条是实跑 electron 才暴露出来的。
    if (!isBareCommand(exe) && !existsSync(exe)) {
      const where = kind === "bundled" ? `安装包缺少内核：${exe}` : `内核可执行文件不存在：${exe}`;
      this.endpoint = null;
      this.setState("dead");
      this.emit("log", `${where}${kind === "source" ? "（可用 PD_SIDECAR_PYTHON 指定）" : ""}`);
      return;
    }

    this.emit("log", `启动内核：${exe} ${[...args, "--duration", "0"].join(" ")}  (cwd=${cwd})`);
    const child: SidecarChild = spawn(exe, [...args, "--duration", "0", ...extraArgs], {
      cwd,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", QT_QPA_PLATFORM: "offscreen" },
      stdio: ["ignore", "pipe", "pipe"],
      // 冻结产物是 console=True 的，不隐藏会弹一个黑框；stdout 仍经管道拿到端口与 token
      windowsHide: true,
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
        // 内核的 INFO 日志走 stdout，被下面逐行正则消费后丢弃；没有这条就绪日志，
        // 排障时无法区分"没解析到端点"和"解析到了但渲染进程没连上"。
        this.emit("log", `内核就绪 port=${pendingPort}`);
        this.emit("endpoint", this.endpoint);
      }
    });

    child.stderr.on("data", (buf: Buffer) => {
      // sidecar 走 logging 到 stderr，保留下来便于排查
      this.emit("log", buf.toString("utf-8").trimEnd());
    });

    child.on("error", (err) => {
      // PATH 里根本没有这个命令：重试五次是纯空转，直接判死并说清缺什么
      if ((err as NodeJS.ErrnoException).code === "ENOENT") {
        this.endpoint = null;
        this.setState("dead");
        this.emit("log", `内核无法启动，PATH 里找不到 ${exe}（可用 PD_SIDECAR_PYTHON 指定）`);
        return;
      }
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
