<script setup lang="ts">
/**
 * PRPD 散点图（历史事件版）。
 *
 * 与 monitor 页的 `prpd-chart.vue` 刻意分开：那个面板画的是**内核实时算好的热图矩阵**
 * （360×256 量化格子），数据源是 `prpd` 帧；这里画的是**逐条事件**，纵轴是带符号幅值。
 *
 * ## 纵轴符号必须由 polarity 决定，不能拿 amplitude 的符号
 *
 * 内核口径（c7f1aaf 确立）：`detect_pd_events` 返回的 `amplitude` 是**幅值大小**，
 * 负极性也返回正数；极性只在 `polarity` 字段里（0=正、1=负）。所以直接拿 amplitude
 * 当纵轴会把**所有**事件画进正半周——双极性放电源看起来就是单极性，正是那次修复
 * 警告过的"容易被读成放电不对称"。这里用 `polarity === 1 ? -|A| : +|A|`。
 *
 * polarity 既不是 0 也不是 1 的事件（例如库里 NULL 的历史行）**跳过并计数**：
 * 没有极性就没法定符号，硬画到正半周等于替数据下结论。
 */

import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { chartTheme } from "@/renderer/lib/chart-theme";

const props = defineProps<{
  /** 逐条历史事件；phase 可空（库里允许 null），amplitude 带符号 */
  events: { phase: number | null; amplitude: number; polarity: number | null }[];
  height?: number;
  /** 空态时补一句数据源实况：区分"没收到帧"与"帧里没事件"，两者的排查方向完全不同 */
  sourceHint?: string;
}>();

const host = ref<HTMLCanvasElement | null>(null);
const info = ref("");

const MARGIN = { left: 46, right: 12, top: 12, bottom: 30 };

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

  const plotX = MARGIN.left;
  const plotY = MARGIN.top;
  const plotW = Math.max(1, w - MARGIN.left - MARGIN.right);
  const plotH = Math.max(1, h - MARGIN.top - MARGIN.bottom);

  // 相位有效、且极性可判定（0=正 / 1=负）的点才画：没有极性就定不了纵轴符号
  const pts = props.events.filter(
    (e) =>
      typeof e.phase === "number" &&
      Number.isFinite(e.phase) &&
      (e.polarity === 0 || e.polarity === 1),
  );
  const skipped = props.events.length - pts.length;
  if (pts.length === 0) {
    // 带上数据源实况：只写"没有事件"会把"没收到帧"和"帧里没事件"混成一件事
    const why = skipped > 0 ? ` · ${skipped} 条缺极性无法定位` : "";
    info.value = `所选范围内没有事件${why}${props.sourceHint ? ` · ${props.sourceHint}` : ""}`;
    ctx.strokeStyle = theme.border;
    ctx.strokeRect(plotX + 0.5, plotY + 0.5, plotW, plotH);
    return;
  }

  /** 纵轴符号来自 polarity，幅值只取大小（内核口径见文件头） */
  const signedOf = (e: { amplitude: number; polarity: number | null }): number =>
    e.polarity === 1 ? -Math.abs(e.amplitude) : Math.abs(e.amplitude);

  let ampMax = 0;
  for (const e of pts) ampMax = Math.max(ampMax, Math.abs(signedOf(e)));
  if (ampMax <= 0) ampMax = 1;
  info.value = `${pts.length} 个事件 · 幅值轴 ±${ampMax.toFixed(1)}${skipped > 0 ? ` · 跳过 ${skipped} 条缺极性` : ""}`;

  const toX = (phase: number): number => plotX + (Math.max(0, Math.min(360, phase)) / 360) * plotW;
  // 幅值轴以 0 为中心：PRPD 的正负半周必须对称才读得出极性
  const toY = (amp: number): number => plotY + (1 - (amp / ampMax + 1) / 2) * plotH;

  // 网格与零线
  ctx.strokeStyle = theme.grid;
  ctx.beginPath();
  for (let p = 0; p <= 360; p += 45) {
    const x = Math.round(toX(p)) + 0.5;
    ctx.moveTo(x, plotY);
    ctx.lineTo(x, plotY + plotH);
  }
  ctx.moveTo(plotX, Math.round(toY(0)) + 0.5);
  ctx.lineTo(plotX + plotW, Math.round(toY(0)) + 0.5);
  ctx.stroke();

  // 散点：正极性用 error 色、负极性用 info 色，与旧版 PRPD 的红/蓝口径一致
  for (const e of pts) {
    ctx.fillStyle = e.polarity === 1 ? theme.info : theme.error;
    ctx.fillRect(toX(e.phase ?? 0) - 1, toY(signedOf(e)) - 1, 2, 2);
  }

  // 轴标签
  ctx.fillStyle = theme.muted;
  ctx.fillText(`+${ampMax.toFixed(1)}`, 4 * dpr, plotY + 8 * dpr);
  ctx.fillText("0", 4 * dpr, toY(0) + 3 * dpr);
  ctx.fillText(`-${ampMax.toFixed(1)}`, 4 * dpr, plotY + plotH);
  for (let p = 0; p <= 360; p += 90) {
    ctx.fillText(`${p}°`, toX(p) - 8 * dpr, plotY + plotH + 14 * dpr);
  }
  ctx.strokeStyle = theme.border;
  ctx.strokeRect(plotX + 0.5, plotY + 0.5, plotW, plotH);
}

let observer: ResizeObserver | null = null;

onMounted(() => {
  render();
  if (typeof ResizeObserver !== "undefined" && host.value) {
    observer = new ResizeObserver(() => render());
    observer.observe(host.value);
  }
});

onBeforeUnmount(() => observer?.disconnect());

watch(
  () => props.events,
  () => render(),
);
</script>

<template>
  <div class="flex flex-col">
    <div class="flex items-center justify-between gap-2 pb-1.5">
      <span class="text-[13px] font-medium">PRPD 散点图</span>
      <span class="text-[11px] text-app-muted">{{ info }}</span>
    </div>
    <canvas ref="host" class="block w-full" :style="{ height: `${height ?? 280}px` }" />
    <p class="text-[11px] text-app-muted">
      相位角（°）× 带符号幅值：符号由 <code>polarity</code> 决定（0=正、1=负），幅值只取大小
      ——内核的 <code>amplitude</code> 对负极性也返回正数，拿它的符号当纵轴会把双极性源画成单极性。
      红色为正极性、蓝色为负极性；幅值单位随现场标定，未标定前不写量纲。
    </p>
  </div>
</template>
