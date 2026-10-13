<script setup lang="ts">
/**
 * AE 特征分析（M6.1）。
 *
 * 需求按 `.design/uhf-pd-light/pages/ae.html` 的观感 + 旧 `ui/pages/ae_page.py` 的口径推导。
 * 设计稿里有、这里**刻意没做**的三样，理由都是"没有数据支撑就会变成按了没反应的死控件"：
 *  - 「AE 特征 / UHF 特征」模式切换：UHF 侧的特征量（PRPD/FFT）已经有实时监测页与趋势页，
 *    在这里再放一个只会重复；AE 页只管 AE。
 *  - 「宽频耦合 / 窄带滤波 / 谐振耦合」：耦合类型是设备通道配置，写路径在设备管理页
 *    （M3.4 已实现 `config.set_channel_coupling`）。在这里再放一份会出现两条互相
 *    矛盾的写入路径。
 *
 * 导出已补（P4）：但**只导数据库历史那一份**（`ae_hits` 表 + 特征散点 + CSV）。
 * 本次会话的累计值不提供导出——它是分钟分桶聚合，导出它等于把聚合结果当原始记录，
 * 复盘时对不上。要原始记录就得先落库。
 *
 * 统计口径见 `lib/ae-stats.ts`：走累计分桶，不用 store 里那个 200 条上限的列表。
 */

import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { AE_FIELDS, aeAggregate, aeChannelKeys, aeHistogram, type AeAggregate } from "@/renderer/lib/ae-stats";
import { DEFAULT_SENSORS, sanitizeSensors, type AeSensor } from "@/renderer/lib/gdop";
import { prefs } from "@/renderer/lib/prefs";
import { call, waitCtrlOpen } from "@/renderer/lib/store";
import GdopMap from "@/renderer/components/charts/gdop-map.vue";
import AeScatter from "@/renderer/components/charts/ae-scatter.vue";

/**
 * 传感器阵列。localStorage 里的值不可信，先过 sanitizeSensors 收敛，
 * 再把收敛结果写回去（这样坏值只会存在到本次打开为止）。
 */
const sensors = ref<AeSensor[]>(sanitizeSensors(prefs.aeSensors.length ? prefs.aeSensors : DEFAULT_SENSORS));
watch(
  sensors,
  (next) => {
    prefs.aeSensors = next.map((s) => ({ ...s }));
  },
  { deep: true },
);

function addSensor(): void {
  const n = sensors.value.length + 1;
  // 新点落在当前包围盒中心附近，免得一加就跑到图外
  const cx = sensors.value.reduce((a, s) => a + s.x, 0) / Math.max(1, sensors.value.length);
  const cy = sensors.value.reduce((a, s) => a + s.y, 0) / Math.max(1, sensors.value.length);
  sensors.value = [...sensors.value, { id: `S${n}`, x: cx, y: cy }];
}

function removeSensor(index: number): void {
  sensors.value = sensors.value.filter((_, i) => i !== index);
}

function resetSensors(): void {
  sensors.value = DEFAULT_SENSORS.map((s) => ({ ...s }));
}

const WINDOWS = [
  { key: 3600, label: "1 小时" },
  { key: 4 * 3600, label: "4 小时" },
  { key: 8 * 3600, label: "8 小时" },
  { key: 24 * 3600, label: "24 小时" },
] as const;

/** 直方图柱宽：短时窗用 5 分钟，24 小时用 30 分钟，否则柱子细到看不见 */
function stepFor(windowS: number): number {
  return windowS > 8 * 3600 ? 1800 : 300;
}

const windowS = ref<number>(3600);
const channelKey = ref("ALL");
/** 每秒重算一次：聚合器刻意不做响应式（命中能到 40+/秒，逐条重算不划算） */
const tick = ref(0);

let timer = 0;
onMounted(() => {
  timer = window.setInterval(() => (tick.value += 1), 1000);
});
onBeforeUnmount(() => window.clearInterval(timer));

/* ────────────────────────────────────────────────────────────────
 * 数据库历史（跨重启可追溯的那一份）
 *
 * 与上面的"本次会话"是两套数据源，必须让用户看得见区别：
 *  - 本次会话：`ae-stats` 的分钟分桶累计，连接内核才有，重启即空；
 *  - 数据库历史：`ae_hits` 表，要内核带 `--with-storage` 才有行。
 * 不区分的话，"总 Hit 数 0"会被读成"设备没放过电"，而实际只是没开落库。
 * ──────────────────────────────────────────────────────────────── */

type SourceMode = "session" | "history";

interface AeHitRow {
  id: number;
  device_id: string;
  channel_id: number | null;
  timestamp: string | null;
  amplitude: number;
  rise_time_us: number | null;
  duration_us: number | null;
  counts: number | null;
  marse_energy: number | null;
  avg_frequency_khz: number | null;
  rms: number | null;
  asl_db: number | null;
  signal_strength: number | null;
  energy: number | null;
  phase_deg: number | null;
  polarity: number | null;
  signal_quality: number | null;
}

interface HistoryDevice {
  device_id: string;
  events: number;
  first_ts: string | null;
  last_ts: string | null;
}

/** 与 storage_rpc.MAX_LIMIT 对齐；到顶要提示，不能静默截断 */
const HISTORY_LIMIT = 1000;
/** 表格只渲染前若干行，避免一次性建上千个 DOM 节点 */
const TABLE_ROWS = 200;

const sourceMode = ref<SourceMode>("session");
const historyDevices = ref<HistoryDevice[]>([]);
const historyDevice = ref("");
const historyWindow = ref<number>(3600);
const historyChannel = ref<number | "ALL">("ALL");
const knownChannels = ref<number[]>([]);
const historyRows = ref<AeHitRow[]>([]);
const historyBusy = ref(false);
const historyError = ref("");
const historyNotice = ref("");
const historyTruncated = ref(false);

/** 三组维度对，与旧 `ui/widgets/ae_scatter_widget.py` 的 DIMENSIONS 一一对应 */
const SCATTER_DIMS = [
  {
    key: "duration",
    label: "持续时间 × 幅值",
    x: "amplitude",
    y: "duration_us",
    xLabel: "幅值 (mV)",
    yLabel: "持续时间 (µs)",
  },
  {
    key: "frequency",
    label: "平均频率 × 幅值",
    x: "amplitude",
    y: "avg_frequency_khz",
    xLabel: "幅值 (mV)",
    yLabel: "平均频率 (kHz)",
  },
  {
    key: "energy",
    label: "MARSE 能量 × 振铃计数",
    x: "counts",
    y: "marse_energy",
    xLabel: "振铃计数",
    yLabel: "MARSE 能量",
  },
] as const;

const scatterDim = ref<string>(SCATTER_DIMS[0].key);

const activeDim = computed(() => SCATTER_DIMS.find((d) => d.key === scatterDim.value) ?? SCATTER_DIMS[0]);

const scatterPoints = computed(() => {
  const dim = activeDim.value;
  const out: { x: number; y: number; amplitude: number }[] = [];
  for (const row of historyRows.value) {
    const x = row[dim.x as keyof AeHitRow];
    const y = row[dim.y as keyof AeHitRow];
    // 特征量允许缺失（内核取不到就是 None）；缺失点不参与绘制，由组件统一提示
    if (typeof x !== "number" || typeof y !== "number") continue;
    out.push({ x, y, amplitude: row.amplitude });
  }
  return out;
});

const CSV_COLUMNS: { key: keyof AeHitRow; label: string }[] = [
  { key: "timestamp", label: "时间(UTC)" },
  { key: "device_id", label: "设备" },
  { key: "channel_id", label: "通道" },
  { key: "amplitude", label: "幅值" },
  { key: "rise_time_us", label: "上升时间_us" },
  { key: "duration_us", label: "持续时间_us" },
  { key: "counts", label: "振铃计数" },
  { key: "avg_frequency_khz", label: "平均频率_kHz" },
  { key: "marse_energy", label: "MARSE能量" },
  { key: "rms", label: "RMS" },
  { key: "asl_db", label: "ASL_dB" },
  { key: "signal_strength", label: "信号强度" },
  { key: "energy", label: "能量" },
  { key: "phase_deg", label: "相位_deg" },
  { key: "polarity", label: "极性" },
  { key: "signal_quality", label: "信号质量" },
];

const exporting = ref(false);

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function fmtCell(value: unknown, digits: number): string {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
  return fmt(value, digits);
}

async function loadHistory(): Promise<void> {
  historyBusy.value = true;
  historyError.value = "";
  historyNotice.value = "";
  try {
    await waitCtrlOpen();
    if (!historyDevice.value) {
      historyDevices.value = await call<HistoryDevice[]>("storage.device_ids");
      historyDevice.value = historyDevices.value[0]?.device_id ?? "";
    }
    if (!historyDevice.value) {
      historyRows.value = [];
      historyTruncated.value = false;
      historyNotice.value = "库里还没有任何设备写过事件。AE 历史需要内核带 --with-storage 才会落库。";
      return;
    }
    const params: Record<string, unknown> = {
      device_id: historyDevice.value,
      start_time: new Date(Date.now() - historyWindow.value * 1000).toISOString(),
      limit: HISTORY_LIMIT,
    };
    if (historyChannel.value !== "ALL") params.channel_id = historyChannel.value;
    const rows = await call<AeHitRow[]>("storage.query_ae_hits", params);
    historyRows.value = rows;
    historyTruncated.value = rows.length >= HISTORY_LIMIT;
    const seen = new Set(knownChannels.value);
    for (const row of rows) {
      if (typeof row.channel_id === "number") seen.add(row.channel_id);
    }
    knownChannels.value = [...seen].sort((a, b) => a - b);
    historyNotice.value = rows.length
      ? `已载入 ${rows.length} 条${historyTruncated.value ? "（已达上限，请缩小时窗或按通道筛选）" : ""}`
      : "该设备在所选时窗内没有 AE 命中记录。";
  } catch (err) {
    historyError.value = `查询失败：${(err as Error).message}`;
    historyRows.value = [];
  } finally {
    historyBusy.value = false;
  }
}

async function exportHistoryCsv(): Promise<void> {
  if (!historyRows.value.length) {
    historyError.value = "没有可导出的数据";
    return;
  }
  const bridge = window.files;
  if (!bridge) {
    historyError.value = "当前环境没有文件保存通道（只有 Electron 里有）";
    return;
  }
  exporting.value = true;
  historyError.value = "";
  try {
    const header = CSV_COLUMNS.map((c) => c.label).join(",");
    const body = historyRows.value.map((row) => CSV_COLUMNS.map((c) => csvCell(row[c.key])).join(","));
    const csv = [header, ...body].join("\r\n");
    // 与日志导出同一条纪律：file:// 下 blob:<a download> 会静默不落盘，必须走主进程保存桥
    const result = await bridge.saveText(`ae-hits-${historyDevice.value}-${Date.now()}.csv`, `\uFEFF${csv}`);
    if (result.ok) historyNotice.value = `已导出 ${historyRows.value.length} 条到 ${result.path ?? ""}`;
    else if (!result.canceled) historyError.value = `导出失败：${result.error ?? "未知原因"}`;
  } catch (err) {
    historyError.value = `导出失败：${(err as Error).message}`;
  } finally {
    exporting.value = false;
  }
}

// 首次切到历史模式时自动拉一次；之后由「查询」按钮显式触发，避免筛选改一下就发一串请求
watch(sourceMode, (mode) => {
  if (mode === "history" && !historyRows.value.length && !historyBusy.value) void loadHistory();
});

const channels = computed<string[]>(() => {
  void tick.value;
  return aeChannelKeys();
});

const keys = computed<string[]>(() => {
  void tick.value;
  const all = aeChannelKeys();
  return channelKey.value === "ALL" ? all : all.filter((k) => k === channelKey.value);
});

const agg = computed<AeAggregate>(() => {
  void tick.value;
  return aeAggregate(windowS.value, keys.value);
});

const hist = computed(() => {
  void tick.value;
  return aeHistogram(windowS.value, stepFor(windowS.value), keys.value);
});

const histMax = computed(() => Math.max(1, ...hist.value.count));
const peak = computed(() => Math.max(0, ...hist.value.count));

function clock(epochS: number): string {
  return new Date(epochS * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

function channelLabel(key: string): string {
  const [device = "", channel = ""] = key.split(":");
  return `${device} / 通道 ${channel}`;
}

function fmt(v: number | undefined, digits: number): string {
  if (v === undefined || !Number.isFinite(v)) return "—";
  // 能量类量级跨度大，固定小数位会糊成 0.00
  if (digits === 2 && v !== 0 && Math.abs(v) < 0.01) return v.toExponential(2);
  return v.toFixed(digits);
}

/** 四张统计卡：口径写进 title，避免把"自界面连接起"读成"自设备投运起" */
const CARDS = computed(() => [
  {
    label: "总 Hit 数",
    value: String(agg.value.count),
    unit: "次",
    tone: "text-app-primary",
    hint: "自本界面连接内核起的累计命中数（内核侧暂无累计计数器，所以不是设备投运以来的总数）",
  },
  {
    label: "最大幅值",
    value: fmt(agg.value.max.amplitude, 1),
    unit: "mV",
    tone: "text-app-warning",
    hint: "幅值单位未标定：真机是原始计数、模拟器是 mV，量纲相差约百倍（见 AGENTS.md）",
  },
  {
    label: "总能量",
    value: fmt(agg.value.sum.energy, 2),
    unit: "",
    tone: "text-app-info",
    hint: "内核 energy 字段的合计；单位随现场标定，未标定前不写具体量纲",
  },
  {
    label: "持续时长",
    value: fmt(agg.value.durationS, 1),
    unit: "s",
    tone: "text-app-success",
    hint: "各次命中 duration_us 的合计（换算成秒），不是观测时长",
  },
]);
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="flex flex-wrap items-center gap-3 rounded-lg border border-app-border bg-app-surface px-3 py-2">
      <div class="flex rounded-md border border-app-border">
        <button
          class="rounded-l-md px-3 py-1 text-[12px]"
          :class="sourceMode === 'session' ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
          @click="sourceMode = 'session'"
        >
          本次会话
        </button>
        <button
          class="rounded-r-md px-3 py-1 text-[12px]"
          :class="sourceMode === 'history' ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
          @click="sourceMode = 'history'"
        >
          数据库历史
        </button>
      </div>
      <span class="text-[11px] text-app-muted">
        {{
          sourceMode === "session"
            ? "连接内核起的累计统计（重启即空）"
            : "ae_hits 表里的记录（跨重启可追溯，需内核带 --with-storage）"
        }}
      </span>

      <template v-if="sourceMode === 'session'">
        <div class="flex flex-wrap items-center gap-1">
          <span class="text-[11px] text-app-muted">通道</span>
          <button
            class="rounded px-2 py-1 text-[12px]"
            :class="channelKey === 'ALL' ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
            @click="channelKey = 'ALL'"
          >
            全部
          </button>
          <button
            v-for="key in channels"
            :key="key"
            class="rounded px-2 py-1 text-[12px]"
            :class="channelKey === key ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
            @click="channelKey = key"
          >
            {{ channelLabel(key) }}
          </button>
        </div>

        <div class="ml-auto flex items-center gap-1">
          <span class="text-[11px] text-app-muted">时间范围</span>
          <button
            v-for="w in WINDOWS"
            :key="w.key"
            class="rounded px-2 py-1 text-[12px]"
            :class="windowS === w.key ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
            @click="windowS = w.key"
          >
            {{ w.label }}
          </button>
        </div>
      </template>
    </section>

    <template v-if="sourceMode === 'session'">
      <section class="grid grid-cols-4 gap-3">
        <div
          v-for="card in CARDS"
          :key="card.label"
          class="rounded-lg border border-app-border bg-app-surface p-3"
          :title="card.hint"
        >
          <div class="text-[11px] text-app-muted">{{ card.label }}</div>
          <div class="tabular mt-1 text-2xl font-semibold" :class="card.tone">
            {{ card.value }}<span class="ml-1 text-xs font-normal text-app-muted">{{ card.unit }}</span>
          </div>
        </div>
      </section>

      <p
        v-if="agg.count === 0"
        class="rounded-md bg-app-surface-variant/60 px-3 py-4 text-center text-[12px] text-app-muted"
      >
        所选范围内没有 AE 命中。AE 仿真要用
        <code>--sim-type ae --pulse-rate 200</code>（默认 5/s 约 5 秒才一条，验收时建议 200~1000）。
      </p>

      <template v-else>
        <section class="rounded-lg border border-app-border bg-app-surface">
          <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
            <span class="text-[13px] font-medium">参数明细</span>
            <span class="text-[11px] text-app-muted">
              有命中的分钟桶 {{ agg.buckets }} 个 · 单位按内核字段，不照抄设计稿的 dB/mJ/ms
            </span>
          </div>
          <table class="w-full text-[12px]">
            <thead class="text-app-muted">
              <tr class="border-b border-app-border">
                <th class="px-3 py-1.5 text-left font-normal">参数</th>
                <th class="px-3 py-1.5 text-right font-normal">均值</th>
                <th class="px-3 py-1.5 text-right font-normal">最大</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in AE_FIELDS" :key="f.key" class="border-b border-app-border/50">
                <td class="px-3 py-1.5">
                  {{ f.label }}<span class="ml-1 text-[10px] text-app-muted">{{ f.unit }}</span>
                </td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmt(agg.mean[f.key], f.digits) }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmt(agg.max[f.key], f.digits) }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section class="rounded-lg border border-app-border bg-app-surface">
          <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
            <span class="text-[13px] font-medium">Hit 分布直方图</span>
            <span class="text-[11px] text-app-muted">
              按 {{ stepFor(windowS) / 60 }} 分钟间隔 · 峰值 {{ peak }}
            </span>
          </div>
          <div class="px-3 pb-2 pt-3">
            <div class="flex h-32 items-end gap-px">
              <div
                v-for="(c, i) in hist.count"
                :key="i"
                class="min-w-0 flex-1 rounded-t-sm bg-app-primary/70"
                :style="{ height: `${Math.max(c > 0 ? 2 : 0, (c / histMax) * 100)}%` }"
                :title="`${clock(hist.t[i] ?? 0)} · ${c} 次`"
              />
            </div>
            <div class="mt-1 flex justify-between text-[10px] text-app-muted">
              <span>{{ clock(hist.t[0] ?? 0) }}</span>
              <span>{{ clock(hist.t[Math.floor(hist.t.length / 2)] ?? 0) }}</span>
              <span>{{ clock(hist.t[hist.t.length - 1] ?? 0) }}</span>
            </div>
          </div>
        </section>
      </template>
    </template>

    <template v-else>
      <section class="flex flex-wrap items-center gap-3 rounded-lg border border-app-border bg-app-surface px-3 py-2">
        <label class="flex items-center gap-1.5 text-[11px] text-app-muted">
          设备
          <select
            v-model="historyDevice"
            class="rounded border border-app-border bg-app-bg px-1.5 py-1 text-[12px] text-app-fg"
          >
            <option v-for="d in historyDevices" :key="d.device_id" :value="d.device_id">
              {{ d.device_id }}（{{ d.events }} 条事件）
            </option>
            <option v-if="!historyDevices.length" value="">（库里无设备）</option>
          </select>
        </label>

        <label class="flex items-center gap-1.5 text-[11px] text-app-muted">
          通道
          <select
            v-model="historyChannel"
            class="rounded border border-app-border bg-app-bg px-1.5 py-1 text-[12px] text-app-fg"
          >
            <option value="ALL">全部</option>
            <option v-for="c in knownChannels" :key="c" :value="c">通道 {{ c }}</option>
          </select>
        </label>

        <div class="flex items-center gap-1">
          <span class="text-[11px] text-app-muted">时窗</span>
          <button
            v-for="w in WINDOWS"
            :key="w.key"
            class="rounded px-2 py-1 text-[12px]"
            :class="historyWindow === w.key ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
            @click="historyWindow = w.key"
          >
            {{ w.label }}
          </button>
        </div>

        <div class="ml-auto flex items-center gap-2">
          <button
            :disabled="historyBusy"
            class="rounded-md border border-app-border px-2.5 py-1 text-[12px] disabled:opacity-50"
            @click="loadHistory"
          >
            {{ historyBusy ? "查询中…" : "查询" }}
          </button>
          <button
            :disabled="exporting || !historyRows.length"
            class="rounded-md bg-app-primary px-2.5 py-1 text-[12px] text-white disabled:opacity-50"
            @click="exportHistoryCsv"
          >
            {{ exporting ? "导出中…" : "导出 CSV" }}
          </button>
        </div>
      </section>

      <p v-if="historyError" class="rounded-md bg-app-error/10 px-3 py-2 text-[12px] text-app-error">
        {{ historyError }}
      </p>
      <p v-if="historyNotice" class="rounded-md bg-app-success/10 px-3 py-2 text-[12px] text-app-success">
        {{ historyNotice }}
      </p>

      <section class="rounded-lg border border-app-border bg-app-surface">
        <div class="flex flex-wrap items-center justify-between gap-2 border-b border-app-border px-3 py-2">
          <span class="text-[13px] font-medium">特征散点</span>
          <div class="flex items-center gap-1">
            <button
              v-for="d in SCATTER_DIMS"
              :key="d.key"
              class="rounded border px-2 py-0.5 text-[11px]"
              :class="
                scatterDim === d.key ? 'border-app-primary text-app-primary' : 'border-app-border text-app-muted'
              "
              @click="scatterDim = d.key"
            >
              {{ d.label }}
            </button>
            <span class="ml-2 text-[11px] text-app-muted">
              {{ scatterPoints.length }} / {{ historyRows.length }} 点可绘
            </span>
          </div>
        </div>
        <div class="p-3">
          <AeScatter
            :points="scatterPoints"
            :x-label="activeDim.xLabel"
            :y-label="activeDim.yLabel"
            :height="280"
          />
        </div>
      </section>

      <section class="rounded-lg border border-app-border bg-app-surface">
        <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
          <span class="text-[13px] font-medium">命中记录</span>
          <span class="text-[11px] text-app-muted">
            共 {{ historyRows.length }} 条，下表显示前 {{ Math.min(TABLE_ROWS, historyRows.length) }} 条 · 时间按 UTC
          </span>
        </div>
        <div class="max-h-[420px] overflow-auto">
          <table class="w-full text-[12px]">
            <thead class="sticky top-0 bg-app-surface text-app-muted">
              <tr class="border-b border-app-border">
                <th class="px-3 py-1.5 text-left font-normal">时间</th>
                <th class="px-3 py-1.5 text-right font-normal">通道</th>
                <th class="px-3 py-1.5 text-right font-normal">幅值</th>
                <th class="px-3 py-1.5 text-right font-normal">上升 (µs)</th>
                <th class="px-3 py-1.5 text-right font-normal">持续 (µs)</th>
                <th class="px-3 py-1.5 text-right font-normal">振铃</th>
                <th class="px-3 py-1.5 text-right font-normal">频率 (kHz)</th>
                <th class="px-3 py-1.5 text-right font-normal">ASL (dB)</th>
                <th class="px-3 py-1.5 text-right font-normal">RMS</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in historyRows.slice(0, TABLE_ROWS)" :key="row.id" class="border-b border-app-border/50">
                <td class="px-3 py-1.5">{{ row.timestamp ?? "—" }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ row.channel_id ?? "—" }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmtCell(row.amplitude, 1) }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmtCell(row.rise_time_us, 1) }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmtCell(row.duration_us, 1) }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmtCell(row.counts, 0) }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmtCell(row.avg_frequency_khz, 1) }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmtCell(row.asl_db, 1) }}</td>
                <td class="tabular px-3 py-1.5 text-right">{{ fmtCell(row.rms, 2) }}</td>
              </tr>
              <tr v-if="!historyRows.length">
                <td colspan="9" class="px-3 py-6 text-center text-app-muted">
                  还没有载入记录。点右上角「查询」，或确认内核启动时带了 --with-storage。
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <section class="grid grid-cols-5 gap-3">
      <div class="col-span-2 rounded-lg border border-app-border bg-app-surface">
        <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
          <span class="text-[13px] font-medium">传感器阵列</span>
          <span class="flex gap-1">
            <button class="rounded px-2 py-0.5 text-[11px] text-app-muted hover:bg-app-surface-variant" @click="addSensor">
              新增
            </button>
            <button
              class="rounded px-2 py-0.5 text-[11px] text-app-muted hover:bg-app-surface-variant"
              @click="resetSensors"
            >
              复位
            </button>
          </span>
        </div>
        <table class="w-full text-[12px]">
          <thead class="text-app-muted">
            <tr class="border-b border-app-border">
              <th class="px-3 py-1.5 text-left font-normal">编号</th>
              <th class="px-3 py-1.5 text-right font-normal">X (m)</th>
              <th class="px-3 py-1.5 text-right font-normal">Y (m)</th>
              <th class="w-8" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in sensors" :key="i" class="border-b border-app-border/50">
              <td class="px-2 py-1">
                <input
                  v-model="s.id"
                  class="w-full rounded bg-transparent px-1 py-0.5 outline-none focus:bg-app-surface-variant"
                />
              </td>
              <td class="px-2 py-1">
                <input
                  v-model.number="s.x"
                  type="number"
                  step="0.05"
                  class="tabular w-full rounded bg-transparent px-1 py-0.5 text-right outline-none focus:bg-app-surface-variant"
                />
              </td>
              <td class="px-2 py-1">
                <input
                  v-model.number="s.y"
                  type="number"
                  step="0.05"
                  class="tabular w-full rounded bg-transparent px-1 py-0.5 text-right outline-none focus:bg-app-surface-variant"
                />
              </td>
              <td class="px-2 py-1 text-right">
                <button class="rounded px-1 text-[11px] text-app-muted hover:text-app-error" @click="removeSensor(i)">
                  删
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="px-3 pb-2 pt-1 text-[11px] text-app-muted">
          位置单位米，现场量取后填入。全仓库没有传感器阵列的 RPC，所以存在界面偏好里
          （旧版连内存都不留：`sensor_array_config_dialog.py` 只返回一个临时 dict，重启即丢）。
        </p>
      </div>
      <div class="col-span-3 rounded-lg border border-app-border bg-app-surface p-3">
        <GdopMap :sensors="sensors" :height="260" />
      </div>
    </section>
  </div>
</template>
