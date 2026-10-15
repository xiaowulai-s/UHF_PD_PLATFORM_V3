/**
 * 全应用共享的 sidecar 连接与最新帧缓存。
 *
 * 有损 topic 由服务端按最新帧合并下发，所以这里每个 topic 只留最后一帧即可；
 * 告警/设备状态这类无损 topic 走数组累加，不能覆盖。
 */

import { reactive, ref, shallowRef } from "vue";
import { SidecarClient, type ConnState, type Endpoint } from "./sidecar-client";
import { recordAeHit } from "./ae-stats";
import type { AeHit, DischargeTypeInfo, Frame, LogLine, StatsSnapshot, Topic } from "@/shared/protocol";

const STREAM_TOPICS: Topic[] = ["waveform", "fft", "prpd", "prps", "ae_envelope", "ae_hit", "stats", "log"];

export const client = new SidecarClient(STREAM_TOPICS);

export const streamState = ref<ConnState>("closed");
export const ctrlState = ref<ConnState>("closed");
export const sidecarState = ref("starting");
export const advertisedTopics = ref<Topic[]>([]);

/**
 * 帧与日志一律用 shallowRef。
 *
 * 实测教训：logs 原本是 ref<LogLine[]>，赋值 1000 行要 913ms——Vue 会给每个元素
 * 建深度响应式代理，外推 10k 行需 9 秒以上，直接违背 M3.2「10k 行不卡」的验收。
 * 列表的虚拟化只限制 DOM 数量，管不了代理开销。latest 同理：一帧的 Float32Array
 * 有 4096 个元素，20FPS 下持续深代理代价可观。
 * 改 shallowRef 后靠"换整个引用"触发更新。
 */
export const latest = shallowRef<Partial<Record<Topic, Frame>>>({});
export const frameCount = reactive<Record<string, number>>({});
export const alarms = shallowRef<Record<string, unknown>[]>([]);
export const logs = shallowRef<LogLine[]>([]);
export const stats = shallowRef<StatsSnapshot | null>(null);
/**
 * AE 命中序列（最新的在最前）。
 *
 * 与 alarms 同理是"不能覆盖"的累加型：ae_hit 是逐条事件帧，latest 里只留最后一帧
 * 就没法看频率、重复率这些需要跨命中统计的量。上限同旧版 AEParametersWidget 的
 * 历史表量级（100 条），再多就是无意义的内存占用。
 */
export const aeHits = shallowRef<AeHit[]>([]);

/**
 * 各通道最近一次的放电类型判定，键为 `${device}:${channel}`。
 *
 * 和 alarms 一样是"不能覆盖"的按通道状态：一个通道的新判定只覆盖它自己。
 * 用映射而不是单个 latest，否则多通道时后到的判定会把别的通道的标签一起冲掉。
 */
export const dischargeTypes = shallowRef<Record<string, DischargeTypeInfo>>({});

export function dischargeTypeFor(device: string, channel: number): DischargeTypeInfo | null {
  if (!device) return null;
  return dischargeTypes.value[`${device}:${channel}`] ?? null;
}

/**
 * 内核已判定"条件消失"的报警，键为 `${device}:${alarmType}`，值是消除时刻。
 *
 * `alarm_cleared` 是可靠 topic，而 `CtrlChannel` 构造时没传 topics、不发 `sub`，
 * 所以服务端本来就把全部可靠 topic 下发了——帧一直到得了前端，只是此前没人处理，
 * 表现为报警条件恢复后界面仍挂着那条告警，要手动刷新才消失。
 *
 * 只对**实时帧**来的行生效，不套用到库内历史行：`alarm_cleared` 说的是"此刻这个条件
 * 没了"，拿它去标注三天前那条历史告警是错的。
 *
 * **语义是单调的**：某一行已消除 ⟺ 该键的消除时刻**晚于这一行的时刻**。
 * 不要写成"收到新 alarm 就把这个键删掉"——AE 信号丢失那类告警会以秒级周期
 * "触发→恢复"反复循环（实测 40 秒 7 组），删键会把**已经消除的历史行**也一起抹掉
 * 标记，表现为"已消除"列表忽有忽无。单调比较天然满足：消除只影响比它早的行，
 * 之后新触发的行不受影响。
 */
export const clearedAlarms = shallowRef<Record<string, number>>({});

export function alarmKey(device: string, alarmType: string): string {
  return `${device}:${alarmType}`;
}

/** 该行是否已被消除。`atSeconds` 是这一行自己的时刻，单位秒（与告警帧的 timestamp 一致）。 */
export function isAlarmCleared(device: string, alarmType: string, atSeconds: number): boolean {
  const clearedAt = clearedAlarms.value[alarmKey(device, alarmType)];
  return clearedAt !== undefined && clearedAt > atSeconds;
}

/** 最新一条**尚未消除**的实时报警，供概览页"最新告警"卡使用。 */
export function latestActiveAlarm(): Record<string, unknown> | null {
  for (const a of alarms.value) {
    const dev = String(a.device_id ?? "");
    const type = String(a.alarm_type ?? "");
    if (!isAlarmCleared(dev, type, Number(a.timestamp ?? 0))) return a;
  }
  return null;
}

const MAX_LOG_LINES = 10_000;
const MAX_ALARMS = 500;
const MAX_AE_HITS = 200;
/** 已消除标记的上限，超出按消除时刻裁掉最旧的。 */
const MAX_CLEARED_ALARMS = 500;

/**
 * 订阅必须在模块初始化时就挂好，不能挂在 connect() 里。
 *
 * 窗口现在早于内核创建，首帧端点可能是后到的：那条路径上 connect() 根本不会被调用
 * （main.ts 只在 bootstrap 拿到端点时才 connect），于是 socket 连上了、帧也进来了，
 * 但没有任何 onFrame 监听者——表现是界面停在"未连接"且一个点都不画，且完全不报错。
 */
client.stream.onStateChange((s) => (streamState.value = s));
client.ctrl.onStateChange((s) => (ctrlState.value = s));

client.stream.onFrame((frame) => {
  latest.value = { ...latest.value, [frame.t]: frame };
  frameCount[frame.t] = (frameCount[frame.t] ?? 0) + 1;
  if (frame.t === "stats") stats.value = (frame.m as { stats: StatsSnapshot }).stats;
  if (frame.t === "log") appendLogs((frame.m as { lines: LogLine[] }).lines);
  if (frame.t === "ae_hit") appendAeHits((frame.m as { hit: AeHit }).hit, frame.d, frame.c);
});

client.ctrl.onFrame((frame) => {
  if (frame.t === "alarm") {
    const alarm = (frame.m as { alarm: Record<string, unknown> }).alarm;
    const next = [alarm, ...alarms.value];
    if (next.length > MAX_ALARMS) next.length = MAX_ALARMS;
    alarms.value = next;
    // 这里**刻意不动** clearedAlarms：新 alarm 只说明"此刻又触发了"，
    // 不能因此把已经消除的历史行的标记也撤掉（见 clearedAlarms 的注释）。
    // 新行的时间戳晚于已有的消除时刻，靠 isAlarmCleared 的单调比较自然判为未消除。
  }
  if (frame.t === "alarm_cleared") {
    const m = frame.m as { alarm_type?: string };
    const k = alarmKey(String(frame.d ?? ""), String(m.alarm_type ?? ""));
    if (!k.endsWith(":")) {
      // 单位必须与告警帧的 timestamp 一致（秒）。Date.now() 是毫秒，
      // 混用会让"消除时刻 > 行时刻"恒成立，把所有行都标成已消除。
      const next = { ...clearedAlarms.value, [k]: Date.now() / 1000 };
      const keys = Object.keys(next);
      if (keys.length > MAX_CLEARED_ALARMS) {
        // 按消除时刻裁掉最旧的，防止长时间运行后无限增长
        for (const old of keys.sort((a, b) => (next[a] ?? 0) - (next[b] ?? 0)).slice(0, keys.length - MAX_CLEARED_ALARMS)) {
          delete next[old];
        }
      }
      clearedAlarms.value = next;
    }
  }
  if (frame.t === "discharge_type") {
    const info = (frame.m as { type?: DischargeTypeInfo }).type;
    if (info) {
      dischargeTypes.value = { ...dischargeTypes.value, [`${frame.d}:${frame.c}`]: info };
    }
  }
});

export function connect(endpoint: Endpoint): void {
  client.connect(endpoint);
}

export function retarget(endpoint: Endpoint): void {
  client.retarget(endpoint);
}

function appendLogs(lines: LogLine[]): void {
  const merged = logs.value.concat(lines);
  const overflow = merged.length - MAX_LOG_LINES;
  logs.value = overflow > 0 ? merged.slice(overflow) : merged;
}

/**
 * 追加一条 AE 命中。
 *
 * 用"换整个数组引用"而不是 push：aeHits 是 shallowRef，push 不触发更新；
 * 而 AE 在 --pulse-rate 调高后能到 10+ 条/秒，每条都复制 200 元素的数组，
 * 代价远小于给每个对象建深度代理。
 *
 * 同时喂给累计聚合器（lib/ae-stats）。**不能只靠这个列表做统计**：它被
 * MAX_AE_HITS 截断，命中率一高就饱和，"总 Hit 数"会停在假上限上。
 * device/channel 必须一起传：聚合器要按通道分开算，而 AeHit 本身不带这两个字段
 * （它们在 Frame 上）。
 */
function appendAeHits(hit: AeHit, device: string, channel: number): void {
  if (!hit || typeof hit.amplitude !== "number") return;
  recordAeHit(hit, device, channel);
  const next = [hit, ...aeHits.value];
  if (next.length > MAX_AE_HITS) next.length = MAX_AE_HITS;
  aeHits.value = next;
}

export function call<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  return client.ctrl.call<T>(method, params);
}

/**
 * 等 /ctrl 通道就绪。
 *
 * 视图在 onMounted 里就发 RPC 会撞上 WebSocket 还没握手完成的竞态——表现是
 * 偶发"sidecar 未连接"，刷新一下又好了。所有挂载期读数据都必须先 await 这个。
 */
export function waitCtrlOpen(timeoutMs = 8000): Promise<void> {
  if (ctrlState.value === "open") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const poll = window.setInterval(() => {
      if (ctrlState.value === "open") {
        window.clearInterval(poll);
        resolve();
      } else if (Date.now() - started > timeoutMs) {
        window.clearInterval(poll);
        reject(new Error("等待内核连接超时"));
      }
    }, 60);
  });
}
