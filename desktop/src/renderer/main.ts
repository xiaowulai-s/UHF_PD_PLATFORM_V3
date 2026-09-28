import { createApp } from "vue";
import App from "./App.vue";
import { router, PAGES } from "./router";
import { connect, retarget, sidecarState } from "./lib/store";
import { persistPrefs } from "./lib/prefs";
import "./style.css";

function bootstrapEndpoint() {
  const bridge = window.sidecar;
  if (bridge?.port) return { port: bridge.port, token: bridge.token };
  // 纯浏览器预览（没有 Electron preload）时走环境变量，便于单独调样式
  const port = Number(import.meta.env.VITE_SIDECAR_PORT ?? 0);
  const token = String(import.meta.env.VITE_SIDECAR_TOKEN ?? "");
  return port && token ? { port, token } : null;
}

const endpoint = bootstrapEndpoint();
if (endpoint) connect(endpoint);
else console.warn("[renderer] 未拿到 sidecar 端点，界面将以离线模式运行");

window.sidecar?.onEndpoint((next) => {
  sidecarState.value = "ready";
  retarget(next);
});
window.sidecar?.onState((s) => (sidecarState.value = s));

/** F1–F9 沿用旧版键位，现场操作习惯不变 */
function bindShortcuts(): void {
  const byKey = new Map<string, string>(PAGES.map((p) => [p.shortcut, p.path]));
  window.addEventListener("keydown", (ev) => {
    if (!/^F[1-9]$/.test(ev.key)) return;
    const path = byKey.get(ev.key);
    if (!path) return;
    ev.preventDefault();
    void router.push(path);
  });
}

persistPrefs();
bindShortcuts();
createApp(App).use(router).mount("#app");
