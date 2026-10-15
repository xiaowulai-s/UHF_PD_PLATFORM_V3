<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Check, RefreshCw } from "lucide-vue-next";
import { alarms, call, isAlarmCleared, waitCtrlOpen } from "@/renderer/lib/store";
import { formatStamp } from "@/renderer/lib/prefs";

interface AlarmRow {
  id: number | null;
  device_id: string;
  channel_id: number | null;
  alarm_type: string;
  level: string;
  amplitude: number;
  threshold: number;
  description: string;
  timestamp: number;
  acknowledged: boolean;
  /**
   * 内核已发 `alarm_cleared`：条件消失。
   *
   * 只对实时帧来的行可能为 true——`alarm_cleared` 说的是"此刻这个条件没了"，
   * 拿它去标注库内三天前那条历史告警是错的，所以历史行恒为 false。
   */
  cleared: boolean;
}

const SEVERITY = [
  { key: "ALL", label: "全部严重程度" },
  { key: "critical", label: "严重" },
  { key: "warning", label: "警告" },
  { key: "info", label: "提示" },
];
const STATES = [
  { key: "ALL", label: "全部状态" },
  { key: "open", label: "未确认" },
  { key: "ack", label: "已确认" },
  { key: "cleared", label: "已消除" },
];

const LEVEL_STYLE: Record<string, string> = {
  critical: "text-app-error",
  high: "text-app-error",
  warning: "text-app-warning",
  medium: "text-app-warning",
  info: "text-app-muted",
  low: "text-app-muted",
};

const severity = ref("ALL");
const state = ref("ALL");
const query = ref("");
const history = ref<AlarmRow[]>([]);
const selected = ref<Set<number>>(new Set());
const error = ref("");
const notice = ref("");
const busy = ref(false);
const loaded = ref(false);

function fromLive(raw: Record<string, unknown>): AlarmRow {
  return {
    id: (raw.alarm_id as number | null) ?? null,
    device_id: String(raw.device_id ?? ""),
    channel_id: (raw.channel_id as number | null) ?? null,
    alarm_type: String(raw.alarm_type ?? ""),
    level: String(raw.level ?? "info"),
    amplitude: Number(raw.amplitude ?? 0),
    threshold: Number(raw.threshold ?? 0),
    description: String(raw.description ?? ""),
    timestamp: Number(raw.timestamp ?? 0),
    acknowledged: false,
    cleared: isAlarmCleared(
      String(raw.device_id ?? ""),
      String(raw.alarm_type ?? ""),
      Number(raw.timestamp ?? 0),
    ),
  };
}

function fromHistory(raw: Record<string, unknown>): AlarmRow {
  const ts = raw.timestamp ? new Date(String(raw.timestamp)).getTime() / 1000 : 0;
  return {
    id: Number(raw.id),
    device_id: String(raw.device_id ?? ""),
    channel_id: (raw.channel_id as number | null) ?? null,
    alarm_type: String(raw.alarm_type ?? ""),
    level: String(raw.level ?? "info"),
    amplitude: Number(raw.amplitude ?? 0),
    threshold: Number(raw.threshold ?? 0),
    description: String(raw.description ?? ""),
    timestamp: ts,
    acknowledged: Boolean(raw.acknowledged),
    cleared: false,
  };
}

/**
 * 实时帧与库内历史按主键去重合并。
 *
 * 落库失败的告警 id 为 null：它只存在于内存，确认了也不会持久化，所以这类行不
 * 允许勾选，并显式标成"未持久化"，而不是伪装成一条可操作的记录。
 */
const merged = computed<AlarmRow[]>(() => {
  const byId = new Map<number, AlarmRow>();
  const unpersisted: AlarmRow[] = [];
  for (const raw of alarms.value) {
    const row = fromLive(raw);
    if (row.id === null) unpersisted.push(row);
    else byId.set(row.id, row);
  }
  for (const row of history.value) if (!byId.has(row.id as number)) byId.set(row.id as number, row);
  return [...unpersisted, ...byId.values()].sort((a, b) => b.timestamp - a.timestamp);
});

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  return merged.value.filter((row) => {
    if (severity.value !== "ALL" && row.level !== severity.value) return false;
    if (state.value === "open" && (row.acknowledged || row.cleared)) return false;
    if (state.value === "ack" && (!row.acknowledged || row.cleared)) return false;
    if (state.value === "cleared" && !row.cleared) return false;
    if (!q) return true;
    return row.description.toLowerCase().includes(q) || row.device_id.toLowerCase().includes(q);
  });
});

/** 已消除的行不再可确认：条件都没了，确认它没有意义。 */
const ackable = computed(() => filtered.value.filter((r) => r.id !== null && !r.acknowledged && !r.cleared));
const allAckableSelected = computed(
  () => ackable.value.length > 0 && ackable.value.every((r) => selected.value.has(r.id as number)),
);

function toggle(id: number | null): void {
  if (id === null) return;
  const next = new Set(selected.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  selected.value = next;
}

function toggleAll(): void {
  selected.value = allAckableSelected.value ? new Set() : new Set(ackable.value.map((r) => r.id as number));
}

async function loadHistory(): Promise<void> {
  error.value = "";
  try {
    await waitCtrlOpen();
    const rows = await call<Record<string, unknown>[]>("storage.query_alarms", { limit: 300 });
    history.value = rows.map(fromHistory);
    loaded.value = true;
  } catch (err) {
    error.value = `历史告警加载失败：${(err as Error).message}`;
  }
}

async function acknowledge(): Promise<void> {
  const ids = [...selected.value];
  if (!ids.length) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const res = await call<{ requested: number; acknowledged: number }>("alarm.acknowledge", {
      alarm_ids: ids,
      acknowledged_by: "operator",
    });
    notice.value = `已确认 ${res.acknowledged} / ${res.requested} 条`;
    selected.value = new Set();
    await loadHistory();
  } catch (err) {
    error.value = `确认失败：${(err as Error).message}`;
  } finally {
    busy.value = false;
  }
}

function timeOf(row: AlarmRow): string {
  return row.timestamp ? formatStamp(row.timestamp * 1000) : "—";
}

onMounted(loadHistory);
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-2">
    <div class="flex shrink-0 flex-wrap items-center gap-2">
      <select v-model="severity" class="rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px]">
        <option v-for="s in SEVERITY" :key="s.key" :value="s.key">{{ s.label }}</option>
      </select>
      <select v-model="state" class="rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px]">
        <option v-for="s in STATES" :key="s.key" :value="s.key">{{ s.label }}</option>
      </select>
      <input v-model="query" placeholder="搜索报警内容…" class="w-56 rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px]" />
      <span class="text-[11px] text-app-muted">{{ filtered.length }} / {{ merged.length }} 条</span>

      <div class="ml-auto flex items-center gap-2">
        <button
          class="flex items-center gap-1.5 rounded-md border border-app-border px-2.5 py-1 text-[12px]"
          :disabled="busy"
          @click="loadHistory"
        >
          <RefreshCw :size="13" :class="busy ? 'animate-spin' : ''" /> 刷新
        </button>
        <button
          :disabled="!selected.size || busy"
          class="flex items-center gap-1.5 rounded-md bg-app-primary px-2.5 py-1 text-[12px] text-white disabled:opacity-40"
          @click="acknowledge"
        >
          <Check :size="13" /> 确认{{ selected.size ? ` (${selected.size})` : "" }}
        </button>
      </div>
    </div>

    <p v-if="error" class="shrink-0 rounded-md bg-app-error/10 px-3 py-2 text-[12px] text-app-error">{{ error }}</p>
    <p v-if="notice" class="shrink-0 rounded-md bg-app-success/10 px-3 py-2 text-[12px] text-app-success">{{ notice }}</p>

    <div class="min-h-0 flex-1 overflow-auto rounded-lg border border-app-border bg-app-surface">
      <table v-if="filtered.length" class="w-full text-[12px]">
        <thead class="sticky top-0 bg-app-surface text-app-muted">
          <tr class="border-b border-app-border">
            <th class="w-8 px-2 py-2">
              <label class="flex h-6 w-6 cursor-pointer items-center justify-center">
                <input
                  type="checkbox"
                  class="accent-[var(--color-primary)]"
                  :disabled="!ackable.length"
                  :checked="allAckableSelected"
                  @change="toggleAll"
                />
              </label>
            </th>
            <th class="px-3 py-2 text-left font-normal">时间</th>
            <th class="px-3 py-2 text-left font-normal">严重程度</th>
            <th class="px-3 py-2 text-left font-normal">报警信息</th>
            <th class="px-3 py-2 text-left font-normal">来源通道</th>
            <th class="px-3 py-2 text-left font-normal">状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, i) in filtered"
            :key="`${row.id ?? 'live'}-${row.timestamp}-${i}`"
            class="border-b border-app-border/50 hover:bg-app-surface-variant/50"
          >
            <td class="px-2 py-2">
              <label
                class="flex h-6 w-6 cursor-pointer items-center justify-center"
                :title="row.id === null ? '该告警未能落库，无法确认' : ''"
              >
                <input
                  type="checkbox"
                  class="accent-[var(--color-primary)]"
                  :disabled="row.id === null || row.acknowledged || row.cleared"
                  :checked="row.id !== null && selected.has(row.id)"
                  @change="toggle(row.id)"
                />
              </label>
            </td>
            <td class="tabular px-3 py-2">{{ timeOf(row) }}</td>
            <td class="px-3 py-2" :class="LEVEL_STYLE[row.level] ?? ''">{{ row.level }}</td>
            <td class="px-3 py-2">
              <div>{{ row.description }}</div>
              <div class="text-[10px] text-app-muted">
                {{ row.alarm_type }} · {{ row.amplitude.toFixed(1) }} / 阈值 {{ row.threshold.toFixed(1) }} mV
              </div>
            </td>
            <td class="px-3 py-2">
              {{ row.device_id }}<span v-if="row.channel_id !== null"> / {{ row.channel_id }}</span>
            </td>
            <td class="px-3 py-2">
              <span
                v-if="row.cleared"
                class="rounded bg-app-info/15 px-1.5 py-0.5 text-[11px] text-app-info"
                title="内核已下发 alarm_cleared：报警条件已消失"
              >
                已消除
              </span>
              <span v-else-if="row.acknowledged" class="rounded bg-app-success/15 px-1.5 py-0.5 text-[11px] text-app-success">已确认</span>
              <span
                v-else-if="row.id === null"
                class="rounded bg-app-warning/15 px-1.5 py-0.5 text-[11px] text-app-warning"
                title="落库失败，仅存在于内存，无法确认"
              >
                未持久化
              </span>
              <span v-else class="rounded bg-app-surface-variant px-1.5 py-0.5 text-[11px] text-app-muted">未确认</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="px-4 py-10 text-center text-[12px] text-app-muted">
        {{ loaded ? "没有匹配当前筛选的告警" : "正在加载历史告警…" }}
      </p>
    </div>
  </div>
</template>
