// 校验"内核怎么被打包"与"内核怎么被找"两处约定是否一致。
//
// 这两个约定分属两种语言、三个文件（sidecar.spec / sidecar.ts / electron-builder.json），
// 写错任何一边的后果都不是构建失败，而是装机版静默起不来：resources 下没有 exe，
// resolveLaunch() 拼出的路径不存在，界面只显示"内核已停止"，没人知道为什么。
// 所以打包前先把这几处字面量对齐。
//
// 只读源码文本、不 import 编译产物——dist-electron/main/sidecar.js 要 electron
// 运行时才加载得了，构建期拿不到。

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const desktop = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(p, "utf-8");

const spec = read(join(desktop, "..", "sidecar.spec"));
const supervisor = read(join(desktop, "src", "main", "sidecar.ts"));
const builder = JSON.parse(read(join(desktop, "electron-builder.json")));

const fail = [];
const ok = [];

// PyInstaller 的产物名同时决定目录名与 exe 名
const exeName = spec.match(/name="([^"]+)"/)?.[1];
if (!exeName) fail.push("sidecar.spec 里找不到 name=\"...\"");
else ok.push(`冻结产物名 = ${exeName}`);

if (!/Analysis\(\s*\["sidecar\.py"\]/.test(spec)) {
  fail.push('sidecar.spec 的 Analysis 入口不是 ["sidecar.py"]（那两个旧 spec 冻的是 Qt main.py）');
}

// 主进程按哪个目录名、哪个文件名去找内核
const kernelDir = supervisor.match(/export const KERNEL_DIR = "([^"]+)"/)?.[1];
if (!kernelDir) fail.push('sidecar.ts 里找不到 export const KERNEL_DIR = "..."');

const exePattern = new RegExp(`join\\(dir, "${exeName}\\.exe"\\)`);
if (!exePattern.test(supervisor)) fail.push(`sidecar.ts 没有按 "${exeName}.exe" 拼 exe 路径`);

// electron-builder 把内核铺到 resources 下的哪个名字
const entry = (builder.extraResources ?? []).find((e) => e?.to === kernelDir);
if (!entry) {
  fail.push(`electron-builder.json 的 extraResources 里没有 to="${kernelDir}"`);
} else {
  const expectedFrom = `../dist/${exeName}`;
  if (entry.from !== expectedFrom) {
    fail.push(`extraResources.from 应为 "${expectedFrom}"，实际 "${entry.from}"`);
  } else {
    ok.push(`${expectedFrom} → resources/${kernelDir}/${exeName}.exe 一致`);
  }
}

// 装机版必须先把内核铺进 asar 之外
if (!/app\.isPackaged/.test(supervisor)) fail.push("sidecar.ts 没有 app.isPackaged 分支");

if (fail.length) {
  console.error("内核打包约定校验失败：");
  for (const f of fail) console.error(`  ✗ ${f}`);
  process.exit(1);
}
for (const m of ok) console.error(`  ✓ ${m}`);
console.log("内核打包约定一致");
