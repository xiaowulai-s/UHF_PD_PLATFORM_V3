/**
 * 仪表板"三类快照"的绘制。刻意不套用 monitor 页的 uPlot / three 组件：
 * 那几个面板各自带游标、框选、RPC 与 rAF 循环，而快照只需要"一眼看出形状"，
 * 验收线是 20 FPS 下 CPU < 15%。这里全部是"定时器 + 一次 fillRect/polyline"，
 * 没有 rAF、没有交互、没有 RPC。
 *
 * 三个画法都按帧的 meta 自报口径取轴，不猜单位：
 *  - 波形/AE 包络：sample_rate 直接来自帧（AE 是 2 MHz，曾被前端兜底成 100 MHz，
 *    横轴差 50 倍而图看起来完全正常）
 *  - FFT：负载是 [频率, 幅值] 交错的 (N,2)，x 轴按真实 Hz 画到奈奎斯特
 *  - PRPD：幅值轴用 meta.amp_axis_max（矩阵分箱轴），不是 max_amplitude（观测值）
 */

import { paintPrpdCells } from "./colormap";
import { decodeCells, toFloat32, type Frame, type PrpdMeta, type WaveformMeta, type FftMeta } from "@/shared/protocol";
import type { ChartTheme } from "./chart-theme";

export interface SnapshotResult {
  /** 画在标题右侧的一句话口径说明；拿不到就返回 "—" */
  caption: string;
}

/** 统一的留白：快照不画刻度文字，只留一点点边距免得曲线贴边被裁 */
const PAD = 2;

function prep(canvas: HTMLCanvasElement, theme: ChartTheme): CanvasRenderingContext2D | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const { width: w, height: h } = canvas;
  ctx.clearRect(0, 0, w, h);
  // 底色用 surface：截图里透明底会被下层卡片色顶出来，看着像"没画"
  ctx.fillStyle = theme.surface;
  ctx.fillRect(0, 0, w, h);
  ctx.lineWidth = 1;
  return ctx;
}

/** 折线：把一维序列按 [min,max] 归一化后铺满画布 */
function polyline(
  ctx: CanvasRenderingContext2D,
  values: Float32Array,
  w: number,
  h: number,
  color: string,
): void {
  const n = values.length;
  if (n < 2) return;
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < n; i += 1) {
    const v = values[i] ?? 0;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  // 全平的一帧（例如全零）不能让 span 为 0，否则会除零并画出 NaN 路径
  const span = hi - lo || 1;
  const innerH = Math.max(1, h - PAD * 2);
  ctx.strokeStyle = color;
  ctx.beginPath();
  for (let i = 0; i < n; i += 1) {
    const x = (i / (n - 1)) * (w - 1);
    const y = PAD + (1 - ((values[i] ?? 0) - lo) / span) * innerH;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

/** 波形 / AE 包络快照。两者负载同构，区别只在 sample_rate 与传感器类型。 */
export function drawWaveformSnapshot(
  canvas: HTMLCanvasElement,
  frame: Frame,
  theme: ChartTheme,
): SnapshotResult {
  const ctx = prep(canvas, theme);
  if (!ctx) return { caption: "—" };
  const meta = frame.m as unknown as WaveformMeta;
  const samples = toFloat32(frame);
  polyline(ctx, samples, canvas.width, canvas.height, theme.primary);
  const sr = meta.sample_rate ?? 0;
  // 横轴时长 = 点数 / 采样率。内核不给采样率时**不要兜底**：monitor 页的波形组件
  // 把 0 兜底成 100 MHz，对 UHF 恰好蒙对、对 AE 差 50 倍而图看起来完全正常
  // （M5.1 修的就是这条）。这里宁可写明"时间基缺失"，也不给一个猜出来的刻度。
  if (sr <= 0) {
    return { caption: `${meta.sensor_type ?? "?"} · ${samples.length} 点（帧内无 sample_rate，时间基缺失）` };
  }
  const us = (samples.length / sr) * 1e6;
  return {
    caption: `${meta.sensor_type ?? "?"} · ${us.toFixed(1)} µs @ ${(sr / 1e6).toFixed(2)} MHz`,
  };
}

/** FFT 快照：横轴真实频率，纵轴 dB（线性幅值在快照尺寸下几乎读不出底噪结构） */
export function drawFftSnapshot(
  canvas: HTMLCanvasElement,
  frame: Frame,
  theme: ChartTheme,
): SnapshotResult {
  const ctx = prep(canvas, theme);
  if (!ctx) return { caption: "—" };
  const meta = frame.m as unknown as FftMeta;
  const flat = toFloat32(frame);
  const n = Math.floor(flat.length / 2);
  if (n < 2) return { caption: "—" };
  const mags = new Float32Array(n);
  for (let i = 0; i < n; i += 1) mags[i] = flat[i * 2 + 1] ?? 0;
  // dB 换算留在显示层：协议只传 Hz 与线性幅值（见 M4.2 的口径说明）
  let peak = 0;
  for (let i = 0; i < n; i += 1) peak = Math.max(peak, mags[i] ?? 0);
  const floor = peak > 0 ? peak * 1e-3 : 1e-12;
  const db = new Float32Array(n);
  for (let i = 0; i < n; i += 1) db[i] = 20 * Math.log10(Math.max(mags[i] ?? 0, floor) / peak);
  polyline(ctx, db, canvas.width, canvas.height, theme.info);
  const nyq = (flat[(n - 1) * 2] ?? 0) / 1e6;
  return {
    caption: `${meta.sensor_type ?? "?"} · 0–${nyq.toFixed(2)} MHz · 峰值 ${peak.toFixed(1)}`,
  };
}

/**
 * PRPD 快照。
 *
 * 量化格子必须先铺进一张 phaseBins×ampBins 的 ImageData，再整体缩放贴到目标画布上：
 * 逐格算缩放会把 92k 次乘法塞进主线程，而 drawImage 走的是合成器。
 * 临时画布与 ImageData 都按尺寸缓存复用，避免 2 Hz 下每秒产生几百 KB 垃圾。
 */
let scratch: HTMLCanvasElement | null = null;
let scratchImage: ImageData | null = null;

export async function drawPrpdSnapshot(
  canvas: HTMLCanvasElement,
  frame: Frame,
  theme: ChartTheme,
): Promise<SnapshotResult> {
  const ctx = prep(canvas, theme);
  if (!ctx) return { caption: "—" };
  const meta = frame.m as unknown as PrpdMeta;
  const decoded = await decodeCells(frame);
  const [phaseBins = 0, ampBins = 0] = decoded.shape;
  if (phaseBins < 1 || ampBins < 1) return { caption: "—" };

  if (!scratch) scratch = document.createElement("canvas");
  if (scratch.width !== phaseBins || scratch.height !== ampBins) {
    scratch.width = phaseBins;
    scratch.height = ampBins;
    scratchImage = null;
  }
  const sctx = scratch.getContext("2d");
  if (!sctx) return { caption: "—" };
  if (!scratchImage) scratchImage = sctx.createImageData(phaseBins, ampBins);
  // 量化格子的原始值域是 0..255，直接当索引查色表；要真实量纲才走 toValues()
  paintPrpdCells(scratchImage, decoded.cells, decoded.shape);
  sctx.putImageData(scratchImage, 0, 0);

  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(scratch, 0, 0, phaseBins, ampBins, 0, 0, canvas.width, canvas.height);

  const ampMax = meta.amp_axis_max ?? (meta.max_amplitude > 0 ? meta.max_amplitude : 100);
  return {
    caption: `${meta.mode ?? "heatmap"} · ${meta.total_events ?? 0} 事件 · 幅值轴 0–${ampMax.toFixed(0)}`,
  };
}
