<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import { Activity, Gauge, Radio, Settings, ShieldAlert, Waves, LayoutDashboard, ScrollText, Boxes } from "lucide-vue-next";
import { PAGES } from "./router";
import { prefs } from "./lib/prefs";
import { ctrlState, sidecarState, streamState } from "./lib/store";

const route = useRoute();

const ICONS: Record<string, unknown> = {
  dashboard: LayoutDashboard,
  monitor: Activity,
  trend: Gauge,
  analysis: Boxes,
  ae: Waves,
  alarm: ShieldAlert,
  device: Radio,
  settings: Settings,
  log: ScrollText,
};

const connection = computed(() => {
  if (sidecarState.value === "dead") return { label: "内核已停止", tone: "text-app-error" };
  if (sidecarState.value === "restarting") return { label: "内核重启中", tone: "text-app-warning" };
  if (streamState.value === "open" && ctrlState.value === "open") return { label: "已连接", tone: "text-app-success" };
  if (streamState.value === "connecting" || ctrlState.value === "connecting")
    return { label: "连接中", tone: "text-app-warning" };
  return { label: "未连接", tone: "text-app-error" };
});
</script>

<template>
  <div class="flex h-full">
    <aside class="flex w-60 shrink-0 flex-col border-r border-app-border bg-app-surface">
      <div class="px-4 py-3">
        <div class="text-sm font-semibold">{{ prefs.systemName }}</div>
        <div class="text-[11px] text-app-muted">UHF / AE 双模态</div>
      </div>
      <nav class="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-2">
        <RouterLink
          v-for="page in PAGES"
          :key="page.key"
          :to="page.path"
          class="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] no-underline transition-colors"
          :class="
            route.name === page.key
              ? 'bg-app-primary/10 font-medium text-app-primary'
              : 'text-app-muted hover:bg-app-surface-variant hover:text-app-fg'
          "
        >
          <component :is="ICONS[page.key]" :size="16" :stroke-width="1.75" />
          <span class="flex-1">{{ page.label }}</span>
          <kbd class="text-[10px] text-app-muted/70">{{ page.shortcut }}</kbd>
        </RouterLink>
      </nav>
    </aside>

    <div class="flex min-w-0 flex-1 flex-col">
      <header class="flex h-12 shrink-0 items-center justify-end gap-3 border-b border-app-border px-4">
        <span class="text-[13px] text-app-muted">{{ route.meta.label }}</span>
        <span class="flex items-center gap-1.5 text-[12px]" :class="connection.tone">
          <span class="h-1.5 w-1.5 rounded-full bg-current" />
          {{ connection.label }}
        </span>
      </header>
      <main class="min-h-0 flex-1 overflow-auto p-4">
        <RouterView />
      </main>
    </div>
  </div>
</template>
