<script setup lang="ts">
import { onMounted, ref } from "vue";
import { runRenderProbe, formatVerdict, type ProbeResult } from "@/renderer/lib/render-probe";

// hash 路由下 query 在 # 之后，location.search 恒为空，必须从 hash 里取
const hashQuery = location.hash.split("?")[1] ?? "";
const DURATION_MS = Math.max(2000, Number(new URLSearchParams(hashQuery).get("probeMs") ?? 20_000) || 20_000);

const wave = ref<HTMLCanvasElement>();
const fft = ref<HTMLCanvasElement>();
const prpd = ref<HTMLCanvasElement>();
const prps = ref<HTMLCanvasElement>();
const frames = ref(0);
const elapsed = ref(0);
const done = ref<ProbeResult | null>(null);
const error = ref("");

onMounted(async () => {
  if (!wave.value || !fft.value || !prpd.value || !prps.value) {
    error.value = "画布未就绪";
    return;
  }
  const result = await runRenderProbe(
    { waveform: wave.value, fft: fft.value, prpd: prpd.value, prps: prps.value },
    { durationMs: DURATION_MS, onProgress: (f, e) => ((frames.value = f), (elapsed.value = e)) },
  );
  done.value = result;
  // Electron 下由主进程落盘并退出；纯浏览器里只展示
  window.probe?.report(result);
});
</script>

<template>
  <div class="flex flex-col gap-3 p-2">
    <div class="flex items-center justify-between">
      <span class="text-[13px] font-medium">渲染能力探针 (M7.1)</span>
      <span class="tabular text-[12px] text-app-muted">
        {{ done ? `完成 ${done.frames} 帧` : `${(elapsed / 1000).toFixed(1)}s / ${(DURATION_MS / 1000).toFixed(0)}s · ${frames} 帧` }}
      </span>
    </div>

    <div class="grid grid-cols-2 gap-3">
      <div class="rounded-lg border border-app-border bg-app-surface p-2">
        <div class="text-[11px] text-app-muted">波形 4096 点</div>
        <canvas ref="wave" width="760" height="180" class="w-full" />
      </div>
      <div class="rounded-lg border border-app-border bg-app-surface p-2">
        <div class="text-[11px] text-app-muted">FFT 2049 点</div>
        <canvas ref="fft" width="760" height="180" class="w-full" />
      </div>
      <div class="rounded-lg border border-app-border bg-app-surface p-2">
        <div class="text-[11px] text-app-muted">PRPD 360x256 热图</div>
        <canvas ref="prpd" width="760" height="256" class="w-full" />
      </div>
      <div class="rounded-lg border border-app-border bg-app-surface p-2">
        <div class="text-[11px] text-app-muted">PRPS 512x360 热图</div>
        <canvas ref="prps" width="760" height="256" class="w-full" />
      </div>
    </div>

    <p v-if="error" class="text-[12px] text-app-error">{{ error }}</p>

    <div v-if="done" class="rounded-lg border border-app-border bg-app-surface p-3">
      <div
        class="text-[13px] font-medium"
        :class="done.fps >= done.targetFps * 2 && done.longFrames === 0 ? 'text-app-success' : done.fps >= done.targetFps ? 'text-app-warning' : 'text-app-error'"
      >
        {{ formatVerdict(done) }}
      </div>
      <dl class="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-[12px]">
        <div class="flex justify-between"><dt class="text-app-muted">实测 FPS</dt><dd class="tabular">{{ done.fps.toFixed(1) }}</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">目标 FPS</dt><dd class="tabular">{{ done.targetFps }}</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">绘制 p50</dt><dd class="tabular">{{ done.drawMs.p50.toFixed(2) }} ms</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">绘制 p95</dt><dd class="tabular">{{ done.drawMs.p95.toFixed(2) }} ms</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">绘制 p99</dt><dd class="tabular">{{ done.drawMs.p99.toFixed(2) }} ms</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">绘制最慢</dt><dd class="tabular">{{ done.drawMs.max.toFixed(2) }} ms</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">超 20FPS 预算</dt><dd class="tabular">{{ done.overBudget }} / {{ done.frames }}</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">预算余量</dt><dd class="tabular">{{ done.headroomAtTarget.toFixed(1) }}x</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">JS 堆增长</dt><dd class="tabular">{{ done.memoryMB.growthMB.toFixed(1) }} MB</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">WebGL</dt><dd>{{ done.gpu.webglAvailable ? "可用" : "不可用" }}</dd></div>
        <div class="flex justify-between"><dt class="text-app-muted">最大纹理</dt><dd class="tabular">{{ done.gpu.maxTextureSize }}</dd></div>
      </dl>
      <div class="mt-2 border-t border-app-border pt-2 text-[11px]">
        <div><span class="text-app-muted">GPU renderer：</span><span class="font-mono">{{ done.gpu.renderer }}</span></div>
        <div><span class="text-app-muted">vendor：</span><span class="font-mono">{{ done.gpu.vendor }}</span></div>
        <div><span class="text-app-muted">GL 版本：</span><span class="font-mono">{{ done.gpu.webglVersion }}</span></div>
      </div>
    </div>
  </div>
</template>
