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

declare global {
  interface Window {
    /** 由 preload 经 contextBridge 暴露；纯浏览器预览时为 undefined。 */
    sidecar?: SidecarBridge;
    probe?: ProbeBridge;
  }
}

export {};
