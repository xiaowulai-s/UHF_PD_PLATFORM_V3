<script setup lang="ts">
/**
 * 仪表板用的"三类快照"卡片：波形 / FFT / PRPD。
 *
 * 与 monitor 页的面板是两套东西，刻意不共用：那边要游标、框选、缩放与 rAF 循环，
 * 而这里的验收线是"20 FPS 下 CPU < 15%"。所以节奏是 500ms 一次定时重绘，只取
 * 每个 topic 的**最新一帧**（有损 topic 的语义本来就是最新帧胜出），不做任何 RPC。
 *
 * 用 setInterval 而不是 rAF：rAF 在窗口被遮挡时整条停摆，正好符合"不画"的期望，
 * 但它也意味着快照在后台永远停在最后一帧——对快照是合理的。这里额外显式判
 * document.hidden，是因为 setInterval 不会自己停。
 */

import { onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { latest } from "@/renderer/lib/store";
import { chartTheme } from "@/renderer/lib/chart-theme";
import {
  drawFftSnapshot,
  drawPrpdSnapshot,
  drawWaveformSnapshot,
  type SnapshotResult,
} from "@/renderer/lib/snapshot-draw";

const props = withDefaults(
  defineProps<{ topic: "waveform" | "fft" | "prpd"; title: string; height?: number }>(),
  { height: 128 },
);

/** 500ms 是"看得出在动"与"几乎不耗 CPU"之间的折中；旧版快照也是远低于入帧率的节奏 */
const SNAPSHOT_MS = 500;

const host = ref<HTMLCanvasElement | null>(null);
const caption = shallowRef("等待数据…");
const failed = shallowRef("");
let timer = 0;
/** 解码是异步的（PRPD 走 DecompressionStream），上一次没画完就跳过本轮 */
let busy = false;

function resize(): void {
  const canvas = host.value;
  if (!canvas) return;
  // 按 devicePixelRatio 开画布：CSS 像素尺寸直接当画布尺寸会让曲线发虚
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
  const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
}

async function tick(): Promise<void> {
  if (document.hidden || busy) return;
  const canvas = host.value;
  const frame = latest.value[props.topic];
  if (!canvas || !frame) return;
  busy = true;
  try {
    resize();
    const theme = chartTheme();
    let result: SnapshotResult;
    if (props.topic === "prpd") result = await drawPrpdSnapshot(canvas, frame, theme);
    else if (props.topic === "fft") result = drawFftSnapshot(canvas, frame, theme);
    else result = drawWaveformSnapshot(canvas, frame, theme);
    caption.value = result.caption;
    failed.value = "";
  } catch (err) {
    // 单帧画崩不能把定时器带走；把原因显示出来，别让快照静默留白
    failed.value = (err as Error).message;
  } finally {
    busy = false;
  }
}

onMounted(() => {
  resize();
  timer = window.setInterval(() => void tick(), SNAPSHOT_MS);
});

onBeforeUnmount(() => window.clearInterval(timer));
</script>

<template>
  <div class="flex flex-col overflow-hidden rounded-lg border border-app-border bg-app-surface">
    <div class="flex items-center justify-between gap-2 border-b border-app-border px-3 py-1.5">
      <span class="shrink-0 text-[13px] font-medium">{{ title }}</span>
      <span class="truncate text-[11px]" :class="failed ? 'text-app-error' : 'text-app-muted'">
        {{ failed || caption }}
      </span>
    </div>
    <canvas ref="host" class="block w-full" :style="{ height: `${height}px` }" />
  </div>
</template>
