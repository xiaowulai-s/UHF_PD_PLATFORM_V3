/**
 * 以显示帧为节奏消费某个 topic 的最新一帧。
 *
 * 刻意不在 WebSocket 回调里直接画：入帧是 20FPS、合成是 60FPS（或 144Hz），
 * 来一帧画一帧会在同一个显示帧里重复绘制，也可能整帧被跳过，量出来的"掉帧"
 * 其实是限流器的节奏而不是绘制的代价——render-probe 最初就栽在这个测法上。
 * rAF 循环保证"每个显示帧最多画一次，且总是画最新的一帧"。
 *
 * 解码是异步的（zlib topic 走 DecompressionStream），所以必须防重入：上一次还没
 * 画完时新帧只覆盖 pending，不排队。这正是 lossy topic 该有的语义，同时把
 * "绘制跟不上入帧"这件事计成 dropped，让它变成可见的数字而不是画面的卡顿。
 */

import { onScopeDispose, ref, shallowRef, watch, type Ref } from "vue";
import { latest } from "./store";
import type { Frame, Topic } from "@/shared/protocol";

export interface DrawStats {
  /** 成功绘制次数 */
  drawn: number;
  /** 到达后被覆盖、因而永远不会被单独绘制的帧数（累计，含节奏合并与绘制重入） */
  dropped: number;
  /** 同上，但按采样窗口折算成"每秒丢帧"，与 inFps/fps 同口径可直接相减 */
  dropFps: number;
  /** 最近一个采样窗口的实际绘制频率 */
  fps: number;
  /**
   * 最近一个采样窗口的**入帧**频率。
   *
   * 必须和 fps 分开显示：读数只给一个"11 FPS"时，分不清是内核没发够 20 帧，
   * 还是渲染跟不上——前者要改服务端节流契约，后者要改绘图，是完全不同的活。
   */
  inFps: number;
  /** 单次绘制耗时 p95（毫秒）。M7.1 要求图表层落地后复测，这里就是那个复测点。 */
  drawP95: number;
}

/** 绘制频率采样窗口。1s 足够稳，又能让"停了"在两秒内可见。 */
const SAMPLE_MS = 1000;

export function useFrameDraw(
  topic: Topic,
  draw: (frame: Frame) => void | Promise<void>,
): { stats: Ref<DrawStats> } {
  const stats = ref<DrawStats>({ drawn: 0, dropped: 0, fps: 0, inFps: 0, dropFps: 0, drawP95: 0 });
  const pending = shallowRef<Frame | null>(null);
  let inFlight = false;
  let raf = 0;
  let drawsSinceSample = 0;
  let receivesSinceSample = 0;
  let dropsSinceSample = 0;
  /** 最近 60 次绘制耗时，够算 p95 又不用为排序留大数组 */
  const durations: number[] = [];

  watch(
    () => latest.value[topic],
    (frame) => {
      if (!frame) return;
      receivesSinceSample += 1;
      // 判据是"有没有一帧还堵在 pending 里"，不是"绘制是否进行中"：
      // 后者会漏掉纯节奏合并（两个入帧落在同一个 rAF tick 里，前一个被覆盖），
      // 实测 16 入帧只画了 12 次，而按 inFlight 统计的计数器一直显示 0——那个数
      // 字会让人以为什么都没丢，实际上每秒丢了 4 帧的显示机会。
      if (pending.value !== null) {
        stats.value.dropped += 1;
        dropsSinceSample += 1;
      }
      pending.value = frame;
    },
    { immediate: true },
  );

  const loop = () => {
    raf = requestAnimationFrame(loop);
    const frame = pending.value;
    if (frame === null || inFlight) return;
    pending.value = null;
    inFlight = true;
    const started = performance.now();
    Promise.resolve()
      .then(() => draw(frame))
      .catch((err: unknown) => {
        // 单帧画崩不能把整个 rAF 循环带走，但要留下痕迹
        console.error(`[${topic}] 绘制失败`, err);
      })
      .finally(() => {
        inFlight = false;
        durations.push(performance.now() - started);
        if (durations.length > 60) durations.shift();
        stats.value.drawn += 1;
        drawsSinceSample += 1;
      });
  };
  raf = requestAnimationFrame(loop);

  // 频率必须用独立的 setInterval 算，不能只在绘制完成时更新：窗口最小化或被遮挡时
  // rAF 完全停摆，一次绘制都不发生，于是"永远停在最后一次成功值"上。操作台看板上
  // 显示"绘制 20 FPS"而实际什么都没画，比显示 0 危险得多。
  const sampler = window.setInterval(() => {
    stats.value.fps = Math.round((drawsSinceSample * 1000) / SAMPLE_MS);
    stats.value.inFps = Math.round((receivesSinceSample * 1000) / SAMPLE_MS);
    if (durations.length > 0) {
      const sorted = [...durations].sort((a, b) => a - b);
      const idx = Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1);
      stats.value.drawP95 = Number((sorted[idx] ?? 0).toFixed(2));
    }
    drawsSinceSample = 0;
    receivesSinceSample = 0;
    stats.value.dropFps = Math.round((dropsSinceSample * 1000) / SAMPLE_MS);
    dropsSinceSample = 0;
  }, SAMPLE_MS);

  onScopeDispose(() => {
    cancelAnimationFrame(raf);
    window.clearInterval(sampler);
  });

  // 只读靠约定：内部要自增计数，包成 readonly() 会连自己的写入一起挡掉，
  // 还给每次读取加一层代理。
  return { stats };
}
