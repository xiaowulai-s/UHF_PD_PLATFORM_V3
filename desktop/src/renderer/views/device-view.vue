<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ChevronDown, ChevronRight, Play, Plus, RefreshCw, Square, Trash2 } from "lucide-vue-next";
import { call, stats, waitCtrlOpen } from "@/renderer/lib/store";
import { formatStamp } from "@/renderer/lib/prefs";

interface ChannelRow {
  channel_index: number;
  name: string;
  enabled: boolean;
  status: number;
  coupling_type: string;
  sensor_type: string | null;
  gain: number;
  attenuation_db: number;
  frequency_min_mhz: number;
  frequency_max_mhz: number;
}
interface DeviceRow {
  id: string;
  name: string;
  device_type: string;
  host: string | null;
  tcp_port: number;
  udp_port: number;
  firmware_version: string | null;
  status: number;
  channel_count: number;
  sample_rate_hz: number;
  use_simulator: boolean;
  last_connected_at: string | null;
  channels: ChannelRow[];
}

/** 字段集必须与 core/protocol/write_rpc.py 的白名单一致，多传的列会被后端丢弃 */
const COUPLING = ["UHF", "HFCT", "TEV", "AE"];
const DEVICE_TYPES = ["fpga_acq", "simulator", "gateway"];
const STATUS_LABEL: Record<number, string> = { 0: "离线", 1: "在线", 2: "告警", 3: "错误" };

/**
 * 运行时来源（`source.*`）与上面的设备配置是两套对象：
 * 前者是 DataSourceManager 内存里正在跑的来源，后者是 SQLite 里的持久化配置。
 * 移除来源不会删设备配置，删设备配置也不会停来源。
 */
interface SourceRow {
  id: string;
  name?: string;
  type?: string;
  sensor?: string;
  status?: string;
  pd_type?: string;
  fps?: number;
  generated?: number;
  host?: string;
  port?: number;
  channels?: number;
  packets?: number;
  errors?: number;
  reconnects?: number;
}
interface SourceListResult {
  sources: Record<string, SourceRow>;
  count: number;
  active_source: string | null;
}

const devices = ref<DeviceRow[]>([]);
const loading = ref(true);
const error = ref("");
const notice = ref("");
const query = ref("");
const expanded = ref<Set<string>>(new Set());
const editing = ref<string | null>(null);
const pendingDelete = ref<string | null>(null);
const pendingSourceRemove = ref<string | null>(null);
const busy = ref("");

/** 运行时来源清单来自 source.list；运行态另由 stats 帧覆盖（见 runtimeRows） */
const runtime = ref<SourceListResult>({ sources: {}, count: 0, active_source: null });

const draft = reactive({
  id: "",
  name: "",
  device_type: "fpga_acq",
  host: "",
  tcp_port: 5000,
  udp_port: 6000,
  channel_count: 4,
  sample_rate_hz: 100_000_000,
  use_simulator: false,
});

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return devices.value;
  return devices.value.filter(
    (d) => d.id.toLowerCase().includes(q) || d.name.toLowerCase().includes(q) || (d.host ?? "").toLowerCase().includes(q),
  );
});

/** 在线状态来自内核的 stats 帧（数据源真实运行态），不是数据库里的 status 列 */
const liveSourceState = computed(() => {
  const out: Record<string, string> = {};
  for (const [sid, entry] of Object.entries(stats.value?.sources ?? {})) {
    out[sid] = (entry as { status?: string }).status ?? "unknown";
  }
  return out;
});

const SOURCE_STATE_LABEL: Record<string, string> = {
  running: "采集中",
  stopped: "已停止",
  error: "错误",
  reconnecting: "重连中",
};

/**
 * 运行态以 stats 帧（每 2s）为准，source.list 只在挂载与每次动作后拉。
 *
 * 两处刻意不各写一份真相：列表结构（名称/类型/参数）来自 source.list，
 * status 一律取 stats——因为数据源自身的断线重连不经过 RPC，
 * 只有 stats 会反映出来。
 */
const runtimeRows = computed(() => {
  const live = stats.value?.sources ?? {};
  return Object.entries(runtime.value.sources).map(([sid, row]) => ({
    ...row,
    id: sid,
    status: (live[sid] as { status?: string } | undefined)?.status ?? row.status ?? "unknown",
  }));
});

function sourceStateClass(status: string): string {
  if (status === "running") return "bg-app-success/15 text-app-success";
  if (status === "error") return "bg-app-error/15 text-app-error";
  if (status === "reconnecting") return "bg-app-warning/15 text-app-warning";
  return "bg-app-surface-variant text-app-muted";
}

function sourceDetail(src: SourceRow): string {
  if (src.type === "simulator") {
    return [src.pd_type, src.fps ? `${src.fps} FPS` : "", src.generated ? `${src.generated} 帧` : ""].filter(Boolean).join(" · ") || "—";
  }
  return [
    src.host ?? "",
    src.port ? `:${src.port}` : "",
    src.channels ? `${src.channels} 通道` : "",
    src.reconnects ? `重连 ${src.reconnects}` : "",
  ]
    .filter(Boolean)
    .join(" ") || "—";
}

async function refreshRuntime(): Promise<void> {
  try {
    runtime.value = await call<SourceListResult>("source.list");
  } catch (err) {
    error.value = `运行时来源读取失败：${(err as Error).message}`;
  }
}

async function sourceAction(method: string, sourceId: string, label: string): Promise<void> {
  busy.value = `src:${sourceId}`;
  error.value = "";
  try {
    await call(method, { source_id: sourceId });
    notice.value = `来源 ${sourceId} ${label}`;
    pendingSourceRemove.value = null;
    await refreshRuntime();
  } catch (err) {
    error.value = `来源 ${sourceId} ${label}失败：${(err as Error).message}`;
  } finally {
    busy.value = "";
  }
}

function resetDraft(next?: Partial<typeof draft>): void {
  Object.assign(draft, {
    id: "",
    name: "",
    device_type: "fpga_acq",
    host: "",
    tcp_port: 5000,
    udp_port: 6000,
    channel_count: 4,
    sample_rate_hz: 100_000_000,
    use_simulator: false,
    ...next,
  });
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    await waitCtrlOpen();
    devices.value = await call<DeviceRow[]>("device.list");
  } catch (err) {
    error.value = `加载失败：${(err as Error).message}`;
  } finally {
    loading.value = false;
  }
}

function toggleRow(id: string): void {
  const next = new Set(expanded.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expanded.value = next;
}

function startEdit(device?: DeviceRow): void {
  if (device) {
    resetDraft({
      id: device.id,
      name: device.name,
      device_type: device.device_type,
      host: device.host ?? "",
      tcp_port: device.tcp_port,
      udp_port: device.udp_port,
      channel_count: device.channel_count,
      sample_rate_hz: device.sample_rate_hz,
      use_simulator: Boolean(device.use_simulator),
    });
    editing.value = device.id;
  } else {
    resetDraft();
    editing.value = "__new__";
  }
}

function validate(): string | null {
  if (!draft.id.trim()) return "设备 ID 不能为空";
  if (!/^[A-Za-z0-9_.-]{1,64}$/.test(draft.id.trim())) return "设备 ID 只允许字母、数字、_ . -，长度 1-64";
  if (!draft.name.trim()) return "设备名称不能为空";
  if (!Number.isInteger(draft.tcp_port) || draft.tcp_port < 1 || draft.tcp_port > 65535) return "TCP 端口需在 1-65535";
  if (!Number.isInteger(draft.udp_port) || draft.udp_port < 1 || draft.udp_port > 65535) return "UDP 端口需在 1-65535";
  if (draft.tcp_port === draft.udp_port) return "TCP 与 UDP 端口不能相同";
  if (draft.channel_count < 1 || draft.channel_count > 32) return "通道数需在 1-32";
  if (draft.sample_rate_hz < 1) return "采样率必须为正数";
  if (!draft.use_simulator && !draft.host.trim()) return "非模拟器设备必须填主机地址";
  return null;
}

async function save(): Promise<void> {
  const invalid = validate();
  if (invalid) {
    error.value = invalid;
    return;
  }
  busy.value = draft.id || "__new__";
  error.value = "";
  try {
    const existing = devices.value.find((d) => d.id === draft.id);
    await call("device.save", {
      device: { ...draft },
      // 保存设备会整体替换通道（后端 clear 后重建），所以必须把现有通道一并回传
      channels: (existing?.channels ?? []).map((c) => ({
        channel_index: c.channel_index,
        name: c.name,
        enabled: c.enabled,
        coupling_type: c.coupling_type.toLowerCase(),
        sensor_type: c.sensor_type,
        gain: c.gain,
        attenuation_db: c.attenuation_db,
        frequency_min_mhz: c.frequency_min_mhz,
        frequency_max_mhz: c.frequency_max_mhz,
      })),
    });
    notice.value = existing ? `设备 ${draft.name} 已更新` : `设备 ${draft.name} 已新增`;
    editing.value = null;
    await load();
  } catch (err) {
    error.value = `保存失败：${(err as Error).message}`;
  } finally {
    busy.value = "";
  }
}

async function remove(deviceId: string): Promise<void> {
  busy.value = deviceId;
  error.value = "";
  try {
    const res = await call<{ deleted: boolean }>("device.delete", { device_id: deviceId });
    notice.value = res.deleted ? `设备 ${deviceId} 已删除` : `设备 ${deviceId} 不存在，可能已被删除`;
    pendingDelete.value = null;
    await load();
  } catch (err) {
    error.value = `删除失败：${(err as Error).message}`;
  } finally {
    busy.value = "";
  }
}

async function applyChannel(device: DeviceRow, ch: ChannelRow): Promise<void> {
  const key = `${device.id}:${ch.channel_index}`;
  busy.value = key;
  error.value = "";
  try {
    await call("config.set_channel_coupling", {
      device_id: device.id,
      channel_id: ch.channel_index,
      coupling_type: ch.coupling_type.toLowerCase(),
    });
    await call("device.save", {
      device: { id: device.id, name: device.name, device_type: device.device_type },
      channels: device.channels.map((c) => ({
        channel_index: c.channel_index,
        name: c.name,
        enabled: c.enabled,
        coupling_type: c.coupling_type.toLowerCase(),
        sensor_type: c.sensor_type,
        gain: c.gain,
        attenuation_db: c.attenuation_db,
        frequency_min_mhz: c.frequency_min_mhz,
        frequency_max_mhz: c.frequency_max_mhz,
      })),
    });
    notice.value = `设备 ${device.id} 通道 ${ch.channel_index} 已更新`;
  } catch (err) {
    error.value = `通道更新失败：${(err as Error).message}`;
  } finally {
    busy.value = "";
  }
}

function mhz(sampleRate: number): string {
  return sampleRate >= 1e6 ? `${(sampleRate / 1e6).toFixed(0)} MHz` : String(sampleRate);
}

onMounted(async () => {
  await load();
  // load() 里已经 await 过 waitCtrlOpen，这里可以直接发 RPC
  await refreshRuntime();
});
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-3">
    <div class="flex shrink-0 items-center gap-2">
      <input v-model="query" placeholder="搜索设备 ID / 名称 / 地址…" class="w-72 rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px]" />
      <span class="text-[11px] text-app-muted">{{ filtered.length }} / {{ devices.length }} 台</span>
      <div class="ml-auto flex gap-2">
        <button
          class="flex items-center gap-1.5 rounded-md border border-app-border px-2.5 py-1 text-[12px]"
          :disabled="loading"
          @click="load"
        >
          <RefreshCw :size="13" :class="loading ? 'animate-spin' : ''" /> 刷新
        </button>
        <button
          v-if="editing === null"
          class="flex items-center gap-1.5 rounded-md bg-app-primary px-2.5 py-1 text-[12px] text-white"
          @click="startEdit()"
        >
          <Plus :size="13" /> 新增设备
        </button>
      </div>
    </div>

    <p v-if="error" class="shrink-0 rounded-md bg-app-error/10 px-3 py-2 text-[12px] text-app-error">{{ error }}</p>
    <p v-if="notice" class="shrink-0 rounded-md bg-app-success/10 px-3 py-2 text-[12px] text-app-success">{{ notice }}</p>

    <section class="shrink-0 rounded-lg border border-app-border bg-app-surface">
      <div class="flex flex-wrap items-center gap-2 border-b border-app-border px-4 py-2">
        <span class="text-[13px] font-medium">运行时来源</span>
        <span class="text-[11px] text-app-muted">内核里正在跑的数据源，可不停机启停；切换当前源只影响 UI 展示，不影响采集；与下方设备配置是两套对象，移除来源不会删除设备配置</span>
        <button
          class="ml-auto flex items-center gap-1.5 rounded-md border border-app-border px-2 py-1 text-[11px]"
          @click="refreshRuntime"
        >
          <RefreshCw :size="12" /> 刷新
        </button>
      </div>
      <p v-if="!runtimeRows.length" class="px-4 py-4 text-[12px] text-app-muted">
        内核里还没有运行时来源。可用启动参数指定（如 --sim-type uhf），或经 acquisition.start_simulator 添加。
      </p>
      <table v-else class="w-full text-[12px]">
        <thead class="bg-app-surface text-app-muted">
          <tr class="border-b border-app-border">
            <th class="px-4 py-2 text-left font-normal">来源</th>
            <th class="px-3 py-2 text-left font-normal">类型</th>
            <th class="px-3 py-2 text-left font-normal">参数</th>
            <th class="px-3 py-2 text-left font-normal">状态</th>
            <th class="px-3 py-2 text-right font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="src in runtimeRows" :key="src.id" class="border-b border-app-border/50">
            <td class="px-4 py-2">
              <div class="flex items-center gap-1.5">
                <span>{{ src.name || src.id }}</span>
                <span
                  v-if="src.id === runtime.active_source"
                  class="rounded bg-app-primary/15 px-1.5 py-0.5 text-[10px] text-app-primary"
                >
                  当前
                </span>
              </div>
              <div class="font-mono text-[10px] text-app-muted">{{ src.id }}</div>
            </td>
            <td class="px-3 py-2">
              {{ src.type ?? "—" }}<span class="text-app-muted"> · {{ src.sensor ?? "—" }}</span>
            </td>
            <td class="px-3 py-2 text-[11px] text-app-muted">{{ sourceDetail(src) }}</td>
            <td class="px-3 py-2">
              <span class="rounded px-1.5 py-0.5 text-[11px]" :class="sourceStateClass(src.status)">
                {{ SOURCE_STATE_LABEL[src.status] ?? src.status }}
              </span>
            </td>
            <td class="px-3 py-2 text-right">
              <div class="flex justify-end gap-1.5">
                <button
                  :disabled="busy === `src:${src.id}` || src.status !== 'stopped'"
                  class="flex items-center gap-1 rounded-md border border-app-border px-2 py-1 text-[11px] disabled:opacity-40"
                  @click="sourceAction('source.start', src.id, '已启动')"
                >
                  <Play :size="11" /> 启动
                </button>
                <button
                  :disabled="busy === `src:${src.id}` || src.status !== 'running'"
                  class="flex items-center gap-1 rounded-md border border-app-border px-2 py-1 text-[11px] disabled:opacity-40"
                  @click="sourceAction('source.stop', src.id, '已停止')"
                >
                  <Square :size="11" /> 停止
                </button>
                <button
                  :disabled="busy === `src:${src.id}` || src.id === runtime.active_source"
                  :title="src.id === runtime.active_source ? '此来源已是当前 UI 展示源' : '只切换 UI 展示源，不影响后台采集'"
                  class="rounded-md border border-app-border px-2 py-1 text-[11px] disabled:opacity-40"
                  @click="sourceAction('source.set_active', src.id, '已设为当前 UI 展示源')"
                >
                  {{ src.id === runtime.active_source ? "当前源" : "设为当前" }}
                </button>
                <button
                  v-if="pendingSourceRemove !== src.id"
                  class="flex items-center gap-1 rounded-md border border-app-border px-2 py-1 text-[11px] text-app-muted"
                  @click="pendingSourceRemove = src.id"
                >
                  <Trash2 :size="11" /> 移除
                </button>
                <template v-else>
                  <span class="self-center text-[11px] text-app-error">确认移除？</span>
                  <button
                    :disabled="busy === `src:${src.id}`"
                    class="rounded-md bg-app-error px-2 py-1 text-[11px] text-white disabled:opacity-50"
                    @click="sourceAction('source.remove', src.id, '已移除')"
                  >
                    {{ busy === `src:${src.id}` ? "…" : "移除" }}
                  </button>
                  <button class="rounded-md border border-app-border px-2 py-1 text-[11px]" @click="pendingSourceRemove = null">取消</button>
                </template>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="editing !== null" class="shrink-0 rounded-lg border border-app-border bg-app-surface">
      <div class="border-b border-app-border px-4 py-2 text-[13px] font-medium">
        {{ editing === "__new__" ? "新增设备" : `编辑设备 ${editing}` }}
      </div>
      <div class="grid grid-cols-4 gap-x-6 gap-y-3 p-4">
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">设备 ID *</span>
          <input v-model="draft.id" :disabled="editing !== '__new__'" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 font-mono text-[12px] disabled:opacity-50" placeholder="DEV-002" />
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">设备名称 *</span>
          <input v-model="draft.name" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[12px]" placeholder="UHF-超高频采集-02" />
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">设备类型</span>
          <select v-model="draft.device_type" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[12px]">
            <option v-for="t in DEVICE_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">主机地址</span>
          <input v-model="draft.host" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 font-mono text-[12px]" placeholder="192.168.1.101" />
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">TCP 端口</span>
          <input v-model.number="draft.tcp_port" type="number" class="tabular rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[12px]" />
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">UDP 端口</span>
          <input v-model.number="draft.udp_port" type="number" class="tabular rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[12px]" />
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">通道数</span>
          <input v-model.number="draft.channel_count" type="number" min="1" max="32" class="tabular rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[12px]" />
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">采样率 (Hz)</span>
          <input v-model.number="draft.sample_rate_hz" type="number" step="1000000" class="tabular rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[12px]" />
        </label>
        <label class="col-span-4 flex items-center gap-2 text-[12px]">
          <input v-model="draft.use_simulator" type="checkbox" class="accent-[var(--color-primary)]" />
          <span>使用模拟器数据源（无需 FPGA 硬件）</span>
        </label>
      </div>
      <div class="flex justify-end gap-2 border-t border-app-border px-4 py-2.5">
        <button class="rounded-md border border-app-border px-3 py-1.5 text-[12px]" @click="editing = null">取消</button>
        <button
          :disabled="busy !== ''"
          class="rounded-md bg-app-primary px-3 py-1.5 text-[12px] text-white disabled:opacity-50"
          @click="save"
        >
          {{ busy !== "" ? "保存中…" : "保存" }}
        </button>
      </div>
    </section>

    <div class="min-h-0 flex-1 overflow-auto rounded-lg border border-app-border bg-app-surface">
      <p v-if="loading && !devices.length" class="px-4 py-8 text-center text-[12px] text-app-muted">加载中…</p>
      <p v-else-if="!filtered.length" class="px-4 py-8 text-center text-[12px] text-app-muted">
        {{ devices.length ? "没有匹配搜索条件的设备" : "内核里还没有已保存的设备配置，点右上角「新增设备」创建第一台。" }}
      </p>
      <table v-else class="w-full text-[12px]">
        <thead class="sticky top-0 bg-app-surface text-app-muted">
          <tr class="border-b border-app-border">
            <th class="w-8 px-2 py-2" />
            <th class="px-3 py-2 text-left font-normal">设备</th>
            <th class="px-3 py-2 text-left font-normal">地址</th>
            <th class="px-3 py-2 text-left font-normal">类型</th>
            <th class="px-3 py-2 text-right font-normal">采样率</th>
            <th class="px-3 py-2 text-right font-normal">通道</th>
            <th class="px-3 py-2 text-left font-normal">运行状态</th>
            <th class="px-3 py-2 text-right font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="device in filtered" :key="device.id">
            <tr class="border-b border-app-border/50 hover:bg-app-surface-variant/50">
              <td class="px-2 py-2 align-middle">
                <button class="text-app-muted" :aria-expanded="expanded.has(device.id)" @click="toggleRow(device.id)">
                  <ChevronDown v-if="expanded.has(device.id)" :size="14" />
                  <ChevronRight v-else :size="14" />
                </button>
              </td>
              <td class="px-3 py-2">
                <div>{{ device.name }}</div>
                <div class="font-mono text-[10px] text-app-muted">{{ device.id }}</div>
              </td>
              <td class="px-3 py-2 font-mono text-[11px]">
                {{ device.host || "—" }}
                <span class="text-app-muted">:{{ device.udp_port }}</span>
              </td>
              <td class="px-3 py-2">{{ device.device_type }}</td>
              <td class="tabular px-3 py-2 text-right">{{ mhz(device.sample_rate_hz) }}</td>
              <td class="tabular px-3 py-2 text-right">{{ device.channels.length || device.channel_count }}</td>
              <td class="px-3 py-2">
                <span
                  class="rounded px-1.5 py-0.5 text-[11px]"
                  :class="liveSourceState[device.id] ? 'bg-app-success/15 text-app-success' : 'bg-app-surface-variant text-app-muted'"
                >
                  {{ liveSourceState[device.id] ? "采集中" : STATUS_LABEL[device.status] ?? "未知" }}
                </span>
              </td>
              <td class="px-3 py-2 text-right">
                <div class="flex justify-end gap-1.5">
                  <button class="rounded-md border border-app-border px-2 py-1 text-[11px]" @click="startEdit(device)">编辑</button>
                  <button
                    v-if="pendingDelete !== device.id"
                    class="flex items-center gap-1 rounded-md border border-app-border px-2 py-1 text-[11px] text-app-muted"
                    @click="pendingDelete = device.id"
                  >
                    <Trash2 :size="12" /> 删除
                  </button>
                  <template v-else>
                    <span class="self-center text-[11px] text-app-error">确认删除？</span>
                    <button
                      :disabled="busy === device.id"
                      class="rounded-md bg-app-error px-2 py-1 text-[11px] text-white disabled:opacity-50"
                      @click="remove(device.id)"
                    >
                      {{ busy === device.id ? "…" : "删除" }}
                    </button>
                    <button class="rounded-md border border-app-border px-2 py-1 text-[11px]" @click="pendingDelete = null">取消</button>
                  </template>
                </div>
              </td>
            </tr>
            <tr v-if="expanded.has(device.id)">
              <td colspan="8" class="bg-app-bg/60 px-6 py-3">
                <div v-if="!device.channels.length" class="text-[11px] text-app-muted">
                  该设备还没有通道记录。声明的通道数为 {{ device.channel_count }}，保存一次即可建立通道行。
                </div>
                <table v-else class="w-full text-[11px]">
                  <thead class="text-app-muted">
                    <tr class="border-b border-app-border/60">
                      <th class="py-1.5 text-left font-normal">通道</th>
                      <th class="py-1.5 text-left font-normal">名称</th>
                      <th class="py-1.5 text-left font-normal">耦合模式</th>
                      <th class="py-1.5 text-right font-normal">增益</th>
                      <th class="py-1.5 text-right font-normal">衰减 (dB)</th>
                      <th class="py-1.5 text-right font-normal">频带 (MHz)</th>
                      <th class="py-1.5 text-right font-normal">启用</th>
                      <th class="py-1.5 text-right font-normal">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="ch in device.channels" :key="ch.channel_index" class="border-b border-app-border/40">
                      <td class="tabular py-1.5">{{ ch.channel_index }}</td>
                      <td class="py-1.5">
                        <input v-model="ch.name" class="w-32 rounded border border-app-border bg-app-bg px-1.5 py-0.5 text-[11px]" />
                      </td>
                      <td class="py-1.5">
                        <select v-model="ch.coupling_type" class="rounded border border-app-border bg-app-bg px-1.5 py-0.5 text-[11px]">
                          <option v-for="c in COUPLING" :key="c" :value="c">{{ c }}</option>
                        </select>
                      </td>
                      <td class="tabular py-1.5 text-right">{{ ch.gain }}</td>
                      <td class="tabular py-1.5 text-right">{{ ch.attenuation_db }}</td>
                      <td class="tabular py-1.5 text-right">{{ ch.frequency_min_mhz }}–{{ ch.frequency_max_mhz }}</td>
                      <td class="py-1.5 text-right">
                        <button
                          class="rounded px-1.5 py-0.5 text-[11px]"
                          :class="ch.enabled ? 'bg-app-success/15 text-app-success' : 'bg-app-surface-variant text-app-muted'"
                          @click="ch.enabled = !ch.enabled"
                        >
                          {{ ch.enabled ? "已启用" : "已停用" }}
                        </button>
                      </td>
                      <td class="py-1.5 text-right">
                        <button
                          :disabled="busy === `${device.id}:${ch.channel_index}`"
                          class="rounded-md border border-app-border px-2 py-0.5 text-[11px] disabled:opacity-40"
                          @click="applyChannel(device, ch)"
                        >
                          {{ busy === `${device.id}:${ch.channel_index}` ? "…" : "应用" }}
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div class="mt-2 text-[10px] text-app-muted">
                  最近连接：{{ device.last_connected_at ? formatStamp(new Date(device.last_connected_at).getTime()) : "从未" }}
                  · 固件 {{ device.firmware_version || "未上报" }}
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
