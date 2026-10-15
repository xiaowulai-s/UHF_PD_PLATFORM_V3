<script setup lang="ts">
/**
 * PRPD 图谱面板（M4.3）。
 *
 * 四种视图：散点图用帧里的原始事件点；热力图/密度图画 360×256 计数矩阵；
 * 三维（`prpd-3d.vue`）用同一份矩阵，轴是 相位 × 幅值 × 强度（计数，取对数）。
 * 热力图与密度图的差别**在内核**：两者共用同一份计数矩阵，密度是 compute 时多过一遍
 * gaussian_filter，所以切它们要发 RPC，不能只改前端状态——否则就是一个按了没反应的按钮。
 * 帧自带 meta.mode，用它核对开关是否真的生效。散点与三维是纯前端，不发 RPC。
 *
 * 旧版 ui/widgets/prpd_widget.py 的两处已确认问题没有照抄：
 *  - set_colormap 调用点为 0，所以这里只实现默认 jet；
 *  - 点击读数按 [amp_idx, phase_idx] 索引未转置的矩阵（显示时才是 .T），
 *    读到的是错的格子；这里矩阵按 (phase, amplitude) 行主序，索引为 [phase][amplitude]。
 */

import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { call, dischargeTypeFor } from "@/renderer/lib/store";
import { chartTheme } from "@/renderer/lib/chart-theme";
import { useFrameDraw } from "@/renderer/lib/use-frame-draw";
import { decodeCells, type Frame, type PrpdMeta } from "@/shared/protocol";
import { paintPrpdCells } from "@/renderer/lib/colormap";
import Prpd3D from "@/renderer/components/charts/prpd-3d.vue";

type View = "scatter" | "heatmap" | "density" | "3d";

const view = ref<View>("heatmap");
const info = shallowRef("—");
const kernelMode = shallowRef("");
const truncated = shallowRef(0);
const host = ref<HTMLCanvasElement | null>(null);

/**
 * 放电类型标签按 (device, channel) 取，跟着本面板正在画的那一帧走。
 *
 * 类型判定由内核在 PRPD 结果上跑分类器得出（`discharge_type` 可靠帧），
 * 节奏与 PRPD 帧同步（单通道约 0.33Hz），所以这里不需要轮询。
 */
const device = shallowRef("");
const channel = ref(-1);
const typeInfo = computed(() => dischargeTypeFor(device.value, channel.value));
const typeClass = computed(() => {
  const t = typeInfo.value;
  if (!t || t.type === "unknown") return "bg-app-surface-variant text-app-muted";
  if (t.type === "noise") return "bg-app-warning/15 text-app-warning";
  return "bg-app-primary/15 text-app-primary";
});

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

const MARGIN = { left: 52, right: 12, top: 10, bottom: 34 };

/** 只有散点与三维不走计数矩阵：散点用帧里的原始事件，三维用矩阵但由 WebGL 画 */
const showHeatmap = computed(() => view.value !== "scatter" && view.value !== "3d");

async function switchView(next: View): Promise<void> {
  view.value = next;
  // 散点是纯前端（用帧里的 events），三维也是纯前端（同一份矩阵交给 WebGL）——
  // 两者都不该发 config.set_prpd_mode，否则会白白把内核的算法切走。
  if (next === "scatter" || next === "3d") return;
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
  // 铺格子的朝向约定与零值透明都在 paintPrpdCells 里，与仪表板快照共用一份实现
  paintPrpdCells(cellData, cells, shape);
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
  device.value = frame.d;
  channel.value = frame.c;
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
  if (!canvas || !cells || view.value === "scatter" || view.value === "3d") return;
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
  { key: "3d", label: "三维" },
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
      <span
        v-if="typeInfo"
        class="shrink-0 rounded px-1.5 py-0.5"
        :class="typeClass"
        :title="typeInfo.description"
        data-role="discharge-type"
      >
        放电类型 {{ typeInfo.name }} · {{ (typeInfo.confidence * 100).toFixed(0) }}%
      </span>
    </div>
    <div class="relative min-h-0 flex-1">
      <!-- 用 v-show 而不是 v-if：切回 2D 时不重建 canvas（重建后 onMounted 不会再跑，
           ResizeObserver 与离屏图都得重新初始化）。隐藏期间尺寸为 0，ResizeObserver
           会在重新可见时再触发一次，resize() 里对 0 有兜底。 -->
      <canvas v-show="view !== '3d'" ref="host" class="absolute inset-0 h-full w-full" @click="readCell" />
      <Prpd3D v-if="view === '3d'" />
    </div>
  </div>
</template>
