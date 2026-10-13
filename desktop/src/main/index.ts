import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { existsSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { SidecarSupervisor } from "./sidecar.js";

/** `--probe[=秒]` 进入渲染能力探针模式，跑完写报告并退出，用于现场工控机实测。 */
const PROBE_ARG = process.argv.find((a) => a === "--probe" || a.startsWith("--probe="));
const PROBE_SECONDS = PROBE_ARG ? Number(PROBE_ARG.split("=")[1] ?? 20) : 0;
const PROBE_REPORT = process.env.PD_PROBE_REPORT ?? "render-probe.json";

/** PD_CAPTURE=出图路径 进入截图验收模式；PD_ROUTE 指定 hash 路由，PD_CAPTURE_WAIT 毫秒。 */
const CAPTURE_TO = process.env.PD_CAPTURE ?? "";
const CAPTURE_ROUTE = (process.env.PD_ROUTE ?? "").replace(/^#?\/?/, "");
const CAPTURE_WAIT_MS = Number(process.env.PD_CAPTURE_WAIT ?? 4000);
const CAPTURE_INTERACT = process.env.PD_INTERACT === "1";

const PRELOAD = fileURLToPath(new URL("../preload/preload.cjs", import.meta.url));
// 编译产物在 dist-electron/main/，渲染层在 desktop/dist/
const RENDERER_INDEX = fileURLToPath(new URL("../../dist/index.html", import.meta.url));

/**
 * 窗口/任务栏图标。装机版由 electron-builder 的 win.icon 烘进 exe，这里管的是
 * 开发模式（不设就是 Electron 默认图标）以及窗口标题栏。
 * 打包后 build-resources 不在 asar 里，取不到就退回 exe 自带图标，所以给 undefined。
 */
const ICON_PATH = fileURLToPath(new URL("../../build-resources/icon.png", import.meta.url));
const WINDOW_ICON = existsSync(ICON_PATH) ? ICON_PATH : undefined;

const sidecar = new SidecarSupervisor();
let window: BrowserWindow | null = null;

/**
 * 端点信息经 additionalArguments 交给 preload，再经 contextBridge 暴露成
 * window.sidecar。渲染进程拿不到 Node 能力，只拿到这一份连接参数。
 *
 * endpoint 可为 null：窗口必须不等内核就绪就建出来。否则内核起不来（目标机没
 * 冻结产物、DLL 缺失、杀毒软件拦截）时整个应用是"有个 Electron 进程但一个窗口
 * 都没有"，restarting/dead 这两个状态发往 null window 被静默丢弃，现场只能看到
 * 双击图标没反应。渲染层本来就支持无端点启动并在拿到 endpoint 时重连。
 */
function createWindow(endpoint: { port: number; token: string } | null): void {
  window = new BrowserWindow({
    width: 1600,
    height: 960,
    minWidth: 1180,
    minHeight: 720,
    show: false,
    backgroundColor: "#FFFFFF",
    icon: WINDOW_ICON,
    // 截图验收模式下强制置顶可见：窗口一旦被遮挡，document.hidden 变 true，
    // rAF 与 uPlot 初始化整条停摆，onFrame 不再被调用——那时候截到的是旧画面，
    // 而读数会显示"绘制 0 FPS"，很容易误判成渲染 bug。
    ...(CAPTURE_TO ? { alwaysOnTop: true, show: true } : {}),
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      additionalArguments: endpoint
        ? [`--sidecar-port=${endpoint.port}`, `--sidecar-token=${endpoint.token}`]
        : [],
    },
  });

  window.once("ready-to-show", () => window?.show());

  // 渲染进程里没有可信的相对链接，外链一律交给系统浏览器
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });

  window.webContents.on("did-fail-load", (_e, code, description, url) => {
    console.error(`[main] 渲染层加载失败 ${code} ${description}: ${url}`);
  });
  window.webContents.on("did-finish-load", () => {
    console.log("[main] 渲染层已加载");
    // 窗口现在早于内核创建，endpoint 有可能在渲染进程装好 onEndpoint 监听之前就到过
    // 一次——那一推就永久丢了，界面停在离线模式。加载完成后再补推一次当前端点。
    if (sidecar.endpoints) window?.webContents.send("sidecar:endpoint", sidecar.endpoints);
  });
  window.webContents.on("render-process-gone", (_e, details) => {
    console.error(`[main] 渲染进程消失: ${details.reason} exit=${details.exitCode}`);
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  // hash 路由：参数要写在 # 之后，形如 index.html#probe?probeMs=20000
  const probeHash = PROBE_ARG ? `probe?probeMs=${Math.round(PROBE_SECONDS * 1000)}` : "";
  const hash = probeHash || (CAPTURE_TO ? `${CAPTURE_ROUTE}` : "");
  if (devUrl) {
    void window.loadURL(devUrl.replace(/\/$/, "") + "/" + (hash ? `#/${hash}` : ""));
    if (!CAPTURE_TO) window.webContents.openDevTools({ mode: "detach" });
  } else {
    void window.loadFile(RENDERER_INDEX, hash ? { hash } : undefined);
  }

  if (CAPTURE_TO) {
    // 截图模式要把渲染进程的控制台搬到 stdout：图表组件在 setup/mount 里抛错时，
    // 页面只会安静地少一个 canvas，光看截图分不清是"没数据"还是"组件崩了"。
    window.webContents.on("console-message", (_e, _level, message, line, source) => {
      console.log(`[renderer] ${message} (${source}:${line})`);
    });
    void takeCapture(window);
  }
}

/**
 * 截图并回读页面读数，用于图表类页面的可视验收。
 *
 * 存在的理由：canvas + rAF 驱动的面板没法用 DOM 快照验证，而 IDE 内置浏览器在
 * 后台时 document.hidden 为 true，rAF 与 uPlot 的内部初始化整条停摆（连
 * redraw(true,true) 都算不出 scale），拿不到任何画面。Electron 窗口是真可见表面，
 * 从它这里截既拿到像素，又顺带跑通了 preload/token/端点这条真实链路。
 */
async function snap(win: BrowserWindow, to: string): Promise<Record<string, unknown>> {
  // 路由漂移检测：实测有游离的 F6/F3 keydown 会把页面跳走，截图就拍到了别的页，
  // 而读数看起来只是"没数据"，非常误导。只在真的跳走时才导航回去——每次都强制
  // 设 hash 会让各图表面板重新挂载，交互验收里刚按下的暂停/自动量程就丢了。
  if (CAPTURE_ROUTE) {
    const drifted = await win.webContents.executeJavaScript(`location.hash !== "#/${CAPTURE_ROUTE}"`);
    if (drifted) {
      // 先分诊再纠正：实测"页面自己跳走"绝大多数不是应用缺陷，而是截图窗口带
      // alwaysOnTop，谁在机器前动鼠标就会点到侧栏把它点走了。探针里有 pointerdown
      // 就是外部输入，别当成路由 bug 去查。
      const external = await win.webContents.executeJavaScript(
        `(() => (window.__pdNav || []).some(e => e.includes('pointerdown')))()`,
      );
      console.log(
        external
          ? `[capture] 路由被外部鼠标点击改变（探针见 pointerdown），非应用自身跳转；仍强制回 #/${CAPTURE_ROUTE}`
          : `[capture] 路由漂移到别处且无鼠标事件，已强制回 #/${CAPTURE_ROUTE}` +
            `；无 pointerdown 的跳转才需要查前端`,
      );
      await win.webContents.executeJavaScript(`location.hash = "#/${CAPTURE_ROUTE}"`);
      await new Promise((r) => setTimeout(r, 900));
    }
  }
  const image = await win.webContents.capturePage();
  writeFileSync(to, image.toPNG());
  const readout = await win.webContents.executeJavaScript(
    // 带上 location.hash：只读 main 的文本没法区分"路由没跳对"和"读错了元素"，
    // 这两者的排查方向完全不同。nav 是导航探针记到的事件序列，用来定位"页面自己跳走"。
    `(() => { const m = document.querySelector('main'); return { hash: location.hash, text: m ? m.innerText.slice(0, 400) : '', canvases: document.querySelectorAll('canvas').length, hidden: document.hidden, nav: window.__pdNav || [] }; })()`,
  );
  console.log(`[capture] 已写入 ${to}`);
  return { to, ...readout };
}

/**
 * 用合成输入跑一遍交互并逐张取证。
 *
 * 图表验收不只看静态画面：游标读数、框选缩放、暂停都是 M4 的验收项，而这些只能靠
 * 真实 DOM 事件触发。sendInputEvent 走的是浏览器输入管线，uPlot 监听到的就是真的
 * mousedown/mousemove，比在控制台里手调内部方法可信。
 */
async function driveInteractions(win: BrowserWindow, prefix: string): Promise<void> {
  const { width, height } = win.getContentBounds();
  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const centerOf = async (label: string): Promise<{ x: number; y: number }> => {
    const rect = await win.webContents.executeJavaScript(
      `(() => { const b = [...document.querySelectorAll('button')].find(e => e.textContent.trim() === ${JSON.stringify(label)}); if (!b) return null; const r = b.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`,
    );
    if (!rect) throw new Error(`找不到按钮：${label}`);
    return rect;
  };
  const click = async (label: string) => {
    const { x, y } = await centerOf(label);
    const px = Math.round(x);
    const py = Math.round(y);
    // 必须先 move 再 down/up：Chromium 的 click 依赖指针已落在该元素上，
    // 直接对着没去过的坐标发 mouseDown/mouseUp 不会派发 click。
    await win.webContents.sendInputEvent({ type: "mouseMove", x: px, y: py });
    // clickCount 不显式给 1 时 Chromium 不会合成 click 事件，表现就是"鼠标到位了、
    // 按钮却像没被点过"——排查了很久，值得留在这里。
    await win.webContents.sendInputEvent({ type: "mouseDown", x: px, y: py, clickCount: 1 });
    await win.webContents.sendInputEvent({ type: "mouseUp", x: px, y: py, clickCount: 1 });
    await wait(400);
  };

  // 0) 先点"暂停"：它的效果会直接写进 innerText（● 实时 → ● 暂停），
  //    是判断合成点击到底有没有送达的最省事探针。
  await click("暂停");
  console.log(JSON.stringify(await snap(win, `${prefix}-pause.png`)));

  // 1) 打开游标并把指针停在波形中部，验证读数
  await click("游标");
  const waveY = Math.round(height * 0.28);
  await win.webContents.sendInputEvent({ type: "mouseMove", x: Math.round(width * 0.45), y: waveY });
  await wait(500);
  console.log(JSON.stringify(await snap(win, `${prefix}-cursor.png`)));

  // 2) 框选放大：uPlot 要靠相邻两次 move 的位移判定拖拽，且 move 必须带上"键仍按着"
  //    的状态——Electron 用 clickCount 承载 CDP 的 buttons 位，不给就等于松手在拖。
  const from = { x: Math.round(width * 0.3), y: waveY - 45 };
  const to = { x: Math.round(width * 0.58), y: waveY + 45 };
  await win.webContents.sendInputEvent({ type: "mouseMove", x: from.x, y: from.y });
  await win.webContents.sendInputEvent({ type: "mouseDown", x: from.x, y: from.y, clickCount: 1 });
  for (let i = 1; i <= 6; i += 1) {
    await win.webContents.sendInputEvent({
      type: "mouseMove",
      x: Math.round(from.x + ((to.x - from.x) * i) / 6),
      y: Math.round(from.y + ((to.y - from.y) * i) / 6),
      clickCount: 1,
      // Electron 的 sendInputEvent 不暴露 CDP 的 buttons 位掩码；不给 button 时
      // 中间这些 move 会以"无键按下"送达，uPlot 就把整段当成 click 而不是拖拽。
      button: "left",
    });
    await wait(30);
  }
  await win.webContents.sendInputEvent({ type: "mouseUp", x: to.x, y: to.y, clickCount: 1 });
  await wait(900);
  console.log(JSON.stringify(await snap(win, `${prefix}-zoom.png`)));

  // 3) 复位
  await click("复位");
  await wait(700);
  console.log(JSON.stringify(await snap(win, `${prefix}-reset.png`)));

  // 4) 继续 + 关掉自动量程：走的是 setScale 分支，与上面的 setData 复位不是同一条路，
  //    必须单独验。关掉后 y 轴应钉在当前范围，不再随新帧重算。
  await click("继续");
  await click("自动量程");
  await wait(1500);
  console.log(JSON.stringify(await snap(win, `${prefix}-autorange-off.png`)));

  // 5) PRPD 密度图：这个开关必须落到内核（高斯平滑在 compute 里做），而且 PRPD
  //    每 50 帧才发布一次，等太久会拿不到新模式的帧，等太短又来不及发。
  await click("密度图");
  await wait(6000);
  console.log(JSON.stringify(await snap(win, `${prefix}-prpd-density.png`)));

  // 6) PRPD 散点图：纯前端视图切换，数据来自帧里的 events，不该惊动内核
  await click("散点图");
  await wait(1500);
  console.log(JSON.stringify(await snap(win, `${prefix}-prpd-scatter.png`)));

  // 7) PRPS 自转：按需渲染的计数天生很低，只有每帧都画才能量出三维链路的真实上限
  await click("自转");
  await wait(2500);
  console.log(JSON.stringify(await snap(win, `${prefix}-prps-spin.png`)));

  // 8) 回到热力图，与上面的散点图同场对照：两图共用同一个 maxAmp 纵轴，
  //    事件带若一个在中上部、一个贴底，就是映射错了而不是数据变了。
  await click("热力图");
  await wait(3000);
  console.log(JSON.stringify(await snap(win, `${prefix}-prpd-heatmap.png`)));

  // 9) AE 模式切换（M5.1）：右下角面板从 PRPS 换成 AE 命中参数。
  //    这一步只能真点——模式是显式 UI 状态，不点就永远拍不到 AE 视图。
  await click("AE 视图");
  await wait(2500);
  console.log(JSON.stringify(await snap(win, `${prefix}-ae-mode.png`)));
  await click("局放视图");
  await wait(1200);

  // 10) 热力图点击读数：这条链路唯一的风险点是索引方向——矩阵是 (phase, amplitude)
  //    行主序、显示时才是 .T，读成 cells[ampIdx*phaseBins+phaseIdx] 不会越界也不报错，
  //    只会给出一个"看着像回事"的错数字。
  //
  //    不能只点一下：483 个事件摊进 360×256 个格，簇内单格期望值约 0.1，
  //    随机点一下读到 0 是正确结果，测不出任何东西。所以扫一片网格，
  //    再用"非零命中聚在哪个相位列"和电晕的已知相位区间(270~330°)对撞——
  //    读反了轴的话命中会跑到别处去。
  const rect = await win.webContents.executeJavaScript(
    `(() => { const b = [...document.querySelectorAll('button')].find(e => e.textContent.trim() === '热力图');` +
      ` if (!b) return null; let el = b.parentElement, cv = null;` +
      ` while (el && !cv) { cv = el.querySelector('canvas'); if (!cv) el = el.parentElement; }` +
      ` if (!cv) return null; const r = cv.getBoundingClientRect();` +
      ` return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`,
  );
  if (!rect) throw new Error("找不到 PRPD 热力图 canvas");
  const MARGIN = { left: 46, right: 12, top: 10, bottom: 34 };
  const plotW = rect.w - MARGIN.left - MARGIN.right;
  const plotH = rect.h - MARGIN.top - MARGIN.bottom;
  const countOf = (s: string) => Number((s.match(/计数\s*([\d.]+)/) ?? [0, "NaN"])[1]);
  const phaseOf = (s: string) => Number((s.match(/相位\s*([\d.]+)/) ?? [0, "NaN"])[1]);
  const ampOf = (s: string) => Number((s.match(/幅值\s*([\d.]+)/) ?? [0, "NaN"])[1]);

  /** 点一次并回读页面读数。fx/fy 是绘图区内比例：fy=0 在顶部（满量程），fx=1 在 360° */
  async function probe(fx: number, fy: number): Promise<string> {
    const px = Math.round(rect.x + MARGIN.left + plotW * fx);
    const py = Math.round(rect.y + MARGIN.top + plotH * fy);
    await win.webContents.sendInputEvent({ type: "mouseMove", x: px, y: py });
    await win.webContents.sendInputEvent({ type: "mouseDown", x: px, y: py, clickCount: 1 });
    await win.webContents.sendInputEvent({ type: "mouseUp", x: px, y: py, clickCount: 1 });
    await wait(120);
    return (await win.webContents.executeJavaScript(
      `(() => { const el = [...document.querySelectorAll('*')]` +
        `.filter(e => /相位 .*幅值 .*计数/.test(e.innerText) && e.children.length === 0).pop();` +
        ` return el ? el.innerText : ''; })()`,
    )) as string;
  }

  const hits: { phase: number; amp: number; count: number }[] = [];
  let probes = 0;
  // 幅值直接写 mV：电晕簇实测在 8~35 mV（量程 100 mV），别和"占满量程的比例"混掉
  for (const fx of [0.75, 0.8, 0.83, 0.87, 0.92]) {
    for (const mv of [12, 20, 30]) {
      const info = await probe(fx, 1 - mv / 100);
      probes += 1;
      const c = countOf(info);
      if (c > 0) hits.push({ phase: phaseOf(info), amp: ampOf(info), count: c });
    }
  }
  const maxHit = hits.reduce((a, b) => (b.count > a.count ? b : a), { phase: 0, amp: 0, count: 0 });
  console.log(
    `[capture] 簇内扫描 ${probes} 次，非零命中 ${hits.length} 个，` +
      `最大读数：相位 ${maxHit.phase}° 幅值 ${maxHit.amp} mV 计数 ${maxHit.count}`,
  );
  const phases = hits.map((h) => h.phase);
  const inCoronaBand = hits.filter((h) => h.phase >= 265 && h.phase <= 335).length;
  console.log(
    hits.length === 0
      ? "[capture] 命中相位区间：<无命中> ✗ 一次非零读数都没拿到，读数链路等于没测"
      : `[capture] 命中相位区间 ${Math.min(...phases)}~${Math.max(...phases)}°，` +
          `落在电晕带(265~335°)内 ${inCoronaBand}/${hits.length}` +
          `${inCoronaBand === hits.length ? " ✓ 与电晕特征一致" : " ✗ 与电晕特征不符"}`,
  );
  const miss = await probe(0.35, 1 - 0.9);
  console.log(`[capture] 空区对照读数：${miss}（应为 0）`);
  console.log(JSON.stringify(await snap(win, `${prefix}-prpd-readout.png`)));
}

/**
 * 走一遍"改速率 → 下发 → 读回内核态"的真实点击链路。
 *
 * M4.6 的契约有两半：内核能限流，前端能把用户输入落到那条 RPC 上。后半只有真点
 * 才测得出来——param 形状写错（比如多包一层 {rates:{...}}）在 Python 侧的测试里
 * 是测不到的，页面只会安静地报错条或者干脆显示"跟随管线"。
 */
async function driveThrottle(win: BrowserWindow, prefix: string): Promise<void> {
  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const evalJs = (code: string) => win.webContents.executeJavaScript(code);
  const rowOf = (topic: string) =>
    `(() => { const r = [...document.querySelectorAll('tbody tr')]` +
    `.find(x => x.children[0] && x.children[0].innerText.trim() === ${JSON.stringify(topic)});` +
    ` return r ? [...r.children].map(c => c.innerText.trim()) : null; })()`;

  console.log(JSON.stringify(await evalJs(rowOf("waveform"))));

  // 按行定位而不是取 document 里第一个 input：设置页上半部就有数字框，选错会把
  // Hz 写进幅值阈值那种毫不相干的字段。
  const box = await evalJs(
    `(() => { const r = [...document.querySelectorAll('tbody tr')]` +
      `.find(x => x.children[0] && x.children[0].innerText.trim() === 'waveform');` +
      ` if (!r) return null; const b = r.children[1].querySelector('input').getBoundingClientRect();` +
      ` return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2),` +
      ` before: r.children[2].innerText.trim() }; })()`,
  );
  if (!box) throw new Error("设置页没找到 waveform 行");

  await win.webContents.sendInputEvent({ type: "mouseMove", x: box.x, y: box.y });
  await win.webContents.sendInputEvent({ type: "mouseDown", x: box.x, y: box.y, clickCount: 1 });
  await win.webContents.sendInputEvent({ type: "mouseUp", x: box.x, y: box.y, clickCount: 1 });
  await wait(150);
  // type="number" 要先清空才好用单键输入：直接敲 5 会变成 5<原值>。
  await evalJs(
    `(() => { const el = document.activeElement; if (el && el.tagName === 'INPUT') { el.value = ''; el.dispatchEvent(new Event('input', { bubbles: true })); } return el && el.tagName; })()`,
  );
  for (const ch of "5") {
    // 按键入框要走 Electron 的 KeyboardInputEvent：它认的是 keyCode，而且只有
    // type:"char" 才会真正插入文本——只发 rawKeyDown 的话 focus 是对的、框里却是空的。
    await win.webContents.sendInputEvent({ type: "rawKeyDown", keyCode: ch });
    await win.webContents.sendInputEvent({ type: "char", keyCode: ch });
    await win.webContents.sendInputEvent({ type: "keyUp", keyCode: ch });
  }
  await wait(150);
  const typed = await evalJs(
    `(() => { const r = [...document.querySelectorAll('tbody tr')]` +
      `.find(x => x.children[0] && x.children[0].innerText.trim() === 'waveform');` +
      ` return { value: r.children[1].querySelector('input').value, focused: document.activeElement.tagName }; })()`,
  );
  console.log(`[capture] waveform 速率输入框 = ${JSON.stringify(typed)}`);
  console.log(JSON.stringify(await snap(win, `${prefix}-form.png`)));

  // 点该行的"应用"：按行取按钮，而不是页面里第一个应用按钮。
  const btn = await evalJs(
    `(() => { const r = [...document.querySelectorAll('tbody tr')]` +
      `.find(x => x.children[0] && x.children[0].innerText.trim() === 'waveform');` +
      ` const b = [...r.querySelectorAll('button')][0]; const q = b.getBoundingClientRect();` +
      ` return { x: Math.round(q.left + q.width / 2), y: Math.round(q.top + q.height / 2) }; })()`,
  );
  await win.webContents.sendInputEvent({ type: "mouseMove", x: btn.x, y: btn.y });
  await win.webContents.sendInputEvent({ type: "mouseDown", x: btn.x, y: btn.y, clickCount: 1 });
  await win.webContents.sendInputEvent({ type: "mouseUp", x: btn.x, y: btn.y, clickCount: 1 });
  await wait(1200);
  console.log(JSON.stringify(await snap(win, `${prefix}-applied.png`)));
  console.log(
    `[capture] 应用后 waveform 行 = ${JSON.stringify(await evalJs(rowOf("waveform")))}（下发前 ${box.before}）`,
  );

  // 端到端闭环：限流值必须真的把线上的 waveform 帧掐下来，而不只是 RPC 返回个 map。
  // 概览页的表尾读的是内核 diagnostics 累计计数，所以跳过去看一眼就能同时证明
  // "写进去了"和"生效了"。这里不用 snap()：它会做路由纠偏，正是要离开设置页的时候。
  await evalJs(`location.hash = "#/"`);
  await wait(4000);
  const footer = await evalJs(
    `(() => { const d = [...document.querySelectorAll('div')]` +
      `.map(e => e.innerText).find(t => t && t.startsWith('服务端发出')); return d || '<未找到表尾>'; })()`,
  );
  console.log(`[capture] 概览页表尾：${footer.replace(/\n/g, " ")}`);
  const image = await win.webContents.capturePage();
  writeFileSync(`${prefix}-dashboard.png`, image.toPNG());
  console.log(`[capture] 已写入 ${prefix}-dashboard.png`);
  await evalJs(`location.hash = "#/settings"`);
  await wait(800);

  // 越界值必须把内核的 ValueError 渲染成错误条，而不是静默回落到"跟随管线"。
  const bad = await evalJs(
    `(() => { const r = [...document.querySelectorAll('tbody tr')]` +
      `.find(x => x.children[0] && x.children[0].innerText.trim() === 'fft');` +
      ` const i = r.children[1].querySelector('input'); i.value = '9999';` +
      ` i.dispatchEvent(new Event('input', { bubbles: true }));` +
      ` const b = [...r.querySelectorAll('button')][0]; b.click(); return i.value; })()`,
  );
  await wait(1000);
  console.log(`[capture] 给 fft 灌越界值 ${bad} 后的提示条：`);
  console.log(
    JSON.stringify(
      await evalJs(
        `(() => { const p = [...document.querySelectorAll('main p')]` +
          `.filter(e => /失败|已下发/.test(e.innerText)).map(e => e.innerText); return p; })()`,
      ),
    ),
  );
  console.log(JSON.stringify(await snap(win, `${prefix}-rejected.png`)));

  // 留空 = 不限流：这是前端唯一自己做语义翻译的地方，必须真点一次确认它落到 null
  // 而不是 0（内核把 0 和 null 同样视为清除，所以这里看不出差别，但 0 会走 float(0)
  // 之外的分支，仍值得钉住）。
  await evalJs(
    `(() => { const r = [...document.querySelectorAll('tbody tr')]` +
      `.find(x => x.children[0] && x.children[0].innerText.trim() === 'waveform');` +
      ` const i = r.children[1].querySelector('input'); i.value = '';` +
      ` i.dispatchEvent(new Event('input', { bubbles: true }));` +
      ` [...r.querySelectorAll('button')][0].click(); return true; })()`,
  );
  await wait(1200);
  console.log(
    `[capture] 清空后 waveform 行 = ${JSON.stringify(await evalJs(rowOf("waveform")))}`,
  );
  console.log(JSON.stringify(await snap(win, `${prefix}-cleared.png`)));
}

/**
 * 截图模式下给渲染层装一个导航探针。
 *
 * 起因：实测有间歇性的"页面自己跳走"（强制纠正到 #/monitor 后 900ms 内又变成
 * #/settings），而静态截图只能看到结果、看不到是谁触发的。路由表里没有任何自动
 * 跳转逻辑，所以嫌疑只剩 keydown 与 hashchange 两类真实事件——把它们按序记下来
 * 才能定性。只装在 capture 路径里，不进正常启动流程。
 */
async function installNavProbe(win: BrowserWindow): Promise<void> {
  await win.webContents.executeJavaScript(`(() => {
    window.__pdNav = [];
    const t0 = performance.now();
    const at = () => ((performance.now() - t0) / 1000).toFixed(2) + 's';
    addEventListener('hashchange', (e) => window.__pdNav.push(
      at() + ' hashchange ' + (location.hash || '(空)') + ' <- ' + (e.oldURL.split('#')[1] ? '#' + e.oldURL.split('#')[1] : '(空)')), true);
    addEventListener('keydown', (e) => window.__pdNav.push(
      at() + ' keydown ' + e.key + ' repeat=' + e.repeat + ' target=' + (e.target && e.target.tagName)), true);
    addEventListener('pointerdown', (e) => window.__pdNav.push(
      at() + ' pointerdown ' + (e.target && e.target.tagName)), true);
    return true;
  })()`);
}

async function takeCapture(win: BrowserWindow): Promise<void> {
  await new Promise((r) => win.webContents.once("did-finish-load", () => r(null)));
  await installNavProbe(win);
  await new Promise((r) => setTimeout(r, CAPTURE_WAIT_MS));
  console.log(JSON.stringify(await snap(win, CAPTURE_TO)));
  try {
    if (CAPTURE_INTERACT) {
      // 节流契约的验收项在设置页，与图表页的游标/框选完全不同一套动作，走各自的驱动器。
      if (CAPTURE_ROUTE.startsWith("settings")) await driveThrottle(win, CAPTURE_TO.replace(/\.png$/, ""));
      else await driveInteractions(win, CAPTURE_TO.replace(/\.png$/, ""));
    }
  } catch (err) {
    // 驱动器抛错必须让进程退出：之前异常一路逃到 promise 里没人接，表现是应用
    // 挂着不走、验收脚本干等到超时也拿不到任何后续截图，也看不到错在哪。
    console.error(`[capture] 交互驱动失败：${(err as Error).stack ?? err}`);
  }
  sidecar.stop();
  app.quit();
}

ipcMain.on("probe:done", (_event, result) => {
  const gpu = (result as { gpu?: { renderer?: string } })?.gpu;
  console.log(`[probe] GPU renderer: ${gpu?.renderer ?? "<unknown>"}`);
  writeFileSync(PROBE_REPORT, JSON.stringify({ ...result, electron: process.versions.electron, chrome: process.versions.chrome, node: process.versions.node, platform: process.platform, arch: process.arch }, null, 2), "utf-8");
  console.log(`[probe] 报告已写入 ${PROBE_REPORT}`);
  sidecar.stop();
  app.quit();
});

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

/**
 * 设置窗口底色，供渲染进程在切主题时调用。
 *
 * 为什么由渲染进程发起：主题存在渲染进程的 localStorage 里，主进程读不到；
 * 而 `backgroundColor` 是主进程侧属性，渲染进程改不了。窗口缩放时新暴露的区域
 * 在页面重绘前显示的就是这个底色——不同步就会在深色主题下闪一条白边。
 *
 * 只接受 `#rrggbb`：这是从渲染进程过来的值，不能当成可信输入直接塞给 Electron。
 */
ipcMain.handle("window:background", (_event, color: unknown) => {
  if (typeof color !== "string" || !/^#[0-9a-f]{6}$/i.test(color)) {
    return { ok: false, error: "颜色必须是 #rrggbb" };
  }
  window?.setBackgroundColor(color);
  return { ok: true, color };
});

/**
 * 保存一段文本到用户选定的路径。
 *
 * 存在的理由：渲染页面是 `loadFile` 加载的（origin 为 `file://`），
 * `blob:file://…` 的 `<a download>` 在这个 origin 下不会落盘——无异常、无控制台报错、
 * 也没有文件。打包版同样走 loadFile，所以必须由主进程来写。
 *
 * 路径**只能**来自原生对话框：渲染进程不能传路径，否则这个桥就等于任意文件写入。
 * 截图验收模式下没有人在对话框前，所以返回 canceled 让调用方如实显示"未保存"，
 * 而不是假装成功。
 */
ipcMain.handle("file:save-text", async (_event, payload: { name?: string; text?: string } | undefined) => {
  const name = payload?.name || "export.txt";
  const text = payload?.text ?? "";

  // 截图验收模式下没人能点原生对话框，所以给一个**只在该模式下生效**的落盘路径。
  // 刻意不无条件读环境变量：那等于给生产路径开一个"环境变量决定写哪"的任意写入口子。
  const verifyPath = CAPTURE_TO ? process.env.PD_SAVE_PATH : undefined;
  if (verifyPath) {
    try {
      writeFileSync(verifyPath, text, "utf-8");
      return { ok: true, path: verifyPath };
    } catch (err) {
      return { ok: false, error: (err as Error).message };
    }
  }

  const options = {
    defaultPath: name,
    filters: [
      { name: "CSV", extensions: ["csv"] },
      { name: "全部文件", extensions: ["*"] },
    ],
  };
  const { canceled, filePath } = window
    ? await dialog.showSaveDialog(window, options)
    : await dialog.showSaveDialog(options);
  if (canceled || !filePath) return { ok: false, canceled: true };
  try {
    writeFileSync(filePath, text, "utf-8");
    return { ok: true, path: filePath };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
});

/**
 * 保存二进制报告（xlsx/docx）到用户选定的路径。与 save-text 同一套约束：
 * 路径只能来自原生对话框，渲染进程只交字节。字节由内核 report.export 生成，
 * 主进程不经内容转换地写出去——报告内容对不对是内核职责，写哪里是这里的职责。
 */
ipcMain.handle(
  "file:save-binary",
  async (_event, payload: { name?: string; bytes?: Uint8Array } | undefined) => {
    const name = payload?.name || "report.bin";
    const bytes = payload?.bytes;
    if (!(bytes instanceof Uint8Array)) return { ok: false, error: "bytes 必须是 Uint8Array" };
    if (bytes.byteLength === 0) return { ok: false, error: "报告字节为空，未写入" };

    // 截图验收模式：对话框没人能点，走环境变量指定的路径（只在 CAPTURE 模式下生效，
    // 与 save-text 同一个口径，不给生产留"环境变量决定写哪"的后门）
    if (CAPTURE_TO && process.env.PD_SAVE_PATH) {
      const to = process.env.PD_SAVE_PATH;
      try {
        writeFileSync(to, Buffer.from(bytes));
        return { ok: true, path: to };
      } catch (err) {
        return { ok: false, error: (err as Error).message };
      }
    }

    const ext = (name.match(/\.([A-Za-z0-9]+)$/) || [])[1]?.toLowerCase();
    const options = {
      defaultPath: name,
      filters: ext ? [{ name: ext.toUpperCase(), extensions: [ext] }, { name: "全部文件", extensions: ["*"] }] : undefined,
    };
    const { canceled, filePath } = window
      ? await dialog.showSaveDialog(window, options)
      : await dialog.showSaveDialog(options);
    if (canceled || !filePath) return { ok: false, canceled: true };
    try {
      writeFileSync(filePath, Buffer.from(bytes));
      return { ok: true, path: filePath };
    } catch (err) {
      return { ok: false, error: (err as Error).message };
    }
  },
);

app.whenReady().then(() => {
  // 先建窗口再起内核：内核起不来时用户看得到"内核已停止"，而不是双击图标没反应
  createWindow(sidecar.endpoints);
  sidecar.start();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(sidecar.endpoints);
  });
});

app.on("window-all-closed", () => {
  sidecar.stop();
  app.quit();
});

// 未捕获异常会静默杀掉守护逻辑，至少留在 stderr 里
process.on("uncaughtException", (err) => console.error("[main] uncaught", err));
process.on("unhandledRejection", (reason) => console.error("[main] unhandled rejection", reason));
