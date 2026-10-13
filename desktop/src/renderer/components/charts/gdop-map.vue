<script setup lang="ts">
/**
 * 定位精度热图（GDOP）+ ColorBar。
 *
 * 只画几何精度，不画"AE 源"：真实声源定位需要多传感器到达时差，而 `ae_hit` 帧不带
 * sensor_id 与到达时刻，内核 `AELocalizer.locate()` 在生产路径上也没有调用点。
 * 这一页能给的是"这套阵列摆法能到什么精度"，不是"这次放电在哪"。少了这一条，
 * 图上那个点就会被读成一次真实定位结果。
 *
 * 色带语义：低 GDOP = 几何条件好（蓝），高 GDOP = 差（红）。**不带单位**——GDOP 是
 * 无量纲的，旧版把它换算成"米"是硬编码假设（见 lib/gdop.ts 顶部说明）。
 */

import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { JET } from "@/renderer/lib/colormap";
import { chartTheme } from "@/renderer/lib/chart-theme";
import { computeGdop, type AeSensor } from "@/renderer/lib/gdop";

const props = defineProps<{ sensors: AeSensor[]; height?: number }>();

const host = ref<HTMLCanvasElement | null>(null);
const info = ref("");
const note = ref("");

const MARGIN = { left: 40, right: 54, top: 12, bottom: 30 };
const BAR_W = 10;

let scratch: HTMLCanvasElement | null = null;
let scratchN = 0;
let image: ImageData | null = null;

function ensureScratch(n: number): CanvasRenderingContext2D | null {
  if (!scratch) scratch = document.createElement("canvas");
  if (scratchN !== n) {
    scratch.width = n;
    scratch.height = n;
    scratchN = n;
    image = null;
  }
  const sctx = scratch.getContext("2d");
  if (!sctx) return null;
  if (!image) image = sctx.createImageData(n, n);
  return sctx;
}

function render(): void {
  const canvas = host.value;
  if (!canvas) return;
  const theme = chartTheme();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
  const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.fillStyle = theme.surface;
  ctx.fillRect(0, 0, w, h);
  ctx.font = theme.tickFont;

  const grid = computeGdop(props.sensors);
  if (!grid) {
    note.value = "至少需要 3 个传感器：2D TDOA 以 1 号为参考，N 个阵元只有 N−1 个方程";
    info.value = "—";
    return;
  }
  note.value = "";
  const { n, bounds, values, min, max } = grid;
  const span = max - min || 1;
  info.value = `最优 GDOP ${grid.best ? grid.best.gdop.toFixed(2) : "—"} · 最差 ${max.toFixed(2)} · 网格 ${n}×${n}`;

  const plotX = MARGIN.left;
  const plotY = MARGIN.top;
  const plotW = Math.max(1, w - MARGIN.left - MARGIN.right - BAR_W - 16);
  const plotH = Math.max(1, h - MARGIN.top - MARGIN.bottom);

  // ── 热图本体 ───────────────────────────────────────
  const sctx = ensureScratch(n);
  if (sctx && image) {
    const px = image.data;
    for (let iy = 0; iy < n; iy += 1) {
      // 画布 y 向下、阵列 y 向上，所以行要翻转
      const row = n - 1 - iy;
      for (let ix = 0; ix < n; ix += 1) {
        const g = values[iy * n + ix] ?? Number.NaN;
        const dst = (row * n + ix) * 4;
        if (!Number.isFinite(g)) {
          // 退化格点画透明：涂成色带端点会把它读成"最好/最差"
          px[dst + 3] = 0;
          continue;
        }
        const src = Math.min(255, Math.max(0, Math.round(((g - min) / span) * 255))) * 4;
        px[dst] = JET[src] ?? 0;
        px[dst + 1] = JET[src + 1] ?? 0;
        px[dst + 2] = JET[src + 2] ?? 0;
        px[dst + 3] = 255;
      }
    }
    sctx.putImageData(image, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(scratch as HTMLCanvasElement, 0, 0, n, n, plotX, plotY, plotW, plotH);
  }

  const toPx = (x: number): number =>
    plotX + ((x - bounds.xMin) / (bounds.xMax - bounds.xMin)) * plotW;
  const toPy = (y: number): number =>
    plotY + (1 - (y - bounds.yMin) / (bounds.yMax - bounds.yMin)) * plotH;

  // ── 传感器与最优区 ─────────────────────────────────
  ctx.strokeStyle = theme.border;
  ctx.strokeRect(plotX + 0.5, plotY + 0.5, plotW, plotH);
  for (const s of props.sensors) {
    // 编辑中的空输入会给出 NaN 坐标，画上去是 no-op 但标签会飘；直接跳过
    if (!Number.isFinite(s.x) || !Number.isFinite(s.y)) continue;
    const cx = toPx(s.x);
    const cy = toPy(s.y);
    ctx.beginPath();
    ctx.arc(cx, cy, 4 * dpr, 0, Math.PI * 2);
    ctx.fillStyle = theme.fg;
    ctx.fill();
    ctx.fillStyle = theme.surface;
    ctx.beginPath();
    ctx.arc(cx, cy, 1.8 * dpr, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = theme.muted;
    ctx.fillText(s.id, cx + 6 * dpr, cy - 5 * dpr);
  }
  if (grid.best) {
    const bx = toPx(grid.best.x);
    const by = toPy(grid.best.y);
    ctx.strokeStyle = theme.success;
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath();
    ctx.arc(bx, by, 6 * dpr, 0, Math.PI * 2);
    ctx.stroke();
    ctx.lineWidth = 1;
  }

  // ── 坐标轴（米） ───────────────────────────────────
  ctx.fillStyle = theme.muted;
  const xticks = 3;
  for (let i = 0; i < xticks; i += 1) {
    const x = bounds.xMin + ((bounds.xMax - bounds.xMin) * i) / (xticks - 1);
    ctx.fillText(`${x.toFixed(2)}`, toPx(x) - 12 * dpr, plotY + plotH + 14 * dpr);
  }
  for (let i = 0; i < 3; i += 1) {
    const y = bounds.yMin + ((bounds.yMax - bounds.yMin) * i) / 2;
    ctx.fillText(`${y.toFixed(2)}`, 4 * dpr, toPy(y) + 3 * dpr);
  }

  // ── ColorBar（竖直，右侧） ─────────────────────────
  const barX = plotX + plotW + 14 * dpr;
  const steps = 48;
  const stepH = plotH / steps;
  for (let i = 0; i < steps; i += 1) {
    // 顶部放最大值：与热图"上=大"的直觉一致
    const t = 1 - i / (steps - 1);
    const src = Math.min(255, Math.round(t * 255)) * 4;
    ctx.fillStyle = `rgb(${JET[src] ?? 0},${JET[src + 1] ?? 0},${JET[src + 2] ?? 0})`;
    ctx.fillRect(barX, plotY + i * stepH, BAR_W * dpr, stepH + 1);
  }
  ctx.fillStyle = theme.muted;
  ctx.fillText(max.toFixed(2), barX, plotY - 2 * dpr);
  ctx.fillText(min.toFixed(2), barX, plotY + plotH + 11 * dpr);
}

let observer: ResizeObserver | null = null;

onMounted(() => {
  render();
  // 容器尺寸变化（窗口缩放、侧栏折叠）都要重画：canvas 的像素尺寸是按 clientWidth 定的
  if (typeof ResizeObserver !== "undefined" && host.value) {
    observer = new ResizeObserver(() => render());
    observer.observe(host.value);
  }
});

onBeforeUnmount(() => observer?.disconnect());

watch(
  () => props.sensors,
  () => render(),
  { deep: true },
);
</script>

<template>
  <div class="flex flex-col">
    <div class="flex items-center justify-between gap-2 pb-1.5">
      <span class="text-[13px] font-medium">定位精度热图（GDOP）</span>
      <span class="text-[11px] text-app-muted">{{ info }}</span>
    </div>
    <canvas ref="host" class="block w-full" :style="{ height: `${height ?? 260}px` }" />
    <p v-if="note" class="text-[11px] text-app-warning">{{ note }}</p>
    <p class="text-[11px] text-app-muted">
      只反映阵列几何（无量纲 GDOP，越小越好），不含任何"米"的换算——真实声源定位需要多传感器到达时差，
      而 ae_hit 帧不带 sensor_id 与到达时刻，内核侧也没有调用点。所以这里不画"AE 源"。
    </p>
  </div>
</template>
