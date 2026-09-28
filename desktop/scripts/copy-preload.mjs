// tsc 只编译 .ts，preload 是手写的 .cjs（sandbox 下不能用 ESM），
// 所以要显式搬进 dist-electron，否则运行时静默找不到桥接脚本。
import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const from = fileURLToPath(new URL("../src/preload/preload.cjs", import.meta.url));
const to = fileURLToPath(new URL("../dist-electron/preload/preload.cjs", import.meta.url));
mkdirSync(fileURLToPath(new URL("../dist-electron/preload", import.meta.url)), { recursive: true });
copyFileSync(from, to);
console.log(`preload -> ${to}`);
