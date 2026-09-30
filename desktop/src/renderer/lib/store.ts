/**
 * 全应用共享的 sidecar 连接与最新帧缓存。
 *
 * 有损 topic 由服务端按最新帧合并下发，所以这里每个 topic 只留最后一帧即可；
 * 告警/设备状态这类无损 topic 走数组累加，不能覆盖。
 */

import { reactive, ref, shallowRef } from "vue";
import { SidecarClient, type ConnState, type Endpoint } from "./sidecar-client";
import type { AeHit, Frame, LogLine, StatsSnapshot, Topic } from "@/shared/protocol";

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

const MAX_LOG_LINES = 10_000;
const MAX_ALARMS = 500;
const MAX_AE_HITS = 200;

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
  if (frame.t === "ae_hit") appendAeHits((frame.m as { hit: AeHit }).hit);
});

client.ctrl.onFrame((frame) => {
  if (frame.t === "alarm") {
    const next = [(frame.m as { alarm: Record<string, unknown> }).alarm, ...alarms.value];
    if (next.length > MAX_ALARMS) next.length = MAX_ALARMS;
    alarms.value = next;
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
 */
function appendAeHits(hit: AeHit): void {
  if (!hit || typeof hit.amplitude !== "number") return;
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
