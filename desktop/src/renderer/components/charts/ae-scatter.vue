<script setup lang="ts">
/**
 * AE 特征散点图（canvas 2D）。
 *
 * 三组维度对沿用旧 `ui/widgets/ae_scatter_widget.py` 的 DIMENSIONS，不自己发明：
 * 持续-幅值 / 频率-幅值 / 能量-计数。着色同样沿用旧版"按幅值三级"的做法
 * （success / warning / error 令牌），它表达的是严重度直觉——幅值越高越需要看。
 *
 * 与实时面板的区别：这里的点是**数据库历史查询的结果**，一次给全、不滚动刷新，
 * 所以不用 rAF 循环，props 变了重画一次即可。旧版把记录数上限设成 2000，这里由
 * 调用方按查询 limit 控制，组件不静默截断。
 */

import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { chartTheme } from "@/renderer/lib/chart-theme";

const props = withDefaults(
  defineProps<{
    points: { x: number; y: number; amplitude: number }[];
    xLabel: string;
    yLabel: string;
    height?: number;
  }>(),
  { height: 280 },
);

const host = ref<HTMLCanvasElement | null>(null);
let observer: ResizeObserver | null = null;

const MARGIN = { left: 58, right: 14, top: 12, bottom: 36 };

/** 取一个"好看"的轴上界：1/2/5×10ⁿ 中第一个不小于数据的值 */
function niceMax(values: number[]): number {
  const m = values.reduce((a, b) => (Number.isFinite(b) ? Math.max(a, b) : a), 0);
  if (!Number.isFinite(m) || m <= 0) return 1;
  const base = 10 ** Math.floor(Math.log10(m));
  const n = m / base;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * base;
}

function tickText(v: number): string {
  if (v === 0) return "0";
  const abs = Math.abs(v);
  if (abs >= 1e5 || abs < 1e-3) return v.toExponential(1);
  return abs >= 100 ? v.toFixed(0) : v.toFixed(abs >= 1 ? 1 : 3);
}

function draw(): void {
  const canvas = host.value;
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
  const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const theme = chartTheme();

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  const W = canvas.clientWidth;
  const H = canvas.clientHeight;
  const plotW = Math.max(1, W - MARGIN.left - MARGIN.right);
  const plotH = Math.max(1, H - MARGIN.top - MARGIN.bottom);

  // 只画有限值：内核允许特征量缺失（None → null），NaN 会把轴上界污染成 NaN
  const pts = props.points.filter(
    (p) => Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.amplitude),
  );
  const xMax = niceMax(pts.map((p) => p.x));
  const yMax = niceMax(pts.map((p) => p.y));
  const maxAmp = pts.reduce((a, p) => Math.max(a, p.amplitude), 0);

  // 网格与刻度
  ctx.font = theme.tickFont;
  ctx.fillStyle = theme.muted;
  ctx.strokeStyle = theme.grid;
  ctx.lineWidth = 1;
  const TICKS = 5;
  for (let i = 0; i <= TICKS; i += 1) {
    const gx = MARGIN.left + (plotW * i) / TICKS;
    const gy = MARGIN.top + plotH - (plotH * i) / TICKS;
    ctx.beginPath();
    ctx.moveTo(gx, MARGIN.top);
    ctx.lineTo(gx, MARGIN.top + plotH);
    ctx.moveTo(MARGIN.left, gy);
    ctx.lineTo(MARGIN.left + plotW, gy);
    ctx.stroke();

    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(tickText((xMax * i) / TICKS), gx, MARGIN.top + plotH + 5);
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(tickText((yMax * i) / TICKS), MARGIN.left - 5, gy);
  }

  // 轴标题
  ctx.fillStyle = theme.fg;
  ctx.font = theme.axisFont;
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.fillText(props.xLabel, MARGIN.left + plotW / 2, H - 2);
  ctx.save();
  ctx.translate(12, MARGIN.top + plotH / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textBaseline = "top";
  ctx.fillText(props.yLabel, 0, 0);
  ctx.restore();

  if (!pts.length) {
    ctx.fillStyle = theme.muted;
    ctx.font = theme.axisFont;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("所选范围内没有可绘制的命中（特征量为空的行不参与绘制）", MARGIN.left + plotW / 2, MARGIN.top + plotH / 2);
    return;
  }

  // 幅值三级着色，阈值与旧 AEScatterWidget.AMP_COLORS 一致（0.3 / 0.7 相对最大值）
  for (const p of pts) {
    const ratio = maxAmp > 0 ? p.amplitude / maxAmp : 0;
    ctx.fillStyle = ratio < 0.3 ? theme.success : ratio < 0.7 ? theme.warning : theme.error;
    const px = MARGIN.left + (p.x / xMax) * plotW;
    const py = MARGIN.top + plotH - (p.y / yMax) * plotH;
    ctx.beginPath();
    ctx.arc(px, py, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

watch(
  () => [props.points, props.xLabel, props.yLabel],
  () => draw(),
);

onMounted(() => {
  observer = new ResizeObserver(() => draw());
  if (host.value) observer.observe(host.value);
  draw();
});

onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <div class="flex flex-col">
    <canvas ref="host" class="block w-full" :style="{ height: `${height}px` }" />
    <div class="flex items-center justify-center gap-3 text-[10px] text-app-muted">
      <span class="flex items-center gap-1"><i class="inline-block h-2 w-2 rounded-full bg-app-success" />幅值低</span>
      <span class="flex items-center gap-1"><i class="inline-block h-2 w-2 rounded-full bg-app-warning" />幅值中</span>
      <span class="flex items-center gap-1"><i class="inline-block h-2 w-2 rounded-full bg-app-error" />幅值高</span>
      <span>· 分级按本次查询内的最大幅值，非绝对阈值</span>
    </div>
  </div>
</template>
