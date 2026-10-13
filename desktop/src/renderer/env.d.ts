/// <reference types="vite/client" />

// 环境模块声明里做顶层 import 会让 vue-tsc 在该作用域解析不到 "vue"
// （普通 .ts 文件里同样的 import 正常），所以这里改用内联 import type。
declare module "*.vue" {
  // props 用索引签名而不是 never：否则任何带 prop 的组件调用都会被判类型不符
  const component: import("vue").DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>;
  export default component;
}

interface ProbeBridge {
  report(result: unknown): void;
}

interface SidecarBridge {
  port: number;
  token: string;
  onEndpoint(fn: (endpoint: { port: number; token: string }) => void): () => void;
  onState(fn: (state: string) => void): () => void;
}

/**
 * 文件保存桥。渲染页面 origin 是 `file://`，`blob:` 的 `<a download>` 在这种 origin 下
 * 不会落盘，所以导出必须走主进程（路径由原生对话框决定）。
 */
interface FilesBridge {
  saveText(
    name: string,
    text: string,
  ): Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }>;
  /** 保存内核 report.export 生成的字节（xlsx/docx）。渲染进程不能指定路径。 */
  saveBinary(
    name: string,
    bytes: Uint8Array,
  ): Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }>;
  /**
   * 把用户选中的 File 换成内核可读的绝对路径（preload 里的 webUtils.getPathForFile）。
   * 沙箱渲染进程拿不到 File.path（Electron 32 起移除），数据导入必须走这里。
   */
  pathFor(file: File): string;
}

declare global {
  interface Window {
    /** 由 preload 经 contextBridge 暴露；纯浏览器预览时为 undefined。 */
    sidecar?: SidecarBridge;
    probe?: ProbeBridge;
    files?: FilesBridge;
    /**
     * 主题相关的窗口属性桥。`backgroundColor` 是主进程侧属性，而主题存在渲染进程的
     * localStorage 里，主进程读不到，只能由这边推过去。
     */
    appTheme?: {
      setWindowBackground(color: string): Promise<{ ok: boolean; color?: string; error?: string }>;
    };
  }
}

export {};
