<script setup lang="ts">
import { computed } from "vue";
import { frameCount, latest, stats, streamState } from "@/renderer/lib/store";
import { TOPICS, type Topic } from "@/shared/protocol";

const CARDS = [
  { key: "pd_events_detected", label: "局放事件", suffix: "次" },
  { key: "waveforms_processed", label: "已处理波形", suffix: "帧" },
  { key: "observed_max_amplitude", label: "最大幅值", suffix: "mV" },
  { key: "online_count", label: "在线数据源", suffix: "" },
] as const;

function metric(key: string): string {
  const value = stats.value?.[key as keyof typeof stats.value];
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(1);
  return "—";
}

const transport = computed(() => stats.value?.transport ?? null);
const uptime = computed(() => {
  const s = transport.value?.uptime_s;
  return s === undefined ? "—" : `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒`;
});

/** 逐 topic 收帧计数：这是验证"边界是否真的在流动"最直接的观测量 */
const received = computed(() =>
  (Object.keys(TOPICS) as Topic[]).map((topic) => ({ topic, count: frameCount[topic] ?? 0 })),
);
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="grid grid-cols-4 gap-3">
      <div v-for="card in CARDS" :key="card.key" class="rounded-lg border border-app-border bg-app-surface p-3">
        <div class="text-[11px] text-app-muted">{{ card.label }}</div>
        <div class="tabular mt-1 text-2xl font-semibold">
          {{ metric(card.key) }}<span class="ml-1 text-xs font-normal text-app-muted">{{ card.suffix }}</span>
        </div>
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
        <span class="text-[13px] font-medium">跨进程边界吞吐</span>
        <span class="text-[11px] text-app-muted">
          连接 {{ streamState }} · 内核运行 {{ uptime }}
        </span>
      </div>
      <table class="w-full text-[12px]">
        <thead class="text-app-muted">
          <tr class="border-b border-app-border">
            <th class="px-3 py-1.5 text-left font-normal">topic</th>
            <th class="px-3 py-1.5 text-right font-normal">已收帧</th>
            <th class="px-3 py-1.5 text-right font-normal">投递等级</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in received" :key="row.topic" class="border-b border-app-border/50">
            <td class="px-3 py-1.5 font-mono">{{ row.topic }}</td>
            <td class="tabular px-3 py-1.5 text-right">{{ row.count }}</td>
            <td class="px-3 py-1.5 text-right text-app-muted">{{ TOPICS[row.topic] }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="transport" class="border-t border-app-border px-3 py-2 text-[11px] text-app-muted">
        服务端发出 {{ transport.sent_frames }} 帧 · 因滞后丢弃 {{ transport.dropped_frames }} 帧 · 当前客户端
        {{ transport.clients }}
      </div>
      <div v-else class="border-t border-app-border px-3 py-2 text-[11px] text-app-muted">
        尚未收到 stats 帧
      </div>
    </section>

    <section v-if="latest.waveform" class="rounded-lg border border-app-border bg-app-surface p-3">
      <div class="text-[13px] font-medium">最新波形</div>
      <div class="mt-1 text-[11px] text-app-muted">
        {{ (latest.waveform.m as { shape: number[] }).shape }} ·
        {{ (latest.waveform.m as { sensor_type: string }).sensor_type }} ·
        {{ (latest.waveform.m as { sample_rate: number }).sample_rate }} Hz
      </div>
    </section>
  </div>
</template>
