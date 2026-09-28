/**
 * 全应用共享的 sidecar 连接与最新帧缓存。
 *
 * 有损 topic 由服务端按最新帧合并下发，所以这里每个 topic 只留最后一帧即可；
 * 告警/设备状态这类无损 topic 走数组累加，不能覆盖。
 */

import { reactive, ref } from "vue";
import { SidecarClient, type ConnState, type Endpoint } from "./sidecar-client";
import type { Frame, LogLine, StatsSnapshot, Topic } from "@/shared/protocol";

const STREAM_TOPICS: Topic[] = ["waveform", "fft", "prpd", "prps", "ae_envelope", "ae_hit", "stats", "log"];

export const client = new SidecarClient(STREAM_TOPICS);

export const streamState = ref<ConnState>("closed");
export const ctrlState = ref<ConnState>("closed");
export const sidecarState = ref("starting");
export const advertisedTopics = ref<Topic[]>([]);

/** 每个 topic 最近一帧，供图表组件按自身刷新节奏取用 */
export const latest = reactive<Partial<Record<Topic, Frame>>>({});
export const frameCount = reactive<Record<string, number>>({});
export const alarms = ref<Record<string, unknown>[]>([]);
export const logs = ref<LogLine[]>([]);
export const stats = ref<StatsSnapshot | null>(null);

const MAX_LOG_LINES = 2000;

export function connect(endpoint: Endpoint): void {
  client.connect(endpoint);
  client.stream.onStateChange((s) => (streamState.value = s));
  client.ctrl.onStateChange((s) => (ctrlState.value = s));

  client.stream.onFrame((frame) => {
    latest[frame.t] = frame;
    frameCount[frame.t] = (frameCount[frame.t] ?? 0) + 1;
    if (frame.t === "stats") stats.value = (frame.m as { stats: StatsSnapshot }).stats;
    if (frame.t === "log") appendLogs((frame.m as { lines: LogLine[] }).lines);
  });

  client.ctrl.onFrame((frame) => {
    if (frame.t === "alarm") {
      alarms.value.unshift((frame.m as { alarm: Record<string, unknown> }).alarm);
      if (alarms.value.length > 500) alarms.value.length = 500;
    }
  });
}

export function retarget(endpoint: Endpoint): void {
  client.retarget(endpoint);
}

function appendLogs(lines: LogLine[]): void {
  logs.value.push(...lines);
  const overflow = logs.value.length - MAX_LOG_LINES;
  if (overflow > 0) logs.value.splice(0, overflow);
}

export function call<T>(method: string, params?: Record<string, unknown>): Promise<T> {
  return client.ctrl.call<T>(method, params);
}
