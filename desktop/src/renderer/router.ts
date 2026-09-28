import type { Component } from "vue";
import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";

/**
 * 用 hash 模式：打包后主进程 loadFile() 打开的是 file://，history 模式会 404。
 * F1–F9 快捷键沿用旧版 ui/main_window.py 的键位，现场操作肌肉记忆不变。
 */
export const PAGES = [
  { key: "dashboard", path: "/", label: "系统概览", shortcut: "F1" },
  { key: "monitor", path: "/monitor", label: "实时监测", shortcut: "F2" },
  { key: "trend", path: "/trend", label: "趋势分析", shortcut: "F3" },
  { key: "analysis", path: "/analysis", label: "数据分析", shortcut: "F4" },
  { key: "ae", path: "/ae", label: "AE 分析", shortcut: "F5" },
  { key: "alarm", path: "/alarm", label: "报警管理", shortcut: "F6" },
  { key: "device", path: "/device", label: "设备管理", shortcut: "F7" },
  { key: "settings", path: "/settings", label: "系统设置", shortcut: "F8" },
  { key: "log", path: "/log", label: "系统日志", shortcut: "F9" },
] as const;

const views = import.meta.glob("./views/*.vue", { eager: true }) as Record<string, { default: Component }>;

const routes: RouteRecordRaw[] = PAGES.map((page) => ({
  path: page.path,
  name: page.key,
  component: views[`./views/${page.key}-view.vue`]!.default,
  meta: { label: page.label, shortcut: page.shortcut },
}));

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
});
