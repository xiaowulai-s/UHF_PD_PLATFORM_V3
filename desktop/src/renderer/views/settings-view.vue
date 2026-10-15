<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { Save } from "lucide-vue-next";
import { call, waitCtrlOpen } from "@/renderer/lib/store";
import { TOPICS, type Topic, type TransportDiagnostics } from "@/shared/protocol";
import { DATE_FORMATS, TIMEZONES, formatStamp, persistPrefs, prefs } from "@/renderer/lib/prefs";

interface ChannelRow {
  channel_index: number;
  name: string;
  coupling_type: string;
  status: number;
  enabled: boolean;
}
interface DeviceRow {
  id: string;
  name: string;
  firmware_version: string | null;
  channels: ChannelRow[];
}

const COUPLING = ["UHF", "HFCT", "TEV", "AE"];
const devices = ref<DeviceRow[]>([]);
const thresholds = ref<Record<string, unknown> | null>(null);
const loading = ref(true);
const saving = ref(false);
const busy = ref("");
const error = ref("");
const notice = ref("");

/** 稿子里有、但内核没有对应写接口的项：渲染出来但禁用，避免假装能保存 */
const UNSUPPORTED = new Set(["放电次数阈值", "检查更新"]);

const form = reactive({
  pdMaxAmplitude: 100,
});

const firstDevice = computed(() => devices.value[0] ?? null);
const firmware = computed(() => firstDevice.value?.firmware_version || "未上报");

/** 无损 topic 才能限流；内核会拒损无损之外的名字，这里先按同一份 TOPICS 表挡住 */
const LOSSY_TOPICS = (Object.keys(TOPICS) as Topic[]).filter((t) => TOPICS[t] === "lossy");
const serverRates = ref<Partial<Record<Topic, number>>>({});
/**
 * 输入框里的草稿值。
 *
 * 类型必须写成 number|string：`v-model` 落在 type="number" 上会自动走 looseToNumber
 * （不需要 .number 修饰符），所以填了数字时这里存的是 number 而不是 string。
 * 实测踩过：按 string 处理再 .trim()，真点一次"应用"就抛
 * "trim is not a function"，速率从没下发成功过。
 */
const rateDraft = reactive<Partial<Record<Topic, number | string>>>({});
const rateSaving = ref<Topic | null>(null);

/**
 * AE 报警阈值（内核 `AEAlarmChecker` 的四个字段）。
 *
 * 回显值一律来自 `config.get_ae_thresholds`，**不写死默认值**——内核 setter 里有
 * `max(1, ...)` / `max(0.1, ...)` 夹紧，请求 0 帧会被存成 1；界面若拿请求值当回显，
 * 就会显示一个判定根本不用的数（`PDAlarmService` 上正是这么出过事故）。
 */
const AE_FIELDS = [
  { key: "signal_loss_frames", label: "信号丢失判定帧数", hint: "连续这么多帧没有 AE 命中就报“信号丢失”", step: 1, min: 1, unit: "帧" },
  { key: "noise_threshold_mv", label: "噪声报警阈值", hint: "包络 RMS 超过它开始计数", step: 0.1, min: 0.1, unit: "mV" },
  { key: "noise_clear_mv", label: "噪声消除阈值", hint: "低于它才撤掉噪声报警（应小于报警阈值，否则会反复抖动）", step: 0.1, min: 0.1, unit: "mV" },
  { key: "noise_frame_count", label: "噪声连续帧数", hint: "连续这么多帧超标才真的报警", step: 1, min: 1, unit: "帧" },
] as const;
const aeThresholds = ref<Record<string, number> | null>(null);
const aeDraft = reactive<Record<string, number | string>>({});
const aeSaving = ref(false);

/** 单通道量程草稿，键为 `${device}:${channel}`（与内核 `_amplitude_thresholds` 的键一致）。 */
const channelMaxDraft = reactive<Record<string, number | string>>({});
const channelMaxSaving = ref("");

/** 内核报回来的单通道量程：`config.get_thresholds` → `per_device.channel_max`。 */
const channelMaxApplied = computed<Record<string, number>>(() => {
  const per = (thresholds.value?.per_device ?? {}) as Record<string, unknown>;
  return (per.channel_max ?? {}) as Record<string, number>;
});

function channelKey(deviceId: string, channelIndex: number): string {
  return `${deviceId}:${channelIndex}`;
}

async function loadAeThresholds(): Promise<boolean> {
  try {
    const t = await call<Record<string, number>>("config.get_ae_thresholds", {});
    aeThresholds.value = t;
    for (const f of AE_FIELDS) aeDraft[f.key] = t[f.key] ?? "";
    return true;
  } catch {
    // 没有采集服务时这条 RPC 未注册（条件注册）。不报错，卡片显示为不可用。
    aeThresholds.value = null;
    return false;
  }
}

async function applyAeThresholds(): Promise<void> {
  aeSaving.value = true;
  error.value = "";
  try {
    const payload: Record<string, number> = {};
    for (const f of AE_FIELDS) {
      const raw = String(aeDraft[f.key] ?? "").trim();
      if (raw === "") {
        error.value = `${f.label}不能留空`;
        return;
      }
      const n = Number(raw);
      if (Number.isNaN(n)) {
        error.value = `${f.label}“${raw}”不是数字`;
        return;
      }
      if (n < f.min) {
        error.value = `${f.label}不能小于 ${f.min} ${f.unit}`;
        return;
      }
      if (f.step === 1 && !Number.isInteger(n)) {
        error.value = `${f.label}必须是整数`;
        return;
      }
      payload[f.key] = n;
    }
    const noiseThreshold = payload.noise_threshold_mv;
    const noiseClear = payload.noise_clear_mv;
    if (noiseThreshold === undefined || noiseClear === undefined) {
      error.value = "AE 噪声报警阈值与消除阈值都是必填项";
      return;
    }
    if (noiseClear >= noiseThreshold) {
      error.value = "AE 噪声消除阈值必须低于报警阈值，避免报警状态反复抖动";
      return;
    }
    await call("config.set_ae_thresholds", payload);
    // 必须回读：内核会夹紧（如 0 帧→1），照抄请求值显示的就是假数
    if (!(await loadAeThresholds())) throw new Error("写入成功，但读取当前生效值失败");
    const t = aeThresholds.value ?? {};
    notice.value = `AE 阈值已下发：丢失 ${t.signal_loss_frames} 帧 / 噪声 ${t.noise_threshold_mv} mV / 消除 ${t.noise_clear_mv} mV / 连续 ${t.noise_frame_count} 帧`;
  } catch (err) {
    error.value = `AE 阈值下发失败：${(err as Error).message}`;
  } finally {
    aeSaving.value = false;
  }
}

async function applyChannelMax(deviceId: string, channelIndex: number): Promise<void> {
  const key = channelKey(deviceId, channelIndex);
  const raw = String(channelMaxDraft[key] ?? "").trim();
  const n = Number(raw);
  if (raw === "" || Number.isNaN(n) || n <= 0) {
    error.value = `通道 ${deviceId}/${channelIndex} 的量程必须是正数`;
    return;
  }
  channelMaxSaving.value = key;
  error.value = "";
  try {
    await call("config.set_channel_max_amplitude", {
      device_id: deviceId,
      channel_id: channelIndex,
      max_amplitude: n,
    });
    // 回读确认内核真的收下了（返回值是入参回显，不足以证明已生效）
    thresholds.value = await call<Record<string, unknown>>("config.get_thresholds");
    const got = channelMaxApplied.value[key];
    if (got === undefined || Math.abs(got - n) > 1e-6) {
      error.value = `通道 ${key} 量程下发后回读为 ${got ?? "未设置"}，与请求的 ${n} 不一致`;
      return;
    }
    notice.value = `通道 ${key} 量程已设为 ${got} mV`;
  } catch (err) {
    error.value = `通道量程下发失败：${(err as Error).message}`;
  } finally {
    channelMaxSaving.value = "";
  }
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    await waitCtrlOpen();
    const [list, cfg, diag] = await Promise.all([
      call<DeviceRow[]>("device.list"),
      call<Record<string, unknown>>("config.get_thresholds"),
      call<TransportDiagnostics>("system.diagnostics"),
    ]);
    devices.value = list;
    thresholds.value = cfg;
    form.pdMaxAmplitude = Number(cfg.max_amplitude ?? 100);
    serverRates.value = diag.topic_rates;
    for (const topic of LOSSY_TOPICS) {
      const hz = diag.topic_rates[topic];
      rateDraft[topic] = hz === undefined ? "" : String(hz);
    }
    // 每通道量程的草稿初值：已设置过的用内核值，没设过的留空（表示"跟随全局"）
    for (const device of list) {
      for (const ch of device.channels ?? []) {
        const key = channelKey(device.id, ch.channel_index);
        const applied = (cfg.per_device as Record<string, unknown> | undefined)?.channel_max as
          | Record<string, number>
          | undefined;
        channelMaxDraft[key] = applied?.[key] === undefined ? "" : String(applied[key]);
      }
    }
    await loadAeThresholds();
  } catch (err) {
    error.value = `加载失败：${(err as Error).message}`;
  } finally {
    loading.value = false;
  }
}

/**
 * 下发单个 topic 的目标发布速率。
 *
 * 速率的合法区间只有内核自己知道（sidecar_server.set_topic_rates），所以这里不复制
 * 一份校验——前端掐一份、内核掐一份，改一处就会不一致，而内核的 ValueError 文案
 * 本来就能直接渲染进 error 条。空值是唯一需要在这里翻译的语义："" 表示不限流。
 */
async function applyRate(topic: Topic): Promise<void> {
  const raw = String(rateDraft[topic] ?? "").trim();
  const hz = raw === "" ? null : Number(raw);
  if (hz !== null && Number.isNaN(hz)) {
    error.value = `${topic} 的速率"${raw}"不是数字；留空表示不限流`;
    return;
  }
  rateSaving.value = topic;
  error.value = "";
  try {
    serverRates.value = await call<Partial<Record<Topic, number>>>("system.set_topic_rates", { [topic]: hz });
    notice.value = `${topic} 发布速率已下发：${hz === null ? "不限流（跟随管线节奏）" : `${hz} Hz`}`;
  } catch (err) {
    error.value = `速率下发失败：${(err as Error).message}`;
  } finally {
    rateSaving.value = null;
  }
}

async function saveThresholds(): Promise<void> {
  saving.value = true;
  error.value = "";
  try {
    // scope=global：这个字段改的是全局量程，与 config.get_thresholds 报告的同一份状态
    await call("config.set_amplitude_threshold", { scope: "global", max_amplitude: form.pdMaxAmplitude });
    notice.value = `PD 幅值阈值已下发：${form.pdMaxAmplitude} mV`;
    thresholds.value = await call<Record<string, unknown>>("config.get_thresholds");
  } catch (err) {
    error.value = `阈值下发失败：${(err as Error).message}`;
  } finally {
    saving.value = false;
  }
}

async function toggleCoupling(deviceId: string, channel: ChannelRow): Promise<void> {
  const key = `${deviceId}:${channel.channel_index}`;
  busy.value = key;
  error.value = "";
  try {
    // 运行时生效与持久化是两条路：前者改内核内存，后者写库
    await call("config.set_channel_coupling", {
      device_id: deviceId,
      channel_id: channel.channel_index,
      coupling_type: channel.coupling_type.toLowerCase(),
    });
    const device = devices.value.find((d) => d.id === deviceId);
    if (device) {
      await call("device.save", {
        device: { id: device.id, name: device.name, firmware_version: device.firmware_version },
        channels: device.channels.map((c) => ({
          channel_index: c.channel_index,
          name: c.name,
          coupling_type: c.coupling_type.toLowerCase(),
          enabled: c.enabled,
        })),
      });
    }
    notice.value = `通道 ${channel.channel_index} 已切换为 ${channel.coupling_type}`;
  } catch (err) {
    error.value = `通道切换失败：${(err as Error).message}`;
  } finally {
    busy.value = "";
  }
}

async function toggleEnabled(deviceId: string, channel: ChannelRow): Promise<void> {
  const key = `${deviceId}:${channel.channel_index}`;
  busy.value = key;
  try {
    const device = devices.value.find((d) => d.id === deviceId);
    if (!device) return;
    await call("device.save", {
      device: { id: device.id, name: device.name },
      channels: device.channels.map((c) => ({
        channel_index: c.channel_index,
        name: c.name,
        coupling_type: c.coupling_type.toLowerCase(),
        enabled: c.channel_index === channel.channel_index ? !c.enabled : c.enabled,
      })),
    });
    channel.enabled = !channel.enabled;
  } catch (err) {
    error.value = `启用状态更新失败：${(err as Error).message}`;
  } finally {
    busy.value = "";
  }
}

function savePrefs(): void {
  persistPrefs();
  notice.value = "界面偏好已保存到本机";
}

onMounted(() => {
  persistPrefs();
  void load();
});
</script>

<template>
  <div class="flex flex-col gap-4 pb-6">
    <p v-if="error" class="rounded-md bg-app-error/10 px-3 py-2 text-[12px] text-app-error">{{ error }}</p>
    <p v-if="notice" class="rounded-md bg-app-success/10 px-3 py-2 text-[12px] text-app-success">{{ notice }}</p>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="border-b border-app-border px-4 py-2.5 text-[13px] font-medium">界面偏好</div>
      <div class="grid grid-cols-2 gap-x-8 gap-y-3 p-4">
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">系统名称</span>
          <input v-model="prefs.systemName" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px]" placeholder="请输入系统名称" />
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">主题</span>
          <select
            v-model="prefs.theme"
            class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px]"
            title="切换后立即生效并持久化；图表（含 canvas 与 WebGL）会跟着换色"
          >
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </select>
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">时区</span>
          <select v-model="prefs.timezone" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px]">
            <option v-for="tz in TIMEZONES" :key="tz.value" :value="tz.value">{{ tz.value }} ({{ tz.label }})</option>
          </select>
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">日期格式</span>
          <select v-model="prefs.dateFormat" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px]">
            <option v-for="f in DATE_FORMATS" :key="f" :value="f">{{ f }}</option>
          </select>
        </label>
      </div>
      <div class="flex justify-end border-t border-app-border px-4 py-2.5">
        <button class="flex items-center gap-1.5 rounded-md bg-app-primary px-3 py-1.5 text-[12px] text-white" @click="savePrefs">
          <Save :size="14" /> 保存偏好
        </button>
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="border-b border-app-border px-4 py-2.5 text-[13px] font-medium">报警阈值</div>
      <div class="grid grid-cols-2 gap-x-8 gap-y-3 p-4">
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">PD 幅值阈值 (mV)</span>
          <div class="flex gap-2">
            <input v-model.number="form.pdMaxAmplitude" type="number" min="1" step="1" class="tabular w-28 rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px]" />
            <button :disabled="saving" class="rounded-md bg-app-primary px-3 py-1.5 text-[12px] text-white disabled:opacity-50" @click="saveThresholds">
              {{ saving ? "下发中…" : "应用" }}
            </button>
          </div>
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">放电次数阈值</span>
          <input :title="'内核暂无对应写接口'" disabled value="—" class="cursor-not-allowed rounded-md border border-app-border bg-app-surface-variant px-2 py-1.5 text-[13px] text-app-muted" />
          <span v-if="UNSUPPORTED.has('放电次数阈值')" class="text-[11px] text-app-muted">内核暂无对应写接口，待 M4 补</span>
        </label>
        <dl v-if="thresholds" class="col-span-2 grid grid-cols-4 gap-2 border-t border-app-border pt-3 text-[11px]">
          <div v-for="(mv, level) in (thresholds.custom_absolute_mv as Record<string, number>)" :key="level" class="flex flex-col">
            <dt class="text-app-muted">{{ level }}</dt>
            <dd class="tabular">{{ mv.toFixed(1) }} mV</dd>
          </div>
        </dl>
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="flex items-center justify-between border-b border-app-border px-4 py-2.5">
        <span class="text-[13px] font-medium">AE 报警阈值</span>
        <span class="text-[11px] text-app-muted">仅 AE 通道适用</span>
      </div>
      <p v-if="!aeThresholds" class="px-4 py-4 text-[12px] text-app-muted">
        内核未注册 <code>config.get_ae_thresholds</code>（没有采集服务时该 RPC 不注册），本项不可用。
      </p>
      <div v-else class="grid grid-cols-2 gap-x-8 gap-y-3 p-4">
        <label v-for="f in AE_FIELDS" :key="f.key" class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">{{ f.label }} ({{ f.unit }})</span>
          <input
            v-model="aeDraft[f.key]"
            :disabled="aeSaving"
            type="number"
            :min="f.min"
            :step="f.step"
            class="tabular w-28 rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px] disabled:opacity-50"
          />
          <span class="text-[11px] text-app-muted">{{ f.hint }}</span>
        </label>
        <div class="col-span-2 flex items-center gap-3 border-t border-app-border pt-3">
          <button
            :disabled="aeSaving"
            class="rounded-md bg-app-primary px-3 py-1.5 text-[12px] text-white disabled:opacity-50"
            @click="applyAeThresholds"
          >
            {{ aeSaving ? "下发中…" : "应用 AE 阈值" }}
          </button>
          <span class="text-[11px] text-app-muted">
            当前生效：丢失 {{ aeThresholds.signal_loss_frames }} 帧 · 噪声 {{ aeThresholds.noise_threshold_mv }} mV
            · 消除 {{ aeThresholds.noise_clear_mv }} mV · 连续 {{ aeThresholds.noise_frame_count }} 帧
          </span>
        </div>
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="border-b border-app-border px-4 py-2.5 text-[13px] font-medium">推流节流</div>
      <p class="px-4 pt-3 text-[11px] text-app-muted">
        有损 topic 的目标发布频率（Hz），留空表示不限流、跟随管线节奏。无损 topic（报警、设备状态等）
        不允许限流。这里只改内核进程的内存状态，不写库，内核重启后回到默认。
      </p>
      <table class="mt-2 w-full text-[12px]">
        <thead class="text-app-muted">
          <tr class="border-b border-app-border">
            <th class="px-4 py-2 text-left font-normal">topic</th>
            <th class="px-4 py-2 text-right font-normal">目标速率 (Hz)</th>
            <th class="px-4 py-2 text-right font-normal">内核当前值</th>
            <th class="px-4 py-2 text-right font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="topic in LOSSY_TOPICS" :key="topic" class="border-b border-app-border/50">
            <td class="px-4 py-2 font-mono">{{ topic }}</td>
            <td class="px-4 py-2 text-right">
              <input
                v-model="rateDraft[topic]"
                type="number"
                min="1"
                max="200"
                step="1"
                placeholder="不限流"
                class="tabular w-24 rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px] text-right"
              />
            </td>
            <td class="tabular px-4 py-2 text-right text-app-muted">
              {{ serverRates[topic] === undefined ? "跟随管线" : `${serverRates[topic]} Hz` }}
            </td>
            <td class="px-4 py-2 text-right">
              <button
                :disabled="rateSaving === topic"
                class="rounded-md bg-app-primary px-3 py-1 text-[11px] text-white disabled:opacity-50"
                @click="applyRate(topic)"
              >
                {{ rateSaving === topic ? "下发中…" : "应用" }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="border-b border-app-border px-4 py-2.5 text-[13px] font-medium">通道配置</div>
      <p v-if="loading" class="px-4 py-6 text-center text-[12px] text-app-muted">加载中…</p>
      <table v-else-if="devices.length" class="w-full text-[12px]">
        <thead class="text-app-muted">
          <tr class="border-b border-app-border">
            <th class="px-4 py-2 text-left font-normal">通道</th>
            <th class="px-4 py-2 text-left font-normal">通道名称</th>
            <th class="px-4 py-2 text-left font-normal">耦合模式</th>
            <th class="px-4 py-2 text-left font-normal">状态</th>
            <th class="px-4 py-2 text-right font-normal">单通道量程 (mV)</th>
            <th class="px-4 py-2 text-right font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="device in devices" :key="device.id" class="border-b border-app-border/50 bg-app-surface-variant/40">
            <td class="px-4 py-1.5 text-[11px] text-app-muted" colspan="6">设备 {{ device.name }} ({{ device.id }})</td>
          </tr>
          <template v-for="device in devices" :key="device.id">
            <tr v-for="ch in device.channels" :key="`${device.id}:${ch.channel_index}`" class="border-b border-app-border/50">
              <td class="tabular px-4 py-2">{{ ch.channel_index }}</td>
              <td class="px-4 py-2">{{ ch.name || `通道 ${ch.channel_index}` }}</td>
              <td class="px-4 py-2">
                <select v-model="ch.coupling_type" class="rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px]">
                  <option v-for="c in COUPLING" :key="c" :value="c">{{ c }}</option>
                </select>
              </td>
              <td class="px-4 py-2 text-app-muted">{{ ch.status === 1 ? "异常" : "正常" }}</td>
              <td class="px-4 py-2 text-right">
                <div class="inline-flex items-center justify-end gap-1.5">
                  <input
                    v-model="channelMaxDraft[channelKey(device.id, ch.channel_index)]"
                    type="number"
                    min="0.1"
                    step="1"
                    :disabled="channelMaxSaving === channelKey(device.id, ch.channel_index)"
                    :placeholder="`全局 ${Number(thresholds?.max_amplitude ?? 100).toFixed(1)}`"
                    :aria-label="`${device.name} 通道 ${ch.channel_index} 单通道量程 mV`"
                    class="tabular w-24 rounded-md border border-app-border bg-app-bg px-2 py-1 text-[12px] text-right disabled:opacity-50"
                  />
                  <button
                    :disabled="channelMaxSaving === channelKey(device.id, ch.channel_index)"
                    class="rounded-md bg-app-primary px-2 py-1 text-[11px] text-white disabled:opacity-50"
                    @click="applyChannelMax(device.id, ch.channel_index)"
                  >
                    {{ channelMaxSaving === channelKey(device.id, ch.channel_index) ? "…" : "应用" }}
                  </button>
                </div>
                <div class="mt-1 text-[10px] text-app-muted">
                  生效：{{ channelMaxApplied[channelKey(device.id, ch.channel_index)] ?? thresholds?.max_amplitude ?? 100 }} mV
                  <span v-if="channelMaxApplied[channelKey(device.id, ch.channel_index)] === undefined">（跟随全局）</span>
                </div>
              </td>
              <td class="px-4 py-2 text-right">
                <div class="flex items-center justify-end gap-2">
                  <button
                    :disabled="busy === `${device.id}:${ch.channel_index}`"
                    class="rounded-md border border-app-border px-2 py-1 text-[11px] disabled:opacity-40"
                    @click="toggleCoupling(device.id, ch)"
                  >
                    {{ busy === `${device.id}:${ch.channel_index}` ? "…" : "应用模式" }}
                  </button>
                  <button
                    :disabled="busy === `${device.id}:${ch.channel_index}`"
                    class="rounded-md px-2 py-1 text-[11px]"
                    :class="ch.enabled ? 'bg-app-success/15 text-app-success' : 'bg-app-surface-variant text-app-muted'"
                    @click="toggleEnabled(device.id, ch)"
                  >
                    {{ ch.enabled ? "已启用" : "已停用" }}
                  </button>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-else class="px-4 py-6 text-center text-[12px] text-app-muted">
        内核里没有已保存的设备配置。设备管理页 (M3.4) 会补上新增设备的入口。
      </p>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="border-b border-app-border px-4 py-2.5 text-[13px] font-medium">固件与更新</div>
      <div class="p-4">
        <div class="grid grid-cols-3 gap-4 text-[12px]">
          <div class="flex flex-col gap-1"><span class="text-app-muted">固件版本</span><span class="font-mono">{{ firmware }}</span></div>
          <div class="flex flex-col gap-1"><span class="text-app-muted">更新状态</span><span>未接入更新服务</span></div>
          <div class="flex flex-col gap-1">
            <span class="text-app-muted">检查更新</span>
            <button disabled class="cursor-not-allowed self-start rounded-md border border-app-border px-3 py-1.5 text-[12px] text-app-muted">内核暂无对应接口</button>
          </div>
        </div>
        <div class="mt-3 text-[11px] text-app-muted">当前时间 {{ formatStamp(Date.now()) }} · 时区 {{ prefs.timezone }}</div>
      </div>
    </section>
  </div>
</template>
