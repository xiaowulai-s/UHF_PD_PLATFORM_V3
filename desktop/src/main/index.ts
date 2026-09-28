import { app, BrowserWindow, ipcMain, shell } from "electron";
import { fileURLToPath } from "node:url";
import { SidecarSupervisor } from "./sidecar.js";

const PRELOAD = fileURLToPath(new URL("../preload/preload.cjs", import.meta.url));
// 编译产物在 dist-electron/main/，渲染层在 desktop/dist/
const RENDERER_INDEX = fileURLToPath(new URL("../../dist/index.html", import.meta.url));

const sidecar = new SidecarSupervisor();
let window: BrowserWindow | null = null;

/**
 * 端点信息经 additionalArguments 交给 preload，再经 contextBridge 暴露成
 * window.sidecar。渲染进程拿不到 Node 能力，只拿到这一份连接参数。
 */
function createWindow(endpoint: { port: number; token: string }): void {
  window = new BrowserWindow({
    width: 1600,
    height: 960,
    minWidth: 1180,
    minHeight: 720,
    show: false,
    backgroundColor: "#FFFFFF",
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: [`--sidecar-port=${endpoint.port}`, `--sidecar-token=${endpoint.token}`],
    },
  });

  window.once("ready-to-show", () => window?.show());

  // 渲染进程里没有可信的相对链接，外链一律交给系统浏览器
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    void window.loadURL(devUrl);
    window.webContents.openDevTools({ mode: "detach" });
  } else {
    void window.loadFile(RENDERER_INDEX);
  }
}

sidecar.on("endpoint", (endpoint) => {
  if (!window) {
    createWindow(endpoint);
    return;
  }
  // sidecar 重启后 token 会变，渲染进程必须拿新凭证重连
  window.webContents.send("sidecar:endpoint", endpoint);
});

sidecar.on("log", (line) => console.log(`[sidecar] ${line}`));
sidecar.on("state", (state) => window?.webContents.send("sidecar:state", state));

// 渲染进程直连 ws://127.0.0.1，主进程不做数据中转，避免 20FPS 帧多一次进程间拷贝
ipcMain.handle("app:sidecar-state", () => ({ state: sidecar.current, endpoint: sidecar.endpoints }));

app.whenReady().then(() => {
  sidecar.start();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && sidecar.endpoints) createWindow(sidecar.endpoints);
  });
});

app.on("window-all-closed", () => {
  sidecar.stop();
  app.quit();
});

// 未捕获异常会静默杀掉守护逻辑，至少留在 stderr 里
process.on("uncaughtException", (err) => console.error("[main] uncaught", err));
process.on("unhandledRejection", (reason) => console.error("[main] unhandled rejection", reason));
