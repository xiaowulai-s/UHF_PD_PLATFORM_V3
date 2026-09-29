<script setup lang="ts">
/**
 * PRPD 图谱面板（M4.3）。
 *
 * 三种视图：散点图用帧里的原始事件点，热力图/密度图画 360×256 计数矩阵。
 * 热力图与密度图的差别**在内核**：两者共用同一份计数矩阵，密度是 compute 时多过一遍
 * gaussian_filter，所以切它们要发 RPC，不能只改前端状态——否则就是一个按了没反应的按钮。
 * 帧自带 meta.mode，用它核对开关是否真的生效。
 *
 * 旧版 ui/widgets/prpd_widget.py 的两处已确认问题没有照抄：
 *  - set_colormap 调用点为 0，所以这里只实现默认 jet；
 *  - 点击读数按 [amp_idx, phase_idx] 索引未转置的矩阵（显示时才是 .T），
 *    读到的是错的格子；这里矩阵按 (phase, amplitude) 行主序，索引为 [phase][amplitude]。
 */

import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { call } from "@/renderer/lib/store";
import { chartTheme } from "@/renderer/lib/chart-theme";
import { useFrameDraw } from "@/renderer/lib/use-frame-draw";
import { decodeCells, type Frame, type PrpdMeta } from "@/shared/protocol";

type View = "scatter" | "heatmap" | "density";

const view = ref<View>("heatmap");
const info = shallowRef("—");
const kernelMode = shallowRef("");
const truncated = shallowRef(0);
const host = ref<HTMLCanvasElement | null>(null);

let ctx: CanvasRenderingContext2D | null = null;
let observer: ResizeObserver | null = null;
/** 360×256 的矩阵着色结果，复用同一张离屏图避免每帧重新分配 */
let cellCanvas: HTMLCanvasElement | null = null;
let cellData: ImageData | null = null;
let cells: Uint8Array | null = null;
let shape: [number, number] = [360, 256];
let maxAmp = 100;
let phaseMax = 360;
let events: PrpdMeta["events"] = [];

/** jet 颜色表：与旧版 pyqtgraph 用的 matplotlib jet 同一条分段线性定义 */
const JET = (() => {
  const lut = new Uint8ClampedArray(256 * 4);
  const f = (t: number, offset: number) => Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - offset)));
  for (let i = 0; i < 256; i += 1) {
    const t = i / 255;
    lut[i * 4] = Math.round(f(t, 3.5) * 255);
    lut[i * 4 + 1] = Math.round(f(t, 2) * 255);
    lut[i * 4 + 2] = Math.round(f(t, 0.5) * 255);
    lut[i * 4 + 3] = 255;
  }
  return lut;
})();

const MARGIN = { left: 52, right: 12, top: 10, bottom: 34 };

const showHeatmap = computed(() => view.value !== "scatter");

async function switchView(next: View): Promise<void> {
  view.value = next;
  if (next === "scatter") return;
  // 热力/密度必须让内核换算法；失败要说出来，否则界面看起来"切成功了"但矩阵没变
  try {
    const r = await call<{ mode: string }>("config.set_prpd_mode", { mode: next });
    kernelMode.value = r.mode;
  } catch (err) {
    info.value = `切换 ${next} 失败：${(err as Error).message}`;
  }
}

function paintCells(): void {
  if (!cells || !cellData || !cellCanvas) return;
  const [phaseBins, ampBins] = shape;
  const px = cellData.data;
  for (let y = 0; y < ampBins; y += 1) {
    // 画布 y=0 在顶部，而幅值轴向上增大，所以要上下翻转
    const ampIdx = ampBins - 1 - y;
    for (let x = 0; x < phaseBins; x += 1) {
      const v = cells[x * ampBins + ampIdx] ?? 0;
      const dst = (y * phaseBins + x) * 4;
      if (v === 0) {
        // 零计数画成透明。jet(0) 是饱和蓝，而 92160 格里通常 99% 以上为零，
        // 全涂蓝会让"无事件"和"低计数"同色、真实聚集区反而读不出来。
        // 刻意偏离旧版 pyqtgraph ImageItem(autoLevels) 的观感。
        px[dst + 3] = 0;
        continue;
      }
      const src = v * 4;
      px[dst] = JET[src] ?? 0;
      px[dst + 1] = JET[src + 1] ?? 0;
      px[dst + 2] = JET[src + 2] ?? 0;
      px[dst + 3] = 255;
    }
  }
  const cctx = cellCanvas.getContext("2d");
  cctx?.putImageData(cellData, 0, 0);
}

function draw(): void {
  const canvas = host.value;
  if (!ctx || !canvas) return;
  const t = chartTheme();
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.width / dpr;
  const h = canvas.height / dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const plotW = w - MARGIN.left - MARGIN.right;
  const plotH = h - MARGIN.top - MARGIN.bottom;
  if (plotW <= 0 || plotH <= 0) return;

  // 先铺底再画网格最后画数据：零格现在透明，若顺序反了会被网格线压住
  ctx.fillStyle = t.surface;
  ctx.fillRect(MARGIN.left, MARGIN.top, plotW, plotH);

  ctx.strokeStyle = t.grid;
  ctx.fillStyle = t.muted;
  ctx.font = t.tickFont;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let p = 0; p <= phaseMax; p += 45) {
    const x = Math.round(MARGIN.left + (p / phaseMax) * plotW) + 0.5;
    ctx.moveTo(x, MARGIN.top);
    ctx.lineTo(x, MARGIN.top + plotH);
  }
  for (let i = 0; i <= 5; i += 1) {
    const y = Math.round(MARGIN.top + (i / 5) * plotH) + 0.5;
    ctx.moveTo(MARGIN.left, y);
    ctx.lineTo(MARGIN.left + plotW, y);
  }
  ctx.stroke();

  if (showHeatmap.value && cellCanvas) {
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(cellCanvas, MARGIN.left, MARGIN.top, plotW, plotH);
  } else if (view.value === "scatter") {
    for (const e of events) {
      const x = MARGIN.left + (e.phase / phaseMax) * plotW;
      const y = MARGIN.top + (1 - e.amplitude / maxAmp) * plotH;
      ctx.fillStyle = e.polarity ? t.warning : t.primary;
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  ctx.textAlign = "center";
  for (let p = 0; p <= phaseMax; p += 90) {
    ctx.fillText(String(p), MARGIN.left + (p / phaseMax) * plotW, h - MARGIN.bottom + 14);
  }
  ctx.fillText("相位 (°)", MARGIN.left + plotW / 2, h - 4);

  ctx.textAlign = "right";
  for (let i = 0; i <= 5; i += 1) {
    const v = maxAmp * (1 - i / 5);
    ctx.fillText(v.toFixed(0), MARGIN.left - 5, MARGIN.top + (i / 5) * plotH + 3);
  }
  ctx.save();
  ctx.translate(11, MARGIN.top + plotH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.fillText("幅值 (mV)", 0, 0);
  ctx.restore();
}

async function onFrame(frame: Frame): Promise<void> {
  const meta = frame.m as unknown as PrpdMeta;
  const decoded = await decodeCells(frame);
  const [rows = 360, cols = 256] = decoded.shape;
  // 纵轴必须用矩阵自己的分箱轴上限，不是"观测到的最大幅值"：后者通常只有前者的
  // 几分之一，拿它铺矩阵会把事件带压到贴底（实测电晕被压 4 倍）。
  maxAmp = meta.amp_axis_max ?? (meta.max_amplitude > 0 ? meta.max_amplitude : 100);
  phaseMax = meta.phase_axis_max && meta.phase_axis_max > 0 ? meta.phase_axis_max : 360;
  events = meta.events ?? [];
  truncated.value = meta.events_truncated ?? 0;
  kernelMode.value = meta.mode ?? "";

  if (decoded.cells instanceof Uint8Array) {
    cells = decoded.cells;
    shape = [rows, cols];
    if (!cellCanvas) {
      cellCanvas = document.createElement("canvas");
      cellCanvas.width = rows;
      cellCanvas.height = cols;
    }
    if (cellCanvas.width !== rows || cellCanvas.height !== cols) {
      cellCanvas.width = rows;
      cellCanvas.height = cols;
      cellData = null;
    }
    if (!cellData) cellData = new ImageData(rows, cols);
    paintCells();
  }

  const total = meta.total_events ?? 0;
  const shown = view.value === "scatter" ? `${events.length} 点` : `${rows}×${cols}`;
  info.value = `${shown} | 事件 ${total}${truncated.value ? ` (散点截断于 ${truncated.value})` : ""} | 内核 ${kernelMode.value || "?"}`;
  draw();
}

useFrameDraw("prpd", onFrame);

function readCell(ev: MouseEvent): void {
  const canvas = host.value;
  if (!canvas || !cells || view.value === "scatter") return;
  const rect = canvas.getBoundingClientRect();
  const [phaseBins, ampBins] = shape;
  const plotW = rect.width - MARGIN.left - MARGIN.right;
  const plotH = rect.height - MARGIN.top - MARGIN.bottom;
  const px = ev.clientX - rect.left - MARGIN.left;
  const py = ev.clientY - rect.top - MARGIN.top;
  if (px < 0 || py < 0 || px > plotW || py > plotH) return;
  const phaseIdx = Math.min(phaseBins - 1, Math.floor((px / plotW) * phaseBins));
  const ampIdx = Math.min(ampBins - 1, Math.floor((1 - py / plotH) * ampBins));
  const count = cells[phaseIdx * ampBins + ampIdx] ?? 0;
  info.value = `相位 ${((phaseIdx / phaseBins) * phaseMax).toFixed(1)}° | 幅值 ${((ampIdx / ampBins) * maxAmp).toFixed(1)} mV | 计数 ${count}`;
  draw();
}

const VIEWS: { key: View; label: string }[] = [
  { key: "scatter", label: "散点图" },
  { key: "heatmap", label: "热力图" },
  { key: "density", label: "密度图" },
];

/** 画布尺寸只改 backing store 并重绘；DPR 下不重设 width/height 会让图像被拉伸模糊 */
function resize(): void {
  const canvas = host.value;
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
  const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  draw();
}

onMounted(() => {
  const canvas = host.value;
  if (!canvas) return;
  ctx = canvas.getContext("2d");
  observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();
});

onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-1">
    <div class="flex items-center justify-between gap-2 text-[11px]">
      <div class="flex items-center gap-1.5">
        <button
          v-for="m in VIEWS"
          :key="m.key"
          class="rounded border px-2 py-0.5"
          :class="view === m.key ? 'border-app-primary text-app-primary' : 'border-app-border text-app-muted'"
          @click="switchView(m.key)"
        >
          {{ m.label }}
        </button>
        <span class="tabular text-app-muted">{{ info }}</span>
      </div>
    </div>
    <div class="relative min-h-0 flex-1">
      <canvas ref="host" class="absolute inset-0 h-full w-full" @click="readCell" />
    </div>
  </div>
</template>
