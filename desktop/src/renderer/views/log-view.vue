<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Download } from "lucide-vue-next";
import { logs } from "@/renderer/lib/store";
import { formatStamp } from "@/renderer/lib/prefs";
import type { LogLine } from "@/shared/protocol";

const LEVELS = [
  { key: "ALL", label: "全部" },
  { key: "DEBUG", label: "调试" },
  { key: "INFO", label: "信息" },
  { key: "WARNING", label: "警告" },
  { key: "ERROR", label: "错误" },
] as const;

const LEVEL_STYLE: Record<string, string> = {
  ERROR: "text-app-error",
  CRITICAL: "text-app-error",
  WARNING: "text-app-warning",
  INFO: "text-app-fg",
  DEBUG: "text-app-muted",
};

const ROW_HEIGHT = 26;
const OVERSCAN = 12;

const filter = ref<string>("ALL");
const query = ref("");
const expanded = ref<number | null>(null);
const scrollEl = ref<HTMLElement>();
const scrollTop = ref(0);
const viewHeight = ref(600);

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  return logs.value.filter((row) => {
    if (filter.value !== "ALL" && row.level !== filter.value) return false;
    if (!q) return true;
    return row.message.toLowerCase().includes(q) || row.logger.toLowerCase().includes(q);
  });
});

const counts = computed(() => {
  const out: Record<string, number> = { ALL: logs.value.length };
  for (const row of logs.value) out[row.level] = (out[row.level] ?? 0) + 1;
  return out;
});

/**
 * 只渲染可视窗口内的行。旧版 log_page 不停往 QTextEdit 里 append，上万行会明显
 * 卡顿；这里用上下占位块撑出总高，DOM 节点数恒定在几十个。
 * 行高会因展开详情而变，故窗口按平均行高估算后多留 OVERSCAN 行余量。
 */
const window_ = computed(() => {
  const total = filtered.value.length;
  const start = Math.max(0, Math.floor(scrollTop.value / ROW_HEIGHT) - OVERSCAN);
  const end = Math.min(total, Math.ceil((scrollTop.value + viewHeight.value) / ROW_HEIGHT) + OVERSCAN);
  return { start, end, padTop: start * ROW_HEIGHT, padBottom: Math.max(0, (total - end) * ROW_HEIGHT) };
});

const visible = computed(() => filtered.value.slice(window_.value.start, window_.value.end));

function onScroll(): void {
  scrollTop.value = scrollEl.value?.scrollTop ?? 0;
}

let ro: ResizeObserver | null = null;
onMounted(() => {
  const el = scrollEl.value;
  if (!el) return;
  viewHeight.value = el.clientHeight;
  ro = new ResizeObserver(() => (viewHeight.value = el.clientHeight));
  ro.observe(el);
});
onBeforeUnmount(() => ro?.disconnect());

function exportCsv(): void {
  const header = "timestamp,level,logger,module,line,message\n";
  const body = filtered.value
    .map((r) =>
      [
        new Date(r.ts * 1000).toISOString(),
        r.level,
        r.logger,
        r.module,
        r.line,
        `"${r.message.replace(/"/g, '""')}"`,
      ].join(","),
    )
    .join("\n");
  // BOM 让 Excel 正确按 UTF-8 打开，否则中文列会乱码
  const url = URL.createObjectURL(new Blob(["" + header + body], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `pd-log-${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function detailOf(row: LogLine): string {
  return `${row.logger} · ${row.module}:${row.line} · seq=${row.seq}`;
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-2">
    <div class="flex shrink-0 items-center gap-2">
      <div class="flex overflow-hidden rounded-md border border-app-border">
        <button
          v-for="lv in LEVELS"
          :key="lv.key"
          class="px-2.5 py-1 text-[12px] transition-colors"
          :class="filter === lv.key ? 'bg-app-primary text-white' : 'bg-app-surface text-app-muted hover:bg-app-surface-variant'"
          @click="filter = lv.key"
        >
          {{ lv.label }}<span v-if="counts[lv.key]" class="tabular ml-1 opacity-70">{{ counts[lv.key] }}</span>
        </button>
      </div>
      <input v-model="query" placeholder="搜索日志…" class="w-56 rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px]" />
      <span class="text-[11px] text-app-muted">显示 {{ filtered.length }} / {{ logs.length }} 条</span>
      <button
        :disabled="!filtered.length"
        class="ml-auto flex items-center gap-1.5 rounded-md border border-app-border px-2.5 py-1 text-[12px] disabled:opacity-40"
        @click="exportCsv"
      >
        <Download :size="13" /> 导出日志
      </button>
    </div>

    <div
      ref="scrollEl"
      class="min-h-0 flex-1 overflow-auto rounded-lg border border-app-border bg-app-surface"
      @scroll.passive="onScroll"
    >
      <div :style="{ height: `${window_.padTop}px` }" />
      <div
        v-for="row in visible"
        :key="row.seq"
        class="cursor-pointer border-b border-app-border/40 px-2 hover:bg-app-surface-variant/60"
        :style="{ height: `${expanded === row.seq ? ROW_HEIGHT + 16 : ROW_HEIGHT}px` }"
        @click="expanded = expanded === row.seq ? null : row.seq"
      >
        <div class="flex gap-3 font-mono text-[11px]" :style="{ lineHeight: `${ROW_HEIGHT}px` }">
          <span class="w-[150px] shrink-0 text-app-muted">{{ formatStamp(row.ts * 1000) }}</span>
          <span class="w-14 shrink-0" :class="LEVEL_STYLE[row.level] ?? ''">{{ row.level }}</span>
          <span class="w-44 shrink-0 truncate text-app-muted">{{ row.logger }}</span>
          <span class="min-w-0 flex-1 truncate">{{ row.message }}</span>
        </div>
        <div v-if="expanded === row.seq" class="truncate font-mono text-[10px] text-app-muted" :style="{ lineHeight: '16px' }">
          {{ detailOf(row) }}
        </div>
      </div>
      <div :style="{ height: `${window_.padBottom}px` }" />
      <p v-if="!filtered.length" class="px-3 py-8 text-center text-[12px] text-app-muted">
        {{ logs.length ? "没有匹配当前筛选的日志" : "暂无日志" }}
      </p>
    </div>
  </div>
</template>
