// preload 必须是 CommonJS：sandbox:true 的 preload 不走 ESM 加载器。
// 这里只做两件事——把连接参数交给渲染进程、把主进程的状态事件转发过去。
// 不暴露任何 Node 能力或 ipcRenderer 本体。
const { contextBridge, ipcRenderer } = require("electron");

function readArg(prefix) {
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : "";
}

const port = Number(readArg("--sidecar-port=")) || 0;
const token = readArg("--sidecar-token=");

const listeners = { endpoint: new Set(), state: new Set() };

function subscribe(kind, fn) {
  listeners[kind].add(fn);
  return () => listeners[kind].delete(fn);
}

ipcRenderer.on("sidecar:endpoint", (_e, endpoint) => {
  for (const fn of listeners.endpoint) fn(endpoint);
});
ipcRenderer.on("sidecar:state", (_e, state) => {
  for (const fn of listeners.state) fn(state);
});

contextBridge.exposeInMainWorld("probe", {
  report: (result) => ipcRenderer.send("probe:done", result),
});

contextBridge.exposeInMainWorld("sidecar", {
  port,
  token,
  onEndpoint: (fn) => subscribe("endpoint", fn),
  onState: (fn) => subscribe("state", fn),
});
