<script setup lang="ts">
import { computed } from "vue";
import { logs } from "@/renderer/lib/store";

const LEVEL_STYLE: Record<string, string> = {
  ERROR: "text-app-error",
  CRITICAL: "text-app-error",
  WARNING: "text-app-warning",
  INFO: "text-app-fg",
  DEBUG: "text-app-muted",
};

/** 倒序展示，最新在上；日志量大时只渲染尾部，避免 DOM 爆掉 */
const TAIL = 400;
const visible = computed(() => logs.value.slice(-TAIL).reverse());
</script>

<template>
  <div class="flex h-full flex-col gap-2">
    <div class="text-[11px] text-app-muted">
      缓冲 {{ logs.length }} 条 · 渲染最近 {{ Math.min(visible.length, TAIL) }} 条
    </div>
    <div class="min-h-0 flex-1 overflow-auto rounded-lg border border-app-border bg-app-surface p-2">
      <div v-for="row in visible" :key="row.seq" class="flex gap-2 font-mono text-[11px] leading-5">
        <span class="shrink-0 text-app-muted">
          {{ new Date(row.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false }) }}
        </span>
        <span class="w-14 shrink-0" :class="LEVEL_STYLE[row.level] ?? ''">{{ row.level }}</span>
        <span class="w-40 shrink-0 truncate text-app-muted">{{ row.logger }}</span>
        <span class="min-w-0 flex-1 whitespace-pre-wrap break-all">{{ row.message }}</span>
      </div>
      <p v-if="!visible.length" class="px-2 py-6 text-center text-[12px] text-app-muted">
        暂无日志
      </p>
    </div>
  </div>
</template>
