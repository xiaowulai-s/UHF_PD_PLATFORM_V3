<script setup lang="ts">
/**
 * 时域波形面板（M4.1）。
 *
 * 对齐旧 ui/widgets/waveform_widget.py 中**真的被接线使用**的部分：实时刷新、
 * 框选缩放（旧版 ViewBox.RectMode）、十字游标、自动量程、暂停、信息行
 * （`X mVpk | Y μs | N pts`，旧版每 10 帧才更新一次标签，这里沿用同样的节流）。
 *
 * 刻意没做的：触发标记线、zoom_x/zoom_y、fit_to_view、set_y_unit——在旧版里
 * `set_trigger_position` 等调用点为 0，是死 API；`cursor_position` 信号只 emit、
 * 全仓库无人 connect，也就是旧版其实没有游标读数。读数在这里是新增能力，
 * 不是复刻。
 */

import { ref, shallowRef } from "vue";
import type uPlot from "uplot";
import PlotCanvas from "@/renderer/components/plot-canvas.vue";
import { chartTheme } from "@/renderer/lib/chart-theme";
import { useFrameDraw } from "@/renderer/lib/use-frame-draw";
import { toFloat32, type Frame, type WaveformMeta } from "@/shared/protocol";

const paused = ref(false);
const autoRange = ref(true);
const cursorOn = ref(false);
const info = shallowRef("—");
const cursorRead = shallowRef("");

let plot: uPlot | null = null;
let xBuf: Float64Array | null = null;
let yBuf: Float64Array | null = null;
let frameCount = 0;
/** 暂停时仍要能改游标读数，但不能吃新帧 */
let lastSampleRate = 0;

function buildOptions(): uPlot.Options {
  const t = chartTheme();
  return {
    width: 10,
    height: 10,
    legend: { show: false },
    // x+y 都为 true：一条竖直 + 一条水平游标线，等价旧版两条 InfiniteLine
    cursor: { x: true, y: true, drag: { x: true, y: true } },
    scales: { x: { time: false }, y: { auto: true } },
    axes: [
      {
        label: "时间 (μs)",
        stroke: t.fg,
        grid: { stroke: t.grid },
        ticks: { stroke: t.grid },
        values: (_s, v) => v.map((x) => Number(x).toFixed(1)),
        font: t.axisFont,
      },
      {
        label: "幅值 (mV)",
        stroke: t.fg,
        grid: { stroke: t.grid },
        ticks: { stroke: t.grid },
        values: (_s, v) => v.map((x) => Number(x).toFixed(1)),
        font: t.axisFont,
        size: 58,
      },
    ],
    series: [{ label: "时间" }, { label: "波形", stroke: t.primary, width: 1 }],
    hooks: {
      setCursor: [
        (self: uPlot) => {
          const idx = self.cursor.idx;
          if (!cursorOn.value || idx == null || !xBuf) {
            if (!cursorOn.value && cursorRead.value) cursorRead.value = "";
            return;
          }
          cursorRead.value = `t=${(xBuf[idx] ?? 0).toFixed(2)} μs  v=${(yBuf?.[idx] ?? 0).toFixed(2)} mV`;
        },
      ],
    },
  };
}

const options = ref<uPlot.Options>(buildOptions());

function onReady(instance: uPlot): void {
  plot = instance;
}

function draw(frame: Frame): void {
  if (paused.value || !plot) return;
  const meta = frame.m as unknown as WaveformMeta;
  const samples = toFloat32(frame);
  const n = samples.length;
  if (n < 2) return;

  const rate = meta.sample_rate || lastSampleRate || 100_000_000;
  const rateChanged = rate !== lastSampleRate;
  lastSampleRate = rate;

  // 时间轴只由 (n, sample_rate) 决定，两者不变就不必每帧重算
  if (xBuf === null || xBuf.length !== n || yBuf === null || rateChanged) {
    xBuf = new Float64Array(n);
    yBuf = new Float64Array(n);
    const dtUs = 1_000_000 / rate;
    for (let i = 0; i < n; i += 1) xBuf[i] = i * dtUs;
  }
  for (let i = 0; i < n; i += 1) yBuf[i] = samples[i] ?? 0;

  plot.setData([xBuf, yBuf]);

  frameCount += 1;
  if (frameCount % 10 === 0) {
    let peak = 0;
    for (let i = 0; i < n; i += 1) {
      const v = Math.abs(yBuf[i] ?? 0);
      if (v > peak) peak = v;
    }
    info.value = `${peak.toFixed(1)} mVpk | ${(xBuf[n - 1] ?? 0).toFixed(1)} μs | ${n} pts`;
  }
}

// stats 本身就是 ref，直接在模板里绑定即可，不需要定时器搬运
const { stats } = useFrameDraw("waveform", draw);

function togglePause(): void {
  paused.value = !paused.value;
}

function toggleCursor(): void {
  cursorOn.value = !cursorOn.value;
  if (!cursorOn.value) cursorRead.value = "";
}

/**
 * 自动量程关掉后要把 y 轴钉在"当前看到的范围"上，否则关掉等于没关。
 * 重新打开时把 auto 交还给 uPlot，并用一次 setData 触发重新计算。
 *
 * setData 必须传**新的外层数组**：uPlot 内部拿 `data !== self.data` 判断要不要重排
 * 与重算 scale，把 `plot.data` 原样传回去会被当成同一份数据而跳过复位——这个坑同时
 * 影响这里的复位和下面的自动量程重开。
 */
function toggleAutoRange(): void {
  autoRange.value = !autoRange.value;
  const yScale = plot?.scales.y;
  if (!plot || !yScale || xBuf === null) return;
  if (autoRange.value) {
    yScale.auto = true;
    plot.setData([xBuf, yBuf!]);
  } else {
    const { min, max } = yScale;
    if (min != null && max != null) plot.setScale("y", { min, max });
  }
}

/** 框选放大后没有右键复位可用，给一个明确的出口。 */
function resetZoom(): void {
  const xScale = plot?.scales.x;
  const yScale = plot?.scales.y;
  if (!plot || !xScale || !yScale || xBuf === null) return;
  xScale.auto = true;
  yScale.auto = autoRange.value;
  plot.setData([xBuf, yBuf!]);
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-1">
    <div class="flex items-center justify-between gap-2 text-[11px]">
      <div class="flex items-center gap-2">
        <button
          class="rounded border px-2 py-0.5"
          :class="autoRange ? 'border-app-primary text-app-primary' : 'border-app-border text-app-muted'"
          @click="toggleAutoRange"
        >
          自动量程
        </button>
        <button
          class="rounded border px-2 py-0.5"
          :class="cursorOn ? 'border-app-primary text-app-primary' : 'border-app-border text-app-muted'"
          @click="toggleCursor"
        >
          游标
        </button>
        <button
          class="rounded border px-2 py-0.5"
          :class="paused ? 'border-app-warning text-app-warning' : 'border-app-border text-app-muted'"
          @click="togglePause"
        >
          {{ paused ? "继续" : "暂停" }}
        </button>
        <button class="rounded border border-app-border px-2 py-0.5 text-app-muted" @click="resetZoom">
          复位
        </button>
        <span class="tabular text-app-muted">{{ info }}</span>
      </div>
      <div class="flex items-center gap-3">
        <span v-if="cursorRead" class="tabular text-app-primary">{{ cursorRead }}</span>
        <span class="tabular text-app-muted">入帧 {{ stats.inFps }} · 绘制 {{ stats.fps }} · p95 {{ stats.drawP95 }}ms · 丢帧 {{ stats.dropFps }}</span>
        <span class="tabular" :class="paused ? 'text-app-warning' : 'text-app-success'">
          {{ paused ? "● 暂停" : "● 实时" }}
        </span>
      </div>
    </div>
    <div class="min-h-0 flex-1">
      <PlotCanvas :options="options" @ready="onReady" />
    </div>
  </div>
</template>
