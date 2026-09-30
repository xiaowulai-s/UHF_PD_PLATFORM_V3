<script setup lang="ts">
/**
 * 趋势分析页（M5.2）。
 *
 * 数据源是 `storage.query_trend_series`：从 `pd_events` 按等宽时间箱即时聚合。
 * 刻意不用 `storage.query_trends`（读 pd_trend_data 表）——那里的行是旧
 * signal_router 每 50 帧写一次的**累计计数**与编造的常量关系
 * （avg=max*0.6、energy=count*max*0.1、noise=0），画成趋势曲线会把"自启动以来
 * 共多少事件"当成"这一箱多少事件"，读数完全不可信。
 *
 * 因此本页不承诺"与旧版数据点一致"：旧版趋势页根本不查库，画的是
 * `get_example_trend_data()` 的未播种随机数，每次启动都不同。这里对齐的是
 * 旧版的**交互契约**（四个时窗、每箱一行、本地时区横轴），不是那组随机数。
 */

import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RefreshCw } from "lucide-vue-next";
import type uPlot from "uplot";
import PlotCanvas from "@/renderer/components/plot-canvas.vue";
import { chartTheme } from "@/renderer/lib/chart-theme";
import { call, waitCtrlOpen } from "@/renderer/lib/store";
import { formatStamp } from "@/renderer/lib/prefs";

interface TrendBin {
  t: number;
  iso: string;
  count: number;
  max_amplitude: number;
  avg_amplitude: number;
  total_energy: number;
  bin_seconds: number;
  window: string;
}
interface DeviceRow {
  id: string;
  name: string;
  channels: { channel_index: number; name: string }[];
}
interface HistoryDevice {
  device_id: string;
  events: number;
  first_ts: string | null;
  last_ts: string | null;
}

/** 箱数取"人眼能分辨的密度"而不是整数好看：1h 每分钟一箱、30d 每 8 小时一箱 */
const WINDOWS = [
  { key: "1h", label: "最近 1 小时", bins: 60 },
  { key: "24h", label: "最近 24 小时", bins: 96 },
  { key: "7d", label: "最近 7 天", bins: 84 },
  { key: "30d", label: "最近 30 天", bins: 90 },
] as const;

const devices = ref<DeviceRow[]>([]);
const historyDevices = ref<HistoryDevice[]>([]);
const deviceId = ref("");
const channelId = ref<string>("ALL");
// 别把这个 ref 叫 window：<script setup> 里的顶层同名声明会遮蔽全局 window 对象，
// 之后 window.setInterval(...) 变成对一个 Ref 取属性，运行期才炸。
const timeWindow = ref<(typeof WINDOWS)[number]["key"]>("1h");
const bins = ref<Record<string, TrendBin[]>>({});
const error = ref("");
const notice = ref("");
const busy = ref(false);
const lastFetch = ref(0);
/** 内核没带 --with-storage 时这个 RPC 根本没注册，要能区分"没数据"和"没开写库" */
const rpcMissing = ref(false);
const auto = ref(false);
let timer: number | null = null;

let plot: uPlot | null = null;

const currentWindow = computed(() => WINDOWS.find((w) => w.key === timeWindow.value) ?? WINDOWS[0]);
const rows = computed<TrendBin[]>(() => bins.value[timeWindow.value] ?? []);
const current = computed(() => {
  const list = rows.value;
  const total = list.reduce((a, b) => a + b.count, 0);
  const peak = list.reduce((a, b) => Math.max(a, b.max_amplitude), 0);
  const filled = list.filter((b) => b.count > 0).length;
  return { total, peak, filled, binSeconds: list[0]?.bin_seconds ?? 0 };
});
const hasData = computed(() => current.value.total > 0);
const device = computed(() => devices.value.find((d) => d.id === deviceId.value) ?? null);

/**
 * 设备候选 = 库里真实有历史的 device_id，再补上已保存但还没有数据的设备。
 *
 * 不要用 stats.sources 的键：模拟器以**采集设备 id**（SIM-001）写事件，而数据源 id
 * 是另一套命名，拿它去查 pd_events 会得到"查询条件全对、结果恒为 0"，看起来像没数据。
 * 实测踩过：下拉显示"模拟器-UHF（数据源）"、库里有 1937 条事件，趋势页仍是空图。
 */
const deviceOptions = computed(() => {
  const out: { id: string; name: string }[] = [];
  const seen = new Set<string>();
  for (const h of historyDevices.value) {
    if (seen.has(h.device_id)) continue;
    seen.add(h.device_id);
    const saved = devices.value.find((d) => d.id === h.device_id);
    out.push({
      id: h.device_id,
      name: `${saved?.name || h.device_id}（${h.events} 条事件）`,
    });
  }
  for (const d of devices.value) {
    if (seen.has(d.id)) continue;
    seen.add(d.id);
    out.push({ id: d.id, name: `${d.name || d.id}（暂无历史）` });
  }
  return out;
});

function buildOptions(): Omit<uPlot.Options, "width" | "height"> {
  const t = chartTheme();
  const axis = { stroke: t.fg, grid: { stroke: t.grid }, ticks: { stroke: t.grid }, font: t.axisFont };
  return {
    // 图例关掉：uPlot 的图例是画在容器下方的 DOM，会溢出到下面的说明文字上
    // （实测两者叠成一片）。两条 y 轴各自带标题且颜色与曲线一致，图例本就冗余。
    legend: { show: false },
    // 横轴是真实时刻：内核给的 t 是 epoch 秒，uPlot 的 time 刻度按秒解释、
    // 按浏览器本地时区渲染。旧版告警页用 new Date(naiveISO) 解析会被当成本地时间，
    // 差一个时区；这里走 epoch 秒就不经过那条路。
    scales: { x: { time: true }, cnt: { auto: true }, mv: { auto: true, min: 0 } },
    axes: [
      { ...axis, label: "时间", size: 62 },
      { ...axis, label: "事件数 / 箱", scale: "cnt", values: (_s, v) => v.map((x) => String(Math.round(Number(x)))), size: 46 },
      { ...axis, label: "最大幅值 (mV)", scale: "mv", side: 1, grid: { stroke: "transparent" }, values: (_s, v) => v.map((x) => Number(x).toFixed(1)), size: 56 },
    ],
    series: [
      { label: "时间" },
      { label: "事件数 / 箱", scale: "cnt", stroke: t.primary, width: 2, points: { show: false } },
      { label: "最大幅值 (mV)", scale: "mv", stroke: t.warning, width: 1.5, points: { show: false } },
    ],
    cursor: { drag: { x: true, y: false } },
  };
}

function onReady(instance: uPlot): void {
  plot = instance;
  paint();
}

function paint(): void {
  if (!plot) return;
  const list = rows.value;
  // 必须给全新的外层数组：uPlot 用引用相等判断是否跳过重算范围，
  // 复用同一个外层数组会让缩放/新数据来了之后坐标轴不更新。
  plot.setData([list.map((r) => r.t), list.map((r) => r.count), list.map((r) => r.max_amplitude)]);
  plot.redraw(false, true);
}

async function load(): Promise<void> {
  if (!deviceId.value) return;
  busy.value = true;
  error.value = "";
  try {
    await waitCtrlOpen();
    const params: Record<string, unknown> = {
      device_id: deviceId.value,
      // 键名 window 是内核 RPC 的参数名，与上面那个被改名的 ref 无关
      window: timeWindow.value,
      bins: currentWindow.value.bins,
    };
    if (channelId.value !== "ALL") params.channel_id = Number(channelId.value);
    const list = await call<TrendBin[]>("storage.query_trend_series", params);
    bins.value = { ...bins.value, [timeWindow.value]: list };
    rpcMissing.value = false;
    lastFetch.value = Date.now();
    paint();
  } catch (err) {
    const msg = (err as Error).message;
    rpcMissing.value = /未知方法/.test(msg);
    error.value = `趋势数据加载失败：${msg}`;
  } finally {
    busy.value = false;
  }
}

async function pickWindow(key: (typeof WINDOWS)[number]["key"]): Promise<void> {
  timeWindow.value = key;
  // 每个时窗查一次就缓存着：切回来不该再打一次库，但"刷新"按钮永远强制重查
  if (!bins.value[key]?.length) await load();
  else paint();
}

function stopTimer(): void {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

function setAuto(on: boolean): void {
  auto.value = on;
  stopTimer();
  if (on) timer = setInterval(() => void load(), 10_000);
}

let stop: () => void = () => {};

onMounted(async () => {
  try {
    await waitCtrlOpen();
    devices.value = await call<DeviceRow[]>("device.list");
    // 没带 --with-storage 时这个 RPC 未注册；拿不到就退回 device.list，不算错误
    try {
      historyDevices.value = await call<HistoryDevice[]>("storage.device_ids");
    } catch {
      historyDevices.value = [];
    }
  } catch (err) {
    error.value = `设备列表加载失败：${(err as Error).message}`;
    return;
  }
  // 候选集要等上面两个异步结果都落地才完整，所以跟着 deviceOptions 走而不是只挑一次
  stop = watch(
    deviceOptions,
    (opts) => {
      const first = opts[0];
      if (!first) return;
      const had = deviceId.value;
      if (!opts.some((o) => o.id === deviceId.value)) deviceId.value = first.id;
      if (deviceId.value && deviceId.value !== had) void load();
    },
    { immediate: true },
  );
});

onBeforeUnmount(() => {
  stop();
  stopTimer();
});

const emptyHint = computed(() => {
  if (!deviceOptions.value.length) {
    return "库里还没有任何事件，也没有已保存的设备。确认 sidecar 带 --with-storage 运行（默认只读不落库），并已采集到局放事件。";
  }
  if (rpcMissing.value) {
    return "内核未开启写库：storage.query_trend_series 未注册。启动 sidecar 时加 --with-storage 才会持久化事件。";
  }
  return (
    `该窗口内没有事件。趋势是从 pd_events 现算的，所以要有数据需要两个条件：` +
    `sidecar 带 --with-storage 运行，且事件确实落在 ${currentWindow.value.label} 内。`
  );
});
</script>

<template>
  <div class="flex flex-col gap-3">
    <section class="flex flex-wrap items-center gap-2 rounded-lg border border-app-border bg-app-surface px-3 py-2">
      <div class="flex items-center gap-1.5">
        <button
          v-for="w in WINDOWS"
          :key="w.key"
          class="rounded-md border px-2.5 py-1 text-[12px]"
          :class="timeWindow === w.key ? 'border-app-primary bg-app-primary/10 text-app-primary' : 'border-app-border text-app-muted hover:bg-app-surface-variant'"
          @click="pickWindow(w.key)"
        >
          {{ w.label }}
        </button>
      </div>

      <label class="ml-auto flex items-center gap-1.5 text-[12px] text-app-muted">
        设备
        <select v-model="deviceId" class="max-w-[180px] rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px] text-app-fg" @change="channelId = 'ALL'; void load()">
          <option v-if="!deviceOptions.length" value="">（无可选设备）</option>
          <option v-for="o in deviceOptions" :key="o.id" :value="o.id">{{ o.name }}</option>
        </select>
      </label>
      <label class="flex items-center gap-1.5 text-[12px] text-app-muted">
        通道
        <select v-model="channelId" class="rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px] text-app-fg" @change="void load()">
          <option value="ALL">全部</option>
          <option v-for="ch in device?.channels ?? []" :key="ch.channel_index" :value="String(ch.channel_index)">
            {{ ch.channel_index }} {{ ch.name }}
          </option>
        </select>
      </label>
      <label class="flex items-center gap-1 text-[12px] text-app-muted">
        <input type="checkbox" :checked="auto" @change="setAuto(($event.target as HTMLInputElement).checked)" />
        自动刷新
      </label>
      <button
        :disabled="busy || !deviceId"
        class="flex items-center gap-1.5 rounded-md border border-app-border px-2.5 py-1 text-[12px] disabled:opacity-40"
        @click="load"
      >
        <RefreshCw :size="13" /> {{ busy ? "查询中…" : "刷新" }}
      </button>
    </section>

    <p v-if="error" class="rounded-md bg-app-error/10 px-3 py-2 text-[12px] text-app-error">{{ error }}</p>
    <p v-if="notice" class="rounded-md bg-app-success/10 px-3 py-2 text-[12px] text-app-success">{{ notice }}</p>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
        <span class="text-[13px] font-medium">{{ currentWindow.label }}趋势</span>
        <span class="text-[11px] text-app-muted">
          {{ current.binSeconds ? `每箱 ${Math.round(current.binSeconds)} 秒 · ${currentWindow.bins} 箱` : "—" }}
          <template v-if="lastFetch"> · 查询于 {{ formatStamp(lastFetch) }}</template>
        </span>
      </div>

      <div class="grid grid-cols-4 divide-x divide-app-border border-b border-app-border text-center">
        <div class="px-3 py-2">
          <div class="text-[11px] text-app-muted">窗口内事件总数</div>
          <div class="tabular mt-0.5 text-xl font-semibold">{{ current.total }}</div>
        </div>
        <div class="px-3 py-2">
          <div class="text-[11px] text-app-muted">峰值幅值</div>
          <div class="tabular mt-0.5 text-xl font-semibold">{{ current.peak ? `${current.peak.toFixed(1)} mV` : "—" }}</div>
        </div>
        <div class="px-3 py-2">
          <div class="text-[11px] text-app-muted">有事件的箱</div>
          <div class="tabular mt-0.5 text-xl font-semibold">{{ current.filled }} / {{ currentWindow.bins }}</div>
        </div>
        <div class="px-3 py-2">
          <div class="text-[11px] text-app-muted">幅值口径</div>
          <div class="mt-0.5 text-[12px]">箱内 |幅值| 最大值</div>
        </div>
      </div>

      <div class="relative h-[320px] w-full">
        <PlotCanvas :options="buildOptions()" @ready="onReady" />
        <div v-if="!hasData" class="pointer-events-none absolute inset-0 flex items-center justify-center px-10">
          <p class="max-w-md rounded-md bg-app-surface/90 px-4 py-3 text-center text-[12px] text-app-muted">
            {{ emptyHint }}
          </p>
        </div>
      </div>

      <p class="border-t border-app-border px-3 py-2 text-[11px] text-app-muted">
        纵轴两条各自独立缩放：事件数是"该时间箱内的事件条数"，幅值是"该箱内 |amplitude| 的最大值"。
        幅值目前按采集原始样本值显示——FPGA 侧是裸 int16 计数、模拟器是 mV，现场标定完成前
        两种量纲共用这一条轴（详见 AGENTS.md「单位口径本身是缺的」）。
      </p>
    </section>
  </div>
</template>
