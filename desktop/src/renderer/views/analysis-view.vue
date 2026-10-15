<script setup lang="ts">
/**
 * 数据分析（M6.4）。
 *
 * ## 这一页只做有真实数据支撑的部分
 *
 * 设计稿 `analysis.html` 里有三样**按"不造数据"的原则做不出来**，这里显式不做，
 * 并在页脚写明原因（而不是画一个假数字）：
 *
 *  - 「放电类型分布 4 类 / 电晕 38%…」：现在对**导入文件**走 `data.import_file` →
 *    `DischargeClassifier.classify_by_cycles` 做逐周期投票；实时采集事件上仍没有类型标签，
 *    所以只显示导入数据的实测分布，不把分类器结论套到实时事件上。
 *  - 「等效放电量 2.8 nC」：全链路没有计数→物理量的换算（`ChannelConfig.gain` 从未作用于
 *    波形），写 nC 就是编的。
 *  - 「最大重复率 1,203 Hz」：口径没有定义（是每秒脉冲数？重复放电间隔的倒数？），
 *    先不给。
 *
 * ## 数据源
 *
 *  - 指标卡的合计走 `storage.query_trend_series`：它从 `pd_events` 按箱现算，
 *    **不受 `query_events` 的 5000 条上限影响**，求和就是窗口内的真实总数。
 *  - 散点与明细表走 `storage.query_events`，那是一份**样本**（默认 1000、上限 5000），
 *    所以卡片里把它标成"样本数"而不是"总数"，免得两个数看着矛盾。
 */

import { computed, onMounted, ref, shallowRef } from "vue";
import { call, latest, waitCtrlOpen } from "@/renderer/lib/store";
import PrpdScatter from "@/renderer/components/charts/prpd-scatter.vue";
import type { PrpdMeta } from "@/shared/protocol";

type WindowKey = "1h" | "24h" | "7d" | "30d";

const WINDOWS: { key: WindowKey; label: string; hours: number }[] = [
  { key: "1h", label: "最近 1 小时", hours: 1 },
  { key: "24h", label: "最近 24 小时", hours: 24 },
  { key: "7d", label: "最近 7 天", hours: 24 * 7 },
  { key: "30d", label: "最近 30 天", hours: 24 * 30 },
];

interface EventRow {
  id: number;
  device_id: string;
  channel_id: number | null;
  sensor_type: string | null;
  timestamp: string | null;
  phase: number | null;
  amplitude: number;
  polarity: number | null;
  energy: number | null;
}

interface TrendBin {
  count: number;
  max_amplitude: number;
  positive_count: number;
  negative_count: number;
}

/**
 * `storage.device_ids` 的返回是**对象数组**而不是字符串数组。
 *
 * 曾经按 string[] 写，`<option>` 就把整个对象渲染成字符串、`device_id` 变成那段 JSON，
 * 于是查询条件永远不匹配、所有指标恒为 0——症状和"库里没有数据"一模一样。
 * 类型定义与 trend-view.vue 保持一致。
 */
interface HistoryDevice {
  device_id: string;
  events: number;
  first_ts: string | null;
  last_ts: string | null;
}

const devices = shallowRef<HistoryDevice[]>([]);
const device = ref("");
const windowKey = ref<WindowKey>("1h");
const channel = ref<"ALL" | number>("ALL");

const events = shallowRef<EventRow[]>([]);
const series = shallowRef<TrendBin[]>([]);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const loaded = ref(false);

/** 事件里出现过的通道号，作为筛选候选；不去问内核要通道表——那是设备管理的职责 */
const channels = computed(() => {
  const set = new Set<number>();
  for (const e of events.value) if (typeof e.channel_id === "number") set.add(e.channel_id);
  return [...set].sort((a, b) => a - b);
});

const shown = computed(() =>
  channel.value === "ALL" ? events.value : events.value.filter((e) => e.channel_id === channel.value),
);

/**
 * 散点图取**实时** `prpd` 帧里的事件，不取库里的历史事件。
 *
 * 两个原因：① 设计稿这一栏本来就写的是"实时采集数据"；② 库里那批事件的 `phase` 与
 * `polarity` 是 NULL——`uhf_pipeline` 发的 `pd_event_detected` 只带 `position` 与幅值，
 * 没有相位与极性，`PersistenceSubscriber` 照抄入库。所以"历史 PRPD 散点"现在无点可画，
 * 页脚把这条记成缺口，而不是拿一份没有相位的样本画一张空图。
 */
const liveEvents = computed(() => {
  const frame = latest.value.prpd;
  if (!frame) return [];
  return (frame.m as unknown as PrpdMeta).events ?? [];
});

/** 空态时告诉用户到底是"没收到帧"还是"帧里没事件" */
const prpdSourceHint = computed(() => {
  const frame = latest.value.prpd;
  if (!frame) return "尚未收到 prpd 帧";
  const meta = frame.m as unknown as PrpdMeta;
  return `prpd 帧内 ${meta.events?.length ?? 0} 个事件（累计 ${meta.total_events ?? 0}）`;
});

// ── 实验数据导入（旧 ui/ 的 data_import_widget 迁移项，内核 RPC: data.import_file） ──

interface ImportValidation {
  valid: boolean;
  total_events: number;
}

interface ImportClassification {
  type: string;
  name: string;
  name_en: string;
  description: string;
  confidence: number;
  severity: string;
  symmetry: boolean;
  pos_ratio: number;
  concentration: number;
}

interface ImportDistribution {
  total_samples: number;
  type_counts: Record<string, number>;
  type_ratios: Record<string, number>;
  severity: string;
  /** 仅供“按实际周期”筛选散点，不携带每周期完整的分类特征大对象 */
  cycle_types: Record<string, string>;
}

interface ImportedSet {
  id: number;
  fileName: string;
  format: string;
  total: number;
  returned: number;
  truncated: number;
  nonfinite: number;
  sourceRows: number | null;
  filteredRows: number | null;
  columns: string[] | null;
  phases: number[];
  amplitudes: number[];
  cycles: number[];
  validation: ImportValidation | null;
  classification: ImportClassification | null;
  classificationError: string | null;
  distribution: ImportDistribution | null;
  distributionError: string | null;
}

interface RawImport {
  format: string;
  file_name: string;
  total_points: number;
  returned_points: number;
  truncated: number;
  nonfinite_dropped: number;
  source_rows: number | null;
  filtered_rows: number | null;
  columns: string[] | null;
  phases: number[];
  amplitudes: number[];
  cycles: number[];
  validation: ImportValidation | null;
  classification: ImportClassification | null;
  classification_error: string | null;
  distribution: ImportDistribution | null;
  distribution_error: string | null;
}

function normalizeImport(id: number, r: RawImport): ImportedSet {
  return {
    id,
    fileName: r.file_name,
    format: r.format,
    total: r.total_points,
    returned: r.returned_points,
    truncated: r.truncated,
    nonfinite: r.nonfinite_dropped,
    sourceRows: r.source_rows,
    filteredRows: r.filtered_rows,
    columns: r.columns,
    phases: r.phases,
    amplitudes: r.amplitudes,
    cycles: r.cycles,
    validation: r.validation,
    classification: r.classification,
    classificationError: r.classification_error,
    distribution: r.distribution,
    distributionError: r.distribution_error,
  };
}

const imports = shallowRef<ImportedSet[]>([]);
const activeImportId = ref<number | null>(null);
const importFilters = ref<Record<number, { minAmplitude: number; type: string }>>({});
const importing = ref(false);
const importError = ref("");
let importSeq = 0;

const activeImport = computed(
  () => imports.value.find((x) => x.id === activeImportId.value) ?? null,
);
const activeImportFilter = computed(() => {
  const id = activeImportId.value;
  return id === null ? { minAmplitude: 0, type: "ALL" } : importFilters.value[id] ?? { minAmplitude: 0, type: "ALL" };
});
const activeCycleTypes = computed(() => activeImport.value?.distribution?.cycle_types ?? {});
const importTypeOptions = computed(() => [...new Set(Object.values(activeCycleTypes.value))].sort());

function patchActiveImportFilter(patch: Partial<{ minAmplitude: number; type: string }>): void {
  const id = activeImportId.value;
  if (id === null) return;
  const current = activeImportFilter.value;
  importFilters.value = {
    ...importFilters.value,
    [id]: { ...current, ...patch },
  };
}

function onMinAmplitudeInput(ev: Event): void {
  const raw = (ev.target as HTMLInputElement).value;
  const n = raw === "" ? 0 : Number(raw);
  patchActiveImportFilter({ minAmplitude: Number.isFinite(n) ? Math.max(0, n) : 0 });
}

function resetImportFilter(): void {
  patchActiveImportFilter({ minAmplitude: 0, type: "ALL" });
}

/** 当前文件经过幅值/逐周期类型筛选后的原始索引；所有视图共享这一份筛选结果。 */
const filteredImportIndices = computed(() => {
  const s = activeImport.value;
  if (!s) return [];
  const { minAmplitude, type } = activeImportFilter.value;
  const cycleTypes = activeCycleTypes.value;
  const indices: number[] = [];
  for (let i = 0; i < s.phases.length; i += 1) {
    const amplitude = s.amplitudes[i] ?? 0;
    if (Math.abs(amplitude) < minAmplitude) continue;
    if (type !== "ALL" && cycleTypes[String(s.cycles[i] ?? 0)] !== type) continue;
    indices.push(i);
  }
  return indices;
});

/** 导入器的 CSV 列映射带 a>=0 过滤；若其他格式保留负值，符号仍按幅值符号映射极性。 */
const importEvents = computed(() => {
  const s = activeImport.value;
  if (!s) return [];
  return filteredImportIndices.value.map((i) => {
    const amplitude = s.amplitudes[i] ?? 0;
    return {
      phase: s.phases[i] ?? 0,
      amplitude,
      polarity: amplitude < 0 ? 1 : 0,
    };
  });
});

/** 32 桶幅值直方图，使用与散点完全相同的筛选索引；不假定幅值单位是 mV。 */
const amplitudeHistogram = computed(() => {
  const s = activeImport.value;
  const indices = filteredImportIndices.value;
  const binCount = 32;
  const maxAmplitude = indices.reduce((m, i) => Math.max(m, Math.abs(s?.amplitudes[i] ?? 0)), 0);
  const binWidth = maxAmplitude > 0 ? maxAmplitude / binCount : 1;
  const counts = Array.from({ length: binCount }, () => 0);
  for (const i of indices) {
    const value = Math.abs(s?.amplitudes[i] ?? 0);
    const bin = Math.min(binCount - 1, Math.floor(value / binWidth));
    counts[bin] = (counts[bin] ?? 0) + 1;
  }
  const top = Math.max(1, ...counts);
  return counts.map((count, i) => ({
    count,
    from: i * binWidth,
    to: (i + 1) * binWidth,
    height: (count / top) * 100,
  }));
});

// ── 分析报告导出（内核 report.export 生成字节，落盘路径由主进程原生对话框决定） ──

const REPORT_FORMATS = [
  { key: "json", label: "JSON" },
  { key: "excel", label: "Excel" },
  { key: "word", label: "Word" },
] as const;

type ReportFormat = (typeof REPORT_FORMATS)[number]["key"];

const reportCaps = ref<{ available: ReportFormat[]; missing: Partial<Record<ReportFormat, string>> } | null>(null);
const exporting = ref<ReportFormat | null>(null);
const exportNotice = ref("");
const exportError = ref("");
/** 散点所在容器：Word 报告的图谱就是从这块 canvas 截的。 */
const scatterHost = ref<HTMLElement | null>(null);

async function loadReportCaps(): Promise<void> {
  try {
    // 必须先等 /ctrl 握手完成：onMounted 里直接发 RPC 会撞上 WebSocket 还没开，
    // 表现是报告区永久禁用，而页面别处一切正常（本仓库 store.ts 里记过这个竞态）
    await waitCtrlOpen();
    reportCaps.value = await call<typeof reportCaps.value>("report.formats", {});
  } catch (err) {
    // 能力查询失败不顶掉整页：报告区自己显示错误，导入与散点照常
    exportError.value = `报告能力查询失败：${(err as Error).message}`;
  }
}

/**
 * 报告口径 = 这份导入文件的**全量样本**，不跟随筛选。
 *
 * 筛选是看图的动作；报告里同时混"整份的分类结论"和"筛选后的统计"会造出一个
 * 没人能复核的数字。截的图谱则是当前视图，所以文件名里如实写明是哪一份、
 * 采样是否被内核截断（超过 5 万点会截）。
 */
function reportPayload(s: ImportedSet): Record<string, unknown> {
  const amps = s.amplitudes.map((a) => Math.abs(a));
  const sorted = [...amps].sort((a, b) => a - b);
  const at = (q: number) => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] : 0);
  const sum = amps.reduce((acc, v) => acc + v, 0);
  const truncated = s.truncated > 0 ? `，另有 ${s.truncated} 点未取` : "";
  return {
    analysis_time: new Date().toISOString(),
    file_name: `${s.fileName}（${s.format}，样本 ${s.returned} 点${truncated}；统计按全量样本，图谱按当前视图）`,
    statistics: {
      total_events: s.returned,
      max_amplitude: at(1),
      min_amplitude: at(0),
      avg_amplitude: sorted.length ? sum / sorted.length : 0,
      median_amplitude: at(0.5),
      p90: at(0.9),
    },
    discharge_classification: {
      name: s.classification?.name ?? "",
      confidence: s.classification?.confidence ?? 0,
      severity: s.classification?.severity ?? s.distribution?.severity ?? "",
      total_samples: s.distribution?.total_samples ?? 0,
      type_counts: s.distribution?.type_counts ?? {},
      type_ratios: s.distribution?.type_ratios ?? {},
    },
    // 单位口径未标定（全链路没有计数→物理量换算），报告里不能写成 mV
    amplitude_note: "幅值为导入文件的原始数值，未做标定，单位随数据源",
    quality: s.validation ?? null,
    errors: [s.classificationError, s.distributionError].filter(Boolean),
  };
}

function chartPngPayload(): string | null {
  const canvas = scatterHost.value?.querySelector("canvas");
  if (!(canvas instanceof HTMLCanvasElement)) return null;
  const url = canvas.toDataURL("image/png");
  const comma = url.indexOf(",");
  return comma >= 0 ? url.slice(comma + 1) : null;
}

async function exportReport(fmt: ReportFormat): Promise<void> {
  const s = activeImport.value;
  if (!s || exporting.value) return;
  exporting.value = fmt;
  exportError.value = "";
  exportNotice.value = "";
  try {
    const png = fmt === "word" ? chartPngPayload() : null;
    const out = await call<{ filename: string; size: number; bytes: Uint8Array; chart_embedded: boolean }>(
      "report.export",
      { format: fmt, data: reportPayload(s), ...(png ? { chart_png: png } : {}) },
    );
    const bridge = window.files;
    if (!bridge) {
      exportError.value = "当前环境没有文件保存通道（只有 Electron 里有）";
      return;
    }
    const saved = await bridge.saveBinary(out.filename, out.bytes);
    if (saved.ok) {
      const kb = (out.size / 1024).toFixed(1);
      const chart = fmt === "word" ? (out.chart_embedded ? "，含当前视图图谱" : "，未取到图谱") : "";
      exportNotice.value = `已导出 ${kb} KB${chart} → ${saved.path ?? ""}`;
    } else if (!saved.canceled) {
      exportError.value = `导出失败：${saved.error ?? "未知原因"}`;
    }
  } catch (err) {
    exportError.value = `导出失败：${(err as Error).message}`;
  } finally {
    exporting.value = null;
  }
}

async function onPickFiles(ev: Event): Promise<void> {
  const input = ev.target as HTMLInputElement;
  const files = [...(input.files ?? [])];
  input.value = ""; // 清掉，同一个文件才能重复选
  if (!files.length) return;
  importing.value = true;
  importError.value = "";
  let lastId: number | null = null;
  try {
    await waitCtrlOpen();
    for (const f of files) {
      const path = window.files?.pathFor(f);
      if (!path) {
        importError.value = `${f.name}：拿不到绝对路径（preload 的 webUtils 不可用）`;
        continue;
      }
      try {
        const r = await call<RawImport>("data.import_file", { path });
        const id = ++importSeq;
        imports.value = [...imports.value, normalizeImport(id, r)];
        importFilters.value = { ...importFilters.value, [id]: { minAmplitude: 0, type: "ALL" } };
        lastId = id;
      } catch (err) {
        importError.value = `${f.name}：${(err as Error).message}`;
      }
    }
    if (lastId !== null) activeImportId.value = lastId;
  } catch (err) {
    // 内核没连上、RPC 报错都要落在这里：不接住的话导入是静默失败，按钮点了像没点
    importError.value = (err as Error).message;
  } finally {
    importing.value = false;
  }
}

function removeImport(id: number): void {
  const next = imports.value.filter((x) => x.id !== id);
  imports.value = next;
  const remainingFilters = { ...importFilters.value };
  delete remainingFilters[id];
  importFilters.value = remainingFilters;
  if (activeImportId.value === id) {
    activeImportId.value = next[next.length - 1]?.id ?? null;
  }
}

function severityLabel(s: string | undefined): string {
  return s === "critical" ? "严重" : s === "warning" ? "注意" : s === "normal" ? "正常" : s || "—";
}

const summary = computed(() => {
  let total = 0;
  let maxAmp = 0;
  let pos = 0;
  let neg = 0;
  for (const b of series.value) {
    total += b.count;
    maxAmp = Math.max(maxAmp, b.max_amplitude);
    pos += b.positive_count;
    neg += b.negative_count;
  }
  // 极性计数全 0 而脉冲数非 0 → 是库里 polarity 为 NULL，不是"一个负极性都没有"。
  // 这两种情况的处置方向完全不同，所以显式分开，不把 0/0 端上去让人误读。
  const polarityKnown = total === 0 || pos + neg > 0;
  return { total, maxAmp, pos, neg, polarityKnown };
});

async function load(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    await waitCtrlOpen();
    if (!devices.value.length) {
      devices.value = await call<HistoryDevice[]>("storage.device_ids");
      if (!device.value) device.value = devices.value[0]?.device_id ?? "";
    }
    if (!device.value) {
      events.value = [];
      series.value = [];
      return;
    }
    const hours = WINDOWS.find((w) => w.key === windowKey.value)?.hours ?? 1;
    const start = new Date(Date.now() - hours * 3600_000).toISOString();
    const [ev, ts] = await Promise.all([
      call<EventRow[]>("storage.query_events", {
        device_id: device.value,
        start_time: start,
        limit: 2000,
      }),
      call<TrendBin[]>("storage.query_trend_series", {
        device_id: device.value,
        window: windowKey.value,
        bins: 120,
      }),
    ]);
    events.value = ev;
    series.value = ts;
    loaded.value = true;
  } catch (err) {
    // 内核把非法参数原样回传，直接显示比吞掉好排查
    error.value = (err as Error).message;
  } finally {
    busy.value = false;
  }
}

function reset(): void {
  channel.value = "ALL";
  windowKey.value = "1h";
  void load();
}

function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? "" : String(v);
  // 逗号/引号/换行都要走引号包裹，否则时间戳里的逗号会把列错开
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

async function exportCsv(): Promise<void> {
  const head = ["时间戳", "设备", "通道", "传感器", "相位(°)", "幅值", "极性", "能量"];
  const rows = shown.value.map((e) => [
    e.timestamp,
    e.device_id,
    e.channel_id,
    e.sensor_type,
    e.phase,
    e.amplitude,
    // 与表格同一口径：polarity 为 NULL 时写空，不能写成"正"
    e.polarity === null || e.polarity === undefined ? "" : e.polarity === 1 ? "负" : "正",
    e.energy,
  ]);
  const csv = [head, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n");

  const bridge = window.files;
  if (!bridge) {
    // 纯浏览器预览（vite dev 直开）没有这个桥。说出来，别让按钮看着像点了没反应。
    error.value = "当前环境没有文件保存通道（只有 Electron 里有）";
    return;
  }
  // BOM 不能省：不加的话 Excel 打开中文列头是乱码
  const res = await bridge.saveText(
    `pd-events-${device.value || "device"}-${windowKey.value}.csv`,
    `\uFEFF${csv}`,
  );
  if (res.ok) {
    notice.value = `已导出 ${shown.value.length} 行到 ${res.path ?? ""}`;
  } else if (!res.canceled) {
    error.value = `导出失败：${res.error ?? "未知原因"}`;
  }
}

function stamp(iso: string | null): string {
  if (!iso) return "—";
  // 库列是 naive UTC，直接 new Date(iso) 会被当本地时间，差一个时区
  const ms = Date.parse(`${iso}${iso.endsWith("Z") ? "" : "Z"}`);
  return Number.isFinite(ms) ? new Date(ms).toLocaleString("zh-CN", { hour12: false }) : iso;
}

/**
 * polarity 可以为 NULL（内核的 `pd_event_detected` 不带极性，入库即 NULL）。
 * NULL 不能显示成"正"——那是显示层在替数据下结论。
 */
function polarityLabel(p: number | null): string {
  if (p === null || p === undefined) return "—";
  return p === 1 ? "负" : "正";
}

function polarityTone(p: number | null): string {
  if (p === null || p === undefined) return "text-app-muted";
  return p === 1 ? "text-app-info" : "text-app-error";
}

onMounted(() => {
  void load();
  void loadReportCaps();
});
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="flex flex-wrap items-center gap-3 rounded-lg border border-app-border bg-app-surface px-3 py-2">
      <label class="flex items-center gap-1 text-[12px]">
        <span class="text-app-muted">设备</span>
        <select
          v-model="device"
          class="rounded border border-app-border bg-app-surface px-2 py-1 text-[12px]"
          @change="load"
        >
          <option v-if="!devices.length" value="">（库内没有设备）</option>
          <option v-for="d in devices" :key="d.device_id" :value="d.device_id">
            {{ d.device_id }}（{{ d.events }} 条）
          </option>
        </select>
      </label>

      <label class="flex items-center gap-1 text-[12px]">
        <span class="text-app-muted">通道</span>
        <select
          :value="channel"
          class="rounded border border-app-border bg-app-surface px-2 py-1 text-[12px]"
          @change="channel = ($event.target as HTMLSelectElement).value === 'ALL' ? 'ALL' : Number(($event.target as HTMLSelectElement).value)"
        >
          <option value="ALL">全部</option>
          <option v-for="c in channels" :key="c" :value="c">{{ c }}</option>
        </select>
      </label>

      <div class="flex items-center gap-1">
        <span class="text-[11px] text-app-muted">时间范围</span>
        <button
          v-for="w in WINDOWS"
          :key="w.key"
          class="rounded px-2 py-1 text-[12px]"
          :class="windowKey === w.key ? 'bg-app-primary text-white' : 'text-app-muted hover:bg-app-surface-variant'"
          @click="windowKey = w.key; load()"
        >
          {{ w.label }}
        </button>
      </div>

      <div class="ml-auto flex items-center gap-1">
        <button
          class="rounded border border-app-border px-2 py-1 text-[12px] text-app-muted hover:bg-app-surface-variant"
          @click="reset"
        >
          重置
        </button>
        <button
          class="rounded bg-app-primary px-2 py-1 text-[12px] text-white disabled:opacity-50"
          :disabled="busy || !device"
          @click="load"
        >
          {{ busy ? "查询中…" : "查询" }}
        </button>
        <button
          class="rounded border border-app-border px-2 py-1 text-[12px] disabled:opacity-50"
          :disabled="!shown.length"
          @click="exportCsv"
        >
          导出 CSV
        </button>
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
        <span class="text-[13px] font-medium">实验数据导入</span>
        <span class="text-[11px] text-app-muted">csv / xlsx / txt / npy / npz / h5 / json — 走内核 DataImporter，只读不落库</span>
      </div>
      <div class="flex flex-wrap items-center gap-3 px-3 py-2">
        <label
          class="cursor-pointer rounded bg-app-primary px-3 py-1.5 text-[12px] text-white"
          :class="importing ? 'opacity-60' : ''"
        >
          {{ importing ? "导入中…" : "选择文件…" }}
          <input
            type="file"
            multiple
            class="hidden"
            accept=".csv,.xlsx,.xls,.txt,.npy,.npz,.h5,.hdf5,.json"
            :disabled="importing"
            @change="onPickFiles"
          />
        </label>
        <span class="text-[11px] text-app-muted">支持多选；超过 5 万点截头并标注，列映射过滤掉的行数会如实显示</span>
        <span v-if="imports.length" class="ml-auto flex flex-wrap items-center gap-1">
          <button
            v-for="s in imports"
            :key="s.id"
            class="rounded px-2 py-0.5 text-[11px]"
            :class="activeImportId === s.id ? 'bg-app-primary/15 text-app-primary' : 'text-app-muted hover:bg-app-surface-variant'"
            @click="activeImportId = s.id"
          >
            {{ s.fileName }}（{{ s.returned }} 点）
          </button>
          <button
            v-if="activeImportId !== null"
            class="rounded px-1 text-[12px] text-app-muted hover:text-app-error"
            title="从列表移除当前导入"
            @click="removeImport(activeImportId)"
          >
            移除
          </button>
        </span>
      </div>
      <p v-if="importError" class="px-3 pb-2 text-[12px] text-app-error">{{ importError }}</p>

      <div v-if="activeImport" class="border-t border-app-border px-3 py-3">
        <div class="flex flex-wrap items-end gap-3">
          <label class="flex flex-col gap-1 text-[11px] text-app-muted">
            最低幅值（绝对值）
            <input
              type="number"
              min="0"
              step="any"
              :value="activeImportFilter.minAmplitude"
              class="w-36 rounded border border-app-border bg-app-bg px-2 py-1 text-[12px] text-app-fg"
              @input="onMinAmplitudeInput"
            />
          </label>
          <label class="flex flex-col gap-1 text-[11px] text-app-muted">
            逐周期类型
            <select
              :value="activeImportFilter.type"
              :disabled="!importTypeOptions.length"
              class="w-48 rounded border border-app-border bg-app-bg px-2 py-1 text-[12px] text-app-fg disabled:opacity-50"
              @change="patchActiveImportFilter({ type: ($event.target as HTMLSelectElement).value })"
            >
              <option value="ALL">全部类型</option>
              <option v-for="t in importTypeOptions" :key="t" :value="t">
                {{ t }}（{{ activeImport.distribution?.type_counts[t] ?? 0 }} 周期）
              </option>
            </select>
          </label>
          <button
            class="rounded border border-app-border px-2 py-1 text-[12px] text-app-muted hover:bg-app-surface-variant"
            @click="resetImportFilter"
          >
            重置筛选
          </button>
          <span class="ml-auto text-[11px] text-app-muted">
            筛选结果 {{ filteredImportIndices.length }} / {{ activeImport.returned }} 点 · 类型筛选按周期整组保留
          </span>
        </div>
        <div class="mt-3 rounded border border-app-border/70 bg-app-bg/40 p-2">
          <div class="mb-1 flex items-center justify-between text-[10px] text-app-muted">
            <span>筛选后幅值分布（32 桶，绝对值）</span>
            <span>{{ amplitudeHistogram[amplitudeHistogram.length - 1]?.to.toFixed(1) ?? "0" }} · 单位随数据源，未标定</span>
          </div>
          <div class="flex h-20 items-end gap-px" role="img" aria-label="筛选后幅值分布直方图">
            <div
              v-for="(bin, i) in amplitudeHistogram"
              :key="i"
              class="min-w-0 flex-1 rounded-t-sm bg-app-primary/70"
              :style="{ height: `${bin.count ? Math.max(2, bin.height) : 0}%` }"
              :title="`${bin.from.toFixed(1)}–${bin.to.toFixed(1)}：${bin.count} 点`"
            />
          </div>
          <p v-if="!filteredImportIndices.length" class="pt-1 text-center text-[10px] text-app-muted">当前筛选没有数据点</p>
        </div>

        <div class="mt-3 flex flex-wrap items-center gap-2">
          <span class="text-[11px] text-app-muted">导出分析报告</span>
          <button
            v-for="f in REPORT_FORMATS"
            :key="f.key"
            class="rounded border border-app-border px-2 py-1 text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-45"
            :disabled="!!exporting || !reportCaps?.available.includes(f.key)"
            :title="reportCaps?.missing[f.key] || `按整份导入样本导出（不随筛选变化）`"
            @click="exportReport(f.key)"
          >
            {{ exporting === f.key ? "生成中…" : f.label }}
          </button>
          <span class="text-[10px] text-app-muted">
            统计按整份样本（{{ activeImport.returned }} 点{{ activeImport.truncated ? `，另有 ${activeImport.truncated} 点未取` : "" }}），不受筛选影响；Word 附当前视图图谱
          </span>
        </div>
        <p v-if="exportError" class="pt-1 text-[11px] text-app-error">{{ exportError }}</p>
        <p v-if="exportNotice" class="pt-1 text-[11px] text-app-muted">{{ exportNotice }}</p>
      </div>

      <div v-if="activeImport" class="grid grid-cols-5 gap-4 border-t border-app-border px-3 py-3">
        <div class="col-span-2">
          <div class="text-[11px] text-app-muted">放电类型识别（整体）</div>
          <template v-if="activeImport.classification">
            <div class="mt-1 flex items-baseline gap-2">
              <span class="text-2xl font-semibold">{{ activeImport.classification.name }}</span>
              <span class="tabular text-[12px] text-app-muted">
                置信度 {{ (activeImport.classification.confidence * 100).toFixed(0) }}%
              </span>
            </div>
            <div class="mt-0.5 text-[11px] text-app-muted">
              {{ activeImport.classification.description }} · 严重度：{{ severityLabel(activeImport.classification.severity) }}
              · 正半周占比 {{ (activeImport.classification.pos_ratio * 100).toFixed(0) }}%
            </div>
          </template>
          <div v-else class="mt-1 text-[12px] text-app-error">
            {{ activeImport.classificationError ?? "—" }}
          </div>
        </div>

        <div class="col-span-2">
          <div class="text-[11px] text-app-muted">
            类型分布（逐周期投票{{ activeImport.distribution ? `，${activeImport.distribution.total_samples} 个周期` : "" }}）
          </div>
          <template v-if="activeImport.distribution">
            <div
              v-for="(ratio, t) in activeImport.distribution.type_ratios"
              :key="t"
              class="mt-1 flex items-center gap-2"
            >
              <span class="w-24 shrink-0 truncate text-[11px]" :title="String(t)">{{ t }}</span>
              <span class="h-1.5 flex-1 overflow-hidden rounded bg-app-surface-variant">
                <span
                  class="block h-full bg-app-primary"
                  :style="{ width: `${(Number(ratio) * 100).toFixed(1)}%` }"
                />
              </span>
              <span class="tabular w-12 text-right text-[11px] text-app-muted">
                {{ (Number(ratio) * 100).toFixed(1) }}%
              </span>
            </div>
          </template>
          <div v-else class="mt-1 text-[12px] text-app-muted">
            {{ activeImport.distributionError ?? "无周期列，逐周期分类不可用" }}
          </div>
        </div>

        <div>
          <div class="text-[11px] text-app-muted">数据质量</div>
          <div class="tabular mt-1 text-[12px] leading-5">
            <div>
              点数 {{ activeImport.returned
              }}<span v-if="activeImport.total !== activeImport.returned"> / {{ activeImport.total }}</span>
            </div>
            <div v-if="activeImport.truncated" class="text-app-warning">截头 {{ activeImport.truncated }}</div>
            <div v-if="activeImport.filteredRows" class="text-app-muted">
              过滤行 {{ activeImport.filteredRows }}（相位越界或负幅值）
            </div>
            <div v-if="activeImport.nonfinite" class="text-app-warning">非有限值 {{ activeImport.nonfinite }}</div>
            <div class="text-app-muted">列：{{ (activeImport.columns ?? []).join("、") || "—" }}</div>
          </div>
        </div>
      </div>

      <div v-if="activeImport" ref="scatterHost" class="border-t border-app-border p-3">
        <PrpdScatter
          :events="importEvents"
          :source-hint="`导入文件 ${activeImport.fileName}`"
          :height="280"
        />
      </div>
    </section>

    <p v-if="error" class="rounded-md bg-app-error/10 px-3 py-2 text-[12px] text-app-error">{{ error }}</p>
    <p v-if="notice" class="rounded-md bg-app-surface-variant/60 px-3 py-2 text-[12px] text-app-muted">{{ notice }}</p>

    <section class="grid grid-cols-4 gap-3">
      <div class="rounded-lg border border-app-border bg-app-surface p-3">
        <div class="text-[11px] text-app-muted">窗口内脉冲数</div>
        <div class="tabular mt-1 text-2xl font-semibold text-app-primary">
          {{ summary.total }}<span class="ml-1 text-xs font-normal text-app-muted">次</span>
        </div>
        <div class="text-[10px] text-app-muted">按箱聚合求和，不受明细查询的 5000 条上限影响</div>
      </div>
      <div class="rounded-lg border border-app-border bg-app-surface p-3">
        <div class="text-[11px] text-app-muted">最大幅值</div>
        <div class="tabular mt-1 text-2xl font-semibold text-app-warning">
          {{ summary.maxAmp.toFixed(1) }}
        </div>
        <div class="text-[10px] text-app-muted">单位未标定（真机为原始计数、模拟器为 mV）</div>
      </div>
      <div class="rounded-lg border border-app-border bg-app-surface p-3">
        <div class="text-[11px] text-app-muted">正 / 负极性</div>
        <div class="tabular mt-1 text-2xl font-semibold text-app-info">
          <template v-if="summary.polarityKnown">
            {{ summary.pos }}<span class="mx-1 text-xs font-normal text-app-muted">/</span>{{ summary.neg }}
          </template>
          <template v-else>—</template>
        </div>
        <div class="text-[10px] text-app-muted">
          {{ summary.polarityKnown ? "按箱的极性计数求和" : "库内 polarity 为 NULL（未落库）；不显示 0/0，免得被读成一个负极性都没有" }}
        </div>
      </div>
      <div class="rounded-lg border border-app-border bg-app-surface p-3">
        <div class="text-[11px] text-app-muted">明细样本数</div>
        <div class="tabular mt-1 text-2xl font-semibold">{{ shown.length }}</div>
        <div class="text-[10px] text-app-muted">查询上限 5000；散点与明细表都基于这份样本</div>
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="px-3 py-2">
        <p class="pb-2 text-[11px] text-app-muted">
          散点取<span class="text-app-fg">实时</span> <code>prpd</code> 帧（那里带相位与极性）。
          下方明细表是库内历史事件，它的 <code>phase</code>/<code>polarity</code> 目前是 NULL，
          所以历史 PRPD 散点暂时无点可画。
        </p>
        <PrpdScatter :events="liveEvents" :source-hint="prpdSourceHint" :height="300" />
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
        <span class="text-[13px] font-medium">放电事件明细</span>
        <span class="text-[11px] text-app-muted">按时间倒序，最多 2000 行</span>
      </div>
      <div class="max-h-[360px] overflow-auto">
        <table class="w-full text-[12px]">
          <thead class="sticky top-0 bg-app-surface text-app-muted">
            <tr class="border-b border-app-border">
              <th class="px-3 py-1.5 text-left font-normal">时间戳</th>
              <th class="px-3 py-1.5 text-left font-normal">通道</th>
              <th class="px-3 py-1.5 text-left font-normal">传感器</th>
              <th class="px-3 py-1.5 text-right font-normal">相位</th>
              <th class="px-3 py-1.5 text-right font-normal">幅值</th>
              <th class="px-3 py-1.5 text-right font-normal">极性</th>
              <th class="px-3 py-1.5 text-right font-normal">能量</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in shown" :key="e.id" class="border-b border-app-border/40">
              <td class="tabular px-3 py-1">{{ stamp(e.timestamp) }}</td>
              <td class="px-3 py-1">{{ e.channel_id ?? "—" }}</td>
              <td class="px-3 py-1 text-app-muted">{{ e.sensor_type ?? "—" }}</td>
              <td class="tabular px-3 py-1 text-right">{{ e.phase === null ? "—" : e.phase.toFixed(1) }}</td>
              <td class="tabular px-3 py-1 text-right">{{ e.amplitude.toFixed(1) }}</td>
              <td class="px-3 py-1 text-right" :class="polarityTone(e.polarity)">
                {{ polarityLabel(e.polarity) }}
              </td>
              <td class="tabular px-3 py-1 text-right text-app-muted">
                {{ e.energy === null ? "—" : e.energy.toFixed(2) }}
              </td>
            </tr>
            <tr v-if="!shown.length">
              <td colspan="7" class="px-3 py-6 text-center text-app-muted">
                {{ loaded ? "所选范围内没有事件（内核需要 --with-storage 才会把事件写进库）" : "尚未查询" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface px-3 py-2">
      <div class="text-[12px] font-medium">本页未实现的项（设计稿有，但缺数据支撑）</div>
      <ul class="mt-1 list-disc pl-5 text-[11px] text-app-muted">
        <li>
          <span class="text-app-fg">放电类型分布</span>：已随"实验数据导入"恢复（走
          <code>data.import_file</code> → <code>classify_by_cycles</code> 逐周期投票）；
          但**只对导入的文件可用**，实时采集事件上仍没有类型标签（内核不在
          <code>pd_event_detected</code> 里带分类结果）。
        </li>
        <li>
          <span class="text-app-fg">等效放电量（nC）</span>：全链路没有计数→物理量的换算，
          写了就是编的。
        </li>
        <li><span class="text-app-fg">最大重复率</span>：口径未定义，先不给。</li>
        <li>
          <span class="text-app-fg">历史事件的相位与极性</span>：<code>uhf_pipeline</code> 发的
          <code>pd_event_detected</code> 只带 <code>position</code> 与幅值，没有相位与极性，
          <code>PersistenceSubscriber</code> 照抄入库 → 库里 <code>phase</code>/<code>polarity</code>
          恒为 NULL，历史 PRPD 散点与极性统计都做不了。补齐要动内核（按 position 换算相位、
          补极性字段），本页先不假造。
        </li>
      </ul>
    </section>
  </div>
</template>
