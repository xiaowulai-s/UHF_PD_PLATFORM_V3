// preload 必须是 CommonJS：sandbox:true 的 preload 不走 ESM 加载器。
// 这里只做两件事——把连接参数交给渲染进程、把主进程的状态事件转发过去。
// 不暴露任何 Node 能力或 ipcRenderer 本体。
const { contextBridge, ipcRenderer, webUtils } = require("electron");

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

/**
 * 告知主进程当前主题的窗口底色。
 *
 * 为什么需要：`BrowserWindow` 的 `backgroundColor` 是主进程侧的属性，而主题存在渲染进程的
 * localStorage 里，主进程读不到。窗口缩放时新暴露出来的那块区域在页面重绘前显示的就是
 * 这个底色——深色主题下会闪一条白边。只接受 `#rrggbb`，主进程还会再校验一次。
 */
contextBridge.exposeInMainWorld("appTheme", {
  setWindowBackground: (color) => ipcRenderer.invoke("window:background", color),
});

/**
 * 文件保存桥。
 *
 * 为什么不能让渲染进程自己下载：渲染页面是 `loadFile` 加载的，origin 是 `file://`，
 * 而 `blob:file://…` 的 `<a download>` 在这个 origin 下**不会落盘**（无异常、无控制台
 * 报错、也没有文件，只能靠"点了没反应"发现）。打包版同样走 loadFile，所以不是 dev 专属问题。
 *
 * 这里只暴露"存文本"和"存内核给的字节"两个动作，落盘路径一律由主进程的原生对话框决定——
 * 渲染进程不能自己指定路径，避免把桥变成任意文件写入。
 */
contextBridge.exposeInMainWorld("files", {
  saveText: (name, text) => ipcRenderer.invoke("file:save-text", { name, text }),
  /**
   * 保存内核生成的二进制报告（xlsx/docx）。字节走 IPC 的结构化克隆原样传过去，
   * 渲染进程同样不能指定路径——和 saveText 一条纪律。
   */
  saveBinary: (name, bytes) => ipcRenderer.invoke("file:save-binary", { name, bytes }),
  /**
   * 把用户选中的 File 换成内核可读的绝对路径。
   * 沙箱渲染进程拿不到真实路径（Electron 32 起 `File.path` 已移除），必须在 preload
   * 里用 `webUtils.getPathForFile` 换——这一步只能在主世界做，contextBridge 可以把
   * File 对象原样传进来。路径只用于让内核读取该文件（data.import_file）。
   */
  pathFor: (file) => webUtils.getPathForFile(file),
});
