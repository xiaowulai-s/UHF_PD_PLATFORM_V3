/**
 * 渲染能力探针（M7.1）。
 *
 * 目的：在**目标工控机**上量化 Chromium 是否回落软件渲染、以及四类图元按真实
 * 数据尺寸能否稳住 20 FPS。这是唯一还没被任何实测覆盖的架构假设。
 *
 * 负载刻意对齐旧版 pyqtgraph 的实际绘制量，而不是画个三角形糊弄过去：
 *   波形 4096 点、FFT 2049 点、PRPD 360x256 热图、PRPS 512x360 热图。
 * 热图走 ImageData + putImageData，与 prpd_widget.py 的 ImageItem 等价。
 */

const WAVE_POINTS = 4096;
const FFT_POINTS = 2049;
const PRPD_W = 360;
const PRPD_H = 256;
const PRPS_W = 512;
const PRPS_H = 360;

export interface ProbeCanvasSet {
  waveform: HTMLCanvasElement;
  fft: HTMLCanvasElement;
  prpd: HTMLCanvasElement;
  prps: HTMLCanvasElement;
}

export interface GpuReport {
  webglAvailable: boolean;
  renderer: string;
  vendor: string;
  webglVersion: string;
  shadingLanguageVersion: string;
  maxTextureSize: number;
  /** Chrome 的硬件加速判定；软件渲染时通常是 "SwiftShader" 或含 "Software" */
  likelySoftware: boolean;
}

export interface ProbeResult {
  durationMs: number;
  frames: number;
  /** 不限流下的实际 rAF 速率（通常等于显示器刷新率，不是绘制能力） */
  fps: number;
  targetFps: number;
  /**
   * 每帧四次绘制的实际耗时。这才是 M7.1 要的答案：限流闸门会掩盖真实余量，
   * 所以不限流、直接量绘制成本，再用预算周期除以它算余量。
   */
  drawMs: { p50: number; p95: number; p99: number; max: number; mean: number };
  /** 绘制耗时超过一个目标帧预算（1000/targetFps）的次数 */
  overBudget: number;
  /** 目标帧率下每帧可用的预算余量 */
  headroomAtTarget: number;
  longFrames: number;
  gpu: GpuReport;
  memoryMB: { start: number; end: number; growthMB: number };
  workload: { wavePoints: number; fftPoints: number; prpd: string; prps: string };
}

function percentile(sorted: number[], q: number): number {
  if (!sorted.length) return 0;
  const idx = Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)));
  return sorted[idx] ?? 0;
}

export function readGpuInfo(): GpuReport {
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
  if (!gl) {
    return {
      webglAvailable: false,
      renderer: "<无 WebGL 上下文>",
      vendor: "",
      webglVersion: "",
      shadingLanguageVersion: "",
      maxTextureSize: 0,
      likelySoftware: true,
    };
  }
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = debug ? String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)) : String(gl.getParameter(gl.RENDERER));
  const vendor = debug ? String(gl.getParameter(debug.UNMASKED_VENDOR_WEBGL)) : String(gl.getParameter(gl.VENDOR));
  return {
    webglAvailable: true,
    renderer,
    vendor,
    webglVersion: String(gl.getParameter(gl.VERSION)),
    shadingLanguageVersion: String(gl.getParameter(gl.SHADING_LANGUAGE_VERSION)),
    maxTextureSize: Number(gl.getParameter(gl.MAX_TEXTURE_SIZE)),
    likelySoftware: /swiftshader|software|llvmpipe|basic render/i.test(renderer),
  };
}

/** 预生成负载数据。用固定形状的正弦+噪声，保证每次运行可比。 */
function makeBuffers(): {
  wave: Float32Array;
  fft: Float32Array;
  prpd: Uint8ClampedArray<ArrayBuffer>;
  prps: Uint8ClampedArray<ArrayBuffer>;
} {
  const wave = new Float32Array(WAVE_POINTS);
  for (let i = 0; i < WAVE_POINTS; i += 1) {
    wave[i] = Math.sin(i / 40) * 0.6 + Math.sin(i / 7) * 0.25 + Math.sin(i / 173) * 0.4;
  }
  const fft = new Float32Array(FFT_POINTS);
  for (let i = 0; i < FFT_POINTS; i += 1) {
    const t = i / FFT_POINTS;
    fft[i] = Math.exp(-t * 6) * 0.9 + Math.exp(-((t - 0.32) ** 2) / 0.002) * 0.7 + Math.random() * 0.05;
  }
  const prpd = new Uint8ClampedArray(PRPD_W * PRPD_H * 4);
  for (let y = 0; y < PRPD_H; y += 1) {
    for (let x = 0; x < PRPD_W; x += 1) {
      const o = (y * PRPD_W + x) * 4;
      const v = Math.max(
        Math.exp(-(((x - 45) / 22) ** 2 + ((y - 180) / 40) ** 2)),
        Math.exp(-(((x - 315) / 22) ** 2 + ((y - 90) / 40) ** 2) * 0.8),
      );
      prpd[o] = Math.min(255, v * 255);
      prpd[o + 1] = 40 + v * 120;
      prpd[o + 2] = 255 - v * 120;
      prpd[o + 3] = 255;
    }
  }
  const prps = new Uint8ClampedArray(PRPS_W * PRPS_H * 4);
  for (let y = 0; y < PRPS_H; y += 1) {
    for (let x = 0; x < PRPS_W; x += 1) {
      const o = (y * PRPS_W + x) * 4;
      const v = Math.exp(-(((x - 120) / 60) ** 2 + ((y - 200) / 70) ** 2)) * (0.4 + 0.6 * Math.sin(y / 13) ** 2);
      prps[o] = v * 255;
      prps[o + 1] = 60 + v * 90;
      prps[o + 2] = 200 - v * 80;
      prps[o + 3] = 255;
    }
  }
  return { wave, fft, prpd, prps };
}

function drawLine(canvas: HTMLCanvasElement, data: Float32Array, color: string): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { width, height } = canvas;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  const step = width / (data.length - 1);
  for (let i = 0; i < data.length; i += 1) {
    const value = data[i] ?? 0;
    const y = height / 2 - (value * height) / 2.2;
    const x = i * step;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

function drawHeat(
  canvas: HTMLCanvasElement,
  rgba: Uint8ClampedArray<ArrayBuffer>,
  w: number,
  h: number,
  scratch: HTMLCanvasElement,
): void {
  const sctx = scratch.getContext("2d");
  const ctx = canvas.getContext("2d");
  if (!sctx || !ctx) return;
  const img = new ImageData(rgba, w, h);
  sctx.putImageData(img, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(scratch, 0, 0, canvas.width, canvas.height);
}

export interface ProbeOptions {
  durationMs?: number;
  /** 目标帧率，与上位机 20 FPS 刷新对齐 */
  targetFps?: number;
  onProgress?: (frames: number, elapsedMs: number) => void;
}

/**
 * 跑一轮压测。返回帧时分布与 GPU 判定。
 * 用 rAF 而不是 setTimeout：要测的是浏览器实际合成帧的节奏，不是定时器精度。
 */
export function runRenderProbe(
  canvases: ProbeCanvasSet,
  options: ProbeOptions = {},
): Promise<ProbeResult> {
  const durationMs = options.durationMs ?? 20_000;
  const targetFps = options.targetFps ?? 20;
  const buffers = makeBuffers();
  const scratchPrpd = document.createElement("canvas");
  scratchPrpd.width = PRPD_W;
  scratchPrpd.height = PRPD_H;
  const scratchPrps = document.createElement("canvas");
  scratchPrps.width = PRPS_W;
  scratchPrps.height = PRPS_H;

  const mem = performance as unknown as { memory?: { usedJSHeapSize: number } };
  const memStart = mem.memory?.usedJSHeapSize ?? 0;
  const drawTimes: number[] = [];
  const frameTimes: number[] = [];

  return new Promise((resolve) => {
    const t0 = performance.now();
    let last = t0;
    let frames = 0;

    const tick = () => {
      const now = performance.now();
      const elapsed = now - t0;
      // 不限流：每帧都画，直接量绘制成本
      const drawStart = performance.now();
      drawLine(canvases.waveform, buffers.wave, "#4B3FE3");
      drawLine(canvases.fft, buffers.fft, "#1A7F37");
      drawHeat(canvases.prpd, buffers.prpd, PRPD_W, PRPD_H, scratchPrpd);
      drawHeat(canvases.prps, buffers.prps, PRPS_W, PRPS_H, scratchPrps);
      const drawMs = performance.now() - drawStart;

      drawTimes.push(drawMs);
      frameTimes.push(now - last);
      last = now;
      frames += 1;
      options.onProgress?.(frames, elapsed);

      if (elapsed < durationMs) {
        requestAnimationFrame(tick);
        return;
      }
      const sorted = [...drawTimes].sort((a, b) => a - b);
      const budget = 1000 / targetFps;
      const p95 = percentile(sorted, 0.95);
      const mean = sorted.reduce((a, b) => a + b, 0) / (sorted.length || 1);
      resolve({
        durationMs: elapsed,
        frames,
        fps: (frames / elapsed) * 1000,
        targetFps,
        drawMs: {
          p50: percentile(sorted, 0.5),
          p95,
          p99: percentile(sorted, 0.99),
          max: sorted[sorted.length - 1] ?? 0,
          mean,
        },
        overBudget: drawTimes.filter((t) => t > budget).length,
        headroomAtTarget: p95 > 0 ? budget / p95 : Number.POSITIVE_INFINITY,
        longFrames: frameTimes.filter((t) => t > budget * 1.5).length,
        gpu: readGpuInfo(),
        memoryMB: {
          start: memStart / 1048576,
          end: (mem.memory?.usedJSHeapSize ?? 0) / 1048576,
          growthMB: ((mem.memory?.usedJSHeapSize ?? 0) - memStart) / 1048576,
        },
        workload: {
          wavePoints: WAVE_POINTS,
          fftPoints: FFT_POINTS,
          prpd: `${PRPD_W}x${PRPD_H}`,
          prps: `${PRPS_W}x${PRPS_H}`,
        },
      });
    };

    requestAnimationFrame(tick);
  });
}

export function formatVerdict(r: ProbeResult): string {
  const gpuNote = !r.gpu.webglAvailable
    ? "WebGL 不可用"
    : r.gpu.likelySoftware
      ? `软件渲染 (${r.gpu.renderer})`
      : r.gpu.renderer;
  if (!r.gpu.webglAvailable || r.gpu.likelySoftware) {
    return `降级风险：${gpuNote}`;
  }
  const pct = (r.drawMs.p95 / (1000 / r.targetFps)) * 100;
  if (r.headroomAtTarget >= 4 && r.overBudget === 0) {
    return `通过：单帧绘制 p95 ${r.drawMs.p95.toFixed(1)}ms，仅占 ${r.targetFps}FPS 预算的 ${pct.toFixed(0)}%，余量 ${r.headroomAtTarget.toFixed(0)}x`;
  }
  if (r.headroomAtTarget >= 1.5) {
    return `勉强：单帧绘制 p95 ${r.drawMs.p95.toFixed(1)}ms 占预算 ${pct.toFixed(0)}%，超预算 ${r.overBudget} 次`;
  }
  return `不通过：单帧绘制 p95 ${r.drawMs.p95.toFixed(1)}ms 已接近或超过 ${r.targetFps}FPS 的 ${(1000 / r.targetFps).toFixed(0)}ms 预算`;
}
