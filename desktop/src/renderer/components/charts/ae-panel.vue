<script setup lang="ts">
/**
 * AE 命中参数面板（M5.1 的 AE 模式）。
 *
 * 对齐旧 ui/widgets/ae_parameters_widget.py：6 个关键特征量 + 一张滚动历史表。
 * 旧版那张表是 100 条，这里取 200 条上限但默认只渲染前 12 行——虚拟列表对 12 行
 * 是纯开销，而且旧版页面上也就看得见这么多。
 *
 * 包络曲线不在这里画：内核 AE 管线已把包络作为 `waveform` 帧重发（且带
 * sample_rate=2 MHz），左侧波形面板在 AE 模式下显示的就是包络，重复画一份
 * 只会多一条互相矛盾的时间轴。
 */

import { computed } from "vue";
import { aeHits } from "@/renderer/lib/store";
import type { AeHit } from "@/shared/protocol";
/** 单位写死在这里，别让模板去猜：这些是内核字段的物理含义 */
const FIELDS: { key: keyof AeHit; label: string; unit: string; digits: number }[] = [
  { key: "amplitude", label: "幅值", unit: "mV", digits: 1 },
  { key: "rise_time_us", label: "上升时间", unit: "µs", digits: 1 },
  { key: "duration_us", label: "持续时间", unit: "µs", digits: 1 },
  { key: "counts", label: "振铃计数", unit: "", digits: 0 },
  { key: "avg_frequency_khz", label: "平均频率", unit: "kHz", digits: 1 },
  { key: "marse_energy", label: "MARSE 能量", unit: "", digits: 2 },
  { key: "rms", label: "有效值", unit: "", digits: 2 },
  { key: "asl_db", label: "ASL", unit: "dB", digits: 1 },
];

const latest = computed<AeHit | null>(() => aeHits.value[0] ?? null);
const rows = computed(() => aeHits.value.slice(0, 12));

function fmt(hit: AeHit | null, key: keyof AeHit, digits: number): string {
  if (!hit) return "—";
  const value = hit[key];
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  // MARSE 能量量级跨度大，固定小数位会糊成 0.00，指数更可读
  if (digits === 2 && Math.abs(value) < 0.01) return value.toExponential(2);
  return value.toFixed(digits);
}

/**
 * 平均幅值：只有"最新一条"看不出 AE 源强弱，均值是最省事的补充量。
 *
 * 刻意不给"命中率"——列表被 MAX_AE_HITS 截断后，条数/秒数 会随饱和而失真，
 * 一个会骗人的指标不如不放。
 */
const summary = computed(() => {
  const amps = aeHits.value.map((h) => h.amplitude).filter((a) => Number.isFinite(a));
  if (!amps.length) return { meanAmp: "—" };
  return { meanAmp: (amps.reduce((a, b) => a + b, 0) / amps.length).toFixed(1) };
});
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="flex items-center justify-between gap-2 pb-1.5">
      <span class="text-[12px] font-medium">AE 命中参数</span>
      <span class="text-[11px] text-app-muted">
        累计 {{ aeHits.length }} 次{{ latest ? ` · 均值 ${summary.meanAmp}` : "" }}
      </span>
    </div>

    <p v-if="!latest" class="rounded-md bg-app-surface-variant/60 px-3 py-4 text-center text-[12px] text-app-muted">
      尚未收到 ae_hit 帧。AE 仿真的命中密度由 <code>--pulse-rate</code> 决定
      （默认 5/s，约 5 秒才一条，验收时建议给 200~1000）。
    </p>

    <template v-else>
      <div class="grid grid-cols-4 gap-x-3 gap-y-2 border-b border-app-border pb-2">
        <div v-for="f in FIELDS" :key="f.key" class="flex flex-col">
          <span class="text-[10px] text-app-muted">{{ f.label }}</span>
          <span class="tabular text-[13px] font-medium">
            {{ fmt(latest, f.key, f.digits) }}<span class="ml-0.5 text-[10px] text-app-muted">{{ f.unit }}</span>
          </span>
        </div>
      </div>

      <div class="min-h-0 flex-1 overflow-auto">
        <table class="w-full text-[11px]">
          <thead class="sticky top-0 bg-app-surface text-app-muted">
            <tr class="border-b border-app-border">
              <th class="px-1.5 py-1 text-right font-normal">幅值</th>
              <th class="px-1.5 py-1 text-right font-normal">上升 µs</th>
              <th class="px-1.5 py-1 text-right font-normal">持续 µs</th>
              <th class="px-1.5 py-1 text-right font-normal">计数</th>
              <th class="px-1.5 py-1 text-right font-normal">均频 kHz</th>
              <th class="px-1.5 py-1 text-right font-normal">相位°</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(h, i) in rows" :key="i" class="border-b border-app-border/40">
              <td class="tabular px-1.5 py-1 text-right">{{ h.amplitude.toFixed(1) }}</td>
              <td class="tabular px-1.5 py-1 text-right">{{ h.rise_time_us.toFixed(1) }}</td>
              <td class="tabular px-1.5 py-1 text-right">{{ h.duration_us.toFixed(1) }}</td>
              <td class="tabular px-1.5 py-1 text-right">{{ h.counts }}</td>
              <td class="tabular px-1.5 py-1 text-right">{{ h.avg_frequency_khz.toFixed(1) }}</td>
              <td class="tabular px-1.5 py-1 text-right">{{ h.phase_deg.toFixed(0) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
