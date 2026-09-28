// 开发模式：等 Vite dev server 就绪后再拉起 Electron，并把 URL 传给它。
// 不用 vite-plugin-electron，是为了少绑一个第三方插件、主进程构建完全走 tsc。
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";

const URL = process.env.VITE_DEV_SERVER_URL ?? "http://localhost:5199/";

async function waitReady() {
  for (let i = 0; i < 120; i += 1) {
    try {
      const res = await fetch(URL, { signal: AbortSignal.timeout(1000) });
      if (res.ok) return;
    } catch {
      /* 还没起来 */
    }
    await delay(250);
  }
  throw new Error(`Vite dev server 未在 30 秒内就绪: ${URL}`);
}

await waitReady();

const { default: electronPath } = await import("electron").then((m) => ({ default: m.default }));
const child = spawn(electronPath, ["."], {
  stdio: "inherit",
  env: { ...process.env, VITE_DEV_SERVER_URL: URL },
});
child.on("exit", (code) => process.exit(code ?? 0));
