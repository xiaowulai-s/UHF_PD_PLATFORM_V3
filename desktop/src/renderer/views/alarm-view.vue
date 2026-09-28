<script setup lang="ts">
import { alarms } from "@/renderer/lib/store";

const LEVEL_STYLE: Record<string, string> = {
  critical: "text-app-error",
  high: "text-app-error",
  warning: "text-app-warning",
  medium: "text-app-warning",
  info: "text-app-muted",
  low: "text-app-muted",
};

function timeOf(row: Record<string, unknown>): string {
  const ts = Number(row.timestamp ?? 0);
  return ts ? new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "—";
}
</script>

<template>
  <div class="flex h-full flex-col gap-2">
    <div class="flex items-center justify-between">
      <span class="text-[13px] font-medium">实时告警</span>
      <span class="text-[11px] text-app-muted">
        {{ alarms.length }} 条 · 历史告警需在 M3 接入 storage.query_alarms
      </span>
    </div>
    <div class="min-h-0 flex-1 overflow-auto rounded-lg border border-app-border bg-app-surface">
      <table v-if="alarms.length" class="w-full text-[12px]">
        <thead class="sticky top-0 bg-app-surface text-app-muted">
          <tr class="border-b border-app-border">
            <th class="px-3 py-1.5 text-left font-normal">时间</th>
            <th class="px-3 py-1.5 text-left font-normal">级别</th>
            <th class="px-3 py-1.5 text-left font-normal">设备 / 通道</th>
            <th class="px-3 py-1.5 text-right font-normal">幅值</th>
            <th class="px-3 py-1.5 text-left font-normal">说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in alarms" :key="i" class="border-b border-app-border/50">
            <td class="tabular px-3 py-1.5">{{ timeOf(row) }}</td>
            <td class="px-3 py-1.5" :class="LEVEL_STYLE[String(row.level)] ?? ''">{{ row.level }}</td>
            <td class="px-3 py-1.5">{{ row.device_id }} / {{ row.channel_id ?? "—" }}</td>
            <td class="tabular px-3 py-1.5 text-right">{{ Number(row.amplitude ?? 0).toFixed(1) }}</td>
            <td class="px-3 py-1.5 text-app-muted">{{ row.description }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="px-3 py-8 text-center text-[12px] text-app-muted">
        暂无告警。告警走无损通道，不会因客户端落后而丢失。
      </p>
    </div>
  </div>
</template>
