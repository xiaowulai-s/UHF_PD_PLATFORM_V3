<script setup lang="ts">
/**
 * 实时监测页（M5.1）。
 *
 * 四视图（波形/FFT/PRPD/PRPS）在 M4 已逐个验收，这里补的是 AE 模式。
 *
 * 模式是**显式 UI 状态**，不是由 sensor_type 数据驱动：旧版那条数据驱动路径
 * （signal_router.py:157-161 调 monitor.update_waveform）里 `update_waveform`
 * 这个方法根本不存在，每次都抛 AttributeError 被 except 吞掉，可达调用点为 0——
 * 也就是说旧版"按 AE/UHF 自动切换"从来没真的执行过。照它复刻等于凭空造需求。
 *
 * AE 模式下左侧波形面板显示的就是包络：内核 AE 管线把包络作为 `waveform` 帧
 * 重发（且带 sample_rate=2 MHz），所以不另画一条包络曲线，避免出现两个时间轴。
 */

import { computed, ref } from "vue";
import AePanel from "@/renderer/components/charts/ae-panel.vue";
import FftChart from "@/renderer/components/charts/fft-chart.vue";
import PrpdChart from "@/renderer/components/charts/prpd-chart.vue";
import PrpsChart from "@/renderer/components/charts/prps-chart.vue";
import WaveformChart from "@/renderer/components/charts/waveform-chart.vue";
import { stats } from "@/renderer/lib/store";

type Mode = "uhf" | "ae";
const mode = ref<Mode>("uhf");

/**
 * 只作为初值提示，不作为切换依据。device.list 的 coupling_type 来自数据库，
 * 与内核内存里的运行时耦合类型可能不一致（除非走过 device.save）。
 */
const liveCoupling = computed(() => stats.value?.active_source ?? null);

function setMode(next: Mode): void {
  mode.value = next;
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-2">
    <div class="flex items-center gap-2">
      <div class="flex rounded-md border border-app-border">
        <button
          class="rounded-l-md px-3 py-1 text-[12px]"
          :class="mode === 'uhf' ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
          @click="setMode('uhf')"
        >
          局放视图
        </button>
        <button
          class="rounded-r-md px-3 py-1 text-[12px]"
          :class="mode === 'ae' ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
          @click="setMode('ae')"
        >
          AE 视图
        </button>
      </div>
      <span class="text-[11px] text-app-muted">
        {{ mode === "ae" ? "左下为 AE 命中参数；波形面板此时显示 2 MHz 包络" : "波形 / FFT / PRPD / PRPS 四视图" }}
        <template v-if="liveCoupling"> · 活动源 {{ liveCoupling }}</template>
      </span>
    </div>

    <div class="grid min-h-0 flex-1 grid-cols-2 grid-rows-2 gap-2">
      <div class="flex min-h-0 flex-col rounded-lg border border-app-border bg-app-surface p-2">
        <WaveformChart />
      </div>
      <div class="flex min-h-0 flex-col rounded-lg border border-app-border bg-app-surface p-2">
        <FftChart />
      </div>
      <div class="flex min-h-0 flex-col rounded-lg border border-app-border bg-app-surface p-2">
        <PrpdChart />
      </div>
      <div class="flex min-h-0 flex-col rounded-lg border border-app-border bg-app-surface p-2">
        <PrpsChart v-if="mode === 'uhf'" />
        <AePanel v-else />
      </div>
    </div>
  </div>
</template>
