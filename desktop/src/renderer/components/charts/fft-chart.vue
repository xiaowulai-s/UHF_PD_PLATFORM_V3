<script setup lang="ts">
/**
 * FFT 频谱面板（M4.2）。
 *
 * 对齐旧 ui/widgets/fft_widget.py：x 轴 MHz、y 轴可选 dB（20*log10(mag+1e-10)）、
 * 峰值标注、噪声底横线、频段高亮带。Hz→MHz 与线性→dB 在旧版也归显示层，
 * 协议始终传原始 Hz 与线性幅值，所以这里只改画法不改契约。
 *
 * 协议里 BandStats 的 f_min/f_max 历史上是 MHz 而 peak_frequency 是 Hz，
 * 由 payloads.build_fft 统一换算成 _hz 后缀字段后才到这里，单位不再靠猜。
 *
 * 峰值与频段带都不走 uPlot 的 series 而是同一个 draw hook 里手画：
 * 稀疏散点要拿 null 表达空洞，就得每帧重建三个 number[]（2049*3 元素 / 帧），
 * 而用 hook 画可以整条数据链都用复用的 Float64Array，还顺带拿到基波/谐波的配色区分。
 */

import { ref, shallowRef } from "vue";
import type uPlot from "uplot";
import PlotCanvas from "@/renderer/components/plot-canvas.vue";
import { chartTheme } from "@/renderer/lib/chart-theme";
import { useFrameDraw } from "@/renderer/lib/use-frame-draw";
import { latest } from "@/renderer/lib/store";
import { toFloat32, type FftBand, type FftMeta, type Frame } from "@/shared/protocol";

interface PeakRow {
  freqMHz: string;
  mag: string;
  kind: string;
  /** 排名决定配色：第 1 名 error、2–3 名 warning、其余 muted（沿用旧版高亮规则） */
  tone: "error" | "warning" | "muted";
  harmonic: boolean;
}

const TONE: Record<PeakRow["tone"], string> = {
  error: "text-app-error",
  warning: "text-app-warning",
  muted: "text-app-muted",
};

const logScale = ref(true);
const readout = shallowRef({ snr: 0, noise: 0, peaks: 0 });
/** 峰值表只在内容真的变化时才换引用，否则 15FPS 的帧会驱动 15 次表格重渲染 */
const peakRows = shallowRef<PeakRow[]>([]);
let peakSignature = "";

let plot: uPlot | null = null;
/** draw hook 读不到组件作用域的最新帧数据，覆盖层状态显式缓存在这里 */
let overlay: { bands: FftBand[]; noise: number | null; peaks: FftMeta["peaks"] } = {
  bands: [],
  noise: null,
  peaks: [],
};

/** 4096 点窗固定 2049 个频点，逐帧新建等于每秒制造 ~120K 个临时值 */
let xBuf: Float64Array | null = null;
let yBuf: Float64Array | null = null;

function toDisplay(mag: number): number {
  return logScale.value ? 20 * Math.log10(mag + 1e-10) : mag;
}

function buildOptions(): uPlot.Options {
  const t = chartTheme();
  return {
    width: 10,
    height: 10,
    legend: { show: false },
    // 拖动框选缩放；y 轴不参与，幅值范围跟着数据走
    cursor: { drag: { x: true, y: false } },
    scales: { x: { time: false }, y: { auto: true } },
    axes: [
      {
        label: "频率 (MHz)",
        stroke: t.fg,
        grid: { stroke: t.grid },
        ticks: { stroke: t.grid },
        values: (_self, vals) => vals.map((v) => Number(v).toFixed(Number(v) < 10 ? 2 : 0)),
        font: t.axisFont,
      },
      {
        label: "幅值",
        stroke: t.fg,
        grid: { stroke: t.grid },
        ticks: { stroke: t.grid },
        values: (_self, vals) => vals.map((v) => Number(v).toFixed(1)),
        font: t.axisFont,
        size: 58,
      },
    ],
    series: [{ label: "频率" }, { label: "频谱", stroke: t.primary, width: 1 }],
    hooks: { draw: [paintOverlay] },
  };
}

function paintOverlay(self: uPlot): void {
  const ctx = self.ctx;
  const { left, top, width, height } = self.bbox;
  const t = chartTheme();
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, width, height);
  ctx.clip();

  for (const band of overlay.bands) {
    const x1 = self.valToPos(band.f_min_hz / 1e6, "x", true);
    const x2 = self.valToPos(band.f_max_hz / 1e6, "x", true);
    if (x2 <= left || x1 >= left + width) continue;
    ctx.fillStyle = "rgba(47, 116, 255, 0.07)";
    ctx.fillRect(x1, top, x2 - x1, height);
    ctx.fillStyle = "rgba(47, 116, 255, 0.6)";
    ctx.font = t.tickFont;
    ctx.fillText(band.name, Math.max(x1, left) + 3, top + 11);
  }

  if (overlay.noise !== null) {
    const y = self.valToPos(overlay.noise, "y", true);
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = t.error;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(left + width, y);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  for (const p of overlay.peaks) {
    const x = self.valToPos(p.frequency / 1e6, "x", true);
    const y = self.valToPos(toDisplay(p.magnitude), "y", true);
    if (x < left || x > left + width) continue;
    ctx.beginPath();
    ctx.arc(x, y, p.is_harmonic ? 2.5 : 3.5, 0, Math.PI * 2);
    ctx.fillStyle = p.is_harmonic ? t.warning : t.error;
    ctx.fill();
  }
  ctx.restore();
}

const options = ref<uPlot.Options>(buildOptions());

function onReady(instance: uPlot): void {
  plot = instance;
}

/** 与旧版 ui/widgets/fft_widget.py:249-277 逐字段对齐：前 10、按幅值降序、同样的类型文案。
 *  旧版列头写 "幅值 (dBm)" 但填的是线性原始值，这里保留数值、改掉误导性列头。 */
function syncPeakTable(peaks: FftMeta["peaks"]): void {
  const top = [...peaks].sort((a, b) => b.magnitude - a.magnitude).slice(0, 10);
  const sig = top.map((p) => `${p.frequency.toFixed(0)}:${p.magnitude.toFixed(1)}`).join("|");
  if (sig === peakSignature) return;
  peakSignature = sig;
  peakRows.value = top.map((p, i) => ({
    freqMHz: (p.frequency / 1e6).toFixed(2),
    mag: p.magnitude.toFixed(1),
    kind: p.is_harmonic && p.harmonic_order > 1 ? `谐波${p.harmonic_order}` : p.is_harmonic ? "基频" : "峰值",
    tone: i === 0 ? "error" : i < 3 ? "warning" : "muted",
    harmonic: p.is_harmonic,
  }));
}

function draw(frame: Frame): void {
  if (!plot) return;
  const meta = frame.m as unknown as FftMeta;
  const pair = toFloat32(frame);
  const n = pair.length >> 1;
  if (n < 2) return;

  if (xBuf === null || xBuf.length !== n) {
    xBuf = new Float64Array(n);
    yBuf = new Float64Array(n);
  }
  const xs = xBuf;
  const ys = yBuf!;
  // 负载是 [频率(Hz), 幅值] 交错的行主序 (N,2)
  for (let i = 0; i < n; i += 1) {
    xs[i] = (pair[i * 2] ?? 0) / 1e6;
    ys[i] = toDisplay(pair[i * 2 + 1] ?? 0);
  }

  overlay = {
    bands: meta.bands ?? [],
    noise: meta.noise_floor > 0 ? toDisplay(meta.noise_floor) : null,
    peaks: meta.peaks ?? [],
  };

  // setData 内部就会重算并绘制；uPlot 没有 draw()，只有 redraw()，别在这里多画一遍
  plot.setData([xs, ys]);

  const peaks = meta.peaks ?? [];
  syncPeakTable(peaks);
  readout.value = { ...readout.value, snr: meta.snr_db ?? 0, noise: meta.noise_floor ?? 0, peaks: peaks.length };
}

// stats 是 ref，模板直接绑定即可；再套一层 setInterval 搬运只是多一条失效路径
const { stats } = useFrameDraw("fft", draw);

function toggleLog(): void {
  logScale.value = !logScale.value;
  const frame = latest.value.fft;
  if (frame) draw(frame);
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-1">
    <div class="flex items-center justify-between gap-2 text-[11px] text-app-muted">
      <div class="flex items-center gap-3">
        <label class="flex cursor-pointer items-center gap-1.5 select-none">
          <input type="checkbox" :checked="logScale" @change="toggleLog" />
          <span>对数刻度 (dB)</span>
        </label>
        <span class="tabular">SNR {{ readout.snr.toFixed(1) }} dB</span>
        <span class="tabular">噪声 {{ readout.noise.toFixed(2) }}</span>
        <span class="tabular">峰值 {{ readout.peaks }}</span>
      </div>
      <div class="tabular flex gap-3">
        <span :class="stats.fps > 0 && stats.fps < 18 ? 'text-app-warning' : ''">
          入帧 {{ stats.inFps }} · 绘制 {{ stats.fps }} · p95 {{ stats.drawP95 }}ms
        </span>
        <span :class="stats.dropped > 0 ? 'text-app-warning' : ''">合并丢帧 {{ stats.dropFps }}</span>
      </div>
    </div>
    <div class="flex min-h-0 flex-1 gap-2">
      <div class="min-w-0 flex-1">
        <PlotCanvas :options="options" @ready="onReady" />
      </div>
      <div class="w-52 shrink-0 overflow-auto rounded border border-app-border">
        <table class="w-full text-[11px]">
          <thead class="sticky top-0 bg-app-surface text-app-muted">
            <tr>
              <th class="px-2 py-1 text-left font-normal">MHz</th>
              <th class="px-2 py-1 text-right font-normal">幅值</th>
              <th class="px-2 py-1 text-left font-normal">类型</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in peakRows" :key="i" class="border-t border-app-border/50">
              <td class="tabular px-2 py-0.5" :class="TONE[row.tone]">{{ row.freqMHz }}</td>
              <td class="tabular px-2 py-0.5 text-right" :class="TONE[row.tone]">{{ row.mag }}</td>
              <td class="px-2 py-0.5" :class="row.harmonic ? 'text-app-primary' : 'text-app-muted'">
                {{ row.kind }}
              </td>
            </tr>
            <tr v-if="!peakRows.length">
              <td colspan="3" class="px-2 py-3 text-center text-app-muted">无峰值</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
