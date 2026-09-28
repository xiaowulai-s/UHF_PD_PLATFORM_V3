/**
 * 跨进程协议契约的 TypeScript 侧定义，与 core/protocol/frames.py 手工镜像。
 * 改一边必须改另一边；Python 侧的字段生成逻辑见 core/protocol/payloads.py。
 *
 * 传输：单个 WebSocket 二进制帧，内容为 msgpack 映射。
 * 数值负载走 msgpack 原生 bin 类型而非 JSON，因此 @msgpack/msgpack 解码后直接是
 * Uint8Array，无需配置 ExtensionCodec；Float32Array 可零拷贝视图化，且 NaN/Inf
 * 保持原样。
 */

export const PROTOCOL_VERSION = 1;

export type Qos = "lossy" | "reliable";

export const TOPICS = {
  waveform: "lossy",
  fft: "lossy",
  prpd: "lossy",
  prps: "lossy",
  ae_envelope: "lossy",
  ae_hit: "lossy",
  stats: "lossy",
  alarm: "reliable",
  alarm_cleared: "reliable",
  device_status: "reliable",
  acquisition: "reliable",
  source: "reliable",
  log: "reliable",
} as const satisfies Record<string, Qos>;

export type Topic = keyof typeof TOPICS;

/** 数组负载的元素编码 */
export type DTypeCode = "f4" | "f8" | "i4" | "i2" | "i1" | "u1";

export interface ArrayMeta {
  shape: number[];
  dtype: DTypeCode;
  endian: "le";
}

export type Kind = "tele" | "event";

/** 解码后的帧。payload 为原始二进制视图，未展开。 */
export interface Frame {
  v: number;
  k: Kind;
  t: Topic;
  d: string;
  c: number;
  q: number;
  ts: number;
  m: Record<string, unknown>;
  p?: Uint8Array;
}

export interface WaveformMeta extends ArrayMeta {
  sensor_type: "uhf" | "ae" | "hfct" | "tev";
  sample_rate: number;
}

export interface FftPeak {
  frequency: number;
  magnitude: number;
  is_harmonic: boolean;
  harmonic_order: number;
}

/** payload 是 [频率, 幅值] 交替的 (N,2) 行主序数组 */
export interface FftMeta extends ArrayMeta {
  sensor_type: string;
  peaks: FftPeak[];
  noise_floor: number;
  snr_db: number;
}

export interface PrpdEvent {
  phase: number;
  amplitude: number;
  polarity: number;
  energy: number;
  cycle: number;
}

/** payload 是 (360,256) 相位×幅值密度矩阵 */
export interface PrpdMeta extends ArrayMeta {
  max_amplitude: number;
  min_amplitude: number;
  total_events: number;
  events: PrpdEvent[];
  /** 仅在服务端截断时出现，值为截断前的原始条数 */
  events_truncated?: number;
}

/** payload 是 (512,360) 的周期×相位矩阵 */
export interface PrpsMeta extends ArrayMeta {
  total_cycles: number;
}

export interface AlarmRecord {
  device_id?: string;
  channel_id?: number;
  alarm_type: string;
  level: "critical" | "warning" | "info" | string;
  amplitude: number;
  threshold: number;
  description: string;
  timestamp: number;
}

export interface Hello {
  v: number;
  k: "hello";
  mode: "stream" | "ctrl";
  topics: Record<Topic, Qos>;
  rpc: string[];
}

export interface Subscription {
  k: "sub";
  topics: Topic[];
}

export interface RpcCall {
  k: "call";
  i: string;
  m: string;
  p?: Record<string, unknown>;
}

export type RpcResponse<T = unknown> =
  | { v: number; k: "result"; i: string; ok: true; d: T }
  | { v: number; k: "result"; i: string; ok: false; e: string };

/**
 * 把帧的 payload 还原为指定形状的单精度数组。
 * 假定 buf 已按 msgpack bin 精确切出，因此可零拷贝取视图。
 */
export function toArray(frame: Frame, dtype: DTypeCode = "f4"): Float32Array {
  const meta = frame.m as ArrayMeta;
  const bytes = frame.p;
  if (!bytes) return new Float32Array(0);
  if (meta.dtype !== dtype) {
    throw new Error(`期望 dtype=${dtype}，实际=${meta.dtype}`);
  }
  const offset = bytes.byteOffset ?? 0;
  return new Float32Array(bytes.buffer, offset, bytes.byteLength / 4);
}

/** 端点：/stream 收有损遥测，/ctrl 收无损事件与 RPC。两者都要带 token。 */
export function endpointUrl(port: number, mode: "stream" | "ctrl", token: string): string {
  return `ws://127.0.0.1:${port}/${mode}?token=${encodeURIComponent(token)}`;
}
