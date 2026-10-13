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
          <span class="text-app-muted">界面语言</span>
          <select v-model="prefs.language" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px]">
            <option value="zh-CN">简体中文</option>
            <option value="en">English</option>
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
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">显示密度</span>
          <select v-model="prefs.density" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px]">
            <option value="realtime">实时</option>
            <option value="comfortable">宽松</option>
          </select>
        </label>
        <label class="flex flex-col gap-1 text-[12px]">
          <span class="text-app-muted">通知频率</span>
          <select v-model="prefs.notifyFrequency" class="rounded-md border border-app-border bg-app-bg px-2 py-1.5 text-[13px]">
            <option value="realtime">实时</option>
            <option value="5m">每 5 分钟</option>
            <option value="15m">每 15 分钟</option>
            <option value="hourly">每小时</option>
            <option value="daily">每日汇总</option>
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
            <th class="px-4 py-2 text-right font-normal">启用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="device in devices" :key="device.id" class="border-b border-app-border/50 bg-app-surface-variant/40">
            <td class="px-4 py-1.5 text-[11px] text-app-muted" colspan="5">设备 {{ device.name }} ({{ device.id }})</td>
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

    <section class="rounded-lg border border-app-border bg-app-surface p-4">
      <div class="grid grid-cols-3 gap-4 text-[12px]">
        <div class="flex flex-col gap-1"><span class="text-app-muted">固件版本</span><span class="font-mono">{{ firmware }}</span></div>
        <div class="flex flex-col gap-1"><span class="text-app-muted">更新状态</span><span>未接入更新服务</span></div>
        <div class="flex flex-col gap-1">
          <span class="text-app-muted">检查更新</span>
          <button disabled class="cursor-not-allowed self-start rounded-md border border-app-border px-3 py-1.5 text-[12px] text-app-muted">内核暂无对应接口</button>
        </div>
      </div>
      <div class="mt-3 text-[11px] text-app-muted">当前时间 {{ formatStamp(Date.now()) }} · 时区 {{ prefs.timezone }}</div>
    </section>
  </div>
</template>
